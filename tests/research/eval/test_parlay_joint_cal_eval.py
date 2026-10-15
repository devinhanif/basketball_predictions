# ruff: noqa: E501
"""PARLAY_JOINT_CAL harness tests: the vectorised pair correlations equal estimate_pair_corr, the per-ticket
correlation equals build_corr_matrix, the orthant price handles the side sign, planted futures change nothing,
guards raise (not assert), words obey the rule, and the freeze refusals fire."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from nba.parlay.copula import ResidualRow, build_corr_matrix, estimate_pair_corr
from nba.parlay.legs import Leg
from research.eval import parlay_joint_cal_eval as ev
from research.eval import parlay_joint_cal_rule as rule

STATS = ("ast", "fg3m", "pts", "reb")


def _rows(n_games: int = 30, seed: int = 0, start: str = "2024-01-01") -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    out = []
    d0 = np.datetime64(start)
    for g in range(n_games):
        date = d0 + np.timedelta64(g // 3, "D")
        teams = (1, 2)
        for tm in teams:
            for p in range(5):
                pid = 100 * tm + p
                fac = rng.standard_normal()
                for st in STATS:
                    z = 0.5 * fac + rng.standard_normal()
                    out.append((date, f"g{g:03d}", pid, tm, st, z))
    out.sort(key=lambda r: (r[0], r[1], r[2], r[4]))
    arr = list(zip(*out, strict=True))
    return (
        np.array(arr[0], dtype="datetime64[D]"), np.array(arr[1]), np.array(arr[2]), np.array(arr[3]),
        np.array(arr[4]), np.array(arr[5]),
    )  # fmt: skip


def test_gamestats_equals_estimate_pair_corr() -> None:
    date, gid, pid, tid, st, z = _rows()
    gs = ev.GameStats(date, gid, pid, tid, st, z)
    block = np.datetime64("2024-01-08")
    pc = gs.paircorr(block)
    sel = date < block
    rows = [
        ResidualRow(str(gid[i]), int(pid[i]), int(tid[i]), str(st[i]), float(z[i]))
        for i in np.flatnonzero(sel)
    ]
    ref = estimate_pair_corr(rows, ev.SHRINK_K)
    assert set(ref.rho) == set(pc.rho)
    for k, v in ref.rho.items():
        assert abs(v - pc.rho[k]) < 1e-9


def test_planted_future_row_leaves_block_unchanged() -> None:
    date, gid, pid, tid, st, z = _rows()
    block = np.datetime64("2024-01-08")
    base = ev.GameStats(date, gid, pid, tid, st, z).paircorr(block)
    # one extra game dated inside the block, |z| = 8 everywhere
    pd = date.copy()
    inside = np.datetime64("2024-01-09")
    extra = 40
    date2 = np.concatenate([date, np.full(extra, inside)])
    gid2 = np.concatenate([gid, np.full(extra, "gZZZ")])
    pid2 = np.concatenate([pid, np.arange(extra)])
    tid2 = np.concatenate([tid, np.tile([1, 2], extra // 2)])
    st2 = np.concatenate([st, np.tile(np.array(STATS), extra // 4)])
    z2 = np.concatenate([z, np.full(extra, 8.0)])
    assert len(pd) == len(date)
    got = ev.GameStats(date2, gid2, pid2, tid2, st2, z2).paircorr(block)
    assert got.rho == base.rho


def test_fit_guard_raises_not_asserts() -> None:
    block = np.datetime64("2024-01-08")
    with pytest.raises(RuntimeError):
        ev.guard_fit_before(np.array(["2024-01-08"], dtype="datetime64[D]"), block, "J fit tickets")
    ev.guard_fit_before(np.array(["2024-01-07"], dtype="datetime64[D]"), block, "ok")
    with pytest.raises(RuntimeError):
        ev.guard_season([2023, 2025])
    with pytest.raises(RuntimeError):
        ev.guard_lead(59.9)
    ev.guard_lead(60.0)


def test_ticket_R_equals_build_corr_matrix() -> None:
    date, gid, pid, tid, st, z = _rows()
    gs = ev.GameStats(date, gid, pid, tid, st, z)
    pc = gs.paircorr(np.datetime64("2024-01-08"))
    legs = [
        Leg("g1", 100, "pts", 10.5, "yes", 1),
        Leg("g1", 100, "ast", 4.5, "no", 1),
        Leg("g1", 201, "reb", 5.5, "yes", 2),
    ]
    ref = build_corr_matrix(legs, pc)
    got = ev.ticket_R(
        [leg.stat for leg in legs], [leg.player_id for leg in legs], [1, 1, 2], ["g1"] * 3, pc
    )
    assert np.allclose(ref, got, atol=1e-12)
    cross = ev.ticket_R(["pts", "reb"], [1, 2], [1, 2], ["g1", "g2"], pc)
    assert np.array_equal(cross, np.eye(2))


def test_orthant_sides_and_product() -> None:
    assert abs(ev.orthant(np.array([0.37]), np.array([False]), np.eye(1)) - 0.37) < 1e-12
    q = np.array([0.6, 0.55])
    assert abs(ev.orthant(q, np.array([True, False]), np.eye(2)) - 0.33) < 1e-12
    rho = 0.5
    R = np.array([[1.0, rho], [rho, 1.0]])
    rng = np.random.default_rng(1)
    zz = rng.multivariate_normal([0, 0], R, size=400_000)
    from scipy import stats

    over_hit = zz[:, 0] > stats.norm.ppf(1 - 0.6)  # leg 1: over, q = 0.6
    under_hit = zz[:, 1] < stats.norm.ppf(0.55)  # leg 2: under, q = 0.55
    mc = float(np.mean(over_hit & under_hit))
    got = ev.orthant(q, np.array([True, False]), R)
    assert abs(got - mc) < 0.004
    assert got < 0.33  # over/under with positive latent correlation hit together less often
    both_over = ev.orthant(q, np.array([True, True]), R)
    assert both_over > 0.33


def test_cdf_pair_reads_survival_vector() -> None:
    s = [[0.8, 0.5, 0.2]]  # P(Y>=1)=.8, P(Y>=2)=.5, P(Y>=3)=.2, P(Y>=4)=0
    lo, hi = ev.cdf_pair(s * 5, np.array([0, 1, 2, 3, 5]))
    assert np.allclose(hi, [0.2, 0.5, 0.8, 1.0, 1.0])
    assert np.allclose(lo, [0.0, 0.2, 0.5, 0.8, 1.0])


def test_words_follow_the_rule() -> None:
    g = {"point": 0.004, "lo": -0.012, "hi": 0.02}
    assert ev.word_t1(g, 0.01) == "CALIBRATED"
    assert ev.word_t1({"point": 0.004, "lo": -0.012, "hi": 0.04}, 0.01) == "INCONCLUSIVE"
    assert ev.word_t1(g, 0.05) == "INCONCLUSIVE"  # 2023 point outside 0.03
    over = {"point": -0.05, "lo": -0.08, "hi": -0.02}
    assert ev.word_t1(over, -0.01) == "OVERCONFIDENT?"
    assert ev.word_t1(over, 0.01) == "INCONCLUSIVE"
    assert ev.finalize("OVERCONFIDENT?", 0.2, ev.EXPOSED).startswith("INCONCLUSIVE")
    assert ev.finalize("OVERCONFIDENT?", 0.01, ev.EXPOSED).startswith("OVERCONFIDENT")
    assert ev.finalize("CALIBRATED", 0.9, ev.EXPOSED).endswith("(2024, exposed: LC select season)")
    d = {"point": -0.01, "lo": -0.02, "hi": -0.002}
    assert ev.word_t2(d, -0.005) == "CAL HELPS THE JOINT?"
    assert ev.word_t2({"point": -0.001, "lo": -0.002, "hi": 0.0}, -0.005) == "CAL DOES NOT HELP"
    top = {"point": -0.06, "lo": -0.1, "hi": -0.01}
    assert ev.word_t3(top, -0.02, -0.08) == "TOP OVERCONFIDENT?"
    assert ev.word_t3(top, 0.02, -0.08) == "INCONCLUSIVE"


def test_freeze_refusals(tmp_path: Path) -> None:
    doc = tmp_path / "r.md"
    doc.write_text("# r\nrule text\n=== RESULTS BELOW ===\nresults\n")
    sha = rule.frozen_sha(doc)
    assert sha == rule.text_sha("# r\nrule text\n=== RESULTS BELOW ===\nanything\n")
    with pytest.raises(RuntimeError):
        rule.require_frozen(None, doc)
    with pytest.raises(RuntimeError):
        rule.require_frozen("0" * 64, doc, run=lambda *a: "")
    with pytest.raises(RuntimeError):  # right text hash but no freeze commit
        rule.require_frozen(sha, doc, run=lambda *a: "")
    with pytest.raises(RuntimeError):
        rule.require_harness_pinned(sha, run=lambda *a: "")
    assert dt.date(2024, 1, 1)
