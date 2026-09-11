
import os, math, re, statistics, requests, time, asyncio, json
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo
try:
    import websockets
except ImportError:
    websockets=None

app = FastAPI(title="Contratos León Real Data API", version="51.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET","POST"], allow_headers=["*"])

AV_KEY=os.getenv("ALPHAVANTAGE_API_KEY","").strip()
TD_KEY=os.getenv("TWELVE_DATA_API_KEY","").strip()
ALPACA_KEY=os.getenv("ALPACA_KEY_ID","").strip()
ALPACA_SECRET=os.getenv("ALPACA_SECRET_KEY","").strip()
ALPACA_DATA_BASE="https://data.alpaca.markets"
ALPACA_PAPER_BASE="https://paper-api.alpaca.markets"
ALPACA_WS_URL=os.getenv("ALPACA_WS_URL","wss://stream.data.alpaca.markets/v2/iex").strip()

AV_DAILY_LIMIT=25
_AV_USAGE={"date":None,"used":0,"limit_hit":False}
NY_TZ=ZoneInfo("America/New_York")

def alpha_usage_state():
    now=datetime.now(NY_TZ)
    day=now.date().isoformat()
    if _AV_USAGE["date"]!=day:
        _AV_USAGE.update({"date":day,"used":0,"limit_hit":False})
    tomorrow=datetime.combine(now.date()+timedelta(days=1), datetime.min.time(), tzinfo=NY_TZ)
    used=min(AV_DAILY_LIMIT,int(_AV_USAGE["used"]))
    return {
        "date":day,
        "limit":AV_DAILY_LIMIT,
        "used":used,
        "remaining":max(0,AV_DAILY_LIMIT-used),
        "limit_hit":bool(_AV_USAGE["limit_hit"]),
        "resets_at":tomorrow.isoformat(),
        "timezone":"America/New_York",
        "note":"Contador de llamadas Alpha hechas por esta instancia. Si Render reinicia, Alpha Vantage sigue aplicando su límite real."
    }

MAGNIFICENT_7=["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA"]
_M7_CACHE={"ts":0,"data":None}

# Feriados NYSE. Lista fija — hay que actualizarla cada año (no hay API de calendario integrada todavía).
NYSE_HOLIDAYS={
    date(2026,1,1), date(2026,1,19), date(2026,2,16), date(2026,4,3), date(2026,5,25),
    date(2026,6,19), date(2026,7,3), date(2026,9,7), date(2026,11,26), date(2026,12,25),
    date(2027,1,1), date(2027,1,18), date(2027,2,15), date(2027,3,26), date(2027,5,31),
    date(2027,6,18), date(2027,7,5), date(2027,9,6), date(2027,11,25), date(2027,12,24),
}

# --- Data Hub: salud y frescura por proveedor -------------------------
_PROVIDER_HEALTH={
    "alpaca":{"last_ok":None,"last_latency_ms":None,"last_error":None,"last_data_time":None},
    "twelvedata":{"last_ok":None,"last_latency_ms":None,"last_error":None,"last_data_time":None},
    "alphavantage":{"last_ok":None,"last_latency_ms":None,"last_error":None,"last_data_time":None},
}
DATA_HUB_DELAYED_AFTER_S=20   # más de esto = DELAYED
DATA_HUB_DOWN_AFTER_S=90      # más de esto (o error) = DOWN

def _parse_iso_epoch(ts):
    """Convierte un timestamp ISO8601 (con o sin nanosegundos/Z) a epoch. None si no se puede."""
    if not ts: return None
    try:
        s=str(ts).strip()
        if s.endswith("Z"): s=s[:-1]+"+00:00"
        s=re.sub(r'(\.\d{6})\d+', r'\1', s)     # recorta nanosegundos a microsegundos
        if "+" not in s and "T" in s: s+="+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def _record_health(name, ok, latency_ms=None, error=None, data_time=None):
    h=_PROVIDER_HEALTH.setdefault(name,{})
    if ok:
        h["last_ok"]=time.time()
        h["last_latency_ms"]=latency_ms
        h["last_error"]=None
        if data_time is not None:
            h["last_data_time"]=data_time
    else:
        h["last_error"]=str(error) if error else "error desconocido"

def _provider_status(name):
    h=_PROVIDER_HEALTH.get(name,{})
    last_ok=h.get("last_ok")
    if last_ok is None:
        return "DOWN", None, False
    now=time.time()
    call_age=now-last_ok
    data_time=h.get("last_data_time")
    has_real_data_age = data_time is not None
    age = (now-data_time) if has_real_data_age else call_age
    if h.get("last_error") and call_age>10:
        return "DOWN", age, has_real_data_age
    if age <= DATA_HUB_DELAYED_AFTER_S:
        return "LIVE", age, has_real_data_age
    if age <= DATA_HUB_DOWN_AFTER_S:
        return "DELAYED", age, has_real_data_age
    return "DOWN", age, has_real_data_age

def data_hub_snapshot():
    providers={}
    for name in _PROVIDER_HEALTH:
        status,age,real_data_age=_provider_status(name)
        providers[name]={
            "status":status,
            "age_seconds":round(age,1) if age is not None else None,
            "age_is_real_market_time":real_data_age,
            "latency_ms":_PROVIDER_HEALTH[name].get("last_latency_ms"),
            "last_error":_PROVIDER_HEALTH[name].get("last_error"),
        }
        if name=="alpaca":
            providers[name]["ws_connected"]=_WS_STATE.get("connected", False)
    any_live=any(p["status"]=="LIVE" for p in providers.values())
    all_down=all(p["status"]=="DOWN" for p in providers.values())
    if all_down:
        overall="DOWN"
    elif any_live:
        overall="LIVE"
    else:
        overall="DELAYED"
    return {
        "overall":overall,
        "safe_to_trade": overall!="DOWN",
        "providers":providers,
        "note":"Si el overall es DOWN, ningún proveedor respondió a tiempo: LEONIX debe forzar NO TRADE en vez de usar datos viejos. Cuando age_is_real_market_time es false, la antigüedad se mide desde la última respuesta exitosa del servidor, no desde la hora real del dato de mercado (Twelve Data y Alpha Vantage no exponen esa hora en estos endpoints)."
    }

def alpaca_headers():
    return {"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET}

def alpaca_get(base, path, params=None):
    if not (ALPACA_KEY and ALPACA_SECRET):
        raise HTTPException(503,"ALPACA_KEY_ID / ALPACA_SECRET_KEY no configuradas")
    t0=time.time()
    try:
        r=requests.get(f"{base}{path}", headers=alpaca_headers(), params=params or {}, timeout=15)
        latency=round((time.time()-t0)*1000,1)
        if r.status_code>=400:
            _record_health("alpaca", False, error=f"HTTP {r.status_code}")
            raise HTTPException(r.status_code, f"Alpaca error: {r.text[:200]}")
        _record_health("alpaca", True, latency_ms=latency)
        return r.json()
    except HTTPException:
        raise
    except Exception as e:
        _record_health("alpaca", False, error=e)
        raise HTTPException(502, f"Alpaca no disponible: {e}")

def alpaca_quote(symbol):
    d=alpaca_get(ALPACA_DATA_BASE, f"/v2/stocks/{symbol}/quotes/latest")
    q=d.get("quote",{})
    bid=f(q.get("bp"),0); ask=f(q.get("ap"),0)
    price=(bid+ask)/2 if bid and ask else f(q.get("ap") or q.get("bp"),0)
    if not price:
        raise HTTPException(404,"Alpaca sin cotización")
    market_time=_parse_iso_epoch(q.get("t"))
    if market_time is not None:
        _record_health("alpaca", True, data_time=market_time)
    return {"symbol":symbol,"price":price,"bid":bid,"ask":ask,"change_pct":None,"provider":"Alpaca",
            "market_time":market_time,"market_time_iso":q.get("t")}

# --- v51: Alpaca WebSocket -> caché LIVE en memoria ---------------------
# Diseño: una sola tarea asyncio de fondo, dentro del mismo proceso uvicorn
# (no un worker aparte). Se lanza en el evento startup de FastAPI SOLO si
# hay credenciales de Alpaca. Reconecta con backoff exponencial si se cae.
# El caché (_LIVE_CACHE) es lo que consulta quote() primero; si no hay dato
# fresco ahí, cae a REST (Alpaca -> Twelve Data -> Alpha Vantage), igual
# que antes. En el plan free de Render, si el servicio duerme por
# inactividad, la tarea se reinicia sola al despertar (ver evento startup).
_LIVE_CACHE={}
_WS_STATE={"connected":False,"last_message_at":None,"reconnects":0,"last_error":None,"started":False}
WS_MAX_BACKOFF_S=60

def _live_quote(symbol):
    c=_LIVE_CACHE.get(symbol)
    if not c: return None
    ref=c.get("market_time") or c.get("received_at")
    if ref is None: return None
    if time.time()-ref > DATA_HUB_DELAYED_AFTER_S:
        return None
    return c

async def alpaca_ws_loop():
    if websockets is None:
        _WS_STATE["last_error"]="librería websockets no instalada"
        return
    backoff=2
    while True:
        try:
            async with websockets.connect(ALPACA_WS_URL, ping_interval=15, ping_timeout=10) as ws:
                await ws.send(json.dumps({"action":"auth","key":ALPACA_KEY,"secret":ALPACA_SECRET}))
                await ws.recv()  # respuesta de auth; no bloqueamos el loop si falla, el próximo mensaje lo revela
                await ws.send(json.dumps({"action":"subscribe","quotes":MAGNIFICENT_7}))
                _WS_STATE["connected"]=True
                _WS_STATE["last_error"]=None
                backoff=2
                async for raw in ws:
                    try:
                        msgs=json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(msgs, list): msgs=[msgs]
                    for m in msgs:
                        t=m.get("T")
                        if t=="q":
                            sym=m.get("S")
                            bid=f(m.get("bp"),0); ask=f(m.get("ap"),0)
                            price=(bid+ask)/2 if bid and ask else (ask or bid)
                            if not sym or not price: continue
                            mt=_parse_iso_epoch(m.get("t"))
                            _LIVE_CACHE[sym]={"symbol":sym,"price":price,"bid":bid,"ask":ask,
                                               "market_time":mt,"market_time_iso":m.get("t"),
                                               "received_at":time.time(),"provider":"Alpaca WS"}
                            _WS_STATE["last_message_at"]=time.time()
                            if mt is not None:
                                _record_health("alpaca", True, data_time=mt)
                        elif t=="error":
                            _WS_STATE["last_error"]=m.get("msg") or str(m)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _WS_STATE["connected"]=False
            _WS_STATE["last_error"]=str(e)
            _WS_STATE["reconnects"]+=1
            await asyncio.sleep(min(backoff, WS_MAX_BACKOFF_S))
            backoff=min(backoff*2, WS_MAX_BACKOFF_S)

@app.on_event("startup")
async def _leonix_startup():
    if ALPACA_KEY and ALPACA_SECRET and not _WS_STATE["started"]:
        _WS_STATE["started"]=True
        asyncio.create_task(alpaca_ws_loop())

@app.get("/api/live/status")
def live_status():
    return {"configured":bool(ALPACA_KEY and ALPACA_SECRET),
            "connected":_WS_STATE["connected"],
            "last_message_at":_WS_STATE["last_message_at"],
            "reconnects":_WS_STATE["reconnects"],
            "last_error":_WS_STATE["last_error"],
            "symbols_live":[s for s in MAGNIFICENT_7 if _live_quote(s)],
            "note":"connected=true significa socket abierto; symbols_live son los que tienen dato de menos de 20s. Si el plan Alpaca no incluye WebSocket o la key es solo REST, esto queda en connected=false y LEONIX sigue funcionando por REST/polling."}

@app.get("/api/live/quotes")
def live_quotes():
    return {"quotes":_LIVE_CACHE, "ws_connected":_WS_STATE["connected"]}

@app.get("/api/data-hub/status")
def data_hub_status():
    return data_hub_snapshot()


def td(endpoint, params=None):
    if not TD_KEY:
        raise HTTPException(503,"TWELVE_DATA_API_KEY no configurada")
    p=dict(params or {})
    p["apikey"]=TD_KEY
    t0=time.time()
    try:
        r=requests.get(f"https://api.twelvedata.com/{endpoint}",params=p,timeout=20)
        r.raise_for_status()
        d=r.json()
        if isinstance(d,dict) and d.get("status")=="error":
            _record_health("twelvedata", False, error=d.get("message"))
            raise HTTPException(502,d.get("message","Twelve Data error"))
        _record_health("twelvedata", True, latency_ms=round((time.time()-t0)*1000,1))
        return d
    except HTTPException:
        raise
    except Exception as e:
        _record_health("twelvedata", False, error=e)
        raise

def av(params):
    if not AV_KEY:
        raise HTTPException(503,"ALPHAVANTAGE_API_KEY no configurada")
    st=alpha_usage_state()
    if st["remaining"]<=0:
        raise HTTPException(429,"Límite diario Alpha Vantage agotado (25/25). Usa Twelve Data o espera el reinicio diario.")
    params=dict(params); params["apikey"]=AV_KEY
    _AV_USAGE["used"]+=1
    t0=time.time()
    try:
        r=requests.get("https://www.alphavantage.co/query",params=params,timeout=25)
        r.raise_for_status()
        d=r.json()
        if "Error Message" in d:
            _record_health("alphavantage", False, error=d["Error Message"])
            raise HTTPException(404,d["Error Message"])
        if "Note" in d or "Information" in d:
            msg=d.get("Note") or d.get("Information")
            if "25 requests per day" in str(msg).lower() or "rate limit" in str(msg).lower():
                _AV_USAGE.update({"used":AV_DAILY_LIMIT,"limit_hit":True})
            _record_health("alphavantage", False, error=msg)
            raise HTTPException(429,msg)
        _record_health("alphavantage", True, latency_ms=round((time.time()-t0)*1000,1))
        return d
    except HTTPException:
        raise
    except Exception as e:
        _record_health("alphavantage", False, error=e)
        raise

@app.api_route("/", methods=["GET","HEAD"])
def home():
    return {"ok":True,"service":"Contratos León API","version":"51.0","docs":"/docs"}

@app.api_route("/health", methods=["GET","HEAD"])
@app.api_route("/api/health", methods=["GET","HEAD"])
def health():
    primary = "Alpaca" if (ALPACA_KEY and ALPACA_SECRET) else ("Twelve Data" if TD_KEY else "Alpha Vantage")
    return {"ok":True,"version":"51.0","alpaca":bool(ALPACA_KEY and ALPACA_SECRET),
            "alpha_vantage":bool(AV_KEY),"twelve_data":bool(TD_KEY),
            "market_provider":primary,"options_provider":"Alpha Vantage",
            "alpha_daily":alpha_usage_state(),"data_hub":data_hub_snapshot()}

@app.get("/api/alpha-usage")
def alpha_usage():
    return alpha_usage_state()

def market_clock_state():
    now=datetime.now(NY_TZ)
    open_t=now.replace(hour=9,minute=30,second=0,microsecond=0)
    close_t=now.replace(hour=16,minute=0,second=0,microsecond=0)
    is_holiday_today = now.date() in NYSE_HOLIDAYS
    if now.weekday()<5 and not is_holiday_today and open_t <= now < close_t:
        target=close_t
        status="open"
        label="Cierre hoy · 4:00 PM ET"
    else:
        status="closed"
        if now.weekday()<5 and not is_holiday_today and now < open_t:
            target=open_t
        else:
            d=(now+timedelta(days=1)).date()
            while d.weekday()>=5 or d in NYSE_HOLIDAYS:
                d+=timedelta(days=1)
            target=datetime.combine(d, datetime.min.time(), tzinfo=NY_TZ).replace(hour=9,minute=30)
        label=target.strftime("Próxima apertura · %a %b %d · 9:30 AM ET")
    return {
        "status":status,
        "now":now.isoformat(),
        "target_at":target.isoformat(),
        "seconds":max(0,int((target-now).total_seconds())),
        "target_label":label,
        "timezone":"America/New_York",
        "regular_hours":"09:30-16:00 ET",
        "is_holiday_today":is_holiday_today
    }

@app.get("/api/market-clock")
def market_clock():
    return market_clock_state()

@app.get("/api/search")
def search(q:str=Query(...,min_length=1,max_length=32)):
    q=q.upper().strip()
    names={"AAPL":"Apple","MSFT":"Microsoft","GOOGL":"Alphabet / Google","AMZN":"Amazon","NVDA":"NVIDIA","META":"Meta Platforms","TSLA":"Tesla"}
    items=[{"symbol":x,"name":names[x],"type":"Equity","region":"United States"} for x in MAGNIFICENT_7 if q in x or q in names[x].upper()]
    return {"mode":"magnificent7","items":items}

@app.get("/api/quote")
def quote(symbol:str):
    symbol=symbol.upper().strip()
    if symbol not in MAGNIFICENT_7:
        raise HTTPException(403,"Modo prueba: Contratos León está limitado a las Magníficas 7")
    live=_live_quote(symbol)
    if live:
        return {**live, "change_pct":None}
    if ALPACA_KEY and ALPACA_SECRET:
        try:
            q=alpaca_quote(symbol)
            q["received_at"]=time.time()   # cuándo lo pidió LEONIX; NO reemplaza market_time real
            return q
        except Exception:
            pass
    try:
        d=td("quote", {"symbol":symbol})
        price=float(d.get("close") or d.get("price") or 0)
        pct=float(d.get("percent_change") or 0)
        if price>0:
            return {"symbol":symbol,"price":price,"change_pct":pct,"provider":"Twelve Data",
                    "market_time":None,"received_at":time.time(),
                    "freshness_note":"Twelve Data no expone la hora del tick en este endpoint; la antigüedad se estima por tiempo de respuesta."}
    except Exception:
        pass
    d=av({"function":"GLOBAL_QUOTE","symbol":symbol}).get("Global Quote",{})
    if not d: raise HTTPException(404,"Sin cotización")
    return {"symbol":symbol,"price":float(d.get("05. price",0) or 0),"change_pct":float(str(d.get("10. change percent","0")).replace("%","") or 0),
            "provider":"Alpha Vantage","market_time":None,"received_at":time.time(),
            "freshness_note":"Alpha Vantage no expone la hora del tick en este endpoint; la antigüedad se estima por tiempo de respuesta."}

def series(symbol, intraday=True):
    symbol=symbol.upper().strip()
    interval="5min" if intraday else "1day"
    try:
        d=td("time_series", {"symbol":symbol,"interval":interval,"outputsize":120,"order":"ASC"})
        vals=d.get("values") or []
        rows=[]
        for v in vals:
            try: rows.append((v.get("datetime"),float(v.get("close")),float(v.get("volume") or 0)))
            except: pass
        rows.sort()
        if rows: return rows
    except Exception:
        pass
    if intraday:
        d=av({"function":"TIME_SERIES_INTRADAY","symbol":symbol,"interval":"5min","outputsize":"compact"})
        key=next((k for k in d if k.startswith("Time Series")),None)
        ts=d.get(key,{}) if key else {}
    else:
        d=av({"function":"TIME_SERIES_DAILY","symbol":symbol,"outputsize":"compact"})
        ts=d.get("Time Series (Daily)") or {}
    rows=[]
    for t,v in ts.items():
        try: rows.append((t,float(v["4. close"]),float(v["5. volume"])))
        except: pass
    rows.sort()
    return rows

def steps(h):
    return {"1m":1,"2m":1,"3m":1,"4m":1,"5m":1,"10m":2,"15m":3,"30m":6,"45m":9,"1h":12,"2h":24,"4h":48,"1d":1,"3d":3,"1w":5}.get(h,3)

@app.get("/api/magnificent7")
def magnificent7():
    now=time.time()
    if _M7_CACHE["data"] and now-_M7_CACHE["ts"]<60:
        return _M7_CACHE["data"]
    rows=[]
    for sym in MAGNIFICENT_7:
        try:
            q=quote(sym)
            pct=f(q.get("change_pct"),0)
            score=round(_clamp(62+pct*4,35,92),1)
            strength="FUERTE" if score>=75 else "NEUTRAL" if score>=58 else "DÉBIL"
            signal="VIGILAR" if score>=75 else "ESPERAR" if score>=58 else "EVITAR"
            rows.append({"symbol":sym,"price":f(q.get("price")),"change_pct":pct,"score":score,"strength":strength,"signal":signal,"provider":q.get("provider")})
        except Exception as e:
            rows.append({"symbol":sym,"price":None,"change_pct":None,"score":0,"strength":"SIN DATOS","signal":"NO OPERAR","error":str(e)})
    valid=[x for x in rows if x.get("price")]
    valid.sort(key=lambda x:x.get("score",0),reverse=True)
    best=valid[0] if valid else None
    data={"mode":"MAGNIFICENT_7_TEST","symbols":MAGNIFICENT_7,"best":best,"ranking":valid,"all":rows,"note":"Ranking inicial de mercado para pruebas. Los contratos de opciones siguen en módulos separados y requieren datos de opciones compatibles. No garantiza ganancias."}
    _M7_CACHE.update({"ts":now,"data":data})
    return data

@app.get("/api/backtest")
def backtest(symbol:str,horizon:str="15m"):
    intraday=horizon.endswith("m") or horizon.endswith("h")
    rows=series(symbol.upper(),intraday)
    if len(rows)<40: raise HTTPException(422,"Histórico insuficiente")
    closes=[r[1] for r in rows]; vols=[r[2] for r in rows]
    s=steps(horizon); samples=wins=0; rets=[]
    for i in range(20,len(rows)-s):
        ma5=sum(closes[i-4:i+1])/5
        ma20=sum(closes[i-19:i+1])/20
        avgv=sum(vols[i-19:i+1])/20
        signal=closes[i]>ma5>ma20 and vols[i]>=avgv
        if not signal: continue
        samples+=1
        rr=(closes[i+s]/closes[i]-1)*100
        rets.append(rr)
        if rr>0:wins+=1
    return {"symbol":symbol.upper(),"horizon":horizon,"samples":samples,"wins":wins,
            "win_rate":(wins/samples*100) if samples else 0,
            "avg_return_pct":statistics.mean(rets) if rets else 0}

# --- Backtesting avanzado (proxy Black-Scholes) -------------------------
# Alpha Vantage HISTORICAL_OPTIONS existe pero el límite de 25 llamadas/día
# hace inviable un backtest real de opciones. Esta es una aproximación
# teórica: usa precio histórico de la acción + volatilidad realizada como
# proxy de IV. NO es dato real de mercado de opciones — se etiqueta así
# en cada respuesta para que Robot AI nunca lo presente como si lo fuera.
def _norm_cdf(x):
    return (1.0+math.erf(x/math.sqrt(2.0)))/2.0

def _bs_price(spot, strike, t_years, sigma, r=0.045, opt_type="CALL"):
    if t_years<=0 or sigma<=0 or spot<=0 or strike<=0:
        return max(0.0, (spot-strike) if opt_type=="CALL" else (strike-spot))
    d1=(math.log(spot/strike)+(r+sigma*sigma/2)*t_years)/(sigma*math.sqrt(t_years))
    d2=d1-sigma*math.sqrt(t_years)
    if opt_type=="CALL":
        return spot*_norm_cdf(d1)-strike*math.exp(-r*t_years)*_norm_cdf(d2)
    return strike*math.exp(-r*t_years)*_norm_cdf(-d2)-spot*_norm_cdf(-d1)

def _realized_vol(closes, window=20):
    if len(closes)<window+1: return 0.35
    rets=[math.log(closes[i]/closes[i-1]) for i in range(len(closes)-window,len(closes)) if closes[i-1]>0]
    if len(rets)<5: return 0.35
    sd=statistics.pstdev(rets)
    return max(0.08, min(2.5, sd*math.sqrt(252)))

@app.get("/api/backtest/options")
def backtest_options(symbol:str, horizon:str="15m", strike_offset_pct:float=0, option_type:str="AUTO", dte_days:int=7):
    symbol=symbol.upper().strip()
    intraday=horizon.endswith("m") or horizon.endswith("h")
    rows=series(symbol,intraday)
    if len(rows)<45: raise HTTPException(422,"Histórico insuficiente para el proxy")
    closes=[r[1] for r in rows]; vols=[r[2] for r in rows]
    s=steps(horizon)
    bar_years=(1/252) if not intraday else (1/252)/26   # aprox 6.5h/5min ~ 78 barras/dia; usamos 1/(252*26)
    samples=0; wins=0; rets=[]
    for i in range(20,len(rows)-s):
        ma5=sum(closes[i-4:i+1])/5
        ma20=sum(closes[i-19:i+1])/20
        avgv=sum(vols[i-19:i+1])/20
        long_signal=closes[i]>ma5>ma20 and vols[i]>=avgv
        short_signal=closes[i]<ma5<ma20 and vols[i]>=avgv
        ot = option_type.upper()
        if ot=="AUTO":
            if long_signal: ot="CALL"
            elif short_signal: ot="PUT"
            else: continue
        elif ot=="CALL" and not long_signal: continue
        elif ot=="PUT" and not short_signal: continue
        spot0=closes[i]
        strike=round(spot0*(1+strike_offset_pct/100),2)
        sigma=_realized_vol(closes[max(0,i-40):i+1])
        t0=dte_days/365
        entry_px=_bs_price(spot0, strike, t0, sigma, opt_type=ot)
        if entry_px<=0.01: continue
        spot1=closes[i+s]
        elapsed_years=(bar_years*s)
        t1=max(0.0001, t0-elapsed_years)
        exit_px=_bs_price(spot1, strike, t1, sigma, opt_type=ot)
        rr=(exit_px/entry_px-1)*100
        samples+=1; rets.append(rr)
        if rr>0: wins+=1
    return {"symbol":symbol,"horizon":horizon,"option_type":option_type.upper(),"strike_offset_pct":strike_offset_pct,
            "dte_days":dte_days,"samples":samples,"wins":wins,
            "win_rate":(wins/samples*100) if samples else 0,
            "avg_return_pct":statistics.mean(rets) if rets else 0,
            "method":"proxy_black_scholes",
            "note":"PROXY teórico: precio de acción histórico + volatilidad realizada como IV estimada, valorado con Black-Scholes. No es dato real de cadena de opciones histórica — Alpha Vantage limita HISTORICAL_OPTIONS a 25 llamadas/día, insuficiente para un backtest real. No usar para prometer resultados reales."}

def f(v,d=0):
    try:return float(v)
    except:return d

def dte(exp):
    try:
        y,m,d=map(int,exp.split("-"))
        return max(0,(date(y,m,d)-date.today()).days)
    except:return 9999

def normalize(o):
    return {
      "type":str(o.get("type") or o.get("option_type") or "").upper(),
      "strike":f(o.get("strike")),
      "expiration":o.get("expiration") or o.get("expiration_date"),
      "bid":f(o.get("bid")),"ask":f(o.get("ask")),"last":f(o.get("last")),"mark":f(o.get("mark")),
      "volume":f(o.get("volume")),"open_interest":f(o.get("open_interest") or o.get("openInterest")),
      "iv":f(o.get("implied_volatility") or o.get("impliedVolatility")),
      "delta":f(o.get("delta")),"gamma":f(o.get("gamma")),"theta":f(o.get("theta")),"vega":f(o.get("vega"))
    }

def score_option(o,spot):
    px=o["ask"] or o["last"] or o["mark"]
    mid=((o["bid"]+o["ask"])/2) if o["bid"]>0 and o["ask"]>0 else px
    spread=((o["ask"]-o["bid"])/mid*100) if mid and o["ask"]>=o["bid"] and o["bid"]>0 else 100
    money=abs(o["strike"]-spot)/spot*100 if spot and o["strike"] else 99
    delta=abs(o["delta"]); theta=abs(o["theta"]); days=dte(o["expiration"] or "")
    s=50
    s+=min(12,math.log10(max(1,o["volume"])+1)*4)
    s+=min(12,math.log10(max(1,o["open_interest"])+1)*3)
    s+=12 if spread<=5 else 7 if spread<=10 else 2 if spread<=20 else -12
    s+=10 if money<=3 else 6 if money<=7 else 2 if money<=12 else -8
    s+=10 if .45<=delta<=.70 else 5 if (.30<=delta<.45 or .70<delta<=.80) else -2 if delta else 0
    s-=8 if theta>.20 else 4 if theta>.10 else 0
    s+=8 if 7<=days<=45 else 3 if 2<=days<7 or 45<days<=90 else -12 if days<2 else -3 if days>120 else 0
    if o["iv"]>1.5:s-=6
    elif .20<=o["iv"]<=.80:s+=4
    return max(0,min(100,round(s,1))),round(spread,2),days,px

@app.get("/api/options/horizons")
def option_horizons(symbol:str):
    hs=["1m","2m","3m","4m","5m","10m","15m","30m","45m","1h","2h","4h","1d","3d","1w"]
    out=[]
    for h in hs:
        try:
            b=backtest(symbol,h)
            wr=f(b["win_rate"]); ar=f(b["avg_return_pct"])
            rs=max(0,min(100,round(45+wr*.35+max(-10,min(10,ar*3)),1)))
            out.append({"horizon":h,"win_rate":round(wr,1),"avg_return_pct":round(ar,2),"leon_range_score":rs})
        except:
            out.append({"horizon":h,"win_rate":None,"avg_return_pct":None,"leon_range_score":None})
    valid=[x for x in out if x["leon_range_score"] is not None]
    best=max(valid,key=lambda x:x["leon_range_score"]) if valid else None
    return {"symbol":symbol.upper(),"best_horizon":best,"ranges":out}

@app.get("/api/options/scan")
def options_scan(symbol:str,direction:str="AUTO",horizon:str="15m",top:int=5):
    symbol=symbol.upper().strip()
    q=quote(symbol); spot=f(q.get("price"))
    if direction.upper()=="AUTO":
        try: direction="CALL" if backtest(symbol,horizon)["avg_return_pct"]>=0 else "PUT"
        except: direction="CALL"
    direction=direction.upper()
    d=av({"function":"REALTIME_OPTIONS","symbol":symbol,"require_greeks":"true"})
    raw=d.get("data") or d.get("options") or d.get("option_chain") or []
    if not raw:
        raise HTTPException(402,"REALTIME_OPTIONS no devolvió contratos. Alpha Vantage exige un plan Premium para opciones en tiempo real.")
    items=[]
    for r in raw:
        o=normalize(r)
        if o["type"] and o["type"]!=direction: continue
        if o["ask"]<=0 and o["last"]<=0: continue
        sc,spread,days,px=score_option(o,spot)
        o.update({"leon_score":sc,"spread_pct":spread,"dte":days,"cost_1_contract":round(px*100,2),
                  "break_even":round(o["strike"]+px,2) if direction=="CALL" else round(o["strike"]-px,2)})
        items.append(o)
    items.sort(key=lambda x:(x["leon_score"],x["open_interest"],x["volume"]),reverse=True)
    picks=items[:max(1,min(top,20))]
    if not picks: raise HTTPException(422,"No se encontraron contratos utilizables")
    return {"symbol":symbol,"spot":spot,"direction":direction,"horizon":horizon,"best":picks[0],"top":picks,
            "note":"León Score compara liquidez, spread, moneyness, delta, theta, IV y vencimiento. No garantiza ganancias."}


def _option_chain_raw(symbol):
    d=av({"function":"REALTIME_OPTIONS","symbol":symbol.upper().strip(),"require_greeks":"true"})
    raw=d.get("data") or d.get("options") or d.get("option_chain") or []
    if not raw:
        raise HTTPException(402,"REALTIME_OPTIONS no devolvió contratos. El proveedor requiere acceso compatible con opciones en tiempo real.")
    return raw

def _find_contract(symbol, opt_type, strike, expiration):
    for r in _option_chain_raw(symbol):
        o=normalize(r)
        if o["type"]==opt_type and abs(o["strike"]-strike)<0.005 and o["expiration"]==expiration:
            return o
    return None

# --- Paper Options Engine (v49) ----------------------------------------
SLIPPAGE_PCT=0.01           # 1% contra el trader al llenar la orden
COMMISSION_PER_CONTRACT=0.65
_PAPER={"cash":10000.0,"start":10000.0,"realized":0.0,"positions":[],"closed":[],"next_id":1,"day":None,"realized_today":0.0}

def _paper_mark(pos):
    try:
        c=_find_contract(pos["symbol"], pos["type"], pos["strike"], pos["expiration"])
    except Exception as e:
        return {**pos,"mark":None,"unrealized_pl":None,"mark_error":str(e)}
    if not c:
        return {**pos,"mark":None,"unrealized_pl":None,"mark_error":"Contrato ya no está en la cadena actual"}
    mid=(c["bid"]+c["ask"])/2 if c["bid"]>0 and c["ask"]>0 else (c["last"] or c["bid"] or c["ask"])
    market_value=round(mid*100*pos["contracts"],2)
    unrealized=round(market_value-pos["entry_cost"],2)
    return {**pos,"mark":mid,"market_value":market_value,"unrealized_pl":unrealized,
            "current_bid":c["bid"],"current_ask":c["ask"],"current_iv":c["iv"],"mark_error":None}

def _paper_account_snapshot():
    marked=[_paper_mark(p) for p in _PAPER["positions"]]
    open_value=sum(p["market_value"] for p in marked if p.get("market_value") is not None)
    stale_cost=sum(p["entry_cost"] for p in marked if p.get("mark_error"))
    equity=round(_PAPER["cash"]+open_value+stale_cost,2)
    return_pct=round((equity/_PAPER["start"]-1)*100,2) if _PAPER["start"] else 0
    return {"cash":round(_PAPER["cash"],2),"start":_PAPER["start"],"realized_pl":round(_PAPER["realized"],2),
            "equity":equity,"return_pct":return_pct,"open_positions":marked,"closed_trades":_PAPER["closed"][-25:],
            "daily":paper_daily_status(),
            "note":"Paper trading con datos reales de opciones (bid/ask, IV, Greeks) y slippage simulado. Dinero ficticio. Si un contrato ya no aparece en la cadena, se marca como sin datos en vez de inventar un precio."}

@app.get("/api/paper/options/account")
def paper_options_account():
    return _paper_account_snapshot()

@app.post("/api/paper/options/open")
def paper_options_open(symbol:str, direction:str="AUTO", horizon:str="AUTO", contracts:int=1):
    if contracts<1: raise HTTPException(400,"contracts debe ser >= 1")
    daily=paper_daily_status()
    if daily["blocked"]:
        raise HTTPException(423,f"NO TRADE: límite de pérdida diaria alcanzado ({daily['realized_today']} / {daily['limit']}). Se reinicia mañana.")
    h = "15m" if horizon.upper()=="AUTO" else horizon
    scan=options_scan(symbol, direction, h, top=1)
    c=scan["best"]
    fill_ref=c["ask"] if c["ask"]>0 else c["last"]
    if not fill_ref: raise HTTPException(422,"Contrato sin precio utilizable para abrir posición")
    entry_fill=round(fill_ref*(1+SLIPPAGE_PCT),4)
    entry_cost=round(entry_fill*100*contracts + COMMISSION_PER_CONTRACT*contracts,2)
    if entry_cost>_PAPER["cash"]:
        raise HTTPException(400,f"Cash insuficiente: se necesitan {entry_cost}, disponible {round(_PAPER['cash'],2)}")
    equity=_paper_account_snapshot()["equity"]
    if equity>0 and entry_cost/equity*100>MAX_POSITION_PCT:
        raise HTTPException(400,f"Tamaño de posición excede el máximo de {MAX_POSITION_PCT}% del equity ({round(entry_cost/equity*100,1)}%).")
    pos={"id":_PAPER["next_id"],"symbol":scan["symbol"],"type":c["type"],"strike":c["strike"],
         "expiration":c["expiration"],"contracts":contracts,"direction":scan["direction"],
         "entry_bid":c["bid"],"entry_ask":c["ask"],"entry_fill":entry_fill,"entry_cost":entry_cost,
         "entry_spread_pct":c.get("spread_pct"),"entry_iv":c["iv"],"entry_delta":c["delta"],"entry_theta":c["theta"],
         "opened_at":time.time()}
    _PAPER["next_id"]+=1
    _PAPER["cash"]=round(_PAPER["cash"]-entry_cost,2)
    _PAPER["positions"].append(pos)
    return {"opened":pos,"account":_paper_account_snapshot()}

@app.post("/api/paper/options/close")
def paper_options_close(position_id:int):
    pos=next((p for p in _PAPER["positions"] if p["id"]==position_id), None)
    if not pos: raise HTTPException(404,"Posición no encontrada")
    c=_find_contract(pos["symbol"], pos["type"], pos["strike"], pos["expiration"])
    if not c: raise HTTPException(409,"El contrato ya no está en la cadena actual; no se puede cerrar con datos reales")
    fill_ref=c["bid"] if c["bid"]>0 else c["last"]
    if not fill_ref: raise HTTPException(422,"Contrato sin precio utilizable para cerrar posición")
    exit_fill=round(fill_ref*(1-SLIPPAGE_PCT),4)
    proceeds=round(exit_fill*100*pos["contracts"] - COMMISSION_PER_CONTRACT*pos["contracts"],2)
    pnl=round(proceeds-pos["entry_cost"],2)
    _PAPER["cash"]=round(_PAPER["cash"]+proceeds,2)
    _PAPER["realized"]=round(_PAPER["realized"]+pnl,2)
    _paper_check_daily_reset()
    _PAPER["realized_today"]=round(_PAPER["realized_today"]+pnl,2)
    _PAPER["positions"]=[p for p in _PAPER["positions"] if p["id"]!=position_id]
    trade={**pos,"exit_fill":exit_fill,"exit_bid":c["bid"],"exit_ask":c["ask"],"proceeds":proceeds,"pnl":pnl,"closed_at":time.time()}
    _PAPER["closed"].append(trade)
    return {"closed":trade,"account":_paper_account_snapshot()}

@app.post("/api/paper/options/reset")
def paper_options_reset(start:float=10000):
    _PAPER.update({"cash":start,"start":start,"realized":0.0,"positions":[],"closed":[],"next_id":1,"day":_ny_today(),"realized_today":0.0})
    return _paper_account_snapshot()

@app.get("/api/options/gex")
def options_gex(symbol:str, expiration:str|None=None):
    symbol=symbol.upper().strip()
    spot=f(quote(symbol).get("price"))
    raw=_option_chain_raw(symbol)
    rows=[]
    for r in raw:
        o=normalize(r)
        if expiration and o.get("expiration")!=expiration:
            continue
        if not o["strike"] or not o["open_interest"] or not o["gamma"]:
            continue
        sign = 1 if o["type"]=="CALL" else -1
        gex = sign * o["gamma"] * o["open_interest"] * 100 * (spot**2) * 0.01
        rows.append({"strike":o["strike"],"type":o["type"],"expiration":o["expiration"],"gamma":o["gamma"],"open_interest":o["open_interest"],"gex":gex})
    if not rows:
        raise HTTPException(422,"No hay gamma/open interest suficientes para calcular GEX")
    by={}
    for x in rows:
        k=x["strike"]
        by.setdefault(k,{"strike":k,"call_gex":0.0,"put_gex":0.0,"net_gex":0.0})
        if x["type"]=="CALL": by[k]["call_gex"] += abs(x["gex"])
        else: by[k]["put_gex"] += -abs(x["gex"])
        by[k]["net_gex"] += x["gex"]
    levels=sorted(by.values(), key=lambda x:x["strike"])
    call_wall=max(levels,key=lambda x:x["call_gex"])
    put_wall=min(levels,key=lambda x:x["put_gex"])
    total=sum(x["net_gex"] for x in levels)
    cum=0.0; flip=None; best_abs=float("inf")
    for x in levels:
        cum += x["net_gex"]
        if abs(cum) < best_abs:
            best_abs=abs(cum); flip=x["strike"]
    return {"symbol":symbol,"spot":spot,"net_gex":total,"call_wall":call_wall["strike"],"put_wall":put_wall["strike"],"gamma_flip":flip,"levels":levels,"note":"GEX es una aproximación basada en gamma y open interest. No es una predicción garantizada."}

@app.get("/api/orderflow/proxy")
def orderflow_proxy(symbol:str, interval:str="5min"):
    symbol=symbol.upper().strip()
    rows=series(symbol, True)
    if len(rows)<20:
        raise HTTPException(422,"Histórico intradía insuficiente")
    data=[]; prev=rows[0][1]; buy_vol=sell_vol=0.0
    for t,close,vol in rows[-80:]:
        delta=close-prev
        signed=vol if delta>0 else -vol if delta<0 else 0
        if signed>0: buy_vol+=vol
        elif signed<0: sell_vol+=vol
        data.append({"time":t,"price":close,"volume":vol,"signed_volume":signed})
        prev=close
    total=buy_vol+sell_vol
    imbalance=((buy_vol-sell_vol)/total*100) if total else 0
    return {"symbol":symbol,"buy_volume":buy_vol,"sell_volume":sell_vol,"imbalance_pct":imbalance,"points":data,"source":"price-volume proxy","note":"Este panel NO es Bookmap L2/MBO real. Para liquidez resting, DOM e icebergs se necesita un feed de profundidad compatible."}


def _clamp(v,a=0,b=100):
    return max(a,min(b,v))

# --- Risk Shield 2.0: earnings, sesión, pérdida diaria, tamaño de posición ---
_EARNINGS_CACHE={}
EARNINGS_CACHE_TTL=6*3600
DAILY_LOSS_LIMIT_PCT=3.0
MAX_POSITION_PCT=10.0

def days_to_next_earnings(symbol):
    symbol=symbol.upper().strip()
    c=_EARNINGS_CACHE.get(symbol)
    if c and time.time()-c["ts"]<EARNINGS_CACHE_TTL:
        return c["days"]
    if not AV_KEY:
        return c["days"] if c else None
    st=alpha_usage_state()
    if st["remaining"]<=1:
        return c["days"] if c else None
    try:
        params={"function":"EARNINGS_CALENDAR","symbol":symbol,"horizon":"3month","apikey":AV_KEY}
        _AV_USAGE["used"]+=1
        r=requests.get("https://www.alphavantage.co/query",params=params,timeout=20)
        r.raise_for_status()
        import csv, io
        days=None
        for row in csv.DictReader(io.StringIO(r.text)):
            rd=row.get("reportDate")
            if not rd: continue
            try:
                y,m,dd=map(int,rd.split("-"))
                delta=(date(y,m,dd)-date.today()).days
                if delta>=0 and (days is None or delta<days): days=delta
            except: pass
        _EARNINGS_CACHE[symbol]={"days":days,"ts":time.time()}
        return days
    except Exception:
        return c["days"] if c else None

def _ny_today():
    return datetime.now(NY_TZ).date().isoformat()

def _paper_check_daily_reset():
    today=_ny_today()
    if _PAPER.get("day")!=today:
        _PAPER["day"]=today
        _PAPER["realized_today"]=0.0

def paper_daily_status():
    _paper_check_daily_reset()
    limit=-_PAPER["start"]*DAILY_LOSS_LIMIT_PCT/100
    return {"realized_today":round(_PAPER["realized_today"],2),"limit":round(limit,2),
            "blocked":_PAPER["realized_today"]<=limit,"day":_PAPER["day"]}

def _grade(score):
    if score >= 85: return "FUERTE"
    if score >= 72: return "FAVORABLE"
    if score >= 60: return "VIGILAR"
    return "ESPERAR"

def _direction_from_components(range_avg, imbalance, net_gex):
    votes=0
    votes += 1 if range_avg >= 0 else -1
    votes += 1 if imbalance >= 0 else -1
    votes += 1 if net_gex >= 0 else -1
    return "CALL" if votes >= 0 else "PUT"

def risk_shield(symbol, best, components, win_rate, hub=None):
    """Reglas deterministicas de proteccion de capital. Nunca inventa datos:
    si falta un campo critico, la puerta correspondiente se marca en rojo."""
    gates=[]

    hub=hub or data_hub_snapshot()
    if hub["overall"]=="DOWN":
        gates.append({"name":"datos","status":"RED","detail":"Data Hub: ningún proveedor está LIVE. Datos no confiables."})
    elif hub["overall"]=="DELAYED":
        gates.append({"name":"datos","status":"YELLOW","detail":"Data Hub: datos DELAYED, ningún proveedor en tiempo real ahora mismo."})
    else:
        gates.append({"name":"datos","status":"GREEN","detail":"Data Hub: al menos un proveedor está LIVE."})

    mc=market_clock_state()
    if mc["status"]!="open":
        motivo=" (feriado NYSE)" if mc.get("is_holiday_today") else ""
        gates.append({"name":"sesión","status":"YELLOW","detail":f"Mercado cerrado ahora{motivo} · {mc['target_label']}."})
    else:
        gates.append({"name":"sesión","status":"GREEN","detail":"Sesión regular NYSE/NASDAQ abierta."})

    try:
        de=days_to_next_earnings(symbol)
    except Exception:
        de=None
    if de is None:
        gates.append({"name":"earnings","status":"YELLOW","detail":"Sin dato de earnings disponible todavía."})
    elif de<=2:
        gates.append({"name":"earnings","status":"RED","detail":f"Earnings en {de} día(s) — alto riesgo de gap."})
    elif de<=5:
        gates.append({"name":"earnings","status":"YELLOW","detail":f"Earnings en {de} días — IV probablemente inflado."})
    else:
        gates.append({"name":"earnings","status":"GREEN","detail":f"Próximos earnings en {de} días."})

    daily=paper_daily_status()
    if daily["blocked"]:
        gates.append({"name":"pérdida diaria","status":"RED","detail":f"Límite diario alcanzado ({daily['realized_today']} / {daily['limit']}). NO TRADE por hoy."})
    elif daily["realized_today"]<0 and daily["realized_today"]<=daily["limit"]*0.6:
        gates.append({"name":"pérdida diaria","status":"YELLOW","detail":f"Cerca del límite diario ({daily['realized_today']} / {daily['limit']})."})
    else:
        gates.append({"name":"pérdida diaria","status":"GREEN","detail":f"P/L del día: {daily['realized_today']}."})

    spread=f(best.get("spread_pct"), None) if best.get("spread_pct") is not None else None
    oi=f(best.get("open_interest"), 0)
    vol=f(best.get("volume"), 0)
    iv=f(best.get("iv"), None) if best.get("iv") is not None else None

    if spread is None:
        gates.append({"name":"spread","status":"RED","detail":"Sin dato de spread del proveedor de opciones."})
    elif spread > 15:
        gates.append({"name":"spread","status":"RED","detail":f"Spread {spread:.1f}% — demasiado amplio para entrar."})
    elif spread > 8:
        gates.append({"name":"spread","status":"YELLOW","detail":f"Spread {spread:.1f}% — aceptable pero vigilar."})
    else:
        gates.append({"name":"spread","status":"GREEN","detail":f"Spread {spread:.1f}% dentro de rango sano."})

    if oi < 50 and vol < 20:
        gates.append({"name":"liquidez","status":"RED","detail":f"OI {int(oi)} / volumen {int(vol)} — liquidez insuficiente."})
    elif oi < 200 and vol < 100:
        gates.append({"name":"liquidez","status":"YELLOW","detail":f"OI {int(oi)} / volumen {int(vol)} — liquidez moderada."})
    else:
        gates.append({"name":"liquidez","status":"GREEN","detail":f"OI {int(oi)} / volumen {int(vol)} — liquidez saludable."})

    if iv is None:
        gates.append({"name":"iv","status":"YELLOW","detail":"Sin dato de IV del proveedor."})
    elif iv*100 > 120:
        gates.append({"name":"iv","status":"RED","detail":f"IV {iv*100:.0f}% — extremadamente alta."})
    elif iv*100 > 70:
        gates.append({"name":"iv","status":"YELLOW","detail":f"IV {iv*100:.0f}% — elevada, prima cara."})
    else:
        gates.append({"name":"iv","status":"GREEN","detail":f"IV {iv*100:.0f}% dentro de rango normal."})

    scores=[v for v in (components or {}).values() if isinstance(v,(int,float))]
    disagreement = (max(scores)-min(scores)) if len(scores)>=2 else 0
    if disagreement > 45:
        gates.append({"name":"consenso","status":"RED","detail":f"Componentes en desacuerdo (dispersión {disagreement:.0f} pts)."})
    elif disagreement > 25:
        gates.append({"name":"consenso","status":"YELLOW","detail":f"Componentes algo divididos (dispersión {disagreement:.0f} pts)."})
    else:
        gates.append({"name":"consenso","status":"GREEN","detail":"Los componentes coinciden en la misma lectura."})

    if win_rate and win_rate < 40:
        gates.append({"name":"historial","status":"YELLOW","detail":f"Win rate histórico {win_rate:.0f}% — por debajo del promedio."})
    else:
        gates.append({"name":"historial","status":"GREEN","detail":f"Win rate histórico {win_rate:.0f}%."})

    cost1=f(best.get("cost_1_contract"), None) if best.get("cost_1_contract") is not None else None
    equity=_paper_account_snapshot()["equity"] if _PAPER["start"] else 0
    if cost1 and equity:
        pos_pct=cost1/equity*100
        if pos_pct>MAX_POSITION_PCT:
            gates.append({"name":"tamaño","status":"RED","detail":f"1 contrato ({round(pos_pct,1)}% del equity) excede el máximo de {MAX_POSITION_PCT}%."})
        elif pos_pct>MAX_POSITION_PCT*0.6:
            gates.append({"name":"tamaño","status":"YELLOW","detail":f"1 contrato usa {round(pos_pct,1)}% del equity — cerca del máximo."})
        else:
            gates.append({"name":"tamaño","status":"GREEN","detail":f"1 contrato usa {round(pos_pct,1)}% del equity."})
    else:
        gates.append({"name":"tamaño","status":"YELLOW","detail":"Sin dato de costo de contrato para validar tamaño."})

    if any(g["status"]=="RED" for g in gates):
        overall="RED"
    elif any(g["status"]=="YELLOW" for g in gates):
        overall="YELLOW"
    else:
        overall="GREEN"
    return {"status":overall,"gates":gates}

def robot_ai_summary(symbol, direction, unified_score, shield, providers_used):
    if shield["status"]=="RED":
        decision="NO TRADE"
        line=f"Risk Shield bloquea {symbol}: hay una condición crítica sin cumplir. LEONIX no arma el contrato aunque el score sea alto."
    elif shield["status"]=="YELLOW" or unified_score < 60:
        decision="WAIT"
        line=f"{symbol} está en zona gris (score {unified_score}/100). LEONIX prefiere esperar confirmación antes de proponer un contrato."
    else:
        decision=direction
        line=f"{symbol} pasó los filtros de Risk Shield y el score unificado es {unified_score}/100 a favor de {direction}."
    used=", ".join(providers_used) if providers_used else "sin proveedor confirmado"
    return {"decision":decision,"summary":line,"providers_used":used,
            "note":"Esta explicación no garantiza ganancias; resume los datos reales usados, nunca inventa cifras que el proveedor no entregó."}

@app.get("/api/leon/full-scan")
def leon_full_scan(symbol:str, horizon:str="AUTO", top:int=5):
    symbol=symbol.upper().strip()

    # 1) Quote
    q=quote(symbol)
    spot=f(q.get("price"))
    change=f(q.get("change_pct"))

    # 2) Horizons
    hs=option_horizons(symbol)
    best_h=hs.get("best_horizon")
    chosen_h = (best_h or {}).get("horizon") if horizon.upper()=="AUTO" else horizon
    if not chosen_h: chosen_h="15m"
    range_score=f((best_h or {}).get("leon_range_score"),50)
    range_avg=f((best_h or {}).get("avg_return_pct"),0)
    win_rate=f((best_h or {}).get("win_rate"),0)

    # 3) Order flow proxy
    of=orderflow_proxy(symbol)
    imbalance=f(of.get("imbalance_pct"),0)
    flow_score=_clamp(50 + imbalance*1.2)

    # 4) GEX
    g=options_gex(symbol)
    net_gex=f(g.get("net_gex"),0)
    call_wall=f(g.get("call_wall"),0)
    put_wall=f(g.get("put_wall"),0)
    flip=f(g.get("gamma_flip"),0)
    # simple structure score around walls/flip
    gex_score=50
    if net_gex >= 0: gex_score += 8
    else: gex_score -= 4
    if spot and call_wall and put_wall:
        if put_wall < spot < call_wall: gex_score += 8
        if abs(spot-flip)/spot*100 <= 1.5: gex_score += 4
    gex_score=_clamp(gex_score)

    # 5) Direction + options
    direction=_direction_from_components(range_avg,imbalance,net_gex)
    opt=options_scan(symbol,direction,chosen_h,top)
    picks=opt.get("top",[])
    best=opt.get("best",{})

    # 6) Unified score per contract
    scored=[]
    for o in picks:
        option_score=f(o.get("leon_score"),50)
        liquidity_score=_clamp(
            45 +
            min(20, math.log10(max(1,f(o.get("open_interest")))+1)*5) +
            min(15, math.log10(max(1,f(o.get("volume")))+1)*4) -
            min(25, f(o.get("spread_pct"))*.7)
        )
        unified=round(_clamp(
            option_score*.42 +
            range_score*.22 +
            flow_score*.16 +
            gex_score*.12 +
            liquidity_score*.08
        ),1)
        item=dict(o)
        item.update({
            "unified_score":unified,
            "grade":_grade(unified),
            "components":{
                "options":round(option_score,1),
                "range":round(range_score,1),
                "order_flow":round(flow_score,1),
                "gex":round(gex_score,1),
                "liquidity":round(liquidity_score,1)
            }
        })
        scored.append(item)

    scored.sort(key=lambda x:(x["unified_score"],x.get("open_interest",0),x.get("volume",0)), reverse=True)
    if not scored:
        raise HTTPException(422,"No hay contratos suficientes para el análisis unificado")

    best=scored[0]
    conservative=sorted(
        scored,
        key=lambda x:(f(x.get("spread_pct"),999), -f(x.get("open_interest")), -f(x.get("unified_score")))
    )[0]
    aggressive=sorted(
        scored,
        key=lambda x:(-abs(f(x.get("delta"))), -f(x.get("unified_score")))
    )[0]

    reasons=[]
    reasons.append(f"Rango {chosen_h}: score {range_score:.0f}/100, win rate histórico {win_rate:.1f}%.")
    reasons.append(f"Order flow proxy: imbalance {imbalance:+.1f}%.")
    reasons.append(f"GEX: {'positivo' if net_gex>=0 else 'negativo'}, Call Wall {call_wall:.2f}, Put Wall {put_wall:.2f}.")
    reasons.append(f"Contrato: spread {f(best.get('spread_pct')):.1f}%, OI {int(f(best.get('open_interest')))}, volumen {int(f(best.get('volume')))}.")
    reasons.append(f"Greeks: delta {f(best.get('delta')):.2f}, theta {f(best.get('theta')):.3f}, IV {f(best.get('iv'))*100:.1f}%.")

    hub=data_hub_snapshot()
    shield=risk_shield(symbol, best, best.get("components",{}), win_rate, hub)
    providers_used=[]
    if ALPACA_KEY and ALPACA_SECRET: providers_used.append("Alpaca")
    if TD_KEY: providers_used.append("Twelve Data")
    if AV_KEY and alpha_usage_state()["used"]>0: providers_used.append("Alpha Vantage")
    robot=robot_ai_summary(symbol, direction, best.get("unified_score",0), shield, providers_used)

    return {
        "symbol":symbol,
        "spot":spot,
        "change_pct":change,
        "direction":direction,
        "best_horizon":chosen_h,
        "range_score":range_score,
        "win_rate":win_rate,
        "avg_return_pct":range_avg,
        "order_flow":{"imbalance_pct":imbalance,"score":flow_score},
        "gex":{"net_gex":net_gex,"call_wall":call_wall,"put_wall":put_wall,"gamma_flip":flip,"score":gex_score},
        "best":best,
        "conservative":conservative,
        "aggressive":aggressive,
        "top":scored[:max(1,min(top,10))],
        "reasons":reasons,
        "risk_shield":shield,
        "robot_ai":robot,
        "decision":robot["decision"],
        "data_hub":hub,
        "note":"León Score unifica opciones, rango, order-flow proxy, GEX y liquidez. No es una garantía de ganancia."
    }


@app.get("/api/chart")
def chart_data(symbol:str, timeframe:str="5m", limit:int=120):
    symbol=symbol.upper().strip()
    if symbol not in MAGNIFICENT_7:
        raise HTTPException(403,"Modo prueba: gráfico limitado a las Magníficas 7")
    tf=timeframe.lower()
    td_map={"1m":"1min","2m":"1min","3m":"1min","4m":"1min","5m":"5min","10m":"5min","15m":"15min","30m":"30min","45m":"15min","1h":"1h","2h":"1h","4h":"4h","1d":"1day","1w":"1week"}
    interval=td_map.get(tf,"5min")
    rows=[]
    provider="Twelve Data"
    try:
        d=td("time_series", {"symbol":symbol,"interval":interval,"outputsize":max(20,min(limit,500)),"order":"ASC"})
        for v in d.get("values") or []:
            try:
                rows.append({"time":v.get("datetime"),"open":float(v.get("open")),"high":float(v.get("high")),"low":float(v.get("low")),"close":float(v.get("close")),"volume":float(v.get("volume") or 0)})
            except: pass
    except Exception:
        provider="Alpha Vantage"
        intraday_map={"1m":"1min","2m":"1min","3m":"1min","4m":"1min","5m":"5min","10m":"5min","15m":"15min","30m":"30min","45m":"15min","1h":"60min","2h":"60min","4h":"60min"}
        if tf in intraday_map:
            d=av({"function":"TIME_SERIES_INTRADAY","symbol":symbol,"interval":intraday_map[tf],"outputsize":"compact"})
            key=next((k for k in d if k.startswith("Time Series")),None); ts=d.get(key,{}) if key else {}
        else:
            d=av({"function":"TIME_SERIES_DAILY","symbol":symbol,"outputsize":"compact"}); ts=d.get("Time Series (Daily)") or {}
        for t,v in ts.items():
            try: rows.append({"time":t,"open":float(v["1. open"]),"high":float(v["2. high"]),"low":float(v["3. low"]),"close":float(v["4. close"]),"volume":float(v["5. volume"])})
            except: pass
    rows.sort(key=lambda x:x["time"] or "")
    rows=rows[-max(20,min(limit,500)):]
    if not rows: raise HTTPException(422,"No hay datos de gráfico para este rango.")

    # Indicators calculated locally on returned bars.
    cum_pv=0.0; cum_v=0.0
    closes=[]
    for i,r in enumerate(rows):
        typical=(r["high"]+r["low"]+r["close"])/3
        cum_pv += typical*r["volume"]
        cum_v += r["volume"]
        r["vwap"]=cum_pv/cum_v if cum_v else r["close"]
        closes.append(r["close"])
        r["ema9"]=sum(closes[-9:])/min(9,len(closes))
        r["ema20"]=sum(closes[-20:])/min(20,len(closes))

    highs=[r["high"] for r in rows]
    lows=[r["low"] for r in rows]
    support=min(lows[-20:]) if lows else None
    resistance=max(highs[-20:]) if highs else None
    last=rows[-1]["close"]
    return {
        "symbol":symbol,
        "timeframe":timeframe,
        "bars":rows,
        "levels":{
            "support":support,
            "resistance":resistance,
            "last":last
        },
        "provider":provider,
        "note":"Velas e indicadores se calculan automáticamente con los datos disponibles del proveedor."
    }

# --- TradingView: TradingView Alert -> Webhook HTTPS -> LEONIX ---------
TRADINGVIEW_SECRET=os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip()
_TV_ALERTS=[]
TV_ALERTS_MAX=200

@app.post("/api/tradingview/webhook")
async def tradingview_webhook(request: Request, secret: str | None = None):
    if TRADINGVIEW_SECRET:
        header_secret = request.headers.get("x-leonix-secret")
        if (secret or header_secret) != TRADINGVIEW_SECRET:
            raise HTTPException(401,"Secreto de webhook inválido. Configura TRADINGVIEW_WEBHOOK_SECRET y pásalo en el mensaje de alerta o en el header X-Leonix-Secret.")
    else:
        raise HTTPException(503,"TRADINGVIEW_WEBHOOK_SECRET no configurada en el servidor. Sin secreto no se aceptan webhooks.")
    try:
        payload = await request.json()
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        payload = {"raw": raw}
    symbol = str(payload.get("symbol") or payload.get("ticker") or "").upper().strip()
    if symbol and symbol not in MAGNIFICENT_7:
        raise HTTPException(403, f"Símbolo {symbol} fuera del universo fijo de las Magníficas 7")
    alert = {
        "id": len(_TV_ALERTS)+1,
        "received_at": time.time(),
        "symbol": symbol or None,
        "action": str(payload.get("action") or payload.get("strategy") or "").upper() or None,
        "price": f(payload.get("price"), None) if payload.get("price") is not None else None,
        "message": payload.get("message") or payload.get("comment"),
        "raw": payload,
    }
    _TV_ALERTS.append(alert)
    del _TV_ALERTS[:-TV_ALERTS_MAX]
    return {"received": True, "alert": alert,
            "note": "LEONIX registró la alerta. No ejecuta nada automáticamente: pasa por Consensus Engine y Risk Shield antes de convertirse en decisión."}

@app.get("/api/tradingview/alerts")
def tradingview_alerts(limit: int = 20):
    return {"alerts": _TV_ALERTS[-max(1,min(limit,TV_ALERTS_MAX)):][::-1],
            "webhook_configured": bool(TRADINGVIEW_SECRET),
            "note": "Alertas crudas de TradingView. Todavía no se combinan automáticamente con el Consensus Score; eso es el siguiente paso."}

