# BOTS BETANO MASTER ÚNICO V11 · MODO CONSERVADOR

# BOTS BETANO · MASTER ÚNICO V9
## ESPECIALIZADO · ECO · SOBERANO

Nueva base reconstruida tomando la estructura modular del bot `bot_futbol_v2_7` y las mejores reglas desarrolladas en BOTS BETANO V4–V8.

## Objetivo

No obliga a encontrar una apuesta. Primero intenta entender el partido y después muestra **toda la escalera de goles** para que la decisión sea visible:

```text
📈 ESCALERA OVER FT
🟢 +0.5 · 96.4% · APROBADO
🟢 +1.5 · 93.1% · APROBADO
🟡 +2.5 · 82.7% · SIN CONFIRMAR
🔴 +3.5 · 61.2% · NO APROBADO
🎯 LÍMITE VERDE OVER: +1.5

📉 ESCALERA UNDER FT
🟡 U4.5 · 90.1%
🟢 U5.5 · 96.7%
🟢 U6.5 · 98.4%
🎯 UNDER NATURAL: U5.5
```

Todas las líneas salen de **UNA distribución final de goles**. No se permiten porcentajes paralelos que contradigan piso, techo u otras líneas.

## Qué cambia frente a Claude y frente a V8

### 1. CEREBRO SOBERANO ÚNICO
- El modelo local produce una distribución completa de goles.
- OpenAI hace una sola investigación independiente por PRE y devuelve la escalera 0.5–9.5.
- La segunda lectura se convierte en otra distribución.
- Ambas se fusionan en **una sola masa final**.
- +0.5, +1.5, +2.5…, U4.5, U5.5… piso y techo nacen de esa misma masa.
- Ya no se usa la vieja regla de “tomar siempre el porcentaje menor”.

### 2. ESTUDIO DIFERENTE SEGÚN EL TIPO DE FÚTBOL
Detección automática:
- MAYORES
- FEMENINO
- JUVENIL (U15–U23 / youth / sub)
- RESERVAS / B
- AMISTOSO

Cada perfil cambia recencia, encogimiento estadístico, localía, volatilidad, mínimo de muestra y exigencia para mostrar verde.

Juveniles/reservas: el histórico envejece rápido y pesan convocatoria/XI/generación actual.
Femenino: se vigilan diferencias de nivel, profundidad y colas de goleada.
Amistosos: se exige más confianza por rotaciones y objetivos inciertos.

### 3. CALIDAD DEL ESTUDIO
No confunde “probabilidad del mercado” con “qué tan bien pudimos estudiar el partido”.

```text
🧠 CATEGORÍA: JUVENIL
📚 CALIDAD DEL ESTUDIO: 78/100 · MEDIA
```

Un 95% en un U20 mal documentado no se trata igual que un 95% en una liga mayor con XI confirmado.

### 4. PRE ANCLA · AHORRO DE CRÉDITO
El primer PRE completo se guarda durante 6 horas.

- Repetir el mismo PRE: **0 llamadas nuevas a OpenAI y 0 historiales nuevos**.
- `DETALLE`: usa el mismo snapshot; no gasta OpenAI.
- `AHORA`: hace solo la comprobación necesaria para saber si el partido sigue PRE o pasó a LIVE.
- La búsqueda intenta primero solo HOY; ayer/mañana se consultan únicamente si no encontró el equipo.
- OddsPapi queda apagado por defecto.
- Segunda IA queda apagada por defecto.

### 5. RESILIENCIA
Si la ruta OpenAI con web falla, existe un único rescate sin web dentro del mismo presupuesto de tiempo. Si OpenAI no responde, el modelo local sigue produciendo una distribución coherente; no inventa “99 pp de discrepancia”.

### 6. PISO Y TECHO
- Piso protegido 1 cuando P(0-0) <= `FLOOR_RISK` (8% por defecto).
- Techo N cuando P(total > N) <= `TAIL` (3% por defecto).
- Si U7.5 = 96.1%, 7 NO puede declararse techo de 97%; el bot sigue buscando más arriba.

## Telegram

Escribe un equipo y elige el número. Después puedes usar:

- `AHORA` → mismo partido activo.
- `DETALLE` → auditoría del mismo PRE ancla.
- `/hoy` → partidos de hoy.
- `/vivo` → partidos en juego.
- `/stats` → resultados liquidados del historial.
- `/probar` → backtest heredado del bot modular.

## Variables Railway

Obligatorias:

```text
API_FOOTBALL_KEY
OPENAI_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Compatibilidad automática con tus variables anteriores:

```text
BETANO_TELEGRAM_BOT_TOKEN
BETANO_TELEGRAM_CHAT_ID
BETANO_DATA_DIR
```

Recomendadas:

```text
DATA_DIR=/data
OPENAI_WEB=1
PRE_ANCHOR_TTL=21600
USE_ODDS_VALIDATION=0
USE_SECOND_AI=0
TELEGRAM_PROGRESS=0
```

Monta un Volume de Railway en `/data` para conservar anclas, caché y resultados.

## Validación incluida

Ejecuta:

```bash
python test_all.py
```

La batería MASTER V9 valida, entre otras cosas:
- distribución suma 1;
- Over decreciente y Under creciente;
- +0.5 = 1 − P(0-0);
- regresión Haití: P0 6.2% no puede dar piso 0;
- techo sale de la cola real;
- clasificación mayores/femenino/juvenil/reservas/amistoso;
- PRE ancla no vuelve a pedir IA ni historiales;
- DETALLE reutiliza snapshot;
- escalera +0.5 a +5.5 visible.

Las pruebas heredadas que dependían de la vieja regla de “mínimo bot/IA/mercado” se conservan aparte en `test_legacy_claude.py` como referencia, pero esa lógica fue descartada intencionalmente.

## Importante

Este bot busca reducir contradicciones, controlar consultas y medir mejor la incertidumbre. No puede garantizar beneficios ni que una probabilidad estimada se cumpla en un partido individual. El backtest y el registro de resultados reales siguen siendo necesarios antes de aumentar montos.

## EDGE FINDER — /valor

Después de analizar y seleccionar un partido, escribe:

```
/valor
```

El comando NO vuelve a consultar OpenAI ni historiales. Reutiliza el PRE ANCLA y solo compara la distribución soberana con el mercado de OddsPapi. OddsPapi mantiene caché de 30 minutos.

Un `VACÍO` significa que la probabilidad conservadora del bot supera la probabilidad justa del mercado después de quitar el margen de la casa. Si además hay cuota decimal, calcula EV real:

- `break-even = 1 / cuota`
- `EV = p_conservadora * cuota - 1`

El filtro descuenta incertidumbre por categoría, calidad del estudio, divergencia modelo/IA y muestra. Juveniles, reservas y amistosos exigen más margen que mayores.

Estados:

- 🟢 VACÍO ROBUSTO
- 🟡 VACÍO FRÁGIL
- 🔴 SIN VACÍO

Y, separado del vacío:

- 🟢 VERDE SEGURO
- 🟢 VERDE VALOR
- 🟡 VALOR SIN ENTRADA
- 🔴 NO ENTRAR

No se presentan varias líneas Over correlacionadas como si fueran ventajas independientes: la selección no redundante conserva como máximo un Over y un Under por familia.

## MODO CONSERVADOR V11 — /seguro

Pensado para usuarios que prefieren pocas entradas y cuotas bajas. No interpreta una cuota baja como segura.
Reutiliza el PRE ANCLA y no vuelve a gastar OpenAI ni historiales. Solo pide/reutiliza el snapshot de mercado.

Filtros simultáneos: calidad del estudio, consenso modelo/IA, probabilidad conservadora, colchón sobre break-even,
edge seguro, EV conservador y corredor de cuota baja. Mayores es la categoría principal. Femenino usa exigencia mayor.
Juveniles, reservas/B y amistosos están bloqueados por defecto para este modo por su mayor volatilidad.

El bot no recomienda un monto de apuesta. El tamaño de la apuesta no modifica la probabilidad del evento.
