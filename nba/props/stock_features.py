# ruff: noqa: E501
"""As-of feature columns for steals and blocks, built from production's recipe (docs/prereg/COMBO_STOCKS_SHADOW.md).

``nba.props.context_residual.build_features`` carries rolling features for ``PROP_STATS`` only (pts, reb, ast,
fg3m). The heads for ``blk`` and ``stl`` need the same columns for those two stats. This module recomputes,
for any list of box columns, exactly the columns production builds for a stat ``s``::

    m5_s m10_s m40_s m100_s sd10_s role_gap_s rate_s opp_allow_s vac_s

with the same recurrence (half-lives 5/10/40/100 over PLAYED games strictly before the row, reset per
player), the same rolling-20 opponent-allowed mean (shifted one game, ``min_samples=5``), and the same
vacated-production rule (OUT rotation teammates on the official report). ``tests/props/test_stock_features.py``
proves the equivalence: with ``stats=PROP_STATS`` every column equals production's, and a planted future
row never moves a feature. Nothing here is fitted; nothing reads a row dated on or after the target game.

The module imports only production code (``nba.props``), so the live path may import it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.baselines import _DEFAULT_STD
from nba.props.context_residual import (
    _DECAYS,
    CRPS_TAUS,
    MIN_PRIOR_PLAYED,
    ROTATION_MIN40,
    ContextResidualConfig,
    ContextResidualModel,
    stat_frame,
    to_integer_support,
)

STOCK_STATS: tuple[str, ...] = ("blk", "stl")
#: The five box stats the combos are built from, plus the stocks.
COMBO_STATS: tuple[str, ...] = ("pts", "reb", "ast", "blk", "stl")
_DI = {h: i for i, h in enumerate(_DECAYS)}
KEYS = ["game_id", "player_id"]


def ew_states(
    played: pl.DataFrame, cols: tuple[str, ...]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-player exponentially weighted means over PLAYED rows (sorted by player_id, game_date, game_id).

    Returns ``(pre, post, n_prior)``: ``pre[i, d, c]`` is the weighted mean of column ``c`` at decay ``d``
    over the player's games strictly BEFORE row ``i`` (NaN when there are none); ``post`` includes the row
    (used only for the vacated-production state of a LATER game); ``n_prior`` counts earlier played games.
    Same recurrence as ``context_residual.player_states`` (weight ``0.5 ** (games_ago / half_life)``)."""
    n = played.height
    pid = played["player_id"].to_numpy()
    x = np.column_stack([played[c].to_numpy().astype(float) for c in cols])
    dec = np.array([0.5 ** (1.0 / h) for h in _DECAYS])
    pre = np.full((n, len(_DECAYS), x.shape[1]), np.nan)
    post = np.empty_like(pre)
    n_prior = np.zeros(n, dtype=np.int32)
    s_mat = np.zeros((len(_DECAYS), x.shape[1]))
    d_vec = np.zeros(len(_DECAYS))
    cnt = 0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s_mat[:] = 0.0
            d_vec[:] = 0.0
            cnt = 0
        n_prior[i] = cnt
        if cnt > 0:
            pre[i] = s_mat / d_vec[:, None]
        s_mat = dec[:, None] * s_mat + x[i][None, :]
        d_vec = dec * d_vec + 1.0
        post[i] = s_mat / d_vec[:, None]
        cnt += 1
    return pre, post, n_prior


def _played_sorted(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    return (
        pgs.join(
            games.select(["game_id", "game_date", "season", "home_team"]).with_columns(
                pl.col("game_date").cast(pl.Date)
            ),
            on="game_id",
        )
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )


def opp_allowed_rolling(
    games: pl.DataFrame, pgs: pl.DataFrame, stats: tuple[str, ...]
) -> pl.DataFrame:
    """``opp_allow_s`` per (game_id, team_id): the OPPONENT's rolling-20 mean (shifted one game,
    ``min_samples=5``) of the ``s`` its games allowed. Same arithmetic as ``team_game_context``."""
    g = games.select(["game_id", "game_date", "home_team", "away_team"]).with_columns(
        pl.col("game_date").cast(pl.Date)
    )
    home = g.select(
        "game_id",
        "game_date",
        pl.col("home_team").alias("team_id"),
        pl.col("away_team").alias("opp_id"),
    )
    away = g.select(
        "game_id",
        "game_date",
        pl.col("away_team").alias("team_id"),
        pl.col("home_team").alias("opp_id"),
    )
    tg = pl.concat([home, away]).sort(["team_id", "game_date", "game_id"])
    tot = pgs.group_by(["game_id", "team_id"]).agg(
        [pl.col(s).sum().alias(f"tot_{s}") for s in stats]
    )
    opp_tot = tot.rename({"team_id": "opp_id", **{f"tot_{s}": f"allow_{s}" for s in stats}})
    tg = tg.join(opp_tot, on=["game_id", "opp_id"], how="left").sort(
        ["team_id", "game_date", "game_id"]
    )
    tg = tg.with_columns(
        [
            pl.col(f"allow_{s}")
            .shift(1)
            .rolling_mean(20, min_samples=5)
            .over("team_id")
            .alias(f"r20_allow_{s}")
            for s in stats
        ]
    )
    opp = tg.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        *[pl.col(f"r20_allow_{s}").alias(f"opp_allow_{s}") for s in stats],
    )
    return (
        tg.select("game_id", "team_id", "opp_id")
        .join(opp, on=["game_id", "opp_id"], how="left")
        .drop("opp_id")
    )


def vacated(
    played: pl.DataFrame,
    post: np.ndarray,
    n_prior: np.ndarray,
    min_col: int,
    stat_cols: dict[str, int],
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[object, int, int]],
) -> pl.DataFrame:
    """``vac_s`` per (game_id, team_id): sum over rotation teammates OUT on the official report of their
    state-10 mean of ``s`` after their latest game strictly before the game date. Same rule as
    ``context_residual.vacated_features`` (a rotation player has a 40-game-decay minutes mean >= 12 and at
    least ``MIN_PRIOR_PLAYED`` games). Games without a report are absent (the caller left-joins)."""
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": n_prior + 1,
            "min40": post[:, _DI[40.0], min_col],
            "min10": post[:, _DI[10.0], min_col],
            **{f"v_{s}": post[:, _DI[10.0], c] for s, c in stat_cols.items()},
        }
    ).sort("game_date")
    recs = [
        (gid, p, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for p in pids
    ]
    if not recs:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "team_id": pl.Int64,
                **{f"vac_{s}": pl.Float64 for s in stat_cols},
            }
        )
    left = pl.DataFrame(
        recs,
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date},
        orient="row",
    )
    left = left.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
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
    return j.group_by(["game_id", "team_id"]).agg(
        *[pl.col(f"v_{s}").sum().alias(f"vac_{s}") for s in stat_cols]
    )


def stock_columns(
    feats: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    flagged: dict[str, set[int]],
    stats: tuple[str, ...] = STOCK_STATS,
) -> pl.DataFrame:
    """The nine production-style columns for each stat in ``stats`` (box columns of ``pgs``), keyed by
    (game_id, player_id) and aligned with ``feats`` (production ``build_features`` output, which supplies
    ``min10``, ``has_report`` and ``team_id``).

    ``pgs`` must carry ``minutes``, ``starter`` is not needed, and every column in ``stats``."""
    played = _played_sorted(games, pgs)
    cols = ("minutes", *stats, *[f"{s}__sq" for s in stats])
    played = played.with_columns(
        [(pl.col(s).cast(pl.Float64) ** 2).alias(f"{s}__sq") for s in stats]
    )
    pre, post, n_prior = ew_states(played, cols)
    ci = {c: i for i, c in enumerate(cols)}
    base = played.select("game_id", "player_id", "team_id")
    data: dict[str, np.ndarray] = {}
    for s in stats:
        for h in (5.0, 10.0, 40.0, 100.0):
            data[f"m{int(h)}_{s}"] = pre[:, _DI[h], ci[s]]
        m10 = data[f"m10_{s}"]
        var = np.clip(pre[:, _DI[10.0], ci[f"{s}__sq"]] - m10**2, 0.0, None)
        nn = np.maximum(n_prior, 2)
        sd = np.sqrt(var * nn / (nn - 1))
        default = _DEFAULT_STD[s]
        data[f"sd10_{s}"] = np.where((n_prior >= 2) & (sd > 0), sd, default)
        data[f"role_gap_{s}"] = m10 - data[f"m100_{s}"]
        data[f"rate_{s}"] = m10 / np.maximum(pre[:, _DI[10.0], ci["minutes"]], 1.0)
    cols_df = base.with_columns([pl.Series(k, v) for k, v in data.items()])
    out = feats.select("game_id", "player_id", "team_id", "has_report").join(
        cols_df.drop("team_id"), on=KEYS, how="left"
    )
    opp = opp_allowed_rolling(games, pgs, stats)
    out = out.join(opp, on=["game_id", "team_id"], how="left")
    game_info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(["game_id", "game_date", "home_team", "away_team"])
        .with_columns(pl.col("game_date").cast(pl.Date))
        .iter_rows()
    }
    vac = vacated(
        played, post, n_prior, ci["minutes"], {s: ci[s] for s in stats}, flagged, game_info
    )
    out = out.join(vac, on=["game_id", "team_id"], how="left")
    out = out.with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(f"vac_{s}").fill_null(0.0))
            .otherwise(None)
            .alias(f"vac_{s}")
            for s in stats
        ]
    )
    keep = [
        f"{p}_{s}"
        for s in stats
        for p in ("m5", "m10", "m40", "m100", "sd10", "role_gap", "rate", "opp_allow", "vac")
    ]
    return out.select(*KEYS, *keep)


def add_stock_features(
    feats: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    flagged: dict[str, set[int]],
    stats: tuple[str, ...] = STOCK_STATS,
) -> pl.DataFrame:
    """``feats`` (production frame) with the stock columns appended; row count and order unchanged."""
    extra = stock_columns(feats, games, pgs, flagged, stats)
    out = feats.join(extra, on=KEYS, how="left")
    if out.height != feats.height:
        raise ValueError("adding stock columns changed the row count")
    return out


def stock_feature_names(stat: str) -> list[str]:
    """Head inputs for ``stat`` in {blk, stl}: production's list for a stat (``stat_feature_names``) plus the
    player's own recent blocks and steals (production's ``own`` block lists the recent mean of every modelled stat)."""
    from nba.props.context_residual import stat_feature_names

    return stat_feature_names(stat, extra=tuple(f"m10_{s}" for s in STOCK_STATS))


def fit_stock_models(
    feats: pl.DataFrame, as_of: dt.date, cfg: ContextResidualConfig | None = None
) -> dict[str, ContextResidualModel]:
    """One production-recipe head pair (mean residual + scale, conformal standardised quantiles) per stock
    stat, fitted on PLAYED rows with ``game_date < as_of`` (``fit_for_date``'s rule). ``feats`` is the
    production frame with :func:`add_stock_features` applied. Default config = production's, unchanged."""
    cfg = cfg or ContextResidualConfig()
    hist = feats.filter(pl.col("game_date") < as_of)
    models: dict[str, ContextResidualModel] = {}
    for stat in STOCK_STATS:
        train = stat_frame(hist, stat).sort(["game_date", "game_id", "player_id"])
        models[stat] = ContextResidualModel(stat, cfg, stock_feature_names(stat)).fit(train)
    return models


def predict_stock_grid(
    model: ContextResidualModel, feats: pl.DataFrame
) -> tuple[pl.DataFrame, np.ndarray, np.ndarray]:
    """Eligible rows (>= ``MIN_PRIOR_PLAYED`` prior played games), clipped means, and the 199-point INTEGER grids
    (``int16``, ``to_integer_support`` of the predictive quantiles at ``CRPS_TAUS``)."""
    rows = stat_frame(feats, model.stat)
    if rows.is_empty():
        return rows, np.empty(0), np.empty((0, len(CRPS_TAUS)), dtype=np.int16)
    mean, q = model.predict(rows, CRPS_TAUS)
    return rows, mean, to_integer_support(q).astype(np.int16)
