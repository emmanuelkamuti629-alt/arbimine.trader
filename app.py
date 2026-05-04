import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os
from dotenv import load_dotenv

load_dotenv()

API_KEYS = {
    'mexc': {'apiKey': os.getenv('MEXC_KEY'), 'secret': os.getenv('MEXC_SECRET')},
    'kucoin': {'apiKey': os.getenv('KUCOIN_KEY'), 'secret': os.getenv('KUCOIN_SECRET'), 'password': os.getenv('KUCOIN_PASS')},
    'coinex': {'apiKey': os.getenv('COINEX_KEY'), 'secret': os.getenv('COINEX_SECRET')},
}

MIN_PROFIT_PERCENT = 0.3
FEE_PERCENT = {'mexc': 0.1, 'kucoin': 0.1, 'coinex': 0.2}
MIN_LIQUIDITY_USD = 100
SCAN_INTERVAL = 5

exchanges = {name: getattr(ccxt, name)(keys) for name, keys in API_KEYS.items()}
app = FastAPI()
latest_opportunities = []

async def get_symbols():
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    return [s for s in common if s.endswith('/USDT')]

async def scan_symbol(symbol):
    try:
        obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=5) for ex in exchanges.values()])
        data = {}
        for (name, ex), ob in zip(exchanges.items(), obs):
            if ob['asks'] and ob['bids']:
                data[name] = {
                    'ask': ob['asks'][0][0],
                    'bid': ob['bids'][0][0],
                    'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],
                    'bid_vol': ob['bids'][0][1] * ob['bids'][0][0]
                }
        
        opps = []
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue
                buy_cost = b['ask'] * (1 + FEE_PERCENT[buy_ex] / 100)
                sell_rev = s['bid'] * (1 - FEE_PERCENT[sell_ex] / 100)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                if profit_pct > MIN_PROFIT_PERCENT and liquidity > MIN_LIQUIDITY_USD:
                    opps.append({
                        'symbol': symbol,
                        'buy_exchange': buy_ex,
                        'sell_exchange': sell_ex,
                        'profit': round(profit_pct, 1),
                        'liquidity': round(liquidity, 0),
                        'timestamp': time.time()
                    })
        return opps
    except:
        return []

async def scanner_loop():
    global latest_opportunities
    symbols = await get_symbols()
    print(f"Scanning {len(symbols)} pairs...")
    while True:
        results = await asyncio.gather(*[scan_symbol(s) for s in symbols])
        opps = [opp for sublist in results for opp in sublist]
        latest_opportunities = sorted(opps, key=lambda x: x['profit'], reverse=True)[:100]
        await asyncio.sleep(SCAN_INTERVAL)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(scanner_loop())

@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Cross-Exchange Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0D0D0D;
            color: #EAEAEA;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
      .header {
            background: #000;
            padding: 12px 0;
            text-align: center;
            position: sticky;
            top: 0;
            z-index: 10;
        }
      .header h1 {
            font-size: 16px;
            font-weight: 600;
            color: #fff;
        }
      .header p {
            font-size: 12px;
            color: #888;
            margin-top: 2px;
        }
      .container {
            padding: 8px;
            max-width: 500px;
            margin: 0 auto;
        }
      .card {
            background: #1A1A1A;
            border-radius: 12px;
            padding: 14px 12px;
            margin-bottom: 8px;
            display: grid;
            grid-template-columns: 95px 1fr auto;
            gap: 12px;
            align-items: center;
        }
      .ex-box {
            background: #252525;
            border-radius: 8px;
            padding: 8px;
        }
      .ex-line {
            font-size: 11px;
            font-weight: 700;
            line-height: 1.4;
        }
      .ex-line.label {
            color: #FFC107;
            display: inline-block;
            width: 30px;
        }
      .ex-line.name {
            color: #fff;
            text-transform: capitalize;
        }
      .mid {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
      .symbol {
            font-size: 16px;
            font-weight: 600;
            color: #fff;
        }
      .liq {
            font-size: 13px;
            color: #999;
        }
      .verified {
            font-size: 12px;
            color: #FFB300;
            margin-top: 2px;
        }
      .profit {
            font-size: 22px;
            font-weight: 700;
            color: #00E676
