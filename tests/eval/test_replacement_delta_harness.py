# ruff: noqa: E501
"""REPLACEMENT_DELTA harness tests: the statistics, the words in each synthetic world, and the refusals (no database)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from research.eval import replacement_delta_eval as ev
from research.eval import replacement_delta_rule as rule

N = 4000


def synth(effect: float, harm: float = 0.0, seed: int = 0) -> tuple[Any, Any, Any, Any, Any]:
    """Two seasons of rows; arms are integer 199-grids around a per-row centre; RD shifts affected rows by ``effect`` (truth shifts them by
    ``effect`` too when the world has one)."""
    rng = np.random.default_rng(seed)
    n = 2 * N
    season = np.r_[np.full(N, 2023), np.full(N, 2024)]
    gid = np.array([f"00222{i // 8:05d}" for i in range(n)])
    aff = rng.random(n) < 0.8
    centre = rng.uniform(6, 22, n)
    taus = (np.arange(1, 200) - 0.5) / 199
    from scipy.stats import norm

    def grid(shift: np.ndarray) -> np.ndarray:
        q: np.ndarray = np.ceil(
            centre[:, None] + shift[:, None] + 4.0 * norm.ppf(taus)[None, :] - 0.5
        )
        return q.astype(np.int16)

    true_shift = np.where(aff, effect, 0.0)
    y = np.ceil(centre + true_shift + 4.0 * rng.standard_normal(n) - 0.5)
    cand = np.where(aff, effect, 0.0) + harm * rng.standard_normal(n)
    qi = {
        "pts": {
            "A0": grid(np.zeros(n)),
            "RD": grid(cand),
            "V0": grid(0.5 * cand),
            "PQ": grid(rng.permutation(cand)),
        }
    }
    ft = {
        "affected": aff,
        "n_out": np.where(aff, rng.integers(1, 4, n), 0).astype(float),
        "last_starter": rng.integers(0, 2, n).astype(float),
        "team_game_no": rng.integers(0, 82, n).astype(float),
        "gid": gid,
        "qv_pts": rng.normal(0, 1, n),
    }
    return qi, {"pts": y}, gid, season, ft


def test_bh_adjust_is_monotone_and_bounded() -> None:
    p = np.array([0.001, 0.02, 0.04, 0.5])
    a = ev.bh_adjust(p)
    assert (a >= p - 1e-12).all() and (a <= 1).all() and (np.diff(a[np.argsort(p)]) >= -1e-12).all()


def test_bootstrap_is_cluster_wide() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.arange(300), 10).astype(str)
    v = np.repeat(rng.normal(0, 1, 300), 10)  # perfectly clustered noise
    m = np.ones(len(v), bool)
    wide = ev.Boot(gid, b=2000).ci(v, m)
    row = ev.Boot(np.arange(len(v)).astype(str), b=2000).ci(v, m)
    assert (wide["hi"] - wide["lo"]) > 2 * (row["hi"] - row["lo"])


def test_effect_world_passes_null_and_harm_do_not() -> None:
    qi, y, gid, season, ft = synth(effect=-1.5)
    dec = ev.decide(ev.score_arms(qi, y, gid, season, ft, {"pts": 0.5}), 0.003)
    assert dec["words"]["pts"] in {"PASS_QUALITY", "PASS_WARM_ONLY", "GUARD_FAIL"}
    assert dec["detail"]["pts"]["primary"] == {2023: True, 2024: True}
    qi, y, gid, season, ft = synth(effect=0.0)
    ft_n = ft
    qi = {"pts": {**qi["pts"], "RD": qi["pts"]["A0"]}}
    dec0 = ev.decide(ev.score_arms(qi, y, gid, season, ft_n, {"pts": 0.5}), 0.003)
    assert dec0["words"]["pts"] == "CLOSED"
    qi, y, gid, season, ft = synth(effect=0.0, harm=3.0)
    dec_h = ev.decide(ev.score_arms(qi, y, gid, season, ft, {"pts": 0.5}), 0.003)
    assert dec_h["words"]["pts"] == "CLOSED" and not dec_h["any_pass"]
    assert not dec_h["detail"]["pts"]["guards"]["G1_no_harm_2024"]


def test_quality_word_needs_rd_to_beat_v0() -> None:
    qi, y, gid, season, ft = synth(effect=-1.5)
    qi["pts"]["V0"] = qi["pts"][
        "RD"
    ]  # the warm twin gains exactly as much: the subtraction adds nothing
    dec = ev.decide(ev.score_arms(qi, y, gid, season, ft, {"pts": 0.5}), 0.003)
    assert dec["words"]["pts"] == "PASS_WARM_ONLY"


def test_arm_columns_are_four_per_stat() -> None:
    for arm in ("RD", "V0", "PQ"):
        cols = ev.arm_columns(arm, "ast")
        assert (
            len(cols) == 4
            and cols[-2:] == ["share", "n_out"]
            and all(c.endswith("_ast") for c in cols[:2])
        )


def test_run_refuses_without_t_c_and_without_a_freeze() -> None:
    with pytest.raises(RuntimeError, match="not sized"):
        ev.run("0" * 64)
    old = ev.T_C
    ev.T_C = 0.003
    try:
        with pytest.raises(RuntimeError):
            ev.run("0" * 64)  # not frozen: refuses before any data is read
    finally:
        ev.T_C = old


def test_freeze_text_hash_stops_at_the_marker() -> None:
    a = "rule text\n=== RESULTS BELOW ===\nnot run\n"
    b = "rule text\n=== RESULTS BELOW ===\nsomething else\n"
    assert (
        rule.text_sha(a)
        == rule.text_sha(b)
        != rule.text_sha("rule text changed\n=== RESULTS BELOW ===\n")
    )


def test_pin_changes_when_any_pinned_file_changes() -> None:
    blobs = {k: "a" * 64 for k in rule.HARNESS_FILES}
    p0 = rule.pin_of(blobs)
    blobs2 = {**blobs, "nba/props/repl_delta.py": "b" * 64}
    assert rule.pin_of(blobs2) != p0
    assert (
        "nba/props/repl_delta.py" in rule.HARNESS_FILES
        and "nba/props/context_residual.py" in rule.HARNESS_FILES
    )


def test_require_ready_refuses_when_not_frozen() -> None:
    with pytest.raises(RuntimeError):
        rule.require_ready(None)
    with pytest.raises(RuntimeError):
        rule.require_ready("f" * 64, run=lambda *a, **k: "", ok=lambda *a, **k: False)


def test_quality_word_when_the_warm_twin_gains_nothing() -> None:
    qi, y, gid, season, ft = synth(effect=-1.5)
    qi["pts"]["V0"] = qi["pts"][
        "A0"
    ]  # the unsubtracted twin carries no signal: the whole gain is the quality column
    dec = ev.decide(ev.score_arms(qi, y, gid, season, ft, {"pts": 0.5}), 0.003)
    assert dec["words"]["pts"] == "PASS_QUALITY"
