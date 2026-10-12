# ruff: noqa: E501
"""As-of builders for TEAM_BUDGET: the rotation roster and coach habits (research).

Everything here is strictly as-of: a row for (game, team) uses only games dated STRICTLY EARLIER than
the row's game date, never the row's own box, minutes, margin or stints. The roster is NOT the box
score's membership (the sim's leak): it is the players who appeared for the team in its previous
``ROSTER_LOOKBACK`` games.
"""

from __future__ import annotations

import polars as pl

ROSTER_LOOKBACK = 10
DEPTH_MIN = 10.0  # a "rotation" appearance is >= 10 minutes
BLOWOUT = 20.0
CLOSE = 10.0
#: Pseudo-counts (games) of the empirical-Bayes shrinkage of each coach habit to the league as-of mean.
K_HABIT = 15.0
K_COND = 6.0  # b2b / blowout conditional means have fewer games
HABIT_COLS = ("h_depth", "h_conc", "h_b2b", "h_blowout")


def asof_roster(
    box: pl.DataFrame, team_games: pl.DataFrame, lookback: int = ROSTER_LOOKBACK
) -> pl.DataFrame:
    """Rotation roster per (game_id, team_id) from the team's previous ``lookback`` games.

    ``box``: game_id, team_id, player_id, minutes, starter. ``team_games``: game_id, team_id, game_date.
    Returns game_id, team_id, player_id, apps (appearances in the window), mean_min (minutes per
    appearance), start_rate (starts per appearance). Only minutes > 0 count as an appearance.
    """
    tg = team_games.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.int_range(pl.len()).over("team_id").alias("_k")
    )
    app = box.filter(pl.col("minutes") > 0).join(
        tg.select("game_id", "team_id", "_k"), on=["game_id", "team_id"]
    )
    # An appearance at team-game j feeds the rosters of team-games j+1 .. j+lookback.
    exp = (
        app.with_columns(pl.int_ranges(pl.col("_k") + 1, pl.col("_k") + lookback + 1).alias("_t"))
        .explode("_t")
        .group_by("team_id", "_t", "player_id")
        .agg(
            pl.len().alias("apps"),
            pl.col("minutes").mean().alias("mean_min"),
            pl.col("starter").cast(pl.Float64).mean().alias("start_rate"),
        )
    )
    return (
        tg.select("game_id", "team_id", pl.col("_k").alias("_t"))
        .join(exp, on=["team_id", "_t"], how="inner")
        .drop("_t")
        .sort(["game_id", "team_id", "player_id"])
    )


def team_game_shape(
    box: pl.DataFrame, games: pl.DataFrame, b2b: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Per (game_id, team_id) post-game shape used ONLY as history for later games: rotation depth,
    minutes concentration (Herfindahl of minute shares), top-5 minutes, back-to-back flag, |margin|.

    ``games``: game_id, game_date, home_team, away_team, home_pts, away_pts."""
    b = box.filter(pl.col("minutes") > 0)
    tot = b.group_by("game_id", "team_id").agg(pl.col("minutes").sum().alias("_tot"))
    j = b.join(tot, on=["game_id", "team_id"]).with_columns(
        (pl.col("minutes") / pl.col("_tot")).alias("_sh")
    )
    shape = j.group_by("game_id", "team_id").agg(
        (pl.col("minutes") >= DEPTH_MIN).sum().cast(pl.Float64).alias("depth"),
        (pl.col("_sh") ** 2).sum().alias("conc"),
        pl.col("minutes").sort(descending=True).head(5).sum().alias("top5_min"),
    )
    g = games.select(
        "game_id",
        "game_date",
        pl.col("home_team").alias("team_id"),
        (pl.col("home_pts") - pl.col("away_pts")).abs().cast(pl.Float64).alias("abs_margin"),
    )
    g2 = games.select(
        "game_id",
        "game_date",
        pl.col("away_team").alias("team_id"),
        (pl.col("home_pts") - pl.col("away_pts")).abs().cast(pl.Float64).alias("abs_margin"),
    )
    tg = pl.concat([g, g2]).sort(["team_id", "game_date", "game_id"])
    if b2b is None:
        b2b = tg.select(
            "game_id",
            "team_id",
            (
                (pl.col("game_date") - pl.col("game_date").shift(1).over("team_id")).dt.total_days()
                == 1
            )
            .fill_null(False)
            .alias("b2b"),
        )
    return tg.join(shape, on=["game_id", "team_id"], how="left").join(
        b2b.select("game_id", "team_id", "b2b"), on=["game_id", "team_id"], how="left"
    )


def _league_prior(tg: pl.DataFrame, value: str, mask: str | None) -> pl.DataFrame:
    """League mean of ``value`` over rows dated STRICTLY earlier than each date (cumulative by date,
    shifted one date). ``mask`` selects the rows (None = all)."""
    d = tg.filter(pl.col(value).is_not_null())
    if mask is not None:
        d = d.filter(pl.col(mask))
    daily = (
        d.group_by("game_date")
        .agg(pl.col(value).sum().alias("s"), pl.len().alias("n"))
        .sort("game_date")
        .with_columns(pl.col("s").cum_sum().alias("cs"), pl.col("n").cum_sum().alias("cn"))
        .select(
            "game_date",
            pl.col("cs").alias(f"_ls_{value}_{mask}"),
            pl.col("cn").alias(f"_ln_{value}_{mask}"),
        )
    )
    return daily


def _shrunk(
    tg: pl.DataFrame, value: str, mask: str | None, k: float, name: str, prior_default: float
) -> pl.DataFrame:
    """Coach-level shrunk mean of ``value`` (optionally over ``mask`` rows) as of each row's date:
    ``(S_coach + k * L) / (N_coach + k)``. The coach looked up for a row is ``id_key`` (the coach of
    the team's PREVIOUS game, the only identity knowable at T-60); S, N are that coach's running
    totals over his own games (``coach_key``, any team) dated STRICTLY earlier than the row's date;
    L is the league mean over strictly earlier dates (``prior_default`` before any exist)."""
    use = pl.col(value).is_not_null() & (pl.col(mask) if mask else pl.lit(True))
    hist = (
        tg.filter(pl.col("coach_key").is_not_null())
        .sort(["coach_key", "game_date", "game_id"])
        .with_columns(
            pl.when(use).then(pl.col(value)).otherwise(0.0).alias("_v"),
            use.cast(pl.Float64).alias("_u"),
        )
        .with_columns(
            pl.col("_v").cum_sum().over("coach_key").alias("_cs"),
            pl.col("_u").cum_sum().over("coach_key").alias("_cn"),
        )
        .select(pl.col("coach_key").alias("id_key"), "game_date", "_cs", "_cn")
        .unique(subset=["id_key", "game_date"], keep="last")
        .sort("game_date")
    )
    lp = _league_prior(tg, value, mask).sort("game_date")
    sc, nc = f"_ls_{value}_{mask}", f"_ln_{value}_{mask}"
    q = tg.select("game_id", "team_id", "game_date", "id_key").sort("game_date")
    q = q.join_asof(lp, on="game_date", strategy="backward", allow_exact_matches=False)
    q = q.join_asof(
        hist, on="game_date", by="id_key", strategy="backward", allow_exact_matches=False
    )
    lg = pl.when(pl.col(nc) > 0).then(pl.col(sc) / pl.col(nc)).otherwise(prior_default)
    cs = pl.col("_cs").fill_null(0.0)
    cn = pl.col("_cn").fill_null(0.0)
    return q.select(
        "game_id", "team_id", ((cs + k * lg) / (cn + k)).alias(name), cn.alias(f"{name}_n")
    )


def coach_habits_asof(tg: pl.DataFrame) -> pl.DataFrame:
    """Coach habits per (game_id, team_id), strictly as-of.

    ``tg``: output of ``team_game_shape`` plus ``coach_key`` (the ACTUAL coach of that game; it is
    used only as history for LATER games). The coach whose habits a game receives is the coach of
    the team's previous game (``id_key``), because ``team_coach_games.known_at`` is each game's own
    tip, so tonight's coach is not stamped knowable at T-60 but the previous game's is. A new
    coach's first game therefore receives the predecessor's habits (honest). Columns: ``coach_n_prior`` (the coach's earlier games in the data), ``h_depth`` (rotation
    depth), ``h_conc`` (minutes concentration), ``h_b2b`` (top-5 minutes on back-to-backs minus
    otherwise), ``h_blowout`` (top-5 minutes in |margin| >= 20 games minus <= 10 games)."""
    t = tg.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.col("coach_key").shift(1).over("team_id").alias("id_key"),
        pl.col("b2b").fill_null(False),
        (pl.col("abs_margin") >= BLOWOUT).alias("_blow"),
        (pl.col("abs_margin") <= CLOSE).alias("_close"),
        (~pl.col("b2b").fill_null(False)).alias("_rest"),
    )
    d = _shrunk(t, "depth", None, K_HABIT, "h_depth", 9.0)
    c = _shrunk(t, "conc", None, K_HABIT, "h_conc", 0.12)
    tb = _shrunk(t, "top5_min", "b2b", K_COND, "_t_b2b", 150.0)
    tr = _shrunk(t, "top5_min", "_rest", K_COND, "_t_rest", 150.0)
    tl = _shrunk(t, "top5_min", "_blow", K_COND, "_t_blow", 150.0)
    tc = _shrunk(t, "top5_min", "_close", K_COND, "_t_close", 150.0)
    key = ["game_id", "team_id"]
    out = d.join(c.drop("h_conc_n"), on=key).join(tb.drop("_t_b2b_n"), on=key)
    out = out.join(tr.drop("_t_rest_n"), on=key).join(tl.drop("_t_blow_n"), on=key)
    out = out.join(tc.drop("_t_close_n"), on=key)
    return out.select(
        *key,
        pl.col("h_depth_n").alias("coach_n_prior"),
        "h_depth",
        "h_conc",
        (pl.col("_t_b2b") - pl.col("_t_rest")).alias("h_b2b"),
        (pl.col("_t_blow") - pl.col("_t_close")).alias("h_blowout"),
    ).sort(key)
