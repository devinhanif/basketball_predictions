"""Context-residual props candidate: recency average + LightGBM correction.

Target: ``actual stat - recency_avg`` on PLAYED rows (``minutes > 0``; DNP rows
carry stats of 0 and NULL minutes and are excluded from labels and from every
average). ``recency_avg`` is the played-only exponentially weighted mean
(weight ``0.5 ** (games_ago / halflife)``, half-life 10 games) used by
``nba.props.forward.recency_weighted_dist``.

Every feature is strictly as-of: it is built from games dated BEFORE the
target game's date, or from the official pre-tip injury report (latest report
stamped before the tip-off proxy, via the ``nba.sim.usage_redistribution``
helpers). Nothing uses tonight's box score, lineup, or starter flag.

Predictive distribution (full, not just a mean)::

    y ~ recency_mean + mu_hat(x) + s_hat(x) * Z,   Z ~ empirical, clipped at 0

``mu_hat`` is a LightGBM mean-residual model, ``s_hat`` a LightGBM model of
``|residual|`` (the residual-scale head), and ``Z`` is the empirical
distribution of standardized residuals ``(resid - mu_hat) / s_hat`` on a
held-out calibration window carved from the END of the training window
(split-conformal style: the quantiles of the standardized scores on rows the
heads never saw give the interval its finite-sample coverage without a
distributional assumption).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
)
from nba.models.rung0_baselines import MovEloBaseline
from nba.props.baselines import _DEFAULT_STD

PROP_STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
QUANTILE_TAUS: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 20))
#: Fine tau grid for CRPS (same grid as ``nba.props.metrics``).
CRPS_TAUS: np.ndarray = (np.arange(1, 200) - 0.5) / 199
ELO_PER_POINT = 28.0  # rating points per point of expected margin (NBA rule of thumb)
HALFLIFE = 10.0
MIN_PRIOR_PLAYED = 5
_DECAYS: tuple[float, ...] = (5.0, 10.0, 40.0, 100.0)
_RAW = ("pts", "reb", "ast", "fg3m", "min", "starter")  # then squares of first four
_COLS = _RAW + ("pts2", "reb2", "ast2", "fg3m2")
_CI = {c: i for i, c in enumerate(_COLS)}
_DI = {h: i for i, h in enumerate(_DECAYS)}
ROTATION_MIN40 = 12.0  # an OUT teammate counts if his long-run minutes >= this
CUSUM_THRESHOLD = 4.0
CUSUM_DRIFT = 0.5


#: Count stats whose quantiles the ``integer_support`` flag maps to the integers.
INTEGER_SUPPORT_STATS: tuple[str, ...] = ("reb", "ast", "fg3m")


def to_integer_support(q: np.ndarray) -> np.ndarray:
    """Map non-negative quantiles to integer support: ``ceil(q - 0.5)`` (== round half
    up), the same N-0.5 continuity convention P(stat >= N) uses. Monotone in ``q``, so a
    non-decreasing grid stays non-decreasing; ``mean`` is never touched."""
    out = np.maximum(np.ceil(np.asarray(q, dtype=float) - 0.5), 0.0)
    return np.asarray(np.maximum.accumulate(out, axis=-1))


@dataclass(frozen=True)
class ContextResidualConfig:
    n_estimators: int = 200
    learning_rate: float = 0.04
    num_leaves: int = 15
    min_child_samples: int = 100
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    cal_frac: float = 0.15
    min_cal_rows: int = 2000
    seed: int = 0
    n_jobs: int = 4
    #: Map predictive quantiles to integer support (docs/INTEGER_QUANTILES.md).
    #: Default False = byte-identical production output.
    integer_support: bool = False
    integer_support_stats: tuple[str, ...] = INTEGER_SUPPORT_STATS
    #: Upper-tail construction for the points quantiles (docs/PTS_TAIL.md): "off"
    #: (production, byte-identical) | "sqrt" | "mondrian_up" | "gamma_tail".
    pts_tail: str = "off"
    pts_tail_stats: tuple[str, ...] = ("pts",)
    #: Lower-tail construction (docs/LOWER_TAIL.md): "off" (production, byte-identical) |
    #: "mixture" | "mondrian_centre" | "mondrian_minutes". Needs a ``minutes`` column in the
    #: training frame; mutually exclusive with ``pts_tail``.
    lower_tail: str = "off"
    lower_tail_stats: tuple[str, ...] = PROP_STATS
    #: F9 young-pick prior (docs/prereg/F9_YOUNG_PICK_PRIOR.md): "off" (production,
    #: byte-identical) | "on" appends ``YP_FEATURES`` to every stat's feature list. Needs a
    #: feature frame from ``build_features(..., young_pick=True)``.
    young_pick: str = "off"
    #: Production gates official-report snapshots on the REAL scheduled tip-off
    #: (maintainer decision 2026-10-09); ``tip_source="proxy19"`` reproduces the old runs.
    report: ReportTriggerConfig = field(
        default_factory=lambda: ReportTriggerConfig(tip_source="real")
    )


# --------------------------------------------------------------------------- Elo


def asof_elo_margin(games: pl.DataFrame, params: dict[str, float]) -> pl.DataFrame:
    """Pre-game MOV-Elo expected home margin per game (sequential, uses only
    games completed before each game). Columns: game_id, exp_margin_home."""
    model = MovEloBaseline(
        seed=0,
        k_factor=params["k_factor"],
        home_advantage_elo=params["home_advantage_elo"],
        season_carryover=params["season_carryover"],
        mov_c=params["mov_c"],
        mov_div=params["mov_div"],
    )
    ordered = games.sort(["game_date", "game_id"])
    cur = None
    out_ids: list[str] = []
    out_m: list[float] = []
    for gid, season, h, a, hp, ap in ordered.select(
        ["game_id", "season", "home_team", "away_team", "home_pts", "away_pts"]
    ).iter_rows():
        if cur is not None and season != cur:
            model._regress_ratings_to_mean()
        cur = season
        hr = model.ratings_.get(h, model.initial_rating)
        ar = model.ratings_.get(a, model.initial_rating)
        out_ids.append(str(gid))
        out_m.append((hr + model.home_advantage_elo - ar) / ELO_PER_POINT)
        if hp is None or ap is None:
            continue
        p_home = model._win_prob(h, a)
        actual = 1.0 if hp > ap else 0.0
        diff_w = (
            (hr + model.home_advantage_elo - ar)
            if actual >= 0.5
            else (ar - hr - model.home_advantage_elo)
        )
        upd = model.k_factor * model._mov_multiplier(abs(hp - ap), diff_w) * (actual - p_home)
        model.ratings_[h] = hr + upd
        model.ratings_[a] = ar - upd
    return pl.DataFrame({"game_id": out_ids, "exp_margin_home": out_m})


# --------------------------------------------------------------------------- team-game context


def team_game_context(games: pl.DataFrame, pgs: pl.DataFrame, elo: pl.DataFrame) -> pl.DataFrame:
    """One row per (game_id, team_id): rest, b2b, season game no, rolling-20
    as-of team scoring/allowing/stat-allowed, Elo expected margin (signed to the team)."""
    g = games.select(
        ["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"]
    )
    home = g.select(
        "game_id",
        "game_date",
        "season",
        pl.col("home_team").alias("team_id"),
        pl.col("away_team").alias("opp_id"),
        pl.lit(1).alias("is_home"),
        pl.col("home_pts").alias("pts_for"),
        pl.col("away_pts").alias("pts_against"),
    )
    away = g.select(
        "game_id",
        "game_date",
        "season",
        pl.col("away_team").alias("team_id"),
        pl.col("home_team").alias("opp_id"),
        pl.lit(0).alias("is_home"),
        pl.col("away_pts").alias("pts_for"),
        pl.col("home_pts").alias("pts_against"),
    )
    tg = pl.concat([home, away]).sort(["team_id", "game_date", "game_id"])
    tot = pgs.group_by(["game_id", "team_id"]).agg(
        [pl.col(s).sum().alias(f"tot_{s}") for s in PROP_STATS]
    )
    tg = tg.join(tot, on=["game_id", "team_id"], how="left")
    opp_tot = tot.rename({"team_id": "opp_id", **{f"tot_{s}": f"allow_{s}" for s in PROP_STATS}})
    tg = tg.join(opp_tot, on=["game_id", "opp_id"], how="left").sort(
        ["team_id", "game_date", "game_id"]
    )
    roll = [
        pl.col("game_date").diff().dt.total_days().over("team_id").alias("rest_days"),
        (pl.col("season").cum_count().over(["team_id", "season"]) - 1).alias("team_game_no"),
    ]
    tg = tg.with_columns(roll)
    r20 = [
        pl.col(c).shift(1).rolling_mean(20, min_samples=5).over("team_id").alias(f"r20_{c}")
        for c in ["pts_for", "pts_against", *[f"allow_{s}" for s in PROP_STATS]]
    ]
    tg = tg.with_columns(r20).with_columns(
        (pl.col("rest_days").fill_null(7).clip(0, 7)).alias("rest_days"),
        (pl.col("rest_days") == 1).fill_null(False).cast(pl.Int8).alias("b2b"),
    )
    tg = tg.join(elo, on="game_id", how="left").with_columns(
        pl.when(pl.col("is_home") == 1)
        .then(pl.col("exp_margin_home"))
        .otherwise(-pl.col("exp_margin_home"))
        .alias("exp_margin")
    )
    opp_ctx = tg.select(
        pl.col("game_id"),
        pl.col("team_id").alias("opp_id"),
        pl.col("rest_days").alias("opp_rest_days"),
        pl.col("b2b").alias("opp_b2b"),
        pl.col("r20_pts_for").alias("opp_r20_pts_for"),
        pl.col("r20_pts_against").alias("opp_r20_pts_against"),
        *[pl.col(f"r20_allow_{s}").alias(f"opp_allow_{s}") for s in PROP_STATS],
    )
    tg = tg.join(opp_ctx, on=["game_id", "opp_id"], how="left")
    keep = [
        "game_id",
        "team_id",
        "opp_id",
        "is_home",
        "rest_days",
        "b2b",
        "team_game_no",
        "exp_margin",
        "r20_pts_for",
        "r20_pts_against",
        "opp_rest_days",
        "opp_b2b",
        "opp_r20_pts_for",
        "opp_r20_pts_against",
        *[f"opp_allow_{s}" for s in PROP_STATS],
    ]
    return tg.select(keep)


# --------------------------------------------------------------------------- player states


def player_states(played: pl.DataFrame) -> dict[str, np.ndarray]:
    """Sequential per-player exponentially weighted states over PLAYED rows.

    ``played`` must be sorted by (player_id, game_date, game_id). Returns arrays
    aligned with the rows: ``pre`` (n, n_decay, n_cols) = weighted means over
    games strictly BEFORE the row, ``post`` = including the row, plus counts and
    the causal CUSUM / team-change features.
    """
    n = played.height
    pid = played["player_id"].to_numpy()
    tid = played["team_id"].to_numpy()
    seas = played["season"].to_numpy()
    dates = played["game_date"].cast(pl.Int32).to_numpy()  # days since epoch
    base = np.column_stack(
        [played[c].to_numpy().astype(float) for c in ("pts", "reb", "ast", "fg3m", "minutes")]
        + [played["starter"].fill_null(False).cast(pl.Float64).to_numpy()]
    )
    x = np.column_stack([base, base[:, :4] ** 2])
    dec = np.array([0.5 ** (1.0 / h) for h in _DECAYS])
    pre = np.full((n, len(_DECAYS), x.shape[1]), np.nan)
    post = np.empty_like(pre)
    n_prior = np.zeros(n, dtype=np.int32)
    n_season = np.zeros(n, dtype=np.int32)
    gap_days = np.full(n, np.nan)
    since_flag = np.full(n, 30.0)
    flag_dir = np.zeros(n)
    since_team = np.full(n, 30.0)
    s_mat = np.zeros((len(_DECAYS), x.shape[1]))
    d_vec = np.zeros(len(_DECAYS))
    cnt = 0
    sn = 0
    w_mean = w_m2 = 0.0
    s_hi = s_lo = 0.0
    since = 30.0
    last_dir = 0.0
    since_t = 30.0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s_mat[:] = 0.0
            d_vec[:] = 0.0
            cnt = sn = 0
            w_mean = w_m2 = 0.0
            s_hi = s_lo = 0.0
            since, last_dir, since_t = 30.0, 0.0, 30.0
        else:
            if seas[i] != seas[i - 1]:
                sn = 0
            gap_days[i] = dates[i] - dates[i - 1]
            if tid[i] != tid[i - 1]:
                since_t = 0.0
        n_prior[i] = cnt
        n_season[i] = sn
        since_flag[i] = since
        flag_dir[i] = last_dir
        since_team[i] = since_t
        if cnt > 0:
            pre[i] = s_mat / d_vec[:, None]
        s_mat = dec[:, None] * s_mat + x[i][None, :]
        d_vec = dec * d_vec + 1.0
        post[i] = s_mat / d_vec[:, None]
        # causal CUSUM on minutes: standardize by the mean/std of PRIOR games only
        m_i = base[i, 4]
        if cnt >= 4:
            sd = max((w_m2 / max(cnt - 1, 1)) ** 0.5, 2.0)
            z = (m_i - w_mean) / sd
            s_hi = max(0.0, s_hi + z - CUSUM_DRIFT)
            s_lo = min(0.0, s_lo + z + CUSUM_DRIFT)
            if s_hi > CUSUM_THRESHOLD or -s_lo > CUSUM_THRESHOLD:
                last_dir = 1.0 if s_hi > CUSUM_THRESHOLD else -1.0
                since = 0.0
                s_hi = s_lo = 0.0
            else:
                since = min(since + 1.0, 30.0)
        cnt += 1
        sn += 1
        since_t = min(since_t + 1.0, 30.0)
        delta = m_i - w_mean
        w_mean += delta / cnt
        w_m2 += delta * (m_i - w_mean)
    return {
        "pre": pre,
        "post": post,
        "n_prior": n_prior,
        "n_season": n_season,
        "gap_days": gap_days,
        "since_flag": since_flag,
        "flag_dir": flag_dir,
        "since_team": since_team,
    }


def _sel(arr: np.ndarray, h: float, col: str) -> np.ndarray:
    return arr[:, _DI[h], _CI[col]]


# --------------------------------------------------------------------------- vacated usage


def vacated_features(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[object, int, int]],
) -> pl.DataFrame:
    """Per (game_id, team_id) vacated production from teammates OUT on the
    official pre-tip report: sums of their as-of (state after their latest
    game strictly before the game date) minutes and stat averages.

    Only games present in ``flagged`` have a report; the caller left-joins, so
    games without a report get null (never imputed as "nobody out")."""
    post = states["post"]
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "min10": _sel(post, 10.0, "min"),
            **{f"v_{s}": _sel(post, 10.0, s) for s in PROP_STATS},
        }
    ).sort("game_date")
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date}
    if recs:
        left = pl.DataFrame(recs, schema=schema, orient="row")
        left = left.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort(
            "_key"
        )
        j = left.join_asof(
            st.rename({"game_date": "_pd"}),
            left_on="_key",
            right_on="_pd",
            by="player_id",
            strategy="backward",
        ).filter((pl.col("n_after") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= ROTATION_MIN40))
        gi = pl.DataFrame(
            [(g, v[1], v[2]) for g, v in game_info.items()],
            schema={"game_id": pl.Utf8, "home": pl.Int64, "away": pl.Int64},
            orient="row",
        )
        j = j.join(gi, on="game_id").filter(
            (pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away"))
        )
        agg = j.group_by(["game_id", "team_id"]).agg(
            pl.len().alias("n_out_rot"),
            pl.col("min10").sum().alias("vac_min"),
            *[pl.col(f"v_{s}").sum().alias(f"vac_{s}") for s in PROP_STATS],
        )
    else:
        agg = pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "team_id": pl.Int64,
                "n_out_rot": pl.UInt32,
                "vac_min": pl.Float64,
                **{f"vac_{s}": pl.Float64 for s in PROP_STATS},
            }
        )
    return agg


def flagged_from_availability(
    avail: pl.DataFrame, games: pl.DataFrame, cfg: ReportTriggerConfig
) -> tuple[dict[str, set[int]], dict[str, tuple[object, int, int]]]:
    """Latest usable pre-tip OUT set per game from raw ``player_availability``
    rows (columns game_id, player_id, status, as_of, source). Games without a
    usable report are absent from the first dict."""
    rows = (
        avail.filter(pl.col("source").is_in(list(cfg.sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            "player_id",
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            "game_date",
        )
    )
    if cfg.tip_source == "real":
        from nba.features.game_tipoff import attach_real_tips

        rows = attach_real_tips(rows)
    flagged, _used = latest_pretip_flagged(rows, cfg)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    return flagged, info


# --------------------------------------------------------------------------- feature frame


def build_features(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    flagged: dict[str, set[int]],
    elo_params: dict[str, float],
    tracking_feats: pl.DataFrame | None = None,
    lineups_known: bool = False,
    t30_scratch_prob: float = 0.0,
    young_pick: bool = False,
) -> pl.DataFrame:
    """One row per PLAYED player-game with as-of features and the four stat
    labels. ``flagged`` maps game_id -> OUT player ids (only games with a usable
    pre-tip report are keys).

    ``tracking_feats`` is an OPT-IN screen hook (default ``None`` = production,
    output unchanged): as-of rows from
    ``nba.features.tracking_features.build_tracking_features``, left-joined on
    (game_id, player_id). Production never passes it.

    ``lineups_known=True`` (default False = production, output unchanged) attaches
    the ``t30_*`` columns of :mod:`nba.props.lineup_features` (tonight's confirmed
    starters). They are a LEAK at the production T-60 prediction time and exist only
    for the T-30 experiment (docs/LINEUPS_KNOWN.md).

    ``young_pick=True`` (default False = production, output unchanged) appends the seven
    ``yp_*`` columns of :func:`young_pick_features` (docs/prereg/F9_YOUNG_PICK_PRIOR.md);
    ``static`` must then carry ``draft_pick``."""
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    st = player_states(played)
    pre, n_prior = st["pre"], st["n_prior"]
    cols: dict[str, np.ndarray] = {
        "n_prior": n_prior.astype(float),
        "n_season": st["n_season"].astype(float),
        "gap_days": st["gap_days"],
        "since_flag": st["since_flag"],
        "flag_dir": st["flag_dir"],
        "since_team": st["since_team"],
        "min5": _sel(pre, 5.0, "min"),
        "min10": _sel(pre, 10.0, "min"),
        "min40": _sel(pre, 40.0, "min"),
        "starter10": _sel(pre, 10.0, "starter"),
        "starter40": _sel(pre, 40.0, "starter"),
    }
    cols["min_gap"] = cols["min5"] - cols["min40"]
    cols["min_gap10"] = cols["min10"] - cols["min40"]
    for s in PROP_STATS:
        cols[f"m5_{s}"] = _sel(pre, 5.0, s)
        cols[f"m10_{s}"] = _sel(pre, 10.0, s)
        cols[f"m40_{s}"] = _sel(pre, 40.0, s)
        cols[f"m100_{s}"] = _sel(pre, 100.0, s)
        var = np.clip(_sel(pre, 10.0, f"{s}2") - cols[f"m10_{s}"] ** 2, 0.0, None)
        nn = np.maximum(n_prior, 2)
        sd = np.sqrt(var * nn / (nn - 1))
        cols[f"sd10_{s}"] = np.where((n_prior >= 2) & (sd > 0), sd, _DEFAULT_STD[s])
        cols[f"role_gap_{s}"] = cols[f"m10_{s}"] - cols[f"m100_{s}"]
        cols[f"rate_{s}"] = cols[f"m10_{s}"] / np.maximum(cols["min10"], 1.0)
    # starter flag of the player's LAST game (as-of; never tonight's)
    last_starter = np.full(played.height, np.nan)
    pid = played["player_id"].to_numpy()
    stv = played["starter"].fill_null(False).cast(pl.Float64).to_numpy()
    same = np.r_[False, pid[1:] == pid[:-1]]
    last_starter[1:] = np.where(same[1:], stv[:-1], np.nan)
    cols["last_starter"] = last_starter
    base = played.select(
        "game_id",
        "player_id",
        "team_id",
        "game_date",
        "season",
        pl.col("home_team").alias("_home"),
        *PROP_STATS,
    )
    feats = base.with_columns([pl.Series(k, v) for k, v in cols.items()])
    elo = asof_elo_margin(games, elo_params)
    tctx = team_game_context(games, pgs, elo)
    feats = feats.join(tctx, on=["game_id", "team_id"], how="left")
    game_info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    vac = vacated_features(played, st, flagged, game_info)
    reported = pl.DataFrame({"game_id": sorted(flagged)}, schema={"game_id": pl.Utf8}).with_columns(
        pl.lit(1).alias("has_report")
    )
    feats = (
        feats.join(vac, on=["game_id", "team_id"], how="left")
        .join(reported, on="game_id", how="left")
        .with_columns(pl.col("has_report").fill_null(0))
    )
    # reported game with nobody rotation-out -> explicit zeros (report exists)
    zero_cols = ["n_out_rot", "vac_min", *[f"vac_{s}" for s in PROP_STATS]]
    feats = feats.with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(c).fill_null(0.0))
            .otherwise(None)
            .alias(c)
            for c in zero_cols
        ]
    )
    pos = static.select("player_id", "position") if "position" in static.columns else None
    if pos is not None:
        code = (
            pl.col("position")
            .cast(pl.Utf8)
            .str.slice(0, 1)
            .replace_strict({"G": 0, "F": 1, "C": 2}, default=-1)
        )
        feats = feats.join(
            pos.with_columns(code.alias("pos_code"))
            .select("player_id", "pos_code")
            .unique("player_id"),
            on="player_id",
            how="left",
        )
    else:
        feats = feats.with_columns(pl.lit(-1).alias("pos_code"))
    feats = feats.with_columns(
        (pl.col("_home") == pl.col("team_id")).cast(pl.Int8).alias("is_home_f"),
        pl.col("exp_margin").abs().alias("abs_margin"),
    ).drop("_home")
    if tracking_feats is not None:
        feats = feats.join(tracking_feats, on=["game_id", "player_id"], how="left")
    if young_pick:
        feats = feats.join(
            young_pick_features(games, played, static, st, feats), on=["game_id", "player_id"]
        )
    if lineups_known:
        from nba.props.lineup_features import build_lineup_features

        t30 = build_lineup_features(games, pgs, scratch_prob=t30_scratch_prob).drop("team_id")
        feats = feats.join(t30, on=["game_id", "player_id"], how="left")
    return feats.sort(["game_date", "game_id", "player_id"])


#: F9 young-pick columns (appended, in this order, when ``young_pick`` is on).
YP_FEATURES: tuple[str, ...] = (
    "yp_pick",
    "yp_seasons",
    "yp_w",
    "yp_min_share",
    "yp_min_trend",
    "yp_pts_share",
    "yp_w_x_role",
)
#: A3 (role-only) arm: the seven columns without the pedigree ones.
YP_ROLE_ONLY: tuple[str, ...] = ("yp_seasons", "yp_min_share", "yp_min_trend", "yp_pts_share")
YP_DECAY: dict[int, float] = {1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25}
YP_TOP_PICK = 10
YP_T10_MIN_GAMES = 5


def yp_weight(seasons: np.ndarray, pick: np.ndarray) -> np.ndarray:
    """Pedigree weight ``{1:1, 2:.75, 3:.5, 4:.25, >=5:0} * 1[pick <= 10]`` (NaN pick -> 0)."""
    w = np.zeros(len(seasons))
    for k, v in YP_DECAY.items():
        w[seasons == k] = v
    return np.asarray(w * (np.nan_to_num(pick, nan=1e9) <= YP_TOP_PICK))


def young_pick_features(
    games: pl.DataFrame,
    played: pl.DataFrame,
    static: pl.DataFrame,
    states: dict[str, np.ndarray],
    feats: pl.DataFrame,
) -> pl.DataFrame:
    """The seven F9 columns per (game_id, player_id), all as-of the game date.

    * ``yp_seasons``: distinct seasons with a played game STRICTLY BEFORE tonight's season,
      plus 1. Counted from the loaded games only (the DB starts in 2022, so this is truncated
      for veterans; the frozen definition is used as written).
    * ``yp_pick``: static draft slot (known at the draft); NaN if undrafted/unknown.
    * ``yp_min_share = min10/240``, ``yp_min_trend = (min5 - min100)/48``,
      ``yp_pts_share = m10_pts / T10`` with ``T10`` the same team's points per game over its
      prior 10 games (shifted: tonight excluded).
    """
    if "draft_pick" not in static.columns:
        raise ValueError("young_pick needs static.draft_pick")
    ps = (
        played.select("player_id", "season")
        .unique()
        .sort(["player_id", "season"])
        .with_columns(pl.col("season").cum_count().over("player_id").alias("_k"))
    )  # 1-based rank of the season = prior distinct seasons + 1
    pk = static.select("player_id", pl.col("draft_pick").cast(pl.Float64).alias("yp_pick")).unique(
        "player_id"
    )
    min100 = pl.DataFrame(
        {
            "game_id": played["game_id"],
            "player_id": played["player_id"],
            "_min100": _sel(states["pre"], 100.0, "min"),
        }
    )
    base = (
        feats.select("game_id", "player_id", "team_id", "season", "min5", "min10", "m10_pts")
        .join(ps, on=["player_id", "season"], how="left")
        .join(pk, on="player_id", how="left")
        .join(min100, on=["game_id", "player_id"], how="left")
    )
    g = games.select("game_id", "game_date", "home_team", "away_team", "home_pts", "away_pts")
    tg = pl.concat(
        [
            g.select(
                "game_id",
                "game_date",
                pl.col("home_team").alias("team_id"),
                pl.col("home_pts").alias("pf"),
            ),
            g.select(
                "game_id",
                "game_date",
                pl.col("away_team").alias("team_id"),
                pl.col("away_pts").alias("pf"),
            ),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    tg = tg.with_columns(
        pl.col("pf")
        .shift(1)
        .rolling_mean(10, min_samples=YP_T10_MIN_GAMES)
        .over("team_id")
        .alias("t10")
    ).select("game_id", "team_id", "t10")
    base = base.join(tg, on=["game_id", "team_id"], how="left")
    seasons = base["_k"].to_numpy().astype(float)
    pick = base["yp_pick"].to_numpy()
    w = yp_weight(seasons, pick)
    share = base["min10"].to_numpy() / 240.0
    return base.select("game_id", "player_id").with_columns(
        pl.Series("yp_pick", pick),
        pl.Series("yp_seasons", seasons),
        pl.Series("yp_w", w),
        pl.Series("yp_min_share", share),
        pl.Series("yp_min_trend", (base["min5"].to_numpy() - base["_min100"].to_numpy()) / 48.0),
        pl.Series("yp_pts_share", base["m10_pts"].to_numpy() / base["t10"].to_numpy()),
        pl.Series("yp_w_x_role", w * share),
    )


COMMON_FEATURES: tuple[str, ...] = (
    "n_prior",
    "n_season",
    "gap_days",
    "since_flag",
    "flag_dir",
    "since_team",
    "min5",
    "min10",
    "min40",
    "min_gap",
    "min_gap10",
    "starter10",
    "starter40",
    "last_starter",
    "rest_days",
    "b2b",
    "team_game_no",
    "exp_margin",
    "abs_margin",
    "r20_pts_for",
    "r20_pts_against",
    "opp_rest_days",
    "opp_b2b",
    "opp_r20_pts_for",
    "opp_r20_pts_against",
    "n_out_rot",
    "vac_min",
    "has_report",
    "pos_code",
    "is_home_f",
)


def stat_feature_names(
    stat: str, extra: tuple[str, ...] = (), lineups_known: bool = False
) -> list[str]:
    """Production feature names for ``stat``; ``extra`` (default none) appends
    opt-in screen columns after the production ones. ``lineups_known=True`` appends
    the T-30 columns of :mod:`nba.props.lineup_features` (a leak at T-60)."""
    if lineups_known:
        from nba.props.lineup_features import T30_FEATURES

        extra = (*extra, *T30_FEATURES)
    own = [f"{p}_{s}" for s in PROP_STATS for p in ("m10",)]
    return [
        *COMMON_FEATURES,
        *own,
        f"m5_{stat}",
        f"m40_{stat}",
        f"m100_{stat}",
        f"sd10_{stat}",
        f"role_gap_{stat}",
        f"rate_{stat}",
        f"opp_allow_{stat}",
        f"vac_{stat}",
        *extra,
    ]


def stat_frame(feats: pl.DataFrame, stat: str, min_prior: int = MIN_PRIOR_PLAYED) -> pl.DataFrame:
    """Rows with enough played history, plus ``m`` (recency mean), ``s`` (recency
    std), ``y`` and ``resid``."""
    return feats.filter(pl.col("n_prior") >= min_prior).with_columns(
        pl.col(f"m10_{stat}").alias("m"),
        pl.col(f"sd10_{stat}").alias("s"),
        pl.col(stat).cast(pl.Float64).alias("y"),
        (pl.col(stat).cast(pl.Float64) - pl.col(f"m10_{stat}")).alias("resid"),
    )


# --------------------------------------------------------------------------- model


def _matrix(df: pl.DataFrame, names: list[str]) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()


def _params(cfg: ContextResidualConfig, objective: str = "regression") -> dict[str, object]:
    return {
        "objective": objective,
        "n_estimators": cfg.n_estimators,
        "learning_rate": cfg.learning_rate,
        "num_leaves": cfg.num_leaves,
        "min_child_samples": cfg.min_child_samples,
        "colsample_bytree": cfg.feature_fraction,
        "subsample": cfg.bagging_fraction,
        "subsample_freq": 1,
        "random_state": cfg.seed,
        "n_jobs": cfg.n_jobs,
        "verbose": -1,
        "deterministic": True,
        "force_row_wise": True,
    }


def split_calibration(
    train: pl.DataFrame, cfg: ContextResidualConfig
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(fit rows, calibration rows): the newest ``cal_frac`` of ``train`` (sorted by date),
    cut on a date boundary. Shared by the model fit and the tail diagnostics."""
    dates = train["game_date"].to_numpy()
    cut_idx = int(len(train) * (1.0 - cfg.cal_frac))
    cut_idx = min(cut_idx, max(len(train) - cfg.min_cal_rows, 1))
    cut_date = dates[cut_idx]
    return (
        train.filter(pl.col("game_date") < cut_date),
        train.filter(pl.col("game_date") >= cut_date),
    )


@dataclass
class ContextResidualModel:
    """Mean-residual head + residual-scale head + conformal standardized quantiles."""

    stat: str
    cfg: ContextResidualConfig
    names: list[str]
    mean_head: lgb.LGBMRegressor | None = None
    scale_head: lgb.LGBMRegressor | None = None
    z_sorted: np.ndarray | None = None
    z_base_sorted: np.ndarray | None = None
    #: calibration-window arrays (y, centre m+mu, scale), kept only when ``pts_tail != "off"``.
    cal_tail_: dict[str, np.ndarray] | None = None
    #: lower-tail state (cal arrays + short-minutes model); kept only when ``lower_tail != "off"``.
    lt_: dict[str, Any] | None = None
    importance_: dict[str, float] = field(default_factory=dict)

    def fit(self, train: pl.DataFrame) -> ContextResidualModel:
        """``train`` is a stat_frame sorted by date; the newest ``cal_frac`` of
        rows (cut on a date boundary) is the held-out calibration window."""
        fit_df, cal_df = split_calibration(train, self.cfg)
        if fit_df.height < 200 or cal_df.height < 50:
            raise ValueError("not enough rows to fit + calibrate")
        x_fit = _matrix(fit_df, self.names)
        r_fit = fit_df["resid"].to_numpy()
        self.mean_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x_fit, r_fit)  # type: ignore[arg-type]
        self.scale_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x_fit, np.abs(r_fit))  # type: ignore[arg-type]
        x_cal = _matrix(cal_df, self.names)
        mu = self.mean_head.predict(x_cal)
        s = self._scale(x_cal)
        z = (cal_df["resid"].to_numpy() - mu) / s
        self.z_sorted = np.sort(z)
        if self.cfg.pts_tail != "off" and self.stat in self.cfg.pts_tail_stats:
            self.cal_tail_ = {
                "y": cal_df["y"].to_numpy(),
                "c": cal_df["m"].to_numpy() + mu,
                "s": s,
                "z": z,
            }
        if self.cfg.lower_tail != "off" and self.stat in self.cfg.lower_tail_stats:
            self.lt_ = self._fit_lower_tail(train, cal_df, np.asarray(mu), s, np.asarray(z))
        self.z_base_sorted = np.sort(
            (cal_df["y"].to_numpy() - cal_df["m"].to_numpy()) / cal_df["s"].to_numpy()
        )
        gain = self.mean_head.booster_.feature_importance("gain")
        tot = float(gain.sum()) or 1.0
        self.importance_ = {n: float(g) / tot for n, g in zip(self.names, gain, strict=True)}
        return self

    def _fit_lower_tail(
        self,
        train: pl.DataFrame,
        cal_df: pl.DataFrame,
        mu: np.ndarray,
        s: np.ndarray,
        z: np.ndarray,
    ) -> dict[str, Any]:
        from nba.props.lower_tail import fit_short_state, short_flag

        if self.cfg.pts_tail != "off":
            raise ValueError("lower_tail and pts_tail are mutually exclusive")
        if "minutes" not in train.columns:
            raise ValueError("lower_tail needs a 'minutes' (realised) column in the training frame")
        min10 = cal_df["min10"].to_numpy()
        state: dict[str, Any] = {
            "z": z,
            "c": cal_df["m"].to_numpy() + mu,
            "min10": min10,
            "short": short_flag(cal_df["minutes"].to_numpy(), min10),
            "pi_model": None,
            "w_q": None,
        }
        if self.cfg.lower_tail == "mixture":
            tr_short = short_flag(train["minutes"].to_numpy(), train["min10"].to_numpy())
            state.update(
                fit_short_state(
                    _matrix(train, list(COMMON_FEATURES)),
                    tr_short,
                    train["y"].to_numpy(),
                    train["m"].to_numpy(),
                    CRPS_TAUS,
                    _params(self.cfg, "binary"),
                )
            )
        return state

    def lower_tail_quantiles(
        self, df: pl.DataFrame, c: np.ndarray, s: np.ndarray, taus: np.ndarray
    ) -> np.ndarray:
        """Quantiles of rows ``df`` (centre ``c``, scale ``s``) under ``cfg.lower_tail``."""
        from nba.props.lower_tail import lower_quantiles, predict_pi

        if self.lt_ is None:
            raise ValueError("model was fitted with lower_tail='off'; no lower-tail state kept")
        lt = self.lt_
        pi = (
            predict_pi(lt, _matrix(df, list(COMMON_FEATURES)))
            if self.cfg.lower_tail == "mixture"
            else None
        )
        return lower_quantiles(
            self.cfg.lower_tail,
            z_cal=lt["z"],
            c_cal=lt["c"],
            min10_cal=lt["min10"],
            short_cal=lt["short"],
            c=c,
            s=s,
            m_row=df["m"].to_numpy(),
            min10_row=df["min10"].to_numpy(),
            pi=pi,
            w_q=lt["w_q"],
            taus=taus,
        )

    def _scale(self, x: np.ndarray) -> np.ndarray:
        assert self.scale_head is not None
        return np.maximum(self.scale_head.predict(x), 0.3 * _DEFAULT_STD[self.stat] / 2.5)

    def predict(self, df: pl.DataFrame, taus: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (mean, quantiles[n, len(taus)]) of the stat, clipped at 0."""
        assert self.mean_head is not None and self.z_sorted is not None
        x = _matrix(df, self.names)
        mu = np.asarray(self.mean_head.predict(x))
        s = self._scale(x)
        m = df["m"].to_numpy()
        if self.cfg.pts_tail != "off" and self.stat in self.cfg.pts_tail_stats:
            q = self.tail_quantiles(self.cfg.pts_tail, m + mu, s, taus)
        elif self.lt_ is not None:
            q = self.lower_tail_quantiles(df, m + mu, s, taus)
        else:
            zq = np.asarray(np.quantile(self.z_sorted, taus))
            q = m[:, None] + mu[:, None] + s[:, None] * zq[None, :]
            q = np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)
        if self.cfg.integer_support and self.stat in self.cfg.integer_support_stats:
            q = to_integer_support(q)
        mean = np.clip(m + mu + s * float(self.z_sorted.mean()), 0.0, None)
        return mean, q

    def tail_quantiles(
        self, kind: str, c: np.ndarray, s: np.ndarray, taus: np.ndarray
    ) -> np.ndarray:
        """Quantiles of centre ``c`` / scale ``s`` rows under tail construction ``kind``."""
        from nba.props.pts_tail import tail_quantiles

        if self.cal_tail_ is None:
            raise ValueError("model was fitted with pts_tail='off'; no calibration arrays kept")
        cal = self.cal_tail_
        return tail_quantiles(
            kind,
            y_cal=cal["y"],
            c_cal=cal["c"],
            s_cal=cal["s"],
            z_cal=cal["z"],
            c=c,
            s=s,
            taus=taus,
        )

    def predict_components(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(centre ``m + mu``, scale) rows; building block for tail diagnostics."""
        assert self.mean_head is not None
        x = _matrix(df, self.names)
        return df["m"].to_numpy() + np.asarray(self.mean_head.predict(x)), self._scale(x)

    def predict_baseline_cal(
        self, df: pl.DataFrame, taus: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Diagnostic: recency mean/std with the SAME conformal standardized
        quantiles (isolates calibration/shape gains from context features)."""
        assert self.z_base_sorted is not None
        m, s = df["m"].to_numpy(), df["s"].to_numpy()
        zq = np.quantile(self.z_base_sorted, taus)
        q = np.maximum.accumulate(np.clip(m[:, None] + s[:, None] * zq[None, :], 0.0, None), axis=1)
        return np.clip(m + s * float(self.z_base_sorted.mean()), 0.0, None), q


# --------------------------------------------------------------------------- forward API


def fit_for_date(
    feats: pl.DataFrame,
    as_of: dt.date,
    cfg: ContextResidualConfig | None = None,
    stats: tuple[str, ...] = PROP_STATS,
    lineups_known: bool = False,
) -> dict[str, ContextResidualModel]:
    """Fit one model per stat on PLAYED rows with ``game_date`` strictly before
    ``as_of`` (a ``datetime.date``). ``feats`` is :func:`build_features` output;
    rows on/after ``as_of`` are never used for fitting, even if present.
    ``lineups_known=True`` (default False = production) fits on the T-30 feature set
    (``feats`` must come from ``build_features(..., lineups_known=True)``)."""
    cfg = cfg or ContextResidualConfig()
    hist = feats.filter(pl.col("game_date") < as_of)
    models: dict[str, ContextResidualModel] = {}
    for stat in stats:
        train = stat_frame(hist, stat).sort(["game_date", "game_id", "player_id"])
        extra = YP_FEATURES if cfg.young_pick == "on" else ()
        models[stat] = ContextResidualModel(
            stat, cfg, stat_feature_names(stat, extra=extra, lineups_known=lineups_known)
        ).fit(train)
    return models


def predict_rows(
    models: dict[str, ContextResidualModel],
    feats: pl.DataFrame,
    stat: str,
    taus: np.ndarray,
) -> tuple[pl.DataFrame, np.ndarray, np.ndarray]:
    """Predict the rows of ``feats`` (as-of feature rows, e.g. tonight's players)
    that have >= ``MIN_PRIOR_PLAYED`` prior played games. Returns the eligible
    rows (``stat_frame`` output, in order), the clipped means, and the
    quantiles ``[n, len(taus)]``."""
    rows = stat_frame(feats, stat)
    if rows.is_empty():
        return rows, np.empty(0), np.empty((0, len(taus)))
    mean, q = models[stat].predict(rows, taus)
    return rows, mean, q
