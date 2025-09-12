import requests
import yaml
import asyncio
import logging
import signal
import urllib3
from quart import Quart, request, jsonify

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
def get_token():
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
        return data.get("token") if data.get("success") else None
    except Exception as e:
        logging.error(f"Auth error: {e}")
        return None

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

# --- Place OCO ---
async def place_oco_generic(data, entry_type):
    quantity = int(data.get("quantity", 1))
    op = data.get("op")
    tp = data.get("tp")
    sl = data.get("sl")
    symbol = data.get("symbol", "").upper()
    custom_tag = data.get("customTag")
    contract = contract_map.get(symbol)
    if not contract:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 400

    contract_id = contract["contractId"]
    side = 0 if quantity > 0 else 1
    size = abs(quantity)
    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    entry = api_post(token, "/api/Order/place", {
        "accountId": ACCOUNT_ID,
        "contractId": contract_id,
        "type": entry_type,
        "side": side,
        "size": size,
        "limitPrice": op if entry_type == 1 else None,
        "stopPrice": op if entry_type == 4 else None
    })
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
        "tickSize": contract["tickSize"],
        "tickValue": contract["tickValue"],
        "message": "OCO placed"
    })

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
            return jsonify({"error": "No account found"}), 404

        balance = accounts[0].get("balance")
        if balance is None:
            return jsonify({"error": "Balance not available"}), 404

        return jsonify({"balance": balance})

    except Exception as e:
        logging.error(f"Balance fetch error: {e}")
        return jsonify({"error": "Failed to fetch balance"}), 500

@app.before_serving
async def startup():
    load_contracts()
    asyncio.create_task(monitor_oco_orders())

def run_server():
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    app.run(port=5000)

if __name__ == "__main__":
    run_server()
