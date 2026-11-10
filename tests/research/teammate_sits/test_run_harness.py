# ruff: noqa: E501
"""TEAMMATE_SITS scoring harness: rule refusal, decision words on synthetic worlds, and the label-free pipeline on a
synthetic league (no DB, no real outcome)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig, build_features
from research.eval import teammate_sits_eval as E
from tests.research.teammate_sits.test_gate import _world
from tests.research.teammate_sits.test_scenario import ELO

ROOT = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ rule


def test_verify_rule_accepts_the_frozen_text() -> None:
    assert E.verify_rule(ROOT / E.RULE) == E.FROZEN_SHA
    assert E.verify_rule(ROOT / E.RULE, sha=E.FROZEN_SHA) == E.FROZEN_SHA


def test_verify_rule_refuses_edits_drafts_and_other_shas(tmp_path: Path) -> None:
    text = (ROOT / E.RULE).read_text()
    edited = tmp_path / "edited.md"
    edited.write_text(text.replace("FLOOR = -0.005", "FLOOR = -0.004", 1))
    with pytest.raises(RuntimeError, match="hash"):
        E.verify_rule(edited)
    draft = tmp_path / "draft.md"
    draft.write_text(text.replace("Status: **FROZEN", "Status: **DRAFT", 1))
    with pytest.raises(RuntimeError, match="FROZEN"):
        E.verify_rule(draft)
    with pytest.raises(RuntimeError, match="sha"):
        E.verify_rule(ROOT / E.RULE, sha="0" * 64)
    # text below the marker (the results section) does not change the hash
    tail = tmp_path / "tail.md"
    tail.write_text(text + "\nresults appended\n")
    assert E.verify_rule(tail) == E.FROZEN_SHA


def test_refuses_holdout_season() -> None:
    with pytest.raises(RuntimeError, match="2025"):
        E.require_season(2025)
    E.require_season(2024)


# ------------------------------------------------------------------ synthetic worlds


def _arrays(seed: int, n: int = 3000) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    base = np.sort(rng.normal(12, 5, (n, 199)), axis=1).round()
    shift = rng.uniform(0.0, 3.0, n)  # the scenario moves each teammate by a different amount
    q_u = base
    q_s = np.sort((base + shift[:, None]).round(), axis=1)
    return {
        "game_id": np.array([f"g{i // 10}" for i in range(n)]),
        "player_id": np.arange(n) % 700,
        "sitter_id": np.arange(n) // 40,
        "k": rng.integers(1, 4, n),
        "sitter_min10": rng.uniform(20, 38, n),
        "sitter_pos": rng.integers(0, 3, n),
        "pos_code": rng.integers(0, 3, n),
        "starter10": rng.uniform(0, 1, n),
        "q_s": q_s,
        "q_u": q_u,
        "mean_s": q_s.mean(axis=1),
        "mean_u": q_u.mean(axis=1),
    }


def _world_scores(truth: str) -> dict[str, dict[str, object]]:
    scores: dict[str, dict[str, object]] = {}
    for i, stat in enumerate(E.STATS):
        for season in E.SEASONS:
            r = _arrays(100 * i + season)
            rng = np.random.default_rng(season + i)
            q = r["q_s"] if truth == "S" else r["q_u"]
            y = q[np.arange(len(q)), rng.integers(0, 199, len(q))]
            scores.setdefault(stat, {})[str(season)] = E.score_season(r, y, season)
    return scores


ALL_PASS = {s: ("PASS", "PASS") for s in E.STATS}


def test_planted_helps_world_is_detected() -> None:
    sc = _world_scores("S")
    dec = E.decide(sc, ALL_PASS)
    for s in E.STATS:
        assert dec[s]["word"] == "HELPS", (s, sc[s][str(E.REPORT)]["point"])  # type: ignore[index]
        assert dec[s]["slope_word"] in ("RIGHT_SIZED", "UNDER_REACTS", "OVER_REACTS")
    assert not dec["kill"]["pts_display_closed"]


def test_null_world_is_not_detected() -> None:
    sc = _world_scores("U")
    dec = E.decide(sc, ALL_PASS)
    for s in E.STATS:
        assert dec[s]["word"] in ("HURTS", "INCONCLUSIVE", "CLOSED_TIE")
        assert dec[s]["word"] != "HELPS"
        assert dec[s]["slope_word"] == "NO_SIGNAL"
        assert not dec[s]["display_ok"]
    assert dec["kill"]["pts_display_closed"]  # a null world makes the shift hurt


def test_first_match_order_and_unresolved() -> None:
    sc = _world_scores("S")
    dec = E.decide(sc, E.FROZEN_G2)
    assert dec["fg3m"]["word"] == "UNRESOLVED" and dec["fg3m"]["slope_word"] == "SLOPE_UNRESOLVED"
    assert dec["pts"]["word"] == "HELPS"
    # HURTS outranks UNRESOLVED
    scn = _world_scores("U")
    assert E.decide(scn, E.FROZEN_G2)["fg3m"]["word"] == "HURTS"


def test_closed_tie_needs_the_ci_to_exclude_the_floor() -> None:
    r = _arrays(7)
    r["q_s"] = r["q_u"].copy()
    r["mean_s"] = r["mean_u"].copy()
    y = r["q_u"][:, 100]
    s = E.score_season(r, y, 2024)
    assert s["point"] == 0.0 and s["binding"]["lo"] == 0.0
    sc = {st: {"2023": s, "2024": s} for st in E.STATS}
    assert E.decide(sc, ALL_PASS)["pts"]["word"] == "CLOSED_TIE"


def test_bootstrap_is_deterministic() -> None:
    r = _arrays(3)
    d = np.random.default_rng(0).normal(size=len(r["game_id"]))
    assert E.boot_mean(d, r["game_id"]) == E.boot_mean(d, r["game_id"])


def test_top5_share() -> None:
    d = np.array([5.0, 1.0, 1.0, 1.0, 1.0, 1.0, -1.0])
    key = np.array(list("abcdefg"))
    assert E.top5_share(d, key) == pytest.approx(9.0 / 9.0)


# ------------------------------------------------------------------ pipeline (synthetic league, label-free)


def test_pipeline_up_to_the_outcome_join_on_a_synthetic_league() -> None:
    w = _world()
    w["cfg"] = ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300)
    feats = build_features(w["games"], w["pgs"], w["static"], w["flagged"], ELO)  # type: ignore[arg-type]
    arm_s, arm_u, sitters = E.build_arms(w, feats)
    assert arm_s.height == arm_u.height > 0
    heads = E.walk_heads(
        feats,
        arm_s,
        arm_u,
        sitters,
        w["static"],  # type: ignore[arg-type]
        w["cfg"],
        "pts",
        seasons=(2023,),
        cache=None,
    )
    h = heads["2023"]
    assert h["q_s"].shape == h["q_u"].shape and h["q_s"].shape[1] == 199
    assert np.array_equal(h["q_s"], np.round(h["q_s"]))  # integer support
    # blinding is inert: a head predicts identically from raw and blinded test rows
    from nba.props.context_residual import (
        CRPS_TAUS,
        ContextResidualModel,
        predict_rows,
        stat_feature_names,
        stat_frame,
    )

    blk = arm_s.filter(pl.col("season") == 2023)
    start = blk["game_date"].min()
    train = (
        stat_frame(feats, "pts")
        .filter(pl.col("game_date") < start)
        .sort(["game_date", "game_id", "player_id"])
    )
    m = ContextResidualModel("pts", w["cfg"], stat_feature_names("pts")).fit(train)  # type: ignore[arg-type]
    q_raw = predict_rows({"pts": m}, blk, "pts", CRPS_TAUS)[2]
    q_bl = predict_rows({"pts": m}, E.blind(blk), "pts", CRPS_TAUS)[2]
    assert np.array_equal(q_raw, q_bl)
    assert E.g_det(feats, arm_s, w["cfg"], "pts")
    assert E.planted_future(w, sitters, n=3, season=2023)["pass"]


def test_blind_zeroes_every_outcome_column() -> None:
    f = pl.DataFrame(
        {"pts": [10, 3], "reb": [2, 1], "ast": [1, 1], "fg3m": [0, 2], "x": [1.0, 2.0]}
    )
    b = E.blind(f)
    assert b.select("pts", "reb", "ast", "fg3m").sum().row(0) == (0, 0, 0, 0)
    assert b["x"].to_list() == [1.0, 2.0]


def test_outcome_join_aligns_rows() -> None:
    labels = pl.DataFrame({"game_id": ["a", "b"], "player_id": [1, 2], "pts": [7, 9]})
    r = {"game_id": np.array(["b", "a"]), "player_id": np.array([2, 1])}
    assert E.outcomes(labels, r, "pts").tolist() == [9.0, 7.0]
