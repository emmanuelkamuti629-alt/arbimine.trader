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
MIN_VOLUME_USD =300
REFRESH_SECONDS = 3

FEES = {
    'mexc': (0.0, 0.2),
    'kucoin': (0.1, 0.1),
    'coinex': (0.2, 0.2),
    'gate': (0.15, 0.15),
    'bitget': (0.1, 0.1)
}

COINS = ['BTC','ETH','BNB','SOL','XRP','ADA','DOGE','AVAX','TRX','MATIC','LINK','DOT']
TARGET_PAIRS = [f"{c}/USDT" for c in COINS]

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARB")

# ================= GLOBAL STATE =================
exchanges = {}
opportunities = []
all_pairs = []
last_update = None
initialized = False

lock = threading.Lock()

# ================= INIT EXCHANGES =================
logger.info("Initializing exchanges...")

for ex_id in EXCHANGE_IDS:
    try:
        ex_class = getattr(ccxt, ex_id)
        exchanges[ex_id] = ex_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        logger.info(f"Loaded exchange: {ex_id}")
    except Exception as e:
        logger.error(f"Exchange init failed {ex_id}: {e}")

# ================= MARKET LOADER =================
async def load_markets():
    global all_pairs, initialized

    logger.info("Loading markets...")

    tasks = {k: ex.load_markets() for k, ex in exchanges.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    valid_pairs = set()

    for ex_id, markets in zip(tasks.keys(), results):
        if isinstance(markets, Exception):
            logger.error(f"{ex_id} market load failed: {markets}")
            continue

        logger.info(f"{ex_id} markets loaded: {len(markets)}")

        for symbol in markets.keys():
            base = symbol.split('/')[0]
            if symbol in TARGET_PAIRS:
                valid_pairs.add(symbol)
            elif f"{base}/USDT" in TARGET_PAIRS:
                valid_pairs.add(f"{base}/USDT")

    all_pairs = list(valid_pairs)
    initialized = True

    logger.info(f"VALID PAIRS FOUND: {len(all_pairs)}")
    logger.info(f"SAMPLE: {all_pairs[:10]}")

# ================= FETCH TICKERS =================
async def fetch_tickers(pairs):
    tasks = {eid: ex.fetch_tickers(pairs) for eid, ex in exchanges.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    data = {}
    for eid, res in zip(tasks.keys(), results):
        data[eid] = res if not isinstance(res, Exception) else {}

    return data

# ================= ARB ENGINE =================
def find_opportunities(tickers):
    ops = []

    for pair in all_pairs:
        data = {}

        for ex_id, tks in tickers.items():
            if pair in tks:
                t = tks[pair]

                price = t.get('last')
                if not price:
                    continue

                vol = t.get('quoteVolume') or t.get('baseVolume') or 0

                if vol < MIN_VOLUME_USD:
                    continue

                data[ex_id] = {
                    'ask': t.get('ask', price),
                    'bid': t.get('bid', price),
                    'vol': vol
                }

        if len(data) < 2:
            continue

        buy_ex = min(data, key=lambda x: data[x]['ask'])
        sell_ex = max(data, key=lambda x: data[x]['bid'])

        buy = data[buy_ex]['ask']
        sell = data[sell_ex]['bid']

        if buy >= sell:
            continue

        buy_fee = FEES.get(buy_ex, (0.1,0.1))[1]/100
        sell_fee = FEES.get(sell_ex, (0.1,0.1))[1]/100

        buy_adj = buy * (1 + buy_fee)
        sell_adj = sell * (1 - sell_fee)

        spread = ((sell_adj - buy_adj) / buy_adj) * 100

        if spread < MIN_SPREAD:
            continue

        ops.append({
            "pair": pair,
            "buy_exchange": buy_ex,
            "sell_exchange": sell_ex,
            "buy_price": buy,
            "sell_price": sell,
            "spread": round(spread, 2),
            "liquidity": min(data[buy_ex]['vol'], data[sell_ex]['vol']),
            "profit_100": round(100 * spread / 100, 2),
            "age": "now"
        })

    return sorted(ops, key=lambda x: x['spread'], reverse=True)[:20]

# ================= SCANNER LOOP =================
async def scanner():
    global opportunities, last_update

    await load_markets()

    while True:
        try:
            tickers = await fetch_tickers(all_pairs)

            ops = find_opportunities(tickers)

            with lock:
                opportunities = ops
                last_update = datetime.now()

            logger.info(f"Found {len(ops)} opportunities")

            await asyncio.sleep(REFRESH_SECONDS)

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(3)

# ================= THREAD LOOP FIX =================
def run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scanner())

# ================= FLASK =================
HTML = """
<h1>Crypto Arb Scanner</h1>
<p>Opportunities: {{count}}</p>
"""

@app.route("/")
def home():
    with lock:
        return render_template_string(HTML, count=len(opportunities))

@app.route("/api/opportunities")
def api():
    with lock:
        return jsonify({
            "opportunities": opportunities,
            "count": len(opportunities),
            "pairs": len(all_pairs),
            "time": str(last_update)
        })

# ================= START =================
if __name__ == "__main__":
    logger.info(f"Starting exchanges: {list(exchanges.keys())}")

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
