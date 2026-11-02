"""Canonical membership-tier priority used by backend operations."""

from __future__ import annotations

from sqlalchemy import case, func

TIER_RANKS = {
    "diamond": 500,
    "platinum": 400,
    "gold": 300,
    "silver": 200,
    "bronze": 100,
}


def normalize_tier(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in TIER_RANKS else "unknown"


def tier_rank(value: object) -> int:
    return TIER_RANKS.get(normalize_tier(value), 0)


def alert_level(value: object) -> str:
    normalized = normalize_tier(value)
    if normalized in {"diamond", "platinum", "gold"}:
        return "vip"
    if normalized == "silver":
        return "priority"
    return "normal"


def sql_tier_rank(column):
    normalized = func.lower(func.trim(column))
    return case(
        (normalized == "diamond", 500),
        (normalized == "platinum", 400),
        (normalized == "gold", 300),
        (normalized == "silver", 200),
        (normalized == "bronze", 100),
        else_=0,
    )
