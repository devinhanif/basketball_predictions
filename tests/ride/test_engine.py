from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from nba.ride import gate, sizing
from nba.ride.notify import Notifier
from nba.ride.watch import Engine, Event
from nba.ride.winprob import WinProbModel

from .helpers import T0, build, heartbeat, markets, put


def engine(root: Path, tmp_path: Path, cfg: gate.RideConfig | None = None) -> Engine:
    return Engine(
        root,
        "2026-10-22",
        WinProbModel.load(),
        cfg or gate.RideConfig(),
        sizing.load_fee(),
        Notifier(tmp_path / "x.log", push=False),
    )


def kinds(ev: list[Event]) -> list[str]:
    return [e.kind for e in ev]


def test_ride_fires_once_with_ticker_ask_slice_and_fee(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root)
    eng = engine(root, tmp_path)
    now = T0 + timedelta(seconds=30)
    ev = eng.step(now)
    ride = [e for e in ev if e.kind == "RIDE"]
    assert len(ride) == 1
    t = ride[0].text
    assert "KXNBAGAME-26OCT22CLEPHI-PHI" in t and "$0.97" in t and "fee $0." in t and "CLE@PHI" in t
    assert "$10.84 over 1 games" in t  # one game left that can still qualify
    assert "Ours" in t and "ESPN" in t
    again = eng.step(now + timedelta(seconds=60))  # same state, still fresh: no second alert
    assert "RIDE" not in kinds(again)


def test_stale_heartbeat_gives_no_alert_and_logs_why(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root)
    heartbeat(root, T0 - timedelta(minutes=5))
    eng = engine(root, tmp_path)
    ev = eng.step(T0 + timedelta(seconds=30))
    assert "RIDE" not in kinds(ev)
    assert "stale_heartbeat" in (tmp_path / "x.log").read_text()


def test_espn_disagreement_blocks(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root, wp=0.90)  # ESPN 90% vs ours ~98.5%
    eng = engine(root, tmp_path)
    ev = eng.step(T0 + timedelta(seconds=30))
    assert "RIDE" not in kinds(ev)
    assert "espn_disagrees" in (tmp_path / "x.log").read_text()


def test_ask_at_a_dollar_has_no_room(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root)
    put(root, "kalshi", "KXNBAGAME_p0", T0 + timedelta(seconds=10), markets(ask=1.0, bid=0.99))
    ev = engine(root, tmp_path).step(T0 + timedelta(seconds=30))
    assert "RIDE" not in kinds(ev)
    assert "quote_no_room" in (tmp_path / "x.log").read_text()


def test_stale_game_file_blocks(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root)
    heartbeat(root, T0 + timedelta(minutes=10))
    ev = engine(root, tmp_path).step(
        T0 + timedelta(minutes=10, seconds=10)
    )  # summary file now 10 min old
    assert "RIDE" not in kinds(ev)


def test_close_game_is_watch_only_then_settles_after_ride(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root, hs=100, as_=96, period=4, clock="5:00", wp=0.80)
    eng = engine(root, tmp_path)
    ev = eng.step(T0 + timedelta(seconds=30))
    assert "RIDE" not in kinds(ev)
    root2 = tmp_path / "cap2"
    build(root2)
    e2 = engine(root2, tmp_path, replace(gate.RideConfig(), credit=10.84))
    e2.step(T0 + timedelta(seconds=30))
    t2 = T0 + timedelta(seconds=90)
    put(
        root2,
        "espn_summary",
        "401",
        t2,
        __import__("tests.ride.helpers", fromlist=["summary"]).summary(
            115, 96, 4, "0.0", 0.999, state="post"
        ),
    )
    heartbeat(root2, t2)
    ev2 = e2.step(t2 + timedelta(seconds=5))
    assert any(e.kind == "PLAN" and e.text.startswith("SETTLED WIN") for e in ev2)
    assert [
        e for e in e2.step(t2 + timedelta(seconds=65)) if e.kind == "PLAN"
    ] == []  # one line only


def test_state_persists_across_restart(tmp_path: Path) -> None:
    root = tmp_path / "cap"
    build(root)
    sp = tmp_path / "state.json"
    e1 = Engine(
        root,
        "2026-10-22",
        WinProbModel.load(),
        gate.RideConfig(),
        sizing.load_fee(),
        Notifier(tmp_path / "x.log", push=False),
        state_path=sp,
    )
    assert "RIDE" in kinds(e1.step(T0 + timedelta(seconds=30)))
    e2 = Engine(
        root,
        "2026-10-22",
        WinProbModel.load(),
        gate.RideConfig(),
        sizing.load_fee(),
        Notifier(tmp_path / "x.log", push=False),
        state_path=sp,
    )
    assert "RIDE" not in kinds(e2.step(T0 + timedelta(seconds=60)))
