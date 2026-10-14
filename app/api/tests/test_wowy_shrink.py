"""The shrunk with/without-teammate headline: the arithmetic, the threes rule, the flags, the
wording, and the wall (pure arithmetic on rows already loaded; no read of any 2025 data)."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_role import GAME, PLAYER, P, _teammate_hist, row

from parlayhitters_api import breakdown as bd
from parlayhitters_api import wowy_shrink as ws
from parlayhitters_api.history import History, Row
from parlayhitters_api.role import player_role
from parlayhitters_api.store import Store
from parlayhitters_api.wowy_shrink import (
    COEF,
    GATE_SOURCE,
    SHRINK_K,
    beyond,
    coef_spans_zero,
    large_cut,
    plain_note,
)

SAT = (6, 7, 8, 9)  # the games X sat in _teammate_hist


@pytest.fixture(scope="module")
def store() -> Store:
    return Store()


def _with_base(
    h: History, fn: Callable[[Row, bool], tuple[int, int, int, int, int, int]]
) -> History:
    h.logs[P] = [Row(**{**r.__dict__, "base": fn(r, int(r.game_id[1:]) in SAT)}) for r in h.logs[P]]
    return h


def test_expected_extra_is_coefficient_times_weight_times_raw_gap() -> None:
    m = player_role(_teammate_hist(), GAME, PLAYER).teammates_out[0]
    b = m.beyond
    assert (b.n_without, b.n_with) == (4, 11)
    assert b.weight == pytest.approx(4 / (4 + SHRINK_K))
    by = {s.stat: s for s in b.stats}
    # P: 10 / 5 / 2 / 1 without X, 20 / 6 / 4 / 1 with; 30 minutes either way
    assert by["pts"].past_gap == pytest.approx(-10.0)
    assert by["pts"].expected_extra == pytest.approx(COEF["pts"][0] * 0.4 * -10.0)
    assert by["pts"].expected_extra == pytest.approx(-0.14764, abs=1e-4)
    assert by["reb"].expected_extra == pytest.approx(COEF["reb"][0] * 0.4 * -1.0)
    assert by["ast"].expected_extra == pytest.approx(COEF["ast"][0] * 0.4 * -2.0)
    assert by["min"].past_gap == pytest.approx(0.0)
    assert by["min"].expected_extra == pytest.approx(0.0)


def test_the_shrunk_number_is_a_small_fraction_of_the_raw_gap() -> None:
    b = player_role(_teammate_hist(), GAME, PLAYER).teammates_out[0].beyond
    for s in b.stats:
        if s.past_gap and s.expected_extra is not None:
            assert abs(s.expected_extra) < 0.25 * abs(s.past_gap)


def test_threes_are_zero_and_say_no_measurable_effect_even_with_a_big_raw_gap() -> None:
    h = _with_base(
        _teammate_hist(), lambda r, sat: (r.base[0], r.base[1], r.base[2], 0 if sat else 9, 0, 0)
    )
    b = player_role(h, GAME, PLAYER).teammates_out[0].beyond
    t = next(s for s in b.stats if s.stat == "fg3m")
    assert t.past_gap == pytest.approx(-9.0)  # the raw history is still computed
    assert t.no_effect and t.expected_extra == 0.0
    assert t.one_in is None and not t.large_gap
    assert all(not s.no_effect for s in b.stats if s.stat != "fg3m")


def test_raw_history_is_still_present_and_the_small_flag_stays() -> None:
    m = player_role(_teammate_hist(), GAME, PLAYER).teammates_out[0]
    assert m.without.n == 4 and m.with_.n == 11 and m.without.pts36 is not None
    assert m.beyond.n_without == m.without.n and m.beyond.n_with == m.with_.n
    assert m.beyond.small and m.without.small and "small sample" in m.note.lower()


def test_a_large_gap_is_flagged_and_says_large_gaps_repeat_least() -> None:
    h = _with_base(
        _teammate_hist(),
        lambda r, sat: (0 if sat else 40, r.base[1], r.base[2], r.base[3], r.base[4], r.base[5]),
    )
    b = player_role(h, GAME, PLAYER).teammates_out[0].beyond
    pts = next(s for s in b.stats if s.stat == "pts")
    assert pts.past_gap is not None
    assert pts.large_gap and abs(b.weight * pts.past_gap) >= large_cut("pts")
    assert any("repeat least" in f and "points" in f for f in b.flags)
    # the ordinary fixture has a mid-size gap and no flag
    assert player_role(_teammate_hist(), GAME, PLAYER).teammates_out[0].beyond.flags == []


def test_no_games_on_either_side_gives_no_number() -> None:
    for b in (beyond([], [row(30.0)]), beyond([row(30.0)], [])):
        assert not b.available
        assert all(s.past_gap is None and s.expected_extra is None for s in b.stats)
    assert beyond([], [row(30.0)]).weight == 0.0


def test_the_plain_note_fractions_come_from_the_coefficients() -> None:
    mean = statistics.fmean(COEF[s][0] for s in ("pts", "reb", "ast"))
    note = plain_note()
    assert f"about 1 in {round(1 / mean)} of a gap" in note
    assert f"about 1 in {round(1 / COEF['min'][0])} for minutes" in note
    assert note.startswith("The forecast already knows he is out.")
    assert "Threes showed nothing" in note
    assert player_role(_teammate_hist(), GAME, PLAYER).teammates_out[0].beyond.note == note


def test_constants_cite_the_gate_and_match_it() -> None:
    assert "gate.json" in GATE_SOURCE and "2026-10-14" in GATE_SOURCE
    assert SHRINK_K == 6.0
    assert coef_spans_zero("fg3m")
    assert not any(coef_spans_zero(s) for s in ("pts", "reb", "ast", "min"))
    assert 0.03 <= COEF["pts"][0] <= 0.04 and 0.03 <= COEF["reb"][0] <= 0.05
    assert 0.04 <= COEF["ast"][0] <= 0.05 and 0.16 <= COEF["min"][0] <= 0.18


def test_the_module_is_pure_arithmetic_with_no_2025_path() -> None:
    src = Path(ws.__file__).read_text()
    assert "2025" not in src
    for token in ("open(", "read_text", "duckdb", "export", "Path"):
        assert token not in src.split('"""', 2)[2], token


def test_leg_breakdown_carries_the_same_headline_and_keeps_the_raw_numbers() -> None:
    h = _teammate_hist()
    t = bd.build(h, GAME, PLAYER, "pts", 14.5, "over").teammates.items[0]
    r = player_role(h, GAME, PLAYER).teammates_out[0]
    assert t.beyond.model_dump() == r.beyond.model_dump()
    assert t.per36_without.n == 4 and t.without.n == 4


def test_fixture_slate_headlines_recompute_from_raw_sets_and_stay_pre_2025(
    client: TestClient, store: Store
) -> None:
    assert store.history is not None
    seen = 0
    for date_, slate in store.slates.items():
        assert date_ < "2025-01-01"
        for g in slate["games"][:2]:
            for pl in client.get(f"/api/games/{g['game_id']}").json()["players"]:
                for m in pl["role"]["teammates_out"]:
                    b = m["beyond"]
                    assert b["n_without"] == m["without"]["n"] and b["n_with"] == m["with_"]["n"]
                    assert b["weight"] == pytest.approx(
                        b["n_without"] / (b["n_without"] + SHRINK_K)
                    )
                    for s in b["stats"]:
                        if s["expected_extra"] is None:
                            continue
                        want = (
                            0.0
                            if s["no_effect"]
                            else COEF[s["stat"]][0] * b["weight"] * s["past_gap"]
                        )
                        assert s["expected_extra"] == pytest.approx(want)
                    seen += 1
    assert seen > 0
