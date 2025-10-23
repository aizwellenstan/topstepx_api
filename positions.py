import requests
import yaml
import logging
import urllib3

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Load config ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)

API_URL = "https://api.topstepx.com"
USERNAME = config["username"]
API_KEY = config["api_key"]

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Token cache ---
TOKEN = None

def get_token(force_refresh=False):
    """
    Return a valid bearer token. Refresh if needed.
    """
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
    """
    Return list of active accounts.
    """
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
    """
    Return open positions for a given account ID.
    """
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
    except requests.exceptions.JSONDecodeError:
        logging.error(f"Invalid JSON response for account {account_id}: {res.text}")
        return []
    except Exception as e:
        logging.error(f"Position fetch error for account {account_id}: {e}")
        return []

# --- Main execution ---
def main():
    token = get_token()
    if not token:
        logging.error("Authentication failed. Exiting.")
        return

    accounts = get_active_accounts(token)
    if not accounts:
        logging.info("No active accounts found.")
        return

    logging.info(f"Found {len(accounts)} active accounts.")
    for account in accounts:
        account_id = account["id"]
        logging.info(f"\nFetching positions for Account ID: {account_id}")
        positions = get_positions(token, account_id)
        if positions:
            for pos in positions:
                logging.info(f"  Position ID: {pos['id']}, Contract: {pos['contractId']}, Size: {pos['size']}, Avg Price: {pos['averagePrice']}")
        else:
            logging.info("  No open positions.")

if __name__ == "__main__":
    main()
