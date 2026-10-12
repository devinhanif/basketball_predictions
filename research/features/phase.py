# ruff: noqa: E501
"""Season-phase state for COACH_PHASE (docs/prereg/COACH_PHASE.md, DRAFT). Pure polars, no database.

``series_state``: playoff series position per (game_id, team_id), from games dated strictly earlier in the same
series (same season, same team pair). ``clinch_state``: regular-season clinch / elimination flags from standings
over games dated STRICTLY earlier than the row's date (same-day games are blind to each other), using sufficient
conditions that ignore tiebreakers in the team's disfavour:

* ``clinched_k``: fewer than k other conference teams can still finish with at least this team's CURRENT wins.
* ``elim_k``: at least k other conference teams already have at least this team's MAXIMUM possible wins.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import polars as pl

from nba.features.game_context import TEAM_CONFERENCE

SEASON_GAMES = 82


def series_state(po_games: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): series game number, wins before the game for team and opponent, home flag,
    elimination game (the opponent already has 3 wins), clinching chance (the team has 3), home-court holder.

    ``po_games``: game_id, game_date, season, home_team, away_team, home_pts, away_pts (playoff games only)."""
    g = po_games.sort(["game_date", "game_id"])
    wins: dict[tuple[int, frozenset[int]], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    count: dict[tuple[int, frozenset[int]], int] = defaultdict(int)
    first_home: dict[tuple[int, frozenset[int]], int] = {}
    out: list[dict[str, Any]] = []
    day_updates: list[tuple[tuple[int, frozenset[int]], int]] = []
    last_date = None
    for r in g.iter_rows(named=True):
        if last_date is not None and r["game_date"] != last_date:
            for key, w in day_updates:
                wins[key][w] += 1
                count[key] += 1
            day_updates = []
        last_date = r["game_date"]
        key = (r["season"], frozenset((r["home_team"], r["away_team"])))
        first_home.setdefault(key, r["home_team"])
        for t, o in ((r["home_team"], r["away_team"]), (r["away_team"], r["home_team"])):
            wt, wo = wins[key][t], wins[key][o]
            out.append(
                {
                    "game_id": r["game_id"],
                    "team_id": t,
                    "series_game": count[key] + 1,
                    "wins_for": wt,
                    "wins_against": wo,
                    "is_home": t == r["home_team"],
                    "home_court": t == first_home[key],
                    "elim_game": wo >= 3,
                    "close_out_chance": wt >= 3,
                }
            )
        day_updates.append(
            (key, r["home_team"] if r["home_pts"] > r["away_pts"] else r["away_team"])
        )
    return pl.DataFrame(out)


def clinch_state(rs_games: pl.DataFrame, ks: tuple[int, ...] = (6, 10)) -> pl.DataFrame:
    """Per (game_id, team_id): clinched_k and elim_k flags for each k, as-of strictly earlier dates.

    ``rs_games``: regular-season games only (game_id, game_date, season, home_team, away_team, home_pts, away_pts)."""
    g = rs_games.sort(["game_date", "game_id"])
    out: list[dict[str, Any]] = []
    for season in g["season"].unique().sort().to_list():
        gs = g.filter(pl.col("season") == season)
        rec: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for date in gs["game_date"].unique().sort().to_list():
            day = gs.filter(pl.col("game_date") == date)
            for r in day.iter_rows(named=True):
                for t in (r["home_team"], r["away_team"]):
                    conf = TEAM_CONFERENCE.get(t)
                    w, lo = rec[t]
                    peers = [
                        (rec[u][0], SEASON_GAMES - rec[u][0] - rec[u][1])
                        for u in TEAM_CONFERENCE
                        if u != t and TEAM_CONFERENCE[u] == conf
                    ]
                    row: dict[str, Any] = {"game_id": r["game_id"], "team_id": t}
                    left = SEASON_GAMES - w - lo
                    for k in ks:
                        row[f"clinched_{k}"] = sum(1 for wj, rj in peers if wj + rj >= w) < k
                        row[f"elim_{k}"] = sum(1 for wj, _ in peers if wj >= w + left) >= k
                    out.append(row)
            for r in day.iter_rows(named=True):
                hw = r["home_pts"] > r["away_pts"]
                rec[r["home_team"]][0 if hw else 1] += 1
                rec[r["away_team"]][1 if hw else 0] += 1
    return pl.DataFrame(out)
