# ruff: noqa: E501
"""JARVIS holds an alert whose Pinnacle reference is older than the pull rule and files ONE deduped pull
request; it sends once the reference is fresh. Labels, edges and stakes are untouched; flag off = the old
behaviour. Tmp stores and fakes only."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis.runner import Runtime, run_tick
from nba.jarvis.slate import gather, plan_slate
from nba.jarvis.sources import load_pinnacle
from nba.pulls.config import PullsConfig
from nba.pulls.store import PullStore
from tests.jarvis.conftest import G1, LEBRON, NOW, SLATE, TIP1, Sent

# the Pinnacle rows in the fixture were requested at 19:30, so the reference is 30 min old at NOW
RULE = 20.0


@pytest.fixture
def pcfg(tmp_path: Path) -> PullsConfig:
    return PullsConfig(
        requests_db=tmp_path / "pulls" / "pulls.duckdb", pinnacle_alert_max_age_min=RULE
    )


@pytest.fixture
def hold_rt(rt: Runtime, pcfg: PullsConfig) -> Runtime:
    rt.pulls_cfg = pcfg
    assert rt.cfg.pulls["hold_stale_alerts"] is True  # the shipped config turns it on
    return rt


def add_fresh_pinnacle(rt: Runtime, requested: datetime, kind: str = "ondemand1") -> None:
    """What the orchestrator's pull writes: the same Pinnacle prices, requested more recently."""
    con = duckdb.connect(str(rt.cfg.paths["market_db"]))
    rows = con.execute(
        "SELECT * FROM live_book_odds WHERE book = 'pinnacle' AND snapshot_kind = 't90'"
    ).fetchall()
    for r in rows:
        r = list(r)
        r[3], r[4], r[5], r[19] = kind, requested, requested, requested
        con.execute("INSERT INTO live_book_odds VALUES (" + ",".join("?" * len(r)) + ")", r)
    con.close()


def requests(pcfg: PullsConfig) -> list[Any]:
    return PullStore(pcfg.requests_db).list_requests()


def test_stale_reference_holds_the_alert_and_files_exactly_one_request(
    hold_rt: Runtime, pcfg: PullsConfig, sent: Sent
) -> None:
    rep = run_tick(hold_rt, SLATE)
    assert rep["alerts"] == [] and not any("ALERT" in t for t in sent.texts)
    (h,) = rep["held"]
    assert h["game_id"] == G1 and h["ref_age_min"] == 30.0 and h["max_age_min"] == RULE
    assert h["request"] == "filed" and h["request_id"].startswith("pr-") and "held" in h["why"]
    (req,) = requests(pcfg)
    assert (req.source, req.scope_game_id, req.requester, req.status) == (
        "pinnacle",
        G1,
        "jarvis",
        "open",
    )
    assert req.max_age_min == RULE and req.slate_date == SLATE
    # later ticks while it is still stale: still held, still one request (deduped)
    hold_rt.clock = lambda: NOW + timedelta(minutes=1)
    rep2 = run_tick(hold_rt, SLATE)
    assert rep2["alerts"] == [] and len(requests(pcfg)) == 1
    assert rep2["held"][0]["request"] == "already open"
    assert req.request_id == rep2["held"][0]["request_id"]


def test_the_alert_goes_out_on_a_later_tick_once_the_reference_is_fresh(
    hold_rt: Runtime, pcfg: PullsConfig, sent: Sent
) -> None:
    assert run_tick(hold_rt, SLATE)["alerts"] == []
    add_fresh_pinnacle(hold_rt, NOW - timedelta(minutes=2))  # the orchestrator's ondemand1 pull
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and "held" not in rep
    assert any("ALERT" in t and "STRONG" in t for t in sent.texts)
    assert len(requests(pcfg)) == 1  # nothing new was filed


def test_jarvis_reads_an_ondemand_row_as_the_newest_pinnacle_reference(hold_rt: Runtime) -> None:
    add_fresh_pinnacle(hold_rt, NOW - timedelta(minutes=2))
    games = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False).games
    refs = load_pinnacle(hold_rt.cfg, games, SLATE, NOW)
    assert refs.prop[(G1, LEBRON, "pts", 24.5)].ts == NOW - timedelta(minutes=2)
    assert all(r.ts == NOW - timedelta(minutes=2) for r in refs.win.values())


def test_the_hold_changes_no_label_edge_or_stake(hold_rt: Runtime) -> None:
    st = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False)
    base = plan_slate(hold_rt.cfg, st, hold_rt.book)
    run_tick(hold_rt, SLATE)
    st2 = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False)
    after = plan_slate(hold_rt.cfg, st2, hold_rt.book)
    key = [(p.key, p.label, p.edge_cents, p.contracts, p.stake_usd) for p in base]
    assert repr(key) == repr(
        [(p.key, p.label, p.edge_cents, p.contracts, p.stake_usd) for p in after]
    )  # NaN-safe


def test_flag_off_is_the_old_behaviour(hold_rt: Runtime, pcfg: PullsConfig, sent: Sent) -> None:
    hold_rt.cfg = replace(hold_rt.cfg, pulls={"hold_stale_alerts": False})
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and "held" not in rep
    assert not pcfg.requests_db.exists()  # no request, no file


def test_orchestrator_disabled_means_no_hold(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    hold_rt.pulls_cfg = replace(pcfg, enabled=False)
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and not pcfg.requests_db.exists()


def test_reference_inside_the_rule_is_sent_as_before(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    hold_rt.pulls_cfg = replace(pcfg, pinnacle_alert_max_age_min=30.0)  # 30.0 <= 30.0
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and "held" not in rep and not pcfg.requests_db.exists()


def test_dry_run_reports_the_hold_and_writes_nothing(
    hold_rt: Runtime, pcfg: PullsConfig, sent: Sent
) -> None:
    hold_rt.dry_run = True
    rep = run_tick(hold_rt, SLATE)
    assert rep["alerts"] == [] and rep["held"][0]["request"] == "dry run: none filed"
    assert not pcfg.requests_db.exists() and not hold_rt.book.path.exists() and sent.texts == []


def test_a_broken_request_store_still_holds_the_stale_alert(hold_rt: Runtime, sent: Sent) -> None:
    def boom(**_kw: object) -> None:
        raise OSError("disk")

    hold_rt.pull_requester = boom
    rep = run_tick(hold_rt, SLATE)
    assert rep["alerts"] == [] and rep["held"][0]["request"] == "request failed (OSError)"
    assert not any("ALERT" in t for t in sent.texts)


def test_an_alert_without_a_pinnacle_reference_is_never_held(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    con = duckdb.connect(str(hold_rt.cfg.paths["market_db"]))
    con.execute("DELETE FROM live_book_odds WHERE book = 'pinnacle'")
    con.close()
    rep = run_tick(hold_rt, SLATE)
    assert "held" not in rep and not pcfg.requests_db.exists()


def test_tick_line_names_the_hold(hold_rt: Runtime) -> None:
    from nba.jarvis.runner import tick_line

    rep = run_tick(hold_rt, SLATE)
    assert "held_stale_ref=1" in tick_line(rep)
    assert TIP1 > NOW
