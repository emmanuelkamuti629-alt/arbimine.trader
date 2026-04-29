from gevent import monkey
monkey.patch_all()

import time
import random
import threading
import os
from flask import Flask
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

exchanges = ["mexc", "kucoin", "gateio", "coinex"]
symbols = ["BTC/USDT", "ETH/USDT"]
ORDERBOOKS = {}
MIN_PROFIT = 0.3

def update_orderbook(exchange, symbol, bid, ask):
    key = f"{exchange}:{symbol}"
    ORDERBOOKS[key] = {
        "bid": bid,
        "ask": ask,
        "time": time.time()
    }

def stream_data():
    while True:
        for ex in exchanges:
            for sym in symbols:
                base = 100 + random.random() * 10
                bid = base
                ask = base + random.random() * 0.5
                update_orderbook(ex, sym, bid, ask)
        socketio.sleep(0.3)

def detect_arbitrage():
    grouped = {}
    results = []
    for k, v in ORDERBOOKS.items():
        ex, sym = k.split(":")
        grouped.setdefault(sym, {})[ex] = v
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
                profit = ((sell - buy) / buy) * 100
                if profit > MIN_PROFIT:
                    results.append({
                        "symbol": sym,
                        "buy": buy_ex,
                        "sell": sell_ex,
                        "profit": round(profit, 3)
                    })
    return results

HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Arbitrage Dashboard</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
</head>
<body style="background:#0f0f0f;color:white;font-family:Arial">
<h2>🔥 Institutional Arbitrage Scanner</h2>
<div id="data"></div>
<script>
const socket = io();
socket.on("update", function(data) {
    let html = "<h3>Live Opportunities</h3>";
    if(data.length === 0){
        html += "<p>No opportunities</p>";
    }
    data.forEach(d => {
        html += `
        <div style="margin:10px;padding:10px;border:1px solid #333">
            <b>${d.symbol}</b><br>
            BUY: ${d.buy} → SELL: ${d.sell}<br>
            PROFIT: <span style="color:lime">${d.profit}%</span>
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

def push_updates():
    while True:
        data = detect_arbitrage()
        socketio.emit("update", data)
        socketio.sleep(1)

# Start background threads when gunicorn loads the app
threading.Thread(target=stream_data, daemon=True).start()
threading.Thread(target=push_updates, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
