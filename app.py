import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os
import json
from datetime import datetime

# ================= CONFIG - ULTRA FAST =================
EXCHANGE_IDS = ["gateio", "kucoin", "mexc", "bitget", "coinex"]
MAX_COINS = 500  # Scan ALL coins
CACHE_FILE = "symbols_cache.json"
CACHE_TTL = 3600  # Cache markets for 1 hour

# ================= TRADING CONFIG =================
TRADING_FEES = {
    'gateio': 0.1,
    'kucoin': 0.1,
    'mexc': 0.1,
    'bitget': 0.1,
    'coinex': 0.2,
}

# ULTRA FAST SETTINGS
MIN_PROFIT_PERCENT = 0.01  # Catch tiny spreads
MIN_LIQUIDITY_USD = 10     # Minimum liquidity
BATCH_SIZE = 50            # Big batches for speed
MARKET_LOAD_TIMEOUT = 20   # Timeout for loading
SCAN_TIMEOUT = 2.0         # Per-symbol scan timeout

app = FastAPI()
latest_opportunities = []
all_symbols = []
exchanges = {}
exchanges_loaded = 0
scanning_active = False
scan_round = 0

# ================= CACHE FUNCTIONS =================
def load_cached_symbols():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                if time.time() - data.get('timestamp', 0) < CACHE_TTL:
                    return data.get('symbols', {})
    except:
        pass
    return {}

def save_cached_symbols(symbols_dict):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({'timestamp': time.time(), 'symbols': symbols_dict}, f)
    except:
        pass

# ================= LOAD EXCHANGE =================
async def load_exchange(exchange_id, cached_data):
    global exchanges_loaded, all_symbols, scanning_active
    
    start = datetime.now()
    exchange = None

    try:
        exchange_class = getattr(ccxt, exchange_id)

        config = {
            "enableRateLimit": False,
            "timeout": 20000,
            "options": {"defaultType": "spot"}
        }

        # ✅ FIXED: Correct env variable names for each exchange
        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY', '')
        
        if exchange_id == 'mexc' or exchange_id == 'bitget':
            api_secret = os.getenv(f'{exchange_id.upper()}_SECRET_KEY', '')
        elif exchange_id == 'kucoin':
            api_secret = os.getenv(f'{exchange_id.upper()}_API_SECRET', '')
        else:
            api_secret = os.getenv(f'{exchange_id.upper()}_API_SECRET', '')
        
        if exchange_id == 'kucoin':
            passphrase = os.getenv('KUCOIN_API_PASSPHRASE', '')
            if passphrase:
                config['password'] = passphrase
        elif exchange_id == 'bitget':
            passphrase = os.getenv('BITGET_PASSPHRASE', '')
            if passphrase:
                config['password'] = passphrase

        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret

        exchange = exchange_class(config)
        print(f"⚡ Loading {exchange_id}...")

        # CHECK CACHE FIRST
        if exchange_id in cached_data:
            symbols = [s for s in cached_data[exchange_id] if s.endswith('/USDT')]
            print(f"✓ {exchange_id}: {len(symbols)} USDT pairs (cached)")
        else:
            # LOAD FRESH MARKETS
            print(f"📡 {exchange_id}: Fetching all spot markets...")
            try:
                await asyncio.wait_for(exchange.load_markets(), timeout=MARKET_LOAD_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"⏰ {exchange_id} TIMEOUT")
                if exchange:
                    await exchange.close()
                return None
            
            usdt_symbols = [s for s in exchange.symbols if s.endswith('/USDT')]
            symbols = usdt_symbols[:MAX_COINS]
            print(f"✓ {exchange_id}: {len(symbols)} USDT pairs loaded")

        exchanges[exchange_id] = exchange
        exchanges_loaded += 1
        
        # Merge all symbols
        symbol_set = set(all_symbols)
        symbol_set.update(symbols)
        all_symbols = list(symbol_set)
        
        elapsed = (datetime.now() - start).total_seconds()
        print(f"📊 {exchange_id} ready in {elapsed:.1f}s | Total: {len(all_symbols)} pairs | Exchanges: {exchanges_loaded}")
        
        # Start scanner when we have at least 2 exchanges
        if exchanges_loaded >= 2 and not scanning_active:
            scanning_active = True
            asyncio.create_task(continuous_scanner())

        return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

    except Exception as e:
        print(f"❌ {exchange_id} ERROR: {type(e).__name__}: {str(e)[:100]}")
        if exchange:
            try:
                await exchange.close()
            except:
                pass
        return None

# ================= SAFE LOAD WRAPPER =================
async def safe_load(exchange_id, cached_data):
    try:
        return await load_exchange(exchange_id, cached_data)
    except Exception as e:
        print(f"❌ {exchange_id} FATAL: {e}")
        return None

# ================= INITIALIZE ALL EXCHANGES =================
async def initialize_exchanges():
    global exchanges, all_symbols, exchanges_loaded, scanning_active
    
    print("\n" + "="*60)
    print("⚡ ULTRA-FAST ARBITRAGE SCANNER")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}% | Min Liq: ${MIN_LIQUIDITY_USD}")
    print(f"📊 Max pairs per exchange: {MAX_COINS}")
    print("="*60 + "\n")

    cached_data = load_cached_symbols()
    exchanges_loaded = 0
    scanning_active = False
    all_symbols = []

    # Load all exchanges in parallel
    tasks = [safe_load(eid, cached_data) for eid in EXCHANGE_IDS]
    results = await asyncio.gather(*tasks)

    # Save successful caches
    new_cache = {}
    for result in results:
        if result and result.get("symbols"):
            new_cache[result["id"]] = result["symbols"]
    
    if new_cache:
        save_cached_symbols(new_cache)

    print("\n" + "="*60)
    print(f"✅ {exchanges_loaded}/{len(EXCHANGE_IDS)} exchanges loaded")
    print(f"📊 {len(all_symbols)} unique USDT pairs to scan")
    print("="*60 + "\n")
    
    return exchanges_loaded >= 2

# ================= HEALTH CHECK =================
@app.get("/health")
@app.head("/health")
async def health():
    return {
        "status": "ok",
        "exchanges_loaded": exchanges_loaded,
        "active_opportunities": len(latest_opportunities),
        "symbols_loaded": len(all_symbols),
        "scanning_active": scanning_active,
        "scan_round": scan_round,
        "timestamp": datetime.now().isoformat()
    }

# ================= ULTRA FAST SCAN SINGLE SYMBOL =================
async def scan_symbol(symbol):
    try:
        tasks = []
        ex_names = []
        
        for name, ex in exchanges.items():
            tasks.append(asyncio.wait_for(ex.fetch_order_book(symbol, limit=1), timeout=1.5))
            ex_names.append(name)
        
        if len(tasks) < 2:
            return None
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        data = {}
        for name, result in zip(ex_names, results):
            if isinstance(result, Exception):
                continue
            if result and result.get('asks') and result.get('bids'):
                if len(result['asks']) > 0 and len(result['bids']) > 0:
                    ask_price = result['asks'][0][0]
                    bid_price = result['bids'][0][0]
                    ask_vol = result['asks'][0][1] if len(result['asks'][0]) > 1 else 0
                    bid_vol = result['bids'][0][1] if len(result['bids'][0]) > 1 else 0
                    data[name] = {
                        'ask': ask_price,
                        'bid': bid_price,
                        'ask_vol_usd': ask_vol * ask_price,
                        'bid_vol_usd': bid_vol * bid_price,
                    }
        
        if len(data) < 2:
            return None
        
        best_opp = None
        best_profit = 0
        
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex:
                    continue
                
                buy_fee = TRADING_FEES.get(buy_ex, 0.1) / 100
                sell_fee = TRADING_FEES.get(sell_ex, 0.1) / 100
                
                buy_cost = b['ask'] * (1 + buy_fee)
                sell_rev = s['bid'] * (1 - sell_fee)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                liquidity = min(b['ask_vol_usd'], s['bid_vol_usd'])
                
                if profit_pct > best_profit and liquidity >= MIN_LIQUIDITY_USD:
                    best_profit = profit_pct
                    best_opp = {
                        'symbol': symbol.replace('/USDT', ''),
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': b['ask'],
                        'sell_price': s['bid'],
                        'spread': round(profit_pct, 4),
                        'net_profit': round(profit_pct - 0.05, 4),
                        'liquidity': round(liquidity, 0),
                        'buy_liquidity': round(b['ask_vol_usd'], 0),
                        'sell_liquidity': round(s['bid_vol_usd'], 0),
                        'timestamp': time.time()
                    }
        
        if best_opp and best_profit >= MIN_PROFIT_PERCENT:
            return best_opp
        return None
        
    except Exception:
        return None

# ================= CONTINUOUS SCANNER - ULTRA FAST =================
async def continuous_scanner():
    global latest_opportunities, all_symbols, scanning_active, scan_round
    
    if len(all_symbols) == 0:
        print("❌ No symbols to scan!")
        scanning_active = False
        return
    
    print(f"\n⚡ SCANNER ACTIVE - {len(all_symbols)} pairs")
    print(f"🔄 Continuous scanning with batch size {BATCH_SIZE}\n")
    
    scan_index = 0
    cycle_start = time.time()
    opportunities_found = 0
    
    while True:
        try:
            # Reset index if we've scanned all symbols
            if scan_index >= len(all_symbols):
                scan_round += 1
                cycle_time = time.time() - cycle_start
                print(f"\n[{time.strftime('%H:%M:%S')}] 🔄 Round {scan_round} done in {cycle_time:.1f}s")
                print(f"   📊 {len(latest_opportunities)} active opportunities | {opportunities_found} found this round")
                print(f"   💰 Top spread: {latest_opportunities[0]['spread']}% - {latest_opportunities[0]['symbol']}" if latest_opportunities else "   ⚠️ No opportunities found")
                cycle_start = time.time()
                scan_index = 0
                opportunities_found = 0
            
            # Get next batch
            batch = all_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            # Scan batch in parallel
            tasks = [scan_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            # Process results
            for result in results:
                if result:
                    opportunities_found += 1
                    # Remove old entry for same symbol
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != result['symbol']]
                    latest_opportunities.append(result)
                    # Sort and keep top 50
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    latest_opportunities = latest_opportunities[:50]
            
            # Print progress every 100 symbols
            if scan_index % 100 == 0:
                pct = (scan_index / len(all_symbols)) * 100 if len(all_symbols) > 0 else 0
                print(f"[{time.strftime('%H:%M:%S')}] 📊 {scan_index}/{len(all_symbols)} ({pct:.0f}%) | Found: {opportunities_found} | Active: {len(latest_opportunities)}")
            
            # Minimal delay
            await asyncio.sleep(0.005)
            
        except Exception as e:
            print(f"❌ Scanner error: {e}")
            await asyncio.sleep(1)

# ================= STARTUP =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(initialize_exchanges())

# ================= WEB UI =================
@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Ultra-Fast Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="300">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 12px; }
        .container { max-width: 650px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 1px solid #222; }
        .header h1 { font-size: 22px; color: #fff; }
        .badge { display: inline-block; background: #2ecc71; color: #0a0a0a; padding: 3px 8px; border-radius: 12px; font-size: 10px; font-weight: 600; margin: 4px 2px; }
        .stats { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 11px; color: #666; background: #111; padding: 10px 14px; border-radius: 10px; }
        .live-dot { display: inline-block; width: 7px; height: 7px; background: #2ecc71; border-radius: 50%; animation: pulse 1s infinite; margin-right: 5px; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }
        .opp-card { background: #111; border-radius: 10px; margin-bottom: 6px; border: 1px solid #1a1a1a; cursor: pointer; transition: all 0.15s; overflow: hidden; }
        .opp-card:hover { border-color: #f39c12; }
        .card-main { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; }
        .left-side { flex: 1; min-width: 0; }
        .route { font-size: 11px; margin-bottom: 4px; }
        .buy-tag { color: #2ecc71; font-weight: 600; }
        .sell-tag { color: #e74c3c; font-weight: 600; }
        .arrow { color: #666; margin: 0 4px; }
        .coin { font-size: 16px; font-weight: 700; color: #fff; }
        .meta { font-size: 10px; color: #555; margin-top: 3px; }
        .right-side { text-align: right; flex-shrink: 0; }
        .spread { font-size: 20px; font-weight: 700; color: #2ecc71; }
        .net { font-size: 10px; color: #666; }
        .detail { display: none; border-top: 1px solid #1a1a1a; padding: 14px; background: #0d0d0d; }
        .detail.show { display: block; }
        .detail-row { display: flex; justify-content: space-between; padding: 6px 0; font-size: 12px; border-bottom: 1px solid #141414; }
        .detail-label { color: #777; }
        .detail-val { color: #fff; font-weight: 500; }
        .warning { background: rgba(231,76,60,0.08); border-left: 2px solid #e74c3c; padding: 8px 10px; font-size: 11px; color: #e74c3c; margin-top: 10px; border-radius: 4px; }
        .no-data { text-align: center; color: #444; padding: 50px 20px; background: #111; border-radius: 10px; font-size: 14px; }
        .footer { text-align: center; font-size: 10px; color: #333; margin-top: 16px; padding-top: 10px; border-top: 1px solid #1a1a1a; }
        @media (max-width: 480px) {
            .card-main { padding: 10px; }
            .coin { font-size: 14px; }
            .spread { font-size: 17px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚡ Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 5 EXCHANGES</span>
                <span class="badge" style="background:#e74c3c;">ALL USDT PAIRS</span>
            </div>
        </div>
        <div class="stats">
            <span><span class="live-dot"></span>LIVE SCANNING</span>
            <span id="count">0 opportunities</span>
            <span id="round" style="color:#555;">Round 0</span>
        </div>
        <div id="container"></div>
        <div class="footer">🔍 Scanning all USDT spot pairs • 0.01%+ profit</div>
    </div>
    <script>
        let opps = [], expanded = null, roundNum = 0;
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function ago(ts) {
            const s = Math.floor(Date.now()/1000 - ts);
            return s < 60 ? `${s}s` : `${Math.floor(s/60)}m`;
        }
        
        function fmtEx(n) {
            const m = {'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin','COINEX':'CoinEx','BITGET':'Bitget'};
            return m[n] || n;
        }
        
        function toggle(i, e) {
            e.stopPropagation();
            const d = document.getElementById('d-'+i);
            if (expanded === i) { d.classList.remove('show'); expanded = null; }
            else {
                if (expanded !== null) document.getElementById('d-'+expanded)?.classList.remove('show');
                d.classList.add('show');
                expanded = i;
            }
        }
        
        function render() {
            document.getElementById('count').textContent = opps.length + ' opps';
            document.getElementById('round').textContent = 'Round ' + roundNum;
            const c = document.getElementById('container');
            if (!opps.length) {
                c.innerHTML = '<div class="no-data">⚡ Scanning all USDT pairs...<br><span style="font-size:12px;color:#555;">Checking order books in real-time</span></div>';
                return;
            }
            c.innerHTML = opps.map((o,i) => `
                <div class="opp-card" onclick="toggle(${i},event)">
                    <div class="card-main">
                        <div class="left-side">
                            <div class="route">
                                <span class="buy-tag">BUY</span> ${fmtEx(o.buy_exchange)}
                                <span class="arrow">→</span>
                                <span class="sell-tag">SELL</span> ${fmtEx(o.sell_exchange)}
                            </div>
                            <div class="coin">${o.symbol}/USDT</div>
                            <div class="meta">💰 $${o.liquidity.toLocaleString()} • ⏱ ${ago(o.timestamp)}</div>
                        </div>
                        <div class="right-side">
                            <div class="spread">${o.spread}%</div>
                            <div class="net">net: ${o.net_profit}%</div>
                        </div>
                    </div>
                    <div class="detail" id="d-${i}">
                        <div class="detail-row"><span class="detail-label">Buy Price</span><span class="detail-val">$${o.buy_price} @ ${fmtEx(o.buy_exchange)}</span></div>
                        <div class="detail-row"><span class="detail-label">Sell Price</span><span class="detail-val">$${o.sell_price} @ ${fmtEx(o.sell_exchange)}</span></div>
                        <div class="detail-row"><span class="detail-label">Buy Liquidity</span><span class="detail-val">$${o.buy_liquidity.toLocaleString()}</span></div>
                        <div class="detail-row"><span class="detail-label">Sell Liquidity</span><span class="detail-val">$${o.sell_liquidity.toLocaleString()}</span></div>
                        <div class="detail-row"><span class="detail-label">Gross Spread</span><span class="detail-val">${o.spread}%</span></div>
                        <div class="detail-row"><span class="detail-label">Net Profit (est.)</span><span class="detail-val">${o.net_profit}%</span></div>
                        <div class="warning">⚠️ Verify contract address before trading. Prices change rapidly.</div>
                    </div>
                </div>
            `).join('');
            expanded = null;
        }
        
        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'opps') {
                opps = data.data;
                render();
            } else if (data.type === 'round') {
                roundNum = data.round;
                document.getElementById('round').textContent = 'Round ' + roundNum;
            }
        };
        
        ws.onclose = () => setTimeout(() => location.reload(), 2000);
        
        // Fallback polling if WebSocket fails
        setInterval(async () => {
            if (ws.readyState !== WebSocket.OPEN) {
                try {
                    const res = await fetch('/api/opportunities');
                    const data = await res.json();
                    opps = data;
                    render();
                } catch(e) {}
            }
        }, 3000);
    </script>
</body>
</html>
    """)

# ================= REST API FALLBACK =================
@app.get("/api/opportunities")
async def get_opportunities():
    return latest_opportunities

# ================= WEBSOCKET =================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_data = None
    last_round = 0
    while True:
        try:
            current_data = json.dumps(latest_opportunities, default=str)
            if current_data != last_data:
                await websocket.send_json({"type": "opps", "data": latest_opportunities})
                last_data = current_data
            if scan_round != last_round:
                await websocket.send_json({"type": "round", "round": scan_round})
                last_round = scan_round
            await asyncio.sleep(0.5)
        except Exception:
            break

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 10000))
    print(f"\n{'='*60}")
    print(f"⚡ ULTRA-FAST ARBITRAGE SCANNER")
    print(f"{'='*60}")
    print(f"📊 Exchanges: {', '.join(EXCHANGE_IDS)}")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}%")
    print(f"💵 Min Liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"🔍 Scanning ALL USDT spot pairs")
    print(f"🌐 Web UI: http://0.0.0.0:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
