import requests

BASE_URL = "http://127.0.0.1:5000"
symbol = "YM"

limit_payload = {
    "quantity": 1,
    "op": 45500.0,
    "tp": 46200.0,
    "sl": 45300.0,
    "symbol": symbol,
    "customTag": "TEST"
}

stop_payload = {
    "quantity": 1,
    "op": 15650.0,
    "tp": 15800.0,
    "sl": 15400.0,
    "symbol": symbol,
    "customTag": "TEST"
}

def send_request(endpoint, payload, label):
    try:
        res = requests.post(f"{BASE_URL}{endpoint}", json=payload)
        print(f"\n--- {label} ---")
        print("Status Code:", res.status_code)
        print("Response:", res.json())
    except Exception as e:
        print(f"{label} failed:", e)

def get_balance():
    try:
        res = requests.get(f"{BASE_URL}/balance")
        print("\n--- Account Balance ---")
        print("Status Code:", res.status_code)
        data = res.json()
        print("Balance:", data.get("balance"))
    except Exception as e:
        print("Balance fetch failed:", e)
send_request("/place-oco", limit_payload, "Limit Entry OCO")
# send_request("/place-oco-stop", stop_payload, "Stop-Market Entry OCO")
# get_balance()