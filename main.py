import asyncio
import logging
import math
import datetime
import yaml
import httpx
import urllib3
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict
from contextlib import asynccontextmanager

from modules.discord import Alert

# --- Setup ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]
ACCOUNT_ID = int(config["account_id"])

TOKEN = None
pending_entries = {}  # entry_id: sl_order_id (Only tracked while Entry is active/unfilled)
contract_map = {}
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
templates = Jinja2Templates(directory="templates")

class OCORequest(BaseModel):
    symbol: str
    op: float
    tp: float
    sl: float
    quantity: Optional[int] = 1
    customTag: Optional[str] = None

# --- Helpers ---
def get_precision(tick_size):
    return max(0, -int(math.floor(math.log10(tick_size)))) if tick_size > 0 else 0

def round_to_tick(value, tick_size):
    precision = get_precision(tick_size) + 1
    return round(math.floor(value / tick_size) * tick_size, precision)

async def api_call(method: str, endpoint: str, payload: dict = None, token: str = None):
    async with httpx.AsyncClient(verify=False) as client:
        headers = {"Content-Type": "application/json"}
        if token: headers["Authorization"] = f"Bearer {token}"
        url = f"{API_URL}{endpoint}" if endpoint.startswith("/api") else endpoint
        try:
            if method.upper() == "POST":
                res = await client.post(url, json=payload, headers=headers, timeout=10)
            else:
                headers.update({"x-app-type": "px-desktop", "x-app-version": "1.21.1"})
                res = await client.get(url, headers=headers, timeout=10)
            return res.json()
        except Exception as e:
            logging.error(f"API Error: {e}")
            return {}

async def get_token():
    global TOKEN
    res = await api_call("POST", "/api/Auth/loginKey", {"userName": USERNAME, "apiKey": API_KEY})
    if res.get("success"):
        TOKEN = res.get("token")
        acc_url = "https://userapi.topstepx.com/TradingAccount"
        accs = await api_call("GET", acc_url, token=TOKEN)
        info = next((a for a in accs if a.get("accountId") == ACCOUNT_ID), None) if isinstance(accs, list) else None
        return TOKEN, info
    return None, None

# --- Main Logic ---

async def monitor_entry_and_place_tp(entry_id, contract_id, side, size, tp, sl_id):
    """
    Handles the lifecycle of the order set:
    1. If Entry is CANCELED manually -> Cancel the SL and stop.
    2. If Entry is FILLED -> Place the TP and stop (SL stays alive).
    """
    pending_entries[entry_id] = sl_id
    
    while True:
        await asyncio.sleep(0.5)
        token, _ = await get_token()
        if not token: continue

        # Check Order History for Status
        now = datetime.datetime.now(datetime.timezone.utc)
        start = (now - datetime.timedelta(minutes=5)).isoformat()
        res = await api_call("POST", "/api/Order/search", {"accountId": ACCOUNT_ID, "startTimestamp": start}, token=token)
        
        order_data = next((o for o in res.get("orders", []) if o.get("id") == entry_id), None)
        
        if not order_data:
            continue

        # --- CASE A: Entry Filled ---
        if order_data.get("filledPrice") is not None:
            logging.info(f"Entry {entry_id} filled. Placing TP. Leaving SL {sl_id} alone.")
            
            # Place TP
            await api_call("POST", "/api/Order/place", {
                "accountId": ACCOUNT_ID, "contractId": contract_id,
                "type": 1, "side": 1 - side, "size": size,
                "limitPrice": tp, "linkedOrderId": entry_id
            }, token=token)
            
            # REMOVE from protection list so monitor stops
            pending_entries.pop(entry_id, None)
            return # EXIT TASK: Script will no longer touch this trade

        # --- CASE B: Entry Canceled/Rejected ---
        status = order_data.get("status")
        if status in [2, 3]: # 2=Canceled, 3=Rejected
            logging.info(f"Entry {entry_id} was manually canceled or rejected. Killing SL {sl_id}")
            
            await api_call("POST", "/api/Order/cancel", {
                "accountId": ACCOUNT_ID, "orderId": sl_id
            }, token=token)
            
            pending_entries.pop(entry_id, None)
            return # EXIT TASK

# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    token, _ = await get_token()
    if token:
        c_url = "https://userapi.topstepx.com/UserContract/active/nonprofesional"
        contracts = await api_call("GET", c_url, token=token)
        for c in contracts:
            if c.get("disabled"): continue
            p_id = c.get("productId", "").split(".")[-1]
            contract_map[p_id] = c
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def index(request: Request):
    priority = ["YM", "MYM", "NQ", "MNQ", "GC", "MGC", "ES", "MES"]
    sorted_symbols = priority + [s for s in contract_map.keys() if s not in priority]
    return templates.TemplateResponse("order_form.html", {"request": request, "symbols": sorted_symbols})

@app.post("/place-oco")
async def place_oco(data: OCORequest):
    token, account_info = await get_token()
    if not token: raise HTTPException(500, "Auth Failed")

    symbol = data.symbol.upper()
    c = contract_map.get(symbol)
    if not c: raise HTTPException(400, "Symbol not found")

    op = round_to_tick(data.op, c["tickSize"])
    tp = round_to_tick(data.tp, c["tickSize"])
    sl = round_to_tick(data.sl, c["tickSize"])
    side = 0 if op < tp else 1
    
    # 1. Place Entry
    entry = await api_call("POST", "/api/Order/place", {
        "accountId": ACCOUNT_ID, "contractId": c["contractId"],
        "type": 1, "side": side, "size": data.quantity, "limitPrice": op
    }, token=token)

    e_id = entry.get("orderId")
    if not e_id: raise HTTPException(500, "Entry placement failed")

    # 2. Place SL immediately
    sl_res = await api_call("POST", "/api/Order/place", {
        "accountId": ACCOUNT_ID, "contractId": c["contractId"],
        "type": 4, "side": 1 - side, "size": data.quantity,
        "stopPrice": sl, "linkedOrderId": e_id
    }, token=token)
    sl_id = sl_res.get("orderId")

    # 3. Start Lifecycle Monitor
    asyncio.create_task(monitor_entry_and_place_tp(e_id, c["contractId"], side, data.quantity, tp, sl_id))

    Alert(f"🔥 Set Placed: {symbol} @ {op}")
    return {"status": "success", "entry": e_id, "sl": sl_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)