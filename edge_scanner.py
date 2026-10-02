from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from edge_engine import EdgeInput, EdgeResult, evaluate


@dataclass(frozen=True)
class MarketSnapshot:
    """Snapshot de mercado ya devigado.

    probs:  {'over': {0.5: 0.965}, 'under': {0.5: 0.035}}
    prices: {'over': {0.5: 1.04},  'under': {0.5: 18.0}}
    books:  {'over': {0.5: 'pinnacle'}, ...} o una cadena/lista común.
    """
    probs: Dict[str, Dict[float, float]]
    prices: Optional[Dict[str, Dict[float, float]]] = None
    books: Optional[object] = None
    age_seconds: Optional[float] = None


def _book(snapshot: MarketSnapshot, side: str, line: float) -> str:
    b = snapshot.books
    if isinstance(b, dict):
        v = (b.get(side) or {}).get(line)
        return str(v or "")
    if isinstance(b, (list, tuple)):
        return ",".join(map(str, b))
    return str(b or "")


def scan_totals(*, p_over: Dict[float, float], p_under: Dict[float, float],
                snapshot: MarketSnapshot, quality: int, category: str,
                disagreement: Optional[float] = None,
                calibration_error: Optional[float] = None,
                sample_size: Optional[int] = None,
                lines: Iterable[float] = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5)) -> List[EdgeResult]:
    out: List[EdgeResult] = []
    prices = snapshot.prices or {}
    for side, model in (("over", p_over), ("under", p_under)):
        mp = snapshot.probs.get(side) or {}
        px = prices.get(side) or {}
        for line in lines:
            if line not in model or line not in mp:
                continue
            out.append(evaluate(EdgeInput(
                market="total_goals_ft",
                side=side,
                line=line,
                model_prob=model[line],
                market_fair_prob=mp[line],
                decimal_odds=px.get(line),
                quality=quality,
                category=category,
                disagreement=disagreement,
                calibration_error=calibration_error,
                age_seconds=snapshot.age_seconds,
                sample_size=sample_size,
                bookmaker=_book(snapshot, side, line),
            )))
    return out


def rank_opportunities(results: Iterable[EdgeResult], limit: int = 8) -> List[EdgeResult]:
    # Primero verdes; luego mayor edge conservador y EV conservador.
    def key(r: EdgeResult):
        green = 2 if r.entry_status.startswith("🟢") else (1 if r.gap_status.startswith("🟢") else 0)
        ev = -999 if r.ev_safe is None else r.ev_safe
        return (green, r.edge_safe, ev, r.conservative_prob)
    return sorted(results, key=key, reverse=True)[:max(0, int(limit))]


def select_nonredundant(results: Iterable[EdgeResult]) -> List[EdgeResult]:
    """Evita presentar 5 apuestas altamente correlacionadas como 5 ventajas independientes.

    Conserva como máximo un Over y un Under del mismo partido/familia.
    """
    ranked = rank_opportunities(results, 99)
    chosen, seen = [], set()
    for r in ranked:
        if not r.entry_status.startswith("🟢"):
            continue
        family = (r.market, r.side)
        if family in seen:
            continue
        seen.add(family)
        chosen.append(r)
    return chosen


def format_telegram(results: Iterable[EdgeResult]) -> str:
    rows = rank_opportunities(results, 12)
    if not rows:
        return "🕳️ EDGE FINDER\nSin mercado comparable para este partido."

    L = ["🕳️ EDGE FINDER · VACÍOS DE MERCADO", ""]
    for r in rows:
        sign = "+" if r.side == "over" else "U"
        label = f"{sign}{r.line:.1f}"
        odds = "s/cuota" if r.decimal_odds is None else f"@{r.decimal_odds:.2f}"
        ev = "N/D" if r.ev_safe is None else f"{r.ev_safe*100:+.1f}%"
        L.append(
            f"{r.entry_status.split()[0]} {label} {odds} · modelo {r.model_prob*100:.1f}% · "
            f"conserv. {r.conservative_prob*100:.1f}% · mercado {r.market_fair_prob*100:.1f}% · "
            f"EDGE {r.edge_safe*100:+.1f} pp · EV {ev}"
        )
        L.append(f"   {r.gap_status} · {r.reason}")
    picks = select_nonredundant(rows)
    L += ["", "🎯 ENTRADAS NO REDUNDANTES"]
    if picks:
        for p in picks:
            sign = "+" if p.side == "over" else "U"
            L.append(f"✅ {sign}{p.line:.1f} · {p.entry_status.replace('🟢 ', '')}")
    else:
        L.append("⛔ NO HAY VACÍO SUFICIENTEMENTE ROBUSTO")
    L.append("\nEl vacío es diferencia contra el precio justo del mercado; no es garantía de acierto.")
    return "\n".join(L)
