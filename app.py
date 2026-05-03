import os
import asyncio
import logging
import threading
from flask import Flask
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

# Fees dict (maker/taker %)
FEES = {
    'mexc': (0.0, 0.02),
    'kucoin': (0.1, 0.1),
    'coinex': (0.2, 0.2),
    'gate': (0.15, 0.15),
    'bitget': (0.1, 0.1)
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

exchanges = {id: getattr(ccxt, id)({'enableRateLimit': True}) for id in EXCHANGE_IDS}

# API keys
for name, ex in exchanges.items():
    api_key = os.getenv(f'{name.upper()}_API_KEY')
    if api_key:
        ex.apiKey = api_key
        ex.secret = os.getenv(f'{name.upper()}_SECRET')
        if name == 'kucoin':
            ex.password = os.getenv(f'{name.upper()}_PASSWORD')

PAIRS = []  # Auto-populate /USDT

async def load_pairs():
    """Load common USDT pairs across >=3 exchanges."""
    global PAIRS
    markets = await asyncio.gather(*(ex.load_markets() for ex in exchanges.values()))
    usdt_pairs = {}
    for i, m in enumerate(markets):
        name = EXCHANGE_IDS[i]
        pairs = [s for s in m if s.endswith('/USDT')]
        for p in ['BTC/USDT', 'ETH/USDT', 'XRP/USDT']:  # Your full list here
            base = p.split('/')[0]
            pair = f"{base}/USDT"
            if pair in m:
                usdt_pairs.setdefault(pair, []).append(name)
    
    PAIRS = [p for p, exs in usdt_pairs.items() if len(exs) >= 3]
    logger.info(f"Loaded {len(PAIRS)} common pairs: {PAIRS[:10]}...")

async def fetch_tickers(ex):
    try:
        return await ex.fetch_tickers(PAIRS)
    except Exception as e:
        logger.error(f"{ex.id} error: {e}")
        return {}

def calc_profitable_spread(tickers, pair):
    prices = {}
    volumes = {}
    for name, data in tickers.items():
        if pair in data:
            info = data[pair]
            prices[name] = info['last']
            volumes[name] = info.get('quoteVolume', 0)
    
    if len(prices) < 2 or min(volumes.values() or [0]) < MIN_VOLUME_USD:
        return None
    
    min_ex, max_ex = min(prices), max(prices)
    min_p, max_p = prices[min_ex], prices[max_ex]
    buy_fee, sell_fee = FEES[min_ex][1], FEES[max_ex][0]  # Taker buy, maker sell
    gross_spread = (max_p - min_p) / min_p * 100
    net_spread = ((max_p * (1 - sell_fee)) - (min_p * (1 + buy_fee))) / (min_p * (1 + buy_fee)) * 100
    
    profit_100 = 100 * net_spread / 100  # $100 trade profit
    return net_spread if net_spread >= MIN_SPREAD else None, min_ex, max_ex, profit_100

async def run_scanner():
    await load_pairs()
    while True:
        try:
            tickers_list = await asyncio.gather(*(fetch_tickers(ex) for ex in exchanges.values()))
            tickers = {EXCHANGE_IDS[i]: tickers_list[i] for i in range(len(EXCHANGE_IDS))}
            
            for pair in PAIRS:
                result = calc_profitable_spread(tickers, pair)
                if result:
                    spread, min_ex, max_ex, profit = result
                    msg = f"🚨 {pair} +{spread:.2f}% (net) | ${min_p:.4f}@{min_ex.upper()} → ${max_p:.4f}@{max_ex.upper()} | $100 → ${profit:.2f}"
                    logger.info(msg)
                    await send_telegram(msg)
            
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Loop error: {e}")
            await asyncio.sleep(10)

async def send_telegram(msg):  # Same as before
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        except:
            pass

@app.route('/')
@app.route('/health')
def health():
    return {"status": "ok", "exchanges": EXCHANGE_IDS, "pairs_count": len(PAIRS)}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Multi-ex arb scanner on {EXCHANGE_IDS} | port {port}")
    
    def run_async():
        asyncio.run(run_scanner())
    
    threading.Thread(target=run_async, daemon=True).start()
    app.run(host='0.0.0.0', port=port)
