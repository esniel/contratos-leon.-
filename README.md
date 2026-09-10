# LEONIX v46 — Magníficas 7

Versión de prueba enfocada en AAPL, MSFT, GOOGL, AMZN, NVDA, META y TSLA.

## Incluye
- Twelve Data como fuente principal para cotización y gráficos.
- Alpha Vantage como respaldo de mercado y proveedor configurado para opciones.
- Escáner Magníficas 7 con ranking inicial y selección rápida.
- Modo Fácil / Profesional.
- Contratos CALL/PUT, Greeks, GEX y Order Flow proxy (cuando el proveedor de opciones tenga acceso compatible).
- Gráficas LEONIX, Paper Trading, Trabajar Solo, Backtesting y Chat LEONIX.
- Inicio liviano: al abrir la app no consume automáticamente la cadena de opciones; el botón ANÁLISIS COMPLETO ejecuta los módulos profundos.

## Variables en Render
- TWELVE_DATA_API_KEY
- ALPHAVANTAGE_API_KEY

## Importante
Esta versión prioriza siete acciones para probar estabilidad y flujo completo. Los scores no garantizan ganancias. Si no hay datos de opciones compatibles, la app debe mostrar que faltan datos en vez de inventar contratos.


## v46 — Contador Alpha 25/día
- Contador visible de consultas Alpha Vantage restantes.
- Reinicio diario mostrado en hora de Nueva York.
- Twelve Data sigue siendo la fuente principal para precios/gráficas; Alpha se reserva como respaldo y opciones.
- El contador refleja llamadas hechas por la instancia y reconoce cuando Alpha reporta el límite agotado.

## v47 — Reloj del mercado
- Contador Alpha Vantage 25/día.
- Debajo: cuenta regresiva automática hasta la próxima apertura regular (9:30 AM ET).
- Durante sesión: cambia automáticamente a "Mercado abierto" y cuenta hasta el cierre (4:00 PM ET).
- Fines de semana: salta a la próxima sesión de lunes a viernes.

## v49 — Data Hub + Alpaca
- Alpaca añadido como fuente principal para cotización (REST), con Twelve Data y Alpha Vantage como respaldo automático.
- Data Hub: cada proveedor reporta LIVE / DELAYED / DOWN según antigüedad del último dato bueno; endpoint `/api/data-hub/status`.
- Risk Shield ahora incluye la puerta "datos": si el Data Hub está DOWN, la puerta se pone en rojo y bloquea el contrato.
- Nuevas variables en Render: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` (modo paper).

## v49 — Risk Shield 2.0 + Paper Options Engine
- **Paper Options Engine real**: `POST /api/paper/options/open|close`, `GET /api/paper/options/account`. Usa el contrato real (strike, expiración, bid/ask, IV, delta, theta) que ya entrega `options_scan()`, con slippage (1%) y comisión ($0.65/contrato) simulados. Si un contrato ya no está en la cadena al cerrar, devuelve error 409 en vez de inventar un precio.
- Varias posiciones simultáneas, cierre individual por `position_id`, P/L abierto y realizado.
- **Risk Shield 2.0**: 9 puertas — datos (Data Hub), sesión de mercado, earnings (Alpha Vantage `EARNINGS_CALENDAR`, cacheado 6h), pérdida diaria máxima, spread, liquidez, IV, consenso entre componentes, historial, y tamaño de posición.
- Pérdida diaria máxima (3% del capital) y tamaño de posición (10% del equity) **bloquean de verdad** en `paper_options_open()` (HTTP 423 / 400), no son solo un aviso visual.
- CORS: se añadió `POST` (antes solo `GET`), necesario para abrir/cerrar posiciones.
- Frontend: el panel de Paper Trading ya no simula sobre el precio de la acción — muestra y opera el contrato real.

## v49 — Backtesting de opciones (proxy Black-Scholes)
- `GET /api/backtest/options`: como Alpha Vantage limita `HISTORICAL_OPTIONS` a 25 llamadas/día (insuficiente para backtest real), se usa precio histórico de la acción + volatilidad realizada como IV estimada, valorado con Black-Scholes.
- Se etiqueta explícitamente `"method":"proxy_black_scholes"` en la respuesta y en el frontend, para que nunca se confunda con datos reales de opciones históricas.

## v49 — TradingView (receptor de webhooks)
- `POST /api/tradingview/webhook` (requiere `TRADINGVIEW_WEBHOOK_SECRET` configurado en Render + header `X-Leonix-Secret` o parámetro `secret`).
- `GET /api/tradingview/alerts` — últimas alertas recibidas, sin ejecutar nada automáticamente todavía. Falta combinarlas con el Consensus Engine.

## v50 — Estabilización (antes de seguir sumando features)
Correcciones de calidad detectadas en revisión manual del código antes de confiar en las métricas mostradas:
- **Timestamps reales**: se corrigió un bug donde la cotización de Alpaca perdía su hora real de mercado (`t`) y se sobrescribía con la hora local de LEONIX. Ahora `quote()` devuelve `market_time` (hora real si el proveedor la da) separado de `received_at` (cuándo LEONIX la pidió).
- **Data Hub honesto**: LIVE/DELAYED/DOWN ahora usa la antigüedad real del dato cuando está disponible (Alpaca); para Twelve Data/Alpha Vantage, que no exponen esa hora en estos endpoints, se documenta explícitamente con `age_is_real_market_time: false` en vez de aparentar precisión que no existe.
- **`/health` honesto**: ya no dice "Twelve Data" fijo como proveedor principal — refleja Alpaca si está configurada, e incluye la versión real (`50.0`) y el snapshot del Data Hub.
- **Calendario NYSE**: `market_clock_state()` ahora conoce los feriados bursátiles de 2026-2027 (lista fija en `NYSE_HOLIDAYS`, hay que actualizarla cada año — no hay API de calendario integrada todavía) y ya no los trata como día hábil normal.
- **Pruebas**: `test_paper_engine.py` — 15 pruebas de humo sin red ni API keys (stubea FastAPI, inyecta datos falsos de opciones) que verifican: aritmética de slippage/comisión al abrir y cerrar, que nunca se inventa un precio si el contrato desaparece de la cadena, que el límite de pérdida diaria y el de tamaño de posición bloquean de verdad, y que el calendario de feriados funciona. Correr con `python3 test_paper_engine.py`.
