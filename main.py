# main_server.py
from trading_server import TradingServer

server = TradingServer(config_path="config.yaml", relay_urls=["http://localhost:5001/place-oco", "http://localhost:5002/place-oco"])
app = server.app   # expose FastAPI app