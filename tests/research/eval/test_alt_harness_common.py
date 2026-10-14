"""Tests for the shared harness pieces: rule hashing, freeze commits, FROZEN status, the cluster
bootstrap, BH, odds arithmetic and the $2 x 10-a-week plan simulation."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import pytest

from research.eval import alt_harness_common as hc


def _rule(path: Path, status: str = "Status: **FROZEN Wed 13:47**") -> str:
    above = f"# T\n\n{status}\n- line\n"
    path.write_text(above + "\n=== RESULTS BELOW ===\n(empty)\n")
    return hashlib.sha256((f"# T\n\n{status}\n- line\n" + "\n").encode()).hexdigest()


def test_rule_sha_is_the_awk_hash_of_everything_above_the_mark(tmp_path: Path) -> None:
    p = tmp_path / "r.md"
    sha = _rule(p)
    assert hc.rule_sha256(p) == sha
    # text below the mark does not matter
    p.write_text(p.read_text() + "results appended\n")
    assert hc.rule_sha256(p) == sha
    # a change above the mark does
    p.write_text(p.read_text().replace("- line", "- line!"))
    assert hc.rule_sha256(p) != sha
    (tmp_path / "bad.md").write_text("no mark here\n")
    with pytest.raises(hc.PreregError):
        hc.rule_sha256(tmp_path / "bad.md")


def test_frozen_status_needs_the_exact_prefix(tmp_path: Path) -> None:
    p = tmp_path / "r.md"
    _rule(p, "Status: **FROZEN Wed Oct 14**")
    assert hc.frozen_status(p)
    _rule(p, "Status: **DRAFT Wed Oct 14. Not frozen.**")
    assert not hc.frozen_status(p)
    _rule(p, "Status: FROZEN (no bold)")
    assert not hc.frozen_status(p)


def test_git_prereg_shas_reads_commit_subjects(tmp_path: Path) -> None:
    sha = "a" * 64
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for msg in (f"prereg(ALT_X): {sha}", "prereg(ALT_Y): " + "b" * 64, "unrelated"):
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                msg,
            ],
            check=True,
        )
    assert hc.git_prereg_shas("ALT_X", tmp_path) == [sha]
    assert hc.git_prereg_shas("ALT_Z", tmp_path) == []
    assert hc.git_prereg_shas("ALT_X", tmp_path / "missing") == []


def test_check_rule_refuses_a_mismatch_a_missing_commit_and_a_draft(tmp_path: Path) -> None:
    p = tmp_path / "r.md"
    sha = _rule(p)
    pf = hc.Preflight()
    hc.check_rule(pf, p, "R", tmp_path, sha, lambda _i, _r: [sha])
    assert pf.ok
    pf2 = hc.Preflight()
    hc.check_rule(pf2, p, "R", tmp_path, "0" * 64, lambda _i, _r: [sha])
    assert not pf2.ok and "pinned" in pf2.failures()[0].name
    pf3 = hc.Preflight()
    hc.check_rule(pf3, p, "R", tmp_path, sha, lambda _i, _r: [])
    assert not pf3.ok
    _rule(p, "Status: **DRAFT**")
    pf4 = hc.Preflight()
    new = hc.rule_sha256(p)
    hc.check_rule(pf4, p, "R", tmp_path, None, lambda _i, _r: [new])
    assert not pf4.ok and "FROZEN" in pf4.failures()[0].name
    with pytest.raises(hc.PreregError):
        pf4.require()
    pf5 = hc.Preflight()
    assert hc.check_rule(pf5, tmp_path / "nope.md", "R", tmp_path) is None and not pf5.ok


def test_cluster_matrix_rows_are_resamples_and_seeded() -> None:
    m = hc.cluster_matrix(30, 50, np.random.default_rng(1))
    assert m.shape == (50, 30) and (m.sum(axis=1) == 30).all() and m.min() >= 0
    again = hc.cluster_matrix(30, 50, np.random.default_rng(1))
    assert np.array_equal(m, again)
    boots = hc.make_boot({2024: 10, 2023: 20}, 40, 5)
    assert list(boots) == [2023, 2024] and boots[2023].shape == (40, 20)
    assert np.array_equal(boots[2024], hc.make_boot({2023: 20, 2024: 10}, 40, 5)[2024])
    assert hc.cluster_matrix(0, 5, np.random.default_rng(1)).shape == (5, 0)


def test_boot_ratio_and_pooling() -> None:
    num = np.array([1.0, 2.0, 3.0, 4.0])
    den = np.array([2.0, 2.0, 2.0, 2.0])
    ident = np.tile(np.eye(4)[0] * 0 + 1.0, (3, 1))  # every resample = each cluster once
    point, boot = hc.boot_ratio([(ident, num, den)])
    assert abs(point - 10 / 8) < 1e-12 and np.allclose(boot, point)
    # two strata add numerators and denominators
    point2, boot2 = hc.boot_ratio([(ident, num, den), (ident, num, den)])
    assert abs(point2 - point) < 1e-12 and np.allclose(boot2, point)
    # an empty resample gives nan, not an exception
    z = np.zeros((2, 4))
    _, b3 = hc.boot_ratio([(z, num, den)])
    assert np.isnan(b3).all()


def test_p_values_use_the_plus_one_correction_and_decide_takes_the_union() -> None:
    boot = np.array([0.1] * 99)
    assert hc.p_le0(boot) == pytest.approx(1 / 100)
    assert hc.p_two_sided(boot) == pytest.approx(2 / 100)
    sym = np.array([-0.1, 0.1] * 50)
    assert hc.p_two_sided(sym) == 1.0
    g = np.linspace(0.0, 1.0, 1001)
    d = np.linspace(0.2, 1.4, 1001)
    r = hc.decide(0.5, g, d)
    lo_g, hi_g = hc.percentile_interval(g)
    lo_d, hi_d = hc.percentile_interval(d)
    assert r["lo"] == min(lo_g, lo_d) and r["hi"] == max(hi_g, hi_d)
    assert r["p_two_sided"] == max(hc.p_two_sided(g), hc.p_two_sided(d))
    assert hc.decide(0.5, g, None)["lo"] == lo_g


def test_bh_q_matches_the_textbook_example() -> None:
    p = [0.01, 0.04, 0.03, 0.005]
    q = hc.bh_q(p)
    # sorted p .005 .01 .03 .04 -> scaled .02 .02 .04 .04
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert hc.bh_q([]) == []
    assert hc.bh_q([0.9, 0.95]) == pytest.approx([0.95, 0.95])


def test_odds_arithmetic() -> None:
    assert hc.american_from_decimal(2.5) == pytest.approx(150.0)
    assert hc.american_from_decimal(1.5) == pytest.approx(-200.0)
    assert np.isnan(hc.american_from_decimal(1.0))
    be = hc.break_even(0.75, 0.6)
    assert be["decimal_at_claim"] == pytest.approx(1 / 0.75)
    assert be["decimal_at_realised"] == pytest.approx(1 / 0.6)
    assert be["american_at_claim"] == pytest.approx(-300.0)


def test_plan_never_bets_more_than_ten_units_or_twenty_dollars_a_week() -> None:
    # week 1: 25 units with 2 rungs each; week 2: 4 units with 1 rung
    w1 = (np.tile(np.array([[0.5, -1.0]]), (25, 1)), np.full(25, 2))
    w2 = (np.array([[0.2], [0.2], [0.2], [-1.0]]), np.ones(4, dtype=int))
    sims = hc.simulate_plan({2023: [w1, w2]}, r=300, seed=3)
    st = sims[2023]["staked"]
    assert st.shape == (300, 2)
    assert (st[:, 0] == 20.0).all() and (st[:, 1] == 8.0).all()
    pr = sims[2023]["profit"]
    # week 2: all four units are bet, one rung each: 3 x 0.2 - 1 = -0.4 stake units = -$0.8
    assert np.allclose(pr[:, 1], -0.8)
    # week 1: ten bets of $2, each +0.5 or -1 stake units
    assert pr[:, 0].min() >= -20.0 and pr[:, 0].max() <= 10.0
    again = hc.simulate_plan({2023: [w1, w2]}, r=300, seed=3)
    assert np.array_equal(again[2023]["profit"], pr)
    other = hc.simulate_plan({2023: [w1, w2]}, r=300, seed=4)
    assert not np.array_equal(other[2023]["profit"], pr)


def test_build_weeks_groups_units_and_pads_rungs() -> None:
    weeks = hc.build_weeks(
        np.array([1.0, 2.0, 3.0, 4.0]), ["w2", "w1", "w1", "w1"], ["a", "b", "b", "c"]
    )
    assert len(weeks) == 2
    mat, ks = weeks[0]  # week w1: unit b has two rungs, unit c one
    assert sorted(ks.tolist()) == [1, 2] and mat.shape == (2, 2)


def test_plan_summary_values() -> None:
    profit = np.array([[2.0, -4.0, -4.0, 8.0], [-2.0, -2.0, 2.0, 2.0]])
    s = hc.plan_summary(profit, np.full_like(profit, 20.0))
    assert s["weeks"] == 4
    assert s["p_final_positive"] == 0.5  # finals are +2 and 0
    assert s["longest_losing_run_median_p95"][0] == 2.0
    assert hc.plan_summary(np.zeros((3, 0)), np.zeros((3, 0))) == {"weeks": 0}
