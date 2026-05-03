import os
import asyncio
import logging
import threading
from datetime import datetime
from flask import Flask, render_template_string, jsonify
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# === CONFIG ===
EXCHANGE_IDS = ['mexc', 'kucoin', 'coinex', 'gate', 'bitget']
MIN_SPREAD = 0.3  # % after fees
MIN_VOLUME_USD = 10000
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
REFRESH_SECONDS = 5  # How often to scan

# Fees dict (maker/taker %)
FEES = {
    'mexc': (0.0, 0.2),  # maker, taker
    'kucoin': (0.1, 0.1),
    'coinex': (0.2, 0.2),
    'gate': (0.15, 0.15),
    'bitget': (0.1, 0.1)
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Initialize exchanges
exchanges = {}
for exchange_id in EXCHANGE_IDS:
    try:
        exchange_class = getattr(ccxt, exchange_id)
        exchanges[exchange_id] = exchange_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        
        # Add API keys if available
        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY')
        if api_key:
            exchanges[exchange_id].apiKey = api_key
            exchanges[exchange_id].secret = os.getenv(f'{exchange_id.upper()}_SECRET')
            if exchange_id == 'kucoin':
                exchanges[exchange_id].password = os.getenv(f'{exchange_id.upper()}_PASSWORD')
    except Exception as e:
        logger.error(f"Failed to initialize {exchange_id}: {e}")

# Global storage for opportunities
opportunities = []
last_update = None
initialized = False

# HTML Template for the dashboard
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, refresh=10">
    <title>Crypto Arbitrage Scanner</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .stats {
            display: flex;
            justify-content: space-around;
            padding: 20px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
        }
        
        .stat-card {
            text-align: center;
            padding: 15px;
        }
        
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }
        
        .stat-label {
            color: #666;
            margin-top: 5px;
            font-size: 0.9em;
        }
        
        .update-time {
            text-align: center;
            padding: 10px;
            background: #fff3cd;
            color: #856404;
            font-size: 0.9em;
        }
        
        .table-container {
            overflow-x: auto;
            padding: 20px;
            max-height: 70vh;
            overflow-y: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        
        th {
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #e0e0e0;
        }
        
        tr:hover {
            background: #f5f5f5;
            transition: background 0.3s;
        }
        
        .badge-buy {
            background: #28a745;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: bold;
            display: inline-block;
            font-size: 0.85em;
        }
        
        .badge-sell {
            background: #dc3545;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: bold;
            display: inline-block;
            font-size: 0.85em;
        }
        
        .spread-positive {
            color: #28a745;
            font-weight: bold;
            font-size: 1.1em;
        }
        
        .exchange-badge {
            background: #f8f9fa;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 600;
            display: inline-block;
        }
        
        .profit {
            color: #28a745;
            font-weight: bold;
        }
        
        .liquidity {
            color: #666;
            font-family: monospace;
        }
        
        .timestamp {
            color: #999;
            font-size: 0.85em;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        
        .loading {
            animation: pulse 1.5s ease-in-out infinite;
        }
        
        @media (max-width: 768px) {
            .stat-value { font-size: 1.2em; }
            td, th { padding: 8px; font-size: 11px; }
            .badge-buy, .badge-sell { padding: 2px 6px; font-size: 0.75em; }
        }
    </style>
    <script>
        function refreshData() {
            fetch('/api/opportunities')
                .then(response => response.json())
                .then(data => {
                    if (data.opportunities && data.opportunities.length > 0) {
                        updateTable(data);
                    }
                    document.getElementById('lastUpdate').innerHTML = 
                        'Last updated: ' + new Date().toLocaleTimeString();
                });
        }
        
        function updateTable(data) {
            const tbody = document.getElementById('opportunitiesBody');
            tbody.innerHTML = '';
            
            data.opportunities.forEach(opp => {
                const row = tbody.insertRow();
                row.innerHTML = `
                    <td><span class="badge-buy">BUY</span></td>
                    <td><strong>${opp.buy_exchange.toUpperCase()}</strong></td>
                    <td><strong>${opp.pair}</strong></td>
                    <td><span class="spread-positive">${opp.spread}%</span></td>
                    <td><span class="badge-sell">SELL</span></td>
                    <td><strong>${opp.sell_exchange.toUpperCase()}</strong></td>
                    <td class="liquidity">$${opp.liquidity}</td>
                    <td class="profit">$${opp.profit_100}</td>
                    <td class="timestamp">${opp.age}</td>
                `;
            });
            
            document.getElementById('oppCount').innerHTML = data.opportunities.length;
            document.getElementById('bestSpread').innerHTML = data.best_spread || '0%';
            document.getElementById('exchangesCount').innerHTML = data.exchanges_count;
        }
        
        // Auto-refresh every 5 seconds
        setInterval(refreshData, 5000);
        refreshData();
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Crypto Arbitrage Scanner</h1>
            <p>Real-time arbitrage opportunities across multiple exchanges</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value" id="oppCount">0</div>
                <div class="stat-label">Opportunities Found</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="bestSpread">0%</div>
                <div class="stat-label">Best Spread</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="exchangesCount">{{ exchanges|length }}</div>
                <div class="stat-label">Exchanges</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="pairsCount">{{ pairs_count }}</div>
                <div class="stat-label">Pairs Monitored</div>
            </div>
        </div>
        
        <div class="update-time" id="lastUpdate">
            Waiting for data...
        </div>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Action</th>
                        <th>Exchange</th>
                        <th>Pair</th>
                        <th>Spread</th>
                        <th>Action</th>
                        <th>Exchange</th>
                        <th>Liquidity</th>
                        <th>Profit ($100)</th>
                        <th>Age</th>
                    </tr>
                </thead>
                <tbody id="opportunitiesBody">
                    <tr>
                        <td colspan="9" style="text-align: center; padding: 40px;">
                            <div class="loading">🔍 Scanning for opportunities...</div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

async def load_common_pairs():
    """Load all common USDT pairs across exchanges"""
    global initialized
    common_pairs = {}
    
    try:
        # Load markets for all exchanges
        markets_list = await asyncio.gather(*[
            ex.load_markets() for ex in exchanges.values()
        ])
        
        # Find pairs that exist on multiple exchanges
        for idx, markets in enumerate(markets_list):
            exchange_id = EXCHANGE_IDS[idx]
            usdt_pairs = [symbol for symbol in markets if symbol.endswith('/USDT')]
            
            for pair in usdt_pairs:
                if pair not in common_pairs:
                    common_pairs[pair] = []
                common_pairs[pair].append(exchange_id)
        
        # Keep pairs that exist on at least 2 exchanges
        valid_pairs = [pair for pair, exs in common_pairs.items() if len(exs) >= 2]
        
        logger.info(f"Loaded {len(valid_pairs)} common pairs across {len(EXCHANGE_IDS)} exchanges")
        initialized = True
        return valid_pairs
        
    except Exception as e:
        logger.error(f"Error loading pairs: {e}")
        return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']  # Fallback pairs

async def fetch_all_tickers(pairs):
    """Fetch tickers from all exchanges"""
    tickers_data = {}
    
    for exchange_id, exchange in exchanges.items():
        try:
            if pairs:
                tickers = await exchange.fetch_tickers(pairs)
                tickers_data[exchange_id] = tickers
            else:
                tickers_data[exchange_id] = {}
        except Exception as e:
            logger.error(f"Error fetching from {exchange_id}: {e}")
            tickers_data[exchange_id] = {}
    
    return tickers_data

def find_arbitrage_opportunities(tickers_data, pairs):
    """Find arbitrage opportunities from ticker data"""
    opportunities_list = []
    
    for pair in pairs:
        pair_data = {}
        
        # Collect data for this pair from all exchanges
        for exchange_id, tickers in tickers_data.items():
            if pair in tickers:
                ticker = tickers[pair]
                if ticker.get('last') and ticker.get('quoteVolume', 0) >= MIN_VOLUME_USD:
                    pair_data[exchange_id] = {
                        'price': ticker['last'],
                        'volume': ticker.get('quoteVolume', 0),
                        'bid': ticker.get('bid', ticker['last']),
                        'ask': ticker.get('ask', ticker['last'])
                    }
        
        # Need at least 2 exchanges with data
        if len(pair_data) < 2:
            continue
        
        # Find best buy (lowest ask) and best sell (highest bid)
        buy_exchange = min(pair_data.keys(), key=lambda x: pair_data[x]['ask'])
        sell_exchange = max(pair_data.keys(), key=lambda x: pair_data[x]['bid'])
        
        buy_price = pair_data[buy_exchange]['ask']
        sell_price = pair_data[sell_exchange]['bid']
        
        if buy_price >= sell_price:
            continue
        
        # Calculate fees
        buy_fee = FEES.get(buy_exchange, (0.1, 0.1))[1] / 100  # taker fee
        sell_fee = FEES.get(sell_exchange, (0.1, 0.1))[0] / 100  # maker fee
        
        # Net spread after fees
        buy_with_fee = buy_price * (1 + buy_fee)
        sell_with_fee = sell_price * (1 - sell_fee)
        net_spread = ((sell_with_fee - buy_with_fee) / buy_with_fee) * 100
        
        if net_spread >= MIN_SPREAD:
            liquidity = min(pair_data[buy_exchange]['volume'], pair_data[sell_exchange]['volume'])
            profit_100 = 100 * net_spread / 100
            
            opportunities_list.append({
                'pair': pair,
                'buy_exchange': buy_exchange,
                'sell_exchange': sell_exchange,
                'buy_price': buy_price,
                'sell_price': sell_price,
                'spread': round(net_spread, 2),
                'liquidity': round(liquidity),
                'profit_100': round(profit_100, 2),
                'timestamp': datetime.now(),
                'age': 'just now'
            })
    
    # Sort by spread (highest first)
    opportunities_list.sort(key=lambda x: x['spread'], reverse=True)
    return opportunities_list[:20]  # Keep top 20

async def scanner_loop():
    """Main scanner loop"""
    global opportunities, last_update
    
    logger.info("Loading common trading pairs...")
    pairs = await load_common_pairs()
    logger.info(f"Monitoring {len(pairs)} pairs for arbitrage")
    
    while True:
        try:
            start_time = datetime.now()
            
            # Fetch tickers from all exchanges
            tickers_data = await fetch_all_tickers(pairs)
            
            # Find arbitrage opportunities
            new_opps = find_arbitrage_opportunities(tickers_data, pairs)
            
            if new_opps:
                opportunities = new_opps
                last_update = datetime.now()
                
                # Log top opportunity
                top = new_opps[0]
                logger.info(f"🎯 Top arb: {top['pair']} | {top['spread']}% | "
                          f"Buy {top['buy_exchange']} @ ${top['buy_price']:.4f} → "
                          f"Sell {top['sell_exchange']} @ ${top['sell_price']:.4f}")
                
                # Send Telegram alert for top 3 opportunities
                if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                    for opp in new_opps[:3]:
                        await send_telegram_alert(opp)
            
            # Calculate scan time and wait
            scan_time = (datetime.now() - start_time).total_seconds()
            wait_time = max(0, REFRESH_SECONDS - scan_time)
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            logger.error(f"Scanner loop error: {e}")
            await asyncio.sleep(5)

async def send_telegram_alert(opp):
    """Send Telegram alert for an opportunity"""
    try:
        import aiohttp
        msg = (f"🚨 Arbitrage Opportunity!\n"
               f"Pair: {opp['pair']}\n"
               f"BUY: {opp['buy_exchange'].upper()} @ ${opp['buy_price']:.4f}\n"
               f"SELL: {opp['sell_exchange'].upper()} @ ${opp['sell_price']:.4f}\n"
               f"Spread: +{opp['spread']}%\n"
               f"Profit on $100: ${opp['profit_100']}\n"
               f"Liquidity: ${opp['liquidity']}")
        
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML"
            })
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# Flask routes
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, 
                                 exchanges=EXCHANGE_IDS,
                                 pairs_count=len(opportunities) * 10 if opportunities else 0)

@app.route('/api/opportunities')
def api_opportunities():
    """API endpoint for opportunities"""
    if opportunities:
        # Update ages
        for opp in opportunities:
            if last_update:
                seconds_ago = (datetime.now() - last_update).seconds
                if seconds_ago < 60:
                    opp['age'] = f"{seconds_ago} seconds ago"
                else:
                    opp['age'] = f"{seconds_ago // 60} minutes ago"
        
        best_spread = f"{opportunities[0]['spread']}%" if opportunities else "0%"
        
        return jsonify({
            'opportunities': opportunities,
            'best_spread': best_spread,
            'exchanges_count': len(EXCHANGE_IDS),
            'last_update': last_update.isoformat() if last_update else None,
            'total_opportunities': len(opportunities)
        })
    else:
        return jsonify({
            'opportunities': [],
            'best_spread': '0%',
            'exchanges_count': len(EXCHANGE_IDS),
            'last_update': None,
            'total_opportunities': 0
        })

@app.route('/health')
def health():
    return {
        "status": "ok" if initialized else "initializing",
        "exchanges": EXCHANGE_IDS,
        "opportunities_found": len(opportunities),
        "last_update": last_update.isoformat() if last_update else None
    }

@app.route('/exchanges')
def list_exchanges():
    return jsonify({
        'exchanges': EXCHANGE_IDS,
        'status': {ex_id: 'connected' for ex_id in EXCHANGE_IDS}
    })

def run_scanner_thread():
    """Run scanner in a separate thread"""
    asyncio.run(scanner_loop())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    # Start scanner in background thread
    scanner_thread = threading.Thread(target=run_scanner_thread, daemon=True)
    scanner_thread.start()
    
    logger.info(f"🚀 Arbitrage Scanner starting on port {port}")
    logger.info(f"📊 Monitoring exchanges: {', '.join(EXCHANGE_IDS)}")
    logger.info(f"🎯 Minimum spread target: {MIN_SPREAD}%")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
