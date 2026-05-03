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

# ================= CONFIG =================
EXCHANGE_IDS = ['mexc', 'kucoin', 'coinex', 'gate', 'bitget']
MIN_SPREAD = 0.3
MIN_VOLUME_USD = 3000  # Lowered for more opportunities
REFRESH_SECONDS = 4

FEES = {
    'mexc': (0.0, 0.2),
    'kucoin': (0.1, 0.1),
    'coinex': (0.2, 0.2),
    'gate': (0.15, 0.15),
    'bitget': (0.1, 0.1)
}

# ================= COMPLETE COIN LIST (350+ COINS) =================
COINS = [
    # ================= TIER 1 =================
    'BTC', 'ETH', 'BNB', 'SOL', 'TON', 'SUI', 'XLM', 'XMR',
    
    # ================= LARGE CAPS =================
    'DOGE', 'AVAX', 'TRX', 'ADA', 'MATIC', 'POL', 'LINK', 'DOT', 'XRP', 'ICP', 'HBAR', 'SHIB',
    'LTC', 'ETC', 'BCH', 'NEAR', 'APT', 'ARB', 'OP', 'INJ', 'RUNE', 'FET', 'GRT', 'UNI',
    'AAVE', 'MKR', 'SNX', 'CRV', 'SUSHI', '1INCH', 'COMP', 'YFI', 'BAL', 'GMX', 'WOO',
    
    # ================= MID / INFRA =================
    'IMX', 'AR', 'EGLD', 'FLUX', 'ALGO', 'KCS', 'ZEC', 'STX', 'VET', 'FIL', 'RNDR',
    'ROSE', 'STG', 'RLB', 'RDNT', 'JUP', 'PYR', 'JTO', 'WLD', 'ZRO', 'TIA', 'SEI',
    'AGIX', 'AKT', 'MINA', 'FLOW', 'ZIL', 'QTUM', 'ENJ', 'CHZ', 'BAT', 'MANA', 'SAND',
    'GALA', 'AXS', 'ILV', 'YGG', 'DYDX', 'LDO', 'PENDLE', 'JOE', 'CELR', 'IOTX', 'CKB',
    'XVS', 'XEM', 'DGB', 'RVN', 'SC', 'NKN', 'SYS', 'RLC', 'OMG', 'ZRX', 'BAND', 'OXT',
    'CTSI', 'PERP', 'RIF', 'LRC', 'SKL', 'CVC', 'WAXP', 'BNT', 'REN', 'UMA', 'NMR', 'TRU',
    'IDEX', 'HFT', 'BIGTIME', 'FLM', 'ALPHA', 'TLM', 'OGN', 'CHR', 'REEF', 'POLYX', 'DAR',
    'LPT', 'MX', 'HOT', 'ANKR', 'UTK', 'API3', 'CELO', 'KDA', 'GLMR', 'MOVR', 'SXP', 'KAVA',
    
    # ================= CEX + L1 ECOSYSTEM =================
    'OKB', 'GT', 'HT', 'LEO', 'KLAY', 'XTZ', 'ATOM', 'KSM', 'WAVES', 'IOTA', 'NEO', 'ONT',
    'NANO', 'DASH', 'XEC', 'THETA', 'TFUEL', 'FTM', 'CFX', 'APTOS', 'SUIX', 'STRK', 'METIS',
    
    # ================= DEFI / AI / TREND =================
    'PEPE', 'BONK', 'WIF', 'POPCAT', 'MEW', 'BOME', 'MOG', 'NEIRO', 'BRETT', 'FLOKI',
    'TURBO', 'WEN', 'SLERF', 'PNUT', 'TRUMP', 'MELANIA', 'BABYDOGE', 'SHIBAINU',
    'DOGWIFHAT', 'TOSHI', 'PURR', 'NYAN', 'CAT', 'MASK', 
    
    # ================= ADDITIONAL MIDCAP EXPANSION =================
    'STMX', 'BICO', 'ACH', 'LINA', 'HARD', 'IRIS', 'LTO', 'MDX', 'NULS', 'PHA',
    'QNT', 'KNC', 'DODO', 'BEL', 'DATA', 'SFP', 'LIT', 'ARPA', 'GTC',
    'CTK', 'COS', 'DUSK', 'FIS', 'FORTH', 'FXS', 'MAGIC', 'RAD',
    'ALICE', 'SLP', 'GMT', 'GST', 'VOXEL', 'XETA', 'HOOK', 'ID',
    'ASTR', 'WOO', 'RPL', 'LDO', 'SD', 'STG', 'LQTY', 'POND',
    
    # ================= MEME / HIGH VOLATILITY =================
    'PEOPLE', 'WOJAK', 'MILADY', 'DOGS', 'CATE', 'ELON', 'SHIB2', 'PEPE2', 'MOON', 'STAR',
    'X', 'AIDOGE', 'BULL', 'BEAR', 'FLOKICEO', 'CHEEMS', 'KISHU', 'HOGE', 'AKITA', 'SAFEMOON',
    
    # ================= ADDITIONAL (from your original list) =================
    'QNT', 'ALGO', 'VET', 'THETA', 'IMX', 'RUNE', 'KAS', 'GALA', 'ENJ', 'SAND', 'MANA',
    'CRV', 'LDO', 'DYDX', 'MKR', 'COMP', 'ZEC', 'DASH', 'XTZ', 'ZRX', 'ONE', 'EGLD',
    'FLOW', 'CHZ', 'HOT', 'IOTA', 'WAVES', 'KAVA', 'KSM', 'SNX', 'ZIL', 'CELR', 'ROSE',
    'AR', 'JASMY', 'FTM', 'TWT', 'CFX', 'BLUR', 'GMX', 'LRC', 'LPT', 'SUSHI', 'BAL',
    'YFI', 'UMA', 'DGB', 'SC', 'SRM', 'OCEAN', 'API3', 'CELO', 'NKN', 'PERP', 'HNT',
    'MINA', 'AGIX', 'FET', 'RLC', 'CTSI', 'AUDIO', 'LUNA2', 'XNO', 'STORJ', 'CTK',
    'DODO', 'ALPHA', 'ILV', 'MAGIC', 'VOXEL', 'LOOKS', 'XEM', 'KLAY', 'ASTR',
    'LINA', 'PERL', 'BORA', 'BSW', 'TLM', 'REQ', 'MDT', 'ERN', 'BICO', 'C98',
    'GLMR', 'MOVR', 'KNC', 'RAY', 'HIGH', 'NOT', 'PIXEL', 'WIF', 'BONK', 'FLOKI',
    'TURBO', 'BABYDOGE', 'ELON', 'MEME', 'CAT', '1000SATS', 'LADYS', 'ORDI', 'SATS',
    'RATS', 'PEPE2', 'SHIB2', 'BRISE', 'EVER', 'XEC', 'XCN', 'LUNC', 'USTC', 'CEEK',
    'MLN', 'FXS', 'SPELL', 'REI', 'POND', 'PDA', 'ONG', 'ARKM', 'PENDLE', 'TRB',
    'LIT', 'FIO', 'COTI', 'XVS', 'FORTH', 'RIF', 'TRU', 'STRAX', 'NMR', 'POLYX',
    'POLS', 'AKRO', 'OGN', 'OXT', 'RARE', 'GHST', 'BETA', 'QUICK', 'FOR', 'AST',
    'BNT', 'PHA', 'ID', 'NFP', 'ALT', 'ACE', 'SAGA', 'ZKF', 'ZENT', 'MANTA',
    'DYM', 'JTO', 'JUP', 'WLD', 'PYTH', 'TIA', 'BLAST', 'ZRO', 'STRK', 'AI16Z',
    'BOME', 'METIS', 'RDNT', 'TOKE', 'TORN', 'MBOX', 'SUPER', 'XYO', 'DENT',
    'WIN', 'JST', 'SUN', 'BTT', 'TRIBE', 'CVX', 'KEEP', 'BADGER', 'NEXO',
    'LQTY', 'YGG', 'AXS', 'ENS', 'PEOPLE', 'MAGIC'
]

# Remove duplicates and create USDT pairs
unique_coins = list(dict.fromkeys(COINS))  # Preserve order while removing duplicates
TARGET_PAIRS = [f"{coin}/USDT" for coin in unique_coins]

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ARB")

# ================= GLOBAL STATE =================
exchanges = {}
opportunities = []
all_pairs = []
exchange_available_pairs = {}
last_update = None
initialized = False
scan_count = 0

lock = threading.Lock()

# ================= INIT EXCHANGES =================
logger.info(f"Initializing {len(EXCHANGE_IDS)} exchanges...")
logger.info(f"Targeting {len(TARGET_PAIRS)} potential trading pairs")

for ex_id in EXCHANGE_IDS:
    try:
        ex_class = getattr(ccxt, ex_id)
        exchanges[ex_id] = ex_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'rateLimit': 1000,
        })
        logger.info(f"✓ Loaded exchange: {ex_id}")
    except Exception as e:
        logger.error(f"✗ Exchange init failed {ex_id}: {e}")
        exchanges[ex_id] = None

# ================= MARKET LOADER =================
async def load_markets():
    global all_pairs, exchange_available_pairs, initialized
    
    logger.info("Loading markets from all exchanges...")
    
    # Filter out None exchanges
    active_exchanges = {k: v for k, v in exchanges.items() if v is not None}
    if not active_exchanges:
        logger.error("No active exchanges available!")
        all_pairs = TARGET_PAIRS[:50]  # Fallback to first 50
        initialized = True
        return
    
    # Load markets in parallel
    tasks = {k: ex.load_markets() for k, ex in active_exchanges.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    valid_pairs = set()
    
    for ex_id, markets in zip(tasks.keys(), results):
        if isinstance(markets, Exception):
            logger.error(f"{ex_id} market load failed: {markets}")
            exchange_available_pairs[ex_id] = set()
            continue
        
        # Find which of our target pairs exist on this exchange
        available = [p for p in TARGET_PAIRS if p in markets]
        exchange_available_pairs[ex_id] = set(available)
        
        logger.info(f"{ex_id}: {len(available)}/{len(TARGET_PAIRS)} pairs available")
        
        for pair in available:
            valid_pairs.add(pair)
    
    all_pairs = list(valid_pairs)
    
    if not all_pairs:
        # Fallback to major pairs
        major_coins = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'MATIC', 'LINK']
        all_pairs = [f"{c}/USDT" for c in major_coins]
        logger.warning(f"No pairs found, using fallback: {len(all_pairs)} major pairs")
    else:
        logger.info(f"✅ Found {len(all_pairs)} common pairs across exchanges")
        
        # Show distribution
        pair_counts = {}
        for ex_pairs in exchange_available_pairs.values():
            for pair in ex_pairs:
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
        
        # Pairs available on most exchanges
        high_coverage = [p for p, count in pair_counts.items() if count >= 3]
        logger.info(f"📊 Pairs on 3+ exchanges: {len(high_coverage)}")
        logger.info(f"📊 Sample pairs: {all_pairs[:15]}")
    
    initialized = True

# ================= FETCH TICKERS =================
async def fetch_tickers(pairs):
    if not pairs:
        return {}
    
    # Filter to only pairs that exist on each exchange
    tasks = {}
    for ex_id, ex in exchanges.items():
        if ex and exchange_available_pairs.get(ex_id):
            # Only fetch pairs that exist on this exchange
            pairs_to_fetch = [p for p in pairs if p in exchange_available_pairs.get(ex_id, set())]
            if pairs_to_fetch:
                # Fetch in chunks of 100 to avoid rate limits
                tasks[ex_id] = fetch_exchange_tickers_chunked(ex, pairs_to_fetch)
    
    if not tasks:
        return {}
    
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    data = {}
    for ex_id, res in zip(tasks.keys(), results):
        if isinstance(res, Exception):
            logger.debug(f"{ex_id} fetch error: {res}")
            data[ex_id] = {}
        else:
            data[ex_id] = res
    
    return data

async def fetch_exchange_tickers_chunked(exchange, pairs, chunk_size=50):
    """Fetch tickers in chunks to avoid rate limits"""
    all_tickers = {}
    
    for i in range(0, len(pairs), chunk_size):
        chunk = pairs[i:i+chunk_size]
        try:
            tickers = await exchange.fetch_tickers(chunk)
            all_tickers.update(tickers)
            await asyncio.sleep(0.1)  # Small delay between chunks
        except Exception as e:
            logger.debug(f"Chunk fetch error: {e}")
    
    return all_tickers

# ================= ARB ENGINE =================
def find_opportunities(tickers):
    global scan_count
    ops = []
    scan_count += 1
    
    for pair in all_pairs:
        data = {}
        
        for ex_id, tks in tickers.items():
            if pair in tks and tks[pair]:
                t = tks[pair]
                
                # Get best prices
                ask_price = t.get('ask')
                bid_price = t.get('bid')
                
                # Fallback to last price if needed
                if not ask_price or not bid_price:
                    last_price = t.get('last')
                    if last_price:
                        if not ask_price:
                            ask_price = last_price
                        if not bid_price:
                            bid_price = last_price
                
                if not ask_price or not bid_price or ask_price <= 0 or bid_price <= 0:
                    continue
                
                # Check volume
                vol = t.get('quoteVolume') or t.get('baseVolume') or 0
                if vol < MIN_VOLUME_USD:
                    continue
                
                data[ex_id] = {
                    'ask': ask_price,
                    'bid': bid_price,
                    'vol': vol,
                    'last': t.get('last', ask_price)
                }
        
        if len(data) < 2:
            continue
        
        # Find best buy (lowest ask) and best sell (highest bid)
        buy_ex = min(data, key=lambda x: data[x]['ask'])
        sell_ex = max(data, key=lambda x: data[x]['bid'])
        
        buy_price = data[buy_ex]['ask']
        sell_price = data[sell_ex]['bid']
        
        if buy_price >= sell_price:
            continue
        
        # Calculate fees
        buy_fee = FEES.get(buy_ex, (0.1, 0.1))[1] / 100
        sell_fee = FEES.get(sell_ex, (0.1, 0.1))[1] / 100
        
        buy_adj = buy_price * (1 + buy_fee)
        sell_adj = sell_price * (1 - sell_fee)
        
        net_spread = ((sell_adj - buy_adj) / buy_adj) * 100
        
        if net_spread < MIN_SPREAD:
            continue
        
        # Calculate liquidity (use minimum volume of both exchanges)
        liquidity = min(data[buy_ex]['vol'], data[sell_ex]['vol'])
        profit_100 = round(100 * net_spread / 100, 2)
        
        ops.append({
            "pair": pair,
            "buy_exchange": buy_ex,
            "sell_exchange": sell_ex,
            "buy_price": round(buy_price, 8),
            "sell_price": round(sell_price, 8),
            "spread": round(net_spread, 2),
            "liquidity": round(liquidity),
            "profit_100": profit_100,
            "buy_fee_pct": round(buy_fee * 100, 2),
            "sell_fee_pct": round(sell_fee * 100, 2)
        })
    
    return sorted(ops, key=lambda x: x['spread'], reverse=True)[:30]

# ================= SCANNER LOOP =================
async def scanner():
    global opportunities, last_update, initialized
    
    await load_markets()
    
    logger.info(f"🚀 Scanner started with {len(all_pairs)} active pairs")
    logger.info(f"💹 Minimum spread target: {MIN_SPREAD}%")
    logger.info(f"💰 Minimum volume filter: ${MIN_VOLUME_USD:,}")
    
    while True:
        try:
            start_time = datetime.now()
            
            tickers = await fetch_tickers(all_pairs)
            ops = find_opportunities(tickers)
            
            with lock:
                opportunities = ops
                last_update = datetime.now()
            
            if ops:
                top = ops[0]
                logger.info(f"🎯 [{len(ops)} opportunities] Best: {top['pair']} +{top['spread']}% | "
                          f"Buy {top['buy_exchange']} @ ${top['buy_price']:.6f} → "
                          f"Sell {top['sell_exchange']} @ ${top['sell_price']:.6f} | "
                          f"Profit: ${top['profit_100']}/$100")
            else:
                # Only log every 10 scans to reduce noise
                if scan_count % 10 == 0:
                    logger.info(f"🔍 Scan {scan_count}: No opportunities found above {MIN_SPREAD}%")
            
            # Maintain consistent scan interval
            elapsed = (datetime.now() - start_time).total_seconds()
            await asyncio.sleep(max(0.5, REFRESH_SECONDS - elapsed))
            
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(5)

# ================= THREAD LOOP =================
def run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scanner())

# ================= BEAUTIFUL HTML DASHBOARD =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Arbitrage Scanner - 350+ Pairs</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
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
            min-width: 130px;
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            color: #666;
            margin-top: 5px;
            font-size: 0.85em;
        }
        .update-time {
            text-align: center;
            padding: 10px;
            background: #fff3cd;
            color: #856404;
            font-size: 0.85em;
        }
        .table-container {
            overflow-x: auto;
            padding: 20px;
            max-height: 65vh;
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
            font-size: 0.8em;
        }
        .badge-sell {
            background: #dc3545;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: bold;
            display: inline-block;
            font-size: 0.8em;
        }
        .spread-positive { color: #28a745; font-weight: bold; font-size: 1.1em; }
        .exchange-name {
            background: #f0f0f0;
            padding: 3px 10px;
            border-radius: 6px;
            font-weight: 600;
            display: inline-block;
            font-size: 0.85em;
        }
        .profit { color: #28a745; font-weight: bold; }
        .loading {
            text-align: center;
            padding: 40px;
            color: #999;
            animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .fees { font-size: 0.7em; color: #888; }
        @media (max-width: 768px) {
            .stat-value { font-size: 1.2em; }
            td, th { padding: 6px; font-size: 10px; }
            .badge-buy, .badge-sell { padding: 2px 6px; font-size: 0.7em; }
        }
    </style>
    <script>
        let autoRefresh = setInterval(refreshData, 3000);
        
        async function refreshData() {
            try {
                const response = await fetch('/api/opportunities');
                const data = await response.json();
                updateTable(data);
                document.getElementById('lastUpdate').innerHTML = 
                    'Last updated: ' + new Date().toLocaleTimeString() + ' | Monitoring ' + data.pairs + ' pairs';
            } catch(err) {
                console.error('Error:', err);
            }
        }
        
        function updateTable(data) {
            const tbody = document.getElementById('opportunitiesBody');
            tbody.innerHTML = '';
            
            if (data.opportunities && data.opportunities.length > 0) {
                data.opportunities.forEach(opp => {
                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td><span class="badge-buy">BUY</span></td>
                        <td><span class="exchange-name">${opp.buy_exchange.toUpperCase()}</span><div class="fees">fee: ${opp.buy_fee_pct}%</div></td>
                        <td><strong>${opp.pair}</strong></td>
                        <td class="spread-positive">+${opp.spread}%</td>
                        <td><span class="badge-sell">SELL</span></td>
                        <td><span class="exchange-name">${opp.sell_exchange.toUpperCase()}</span><div class="fees">fee: ${opp.sell_fee_pct}%</div></td>
                        <td>$${opp.liquidity.toLocaleString()}</td>
                        <td class="profit">$${opp.profit_100}</td>
                    `;
                });
                document.getElementById('bestSpread').innerHTML = data.opportunities[0].spread + '%';
            } else {
                tbody.innerHTML = '<tr><td colspan="8" class="loading">🔍 Scanning for arbitrage opportunities across ' + data.pairs + ' pairs...</td></tr>';
                document.getElementById('bestSpread').innerHTML = '0%';
            }
            
            document.getElementById('oppCount').innerHTML = data.opportunities.length;
            document.getElementById('pairsCount').innerHTML = data.pairs;
        }
        
        refreshData();
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Crypto Arbitrage Scanner</h1>
            <p>Real-time arbitrage across 5 exchanges | {{ total_pairs }}+ Trading Pairs</p>
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
                <div class="stat-value" id="pairsCount">0</div>
                <div class="stat-label">Pairs Monitored</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ exchanges|length }}</div>
                <div class="stat-label">Exchanges</div>
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
                        <th>Exchange (Fee)</th>
                        <th>Pair</th>
                        <th>Spread</th>
                        <th>Action</th>
                        <th>Exchange (Fee)</th>
                        <th>Liquidity</th>
                        <th>Profit ($100)</th>
                    </tr>
                </thead>
                <tbody id="opportunitiesBody">
                    <tr><td colspan="8" class="loading">Loading markets and scanning for opportunities...</td></tr>
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

# ================= FLASK ROUTES =================
@app.route("/")
def home():
    with lock:
        return render_template_string(HTML_TEMPLATE, 
                                    exchanges=EXCHANGE_IDS,
                                    total_pairs=len(unique_coins))

@app.route("/api/opportunities")
def api():
    with lock:
        return jsonify({
            "opportunities": opportunities,
            "count": len(opportunities),
            "pairs": len(all_pairs),
            "target_pairs": len(TARGET_PAIRS),
            "time": last_update.isoformat() if last_update else None
        })

@app.route("/health")
def health():
    with lock:
        return jsonify({
            "status": "ok" if initialized else "initializing",
            "opportunities": len(opportunities),
            "pairs_monitored": len(all_pairs),
            "target_pairs": len(TARGET_PAIRS),
            "exchanges_active": len([e for e in exchanges if exchanges[e]]),
            "last_update": str(last_update) if last_update else None
        })

@app.route("/stats")
def stats():
    with lock:
        return jsonify({
            "total_coins": len(unique_coins),
            "total_pairs_target": len(TARGET_PAIRS),
            "active_pairs": len(all_pairs),
            "opportunities_found": len(opportunities),
            "exchanges": {
                ex_id: len(exchange_available_pairs.get(ex_id, set()))
                for ex_id in EXCHANGE_IDS
            }
        })

# ================= START =================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    
    logger.info("=" * 60)
    logger.info(f"🚀 Starting Arbitrage Scanner")
    logger.info(f"📊 Monitoring {len(EXCHANGE_IDS)} exchanges: {', '.join(EXCHANGE_IDS)}")
    logger.info(f"🎯 Targeting {len(TARGET_PAIRS)} potential pairs ({len(unique_coins)} unique coins)")
    logger.info(f"💹 Minimum spread: {MIN_SPREAD}% | Min volume: ${MIN_VOLUME_USD:,}")
    logger.info("=" * 60)
    
    # Start scanner in background thread
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    
    # Run Flask app
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
