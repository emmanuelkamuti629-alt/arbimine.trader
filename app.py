import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import time
import os
import json
from datetime import datetime
from collections import Counter

# ================= CONFIG =================
EXCHANGE_IDS = ["gateio", "kucoin", "mexc", "bitget", "coinex", "bingx"]
MAX_COINS = 500
CACHE_FILE = "symbols_cache.json"
CACHE_TTL = 3600

TRADING_FEES = {
    'gateio': 0.1, 'kucoin': 0.1, 'mexc': 0.1,
    'bitget': 0.1, 'coinex': 0.2, 'bingx': 0.1,
}

WITHDRAWAL_FEES = {
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06},
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05},
    'bitget': {'ERC20': 0.12, 'TRC20': 0.07, 'BEP20': 0.04},
    'coinex': {'ERC20': 0.13, 'TRC20': 0.08, 'BEP20': 0.05},
    'bingx': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.05},
}

MIN_PROFIT_PERCENT = 0.5
MIN_LIQUIDITY_USD = 50
BATCH_SIZE = 50
MARKET_LOAD_TIMEOUT = 25
SCAN_INTERVAL = 0.01

app = FastAPI()
latest_opportunities = []
all_symbols = []
exchanges = {}
exchanges_loaded = 0
scanning_active = False
scan_round = 0

# ================= CACHE =================
def load_cached_symbols():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                if time.time() - data.get('timestamp', 0) < CACHE_TTL:
                    return data.get('symbols', {})
    except: pass
    return {}

def save_cached_symbols(symbols_dict):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({'timestamp': time.time(), 'symbols': symbols_dict}, f)
    except: pass

# ================= LOAD EXCHANGE =================
async def load_exchange(exchange_id, cached_data):
    global exchanges_loaded, scanning_active
    
    start = datetime.now()
    exchange = None

    try:
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            "enableRateLimit": True,
            "timeout": 3000,
            "options": {"defaultType": "spot"}
        }

        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY', '')
        if exchange_id in ['mexc', 'bitget']:
            api_secret = os.getenv(f'{exchange_id.upper()}_SECRET_KEY', '')
        else:
            api_secret = os.getenv(f'{exchange_id.upper()}_API_SECRET', '')
        
        if exchange_id == 'kucoin':
            passphrase = os.getenv('KUCOIN_API_PASSPHRASE', '')
            if passphrase: config['password'] = passphrase
        elif exchange_id == 'bitget':
            passphrase = os.getenv('BITGET_PASSPHRASE', '')
            if passphrase: config['password'] = passphrase

        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret

        exchange = exchange_class(config)
        print(f"⚡ Loading {exchange_id}...")

        if exchange_id in cached_data:
            symbols = [s for s in cached_data[exchange_id] if s.endswith('/USDT')]
            print(f"✓ {exchange_id}: {len(symbols)} pairs (cached)")
        else:
            print(f"📡 {exchange_id}: Fetching markets...")
            try:
                await asyncio.wait_for(exchange.load_markets(), timeout=MARKET_LOAD_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"⏰ {exchange_id} TIMEOUT")
                if exchange: await exchange.close()
                return None
            usdt_symbols = [s for s in exchange.symbols if s.endswith('/USDT')]
            symbols = usdt_symbols[:MAX_COINS]
            print(f"✓ {exchange_id}: {len(symbols)} pairs")

        exchanges[exchange_id] = exchange
        exchanges_loaded += 1
        
        elapsed = (datetime.now() - start).total_seconds()
        print(f"📊 {exchange_id} ready in {elapsed:.1f}s")
        
        return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

    except Exception as e:
        print(f"❌ {exchange_id}: {type(e).__name__}")
        if exchange:
            try: await exchange.close()
            except: pass
        return None

async def safe_load(exchange_id, cached_data):
    try: return await load_exchange(exchange_id, cached_data)
    except Exception as e:
        print(f"❌ {exchange_id} FATAL: {e}")
        return None

# ================= INITIALIZE - WITH COUNTER =================
async def initialize_exchanges():
    global exchanges, all_symbols, exchanges_loaded, scanning_active
    
    print("\n" + "="*50)
    print("⚡ ARBIHUNT SCANNER")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}% | Min Liq: ${MIN_LIQUIDITY_USD}")
    print("="*50 + "\n")

    cached_data = load_cached_symbols()
    exchanges_loaded = 0
    scanning_active = False
    all_symbols = []

    tasks = [safe_load(eid, cached_data) for eid in EXCHANGE_IDS]
    results = await asyncio.gather(*tasks)

    # ✅ Use Counter to only keep symbols on 2+ exchanges
    symbol_counter = Counter()
    
    new_cache = {}
    for result in results:
        if result and result.get("symbols"):
            new_cache[result["id"]] = result["symbols"]
            for s in result["symbols"]:
                symbol_counter[s] += 1
    
    if new_cache: save_cached_symbols(new_cache)
    
    # Only scan symbols available on at least 2 exchanges
    all_symbols = [s for s, count in symbol_counter.items() if count >= 2]

    print(f"\n✅ {exchanges_loaded}/{len(EXCHANGE_IDS)} exchanges loaded")
    print(f"📊 {len(all_symbols)} pairs on 2+ exchanges (from {sum(symbol_counter.values())} total)")
    print(f"🔍 Counter filter applied: only scanning cross-exchange pairs\n")
    
    if exchanges_loaded >= 2 and len(all_symbols) > 0:
        scanning_active = True
        asyncio.create_task(continuous_scanner())
    
    return exchanges_loaded >= 2

# ================= SCAN SYMBOL =================
async def scan_symbol(symbol):
    try:
        tasks = []
        ex_names = []
        for name, ex in exchanges.items():
            tasks.append(asyncio.wait_for(ex.fetch_order_book(symbol, limit=1), timeout=2.0))
            ex_names.append(name)
        
        if len(tasks) < 2: return None
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        data = {}
        for name, result in zip(ex_names, results):
            if isinstance(result, Exception): continue
            if result and result.get('asks') and result.get('bids'):
                if len(result['asks']) > 0 and len(result['bids']) > 0:
                    ask = result['asks'][0][0]
                    bid = result['bids'][0][0]
                    ask_vol = (result['asks'][0][1] or 0) * ask
                    bid_vol = (result['bids'][0][1] or 0) * bid
                    data[name] = {'ask': ask, 'bid': bid, 'ask_vol': ask_vol, 'bid_vol': bid_vol}
        
        if len(data) < 2: return None
        
        best = None
        best_profit = 0
        
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue
                buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1)/100)
                sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1)/100)
                profit = (sell_rev - buy_cost) / buy_cost * 100
                liq = min(b['ask_vol'], s['bid_vol'])
                
                if profit > best_profit and liq >= MIN_LIQUIDITY_USD:
                    best_profit = profit
                    best = {
                        'symbol': symbol.replace('/USDT', ''),
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': round(b['ask'], 8),
                        'sell_price': round(s['bid'], 8),
                        'spread': round(profit, 2),
                        'net_profit': round(profit - 0.2, 4),
                        'liquidity': round(liq, 0),
                        'buy_liquidity': round(b['ask_vol'], 0),
                        'sell_liquidity': round(s['bid_vol'], 0),
                        'buy_volume': round(b['ask_vol'] * 50, 0),
                        'sell_volume': round(s['bid_vol'] * 50, 0),
                        'withdrawal_fee': 0.05,
                        'network': 'BEP20',
                        'timestamp': time.time()
                    }
        
        if best and best_profit >= MIN_PROFIT_PERCENT:
            return best
        return None
    except: return None

# ================= CONTINUOUS SCANNER =================
async def continuous_scanner():
    global latest_opportunities, scan_round
    
    print(f"⚡ SCANNER ACTIVE - {len(all_symbols)} pairs\n")
    
    scan_index = 0
    cycle_start = time.time()
    
    while True:
        try:
            if scan_index >= len(all_symbols) or len(all_symbols) == 0:
                scan_round += 1
                cycle_time = time.time() - cycle_start
                top = latest_opportunities[0]['symbol'] if latest_opportunities else 'None'
                print(f"[{time.strftime('%H:%M:%S')}] 🔄 Round {scan_round} in {cycle_time:.1f}s | Top: {top} | Opps: {len(latest_opportunities)}")
                cycle_start = time.time()
                scan_index = 0
                
                if len(all_symbols) == 0:
                    await asyncio.sleep(5)
                    continue
            
            batch = all_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            tasks = [scan_symbol(s) for s in batch]
            results = await asyncio.gather(*tasks)
            
            for r in results:
                if r:
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != r['symbol']]
                    latest_opportunities.append(r)
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    latest_opportunities = latest_opportunities[:50]
            
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"❌ Scan error: {e}")
            await asyncio.sleep(1)

# ================= STARTUP =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(initialize_exchanges())

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "exchanges": exchanges_loaded,
        "opps": len(latest_opportunities),
        "round": scan_round,
        "symbols": len(all_symbols)
    }

# ================= WEB UI - ARBIHUNT STYLE =================
@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>ArbiHunt</title>
    <style>
        :root {
            --bg: #0D0D0D;
            --card: #141414;
            --border: #1F1F1F;
            --text: #E0E0E0;
            --text-secondary: #888;
            --green: #00C853;
            --red: #FF5252;
            --orange: #FF9800;
            --gold: #FFD700;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
            -webkit-tap-highlight-color: transparent;
            -webkit-font-smoothing: antialiased;
        }
        
        .header {
            position: sticky;
            top: 0;
            z-index: 100;
            background: var(--bg);
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .logo {
            font-size: 22px;
            font-weight: 800;
            background: linear-gradient(135deg, #FFD700, #FFA000);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .pro-badge {
            background: linear-gradient(135deg, #FFD700, #FF8F00);
            color: #000;
            font-size: 11px;
            font-weight: 700;
            padding: 6px 14px;
            border-radius: 20px;
            cursor: pointer;
        }
        
        .pro-lock-banner {
            background: linear-gradient(135deg, rgba(255,152,0,0.15), rgba(255,152,0,0.05));
            border: 1px solid rgba(255,152,0,0.3);
            border-radius: 12px;
            margin: 12px 16px;
            padding: 14px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .lock-text {
            flex: 1;
            font-size: 13px;
            color: var(--orange);
            font-weight: 600;
        }
        .go-pro-btn {
            background: linear-gradient(135deg, #FFD700, #FF8F00);
            color: #000;
            font-weight: 700;
            font-size: 12px;
            padding: 10px 18px;
            border-radius: 20px;
            border: none;
            cursor: pointer;
            white-space: nowrap;
        }
        
        .stats-bar {
            display: flex;
            gap: 8px;
            padding: 8px 16px;
            overflow-x: auto;
        }
        .stats-bar::-webkit-scrollbar { display: none; }
        .stat-pill {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 8px 14px;
            font-size: 11px;
            white-space: nowrap;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .live-dot {
            width: 7px;
            height: 7px;
            background: #00E676;
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }
        .stat-pill .count { color: #fff; font-weight: 700; }
        
        .opp-list { padding: 8px 16px 100px; }
        .opp-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            margin-bottom: 10px;
            overflow: hidden;
            cursor: pointer;
            transition: all 0.2s;
        }
        .opp-card:active { transform: scale(0.98); }
        
        .card-header {
            padding: 14px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .card-left { flex: 1; min-width: 0; }
        .exchange-route {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 6px;
            flex-wrap: wrap;
        }
        .ex-buy {
            background: rgba(0,200,83,0.15);
            color: #00C853;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 700;
        }
        .ex-sell {
            background: rgba(255,82,82,0.15);
            color: #FF5252;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 700;
        }
        .route-arrow { color: #555; font-size: 12px; }
        .symbol-name { font-size: 18px; font-weight: 700; color: #fff; }
        .card-meta {
            display: flex;
            gap: 12px;
            margin-top: 4px;
            font-size: 11px;
            color: var(--text-secondary);
        }
        .card-right { text-align: right; flex-shrink: 0; }
        .spread-badge { font-size: 24px; font-weight: 800; color: #00C853; }
        .verified-text { font-size: 10px; color: #555; margin-top: 2px; }
        
        .card-detail {
            display: none;
            border-top: 1px solid var(--border);
            background: #0F0F0F;
            padding: 16px;
        }
        .card-detail.show { display: block; }
        
        .detail-section { margin-bottom: 16px; }
        .detail-title {
            font-size: 14px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .detail-title .step {
            background: var(--border);
            color: var(--text-secondary);
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 700;
        }
        .detail-row {
            display: flex;
            justify-content: space-between;
            padding: 7px 0;
            font-size: 13px;
            border-bottom: 1px solid #1A1A1A;
        }
        .detail-row:last-child { border-bottom: none; }
        .detail-label { color: #777; }
        .detail-value { color: #fff; font-weight: 500; }
        .network-badge {
            display: inline-block;
            background: #1A1A1A;
            border: 1px solid #2A2A2A;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            color: #aaa;
            margin: 2px;
        }
        .action-btn {
            display: block;
            width: 100%;
            padding: 10px;
            background: var(--card);
            border: 1px solid var(--border);
            color: var(--text);
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            text-align: center;
            cursor: pointer;
            margin-top: 8px;
            text-decoration: none;
        }
        .action-btn:hover, .action-btn:active {
            background: #1F1F1F;
            border-color: #444;
        }
        .warning-box {
            background: rgba(255,152,0,0.1);
            border-left: 3px solid var(--orange);
            padding: 10px 12px;
            border-radius: 6px;
            font-size: 11px;
            color: var(--orange);
            margin-top: 12px;
        }
        .time-warning {
            text-align: center;
            font-size: 11px;
            color: #FF9800;
            padding: 8px;
            background: rgba(255,152,0,0.08);
            border-radius: 8px;
            margin-top: 10px;
        }
        
        .no-data {
            text-align: center;
            padding: 60px 20px;
            color: #444;
        }
        .no-data .icon { font-size: 48px; margin-bottom: 12px; }
        .no-data .sub { font-size: 11px; color: #333; margin-top: 6px; }
        
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: var(--bg);
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: space-around;
            padding: 10px 0;
            padding-bottom: max(10px, env(safe-area-inset-bottom));
            z-index: 100;
        }
        .nav-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
            cursor: pointer;
            color: #555;
            font-size: 10px;
            font-weight: 600;
            text-decoration: none;
        }
        .nav-item.active { color: #FFD700; }
        .nav-item .nav-icon { font-size: 20px; }
        
        .toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: #1F1F1F;
            color: #fff;
            padding: 10px 20px;
            border-radius: 20px;
            font-size: 12px;
            z-index: 200;
            opacity: 0;
            transition: opacity 0.3s;
            pointer-events: none;
        }
        .toast.show { opacity: 1; }
    </style>
</head>
<body>

    <div class="header">
        <div class="logo">ArbiHunt</div>
        <div class="pro-badge" onclick="showToast('Upgrade to PRO')">GO PRO</div>
    </div>

    <div class="pro-lock-banner">
        <span style="font-size:24px;">🔒</span>
        <span class="lock-text">Upgrade to PRO to Discover Profit Opportunities Above 2%</span>
        <button class="go-pro-btn" onclick="showToast('PRO Coming Soon')">GO PRO</button>
    </div>

    <div class="stats-bar">
        <div class="stat-pill"><span class="live-dot"></span> LIVE</div>
        <div class="stat-pill">📊 <span class="count" id="oppCount">0</span> opps</div>
        <div class="stat-pill">🔄 Round <span class="count" id="roundNum">0</span></div>
        <div class="stat-pill">📈 <span class="count" id="symCount">0</span> pairs</div>
    </div>

    <div class="opp-list" id="oppList">
        <div class="no-data">
            <div class="icon">⚡</div>
            <div class="text" style="font-size:14px;">Scanning all USDT pairs...</div>
            <div class="sub">Gate.io • KuCoin • MEXC • Bitget • CoinEx • BingX</div>
        </div>
    </div>

    <div class="bottom-nav">
        <a class="nav-item active" href="/"><span class="nav-icon">🏠</span> Home</a>
        <a class="nav-item" href="#pro" onclick="showToast('Upgrade to PRO')"><span class="nav-icon">⭐</span> Go PRO</a>
        <a class="nav-item" href="#chat" onclick="showToast('Chat Coming Soon')"><span class="nav-icon">💬</span> Chat Room</a>
        <a class="nav-item" href="#profile" onclick="showToast('Profile')"><span class="nav-icon">👤</span> Profile</a>
    </div>

    <div class="toast" id="toast"></div>

<script>
    let opps = [];
    let expandedCard = null;
    let roundNum = 0;
    let symCount = 0;
    let ws;
    
    function connectWS() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws`);
        
        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'opps') {
                opps = data.data;
                roundNum = data.round || roundNum;
                symCount = data.symbols || symCount;
                render();
            }
        };
        
        ws.onclose = () => setTimeout(connectWS, 2000);
        ws.onerror = () => ws.close();
    }
    
    function ago(ts) {
        const s = Math.floor(Date.now()/1000 - ts);
        if (s < 5) return 'Just now';
        if (s < 60) return `${s}s ago`;
        if (s < 3600) return `${Math.floor(s/60)}m ago`;
        return `${Math.floor(s/3600)}h ago`;
    }
    
    function fmtEx(name) {
        const map = {
            'MEXC': 'MEXC', 'GATEIO': 'Gate.io', 'KUCOIN': 'KuCoin',
            'COINEX': 'CoinEx', 'BITGET': 'Bitget', 'BINGX': 'BingX'
        };
        return map[name] || name;
    }
    
    function toggleCard(idx, e) {
        e.stopPropagation();
        const detail = document.getElementById(`detail-${idx}`);
        
        if (expandedCard === idx) {
            detail.classList.remove('show');
            expandedCard = null;
        } else {
            if (expandedCard !== null) {
                const prev = document.getElementById(`detail-${expandedCard}`);
                if (prev) prev.classList.remove('show');
            }
            detail.classList.add('show');
            expandedCard = idx;
        }
    }
    
    function render() {
        document.getElementById('oppCount').textContent = opps.length;
        document.getElementById('roundNum').textContent = roundNum;
        document.getElementById('symCount').textContent = symCount;
        
        const container = document.getElementById('oppList');
        
        if (!opps.length) {
            container.innerHTML = `
                <div class="no-data">
                    <div class="icon">⚡</div>
                    <div class="text" style="font-size:14px;">Scanning all USDT pairs...</div>
                    <div class="sub">Gate.io • KuCoin • MEXC • Bitget • CoinEx • BingX</div>
                </div>`;
            return;
        }
        
        container.innerHTML = opps.map((o, i) => `
            <div class="opp-card" onclick="toggleCard(${i}, event)">
                <div class="card-header">
                    <div class="card-left">
                        <div class="exchange-route">
                            <span class="ex-buy">BUY</span>
                            <span style="font-size:12px;color:#fff;font-weight:600;">${fmtEx(o.buy_exchange)}</span>
                            <span class="route-arrow">→</span>
                            <span class="ex-sell">SELL</span>
                            <span style="font-size:12px;color:#fff;font-weight:600;">${fmtEx(o.sell_exchange)}</span>
                        </div>
                        <div class="symbol-name">${o.symbol}/USDT</div>
                        <div class="card-meta">
                            <span>💰 $${Number(o.liquidity).toLocaleString()}</span>
                            <span>⏱ ${ago(o.timestamp)}</span>
                        </div>
                    </div>
                    <div class="card-right">
                        <div class="spread-badge">${o.spread}%</div>
                        <div class="verified-text">Last Verified ${ago(o.timestamp)}</div>
                    </div>
                </div>
                <div class="card-detail" id="detail-${i}">
                    <div class="detail-section">
                        <div class="detail-title"><span class="step">1</span> Buy at ${fmtEx(o.buy_exchange)}</div>
                        <div class="detail-row">
                            <span class="detail-label">Lowest Ask:</span>
                            <span class="detail-value">$${o.buy_price}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Volume:</span>
                            <span class="detail-value">$${Number(o.buy_volume || 0).toLocaleString()}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Buy Liquidity:</span>
                            <span class="detail-value">$${Number(o.buy_liquidity).toLocaleString()}</span>
                        </div>
                        <div style="margin-top:6px;font-size:11px;color:#777;">
                            Withdrawal Networks:<br>
                            <span class="network-badge">BEP20 ($0.05)</span>
                            <span class="network-badge">TRC20 ($0.10)</span>
                        </div>
                        <a href="https://${o.buy_exchange.toLowerCase()}.com" target="_blank" class="action-btn">📊 CHECK ON ${fmtEx(o.buy_exchange).toUpperCase()}</a>
                    </div>
                    
                    <div class="detail-section">
                        <div class="detail-title"><span class="step">2</span> Sell on ${fmtEx(o.sell_exchange)}</div>
                        <div class="detail-row">
                            <span class="detail-label">Highest Bid:</span>
                            <span class="detail-value">$${o.sell_price}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Sell Liquidity:</span>
                            <span class="detail-value">$${Number(o.sell_liquidity).toLocaleString()}</span>
                        </div>
                        <div style="margin-top:6px;font-size:11px;color:#777;">
                            Deposit Networks:<br>
                            <span class="network-badge">BEP20</span>
                            <span class="network-badge">TRC20</span>
                        </div>
                        <a href="https://${o.sell_exchange.toLowerCase()}.com" target="_blank" class="action-btn">📊 CHECK ON ${fmtEx(o.sell_exchange).toUpperCase()}</a>
                    </div>
                    
                    <div class="detail-row">
                        <span class="detail-label">Spread:</span>
                        <span class="detail-value" style="color:#00C853;">${o.spread}%</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Net Profit:</span>
                        <span class="detail-value" style="color:#00C853;">${o.net_profit}%</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Liquidity:</span>
                        <span class="detail-value">$${Number(o.liquidity).toLocaleString()}</span>
                    </div>
                    
                    <div class="warning-box">
                        ⚠️ Double check coin's contract and name on both exchanges before initiating the trade.
                    </div>
                    <div class="time-warning">
                        🟢 Act Fast! Arbitrage opportunities are time-sensitive and typically last for no more than 10-15 minutes.
                    </div>
                </div>
            </div>
        `).join('');
        
        expandedCard = null;
    }
    
    function showToast(msg) {
        const toast = document.getElementById('toast');
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2000);
    }
    
    connectWS();
    
    setInterval(async () => {
        if (ws.readyState !== WebSocket.OPEN) {
            try {
                const res = await fetch('/api/opportunities');
                opps = await res.json();
                render();
            } catch(e) {}
        }
    }, 5000);
</script>
</body>
</html>
    """)

# ================= API =================
@app.get("/api/opportunities")
async def get_opportunities():
    return latest_opportunities

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_data = None
    last_round = 0
    while True:
        try:
            current = json.dumps(latest_opportunities, default=str)
            if current != last_data or scan_round != last_round:
                await websocket.send_json({
                    "type": "opps",
                    "data": latest_opportunities,
                    "round": scan_round,
                    "symbols": len(all_symbols)
                })
                last_data = current
                last_round = scan_round
            await asyncio.sleep(0.5)
        except Exception:
            break

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 10000))
    print(f"\n{'='*50}")
    print(f"⚡ ARBIHUNT SCANNER v2.0")
    print(f"{'='*50}")
    print(f"📊 Exchanges: {', '.join(EXCHANGE_IDS)}")
    print(f"💰 Min Profit: {MIN_PROFIT_PERCENT}%")
    print(f"💵 Min Liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"🔍 Counter filter: Only pairs on 2+ exchanges")
    print(f"⏱️ Rate limiting: ENABLED")
    print(f"🌐 Web UI: http://0.0.0.0:{port}")
    print(f"{'='*50}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
