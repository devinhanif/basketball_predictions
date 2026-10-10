"""Smoke test of the T-30 vs T-60 evaluation plumbing on the synthetic league (no real DB)."""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.props.context_residual import ContextResidualConfig, build_features
from nba.props.lineup_features import T30_FEATURES, build_lineup_features
from research.eval.lineups_known_eval import (
    apply_rule,
    build_pplay_frame,
    pplay_walk_forward,
    starter_proxy_bounds,
    summarize,
    walk_forward_arms,
)
from tests.props.test_context_residual import ELO, _league


def test_arms_share_rows_and_starter_flag_helps_in_a_planted_world() -> None:
    gdf, pdf, static = _league(n_days=330)
    # planted world: who starts is random per game, and starters score +8 (unknowable from history)
    rng = np.random.default_rng(1)
    flag = rng.random(pdf.height) < 0.5
    boost = pdf.with_columns(pl.Series("starter", flag)).with_columns(
        pl.when(pl.col("minutes").is_null())
        .then(0)
        .when(pl.col("starter"))
        .then(pl.col("pts") + 8)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    feats = build_features(gdf, boost, static, {}, ELO, lineups_known=True)
    feats_s = feats.drop(list(T30_FEATURES)).join(
        build_lineup_features(gdf, boost, scratch_prob=0.5).drop("team_id"),
        on=["game_id", "player_id"],
        how="left",
    )
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300, n_jobs=1)
    res, imp = walk_forward_arms(feats, "pts", cfg, (2023,), feats_s)
    assert res.height > 500 and {"crps_a", "crps_b", "crps_s"} <= set(res.columns)
    assert set(imp) == set(T30_FEATURES)
    sm = {"pts": summarize(res, n_boot=100)}
    assert np.isfinite(sm["pts"]["delta"][0])
    assert "sensitivity_scratch" in sm["pts"]
    assert {s["slice"].split("=")[0] for s in sm["pts"]["slices"]} >= {"sl_status", "sl_role"}
    assert "pts" in apply_rule(sm)
    assert sm["pts"]["delta"][2] < 0  # planted tonight-only signal: B beats A, CI excludes 0
    assert res["crps_b"].mean() < res["crps_a"].mean()  # type: ignore[operator]


def test_pplay_frame_and_walk_forward_run() -> None:
    gdf, pdf, _ = _league(n_days=330)
    t30 = build_lineup_features(gdf, pdf)
    pf = build_pplay_frame(gdf, pdf, t30)
    assert pf.height == pdf.height and pf["p_raw"].is_between(0, 1).all()
    out = pplay_walk_forward(pf, (2023,), n_boot=50)
    assert out["all"]["overall"]["n"] > 100
    # every box-score starter in the synthetic league played, so the T-30 flag must help overall
    assert out["all"]["overall"]["ll_b"] <= out["all"]["overall"]["ll_a_cal"] + 1e-9
    b = starter_proxy_bounds(pdf.join(gdf.select("game_id", "season"), on="game_id"))
    assert b["share_team_games_not_5_starters"] == 0.0
