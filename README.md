# Cross-CEX Arbitrage Scanner

Real-time spot arbitrage scanner for MEXC, Kucoin, Gate.io, CoinEx, Bitget.
Scans 180+ pairs simultaneously.

### Setup
1. Clone: `git clone https://github.com/yourusername/cross-cex-arb-scanner`
2. Install: `pip install -r requirements.txt`
3. Add API keys to `.env` file. Read-only keys work for scanning.
4. Optional: Set `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` for alerts
5. Run: `python app.py`

### Config
Edit `config.json`:
- `min_spread`: 0.008 = 0.8% minimum net profit after fees
- `min_volume_usd`: Skip pairs with < $50k 24h volume
- `scan_interval`: Seconds between full scans

### Notes
1. This is SCANNER ONLY. No auto-trading.
2. Spreads < 1s get eaten by bots. Best arbs are on low-cap alts + new listings.
3. Always check withdrawal status before acting. A 2% spread is 0% if withdrawals are suspended.

### Disclaimer
Crypto trading is risky. Use at your own risk. This is not financial advice.
