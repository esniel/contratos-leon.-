# Contratos León v31 — Render simplificado

## GitHub Pages
La aplicación web sigue en `index.html` y funciona desde la rama `principal`.

## Render Web Service
Configura:
- Language: Python
- Branch: principal
- Root Directory: vacío
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn leon_api:app --host 0.0.0.0 --port $PORT`
- Instance: Free (para pruebas)
- Environment Variable:
  - `ALPHAVANTAGE_API_KEY` = tu clave privada

No publiques la clave en GitHub.

Cuando Render termine, copia la URL HTTPS del servicio y colócala en `config.js`:
`window.CONTRATOS_LEON_API = "https://TU-SERVICIO.onrender.com";`

Luego vuelve a subir `config.js` a GitHub y GitHub Pages quedará conectado con el backend real.
