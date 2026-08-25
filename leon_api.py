
import os, math, statistics, requests, time
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo

app = FastAPI(title="Contratos León Real Data API", version="47.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])

AV_KEY=os.getenv("ALPHAVANTAGE_API_KEY","").strip()
TD_KEY=os.getenv("TWELVE_DATA_API_KEY","").strip()

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

def td(endpoint, params=None):
    if not TD_KEY:
        raise HTTPException(503,"TWELVE_DATA_API_KEY no configurada")
    p=dict(params or {})
    p["apikey"]=TD_KEY
    r=requests.get(f"https://api.twelvedata.com/{endpoint}",params=p,timeout=20)
    r.raise_for_status()
    d=r.json()
    if isinstance(d,dict) and d.get("status")=="error":
        raise HTTPException(502,d.get("message","Twelve Data error"))
    return d

def av(params):
    if not AV_KEY:
        raise HTTPException(503,"ALPHAVANTAGE_API_KEY no configurada")
    st=alpha_usage_state()
    if st["remaining"]<=0:
        raise HTTPException(429,"Límite diario Alpha Vantage agotado (25/25). Usa Twelve Data o espera el reinicio diario.")
    params=dict(params); params["apikey"]=AV_KEY
    _AV_USAGE["used"]+=1
    r=requests.get("https://www.alphavantage.co/query",params=params,timeout=25)
    r.raise_for_status()
    d=r.json()
    if "Error Message" in d: raise HTTPException(404,d["Error Message"])
    if "Note" in d or "Information" in d:
        msg=d.get("Note") or d.get("Information")
        if "25 requests per day" in str(msg).lower() or "rate limit" in str(msg).lower():
            _AV_USAGE.update({"used":AV_DAILY_LIMIT,"limit_hit":True})
        raise HTTPException(429,msg)
    return d

@app.get("/")
def home():
    return {"ok":True,"service":"Contratos León API","version":"47.0","docs":"/docs"}

@app.get("/health")
@app.get("/api/health")
def health():
    return {"ok":True,"alpha_vantage":bool(AV_KEY),"twelve_data":bool(TD_KEY),"market_provider":"Twelve Data" if TD_KEY else "Alpha Vantage","options_provider":"Alpha Vantage","alpha_daily":alpha_usage_state()}

@app.get("/api/alpha-usage")
def alpha_usage():
    return alpha_usage_state()

def market_clock_state():
    now=datetime.now(NY_TZ)
    open_t=now.replace(hour=9,minute=30,second=0,microsecond=0)
    close_t=now.replace(hour=16,minute=0,second=0,microsecond=0)
    # Sesión regular: lunes-viernes. Feriados bursátiles se incorporarán en una capa de calendario posterior.
    if now.weekday()<5 and open_t <= now < close_t:
        target=close_t
        status="open"
        label="Cierre hoy · 4:00 PM ET"
    else:
        status="closed"
        if now.weekday()<5 and now < open_t:
            target=open_t
        else:
            d=(now+timedelta(days=1)).date()
            while d.weekday()>=5:
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
        "regular_hours":"09:30-16:00 ET"
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
    try:
        d=td("quote", {"symbol":symbol})
        price=float(d.get("close") or d.get("price") or 0)
        pct=float(d.get("percent_change") or 0)
        if price>0:
            return {"symbol":symbol,"price":price,"change_pct":pct,"provider":"Twelve Data"}
    except Exception:
        pass
    d=av({"function":"GLOBAL_QUOTE","symbol":symbol}).get("Global Quote",{})
    if not d: raise HTTPException(404,"Sin cotización")
    return {"symbol":symbol,"price":float(d.get("05. price",0) or 0),"change_pct":float(str(d.get("10. change percent","0")).replace("%","") or 0),"provider":"Alpha Vantage"}

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
