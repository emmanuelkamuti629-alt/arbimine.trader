import asyncio
import json
import os
import ccxt.pro as ccxt
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

class WSArbitrageScanner:
    def __init__(self):
        with open('config.json', 'r') as f:
            self.config = json.load(f)
        self.exchanges = {}
        self.prices = {} # {symbol: {exchange: {bid, ask, ts}}}
        self.symbols = [f"{coin}/USDT" for coin in self.config['coins']]
        self.min_spread = self.config['min_spread']

    async def init_exchanges(self):
        for ex_id in ['mexc', 'kucoin', 'gateio', 'bitget']: # coinex has no WS in ccxt.pro
            try:
                exchange_class = getattr(ccxt, ex_id)
                self.exchanges[ex_id] = exchange_class({'enableRateLimit': True})
                print(f"[+] {ex_id} WS ready")
            except Exception as e:
                print(f"[-] {ex_id} WS failed: {e}")

    async def watch_symbol(self, ex_id, exchange, symbol):
        while True:
            try:
                ticker = await exchange.watch_ticker(symbol)
                if symbol not in self.prices:
                    self.prices[symbol] = {}
                self.prices[symbol][ex_id] = {
                    'bid': ticker['bid'],
                    'ask': ticker['ask'],
                    'ts': datetime.now()
                }
                await self.check_arb(symbol)
            except Exception as e:
                print(f"WS error {ex_id} {symbol}: {e}")
                await asyncio.sleep(5)

    async def check_arb(self, symbol):
        if symbol not in self.prices or len(self.prices[symbol]) < 2:
            return

        ex_data = self.prices[symbol]
        for buy_ex, buy in ex_data.items():
            for sell_ex, sell in ex_data.items():
                if buy_ex == sell_ex: continue

                # Skip if data is stale >3s
                if (datetime.now() - buy['ts']).seconds > 3: continue

                buy_price = buy['ask']
                sell_price = sell['bid']
                fee = self.config['fees'].get(buy_ex, 0.001) + self.config['fees'].get(sell_ex, 0.001) + 0.002

                spread = (sell_price - buy_price) / buy_price - fee
                if spread > self.min_spread:
                    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {symbol} | "
                          f"Buy {buy_ex} @ {buy_price:.6f} -> Sell {sell_ex} @ {sell_price:.6f} | "
                          f"Net: {spread*100:.2f}%")

    async def run(self):
        await self.init_exchanges()

        tasks = []
        for ex_id, exchange in self.exchanges.items():
            markets = await exchange.load_markets()
            for symbol in self.symbols:
                if symbol in markets:
                    tasks.append(self.watch_symbol(ex_id, exchange, symbol))

        print(f"[WS SCANNER STARTED] Watching {len(tasks)} streams\n")
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    scanner = WSArbitrageScanner()
    try:
        asyncio.run(scanner.run())
    except KeyboardInterrupt:
        print("\nShutting down...")
