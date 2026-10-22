"""Paper trader v2 on recorded Kalshi fixtures: scan math, placebo, order-book entry, refusals,
exit rule, settle P&L, looks, scope filter, digest refusal."""

from __future__ import annotations

import copy
import json
import random
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.papertrade import config as C
from nba.papertrade import db, digest
from nba.papertrade.forecasts import Forecasts, PropForecast, WinForecast, load_forecasts
from nba.papertrade.monitor import monitor_once
from nba.papertrade.rules import (
    EntryRefused,
    LegMark,
    Plan,
    build_plan,
    draw_placebo,
    exit_decision,
    exit_proceeds,
    flipped,
    plan_entry,
    screen_leg,
    shrunk,
)
from nba.papertrade.scan import NO_EV, REFUSED_DIGEST, check_entry, legs_from_markets, run_scan
from nba.papertrade.settle import clv_rows, look_dates, settle_positions, summarize
from nba.papertrade.stats import cluster_ratio, concentration
from nba.parlay.ev import order_fee

FIX = Path(__file__).parents[1] / "live" / "fixtures" / "kalshi_markets_pages.json"
DAY = date(2026, 10, 21)
TIP_LATE = datetime(2026, 10, 22, 2, 0)  # GSW @ LAL
TIP_EARLY = datetime(2026, 10, 21, 23, 30)  # CHA @ BKN
NOW = datetime(2026, 10, 21, 20, 0)
ALIASES = {"stephen curry": 201939, "luka doncic": 1629029, "grayson allen": 1627}


@pytest.fixture
def cfg() -> C.PaperConfig:
    return C.load().with_rule_version("paper-trader-v2-shakedown")


def markets() -> list[dict[str, Any]]:
    ms = json.loads(FIX.read_text())["markets"]
    allen = copy.deepcopy(next(m for m in ms if m["ticker"].endswith("LALLDONCIC77-30")))
    allen["ticker"] = "KXNBAPTS-26OCT21CHABKN-CHAGALLEN8-30"
    allen["event_ticker"] = "KXNBAPTS-26OCT21CHABKN"
    allen["title"] = "Grayson Allen: 30+ points"
    return [*ms, allen]


def prop(
    pid: int, game: str, tip: datetime, p: dict[int, float], p_play: float = 0.97,
    made: datetime | None = None, version: str = "ctxres-v1",
) -> PropForecast:  # fmt: skip
    counts = [50] * 60
    for k, v in p.items():
        counts[k - 1] = round(100 * v)
    made = made or NOW - timedelta(hours=2)
    return PropForecast(game, tip, made, "pts", pid, tuple(counts), 100, p_play, version)


def forecasts(curry: float = 0.80, allen: float = 0.85, doncic: float = 0.30) -> Forecasts:
    fc = Forecasts()
    fc.props[(201939, "pts")] = prop(201939, "G1", TIP_LATE, {25: curry, 30: 0.4, 35: 0.2, 20: 0.9})
    fc.props[(1629029, "pts")] = prop(1629029, "G1", TIP_LATE, {30: doncic, 35: 0.2, 40: 0.1})
    fc.props[(1627, "pts")] = prop(1627, "G2", TIP_EARLY, {30: allen})
    fc.wins[("GSW", "LAL")] = WinForecast("G1", TIP_LATE, NOW, "GSW", "LAL", 0.8)
    return fc


def scan(cfg: C.PaperConfig, tmp: Path, fc: Forecasts, now: datetime = NOW, **kw: Any) -> Any:
    tmp.mkdir(parents=True, exist_ok=True)
    return run_scan(
        DAY, now, cfg, markets=markets(), fc=fc, aliases=ALIASES,
        db_path=tmp / "pt.duckdb", legs_file=tmp / "legs.json", **kw,
    )  # fmt: skip


def q(con: Any, sql: str) -> list[tuple[Any, ...]]:
    return list(con.execute(sql).fetchall())


# ----------------------------------------------------------------------------- fees and screen
def test_fee_comes_from_the_verified_config_object(cfg: C.PaperConfig) -> None:
    n, fee = plan_entry(0.50, cfg.fee, 8.0)
    assert fee == order_fee(0.50, cfg.fee, n)
    assert n * 0.5 + fee <= 8.0 < (n + 1) * 0.5 + order_fee(0.50, cfg.fee, n + 1)
    n0, fee0 = plan_entry(0.50, replace(cfg.fee, coefficient=0.0), 8.0)
    assert fee0 == 0.0 and n0 == 16


def test_series_multiplier_reaches_the_fee(cfg: C.PaperConfig) -> None:
    mult = replace(cfg.fee, multipliers=(("KXNBAPTS", 0.5),))
    _, base = plan_entry(0.50, cfg.fee, 8.0, "KXNBAPTS")
    _, half = plan_entry(0.50, mult, 8.0, "KXNBAPTS")
    assert half < base  # the series is passed through (N2)


def test_unverified_fee_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "parlay.yaml"
    bad.write_text(Path(C.PARLAY_PATH).read_text().replace("verified: true", "verified: false", 1))
    with pytest.raises(ValueError, match="verified"):
        C.load(parlay_path=bad)


def test_shrink_is_025_and_screen_reasons(cfg: C.PaperConfig, tmp_path: Path) -> None:
    assert cfg.shrink == 0.25 and shrunk(0.8, 0.5, 0.25) == pytest.approx(0.575)
    scan(cfg, tmp_path, forecasts())
    with db.reader(tmp_path / "pt.duckdb") as con:
        why = dict(q(con, "SELECT reason, count(*) FROM candidates GROUP BY 1"))
        p_model, p_shr, bid, ask = q(
            con,
            "SELECT p_model, p_shrunk, bid, ask FROM candidates WHERE ticker LIKE '%CURRY30-25'",
        )[0]
    assert why["spread_over_cap"] >= 2 and why["leg_type_disabled"] == 4
    assert p_shr == pytest.approx(0.25 * p_model + 0.75 * (bid + ask) / 2)


def test_stale_and_unfrozen_forecasts_are_skipped(cfg: C.PaperConfig) -> None:
    fc = forecasts()
    stale = NOW - timedelta(hours=13)
    fc.props[(201939, "pts")] = prop(201939, "G1", TIP_LATE, {25: 0.8}, made=stale)
    legs, _, _ = legs_from_markets(markets(), fc, cfg, ALIASES)
    curry = next(x for x in legs if "CURRY30-25" in x.ticker and x.side == "yes")
    assert screen_leg(curry, cfg, now=NOW)[1] == "forecast_stale"
    fc.props[(201939, "pts")] = prop(201939, "G1", TIP_LATE, {25: 0.8}, version="ctxres-v9")
    legs, _, _ = legs_from_markets(markets(), fc, cfg, ALIASES)
    curry = next(x for x in legs if "CURRY30-25" in x.ticker and x.side == "yes")
    assert screen_leg(curry, cfg, now=NOW)[1] == "forecast_version_not_frozen"


def test_threshold_must_agree_between_strike_type_and_title(cfg: C.PaperConfig) -> None:
    ms = markets()
    for m in ms:
        if m["ticker"].endswith("CURRY30-25"):
            m["strike_type"] = "greater_or_equal"
        if m["ticker"].endswith("CURRY30-20"):
            m["title"] = "Stephen Curry: 21+ points"
    legs, _, skipped = legs_from_markets(ms, forecasts(), cfg, ALIASES)
    assert skipped["threshold_mismatch"] == 2
    assert not any("CURRY30-25" in x.ticker or "CURRY30-20" in x.ticker for x in legs)


# ----------------------------------------------------------------------------- scan outcomes
def test_scan_opens_three_distinct_singles_with_placebos(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    res = scan(cfg, tmp_path, forecasts())
    assert res.verdict == "positions_opened" and len(res.positions) == 3
    with db.reader(tmp_path / "pt.duckdb") as con:
        strat = q(
            con,
            "SELECT slot, n_legs, cost, synthetic, rule_version FROM positions "
            "WHERE role = 'strategy'",
        )
        fams = q(
            con,
            "SELECT l.game_id, l.player_id, l.stat FROM position_legs l JOIN positions p USING "
            "(position_id) WHERE p.role = 'strategy'",
        )
        plc = q(con, "SELECT count(*) FROM positions WHERE role = 'placebo'")
    assert {r[0] for r in strat} == {"single"} and all(r[1] == 1 and not r[3] for r in strat)
    assert all(r[2] <= cfg.stake_usd + 1e-9 for r in strat)  # fixed paper stake never exceeded
    assert len(set(fams)) == 3  # no two positions on the same player-stat family
    assert plc == [(3,)]
    assert {r[4] for r in strat} == {"paper-trader-v2-shakedown"}
    assert set(json.loads((tmp_path / "legs.json").read_text())["tickers"])


def test_parlays_are_synthetic_descriptive_rows(cfg: C.PaperConfig, tmp_path: Path) -> None:
    res = scan(replace(cfg, slots=("parlay2",)), tmp_path, forecasts())
    assert len(res.positions) == 1
    leg_sql = (
        "SELECT l.{c} FROM position_legs l JOIN positions p USING (position_id) "
        "WHERE p.role = 'strategy'"
    )
    with db.reader(tmp_path / "pt.duckdb") as con:
        pos = q(
            con,
            "SELECT synthetic, entry_label, n_legs, entry_price FROM positions "
            "WHERE role = 'strategy'",
        )[0]
        asks = [r[0] for r in q(con, leg_sql.format(c="ask"))]
        tips = {r[0] for r in q(con, leg_sql.format(c="tipoff"))}
    assert pos[0] is True and "SYNTHETIC" in pos[1] and pos[2] == 2
    assert pos[3] == pytest.approx(asks[0] * asks[1]) and len(tips) == 2


def test_placebo_is_seeded_and_ignores_the_model(cfg: C.PaperConfig) -> None:
    legs, _, _ = legs_from_markets(markets(), forecasts(), cfg, ALIASES)
    universe = [x for x in legs if screen_leg(x, cfg, now=NOW, check_ev=False)[0]]
    assert len(universe) > len([x for x in legs if screen_leg(x, cfg, now=NOW)[0]])  # no EV floor
    plan = build_plan("single", (universe[0],), cfg)
    a = draw_placebo(plan, universe, random.Random(7), cfg)
    b = draw_placebo(plan, universe, random.Random(7), cfg)
    assert a is not None and b is not None
    assert a.legs[0].ticker == b.legs[0].ticker  # same seed, same pick
    legs2, _, _ = legs_from_markets(markets(), forecasts(curry=0.1, allen=0.1), cfg, ALIASES)
    uni2 = [x for x in legs2 if screen_leg(x, cfg, now=NOW, check_ev=False)[0]]
    assert [x.ticker for x in uni2] == [x.ticker for x in universe]  # model-free universe


def test_no_positive_ev_found_is_a_verdict(cfg: C.PaperConfig, tmp_path: Path) -> None:
    flat = Forecasts()
    pids = {
        "SCURRY30": (201939, "G1", TIP_LATE),
        "LDONCIC77": (1629029, "G1", TIP_LATE),
        "GALLEN8": (1627, "G2", TIP_EARLY),
    }
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
        assert q(con, "SELECT verdict FROM scans") == [(NO_EV,)]


def test_second_scan_does_not_double_fill(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scan(cfg, tmp_path, forecasts())
    again = scan(cfg, tmp_path, forecasts())
    assert again.positions == []
    with db.reader(tmp_path / "pt.duckdb") as con:
        assert q(con, "SELECT count(*) FROM positions WHERE role = 'strategy'") == [(3,)]


def _any_plan(cfg: C.PaperConfig) -> Plan:
    legs, _, _ = legs_from_markets(markets(), forecasts(), cfg, ALIASES)
    ok = [x for x in legs if screen_leg(x, cfg, now=NOW)[0]]
    return build_plan("single", (ok[0],), cfg)


def test_entry_refused_at_or_after_tip(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scan(cfg, tmp_path, forecasts(), now=TIP_EARLY)  # Allen's game has tipped
    with db.reader(tmp_path / "pt.duckdb") as con:
        legs = q(con, "SELECT ticker FROM position_legs")
    assert not any("CHABKN" in t[0] for t in legs)
    plan = _any_plan(cfg)
    with pytest.raises(EntryRefused):
        check_entry(plan, plan.legs[0].tipoff, cfg)
    with pytest.raises(EntryRefused):
        check_entry(plan, plan.legs[0].tipoff - timedelta(minutes=cfg.entry_lead_min - 1), cfg)
    check_entry(plan, plan.legs[0].tipoff - timedelta(hours=1), cfg)


def test_forecast_loader_ignores_rows_made_at_or_after_tip() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, "
        "tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, "
        "prediction JSON)"
    )
    body = json.dumps({"p_ge_full": {"c": [90, 80], "n": 100}, "p_play": 0.95})
    for made in (datetime(2026, 10, 21, 20, 0), datetime(2026, 10, 22, 2, 5)):
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r', ?, 'G1', ?, 'props_context_residual', "
            "'ctxres-v1', 'pts', 5, ?)",
            [made, TIP_LATE, body],
        )
    fc = load_forecasts(con, DAY, datetime(2026, 10, 22, 3, 0))
    f = fc.props[(5, "pts")]
    assert f.made_at == datetime(2026, 10, 21, 20, 0) and f.version == "ctxres-v1"
    assert f.p_ge(2) == 0.8 and f.p_ge(3) is None


# ----------------------------------------------------------------------------- order-book entry
def book(no_bid: float, yes_bid: float = 0.48) -> dict[str, Any]:
    return {
        "orderbook_fp": {
            "yes_dollars": [[f"{yes_bid:.4f}", "5000.00"]],
            "no_dollars": [[f"{no_bid:.4f}", "5000.00"]],
        }
    }


def test_entry_is_priced_from_the_book_and_refused_when_worse(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    one = replace(cfg, slots=("single",))
    # a YES ask of 1 - no_bid; the held side may be NO, so give both sides a tight book
    tight = lambda t: book(0.52, 0.47)  # noqa: E731
    res = scan(one, tmp_path, forecasts(), book_fn=tight)
    with db.reader(tmp_path / "pt.duckdb") as con:
        src = q(con, "SELECT entry_source, book_ts FROM position_legs")
    assert res.positions and src[0] == ("book", NOW)
    # every book is much worse than the listing ask: all refused, nothing entered
    far = lambda t: book(0.05, 0.04)  # noqa: E731
    res2 = scan(one, tmp_path / "w", forecasts(), book_fn=far)
    assert res2.positions == [] and res2.notes["book_refusals"]


# ----------------------------------------------------------------------------- digest refusal
def test_scored_version_refuses_without_digest_match(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scored = cfg.with_rule_version("paper-trader-v2")
    res = scan(scored, tmp_path, forecasts(), digest_status=(False, "changed since freeze: x"))
    assert res.verdict == REFUSED_DIGEST and res.positions == []
    shake = scan(cfg, tmp_path / "s", forecasts(), digest_status=(False, "x"))
    assert shake.positions  # a shakedown never needs the digest
    with db.reader(tmp_path / "pt.duckdb") as con:
        assert q(con, "SELECT verdict, digest_ok FROM scans") == [(REFUSED_DIGEST, False)]


def test_digest_detects_any_covered_change(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    files = {
        "docs/prereg/PAPER_TRADER.md": "rule",
        "configs/papertrade.yaml": "a: 1",
        "configs/kalshi_aliases.yaml": "aliases: {}",
        "nba/live/kalshi_pull.py": "x = 1",
        "nba/live/http.py": "y = 1",
        "nba/papertrade/rules.py": "z = 1",
        "configs/parlay.yaml": "fee_model: {coefficient: 0.07}\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    freeze = tmp_path / "freeze.json"
    assert digest.check(freeze, root)[0] is False  # no freeze record
    digest.write_freeze("paper-trader-v2", "2026-10-23", freeze, root)
    assert digest.check(freeze, root) == (True, "ok")
    for rel in files:
        if rel == "configs/parlay.yaml":
            continue
        p = root / rel
        old = p.read_text()
        p.write_text(old + " # edited")
        ok, why = digest.check(freeze, root)
        assert not ok and "changed since freeze" in why
        p.write_text(old)
    (root / "configs/parlay.yaml").write_text("fee_model: {coefficient: 0.08}\n")
    assert not digest.check(freeze, root)[0]  # the fee block is covered


# ----------------------------------------------------------------------------- exit rule
def lm(
    bid: float, ask: float, started: bool, p: float = 0.7, settled: float | None = None
) -> LegMark:
    return LegMark("yes", p, bid, ask, started, settled)


def test_exit_rule_is_net_of_fee_with_a_relative_margin(cfg: C.PaperConfig) -> None:
    assert exit_decision([lm(0.6, 0.62, False)], 10, cfg)[0] == "wait_pretip"
    # a live single: bid <= mid = hold, so the rule does NOT fire at a normal spread
    assert exit_decision([lm(0.60, 0.62, True)], 10, cfg)[0] == "hold"
    assert exit_decision([lm(0.60, 0.61, True)], 10, cfg)[0] == "hold"  # even 1c wide
    # an unstarted leg valued low by the shrunk model: bids beat hold by > 5% after the fee
    marks = [lm(0.95, 0.96, True), lm(0.80, 0.82, False, p=0.20)]
    d, _fair, hold, est = exit_decision(marks, 100, cfg)
    assert d == "exit" and est == pytest.approx(0.95 * 0.80)
    assert est - order_fee(est, cfg.fee, 100) / 100 >= hold * (1 + cfg.exit_margin)
    assert exit_decision(marks, 100, cfg, sizes_ok=False)[0] == "size_blocked"
    assert exit_decision(marks, 100, replace(cfg, exit_margin=0.30))[0] == "hold"
    assert exit_decision([lm(0, 0, True, settled=0.0)], 10, cfg)[0] == "dead"
    assert exit_decision([lm(0, 0, True, settled=1.0)], 10, cfg)[0] == "settling"
    fee2, net = exit_proceeds(0.60, 10, cfg)
    assert fee2 == order_fee(0.60, cfg.fee, 10) and net == pytest.approx(6.0 - fee2)


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
        return dict(q(con, "SELECT ticker, side FROM position_legs"))


def test_monitor_marks_both_roles_waits_pretip_and_exits_at_real_bids(
    cfg: C.PaperConfig, tmp_path: Path
) -> None:
    cfg2 = replace(cfg, exit_margin=-0.20)  # forces the exit path; the arithmetic is tested above
    scan(cfg2, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    legs_file = tmp_path / "legs.json"
    good = lambda t: quote_for(sides[t], 0.90, 0.91)  # noqa: E731
    c0 = monitor_once(DAY, NOW, cfg2, good, path, legs_file)
    assert c0["exits"] == 0 and c0["positions"] == 6  # strategy and placebo are both marked
    after = TIP_EARLY + timedelta(minutes=30)
    c1 = monitor_once(DAY, after, cfg2, good, path, legs_file)
    assert c1["exits"] >= 1
    with db.reader(path) as con:
        ex = q(con, "SELECT e.exit_price, p.role FROM exits e JOIN positions p USING (position_id)")
        marks = q(con, "SELECT decision FROM marks")
    assert ex and all(r[1] == "strategy" and abs(r[0] - 0.90) < 1e-9 for r in ex)
    assert ("wait_pretip",) in marks
    c2 = monitor_once(DAY, after + timedelta(minutes=1), cfg2, good, path, legs_file)
    assert c2["positions"] < c1["positions"]  # exited positions are not marked again


def test_monitor_does_not_exit_into_thin_bids(cfg: C.PaperConfig, tmp_path: Path) -> None:
    cfg2 = replace(cfg, exit_margin=-0.20)
    scan(cfg2, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    thin = lambda t: quote_for(sides[t], 0.90, 0.91, size=1.0)  # noqa: E731
    c = monitor_once(
        DAY, TIP_EARLY + timedelta(minutes=30), cfg2, thin, path, tmp_path / "legs.json"
    )
    assert c["exits"] == 0
    with db.reader(path) as con:
        assert q(con, "SELECT count(*) FROM marks WHERE decision = 'size_blocked'")[0][0] >= 1


def test_settle_pnl_after_fees_and_voids(cfg: C.PaperConfig, tmp_path: Path) -> None:
    cfg2 = replace(cfg, exit_margin=-0.20)
    scan(cfg2, tmp_path, forecasts())
    path = tmp_path / "pt.duckdb"
    sides = held_sides(path)
    after = TIP_EARLY + timedelta(minutes=30)
    monitor_once(
        DAY, after, cfg2, lambda t: quote_for(sides[t], 0.90, 0.91), path, tmp_path / "legs.json"
    )
    wait = settle_positions(DAY, NOW, cfg2, lambda t: {"result": "", "status": "active"}, path)
    assert wait["waiting"] == 6
    resolved = lambda t: {"result": "no", "status": "finalized"}  # noqa: E731
    counts = settle_positions(DAY, NOW + timedelta(days=1), cfg2, resolved, path)
    assert counts["settled"] == 6 and counts["void"] == 0
    with db.reader(path) as con:
        rows = q(
            con,
            "SELECT p.role, p.n_contracts, p.cost, r.payout_per_contract, r.pnl_hold, "
            "r.pnl_exit_rule, r.exited FROM results r JOIN positions p USING (position_id)",
        )
    for role, n, cost, payout, pnl_hold, pnl_exit, exited in rows:
        assert pnl_hold == pytest.approx(n * payout - cost)
        if role == "placebo":
            assert not exited and pnl_exit == pytest.approx(pnl_hold)
        elif exited:
            assert pnl_exit != pytest.approx(pnl_hold)
    scan(cfg, tmp_path / "v", forecasts())
    void = settle_positions(
        DAY, NOW, cfg, lambda t: {"result": "", "status": "finalized"}, tmp_path / "v" / "pt.duckdb"
    )
    assert void["void"] == 6


# ----------------------------------------------------------------------------- looks and scope
def put_result(
    con: Any, i: int, d: str, pnl: float, *, rule: str = "paper-trader-v2",
    synthetic: bool = False, role: str = "strategy", twin: str | None = None,
) -> str:  # fmt: skip
    pid = f"p{i}{role}"
    con.execute(
        "INSERT INTO positions (position_id, scan_id, slate_date, made_at, role, slot, twin_id, "
        "n_legs, n_contracts, cost, rule_version, synthetic, first_tip) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            pid,
            "s",
            d,
            NOW,
            role,
            "single",
            twin,
            1,
            10,
            8.0,
            rule,
            synthetic,
            datetime(2026, 12, 1),
        ],
    )
    con.execute(
        "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
        [pid, NOW, d, role, "settled", 8.0, 0.0, pnl, pnl, False],
    )
    return pid


def seed(path: Path) -> None:
    with db.writer(path) as con:
        i = 0
        for day in range(3):
            d = (date(2026, 11, 1) + timedelta(days=day)).isoformat()
            for k in range(2):
                i += 1
                sid = put_result(con, i, d, 1.0 + 0.1 * k - 0.4 * day)
                put_result(con, i, d, -0.2, role="placebo", twin=sid)
        put_result(con, 900, "2026-10-01", 50.0)  # before the freeze date
        put_result(con, 901, "2026-11-01", 50.0, rule="paper-trader-v0")  # another rule version
        put_result(con, 902, "2026-11-01", 50.0, synthetic=True)  # parlay: descriptive only


def test_summary_scope_filter_and_no_ci_off_look(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scored = cfg.with_rule_version("paper-trader-v2")
    path = tmp_path / "pt.duckdb"
    seed(path)
    s = summarize(path, scored)
    cur = s["current_not_a_look"]
    assert cur["a_exit_rule"]["n_positions"] == 6  # pre-freeze, v0 and synthetic rows excluded
    assert "ci" not in cur["a_exit_rule"] and cur["status"] == "not_a_look"
    assert s["looks"]["L1"]["status"] == "not_reached"
    assert s["looks"]["L2"]["status"] == "not_reached"
    assert s["synthetic_parlays_descriptive"]["n_positions"] == 1
    assert "ci" not in s["synthetic_parlays_descriptive"]
    assert summarize(path, cfg)["verdict"] == "unscored_shakedown"


def test_ci_exists_only_at_a_named_look_with_its_alpha(cfg: C.PaperConfig, tmp_path: Path) -> None:
    scored = replace(
        cfg.with_rule_version("paper-trader-v2"),
        look1_min_positions=4, look1_min_dates=2, look2_dates=3, bootstrap_reps=300,
    )  # fmt: skip
    path = tmp_path / "pt.duckdb"
    seed(path)
    s = summarize(path, scored)
    l1, l2 = s["looks"]["L1"], s["looks"]["L2"]
    assert l1["status"] == "taken" and l1["data_through"] == "2026-11-02"
    assert l1["alpha"] == scored.alpha_l1
    assert l1["a_exit_rule"]["n_positions"] == 4 and l1["a_exit_rule"]["ci"] is not None
    assert l2["data_through"] == "2026-11-03" and l2["alpha"] == scored.alpha_l2
    assert l2["a_exit_rule"]["n_positions"] == 6
    assert l1["a_minus_placebo_stake_matched"]["n_pairs"] == 4
    assert set(l1["live_money_conditions"]) == {
        "a_ci_lower_above_zero",
        "a_minus_placebo_ci_lower_above_zero",
        "concentration_ok",
    }
    assert l1["a_minus_b"]["alpha"] == scored.alpha_l1 / 2  # secondaries use half the look alpha


def test_look_dates_and_alpha_split(cfg: C.PaperConfig) -> None:
    per_date = [(f"2026-11-{i:02d}", 3) for i in range(1, 29)]
    per_date += [(f"2026-12-{i:02d}", 3) for i in range(1, 29)]
    got = look_dates(per_date, cfg, "")
    assert (
        got["L1"] == "2026-12-22"
    )  # the 50th date gives 150 positions; 30 dates alone is not enough
    assert got["L2"] is None  # fewer than 100 dates and before the season end
    assert cfg.alpha_l1 + cfg.alpha_l2 == pytest.approx(0.05)


def test_concentration_is_share_of_total_absolute_pnl() -> None:
    assert concentration(["a", "b", "b"], [10.0, -5.0, -5.0]) == pytest.approx(0.5)
    assert concentration(["a", "b"], [0.0, 0.0]) is None


def test_cluster_ratio_alpha_and_dates() -> None:
    d = ["d1", "d1", "d2", "d3", "d4"]
    p = [1.0, -2.0, 3.0, 0.5, -1.0]
    wide = cluster_ratio(d, p, [8.0] * 5, alpha=0.05, reps=800, seed=1)
    wider = cluster_ratio(d, p, [8.0] * 5, alpha=0.005, reps=800, seed=1)
    assert wide.n_dates == 4 and wide.ci and wider.ci
    assert wider.ci[0] <= wide.ci[0] and wider.ci[1] >= wide.ci[1]
    assert wide.per_dollar == pytest.approx(1.5 / 40.0)


def test_flip_algebra(cfg: C.PaperConfig) -> None:
    leg = _any_plan(cfg).legs[0]
    f = flipped(leg)
    assert f.ask == pytest.approx(1 - leg.bid) and f.bid == pytest.approx(1 - leg.ask)
    assert f.p_model == pytest.approx(1 - leg.p_model) and flipped(f).side == leg.side
    assert f.forecast_made_at == leg.forecast_made_at


def test_store_is_append_only_and_never_nba_duckdb(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nba.duckdb"), db.writer(tmp_path / "nba.duckdb"):
        pass
    src = "".join(p.read_text() for p in Path(C.__file__).parent.glob("*.py")).upper()
    assert "UPDATE " not in src and "DELETE " not in src


def test_clv_is_last_pre_tip_mid_minus_entry_ask(tmp_path: Path) -> None:
    path = tmp_path / "pt.duckdb"
    tip = datetime(2026, 11, 1, 23, 0)
    with db.writer(path) as con:
        pid = put_result(con, 1, "2026-11-01", 0.0)
        con.execute(
            "INSERT INTO position_legs (position_id, leg_idx, ticker, ask, tipoff) "
            "VALUES (?, 0, 'T', 0.40, ?)",
            [pid, tip],
        )
        for minute, (bid, ask) in {0: (0.40, 0.44), 30: (0.46, 0.50), 90: (0.70, 0.72)}.items():
            con.execute(
                "INSERT INTO marks (position_id, made_at, detail_json) VALUES (?, ?, ?)",
                [pid, tip - timedelta(minutes=120 - minute), json.dumps([[bid, ask, None]])],
            )  # the 90-minute mark is 30 minutes before tip: the last pre-tip mark
        con.execute(
            "INSERT INTO marks (position_id, made_at, detail_json) VALUES (?, ?, ?)",
            [pid, tip + timedelta(minutes=5), json.dumps([[0.99, 0.99, None]])],
        )  # after tip: must not be used (never judged with later information)
        got = clv_rows(con, [pid], {pid: "2026-11-01"})
    assert got == [("2026-11-01", pytest.approx(0.71 - 0.40))]
