# ruff: noqa: E501, F811
"""Re-review fixes: an unserved request past its deadline releases the hold, a failing pulls tick is not
"alive", a play held out of the preview is not alerted on the same price, the Daily Dollar is held on a
stale sharp line, and a dry run files nothing."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from nba.jarvis import runner
from nba.jarvis.models import Play, PlayLeg
from nba.jarvis.runner import Runtime, run_slate, run_tick
from nba.jarvis.slate import gather
from nba.pulls.config import PullsConfig
from nba.pulls.freshness import pulls_alive
from nba.pulls.store import PullStore
from tests.jarvis.conftest import G1, NOW, SLATE, TIP1, Sent
from tests.jarvis.test_pull_hold import (  # noqa: F401  (fixtures)
    RULE,
    add_fresh_pinnacle,
    beat,
    hold_rt,
    pcfg,
    requests,
)


def old_open_request(pcfg: PullsConfig) -> Any:
    """A request nobody ever served: 3 hours old, 2 h 45 past its deadline."""
    t0 = NOW - timedelta(hours=3)
    return PullStore(pcfg.requests_db).request(
        source="pinnacle", scope_game_id=G1, slate_date=SLATE, need_by_utc=t0 + timedelta(minutes=15),
        max_age_min=RULE, requester="jarvis", reason="old", now=t0,
    )  # fmt: skip


# ---- R1 -------------------------------------------------------------------------------------------


def test_an_open_request_past_its_deadline_releases_the_alert_and_the_preview(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    old_open_request(pcfg)
    rep = run_tick(hold_rt, SLATE)  # fresh, healthy heartbeat (rc 0); the request was never served
    assert len(rep["alerts"]) == 1 and "held" not in rep
    pre = run_slate(hold_rt, SLATE, force=True)
    assert "slate_held" not in pre and len(requests(pcfg)) == 1  # nothing new filed either


def test_a_failing_tick_with_a_fresh_heartbeat_is_not_alive(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    old_open_request(pcfg)
    beat(pcfg, NOW - timedelta(minutes=1))
    import json

    path = pcfg.ops_dir / "heartbeat" / "pulls.json"
    hb = json.loads(path.read_text())
    for rc in (1, 2, 75, 143):
        path.write_text(json.dumps({**hb, "rc": rc}))
        assert pulls_alive(pcfg, NOW)[0] is False and "failing" in pulls_alive(pcfg, NOW)[1]
        if rc == 1:
            assert len(run_tick(hold_rt, SLATE)["alerts"]) == 1  # sent as before (not held)
    for rc in (0, 4, None):
        path.write_text(json.dumps({**hb, "rc": rc}))
        assert pulls_alive(pcfg, NOW)[0] is True


def test_a_request_inside_its_deadline_still_holds(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    assert run_tick(hold_rt, SLATE)["alerts"] == []  # filed now, need_by in 15 min
    hold_rt.clock = lambda: NOW + timedelta(minutes=1)
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    assert run_tick(hold_rt, SLATE)["alerts"] == []


# ---- R3: a play held out of the preview is not alerted on the same price -----------------------------


def preview_due(hold_rt: Runtime, alerts: bool) -> None:
    hold_rt.cfg = replace(
        hold_rt.cfg,
        slate_lead_min=300.0,
        pulls={"hold_stale_alerts": alerts, "hold_stale_slate": True},
    )


def test_slate_hold_without_alert_hold_does_not_alert_the_held_play_in_the_same_tick(
    hold_rt: Runtime, pcfg: PullsConfig, sent: Sent
) -> None:
    preview_due(hold_rt, alerts=False)
    rep = run_tick(hold_rt, SLATE)
    assert rep["slate_held"] == 1 and rep["alerts"] == []
    assert not any("ALERT" in t for t in sent.texts)
    assert rep["held"][0]["game_id"] == G1
    # next tick the preview is done and its hold is over: the alert flag (off) decides, as before
    hold_rt.clock = lambda: NOW + timedelta(minutes=1)
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    assert len(run_tick(hold_rt, SLATE)["alerts"]) == 1


def test_a_held_strong_play_reaches_the_alert_hold_in_the_preview_tick(
    hold_rt: Runtime, pcfg: PullsConfig, sent: Sent
) -> None:
    preview_due(hold_rt, alerts=True)
    rep = run_tick(hold_rt, SLATE)
    assert rep["alerts"] == [] and rep["slate_held"] == 1
    assert rep["held"][0]["request"] == "filed"  # the alert hold filed the request this tick
    assert len(requests(pcfg)) == 1


def test_the_preview_marks_only_the_posted_plays_as_announced(hold_rt: Runtime) -> None:
    rep = run_slate(hold_rt, SLATE, force=True)
    assert rep["held_keys"] and all(k.startswith(G1) for k in rep["held_keys"])
    assert rep["announced_keys"] and not set(rep["announced_keys"]) & set(rep["held_keys"])
    assert all(not k.startswith(G1) for k in rep["announced_keys"])


# ---- R4: the Daily Dollar -----------------------------------------------------------------------------


def ticket() -> Play:
    legs = (
        PlayLeg("kalshi", "KXNBAGAME-26OCT28LALLAC-LAL", "yes", 0.5, "game_win", "LAL to win", team_id=1610612747),
        PlayLeg("kalshi", "KXNBAPTS-26OCT28LALLAC-LALLJAMES23-25", "yes", 0.4, "prop", "LeBron 25+", player_id=2544, stat="pts", threshold=25.0),
    )  # fmt: skip
    return Play(G1, "LAL@LAC", TIP1, legs, "daily_dollar", 5.0, 0.05, 1.0, 0.04, 1, 1.0, "r")


def test_a_daily_dollar_on_a_stale_sharp_line_is_skipped_with_one_line(
    hold_rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False)
    reason = runner._stale_ticket_reason(hold_rt, st, ticket(), SLATE)
    assert reason == "held: sharp price not refreshed (oldest 30 min)"
    monkeypatch.setattr(runner.dailydollar, "build", lambda *a, **k: (ticket(), ""))
    play, why = runner._daily_dollar(hold_rt, st, SLATE)
    assert play is None and why == reason
    assert runner.dailydollar.lines(play, why) == [f"**Daily Dollar**: skipped ({reason})"]


def test_a_daily_dollar_is_posted_when_the_line_is_fresh_released_or_the_flag_is_off(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    st = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False)
    off = replace(
        hold_rt,
        cfg=replace(hold_rt.cfg, pulls={"hold_stale_alerts": True, "hold_stale_slate": False}),
    )
    assert runner._stale_ticket_reason(off, st, ticket(), SLATE) is None
    res = PullStore(pcfg.requests_db).request(
        source="pinnacle", scope_game_id=G1, slate_date=SLATE, need_by_utc=NOW + timedelta(minutes=5),
        max_age_min=RULE, requester="jarvis", reason="x", now=NOW,
    )  # fmt: skip
    PullStore(pcfg.requests_db).set_status(res.request.request_id, "failed", NOW, "gave up")
    assert runner._stale_ticket_reason(hold_rt, st, ticket(), SLATE) is None  # released
    add_fresh_pinnacle(hold_rt, NOW - timedelta(minutes=2))
    st2 = gather(hold_rt.cfg, SLATE, NOW, use_polymarket=False)
    assert runner._stale_ticket_reason(hold_rt, st2, ticket(), SLATE) is None  # fresh


# ---- R5: a dry run files nothing --------------------------------------------------------------------------


def test_a_dry_run_prefetch_lists_games_but_files_no_request(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    hold_rt.dry_run = True
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=100)
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    rep = run_tick(hold_rt, SLATE)
    assert rep["prefetch"]["dry_run"] is True and len(rep["prefetch"]["filed"]) == 2
    assert not pcfg.requests_db.exists()
