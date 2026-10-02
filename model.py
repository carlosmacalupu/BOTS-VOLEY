"""Núcleo determinista del bot de fútbol.

Misma entrada -> misma salida. Sin IA, sin azar, sin web.
Produce UNA distribución de goles totales (90 min) de la que salen TODOS los
mercados: +0.5, +1.5, escalera Under, piso y techo.

Dos modos:
  - forecast():      antes del partido (PRE).
  - live_forecast(): partido en juego, con marcador y minuto actuales.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

try:
    from profiles import CategoryProfile
except Exception:  # tests/standalone
    CategoryProfile = object

MAX_GOALS = 12          # goles máximos por equipo en la grilla
HOME_ADV = 1.12         # ventaja de local (multiplicador de goles esperados)
PRIOR_WEIGHT = 6.0      # peso del "promedio" al estimar fuerzas (encogimiento)
HALF_LIFE_DAYS = 90.0   # un partido pierde la mitad de su peso cada 90 días
RHO = -0.05             # corrección Dixon-Coles para marcadores bajos
MIN_GAMES = 8           # mínimo de partidos previos por equipo para opinar
LINES = [x + 0.5 for x in range(10)]  # 0.5 ... 9.5
FIRST_HALF_SHARE = 0.45  # fracción de los goles que cae en el primer tiempo


@dataclass(frozen=True)
class Game:
    """Un partido previo visto desde un equipo."""
    gf: int          # goles a favor (90 min)
    ga: int          # goles en contra (90 min)
    home: bool       # ¿jugó de local?
    age_days: float  # días antes del partido que se analiza


@dataclass
class Forecast:
    lam_home: float                 # goles esperados (PRE) o que FALTAN (en vivo)
    lam_away: float
    mu: float
    p_total: List[float]            # P(total final = k)
    p_under: Dict[float, float]     # línea -> P(total final < línea)
    p_over: Dict[float, float]      # línea -> P(total final > línea)
    floor: int                      # piso protegido (0 si no hay)
    ceiling_line: float             # línea Under natural (techo + 0.5)
    p_ceiling: float                # P(total final <= techo)
    cur_goals: int = 0              # goles ya marcados (solo en vivo)
    elapsed: Optional[int] = None   # minuto (solo en vivo)

    @property
    def p_zero(self) -> float:
        return self.p_total[0]

    @property
    def exp_total(self) -> float:
        return self.lam_home + self.lam_away

    @property
    def ceiling(self) -> int:
        return int(self.ceiling_line - 0.5)

    @property
    def live(self) -> bool:
        return self.elapsed is not None


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    if x == 0 and y == 0:
        return max(0.0, 1.0 - lh * la * rho)
    if x == 0 and y == 1:
        return max(0.0, 1.0 + lh * rho)
    if x == 1 and y == 0:
        return max(0.0, 1.0 + la * rho)
    if x == 1 and y == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


def score_grid(lh: float, la: float, rho: float = RHO) -> List[List[float]]:
    grid = [[_pois(i, lh) * _pois(j, la) * _tau(i, j, lh, la, rho)
             for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    s = sum(sum(r) for r in grid)
    return [[v / s for v in r] for r in grid]


def total_distribution(grid: List[List[float]]) -> List[float]:
    n = len(grid)
    out = [0.0] * (2 * n - 1)
    for i in range(n):
        for j in range(n):
            out[i + j] += grid[i][j]
    return out


def baseline_mu(home_games: Sequence[Game], away_games: Sequence[Game]) -> float:
    """Goles medios por equipo y partido, sacados de los dos historiales."""
    rows = list(home_games) + list(away_games)
    mu = sum((g.gf + g.ga) / 2.0 for g in rows) / len(rows)
    return min(1.9, max(0.9, mu))


def team_rates(games: Sequence[Game], mu: float, half_life: float = HALF_LIFE_DAYS,
               k: float = PRIOR_WEIGHT, hfa: float = HOME_ADV):
    """(ataque, defensa, peso efectivo) relativos a mu; 1.0 = promedio.

    - Pesa más los partidos recientes.
    - Neutraliza la localía de cada partido pasado.
    - Encoge hacia el promedio cuando hay pocos datos.
    """
    sw = sgf = sga = 0.0
    for g in games:
        w = 0.5 ** (max(0.0, g.age_days) / half_life)
        f = hfa if g.home else 1.0 / hfa
        sw += w
        sgf += w * (g.gf / f)
        sga += w * (g.ga * f)
    att = (sgf + k * mu) / (sw + k) / mu
    dfn = (sga + k * mu) / (sw + k) / mu
    return att, dfn, sw


def expected_goals(home_games: Sequence[Game], away_games: Sequence[Game], profile=None):
    mu = baseline_mu(home_games, away_games)
    half_life = getattr(profile, "half_life_days", HALF_LIFE_DAYS) if profile else HALF_LIFE_DAYS
    prior = getattr(profile, "prior_weight", PRIOR_WEIGHT) if profile else PRIOR_WEIGHT
    hfa = getattr(profile, "home_adv", HOME_ADV) if profile else HOME_ADV
    att_h, def_h, _ = team_rates(home_games, mu, half_life=half_life, k=prior, hfa=hfa)
    att_a, def_a, _ = team_rates(away_games, mu, half_life=half_life, k=prior, hfa=hfa)
    lh = mu * att_h * def_a * hfa
    la = mu * att_a * def_h / hfa
    clamp = lambda x: min(4.5, max(0.15, x))
    return clamp(lh), clamp(la), mu



def _profile_distribution(lh: float, la: float, profile=None, rho: float = RHO) -> List[float]:
    """Distribución por perfil. Categorías volátiles usan una mezcla suave de ritmos,
    para no fingir que juveniles/reservas tienen la misma estabilidad que mayores."""
    disp = float(getattr(profile, "dispersion", 0.0) or 0.0) if profile else 0.0
    prho = float(getattr(profile, "rho", rho)) if profile else rho
    if disp <= 0.001:
        return total_distribution(score_grid(lh, la, prho))
    scales = [max(0.55, 1.0 - disp), 1.0, 1.0 + disp]
    weights = [0.22, 0.56, 0.22]
    parts = [total_distribution(score_grid(lh * sc, la * sc, prho)) for sc in scales]
    n = max(map(len, parts))
    out = [0.0] * n
    for w, part in zip(weights, parts):
        for i, v in enumerate(part):
            out[i] += w * v
    z = sum(out) or 1.0
    return [v / z for v in out]

def _summarize(lam_h: float, lam_a: float, mu: float, dist: List[float],
               floor_prob: float, tail: float, cur_goals: int = 0,
               elapsed: Optional[int] = None) -> Forecast:
    cdf, acc = [], 0.0
    for p in dist:
        acc += p
        cdf.append(min(1.0, acc))
    p_under = {line: cdf[int(line - 0.5)] for line in LINES}
    p_over = {line: 1.0 - v for line, v in p_under.items()}
    floor = 0
    for cand in range(1, len(cdf)):
        if 1.0 - cdf[cand - 1] >= floor_prob:
            floor = cand
        else:
            break
    n = len(dist) - 1
    for n in range(len(dist)):
        if 1.0 - cdf[n] <= tail:
            break
    return Forecast(lam_h, lam_a, mu, dist, p_under, p_over, floor, n + 0.5, cdf[n],
                    cur_goals, elapsed)


def _check(home_games: Sequence[Game], away_games: Sequence[Game], profile=None) -> None:
    need = int(getattr(profile, "min_games", MIN_GAMES)) if profile else MIN_GAMES
    if len(home_games) < need or len(away_games) < need:
        raise ValueError(f"Pocos partidos previos ({len(home_games)} y {len(away_games)}); mínimo {need}")


def forecast(home_games: Sequence[Game], away_games: Sequence[Game],
             floor_prob: float = 0.93, tail: float = 0.03,
             rho: float = RHO, profile=None) -> Forecast:
    """Pronóstico PRE-partido. Lanza ValueError si faltan datos."""
    _check(home_games, away_games, profile)
    lh, la, mu = expected_goals(home_games, away_games, profile)
    dist = _profile_distribution(lh, la, profile, rho)
    return _summarize(lh, la, mu, dist, floor_prob, tail)


def remaining_fraction(elapsed: float) -> float:
    """Fracción de los goles del partido que aún falta por caer a este minuto.

    El segundo tiempo tiene algo más de goles que el primero. Mínimo 3% para el
    descuento.
    """
    m = min(max(float(elapsed), 0.0), 90.0)
    if m <= 45.0:
        done = FIRST_HALF_SHARE * m / 45.0
    else:
        done = FIRST_HALF_SHARE + (1.0 - FIRST_HALF_SHARE) * (m - 45.0) / 45.0
    return max(0.03, 1.0 - done)


def live_forecast(home_games: Sequence[Game], away_games: Sequence[Game],
                  cur_goals: int, elapsed: int,
                  floor_prob: float = 0.93, tail: float = 0.03, profile=None) -> Forecast:
    """Pronóstico EN VIVO: goles actuales + goles que faltan (Poisson).

    Solo usa marcador y minuto. No ve rojas, ritmo ni quién domina.
    """
    _check(home_games, away_games, profile)
    lh, la, mu = expected_goals(home_games, away_games, profile)
    r = remaining_fraction(elapsed)
    lam_h, lam_a = lh * r, la * r
    lam = max(1e-6, lam_h + lam_a)
    rest = [_pois(j, lam) for j in range(26)]
    s = sum(rest)
    dist = [0.0] * cur_goals + [v / s for v in rest]
    return _summarize(lam_h, lam_a, mu, dist, floor_prob, tail, cur_goals, int(elapsed))


def range_prob(fc: Forecast) -> float:
    """P(al menos 1 gol Y total <= techo): el 'rango' 1..techo. Se apuesta con +0.5 y Under techo a la vez."""
    return max(0.0, sum(fc.p_total[1:fc.ceiling + 1]))


def market_prob(fc: Forecast, side: str, line: float) -> float:
    table = fc.p_over if side == "over" else fc.p_under
    if line not in table:
        raise ValueError(f"Línea no soportada: {line}")
    return table[line]


def evaluate_bet(p: float, odds: float, stake: float = 500.0,
                 edge_min: float = 0.02, uncertainty: float = 0.02) -> dict:
    """Valora una apuesta. Resta `uncertainty` a la probabilidad del modelo
    (el modelo se equivoca) antes de decidir si hay valor."""
    p_cons = max(0.0, p - uncertainty)
    ev = p * odds - 1.0
    ev_cons = p_cons * odds - 1.0
    return {
        "p": p, "p_cons": p_cons, "odds": odds,
        "fair_odds": (1.0 / p) if p > 0 else float("inf"),
        "implied": 1.0 / odds,
        "min_odds": ((1.0 + edge_min) / p_cons) if p_cons > 0 else float("inf"),
        "ev": ev, "ev_cons": ev_cons,
        "win": stake * (odds - 1.0), "loss": stake,
        "value": ev_cons >= edge_min,
    }
