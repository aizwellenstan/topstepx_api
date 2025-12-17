import requests
import yaml
import logging
import urllib3
import time
from datetime import datetime, timedelta, timezone

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Load config ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
GATEWAY_URL = "https://gateway-api-demo.s2f.projectx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Token cache ---
TOKEN = None

def get_token(force_refresh=False):
    global TOKEN
    if TOKEN and not force_refresh:
        return TOKEN
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
        token = data.get("token") if data.get("success") else None
        if token:
            TOKEN = token
            return TOKEN
        else:
            logging.error("Token request failed: no token returned.")
            return None
    except Exception as e:
        logging.error(f"Auth error: {e}")
        return None

def get_active_accounts(token):
    try:
        res = requests.post(
            f"{API_URL}/api/Account/search",
            json={"onlyActiveAccounts": True},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
            verify=False
        )
        res.raise_for_status()
        return res.json().get("accounts", [])
    except Exception as e:
        logging.error(f"Account fetch error: {e}")
        return []

def get_positions(token, account_id):
    try:
        res = requests.post(
            f"{API_URL}/api/Position/searchOpen",
            json={"accountId": account_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=10,
            verify=False
        )
        res.raise_for_status()
        return res.json().get("positions", [])
    except Exception as e:
        logging.error(f"Position fetch error for account {account_id}: {e}")
        return []

# --- Commented Close Logic ---
def close_contract_position(token, account_id, contract_id):
    try:
        payload = {
            "accountId": account_id,
            "contractId": contract_id
        }
        res = requests.post(
            f"{API_URL}/api/Position/closeContract",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=10,
            verify=False
        )
        res.raise_for_status()
        data = res.json()
        if data.get("success"):
            logging.info(f"✅ Closed contract {contract_id} for account {account_id}")
            return True
        else:
            logging.warning(f"❌ Close failed for {contract_id}: {data}")
            return False
    except Exception as e:
        logging.error(f"Error closing contract {contract_id}: {e}")
        return False

def search_trades(token, account_id, start_time_iso, end_time_iso=None):
    try:
        payload = {
            "accountId": account_id,
            "startTimestamp": start_time_iso
        }
        if end_time_iso:
            payload["endTimestamp"] = end_time_iso

        res = requests.post(
            f"{API_URL}/api/Trade/search",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=15,
            verify=False
        )
        res.raise_for_status()
        return res.json().get("trades", [])
    except Exception as e:
        logging.error(f"Trade search error for account {account_id}: {e}")
        return []

def search_orders(token, account_id, start_time_iso, end_time_iso=None):
    try:
        payload = {
            "accountId": account_id,
            "startTimestamp": start_time_iso
        }
        if end_time_iso:
            payload["endTimestamp"] = end_time_iso

        res = requests.post(
            f"{API_URL}/api/Order/search",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=15,
            verify=False
        )
        res.raise_for_status()
        return res.json().get("orders", [])
    except Exception as e:
        logging.error(f"Order search error for account {account_id}: {e}")
        return []

# --- Main execution ---
def main():
    token = get_token()
    if not token:
        logging.error("Authentication failed. Exiting.")
        return

    accounts = get_active_accounts(token)
    time.sleep(0.3)

    if not accounts:
        logging.info("No active accounts found.")
        return

    logging.info(f"Found {len(accounts)} active accounts.")
    for account in accounts:
        account_id = account["id"]
        logging.info(f"\nFetching positions for Account ID: {account_id}")
        positions = get_positions(token, account_id)
        time.sleep(0.3)

        if positions:
            for pos in positions:
                contract_id = pos["contractId"]
                logging.info(f"  Position ID: {pos['id']}, Contract: {contract_id}, Size: {pos['size']}, Avg Price: {pos['averagePrice']}")

                # --- Commented Close Logic ---
                success = close_contract_position(token, account_id, contract_id)
                time.sleep(0.3)

                # # --- Trade Search ---
                # now = datetime.now(timezone.utc)
                # start_time = (now - timedelta(hours=24)).isoformat()
                # trades = search_trades(token, account_id, start_time)
                # time.sleep(0.3)

                # for trade in trades:
                #     logging.info(f"    Trade ID: {trade.get('id')}, Contract: {trade.get('contractId')}, "
                #                  f"Side: {trade.get('side')}, Size: {trade.get('size')}, Price: {trade.get('price')}")

                # --- Order Search ---
                # orders = search_orders(token, account_id, start_time)
                # time.sleep(0.3)

                # for order in orders:
                #     logging.info(f"    Order ID: {order.get('id')}, Contract: {order.get('contractId')}, "
                #                  f"Side: {order.get('side')}, Size: {order.get('size')}, "
                #                  f"Type: {order.get('type')}, Status: {order.get('status')}")
        else:
            logging.info("  No open positions.")

if __name__ == "__main__":
    main()
