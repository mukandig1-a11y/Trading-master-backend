# Trading Master V2.2 backend

FastAPI backend expected by TradingMasterV2_2.html.

## Deploy
- Deploy this repository as a Python Web Service on Render.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Add private environment variables `FINNHUB_API_KEY` and `ALPHA_VANTAGE_API_KEY` in the hosting dashboard. Never commit API keys.

## Routes
- `GET /health`
- `GET /market-analysis?symbol=AAPL&provider=auto&interval=1day`
- `POST /paper-orders` (paper mode only)
- `GET /paper-orders`

The in-memory paper journal resets when the service restarts. This starter backend does not send broker orders. Provider symbol coverage, data entitlements, latency and request quotas depend on the user's plan. The signal is a basic illustrative trend/momentum calculation, not a validated trading strategy. Review freshness and contract specifications before use.
