import requests
import yaml

# Load credentials from config.yaml
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

username = config["username"]
api_key = config["api_key"]

# Authenticate
auth_response = requests.post(
    "https://api.topstepx.com/api/Auth/loginKey",
    json={"userName": username, "apiKey": api_key},
    headers={"Content-Type": "application/json"},
    verify=False
).json()

token = auth_response.get("token")
if not token:
    print("Authentication failed.")
    exit()

# Search for active accounts
account_response = requests.post(
    "https://api.topstepx.com/api/Account/search",
    json={"onlyActiveAccounts": True},
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    verify=False
).json()

accounts = account_response.get("accounts", [])
if accounts:
    print(accounts)
    print(f"First active account ID: {accounts[0]['id']}")
else:
    print("No active accounts found.")
