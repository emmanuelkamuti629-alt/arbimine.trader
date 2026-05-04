import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os

# ============ CONFIG ============
API_KEYS = {
    'mexc': {
        'apiKey': os.getenv('MEXC_API_KEY', ''),
        'secret': os.getenv('MEXC_SECRET', ''),
        'options': {'defaultType': 'spot'}
    },
    'kucoin': {
        'apiKey': os.getenv('KUCOIN_API_KEY', ''),
        'secret': os.getenv('KUCOIN_SECRET', ''),
        'password': os.getenv('KUCOIN_PASSPHRASE', ''),
        'options': {'defaultType': 'spot'}
    },
    'gateio': {
        'apiKey': os.getenv('GATEIO_API_KEY', ''),
        'secret': os.getenv('GATEIO_SECRET', ''),
        'options': {'defaultType': 'spot'}
    },
    'bitget': {
        'apiKey': os.getenv('BITGET_API_KEY', ''),
        'secret': os.getenv('BITGET_SECRET', ''),
        'options': {'defaultType': 'spot'}
    },
    'coinex': {
        'apiKey': os.getenv('COINEX_API_KEY', ''),
        'secret': os.getenv('COINEX_SECRET', ''),
        'options': {'defaultType': 'spot'}
    },
}

MIN_PROFIT_PERCENT = 0.4
FEE_PERCENT = {
    'mexc': 0.1,
    'kucoin': 0.1,
    'gateio': 0.1,
    'bitget': 0.1,
    'coinex': 0.2,
}
MIN_LIQUIDITY_USD = 100
SCAN_INTERVAL = 5  # Scan every 5 seconds
DISPLAY_REFRESH_INTERVAL = 30  # UI refresh every 30 seconds

# Initialize exchanges
exchanges = {}
for name, keys in API_KEYS.items():
    if keys['apiKey'] and keys['secret']:
        try:
            exchanges[name] = getattr(ccxt, name)(keys)
            print(f"✓ Initialized {name}")
        except Exception as e:
            print(f"✗ Failed to initialize {name}: {e}")

app = FastAPI()
latest_opportunities = []
last_scan_time = None
scan_results_history = []  # Store last 6 scans (30 seconds worth)

# ============ SCANNER LOGIC ============
async def get_symbols():
    if len(exchanges) < 2:
        return []
    
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    symbols = [s for s in common if s.endswith('/USDT')]
    print(f"✓ Found {len(symbols)} USDT pairs")
    return symbols

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
                
                buy_cost = b['ask'] * (1 + FEE_PERCENT.get(buy_ex, 0.1) / 100)
                sell_rev = s['bid'] * (1 - FEE_PERCENT.get(sell_ex, 0.1) / 100)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                if profit_pct > MIN_PROFIT_PERCENT and liquidity > MIN_LIQUIDITY_USD:
                    opps.append({
                        'symbol': symbol.replace('/USDT', ''),
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'profit': round(profit_pct, 2),
                        'liquidity': round(liquidity, 0),
                        'timestamp': time.time()
                    })
        return opps
    except:
        return []

async def scanner_loop():
    global latest_opportunities, last_scan_time, scan_results_history
    
    if len(exchanges) < 2:
        print("❌ Need at least 2 exchanges with valid API keys")
        return
    
    symbols = await get_symbols()
    print(f"\n{'='*60}")
    print(f"🔄 ARBITRAGE SCANNER ACTIVE")
    print(f"{'='*60}")
    print(f"📊 Connected to: {', '.join(exchanges.keys())}")
    print(f"📈 Scanning: {len(symbols)} spot USDT pairs")
    print(f"⏱️  Scan frequency: Every {SCAN_INTERVAL} seconds")
    print(f"🖥️  Display refresh: Every {DISPLAY_REFRESH_INTERVAL} seconds")
    print(f"💰 Min profit: {MIN_PROFIT_PERCENT}%")
    print(f"💵 Min liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"{'='*60}\n")
    
    while True:
        scan_start = time.time()
        last_scan_time = scan_start
        
        print(f"[{time.strftime('%H:%M:%S')}] 🔍 Scanning for opportunities...")
        
        # Scan all symbols
        results = await asyncio.gather(*[scan_symbol(s) for s in symbols])
        current_scan_results = [opp for sublist in results for opp in sublist]
        
        # Store this scan result
        scan_results_history.append({
            'timestamp': scan_start,
            'opportunities': current_scan_results
        })
        
        # Keep only last 30 seconds worth of scans (6 scans if 5s interval)
        max_history = DISPLAY_REFRESH_INTERVAL // SCAN_INTERVAL
        if len(scan_results_history) > max_history:
            scan_results_history.pop(0)
        
        # Update displayed opportunities with most recent valid opportunities
        # Show opportunities from the latest scan that had any results
        for scan in reversed(scan_results_history):
            if scan['opportunities']:
                latest_opportunities = sorted(scan['opportunities'], key=lambda x: x['profit'], reverse=True)[:50]
                break
        else:
            latest_opportunities = []
        
        if latest_opportunities:
            best = latest_opportunities[0]
            print(f"[{time.strftime('%H:%M:%S')}] ✅ Found {len(current_scan_results)} opportunities in this scan")
            print(f"    Displaying {len(latest_opportunities)} best opportunities from last {DISPLAY_REFRESH_INTERVAL}s")
            if len(current_scan_results) > 0:
                print(f"    Best now: {best['symbol']} {best['profit']}% (${best['liquidity']})")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] 🔍 No opportunities found in this scan")
        
        elapsed = time.time() - scan_start
        wait_time = max(0, SCAN_INTERVAL - elapsed)
        
        # Show next scan countdown
        if wait_time > 0:
            print(f"[{time.strftime('%H:%M:%S')}] ⏳ Next scan in {wait_time:.1f} seconds\n")
        
        await asyncio.sleep(wait_time)

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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            padding: 20px;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #2a2a2a;
        }
        .header h1 {
            font-size: 28px;
            color: #fff;
            margin-bottom: 8px;
        }
        .badge {
            display: inline-block;
            background: #f39c12;
            color: #0a0a0a;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            margin: 5px 0;
        }
        .api-badge {
            display: inline-block;
            background: #2ecc71;
            color: #0a0a0a;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            margin: 5px 0;
            margin-left: 10px;
        }
        .exchanges {
            text-align: center;
            color: #888;
            font-size: 13px;
            margin: 10px 0;
        }
        .count {
            text-align: center;
            font-size: 24px;
            font-weight: bold;
            color: #f39c12;
            margin: 10px 0;
        }
        .scan-info {
            text-align: center;
            font-size: 12px;
            color: #666;
            margin-bottom: 20px;
        }
        .next-scan {
            text-align: center;
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }
        
        .opportunities {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .card {
            background: #151515;
            border: 1px solid #2a2a2a;
            border-radius: 12px;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.2s;
        }
        .card:hover {
            border-color: #f39c12;
            background: #1a1a1a;
        }
        
        .left {
            display: flex;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }
        
        .exchange-pair {
            background: #0a0a0a;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
        }
        .buy {
            color: #2ecc71;
        }
        .sell {
            color: #e74c3c;
        }
        .exchange-name {
            color: #fff;
            margin: 0 3px;
        }
        
        .symbol {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            font-family: 'Courier New', monospace;
            min-width: 80px;
        }
        
        .right {
            text-align: right;
        }
        
        .profit {
            font-size: 24px;
            font-weight: 700;
            font-family: 'Courier New', monospace;
            color: #2ecc71;
        }
        
        .liquidity {
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }
        
        .time {
            font-size: 11px;
            color: #666;
            margin-top: 4px;
        }
        
        .no-data {
            text-align: center;
            color: #555;
            padding: 40px;
            background: #151515;
            border-radius: 12px;
            border: 1px solid #2a2a2a;
        }
        
        .footer {
            text-align: center;
            color: #555;
            font-size: 11px;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #2a2a2a;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .scanning {
            animation: pulse 1s infinite;
        }
        
        @media (max-width: 600px) {
            .card { flex-direction: column; align-items: flex-start; }
            .right { text-align: left; margin-top: 10px; }
            .left { gap: 10px; }
            .symbol { font-size: 16px; }
            .profit { font-size: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Cross-Exchange Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 SPOT | USDT PAIRS ONLY</span>
                <span class="api-badge">🔐 REAL EXCHANGE APIS</span>
            </div>
            <div class="exchanges">MEXC | KuCoin | Gate.io | Bitget | CoinEx</div>
            <div class="count" id="count">0 opportunities</div>
            <div class="scan-info">
                🔍 Scanning every 5 seconds | 📺 Display refreshes every 30 seconds
            </div>
            <div class="next-scan" id="nextScan">⏳ Next scan in 5s</div>
        </div>
        
        <div class="opportunities" id="opps"></div>
        
        <div class="footer">
            🔐 Using PRIVATE API keys | REAL exchange data | Best opportunities from last 30 seconds
        </div>
    </div>

    <script>
        let allOpportunities = [];
        let lastUpdateTime = Date.now();
        
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function timeAgo(ts) {
            const secs = Math.floor(Date.now()/1000 - ts);
            if (secs < 60) return `${secs}s ago`;
            return `${Math.floor(secs/60)}m ago`;
        }
        
        function formatExchange(name) {
            const names = {
                'GATEIO': 'Gate.io',
                'KUCOIN': 'KuCoin',
                'MEXC': 'MEXC',
                'BITGET': 'Bitget',
                'COINEX': 'CoinEx'
            };
            return names[name] || name;
        }
        
        function updateCountdown() {
            const nextScanEl = document.getElementById('nextScan');
            if (!nextScanEl) return;
            
            const elapsed = (Date.now() - lastUpdateTime) / 1000;
            // Show scan countdown (5 seconds)
            const scanRemaining = Math.max(0, 5 - (elapsed % 5));
            
            if (scanRemaining <= 0.1) {
                nextScanEl.innerHTML = '🔄 Scanning now...';
                nextScanEl.classList.add('scanning');
            } else {
                nextScanEl.innerHTML = `⏳ Next scan in ${Math.ceil(scanRemaining)}s`;
                nextScanEl.classList.remove('scanning');
            }
        }
        
        function updateDisplay() {
            const container = document.getElementById('opps');
            const countEl = document.getElementById('count');
            
            if (allOpportunities.length === 0) {
                countEl.innerHTML = '0 opportunities';
                container.innerHTML = '<div class="no-data">🔍 No arbitrage opportunities found<br><span style="font-size: 12px;">Scanning REAL exchange data every 5 seconds...</span></div>';
                return;
            }
            
            countEl.innerHTML = allOpportunities.length + ' opportunities';
            
            container.innerHTML = allOpportunities.map(opp => `
                <div class="card">
                    <div class="left">
                        <div class="exchange-pair">
                            <span class="buy">BUY</span>
                            <span class="exchange-name">${formatExchange(opp.buy_exchange)}</span>
                            <span class="sell"> → SELL</span>
                            <span class="exchange-name">${formatExchange(opp.sell_exchange)}</span>
                        </div>
                        <div class="symbol">${opp.symbol}</div>
                    </div>
                    <div class="right">
                        <div class="profit">+${opp.profit}%</div>
                        <div class="liquidity">💰 $${opp.liquidity.toLocaleString()}</div>
                        <div class="time">⏱️ ${timeAgo(opp.timestamp)}</div>
                    </div>
                </div>
            `).join('');
        }
        
        ws.onmessage = (event) => {
            allOpportunities = JSON.parse(event.data);
            lastUpdateTime = Date.now();
            updateDisplay();
        };
        
        ws.onclose = () => {
            setTimeout(() => location.reload(), 3000);
        };
        
        // Update countdown every second
        setInterval(updateCountdown, 1000);
        updateCountdown();
    </script>
</body>
</html>
    """)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        # Send latest opportunities to UI
        await websocket.send_json(latest_opportunities)
        await asyncio.sleep(DISPLAY_REFRESH_INTERVAL)  # Send updates every 30 seconds

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    print(f"\n{'='*60}")
    print(f"🚀 ARBITRAGE SCANNER WITH REAL EXCHANGE APIS")
    print(f"{'='*60}")
    print(f"🔐 Authentication: Using YOUR private API keys from Render")
    print(f"📊 Market Type: SPOT only")
    print(f"💰 Pairs: */USDT only")
    print(f"⏱️  Scan frequency: Every {SCAN_INTERVAL} seconds")
    print(f"🖥️  Display refresh: Every {DISPLAY_REFRESH_INTERVAL} seconds")
    print(f"🏦 Connected Exchanges: {', '.join(exchanges.keys())}")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
