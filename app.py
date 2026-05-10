import ccxt.async_support as ccxt
import asyncio
from fastapi import FastAPI, WebSocket, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import time
import os
import json
import httpx
import base64
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional
import hashlib

# ================= SUPABASE SETUP =================
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= M-PESA SETUP =================
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY", "")
MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "174379")
MPESA_CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "")
BUSINESS_SHORTCODE = os.getenv("MPESA_BUSINESS_SHORTCODE", "174379")

# ================= ARBITRAGE CONFIG =================
EXCHANGE_IDS = ["gateio", "kucoin", "mexc", "bitget", "coinex", "bingx"]
MAX_COINS = 500
CACHE_FILE = "symbols_cache.json"
CACHE_TTL = 3600

TRADING_FEES = {
    'gateio': 0.1, 'kucoin': 0.1, 'mexc': 0.1,
    'bitget': 0.1, 'coinex': 0.2, 'bingx': 0.1,
}

MIN_PROFIT_PERCENT = 0.5
MIN_LIQUIDITY_USD = 50
BATCH_SIZE = 50
MARKET_LOAD_TIMEOUT = 25

app = FastAPI()
latest_opportunities = []
all_symbols = []
exchanges = {}
exchanges_loaded = 0
scanning_active = False
scan_round = 0

# ================= SUBSCRIPTION PRICES =================
SUB_PRICES = {
    "weekly": {"amount": 200, "days": 7, "label": "Weekly PRO"},
    "monthly": {"amount": 700, "days": 30, "label": "Monthly PRO"},
}

# ================= M-PESA AUTH =================
async def get_mpesa_token():
    """Get M-Pesa access token"""
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    auth = base64.b64encode(f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.json().get("access_token")
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

# ================= LOAD EXCHANGE =================
async def load_exchange(exchange_id, cached_data):
    global exchanges_loaded
    
    exchange = None
    try:
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            "enableRateLimit": True,
            "timeout": 3000,
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

        if exchange_id in cached_data:
            symbols = [s for s in cached_data[exchange_id] if s.endswith('/USDT')]
        else:
            try:
                await asyncio.wait_for(exchange.load_markets(), timeout=MARKET_LOAD_TIMEOUT)
            except asyncio.TimeoutError:
                if exchange: await exchange.close()
                return None
            usdt_symbols = [s for s in exchange.symbols if s.endswith('/USDT')]
            symbols = usdt_symbols[:MAX_COINS]

        exchanges[exchange_id] = exchange
        exchanges_loaded += 1
        return {"id": exchange_id, "exchange": exchange, "symbols": symbols}

    except Exception as e:
        print(f"❌ {exchange_id}: {type(e).__name__}")
        if exchange:
            try: await exchange.close()
            except: pass
        return None

async def safe_load(exchange_id, cached_data):
    try: return await load_exchange(exchange_id, cached_data)
    except: return None

# ================= INITIALIZE =================
async def initialize_exchanges():
    global all_symbols, exchanges_loaded, scanning_active
    
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
    
    print(f"✅ {exchanges_loaded} exchanges | {len(all_symbols)} pairs on 2+ exchanges")
    
    if exchanges_loaded >= 2 and len(all_symbols) > 0:
        scanning_active = True
        asyncio.create_task(continuous_scanner())

# ================= SCAN SYMBOL =================
async def scan_symbol(symbol):
    try:
        tasks = []
        ex_names = []
        for name, ex in exchanges.items():
            tasks.append(asyncio.wait_for(ex.fetch_order_book(symbol, limit=1), timeout=2.0))
            ex_names.append(name)
        
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
        
        if best and best_profit >= MIN_PROFIT_PERCENT:
            return best
        return None
    except: return None

# ================= CONTINUOUS SCANNER =================
async def continuous_scanner():
    global latest_opportunities, scan_round
    
    scan_index = 0
    while True:
        try:
            if scan_index >= len(all_symbols) or len(all_symbols) == 0:
                scan_round += 1
                scan_index = 0
                if len(all_symbols) == 0:
                    await asyncio.sleep(5)
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
            
            await asyncio.sleep(0.01)
        except Exception as e:
            await asyncio.sleep(1)

# ================= STARTUP =================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(initialize_exchanges())

# ================= AUTH ENDPOINTS =================
@app.post("/api/auth/signup")
async def signup(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    name = data.get("name", "")
    phone = data.get("phone", "")
    
    try:
        # Create auth user
        result = supabase.auth.sign_up({
            "email": email,
            "password": password
        })
        
        user_id = result.user.id
        
        # Create profile
        supabase.table("profiles").insert({
            "id": user_id,
            "email": email,
            "name": name,
            "phone": phone,
            "is_pro": False,
            "pro_expiry": None,
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return {"success": True, "user_id": user_id}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/auth/login")
async def login(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    
    try:
        result = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        # Get profile
        profile = supabase.table("profiles").select("*").eq("id", result.user.id).single().execute()
        
        return {
            "success": True,
            "access_token": result.session.access_token,
            "user": profile.data
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ================= M-PESA STK PUSH =================
@app.post("/api/mpesa/stkpush")
async def mpesa_stk_push(request: Request):
    data = await request.json()
    phone_number = data.get("phone")  # e.g., 254712345678
    plan = data.get("plan", "weekly")  # weekly or monthly
    user_id = data.get("user_id", "")
    
    plan_config = SUB_PRICES.get(plan, SUB_PRICES["weekly"])
    amount = plan_config["amount"]
    
    token = await get_mpesa_token()
    if not token:
        return {"success": False, "error": "Failed to get M-Pesa token"}
    
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{BUSINESS_SHORTCODE}{MPESA_PASSKEY}{timestamp}".encode()).decode()
    
    stk_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    
    payload = {
        "BusinessShortCode": BUSINESS_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone_number,
        "PartyB": BUSINESS_SHORTCODE,
        "PhoneNumber": phone_number,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": f"ArbiHunt-{plan}",
        "TransactionDesc": f"ArbiHunt {plan_config['label']} Subscription"
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(stk_url, json=payload, headers=headers)
        result = resp.json()
    
    # Save transaction to Supabase
    checkout_id = result.get("CheckoutRequestID", "")
    supabase.table("transactions").insert({
        "user_id": user_id,
        "checkout_request_id": checkout_id,
        "phone": phone_number,
        "amount": amount,
        "plan": plan,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }).execute()
    
    return {
        "success": True,
        "message": "STK Push sent. Check your phone and enter PIN.",
        "checkout_request_id": checkout_id
    }

# ================= M-PESA CALLBACK =================
@app.post("/api/mpesa/callback")
async def mpesa_callback(request: Request):
    data = await request.json()
    
    try:
        body = data.get("Body", {})
        stk_callback = body.get("stkCallback", {})
        checkout_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc", "")
        
        if result_code == 0:
            # Payment successful
            callback_data = stk_callback.get("CallbackMetadata", {}).get("Item", [])
            amount = next((item["Value"] for item in callback_data if item["Name"] == "Amount"), 0)
            mpesa_code = next((item["Value"] for item in callback_data if item["Name"] == "MpesaReceiptNumber"), "")
            phone = next((item["Value"] for item in callback_data if item["Name"] == "PhoneNumber"), "")
            
            # Update transaction
            supabase.table("transactions").update({
                "status": "completed",
                "mpesa_code": mpesa_code,
                "completed_at": datetime.now().isoformat()
            }).eq("checkout_request_id", checkout_id).execute()
            
            # Get transaction details
            txn = supabase.table("transactions").select("*").eq("checkout_request_id", checkout_id).single().execute()
            if txn.data:
                plan = txn.data.get("plan", "weekly")
                plan_config = SUB_PRICES.get(plan, SUB_PRICES["weekly"])
                pro_expiry = datetime.now() + timedelta(days=plan_config["days"])
                
                # Update user to PRO
                supabase.table("profiles").update({
                    "is_pro": True,
                    "pro_expiry": pro_expiry.isoformat(),
                    "plan": plan
                }).eq("id", txn.data.get("user_id")).execute()
        
        return {"success": True}
    except Exception as e:
        print(f"Callback error: {e}")
        return {"success": False}

# ================= SUBSCRIPTION CHECK =================
@app.get("/api/user/status")
async def user_status(user_id: str):
    try:
        profile = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
        if profile.data:
            is_pro = profile.data.get("is_pro", False)
            pro_expiry = profile.data.get("pro_expiry")
            
            if is_pro and pro_expiry:
                expiry_date = datetime.fromisoformat(pro_expiry)
                if datetime.now() > expiry_date:
                    # Expired - downgrade
                    supabase.table("profiles").update({
                        "is_pro": False
                    }).eq("id", user_id).execute()
                    is_pro = False
            
            return {
                "is_pro": is_pro,
                "pro_expiry": pro_expiry,
                "name": profile.data.get("name", ""),
                "email": profile.data.get("email", "")
            }
        return {"is_pro": False}
    except Exception as e:
        return {"is_pro": False, "error": str(e)}

# ================= CHAT ENDPOINTS =================
@app.post("/api/chat/send")
async def send_message(request: Request):
    data = await request.json()
    
    try:
        supabase.table("messages").insert({
            "user_id": data.get("user_id"),
            "user_name": data.get("user_name", "Anonymous"),
            "message": data.get("message"),
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/chat/messages")
async def get_messages(limit: int = 50):
    try:
        result = supabase.table("messages").select("*").order("created_at", desc=True).limit(limit).execute()
        messages = list(reversed(result.data)) if result.data else []
        return {"messages": messages}
    except:
        return {"messages": []}

# ================= CHAT WEBSOCKET =================
chat_connections = []

@app.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    chat_connections.append(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # Save message
            supabase.table("messages").insert({
                "user_id": data.get("user_id", "anon"),
                "user_name": data.get("user_name", "Anonymous"),
                "message": data.get("message", ""),
                "created_at": datetime.now().isoformat()
            }).execute()
            
            # Broadcast
            for conn in chat_connections:
                try:
                    await conn.send_json({
                        "user_name": data.get("user_name", "Anonymous"),
                        "message": data.get("message", ""),
                        "created_at": datetime.now().isoformat()
                    })
                except:
                    chat_connections.remove(conn)
    except:
        chat_connections.remove(websocket)

# ================= HEALTH =================
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "exchanges": exchanges_loaded,
        "opps": len(latest_opportunities),
        "round": scan_round,
        "symbols": len(all_symbols)
    }

# ================= API =================
@app.get("/api/opportunities")
async def get_opportunities():
    return latest_opportunities

@app.websocket("/ws")
async def arb_websocket(websocket: WebSocket):
    await websocket.accept()
    last_data = None
    last_round = 0
    while True:
        try:
            current = json.dumps(latest_opportunities, default=str)
            if current != last_data or scan_round != last_round:
                await websocket.send_json({
                    "type": "opps",
                    "data": latest_opportunities,
                    "round": scan_round,
                    "symbols": len(all_symbols)
                })
                last_data = current
                last_round = scan_round
            await asyncio.sleep(0.5)
        except:
            break

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
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>ArbiHunt</title>
    <style>
        :root {
            --bg: #0D0D0D; --card: #141414; --border: #1F1F1F;
            --text: #E0E0E0; --text-secondary: #888;
            --green: #00C853; --red: #FF5252; --orange: #FF9800; --gold: #FFD700;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg); color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh; -webkit-tap-highlight-color: transparent;
        }
        .header {
            position: sticky; top: 0; z-index: 100; background: var(--bg);
            padding: 12px 16px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between;
        }
        .logo {
            font-size: 22px; font-weight: 800;
            background: linear-gradient(135deg, #FFD700, #FFA000);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .pro-lock-banner {
            background: linear-gradient(135deg, rgba(255,152,0,0.15), rgba(255,152,0,0.05));
            border: 1px solid rgba(255,152,0,0.3); border-radius: 12px;
            margin: 12px 16px; padding: 14px 16px;
            display: flex; align-items: center; gap: 12px;
        }
        .lock-text { flex: 1; font-size: 13px; color: var(--orange); font-weight: 600; }
        .go-pro-btn {
            background: linear-gradient(135deg, #FFD700, #FF8F00); color: #000;
            font-weight: 700; font-size: 12px; padding: 10px 18px;
            border-radius: 20px; border: none; cursor: pointer; white-space: nowrap; text-decoration: none;
        }
        .stats-bar {
            display: flex; gap: 8px; padding: 8px 16px; overflow-x: auto;
        }
        .stats-bar::-webkit-scrollbar { display: none; }
        .stat-pill {
            background: var(--card); border: 1px solid var(--border);
            border-radius: 20px; padding: 8px 14px; font-size: 11px;
            white-space: nowrap; color: var(--text-secondary);
            display: flex; align-items: center; gap: 6px;
        }
        .live-dot { width: 7px; height: 7px; background: #00E676; border-radius: 50%; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }
        .stat-pill .count { color: #fff; font-weight: 700; }
        
        .opp-list { padding: 8px 16px 100px; }
        .opp-card {
            background: var(--card); border: 1px solid var(--border);
            border-radius: 14px; margin-bottom: 10px; overflow: hidden;
            cursor: pointer; transition: all 0.2s;
        }
        .opp-card:active { transform: scale(0.98); }
        .card-header {
            padding: 14px 16px; display: flex; align-items: center; justify-content: space-between;
        }
        .card-left { flex: 1; min-width: 0; }
        .exchange-route { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
        .ex-buy { background: rgba(0,200,83,0.15); color: #00C853; padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; }
        .ex-sell { background: rgba(255,82,82,0.15); color: #FF5252; padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; }
        .route-arrow { color: #555; font-size: 12px; }
        .symbol-name { font-size: 18px; font-weight: 700; color: #fff; }
        .card-meta { display: flex; gap: 12px; margin-top: 4px; font-size: 11px; color: var(--text-secondary); }
        .card-right { text-align: right; flex-shrink: 0; }
        .spread-badge { font-size: 24px; font-weight: 800; color: #00C853; }
        .verified-text { font-size: 10px; color: #555; margin-top: 2px; }
        
        .card-detail { display: none; border-top: 1px solid var(--border); background: #0F0F0F; padding: 16px; }
        .card-detail.show { display: block; }
        .detail-section { margin-bottom: 16px; }
        .detail-title { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
        .detail-title .step { background: var(--border); color: var(--text-secondary); width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }
        .detail-row { display: flex; justify-content: space-between; padding: 7px 0; font-size: 13px; border-bottom: 1px solid #1A1A1A; }
        .detail-label { color: #777; } .detail-value { color: #fff; font-weight: 500; }
        .action-btn { display: block; width: 100%; padding: 10px; background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 10px; font-size: 13px; font-weight: 600; text-align: center; cursor: pointer; margin-top: 8px; text-decoration: none; }
        .warning-box { background: rgba(255,152,0,0.1); border-left: 3px solid var(--orange); padding: 10px 12px; border-radius: 6px; font-size: 11px; color: var(--orange); margin-top: 12px; }
        .time-warning { text-align: center; font-size: 11px; color: #FF9800; padding: 8px; background: rgba(255,152,0,0.08); border-radius: 8px; margin-top: 10px; }
        
        .no-data { text-align: center; padding: 60px 20px; color: #444; }
        .no-data .icon { font-size: 48px; margin-bottom: 12px; }
        
        .bottom-nav {
            position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg);
            border-top: 1px solid var(--border); display: flex; justify-content: space-around;
            padding: 10px 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); z-index: 100;
        }
        .nav-item { display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; color: #555; font-size: 10px; font-weight: 600; text-decoration: none; }
        .nav-item.active { color: #FFD700; }
        .nav-item .nav-icon { font-size: 20px; }

        /* Modal */
        .modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); z-index: 200; align-items: center; justify-content: center; }
        .modal.show { display: flex; }
        .modal-content { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; width: 90%; max-width: 400px; }
        .modal-title { font-size: 18px; font-weight: 700; color: #fff; margin-bottom: 16px; text-align: center; }
        .input-group { margin-bottom: 12px; }
        .input-group label { display: block; font-size: 12px; color: #888; margin-bottom: 4px; }
        .input-group input { width: 100%; padding: 10px 14px; background: #1A1A1A; border: 1px solid var(--border); border-radius: 10px; color: #fff; font-size: 14px; }
        .btn-primary { width: 100%; padding: 12px; background: linear-gradient(135deg, #FFD700, #FF8F00); color: #000; border: none; border-radius: 10px; font-size: 14px; font-weight: 700; cursor: pointer; margin-top: 8px; }
        .btn-secondary { width: 100%; padding: 10px; background: transparent; color: #888; border: 1px solid #333; border-radius: 10px; font-size: 13px; cursor: pointer; margin-top: 8px; }
        .plan-card { background: #1A1A1A; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-bottom: 8px; cursor: pointer; }
        .plan-card.selected { border-color: #FFD700; }
        .plan-card .plan-name { font-weight: 700; color: #fff; }
        .plan-card .plan-price { font-size: 20px; font-weight: 800; color: #FFD700; }
        .plan-card .plan-desc { font-size: 11px; color: #888; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">ArbiHunt</div>
        <div style="display:flex;gap:8px;">
            <button class="go-pro-btn" onclick="showProModal()" id="proBtn" style="display:none;">GO PRO</button>
            <button onclick="showAuthModal()" id="loginBtn" style="background:var(--card);color:#fff;border:1px solid var(--border);padding:8px 14px;border-radius:20px;font-size:12px;cursor:pointer;">Sign In</button>
            <span id="userName" style="color:#FFD700;font-weight:600;display:none;cursor:pointer;" onclick="window.location.href='/profile'"></span>
        </div>
    </div>

    <div class="pro-lock-banner" id="proBanner">
        <span style="font-size:24px;">🔒</span>
        <span class="lock-text">Upgrade to PRO to Discover Profit Opportunities Above 2%</span>
        <button class="go-pro-btn" onclick="showProModal()">GO PRO</button>
    </div>

    <div class="stats-bar">
        <div class="stat-pill"><span class="live-dot"></span> LIVE</div>
        <div class="stat-pill">📊 <span class="count" id="oppCount">0</span> opps</div>
        <div class="stat-pill">🔄 Round <span class="count" id="roundNum">0</span></div>
    </div>

    <div class="opp-list" id="oppList">
        <div class="no-data">
            <div class="icon">⚡</div>
            <div style="font-size:14px;">Scanning all USDT pairs...</div>
        </div>
    </div>

    <div class="bottom-nav">
        <a class="nav-item active" href="/"><span class="nav-icon">🏠</span> Home</a>
        <a class="nav-item" href="/chat"><span class="nav-icon">💬</span> Chat</a>
        <a class="nav-item" href="#" onclick="showProModal()"><span class="nav-icon">⭐</span> Go PRO</a>
        <a class="nav-item" href="/profile"><span class="nav-icon">👤</span> Profile</a>
    </div>

    <!-- Auth Modal -->
    <div class="modal" id="authModal">
        <div class="modal-content">
            <div class="modal-title" id="authTitle">Sign In</div>
            <div class="input-group"><label>Email</label><input type="email" id="authEmail" placeholder="you@email.com"></div>
            <div class="input-group"><label>Password</label><input type="password" id="authPassword" placeholder="••••••"></div>
            <div class="input-group" id="nameGroup" style="display:none;"><label>Name</label><input type="text" id="authName" placeholder="Your name"></div>
            <div class="input-group" id="phoneGroup" style="display:none;"><label>Phone (Safaricom)</label><input type="tel" id="authPhone" placeholder="2547XXXXXXXX"></div>
            <button class="btn-primary" onclick="handleAuth()" id="authBtn">Sign In</button>
            <button class="btn-secondary" onclick="toggleAuthMode()"><span id="authToggle">Create Account</span></button>
            <button class="btn-secondary" onclick="closeModal('authModal')">Cancel</button>
            <div id="authError" style="color:#FF5252;font-size:12px;margin-top:8px;text-align:center;"></div>
        </div>
    </div>

    <!-- PRO Upgrade Modal -->
    <div class="modal" id="proModal">
        <div class="modal-content">
            <div class="modal-title">Upgrade to PRO</div>
            <div class="plan-card" onclick="selectPlan('weekly')" id="planWeekly">
                <div class="plan-name">Weekly PRO</div>
                <div class="plan-price">KES 200</div>
                <div class="plan-desc">7 days access</div>
            </div>
            <div class="plan-card selected" onclick="selectPlan('monthly')" id="planMonthly">
                <div class="plan-name">Monthly PRO</div>
                <div class="plan-price">KES 700</div>
                <div class="plan-desc">30 days access • Best Value!</div>
            </div>
            <div class="input-group"><label>Safaricom Phone Number</label><input type="tel" id="mpesaPhone" placeholder="254712345678"></div>
            <button class="btn-primary" onclick="payWithMpesa()">💳 Pay with M-Pesa</button>
            <button class="btn-secondary" onclick="closeModal('proModal')">Cancel</button>
            <div id="payStatus" style="text-align:center;margin-top:8px;font-size:12px;"></div>
        </div>
    </div>

<script>
    let opps = [], expandedCard = null, roundNum = 0;
    let currentUser = null, selectedPlan = 'monthly';
    let ws, chatWs;
    
    // Check stored session
    const storedUser = localStorage.getItem('arbihunt_user');
    if (storedUser) {
        currentUser = JSON.parse(storedUser);
        updateUIForUser();
    }
    
    function connectWS() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws`);
        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'opps') {
                opps = data.data;
                roundNum = data.round || roundNum;
                render();
            }
        };
        ws.onclose = () => setTimeout(connectWS, 2000);
    }
    
    function ago(ts) {
        const s = Math.floor(Date.now()/1000 - ts);
        if (s < 5) return 'Just now';
        if (s < 60) return `${s}s ago`;
        return `${Math.floor(s/60)}m ago`;
    }
    
    function fmtEx(name) {
        const map = {'MEXC':'MEXC','GATEIO':'Gate.io','KUCOIN':'KuCoin','COINEX':'CoinEx','BITGET':'Bitget','BINGX':'BingX'};
        return map[name] || name;
    }
    
    function toggleCard(idx, e) {
        e.stopPropagation();
        const detail = document.getElementById(`detail-${idx}`);
        if (expandedCard === idx) { detail.classList.remove('show'); expandedCard = null; }
        else {
            if (expandedCard !== null) document.getElementById(`detail-${expandedCard}`)?.classList.remove('show');
            detail.classList.add('show'); expandedCard = idx;
        }
    }
    
    function render() {
        document.getElementById('oppCount').textContent = opps.length;
        document.getElementById('roundNum').textContent = roundNum;
        const container = document.getElementById('oppList');
        
        if (!opps.length) {
            container.innerHTML = '<div class="no-data"><div class="icon">⚡</div><div style="font-size:14px;">Scanning all USDT pairs...</div></div>';
            return;
        }
        
        container.innerHTML = opps.map((o, i) => `
            <div class="opp-card" onclick="toggleCard(${i}, event)">
                <div class="card-header">
                    <div class="card-left">
                        <div class="exchange-route">
                            <span class="ex-buy">BUY</span> ${fmtEx(o.buy_exchange)}
                            <span class="route-arrow">→</span>
                            <span class="ex-sell">SELL</span> ${fmtEx(o.sell_exchange)}
                        </div>
                        <div class="symbol-name">${o.symbol}/USDT</div>
                        <div class="card-meta">
                            <span>💰 $${Number(o.liquidity).toLocaleString()}</span>
                            <span>⏱ ${ago(o.timestamp)}</span>
                        </div>
                    </div>
                    <div class="card-right">
                        <div class="spread-badge">${o.spread}%</div>
                        <div class="verified-text">Verified ${ago(o.timestamp)}</div>
                    </div>
                </div>
                <div class="card-detail" id="detail-${i}">
                    <div class="detail-section">
                        <div class="detail-title"><span class="step">1</span> Buy at ${fmtEx(o.buy_exchange)}</div>
                        <div class="detail-row"><span class="detail-label">Lowest Ask:</span><span class="detail-value">$${o.buy_price}</span></div>
                        <div class="detail-row"><span class="detail-label">Liquidity:</span><span class="detail-value">$${Number(o.buy_liquidity).toLocaleString()}</span></div>
                    </div>
                    <div class="detail-section">
                        <div class="detail-title"><span class="step">2</span> Sell on ${fmtEx(o.sell_exchange)}</div>
                        <div class="detail-row"><span class="detail-label">Highest Bid:</span><span class="detail-value">$${o.sell_price}</span></div>
                        <div class="detail-row"><span class="detail-label">Liquidity:</span><span class="detail-value">$${Number(o.sell_liquidity).toLocaleString()}</span></div>
                    </div>
                    <div class="detail-row"><span class="detail-label">Spread:</span><span class="detail-value" style="color:#00C853;">${o.spread}%</span></div>
                    <div class="detail-row"><span class="detail-label">Net Profit:</span><span class="detail-value">${o.net_profit}%</span></div>
                    <div class="warning-box">⚠️ Double check coin's contract and name on both exchanges before initiating the trade.</div>
                    <div class="time-warning">🟢 Act Fast! Arbitrage opportunities are time-sensitive and typically last for no more than 10-15 minutes.</div>
                </div>
            </div>
        `).join('');
        expandedCard = null;
    }
    
    function showAuthModal() {
        document.getElementById('authModal').classList.add('show');
        document.getElementById('authError').textContent = '';
    }
    
    function closeModal(id) {
        document.getElementById(id).classList.remove('show');
    }
    
    let isSignUp = false;
    function toggleAuthMode() {
        isSignUp = !isSignUp;
        document.getElementById('authTitle').textContent = isSignUp ? 'Create Account' : 'Sign In';
        document.getElementById('authBtn').textContent = isSignUp ? 'Create Account' : 'Sign In';
        document.getElementById('authToggle').textContent = isSignUp ? 'Already have an account? Sign In' : 'Create Account';
        document.getElementById('nameGroup').style.display = isSignUp ? 'block' : 'none';
        document.getElementById('phoneGroup').style.display = isSignUp ? 'block' : 'none';
        document.getElementById('authError').textContent = '';
    }
    
    async function handleAuth() {
        const email = document.getElementById('authEmail').value;
        const password = document.getElementById('authPassword').value;
        const name = document.getElementById('authName').value;
        const phone = document.getElementById('authPhone').value;
        
        if (!email || !password) {
            document.getElementById('authError').textContent = 'Please fill all fields';
            return;
        }
        
        const endpoint = isSignUp ? '/api/auth/signup' : '/api/auth/login';
        const body = isSignUp ? { email, password, name, phone } : { email, password };
        
        try {
            const res = await fetch(endpoint, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
            const data = await res.json();
            
            if (data.success) {
                currentUser = data.user || { email, name, phone };
                localStorage.setItem('arbihunt_user', JSON.stringify(currentUser));
                updateUIForUser();
                closeModal('authModal');
            } else {
                document.getElementById('authError').textContent = data.error || 'Error';
            }
        } catch(e) {
            document.getElementById('authError').textContent = 'Network error';
        }
    }
    
    function updateUIForUser() {
        document.getElementById('loginBtn').style.display = 'none';
        document.getElementById('userName').style.display = 'inline';
        document.getElementById('userName').textContent = currentUser?.name || currentUser?.email || 'User';
        checkProStatus();
    }
    
    async function checkProStatus() {
        if (!currentUser?.id) return;
        try {
            const res = await fetch(`/api/user/status?user_id=${currentUser.id}`);
            const data = await res.json();
            if (data.is_pro) {
                document.getElementById('proBanner').style.display = 'none';
                document.getElementById('proBtn').style.display = 'none';
            } else {
                document.getElementById('proBtn').style.display = 'inline-block';
            }
        } catch(e) {}
    }
    
    function showProModal() {
        document.getElementById('proModal').classList.add('show');
        document.getElementById('payStatus').textContent = '';
        if (currentUser?.phone) {
            document.getElementById('mpesaPhone').value = currentUser.phone;
        }
    }
    
    function selectPlan(plan) {
        selectedPlan = plan;
        document.getElementById('planWeekly').classList.remove('selected');
        document.getElementById('planMonthly').classList.remove('selected');
        document.getElementById(`plan${plan.charAt(0).toUpperCase() + plan.slice(1)}`).classList.add('selected');
    }
    
    async function payWithMpesa() {
        const phone = document.getElementById('mpesaPhone').value;
        if (!phone) {
            document.getElementById('payStatus').innerHTML = '<span style="color:#FF5252;">Enter phone number</span>';
            return;
        }
        
        document.getElementById('payStatus').innerHTML = '<span style="color:#FFD700;">Sending payment prompt...</span>';
        
        try {
            const res = await fetch('/api/mpesa/stkpush', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({
                    phone: phone,
                    plan: selectedPlan,
                    user_id: currentUser?.id || ''
                })
            });
            const data = await res.json();
            
            if (data.success) {
                document.getElementById('payStatus').innerHTML = '<span style="color:#00C853;">✅ Payment prompt sent! Check your phone and enter PIN.</span>';
            } else {
                document.getElementById('payStatus').innerHTML = `<span style="color:#FF5252;">Error: ${data.error}</span>`;
            }
        } catch(e) {
            document.getElementById('payStatus').innerHTML = '<span style="color:#FF5252;">Network error</span>';
        }
    }
    
    connectWS();
    setInterval(async () => {
        if (ws.readyState !== WebSocket.OPEN) {
            try { const res = await fetch('/api/opportunities'); opps = await res.json(); render(); } catch(e) {}
        }
    }, 5000);
</script>
</body>
</html>
"""

def get_chat_html():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>ArbiHunt - Chat</title>
    <style>
        :root { --bg: #0D0D0D; --card: #141414; --border: #1F1F1F; --text: #E0E0E0; --gold: #FFD700; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; min-height: 100vh; }
        .header { position: sticky; top: 0; z-index: 100; background: var(--bg); padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
        .logo { font-size: 22px; font-weight: 800; background: linear-gradient(135deg, #FFD700, #FFA000); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .chat-container { padding: 12px 16px; padding-bottom: 160px; }
        .message { margin-bottom: 10px; animation: fadeIn 0.3s; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .message-name { font-size: 11px; font-weight: 700; color: #FFD700; margin-bottom: 2px; }
        .message-text { background: var(--card); padding: 10px 14px; border-radius: 12px; font-size: 14px; display: inline-block; max-width: 80%; }
        .message-time { font-size: 9px; color: #555; margin-top: 2px; }
        .input-bar { position: fixed; bottom: 70px; left: 0; right: 0; background: var(--bg); border-top: 1px solid var(--border); padding: 10px 16px; display: flex; gap: 8px; z-index: 100; }
        .input-bar input { flex: 1; padding: 10px 14px; background: #1A1A1A; border: 1px solid var(--border); border-radius: 20px; color: #fff; font-size: 14px; }
        .input-bar button { background: linear-gradient(135deg, #FFD700, #FF8F00); color: #000; border: none; border-radius: 20px; padding: 10px 18px; font-weight: 700; cursor: pointer; font-size: 13px; }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg); border-top: 1px solid var(--border); display: flex; justify-content: space-around; padding: 10px 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); z-index: 100; }
        .nav-item { display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; color: #555; font-size: 10px; font-weight: 600; text-decoration: none; }
        .nav-item.active { color: #FFD700; }
        .nav-item .nav-icon { font-size: 20px; }
    </style>
</head>
<body>
    <div class="header"><div class="logo">ArbiHunt Chat</div></div>
    <div class="chat-container" id="chatContainer">
        <div style="text-align:center;color:#444;padding:40px;">Loading messages...</div>
    </div>
    <div class="input-bar">
        <input type="text" id="chatInput" placeholder="Share your story..." onkeypress="if(event.key==='Enter')sendMessage()">
        <button onclick="sendMessage()">Send</button>
    </div>
    <div class="bottom-nav">
        <a class="nav-item" href="/"><span class="nav-icon">🏠</span> Home</a>
        <a class="nav-item active" href="/chat"><span class="nav-icon">💬</span> Chat</a>
        <a class="nav-item" href="#" onclick="window.location.href='/?pro=true'"><span class="nav-icon">⭐</span> Go PRO</a>
        <a class="nav-item" href="/profile"><span class="nav-icon">👤</span> Profile</a>
    </div>

<script>
    let chatSocket;
    const user = JSON.parse(localStorage.getItem('arbihunt_user') || '{}');
    const userName = user.name || user.email || 'Anonymous';
    const userId = user.id || 'anon';
    
    function connectChat() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        chatSocket = new WebSocket(`${protocol}//${location.host}/ws/chat`);
        
        chatSocket.onmessage = (e) => {
            const data = JSON.parse(e.data);
            addMessage(data.user_name || 'Anon', data.message, data.created_at);
        };
        
        chatSocket.onclose = () => setTimeout(connectChat, 2000);
    }
    
    function addMessage(name, text, time) {
        const container = document.getElementById('chatContainer');
        if (container.querySelector('div[style]')) container.innerHTML = '';
        
        const d = new Date(time || Date.now());
        const timeStr = d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        
        const div = document.createElement('div');
        div.className = 'message';
        div.innerHTML = `<div class="message-name">${name}</div><div class="message-text">${text}</div><div class="message-time">${timeStr}</div>`;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }
    
    function sendMessage() {
        const input = document.getElementById('chatInput');
        const msg = input.value.trim();
        if (!msg) return;
        
        if (chatSocket && chatSocket.readyState === WebSocket.OPEN) {
            chatSocket.send(JSON.stringify({
                user_id: userId,
                user_name: userName,
                message: msg
            }));
        }
        
        input.value = '';
    }
    
    async function loadMessages() {
        try {
            const res = await fetch('/api/chat/messages?limit=50');
            const data = await res.json();
            const container = document.getElementById('chatContainer');
            if (data.messages && data.messages.length > 0) {
                container.innerHTML = '';
                data.messages.forEach(m => addMessage(m.user_name, m.message, m.created_at));
            }
        } catch(e) {}
    }
    
    loadMessages();
    connectChat();
</script>
</body>
</html>
"""

def get_profile_html():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>ArbiHunt - Profile</title>
    <style>
        :root { --bg: #0D0D0D; --card: #141414; --border: #1F1F1F; --text: #E0E0E0; --gold: #FFD700; --red: #FF5252; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; min-height: 100vh; }
        .header { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
        .logo { font-size: 22px; font-weight: 800; background: linear-gradient(135deg, #FFD700, #FFA000); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .profile-card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; margin: 16px; padding: 24px; text-align: center; }
        .avatar { width: 70px; height: 70px; background: linear-gradient(135deg, #FFD700, #FF8F00); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 30px; margin: 0 auto 12px; }
        .profile-name { font-size: 20px; font-weight: 700; color: #fff; }
        .profile-email { font-size: 13px; color: #888; margin-top: 4px; }
        .pro-badge { display: inline-block; background: linear-gradient(135deg, #FFD700, #FF8F00); color: #000; padding: 6px 16px; border-radius: 20px; font-weight: 700; font-size: 12px; margin-top: 12px; }
        .info-row { display: flex; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border); font-size: 14px; }
        .info-label { color: #888; }
        .logout-btn { width: calc(100% - 32px); margin: 16px; padding: 14px; background: transparent; border: 1px solid var(--red); color: var(--red); border-radius: 12px; font-size: 14px; font-weight: 600; cursor: pointer; }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg); border-top: 1px solid var(--border); display: flex; justify-content: space-around; padding: 10px 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); z-index: 100; }
        .nav-item { display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; color: #555; font-size: 10px; font-weight: 600; text-decoration: none; }
        .nav-item.active { color: #FFD700; }
        .nav-item .nav-icon { font-size: 20px; }
    </style>
</head>
<body>
    <div class="header"><div class="logo">ArbiHunt</div></div>
    <div class="profile-card" id="profileCard">
        <div class="avatar" id="avatarInitial">?</div>
        <div class="profile-name" id="profileName">Loading...</div>
        <div class="profile-email" id="profileEmail"></div>
        <div id="proStatus"></div>
    </div>
    <div class="info-row"><span class="info-label">Member Since</span><span id="memberSince">-</span></div>
    <div class="info-row"><span class="info-label">Account ID</span><span id="accountId">-</span></div>
    <button class="logout-btn" onclick="logout()">Logout</button>
    <div class="bottom-nav">
        <a class="nav-item" href="/"><span class="nav-icon">🏠</span> Home</a>
        <a class="nav-item" href="/chat"><span class="nav-icon">💬</span> Chat</a>
        <a class="nav-item" href="#" onclick="window.location.href='/?pro=true'"><span class="nav-icon">⭐</span> Go PRO</a>
        <a class="nav-item active" href="/profile"><span class="nav-icon">👤</span> Profile</a>
    </div>

<script>
    const user = JSON.parse(localStorage.getItem('arbihunt_user') || '{}');
    
    if (!user.email) {
        window.location.href = '/';
    }
    
    document.getElementById('profileName').textContent = user.name || 'User';
    document.getElementById('profileEmail').textContent = user.email || '';
    document.getElementById('avatarInitial').textContent = (user.name || 'U')[0].toUpperCase();
    document.getElementById('accountId').textContent = user.id || 'N/A';
    
    if (user.is_pro) {
        document.getElementById('proStatus').innerHTML = '<span class="pro-badge">⭐ PRO Member</span>';
    }
    
    function logout() {
        localStorage.removeItem('arbihunt_user');
        window.location.href = '/';
    }
</script>
</body>
</html>
"""

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 10000))
    print(f"\n{'='*50}")
    print(f"⚡ ARBIHUNT v3.0 - With Auth, M-Pesa & Chat")
    print(f"{'='*50}")
    uvicorn.run(app, host="0.0.0.0", port=port)
