"""Universe assembly: integer cents, entry snapshot, filters, game-level settlement, DNP scalar."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from research.market_forward.__main__ import build_universe
from research.market_forward.data import (
    Universe,
    dollars_to_micro,
    et_date,
    ladder_key,
    open_copy,
)
from research.market_forward.spec import LONGSHOT, UNDERS, RuleSpec
from tests.research.market_forward.conftest import GAME, TIP, Mk, build_capture

_N = 0


def run(
    tmp: Path,
    mks: list[Mk],
    spec: RuleSpec = UNDERS,
    window_start: date = date(2026, 10, 25),
    cutoff: date = date(2026, 10, 29),
) -> Universe:
    global _N
    _N += 1
    sub = tmp / f"cap{_N}"
    sub.mkdir()
    p = build_capture(sub, mks)
    uni, _ = build_universe(
        spec, p["db"], p["raw"], p["sched"], window_start, cutoff, date(2026, 11, 1)
    )
    return uni


def mk(n: int, bid: float | None, ask: float | None, res: str | None = "no", **kw: Any) -> Mk:
    return Mk(f"KXNBAPTS-{GAME}-AAAP{n}-{n}", "KXNBAPTS", res, bid, ask, **kw)


def test_exact_five_cent_spread_is_eligible_float_columns_never_compared(tmp_path: Path) -> None:
    # 0.2 and 0.25 as FLOAT32; the float difference is not exactly 0.05 but the integer cents are.
    u = run(tmp_path, [mk(1, 0.2, 0.25), mk(2, 0.2, 0.26), mk(3, 0.0, 0.03), mk(4, 0.7, 0.75)])
    assert sorted(u.df["bid_c"].to_list()) == [20, 70]
    assert u.counts["wide_spread"] == 1
    assert u.counts["bid_zero"] == 1
    assert u.df["ask_c"].to_list() == [25, 75]


def test_longshot_mid_cap_is_integer_and_inclusive(tmp_path: Path) -> None:
    ms = [mk(1, 0.14, 0.16), mk(2, 0.15, 0.17), mk(3, 0.01, 0.02)]
    lu = run(tmp_path, ms, spec=LONGSHOT)
    assert sorted(lu.df["bid_c"].to_list()) == [1, 14]  # mid 0.15 in, mid 0.16 out
    assert lu.counts["above_15c"] == 1
    uu = run(tmp_path, ms, spec=UNDERS)
    assert uu.df.height == 3  # UNDERS has no upper mid limit


def test_entry_is_latest_row_at_or_before_t_minus_60_never_later(tmp_path: Path) -> None:
    cut = TIP - timedelta(minutes=60)
    m = mk(
        1,
        0.30,
        0.32,
        ts=cut - timedelta(minutes=20),
        extra_rows=[
            (cut + timedelta(minutes=1), 0.60, 0.62),
            (cut - timedelta(minutes=50), 0.1, 0.12),
        ],
    )
    u = run(tmp_path, [m])
    assert u.df["bid_c"].to_list() == [30]
    assert u.df["age_min"].to_list() == [pytest.approx(20.0)]


def test_entry_age_limit_240_inclusive(tmp_path: Path) -> None:
    cut = TIP - timedelta(minutes=60)
    ok = mk(1, 0.3, 0.32, ts=cut - timedelta(minutes=240))
    stale = mk(2, 0.3, 0.32, ts=cut - timedelta(minutes=241))
    u = run(tmp_path, [ok, stale])
    assert u.df.height == 1
    assert u.counts["no_entry_snapshot"] == 1


def test_game_with_any_eligible_contract_unsettled_is_dropped_whole(tmp_path: Path) -> None:
    ms = [mk(1, 0.3, 0.32, "yes"), mk(2, 0.3, 0.32, "no"), mk(3, 0.3, 0.32, None)]
    u = run(tmp_path, ms)
    assert u.df.height == 0
    assert u.game_counts["unsettled_game"] == 1
    # an unsettled but INELIGIBLE contract (wide spread) does not drop the game
    ms2 = [mk(1, 0.3, 0.32, "yes"), mk(2, 0.3, 0.32, "no"), mk(3, 0.3, 0.50, None)]
    u2 = run(tmp_path, ms2)
    assert u2.df.height == 2 and u2.game_counts["unsettled_game"] == 0


def test_longshot_unsettled_spread_contract_drops_the_game(tmp_path: Path) -> None:
    sp = Mk(f"KXNBASPREAD-{GAME}-AAA5", "KXNBASPREAD", None, 0.05, 0.07, stat="spread")
    u = run(tmp_path, [mk(1, 0.05, 0.07, "no"), sp], spec=LONGSHOT)
    assert u.df.height == 0 and u.game_counts["unsettled_game"] == 1


def test_longshot_spread_is_descriptive_not_in_pool(tmp_path: Path) -> None:
    sp = Mk(f"KXNBASPREAD-{GAME}-AAA5", "KXNBASPREAD", "no", 0.05, 0.07, stat="spread")
    u = run(tmp_path, [mk(1, 0.05, 0.07, "no"), sp], spec=LONGSHOT)
    flags = dict(zip(u.df["series"].to_list(), u.df["in_pool"].to_list(), strict=True))
    assert flags == {"KXNBAPTS": True, "KXNBASPREAD": False}
    # UNDERS does not read game markets at all
    assert run(tmp_path, [mk(1, 0.05, 0.07, "no"), sp]).df.height == 1


def test_dnp_scalar_paid_from_raw_page_and_counted(tmp_path: Path) -> None:
    ms = [mk(1, 0.3, 0.32, "scalar", scalar="0.1600"), mk(2, 0.3, 0.32, "no")]
    u = run(tmp_path, ms)
    row = u.df.filter(u.df["dnp"])
    assert row["y_u"].to_list() == [160_000]
    assert u.n_dnp == 1 and u.scalar_total == 1 and u.scalar_missing == []


def test_scalar_without_value_is_excluded_counted_and_flagged(tmp_path: Path) -> None:
    ms = [mk(1, 0.3, 0.32, "scalar"), mk(2, 0.3, 0.32, "no")]
    u = run(tmp_path, ms)
    assert u.df.height == 1
    assert u.scalar_missing == [ms[0].ticker]
    assert u.counts["scalar_no_value"] == 1


def test_window_and_schedule_exclusions(tmp_path: Path) -> None:
    ms = [mk(1, 0.3, 0.32, "no")]
    assert run(tmp_path, ms, window_start=date(2026, 10, 28)).game_counts["before_window"] == 1
    assert run(tmp_path, ms, cutoff=date(2026, 10, 26)).game_counts["after_cutoff"] == 1


def test_no_schedule_match_is_excluded_and_counted(tmp_path: Path) -> None:
    import polars as pl

    p = build_capture(tmp_path, [mk(1, 0.3, 0.32, "no")])
    pl.DataFrame(
        {"gameId": ["x"], "gameDateTimeUTC": ["2026-10-27T23:30:00Z"], "gameDateTimeEst": ["y"]}
    ).write_parquet(p["sched"])
    uni, _ = build_universe(
        UNDERS,
        p["db"],
        p["raw"],
        p["sched"],
        date(2026, 10, 25),
        date(2026, 10, 29),
        date(2026, 11, 1),
    )
    assert uni.df.height == 0 and uni.game_counts["no_schedule_match"] == 1


def test_null_player_props_are_kept_and_listed(tmp_path: Path) -> None:
    u = run(tmp_path, [mk(1, 0.3, 0.32, "no", player_id=None)])
    assert u.df.height == 1 and u.null_player_props == [u.df["ticker"][0]]


def test_coverage_counts_settled_markets_with_an_entry_row(tmp_path: Path) -> None:
    cut = TIP - timedelta(minutes=60)
    ms = [mk(1, 0.3, 0.32, "no"), mk(2, 0.3, 0.32, "no", ts=cut - timedelta(minutes=500))]
    u = run(tmp_path, ms)
    assert u.coverage_by_date[date(2026, 10, 27)] == (1, 2)


def test_live_snapshot_db_is_refused() -> None:
    live = Path(__file__).resolve().parents[3] / "data" / "kalshi" / "kalshi.duckdb"
    with pytest.raises(PermissionError):
        open_copy(live)


def test_helpers() -> None:
    assert dollars_to_micro("0.1600") == 160_000
    assert et_date(TIP) == date(2026, 10, 27)
    assert ladder_key("KXNBAPTS", "KXNBAPTS-E-ABCDEF9-10", "KXNBAPTS-E") == "KXNBAPTS-E-ABCDEF9"
    assert (
        ladder_key("KXNBASPREAD", "KXNBASPREAD-E-SAC8", "KXNBASPREAD-E")
        == "KXNBASPREAD|KXNBASPREAD-E|SAC"
    )
    assert ladder_key("KXNBATOTAL", "KXNBATOTAL-E-259", "KXNBATOTAL-E") == "KXNBATOTAL|KXNBATOTAL-E"
