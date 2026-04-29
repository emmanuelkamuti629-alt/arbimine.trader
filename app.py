import eventlet
eventlet.monkey_patch()

import time
import ccxt
import os
import threading
from flask import Flask
from flask_socketio import SocketIO

# =============================
# CONFIG
# =============================
exchanges = {
    "binance": ccxt.binance({"enableRateLimit": True}),
    "kucoin": ccxt.kucoin({"enableRateLimit": True}),
    "gateio": ccxt.gateio({"enableRateLimit": True}),
    "mexc": ccxt.mexc({"enableRateLimit": True})
}

ORDERBOOKS = {}
LOCK = threading.Lock()

MIN_PROFIT = 0.8
FEE = 0.2
SLIPPAGE = 0.05 / 100

DEPTH_LEVEL = 5
TRADE_SIZE = 0.5
MAX_STALE_TIME = 6

# =============================
# FLASK
# =============================
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# =============================
# SYMBOLS
# =============================
def get_top_symbols():
    try:
        markets = exchanges["binance"].load_markets()
        return [
            s for s in markets
            if s.endswith("/USDT") and markets[s]["active"]
        ][:50]
    except:
        return ["BTC/USDT", "ETH/USDT", "BNB/USDT"]

SYMBOLS = get_top_symbols()

# =============================
# EXECUTION PRICE (REAL DEPTH)
# =============================
def execution_price(book, side):
    depth = book.get(side, [])
    if not depth:
        return 0

    remaining = TRADE_SIZE
    cost = 0

    for price, qty in depth[:DEPTH_LEVEL]:
        take = min(qty, remaining)
        cost += take * price
        remaining -= take
        if remaining <= 0:
            break

    if remaining > 0:
        return 0

    return cost / TRADE_SIZE

# =============================
# SAFE FETCH (STABLE)
# =============================
def safe_fetch(exchange, symbol, ex_name):
    try:
        ob = exchange.fetch_order_book(symbol)

        if ob.get("bids") and ob.get("asks"):
            with LOCK:
                ORDERBOOKS[f"{ex_name}:{symbol}"] = {
                    "bids": ob["bids"],
                    "asks": ob["asks"],
                    "time": time.time()
                }
    except:
        socketio.sleep(0.15)

# =============================
# FETCH LOOP (RATE SAFE)
# =============================
def fetch_orderbooks():
    while True:
        for ex_name, ex in exchanges.items():
            for sym in SYMBOLS:
                safe_fetch(ex, sym, ex_name)
                socketio.sleep(0.08)   # safe pacing

            socketio.sleep(0.5)

        socketio.sleep(0.3)

# =============================
# ARBITRAGE ENGINE
# =============================
def detect_arbitrage():
    results = []
    grouped = {}
    now = time.time()

    with LOCK:
        snapshot = dict(ORDERBOOKS)

    for k, v in snapshot.items():
        if now - v["time"] > MAX_STALE_TIME:
            continue

        try:
            ex, sym = k.split(":")
            grouped.setdefault(sym, {})[ex] = v
        except:
            continue

    for sym, data in grouped.items():
        if len(data) < 2:
            continue

        ex_list = list(data.keys())

        for buy_ex in ex_list:
            for sell_ex in ex_list:

                if buy_ex == sell_ex:
                    continue

                buy_book = data[buy_ex]
                sell_book = data[sell_ex]

                buy_price = execution_price(buy_book, "asks")
                sell_price = execution_price(sell_book, "bids")

                if buy_price <= 0 or sell_price <= 0:
                    continue

                spread = sell_price - buy_price
                if spread <= 0:
                    continue

                profit = (spread / buy_price) * 100
                net_profit = profit - FEE - (SLIPPAGE * 100)

                if net_profit > MIN_PROFIT:
                    results.append({
                        "symbol": sym,
                        "buy": buy_ex,
                        "sell": sell_ex,
                        "buy_price": round(buy_price, 2),
                        "sell_price": round(sell_price, 2),
                        "profit": round(net_profit, 3)
                    })

    return results

# =============================
# PUSH UPDATES
# =============================
def push_updates():
    while True:
        data = detect_arbitrage()
        socketio.emit("update", data)
        socketio.sleep(2)

# =============================
# START SYSTEM
# =============================
def start_system():
    socketio.start_background_task(fetch_orderbooks)
    socketio.start_background_task(push_updates)

# =============================
# FRONTEND
# =============================
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Arbitrage Engine</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
</head>

<body style="background:#0f0f0f;color:white;font-family:Arial">

<h2>🔥 LIVE ARBITRAGE ENGINE</h2>
<div id="data"></div>

<script>
const socket = io();

socket.on("update", function(data) {

    let html = "<h3>Opportunities</h3>";

    if (!data || data.length === 0) {
        html += "<p>No arbitrage detected</p>";
    } else {
        data.forEach(d => {
            html += `
            <div style="margin:10px;padding:10px;border:1px solid #333">
                <b>${d.symbol}</b><br>
                BUY: ${d.buy} (${d.buy_price}) → SELL: ${d.sell} (${d.sell_price})<br>
                PROFIT: <span style="color:lime">${d.profit}%</span>
            </div>`;
        });
    }

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
# RUN
# =============================
if __name__ == "__main__":
    start_system()

    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
                  )
