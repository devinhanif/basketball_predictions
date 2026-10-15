# ruff: noqa: E501
"""REPLACEMENT_DELTA features (rule: docs/prereg/REPLACEMENT_DELTA.md; DRAFT until frozen).

When rotation teammates are OUT on the T-60 report, production counts what they vacated (``vac_min``,
``vac_<stat>``: sums of the OUT players' recency-weighted per-game numbers). This module weights each absent
teammate by how much better than a REPLACEMENT-LEVEL player he is, per possession, and hands that quality-weighted
vacancy to the remaining rotation in proportion to usage.

Everything is as-of: a player's rate at game g uses only his games strictly before g's date (all of them, across
seasons), shrunk toward the replacement rate with an empirical-Bayes weight; the replacement rate of season S uses only
seasons S-1 and S-2; tonight's box is never read (only the list of who has a row, which the callers do not pass).

Columns per stat s in {pts, reb, ast, fg3m} (None where there is no usable report):

* ``qv_s``  team quality-weighted vacancy: sum over OUT rotation players j of (rate_js - repl_s) x P_j  [stat units]
* ``rd_s``  qv_s x share_i, the scored player's usage share of the remaining pool (the v1 redistribution)
* ``v0_s``  the same sum WITHOUT subtracting the replacement rate (construction control, arm V0)
* ``v0d_s`` v0_s x share_i
* ``n_out`` the number of OUT rotation players (null with no usable report)
* ``share`` share_i itself (usage x expected possessions, over the pool minus the OUT set plus i if absent)

rate_js: shrunk per-possession rate, (S_js + k_s repl_s) / (Q_j + k_s); P_j: expected possessions tonight
(production's ``min10`` x the team's as-of possessions per minute).
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from typing import Any

import numpy as np
import polars as pl

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
RATE_STATS: tuple[str, ...] = (*STATS, "usage")
FRINGE_RANK = 9  # players ranked 10th or lower in their team-season by total minutes are the fringe
PRIOR_SEASONS = 2  # the replacement rate of season S pools seasons S-2 and S-1
POOL_WINDOW = 10  # redistribution pool: the team's previous 10 games ...
POOL_MIN_APP = 5  # ... >= 5 appearances ...
POOL_MEAN_MIN = (
    12.0  # ... a mean of >= 12 minutes over those appearances (same season, strictly earlier)
)
PACE_WINDOW = 10
PACE_MIN_GAMES = 3
MIN_Q_FOR_K = 200.0  # player-seasons with fewer possessions on the floor do not inform the prior
K_BOUNDS = (25.0, 20000.0)
COLUMNS: tuple[str, ...] = (
    "n_out",
    *(f"qv_{s}" for s in STATS),
    *(f"rd_{s}" for s in STATS),
    *(f"v0_{s}" for s in STATS),
    *(f"v0d_{s}" for s in STATS),
    "share",
)
BOX_COLS = (
    "game_id",
    "team_id",
    "player_id",
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "fga",
    "fta",
    "tov",
    "oreb",
    "game_date",
    "season",
)

# --------------------------------------------------------------------------- possessions


def prep_box(box: pl.DataFrame) -> pl.DataFrame:
    """Played rows with ``usage_n`` (fga + 0.44 fta + tov), the game's possessions ``poss_g`` (mean of the two teams'
    fga + 0.44 fta - oreb + tov) and ``P``: the player's possessions on the floor, ``poss_g x min / (team minutes / 5)``."""
    b = box.filter(pl.col("minutes") > 0).with_columns(
        (pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov")).alias("usage_n"),
        pl.col("game_date").cast(pl.Date),
        pl.col("player_id").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
    )
    tm = b.group_by(["game_id", "team_id"]).agg(
        (
            pl.col("fga").sum()
            + 0.44 * pl.col("fta").sum()
            - pl.col("oreb").sum()
            + pl.col("tov").sum()
        ).alias("tposs"),
        pl.col("minutes").sum().alias("tmin"),
    )
    gp = tm.group_by("game_id").agg(pl.col("tposs").mean().alias("poss_g"))
    return (
        b.join(tm.select("game_id", "team_id", "tmin"), on=["game_id", "team_id"])
        .join(gp, on="game_id")
        .with_columns((pl.col("poss_g") * pl.col("minutes") / (pl.col("tmin") / 5.0)).alias("P"))
    )


def _stat_expr(s: str) -> pl.Expr:
    return pl.col("usage_n") if s == "usage" else pl.col(s)


# --------------------------------------------------------------------------- replacement rate and prior


def fringe_player_seasons(b: pl.DataFrame) -> pl.DataFrame:
    """(season, player_id) of fringe player-seasons: rank by total minutes within the team-season of the team where
    the player logged the most minutes that season; rank > FRINGE_RANK is fringe. Deterministic (ties by player_id)."""
    ts = b.group_by(["season", "team_id", "player_id"]).agg(pl.col("minutes").sum().alias("m"))
    ps = ts.sort(
        ["season", "player_id", "m", "team_id"], descending=[False, False, True, False]
    ).unique(["season", "player_id"], keep="first", maintain_order=True)
    ps = ps.sort(["season", "team_id", "m", "player_id"], descending=[False, False, True, False])
    ps = ps.with_columns((pl.int_range(pl.len()).over(["season", "team_id"]) + 1).alias("rk"))
    return ps.filter(pl.col("rk") > FRINGE_RANK).select("season", "player_id")


def replacement_rates(b: pl.DataFrame) -> dict[int, dict[str, float]]:
    """{season S: {stat: per-possession fringe rate pooled over seasons S-2..S-1}} for every S with >= 1 prior season in
    ``b``. Only complete prior seasons enter; season S itself never does."""
    fr = fringe_player_seasons(b)
    bb = b.join(fr, on=["season", "player_id"], how="inner")
    tot = bb.group_by("season").agg(
        pl.col("P").sum().alias("P"), *[_stat_expr(s).sum().alias(s) for s in RATE_STATS]
    )
    by = {int(r["season"]): r for r in tot.iter_rows(named=True)}
    out: dict[int, dict[str, float]] = {}
    for season in sorted(int(x) for x in b["season"].unique().to_list()):
        priors = [by[p] for p in range(season - PRIOR_SEASONS, season) if p in by]
        if not priors:
            continue
        q = sum(float(r["P"]) for r in priors)
        out[season] = {s: sum(float(r[s]) for r in priors) / q for s in RATE_STATS}
    return out


def estimate_k(
    b: pl.DataFrame, repl: Mapping[int, Mapping[str, float]], seasons: tuple[int, ...]
) -> dict[str, dict[str, float]]:
    """Normal-normal empirical-Bayes prior strength k_s (possessions) per stat, by method of moments on player-seasons of
    ``seasons`` with >= MIN_Q_FOR_K possessions: the prior is centred on the season's replacement rate,
    tau2 = mean[(rate - repl)^2 - sampling variance], sigma2 = mean[Q x sampling variance], k = sigma2 / tau2, clipped
    to K_BOUNDS. The sampling variance of a player-season rate is sum_g (S_g - rate P_g)^2 / Q^2."""
    res: dict[str, dict[str, float]] = {}
    sub = b.filter(pl.col("season").is_in(list(seasons)))
    for s in RATE_STATS:
        ps = sub.group_by(["season", "player_id"]).agg(
            _stat_expr(s).sum().alias("S"), pl.col("P").sum().alias("Q")
        )
        ps = ps.filter(pl.col("Q") >= MIN_Q_FOR_K).with_columns(
            (pl.col("S") / pl.col("Q")).alias("r")
        )
        g = sub.join(
            ps.select("season", "player_id", "r"), on=["season", "player_id"]
        ).with_columns(((_stat_expr(s) - pl.col("r") * pl.col("P")) ** 2).alias("e2"))
        sv = g.group_by(["season", "player_id"]).agg(pl.col("e2").sum().alias("e2"))
        ps = ps.join(sv, on=["season", "player_id"]).with_columns(
            (pl.col("e2") / pl.col("Q") ** 2).alias("samp"),
            pl.col("season")
            .replace_strict({k: v[s] for k, v in repl.items()}, default=None)
            .alias("rep"),
        )
        ps = ps.drop_nulls("rep")
        tau2 = float(((ps["r"] - ps["rep"]) ** 2 - ps["samp"]).mean())  # type: ignore[arg-type]
        sig2 = float((ps["Q"] * ps["samp"]).mean())  # type: ignore[arg-type]
        k = sig2 / tau2 if tau2 > 0 else K_BOUNDS[1]
        res[s] = {
            "k": float(min(max(k, K_BOUNDS[0]), K_BOUNDS[1])),
            "k_unclipped": float(k),
            "tau2": tau2,
            "sigma2": sig2,
            "n_player_seasons": float(ps.height),
        }
    return res


# --------------------------------------------------------------------------- as-of cumulative sums


def player_cum(b: pl.DataFrame) -> pl.DataFrame:
    """Per player-game cumulative sums INCLUDING the row: ``Qc`` and ``S_<stat>`` for every rate stat. Looked up as-of with
    :func:`asof_cum` (the last row strictly before a date)."""
    s = b.sort(["player_id", "game_date", "game_id"])
    return s.select(
        "player_id",
        "game_date",
        pl.col("P").cum_sum().over("player_id").alias("Qc"),
        *[_stat_expr(x).cum_sum().over("player_id").alias(f"S_{x}") for x in RATE_STATS],
    )


def asof_cum(cum: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """``keys`` (player_id, game_date, + any columns) joined to the cumulative sums after the player's last game strictly
    BEFORE ``game_date`` (as production does for its states). Rows with no earlier game get nulls. Row order of ``keys`` is
    not preserved; callers join on their own keys."""
    left = keys.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
    right = cum.rename({"game_date": "_cd"}).sort("_cd")
    return left.join_asof(
        right, left_on="_key", right_on="_cd", by="player_id", strategy="backward"
    ).drop("_key", "_cd")


def shrunk_rates(
    frame: pl.DataFrame, repl_by_season: Mapping[int, Mapping[str, float]], k: Mapping[str, float]
) -> pl.DataFrame:
    """Adds ``r_<stat>`` (per possession) = (S + k repl) / (Q + k) for every rate stat; ``frame`` carries ``Qc``,
    ``S_<stat>`` (null -> a player with no earlier game gets the replacement rate) and the game's ``season``."""
    out = frame.with_columns(
        pl.col("Qc").fill_null(0.0), *[pl.col(f"S_{x}").fill_null(0.0) for x in RATE_STATS]
    )
    reps = {x: {s: v[x] for s, v in repl_by_season.items()} for x in RATE_STATS}
    exprs = []
    for x in RATE_STATS:
        rep = pl.col("season").replace_strict(reps[x], default=None)
        exprs.append(((pl.col(f"S_{x}") + k[x] * rep) / (pl.col("Qc") + k[x])).alias(f"r_{x}"))
        exprs.append(rep.alias(f"rep_{x}"))
    return out.with_columns(exprs)


# --------------------------------------------------------------------------- team pace and the redistribution pool


def team_pace(b: pl.DataFrame) -> pl.DataFrame:
    """(game_id, team_id, ppm): the team's mean game possessions over its previous PACE_WINDOW games (strictly earlier,
    across seasons, >= PACE_MIN_GAMES) divided by 48. Null where the team has fewer earlier games."""
    tg = b.group_by(["team_id", "game_id", "game_date"]).agg(pl.col("poss_g").first())
    tg = tg.sort(["team_id", "game_date", "game_id"])
    return tg.select(
        "game_id",
        "team_id",
        (
            pl.col("poss_g")
            .shift(1)
            .rolling_mean(window_size=PACE_WINDOW, min_samples=PACE_MIN_GAMES)
            .over("team_id")
            / 48.0
        ).alias("ppm"),
    )


def rotation_pool(b: pl.DataFrame) -> pl.DataFrame:
    """(game_id, team_id, player_id): players with >= POOL_MIN_APP appearances in the team's previous POOL_WINDOW games OF
    THE SAME SEASON (strictly earlier) and a mean of >= POOL_MEAN_MIN minutes over those appearances."""
    tg = (
        b.group_by(["team_id", "season", "game_id", "game_date"])
        .agg(pl.len().alias("n"))
        .sort(["team_id", "season", "game_date", "game_id"])
    )
    tg = tg.with_columns(pl.int_range(pl.len()).over(["team_id", "season"]).alias("t"))
    bb = b.join(tg.select("team_id", "game_id", "t"), on=["team_id", "game_id"])
    rows: list[pl.DataFrame] = []
    for (team, season), g in tg.group_by(["team_id", "season"], maintain_order=True):
        games = g.sort("t")
        n_t = games.height
        sub = bb.filter((pl.col("team_id") == team) & (pl.col("season") == season))
        pids = np.sort(sub["player_id"].unique().to_numpy())
        pidx = np.searchsorted(pids, sub["player_id"].to_numpy())
        tt = sub["t"].to_numpy()
        mins = np.zeros((len(pids), n_t))
        app = np.zeros((len(pids), n_t))
        mins[pidx, tt] = sub["minutes"].to_numpy()
        app[pidx, tt] = 1.0
        cm = np.concatenate([np.zeros((len(pids), 1)), np.cumsum(mins, axis=1)], axis=1)
        ca = np.concatenate([np.zeros((len(pids), 1)), np.cumsum(app, axis=1)], axis=1)
        gids = games["game_id"].to_list()
        out_g: list[str] = []
        out_p: list[int] = []
        for t in range(1, n_t):
            lo = max(0, t - POOL_WINDOW)
            cnt = ca[:, t] - ca[:, lo]
            sm = cm[:, t] - cm[:, lo]
            with np.errstate(invalid="ignore", divide="ignore"):
                ok = (cnt >= POOL_MIN_APP) & (sm / np.where(cnt > 0, cnt, 1.0) >= POOL_MEAN_MIN)
            for p in pids[ok]:
                out_g.append(gids[t])
                out_p.append(int(p))
        if out_g:
            rows.append(
                pl.DataFrame(
                    {"game_id": out_g, "team_id": [int(team)] * len(out_g), "player_id": out_p},
                    schema={"game_id": pl.Utf8, "team_id": pl.Int64, "player_id": pl.Int64},
                )
            )
    if not rows:
        return pl.DataFrame(schema={"game_id": pl.Utf8, "team_id": pl.Int64, "player_id": pl.Int64})
    return pl.concat(rows)


# --------------------------------------------------------------------------- production states and the OUT rotation set


def state_table(
    played: pl.DataFrame, states: Mapping[str, Any], di: Mapping[float, int], ci: Mapping[str, int]
) -> pl.DataFrame:
    """Per played row, production's state AFTER the row (``n_after``, ``min40``, ``min10``): the frame ``vacated_features``
    builds. ``played`` must be the frame that was passed to ``player_states``."""
    post = states["post"]
    return pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": post[:, di[40.0], ci["min"]],
            "min10": post[:, di[10.0], ci["min"]],
        }
    )


def asof_state(st: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """``keys`` (player_id, game_date, ...) joined to the production state after the player's last game strictly before
    ``game_date``."""
    left = keys.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
    right = st.rename({"game_date": "_pd", "team_id": "_st_team"}).sort("_pd")
    return left.join_asof(
        right, left_on="_key", right_on="_pd", by="player_id", strategy="backward"
    ).drop("_key", "_pd")


def out_rotation_rows(
    flagged: Mapping[str, set[int]],
    game_info: Mapping[str, tuple[object, int, int]],
    st: pl.DataFrame,
    min_prior: int,
    rotation_min40: float,
) -> pl.DataFrame:
    """Player-level twin of production's ``vacated_features`` join: (game_id, team_id, player_id, game_date, min10) of OUT
    rotation players (as-of ``n_after >= min_prior``, ``min40 >= rotation_min40``) whose latest team is the game's home or
    away team. Summing ``min10`` per (game_id, team_id) gives production ``vac_min`` (unit-tested)."""
    cols = {
        "game_id": pl.Utf8,
        "team_id": pl.Int64,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "min10": pl.Float64,
    }
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    if not recs:
        return pl.DataFrame(schema=cols)
    left = pl.DataFrame(
        recs, schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date}, orient="row"
    )
    j = asof_state(st, left).filter(
        (pl.col("n_after") >= min_prior) & (pl.col("min40") >= rotation_min40)
    )
    gi = pl.DataFrame(
        [(g, v[1], v[2]) for g, v in game_info.items()],
        schema={"game_id": pl.Utf8, "home": pl.Int64, "away": pl.Int64},
        orient="row",
    )
    j = (
        j.rename({"_st_team": "team_id"})
        .join(gi, on="game_id")
        .filter((pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away")))
    )
    return j.select(list(cols)).cast(cols)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the features


def _rates_at(
    keys: pl.DataFrame,
    cum: pl.DataFrame,
    st: pl.DataFrame,
    pace: pl.DataFrame,
    repl: Mapping[int, Mapping[str, float]],
    k: Mapping[str, float],
) -> pl.DataFrame:
    """For ``keys`` (game_id, team_id, player_id, game_date, season): as-of shrunk rates r_<stat>, the replacement rate
    rep_<stat>, expected possessions ``P_exp`` = production min10 x the team's as-of possessions per minute."""
    j = asof_cum(cum, keys)
    j = shrunk_rates(j, repl, k)
    j = (
        asof_state(st, j.select("game_id", "player_id", "game_date"))
        .select("game_id", "player_id", "min10")
        .join(j, on=["game_id", "player_id"], how="right")
    )
    j = j.join(pace, on=["game_id", "team_id"], how="left")
    return j.with_columns((pl.col("min10") * pl.col("ppm")).alias("P_exp"))


def build_repl_delta(
    b: pl.DataFrame,
    st: pl.DataFrame,
    out_rows: pl.DataFrame,
    out_all: pl.DataFrame,
    scored: pl.DataFrame,
    reported: set[str],
    repl: Mapping[int, Mapping[str, float]],
    k: Mapping[str, float],
    placebo_seed: int = 0,
) -> pl.DataFrame:
    """The REPLACEMENT_DELTA columns for the ``scored`` rows (game_id, team_id, player_id, game_date, season).

    * ``b``: prepped box of every played row (history + database), all seasons <= the last scored one; a row is read only
      through as-of lookups, so nothing dated on or after a game's date reaches that game's columns.
    * ``st``: :func:`state_table`; ``out_rows``: :func:`out_rotation_rows`; ``out_all``: (game_id, player_id) of EVERY player
      on the game's OUT list (they cannot absorb); ``reported``: game ids with a usable report. Games outside it get null
      columns (never imputed as "nobody out"); a reported game with nobody rotation-OUT gets explicit zeros.
    * Row order of the result is NOT part of the contract (polars joins are not order-stable); callers join on (game_id, player_id).
    * Placebo columns ``pq_qv_<s>`` / ``pq_rd_<s>``: the same sums with each OUT player's quality replaced by that of a
      uniformly drawn same-team pool member who is NOT out tonight (as-of rates, the OUT player's own possessions kept).
    """
    cum = player_cum(b)
    pace = team_pace(b)
    pool = (
        rotation_pool(b)
        .join(
            out_all.with_columns(pl.lit(True).alias("_o")), on=["game_id", "player_id"], how="left"
        )
        .filter(pl.col("_o").is_null())
        .drop("_o")
    )
    gm = b.select("game_id", "season", "game_date").unique("game_id")
    # --- OUT rotation players: quality-weighted vacancy per (game, team)
    o = out_rows.drop("min10").join(gm.select("game_id", "season"), on="game_id", how="left")
    o = _rates_at(o, cum, st, pace, repl, k)
    qv_exprs = [
        ((pl.col(f"r_{s}") - pl.col(f"rep_{s}")) * pl.col("P_exp")).sum().alias(f"qv_{s}")
        for s in STATS
    ]
    v0_exprs = [(pl.col(f"r_{s}") * pl.col("P_exp")).sum().alias(f"v0_{s}") for s in STATS]
    team = o.group_by(["game_id", "team_id"]).agg(
        *qv_exprs, *v0_exprs, pl.col("P_exp").sum().alias("vp"), pl.len().alias("n_out")
    )
    # --- redistribution pool weights
    pk = pool.join(gm, on="game_id", how="left")
    pk = _rates_at(pk, cum, st, pace, repl, k).with_columns(
        (pl.col("r_usage") * pl.col("P_exp")).alias("w")
    )
    wsum = pk.group_by(["game_id", "team_id"]).agg(
        pl.col("w").sum().alias("W"), pl.len().alias("n_pool")
    )
    # --- placebo: wrong-identity quality
    pq = _placebo(o, pk, placebo_seed)
    # --- scored players
    sc = _rates_at(
        scored.select("game_id", "team_id", "player_id", "game_date", "season"),
        cum,
        st,
        pace,
        repl,
        k,
    )
    sc = sc.with_columns((pl.col("r_usage") * pl.col("P_exp")).alias("w_i"))
    in_pool = pool.select("game_id", "team_id", "player_id").with_columns(pl.lit(1).alias("_in"))
    sc = (
        sc.join(in_pool, on=["game_id", "team_id", "player_id"], how="left")
        .join(wsum, on=["game_id", "team_id"], how="left")
        .join(team, on=["game_id", "team_id"], how="left")
        .join(pq, on=["game_id", "team_id"], how="left")
    )
    rep_flag = pl.col("game_id").is_in(sorted(reported))
    den = pl.col("W") + pl.when(pl.col("_in").is_null()).then(pl.col("w_i")).otherwise(0.0)
    share = pl.when(pl.col("W").is_not_null() & (den > 0)).then(pl.col("w_i") / den).otherwise(None)
    sc = sc.with_columns(share.alias("share"))
    cols = []
    for s in STATS:
        for name, base in (("qv", f"qv_{s}"), ("v0", f"v0_{s}")):
            tot = pl.when(rep_flag).then(pl.col(base).fill_null(0.0)).otherwise(None)
            cols.append(tot.alias(name + "_" + s))
        cols.append(
            pl.when(rep_flag)
            .then(pl.col(f"qv_{s}").fill_null(0.0) * pl.col("share"))
            .otherwise(None)
            .alias(f"rd_{s}")
        )
        cols.append(
            pl.when(rep_flag)
            .then(pl.col(f"v0_{s}").fill_null(0.0) * pl.col("share"))
            .otherwise(None)
            .alias(f"v0d_{s}")
        )
        cols.append(
            pl.when(rep_flag)
            .then(pl.col(f"pq_qv_{s}").fill_null(0.0))
            .otherwise(None)
            .alias(f"pq_qv_{s}")
        )
        cols.append(
            pl.when(rep_flag)
            .then(pl.col(f"pq_qv_{s}").fill_null(0.0) * pl.col("share"))
            .otherwise(None)
            .alias(f"pq_rd_{s}")
        )
    cols.append(pl.when(rep_flag).then(pl.col("n_out").fill_null(0)).otherwise(None).alias("n_out"))
    sc = sc.with_columns(cols).with_columns(
        pl.when(rep_flag).then(pl.col("share")).otherwise(None).alias("share")
    )
    keep = ["game_id", "player_id", *COLUMNS, *PLACEBO_COLUMNS]
    out = sc.select(keep)
    if out.height != scored.height:
        raise RuntimeError(
            f"feature rows {out.height} != scored rows {scored.height}: a join duplicated or dropped rows"
        )
    return out


PLACEBO_COLUMNS: tuple[str, ...] = (
    *(f"pq_qv_{s}" for s in STATS),
    *(f"pq_rd_{s}" for s in STATS),
)


def _placebo(o: pl.DataFrame, pk: pl.DataFrame, seed: int) -> pl.DataFrame:
    """Per (game, team): sum over the OUT rotation players of (rate of a random not-out pool member - repl) x the OUT
    player's own expected possessions. Deterministic per (seed, game, team); pool of size < n_out draws with replacement."""
    empty = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            **{f"pq_qv_{s}": pl.Float64 for s in STATS},
        }
    )
    if o.is_empty():
        return empty
    pool_by = {
        key: grp
        for key, grp in pk.sort(["game_id", "team_id", "player_id"]).group_by(
            ["game_id", "team_id"], maintain_order=True
        )
    }
    rows = []
    for (gid, tid), grp in o.sort(["game_id", "team_id", "player_id"]).group_by(
        ["game_id", "team_id"], maintain_order=True
    ):
        cand = pool_by.get((gid, tid))
        if cand is None or cand.height == 0:
            continue
        rng = _rng_for(seed, str(gid), int(tid))
        n = grp.height
        pick = rng.choice(cand.height, size=n, replace=cand.height < n)
        p_exp = grp["P_exp"].to_numpy()
        rec: dict[str, Any] = {"game_id": gid, "team_id": tid}
        for s in STATS:
            rate = cand[f"r_{s}"].to_numpy()[pick]
            rep = grp[f"rep_{s}"].to_numpy()
            rec[f"pq_qv_{s}"] = float(np.nansum((rate - rep) * p_exp))
        rows.append(rec)
    if not rows:
        return empty
    return pl.DataFrame(rows, schema=empty.schema)


def _rng_for(seed: int, game_id: str, team_id: int) -> np.random.Generator:
    """Deterministic per (seed, game, team), independent of row order."""
    return np.random.default_rng([seed, zlib.crc32(f"{game_id}|{team_id}".encode())])
