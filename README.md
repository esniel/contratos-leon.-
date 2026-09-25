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
Con dependencias instaladas (`pip install -r requirements.txt`):
`python3 test_v52.py`, `python3 test_v52_1.py`, `python3 test_v53.py`.
Sin dependencias (usa stubs): `python3 test_paper_engine.py`.

## v52.1 Hotfix
- Análisis atómico por símbolo: precio, velas, soporte y resistencia no pueden mezclarse.
- `SYNC ERROR` fuerza NO TRADE.
- El Robot ya no cambia silenciosamente el gráfico elegido por el usuario.
- Data Health distingue MARKET CLOSED de caída real del feed de acciones.
- UI rehecha tipo terminal/TradingView, con gráfico protagonista y panel Robot lateral.


## v57 Auto Scanner
- No fixed NVDA startup.
- AUTO mode selects the highest-quality current opportunity from /api/market-selector.
- Scanner refreshes in place; manual symbol selection temporarily stops auto-follow.
- New /api/options/opportunities ranks real Alpaca option snapshots by quote quality, delta fit, activity and DTE. The score is not probability of profit.
- PAPER/SHADOW only; live trading remains blocked.

## v57.2 Terminal Pro (rediseño del frontend)

- Solo presentación: el backend y el loop del robot no cambian.
- Nueva interfaz estilo terminal profesional, oscura y densa pero legible en móvil.
- Franja fija de estado del robot: estado · símbolo/dirección · tiempo de sesión · nivel de riesgo · P/L no realizado.
- Gráfico de velas protagonista con EMA20/EMA50 calculadas en cliente y barras de volumen; insignia SYNC/DESYNC visible.
- Paneles RSI(14) y MACD(12,26,9) calculados en cliente desde las velas reales.
- Ranking de oportunidades con quality score y nota de señal honesta (ej. "Señal: Fuerte · spread 0.08%").
- Risk Shield detallado: límite de pérdida diaria, stop automático, exposición y gates.
- Cuenta paper: equity, disponible, P/L del día, winrate y drawdown (vía /api/metrics).
- Notas del bot con la última decisión en lenguaje claro + diario completo en modal.
- Navegación inferior: Inicio, Señales, Portafolio, Riesgo, Perfil — todo funcional, ningún botón decorativo.
- El quality score sigue declarado como calidad técnica relativa: no es probabilidad de ganar.

## v57.1 Robot automático (loop completo)

El robot ejecuta en segundo plano, cada `ROBOT_INTERVAL_SEC` segundos, el ciclo completo:

**DATOS -> SCANNER -> ANÁLISIS -> CONSENSUS -> RISK SHIELD -> PAPER TRADE -> ADAPTIVE MANAGER -> JOURNAL -> MÉTRICAS**

- **PAPER/SHADOW únicamente.** Live trading sigue bloqueado. Sin oportunidad suficientemente buena: **WAIT / NO TRADE**.
- **Estados del robot:** WAIT, ARMED, ACTIVE, PROFIT LOCK, EXIT (+ PAUSED si el usuario lo pausa, BLOCKED si se alcanza el límite de pérdida diaria).
- **Consensus:** requiere tendencia BULL y score técnico >= `CONSENSUS_MIN_SCORE` (70 por defecto) en velas reales de 15m. Sin velas: NO TRADE.
- **Risk Shield puede cancelar cualquier decisión** (spread, frescura <90s, sesión, límite 10% por posición, límite de pérdida diaria 3% — ahora con enforcement automático).
- **Sizing:** riesgo del 1% del equity por operación, tope 10% del equity por posición, 1 posición abierta a la vez (v1, solo LONG).
- **Adaptive Trade Manager:** stop estructural (o 1% fallback), TP1/TP2/TP3 por múltiplos de R (1.5R/2.5R/4R), profit lock a breakeven en TP1, trailing stop 1.5% desde TP2, time exit a los 120 min. Si el dato está viejo: la posición se marca STALE y no se opera a ciegas.
- **Journal:** cada decisión queda en `/api/robot/decisions`. **Métricas** en `/api/metrics`: sample size, win rate, profit factor, expectancy, drawdown. El score interno nunca es probabilidad de ganar.

### Variables de entorno (opcionales, todas con valor seguro por defecto)
| Variable | Default | Descripción |
|---|---|---|
| `ROBOT_INTERVAL_SEC` | 180 | Segundos entre ciclos (mínimo 30) |
| `CONSENSUS_MIN_SCORE` | 70 | Score técnico mínimo para armar |
| `MAX_OPEN_POSITIONS` | 1 | Posiciones spot simultáneas |
| `RISK_PER_TRADE_PCT` | 1.0 | % del equity arriesgado por trade |
| `TRAIL_PCT` | 1.5 | Trailing stop % desde el máximo |
| `TIME_EXIT_MIN` | 120 | Salida por tiempo (minutos) |

### Costo de datos
Cada ciclo con evaluación de entrada consume ~1 llamada a Twelve Data (velas 15m). Con 180s por ciclo: ~480 llamadas/día, dentro del plan gratuito de Twelve Data (800/día). Los quotes van por Alpaca (caché + REST).

### Honestidad operativa
El loop corre **mientras el proceso viva**. En Render Free el proceso puede dormir por inactividad: no se afirma autonomía 24/7. Para operación continua real hace falta un worker persistente o un servidor always-on.

### Tests
`test_robot_loop.py`: 44 checks del loop (entrada, stop, profit lock, trailing, TP3, time exit, STALE, límite diario, métricas, journal). Suite completa: `test_v52.py`, `test_v52_1.py`, `test_v53.py`, `test_paper_engine.py`, `test_robot_loop.py` — todo en verde.
