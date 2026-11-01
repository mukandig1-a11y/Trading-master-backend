import os, math, time
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title='Trading Master Data API', version='2.2.0')
# For production, replace '*' with the exact origin serving your HTML.
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])
PAPER_ORDERS = []
TIMEOUT = 15

@app.get('/')
def root():
    return {'service':'Trading Master backend','version':'2.2.0','health':'/health'}

@app.get('/health')
def health():
    return {'status':'ok','service':'trading-master-backend','version':'2.2.0','timestamp':datetime.now(timezone.utc).isoformat(),'finnhub_configured':bool(os.getenv('FINNHUB_API_KEY')),'alpha_vantage_configured':bool(os.getenv('ALPHA_VANTAGE_API_KEY')),'execution_enabled':False}

def fetch_finnhub(symbol: str, interval: str):
    key=os.getenv('FINNHUB_API_KEY')
    if not key: raise RuntimeError('FINNHUB_API_KEY is not configured')
    resolution_map={'1min':'1','5min':'5','15min':'15','30min':'30','60min':'60','1h':'60','1day':'D','daily':'D'}
    resolution=resolution_map.get(interval, 'D')
    now=int(time.time()); days=7 if resolution!='D' else 180
    start=now-days*86400
    r=requests.get('https://finnhub.io/api/v1/stock/candle',params={'symbol':symbol,'resolution':resolution,'from':start,'to':now,'token':key},timeout=TIMEOUT)
    r.raise_for_status(); d=r.json()
    if d.get('s')!='ok' or not d.get('c'): raise RuntimeError('Finnhub returned no candle data for this symbol/interval')
    return [{'time':datetime.fromtimestamp(t,timezone.utc).isoformat(),'open':o,'high':h,'low':l,'close':c,'volume':v} for t,o,h,l,c,v in zip(d['t'],d['o'],d['h'],d['l'],d['c'],d['v'])]

def fetch_alpha(symbol: str, interval: str):
    key=os.getenv('ALPHA_VANTAGE_API_KEY')
    if not key: raise RuntimeError('ALPHA_VANTAGE_API_KEY is not configured')
    if interval in ('1min','5min','15min','30min','60min','1h'):
        iv='60min' if interval in ('60min','1h') else interval
        params={'function':'TIME_SERIES_INTRADAY','symbol':symbol,'interval':iv,'outputsize':'compact','apikey':key}
        series_key=f'Time Series ({iv})'
    else:
        params={'function':'TIME_SERIES_DAILY','symbol':symbol,'outputsize':'compact','apikey':key}
        series_key='Time Series (Daily)'
    r=requests.get('https://www.alphavantage.co/query',params=params,timeout=TIMEOUT); r.raise_for_status(); d=r.json()
    if 'Note' in d or 'Information' in d: raise RuntimeError(d.get('Note') or d.get('Information'))
    series=d.get(series_key)
    if not series: raise RuntimeError(d.get('Error Message') or 'Alpha Vantage returned no price series; check symbol, plan and API quota')
    out=[]
    for stamp,row in sorted(series.items()):
        out.append({'time':stamp,'open':float(row['1. open']),'high':float(row['2. high']),'low':float(row['3. low']),'close':float(row['4. close']),'volume':float(row.get('5. volume',0))})
    return out

def analyse(symbol, provider, interval):
    errors=[]; providers=['finnhub','alpha_vantage'] if provider=='auto' else [provider]
    for p in providers:
        try:
            bars=fetch_finnhub(symbol,interval) if p=='finnhub' else fetch_alpha(symbol,interval)
            closes=[float(x['close']) for x in bars if x.get('close') is not None]
            if len(closes)<2: raise RuntimeError('Not enough valid price bars')
            last=closes[-1]; recent=closes[-min(20,len(closes)):]; prior=closes[-min(50,len(closes)):-min(20,len(closes))]
            short=sum(recent)/len(recent); long=sum(prior)/len(prior) if prior else short
            returns=[(closes[i]/closes[i-1]-1)*100 for i in range(1,len(closes)) if closes[i-1]]
            vol=math.sqrt(sum(x*x for x in returns[-20:])/max(1,len(returns[-20:]))) if returns else 0
            trend='BULLISH' if short>long*1.001 else 'BEARISH' if short<long*.999 else 'SIDEWAYS'
            momentum=(closes[-1]/closes[-min(6,len(closes))]-1)*100 if len(closes)>1 else 0
            decision='WAIT'; reason='No strong directional confirmation; wait for a clearer setup.'
            if trend=='BULLISH' and momentum>0.15: decision='BUY'; reason='Short-term average is above the prior average and momentum is positive. Confirm your own risk and entry conditions.'
            elif trend=='BEARISH' and momentum< -0.15: decision='SELL'; reason='Short-term average is below the prior average and momentum is negative. Confirm your own risk and entry conditions.'
            # Data freshness: daily candles may be older outside market hours; expose timestamp, do not claim real-time.
            stamp=bars[-1]['time']
            return {'symbol':symbol,'provider':p,'interval':interval,'price':last,'decision':decision,'reason':reason,'trend':trend,'volatility_pct':round(vol,4),'momentum_pct':round(momentum,4),'data_status':'VALID','stale':False,'timestamp':stamp,'retrieved_at':datetime.now(timezone.utc).isoformat(),'bars_used':len(bars),'candles':closes[-100:],'execution_enabled':False,'risk_lock':'PAPER_ONLY'}
        except Exception as e: errors.append(f'{p}: {e}')
    raise HTTPException(status_code=502,detail='; '.join(errors))

@app.get('/market-analysis')
def market_analysis(symbol: str=Query(...,min_length=1), provider: str=Query('auto'), interval: str=Query('1day')):
    provider=provider.lower()
    if provider not in ('auto','finnhub','alpha_vantage'): raise HTTPException(400,'provider must be auto, finnhub or alpha_vantage')
    return analyse(symbol.strip().upper(),provider,interval)

class PaperOrder(BaseModel):
    symbol: str=Field(min_length=1,max_length=40)
    side: str
    quantity: float=Field(gt=0)
    entry_price: Optional[float]=Field(default=None,gt=0)
    mode: str='paper'

@app.post('/paper-orders')
def create_paper_order(order: PaperOrder):
    if order.mode.lower()!='paper': raise HTTPException(400,'Only paper mode is permitted')
    if order.side.upper() not in ('BUY','SELL'): raise HTTPException(400,'side must be BUY or SELL')
    item={'id':len(PAPER_ORDERS)+1,'time':datetime.now(timezone.utc).isoformat(),'symbol':order.symbol.upper(),'side':order.side.upper(),'quantity':order.quantity,'entry_price':order.entry_price,'status':'PAPER_RECORDED','mode':'paper','execution_enabled':False}
    PAPER_ORDERS.insert(0,item)
    return item

@app.get('/paper-orders')
def list_paper_orders():
    return {'orders':PAPER_ORDERS,'mode':'paper','execution_enabled':False}
