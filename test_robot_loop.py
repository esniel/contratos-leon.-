"""Tests del loop automático del robot LEONIX v57.1.

Sin red ni API keys: market_selector, _fetch_candles, quote y market_clock
se patchean con datos sintéticos deterministas. El scoring técnico y el
Risk Shield son los reales.
"""
import os
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)
os.environ.pop("TWELVE_DATA_API_KEY", None)

import sys, types, asyncio, time as _time
from datetime import datetime

# --- Stub mínimo de fastapi (igual que test_paper_engine) ---
fastapi = types.ModuleType("fastapi")
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
    def websocket(self, *a, **k):
        def deco(fn): return fn
        return deco
    def api_route(self, *a, **k):
        def deco(fn): return fn
        return deco
fastapi.FastAPI = _FastAPI
class _HTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        super().__init__(detail); self.status_code = status_code; self.detail = detail
fastapi.HTTPException = _HTTPException
class _Request: pass
fastapi.Request = _Request
class _WebSocket: pass
fastapi.WebSocket = _WebSocket
class _WebSocketDisconnect(Exception): pass
fastapi.WebSocketDisconnect = _WebSocketDisconnect
sys.modules["fastapi"] = fastapi
resp_stub = types.ModuleType("fastapi.responses")
resp_stub.FileResponse = object; resp_stub.JSONResponse = object
sys.modules["fastapi.responses"] = resp_stub
cors_stub = types.ModuleType("fastapi.middleware.cors")
cors_stub.CORSMiddleware = object
mw_stub = types.ModuleType("fastapi.middleware")
mw_stub.cors = cors_stub
sys.modules["fastapi.middleware"] = mw_stub
sys.modules["fastapi.middleware.cors"] = cors_stub
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import leon_api as L

passed, failed = 0, 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"[OK  ] {name}")
    else: failed += 1; print(f"[FAIL] {name}")

# --- Guardar originales para restaurar ---
_orig = {"market_selector": L.market_selector, "_fetch_candles": L._fetch_candles,
         "quote": L.quote, "market_clock": L.market_clock}

def reset_state():
    L.PAPER["cash"] = L.PAPER["starting_cash"]; L.PAPER["realized"] = 0.0
    L.PAPER["positions"].clear(); L.PAPER["trades"].clear(); L.DECISIONS.clear()
    L.ROBOT.update({"enabled": True, "state": "WAIT", "symbol": None,
                    "reason": "", "cycles": 0, "last_cycle_at": None, "last_decision": None})

def synth_candles():
    """Velas sintéticas con tendencia BULL y score 73 (verificado)."""
    pat = [1, -1, 1, 1, -1]; closes = [100.0]
    for i in range(1, 45):
        closes.append(closes[-1] + pat[(i - 1) % len(pat)])
    return [{"time": i, "open": closes[i - 1] if i > 0 else closes[0],
             "high": c + 0.3, "low": c - 0.3, "close": c, "volume": 1e6}
            for i, c in enumerate(closes)]

def fake_quote(price):
    t = L.now()
    return {"symbol": "AAPL", "price": price, "bid": price - 0.05, "ask": price + 0.05,
            "provider": "TEST", "market_time": t, "received_at": t}

def patch_all(price=110.0, market="ACCIONES", best="AAPL", candles=None):
    L.market_clock = lambda: {"open": True, "session": "REGULAR", "now_et": "x",
                              "auto_scan": True, "note": ""}
    L.market_selector = lambda: {"selected_market": market,
                                 "best": {"symbol": best, "score": 80} if best else None,
                                 "stocks": [], "crypto": [], "robot": L.ROBOT}
    c = candles if candles is not None else synth_candles()
    L._fetch_candles = lambda *a, **k: c
    L.quote = lambda s="": dict(fake_quote(price))

def restore():
    for k, v in _orig.items():
        setattr(L, k, v)

# =====================================================================
print("== 1. Sin datos técnicos: WAIT, sin entradas ==")
reset_state()
patch_all(market="NO TRADE", best=None, candles=[])
asyncio.run(L.robot_cycle())
check("sin oportunidades no abre posiciones", len(L.PAPER["positions"]) == 0)
check("estado WAIT", L.ROBOT["state"] == "WAIT")
check("journal registra WAIT", L.DECISIONS and L.DECISIONS[0]["decision"] == "WAIT")
check("ciclos contados", L.ROBOT["cycles"] == 1 and L.ROBOT["last_cycle_at"] is not None)

print("== 2. Robot pausado: no opera ==")
reset_state(); patch_all()
L.ROBOT["enabled"] = False
asyncio.run(L.robot_cycle())
check("pausado no abre posiciones", len(L.PAPER["positions"]) == 0)
check("estado PAUSED", L.ROBOT["state"] == "PAUSED")
L.ROBOT["enabled"] = True

print("== 3. Señal válida: ARMED -> PAPER ENTRY ==")
reset_state(); patch_all(price=110.0)
tech = L.technical_score(synth_candles())
check("sintético da BULL", tech["trend"] == "BULL")
check("sintético da score>=70", tech["score"] >= 70)
asyncio.run(L.robot_cycle())
check("abre 1 posición SPOT", len(L.PAPER["positions"]) == 1)
pos = L.PAPER["positions"][0]
check("es LONG", pos["side"] == "LONG" and pos["kind"] == "SPOT")
check("stop = soporte estructural", pos["stop"] == round(tech["support"], 4))
check("qty dimensionada por riesgo (9 acc)", pos["qty"] == 9)
check("entry_cost 990.0", pos["entry_cost"] == 990.0)
check("tp1/tp2/tp3 coherentes", pos["tp1"] == 120.95 and pos["tp2"] == 128.25 and pos["tp3"] == 139.2)
check("estado ACTIVE", L.ROBOT["state"] == "ACTIVE")
kinds = [d["decision"] for d in L.DECISIONS]
check("journal: ARMED y ACTIVE", "ARMED" in kinds and "ACTIVE" in kinds)

print("== 4. Con posición abierta: no duplica entradas ==")
patch_all(price=110.0)
asyncio.run(L.robot_cycle())
check("sigue 1 posición", len(L.PAPER["positions"]) == 1)
check("estado ACTIVE en gestión", L.ROBOT["state"] == "ACTIVE")

print("== 5. STOP alcanzado: cierre con pérdida ==")
patch_all(price=100.0)  # bajo el stop 102.7
closed = L._manage_positions()
check("cerró la posición", closed and len(L.PAPER["positions"]) == 0)
check("P/L realizado = -90", L.PAPER["realized"] == -90.0)
check("journal EXIT por STOP", L.DECISIONS[0]["decision"] == "EXIT" and "STOP" in L.DECISIONS[0]["reason"])
exits = [t for t in L.PAPER["trades"] if t["event"] == "EXIT"]
check("trade EXIT registrado con pnl", exits and exits[0]["pnl"] == -90.0)

print("== 6. Profit lock: TP1 -> stop a breakeven ==")
reset_state(); patch_all(price=110.0)
asyncio.run(L.robot_cycle())
pos = L.PAPER["positions"][0]
L.quote = lambda s="": dict(fake_quote(121.0))  # >= tp1 120.95
L._manage_positions()
check("stage 1", pos["profit_lock_stage"] == 1)
check("stop en breakeven", pos["stop"] == pos["entry_price"])
check("estado PROFIT LOCK", pos["state"] == "PROFIT LOCK")

print("== 7. Trailing: el stop sube con el máximo ==")
L.quote = lambda s="": dict(fake_quote(130.0))
L._manage_positions()
check("trailing stop 128.05", pos["stop"] == round(130.0 * (1 - 0.015), 4))
check("stage 2", pos["profit_lock_stage"] == 2)
check("journal PROFIT LOCK", L.DECISIONS[0]["decision"] == "PROFIT LOCK")

print("== 8. TP3: cierre con ganancia ==")
L.quote = lambda s="": dict(fake_quote(140.0))  # >= tp3 139.2
L._manage_positions()
check("posición cerrada en TP3", len(L.PAPER["positions"]) == 0)
check("realizado positivo", L.PAPER["realized"] > 0)

print("== 9. TIME EXIT ==")
reset_state(); patch_all(price=110.0)
asyncio.run(L.robot_cycle())
pos = L.PAPER["positions"][0]
pos["opened_at"] = _time.time() - (L.TIME_EXIT_MIN + 1) * 60
L._manage_positions()
check("cerrada por tiempo", len(L.PAPER["positions"]) == 0)
check("razón TIME EXIT", "TIME EXIT" in L.DECISIONS[0]["reason"])

print("== 10. Dato viejo: STALE, no opera a ciegas ==")
reset_state(); patch_all(price=110.0)
asyncio.run(L.robot_cycle())
pos = L.PAPER["positions"][0]
def bad_quote(s=""):
    raise RuntimeError("proveedor caído")
L.quote = bad_quote
L._manage_positions()
check("posición marcada STALE", pos["state"] == "STALE")
check("no se cerró a ciegas", len(L.PAPER["positions"]) == 1)
L.quote = lambda s="": dict(fake_quote(110.0))
L._manage_positions()
check("se recupera a ACTIVE", pos["state"] == "ACTIVE")

print("== 11. Límite de pérdida diaria bloquea entradas ==")
reset_state(); patch_all(price=110.0)
today = L.now()
L.PAPER["trades"].append({"event": "EXIT", "kind": "SPOT", "pnl": -400.0, "closed_at": today})
check("pérdida diaria -400 supera límite 3%", L._daily_loss_hit())
asyncio.run(L.robot_cycle())
check("bloqueado: sin posiciones", len(L.PAPER["positions"]) == 0)
check("estado BLOCKED", L.ROBOT["state"] == "BLOCKED")
check("journal NO TRADE", L.DECISIONS[0]["decision"] == "NO TRADE")

print("== 12. Posiciones de opciones manuales no las toca el manager ==")
reset_state()
L.PAPER["positions"].append({"position_id": "P1", "symbol": "AAPL", "side": "CALL",
                             "qty": 1, "entry_cost": 100.0, "state": "ACTIVE",
                             "market_value": 100.0})
patch_all(price=50.0)
L._manage_positions()
check("opción intacta", len(L.PAPER["positions"]) == 1 and L.PAPER["positions"][0]["position_id"] == "P1")

print("== 13. Métricas y decisiones ==")
reset_state(); patch_all(price=110.0)
asyncio.run(L.robot_cycle())
L.quote = lambda s="": dict(fake_quote(140.0))
L._manage_positions()
m = L.metrics()
check("métricas con muestra", m["sample_size"] == 1 and "win_rate" in m)
check("profit_factor presente", "profit_factor" in m and "expectancy" in m)
check("nota honesta incluida", "nunca es probabilidad de ganar" in m["note"])
d = L.robot_decisions()
check("endpoint decisiones", d["cycles"] >= 1 and len(d["decisions"]) > 0)

restore()
print()
print(f"{passed} OK, {failed} fallo(s)")
sys.exit(1 if failed else 0)
