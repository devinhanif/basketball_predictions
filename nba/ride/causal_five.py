# ruff: noqa: E501
"""Causal state prefix and causal on-floor five for INGAME_WINPROB_V2 (review M2, M3).

State at T = the longest prefix of the game's events, in action-number order, in which every event has
elapsed <= T. The first event with elapsed > T and everything after it are excluded, even a later-logged
event whose elapsed is <= T. ``np.searchsorted`` on the elapsed vector is NOT used (the vector is not
sorted: elapsed goes backwards about 3 times a game).

The five on the floor at T is the lineup tracker (``nba.parse.lineups``) run on that prefix only, with the
Q1 seed from the box starters (announced before tip) and NO box roster (``roster_by_team=None``: who
played is end-of-game information). Period openers are therefore found only from events <= T; open slots
are filled from the previous closing five by the tracker's own rule. The stints table is never read.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from nba.parse.lineups import track_lineups_with_report


def prefix_len(elapsed: np.ndarray, t: float) -> int:
    """Number of leading events (in the given order) before the first one with elapsed > t."""
    over = np.asarray(elapsed, dtype=float) > t
    return int(np.argmax(over)) if bool(over.any()) else int(len(over))


def causal_prefix(pbp: pd.DataFrame, elapsed: np.ndarray, t: float) -> pd.DataFrame:
    """Events of the causal prefix at T. ``pbp`` and ``elapsed`` must already be in action-number order."""
    return pbp.iloc[: prefix_len(elapsed, t)]


def causal_five_with_report(
    prefix: pd.DataFrame,
    starters_by_team: dict[int, list[int]],
    roster_by_team: dict[int, list[int]] | None = None,
    names_by_player: dict[int, str] | None = None,
) -> tuple[dict[int, tuple[int, ...]], list[dict[str, object]]]:
    """Five per team at the end of ``prefix`` and the substitutions the tracker could not name.

    ``roster_by_team`` / ``names_by_player`` must be AS-OF (see ``asof_inputs``); the box roster of this game
    is never passed. An unresolved substitution leaves the outgoing player on the floor (the tracker's rule).
    """
    if len(prefix) == 0:
        return {t: tuple(v) for t, v in starters_by_team.items()}, []
    stints, report = track_lineups_with_report(
        pl.from_pandas(prefix), starters_by_team, roster_by_team, names_by_player
    )
    out: dict[int, tuple[int, ...]] = {}
    for team, five in starters_by_team.items():
        rows = stints.filter(pl.col("team_id") == team)
        if rows.is_empty():
            out[team] = tuple(five)
            continue
        last = rows.sort(["period", "stint_idx"]).tail(1)
        out[team] = tuple(int(p) for p in last["players"][0])
    return out, list(report.unresolved)


def causal_five(
    prefix: pd.DataFrame,
    starters_by_team: dict[int, list[int]],
    roster_by_team: dict[int, list[int]] | None = None,
    names_by_player: dict[int, str] | None = None,
) -> dict[int, tuple[int, ...]]:
    return causal_five_with_report(prefix, starters_by_team, roster_by_team, names_by_player)[0]


def asof_roster(
    pgs: pd.DataFrame,
    games: pd.DataFrame,
    game_id: str,
    starters_by_team: dict[int, list[int]],
) -> dict[int, list[int]]:
    """Per team: players with minutes > 0 for that team in strictly earlier games (same season) + this game's box starters.

    ``pgs``: game_id, team_id, player_id, minutes for the season; ``games``: game_id, game_date, home_team, away_team.
    """
    date = games.loc[games.game_id == game_id, "game_date"].iloc[0]
    earlier = set(games.loc[pd.to_datetime(games.game_date) < pd.to_datetime(date), "game_id"])
    out: dict[int, list[int]] = {}
    for team, st in starters_by_team.items():
        got = pgs[(pgs.team_id == team) & pgs.game_id.isin(earlier) & (pgs.minutes > 0)]
        ids = list(dict.fromkeys([*st, *[int(x) for x in got.player_id]]))
        out[team] = ids
    return out


def asof_names(
    root_pbp: Path, games: pd.DataFrame, game_id: str, team: int, ids: set[int], n_prev: int = 15
) -> dict[int, str]:
    """player_id -> feed name from the team's ``n_prev`` previous pbp files (strictly earlier games)."""
    date = games.loc[games.game_id == game_id, "game_date"].iloc[0]
    g = games[
        (pd.to_datetime(games.game_date) < pd.to_datetime(date))
        & ((games.home_team == team) | (games.away_team == team))
    ].sort_values(["game_date", "game_id"])
    names: dict[int, str] = {}
    for gid in list(g.game_id)[-n_prev:]:
        f = root_pbp / f"{gid}.parquet"
        if not f.exists():
            continue
        d = pd.read_parquet(f, columns=["team_id", "player_id", "player_name"])
        d = d[(d.team_id == team) & d.player_id.isin(ids) & (d.player_name != "")]
        for pid, nm in zip(d.player_id, d.player_name, strict=True):
            names[int(pid)] = str(nm)
    return names


def rows_mismatch(a: dict[str, object], b: dict[str, object], tol: float = 1e-12) -> list[str]:
    """Column names on which two state rows differ: integers and sets exactly, floats within ``tol``.

    The comparison used by the incremental-builder equality test and by the run-time identity (rule 7(b)).
    """
    bad: list[str] = []
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            bad.append(k)
            continue
        x, y = a[k], b[k]
        if isinstance(x, float) or isinstance(y, float):
            fx, fy = float(str(x)), float(str(y))
            if not (fx == fy or abs(fx - fy) <= tol or (fx != fx and fy != fy)):
                bad.append(k)
        elif isinstance(x, (set, frozenset, tuple, list)) and isinstance(
            y, (set, frozenset, tuple, list)
        ):
            if set(x) != set(y):
                bad.append(k)
        elif x != y:
            bad.append(k)
    return bad
