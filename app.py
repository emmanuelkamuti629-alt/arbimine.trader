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
EXCHANGE_IDS = ["mexc", "gateio", "kucoin", "coinex", "bitget"]
# NO MAX COINS - SCAN ALL USDT PAIRS

# Cache file for symbols (to speed up restarts)
CACHE_FILE = "symbols_cache.json"

# ================= TRADING CONFIG =================
TRADING_FEES = {
    'mexc': 0.1,
    'gateio': 0.1,
    'kucoin': 0.1,
    'coinex': 0.2,
    'bitget': 0.1,
}

WITHDRAWAL_FEES = {
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05},
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06},
    'coinex': {'ERC20': 0.13, 'TRC20': 0.08, 'BEP20': 0.05},
    'bitget': {'ERC20': 0.12, 'TRC20': 0.07, 'BEP20': 0.04},
}

MIN_PROFIT_PERCENT = 0.1
MIN_LIQUIDITY_USD = 30
BATCH_SIZE = 20

app = FastAPI()
latest_opportunities = []
all_symbols = []
exchanges = {}
exchange_symbols = {}

# ================= CACHE FUNCTIONS =================
def load_cached_symbols():
    """Load cached symbols from file for faster startup"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
                print(f"📦 Loaded cache with {len(cache)} exchanges")
                return cache
    except Exception as e:
        print(f"⚠️ Cache load error: {e}")
    return {}

def save_cached_symbols(cache):
    """Save symbols to cache for future fast startups"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
            print(f"💾 Saved cache for {len(cache)} exchanges")
    except Exception as e:
        print(f"⚠️ Cache save error: {e}")

# ================= SYMBOL FILTERING - ALL USDT =================
def get_all_usdt_symbols(exchange):
    """Get ALL USDT spot pairs - NO LIMITS"""
    usdt_symbols = []
    for symbol in exchange.symbols:
        # ONLY take /USDT pairs
        if symbol.endswith('/USDT'):
            usdt_symbols.append(symbol)
    
    print(f"    Found {len(usdt_symbols)} total USDT pairs")
    return usdt_symbols  # NO LIMIT - RETURN ALL

# ================= LOAD SINGLE EXCHANGE =================
async def load_exchange(exchange_id, cached_data):
    start = datetime.now()
    exchange = None

    try:
        exchange_class = getattr(ccxt, exchange_id)

        # BASE CONFIG
        config = {
            "enableRateLimit": False,   # Max speed
            "timeout": 30000,           # Internal CCXT request timeout only
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": False,
                "recvWindow": 10000,
            }
        }

        # EXCHANGE-SPECIFIC SPEED TUNING
        if exchange_id == "bitget":
            config["options"].update({"defaultType": "spot"})
        elif exchange_id == "coinex":
            config["options"].update({"createMarketBuyOrderRequiresPrice": False})
        elif exchange_id == "gateio":
            config["options"].update({"defaultType": "spot"})
        elif exchange_id == "kucoin":
            config["options"].update({"fetchMarkets": ["spot"]})
        elif exchange_id == "mexc":
            config["options"].update({"defaultType": "spot"})

        # ADD API KEYS IF AVAILABLE
        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY', '')
        api_secret = os.getenv(f'{exchange_id.upper()}_SECRET', '')
        
        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret
            if exchange_id == 'kucoin':
                config['password'] = os.getenv('KUCOIN_PASSWORD', '')

        # CREATE EXCHANGE
        exchange = exchange_class(config)
        print(f"⚡ Initializing {exchange_id}...")

        # CHECK CACHE FIRST
        if exchange_id in cached_data:
            symbols = cached_data[exchange_id]
            # Filter cached symbols to ensure only USDT
            symbols = [s for s in symbols if s.endswith('/USDT')]
            print(f"⚡ {exchange_id}: Loaded {len(symbols)} cached USDT symbols instantly")
            return {
                "id": exchange_id,
                "exchange": exchange,
                "symbols": symbols,
                "cached": True
            }

        # FULL MARKET LOAD
        print(f"📡 {exchange_id}: Loading ALL USDT spot markets...")
        await exchange.load_markets()

        # GET ALL USDT SYMBOLS (NO LIMIT)
        symbols = get_all_usdt_symbols(exchange)

        # Remove duplicates and keep ALL
        symbols = list(dict.fromkeys(symbols))

        elapsed = (datetime.now() - start).total_seconds()
        print(f"✓ {exchange_id}: Loaded {len(symbols)} USDT symbols in {elapsed:.2f}s")

        return {
            "id": exchange_id,
            "exchange": exchange,
            "symbols": symbols,
            "cached": False
        }

    except Exception as e:
        print(f"❌ {exchange_id} failed: {type(e).__name__}: {e}")
        if exchange:
            try:
                await exchange.close()
            except:
                pass
        return None

# ================= PARALLEL INITIALIZER =================
async def initialize_exchanges():
    """FULL PRODUCTION INITIALIZATION - ALL USDT SPOT"""
    global exchanges, exchange_symbols, all_symbols
    
    print("\n" + "="*60)
    print("🚀 ALL USDT SPOT MARKET SCANNER")
    print("="*60)

    overall_start = datetime.now()
    cached_data = load_cached_symbols()

    # LOAD ALL EXCHANGES IN PARALLEL
    tasks = [load_exchange(exchange_id, cached_data) for exchange_id in EXCHANGE_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    new_exchanges = {}
    symbols_map = {}
    new_cache = {}

    # PROCESS RESULTS
    for result in results:
        if not result or isinstance(result, Exception):
            continue

        exchange_id = result["id"]
        new_exchanges[exchange_id] = result["exchange"]
        symbols_map[exchange_id] = result["symbols"]
        new_cache[exchange_id] = result["symbols"]

    # SAVE CACHE
    if new_cache:
        save_cached_symbols(new_cache)

    exchanges = new_exchanges
    exchange_symbols = symbols_map
    
    # Combine ALL USDT symbols for scanning
    all_symbols_set = set()
    for syms in symbols_map.values():
        all_symbols_set.update(syms)
    all_symbols = list(all_symbols_set)

    elapsed = (datetime.now() - overall_start).total_seconds()

    # SUMMARY
    print("="*60)
    print(f"✅ READY: {len(exchanges)}/{len(EXCHANGE_IDS)} exchanges loaded")
    print(f"🪙 Total USDT symbols loaded (per exchange):")
    for name, syms in symbols_map.items():
        print(f"     {name}: {len(syms)} USDT pairs")
    print(f"📊 Unique USDT pairs to scan: {len(all_symbols)}")
    print(f"⏱ Total startup time: {elapsed:.2f}s")
    print("="*60 + "\n")
    
    return len(exchanges) >= 2

# ================= HELPER FUNCTIONS =================
def get_lowest_fee_network(exchange):
    networks = WITHDRAWAL_FEES.get(exchange.lower(), {'ERC20': 0.15})
    best_network = min(networks.items(), key=lambda x: x[1])
    return best_network[0], best_network[1]

def get_all_networks_with_fees(exchange):
    networks = WITHDRAWAL_FEES.get(exchange.lower(), {'ERC20': 0.15})
    return " | ".join([f"{k} (${v})" for k, v in networks.items()])

def calculate_best_net_profit(profit_pct, amount_usd, buy_exchange, sell_exchange):
    gross_profit_usd = amount_usd * (profit_pct / 100)
    network, withdrawal_fee = get_lowest_fee_network(buy_exchange)
    net_profit_usd = gross_profit_usd - withdrawal_fee
    net_profit_pct = (net_profit_usd / amount_usd) * 100 if amount_usd > 0 else 0
    return net_profit_usd, net_profit_pct, withdrawal_fee, network

def get_exchange_link(exchange, symbol):
    exchange_lower = exchange.lower()
    links = {
        'mexc': f"https://www.mexc.com/exchange/{symbol}_USDT",
        'gateio': f"https://www.gate.io/trade/{symbol}_USDT",
        'kucoin': f"https://www.kucoin.com/trade/{symbol}-USDT",
        'coinex': f"https://www.coinex.com/trading/{symbol}USDT",
        'bitget': f"https://www.bitget.com/spot/{symbol}USDT",
    }
    return links.get(exchange_lower, "#")

# ================= SCAN SINGLE SYMBOL =================
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
                data[name] = {
                    'ask': result['asks'][0][0],
                    'bid': result['bids'][0][0],
                    'ask_vol': result['asks'][0][1] * result['asks'][0][0],
                    'bid_vol': result['bids'][0][1] * result['bids'][0][0],
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
            net_profit_usd, net_profit_pct, withdrawal_fee, network = calculate_best_net_profit(
                profit_pct, 100, buy_ex, sell_ex
            )
            
            return {
                'symbol': symbol.replace('/USDT', ''),
                'buy_exchange': buy_ex.upper(),
                'sell_exchange': sell_ex.upper(),
                'buy_price': b['ask'],
                'sell_price': s['bid'],
                'spread': round(profit_pct, 1),
                'net_profit': round(net_profit_pct, 1),
                'net_profit_usd': round(net_profit_usd, 2),
                'withdrawal_fee': round(withdrawal_fee, 2),
                'recommended_network': network,
                'buy_networks': get_all_networks_with_fees(buy_ex),
                'sell_networks': get_all_networks_with_fees(sell_ex),
                'liquidity': round(liquidity, 0),
                'buy_liquidity': round(b['ask_vol'], 0),
                'sell_liquidity': round(s['bid_vol'], 0),
                'timestamp': time.time()
            }
        return None
    except Exception as e:
        return None

# ================= CONTINUOUS SCANNER =================
async def continuous_scanner():
    global latest_opportunities, all_symbols
    
    success = await initialize_exchanges()
    if not success:
        print("❌ Failed to initialize exchanges")
        return
    
    if not all_symbols:
        print("❌ No USDT symbols loaded")
        return
    
    print(f"🔄 STARTING CONTINUOUS USDT SCAN")
    print(f"💰 Target: {MIN_PROFIT_PERCENT}% profit | ${MIN_LIQUIDITY_USD} liquidity")
    print(f"📊 Scanning {len(all_symbols)} USDT pairs continuously\n")
    
    scan_index = 0
    last_log = time.time()
    scanned = 0
    
    while True:
        try:
            batch = all_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            if scan_index >= len(all_symbols):
                scan_index = 0
                print(f"\n[{time.strftime('%H:%M:%S')}] 🔄 FULL CYCLE - {len(latest_opportunities)} USDT opportunities active\n")
                scanned = 0
            
            tasks = [scan_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            for result in results:
                if result:
                    symbol_name = result['symbol']
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != symbol_name]
                    latest_opportunities.append(result)
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    if len(latest_opportunities) > 50:
                        latest_opportunities = latest_opportunities[:50]
                    
                    print(f"  🎯 {result['symbol']}/USDT - {result['spread']}% ({result['buy_exchange']} → {result['sell_exchange']})")
            
            scanned += len(batch)
            
            if time.time() - last_log > 10:
                last_log = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] 📊 USDT: {scanned}/{len(all_symbols)} | Active: {len(latest_opportunities)}")
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"❌ Scan error: {e}")
            await asyncio.sleep(1)

# ================= WEB UI =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(continuous_scanner())

@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Full USDT Spot Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 16px; }
        .container { max-width: 600px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #222; }
        .header h1 { font-size: 24px; color: #fff; }
        .badge { display: inline-block; background: #2ecc71; color: #0a0a0a; padding: 4px 10px; border-radius: 16px; font-size: 10px; font-weight: 600; margin: 6px 0; }
        .usdt-badge { background: #f39c12; }
        .all-badge { background: #e74c3c; }
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
        .network-list { font-size: 11px; color: #888; }
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
            <h1>🚀 Full USDT Spot Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 5 EXCHANGES</span>
                <span class="badge usdt-badge">💵 ALL USDT SPOT</span>
                <span class="badge all-badge">🪙 NO LIMITS</span>
            </div>
        </div>
        <div class="stats">
            <span><span class="live-indicator"></span> SCANNING ALL USDT</span>
            <span id="count">0 opportunities</span>
        </div>
        <div id="opportunities-container"></div>
        <div class="footer">💰 0.1%+ profit | $30+ liquidity | Scanning ALL USDT pairs from all exchanges</div>
    </div>
    <script>
        let allOpportunities = [], expandedCard = null;
        const ws = new WebSocket(`ws://${location.host}/ws`);
        function timeAgo(ts) { const s = Math.floor(Date.now()/1000 - ts); return s < 60 ? `${s}s ago` : `${Math.floor(s/60)}m ago`; }
        function formatExchange(n) { const names={'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin','COINEX':'CoinEx','BITGET':'Bitget'}; return names[n]||n; }
        function toggleDetail(id,e) { e.stopPropagation(); const d = document.getElementById(`detail-${id}`); if(expandedCard===id){ d.classList.remove('show'); expandedCard=null; } else { if(expandedCard!==null) document.getElementById(`detail-${expandedCard}`).classList.remove('show'); d.classList.add('show'); expandedCard=id; } }
        function getExchangeLink(ex,sym) { const l=ex.toLowerCase(); if(l==='mexc') return `https://www.mexc.com/exchange/${sym}_USDT`; if(l==='gateio') return `https://www.gate.io/trade/${sym}_USDT`; if(l==='kucoin') return `https://www.kucoin.com/trade/${sym}-USDT`; return '#'; }
        function updateDisplay() {
            const c = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            if(allOpportunities.length===0){ c.innerHTML='<div class="no-data">🔍 Loading ALL USDT Spot Markets...<br><span style="font-size:12px;">MEXC | Gate.io | KuCoin | CoinEx | Bitget</span><br><span style="font-size:11px;">This may take 1-2 minutes for first load</span></div>'; return; }
            c.innerHTML = allOpportunities.map((opp,idx)=>`<div class="opportunity-card" onclick="toggleDetail(${idx},event)"><div class="card-main"><div class="left-section"><div class="exchange-pair"><span class="buy-text">BUY</span> <span class="exchange-name">${formatExchange(opp.buy_exchange)}</span> <span class="sell-text">→ SELL</span> <span class="exchange-name">${formatExchange(opp.sell_exchange)}</span></div><div class="token-symbol">${opp.symbol}/USDT</div><div class="details-row"><span>💰 $${opp.liquidity.toLocaleString()}</span><span>⏱️ ${timeAgo(opp.timestamp)}</span></div></div><div class="profit-section"><div class="spread-percent">${opp.spread}%</div><div class="net-profit">net: ${opp.net_profit}%</div></div></div><div class="detail-expanded" id="detail-${idx}"><div class="trade-section"><div class="trade-title">1️⃣ Buy at ${formatExchange(opp.buy_exchange)}</div><div class="info-row"><span class="info-label">Lowest Ask:</span><span class="info-value">$${opp.buy_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.buy_liquidity.toLocaleString()}</span></div><div class="network-list"><strong>Withdrawal:</strong> ${opp.recommended_network} ($${opp.withdrawal_fee})</div><div class="button-group"><a href="${getExchangeLink(opp.buy_exchange, opp.symbol)}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 Go to ${formatExchange(opp.buy_exchange)}</a></div></div><div class="trade-section"><div class="trade-title">2️⃣ Sell at ${formatExchange(opp.sell_exchange)}</div><div class="info-row"><span class="info-label">Highest Bid:</span><span class="info-value">$${opp.sell_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.sell_liquidity.toLocaleString()}</span></div><div class="network-list"><strong>Deposit:</strong> ${opp.recommended_network} (Free)</div><div class="button-group"><a href="${getExchangeLink(opp.sell_exchange, opp.symbol)}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 Go to ${formatExchange(opp.sell_exchange)}</a></div></div><div class="info-row"><span class="info-label">Gross Spread:</span><span class="info-value">${opp.spread}%</span></div><div class="info-row"><span class="info-label">Net Profit:</span><span class="info-value">${opp.net_profit}% ($${opp.net_profit_usd} on $100)</span></div><div class="warning-box">⚠️ Double check coin contract before trading</div><div class="time-warning">🟢 Act fast - opportunities last 10-15 min</div></div></div>`).join('');
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
    print(f"\n{'='*60}")
    print(f"🚀 FULL USDT SPOT ARBITRAGE SCANNER")
    print(f"{'='*60}")
    print(f"📊 Exchanges: {', '.join(EXCHANGE_IDS)}")
    print(f"💵 Market: ALL USDT SPOT PAIRS (NO LIMITS)")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}% | Min Liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"💾 Cache enabled for faster restarts")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
