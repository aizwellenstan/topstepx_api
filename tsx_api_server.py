import requests
import yaml
import asyncio
import logging
import urllib3
# from quart import Quart, request, jsonify
from quart import Quart, render_template, request, jsonify
from modules.discord import Alert
import json

# --- Suppress SSL warnings ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Load config ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]
ACCOUNT_ID = int(config["account_id"])

app = Quart(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

oco_orders = {}  # entry_id: [tp_id, sl_id]
contract_map = {}  # "MYM" → full contract metadata dict

# --- Auth ---
# def get_token():
#     try:
#         res = requests.post(
#             f"{API_URL}/api/Auth/loginKey",
#             json={"userName": USERNAME, "apiKey": API_KEY},
#             headers={"Content-Type": "application/json"},
#             timeout=10,
#             verify=False
#         )
#         res.raise_for_status()
#         data = res.json()
#         return data.get("token") if data.get("success") else None
#     except Exception as e:
#         logging.error(f"Auth error: {e}")
#         return None
TOKEN = None
def get_token(force_refresh=False):
    """
    Return a valid token. 
    If existing token fails account-info test, fetch a new one.
    """
    global TOKEN
    if TOKEN and not force_refresh:
        # test token by calling account info
        if _test_token(TOKEN):
            return TOKEN
        else:
            logging.info("Stored token invalid, refreshing...")
            TOKEN = None  # reset

    # request new token
    try:
        res = requests.post(
            f"{API_URL}/api/Auth/loginKey",
            json={"userName": USERNAME, "apiKey": API_KEY},
            headers={"Content-Type": "application/json"},
            timeout=10,
            verify=False
        )
        res.raise_for_status()
        data = res.json()
        TOKEN = data.get("token") if data.get("success") else None
        return TOKEN
    except Exception as e:
        logging.error(f"Auth error: {e}")
        return None


def _test_token(token):
    """Try account info with given token. Return True if valid."""
    try:
        res = requests.get(
            "https://userapi.topstepx.com/TradingAccount",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "x-app-type": "px-desktop",
                "x-app-version": "1.21.1"
            },
            timeout=10,
            verify=False
        )
        if res.status_code == 200:
            return True
        return False
    except Exception:
        return False

# --- API POST ---
def api_post(token, endpoint, payload):
    try:
        res = requests.post(
            f"{API_URL}{endpoint}",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            verify=False
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logging.error(f"API error on {endpoint}: {e}")
        return {}

# --- Cancel Order ---
def cancel_order(token, account_id, order_id):
    try:
        res = requests.post(
            f"{API_URL}/api/Order/cancel",
            json={"accountId": account_id, "orderId": order_id},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            verify=False
        )
        res.raise_for_status()
        return res.json().get("success", False)
    except Exception as e:
        logging.error(f"Cancel failed for {order_id}: {e}")
        return False

# --- Load Contracts ---
def load_contracts():
    token = get_token()
    if not token:
        logging.error("Contract preload failed: auth error")
        return

    try:
        res = requests.get(
            "https://userapi.topstepx.com/UserContract/active/nonprofesional",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "x-app-type": "px-desktop",
                "x-app-version": "1.21.1"
            },
            verify=False
        )
        res.raise_for_status()
        contracts = res.json()
        if not isinstance(contracts, list):
            logging.warning("Unexpected contract format.")
            return

        for c in contracts:
            if c.get("disabled"):
                continue
            product_id = c.get("productId")
            if not product_id or not c.get("contractId"):
                continue
            parts = product_id.split(".")
            if len(parts) >= 3:
                short_symbol = parts[-1]
                if short_symbol == "ENQ": short_symbol = "NQ"
                elif short_symbol == "EP": short_symbol = "ES"
                elif short_symbol == "GCE": short_symbol = "GC"
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
        print("\n--- Contract Map ---")
        print(contract_map)
        for k, v in contract_map.items():
            print(f"{k}: {v['contractId']}")
        
    except Exception as e:
        logging.error(f"UserContract load error: {e}")

# --- Monitor OCO Orders ---
async def monitor_oco_orders():
    while True:
        if not oco_orders:
            await asyncio.sleep(0.3)
            continue

        token = get_token()
        if not token:
            await asyncio.sleep(0.3)
            continue

        response = api_post(token, "/api/Order/searchOpen", {"accountId": ACCOUNT_ID})
        orders = response.get("orders", [])
        active_ids = {o["id"] for o in orders if "id" in o}

        for entry_id, linked_ids in list(oco_orders.items()):
            if not entry_id or not all(linked_ids):
                continue

            tp_id, sl_id = linked_ids
            tp_missing = tp_id not in active_ids
            sl_missing = sl_id not in active_ids

            # If either SL or TP is triggered, cancel the other
            if tp_missing or sl_missing:
                remaining_id = sl_id if tp_missing else tp_id
                if remaining_id in active_ids:
                    success = cancel_order(token, ACCOUNT_ID, remaining_id)
                    if success:
                        logging.info(f"Canceled remaining OCO leg: {remaining_id}")
                    else:
                        logging.warning(f"Failed to cancel remaining leg: {remaining_id}")
                else:
                    logging.info(f"Remaining leg already inactive: {remaining_id}")

                # Remove the OCO group from tracking
                del oco_orders[entry_id]

        await asyncio.sleep(0.3)

def get_account_info(token):
    try:
        res = requests.get(
            "https://userapi.topstepx.com/TradingAccount",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "x-app-type": "px-desktop",
                "x-app-version": "1.21.1"
            },
            verify=False
        )
        res.raise_for_status()
        accounts = res.json()
        if not isinstance(accounts, list) or not accounts:
            logging.warning("No account data found.")
            return None

        for account in accounts:
            if account.get("accountId") == ACCOUNT_ID:
                return account

        logging.warning(f"No account found with id: {ACCOUNT_ID}")
        return None
    except Exception as e:
        logging.error(f"Account info fetch error: {e}")
        return None

# --- Place OCO ---
async def place_oco_generic(data, entry_type):
    def round_to_tick(value, tick_size):
        return round(value / tick_size) * tick_size

    quantity = int(data.get("quantity", 1))
    op = data.get("op")
    tp = data.get("tp")
    sl = data.get("sl")
    symbol = data.get("symbol", "").upper()
    custom_tag = data.get("customTag")

    contract = contract_map.get(symbol)
    if not contract:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 400

    tick_size = contract["tickSize"]
    tick_value = contract["tickValue"]
    contract_id = contract["contractId"]

    # Round prices to tick size
    op = round_to_tick(op, tick_size)
    tp = round_to_tick(tp, tick_size)
    sl = round_to_tick(sl, tick_size)

    if op > sl: op += tick_size

    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    account_info = get_account_info(token)
    print(account_info)
    if not account_info:
        return jsonify({"error": "Failed to fetch account data"}), 500

    balance = account_info.get("balance")
    maximum_loss = account_info.get("maximumLoss")
    if balance is None or maximum_loss is None:
        return jsonify({"error": "Missing account data"}), 500

    sl_ticks = abs(op - sl) / tick_size
    if sl_ticks == 0:
        return jsonify({"error": "SL too close to OP"}), 400

    risk_budget = (balance - maximum_loss) * 0.24
    quantity = int(risk_budget / (sl_ticks * tick_value))
    if quantity > 2 and tick_value >= 5 and risk_budget < 1500:
        quantity = 2
    print(risk_budget)
    if quantity <= 0:
        return jsonify({"error": "Calculated quantity is zero"}), 400

    micro_to_standard = {
        "MNQ": "NQ",
        "MYM": "YM",
        "MGC": "GC",
        "MES": "ES"
    }

    if quantity >= 10 and symbol in micro_to_standard:
        symbol = micro_to_standard[symbol]
        contract = contract_map.get(symbol)
        if not contract:
            return jsonify({"error": f"Standard symbol not found: {symbol}"}), 400
        contract_id = contract["contractId"]
        tick_size = contract["tickSize"]
        tick_value = contract["tickValue"]
        quantity = int(risk_budget / (sl_ticks * tick_value))

        # Re-round prices to new tick size
        op = round_to_tick(op, tick_size)
        tp = round_to_tick(tp, tick_size)
        sl = round_to_tick(sl, tick_size)

    if quantity > 3:
        quantity = 3

    side = 0 if op < tp else 1
    size = abs(quantity)
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
    print(message)
    Alert(json.dumps(message))
    # return jsonify({
    #     "contract": contract_id,
    #     "side": side,
    #     "size": size,
    #     "balance": balance,
    #     "maximum_loss": maximum_loss,
    #     "risk_budget": risk_budget,
    #     "message": "OCO placed"
    # })
    entry = api_post(token, "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": entry_type,
        "side": side,
        "size": size,
        "limitPrice": op if entry_type == 1 else None,
        "stopPrice": op if entry_type == 4 else None
    })
    print(entry)
    entry_id = entry.get("orderId")
    if not entry.get("success") or not entry_id:
        return jsonify({"error": "Entry order failed"}), 500

    await asyncio.sleep(0.3)
    tp_order = api_post(token, "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": 1,
        "side": 1 - side,
        "size": size,
        "limitPrice": tp,
        "linkedOrderId": entry_id
    })
    await asyncio.sleep(0.3)
    sl_order = api_post(token, "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": 4,
        "side": 1 - side,
        "size": size,
        "stopPrice": sl,
        "linkedOrderId": entry_id
    })
    print(sl_order)

    oco_orders[entry_id] = [tp_order.get("orderId"), sl_order.get("orderId")]

    return jsonify({
        "entryOrderId": entry_id,
        "takeProfitOrderId": tp_order.get("orderId"),
        "stopLossOrderId": sl_order.get("orderId"),
        "contractId": contract_id,
        "tickSize": tick_size,
        "tickValue": tick_value,
        "balance": balance,
        "maximum_loss": maximum_loss,
        "risk_budget": risk_budget,
        "message": "OCO placed"
    })

@app.route("/")
async def index():
    priority = ["YM", "MYM", "NQ", "MNQ", "GC", "MGC", "ES", "MES"]
    all_symbols = list(contract_map.keys())

    # Put priority symbols first, then the rest (excluding duplicates)
    sorted_symbols = priority + [s for s in all_symbols if s not in priority]

    return await render_template("order_form.html", symbols=sorted_symbols)

@app.route("/place-oco", methods=["POST"])
async def place_oco():
    data = await request.get_json()
    return await place_oco_generic(data, entry_type=1)

@app.route("/place-oco-stop", methods=["POST"])
async def place_oco_stop():
    data = await request.get_json()
    return await place_oco_generic(data, entry_type=4)

@app.route("/balance", methods=["GET"])
async def balance():
    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    account_info = get_account_info(token)
    if not account_info:
        return jsonify({"error": "Failed to fetch account data"}), 500

    balance = account_info.get("balance")
    maximum_loss = account_info.get("maximumLoss")

    return jsonify({
        "balance": balance,
        "maximumLoss": maximum_loss
    })

@app.before_serving
async def startup():
    load_contracts()
    asyncio.create_task(monitor_oco_orders())
    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    account_info = get_account_info(token)
    if not account_info:
        return jsonify({"error": "Failed to fetch account data"}), 500

def run_server():
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    run_server()
