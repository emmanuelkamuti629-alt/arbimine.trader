#!/usr/bin/env python3
import os
import asyncio
import threading
import time
import requests
import gc
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# Add CORS headers manually instead of using flask_cors
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
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", "1000"))
TOP_N_COINS = 300

EXCHANGES_TO_SCAN = {
    'mexc': 'mexc',
    'kucoin': 'kucoin', 
    'coinex': 'coinex',
    'bitmart': 'bitmart',
    'gateio': 'gateio',
}

# Global state
latest_opportunities = []
last_scan_time = None
scan_count = 0
price_table = {}
top_300_symbols = []
symbol_count_by_exchange = {}
exchange_status = {}
table_lock = threading.Lock()

# Bot control flags
bot_running = True
poller_thread = None
calculator_thread = None
bot_control_lock = threading.Lock()

EXCHANGE_FEES = {
    'mexc': 0.002, 'kucoin': 0.001, 'coinex': 0.002,
    'bitmart': 0.0025, 'gateio': 0.002,
}

# =========================
# COMPLETE COIN LIST (300+ coins)
# =========================
FALLBACK_COINS = [
    # ================= TIER 1 =================
    'BTC','ETH','BNB','SOL','TON','SUI','XLM','XMR',
    # ================= LARGE CAPS =================
    'DOGE','AVAX','TRX','ADA','MATIC','POL','LINK','DOT','XRP','ICP','HBAR','SHIB',
    'LTC','ETC','BCH','NEAR','APT','ARB','OP','INJ','RUNE','FET','GRT','UNI',
    'AAVE','MKR','SNX','CRV','SUSHI','1INCH','COMP','YFI','BAL','GMX','WOO',
    # ================= MID / INFRA =================
    'IMX','AR','EGLD','FLUX','ALGO','KCS','ZEC','STX','VET','FIL','RNDR',
    'ROSE','STG','RLB','RDNT','JUP','PYR','JTO','WLD','ZRO','TIA','SEI',
    'AGIX','AKT','MINA','FLOW','ZIL','QTUM','ENJ','CHZ','BAT','MANA','SAND',
    'GALA','AXS','ILV','YGG','DYDX','LDO','PENDLE','JOE','CELR','IOTX','CKB',
    'XVS','XEM','DGB','RVN','SC','NKN','SYS','RLC','OMG','ZRX','BAND','OXT',
    'CTSI','PERP','RIF','LRC','SKL','CVC','WAXP','BNT','REN','UMA','NMR','TRU',
    'IDEX','HFT','BIGTIME','FLM','ALPHA','TLM','OGN','CHR','REEF','POLYX','DAR',
    'LPT','MX','HOT','ANKR','UTK','API3','CELO','KDA','GLMR','MOVR','SXP','KAVA',
    # ================= CEX + L1 ECOSYSTEM =================
    'OKB','GT','HT','LEO','KLAY','XTZ','ATOM','KSM','WAVES','IOTA','NEO','ONT',
    'NANO','DASH','XEC','THETA','TFUEL','FTM','CFX','APTOS','SUIX','STRK','METIS',
    # ================= DEFI / AI / TREND =================
    'PEPE','BONK','WIF','POPCAT','MEW','BOME','MOG','NEIRO','BRETT','FLOKI',
    'TURBO','WEN','SLERF','PNUT','TRUMP','MELANIA','BABYDOGE','SHIBAINU',
    'DOGWIFHAT','TOSHI','PURR','NYAN','CAT','MASK','GRT','FET','AGIX','RNDR',
    # ================= ADDITIONAL MIDCAP EXPANSION =================
    'STMX','BICO','ACH','LINA','REEF','HARD','IRIS','LTO','MDX','NULS','PHA',
    'QNT','KNC','DODO','BEL','PERL','DATA','SFP','LIT','TORN','ARPA','GTC',
    'CTK','COS','DUSK','FIS','FORTH','FXS','GMX','JOE','MAGIC','IMX','RAD',
    'ALICE','SLP','GMT','GST','VOXEL','XETA','HOOK','ID','RDNT','PENDLE',
    'ASTR','MOVR','GLMR','WOO','API3','RPL','LDO','SD','STG','LQTY','POND',
    # ================= MEME / HIGH VOLATILITY =================
    'PEOPLE','WOJAK','MILADY','DOGS','CATE','ELON','SHIB2','PEPE2','MOON','STAR',
    'X','AIDOGE','BULL','BEAR','FLOKICEO','CHEEMS','KISHU','HOGE','AKITA','SAFEMOON'
]

# =========================
# TOP 300 COIN FETCHER
# =========================
def get_top_300_coins():
    global top_300_symbols
    if top_300_symbols:
        return top_300_symbols
    
    try:
        print("🔍 Fetching top 300 coins from CoinGecko...")
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'volume_desc', 'per_page': 250, 'page': 1}
        r1 = requests.get(url, params=params, timeout=20)
        
        coins = []
        if isinstance(r1.json(), list):
            coins.extend(r1.json())
        
        params['page'] = 2
        r2 = requests.get(url, params=params, timeout=20)
        if isinstance(r2.json(), list):
            coins.extend(r2.json())
        
        if coins:
            top_300_symbols = [f"{c['symbol'].upper()}/USDT" for c in coins[:TOP_N_COINS]]
            print(f"✅ Loaded {len(top_300_symbols)} coins from CoinGecko")
        else:
            raise Exception("CoinGecko returned no valid data")
            
        return top_300_symbols
    except Exception as e:
        print(f"CoinGecko error: {e}. Using fallback list with {len(FALLBACK_COINS)} coins")
        top_300_symbols = [f"{s}/USDT" for s in FALLBACK_COINS[:TOP_N_COINS]]
        print(f"✅ Loaded {len(top_300_symbols)} coins from fallback list")
        return top_300_symbols

# =========================
# PRICE ENGINE
# =========================
class PriceEngine:
    def __init__(self):
        self.exchanges = {}
        for name, ccxt_id in EXCHANGES_TO_SCAN.items():
            try:
                import ccxt
                exchange_class = getattr(ccxt, ccxt_id)
                self.exchanges[name] = exchange_class({
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'},
                    'timeout': 30000,
                })
                exchange_status[name] = "init"
            except Exception as e:
                exchange_status[name] = f"init_failed: {str(e)[:20]}"

    def get_fee(self, exchange):
        return EXCHANGE_FEES.get(exchange, 0.002)

# =========================
# ARBITRAGE CALCULATOR
# =========================
class ArbitrageCalculator:
    def __init__(self):
        pass

    def find_opportunities(self):
        global latest_opportunities, last_scan_time, scan_count
        start = time.time()
        opps = []

        with table_lock:
            items = list(price_table.items())

        for symbol, ex_data in items:
            if len(ex_data) < 2:
                continue

            best_buy = None
            best_sell = None
            min_ask_eff = float('inf')
            max_bid_eff = 0

            for ex_name, data in ex_data.items():
                if time.time() - data['ts'] > 90:
                    continue

                fee = EXCHANGE_FEES.get(ex_name, 0.002)
                ask_eff = data['ask'] * (1 + fee)
                bid_eff = data['bid'] * (1 - fee)

                if ask_eff < min_ask_eff:
                    min_ask_eff = ask_eff
                    best_buy = (ex_name, data)
                if bid_eff > max_bid_eff:
                    max_bid_eff = bid_eff
                    best_sell = (ex_name, data)

            if best_buy and best_sell and best_buy[0] != best_sell[0]:
                profit_pct = ((max_bid_eff - min_ask_eff) / min_ask_eff) * 100
                liquidity = min(
                    best_buy[1]['askVolume'] * best_buy[1]['ask'],
                    best_sell[1]['bidVolume'] * best_sell[1]['bid']
                )

                if profit_pct >= MIN_PROFIT and liquidity >= MIN_LIQUIDITY:
                    opps.append({
                        "buy_exchange": best_buy[0].upper(),
                        "sell_exchange": best_sell[0].upper(),
                        "symbol": symbol.replace("/USDT", ""),
                        "profit_percent": round(profit_pct, 3),
                        "liquidity": int(liquidity),
                        "buy_price": round(best_buy[1]['ask'], 6),
                        "sell_price": round(best_sell[1]['bid'], 6),
                        "timestamp": datetime.utcnow().strftime('%H:%M:%S')
                    })

        opps.sort(key=lambda x: x['profit_percent'], reverse=True)
        latest_opportunities = opps[:50]
        last_scan_time = datetime.utcnow()
        scan_count += 1
        print(f"Scan #{scan_count}: {len(opps)} opps from {len(items)} symbols in {time.time()-start:.2f}s")

# =========================
# BACKGROUND TASKS
# =========================
def fetch_exchange_sequential(name, exchange, coins):
    global bot_running
    try:
        exchange.load_markets()
        available = [s for s in coins if s in exchange.markets and exchange.markets[s].get('active', True)]
        symbol_count_by_exchange[name] = len(available)
        
        if len(available) == 0:
            exchange_status[name] = "no_pairs"
            return name, 0
            
        exchange_status[name] = "ready"
        
        batch_size = 30
        total_count = 0
        for i in range(0, len(available), batch_size):
            if not bot_running:
                break
            batch = available[i:i+batch_size]
            try:
                tickers = exchange.fetch_tickers(batch)
                now = time.time()
                
                with table_lock:
                    for symbol, ticker in tickers.items():
                        if ticker.get('bid') and ticker.get('ask') and ticker['bid'] > 0 and ticker['ask'] > 0:
                            if symbol not in price_table:
                                price_table[symbol] = {}
                            price_table[symbol][name] = {
                                'bid': float(ticker['bid']),
                                'ask': float(ticker['ask']),
                                'bidVolume': float(ticker.get('quoteVolume', 0)),
                                'askVolume': float(ticker.get('quoteVolume', 0)),
                                'ts': now
                            }
                            total_count += 1
            except Exception as e:
                print(f"[{name}] Batch error: {e}")
            
            if 'tickers' in locals():
                del tickers
            
            gc.collect()
            if bot_running:
                time.sleep(0.3)
        
        exchange_status[name] = f"live: {total_count}" if bot_running else "stopped"
        return name, total_count
    except Exception as e:
        exchange_status[name] = "error"
        print(f"[{name}] Fatal error: {e}")
        return name, 0

def run_rest_poller():
    global bot_running
    engine = PriceEngine()
    coins = get_top_300_coins()
    
    while True:
        if not bot_running:
            time.sleep(2)
            continue
            
        cycle_start = time.time()
        print(f"\n{'='*50}")
        print(f"🔄 Starting poll cycle at {datetime.utcnow().strftime('%H:%M:%S')}")
        print(f"📊 Scanning {len(coins)} coins across {len(engine.exchanges)} exchanges")
        print(f"{'='*50}")
        
        for name, exchange in engine.exchanges.items():
            if not bot_running:
                break
            poll_start = time.time()
            print(f"📊 Polling {name}...")
            _, count = fetch_exchange_sequential(name, exchange, coins)
            print(f"   ✅ {name}: {count} prices in {time.time()-poll_start:.1f}s")
            gc.collect()
            time.sleep(2)
        
        if not bot_running:
            continue
            
        cycle_time = time.time() - cycle_start
        with table_lock:
            print(f"📈 Cycle complete: {len(price_table)} symbols tracked in {cycle_time:.1f}s")
        
        with table_lock:
            now = time.time()
            symbols_to_delete = []
            for symbol in list(price_table.keys()):
                exchanges_to_delete = []
                for ex in list(price_table[symbol].keys()):
                    if now - price_table[symbol][ex]['ts'] > 120:
                        exchanges_to_delete.append(ex)
                for ex in exchanges_to_delete:
                    del price_table[symbol][ex]
                if len(price_table[symbol]) == 0:
                    symbols_to_delete.append(symbol)
            for symbol in symbols_to_delete:
                if symbol in price_table:
                    del price_table[symbol]
        
        time.sleep(max(60, 180 - cycle_time))

def run_calculator_loop():
    global bot_running
    calc = ArbitrageCalculator()
    while True:
        if bot_running and len(price_table) > 0:
            try:
                calc.find_opportunities()
            except Exception as e:
                print(f"Calculator error: {e}")
        time.sleep(10)

def start_bot():
    global bot_running, poller_thread, calculator_thread
    with bot_control_lock:
        if not bot_running:
            bot_running = True
            print("🤖 Bot started by user")
            return {"status": "success", "message": "Bot started"}
        return {"status": "info", "message": "Bot already running"}

def stop_bot():
    global bot_running
    with bot_control_lock:
        if bot_running:
            bot_running = False
            print("🛑 Bot stopped by user")
            return {"status": "success", "message": "Bot stopped"}
        return {"status": "info", "message": "Bot already stopped"}

# Start threads only once
if poller_thread is None:
    print("Starting poller thread...")
    poller_thread = threading.Thread(target=run_rest_poller, daemon=True)
    poller_thread.start()
    time.sleep(30)

if calculator_thread is None:
    print("Starting calculator thread...")
    calculator_thread = threading.Thread(target=run_calculator_loop, daemon=True)
    calculator_thread.start()

# =========================
# FLASK ROUTES
# =========================
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE,
                                  opportunities=latest_opportunities,
                                  scan_count=scan_count,
                                  last_scan=last_scan_time.strftime('%H:%M:%S UTC') if last_scan_time else 'Starting...',
                                  min_profit=MIN_PROFIT,
                                  coins=len(top_300_symbols) if top_300_symbols else len(FALLBACK_COINS),
                                  exchanges=symbol_count_by_exchange,
                                  status=exchange_status,
                                  bot_running=bot_running)

@app.route('/api/debug')
def debug():
    with table_lock:
        tracked = len(price_table)
        symbols_with_2plus = sum(1 for s in price_table.values() if len(s) >= 2)
    active_count = sum(1 for s in exchange_status.values() if 'live' in s)
    return jsonify({
        "scan_count": scan_count,
        "total_coins": len(top_300_symbols) if top_300_symbols else len(FALLBACK_COINS),
        "symbols_tracked": tracked,
        "symbols_on_2plus_exchanges": symbols_with_2plus,
        "exchange_status": exchange_status,
        "symbols_per_exchange": symbol_count_by_exchange,
        "active_count": f"{active_count}/5",
        "last_scan": last_scan_time.isoformat() if last_scan_time else None,
        "bot_running": bot_running
    })

@app.route('/api/bot/start', methods=['POST'])
def api_start_bot():
    return jsonify(start_bot())

@app.route('/api/bot/stop', methods=['POST'])
def api_stop_bot():
    return jsonify(stop_bot())

@app.route('/api/bot/status', methods=['GET'])
def api_bot_status():
    return jsonify({
        "running": bot_running,
        "scan_count": scan_count,
        "last_scan": last_scan_time.isoformat() if last_scan_time else None,
        "opportunities": len(latest_opportunities)
    })

@app.route('/api/coins', methods=['GET'])
def api_coins():
    """Return the list of coins being tracked"""
    return jsonify({
        "total": len(top_300_symbols) if top_300_symbols else len(FALLBACK_COINS),
        "coins": [s.replace("/USDT", "") for s in (top_300_symbols if top_300_symbols else [f"{c}/USDT" for c in FALLBACK_COINS[:TOP_N_COINS]])]
    })

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arbimine.pro - 300+ Coins</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #0a0e27; padding: 15px; color: #ccc; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; margin-bottom: 20px; color: white; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .stat-card { background: #1a1f3f; padding: 12px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 1.5em; font-weight: bold; color: #667eea; }
        .stat-label { font-size: 0.7em; opacity: 0.8; margin-top: 5px; }
        .opportunities { background: #1a1f3f; border-radius: 10px; padding: 15px; overflow-x: auto; max-height: 500px; overflow-y: auto; }
        .arb-row { display: grid; grid-template-columns: 40px 65px 65px 70px 70px 90px; gap: 8px; padding: 8px 0; border-bottom: 1px solid #2a2f4f; align-items: center; font-size: 0.8em; }
        .arb-header { font-weight: bold; color: #667eea; border-bottom: 2px solid #667eea; margin-bottom: 8px; font-size: 0.75em; position: sticky; top: 0; background: #1a1f3f; }
        .buy { color: #10b981; font-weight: bold; }
        .sell { color: #f59e0b; font-weight: bold; }
        .profit { color: #10b981; font-weight: bold; }
        .exchange { background: #2a2f4f; padding: 3px 5px; border-radius: 4px; font-size: 0.7em; display: inline-block; }
        .live-indicator { display: inline-block; width: 10px; height: 10px; background: #10b981; border-radius: 50%; animation: pulse 1s infinite; margin-left: 8px; }
        .stopped-indicator { display: inline-block; width: 10px; height: 10px; background: #ef4444; border-radius: 50%; margin-left: 8px; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        footer { text-align: center; margin-top: 20px; color: #666; font-size: 0.7em; }
        .debug { background: #2a2f4f; padding: 10px; border-radius: 5px; margin: 10px 0; font-size: 0.65em; font-family: monospace; word-break: break-all; max-height: 80px; overflow-y: auto; }
        .no-opps { text-align: center; padding: 40px; color: #888; }
        .control-panel { background: #1a1f3f; padding: 15px; border-radius: 10px; margin-bottom: 20px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 5px; font-size: 14px; font-weight: bold; cursor: pointer; transition: all 0.3s; }
        .btn-start { background: #10b981; color: white; }
        .btn-start:hover { background: #059669; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .status-badge { padding: 5px 15px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .status-running { background: #10b981; color: white; }
        .status-stopped { background: #ef4444; color: white; }
        .status-text { margin-left: 10px; font-size: 14px; font-weight: bold; }
        .btn-refresh { background: #667eea; color: white; }
        .btn-refresh:hover { background: #5b67d6; }
        .coin-count { background: #2a2f4f; padding: 5px 10px; border-radius: 20px; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Arbimine.pro <span id="statusIndicator" class="{% if bot_running %}live-indicator{% else %}stopped-indicator{% endif %}"></span></h1>
            <p>5 Exchanges | <span id="coinCount">{{ coins }}</span>+ Coins | Real-time Arbitrage Scanner</p>
        </div>

        <div class="control-panel">
            <button class="btn btn-start" id="startBtn" {% if bot_running %}disabled{% endif %}>▶ START BOT</button>
            <button class="btn btn-stop" id="stopBtn" {% if not bot_running %}disabled{% endif %}>⏹ STOP BOT</button>
            <button class="btn btn-refresh" id="refreshBtn">🔄 Refresh Status</button>
            <div class="status-text">
                Bot Status: <span id="botStatus" class="status-badge {% if bot_running %}status-running{% else %}status-stopped{% endif %}">
                    {% if bot_running %}RUNNING{% else %}STOPPED{% endif %}
                </span>
            </div>
            <div class="coin-count">📊 Tracking {{ coins }} coins</div>
        </div>

        <div class="stats">
            <div class="stat-card"><div class="stat-value">{{ opportunities|length }}</div><div class="stat-label">Opportunities</div></div>
            <div class="stat-card"><div class="stat-value">{{ coins }}</div><div class="stat-label">Coins Tracked</div></div>
            <div class="stat-card"><div class="stat-value">{{ exchanges|length }}</div><div class="stat-label">Active Exchanges</div></div>
            <div class="stat-card"><div class="stat-value">{{ scan_count }}</div><div class="stat-label">Total Scans</div></div>
            <div class="stat-card"><div class="stat-value">{{ last_scan }}</div><div class="stat-label">Last Update</div></div>
            <div class="stat-card"><div class="stat-value">{{ min_profit }}%</div><div class="stat-label">Min Profit</div></div>
        </div>

        <div class="debug">
            <strong>🔌 Exchange Status:</strong> {% for ex, stat in status.items() %}{{ ex }}:{{ stat }} | {% endfor %}
        </div>

        <div class="opportunities">
            <div class="arb-row arb-header">
                <div>Action</div><div>Buy From</div><div>Sell To</div><div>Coin</div><div>Profit</div><div>Liquidity</div>
            </div>
            {% if opportunities %}
                {% for opp in opportunities %}
                <div class="arb-row">
                    <div class="buy">BUY</div>
                    <div><span class="exchange">{{ opp.buy_exchange }}</span></div>
                    <div><span class="exchange">{{ opp.sell_exchange }}</span></div>
                    <div><strong>{{ opp.symbol }}</strong></div>
                    <div class="profit">{{ opp.profit_percent }}%</div>
                    <div>${{ "{:,}".format(opp.liquidity) }}</div>
                </div>
                {% endfor %}
            {% else %}
                <div class="no-opps">
                    🔍 Scanning {{ coins }} coins across 5 exchanges...<br>
                    Full cycle takes ~3-4 minutes. Check <a href="/api/debug" style="color:#667eea">/api/debug</a> for details.
                </div>
            {% endif %}
        </div>

        <footer>
            Active pairs: {% for ex, count in exchanges.items() if count > 0 %}{{ ex|upper }}:{{ count }} {% endfor %}
            <br>Auto-refreshes every 20 seconds | Total fallback coins: 300+
        </footer>
    </div>

    <script>
        function updateBotStatus() {
            fetch('/api/bot/status')
                .then(response => response.json())
                .then(data => {
                    const statusSpan = document.getElementById('botStatus');
                    const indicator = document.getElementById('statusIndicator');
                    const startBtn = document.getElementById('startBtn');
                    const stopBtn = document.getElementById('stopBtn');
                    
                    if (data.running) {
                        statusSpan.textContent = 'RUNNING';
                        statusSpan.className = 'status-badge status-running';
                        indicator.className = 'live-indicator';
                        startBtn.disabled = true;
                        stopBtn.disabled = false;
                    } else {
                        statusSpan.textContent = 'STOPPED';
                        statusSpan.className = 'status-badge status-stopped';
                        indicator.className = 'stopped-indicator';
                        startBtn.disabled = false;
                        stopBtn.disabled = true;
                    }
                })
                .catch(error => console.error('Error:', error));
        }

        function startBot() {
            fetch('/api/bot/start', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log(data.message);
                    updateBotStatus();
                    setTimeout(() => location.reload(), 1000);
                })
                .catch(error => console.error('Error:', error));
        }

        function stopBot() {
            fetch('/api/bot/stop', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log(data.message);
                    updateBotStatus();
                    setTimeout(() => location.reload(), 1000);
                })
                .catch(error => console.error('Error:', error));
        }

        document.getElementById('startBtn').addEventListener('click', startBot);
        document.getElementById('stopBtn').addEventListener('click', stopBot);
        document.getElementById('refreshBtn').addEventListener('click', updateBotStatus);
        
        setInterval(() => location.reload(), 20000);
        setInterval(updateBotStatus, 5000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("="*70)
    print("🚀 Arbimine.pro - Enhanced Arbitrage Scanner with 300+ Coins")
    print("="*70)
    print(f"📊 Exchanges: {', '.join(EXCHANGES_TO_SCAN.keys())}")
    print(f"⚡ Config: {TOP_N_COINS} coins | Min Profit: {MIN_PROFIT}% | Min Liquidity: ${MIN_LIQUIDITY}")
    print(f"📈 Total coins in fallback list: {len(FALLBACK_COINS)}")
    print(f"🌐 Dashboard: http://localhost:{port}")
    print(f"🔍 Debug: http://localhost:{port}/api/debug")
    print(f"🎮 Bot Control: Use buttons on dashboard to start/stop the scanner")
    print("="*70)
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
