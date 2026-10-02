"""Segunda opinión independiente con OpenAI (Responses API) y cotejo con el modelo.

La IA NO ve los números del bot: razona sola (con búsqueda web si está activada) y devuelve
probabilidades. El bot las coteja con las suyas y se queda con la lectura más prudente.
Si la IA falla, el bot lo dice y no promete más de "DUDOSO".
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

URL = "https://api.openai.com/v1/responses"
DISAGREE = 0.08   # diferencia (en probabilidad) a partir de la cual se considera discrepancia


def digest_text(data: Optional[dict]) -> str:
    """Bloque con los datos reales de los equipos (los calcula el bot a partir de API-Football)."""
    if not data:
        return ""
    L = ["DATOS REALES DE LOS EQUIPOS (calculados por mí a partir de resultados oficiales; úsalos como base de tu estimación):"]
    for side in ("home", "away"):
        d = data.get(side)
        if d:
            L.append(f"- {d['name']}: últimos {d['n']} partidos (más reciente primero, L=local V=visitante): {d['results']}. "
                     f"Con al menos 1 gol en el partido: {d['with_goal']} de {d['n']}; 0-0: {d['zero']}; "
                     f"goles a favor por partido {d['gf']:.2f}, en contra {d['ga']:.2f}.")
    for t in data.get("context") or []:
        L.append(f"- Contexto: {t}")
    cat = data.get("category") or {}
    if cat:
        L.append(f"- TIPO DE PARTIDO: {cat.get('label','')} · regla de estudio: {cat.get('note','')}")
        if cat.get("code") in ("youth", "reserve"):
            L.append("- Para esta categoría NO trates el historial de hace meses/años como si fuera la misma plantilla: prioriza convocatoria, edad/generación, XI actual y forma muy reciente.")
        elif cat.get("code") == "women":
            L.append("- En femenino revisa especialmente diferencia real de nivel, profesionalización, profundidad de plantel y frecuencia de goleadas en ESA competición.")
        elif cat.get("code") == "friendly":
            L.append("- En amistoso penaliza la confianza si hay rotaciones, cargas físicas u objetivos no competitivos.")
    news = data.get("news") or {}
    for t, ps in (news.get("injuries") or {}).items():
        L.append(f"- Bajas/lesionados de {t}: {', '.join(ps[:8])}.")
    for t, xi in (news.get("lineups") or {}).items():
        L.append(f"- Alineación confirmada de {t}: {', '.join(xi)}.")
    L.append("Si tienes búsqueda web, úsala solo para confirmar alineaciones, bajas y contexto; no contradigas estos datos sin una razón concreta y verificable.")
    return "\n".join(L) + "\n"


def build_prompt(fx: dict, over_lines: List[float], under_lines: List[float],
                 live: Optional[dict] = None, data: Optional[dict] = None) -> str:
    estado = "El partido AÚN NO EMPIEZA."
    if live:
        estado = (f"El partido ESTÁ EN JUEGO: minuto {live['elapsed']}, marcador actual "
                  f"{fx['home']} {live['cur_h']} - {live['cur_a']} {fx['away']}.")
        if live.get("reds_txt"):
            estado += f" Expulsiones: {live['reds_txt']}."
        if live.get("stats_txt"):
            estado += f" Ritmo del partido hasta ahora: {live['stats_txt']}."
    over = ", ".join(f"{l:.1f}" for l in over_lines)
    under = ", ".join(f"{l:.1f}" for l in under_lines)
    return f"""Eres analista de fútbol. Estima probabilidades de goles TOTALES del partido a los 90 minutos (sin prórroga ni penales).

PARTIDO: {fx['home']} vs {fx['away']}
COMPETICIÓN: {fx['league']} · {fx['country']}
{estado}

{digest_text(data)}
Busca en la web información actual y fiable: forma reciente, goles y 0-0 recientes de ambos, alineaciones y bajas confirmadas, rotaciones, necesidad de puntos, fase y reglas del torneo, estilo de los entrenadores, sede/clima. NO uses cuotas ni pronósticos de casas de apuestas. NO inventes datos: si algo no aparece, dilo en "faltan_datos".

Da la probabilidad de que el total final de goles sea:
- MÁS de cada línea: {over}
- MENOS de cada línea: {under}
{'Los goles ya marcados cuentan en el total.' if live else ''}
Sé calibrado: casi nunca justifica pasar de 97%, y en partidos inciertos debes bajar la cifra.
Antes de responder considera el CONTEXTO: qué se juega cada equipo (título, descenso, clasificado, partido sin importancia, ida/vuelta de copa), si habrá rotaciones o descanso corto, bajas de delanteros o portero, clima y si es amistoso. Si algo de eso cambia el número, ajusta tu probabilidad y dilo en «resumen» o «riesgos».

Responde SOLO con un JSON válido, sin texto extra, con esta forma exacta:
{{"over": {{"<linea>": <prob 0-1>}}, "under": {{"<linea>": <prob 0-1>}}, "calidad_estudio": <0-100>, "perfil": "ESTABLE|VOLATIL|BIFURCADO|ABIERTO|CERRADO", "resumen": "<máx. 2 frases>", "riesgos": ["<máx. 3 cortos>"], "faltan_datos": ["<opcional>"]}}

La calidad_estudio NO es probabilidad de acertar: mide cuánto material fiable existe para estudiar ESTE partido.
Si es juvenil/reservas y no encuentras convocatoria o XI actual, baja calidad_estudio aunque haya historial."""


def _extract_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    out = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                    out.append(c["text"])
    return "\n".join(out)


def parse_opinion(text: str) -> Optional[dict]:
    """Extrae y valida el JSON. Devuelve None si no es utilizable."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None

    def probs(d) -> Dict[float, float]:
        out = {}
        if isinstance(d, dict):
            for k, v in d.items():
                try:
                    line, p = float(str(k).replace(",", ".")), float(v)
                except (TypeError, ValueError):
                    continue
                if p > 1.0 and p <= 100.0:   # por si manda 95 en vez de 0.95
                    p /= 100.0
                if 0.0 <= p <= 1.0:
                    out[line] = p
        return out

    over, under = probs(obj.get("over")), probs(obj.get("under"))
    if not over and not under:
        return None
    # coherencia: "más de" no puede subir con la línea; "menos de" no puede bajar
    prev = 1.0
    for l in sorted(over):
        over[l] = prev = min(over[l], prev)
    prev = 0.0
    for l in sorted(under):
        under[l] = prev = max(under[l], prev)
    try:
        quality = int(float(obj.get("calidad_estudio")))
        quality = max(0, min(100, quality))
    except (TypeError, ValueError):
        quality = None
    profile = str(obj.get("perfil") or "").upper()[:20]
    return {"over": over, "under": under,
            "quality": quality, "profile": profile,
            "resumen": str(obj.get("resumen") or "")[:300],
            "riesgos": [str(x)[:120] for x in (obj.get("riesgos") or [])][:3],
            "faltan": [str(x)[:120] for x in (obj.get("faltan_datos") or [])][:3]}


class OpenAIClient:
    def __init__(self, key: str, model: str, cache, use_web: bool = True, timeout: float = 75,
                 http: Optional[Callable] = None, ttl_pre: float = 6 * 3600, ttl_live: float = 240,
                 deadline: float = 60, fail_memory: float = 120):
        self.key, self.model, self.cache = key, model, cache
        self.use_web, self.timeout = use_web, timeout
        self._http = http or requests.post
        self.ttl_pre, self.ttl_live = ttl_pre, ttl_live
        self.last_error = ""
        self.deadline = deadline          # tope DURO total (segundos) por consulta, pase lo que pase
        self.fail_memory = fail_memory    # tras un fallo, no reintentar durante estos segundos
        self._failed: Dict[str, tuple] = {}

    def _with_deadline(self, fn: Callable, *args, limit: Optional[float] = None):
        """Ejecuta fn en un hilo con tope duro. `limit` permite repartir el presupuesto entre web y rescate."""
        box: dict = {}

        def run():
            try:
                box["v"] = fn(*args)
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self.deadline if limit is None else max(1.0, float(limit)))
        if t.is_alive():
            raise TimeoutError("tiempo agotado")
        if "e" in box:
            raise box["e"]
        return box["v"]

    def _call(self, prompt: str, web: bool) -> str:
        body = {"model": self.model, "input": prompt}
        if web:
            body["tools"] = [{"type": "web_search"}]
        r = self._http(URL, headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                       json=body, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return _extract_text(r.json())

    def opinion(self, fx: dict, over_lines: List[float], under_lines: List[float],
                live: Optional[dict] = None, data: Optional[dict] = None) -> Optional[dict]:
        """None si la IA falla (queda el motivo en self.last_error). Con caché: misma consulta, misma respuesta."""
        self.last_error = ""
        state = f"live|{live['elapsed'] // 5}|{live['cur_h']}-{live['cur_a']}|r{live.get('reds', 0)}" if live else "pre"
        key = "ai|" + hashlib.sha1(json.dumps([fx["id"], state, over_lines, under_lines, self.model, self.use_web, "v9-unico", data]).encode()).hexdigest()
        cached = self.cache.get(key, self.ttl_live if live else self.ttl_pre)
        if cached is not None:
            return _restore(cached)
        bad = self._failed.get(key)
        if bad and time.time() - bad[0] < self.fail_memory:
            self.last_error = bad[1]
            return None
        prompt = build_prompt(fx, over_lines, under_lines, live, data)
        text = None
        try:
            try:
                first_budget = self.deadline * (0.72 if self.use_web else 1.0)
                text = self._with_deadline(self._call, prompt, self.use_web, limit=first_budget)
            except Exception as first:
                # Resiliencia: si la ruta web falla, usa UNA sola recuperación sin web.
                # No repite indefinidamente ni convierte un fallo externo en una discrepancia ficticia.
                if not self.use_web:
                    raise
                try:
                    text = self._with_deadline(self._call, prompt, False, limit=max(5.0, self.deadline * 0.28))
                except Exception:
                    raise first
        except Exception as e:  # red, 401, 429, timeout...
            self.last_error = (str(e) or type(e).__name__)[:80]
            self._failed[key] = (time.time(), self.last_error)
            return None
        op = parse_opinion(text)
        if op is None:
            self.last_error = "respuesta no válida"
            return None
        self.cache.set(key, _dump(op))
        return op


class ClaudeClient(OpenAIClient):
    """Segunda IA, de otro proveedor (Anthropic). Misma interfaz y misma prudencia que OpenAIClient."""
    URL = "https://api.anthropic.com/v1/messages"

    def _call(self, prompt: str, web: bool) -> str:
        body = {"model": self.model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]}
        if web:
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
        r = self._http(self.URL, headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"}, json=body, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return "\n".join(b.get("text", "") for b in (r.json().get("content") or []) if b.get("type") == "text")


def merge_opinions(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """Une dos opiniones de IA con prudencia: por cada línea manda la probabilidad MÁS baja de las dos."""
    if a is None or b is None:
        return a or b
    def mn(x: dict, y: dict) -> dict:
        return {l: min(x[l], y[l]) if l in x and l in y else x.get(l, y.get(l)) for l in set(x) | set(y)}
    return {"over": mn(a["over"], b["over"]), "under": mn(a["under"], b["under"]),
            "resumen": (a.get("resumen") or b.get("resumen") or ""),
            "riesgos": list(dict.fromkeys((a.get("riesgos") or []) + (b.get("riesgos") or [])))[:3],
            "faltan": list(dict.fromkeys((a.get("faltan") or []) + (b.get("faltan") or [])))[:3],
            "n_ias": 2}


def _dump(op: dict) -> dict:
    return {**op, "over": {str(k): v for k, v in op["over"].items()}, "under": {str(k): v for k, v in op["under"].items()}}


def _restore(d: dict) -> dict:
    return {**d, "over": {float(k): v for k, v in d["over"].items()}, "under": {float(k): v for k, v in d["under"].items()}}


def cross_check(p_model: float, p_ai: Optional[float]):
    """-> (probabilidad prudente, hay_discrepancia). Sin IA: (p_model, False)."""
    if p_ai is None:
        return p_model, False
    return min(p_model, p_ai), abs(p_model - p_ai) > DISAGREE
