import asyncio
import json
import ccxt.async_support as ccxt

async def fetch_withdrawal_fees():
    exchanges = {
        'mexc': ccxt.mexc(),
        'gateio': ccxt.gateio(),
        'bitget': ccxt.bitget()
    }

    fee_data = {}

    for ex_id, exchange in exchanges.items():
        try:
            await exchange.load_markets()
            currencies = await exchange.fetch_currencies()
            fee_data[ex_id] = {}

            for code, currency in currencies.items():
                if not currency.get('active') or code == 'USDT':
                    continue

                networks = currency.get('networks', {})
                best_net = None
                best_fee = float('inf')

                # Priority: TRC20 > BEP20 > SOL > TON > ERC20
                priority = ['TRC20', 'BEP20', 'SOL', 'TON', 'ERC20']
                for net in priority:
                    if net in networks and networks[net].get('withdraw', False):
                        fee = networks[net].get('fee', float('inf'))
                        if fee < best_fee:
                            best_fee = fee
                            best_net = net

                if best_net:
                    fee_data[ex_id][code] = {
                        'network': best_net,
                        'fee': best_fee,
                        'withdraw_enabled': networks[best_net]['withdraw']
                    }

            print(f"[+] {ex_id}: fetched {len(fee_data[ex_id])} withdrawal fees")
        except Exception as e:
            print(f"[-] {ex_id} failed: {e}")
        finally:
            await exchange.close()

    with open('withdrawal_fees.json', 'w') as f:
        json.dump(fee_data, f, indent=2)
    print("\nSaved to withdrawal_fees.json")

if __name__ == "__main__":
    asyncio.run(fetch_withdrawal_fees())
