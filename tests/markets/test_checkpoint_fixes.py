# ruff: noqa: E501
"""Review fixes (2026-11-10): flag-on tick wiring, notes after rows, game over during a gap, lag named first."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis import halftime_overs as HO
from nba.jarvis import ingame_checkpoints as IC
from nba.jarvis.config import JarvisConfig, load_config
from nba.markets import checkpoint_scorer as ck
from nba.markets.checkpoint_store import CheckpointStore
from tests.markets.checkpoint_helpers import T0, S, build_night, first_tick, regular
from tests.markets.halftime_helpers import tiny_artifact
from tests.markets.test_checkpoint_scorer import PIN, SLATE, run
from tests.markets.test_halftime_scorer import FakeRecency

ROOT = Path(__file__).resolve().parents[2]
NS = 4


def _cfg(tmp: Path, digest: str | None = None) -> tuple[JarvisConfig, Path]:
    art = tiny_artifact(tmp)
    base = load_config(ROOT / "configs" / "jarvis.yaml")
    ho = {
        **base.halftime_overs, "enabled": True, "artifact": str(tmp / "artifact.json"),
        "artifact_digest": digest or art.digest, "fee_pin": PIN,
    }  # fmt: skip
    ic = {"enabled": True, "store": str(tmp / "ck.duckdb"), "top_n": 3}
    return replace(base, halftime_overs=ho, ingame_checkpoints=ic, root=tmp), tmp / "ck.duckdb"


def _prime(cfg: JarvisConfig, db: Path) -> None:
    mgd = cfg.halftime_overs.get("recency_min_game_date")
    HO._CACHE[("rec", f"{db}:{SLATE}:{mgd}")] = FakeRecency()


def test_flag_on_tick_scores_stores_and_returns_the_card_summary(tmp_path: Path) -> None:
    cfg, store_path = _cfg(tmp_path)
    build_night(tmp_path / "data" / "raw", regular())
    db = tmp_path / "nba.duckdb"
    _prime(cfg, db)
    now = T0 + (60 * first_tick(10) + 5 + 45) * S
    out = IC.score_checkpoints(cfg, SLATE, now, ["DET@PHI"], dry_run=False, nba_db=db)
    assert out["ok"] is True and out["stored"] > 0
    v = out["latest"]["DET@PHI"]
    assert v["checkpoint"] == 10 and v["n_units"] >= 2 and v["known_at"] is not None
    store = CheckpointStore.open(store_path, mode="live")
    n = len(store.rows())
    store.close()
    again = IC.score_checkpoints(cfg, SLATE, now, ["DET@PHI"], dry_run=False, nba_db=db)
    assert again["stored"] == 0  # a restart adds nothing
    store = CheckpointStore.open(store_path, mode="live")
    assert len(store.rows()) == n


def test_flag_on_tick_refuses_a_digest_mismatch_and_stores_nothing(tmp_path: Path) -> None:
    cfg, store_path = _cfg(tmp_path, digest="0" * 64)
    build_night(tmp_path / "data" / "raw", regular())
    out = IC.score_checkpoints(
        cfg, SLATE, T0 + 700 * S, ["DET@PHI"], dry_run=False, nba_db=tmp_path / "nba.duckdb"
    )
    assert out["ok"] is False and out["error"] == "artifact digest mismatch" and out["latest"] == {}
    assert not store_path.exists()


def test_flag_on_dry_run_leaves_no_store(tmp_path: Path) -> None:
    cfg, store_path = _cfg(tmp_path)
    build_night(tmp_path / "data" / "raw", regular())
    db = tmp_path / "nba.duckdb"
    _prime(cfg, db)
    now = T0 + (60 * first_tick(10) + 50) * S
    out = IC.score_checkpoints(cfg, SLATE, now, ["DET@PHI"], dry_run=True, nba_db=db)
    assert out["ok"] is True and out["stored"] > 0 and not store_path.exists()


def test_a_failed_append_leaves_the_decision_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    night = build_night(tmp_path, regular())
    store = CheckpointStore.open(":memory:")
    real = store.append

    def boom(*a: Any, **k: Any) -> int:
        raise RuntimeError("disk")

    monkeypatch.setattr(store, "append", boom)
    with pytest.raises(RuntimeError):
        run(night, store, tmp_path)
    assert not store.noted("401900001", "KXNBAPTS", 5)  # nothing was closed without rows
    monkeypatch.setattr(store, "append", real)
    run(night, store, tmp_path)
    assert {r["checkpoint"] for r in store.rows()} == set(ck.CHECKPOINTS)


def test_a_game_that_ended_during_a_gap_gets_a_named_skip(tmp_path: Path) -> None:
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 2650.0, "STATUS_IN_PROGRESS"),
        (120, 2710.0, "STATUS_IN_PROGRESS"),
    ]
    night = build_night(tmp_path, states, extra=[(180, "STATUS_FINAL", 2880.0, None)])
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(night, store, tmp_path, now=T0 + 400 * S)
    assert store.rows() == [] and rep.by_checkpoint[45]["game_over_during_gap"] == NS
    assert all(store.noted("401900001", s, 45) for s in ("KXNBAPTS", "KXNBAREB"))
    again = run(night, store, tmp_path, now=T0 + 410 * S)
    assert again.counts["fires"] == 0  # noted once, not repeated


def test_a_missed_tick_is_named_late_not_stale(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular(), play_age_s=190)  # stale by the play rule at its page
    known = T0 + (60 * first_tick(10) + 5) * S
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(
        night, store, tmp_path, now=known + 200 * S
    )  # ... and over 180 s old at now: a missed tick
    assert (
        rep.by_checkpoint[10]["refused_late_write"] >= 1
        and rep.by_checkpoint[10].get("state_stale", 0) == 0
    )


def test_the_live_window_invariant_is_asserted() -> None:
    assert ck.LIVE_WINDOW_S > ck.LIVE_MAX_LAG_S + ck.QUOTE_MAX_AFTER_S
