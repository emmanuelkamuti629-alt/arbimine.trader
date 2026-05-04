import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
from datetime import datetime
import json

# ============ CONFIG ============
API_KEYS = {
    'mexc': {'apiKey': 'YOUR_MEXC_KEY', 'secret': 'YOUR_MEXC_SECRET'},
    'kucoin': {'apiKey': 'YOUR_KUCOIN_KEY', 'secret': 'YOUR_KUCOIN_SECRET', 'password': 'YOUR_PASSPHRASE'},
    'coinex': {'apiKey': 'YOUR_COINEX_KEY', 'secret': 'YOUR_COINEX_SECRET'},
}

MIN_PROFIT_PERCENT = 0.4 # Show smaller spreads since we're just scanning
FEE_PERCENT = {'mexc': 0.1, 'kucoin': 0.1, 'coinex': 0.2}
MIN_LIQUIDITY_USD = 100
SCAN_INTERVAL = 4 # seconds

exchanges = {name: getattr(ccxt, name)(keys) for name, keys in API_KEYS.items()}
app = FastAPI()
latest_opportunities = []

# ============ SCANNER LOGIC ============
async def get_symbols():
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    return [s for s in common if s.endswith('/USDT')]

async def scan_symbol(symbol):
    try:
        obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=3) for ex in exchanges.values()])
        
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
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': b['ask'],
                        'sell_price': s['bid'],
                        'profit': round(profit_pct, 2),
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
        latest_opportunities = sorted(opps, key=lambda x: x['profit'], reverse=True)[:50]
        await asyncio.sleep(SCAN_INTERVAL)

# ============ WEB UI ============
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
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #e0e0e0;
            font-family: 'Segoe UI', system-ui, sans-serif;
            padding: 20px;
        }
       .header {
            text-align: center;
            margin-bottom: 20px;
        }
       .header h1 {
            font-size: 24px;
            color: #fff;
            margin-bottom: 5px;
        }
       .stats {
            text-align: center;
            color: #888;
            font-size: 14px;
            margin-bottom: 20px;
        }
       .opportunities {
            max-width: 600px;
            margin: 0 auto;
        }
       .card {
            background: #151515;
            border: 1px solid #2a2a2a;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.3s;
        }
       .card:hover {
            border-color: #3a3a3a;
            transform: translateY(-2px);
        }
       .left {
            display: flex;
            gap: 12px;
            align-items: center;
        }
       .exchanges {
            background: #1a1a1a;
            border-radius: 8px;
            padding: 8px 12px;
            min-width: 110px;
        }
       .buy,.sell {
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
       .buy { color: #f1c40f; }
       .sell { color: #f1c40f; }
       .ex-name { color: #fff; margin-left: 6px; }
       .info {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
       .symbol {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
        }
       .liquidity {
            font-size: 13px;
            color: #888;
        }
       .verified {
            font-size: 12px;
            color: #f39c12;
        }
       .profit {
            font-size: 24px;
            font-weight: 700;
        }
       .profit.high { color: #2ecc71; }
       .profit.mid { color: #2ecc71; }
       .profit.low { color: #27ae60; }
       .no-data {
            text-align: center;
            color: #555;
            padding: 40px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Cross-Exchange Arbitrage Scanner</h1>
        <div class="stats">MEXC | KuCoin | CoinEx | <span id="count">0</span> opportunities</div>
    </div>
    <div class="opportunities" id="opps"></div>

    <script>
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function timeAgo(ts) {
            const secs = Math.floor(Date.now()/1000 - ts);
            if (secs < 60) return `${secs} seconds ago`;
            return `${Math.floor(secs/60)} min ago`;
        }
        
        function profitClass(p) {
            if (p >= 1.5) return 'high';
            if (p >= 0.8) return 'mid';
            return 'low';
        }
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const container = document.getElementById('opps');
            document.getElementById('count').textContent = data.length;
            
            if (data.length === 0) {
                container.innerHTML = '<div class="no-data">Scanning... No spreads above ' + """ + str(MIN_PROFIT_PERCENT) + """ + '%</div>';
                return;
            }
            
            container.innerHTML = data.map(opp => `
                <div class="card">
                    <div class="left">
                        <div class="exchanges">
                            <div class="buy">BUY <span class="ex-name">${opp.buy_exchange}</span></div>
                            <div class="sell">SELL <span class="ex-name">${opp.sell_exchange}</span></div>
                        </div>
                        <div class="info">
                            <div class="symbol">${opp.symbol}</div>
                            <div class="liquidity">Liquidity: $${opp.liquidity.toLocaleString()}</div>
                            <div class="verified">Last Verified ${timeAgo(opp.timestamp)}</div>
                        </div>
                    </div>
                    <div class="profit ${profitClass(opp.profit)}">${opp.profit}%</div>
                </div>
            `).join('');
        };
    </script>
</body>
</html>
    """)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        await websocket.send_json(latest_opportunities)
        await asyncio.sleep(1)

if __name__ == "__main__":
    print("Starting Arbitrage Scanner Dashboard...")
    print("Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
