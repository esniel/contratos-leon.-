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

# Risk Shield: spread sano no bloquea, spread absurdo sí bloquea
q = {"price": 76455, "bid": 76450, "ask": 76460, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=q, score=60)["blocked"] is False
bad = {"price": 76455, "bid": 70000, "ask": 80000, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=bad, score=60)["blocked"] is True

assert "BTC/USD" in m.CRYPTO and "NVDA" in m.M7
assert m.ROBOT["mode"] == "PAPER"
assert m.market_clock()["session"] in ("REGULAR", "CLOSED")

# Seguridad/sincronía en el frontend actual (v57)
h = (m.BASE / "index.html").read_text(encoding="utf-8")
assert "let symbol=null" in h, "el frontend no debe fijar un símbolo por defecto (ej. NVDA)"
assert "/api/market-selector" in h, "el modo AUTO debe escanear vía market-selector"
assert "sync_ok" in h and "DESYNC" in h, "la UI debe distinguir dato sincronizado de desincronizado"
assert "Live Trading" in h and "BLOQUEADO" in h, "live trading debe aparecer bloqueado en la UI"

r = (m.BASE / "README.md").read_text(encoding="utf-8")
assert "Live Trading desactivado" in r

print("LEONIX v57: safety/sync smoke tests OK")
