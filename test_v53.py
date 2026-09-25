import os, re
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

h = (m.BASE / "index.html").read_text(encoding="utf-8")
assert m.VERSION == "57.2", f"VERSION={m.VERSION}"

# Vistas principales del frontend actual
for x in ["tradingView", "scannerPanel", "portfolioView", "aiPanel"]:
    assert f'id="{x}"' in h, f"falta vista {x}"

# REGLA ABSOLUTA: ningún botón decorativo — todo <button> debe tener onclick
buttons = re.findall(r"<button[^>]*>", h)
assert buttons, "no se encontró ningún botón en el HTML"
sin_accion = [b for b in buttons if "onclick" not in b]
assert not sin_accion, f"botones decorativos (sin onclick): {sin_accion}"

# Funciones clave cableadas en la UI
for x in ["scanNow(", "loadOptions(", "loadPaper(", "refreshAnalysis(", "toggleRobot("]:
    assert x in h, f"falta cablear {x}"

# Seguridad visible
assert "Live Trading" in h and "BLOQUEADO" in h
assert "Paper Trading" in h and "Live bloqueado" in h

# El score nunca se presenta como probabilidad de ganar
assert "no es probabilidad de ganar" in h or "no probabilidad de ganar" in h, \
    "el quality score debe aclarar que no es probabilidad de ganar"

print("LEONIX v57: botones/navegación + seguridad OK")
