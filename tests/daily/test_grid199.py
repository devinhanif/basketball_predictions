# ruff: noqa: E501
"""Production's continuous 199-point grid is persisted per primary context row (own table), reproduces
the stored ``p_ge_full`` exactly, is written on change only, never touches a primary row, and stays
out of the canonical dump."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest

from nba.daily import canonical_dump, grid199
from nba.daily.pipeline import RunSummary, run_daily
from nba.daily.schedule import ScheduledGame
from nba.props import full_support as fs
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db
from tests.props.test_forward_context import AS_OF, _big_history

PRIMARY = "props_context_residual"
TIP = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 30)
GAMES = [
    ScheduledGame("0022600901", TIP, T1, T2),
    ScheduledGame("0022600902", TIP + timedelta(hours=3), T3, T4),
]


@pytest.fixture(scope="module")
def big() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    _big_history(c, 100, seed=3)
    return c


def _run(c: duckdb.DuckDBPyConnection, hours_before: float = 3.0, **kw: object) -> RunSummary:
    return run_daily(
        c, AS_OF, now=TIP - timedelta(hours=hours_before), schedule_fn=lambda _s: GAMES,
        skip_ingest=True, skip_injury=True, **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def test_run_persists_one_grid_per_context_row_matching_p_ge_full(
    big: duckdb.DuckDBPyConnection,
) -> None:
    s = _run(big)
    assert s.grid_status.startswith("ok:") and not s.shadow_errors
    prim = big.execute(
        "SELECT game_id, player_id, target, CAST(prediction AS VARCHAR) FROM forward_predictions "
        "WHERE run_id = ? AND model_name = ?",
        [s.run_id, PRIMARY],
    ).fetchall()
    grids = {
        (g, p, st): (q, sha, t)
        for g, p, st, q, sha, t in big.execute(
            f"SELECT game_id, player_id, stat, q199, q199_sha, tipoff FROM {grid199.TABLE} "
            "WHERE run_id = ?",
            [s.run_id],
        ).fetchall()
    }
    ctx = [r for r in prim if json.loads(r[3]).get("routed_to") != "recency_fallback"]
    assert ctx and len(grids) == len(ctx)
    for g, p, st, pj in ctx:
        q, sha, tip = grids[(g, p, st)]
        arr = np.asarray(q, dtype=float)
        assert arr.shape == (199,) and np.all(np.diff(arr) >= 0) and sha == grid199.grid_sha(arr)
        full = json.loads(pj)["p_ge_full"]
        assert fs.from_samples(arr, st) == full  # the grid IS what p_ge_full was cut from
        assert tip is not None


def test_write_on_change_second_identical_run_adds_nothing(big: duckdb.DuckDBPyConnection) -> None:
    n0 = big.execute(f"SELECT count(*) FROM {grid199.TABLE}").fetchone()
    s = _run(big, hours_before=2.5)
    n1 = big.execute(f"SELECT count(*) FROM {grid199.TABLE}").fetchone()
    assert n0 is not None and n1 is not None and n1[0] == n0[0]
    assert " 0 written" in s.grid_status


def test_failure_never_changes_a_primary_row_or_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rows(c: duckdb.DuckDBPyConnection, rid: str) -> list[tuple[object, ...]]:
        return c.execute(
            "SELECT model_name, game_id, target, player_id, CAST(prediction AS VARCHAR) "
            "FROM forward_predictions WHERE run_id = ? ORDER BY 1,2,3,4",
            [rid],
        ).fetchall()

    a = build_fixture_db(":memory:")
    _big_history(a, 100, seed=3)
    b = build_fixture_db(":memory:")
    _big_history(b, 100, seed=3)
    ok = _run(a)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("grid table unavailable")

    monkeypatch.setattr(grid199, "ensure_table", boom)
    bad = _run(b)
    assert bad.grid_status.startswith("failed: RuntimeError")
    assert any("prop grid199" in e for e in bad.shadow_errors)
    assert rows(a, ok.run_id) == rows(b, bad.run_id) and rows(b, bad.run_id)  # primaries identical


def test_persist_refuses_post_tip_and_bad_grids_and_never_raises() -> None:
    c = duckdb.connect(":memory:")
    good = np.linspace(0, 30, 199)
    key = ("G", 1, "pts")
    tips = {"G": TIP}
    late = grid199.persist(c, "r", TIP, {key: good}, tips)
    assert late == "ok: 0 written, 0 unchanged, 1 refused"
    bad = {("G", 2, "pts"): good[:10], ("G", 3, "pts"): np.full(199, np.nan), key: good}
    assert grid199.persist(c, "r", TIP - timedelta(hours=1), bad, tips) == (
        "ok: 1 written, 0 unchanged, 2 refused"
    )
    assert grid199.persist(c, "r2", TIP - timedelta(minutes=30), {key: good}, tips) == (
        "ok: 0 written, 1 unchanged, 0 refused"
    )
    changed = good + 0.01
    assert grid199.persist(c, "r3", TIP - timedelta(minutes=20), {key: changed}, tips).startswith(
        "ok: 1 written"
    )
    c.close()
    assert grid199.persist(c, "r4", TIP - timedelta(minutes=10), {key: good}, tips).startswith(
        "failed:"
    )


def test_canonical_dump_does_not_read_the_grid_table(big: duckdb.DuckDBPyConnection) -> None:
    _run(big, hours_before=2.0)
    n = big.execute(f"SELECT count(*) FROM {grid199.TABLE}").fetchone()
    assert n is not None and n[0] > 0
    with_table = canonical_dump.canonical_csv(big)
    big.execute(f"DROP TABLE {grid199.TABLE}")
    assert (
        canonical_dump.canonical_csv(big) == with_table
    )  # byte-identical: the replay hash is blind to it
