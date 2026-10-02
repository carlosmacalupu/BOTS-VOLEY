"""Bot de fútbol por Telegram (PRE-partido): +0.5, +1.5 y techo (Under).

Despliegue: ver README.md. Variables de entorno: ver .env.example.
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from apifootball import ApiError, ApiFootball, Cache, FINISHED, NOT_STARTED
from model import MIN_GAMES, LINES, evaluate_bet, forecast, market_prob
from store import Store, compute_stats

# ---------- configuración ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()}
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
DATA_DIR = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "./data"
TZ = os.getenv("TIMEZONE", "America/Lima")
STAKE = float(os.getenv("STAKE", "500"))
EDGE_MIN = float(os.getenv("EDGE_MIN", "0.02"))
UNCERTAINTY = float(os.getenv("UNCERTAINTY_PP", "2.0")) / 100.0
FLOOR_PROB = float(os.getenv("FLOOR_PROB", "0.93"))
TAIL = float(os.getenv("TAIL", "0.03"))
HISTORY_GAMES = int(os.getenv("HISTORY_GAMES", "20"))

HELP = (
    "⚽ Bot de fútbol (PRE-partido)\n\n"
    "/partido Local vs Visitante\n"
    "/partido Local vs Visitante | +0.5 1.10 | -5.5 1.12\n"
    "   (+ = más de, - = menos de; después de la línea va la cuota de Betano)\n"
    "/partido 1234567   (por id de partido)\n"
    "/hoy [filtro]   lista los partidos de hoy (hora de Lima)\n"
    "/liquidar   registra los resultados reales de lo ya analizado\n"
    "/stats   acierto real acumulado del bot\n\n"
    "Busco partidos de hoy y mañana. Si el nombre no coincide, usa /hoy para ver el id."
)


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


def find_fixture(fixtures: List[dict], q_home: str, q_away: str) -> Optional[dict]:
    best, best_s = None, 0.0
    for f in fixtures:
        a, b = name_score(q_home, f["home"]), name_score(q_away, f["away"])
        if min(a, b) < 0.6:
            continue
        if a + b > best_s:
            best, best_s = f, a + b
    return best


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
    """-> (id_o_None, local, visitante, apuestas)"""
    head, _, rest = text.partition("|")
    head = head.strip()
    bets = parse_bets(rest)
    if head.isdigit():
        return int(head), "", "", bets
    parts = re.split(r"\s+(?:vs\.?|v|-|–|contra)\s+", head, maxsplit=1, flags=re.I)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError("Escribe: /partido Local vs Visitante")
    return None, parts[0].strip(), parts[1].strip(), bets


def pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(TZ)).strftime("%d/%m %H:%M")


# ---------- lógica ----------
def render(fx: dict, fc, nh: int, na: int, evals: List[Tuple[str, float, dict]]) -> str:
    L = [f"⚽ {fx['home']} vs {fx['away']}",
         f"{fx['league']} · {fx['country']} · {hhmm(fx['ts'])} (Lima)",
         f"Goles esperados: {fx['home']} {fc.lam_home:.2f} · {fx['away']} {fc.lam_away:.2f} · total {fc.exp_total:.2f}",
         f"Muestra: {nh} y {na} partidos previos"]
    if min(nh, na) < 12:
        L.append("⚠️ Muestra corta: tómalo con más cautela.")
    if fx["status"] not in NOT_STARTED:
        L.append("⚠️ El partido ya empezó o terminó: este bot es solo PRE-partido.")
    L += ["",
          f"+0.5 goles: {pct(fc.p_over[0.5])}  (cuota justa {1 / fc.p_over[0.5]:.2f})",
          f"+1.5 goles: {pct(fc.p_over[1.5])}  (cuota justa {1 / fc.p_over[1.5]:.2f})",
          f"Riesgo 0-0: {pct(fc.p_zero)}"]
    if fc.floor >= 1:
        L.append(f"Piso: {fc.floor} gol(es) con ≥{FLOOR_PROB * 100:.0f}% (equivale a +{fc.floor - 0.5:.1f})")
    else:
        L.append(f"Piso: no hay piso protegido (+0.5 está bajo {FLOOR_PROB * 100:.0f}%)")
    L.append(f"Techo: hasta {fc.ceiling} goles → Under {fc.ceiling_line:.1f} "
             f"({pct(fc.p_ceiling)}, cuota justa {1 / fc.p_ceiling:.2f})")
    L.append("Escalera Under: " + " | ".join(f"{l:.1f}: {fc.p_under[l] * 100:.0f}%" for l in LINES[2:9]))
    if evals:
        L += ["", f"💰 Valoración (apuesta de S/{STAKE:.0f}, margen mínimo {EDGE_MIN * 100:.0f}%, "
                  f"resto {UNCERTAINTY * 100:.0f} pts por error del modelo):"]
        for side, line, e in evals:
            name = f"{'+' if side == 'over' else '-'}{line:.1f}"
            tag = "✅ VALOR" if e["value"] else "❌ SIN VALOR"
            L.append(f"{name} @ {e['odds']:.2f}: {tag}")
            L.append(f"  modelo {pct(e['p'])} (conservador {pct(e['p_cons'])}) · la cuota exige acertar >{pct(e['implied'])}")
            L.append(f"  EV conservador {e['ev_cons'] * 100:+.1f}% · cuota mínima con valor: {e['min_odds']:.2f}")
            L.append(f"  Si acierta +S/{e['win']:.0f}; si falla -S/{e['loss']:.0f}")
    L += ["", "Esto es una estimación estadística, no una garantía."]
    return "\n".join(L)


def analyze(api: ApiFootball, store: Store, text: str) -> str:
    fid, q_home, q_away, bets = parse_query(text)
    if fid is not None:
        fx = api.fixture_by_id(fid, ttl=300)
        if not fx:
            return "No encontré ese id de partido."
    else:
        today = datetime.now(ZoneInfo(TZ)).date()
        fixtures: List[dict] = []
        for d in (today, today + timedelta(days=1)):
            fixtures += api.fixtures_by_date(d.isoformat(), TZ)
        fx = find_fixture(fixtures, q_home, q_away)
        if not fx:
            return (f"No encontré «{q_home} vs {q_away}» hoy ni mañana. "
                    "Prueba con /hoy para ver los nombres o usa el id.")
    hg = api.team_games(fx["home_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
    ag = api.team_games(fx["away_id"], fx["season"], fx["ts"], n=HISTORY_GAMES)
    if len(hg) < MIN_GAMES or len(ag) < MIN_GAMES:
        return (f"Pocos partidos previos ({fx['home']}: {len(hg)}, {fx['away']}: {len(ag)}). "
                f"Necesito al menos {MIN_GAMES} por equipo; no opino sin datos.")
    fc = forecast(hg, ag, floor_prob=FLOOR_PROB, tail=TAIL)
    evals, signals = [], []
    for side, line, odds in bets:
        e = evaluate_bet(market_prob(fc, side, line), odds, STAKE, EDGE_MIN, UNCERTAINTY)
        evals.append((side, line, e))
        signals.append({"side": side, "line": line, "odds": odds, "p": e["p"], "value": e["value"]})
    store.log(fx, fc, time.time(), signals)
    return render(fx, fc, len(hg), len(ag), evals)


def list_today(api: ApiFootball, flt: str) -> str:
    today = datetime.now(ZoneInfo(TZ)).date().isoformat()
    fx = [f for f in api.fixtures_by_date(today, TZ) if f["status"] in NOT_STARTED]
    if flt:
        q = norm(flt)
        fx = [f for f in fx if q in norm(f"{f['league']} {f['country']} {f['home']} {f['away']}")]
    fx.sort(key=lambda f: f["ts"] or 0)
    if not fx:
        return "No hay partidos pendientes hoy" + (f" con «{flt}»." if flt else ".")
    lines = [f"{f['id']} · {hhmm(f['ts'])[6:]} · {f['home']} vs {f['away']} ({f['league']})" for f in fx[:30]]
    extra = f"\n… y {len(fx) - 30} más. Usa un filtro: /hoy peru" if len(fx) > 30 else ""
    return "Partidos de hoy (hora de Lima):\n" + "\n".join(lines) + extra


def settle(api: ApiFootball, store: Store) -> str:
    pend = store.pending(time.time())
    if not pend:
        return "No hay partidos pendientes de liquidar."
    ok = 0
    for r in pend[:20]:
        fx = api.fixture_by_id(r["id"], ttl=0)
        if fx and fx["status"] in FINISHED and fx["gh"] is not None and fx["ga"] is not None:
            store.settle(r["id"], fx["gh"] + fx["ga"])
            ok += 1
    return f"Liquidados {ok} de {min(len(pend), 20)} partidos revisados ({len(pend)} pendientes en total)."


def stats(store: Store) -> str:
    s = compute_stats(list(store.load().values()), STAKE)
    if not s["n"]:
        return f"Aún no hay partidos liquidados ({s['pending']} pendientes). Analiza partidos y usa /liquidar cuando terminen."
    o, c, g = s["over05"], s["ceiling"], s["signals"]
    L = [f"📊 Acierto real ({s['n']} partidos liquidados, {s['pending']} pendientes)",
         f"+0.5: el bot dijo {pct(o['mean_p'])} de media, ocurrió {pct(o['hit'])} (Brier {o['brier']:.3f})",
         f"Techo: el bot dijo {pct(c['mean_p'])} de media, se respetó {pct(c['hit'])} (Brier {c['brier']:.3f})",
         f"Señales con VALOR: {g['n']} · acertadas {g['won']} · resultado S/{g['profit']:+.0f} · ROI {g['roi'] * 100:+.1f}%"]
    if s["n"] < 100:
        L.append("⚠️ Menos de 100 partidos: la muestra aún no permite sacar conclusiones.")
    return "\n".join(L)


def answer(text: str, api: ApiFootball, store: Store) -> str:
    t = text.strip()
    cmd, _, arg = t.partition(" ")
    cmd = cmd.lower().split("@")[0]
    if cmd in ("/start", "/ayuda", "/help"):
        return HELP
    if cmd == "/hoy":
        return list_today(api, arg.strip())
    if cmd == "/liquidar":
        return settle(api, store)
    if cmd == "/stats":
        return stats(store)
    if cmd == "/partido":
        return analyze(api, store, arg)
    if re.search(r"\s(vs\.?|contra)\s", t, re.I) or "|" in t:
        return analyze(api, store, t)
    return HELP


# ---------- Telegram ----------
def tg_send(chat_id, text: str) -> None:
    for i in range(0, len(text), 4000):
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": chat_id, "text": text[i:i + 4000]}, timeout=20)


def handle_update(u: dict, api: ApiFootball, store: Store) -> None:
    msg = u.get("message") or {}
    text, chat_id = msg.get("text"), (msg.get("chat") or {}).get("id")
    if not text or chat_id is None:
        return
    if not ALLOWED:  # primer arranque: solo ayuda a descubrir el chat id
        if text.startswith("/start"):
            tg_send(chat_id, f"Tu chat id es {chat_id}. Ponlo en la variable TELEGRAM_CHAT_ID y reinicia el bot.")
        return
    if str(chat_id) not in ALLOWED:
        return
    try:
        tg_send(chat_id, answer(text, api, store))
    except ApiError as e:
        tg_send(chat_id, f"Error de API-Football: {e}")
    except ValueError as e:
        tg_send(chat_id, str(e))
    except Exception:
        traceback.print_exc()
        tg_send(chat_id, "Error interno. Revisa los logs.")


def main() -> None:
    if not TOKEN:
        sys.exit("Falta TELEGRAM_BOT_TOKEN")
    api = ApiFootball(API_KEY, Cache(os.path.join(DATA_DIR, "cache")))
    store = Store(DATA_DIR)
    print(f"Bot iniciado · datos en {DATA_DIR} · chats permitidos: {len(ALLOWED)}", flush=True)
    offset = None
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                             params={"timeout": 25, "offset": offset}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u, api, store)
        except Exception as e:  # red caída, 409 por doble instancia, etc.
            print("loop:", repr(e), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
