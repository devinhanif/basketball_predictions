"""Per-game player SETS for the joint game-set model: strictly as-of tokens, labels, PIT targets.

Used by ``nba/colab/jobs/joint_game_set`` (a set transformer over every rostered player of a game
plus a game-context token; see ``docs/JOINT_GAME_SET.md`` for the design and the PRE-REGISTERED
decision rule). This module builds the training/eval arrays; it never trains anything.

One game = one set:
  * PLAYER TOKENS: every box-score roster row of both teams (played AND DNP rows, up to
    ``MAX_PER_TEAM`` per team). Token features are identical in kind for every row, whether or not
    the player played, so the set can never reveal who actually played (the played flag, the
    minutes and every stat of the game are LABELS only).
  * GAME TOKEN: injury-Elo win probability, the ``nba.parlay.game_model`` margin/total
    distribution parameters, rest / back-to-back / games played, playoff flag, season phase.
  * LABELS: realized pts/reb/ast/fg3m per played player, game margin and total, the played flag.

Targets live in NORMAL-SCORE (PIT) space relative to a marginal that was itself available pre-game:
  src 1  (seasons 2023-24) the production context-residual quantile grid (walk-forward OOF);
  src 0  (season 2022, optional pre-training) a recency Normal(mean, sd) built here as-of.
  The randomized PIT bounds ``(F(y - 0.5), F(y + 0.5))`` are exported (the job re-randomizes every
  epoch) together with a fixed-seed ``z_fixed`` used for evaluation.
  The production OOF exists for PLAYED rows only, so it is used ONLY to standardize labels (it is a
  deterministic function of pre-game information evaluated at the realized outcome). It is never a
  token feature: a feature present only for players who played would leak who played.

No-leakage (same discipline as ``nba.features.player_possession_features``): every history
feature at a player row is computed from that player's rows on STRICTLY EARLIER dates (played-only
exponential states are looked up with ``join_asof(..., allow_exact_matches=False)``; row-count
windows use ``shift(1)``). Pre-tip injury-report status comes from
``nba.props.context_features_v2.pretip_status`` (latest report stamped before the tip-off proxy).
Seasons above 2024 are refused (the 2025 holdout is never loaded). Tests:
``tests/features/test_game_sets.py`` (planted-future-game and played-flag invariance proofs).

Residual risk, documented: the roster ROW LIST of a game (who has a box-score row, including
inactive and two-way players) is taken from the realized box score. It is the game-day roster and
is very nearly known pre-tip, but it is not point-in-time. Feature VALUES do not depend on it.
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy import stats

from nba.parlay.game_model import (
    FIT_MAX_SEASON,
    GameModelParams,
    asof_total_features,
    fit_game_model,
    logit,
)
from nba.parlay.qdist import QuantileGridDist
from nba.props.baselines import _DEFAULT_STD
from nba.props.context_features_v2 import OUT_ORD, absence_features, pretip_status
from nba.sim.usage_redistribution import ReportTriggerConfig

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
EXPORT_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
MAX_SEASON = 2024  # season 2025 is never loaded
MAX_PER_TEAM = 16  # real max is 16 box-score rows per team in 2022-24
N_TOK = 2 * MAX_PER_TEAM
SEED = 20261008
HL_FAST, HL_MID, HL_SLOW = 3.0, 10.0, 40.0
MIN_PRIOR_PLAYED = 5  # production OOF convention; also gates the 2022 recency-Normal marginal
DEFAULT_SD_PRIOR_K = 5.0
PLAY_PRIOR = 0.8  # smoothed play rate prior (same as nba.eval.fair_rematch)
PLAY_PRIOR_K = 3.0
GAP_CAP = 90.0
MODEL_NAME_ELO = "rung0_injury_elo"
TAIL_W = 0.01  # PIT mass assumed for outcomes beyond the grid ends (see ``randomized_z``)

#: per-token feature columns (order is the array layout; counts/gaps already log1p'd or capped)
TOK_FEATURES: tuple[str, ...] = (
    "is_home",
    "log_n_rows",
    "log_n_played",
    "log_n_played_season",
    "has_hist",
    "debut",
    "play5",
    "play10",
    "play20",
    "gap_days",
    "days_since_played",
    "min_ew3",
    "min_ew10",
    "min_ew40",
    "min_sd",
    "starter_ew10",
    "last_starter",
    *[f"{k}_ew10" for k in STATS],
    *[f"{k}_ew40" for k in STATS],
    *[f"{k}_sd" for k in STATS],
    *[f"{k}_r36" for k in STATS],
    "has_report",
    "own_listed",
    "own_status_ord",
    "absent_prev",
    "abs_streak_prev",
    "since_return",
    "last_abs_len",
    "own_returning",
    "team_out_min",
    "team_flag_min",
    "pos_g",
    "pos_f",
    "pos_c",
    "height_in",
    "height_missing",
    "ln_pick",
    "undrafted",
    "yrs_since_draft",
)
#: per-game feature columns
GAME_FEATURES: tuple[str, ...] = (
    "logit_p_home",
    "mu_margin",
    "sd_margin",
    "mu_total",
    "sd_total",
    "rest_home",
    "rest_away",
    "b2b_home",
    "b2b_away",
    "gn_home",
    "gn_away",
    "is_playoff",
    "season_phase",
)


@dataclass(frozen=True)
class GameSetConfig:
    seed: int = SEED
    max_games: int | None = None
    z_clip: float = 1e-6


# --------------------------------------------------------------------------- history features


def _ewm(col: str, half_life: float) -> pl.Expr:
    return pl.col(col).ewm_mean(half_life=half_life, ignore_nulls=True).over("player_id")


def history_features(pgs: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """As-of player history for EVERY ``pgs`` row (played and DNP).

    Row-count windows look at the player's previous rows only (``shift(1)``); played-only
    exponential states are looked up as of the last played game dated strictly before the row's
    date. Columns: ``game_id``, ``player_id``, ``team_id`` plus the history subset of
    ``TOK_FEATURES``.
    """
    g = "player_id"
    base = (
        pgs.select("game_id", "player_id", "team_id", "minutes", "starter", *STATS)
        .join(games.select("game_id", "game_date", "season"), on="game_id")
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .sort([g, "game_date", "game_id"])
        .with_columns(
            (pl.col("minutes").fill_null(0.0) > 0.0).cast(pl.Float64).alias("_pl"),
            pl.col("minutes").fill_null(0.0).alias("_min"),
            pl.col("starter").fill_null(False).cast(pl.Float64).alias("_st"),
            pl.lit(1.0).alias("_one"),
        )
    )
    # row-count windows (rows = box-score rows including DNP), strictly previous rows
    base = base.with_columns(
        pl.int_range(pl.len()).over(g).alias("n_rows_prior"),
        pl.col("_pl").cum_sum().over(g).alias("_cum_pl"),
        pl.col("_pl").cum_sum().over([g, "season"]).alias("_cum_pl_s"),
        *[pl.col("_pl").rolling_sum(k, min_samples=1).over(g).alias(f"_s{k}") for k in (5, 10, 20)],
        *[
            pl.col("_one").rolling_sum(k, min_samples=1).over(g).alias(f"_c{k}")
            for k in (5, 10, 20)
        ],
        pl.col("game_date").shift(1).over(g).alias("_prev_date"),
    ).with_columns(
        pl.col("_cum_pl").shift(1).over(g).fill_null(0.0).alias("n_played_prior"),
        pl.col("_cum_pl_s").shift(1).over([g, "season"]).fill_null(0.0).alias("_nps"),
        *[pl.col(f"_s{k}").shift(1).over(g).fill_null(0.0).alias(f"_s{k}p") for k in (5, 10, 20)],
        *[pl.col(f"_c{k}").shift(1).over(g).fill_null(0.0).alias(f"_c{k}p") for k in (5, 10, 20)],
        (pl.col("game_date") - pl.col("_prev_date"))
        .dt.total_days()
        .fill_null(int(GAP_CAP))
        .clip(0, int(GAP_CAP))
        .cast(pl.Float64)
        .alias("gap_days"),
    )
    base = base.with_columns(
        *[
            ((pl.col(f"_s{k}p") + PLAY_PRIOR * PLAY_PRIOR_K) / (pl.col(f"_c{k}p") + PLAY_PRIOR_K))
            .clip(0.0, 1.0)
            .alias(f"play{k}")
            for k in (5, 10, 20)
        ],
        (pl.col("n_rows_prior") + 1.0).log().alias("log_n_rows"),
        (pl.col("n_played_prior") + 1.0).log().alias("log_n_played"),
        (pl.col("_nps") + 1.0).log().alias("log_n_played_season"),
        (pl.col("n_played_prior") > 0).cast(pl.Float64).alias("has_hist"),
        (pl.col("n_rows_prior") == 0).cast(pl.Float64).alias("debut"),
    )
    # played-only exponential states, including the played row itself (looked up strictly after)
    ps = (
        base.filter(pl.col("_pl") > 0)
        .with_columns(
            _ewm("_min", HL_FAST).alias("min_ew3"),
            _ewm("_min", HL_MID).alias("min_ew10"),
            _ewm("_min", HL_SLOW).alias("min_ew40"),
            pl.col("_min")
            .ewm_std(half_life=HL_MID, ignore_nulls=True, min_samples=2)
            .over(g)
            .alias("min_sd"),
            _ewm("_st", HL_MID).alias("starter_ew10"),
            pl.col("_st").alias("last_starter"),
            *[_ewm(k, HL_MID).alias(f"{k}_ew10") for k in STATS],
            *[_ewm(k, HL_SLOW).alias(f"{k}_ew40") for k in STATS],
            *[
                pl.col(k)
                .cast(pl.Float64)
                .ewm_std(half_life=HL_MID, ignore_nulls=True, min_samples=2)
                .over(g)
                .alias(f"{k}_sd")
                for k in STATS
            ],
            pl.col("game_date").alias("_last_played"),
        )
        .select(
            g,
            "game_date",
            "_last_played",
            "min_ew3",
            "min_ew10",
            "min_ew40",
            "min_sd",
            "starter_ew10",
            "last_starter",
            *[f"{k}_ew10" for k in STATS],
            *[f"{k}_ew40" for k in STATS],
            *[f"{k}_sd" for k in STATS],
        )
        .sort("game_date")
    )
    with warnings.catch_warnings():  # sortedness is not checkable with ``by``; sorted above
        warnings.simplefilter("ignore", UserWarning)
        out = base.sort("game_date").join_asof(
            ps, on="game_date", by=g, strategy="backward", allow_exact_matches=False
        )
    sd_floor = {k: _DEFAULT_STD[k] for k in STATS}
    out = out.with_columns(
        (pl.col("game_date") - pl.col("_last_played"))
        .dt.total_days()
        .fill_null(int(GAP_CAP))
        .clip(0, int(GAP_CAP))
        .cast(pl.Float64)
        .alias("days_since_played"),
        # sd: shrink the exponential sd toward the league default by the played-game count
        *[
            (
                (
                    pl.col("n_played_prior").clip(0.0, 40.0)
                    * pl.col(f"{k}_sd").fill_null(sd_floor[k])
                    + DEFAULT_SD_PRIOR_K * sd_floor[k]
                )
                / (pl.col("n_played_prior").clip(0.0, 40.0) + DEFAULT_SD_PRIOR_K)
            ).alias(f"{k}_sd")
            for k in STATS
        ],
        pl.col("min_sd").fill_null(6.0).alias("min_sd"),
    )
    fill0 = [
        "min_ew3",
        "min_ew10",
        "min_ew40",
        "starter_ew10",
        "last_starter",
        *[f"{k}_ew10" for k in STATS],
        *[f"{k}_ew40" for k in STATS],
    ]
    out = out.with_columns(
        *[pl.col(c).fill_null(0.0) for c in fill0],
        *[
            (pl.col(f"{k}_ew10") / pl.col("min_ew10").clip(1.0, None) * 36.0).alias(f"{k}_r36")
            for k in STATS
        ],
    )
    keep = [
        "game_id",
        "player_id",
        "team_id",
        "n_played_prior",
        "n_rows_prior",
        *[
            c
            for c in TOK_FEATURES
            if c
            in (
                "log_n_rows",
                "log_n_played",
                "log_n_played_season",
                "has_hist",
                "debut",
                "play5",
                "play10",
                "play20",
                "gap_days",
                "days_since_played",
                "min_ew3",
                "min_ew10",
                "min_ew40",
                "min_sd",
                "starter_ew10",
                "last_starter",
            )
            or c.endswith(("_ew10", "_ew40", "_sd", "_r36"))
        ],
    ]
    cols = list(dict.fromkeys(keep))
    return out.select(cols)


def static_features(rows: pl.DataFrame, static: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Static public facts (position flags, height, draft slot) joined onto ``rows``
    (``game_id``, ``player_id``). Same kind of value for played and DNP rows."""
    if "height_in" not in static.columns:
        static = static.with_columns(pl.lit(None, dtype=pl.Float64).alias("height_in"))
    s = static.select(
        pl.col("player_id").cast(pl.Int64),
        pl.col("position").fill_null("").str.to_lowercase().alias("_pos"),
        pl.col("height_in").cast(pl.Float64),
        pl.col("draft_year").cast(pl.Float64),
        pl.col("draft_pick").cast(pl.Float64),
    )
    gy = games.select("game_id", (pl.col("season")).cast(pl.Float64).alias("_season"))
    out = (
        rows.select("game_id", pl.col("player_id").cast(pl.Int64))
        .join(gy, on="game_id", how="left")
        .join(s, on="player_id", how="left")
    )
    return out.with_columns(
        pl.col("_pos").str.contains("guard").cast(pl.Float64).alias("pos_g"),
        pl.col("_pos").str.contains("forward").cast(pl.Float64).alias("pos_f"),
        pl.col("_pos").str.contains("center").cast(pl.Float64).alias("pos_c"),
        pl.col("height_in").is_null().cast(pl.Float64).alias("height_missing"),
        pl.col("height_in").fill_null(78.0),
        pl.col("draft_pick").fill_null(61.0).clip(1.0, 61.0).log().alias("ln_pick"),
        (pl.col("draft_pick").is_null() | pl.col("draft_year").is_null())
        .cast(pl.Float64)
        .alias("undrafted"),
        (pl.col("_season") - pl.col("draft_year"))
        .fill_null(0.0)
        .clip(0.0, 25.0)
        .alias("yrs_since_draft"),
    ).select(
        "game_id",
        "player_id",
        "pos_g",
        "pos_f",
        "pos_c",
        "height_in",
        "height_missing",
        "ln_pick",
        "undrafted",
        "yrs_since_draft",
    )


def status_features(
    pgs: pl.DataFrame, games: pl.DataFrame, avail: pl.DataFrame, cfg: ReportTriggerConfig
) -> pl.DataFrame:
    """Pre-tip report status + absence history for every ``pgs`` row (DNP rows included).

    Wraps the production ``absence_features`` (streaks use PRIOR rows only; tonight's status is
    the latest report stamped before the tip-off proxy). ``has_report`` = the game has a usable
    report at all; without one the status columns are 0 (never imputed from the box score)."""
    status, reported = pretip_status(avail, games, cfg)
    f = absence_features(pgs, games, status, reported)
    cols = [
        "own_listed",
        "own_status_ord",
        "absent_prev",
        "abs_streak_prev",
        "since_return",
        "last_abs_len",
        "own_returning",
    ]
    return f.with_columns(
        pl.col("game_id").is_in(sorted(reported)).cast(pl.Float64).alias("has_report"),
        *[pl.col(c).cast(pl.Float64).fill_null(0.0) for c in cols],
    ).select("game_id", "player_id", "has_report", *cols)


def token_frame(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    cfg: ReportTriggerConfig | None = None,
) -> pl.DataFrame:
    """One row per box-score roster row: keys, token features (``TOK_FEATURES``) and labels.

    Labels (``played``, ``pts``..``fg3m``) are never used by any feature column."""
    cfg = cfg or ReportTriggerConfig()
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError(f"season > {MAX_SEASON} present: the frozen holdout must not be loaded")
    hist = history_features(pgs, games)
    stat = static_features(pgs.select("game_id", "player_id"), static, games)
    sts = status_features(pgs, games, avail, cfg)
    lab = pgs.select(
        "game_id",
        pl.col("player_id").cast(pl.Int64),
        (pl.col("minutes").fill_null(0.0) > 0.0).cast(pl.Float64).alias("played"),
        *[pl.col(k).fill_null(0).cast(pl.Float64).alias(f"y_{k}") for k in STATS],
    )
    gh = games.select(
        "game_id",
        "home_team",
        "game_date",
        "season",
    )
    df = (
        hist.join(stat, on=["game_id", "player_id"], how="left")
        .join(sts, on=["game_id", "player_id"], how="left")
        .join(lab, on=["game_id", "player_id"], how="left")
        .join(gh, on="game_id", how="left")
        .with_columns((pl.col("team_id") == pl.col("home_team")).cast(pl.Float64).alias("is_home"))
    )
    # teammate availability context (pre-tip status x as-of minutes), excluding the row itself
    out_min = (
        pl.when(pl.col("own_status_ord") == float(OUT_ORD)).then(pl.col("min_ew10")).otherwise(0.0)
    )
    flag_min = (
        pl.when((pl.col("own_status_ord") >= 3.0) & (pl.col("own_status_ord") < float(OUT_ORD)))
        .then(pl.col("min_ew10"))
        .otherwise(0.0)
    )
    df = df.with_columns(out_min.alias("_om"), flag_min.alias("_fm")).with_columns(
        (pl.col("_om").sum().over(["game_id", "team_id"]) - pl.col("_om")).alias("team_out_min"),
        (pl.col("_fm").sum().over(["game_id", "team_id"]) - pl.col("_fm")).alias("team_flag_min"),
    )
    return df.drop("_om", "_fm")


# --------------------------------------------------------------------------- game context


def game_frame(games: pl.DataFrame, elo: pl.DataFrame, params: GameModelParams) -> pl.DataFrame:
    """One row per played game with the game-token features and the margin/total targets.

    ``elo``: game_id, p (pre-game injury-Elo home win probability). Margin/total distribution
    parameters come from ``params`` (``fit_game_model`` excluding season 2024, so 2024 is a
    genuinely out-of-fit season; the fit is a 2-parameter OLS per quantity)."""
    feats = asof_total_features(games.filter(pl.col("season") <= FIT_MAX_SEASON), 20.0, 8.0)
    g = (
        games.filter(
            (pl.col("season") <= MAX_SEASON)
            & pl.col("home_pts").is_not_null()
            & (pl.col("home_pts") > 0)
        )
        .join(elo.select("game_id", "p"), on="game_id")
        .join(feats, on="game_id", how="left")
        .sort(["game_date", "game_id"])
    )
    # team rest / games so far (strictly earlier games only)
    long = pl.concat(
        [
            g.select(
                "game_id", "game_date", "season", pl.col("home_team").alias("team")
            ).with_columns(pl.lit(1).alias("side")),
            g.select(
                "game_id", "game_date", "season", pl.col("away_team").alias("team")
            ).with_columns(pl.lit(0).alias("side")),
        ]
    ).sort(["team", "game_date", "game_id"])
    long = long.with_columns(
        (pl.col("game_date") - pl.col("game_date").shift(1).over("team"))
        .dt.total_days()
        .fill_null(7)
        .clip(0, 10)
        .cast(pl.Float64)
        .alias("rest"),
        pl.int_range(pl.len()).over(["team", "season"]).cast(pl.Float64).alias("gn"),
    )
    home = long.filter(pl.col("side") == 1).select(
        "game_id", pl.col("rest").alias("rest_home"), pl.col("gn").alias("gn_home")
    )
    away = long.filter(pl.col("side") == 0).select(
        "game_id", pl.col("rest").alias("rest_away"), pl.col("gn").alias("gn_away")
    )
    # joins do not guarantee row order: re-sort, THEN read arrays that are attached by position
    g = g.join(home, on="game_id").join(away, on="game_id").sort(["game_date", "game_id"])
    p = np.asarray(g["p"].to_numpy(), dtype=float)
    tf = g["total_feat"].to_numpy().astype(float)
    tf = np.where(np.isnan(tf), params.league_total, tf)
    mu_m = params.margin_b + params.margin_s * np.asarray(logit(p), dtype=float)
    mu_t = params.total_a + params.total_c * tf
    doy = (
        (g["game_date"].dt.ordinal_day().to_numpy().astype(float) - 274.0) % 365.0
    ) / 365.0  # days since ~Oct 1
    return g.with_columns(
        pl.Series("mu_margin", mu_m),
        pl.Series("sd_margin", np.full(g.height, params.margin_sd)),
        pl.Series("mu_total", mu_t),
        pl.Series("sd_total", np.full(g.height, params.total_sd)),
        pl.Series("logit_p_home", np.asarray(logit(p), dtype=float)),
        (pl.col("rest_home") == 1.0).cast(pl.Float64).alias("b2b_home"),
        (pl.col("rest_away") == 1.0).cast(pl.Float64).alias("b2b_away"),
        (pl.col("gn_home") / 82.0).alias("gn_home"),
        (pl.col("gn_away") / 82.0).alias("gn_away"),
        pl.col("game_id").str.slice(2, 1).eq("4").cast(pl.Float64).alias("is_playoff"),
        pl.Series("season_phase", doy),
        (pl.col("home_pts") - pl.col("away_pts")).cast(pl.Float64).alias("margin"),
        (pl.col("home_pts") + pl.col("away_pts")).cast(pl.Float64).alias("total"),
    )


# --------------------------------------------------------------------------- PIT targets


def grid_pit_bounds(q_grid: list[float], y: float) -> tuple[float, float]:
    """``(F(y - 0.5), F(y + 0.5))`` of the production quantile-grid CDF."""
    return QuantileGridDist.from_grid(q_grid).pit_bounds(y)


def normal_pit_bounds(
    y: np.ndarray, mu: np.ndarray, sd: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """PIT bounds of a Normal(mu, sd) count marginal with all mass below 0.5 folded onto y = 0."""
    hi = stats.norm.cdf((y + 0.5 - mu) / sd)
    lo = np.where(y <= 0.0, 0.0, stats.norm.cdf((y - 0.5 - mu) / sd))
    return np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)


def randomized_z(
    lo: np.ndarray,
    hi: np.ndarray,
    u: np.ndarray,
    clip: float,
    u_tail: np.ndarray | None = None,
    tail_w: float = TAIL_W,
) -> np.ndarray:
    """Normal score of a randomized PIT ``v = lo + u * (hi - lo)``.

    An outcome beyond the extended quantile-grid ends has ``lo == hi == 1`` (or ``== 0`` for a
    non-zero count below the lower end): its true PIT is unknown but certainly in the extreme
    tail. Instead of a point mass at the clip value (a degenerate target for a density model) the
    PIT is spread uniformly over the outer ``tail_w`` of the unit interval (~2% of outcomes)."""
    v = lo + u * (hi - lo)
    if u_tail is not None:
        top = lo >= 1.0 - 1e-12
        bot = (hi <= 1e-12) & ~top
        v = np.where(top, 1.0 - tail_w * u_tail, v)
        v = np.where(bot, tail_w * u_tail, v)
    return np.asarray(stats.norm.ppf(np.clip(v, clip, 1.0 - clip)), dtype=np.float32)


# --------------------------------------------------------------------------- assembly


@dataclass
class GameSets:
    """Padded arrays for every game (``G`` games, ``N_TOK`` token slots, 4 stats)."""

    arrays: dict[str, np.ndarray]
    meta: dict[str, Any]

    def save(self, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        npz, js = out / "game_sets.npz", out / "game_sets_meta.json"
        tmp = out / "game_sets.tmp.npz"
        np.savez_compressed(str(tmp), **self.arrays)  # type: ignore[arg-type]
        tmp.replace(npz)
        js.write_text(json.dumps(self.meta, indent=1, sort_keys=True, default=str))
        return npz, js

    @staticmethod
    def load(out_dir: str | Path) -> GameSets:
        out = Path(out_dir)
        with np.load(out / "game_sets.npz", allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        meta = json.loads((out / "game_sets_meta.json").read_text())
        return GameSets(arrays, meta)


def build_game_sets(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    ctx_oof: pl.DataFrame,
    elo: pl.DataFrame,
    params: GameModelParams,
    cfg: GameSetConfig | None = None,
) -> GameSets:
    """Assemble padded arrays. ``ctx_oof``: production OOF (game_id, player_id, target, q_grid)."""
    cfg = cfg or GameSetConfig()
    gf = game_frame(games, elo, params)
    if cfg.max_games is not None:
        gf = gf.head(cfg.max_games)
    tok = token_frame(games.filter(pl.col("season") <= MAX_SEASON), pgs, static, avail)
    tok = tok.join(gf.select("game_id"), on="game_id", how="semi")
    # token order inside a team: as-of minutes (a pre-game quantity), then player id for ties
    tok = tok.sort(
        ["game_id", "is_home", "min_ew10", "player_id"], descending=[False, True, True, False]
    ).with_columns(
        pl.int_range(pl.len()).over(["game_id", "team_id"]).alias("_slot_in_team"),
    )
    tok = tok.filter(pl.col("_slot_in_team") < MAX_PER_TEAM)
    tok = tok.with_columns(
        (pl.col("_slot_in_team") + (1 - pl.col("is_home").cast(pl.Int64)) * MAX_PER_TEAM).alias(
            "slot"
        )
    )
    gid = gf["game_id"].to_list()
    gidx = {g: i for i, g in enumerate(gid)}
    n_g = len(gid)
    ti = np.array([gidx[g] for g in tok["game_id"].to_list()], dtype=np.int64)
    ts = tok["slot"].to_numpy().astype(np.int64)

    def scat(values: np.ndarray, fill: float, dtype: Any = np.float32) -> np.ndarray:
        shape = (n_g, N_TOK) + values.shape[1:]
        a = np.full(shape, fill, dtype=dtype)
        a[ti, ts] = values
        return a

    x = np.stack([tok[c].to_numpy().astype(np.float32) for c in TOK_FEATURES], axis=1)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    played = tok["played"].to_numpy()
    y = np.stack([tok[f"y_{k}"].to_numpy() for k in STATS], axis=1).astype(np.float32)
    pid = tok["player_id"].to_numpy().astype(np.int64)
    # --- PIT bounds
    lo = np.full((len(tok), 4), np.nan, dtype=np.float32)
    hi = np.full((len(tok), 4), np.nan, dtype=np.float32)
    src = np.zeros(len(tok), dtype=np.int8)
    season = tok["season"].to_numpy()
    key = {
        (g, int(p)): i for i, (g, p) in enumerate(zip(tok["game_id"].to_list(), pid, strict=True))
    }
    oof_rows = ctx_oof.select("game_id", "player_id", "target", "q_grid")
    n_oof = 0
    for r in oof_rows.iter_rows():
        i = key.get((r[0], int(r[1])))
        if i is None or r[2] not in STATS or played[i] <= 0:
            continue
        k = STATS.index(r[2])
        lo[i, k], hi[i, k] = grid_pit_bounds(r[3], float(y[i, k]))
        src[i] = 1
        n_oof += 1
    npl = tok["n_played_prior"].to_numpy()
    for k, name in enumerate(STATS):
        # 2022 (no production OOF exists): recency Normal built from the as-of EW mean and sd
        m22 = (season == 2022) & (played > 0) & (npl >= MIN_PRIOR_PLAYED)
        if m22.any():
            mu = tok[f"{name}_ew10"].to_numpy()[m22]
            sd = np.maximum(tok[f"{name}_sd"].to_numpy()[m22], 0.5)
            l_, h_ = normal_pit_bounds(y[m22, k].astype(float), mu, sd)
            lo[m22, k], hi[m22, k] = l_, h_
    obs = ~np.isnan(lo)
    rng = np.random.default_rng(cfg.seed)
    u = rng.random(lo.shape)
    u_tail = rng.random(lo.shape)
    z = np.where(
        obs, randomized_z(np.nan_to_num(lo), np.nan_to_num(hi), u, cfg.z_clip, u_tail), 0.0
    )
    game_x = np.stack([gf[c].to_numpy().astype(np.float32) for c in GAME_FEATURES], axis=1)
    mu_m = gf["mu_margin"].to_numpy().astype(np.float64)
    sd_m = gf["sd_margin"].to_numpy().astype(np.float64)
    mu_t = gf["mu_total"].to_numpy().astype(np.float64)
    sd_t = gf["sd_total"].to_numpy().astype(np.float64)
    margin = gf["margin"].to_numpy().astype(np.float64)
    total = gf["total"].to_numpy().astype(np.float64)
    dates = gf["game_date"].to_numpy().astype("datetime64[D]").astype(np.int32)
    arrays: dict[str, np.ndarray] = {
        "tok_x": scat(x, 0.0),
        "tok_mask": scat(np.ones(len(tok), dtype=bool), False, np.bool_),
        "tok_pid": scat(pid, -1, np.int64),
        "tok_team": scat(tok["team_id"].to_numpy().astype(np.int64), -1, np.int64),
        "tok_side": scat(tok["is_home"].to_numpy().astype(np.int8), 0, np.int8),
        "tok_played": scat(played.astype(np.int8), 0, np.int8),
        "tok_src": scat(src, 0, np.int8),
        "tok_y": scat(y, 0.0),
        "tok_pit_lo": scat(np.nan_to_num(lo, nan=0.0), 0.0),
        "tok_pit_hi": scat(np.nan_to_num(hi, nan=0.0), 0.0),
        "tok_obs": scat(obs, False, np.bool_),
        "tok_z": scat(z, 0.0),
        "game_id": np.array(gid),
        "game_x": game_x,
        "game_date": dates,
        "season": gf["season"].to_numpy().astype(np.int16),
        "home_team": gf["home_team"].to_numpy().astype(np.int64),
        "margin": margin,
        "total": total,
        "mu_margin": mu_m,
        "sd_margin": sd_m,
        "mu_total": mu_t,
        "sd_total": sd_t,
        "z_margin": (margin - mu_m) / sd_m,
        "z_total": (total - mu_t) / sd_t,
    }
    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "tok_features": list(TOK_FEATURES),
        "game_features": list(GAME_FEATURES),
        "stats": list(STATS),
        "n_games": n_g,
        "n_tokens": int(len(tok)),
        "n_tokens_played": int(played.sum()),
        "n_obs_dims": int(obs.sum()),
        "n_oof_tokens": int(n_oof),
        "seasons": sorted({int(s) for s in set(gf["season"].to_list())}),
        "n_games_by_season": {
            str(s): int(c)
            for s, c in zip(*np.unique(arrays["season"], return_counts=True), strict=True)
        },
        "max_per_team": MAX_PER_TEAM,
        "seed": cfg.seed,
        "params": json.loads(params.to_json()),
        "src_legend": {"0": "recency Normal (2022, pre-train only)", "1": "production OOF grid"},
    }
    return GameSets(arrays, meta)


def load_db_inputs(
    db_path: str | Path,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, pgs, static, avail) for seasons <= 2024; read-only; modest memory."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        con.execute("SET memory_limit='2GB'")
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.starter, s.pts, s.reb, "
            "s.ast, s.fg3m FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON}"
        ).pl()
        cols = {r[0] for r in con.execute("DESCRIBE players_static").fetchall()}
        want = [
            c
            for c in ("player_id", "position", "height_in", "draft_year", "draft_pick")
            if c in cols
        ]
        static = con.execute(f"SELECT {', '.join(want)} FROM players_static").pl()
        avail = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.as_of, a.source FROM player_availability a "
            f"JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    return games, pgs, static, avail


def export_game_sets(
    db_path: str | Path,
    ctx_oof: str | Path,
    elo_oof: str | Path,
    out_dir: str | Path,
    *,
    max_games: int | None = None,
    seed: int = SEED,
) -> GameSets:
    """Build and write ``game_sets.npz`` + ``game_sets_meta.json`` (seasons 2022-2024 only)."""
    games, pgs, static, avail = load_db_inputs(db_path)
    elo = pl.read_parquet(elo_oof).filter(pl.col("model") == MODEL_NAME_ELO).select("game_id", "p")
    oof = pl.read_parquet(ctx_oof, columns=["game_id", "player_id", "target", "q_grid", "season"])
    oof = oof.filter(pl.col("season") <= MAX_SEASON)
    params = fit_game_model(games, elo, exclude_seasons=(2024,))
    gs = build_game_sets(
        games, pgs, static, avail, oof, elo, params, GameSetConfig(seed=seed, max_games=max_games)
    )
    gs.save(out_dir)
    return gs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--ctx-oof", default="reports/context_residual/oof_context_residual.parquet")
    ap.add_argument("--elo-oof", default="data/injury_elo/oof_predictions.parquet")
    ap.add_argument("--out-dir", default="data/colab/joint_game_set")
    ap.add_argument("--max-games", type=int, default=None)
    a = ap.parse_args(argv)
    gs = export_game_sets(a.db, a.ctx_oof, a.elo_oof, a.out_dir, max_games=a.max_games)
    print(
        json.dumps({k: gs.meta[k] for k in ("n_games", "n_tokens", "n_obs_dims", "n_oof_tokens")})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
