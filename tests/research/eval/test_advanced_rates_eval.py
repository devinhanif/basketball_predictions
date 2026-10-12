# ruff: noqa: E501
"""Scoring, pass rule, placebo, knockout and the freeze guard of the ADVANCED_RATES harness (no model fit)."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from research.eval import advanced_rates_eval as E
from research.features import advanced_assemble as A


def test_arms_refuse_without_a_matching_freeze() -> None:
    assert E.FROZEN_FULL == "" or len(E.FROZEN_FULL) == 64
    if not E.FROZEN_FULL:
        with pytest.raises(RuntimeError, match="not frozen"):
            E.run_arms()


def test_placebo_permutes_jointly_within_player_season() -> None:
    f = pl.DataFrame(
        {
            "player_id": [1, 1, 1, 1, 2, 2, 2],
            "season": [2023, 2023, 2024, 2024, 2023, 2023, 2023],
            "a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "b": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
            "keep": [0, 1, 2, 3, 4, 5, 6],
        }
    )
    p = E.placebo_permute(f, ["a", "b"], seed=0)
    assert p["keep"].to_list() == f["keep"].to_list()  # untouched columns stay
    assert (p["b"] == p["a"] * 10).all()  # columns move together
    for pid, season in ((1, 2023), (1, 2024), (2, 2023)):  # marginal preserved per player-season
        x = f.filter((pl.col("player_id") == pid) & (pl.col("season") == season))["a"]
        y = p.filter((pl.col("player_id") == pid) & (pl.col("season") == season))["a"]
        assert sorted(x.to_list()) == sorted(y.to_list())
    assert p["a"].to_list() != f["a"].to_list()


def test_knockout_groups_and_carry_rule() -> None:
    g = E.knockout_groups("PLAYMAKING", A.FAMILY_COLUMNS["PLAYMAKING"])
    assert set(g) == {"ppr", "ast_ratio", "ast_pct", "tov_pct", "other"}
    assert all(len(v) == 6 for k, v in g.items() if k != "other") and g["other"] == ["adv_stint_n"]
    assert E.carries(-0.01, -0.001, -0.004)  # dropping it removes 90% of the gain
    assert E.carries(-0.01, -0.009, -0.0085)  # alone keeps 85%
    assert not E.carries(-0.01, -0.007, -0.004)
    assert not E.carries(0.002, 0.0, 0.0)


def _fake_arms(shift_b: float, shift_p: float) -> dict[str, dict[str, object]]:
    rng = np.random.default_rng(0)
    n = 2400
    game = np.repeat(np.arange(n // 8), 8)
    season = np.where(np.arange(n) < n // 2, 2023, 2024)
    y = rng.poisson(10, n)
    base = np.tile(np.round(np.linspace(2, 20, 199)), (n, 1))  # integer quantile grid, 199 taus

    def arm(shift: float) -> dict[str, object]:
        grid = np.round(
            10 + (base - 10) * (1.0 - shift)
        )  # shift = fraction compressed toward the centre
        return {"qi": grid.astype(np.int16), "y": y.astype(float), "meta": pl.DataFrame({
            "game_id": [f"g{x}" for x in game], "player_id": np.arange(n), "season": season, "n_prior": rng.integers(5, 40, n),
            "starter10": rng.random(n), "team_game_no": rng.integers(1, 80, n), "has_report": np.ones(n, dtype=int), "n_out_rot": np.zeros(n, dtype=int),
            "min_gap": np.zeros(n), "adv_stint_n": rng.integers(0, 40, n)})}  # fmt: skip

    a0 = arm(0.0)
    b, p = arm(shift_b), arm(shift_p)
    for x in (b, p):
        x["meta"] = a0["meta"]  # identical rows
    return {"A0": a0, "B": b, "P": p}


def test_score_cell_and_rules_on_fake_arms() -> None:
    arms = _fake_arms(0.0, 0.0)
    out = E.score_cell("pts", "PLAY_TYPE", arms)
    rows = [r for r in out["rows"] if r["arm"] == "B"]
    assert {r["season"] for r in rows} == {2023, 2024}
    assert all(abs(r["dcrps"]) < 1e-12 for r in rows)  # B == A0
    assert any(s["slice"] == "recent_move" and s["n"] > 0 for s in out["slices"])
    # a cell table over all 15 cells with an identical B: nothing selects, the run closes
    allrows, allsl = [], []
    for fam, stat in A.CELLS:
        o = E.score_cell(stat, fam, arms)
        allrows += o["rows"]
        allsl += o["slices"]
    E.add_bh(allrows)
    v = E.evaluate_cells({"rows": allrows, "slices": allsl}, tests_ok=True)
    assert len(v["cells"]) == 15 == len(A.CELLS)
    assert v["close"] and not any(v["families"].values())
    assert set(v["families"]) == set(A.FAMILY_COLUMNS) and len(v["families"]) == 7


def test_better_arm_is_selected_and_placebo_gate_works() -> None:
    arms = _fake_arms(0.5, 0.0)  # B compressed (sharper, well-calibrated for Poisson(10)), P = A0
    rows, sls = [], []
    for fam, stat in A.CELLS:
        o = E.score_cell(stat, fam, arms)
        rows += o["rows"]
        sls += o["slices"]
    E.add_bh(rows)
    v = E.evaluate_cells({"rows": rows, "slices": sls}, tests_ok=True)
    c = v["cells"]["PLAY_TYPE/pts"]
    assert c["select_2023"] and c["rule1_2024"] and c["beats_placebo_by_0.003"]
    assert not v["close"]
    # if the placebo is just as good, the placebo gate blocks the pass
    arms2 = _fake_arms(0.5, 0.5)
    rows2, sls2 = [], []
    for fam, stat in A.CELLS:
        o = E.score_cell(stat, fam, arms2)
        rows2 += o["rows"]
        sls2 += o["slices"]
    E.add_bh(rows2)
    v2 = E.evaluate_cells({"rows": rows2, "slices": sls2}, tests_ok=True)
    assert not v2["cells"]["PLAY_TYPE/pts"]["beats_placebo_by_0.003"]
    assert not v2["cells"]["PLAY_TYPE/pts"]["pass_before_knockout"]


def test_a0_names_are_the_production_names_and_b_only_appends() -> None:
    from nba.props.context_residual import stat_feature_names

    for stat in E.STATS:
        assert E.arm_names(stat, None) == stat_feature_names(stat)
        for fam in A.FAMILY_COLUMNS:
            b = E.arm_names(stat, fam)
            assert b[: len(stat_feature_names(stat))] == stat_feature_names(stat)
            assert len(b) == len(set(b))
