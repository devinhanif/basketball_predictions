"""T-30 ("lineups known") features for the props models.

LEAK WARNING. Every column built here uses TONIGHT's confirmed starting five,
which is public roughly 30 minutes before tip-off (T-30) but NOT at the
production prediction time T-60. These features are a leak if used at T-60. They
define a different prediction time and live behind the explicit opt-in flag
``lineups_known=True`` of :func:`nba.props.context_residual.build_features` and
``stat_feature_names``; the default (production) path never touches them.

Historical proxy. The announced starting five is not stored; the box-score
``player_game_stats.starter`` flag is used. A late warm-up scratch of an
announced starter makes the box-score flag more informative than the real
announcement, so any gain measured with this proxy is biased upward (see
docs/LINEUPS_KNOWN.md). ``scratch_prob`` degrades the proxy with a synthetic
scratch process for a sensitivity analysis.

Everything except the own-starter flag and the tonight-five comparisons is built
from strictly earlier team games of the SAME season (season-scope rule); team
features of a team's first games of a season are NULL.

Columns (per ``(game_id, player_id)``, one row for every box-score row,
including DNP rows, whose starter flag is False):

* ``t30_starter``          - own announced-starter flag tonight (0/1).
* ``t30_n_changed``        - tonight's starters not in the team's previous-game five.
* ``t30_n_usual_absent``   - usual starters (>= 50% of the last <= 10 team games,
  at least 3 games in the window) absent from tonight's five.
* ``t30_vac_starter_min``  - mean minutes (in the window games they played) of those
  absent usual starters, summed.
* ``t30_start_delta``      - ``t30_starter`` minus the player's starter flag in his
  previous row of the same season.
"""

from __future__ import annotations

import numpy as np
import polars as pl

T30_FEATURES: tuple[str, ...] = (
    "t30_starter",
    "t30_n_changed",
    "t30_n_usual_absent",
    "t30_vac_starter_min",
    "t30_start_delta",
)
WINDOW_GAMES = 10
MIN_WINDOW_GAMES = 3
USUAL_START_FRAC = 0.5


def _contaminate(rows: pl.DataFrame, scratch_prob: float, seed: int) -> pl.DataFrame:
    """Sensitivity only: with probability ``scratch_prob`` per team-game, one random
    box-score starter is treated as announced on the bench (a replacement for a
    late scratch). Deterministic given ``seed`` and the sorted team-game order."""
    rng = np.random.default_rng(seed)
    rows = rows.with_row_index("_ri")
    out = rows["started"].to_numpy().copy()
    starters = rows.filter(pl.col("started")).sort(["game_id", "team_id", "player_id"])
    for _key, sub in starters.group_by(["game_id", "team_id"], maintain_order=True):
        if rng.random() < scratch_prob:
            idx = sub["_ri"].to_numpy()
            out[int(rng.choice(idx))] = False
    return rows.with_columns(pl.Series("started_ann", out)).drop("_ri")


def build_lineup_features(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    scratch_prob: float = 0.0,
    seed: int = 0,
) -> pl.DataFrame:
    """T-30 features for every ``pgs`` row (see module docstring)."""
    rows = (
        pgs.select(
            "game_id",
            "player_id",
            "team_id",
            "minutes",
            pl.col("starter").fill_null(False).cast(pl.Boolean).alias("started"),
        )
        .join(
            games.select(["game_id", pl.col("game_date").cast(pl.Date), "season"]),
            on="game_id",
        )
        .sort(["game_date", "game_id", "team_id", "player_id"])
    )
    rows = (
        _contaminate(rows, scratch_prob, seed)
        if scratch_prob > 0.0
        else rows.with_columns(pl.col("started").alias("started_ann"))
    )
    rows = rows.with_columns(
        (pl.col("minutes").fill_null(0.0) > 0.0).alias("played"),
        pl.col("minutes").fill_null(0.0).alias("mins0"),
    )
    tg = (
        rows.group_by(["game_id", "team_id"])
        .agg(
            pl.col("game_date").first(),
            pl.col("season").first(),
            pl.col("started_ann").sum().alias("n_start"),
        )
        .sort(["team_id", "season", "game_date", "game_id"])
        .with_columns(pl.int_range(pl.len()).over(["team_id", "season"]).alias("tg_no"))
    )
    starters = rows.filter(pl.col("started_ann")).join(
        tg.select("game_id", "team_id", "tg_no"), on=["game_id", "team_id"]
    )

    # --- n starters changed vs the team's previous game (same season)
    prev_set = starters.select(
        "team_id",
        "season",
        (pl.col("tg_no") + 1).alias("tg_no"),
        "player_id",
        pl.lit(1).alias("in_prev"),
    )
    changed = (
        starters.join(prev_set, on=["team_id", "season", "tg_no", "player_id"], how="left")
        .group_by(["game_id", "team_id"])
        .agg(pl.col("in_prev").is_null().sum().alias("_n_changed"))
    )
    prev_n = tg.select(
        "team_id", "season", (pl.col("tg_no") + 1).alias("tg_no"), pl.col("n_start").alias("prev_n")
    )
    team = (
        tg.join(prev_n, on=["team_id", "season", "tg_no"], how="left")
        .join(changed, on=["game_id", "team_id"], how="left")
        .with_columns(
            pl.when((pl.col("n_start") == 5) & (pl.col("prev_n") == 5))
            .then(pl.col("_n_changed").cast(pl.Float64))
            .otherwise(None)
            .alias("t30_n_changed")
        )
    )

    # --- usual starters (window of previous team games) absent from tonight's five
    tg_t = tg.select("game_id", "team_id", "season", "tg_no")
    tg_p = tg.select(
        pl.col("game_id").alias("p_game"),
        "team_id",
        "season",
        pl.col("tg_no").alias("p_no"),
    )
    pairs = tg_t.join(tg_p, on=["team_id", "season"]).filter(
        (pl.col("p_no") < pl.col("tg_no")) & (pl.col("p_no") >= pl.col("tg_no") - WINDOW_GAMES)
    )
    n_win = pairs.group_by(["game_id", "team_id"]).agg(pl.len().alias("n_window"))
    prow = rows.select(
        pl.col("game_id").alias("p_game"),
        "team_id",
        "player_id",
        "started_ann",
        "played",
        "mins0",
    )
    per_pl = (
        pairs.select("game_id", "team_id", "p_game")
        .join(prow, on=["p_game", "team_id"])
        .group_by(["game_id", "team_id", "player_id"])
        .agg(
            pl.col("started_ann").cast(pl.Float64).sum().alias("n_st"),
            pl.col("played").cast(pl.Float64).sum().alias("n_pl"),
            pl.col("mins0").sum().alias("sum_min"),
        )
        .join(n_win, on=["game_id", "team_id"])
        .filter(
            (pl.col("n_window") >= MIN_WINDOW_GAMES)
            & (pl.col("n_st") >= USUAL_START_FRAC * pl.col("n_window"))
        )
    )
    tonight = starters.select("game_id", "team_id", "player_id", pl.lit(1).alias("_tonight"))
    absent = (
        per_pl.join(tonight, on=["game_id", "team_id", "player_id"], how="left")
        .filter(pl.col("_tonight").is_null())
        .group_by(["game_id", "team_id"])
        .agg(
            pl.len().cast(pl.Float64).alias("_n_abs"),
            (pl.col("sum_min") / pl.col("n_pl").clip(lower_bound=1.0)).sum().alias("_vac"),
        )
    )
    team = (
        team.join(n_win, on=["game_id", "team_id"], how="left")
        .join(absent, on=["game_id", "team_id"], how="left")
        .with_columns(
            pl.when(
                (pl.col("n_window").fill_null(0) >= MIN_WINDOW_GAMES) & (pl.col("n_start") == 5)
            )
            .then(pl.col("_n_abs").fill_null(0.0))
            .otherwise(None)
            .alias("t30_n_usual_absent"),
            pl.when(
                (pl.col("n_window").fill_null(0) >= MIN_WINDOW_GAMES) & (pl.col("n_start") == 5)
            )
            .then(pl.col("_vac").fill_null(0.0))
            .otherwise(None)
            .alias("t30_vac_starter_min"),
        )
        .select("game_id", "team_id", "t30_n_changed", "t30_n_usual_absent", "t30_vac_starter_min")
    )

    # --- own status change vs the player's previous row (same season)
    own = (
        rows.sort(["player_id", "game_date", "game_id"])
        .with_columns(
            pl.col("started_ann").cast(pl.Float64).alias("t30_starter"),
            pl.col("started_ann").cast(pl.Float64).shift(1).over("player_id").alias("_prev"),
            pl.col("season").shift(1).over("player_id").alias("_prev_season"),
        )
        .with_columns(
            pl.when(pl.col("_prev_season") == pl.col("season"))
            .then(pl.col("t30_starter") - pl.col("_prev"))
            .otherwise(None)
            .alias("t30_start_delta")
        )
        .select("game_id", "player_id", "team_id", "t30_starter", "t30_start_delta")
    )
    out = own.join(team, on=["game_id", "team_id"], how="left")
    return out.select("game_id", "player_id", "team_id", *T30_FEATURES).sort(
        ["game_id", "team_id", "player_id"]
    )
