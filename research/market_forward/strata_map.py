"""Kalshi game key -> (ET date, away team id, home team id), using the reviewed team table."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from nba.parlay.kalshi_map import load_team_abbr, parse_event


@lru_cache(maxsize=1)
def _abbr() -> dict[str, int]:
    return load_team_abbr()


def parse_key(key: str) -> tuple[date, int, int] | None:
    ev = parse_event(f"KXNBAGAME-{key}")
    if ev is None:
        return None
    d, away, home = ev
    a, h = _abbr().get(away), _abbr().get(home)
    if a is None or h is None:
        return None
    return d, a, h
