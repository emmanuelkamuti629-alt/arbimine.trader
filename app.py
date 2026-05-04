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

# Trading fees (percentage)
TRADING_FEES = {
    'mexc': 0.1,
    'kucoin': 0.1,
    'gateio': 0.1,
    'bitget': 0.1,
    'coinex': 0.2,
}

# Withdrawal fees by exchange and network (USD)
# These are estimates - you should update with actual fees from each exchange
WITHDRAWAL_FEES = {
    'mexc': {
        'ERC20': 0.14,
        'TRC20': 0.10,
        'BEP20': 0.05,
        'SOL': 0.02,
    },
    'kucoin': {
        'ERC20': 0.15,
        'TRC20': 0.08,
        'BEP20': 0.06,
        'SOL': 0.01,
    },
    'gateio': {
        'ERC20': 0.15,
        'TRC20': 0.09,
        'BEP20': 0.05,
        'SOL': 0.02,
    },
    'bitget': {
        'ERC20': 0.12,
        'TRC20': 0.07,
        'BEP20': 0.04,
        'SOL': 0.01,
    },
    'coinex': {
        'ERC20': 0.13,
        'TRC20': 0.08,
        'BEP20': 0.05,
        'SOL': 0.02,
    },
}

# Default withdrawal network (most common)
DEFAULT_NETWORK = 'ERC20'

MIN_PROFIT_PERCENT = 0.4
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
scan_results_history = []

# ============ HELPER FUNCTIONS ============
def calculate_withdrawal_fee(exchange, amount_usd):
    """Calculate withdrawal fee in USD for a given exchange"""
    fees = WITHDRAWAL_FEES.get(exchange.lower(), WITHDRAWAL_FEES['mexc'])
    # Use ERC20 as default network
    fee = fees.get(DEFAULT_NETWORK, 0.14)
    return fee

def calculate_net_profit(profit_pct, amount_usd, buy_exchange, sell_exchange):
    """
    Calculate net profit after trading fees AND withdrawal/deposit fees
    Returns: (net_profit_usd, net_profit_pct, withdrawal_fee_usd)
    """
    # Trading profit in USD
    gross_profit_usd = amount_usd * (profit_pct / 100)
    
    # Withdrawal fee from buy exchange (you need to withdraw to send)
    withdrawal_fee = calculate_withdrawal_fee(buy_exchange, amount_usd)
    
    # Deposit fee on sell exchange (usually free, but some charge)
    deposit_fee = 0  # Most exchanges have free deposits
    
    # Net profit
    net_profit_usd = gross_profit_usd - withdrawal_fee - deposit_fee
    net_profit_pct = (net_profit_usd / amount_usd) * 100 if amount_usd > 0 else 0
    
    return net_profit_usd, net_profit_pct, withdrawal_fee

# ============ SCANNER LOGIC ============
async def get_symbols():
    if len(exchanges) < 2:
        return []
    
    await asyncio.gather(*[ex.load_markets() for ex in exchanges.values()])
    common = set.intersection(*[set(ex.symbols) for ex in exchanges.values()])
    symbols = [s for s in common if s.endswith('/USDT')]
    print(f"✓ Found {len(symbols)} USDT pairs")
    return symbols

async def get_24h_volume(symbol, exchange):
    """Get 24h volume for a symbol from exchange"""
    try:
        ticker = await exchange.fetch_ticker(symbol)
        return ticker.get('quoteVolume', 0)  # Volume in USDT
    except:
        return 0

async def scan_symbol(symbol):
    try:
        # Fetch order books
        obs = await asyncio.gather(*[ex.fetch_order_book(symbol, limit=10) for ex in exchanges.values()])
        
        # Fetch 24h volumes in parallel
        volume_tasks = []
        for ex in exchanges.values():
            try:
                volume_tasks.append(get_24h_volume(symbol, ex))
            except:
                volume_tasks.append(asyncio.sleep(0, result=0))
        
        volumes = await asyncio.gather(*volume_tasks)
        
        data = {}
        volume_data = {}
        for i, ((name, ex), ob) in enumerate(zip(exchanges.items(), obs)):
            if ob['asks'] and ob['bids']:
                data[name] = {
                    'ask': ob['asks'][0][0],
                    'bid': ob['bids'][0][0],
                    'ask_vol': ob['asks'][0][1] * ob['asks'][0][0],
                    'bid_vol': ob['bids'][0][1] * ob['bids'][0][0],
                }
                volume_data[name] = volumes[i] if i < len(volumes) else 0
        
        opps = []
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue
                
                # Calculate trading profit
                buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1) / 100)
                sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1) / 100)
                profit_pct = (sell_rev - buy_cost) / buy_cost * 100
                
                # Calculate liquidity (maximum tradeable amount)
                liquidity = min(b['ask_vol'], s['bid_vol'])
                
                # Calculate net profit including withdrawal fees
                # Use $100 as example trade amount
                trade_amount_usd = min(100, liquidity)
                net_profit_usd, net_profit_pct, withdrawal_fee = calculate_net_profit(
                    profit_pct, trade_amount_usd, buy_ex, sell_ex
                )
                
                if profit_pct > MIN_PROFIT_PERCENT and liquidity > MIN_LIQUIDITY_USD:
                    opps.append({
                        'symbol': symbol.replace('/USDT', ''),
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': b['ask'],
                        'sell_price': s['bid'],
                        'spread': round(profit_pct, 1),  # Raw spread %
                        'gross_profit': round(profit_pct, 1),
                        'net_profit': round(net_profit_pct, 1),
                        'net_profit_usd': round(net_profit_usd, 2),
                        'withdrawal_fee': round(withdrawal_fee, 2),
                        'liquidity': round(liquidity, 0),
                        'buy_liquidity': round(b['ask_vol'], 0),
                        'sell_liquidity': round(s['bid_vol'], 0),
                        'buy_volume': round(volume_data.get(buy_ex, 0), 0),
                        'sell_volume': round(volume_data.get(sell_ex, 0), 0),
                        'withdrawal_network': DEFAULT_NETWORK,
                        'timestamp': time.time()
                    })
        return opps
    except Exception as e:
        return []

async def scanner_loop():
    global latest_opportunities, last_scan_time, scan_results_history
    
    if len(exchanges) < 2:
        print("❌ Need at least 2 exchanges with valid API keys")
        return
    
    symbols = await get_symbols()
    print(f"\n{'='*60}")
    print(f"🔄 ARBITRAGE SCANNER (ArbiHunt Style)")
    print(f"{'='*60}")
    print(f"📊 Connected to: {', '.join(exchanges.keys())}")
    print(f"📈 Scanning: {len(symbols)} spot USDT pairs")
    print(f"⏱️  Scan frequency: Every {SCAN_INTERVAL} seconds")
    print(f"💰 Min spread: {MIN_PROFIT_PERCENT}%")
    print(f"💵 Min liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"🏦 Withdrawal network: {DEFAULT_NETWORK}")
    print(f"{'='*60}\n")
    
    while True:
        scan_start = time.time()
        last_scan_time = scan_start
        
        print(f"[{time.strftime('%H:%M:%S')}] 🔍 Scanning for opportunities...")
        
        results = await asyncio.gather(*[scan_symbol(s) for s in symbols])
        current_scan_results = [opp for sublist in results for opp in sublist]
        
        scan_results_history.append({
            'timestamp': scan_start,
            'opportunities': current_scan_results
        })
        
        max_history = DISPLAY_REFRESH_INTERVAL // SCAN_INTERVAL
        if len(scan_results_history) > max_history:
            scan_results_history.pop(0)
        
        for scan in reversed(scan_results_history):
            if scan['opportunities']:
                latest_opportunities = sorted(scan['opportunities'], key=lambda x: x['spread'], reverse=True)[:50]
                break
        else:
            latest_opportunities = []
        
        if latest_opportunities:
            best = latest_opportunities[0]
            print(f"[{time.strftime('%H:%M:%S')}] ✅ Found {len(current_scan_results)} opportunities")
            print(f"    Best: {best['symbol']} - {best['spread']}% spread (net: {best['net_profit']}%)")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] 🔍 No opportunities found")
        
        elapsed = time.time() - scan_start
        wait_time = max(0, SCAN_INTERVAL - elapsed)
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
    <title>ArbiHunt-Style Arbitrage Scanner</title>
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
        
        /* Header */
        .header {
            text-align: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid #222;
        }
        .header h1 {
            font-size: 24px;
            color: #fff;
            letter-spacing: -0.5px;
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
        
        /* Stats */
        .stats {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            font-size: 12px;
            color: #666;
        }
        
        /* Opportunity Cards - ArbiHunt Style */
        .opportunity-card {
            background: #121212;
            border-radius: 12px;
            margin-bottom: 8px;
            overflow: hidden;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid #1e1e1e;
        }
        .opportunity-card:hover {
            border-color: #f39c12;
            background: #161616;
        }
        
        /* Main card row */
        .card-main {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 16px;
        }
        
        .left-section {
            flex: 1;
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
        }
        .profit-percent {
            font-size: 22px;
            font-weight: 700;
            color: #2ecc71;
        }
        .net-profit {
            font-size: 11px;
            color: #666;
            margin-top: 2px;
        }
        
        /* Expanded detail view (modal style) */
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
            font-size: 13px;
            font-weight: 600;
            color: #f39c12;
            margin-bottom: 10px;
        }
        .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 13px;
        }
        .info-label { color: #888; }
        .info-value { color: #fff; font-weight: 500; }
        
        .network-box {
            background: #1a1a1a;
            border-radius: 8px;
            padding: 10px;
            margin: 10px 0;
        }
        .network-name {
            color: #2ecc71;
            font-weight: 600;
            font-size: 13px;
        }
        .fee-value {
            color: #f39c12;
        }
        
        .warning {
            background: rgba(231, 76, 60, 0.1);
            border-left: 3px solid #e74c3c;
            padding: 10px;
            font-size: 11px;
            color: #e74c3c;
            margin-top: 12px;
            border-radius: 6px;
        }
        
        .time-warning {
            color: #f39c12;
            font-size: 10px;
            margin-top: 8px;
            text-align: center;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Arbitrage Scanner</h1>
            <div class="badge">📊 SPOT | USDT | WITHDRAWAL FEES INCLUDED</div>
        </div>
        
        <div class="stats">
            <span>🔍 Scanning every 5s</span>
            <span id="count">0 opportunities</span>
            <span id="nextScan">⏳ Next scan soon</span>
        </div>
        
        <div id="opportunities-container"></div>
        
        <div class="footer">
            ⚡ Includes withdrawal fees (ERC20) | Net profit after all fees
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
        
        function toggleDetail(id) {
            const detail = document.getElementById(`detail-${id}`);
            if (expandedCard === id) {
                detail.classList.remove('show');
                expandedCard = null;
            } else {
                if (expandedCard) {
                    document.getElementById(`detail-${expandedCard}`).classList.remove('show');
                }
                detail.classList.add('show');
                expandedCard = id;
            }
        }
        
        function getOpportunityAge(timestamp) {
            const age = (Date.now() / 1000) - timestamp;
            if (age > 600) return '⚠️ 10+ minutes old - likely expired';
            if (age > 300) return '⚠️ 5+ minutes old - act fast!';
            return '🟢 Fresh opportunity';
        }
        
        function updateDisplay() {
            const container = document.getElementById('opportunities-container');
            document.getElementById('count').textContent = allOpportunities.length + ' opportunities';
            
            if (allOpportunities.length === 0) {
                container.innerHTML = '<div class="no-data">🔍 Scanning for opportunities...<br><span style="font-size: 12px;">Best spreads will appear here</span></div>';
                return;
            }
            
            container.innerHTML = allOpportunities.map((opp, idx) => `
                <div class="opportunity-card" onclick="toggleDetail(${idx})">
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
                            <div class="profit-percent">${opp.spread}%</div>
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
                                <span class="info-label">Volume (24h):</span>
                                <span class="info-value">$${opp.buy_volume.toLocaleString()}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Buy Liquidity:</span>
                                <span class="info-value">$${opp.buy_liquidity.toLocaleString()}</span>
                            </div>
                            <div class="network-box">
                                <div class="network-name">${opp.withdrawal_network}</div>
                                <div>Withdrawal Fee: <span class="fee-value">$${opp.withdrawal_fee}</span></div>
                            </div>
                        </div>
                        
                        <div class="trade-section">
                            <div class="trade-title">2. Sell on ${formatExchange(opp.sell_exchange)}</div>
                            <div class="info-row">
                                <span class="info-label">Highest Bid:</span>
                                <span class="info-value">$${opp.sell_price}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Volume (24h):</span>
                                <span class="info-value">$${opp.sell_volume.toLocaleString()}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Sell Liquidity:</span>
                                <span class="info-value">$${opp.sell_liquidity.toLocaleString()}</span>
                            </div>
                            <div class="network-box">
                                <div class="network-name">${opp.withdrawal_network}</div>
                                <div>Deposit: Free</div>
                            </div>
                        </div>
                        
                        <div class="info-row">
                            <span class="info-label">📊 Gross Spread:</span>
                            <span class="info-value">${opp.spread}%</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">💰 Net Profit (after withdrawal fee):</span>
                            <span class="info-value" style="color: #2ecc71;">${opp.net_profit}% ($${opp.net_profit_usd} on $100)</span>
                        </div>
                        
                        <div class="warning">
                            ⚠️ Double check coin's contract and name on both exchanges before initiating the trade.
                        </div>
                        <div class="time-warning">
                            ${getOpportunityAge(opp.timestamp)}
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        function updateCountdown() {
            const elapsed = (Date.now() - lastUpdateTime) / 1000;
            const remaining = Math.max(0, 30 - elapsed);
            const nextScanEl = document.getElementById('nextScan');
            if (nextScanEl) {
                if (remaining <= 0) {
                    nextScanEl.innerHTML = '🔄 Updating...';
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
            expandedCard = null;
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
    print(f"🚀 ArbiHunt-Style Arbitrage Scanner")
    print(f"{'='*60}")
    print(f"🔐 Using PRIVATE API keys from Render")
    print(f"📊 Spot Market | USDT Pairs Only")
    print(f"💰 Includes withdrawal fees ({DEFAULT_NETWORK})")
    print(f"⏱️  Scan: 5s | Display refresh: 30s")
    print(f"🏦 Exchanges: {', '.join(exchanges.keys())}")
    print(f"🌐 Web UI: http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
