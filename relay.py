# main_server.py
from trading_server import TradingServer

server = TradingServer(config_path="config_express.yaml", relay_urls=[])
app = server.app   # expose FastAPI app