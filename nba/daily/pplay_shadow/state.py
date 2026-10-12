# ruff: noqa: E501
"""Production's per-player recency state at ANY (player, date), played or not.

Vendored from ``research/recent_role/prod_state.py`` (docs/prereg/RECENT_ROLE.md; the live path may not
import research code). The logic is unchanged: append one sentinel row per player (date 2100-01-01) to the
played table, run the unchanged ``nba.props.context_residual.player_states``, and read the ``pre`` state of
the first played-or-sentinel row dated on or after the query date, i.e. the state AFTER the player's last
PLAYED game dated strictly before the query date. Four columns depend on the query row itself and are
recomputed from that last played game: ``gap_days``, ``n_season``, ``since_team``, ``last_starter``.

Strictly as-of: the query row's own game, and every row dated on or after its date, never enter.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.baselines import _DEFAULT_STD
from nba.props.context_residual import _CI, _DI, PROP_STATS, player_states

SENTINEL_DATE = dt.date(2100, 1, 1)
PLAYED_COLS = (
    "player_id",
    "team_id",
    "season",
    "game_date",
    "game_id",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "minutes",
    "starter",
)

#: scalar recent-minutes / role columns (the Stage 1 comparator adds the ones C lacks; see STAGE1_PROD_COLS)
SCALAR_COLS: tuple[str, ...] = (
    "n_prior", "n_season", "gap_days", "since_flag", "flag_dir", "since_team",
    "min5", "min10", "min40", "min_gap", "min_gap10", "starter10", "starter40", "last_starter",
)  # fmt: skip
STAT_COLS: tuple[str, ...] = tuple(
    f"{p}_{s}" for s in PROP_STATS for p in ("m5", "m10", "m40", "m100", "sd10", "role_gap", "rate")
)
ALL_STATE_COLS: tuple[str, ...] = SCALAR_COLS + STAT_COLS
#: Stage 1 comparator additions: production's recent-minutes and role columns (COMMON_FEATURES, player-state part)
#: that the WORKLOAD comparator C does not hold. n_prior is C's n_played_prior and is not added.
STAGE1_PROD_COLS: tuple[str, ...] = (
    "min5", "min10", "min40", "min_gap", "min_gap10", "starter10", "starter40",
    "last_starter", "gap_days", "since_flag", "flag_dir", "since_team", "n_season",
)  # fmt: skip


def _prep_played(played: pl.DataFrame) -> pl.DataFrame:
    p = played.select(PLAYED_COLS).filter(pl.col("minutes") > 0)
    return p.with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("starter").fill_null(False),
        pl.col("game_date").cast(pl.Date),
    ).sort(["player_id", "game_date", "game_id"])


def _sentinels(p: pl.DataFrame) -> pl.DataFrame:
    last = p.group_by("player_id", maintain_order=True).tail(1)
    return last.with_columns(
        pl.lit(SENTINEL_DATE).alias("game_date"),
        pl.lit("~sentinel").alias("game_id"),
        pl.lit(0).cast(p.schema["pts"]).alias("pts"),
        pl.lit(0).cast(p.schema["reb"]).alias("reb"),
        pl.lit(0).cast(p.schema["ast"]).alias("ast"),
        pl.lit(0).cast(p.schema["fg3m"]).alias("fg3m"),
        pl.lit(0.0).cast(p.schema["minutes"]).alias("minutes"),
        pl.lit(False).alias("starter"),
    )


def prod_state_rows(played: pl.DataFrame, queries: pl.DataFrame) -> pl.DataFrame:
    """Production state columns for every query ``(game_id, player_id, team_id, season, game_date)``.

    ``played`` is the box-score table (any rows; only ``minutes > 0`` rows are used). Returns the queries'
    ``game_id, player_id`` plus :data:`ALL_STATE_COLS` (Float64), in the queries' row order. A query with no
    earlier played game has ``n_prior = 0``, production's defaults (since_flag 30, flag_dir 0, since_team 30) and NaN elsewhere (such rows are never eligible: n_prior >= 5)."""
    p = _prep_played(played)
    aug = pl.concat([p, _sentinels(p)], how="vertical_relaxed").sort(
        ["player_id", "game_date", "game_id"]
    )
    st = player_states(aug)
    pid = aug["player_id"].to_numpy().astype(np.int64)
    days = aug["game_date"].cast(pl.Int32).to_numpy().astype(np.int64)
    team = aug["team_id"].to_numpy().astype(np.int64)
    seas = aug["season"].to_numpy().astype(np.int64)
    starter = aug["starter"].cast(pl.Float64).to_numpy()
    pre = st["pre"]

    q = queries.select("game_id", "player_id", "team_id", "season", "game_date")
    qpid = q["player_id"].to_numpy().astype(np.int64)
    qdays = q["game_date"].cast(pl.Date).cast(pl.Int32).to_numpy().astype(np.int64)
    qteam = q["team_id"].to_numpy().astype(np.int64)
    qseas = q["season"].to_numpy().astype(np.int64)

    # composite sorted key: (dense player index, day)
    uniq, inv = np.unique(pid, return_inverse=True)
    span = 100_000
    key = inv.astype(np.int64) * span + days
    qi = np.searchsorted(uniq, qpid)
    known = (qi < len(uniq)) & (uniq[np.minimum(qi, len(uniq) - 1)] == qpid)
    qkey = np.where(known, qi.astype(np.int64) * span + qdays, 0)
    k = np.searchsorted(key, qkey, side="left")
    k = np.minimum(k, len(key) - 1)
    j = k - 1
    has_j = known & (j >= 0) & (pid[np.maximum(j, 0)] == qpid) & (k > 0)
    jj = np.maximum(j, 0)
    nan = np.full(len(q), np.nan)

    def pick(a: np.ndarray) -> np.ndarray:
        return np.where(has_j, a[k], nan)

    n_prior = np.where(has_j, st["n_prior"][k].astype(float), 0.0)
    cols: dict[str, np.ndarray] = {
        "n_prior": n_prior,
        "since_flag": np.where(has_j, st["since_flag"][k], 30.0),
        "flag_dir": np.where(has_j, st["flag_dir"][k], 0.0),
        "gap_days": np.where(has_j, (qdays - days[jj]).astype(float), nan),
        "n_season": np.where(
            has_j, np.where(qseas == seas[jj], st["n_season"][jj] + 1.0, 0.0), 0.0
        ),
        "since_team": np.where(
            has_j,
            np.where(qteam != team[jj], 0.0, np.minimum(st["since_team"][jj] + 1.0, 30.0)),
            30.0,
        ),
        "last_starter": np.where(has_j, starter[jj], nan),
    }

    def sel(h: float, col: str) -> np.ndarray:
        return np.where(has_j, pre[k, _DI[h], _CI[col]], nan)

    cols["min5"], cols["min10"], cols["min40"] = sel(5.0, "min"), sel(10.0, "min"), sel(40.0, "min")
    cols["starter10"], cols["starter40"] = sel(10.0, "starter"), sel(40.0, "starter")
    cols["min_gap"] = cols["min5"] - cols["min40"]
    cols["min_gap10"] = cols["min10"] - cols["min40"]
    for s in PROP_STATS:
        cols[f"m5_{s}"], cols[f"m10_{s}"] = sel(5.0, s), sel(10.0, s)
        cols[f"m40_{s}"], cols[f"m100_{s}"] = sel(40.0, s), sel(100.0, s)
        var = np.clip(sel(10.0, f"{s}2") - cols[f"m10_{s}"] ** 2, 0.0, None)
        nn = np.maximum(n_prior, 2)
        sd = np.sqrt(var * nn / (nn - 1))
        cols[f"sd10_{s}"] = np.where((n_prior >= 2) & (sd > 0), sd, _DEFAULT_STD[s])
        cols[f"role_gap_{s}"] = cols[f"m10_{s}"] - cols[f"m100_{s}"]
        cols[f"rate_{s}"] = cols[f"m10_{s}"] / np.maximum(cols["min10"], 1.0)
    out = q.select("game_id", "player_id").with_columns(
        [pl.Series(c, np.asarray(cols[c], dtype=float)) for c in ALL_STATE_COLS]
    )
    return out
