# app.py
import os
import time
from datetime import datetime
import ccxt
from dotenv import load_dotenv
from threading import Lock

load_dotenv()

# Initialize Binance Spot
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_API_SECRET'),
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'},
})

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
TAKER_FEE = 0.001
MIN_PROFIT_PCT = 0.6
TRADE_AMOUNT_USDT = 10.0  # 10 USDT per arbitrage
LOG_OPPORTUNITIES = 10    # keep last N opportunities in memory
LOG_TRADES = 100          # keep last N executed trades

# ----------------------------------------------------------------------
# Global stats (what you asked for)
# ----------------------------------------------------------------------
total_profit_usdt = 0.0
total_trades_executed = 0

# Global logs
opportunitites_log = []
executed_trades_log = []

# Global lock: only one trade at a time
execute_lock = Lock()

# Example triangles (USDT → asset → asset → USDT)
# You can later auto‑generate these from exchange.load_markets()
triangles = [
    {
        'id': 'USDT→BTC→ETH→USDT',
        'legs': [
            {'symbol': 'BTCUSDT', 'side': 'buy'},   # buy BTC with USDT
            {'symbol': 'ETHBTC',  'side': 'buy'},   # buy ETH with BTC
            {'symbol': 'ETHUSDT', 'side': 'sell'},  # sell ETH for USDT
        ],
    },
    {
        'id': 'USDT→BTC→BNB→USDT',
        'legs': [
            {'symbol': 'BTCUSDT', 'side': 'buy'},
            {'symbol': 'BNBBTC',  'side': 'buy'},
            {'symbol': 'BNBUSDT', 'side': 'sell'},
        ],
    },
    # Add more triangles here later
]

# =============================================================================
# Balance + opportunities logic
# =============================================================================

def get_usdt_balance():
    """Fetch and print USDT balance."""
    balance = exchange.fetch_balance()
    free = balance['free'].get('USDT', 0.0)
    total = balance['total'].get('USDT', 0.0)
    print(f"📊 USDT balance: free={free:.2f}, total={total:.2f}")
    return free

def fetch_ticker_safe(symbol):
    """Fetch ticker with error handling."""
    try:
        t = exchange.fetch_ticker(symbol)
        return {'bid': t['bid'], 'ask': t['ask']}
    except Exception as e:
        print(f"⚠️ Error fetching {symbol}: {e}")
        return None

def calc_net_profit(triangle, prices):
    """
    Simulate 10 USDT through a triangle and return profit % and final USDT.
    Returns (final_usdt, profit_pct) or (None, None) if error.
    """
    amount = 10.0  # base in USDT

    for leg in triangle['legs']:
        s = leg['symbol']
        if s not in prices or prices[s] is None:
            return None, None

        p = prices[s]
        if leg['side'] == 'buy':
            price = p['ask']  # we pay ask
            amount = amount / price  # buy quote asset
        else:  # sell
            price = p['bid']  # we receive at bid
            amount = amount * price  # sell base asset

        # Apply taker fee
        amount = amount * (1 - TAKER_FEE)

    profit_pct = (amount - 10.0) / 10.0 * 100.0
    return amount, profit_pct

def log_opportunities(opportunities):
    """Show all opportunities ≥ 0.6% and append to opportunitites_log."""
    if not opportunities:
        print("📉 No profitable opportunities ≥ 0.6%")
        return

    print("🔍 Available arbitrage opportunities:")
    for i, opp in enumerate(opportunities[:LOG_OPPORTUNITIES]):
        tri_id = opp['triangle']['id']
        profit = opp['profit_pct']
        print(f"  {i+1}. {tri_id} → {profit:.3f}%")

    # Keep latest opportunities in memory
    opportunitites_log.clear()
    for opp in opportunities:
        opportunitites_log.append({
            'timestamp': datetime.utcnow().isoformat(),
            'triangle_id': opp['triangle']['id'],
            'profit_pct': opp['profit_pct'],
        })

def execute_triangle(best):
    """Execute one 10 USDT triangle and update logs + stats."""
    global total_profit_usdt, total_trades_executed, executed_trades_log

    if not execute_lock.acquire(blocking=False):
        print("⚙️ Another trade already running, skipping execution.")
        return

    try:
        free_usdt = get_usdt_balance()
        if free_usdt < TRADE_AMOUNT_USDT:
            print("❌ Balance insufficient for 10 USDT trade. Will not execute.")
            return

        print(f"🚀 Executing best opportunity: {best['triangle']['id']} "
              f"({best['profit_pct']:.3f}%)")

        amount = TRADE_AMOUNT_USDT
        tri = best['triangle']

        for i, leg in enumerate(tri['legs']):
            symbol = leg['symbol']
            side = leg['side']

            order = exchange.create_market_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=amount,
            )

            if not order or order.get('status') != 'closed':
                print(f"🚨 Trade failed at leg {i+1}: {symbol}, {side}")
                return

            if side == 'buy':
                amount = order['filled']  # now base asset
            else:
                amount = order['cost']     # received in quote / USDT

        # At the end, amount is roughly the final USDT received
        final_usdt = amount
        profit_usdt = final_usdt - TRADE_AMOUNT_USDT

        # Update global stats
        total_profit_usdt += profit_usdt
        total_trades_executed += 1

        # Log this trade
        executed_trades_log.append({
            'timestamp': datetime.utcnow().isoformat(),
            'triangle_id': best['triangle']['id'],
            'start_usdt': 10.0,
            'final_usdt': final_usdt,
            'profit_usdt': profit_usdt,
            'profit_pct': best['profit_pct'],
        })

        # Keep only last N trades
        if len(executed_trades_log) > LOG_TRADES:
            executed_trades_log = executed_trades_log[-LOG_TRADES:]

        print(f"✅ Trade completed: started 10 USDT, ended ≈ {final_usdt:.4f} USDT")
        print(f"   Profit: {profit_usdt:+.4f} USDT")
        print(f"📊 TOTAL so far: {total_trades_executed} trades, "
              f"{total_profit_usdt:+.4f} USDT net profit")

    finally:
        execute_lock.release()

# =============================================================================
# Main loop (spot triangular arbitrage only)
# =============================================================================

def main_loop():
    """Main scan → execute → repeat loop."""
    global total_profit_usdt, total_trades_executed

    print("🚀 Binance Spot Triangular Arbitrage Bot (USDT start & end, 10 USDT per trade)")
    print(f"  Minimum profit: {MIN_PROFIT_PCT}% per triangle")
    print(f"  Trade size: {TRADE_AMOUNT_USDT} USDT (1 trade at a time)")

    while True:
        # 1. Get latest prices for all relevant symbols
        prices = {}
        all_symbols = set()
        for tri in triangles:
            for leg in tri['legs']:
                all_symbols.add(leg['symbol'])

        for sym in all_symbols:
            prices[sym] = fetch_ticker_safe(sym)

        # 2. Find opportunities ≥ 0.6%
        opportunities = []
        for tri in triangles:
            final, profit_pct = calc_net_profit(tri, prices)
            if profit_pct is not None and profit_pct >= MIN_PROFIT_PCT:
                opportunities.append({
                    'triangle': tri,
                    'final_usdt': final,
                    'profit_pct': profit_pct,
                })

        # 3. Always show and log opportunities
        log_opportunities(opportunities)

        # 4. Show current stats
        free_usdt = get_usdt_balance()
        print(f"📊 SESSION STATS: {total_trades_executed} trades, "
              f"{total_profit_usdt:+.4f} USDT total profit")

        # 5. Execute ONLY if balance >= 10 USDT and opportunities exist
        if free_usdt < 10.0:
            print("⚠️ Balance insufficient for 10 USDT trade. Waiting for deposit...")
        else:
            if opportunities:
                best = max(opportunities, key=lambda x: x['profit_pct'])
                execute_triangle(best)

        time.sleep(1)  # Adjust as needed (0.5 for faster, 1–2 for safer)

if __name__ == "__main__":
    main_loop()
