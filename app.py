#!/usr/bin/env python3
"""
Arbimine.pro - Enhanced Arbitrage Scanner
500+ Coins | 5 Exchanges | Real-time Scanning
"""

import os
import asyncio
import threading
import time
import requests
import gc
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# =========================
# CONFIGURATION
# =========================
MIN_PROFIT = float(os.getenv("MIN_PROFIT", "0.1"))
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", "300"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
PARALLEL_BATCHES = 3

EXCHANGES_TO_SCAN = {
    'mexc': 'mexc',
    'kucoin': 'kucoin',
    'coinex': 'coinex',
    'bitmart': 'bitmart',
    'gateio': 'gateio',
}

EXCHANGE_FEES = {
    'mexc': 0.002, 'kucoin': 0.001, 'coinex': 0.002,
    'bitmart': 0.0025, 'gateio': 0.002,
}

# Global state
latest_opportunities = []
last_scan_time = None
scan_count = 0
price_table = {}
all_symbols = []
symbol_count_by_exchange = {}
exchange_status = {}
table_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=PARALLEL_BATCHES)

# =========================
# YOUR COMPLETE COIN LIST (Tiered)
# =========================
COINS = [
    # ================= TIER 1 =================
    'BTC', 'ETH', 'BNB', 'SOL', 'TON', 'SUI', 'XLM', 'XMR',

    # ================= LARGE CAPS =================
    'DOGE', 'AVAX', 'TRX', 'ADA', 'MATIC', 'POL', 'LINK', 'DOT', 'XRP', 'ICP',
    'HBAR', 'SHIB', 'LTC', 'ETC', 'BCH', 'NEAR', 'APT', 'ARB', 'OP', 'INJ',
    'RUNE', 'FET', 'GRT', 'UNI', 'AAVE', 'MKR', 'SNX', 'CRV', 'SUSHI', '1INCH',
    'COMP', 'YFI', 'BAL', 'GMX', 'WOO',

    # ================= MID / INFRA =================
    'IMX', 'AR', 'EGLD', 'FLUX', 'ALGO', 'KCS', 'ZEC', 'STX', 'VET', 'FIL',
    'RNDR', 'ROSE', 'STG', 'RLB', 'RDNT', 'JUP', 'PYR', 'JTO', 'WLD', 'ZRO',
    'TIA', 'SEI', 'AGIX', 'AKT', 'MINA', 'FLOW', 'ZIL', 'QTUM', 'ENJ', 'CHZ',
    'BAT', 'MANA', 'SAND', 'GALA', 'AXS', 'ILV', 'YGG', 'DYDX', 'LDO', 'PENDLE',
    'JOE', 'CELR', 'IOTX', 'CKB', 'XVS', 'XEM', 'DGB', 'RVN', 'SC', 'NKN',
    'SYS', 'RLC', 'OMG', 'ZRX', 'BAND', 'OXT', 'CTSI', 'PERP', 'RIF', 'LRC',
    'SKL', 'CVC', 'WAXP', 'BNT', 'REN', 'UMA', 'NMR', 'TRU', 'IDEX', 'HFT',
    'BIGTIME', 'FLM', 'ALPHA', 'TLM', 'OGN', 'CHR', 'REEF', 'POLYX', 'DAR',
    'LPT', 'MX', 'HOT', 'ANKR', 'UTK', 'API3', 'CELO', 'KDA', 'GLMR', 'MOVR',
    'SXP', 'KAVA',

    # ================= CEX + L1 ECOSYSTEM =================
    'OKB', 'GT', 'HT', 'LEO', 'KLAY', 'XTZ', 'ATOM', 'KSM', 'WAVES', 'IOTA',
    'NEO', 'ONT', 'NANO', 'DASH', 'XEC', 'THETA', 'TFUEL', 'FTM', 'CFX',
    'APTOS', 'SUIX', 'STRK', 'METIS',

    # ================= DEFI / AI / TREND =================
    'PEPE', 'BONK', 'WIF', 'POPCAT', 'MEW', 'BOME', 'MOG', 'NEIRO', 'BRETT',
    'FLOKI', 'TURBO', 'WEN', 'SLERF', 'PNUT', 'TRUMP', 'MELANIA', 'BABYDOGE',
    'SHIBAINU', 'DOGWIFHAT', 'TOSHI', 'PURR', 'NYAN', 'CAT', 'MASK', 'GRT',
    'FET', 'AGIX', 'RNDR',

    # ================= ADDITIONAL MIDCAP EXPANSION =================
    'STMX', 'BICO', 'ACH', 'LINA', 'REEF', 'HARD', 'IRIS', 'LTO', 'MDX', 'NULS',
    'PHA', 'QNT', 'KNC', 'DODO', 'BEL', 'PERL', 'DATA', 'SFP', 'LIT', 'TORN',
    'ARPA', 'GTC', 'CTK', 'COS', 'DUSK', 'FIS', 'FORTH', 'FXS', 'GMX', 'JOE',
    'MAGIC', 'IMX', 'RAD', 'ALICE', 'SLP', 'GMT', 'GST', 'VOXEL', 'XETA',
    'HOOK', 'ID', 'RDNT', 'PENDLE', 'ASTR', 'MOVR', 'GLMR', 'WOO', 'API3',
    'RPL', 'LDO', 'SD', 'STG', 'LQTY', 'POND',

    # ================= MEME / HIGH VOLATILITY =================
    'PEOPLE', 'WOJAK', 'MILADY', 'DOGS', 'CATE', 'ELON', 'SHIB2', 'PEPE2',
    'MOON', 'STAR', 'X', 'AIDOGE', 'BULL', 'BEAR', 'FLOKICEO', 'CHEEMS',
    'KISHU', 'HOGE', 'AKITA', 'SAFEMOON'
]

# Remove duplicates and create USDT pairs
UNIQUE_COINS = list(dict.fromkeys(COINS))  # Preserves order while removing duplicates
SYMBOLS = [f"{coin}/USDT" for coin in UNIQUE_COINS]

print(f"✅ Loaded {len(SYMBOLS)} unique trading pairs from your coin list")

# =========================
# COIN TIER MAPPING (for display)
# =========================
COIN_TIERS = {}

# TIER 1
tier1_coins = ['BTC', 'ETH', 'BNB', 'SOL', 'TON', 'SUI', 'XLM', 'XMR']
for coin in tier1_coins:
    COIN_TIERS[coin] = '👑 TIER 1'

# Large Caps
large_caps = ['DOGE', 'AVAX', 'TRX', 'ADA', 'MATIC', 'POL', 'LINK', 'DOT', 'XRP',
              'ICP', 'HBAR', 'SHIB', 'LTC', 'ETC', 'BCH', 'NEAR', 'APT', 'ARB',
              'OP', 'INJ', 'RUNE', 'FET', 'GRT', 'UNI', 'AAVE', 'MKR']
for coin in large_caps:
    COIN_TIERS[coin] = '⭐ LARGE CAP'

# Mid/Infra
mid_coins = ['IMX', 'AR', 'EGLD', 'FLUX', 'ALGO', 'KCS', 'ZEC', 'STX', 'VET',
             'FIL', 'RNDR', 'ROSE', 'STG', 'JUP', 'TIA', 'SEI', 'AGIX', 'MINA',
             'FLOW', 'ENJ', 'CHZ', 'MANA', 'SAND', 'GALA', 'AXS', 'YGG', 'DYDX',
             'LDO', 'PENDLE', 'LRC', 'CKB']
for coin in mid_coins:
    COIN_TIERS[coin] = '🔷 MID CAP'

# Meme / High Volatility
meme_coins = ['PEPE', 'BONK', 'WIF', 'POPCAT', 'MEW', 'BOME', 'MOG', 'NEIRO',
              'BRETT', 'FLOKI', 'TURBO', 'WEN', 'PNUT']
for coin in meme_coins:
    COIN_TIERS[coin] = '🐸 MEME'

# Default tier for others
def get_coin_tier(coin):
    return COIN_TIERS.get(coin, '📊 STANDARD')

# =========================
# OPTIMIZED PRICE ENGINE
# =========================
class OptimizedPriceEngine:
    def __init__(self):
        import ccxt
        self.exchanges = {}
        for name, ccxt_id in EXCHANGES_TO_SCAN.items():
            try:
                exchange_class = getattr(ccxt, ccxt_id)
                self.exchanges[name] = exchange_class({
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'},
                    'timeout': 15000,
                })
                exchange_status[name] = "init"
            except Exception as e:
                exchange_status[name] = f"failed: {str(e)[:20]}"

def fetch_exchange_optimized(name, exchange, symbols):
    """Optimized fetch for single exchange"""
    try:
        exchange.load_markets()
        available = [s for s in symbols if s in exchange.markets]
        symbol_count_by_exchange[name] = len(available)
        
        if not available:
            exchange_status[name] = "no_pairs"
            return 0
        
        exchange_status[name] = "fetching"
        total_fetched = 0
        
        # Dynamic batch sizing
        batch_size = 50 if len(available) > 200 else 30
        batches = [available[i:i+batch_size] for i in range(0, len(available), batch_size)]
        
        for batch in batches:
            try:
                tickers = exchange.fetch_tickers(batch)
                now = time.time()
                
                with table_lock:
                    for symbol, ticker in tickers.items():
                        if ticker.get('bid') and ticker.get('ask') and ticker['bid'] > 0:
                            if symbol not in price_table:
                                price_table[symbol] = {}
                            price_table[symbol][name] = {
                                'bid': float(ticker['bid']),
                                'ask': float(ticker['ask']),
                                'bidVolume': float(ticker.get('quoteVolume', ticker.get('baseVolume', 0))),
                                'askVolume': float(ticker.get('quoteVolume', ticker.get('baseVolume', 0))),
                                'ts': now
                            }
                            total_fetched += 1
            except Exception as e:
                print(f"   [{name}] Batch error: {e}")
            
            time.sleep(0.2)
        
        exchange_status[name] = f"live: {total_fetched}"
        return total_fetched
        
    except Exception as e:
        exchange_status[name] = f"error: {str(e)[:30]}"
        return 0

def run_optimized_poller():
    """Main polling loop"""
    engine = OptimizedPriceEngine()
    global all_symbols
    all_symbols = SYMBOLS
    
    print(f"\n{'='*60}")
    print(f"🚀 Starting Optimized Poller")
    print(f"📊 {len(all_symbols)} coins | {len(engine.exchanges)} exchanges")
    print(f"{'='*60}\n")
    
    while True:
        cycle_start = time.time()
        print(f"\n🔄 Cycle at {datetime.utcnow().strftime('%H:%M:%S')}")
        
        for name, exchange in engine.exchanges.items():
            fetch_start = time.time()
            print(f"📡 Fetching {name}...", end=' ', flush=True)
            
            count = fetch_exchange_optimized(name, exchange, all_symbols)
            
            print(f"✓ {count} pairs in {time.time()-fetch_start:.1f}s")
            
            gc.collect()
            time.sleep(1)
        
        # Clean stale data
        with table_lock:
            now = time.time()
            stale_threshold = 180
            
            for symbol in list(price_table.keys()):
                stale_exchanges = []
                for ex in list(price_table[symbol].keys()):
                    if now - price_table[symbol][ex]['ts'] > stale_threshold:
                        stale_exchanges.append(ex)
                for ex in stale_exchanges:
                    del price_table[symbol][ex]
                if len(price_table[symbol]) == 0:
                    del price_table[symbol]
        
        cycle_time = time.time() - cycle_start
        with table_lock:
            active_symbols = len(price_table)
            symbols_with_2plus = sum(1 for s in price_table.values() if len(s) >= 2)
        
        print(f"\n📈 Cycle complete: {active_symbols} symbols | {symbols_with_2plus} with 2+ exchanges | {cycle_time:.1f}s")
        
        sleep_time = max(SCAN_INTERVAL - cycle_time, 10)
        time.sleep(sleep_time)

# =========================
# FAST ARBITRAGE CALCULATOR
# =========================
class FastArbitrageCalculator:
    @staticmethod
    def find_opportunities():
        global latest_opportunities, last_scan_time, scan_count
        start_time = time.time()
        opportunities = []
        
        with table_lock:
            symbols = list(price_table.keys())
        
        def process_symbol(symbol):
            ex_data = price_table.get(symbol, {})
            if len(ex_data) < 2:
                return None
            
            now = time.time()
            clean_data = {k: v for k, v in ex_data.items() if now - v['ts'] < 120}
            if len(clean_data) < 2:
                return None
            
            best_buy = None
            best_sell = None
            min_ask = float('inf')
            max_bid = 0
            
            for ex_name, data in clean_data.items():
                fee = EXCHANGE_FEES.get(ex_name, 0.002)
                ask_eff = data['ask'] * (1 + fee)
                bid_eff = data['bid'] * (1 - fee)
                
                if ask_eff < min_ask:
                    min_ask = ask_eff
                    best_buy = (ex_name, data)
                if bid_eff > max_bid:
                    max_bid = bid_eff
                    best_sell = (ex_name, data)
            
            if best_buy and best_sell and best_buy[0] != best_sell[0]:
                profit_pct = ((max_bid - min_ask) / min_ask) * 100
                liquidity = min(
                    best_buy[1]['askVolume'] * best_buy[1]['ask'],
                    best_sell[1]['bidVolume'] * best_sell[1]['bid']
                )
                
                if profit_pct >= MIN_PROFIT and liquidity >= MIN_LIQUIDITY:
                    coin = symbol.replace("/USDT", "")
                    return {
                        "buy_exchange": best_buy[0].upper(),
                        "sell_exchange": best_sell[0].upper(),
                        "symbol": coin,
                        "tier": get_coin_tier(coin),
                        "profit_percent": round(profit_pct, 3),
                        "liquidity": int(liquidity),
                        "buy_price": round(best_buy[1]['ask'], 6),
                        "sell_price": round(best_sell[1]['bid'], 6),
                        "timestamp": datetime.utcnow().strftime('%H:%M:%S')
                    }
            return None
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_symbol, symbols))
        
        opportunities = [r for r in results if r is not None]
        opportunities.sort(key=lambda x: x['profit_percent'], reverse=True)
        
        latest_opportunities = opportunities[:50]
        last_scan_time = datetime.utcnow()
        scan_count += 1
        
        elapsed = time.time() - start_time
        print(f"✅ Scan #{scan_count}: {len(opportunities)} opportunities | {elapsed:.1f}s | {len(symbols)} symbols")
        
        return opportunities

def run_calculator_fast():
    """Run calculator frequently"""
    while True:
        try:
            if len(price_table) > 0:
                FastArbitrageCalculator.find_opportunities()
            time.sleep(8)
        except Exception as e:
            print(f"Calculator error: {e}")
            time.sleep(10)

# Start threads
print("🚀 Starting Arbimine.pro...")
threading.Thread(target=run_optimized_poller, daemon=True).start()
time.sleep(5)
threading.Thread(target=run_calculator_fast, daemon=True).start()

# =========================
# FLASK ROUTES
# =========================
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE_ENHANCED,
                                  opportunities=latest_opportunities,
                                  scan_count=scan_count,
                                  last_scan=last_scan_time.strftime('%H:%M:%S UTC') if last_scan_time else 'Starting...',
                                  min_profit=MIN_PROFIT,
                                  coins=len(UNIQUE_COINS),
                                  exchanges=symbol_count_by_exchange,
                                  status=exchange_status)

@app.route('/api/opportunities')
def api_opportunities():
    return jsonify({
        "success": True,
        "scan_count": scan_count,
        "timestamp": datetime.utcnow().isoformat(),
        "opportunities": latest_opportunities[:30]
    })

@app.route('/api/debug')
def debug():
    with table_lock:
        tracked = len(price_table)
        symbols_with_2plus = sum(1 for s in price_table.values() if len(s) >= 2)
        symbols_with_3plus = sum(1 for s in price_table.values() if len(s) >= 3)
    
    return jsonify({
        "scan_count": scan_count,
        "total_coins": len(UNIQUE_COINS),
        "symbols_tracked": tracked,
        "symbols_2plus_exchanges": symbols_with_2plus,
        "symbols_3plus_exchanges": symbols_with_3plus,
        "opportunities_current": len(latest_opportunities),
        "exchange_status": exchange_status,
        "pairs_per_exchange": symbol_count_by_exchange,
        "last_scan": last_scan_time.isoformat() if last_scan_time else None
    })

HTML_TEMPLATE_ENHANCED = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arbimine.pro - 500+ Coins Arbitrage Scanner</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace; background: #0a0e27; padding: 15px; color: #ccc; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; margin-bottom: 20px; color: white; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .stat-card { background: #1a1f3f; padding: 12px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 1.8em; font-weight: bold; color: #667eea; }
        .stat-label { font-size: 0.7em; opacity: 0.8; margin-top: 5px; }
        .opportunities { background: #1a1f3f; border-radius: 10px; padding: 15px; overflow-x: auto; }
        .arb-table { width: 100%; border-collapse: collapse; }
        .arb-table th { text-align: left; padding: 12px; background: #0a0e27; color: #667eea; border-bottom: 2px solid #667eea; }
        .arb-table td { padding: 10px; border-bottom: 1px solid #2a2f4f; }
        .buy { color: #10b981; font-weight: bold; }
        .sell { color: #f59e0b; font-weight: bold; }
        .profit { color: #10b981; font-weight: bold; }
        .tier-badge { font-size: 0.7em; padding: 2px 6px; border-radius: 4px; display: inline-block; }
        .exchange { background: #2a2f4f; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; display: inline-block; }
        .live-indicator { display: inline-block; width: 10px; height: 10px; background: #10b981; border-radius: 50%; animation: pulse 1s infinite; margin-left: 8px; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .status-bar { background: #1a1f3f; padding: 10px; border-radius: 5px; margin: 10px 0; font-family: monospace; font-size: 0.7em; display: flex; flex-wrap: wrap; gap: 15px; }
        .status-good { color: #10b981; }
        .status-warning { color: #f59e0b; }
        footer { text-align: center; margin-top: 20px; color: #666; font-size: 0.7em; }
        .no-opps { text-align: center; padding: 40px; color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Arbimine.pro <span class="live-indicator"></span></h1>
            <p>5 Exchanges | {{ coins }}+ Coins | Real-time Arbitrage Scanner</p>
        </div>

        <div class="stats">
            <div class="stat-card"><div class="stat-value">{{ opportunities|length }}</div><div class="stat-label">Opportunities</div></div>
            <div class="stat-card"><div class="stat-value">{{ coins }}</div><div class="stat-label">Coins</div></div>
            <div class="stat-card"><div class="stat-value">{{ exchanges|length }}</div><div class="stat-label">Exchanges</div></div>
            <div class="stat-card"><div class="stat-value">{{ scan_count }}</div><div class="stat-label">Scans</div></div>
            <div class="stat-card"><div class="stat-value">{{ last_scan }}</div><div class="stat-label">Updated</div></div>
            <div class="stat-card"><div class="stat-value">{{ min_profit }}%</div><div class="stat-label">Min Profit</div></div>
        </div>

        <div class="status-bar">
            <span>🔌 Status:</span>
            {% for ex, stat in status.items() %}
                <span class="{% if 'live' in stat %}status-good{% else %}status-warning{% endif %}">{{ ex }}:{{ stat[:15] }}</span>
            {% endfor %}
        </div>

        <div class="opportunities">
            <table class="arb-table">
                <thead>
                    <tr><th>Action</th><th>Tier</th><th>Buy From</th><th>Sell To</th><th>Coin</th><th>Profit</th><th>Liquidity</th></tr>
                </thead>
                <tbody>
                    {% if opportunities %}
                        {% for opp in opportunities %}
                        <tr>
                            <td class="buy">BUY→SELL</td>
                            <td><span class="tier-badge">{{ opp.tier }}</span></td>
                            <td><span class="exchange">{{ opp.buy_exchange }}</span></td>
                            <td><span class="exchange">{{ opp.sell_exchange }}</span></td>
                            <td><strong>{{ opp.symbol }}/USDT</strong></td>
                            <td class="profit">+{{ opp.profit_percent }}%</td>
                            <td>${{ "{:,}".format(opp.liquidity) }}</td>
                        </tr>
                        {% endfor %}
                    {% else %}
                        <tr><td colspan="7" class="no-opps">🔍 Scanning {{ coins }} coins across 5 exchanges...<br>Total scans: {{ scan_count }}</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>

        <footer>
            Active pairs: {% for ex, count in exchanges.items() if count > 0 %}{{ ex|upper }}:{{ count }} {% endfor %}
            <br>🔄 Auto-refreshes every 15 seconds
        </footer>
    </div>
    <script>setInterval(() => location.reload(), 15000);</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("="*70)
    print("🚀 Arbimine.pro - Complete Edition")
    print("="*70)
    print(f"📊 Exchanges: {', '.join(EXCHANGES_TO_SCAN.keys())}")
    print(f"⚡ {len(UNIQUE_COINS)} Coins | Min Profit: {MIN_PROFIT}% | Min Liq: ${MIN_LIQUIDITY}")
    print(f"🌐 Dashboard: http://localhost:{port}")
    print(f"🔍 Debug: http://localhost:{port}/api/debug")
    print("="*70)
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
