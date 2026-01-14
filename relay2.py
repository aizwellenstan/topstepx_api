# main_server.py
from trading_server import TradingServer

server = TradingServer(config_path="config_combine.yaml", relay_urls=[])
app = server.app   # expose FastAPI app