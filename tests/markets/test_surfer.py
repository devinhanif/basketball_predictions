"""Market-surfer paper replay: synthetic quotes, no network, no fixtures from the live store."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from nba.markets import surfer as sf
from nba.parlay.config import load_config
from nba.parlay.ev import order_fee

P = sf.load_params()
FEE = load_config().fee


def mk(quotes: list[tuple[float, float]], result: str = "yes", size: float = 100.0) -> sf.Market:
    recs = [sf.Rec(i, 1_000.0 + 60.0 * i, b, a, size, size) for i, (b, a) in enumerate(quotes)]
    m = sf.Market(
        "KXNBAPTS-26OCT22DENOKC-X-10",
        "KXNBAPTS",
        "26OCT22DENOKC",
        "2026-10-22",
        result,
        ("DEN", "OKC"),
    )
    m.recs = recs
    m.by_idx = {r.idx: k for k, r in enumerate(recs)}
    m.inplay = [True] * len(recs)
    return m


RISE = [(0.48, 0.50), (0.49, 0.51), (0.50, 0.52), (0.52, 0.54), (0.53, 0.55)]


def test_take_profit_round_trip_pays_spread_and_fees() -> None:
    m = mk(RISE + [(0.61, 0.63), (0.60, 0.62), (0.60, 0.62)])
    trades, missed = sf.run_market(m, P, FEE)
    assert missed == 0 and len(trades) == 1
    t = trades[0]
    assert t.reason == "tp" and t.side == 0
    assert t.entry_ask == pytest.approx(0.55)  # filled on the NEXT snapshot at the ask
    assert t.exit_price == pytest.approx(
        0.60
    )  # filled on the snapshot after the trigger, at the bid
    n = int(P["unit_contracts"])
    fe = order_fee(0.55, FEE, n, order_type="taker", series="KXNBAPTS") / n
    fx = order_fee(0.60, FEE, n, order_type="taker", series="KXNBAPTS") / n
    assert t.pnl == pytest.approx(0.60 - 0.55 - fe - fx)
    assert t.pnl_hold == pytest.approx(1.0 - 0.55 - fe)  # result yes: held to settlement pays 1
    # decomposition identity: pnl = mid drift - spread cost - fees
    assert t.pnl == pytest.approx(t.pnl_gross_mid - t.spread_cost - fe - fx)


def test_stop_and_settlement_fallback() -> None:
    m = mk(RISE + [(0.45, 0.47), (0.44, 0.46), (0.44, 0.46)], result="no")
    t = sf.run_market(m, P, FEE)[0][0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(0.44)
    # market never reaches a trigger -> held to settlement; losing side pays 0 and no exit fee
    m2 = mk(RISE + [(0.54, 0.56)] * 3, result="no")
    t2 = sf.run_market(m2, P, FEE)[0][0]
    assert t2.reason == "settle" and t2.exit_price == 0.0 and t2.fee_exit == 0.0


def test_no_entry_when_spread_wide_or_size_thin_or_not_in_play() -> None:
    wide = mk([(0.46, 0.50), (0.47, 0.51), (0.48, 0.52), (0.50, 0.54), (0.51, 0.55), (0.51, 0.55)])
    assert sf.run_market(wide, P, FEE) == ([], 0)
    thin = mk(RISE + [(0.61, 0.63)] * 3, size=10.0)
    assert sf.run_market(thin, P, FEE) == ([], 0)
    off = mk(RISE + [(0.61, 0.63)] * 3)
    off.inplay = [False] * len(off.recs)
    assert sf.run_market(off, P, FEE) == ([], 0)


def test_missed_fill_when_ask_jumps_and_nothing_is_retried() -> None:
    m = mk([(0.48, 0.50), (0.49, 0.51), (0.50, 0.52), (0.52, 0.54), (0.58, 0.60), (0.66, 0.68)])
    trades, missed = sf.run_market(m, P, FEE)
    assert trades == [] and missed >= 1


def test_no_side_mirrors_yes_side() -> None:
    fall = [(1 - a, 1 - b) for b, a in RISE] + [
        (1 - 0.63, 1 - 0.61),
        (1 - 0.62, 1 - 0.60),
        (1 - 0.62, 1 - 0.60),
    ]
    t = sf.run_market(mk(fall, result="no"), P, FEE)[0][0]
    assert t.side == 1 and t.reason == "tp" and t.entry_ask == pytest.approx(0.55)


def test_lookback_requires_contiguous_snapshots() -> None:
    m = mk(RISE + [(0.61, 0.63)] * 3)
    m.recs = [sf.Rec(r.idx * 5, r.t + 600 * r.idx, r.yb, r.ya, r.ybs, r.yas) for r in m.recs]
    m.by_idx = {r.idx: k for k, r in enumerate(m.recs)}
    assert sf.run_market(m, P, FEE) == ([], 0)


def test_event_parse_and_verdict_rule() -> None:
    assert sf.parse_event("KXNBAPTS-26OCT22DENOKC") == (
        "26OCT22DENOKC",
        "2026-10-22",
        ("DEN", "OKC"),
    )
    assert sf.parse_event("KXNBAWEIRD") is None
    gate = P["gate"]
    ok = (0.01, 0.03)
    assert sf.verdict(50, 2, 5, -0.03, ok, ok, 0.0, 0.0, gate) == "insufficient_sample"
    assert (
        sf.verdict(500, 2, 30, -0.03, (-0.05, -0.01), (-0.04, -0.02), 0.0, 0.0, gate)
        == "negative_ev_supported"
    )
    # a positive mean on 2 nights can never be a candidate
    assert sf.verdict(500, 2, 30, 0.02, ok, ok, 0.0, 0.0, gate) == "no_positive_ev_found"
    assert sf.verdict(500, 5, 30, 0.02, ok, ok, 0.0, 0.0, gate) == "positive_ev_candidate"
    assert sf.verdict(500, 5, 30, 0.02, ok, ok, 0.05, 0.0, gate) == "no_positive_ev_found"


def test_game_bootstrap_clusters_by_game() -> None:
    m = mk(RISE + [(0.61, 0.63), (0.60, 0.62), (0.60, 0.62)])
    t = sf.run_market(m, P, FEE)[0][0]
    mean, lo, hi = sf.game_bootstrap([t, t], [0.1, 0.3], 200, 1, 0.95)
    assert mean == pytest.approx(0.2) and not math.isnan(lo) and lo <= hi


def test_fee_is_read_from_the_verified_config_object() -> None:
    assert FEE.verified
    src = Path(sf.__file__).read_text()
    assert "0.07" not in src and "0.0175" not in src


def test_no_execution_surface_in_surfer_modules() -> None:
    banned = re.compile(
        r"/orders|/portfolio|create_order|place_order|api[_-]?key|private[_-]?key|Authorization",
        re.I,
    )
    for name in ("surfer.py", "surfer_run.py"):
        text = (Path(sf.__file__).parent / name).read_text()
        assert not banned.search(text), name
        assert "requests" not in text and "urllib" not in text and "httpx" not in text
