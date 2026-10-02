from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from typing import Dict, Iterable, List, Optional


class EdgeTracker:
    """Registro append-only para demostrar si los vacíos son reales fuera de muestra."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def add(self, *, fixture_id: int, ts: float, home: str, away: str,
            result, total_goals: Optional[int] = None,
            closing_odds: Optional[float] = None) -> None:
        r = asdict(result) if is_dataclass(result) else dict(result)
        rec = {
            "fixture_id": int(fixture_id), "ts": float(ts), "home": home, "away": away,
            "prediction": r, "total_goals": total_goals, "closing_odds": closing_odds,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def load(self) -> List[dict]:
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        return out


def _hit(rec: dict) -> Optional[int]:
    tg = rec.get("total_goals")
    if tg is None:
        return None
    p = rec["prediction"]
    side, line = p.get("side"), float(p.get("line"))
    if side == "over":
        return int(tg > line)
    if side == "under":
        return int(tg < line)
    return None


def stats(records: Iterable[dict]) -> Dict[str, object]:
    settled = [r for r in records if _hit(r) is not None]
    out: Dict[str, object] = {"n": len(settled)}
    if not settled:
        return out

    won = 0
    pnl = 0.0
    brier = 0.0
    clv = []
    bins = defaultdict(lambda: [0, 0])

    for r in settled:
        y = _hit(r)
        p = r["prediction"]
        pc = float(p.get("conservative_prob", p.get("model_prob", 0.5)))
        won += y
        brier += (pc - y) ** 2
        odds = p.get("decimal_odds")
        if odds:
            pnl += (float(odds) - 1.0) if y else -1.0
        close = r.get("closing_odds")
        if odds and close:
            # CLV log-simple: positivo si obtuvimos mejor cuota que el cierre.
            clv.append(float(odds) / float(close) - 1.0)
        bucket = min(9, int(pc * 10))
        bins[bucket][0] += 1
        bins[bucket][1] += y

    out.update({
        "hit_rate": won / len(settled),
        "brier": brier / len(settled),
        "roi_flat_1u": pnl / len(settled),
        "mean_clv": (sum(clv) / len(clv)) if clv else None,
        "calibration": {
            f"{b*10}-{b*10+9}%": {"n": n, "hit": w / n}
            for b, (n, w) in sorted(bins.items()) if n
        },
    })
    return out
