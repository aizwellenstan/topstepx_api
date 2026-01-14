import asyncio
import logging
import math
import datetime
import yaml
import httpx
import urllib3
from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, List
from contextlib import asynccontextmanager
import json

from modules.discord import Alert

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

templates = Jinja2Templates(directory="templates")

class OCORequest(BaseModel):
    symbol: str
    op: float
    tp: float
    sl: float
    quantity: Optional[int] = 1
    customTag: Optional[str] = None

class TradingServer:
    def __init__(self, config_path: str, relay_urls: Optional[List[str]] = None):
        with open(config_path) as f:
            config = yaml.safe_load(f)

        self.api_url = "https://api.topstepx.com"
        self.username = config["username"]
        self.api_key = config["api_key"]
        self.account_id = int(config["account_id"])
        self.risk_pct = float(config["risk_pct"])
        self.relay_urls = relay_urls or []

        self.token = None
        self.contract_map: Dict[str, dict] = {}
        self.oco_orders: Dict[int, List[Optional[int]]] = {}

        self.client = httpx.AsyncClient(verify=False, timeout=10)
        self.app = FastAPI(lifespan=self.lifespan)
        self._register_routes()

    # --- Helpers ---
    @staticmethod
    def get_precision(tick_size):
        return max(0, -int(math.floor(math.log10(tick_size)))) if tick_size > 0 else 0

    @classmethod
    def round_to_tick(cls, value, tick_size):
        precision = cls.get_precision(tick_size) + 1
        ticks = math.floor(value / tick_size)
        return round(ticks * tick_size, precision)

    async def api_call(self, method: str, endpoint: str, payload: dict = None, token: str = None):
        async def _do_request(token_value):
            headers = {"Content-Type": "application/json"}
            if token_value:
                headers["Authorization"] = f"Bearer {token_value}"
            url = f"{self.api_url}{endpoint}" if endpoint.startswith("/api") else endpoint
            if method.upper() == "POST":
                return await self.client.post(url, json=payload, headers=headers)
            else:
                headers.update({"x-app-type": "px-desktop", "x-app-version": "1.21.1"})
                return await self.client.get(url, headers=headers)

        try:
            # First attempt
            res = await _do_request(token)
            if res.status_code == 401:  # token invalid/expired
                self.token = None
                new_token = await self.get_token()
                if new_token:
                    res = await _do_request(new_token)
            return res.json()
        except Exception as e:
            logging.error(f"API Error: {e}")
            return {}


    async def get_token(self, refresh = False):
        # If we already have a token, just return it
        if self.token and not refresh:
            return self.token

        # Otherwise, request a new one
        res = await self.api_call("POST", "/api/Auth/loginKey", {
            "userName": self.username,
            "apiKey": self.api_key
        })

        if res.get("success"):
            self.token = res.get("token")
            return self.token

        # If login fails, clear token so next call retries
        self.token = None
        return None

    async def get_account_info(self):
        token = await self.get_token(refresh=True)
        if not token:
            return None

        acc_url = "https://userapi.topstepx.com/TradingAccount"
        accs = await self.api_call("GET", acc_url, token=token)
        if isinstance(accs, list):
            return token, next((a for a in accs if a.get("accountId") == self.account_id), None)
        return None, None


    async def monitor_entry_and_place_tp(self, entry_id, contract_id, side, size, tp):
        """
        Handles the lifecycle of the order set:
        1. If Entry is CANCELED manually -> Cancel the SL and stop.
        2. If Entry is FILLED -> Place the TP and stop (SL stays alive).
        """
        while True:
            await asyncio.sleep(3)
            token = await self.get_token()
            if not token: continue

            # Check Order History for Status
            now = datetime.datetime.now(datetime.timezone.utc)
            start = (now - datetime.timedelta(minutes=5)).isoformat()
            res = await self.api_call("POST", "/api/Order/search", {"accountId": self.account_id, "startTimestamp": start}, token=token)
            
            order_data = next((o for o in res.get("orders", []) if o.get("id") == entry_id), None)
            if order_data is None: continue
            oco_order = self.oco_orders[entry_id]
            sl_id = oco_order[1]
            if order_data.get("filledPrice") is not None:
                tp_id = oco_order[0]
                if tp_id is None:
                    # Place TP
                    await asyncio.sleep(0.3)
                    tp_order = await self.api_call("POST", "/api/Order/place", {
                        "accountId": self.account_id, "contractId": contract_id,
                        "type": 1, "side": 1 - side, "size": size,
                        "limitPrice": tp, "linkedOrderId": entry_id
                    }, token=token)
                    self.oco_orders[entry_id][0] = tp_order.get("orderId")
                    logging.info(f"Entry {entry_id} filled. Placed TP {tp_id}")
                else:
                    tp_order_data = next((o for o in res.get("orders", []) if o.get("id") == tp_id), None)
                    sl_order_data = next((o for o in res.get("orders", []) if o.get("id") == sl_id), None)
                    
                    if tp_order_data.get("filledPrice") is not None:
                        await asyncio.sleep(0.3)
                        await self.api_call("POST", "/api/Order/cancel", {
                            "accountId": self.account_id, "orderId": sl_id
                        }, token=token)
                        logging.info(f"TP {tp_id} FILLED CANCEL SL {sl_id}")
                        self.oco_orders.pop(entry_id, None)
                        return
                    elif sl_order_data.get("filledPrice") is not None:
                        await asyncio.sleep(0.3)
                        await self.api_call("POST", "/api/Order/cancel", {
                            "accountId": self.account_id, "orderId": tp_id
                        }, token=token)
                        logging.info(f"SL {sl_id} FILLED CANCEL TP {tp_id}")
                        self.oco_orders.pop(entry_id, None)
                        return

                    status = order_data.get("status")
                    logging.info(f"Entry {entry_id} SL {sl_id} ENTRY_STATUS {status}")
       
            else:
                # --- CASE B: Entry Canceled/Rejected ---
                status = order_data.get("status")
                if status in [2, 3]: # 2=Canceled, 3=Rejected
                    logging.info(f"Entry {entry_id} was manually canceled or rejected. Killing SL {sl_id}")
                    await asyncio.sleep(0.3)
                    await self.api_call("POST", "/api/Order/cancel", {
                        "accountId": self.account_id, "orderId": sl_id
                    }, token=token)
                    logging.info(f"CANCEL SL {sl_id}")
                    return

    # --- Async Contract Loader ---
    async def load_contracts(self, token: str):
        try:
            res = await self.client.get(
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

                    self.contract_map[short_symbol] = {
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

            logging.info(f"Loaded {len(self.contract_map)} contracts")
        except Exception as e:
            logging.error(f"UserContract load error: {e}")

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        token = await self.get_token()
        if token:
            await self.load_contracts(token)
        yield
        await self.client.aclose()

    def _register_routes(self):
        @self.app.get("/")
        async def index(request: Request):
            priority = ["YM", "MYM", "NQ", "MNQ", "GC", "MGC", "ES", "MES"]
            sorted_symbols = priority + [s for s in self.contract_map.keys() if s not in priority]
            return templates.TemplateResponse("order_form.html", {"request": request, "symbols": sorted_symbols})

        @self.app.post("/place-oco")
        async def place_oco(data: OCORequest):
            # relay if configured
            async def forward_request(relay_urls, payload):
                async with httpx.AsyncClient() as client:
                    tasks = []
                    for url in relay_urls:
                        tasks.append(post_with_logging(client, url, payload))
                    await asyncio.gather(*tasks)

            async def post_with_logging(client, url, payload):
                try:
                    await client.post(url, json=payload)
                except Exception as e:
                    logging.error(f"Relay failed to {url}: {e}")

            # Usage
            if self.relay_urls:
                asyncio.create_task(forward_request(self.relay_urls, data.dict()))

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
            token, account_info = await self.get_account_info()
            if not token or not account_info:
                raise HTTPException(500, "Authentication failed")

            balance = account_info.get("balance")
            maximum_loss = account_info.get("maximumLoss")
            if balance is None or maximum_loss is None:
                raise HTTPException(500, "Missing account data")

            # --- Risk sizing ---
            micro_to_standard = {
                "MNQ": "NQ", "MYM": "YM", "MGC": "GC", "MES": "ES",
                "SIL": "SI", "MHG": "HG", "M6E": "6E"
            }
            standard_to_micro = {v: k for k, v in micro_to_standard.items()}
            micro_symbol = standard_to_micro.get(symbol, symbol)
            micro_contract = self.contract_map.get(micro_symbol)
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

            risk_budget = (balance - maximum_loss) * self.risk_pct
            if self.risk_pct > 0.049 and custom_tag == "AllTimeLongES":
                risk_budget = (balance - maximum_loss) - 64

            dynamic_contracts = math.floor(risk_budget / (sl_ticks * tick_value))
            quantity = max(dynamic_contracts, 1)

            # Upgrade to standard contract if sizing is large
            if quantity >= 10 and micro_symbol in micro_to_standard:
                symbol = micro_to_standard[micro_symbol]
                contract = self.contract_map.get(symbol)
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
            entry = await self.api_call("POST", "/api/Order/place", {
                "accountId": self.account_id,
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
            sl_order = await self.api_call("POST", "/api/Order/place", {
                "accountId": self.account_id,
                "contractId": contract_id,
                "type": 4,
                "side": 1 - side,
                "size": size,
                "stopPrice": sl,
                "linkedOrderId": entry_id
            }, token=token)

            self.oco_orders[entry_id] = [None, sl_order.get("orderId")]
            # --- Lifecycle monitor for TP ---
            asyncio.create_task(self.monitor_entry_and_place_tp(
                entry_id=entry_id,
                contract_id=contract_id,
                side=side,
                size=size,
                tp=tp
            ))

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
    server = TradingServer(config_path="config.yaml", relay_urls=["http://localhost:5001/place-oco"])
    uvicorn.run(server.app, host="0.0.0.0", port=5000)