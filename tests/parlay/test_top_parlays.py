"""top_parlays/1: ranking, pruning, empty slate, refusal after tip, numguard, verdict."""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nba.parlay.assistant.numguard import check_prose, collect_numbers
from nba.parlay.config import load_config
from nba.parlay.copula import PairCorr
from nba.parlay.top_parlays import (
    KEEP,
    T490_CAVEAT,
    T495_CAVEAT,
    Gate,
    LegCand,
    build_report,
    dedupe,
    implied_prob,
    make_leg,
    prune,
    refuse_post_tip,
)

CFG = load_config()
PC = PairCorr(rho={}, n={})
TIP = datetime(2026, 10, 28, 23, 0)
PRICED = TIP - timedelta(minutes=60)
CAL = {"rule": "LINE_CALIBRATION", "sha8": "a5441242", "fit_through": "2025-06-30"}
HYPE = re.compile(r"\b(lock|guarantee\w*|sure|easy|safe|hammer|smash|best bet|free money)\b", re.I)


def leg(
    i: int,
    p: float,
    *,
    game: str = "G1",
    stat: str = "pts",
    side: str = "over",
    price: int = -110,
    book: str = "draftkings",
    tip: datetime = TIP,
    priced: datetime = PRICED,
    pid: int | None = None,
    team: int = 1,
) -> LegCand:
    return make_leg(
        p_over_raw=p,
        p_over_cal=p,
        side=side,
        game_id=game,
        tipoff=tip,
        player_id=pid if pid is not None else i,
        player_name=f"Player Name{i}",
        team_id=team,
        opp_id=2,
        team="BOS",
        opp="NYK",
        stat=stat,
        line=20.5,
        book=book,
        price_american=price,
        price_at=priced,
        model_source="production",
        drivers={"mean": 22.4, "recent_avg": 21.1, "minutes": 33.5, "minutes_kind": "projected"},
    )


def report(legs: list[LegCand], **kw: object) -> dict:
    args: dict = {
        "slate_date": "2026-10-28",
        "made_at": PRICED,
        "now": PRICED,
        "cfg": CFG,
        "pc": PC,
        "calibration": CAL,
        "gate": Gate(),
    }
    args.update(kw)
    return build_report(legs, **args)


def cross_game_legs(n: int = 8) -> list[LegCand]:
    # n legs, each its own game, descending probability
    return [leg(i, 0.90 - 0.02 * i, game=f"G{i}", pid=100 + i) for i in range(n)]


def test_ranking_cross_game_is_product_of_top_legs_and_sorted() -> None:
    legs = cross_game_legs(12)
    rep = report(legs)
    two, three = rep["sizes"]["2"], rep["sizes"]["3"]
    assert [t["rank"] for t in two] == [1, 2, 3] and [t["rank"] for t in three] == [1, 2, 3]
    assert two[0]["p_joint_cal"] == pytest.approx(0.90 * 0.88, abs=1e-4)
    assert three[0]["p_joint_cal"] == pytest.approx(0.90 * 0.88 * 0.86, abs=1e-4)
    for ts in (two, three):
        ps = [t["p_joint_cal"] for t in ts]
        assert ps == sorted(ps, reverse=True)
    assert {leg_["player_id"] for leg_ in two[0]["legs"]} == {100, 101}
    assert not two[0]["same_game"]


def test_tie_broken_by_higher_ev() -> None:
    # two identical probabilities; the longer price (higher EV) must rank first
    legs = [
        leg(1, 0.80, game="G1", pid=1, price=-110),
        leg(2, 0.80, game="G2", pid=2, price=-110),
        leg(3, 0.80, game="G3", pid=3, price=-300),
    ]
    rep = report(legs)
    top = rep["sizes"]["2"][0]
    assert {x["player_id"] for x in top["legs"]} == {1, 2}


def test_pruning_states_k_and_bounds_the_search() -> None:
    legs = cross_game_legs(12)
    rep = report(legs, k=5)
    s = rep["diagnostics"]["search"]
    assert s["k_per_book"] == 5 and s["legs_searched"] == {"draftkings": 5}
    assert s["tickets_searched"] == 10 + 10  # C(5,2) + C(5,3)
    keep = {100 + i for i in range(5)}
    for ts in rep["sizes"].values():
        for t in ts:
            assert {x["player_id"] for x in t["legs"]} <= keep


def test_prune_is_per_book_and_by_p_cal() -> None:
    legs = [leg(i, 0.5 + 0.01 * i, game=f"G{i}", book="draftkings") for i in range(6)]
    legs += [leg(10 + i, 0.6 + 0.01 * i, game=f"H{i}", book="pinnacle") for i in range(6)]
    by_book = prune(legs, 3)
    assert {b: len(v) for b, v in by_book.items()} == {"draftkings": 3, "pinnacle": 3}
    assert by_book["draftkings"][0].p_cal == pytest.approx(0.55)


def test_dedupe_one_leg_per_player_stat_keeps_higher_probability() -> None:
    a = leg(1, 0.30, side="over")  # under hits with 0.70
    b = leg(1, 0.30, side="under")
    c = leg(1, 0.30, side="over", book="pinnacle")
    out = dedupe([a, b, c])
    by_book = {x.book: x for x in out}
    assert by_book["draftkings"].side == "under" and by_book["pinnacle"].side == "over"


def test_same_game_same_player_uses_copula_and_cross_game_is_independent() -> None:
    pc = PairCorr(rho={("same_player", "ast", "pts"): 0.6}, n={})
    a = leg(1, 0.6, stat="pts", pid=7, game="G1")
    b = leg(2, 0.6, stat="ast", pid=7, game="G1")
    c = leg(3, 0.6, stat="pts", pid=8, game="G2")
    d = leg(4, 0.6, stat="pts", pid=9, game="G3")
    rep = report([a, b, c, d], pc=pc)
    same = [t for t in rep["sizes"]["2"] if t["same_game"]]
    cross = [t for t in rep["sizes"]["2"] if not t["same_game"]]
    assert same and cross
    assert same[0]["p_joint_cal"] > 0.6 * 0.6 + 0.03  # positive dependence lifts the joint
    assert cross[0]["p_joint_cal"] == pytest.approx(0.36, abs=1e-4)
    # an under leg flips the sign: a positively correlated pair, one over and one under
    e = leg(2, 0.6, stat="ast", pid=7, game="G1", side="under")  # hits with 0.4
    rep2 = report([a, e], pc=pc)
    assert rep2["sizes"]["2"][0]["p_joint_cal"] < 0.6 * 0.4 - 0.03


def test_same_game_means_any_two_legs_share_a_game_and_is_not_a_book_price() -> None:
    """M3: a mixed ticket (two legs in one game, one elsewhere) is same_game, carries an EV that is
    not a book price, and can never open the verdict."""
    pc = PairCorr(rho={("same_player", "fg3m", "pts"): 0.6}, n={})
    a = leg(1, 0.7, stat="pts", pid=7, game="G1", price=+400)
    b = leg(2, 0.7, stat="fg3m", pid=7, game="G1", price=+400)
    c = leg(3, 0.9, game="G2", pid=8, price=+400)
    rep = report([a, b, c], pc=pc, gate=Gate(40, 20, 0.05))
    t3 = rep["sizes"]["3"][0]
    assert t3["same_game"] is True and "not a book price" in t3["ev_basis"]
    assert "not a book price" in t3["why"]
    only = report([a, b], pc=pc, gate=Gate(40, 20, 0.05))  # a same-game ticket alone
    top = only["sizes"]["2"][0]
    assert top["same_game"] and top["ev_low80"] > 0  # positive on paper, but not a book price
    assert only["verdict"] == KEEP and only["diagnostics"]["no_positive_ev_found"]


def test_each_leg_appears_in_at_most_one_shown_ticket_per_size() -> None:
    """M5 (default pending Devin): greedy by rank; the rank-1 ticket is the most probable one."""
    legs = cross_game_legs(12)
    rep = report(legs)
    for size in ("2", "3"):
        keys = [x["player_id"] for t in rep["sizes"][size] for x in t["legs"]]
        assert len(keys) == len(set(keys)) == int(size) * 3
    assert rep["sizes"]["2"][0]["p_joint_cal"] == pytest.approx(0.90 * 0.88, abs=1e-4)
    assert "at most one shown ticket" in rep["ranking"]


def test_empty_slate_and_no_legs_are_first_class() -> None:
    rep = report([], no_slate=True)
    assert rep["sizes"] == {"2": [], "3": []}
    assert rep["empty_reason"] == "no slate" and rep["verdict"] == KEEP
    rep = report([])
    assert rep["empty_reason"] == "no priced legs" and rep["verdict"] == KEEP
    json.dumps(rep)  # serialisable


def test_fewer_than_three_legs_leaves_size_three_empty_with_a_reason() -> None:
    rep = report(cross_game_legs(2))
    assert len(rep["sizes"]["2"]) == 1 and rep["sizes"]["3"] == []
    assert "size 3" in rep["empty_reason"]


def test_legs_priced_at_or_after_tip_are_refused() -> None:
    late = leg(1, 0.9, game="G1", priced=TIP)  # priced exactly at tip
    later = leg(2, 0.9, game="G2", priced=TIP + timedelta(minutes=5))
    ok = [leg(3, 0.7, game="G3"), leg(4, 0.7, game="G4")]
    kept, counts = refuse_post_tip([late, later, *ok], None)
    assert [x.player_id for x in kept] == [3, 4] and counts["priced_at_or_after_tip"] == 2
    rep = report([late, later, *ok])
    ids = {x["player_id"] for ts in rep["sizes"].values() for t in ts for x in t["legs"]}
    assert ids == {3, 4}
    assert rep["diagnostics"]["refused"]["priced_at_or_after_tip"] == 2


def test_game_already_tipped_at_the_run_clock_is_refused() -> None:
    legs = [leg(1, 0.9, game="G1"), leg(2, 0.8, game="G2"), leg(3, 0.7, game="G3")]
    rep = report(legs, now=TIP + timedelta(minutes=1))
    assert rep["diagnostics"]["refused"]["game_already_tipped"] == 3
    assert rep["sizes"] == {"2": [], "3": []} and rep["empty_reason"] == "no legs priced before tip"


def test_verdict_is_keep_unless_gate_open_and_ev_low_positive() -> None:
    # model far above a long price: ev_low80 > 0
    legs = [leg(i, 0.97, game=f"G{i}", pid=i, price=+400) for i in range(4)]
    closed = report(legs, gate=Gate(n_settled=10, n_dates=3, skill=0.05))
    assert closed["verdict"] == KEEP
    assert max(t["ev_low80"] for ts in closed["sizes"].values() for t in ts) > 0
    no_skill = report(legs, gate=Gate(n_settled=40, n_dates=20, skill=None))
    assert no_skill["verdict"] == KEEP
    opened = report(legs, gate=Gate(n_settled=40, n_dates=20, skill=0.05))
    assert opened["verdict"] == "positive_ev_found"
    assert opened["gate"] == {
        "settled": 40,
        "settled_min": 30,
        "slate_dates": 20,
        "dates_min": 14,
        "skill_vs_market": 0.05,
        "open": True,
    }
    assert report(legs, gate=Gate(40, 20, 0.05), illustration=True)["verdict"] == KEEP
    # gate open but nothing clears the bound: still keep your money
    fair = [leg(i, 0.80, game=f"G{i}", pid=i, price=-400) for i in range(4)]
    flat = report(fair, gate=Gate(40, 20, 0.05))
    assert flat["verdict"] == KEEP and flat["diagnostics"]["no_positive_ev_found"]


def test_sportsbook_tickets_carry_no_fee_and_kalshi_tickets_pay_the_config_fee_once() -> None:
    """B2: the book's margin is inside the legs' prices; only a venue in fee_venues pays the fee."""
    book = report(cross_game_legs(4))["sizes"]["2"][0]
    assert book["fee"] == 0.0 and book["fee_model"].startswith("none")
    assert book["ev_per_dollar"] == pytest.approx(
        book["p_joint_cal"] / book["book_implied"] - 1.0, abs=2e-3
    )
    assert book["book_implied"] == pytest.approx(implied_prob(-110) ** 2, abs=1e-4)
    assert "margin is included" in book["why"] and "fee schedule" not in book["why"]
    kal = report([leg(i, 0.9 - 0.02 * i, game=f"G{i}", pid=i, book="kalshi") for i in range(4)])
    k2 = kal["sizes"]["2"][0]
    assert k2["fee"] > 0 and k2["fee_model"] == CFG.fee.name
    assert k2["ev_per_dollar"] == pytest.approx(
        k2["p_joint_cal"] / (k2["book_implied"] + k2["fee"]) - 1.0, abs=2e-3
    )
    free = replace(CFG, fee=replace(CFG.fee, coefficient=0.0))
    assert (
        report([leg(i, 0.9, game=f"G{i}", pid=i, book="kalshi") for i in range(4)], cfg=free)[
            "sizes"
        ]["2"][0]["fee"]
        == 0.0
    )  # the rate is read from the config object


def test_leg_priced_after_made_at_but_before_tip_is_refused_and_counted() -> None:
    """B1: a price stamped after the file's own clock may not appear in the file."""
    late = leg(1, 0.95, game="G1", priced=PRICED + timedelta(minutes=10))  # before tip
    ok = [leg(2, 0.7, game="G2"), leg(3, 0.7, game="G3")]
    kept, counts = refuse_post_tip([late, *ok], None, PRICED)
    assert [x.player_id for x in kept] == [2, 3] and counts["priced_after_made_at"] == 1
    rep = report([late, *ok], made_at=PRICED, now=None)
    ids = {x["player_id"] for ts in rep["sizes"].values() for t in ts for x in t["legs"]}
    assert ids == {2, 3} and rep["diagnostics"]["refused"]["priced_after_made_at"] == 1
    for ts in rep["sizes"].values():
        for t in ts:
            assert all(x["price_at"] <= rep["made_at"] for x in t["legs"])


def test_caveat_is_verbatim_from_the_contract_and_free_text_is_screened() -> None:
    contract = (
        Path(__file__).resolve().parents[2] / "docs/contracts/top_parlays_v2.md"
    ).read_text()
    assert T490_CAVEAT in contract
    rep = report(cross_game_legs(4))
    assert (
        rep["caveats"][:2] == [T490_CAVEAT, T495_CAVEAT]
        and rep["diagnostics"]["numguard_removed"] == 0
    )
    with pytest.raises(ValueError, match="banned"):
        report(cross_game_legs(4), calibration={**CAL, "status": "a lock of a ticket"})
    with pytest.raises(ValueError, match="banned"):
        report(cross_game_legs(4), extra_caveats=["this is free money"])


def test_gate_bars_are_the_contract_values_and_interval_is_labelled() -> None:
    rep = report(cross_game_legs(4))
    assert rep["gate"]["settled_min"] == 30 and rep["gate"]["dates_min"] == 14
    assert "tickets_with_positive_point_ev" not in rep["diagnostics"]["search"]
    assert "not a tested residual" in rep["diagnostics"]["search"]["interval_basis"]
    assert "configured noise, not tested" in rep["sizes"]["2"][0]["why"]


def test_numguard_passes_on_every_sentence_and_no_hype() -> None:
    rep = report(cross_game_legs(6) + [leg(50, 0.8, game="G0", pid=500, stat="reb")])
    assert rep["diagnostics"]["numguard_removed"] == 0
    for ts in rep["sizes"].values():
        for t in ts:
            allowed = [*collect_numbers(t), CFG.tail_cut, 1.0 - CFG.alpha]
            for text in (t["why"], *(x["reason"] for x in t["legs"])):
                assert check_prose(text, allowed)[1] == 0, text
                assert not HYPE.search(text), text
                assert "[number removed" not in text
            assert 2 <= t["why"].count(". ") + 1 <= 3


def test_reason_is_a_deterministic_template_of_the_legs_own_fields() -> None:
    a = leg(1, 0.8, game="G1")
    r1, r2 = report([a, leg(2, 0.7, game="G2")]), report([a, leg(2, 0.7, game="G2")])
    assert r1 == r2  # byte-for-byte deterministic
    text = r1["sizes"]["2"][0]["legs"][0]["reason"]
    assert "33.5" in text and "22.4" in text and "21.1" in text and "-110" in text


def test_contract_shape_and_caveat() -> None:
    rep = report(cross_game_legs(5))
    assert rep["schema"] == "top_parlays/2" and T490_CAVEAT in rep["caveats"]
    assert rep["illustration"] is False and rep["calibration"]["joint_status"].endswith(
        "not yet tested"
    )
    assert {"slate_date", "made_at", "calibration", "ranking", "verdict", "empty_reason"} <= set(
        rep
    )
    t = rep["sizes"]["3"][0]
    assert {
        "rank", "same_game", "legs", "p_joint_raw", "p_joint_cal", "p_joint_cal_80",
        "book_implied", "ev_per_dollar", "ev_low80", "why",
    } <= set(t)  # fmt: skip
    lg = t["legs"][0]
    assert {
        "game_id", "tipoff", "player_id", "player_name", "team", "opp", "stat", "side", "line",
        "book", "price_american", "price_at", "p_raw", "p_cal", "model_source", "reason",
    } <= set(lg)  # fmt: skip
    lo, hi = t["p_joint_cal_80"]
    assert lo < t["p_joint_cal"] < hi
    assert t["ev_low80"] < t["ev_per_dollar"]


def test_twelve_game_slate_runs_well_under_a_minute() -> None:
    stats = ("pts", "reb", "ast", "fg3m")
    legs: list[LegCand] = []
    n = 0
    for g in range(12):
        for p in range(10):
            for s, st in enumerate(stats):
                n += 1
                legs.append(
                    leg(
                        n,
                        0.35 + 0.3 * ((n * 7919) % 100) / 100,
                        game=f"G{g}",
                        pid=1000 * g + p,
                        stat=st,
                        side="over" if s % 2 else "under",
                        team=1 + (p % 2),
                    )
                )
    pc = PairCorr(rho={("same_player", "pts", "reb"): 0.3, ("teammate", "pts", "pts"): -0.1}, n={})
    t0 = time.time()
    rep = report(legs, pc=pc)
    elapsed = time.time() - t0
    assert elapsed < 60
    s = rep["diagnostics"]["search"]
    assert s["legs_searched"]["draftkings"] == 40
    assert s["tickets_searched"] == 780 + 9880
    assert len(rep["sizes"]["2"]) == 3 and len(rep["sizes"]["3"]) == 3
