# ruff: noqa: E501
"""RETURNING_TEAMMATE_2 features (rule: docs/prereg/RETURNING_TEAMMATE_2.md; DRAFT until frozen).

As-of "returning teammate" columns for every player-game. For player ``i`` of team ``T`` in game ``g`` (``k`` = T's game order in the
season by real tip, ties by game_id), a RETURNER ``j != i`` is a player who, using only information knowable at T-60 before g:

* played (minutes > 0) for T earlier this season, and played none of T's last ``MIN_MISSED`` games before g (streak = T games since his last
  played T game, >= MIN_MISSED), and played for no other team since (traded players drop out);
* was listed (any status) on the latest usable snapshot of T's PREVIOUS game (the report-listed absence; ``prev_rule="listed_prev"``, the rule's
  definition). The stricter ``prev_rule="out_all"`` (listed ``out`` on each of the previous ``MIN_MISSED`` snapshots; the brief's literal "was OUT for
  the previous N games") is a declared SLICE. A game with no usable snapshot breaks the run: the player is not a returner (no imputation);
* g itself has a usable snapshot (``ret_known`` = 1; otherwise every column is 0 and ``ret_known`` = 0, the same fact production stores as
  ``has_report``), and j is neither ``out`` nor ``doubtful`` on it. ``doubtful`` is excluded because the 2026-10-12 gate measured doubtful
  returners playing 1.0% of 204 games (an outcome-free choice on the returner's own play flag, disclosed in the rule).

Columns (all 0 when nobody returns): ``ret_min`` (sum over returners of j's mean minutes over his last ``PRE_WINDOW`` played games before g),
``ret_min_star`` (the same, only returners with that mean >= ``STAR_MIN``), ``ret_min_listed`` (the same, only returners listed
``available``, ``probable`` or ``questionable`` on g's snapshot, i.e. not merely absent from it; added after the gate showed unlisted returners play
38-45% against 49-82% for listed ones), ``n_ret``, ``ret_use`` (sum over returners of j's mean usage share
over the same games: (fga + 0.44 fta + tov) / the team's total of that quantity in that game), ``ret_missed`` (longest streak among returners,
capped at ``MISSED_CAP``), ``ret_own_gain`` (i's mean minutes over his played games in T's last min(5, ret_missed) games before g minus his mean
over his last ``OWN_BASE`` played games before that window; 0 when either is empty or nobody returns).

Snapshot (the one input the injury-timing work may change): ``status`` maps (game_id, player_id) to the status on the latest usable snapshot
of that game as produced by ``snapshot_status`` below, which wraps ``nba.features.injury_report.usable_report_rows`` with
``ReportTriggerConfig(lead_minutes=60, tip_source="real")``. ``SNAPSHOT_ID`` names that definition; the harness pin refuses a run whose
snapshot source differs from the one the rule was frozen with.
"""

from __future__ import annotations

import hashlib
import inspect
from collections import defaultdict
from typing import Any

import numpy as np
import polars as pl

MIN_MISSED = 3
STAR_MIN = 30.0
ROTATION_MIN = 10.0
PRE_WINDOW = 10
OWN_WINDOW_MAX = 5
OWN_BASE = 40
MISSED_CAP = 30
LISTED_TONIGHT = ("available", "probable", "questionable")
FEATURES = (
    "ret_min",
    "ret_min_star",
    "ret_min_listed",
    "n_ret",
    "ret_use",
    "ret_missed",
    "ret_own_gain",
)
FEATURES_ALL = (*FEATURES, "ret_known")
SNAPSHOT_ID = "latest usable snapshot per game: as_of <= coalesce(tip_et, game_date+19h) - 60 min, source nba_official_report"


def snapshot_status(report: pl.DataFrame, games: pl.DataFrame, lead: int = 60) -> pl.DataFrame:
    """Per (game, player): status on the LATEST snapshot stamped ``<= tip - lead``. ``report``: game_id, player_id, status, as_of;
    ``games``: game_id, tip. A player absent from that snapshot is unlisted (no row). Games without a usable snapshot have no rows."""
    r = report.join(games.select("game_id", "tip"), on="game_id", how="inner")
    r = r.filter(pl.col("as_of") <= pl.col("tip") - pl.duration(minutes=lead))
    latest = r.group_by("game_id").agg(pl.col("as_of").max().alias("snap_as_of"))
    snap = r.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("snap_as_of"))
    return snap.select("game_id", "player_id", "status", "snap_as_of").unique(
        ["game_id", "player_id"], keep="last"
    )


def snapshot_source_sha() -> str:
    """sha256 of the snapshot rule: its description plus ``snapshot_status``'s source with all whitespace collapsed (robust to reformatting;
    pinned by the harness: a change means the rule must be re-registered)."""
    src = " ".join(inspect.getsource(snapshot_status).split())
    return hashlib.sha256((SNAPSHOT_ID + src).encode()).hexdigest()


def usage_table(box: pl.DataFrame) -> dict[tuple[str, int], float]:
    """(game_id, player_id) -> usage share of the team's possessions used in that game: (fga + 0.44 fta + tov) / team total."""
    u = box.with_columns(
        (
            pl.col("fga").fill_null(0)
            + 0.44 * pl.col("fta").fill_null(0)
            + pl.col("tov").fill_null(0)
        ).alias("_u")
    )
    u = u.with_columns(
        (pl.col("_u") / pl.col("_u").sum().over(["game_id", "team_id"]))
        .fill_nan(0.0)
        .fill_null(0.0)
        .alias("_share")
    )
    return {
        (str(g), int(p)): float(s)
        for g, p, s in u.select("game_id", "player_id", "_share").iter_rows()
    }


def build_returning(
    box: pl.DataFrame,
    tg: pl.DataFrame,
    status: pl.DataFrame,
    min_missed: int = MIN_MISSED,
    exclude: tuple[str, ...] = ("out", "doubtful"),
    prev_rule: str = "listed_prev",
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """As-of returning-teammate columns.

    ``box``: game_id, team_id, player_id, minutes (NULL or 0 = did not play), game_date, fga, fta, tov.
    ``tg``: team_id, season, game_id, game_date, k.  ``status``: game_id, player_id, status (``snapshot_status`` output).
    Returns (features for every box player of every team-game, flagged returners per (game_id, team_id, returner_id, streak, pre_mpg, pre_use,
    plays_tonight)). ``plays_tonight`` is DESCRIPTIVE (knowability of the flag); it never feeds a feature. ``prev_rule``: "listed_prev" (the rule: any status on the previous
    game's snapshot; RETURNING_TEAMMATE v1's variant B), "out_all" (listed out on every one of the previous ``min_missed`` snapshots; the slice),
    "box_only" (audit only).
    For game g only box rows of T's earlier games, other teams' games dated strictly before g, the snapshots of T's previous ``min_missed``
    games and g's own snapshot are read; g's own box row is used only to enumerate who gets a row (and, in the second frame, ``plays``)."""
    played = box.with_columns(pl.col("minutes").fill_null(0.0).cast(pl.Float64)).join(
        tg.select("team_id", "game_id", "season", "k"), on=["team_id", "game_id"], how="inner"
    )
    st: dict[tuple[str, int], str] = {
        (str(g), int(p)): s
        for g, p, s in status.select("game_id", "player_id", "status").iter_rows()
    }
    snap_games = set(status["game_id"].unique().to_list())
    use = usage_table(box)
    pl_hist: dict[int, list[tuple[Any, int, float, str]]] = defaultdict(list)
    for d, team, pid, m, gid in (
        played.filter(pl.col("minutes") > 0)
        .sort(["game_date", "game_id"])
        .select("game_date", "team_id", "player_id", "minutes", "game_id")
        .iter_rows()
    ):
        pl_hist[int(pid)].append((d, int(team), float(m), str(gid)))

    feats: list[tuple[Any, ...]] = []
    rets: list[tuple[Any, ...]] = []
    for (team, _season), grp in played.group_by(["team_id", "season"], maintain_order=True):
        team = int(team)
        games = grp.select("k", "game_id", "game_date").unique().sort("k").rows()
        by_k: dict[int, dict[int, float]] = defaultdict(dict)
        for k, pid, m in grp.select("k", "player_id", "minutes").iter_rows():
            by_k[int(k)][int(pid)] = float(m)
        last_played: dict[int, int] = {}
        for k, gid, gdate in games:
            k = int(k)
            known = str(gid) in snap_games
            flagged: list[tuple[int, int, float, float, bool]] = []
            if known:
                prev_ids = [str(games[x][1]) for x in range(max(0, k - min_missed), k)]
                for j, lk in last_played.items():
                    streak = k - 1 - lk
                    if streak < min_missed or len(prev_ids) < min_missed:
                        continue
                    hist = [h for h in pl_hist[j] if h[0] < gdate]
                    if hist and hist[-1][1] != team:
                        continue  # played for another team since: not a returner
                    if prev_rule == "out_all" and any(st.get((pg, j)) != "out" for pg in prev_ids):
                        continue  # not listed out on every one of the previous min_missed snapshots
                    if prev_rule == "listed_prev" and (prev_ids[-1], j) not in st:
                        continue  # not listed (any status) on the snapshot of the team's previous game
                    if st.get((str(gid), j)) in exclude:
                        continue
                    last = hist[-PRE_WINDOW:]
                    pre_mpg = float(np.mean([h[2] for h in last])) if last else 0.0
                    pre_use = (
                        float(np.mean([use.get((h[3], j), 0.0) for h in last])) if last else 0.0
                    )
                    flagged.append(
                        (j, streak, pre_mpg, pre_use, st.get((str(gid), j)) in LISTED_TONIGHT)
                    )
                    rets.append(
                        (str(gid), team, j, streak, pre_mpg, pre_use, by_k[k].get(j, 0.0) > 0)
                    )
            miss = min(MISSED_CAP, max((f[1] for f in flagged), default=0))
            w = min(OWN_WINDOW_MAX, miss)
            win_dates = {games[x][2] for x in range(k - w, k) if x >= 0}
            first_win = min(win_dates) if win_dates else gdate
            for i in by_k[k]:
                others = [f for f in flagged if f[0] != i]
                own = 0.0
                if others:
                    hi = [h for h in pl_hist[i] if h[0] < gdate]
                    in_w = [h[2] for h in hi if h[1] == team and h[0] in win_dates]
                    before = [h[2] for h in hi if h[0] < first_win][-OWN_BASE:]
                    if in_w and before:
                        own = float(np.mean(in_w) - np.mean(before))
                feats.append(
                    (
                        str(gid),
                        team,
                        int(i),
                        float(sum(f[2] for f in others)),
                        float(sum(f[2] for f in others if f[2] >= STAR_MIN)),
                        float(sum(f[2] for f in others if f[4])),
                        len(others),
                        float(sum(f[3] for f in others)),
                        float(min(MISSED_CAP, max((f[1] for f in others), default=0))),
                        own,
                        1 if known else 0,
                    )
                )
            for pid, m in by_k[k].items():
                if m > 0:
                    last_played[pid] = k
    f = pl.DataFrame(
        feats,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "ret_min": pl.Float64,
            "ret_min_star": pl.Float64,
            "ret_min_listed": pl.Float64,
            "n_ret": pl.Int64,
            "ret_use": pl.Float64,
            "ret_missed": pl.Float64,
            "ret_own_gain": pl.Float64,
            "ret_known": pl.Int64,
        },
        orient="row",
    )
    r = pl.DataFrame(
        rets,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "returner_id": pl.Int64,
            "streak": pl.Int64,
            "pre_mpg": pl.Float64,
            "pre_use": pl.Float64,
            "plays_tonight": pl.Boolean,
        },
        orient="row",
    )
    return f, r


__all__ = [
    "FEATURES",
    "FEATURES_ALL",
    "MIN_MISSED",
    "SNAPSHOT_ID",
    "build_returning",
    "snapshot_source_sha",
    "snapshot_status",
    "usage_table",
]
