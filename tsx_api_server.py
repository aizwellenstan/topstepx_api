import requests
import yaml
from quart import Quart, request, jsonify

# --- Load config.yaml ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]
ACCOUNT_ID = int(config["account_id"])
CONTRACT_ID = "CON.F.US.MYM.U25"

app = Quart(__name__)

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
    except Exception:
        return None

# --- API Call ---
def api_post(token, endpoint, payload):
    res = requests.post(
        f"{API_URL}{endpoint}",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        verify=False
    )
    res.raise_for_status()
    return res.json()

# --- Limit Entry OCO ---
@app.route("/place-oco", methods=["POST"])
async def place_oco():
    data = await request.get_json()
    quantity = int(data.get("quantity", 1))
    op = data.get("op")  # limit entry price
    tp = data.get("tp")  # take profit
    sl = data.get("sl")  # stop loss

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
            "type": 1,  # Limit
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

        return jsonify({
            "entryOrderId": entry_id,
            "takeProfitOrderId": tp_order.get("orderId"),
            "stopLossOrderId": sl_order.get("orderId"),
            "message": "Limit OCO bracket placed"
        })
    except Exception:
        return jsonify({"error": "OCO placement failed"}), 500

# --- Stop-Market Entry OCO ---
@app.route("/place-oco-stop", methods=["POST"])
async def place_oco_stop():
    data = await request.get_json()
    quantity = int(data.get("quantity", 1))
    op = data.get("op")  # stop trigger price
    tp = data.get("tp")  # take profit
    sl = data.get("sl")  # stop loss

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
            "type": 4,  # Stop
            "side": side,
            "size": size,
            "stopPrice": op
        })
        entry_id = entry.get("orderId")
        if not entry.get("success") or not entry_id:
            return jsonify({"error": "Entry stop-market order failed"}), 500

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

        return jsonify({
            "entryOrderId": entry_id,
            "takeProfitOrderId": tp_order.get("orderId"),
            "stopLossOrderId": sl_order.get("orderId"),
            "message": "Stop-market OCO bracket placed"
        })
    except Exception:
        return jsonify({"error": "OCO stop-market placement failed"}), 500

# --- Run ---
if __name__ == "__main__":
    app.run(port=5000)
