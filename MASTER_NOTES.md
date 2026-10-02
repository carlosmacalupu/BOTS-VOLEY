# MASTER V11 · MODO CONSERVADOR

Base: V10 EDGE FINDER.

Objetivo: pocas entradas, cuotas bajas y alta exigencia. El monto de apuesta no modifica el cálculo ni la probabilidad.

## /seguro
- Reutiliza PRE ANCLA; no llama de nuevo a OpenAI ni historiales.
- Reutiliza el snapshot/cache de OddsPapi.
- Mayores: categoría principal.
- Femenino: umbrales más exigentes.
- Juveniles, reservas/B y amistosos: bloqueados por defecto para modo de alta exigencia.
- Exige: calidad alta, consenso modelo/IA, probabilidad conservadora muy alta, colchón sobre break-even, edge seguro y EV conservador positivo.
- Cuotas bajas no se consideran seguras por sí solas.
- V11 reutiliza PRE ANCLA compatible de V10 para evitar una consulta OpenAI extra al desplegar.

## Validación
30/30 MASTER + 7/7 EDGE + 7/7 CONSERVADOR = 44/44.
Py_compile correcto.
