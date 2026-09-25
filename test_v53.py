import os, re
os.environ.pop("ALPACA_KEY_ID", None)
os.environ.pop("ALPACA_SECRET_KEY", None)
import leon_api as m

h = (m.BASE / "index.html").read_text(encoding="utf-8")
assert m.VERSION == "57.1", f"VERSION={m.VERSION}"

# Vistas principales del frontend actual
for x in ["tradingView", "scannerPanel", "portfolioView", "aiPanel"]:
    assert f'id="{x}"' in h, f"falta vista {x}"

# REGLA ABSOLUTA: ningún botón decorativo — todo <button> debe tener onclick
buttons = re.findall(r"<button[^>]*>", h)
assert buttons, "no se encontró ningún botón en el HTML"
sin_accion = [b for b in buttons if "onclick" not in b]
assert not sin_accion, f"botones decorativos (sin onclick): {sin_accion}"

# Funciones clave cableadas en la UI
for x in ["scanNow(", "loadOptions(", "loadPaper(", "refreshAnalysis(", "toggleRobot("]:
    assert x in h, f"falta cablear {x}"

# Seguridad visible
assert "Live Trading" in h and "BLOQUEADO" in h
assert "Paper Trading" in h and "Live bloqueado" in h

# El score nunca se presenta como probabilidad de ganar
assert "no es probabilidad de ganar" in h or "no probabilidad de ganar" in h, \
    "el quality score debe aclarar que no es probabilidad de ganar"

print("LEONIX v57: botones/navegación + seguridad OK")
