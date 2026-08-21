
import os, math, statistics, requests
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Contratos León Real Data API", version="30.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])

AV_KEY=os.getenv("ALPHAVANTAGE_API_KEY","").strip()
POLYGON_KEY=os.getenv("POLYGON_API_KEY","").strip() or os.getenv("MASSIVE_API_KEY","").strip()

def av(params):
    if not AV_KEY: raise HTTPException(503,"ALPHAVANTAGE_API_KEY no configurada")
    params=dict(params); params["apikey"]=AV_KEY
    r=requests.get("https://www.alphavantage.co/query",params=params,timeout=20)
    r.raise_for_status()
    data=r.json()
    if "Error Message" in data: raise HTTPException(404,data["Error Message"])
    if "Note" in data or "Information" in data: raise HTTPException(429,data.get("Note") or data.get("Information"))
    return data

def polygon(path, params=None):
    if not POLYGON_KEY: raise HTTPException(503,"POLYGON_API_KEY/MASSIVE_API_KEY no configurada")
    p=dict(params or {}); p["apiKey"]=POLYGON_KEY
    r=requests.get("https://api.polygon.io"+path,params=p,timeout=20)
    r.raise_for_status(); return r.json()

@app.get("/api/health")
def health():
    return {"ok":True,"alpha_vantage":bool(AV_KEY),"polygon_massive":bool(POLYGON_KEY)}

@app.get("/api/search")
def search(q:str=Query(...,min_length=1,max_length=32)):
    if AV_KEY:
        d=av({"function":"SYMBOL_SEARCH","keywords":q})
        out=[]
        for x in d.get("bestMatches",[])[:12]:
            out.append({"symbol":x.get("1. symbol"),"name":x.get("2. name"),"type":x.get("3. type"),"region":x.get("4. region")})
        return {"items":out,"provider":"Alpha Vantage"}
    raise HTTPException(503,"Configura ALPHAVANTAGE_API_KEY")

@app.get("/api/quote")
def quote(symbol:str):
    symbol=symbol.upper().strip()
    if POLYGON_KEY:
        d=polygon(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
        t=d.get("ticker",{})
        day=t.get("day",{}); prev=t.get("prevDay",{})
        price=t.get("lastTrade",{}).get("p") or day.get("c")
        prevc=prev.get("c")
        ch=((price-prevc)/prevc*100) if price and prevc else None
        return {"symbol":symbol,"price":price,"change_pct":ch,"provider":"Polygon/Massive"}
    d=av({"function":"GLOBAL_QUOTE","symbol":symbol}).get("Global Quote",{})
    if not d: raise HTTPException(404,"Sin cotización")
    return {"symbol":symbol,"price":float(d.get("05. price",0)),"change_pct":float(str(d.get("10. change percent","0")).replace("%","")),"provider":"Alpha Vantage"}

@app.get("/api/news")
def news(symbol:str):
    d=av({"function":"NEWS_SENTIMENT","tickers":symbol.upper(),"limit":30})
    items=[]
    for x in d.get("feed",[])[:20]:
        items.append({"title":x.get("title"),"url":x.get("url"),"source":x.get("source"),"time_published":x.get("time_published"),"summary":x.get("summary"),"sentiment":x.get("overall_sentiment_label")})
    return {"items":items,"provider":"Alpha Vantage"}

def daily_series(symbol):
    d=av({"function":"TIME_SERIES_DAILY_ADJUSTED","symbol":symbol,"outputsize":"full"})
    ts=d.get("Time Series (Daily)") or {}
    rows=[]
    for day,v in ts.items():
        try: rows.append((day,float(v["4. close"]),float(v["6. volume"])))
        except: pass
    rows.sort()
    return rows

def intraday_series(symbol, interval):
    d=av({"function":"TIME_SERIES_INTRADAY","symbol":symbol,"interval":interval,"outputsize":"full"})
    key=next((k for k in d if k.startswith("Time Series")),None)
    ts=d.get(key,{}) if key else {}
    rows=[]
    for t,v in ts.items():
        try: rows.append((t,float(v["4. close"]),float(v["5. volume"])))
        except: pass
    rows.sort()
    return rows

def horizon_steps(h):
    return {"1m":1,"2m":2,"3m":3,"4m":4,"5m":5,"10m":2,"15m":3,"30m":6,"45m":9,"1h":12,
            "2h":24,"4h":48,"1d":1,"3d":3,"1w":5}.get(h,3)

@app.get("/api/backtest")
def backtest(symbol:str,horizon:str="15m",lookback:int=600):
    symbol=symbol.upper().strip()
    intraday=horizon.endswith("m") or horizon.endswith("h")
    if intraday:
        # Alpha Vantage intraday availability depends on plan; 5min is used as a common base.
        rows=intraday_series(symbol,"5min")
    else:
        rows=daily_series(symbol)
    if len(rows)<80: raise HTTPException(422,"Histórico insuficiente para backtest")
    rows=rows[-max(100,min(lookback,len(rows))):]
    closes=[r[1] for r in rows]; vols=[r[2] for r in rows]
    step=horizon_steps(horizon)
    samples=wins=0; rets=[]
    # Simple transparent baseline: momentum + volume confirmation. Replace with León production rules later.
    for i in range(20,len(rows)-step):
        ma5=sum(closes[i-4:i+1])/5
        ma20=sum(closes[i-19:i+1])/20
        avgvol=sum(vols[i-19:i+1])/20
        signal=(closes[i]>ma5>ma20 and vols[i]>=avgvol)
        if not signal: continue
        samples+=1
        r=(closes[i+step]/closes[i]-1)*100
        rets.append(r)
        if r>0: wins+=1
    wr=(wins/samples*100) if samples else 0
    return {"symbol":symbol,"horizon":horizon,"samples":samples,"wins":wins,"win_rate":wr,
            "avg_return_pct":statistics.mean(rets) if rets else 0,
            "note":"Backtest histórico real de una regla base de momentum+volumen. No garantiza resultados futuros.",
            "provider":"Alpha Vantage"}
