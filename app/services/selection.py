from __future__ import annotations

from app.config import Settings


def freshness_bucket(age_min: int | None, settings: Settings) -> str | None:
    if age_min is None:
        return None
    if age_min <= settings.freshness_hot_max:
        return "hot"
    if age_min <= settings.freshness_warm_max:
        return "warm"
    if age_min <= settings.freshness_cooling_max:
        return "cooling"
    return "stale"


def activity_score_from_age(age_min: int | None, restricted: bool) -> float:
    if restricted:
        return 0.0
    if age_min is None:
        return 5.0
    if age_min <= 60:
        return 100.0
    if age_min <= 180:
        return 80.0
    if age_min <= 360:
        return 60.0
    if age_min <= 1440:
        return 25.0
    return 5.0


def number_status_from_age(age_min: int | None, restricted: bool, settings: Settings) -> str:
    if restricted:
        return "suspect"
    if age_min is None:
        return "candidate"
    if age_min <= settings.freshness_warm_max:
        return "active"
    if age_min <= settings.freshness_cooling_max:
        return "candidate"
    return "stale"
