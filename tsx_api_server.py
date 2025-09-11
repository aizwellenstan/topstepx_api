import requests
import yaml
import asyncio
import logging
import urllib3
from datetime import datetime
from quart import Quart, request, jsonify

# --- Suppress SSL warnings ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Load config.yaml ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]
ACCOUNT_ID = int(config["account_id"])
CONTRACT_ID = "CON.F.US.MYM.U25"

app = Quart(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- In-memory OCO tracking ---
oco_orders = {}  # entry_id: [tp_id, sl_id]

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

# --- API DELETE for cancellation ---
def cancel_order(token, account_id, order_id):
    try:
        res = requests.post(
            f"https://api.topstepx.com/api/Order/cancel",
            json={"accountId": account_id, "orderId": order_id},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            verify=False
        )
        res.raise_for_status()
        data = res.json()
        if data.get("success"):
            return True
        logging.error(f"Cancel failed: {data.get('errorMessage')}")
        return False
    except Exception as e:
        logging.error(f"Cancel failed for {order_id}: {e}")
        return False

# --- Monitor OCO Orders ---
async def monitor_oco_orders():
    while True:
        token = get_token()
        if not token:
            logging.warning("Monitoring skipped: auth failed.")
            await asyncio.sleep(1)
            continue

        try:
            response = api_post(token, "/api/Order/searchOpen", {
                "accountId": ACCOUNT_ID
            })
            orders = response.get("orders", [])
        except Exception as e:
            logging.error(f"Order searchOpen failed: {e}")
            await asyncio.sleep(1)
            continue

        active_ids = {o["id"] for o in orders if "id" in o}

        for entry_id, linked_ids in list(oco_orders.items()):
            all_ids = [entry_id] + linked_ids
            missing = [oid for oid in all_ids if oid not in active_ids]

            logging.info(f"OCO Group: Entry={entry_id}, Linked={linked_ids}")
            for oid in all_ids:
                status = "Active" if oid in active_ids else "Not Found"
                logging.info(f"Order {oid} → Status: {status}")

            if missing:
                logging.warning(f"OCO cancel triggered: missing = {missing}")
                for oid in all_ids:
                    if oid in active_ids:
                        if cancel_order(token, ACCOUNT_ID, oid):
                            logging.info(f"Canceled order {oid}")
                del oco_orders[entry_id]

        await asyncio.sleep(1)

# --- Place Limit OCO ---
@app.route("/place-oco", methods=["POST"])
async def place_oco():
    data = await request.get_json()
    quantity = int(data.get("quantity", 1))
    op = data.get("op")
    tp = data.get("tp")
    sl = data.get("sl")

    if not op or not tp or not sl:
        return jsonify({"error": "Missing op, tp, or sl"}), 400

    side = 0 if quantity > 0 else 1
    size = abs(quantity)
    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    try:
        entry = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 1,
            "side": side,
            "size": size,
            "limitPrice": op
        })
        entry_id = entry.get("orderId")
        if not entry.get("success") or not entry_id:
            return jsonify({"error": "Entry order failed"}), 500

        tp_order = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 1,
            "side": 1 - side,
            "size": size,
            "limitPrice": tp,
            "linkedOrderId": entry_id
        })

        sl_order = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 4,
            "side": 1 - side,
            "size": size,
            "stopPrice": sl,
            "linkedOrderId": entry_id
        })

        oco_orders[entry_id] = [tp_order.get("orderId"), sl_order.get("orderId")]

        logging.info(f"Placed entry order: {entry_id}")
        logging.info(f"Placed TP order: {tp_order.get('orderId')}")
        logging.info(f"Placed SL order: {sl_order.get('orderId')}")

        return jsonify({
            "entryOrderId": entry_id,
            "takeProfitOrderId": tp_order.get("orderId"),
            "stopLossOrderId": sl_order.get("orderId"),
            "message": "Limit OCO placed"
        })
    except Exception:
        return jsonify({"error": "OCO placement failed"}), 500

# --- Place Stop-Market OCO ---
@app.route("/place-oco-stop", methods=["POST"])
async def place_oco_stop():
    data = await request.get_json()
    quantity = int(data.get("quantity", 1))
    op = data.get("op")
    tp = data.get("tp")
    sl = data.get("sl")

    if not op or not tp or not sl:
        return jsonify({"error": "Missing op, tp, or sl"}), 400

    side = 0 if quantity > 0 else 1
    size = abs(quantity)
    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    try:
        entry = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 4,
            "side": side,
            "size": size,
            "stopPrice": op
        })
        entry_id = entry.get("orderId")
        if not entry.get("success") or not entry_id:
            return jsonify({"error": "Entry stop order failed"}), 500

        tp_order = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 1,
            "side": 1 - side,
            "size": size,
            "limitPrice": tp,
            "linkedOrderId": entry_id
        })

        sl_order = api_post(token, "/api/Order/place", {
            "accountId": ACCOUNT_ID,
            "contractId": CONTRACT_ID,
            "type": 4,
            "side": 1 - side,
            "size": size,
            "stopPrice": sl,
            "linkedOrderId": entry_id
        })

        oco_orders[entry_id] = [tp_order.get("orderId"), sl_order.get("orderId")]

        logging.info(f"Placed entry order: {entry_id}")
        logging.info(f"Placed TP order: {tp_order.get('orderId')}")
        logging.info(f"Placed SL order: {sl_order.get('orderId')}")

        return jsonify({
            "entryOrderId": entry_id,
            "takeProfitOrderId": tp_order.get("orderId"),
            "stopLossOrderId": sl_order.get("orderId"),
            "message": "Stop-market OCO placed"
        })
    except Exception:
        return jsonify({"error": "OCO stop placement failed"}), 500

# --- Startup Hook ---
@app.before_serving
async def startup():
    asyncio.create_task(monitor_oco_orders())

# --- Run ---
if __name__ == "__main__":
    app.run(port=5000)
