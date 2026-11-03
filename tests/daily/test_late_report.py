"""LATE_REPORT shadow (nba.daily.late_report): a player ruled OUT between T-60 and T-15 moves the
shadow's rows and not production's; report-as-of discipline; dedup; refuse-after-tip; settle
eligibility; the CLI wiring is fail-soft; the replay oracle cannot see the shadow."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import polars as pl
import pytest

from nba.daily import __main__ as cli
from nba.daily import late_report as lr
from nba.daily.canonical_dump import canonical_csv
from nba.daily.injury import to_report_dt
from nba.daily.predict import context_prop_predictions, load_elo_params, slate_report_outs
from nba.daily.settle import LATE_MODEL, settle_eligible_pending, settle_pending
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions_ex, ensure_tables
from nba.daily.t30 import T15_MODEL_NAME, run_t15
from tests.daily.conftest import add_final_box
from tests.fixtures.loader import build_fixture_db
from tests.lineups.test_t30 import (  # noqa: F401  (hist is a module-scoped fixture)
    G1,
    G2,
    P1,
    TIP,
    _fill,
    _sched,
    _store,
    hist,
)

D = TIP.date() - timedelta(days=1)  # slate date (ET) of the 7:00 pm ET tip
STAR = P1[0]  # T1's top-minutes starter
SNAP_EARLY = to_report_dt(TIP) - timedelta(minutes=90)  # 17:30 ET: before T-60
SNAP_LATE = to_report_dt(TIP) - timedelta(minutes=25)  # 18:35 ET: after T-60, before T-15
T14 = TIP - timedelta(minutes=14)  # the first 5-minute tick inside [tip-15, tip)


@pytest.fixture
def con(hist: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:  # noqa: F811
    ensure_tables(hist)
    hist.execute("DELETE FROM forward_predictions")
    hist.execute("DELETE FROM player_availability")
    for t in ("forward_late_decisions", "forward_t15_decisions", "forward_model_stamps",
              "forward_scores_elig"):  # fmt: skip
        hist.execute(f"DROP TABLE IF EXISTS {t}")
    _report(hist, SNAP_EARLY, {G1: {1007: "questionable"}, G2: {3007: "questionable"}})
    return hist


def _report(
    con: duckdb.DuckDBPyConnection, as_of: datetime, by_game: dict[str, dict[int, str]]
) -> None:
    """One official-report snapshot stamped ``as_of`` (naive ET) for the given games."""
    ins = "INSERT INTO player_availability VALUES (?, ?, ?, ?, 'x', 'nba_official_report', ?)"
    for gid, players in by_game.items():
        for pid, status in players.items():
            con.execute(ins, [pid, as_of, gid, status, as_of])


def _late(con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection | None, now: datetime,
          tmp: Path) -> lr.LateSummary:  # fmt: skip
    return lr.run_late_shadow(
        con, lcon, D, schedule_fn=_sched, now=now, model_cache=tmp / "late",
        seed_ctx_cache=tmp / "prod", seed_t30_cache=tmp / "t30",
    )  # fmt: skip


def _rows(con: duckdb.DuckDBPyConnection, model: str) -> list[tuple]:  # type: ignore[type-arg]
    return con.execute(
        "SELECT game_id, player_id, target, made_at, tipoff, prediction FROM forward_predictions "
        "WHERE model_name = ? ORDER BY 1, 2, 3",
        [model],
    ).fetchall()


def _production(con: duckdb.DuckDBPyConnection, made_at: datetime, tmp: Path) -> pl.DataFrame:
    """Production's props for the slate under its own rule (T-60 report, OUT only)."""
    out = slate_report_outs(con, [(G1, TIP), (G2, TIP)], made_at)
    res = context_prop_predictions(
        con, D, [(G1, 1610612738, 1610612755), (G2, 1610612747, 1610612744)], out,
        load_elo_params(), cache_root=tmp / "prod",
    )  # fmt: skip
    return res.primary.sort(["game_id", "player_id", "stat"])


def test_player_out_between_t60_and_t15_moves_late_rows_not_production(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    prod_before = _production(con, T14, tmp_path)
    _report(con, SNAP_LATE, {G1: {STAR: "out", 1007: "questionable"}, G2: {3007: "questionable"}})
    prod_after = _production(con, T14, tmp_path)
    assert prod_after.equals(prod_before)  # production's T-60 rule cannot see the 18:35 report
    assert STAR in prod_after.filter(pl.col("game_id") == G1)["player_id"].to_list()

    s = _late(con, None, T14, tmp_path)
    assert s.n_logged_games == 2 and s.n_rows_written > 0 and s.run_id.startswith("late_")
    assert "seeded ctx cache" in " ".join(s.notes)  # the production cache was copied, not refit
    rows = _rows(con, lr.LATE_MODEL_NAME)
    g1 = [r for r in rows if r[0] == G1]
    assert STAR not in {r[1] for r in g1}  # the late report excluded him
    for _g, _p, _t, made_at, tip, pred in rows:
        p = json.loads(pred)
        assert made_at == T14 < tip
        assert p["comparison_only"] is True and p["late_report"] is True
        assert p["report_statuses"] == ["out", "doubtful"] and p["lineups_known"] is False
        assert len(p["q_grid"]) == 19 and "p_ge_full" in p
    p1 = json.loads(g1[0][5])
    assert p1["report_as_of_et"] == SNAP_LATE.isoformat() and p1["n_report_excluded"] == 1
    # teammates: same players as production minus the star; nothing written under other names
    late_keys = {(r[0], r[1], r[2]) for r in rows}
    prod_keys = {
        (r["game_id"], r["player_id"], r["stat"]) for r in prod_after.iter_rows(named=True)
    }
    assert late_keys == {k for k in prod_keys if k[1] != STAR}
    assert con.execute(
        "SELECT count(*) FROM forward_predictions WHERE model_name <> ?", [lr.LATE_MODEL_NAME]
    ).fetchone() == (0,)
    dec = con.execute(
        "SELECT game_id, outcome, report_as_of, report_excluded, lineups_known "
        "FROM forward_late_decisions ORDER BY 1"
    ).fetchall()
    assert dec == [(G1, "logged", SNAP_LATE, f"[{STAR}]", False),
                   (G2, "logged", SNAP_LATE, "[]", False)]  # fmt: skip
    stamp = con.execute("SELECT run_id, model_name, version FROM forward_model_stamps").fetchall()
    assert stamp == [(s.run_id, lr.LATE_MODEL_NAME, lr.LATE_VERSION)]
    # idempotent: a later tick decides nothing new
    again = _late(con, None, TIP - timedelta(minutes=9), tmp_path)
    assert again.n_already_decided == 2 and again.n_rows_written == 0
    assert len(_rows(con, lr.LATE_MODEL_NAME)) == len(rows)


def test_doubtful_excludes_and_report_after_made_at_is_never_used(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _report(con, SNAP_LATE, {G1: {STAR: "doubtful", 1001: "questionable"}})
    future = to_report_dt(T14) + timedelta(minutes=1)  # stamped after made_at (planted future)
    _report(con, future, {G1: {1002: "out"}})
    flagged, used = lr.late_report_outs(con, [(G1, TIP), (G2, TIP)], T14)
    assert flagged[G1] == {STAR} and used[G1] == SNAP_LATE  # doubtful counts; 1002 not yet known
    assert flagged[G2] == set() and used[G2] == SNAP_EARLY
    # at production's rule the same data says nobody is out (questionable only at 17:30)
    assert slate_report_outs(con, [(G1, TIP)], T14) == {G1: set()}


def test_refused_after_tipoff_and_not_yet(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert _late(con, None, TIP - timedelta(minutes=16), tmp_path).n_not_yet == 2
    s = _late(con, None, TIP, tmp_path)
    assert s.n_skipped_games == 2 and s.n_rows_written == 0
    assert (
        con.execute(
            "SELECT outcome, reason FROM forward_late_decisions ORDER BY game_id"
        ).fetchall()
        == [("skipped", "run_after_tipoff")] * 2
    )
    assert not _rows(con, lr.LATE_MODEL_NAME)


def test_store_refuses_a_late_batch_with_any_row_at_or_after_tip(
    con: duckdb.DuckDBPyConnection,
) -> None:
    pred = ForwardPrediction(G1, TIP, lr.LATE_MODEL_NAME, lr.LATE_VERSION, "pts", {"mean": 1.0}, 1)
    with pytest.raises(LeakageError):
        append_predictions_ex(con, "late_x", TIP, [pred], dedup=True)
    assert not _rows(con, lr.LATE_MODEL_NAME)


def test_dedup_on_retry_after_a_crash_before_the_decision_row(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    s1 = _late(con, None, T14, tmp_path)
    n = len(_rows(con, lr.LATE_MODEL_NAME))
    assert n == s1.n_rows_written > 0
    con.execute("DROP TABLE forward_late_decisions")  # as if the run died after its rows
    s2 = _late(con, None, TIP - timedelta(minutes=9), tmp_path)
    assert s2.n_logged_games == 2 and s2.n_rows_written == 0 and s2.n_rows_deduped == n
    assert len(_rows(con, lr.LATE_MODEL_NAME)) == n


def test_lineup_games_use_the_t15_snapshot_and_differ_from_t15_only_by_report(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=20))
    t15 = run_t15(con, lcon, D, schedule_fn=_sched, now=T14, model_cache=tmp_path / "t30")
    assert t15.n_logged_games == 2
    # no report change: the late arm reproduces the T-15 arm's numbers on every key
    s = _late(con, lcon, T14, tmp_path)
    assert s.n_lineup_games == 2 and "seeded t30 cache" in " ".join(s.notes)
    keys = ("mean", "std", "q_grid", "p_play", "snapshot_id", "snapshot_fetched_at")
    a = {(r[0], r[1], r[2]): json.loads(r[5]) for r in _rows(con, T15_MODEL_NAME)}
    b = {(r[0], r[1], r[2]): json.loads(r[5]) for r in _rows(con, lr.LATE_MODEL_NAME)}
    assert a.keys() == b.keys()
    for k in a:
        assert {f: a[k][f] for f in keys} == {f: b[k][f] for f in keys}
        assert b[k]["lineups_known"] is True


def test_settle_scores_late_rows_with_crps_and_eligibility() -> None:
    con = build_fixture_db(":memory:")
    ensure_tables(con)
    tip = datetime(2026, 10, 28, 23, 30)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES ('GL', '2026-10-28', 2026, 1, 2, 110, 100)"
    )
    add_final_box(con, "GL")
    grid = [float(i) for i in range(19)]
    surv = {"c": [200 - 5 * k for k in range(1, 41)], "n": 200}  # P(Y >= k), tail reaches 0
    pred = {"mean": 9.0, "std": 3.0, "q_grid": grid, "p_ge_full": surv}
    for model, made in ((LATE_MODEL, tip - timedelta(minutes=13)),
                        ("props_context_residual", tip - timedelta(minutes=70))):  # fmt: skip
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["r", made, "GL", tip, model, "v", "pts", 70000, json.dumps(pred)],
        )
    settle_pending(con, datetime(2026, 10, 29, 12))
    got = con.execute(
        "SELECT status, y, crps IS NOT NULL FROM forward_scores WHERE model_name = ?", [LATE_MODEL]
    ).fetchall()
    assert got == [("scored", 22.0, True)]  # add_final_box: player 70000 scores 110 / 5
    elig = con.execute(
        "SELECT model_name, lead_min, status FROM forward_scores_elig ORDER BY 1"
    ).fetchall()
    assert elig == [("props_context_residual", 60, "scored"),
                    ("props_context_residual_late", 0, "scored")]  # fmt: skip
    assert settle_eligible_pending(con, datetime(2026, 10, 29, 13)) == {
        "scored": 0, "dnp": 0, "unscorable": 0,
    }  # fmt: skip


def test_cli_flag_is_fail_soft_and_alerts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a15 = cli._parser().parse_args(["run-t15", "--date", "2026-10-28", "--log-late-shadow"])
    assert a15.log_late_shadow is True
    assert cli._parser().parse_args(["run-t15", "--date", "2026-10-28"]).log_late_shadow is False

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("planted\nfailure")

    monkeypatch.setattr(lr, "run_late_shadow", boom)
    args = SimpleNamespace(
        date=D, now=None, schedule_from_db=False, schedule_nba_api=False, roster_source="recent",
        rate_limit_s=2.5, roster_dir=None,
    )  # fmt: skip
    lcon = duckdb.connect(":memory:")
    assert cli._late_shadow(duckdb.connect(":memory:"), lcon, args, ops_dir=tmp_path) is False  # type: ignore[arg-type]
    alert = (tmp_path / "ALERTS.md").read_text()
    assert "shadow failed (T-15 rows unaffected): RuntimeError: planted failure" in alert
    assert alert.count("\n") == 1


def test_replay_never_runs_the_late_shadow_and_the_dump_would_see_it(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Oracle exclusion, mirroring the T-15 arm: the replay calls neither run-t15 nor this module,
    and the canonical dump is not filtered by model, so a replay that ran it would differ."""
    src = Path(cli.__file__).with_name("replay_season.py").read_text()
    mods: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert not any("late_report" in m for m in mods)
    assert "run_t15" not in src and "run-t15" not in src and "late_report" not in src
    assert lr.LATE_MODEL_NAME not in src
    before = canonical_csv(con)
    _late(con, None, T14, tmp_path)
    assert canonical_csv(con) != before


def test_late_report_reaches_the_teammate_features_and_production_does_not(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture's history has no report rows, so its fitted models cannot react to a teammate
    flag (teammate means stay equal there); this pins the wiring instead: the slate game's
    ``flagged`` set handed to the feature builder holds the newly-out star for the late arm only."""
    import nba.props.forward as fwd

    seen: list[set[int]] = []
    real = fwd.build_features

    def spy(*a: object, **k: object) -> pl.DataFrame:
        flagged = a[3]
        assert isinstance(flagged, dict)
        seen.append(set(flagged.get(G1, set())))
        return real(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(fwd, "build_features", spy)
    _report(con, SNAP_LATE, {G1: {STAR: "out"}, G2: {3007: "questionable"}})
    _production(con, T14, tmp_path)
    _late(con, None, T14, tmp_path)
    assert seen == [set(), {STAR}]
