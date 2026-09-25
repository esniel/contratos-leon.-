import os
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)
import leon_api as m

assert m.VERSION == "57.1", f"VERSION={m.VERSION}"

# Risk Shield: spread sano no bloquea, spread absurdo sí bloquea
q = {"price": 76455, "bid": 76450, "ask": 76460, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=q, score=60)["blocked"] is False
bad = {"price": 76455, "bid": 70000, "ask": 80000, "market_time": m.now()}
assert m.risk_shield("BTC/USD", q=bad, score=60)["blocked"] is True

assert "BTC/USD" in m.CRYPTO and "NVDA" in m.M7
assert m.ROBOT["mode"] == "PAPER"
assert m.market_clock()["session"] in ("REGULAR", "CLOSED")

# Seguridad/sincronía en el frontend actual (v57)
h = (m.BASE / "index.html").read_text(encoding="utf-8")
assert "let symbol=null" in h, "el frontend no debe fijar un símbolo por defecto (ej. NVDA)"
assert "/api/market-selector" in h, "el modo AUTO debe escanear vía market-selector"
assert "sync_ok" in h and "DESYNC" in h, "la UI debe distinguir dato sincronizado de desincronizado"
assert "Live Trading" in h and "BLOQUEADO" in h, "live trading debe aparecer bloqueado en la UI"

r = (m.BASE / "README.md").read_text(encoding="utf-8")
assert "Live Trading desactivado" in r

print("LEONIX v57: safety/sync smoke tests OK")
