import requests

# --- Config ---
BASE_URL = "http://localhost:5000"

# --- Shared parameters ---
quantity = 1           # Use -1 for short
tp = 46300.0           # Take profit
sl = 45400.0           # Stop loss

# --- Limit Entry OCO ---
limit_payload = {
    "quantity": quantity,
    "op": 39500.0,      # Limit entry price
    "tp": tp,
    "sl": sl
}

# --- Stop-Market Entry OCO ---
stop_payload = {
    "quantity": quantity,
    "op": 45700.0,      # Stop trigger price
    "tp": tp,
    "sl": sl
}

def send_request(endpoint, payload, label):
    try:
        res = requests.post(f"{BASE_URL}{endpoint}", json=payload)
        print(f"\n--- {label} ---")
        print("Status Code:", res.status_code)
        print("Response:", res.json())
    except Exception as e:
        print(f"{label} failed:", e)

# --- Run both ---
# send_request("/place-oco", limit_payload, "Limit Entry OCO")
send_request("/place-oco-stop", stop_payload, "Stop-Market Entry OCO")
