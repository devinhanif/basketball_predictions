"""Stored production forecasts for one slate date (read-only; nothing is refit or re-predicted).

Props: the newest ``props_context_residual`` row per (game, player, stat) made before tip; the
marginal P(stat >= k) is the stored full-support count ``p_ge_full`` (conditional on playing) and
``p_play`` is the stored P(play). Wins: the row flagged ``primary`` of ``win_prob_home``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.parlay.kalshi_map import load_team_abbr

ET = ZoneInfo("America/New_York")
PROPS_MODEL = "props_context_residual"
PROP_TARGETS = ("pts", "reb", "ast", "fg3m")


@dataclass(frozen=True)
class PropForecast:
    game_id: str
    tipoff: datetime
    made_at: datetime
    stat: str
    player_id: int
    counts: tuple[int, ...]  # counts[k-1] = #samples with stat >= k
    n: int
    p_play: float
    version: str = ""

    def p_ge(self, k: int) -> float | None:
        """P(stat >= k | plays); None when k is beyond the stored support."""
        if k < 1:
            return 1.0
        if k > len(self.counts) or self.n <= 0:
            return None
        return self.counts[k - 1] / self.n


@dataclass(frozen=True)
class WinForecast:
    game_id: str
    tipoff: datetime
    made_at: datetime
    away: str  # Kalshi abbreviation
    home: str
    p_home: float


@dataclass
class Forecasts:
    props: dict[tuple[int, str], PropForecast] = field(default_factory=dict)
    wins: dict[tuple[str, str], WinForecast] = field(default_factory=dict)


def slate_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Naive-UTC bounds of the ET calendar day (tip-off times are stored naive UTC)."""
    start = datetime(day.year, day.month, day.day, tzinfo=ET)
    end = start + timedelta(days=1)
    end = datetime(end.year, end.month, end.day, tzinfo=ET)
    return start.astimezone(UTC).replace(tzinfo=None), end.astimezone(UTC).replace(tzinfo=None)


def load_forecasts(con: duckdb.DuckDBPyConnection, day: date, now: datetime) -> Forecasts:
    lo, hi = slate_bounds_utc(day)
    out = Forecasts()
    rows = con.execute(
        """
        SELECT game_id, tipoff, made_at, target, player_id, prediction, version
        FROM forward_predictions
        WHERE model_name = ? AND target IN ('pts','reb','ast','fg3m')
          AND tipoff >= ? AND tipoff < ? AND made_at <= ? AND made_at < tipoff
        QUALIFY row_number() OVER (PARTITION BY game_id, target, player_id
                                   ORDER BY made_at DESC) = 1
        """,
        [PROPS_MODEL, lo, hi, now],
    ).fetchall()
    for gid, tip, made, target, pid, pred, ver in rows:
        d: dict[str, Any] = json.loads(pred) if isinstance(pred, str) else pred
        full = d.get("p_ge_full") or {}
        counts = full.get("c")
        if not counts or not full.get("n") or d.get("p_play") is None:
            continue
        out.props[(int(pid), str(target))] = PropForecast(
            str(gid), tip, made, str(target), int(pid), tuple(int(c) for c in counts),
            int(full["n"]), float(d["p_play"]), str(ver or ""),
        )  # fmt: skip
    id_to_abbr = {v: k for k, v in load_team_abbr().items()}
    wrows = con.execute(
        """
        SELECT game_id, tipoff, made_at, prediction FROM forward_predictions
        WHERE target = 'win_prob_home' AND json_extract_string(prediction, '$.primary') = 'true'
          AND tipoff >= ? AND tipoff < ? AND made_at <= ? AND made_at < tipoff
        QUALIFY row_number() OVER (PARTITION BY game_id ORDER BY made_at DESC) = 1
        """,
        [lo, hi, now],
    ).fetchall()
    for gid, tip, made, pred in wrows:
        d = json.loads(pred) if isinstance(pred, str) else pred
        away, home = id_to_abbr.get(int(d["away_team"])), id_to_abbr.get(int(d["home_team"]))
        if away is None or home is None or d.get("p_home") is None:
            continue
        out.wins[(away, home)] = WinForecast(str(gid), tip, made, away, home, float(d["p_home"]))
    return out
