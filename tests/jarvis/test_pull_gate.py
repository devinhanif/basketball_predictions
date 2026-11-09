# ruff: noqa: E501, F811
"""The JARVIS pull gate beyond the first alert hold: config/store errors never escape the tick, the hold
exists only while the orchestrator is alive and has not given up, the slate preview path (prefetch and
left-out plays), the lead-cap counter, and a held play leaving no alert_state row. Tmp stores only."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import duckdb
import pytest

from nba.jarvis import runner
from nba.jarvis.runner import Runtime, run_slate, run_tick, tick_line
from nba.pulls.config import PullsConfig
from nba.pulls.store import PullStore
from tests.jarvis.conftest import G1, G2, NOW, SLATE, TIP1, Sent
from tests.jarvis.test_pull_hold import (  # noqa: F401  (fixtures)
    RULE,
    add_fresh_pinnacle,
    beat,
    hold_rt,
    pcfg,
    requests,
)

# ---- F1: pulls config and store errors never escape the tick ------------------------------------


def test_a_bad_pulls_config_does_not_stop_the_tick(
    rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad() -> PullsConfig:
        raise ValueError("pulls: pinnacle_alert_max_age_min must be > 0")

    monkeypatch.setattr(runner, "load_pulls_config", bad)
    rep = run_tick(rt, SLATE)
    assert len(rep["alerts"]) == 1 and "held" not in rep  # sent as before
    assert rt.pulls_loaded
    run_tick(rt, SLATE)  # loaded once, not once per play


def test_an_unreadable_request_store_never_raises(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    pcfg.requests_db.parent.mkdir(parents=True, exist_ok=True)
    pcfg.requests_db.write_bytes(b"not a duckdb file")
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1


# ---- F4: the hold exists only while the orchestrator is alive and has not given up ----------------


def test_no_heartbeat_means_send_as_before(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    (pcfg.ops_dir / "heartbeat" / "pulls.json").unlink()
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and not pcfg.requests_db.exists()


def test_a_stale_heartbeat_means_send_as_before(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    beat(pcfg, NOW - timedelta(minutes=11))
    assert len(run_tick(hold_rt, SLATE)["alerts"]) == 1
    assert not pcfg.requests_db.exists()


def test_a_disabled_beat_means_send_as_before(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    beat(pcfg, NOW - timedelta(minutes=1), "disabled (NBA_CLOSE_CAPTURE != 1)")
    assert len(run_tick(hold_rt, SLATE)["alerts"]) == 1 and not pcfg.requests_db.exists()


@pytest.mark.parametrize("status", ["refused_budget", "failed", "expired", "refused_post_tip"])
def test_a_request_that_ended_without_data_releases_the_alert(
    hold_rt: Runtime, pcfg: PullsConfig, status: str
) -> None:
    assert run_tick(hold_rt, SLATE)["alerts"] == []  # held, request filed
    (req,) = requests(pcfg)
    PullStore(pcfg.requests_db).set_status(req.request_id, status, NOW, "gave up")
    rep = run_tick(hold_rt, SLATE)
    assert len(rep["alerts"]) == 1 and "held" not in rep
    assert len(requests(pcfg)) == 1  # no new request while released


def test_the_hold_returns_after_the_cooldown_with_a_new_request(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    hold_rt.pulls_cfg = replace(pcfg, cooldown_min=2.0)  # the quote must stay under 5 min old
    run_tick(hold_rt, SLATE)
    (req,) = requests(pcfg)
    PullStore(pcfg.requests_db).set_status(req.request_id, "failed", NOW, "gave up")
    later = NOW + timedelta(minutes=3)
    beat(pcfg, later - timedelta(minutes=1))
    hold_rt.clock = lambda: later
    assert run_tick(hold_rt, SLATE)["alerts"] == []
    assert len(requests(pcfg)) == 2


# ---- F10: a held play leaves no alert_state row --------------------------------------------------


def test_a_held_play_writes_nothing_to_alert_state(hold_rt: Runtime) -> None:
    assert run_tick(hold_rt, SLATE)["alerts"] == []
    if not hold_rt.book.path.exists():  # nothing at all was written
        return
    con = duckdb.connect(str(hold_rt.book.path), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM alert_state").fetchone() == (0,)
        assert con.execute("SELECT count(*) FROM sends WHERE kind = 'alert'").fetchone() == (0,)
    finally:
        con.close()


# ---- the slate preview ------------------------------------------------------------------------


def text_of(rep: dict[str, Any]) -> str:
    return "\n".join(rep["texts"])


def test_prefetch_files_one_request_per_stale_game_before_the_preview(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=100)  # the preview opens at T-92: 8 min away
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    rep = run_tick(hold_rt, SLATE)
    assert sorted(rep["prefetch"]["filed"]) == sorted([G1, G2])
    assert {r.scope_game_id for r in requests(pcfg)} == {G1, G2}
    assert all(r.requester == "jarvis" and r.max_age_min == RULE for r in requests(pcfg))
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=98)
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    rep2 = run_tick(hold_rt, SLATE)
    assert rep2["prefetch"]["filed"] == [] and len(rep2["prefetch"]["existing"]) == 2
    assert len(requests(pcfg)) == 2  # deduped
    assert "prefetch=0new/2open" in tick_line(rep2)


@pytest.mark.parametrize("minutes_before", [130, 108, 91, 60])
def test_prefetch_only_runs_in_the_lead_window(
    hold_rt: Runtime, pcfg: PullsConfig, minutes_before: int
) -> None:
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=minutes_before)
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    rep = run_tick(hold_rt, SLATE)
    assert "prefetch" not in rep and not pcfg.requests_db.exists()


def test_prefetch_skips_a_game_whose_reference_is_fresh(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    now = TIP1 - timedelta(minutes=100)
    add_fresh_pinnacle(hold_rt, now - timedelta(minutes=5))  # G1 only: the fixture has no G2 rows
    hold_rt.clock = lambda: now
    beat(pcfg, now - timedelta(minutes=1))
    rep = run_tick(hold_rt, SLATE)
    assert rep["prefetch"]["filed"] == [G2]


def test_prefetch_window_lead_is_configurable(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    hold_rt.pulls_cfg = replace(pcfg, slate_prefetch_lead_min=40.0)
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=130)  # inside T-92-40
    beat(pcfg, hold_rt.clock() - timedelta(minutes=1))
    assert "prefetch" in run_tick(hold_rt, SLATE)


def test_preview_leaves_out_stale_plays_and_says_so(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    rep = run_slate(hold_rt, SLATE, force=True)
    assert rep["slate_held"] == 1 and rep["plays"] == 2  # planned plays are still counted
    assert "1 plays held: sharp price not refreshed (oldest 30 min)" in text_of(rep)
    assert all(not k.startswith(G1) for k in rep["announced_keys"])
    con = duckdb.connect(str(hold_rt.book.path), read_only=True)
    try:
        games = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM plays").fetchall()}
    finally:
        con.close()
    assert G1 not in games and G2 in games  # the held play is not recorded as announced


def test_preview_is_unchanged_when_not_alive_or_flag_off(rt: Runtime, pcfg: PullsConfig) -> None:
    rt.dry_run = True  # a real post twice at one clock tick would collide on the send id
    base = run_slate(rt, SLATE, force=True)["texts"]  # no pulls config, no heartbeat
    rt.pulls_cfg = pcfg
    assert run_slate(rt, SLATE, force=True).get("slate_held") is None  # no heartbeat: gate off
    beat(pcfg, NOW - timedelta(minutes=1))
    rt.cfg = replace(rt.cfg, pulls={"hold_stale_alerts": True, "hold_stale_slate": False})
    off = run_slate(rt, SLATE, force=True)
    assert off["texts"] == base and "slate_held" not in off


def test_preview_releases_a_game_whose_request_gave_up(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    store = PullStore(pcfg.requests_db)
    res = store.request(
        source="pinnacle", scope_game_id=G1, slate_date=SLATE,
        need_by_utc=NOW + timedelta(minutes=5), max_age_min=RULE, requester="jarvis",
        reason="x", now=NOW,
    )  # fmt: skip
    store.set_status(res.request.request_id, "refused_budget", NOW, "cap")
    rep = run_slate(hold_rt, SLATE, force=True)
    assert "slate_held" not in rep and "plays held" not in text_of(rep)


def test_preview_with_every_play_held_says_so_instead_of_no_prices(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    hold_rt.pulls_cfg = replace(pcfg, pinnacle_alert_max_age_min=1.0)
    rep = run_slate(hold_rt, SLATE, force=True)
    text = text_of(rep)
    assert rep["slate_held"] >= 1 and "plays held: sharp price not refreshed" in text
    if not rep["announced_keys"]:
        assert "No live prices read yet" not in text


def test_flag_off_files_nothing_and_changes_nothing(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    hold_rt.cfg = replace(
        hold_rt.cfg, pulls={"hold_stale_alerts": False, "hold_stale_slate": False}
    )
    hold_rt.clock = lambda: TIP1 - timedelta(minutes=100)
    rep = run_tick(hold_rt, SLATE)
    assert "prefetch" not in rep and not pcfg.requests_db.exists()
    assert "slate_held" not in run_slate(hold_rt, SLATE, force=True)


def test_dry_run_preview_holds_but_files_nothing(hold_rt: Runtime, pcfg: PullsConfig) -> None:
    hold_rt.dry_run = True
    rep = run_slate(hold_rt, SLATE, force=True)
    assert rep["slate_held"] == 1 and not pcfg.requests_db.exists()


# ---- F2: count plays capped by max_ref_lead after a pull request ---------------------------------


def test_lead_cap_after_a_request_is_counted_and_the_rule_is_unchanged(
    hold_rt: Runtime, pcfg: PullsConfig
) -> None:
    add_fresh_pinnacle(hold_rt, NOW + timedelta(minutes=5))  # Pinnacle 7 min newer than the quote
    now = NOW + timedelta(minutes=6)
    hold_rt.clock = lambda: now
    beat(pcfg, now - timedelta(minutes=1))
    PullStore(pcfg.requests_db).request(
        source="pinnacle", scope_game_id=G1, slate_date=SLATE, need_by_utc=now,
        max_age_min=RULE, requester="jarvis", reason="x", now=NOW,
    )  # fmt: skip
    rep = run_tick(hold_rt, SLATE)
    assert rep["alerts"] == []  # the rule still caps it at thin: no alert
    assert rep["lead_capped_after_hold"] >= 1 and "lead_capped_after_hold=" in tick_line(rep)
