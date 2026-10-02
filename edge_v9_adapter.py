from __future__ import annotations

from typing import Optional

from edge_scanner import MarketSnapshot, format_telegram, scan_totals


def from_v9(*, sovereign, profile, quality: int, market: dict,
            calibration_error: Optional[float] = None,
            sample_size: Optional[int] = None,
            age_seconds: Optional[float] = None):
    """Adaptador directo a MASTER ÚNICO V9.

    `market` puede ser el dict devuelto por OddsFeed. Si incluye `prices`, calcula EV;
    si solo incluye probabilidades sin margen, calcula gap pero no EV.
    """
    snap = MarketSnapshot(
        probs={"over": dict(market.get("over") or {}), "under": dict(market.get("under") or {})},
        prices=market.get("prices"),
        books=market.get("book_by_line") or market.get("books"),
        age_seconds=age_seconds,
    )
    return scan_totals(
        p_over=sovereign.p_over,
        p_under=sovereign.p_under,
        snapshot=snap,
        quality=quality,
        category=getattr(profile, "code", "senior"),
        disagreement=getattr(sovereign, "disagreement", None),
        calibration_error=calibration_error,
        sample_size=sample_size,
    )


def telegram(**kwargs) -> str:
    return format_telegram(from_v9(**kwargs))
