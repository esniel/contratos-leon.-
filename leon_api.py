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
VERSION = "57.2"
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
ALPACA_OPTIONS_FEED = os.getenv("ALPACA_OPTIONS_FEED","indicative").strip().lower() or "indicative"

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
PAPER={"starting_cash":10000.0,"cash":10000.0,"realized":0.0,"positions":[],"trades":[],"daily_loss_limit_pct":3.0,"max_position_pct":10.0}
ROBOT={"enabled":True,"mode":"PAPER","state":"WAIT","market":"AUTO","selected_market":"NO TRADE",
       "symbol":None,"reason":"Esperando datos confiables","updated_at":None,"profit_lock_stage":0,
       "cycles":0,"last_cycle_at":None,"last_cycle_secs":None,"last_decision":None}

# --- Robot automático: configuración (todo por variables de entorno) ---
ROBOT_INTERVAL_SEC=max(30,int(os.getenv("ROBOT_INTERVAL_SEC","180")))
CONSENSUS_MIN_SCORE=int(os.getenv("CONSENSUS_MIN_SCORE","70"))
MAX_OPEN_POSITIONS=max(1,int(os.getenv("MAX_OPEN_POSITIONS","1")))
RISK_PER_TRADE_PCT=float(os.getenv("RISK_PER_TRADE_PCT","1.0"))
TRAIL_PCT=float(os.getenv("TRAIL_PCT","1.5"))/100.0
TIME_EXIT_MIN=int(os.getenv("TIME_EXIT_MIN","120"))

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
    global _robot_loop_started
    if ALPACA_KEY and ALPACA_SECRET:
        asyncio.create_task(ws_stocks()); asyncio.create_task(ws_crypto())
    # El robot automático corre mientras el proceso viva (PAPER/SHADOW).
    # En Render Free el proceso puede dormir: no se afirma autonomía 24/7.
    if not _robot_loop_started:
        _robot_loop_started=True
        asyncio.create_task(_robot_loop())

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

def _fetch_candles(symbol, interval="15min", outputsize=120):
    """Velas vía Twelve Data. Devuelve None si no hay proveedor; lanza 502 si el proveedor falla."""
    if not TD_KEY:
        return None
    r=requests.get("https://api.twelvedata.com/time_series",
        params={"symbol":symbol,"interval":interval,"outputsize":min(max(outputsize,30),500),"apikey":TD_KEY},timeout=15)
    d=r.json()
    if d.get("status")=="error":
        raise HTTPException(502,d.get("message","Twelve Data error"))
    vals=list(reversed(d.get("values",[])))
    candles=[{"time":v["datetime"],"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),
              "close":float(v["close"]),"volume":float(v.get("volume") or 0)} for v in vals]
    record("twelvedata",True)
    return candles

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
    # El loop del robot es dueño del estado; el selector solo informa mercado/símbolo.
    # No pisa ACTIVE/PROFIT LOCK/EXIT mientras hay posiciones abiertas.
    ROBOT.update({"selected_market":selected,"symbol":best["symbol"] if best else None,
                  "reason":"Selección objetiva por frescura/spread/sesión; no es garantía de beneficio.","updated_at":now()})
    return {"selected_market":selected,"best":best,"stocks":stocks,"crypto":cryptos,"robot":ROBOT}

@app.get("/api/robot/status")
def robot_status(): return ROBOT
@app.post("/api/robot/toggle")
async def robot_toggle(request:Request):
    d=await request.json(); ROBOT["enabled"]=bool(d.get("enabled",True)); ROBOT["updated_at"]=now()
    return ROBOT



def _occ_parts(contract_symbol):
    """Parse a standard OCC option symbol into basic metadata."""
    m=re.match(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$", str(contract_symbol or ""))
    if not m: return {}
    root, ymd, cp, strike=m.groups()
    try:
        exp=datetime.strptime(ymd,"%y%m%d").date()
        dte=max(0,(exp-datetime.now(NY).date()).days)
    except Exception:
        exp=None; dte=None
    return {"root":root,"expiration":exp.isoformat() if exp else None,"dte":dte,"type":"CALL" if cp=="C" else "PUT","strike":int(strike)/1000}

def option_opportunity_scan(symbol, side="auto", limit=6):
    """Rank actual Alpaca option snapshots by quote quality + delta fit + activity.
    Score is a quality/ranking score, NOT probability of profit.
    """
    symbol=str(symbol or "").upper().replace("/","")
    if not symbol or symbol in {x.replace('/','') for x in CRYPTO}:
        return {"symbol":symbol,"status":"UNAVAILABLE","reason":"Las opciones se escanean sobre acciones, no cripto.","contracts":[]}
    if not (ALPACA_KEY and ALPACA_SECRET):
        return {"symbol":symbol,"status":"UNAVAILABLE","reason":"Alpaca no está configurado.","contracts":[]}
    try:
        uq=quote(symbol); underlying=float(uq.get("price") or 0)
    except Exception as e:
        return {"symbol":symbol,"status":"UNAVAILABLE","reason":f"Sin precio del subyacente: {e}","contracts":[]}
    if underlying<=0:
        return {"symbol":symbol,"status":"UNAVAILABLE","reason":"Precio del subyacente no disponible.","contracts":[]}
    today=datetime.now(NY).date(); end=today+timedelta(days=30)
    params={"feed":ALPACA_OPTIONS_FEED,"limit":350,"expiration_date_gte":today.isoformat(),"expiration_date_lte":end.isoformat(),
            "strike_price_gte":round(underlying*.88,2),"strike_price_lte":round(underlying*1.12,2)}
    side=(side or "auto").lower()
    if side in ("call","put"): params["type"]=side
    try:
        r=requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}",headers=headers(),params=params,timeout=14)
        if r.status_code>=400:
            msg=(r.json().get("message") if 'application/json' in r.headers.get('content-type','') else r.text[:160])
            return {"symbol":symbol,"status":"UNAVAILABLE","reason":f"Options data {r.status_code}: {msg}","feed":ALPACA_OPTIONS_FEED,"contracts":[]}
        snaps=(r.json().get("snapshots") or {})
    except Exception as e:
        return {"symbol":symbol,"status":"UNAVAILABLE","reason":f"Error options data: {e}","feed":ALPACA_OPTIONS_FEED,"contracts":[]}
    rows=[]
    for cs,snap in snaps.items():
        meta=_occ_parts(cs); q=snap.get("latestQuote") or snap.get("latest_quote") or {}
        bid=float(q.get("bp") or q.get("bid_price") or 0); ask=float(q.get("ap") or q.get("ask_price") or 0)
        if bid<=0 or ask<=0 or ask<bid: continue
        mid=(bid+ask)/2; spread_pct=(ask-bid)/mid*100 if mid else 999
        if spread_pct>35: continue
        g=snap.get("greeks") or {}; delta=g.get("delta")
        try: delta=float(delta)
        except: delta=None
        bar=snap.get("dailyBar") or snap.get("daily_bar") or {}; volume=float(bar.get("v") or bar.get("volume") or 0)
        dte=meta.get("dte") if meta else None
        # Quality score: tighter spread, usable delta, some activity, reasonable DTE. Not win probability.
        score=100.0
        score-=min(60.0, spread_pct*2.5)
        if delta is not None:
            score-=min(22.0, abs(abs(delta)-0.45)*55)
        else:
            score-=12
        score+=min(10.0, math.log10(volume+1)*2.5)
        if dte is not None:
            score+=max(0,8-abs(dte-10)*0.45)
        score=max(0,min(100,score))
        rows.append({"contract":cs,"type":meta.get("type") if meta else None,"strike":meta.get("strike") if meta else None,
                     "expiration":meta.get("expiration") if meta else None,"dte":dte,"bid":round(bid,2),"ask":round(ask,2),
                     "mid":round(mid,2),"spread_pct":round(spread_pct,1),"delta":round(delta,3) if delta is not None else None,
                     "iv":round(float(snap.get("impliedVolatility") or snap.get("implied_volatility") or 0),4) or None,
                     "volume":int(volume),"quality_score":round(score,1)})
    rows.sort(key=lambda x:(x["quality_score"],x["volume"]),reverse=True)
    rows=rows[:max(1,min(int(limit),12))]
    return {"symbol":symbol,"underlying_price":round(underlying,2),"status":"OK" if rows else "NO_DATA",
            "feed":ALPACA_OPTIONS_FEED,"feed_note":"indicative puede ser retrasado/modificado" if ALPACA_OPTIONS_FEED=="indicative" else "OPRA según suscripción",
            "ranking_note":"quality_score mide calidad relativa (spread/delta/actividad/DTE), no probabilidad de ganar.","contracts":rows}

@app.get("/api/options/opportunities")
def options_opportunities(symbol:str="", side:str="auto", limit:int=6):
    if not symbol:
        ms=market_selector(); stocks=sorted(ms.get("stocks") or [], key=lambda x:x.get("score",0), reverse=True)
        symbol=stocks[0]["symbol"] if stocks else ""
    return option_opportunity_scan(symbol,side,limit)

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
    cost=premium*100*qty*1.01 + .65*qty
    equity=PAPER["cash"]+sum(p["entry_cost"] for p in PAPER["positions"])
    if cost>equity*(PAPER["max_position_pct"]/100): raise HTTPException(400,"Risk Shield: posición >10% del equity")
    if cost>PAPER["cash"]: raise HTTPException(400,"Cash insuficiente")
    pos={"position_id":f"P{int(now()*1000)}","symbol":symbol,"side":side,"qty":qty,"entry_premium":premium,
         "entry_cost":round(cost,2),"market_value":round(cost,2),"opened_at":now(),"state":"ACTIVE","profit_lock_stage":0}
    PAPER["cash"]-=cost; PAPER["positions"].append(pos); PAPER["trades"].append({"event":"PAPER ENTRY ✓",**pos})
    ROBOT.update({"state":"ACTIVE","symbol":symbol,"reason":"PAPER ENTRY ✓","updated_at":now()})
    return {"ok":True,"position":pos,"paper":True}

@app.post("/api/paper/options/close")
async def paper_close(request:Request):
    d=await request.json(); pid=d.get("position_id"); premium=float(d.get("premium",0))
    pos=next((p for p in PAPER["positions"] if p["position_id"]==pid),None)
    if not pos: raise HTTPException(404,"Posición no encontrada")
    if premium<=0: raise HTTPException(409,"Contrato sin precio actual; no se inventa precio de salida.")
    proceeds=premium*100*pos["qty"]*.99-.65*pos["qty"]; pnl=proceeds-pos["entry_cost"]
    PAPER["cash"]+=proceeds; PAPER["realized"]+=pnl; PAPER["positions"].remove(pos)
    PAPER["trades"].append({"event":"EXIT","position_id":pid,"pnl":round(pnl,2),"closed_at":now()})
    ROBOT.update({"state":"EXIT","reason":f"Paper exit P/L ${pnl:.2f}","updated_at":now()})
    return {"ok":True,"pnl":round(pnl,2),"realized":round(PAPER["realized"],2)}

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
            "stages":["ENTRY","INITIAL STOP","TP1","PROFIT LOCK","TP2","TRAILING","TP3/EXIT"],
            "note":"v52: gestor adaptativo en PAPER/SHADOW. Live Trading desactivado."}

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
    candles=_fetch_candles(symbol,interval,outputsize)
    if candles is None:
        return {"symbol":symbol,"quote":q,"candles":[],"technical":{"score":50,"trend":"NEUTRAL","rsi":None,
                "support":None,"resistance":None},"risk":risk_shield(symbol,q=q,score=50),
                "sync_ok":True,"warning":"Sin TWELVE_DATA_API_KEY: niveles no disponibles"}
    tech=technical_score(candles)
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

# =====================================================================
# ROBOT AUTOMÁTICO — loop: DATOS -> SCANNER -> ANÁLISIS -> CONSENSUS ->
# RISK SHIELD -> PAPER TRADE -> ADAPTIVE MANAGER -> JOURNAL -> MÉTRICAS
# PAPER/SHADOW únicamente. Live trading bloqueado. Si no hay una
# oportunidad suficientemente buena: WAIT / NO TRADE.
# =====================================================================

def _journal(decision, symbol, reason):
    entry={"at":now(),"at_et":datetime.now(NY).isoformat(timespec="seconds"),
           "decision":decision,"symbol":symbol,"reason":str(reason)[:220]}
    DECISIONS.insert(0,entry); del DECISIONS[200:]
    ROBOT["last_decision"]={"decision":decision,"symbol":symbol,
                            "reason":str(reason)[:220],"at":now()}

def _today_et():
    return datetime.now(NY).date().isoformat()

def _daily_realized():
    today=_today_et(); total=0.0
    for t in PAPER["trades"]:
        if t.get("event")=="EXIT" and t.get("closed_at"):
            try:
                d=datetime.fromtimestamp(t["closed_at"],tz=NY).date().isoformat()
            except Exception:
                continue
            if d==today:
                total+=float(t.get("pnl") or 0)
    return round(total,2)

def _daily_loss_hit():
    limit=PAPER["starting_cash"]*(PAPER["daily_loss_limit_pct"]/100.0)
    return _daily_realized()<=-limit

def _equity_now():
    return PAPER["cash"]+sum(float(p.get("market_value",p.get("entry_cost",0))) for p in PAPER["positions"])

def _paper_spot_open(symbol, q, tech, risk):
    """Abre posición PAPER sobre el subyacente (solo LONG en v1). Devuelve la posición o None."""
    price=float(q.get("price") or 0)
    if price<=0:
        _journal("WAIT",symbol,"Precio inválido: sin entrada"); return None
    if tech.get("trend")!="BULL":
        _journal("WAIT",symbol,f"Tendencia {tech.get('trend')}: v1 solo opera LONG"); return None
    support=tech.get("support")
    structural=support and support<price
    stop=round(float(support),4) if structural else round(price*0.99,4)
    risk_per_share=price-stop
    if risk_per_share<=0:
        _journal("WAIT",symbol,"Stop inválido: sin entrada"); return None
    equity=_equity_now()
    risk_amount=equity*(RISK_PER_TRADE_PCT/100.0)
    qty=risk_amount/risk_per_share
    qty=min(qty,(equity*(PAPER["max_position_pct"]/100.0))/price)
    qty=round(qty,6) if "/" in symbol else math.floor(qty)
    if qty<=0:
        _journal("WAIT",symbol,"Sizing da 0 unidades: sin entrada"); return None
    cost=round(qty*price,2)
    if cost>equity*(PAPER["max_position_pct"]/100.0):
        _journal("WAIT",symbol,"Risk Shield: posición >10% del equity"); return None
    if cost>PAPER["cash"]:
        _journal("WAIT",symbol,"Cash insuficiente"); return None
    pos={"position_id":f"S{int(now()*1000)}","kind":"SPOT","symbol":symbol,"side":"LONG",
         "qty":qty,"entry_price":round(price,4),"entry_cost":cost,
         "stop":stop,"initial_stop":stop,
         "tp1":round(price+1.5*risk_per_share,4),"tp2":round(price+2.5*risk_per_share,4),
         "tp3":round(price+4.0*risk_per_share,4),
         "highest":round(price,4),"profit_lock_stage":0,"opened_at":now(),
         "state":"ACTIVE","market_value":cost,"unrealized":0.0,
         "tech_score":tech.get("score"),"risk_status":risk.get("status"),
         "basis":"soporte estructural" if structural else "stop 1% (fallback)"}
    PAPER["cash"]=round(PAPER["cash"]-cost,2)
    PAPER["positions"].append(pos)
    PAPER["trades"].append({"event":"PAPER ENTRY","kind":"SPOT","position_id":pos["position_id"],
                            "symbol":symbol,"side":"LONG","qty":qty,"entry_price":pos["entry_price"],
                            "entry_cost":cost,"stop":stop,"tp1":pos["tp1"],"tp2":pos["tp2"],"tp3":pos["tp3"],
                            "tech_score":tech.get("score"),"risk_status":risk.get("status"),
                            "basis":pos["basis"],"opened_at":pos["opened_at"]})
    return pos

def _paper_spot_close(pos, price, reason):
    price=float(price)
    proceeds=round(pos["qty"]*price,2)
    pnl=round(proceeds-pos["entry_cost"],2)
    PAPER["cash"]=round(PAPER["cash"]+proceeds,2)
    PAPER["realized"]=round(PAPER["realized"]+pnl,2)
    try: PAPER["positions"].remove(pos)
    except ValueError: pass
    PAPER["trades"].append({"event":"EXIT","kind":"SPOT","position_id":pos["position_id"],
                            "symbol":pos["symbol"],"side":pos["side"],"qty":pos["qty"],
                            "entry_price":pos["entry_price"],"exit_price":round(price,4),
                            "pnl":pnl,"reason":reason,"closed_at":now(),
                            "duration_min":round((now()-pos["opened_at"])/60,1)})
    _journal("EXIT",pos["symbol"],f"{reason} · P/L ${pnl:+.2f}")
    return pnl

def _manage_positions():
    """Adaptive Trade Manager: stop, profit lock, trailing, TP3 y time exit. Devuelve True si cerró algo."""
    closed_any=False
    for pos in list(PAPER["positions"]):
        if pos.get("kind","OPTION")!="SPOT" or pos.get("state") not in ("ACTIVE","PROFIT LOCK","STALE"):
            continue
        symbol=pos["symbol"]
        try:
            q=quote(symbol)
        except Exception as e:
            pos["state"]="STALE"; _journal("WAIT",symbol,f"Sin quote para gestionar: {e}"); continue
        price=float(q.get("price") or 0)
        age=now()-(q.get("market_time") or q.get("received_at") or 0)
        if price<=0 or age>90:
            pos["state"]="STALE"; _journal("WAIT",symbol,"Dato viejo: gestión pausada, no se opera a ciegas"); continue
        if pos.get("state")=="STALE":
            pos["state"]="PROFIT LOCK" if pos.get("profit_lock_stage",0)>0 else "ACTIVE"
        pos["highest"]=max(pos.get("highest",price),price)
        pos["market_value"]=round(pos["qty"]*price,2)
        pos["unrealized"]=round(pos["market_value"]-pos["entry_cost"],2)
        if price<=pos["stop"]:
            _paper_spot_close(pos,price,"STOP alcanzado"); closed_any=True; continue
        stage=pos.get("profit_lock_stage",0)
        if stage==0 and price>=pos["tp1"]:
            pos["stop"]=max(pos["stop"],pos["entry_price"]); pos["profit_lock_stage"]=1; pos["state"]="PROFIT LOCK"
            _journal("PROFIT LOCK",symbol,f"TP1 alcanzado: stop a breakeven ${pos['stop']:.4f}")
        elif stage>=1:
            trail=round(pos["highest"]*(1-TRAIL_PCT),4)
            if trail>pos["stop"]:
                pos["stop"]=trail; pos["profit_lock_stage"]=2; pos["state"]="PROFIT LOCK"
                _journal("PROFIT LOCK",symbol,f"Trailing stop en ${pos['stop']:.4f}")
        if price>=pos["tp3"]:
            _paper_spot_close(pos,price,"TP3 alcanzado"); closed_any=True; continue
        if now()-pos["opened_at"]>TIME_EXIT_MIN*60:
            _paper_spot_close(pos,price,f"TIME EXIT ({TIME_EXIT_MIN} min)"); closed_any=True; continue
    return closed_any

_cycle_running=False

async def robot_cycle():
    """Un ciclo completo del robot. Nunca revienta: todo fallo queda en el journal."""
    global _cycle_running
    if _cycle_running:
        return
    _cycle_running=True
    t0=now()
    try:
        ROBOT["cycles"]=ROBOT.get("cycles",0)+1
        if not ROBOT.get("enabled",True):
            ROBOT.update({"state":"PAUSED","reason":"Robot pausado por el usuario","updated_at":now()})
            _journal("WAIT",None,"Robot pausado"); return
        # 1) Gestionar posiciones abiertas
        closed_any=_manage_positions()
        open_spots=[p for p in PAPER["positions"]
                    if p.get("kind","OPTION")=="SPOT" and p.get("state") in ("ACTIVE","PROFIT LOCK","STALE")]
        if open_spots:
            locked=any(p.get("profit_lock_stage",0)>0 for p in open_spots)
            ROBOT.update({"state":"PROFIT LOCK" if locked else "ACTIVE","symbol":open_spots[0]["symbol"],
                          "reason":"Gestionando posición paper abierta","updated_at":now()})
            if len(open_spots)>=MAX_OPEN_POSITIONS:
                _journal("WAIT",open_spots[0]["symbol"],"Posición en gestión; sin nuevas entradas"); return
        if closed_any:
            ROBOT.update({"state":"EXIT","reason":"Posición cerrada; reevaluando próximo ciclo","updated_at":now()})
            _journal("WAIT",None,"Cierre ejecutado este ciclo; próxima evaluación en el siguiente"); return
        # 2) Límite de pérdida diaria
        if _daily_loss_hit():
            ROBOT.update({"state":"BLOCKED","selected_market":"NO TRADE","symbol":None,
                          "reason":f"Límite de pérdida diaria alcanzado ({PAPER['daily_loss_limit_pct']}%)",
                          "updated_at":now()})
            _journal("NO TRADE",None,"Risk Shield: límite de pérdida diaria"); return
        # 3) Scanner
        ms=market_selector()
        if ms["selected_market"]=="NO TRADE" or not ms.get("best"):
            ROBOT.update({"state":"WAIT","reason":"Sin oportunidades con datos suficientes","updated_at":now()})
            _journal("WAIT",None,"Scanner: NO TRADE"); return
        symbol=ms["best"]["symbol"]
        # 4) Análisis técnico (requiere velas reales)
        try:
            candles=_fetch_candles(symbol,"15min",120)
        except Exception:
            candles=None
        if not candles or len(candles)<30:
            ROBOT.update({"state":"WAIT","symbol":symbol,
                          "reason":"Sin velas suficientes para análisis técnico","updated_at":now()})
            _journal("WAIT",symbol,"Sin datos técnicos suficientes: NO TRADE"); return
        tech=technical_score(candles)
        # 5) Consensus
        if tech["trend"]!="BULL" or tech["score"]<CONSENSUS_MIN_SCORE:
            ROBOT.update({"state":"WAIT","symbol":symbol,
                          "reason":f"Sin consenso: tendencia {tech['trend']}, score {tech['score']}",
                          "updated_at":now()})
            _journal("WAIT",symbol,f"Score {tech['score']}/umbral {CONSENSUS_MIN_SCORE}, tendencia {tech['trend']}"); return
        # 6) Risk Shield
        try:
            q=quote(symbol)
        except Exception as e:
            ROBOT.update({"state":"WAIT","symbol":symbol,
                          "reason":f"Sin quote confiable: {e}","updated_at":now()})
            _journal("NO TRADE",symbol,f"Sin precio confiable: {e}"); return
        risk=risk_shield(symbol,q=q,score=tech["score"])
        if risk["blocked"] or risk["status"]!="GREEN":
            bad=[g["name"] for g in risk["gates"] if g["status"]!="GREEN"]
            ROBOT.update({"state":"WAIT","symbol":symbol,
                          "reason":"Risk Shield "+risk["status"]+": "+",".join(bad),"updated_at":now()})
            _journal("NO TRADE" if risk["blocked"] else "WAIT",symbol,f"Risk Shield {risk['status']}: {','.join(bad)}"); return
        # 7) ARMED -> entrada PAPER
        ROBOT.update({"state":"ARMED","symbol":symbol,
                      "reason":f"Señal armada: score {tech['score']}, Risk Shield GREEN","updated_at":now()})
        _journal("ARMED",symbol,f"Score {tech['score']} + Risk Shield GREEN: activando PAPER")
        pos=_paper_spot_open(symbol,q,tech,risk)
        if pos:
            ROBOT.update({"state":"ACTIVE","symbol":symbol,
                          "reason":f"PAPER ENTRY: {pos['qty']} x {symbol} @ ${pos['entry_price']}",
                          "updated_at":now()})
            _journal("ACTIVE",symbol,f"PAPER ENTRY {pos['qty']} @ ${pos['entry_price']} · stop ${pos['stop']}")
        else:
            ROBOT.update({"state":"WAIT","symbol":symbol,
                          "reason":"No se pudo dimensionar la posición","updated_at":now()})
    except Exception as e:
        _journal("ERROR",None,str(e)[:200])
        ROBOT.update({"reason":f"Error en ciclo: {str(e)[:120]}","updated_at":now()})
    finally:
        ROBOT["last_cycle_at"]=now()
        ROBOT["last_cycle_secs"]=round(now()-t0,2)
        _cycle_running=False

_robot_loop_started=False

async def _robot_loop():
    """Tarea de fondo: ejecuta robot_cycle cada ROBOT_INTERVAL_SEC mientras el proceso viva."""
    await asyncio.sleep(10)
    while True:
        try:
            await robot_cycle()
        except Exception as e:
            _journal("ERROR",None,f"loop: {e}"[:200])
        await asyncio.sleep(max(30,ROBOT_INTERVAL_SEC))

@app.get("/api/robot/decisions")
def robot_decisions(limit:int=30):
    return {"decisions":DECISIONS[:max(1,min(limit,200))],"cycles":ROBOT.get("cycles",0),
            "interval_sec":ROBOT_INTERVAL_SEC,"paper_only":True}

@app.get("/api/metrics")
def metrics():
    closed=[t for t in PAPER["trades"]
            if t.get("event")=="EXIT" and isinstance(t.get("pnl"),(int,float))]
    n=len(closed)
    base={"sample_size":n,"paper":True,
          "note":"Métricas paper. El score interno nunca es probabilidad de ganar."}
    if n==0:
        return {**base,"detail":"Sin operaciones cerradas todavía. Las métricas necesitan muestra."}
    wins=[t for t in closed if t["pnl"]>0]; losses=[t for t in closed if t["pnl"]<=0]
    gross_win=sum(t["pnl"] for t in wins); gross_loss=-sum(t["pnl"] for t in losses)
    eq=PAPER["starting_cash"]; peak=eq; mdd=0.0
    for t in closed:
        eq+=t["pnl"]; peak=max(peak,eq); mdd=max(mdd,peak-eq)
    return {**base,
            "win_rate":round(len(wins)/n*100,1),
            "profit_factor":round(gross_win/gross_loss,2) if gross_loss>0 else None,
            "expectancy":round(sum(t["pnl"] for t in closed)/n,2),
            "avg_win":round(gross_win/len(wins),2) if wins else 0,
            "avg_loss":round(-gross_loss/len(losses),2) if losses else 0,
            "max_drawdown":round(mdd,2),
            "realized":round(PAPER["realized"],2),
            "daily_realized":_daily_realized(),
            "daily_loss_limit_pct":PAPER["daily_loss_limit_pct"]}
