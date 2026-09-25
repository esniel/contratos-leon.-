"""
Pruebas de humo para leon_api.py (v57) sin red ni API keys reales.
Stubea fastapi/requests lo mínimo necesario e inyecta datos falsos para
validar la aritmética del Paper Options Engine, el bloqueo de Risk Shield,
el calendario de feriados NYSE y el caché de quotes en vivo.

Uso: python3 test_paper_engine.py
"""
import os
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)

import sys, types, json, time as _time
from datetime import datetime, date

# --- Stub mínimo de fastapi para poder importar leon_api sin instalarlo ---
fastapi_stub = types.ModuleType("fastapi")

class _HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")

class _FastAPI:
    def __init__(self, *a, **kw): pass
    def add_middleware(self, *a, **kw): pass
    def get(self, *a, **kw):
        def deco(f): return f
        return deco
    def post(self, *a, **kw):
        def deco(f): return f
        return deco
    def api_route(self, *a, **kw):
        def deco(f): return f
        return deco
    def on_event(self, *a, **kw):
        def deco(f): return f
        return deco

class _Request:
    pass

fastapi_stub.FastAPI = _FastAPI
fastapi_stub.HTTPException = _HTTPException
fastapi_stub.Request = _Request
sys.modules["fastapi"] = fastapi_stub

cors_stub = types.ModuleType("fastapi.middleware.cors")
cors_stub.CORSMiddleware = object
mw_stub = types.ModuleType("fastapi.middleware")
mw_stub.cors = cors_stub
sys.modules["fastapi.middleware"] = mw_stub
sys.modules["fastapi.middleware.cors"] = cors_stub

resp_stub = types.ModuleType("fastapi.responses")
class _FileResponse:
    def __init__(self, *a, **kw): pass
class _JSONResponse:
    def __init__(self, *a, **kw): pass
resp_stub.FileResponse = _FileResponse
resp_stub.JSONResponse = _JSONResponse
sys.modules["fastapi.responses"] = resp_stub

import leon_api as L
import asyncio as _asyncio

FAILS = []
def check(name, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        FAILS.append(name)

class _FakeRequest:
    """Imita el Request de FastAPI para llamar los endpoints paper directamente."""
    def __init__(self, payload):
        self._payload = dict(payload)
    async def json(self):
        return dict(self._payload)

def _reset_paper(cash=10000.0):
    L.PAPER.update({"starting_cash": cash, "cash": cash, "realized": 0.0,
                    "positions": [], "trades": [],
                    "daily_loss_limit_pct": 3.0, "max_position_pct": 10.0})
    L.ROBOT.update({"state": "ESPERANDO", "symbol": None,
                    "reason": "reset de pruebas", "updated_at": None})

def _open(premium, qty=1, symbol="NVDA", side="CALL"):
    return _asyncio.run(L.paper_open(_FakeRequest(
        {"symbol": symbol, "side": side, "qty": qty, "premium": premium})))

def _close(position_id, premium):
    return _asyncio.run(L.paper_close(_FakeRequest(
        {"position_id": position_id, "premium": premium})))

# ---------------------------------------------------------------------
# 1) Paper Options Engine: abrir y cerrar con slippage + comisión
#    costo apertura = premium*100*qty*1.01 + 0.65*qty
#    proceeds cierre = premium*100*qty*0.99 - 0.65*qty
# ---------------------------------------------------------------------
_reset_paper()
acc0 = L.paper_account()
check("cuenta inicia con 10000 de cash", acc0["cash"] == 10000.0)

opened = _open(premium=3.40, qty=1)
pos = opened["position"]
expected_cost = round(3.40 * 100 * 1 * 1.01 + 0.65 * 1, 2)  # 344.05
check("entry_cost = premium*100*1.01 + comisión",
      pos["entry_cost"] == expected_cost, f"(cost={pos['entry_cost']} esperado={expected_cost})")
check("entry guarda el premium real (no inventado)", pos["entry_premium"] == 3.40)
check("cash bajó exactamente el entry_cost",
      round(L.PAPER["cash"], 2) == round(10000 - expected_cost, 2))
check("el robot pasa a ACTIVE al abrir", L.ROBOT["state"] == "ACTIVE")

closed = _close(pos["position_id"], premium=4.00)
expected_proceeds = round(4.00 * 100 * 1 * 0.99 - 0.65 * 1, 2)  # 395.35
expected_pnl = round(expected_proceeds - expected_cost, 2)      # 51.30
check("pnl = proceeds - entry_cost", closed["pnl"] == expected_pnl,
      f"(pnl={closed['pnl']} esperado={expected_pnl})")
check("posición ya no aparece en abiertas tras cerrar", len(L.PAPER["positions"]) == 0)
check("realized refleja el pnl", round(L.PAPER["realized"], 2) == expected_pnl)
check("el robot pasa a EXIT al cerrar", L.ROBOT["state"] == "EXIT")

# ---------------------------------------------------------------------
# 2) No se inventan precios: premium 0/ausente -> 409, posición fantasma -> 404
# ---------------------------------------------------------------------
_reset_paper()
try:
    _open(premium=0)
    check("abrir sin premium real debe fallar", False)
except L.HTTPException as e:
    check("abrir sin premium lanza 409 (no inventa precio)", e.status_code == 409)

o = _open(premium=3.40)
try:
    _close(o["position"]["position_id"], premium=0)
    check("cerrar sin precio real debe fallar", False)
except L.HTTPException as e:
    check("cerrar sin precio real lanza 409 (no inventa precio)", e.status_code == 409)
try:
    _close("P-INEXISTENTE", premium=4.00)
    check("cerrar posición inexistente debe fallar", False)
except L.HTTPException as e:
    check("cerrar posición inexistente lanza 404", e.status_code == 404)

# ---------------------------------------------------------------------
# 3) Risk Shield en apertura: tamaño de posición y cash
# ---------------------------------------------------------------------
_reset_paper(cash=3000.0)  # 1 contrato (~344) supera el 10% del equity (300)
try:
    _open(premium=3.40)
    check("posición sobredimensionada debe rechazarse", False)
except L.HTTPException as e:
    check("posición > 10% del equity lanza 400", e.status_code == 400, f"(detail={e.detail})")

_reset_paper(cash=100.0)
# equity alto vía posición existente, pero cash insuficiente para una nueva
L.PAPER["positions"].append({"position_id": "P-FIX", "symbol": "AAPL", "side": "CALL",
                              "qty": 10, "entry_premium": 3.40, "entry_cost": 3400.0,
                              "market_value": 3400.0, "opened_at": 0, "state": "ACTIVE",
                              "profit_lock_stage": 0})
try:
    _open(premium=3.40)
    check("abrir sin cash debe fallar", False)
except L.HTTPException as e:
    check("cash insuficiente lanza 400", e.status_code == 400 and "Cash" in str(e.detail))

# Límite de pérdida diaria: configurado (el enforcement automático aún no existe)
_reset_paper()
check("límite de pérdida diaria configurado en 3%",
      L.PAPER["daily_loss_limit_pct"] == 3.0)
print("      (nota: el bloqueo automático por pérdida diaria aún no está implementado)")

# ---------------------------------------------------------------------
# 4) Risk Shield: gates de datos, spread, sesión y consenso
# ---------------------------------------------------------------------
tight = {"price": 100, "bid": 99.9, "ask": 100.1, "market_time": L.now()}
r = L.risk_shield("BTC/USD", q=tight, score=60)
check("spread sano no bloquea", r["blocked"] is False and r["status"] == "GREEN")

wide = {"price": 100, "bid": 90, "ask": 110, "market_time": L.now()}
check("spread absurdo bloquea (RED/NO TRADE)",
      L.risk_shield("BTC/USD", q=wide, score=60)["blocked"] is True)

stale = {"price": 100, "bid": 99.9, "ask": 100.1, "market_time": L.now() - 500}
check("dato viejo bloquea", L.risk_shield("BTC/USD", q=stale, score=60)["blocked"] is True)

low = {"price": 100, "bid": 99.9, "ask": 100.1, "market_time": L.now()}
r2 = L.risk_shield("BTC/USD", q=low, score=40)
check("consenso bajo deja en WAIT/amarillo sin bloquear",
      r2["blocked"] is False and r2["decision"] == "WAIT")

# ---------------------------------------------------------------------
# 5) Calendario NYSE: feriado y mercado cerrado
# ---------------------------------------------------------------------
class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 12, 25, 10, 0, tzinfo=tz)  # Navidad 2026: viernes, feriado NYSE

L.datetime = _FixedDateTime
mc = L.market_clock()
check("25-dic-2026 se detecta como feriado (sesión CLOSED)", mc["session"] == "CLOSED")
check("mercado se marca cerrado en feriado", mc["open"] is False)

class _NormalDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 10, 10, 0, tzinfo=tz)  # jueves normal, 10:00 ET

L.datetime = _NormalDateTime
mc2 = L.market_clock()
check("día hábil normal no se marca cerrado", mc2["session"] == "REGULAR")
check("en horario y día normal el mercado está abierto", mc2["open"] is True)
L.datetime = datetime  # restaurar

# ---------------------------------------------------------------------
# 6) Caché de quotes en vivo, frescura y parseo de timestamps
# ---------------------------------------------------------------------
L.LIVE.clear(); L.CRYPTO_LIVE.clear()
L.LIVE["NVDA"] = {"symbol": "NVDA", "price": 187.5, "bid": 187.4, "ask": 187.6,
                  "market_time": _time.time() - 5, "received_at": _time.time(),
                  "provider": "Alpaca WS"}
check("quote() usa el caché LIVE cuando está fresco (<20s)",
      L.quote("NVDA")["provider"] == "Alpaca WS")
check("fresh() acepta dato de hace 5s", L.fresh(L.LIVE["NVDA"]) is True)

L.LIVE["TSLA"] = {"symbol": "TSLA", "price": 261.0, "bid": 260.9, "ask": 261.1,
                  "market_time": _time.time() - 120, "received_at": _time.time() - 120,
                  "provider": "Alpaca WS"}
check("fresh() descarta dato viejo (>20s)", L.fresh(L.LIVE["TSLA"]) is False)
try:
    L.quote("TSLA")  # sin keys -> Alpaca REST debe fallar con 503, no inventar dato
    check("quote() con dato viejo y sin keys debe fallar", False)
except L.HTTPException as e:
    check("quote() sin proveedor configurado lanza 503", e.status_code == 503)
L.LIVE.clear()

epoch = L.parse_ts("2026-09-10T14:23:01.123456789Z")
check("parse_ts parsea timestamps con nanosegundos de Alpaca", epoch is not None and epoch > 0)
check("parse_ts devuelve None con basura en vez de reventar", L.parse_ts("no-es-fecha") is None)

check("health() reporta la versión real 57.0", L.health()["version"] == "57.1")

# Sin API keys, el selector no inventa oportunidades: NO TRADE
ms = L.market_selector()
check("sin proveedores el market-selector queda en NO TRADE",
      ms["selected_market"] == "NO TRADE" and ms["best"] is None)
L.ROBOT["state"]="ACTIVE"  # simula loop con posición abierta
L.market_selector()
check("el selector no inventa estado del loop: no pisa ACTIVE", L.ROBOT["state"]=="ACTIVE")

# ---------------------------------------------------------------------
# 7) WebSocket loop: no revienta sin 'websockets' y parsea quotes reales
# ---------------------------------------------------------------------
_orig_ws = L.websockets
L.websockets = None
L.ALPACA_KEY = "x"
res = _asyncio.run(L.ws_stocks())
check("ws_stocks retorna sin reventar si falta 'websockets'", res is None)

class _FakeWSConn:
    def __init__(self, messages):
        self._messages = messages
    async def send(self, msg): pass
    async def recv(self): return json.dumps({"T": "success", "msg": "authenticated"})
    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
            raise ConnectionResetError("fake: conexión simulada cerrada tras el mensaje")
        return gen()
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class _FakeWSModule:
    @staticmethod
    def connect(url, **kw):
        return _FakeWSConn([json.dumps([{"T": "q", "S": "NVDA", "bp": 187.40, "ap": 187.60,
                                          "t": "2026-09-10T14:00:00.000000000Z"}])])

L.ALPACA_KEY, L.ALPACA_SECRET = "fake", "fake"
L.websockets = _FakeWSModule
try:
    _asyncio.run(_asyncio.wait_for(L.ws_stocks(), timeout=1.0))
except _asyncio.TimeoutError:
    pass  # esperado: el loop reconecta para siempre, lo cortamos tras el mensaje

live_nvda = L.LIVE.get("NVDA")
check("el loop parsea un quote real y llena el caché", live_nvda is not None)
check("el precio se calcula como mid de bid/ask",
      live_nvda is not None and abs(live_nvda["price"] - 187.5) < 0.001)
check("el market_time viene del timestamp del mensaje",
      live_nvda is not None and live_nvda["market_time"] is not None)
L.websockets = _orig_ws
L.ALPACA_KEY, L.ALPACA_SECRET = "", ""
L.LIVE.clear()

# ---------------------------------------------------------------------
print(f"\n{len(FAILS)} fallo(s)" if FAILS else "\nTodas las pruebas pasaron.")
sys.exit(1 if FAILS else 0)
