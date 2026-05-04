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
    'gateio': {
        'apiKey': os.getenv('GATEIO_API_KEY', ''),
        'secret': os.getenv('GATEIO_SECRET', ''),
        'options': {'defaultType': 'spot'}
    },
    'kucoin': {
        'apiKey': os.getenv('KUCOIN_API_KEY', ''),
        'secret': os.getenv('KUCOIN_SECRET', ''),
        'password': os.getenv('KUCOIN_PASSWORD', ''),
        'options': {'defaultType': 'spot'}
    },
}

TRADING_FEES = {
    'mexc': 0.1,
    'gateio': 0.1,
    'kucoin': 0.1,
}

WITHDRAWAL_FEES = {
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05},
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06},
}

MIN_PROFIT_PERCENT = 0.1
MIN_LIQUIDITY_USD = 30
BATCH_SIZE = 10
MAX_COINS_PER_EXCHANGE = 1000

# Initialize exchanges
exchanges = {}
for name, keys in API_KEYS.items():
    if keys['apiKey'] and keys['secret']:
        try:
            exchanges[name] = getattr(ccxt, name)(keys)
            print(f"✓ Initialized {name}")
        except Exception as e:
            print(f"✗ Failed to initialize {name}: {e}")
    else:
        print(f"✗ {name}: No API keys found")

app = FastAPI()
latest_opportunities = []
all_symbols = []
ready_exchanges = []
kucoin_loading = True
gateio_loading = True

# ============ HELPER FUNCTIONS ============
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
    if 'mexc' in exchange_lower:
        return f"https://www.mexc.com/exchange/{symbol}_USDT"
    elif 'gate' in exchange_lower:
        return f"https://www.gate.io/trade/{symbol}_USDT"
    elif 'kucoin' in exchange_lower:
        return f"https://www.kucoin.com/trade/{symbol}-USDT"
    return "#"

# ============ LOAD MEXC FIRST ============
async def load_mexc():
    global all_symbols, ready_exchanges
    
    print("\n" + "="*60)
    print("📊 STEP 1: LOADING MEXC (FIRST)")
    print("="*60)
    
    if 'mexc' not in exchanges:
        print("❌ MEXC not available")
        return False
    
    try:
        print("  Loading MEXC markets...")
        await exchanges['mexc'].load_markets()
        all_usdt = [s for s in exchanges['mexc'].symbols if s.endswith('/USDT')]
        symbols = all_usdt[:MAX_COINS_PER_EXCHANGE]
        all_symbols = symbols.copy()  # Start with MEXC coins only
        ready_exchanges.append('mexc')
        print(f"  ✓ MEXC: {len(symbols)} USDT pairs loaded")
        print(f"\n✅ READY TO SCAN: {len(all_symbols)} pairs from MEXC only")
        print("⏳ Gate.io loading next...")
        print("="*60 + "\n")
        return True
    except Exception as e:
        print(f"  ✗ MEXC failed: {e}")
        return False

# ============ LOAD GATE.IO SECOND ============
async def load_gateio():
    global all_symbols, ready_exchanges, gateio_loading
    
    print("\n" + "="*60)
    print("📊 STEP 2: LOADING GATE.IO (SECOND)")
    print("="*60)
    
    if 'gateio' not in exchanges:
        print("❌ Gate.io not available")
        gateio_loading = False
        return
    
    try:
        print("  Loading Gate.io markets...")
        await exchanges['gateio'].load_markets()
        all_usdt = [s for s in exchanges['gateio'].symbols if s.endswith('/USDT')]
        symbols = all_usdt[:MAX_COINS_PER_EXCHANGE]
        
        # Add Gate.io symbols to existing set
        current_set = set(all_symbols)
        current_set.update(symbols)
        all_symbols = list(current_set)
        ready_exchanges.append('gateio')
        
        print(f"  ✓ Gate.io: {len(symbols)} USDT pairs loaded")
        print(f"\n✅ NOW SCANNING: {len(all_symbols)} pairs from MEXC + Gate.io")
        print("⏳ KuCoin loading next...")
        print("="*60 + "\n")
    except Exception as e:
        print(f"  ✗ Gate.io failed: {e}")
    
    gateio_loading = False

# ============ LOAD KUCOIN THIRD ============
async def load_kucoin():
    global all_symbols, ready_exchanges, kucoin_loading
    
    print("\n" + "="*60)
    print("📊 STEP 3: LOADING KUCOIN (THIRD)")
    print("="*60)
    
    if 'kucoin' not in exchanges:
        print("❌ KuCoin not available")
        kucoin_loading = False
        return
    
    try:
        print("  Loading KuCoin markets...")
        await exchanges['kucoin'].load_markets()
        all_usdt = [s for s in exchanges['kucoin'].symbols if s.endswith('/USDT')]
        symbols = all_usdt[:MAX_COINS_PER_EXCHANGE]
        
        # Add KuCoin symbols to existing set
        current_set = set(all_symbols)
        current_set.update(symbols)
        all_symbols = list(current_set)
        ready_exchanges.append('kucoin')
        
        print(f"  ✓ KuCoin: {len(symbols)} USDT pairs loaded")
        print(f"\n✅ ALL EXCHANGES READY: {len(all_symbols)} total pairs from {', '.join(ready_exchanges)}")
        print("="*60 + "\n")
    except Exception as e:
        print(f"  ✗ KuCoin failed: {e}")
    
    kucoin_loading = False

# ============ SCAN SINGLE SYMBOL ============
async def scan_symbol(symbol):
    try:
        tasks = []
        ex_names = []
        
        for name in ready_exchanges:
            if name in exchanges:
                tasks.append(exchanges[name].fetch_order_book(symbol, limit=1))
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

# ============ CONTINUOUS SCANNER ============
async def continuous_scanner():
    global latest_opportunities, all_symbols, kucoin_loading, gateio_loading
    
    if len(exchanges) < 2:
        print("\n❌ Need at least 2 exchanges to start")
        return
    
    # STEP 1: Load MEXC first
    success = await load_mexc()
    if not success:
        print("❌ Failed to load MEXC")
        return
    
    # Start scanning immediately with MEXC only
    print("🔄 STARTING SCAN WITH MEXC ONLY")
    print(f"💰 Target: {MIN_PROFIT_PERCENT}% profit | ${MIN_LIQUIDITY_USD} liquidity")
    print("="*60 + "\n")
    
    # Start Gate.io loading in background
    asyncio.create_task(load_gateio())
    
    # Start KuCoin loading in background (will wait for Gate.io to finish)
    asyncio.create_task(load_kucoin())
    
    scan_index = 0
    last_log_time = time.time()
    total_scanned = 0
    
    while True:
        try:
            if len(all_symbols) == 0:
                await asyncio.sleep(1)
                continue
            
            batch = all_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            if scan_index >= len(all_symbols):
                scan_index = 0
                exchanges_str = ', '.join(ready_exchanges)
                print(f"\n[{time.strftime('%H:%M:%S')}] 🔄 COMPLETED FULL CYCLE - {total_scanned} scans, {len(latest_opportunities)} active opportunities")
                print(f"    📊 Active exchanges: {exchanges_str}")
                if gateio_loading:
                    print(f"    ⏳ Gate.io still loading...")
                if kucoin_loading:
                    print(f"    ⏳ KuCoin still loading...")
                total_scanned = 0
            
            tasks = [scan_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            found_in_batch = 0
            for result in results:
                if result:
                    found_in_batch += 1
                    symbol_name = result['symbol']
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != symbol_name]
                    latest_opportunities.append(result)
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    if len(latest_opportunities) > 50:
                        latest_opportunities = latest_opportunities[:50]
                    
                    print(f"  🎯 FOUND: {result['symbol']} - {result['spread']}% on {result['buy_exchange']} → {result['sell_exchange']}")
            
            total_scanned += len(batch)
            
            now = time.time()
            if now - last_log_time >= 15:
                last_log_time = now
                exchanges_str = ', '.join(ready_exchanges)
                status = ""
                if gateio_loading:
                    status = " | Gate.io loading..."
                if kucoin_loading:
                    status = " | KuCoin loading..."
                print(f"[{time.strftime('%H:%M:%S')}] 📊 Scanned {total_scanned}/{len(all_symbols)} | Found: {found_in_batch} | Active: {len(latest_opportunities)} | Exchanges: {exchanges_str}{status}")
                
                if latest_opportunities:
                    best = latest_opportunities[0]
                    print(f"    🏆 BEST: {best['symbol']} - {best['spread']}% → {best['buy_exchange']} to {best['sell_exchange']}")
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"❌ Scanner error: {e}")
            await asyncio.sleep(1)

# ============ WEB UI ============
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(continuous_scanner())

@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Arbitrage Scanner - 3 Exchanges</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 16px; }
        .container { max-width: 600px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #222; }
        .header h1 { font-size: 24px; color: #fff; }
        .badge { display: inline-block; background: #2ecc71; color: #0a0a0a; padding: 4px 10px; border-radius: 16px; font-size: 10px; font-weight: 600; margin: 6px 0; }
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
        .loading-badge { background: #f39c12; animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 MEXC → Gate.io → KuCoin</span>
                <span class="badge">🪙 1000 coins max</span>
                <span class="badge loading-badge" id="gateioStatus">⏳ Gate.io Loading...</span>
                <span class="badge loading-badge" id="kucoinStatus">⏳ KuCoin Loading...</span>
            </div>
        </div>
        <div class="stats">
            <span><span class="live-indicator"></span> SCANNING</span>
            <span id="count">0 opportunities</span>
        </div>
        <div id="opportunities-container"></div>
        <div class="footer">💰 0.1%+ profit | $30+ liquidity | Loading: MEXC → Gate.io → KuCoin</div>
    </div>
    <script>
        let allOpportunities = [], expandedCard = null;
        let gateioReady = false;
        let kucoinReady = false;
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function timeAgo(ts) { const s = Math.floor(Date.now()/1000 - ts); return s < 60 ? `${s}s ago` : `${Math.floor(s/60)}m ago`; }
        function formatExchange(n) { const names={'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin'}; return names[n]||n; }
        function toggleDetail(id,e) { e.stopPropagation(); const d = document.getElementById(`detail-${id}`); if(expandedCard===id){ d.classList.remove('show'); expandedCard=null; } else { if(expandedCard!==null) document.getElementById(`detail-${expandedCard}`).classList.remove('show'); d.classList.add('show'); expandedCard=id; } }
        function getExchangeLink(ex,sym) { const l=ex.toLowerCase(); if(l==='mexc') return `https://www.mexc.com/exchange/${sym}_USDT`; if(l==='gateio') return `https://www.gate.io/trade/${sym}_USDT`; if(l==='kucoin') return `https://www.kucoin.com/trade/${sym}-USDT`; return '#'; }
        function updateDisplay() {
            const c = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            if(allOpportunities.length===0){ c.innerHTML='<div class="no-data">🔍 Scanning MEXC first...<br><span style="font-size:12px;">Gate.io and KuCoin loading in background</span><br><span style="font-size:11px; color:#f39c12;">MEXC → Gate.io → KuCoin</span></div>'; return; }
            c.innerHTML = allOpportunities.map((opp,idx)=>`<div class="opportunity-card" onclick="toggleDetail(${idx},event)"><div class="card-main"><div class="left-section"><div class="exchange-pair"><span class="buy-text">BUY</span> <span class="exchange-name">${formatExchange(opp.buy_exchange)}</span> <span class="sell-text">→ SELL</span> <span class="exchange-name">${formatExchange(opp.sell_exchange)}</span></div><div class="token-symbol">${opp.symbol}/USDT</div><div class="details-row"><span>💰 $${opp.liquidity.toLocaleString()}</span><span>⏱️ ${timeAgo(opp.timestamp)}</span></div></div><div class="profit-section"><div class="spread-percent">${opp.spread}%</div><div class="net-profit">net: ${opp.net_profit}%</div></div></div><div class="detail-expanded" id="detail-${idx}"><div class="trade-section"><div class="trade-title">1️⃣ Buy at ${formatExchange(opp.buy_exchange)}</div><div class="info-row"><span class="info-label">Lowest Ask:</span><span class="info-value">$${opp.buy_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.buy_liquidity.toLocaleString()}</span></div><div class="network-list"><strong>Withdrawal:</strong> ${opp.recommended_network} ($${opp.withdrawal_fee})</div><div class="button-group"><a href="${getExchangeLink(opp.buy_exchange, opp.symbol)}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 Go to ${formatExchange(opp.buy_exchange)}</a></div></div><div class="trade-section"><div class="trade-title">2️⃣ Sell at ${formatExchange(opp.sell_exchange)}</div><div class="info-row"><span class="info-label">Highest Bid:</span><span class="info-value">$${opp.sell_price}</span></div><div class="info-row"><span class="info-label">Liquidity:</span><span class="info-value">$${opp.sell_liquidity.toLocaleString()}</span></div><div class="network-list"><strong>Deposit:</strong> ${opp.recommended_network} (Free)</div><div class="button-group"><a href="${getExchangeLink(opp.sell_exchange, opp.symbol)}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 Go to ${formatExchange(opp.sell_exchange)}</a></div></div><div class="info-row"><span class="info-label">Gross Spread:</span><span class="info-value">${opp.spread}%</span></div><div class="info-row"><span class="info-label">Net Profit:</span><span class="info-value">${opp.net_profit}% ($${opp.net_profit_usd} on $100)</span></div><div class="warning-box">⚠️ Double check coin contract before trading</div><div class="time-warning">🟢 Act fast - opportunities last 10-15 min</div></div></div>`).join('');
            expandedCard = null;
        }
        ws.onmessage = (e) => { 
            const data = JSON.parse(e.data);
            if (data.gateio_ready !== undefined) {
                gateioReady = data.gateio_ready;
                document.getElementById('gateioStatus').textContent = gateioReady ? '✅ Gate.io Ready' : '⏳ Gate.io Loading...';
                document.getElementById('gateioStatus').style.background = gateioReady ? '#2ecc71' : '#f39c12';
            }
            if (data.kucoin_ready !== undefined) {
                kucoinReady = data.kucoin_ready;
                document.getElementById('kucoinStatus').textContent = kucoinReady ? '✅ KuCoin Ready' : '⏳ KuCoin Loading...';
                document.getElementById('kucoinStatus').style.background = kucoinReady ? '#2ecc71' : '#f39c12';
            }
            if (data.opportunities) {
                allOpportunities = data.opportunities;
                updateDisplay();
            }
        };
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
        message = {
            'opportunities': latest_opportunities,
            'gateio_ready': not gateio_loading,
            'kucoin_ready': not kucoin_loading
        }
        if message != last_sent:
            await websocket.send_json(message)
            last_sent = message.copy()
        await asyncio.sleep(2)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 10000))
    print(f"\n{'='*60}")
    print(f"🚀 ARBITRAGE SCANNER - SEQUENTIAL LOADING")
    print(f"{'='*60}")
    print(f"📊 Loading order: MEXC → Gate.io → KuCoin")
    print(f"🪙 Max coins per exchange: {MAX_COINS_PER_EXCHANGE}")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}% | Min Liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
