import os, sys, importlib.util
os.environ.pop("ALPACA_KEY_ID",None); os.environ.pop("ALPACA_SECRET_KEY",None)
import leon_api as m
assert m.VERSION=="52.0"
assert "BTC/USD" in m.CRYPTO
assert m.market_clock()["session"] in ("REGULAR","CLOSED")
assert m.risk_shield("BTC/USD",q={"price":100,"bid":99.9,"ask":100.1,"market_time":m.now()},score=60)["blocked"] is False
assert m.risk_shield("BTC/USD",q={"price":100,"bid":90,"ask":110,"market_time":m.now()},score=60)["blocked"] is True
assert m.PAPER["max_position_pct"]==10.0
assert m.ROBOT["mode"]=="PAPER"
assert (m.BASE/"index.html").exists()
print("LEONIX v52: 8 smoke checks OK")
