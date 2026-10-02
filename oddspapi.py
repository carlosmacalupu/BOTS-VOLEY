"""Diagnóstico de OddsPapi (plan gratis: 250 consultas al MES).

Solo sirve para descubrir, con 4 consultas como máximo, qué devuelve tu cuenta:
  1) /bookmakers  -> slug de Betano y de Pinnacle
  2) /fixtures    -> un partido de fútbol cercano
  3) /fixtures/odds -> sus cuotas (ids de mercado y precios)
  4) /markets     -> qué significa cada id (totales de goles)
La clave va en la cabecera X-API-Key (no en la URL) para que no aparezca en logs.
"""
from __future__ import annotations

import json
import time
from typing import Callable, List, Optional

import requests

BASES = ["https://v5.oddspapi.io/en", "https://api.oddspapi.io/v4"]


class OddsError(Exception):
    pass


class OddsPapi:
    def __init__(self, key: str, base: str = "", http: Optional[Callable] = None, cache=None):
        self.cache = cache          # Cache de apifootball (get/set); opcional
        self.key = (key or "").strip().strip("'\"").strip()    # sin espacios ni comillas pegadas por error
        self.bases = [base.rstrip("/")] if base else list(BASES)
        self._http = http or requests.get
        self.used = 0
        self._mode = "header"
        if cache is not None and not base:           # recordar qué base y forma de clave funcionaron (no gastar consultas probando)
            saved = cache.get("oddsauth", 30 * 86400)
            if saved:
                self.bases, self._mode = [saved[0]], saved[1]

    def get(self, path: str, params: Optional[dict] = None, ttl: float = 0):
        """Prueba la clave en cabecera y, si da 401/403, como parámetro apiKey; y las dos versiones de la API.
        Con ttl>0 y caché en disco, lo que casi no cambia (casas, mercados) no gasta consultas."""
        ckey = "odds|" + path + json.dumps(params or {}, sort_keys=True)
        if ttl and self.cache is not None:
            hit = self.cache.get(ckey, ttl)
            if hit is not None:
                return hit
        data = self._get(path, params)
        if ttl and self.cache is not None:
            self.cache.set(ckey, data)
        return data

    def _get(self, path: str, params: Optional[dict] = None):
        last = ""
        for base in self.bases:
            for mode in (self._mode, "query" if self._mode == "header" else "header"):
                self.used += 1
                headers = {"X-API-Key": self.key} if mode == "header" else {}
                q = dict(params or {})
                if mode == "query":
                    q["apiKey"] = self.key
                r = self._http(base + path, headers=headers, params=q, timeout=25)
                if r.status_code in (401, 403):
                    last = f"clave no válida o sin permiso (HTTP {r.status_code})"
                    continue
                if r.status_code == 404:
                    last = "HTTP 404"
                    break                      # esta base no tiene esa ruta: probar la otra
                if r.status_code == 429:
                    raise OddsError("límite de consultas alcanzado (HTTP 429)")
                if r.status_code != 200:
                    body = str(getattr(r, "text", "") or "")[:160].replace(self.key, "***") if self.key else ""
                    raise OddsError(f"HTTP {r.status_code} en {path} {params or ''} → {body}")
                self.bases, self._mode = [base], mode
                if self.cache is not None:
                    self.cache.set("oddsauth", [base, mode])
                return r.json()
        raise OddsError(last or "sin respuesta")


def _items(data) -> List[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "items", "results", "bookmakers", "markets", "fixtures"):
            if isinstance(data.get(k), list):
                return [x for x in data[k] if isinstance(x, dict)]
        if all(isinstance(v, dict) for v in data.values()) and data:
            return [{"_key": k, **v} for k, v in data.items()]
    return []


def _short(x, n: int = 140) -> str:
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _flatten_odds(odds) -> dict:
    """{(casa, id_mercado): [(id_resultado, precio), ...]} para la forma v5 ('odds') y la v4 ('bookmakerOdds')."""
    seen: dict = {}
    if not isinstance(odds, dict):
        return seen
    for slug, entries in (odds.get("odds") or {}).items():           # v5: plano
        if isinstance(entries, dict):
            for e in entries.values():
                if isinstance(e, dict) and "marketId" in e and e.get("active", True) and e.get("marketActive", True):
                    seen.setdefault((slug, e["marketId"]), []).append((e.get("outcomeId"), e.get("price")))
    for slug, book in (odds.get("bookmakerOdds") or {}).items():     # v4: casa -> markets -> id -> outcomes -> id -> players -> precio
        for mid, m in ((book or {}).get("markets") or {}).items():
            for oid, o in ((m or {}).get("outcomes") or {}).items():
                for pl in ((o or {}).get("players") or {}).values():
                    if isinstance(pl, dict) and pl.get("active", True):
                        seen.setdefault((slug, int(mid) if str(mid).isdigit() else mid), []).append((oid, pl.get("price")))
    return seen


def discover(api: OddsPapi, log: Callable[[str], None] = print) -> str:
    out: List[str] = ["🔬 Diagnóstico de OddsPapi"]
    # 1) casas de apuestas
    try:
        books = _items(api.get("/bookmakers", ttl=7 * 86400))
    except OddsError as e:
        books = []
        out.append(f"(casas de apuestas no disponibles: {e})")
    available = [str(b.get("slug") or b.get("bookmaker") or b.get("_key") or "") for b in books]
    wanted = ("pinnacle", "singbet", "betfair-ex", "betfair", "sbobet", "bet365", "1xbet", "betano")   # casas de referencia a mirar
    refs = []
    for name in wanted:
        for sl in available:
            low = sl.lower()
            if (low == name or low.startswith(name + ".")) and sl not in refs:
                refs.append(sl)
    refs = refs[:6]
    slugs = {"betano": [x for x in available if x.lower() == "betano" or x.lower().startswith("betano.")],
             "pinnacle": [x for x in available if x.lower() == "pinnacle"]}
    out.append(f"Casas: {len(books)} · de referencia disponibles: {', '.join(refs) or 'ninguna'}"
               f" · Betano: {', '.join(slugs['betano'][:8]) or 'no aparece'}")
    # 2) un partido cercano de fútbol (v4 pide from/to en ISO; v5 usa startTimeFrom/To: se mandan ambos)
    now = int(time.time())
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    fx = _items(api.get("/fixtures", {"sportId": 10, "from": iso(now), "to": iso(now + 36 * 3600),
                                      "startTimeFrom": now, "startTimeTo": now + 36 * 3600, "hasOdds": "true"}))
    fx = [f for f in fx if f.get("hasOdds", True)]
    out.append(f"Partidos de fútbol con cuotas en las próximas 36 h: {len(fx)}")
    if not fx:
        log("oddspapi: sin partidos para sondear")
        return "\n".join(out + ["No hay partidos para sondear ahora; prueba más tarde."])
    f = fx[0]
    fid = f.get("fixtureId")
    p = f.get("participants") or f
    out.append(f"Sondeo con: {p.get('participant1Name')} vs {p.get('participant2Name')}")
    # 3) cuotas de ese partido (solo Betano si la encontramos)
    params = {"fixtureId": fid}
    if refs:
        params["bookmakers"] = ",".join(refs)       # varias casas en UNA consulta: no gasta más cupo
    odds = api.get("/odds", params) if "v4" in api.bases[0] else api.get("/fixtures/odds", params)
    seen = _flatten_odds(odds)
    out.append(f"Mercados con cuota en ese partido: {len(seen)}")
    # 4) significado de los mercados de totales
    names = {}
    try:
        for m in _items(api.get("/markets", ttl=7 * 86400)):
            names[m.get("marketId")] = m
    except OddsError as e:
        out.append(f"(no pude leer /markets: {e})")
    shown_meta = 0
    for house in sorted({sl for sl, _ in seen}):
        ids = []
        for (sl, mid), vals in sorted(seen.items(), key=lambda kv: str(kv[0])):
            if sl != house:
                continue
            meta = names.get(mid) or {}
            nm = (str(meta.get("marketName", "")) + " " + str(meta.get("marketType", ""))).lower()
            if ("over" in nm or "total" in nm) and "half" not in nm and str(meta.get("period", "fulltime")).lower() == "fulltime":
                ids.append((mid, vals[:2]))
                if shown_meta < 2:
                    out.append(f"• ficha del mercado {mid}: {_short(meta, 420)}")
                    shown_meta += 1
        out.append(f"{house}: {len(ids)} mercados de totales (tiempo completo) → {ids[:8]}")
    out.append(f"Consultas gastadas ahora: {api.used} de 250 al mes.")
    log("oddspapi: " + _short({"base": api.bases[0], "betano": slugs.get("betano"), "n_mercados": len(seen)}, 300))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Cuotas del mercado como TERCERA lectura de la probabilidad (solo antes del partido)
# ---------------------------------------------------------------------------------------------
import re as _re
import unicodedata as _ud
from datetime import datetime as _dt, timezone as _tz
from difflib import SequenceMatcher as _SM

SHARP = ("pinnacle", "singbet", "betfair-ex")      # orden de preferencia, por línea
_STOP = {"fc", "cf", "sc", "ac", "afc", "club", "de", "the", "cd", "ud", "sd", "fk", "sk", "u21", "u23"}


def _norm(x: str) -> str:
    x = _ud.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in _re.sub(r"[^a-z0-9 ]", " ", x).split() if w not in _STOP)


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    return max(_SM(None, a, b).ratio(), jac)


def _ts(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return _dt.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(_tz.utc).timestamp()
    except ValueError:
        return None


def devig(p_over_price: float, p_under_price: float) -> Optional[float]:
    """Probabilidad de 'más de' quitando el margen de la casa (proporcional). None si los precios no son creíbles."""
    try:
        io, iu = 1.0 / float(p_over_price), 1.0 / float(p_under_price)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    tot = io + iu
    if not (1.0 <= tot <= 1.15):
        return None
    return io / tot


class OddsFeed:
    """Probabilidades 'más de / menos de' de casas de referencia para un partido de API-Football.

    - Fichas de mercado (líneas) y casas: caché 7 días.  Lista de partidos: caché 30 min.  Cuotas: 30 min.
    - Tope diario de consultas (daily_cap) para no agotar las 250 del mes.
    """

    def __init__(self, api: OddsPapi, cache, daily_cap: int = 8, log: Callable[[str], None] = print):
        self.api, self.cache, self.daily_cap, self.log = api, cache, daily_cap, log
        self.last_error = ""

    # -- cupo diario -------------------------------------------------------
    def _day_key(self) -> str:
        return "oddsused|" + time.strftime("%Y-%m-%d", time.gmtime())

    def _used_today(self) -> int:
        return int(self.cache.get(self._day_key(), 2 * 86400) or 0)

    def _call(self, path: str, params: dict, ttl: float):
        before = self.api.used
        try:
            return self.api.get(path, params, ttl=ttl)
        finally:
            spent = self.api.used - before
            if spent:
                self.cache.set(self._day_key(), self._used_today() + spent)

    # -- datos de referencia -----------------------------------------------
    def line_map(self) -> dict:
        """{línea: {'over': id_resultado, 'under': id_resultado, 'mid': id_mercado}} de totales a tiempo completo."""
        out = {}
        for m in _items(self._call("/markets", {}, 7 * 86400)):
            if str(m.get("marketType", "")).lower() != "totals" or str(m.get("period", "")).lower() != "fulltime" \
                    or m.get("playerProp"):
                continue
            try:
                line = float(m.get("handicap"))
            except (TypeError, ValueError):
                continue
            ids = {str(o.get("outcomeName", "")).lower(): o.get("outcomeId") for o in (m.get("outcomes") or [])}
            if "over" in ids and "under" in ids:
                out[line] = {"over": ids["over"], "under": ids["under"], "mid": m.get("marketId")}
        return out

    def houses(self) -> list:
        slugs = [str(b.get("slug") or b.get("bookmaker") or b.get("_key") or "")
                 for b in _items(self._call("/bookmakers", {}, 7 * 86400))]
        return [x for x in SHARP if x in slugs]

    def find_fixture(self, fx: dict) -> Optional[str]:
        """Empareja el partido de API-Football con el de OddsPapi por nombres y hora de inicio."""
        now = int(time.time())
        t0 = (fx.get("ts") or now) - 6 * 3600
        t1 = (fx.get("ts") or now) + 6 * 3600
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        # ventana fija por hora (para que varias consultas cercanas compartan caché)
        t0, t1 = t0 - t0 % 3600, t1 - t1 % 3600 + 3600
        rows = _items(self._call("/fixtures", {"sportId": 10, "from": iso(t0), "to": iso(t1),
                                               "startTimeFrom": int(t0), "startTimeTo": int(t1), "hasOdds": "true"}, 1800))
        best, best_s = None, 0.0
        for r in rows:
            p = r.get("participants") or r
            st = _ts(r.get("startTime"))
            if st is not None and fx.get("ts") and abs(st - fx["ts"]) > 3 * 3600:
                continue
            sc = min(_sim(fx["home"], p.get("participant1Name")), _sim(fx["away"], p.get("participant2Name")))
            if sc > best_s:
                best, best_s = r, sc
        if best is None or best_s < 0.6:
            return None
        return best.get("fixtureId")

    # -- API pública -------------------------------------------------------
    def probs(self, fx: dict) -> Optional[dict]:
        """{'over': {línea: p}, 'under': {línea: p}, 'books': [...]} o None (sin datos, sin cupo o error)."""
        self.last_error = ""
        try:
            if self._used_today() >= self.daily_cap:
                self.last_error = f"tope diario de {self.daily_cap} consultas"
                return None
            lines = self.line_map()
            houses = self.houses()
            if not lines or not houses:
                self.last_error = "sin líneas o casas de referencia"
                return None
            fid = self.find_fixture(fx)
            if not fid:
                self.last_error = "partido no encontrado en OddsPapi"
                return None
            odds = self._call("/odds", {"fixtureId": fid, "bookmakers": ",".join(houses)}, 1800)
        except (OddsError, requests.RequestException) as e:
            self.last_error = str(e)[:100]
            self.log(f"oddspapi: {self.last_error}")
            return None
        seen = _flatten_odds(odds)
        over, under, used = {}, {}, set()
        prices = {"over": {}, "under": {}}
        book_by_line = {"over": {}, "under": {}}
        for line, ids in sorted(lines.items()):
            for house in houses:                         # por línea: la primera casa de referencia que la tenga
                vals = {}
                for oid, price in seen.get((house, ids["mid"]), []):
                    try:
                        vals[int(oid)] = float(price)
                    except (TypeError, ValueError):
                        pass
                p = devig(vals.get(ids["over"]), vals.get(ids["under"])) if ids["over"] in vals and ids["under"] in vals else None
                if p is not None:
                    over[line], under[line] = p, 1.0 - p
                    prices["over"][line] = vals[ids["over"]]
                    prices["under"][line] = vals[ids["under"]]
                    book_by_line["over"][line] = house
                    book_by_line["under"][line] = house
                    used.add(house)
                    break
        if not over:
            self.last_error = "las casas de referencia no traen líneas de totales para este partido"
            return None
        return {"over": over, "under": under, "books": sorted(used),
                "prices": prices, "book_by_line": book_by_line}
