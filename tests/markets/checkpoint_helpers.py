# ruff: noqa: E501
"""Synthetic in-progress captures for the checkpoint scorer tests (no network, no real data)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nba.markets import checkpoint_scorer as ck
from tests.markets.halftime_helpers import T0, Night, box, market

S = timedelta(seconds=1)
LABELS = [
    "MIN",
    "PTS",
    "FG",
    "3PT",
    "FT",
    "REB",
    "AST",
    "TO",
    "STL",
    "BLK",
    "OREB",
    "DREB",
    "PF",
    "+/-",
]


def clock_text(elapsed_s: float) -> tuple[int, str]:
    """(period, displayClock) for an elapsed regulation or overtime time."""
    if elapsed_s <= 2880:
        p = min(int(elapsed_s // 720) + 1, 4)
        rem = 720 - (elapsed_s - 720 * (p - 1))
    else:
        p = 5 + int((elapsed_s - 2880) // 300)
        rem = 300 - ((elapsed_s - 2880) - 300 * (p - 5))
    return p, f"{int(rem // 60)}:{int(rem % 60):02d}"


def players_at(elapsed_s: float) -> list[dict[str, Any]]:
    m = elapsed_s / 60.0
    return [
        box("Ann Alpha", "DET", max(1, int(m * 0.7)), pts=int(m * 0.3), reb=int(m * 0.1), ast=int(m * 0.08), pf=int(m / 20), starter=True),
        box("Bob Beta", "DET", 0, dnp=True, no_stats=True),
        box("Cy Gamma", "PHI", max(1, int(m * 0.5)), pts=int(m * 0.1), reb=int(m * 0.1), ast=1, pf=1),
        box("Di Delta", "PHI", max(1, int(m * 0.7)), pts=int(m * 0.25), reb=int(m * 0.05), ast=int(m * 0.12), pf=int(m / 15), starter=True),
    ]  # fmt: skip


def summary_body(
    players: list[dict[str, Any]],
    status: str,
    period: int | None,
    clock: str | None,
    away: int,
    home: int,
    last_play_at: datetime | None,
) -> dict[str, Any]:
    teams: dict[str, list[dict[str, Any]]] = {}
    for p in players:
        teams.setdefault(p["team"], []).append(
            {
                "athlete": {"displayName": p["name"]},
                "starter": p.get("starter", False),
                "didNotPlay": p.get("dnp", False),
                "ejected": p.get("ejected", False),
                "stats": p.get("stats", []),
            }
        )
    st: dict[str, Any] = {"type": {"name": status}}
    if period is not None:
        st["period"], st["displayClock"] = period, clock
    return {
        "header": {
            "competitions": [
                {
                    "status": st,
                    "competitors": [
                        {"homeAway": "away", "score": str(away), "team": {"abbreviation": "DET"}},
                        {"homeAway": "home", "score": str(home), "team": {"abbreviation": "PHI"}},
                    ],
                }
            ]
        },
        "plays": []
        if last_play_at is None
        else [{"wallclock": last_play_at.strftime("%Y-%m-%dT%H:%M:%SZ")}],
        "boxscore": {
            "players": [
                {"team": {"abbreviation": t}, "statistics": [{"labels": LABELS, "athletes": a}]}
                for t, a in teams.items()
            ]
        },
    }


def markets_for(series: str) -> list[dict[str, Any]]:
    return [
        market(series, "Ann Alpha", 15, ask=0.44),
        market(series, "Ann Alpha", 20, ask=0.10),
        market(series, "Di Delta", 15, ask=0.30),
        market(series, "Cy Gamma", 10, ask=0.30),  # n_prior 2: not scored
        market(series, "Bob Beta", 10, ask=0.30),  # no minutes: excluded
    ]


def build_night(
    root: Path,
    states: list[tuple[int, float, str]],
    *,
    page_after_s: int | None = 5,
    play_age_s: int = 10,
    series: tuple[str, ...] = ("KXNBAPTS",),
    extra: list[tuple[int, str, float, int | None]] | None = None,
) -> Path:
    """One game DET@PHI. ``states`` = (seconds after T0, elapsed game seconds, status): one ESPN summary
    each, newest play ``play_age_s`` old; a Kalshi page of each series ``page_after_s`` after it
    (None: no pages). ``extra`` = (seconds after T0, status, elapsed, period) summaries with no page."""
    n = Night(root / "live_2026-10-26")
    for t, e, status in states:
        period, clock = clock_text(e)
        at = T0 + t * S
        n.add(
            "espn_summary",
            "401900001",
            at,
            summary_body(players_at(e), status, period, clock, 50 + int(e / 60), 55 + int(e / 60), at - play_age_s * S),
        )  # fmt: skip
        if page_after_s is not None:
            for s in series:
                n.kalshi(s, at + page_after_s * S, markets_for(s))
    for t, status, e, period in extra or []:
        at = T0 + t * S
        if period is None:
            n.add(
                "espn_summary",
                "401900001",
                at,
                summary_body(players_at(e), status, None, None, 90, 95, None),
            )
        else:
            n.add(
                "espn_summary",
                "401900001",
                at,
                summary_body(players_at(e), status, period, "2:00", 90, 95, at - 5 * S),
            )
    return n.close()


def regular(n_ticks: int = 45, step_s: int = 65) -> list[tuple[int, float, str]]:
    """A tick every 60 s while the game clock advances ``step_s`` game seconds per tick."""
    return [(60 * i, float(step_s * i), "STATUS_IN_PROGRESS") for i in range(n_ticks)]


def first_tick(c: int, step_s: int = 65) -> int:
    return -(-60 * c // step_s)  # ceil(60 c / step_s): the first tick at or after checkpoint c


__all__ = ["T0", "S", "ck", "build_night", "regular", "first_tick", "clock_text"]
