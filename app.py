from gevent import monkey
monkey.patch_all()

import time
import os
import logging
from flask import Flask
from flask_socketio import SocketIO
import ccxt
from concurrent.futures import ThreadPoolExecutor

# =============================
# SETUP
# =============================
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

executor = ThreadPoolExecutor(max_workers=20)

# =============================
# EXCHANGES
# =============================
exchanges = {
    "mexc": ccxt.mexc({"enableRateLimit": True}),
    "kucoin": ccxt.kucoin({"enableRateLimit": True}),
    "gateio": ccxt.gateio({"enableRateLimit": True}),
    "coinex": ccxt.coinex({"enableRateLimit": True})
}

for name, ex in exchanges.items():
    try:
        ex.load_markets()
        logging.info(f"{name} markets loaded: {len(ex.markets)}")
    except Exception as e:
        logging.error(f"{name} failed: {e}")

# =============================
# BUILD COMMON SYMBOL LIST (IMPORTANT FIX)
# =============================
common_symbols = set(exchanges["mexc"].markets)

for ex in exchanges.values():
    common_symbols &= set(ex.markets)

symbols = list(common_symbols)

logging.info(f"Common symbols found: {len(symbols)}")

# fallback if too small
if len(symbols) < 10:
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

# =============================
# CONFIG
# =============================
ORDERBOOKS = {}
MIN_PROFIT = 0.01   # lowered for detection
MAX_AGE = 10

FEES = {
    "mexc": 0.001,
    "kucoin": 0.001,
    "gateio": 0.002,
    "coinex": 0.002
}

# =============================
# ORDERBOOK STORAGE
# =============================
def update_orderbook(exchange, symbol, bid, ask, bid_vol, ask_vol):
    ORDERBOOKS[f"{exchange}:{symbol}"] = {
        "bid": bid,
        "ask": ask,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "time": time.time()
    }

# =============================
# FETCH DATA (THREAD SAFE)
# =============================
def fetch_orderbook(ex_name, ex, sym):
    try:
        if sym not in ex.markets:
            return

        ob = ex.fetch_order_book(sym, limit=5)
        bids = ob.get("bids")
        asks = ob.get("asks")

        if bids and asks and bids[0] and asks[0]:
            update_orderbook(
                ex_name,
                sym,
                bids[0][0],
                asks[0][0],
                bids[0][1],
                asks[0][1]
            )

    except Exception as e:
        logging.warning(f"{ex_name} {sym}: {e}")

# =============================
# STREAM LOOP
# =============================
def stream_data():
    while True:
        tasks = []

        for ex_name, ex in exchanges.items():
            for sym in symbols:
                tasks.append(executor.submit(fetch_orderbook, ex_name, ex, sym))

        # allow tasks to complete
        for t in tasks:
            try:
                t.result(timeout=5)
            except:
                pass

        socketio.sleep(1)

# =============================
# ARBITRAGE DETECTOR
# =============================
def detect_arbitrage():
    now = time.time()
    grouped = []
    results = []

    data = {}

    for k, v in ORDERBOOKS.items():
        if now - v["time"] > MAX_AGE:
            continue
        try:
            ex, sym = k.split(":", 1)
            data.setdefault(sym, {})[ex] = v
        except:
            continue

    for sym, ex_data in data.items():
        ex_list = list(ex_data.keys())

        for buy_ex in ex_list:
            for sell_ex in ex_list:
                if buy_ex == sell_ex:
                    continue

                buy = ex_data[buy_ex]["ask"]
                sell = ex_data[sell_ex]["bid"]

                if not buy or not sell:
                    continue

                fee = (FEES.get(buy_ex, 0) + FEES.get(sell_ex, 0)) * 100
                profit = ((sell - buy) / buy) * 100 - fee

                buy_liq = buy * ex_data[buy_ex]["ask_vol"]
                sell_liq = sell * ex_data[sell_ex]["bid_vol"]

                if profit > MIN_PROFIT and buy_liq > 100 and sell_liq > 100:
                    results.append({
                        "symbol": sym,
                        "buy": buy_ex,
                        "sell": sell_ex,
                        "profit": round(profit, 4),
                        "buy_price": buy,
                        "sell_price": sell
                    })

    return sorted(results, key=lambda x: x["profit"], reverse=True)

# =============================
# FRONTEND
# =============================
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Arbitrage Scanner</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
</head>
<body style="background:#0f0f0f;color:white;font-family:Arial;padding:20px">

<h2>🔥 Live Arbitrage Scanner</h2>
<div id="status">Loading...</div>
<div id="data"></div>

<script>
const socket = io();

socket.on("update", function(data) {
    document.getElementById("status").innerHTML =
        `Pairs: ${data.pairs} | Opportunities: ${data.opps.length}`;

    let html = "";

    if (data.opps.length === 0) {
        html = "<p>No opportunities right now</p>";
    }

    data.opps.forEach(d => {
        html += `
        <div style="margin:10px;padding:10px;border:1px solid #333">
            <b>${d.symbol}</b><br>
            BUY: ${d.buy} @ ${d.buy_price} → SELL: ${d.sell} @ ${d.sell_price}<br>
            <span style="color:lime">PROFIT: ${d.profit}%</span>
        </div>`;
    });

    document.getElementById("data").innerHTML = html;
});
</script>

</body>
</html>
"""

@app.route("/")
def home():
    return HTML

# =============================
# PUSH LOOP
# =============================
def push_loop():
    while True:
        arbs = detect_arbitrage()

        socketio.emit("update", {
            "opps": arbs,
            "pairs": len(ORDERBOOKS)
        })

        socketio.sleep(2)

# =============================
# START BACKGROUND TASKS
# =============================
socketio.start_background_task(stream_data)
socketio.start_background_task(push_loop)

# =============================
# RUN
# =============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
