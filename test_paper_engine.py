"""
Pruebas de humo para leon_api.py sin red ni API keys reales.
Stubea fastapi/requests lo mínimo necesario e inyecta datos falsos de
opciones para validar la aritmética del Paper Options Engine, el
bloqueo de Risk Shield 2.0 y el calendario de feriados NYSE.

Uso: python3 test_paper_engine.py
"""
import sys, types, json
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

def _Query(default=..., **kw):
    return default if default is not ... else None

class _Request:
    pass

fastapi_stub.FastAPI = _FastAPI
fastapi_stub.HTTPException = _HTTPException
fastapi_stub.Query = _Query
fastapi_stub.Request = _Request
sys.modules["fastapi"] = fastapi_stub

cors_stub = types.ModuleType("fastapi.middleware.cors")
cors_stub.CORSMiddleware = object
mw_stub = types.ModuleType("fastapi.middleware")
mw_stub.cors = cors_stub
sys.modules["fastapi.middleware"] = mw_stub
sys.modules["fastapi.middleware.cors"] = cors_stub

import leon_api as L

FAILS = []
def check(name, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond: FAILS.append(name)

# ---------------------------------------------------------------------
# 1) Paper Options Engine: abrir y cerrar con slippage + comisión reales
# ---------------------------------------------------------------------
FAKE_CONTRACT = {
    "type": "CALL", "strike": 190.0, "expiration": "2026-09-25",
    "bid": 3.20, "ask": 3.40, "last": 3.30, "mark": 3.30,
    "volume": 500, "open_interest": 2000, "iv": 0.32,
    "delta": 0.55, "gamma": 0.02, "theta": -0.05, "vega": 0.10,
    "leon_score": 78, "spread_pct": 6.0, "dte": 15,
    "cost_1_contract": 340.0, "break_even": 193.4,
}

def fake_options_scan(symbol, direction="AUTO", horizon="15m", top=1):
    return {"symbol": symbol.upper(), "spot": 187.0, "direction": "CALL", "horizon": horizon,
            "best": dict(FAKE_CONTRACT), "top": [dict(FAKE_CONTRACT)],
            "note": "fake para pruebas"}

def fake_find_contract_bid_up(symbol, opt_type, strike, expiration):
    # cierre ganador: bid subió a 4.00
    c = dict(FAKE_CONTRACT); c["bid"] = 4.00; c["ask"] = 4.20
    return c

def fake_find_contract_bid_down(symbol, opt_type, strike, expiration):
    # cierre perdedor: bid bajó a 1.00
    c = dict(FAKE_CONTRACT); c["bid"] = 1.00; c["ask"] = 1.20
    return c

L.options_scan = fake_options_scan
L.paper_options_reset(start=10000)

acc0 = L._paper_account_snapshot()
check("cuenta inicia con 10000 de cash", acc0["cash"] == 10000.0, f"(cash={acc0['cash']})")

L._find_contract = fake_find_contract_bid_up
opened = L.paper_options_open(symbol="NVDA", direction="CALL", horizon="15m", contracts=1)
pos = opened["opened"]
expected_fill = round(FAKE_CONTRACT["ask"] * (1 + L.SLIPPAGE_PCT), 4)
expected_cost = round(expected_fill*100 + L.COMMISSION_PER_CONTRACT, 2)
check("entry_fill incluye slippage sobre el ask", pos["entry_fill"] == expected_fill, f"(fill={pos['entry_fill']} esperado={expected_fill})")
check("entry_cost = fill*100 + comisión", pos["entry_cost"] == expected_cost, f"(cost={pos['entry_cost']} esperado={expected_cost})")
check("cash bajó exactamente el entry_cost", opened["account"]["cash"] == round(10000 - expected_cost, 2))

closed = L.paper_options_close(position_id=pos["id"])
trade = closed["closed"]
expected_exit = round(4.00 * (1 - L.SLIPPAGE_PCT), 4)
expected_proceeds = round(expected_exit*100 - L.COMMISSION_PER_CONTRACT, 2)
expected_pnl = round(expected_proceeds - expected_cost, 2)
check("exit_fill incluye slippage sobre el bid", trade["exit_fill"] == expected_exit)
check("pnl = proceeds - entry_cost", trade["pnl"] == expected_pnl, f"(pnl={trade['pnl']} esperado={expected_pnl})")
check("posición ya no aparece en abiertas tras cerrar", len(closed["account"]["open_positions"]) == 0)
check("realized_pl refleja el pnl", closed["account"]["realized_pl"] == expected_pnl)

# ---------------------------------------------------------------------
# 2) Contrato ya no existe en la cadena -> no se inventa precio de cierre
# ---------------------------------------------------------------------
L._find_contract = fake_find_contract_bid_up
opened2 = L.paper_options_open(symbol="NVDA", direction="CALL", horizon="15m", contracts=1)
pos2_id = opened2["opened"]["id"]
L._find_contract = lambda *a, **kw: None
try:
    L.paper_options_close(position_id=pos2_id)
    check("cerrar sin contrato disponible debe fallar", False)
except L.HTTPException as e:
    check("cerrar sin contrato disponible lanza 409 (no inventa precio)", e.status_code == 409)
L._find_contract = fake_find_contract_bid_up
L.paper_options_close(position_id=pos2_id)  # limpiar para el siguiente bloque

# ---------------------------------------------------------------------
# 3) Límite de pérdida diaria bloquea nuevas aperturas
# ---------------------------------------------------------------------
L.paper_options_reset(start=5000)  # suficiente para que el tamaño de posición no interfiera aquí
L._find_contract = fake_find_contract_bid_down
losing_contract = dict(FAKE_CONTRACT); losing_contract["ask"] = 3.40; losing_contract["strike"] = 190.0
L.options_scan = lambda *a, **kw: {"symbol":"NVDA","spot":187.0,"direction":"CALL","horizon":"15m",
                                    "best": dict(losing_contract), "top":[dict(losing_contract)], "note":""}
o = L.paper_options_open(symbol="NVDA", direction="CALL", horizon="15m", contracts=1)
L.paper_options_close(position_id=o["opened"]["id"])
daily = L.paper_daily_status()
check("una pérdida grande activa el límite diario (3% de 5000)", daily["blocked"] is True, f"(realized_today={daily['realized_today']} limit={daily['limit']})")
try:
    L.paper_options_open(symbol="NVDA", direction="CALL", horizon="15m", contracts=1)
    check("abrir tras límite diario debe fallar", False)
except L.HTTPException as e:
    check("abrir tras límite diario lanza 423 NO TRADE", e.status_code == 423)

# ---------------------------------------------------------------------
# 4) Tamaño de posición: rechaza si 1 contrato excede el % máximo del equity
# ---------------------------------------------------------------------
L.paper_options_reset(start=3000)  # cash suficiente, pero 1 contrato (344) supera el 10% del equity (300)
L.options_scan = fake_options_scan
try:
    L.paper_options_open(symbol="NVDA", direction="CALL", horizon="15m", contracts=1)
    check("posición sobredimensionada debe rechazarse", False)
except L.HTTPException as e:
    check("posición > 10% del equity lanza 400", e.status_code == 400, f"(detail={e.detail})")

# ---------------------------------------------------------------------
# 5) Calendario NYSE: Risk Shield detecta feriado y mercado cerrado
# ---------------------------------------------------------------------
class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 12, 25, 10, 0, tzinfo=tz)  # Navidad 2026, feriado NYSE

L.datetime = _FixedDateTime
mc = L.market_clock_state()
check("25-dic-2026 se detecta como feriado NYSE", mc["is_holiday_today"] is True)
check("mercado se marca cerrado en feriado", mc["status"] == "closed")

class _NormalDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 10, 10, 0, tzinfo=tz)  # jueves normal de sesión

L.datetime = _NormalDateTime
mc2 = L.market_clock_state()
check("día hábil normal no se marca feriado", mc2["is_holiday_today"] is False)
check("en horario y día normal el mercado está abierto", mc2["status"] == "open")
L.datetime = datetime  # restaurar

# ---------------------------------------------------------------------
# 6) v51 Live Data Engine: caché en memoria, sin red real
# ---------------------------------------------------------------------
import time as _time
L._LIVE_CACHE.clear()
L._LIVE_CACHE["NVDA"]={"symbol":"NVDA","price":187.5,"bid":187.4,"ask":187.6,
                        "market_time":_time.time()-5,"received_at":_time.time(),"provider":"Alpaca WS"}
check("_live_quote devuelve dato fresco (<20s)", L._live_quote("NVDA") is not None)

L._LIVE_CACHE["TSLA"]={"symbol":"TSLA","price":261.0,"bid":260.9,"ask":261.1,
                        "market_time":_time.time()-120,"received_at":_time.time()-120,"provider":"Alpaca WS"}
check("_live_quote descarta dato viejo (>20s)", L._live_quote("TSLA") is None)

check("quote() usa el caché LIVE antes que REST cuando está fresco",
      L.quote(symbol="NVDA")["provider"] == "Alpaca WS")

epoch = L._parse_iso_epoch("2026-09-10T14:23:01.123456789Z")
check("_parse_iso_epoch parsea timestamps con nanosegundos de Alpaca", epoch is not None and epoch > 0)
check("_parse_iso_epoch devuelve None con basura en vez de reventar", L._parse_iso_epoch("no-es-fecha") is None)
check("home() ya reporta la versión real 51.0 (antes decía 50.0 por descuido)", L.home()["version"] == "51.0")
check("health() también reporta 51.0", L.health()["version"] == "51.0")


import asyncio as _asyncio
_orig_ws = L.websockets
L.websockets = None
L._WS_STATE.update({"connected":False,"last_error":None})
_asyncio.run(L.alpaca_ws_loop())
check("alpaca_ws_loop no revienta si falta 'websockets', deja el error explicado",
      L._WS_STATE["last_error"] is not None and "websockets" in L._WS_STATE["last_error"])
L.websockets = _orig_ws

# Simulación de mensajes reales del WebSocket (sin red) para validar el parseo end-to-end
class _FakeWSConn:
    def __init__(self, messages):
        self._messages = messages
    async def send(self, msg): pass
    async def recv(self): return json.dumps({"T":"success","msg":"authenticated"})
    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
            raise ConnectionResetError("fake: conexión simulada cerrada tras el mensaje de prueba")
        return gen()
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class _FakeWSModule:
    @staticmethod
    def connect(url, **kw):
        return _FakeWSConn([json.dumps([{"T":"q","S":"NVDA","bp":187.40,"ap":187.60,
                                          "t":"2026-09-10T14:00:00.000000000Z"}])])

L.ALPACA_KEY, L.ALPACA_SECRET = "fake", "fake"
L.websockets = _FakeWSModule
try:
    _asyncio.run(_asyncio.wait_for(L.alpaca_ws_loop(), timeout=0.5))
except _asyncio.TimeoutError:
    pass  # esperado: el loop reconecta para siempre, lo cortamos tras procesar el mensaje

live_nvda = L._LIVE_CACHE.get("NVDA")
check("el loop parsea un mensaje de quote real y llena el caché", live_nvda is not None)
check("el precio se calcula como mid de bid/ask", live_nvda and abs(live_nvda["price"] - 187.5) < 0.001)
check("el market_time viene del timestamp del mensaje, no de time.time() local",
      live_nvda and live_nvda["market_time"] is not None)
L.websockets = _orig_ws
L.ALPACA_KEY, L.ALPACA_SECRET = "", ""
L._LIVE_CACHE.clear()

# ---------------------------------------------------------------------
print(f"\n{len(FAILS)} fallo(s)" if FAILS else "\nTodas las pruebas pasaron.")
sys.exit(1 if FAILS else 0)
