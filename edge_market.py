from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional


class MarketError(ValueError):
    pass


def implied_prob(decimal_odds: float) -> float:
    o = float(decimal_odds)
    if o <= 1.0:
        raise MarketError(f"Cuota decimal inválida: {o}")
    return 1.0 / o


def overround(decimal_odds: Iterable[float]) -> float:
    ps = [implied_prob(x) for x in decimal_odds]
    if not ps:
        raise MarketError("Sin cuotas")
    return sum(ps) - 1.0


def devig_proportional(odds: Mapping[str, float]) -> Dict[str, float]:
    """Quita el margen de una casa reescalando las probabilidades implícitas a 100%."""
    raw = {k: implied_prob(v) for k, v in odds.items()}
    z = sum(raw.values())
    if z <= 0:
        raise MarketError("Mercado inválido")
    return {k: p / z for k, p in raw.items()}


def _power_sum(k: float, raw: Mapping[str, float]) -> float:
    return sum(p ** k for p in raw.values())


def devig_power(odds: Mapping[str, float], iters: int = 80) -> Dict[str, float]:
    """Método power. Encuentra k tal que sum((1/cuota)^k)=1."""
    raw = {name: implied_prob(o) for name, o in odds.items()}
    if len(raw) < 2:
        raise MarketError("El método power requiere al menos dos resultados")
    lo, hi = 0.01, 8.0
    # Para cuotas normales la función es decreciente en k.
    if _power_sum(lo, raw) < 1.0 or _power_sum(hi, raw) > 1.0:
        return devig_proportional(odds)
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        if _power_sum(mid, raw) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    out = {name: p ** k for name, p in raw.items()}
    z = sum(out.values()) or 1.0
    return {name: p / z for name, p in out.items()}


def devig(odds: Mapping[str, float], method: str = "power") -> Dict[str, float]:
    method = (method or "power").lower().strip()
    if method == "proportional":
        return devig_proportional(odds)
    if method == "power":
        return devig_power(odds)
    raise MarketError(f"Método no soportado: {method}")


@dataclass(frozen=True)
class TwoWayQuote:
    line: float
    over_odds: float
    under_odds: float
    bookmaker: str = ""
    age_seconds: Optional[float] = None

    def fair(self, method: str = "power") -> Dict[str, float]:
        return devig({"over": self.over_odds, "under": self.under_odds}, method=method)

    @property
    def margin(self) -> float:
        return overround([self.over_odds, self.under_odds])
