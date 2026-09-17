import leon_api as m
h=(m.BASE/"index.html").read_text(encoding="utf-8")
assert m.VERSION=="53.0"
for x in ["homePage","graphPage","robotPage","opsPage","settingsPage"]: assert x in h
for x in ["go(","loadOps(","loadAnalysis("]: assert x in h
assert "Live Trading" in h and "BLOQUEADO" in h
print("LEONIX v53: botones/navegación + seguridad OK")
