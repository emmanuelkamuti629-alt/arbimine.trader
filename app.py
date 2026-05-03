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
MIN_VOLUME_USD = 5000  # Lowered to catch more opportunities
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
REFRESH_SECONDS = 3  # Faster scanning

# Fees dict (maker/taker %)
FEES = {
    'mexc': (0.0, 0.2),
    'kucoin': (0.1, 0.1),
    'coinex': (0.2, 0.2),
    'gate': (0.15, 0.15),
    'bitget': (0.1, 0.1)
}

# Complete coin list (all from your request)
COINS = [
    # Major Coins
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'AVAX', 'TRX', 'MATIC',
    'LINK', 'DOT', 'LTC', 'BCH', 'ATOM', 'ETC', 'XLM', 'NEAR', 'FIL', 'ICP',
    'APT', 'ARB', 'OP', 'AAVE', 'UNI', 'INJ', 'SUI', 'SEI', 'STX', 'RNDR',
    'GRT', 'HBAR', 'QNT', 'ALGO', 'VET', 'THETA', 'IMX', 'RUNE', 'KAS', 'GALA',
    'ENJ', 'SAND', 'MANA', 'CRV', 'LDO', 'DYDX', 'MKR', 'COMP',
    
    # Layer 1 & Interoperability
    'ZEC', 'DASH', 'XTZ', 'ZRX', 'ONE', 'EGLD', 'FLOW', 'CHZ', 'HOT', 'IOTA',
    'WAVES', 'KAVA', 'KSM', 'SNX', 'ZIL', 'CELR', 'ROSE', 'AR', 'JASMY', 'FTM',
    'TWT', 'CFX', 'BLUR', 'GMX', 'LRC', 'LPT', 'SUSHI', 'BAL', 'YFI', '1INCH',
    'UMA', 'DGB', 'SC', 'SRM', 'OCEAN', 'API3', 'CELO', 'NKN', 'PERP', 'HNT',
    'MINA', 'AGIX', 'FET', 'RLC', 'CTSI', 'AUDIO', 'LUNA2', 'XNO', 'STORJ', 'CTK',
    'DODO', 'ALPHA', 'ILV', 'MAGIC', 'VOXEL', 'LOOKS', 'XEM', 'KLAY', 'ASTR',
    'LINA', 'PERL', 'BORA', 'BSW', 'TLM', 'REQ', 'MDT', 'ERN', 'BICO', 'C98',
    'GLMR', 'MOVR', 'KNC', 'RAY', 'ALPINE', 'CITY', 'SANTOS', 'HIGH',
    
    # Meme & Community
    'DOGS', 'NOT', 'HMSTR', 'PIXEL', 'WIF', 'BONK', 'FLOKI', 'TURBO', 'BABYDOGE',
    'ELON', 'MEME', 'CAT', '1000SATS', 'LADYS', 'ORDI', 'SATS', 'RATS', 'PEPE2',
    'SHIB2', 'BRISE', 'EVER', 'XEC', 'XCN', 'LUNC', 'USTC', 'CEEK', 'MLN', 'FXS',
    'SPELL', 'REI', 'POND', 'PDA', 'ONG', 'ARKM', 'PENDLE', 'PRT', 'TRB', 'LIT',
    'FIO', 'COTI', 'XVS', 'FORTH', 'RIF', 'TRU', 'STRAX', 'NMR', 'POLYX', 'POLS',
    'AKRO', 'OGN', 'OXT', 'RARE', 'GHST', 'BETA', 'QUICK', 'FOR', 'AST', 'BNT',
    'PHA', 'ID', 'NFP', 'ALT', 'ACE', 'SAGA', 'ZKF', 'ZENT', 'MANTA', 'DYM',
    'JTO', 'JUP', 'WLD', 'PYTH', 'TIA', 'BLAST', 'ZRO', 'STRK', 'AI16Z', 'BOME',
    'METIS', 'RDNT', 'TOKE', 'TORN', 'MBOX', 'SUPER', 'XYO', 'DENT', 'WIN', 'JST',
    'SUN', 'BTT', 'TRIBE', 'CVX', 'KEEP', 'BADGER', 'NEXO', 'LQTY', 'YGG', 'AXS',
    'ENS', 'PEOPLE', 'MAGIC'
]

# Generate USDT pairs from coins
TARGET_PAIRS = [f"{coin}/USDT" for coin in COINS]

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Initialize exchanges
exchanges = {}
for exchange_id in EXCHANGE_IDS:
    try:
        exchange_class = getattr(ccxt, exchange_id)
        exchanges[exchange_id] = exchange_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'rateLimit': 1200,  # Respect exchange rate limits
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

# Global storage
opportunities = []
all_pairs = []
last_update = None
initialized = False

# HTML Template (same as before, but updated)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, refresh=10">
    <title>Crypto Arbitrage Scanner - 200+ Pairs</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1600px;
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
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { opacity: 0.9; }
        .stats {
            display: flex;
            justify-content: space-around;
            padding: 20px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
            flex-wrap: wrap;
        }
        .stat-card {
            text-align: center;
            padding: 15px;
            min-width: 120px;
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
            font-size: 13px;
        }
        th {
            background: #667eea;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        td {
            padding: 10px 12px;
            border-bottom: 1px solid #e0e0e0;
        }
        tr:hover { background: #f5f5f5; transition: background 0.3s; }
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
        .spread-positive { color: #28a745; font-weight: bold; font-size: 1.1em; }
        .exchange-badge {
            background: #f8f9fa;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 600;
            display: inline-block;
        }
        .profit { color: #28a745; font-weight: bold; }
        .liquidity { color: #666; font-family: monospace; }
        .timestamp { color: #999; font-size: 0.85em; }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        .loading { animation: pulse 1.5s ease-in-out infinite; text-align: center; padding: 40px; }
        @media (max-width: 768px) {
            .stat-value { font-size: 1.2em; }
            td, th { padding: 6px; font-size: 10px; }
            .badge-buy, .badge-sell { padding: 2px 6px; font-size: 0.7em; }
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
                        'Last updated: ' + new Date().toLocaleTimeString() + ' | Scanning ' + data.total_pairs + ' pairs';
                });
        }
        
        function updateTable(data) {
            const tbody = document.getElementById('opportunitiesBody');
            tbody.innerHTML = '';
            
            data.opportunities.forEach(opp => {
                const row = tbody.insertRow();
                row.innerHTML = `
                    <td><span class="badge-buy">BUY</span></td>
                    <td><span class="exchange-badge">${opp.buy_exchange.toUpperCase()}</span></td>
                    <td><strong>${opp.pair}</strong></td>
                    <td class="spread-positive">+${opp.spread}%</td>
                    <td><span class="badge-sell">SELL</span></td>
                    <td><span class="exchange-badge">${opp.sell_exchange.toUpperCase()}</span></td>
                    <td class="liquidity">$${opp.liquidity.toLocaleString()}</td>
                    <td class="profit">$${opp.profit_100}</td>
                    <td class="timestamp">${opp.age}</td>
                `;
            });
            
            document.getElementById('oppCount').innerHTML = data.opportunities.length;
            document.getElementById('bestSpread').innerHTML = data.best_spread || '0%';
            document.getElementById('exchangesCount').innerHTML = data.exchanges_count;
            document.getElementById('pairsCount').innerHTML = data.total_pairs;
        }
        
        setInterval(refreshData, 3000);
        refreshData();
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Crypto Arbitrage Scanner</h1>
            <p>Real-time arbitrage opportunities across 5 exchanges | 200+ Trading Pairs</p>
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
                <div class="stat-value" id="exchangesCount">0</div>
                <div class="stat-label">Exchanges</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="pairsCount">0</div>
                <div class="stat-label">Pairs Monitored</div>
            </div>
        </div>
        
        <div class="update-time" id="lastUpdate">
            Initializing scanner...
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
                    <tr><td colspan="9"><div class="loading">🔍 Scanning for opportunities across 200+ pairs...</div></td></tr>
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

async def load_markets():
    """Load markets and verify which pairs are available"""
    global all_pairs, initialized
    
    valid_pairs = set()
    
    try:
        # Load markets from all exchanges in parallel
        markets_list = await asyncio.gather(*[
            ex.load_markets() for ex in exchanges.values()
        ], return_exceptions=True)
        
        # Find which of our target pairs are available on each exchange
        for idx, markets in enumerate(markets_list):
            if isinstance(markets, Exception):
                logger.error(f"Failed to load markets for {EXCHANGE_IDS[idx]}: {markets}")
                continue
                
            exchange_id = EXCHANGE_IDS[idx]
            available_pairs = [pair for pair in TARGET_PAIRS if pair in markets]
            logger.info(f"{exchange_id}: {len(available_pairs)}/{len(TARGET_PAIRS)} pairs available")
            
            for pair in available_pairs:
                valid_pairs.add(pair)
        
        all_pairs = list(valid_pairs)
        initialized = True
        
        logger.info(f"✅ Loaded {len(all_pairs)} common pairs across {len(EXCHANGE_IDS)} exchanges")
        logger.info(f"Sample pairs: {all_pairs[:20]}")
        
        return all_pairs
        
    except Exception as e:
        logger.error(f"Error loading markets: {e}")
        # Fallback to a subset of major pairs
        all_pairs = TARGET_PAIRS[:50]
        initialized = True
        return all_pairs

async def fetch_all_tickers(pairs_chunk):
    """Fetch tickers from all exchanges with chunking for performance"""
    tickers_data = {}
    
    for exchange_id, exchange in exchanges.items():
        try:
            # Fetch only specified pairs to reduce bandwidth
            tickers = await exchange.fetch_tickers(pairs_chunk)
            tickers_data[exchange_id] = tickers
        except Exception as e:
            logger.debug(f"Error fetching from {exchange_id}: {e}")
            tickers_data[exchange_id] = {}
    
    return tickers_data

def find_arbitrage_opportunities(tickers_data):
    """Find arbitrage opportunities from ticker data"""
    opportunities_list = []
    
    for pair in all_pairs:
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
                        'ask': ticker.get('ask', ticker['last']),
                        'percentage': ticker.get('percentage', 0)
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
        buy_fee = FEES.get(buy_exchange, (0.1, 0.1))[1] / 100
        sell_fee = FEES.get(sell_exchange, (0.1, 0.1))[0] / 100
        
        # Net spread after fees
        buy_with_fee = buy_price * (1 + buy_fee)
        sell_with_fee = sell_price * (1 - sell_fee)
        net_spread = ((sell_with_fee - buy_with_fee) / buy_with_fee) * 100
        
        if net_spread >= MIN_SPREAD:
            liquidity = min(pair_data[buy_exchange]['volume'], pair_data[sell_exchange]['volume'])
            profit_100 = round(100 * net_spread / 100, 2)
            
            opportunities_list.append({
                'pair': pair,
                'buy_exchange': buy_exchange,
                'sell_exchange': sell_exchange,
                'buy_price': round(buy_price, 8),
                'sell_price': round(sell_price, 8),
                'spread': round(net_spread, 2),
                'liquidity': round(liquidity),
                'profit_100': profit_100,
                'timestamp': datetime.now(),
                'age': 'just now'
            })
            
            # Log opportunity
            logger.info(f"🎯 ARB: {pair} | +{net_spread:.2f}% | Buy {buy_exchange} @ ${buy_price:.4f} → Sell {sell_exchange} @ ${sell_price:.4f} | Profit: ${profit_100}/$100")
    
    # Sort by spread (highest first)
    opportunities_list.sort(key=lambda x: x['spread'], reverse=True)
    return opportunities_list[:20]

async def scanner_loop():
    """Main scanner loop"""
    global opportunities, last_update
    
    logger.info("Loading markets and verifying available pairs...")
    await load_markets()
    logger.info(f"Starting scanner with {len(all_pairs)} pairs")
    
    # Process pairs in chunks to avoid rate limits
    chunk_size = 50
    
    while True:
        try:
            start_time = datetime.now()
            all_opportunities = []
            
            # Process pairs in chunks
            for i in range(0, len(all_pairs), chunk_size):
                chunk = all_pairs[i:i+chunk_size]
                
                # Fetch tickers for this chunk
                tickers_data = await fetch_all_tickers(chunk)
                
                # Find opportunities in this chunk
                chunk_opps = find_arbitrage_opportunities(tickers_data)
                all_opportunities.extend(chunk_opps)
            
            # Sort all opportunities
            all_opportunities.sort(key=lambda x: x['spread'], reverse=True)
            opportunities = all_opportunities[:20]
            last_update = datetime.now()
            
            # Send Telegram alerts for top opportunities
            if opportunities and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                for opp in opportunities[:3]:
                    await send_telegram_alert(opp)
            
            # Calculate scan time and wait
            scan_time = (datetime.now() - start_time).total_seconds()
            wait_time = max(0, REFRESH_SECONDS - scan_time)
            
            logger.info(f"Scan complete: {len(opportunities)} opportunities found in {scan_time:.1f}s")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            logger.error(f"Scanner loop error: {e}")
            await asyncio.sleep(5)

async def send_telegram_alert(opp):
    """Send Telegram alert for an opportunity"""
    try:
        import aiohttp
        msg = (f"🚨 **Arbitrage Opportunity!**\n\n"
               f"📊 **Pair:** {opp['pair']}\n"
               f"🟢 **BUY:** {opp['buy_exchange'].upper()} @ ${opp['buy_price']:.6f}\n"
               f"🔴 **SELL:** {opp['sell_exchange'].upper()} @ ${opp['sell_price']:.6f}\n"
               f"📈 **Spread:** +{opp['spread']}%\n"
               f"💰 **Profit on $100:** ${opp['profit_100']}\n"
               f"💧 **Liquidity:** ${opp['liquidity']:,}\n"
               f"⏱️ **Age:** {opp['age']}")
        
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown"
            })
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# Flask routes
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/opportunities')
def api_opportunities():
    """API endpoint for opportunities"""
    if opportunities and last_update:
        # Update ages
        for opp in opportunities:
            seconds_ago = (datetime.now() - last_update).seconds
            if seconds_ago < 60:
                opp['age'] = f"{seconds_ago}s ago"
            else:
                opp['age'] = f"{seconds_ago // 60}m ago"
        
        best_spread = f"+{opportunities[0]['spread']}%" if opportunities else "0%"
        
        return jsonify({
            'opportunities': opportunities,
            'best_spread': best_spread,
            'exchanges_count': len(EXCHANGE_IDS),
            'total_pairs': len(all_pairs),
            'last_update': last_update.isoformat()
        })
    else:
        return jsonify({
            'opportunities': [],
            'best_spread': '0%',
            'exchanges_count': len(EXCHANGE_IDS),
            'total_pairs': len(all_pairs),
            'last_update': None
        })

@app.route('/health')
def health():
    return {
        "status": "ok" if initialized else "initializing",
        "exchanges": EXCHANGE_IDS,
        "opportunities_found": len(opportunities),
        "pairs_monitored": len(all_pairs),
        "last_update": last_update.isoformat() if last_update else None
    }

@app.route('/pairs')
def list_pairs():
    """List all monitored pairs"""
    return jsonify({
        'total_pairs': len(all_pairs),
        'pairs': all_pairs[:100],  # Return first 100
        'sample': all_pairs[:20]
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
    logger.info(f"📊 Monitoring {len(EXCHANGE_IDS)} exchanges: {', '.join(EXCHANGE_IDS)}")
    logger.info(f"🎯 Tracking {len(TARGET_PAIRS)} potential pairs")
    logger.info(f"💹 Minimum spread target: {MIN_SPREAD}%")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
