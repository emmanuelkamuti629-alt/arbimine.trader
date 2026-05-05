import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os
import json
from datetime import datetime

# ================= CONFIG =================
EXCHANGE_IDS = ["gateio", "kucoin", "mexc", "bitget", "coinex"]
MAX_COINS = 300
CACHE_FILE = "symbols_cache.json"

# ================= TRADING CONFIG =================
TRADING_FEES = {
    'gateio': 0.1,
    'kucoin': 0.1,
    'mexc': 0.1,
    'bitget': 0.1,
    'coinex': 0.2,
}

WITHDRAWAL_FEES = {
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06},
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05},
    'bitget': {'ERC20': 0.12, 'TRC20': 0.07, 'BEP20': 0.04},
    'coinex': {'ERC20': 0.13, 'TRC20': 0.08, 'BEP20': 0.05},
}

MIN_PROFIT_PERCENT = 0.1
MIN_LIQUIDITY_USD = 30
BATCH_SIZE = 10
MARKET_LOAD_TIMEOUT = 20  # 20 seconds timeout per exchange

app = FastAPI()
latest_opportunities = []
all_symbols = []
exchanges = {}
exchanges_loaded = 0
scanning_active = False
scan_task = None

# ================= CACHE FUNCTIONS =================
def load_cached_symbols():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_cached_symbols(cache):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except:
        pass

# ================= LOAD EXCHANGE WITH TIMEOUT =================
async def load_exchange(exchange_id, cached_data):
    global exchanges_loaded, all_symbols, scanning_active
    
    start = datetime.now()
    exchange = None

    try:
        exchange_class = getattr(ccxt, exchange_id)

        config = {
            "enableRateLimit": True,
            "timeout": 30000,
            "options": {"defaultType": "spot"}
        }

        # ADD API KEYS
        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY', '')
        api_secret = os.getenv(f'{exchange_id.upper()}_SECRET', '')
        
        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret
            if exchange_id == 'kucoin':
                config['password'] = os.getenv('KUCOIN_PASSWORD', '')

        exchange = exchange_class(config)
        print(f"⚡ Loading {exchange_id}...")

        # CHECK CACHE FIRST
        if exchange_id in cached_data:
            symbols = [s for s in cached_data[exchange_id] if s.endswith('/USDT')][:MAX_COINS]
            print(f"✓ {exchange_id}: {len(symbols)} cached USDT symbols")
            
            # Add to exchanges immediately
            exchanges[exchange_id] = exchange
            exchanges_loaded += 1
            
            # Update symbols
            new_symbols = set(all_symbols)
            new_symbols.update(symbols)
            all_symbols = list(new_symbols)
            
            print(f"📊 Total symbols now: {len(all_symbols)} | Exchanges: {exchanges_loaded}")
            
            # Start scanning if we have at least 2 exchanges and not already scanning
            if exchanges_loaded >= 2 and not scanning_active:
                scanning_active = True
                asyncio.create_task(continuous_scanner())
            
            return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

        # LOAD MARKETS WITH TIMEOUT
        print(f"📡 {exchange_id}: Loading markets (timeout: {MARKET_LOAD_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(exchange.load_markets(), timeout=MARKET_LOAD_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"⏰ {exchange_id} TIMEOUT - skipping (took >{MARKET_LOAD_TIMEOUT}s)")
            if exchange:
                try:
                    await exchange.close()
                except:
                    pass
            return None
        
        # GET ONLY USDT PAIRS, LIMITED TO MAX_COINS
        usdt_symbols = [s for s in exchange.symbols if s.endswith('/USDT')]
        symbols = usdt_symbols[:MAX_COINS]

        elapsed = (datetime.now() - start).total_seconds()
        print(f"✓ {exchange_id}: {len(symbols)} USDT symbols in {elapsed:.1f}s")
        
        # Add to exchanges immediately
        exchanges[exchange_id] = exchange
        exchanges_loaded += 1
        
        # Update symbols
        new_symbols = set(all_symbols)
        new_symbols.update(symbols)
        all_symbols = list(new_symbols)
        
        print(f"📊 Total symbols now: {len(all_symbols)} | Exchanges: {exchanges_loaded}")
        
        # Start scanning if we have at least 2 exchanges and not already scanning
        if exchanges_loaded >= 2 and not scanning_active:
            scanning_active = True
            asyncio.create_task(continuous_scanner())

        return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

    except Exception as e:
        print(f"❌ {exchange_id} failed: {type(e).__name__}: {e}")
        if exchange:
            try:
                await exchange.close()
            except:
                pass
        return None

# ================= SAFE LOAD WRAPPER =================
async def safe_load(exchange_id, cached_data):
    """Safe wrapper for load_exchange with exception handling"""
    try:
        return await load_exchange(exchange_id, cached_data)
    except Exception as e:
        print(f"❌ {exchange_id} failed hard: {e}")
        return None

# ================= INITIALIZE EXCHANGES =================
async def initialize_exchanges():
    global exchanges, all_symbols, exchanges_loaded, scanning_active
    
    print("\n" + "="*50)
    print("🚀 IMMEDIATE SCAN MODE")
    print(f"⏱️  Exchange timeout: {MARKET_LOAD_TIMEOUT}s")
    print("💡 Scanning will start as soon as 2+ exchanges are loaded")
    print("="*50)

    cached_data = load_cached_symbols()
    exchanges_loaded = 0
    scanning_active = False

    # LOAD ALL EXCHANGES IN PARALLEL
    tasks = [safe_load(exchange_id, cached_data) for exchange_id in EXCHANGE_IDS]
    results = await asyncio.gather(*tasks)

    # Save cache for future runs
    new_cache = {}
    for result in results:
        if result:
            new_cache[result["id"]] = result["symbols"]

    if new_cache:
        save_cached_symbols(new_cache)

    print("="*50)
    print(f"✅ {exchanges_loaded}/{len(EXCHANGE_IDS)} exchanges loaded")
    print(f"📊 {len(all_symbols)} USDT pairs ready to scan")
    print("="*50 + "\n")
    
    # If we still don't have 2 exchanges after all loading, can't scan
    if exchanges_loaded < 2:
        print("❌ Not enough exchanges loaded. Need at least 2.")
        return False
    
    return True

# ================= HEALTH CHECK ENDPOINT =================
@app.get("/health")
@app.head("/health")
async def health():
    """Health check endpoint for uptime monitoring"""
    return {
        "status": "ok",
        "exchanges_loaded": exchanges_loaded,
        "total_exchanges": len(EXCHANGE_IDS),
        "active_opportunities": len(latest_opportunities),
        "symbols_loaded": len(all_symbols),
        "scanning_active": scanning_active,
        "timestamp": datetime.now().isoformat()
    }

# ================= SCAN SYMBOL =================
async def scan_symbol(symbol):
    try:
        tasks = []
        ex_names = []
        
        for name, ex in exchanges.items():
            tasks.append(ex.fetch_order_book(symbol, limit=1))
            ex_names.append(name)
        
        if len(tasks) < 2:
            return None
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        data = {}
        for i, (name, result) in enumerate(zip(ex_names, results)):
            if isinstance(result, Exception):
                continue
            if result and result.get('asks') and result.get('bids') and len(result['asks']) > 0:
                ask_price = result['asks'][0][0]
                bid_price = result['bids'][0][0]
                data[name] = {
                    'ask': ask_price,
                    'bid': bid_price,
                    'ask_vol': result['asks'][0][1] * ask_price,
                    'bid_vol': result['bids'][0][1] * bid_price,
                }
        
        if len(data) < 2:
            return None
        
        best_profit = 0
        best_opp = None
        
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex:
                    continue
                
                buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1) / 100)
                sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1) / 100)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                if profit_pct > best_profit and liquidity > MIN_LIQUIDITY_USD:
                    best_profit = profit_pct
                    best_opp = (buy_ex, sell_ex, b, s, profit_pct, liquidity)
        
        if best_opp and best_profit >= MIN_PROFIT_PERCENT:
            buy_ex, sell_ex, b, s, profit_pct, liquidity = best_opp
            withdrawal_fee = 0.10
            net_profit_pct = profit_pct - 0.1
            
            return {
                'symbol': symbol.replace('/USDT', ''),
                'buy_exchange': buy_ex.upper(),
                'sell_exchange': sell_ex.upper(),
                'buy_price': b['ask'],
                'sell_price': s['bid'],
                'spread': round(profit_pct, 1),
                'net_profit': round(net_profit_pct, 1),
                'net_profit_usd': round(profit_pct - 0.1, 2),
                'withdrawal_fee': 0.10,
                'recommended_network': 'BEP20',
                'buy_networks': 'BEP20 ($0.05) | TRC20 ($0.10)',
                'sell_networks': 'BEP20 (Free) | TRC20 (Free)',
                'liquidity': round(liquidity, 0),
                'buy_liquidity': round(b['ask_vol'], 0),
                'sell_liquidity': round(s['bid_vol'], 0),
                'timestamp': time.time()
            }
        return None
    except:
        return None

# ================= CONTINUOUS SCANNER =================
async def continuous_scanner():
    global latest_opportunities, all_symbols, scanning_active
    
    if len(all_symbols) == 0:
        print("❌ No symbols to scan yet")
        scanning_active = False
        return
    
    print(f"\n🔄 STARTING IMMEDIATE SCAN with {exchanges_loaded} exchanges")
    print(f"💰 Target: {MIN_PROFIT_PERCENT}% profit | ${MIN_LIQUIDITY_USD} liquidity")
    print(f"📊 Scanning {len(all_symbols)} USDT pairs (will update as more exchanges load)\n")
    
    scan_index = 0
    last_log = time.time()
    scanned = 0
    
    while True:
        try:
            # If more exchanges loaded, update symbols
            current_symbols = all_symbols.copy()
            
            if scan_index >= len(current_symbols):
                scan_index = 0
                print(f"\n[{time.strftime('%H:%M:%S')}] 🔄 Full cycle - {len(latest_opportunities)} opportunities active | Exchanges: {exchanges_loaded}")
                scanned = 0
            
            batch = current_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            tasks = [scan_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            for result in results:
                if result:
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != result['symbol']]
                    latest_opportunities.append(result)
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    if len(latest_opportunities) > 30:
                        latest_opportunities = latest_opportunities[:30]
                    print(f"  🎯 {result['symbol']} - {result['spread']}% ({result['buy_exchange']} → {result['sell_exchange']})")
            
            scanned += len(batch)
            
            if time.time() - last_log > 15:
                last_log = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] 📊 {scanned}/{len(current_symbols)} | Active: {len(latest_opportunities)} | Exchanges: {exchanges_loaded}")
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"❌ Scan error: {e}")
            await asyncio.sleep(1)

# ================= WEB UI =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(initialize_exchanges())

@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Immediate Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 16px; }
        .container { max-width: 600px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #222; }
        .header h1 { font-size: 24px; color: #fff; }
        .badge { display: inline-block; background: #2ecc71; color: #0a0a0a; padding: 4px 10px; border-radius: 16px; font-size: 10px; font-weight: 600; margin: 6px 0; }
        .immediate-badge { background: #e74c3c; }
        .stats { display: flex; justify-content: space-between; margin-bottom: 16px; font-size: 12px; color: #666; }
        .live-indicator { display: inline-block; width: 8px; height: 8px; background: #2ecc71; border-radius: 50%; animation: blink 1s infinite; margin-right: 6px; }
        @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .opportunity-card { background: #121212; border-radius: 12px; margin-bottom: 8px; border: 1px solid #1e1e1e; cursor: pointer; transition: all 0.2s; }
        .opportunity-card:hover { border-color: #f39c12; background: #161616; transform: translateX(4px); }
        .card-main { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; }
        .left-section { flex: 1; }
        .exchange-pair { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
        .buy-text { color: #2ecc71; }
        .sell-text { color: #e74c3c; }
        .exchange-name { color: #fff; margin: 0 3px; }
        .token-symbol { font-size: 16px; font-weight: 700; color: #fff; margin-top: 4px; }
        .details-row { display: flex; gap: 16px; font-size: 11px; color: #666; margin-top: 6px; }
        .profit-section { text-align: right; }
        .spread-percent { font-size: 22px; font-weight: 700; color: #2ecc71; }
        .net-profit { font-size: 11px; color: #666; margin-top: 2px; }
        .detail-expanded { border-top: 1px solid #1e1e1e; padding: 16px; background: #0d0d0d; display: none; }
        .detail-expanded.show { display: block; }
        .trade-section { margin-bottom: 20px; }
        .trade-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 12px; }
        .info-row { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 13px; }
        .info-label { color: #888; }
        .info-value { color: #fff; font-weight: 500; }
        .button-group { display: flex; gap: 12px; margin: 16px 0; }
        .action-btn { flex: 1; background: #1a1a1a; border: 1px solid #2a2a2a; color: #e0e0e0; padding: 10px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none; }
        .action-btn:hover { background: #f39c12; border-color: #f39c12; color: #0a0a0a; }
        .warning-box { background: rgba(231,76,60,0.1); border-left: 3px solid #e74c3c; padding: 12px; font-size: 12px; color: #e74c3c; margin: 16px 0; border-radius: 6px; }
        .time-warning { color: #f39c12; font-size: 11px; text-align: center; padding: 10px; background: rgba(243,156,18,0.1); border-radius: 8px; }
        .no-data { text-align: center; color: #555; padding: 40px; background: #121212; border-radius: 12px; }
        .footer { text-align: center; font-size: 10px; color: #444; margin-top: 20px; padding-top: 12px; border-top: 1px solid #1e1e1e; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Immediate Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 5 EXCHANGES</span>
                <span class="badge immediate-badge">⚡ SCANS IMMEDIATELY</span>
            </div>
        </div>
        <div class="stats">
            <span><span class="live-indicator"></span> SCANNING</span>
            <span id="count">0 opportunities</span>
        </div>
        <div id="opportunities-container"></div>
        <div class="footer">💰 0.1%+ profit | $30+ liquidity | Scans as soon as 2+ exchanges load</div>
    </div>
    <script>
        let allOpportunities = [], expandedCard = null;
        const ws = new WebSocket(`ws://${location.host}/ws`);
        function timeAgo(ts) { const s = Math.floor(Date.now()/1000 - ts); return s < 60 ? `${s}s ago` : `${Math.floor(s/60)}m ago`; }
        function formatExchange(n) { const names={'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin','COINEX':'CoinEx','BITGET':'Bitget'}; return names[n]||n; }
        function toggleDetail(id,e) { e.stopPropagation(); const d = document.getElementById(`detail-${id}`); if(expandedCard===id){ d.classList.remove('show'); expandedCard=null; } else { if(expandedCard!==null) document.getElementById(`detail-${expandedCard}`).classList.remove('show'); d.classList.add('show'); expandedCard=id; } }
        function updateDisplay() {
            const c = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            if(allOpportunities.length===0){ c.innerHTML='<div class="no-data">🔍 Loading exchanges...<br><span style="font-size:12px;">Scanning will start as soon as 2+ exchanges are ready</span><br><span style="font-size:11px;">Gate.io | KuCoin | MEXC | Bitget | CoinEx</span></div>'; return; }
            c.innerHTML = allOpportunities.map((opp,idx)=>`<div class="opportunity-card" onclick="toggleDetail(${idx},event)"><div class="card-main"><div class="left-section"><div class="exchange-pair"><span class="buy-text">BUY</span> <span class="exchange-name">${formatExchange(opp.buy_exchange)}</span> <span class="sell-text">→ SELL</span> <span class="exchange-name">${formatExchange(opp.sell_exchange)}</span></div><div class="token-symbol">${opp.symbol}/USDT</div><div class="details-row"><span>💰 $${opp.liquidity.toLocaleString()}</span><span>⏱️ ${timeAgo(opp.timestamp)}</span></div></div><div class="profit-section"><div class="spread-percent">${opp.spread}%</div><div class="net-profit">net: ${opp.net_profit}%</div></div></div><div class="detail-expanded" id="detail-${idx}"><div class="trade-section"><div class="trade-title">1️⃣ Buy at ${formatExchange(opp.buy_exchange)}</div><div class="info-row"><span class="info-label">Lowest Ask:</span><span class="info-value">$${opp.buy_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.buy_liquidity.toLocaleString()}</span></div><div class="button-group"><a href="#" class="action-btn">📊 Go to ${formatExchange(opp.buy_exchange)}</a></div></div><div class="trade-section"><div class="trade-title">2️⃣ Sell at ${formatExchange(opp.sell_exchange)}</div><div class="info-row"><span class="info-label">Highest Bid:</span><span class="info-value">$${opp.sell_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.sell_liquidity.toLocaleString()}</span></div><div class="button-group"><a href="#" class="action-btn">📊 Go to ${formatExchange(opp.sell_exchange)}</a></div></div><div class="info-row"><span class="info-label">Gross Spread:</span><span class="info-value">${opp.spread}%</span></div><div class="info-row"><span class="info-label">Net Profit:</span><span class="info-value">${opp.net_profit}% ($${opp.net_profit_usd} on $100)</span></div><div class="warning-box">⚠️ Double check coin contract before trading</div><div class="time-warning">🟢 Act fast - opportunities last 10-15 min</div></div></div>`).join('');
            expandedCard = null;
        }
        ws.onmessage = (e) => { allOpportunities = JSON.parse(e.data); updateDisplay(); };
        ws.onclose = () => setTimeout(() => location.reload(), 3000);
    </script>
</body>
</html>
    """)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_sent = []
    while True:
        if latest_opportunities != last_sent:
            await websocket.send_json(latest_opportunities)
            last_sent = latest_opportunities.copy()
        await asyncio.sleep(2)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 10000))
    print(f"\n{'='*50}")
    print(f"🚀 IMMEDIATE SCAN MODE")
    print(f"{'='*50}")
    print(f"📊 {len(EXCHANGE_IDS)} exchanges | {MAX_COINS} coins each")
    print(f"⏱️  Exchange timeout: {MARKET_LOAD_TIMEOUT}s")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}%")
    print(f"⚡ Scans start as soon as 2+ exchanges are loaded")
    print(f"❤️ Health check: http://localhost:{port}/health")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*50}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
