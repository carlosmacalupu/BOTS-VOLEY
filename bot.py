"""Bot de fútbol por Telegram: PRE-partido, EN VIVO y resultado.

Escribes el nombre de un equipo (o /partido ...). El bot busca en los partidos de
ayer, hoy y mañana; si hay varios, te muestra botones para elegir.
  - Por jugar  -> pronóstico PRE (+0.5, +1.5, techo, escalera Under)
  - En juego   -> pronóstico EN VIVO con marcador y minuto
  - Terminado  -> resultado y qué habría pasado con tus apuestas

Despliegue: ver README.md. Variables de entorno: ver .env.example.
"""
from __future__ import annotations

import contextvars
import json
import math
import os
import re
import sys
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from apifootball import (to_games as _to_games, ApiError, ApiFootball, Cache, FINISHED, LIVE_ALL, LIVE_OK,
                         NOT_STARTED)
from ai import DISAGREE, ClaudeClient, OpenAIClient, cross_check, merge_opinions
from model import Forecast, range_prob, LINES, MIN_GAMES, evaluate_bet, forecast, live_forecast, market_prob
from profiles import PROFILES, classify, study_quality, quality_label
from sovereign import build as build_sovereign, color as sov_color, approval as sov_approval
from oddspapi import OddsError, OddsFeed, OddsPapi, discover
from store import Store, compute_stats
from edge_scanner import MarketSnapshot as EdgeMarketSnapshot, scan_totals as edge_scan_totals, format_telegram as edge_format_telegram
from conservative import format_conservative

# ---------- configuración ----------
DISPLAY_VERSION = "BOTS_BETANO_MASTER_UNICO_V11_MODO_CONSERVADOR"
ANCHOR_COMPAT = {DISPLAY_VERSION, "BOTS_BETANO_MASTER_UNICO_V10_EDGE_FINDER"}
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BETANO_TELEGRAM_BOT_TOKEN", "")
_CHAT_IDS = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("BETANO_TELEGRAM_CHAT_ID", "")
ALLOWED = {c.strip() for c in _CHAT_IDS.split(",") if c.strip()}
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
DATA_DIR = os.getenv("DATA_DIR") or os.getenv("BETANO_DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "./data"
TZ = os.getenv("TIMEZONE", "America/Lima")
STAKE = float(os.getenv("STAKE", "500"))
EDGE_MIN = float(os.getenv("EDGE_MIN", "0.02"))
UNCERTAINTY = float(os.getenv("UNCERTAINTY_PP", "2.0")) / 100.0
FLOOR_PROB = float(os.getenv("FLOOR_PROB", "0.93"))
FLOOR_RISK = float(os.getenv("FLOOR_RISK", "0.08"))
TAIL = float(os.getenv("TAIL", "0.03"))
HISTORY_GAMES = int(os.getenv("HISTORY_GAMES", "20"))
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
ANTHROPIC_WEB = os.getenv("ANTHROPIC_WEB", "0") != "0"
OPENAI_WEB = os.getenv("OPENAI_WEB", "1") != "0"
ODDS_KEY = os.getenv("ODDSPAPI_KEY", "")
ODDS_BASE = os.getenv("ODDSPAPI_BASE", "")
ODDS_CACHE = None
ODDS_FEED = None
ODDS_DAILY_CAP = int(os.getenv("ODDS_DAILY_CAP", "10"))   # consultas a OddsPapi por día (plan gratis: 250 al mes)
SCAN_LEAGUES = int(os.getenv("SCAN_LEAGUES", "40"))             # ligas que revisa un estudio (1-2 consultas por liga)
SCAN_EXCLUDE = [w.strip().lower() for w in os.getenv("SCAN_EXCLUDE", "").split(",") if w.strip()]
API_MIN_GAP = float(os.getenv("API_MIN_GAP", "0.4"))   # pausa (s) entre consultas a API-Football
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "-1"))  # hora de Lima del estudio diario AUTOMÁTICO; -1 = apagado (por defecto: el bot solo responde cuando le preguntas)
DAILY_SCAN = int(os.getenv("DAILY_SCAN", "20"))            # partidos que revisa el estudio diario
SCAN_ATTEMPTS = int(os.getenv("SCAN_ATTEMPTS", "30"))      # tope de partidos probados (protege tu cupo de API-Football)
MAX_ODDS_SCAN = int(os.getenv("MAX_ODDS_SCAN", "3"))     # en /mejores, a cuántos finalistas se les pide el mercado
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "75"))
PRE_ANCHOR_TTL = float(os.getenv("PRE_ANCHOR_TTL", str(6 * 3600)))
USE_ODDS_VALIDATION = os.getenv("USE_ODDS_VALIDATION", "0") == "1"
USE_SECOND_AI = os.getenv("USE_SECOND_AI", "0") == "1"
TELEGRAM_PROGRESS = os.getenv("TELEGRAM_PROGRESS", "0") == "1"   # tope duro en segundos para la IA
API: Optional[ApiFootball] = None          # se crea en main(); lo usa la IA para pedir lesionados/alineaciones
AI2 = None                                  # segunda IA (Claude), opcional
AI: Optional[OpenAIClient] = None          # se crea en main() si hay clave
SAFE_PROB = float(os.getenv("SAFE_PROB", "0.95"))    # desde aquí: ✅ APOSTAR
ENTRY_FROM = int(os.getenv("ENTRY_FROM", "12"))      # /entrada: desde qué minuto mira partidos 0-0
ENTRY_TO = int(os.getenv("ENTRY_TO", "40"))          # hasta qué minuto
ENTRY_PRE_MIN = float(os.getenv("ENTRY_PRE_MIN", "0.90"))   # solo partidos que antes del inicio tenían ≥ esto de +0.5
ENTRY_MAX = int(os.getenv("ENTRY_MAX", "8"))         # cuántos partidos 0-0 revisa a fondo
MAYBE_PROB = float(os.getenv("MAYBE_PROB", "0.90"))  # entre MAYBE y SAFE: 🟡 DUDOSO
SETTLE_EVERY_MIN = int(os.getenv("SETTLE_EVERY_MIN", "60"))   # 0 = sin liquidación automática
AUTO_NOTIFY = os.getenv("AUTO_NOTIFY", "0") == "1"   # por defecto NO te escribe cuando guarda resultados
VOID_STATUS = {"PST", "CANC", "ABD", "AWD", "WO"}
MAX_OPTIONS = 8
MAX_SCAN = int(os.getenv("MAX_SCAN", "12"))   # partidos que revisa /mejores con el modelo
MAX_AI = int(os.getenv("MAX_AI", "5"))        # de esos, a cuántos pide segunda opinión a la IA

HELP = (
    "⚽ Bot de fútbol\n\n"
    "Escribe el nombre de un equipo (ej. Nicaragua) y elige el partido con un botón. "
    "Te digo, para +0.5, +1.5 y el máximo de goles:\n"
    "✅ APOSTAR · 🟡 DUDOSO · ❌ NO APOSTAR\n"
    "Funciona antes, durante (en vivo) y después del partido. El botón «Ver detalle» muestra el análisis completo.\n\n"
    "Si quieres que valore una cuota: Nicaragua | +0.5 1.10 | -5.5 1.12\n"
    "(+ = más de, - = menos de). También: Local vs Visitante, o el id: /partido 1234567\n\n"
    "/vivo   partidos en juego ahora\n"
    "/combinada   mi combinada de hoy: las selecciones que fui añadiendo con los botones ➕\n"
    "/limpiar   vacía mi combinada\n"
    "/banca 500   guarda tu dinero total para mostrarte los montos en soles\n"
    "/meta 25 1.10   cuánto debes apostar para ganar S/25 a esa cuota, y el riesgo real\n"
    "No te escribo nunca por mi cuenta: solo respondo cuando preguntas.\n"
    "/entrada   busca partidos que van 0-0 (min 12–40) y que antes tenían alta probabilidad de gol: te dice si entrar a +0.5 en vivo y la cuota mínima\n"
    "/mejores   revisa los partidos que aún no empiezan y te dice cuáles tienen +0.5 y techo seguros (tarda ~1 min). «/mejores vivo» incluye los que están en juego\n"
    "/oddspapi   diagnóstico de tu cuenta de OddsPapi (gasta ~4 consultas de las 250 al mes)\n"
    "/valor   busca VACÍOS de mercado en el partido activo; usa OddsPapi solo al pedirlo y reutiliza caché\n"
    "/seguro   modo ALTA EXIGENCIA: solo cuotas bajas y líneas que superan consenso, calidad, edge y EV\n"
    "/hoy [filtro]   partidos de hoy (ej. /hoy peru)\n"
    "/probar   prueba el bot con partidos pasados (tarda ~1 minuto)\n"
    "/liquidar   guarda ya los resultados reales (si no, se guardan solos cada hora)\n"
    "/stats   acierto real acumulado del bot"
)

Buttons = Optional[List[Tuple[str, str]]]
Reply = Tuple[str, Buttons]

# apuestas escritas por el usuario mientras elige un partido (se pierden al reiniciar)
PENDING: Dict[int, list] = {}
_tok = [0]
LAST_OPTS: list = []   # (fixture_id, token) de la última lista de opciones, para elegir escribiendo el número
ACTIVE_FID = [None]      # último partido seleccionado; permite escribir AHORA sin repetir el nombre


def stash(bets: list) -> int:
    if not bets:
        return 0
    _tok[0] += 1
    PENDING[_tok[0]] = bets
    for k in sorted(PENDING)[:-200]:
        PENDING.pop(k, None)
    return _tok[0]


# ---------- utilidades de texto ----------
_STOP = {"fc", "cf", "sc", "ac", "afc", "club", "de", "the", "cd", "ud", "sd", "fk", "sk"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(t for t in s.split() if t not in _STOP)


def name_score(query: str, name: str) -> float:
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    r = SequenceMatcher(None, q, n).ratio()
    if q in n or n in q:
        r = max(r, 0.9)
    return r


def _prio(f: dict) -> int:
    s = f["status"]
    if s in LIVE_ALL:
        return 0
    if s in NOT_STARTED:
        return 1
    if s in FINISHED:
        return 2
    return 3


def search_fixtures(fixtures: List[dict], q_home: str, q_away: str = "") -> List[dict]:
    """Partidos cuyo nombre se parece a la consulta (1 o 2 equipos, en cualquier orden)."""
    res = []
    for f in fixtures:
        a, b = name_score(q_home, f["home"]), name_score(q_home, f["away"])
        if q_away:
            s = max(min(a, name_score(q_away, f["away"])), min(b, name_score(q_away, f["home"])))
        else:
            s = max(a, b)
        if s >= 0.6:
            res.append((s, f))
    res.sort(key=lambda t: (_prio(t[1]), (t[1]["ts"] or 0) * (-1 if _prio(t[1]) == 2 else 1), -t[0]))
    return [f for _, f in res]


BET_RE = re.compile(
    r"(?P<side>[+\-]|over|under|mas|más|menos|o|u)\s*(?P<line>\d+(?:[.,]\d+)?)\s*(?:@|=|:|\s)\s*(?P<odds>\d+(?:[.,]\d+)?)",
    re.I)


def parse_bets(text: str) -> List[Tuple[str, float, float]]:
    """'+0.5 1.10 | -5.5@1.12' -> [('over',0.5,1.10), ('under',5.5,1.12)]"""
    out = []
    for m in BET_RE.finditer(text):
        side = "over" if m.group("side").lower() in ("+", "over", "mas", "más", "o") else "under"
        line = float(m.group("line").replace(",", "."))
        odds = float(m.group("odds").replace(",", "."))
        if line not in LINES:
            raise ValueError(f"Línea {line} no soportada (usa 0.5, 1.5 ... 9.5)")
        if odds <= 1.0:
            raise ValueError(f"Cuota {odds} no válida")
        out.append((side, line, odds))
    return out


def parse_query(text: str):
    """-> (id_o_None, equipo1, equipo2_o_vacío, apuestas)"""
    head, _, rest = text.partition("|")
    head = head.strip()
    bets = parse_bets(rest)
    if head.isdigit():
        return int(head), "", "", bets
    parts = re.split(r"\s+(?:vs\.?|v|-|–|contra)\s+", head, maxsplit=1, flags=re.I)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return None, parts[0].strip(), parts[1].strip(), bets
    return None, head, "", bets


def pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def fair(p: float) -> str:
    return f"{1 / p:.2f}" if p > 0.0005 else "—"


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(TZ)).strftime("%d/%m %H:%M")


def status_label(f: dict) -> str:
    s = f["status"]
    if s in NOT_STARTED:
        return f"⏳ {hhmm(f['ts'])}" if f["ts"] else "⏳"
    if s in LIVE_ALL:
        mn = "Descanso" if s == "HT" else f"{f.get('elapsed') or '?'}'"
        return f"🔴 {mn} {f.get('cur_h') or 0}-{f.get('cur_a') or 0}"
    if s in FINISHED:
        return f"✅ {f['gh']}-{f['ga']}"
    return f"⚪ {s}"


# ---------- render ----------
CONTEXT_CACHE: Dict[int, List[str]] = {}      # contexto por partido (se llena al consultarlo)
TOP_ZONE = ("champions", "promotion", "title", "europa", "conference", "championship", "libertadores", "sudamericana")
BOTTOM_ZONE = ("relegation", "descenso")


def ctx_short(fx: dict) -> List[str]:
    t = CONTEXT_CACHE.get(fx.get("id")) or []
    out = ["🧭 " + x for x in t[:4]]
    pen = CTX_PEN.get()
    if pen > 0:
        out.append(f"🧭 Ajuste de prudencia: −{pen * 100:.1f} pts al bot ({', '.join(CONTEXT_REASONS.get(fx.get('id'), []))})")
    return out


def team_context(name: str, tid, rows: List[dict]) -> str:
    """¿Qué se juega este equipo? Heurística con la tabla: zona de título/copas, zona de descenso, o mitad de tabla sin nada cerca."""
    me = next((r for r in rows if r["team_id"] == tid), None)
    if not me:
        return ""
    low = lambda r: (r["desc"] or "").lower()
    top = [r for r in rows if any(k in low(r) for k in TOP_ZONE)]
    bot_ = [r for r in rows if any(k in low(r) for k in BOTTOM_ZONE)]
    base = f"{name}: puesto {me['rank']} con {me['points']} pts en {me['played']} partidos"
    if any(k in low(me) for k in BOTTOM_ZONE):
        return base + " → está en zona de descenso: se juega la permanencia (motivación alta)"
    if any(k in low(me) for k in TOP_ZONE):
        return base + f" → zona alta ({me['desc']}): se juega algo importante (motivación alta)"
    g_top = (min(r["points"] for r in top) - me["points"]) if top else None
    g_bot = (me["points"] - max(r["points"] for r in bot_)) if bot_ else None
    near = []
    if g_top is not None and g_top <= 6:
        near.append(f"a {g_top} pts de la zona alta")
    if g_bot is not None and g_bot <= 6:
        near.append(f"a solo {g_bot} pts del descenso")
    if near:
        return base + " → " + " y ".join(near) + ": todavía se juega algo"
    return base + " → mitad de tabla, sin zona alta ni descenso cerca: puede jugar con menos urgencia"


def context_lines(api, fx: dict, hg, ag) -> List[str]:
    """Contexto de cada equipo: tabla y descanso. Vacío si no hay datos."""
    out = []
    try:
        rows = api.standings_rows(fx["league_id"], fx["season"]) if fx.get("league_id") and fx.get("season") else []
    except Exception:
        rows = []
    for name, tid, games in ((fx["home"], fx.get("home_id"), hg), (fx["away"], fx.get("away_id"), ag)):
        t = team_context(name, tid, rows) if rows else ""
        if t:
            out.append(t)
        if games:
            rest = min(g.age_days for g in games)
            if rest < 20:
                out.append(f"{name}: último partido hace {rest:.0f} días" + (" (descanso corto: puede rotar o llegar cansado)" if rest < 4 else ""))
    return out


def study_lines(fx: dict, fc, op, mk, hg, ag) -> List[str]:
    """El trabajo del bot, a la vista: datos de cada equipo, bajas/alineaciones, escalera de +líneas con cada lectura."""
    L = ["", "🔬 Lo que revisé:"]
    for name, games in ((fx["home"], hg), (fx["away"], ag)):
        if not games:
            continue
        d = team_digest(name, games)
        L.append(f"• {name}: últimos {d['n']} partidos → {d['gf']:.1f} goles a favor y {d['ga']:.1f} en contra por partido; "
                 f"con gol en {d['with_goal']} de {d['n']}, 0-0 en {d['zero']}. Últimos: {d['results']}")
    news = {"injuries": {}, "lineups": {}}
    if API is not None and fx.get("id"):
        try:
            news = API.squad_news(fx["id"])
        except Exception:
            pass
    for t, ps in news["injuries"].items():
        L.append(f"• Bajas de {t}: {', '.join(ps[:8])}")
    for t, xi in news["lineups"].items():
        L.append(f"• Alineación de {t}: {', '.join(xi)}")
    if not news["injuries"] and not news["lineups"]:
        L.append("• Bajas y alineaciones: la API aún no las trae para este partido")
    for t in (CONTEXT_CACHE.get(fx.get("id")) or []):
        L.append(f"• Contexto: {t}")
    L.append("• Escalera de «más de» (bot · IA · mercado):")
    for l in [x for x in LINES if x > fc.cur_goals][:5]:
        p = fc.p_over[l]
        p_ai = None if not op else op["over"].get(l)
        p_mk = None if not mk else mk["over"].get(l)
        parts = [f"bot {p_txt(p)}", f"IA {p_txt(p_ai)}" if p_ai is not None else "IA —",
                 f"mercado {p_txt(p_mk)}" if p_mk is not None else "mercado —"]
        L.append(f"   +{l:.1f}: " + " · ".join(parts) + f" · cuota justa {fair(p)}")
    L.append(f"• Goles esperados: {fx['home']} {fc.lam_home:.2f} · {fx['away']} {fc.lam_away:.2f}")
    return L


def render_evals(evals) -> List[str]:
    L = ["", f"💰 Valoración (apuesta de S/{STAKE:.0f}, margen mínimo {EDGE_MIN * 100:.0f}%, "
              f"resto {UNCERTAINTY * 100:.0f} pts por error del modelo):"]
    for side, line, e in evals:
        name = f"{'+' if side == 'over' else '-'}{line:.1f}"
        tag = "✅ CUMPLE LA REGLA" if e["value"] else "❌ NO CUMPLE LA REGLA"
        L.append(f"{name} @ {e['odds']:.2f}: {tag}")
        if e.get("note"):
            L.append(f"  {e['note']}")
            continue
        L.append(f"  modelo {pct(e['p'])} (conservador {pct(e['p_cons'])}) · la cuota exige acertar >{pct(e['implied'])}")
        L.append(f"  EV conservador {e['ev_cons'] * 100:+.1f}% · cuota mínima con valor: {e['min_odds']:.2f}")
        L.append(f"  Si acierta +S/{e['win']:.0f}; si falla -S/{e['loss']:.0f}")
    return L


def render_pre(fx: dict, fc, nh: int, na: int, evals, op=None, mk=None) -> str:
    L = [f"⚽ {fx['home']} vs {fx['away']}",
         f"{fx['league']} · {fx['country']} · {hhmm(fx['ts'])} (Lima)",
         f"Goles esperados: {fx['home']} {fc.lam_home:.2f} · {fx['away']} {fc.lam_away:.2f} · total {fc.exp_total:.2f}",
         f"Muestra: {nh} y {na} partidos previos"]
    if min(nh, na) < 12:
        L.append("⚠️ Muestra corta: tómalo con más cautela.")
    L += ["",
          f"+0.5 goles: {pct(fc.p_over[0.5])}  (cuota justa {fair(fc.p_over[0.5])})",
          f"+1.5 goles: {pct(fc.p_over[1.5])}  (cuota justa {fair(fc.p_over[1.5])})",
          f"Riesgo 0-0: {pct(fc.p_zero)}"]
    if fc.floor >= 1:
        L.append(f"Piso: {fc.floor} gol(es) con ≥{FLOOR_PROB * 100:.0f}% (equivale a +{fc.floor - 0.5:.1f})")
    else:
        L.append(f"Piso: no hay piso protegido (+0.5 está bajo {FLOOR_PROB * 100:.0f}%)")
    L.append(f"Techo: hasta {fc.ceiling} goles → Under {fc.ceiling_line:.1f} "
             f"({pct(fc.p_ceiling)}, cuota justa {fair(fc.p_ceiling)})")
    L.append("Escalera Under: " + " | ".join(f"{l:.1f}: {fc.p_under[l] * 100:.0f}%" for l in LINES[2:9]))
    if evals:
        L += render_evals(evals)
    if mk:
        L += ["", f"📈 Mercado ({', '.join(mk['books'])}, sin margen): " +
              " | ".join(f"+{l:.1f}: {mk['over'][l] * 100:.0f}%" for l in sorted(mk["over"]) if l <= 2.5) +
              " || " + " | ".join(f"Under {l:.1f}: {mk['under'][l] * 100:.0f}%" for l in sorted(mk["under"]) if l >= 3.5)]
    L += ai_detail(op)
    L += ["", "Esto es una estimación estadística, no una garantía."]
    return "\n".join(L)


def render_live(fx: dict, fc, nh: int, na: int, evals, op=None, extra=()) -> str:
    ch, ca = fx.get("cur_h") or 0, fx.get("cur_a") or 0
    cur = fc.cur_goals
    when = "Descanso" if fx["status"] == "HT" else f"{fc.elapsed}'"
    L = [f"🔴 EN VIVO {when} · {fx['home']} {ch}-{ca} {fx['away']}",
         f"{fx['league']} · {fx['country']}"] + list(extra) + [
         f"Goles ya marcados: {cur} · esperados en lo que falta: {fc.exp_total:.2f}",
         f"Muestra previa: {nh} y {na} partidos"]
    if min(nh, na) < 12:
        L.append("⚠️ Muestra corta: tómalo con más cautela.")
    L.append("")
    if cur >= 1:
        L.append("+0.5: ya cumplido ✅")
    for l in [x for x in LINES if x > cur][:3]:
        L.append(f"+{l:.1f} goles: {pct(fc.p_over[l])}  (cuota justa {fair(fc.p_over[l])})")
    L.append(f"Techo: hasta {fc.ceiling} goles en total → Under {fc.ceiling_line:.1f} "
             f"({pct(fc.p_ceiling)}, cuota justa {fair(fc.p_ceiling)})")
    esc = [l for l in LINES if l > cur][:6]
    if esc:
        L.append("Escalera Under: " + " | ".join(f"{l:.1f}: {fc.p_under[l] * 100:.0f}%" for l in esc))
    if evals:
        L += render_evals(evals)
    L += ai_detail(op)
    L += ["", "⚠️ En vivo el modelo calcula con marcador y minuto (el ritmo y la IA se muestran aparte); no ve lesiones ni quién domina. "
              "Compara siempre con la cuota actual de tu casa. Estimación, no garantía."]
    return "\n".join(L)


def render_result(fx: dict, bets, rec: Optional[dict]) -> str:
    gh, ga = fx["gh"], fx["ga"]
    total = gh + ga
    pen = " (a penales)" if fx["status"] == "PEN" else " (con prórroga)" if fx["status"] == "AET" else ""
    L = [f"✅ TERMINÓ{pen} · {fx['home']} {gh}-{ga} {fx['away']}",
         f"{fx['league']} · {fx['country']}",
         f"Total a los 90 minutos: {total} gol(es)", "",
         f"+0.5: {'✅ se dio' if total >= 1 else '❌ no se dio'}",
         f"+1.5: {'✅ se dio' if total >= 2 else '❌ no se dio'}"]
    if rec:
        ceil = int(rec["ceiling_line"] - 0.5)
        L += ["", f"Lo que había dicho el bot antes del partido: +0.5 {pct(rec['p_over05'])} · "
                  f"techo {ceil} goles ({pct(rec['p_ceiling'])}) → "
                  f"{'se respetó ✅' if total <= ceil else 'se superó ❌'}"]
    if bets:
        L += ["", f"💰 Tus apuestas (S/{STAKE:.0f} cada una):"]
        for side, line, odds in bets:
            hit = total > line if side == "over" else total < line
            name = f"{'+' if side == 'over' else '-'}{line:.1f}"
            L.append(f"{name} @ {odds:.2f}: " + (f"✅ GANADA +S/{STAKE * (odds - 1):.0f}" if hit
                                                  else f"❌ PERDIDA -S/{STAKE:.0f}"))
    return "\n".join(L)


# ---------- mensaje simple (lo que ves primero) ----------
def p_txt(p: float) -> str:
    return f"{int(p * 100)}%"            # se trunca: nunca promete más de lo que hay


def _fc_from_sov(base, sov):
    return Forecast(base.lam_home, base.lam_away, base.mu, list(sov.dist), dict(sov.p_under), dict(sov.p_over),
                    int(sov.floor), float(sov.ceiling_line), float(sov.p_ceiling), base.cur_goals, base.elapsed)


def _op_json(op):
    if not op:
        return None
    return {**op,
            "over": {str(k): float(v) for k, v in (op.get("over") or {}).items()},
            "under": {str(k): float(v) for k, v in (op.get("under") or {}).items()}}


def _op_restore(op):
    if not op:
        return None
    return {**op,
            "over": {float(k): float(v) for k, v in (op.get("over") or {}).items()},
            "under": {float(k): float(v) for k, v in (op.get("under") or {}).items()}}


def _snapshot_pack(fx, fc, op, profile, quality, ctx, hist, disagreement=None, ai_weight=0.0):
    return {
        "version": DISPLAY_VERSION, "fx": dict(fx), "profile": profile.code, "quality": int(quality),
        "context": list(ctx or []), "hist": hist or "", "op": _op_json(op),
        "fc": {"lam_home": fc.lam_home, "lam_away": fc.lam_away, "mu": fc.mu,
               "p_total": list(fc.p_total), "p_over": {str(k): v for k, v in fc.p_over.items()},
               "p_under": {str(k): v for k, v in fc.p_under.items()}, "floor": fc.floor,
               "ceiling_line": fc.ceiling_line, "p_ceiling": fc.p_ceiling,
               "cur_goals": fc.cur_goals, "elapsed": fc.elapsed},
        "disagreement": disagreement, "ai_weight": ai_weight,
    }


def _snapshot_unpack(snap):
    d = snap["fc"]
    fc = Forecast(float(d["lam_home"]), float(d["lam_away"]), float(d.get("mu", 1.3)),
                  [float(x) for x in d["p_total"]],
                  {float(k): float(v) for k, v in d["p_under"].items()},
                  {float(k): float(v) for k, v in d["p_over"].items()},
                  int(d["floor"]), float(d["ceiling_line"]), float(d["p_ceiling"]),
                  int(d.get("cur_goals") or 0), d.get("elapsed"))
    prof = PROFILES.get(snap.get("profile"), PROFILES["senior"] )
    return fc, _op_restore(snap.get("op")), prof, int(snap.get("quality", 0))


def _line_status(prob, quality, profile, kind, disagreement=None):
    c = sov_color(prob, quality, profile, kind, disagreement)
    return c, sov_approval(prob, quality, profile, kind, disagreement)


def _fmt_prob(p):
    return f"{p * 100:.1f}%"


def unified_pre(fx, fc, op, profile, quality, disagreement=None, anchored=False):
    L = [
        "🛡️ DECISIÓN BOTS BETANO",
        f"⚙️ {DISPLAY_VERSION}",
        f"⚽ {fx['home']} vs {fx['away']}",
        "🕒 PRE" + (" · ⚓ ANCLA" if anchored else ""),
        "",
        f"🧠 CATEGORÍA: {profile.label}",
        f"📚 CALIDAD DEL ESTUDIO: {quality}/100 · {quality_label(quality)}",
        f"🧭 CORREDOR PROTEGIDO: {fc.floor}–{fc.ceiling} GOLES",
        "",
    ]
    p05 = fc.p_over.get(0.5, 1.0 - (fc.p_total[0] if fc.p_total else 1.0))
    c, a = _line_status(p05, quality, profile, "over", disagreement)
    L += ["📉 PISO · ¿HABRÁ AL MENOS 1 GOL?",
          f"{c} +0.5 GOLES FT · {_fmt_prob(p05)} · {a}",
          f"⚠️ Riesgo 0-0: {_fmt_prob(1.0-p05)}", ""]
    c, a = _line_status(fc.p_ceiling, quality, profile, "under", disagreement)
    L += ["📈 TECHO · ¿HASTA CUÁNTOS GOLES?",
          f"🎯 MÁXIMO PROTEGIDO: {fc.ceiling} GOLES",
          f"{c} MENOS DE {fc.ceiling_line:.1f} GOLES FT · {_fmt_prob(fc.p_ceiling)} · {a} · TECHO NATURAL",
          f"⚠️ Riesgo {fc.ceiling + 1}+: {_fmt_prob(1.0-fc.p_ceiling)}", "",
          "📈 ESCALERA OVER FT"]
    green_over = None
    for line in [0.5,1.5,2.5,3.5,4.5,5.5]:
        p = fc.p_over.get(line)
        if p is None: continue
        c, a = _line_status(p, quality, profile, "over", disagreement)
        if c == "🟢": green_over = line
        L.append(f"{c} +{line:.1f} · {_fmt_prob(p)} · {a}")
    L.append(f"🎯 LÍMITE VERDE OVER: {'+' + format(green_over,'.1f') if green_over is not None else 'NINGUNO'}")
    L += ["", "📉 ESCALERA UNDER FT"]
    natural = fc.ceiling_line
    for line in [4.5,5.5,6.5,7.5,8.5,9.5]:
        p = fc.p_under.get(line)
        if p is None: continue
        c, a = _line_status(p, quality, profile, "under", disagreement)
        role = " · 🎯 NATURAL" if abs(line-natural) < 1e-9 else ""
        L.append(f"{c} U{line:.1f} · {_fmt_prob(p)} · {a}{role}")
    L += [f"🎯 UNDER NATURAL: U{natural:.1f}", ""]
    if op and op.get("profile"):
        L.append(f"🧠 PERFIL A×B: {op.get('profile')}")
    if disagreement is not None:
        L.append(f"🔬 Brecha modelo/IA: {disagreement*100:.1f} pp")
    L.append("🔄 AHORA · 🔎 DETALLE")
    return "\n".join(L)


def unified_detail(snap):
    fc, op, profile, quality = _snapshot_unpack(snap)
    L = ["", "🔎 AUDITORÍA · MISMO PRE ANCLA", f"Tipo: {profile.label} · calidad {quality}/100",
         f"Goles esperados locales: {fc.lam_home:.2f} + {fc.lam_away:.2f} = {fc.lam_home+fc.lam_away:.2f}",
         f"P(0-0): {_fmt_prob(fc.p_total[0] if fc.p_total else 1.0)}",
         f"Piso/techo soberanos: {fc.floor}–{fc.ceiling}",
         f"Peso IA en distribución final: {float(snap.get('ai_weight') or 0)*100:.0f}%"]
    if snap.get("hist"):
        L.append(snap["hist"] )
    for x in snap.get("context") or []:
        L.append("• " + x)
    if op:
        if op.get("resumen"): L.append("🤖 " + op["resumen"] )
        if op.get("quality") is not None: L.append(f"🤖 Calidad reportada por IA: {op['quality']}/100")
        for x in op.get("riesgos") or []: L.append("• Riesgo IA: " + x)
        for x in op.get("faltan") or []: L.append("• Falta: " + x)
    else:
        L.append("🤖 IA no disponible: la salida quedó en modo soberano local; no se inventa brecha.")
    return "\n".join(L)


def unified_live(fx, fc, op, profile, quality, disagreement=None, extra=None):
    score = f"{fx.get('cur_h') or 0}-{fx.get('cur_a') or 0}"
    when = "DESCANSO" if fx.get("status") == "HT" else f"{fc.elapsed or fx.get('elapsed') or '?'}'"
    L = ["🛡️ DECISIÓN BOTS BETANO", f"⚙️ {DISPLAY_VERSION}",
         f"⚽ {fx['home']} {score} {fx['away']}", f"🔴 LIVE · {when}", "",
         f"🧠 CATEGORÍA: {profile.label}", f"📚 CALIDAD DEL ESTUDIO: {quality}/100 · {quality_label(quality)}",
         f"🧭 CORREDOR FINAL PROTEGIDO: {fc.floor}–{fc.ceiling} GOLES", "",
         "📈 ESCALERA OVER FT"]
    start = max(0.5, float(fc.cur_goals) + 0.5)
    green = None
    for line in [0.5,1.5,2.5,3.5,4.5,5.5,6.5,7.5]:
        p = fc.p_over.get(line)
        if p is None: continue
        c,a = _line_status(p, quality, profile, "over", disagreement)
        if c == "🟢" and line >= start: green=line
        done = " · YA CUMPLIDO" if line < start else ""
        L.append(f"{c} +{line:.1f} · {_fmt_prob(p)} · {a}{done}")
    L.append(f"🎯 LÍMITE VERDE PENDIENTE: {'+'+format(green,'.1f') if green is not None else 'NINGUNO'}")
    L += ["", "📉 TECHO / UNDER"]
    c,a = _line_status(fc.p_ceiling, quality, profile, "under", disagreement)
    L.append(f"{c} U{fc.ceiling_line:.1f} · {_fmt_prob(fc.p_ceiling)} · {a} · 🎯 TECHO NATURAL")
    for line in [5.5,6.5,7.5,8.5,9.5]:
        if abs(line-fc.ceiling_line)<1e-9: continue
        p=fc.p_under.get(line)
        if p is None: continue
        c,a=_line_status(p,quality,profile,"under",disagreement)
        L.append(f"{c} U{line:.1f} · {_fmt_prob(p)} · {a}")
    if extra:
        L += ["", *list(extra)]
    if op and op.get("profile"):
        L.append(f"🧠 PERFIL A×B LIVE: {op.get('profile')}")
    L.append("🔄 AHORA · 🔎 DETALLE")
    return "\n".join(L)


def verdict(p: float, small: bool = False) -> str:
    if p >= SAFE_PROB and not small:
        return "✅ APOSTAR"
    if p >= MAYBE_PROB or p >= SAFE_PROB:   # muestra corta: como mucho DUDOSO
        return "🟡 DUDOSO"
    return "❌ NO APOSTAR"


def _ai_on() -> bool:
    return AI is not None or AI2 is not None


CTX_PEN = contextvars.ContextVar("ctx_pen", default=0.0)   # ajuste de prudencia por contexto (solo dentro de una consulta)
CTX_PEN_STEP = float(os.getenv("CTX_PEN_STEP", "0.015"))     # puntos que se restan por cada señal de contexto dudosa
CONTEXT_REASONS: Dict[int, List[str]] = {}


def ctx_penalty(lines: List[str]):
    """-> (penalización, motivos). No sabemos si el contexto sube o baja los goles; por eso solo resta confianza."""
    why = []
    if any("descanso corto" in x for x in lines):
        why.append("descanso corto")
    if any("menos urgencia" in x for x in lines):
        why.append("equipo sin urgencia")
    return min(2, len(why)) * CTX_PEN_STEP, why


def blend(p_model: float, p_ai: Optional[float], p_mkt: Optional[float] = None):
    """Tres lecturas (bot, IA, mercado): manda la más prudente; discrepancia si se separan más de DISAGREE."""
    p_model = max(0.0, p_model - CTX_PEN.get())
    vals = [v for v in (p_model, p_ai, p_mkt) if v is not None]
    return min(vals), (max(vals) - min(vals)) > DISAGREE


def verdict_x(p_model: float, p_ai: Optional[float], small: bool, p_mkt: Optional[float] = None) -> str:
    """Veredicto cotejando bot, IA y mercado: manda la lectura más prudente."""
    p_use, disagree = blend(p_model, p_ai, p_mkt)
    v = verdict(p_use, small)
    if v == "✅ APOSTAR" and (disagree or (_ai_on() and p_ai is None)):
        v = "🟡 DUDOSO"        # discrepan, o la IA está activa pero no respondió
    return v


def shown(p_model: float, p_ai: Optional[float], p_mkt: Optional[float] = None) -> str:
    parts = [f"bot {p_txt(max(0.0, p_model - CTX_PEN.get()))}"]
    if _ai_on():
        parts.append(f"IA {p_txt(p_ai)}" if p_ai is not None else "IA sin respuesta")
    if p_mkt is not None:
        parts.append(f"mercado {p_txt(p_mkt)}")
    if len(parts) == 1:
        return f"({p_txt(max(0.0, p_model - CTX_PEN.get()))})"
    flag = " ⚠️ difieren" if blend(p_model, p_ai, p_mkt)[1] else ""
    return "(" + " · ".join(parts) + ")" + flag


_progress_chat = [None]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def say(text: str) -> None:
    """Aviso de progreso al chat que se está atendiendo (si lo hay)."""
    if _progress_chat[0] is not None:
        try:
            tg_send(_progress_chat[0], text)
        except Exception:
            pass


def team_digest(name: str, games, k: int = 10) -> dict:
    """Resumen de los últimos partidos de un equipo para la IA (y para mostrártelo)."""
    gs = sorted(games, key=lambda g: g.age_days)
    last = gs[:k]
    n = len(gs)
    return {"name": name, "n": n,
            "results": ", ".join(f"{g.gf}-{g.ga} ({'L' if g.home else 'V'})" for g in last),
            "with_goal": sum(1 for g in gs if g.gf + g.ga >= 1), "zero": sum(1 for g in gs if g.gf + g.ga == 0),
            "gf": sum(g.gf for g in gs) / n if n else 0.0, "ga": sum(g.ga for g in gs) / n if n else 0.0}


def hist_line(hg, ag) -> str:
    """Una línea para ti: cuántos partidos recientes de ambos terminaron con gol."""
    n = len(hg) + len(ag)
    if not n:
        return ""
    w = sum(1 for g in list(hg) + list(ag) if g.gf + g.ga >= 1)
    z = n - w
    return f"📚 Historial reciente de ambos: con gol en {w} de {n} partidos ({int(w * 100 / n)}%) · 0-0: {z}"


def get_opinion(fx: dict, fc, live: Optional[dict], hg=None, ag=None):
    """UNA investigación IA por estado del partido. PRE pide toda la escalera 0.5..9.5 de una vez.
    La caché de ai.py + PRE ANCLA evita volver a pagar por AHORA/DETALLE/repeticiones.
    """
    if AI is None and (AI2 is None or not USE_SECOND_AI):
        return None
    cur = fc.cur_goals
    # Siempre pide la escalera completa; en LIVE las líneas ya cumplidas deben volver ~100%.
    # Esto permite reconstruir UNA distribución IA coherente también tras un gol.
    over = list(LINES[:10])
    unders = list(LINES[:10])
    t0 = time.time()
    log(f"IA: una lectura soberana {fx['home']} vs {fx['away']}")
    prof = classify(fx.get("home", ""), fx.get("away", ""), fx.get("league", ""), fx.get("country", ""))
    data = {"home": team_digest(fx["home"], hg), "away": team_digest(fx["away"], ag)} if hg and ag else {}
    data["category"] = {"code": prof.code, "label": prof.label, "note": prof.note}
    if API is not None and fx.get("id"):
        news = API.squad_news(fx["id"])
        if news["injuries"] or news["lineups"]:
            data["news"] = news
        ctx = CONTEXT_CACHE.get(fx["id"])
        if ctx is None:
            ctx = CONTEXT_CACHE[fx["id"]] = context_lines(API, fx, hg or [], ag or [])
        if ctx:
            data["context"] = ctx
    clients = [c for c in (AI, AI2 if USE_SECOND_AI else None) if c is not None]
    if len(clients) == 2:
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(c.opinion, fx, over, unders, live, data) for c in clients]
            op = merge_opinions(futs[0].result(), futs[1].result())
    else:
        op = clients[0].opinion(fx, over, unders, live, data)
    log(f"IA: {'ok' if op else 'sin respuesta (' + '/'.join(c.last_error for c in clients) + ')'} en {time.time() - t0:.0f}s")
    return op


PICKS: Dict[Tuple[int, str], dict] = {}     # selecciones ✅ que se pueden añadir con un botón (se pierden al reiniciar)


def today_str() -> str:
    return datetime.now(ZoneInfo(TZ)).date().isoformat()


def pick_entries(fx: dict, fc, op, mk, small: bool) -> List[dict]:
    """Selecciones con ✅ de un partido por empezar: +0.5 y techo. Se registran para el botón ➕."""
    out = []
    if fx["status"] not in NOT_STARTED:
        return out
    opts = []
    if fc.cur_goals == 0:
        opts.append(("o", "+0.5", "over", 0.5, fc.p_over[0.5]))
    if fc.ceiling_line <= 6.5:          # líneas más altas no las ofrecen las casas: no tiene sentido añadirlas
        opts.append(("u", f"Under {fc.ceiling_line:.1f}", "under", fc.ceiling_line, fc.p_ceiling))
    for kind, label, side, line, p in opts:
        p_ai = None if not op else op[side].get(line)
        p_mkt = None if not mk else mk[side].get(line)
        if verdict_x(p, p_ai, small, p_mkt).startswith("✅"):
            e = {"fid": fx["id"], "kind": kind, "home": fx["home"], "away": fx["away"], "league": fx["league"],
                 "label": label, "p": blend(p, p_ai, p_mkt)[0], "ts": fx["ts"]}
            PICKS[(fx["id"], kind)] = e
            out.append(e)
    for k in sorted(PICKS, key=lambda k: PICKS[k]["ts"] or 0)[:-300]:
        PICKS.pop(k, None)
    return out


def pick_buttons(entries: List[dict]) -> List[Tuple[str, str]]:
    return [(f"➕ {e['home'][:16]} {e['label']}", f"c:{e['fid']}:{e['kind']}") for e in entries]


def combo_text(items: List[dict]) -> str:
    if not items:
        return "Tu combinada de hoy está vacía. Toca los botones ➕ debajo de un análisis o del estudio del día."
    now = time.time()
    L = [f"🧾 Mi combinada de hoy ({len(items)} {'selecciones' if len(items) != 1 else 'selección'})", ""]
    p = 1.0
    for i, e in enumerate(items, 1):
        started = " (ya empezó)" if e.get("ts") and e["ts"] < now else ""
        L.append(f"{i}. {e['home']} vs {e['away']} · {e['label']} → {p_txt(e['p'])} ({hhmm(e['ts']) if e.get('ts') else '?'}){started}")
        p *= e["p"]
    L.append("")
    if len(items) == 1:
        L.append("Con una sola selección es una apuesta simple, no una combinada.")
    else:
        L.append(f"Probabilidad de ganar todas: {p_txt(p)} · cuota justa {fair(p)} → solo si tu casa paga ≥{min_odds(p):.2f}")
    if len(items) > 3:
        L.append("⚠️ Más de 3 selecciones: la probabilidad de ganar baja rápido.")
    L.append("Estimación, no garantía. Cada selección ya pasó el filtro ✅ (coinciden las lecturas disponibles).")
    return "\n".join(L)


def combo_buttons(items: List[dict]) -> Buttons:
    return [("🗑 Vaciar mi combinada", "c:0:clear")] if items else None


def get_market(fx: dict):
    """Probabilidades del mercado (casas de referencia) para el partido. (dict|None, motivo)."""
    if ODDS_FEED is None:
        return None, ""
    t0 = time.time()
    mk = ODDS_FEED.probs(fx)
    log(f"Mercado: {'ok ' + ','.join(mk['books']) if mk else 'sin datos (' + ODDS_FEED.last_error + ')'} en {time.time() - t0:.0f}s")
    return mk, ODDS_FEED.last_error


def edge_value_active(api: ApiFootball, store: Store) -> str:
    """Escanea vacíos de mercado SOLO cuando el usuario lo pide con /valor.

    No toca OpenAI ni historiales: usa el PRE ANCLA ya guardado. OddsPapi conserva su
    propia caché de 30 min, por lo que repetir /valor no debería gastar otra consulta
    mientras el snapshot siga vigente.
    """
    if not ACTIVE_FID[0]:
        return "No hay partido activo. Busca y analiza un partido primero; después escribe /valor."
    if ODDS_FEED is None:
        return "EDGE FINDER necesita ODDSPAPI_KEY para comparar tu modelo contra cuotas reales. El análisis normal sigue funcionando sin OddsPapi."
    snap = store.get_anchor(int(ACTIVE_FID[0]), PRE_ANCHOR_TTL)
    if not snap or snap.get("version") not in ANCHOR_COMPAT:
        return "Primero analiza el partido para crear su PRE ANCLA; después escribe /valor. Así no vuelvo a gastar OpenAI."
    fx = snap.get("fx") or {}
    fc, op, profile, quality = _snapshot_unpack(snap)
    mk, err = get_market(fx)
    if not mk:
        return "🕳️ EDGE FINDER\nNo pude obtener mercado comparable" + (f": {err}" if err else ".")
    edge_snapshot = EdgeMarketSnapshot(
        probs={"over": dict(mk.get("over") or {}), "under": dict(mk.get("under") or {})},
        prices=mk.get("prices"),
        books=mk.get("book_by_line") or mk.get("books"),
        age_seconds=None,
    )
    results = edge_scan_totals(
        p_over=dict(fc.p_over),
        p_under=dict(fc.p_under),
        snapshot=edge_snapshot,
        quality=quality,
        category=getattr(profile, "code", "senior"),
        disagreement=snap.get("disagreement"),
    )
    head = (f"⚽ {fx.get('home','?')} vs {fx.get('away','?')}\n"
            f"🧠 {getattr(profile, 'label', 'MAYORES')} · calidad {quality}/100\n")
    return head + edge_format_telegram(results)


def conservative_active(api: ApiFootball, store: Store) -> str:
    """Modo de alta exigencia. Reutiliza PRE ANCLA y el mismo snapshot de mercado de /valor."""
    if not ACTIVE_FID[0]:
        return "No hay partido activo. Analiza un partido primero y luego escribe /seguro."
    if ODDS_FEED is None:
        return "MODO CONSERVADOR necesita ODDSPAPI_KEY para comprobar la cuota real y el break-even."
    snap = store.get_anchor(int(ACTIVE_FID[0]), PRE_ANCHOR_TTL)
    if not snap or snap.get("version") not in ANCHOR_COMPAT:
        return "Primero analiza el partido para crear su PRE ANCLA; /seguro no vuelve a gastar OpenAI."
    fx = snap.get("fx") or {}
    fc, op, profile, quality = _snapshot_unpack(snap)
    mk, err = get_market(fx)
    if not mk:
        return "🛡️ MODO CONSERVADOR\nNo pude obtener mercado comparable" + (f": {err}" if err else ".")
    edge_snapshot = EdgeMarketSnapshot(
        probs={"over": dict(mk.get("over") or {}), "under": dict(mk.get("under") or {})},
        prices=mk.get("prices"), books=mk.get("book_by_line") or mk.get("books"), age_seconds=None)
    results = edge_scan_totals(p_over=dict(fc.p_over), p_under=dict(fc.p_under), snapshot=edge_snapshot,
                               quality=quality, category=getattr(profile,"code","senior"),
                               disagreement=snap.get("disagreement"))
    head = f"⚽ {fx.get('home','?')} vs {fx.get('away','?')}\n📚 Calidad {quality}/100\n"
    return head + format_conservative(results, snap.get("disagreement"), getattr(profile,"label","MAYORES"))


def min_odds(p_use: float) -> float:
    """Cuota mínima para que valga la pena: margen de seguridad (UNCERTAINTY) y ventaja mínima (EDGE_MIN)."""
    p_cons = max(p_use - UNCERTAINTY, 0.01)
    return math.ceil((1.0 + EDGE_MIN) / p_cons * 100 - 1e-9) / 100


def market_line(label: str, p: float, op, kind: str, line: float, small: bool, mk=None) -> str:
    p_ai = None if not op else op[kind].get(line)
    p_mkt = None if not mk else mk[kind].get(line)
    v = verdict_x(p, p_ai, small, p_mkt)
    out = f"{label}: {v} {shown(p, p_ai, p_mkt)}"
    if v.startswith("✅"):
        p_use, _ = blend(p, p_ai, p_mkt)
        out += f" → solo si tu casa paga ≥{min_odds(p_use):.2f}"
    elif v.startswith("🟡"):
        p_use, _ = blend(p, p_ai, p_mkt)
        if p_use - UNCERTAINTY > 0.5:
            out += f" → solo valdría la pena si tu casa paga ≥{min_odds(p_use):.2f}"
    if kind == "under" and line >= 6.5:
        out += " · ⚠️ línea muy alta: casi ninguna casa la ofrece"
    return out


def bet_lines(evals) -> List[str]:
    out = []
    for side, line, e in evals:
        name = f"{'+' if side == 'over' else '-'}{line:.1f}"
        if e.get("note"):
            out.append(f"Tu apuesta {name} @ {e['odds']:.2f}: {e['note'].split(':')[0]}")
        else:
            out.append(f"Tu apuesta {name} @ {e['odds']:.2f}: " + ("✅ cumple la regla" if e["value"] else "❌ no la cumple"))
    return out


def range_line(fc, op, small: bool, mk=None) -> str:
    """Línea del rango 1..techo (para apostar +0.5 y Under techo juntos). Vacía si ya hay gol."""
    if fc.cur_goals >= 1:
        return ""
    n = fc.ceiling
    p = range_prob(fc)
    p_ai = None
    if op and 0.5 in op["over"] and fc.ceiling_line in op["under"]:
        p_ai = max(0.0, op["over"][0.5] + op["under"][fc.ceiling_line] - 1.0)   # los dos eventos cubren todo
    p_mkt = None
    if mk and 0.5 in mk["over"] and fc.ceiling_line in mk["under"]:
        p_mkt = max(0.0, mk["over"][0.5] + mk["under"][fc.ceiling_line] - 1.0)
    v = verdict_x(p, p_ai, small, p_mkt)
    p_use, _ = blend(p, p_ai, p_mkt)
    out = f"Rango 1–{n} goles (+0.5 y Under {fc.ceiling_line:.1f} juntos): {v} {p_txt(p_use)} · cuota justa {fair(p_use)}"
    if v.startswith("✅"):
        out += f" · paga ≥{min_odds(p_use):.2f}"
    return out


def habla(fc, op, small: bool = False) -> str:
    """Frase en lenguaje de calle: ¿habrá gol (o un gol más)?"""
    cur = fc.cur_goals
    line = cur + 0.5
    p = fc.p_over.get(line)
    if p is None:
        return ""
    p_ai = None if not op else op["over"].get(line)
    p_use, disagree = blend(p, p_ai, None)
    what = "otro gol" if cur else "gol"
    if p_use >= 0.95:
        frase = f"Casi seguro que sale {what}"
    elif p_use >= 0.90:
        frase = f"Muy probable que salga {what}"
    elif p_use >= 0.80:
        frase = f"Probable que salga {what}, pero puede fallar"
    elif p_use >= 0.65:
        frase = f"Está parejo: algo más probable que salga {what}, pero no es seguro"
    elif p_use >= 0.50:
        frase = f"Dudoso: casi mitad y mitad"
    else:
        frase = f"Difícil que salga {what}"
    ok = " Ojo: bot e IA no coinciden." if disagree else ""
    cautela = " (partido poco fiable)" if small else ""
    return f"🗣️ {frase}{cautela}: de cada 10 partidos así, en ~{round(p_use * 10)} cae.{ok}"


def ladder_rows(fc, op, mk, small: bool, with_over: bool = True) -> List[str]:
    """Revisión completa: todas las líneas de «más de» y «menos de», una fila compacta cada lado."""
    cur = fc.cur_goals
    def cell(kind, sign, l):
        table = fc.p_over if kind == "over" else fc.p_under
        p = table.get(l)
        if p is None:
            return None
        p_ai = None if not op else op[kind].get(l)
        p_mk = None if not mk else mk[kind].get(l)
        v = verdict_x(p, p_ai, small, p_mk)
        p_use, _ = blend(p, p_ai, p_mk)
        icon = "⚪" if (_ai_on() and p_ai is None) else v[0]
        return f"{icon}{sign}{l:.1f} {int(p_use * 100)}%"
    out = []
    if with_over:
        cells = [cell("over", "+", l) for l in LINES if l > cur][:5]
        out.append("Más de:   " + " · ".join(c for c in cells if c))
    cells = [cell("under", "-", l) for l in LINES if l > cur and 1.5 <= l <= 7.5][:7]
    out.append("Menos de: " + " · ".join(c for c in cells if c))
    return out


def brief_line(label: str, p: float, op, kind: str, line: float, small: bool, mk=None) -> str:
    """Una línea corta: etiqueta, veredicto, probabilidad prudente y (si hay valor) la cuota mínima."""
    p_ai = None if not op else op[kind].get(line)
    p_mkt = None if not mk else mk[kind].get(line)
    v = verdict_x(p, p_ai, small, p_mkt)
    p_use, disagree = blend(p, p_ai, p_mkt)
    out = f"{label}: {v} {shown(p, p_ai, p_mkt)}"
    if (v.startswith("✅") or (v.startswith("🟡") and p_use - UNCERTAINTY > 0.5)) and not (kind == "under" and line >= 6.5):
        out += f" · paga ≥{min_odds(p_use):.2f}"
    if kind == "under" and line >= 6.5:
        out += " · línea rara, casi nadie la ofrece"
    return out


def simple_footer(small: bool, op, has_go: bool = False, mk=None, mk_err: str = "") -> List[str]:
    L = [""]
    warn = []
    if small:
        warn.append("Pocos datos de estos equipos: como mucho DUDOSO")
    if _ai_on() and op is None:
        warn.append(f"La IA no respondió ({(AI or AI2).last_error or 'sin motivo'})")
    if mk:
        warn.append(f"Mercado = cuotas de {', '.join(mk['books'])} sin margen (referencia, no es tu casa)")
    elif ODDS_FEED is not None and mk_err:
        warn.append(f"Mercado: sin datos ({mk_err})")
    if warn:
        L.append("⚠️ " + " · ".join(warn))
    who = "bot, IA y mercado" if mk else ("bot e IA" if _ai_on() else "")
    if has_go:
        L.append("«paga ≥X» = cuota mínima en tu casa; si paga menos, no apuestes.")
    L.append((f"APOSTAR = {who} coinciden y dan ≥{int(SAFE_PROB * 100)}%" if who else f"APOSTAR = el modelo da ≥{int(SAFE_PROB * 100)}%") + ". Estimación, no garantía.")
    return L


def simple_pre(fx: dict, fc, nh: int, na: int, evals, op=None, mk=None, mk_err: str = "", hist: str = "") -> str:
    small = min(nh, na) < 12
    L = [f"⚽ {fx['home']} vs {fx['away']} · {hhmm(fx['ts'])} (Lima)", f"{fx['league']}"] + ([hist] if hist else []) + [
         habla(fc, op, small)] + ctx_short(fx) + [""] + [
         brief_line("+0.5 goles", fc.p_over[0.5], op, "over", 0.5, small, mk),
         brief_line("+1.5 goles", fc.p_over[1.5], op, "over", 1.5, small, mk),
         brief_line(f"Máximo: Under {fc.ceiling_line:.1f}", fc.p_ceiling, op, "under", fc.ceiling_line, small, mk)]
    rl = range_line(fc, op, small, mk)
    if rl:
        L.append(rl)
    has_go = any("✅ APOSTAR" in x for x in L)
    L += ["", "📋 Revisé todas las líneas (✅ seguro · 🟡 dudoso · ❌ no · ⚪ sin IA):"] + ladder_rows(fc, op, mk, small)
    if evals:
        L += [""] + bet_lines(evals)
    return "\n".join(L + simple_footer(small, op, has_go, mk, mk_err))


def simple_live(fx: dict, fc, nh: int, na: int, evals, op=None, extra=(), caution: bool = False, entry=()) -> str:
    real_small = min(nh, na) < 12
    small = real_small or caution      # con expulsión el pronóstico previo pierde fiabilidad: como mucho DUDOSO
    cur = fc.cur_goals
    when = "Descanso" if fx["status"] == "HT" else f"{fc.elapsed}'"
    L = [f"🔴 {when} · {fx['home']} {fx.get('cur_h') or 0}-{fx.get('cur_a') or 0} {fx['away']} · {fx['league']}"]
    hb = habla(fc, op, small)
    if hb:
        L.append(hb)
    L += ctx_short(fx)
    L += list(entry) if entry else []
    L += list(extra)
    done = [l for l in LINES if l < cur][:4]
    if done:
        L.append("Ya cumplido: " + ", ".join(f"+{l:.1f}" for l in done) + " ✅")
    if not entry:
        for l in [x for x in LINES if x > cur][:2]:
            L.append(brief_line(f"+{l:.1f} goles", fc.p_over[l], op, "over", l, small))
    L.append(brief_line(f"Máximo: Under {fc.ceiling_line:.1f}", fc.p_ceiling, op, "under", fc.ceiling_line, small))
    if evals:
        L += [""] + bet_lines(evals)
    L += simple_footer(real_small, op, False)
    L.append("En vivo uso el pronóstico previo + marcador y minuto.")
    return "\n".join(L)


def ai_detail(op) -> List[str]:
    if not op:
        return []
    L = ["", "🤖 Segunda opinión (IA, independiente):"]
    if op.get("resumen"):
        L.append(op["resumen"])
    for r in op.get("riesgos") or []:
        L.append(f"• Riesgo: {r}")
    for r in op.get("faltan") or []:
        L.append(f"• Faltan datos: {r}")
    return L


# ---------- lógica ----------
def make_eval(p: float, odds: float) -> dict:
    e = evaluate_bet(p, odds, STAKE, EDGE_MIN, UNCERTAINTY)
    if p >= 0.9999:
        e["value"] = False
        e["note"] = "ya cumplida: no hay riesgo, solo importa cuánto paga la cuota"
    elif p <= 0.0001:
        e["value"] = False
        e["note"] = "ya imposible con el marcador actual"
    return e


def decide(p_model: float, side: str, line: float, odds: float, op, small: bool, mk=None) -> dict:
    """UNA sola regla para el mensaje, el registro y /stats:
    ✅ solo si bot e IA dan ≥ SAFE_PROB, coinciden, hay datos suficientes y la cuota llega a la mínima."""
    e = make_eval(p_model, odds)
    p_ai = None if not op else op[side].get(line)
    p_mkt = None if not mk else mk[side].get(line)
    p_use, _ = blend(p_model, p_ai, p_mkt)
    go = verdict_x(p_model, p_ai, small, p_mkt).startswith("✅") and odds >= min_odds(p_use) and not e.get("note")
    e["value"], e["p_ai"], e["p_mkt"] = bool(go), p_ai, p_mkt
    return e


def gather(api: ApiFootball, adjacent: bool = False) -> List[dict]:
    """Por economía consulta HOY; solo amplía ayer/mañana cuando la búsqueda no encontró nada."""
    today = datetime.now(ZoneInfo(TZ)).date()
    out, seen = [], set()
    for d in ((0, -1, 1) if adjacent else (0,)):
        try:
            rows = api.fixtures_by_date((today + timedelta(days=d)).isoformat(), TZ)
        except ApiError:
            if d == 0:
                raise
            continue
        for f in rows:
            if f["id"] not in seen:
                seen.add(f["id"])
                out.append(f)
    return out


def analyze_fixture(api: ApiFootball, store: Store, fx: dict, bets, detail: bool = False) -> Reply:
    tok = CTX_PEN.set(0.0)          # el ajuste de contexto vale solo para esta consulta
    try:
        return _analyze_fixture(api, store, fx, bets, detail)
    finally:
        CTX_PEN.reset(tok)


def _analyze_fixture(api: ApiFootball, store: Store, fx: dict, bets, detail: bool = False) -> Reply:
    ACTIVE_FID[0] = fx.get("id")
    st = fx["status"]
    if st in FINISHED:
        if fx["gh"] is None or fx["ga"] is None:
            return "El partido terminó pero la API aún no trae el marcador. Intenta en unos minutos.", None
        rec = store.load().get(str(fx["id"]))
        if rec and rec.get("total_goals") is None:
            store.settle(fx["id"], fx["gh"] + fx["ga"])
        return render_result(fx, bets, rec), None
    if st in NOT_STARTED or st in LIVE_OK:
        if not detail and TELEGRAM_PROGRESS:
            say("⏳ Analizando…")
        log(f"Analizando fixture {fx['id']} ({fx['home']} vs {fx['away']})")
        if st in NOT_STARTED:
            profile = classify(fx.get("home", ""), fx.get("away", ""), fx.get("league", ""), fx.get("country", ""))
            # PRE ANCLA: antes de gastar historial/web/OpenAI, reutiliza el primer estudio válido.
            snap = store.get_anchor(fx["id"], PRE_ANCHOR_TTL)
            if snap and snap.get("version") in ANCHOR_COMPAT:
                CONTEXT_CACHE[fx["id"]] = list(snap.get("context") or [])
                fc, op, profile, quality = _snapshot_unpack(snap)
                txt = unified_pre(fx, fc, op, profile, quality, snap.get("disagreement"), anchored=True)
                if detail:
                    txt += unified_detail(snap)
                    return txt, None
                return txt, [("📊 Ver detalle", f"d:{fx['id']}:{stash(bets)}")]

            hg = api.team_games(fx["home_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
            ag = api.team_games(fx["away_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
            if len(hg) < profile.min_games or len(ag) < profile.min_games:
                return (f"Pocos datos para {profile.label}: {fx['home']} {len(hg)}, {fx['away']} {len(ag)}. "
                        f"Necesito al menos {profile.min_games} por equipo; no fabrico porcentaje."), None

            base_fc = forecast(hg, ag, floor_prob=FLOOR_PROB, tail=TAIL, profile=profile)
            CONTEXT_CACHE[fx["id"]] = context_lines(api, fx, hg, ag)
            op = get_opinion(fx, base_fc, None, hg, ag)
            sov = build_sovereign(base_fc.p_total, op, profile, floor_risk=FLOOR_RISK, tail=TAIL)
            fc = _fc_from_sov(base_fc, sov)

            # La calidad mide cuán estudiable es el partido. Una IA caída resta confianza, no rompe la matemática.
            try:
                news = api.squad_news(fx["id"])
                lineup_count = len((news or {}).get("lineups") or {})
            except Exception:
                lineup_count = 0
            quality = study_quality(profile, len(hg), len(ag), op is not None, lineup_count,
                                    len(CONTEXT_CACHE.get(fx["id"]) or []),
                                    None if not op else op.get("quality"))
            hist = hist_line(hg, ag)

            mk, mk_err = (get_market(fx) if USE_ODDS_VALIDATION and bets else (None, ""))
            small = quality < profile.green_quality
            evals, signals = [], []
            for side, line, odds in bets:
                try:
                    p0 = market_prob(fc, side, line)
                except ValueError:
                    continue
                # Las apuestas escritas por el usuario se evalúan con la distribución soberana final.
                e = make_eval(p0, odds)
                c, ap = _line_status(p0, quality, profile, side, sov.disagreement)
                e["value"] = c == "🟢" and odds >= min_odds(p0) and not e.get("note")
                e["p_ai"] = None if not op else op.get(side, {}).get(line)
                e["p_mkt"] = None
                evals.append((side, line, e))
                signals.append({"side": side, "line": line, "odds": odds, "p": p0,
                                "p_ai": e["p_ai"], "p_mkt": None, "value": e["value"]})

            snap = _snapshot_pack(fx, fc, op, profile, quality, CONTEXT_CACHE.get(fx["id"]), hist,
                                  sov.disagreement, sov.ai_weight)
            snap = store.save_anchor_once(fx["id"], snap)
            # Si otro hilo ganó la carrera y guardó antes, usa EL PRIMER PRE, no el recién calculado.
            fc, op, profile, quality = _snapshot_unpack(snap)
            CONTEXT_CACHE[fx["id"]] = list(snap.get("context") or [])
            store.log(fx, fc, time.time(), signals, ai=op, mk=None)
            txt = unified_pre(fx, fc, op, profile, quality, snap.get("disagreement"), anchored=False)
            if evals:
                txt += "\n\n" + "\n".join(bet_lines(evals))
            if detail:
                txt += unified_detail(snap)
                return txt, None
            return txt, [("📊 Ver detalle", f"d:{fx['id']}:{stash(bets)}")]
        # LIVE: refresca datos; aquí sí se permiten nuevas consultas porque el partido cambió de estado.
        profile = classify(fx.get("home", ""), fx.get("away", ""), fx.get("league", ""), fx.get("country", ""))
        hg = api.team_games(fx["home_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
        ag = api.team_games(fx["away_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
        if len(hg) < profile.min_games or len(ag) < profile.min_games:
            return (f"Pocos datos para LIVE {profile.label}: {len(hg)} y {len(ag)}; mínimo {profile.min_games}."), None
        button = [("📊 Ver detalle", f"d:{fx['id']}:{stash(bets)}")]
        elapsed = 45 if st == "HT" else min(int(fx.get("elapsed") or 0), 90)
        cur = (fx.get("cur_h") or 0) + (fx.get("cur_a") or 0)
        fc = live_forecast(hg, ag, cur, elapsed, floor_prob=FLOOR_PROB, tail=TAIL, profile=classify(fx.get("home", ""), fx.get("away", ""), fx.get("league", ""), fx.get("country", "")))
        try:
            reds = api.red_cards(fx["id"])
        except ApiError:
            reds = []
        info = {"elapsed": elapsed, "cur_h": fx.get("cur_h") or 0, "cur_a": fx.get("cur_a") or 0, "reds": len(reds)}
        if reds:
            info["reds_txt"] = ", ".join(f"{r['team']} {r['minute']}'" for r in reds)
        pace, pace_txt = (None, "")
        eline = cur + 0.5
        if elapsed <= 80:
            pace, pace_txt = pace_info(api.live_stats(fx["id"]), elapsed)
            if pace_txt:
                info["stats_txt"] = pace_txt.split(": ", 1)[-1]
        op = get_opinion(fx, fc, info, hg, ag)
        sov = build_sovereign(fc.p_total, op, profile, floor_risk=FLOOR_RISK, tail=TAIL)
        fc = _fc_from_sov(fc, sov)
        try:
            news = api.squad_news(fx["id"])
            lineup_count = len((news or {}).get("lineups") or {})
        except Exception:
            lineup_count = 0
        quality = study_quality(profile, len(hg), len(ag), op is not None, lineup_count,
                                len(CONTEXT_CACHE.get(fx["id"]) or []),
                                None if not op else op.get("quality"))
        if reds:
            quality = max(0, quality - 4)

        evals = []
        for side, line, odds in bets:
            try:
                p0 = market_prob(fc, side, line)
            except ValueError:
                continue
            e = make_eval(p0, odds)
            c, _ = _line_status(p0, quality, profile, side, sov.disagreement)
            e["value"] = c == "🟢" and odds >= min_odds(p0) and not e.get("note")
            e["p_ai"] = None if not op else op.get(side, {}).get(line)
            e["p_mkt"] = None
            evals.append((side, line, e))

        extra = []
        pre_snap = store.get_anchor(fx["id"], PRE_ANCHOR_TTL)
        if pre_snap and pre_snap.get("version") in ANCHOR_COMPAT:
            pfc, _, _, _ = _snapshot_unpack(pre_snap)
            extra.append(f"📌 PRE ANCLA: +0.5 {_fmt_prob(pfc.p_over.get(0.5,0))} · U{pfc.ceiling_line:.1f} {_fmt_prob(pfc.p_ceiling)}")
        if pace_txt:
            extra.append(pace_txt)
        if reds:
            extra.append("🟥 Expulsión: " + ", ".join(f"{r['team']} ({r['minute']}')" for r in reds) + " · LIVE recalculado")

        txt = unified_live(fx, fc, op, profile, quality, sov.disagreement, extra)
        if evals:
            txt += "\n\n" + "\n".join(bet_lines(evals))
        if detail:
            txt += "\n\n🔎 AUDITORÍA LIVE"
            txt += f"\nGoles esperados restantes: {fc.lam_home + fc.lam_away:.2f}"
            txt += f"\nPeso IA en distribución LIVE: {sov.ai_weight*100:.0f}%"
            if sov.disagreement is not None:
                txt += f"\nBrecha modelo/IA: {sov.disagreement*100:.1f} pp"
            if op and op.get("resumen"):
                txt += "\n🤖 " + op["resumen"]
            return txt, None
        return txt, button
    if st in LIVE_ALL:
        return (f"🔴 {fx['home']} {fx.get('cur_h') or 0}-{fx.get('cur_a') or 0} {fx['away']} está en prórroga, "
                "penales o interrumpido. No analizo esa fase."), None
    return f"El partido está en estado «{st}» (aplazado, cancelado o suspendido). No hay nada que analizar.", None


def analyze_text(api: ApiFootball, store: Store, text: str) -> Reply:
    fid, q_home, q_away, bets = parse_query(text)
    if fid is not None:
        fx = api.fixture_by_id(fid, ttl=0)
        if not fx:
            return "No encontré ese id de partido.", None
        return analyze_fixture(api, store, fx, bets)
    if not q_away and len(norm(q_home)) < 3:
        return "Escribe al menos 3 letras del nombre del equipo.", None
    cands = search_fixtures(gather(api, adjacent=False), q_home, q_away)
    if not cands:
        cands = search_fixtures(gather(api, adjacent=True), q_home, q_away)
    if not cands:
        who = f"{q_home} vs {q_away}" if q_away else q_home
        return (f"No encontré «{who}» hoy ni en los días adyacentes. Prueba con otra parte del nombre."), None
    if len(cands) == 1:
        return analyze_fixture(api, store, cands[0], bets)
    tok = stash(bets)
    cands = cands[:MAX_OPTIONS]
    buttons = [(f"{status_label(f)} · {f['home']} vs {f['away']}"[:60], f"f:{f['id']}:{tok}") for f in cands]
    LAST_OPTS[:] = [(f["id"], tok) for f in cands]
    lines = "\n".join(f"{i}. {status_label(f)} · {f['home']} vs {f['away']} ({f['league']})" for i, f in enumerate(cands, 1))
    return f"Encontré {len(cands)} partidos:\n{lines}\n\nToca un botón o escribe el número (1-{len(cands)}).", buttons


def list_matches(api: ApiFootball, flt: str, only_live: bool = False) -> str:
    fx = gather(api)
    today = datetime.now(ZoneInfo(TZ)).date()
    fx = [f for f in fx if f["status"] in LIVE_ALL
          or (not only_live and f["ts"] and datetime.fromtimestamp(f["ts"], ZoneInfo(TZ)).date() == today)]
    if only_live:
        fx = [f for f in fx if f["status"] in LIVE_ALL]
    if flt:
        q = norm(flt)
        fx = [f for f in fx if q in norm(f"{f['league']} {f['country']} {f['home']} {f['away']}")]
    fx.sort(key=lambda f: (_prio(f), f["ts"] or 0))
    if not fx:
        return ("No hay partidos en juego ahora" if only_live else "No hay partidos hoy") + \
               (f" con «{flt}»." if flt else ".")
    lines = [f"{status_label(f)} · {f['home']} vs {f['away']} ({f['league']}) · id {f['id']}" for f in fx[:30]]
    extra = f"\n… y {len(fx) - 30} más. Usa un filtro: /hoy peru" if len(fx) > 30 else ""
    return ("Partidos en juego:\n" if only_live else "Partidos de hoy (hora de Lima):\n") + "\n".join(lines) + extra


def settle_pending(api: ApiFootball, store: Store, limit: int = 20) -> list:
    """Guarda el resultado real de lo analizado. Devuelve [(registro, partido)] de lo liquidado.

    Anula (no cuenta en las estadísticas) los partidos aplazados/cancelados o que siguen sin
    terminar 4 días después.
    """
    done, now = [], time.time()
    for r in store.pending(now)[:limit]:
        try:
            fx = api.fixture_by_id(r["id"], ttl=0)
        except ApiError:
            break                       # límite de la API o red: se reintenta en la próxima vuelta
        if not fx:
            continue
        if fx["status"] in FINISHED and fx["gh"] is not None and fx["ga"] is not None:
            store.settle(r["id"], fx["gh"] + fx["ga"])
            done.append((r, fx))
        elif fx["status"] in VOID_STATUS or now - r["kickoff_ts"] > 4 * 86400:
            store.void(r["id"])
    return done


def format_settled(done: list) -> str:
    L = ["📋 Resultados guardados:"]
    for r, fx in done:
        total = fx["gh"] + fx["ga"]
        ia = f" · IA {p_txt(r['p_ai05'])}" if isinstance(r.get("p_ai05"), (int, float)) else ""
        ceil = r["ceiling_line"]
        L.append(f"• {fx['home']} {fx['gh']}-{fx['ga']} {fx['away']} → "
                 f"+0.5 {'✅' if total >= 1 else '❌'} (bot {p_txt(r['p_over05'])}{ia}) · "
                 f"Under {ceil:.1f} {'✅' if total < ceil else '❌'}")
    L.append("Usa /stats para ver el acierto acumulado.")
    return "\n".join(L)


def settle(api: ApiFootball, store: Store) -> str:
    pend = len(store.pending(time.time()))
    if not pend:
        return "No hay partidos pendientes de liquidar."
    done = settle_pending(api, store)
    return format_settled(done) if done else f"{pend} partido(s) analizado(s) aún no terminaron o la API no trae el resultado."


def auto_settle_loop(api: ApiFootball, store: Store) -> None:
    """Hilo de fondo: guarda solo los resultados reales y avisa por Telegram."""
    time.sleep(120)                     # primera vuelta poco después de arrancar
    while True:
        try:
            done = settle_pending(api, store)
            if done and AUTO_NOTIFY:
                for chat in ALLOWED:
                    tg_send(chat, format_settled(done))
        except Exception:
            traceback.print_exc()
        time.sleep(max(5, SETTLE_EVERY_MIN) * 60)


def stats(store: Store) -> str:
    s = compute_stats(list(store.load().values()), STAKE)
    if not s["n"]:
        return f"Aún no hay partidos liquidados ({s['pending']} pendientes). Analiza partidos antes de que empiecen y usa /liquidar cuando terminen."
    o, c, g = s["over05"], s["ceiling"], s["signals"]
    L = [f"📊 Acierto real ({s['n']} partidos liquidados, {s['pending']} pendientes)",
         f"+0.5: el bot dijo {pct(o['mean_p'])} de media, ocurrió {pct(o['hit'])} (Brier {o['brier']:.3f})",
         f"Techo: el bot dijo {pct(c['mean_p'])} de media, se respetó {pct(c['hit'])} (Brier {c['brier']:.3f})",
         f"Apuestas que el bot habría recomendado (✅ y con cuota suficiente): {g['n']} · acertadas {g['won']} · resultado S/{g['profit']:+.0f} · ROI {g['roi'] * 100:+.1f}%"]
    ai = s.get("ai")
    if ai:
        L.append(f"IA vs bot en +0.5 ({ai['n']} partidos con ambos; menor Brier = mejor): "
                 f"bot {ai['brier_model']:.3f} · IA {ai['brier_ai']:.3f} · combinado {ai['brier_combo']:.3f}")
    if s["n"] < 100:
        L.append("⚠️ Menos de 100 partidos: la muestra aún no permite sacar conclusiones.")
    return "\n".join(L)


def answer(text: str, api: ApiFootball, store: Store) -> Reply:
    t = text.strip()
    low = t.lower()
    if low == "detalle":
        if not ACTIVE_FID[0]:
            return "No hay partido activo. Busca un equipo primero.", None
        snap = store.get_anchor(int(ACTIVE_FID[0]), PRE_ANCHOR_TTL)
        if snap and snap.get("version") in ANCHOR_COMPAT:
            fx = snap.get("fx") or {}
            fc, op, profile, quality = _snapshot_unpack(snap)
            return unified_pre(fx, fc, op, profile, quality, snap.get("disagreement"), anchored=True) + unified_detail(snap), None
        fx = api.fixture_by_id(int(ACTIVE_FID[0]), ttl=0)
        return analyze_fixture(api, store, fx, [], detail=True) if fx else ("No encontré el partido activo.", None)
    if low == "ahora":
        if not ACTIVE_FID[0]:
            return "No hay partido activo. Busca un equipo primero.", None
        fx = api.fixture_by_id(int(ACTIVE_FID[0]), ttl=0)
        return analyze_fixture(api, store, fx, []) if fx else ("No encontré el partido activo.", None)
    cmd, _, arg = t.partition(" ")
    cmd = cmd.lower().split("@")[0]
    if cmd in ("/start", "/ayuda", "/help"):
        return HELP, None
    if cmd == "/valor":
        return edge_value_active(api, store), None
    if cmd == "/seguro":
        return conservative_active(api, store), None
    if cmd == "/hoy":
        return list_matches(api, arg.strip()), None
    if cmd == "/vivo":
        return list_matches(api, arg.strip(), only_live=True), None
    if cmd == "/combinada":
        items = store.combo_items(today_str())
        return combo_text(items), combo_buttons(items)
    if cmd == "/meta":
        return meta_text(arg), None
    if cmd == "/banca":
        nums = re.findall(r"\d+(?:[.,]\d+)?", arg or "")
        if nums and ODDS_CACHE is not None:
            ODDS_CACHE.set("banca", float(nums[0].replace(",", ".")))
            return f"Guardé tu banca: S/{float(nums[0].replace(',', '.')):.0f}. Ahora te muestro los montos en soles.", None
        cur = get_bankroll()
        return (f"Tu banca guardada: S/{cur:.0f}. Para cambiarla: /banca 500" if cur else "Dime tu banca: /banca 500"), None
    if cmd == "/limpiar":
        store.combo_clear(today_str())
        return "Combinada vaciada.", None
    if cmd == "/liquidar":
        return settle(api, store), None
    if cmd == "/stats":
        return stats(store), None
    if cmd == "/partido":
        if not arg.strip():
            return HELP, None
        return analyze_text(api, store, arg)
    if t.startswith("/"):
        return HELP, None
    if t.isdigit() and len(t) <= 2 and LAST_OPTS:
        i = int(t)
        if 1 <= i <= len(LAST_OPTS):
            fid, tok = LAST_OPTS[i - 1]
            fx = api.fixture_by_id(fid, ttl=0)
            if not fx:
                return "No encontré ese partido.", None
            return analyze_fixture(api, store, fx, PENDING.get(tok, []))
        return f"Elige un número entre 1 y {len(LAST_OPTS)}.", None
    return analyze_text(api, store, t)


def answer_callback(data: str, api: ApiFootball, store: Store) -> Reply:
    """Botón pulsado: 'f:<id>:<token>' (elegir partido) o 'd:<id>:<token>' (ver detalle)."""
    mc = re.fullmatch(r"c:(\d+):([ou]|clear)", data or "")
    if mc:
        day = today_str()
        if mc.group(2) == "clear":
            store.combo_clear(day)
            return "Combinada vaciada.", None
        e = PICKS.get((int(mc.group(1)), mc.group(2)))
        if not e:
            return "No tengo guardada esa selección (el bot se reinició). Consulta el partido de nuevo y toca ➕.", None
        added = store.combo_add(e, day)
        items = store.combo_items(day)
        return ("➕ Añadida.\n\n" if added else "Esa selección ya estaba en tu combinada.\n\n") + combo_text(items), combo_buttons(items)
    m = re.fullmatch(r"([fd]):(\d+):(\d+)", data or "")
    if not m:
        return "Botón no válido.", None
    fid = int(m.group(2))
    if m.group(1) == "d":
        snap = store.get_anchor(fid, PRE_ANCHOR_TTL)
        if snap and snap.get("version") in ANCHOR_COMPAT:
            ACTIVE_FID[0] = fid
            fx = snap.get("fx") or {}
            fc, op, profile, quality = _snapshot_unpack(snap)
            return unified_pre(fx, fc, op, profile, quality, snap.get("disagreement"), anchored=True) + unified_detail(snap), None
    fx = api.fixture_by_id(fid, ttl=0)   # solo selección/AHORA necesitan estado fresco
    if not fx:
        return "No encontré ese partido.", None
    return analyze_fixture(api, store, fx, PENDING.get(int(m.group(3)), []), detail=(m.group(1) == "d"))


# ---------- /mejores: revisar varios partidos ----------
_scan_running = [False]


def _live_info(f: dict, fc) -> Optional[dict]:
    if f["status"] not in LIVE_OK:
        return None
    return {"elapsed": fc.elapsed, "cur_h": f.get("cur_h") or 0, "cur_a": f.get("cur_a") or 0}


def scan(api: ApiFootball, store: Store, include_live: bool = False, horizon_h: float = 14,
         max_scan: int = 0, title: str = "") -> Reply:
    """Modelo sobre los próximos partidos -> IA y mercado solo en los mejores -> lista, combinadas y botones ➕."""
    max_scan = max_scan or MAX_SCAN
    fixtures = gather(api)
    now = time.time()
    cands = [f for f in fixtures if (include_live and f["status"] in LIVE_OK)
             or (f["status"] in NOT_STARTED and f["ts"] and now - 600 < f["ts"] < now + horizon_h * 3600)]
    cands.sort(key=lambda f: (_prio(f), f["ts"] or 0))
    rows, api_err, tried, skipped, excluded = [], "", 0, 0, 0
    wide, narrow = {}, []
    for f in cands:
        txt = f"{f['league']} {f['home']} {f['away']}".lower()
        if any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", txt) for w in SCAN_EXCLUDE) \
                or f['home'].lower().endswith(" w") or f['away'].lower().endswith(" w"):
            excluded += 1
        elif f["status"] in NOT_STARTED and f.get("league_id"):
            wide.setdefault((f["league_id"], f["season"]), []).append(f)     # se resuelve por liga
        else:
            narrow.append(f)                                                  # en vivo o sin liga: por equipo
    leagues = sorted(wide.items(), key=lambda kv: -len(kv[1]))[:SCAN_LEAGUES]
    for (lid, season), fxs in leagues:
        try:
            lrows = api.league_rows(lid, season)
            extra = None
            for f in fxs:
                hg = _to_games(lrows, f["home_id"], f["ts"], HISTORY_GAMES)
                ag = _to_games(lrows, f["away_id"], f["ts"], HISTORY_GAMES)
                if min(len(hg), len(ag)) < 12:                                # inicio de temporada: sumar la anterior
                    if extra is None:
                        extra = api.league_rows(lid, season - 1)
                    hg = _to_games(lrows + extra, f["home_id"], f["ts"], HISTORY_GAMES)
                    ag = _to_games(lrows + extra, f["away_id"], f["ts"], HISTORY_GAMES)
                if len(hg) < MIN_GAMES or len(ag) < MIN_GAMES:
                    skipped += 1
                    continue
                rows.append({"fx": f, "fc": forecast(hg, ag, floor_prob=FLOOR_PROB, tail=TAIL),
                             "small": min(len(hg), len(ag)) < 12, "op": None, "hg": hg, "ag": ag})
        except ApiError as e:
            api_err = str(e)
            break
    for f in ([] if api_err else narrow):
        if len([r for r in rows if r["fx"]["status"] in LIVE_OK]) >= max_scan or tried >= SCAN_ATTEMPTS:
            break
        tried += 1
        try:
            hg = api.team_games(f["home_id"], f["season"], f["ts"], n=HISTORY_GAMES)
            ag = api.team_games(f["away_id"], f["season"], f["ts"], n=HISTORY_GAMES)
        except ApiError as e:
            api_err = str(e)
            break
        if len(hg) < MIN_GAMES or len(ag) < MIN_GAMES:
            skipped += 1
            continue
        if f["status"] in LIVE_OK:
            el = 45 if f["status"] == "HT" else min(int(f.get("elapsed") or 0), 90)
            cur = (f.get("cur_h") or 0) + (f.get("cur_a") or 0)
            fc = live_forecast(hg, ag, cur, el, floor_prob=FLOOR_PROB, tail=TAIL)
        else:
            fc = forecast(hg, ag, floor_prob=FLOOR_PROB, tail=TAIL)
        rows.append({"fx": f, "fc": fc, "small": min(len(hg), len(ag)) < 12, "op": None, "hg": hg, "ag": ag})
    if not rows:
        return (("No pude revisar partidos: " + api_err) if api_err else
                "No hay partidos por empezar con datos suficientes." + ("" if include_live else " Para incluir los que están en juego: /mejores vivo")), None
    good = [r for r in rows if r["fc"].cur_goals >= 1 or r["fc"].p_over[0.5] >= MAYBE_PROB]
    # primero partidos de marcador típico (techo ≤ 6.5): los de muchísimos goles son partidos desparejos donde el modelo exagera
    good.sort(key=lambda r: (r["fc"].ceiling_line > 6.5, -(1.0 if r["fc"].cur_goals >= 1 else r["fc"].p_over[0.5])))
    top = good[:MAX_AI]
    if (AI is not None or AI2 is not None) and top:
        with ThreadPoolExecutor(max_workers=len(top)) as ex:
            ops = list(ex.map(lambda r: get_opinion(r["fx"], r["fc"], _live_info(r["fx"], r["fc"]), r.get("hg"), r.get("ag")), top))
        for r, op in zip(top, ops):
            r["op"] = op
    mk_asked, mk_ok, mk_why = 0, 0, ""
    for r in top[:MAX_ODDS_SCAN]:             # el mercado cuesta cupo: solo a los mejores finalistas, antes del partido
        if r["fx"]["status"] in NOT_STARTED:
            r["mk"], why = get_market(r["fx"])
            mk_asked += 1
            mk_ok += 1 if r["mk"] else 0
            mk_why = mk_why or why
    ranked = sorted(rows, key=lambda r: -(1.0 if r["fc"].cur_goals >= 1 else r["fc"].p_over[0.5]))
    for r in ranked[:10]:   # los 10 mejores en PRE también se registran, para medir el acierto
        if r["fx"]["status"] in NOT_STARTED:
            store.log(r["fx"], r["fc"], time.time(), [], ai=r["op"], mk=r.get("mk"))
    out, picks, entries = [], [], []
    for r in top:
        f, fc, op, small, mk = r["fx"], r["fc"], r["op"], r["small"], r.get("mk")
        head = f"{status_label(f)} · {f['home']} vs {f['away']} ({f['league']})"
        if fc.cur_goals >= 1:
            l05 = "+0.5: ya cumplido ✅"
        else:
            l05 = market_line("+0.5", fc.p_over[0.5], op, "over", 0.5, small, mk)
        ltop = market_line(f"Techo Under {fc.ceiling_line:.1f}", fc.p_ceiling, op, "under", fc.ceiling_line, small, mk)
        out.append("\n".join([head, "  " + hist_line(r.get("hg") or [], r.get("ag") or []), "  " + l05, "  " + ltop]))
        es = pick_entries(f, fc, op, mk, small)
        entries += es
        picks += [e for e in es if e["kind"] == "o"]
    L = [title] if title else []
    L += [f"🔎 Revisé {len(rows)} partidos de {len(leagues)} ligas con el modelo" + (f" ({skipped} sin datos suficientes)" if skipped else "")
          + f" y consulté a la IA en los {len(top)} mejores.",
          "Excluí amistosos de clubes y juveniles/reservas (SCAN_EXCLUDE)." if excluded else "", ""]
    L += out if out else ["Ningún partido llega al nivel mínimo por ahora."]
    if ODDS_FEED is not None and mk_asked:
        L.append(f"\n📈 Mercado: {mk_ok} de {mk_asked} finalistas con cuotas de referencia" + (f" ({mk_why})" if mk_ok < mk_asked and mk_why else "") + ".")
    elif ODDS_FEED is None:
        L.append("\n📈 Mercado apagado (falta ODDSPAPI_KEY).")
    if api_err:
        L.append(f"\n⚠️ Me detuve antes de terminar: {api_err}")
    picks.sort(key=lambda e: -e["p"])
    combos = []
    for k in (2, 3):
        if len(picks) >= k:
            p = 1.0
            for e in picks[:k]:
                p *= e["p"]
            combos.append(f"• Los {k} mejores +0.5 juntos: acierto {p_txt(p)}, cuota justa {fair(p)} → solo si tu casa paga ≥{min_odds(p):.2f}")
    if combos:
        L += ["", "🎯 Combinadas posibles (solo +0.5 de los ✅):"] + combos
        L.append("Cada partido que sumas baja la probabilidad de ganar. Con 2 o 3 tiene sentido; con más, no.")
    elif out:
        L += ["", "No hay dos ✅ en +0.5 para combinar por ahora."]
    if entries:
        L += ["", "Toca ➕ para añadir una selección ✅ a tu combinada (/combinada la muestra)."]
    L += ["", "Para el techo: usa en tu casa una línea igual o MÁS ALTA que la del bot, nunca más baja.",
          "Estimación, no garantía. Escribe el nombre de un equipo para ver su detalle."]
    return "\n".join(L), (pick_buttons(entries[:8]) or None)


# ---------- /entrada: pre + en vivo, ¿entro ahora a +0.5? ----------
FRIENDLY_WARN = "⚠️ Amistoso, juvenil o reserva: se cambian muchos jugadores y el ritmo es raro. Poco fiable: como mucho DUDOSO."


def _excluded(f: dict) -> bool:
    txt = f"{f['league']} {f['home']} {f['away']}".lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", txt) for w in SCAN_EXCLUDE) \
        or f["home"].lower().endswith(" w") or f["away"].lower().endswith(" w")


def pace_info(stats: dict, el: int):
    """-> (nivel, texto). Compara remates y córners con lo normal para ese minuto (~25 remates y ~10 córners por partido)."""
    if not stats or el < 8:
        return None, ""
    # puntaje = remates + 1.5 × a puerta + 0.5 × córners; lo normal en un partido entero ≈ 25 + 13.5 + 5 = 43.5
    got = stats.get("shots", 0) + 1.5 * stats.get("sot", 0) + 0.5 * stats.get("corners", 0)
    ratio = got / max(1.0, 43.5 * el / 90.0)
    level = "alto" if ratio >= 1.25 else ("bajo" if ratio <= 0.65 else "normal")
    icon = {"alto": "🔥", "normal": "➖", "bajo": "❄️"}[level]
    txt = f"{stats.get('shots', 0)} remates ({stats.get('sot', 0)} a puerta), {stats.get('corners', 0)} córners en {el}'"
    return level, f"{icon} ritmo {level}: {txt}"


def get_bankroll() -> float:
    try:
        v = ODDS_CACHE.get("banca", 10 * 365 * 86400) if ODDS_CACHE is not None else None
        return float(v) if v else 0.0
    except (TypeError, ValueError):
        return 0.0


def meta_text(arg: str) -> str:
    """/meta 25 1.10 [acierto%]: cuánto hay que apostar para ganar X, y qué riesgo real implica."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", arg or "")
    if len(nums) < 2:
        return "Uso: /meta GANANCIA CUOTA [ACIERTO%]\nEjemplo: /meta 25 1.10   (quiero ganar S/25 a cuota 1.10)"
    gain, odds = float(nums[0].replace(",", ".")), float(nums[1].replace(",", "."))
    hit = float(nums[2].replace(",", ".")) / 100 if len(nums) > 2 else 0.95
    if odds <= 1.0 or gain <= 0 or not 0 < hit <= 1:
        return "Revisa los números: la cuota debe ser mayor que 1, y el acierto entre 1 y 100."
    stake = gain / (odds - 1)
    bank = get_bankroll()
    ev = hit * gain - (1 - hit) * stake
    L = [f"🎯 Para ganar S/{gain:.0f} a cuota {odds:.2f} tienes que apostar S/{stake:.0f}."]
    if bank:
        L.append(f"Eso es el {stake / bank * 100:.0f}% de tu banca (S/{bank:.0f}).")
    L += [f"Si pierdes: -S/{stake:.0f}. Para no perder a la larga necesitas acertar {1 / odds * 100:.1f}% de las veces.",
          f"Con {hit * 100:.0f}% de acierto: resultado esperado por apuesta {'+' if ev >= 0 else '-'}S/{abs(ev):.2f}.",
          f"Probabilidad de fallar al menos 1 de 5 apuestas seguidas: {(1 - hit ** 5) * 100:.0f}%.",
          f"Con el monto prudente (2% de la banca{'' if bank else ': dime tu banca con /banca 500'}) "
          + (f"apostarías S/{bank * 0.02:.0f} y ganarías S/{bank * 0.02 * (odds - 1):.2f}." if bank else "ganarías mucho menos."),
          f"Para ganar S/{gain:.0f} con solo 2% de riesgo necesitarías una banca de S/{stake / 0.02:.0f}.",
          "Estimación, no garantía."]
    return "\n".join(L)


def kelly_pct(p_cons: float, odds: float, frac: float = 0.25, cap: float = 0.02) -> float:
    """Porcentaje de tu banca (cuarto de Kelly, tope 3%). 0 si no hay ventaja."""
    if odds <= 1.0:
        return 0.0
    k = (p_cons * odds - 1.0) / (odds - 1.0)
    return max(0.0, min(cap, k * frac))


def entry_verdict(p_live: float, p_ai: Optional[float], small: bool, pace: Optional[str] = None):
    """-> (veredicto, p_prudente, cuota_minima). Primero manda la probabilidad: si es baja, ❌ sin importar lo demás."""
    p_use, disagree = blend(p_live, p_ai, None)
    need = min_odds(p_use)
    if p_use < ENTRY_MID:
        return "❌ NO ENTRES", p_use, need
    if _ai_on() and p_ai is None:
        return "🟡 ESPERA (IA sin respuesta)", p_use, need
    if disagree:
        return "🟡 ESPERA (bot e IA difieren)", p_use, need
    if small:
        return "🟡 ESPERA (pocos datos o partido poco fiable)", p_use, need
    if pace == "bajo" and p_use >= ENTRY_ENTER - 0.05:
        return "🟡 ESPERA (sin ritmo)", p_use, need
    if p_use >= ENTRY_ENTER:
        return f"🟢 ENTRA si paga ≥{need:.2f}", p_use, need
    return f"🟡 SOLO con cuota ≥{need:.2f} (riesgo medio)", p_use, need


ENTRY_MID = float(os.getenv("ENTRY_MID", "0.75"))      # entre MID y ENTER: solo con cuota alta
ENTRY_ENTER = float(os.getenv("ENTRY_ENTER", "0.90"))   # probabilidad prudente mínima para decir ENTRA


def cur_h_a(fx: dict) -> str:
    return f"{fx.get('cur_h') or 0}-{fx.get('cur_a') or 0}"


def entry_lines(fc, op, small: bool, pace: Optional[str], pace_txt: str = "") -> List[str]:
    """Bloque de entrada: una fila por línea (goles actuales +0.5, +1.5, +2.5) con probabilidad y CUOTA MÍNIMA.
    Tú miras qué líneas ofrece tu casa y comparas con el mínimo de esa fila."""
    cur = fc.cur_goals
    rows = []
    for k in range(3):
        line = cur + 0.5 + k
        p = fc.p_over.get(line)
        if p is None:
            continue
        p_ai = None if not op else op["over"].get(line)
        v, p_use, need = entry_verdict(p, p_ai, small, pace)
        icon = "⚪" if (_ai_on() and p_ai is None) else v[0]
        stake = kelly_pct(max(0.01, p_use - UNCERTAINTY), need) * 100
        rows.append((line, p_use, need, icon, v, stake))
    if not rows:
        return []
    el = fc.elapsed
    if el < ENTRY_FROM:
        win = f"⏱️ AÚN NO: la ventana abre al minuto {ENTRY_FROM} (faltan ~{ENTRY_FROM - el} min). Vuelve a preguntar entonces."
    elif el <= ENTRY_TO:
        win = f"⏱️ VENTANA ABIERTA: minuto {el}, buena hasta el {ENTRY_TO}' (quedan ~{ENTRY_TO - el} min)."
    else:
        win = f"⏱️ VENTANA CERRADA: pasó el minuto {ENTRY_TO}. La ventaja suele caer: entra solo con cuota alta."
    out = [win, "🎯 ¿Qué línea? Elige la que tu casa ofrezca y compara con su mínimo:"]
    for line, p_use, need, icon, v, stake in rows:
        bank = get_bankroll()
        soles = f" (S/{bank * stake / 100:.0f})" if bank else ""
        out.append(f"   {icon} +{line:.1f}: prob. {p_txt(p_use)} · entra si paga ≥{need:.2f} · banca ≤{stake:.1f}%{soles}")
    best = next((r for r in rows if r[3] in "🟢🟡"), None)
    if best:
        out.append(f"   👉 {best[4]}")
    else:
        out.append("   👉 ❌ NO ENTRES en ninguna línea ahora")
    if pace_txt:
        out.append(f"   {pace_txt}")
    return out


def entry_radar(api: ApiFootball, store: Store) -> str:
    fixtures = gather(api)
    cands = []
    for f in fixtures:
        if f["status"] != "1H" or _excluded(f):
            continue
        el = int(f.get("elapsed") or 0)
        if (f.get("cur_h") or 0) + (f.get("cur_a") or 0) == 0 and ENTRY_FROM <= el <= ENTRY_TO:
            cands.append(f)
    if not cands:
        return (f"Ahora no hay partidos 0-0 entre el minuto {ENTRY_FROM} y el {ENTRY_TO} (en tiempo reglamentario). "
                "Pregúntame de nuevo en unos minutos, cuando haya partidos recién empezados.")
    cands.sort(key=lambda f: int(f.get("elapsed") or 0))
    rows, api_err = [], ""
    for f in cands[:ENTRY_MAX]:
        try:
            hg = api.team_games(f["home_id"], f["season"], f["ts"], n=HISTORY_GAMES)
            ag = api.team_games(f["away_id"], f["season"], f["ts"], n=HISTORY_GAMES)
        except ApiError as e:
            api_err = str(e)
            break
        if len(hg) < MIN_GAMES or len(ag) < MIN_GAMES:
            continue
        pre = forecast(hg, ag, floor_prob=FLOOR_PROB, tail=TAIL, profile=classify(f.get("home", ""), f.get("away", ""), f.get("league", ""), f.get("country", "")))
        if pre.p_over[0.5] < ENTRY_PRE_MIN:
            continue
        el = int(f.get("elapsed") or 0)
        fc = live_forecast(hg, ag, 0, el, floor_prob=FLOOR_PROB, tail=TAIL, profile=classify(f.get("home", ""), f.get("away", ""), f.get("league", ""), f.get("country", "")))
        rows.append({"fx": f, "pre": pre, "fc": fc, "hg": hg, "ag": ag, "small": min(len(hg), len(ag)) < 12})
    if not rows:
        return ("Hay partidos 0-0 en juego, pero ninguno tenía antes del inicio una probabilidad de gol tan alta "
                f"(≥{int(ENTRY_PRE_MIN * 100)}%) ni datos suficientes. No hay entrada recomendable ahora." +
                (f"\n({api_err})" if api_err else ""))
    rows.sort(key=lambda r: -r["fc"].p_over[0.5])
    rows = rows[:MAX_AI]
    for r in rows:
        r["pace"], r["pace_txt"] = pace_info(api.live_stats(r["fx"]["id"]), r["fc"].elapsed)
    if _ai_on():
        with ThreadPoolExecutor(max_workers=len(rows)) as ex:
            def _info(r):
                i = _live_info(r["fx"], r["fc"])
                if i and r.get("pace_txt"):
                    i = {**i, "stats_txt": r["pace_txt"].split(": ", 1)[-1]}
                return i
            ops = list(ex.map(lambda r: get_opinion(r["fx"], r["fc"], _info(r), r["hg"], r["ag"]), rows))
        for r, op in zip(rows, ops):
            r["op"] = op
    out = []
    for r in rows:
        f, fc, pre, op = r["fx"], r["fc"], r["pre"], r.get("op")
        p_ai = None if not op else op["over"].get(0.5)
        out.append("\n".join([
            f"{status_label(f)} · {f['home']} vs {f['away']} ({f['league']})",
            "  " + hist_line(r["hg"], r["ag"]),
            f"  Antes: +0.5 {p_txt(pre.p_over[0.5])} → ahora (minuto {fc.elapsed}): {shown(fc.p_over[0.5], p_ai)}",
            *entry_lines(fc, op, r["small"], r.get("pace"), r.get("pace_txt", "")),
        ]))
    return ("🎯 Partidos 0-0 en juego (pre + vivo):\n\n" + "\n\n".join(out)
            + "\n\n💰 = cuarto de Kelly, tope 2% de tu banca. Máximo 2 apuestas abiertas. Si tu casa paga menos que el mínimo, no entres; "
            "vuelve a preguntar en unos minutos (el mínimo cambia con el reloj). Estimación, no garantía.")


def start_entry(chat_id, api: ApiFootball, store: Store) -> None:
    import threading
    if _scan_running[0]:
        tg_send(chat_id, "Ya estoy revisando; espera el resultado.")
        return

    def job():
        _scan_running[0] = True
        try:
            tg_send(chat_id, "⏳ Buscando partidos 0-0 en juego… un momento.")
            tg_send(chat_id, entry_radar(api, store))
        except ApiError as e:
            tg_send(chat_id, f"Error de API-Football: {e}")
        except Exception:
            traceback.print_exc()
            tg_send(chat_id, "No pude completar la revisión. Revisa los logs.")
        finally:
            _scan_running[0] = False
    threading.Thread(target=job, daemon=True).start()


def scan_best(api: ApiFootball, store: Store, include_live: bool = False) -> str:
    return scan(api, store, include_live)[0]


def hours_left_today() -> float:
    now = datetime.now(ZoneInfo(TZ))
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (end - now).total_seconds() / 3600)


def daily_report(api: ApiFootball, store: Store) -> None:
    text, buttons = scan(api, store, horizon_h=hours_left_today(), max_scan=DAILY_SCAN,
                         title=f"🌅 Estudio del día ({today_str()}) — partidos que aún no empiezan, hora de Lima")
    for chat in ALLOWED:
        tg_send(chat, text, buttons)


def daily_report_loop(api: ApiFootball, store: Store) -> None:
    """Hilo de fondo: a la hora configurada envía solo el estudio del día (una vez por día)."""
    while True:
        try:
            now = datetime.now(ZoneInfo(TZ))
            key = "dailyrep|" + now.date().isoformat()
            if DAILY_REPORT_HOUR >= 0 and now.hour == DAILY_REPORT_HOUR and ODDS_CACHE is not None \
                    and ODDS_CACHE.get(key, 2 * 86400) is None:
                ODDS_CACHE.set(key, True)          # primero la marca: si falla no se repite en bucle
                daily_report(api, store)
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def start_scan(chat_id, api: ApiFootball, store: Store, include_live: bool = False) -> None:
    import threading
    if _scan_running[0]:
        tg_send(chat_id, "Ya estoy revisando; espera el resultado.")
        return

    def job():
        _scan_running[0] = True
        try:
            tg_send(chat_id, "⏳ Revisando los próximos partidos… tarda alrededor de 1 minuto.")
            text, buttons = scan(api, store, include_live)
            tg_send(chat_id, text, buttons)
        except ApiError as e:
            tg_send(chat_id, f"Error de API-Football: {e}")
        except Exception:
            traceback.print_exc()
            tg_send(chat_id, "No pude completar la revisión. Revisa los logs.")
        finally:
            _scan_running[0] = False
    threading.Thread(target=job, daemon=True).start()


# ---------- prueba con partidos pasados ----------
_bt_running = [False]


def backtest_report() -> str:
    import backtest
    groups, failed = backtest.run_groups(os.path.join(DATA_DIR, "fd"), FLOOR_PROB, TAIL)
    groups = [(label, rows) for label, rows in groups if rows]
    if not groups:
        return "No pude descargar los partidos pasados (football-data.co.uk). Intenta de nuevo más tarde."
    parts = [backtest.summary(rows, SAFE_PROB, MAYBE_PROB, label, footer=(i == len(groups) - 1))
             for i, (label, rows) in enumerate(groups)]
    return "\n\n".join(parts) + (f"\n\n(No se pudieron bajar {failed} archivos.)" if failed else "")


def start_backtest(chat_id) -> None:
    import threading
    if _bt_running[0]:
        tg_send(chat_id, "Ya estoy probando; espera el resultado.")
        return

    def job():
        _bt_running[0] = True
        try:
            tg_send(chat_id, backtest_report())
        except Exception:
            traceback.print_exc()
            tg_send(chat_id, "No pude completar la prueba. Revisa los logs.")
        finally:
            _bt_running[0] = False

    tg_send(chat_id, "🧪 Probando con partidos pasados reales (sin mirar el futuro)... tarda cerca de 1 minuto.")
    threading.Thread(target=job, daemon=True).start()


# ---------- Telegram ----------
def tg_send(chat_id, text: str, buttons: Buttons = None) -> None:
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for i, ch in enumerate(chunks):
        data = {"chat_id": chat_id, "text": ch}
        if buttons and i == len(chunks) - 1:
            data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d}] for t, d in buttons]})
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=20)


def tg_ack(cb_id: str) -> None:
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery", data={"callback_query_id": cb_id}, timeout=10)
    except requests.RequestException:
        pass


def handle_update(u: dict, api: ApiFootball, store: Store) -> None:
    cq = u.get("callback_query")
    msg = u.get("message") or (cq or {}).get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = None if cq else msg.get("text")
    if chat_id is None or (not cq and not text):
        return
    if not ALLOWED:  # primer arranque: solo ayuda a descubrir el chat id
        if text and text.startswith("/start"):
            tg_send(chat_id, f"Tu chat id es {chat_id}. Ponlo en la variable TELEGRAM_CHAT_ID y reinicia el bot.")
        return
    if str(chat_id) not in ALLOWED:
        return
    _progress_chat[0] = chat_id
    try:
        if cq:
            tg_ack(cq["id"])
            reply, buttons = answer_callback(cq.get("data", ""), api, store)
            tg_send(chat_id, reply, buttons)
        elif text.strip().lower().split("@")[0] == "/probar":
            start_backtest(chat_id)
        elif text.strip().lower().split("@")[0] == "/oddspapi":
            if not ODDS_KEY:
                tg_send(chat_id, "Falta la variable ODDSPAPI_KEY en Railway.")
            else:
                k = ODDS_KEY.strip().strip("'\"")
                huella = f"{len(k)} caracteres" + (f", empieza por «{k[:4]}» y termina en «{k[-3:]}»" if len(k) >= 20 else "")
                tg_send(chat_id, f"Clave guardada en Railway (ODDSPAPI_KEY): {huella}. Compárala con la de tu panel de OddsPapi.")
                tg_send(chat_id, discover(OddsPapi(ODDS_KEY, ODDS_BASE, cache=ODDS_CACHE), log))
        elif text.strip().lower().split(" ")[0].split("@")[0] in ("/entrada", "/entrar"):
            start_entry(chat_id, api, store)
        elif text.strip().lower().split(" ")[0].split("@")[0] == "/mejores":
            start_scan(chat_id, api, store, include_live=text.strip().lower().endswith("vivo"))
        else:
            reply, buttons = answer(text, api, store)
            tg_send(chat_id, reply, buttons)
    except ApiError as e:
        tg_send(chat_id, f"Error de API-Football: {e}")
    except OddsError as e:
        tg_send(chat_id, f"OddsPapi: {e}")
    except ValueError as e:
        tg_send(chat_id, str(e))
    except Exception:
        traceback.print_exc()
        tg_send(chat_id, "Error interno. Revisa los logs.")
    finally:
        _progress_chat[0] = None


def main() -> None:
    if not TOKEN:
        sys.exit("Falta TELEGRAM_BOT_TOKEN")
    global AI, AI2
    cache = Cache(os.path.join(DATA_DIR, "cache"))
    api = ApiFootball(API_KEY, cache, min_gap=API_MIN_GAP)
    global API
    API = api
    global ODDS_CACHE
    ODDS_CACHE = cache
    global ODDS_FEED
    if ODDS_KEY:
        ODDS_FEED = OddsFeed(OddsPapi(ODDS_KEY, ODDS_BASE, cache=cache), cache, ODDS_DAILY_CAP, log)
    store = Store(DATA_DIR)
    if OPENAI_KEY:
        AI = OpenAIClient(OPENAI_KEY, OPENAI_MODEL, cache, use_web=OPENAI_WEB, deadline=OPENAI_TIMEOUT)
    if ANTHROPIC_KEY and USE_SECOND_AI:
        AI2 = ClaudeClient(ANTHROPIC_KEY, ANTHROPIC_MODEL, cache, use_web=ANTHROPIC_WEB, deadline=OPENAI_TIMEOUT)
    print(f"Bot iniciado · datos en {DATA_DIR} · chats permitidos: {len(ALLOWED)} · "
          f"IA: {'activada, modelo ' + OPENAI_MODEL if AI else 'desactivada (falta OPENAI_API_KEY)'}"
          f"{' + Claude ' + ANTHROPIC_MODEL if AI2 else ''} · "
          f"techo TAIL={TAIL} · SAFE_PROB={SAFE_PROB} · "
          f"mercado: {'OddsPapi (tope ' + str(ODDS_DAILY_CAP) + '/día)' if ODDS_FEED else 'apagado (falta ODDSPAPI_KEY)'} · "
          f"estudio diario: {'a las ' + str(DAILY_REPORT_HOUR) + ':00 (' + TZ + ')' if DAILY_REPORT_HOUR >= 0 else 'apagado'} · "
          f"liquidación automática: {'cada ' + str(SETTLE_EVERY_MIN) + ' min' if SETTLE_EVERY_MIN > 0 else 'apagada'}", flush=True)
    if SETTLE_EVERY_MIN > 0:
        import threading
        threading.Thread(target=auto_settle_loop, args=(api, store), daemon=True).start()
    if DAILY_REPORT_HOUR >= 0:
        import threading
        threading.Thread(target=daily_report_loop, args=(api, store), daemon=True).start()
    offset = None
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                             params={"timeout": 25, "offset": offset,
                                     "allowed_updates": json.dumps(["message", "callback_query"])}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                log("update: " + ("botón " + str((u.get("callback_query") or {}).get("data")) if u.get("callback_query") else "mensaje"))
                handle_update(u, api, store)
        except Exception as e:  # red caída, 409 por doble instancia, etc.
            print("loop:", repr(e), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
