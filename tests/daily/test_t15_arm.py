"""Sibling T-15 arm (docs/prereg/FORWARD_T15_2026_27.md): equivalence of the T-30 path, cutoff
discipline, the 5-minute tick, own tables, settle eligibility, checkpoint family and kill rules."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from nba.daily import checkpoint as ck
from nba.daily.__main__ import _parser
from nba.daily.settle import T15_MODEL, settle_eligible_pending, settle_pending
from nba.daily.store import ensure_tables
from nba.daily.t30 import (
    T15_MODEL_NAME,
    T30_MODEL_NAME,
    T30Summary,
    run_t15,
    run_t30,
)
from nba.lineups.__main__ import main as lineups_main
from nba.lineups.source import CONFIRMED, EXPECTED
from nba.lineups.store import T15_LEAD_MINUTES, T30_LEAD_MINUTES, connect_lineups, upsert_tip
from tests.daily.conftest import add_final_box, align_all
from tests.daily.test_checkpoint import SETTLED_AT, build
from tests.fixtures.loader import build_fixture_db
from tests.lineups.test_t30 import (  # noqa: F401  (hist is a module-scoped fixture)
    G1,
    G2,
    TIP,
    _fill,
    _sched,
    _store,
    hist,
)

D = date(2026, 2, 21)


@pytest.fixture
def con(hist: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:  # noqa: F811
    ensure_tables(hist)
    hist.execute("DELETE FROM forward_predictions")
    hist.execute("DROP TABLE IF EXISTS forward_t30_decisions")
    hist.execute("DROP TABLE IF EXISTS forward_t15_decisions")
    return hist


def _t15(
    con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection, now: datetime, tmp: Path
) -> T30Summary:
    return run_t15(con, lcon, D, schedule_fn=_sched, now=now, model_cache=tmp / "cache")


def _t30(
    con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection, now: datetime, tmp: Path,
    **kw: object,
) -> T30Summary:  # fmt: skip
    return run_t30(con, lcon, D, schedule_fn=_sched, now=now, model_cache=tmp / "cache", **kw)  # type: ignore[arg-type]


def _rows(con: duckdb.DuckDBPyConnection, model: str) -> list[tuple]:  # type: ignore[type-arg]
    return con.execute(
        "SELECT run_id, game_id, player_id, target, made_at, tipoff, version, prediction "
        "FROM forward_predictions WHERE model_name = ? ORDER BY 2, 3, 4",
        [model],
    ).fetchall()


def test_constants() -> None:
    assert T15_LEAD_MINUTES == 15 and T30_LEAD_MINUTES == 30


def test_t30_path_identical_with_explicit_default_kwargs(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Equivalence: run_t30 with today's values spelled out reproduces the bare call exactly."""
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=40))
    now = TIP - timedelta(minutes=29)
    s1 = _t30(con, lcon, now, tmp_path)
    base_rows = _rows(con, T30_MODEL_NAME)
    base_dec = con.execute("SELECT * FROM forward_t30_decisions ORDER BY 1").fetchall()
    assert s1.n_logged_games == 2 and base_rows
    con.execute("DELETE FROM forward_predictions")
    con.execute("DROP TABLE forward_t30_decisions")
    s2 = _t30(
        con, lcon, now, tmp_path,
        lead_minutes=30, model_name="props_context_residual_t30", version="ctxres-v1-t30",
        decisions_table="forward_t30_decisions", run_prefix="t30",
        no_snapshot_reason="no_snapshot_before_t30",
    )  # fmt: skip
    assert s2 == s1
    assert _rows(con, T30_MODEL_NAME) == base_rows
    assert con.execute("SELECT * FROM forward_t30_decisions ORDER BY 1").fetchall() == base_dec


def test_t15_logs_from_pre_cutoff_snapshot_in_own_table(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=20))  # confirmed at T-20: after the T-30 cutoff
    s = _t15(con, lcon, TIP - timedelta(minutes=14), tmp_path)
    assert s.n_logged_games == 2 and s.run_id.startswith("t15_")
    rows = _rows(con, T15_MODEL_NAME)
    assert rows and {r[6] for r in rows} == {"ctxres-v1-t15"}
    for _run, _g, _p, _t, made_at, tip, _v, pred in rows:
        assert made_at < tip
        p = json.loads(pred)
        assert datetime.fromisoformat(p["snapshot_fetched_at"]) < tip - timedelta(minutes=15)
        assert p["comparison_only"] is True and p["lineups_known"] is True
    assert con.execute(
        "SELECT game_id, outcome FROM forward_t15_decisions ORDER BY 1"
    ).fetchall() == [
        (G1, "logged"),
        (G2, "logged"),
    ]
    # nothing in the T-30 table or under the T-30 name; the T-30 arm would have skipped these
    assert not _rows(con, T30_MODEL_NAME)
    assert con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'forward_t30_decisions'"
    ).fetchone() == (0,)
    assert _t15(con, lcon, TIP - timedelta(minutes=10), tmp_path).n_already_decided == 2
    assert len(_rows(con, T15_MODEL_NAME)) == len(rows)  # idempotent


def test_planted_future_snapshot_at_tip_minus_14_is_never_used(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=20), status=EXPECTED)
    _fill(lcon, TIP - timedelta(minutes=14), status=CONFIRMED)  # newer than the cutoff
    s = _t15(con, lcon, TIP - timedelta(minutes=10), tmp_path)
    assert s.n_logged_games == 0 and not _rows(con, T15_MODEL_NAME)
    reasons = dict(con.execute("SELECT game_id, reason FROM forward_t15_decisions").fetchall())
    assert reasons[G1].startswith("lineup_not_confirmed:home=Expected,away=Expected")


def test_no_snapshot_reason_differs_only_for_the_new_arm(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=14))
    _t15(con, lcon, TIP - timedelta(minutes=10), tmp_path)
    assert {r[0] for r in con.execute("SELECT reason FROM forward_t15_decisions").fetchall()} == {
        "no_snapshot_before_cutoff"
    }
    lcon2 = _store(tmp_path / "x") if (tmp_path / "x").mkdir() is None else lcon
    _fill(lcon2, TIP - timedelta(minutes=25))
    _t30(con, lcon2, TIP - timedelta(minutes=10), tmp_path)
    assert {r[0] for r in con.execute("SELECT reason FROM forward_t30_decisions").fetchall()} == {
        "no_snapshot_before_t30"
    }


def test_five_minute_tick_reaches_the_t15_deadline(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=18))
    assert _t15(con, lcon, TIP - timedelta(minutes=17), tmp_path).n_not_yet == 2
    s = _t15(con, lcon, TIP - timedelta(minutes=12), tmp_path)  # next tick of a 300 s cadence
    assert s.n_logged_games == 2
    ages = [json.loads(r[7])["snapshot_age_min_before_tip"] for r in _rows(con, T15_MODEL_NAME)]
    assert min(ages) >= 15.0 and max(ages) <= 20.0  # 15-20 min effective information age


def test_run_after_tipoff_is_counted_as_skipped(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=30))
    s = _t15(con, lcon, TIP + timedelta(minutes=1), tmp_path)
    assert s.n_skipped_games == 2 and s.n_rows_written == 0
    assert (
        con.execute("SELECT outcome, reason FROM forward_t15_decisions").fetchall()
        == [("skipped", "run_after_tipoff")] * 2
    )


def test_due_lead_minutes(tmp_path: Path) -> None:
    db = tmp_path / "l.duckdb"
    lcon = connect_lineups(db)
    upsert_tip(lcon, G1, D, TIP, "schedule", TIP - timedelta(days=1))
    lcon.close()
    base = ["--db-path", str(db), "due"]
    at = (TIP - timedelta(minutes=20)).isoformat()
    assert lineups_main([*base, "--now", at]) == 0  # default lead 30: inside
    assert lineups_main([*base, "--lead-minutes", "15", "--now", at]) == 1
    at14 = (TIP - timedelta(minutes=14)).isoformat()
    assert lineups_main([*base, "--lead-minutes", "15", "--now", at14]) == 0
    assert lineups_main([*base, "--lead-minutes", "15", "--now", TIP.isoformat()]) == 1


def test_run_t15_subcommand_has_the_run_t30_options() -> None:
    p = _parser()
    a30 = p.parse_args(["run-t30", "--date", "2026-10-28", "--roster-source", "official"])
    a15 = p.parse_args(["run-t15", "--date", "2026-10-28", "--roster-source", "official"])
    assert {k: v for k, v in vars(a30).items() if k != "command"} == {
        k: v for k, v in vars(a15).items() if k != "command"
    }
    assert a15.command == "run-t15"


def test_settle_eligibility_for_t15_rows() -> None:
    con = build_fixture_db(":memory:")
    ensure_tables(con)
    tip = datetime(2026, 10, 28, 23, 30)
    for gid, made_min, snap_min in (("G_ok", 12, 16), ("G_late", 12, 14), ("G_none", 12, None)):
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts,"
            " away_pts) VALUES (?, ?, 2026, 1, 2, 110, 100)",
            [gid, date(2026, 10, 28)],
        )
        add_final_box(con, gid)
        pred: dict[str, object] = {"p_home": 0.6}
        if snap_min is not None:
            pred["snapshot_fetched_at"] = (tip - timedelta(minutes=snap_min)).isoformat()
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["r", tip - timedelta(minutes=made_min), gid, tip, T15_MODEL, "v1", "win_prob_home",
             -1, json.dumps(pred)],
        )  # fmt: skip
    assert settle_eligible_pending(con, datetime(2026, 10, 29, 12))["scored"] == 1
    assert con.execute("SELECT game_id, lead_min FROM forward_scores_elig").fetchall() == [
        ("G_ok", 15)
    ]


# ------------------------------------------------------------------------------ checkpoint


def test_t15_family_is_separate_with_its_own_bh_of_four() -> None:
    assert ck.FAMILY_T15 == ("t15_pts", "t15_reb", "t15_ast", "t15_fg3m")
    assert not set(ck.FAMILY_T15) & set(ck.FAMILY) and len(ck.FAMILY) == 9
    p = {"t15_pts": 0.001, "t15_reb": 0.02, "t15_ast": 0.2, "t15_fg3m": 0.9}
    got = ck.bh_adjust(p, ck.FAMILY_T15)
    want = multipletests([p[k] for k in ck.FAMILY_T15], method="fdr_bh")[1]
    assert np.allclose([got[k] for k in ck.FAMILY_T15], want)
    with pytest.raises(KeyError):
        ck.bh_adjust(p)  # the T-30 family does not accept T-15 keys
    assert ck.MIN_GATE["t15_pts"] == (60, 5000) and ck.MIN_GATE["t15_fg3m"] == (120, 10000)


def _res(**kw: float) -> dict:  # type: ignore[type-arg]
    return {"point": -0.02, "lo": -0.03, "hi": -0.01, "n_dates": 30, **kw}


def test_t15_l30_kill_only_rules() -> None:
    share_ok = {"n_decisions": 100, "logged_wilson": [0.7, 0.9], "skipped_reasons": {}}
    g = {"leak_ok": True}
    assert (
        ck.decide("t15_pts", "L30", _res(), 0.0, g, share_ok)[0] == "KEEP_SHADOWING"
    )  # never promoted
    # (b) harm with >= 25 dates; not with fewer
    harm = _res(point=0.01, lo=0.002, hi=0.02)
    assert ck.decide("t15_pts", "L30", harm, 1.0, g, share_ok)[0] == "DROP"
    assert (
        ck.decide("t15_pts", "L30", {**harm, "n_dates": 24}, 1.0, g, share_ok)[0]
        == "KEEP_SHADOWING"
    )
    # (c) share upper bound < 0.5 needs >= 60 decided games
    low = {"n_decisions": 60, "logged_wilson": [0.2, 0.45], "skipped_reasons": {}}
    assert ck.decide("t15_pts", "L30", _res(), 0.0, g, low)[0] == "DROP"
    assert ck.decide("t15_pts", "L30", _res(), 0.0, g, {**low, "n_decisions": 59})[0] != "DROP"
    # (d) run_after_tipoff > 10% of decisions
    late = {
        "n_decisions": 100,
        "logged_wilson": [0.7, 0.9],
        "skipped_reasons": {"run_after_tipoff": 11},
    }
    assert ck.decide("t15_pts", "L30", _res(), 0.0, g, late)[0] == "DROP"
    late["skipped_reasons"] = {"run_after_tipoff": 10}
    assert ck.decide("t15_pts", "L30", _res(), 0.0, g, late)[0] == "KEEP_SHADOWING"
    # (a) a leak guard failure is flagged for a human, not decided here
    assert ck.decide("t15_pts", "L30", _res(), 0.0, {"leak_ok": False}, share_ok)[0] == "AUDIT"
    # L14 is monitoring only, as for the T-30 arm
    assert ck.decide("t15_pts", "L14", harm, 0.0, g, share_ok)[0] == "MONITOR"


def test_t15_leak_guard_uses_15_minutes_and_share_gate() -> None:
    con = build(n_dates=3, n_games=1, n_players=4, seed=1)
    align_all(con)
    settle_pending(con, SETTLED_AT)
    pairs, _ = ck.paired_deltas(con, ck.ARM_T30, ck.PROD, "pts")
    import polars as pl

    snap = pl.col("tipoff") - timedelta(minutes=20)
    ok = pairs.with_columns(snapshot_fetched_at=snap)
    bad = pairs.with_columns(snapshot_fetched_at=pl.col("tipoff") - timedelta(minutes=14))
    assert ck._guards("t15_pts", ok, "delta_int", None, 0.9)["leak_ok"] is True
    assert ck._guards("t15_pts", bad, "delta_int", None, 0.9)["leak_ok"] is False
    # 20 minutes is a leak for the T-30 key but fine for the T-15 key
    assert ck._guards("t30_pts", ok, "delta_int", None, 0.9)["leak_ok"] is False
    assert ck._guards("t15_pts", ok, "delta_int", None, 0.5)["logged_share_ok"] is False


def test_evaluate_checkpoint_reports_the_t15_family_and_head_to_head(tmp_path: Path) -> None:
    con = build(n_dates=4, n_games=2, n_players=5, seed=2)
    con.execute(
        "CREATE TABLE IF NOT EXISTS forward_t15_decisions AS SELECT * FROM forward_t30_decisions"
    )
    con.execute(
        "INSERT INTO forward_predictions SELECT 't15-' || run_id, made_at, game_id, tipoff, ?, ?, "
        "target, player_id, prediction FROM forward_predictions WHERE model_name = ?",
        [T15_MODEL, "ctxres-v1-t15", ck.ARM_T30],
    )
    align_all(con)
    settle_pending(con, SETTLED_AT)
    settle_eligible_pending(con, SETTLED_AT)
    snap = ck.evaluate_checkpoint(
        con, "L14", out_dir=tmp_path, B=100, lineups_db=tmp_path / "none.duckdb", descriptive=True
    )
    assert snap["family_t15"] == list(ck.FAMILY_T15) and len(snap["bh_adjusted_t15"]) == 4
    assert len(snap["bh_adjusted"]) == 9  # the T-30 family is untouched
    r = snap["results"]["t15_pts"]
    assert r["n"] > 0 and r["outcome"] == "MONITOR" and r["eligible"] is False
    assert snap["t15_share"]["n_decisions"] > 0
    h = snap["t15_vs_t30"]["pts"]
    assert h["n"] == r["n"] and abs(h["point"]) < 1e-12  # identical rows: zero head-to-head
    md = ck.render_markdown(snap)
    assert "t15_pts" in md and "T-15 logged share" in md
