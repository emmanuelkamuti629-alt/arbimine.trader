import asyncio
import json
import os
import ccxt.async_support as ccxt
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

class ArbitrageScanner:
    def __init__(self):
        self.exchanges = {}
        self.config = self.load_config()
        self.symbols = [f"{coin}/USDT" for coin in self.config['coins']]
        self.min_spread = self.config['min_spread']
        self.telegram_token = os.getenv('TELEGRAM_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

    def load_config(self):
        with open('config.json', 'r') as f:
            return json.load(f)

    async def init_exchanges(self):
        exchange_list = ['mexc', 'kucoin', 'gateio', 'coinex', 'bitget']
        for ex_id in exchange_list:
            try:
                exchange_class = getattr(ccxt, ex_id)
                self.exchanges[ex_id] = exchange_class({
                    'apiKey': os.getenv(f'{ex_id.upper()}_API_KEY', ''),
                    'secret': os.getenv(f'{ex_id.upper()}_SECRET', ''),
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'}
                })
                await self.exchanges[ex_id].load_markets()
                print(f"[+] {ex_id} connected")
            except Exception as e:
                print(f"[-] {ex_id} failed: {e}")

    async def fetch_ticker(self, exchange, ex_id, symbol):
        try:
            ticker = await exchange.fetch_ticker(symbol)
            return ex_id, symbol, ticker['bid'], ticker['ask'], ticker['quoteVolume']
        except:
            return ex_id, symbol, None, None, None

    async def scan_once(self):
        tasks = []
        for ex_id, exchange in self.exchanges.items():
            for symbol in self.symbols:
                if symbol in exchange.markets:
                    tasks.append(self.fetch_ticker(exchange, ex_id, symbol))

        results = await asyncio.gather(*tasks)

        # Organize by symbol
        data = {}
        for ex_id, symbol, bid, ask, vol in results:
            if bid is None or vol < self.config['min_volume_usd']: continue
            if symbol not in data: data[symbol] = {}
            data[symbol][ex_id] = {'bid': bid, 'ask': ask}

        # Find opportunities
        opps = []
        for symbol, ex_data in data.items():
            for buy_ex, buy in ex_data.items():
                for sell_ex, sell in ex_data.items():
                    if buy_ex == sell_ex: continue

                    buy_price = buy['ask']
                    sell_price = sell['bid']
                    fee = self.config['fees'].get(buy_ex, 0.001) + self.config['fees'].get(sell_ex, 0.001) + 0.002

                    spread = (sell_price - buy_price) / buy_price - fee
                    if spread > self.min_spread:
                        opps.append({
                            'symbol': symbol,
                            'buy_ex': buy_ex, 'buy_price': buy_price,
                            'sell_ex': sell_ex, 'sell_price': sell_price,
                            'spread': spread,
                            'time': datetime.now().strftime('%H:%M:%S')
                        })
        return opps

    async def send_telegram(self, msg):
        if not self.telegram_token: return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={'chat_id': self.telegram_chat_id, 'text': msg, 'parse_mode': 'Markdown'})

    async def run(self):
        await self.init_exchanges()
        print(f"\n[SCANNER STARTED] {len(self.symbols)} pairs | Min spread: {self.min_spread*100:.1f}%\n")

        while True:
            try:
                opportunities = await self.scan_once()
                for opp in opportunities:
                    msg = f"*ARB FOUND* `{opp['time']}`\n" \
                          f"`{opp['symbol']}`\n" \
                          f"Buy *{opp['buy_ex']}* @ `${opp['buy_price']:.6f}`\n" \
                          f"Sell *{opp['sell_ex']}* @ `${opp['sell_price']:.6f}`\n" \
                          f"Net Spread: *{opp['spread']*100:.2f}%*"
                    print(msg.replace('*','').replace('`',''))
                    await self.send_telegram(msg)

                await asyncio.sleep(self.config['scan_interval'])
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(10)

    async def close(self):
        for exchange in self.exchanges.values():
            await exchange.close()

if __name__ == "__main__":
    scanner = ArbitrageScanner()
    try:
        asyncio.run(scanner.run())
    except KeyboardInterrupt:
        print("\nShutting down...")
        asyncio.run(scanner.close())
