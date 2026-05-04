import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os

# ============ CONFIG ============
# Read API keys from environment variables (Render secrets)
API_KEYS = {
    'mexc': {
        'apiKey': os.getenv('MEXC_API_KEY', ''),
        'secret': os.getenv('MEXC_SECRET', ''),
        'options': {'defaultType': 'spot'}  # Force spot market
    },
    'kucoin': {
        'apiKey': os.getenv('KUCOIN_API_KEY', ''),
        'secret': os.getenv('KUCOIN_SECRET', ''),
        'password': os.getenv('KUCOIN_PASSPHRASE', ''),
        'options': {'defaultType': 'spot'}  # Force spot market
    },
    'gateio': {
        'apiKey': os.getenv('GATEIO_API_KEY', ''),
        'secret': os.getenv('GATEIO_SECRET', ''),
        'options': {'defaultType': 'spot'}  # Force spot market
    },
    'bitget': {
        'apiKey': os.getenv('BITGET_API_KEY', ''),
        'secret': os.getenv('BITGET_SECRET', ''),
        'options': {'defaultType': 'spot'}  # Force spot market
    },
    'coinex': {
        'apiKey': os.getenv('COINEX_API_KEY', ''),
        'secret': os.getenv('COINEX_SECRET', ''),
        'options': {'defaultType': 'spot'}  # Force spot market
    },
}

MIN_PROFIT_PERCENT = 0.4  # Minimum profit after fees
FEE_PERCENT = {
    'mexc': 0.1,   # 0.1% trading fee
    'kucoin': 0.1,  # 0.1% trading fee
    'gateio': 0.1,  # 0.1% trading fee
    'bitget': 0.1,  # 0.1% trading fee
    'coinex': 0.2,  # 0.2% trading fee
}
MIN_LIQUIDITY_USD = 100  # Minimum liquidity in USD
SCAN_INTERVAL = 4  # Scan every 4 seconds

# Initialize exchanges
exchanges = {}
for name, keys in API_KEYS.items():
    if keys['apiKey'] and keys['secret']:
        try:
            exchanges[name] = getattr(ccxt, name)(keys)
            print(f"✓ Initialized {name} (spot market)")
        except Exception as e:
            print(f"✗ Failed to initialize {name}: {e}")
    else:
        print(f"✗ Skipping {name} - no API keys provided")

app = FastAPI()
latest_opportunities = []

# ============ SCANNER LOGIC ============
async def get_symbols():
    """Get common spot USDT pairs across all exchanges"""
    if len(exchanges) < 2:
        print("⚠️ Need at least 2 exchanges to scan for arbitrage")
        return []
    
    # Load all markets (spot only)
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    
    # Find common symbols across all exchanges
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    
    # ONLY keep USDT pairs (e.g., BTC/USDT, ETH/USDT, UNI/USDT)
    # NO futures, NO perpetuals, NO other pairs
    symbols = [s for s in common if s.endswith('/USDT')]
    
    print(f"✓ Found {len(symbols)} common spot USDT pairs")
    print(f"  Examples: {', '.join(symbols[:5])}...")
    return symbols

async def scan_symbol(symbol):
    """Scan a single trading pair for arbitrage opportunities"""
    try:
        # Fetch order books from all exchanges simultaneously
        obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=3) for ex in exchanges.values()])
        
        # Extract best bid/ask prices
        data = {}
        for (name, ex), ob in zip(exchanges.items(), obs):
            if ob['asks'] and ob['bids']:
                data[name] = {
                    'ask': ob['asks'][0][0],  # Best ask price (what you buy at)
                    'bid': ob['bids'][0][0],  # Best bid price (what you sell at)
                    'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],  # Ask liquidity in USD
                    'bid_vol': ob['bids'][0][1] * ob['bids'][0][0],  # Bid liquidity in USD
                }
        
        # Find arbitrage opportunities
        opps = []
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue  # Same exchange, no arbitrage
                
                # Calculate profit after fees
                # Buy on exchange A: pay ask price + fee
                buy_cost = b['ask'] * (1 + FEE_PERCENT.get(buy_ex, 0.1) / 100)
                # Sell on exchange B: receive bid price - fee
                sell_rev = s['bid'] * (1 - FEE_PERCENT.get(sell_ex, 0.1) / 100)
                # Profit percentage
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                # Available liquidity (limiting factor)
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                # Check if profitable and liquid enough
                if profit_pct > MIN_PROFIT_PERCENT and liquidity > MIN_LIQUIDITY_USD:
                    opps.append({
                        'symbol': symbol,  # e.g., "UNI/USDT"
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': b['ask'],
                        'sell_price': s['bid'],
                        'profit': round(profit_pct, 2),
                        'liquidity': round(liquidity, 0),
                        'timestamp': time.time()
                    })
        return opps
    except Exception as e:
        return []

async def scanner_loop():
    """Main scanning loop - runs forever"""
    global latest_opportunities
    
    if len(exchanges) < 2:
        print("❌ Cannot start scanner - need at least 2 exchanges with valid API keys")
        return
    
    symbols = await get_symbols()
    print(f"\n🔄 Starting arbitrage scanner...")
    print(f"📊 Exchanges: {', '.join(exchanges.keys())}")
    print(f"📈 Pairs to scan: {len(symbols)} spot USDT pairs")
    print(f"⏱️  Scan interval: {SCAN_INTERVAL} seconds")
    print(f"💰 Min profit: {MIN_PROFIT_PERCENT}%")
    print(f"💵 Min liquidity: ${MIN_LIQUIDITY_USD}\n")
    
    while True:
        start_time = time.time()
        
        # Scan all symbols in parallel
        results = await asyncio.gather(*[scan_symbol(s) for s in symbols])
        opps = [opp for sublist in results for opp in sublist]
        
        # Sort by profit and keep top 50
        latest_opportunities = sorted(opps, key=lambda x: x['profit'], reverse=True)[:50]
        
        # Print status
        if latest_opportunities:
            best = latest_opportunities[0]
            print(f"✅ [{time.strftime('%H:%M:%S')}] Found {len(latest_opportunities)} opportunities - Best: {best['symbol']} {best['profit']}%")
        else:
            print(f"🔍 [{time.strftime('%H:%M:%S')}] Scan complete - No opportunities found on {len(symbols)} spot USDT pairs")
        
        # Wait for next scan
        elapsed = time.time() - start_time
        await asyncio.sleep(max(0, SCAN_INTERVAL - elapsed))

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
            padding: 30px 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 36px;
            color: #fff;
            margin-bottom: 10px;
        }
        .badge {
            display: inline-block;
            background: #f39c12;
            color: #0a0a0a;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            margin-top: 5px;
        }
        .stats {
            text-align: center;
            color: #888;
            font-size: 18px;
            margin-bottom: 30px;
        }
        .stats span {
            font-size: 24px;
            font-weight: bold;
            color: #f39c12;
        }
        
        .filters {
            background: #151515;
            border-radius: 16px;
            padding: 25px;
            border: 1px solid #2a2a2a;
            margin-bottom: 30px;
        }
        .filter-group {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }
        .filter-item {
            flex: 1;
            min-width: 180px;
        }
        .filter-item label {
            display: block;
            font-size: 14px;
            color: #888;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }
        .filter-item input {
            width: 100%;
            background: #0a0a0a;
            border: 1px solid #2a2a2a;
            color: #e0e0e0;
            padding: 12px 15px;
            border-radius: 10px;
            font-size: 16px;
        }
        .filter-item input:focus {
            outline: none;
            border-color: #f39c12;
        }
        
        .exchange-section {
            margin-top: 20px;
        }
        .exchange-label {
            font-size: 14px;
            color: #888;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }
        .exchange-buttons {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .exchange-btn {
            background: #0a0a0a;
            border: 1px solid #2a2a2a;
            color: #888;
            padding: 10px 18px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 600;
            transition: all 0.3s;
        }
        .exchange-btn.active {
            background: #f39c12;
            border-color: #f39c12;
            color: #0a0a0a;
        }
        .reset-btn {
            background: #2a2a2a;
            border: 1px solid #3a3a3a;
            color: #e0e0e0;
            padding: 10px 18px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 600;
        }
        
        .opportunities {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        
        .card {
            background: #151515;
            border: 1px solid #2a2a2a;
            border-radius: 16px;
            padding: 20px 25px;
            transition: all 0.2s;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
        }
        .card:hover {
            border-color: #f39c12;
            background: #1a1a1a;
            transform: translateX(5px);
        }
        
        .card-left {
            flex: 1;
            min-width: 250px;
        }
        
        .exchange-row {
            display: flex;
            align-items: baseline;
            gap: 15px;
            flex-wrap: wrap;
            margin-bottom: 12px;
        }
        .exchange-badge {
            font-size: 16px;
            font-weight: 700;
        }
        .exchange-badge.buy {
            color: #2ecc71;
            background: rgba(46, 204, 113, 0.1);
            padding: 4px 10px;
            border-radius: 6px;
        }
        .exchange-badge.sell {
            color: #e74c3c;
            background: rgba(231, 76, 60, 0.1);
            padding: 4px 10px;
            border-radius: 6px;
        }
        .exchange-name {
            color: #fff;
            font-weight: 700;
            font-size: 18px;
            margin-left: 5px;
        }
        
        .symbol {
            font-size: 24px;
            font-weight: 800;
            color: #fff;
            font-family: 'Courier New', monospace;
            margin: 10px 0;
        }
        
        .details {
            display: flex;
            gap: 25px;
            font-size: 15px;
            color: #888;
            flex-wrap: wrap;
        }
        .verified {
            color: #f39c12;
        }
        
        .profit {
            font-size: 32px;
            font-weight: 800;
            font-family: 'Courier New', monospace;
            text-align: right;
            min-width: 100px;
        }
        .profit.high { color: #2ecc71; }
        .profit.mid { color: #2ecc71; }
        .profit.low { color: #27ae60; }
        
        .no-data {
            text-align: center;
            color: #555;
            padding: 60px;
            font-size: 20px;
            background: #151515;
            border-radius: 16px;
            border: 1px solid #2a2a2a;
        }
        
        .info-text {
            text-align: center;
            color: #666;
            font-size: 12px;
            margin-top: 20px;
            padding: 10px;
        }
        
        @media (max-width: 768px) {
            body { padding: 15px; }
            .header h1 { font-size: 28px; }
            .symbol { font-size: 20px; }
            .profit { font-size: 24px; }
            .card { padding: 15px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Cross-Exchange Arbitrage Scanner</h1>
            <div class="badge">📊 SPOT MARKET | ONLY USDT PAIRS</div>
            <div class="stats">
                MEXC | KuCoin | Gate.io | Bitget | CoinEx
                | <span id="count">0</span> opportunities
            </div>
        </div>
        
        <div class="filters">
            <div class="filter-group">
                <div class="filter-item">
                    <label>💰 Min Profit (%)</label>
                    <input type="number" id="minProfit" step="0.1" value="0.4">
                </div>
                <div class="filter-item">
                    <label>💎 Min Liquidity ($)</label>
                    <input type="number" id="minLiquidity" step="100" value="100">
                </div>
                <div class="filter-item">
                    <label>🔍 Symbol Filter</label>
                    <input type="text" id="symbolFilter" placeholder="UNI, BTC, ETH">
                </div>
            </div>
            
            <div class="exchange-section">
                <div class="exchange-label">🏦 Filter by Exchange</div>
                <div class="exchange-buttons">
                    <button class="exchange-btn active" data-exchange="all">All</button>
                    <button class="exchange-btn" data-exchange="MEXC">MEXC</button>
                    <button class="exchange-btn" data-exchange="KUCOIN">KuCoin</button>
                    <button class="exchange-btn" data-exchange="GATEIO">Gate.io</button>
                    <button class="exchange-btn" data-exchange="BITGET">Bitget</button>
                    <button class="exchange-btn" data-exchange="COINEX">CoinEx</button>
                    <button class="reset-btn" id="resetFilters">🔄 Reset All</button>
                </div>
            </div>
        </div>
        
        <div class="opportunities" id="opps"></div>
        <div class="info-text">
            ⚡ Scanning spot markets only (USDT pairs) | Fees included | Updates every 4 seconds
        </div>
    </div>

    <script>
        let allOpportunities = [];
        let currentFilters = {
            minProfit: 0.4,
            minLiquidity: 100,
            symbolFilter: '',
            exchangeFilter: 'all'
        };
        
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function timeAgo(ts) {
            const secs = Math.floor(Date.now()/1000 - ts);
            if (secs < 60) return `${secs} second${secs !== 1 ? 's' : ''} ago`;
            const mins = Math.floor(secs / 60);
            return `${mins} minute${mins !== 1 ? 's' : ''} ago`;
        }
        
        function profitClass(p) {
            if (p >= 1.5) return 'high';
            if (p >= 0.8) return 'mid';
            return 'low';
        }
        
        function formatExchangeName(name) {
            const names = {
                'GATEIO': 'Gate.io',
                'KUCOIN': 'KuCoin',
                'MEXC': 'MEXC',
                'BITGET': 'Bitget',
                'COINEX': 'CoinEx'
            };
            return names[name] || name;
        }
        
        function filterOpportunities() {
            return allOpportunities.filter(opp => {
                if (opp.profit < currentFilters.minProfit) return false;
                if (opp.liquidity < currentFilters.minLiquidity) return false;
                if (currentFilters.symbolFilter && 
                    !opp.symbol.toUpperCase().includes(currentFilters.symbolFilter.toUpperCase())) return false;
                if (currentFilters.exchangeFilter !== 'all') {
                    if (opp.buy_exchange !== currentFilters.exchangeFilter && 
                        opp.sell_exchange !== currentFilters.exchangeFilter) return false;
                }
                return true;
            });
        }
        
        function updateDisplay() {
            const filtered = filterOpportunities();
            const container = document.getElementById('opps');
            document.getElementById('count').textContent = filtered.length;
            
            if (filtered.length === 0) {
                container.innerHTML = '<div class="no-data">🔍 No arbitrage opportunities found<br><span style="font-size: 14px;">Scanning spot USDT pairs across 5 exchanges...</span></div>';
                return;
            }
            
            container.innerHTML = filtered.map(opp => `
                <div class="card">
                    <div class="card-left">
                        <div class="exchange-row">
                            <span class="exchange-badge buy">BUY</span>
                            <span class="exchange-name">${formatExchangeName(opp.buy_exchange)}</span>
                            <span class="exchange-badge sell">SELL</span>
                            <span class="exchange-name">${formatExchangeName(opp.sell_exchange)}</span>
                        </div>
                        <div class="symbol">${opp.symbol}</div>
                        <div class="details">
                            <span>💰 Liquidity: $${opp.liquidity.toLocaleString()}</span>
                            <span class="verified">⏱️ ${timeAgo(opp.timestamp)}</span>
                        </div>
                    </div>
                    <div class="profit ${profitClass(opp.profit)}">${opp.profit}%</div>
                </div>
            `).join('');
        }
        
        document.getElementById('minProfit').addEventListener('input', (e) => {
            currentFilters.minProfit = parseFloat(e.target.value) || 0;
            updateDisplay();
        });
        
        document.getElementById('minLiquidity').addEventListener('input', (e) => {
            currentFilters.minLiquidity = parseFloat(e.target.value) || 0;
            updateDisplay();
        });
        
        document.getElementById('symbolFilter').addEventListener('input', (e) => {
            currentFilters.symbolFilter = e.target.value;
            updateDisplay();
        });
        
        document.querySelectorAll('.exchange-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.exchange-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentFilters.exchangeFilter = btn.dataset.exchange;
                updateDisplay();
            });
        });
        
        document.getElementById('resetFilters').addEventListener('click', () => {
            document.getElementById('minProfit').value = 0.4;
            document.getElementById('minLiquidity').value = 100;
            document.getElementById('symbolFilter').value = '';
            document.querySelectorAll('.exchange-btn').forEach(b => b.classList.remove('active'));
            document.querySelector('[data-exchange="all"]').classList.add('active');
            
            currentFilters = {
                minProfit: 0.4,
                minLiquidity: 100,
                symbolFilter: '',
                exchangeFilter: 'all'
            };
            updateDisplay();
        });
        
        ws.onmessage = (event) => {
            allOpportunities = JSON.parse(event.data);
            updateDisplay();
        };
        
        ws.onclose = () => {
            console.log('WebSocket disconnected, reconnecting...');
            setTimeout(() => location.reload(), 3000);
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
    port = int(os.getenv('PORT', 8000))
    print(f"\n{'='*50}")
    print(f"🚀 ARBITRAGE SCANNER STARTING")
    print(f"{'='*50}")
    print(f"📊 Market: SPOT only")
    print(f"💰 Pairs: * / USDT only")
    print(f"🏦 Exchanges: {', '.join(exchanges.keys())}")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*50}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
