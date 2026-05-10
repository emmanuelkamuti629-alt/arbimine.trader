import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import time
import os
import json
import httpx
import base64
from datetime import datetime, timedelta
from collections import Counter

# ================= SUPABASE SETUP =================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ================= M-PESA SETUP =================
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY", "")
MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "174379")
MPESA_CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "")
BUSINESS_SHORTCODE = os.getenv("MPESA_BUSINESS_SHORTCODE", "174379")

# ================= ARBITRAGE CONFIG =================
EXCHANGE_IDS = ["gateio", "kucoin", "mexc", "bitget", "coinex"]
MAX_COINS = 500
CACHE_FILE = "/tmp/symbols_cache.json"
CACHE_TTL = 3600

TRADING_FEES = {
    'gateio': 0.1, 'kucoin': 0.1, 'mexc': 0.1,
    'bitget': 0.1, 'coinex': 0.2,
}

MIN_PROFIT_PERCENT = 0.5
MIN_LIQUIDITY_USD = 50
BATCH_SIZE = 30

app = FastAPI(title="ArbiHunt", version="5.0")

latest_opportunities = []
all_symbols = []
exchanges = {}
exchanges_loaded = 0
scanning_active = False
scan_round = 0
initialized = False

# ================= SUBSCRIPTION PRICES =================
SUB_PRICES = {
    "weekly": {"amount": 200, "days": 7, "label": "Weekly PRO"},
    "monthly": {"amount": 700, "days": 30, "label": "Monthly PRO"},
}

# ================= SUPABASE HELPERS =================
def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

async def supabase_post(table: str, data: dict):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            json=data,
            headers={**supabase_headers(), "Prefer": "return=minimal"}
        )
        return resp

async def supabase_get(table: str, query: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}?{query}",
            headers=supabase_headers()
        )
        return resp.json() if resp.status_code == 200 else []

async def supabase_update(table: str, query: str, data: dict):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/{table}?{query}",
            json=data,
            headers={**supabase_headers(), "Prefer": "return=minimal"}
        )
        return resp

# ================= M-PESA AUTH =================
async def get_mpesa_token():
    try:
        url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        auth = base64.b64encode(f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("access_token")
        print(f"M-Pesa auth failed: {resp.status_code}")
        return None
    except Exception as e:
        print(f"M-Pesa auth error: {e}")
        return None

# ================= CACHE FUNCTIONS =================
def load_cached_symbols():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                if time.time() - data.get('timestamp', 0) < CACHE_TTL:
                    return data.get('symbols', {})
    except: pass
    return {}

def save_cached_symbols(symbols_dict):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({'timestamp': time.time(), 'symbols': symbols_dict}, f)
    except: pass

# ================= LOAD EXCHANGE - NO TIMEOUT =================
async def load_exchange(exchange_id, cached_data):
    global exchanges_loaded

    exchange = None
    try:
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"}
        }

        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY', '')
        if exchange_id in ['mexc', 'bitget']:
            api_secret = os.getenv(f'{exchange_id.upper()}_SECRET_KEY', '')
        else:
            api_secret = os.getenv(f'{exchange_id.upper()}_API_SECRET', '')

        if exchange_id == 'kucoin':
            passphrase = os.getenv('KUCOIN_API_PASSPHRASE', '')
            if passphrase: config['password'] = passphrase
        elif exchange_id == 'bitget':
            passphrase = os.getenv('BITGET_PASSPHRASE', '')
            if passphrase: config['password'] = passphrase

        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret

        exchange = exchange_class(config)
        print(f"⚡ Loading {exchange_id}...")

        if exchange_id in cached_data:
            symbols = [s for s in cached_data[exchange_id] if s.endswith('/USDT')]
            print(f"✓ {exchange_id}: {len(symbols)} pairs (cached)")
        else:
            print(f"📡 {exchange_id}: Fetching markets (no timeout)...")
            await exchange.load_markets()
            usdt_symbols = [s for s in exchange.symbols if s.endswith('/USDT')]
            symbols = usdt_symbols[:MAX_COINS]
            print(f"✓ {exchange_id}: {len(symbols)} pairs loaded")

        exchanges[exchange_id] = exchange
        exchanges_loaded += 1
        print(f"📊 {exchange_id} ready | Total: {exchanges_loaded}/{len(EXCHANGE_IDS)}")
        return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

    except Exception as e:
        print(f"❌ {exchange_id}: {type(e).__name__}: {str(e)[:150]}")
        if exchange:
            try: await exchange.close()
            except: pass
        return None

async def safe_load(exchange_id, cached_data):
    try: return await load_exchange(exchange_id, cached_data)
    except Exception as e:
        print(f"❌ {exchange_id} FATAL: {e}")
        return None

# ================= INITIALIZE =================
async def initialize_exchanges():
    global all_symbols, exchanges_loaded, scanning_active, initialized

    if initialized: return

    print("\n" + "=" * 50)
    print("⚡ ARBIHUNT SCANNER - NO TIMEOUT")
    print("=" * 50 + "\n")

    cached_data = load_cached_symbols()
    exchanges_loaded = 0
    all_symbols = []

    tasks = [safe_load(eid, cached_data) for eid in EXCHANGE_IDS]
    results = await asyncio.gather(*tasks)

    symbol_counter = Counter()
    new_cache = {}
    for result in results:
        if result and result.get("symbols"):
            new_cache[result["id"]] = result["symbols"]
            for s in result["symbols"]:
                symbol_counter[s] += 1

    if new_cache: save_cached_symbols(new_cache)
    all_symbols = [s for s, count in symbol_counter.items() if count >= 2]

    print(f"\n✅ {exchanges_loaded}/{len(EXCHANGE_IDS)} exchanges")
    print(f"📊 {len(all_symbols)} pairs on 2+ exchanges\n")

    if exchanges_loaded >= 2 and len(all_symbols) > 0:
        scanning_active = True
        initialized = True
        asyncio.create_task(continuous_scanner())

# ================= SCAN SYMBOL =================
async def scan_symbol(symbol):
    try:
        tasks = [asyncio.wait_for(ex.fetch_order_book(symbol, limit=1), timeout=30.0) for name, ex in exchanges.items()]
        ex_names = list(exchanges.keys())
        if len(tasks) < 2: return None
        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {}
        for name, result in zip(ex_names, results):
            if isinstance(result, Exception): continue
            if result and result.get('asks') and result.get('bids'):
                if len(result['asks']) > 0 and len(result['bids']) > 0:
                    ask = result['asks'][0][0]
                    bid = result['bids'][0][0]
                    ask_vol = (result['asks'][0][1] or 0) * ask
                    bid_vol = (result['bids'][0][1] or 0) * bid
                    data[name] = {'ask': ask, 'bid': bid, 'ask_vol': ask_vol, 'bid_vol': bid_vol}

        if len(data) < 2: return None

        best = None
        best_profit = 0
        for buy_ex, b in data.items():
            for sell_ex, s in data.items():
                if buy_ex == sell_ex: continue
                buy_cost = b['ask'] * (1 + TRADING_FEES.get(buy_ex, 0.1)/100)
                sell_rev = s['bid'] * (1 - TRADING_FEES.get(sell_ex, 0.1)/100)
                profit = (sell_rev - buy_cost) / buy_cost * 100
                liq = min(b['ask_vol'], s['bid_vol'])

                if profit > best_profit and liq >= MIN_LIQUIDITY_USD:
                    best_profit = profit
                    best = {
                        'symbol': symbol.replace('/USDT', ''),
                        'buy_exchange': buy_ex.upper(),
                        'sell_exchange': sell_ex.upper(),
                        'buy_price': round(b['ask'], 8),
                        'sell_price': round(s['bid'], 8),
                        'spread': round(profit, 2),
                        'net_profit': round(profit - 0.2, 4),
                        'liquidity': round(liq, 0),
                        'buy_liquidity': round(b['ask_vol'], 0),
                        'sell_liquidity': round(s['bid_vol'], 0),
                        'timestamp': time.time()
                    }
        return best if best and best_profit >= MIN_PROFIT_PERCENT else None
    except: return None

# ================= CONTINUOUS SCANNER =================
async def continuous_scanner():
    global latest_opportunities, scan_round
    print(f"⚡ SCANNER ACTIVE - {len(all_symbols)} pairs\n")
    scan_index = 0
    while True:
        try:
            if scan_index >= len(all_symbols) or len(all_symbols) == 0:
                scan_round += 1
                scan_index = 0
                if len(all_symbols) == 0:
                    await asyncio.sleep(10)
                    continue

            batch = all_symbols[scan_index:scan_index + BATCH_SIZE]
            scan_index += BATCH_SIZE
            tasks = [scan_symbol(s) for s in batch]
            results = await asyncio.gather(*tasks)

            for r in results:
                if r:
                    latest_opportunities = [o for o in latest_opportunities if o['symbol'] != r['symbol']]
                    latest_opportunities.append(r)
                    latest_opportunities.sort(key=lambda x: x['spread'], reverse=True)
                    latest_opportunities = latest_opportunities[:50]
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"❌ Scan error: {e}")
            await asyncio.sleep(5)

# ================= STARTUP =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(initialize_exchanges())

# ================= AUTH ENDPOINTS =================
@app.post("/api/auth/signup")
async def signup(request: Request):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"success": False, "error": "Database not configured"}

    data = await request.json()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()

    if not email or not password:
        return {"success": False, "error": "Email and password required"}

    # Step 1: Create auth user via Supabase REST API
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            auth_resp = await client.post(
                f"{SUPABASE_URL}/auth/v1/signup",
                json={"email": email, "password": password},
                headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
            )

        if auth_resp.status_code != 200:
            error_data = auth_resp.json() if auth_resp.text else {}
            error_msg = error_data.get("msg", error_data.get("message", "Unknown error"))
            return {"success": False, "error": f"Auth failed: {error_msg}"}

        auth_data = auth_resp.json()
        user_id = auth_data.get("id") or auth_data.get("user", {}).get("id")

        if not user_id:
            return {"success": False, "error": "No user ID returned"}

        print(f"✅ User created: {user_id}")

    except Exception as e:
        return {"success": False, "error": f"Auth error: {str(e)[:200]}"}

    # Step 2: Insert profile
    try:
        profile_data = {
            "id": user_id,
            "email": email,
            "name": name,
            "phone": phone,
            "is_pro": False,
            "created_at": datetime.now().isoformat()
        }
        resp = await supabase_post("profiles", profile_data)
        if resp.status_code not in [200, 201, 204]:
            print(f"⚠️ Profile insert: {resp.status_code}")
    except Exception as e:
        print(f"⚠️ Profile error: {e}")

    return {
        "success": True,
        "user_id": user_id,
        "user": {"id": user_id, "email": email, "name": name, "phone": phone}
    }

@app.post("/api/auth/login")
async def login(request: Request):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"success": False, "error": "Database not configured"}

    data = await request.json()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            auth_resp = await client.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                json={"email": email, "password": password},
                headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
            )

        if auth_resp.status_code != 200:
            return {"success": False, "error": "Invalid email or password"}

        auth_data = auth_resp.json()
        user_id = auth_data.get("user", {}).get("id")
        access_token = auth_data.get("access_token")

        # Get profile
        profiles = await supabase_get("profiles", f"id=eq.{user_id}&select=*")
        profile = profiles[0] if profiles else {"id": user_id, "email": email, "name": "", "phone": "", "is_pro": False}

        return {
            "success": True,
            "access_token": access_token,
            "user": profile
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

# ================= M-PESA STK PUSH =================
@app.post("/api/mpesa/stkpush")
async def mpesa_stk_push(request: Request):
    data = await request.json()
    phone = data.get("phone", "")
    plan = data.get("plan", "weekly")
    user_id = data.get("user_id", "")

    plan_config = SUB_PRICES.get(plan, SUB_PRICES["weekly"])
    amount = plan_config["amount"]

    token = await get_mpesa_token()
    if not token:
        return {"success": False, "error": "M-Pesa authentication failed. Check Consumer Key & Secret."}

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{BUSINESS_SHORTCODE}{MPESA_PASSKEY}{timestamp}".encode()).decode()

    payload = {
        "BusinessShortCode": BUSINESS_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": BUSINESS_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": f"ArbiHunt-{plan}",
        "TransactionDesc": f"ArbiHunt {plan_config['label']}"
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
                json=payload, headers=headers
            )

        if resp.status_code != 200:
            return {"success": False, "error": f"M-Pesa error: {resp.text[:200]}"}

        result = resp.json()
        checkout_id = result.get("CheckoutRequestID", "")

        # Save transaction
        if checkout_id:
            await supabase_post("transactions", {
                "user_id": user_id,
                "checkout_request_id": checkout_id,
                "phone": phone,
                "amount": amount,
                "plan": plan,
                "status": "pending",
                "created_at": datetime.now().isoformat()
            })

        return {"success": True, "message": "STK Push sent. Check your phone.", "checkout_request_id": checkout_id}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

# ================= M-PESA CALLBACK =================
@app.post("/api/mpesa/callback")
async def mpesa_callback(request: Request):
    try:
        data = await request.json()
        body = data.get("Body", {})
        stk_callback = body.get("stkCallback", {})
        checkout_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")

        if result_code == 0:
            callback_data = stk_callback.get("CallbackMetadata", {}).get("Item", [])
            amount = next((item["Value"] for item in callback_data if item["Name"] == "Amount"), 0)
            mpesa_code = next((item["Value"] for item in callback_data if item["Name"] == "MpesaReceiptNumber"), "")

            # Update transaction
            await supabase_update("transactions", f"checkout_request_id=eq.{checkout_id}", {
                "status": "completed",
                "mpesa_code": mpesa_code,
                "completed_at": datetime.now().isoformat()
            })

            # Get transaction to find user
            txn_list = await supabase_get("transactions", f"checkout_request_id=eq.{checkout_id}&select=*")
            if txn_list:
                txn = txn_list[0]
                plan = txn.get("plan", "weekly")
                user_id = txn.get("user_id")
                plan_config = SUB_PRICES.get(plan, SUB_PRICES["weekly"])
                pro_expiry = datetime.now() + timedelta(days=plan_config["days"])

                # Upgrade user to PRO
                await supabase_update("profiles", f"id=eq.{user_id}", {
                    "is_pro": True,
                    "pro_expiry": pro_expiry.isoformat(),
                    "plan": plan
                })

        return {"success": True}
    except Exception as e:
        print(f"Callback error: {e}")
        return {"success": False}

# ================= USER STATUS =================
@app.get("/api/user/status")
async def user_status(user_id: str):
    try:
        profiles = await supabase_get("profiles", f"id=eq.{user_id}&select=*")
        if profiles:
            profile = profiles[0]
            is_pro = profile.get("is_pro", False)
            pro_expiry = profile.get("pro_expiry")

            if is_pro and pro_expiry:
                if datetime.now() > datetime.fromisoformat(pro_expiry):
                    await supabase_update("profiles", f"id=eq.{user_id}", {"is_pro": False})
                    is_pro = False

            return {
                "is_pro": is_pro,
                "pro_expiry": pro_expiry,
                "name": profile.get("name", ""),
                "email": profile.get("email", "")
            }
        return {"is_pro": False}
    except:
        return {"is_pro": False}

# ================= CHAT =================
@app.post("/api/chat/send")
async def send_message(request: Request):
    data = await request.json()
    try:
        await supabase_post("messages", {
            "user_id": data.get("user_id", "anon"),
            "user_name": data.get("user_name", "Anonymous"),
            "message": data.get("message", ""),
            "created_at": datetime.now().isoformat()
        })
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/chat/messages")
async def get_messages(limit: int = 50):
    try:
        msgs = await supabase_get("messages", f"order=created_at.desc&limit={limit}")
        return {"messages": list(reversed(msgs))}
    except:
        return {"messages": []}

# ================= HEALTH & API =================
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "exchanges": exchanges_loaded,
        "opps": len(latest_opportunities),
        "round": scan_round,
        "symbols": len(all_symbols)
    }

@app.get("/api/opportunities")
async def get_opportunities():
    return latest_opportunities

# ================= WEB UI =================
@app.get("/")
async def home():
    return HTMLResponse(get_main_html())

@app.get("/chat")
async def chat_page():
    return HTMLResponse(get_chat_html())

@app.get("/profile")
async def profile_page():
    return HTMLResponse(get_profile_html())

def get_main_html():
    return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no"><title>ArbiHunt</title><style>:root{--bg:#0D0D0D;--card:#141414;--border:#1F1F1F;--text:#E0E0E0;--text-secondary:#888;--green:#00C853;--red:#FF5252;--orange:#FF9800;--gold:#FFD700}*{margin:0;padding:0;box-sizing:border-box}body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}.header{position:sticky;top:0;z-index:100;background:var(--bg);padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}.logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#FFD700,#FFA000);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.btn{background:linear-gradient(135deg,#FFD700,#FF8F00);color:#000;font-weight:700;font-size:12px;padding:8px 16px;border-radius:20px;border:none;cursor:pointer;text-decoration:none}.btn-outline{background:transparent;color:#fff;border:1px solid var(--border)}.pro-banner{background:linear-gradient(135deg,rgba(255,152,0,0.15),rgba(255,152,0,0.05));border:1px solid rgba(255,152,0,0.3);border-radius:12px;margin:12px 16px;padding:14px 16px;display:flex;align-items:center;gap:12px}.pro-banner .lock-text{flex:1;font-size:13px;color:var(--orange);font-weight:600}.stats{display:flex;gap:8px;padding:8px 16px;overflow-x:auto}.stats::-webkit-scrollbar{display:none}.stat{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:8px 14px;font-size:11px;white-space:nowrap;color:var(--text-secondary);display:flex;align-items:center;gap:6px}.live-dot{width:7px;height:7px;background:#00E676;border-radius:50%;animation:pulse 1.5s infinite}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}.stat .count{color:#fff;font-weight:700}.opp-list{padding:8px 16px 100px}.opp-card{background:var(--card);border:1px solid var(--border);border-radius:14px;margin-bottom:10px;overflow:hidden;cursor:pointer;transition:all .2s}.opp-card:active{transform:scale(.98)}.card-header{padding:14px 16px;display:flex;align-items:center;justify-content:space-between}.card-left{flex:1;min-width:0}.route{display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap}.tag-buy{background:rgba(0,200,83,.15);color:#00C853;padding:3px 8px;border-radius:6px;font-size:10px;font-weight:700}.tag-sell{background:rgba(255,82,82,.15);color:#FF5252;padding:3px 8px;border-radius:6px;font-size:10px;font-weight:700}.symbol{font-size:18px;font-weight:700;color:#fff}.meta{display:flex;gap:12px;margin-top:4px;font-size:11px;color:var(--text-secondary)}.card-right{text-align:right;flex-shrink:0}.spread{font-size:24px;font-weight:800;color:#00C853}.detail{display:none;border-top:1px solid var(--border);background:#0F0F0F;padding:16px}.detail.show{display:block}.detail-section{margin-bottom:16px}.detail-title{font-size:14px;font-weight:700;color:#fff;margin-bottom:10px}.detail-row{display:flex;justify-content:space-between;padding:7px 0;font-size:13px;border-bottom:1px solid #1A1A1A}.detail-label{color:#777}.detail-val{color:#fff;font-weight:500}.warning{background:rgba(255,152,0,.1);border-left:3px solid var(--orange);padding:10px 12px;border-radius:6px;font-size:11px;color:var(--orange);margin-top:12px}.no-data{text-align:center;padding:60px 20px;color:#444}.no-data .icon{font-size:48px;margin-bottom:12px}.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:10px 0;padding-bottom:max(10px,env(safe-area-inset-bottom));z-index:100}.nav-item{display:flex;flex-direction:column;align-items:center;gap:4px;color:#555;font-size:10px;font-weight:600;text-decoration:none}.nav-item.active{color:#FFD700}.nav-icon{font-size:20px}.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:200;align-items:center;justify-content:center}.modal.show{display:flex}.modal-content{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;width:90%;max-width:400px}.modal-title{font-size:18px;font-weight:700;color:#fff;margin-bottom:16px;text-align:center}.input-group{margin-bottom:12px}.input-group label{display:block;font-size:12px;color:#888;margin-bottom:4px}.input-group input{width:100%;padding:10px 14px;background:#1A1A1A;border:1px solid var(--border);border-radius:10px;color:#fff;font-size:14px}.btn-primary{width:100%;padding:12px;background:linear-gradient(135deg,#FFD700,#FF8F00);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;margin-top:8px}.btn-secondary{width:100%;padding:10px;background:transparent;color:#888;border:1px solid #333;border-radius:10px;font-size:13px;cursor:pointer;margin-top:8px}.plan-card{background:#1A1A1A;border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:8px;cursor:pointer}.plan-card.selected{border-color:#FFD700}.plan-name{font-weight:700;color:#fff}.plan-price{font-size:20px;font-weight:800;color:#FFD700}.plan-desc{font-size:11px;color:#888}</style></head><body><div class="header"><div class="logo">ArbiHunt</div><div style="display:flex;gap:8px"><button class="btn" onclick="showProModal()" id="proBtn" style="display:none">GO PRO</button><button class="btn btn-outline" onclick="showAuthModal()" id="loginBtn">Sign In</button><span id="userName" style="color:#FFD700;font-weight:600;display:none;cursor:pointer" onclick="location.href='/profile'"></span></div></div><div class="pro-banner" id="proBanner"><span style="font-size:24px">🔒</span><span class="lock-text">Upgrade to PRO to Discover Profit Opportunities Above 2%</span><button class="btn" onclick="showProModal()">GO PRO</button></div><div class="stats"><div class="stat"><span class="live-dot"></span>LIVE</div><div class="stat">📊 <span class="count" id="oppCount">0</span> opps</div><div class="stat">🔄 Round <span class="count" id="roundNum">0</span></div></div><div class="opp-list" id="oppList"><div class="no-data"><div class="icon">⚡</div><div style="font-size:14px">Scanning all USDT pairs...</div></div></div><div class="bottom-nav"><a class="nav-item active" href="/"><span class="nav-icon">🏠</span>Home</a><a class="nav-item" href="/chat"><span class="nav-icon">💬</span>Chat</a><a class="nav-item" href="#" onclick="showProModal()"><span class="nav-icon">⭐</span>Go PRO</a><a class="nav-item" href="/profile"><span class="nav-icon">👤</span>Profile</a></div><div class="modal" id="authModal"><div class="modal-content"><div class="modal-title" id="authTitle">Sign In</div><div class="input-group"><label>Email</label><input type="email" id="authEmail"></div><div class="input-group"><label>Password</label><input type="password" id="authPassword"></div><div class="input-group" id="nameGroup" style="display:none"><label>Name</label><input type="text" id="authName"></div><div class="input-group" id="phoneGroup" style="display:none"><label>Phone (2547XXXXXXXX)</label><input type="tel" id="authPhone"></div><button class="btn-primary" onclick="handleAuth()" id="authBtn">Sign In</button><button class="btn-secondary" onclick="toggleAuthMode()"><span id="authToggle">Create Account</span></button><button class="btn-secondary" onclick="closeModal('authModal')">Cancel</button><div id="authError" style="color:#FF5252;font-size:12px;margin-top:8px;text-align:center"></div></div></div><div class="modal" id="proModal"><div class="modal-content"><div class="modal-title">Upgrade to PRO</div><div class="plan-card" onclick="selectPlan('weekly')" id="planWeekly"><div class="plan-name">Weekly PRO</div><div class="plan-price">KES 200</div><div class="plan-desc">7 days access</div></div><div class="plan-card selected" onclick="selectPlan('monthly')" id="planMonthly"><div class="plan-name">Monthly PRO</div><div class="plan-price">KES 700</div><div class="plan-desc">30 days access • Best Value!</div></div><div class="input-group"><label>Safaricom Phone</label><input type="tel" id="mpesaPhone" placeholder="254712345678"></div><button class="btn-primary" onclick="payWithMpesa()">💳 Pay with M-Pesa</button><button class="btn-secondary" onclick="closeModal('proModal')">Cancel</button><div id="payStatus" style="text-align:center;margin-top:8px;font-size:12px"></div></div></div><script>let opps=[],expandedCard=null,roundNum=0,currentUser=null,selectedPlan='monthly';const stored=localStorage.getItem('arbihunt_user');if(stored){currentUser=JSON.parse(stored);updateUI()}function ago(ts){const s=Math.floor(Date.now()/1000-ts);if(s<5)return'Just now';if(s<60)return`${s}s ago`;return`${Math.floor(s/60)}m ago`}function fmtEx(n){const m={'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin','COINEX':'CoinEx','BITGET':'Bitget'};return m[n]||n}function toggleCard(idx,e){e.stopPropagation();const d=document.getElementById(`detail-${idx}`);if(expandedCard===idx){d.classList.remove('show');expandedCard=null}else{if(expandedCard!==null)document.getElementById(`detail-${expandedCard}`)?.classList.remove('show');d.classList.add('show');expandedCard=idx}}function render(){document.getElementById('oppCount').textContent=opps.length;document.getElementById('roundNum').textContent=roundNum;const c=document.getElementById('oppList');if(!opps.length){c.innerHTML='<div class="no-data"><div class="icon">⚡</div><div style="font-size:14px">Scanning all USDT pairs...</div></div>';return}c.innerHTML=opps.map((o,i)=>`<div class="opp-card" onclick="toggleCard(${i},event)"><div class="card-header"><div class="card-left"><div class="route"><span class="tag-buy">BUY</span>${fmtEx(o.buy_exchange)}→<span class="tag-sell">SELL</span>${fmtEx(o.sell_exchange)}</div><div class="symbol">${o.symbol}/USDT</div><div class="meta"><span>💰$${Number(o.liquidity).toLocaleString()}</span><span>⏱${ago(o.timestamp)}</span></div></div><div class="card-right"><div class="spread">${o.spread}%</div></div></div><div class="detail" id="detail-${i}"><div class="detail-section"><div class="detail-title">1️⃣ Buy at ${fmtEx(o.buy_exchange)}</div><div class="detail-row"><span class="detail-label">Price:</span><span class="detail-val">$${o.buy_price}</span></div><div class="detail-row"><span class="detail-label">Liquidity:</span><span class="detail-val">$${Number(o.buy_liquidity).toLocaleString()}</span></div></div><div class="detail-section"><div class="detail-title">2️⃣ Sell at ${fmtEx(o.sell_exchange)}</div><div class="detail-row"><span class="detail-label">Price:</span><span class="detail-val">$${o.sell_price}</span></div><div class="detail-row"><span class="detail-label">Liquidity:</span><span class="detail-val">$${Number(o.sell_liquidity).toLocaleString()}</span></div></div><div class="detail-row"><span class="detail-label">Spread:</span><span class="detail-val" style="color:#00C853">${o.spread}%</span></div><div class="detail-row"><span class="detail-label">Net Profit:</span><span class="detail-val">${o.net_profit}%</span></div><div class="warning">⚠️ Double check coin contract before trading.</div></div></div>`).join('');expandedCard=null}function updateUI(){document.getElementById('loginBtn').style.display='none';document.getElementById('userName').style.display='inline';document.getElementById('userName').textContent=currentUser?.name||currentUser?.email||'User';checkPro()}async function checkPro(){if(!currentUser?.id)return;try{const r=await fetch(`/api/user/status?user_id=${currentUser.id}`);const d=await r.json();if(d.is_pro){document.getElementById('proBanner').style.display='none';document.getElementById('proBtn').style.display='none'}else{document.getElementById('proBtn').style.display='inline-block'}}catch(e){}}function showAuthModal(){document.getElementById('authModal').classList.add('show');document.getElementById('authError').textContent=''}function closeModal(id){document.getElementById(id).classList.remove('show')}let isSignUp=false;function toggleAuthMode(){isSignUp=!isSignUp;document.getElementById('authTitle').textContent=isSignUp?'Create Account':'Sign In';document.getElementById('authBtn').textContent=isSignUp?'Create Account':'Sign In';document.getElementById('authToggle').textContent=isSignUp?'Have account? Sign In':'Create Account';document.getElementById('nameGroup').style.display=isSignUp?'block':'none';document.getElementById('phoneGroup').style.display=isSignUp?'block':'none'}async function handleAuth(){const email=document.getElementById('authEmail').value.trim();const password=document.getElementById('authPassword').value.trim();if(!email||!password){document.getElementById('authError').textContent='Email and password required';return}const url=isSignUp?'/api/auth/signup':'/api/auth/login';const body=isSignUp?{email,password,name:document.getElementById('authName').value.trim(),phone:document.getElementById('authPhone').value.trim()}:{email,password};try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(d.success){currentUser=d.user||{email,name:body.name,phone:body.phone,id:d.user_id};localStorage.setItem('arbihunt_user',JSON.stringify(currentUser));updateUI();closeModal('authModal')}else{document.getElementById('authError').textContent=d.error||'Error'}}catch(e){document.getElementById('authError').textContent='Network error'}}function showProModal(){document.getElementById('proModal').classList.add('show');if(currentUser?.phone)document.getElementById('mpesaPhone').value=currentUser.phone}function selectPlan(p){selectedPlan=p;document.getElementById('planWeekly').classList.remove('selected');document.getElementById('planMonthly').classList.remove('selected');document.getElementById('plan'+p.charAt(0).toUpperCase()+p.slice(1)).classList.add('selected')}async function payWithMpesa(){const phone=document.getElementById('mpesaPhone').value.trim();if(!phone){document.getElementById('payStatus').innerHTML='<span style="color:#FF5252">Enter phone number</span>';return}document.getElementById('payStatus').innerHTML='<span style="color:#FFD700">Sending payment prompt...</span>';try{const r=await fetch('/api/mpesa/stkpush',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,plan:selectedPlan,user_id:currentUser?.id||''})});const d=await r.json();if(d.success){document.getElementById('payStatus').innerHTML='<span style="color:#00C853">✅ Prompt sent! Check your phone and enter PIN.</span>'}else{document.getElementById('payStatus').innerHTML=`<span style="color:#FF5252">${d.error}</span>`}}catch(e){document.getElementById('payStatus').innerHTML='<span style="color:#FF5252">Network error</span>'}}async function pollOpps(){try{const r=await fetch('/api/opportunities');opps=await r.json();render()}catch(e){}}setInterval(pollOpps,3000);pollOpps()</script><script>window.si = window.si || function () { (window.siq = window.siq || []).push(arguments); };</script><script defer src="/_vercel/speed-insights/script.js"></script></body></html>"""

def get_chat_html():
    return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no"><title>ArbiHunt Chat</title><style>:root{--bg:#0D0D0D;--card:#141414;--border:#1F1F1F;--text:#E0E0E0;--gold:#FFD700}*{margin:0;padding:0;box-sizing:border-box}body{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif;min-height:100vh}.header{position:sticky;top:0;z-index:100;background:var(--bg);padding:12px 16px;border-bottom:1px solid var(--border)}.logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#FFD700,#FFA000);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.chat-container{padding:12px 16px 160px}.msg{margin-bottom:10px;animation:fadeIn .3s}@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}.msg-name{font-size:11px;font-weight:700;color:#FFD700;margin-bottom:2px}.msg-text{background:var(--card);padding:10px 14px;border-radius:12px;font-size:14px;display:inline-block;max-width:80%}.msg-time{font-size:9px;color:#555;margin-top:2px}.input-bar{position:fixed;bottom:70px;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);padding:10px 16px;display:flex;gap:8px;z-index:100}.input-bar input{flex:1;padding:10px 14px;background:#1A1A1A;border:1px solid var(--border);border-radius:20px;color:#fff;font-size:14px}.input-bar button{background:linear-gradient(135deg,#FFD700,#FF8F00);color:#000;border:none;border-radius:20px;padding:10px 18px;font-weight:700;cursor:pointer}.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:10px 0;padding-bottom:max(10px,env(safe-area-inset-bottom));z-index:100}.nav-item{display:flex;flex-direction:column;align-items:center;gap:4px;color:#555;font-size:10px;font-weight:600;text-decoration:none}.nav-item.active{color:#FFD700}.nav-icon{font-size:20px}</style></head><body><div class="header"><div class="logo">Chat Room</div></div><div class="chat-container" id="chatContainer"><div style="text-align:center;color:#444;padding:40px">Loading...</div></div><div class="input-bar"><input id="chatInput" placeholder="Share your story..." onkeypress="if(event.key==='Enter')sendMsg()"><button onclick="sendMsg()">Send</button></div><div class="bottom-nav"><a class="nav-item" href="/"><span class="nav-icon">🏠</span>Home</a><a class="nav-item active" href="/chat"><span class="nav-icon">💬</span>Chat</a><a class="nav-item" href="/profile"><span class="nav-icon">👤</span>Profile</a></div><script>const user=JSON.parse(localStorage.getItem('arbihunt_user')||'{}');const userName=user.name||user.email||'Anonymous';const userId=user.id||'anon';function addMsg(name,text,time){const c=document.getElementById('chatContainer');if(c.querySelector('div[style]'))c.innerHTML='';const t=new Date(time||Date.now()).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});const d=document.createElement('div');d.className='msg';d.innerHTML=`<div class="msg-name">${name}</div><div class="msg-text">${text}</div><div class="msg-time">${t}</div>`;c.appendChild(d);c.scrollTop=c.scrollHeight}async function sendMsg(){const input=document.getElementById('chatInput');const msg=input.value.trim();if(!msg)return;input.value='';addMsg(userName,msg);await fetch('/api/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,user_name:userName,message:msg})})}async function loadMsgs(){try{const r=await fetch('/api/chat/messages?limit=50');const d=await r.json();const c=document.getElementById('chatContainer');if(d.messages&&d.messages.length){c.innerHTML='';d.messages.forEach(m=>addMsg(m.user_name,m.message,m.created_at))}}catch(e){}}loadMsgs();setInterval(loadMsgs,5000)</script><script>window.si = window.si || function () { (window.siq = window.siq || []).push(arguments); };</script><script defer src="/_vercel/speed-insights/script.js"></script></body></html>"""

def get_profile_html():
    return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>ArbiHunt Profile</title><style>:root{--bg:#0D0D0D;--card:#141414;--border:#1F1F1F;--text:#E0E0E0;--gold:#FFD700;--red:#FF5252}*{margin:0;padding:0;box-sizing:border-box}body{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif;min-height:100vh}.header{padding:12px 16px;border-bottom:1px solid var(--border)}.logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#FFD700,#FFA000);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.profile-card{background:var(--card);border:1px solid var(--border);border-radius:16px;margin:16px;padding:24px;text-align:center}.avatar{width:70px;height:70px;background:linear-gradient(135deg,#FFD700,#FF8F00);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 12px;color:#000}.name{font-size:20px;font-weight:700;color:#fff}.email{font-size:13px;color:#888;margin-top:4px}.pro-badge{display:inline-block;background:linear-gradient(135deg,#FFD700,#FF8F00);color:#000;padding:6px 16px;border-radius:20px;font-weight:700;font-size:12px;margin-top:12px}.info-row{display:flex;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border);font-size:14px}.info-label{color:#888}.logout-btn{width:calc(100% - 32px);margin:16px;padding:14px;background:transparent;border:1px solid var(--red);color:var(--red);border-radius:12px;font-size:14px;font-weight:600;cursor:pointer}.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:10px 0;padding-bottom:max(10px,env(safe-area-inset-bottom));z-index:100}.nav-item{display:flex;flex-direction:column;align-items:center;gap:4px;color:#555;font-size:10px;font-weight:600;text-decoration:none}.nav-item.active{color:#FFD700}.nav-icon{font-size:20px}</style></head><body><div class="header"><div class="logo">ArbiHunt</div></div><div class="profile-card"><div class="avatar" id="av">?</div><div class="name" id="pname">Loading...</div><div class="email" id="pemail"></div><div id="pstatus"></div></div><div class="info-row"><span class="info-label">Account ID</span><span id="pid">-</span></div><button class="logout-btn" onclick="logout()">Logout</button><div class="bottom-nav"><a class="nav-item" href="/"><span class="nav-icon">🏠</span>Home</a><a class="nav-item" href="/chat"><span class="nav-icon">💬</span>Chat</a><a class="nav-item active" href="/profile"><span class="nav-icon">👤</span>Profile</a></div><script>const user=JSON.parse(localStorage.getItem('arbihunt_user')||'{}');if(!user.email)location.href='/';document.getElementById('pname').textContent=user.name||'User';document.getElementById('pemail').textContent=user.email||'';document.getElementById('av').textContent=(user.name||'U')[0].toUpperCase();document.getElementById('pid').textContent=user.id||'N/A';async function checkPro(){if(!user.id)return;try{const r=await fetch('/api/user/status?user_id='+user.id);const d=await r.json();if(d.is_pro)document.getElementById('pstatus').innerHTML='<span class="pro-badge">⭐ PRO Member</span>'}catch(e){}}checkPro();function logout(){localStorage.removeItem('arbihunt_user');location.href='/'}</script><script>window.si = window.si || function () { (window.siq = window.siq || []).push(arguments); };</script><script defer src="/_vercel/speed-insights/script.js"></script></body></html>"""
