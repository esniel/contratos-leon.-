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
- Durante sesión: cambia automáticamente a “Mercado abierto” y cuenta hasta el cierre (4:00 PM ET).
- Fines de semana: salta a la próxima sesión de lunes a viernes.
