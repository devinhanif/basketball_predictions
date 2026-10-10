"""Model-family bake-off for game win probability on top of the injury-Elo signal.

Everything here is a pure function of (games, box, report rows) frames or a small
estimator with the 3-method contract (``predict`` / ``get_metrics`` / ``get_config``).
The walk-forward harness, the pre-registered decision rule and the CLI live in
:mod:`research.eval.winprob_family_eval`; the rule is also restated in
``docs/WINPROB_FAMILY.md``.

As-of discipline (same convention as ``research/features/player_possession_features.py``):
every feature of a game uses only games dated strictly BEFORE its ``game_date``
(state is updated after a whole date has been read, so same-day games never see each
other) or schedule / pre-tip report facts that are known in advance. The injury
signals reuse the pre-tip report rule of :mod:`nba.models.injury_elo` (latest report
at least 60 minutes before the 19:00 ET tip proxy). Season 2025 is never loaded.

Feature conventions
-------------------
* ``offset``      logit of the production injury-Elo win probability (home).
* ``d_out`` / ``d_doubt``   away-minus-home value OUT / DOUBTFUL (positive favors home).
* ``q_diff``      away-minus-home value QUESTIONABLE (positive favors home).
* ``ret_diff``    home-minus-away value RETURNING (OUT in the team's previous game,
                  not OUT now); positive favors home.
* ``star_out_h/a``  1 if a >=30 mpg (as-of) player is OUT.
* ``rest_*``      days since last game capped at 7; ``b2b``; ``t4`` (3rd game in 4 days);
                  ``travel7_*`` thousand miles travelled over the last 7 days incl. this trip.
* ``ortg/drtg/pace``  possession-based (``FGA + 0.44 FTA + TOV - OREB``), played-only,
                  decayed, shrunk, expressed as deviation from the as-of league mean.
* ``form_*``      last-10 mean of (signed margin - 6.2 * signed injury-Elo logit): the 6.2
                  points-per-logit constant is the textbook NBA Elo conversion, not fit.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
from collections import deque
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.stats import poisson, skellam

from nba.features.game_context import TEAM_CONFERENCE
from nba.ingest.arenas import arena_distance_miles
from nba.models.base import RungModelBase
from nba.models.injury_elo import (
    VALUE_SCALE,
    InjuryFeatureConfig,
    asof_values,
    build_injury_features,
    build_value_state,
    league_rate_by_date,
    sigmoid,
)
from research.sim.usage_redistribution import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
    rotation_flagged_by_team,
)

# lightgbm and torch each ship an OpenMP runtime; loading both with >1 thread deadlocks on macOS
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

FAMILY_SEED = 20261008
MAX_SEASON = 2024  # season 2025 is never used for training or selection
MARGIN_PER_LOGIT = 6.2  # points of margin per logit unit (textbook NBA Elo convention)
STAR_MINUTES = 30.0
SCORE_SCALE = 100.0

#: Per-team columns joined from the feature builders (everything except ``offset``).
BASE_FEATURES: list[str] = [
    "glm_margin",
    "d_out",
    "d_doubt",
    "q_diff",
    "ret_diff",
    "star_out_h",
    "star_out_a",
    "rest_h",
    "rest_a",
    "b2b_h",
    "b2b_a",
    "t4_h",
    "t4_a",
    "travel7_h",
    "travel7_a",
    "tz_shift",
    "altitude_diff_km",
    "home_altitude_km",
    "is_national_tv",
    "wp_h",
    "wp_a",
    "gp_min",
    "tank_h",
    "tank_a",
    "race_h",
    "race_a",
    "ortg_h",
    "drtg_h",
    "ortg_a",
    "drtg_a",
    "pace_avg",
    "tpar_h",
    "tpar_a",
    "ftr_h",
    "ftr_a",
    "form_h",
    "form_a",
    "days_into_season",
    "is_playoff",
]
ALL_FEATURES: list[str] = [*BASE_FEATURES, "offset"]


# --------------------------------------------------------------------------
# As-of feature builders (pure frames in, one row per game out)
# --------------------------------------------------------------------------


def _sorted(games: pl.DataFrame) -> pl.DataFrame:
    return games.sort(["game_date", "game_id"])


def schedule_features(games: pl.DataFrame) -> pl.DataFrame:
    """Rest, back-to-back, 3-in-4 and last-7-days travel for both sides of every game.

    ``games``: game_id, game_date, home_team, away_team. Uses only the team's earlier
    games plus the (known-in-advance) venue of this game; the first game of a team has
    rest 7 and zero travel.
    """
    last_date: dict[int, dt.date] = {}
    last_venue: dict[int, int] = {}
    hist: dict[int, deque[tuple[dt.date, float]]] = {}
    prior_dates: dict[int, deque[dt.date]] = {}
    out: list[dict[str, Any]] = []
    for gid, d, h, a in (
        _sorted(games).select(["game_id", "game_date", "home_team", "away_team"]).iter_rows()
    ):
        rec: dict[str, Any] = {"game_id": gid}
        for tag, team in (("h", int(h)), ("a", int(a))):
            ld = last_date.get(team)
            rest = 7.0 if ld is None else float(min((d - ld).days, 7))
            leg = 0.0
            if team in last_venue:
                try:
                    leg = arena_distance_miles(last_venue[team], int(h))
                except KeyError:
                    leg = 0.0
            dq = hist.setdefault(team, deque())
            while dq and (d - dq[0][0]).days >= 7:
                dq.popleft()
            pd_ = prior_dates.setdefault(team, deque())
            while pd_ and (d - pd_[0]).days >= 4:
                pd_.popleft()
            rec[f"rest_{tag}"] = rest
            rec[f"b2b_{tag}"] = float(rest <= 1.0)
            rec[f"t4_{tag}"] = float(len(pd_) >= 2)
            rec[f"travel7_{tag}"] = (sum(m for _, m in dq) + leg) / 1000.0
            dq.append((d, leg))
            pd_.append(d)
            last_date[team] = d
            last_venue[team] = int(h)
        out.append(rec)
    return pl.DataFrame(out)


def standings_features(
    games: pl.DataFrame, tank_games: int = 50, tank_wp: float = 0.40
) -> pl.DataFrame:
    """Season-to-date shrunk win%, games played, tank flag and in-race flag (as-of).

    Standings reset every season. Conference rank counts teams of the same conference
    with a strictly higher win% from games before the date; ``race`` = rank <= 10 (or
    fewer than 20 games played). ``tank`` = >= ``tank_games`` played and win% below
    ``tank_wp``.
    """
    wins: dict[tuple[int, int], float] = {}
    played: dict[tuple[int, int], float] = {}
    out: list[dict[str, Any]] = []
    g = _sorted(games)
    for _, day in g.group_by("game_date", maintain_order=True):
        rows = list(
            day.select(
                ["game_id", "season", "home_team", "away_team", "home_pts", "away_pts"]
            ).iter_rows()
        )
        for gid, season, h, a, _hp, _ap in rows:
            rec: dict[str, Any] = {"game_id": gid}
            for tag, team in (("h", int(h)), ("a", int(a))):
                w = wins.get((season, team), 0.0)
                n = played.get((season, team), 0.0)
                raw = w / n if n > 0 else 0.5
                conf = TEAM_CONFERENCE.get(team, "?")
                rank = 1 + sum(
                    1
                    for (s2, t2), n2 in played.items()
                    if s2 == season
                    and t2 != team
                    and n2 > 0
                    and TEAM_CONFERENCE.get(t2, "?") == conf
                    and wins.get((s2, t2), 0.0) / n2 > raw
                )
                rec[f"wp_{tag}"] = (w + 2.5) / (n + 5.0)
                rec[f"gp_{tag}"] = n
                rec[f"tank_{tag}"] = float(n >= tank_games and raw < tank_wp)
                rec[f"race_{tag}"] = float(n < 20 or rank <= 10)
            rec["gp_min"] = min(rec["gp_h"], rec["gp_a"])
            del rec["gp_h"], rec["gp_a"]
            out.append(rec)
        for _gid, season, h, a, hp, ap in rows:
            if hp is None or ap is None:
                continue
            for team, won in ((int(h), hp > ap), (int(a), ap > hp)):
                wins[(season, team)] = wins.get((season, team), 0.0) + float(won)
                played[(season, team)] = played.get((season, team), 0.0) + 1.0
    return pl.DataFrame(out)


def team_box_totals(player_box: pl.DataFrame) -> pl.DataFrame:
    """Played-only team totals per (game_id, team_id): fga, fg3a, fta, tov, oreb, minutes."""
    return (
        player_box.filter(pl.col("minutes") > 0)
        .group_by(["game_id", "team_id"])
        .agg(
            pl.col("fga").sum().alias("fga"),
            pl.col("fg3a").sum().alias("fg3a"),
            pl.col("fta").sum().alias("fta"),
            pl.col("tov").sum().alias("tov"),
            pl.col("oreb").sum().alias("oreb"),
            pl.col("minutes").sum().alias("minutes"),
        )
    )


#: Team-rate columns that drift across seasons (the ``rel`` variant removes the league mean).
DRIFT_PAIRS: list[tuple[str, str]] = [
    ("ortg_h", "lg_ortg"),
    ("drtg_h", "lg_ortg"),
    ("ortg_a", "lg_ortg"),
    ("drtg_a", "lg_ortg"),
    ("pace_avg", "lg_pace"),
    ("tpar_h", "lg_tpar"),
    ("tpar_a", "lg_tpar"),
    ("ftr_h", "lg_ftr"),
    ("ftr_a", "lg_ftr"),
]
LEAGUE_MIN_ENTRIES = 20  # team-game entries (10 games) before the in-season mean is trusted


def apply_drift_variant(df: pl.DataFrame, relative: bool) -> pl.DataFrame:
    """``raw``: absolute team levels. ``relative``: minus the season's as-of league mean.

    The league mean is built strictly from games before the date within the season
    (prior season's mean until 10 league games exist); see :func:`efficiency_features`.
    ``form_*`` is already relative to the Elo expectation, so it is identical in both.
    """
    if not relative:
        return df
    return df.with_columns([(pl.col(c) - pl.col(lg)).alias(c) for c, lg in DRIFT_PAIRS])


def efficiency_features(
    games: pl.DataFrame,
    team_box: pl.DataFrame,
    halflife: float = 15.0,
    prior_k: float = 4.0,
) -> pl.DataFrame:
    """As-of absolute team levels: ORtg, DRtg, pace, 3PA rate, FTA rate (+ league means).

    Possessions per team-game = ``FGA + 0.44 FTA + TOV - OREB`` (played-only box
    rows); game possessions = mean of the two teams; ORtg = 100 pts / poss, DRtg =
    100 opp pts / poss, pace = poss * 48 / (team minutes / 5), 3PA rate = 3PA / FGA,
    FTA rate = FTA / FGA (own offense). Each team level is exponentially decayed over
    its own earlier games (``halflife`` games) and shrunk to the cumulative league
    mean with ``prior_k`` pseudo-games. ``lg_*`` columns are the season's as-of league
    mean (games strictly before the date in the same season; the most recent earlier
    season's mean until ``LEAGUE_MIN_ENTRIES`` entries exist; cumulative mean as last
    resort). All state is updated only after a whole date has been read.
    """
    decay = 0.5 ** (1.0 / halflife)
    tb = {
        (str(r[0]), int(r[1])): r[2:]
        for r in team_box.select(
            ["game_id", "team_id", "fga", "fg3a", "fta", "tov", "oreb", "minutes"]
        ).rows()
    }
    nm = 4  # metrics: ortg, pace, tpar, ftr
    team: dict[int, list[float]] = {}  # team -> [s_o, s_d, s_p, s_t, s_f, w]
    cum = [0.0] * (nm + 1)
    by_season: dict[int, list[float]] = {}

    def lg_mean(sums: list[float]) -> list[float]:
        return [sums[i] / sums[nm] for i in range(nm)]

    def season_league(season: int) -> list[float]:
        cur = by_season.get(season)
        if cur is not None and cur[nm] >= LEAGUE_MIN_ENTRIES:
            return lg_mean(cur)
        earlier = [s for s in by_season if s < season and by_season[s][nm] > 0]
        if earlier:
            return lg_mean(by_season[max(earlier)])
        return lg_mean(cum) if cum[nm] > 0 else [0.0] * nm

    def level(t: int, lg: list[float]) -> list[float]:
        s = team.get(t, [0.0] * 6)
        den = s[5] + prior_k
        return [
            (s[0] + prior_k * lg[0]) / den,
            (s[1] + prior_k * lg[0]) / den,
            (s[2] + prior_k * lg[1]) / den,
            (s[3] + prior_k * lg[2]) / den,
            (s[4] + prior_k * lg[3]) / den,
        ]

    out: list[dict[str, Any]] = []
    for _, day in _sorted(games).group_by("game_date", maintain_order=True):
        rows = list(
            day.select(
                ["game_id", "season", "home_team", "away_team", "home_pts", "away_pts"]
            ).iter_rows()
        )
        for gid, season, h, a, _hp, _ap in rows:
            cl = lg_mean(cum) if cum[nm] > 0 else [0.0] * nm
            lh, la = level(int(h), cl), level(int(a), cl)
            sl = season_league(int(season))
            out.append(
                {
                    "game_id": gid,
                    "ortg_h": lh[0],
                    "drtg_h": lh[1],
                    "ortg_a": la[0],
                    "drtg_a": la[1],
                    "pace_avg": 0.5 * (lh[2] + la[2]),
                    "tpar_h": lh[3],
                    "tpar_a": la[3],
                    "ftr_h": lh[4],
                    "ftr_a": la[4],
                    "lg_ortg": sl[0],
                    "lg_pace": sl[1],
                    "lg_tpar": sl[2],
                    "lg_ftr": sl[3],
                }
            )
        upd: list[tuple[int, int, float, float, float, float, float]] = []
        for gid, season, h, a, hp, ap in rows:
            bh, ba = tb.get((str(gid), int(h))), tb.get((str(gid), int(a)))
            if hp is None or ap is None or bh is None or ba is None:
                continue
            poss = 0.5 * (
                (bh[0] + 0.44 * bh[2] + bh[3] - bh[4]) + (ba[0] + 0.44 * ba[2] + ba[3] - ba[4])
            )
            mins = 0.5 * (bh[5] + ba[5])
            if poss <= 0 or mins <= 0 or bh[0] <= 0 or ba[0] <= 0:
                continue
            pace = poss * 48.0 / (mins / 5.0)
            upd.append(
                (
                    int(season),
                    int(h),
                    100.0 * hp / poss,
                    100.0 * ap / poss,
                    pace,
                    bh[1] / bh[0],
                    bh[2] / bh[0],
                )
            )
            upd.append(
                (
                    int(season),
                    int(a),
                    100.0 * ap / poss,
                    100.0 * hp / poss,
                    pace,
                    ba[1] / ba[0],
                    ba[2] / ba[0],
                )
            )
        for season, t, o, d, p, tp, fr in upd:
            s = team.setdefault(t, [0.0] * 6)
            for i, v in enumerate((o, d, p, tp, fr, 1.0)):
                s[i] = s[i] * decay + v
            ss = by_season.setdefault(season, [0.0] * (nm + 1))
            for sums in (cum, ss):
                sums[0] += o
                sums[1] += p
                sums[2] += tp
                sums[3] += fr
                sums[4] += 1.0
    return pl.DataFrame(out)


def form_features(games: pl.DataFrame, p_home: dict[str, float], n: int = 10) -> pl.DataFrame:
    """Last-``n`` mean of (signed margin - 6.2 * signed injury-Elo logit) per side, shrunk.

    ``p_home``: game_id -> out-of-fold injury-Elo probability. A game contributes only
    after its date has been fully read; games without a probability are skipped.
    Shrunk by ``m / (m + 5)`` for m games of history.
    """
    hist: dict[int, deque[float]] = {}
    out: list[dict[str, Any]] = []
    for _, day in _sorted(games).group_by("game_date", maintain_order=True):
        rows = list(
            day.select(["game_id", "home_team", "away_team", "home_pts", "away_pts"]).iter_rows()
        )
        for gid, h, a, _hp, _ap in rows:
            rec: dict[str, Any] = {"game_id": gid}
            for tag, team in (("h", int(h)), ("a", int(a))):
                dq = hist.get(team)
                m = len(dq) if dq else 0
                rec[f"form_{tag}"] = (sum(dq) / m) * (m / (m + 5.0)) if dq and m else 0.0
            out.append(rec)
        for gid, h, a, hp, ap in rows:
            p = p_home.get(str(gid))
            if p is None or hp is None or ap is None:
                continue
            pp = min(max(p, 1e-6), 1 - 1e-6)
            resid = float(hp - ap) - MARGIN_PER_LOGIT * math.log(pp / (1 - pp))
            hist.setdefault(int(h), deque(maxlen=n)).append(resid)
            hist.setdefault(int(a), deque(maxlen=n)).append(-resid)
    return pl.DataFrame(out)


def status_features(
    con: duckdb.DuckDBPyConnection,
    games: pl.DataFrame,
    stats: pl.DataFrame,
    icfg: InjuryFeatureConfig,
    report_rows: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Injury-report features per game (pre-tip report only; values as of prior games).

    Returns ``d_out, d_doubt, n_rot_out, has_report`` (identical to injury-Elo's) plus
    ``q_diff`` (QUESTIONABLE value, away-minus-home), ``ret_diff`` (RETURNING value,
    home-minus-away) and ``star_out_h/a``.
    """
    base = build_injury_features(
        con, games, stats, icfg, report_rows=report_rows, with_oracle=False
    ).select(["game_id", "d_out", "d_doubt", "n_rot_out", "has_report"])
    gids = games["game_id"].to_list()
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    cfg_out = ReportTriggerConfig(
        statuses=("out",),
        tipoff_hour_et=icfg.tipoff_hour_et,
        lead_minutes=icfg.lead_minutes,
        table=icfg.report_table,
    )
    rows = load_report_rows(con, cfg_out, game_ids=gids) if report_rows is None else report_rows
    out_flag, _ = latest_pretip_flagged(rows, cfg_out)
    q_flag, _ = latest_pretip_flagged(
        rows,
        ReportTriggerConfig(
            statuses=("questionable",),
            tipoff_hour_et=icfg.tipoff_hour_et,
            lead_minutes=icfg.lead_minutes,
            table=icfg.report_table,
        ),
    )
    state = build_value_state(stats, icfg.value)
    league = league_rate_by_date(stats)

    def _vals(flag: dict[str, set[int]]) -> pl.DataFrame:
        recs = [(g, p, info[g][0]) for g, ps in flag.items() for p in ps if g in info]
        if not recs:
            return pl.DataFrame(
                schema={
                    "game_id": pl.Utf8,
                    "player_id": pl.Int64,
                    "game_date": pl.Date,
                    "team_id": pl.Int64,
                    "n_after": pl.Int64,
                    "value": pl.Float64,
                }
            )
        pairs = pl.DataFrame(
            recs,
            schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date},
            orient="row",
        )
        return asof_values(pairs, state, league, icfg.value)

    def _sides(vals: pl.DataFrame) -> dict[str, list[float]]:
        acc: dict[str, list[float]] = {}
        for gid, team, v in vals.select(["game_id", "team_id", "value"]).iter_rows():
            _d, h, a = info[str(gid)]
            if team is None or int(team) not in (h, a):
                continue
            slot = acc.setdefault(str(gid), [0.0, 0.0])
            slot[0 if int(team) == h else 1] += float(v)
        return acc

    q_side = _sides(_vals(q_flag))

    # OUT players keyed by (game, team) -> RETURNING = out in team's previous game, not out now
    out_vals = _vals(out_flag)
    out_by: dict[tuple[str, int], set[int]] = {}
    for gid, pid, team in out_vals.select(["game_id", "player_id", "team_id"]).iter_rows():
        if team is None:
            continue
        out_by.setdefault((str(gid), int(team)), set()).add(int(pid))
    prev_game: dict[tuple[str, int], str] = {}
    last: dict[int, str] = {}
    for gid, _d, h, a in (
        _sorted(games).select(["game_id", "game_date", "home_team", "away_team"]).iter_rows()
    ):
        for team in (int(h), int(a)):
            if team in last:
                prev_game[(str(gid), team)] = last[team]
            last[team] = str(gid)
    ret_recs: list[tuple[str, int, dt.date]] = []
    ret_team: dict[tuple[str, int], int] = {}
    for (gid, team), prev in prev_game.items():
        pset = out_by.get((prev, team), set()) - out_by.get((gid, team), set())
        if gid not in out_flag:  # no usable report for this game: nothing to compare against
            continue
        for pid in pset - out_flag.get(gid, set()):
            ret_recs.append((gid, pid, info[gid][0]))
            ret_team[(gid, pid)] = team
    ret_side: dict[str, list[float]] = {}
    if ret_recs:
        rv = asof_values(
            pl.DataFrame(
                ret_recs,
                schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date},
                orient="row",
            ),
            state,
            league,
            icfg.value,
        )
        for gid, pid, v in rv.select(["game_id", "player_id", "value"]).iter_rows():
            _d, h, a = info[str(gid)]
            slot = ret_side.setdefault(str(gid), [0.0, 0.0])
            slot[0 if ret_team[(str(gid), int(pid))] == h else 1] += float(v)

    rot = rotation_flagged_by_team(
        out_flag,
        info,
        state.select(["player_id", "team_id", "game_date", "avg_min_after", "n_after"]),
        STAR_MINUTES,
        10,
    )
    recs_out = []
    for gid in gids:
        _d, h, a = info[gid]
        qh, qa = q_side.get(gid, [0.0, 0.0])
        rh, ra = ret_side.get(gid, [0.0, 0.0])
        recs_out.append(
            {
                "game_id": gid,
                "q_diff": (qa - qh) / VALUE_SCALE,
                "ret_diff": (rh - ra) / VALUE_SCALE,
                "star_out_h": float(len(rot.get((gid, h), ())) > 0),
                "star_out_a": float(len(rot.get((gid, a), ())) > 0),
            }
        )
    return base.join(pl.DataFrame(recs_out), on="game_id", how="left")


# --------------------------------------------------------------------------
# Home/away swap augmentation and leak guards
# --------------------------------------------------------------------------

_SWAP_PAIRS: list[tuple[str, str]] = [
    ("star_out_h", "star_out_a"),
    ("rest_h", "rest_a"),
    ("b2b_h", "b2b_a"),
    ("t4_h", "t4_a"),
    ("travel7_h", "travel7_a"),
    ("wp_h", "wp_a"),
    ("tank_h", "tank_a"),
    ("race_h", "race_a"),
    ("ortg_h", "ortg_a"),
    ("drtg_h", "drtg_a"),
    ("tpar_h", "tpar_a"),
    ("ftr_h", "ftr_a"),
    ("form_h", "form_a"),
]
_NEGATE = [
    "d_out",
    "d_doubt",
    "q_diff",
    "ret_diff",
    "tz_shift",
    "altitude_diff_km",
    "glm_margin",
    "offset",
]


def mirror_frame(df: pl.DataFrame) -> pl.DataFrame:
    """The same games with teams swapped: paired columns exchanged, home-positive
    differentials and the injury-Elo logit negated, ``home_flag`` set to 0.

    Venue-only columns (``home_altitude_km``, TV, playoff flag, date) are unchanged; the
    ``home_flag`` feature lets a model learn the home-court term that the mirror removes.
    """
    exprs: list[pl.Expr] = []
    for h, a in _SWAP_PAIRS:
        exprs += [pl.col(a).alias(h), pl.col(h).alias(a)]
    exprs += [(-pl.col(c)).alias(c) for c in _NEGATE if c in df.columns]
    exprs.append(pl.lit(0.0).alias("home_flag"))
    return df.with_columns(exprs)


def augment_swap(df: pl.DataFrame, y: np.ndarray) -> tuple[pl.DataFrame, np.ndarray]:
    """Original games (home_flag 1) followed by their mirrored copies with label 1 - y."""
    base = df.with_columns(pl.lit(1.0).alias("home_flag"))
    return pl.concat([base, mirror_frame(base)]), np.concatenate([y, 1.0 - y])


ALLOWED_FEATURES: frozenset[str] = frozenset([*BASE_FEATURES, "offset", "home_flag"])
_FORBIDDEN = re.compile(
    r"(home_pts|away_pts|^margin|^total|^y$|score|final|box|post|actual|result|^p0$|^p_)"
)


class LeakError(RuntimeError):
    """A feature that is not on the pre-tip allow-list reached a model."""


def assert_allowed_features(cols: list[str]) -> None:
    """Static allow-list check: every model input must be a named pre-tip feature."""
    bad = [c for c in cols if c not in ALLOWED_FEATURES or _FORBIDDEN.search(c)]
    if bad:
        raise LeakError(f"features not on the pre-tip allow-list: {bad}")


def check_importance_leak(gain: dict[str, float], top: int = 10) -> None:
    """Fail loudly if a non-allow-listed / target-derived name is among the top-``top`` gains."""
    ranked = sorted(gain, key=lambda k: gain[k], reverse=True)[:top]
    bad = [c for c in ranked if c not in ALLOWED_FEATURES or _FORBIDDEN.search(c)]
    if bad:
        raise LeakError(f"target-derived feature(s) in top-{top} importance: {bad}")


def glm_strength_features(
    games: pl.DataFrame, halflife_days: float = 120.0, ridge: float = 11.0
) -> pl.DataFrame:
    """As-of GLM team strength: ``margin ~ home + (team_home - team_away)``, ridge, monthly refit.

    For every calendar month the model is refit on games dated strictly before the month's
    first day, recency-weighted ``0.5 ** (age_days / halflife_days)`` (this decay is also
    the cross-season carryover). ``ridge`` is the prior precision on team strength
    (margin noise var ~180 over strength var ~16 -> ~11). Output: ``game_id, glm_margin``
    = predicted home margin (home-court coefficient + strength difference).
    """
    g = _sorted(games.filter(pl.col("home_pts").is_not_null()))
    allg = _sorted(games)
    teams = sorted(set(allg["home_team"].to_list()) | set(allg["away_team"].to_list()))
    tix = {t: i for i, t in enumerate(teams)}
    gd = g["game_date"].to_list()
    gh = np.array([tix[int(t)] for t in g["home_team"].to_list()])
    ga = np.array([tix[int(t)] for t in g["away_team"].to_list()])
    gm = (g["home_pts"] - g["away_pts"]).to_numpy().astype(float)
    out_id: list[str] = []
    out_v: list[float] = []
    cache: dict[tuple[int, int], np.ndarray] = {}
    for gid, d, h, a in allg.select(["game_id", "game_date", "home_team", "away_team"]).iter_rows():
        key = (d.year, d.month)
        if key not in cache:
            start = dt.date(d.year, d.month, 1)
            m = np.array([x < start for x in gd], dtype=bool)
            k = len(teams)
            if m.sum() < 30:
                cache[key] = np.zeros(k + 1)
            else:
                age = np.array([(start - x).days for x, mm in zip(gd, m, strict=True) if mm], float)
                w = 0.5 ** (age / halflife_days)
                x = np.zeros((int(m.sum()), k + 1))
                r = np.arange(int(m.sum()))
                x[r, 0] = 1.0
                x[r, 1 + gh[m]] += 1.0
                x[r, 1 + ga[m]] -= 1.0
                pen = np.diag([0.0] + [ridge] * k)
                xtw = x.T * w
                cache[key] = np.linalg.solve(xtw @ x + pen, xtw @ gm[m])
        beta = cache[key]
        out_id.append(str(gid))
        out_v.append(float(beta[0] + beta[1 + tix[int(h)]] - beta[1 + tix[int(a)]]))
    return pl.DataFrame({"game_id": out_id, "glm_margin": out_v})


# --------------------------------------------------------------------------
# Estimators
# --------------------------------------------------------------------------


def _clip(p: Any) -> np.ndarray:
    return np.asarray(np.clip(p, 0.02, 0.98), dtype=float)


def _logit(p: np.ndarray) -> np.ndarray:
    pp = np.clip(p, 1e-6, 1 - 1e-6)
    return np.asarray(np.log(pp / (1 - pp)), dtype=float)


def fit_offset_logistic(
    x: np.ndarray, offset: np.ndarray, y: np.ndarray, lam: float
) -> tuple[np.ndarray, float]:
    """L2 logistic regression ``z = offset + b + x w`` (``b`` unpenalised). Returns (w, b)."""
    n, k = x.shape

    def fun(theta: np.ndarray) -> tuple[float, np.ndarray]:
        w, b = theta[:k], theta[k]
        z = offset + b + x @ w
        nll = float(np.sum(np.logaddexp(0.0, z) - y * z) + 0.5 * lam * float(w @ w))
        r = sigmoid(z) - y
        return nll, np.concatenate([x.T @ r + lam * w, [r.sum()]])

    res = minimize(fun, np.zeros(k + 1), jac=True, method="L-BFGS-B")
    return res.x[:k], float(res.x[k])


class _Arm(RungModelBase):
    """Common bookkeeping. ``fit`` consumes a frame with ``y`` and the feature columns."""

    name = "arm"

    def __init__(self, seed: int = FAMILY_SEED) -> None:
        super().__init__(seed=seed)

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:  # pragma: no cover
        raise NotImplementedError

    def predict(self, df: pl.DataFrame) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def get_config(self) -> dict[str, Any]:
        return {"model_name": self.name, "seed": self.seed}


class OffsetLogistic(_Arm):
    """L2 logistic regression; ``use_offset`` pins the injury-Elo logit with coefficient 1.

    Without the offset the logit is *not* a feature either (arm 1b), so it must find team
    strength from as-of win%, ORtg/DRtg and form alone.
    """

    def __init__(
        self,
        use_offset: bool,
        lam: float,
        cols: list[str] | None = None,
        seed: int = FAMILY_SEED,
        swap: bool = False,
        name: str | None = None,
    ) -> None:
        super().__init__(seed)
        self.use_offset = use_offset
        self.lam = lam
        self.swap = swap
        self.cols = list(cols) if cols is not None else list(BASE_FEATURES)
        if swap:
            self.cols.append("home_flag")
        assert_allowed_features(self.cols)
        self.name = name or ("logit_offset" if use_offset else "logit_nooffset")
        self.mu_: np.ndarray = np.zeros(len(self.cols))
        self.sd_: np.ndarray = np.ones(len(self.cols))
        self.w_: np.ndarray = np.zeros(len(self.cols))
        self.b_ = 0.0

    def _x(self, df: pl.DataFrame) -> np.ndarray:
        return np.asarray(
            (df.select(self.cols).to_numpy().astype(float) - self.mu_) / self.sd_, dtype=float
        )

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        if self.swap:
            train_df, y = augment_swap(train_df, y)
        raw = train_df.select(self.cols).to_numpy().astype(float)
        self.mu_ = raw.mean(axis=0)
        self.sd_ = np.where(raw.std(axis=0) > 1e-9, raw.std(axis=0), 1.0)
        off = train_df["offset"].to_numpy() if self.use_offset else np.zeros(len(y))
        self.w_, self.b_ = fit_offset_logistic(self._x(train_df), off, y.astype(float), self.lam)

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.swap:
            df = df.with_columns(pl.lit(1.0).alias("home_flag"))
        off = df["offset"].to_numpy() if self.use_offset else 0.0
        return _clip(sigmoid(off + self.b_ + self._x(df) @ self.w_))

    def get_config(self) -> dict[str, Any]:
        return {
            **super().get_config(),
            "use_offset": self.use_offset,
            "lam": self.lam,
            "n_features": len(self.cols),
            "w": [float(v) for v in self.w_],
            "b": self.b_,
        }


class GbmArm(_Arm):
    """LightGBM binary classifier with small trees and early stopping on the latest prior months.

    ``mode``: ``noff`` (injury-Elo logit absent), ``feat`` (logit as a feature),
    ``init`` (logit as feature AND ``init_score`` so trees learn corrections). XGBoost is
    not installed in this repo; LightGBM (already a project dependency) is the substitute.
    """

    def __init__(
        self, mode: str, seed: int = FAMILY_SEED, val_frac: float = 0.15, swap: bool = False
    ) -> None:
        super().__init__(seed)
        assert mode in ("noff", "feat", "init")
        self.mode = mode
        self.val_frac = val_frac
        self.swap = swap
        self.name = f"gbm_{mode}" + ("_sw" if swap else "")
        self.cols = list(BASE_FEATURES if mode == "noff" else ALL_FEATURES)
        if swap:
            self.cols.append("home_flag")
        assert_allowed_features(self.cols)
        self.gain_: dict[str, float] = {}
        self.model: Any = None
        self.best_iter_ = 0
        self.params: dict[str, Any] = {
            "objective": "binary",
            "learning_rate": 0.03,
            "num_leaves": 4,
            "max_depth": 3,
            "min_data_in_leaf": 50,
            "lambda_l2": 10.0,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "seed": seed,
            "num_threads": 1,
            "verbosity": -1,
            "deterministic": True,
        }

    def _init(self, df: pl.DataFrame) -> np.ndarray | None:
        return df["offset"].to_numpy().astype(float) if self.mode == "init" else None

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        import lightgbm as lgb

        n = train_df.height
        cut = int(n * (1 - self.val_frac))  # rows are date-ordered: validate on the latest rows
        parts = []
        for df_, y_ in ((train_df[:cut], y[:cut]), (train_df[cut:], y[cut:])):
            if self.swap:
                df_, y_ = augment_swap(df_, y_)
            init = self._init(df_)
            parts.append(
                lgb.Dataset(df_.select(self.cols).to_numpy().astype(float), y_, init_score=init)
            )
        self.model = lgb.train(
            self.params,
            parts[0],
            num_boost_round=400,
            valid_sets=[parts[1]],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        self.best_iter_ = int(self.model.best_iteration or 0)
        gain = self.model.feature_importance(importance_type="gain")
        self.gain_ = {c: float(g) for c, g in zip(self.cols, gain, strict=True)}
        check_importance_leak(self.gain_)

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.swap:
            df = df.with_columns(pl.lit(1.0).alias("home_flag"))
        x = df.select(self.cols).to_numpy().astype(float)
        raw = self.model.predict(x, raw_score=True, num_iteration=self.best_iter_ or None)
        init = self._init(df)
        z = np.asarray(raw, dtype=float) + (0.0 if init is None else init)
        return _clip(sigmoid(z))

    def get_config(self) -> dict[str, Any]:
        return {
            **super().get_config(),
            "mode": self.mode,
            "best_iter": self.best_iter_,
            "top_gain": sorted(self.gain_, key=lambda k: self.gain_[k], reverse=True)[:5],
            "params": {k: v for k, v in self.params.items() if k != "seed"},
        }


class MlpArm(_Arm):
    """Small MLP correction on the injury-Elo logit: ``z = offset + net(x)``.

    Last layer zero-initialised (starts exactly at injury-Elo), strong weight decay,
    dropout, fixed epochs (no tuning), mean of ``n_seeds`` nets in logit space.
    """

    name = "mlp_offset"

    def __init__(
        self,
        hidden: int = 16,
        epochs: int = 15,
        weight_decay: float = 5e-2,
        dropout: float = 0.2,
        n_seeds: int = 3,
        seed: int = FAMILY_SEED,
        loss: str = "bce",
        swap: bool = False,
    ) -> None:
        super().__init__(seed)
        assert loss in ("bce", "brier")
        self.hidden, self.epochs, self.wd = hidden, epochs, weight_decay
        self.dropout, self.n_seeds = dropout, n_seeds
        self.loss_kind, self.swap = loss, swap
        self.name = "mlp_offset" + ("_brier" if loss == "brier" else "") + ("_sw" if swap else "")
        self.cols = list(BASE_FEATURES) + (["home_flag"] if swap else [])
        assert_allowed_features(self.cols)
        self.mu_: np.ndarray = np.zeros(len(self.cols))
        self.sd_: np.ndarray = np.ones(len(self.cols))
        self.nets: list[Any] = []

    def _x(self, df: pl.DataFrame) -> np.ndarray:
        return np.asarray(
            (df.select(self.cols).to_numpy().astype(float) - self.mu_) / self.sd_, dtype=float
        )

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        import torch
        from torch import nn

        torch.set_num_threads(1)
        if self.swap:
            train_df, y = augment_swap(train_df, y)
        raw = train_df.select(self.cols).to_numpy().astype(float)
        self.mu_ = raw.mean(axis=0)
        self.sd_ = np.where(raw.std(axis=0) > 1e-9, raw.std(axis=0), 1.0)
        xt = torch.tensor(self._x(train_df), dtype=torch.float32)
        ot = torch.tensor(train_df["offset"].to_numpy(), dtype=torch.float32)
        yt = torch.tensor(y, dtype=torch.float32)
        self.nets = []
        for s in range(self.n_seeds):
            torch.manual_seed(self.seed + s)
            net = nn.Sequential(
                nn.Linear(len(self.cols), self.hidden),
                nn.ReLU(),
                nn.Dropout(self.dropout),
                nn.Linear(self.hidden, 1),
            )
            last: Any = net[3]
            nn.init.zeros_(last.weight)
            nn.init.zeros_(last.bias)
            opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=self.wd)
            g = torch.Generator().manual_seed(self.seed + s)
            for _ in range(self.epochs):
                perm = torch.randperm(len(yt), generator=g)
                net.train()
                for i in range(0, len(yt), 256):
                    b = perm[i : i + 256]
                    z = ot[b] + net(xt[b]).squeeze(1)
                    if self.loss_kind == "brier":
                        loss = ((torch.sigmoid(z) - yt[b]) ** 2).mean()
                    else:
                        loss = nn.functional.binary_cross_entropy_with_logits(z, yt[b])
                    opt.zero_grad()
                    loss.backward()  # type: ignore[no-untyped-call]
                    opt.step()
            net.eval()
            self.nets.append(net)

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        import torch

        if self.swap:
            df = df.with_columns(pl.lit(1.0).alias("home_flag"))
        xt = torch.tensor(self._x(df), dtype=torch.float32)
        with torch.no_grad():
            corr = np.mean([n(xt).squeeze(1).numpy() for n in self.nets], axis=0)
        return np.asarray(
            np.clip(sigmoid(df["offset"].to_numpy() + corr.astype(float)), 0.02, 0.98), dtype=float
        )

    def get_config(self) -> dict[str, Any]:
        return {
            **super().get_config(),
            "hidden": self.hidden,
            "epochs": self.epochs,
            "weight_decay": self.wd,
            "dropout": self.dropout,
            "n_seeds": self.n_seeds,
            "loss": self.loss_kind,
            "swap": self.swap,
        }


# ---- Poisson score model (scaled bivariate Poisson) ----------------------

#: Antisymmetric covariates (home-positive; enter +x/2 on the home row, -x/2 on the away row).
POISSON_ANTI = [
    "glm_margin",
    "d_out",
    "d_doubt",
    "q_diff",
    "ret_diff",
    "tz_shift",
    "altitude_diff_km",
]
#: Derived antisymmetric diffs built in ``_poisson_covariates``.
POISSON_SYM = ["pace_avg", "is_national_tv", "is_playoff"]


def _poisson_covariates(df: pl.DataFrame, use_offset: bool) -> tuple[np.ndarray, np.ndarray]:
    """(anti, sym) covariate matrices, columns standardised-by-construction scales."""
    a = df.with_columns(
        (pl.col("rest_h") - pl.col("rest_a")).alias("rest_diff"),
        (pl.col("b2b_a") - pl.col("b2b_h")).alias("b2b_diff"),
        (pl.col("t4_a") - pl.col("t4_h")).alias("t4_diff"),
        (pl.col("travel7_a") - pl.col("travel7_h")).alias("travel_diff"),
        (pl.col("star_out_a") - pl.col("star_out_h")).alias("star_diff"),
        (pl.col("form_h") - pl.col("form_a")).alias("form_diff"),
        (pl.col("wp_h") - pl.col("wp_a")).alias("wp_diff"),
        (pl.col("ortg_h") - pl.col("ortg_a")).alias("ortg_diff"),
        (pl.col("drtg_a") - pl.col("drtg_h")).alias("drtg_diff"),
        (pl.col("tank_a") - pl.col("tank_h")).alias("tank_diff"),
        (pl.col("race_h") - pl.col("race_a")).alias("race_diff"),
    )
    anti_cols = [
        *POISSON_ANTI,
        "rest_diff",
        "b2b_diff",
        "t4_diff",
        "travel_diff",
        "star_diff",
        "form_diff",
        "wp_diff",
        "ortg_diff",
        "drtg_diff",
        "tank_diff",
        "race_diff",
    ]
    anti = a.select(anti_cols).to_numpy().astype(float)
    if use_offset:
        anti = np.column_stack([anti, a["offset"].to_numpy()])
    sym = a.select(POISSON_SYM).to_numpy().astype(float)
    return anti, sym


class PoissonScoreArm(_Arm):
    """Dixon-Coles-style score model: one Poisson regression for both teams' points.

    ``log mu = intercept + attack[team] + defense[opponent] + home*is_home + covariates``
    on stacked (home row, away row) data, ridge-penalised with an N-scaled penalty
    (prior precision ``prior_precision`` per unit of a standardised column) and
    exponential recency weights. Win probability and the margin / total distributions
    come from a *scaled bivariate Poisson*: points = k * (Z1 + Z3), k * (Z2 + Z3) with
    independent Poissons, so the margin is k * Skellam and the total k * (Z1+Z2+2*Z3).
    ``k`` and the shared factor ``lam3`` are set by method of moments from the
    training residual margin / total variance (an independent-Poisson pair has far too
    much margin variance, so the dispersion has to be matched).
    """

    def __init__(
        self,
        use_offset: bool,
        teams: list[int],
        prior_precision: float = 625.0,
        halflife_days: float = 270.0,
        seed: int = FAMILY_SEED,
    ) -> None:
        super().__init__(seed)
        self.use_offset = use_offset
        self.teams = sorted(set(int(t) for t in teams))
        self.tix = {t: i for i, t in enumerate(self.teams)}
        self.prior_precision = prior_precision
        self.halflife = halflife_days
        self.name = "poisson_dc_offset" if use_offset else "poisson_dc"
        self.reg: Any = None
        self.k_ = 1.0
        self.lam3_ = 0.0
        self.mu_c_: np.ndarray | None = None
        self.sd_c_: np.ndarray | None = None
        self.var_m_ = 0.0
        self.var_t_ = 0.0

    def _matrix(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        n, t = df.height, len(self.teams)
        anti, sym = _poisson_covariates(df, self.use_offset)
        if self.mu_c_ is None:
            allc = np.vstack([np.column_stack([anti, sym]), np.column_stack([anti, sym])])
            self.mu_c_ = np.zeros(allc.shape[1])
            self.sd_c_ = np.where(allc.std(axis=0) > 1e-9, allc.std(axis=0), 1.0)
        assert self.mu_c_ is not None and self.sd_c_ is not None
        na = anti.shape[1]
        sd = self.sd_c_
        anti_s, sym_s = anti / sd[:na], sym / sd[na:]
        home = df["home_team"].to_numpy()
        away = df["away_team"].to_numpy()
        xs = []
        idx_h = np.array([self.tix.get(int(v), -1) for v in home])
        idx_a = np.array([self.tix.get(int(v), -1) for v in away])
        for own, opp, sign, is_home in ((idx_h, idx_a, 1.0, 1.0), (idx_a, idx_h, -1.0, 0.0)):
            m = np.zeros((n, 2 * t + 1 + na + sym_s.shape[1]))
            r = np.arange(n)
            ok = own >= 0
            m[r[ok], own[ok]] = 1.0
            ok = opp >= 0
            m[r[ok], t + opp[ok]] = 1.0
            m[:, 2 * t] = is_home
            m[:, 2 * t + 1 : 2 * t + 1 + na] = 0.5 * sign * anti_s
            m[:, 2 * t + 1 + na :] = sym_s
            xs.append(m)
        return xs[0], xs[1]

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        from sklearn.linear_model import PoissonRegressor

        self.mu_c_ = None
        xh, xa = self._matrix(train_df)
        n = train_df.height
        hp = train_df["home_pts"].to_numpy().astype(float)
        ap = train_df["away_pts"].to_numpy().astype(float)
        dates = train_df["game_date"].to_list()
        age = np.array([(dates[-1] - d).days for d in dates], dtype=float)
        w = 0.5 ** (age / self.halflife)
        w = w / w.mean()
        # points are fit on a /100 scale (well-conditioned for L-BFGS); alpha is rescaled so the
        # penalised objective is identical to the unscaled one
        self.reg = PoissonRegressor(
            alpha=self.prior_precision / (2.0 * n) / SCORE_SCALE, max_iter=3000, tol=1e-7
        )
        self.reg.fit(
            np.vstack([xh, xa]), np.concatenate([hp, ap]) / SCORE_SCALE, sample_weight=np.tile(w, 2)
        )
        mh = self.reg.predict(xh) * SCORE_SCALE
        ma = self.reg.predict(xa) * SCORE_SCALE
        self.var_m_ = float(np.var((hp - ap) - (mh - ma)))
        self.var_t_ = float(np.var((hp + ap) - (mh + ma)))
        m_tot = float(np.mean(mh + ma))
        self.k_ = float(np.clip((self.var_t_ + self.var_m_) / (2.0 * m_tot), 0.3, 3.0))
        self.lam3_ = float(max((self.var_t_ - self.var_m_) / (4.0 * self.k_**2), 0.0))
        self.y_unused_ = y

    def means(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        xh, xa = self._matrix(df)
        return self.reg.predict(xh) * SCORE_SCALE, self.reg.predict(xa) * SCORE_SCALE

    def lambdas(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        mh, ma = self.means(df)
        l1 = np.maximum(mh / self.k_ - self.lam3_, 0.5)
        l2 = np.maximum(ma / self.k_ - self.lam3_, 0.5)
        return l1, l2

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        l1, l2 = self.lambdas(df)
        p = skellam.sf(0, l1, l2) + 0.5 * skellam.pmf(0, l1, l2)
        return np.clip(np.asarray(p, dtype=float), 0.02, 0.98)

    def margin_total_pmfs(
        self, l1: float, l2: float
    ) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
        """((margin points, pmf), (total points, pmf)) for one game."""
        s = l1 + l2
        half = int(math.ceil(10.0 * math.sqrt(max(s, 1.0)))) + 5
        d = np.arange(-half, half + 1)
        pm = skellam.pmf(d, l1, l2)
        nmax = int(s + 10.0 * math.sqrt(max(s, 1.0)) + 10)
        ps = poisson.pmf(np.arange(nmax + 1), s)
        if self.lam3_ > 0:
            m3 = int(self.lam3_ + 10.0 * math.sqrt(self.lam3_) + 10)
            p3 = poisson.pmf(np.arange(m3 + 1), self.lam3_)
            p3x = np.zeros(2 * m3 + 1)
            p3x[::2] = p3
        else:
            p3x = np.array([1.0])
        pt = np.convolve(ps, p3x)
        t = np.arange(len(pt))
        return (self.k_ * d, pm / pm.sum()), (self.k_ * t, pt / pt.sum())

    def get_config(self) -> dict[str, Any]:
        return {
            **super().get_config(),
            "use_offset": self.use_offset,
            "prior_precision": self.prior_precision,
            "halflife_days": self.halflife,
            "k": self.k_,
            "lam3": self.lam3_,
            "var_margin": self.var_m_,
            "var_total": self.var_t_,
        }


def crps_discrete(x: np.ndarray, pmf: np.ndarray, y: float) -> float:
    """CRPS of a discrete forecast (sorted support ``x``, masses ``pmf``) at outcome ``y``.

    ``E|X - y| - 0.5 E|X - X'|`` with ``E|X - X'| = 2 sum_j F_j (1 - F_j) (x_{j+1} - x_j)``.
    """
    cdf = np.cumsum(pmf)
    e_xy = float(np.sum(pmf * np.abs(x - y)))
    e_xx = 2.0 * float(np.sum(cdf[:-1] * (1.0 - cdf[:-1]) * np.diff(x)))
    return e_xy - 0.5 * e_xx


def crps_normal(mu: np.ndarray, sd: float, y: np.ndarray) -> np.ndarray:
    """Closed-form CRPS of N(mu, sd^2) at y."""
    from scipy.stats import norm

    z = (y - mu) / sd
    return np.asarray(sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi)))


@dataclass(frozen=True)
class StackSpec:
    """Pre-registered stack inputs: injury-Elo plus every offset-based arm."""

    members: tuple[str, ...] = ("logit_offset", "gbm_init", "poisson_dc_offset", "mlp_offset")
    lam: float = 20.0
    min_rows: int = 600


class StackArm(_Arm):
    """``z = offset + sum_k w_k (logit_k - offset)`` with L2-penalised ``w`` (prior: w = 0)."""

    name = "stack"

    def __init__(self, spec: StackSpec | None = None, seed: int = FAMILY_SEED) -> None:
        super().__init__(seed)
        self.spec = spec or StackSpec()
        self.w_: np.ndarray = np.zeros(len(self.spec.members))
        self.b_ = 0.0

    def _x(self, df: pl.DataFrame) -> np.ndarray:
        off = df["offset"].to_numpy()
        return np.column_stack([_logit(df[f"p_{m}"].to_numpy()) - off for m in self.spec.members])

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        self.w_, self.b_ = fit_offset_logistic(
            self._x(train_df), train_df["offset"].to_numpy(), y.astype(float), self.spec.lam
        )

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        return _clip(sigmoid(df["offset"].to_numpy() + self.b_ + self._x(df) @ self.w_))

    def get_config(self) -> dict[str, Any]:
        return {
            **super().get_config(),
            "members": list(self.spec.members),
            "lam": self.spec.lam,
            "w": [float(v) for v in self.w_],
            "b": self.b_,
        }


# --------------------------------------------------------------------------
# Calibration layer (fit only on earlier out-of-fold predictions)
# --------------------------------------------------------------------------

CAL_METHODS: tuple[str, ...] = ("platt", "platt_slope", "platt_int", "beta", "iso")
#: (window_days, half_life_days) schemes; None = unbounded / unweighted.
CAL_WINDOWS: tuple[tuple[int | None, float | None], ...] = (
    (120, None),
    (300, None),
    (None, 120.0),
    (None, None),
)
CAL_PARAMS: dict[str, tuple[Any, ...]] = {
    "platt": (1.0, 33.0),  # prior precision on (a, b - 1)
    "platt_slope": (1.0, 33.0),
    "platt_int": (1.0, 33.0),
    "beta": (1.0, 33.0),
    "iso": ((100, 1.0), (250, 1.0), (100, 0.5), (250, 0.5)),  # (min bin size, iso weight)
}


def _fit_platt_like(
    feats: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float, prior: np.ndarray, base: np.ndarray
) -> np.ndarray:
    """Weighted logistic ``z = base + feats @ theta`` with ridge toward ``prior``."""

    def fun(th: np.ndarray) -> tuple[float, np.ndarray]:
        z = base + feats @ th
        nll = float(
            np.sum(w * (np.logaddexp(0.0, z) - y * z)) + 0.5 * lam * np.sum((th - prior) ** 2)
        )
        g = feats.T @ (w * (sigmoid(z) - y)) + lam * (th - prior)
        return nll, g

    return np.asarray(minimize(fun, prior.copy(), jac=True, method="L-BFGS-B").x, dtype=float)


@dataclass
class Calibrator:
    """A fitted map from raw win probability to calibrated probability."""

    method: str
    theta: np.ndarray
    xs: np.ndarray | None = None  # isotonic knots (probability scale)
    ys: np.ndarray | None = None
    iso_w: float = 1.0
    platt_theta: np.ndarray | None = None

    def __call__(self, p: np.ndarray) -> np.ndarray:
        z = _logit(p)
        if self.method == "identity":
            out = np.asarray(p, dtype=float)
        elif self.method == "platt":
            out = sigmoid(self.theta[0] + self.theta[1] * z)
        elif self.method == "platt_slope":
            out = sigmoid(self.theta[0] * z)
        elif self.method == "platt_int":
            out = sigmoid(z + self.theta[0])
        elif self.method == "beta":
            pp = np.clip(p, 1e-6, 1 - 1e-6)
            out = sigmoid(
                self.theta[2] + self.theta[0] * np.log(pp) - self.theta[1] * np.log(1 - pp)
            )
        elif self.method == "iso":
            assert self.xs is not None and self.ys is not None and self.platt_theta is not None
            iso = np.interp(p, self.xs, self.ys)
            pl_ = sigmoid(self.platt_theta[0] + self.platt_theta[1] * z)
            out = self.iso_w * iso + (1.0 - self.iso_w) * pl_
        else:  # pragma: no cover
            raise ValueError(self.method)
        return _clip(out)


def fit_calibrator(
    method: str, p: np.ndarray, y: np.ndarray, w: np.ndarray, param: Any
) -> Calibrator:
    """Fit one calibrator. ``param``: ridge precision (platt/beta) or (min_bin, iso_weight)."""
    z = _logit(p)
    yf = y.astype(float)
    if method == "platt":
        th = _fit_platt_like(
            np.column_stack([np.ones_like(z), z]),
            yf,
            w,
            float(param),
            np.array([0.0, 1.0]),
            np.zeros_like(z),
        )
        return Calibrator(method, th)
    if method == "platt_slope":
        th = _fit_platt_like(z[:, None], yf, w, float(param), np.array([1.0]), np.zeros_like(z))
        return Calibrator(method, th)
    if method == "platt_int":
        th = _fit_platt_like(np.ones((len(z), 1)), yf, w, float(param), np.array([0.0]), z)
        return Calibrator(method, th)
    if method == "beta":
        pp = np.clip(p, 1e-6, 1 - 1e-6)
        f = np.column_stack([np.log(pp), -np.log(1 - pp), np.ones_like(z)])
        th = _fit_platt_like(f, yf, w, float(param), np.array([1.0, 1.0, 0.0]), np.zeros_like(z))
        return Calibrator(method, th)
    if method == "iso":
        from sklearn.isotonic import IsotonicRegression

        min_bin, iso_w = int(param[0]), float(param[1])
        order = np.argsort(p)
        n_bins = max(1, len(p) // min_bin)
        chunks = np.array_split(order, n_bins)
        bx = np.array([np.average(p[c], weights=w[c]) for c in chunks])
        by = np.array([np.average(yf[c], weights=w[c]) for c in chunks])
        bw = np.array([w[c].sum() for c in chunks])
        iso = IsotonicRegression(y_min=0.02, y_max=0.98, out_of_bounds="clip")
        iso.fit(bx, by, sample_weight=bw)
        pth = _fit_platt_like(
            np.column_stack([np.ones_like(z), z]),
            yf,
            w,
            1.0,
            np.array([0.0, 1.0]),
            np.zeros_like(z),
        )
        return Calibrator(method, pth, xs=bx, ys=iso.predict(bx), iso_w=iso_w, platt_theta=pth)
    raise ValueError(method)


def calibration_weights(
    dates: list[dt.date], start: dt.date, window: int | None, half_life: float | None
) -> tuple[np.ndarray, np.ndarray]:
    """(keep mask, weights) for rows dated before ``start`` under a (window, half-life) scheme."""
    age = np.array([(start - d).days for d in dates], dtype=float)
    keep = age >= 1
    if window is not None:
        keep &= age <= window
    w = np.ones(len(dates)) if half_life is None else 0.5 ** (age / half_life)
    return keep, w


def ece_equal_mass(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error with equal-mass (quantile) bins."""
    order = np.argsort(p, kind="stable")
    n = len(p)
    return float(
        sum(
            len(c) / n * abs(float(np.mean(p[c])) - float(np.mean(y[c])))
            for c in np.array_split(order, n_bins)
            if len(c)
        )
    )
