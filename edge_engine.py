from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


CATEGORY = {
    "senior":   {"base_penalty": 0.008, "min_edge": 0.025, "min_quality": 72, "safe_prob": 0.90},
    "women":    {"base_penalty": 0.012, "min_edge": 0.035, "min_quality": 74, "safe_prob": 0.91},
    "youth":    {"base_penalty": 0.020, "min_edge": 0.045, "min_quality": 76, "safe_prob": 0.92},
    "reserve":  {"base_penalty": 0.025, "min_edge": 0.050, "min_quality": 77, "safe_prob": 0.93},
    "friendly": {"base_penalty": 0.025, "min_edge": 0.050, "min_quality": 77, "safe_prob": 0.93},
}


def _clip(p: float) -> float:
    return max(0.0, min(1.0, float(p)))


def _cfg(category: str) -> dict:
    return CATEGORY.get((category or "senior").lower(), CATEGORY["senior"])


def uncertainty_penalty(*, quality: int, category: str = "senior",
                        disagreement: Optional[float] = None,
                        calibration_error: Optional[float] = None,
                        age_seconds: Optional[float] = None,
                        sample_size: Optional[int] = None) -> float:
    """Haircut conservador sobre p_model. Nunca 'sube' una probabilidad."""
    c = _cfg(category)
    pen = c["base_penalty"]

    # Calidad: debajo de 90 se descuenta gradualmente; por encima no se premia.
    q = max(0, min(100, int(quality)))
    pen += max(0, 90 - q) * 0.00125

    # Divergencia entre motores: 10 pp de divergencia resta 2.5 pp.
    if disagreement is not None:
        pen += min(0.045, max(0.0, float(disagreement)) * 0.25)

    # Error de calibración observado en backtest (ECE/Brier-derived gap, si existe).
    if calibration_error is not None:
        pen += min(0.035, max(0.0, float(calibration_error)) * 0.50)

    # Mercado viejo: no convertir una cuota antigua en 'vacío'.
    if age_seconds is not None:
        age = max(0.0, float(age_seconds))
        if age > 3600:
            pen += 0.025
        elif age > 1800:
            pen += 0.015
        elif age > 900:
            pen += 0.0075

    # Poca muestra real calibrada: prudencia extra.
    if sample_size is not None:
        n = max(0, int(sample_size))
        if n < 50:
            pen += 0.020
        elif n < 100:
            pen += 0.012
        elif n < 250:
            pen += 0.006

    return min(0.12, pen)


@dataclass(frozen=True)
class EdgeInput:
    market: str
    side: str
    line: float
    model_prob: float
    market_fair_prob: float
    decimal_odds: Optional[float]
    quality: int
    category: str = "senior"
    disagreement: Optional[float] = None
    calibration_error: Optional[float] = None
    age_seconds: Optional[float] = None
    sample_size: Optional[int] = None
    bookmaker: str = ""


@dataclass(frozen=True)
class EdgeResult:
    market: str
    side: str
    line: float
    bookmaker: str
    model_prob: float
    conservative_prob: float
    market_fair_prob: float
    edge_raw: float
    edge_safe: float
    decimal_odds: Optional[float]
    ev_raw: Optional[float]
    ev_safe: Optional[float]
    break_even_prob: Optional[float]
    uncertainty: float
    quality: int
    category: str
    gap_status: str
    entry_status: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate(x: EdgeInput) -> EdgeResult:
    c = _cfg(x.category)
    p = _clip(x.model_prob)
    pm = _clip(x.market_fair_prob)
    u = uncertainty_penalty(
        quality=x.quality,
        category=x.category,
        disagreement=x.disagreement,
        calibration_error=x.calibration_error,
        age_seconds=x.age_seconds,
        sample_size=x.sample_size,
    )
    pc = max(0.0, p - u)
    edge_raw = p - pm
    edge_safe = pc - pm

    odds = None if x.decimal_odds is None else float(x.decimal_odds)
    if odds is not None and odds > 1.0:
        be = 1.0 / odds
        ev_raw = p * odds - 1.0
        ev_safe = pc * odds - 1.0
    else:
        odds, be, ev_raw, ev_safe = None, None, None, None

    min_edge = c["min_edge"]
    q_ok = x.quality >= c["min_quality"]

    if edge_safe >= min_edge:
        gap = "🟢 VACÍO ROBUSTO"
    elif edge_raw >= min_edge:
        gap = "🟡 VACÍO FRÁGIL"
    elif edge_raw > 0:
        gap = "🟡 DIFERENCIA PEQUEÑA"
    else:
        gap = "🔴 SIN VACÍO"

    # Dos conceptos separados:
    # - 'vacío' = ventaja contra el precio de mercado;
    # - 'entrada segura' = además, alta probabilidad absoluta.
    if not q_ok:
        entry = "🔴 NO ENTRAR"
        reason = "calidad del estudio insuficiente"
    elif edge_safe < min_edge:
        entry = "🔴 NO ENTRAR"
        reason = "la ventaja no sobrevive la incertidumbre"
    elif ev_safe is not None and ev_safe < 0.015:
        entry = "🔴 NO ENTRAR"
        reason = "EV conservador insuficiente para la cuota"
    elif pc >= c["safe_prob"]:
        entry = "🟢 VERDE SEGURO"
        reason = "alta probabilidad + vacío robusto + EV conservador positivo"
    elif pc >= 0.55 and (ev_safe is None or ev_safe >= 0.03):
        entry = "🟢 VERDE VALOR"
        reason = "vacío robusto de valor; no es una apuesta de alta probabilidad"
    else:
        entry = "🟡 VALOR SIN ENTRADA"
        reason = "hay ventaja, pero la probabilidad absoluta es demasiado baja"

    return EdgeResult(
        market=x.market,
        side=x.side,
        line=float(x.line),
        bookmaker=x.bookmaker,
        model_prob=p,
        conservative_prob=pc,
        market_fair_prob=pm,
        edge_raw=edge_raw,
        edge_safe=edge_safe,
        decimal_odds=odds,
        ev_raw=ev_raw,
        ev_safe=ev_safe,
        break_even_prob=be,
        uncertainty=u,
        quality=int(x.quality),
        category=x.category,
        gap_status=gap,
        entry_status=entry,
        reason=reason,
    )


def kelly_fraction(prob: float, odds: float) -> float:
    """Kelly completo. El algoritmo NO recomienda usarlo sin límite."""
    p = _clip(prob)
    o = float(odds)
    if o <= 1:
        return 0.0
    b = o - 1.0
    q = 1.0 - p
    return max(0.0, (b * p - q) / b)


def capped_kelly(prob: float, odds: float, cap: float = 0.01, fraction: float = 0.25) -> float:
    """Kelly fraccional con techo duro. Por defecto: 1/4 Kelly y máximo 1% de banca."""
    return min(max(0.0, cap), kelly_fraction(prob, odds) * max(0.0, fraction))
