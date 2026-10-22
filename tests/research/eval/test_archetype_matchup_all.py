# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL harness: composite, shares, pass function, noise/sim equivalence with the reused power code, bars in code, refusal."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_eval as mev
from research.eval import archetype_matchup_all_gate as gate
from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_rule as t506
from research.eval import role_prior_salary_eval as ev
from research.eval import role_prior_salary_rule as role_rule

ROOT = Path(__file__).resolve().parents[3]
MODS = ("rule", "gate", "eval")


def _grid(n: int = 300, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    base = rng.uniform(2, 12, size=n)
    q = np.sort(rng.poisson(base[:, None], size=(n, arch.NQ)).astype(float), axis=1)
    ps = np.array([f"2024-{i % 40}" for i in range(n)])
    return q, ps


def test_floors_are_project_floors_and_reb_is_documented() -> None:
    rule.check_floors()
    assert rule.FLOORS == {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
    assert rule.FLOORS == role_rule.FLOORS
    assert (
        "reb" not in t506.FLOORS
    )  # T505/T506 carry no reb floor: the reb value is the role_prior_salary_rule one


def test_constants_in_code() -> None:
    assert rule.CM_PASS == -1.0 and rule.HURT_MAX == 1.0 and rule.GATE_DE_BAR == 1.0
    assert rule.N_SEEDS == 20 and rule.TOP5_SHARE_MAX == 0.5 and rule.SHUFFLE_KEEP_MAX == 0.5
    assert rule.N_BOOT == role_rule.N_BOOT
    assert rule.SELECT == 2023 and rule.REPORT == 2024 and rule.MAX_SEASON == 2024
    with pytest.raises(ValueError):
        rule.check_season(2025)


def test_composite_hand_computed_and_parts_sum_to_cm() -> None:
    d = {
        "pts": np.array([-0.005, -0.005]),
        "reb": np.array([0.0, 0.0]),
        "ast": np.array([-0.002, 0.002]),
        "fg3m": np.array([0.001, 0.001]),
    }
    rows = rule.composite_rows(d)
    # row0: (-1 + 0 - 1 + 1) / 4 = -0.25 ; row1: (-1 + 0 + 1 + 1) / 4 = 0.25
    np.testing.assert_allclose(rows, [-0.25, 0.25])
    parts = rule.stat_parts(d)
    assert sum(parts.values()) == pytest.approx(float(rows.mean()))
    assert parts["pts"] == pytest.approx(-0.25)
    sh = rule.stat_shares({"pts": -1.0, "reb": 0.0, "ast": -1.0, "fg3m": 0.0})
    assert sh["pts"] == pytest.approx(0.5) and sh["fg3m"] == pytest.approx(0.0)
    assert rule.stat_shares({"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0})["pts"] is None


def test_composite_refuses_misaligned_or_missing_stats() -> None:
    with pytest.raises(AssertionError):
        rule.composite_rows({"pts": np.zeros(3), "reb": np.zeros(3), "ast": np.zeros(3)})
    with pytest.raises(AssertionError):
        rule.composite_rows(
            {"pts": np.zeros(3), "reb": np.zeros(2), "ast": np.zeros(3), "fg3m": np.zeros(3)}
        )


def _ok() -> dict[str, float]:
    return {"pts": -1.5, "reb": -0.5, "ast": 0.2, "fg3m": 0.0}


def test_verdict_passes_only_when_all_criteria_hold() -> None:
    v = rule.primary_verdict(-1.2, -1.1, -0.2, _ok(), 0.3, -0.2)
    assert v["pass"] and v["failed"] == []
    assert "cm_point" in rule.primary_verdict(-1.2, -0.9, -0.2, _ok(), 0.3, 0.0)["failed"]
    assert "ci_upper_below_zero" in rule.primary_verdict(-1.2, -1.1, 0.1, _ok(), 0.3, 0.0)["failed"]
    assert (
        "select_season_point" in rule.primary_verdict(-0.5, -1.1, -0.2, _ok(), 0.3, 0.0)["failed"]
    )
    hurt = {**_ok(), "fg3m": 1.01}
    assert "no_stat_hurt" in rule.primary_verdict(-1.2, -1.1, -0.2, hurt, 0.3, 0.0)["failed"]
    assert rule.primary_verdict(-1.2, -1.1, -0.2, {**_ok(), "fg3m": 1.0}, 0.3, 0.0)["pass"]
    assert "top5_share" in rule.primary_verdict(-1.2, -1.1, -0.2, _ok(), 0.51, 0.0)["failed"]
    # shuffled keeps exactly half of the gain: fails (strictly less than half is required)
    assert (
        "shuffled_keeps_under_half"
        in rule.primary_verdict(-1.2, -1.2, -0.2, _ok(), 0.3, -0.6)["failed"]
    )
    assert rule.primary_verdict(-1.2, -1.2, -0.2, _ok(), 0.3, -0.59)["pass"]


def test_noise_deltas_equal_reused_noise_arm() -> None:
    q, ps = _grid()
    game = np.arange(len(q)) // 3
    wk = np.arange(len(q)) % 20
    d = gate.noise_deltas(q, ps, 0.05, 7)
    ref = ev.noise_arm(q, ps, game, wk, 0.05, 7, arch.crps_int_q, n_boot=50)
    assert float(d.mean()) == pytest.approx(ref["game"]["point"], rel=0, abs=1e-12)
    assert float(np.std(d)) == pytest.approx(ref["sd_row_diff"], rel=0, abs=1e-12)


def test_sim_deltas_equal_reused_sim_cell() -> None:
    q, ps = _grid(seed=1)
    game = np.arange(len(q)) // 3
    wk = np.arange(len(q)) % 20
    for rec in (1.0, 0.5):
        d = gate.sim_deltas(q, ps, 0.2, rec, 3)
        ref = ev.sim_cell(q, ps, game, wk, 0.2, rec, 3, arch.crps_int_q, n_boot=50)
        assert float(d.mean()) == pytest.approx(ref["game"]["point"], rel=0, abs=1e-12)


def test_seeds_are_independent_streams_per_stat() -> None:
    seeds = {rule.noise_seed(s, k) for s in rule.STATS for k in range(rule.N_SEEDS)}
    assert len(seeds) == 4 * rule.N_SEEDS
    pos = {rule.pos_seed(s, k) for s in rule.STATS for k in range(rule.POS_SEEDS)}
    assert len(pos) == 4 * rule.POS_SEEDS and not (pos & seeds)


def test_joint_bootstrap_uses_one_set_of_clusters() -> None:
    """A stat whose delta is a pure cluster effect cancels against the other only if the same clusters are resampled in both."""
    n = 400
    game = np.arange(n) // 4
    ps = np.array([f"p{i % 20}" for i in range(n)])
    wk = np.arange(n) % 10
    rng = np.random.default_rng(0)
    zg = rng.standard_normal(n // 4)[game]
    d = {
        "pts": zg * rule.FLOORS["pts"],
        "reb": -zg * rule.FLOORS["reb"],
        "ast": np.zeros(n),
        "fg3m": np.zeros(n),
    }
    jc = gate.joint(d, game, ps, wk)
    assert (
        jc["game"]["half_width"] < 1e-9
    )  # perfectly offsetting on the same game clusters: no variance


def test_summarize() -> None:
    s = gate.summarize([1.0, 2.0, 3.0])
    assert (
        s["mean"] == 2.0 and s["sd"] == pytest.approx(1.0) and s["min"] == 1.0 and s["n_seeds"] == 3
    )


def test_counts_bars() -> None:
    ok = {"rows": 300, "player_seasons": 50, "games": 150, "weeks": 20}
    assert all(rule.gate_counts(ok).values())
    assert not rule.gate_counts({**ok, "weeks": 19})["weeks"]


def test_refuses_unfrozen(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        mev.require_frozen(None)
    doc = tmp_path / "d.md"
    doc.write_text("text\n")
    with pytest.raises(SystemExit):
        mev.require_frozen("abc", doc)
    doc.write_text("text\n" + mev.MARKER + "\n")
    with pytest.raises(SystemExit):
        mev.require_frozen("deadbeef", doc)
    with pytest.raises(SystemExit):
        mev.main(["run"])
    with pytest.raises(SystemExit):
        mev.main(["run", "--sha", "0" * 64])


def test_no_2025_and_no_label_columns_in_modules() -> None:
    for m in MODS:
        text = (ROOT / f"research/eval/archetype_matchup_all_{m}.py").read_text()
        assert "season = 2025" not in text and "season=2025" not in text
        for bad in ('"y"', '"minutes"', "'y'", "'minutes'"):
            assert bad not in text, (m, bad)
