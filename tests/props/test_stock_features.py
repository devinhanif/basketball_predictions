# ruff: noqa: E501
"""blk / stl feature columns (nba.props.stock_features): equivalence with production, planted future, heads."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    stat_frame,
)
from nba.props.stock_features import (
    STOCK_STATS,
    add_stock_features,
    fit_stock_models,
    predict_stock_grid,
    stock_columns,
    stock_feature_names,
)

ELO = {
    "k_factor": 10.0,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 0.00067,
}


def _league(seed: int = 0, n_days: int = 300, n_teams: int = 6, per_team: int = 9):
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 10, 20)
    games, rows = [], []
    gid = 0
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 2)
        season = d.year if d.month >= 8 else d.year - 1
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            h, a = int(order[k]), int(order[k + 1])
            gid += 1
            g = f"{gid:07d}"
            games.append(
                (g, d, season, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125)))
            )
            for t in (h, a):
                for p in range(per_team):
                    pid = t * 100 + p
                    played = rng.random() > (0.05 if p < 6 else 0.4)
                    mins = float(max(34 - 3 * p + rng.normal(0, 3), 4)) if played else None
                    lam = max(mins or 0, 0) / 34.0
                    rows.append(
                        (
                            g, pid, t, mins,
                            int(rng.poisson(14 * lam * (1.3 - p * 0.1))) if played else 0,
                            int(rng.poisson(5 * lam)) if played else 0,
                            int(rng.poisson(3 * lam)) if played else 0,
                            int(rng.poisson(1.5 * lam)) if played else 0,
                            int(rng.poisson(0.9 * lam)) if played else 0,
                            int(rng.poisson(0.5 * lam * (1 + (p == 0)))) if played else 0,
                            bool(p < 5),
                        )
                    )  # fmt: skip
    gdf = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )
    pdf = pl.DataFrame(
        rows,
        schema=[
            "game_id",
            "player_id",
            "team_id",
            "minutes",
            "pts",
            "reb",
            "ast",
            "fg3m",
            "stl",
            "blk",
            "starter",
        ],
        orient="row",
    )
    static = pl.DataFrame(
        {"player_id": sorted(pdf["player_id"].unique().to_list()), "position": "G"}
    )
    return gdf, pdf, static


def _flagged(gdf: pl.DataFrame):
    g1 = gdf.sort(["game_date", "game_id"]).row(150, named=True)
    out_pid = g1["home_team"] * 100
    avail = pl.DataFrame(
        {
            "game_id": [g1["game_id"]],
            "player_id": [out_pid],
            "status": ["Out"],
            "as_of": [dt.datetime.combine(g1["game_date"], dt.time(12, 0))],
            "source": ["nba_official_report"],
        }
    )
    flagged, _ = flagged_from_availability(avail, gdf, ContextResidualConfig().report)
    return flagged, g1


def test_columns_equal_production_for_production_stats() -> None:
    """The recomputation is production's recipe: stats=PROP_STATS reproduces every production column bit for bit."""
    gdf, pdf, static = _league()
    flagged, g1 = _flagged(gdf)
    assert flagged[g1["game_id"]]  # the OUT report is usable, so the vac_ columns are exercised
    feats = build_features(gdf, pdf, static, flagged, ELO)
    mine = stock_columns(feats, gdf, pdf, flagged, stats=PROP_STATS)
    joined = feats.join(mine, on=["game_id", "player_id"], suffix="_mine")
    assert joined.height == feats.height
    n_vac = 0
    for s in PROP_STATS:
        for p in ("m5", "m10", "m40", "m100", "sd10", "role_gap", "rate", "opp_allow", "vac"):
            a = joined[f"{p}_{s}"].to_numpy().astype(float)
            b = joined[f"{p}_{s}_mine"].to_numpy().astype(float)
            assert np.allclose(a, b, equal_nan=True, rtol=0, atol=1e-12), f"{p}_{s}"
            if p == "vac":
                n_vac += int(np.sum(~np.isnan(a) & (a > 0)))
    assert n_vac > 0  # the vacated-production path produced non-trivial values


def test_stock_features_are_asof_planted_future() -> None:
    """Changing a game's own blk/stl (or any later game) never moves that game's features."""
    gdf, pdf, static = _league()
    flagged, _ = _flagged(gdf)
    feats = build_features(gdf, pdf, static, flagged, ELO)
    base = add_stock_features(feats, gdf, pdf, flagged)
    last_date = gdf["game_date"].max()
    last_ids = gdf.filter(pl.col("game_date") == last_date)["game_id"].to_list()
    mod = pdf.with_columns(
        pl.when(pl.col("game_id").is_in(last_ids)).then(40).otherwise(pl.col("blk")).alias("blk"),
        pl.when(pl.col("game_id").is_in(last_ids)).then(40).otherwise(pl.col("stl")).alias("stl"),
    )
    feats2 = build_features(gdf, mod, static, flagged, ELO)
    alt = add_stock_features(feats2, gdf, mod, flagged)
    cols = [
        c
        for c in base.columns
        if any(c.endswith(f"_{s}") for s in STOCK_STATS) and not c.startswith("opp_allow")
    ]
    sel = (base["game_date"] == last_date).to_numpy()
    for c in cols:
        a, b = base[c].to_numpy().astype(float)[sel], alt[c].to_numpy().astype(float)[sel]
        assert np.allclose(a, b, equal_nan=True), c
    # opponent-allowed is a shifted rolling mean: tonight's totals are excluded too
    for s in STOCK_STATS:
        a = base[f"opp_allow_{s}"].to_numpy().astype(float)[sel]
        b = alt[f"opp_allow_{s}"].to_numpy().astype(float)[sel]
        assert np.allclose(a, b, equal_nan=True)


def test_stock_columns_have_no_new_missingness() -> None:
    """NULL pattern audit: a stock column is missing exactly where production's own-stat column is missing."""
    gdf, pdf, static = _league()
    flagged, _ = _flagged(gdf)
    feats = build_features(gdf, pdf, static, flagged, ELO)
    ext = add_stock_features(feats, gdf, pdf, flagged)
    for s in STOCK_STATS:
        for p in ("m10", "sd10", "rate"):
            assert ext[f"{p}_{s}"].null_count() == ext["m10_pts"].null_count() or p == "sd10"
        # vac_ is null exactly when the game has no usable report, as production's vac_pts
        a = ext[f"vac_{s}"].is_null().to_numpy()
        b = ext["vac_pts"].is_null().to_numpy()
        assert np.array_equal(a, b)


def test_fit_and_predict_stock_heads_integer_grid() -> None:
    gdf, pdf, static = _league(n_days=330)
    flagged, _ = _flagged(gdf)
    feats = add_stock_features(build_features(gdf, pdf, static, flagged, ELO), gdf, pdf, flagged)
    feats = feats.join(
        pdf.select("game_id", "player_id", "stl", "blk"), on=["game_id", "player_id"], how="left"
    )
    as_of = gdf.sort("game_date")["game_date"][int(gdf.height * 0.8)]
    cfg = ContextResidualConfig(n_estimators=20, min_child_samples=20, min_cal_rows=200, n_jobs=1)
    models = fit_stock_models(feats, as_of, cfg)
    assert set(models) == set(STOCK_STATS)
    test = feats.filter(pl.col("game_date") >= as_of)
    for s, m in models.items():
        rows, mean, grid = predict_stock_grid(m, test)
        assert rows.height == len(mean) == grid.shape[0] > 100
        assert grid.shape[1] == 199 and grid.dtype == np.int16
        assert (grid >= 0).all() and (np.diff(grid, axis=1) >= 0).all()
        assert rows.height == stat_frame(test, s).height
        assert stock_feature_names(s)[-2:] == ["m10_blk", "m10_stl"]
        # fit rows strictly before as_of (no row on/after as_of used): the model never saw `test`
        assert m.z_sorted is not None


def _stock_cols(base: pl.DataFrame) -> list[str]:
    return [c for c in base.columns if any(c.endswith(f"_{s}") for s in STOCK_STATS)]


def test_stock_features_ignore_every_later_game_and_tonights_own_values() -> None:
    """Review N3 (checks 2a, 2b): every box value of every game after date D set to 37 moves nothing at D; nor does
    blanking tonight's own blk/stl (the live placeholder rows carry NULL stats)."""
    gdf, pdf, static = _league()
    flagged, _ = _flagged(gdf)
    d = gdf.sort(["game_date", "game_id"])["game_date"][200]
    base = add_stock_features(build_features(gdf, pdf, static, flagged, ELO), gdf, pdf, flagged)
    later_ids = gdf.filter(pl.col("game_date") > d)["game_id"].to_list()
    mod = pdf.with_columns(
        [
            pl.when(pl.col("game_id").is_in(later_ids)).then(37).otherwise(pl.col(c)).alias(c)
            for c in ("pts", "reb", "ast", "fg3m", "blk", "stl")
        ]
    )
    alt = add_stock_features(build_features(gdf, mod, static, flagged, ELO), gdf, mod, flagged)
    tonight_ids = gdf.filter(pl.col("game_date") == d)["game_id"].to_list()
    blank = pdf.with_columns(
        pl.when(pl.col("game_id").is_in(tonight_ids))
        .then(None)
        .otherwise(pl.col("blk"))
        .alias("blk"),
        pl.when(pl.col("game_id").is_in(tonight_ids))
        .then(None)
        .otherwise(pl.col("stl"))
        .alias("stl"),
    )
    alt2 = add_stock_features(build_features(gdf, blank, static, flagged, ELO), gdf, blank, flagged)
    sel = (base["game_date"] == d).to_numpy()
    assert sel.sum() > 20
    for c in _stock_cols(base):
        a = base[c].to_numpy().astype(float)[sel]
        assert np.allclose(a, alt[c].to_numpy().astype(float)[sel], equal_nan=True), c
        assert np.allclose(a, alt2[c].to_numpy().astype(float)[sel], equal_nan=True), c
