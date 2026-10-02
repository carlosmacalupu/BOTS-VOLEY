"""Registro de predicciones y su resultado real.

Es el historial que dirá, con partidos reales, si el bot acierta o no.
La primera predicción de cada partido es la que cuenta (no se reescribe).
"""
from __future__ import annotations

import json
import os
import threading
from functools import wraps
from typing import Dict, List, Optional

FINISHED_MIN_AGE = 3 * 3600  # segundos tras el kickoff antes de intentar liquidar


def _locked(fn):
    """El bot y el hilo de liquidación leen y escriben el mismo archivo: una operación a la vez."""
    @wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class Store:
    def __init__(self, folder: str):
        self._lock = threading.RLock()
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, "predictions.json")
        self.combo_path = os.path.join(folder, "combinada.json")
        self.anchor_path = os.path.join(folder, "pre_anchors.json")

    def load(self) -> Dict[str, dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, data: Dict[str, dict]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    @_locked
    def log(self, fx: dict, fc, now_ts: float, signals: List[dict], ai: Optional[dict] = None, mk: Optional[dict] = None) -> None:
        data = self.load()
        key = str(fx["id"])
        rec = data.get(key)
        if rec is None:
            rec = {
                "id": fx["id"], "ts_pred": now_ts, "kickoff_ts": fx["ts"],
                "home": fx["home"], "away": fx["away"], "league": fx["league"],
                "p_over05": fc.p_over[0.5], "p_over15": fc.p_over[1.5],
                "ceiling_line": fc.ceiling_line, "p_ceiling": fc.p_ceiling,
                "lam_home": fc.lam_home, "lam_away": fc.lam_away,
                "signals": [], "total_goals": None,
            }
        if ai and rec.get("p_ai05") is None:          # opinión de la IA (la primera es la que cuenta)
            rec["p_ai05"] = ai["over"].get(0.5)
            rec["p_ai15"] = ai["over"].get(1.5)
            rec["p_ai_ceiling"] = ai["under"].get(fc.ceiling_line)
        if mk and rec.get("p_mkt05") is None:         # lectura del mercado (la primera es la que cuenta)
            rec["p_mkt05"] = mk["over"].get(0.5)
            rec["p_mkt15"] = mk["over"].get(1.5)
            rec["p_mkt_ceiling"] = mk["under"].get(fc.ceiling_line)
            rec["mkt_books"] = mk.get("books")
        have = {(s["side"], s["line"]) for s in rec["signals"]}
        for s in signals:
            if (s["side"], s["line"]) not in have:
                rec["signals"].append(s)
        data[key] = rec
        self.save(data)

    def pending(self, now_ts: float) -> List[dict]:
        return [r for r in self.load().values()
                if r.get("total_goals") is None and not r.get("void") and r["kickoff_ts"] + FINISHED_MIN_AGE < now_ts]

    @_locked
    def void(self, fid: int) -> None:
        """Partido aplazado/cancelado: se anula y no cuenta en las estadísticas."""
        data = self.load()
        if str(fid) in data:
            data[str(fid)]["void"] = True
            self.save(data)

    @_locked
    def settle(self, fid: int, total_goals: int) -> None:
        data = self.load()
        if str(fid) in data:
            data[str(fid)]["total_goals"] = int(total_goals)
            self.save(data)


    def _anchor_load(self) -> Dict[str, dict]:
        try:
            with open(self.anchor_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _anchor_save(self, data: Dict[str, dict]) -> None:
        tmp = self.anchor_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.anchor_path)

    @_locked
    def get_anchor(self, fid: int, max_age: float = 6 * 3600) -> Optional[dict]:
        import time
        rec = self._anchor_load().get(str(fid))
        if not rec:
            return None
        if time.time() - float(rec.get("saved_at", 0)) > max_age:
            return None
        return rec

    @_locked
    def save_anchor_once(self, fid: int, snapshot: dict) -> dict:
        """El primer PRE válido manda. Nunca se sobreescribe durante su TTL."""
        import time
        data = self._anchor_load()
        key = str(fid)
        old = data.get(key)
        if old and time.time() - float(old.get("saved_at", 0)) <= 6 * 3600:
            return old
        snap = dict(snapshot)
        snap["saved_at"] = time.time()
        data[key] = snap
        self._anchor_save(data)
        return snap

    @_locked
    def drop_anchor(self, fid: int) -> None:
        data = self._anchor_load()
        if str(fid) in data:
            data.pop(str(fid), None)
            self._anchor_save(data)


    # ---- combinada que el usuario va acumulando (se vacía sola al cambiar el día) ----
    def _combo_load(self, day: str) -> List[dict]:
        try:
            with open(self.combo_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return []
        return d.get("items", []) if d.get("day") == day else []

    def _combo_save(self, day: str, items: List[dict]) -> None:
        tmp = self.combo_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": day, "items": items}, f, ensure_ascii=False)
        os.replace(tmp, self.combo_path)

    @_locked
    def combo_items(self, day: str) -> List[dict]:
        return self._combo_load(day)

    @_locked
    def combo_add(self, entry: dict, day: str) -> bool:
        """False si esa selección (partido + mercado) ya estaba."""
        items = self._combo_load(day)
        if any(i["fid"] == entry["fid"] and i["kind"] == entry["kind"] for i in items):
            return False
        items.append(entry)
        self._combo_save(day, items)
        return True

    @_locked
    def combo_clear(self, day: str) -> None:
        self._combo_save(day, [])


def _brier(ps: List[float], ys: List[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def compute_stats(records: List[dict], stake: float = 500.0) -> dict:
    records = [r for r in records if not r.get("void")]
    done = [r for r in records if r.get("total_goals") is not None]
    out = {"n": len(done), "pending": len(records) - len(done)}
    if not done:
        return out
    ps = [r["p_over05"] for r in done]
    ys = [1 if r["total_goals"] >= 1 else 0 for r in done]
    out["over05"] = {"mean_p": sum(ps) / len(ps), "hit": sum(ys) / len(ys), "brier": _brier(ps, ys)}
    pc = [r["p_ceiling"] for r in done]
    yc = [1 if r["total_goals"] <= int(r["ceiling_line"] - 0.5) else 0 for r in done]
    out["ceiling"] = {"mean_p": sum(pc) / len(pc), "hit": sum(yc) / len(yc), "brier": _brier(pc, yc)}
    both = [r for r in done if isinstance(r.get("p_ai05"), (int, float))]
    if both:
        yb = [1 if r["total_goals"] >= 1 else 0 for r in both]
        pm = [r["p_over05"] for r in both]
        pa = [r["p_ai05"] for r in both]
        pc = [min(a, b) for a, b in zip(pm, pa)]
        out["ai"] = {"n": len(both), "brier_model": _brier(pm, yb), "brier_ai": _brier(pa, yb), "brier_combo": _brier(pc, yb)}
    n_sig = won = 0
    profit = 0.0
    for r in done:
        for s in r["signals"]:
            if not s.get("value"):
                continue
            n_sig += 1
            hit = r["total_goals"] > s["line"] if s["side"] == "over" else r["total_goals"] < s["line"]
            won += hit
            profit += stake * (s["odds"] - 1.0) if hit else -stake
    out["signals"] = {"n": n_sig, "won": won, "profit": profit,
                      "roi": (profit / (stake * n_sig)) if n_sig else 0.0}
    return out

