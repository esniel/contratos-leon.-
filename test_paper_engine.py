"""
Pruebas de humo para leon_api.py sin red ni API keys reales.
Stubea fastapi/requests lo mínimo necesario e inyecta datos falsos de
opciones para validar la aritmética del Paper Options Engine, el
bloqueo de Risk Shield 2.0 y el calendario de feriados NYSE.

Uso: python3 test_paper_engine.py
"""
import sys, types
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
print(f"\n{len(FAILS)} fallo(s)" if FAILS else "\nTodas las pruebas pasaron.")
sys.exit(1 if FAILS else 0)
