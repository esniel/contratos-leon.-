# Contratos León v38 — Full Intelligence

Incluye:
- Escáner automático 1m → 1w
- Mejor rango automático
- Cadena CALL/PUT
- León Score unificado:
  * Options score 42%
  * Range score 22%
  * Order-flow proxy 16%
  * GEX 12%
  * Liquidez 8%
- GEX / Net GEX / Call Wall / Put Wall / Gamma Flip
- Order-flow proxy
- Mejor / Conservadora / Agresiva
- Explicación del porqué
- Top contratos ordenados por score unificado

Importante:
- REALTIME_OPTIONS requiere un proveedor compatible/plan adecuado.
- El order-flow actual es proxy de precio/volumen, NO Bookmap L2/MBO real.
- Ningún score garantiza ganancias.


## v41 — Gráficas León
- Pantalla tipo trading
- Velas automáticas
- Volumen
- VWAP
- EMA 9 / EMA 20
- Soporte y resistencia
- Entrada / objetivo / invalidación
- Rango 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W
- Auto-refresh cada 15 segundos en el frontend
- Endpoint /api/chart

La visualización es analítica; no ejecuta órdenes.


## v42 — Paper Trading León
- Cuenta simulada inicial de $10,000
- Saldo inicial editable
- Monto manual por operación
- Asignación automática por porcentaje
- LONG/CALL y SHORT/PUT simulados
- Apertura y cierre de posiciones falsas
- P/L abierto en vivo
- P/L realizado
- Balance, efectivo, capital invertido y retorno
- Historial de operaciones
- Marcado automático con el precio de la gráfica

No envía órdenes reales ni mueve dinero.
