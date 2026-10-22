"""Paper trader on recorded Kalshi fixtures: scan math, refusal after tip, exit rule, settle P&L."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.papertrade import config as C
from nba.papertrade import db
from nba.papertrade.forecasts import Forecasts, PropForecast, WinForecast, load_forecasts
from nba.papertrade.monitor import monitor_once
from nba.papertrade.rules import (
    EntryRefused,
    LegMark,
    Plan,
    build_plan,
    control_leg,
    exit_decision,
    exit_proceeds,
    flipped,
    plan_entry,
    screen_leg,
    shrunk,
)
from nba.papertrade.scan import NO_EV, check_entry, run_scan
from nba.papertrade.settle import settle_positions, summarize
from nba.papertrade.stats import cluster_ratio
from nba.parlay.ev import order_fee

FIX = Path(__file__).parents[1] / "live" / "fixtures" / "kalshi_markets_pages.json"
DAY = date(2026, 10, 21)
TIP_LATE = datetime(2026, 10, 22, 2, 0)  # GSW @ LAL
TIP_EARLY = datetime(2026, 10, 21, 23, 30)  # CHA @ BKN
NOW = datetime(2026, 10, 21, 20, 0)
ALIASES = {"stephen curry": 201939, "luka doncic": 1629029, "grayson allen": 1627}


@pytest.fixture
def cfg() -> C.PaperConfig:
    return C.load()


def markets() -> list[dict[str, Any]]:
    ms = json.loads(FIX.read_text())["markets"]
    allen = copy.deepcopy(next(m for m in ms if m["ticker"].endswith("LALLDONCIC77-30")))
    allen["ticker"] = "KXNBAPTS-26OCT21CHABKN-CHAGALLEN8-30"
    allen["event_ticker"] = "KXNBAPTS-26OCT21CHABKN"
    allen["title"] = "Grayson Allen: 30+ points"
    return [*ms, allen]


def prop(
    pid: int, game: str, tip: datetime, p: dict[int, float], p_play: float = 0.97
) -> PropForecast:
    counts = [50] * 60
    for k, v in p.items():
        counts[k - 1] = round(100 * v)
    return PropForecast(game, tip, NOW - timedelta(hours=2), "pts", pid, tuple(counts), 100, p_play)


def forecasts(curry: float = 0.80, allen: float = 0.85, doncic: float = 0.30) -> Forecasts:
    fc = Forecasts()
    fc.props[(201939, "pts")] = prop(201939, "G1", TIP_LATE, {25: curry, 30: 0.4, 35: 0.2, 20: 0.9})
    fc.props[(1629029, "pts")] = prop(1629029, "G1", TIP_LATE, {30: doncic, 35: 0.2, 40: 0.1})
    fc.props[(1627, "pts")] = prop(1627, "G2", TIP_EARLY, {30: allen})
    fc.wins[("GSW", "LAL")] = WinForecast("G1", TIP_LATE, NOW, "GSW", "LAL", 0.8)
    return fc


def scan(cfg: C.PaperConfig, tmp: Path, fc: Forecasts, now: datetime = NOW) -> Any:
    return run_scan(
        DAY, now, cfg, markets=markets(), fc=fc, aliases=ALIASES,
        db_path=tmp / "pt.duckdb", legs_file=tmp / "legs.json",
    )  # fmt: skip


# ----------------------------------------------------------------------------- scan math
def test_fee_comes_from_the_verified_config_object(cfg: C.PaperConfig) -> None:
    n, fee = plan_entry(0.50, cfg.fee, 8.0)
    assert fee == order_fee(0.50, cfg.fee, n)
    assert n * 0.5 + fee <= 8.0 < (n + 1) * 0.5 + order_fee(0.50, cfg.fee, n + 1)
    free = replace(cfg.fee, coefficient=0.0)
    n0, fee0 = plan_entry(0.50, free, 8.0)
    assert fee0 == 0.0 and n0 == 16  # a different fee object changes the sizing: nothing hardcoded


def test_unverified_fee_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "parlay.yaml"
    bad.write_text(Path(C.PARLAY_PATH).read_text().replace("verified: true", "verified: false", 1))
    with pytest.raises(ValueError, match="verified"):
        C.load(parlay_path=bad)


def test_shrink_and_screen_reasons(cfg: C.PaperConfig, tmp_path: Path) -> None:
    assert shrunk(0.8, 0.5, 0.5) == pytest.approx(0.65)
    res = scan(cfg, tmp_path, forecasts())
    with db.reader(tmp_path / "pt.duckdb") as con:
        why = dict(con.execute("SELECT reason, count(*) FROM candidates GROUP BY 1").fetchall())
        pshr = con.execute(
            "SELECT p_model, p_shrunk, bid, ask FROM candidates WHERE ticker LIKE '%GSWSCURRY30-25'"
        ).fetchone()
    assert why["spread_over_cap"] >= 2  # Curry 30+ (17c wide) and 15+ (8c wide)
    assert why["leg_type_disabled"] == 4  # game winners are logged, not traded
    assert res.n_candidates >= 1
    p_model, p_shr, bid, ask = pshr
    assert p_shr == pytest.approx(0.5 * p_model + 0.5 * (bid + ask) / 2)


def test_scan_builds_single_and_cross_window_parlay(cfg: C.PaperConfig, tmp_path: Path) -> None:
    res = scan(cfg, tmp_path, forecasts())
    assert res.verdict == "positions_opened" and len(res.positions) == 2
    assert res.notes["slots_without_plan"] == ["parlay3"]  # only two tip windows on this slate
    with db.reader(tmp_path / "pt.duckdb") as con:
        pos = con.execute(
            "SELECT slot, role, n_legs, n_contracts, entry_price, entry_fee, cost, entry_label "
            "FROM positions ORDER BY role, slot"
        ).fetchall()
        legs = con.execute(
            "SELECT p.slot, p.role, l.ticker, l.side, l.ask, l.tipoff FROM position_legs l "
            "JOIN positions p USING (position_id) WHERE p.role = 'strategy' ORDER BY 1, l.leg_idx"
        ).fetchall()
    strat = {r[0]: r for r in pos if r[1] == "strategy"}
    assert strat["single"][2] == 1 and strat["parlay2"][2] == 2
    assert all(r[6] <= cfg.stake_usd + 1e-9 for r in pos)  # fixed paper stake, never exceeded
    p2 = [leg for leg in legs if leg[0] == "parlay2"]
    assert p2[0][5] != p2[1][5]  # different tip windows
    assert strat["parlay2"][4] == pytest.approx(p2[0][4] * p2[1][4])  # entry = product of asks
    assert "product of leg asks" in strat["parlay2"][7]
    assert {r[1] for r in pos} == {"strategy", "control"}
    legs_file = json.loads((tmp_path / "legs.json").read_text())
    assert set(legs_file["tickers"]) >= {leg[2] for leg in legs}


def test_control_takes_the_market_favourite_side(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scan(cfg, tmp_path, forecasts())
    with db.reader(tmp_path / "pt.duckdb") as con:
        rows = con.execute(
            "SELECT l.side, l.mid FROM position_legs l JOIN positions p USING (position_id) "
            "WHERE p.role = 'control'"
        ).fetchall()
    assert rows and all(mid >= 0.5 for _, mid in rows)  # always the side the market favours


def test_no_positive_ev_found_is_a_verdict(cfg: C.PaperConfig, tmp_path: Path) -> None:
    flat = Forecasts()
    pids = {"SCURRY30": (201939, "G1", TIP_LATE), "LDONCIC77": (1629029, "G1", TIP_LATE),
            "GALLEN8": (1627, "G2", TIP_EARLY)}  # fmt: skip
    for m in markets():
        for key, (pid, game, tip) in pids.items():
            if key in m["ticker"]:
                k = int(float(m["floor_strike"]) // 1) + 1
                mid = (float(m["yes_bid_dollars"]) + float(m["yes_ask_dollars"])) / 2
                f = flat.props.get((pid, "pts")) or prop(pid, game, tip, {})
                counts = list(f.counts)
                counts[k - 1] = round(100 * mid)
                flat.props[(pid, "pts")] = replace(f, counts=tuple(counts))
    res = scan(cfg, tmp_path, flat)
    assert res.verdict == NO_EV and res.positions == []
    with db.reader(tmp_path / "pt.duckdb") as con:
        assert con.execute("SELECT verdict FROM scans").fetchone() == (NO_EV,)
        assert con.execute("SELECT count(*) FROM positions").fetchone() == (0,)


def test_second_scan_does_not_double_fill(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scan(cfg, tmp_path, forecasts())
    again = scan(cfg, tmp_path, forecasts())
    assert again.positions == []
    with db.reader(tmp_path / "pt.duckdb") as con:
        n = con.execute("SELECT count(*) FROM positions WHERE role = 'strategy'").fetchone()
    assert n == (2,)


def test_entry_refused_at_or_after_tip(cfg: C.PaperConfig, tmp_path: Path) -> None:
    res = scan(cfg, tmp_path, forecasts(), now=TIP_EARLY)  # Allen's game has tipped
    with db.reader(tmp_path / "pt.duckdb") as con:
        legs = con.execute("SELECT ticker FROM position_legs").fetchall()
    assert not any("CHABKN" in t[0] for t in legs)  # no leg of a game at/after tip
    assert res.verdict in ("positions_opened", NO_EV)
    plan = _any_plan(cfg)
    with pytest.raises(EntryRefused):
        check_entry(plan, plan.legs[0].tipoff, cfg)
    with pytest.raises(EntryRefused):
        check_entry(plan, plan.legs[0].tipoff - timedelta(minutes=cfg.entry_lead_min - 1), cfg)
    check_entry(plan, plan.legs[0].tipoff - timedelta(hours=1), cfg)


def _any_plan(cfg: C.PaperConfig) -> Plan:
    from nba.papertrade.scan import legs_from_markets

    legs, _, _ = legs_from_markets(markets(), forecasts(), cfg, ALIASES)
    ok = [x for x in legs if screen_leg(x, cfg, now=NOW)[0]]
    return build_plan("single", (ok[0],), cfg)


def test_forecast_loader_ignores_rows_made_at_or_after_tip(cfg: C.PaperConfig) -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, "
        "tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, "
        "prediction JSON)"
    )
    body = json.dumps({"p_ge_full": {"c": [90, 80], "n": 100}, "p_play": 0.95})
    for made in (
        datetime(2026, 10, 21, 20, 0),
        datetime(2026, 10, 22, 2, 5),
    ):  # second is after tip
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r', ?, 'G1', ?, 'props_context_residual', "
            "'v', 'pts', 5, ?)",
            [made, TIP_LATE, body],
        )
    fc = load_forecasts(con, DAY, datetime(2026, 10, 22, 3, 0))
    assert fc.props[(5, "pts")].made_at == datetime(2026, 10, 21, 20, 0)
    assert fc.props[(5, "pts")].p_ge(2) == 0.8 and fc.props[(5, "pts")].p_ge(3) is None


# ----------------------------------------------------------------------------- exit rule
def lm(
    bid: float, ask: float, started: bool, p: float = 0.7, settled: float | None = None
) -> LegMark:
    return LegMark("yes", p, bid, ask, started, settled)


def test_exit_rule_cases(cfg: C.PaperConfig) -> None:
    # nothing has tipped: the rule is not live yet
    assert exit_decision([lm(0.6, 0.62, False)], 10, cfg)[0] == "wait_pretip"
    # live leg, sold-at-bid 0.60 vs mid 0.61: within 1c -> exit
    d, fair, hold, est = exit_decision([lm(0.60, 0.62, True)], 10, cfg)
    assert d == "exit" and fair == pytest.approx(0.61) and est == pytest.approx(0.60)
    # wide market: bid 0.50 vs mid 0.61 -> hold
    assert exit_decision([lm(0.50, 0.72, True)], 10, cfg)[0] == "hold"
    # not-started leg is held at the shrunk model (0.5*0.9 + 0.5*mid); live leg at its mid
    marks = [lm(0.60, 0.62, True), lm(0.50, 0.52, False, p=0.9)]
    _, hold, _ = exit_decision(marks, 10, cfg)[1:]
    assert hold == pytest.approx(0.61 * (0.5 * 0.9 + 0.5 * 0.51))
    # settled legs count as 1 or 0; a dead position is not exited
    assert (
        exit_decision([lm(0.0, 0.0, True, settled=0.0), lm(0.5, 0.6, True)], 10, cfg)[0] == "hold"
    )
    assert exit_decision([lm(0, 0, True, settled=0.0)], 10, cfg)[0] == "dead"
    assert exit_decision([lm(0, 0, True, settled=1.0)], 10, cfg)[0] == "settling"
    assert exit_decision([lm(0.60, 0.62, True)], 10, cfg, sizes_ok=False)[0] == "size_blocked"
    fee, net = exit_proceeds(0.60, 10, cfg)
    assert fee == order_fee(0.60, cfg.fee, 10) and net == pytest.approx(6.0 - fee)


# ----------------------------------------------------------------------------- monitor + settle
def quote_for(side: str, bid: float, ask: float, size: float = 1e6) -> dict[str, Any]:
    yb, ya = (bid, ask) if side == "yes" else (1 - ask, 1 - bid)
    return {
        "yes_bid_dollars": f"{yb:.4f}", "yes_ask_dollars": f"{ya:.4f}",
        "no_bid_dollars": f"{1 - ya:.4f}", "no_ask_dollars": f"{1 - yb:.4f}",
        "yes_bid_size_fp": str(size), "yes_ask_size_fp": str(size), "result": "",
    }  # fmt: skip


def held_sides(path: Path) -> dict[str, str]:
    with db.reader(path) as con:
        return dict(
            con.execute(
                "SELECT l.ticker, l.side FROM position_legs l JOIN positions p USING (position_id) "
                "WHERE p.role = 'strategy'"
            ).fetchall()
        )


def test_monitor_waits_pretip_then_exits_at_the_real_bids(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    scan(cfg, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    good = lambda t: quote_for(sides[t], 0.90, 0.91)  # noqa: E731
    c0 = monitor_once(DAY, NOW, cfg, good, path, tmp_path / "legs.json")
    assert c0["exits"] == 0  # nothing tipped yet
    after_tip = TIP_EARLY + timedelta(minutes=30)
    c1 = monitor_once(DAY, after_tip, cfg, good, path, tmp_path / "legs.json")
    assert c1["exits"] >= 1
    with db.reader(path) as con:
        ex = con.execute("SELECT exit_price, exit_fee, n_contracts, made_at FROM exits").fetchall()
        marks = con.execute("SELECT decision FROM marks").fetchall()
    assert ex and all(
        abs(e[0] - 0.90) < 1e-9 or abs(e[0] - 0.81) < 1e-9 for e in ex
    )  # product of bids
    assert ("wait_pretip",) in marks
    # an exited position is not marked again
    c2 = monitor_once(
        DAY, after_tip + timedelta(minutes=1), cfg, good, path, tmp_path / "legs.json"
    )
    assert c2["positions"] < c1["positions"]


def test_monitor_does_not_exit_into_thin_bids(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scan(cfg, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    thin = lambda t: quote_for(sides[t], 0.90, 0.91, size=1.0)  # noqa: E731
    c = monitor_once(
        DAY, TIP_EARLY + timedelta(minutes=30), cfg, thin, path, tmp_path / "legs.json"
    )
    assert c["exits"] == 0
    with db.reader(path) as con:
        assert ("size_blocked",) in con.execute("SELECT decision FROM marks").fetchall()


def test_settle_pnl_after_fees_for_exit_hold_and_control(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    scan(cfg, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    good = lambda t: quote_for(sides[t], 0.90, 0.91)  # noqa: E731
    monitor_once(DAY, TIP_EARLY + timedelta(minutes=30), cfg, good, path, tmp_path / "legs.json")

    def result(ticker: str) -> dict[str, Any]:  # every market resolves NO
        return {"result": "no", "status": "finalized"}

    counts = settle_positions(DAY, NOW + timedelta(days=1), cfg, result, path)
    assert counts["settled"] == 4 and counts["void"] == 0
    with db.reader(path) as con:
        rows = con.execute(
            "SELECT p.role, p.slot, p.n_contracts, p.cost, r.payout_per_contract, r.pnl_hold, "
            "r.pnl_exit_rule, r.exited FROM results r JOIN positions p USING (position_id)"
        ).fetchall()
    for role, _slot, n, cost, payout, pnl_hold, pnl_exit, exited in rows:
        assert pnl_hold == pytest.approx(n * payout - cost)
        if role == "control":
            assert not exited and pnl_exit == pytest.approx(pnl_hold)
        if role == "strategy" and exited:
            assert pnl_exit != pytest.approx(pnl_hold)  # the exit cashed at bids, not the payout
    sing = next(r for r in rows if r[0] == "strategy" and r[1] == "single")
    # single held side is NO; all markets resolved NO -> the single hit and pays 1
    assert sides and sing[4] == 1.0


def test_settle_voids_a_leg_with_no_stated_value_and_waits_for_open_markets(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    scan(cfg, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    waiting = settle_positions(DAY, NOW, cfg, lambda t: {"result": "", "status": "active"}, path)
    assert waiting["waiting"] == 4 and waiting["settled"] == 0
    void = settle_positions(DAY, NOW, cfg, lambda t: {"result": "", "status": "finalized"}, path)
    assert void["void"] == 4
    s = summarize(path, cfg)
    assert (
        s["n_void"] == 4 and s["a_exit_rule"]["n_positions"] == 0
    )  # voids are counted, not scored


def test_summary_says_insufficient_sample_and_clusters_by_date(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    scan(cfg, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    settle_positions(DAY, NOW, cfg, lambda t: {"result": "no", "status": "finalized"}, path)
    s = summarize(path, cfg)
    assert s["a_exit_rule"]["verdict"] == "insufficient_sample"
    assert s["a_exit_rule"]["n_positions"] == 2 and s["a_exit_rule"]["n_slate_dates"] == 1
    r = cluster_ratio(
        ["d1", "d1", "d2", "d3"], [1.0, -2.0, 3.0, 0.5], [8.0] * 4,
        reps=500, seed=1, min_dates=30, min_positions=150,
    )  # fmt: skip
    assert r.n_dates == 3 and not r.sufficient and r.ci is not None
    assert r.per_dollar == pytest.approx(2.5 / 32.0)


def test_control_leg_and_flip_algebra(cfg: C.PaperConfig) -> None:
    leg = _any_plan(cfg).legs[0]
    f = flipped(leg)
    assert f.ask == pytest.approx(1 - leg.bid) and f.bid == pytest.approx(1 - leg.ask)
    assert f.p_model == pytest.approx(1 - leg.p_model) and flipped(f).side == leg.side
    assert control_leg(leg, f).mid >= 0.5


def test_store_is_append_only_and_never_nba_duckdb(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nba.duckdb"), db.writer(tmp_path / "nba.duckdb"):
        pass
    src = "".join(p.read_text() for p in Path(C.__file__).parent.glob("*.py")).upper()
    assert "UPDATE " not in src.replace("UPDATE_", "") and "DELETE " not in src
