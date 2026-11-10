"""BEST_PRICE_FORWARD scorer: refusals, settlement, decision CI, words, guards, synthetic worlds.

No real outcome is read: every world here is synthetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from nba.parlay.config import FeeModel
from research.eval import best_price_forward_eval as ev
from research.eval import best_price_forward_gate as g


def pk(gid: str, d: date, pid: int, side: str = "over", stat: str = "pts", **kw: object) -> g.Pick:
    venues = kw.pop("venues", {"draftkings": 1.91, "fanduel": 1.95})
    other = kw.pop("other", {"draftkings": 1.91, "fanduel": 1.87})
    p = g.Pick(gid, d, stat, pid, 24.5, side, 0.5, 0.56, 0.5, venues)  # type: ignore[arg-type]
    return replace(p, venues_other=other, **kw)  # type: ignore[arg-type]


def world(
    n_games: int = 120, per_game: int = 6, n_players: int = 40, p_true: float = 0.5, seed: int = 1
) -> list[ev.Scored]:
    """Synthetic season: picks over at 1.95 (best), a known hit rate, books only (no voids)."""
    rng = np.random.default_rng(seed)
    rows = []
    for gi in range(n_games):
        d = date(2026, 11, 12) + timedelta(days=gi // 4)
        for j in range(per_game):
            pid = int((gi * per_game + j) % n_players)
            x = pk(f"G{gi:04d}", d, pid, stat=g.STATS[j % 4])
            rows.append(ev.Scored(x, ev.Settle(bool(rng.random() < p_true), None)))
    return sorted(
        rows, key=lambda r: (r.pick.game_date, r.pick.game_id, r.pick.player_id, r.pick.stat)
    )


# ---------------------------------------------------------------- refusals


def test_window_start_from_committer_time_in_et() -> None:
    # 23:30 CST on 11-10 is 00:30 ET on 11-11: the window opens 11-12
    assert ev.window_start_from_iso("2026-11-10T23:30:00-06:00") == date(2026, 11, 12)
    assert ev.window_start_from_iso("2026-11-10T12:00:00-06:00") == date(2026, 11, 11)
    with pytest.raises(ev.Refused):
        ev.window_start_from_iso("2026-11-10T12:00:00")


def test_check_window_refuses_2025_prewindow_and_late_rows() -> None:
    start, last = date(2026, 11, 12), date(2027, 1, 13)
    ev.check_window([("G1", 2026, date(2026, 11, 12))], start, last)
    with pytest.raises(ev.Refused, match="season 2025"):
        ev.check_window([("G0", 2025, date(2026, 11, 20))], start, last)
    with pytest.raises(ev.Refused, match="precedes the window"):
        ev.check_window([("G2", 2026, date(2026, 11, 11))], start, last)
    with pytest.raises(ev.Refused, match="after the read"):
        ev.check_window([("G3", 2026, date(2027, 1, 14))], start, last)
    with pytest.raises(ev.Refused, match="floor"):
        ev.check_window([], date(2026, 11, 1), last)


def test_fee_pin() -> None:
    ok = FeeModel("k", "p_one_minus_p", 0.07, verified=True)
    ev.verify_fee(ok)
    with pytest.raises(ev.Refused):
        ev.verify_fee(replace(ok, verified=False))
    with pytest.raises(ev.Refused):
        ev.verify_fee(replace(ok, coefficient=0.0175))
    with pytest.raises(ev.Refused):
        ev.verify_fee(replace(ok, multipliers=(("KXNBAPTS", 0.5),)))


def test_rule_hash_and_status() -> None:
    text = "Status: **FROZEN** x\nbody\n=== RESULTS BELOW ===\nanything\n"
    sha = ev.rule_text_sha(text)
    ev.verify_rule(text, sha)
    assert ev.rule_text_sha(text + "more results\n") == sha  # results below never change the hash
    with pytest.raises(ev.Refused):
        ev.verify_rule(text, "0" * 64)
    draft = text.replace("FROZEN", "DRAFT")
    with pytest.raises(ev.Refused):
        ev.verify_rule(draft, ev.rule_text_sha(draft))


def test_committed_rule_text_hash_is_the_reviewed_one() -> None:
    text = (Path(__file__).resolve().parents[3] / "docs/prereg/BEST_PRICE_FORWARD.md").read_text()
    assert (
        ev.rule_text_sha(text) == "84a887bd2399778ab5124c7f285e708f6bb4f0b7793d5d29979c56a734331a9b"
    )


def test_score_refuses_before_read_date() -> None:
    with pytest.raises(ev.Refused, match="Read 1 is on"):
        ev.score(Path("/nonexistent"), 1, "deadbeef", "0" * 64, today=date(2027, 1, 14))


# ---------------------------------------------------------------- settlement


def test_grade_and_kalshi_payoff() -> None:
    assert ev.grade_book(30.0, 25, 24.5) is True
    assert ev.grade_book(30.0, 24, 24.5) is False
    assert ev.grade_book(0.0, 0, 24.5) is None
    assert ev.grade_book(None, None, 24.5) is None
    assert ev.kalshi_yes_payoff("yes", None) == 1.0
    assert ev.kalshi_yes_payoff("no", None) == 0.0
    assert ev.kalshi_yes_payoff("scalar", 0.37) == 0.37
    assert ev.kalshi_yes_payoff("scalar", None) is None
    assert ev.kalshi_yes_payoff(None, None) is None


def test_profit_for_books_and_kalshi() -> None:
    assert ev.profit_for("over", "draftkings", 1.91, ev.Settle(True, None)) == pytest.approx(
        (0.91, 1.0)
    )
    assert ev.profit_for("under", "draftkings", 1.91, ev.Settle(True, None)) == (-1.0, 1.0)
    assert ev.profit_for("over", "draftkings", 1.91, ev.Settle(None, None)) == (0.0, 0.0)  # void
    cost = 0.42 + g.kalshi_fee(0.42)
    d = 1 / cost
    assert ev.profit_for("over", "kalshi", d, ev.Settle(None, 1.0))[0] == pytest.approx(  # type: ignore[index]
        (1 - cost) / cost
    )
    # DNP on Kalshi: scalar fair value counts, stake kept
    prof = ev.profit_for("under", "kalshi", d, ev.Settle(None, 0.4))
    assert prof is not None and prof[1] == 1.0 and prof[0] == pytest.approx(0.6 * d - 1)
    assert ev.profit_for("over", "kalshi", d, ev.Settle(None, None)) is None


# ---------------------------------------------------------------- decision CI


def test_decision_ci_reproduces_and_consumes_game_date_player() -> None:
    rows = world()
    arm = ev.build_arm(rows, ev.best_price)
    a = ev.arm_ci(arm, 0.02, b=400)
    b = ev.arm_ci(arm, 0.02, b=400)
    assert a == b
    # manual: one generator, game then date then player
    rng = np.random.default_rng(2026)
    keys = (
        [x.pick.game_id for x in arm.rows],
        [x.pick.game_date for x in arm.rows],
        [x.pick.player_id for x in arm.rows],
    )
    for name, k in zip(("game", "date", "player"), keys, strict=True):
        dr = ev.boot_ratio_draws([arm.profit], arm.stake, ev._codes(k), rng, 400)[0]
        assert a["by_clustering"][name]["lower"] == pytest.approx(float(np.quantile(dr, 0.02)))
    widths = {k: v["upper"] - v["lower"] for k, v in a["by_clustering"].items()}
    assert a["decision"]["clustering"] == max(widths, key=lambda k: widths[k])


def test_paired_difference_uses_shared_draws() -> None:
    rows = world()
    best = ev.build_arm(rows, ev.best_price)
    dk = ev.build_arm(rows, ev.venue_price("draftkings"))
    ci = ev.arm_ci(best, 0.02, b=400, paired=dk.profit)
    assert ci["roi"] == pytest.approx(best.roi - dk.roi)  # type: ignore[operator]
    # best >= DK on every winning pick and equal on losers: the difference is never negative
    assert ci["decision"]["lower"] >= 0


def test_planted_edge_world_is_seen_and_null_world_is_not() -> None:
    edge = ev.build_arm(world(p_true=0.62), ev.best_price)
    ci = ev.arm_ci(edge, 0.02, b=600)
    assert ci["roi"] > 0.1 and ci["decision"]["lower"] > 0
    null = ev.build_arm(world(p_true=1 / 1.95), ev.best_price)
    cn = ev.arm_ci(null, 0.02, b=600)
    assert cn["decision"]["lower"] < 0 < cn["decision"]["upper"]


def test_self_tests_on_a_synthetic_world() -> None:
    picks = [replace(r.pick, p_cons=0.5, p_cal=0.6) for r in world(n_games=80)]
    res = ev.self_tests(picks, read=2, k=40, b=300, progress=False)
    assert set(res) == {"a_null", "b_planted", "ok"}
    assert res["a_null"]["lower_above_truth"] <= 0.1
    assert abs(res["b_planted"]["mean_error"]) <= 0.01
    assert res["b_planted"]["coverage"] >= 0.85


# ---------------------------------------------------------------- words


def test_word_order() -> None:
    w = ev.evaluate_word
    assert w(1, 0.95, 400, 50, 6000, True, 0.05, 0.01, 0.09, True)[0] == "PASS"
    assert w(1, 0.95, 400, 50, 6000, True, 0.05, 0.01, 0.09, False)[0] == "KEEP_SHADOWING"
    assert w(2, 0.95, 800, 100, 9000, True, 0.05, 0.01, 0.09, False)[0] == "INCONCLUSIVE"
    assert w(1, 0.95, 400, 50, 6000, True, -0.04, -0.07, -0.01, True)[0] == "NO_POSITIVE_EV_FOUND"
    assert w(1, 0.95, 200, 50, 6000, True, 0.05, 0.01, 0.09, True)[0] == "INSUFFICIENT_SAMPLE"
    assert w(1, 0.60, 400, 50, 6000, True, 0.05, 0.01, 0.09, True)[0] == "INSUFFICIENT_SAMPLE"
    assert w(2, 0.95, 400, 50, 6000, True, 0.05, 0.01, 0.09, True)[0] == "INCONCLUSIVE"
    # self-test failure outranks a pass, and twice at Read 2 is INCONCLUSIVE
    assert w(1, 0.95, 400, 50, 6000, False, 0.05, 0.01, 0.09, True)[0] == "SELFTEST_FAILED"
    assert w(2, 0.95, 800, 100, 9000, False, 0.05, 0.01, 0.09, True)[0] == "SELFTEST_FAILED"
    assert (
        w(2, 0.95, 800, 100, 9000, False, 0.05, 0.01, 0.09, True, rerun_used=True)[0]
        == "INCONCLUSIVE"
    )
    assert w(1, 0.95, 400, 50, 6000, True, 0.005, -0.02, 0.03, True)[0] == "KEEP_SHADOWING"


# ---------------------------------------------------------------- guards


def test_shuffle_guard_edge_passes_null_fails() -> None:
    edge = world(p_true=0.65)
    assert ev.shuffle_guard(edge, ev.build_arm(edge, ev.best_price).roi or 0, n=200)["ok"]
    null = world(p_true=0.5, seed=3)
    assert not ev.shuffle_guard(null, ev.build_arm(null, ev.best_price).roi or 0, n=200)["ok"]


def test_shuffle_guard_reproduces() -> None:
    rows = world()
    r = ev.build_arm(rows, ev.best_price).roi or 0
    assert ev.shuffle_guard(rows, r, n=100) == ev.shuffle_guard(rows, r, n=100)


def test_second_best_and_venue_loo() -> None:
    x = ev.Scored(pk("G1", date(2026, 11, 12), 1), ev.Settle(True, None))
    assert ev.best_price(x) == ("over", "fanduel", 1.95)
    assert ev.second_price(x) == ("over", "draftkings", 1.91)
    assert ev.without_venue("fanduel")(x) is None  # one venue left: rule 5.6 drops it
    y = ev.Scored(
        pk("G1", date(2026, 11, 12), 1, venues={"a": 1.9, "b": 1.95, "kalshi": 1.95}),
        ev.Settle(True, None),
    )
    assert ev.best_price(y)[1] == "b"  # type: ignore[index]
    assert ev.without_venue("b")(y) == ("over", "kalshi", 1.95)


def test_guards_on_an_edge_world() -> None:
    two = world(p_true=0.65)
    # with only two venues per pick, leaving one out drops every pick (rule 5.6): the guard fails
    assert not ev.guards(two, {i: True for i in range(len(two))})["ok"]
    three = {"draftkings": 1.91, "fanduel": 1.95, "betmgm": 1.93}
    rows = [ev.Scored(replace(r.pick, venues=dict(three)), r.settle) for r in two]
    out = ev.guards(rows, {i: True for i in range(len(rows))})
    assert out["ok"], out
    assert out["executable_fail_share"] == 0
    none_exec = ev.guards(rows, {})
    assert not none_exec["ok"]  # nothing executable: ROI undefined, guard fails


def test_planted_outcome_and_settlement_agreement() -> None:
    rows = world(p_true=0.5)
    assert (ev.planted_outcome_roi(rows) or 0) >= 0.30
    k_rows = [ev.Scored(r.pick, ev.Settle(r.settle.book_over, 1.0)) for r in rows]
    sa = ev.settlement_agreement(k_rows)
    assert sa["n"] == len(rows) and not sa["ok"]  # half disagree
    agree = [
        ev.Scored(r.pick, ev.Settle(r.settle.book_over, 1.0 if r.settle.book_over else 0.0))
        for r in rows
    ]
    assert ev.settlement_agreement(agree)["ok"]


def test_maker_fill_rule() -> None:
    later = [(0.40, 0.44), (0.39, 0.42)]
    assert not ev.maker_filled("over", 0.42, later, at_equality=False)  # ask 42 is not below 41
    assert ev.maker_filled("over", 0.42, later, at_equality=True)
    assert ev.maker_filled("over", 0.43, later, at_equality=False)
    # under: side ask = 100 - bid; bids 40, 39 -> asks 60, 61
    assert ev.maker_filled("under", 0.61, later, at_equality=False)
    assert not ev.maker_filled("under", 0.60, later, at_equality=False)
