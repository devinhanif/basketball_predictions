"""Opt-in tracking hook leaves production behaviour byte-identical when off, and the
screen runs end to end on a synthetic league."""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    stat_feature_names,
)
from research.eval.tracking_screen import (
    apply_rule,
    compare_arms,
    missingness_audit,
    run_screen_frames,
)
from research.features.tracking_features import ALL_FAMILIES, build_tracking_features
from tests.props.test_context_residual import ELO, _league

PINNED_FEATURE_COUNT = 42  # production feature count per stat (guards silent additions)


def _tracking_for(pdf: pl.DataFrame):
    rng = np.random.default_rng(1)
    cols_t = {c for f in ALL_FAMILIES.values() for c in f.tracking_cols}
    cols_h = {c for f in ALL_FAMILIES.values() for c in f.hustle_cols}
    base = pdf.filter(pl.col("minutes") > 0).select("game_id", "player_id")
    base = base.filter(pl.col("game_id").str.slice(-1) != "7")  # some games lack tracking
    n = base.height
    t = base.with_columns([pl.Series(c, rng.poisson(5, n).astype(float)) for c in sorted(cols_t)])
    h = base.with_columns([pl.Series(c, rng.poisson(2, n).astype(float)) for c in sorted(cols_h)])
    return t, h


def test_flag_off_is_byte_identical_and_names_unchanged() -> None:
    gdf, pdf, static = _league(n_days=60)
    a = build_features(gdf, pdf, static, {}, ELO)
    b = build_features(gdf, pdf, static, {}, ELO, tracking_feats=None)
    assert a.equals(b) and a.columns == b.columns
    for s in PROP_STATS:
        names = stat_feature_names(s)
        assert names == stat_feature_names(s, ())
        assert len(names) == PINNED_FEATURE_COUNT
        assert not any(n.startswith(("trk_", "hus_")) for n in names)


def test_flag_on_only_appends_columns() -> None:
    gdf, pdf, static = _league(n_days=60)
    t, h = _tracking_for(pdf)
    played = pdf.join(gdf.select("game_id", "game_date"), on="game_id").filter(
        pl.col("minutes") > 0
    )
    tf = build_tracking_features(played, t, h, static, ("passing",))
    base = build_features(gdf, pdf, static, {}, ELO)
    on = build_features(gdf, pdf, static, {}, ELO, tracking_feats=tf)
    assert on.height == base.height  # left join on unique keys: no row multiplication
    assert on.select(base.columns).equals(base)
    assert stat_feature_names("pts", ("trk_passes",))[:PINNED_FEATURE_COUNT] == stat_feature_names(
        "pts"
    )


def test_screen_end_to_end_synthetic_and_same_rows() -> None:
    gdf, pdf, static = _league(n_days=330)
    t, _ = _tracking_for(pdf)
    avail = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime,
            "source": pl.Utf8,
        }
    )
    cfg = ContextResidualConfig(n_estimators=20, min_child_samples=20, min_cal_rows=200, n_jobs=1)
    res = run_screen_frames(
        gdf, pdf, static, avail, ELO, t, None, cfg, n_boot=50, test_seasons=(2023,),
        stats=("ast",), report_season=2023,
    )  # fmt: skip
    assert res["status"] == "ok" and not res["audit"]["stop"]
    assert res["families_not_run"] == ["rebounding", "activity"]
    assert "passing|ast" in res["cells"] and "activity_T|ast" in res["cells"]
    for v in res["verdicts"].values():
        assert set(v["checks"]) >= {"floor", "ci_excludes_0", "bh_q", "cov80_guard", "audit_clean"}


def test_audit_flags_planted_null_vs_minutes_asymmetry() -> None:
    n = 400
    feats = pl.DataFrame(
        {
            "game_id": [str(i) for i in range(n)],
            "player_id": [1] * n,
            "pts": [1.0] * n,
            "trk_x": [None if i % 2 == 0 else 1.0 for i in range(n)],
        },
        schema_overrides={"trk_x": pl.Float64},
    )
    mins = pl.DataFrame(
        {
            "game_id": [str(i) for i in range(n)],
            "player_id": [1] * n,
            "minutes": [2.0 if i % 2 == 0 else 20.0 for i in range(n)],  # null <=> minutes < 5
        }
    )
    aud = missingness_audit(feats, mins, ["trk_x"], pl.Series([True] * n))
    assert aud["stop"] and aud["columns"]["trk_x"]["asymmetry"] == 1.0


def test_rule_requires_all_guards() -> None:
    n = 400
    base = pl.DataFrame(
        {
            "game_id": [str(g) for g in np.arange(n) // 4],
            "player_id": list(range(n)),
            "season": [2024] * n,
            "n_prior": [20] * n,
            "y": [1.0] * n,
            "mean": [1.0] * n,
            "q10": [0.0] * n,
            "q90": [2.0] * n,
            "crps": [1.0] * n,
        }
    )
    better = base.with_columns((pl.col("crps") - 0.02).alias("crps"))
    cmp = compare_arms(base, better, n_boot=50)
    v = apply_rule({"c": {"report": cmp["report"]}}, audit_stop=False)
    assert v["c"]["checks"]["floor"] and v["c"]["checks"]["cov80_guard"]
    v2 = apply_rule({"c": {"report": cmp["report"]}}, audit_stop=True)
    assert not v2["c"]["pass"]
