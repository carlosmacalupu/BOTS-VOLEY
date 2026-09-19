# BOTS VÓLEY V1.01 — API-SPORTS

Bot independiente de BOTS BETANO. Usa API-SPORTS Volleyball.

## Railway
Configurar SOLO como variables privadas:
- `TELEGRAM_BOT_TOKEN`
- `VOLLEY_API_KEY`

Opcionales:
- `VOLLEY_API_BASE=https://v1.volleyball.api-sports.io`
- `POLL_SECONDS=2`

No subir claves a GitHub.

## Estado de esta versión
- Catálogo de partidos de HOY (`games?date=...`)
- Búsqueda tolerante por nombres parciales
- Selección numerada
- `AHORA`
- PRE conservador usando standings cuando están disponibles
- LIVE conserva el PRE como referencia
- Caché: catálogo 5 min / standings 1 h para proteger la cuota gratuita
- No habilita intuición verde hasta completar validación temporal histórica
- Si faltan datos, no inventa porcentajes fuertes ni fuerza propuesta

Esta V1.01 es una base funcional para conectar Telegram + API-SPORTS. La calibración predictiva avanzada viene después de comprobar el flujo real de datos.
