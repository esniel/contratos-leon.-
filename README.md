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

## v51 — Live Data Engine (Alpaca WebSocket)
Objetivo: `Alpaca WebSocket → Magnificent 7 LIVE → Data Hub → Health/latency → Consensus → Risk Shield`, para dejar de depender solo de REST periódico.

**Decisiones de arquitectura (documentadas para que quien retome esto no tenga que adivinar):**
- La conexión WebSocket corre como una tarea `asyncio` **dentro del mismo proceso uvicorn** (`asyncio.create_task` en el evento `startup` de FastAPI), no en un worker separado. Solo se lanza si `ALPACA_KEY_ID`/`ALPACA_SECRET_KEY` están configuradas.
- Feed usado: `wss://stream.data.alpaca.markets/v2/iex` (plan free de Alpaca, datos IEX) por defecto — configurable con `ALPACA_WS_URL` si se sube a un plan con SIP.
- Se suscribe a `quotes` de las Magníficas 7. Cada mensaje actualiza `_LIVE_CACHE[symbol]` en memoria y llama `_record_health("alpaca", data_time=...)` con la hora real del tick — así el Data Hub de v50 queda alimentado con antigüedad real, no aproximada.
- Reconexión con backoff exponencial (2s → 60s máx) ante cualquier error o corte. `GET /api/live/status` expone si está conectado, cuándo llegó el último mensaje, y cuántas reconexiones lleva.
- **Fallback intacto**: `quote()` ahora prueba primero el caché LIVE (si el dato tiene menos de 20s); si no hay dato fresco, cae exactamente al mismo camino de antes (Alpaca REST → Twelve Data → Alpha Vantage). Si el WebSocket nunca conecta (red bloqueada, plan sin acceso, key inválida), LEONIX sigue funcionando igual que en v50, solo que más lento.
- **Render free tier**: si el servicio duerme por inactividad y Render lo despierta, el evento `startup` vuelve a lanzar la tarea automáticamente — no hace falta intervención manual.
- Nuevos endpoints: `GET /api/live/status` (salud de la conexión), `GET /api/live/quotes` (caché crudo, sin llamadas upstream — pensado para que el frontend haga polling rápido y barato contra esto en vez de golpear `/api/quote` repetidamente, aunque por ahora el frontend sigue usando `/api/quote`, que ya prioriza el caché LIVE internamente).
- **Pendiente, no resuelto en v51**: el frontend todavía hace polling a `/api/quote`/`/api/chart` en vez de tener su propio WebSocket o SSE hacia el navegador. Eso quedaría para cuando el "Live Data Engine" se sienta realmente en tiempo real en la interfaz, no solo en el backend.
- Nueva dependencia: `websockets==13.1` en `requirements.txt`.
- **Pruebas**: se añadieron a `test_paper_engine.py` — parseo de timestamps con nanosegundos de Alpaca, que el caché descarta datos con más de 20s, que `quote()` prioriza el caché LIVE, que la tarea de fondo no revienta si falta la librería `websockets`, y una simulación completa de un mensaje de quote real (sin red) verificando que el precio y el `market_time` se calculan correctamente. No se pudo probar la conexión real (sin credenciales ni red en este entorno) — eso solo se valida desplegando en Render con `ALPACA_KEY_ID`/`ALPACA_SECRET_KEY` reales.

## Hotfix post-v51 (detectado por prueba local del usuario)
- `HEAD /` devolvía 405 Method Not Allowed — algunos monitores/health-checks (Render incluido) mandan HEAD antes del GET real. Se cambió `/`, `/health` y `/api/health` a `@app.api_route(..., methods=["GET","HEAD"])`.
- Se corrigió que `home()` y `health()` seguían reportando `"version":"50.0"` de forma hardcodeada aunque el título de la app ya decía `51.0` — descuido mío al bumpear la versión en v51, no lo propagué a los dos endpoints que la repiten literal.
