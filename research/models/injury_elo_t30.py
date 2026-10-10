"""T-30 injury-adjusted MOV-Elo features: the per-game inactive list is known.

Opt-in module (production ``nba.models.injury_elo`` is untouched). The T-60
model sees the official report only; at T-30 teams have submitted their
inactive list, so a player listed questionable at T-60 is either known out
(inactive) or known active. Features keep the production recipe and sign
convention (away minus home value, ``VALUE_SCALE`` units)::

    out30  = report OUT (lead 30)  UNION  inactive proxy
    dbt30  = report DOUBTFUL (lead 30)  MINUS out30

LEAK STATEMENT: the inactive proxy is derived from the game's own box score
(a rostered player with no box row tonight). It is post-hoc by construction;
see ``docs/INJURY_ELO_T30.md``. Never feed these features to the T-60 path.

As-of discipline (mirrors ``player_possession_features.py``): player values
and the roster window use games with ``game_date`` strictly before the game's
date; the ONLY same-game input is row PRESENCE of a (player, team, game) in
the box score (the list proxy). Tonight's minutes and stat values never enter.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass, field, replace

import duckdb
import polars as pl

from nba.models.injury_elo import (
    VALUE_SCALE,
    InjuryFeatureConfig,
    _rotation_state,
    _side_sums,
    asof_values,
    build_value_state,
    league_rate_by_date,
)
from research.sim.usage_redistribution import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
    rotation_flagged_by_team,
)

T30_FEATURE_COLS: tuple[str, str] = ("d_out30", "d_doubt30")
WC_FEATURE_COLS: tuple[str, str] = ("d_out_wc", "d_doubt_wc")
HINT_STATUSES: tuple[str, ...] = ("questionable", "doubtful", "probable")


@dataclass(frozen=True)
class T30Config:
    base: InjuryFeatureConfig = field(default_factory=InjuryFeatureConfig)
    lead_minutes_t30: int = 30
    window_games: int = 10


def inactive_proxy(
    stats: pl.DataFrame, games: pl.DataFrame, window_games: int = 10
) -> dict[tuple[str, int], set[int]]:
    """Proxy inactive list per ``(game_id, team_id)``.

    A player is inactive tonight iff he has no box row in the game, he has a
    row for the team in one of its previous ``window_games`` games of the same
    season, and his latest prior row is for this team. ``stats`` needs
    player_id, team_id, game_id (ANY row counts as present, minutes ignored).
    Only row presence of tonight enters; the window and trade guard use games
    strictly earlier in (game_date, game_id) order.
    """
    roster: dict[tuple[str, int], set[int]] = {}
    for pid, team, gid in stats.select(["player_id", "team_id", "game_id"]).iter_rows():
        roster.setdefault((str(gid), int(team)), set()).add(int(pid))
    recent: dict[tuple[int, int], deque[set[int]]] = {}
    last_team: dict[int, int] = {}
    out: dict[tuple[str, int], set[int]] = {}
    ordered = games.sort(["game_date", "game_id"]).select(
        ["game_id", "season", "home_team", "away_team"]
    )
    for gid, season, h, a in ordered.iter_rows():
        sides = [
            (int(h), roster.get((str(gid), int(h)), set())),
            (int(a), roster.get((str(gid), int(a)), set())),
        ]
        for team, tonight in sides:
            window = recent.get((int(season), team))
            if not window:
                continue
            cand: set[int] = set().union(*window)
            miss = {p for p in cand - tonight if last_team.get(p) == team}
            if miss:
                out[(str(gid), team)] = miss
        for team, tonight in sides:
            if tonight:
                recent.setdefault((int(season), team), deque(maxlen=window_games)).append(tonight)
                for p in tonight:
                    last_team[p] = team
    return out


def _flags(
    rows: pl.DataFrame, statuses: tuple[str, ...], cfg: InjuryFeatureConfig, lead: int
) -> dict[str, set[int]]:
    rc = ReportTriggerConfig(
        statuses=statuses,
        tipoff_hour_et=cfg.tipoff_hour_et,
        lead_minutes=lead,
        table=cfg.report_table,
        tip_source=cfg.tip_source,
    )
    flagged, _ = latest_pretip_flagged(rows, rc)
    return flagged


def _union(*sets: dict[str, set[int]]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for s in sets:
        for gid, pids in s.items():
            out.setdefault(gid, set()).update(pids)
    return out


def build_t30_features(
    con: duckdb.DuckDBPyConnection,
    games: pl.DataFrame,
    stats: pl.DataFrame,
    cfg: T30Config,
    *,
    report_rows: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per game: T-30 and worst-case injury-value differentials.

    Columns: game_id, d_out30, d_doubt30, d_out_wc, d_doubt_wc, n_rot_out30,
    n_rot_late_add (rotation players on out30 but not on the lead-30 report
    OUT set), n_rot_gtd (rotation players questionable/doubtful at lead 60).
    Positive favors home (away minus home), as in production.
    """
    base = cfg.base
    state = build_value_state(stats, base.value)
    league = league_rate_by_date(stats)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    gids = games["game_id"].to_list()
    if report_rows is None:
        rc = ReportTriggerConfig(
            statuses=("out",),
            tipoff_hour_et=base.tipoff_hour_et,
            lead_minutes=base.lead_minutes,
            table=base.report_table,
            tip_source=base.tip_source,
        )
        rows = load_report_rows(con, rc, game_ids=gids)
    else:
        rows = report_rows
    if base.tip_source == "real":
        from nba.features.game_tipoff import attach_real_tips

        rows = attach_real_tips(rows)

    lead30, lead60 = cfg.lead_minutes_t30, base.lead_minutes
    out30_rep = _flags(rows, ("out",), base, lead30)
    dbt30_rep = _flags(rows, ("doubtful",), base, lead30)
    out60_rep = _flags(rows, ("out",), base, lead60)
    dbt60_rep = _flags(rows, ("doubtful",), base, lead60)
    hint60 = _flags(rows, HINT_STATUSES, base, lead60)
    gtd60 = _flags(rows, ("questionable", "doubtful"), base, lead60)

    inactive = inactive_proxy(stats, games, cfg.window_games)
    inact_by_game: dict[str, set[int]] = {}
    for (gid, _team), pids in inactive.items():
        inact_by_game.setdefault(gid, set()).update(pids)

    out30 = _union(out30_rep, inact_by_game)
    dbt30 = {g: p - out30.get(g, set()) for g, p in dbt30_rep.items()}
    wc_inact = {g: p - hint60.get(g, set()) for g, p in inact_by_game.items()}
    out_wc = _union(out60_rep, wc_inact)
    dbt_wc = {g: p - out_wc.get(g, set()) for g, p in dbt60_rep.items()}

    def side(out: dict[str, set[int]], dbt: dict[str, set[int]]) -> dict[str, tuple[float, ...]]:
        recs: list[tuple[str, int, dt.date, str]] = []
        for status, flag in (("out", out), ("doubtful", dbt)):
            for gid, pids in flag.items():
                for pid in pids:
                    recs.append((gid, pid, info[gid][0], status))
        if not recs:
            return {}
        pairs = pl.DataFrame(
            recs,
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "game_date": pl.Date,
                "status": pl.Utf8,
            },
            orient="row",
        )
        vals = asof_values(pairs.drop("status"), state, league, base.value).join(
            pairs.select(["game_id", "player_id", "status"]), on=["game_id", "player_id"]
        )
        return dict(_side_sums(vals, info, base.doubtful_weight))

    s30 = side(out30, dbt30)
    swc = side(out_wc, dbt_wc)

    rot_state = _rotation_state(state)

    def n_rot(flag: dict[str, set[int]]) -> dict[str, int]:
        rot = rotation_flagged_by_team(
            flag, info, rot_state, base.rotation_min_avg, base.rotation_min_games
        )
        acc: dict[str, int] = {}
        for (gid, _t), pids in rot.items():
            acc[gid] = acc.get(gid, 0) + len(pids)
        return acc

    late = {g: out30.get(g, set()) - out30_rep.get(g, set()) for g in out30}
    n30, nlate, ngtd = n_rot(out30), n_rot(late), n_rot(gtd60)

    zero = (0.0, 0.0, 0.0, 0.0)
    recs_out = []
    for gid in gids:
        a = s30.get(gid, zero)
        w = swc.get(gid, zero)
        recs_out.append(
            (
                gid,
                (a[1] - a[0]) / VALUE_SCALE,
                (a[3] - a[2]) / VALUE_SCALE,
                (w[1] - w[0]) / VALUE_SCALE,
                (w[3] - w[2]) / VALUE_SCALE,
                n30.get(gid, 0),
                nlate.get(gid, 0),
                ngtd.get(gid, 0),
            )
        )
    return pl.DataFrame(
        recs_out,
        schema={
            "game_id": pl.Utf8,
            "d_out30": pl.Float64,
            "d_doubt30": pl.Float64,
            "d_out_wc": pl.Float64,
            "d_doubt_wc": pl.Float64,
            "n_rot_out30": pl.Int64,
            "n_rot_late_add": pl.Int64,
            "n_rot_gtd": pl.Int64,
        },
        orient="row",
    )


def t30_config_from(base: InjuryFeatureConfig, lead_t30: int = 30, window: int = 10) -> T30Config:
    return T30Config(base=replace(base), lead_minutes_t30=lead_t30, window_games=window)
