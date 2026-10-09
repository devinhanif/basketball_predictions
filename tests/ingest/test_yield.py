"""Lock-yield hook: pause while a lock path exists plus a grace period (temp dirs, fake clock)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nba.ingest import cache


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _gate(paths: list[Path], clock: Clock, grace: float = 300.0) -> cache.YieldGate:
    return cache.YieldGate(paths, grace_s=grace, poll_s=60.0, clock=clock.now, sleep=clock.sleep)


def test_no_lock_never_waits(tmp_path: Path) -> None:
    c = Clock()
    assert _gate([tmp_path / "lock"], c).wait() == 0.0 and c.sleeps == []


def test_waits_while_lock_exists_then_grace(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.mkdir()
    c = Clock()
    g = _gate([lock], c)
    removed = {"n": 0}
    orig = c.sleep

    def sleep(s: float) -> None:
        orig(s)
        removed["n"] += 1
        if removed["n"] == 3 and lock.exists():
            lock.rmdir()

    g._sleep = sleep
    waited = g.wait()
    assert not lock.exists()
    # lock seen at t=0,60,120; removed after 3rd sleep (t=180); grace runs 300s from last sighting
    assert waited >= 120 + 300 - 1e-6
    assert waited < 120 + 300 + 61
    assert g.wait() == 0.0  # clear afterwards


def test_second_lock_path_also_yields(tmp_path: Path) -> None:
    other = tmp_path / "lineups_lock"
    other.mkdir()
    c = Clock()
    g = _gate([tmp_path / "lock", other], c, grace=0.0)
    g._sleep = lambda s: (c.sleep(s), other.rmdir())[0]
    assert g.wait() == 60.0


def test_env_hook_default_off_and_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cache.YIELD_ENV, raising=False)
    (tmp_path / "lock").mkdir()
    assert cache.yield_to() == 0.0  # lock present but hook disabled
    monkeypatch.setenv(cache.YIELD_ENV, str(tmp_path / "gone"))
    monkeypatch.setenv(cache.YIELD_GRACE_ENV, "0")
    assert cache.yield_to() < 0.01  # enabled, no lock


def test_pull_loop_yields_only_before_network_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cache, "yield_to", lambda: calls.append("y") or 0.0)
    con = cache.open_db(tmp_path / "t.duckdb")
    cache.mark_done(con, "src", "a", tmp_path / "a.parquet")
    (tmp_path / "a.parquet").write_bytes(b"x")
    cache.pull_ids_with_breaker(con, "src", ["a", "b"], lambda k: None)
    assert calls == ["y"]  # cached key 'a' skipped the gate; 'b' hit it


def test_queue_children_get_yield_env() -> None:
    from nba.ingest import queue as q

    assert [p.name for p in q.YIELD_PATHS] == ["lock"] and q.YIELD_GRACE_S == 120
