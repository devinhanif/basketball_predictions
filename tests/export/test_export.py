"""nba.export on a fixture DB: schema, atomic write, read-only source, holdout refusal,
planted future rows, and the parlay artifact passed through verbatim. No network."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

import nba.export.build as build_mod
from nba.daily import stamps
from nba.export import (
    ExportRefused,
    Window,
    explainer,
    forward_predictions,
    parlay_of_day,
    resolve_seasons,
    settled_scores,
)
from nba.export.__main__ import main
from nba.export.build import DB_NAME, MANIFEST_NAME, PARLAY_NAME, TABLES, build_export
from nba.export.reader import open_read_only
from tests.fixtures.loader import build_fixture_db

DAY = date(2026, 10, 28)
TIP = datetime(2026, 10, 28, 23, 30)  # naive UTC, 7:30pm ET
G_LIVE, G_PREV, G_FUTURE = "0022600001", "0022600000", "0022600009"
G_HOLD, G_HOLD_UNKNOWN, G_PRESEASON = "0022500100", "0022500101", "0012600038"
Q = [float(i) for i in range(2, 21)]


def _pred(mean: float, **kw: Any) -> str:
    return json.dumps(
        {"mean": mean, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q10": 4.0,
         "q50": mean, "q90": 14.0, "q_grid": Q, "p_ge": {"10": 0.4},
         "p_ge_full": {"c": [198, 150, 0], "n": 199}, **kw}
    )  # fmt: skip


def _win(p: float) -> str:
    return json.dumps(
        {"p_home": p, "home_team": 1610612738, "away_team": 1610612755, "primary": True,
         "fallback_reason": None}
    )  # fmt: skip


@pytest.fixture
def src(tmp_path: Path) -> Path:
    """A file DB with fixture games plus planted live, future, holdout and preseason rows."""
    path = tmp_path / "nba.duckdb"
    con = build_fixture_db(path)
    con.execute(stamps.DDL)
    games = [
        (G_PREV, date(2026, 10, 27), 2026, 1610612738, 1610612755, 110, 100),
        (G_LIVE, DAY, 2026, 1610612738, 1610612755, None, None),
        (G_FUTURE, DAY + timedelta(days=3), 2026, 1610612738, 1610612755, None, None),
        (G_HOLD, date(2026, 1, 5), 2025, 1610612738, 1610612755, 111, 99),
    ]
    con.executemany("INSERT INTO games (game_id, game_date, season, home_team, away_team, "
                    "home_pts, away_pts) VALUES (?,?,?,?,?,?,?)", games)  # fmt: skip
    run_a, run_b, run_late = "r_early", "r_t30", "r_late"
    made_a, made_b, made_late = (
        TIP - timedelta(minutes=120),
        TIP - timedelta(minutes=30),
        TIP + timedelta(minutes=10),
    )
    fp = "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)"

    def row(
        run: str,
        made: datetime,
        gid: str,
        tip: datetime,
        model: str,
        target: str,
        pid: int,
        pred: str,
    ) -> tuple[Any, ...]:
        return (run, made, gid, tip, model, "v1", target, pid, pred)

    elo, ctx, win = "rung0_mov_elo", "props_context_residual", "win_prob_home"
    rows = [
        row(run_a, made_a, G_LIVE, TIP, elo, win, -1, _win(0.61)),
        row(run_a, made_a, G_LIVE, TIP, ctx, "pts", 1000, _pred(12.5)),
        row(run_b, made_b, G_LIVE, TIP, ctx, "pts", 1000, _pred(13.5)),
        row(run_late, made_late, G_LIVE, TIP, ctx, "pts", 1000, _pred(99.0)),
        row(
            run_a,
            made_a - timedelta(days=1),
            G_PREV,
            TIP - timedelta(days=1),
            elo,
            win,
            -1,
            _win(0.5),
        ),
        row(run_a, made_a, G_FUTURE, TIP + timedelta(days=3), elo, win, -1, _win(0.7)),
        row(run_a, made_a, G_HOLD, TIP, elo, win, -1, _win(0.9)),
        row(run_a, made_a, G_HOLD_UNKNOWN, TIP, ctx, "pts", 7, _pred(40.0)),
        row(run_a, made_a, G_PRESEASON, TIP, ctx, "pts", 8, _pred(30.0)),
    ]
    con.executemany(fp, rows)
    con.execute(
        f"INSERT INTO {stamps.TABLE} VALUES ('r_early','props_context_residual','v1',?,'abc123',"
        "'fitted state','dv0123456789',DATE '2026-10-27',5000,'deadbeef',false,?)",
        [made_a, made_a],
    )
    sc = ("rung0_mov_elo", "v1", "win_prob_home", -1)
    con.executemany(
        "INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                TIP,
                G_PREV,
                date(2026, 10, 27),
                2026,
                *sc,
                made_a,
                1.0,
                0.5,
                0.69,
                0.25,
                None,
                "scored",
            ),
            (TIP, G_HOLD, date(2026, 1, 5), 2025, *sc, made_a, 1.0, 0.9, 0.1, 0.01, None, "scored"),
        ],
    )
    con.executemany(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, "
        "starter) VALUES (?,?,?,?,?,?,?,?,?)",
        [(G_PREV, 1000, 1610612738, 31.0, 20, 5, 3, 2, True),
         (G_PREV, 1001, 1610612738, None, 0, 0, 0, 0, False),
         (G_HOLD, 1000, 1610612738, 33.0, 44, 5, 3, 2, True)],
    )  # fmt: skip
    con.execute(
        "INSERT INTO player_availability VALUES (1000, ?, ?, 'questionable', 'Left Knee', "
        "'nba_official', ?)",
        [datetime(2026, 10, 28, 12, 0), G_LIVE, datetime(2026, 10, 28, 12, 1)],
    )
    con.close()
    return path


def _build(src: Path, out: Path, **kw: Any) -> dict[str, Any]:
    kw.setdefault("parlay_dir", out.parent / "no_parlay")
    return build_export(
        src, out, DAY, git=lambda: {"git_sha": "cafe", "git_dirty": False},
        version=lambda: "dv0123456789", now=lambda: datetime(2026, 10, 29, 9, 0), **kw,
    )  # fmt: skip


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_schema_manifest_and_content(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    m = _build(src, out)
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    for t, cols in TABLES.items():
        got = [r[0] for r in con.execute(f'DESCRIBE "{t}"').fetchall()]
        assert got == [c for c, _ in cols], t
    assert m["files"][DB_NAME]["sha256"] == _sha(out / DB_NAME)
    assert m["files"][PARLAY_NAME]["sha256"] == _sha(out / PARLAY_NAME)
    assert json.loads((out / MANIFEST_NAME).read_text()) == json.loads(json.dumps(m))
    assert (m["data_version"], m["git_sha"], m["made_at"]) == (
        "dv0123456789",
        "cafe",
        "2026-10-29T09:00:00Z",
    )
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == "1" and meta["made_at"] == m["made_at"]
    # the daily run's forecast grid and the model stamp survive intact
    row = con.execute(
        "SELECT q_grid, p_ge_full_counts, p_ge_full_n, stamp_weights_sha256, stamp_data_version, "
        "stamp_git_sha, lead_minutes, is_latest, mean FROM prop_grids WHERE model_name = "
        "'props_context_residual' AND run_id = 'r_early'"
    ).fetchone()
    assert row == (Q, [198, 150, 0], 199, "abc123", "dv0123456789", "deadbeef", 120.0, False, 12.5)
    assert con.execute("SELECT is_latest FROM prop_grids WHERE run_id = 'r_t30'").fetchone() == (
        True,
    )
    assert con.execute(
        "SELECT p_home, is_latest FROM win_probs WHERE game_id = ?", [G_LIVE]
    ).fetchone() == (0.61, True)
    assert con.execute("SELECT count(*) FROM model_stamps").fetchone() == (1,)
    # settled scores and the box (DNP explicit: NULL minutes, zero stats)
    assert con.execute("SELECT game_id, y FROM settled_scores").fetchall() == [(G_PREV, 1.0)]
    assert con.execute("SELECT minutes, pts FROM box_scores WHERE player_id = 1001").fetchone() == (
        None,
        0,
    )
    assert con.execute("SELECT status, reason FROM injury_report").fetchone() == (
        "questionable",
        "Left Knee",
    )
    con.close()


def test_holdout_and_other_seasons_never_exported(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    m = _build(src, out)
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    for t, col in [("games", "game_id"), ("win_probs", "game_id"), ("prop_grids", "game_id"),
                   ("settled_scores", "game_id"), ("box_scores", "game_id"),
                   ("injury_report", "game_id")]:  # fmt: skip
        ids = {r[0] for r in con.execute(f'SELECT DISTINCT "{col}" FROM {t}').fetchall()}
        assert not ids & {G_HOLD, G_HOLD_UNKNOWN, G_PRESEASON}, t
    assert {r[0] for r in con.execute("SELECT DISTINCT season FROM games").fetchall()} == {2026}
    assert con.execute("SELECT count(*) FROM prop_grids WHERE player_id IN (7, 8)").fetchone() == (
        0,
    )
    assert con.execute("SELECT count(*) FROM box_scores WHERE pts = 44").fetchone() == (0,)
    assert m["seasons"] == [2026]
    con.close()


@pytest.mark.parametrize("bad", [[2025], [2026, 2025], [2027], [2024, 2025]])
def test_resolve_seasons_refuses(bad: list[int]) -> None:
    with pytest.raises(ExportRefused):
        resolve_seasons(bad)


def test_refusal_writes_nothing_and_cli_exits_3(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    with pytest.raises(ExportRefused):
        _build(src, out, seasons=[2025])
    assert not out.exists()
    rc = main(
        ["build", "--date", "2026-10-28", "--out", str(out), "--db", str(src), "--season", "2025"]
    )
    assert rc == 3 and not out.exists()
    assert "REFUSED" in capsys.readouterr().err
    with pytest.raises(ExportRefused):  # the explainer for a holdout game id is refused as well
        con = open_read_only(src)
        try:
            explainer(con, G_HOLD, Window(DAY))
        finally:
            con.close()


def test_planted_future_rows_are_not_exported(src: Path) -> None:
    con = open_read_only(src)
    fwd = forward_predictions(con, Window(DAY))
    con.close()
    runs = {(p["run_id"], p["made_at"] <= p["tipoff"]) for p in fwd.prop_grids}
    assert all(ok for _, ok in runs) and "r_late" not in {r for r, _ in runs}
    assert fwd.excluded_post_tip == 1
    assert G_FUTURE not in {g["game_id"] for g in fwd.games}  # game_date after the build date
    assert all(p["made_at"] <= p["tipoff"] for p in fwd.win_probs)


def test_pre_2025_season_only_when_asked(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    m = build_export(
        src, out, date(2023, 10, 24), seasons=[2023], parlay_dir=tmp_path / "none",
        git=lambda: {"git_sha": None, "git_dirty": None}, version=lambda: None,
    )  # fmt: skip
    assert m["seasons"] == [2023] and m["row_counts"]["box_scores"] > 0
    con = open_read_only(src)
    assert {s["season"] for s in settled_scores(con, Window(DAY))} == {2026}  # default is live only
    assert {s["season"] for s in settled_scores(con, Window(date(2023, 12, 1), 7, (2023,)))} <= {
        2023
    }
    con.close()
    out_con = duckdb.connect(str(tmp_path / "out" / DB_NAME), read_only=True)
    assert out_con.execute("SELECT count(*) FROM box_scores WHERE season <> 2023").fetchone() == (
        0,
    )
    out_con.close()


def test_source_is_read_only_and_unchanged(src: Path, tmp_path: Path) -> None:
    before = _sha(src)
    _build(src, tmp_path / "out")
    assert _sha(src) == before
    con = open_read_only(src)
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE nope (i INT)")
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM forward_predictions")
    con.close()


def test_output_cannot_be_the_source(src: Path) -> None:
    # an out dir whose export file would be the source file is refused before any read
    clash = src.parent / DB_NAME
    src.rename(clash)
    with pytest.raises(ExportRefused):
        _build(clash, clash.parent)


def test_atomic_write_keeps_old_files_on_failure(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    _build(src, out)
    before = {n: _sha(out / n) for n in (DB_NAME, PARLAY_NAME, MANIFEST_NAME)}
    real: Callable[..., int] = build_mod._insert

    def boom(con: Any, table: str, rows: Any) -> int:
        if table == "win_probs":
            raise RuntimeError("disk full")
        return real(con, table, rows)

    monkeypatch.setattr(build_mod, "_insert", boom)
    with pytest.raises(RuntimeError):
        _build(src, out)
    assert {n: _sha(out / n) for n in before} == before  # yesterday's files untouched
    assert sorted(p.name for p in out.iterdir()) == sorted(before)  # no temp or wal left behind
    monkeypatch.setattr(build_mod, "_insert", real)
    _build(src, out)  # and a later build succeeds, leaving only the three files
    assert sorted(p.name for p in out.iterdir()) == sorted(before)


def test_no_stamp_table_is_fine(src: Path, tmp_path: Path) -> None:
    w = duckdb.connect(str(src))
    w.execute(f"DROP TABLE {stamps.TABLE}")
    w.close()
    m = _build(src, tmp_path / "out")
    assert m["row_counts"]["model_stamps"] == 0 and m["row_counts"]["prop_grids"] == 2


def test_explainer_returns_stored_facts(src: Path) -> None:
    con = open_read_only(src)
    e = explainer(con, G_LIVE, Window(DAY))
    prev = explainer(con, G_PREV, Window(DAY))
    con.close()
    assert e["game"]["final"] is False and e["box"] == []
    assert e["injury_report"]["lines"][0]["status"] == "questionable"
    assert {w["model_name"] for w in e["win_probs"]} == {"rung0_mov_elo"}
    assert prev["game"]["final"] and prev["game"]["home_pts"] == 110
    assert {b["player_id"] for b in prev["box"]} == {1000, 1001}


def test_parlay_artifact_is_verbatim(tmp_path: Path) -> None:
    import nba.export.parlay as pm
    from nba.parlay import ev

    assert pm.NO_POSITIVE_EV == ev.NO_POSITIVE_EV and pm.POSITIVE_EV == ev.POSITIVE_EV
    d = tmp_path
    assert parlay_of_day(d, "2026-10-28") | {} == {
        "date": "2026-10-28",
        "source": "evaluate_2026-10-28.json",
        "status": "not_run",
        "verdict": None,
        "n_evaluated": 0,
        "n_positive": 0,
        "records": [],
    }  # absence is not a verdict
    (d / "evaluate_2026-10-28.json").write_text("[]")
    p = parlay_of_day(d, "2026-10-28")
    assert (p["status"], p["verdict"], p["n_evaluated"]) == ("evaluated", "no_positive_ev_found", 0)
    recs = [
        {"ticker": "A", "side": "yes", "verdict": "no_positive_ev_found", "raw_ev_low": -0.2},
        {"ticker": "B", "side": "no", "verdict": ev.POSITIVE_EV, "raw_ev_low": 0.03},
    ]  # fmt: skip
    (d / "evaluate_2026-10-28.json").write_text(json.dumps(recs))
    p = parlay_of_day(d, "2026-10-28")
    assert p["records"] == recs and p["n_positive"] == 1  # same order, same strings
    assert p["verdict"] == "positive_ev_at_conservative_bound"
    (d / "evaluate_2026-10-28.json").write_text("{not json")
    assert parlay_of_day(d, "2026-10-28")["status"] == "unreadable"
    recs0 = [{"ticker": "A", "verdict": "no_positive_ev_found"}]
    (d / "evaluate_2026-10-28.json").write_text(json.dumps(recs0))
    assert parlay_of_day(d, "2026-10-28")["verdict"] == "no_positive_ev_found"


def test_cli_build_end_to_end(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdir = tmp_path / "parlay"
    pdir.mkdir()
    (pdir / "evaluate_2026-10-28.json").write_text("[]")
    out = tmp_path / "out"
    rc = main(
        [
            "build",
            "--date",
            "2026-10-28",
            "--out",
            str(out),
            "--db",
            str(src),
            "--parlay-dir",
            str(pdir),
        ]
    )
    assert rc == 0
    assert json.loads((out / PARLAY_NAME).read_text())["verdict"] == "no_positive_ev_found"
    assert "export 2026-10-28" in capsys.readouterr().out
