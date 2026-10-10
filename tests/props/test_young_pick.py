"""F9 young-pick columns (docs/prereg/F9_YOUNG_PICK_PRIOR.md): as-of and flag-off identity."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import (
    YP_FEATURES,
    ContextResidualConfig,
    build_features,
    stat_feature_names,
    yp_weight,
)
from tests.props.test_context_residual import ELO, _league


def _static(static: pl.DataFrame) -> pl.DataFrame:
    n = static.height
    pick = [(i % 14 + 1) if i % 5 else None for i in range(n)]
    return static.with_columns(pl.Series("draft_pick", pick, dtype=pl.Int64))


def _build(gdf: pl.DataFrame, pdf: pl.DataFrame, static: pl.DataFrame, **kw):  # type: ignore[no-untyped-def]
    return build_features(gdf, pdf, static, {}, ELO, **kw)


def test_flag_off_is_byte_identical_and_config_default_off() -> None:
    gdf, pdf, static = _league(n_days=120)
    assert ContextResidualConfig().young_pick == "off"
    base = _build(gdf, pdf, static)
    off = _build(gdf, pdf, _static(static), young_pick=False)  # extra static column is inert
    assert base.columns == off.columns and not any(c.startswith("yp_") for c in off.columns)
    assert base.equals(off)
    assert stat_feature_names("pts") == stat_feature_names("pts", extra=())


def test_on_appends_exactly_the_seven_columns_and_leaves_others_unchanged() -> None:
    gdf, pdf, static = _league(n_days=120)
    st = _static(static)
    off = _build(gdf, pdf, st)
    on = _build(gdf, pdf, st, young_pick=True)
    assert on.height == off.height
    assert tuple(c for c in on.columns if c.startswith("yp_")) == YP_FEATURES
    assert on.select(off.columns).equals(off)
    names = stat_feature_names("pts", extra=YP_FEATURES)
    assert names[-7:] == list(YP_FEATURES)


def test_yp_seasons_counts_strictly_prior_seasons_and_weights() -> None:
    gdf, pdf, static = _league()
    on = _build(gdf, pdf, _static(static), young_pick=True)
    first = on.group_by("player_id").agg(pl.col("season").min().alias("s0"))
    on = on.join(first, on="player_id")
    assert (on.filter(pl.col("season") == pl.col("s0"))["yp_seasons"] == 1).all()
    assert (on.filter(pl.col("season") > pl.col("s0"))["yp_seasons"] >= 2).all()
    w = yp_weight(np.array([1, 2, 3, 4, 5, 1, 2]), np.array([3, 10, 1, 7, 2, 11, np.nan]))
    assert w.tolist() == [1.0, 0.75, 0.5, 0.25, 0.0, 0.0, 0.0]


def _yp(df: pl.DataFrame) -> pl.DataFrame:
    return df.select("game_id", "player_id", *YP_FEATURES).sort(["game_id", "player_id"])


def test_planted_future_game_leaves_earlier_rows_unchanged() -> None:
    gdf, pdf, static = _league()
    st = _static(static)
    base = _yp(_build(gdf, pdf, st, young_pick=True))
    last = gdf.sort("game_date").tail(1)
    fake_date = last["game_date"][0] + dt.timedelta(days=400)  # a later season
    fg = last.with_columns(
        pl.lit("9999999").alias("game_id"),
        pl.lit(fake_date).cast(gdf.schema["game_date"]).alias("game_date"),
        (pl.col("season") + 1).cast(gdf.schema["season"]).alias("season"),
        pl.lit(150).cast(pl.Int64).alias("home_pts"),
    )
    fp = pdf.filter(pl.col("game_id") == last["game_id"][0]).with_columns(
        pl.lit("9999999").alias("game_id"),
        pl.lit(60).cast(pdf.schema["pts"]).alias("pts"),
        pl.lit(48.0).alias("minutes"),
    )
    got = _yp(_build(pl.concat([gdf, fg]), pl.concat([pdf, fp]), st, young_pick=True))
    got = got.filter(pl.col("game_id") != "9999999")
    assert base.equals(got)


def test_planted_same_game_stats_do_not_change_that_games_rows() -> None:
    gdf, pdf, static = _league()
    st = _static(static)
    base = _yp(_build(gdf, pdf, st, young_pick=True))
    tonight = gdf.sort("game_date").tail(1)["game_id"][0]
    hit = (pl.col("game_id") == tonight) & (pl.col("minutes") > 0)
    mod = pdf.with_columns(
        pl.when(hit).then(99).otherwise(pl.col("pts")).alias("pts"),
        pl.when(hit).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
    )
    g2 = gdf.with_columns(
        pl.when(pl.col("game_id") == tonight)
        .then(200)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    got = _yp(_build(g2, mod, st, young_pick=True))
    a = base.filter(pl.col("game_id") == tonight)
    b = got.filter(pl.col("game_id") == tonight)
    assert a.height > 0 and a.equals(b)


def test_slice_and_missingness_helpers() -> None:
    from nba.eval.f9_young_pick import frozen_sha256, missingness_audit, slice_s

    df = pl.DataFrame(
        {
            "yp_seasons": [1.0, 2.0, 3.0, 1.0, 2.0],
            "yp_pick": [3.0, 10.0, 5.0, 11.0, float("nan")],
            "min10": [25.0, 20.0, 30.0, 30.0, 30.0],
            "minutes": [30.0, 5.0, 30.0, 30.0, 5.0],
        }
    )
    assert slice_s(df).to_list() == [True, True, False, False, False]
    audit = missingness_audit(df)
    assert audit["nan_rate_by_minutes"] == {"played<10": 0.5, "rest": 0.0}
    assert len(frozen_sha256()) == 64
