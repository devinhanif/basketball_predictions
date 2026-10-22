"""Playbook arm on synthetic fixtures: ladder selection, window and started-game rules, skip
reasons, exit policy, monitor/settle/report, isolation from the primary summary."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.live.capture import Capture
from nba.papertrade import config as C
from nba.papertrade import db
from nba.papertrade.forecasts import Forecasts, PropForecast
from nba.papertrade.playbook import (
    PlaybookConfig,
    exit_net,
    load_playbook,
    playbook_cfg,
    playbook_decision,
    playbook_monitor_once,
    playbook_report,
    price_ladder,
    run_playbook_scan,
    select_ladder,
)
from nba.papertrade.rules import Leg, LegMark
from nba.papertrade.runner import scan_times
from nba.papertrade.scan import legs_from_markets
from nba.papertrade.settle import settle_positions, summarize

DAY = date(2026, 10, 21)
T1 = datetime(2026, 10, 21, 23, 30)
T2 = datetime(2026, 10, 22, 2, 0)
NOW = T1 - timedelta(minutes=45)
FORE_MADE = NOW - timedelta(hours=2)
#: (group, count, model p, bid, ask)
GROUPS = [("A", 6, 0.99, 0.89, 0.91), ("B", 6, 0.90, 0.65, 0.67), ("C", 8, 0.80, 0.54, 0.56)]


@pytest.fixture
def cfg() -> C.PaperConfig:
    return C.load().with_rule_version("paper-trader-v2-playbook")


@pytest.fixture
def pb() -> PlaybookConfig:
    return load_playbook()


def universe(
    flat: bool = False, tips: tuple[datetime, datetime] = (T1, T2)
) -> tuple[list[dict[str, Any]], Forecasts, dict[str, int]]:
    ms: list[dict[str, Any]] = []
    fc = Forecasts()
    aliases: dict[str, int] = {}
    pid = 1000
    for g, count, p_model, bid, ask in GROUPS:
        for i in range(count):
            pid += 1
            name = f"Player {g}{'abcdefgh'[i]}"
            first, last = name.split()
            game = "AAABBB" if pid % 2 else "CCCDDD"
            tip = tips[0] if pid % 2 else tips[1]
            ms.append(
                {
                    "ticker": f"KXNBAPTS-26OCT21{game}-{last.upper()}{pid}-20",
                    "event_ticker": f"KXNBAPTS-26OCT21{game}",
                    "title": f"{first} {last}: 20+ points",
                    "floor_strike": 19.5,
                    "strike_type": "greater",
                    "yes_bid_dollars": f"{bid:.4f}",
                    "yes_ask_dollars": f"{ask:.4f}",
                    "yes_bid_size_fp": "500.00",
                    "yes_ask_size_fp": "500.00",
                }
            )
            p = (bid + ask) / 2 if flat else p_model
            counts = [100] * 19 + [round(100 * p)] + [0] * 40
            fc.props[(pid, "pts")] = PropForecast(
                f"G{game}", tip, FORE_MADE, "pts", pid, tuple(counts), 100, 0.97, "ctxres-v1"
            )
            aliases[name.lower()] = pid
    return ms, fc, aliases


def scan(cfg: C.PaperConfig, pb: PlaybookConfig, tmp: Path, **kw: Any) -> Any:
    ms, fc, al = universe(flat=kw.pop("flat", False))
    return run_playbook_scan(
        DAY, kw.pop("now", NOW), cfg, pb, markets=ms, fc=fc, aliases=al,
        db_path=tmp / "pb.duckdb", legs_file=tmp / "legs_pb.json", **kw,
    )  # fmt: skip


def eligible(cfg: C.PaperConfig, pb: PlaybookConfig, now: datetime = NOW) -> list[Leg]:
    from nba.papertrade.rules import screen_leg

    c = playbook_cfg(cfg, pb)
    ms, fc, al = universe()
    legs, _, _ = legs_from_markets(ms, fc, c, al)
    return [x for x in legs if screen_leg(x, c, now=now, check_ev=False)[0]]


def test_config_tiers_match_devins_playbook(pb: PlaybookConfig) -> None:
    t = {x.name: x for x in pb.tiers}
    assert (t["safe"].min_legs, t["safe"].max_legs, t["safe"].min_leg_p) == (3, 3, 0.85)
    assert (t["safe"].joint_lo, t["safe"].max_leg_ask, t["safe"].stake_usd) == (0.55, 0.95, 6.0)
    assert (t["medium"].min_legs, t["medium"].max_legs) == (3, 4)
    assert (t["medium"].joint_lo, t["medium"].joint_hi, t["medium"].stake_usd) == (0.30, 0.45, 4.0)
    assert (t["risky"].min_legs, t["risky"].max_legs) == (5, 8)
    assert (t["risky"].joint_lo, t["risky"].joint_hi, t["risky"].stake_usd) == (0.08, 0.20, 2.0)
    assert pb.max_spread == 0.05 and pb.rule_version == "paper-trader-v2-playbook"


def test_each_tier_selected_inside_its_constraints(cfg: C.PaperConfig, pb: PlaybookConfig) -> None:
    c = playbook_cfg(cfg, pb)
    legs = eligible(cfg, pb)
    for tier in pb.tiers:
        lad, why = select_ladder(tier, legs, c, pb)
        assert lad is not None and why == "ok", (tier.name, why)
        assert tier.min_legs <= len(lad.legs) <= tier.max_legs
        assert tier.joint_lo <= lad.joint <= tier.joint_hi + 1e-9
        assert lad.ev > 0 and lad.n_games >= 2
        keys = [(x.player_id, x.stat) for x in lad.legs]
        assert len(set(keys)) == len(keys)
        assert all(x.spread <= 0.05 + 1e-9 for x in lad.legs)
        assert lad.cost <= tier.stake_usd + 1e-9
    safe, _ = select_ladder(pb.tiers[0], legs, c, pb)
    assert safe is not None and all(x.p_model >= 0.85 and x.ask <= 0.95 for x in safe.legs)


def test_scan_builds_ladders_and_logs_everything(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    res = scan(cfg, pb, tmp_path)
    assert sorted(res.built) == ["medium", "risky", "safe"] and res.window == T1
    with db.reader(tmp_path / "pb.duckdb") as con:
        rows = con.execute(
            "SELECT slot, role, rule_version, synthetic, cost, n_legs FROM positions ORDER BY slot"
        ).fetchall()
        verdict, notes = con.execute("SELECT verdict, notes_json FROM scans").fetchone()  # type: ignore[misc]
    assert {r[1] for r in rows} == {"playbook"} and {r[2] for r in rows} == {pb.rule_version}
    assert all(r[3] for r in rows)
    assert verdict == "playbook_ladders_built" and "tiers" in json.loads(notes)
    again = scan(cfg, pb, tmp_path)  # same window: nothing is added twice
    assert not again.built
    with db.reader(tmp_path / "pb.duckdb") as con:
        assert con.execute("SELECT count(*) FROM positions").fetchone()[0] == 3  # type: ignore[index]
    assert json.loads((tmp_path / "legs_pb.json").read_text())["tickers"]


def test_started_games_are_never_used(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    late = T1 + timedelta(minutes=30)  # game 1 has started; only the T2 game remains
    res = scan(cfg, pb, tmp_path, now=late)
    assert res.window == T2
    with db.reader(tmp_path / "pb.duckdb") as con:
        tips = {r[0] for r in con.execute("SELECT tipoff FROM position_legs").fetchall()}
    assert tips == {T2}


def test_no_edge_is_a_logged_skip_not_a_ladder(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    res = scan(cfg, pb, tmp_path, flat=True)  # model == market: fees and spread make EV negative
    assert not res.built and set(res.skipped.values()) <= {
        "no_positive_ev", "no_combo_in_joint_range", "no_eligible_legs"
    }  # fmt: skip
    with db.reader(tmp_path / "pb.duckdb") as con:
        v = con.execute("SELECT verdict FROM scans").fetchone()[0]  # type: ignore[index]
    assert v == "no_positive_ev_found"
    rep = playbook_report(tmp_path / "pb.duckdb", pb.rule_version)
    assert rep["nights"][str(DAY)]["skipped"]["safe"] and not rep["nights"][str(DAY)]["built"]


def test_book_refusal_retries_without_the_bad_leg(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    ms, _, _ = universe()
    by = {m["ticker"]: m for m in ms}
    seen: list[str] = []

    def book(ticker: str) -> dict[str, Any] | None:
        seen.append(ticker)
        if "-AA" in ticker:  # one-sided book: refused, the ladder is rebuilt without it
            return {"orderbook_fp": {"yes_dollars": [["0.5000", "9999.00"]], "no_dollars": []}}
        yb, ya = float(by[ticker]["yes_bid_dollars"]), float(by[ticker]["yes_ask_dollars"])
        return {
            "orderbook_fp": {
                "yes_dollars": [[f"{yb:.4f}", "9999.00"]],
                "no_dollars": [[f"{1 - ya:.4f}", "9999.00"]],
            }
        }

    res = scan(cfg, pb, tmp_path, book_fn=book)
    assert "safe" in res.built
    with db.reader(tmp_path / "pb.duckdb") as con:
        tickers = [r[0] for r in con.execute("SELECT ticker FROM position_legs").fetchall()]
        src = {r[0] for r in con.execute("SELECT entry_source FROM position_legs").fetchall()}
    assert not any("-AA" in t for t in tickers) and src == {"book"}


def test_everything_refused_is_logged_with_reasons(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    res = scan(
        cfg, pb, tmp_path,
        book_fn=lambda t: {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
    )  # fmt: skip
    assert not res.built and set(res.skipped) == {"safe", "medium", "risky"}
    assert all(res.skipped.values())


# ------------------------------------------------------------------------------ exit policy
def mk(
    p: float, bid: float, ask: float, started: bool = False, settled: float | None = None
) -> LegMark:
    return LegMark("yes", p, bid, ask, started, settled)


def decide(
    tier: str, marks: list[LegMark], cost: float, n: int, cfg: C.PaperConfig, pb: PlaybookConfig
) -> str:
    t = next(x for x in pb.tiers if x.name == tier)
    return playbook_decision(marks, n, cost, t, playbook_cfg(cfg, pb), pb)[0]


def test_exit_policy_by_tier(cfg: C.PaperConfig, pb: PlaybookConfig) -> None:
    n, cost = 10, 5.0  # 0.50 per contract
    nothing_started = [mk(0.9, 0.9, 0.92)] * 3
    assert decide("safe", nothing_started, cost, n, cfg, pb) == "wait_pretip"
    up = [mk(0.9, 0.95, 0.96, started=True)] * 3  # bids 0.857 -> net ~0.85 >= 1.2 x 0.50
    assert decide("safe", up, cost, n, cfg, pb) == "exit_take_profit"
    flat = [mk(0.9, 0.6, 0.62, started=True)] * 3  # 0.216 < 0.60
    assert decide("safe", flat, cost, n, cfg, pb) == "hold"
    # medium: hold value < 0.25 x cost is never sold
    dying = [mk(0.5, 0.05, 0.07, started=True)] * 3
    assert decide("medium", dying, cost, n, cfg, pb) == "hold_near_zero"
    # sell when conservative proceeds >= hold value (not-yet-started leg valued low by the model)
    sell = [mk(0.1, 0.9, 0.92, started=True), mk(0.05, 0.95, 0.96, started=False)]
    assert decide("risky", sell, cost, n, cfg, pb) == "exit_ge_hold"
    hold = [mk(0.9, 0.5, 0.6, started=True)] * 2  # bids product 0.25 < mid product 0.30
    assert decide("risky", hold, cost, n, cfg, pb) == "hold"


def test_let_run_dead_and_settling(cfg: C.PaperConfig, pb: PlaybookConfig) -> None:
    hit = mk(0.9, 1.0, 1.0, settled=1.0)
    near = [hit, hit, mk(0.9, 0.93, 0.95, started=True)]
    assert decide("safe", near, 1.0, 10, cfg, pb) == "let_run"  # would otherwise take profit
    not_yet = [hit, hit, mk(0.9, 0.80, 0.84, started=True)]
    assert decide("safe", not_yet, 1.0, 10, cfg, pb) == "exit_take_profit"
    assert decide("medium", [hit, mk(0.9, 0.0, 0.0, settled=0.0)], 5.0, 10, cfg, pb) == "dead"
    assert decide("medium", [hit, hit], 5.0, 10, cfg, pb) == "settling"


def test_exit_net_subtracts_per_leg_fees_from_product_of_bids(
    cfg: C.PaperConfig, pb: PlaybookConfig
) -> None:
    from nba.parlay.ev import order_fee

    c = playbook_cfg(cfg, pb)
    marks = [mk(0.9, 0.80, 0.82, started=True), mk(0.9, 0.50, 0.52, started=True)]
    gross, fee, net = exit_net(marks, 10, c)
    assert gross == pytest.approx(0.40)
    assert fee == pytest.approx((order_fee(0.80, c.fee, 10) + order_fee(0.50, c.fee, 10)) / 10)
    assert net == pytest.approx(gross - fee) and net < gross
    assert exit_net([mk(0.9, 0.0, 0.0, settled=0.0)], 10, c)[2] == 0.0


# ----------------------------------------------------------------- monitor/settle/report
def market_state(ms: list[dict[str, Any]], move: float, result: str = "") -> Any:
    by = {m["ticker"]: m for m in ms}

    def get(t: str) -> dict[str, Any] | None:
        m = dict(by[t])
        bid, ask = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
        m["yes_bid_dollars"], m["yes_ask_dollars"] = (
            f"{min(bid + move, 0.99):.4f}",
            f"{min(ask + move, 0.99):.4f}",
        )
        m["result"] = result
        return m

    return get


def test_monitor_exit_settle_and_report_and_isolation(
    cfg: C.PaperConfig, pb: PlaybookConfig, tmp_path: Path
) -> None:
    ms, _, _ = universe()
    scan(cfg, pb, tmp_path)
    p = tmp_path / "pb.duckdb"
    legs = tmp_path / "legs_pb.json"
    c0 = playbook_monitor_once(DAY, NOW, cfg, pb, market_state(ms, 0.0), p, legs)
    assert c0["positions"] == 3 and c0["exits"] == 0  # pre-tip: marks only
    after = T1 + timedelta(minutes=40)
    c1 = playbook_monitor_once(DAY, after, cfg, pb, market_state(ms, 0.08), p, legs)
    assert c1["positions"] == 3
    with db.reader(p) as con:
        decisions = con.execute("SELECT decision FROM marks").fetchall()
        exits = con.execute("SELECT reason FROM exits").fetchall()
    assert {d[0] for d in decisions} >= {"wait_pretip"}
    assert len(exits) == c1["exits"] and all(
        r[0] in ("exit_take_profit", "exit_ge_hold") for r in exits
    )
    s = settle_positions(
        date(2026, 10, 22), after, playbook_cfg(cfg, pb), market_state(ms, 0.0, "yes"), p
    )
    assert s["settled"] >= 1
    rep = playbook_report(p, pb.rule_version)
    assert rep["claim"].startswith("none") and rep["nights"][str(DAY)]["built"]
    assert set(rep["running_by_tier"]) <= {"safe", "medium", "risky"}
    for v in rep["running_by_tier"].values():
        assert v["n_settled"] >= 1 and "pnl_exit_policy" in v and "pnl_hold_to_end" in v
    # isolation: the primary summary on the same store sees none of it
    primary = summarize(p, cfg.with_rule_version("paper-trader-v2"))
    assert primary["current_not_a_look"]["a_exit_rule"] is None
    assert primary["synthetic_parlays_descriptive"] is None


def test_scan_times_first_lead_and_sibling_leg_files(tmp_path: Path) -> None:
    assert scan_times([T2, T1], timedelta(minutes=45)) == [
        T1 - timedelta(minutes=45), T2 - timedelta(minutes=45)
    ]  # fmt: skip
    (tmp_path / "open_legs.json").write_text(json.dumps({"tickers": ["A"]}))
    (tmp_path / "open_legs_playbook.json").write_text(json.dumps({"tickers": ["B"]}))
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=None,  # type: ignore[arg-type]
        legs_file=tmp_path / "open_legs.json",
    )  # fmt: skip
    assert cap.leg_tickers() == {"A", "B"}


def test_price_ladder_uses_asks_and_the_verified_fee(
    cfg: C.PaperConfig, pb: PlaybookConfig
) -> None:
    c = playbook_cfg(cfg, pb)
    legs = tuple(eligible(cfg, pb)[:3])
    lad = price_ladder(pb.tiers[0], legs, c)
    prod = legs[0].ask * legs[1].ask * legs[2].ask
    assert lad.entry_price == pytest.approx(prod) and lad.cost == pytest.approx(
        lad.n * prod + lad.entry_fee
    )
