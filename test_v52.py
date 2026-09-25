import os
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)
import leon_api as m

assert m.VERSION == "57.1", f"VERSION={m.VERSION}"
assert "BTC/USD" in m.CRYPTO
assert m.market_clock()["session"] in ("REGULAR", "CLOSED")

tight = {"price": 100, "bid": 99.9, "ask": 100.1, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=tight, score=60)["blocked"] is False

wide = {"price": 100, "bid": 90, "ask": 110, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=wide, score=60)["blocked"] is True

assert m.PAPER["max_position_pct"] == 10.0
assert m.PAPER["daily_loss_limit_pct"] == 3.0
assert m.ROBOT["mode"] == "PAPER"
assert (m.BASE / "index.html").exists()

print("LEONIX v57: 9 smoke checks OK")
