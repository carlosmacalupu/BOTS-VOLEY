"""Backtest del modelo con football-data.co.uk (ligas grandes, resultados + cuotas).

Usa EXACTAMENTE la misma función `forecast` que el bot. Cada partido solo ve
partidos anteriores a su fecha (sin mirar el futuro).

Uso:
    python backtest.py
    python backtest.py --leagues E0 SP1 D1 I1 F1 --seasons 2324 2425 2526

IMPORTANTE: football-data.co.uk NO trae cuotas de +0.5 ni de Under 5.5.
Por eso el informe hace dos cosas:
  1) Calibración de +0.5 y del techo + "cuota de equilibrio" (cuánto tendría que
     pagar Betano para que la estrategia no pierda dinero).
  2) Prueba contra el mercado REAL en Over/Under 2.5, donde sí hay cuotas.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from model import MIN_GAMES, Game, forecast

URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
NEW_URL = "https://www.football-data.co.uk/new/{code}.csv"
EURO_LEAGUES = ("E0", "SP1", "D1", "I1", "F1")
EURO_SEASONS = ("2223", "2324", "2425", "2526")
OTHER_LEAGUES = ("ARG", "BRA", "MEX", "USA", "JPN", "CHN", "NOR", "SWE", "DNK", "FIN", "IRL", "AUT", "POL", "ROU", "RUS", "SWZ")
OVER_COLS = ["AvgC>2.5", "B365C>2.5", "Avg>2.5", "B365>2.5"]
UNDER_COLS = ["AvgC<2.5", "B365C<2.5", "Avg<2.5", "B365<2.5"]


def parse_date(s: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(s)


def _first_float(row: dict, cols: List[str]) -> Optional[float]:
    for c in cols:
        try:
            v = float(row.get(c, ""))
            if v > 1.0:
                return v
        except (TypeError, ValueError):
            continue
    return None


def download_if_stale(url: str, path: str, max_age: float = 86400, getter=None) -> None:
    """Descarga si el archivo no existe o tiene más de un día (la temporada en curso cambia).
    Si falla pero ya hay una copia, usa la copia."""
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return
    try:
        if getter is None:
            import requests
            getter = requests.get
        r = getter(url, timeout=30)
        r.raise_for_status()
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, path)
    except Exception:
        if not os.path.exists(path):
            raise


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def load_csv(season: str, league: str, folder: str) -> List[dict]:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{season}_{league}.csv")
    download_if_stale(URL.format(season=season, league=league), path)
    rows = []
    for r in csv.DictReader(io.StringIO(_read_text(path))):
        try:
            rows.append({"date": parse_date(r["Date"]), "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "hg": int(r["FTHG"]), "ag": int(r["FTAG"]),
                         "o25": _first_float(r, OVER_COLS), "u25": _first_float(r, UNDER_COLS)})
        except (KeyError, ValueError):
            continue
    return rows


def load_new_csv(code: str, folder: str, since: datetime = datetime(2021, 8, 1)) -> List[dict]:
    """Ligas 'extra' de football-data (Brasil, Argentina, México, EE. UU., ...): un archivo con todas las temporadas."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"new_{code}.csv")
    download_if_stale(NEW_URL.format(code=code), path)
    rows = []
    for r in csv.DictReader(io.StringIO(_read_text(path))):
        try:
            d = parse_date(r["Date"])
            home, away = r.get("Home") or r.get("HomeTeam"), r.get("Away") or r.get("AwayTeam")
            hg, ag = r.get("HG") or r.get("FTHG"), r.get("AG") or r.get("FTAG")
            if d < since or not home or not away or hg in (None, "") or ag in (None, ""):
                continue
            rows.append({"date": d, "home": home, "away": away, "hg": int(hg), "ag": int(ag), "o25": None, "u25": None})
        except (KeyError, ValueError):
            continue
    return rows


def run_backtest(matches: List[dict], last_n: int = 20, min_games: int = MIN_GAMES,
                 tail: float = 0.03, floor_prob: float = 0.93) -> List[dict]:
    """matches: partidos de UNA liga en varias temporadas. Devuelve predicciones + resultado."""
    hist: Dict[str, list] = defaultdict(list)  # equipo -> [(fecha, gf, ga, local)]
    out = []
    for m in sorted(matches, key=lambda x: x["date"]):
        hh, ha = hist[m["home"]][-last_n:], hist[m["away"]][-last_n:]
        if len(hh) >= min_games and len(ha) >= min_games:
            mk = lambda rows: [Game(gf, ga, home, (m["date"] - d).days) for d, gf, ga, home in rows]
            fc = forecast(mk(hh), mk(ha), floor_prob=floor_prob, tail=tail)
            out.append({"p05": fc.p_over[0.5], "p15": fc.p_over[1.5], "p25": fc.p_over[2.5],
                        "ceil": fc.ceiling_line, "p_ceil": fc.p_ceiling,
                        "total": m["hg"] + m["ag"], "o25": m.get("o25"), "u25": m.get("u25")})
        hist[m["home"]].append((m["date"], m["hg"], m["ag"], True))
        hist[m["away"]].append((m["date"], m["ag"], m["hg"], False))
    return out


# ---------- métricas ----------
def brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def logloss(ps, ys) -> float:
    e = 1e-9
    return -sum(y * math.log(max(p, e)) + (1 - y) * math.log(max(1 - p, e)) for p, y in zip(ps, ys)) / len(ps)


def calibration(ps, ys, edges):
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
        if sel:
            rows.append((lo, hi, len(sel), sum(p for p, _ in sel) / len(sel), sum(y for _, y in sel) / len(sel)))
    return rows


def flat_roi(profits: List[float]):
    n = len(profits)
    if n == 0:
        return 0, 0.0, 0.0
    mean = sum(profits) / n
    var = sum((x - mean) ** 2 for x in profits) / max(1, n - 1)
    return n, mean, math.sqrt(var / n)


def report(rows: List[dict]) -> str:
    if not rows:
        return "Sin datos suficientes."
    L = [f"Partidos evaluados: {len(rows)}", ""]
    ys = [1 if r["total"] >= 1 else 0 for r in rows]
    ps = [r["p05"] for r in rows]
    base = sum(ys) / len(ys)
    L += ["== +0.5 goles ==",
          f"Tasa real de +0.5: {base * 100:.1f}% · probabilidad media del modelo: {sum(ps) / len(ps) * 100:.1f}%",
          f"Brier modelo {brier(ps, ys):.4f} vs. constante {brier([base] * len(ys), ys):.4f} (menor = mejor)",
          "Calibración (modelo dice → ocurrió):"]
    for lo, hi, n, mp, hit in calibration(ps, ys, [0, .80, .85, .88, .90, .92, .94, .96, 1.01]):
        L.append(f"  {lo * 100:>3.0f}–{min(hi, 1) * 100:>3.0f}%  n={n:<5} dijo {mp * 100:5.1f}%  ocurrió {hit * 100:5.1f}%")
    L += ["", "Estrategia 'apostar +0.5 si p ≥ umbral' (cuota de equilibrio = 1/acierto):"]
    for thr in (0.90, 0.93, 0.95):
        sel = [y for p, y in zip(ps, ys) if p >= thr]
        if sel:
            hit = sum(sel) / len(sel)
            L.append(f"  p≥{thr * 100:.0f}%: n={len(sel):<5} acierto {hit * 100:5.1f}% → Betano debería pagar más de {1 / hit:.3f} "
                     f"| ROI a 1.05: {(hit * 1.05 - 1) * 100:+.1f}%, 1.08: {(hit * 1.08 - 1) * 100:+.1f}%, 1.10: {(hit * 1.10 - 1) * 100:+.1f}%")
    L += ["", "== Techo (Under natural, cola ≤ 3%) =="]
    by = defaultdict(list)
    for r in rows:
        by[r["ceil"]].append(r)
    for line in sorted(by):
        g = by[line]
        cov = sum(1 for r in g if r["total"] < line) / len(g)
        L.append(f"  Under {line:.1f}: n={len(g):<5} el modelo dijo {sum(r['p_ceil'] for r in g) / len(g) * 100:5.1f}%  se respetó {cov * 100:5.1f}%")
    mk = [r for r in rows if r["o25"] and r["u25"]]
    L += ["", "== Prueba contra el MERCADO REAL: Over/Under 2.5 =="]
    if len(mk) < 50:
        L.append("  Sin cuotas suficientes en los datos.")
        return "\n".join(L)
    y = [1 if r["total"] > 2.5 else 0 for r in mk]
    pm = [r["p25"] for r in mk]
    pk = [(1 / r["o25"]) / (1 / r["o25"] + 1 / r["u25"]) for r in mk]
    L.append(f"  Brier modelo {brier(pm, y):.4f} vs. mercado {brier(pk, y):.4f}  (si el mercado es menor, te gana)")
    L.append(f"  LogLoss modelo {logloss(pm, y):.4f} vs. mercado {logloss(pk, y):.4f}")
    for edge in (0.03, 0.05):
        prof = []
        for r, yy in zip(mk, y):
            ev_o, ev_u = r["p25"] * r["o25"] - 1, (1 - r["p25"]) * r["u25"] - 1
            if ev_o >= edge and ev_o >= ev_u:
                prof.append(r["o25"] - 1 if yy else -1.0)
            elif ev_u >= edge:
                prof.append(r["u25"] - 1 if not yy else -1.0)
        n, mean, se = flat_roi(prof)
        L.append(f"  Apostando cuando el modelo ve ≥{edge * 100:.0f}% de ventaja: n={n}, ROI {mean * 100:+.1f}% ± {se * 100:.1f}% (error estándar)")
    L.append("  Si no le ganas al mercado aquí, es poco probable que encuentres ventaja en mercados de cuota baja.")
    return "\n".join(L)


def run_groups(folder: str, floor_prob: float = 0.93, tail: float = 0.03):
    """-> ([(etiqueta, filas)], archivos_que_fallaron). Cada liga se prueba por separado."""
    out, failed = [], 0
    rows: List[dict] = []
    for lg in EURO_LEAGUES:
        matches: List[dict] = []
        for season in EURO_SEASONS:
            try:
                matches += load_csv(season, lg, folder)
            except Exception:
                failed += 1
        rows += run_backtest(matches, floor_prob=floor_prob, tail=tail)
    out.append(("ligas grandes de Europa, 2022-2026", rows))
    rows = []
    for code in OTHER_LEAGUES:
        try:
            matches = load_new_csv(code, folder)
        except Exception:
            failed += 1
            continue
        rows += run_backtest(matches, floor_prob=floor_prob, tail=tail)
    out.append(("otras ligas: Brasil, Argentina, México, EE. UU., Japón, Escandinavia y más, 2021-2026", rows))
    return out, failed


def summary(rows: List[dict], safe: float = 0.95, maybe: float = 0.90,
            label: str = "ligas grandes de Europa, 2022-2026", footer: bool = True) -> str:
    """Resumen corto para Telegram: ¿cuánto ocurre de verdad cuando el bot dice APOSTAR / DUDOSO / NO?"""
    n = len(rows)
    L = [f"🧪 Prueba con {n} partidos pasados ({label}), "
         "cada uno visto solo con partidos anteriores a su fecha.", ""]
    markets = (
        ("+0.5 goles", "p05", lambda r: r["total"] >= 1),
        ("+1.5 goles", "p15", lambda r: r["total"] >= 2),
        ("Máximo de goles (techo)", "p_ceil", lambda r: r["total"] < r["ceil"]),
    )
    tiers = ((f"✅ APOSTAR (≥{safe * 100:.0f}%)", safe, 1.01),
             (f"🟡 DUDOSO ({maybe * 100:.0f}-{safe * 100:.0f}%)", maybe, safe),
             (f"❌ NO APOSTAR (<{maybe * 100:.0f}%)", 0.0, maybe))
    for name, key, hit_fn in markets:
        L.append(name)
        for label, lo, hi in tiers:
            sel = [r for r in rows if lo <= r[key] < hi]
            if not sel:
                L.append(f"  {label}: sin casos")
                continue
            hit = sum(1 for r in sel if hit_fn(r)) / len(sel)
            note = ""
            if lo == safe:
                if len(sel) < 30:
                    note = "  (pocos casos)"
                elif hit >= safe - 0.01:
                    note = "  ✅ se sostiene"
                else:
                    note = f"  ⚠️ NO se sostiene (dijo ≥{safe * 100:.0f}% y acertó {hit * 100:.0f}%)"
            eq = f" · cuota de equilibrio {math.ceil(1 / hit * 100 - 1e-9) / 100:.2f}" if hit > 0 and lo >= maybe else ""
            L.append(f"  {label}: {len(sel)} partidos → ocurrió {hit * 100:.1f}%{eq}{note}")
        L.append("")
    if footer:
        L += ["«Cuota de equilibrio» = lo mínimo que debe pagar Betano para no perder con ese acierto (sin margen de seguridad).",
              "Ojo: esto mide acierto, no ganancia. No incluye selecciones nacionales ni la IA."]
    return "\n".join(L).rstrip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["E0", "SP1", "D1", "I1", "F1"])
    ap.add_argument("--seasons", nargs="+", default=["2223", "2324", "2425", "2526"])
    ap.add_argument("--folder", default="data/fd")
    ap.add_argument("--last", type=int, default=20)
    a = ap.parse_args()
    rows: List[dict] = []
    for lg in a.leagues:
        matches: List[dict] = []
        for s in a.seasons:
            try:
                matches += load_csv(s, lg, a.folder)
            except Exception as e:
                print(f"  (omito {s} {lg}: {e})")
        rows += run_backtest(matches, last_n=a.last)
    print(report(rows))


if __name__ == "__main__":
    main()
