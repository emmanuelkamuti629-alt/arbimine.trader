from gevent import monkey
monkey.patch_all()

import time
import os
from flask import Flask
from flask_socketio import SocketIO
import ccxt
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

exchanges = {
    "mexc": ccxt.mexc({"enableRateLimit": True}),
    "kucoin": ccxt.kucoin({"enableRateLimit": True}),
    "gateio": ccxt.gateio({"enableRateLimit": True}),
    "coinex": ccxt.coinex({"enableRateLimit": True})
}

for name, ex in exchanges.items():
    try:
        ex.load_markets()
        logging.info(f"Loaded {name}: {len(ex.markets)} markets")
    except Exception as e:
        logging.error(f"Failed to load {name}: {e}")

# =============================
# SYMBOLS - All coins from your lists
# =============================
symbols = [
    # Majors
    "BTC/USDT", "ETH/USDT", "USDT/USDC", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "TRX/USDT", "BCH/USDT",
    
    # L1s / L2s
    "AVAX/USDT", "DOT/USDT", "ATOM/USDT", "NEAR/USDT", "APT/USDT", "SUI/USDT",
    "SEI/USDT", "INJ/USDT", "FTM/USDT", "ICP/USDT", "THETA/USDT", "KAS/USDT",
    "ARB/USDT", "OP/USDT", "MATIC/USDT", "POL/USDT", "STX/USDT",
    
    # DeFi
    "LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT", "LDO/USDT", "RUNE/USDT",
    "SNX/USDT", "GRT/USDT", "DYDX/USDT", "GMX/USDT", "JUP/USDT",
    
    # Storage / Infra / AI
    "FIL/USDT", "RNDR/USDT", "IMX/USDT", "ARKM/USDT", "WLD/USDT",
    
    # Memes
    "SHIB/USDT", "PEPE/USDT", "FLOKI/USDT", "WIF/USDT", "BONK/USDT", "LUNC/USDT",
    
    # Misc
    "LTC/USDT"
]

ORDERBOOKS = {}
MIN_PROFIT = 0.05 # After fees
MAX_AGE = 10 # Ignore data older than 10s

FEES = {
    "mexc": 0.001,
    "kucoin": 0.001,
    "gateio": 0.002,
    "coinex": 0.002
}

def update_orderbook(exchange, symbol, bid, ask, bid_vol, ask_vol):
    key = f"{exchange}:{symbol}"
    ORDERBOOKS[key] = {
        "bid": bid,
        "ask": ask,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "time": time.time()
    }

def stream_data():
    while True:
        for ex_name, ex in exchanges.items():
            for sym in symbols:
                try:
                    if sym not in ex.markets:
                        continue
                    
                    orderbook = ex.fetch_order_book(sym, limit=5)
                    bids = orderbook.get("bids")
                    asks = orderbook.get("asks")

                    if bids and asks and bids[0] and asks[0]:
                        update_orderbook(
                            ex_name, sym, 
                            bids[0][0], asks[0][0], # price
                            bids[0][1], asks[0][1] # volume
                        )
                    
                except ccxt.RateLimitExceeded:
                    logging.warning(f"{ex_name} rate limited. Sleeping 10s")
                    socketio.sleep(10)
                except Exception as e:
                    logging.warning(f"{ex_name} {sym} error: {e}")
                
                socketio.sleep(0.3) # 0.3s * 200 pairs = 60s per full loop
        
        socketio.sleep(1)

def detect_arbitrage():
    now = time.time()
    grouped = {}
    results = []

    for k, v in ORDERBOOKS.items():
        if now - v["time"] > MAX_AGE:
            continue
        try:
            ex, sym = k.split(":", 1)
            grouped.setdefault(sym, {})[ex] = v
        except:
            continue

    for sym, data in grouped.items():
        ex_list = list(data.keys())
        for buy_ex in ex_list:
            for sell_ex in ex_list:
                if buy_ex == sell_ex:
                    continue

                buy_data = data[buy_ex]
                sell_data = data[sell_ex]
                
                buy = buy_data["ask"]
                sell = sell_data["bid"]
                
                if not buy or not sell:
                    continue

                fee_cost = (FEES.get(buy_ex, 0) + FEES.get(sell_ex, 0)) * 100
                profit = ((sell - buy) / buy) * 100 - fee_cost
                
                # Basic slippage check: need $100 liquidity on both sides
                min_liq_usd = 100
                buy_liq = buy * buy_data["ask_vol"]
                sell_liq = sell * sell_data["bid_vol"]

                if MIN_PROFIT < profit < 5 and buy_liq > min_liq_usd and sell_liq > min_liq_usd:
                    results.append({
                        "symbol": sym,
                        "buy": buy_ex,
                        "sell": sell_ex,
                        "profit": round(profit, 4),
                        "buy_price": buy,
                        "sell_price": sell
                    })

    return sorted(results, key=lambda x: x["profit"], reverse=True)

HTML = """
<!DOCTYPE html>
<html>
<head><title>Real Arbitrage Scanner</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
</head>
<body style="background:#0f0f0f;color:white;font-family:Arial;padding:20px">
<h2>🔥 REAL Arbitrage Scanner (LIVE)</h2>
<div id="status" style="color:#888;margin-bottom:10px">Waiting for data...</div>
<div id="data"></div>
<script>
const socket = io();
socket.on("update", function(data) {
    document.getElementById("status").innerHTML = `Last update: ${new Date().toLocaleTimeString()} | Pairs tracking: ${data.pairs_checked} | Opportunities: ${data.opportunities.length}`;
    let html = "";
    if(data.opportunities.length === 0){
        html = "<p>No real arbitrage opportunities above 0.05% after fees</p>";
    }
    data.opportunities.forEach(d => {
        html += `
        <div style="margin:10px;padding:10px;border:1px solid #333;border-radius:8px">
            <b>${d.symbol}</b><br>
            BUY: ${d.buy} @ ${d.buy_price} → SELL: ${d.sell} @ ${d.sell_price}<br>
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

def push_loop():
    while True:
        arbs = detect_arbitrage()
        socketio.emit("update", {
            "opportunities": arbs,
            "pairs_checked": len(ORDERBOOKS)
        })
        socketio.sleep(2)

socketio.start_background_task(stream_data)
socketio.start_background_task(push_loop)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
