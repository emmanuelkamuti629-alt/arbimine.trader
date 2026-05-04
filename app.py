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

TRADING_FEES = {
    'mexc': 0.1,
    'kucoin': 0.1,
    'gateio': 0.1,
    'bitget': 0.1,
    'coinex': 0.2,
}

WITHDRAWAL_FEES = {
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06},
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05},
    'bitget': {'ERC20': 0.12, 'TRC20': 0.07, 'BEP20': 0.04},
    'coinex': {'ERC20': 0.13, 'TRC20': 0.08, 'BEP20': 0.05},
}

DEFAULT_NETWORK = 'ERC20'
MIN_PROFIT_PERCENT = 0.4
MIN_LIQUIDITY_USD = 100
SCAN_INTERVAL = 5
DISPLAY_REFRESH_INTERVAL = 30

EXCHANGE_URLS = {
    'MEXC': 'https://www.mexc.com/exchange/',
    'KUCOIN': 'https://www.kucoin.com/trade/',
    'GATEIO': 'https://www.gate.io/trade/',
    'BITGET': 'https://www.bitget.com/spot/',
    'COINEX': 'https://www.coinex.com/trading/',
}

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
all_known_symbols = []  # Store all symbols for priority scanning
historical_profits = {}  # Track which symbols were profitable

# ============ HELPER FUNCTIONS ============
def calculate_withdrawal_fee(exchange):
    fees = WITHDRAWAL_FEES.get(exchange.lower(), WITHDRAWAL_FEES['mexc'])
    return fees.get(DEFAULT_NETWORK, 0.14)

def calculate_net_profit(profit_pct, amount_usd, buy_exchange, sell_exchange):
    gross_profit_usd = amount_usd * (profit_pct / 100)
    withdrawal_fee = calculate_withdrawal_fee(buy_exchange)
    net_profit_usd = gross_profit_usd - withdrawal_fee
    net_profit_pct = (net_profit_usd / amount_usd) * 100 if amount_usd > 0 else 0
    return net_profit_usd, net_profit_pct, withdrawal_fee

def get_exchange_link(exchange, symbol):
    exchange_key = exchange.upper().replace('.', '')
    base_url = EXCHANGE_URLS.get(exchange_key, '')
    if base_url:
        return f"{base_url}{symbol}_USDT"
    return "#"

# ============ PRIORITY SCANNER LOGIC ============
async def get_all_symbols():
    """Get all symbols once at startup"""
    if len(exchanges) < 2:
        return []
    
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    symbols = [s for s in common if s.endswith('/USDT')]
    print(f"✓ Found {len(symbols)} total USDT pairs")
    return symbols

async def quick_scan_symbol(symbol):
    """Quick scan - just get best bid/ask for profit calculation"""
    try:
        obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=1) for ex in exchanges.values()])
        
        data = {}
        for (name, ex), ob in zip(exchanges.items(), obs):
            if ob['asks'] and ob['bids']:
                data[name] = {
                    'ask': ob['asks'][0][0],
                    'bid': ob['bids'][0][0],
                    'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],
                    'bid_vol': ob['bids'][0][1] * ob['bids'][0][0],
                }
        
        best_profit = 0
        best_opp = None
        
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue
                
                buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1) / 100)
                sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1) / 100)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                if profit_pct > best_profit and liquidity > MIN_LIQUIDITY_USD:
                    best_profit = profit_pct
                    best_opp = (buy_ex, sell_ex, b, s, profit_pct, liquidity)
        
        if best_opp and best_profit > MIN_PROFIT_PERCENT:
            buy_ex, sell_ex, b, s, profit_pct, liquidity = best_opp
            net_profit_usd, net_profit_pct, withdrawal_fee = calculate_net_profit(profit_pct, 100, buy_ex, sell_ex)
            
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
                'liquidity': round(liquidity, 0),
                'buy_liquidity': round(b['ask_vol'], 0),
                'sell_liquidity': round(s['bid_vol'], 0),
                'timestamp': time.time()
            }
        return None
    except:
        return None

async def detailed_scan_symbol(symbol, buy_ex, sell_ex, b, s, profit_pct, liquidity):
    """Get detailed data for a profitable opportunity"""
    try:
        # Get 24h volume
        buy_volume = 0
        sell_volume = 0
        for name, ex in exchanges.items():
            if name.upper() == buy_ex:
                try:
                    ticker = await ex.fetch_ticker(symbol)
                    buy_volume = ticker.get('quoteVolume', 0)
                except:
                    pass
            if name.upper() == sell_ex:
                try:
                    ticker = await ex.fetch_ticker(symbol)
                    sell_volume = ticker.get('quoteVolume', 0)
                except:
                    pass
        
        net_profit_usd, net_profit_pct, withdrawal_fee = calculate_net_profit(profit_pct, 100, buy_ex.lower(), sell_ex.lower())
        
        return {
            'symbol': symbol.replace('/USDT', ''),
            'buy_exchange': buy_ex,
            'sell_exchange': sell_ex,
            'buy_price': b['ask'],
            'sell_price': s['bid'],
            'spread': round(profit_pct, 1),
            'net_profit': round(net_profit_pct, 1),
            'net_profit_usd': round(net_profit_usd, 2),
            'withdrawal_fee': round(withdrawal_fee, 2),
            'liquidity': round(liquidity, 0),
            'buy_liquidity': round(b['ask_vol'], 0),
            'sell_liquidity': round(s['bid_vol'], 0),
            'buy_volume': round(buy_volume, 0),
            'sell_volume': round(sell_volume, 0),
            'withdrawal_network': DEFAULT_NETWORK,
            'timestamp': time.time()
        }
    except:
        return None

async def priority_scanner_loop():
    global latest_opportunities, all_known_symbols, historical_profits
    
    if len(exchanges) < 2:
        print("❌ Need at least 2 exchanges with valid API keys")
        return
    
    # Get all symbols first
    all_known_symbols = await get_all_symbols()
    print(f"\n{'='*60}")
    print(f"🔄 PRIORITY ARBITRAGE SCANNER ACTIVE")
    print(f"{'='*60}")
    print(f"📊 Connected to: {', '.join(exchanges.keys())}")
    print(f"📈 Total symbols: {len(all_known_symbols)} USDT pairs")
    print(f"⚡ Priority mode: Profitable symbols scanned first")
    print(f"⏱️  Scan interval: {SCAN_INTERVAL} seconds")
    print(f"💰 Min profit: {MIN_PROFIT_PERCENT}%")
    print(f"{'='*60}\n")
    
    # Track last time each symbol was scanned
    last_scanned = {}
    
    while True:
        scan_start = time.time()
        profitable_opportunities = []
        
        # Step 1: Sort symbols by priority
        # Priority 1: Previously profitable symbols
        # Priority 2: High liquidity symbols
        # Priority 3: Alphabetical
        
        priority_symbols = []
        
        # Add previously profitable symbols first (with recency bonus)
        for symbol, profit_data in sorted(historical_profits.items(), key=lambda x: x[1].get('last_profit', 0), reverse=True):
            if symbol in all_known_symbols:
                priority_flags = all_known_symbols.index(symbol) if symbol in all_known_symbols else -1
                priority_symbols.append(symbol)
        
        # Add remaining symbols
        for symbol in all_known_symbols:
            if symbol not in priority_symbols:
                priority_symbols.append(symbol)
        
        print(f"[{time.strftime('%H:%M:%S')}] 🔍 Priority scanning {len(priority_symbols)} symbols...")
        
        # Quick scan all symbols
        scan_tasks = []
        for symbol in priority_symbols:
            # Rate limiting - don't scan same symbol too often
            if symbol in last_scanned and time.time() - last_scanned[symbol] < 2:
                continue
            scan_tasks.append(quick_scan_symbol(symbol))
            last_scanned[symbol] = time.time()
        
        # Process results as they come in (streaming)
        results = []
        for task in asyncio.as_completed(scan_tasks):
            result = await task
            if result:
                results.append(result)
                # Update historical profits
                historical_profits[result['symbol'] + '/USDT'] = {
                    'last_profit': result['spread'],
                    'last_seen': time.time()
                }
                print(f"  🎯 Found: {result['symbol']} - {result['spread']}% spread")
        
        # Sort by profit
        results.sort(key=lambda x: x['spread'], reverse=True)
        
        # Get detailed data for top opportunities
        detailed_opportunities = []
        for opp in results[:20]:  # Get details for top 20
            # Need to fetch detailed data
            try:
                symbol = f"{opp['symbol']}/USDT"
                obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=10) for ex in exchanges.values()])
                
                data = {}
                for (name, ex), ob in zip(exchanges.items(), obs):
                    if ob['asks'] and ob['bids']:
                        data[name] = {
                            'ask': ob['asks'][0][0],
                            'bid': ob['bids'][0][0],
                            'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],
                            'bid_vol': ob['bids'][0][1] * ob['bids'][0][0],
                        }
                
                for buy_ex, b in data.items():
                    for sell_ex, s in data.items():
                        if buy_ex == sell_ex: continue
                        buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1) / 100)
                        sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1) / 100)
                        profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                        liquidity = min(b['ask_vol'], s['bid_vol'])
                        if profit_pct > MIN_PROFIT_PERCENT:
                            net_profit_usd, net_profit_pct, withdrawal_fee = calculate_net_profit(profit_pct, 100, buy_ex, sell_ex)
                            detailed_opp = {
                                'symbol': opp['symbol'],
                                'buy_exchange': buy_ex.upper(),
                                'sell_exchange': sell_ex.upper(),
                                'buy_price': b['ask'],
                                'sell_price': s['bid'],
                                'spread': round(profit_pct, 1),
                                'net_profit': round(net_profit_pct, 1),
                                'net_profit_usd': round(net_profit_usd, 2),
                                'withdrawal_fee': round(withdrawal_fee, 2),
                                'liquidity': round(liquidity, 0),
                                'buy_liquidity': round(b['ask_vol'], 0),
                                'sell_liquidity': round(s['bid_vol'], 0),
                                'buy_volume': 0,
                                'sell_volume': 0,
                                'withdrawal_network': DEFAULT_NETWORK,
                                'timestamp': time.time()
                            }
                            detailed_opportunities.append(detailed_opp)
                            break
                    break
            except:
                pass
        
        latest_opportunities = sorted(detailed_opportunities, key=lambda x: x['spread'], reverse=True)[:50]
        
        if latest_opportunities:
            best = latest_opportunities[0]
            print(f"[{time.strftime('%H:%M:%S')}] ✅ PRIORITY SCAN COMPLETE")
            print(f"    Found {len(latest_opportunities)} opportunities")
            print(f"    Best: {best['symbol']} - {best['spread']}% spread (net: {best['net_profit']}%)")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] 🔍 Priority scan complete - No profitable opportunities")
        
        elapsed = time.time() - scan_start
        wait_time = max(0, SCAN_INTERVAL - elapsed)
        if wait_time > 0:
            await asyncio.sleep(wait_time)

# ============ WEB UI ============
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(priority_scanner_loop())

@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>Priority Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            padding: 16px;
        }
        
        .container {
            max-width: 600px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid #222;
        }
        .header h1 {
            font-size: 24px;
            color: #fff;
        }
        .badge {
            display: inline-block;
            background: #f39c12;
            color: #0a0a0a;
            padding: 4px 10px;
            border-radius: 16px;
            font-size: 10px;
            font-weight: 600;
            margin: 6px 0;
        }
        .priority-badge {
            background: #2ecc71;
            margin-left: 6px;
        }
        
        .stats {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            font-size: 12px;
            color: #666;
        }
        
        .opportunity-card {
            background: #121212;
            border-radius: 12px;
            margin-bottom: 8px;
            overflow: hidden;
            border: 1px solid #1e1e1e;
            transition: all 0.2s;
            cursor: pointer;
        }
        .opportunity-card:hover {
            border-color: #f39c12;
            background: #161616;
            transform: translateX(4px);
        }
        
        .card-main {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 16px;
            cursor: pointer;
        }
        
        .left-section {
            flex: 1;
            cursor: pointer;
        }
        
        .exchange-pair {
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 6px;
        }
        .buy-text { color: #2ecc71; }
        .sell-text { color: #e74c3c; }
        .exchange-name { color: #fff; margin: 0 3px; }
        
        .token-symbol {
            font-size: 16px;
            font-weight: 700;
            color: #fff;
            margin-top: 4px;
        }
        
        .details-row {
            display: flex;
            gap: 16px;
            font-size: 11px;
            color: #666;
            margin-top: 6px;
        }
        
        .profit-section {
            text-align: right;
            cursor: pointer;
        }
        .spread-percent {
            font-size: 22px;
            font-weight: 700;
            color: #2ecc71;
        }
        .net-profit {
            font-size: 11px;
            color: #666;
            margin-top: 2px;
        }
        
        /* Expanded Detail View */
        .detail-expanded {
            border-top: 1px solid #1e1e1e;
            padding: 16px;
            background: #0d0d0d;
            display: none;
        }
        .detail-expanded.show {
            display: block;
        }
        
        .trade-section {
            margin-bottom: 20px;
        }
        .trade-title {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            margin-bottom: 12px;
        }
        .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            font-size: 13px;
        }
        .info-label {
            color: #888;
        }
        .info-value {
            color: #fff;
            font-weight: 500;
        }
        
        .network-section {
            margin-top: 12px;
            padding-top: 8px;
            border-top: 1px solid #1e1e1e;
        }
        .network-title {
            font-size: 12px;
            color: #888;
            margin-bottom: 6px;
        }
        .network-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1a1a1a;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 6px;
        }
        .network-name {
            color: #2ecc71;
            font-weight: 600;
            font-size: 13px;
        }
        .fee-value {
            color: #f39c12;
        }
        
        .button-group {
            display: flex;
            gap: 12px;
            margin: 16px 0;
        }
        .action-btn {
            flex: 1;
            background: #1a1a1a;
            border: 1px solid #2a2a2a;
            color: #e0e0e0;
            padding: 10px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            transition: all 0.2s;
        }
        .action-btn:hover {
            background: #f39c12;
            border-color: #f39c12;
            color: #0a0a0a;
            transform: scale(1.02);
        }
        
        .warning-box {
            background: rgba(231, 76, 60, 0.1);
            border-left: 3px solid #e74c3c;
            padding: 12px;
            font-size: 12px;
            color: #e74c3c;
            margin: 16px 0;
            border-radius: 6px;
        }
        
        .time-warning {
            color: #f39c12;
            font-size: 11px;
            text-align: center;
            padding: 10px;
            background: rgba(243, 156, 18, 0.1);
            border-radius: 8px;
        }
        
        .no-data {
            text-align: center;
            color: #555;
            padding: 40px;
            background: #121212;
            border-radius: 12px;
        }
        
        .footer {
            text-align: center;
            font-size: 10px;
            color: #444;
            margin-top: 20px;
            padding-top: 12px;
            border-top: 1px solid #1e1e1e;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        .scanning { animation: pulse 1s infinite; }
        
        .clickable {
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Priority Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 SPOT | USDT</span>
                <span class="badge priority-badge">⚡ PROFITABLE FIRST</span>
            </div>
        </div>
        
        <div class="stats">
            <span>⚡ Priority scan every 5s</span>
            <span id="count">0 opportunities</span>
            <span id="nextScan">⏳ Refreshing...</span>
        </div>
        
        <div id="opportunities-container"></div>
        
        <div class="footer">
            🔍 Priority scanning: Profitable symbols checked first<br>
            ✨ Click ANYTHING to expand/collapse details
        </div>
    </div>

    <script>
        let allOpportunities = [];
        let lastUpdateTime = Date.now();
        let expandedCard = null;
        
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
        
        function toggleDetail(id, event) {
            event.stopPropagation();
            const detail = document.getElementById(`detail-${id}`);
            if (expandedCard === id) {
                detail.classList.remove('show');
                expandedCard = null;
            } else {
                if (expandedCard !== null) {
                    const prevDetail = document.getElementById(`detail-${expandedCard}`);
                    if (prevDetail) prevDetail.classList.remove('show');
                }
                detail.classList.add('show');
                expandedCard = id;
                setTimeout(() => {
                    detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }, 100);
            }
        }
        
        function getOpportunityAge(timestamp) {
            const ageSeconds = (Date.now() / 1000) - timestamp;
            if (ageSeconds > 900) return '⚠️ 15+ minutes old - likely expired';
            if (ageSeconds > 600) return '⚠️ 10+ minutes old - act fast!';
            if (ageSeconds > 300) return '⚠️ 5+ minutes old - may disappear soon';
            return '🟢 Fresh opportunity - act now!';
        }
        
        function getExchangeLink(exchange, symbol) {
            const exchangeLower = exchange.toLowerCase();
            if (exchangeLower === 'mexc') return `https://www.mexc.com/exchange/${symbol}_USDT`;
            if (exchangeLower === 'kucoin') return `https://www.kucoin.com/trade/${symbol}-USDT`;
            if (exchangeLower === 'gateio') return `https://www.gate.io/trade/${symbol}_USDT`;
            if (exchangeLower === 'bitget') return `https://www.bitget.com/spot/${symbol}USDT`;
            if (exchangeLower === 'coinex') return `https://www.coinex.com/trading/${symbol}USDT`;
            return '#';
        }
        
        function updateDisplay() {
            const container = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            
            if (allOpportunities.length === 0) {
                container.innerHTML = '<div class="no-data">⚡ Priority scanning active...<br><span style="font-size: 12px;">Profitable symbols checked first</span><br><span style="font-size: 11px; color: #444;">Best opportunities will appear here instantly</span></div>';
                return;
            }
            
            container.innerHTML = allOpportunities.map((opp, idx) => {
                const buyLink = getExchangeLink(opp.buy_exchange, opp.symbol);
                const sellLink = getExchangeLink(opp.sell_exchange, opp.symbol);
                const ageWarning = getOpportunityAge(opp.timestamp);
                
                return `
                <div class="opportunity-card" onclick="toggleDetail(${idx}, event)">
                    <div class="card-main">
                        <div class="left-section">
                            <div class="exchange-pair">
                                <span class="buy-text">BUY</span>
                                <span class="exchange-name">${formatExchange(opp.buy_exchange)}</span>
                                <span class="sell-text"> → SELL</span>
                                <span class="exchange-name">${formatExchange(opp.sell_exchange)}</span>
                            </div>
                            <div class="token-symbol">${opp.symbol}/USDT</div>
                            <div class="details-row">
                                <span>💰 Liquidity: $${opp.liquidity.toLocaleString()}</span>
                                <span>⏱️ ${timeAgo(opp.timestamp)}</span>
                            </div>
                        </div>
                        <div class="profit-section">
                            <div class="spread-percent">${opp.spread}%</div>
                            <div class="net-profit">net: ${opp.net_profit}%</div>
                        </div>
                    </div>
                    
                    <div class="detail-expanded" id="detail-${idx}">
                        <div class="trade-section">
                            <div class="trade-title">1. Buy at ${formatExchange(opp.buy_exchange)}</div>
                            <div class="info-row">
                                <span class="info-label">Lowest Ask:</span>
                                <span class="info-value">$${opp.buy_price}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Buy Liquidity:</span>
                                <span class="info-value">$${opp.buy_liquidity.toLocaleString()}</span>
                            </div>
                            <div class="network-section">
                                <div class="network-title">Active Withdrawal Network(s) and Fees:</div>
                                <div class="network-item">
                                    <span class="network-name">${opp.withdrawal_network}</span>
                                    <span class="fee-value">$${opp.withdrawal_fee}</span>
                                </div>
                            </div>
                            <div class="button-group">
                                <a href="${buyLink}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 CHECK ON ${formatExchange(opp.buy_exchange)}</a>
                            </div>
                        </div>
                        
                        <div class="trade-section">
                            <div class="trade-title">2. Sell on ${formatExchange(opp.sell_exchange)}</div>
                            <div class="info-row">
                                <span class="info-label">Highest Bid:</span>
                                <span class="info-value">$${opp.sell_price}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Sell Liquidity:</span>
                                <span class="info-value">$${opp.sell_liquidity.toLocaleString()}</span>
                            </div>
                            <div class="network-section">
                                <div class="network-title">Currently Active Deposit Network(s):</div>
                                <div class="network-item">
                                    <span class="network-name">${opp.withdrawal_network}</span>
                                    <span class="fee-value">Free Deposit</span>
                                </div>
                            </div>
                            <div class="button-group">
                                <a href="${sellLink}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 CHECK ON ${formatExchange(opp.sell_exchange)}</a>
                            </div>
                        </div>
                        
                        <div class="info-row" style="margin-top: 12px;">
                            <span class="info-label">📊 Gross Spread:</span>
                            <span class="info-value" style="color: #2ecc71;">${opp.spread}%</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">💰 Net Profit (after fees):</span>
                            <span class="info-value" style="color: #2ecc71;">${opp.net_profit}% ($${opp.net_profit_usd} on $100)</span>
                        </div>
                        
                        <div class="warning-box">
                            ⚠️ Double check coin's contract and name on both exchanges before initiating the trade.
                        </div>
                        
                        <div class="time-warning">
                            ${ageWarning}<br>
                            Arbitrage opportunities are time-sensitive and typically last 10-15 minutes.
                        </div>
                    </div>
                </div>
            `}).join('');
            
            expandedCard = null;
        }
        
        function updateCountdown() {
            const elapsed = (Date.now() - lastUpdateTime) / 1000;
            const remaining = Math.max(0, 30 - elapsed);
            const nextScanEl = document.getElementById('nextScan');
            if (nextScanEl) {
                if (remaining <= 0) {
                    nextScanEl.innerHTML = '🔄 Refreshing...';
                    nextScanEl.classList.add('scanning');
                } else {
                    nextScanEl.innerHTML = `⏳ Refresh in ${Math.ceil(remaining)}s`;
                    nextScanEl.classList.remove('scanning');
                }
            }
        }
        
        ws.onmessage = (event) => {
            allOpportunities = JSON.parse(event.data);
            lastUpdateTime = Date.now();
            updateDisplay();
        };
        
        ws.onclose = () => {
            setTimeout(() => location.reload(), 3000);
        };
        
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
        await websocket.send_json(latest_opportunities)
        await asyncio.sleep(DISPLAY_REFRESH_INTERVAL)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    print(f"\n{'='*60}")
    print(f"🚀 PRIORITY ARBITRAGE SCANNER")
    print(f"{'='*60}")
    print(f"⚡ Priority mode: Profitable symbols scanned FIRST")
    print(f"📈 Finds opportunities faster by prioritizing winners")
    print(f"🔐 Using PRIVATE API keys from Render")
    print(f"📊 Spot Market | USDT Pairs Only")
    print(f"💰 Withdrawal fees included ({DEFAULT_NETWORK})")
    print(f"⏱️  Scan: 5s | Display: 30s")
    print(f"🏦 Exchanges: {', '.join(exchanges.keys())}")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
