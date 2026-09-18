import os, time, math, asyncio, json, re, requests
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    import websockets
except ImportError:
    websockets = None

BASE = Path(__file__).resolve().parent
VERSION = "55.0"
NY = ZoneInfo("America/New_York")
M7 = ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA"]
CRYPTO = ["BTC/USD","ETH/USD","SOL/USD","XRP/USD"]

ALPACA_KEY = os.getenv("ALPACA_KEY_ID","").strip()
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY","").strip()
TD_KEY = os.getenv("TWELVE_DATA_API_KEY","").strip()
AV_KEY = os.getenv("ALPHAVANTAGE_API_KEY","").strip()
TV_SECRET = os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip()
ALPACA_WS_URL = os.getenv("ALPACA_WS_URL","wss://stream.data.alpaca.markets/v2/iex").strip()
ALPACA_CRYPTO_WS_URL = os.getenv("ALPACA_CRYPTO_WS_URL","wss://stream.data.alpaca.markets/v1beta3/crypto/us").strip()

app = FastAPI(title="LEONIX Market Intelligence API", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["GET","POST","HEAD"], allow_headers=["*"])

LIVE = {}
CRYPTO_LIVE = {}
WS = {"stocks":{"connected":False,"last_message":None,"reconnects":0,"error":None},
      "crypto":{"connected":False,"last_message":None,"reconnects":0,"error":None}}
HEALTH = {k:{"last_ok":None,"last_data":None,"latency_ms":None,"error":None}
          for k in ("alpaca","twelvedata","alphavantage","crypto")}
ALERTS=[]
DECISIONS=[]
PAPER={"starting_cash":10000.0,"cash":10000.0,"realized":0.0,"positions":[],"trades":[],
       "daily_loss_limit_pct":3.0,"max_position_pct":10.0,"day":None,"realized_today":0.0}
def paper_daily_reset_if_needed():
    today=datetime.now(NY).date().isoformat()
    if PAPER.get("day")!=today:
        PAPER["day"]=today; PAPER["realized_today"]=0.0
def paper_daily_blocked():
    paper_daily_reset_if_needed()
    equity=PAPER["cash"]+sum(p["entry_cost"] for p in PAPER["positions"])
    limit=-equity*(PAPER["daily_loss_limit_pct"]/100)
    return PAPER["realized_today"]<=limit, round(PAPER["realized_today"],2), round(limit,2)
ROBOT={"enabled":True,"mode":"PAPER","state":"ESPERANDO","market":"AUTO","selected_market":"NO TRADE",
       "symbol":None,"reason":"Esperando datos confiables","updated_at":None,"profit_lock_stage":0}

def now(): return time.time()
def headers(): return {"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET}
def record(name, ok=True, data_time=None, latency=None, error=None):
    h=HEALTH[name]
    if ok:
        h["last_ok"]=now(); h["error"]=None
        if data_time: h["last_data"]=data_time
        if latency is not None: h["latency_ms"]=round(latency,1)
    else: h["error"]=str(error or "error")
def parse_ts(v):
    if not v: return None
    try:
        s=str(v)
        if s.endswith("Z"): s=s[:-1]+"+00:00"
        s=re.sub(r'(\.\d{6})\d+',r'\1',s)
        return datetime.fromisoformat(s).timestamp()
    except: return None
def fresh(q, seconds=20):
    if not q: return False
    t=q.get("market_time") or q.get("received_at")
    return bool(t and now()-t <= seconds)

NYSE_HOLIDAYS={
 date(2026,1,1),date(2026,1,19),date(2026,2,16),date(2026,4,3),date(2026,5,25),date(2026,6,19),
 date(2026,7,3),date(2026,9,7),date(2026,11,26),date(2026,12,25),
 date(2027,1,1),date(2027,1,18),date(2027,2,15),date(2027,3,26),date(2027,5,31),date(2027,6,18),
 date(2027,7,5),date(2027,9,6),date(2027,11,25),date(2027,12,24)}
def market_clock():
    n=datetime.now(NY); d=n.date(); weekday=n.weekday()
    regular=(weekday<5 and d not in NYSE_HOLIDAYS and
             (n.hour>9 or (n.hour==9 and n.minute>=30)) and n.hour<16)
    return {"open":regular,"session":"REGULAR" if regular else "CLOSED","now_et":n.isoformat(),
            "auto_scan":regular,"note":"LEONIX escanea al abrir; no compra automáticamente a las 9:30."}

def provider_snapshot():
    out={}
    for name,h in HEALTH.items():
        ref=h["last_data"] or h["last_ok"]
        age=now()-ref if ref else None
        status="DOWN" if age is None or age>90 else ("LIVE" if age<=20 else "DELAYED")
        out[name]={"status":status,"age_seconds":round(age,1) if age is not None else None,
                   "latency_ms":h["latency_ms"],"last_error":h["error"]}
    return out

def alpaca_stock_rest(symbol):
    if not (ALPACA_KEY and ALPACA_SECRET): raise HTTPException(503,"Alpaca no configurado")
    t=time.time()
    r=requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",headers=headers(),timeout=12)
    if r.status_code>=400: raise HTTPException(r.status_code,"Alpaca stock error")
    q=r.json().get("quote",{}); bid=float(q.get("bp") or 0); ask=float(q.get("ap") or 0)
    price=(bid+ask)/2 if bid and ask else (ask or bid)
    mt=parse_ts(q.get("t")); record("alpaca",True,mt,(time.time()-t)*1000)
    return {"symbol":symbol,"price":price,"bid":bid,"ask":ask,"provider":"Alpaca REST",
            "market_time":mt,"received_at":now()}

def alpaca_crypto_rest(symbol):
    if not (ALPACA_KEY and ALPACA_SECRET): raise HTTPException(503,"Alpaca no configurado")
    t=time.time()
    r=requests.get(f"https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes",
                   headers=headers(),params={"symbols":symbol},timeout=12)
    if r.status_code>=400: raise HTTPException(r.status_code,"Alpaca crypto error")
    q=(r.json().get("quotes") or {}).get(symbol,{})
    bid=float(q.get("bp") or 0); ask=float(q.get("ap") or 0); price=(bid+ask)/2 if bid and ask else ask or bid
    mt=parse_ts(q.get("t")); record("crypto",True,mt,(time.time()-t)*1000)
    return {"symbol":symbol,"price":price,"bid":bid,"ask":ask,"provider":"Alpaca Crypto REST",
            "market_time":mt,"received_at":now()}

def quote(symbol):
    symbol=symbol.upper().replace("-","/")
    if symbol in CRYPTO:
        q=CRYPTO_LIVE.get(symbol)
        return q if fresh(q) else alpaca_crypto_rest(symbol)
    q=LIVE.get(symbol)
    return q if fresh(q) else alpaca_stock_rest(symbol)

async def ws_stocks():
    if not websockets or not ALPACA_KEY: return
    backoff=2
    while True:
        try:
            async with websockets.connect(ALPACA_WS_URL,ping_interval=15,ping_timeout=10) as ws:
                await ws.send(json.dumps({"action":"auth","key":ALPACA_KEY,"secret":ALPACA_SECRET})); await ws.recv()
                await ws.send(json.dumps({"action":"subscribe","quotes":M7}))
                WS["stocks"].update({"connected":True,"error":None}); backoff=2
                async for raw in ws:
                    for m in json.loads(raw):
                        if m.get("T")!="q": continue
                        s=m.get("S"); b=float(m.get("bp") or 0); a=float(m.get("ap") or 0); p=(a+b)/2 if a and b else a or b
                        mt=parse_ts(m.get("t"))
                        LIVE[s]={"symbol":s,"price":p,"bid":b,"ask":a,"provider":"Alpaca WS","market_time":mt,"received_at":now()}
                        WS["stocks"]["last_message"]=now(); record("alpaca",True,mt)
        except asyncio.CancelledError: raise
        except Exception as e:
            WS["stocks"].update({"connected":False,"error":str(e)}); WS["stocks"]["reconnects"]+=1
            await asyncio.sleep(backoff); backoff=min(backoff*2,60)

async def ws_crypto():
    if not websockets or not ALPACA_KEY: return
    backoff=2
    while True:
        try:
            async with websockets.connect(ALPACA_CRYPTO_WS_URL,ping_interval=15,ping_timeout=10) as ws:
                await ws.send(json.dumps({"action":"auth","key":ALPACA_KEY,"secret":ALPACA_SECRET})); await ws.recv()
                await ws.send(json.dumps({"action":"subscribe","quotes":CRYPTO}))
                WS["crypto"].update({"connected":True,"error":None}); backoff=2
                async for raw in ws:
                    msgs=json.loads(raw); msgs=msgs if isinstance(msgs,list) else [msgs]
                    for m in msgs:
                        if m.get("T")!="q": continue
                        s=m.get("S"); b=float(m.get("bp") or 0); a=float(m.get("ap") or 0); p=(a+b)/2 if a and b else a or b
                        mt=parse_ts(m.get("t"))
                        CRYPTO_LIVE[s]={"symbol":s,"price":p,"bid":b,"ask":a,"provider":"Alpaca Crypto WS","market_time":mt,"received_at":now()}
                        WS["crypto"]["last_message"]=now(); record("crypto",True,mt)
        except asyncio.CancelledError: raise
        except Exception as e:
            WS["crypto"].update({"connected":False,"error":str(e)}); WS["crypto"]["reconnects"]+=1
            await asyncio.sleep(backoff); backoff=min(backoff*2,60)

@app.on_event("startup")
async def startup():
    if ALPACA_KEY and ALPACA_SECRET:
        asyncio.create_task(ws_stocks()); asyncio.create_task(ws_crypto())
    asyncio.create_task(robot_loop())
    asyncio.create_task(trade_manager_loop())

@app.api_route("/",methods=["GET","HEAD"],include_in_schema=False)
def home():
    return FileResponse(BASE/"index.html",media_type="text/html; charset=utf-8")

@app.api_route("/health",methods=["GET","HEAD"])
@app.api_route("/api/health",methods=["GET","HEAD"])
def health():
    return {"ok":True,"service":"LEONIX","version":VERSION,"utf8":"León",
            "alpaca":bool(ALPACA_KEY and ALPACA_SECRET),"market_clock":market_clock(),
            "ws":WS,"data_hub":data_hub()}

@app.get("/api/data-hub/status")
def data_hub():
    p=provider_snapshot()
    live=any(x["status"]=="LIVE" for x in p.values())
    delayed=any(x["status"]=="DELAYED" for x in p.values())
    overall="LIVE" if live else ("DELAYED" if delayed else "DOWN")
    return {"overall":overall,"safe_to_trade":overall!="DOWN","providers":p}

@app.get("/api/live/status")
def live_status(): return {"version":VERSION,"stocks":WS["stocks"],"crypto":WS["crypto"]}
@app.get("/api/live/quotes")
def live_quotes(): return {"stocks":LIVE,"crypto":CRYPTO_LIVE}
@app.get("/api/market/clock")
def clock(): return market_clock()

@app.get("/api/quote")
def api_quote(symbol:str="NVDA"): return quote(symbol)

@app.get("/api/chart")
def chart(symbol:str="NVDA", interval:str="5min", outputsize:int=120):
    # Twelve Data is used for historical candles; live quote remains Alpaca-first.
    if not TD_KEY: raise HTTPException(503,"TWELVE_DATA_API_KEY no configurada")
    s=symbol.replace("/","/")
    r=requests.get("https://api.twelvedata.com/time_series",
        params={"symbol":s,"interval":interval,"outputsize":min(max(outputsize,20),500),"apikey":TD_KEY},timeout=15)
    d=r.json()
    if d.get("status")=="error": raise HTTPException(502,d.get("message","Twelve Data error"))
    vals=list(reversed(d.get("values",[])))
    candles=[{"time":v["datetime"],"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),
              "close":float(v["close"]),"volume":float(v.get("volume") or 0)} for v in vals]
    record("twelvedata",True)
    return {"symbol":symbol,"interval":interval,"provider":"Twelve Data","candles":candles}

def technical_score(candles):
    if len(candles)<30: return {"score":50,"trend":"NEUTRAL","rsi":None,"support":None,"resistance":None}
    closes=[c["close"] for c in candles]
    gains=[]; losses=[]
    for a,b in zip(closes[-15:-1],closes[-14:]):
        d=b-a; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    rsi=100 if al==0 else 100-(100/(1+ag/al))
    sma9=sum(closes[-9:])/9; sma21=sum(closes[-21:])/21
    trend="BULL" if sma9>sma21 else "BEAR"
    score=50+(15 if trend=="BULL" else -15)+(8 if 45<=rsi<=65 and trend=="BULL" else 0)+(-8 if rsi>75 else 0)
    lows=[c["low"] for c in candles[-30:]]; highs=[c["high"] for c in candles[-30:]]
    return {"score":max(0,min(100,round(score))),"trend":trend,"rsi":round(rsi,1),
            "support":round(min(lows),4),"resistance":round(max(highs),4)}

def risk_shield(symbol, q=None, score=50):
    q=q or quote(symbol); gates=[]; blocked=False
    bid=float(q.get("bid") or 0); ask=float(q.get("ask") or 0); price=float(q.get("price") or 0)
    age=now()-(q.get("market_time") or q.get("received_at") or 0)
    data_ok=age<=90
    gates.append({"name":"datos","status":"GREEN" if data_ok else "RED","detail":f"age {age:.1f}s"})
    if not data_ok: blocked=True
    spread=((ask-bid)/price*100) if bid and ask and price else 0
    spread_ok=(spread<=1.0 if symbol in CRYPTO else spread<=0.5)
    gates.append({"name":"spread","status":"GREEN" if spread_ok else "RED","detail":f"{spread:.3f}%"})
    if not spread_ok: blocked=True
    session_ok=True if symbol in CRYPTO else market_clock()["open"]
    gates.append({"name":"sesión","status":"GREEN" if session_ok else "RED","detail":"24/7" if symbol in CRYPTO else market_clock()["session"]})
    if not session_ok: blocked=True
    consensus_ok=score>=58
    gates.append({"name":"consenso","status":"GREEN" if consensus_ok else "YELLOW","detail":str(score)})
    return {"status":"RED" if blocked else ("GREEN" if consensus_ok else "YELLOW"),
            "blocked":blocked,"gates":gates,"decision":"NO TRADE" if blocked else ("ARMED" if consensus_ok else "WAIT")}

@app.get("/api/risk-shield")
def shield(symbol:str="NVDA", score:int=50): return risk_shield(symbol.upper().replace("-","/"),score=score)

def market_quality(symbol):
    try:
        q=quote(symbol); p=float(q["price"]); b=float(q.get("bid") or 0); a=float(q.get("ask") or 0)
        spread=((a-b)/p*100) if a and b and p else 0.2
        freshness=max(0,100-min(100,(now()-(q.get("market_time") or q.get("received_at") or now()))*3))
        score=max(0,min(100,75-spread*20+freshness*.2))
        return {"symbol":symbol,"score":round(score,1),"spread_pct":round(spread,4),"provider":q.get("provider")}
    except Exception as e: return {"symbol":symbol,"score":0,"error":str(e)}

@app.get("/api/market-selector")
def market_selector():
    stocks=[market_quality(s) for s in M7]
    cryptos=[market_quality(s) for s in CRYPTO]
    bs=max(stocks,key=lambda x:x["score"]); bc=max(cryptos,key=lambda x:x["score"])
    stock_allowed=market_clock()["open"] and bs["score"]>=60
    crypto_allowed=bc["score"]>=60
    if stock_allowed and (not crypto_allowed or bs["score"]>=bc["score"]): selected="ACCIONES"; best=bs
    elif crypto_allowed: selected="CRIPTO"; best=bc
    else: selected="NO TRADE"; best=None
    ROBOT.update({"selected_market":selected,"symbol":best["symbol"] if best else None,
                  "state":"ESCANEANDO" if selected!="NO TRADE" else "ESPERANDO",
                  "reason":"Selección objetiva por frescura/spread/sesión; no es garantía de beneficio.","updated_at":now()})
    return {"selected_market":selected,"best":best,"stocks":stocks,"crypto":cryptos,"robot":ROBOT}

# --- Robot corriendo en segundo plano (no solo cuando alguien pregunta) ---
ROBOT_LOOP_INTERVAL_S=30

async def robot_loop():
    while True:
        try:
            if ROBOT.get("enabled",True):
                snap=market_selector()
                DECISIONS.insert(0,{"at":now(),"selected_market":snap["selected_market"],
                                     "best":snap["best"],"state":ROBOT["state"],"reason":ROBOT["reason"]})
                del DECISIONS[200:]
        except Exception as e:
            pass
        await asyncio.sleep(ROBOT_LOOP_INTERVAL_S)

@app.get("/api/robot/decisions")
def robot_decisions(limit:int=20):
    return {"decisions":DECISIONS[:max(1,min(limit,200))],
            "note":"Snapshot del escaneo automático cada 30s. No ejecuta nada: solo selecciona mercado/símbolo candidato."}

@app.get("/api/robot/status")
def robot_status(): return ROBOT
@app.post("/api/robot/toggle")
async def robot_toggle(request:Request):
    d=await request.json(); ROBOT["enabled"]=bool(d.get("enabled",True)); ROBOT["updated_at"]=now()
    return ROBOT

@app.get("/api/paper/account")
@app.get("/api/paper/options/account")
def paper_account():
    equity=PAPER["cash"]+sum(float(p.get("market_value",p["entry_cost"])) for p in PAPER["positions"])
    return {**PAPER,"equity":round(equity,2)}

@app.post("/api/paper/options/open")
async def paper_open(request:Request):
    d=await request.json(); symbol=str(d.get("symbol","")).upper(); side=str(d.get("side","CALL")).upper()
    qty=max(1,int(d.get("qty",1))); premium=float(d.get("premium",0))
    if premium<=0: raise HTTPException(409,"Falta premium real del contrato; LEONIX no inventa precio.")
    blocked,realized_today,limit=paper_daily_blocked()
    if blocked: raise HTTPException(423,f"NO TRADE: límite de pérdida diaria alcanzado ({realized_today} / {limit}). Se reinicia mañana.")
    cost=premium*100*qty*1.01 + .65*qty
    equity=PAPER["cash"]+sum(p["entry_cost"] for p in PAPER["positions"])
    if cost>equity*(PAPER["max_position_pct"]/100): raise HTTPException(400,"Risk Shield: posición >10% del equity")
    if cost>PAPER["cash"]: raise HTTPException(400,"Cash insuficiente")
    pos={"position_id":f"P{int(now()*1000)}","symbol":symbol,"side":side,"qty":qty,"entry_premium":premium,
         "entry_cost":round(cost,2),"market_value":round(cost,2),"opened_at":now(),"state":"ACTIVE",
         "profit_lock_stage":0,"peak_pnl_pct":0.0,"stop_pnl_pct":ATM_INITIAL_STOP_PCT,"managed":bool(d.get("managed",True))}
    PAPER["cash"]-=cost; PAPER["positions"].append(pos); PAPER["trades"].append({"event":"PAPER ENTRY ✓",**pos})
    ROBOT.update({"state":"ACTIVE","symbol":symbol,"reason":"PAPER ENTRY ✓","updated_at":now()})
    return {"ok":True,"position":pos,"paper":True}

def _close_position(pos, premium, reason="MANUAL"):
    proceeds=premium*100*pos["qty"]*.99-.65*pos["qty"]; pnl=proceeds-pos["entry_cost"]
    PAPER["cash"]+=proceeds; PAPER["realized"]+=pnl
    if pos in PAPER["positions"]: PAPER["positions"].remove(pos)
    paper_daily_reset_if_needed(); PAPER["realized_today"]=round(PAPER["realized_today"]+pnl,2)
    trade={"event":"EXIT","position_id":pos["position_id"],"symbol":pos["symbol"],"reason":reason,
           "pnl":round(pnl,2),"closed_at":now()}
    PAPER["trades"].append(trade)
    ROBOT.update({"state":"EXIT","reason":f"{reason}: P/L ${pnl:.2f} en {pos['symbol']}","updated_at":now()})
    return trade

@app.post("/api/paper/options/close")
async def paper_close(request:Request):
    d=await request.json(); pid=d.get("position_id"); premium=float(d.get("premium",0))
    pos=next((p for p in PAPER["positions"] if p["position_id"]==pid),None)
    if not pos: raise HTTPException(404,"Posición no encontrada")
    if premium<=0: raise HTTPException(409,"Contrato sin precio actual; no se inventa precio de salida.")
    trade=_close_position(pos, premium, reason="MANUAL")
    return {"ok":True,"pnl":trade["pnl"],"realized":round(PAPER["realized"],2)}

# --- Adaptive Trade Manager (TP1/TP2/TP3, Profit Lock, trailing, salida por tiempo) ---
ATM_INITIAL_STOP_PCT=-50.0     # stop inicial: -50% de la prima pagada
ATM_TP1_PCT=30.0               # al llegar aquí, sube el stop a breakeven (profit lock etapa 1)
ATM_TP2_PCT=60.0               # sube el stop a +20% (profit lock etapa 2), activa trailing
ATM_TP3_PCT=100.0              # toma ganancia total
ATM_TRAIL_DROP_PCT=20.0        # en etapa 2+, sale si retrocede esto desde el máximo alcanzado
ATM_MAX_HOLD_HOURS=6.0         # salida por tiempo: no mantener una posición día-trade más de esto
ATM_LOOP_INTERVAL_S=20

def _manage_one_position(pos):
    if not pos.get("managed", True) or pos.get("state")!="ACTIVE": return None
    try:
        c=best_option_contract(pos["symbol"], pos["side"])
    except Exception as e:
        return {"position_id":pos["position_id"],"error":str(e)}
    current=c["bid"] if c.get("bid") else c.get("mid")
    if not current: return None
    pnl_pct=round((current-pos["entry_premium"])/pos["entry_premium"]*100,2)
    pos["peak_pnl_pct"]=max(pos.get("peak_pnl_pct",pnl_pct), pnl_pct)
    held_hours=(now()-pos["opened_at"])/3600
    reason=None
    if pnl_pct>=ATM_TP3_PCT:
        reason="TP3 ✓"
    elif pos.get("profit_lock_stage",0)>=2 and pnl_pct<=pos["peak_pnl_pct"]-ATM_TRAIL_DROP_PCT:
        reason="TRAILING STOP"
    elif pnl_pct<=pos.get("stop_pnl_pct",ATM_INITIAL_STOP_PCT):
        reason="STOP" if pos.get("profit_lock_stage",0)==0 else "PROFIT LOCK STOP"
    elif held_hours>=ATM_MAX_HOLD_HOURS:
        reason="TIME EXIT"
    else:
        if pnl_pct>=ATM_TP2_PCT and pos.get("profit_lock_stage",0)<2:
            pos["profit_lock_stage"]=2; pos["stop_pnl_pct"]=20.0
        elif pnl_pct>=ATM_TP1_PCT and pos.get("profit_lock_stage",0)<1:
            pos["profit_lock_stage"]=1; pos["stop_pnl_pct"]=0.0
    pos["last_pnl_pct"]=pnl_pct; pos["last_checked_at"]=now()
    if reason:
        trade=_close_position(pos, current, reason=reason)
        return {"position_id":trade["position_id"],"closed":True,"reason":reason,"pnl":trade["pnl"]}
    return {"position_id":pos["position_id"],"closed":False,"pnl_pct":pnl_pct,
            "profit_lock_stage":pos.get("profit_lock_stage",0),"stop_pnl_pct":pos.get("stop_pnl_pct")}

async def trade_manager_loop():
    while True:
        try:
            for pos in list(PAPER["positions"]):
                _manage_one_position(pos)
        except Exception:
            pass
        await asyncio.sleep(ATM_LOOP_INTERVAL_S)

@app.get("/api/adaptive-manager/stages")
def manager_stages():
    return {"initial_stop_pct":ATM_INITIAL_STOP_PCT,"tp1_pct":ATM_TP1_PCT,"tp2_pct":ATM_TP2_PCT,"tp3_pct":ATM_TP3_PCT,
            "trailing_drop_pct":ATM_TRAIL_DROP_PCT,"max_hold_hours":ATM_MAX_HOLD_HOURS,
            "positions":[{**p} for p in PAPER["positions"]],
            "note":"TP1 sube el stop a breakeven, TP2 lo sube a +20% y activa trailing, TP3 toma ganancia total. Se revisa cada 20s en el servidor."}

@app.post("/api/tradingview/webhook")
async def tv_webhook(request:Request):
    d=await request.json()
    supplied=request.headers.get("X-Leonix-Secret") or d.get("secret")
    if TV_SECRET and supplied!=TV_SECRET: raise HTTPException(401,"Secret inválido")
    alert={"received_at":now(),"payload":d,"executed":False,
           "note":"TradingView nunca ejecuta directo: pasa por Consensus + Risk Shield + Robot."}
    ALERTS.insert(0,alert); del ALERTS[100:]
    return {"ok":True,"alert":alert}
@app.get("/api/tradingview/alerts")
def tv_alerts(): return {"alerts":ALERTS}

@app.get("/api/adaptive-manager/status")
def manager_status():
    return {"robot":ROBOT,"positions":PAPER["positions"],
            "stages":["ENTRY",f"STOP {ATM_INITIAL_STOP_PCT}%",f"TP1 {ATM_TP1_PCT}%→breakeven",
                      f"TP2 {ATM_TP2_PCT}%→+20% stop",f"TRAILING -{ATM_TRAIL_DROP_PCT}%",f"TP3 {ATM_TP3_PCT}%"],
            "note":"Gestor adaptativo real, revisado cada 20s en el servidor (trade_manager_loop). PAPER/SHADOW. Live Trading desactivado."}

# --- Opciones reales (Alpha Vantage REALTIME_OPTIONS) --------------------
# v55 había perdido esto: paper_options_open aceptaba cualquier "premium" que
# le mandaran, sin una fuente de verdad detrás. Esto le da una.
_OPT_CACHE={}
OPT_CACHE_TTL=45

def _option_chain_raw(symbol):
    if not AV_KEY: raise HTTPException(503,"ALPHAVANTAGE_API_KEY no configurada")
    r=requests.get("https://www.alphavantage.co/query",
        params={"function":"REALTIME_OPTIONS","symbol":symbol,"require_greeks":"true","apikey":AV_KEY},timeout=20)
    d=r.json()
    raw=d.get("data") or d.get("options") or []
    if not raw:
        msg=d.get("Note") or d.get("Information") or "REALTIME_OPTIONS no devolvió contratos"
        raise HTTPException(502,str(msg))
    record("alphavantage",True)
    return raw

def best_option_contract(symbol, side="CALL"):
    """Elige el contrato más líquido y cercano al dinero (ATM) para el lado pedido.
    Nunca inventa un precio: si no hay contratos con bid/ask utilizables, falla explícito."""
    key=f"{symbol}:{side}"
    c=_OPT_CACHE.get(key)
    if c and now()-c["ts"]<OPT_CACHE_TTL:
        return c["data"]
    q=quote(symbol); spot=float(q.get("price") or 0)
    opt_type="call" if side.upper()=="CALL" else "put"
    chain=[o for o in _option_chain_raw(symbol) if str(o.get("type","")).lower()==opt_type]
    usable=[]
    for o in chain:
        bid=float(o.get("bid") or 0); ask=float(o.get("ask") or 0)
        if bid<=0 or ask<=0: continue
        try: strike=float(o.get("strike"))
        except (TypeError,ValueError): continue
        usable.append({**o,"bid":bid,"ask":ask,"strike":strike})
    if not usable:
        raise HTTPException(502,f"Sin contratos {opt_type} con bid/ask utilizable para {symbol}")
    usable.sort(key=lambda o: abs(o["strike"]-spot))
    best=usable[0]
    mid=round((best["bid"]+best["ask"])/2,4)
    out={"symbol":symbol,"side":side.upper(),"strike":best["strike"],"expiration":best.get("expiration"),
         "bid":best["bid"],"ask":best["ask"],"mid":mid,
         "iv":float(best.get("implied_volatility") or 0) or None,
         "delta":float(best.get("delta") or 0) or None,"theta":float(best.get("theta") or 0) or None,
         "gamma":float(best.get("gamma") or 0) or None,"vega":float(best.get("vega") or 0) or None,
         "open_interest":int(float(best.get("open_interest") or 0)),"volume":int(float(best.get("volume") or 0)),
         "spot":spot,"provider":"Alpha Vantage REALTIME_OPTIONS"}
    _OPT_CACHE[key]={"ts":now(),"data":out}
    return out

@app.get("/api/options/quote")
def options_quote(symbol:str="NVDA", side:str="CALL"):
    symbol=symbol.upper().strip()
    if symbol not in M7: raise HTTPException(400,"Opciones solo disponibles para el universo M7")
    return best_option_contract(symbol, side)

@app.get("/api/backtest/options")
def backtest_options():
    return {"method":"proxy_black_scholes","warning":"Proxy Black-Scholes; NO son opciones históricas reales.",
            "status":"available","version":VERSION}


@app.get("/api/analysis")
def analysis(symbol:str="NVDA", interval:str="5min", outputsize:int=120):
    """Atomic symbol-bound analysis. Quote + candles + levels always belong to the same symbol."""
    symbol=symbol.upper().replace("-","/")
    if symbol not in M7+CRYPTO:
        raise HTTPException(400,"Símbolo fuera del universo LEONIX v52.1")
    q=quote(symbol)
    if not TD_KEY:
        return {"symbol":symbol,"quote":q,"candles":[],"technical":{"score":50,"trend":"NEUTRAL","rsi":None,
                "support":None,"resistance":None},"risk":risk_shield(symbol,q=q,score=50),
                "sync_ok":True,"warning":"Sin TWELVE_DATA_API_KEY: niveles no disponibles"}
    r=requests.get("https://api.twelvedata.com/time_series",
        params={"symbol":symbol,"interval":interval,"outputsize":min(max(outputsize,30),500),"apikey":TD_KEY},timeout=15)
    d=r.json()
    if d.get("status")=="error":
        raise HTTPException(502,d.get("message","Twelve Data error"))
    vals=list(reversed(d.get("values",[])))
    candles=[{"time":v["datetime"],"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),
              "close":float(v["close"]),"volume":float(v.get("volume") or 0)} for v in vals]
    tech=technical_score(candles)
    record("twelvedata",True)
    risk=risk_shield(symbol,q=q,score=tech["score"])
    # Sanity check: quote must be in a plausible envelope around recent candles.
    sync_ok=True
    if candles and q.get("price"):
        lo=min(c["low"] for c in candles); hi=max(c["high"] for c in candles); px=float(q["price"])
        sync_ok=(lo*.70 <= px <= hi*1.30)
    if not sync_ok:
        risk={"status":"RED","blocked":True,"decision":"NO TRADE",
              "gates":risk.get("gates",[])+[{"name":"symbol_sync","status":"RED","detail":"Precio y velas no coinciden"}]}
    return {"symbol":symbol,"quote":q,"candles":candles,"technical":tech,"risk":risk,
            "sync_ok":sync_ok,"interval":interval,"provider_chart":"Twelve Data"}

@app.get("/api/data-health-ui")
def data_health_ui():
    """UI-friendly health: a closed stock market is IDLE/CLOSED, not a false outage."""
    snap=data_hub()
    clock=market_clock()
    providers=snap["providers"]
    stock_state=providers.get("alpaca",{}).get("status","DOWN")
    if not clock["open"] and stock_state=="DOWN" and WS["stocks"].get("connected"):
        stock_state="MARKET CLOSED"
    return {"overall":snap["overall"],"stocks":stock_state,
            "crypto":providers.get("crypto",{}).get("status","DOWN"),
            "twelvedata":providers.get("twelvedata",{}).get("status","DOWN"),
            "alphavantage":providers.get("alphavantage",{}).get("status","DOWN"),
            "stock_session":clock["session"],"ws":WS}

@app.get("/api/system")
def system():
    return {"version":VERSION,"mode":"PAPER/SHADOW","live_trading":False,"kill_switch":True,
            "markets":["ACCIONES","CRIPTO"],"stocks":M7,"crypto":CRYPTO}
