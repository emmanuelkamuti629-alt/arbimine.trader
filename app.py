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
        'options': {'defaultType': 'spot', 'timeout': 30000}
    },
    'kucoin': {
        'apiKey': os.getenv('KUCOIN_API_KEY', ''),
        'secret': os.getenv('KUCOIN_SECRET', ''),
        'password': os.getenv('KUCOIN_PASSPHRASE', ''),
        'options': {'defaultType': 'spot', 'timeout': 30000}
    },
    'gateio': {
        'apiKey': os.getenv('GATEIO_API_KEY', ''),
        'secret': os.getenv('GATEIO_SECRET', ''),
        'options': {'defaultType': 'spot', 'timeout': 30000}
    },
    'bitget': {
        'apiKey': os.getenv('BITGET_API_KEY', ''),
        'secret': os.getenv('BITGET_SECRET', ''),
        'options': {'defaultType': 'spot', 'timeout': 30000}
    },
    'coinex': {
        'apiKey': os.getenv('COINEX_API_KEY', ''),
        'secret': os.getenv('COINEX_SECRET', ''),
        'options': {'defaultType': 'spot', 'timeout': 30000}
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
    'mexc': {'ERC20': 0.14, 'TRC20': 0.10, 'BEP20': 0.05, 'SOL': 0.02},
    'kucoin': {'ERC20': 0.15, 'TRC20': 0.08, 'BEP20': 0.06, 'SOL': 0.01},
    'gateio': {'ERC20': 0.15, 'TRC20': 0.09, 'BEP20': 0.05, 'SOL': 0.02},
    'bitget': {'ERC20': 0.12, 'TRC20': 0.07, 'BEP20': 0.04, 'SOL': 0.01},
    'coinex': {'ERC20': 0.13, 'TRC20': 0.08, 'BEP20': 0.05, 'SOL': 0.02},
}

MIN_PROFIT_PERCENT = 0.1
MIN_LIQUIDITY_USD = 30
BATCH_SIZE = 10

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
all_known_symbols = []
historical_profits = {}

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
    elif 'kucoin' in exchange_lower:
        return f"https://www.kucoin.com/trade/{symbol}-USDT"
    elif 'gate' in exchange_lower:
        return f"https://www.gate.io/trade/{symbol}_USDT"
    elif 'bitget' in exchange_lower:
        return f"https://www.bitget.com/spot/{symbol}USDT"
    elif 'coinex' in exchange_lower:
        return f"https://www.coinex.com/trading/{symbol}USDT"
    return "#"

# ============ SCANNER FUNCTIONS ============
async def get_all_symbols():
    if len(exchanges) < 2:
        print("❌ Need at least 2 exchanges with valid API keys")
        return []
    
    print("📊 Loading markets from exchanges (timeout: 45 seconds)...")
    
    try:
        # Load markets with timeout
        tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=45.0)
        print("✓ Markets loaded successfully")
    except asyncio.TimeoutError:
        print("❌ Market loading timed out after 45 seconds")
        print("💡 Check your API keys and network connection")
        return []
    except Exception as e:
        print(f"❌ Error loading markets: {e}")
        return []
    
    # Find common symbols across all exchanges
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    symbols = [s for s in common if s.endswith('/USDT')]
    
    if not symbols:
        print("⚠️ No common USDT pairs found across exchanges")
        return []
    
    print(f"✓ Found {len(symbols)} common USDT pairs")
    return symbols

async def quick_scan_symbol(symbol):
    try:
        # Fetch order books with timeout
        tasks = [ex.fetch_order_book(symbol, limit=1) for ex in exchanges.values()]
        obs = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
        
        data = {}
        for (name, ex), ob in zip(exchanges.items(), obs):
            if isinstance(ob, Exception):
                continue
            if ob and ob.get('asks') and ob.get('bids') and len(ob['asks']) > 0:
                data[name] = {
                    'ask': ob['asks'][0][0],
                    'bid': ob['bids'][0][0] if ob['bids'] else ob['asks'][0][0],
                    'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],
                    'bid_vol': (ob['bids'][0][1] * ob['bids'][0][0]) if ob['bids'] else 0,
                }
        
        if len(data) < 2:
            return {'symbol': symbol.replace('/USDT', ''), 'is_active': False}
        
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
                'timestamp': time.time(),
                'is_active': True
            }
        
        return {'symbol': symbol.replace('/USDT', ''), 'is_active': False}
        
    except asyncio.TimeoutError:
        return {'symbol': symbol.replace('/USDT', ''), 'is_active': False}
    except Exception as e:
        return {'symbol': symbol.replace('/USDT', ''), 'is_active': False}

async def continuous_scanner():
    global latest_opportunities, all_known_symbols
    
    if len(exchanges) < 2:
        print("\n" + "="*60)
        print("❌ CANNOT START SCANNER")
        print("="*60)
        print("Need at least 2 exchanges with valid API keys.")
        print("\nPlease add your API keys to Render environment variables:")
        for name in API_KEYS.keys():
            print(f"  - {name.upper()}_API_KEY")
            print(f"  - {name.upper()}_SECRET")
        if 'kucoin' in API_KEYS:
            print(f"  - KUCOIN_PASSPHRASE")
        print("="*60)
        return
    
    all_known_symbols = await get_all_symbols()
    
    if len(all_known_symbols) == 0:
        print("❌ No common USDT pairs found across exchanges")
        print("💡 Make sure all exchanges have USDT pairs in common")
        return
    
    print(f"\n{'='*60}")
    print(f"🔄 REAL EXCHANGE ARBITRAGE SCANNER ACTIVE")
    print(f"{'='*60}")
    print(f"📊 Connected exchanges: {', '.join(exchanges.keys())}")
    print(f"📈 Total USDT pairs: {len(all_known_symbols)}")
    print(f"💰 Min profit: {MIN_PROFIT_PERCENT}% | Min liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"⚡ Mode: Continuous scanning (never stops)")
    print(f"{'='*60}\n")
    
    scan_index = 0
    
    while True:
        try:
            if scan_index >= len(all_known_symbols):
                scan_index = 0
                # Re-prioritize based on historical profits
                all_known_symbols.sort(key=lambda s: -historical_profits.get(s, {}).get('last_profit', 0))
                print(f"\n[{time.strftime('%H:%M:%S')}] 🔄 Completed full cycle. Re-prioritizing...")
            
            batch = all_known_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            
            tasks = [quick_scan_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            for i, result in enumerate(results):
                if not result:
                    continue
                    
                symbol_name = batch[i].replace('/USDT', '')
                
                if result.get('is_active'):
                    historical_profits[batch[i]] = {
                        'last_profit': result['spread'],
                        'last_seen': time.time()
                    }
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != symbol_name]
                    latest_opportunities.append(result)
                else:
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != symbol_name]
            
            # Sort by profit and keep top 50
            latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
            if len(latest_opportunities) > 50:
                latest_opportunities = latest_opportunities[:50]
            
            # Print status every few batches
            if scan_index % (BATCH_SIZE * 5) == 0 or scan_index <= BATCH_SIZE:
                scanned = min(scan_index, len(all_known_symbols))
                print(f"[{time.strftime('%H:%M:%S')}] 📊 Scanned {scanned}/{len(all_known_symbols)} | Active: {len(latest_opportunities)}")
                
                if latest_opportunities:
                    best = latest_opportunities[0]
                    print(f"    🎯 Best: {best['symbol']} - {best['spread']}% spread (net: {best['net_profit']}%)")
            
            await asyncio.sleep(0.1)
            
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
    <title>Arbitrage Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
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
        .header h1 { font-size: 24px; color: #fff; }
        .badge {
            display: inline-block;
            background: #2ecc71;
            color: #0a0a0a;
            padding: 4px 10px;
            border-radius: 16px;
            font-size: 10px;
            font-weight: 600;
            margin: 6px 0;
        }
        .network-badge { background: #3498db; }
        .stats {
            display: flex;
            justify-content: space-between;
            margin-bottom: 16px;
            font-size: 12px;
            color: #666;
        }
        .live-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: #2ecc71;
            border-radius: 50%;
            animation: blink 1s infinite;
            margin-right: 6px;
        }
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }
        .opportunity-card {
            background: #121212;
            border-radius: 12px;
            margin-bottom: 8px;
            border: 1px solid #1e1e1e;
            cursor: pointer;
            transition: all 0.2s;
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
        }
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
        .detail-expanded {
            border-top: 1px solid #1e1e1e;
            padding: 16px;
            background: #0d0d0d;
            display: none;
        }
        .detail-expanded.show { display: block; }
        .trade-section { margin-bottom: 20px; }
        .trade-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 12px; }
        .info-row { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 13px; }
        .info-label { color: #888; }
        .info-value { color: #fff; font-weight: 500; }
        .network-section { margin-top: 12px; padding-top: 8px; border-top: 1px solid #1e1e1e; }
        .network-title { font-size: 12px; color: #888; margin-bottom: 6px; }
        .network-item { background: #1a1a1a; padding: 8px 12px; border-radius: 8px; margin-bottom: 6px; }
        .network-name { color: #2ecc71; font-weight: 600; }
        .fee-value { color: #f39c12; }
        .network-list { font-size: 11px; color: #888; word-break: break-all; }
        .button-group { display: flex; gap: 12px; margin: 16px 0; }
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
        }
        .action-btn:hover { background: #f39c12; border-color: #f39c12; color: #0a0a0a; }
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Arbitrage Scanner</h1>
            <div>
                <span class="badge">📊 SPOT | USDT</span>
                <span class="badge network-badge">🌐 ALL NETWORKS</span>
            </div>
        </div>
        <div class="stats">
            <span><span class="live-indicator"></span> REAL EXCHANGES</span>
            <span id="count">0 opportunities</span>
        </div>
        <div id="opportunities-container"></div>
        <div class="footer">
            💰 Min profit: 0.1% | Min liquidity: $30 | Continuous scanning<br>
            🌐 Shows ALL available networks | Auto-selects cheapest
        </div>
    </div>
    <script>
        let allOpportunities = [];
        let expandedCard = null;
        const ws = new WebSocket(`ws://${location.host}/ws`);
        
        function timeAgo(ts) {
            const s = Math.floor(Date.now()/1000 - ts);
            if (s < 60) return `${s}s ago`;
            return `${Math.floor(s/60)}m ago`;
        }
        
        function formatExchange(n) {
            const names = {'GATEIO':'Gate.io','KUCOIN':'KuCoin','MEXC':'MEXC','BITGET':'Bitget','COINEX':'CoinEx'};
            return names[n] || n;
        }
        
        function toggleDetail(id, e) {
            e.stopPropagation();
            const d = document.getElementById(`detail-${id}`);
            if (expandedCard === id) {
                d.classList.remove('show');
                expandedCard = null;
            } else {
                if (expandedCard !== null) {
                    document.getElementById(`detail-${expandedCard}`).classList.remove('show');
                }
                d.classList.add('show');
                expandedCard = id;
            }
        }
        
        function getExchangeLink(ex, sym) {
            const l = ex.toLowerCase();
            if (l === 'mexc') return `https://www.mexc.com/exchange/${sym}_USDT`;
            if (l === 'kucoin') return `https://www.kucoin.com/trade/${sym}-USDT`;
            if (l === 'gateio') return `https://www.gate.io/trade/${sym}_USDT`;
            if (l === 'bitget') return `https://www.bitget.com/spot/${sym}USDT`;
            if (l === 'coinex') return `https://www.coinex.com/trading/${sym}USDT`;
            return '#';
        }
        
        function updateDisplay() {
            const c = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            
            if (allOpportunities.length === 0) {
                c.innerHTML = '<div class="no-data">🔍 Scanning real exchanges...<br><span style="font-size: 12px;">Looking for 0.1%+ opportunities with $30+ liquidity</span><br><span style="font-size: 11px; color: #444;">Checking ALL available networks for best fees</span></div>';
                return;
            }
            
            c.innerHTML = allOpportunities.map((opp, idx) => {
                const buyLink = getExchangeLink(opp.buy_exchange, opp.symbol);
                const sellLink = getExchangeLink(opp.sell_exchange, opp.symbol);
                const ageSeconds = Math.floor(Date.now()/1000 - opp.timestamp);
                const ageWarning = ageSeconds > 600 ? '⚠️ 10+ minutes old - act fast!' : '🟢 Fresh opportunity';
                
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
                                <div class="network-title">📤 Active Withdrawal Networks &amp; Fees:</div>
                                <div class="network-list">${opp.buy_networks}</div>
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
                                <div class="network-title">📥 Active Deposit Networks:</div>
                                <div class="network-list">${opp.sell_networks}</div>
                            </div>
                            <div class="button-group">
                                <a href="${sellLink}" target="_blank" class="action-btn" onclick="event.stopPropagation()">📊 CHECK ON ${formatExchange(opp.sell_exchange)}</a>
                            </div>
                        </div>
                        
                        <div class="network-section" style="background: rgba(46,204,113,0.1); border-left: 3px solid #2ecc71;">
                            <div class="network-title">✅ Recommended Network (Lowest Fee):</div>
                            <div class="network-item">
                                <span class="network-name">⭐ ${opp.recommended_network}</span>
                                <span class="fee-value">Withdrawal: $${opp.withdrawal_fee}</span>
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
        
        ws.onmessage = (e) => {
            allOpportunities = JSON.parse(e.data);
            updateDisplay();
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
        if latest_opportunities != last_sent:
            await websocket.send_json(latest_opportunities)
            last_sent = latest_opportunities.copy()
        await asyncio.sleep(2)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    print(f"\n{'='*60}")
    print(f"🚀 REAL EXCHANGE ARBITRAGE SCANNER")
    print(f"{'='*60}")
    print(f"📊 Settings:")
    print(f"   - Min Profit: {MIN_PROFIT_PERCENT}%")
    print(f"   - Min Liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"   - Networks: ALL available (auto-select cheapest)")
    print(f"🔐 Mode: REAL EXCHANGE APIS (NO DEMO)")
    print(f"⚡ Scanning: Continuous - never stops")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
