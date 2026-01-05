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
import json

# --- Setup ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]
ACCOUNT_ID = int(config["express_account_id"])

TOKEN = None
pending_entries = {}  # entry_id: sl_order_id (Only tracked while Entry is active/unfilled)
contract_map = {}
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
templates = Jinja2Templates(directory="templates")
# --- Global HTTPX Client (reused) ---
client = httpx.AsyncClient(verify=False, timeout=10)

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
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{API_URL}{endpoint}" if endpoint.startswith("/api") else endpoint
    try:
        if method.upper() == "POST":
            res = await client.post(url, json=payload, headers=headers)
        else:
            headers.update({"x-app-type": "px-desktop", "x-app-version": "1.21.1"})
            res = await client.get(url, headers=headers)
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

# --- Async Contract Loader ---
async def load_contracts(token: str):
    try:
        res = await client.get(
            "https://userapi.topstepx.com/UserContract/active/nonprofesional",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "x-app-type": "px-desktop",
                "x-app-version": "1.21.1"
            }
        )
        res.raise_for_status()
        contracts = res.json()
        if not isinstance(contracts, list):
            logging.warning("Unexpected contract format.")
            return

        short_symbol_map = {
            "ENQ": "NQ", "EP": "ES", "GCE": "GC", "SIE": "SI",
            "CPE": "HG", "GLE": "LE", "EU6": "6E", "PLE": "PL"
        }

        for c in contracts:
            if c.get("disabled"):
                continue
            product_id = c.get("productId")
            if not product_id or not c.get("contractId"):
                continue

            parts = product_id.split(".")
            if len(parts) >= 3:
                short_symbol = parts[-1]
                short_symbol = short_symbol_map.get(short_symbol, short_symbol)

                contract_map[short_symbol] = {
                    "contractId": c["contractId"],
                    "tickValue": c["tickValue"],
                    "tickSize": c["tickSize"],
                    "pointValue": c["pointValue"],
                    "exchangeFee": c["exchangeFee"],
                    "regulatoryFee": c["regulatoryFee"],
                    "totalFees": c["totalFees"],
                    "decimalPlaces": c["decimalPlaces"],
                    "priceScale": c["priceScale"]
                }

        logging.info(f"Loaded {len(contract_map)} contracts")
    except Exception as e:
        logging.error(f"UserContract load error: {e}")

# --- Lifespan Hook ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    token, _ = await get_token()
    if token:
        await load_contracts(token)   # populate global contract_map once
    yield
    await client.aclose()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def index(request: Request):
    priority = ["YM", "MYM", "NQ", "MNQ", "GC", "MGC", "ES", "MES"]
    sorted_symbols = priority + [s for s in contract_map.keys() if s not in priority]
    return templates.TemplateResponse("order_form.html", {"request": request, "symbols": sorted_symbols})

# Add this near the top with other globals
oco_orders = {}  # entry_id: [tp_order_id, sl_order_id]

@app.post("/place-oco")
async def place_oco(data: OCORequest):
    def get_precision(tick_size):
        return max(0, -int(math.floor(math.log10(tick_size)))) if tick_size > 0 else 0

    def round_to_tick(value, tick_size):
        precision = get_precision(tick_size) + 1
        ticks = math.floor(value / tick_size)
        floored = ticks * tick_size
        return round(floored, precision)

    # --- Extract request data ---
    quantity = int(getattr(data, "quantity", 1))
    op, tp, sl = data.op, data.tp, data.sl
    symbol = data.symbol.upper()
    custom_tag = getattr(data, "customTag", None)

    # --- Auth & account info ---
    token, account_info = await get_token()
    if not token or not account_info:
        raise HTTPException(500, "Authentication failed")

    balance = account_info.get("balance")
    maximum_loss = account_info.get("maximumLoss")
    if balance is None or maximum_loss is None:
        raise HTTPException(500, "Missing account data")

    # --- Risk sizing ---
    risk_pct = 0.01
    micro_to_standard = {
        "MNQ": "NQ", "MYM": "YM", "MGC": "GC", "MES": "ES",
        "SIL": "SI", "MHG": "HG", "M6E": "6E"
    }
    standard_to_micro = {v: k for k, v in micro_to_standard.items()}
    micro_symbol = standard_to_micro.get(symbol, symbol)
    micro_contract = contract_map.get(micro_symbol)
    if not micro_contract:
        raise HTTPException(400, f"Micro contract not found: {micro_symbol}")

    tick_size = micro_contract["tickSize"]
    tick_value = micro_contract["tickValue"]
    contract_id = micro_contract["contractId"]

    op = round_to_tick(op, tick_size)
    tp = round_to_tick(tp, tick_size)
    sl = round_to_tick(sl, tick_size)

    if op > sl:
        op += tick_size * 11
    else:
        op -= tick_size

    sl_ticks = abs(op - sl) / tick_size
    if sl_ticks == 0:
        raise HTTPException(400, "SL too close to OP")

    risk_budget = (balance - maximum_loss) * risk_pct
    # if custom_tag == "AllTimeLongES":
    #     return
    #     # risk_budget = (balance - maximum_loss) - 64

    dynamic_contracts = math.floor(risk_budget / (sl_ticks * tick_value))
    quantity = max(dynamic_contracts, 1)

    # Upgrade to standard contract if sizing is large
    if quantity >= 10 and micro_symbol in micro_to_standard:
        symbol = micro_to_standard[micro_symbol]
        contract = contract_map.get(symbol)
        if not contract:
            raise HTTPException(400, f"Standard symbol not found: {symbol}")
        contract_id = contract["contractId"]
        tick_size = contract["tickSize"]
        tick_value = contract["tickValue"]
        quantity = int(risk_budget / (sl_ticks * tick_value))

        op = round_to_tick(op, tick_size)
        tp = round_to_tick(tp, tick_size)
        sl = round_to_tick(sl, tick_size)

    side = 0 if op < tp else 1
    size = abs(quantity)

    # --- Logging message ---
    message = {
        "contract": contract_id,
        "side": side,
        "size": size,
        "op": op,
        "sl": sl,
        "tp": tp,
        "balance": balance,
        "maximum_loss": maximum_loss,
        "risk_budget": risk_budget,
        "message": "OCO placed"
    }
    logging.info(message)
    Alert(json.dumps(message))

    # --- Place entry order ---
    entry = await api_call("POST", "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": 1,
        "side": side,
        "size": size,
        "limitPrice": op
    }, token=token)

    entry_id = entry.get("orderId")
    if not entry_id:
        raise HTTPException(500, "Entry order failed")

    # --- Place SL order ---
    await asyncio.sleep(0.3)
    sl_order = await api_call("POST", "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": 4,
        "side": 1 - side,
        "size": size,
        "stopPrice": sl,
        "linkedOrderId": entry_id
    }, token=token)

    sl_id = sl_order.get("orderId")
    if not sl_id:
        raise HTTPException(500, "SL order failed")

    # --- Lifecycle monitor for TP ---
    asyncio.create_task(monitor_entry_and_place_tp(
        entry_id=entry_id,
        contract_id=contract_id,
        side=side,
        size=size,
        tp=tp,
        sl_id=sl_id
    ))

    oco_orders[entry_id] = [None, sl_id]

    return {
        "entryOrderId": entry_id,
        "contractId": contract_id,
        "tickSize": tick_size,
        "tickValue": tick_value,
        "balance": balance,
        "maximum_loss": maximum_loss,
        "risk_budget": risk_budget,
        "message": "OCO placed"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)