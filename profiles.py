from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CategoryProfile:
    code: str
    label: str
    half_life_days: float
    prior_weight: float
    home_adv: float
    rho: float
    min_games: int
    dispersion: float
    ai_weight: float
    quality_cap: int
    green_quality: int
    over_green: float
    under_green: float
    yellow: float
    note: str


PROFILES = {
    "senior": CategoryProfile(
        "senior", "MAYORES", 90.0, 6.0, 1.12, -0.05, 8, 0.08, 0.35, 96, 72, 0.93, 0.96, 0.85,
        "Más peso a forma, local/visita, DT, alineaciones y estructura estable.",
    ),
    "women": CategoryProfile(
        "women", "FEMENINO", 70.0, 5.0, 1.10, -0.03, 7, 0.14, 0.40, 90, 74, 0.94, 0.97, 0.86,
        "Mayor sensibilidad a diferencias de nivel, profundidad del plantel y dispersión de marcadores.",
    ),
    "youth": CategoryProfile(
        "youth", "JUVENIL", 45.0, 4.0, 1.06, -0.02, 6, 0.18, 0.45, 84, 76, 0.95, 0.975, 0.87,
        "El histórico envejece rápido; pesan mucho convocatoria, generación actual y alineación.",
    ),
    "reserve": CategoryProfile(
        "reserve", "RESERVAS / B", 40.0, 4.0, 1.05, -0.02, 6, 0.20, 0.45, 80, 77, 0.955, 0.98, 0.88,
        "Alta rotación: pesa más la plantilla disponible del día que el nombre del club.",
    ),
    "friendly": CategoryProfile(
        "friendly", "AMISTOSO", 55.0, 5.0, 1.05, -0.02, 7, 0.18, 0.40, 82, 77, 0.95, 0.98, 0.88,
        "Objetivos competitivos y rotaciones son inciertos; se exige más margen para aprobar.",
    ),
}

_WOMEN = re.compile(r"\b(women|woman|womens|ladies|femenin[oa]s?|fem\.?|women's)\b", re.I)
_YOUTH = re.compile(r"\b(u[- ]?(?:15|16|17|18|19|20|21|22|23)|under[- ]?(?:15|16|17|18|19|20|21|22|23)|youth|juvenil|sub[- ]?(?:15|16|17|18|19|20|21|22|23))\b", re.I)
_RESERVE = re.compile(r"\b(reserve|reserves|reserva|reservas|academy|academia|b team|team b|ii)\b", re.I)
_FRIENDLY = re.compile(r"\b(friendl(?:y|ies)|amistos[oa]s?)\b", re.I)


def classify(home: str = "", away: str = "", league: str = "", country: str = "") -> CategoryProfile:
    text = " ".join(x or "" for x in (home, away, league, country))
    if _WOMEN.search(text):
        return PROFILES["women"]
    if _YOUTH.search(text):
        return PROFILES["youth"]
    if _RESERVE.search(text):
        return PROFILES["reserve"]
    if _FRIENDLY.search(text):
        return PROFILES["friendly"]
    return PROFILES["senior"]


def study_quality(profile: CategoryProfile, n_home: int, n_away: int,
                  ai_ok: bool, lineup_count: int = 0, context_count: int = 0,
                  ai_quality: Optional[int] = None) -> int:
    """Índice 0-100 de cuánto se puede estudiar el partido, no probabilidad de acierto."""
    need = max(1, profile.min_games)
    hist = min(1.0, min(n_home, n_away) / max(need * 2, 12))
    score = 48 + 34 * hist
    if ai_ok:
        score += 7
    if lineup_count >= 2:
        score += 5
    elif lineup_count == 1:
        score += 2
    score += min(4, context_count)
    if ai_quality is not None:
        score = 0.75 * score + 0.25 * max(0, min(100, ai_quality))
    return int(round(max(0, min(profile.quality_cap, score))))


def quality_label(score: int) -> str:
    if score >= 85:
        return "ALTA"
    if score >= 72:
        return "MEDIA"
    return "BAJA"
