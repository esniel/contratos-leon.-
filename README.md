# LEONIX v52.1 — Autonomous Paper/Shadow Engine

## Incluye
- Dashboard `/` corregido: sirve `index.html` en UTF-8.
- Acciones Magnificent 7 con Alpaca WebSocket IEX + REST fallback.
- Cripto 24/7: BTC/USD, ETH/USD, SOL/USD, XRP/USD.
- Selector automático ACCIONES / CRIPTO / NO TRADE.
- Data Hub y health/freshness.
- Risk Shield determinístico.
- Robot LEONIX PAPER/SHADOW.
- Adaptive Trade Manager / Profit Lock architecture.
- Paper Options endpoints con slippage/comisión y límites de posición.
- TradingView webhook como señal; nunca ejecuta directo.
- UI estilo terminal/TradingView, responsive y PWA.
- Live Trading desactivado.

## Render variables
ALPACA_KEY_ID
ALPACA_SECRET_KEY
TWELVE_DATA_API_KEY
ALPHAVANTAGE_API_KEY
TRADINGVIEW_WEBHOOK_SECRET (opcional)
ALPACA_WS_URL (opcional)
ALPACA_CRYPTO_WS_URL (opcional)

## Seguridad
No guardar API keys en GitHub. Todo lo nuevo arranca en PAPER/SHADOW. `Consensus` no es una probabilidad de ganar.

## Verificación
`python3 test_v52.py`

## v52.1 Hotfix
- Análisis atómico por símbolo: precio, velas, soporte y resistencia no pueden mezclarse.
- `SYNC ERROR` fuerza NO TRADE.
- El Robot ya no cambia silenciosamente el gráfico elegido por el usuario.
- Data Health distingue MARKET CLOSED de caída real del feed de acciones.
- UI rehecha tipo terminal/TradingView, con gráfico protagonista y panel Robot lateral.
