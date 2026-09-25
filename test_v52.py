import os
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)
import sys, types
# Stub mínimo de fastapi (igual que test_robot_loop): las pruebas no necesitan red ni servidor real.
_fastapi = types.ModuleType("fastapi")
class _FastAPI:
    def __init__(self, *a, **k): pass
    def add_middleware(self, *a, **kw): pass
    def on_event(self, *a, **k):
        def deco(fn): return fn
        return deco
    def get(self, *a, **k):
        def deco(fn): return fn
        return deco
    def post(self, *a, **k):
        def deco(fn): return fn
        return deco
    def api_route(self, *a, **k):
        def deco(fn): return fn
        return deco
_fastapi.FastAPI = _FastAPI
class _HTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        super().__init__(detail); self.status_code = status_code; self.detail = detail
_fastapi.HTTPException = _HTTPException
class _Request: pass
_fastapi.Request = _Request
sys.modules["fastapi"] = _fastapi
_resp = types.ModuleType("fastapi.responses")
_resp.FileResponse = object; _resp.JSONResponse = object
sys.modules["fastapi.responses"] = _resp
_cors = types.ModuleType("fastapi.middleware.cors")
_cors.CORSMiddleware = object
_mw = types.ModuleType("fastapi.middleware")
_mw.cors = _cors
sys.modules["fastapi.middleware"] = _mw
sys.modules["fastapi.middleware.cors"] = _cors

import leon_api as m

assert m.VERSION == "57.2", f"VERSION={m.VERSION}"
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
