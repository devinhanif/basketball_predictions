# ruff: noqa: E501
"""Entry filters of the halftime scorer on synthetic captures: windows, staleness, Yes-only mechanics."""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from nba.markets import halftime_entry as he
from tests.markets.halftime_helpers import EID, T0, Night, box, market

S = timedelta(seconds=1)


def night(
    tmp: Path,
    *,
    q3_at: int | None = 900,
    page_at: int = 125,
    state_at: int = 110,
    state_status: str = "STATUS_HALFTIME",
    markets: list | None = None,
    players: list | None = None,
    pages: list | None = None,
) -> Path:
    n = Night(tmp / "live_2026-10-26")
    for s in range(0, 900, 60):
        n.scoreboard(T0 + s * S, "STATUS_HALFTIME", 2)
    if q3_at is not None:
        n.scoreboard(T0 + q3_at * S, "STATUS_IN_PROGRESS", 3)
    pl = players or [
        box("Ann Alpha", "DET", 18, pts=12, reb=3, ast=2, tpm=1, pf=2, starter=True),
        box("Bob Beta", "DET", 0, dnp=True, no_stats=True),
        box("Cy Gamma", "PHI", 14, pts=4, reb=5, ast=1, pf=1),
    ]
    n.summary(T0 + state_at * S, pl, state_status)
    mk = markets if markets is not None else [market("KXNBAPTS", "Ann Alpha", 15, ask=0.44)]
    for off in pages if pages is not None else [page_at]:
        n.kalshi("KXNBAPTS", T0 + off * S, mk)
    return n.close()


def snap(path: Path, series: str = "KXNBAPTS") -> tuple[he.Snapshot | None, Counter[str]]:
    r = he.NightReader(path)
    ht = r.hts[EID]
    c: Counter[str] = Counter()
    return r.snapshot(ht, series, c), c


def test_happy_path_first_page_in_window(tmp_path: Path) -> None:
    sn, _ = snap(night(tmp_path))
    assert sn is not None
    assert sn.t_ht == T0 and sn.t_star == T0 + 120 * S
    assert sn.quote_received_at == T0 + 125 * S and sn.state_received_at == T0 + 110 * S
    (c,) = sn.candidates
    assert (c.player_name, c.k, c.ask_c, c.cur, c.minutes, c.starter, c.fouls) == (
        "Ann Alpha",
        15,
        44,
        12,
        18,
        1,
        2,
    )
    assert c.margin == -10  # DET (away) 50 - PHI (home) 60


def test_decision_time_is_ht_plus_120(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, page_at=119))
    assert sn is None and c["series_no_page_in_window"] == 1


def test_page_after_180s_window_is_no_entry(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, page_at=301))
    assert sn is None and c["series_no_page_in_window"] == 1
    sn2, _ = snap(night(tmp_path / "b", page_at=300, state_at=250))
    assert sn2 is not None  # inclusive edge t* + 180


def test_state_older_than_150s_than_the_page_is_refused(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, page_at=260, state_at=100))
    assert sn is None and c["series_state_stale_or_missing"] == 1
    sn2, _ = snap(night(tmp_path / "b", page_at=250, state_at=100))
    assert sn2 is not None  # exactly 150 s old passes


def test_state_must_still_show_halftime(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, state_status="STATUS_IN_PROGRESS"))
    assert sn is None and c["series_state_not_halftime"] == 1


def test_state_after_the_page_is_never_used(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, page_at=125, state_at=200))
    assert (
        sn is None and c["series_state_stale_or_missing"] == 1
    )  # a summary at +200 s did not exist at +125 s


def test_game_in_period_three_at_decision_time_has_no_entry(tmp_path: Path) -> None:
    sn, c = snap(night(tmp_path, q3_at=100))
    assert sn is None and c["q3_before_decision_time"] == 1


def test_second_half_start_is_recorded(tmp_path: Path) -> None:
    r = he.NightReader(night(tmp_path, q3_at=400))
    assert r.hts[EID].q3_first == T0 + 400 * S and r.second_half_started(r.hts[EID])


def test_as_of_hides_later_files(tmp_path: Path) -> None:
    p = night(tmp_path)
    r = he.NightReader(p, as_of=T0 + 100 * S)
    c: Counter[str] = Counter()
    assert r.snapshot(r.hts[EID], "KXNBAPTS", c) is None and c["series_no_page_in_window"] == 1
    r2 = he.NightReader(p, as_of=T0 + 130 * S)
    assert r2.snapshot(r2.hts[EID], "KXNBAPTS", Counter()) is not None
    assert r2.hts[EID].q3_first is None  # the Q3 tick at +900 s is not visible yet


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        (dict(ask=0.01), "x_no_tradeable_ask"),
        (dict(ask=0.98), "x_no_tradeable_ask"),
        (dict(ask=None), "x_no_tradeable_ask"),
        (dict(size=0.0), "x_no_tradeable_ask"),
        (dict(status="closed"), "x_not_active"),
        (dict(k=10), "x_already_resolved"),  # Ann has 12 points
        (dict(k=12), "x_already_resolved"),
        (dict(k=17), "x_off_t508_grid"),
    ],
)
def test_market_filters(tmp_path: Path, kw: dict, reason: str) -> None:
    args = {"ask": 0.44, **kw}
    k = args.pop("k", 15)
    sn, _ = snap(night(tmp_path, markets=[market("KXNBAPTS", "Ann Alpha", k, **args)]))
    assert sn is not None and not sn.candidates and sn.excluded[reason] == 1


def test_ask_band_edges_are_inclusive(tmp_path: Path) -> None:
    sn, _ = snap(
        night(
            tmp_path,
            markets=[
                market("KXNBAPTS", "Ann Alpha", 15, ask=0.02),
                market("KXNBAPTS", "Ann Alpha", 20, ask=0.97),
            ],
        )
    )
    assert sn is not None and [c.ask_c for c in sn.candidates] == [2, 97]


def test_zero_first_half_minutes_excluded(tmp_path: Path) -> None:
    pl = [box("Ann Alpha", "DET", 0, pts=0, starter=False), box("Cy Gamma", "PHI", 14, pts=4)]
    mk = [market("KXNBAPTS", "Ann Alpha", 10), market("KXNBAPTS", "Bob Beta", 10)]
    sn, _ = snap(night(tmp_path, players=pl, markets=mk))
    assert sn is not None and not sn.candidates
    assert sn.excluded["x_dnp_first_half"] == 1 and sn.excluded["x_player_unmatched"] == 1


def test_did_not_play_and_ejected_excluded(tmp_path: Path) -> None:
    pl = [
        box("Ann Alpha", "DET", 5, pts=1, ejected=True),
        box("Bob Beta", "DET", 0, dnp=True, no_stats=True),
    ]
    mk = [market("KXNBAPTS", "Ann Alpha", 10), market("KXNBAPTS", "Bob Beta", 10)]
    sn, _ = snap(night(tmp_path, players=pl, markets=mk))
    assert sn is not None and not sn.candidates
    assert sn.excluded["x_ejected"] == 1 and sn.excluded["x_dnp_first_half"] == 1


def test_unmatched_name_is_listed_never_guessed(tmp_path: Path) -> None:
    sn, _ = snap(night(tmp_path, markets=[market("KXNBAPTS", "Ann Alphabet", 15)]))
    assert sn is not None and not sn.candidates
    assert (
        sn.excluded["x_player_unmatched"] == 1 and sn.excluded["unmatched_names:Ann Alphabet"] == 1
    )


def test_two_players_one_folded_name_is_ambiguous(tmp_path: Path) -> None:
    pl = [box("Jaylen Smith", "DET", 10, pts=2), box("Jaylen Smith Jr.", "PHI", 10, pts=2)]
    sn, _ = snap(night(tmp_path, players=pl, markets=[market("KXNBAPTS", "Jaylen Smith", 10)]))
    assert sn is not None and not sn.candidates and sn.excluded["x_player_ambiguous"] == 1


def test_non_greater_and_non_integer_strikes_are_skipped(tmp_path: Path) -> None:
    bad = market("KXNBAPTS", "Ann Alpha", 15, strike_type="less")
    odd = market("KXNBAPTS", "Ann Alpha", 15)
    odd["floor_strike"] = 15.0
    sn, _ = snap(night(tmp_path, markets=[bad, odd]))
    assert sn is not None and not sn.candidates and sn.excluded["markets_at_snapshot"] == 0


def test_other_games_markets_are_ignored(tmp_path: Path) -> None:
    other = market("KXNBAPTS", "Ann Alpha", 15)
    other["event_ticker"] = "KXNBAPTS-26OCT26BOSNYK"
    sn, _ = snap(night(tmp_path, markets=[other]))
    assert sn is not None and not sn.candidates and sn.excluded["markets_at_snapshot"] == 0


def test_player_stat_uses_the_right_column_and_three_pointers_parse(tmp_path: Path) -> None:
    pl = [box("Ann Alpha", "DET", 18, pts=12, reb=3, ast=2, tpm=2, pf=2)]
    n = Night(tmp_path / "live_2026-10-26")
    for s in range(0, 900, 60):
        n.scoreboard(T0 + s * S, "STATUS_HALFTIME", 2)
    n.scoreboard(T0 + 900 * S, "STATUS_IN_PROGRESS", 3)
    n.summary(T0 + 110 * S, pl)
    n.kalshi("KXNBA3PT", T0 + 125 * S, [market("KXNBA3PT", "Ann Alpha", 3, unit="three pointers")])
    n.kalshi("KXNBAREB", T0 + 125 * S, [market("KXNBAREB", "Ann Alpha", 4, unit="rebounds")])
    n.close()
    r = he.NightReader(tmp_path / "live_2026-10-26")
    s3 = r.snapshot(r.hts[EID], "KXNBA3PT", Counter())
    sr = r.snapshot(r.hts[EID], "KXNBAREB", Counter())
    assert s3 is not None and s3.candidates[0].cur == 2 and s3.candidates[0].stat == "fg3m"
    assert sr is not None and sr.candidates[0].cur == 3 and sr.candidates[0].stat == "reb"


def test_no_side_fields_are_read() -> None:
    src = Path(he.__file__).read_text()
    for word in ("no_ask", "no_bid", "no_sub_title"):
        assert word not in src, word
    assert not re.search(r"\bside\b", src)
