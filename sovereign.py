from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from profiles import CategoryProfile


def _norm(xs: Iterable[float]) -> List[float]:
    out = [max(0.0, float(x)) for x in xs]
    s = sum(out)
    if s <= 0:
        return [1.0]
    return [x / s for x in out]


def _pad(xs: List[float], n: int) -> List[float]:
    if len(xs) >= n:
        return xs[:n]
    return xs + [0.0] * (n - len(xs))


def ai_distribution(op: Optional[dict], model_dist: List[float], max_goal: int = 20) -> Optional[List[float]]:
    """Convierte la escalera Over de la IA a masa discreta coherente.

    P(G=k)=P(G>k-0.5)-P(G>k+0.5). La cola 10+ se reparte con la forma del modelo.
    Si faltan demasiadas líneas, no inventa distribución.
    """
    if not op:
        return None
    over = {float(k): float(v) for k, v in (op.get("over") or {}).items()}
    needed = [x + 0.5 for x in range(10)]
    have = [l for l in needed if l in over]
    if len(have) < 6 or 0.5 not in over:
        return None
    # Completa huecos de forma monótona por interpolación lineal entre líneas conocidas.
    vals: Dict[float, float] = {}
    keys = sorted(over)
    for l in needed:
        if l in over:
            vals[l] = max(0.0, min(1.0, over[l]))
            continue
        lo = max((k for k in keys if k < l), default=None)
        hi = min((k for k in keys if k > l), default=None)
        if lo is None or hi is None:
            continue
        t = (l - lo) / (hi - lo)
        vals[l] = over[lo] + t * (over[hi] - over[lo])
    if len(vals) < 8:
        return None
    # Monotonicidad estricta: el Over nunca sube al subir la línea.
    prev = 1.0
    for l in needed:
        if l in vals:
            vals[l] = prev = min(prev, vals[l])
    masses = [0.0] * max(max_goal + 1, len(model_dist))
    masses[0] = 1.0 - vals[0.5]
    for k in range(1, 10):
        a = vals.get(k - 0.5)
        b = vals.get(k + 0.5)
        if a is None or b is None:
            return None
        masses[k] = max(0.0, a - b)
    tail = max(0.0, vals.get(9.5, 0.0))
    base_tail = _pad(model_dist, len(masses))[10:]
    s = sum(base_tail)
    if s <= 0:
        masses[10] += tail
    else:
        for i, p in enumerate(base_tail, start=10):
            masses[i] += tail * p / s
    return _norm(masses)


def fuse(model_dist: List[float], ai_dist: Optional[List[float]], profile: CategoryProfile,
         ai_quality: Optional[int] = None) -> Tuple[List[float], float]:
    """Fusión soberana. La IA ajusta una distribución, nunca mercados aislados."""
    if ai_dist is None:
        return _norm(model_dist), 0.0
    n = max(len(model_dist), len(ai_dist))
    m = _pad(_norm(model_dist), n)
    a = _pad(_norm(ai_dist), n)
    w = profile.ai_weight
    if ai_quality is not None:
        # 50/100 -> mitad del peso; 80+ -> peso completo.
        w *= max(0.35, min(1.0, ai_quality / 80.0))
    final = [(1.0 - w) * x + w * y for x, y in zip(m, a)]
    return _norm(final), w


def ladder(dist: List[float], max_line: float = 9.5) -> Tuple[Dict[float, float], Dict[float, float]]:
    dist = _norm(dist)
    cdf, s = [], 0.0
    for p in dist:
        s += p
        cdf.append(min(1.0, s))
    over, under = {}, {}
    l = 0.5
    while l <= max_line + 1e-9:
        n = int(l - 0.5)
        u = cdf[n] if n < len(cdf) else 1.0
        under[round(l, 1)] = u
        over[round(l, 1)] = 1.0 - u
        l += 1.0
    return over, under


def protected_floor(dist: List[float], risk: float = 0.08) -> int:
    """Piso protegido: mayor N tal que P(G < N) <= risk."""
    acc = 0.0
    floor = 0
    for n, p in enumerate(_norm(dist)):
        if n == 0:
            acc = p
            if acc <= risk:
                floor = 1
            continue
        if acc + p <= risk:
            acc += p
            floor = n + 1
        else:
            break
    return floor


def protected_ceiling(dist: List[float], tail: float = 0.03) -> Tuple[int, float]:
    """Primer techo N con P(G>N)<=tail."""
    d = _norm(dist)
    c = 0.0
    for n, p in enumerate(d):
        c += p
        if 1.0 - c <= tail + 1e-12:
            return n, c
    return len(d) - 1, 1.0


def divergence(model_dist: List[float], ai_dist: Optional[List[float]]) -> Optional[float]:
    if ai_dist is None:
        return None
    mo, _ = ladder(model_dist)
    ao, _ = ladder(ai_dist)
    keys = [0.5, 1.5, 2.5, 3.5, 4.5]
    diffs = [abs(mo[k] - ao[k]) for k in keys if k in mo and k in ao]
    return max(diffs) if diffs else None


def color(prob: float, quality: int, profile: CategoryProfile, kind: str,
          disagree: Optional[float] = None) -> str:
    green = profile.over_green if kind == "over" else profile.under_green
    if disagree is not None and disagree > 0.12 and prob >= green:
        return "🟡"
    if quality >= profile.green_quality and prob >= green:
        return "🟢"
    if prob >= profile.yellow:
        return "🟡"
    return "🔴"


def approval(prob: float, quality: int, profile: CategoryProfile, kind: str,
             disagree: Optional[float] = None) -> str:
    c = color(prob, quality, profile, kind, disagree)
    return "APROBADO" if c == "🟢" else ("SIN CONFIRMAR" if c == "🟡" else "NO APROBADO")


@dataclass
class Sovereign:
    dist: List[float]
    p_over: Dict[float, float]
    p_under: Dict[float, float]
    floor: int
    ceiling: int
    ceiling_line: float
    p_ceiling: float
    p_zero: float
    ai_weight: float
    disagreement: Optional[float]


def build(model_dist: List[float], op: Optional[dict], profile: CategoryProfile,
          floor_risk: float = 0.08, tail: float = 0.03) -> Sovereign:
    aid = ai_distribution(op, model_dist)
    ai_quality = None if not op else op.get("quality")
    final, w = fuse(model_dist, aid, profile, ai_quality)
    ov, un = ladder(final)
    fl = protected_floor(final, floor_risk)
    ce, pc = protected_ceiling(final, tail)
    return Sovereign(final, ov, un, fl, ce, ce + 0.5, pc, final[0] if final else 1.0,
                     w, divergence(model_dist, aid))
