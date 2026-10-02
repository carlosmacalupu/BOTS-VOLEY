"""Cliente mínimo de API-Football (v3) con caché en disco.

La caché hace el bot determinista (misma consulta -> mismos datos) y cuida la
cuota diaria de la API.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Callable, List, Optional

import requests

from model import Game

BASE = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}
NOT_STARTED = {"NS", "TBD"}
LIVE_OK = {"1H", "HT", "2H", "LIVE"}                   # tiempo reglamentario: se puede analizar
LIVE_ALL = LIVE_OK | {"ET", "BT", "P", "INT", "SUSP"}  # cualquier partido en juego


class ApiError(Exception):
    pass


class Cache:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.folder, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get(self, key: str, ttl: float):
        if ttl <= 0:
            return None
        try:
            with open(self._path(key), "r", encoding="utf-8") as f:
                obj = json.load(f)
            if time.time() - obj["ts"] <= ttl:
                return obj["data"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def set(self, key: str, data) -> None:
        path = self._path(key)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f)
        os.replace(tmp, path)


def parse_fixture(x: dict) -> dict:
    """Normaliza un partido de API-Football. Goles = 90 minutos (sin prórroga)."""
    fx, lg, tm = x.get("fixture") or {}, x.get("league") or {}, x.get("teams") or {}
    status = (fx.get("status") or {}).get("short", "")
    ft = (x.get("score") or {}).get("fulltime") or {}
    gh, ga = ft.get("home"), ft.get("away")
    if (gh is None or ga is None) and status == "FT":
        g = x.get("goals") or {}
        gh, ga = g.get("home"), g.get("away")
    return {
        "id": fx.get("id"),
        "ts": fx.get("timestamp"),
        "status": status,
        "home_id": (tm.get("home") or {}).get("id"),
        "home": (tm.get("home") or {}).get("name", "?"),
        "away_id": (tm.get("away") or {}).get("id"),
        "away": (tm.get("away") or {}).get("name", "?"),
        "league": lg.get("name", "?"),
        "country": lg.get("country", ""),
        "season": lg.get("season"),
        "league_id": lg.get("id"),
        "gh": gh,
        "ga": ga,
        "elapsed": (fx.get("status") or {}).get("elapsed"),   # minuto (en vivo)
        "cur_h": (x.get("goals") or {}).get("home"),          # marcador actual
        "cur_a": (x.get("goals") or {}).get("away"),
    }


def to_games(rows: List[dict], team_id: int, kickoff_ts: float, n: int = 20) -> List[Game]:
    """Últimos n partidos terminados del equipo ANTES del kickoff."""
    seen, done = set(), []
    for r in rows:
        if (r["status"] in FINISHED and r["gh"] is not None and r["ga"] is not None
                and r["ts"] and r["ts"] < kickoff_ts and r["id"] not in seen
                and team_id in (r["home_id"], r["away_id"])):
            seen.add(r["id"])
            done.append(r)
    done.sort(key=lambda r: r["ts"], reverse=True)
    games = []
    for r in done[:n]:
        is_home = r["home_id"] == team_id
        gf, ga = (r["gh"], r["ga"]) if is_home else (r["ga"], r["gh"])
        games.append(Game(int(gf), int(ga), is_home, (kickoff_ts - r["ts"]) / 86400.0))
    return games


class ApiFootball:
    def __init__(self, key: str, cache: Cache, http_get: Optional[Callable] = None,
                 min_gap: float = 0.0, sleep: Callable[[float], None] = time.sleep):
        self.key = key
        self.cache = cache
        self._http = http_get or requests.get
        self.min_gap = min_gap          # pausa mínima (s) entre consultas reales, para no pasar el límite por minuto
        self._sleep = sleep
        self._last = 0.0
        self._pace_lock = threading.Lock()

    def get(self, path: str, params: dict, ttl: float) -> list:
        ckey = path + json.dumps(params, sort_keys=True)
        cached = self.cache.get(ckey, ttl)
        if cached is not None:
            return cached
        if not self.key:
            raise ApiError("Falta API_FOOTBALL_KEY")
        for wait in (12, 30, 60, None):         # si API-Football dice «demasiadas consultas por minuto»: esperar y reintentar
            with self._pace_lock:
                gap = self.min_gap - (time.time() - self._last)
                if gap > 0:
                    self._sleep(gap)
                self._last = time.time()
            r = self._http(BASE + path, headers={"x-apisports-key": self.key},
                           params=params, timeout=25)
            data = r.json() if r.status_code == 200 else {}
            limited = r.status_code == 429 or "ratelimit" in json.dumps(data.get("errors") or "").lower()
            if limited and wait is not None:
                print(f"API-Football: límite por minuto; espero {wait}s y reintento", flush=True)
                self._sleep(wait)
                continue
            break
        if r.status_code != 200:
            raise ApiError(f"API-Football respondió HTTP {r.status_code}")
        if data.get("errors"):
            raise ApiError(json.dumps(data["errors"], ensure_ascii=False))
        resp = data.get("response") or []
        self.cache.set(ckey, resp)
        return resp

    def fixtures_by_date(self, date: str, tz: str, ttl: float = 120) -> List[dict]:
        resp = self.get("/fixtures", {"date": date, "timezone": tz}, ttl=ttl)
        return [parse_fixture(x) for x in resp]

    def fixture_by_id(self, fid: int, ttl: float = 0) -> Optional[dict]:
        resp = self.get("/fixtures", {"id": fid}, ttl=ttl)
        return parse_fixture(resp[0]) if resp else None

    def red_cards(self, fid: int, ttl: float = 60) -> List[dict]:
        """Expulsiones del partido (roja directa o segunda amarilla): [{'minute', 'team'}]."""
        resp = self.get("/fixtures/events", {"fixture": fid}, ttl=ttl)
        out = []
        for e in resp:
            detail = str(e.get("detail", "")).lower()
            if e.get("type") == "Card" and ("red" in detail or "second yellow" in detail):
                out.append({"minute": (e.get("time") or {}).get("elapsed"),
                            "team": (e.get("team") or {}).get("name", "?")})
        return out

    def standings_rows(self, league_id: int, season: int, ttl: float = 6 * 3600) -> List[dict]:
        """Tabla de posiciones: [{'team_id','rank','points','played','desc'}]. [] si la API no la trae (copas, selecciones)."""
        try:
            resp = self.get("/standings", {"league": league_id, "season": season}, ttl=ttl)
        except Exception:
            return []
        out = []
        for r in resp or []:
            for group in (r.get("league") or {}).get("standings") or []:
                for x in group:
                    out.append({"team_id": (x.get("team") or {}).get("id"), "rank": x.get("rank"),
                                "points": x.get("points") or 0, "played": (x.get("all") or {}).get("played") or 0,
                                "desc": x.get("description") or ""})
        return out

    def live_stats(self, fid: int, ttl: float = 90) -> dict:
        """Remates, remates a puerta y córners de AMBOS equipos hasta ahora. {} si la API no los trae."""
        try:
            resp = self.get("/fixtures/statistics", {"fixture": fid}, ttl=ttl)
        except Exception:
            return {}
        tot = {"shots": 0, "sot": 0, "corners": 0}
        seen = 0
        for team in resp or []:
            seen += 1
            for s in team.get("statistics") or []:
                v = s.get("value")
                v = v if isinstance(v, (int, float)) else (int(v) if str(v).isdigit() else 0)
                t = s.get("type")
                if t == "Total Shots":
                    tot["shots"] += v
                elif t == "Shots on Goal":
                    tot["sot"] += v
                elif t == "Corner Kicks":
                    tot["corners"] += v
        return tot if seen else {}

    def squad_news(self, fid: int, ttl: float = 1800) -> dict:
        """Lesionados/suspendidos y alineaciones confirmadas (si ya salieron). Nunca lanza error."""
        out = {"injuries": {}, "lineups": {}}
        try:
            for e in self.get("/injuries", {"fixture": fid}, ttl=ttl):
                t = (e.get("team") or {}).get("name", "?")
                p = (e.get("player") or {}).get("name")
                if p:
                    out["injuries"].setdefault(t, []).append(p)
        except Exception:
            pass
        try:
            for e in self.get("/fixtures/lineups", {"fixture": fid}, ttl=ttl):
                t = (e.get("team") or {}).get("name", "?")
                xi = [(s.get("player") or {}).get("name") for s in (e.get("startXI") or [])]
                xi = [n for n in xi if n]
                if xi:
                    out["lineups"][t] = xi
        except Exception:
            pass
        return out

    def league_rows(self, league_id: int, season: int) -> List[dict]:
        """TODOS los partidos de una liga y temporada en UNA consulta (sirve para todos sus equipos)."""
        return [parse_fixture(x) for x in self.get("/fixtures", {"league": league_id, "season": season}, ttl=6 * 3600)]

    def team_games(self, team_id: int, season: int, kickoff_ts: float,
                   n: int = 20, enough: int = 12, minimum: int = 8) -> List[Game]:
        """Últimos partidos del equipo. Busca por temporada (hasta 3 años atrás: las selecciones juegan pocos
        partidos por año) y, si aún faltan, pide directamente los últimos 30 sin importar la temporada."""
        rows: List[dict] = []
        games: List[Game] = []
        for k, s in enumerate((season, season - 1, season - 2)):
            try:
                resp = self.get("/fixtures", {"team": team_id, "season": s}, ttl=6 * 3600)
            except ApiError:
                if k == 0:
                    raise
                break  # esa temporada puede no estar en tu plan
            rows += [parse_fixture(x) for x in resp]
            games = to_games(rows, team_id, kickoff_ts, n)
            if len(games) >= enough:
                return games
        if len(games) < minimum:
            try:
                rows += [parse_fixture(x) for x in self.get("/fixtures", {"team": team_id, "last": 30}, ttl=6 * 3600)]
                games = to_games(rows, team_id, kickoff_ts, n)
            except ApiError:
                pass
        return games
