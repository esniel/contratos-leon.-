import leon_api as m
assert m.VERSION=="52.1"
q={"price":76455,"bid":76450,"ask":76460,"market_time":m.now()}
r=m.risk_shield("BTC/USD",q=q,score=60)
assert r["blocked"] is False
bad={"price":76455,"bid":70000,"ask":80000,"market_time":m.now()}
assert m.risk_shield("BTC/USD",q=bad,score=60)["blocked"] is True
assert "BTC/USD" in m.CRYPTO and "NVDA" in m.M7
assert m.ROBOT["mode"]=="PAPER"
assert m.market_clock()["session"] in ("REGULAR","CLOSED")
assert (m.BASE/"index.html").read_text(encoding="utf-8").find("SINCRONIZANDO")>0
assert "Live Trading desactivado" in (m.BASE/"README.md").read_text(encoding="utf-8")
print("LEONIX v52.1: safety/sync smoke tests OK")
