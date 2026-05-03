### Running

**Standard REST scanner**: `python app.py`
**Low-latency WS scanner**: `python app_ws.py` - needs `ccxt.pro`
**Docker**: `docker-compose up -d`

**Before trading**: `python utils/fees.py` to update withdrawal fees

### Next Steps
1. Set up API keys in `.env` - read-only is fine for scanning
2. Tune `config.json`: lower `min_spread` to 0.005 for more alerts
3. For auto-execution, create new branch `feat/trader` - DO NOT run blind
