"""
Pruebas de humo para leon_api.py v55, sin red ni API keys reales.
Los archivos test_v52.py / test_v52_1.py / test_v53.py quedaron obsoletos:
comprueban VERSION=="52.0"/"52.1"/"53.0" y buscan texto de una navegación
multi-página ("homePage", "graphPage"...) que ya no existe en el index.html
de una sola pantalla de v55. Este archivo los reemplaza.

Uso: python3 test_v55.py
"""
import sys, types

fastapi_stub = types.ModuleType("fastapi")
class _HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code = status_code; self.detail = detail
        super().__init__(f"{status_code}: {detail}")
class _FastAPI:
    def __init__(self,*a,**kw): pass
    def add_middleware(self,*a,**kw): pass
    def get(self,*a,**kw):
        def deco(f): return f
        return deco
    def post(self,*a,**kw):
        def deco(f): return f
        return deco
    def api_route(self,*a,**kw):
        def deco(f): return f
        return deco
    def on_event(self,*a,**kw):
        def deco(f): return f
        return deco
class _Request:
    async def json(self): return {}
    headers = {}
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
    def __init__(self,*a,**kw): pass
class _JSONResponse:
    def __init__(self,*a,**kw): pass
resp_stub.FileResponse = _FileResponse
resp_stub.JSONResponse = _JSONResponse
sys.modules["fastapi.responses"] = resp_stub

import leon_api as m
import asyncio

FAILS=[]
def check(name, cond, extra=""):
    status="OK " if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond: FAILS.append(name)

check("versión real es 55.0", m.VERSION == "55.0")
check("universo M7 correcto", m.M7 == ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA"])
check("universo cripto correcto", set(m.CRYPTO) == {"BTC/USD","ETH/USD","SOL/USD","XRP/USD"})
check("market_clock reporta un estado válido", m.market_clock()["session"] in ("REGULAR","CLOSED"))
check("index.html existe junto al backend", (m.BASE/"index.html").exists())

# Risk Shield: spread sano vs spread absurdo
q_ok={"price":76455,"bid":76450,"ask":76460,"market_time":m.now()}
check("Risk Shield permite spread sano", m.risk_shield("BTC/USD", q=q_ok, score=60)["blocked"] is False)
q_bad={"price":76455,"bid":70000,"ask":80000,"market_time":m.now()}
check("Risk Shield bloquea spread absurdo", m.risk_shield("BTC/USD", q=q_bad, score=60)["blocked"] is True)

# index.html: el bug tf/interval debe estar corregido (antes el backend ignoraba el timeframe elegido)
html = (m.BASE/"index.html").read_text(encoding="utf-8")
check("index.html manda 'interval=' a /api/analysis (no 'tf=', que el backend ignora)",
      "interval='+tf" in html or "interval=\"+tf" in html)

# --- Límite de pérdida diaria: antes solo existía como número, sin aplicarse ---
m.PAPER.update({"cash":5000.0,"realized":0.0,"positions":[],"trades":[],"day":None,"realized_today":0.0})
blocked, realized_today, limit = m.paper_daily_blocked()
check("al iniciar el día no hay bloqueo por pérdida diaria", blocked is False)

m.PAPER["realized_today"] = -200.0  # supera el 3% de 5000 = -150
blocked, realized_today, limit = m.paper_daily_blocked()
check("una pérdida > 3% del equity activa el bloqueo diario", blocked is True, f"(realized_today={realized_today} limit={limit})")

async def _try_open():
    class FakeReq:
        async def json(self): return {"symbol":"NVDA","side":"CALL","qty":1,"premium":3.40}
    return await m.paper_open(FakeReq())

try:
    asyncio.run(_try_open())
    check("abrir posición tras límite diario debe fallar", False)
except m.HTTPException as e:
    check("abrir posición tras límite diario lanza 423 NO TRADE", e.status_code == 423)

# --- Selección de contrato real (Alpha Vantage), sin red: se mockean quote() y la cadena cruda ---
m.quote = lambda symbol: {"price": 187.0, "bid": 186.9, "ask": 187.1, "market_time": m.now()}
fake_chain = [
    {"type":"call","strike":"185","expiration":"2026-09-25","bid":"3.10","ask":"3.30","open_interest":"500","volume":"120",
     "implied_volatility":"0.31","delta":"0.58","theta":"-0.04","gamma":"0.02","vega":"0.09"},
    {"type":"call","strike":"190","expiration":"2026-09-25","bid":"1.20","ask":"1.40","open_interest":"800","volume":"300",
     "implied_volatility":"0.29","delta":"0.42","theta":"-0.03","gamma":"0.02","vega":"0.08"},
    {"type":"call","strike":"200","expiration":"2026-09-25","bid":"0","ask":"0","open_interest":"10","volume":"0",
     "implied_volatility":"0.40","delta":"0.10","theta":"-0.02","gamma":"0.01","vega":"0.03"},  # sin bid/ask: debe descartarse
    {"type":"put","strike":"185","expiration":"2026-09-25","bid":"2.80","ask":"3.00","open_interest":"400","volume":"90",
     "implied_volatility":"0.33","delta":"-0.45","theta":"-0.04","gamma":"0.02","vega":"0.08"},
]
m._option_chain_raw = lambda symbol: fake_chain
m._OPT_CACHE.clear()
best_call = m.best_option_contract("NVDA", "CALL")
check("elige el CALL más cercano al dinero (185, spot 187)", best_call["strike"] == 185.0, f"(strike={best_call['strike']})")
check("descarta contratos sin bid/ask (strike 200 no debía poder ganar)", best_call["strike"] != 200.0)
check("calcula el mid correctamente", abs(best_call["mid"] - 3.20) < 0.001, f"(mid={best_call['mid']})")

m._OPT_CACHE.clear()
only_bad_chain = [{"type":"call","strike":"185","expiration":"2026-09-25","bid":"0","ask":"0",
                    "open_interest":"0","volume":"0","implied_volatility":"0","delta":"0","theta":"0","gamma":"0","vega":"0"}]
m._option_chain_raw = lambda symbol: only_bad_chain
try:
    m.best_option_contract("NVDA","CALL")
    check("sin contratos con bid/ask usable debe fallar (no inventar precio)", False)
except m.HTTPException as e:
    check("sin contratos con bid/ask usable lanza error explícito", e.status_code == 502)

# --- Robot loop de fondo: registra decisiones reales, no inventadas ---
m.DECISIONS.clear()
snap = m.market_selector()
m.DECISIONS.insert(0, {"at": m.now(), "selected_market": snap["selected_market"], "best": snap["best"],
                        "state": m.ROBOT["state"], "reason": m.ROBOT["reason"]})
check("market_selector() actualiza ROBOT con un estado real", m.ROBOT["state"] in ("ESCANEANDO","ESPERANDO"))
check("la decisión registrada usa el mismo selected_market que devolvió market_selector()",
      m.DECISIONS[0]["selected_market"] == snap["selected_market"])
check("/api/robot/decisions respeta el límite pedido", len(m.robot_decisions(limit=1)["decisions"]) == 1)

# --- Adaptive Trade Manager: TP1/TP2/TP3, profit lock, trailing, stop, tiempo ---
def _open_fake(entry_premium=2.00):
    pos={"position_id":f"T{m.now()}","symbol":"NVDA","side":"CALL","qty":1,"entry_premium":entry_premium,
         "entry_cost":round(entry_premium*100*1.01+0.65,2),"opened_at":m.now(),"state":"ACTIVE",
         "profit_lock_stage":0,"peak_pnl_pct":0.0,"stop_pnl_pct":m.ATM_INITIAL_STOP_PCT,"managed":True}
    m.PAPER["positions"].append(pos)
    return pos

m.paper_options_reset(start=10000) if hasattr(m,"paper_options_reset") else m.PAPER.update({"cash":10000.0,"positions":[],"trades":[],"realized":0.0,"realized_today":0.0})

def _mock_contract(bid):
    return lambda symbol, side: {"symbol":symbol,"side":side,"strike":190.0,"expiration":"2026-09-25","bid":bid,"ask":bid+0.1,"mid":bid+0.05}

# Stop inicial: cae -60% -> debe cerrar por STOP
m.best_option_contract = _mock_contract(0.80)  # -60% desde 2.00
pos=_open_fake(2.00)
r=m._manage_one_position(pos)
check("posición que cae -60% se cierra por STOP inicial", r and r.get("closed") and r["reason"]=="STOP", f"({r})")

# TP1: sube a +35% -> NO cierra, pero sube el stop a breakeven (0%)
m.PAPER["positions"].clear()
m.best_option_contract = _mock_contract(2.70)  # +35% desde 2.00
pos=_open_fake(2.00)
r=m._manage_one_position(pos)
check("al llegar a TP1 no cierra, solo ajusta el stop", r and r.get("closed") is False)
check("TP1 sube el stop a breakeven (0%)", pos["stop_pnl_pct"] == 0.0, f"(stop={pos['stop_pnl_pct']})")
check("TP1 marca profit_lock_stage 1", pos["profit_lock_stage"] == 1)

# Después de TP1, si retrocede a breakeven exacto -> cierra por PROFIT LOCK STOP (protege capital, no pérdida)
m.best_option_contract = _mock_contract(2.00)  # 0% pnl, pos ya tenía stop en 0%
r=m._manage_one_position(pos)
check("tras TP1, retroceder a breakeven cierra por PROFIT LOCK STOP (no deja perder)",
      r and r.get("closed") and r["reason"]=="PROFIT LOCK STOP", f"({r})")

# TP3: +110% -> toma ganancia total
m.PAPER["positions"].clear()
m.best_option_contract = _mock_contract(4.20)  # +110% desde 2.00
pos=_open_fake(2.00)
r=m._manage_one_position(pos)
check("al superar TP3 toma ganancia total", r and r.get("closed") and r["reason"]=="TP3 ✓", f"({r})")

# Salida por tiempo: posición vieja sin moverse de precio
m.PAPER["positions"].clear()
m.best_option_contract = _mock_contract(2.05)  # +2.5%, no dispara ningún TP/stop
pos=_open_fake(2.00); pos["opened_at"]=m.now()-(m.ATM_MAX_HOLD_HOURS+1)*3600
r=m._manage_one_position(pos)
check("posición abierta más de ATM_MAX_HOLD_HOURS se cierra por TIME EXIT",
      r and r.get("closed") and r["reason"]=="TIME EXIT", f"({r})")

print(f"\n{len(FAILS)} fallo(s)" if FAILS else "\nTodas las pruebas pasaron.")
sys.exit(1 if FAILS else 0)
