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

## Auditoría y reconstrucción (Claude, sept. 2026)
- Interfaz reconstruida sobre la referencia visual aprobada: header dorado, watchlist, gráfico central, panel LEÓN IA, vistas Trading/Scanner/Portafolio — todo conectado a endpoints reales, sin Noticias/Configuración (no hay backend real detrás todavía).
- `GET /api/options/quote`: cierra el hueco de opciones reales (Alpha Vantage REALTIME_OPTIONS, elige el contrato ATM con bid/ask usable). `paper_options_open` ya no acepta un premium inventado sin fuente.
- Bug corregido: la interfaz mandaba `tf=` a `/api/analysis`, que espera `interval=` — los botones de timeframe no hacían nada.
- `daily_loss_limit_pct` pasó de ser un número decorativo a aplicarse de verdad (HTTP 423 si se supera).
- Robot corriendo en segundo plano (`robot_loop`, cada 30s) — antes solo escaneaba cuando alguien pedía `/api/market-selector`. `DECISIONS` pasó de lista muerta a historial real (`/api/robot/decisions`).
- **Adaptive Trade Manager real** (`trade_manager_loop`, cada 20s): stop inicial -50%, TP1 +30% (sube el stop a breakeven), TP2 +60% (sube el stop a +20%, activa trailing), TP3 +100% (toma ganancia total), trailing -20% desde el máximo, salida por tiempo a las 6h. Antes `/api/adaptive-manager/status` solo devolvía una lista de nombres de etapas sin lógica detrás.
- Se eliminaron `test_v52.py`, `test_v52_1.py`, `test_v53.py` y `test_paper_engine.py`: probaban versiones anteriores del código (otra firma de función, otra navegación) y fallaban contra el archivo real. Reemplazados por `test_v55.py` (23 pruebas, todas contra el código actual).
