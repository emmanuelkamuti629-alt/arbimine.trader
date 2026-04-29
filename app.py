from gevent import monkey
monkey.patch_all()

import time
import os
from flask import Flask
from flask_socketio import SocketIO
import ccxt

# =============================
# APP SETUP
# =============================
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# =============================
# EXCHANGES
# =============================
exchanges = {
    "mexc": ccxt.mexc({"enableRateLimit": True}),
    "kucoin": ccxt.kucoin({"enableRateLimit": True}),
    "gateio": ccxt.gateio({"enableRateLimit": True}),
    "coinex": ccxt.coinex({"enableRateLimit": True})
}

# Load markets
for ex in exchanges.values():
    ex.load_markets()

# =============================
# SYMBOLS
# =============================
symbols = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT",
    "TRX/USDT", "DOT/USDT", "UNI/USDT", "ATOM/USDT", "NEAR/USDT"
]

# =============================
# DATA STORAGE
# =============================
ORDERBOOKS = {}

# realistic minimum arbitrage threshold
MIN_PROFIT = 0.05

# trading fees (important for REAL profit)
FEES = {
    "mexc": 0.001,
    "kucoin": 0.001,
    "gateio": 0.002,
    "coinex": 0.002
}

# =============================
# UPDATE ORDERBOOK
# =============================
def update_orderbook(exchange, symbol, bid, ask):
    key = f"{exchange}:{symbol}"
    ORDERBOOKS[key] = {
        "bid": bid,
        "ask": ask,
        "time": time.time()
    }

# =============================
# LIVE DATA STREAM
# =============================
def stream_data():
    while True:
        for ex_name, ex in exchanges.items():
            try:
                for sym in symbols:

                    if sym not in ex.markets:
                        continue

                    orderbook = ex.fetch_order_book(sym, limit=20)

                    bids = orderbook.get("bids")
                    asks = orderbook.get("asks")

                    if not bids or not asks:
                        continue

                    bid = bids[0][0]
                    ask = asks[0][0]

                    update_orderbook(ex_name, sym, bid, ask)

            except Exception:
                continue

        socketio.sleep(3)

# =============================
# ARBITRAGE ENGINE
# =============================
def detect_arbitrage():
    grouped = {}
    results = []

    for k, v in ORDERBOOKS.items():
        try:
            ex, sym = k.split(":")
            grouped.setdefault(sym, {})[ex] = v
        except:
            continue

    for sym, data in grouped.items():
        ex_list = list(data.keys())

        for buy_ex in ex_list:
            for sell_ex in ex_list:

                if buy_ex == sell_ex:
                    continue

                buy = data[buy_ex]["ask"]
                sell = data[sell_ex]["bid"]

                if not buy or not sell:
                    continue

                # fee-adjusted profit
                fee_cost = (FEES.get(buy_ex, 0) + FEES.get(sell_ex, 0)) * 100

                profit = ((sell - buy) / buy) * 100 - fee_cost

                if MIN_PROFIT < profit < 5:
                    results.append({
                        "symbol": sym,
                        "buy": buy_ex,
                        "sell": sell_ex,
                        "profit": round(profit, 4)
                    })

    return sorted(results, key=lambda x: x["profit"], reverse=True)

# =============================
# FRONTEND
# =============================
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Real Arbitrage Scanner</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
</head>

<body style="background:#0f0f0f;color:white;font-family:Arial">
<h2>🔥 REAL Arbitrage Scanner (LIVE)</h2>
<div id="data"></div>

<script>
const socket = io();

socket.on("update", function(data) {
    let html = "";

    if(data.length === 0){
        html = "<p>No real arbitrage opportunities</p>";
    }

    data.forEach(d => {
        html += `
        <div style="margin:10px;padding:10px;border:1px solid #333">
            <b>${d.symbol}</b><br>
            BUY: ${d.buy} → SELL: ${d.sell}<br>
            PROFIT: <span style="color:lime;font-weight:bold">${d.profit}%</span>
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
# BACKGROUND TASKS
# =============================
socketio.start_background_task(stream_data)
socketio.start_background_task(
    lambda: (
        socketio.emit("update", detect_arbitrage()),
        time.sleep(2)
    )
)

def push_loop():
    while True:
        socketio.emit("update", detect_arbitrage())
        socketio.sleep(2)

socketio.start_background_task(push_loop)

# =============================
# RUN
# =============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
