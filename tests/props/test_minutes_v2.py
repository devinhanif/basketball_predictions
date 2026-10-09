"""Minutes model v2 (docs/MINUTES_V2.md): as-of discipline, DNP handling, flag-off identity."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import (
    CRPS_TAUS,
    build_features,
    stat_feature_names,
    stat_frame,
)
from nba.props.minutes_v2 import (
    MV2_FEATURES,
    V2_FEATURES,
    V2_MINUTES_FEATURES,
    MinutesV2Model,
    attach_minutes_v2,
    build_v2_features,
    player_sequence_features,
    recency_ratio_quantiles,
    summarize_quantiles,
    trunc_normal_quantiles,
)
from tests.props.test_context_residual import ELO, _league


def _inputs(seed: int = 0):
    gdf, pdf, static = _league(seed=seed, n_days=200)
    rng = np.random.default_rng(seed + 7)
    pdf = pdf.with_columns(pl.Series("pf", rng.integers(0, 6, pdf.height)))
    static = static.with_columns(pl.lit(dt.date(1995, 3, 1)).alias("birth_date"))
    return gdf, pdf, static


def _v2(gdf, pdf, static, flagged=None):
    flagged = flagged or {}
    feats = build_features(gdf, pdf, static, flagged, ELO)
    return feats, build_v2_features(feats, gdf, pdf, static, flagged, None)


def _same(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    a = a.sort(["game_id", "player_id"])
    b = b.sort(["game_id", "player_id"])
    if a.shape != b.shape:
        return False
    for c in a.columns:
        x, y = a[c].to_numpy().astype(float), b[c].to_numpy().astype(float)
        if not np.allclose(x, y, equal_nan=True):
            return False
    return True


def test_flag_off_is_identity_and_production_features_unchanged() -> None:
    gdf, pdf, static = _inputs()
    feats, v2 = _v2(gdf, pdf, static)
    mv2 = v2.with_columns([pl.lit(1.0).alias(c) for c in MV2_FEATURES])
    assert attach_minutes_v2(feats, mv2) is feats  # default off: same object
    assert attach_minutes_v2(feats, None, enabled=False) is feats
    on = attach_minutes_v2(feats, mv2, enabled=True)
    assert on.height == feats.height and set(MV2_FEATURES) <= set(on.columns)
    for stat in ("pts", "reb", "ast", "fg3m"):
        names = stat_feature_names(stat)
        assert not set(names) & (set(MV2_FEATURES) | set(V2_FEATURES))
    assert set(feats.columns).isdisjoint(MV2_FEATURES)


def test_v2_covers_played_rows_only_and_dnp_rows_do_not_matter() -> None:
    gdf, pdf, static = _inputs()
    assert pdf["minutes"].null_count() > 0  # fixture has DNP rows
    feats, v2 = _v2(gdf, pdf, static)
    assert v2.height == feats.height
    pdf_nodnp = pdf.filter(pl.col("minutes").is_not_null())
    feats2, v2b = _v2(gdf, pdf_nodnp, static)
    assert _same(v2, v2b)  # DNP rows never enter any state
    assert set(V2_FEATURES) <= set(v2.columns)


def test_planted_future_game_does_not_change_earlier_rows() -> None:
    gdf, pdf, static = _inputs()
    _, base = _v2(gdf, pdf, static)
    last = gdf["game_date"].max()
    last_ids = gdf.filter(pl.col("game_date") == last)["game_id"].to_list()
    pdf2 = pdf.with_columns(
        pl.when(pl.col("game_id").is_in(last_ids) & pl.col("minutes").is_not_null())
        .then(pl.lit(47.0))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(pl.col("game_id").is_in(last_ids))
        .then(pl.lit(6))
        .otherwise(pl.col("pf"))
        .alias("pf"),
    )
    gdf2 = gdf.with_columns(
        pl.when(pl.col("game_id").is_in(last_ids))
        .then(pl.col("home_pts") + 60)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    _, mod = _v2(gdf2, pdf2, static)
    early = gdf.filter(pl.col("game_date") < last)["game_id"].to_list()
    assert _same(
        base.filter(pl.col("game_id").is_in(early)), mod.filter(pl.col("game_id").is_in(early))
    )
    # and the planted game itself does change the NEXT-game state only through earlier rows: its
    # own rows are not allowed to differ either (same-game values are never inputs)
    assert _same(
        base.filter(pl.col("game_id").is_in(last_ids)),
        mod.filter(pl.col("game_id").is_in(last_ids)),
    )


def test_planted_same_game_values_do_not_change_own_features() -> None:
    gdf, pdf, static = _inputs()
    _, base = _v2(gdf, pdf, static)
    g = gdf.sort(["game_date", "game_id"]).row(90, named=True)
    gid = g["game_id"]
    pdf2 = pdf.with_columns(
        pl.when((pl.col("game_id") == gid) & pl.col("minutes").is_not_null())
        .then(pl.lit(1.0))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(pl.col("game_id") == gid).then(pl.lit(6)).otherwise(pl.col("pf")).alias("pf"),
        pl.when(pl.col("game_id") == gid).then(pl.lit(0)).otherwise(pl.col("pts")).alias("pts"),
    )
    gdf2 = gdf.with_columns(
        pl.when(pl.col("game_id") == gid)
        .then(pl.col("home_pts") + 70)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    _, mod = _v2(gdf2, pdf2, static)
    assert _same(base.filter(pl.col("game_id") == gid), mod.filter(pl.col("game_id") == gid))


def test_return_ramp_and_foul_features_on_a_synthetic_player() -> None:
    d0 = dt.date(2023, 1, 1)
    dates = [d0 + dt.timedelta(days=k) for k in (0, 2, 4, 30, 32, 34)]  # 26-day absence before #4
    n = len(dates)
    played = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(n)],
            "player_id": [1] * n,
            "season": [2022] * n,
            "game_date": dates,
            "minutes": [30.0, 32.0, 30.0, 14.0, 20.0, 24.0],
            "pf": [2, 3, 5, 1, 2, 2],
            "b2b": [0, 0, 0, 0, 0, 0],
            "margin": [5, 5, 5, 5, 5, 20],
            "min10": [30.0, 30.0, 31.0, 31.0, 22.0, 22.0],
        }
    )
    out = player_sequence_features(played)
    ret_n = out["ret_n"].to_list()
    assert ret_n[:3] == [99.0, 99.0, 99.0]  # no absence yet
    assert ret_n[3] == 0.0 and ret_n[4] == 1.0 and ret_n[5] == 2.0
    rm = out["ret_mean_min"].to_numpy()
    assert np.isnan(rm[3]) and rm[4] == 14.0 and abs(rm[5] - 17.0) < 1e-9
    ramp = out["ret_ramp"].to_numpy()
    assert abs(ramp[4] - 14.0 / 31.0) < 1e-9  # pre-absence min10 captured at the return game
    pf36 = out["pf36_h20"].to_numpy()
    assert np.isnan(pf36[0])  # nothing before the first game
    assert pf36[3] > pf36[1] * 0.0  # defined
    # the row's own foul count never enters its own feature
    played2 = played.with_columns(
        pl.when(pl.col("game_id") == "g3").then(pl.lit(6)).otherwise(pl.col("pf")).alias("pf")
    )
    out2 = player_sequence_features(played2)
    assert np.allclose(
        out2["pf36_h20"].to_numpy()[:4], pf36[:4], equal_nan=True
    ) and not np.allclose(out2["pf36_h20"].to_numpy()[4], pf36[4])


def test_out_teammate_features_count_starters_and_exclude_self() -> None:
    gdf, pdf, static = _inputs()
    g1 = gdf.sort(["game_date", "game_id"]).row(120, named=True)
    team = g1["home_team"]
    out_pid = team * 100 + 0  # a starter
    flagged = {g1["game_id"]: {out_pid}}
    feats, v2 = _v2(gdf, pdf, static, flagged)
    j = feats.select("game_id", "player_id", "team_id").join(v2, on=["game_id", "player_id"])
    sub = j.filter((pl.col("game_id") == g1["game_id"]) & (pl.col("team_id") == team))
    mates = sub.filter(pl.col("player_id") != out_pid)
    assert (mates["n_out_starters"] == 1.0).all() and (mates["out_max_min40"] > 20).all()
    own = sub.filter(pl.col("player_id") == out_pid)  # played despite the report: self excluded
    assert own.is_empty() or (own["n_out_starters"] == 0.0).all()
    other = j.filter(pl.col("game_id") != g1["game_id"])
    assert other["n_out_starters"].null_count() == other.height  # no report -> null, not 0


def test_model_quantiles_are_monotone_and_ignore_own_minutes() -> None:
    gdf, pdf, static = _inputs()
    rng = np.random.default_rng(5)
    shorts = rng.random(pdf.height) < 0.12  # plant short-minutes games so the mixture is active
    pdf = pdf.with_columns(
        pl.when(pl.Series(shorts) & pl.col("minutes").is_not_null())
        .then(pl.lit(4.0))
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    feats, v2 = _v2(gdf, pdf, static)
    f = feats.join(v2, on=["game_id", "player_id"], how="left")
    f = f.join(
        pdf.select("game_id", "player_id", "minutes").filter(pl.col("minutes") > 0),
        on=["game_id", "player_id"],
        how="left",
    )
    sf = stat_frame(f, "pts").sort(["game_date", "game_id", "player_id"])
    cut = sf["game_date"].quantile(0.8, "nearest")
    train, test = sf.filter(pl.col("game_date") < cut), sf.filter(pl.col("game_date") >= cut)
    for kind in ("mixture", "single"):
        m = MinutesV2Model(list(V2_MINUTES_FEATURES), kind).fit(train)
        q = m.quantiles(test)
        assert q.shape == (test.height, len(CRPS_TAUS))
        assert (np.diff(q, axis=1) >= -1e-9).all() and q.min() >= 0 and q.max() <= 60
        noisy = test.with_columns(
            pl.Series("minutes", np.random.default_rng(1).random(test.height) * 99)
        )
        assert np.array_equal(m.quantiles(noisy), q)  # the realised minutes are never an input
        feat = summarize_quantiles(q, m.pi(test))
        assert list(feat.columns) == list(MV2_FEATURES)
        assert (feat["mv2_q10"] <= feat["mv2_q50"]).all() and (
            feat["mv2_q50"] <= feat["mv2_q90"]
        ).all()
    pi = MinutesV2Model(list(V2_MINUTES_FEATURES), "mixture").fit(train).pi(test)
    assert pi.min() >= 0.005 - 1e-12 and pi.max() <= 0.5 + 1e-12 and pi.mean() > 0.02


def test_comparator_quantile_helpers() -> None:
    q = trunc_normal_quantiles(np.array([30.0, 5.0]), np.array([6.0, 8.0]))
    assert (np.diff(q, axis=1) >= 0).all() and q.min() >= 0 and q.max() <= 48
    r = recency_ratio_quantiles(
        np.array([20.0, 30.0, 10.0, 25.0]), np.array([25.0, 30.0, 25.0, 25.0]), np.array([28.0])
    )
    assert r.shape == (1, len(CRPS_TAUS)) and (np.diff(r, axis=1) >= 0).all()


# --------------------------------------------------------------------------- runner smoke


def _world(gdf, pdf, static):
    import duckdb

    from nba.props.context_residual import ContextResidualConfig

    sc = duckdb.connect(":memory:")
    games = gdf.with_columns(pl.col("game_date").cast(pl.Date))
    for name, df in (("games", games), ("player_game_stats", pdf), ("team_context", None)):
        if df is None:
            continue
        sc.register("_s", df)
        sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _s")
        sc.unregister("_s")
    cfg = ContextResidualConfig(n_estimators=20, n_jobs=2, min_cal_rows=300)
    return {"cfg": cfg, "sc": sc}


def test_run_minutes_and_props_walk_smoke(tmp_path, monkeypatch) -> None:
    import nba.eval.minutes_v2_eval as ev

    gdf, pdf, static = _inputs()
    feats, v2 = _v2(gdf, pdf, static)
    from nba.eval.lower_tail_eval import attach_realised

    frame = attach_realised(feats.join(v2, on=["game_id", "player_id"], how="left"), gdf, pdf)
    world = _world(gdf, pdf, static)
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path / "cache")
    ev.run_minutes(world, frame, tmp_path / "out")
    z = dict(np.load(tmp_path / "out" / "minutes_scores.npz", allow_pickle=True))
    n = len(z["row_y"])
    assert n > 200 and set(np.unique(z["row_season"])) == {2023}
    for arm in ev.MINUTES_ARMS:
        assert z[f"{arm}_crps"].shape == (n,) and np.isfinite(z[f"{arm}_crps"]).all()
        assert ((z[f"{arm}_pit"] >= 0) & (z[f"{arm}_pit"] <= 1)).all()
    mv2 = pl.read_parquet(tmp_path / "cache" / "mv2.parquet")
    assert list(mv2.columns) == ["game_id", "player_id", *MV2_FEATURES]
    # props walk with the appended mv2 columns, and with the oracle-style extra
    fm = frame.join(mv2, on=["game_id", "player_id"], how="left")
    res = ev.props_walk(fm, "pts", MV2_FEATURES, world["cfg"], test_seasons=(2023,))
    assert len(res["y"]) == n and np.isfinite(res["crps_int"]).all()
    ctrl = ev.props_walk(frame, "pts", (), world["cfg"], test_seasons=(2023,))
    assert np.array_equal(ctrl["pid"], res["pid"])
    perm = ev.permute_within_season(fm, MV2_FEATURES)
    assert np.isnan(perm["mv2_mean"].to_numpy()).sum() == np.isnan(fm["mv2_mean"].to_numpy()).sum()
    assert not np.array_equal(
        perm["mv2_mean"].fill_null(-1).to_numpy(), fm["mv2_mean"].fill_null(-1).to_numpy()
    )


def test_eval_helpers() -> None:
    import nba.eval.minutes_v2_eval as ev

    assert ev.ece(np.array([0.9, 0.9, 0.1, 0.1]), np.array([1.0, 1.0, 0.0, 0.0])) < 0.11
    assert abs(ev._auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1])) - 1.0) < 1e-12
    q = np.tile(np.linspace(0, 60, 199), (50, 1))
    s = ev.score_minutes(q, np.full(50, 30.0))
    assert s["crps"].min() >= 0 and abs(s["mean"].mean() - 30.0) < 0.1
