"""nba.export on a fixture DB: schema, atomic write, read-only source, holdout refusal,
planted future rows, and the parlay artifact passed through verbatim. No network."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
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
    prop_lines,
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
        "starter, stl, blk, tov) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(G_PREV, 1000, 1610612738, 31.0, 20, 5, 3, 2, True, 2, 1, 3),
         (G_PREV, 1001, 1610612738, None, 0, 0, 0, 0, False, 0, 0, 0),
         (G_HOLD, 1000, 1610612738, 33.0, 44, 5, 3, 2, True, 1, 1, 1)],
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
    assert meta["schema_version"] == "2" and meta["made_at"] == m["made_at"]
    assert m["schema_version"] == 2
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
    assert con.execute(
        "SELECT stl, blk, tov FROM box_scores WHERE player_id IN (1000, 1001) ORDER BY player_id"
    ).fetchall() == [(2, 1, 3), (0, 0, 0)]  # DNP stays explicit zeros as stored
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
                   ("injury_report", "game_id"), ("prop_lines", "game_id")]:  # fmt: skip
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
            "--odds-db",
            str(tmp_path / "no_such_odds.duckdb"),
        ]
    )
    assert rc == 0
    assert json.loads((out / PARLAY_NAME).read_text())["verdict"] == "no_positive_ev_found"
    assert "export 2026-10-28" in capsys.readouterr().out


# ---------------------------------------------------------------------- prop_lines / team ids

PRED_A, PRED_B = TIP - timedelta(minutes=120), TIP - timedelta(minutes=30)  # made_at of the runs
PTS = "player_points"


def _odds_db(path: Path, quotes: list[tuple[Any, ...]]) -> Path:
    """A capture store (the real DDL) holding ``quotes``: (game, player, market, book, side, point,
    price, received_at, kind). ``tipoff`` / ``commence_time`` are TIP for every row."""
    from nba.odds import live_close as lc

    con = duckdb.connect(str(path))
    con.execute(lc.DDL)
    for g, pid, market, book, side, point, price, recv, kind in quotes:
        con.execute(
            "INSERT INTO live_book_odds VALUES ('the_odds_api_live', ?, ?, ?, ?, ?, ?, ?, 'ev', "
            "?, ?, 'N', 'P', ?, ?, ?, ?, NULL, NULL, NULL, 'raw')",
            [g, DAY, kind, recv, recv, TIP, TIP, book, market, pid, side, point, price],
        )
    con.close()
    return path


def _quotes() -> list[tuple[Any, ...]]:
    early = TIP - timedelta(minutes=150)  # known to both runs
    mid = TIP - timedelta(minutes=60)  # known to the T-30 run only
    late = TIP - timedelta(minutes=10)  # pre-tip but after both runs were made
    post = TIP + timedelta(minutes=5)
    return [
        (G_LIVE, 1000, PTS, "draftkings", "over", 12.5, -110, early, "t180"),
        (G_LIVE, 1000, PTS, "draftkings", "over", 12.5, -125, mid, "t60"),
        (G_LIVE, 1000, PTS, "draftkings", "over", 13.5, 105, late, "t10"),
        (G_LIVE, 1000, PTS, "draftkings", "over", 12.5, -300, post, "close"),  # planted post-tip
        (G_LIVE, 1000, PTS + "_alternate", "novig", "over", 20.5, 450, early, "t180"),
        (G_LIVE, 1000, PTS + "_alternate", "novig", "over", 25.5, 900, early, "t180"),
        (G_LIVE, 1000, PTS, "kalshi", "over", 14.5, -105, mid, "t60"),
        (G_LIVE, 1000, PTS, "polymarket", "over", 12.5, 120, early, "t180"),
        (G_LIVE, 1000, PTS, "polymarket", "under", 12.5, -3233, early, "t180"),
        (G_LIVE, 1000, PTS, "polymarket_us", "under", 12.5, -900, early, "t180"),
        (G_LIVE, 1000, "player_blocks", "draftkings", "over", 0.5, -150, early, "t180"),
        (G_LIVE, 1000, "player_rebounds", "draftkings", "over", 4.5, -110, early, "t180"),
        (G_LIVE, None, PTS, "draftkings", "over", 9.5, -110, early, "t180"),  # unresolved name
        (G_HOLD, 1000, PTS, "draftkings", "over", 30.5, -110, early, "t180"),  # holdout game
        (G_FUTURE, 1000, PTS, "draftkings", "over", 30.5, -110, early, "t180"),  # outside window
    ]


def _lines_out(
    src: Path, tmp_path: Path, odds: Path
) -> tuple[dict[str, Any], duckdb.DuckDBPyConnection]:
    out = tmp_path / "out"
    m = _build(src, out, odds_db=odds)
    return m, duckdb.connect(str(out / DB_NAME), read_only=True)


def test_prop_lines_are_as_of_each_prediction_and_pre_tip(src: Path, tmp_path: Path) -> None:
    odds = _odds_db(tmp_path / "market_asof.duckdb", _quotes())
    m, con = _lines_out(src, tmp_path, odds)
    got = con.execute(
        "SELECT run_id, book, market, line, side, price_american, minutes_before_tip "
        "FROM prop_lines ORDER BY run_id, book, market, line, side"
    ).fetchall()
    # r_early (made 120 min before tip) cannot see the T-60 quotes, only the T-150 ones
    assert [g[1:5] for g in got if g[0] == "r_early"] == [
        ("draftkings", "player_points", 12.5, "over"),
        ("novig", "player_points_alternate", 20.5, "over"),
        ("novig", "player_points_alternate", 25.5, "over"),
        ("polymarket", "player_points", 12.5, "over"),
        ("polymarket", "player_points", 12.5, "under"),
        ("polymarket_us", "player_points", 12.5, "under"),
    ]
    t30 = {g[1:5]: g for g in got if g[0] == "r_t30"}
    assert t30[("draftkings", "player_points", 12.5, "over")][5] == -125  # newest known quote wins
    assert ("kalshi", "player_points", 14.5, "over") in t30  # T-60 quote, known at T-30
    assert ("draftkings", "player_points", 13.5, "over") not in t30  # T-10 is after made_at
    assert all(g[6] >= 30.0 for g in got)  # no quote from inside a run's own last 30 minutes
    # the planted post-tip quote is never exported, and is counted
    assert -300 not in {g[5] for g in got}
    assert m["excluded"]["lines_post_tip"] == 1
    # scoping: no blocks, no rebounds (no rebound forecast), no unresolved player, no other game
    assert {g[2] for g in got} == {"player_points", "player_points_alternate"}
    assert con.execute("SELECT DISTINCT game_id, player_id FROM prop_lines").fetchall() == [
        (G_LIVE, 1000)
    ]
    assert m["lines"]["status"] == "ok" and m["lines"]["n_quotes"] == len(got)
    assert con.execute(
        "SELECT bool_and(snapshot_ts <= made_at AND snapshot_ts < ?) FROM prop_lines", [TIP]
    ).fetchone() == (True,)
    assert con.execute(
        "SELECT is_alternate FROM prop_lines WHERE market = 'player_points_alternate' LIMIT 1"
    ).fetchone() == (True,)
    con.close()


def test_polymarket_unders_beyond_minus_1000_are_flagged_unusable(
    src: Path, tmp_path: Path
) -> None:
    odds = _odds_db(tmp_path / "market_asof.duckdb", _quotes())
    m, con = _lines_out(src, tmp_path, odds)
    flagged = con.execute(
        "SELECT book, side, price_american, unusable_reason FROM prop_lines WHERE unusable "
        "ORDER BY run_id, book"
    ).fetchall()
    assert flagged == [("polymarket", "under", -3233, "polymarket_under_placeholder")] * 2
    # -900 is above the floor, and the Over of the same book is a real price
    assert con.execute(
        "SELECT count(*) FROM prop_lines WHERE NOT unusable AND book LIKE 'polymarket%' "
        "AND run_id = 'r_early'"
    ).fetchone() == (2,)
    assert m["lines"]["n_unusable"] == 2
    con.close()


def test_holdout_quotes_refused_and_not_exported(src: Path, tmp_path: Path) -> None:
    odds = _odds_db(tmp_path / "market_asof.duckdb", _quotes())
    _, con = _lines_out(src, tmp_path, odds)
    assert con.execute(
        "SELECT count(*) FROM prop_lines WHERE game_id IN (?, ?)", [G_HOLD, G_FUTURE]
    ).fetchone() == (0,)
    con.close()
    oc = duckdb.connect(str(odds), read_only=True)
    forged = [{"run_id": "x", "game_id": G_HOLD, "player_id": 1000, "stat": "pts",
               "made_at": PRED_A, "tipoff": TIP}]  # fmt: skip
    with pytest.raises(ExportRefused):  # a 2025 prediction reaching the lines reader is refused
        prop_lines(oc, forged, Window(DAY))
    oc.close()


def test_lines_absent_missing_table_and_locked(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _build(src, tmp_path / "o1")  # not requested
    assert m["lines"] == {"status": "not_requested", "n_quotes": 0, "n_unusable": 0}
    m = _build(src, tmp_path / "o2", odds_db=tmp_path / "missing.duckdb")
    assert m["lines"]["status"] == "absent" and m["row_counts"]["prop_lines"] == 0
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    m = _build(src, tmp_path / "o3", odds_db=empty)
    assert m["lines"]["status"] == "no_table"

    def locked(*a: Any, **k: Any) -> Any:
        raise duckdb.IOException("IO Error: Could not set lock on file: Conflicting lock")

    monkeypatch.setattr(build_mod.reader, "open_odds_read_only", locked)
    present = _odds_db(tmp_path / "market_asof.duckdb", _quotes())
    m = _build(src, tmp_path / "o4", odds_db=present)
    assert m["lines"]["status"] == "locked" and m["row_counts"]["prop_grids"] == 2


def test_odds_store_is_read_only_and_unchanged(src: Path, tmp_path: Path) -> None:
    odds = _odds_db(tmp_path / "market_asof.duckdb", _quotes())
    before = _sha(odds)
    _build(src, tmp_path / "out", odds_db=odds)
    assert _sha(odds) == before


def test_prop_grids_carry_team_id_without_reading_the_holdout(src: Path, tmp_path: Path) -> None:
    w = duckdb.connect(str(src))
    ctx = "props_context_residual"
    w.execute("INSERT INTO games (game_id, game_date, season, home_team, away_team) VALUES "
              "('0022500001', DATE '2026-04-01', 2025, 1610612738, 1610612755)")  # fmt: skip
    w.executemany(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m,"
        " starter) VALUES (?,?,?,?,?,?,?,?,?)",
        [(G_PREV, 1002, 1610612999, 20.0, 5, 1, 1, 0, False),  # prior team is not in this game
         ("0022500001", 4243, 1610612755, 20.0, 5, 1, 1, 0, False)],  # holdout-only history
    )  # fmt: skip
    for pid in (1002, 4242, 4243):
        w.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["r_early", PRED_A, G_LIVE, TIP, ctx, "v1", "pts", pid, _pred(8.0)],
        )
    w.close()
    out = tmp_path / "out"
    _build(src, out)
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    got = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT player_id, team_id, team_source FROM prop_grids WHERE run_id = 'r_early'"
        ).fetchall()
    }
    assert got[1000] == (1610612738, "prior_game")  # last game before this one, a team in the game
    assert got[1002] == (None, None)  # prior team is not one of the two: never guessed
    assert got[4242] == (None, None)  # no history at all
    assert got[4243] == (None, None)  # only a season-2025 row exists: the holdout is not read
    con.close()


def test_team_id_from_box_when_the_game_is_final(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    w = duckdb.connect(str(src))
    w.execute("UPDATE games SET home_pts = 100, away_pts = 99 WHERE game_id = ?", [G_LIVE])
    w.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, starter) VALUES (?, 1000, 1610612755, 30.0, 10, 1, 1, 1, true)",
        [G_LIVE],
    )
    w.close()
    _build(src, out)
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    assert con.execute(
        "SELECT DISTINCT team_id, team_source FROM prop_grids WHERE player_id = 1000 "
        "AND game_id = ?",
        [G_LIVE],
    ).fetchall() == [(1610612755, "box")]
    con.close()


# ----------------------------------------------------------------------- roster placement

HOME, AWAY = 1610612738, 1610612755


def _roster(root: Path, day: date, team: int, players: list[int], known: datetime) -> None:
    """One cached official roster file; its mtime (naive UTC ``known``) is the known-at time."""
    import os

    import polars as pl

    d = root / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{team}.parquet"
    pl.DataFrame({"team_id": [team] * len(players), "player_id": players}).write_parquet(f)
    ts = known.replace(tzinfo=UTC).timestamp()
    os.utime(f, (ts, ts))


def _add_props(src: Path, pids: list[int]) -> None:
    w = duckdb.connect(str(src))
    for pid in pids:
        w.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                "r_early",
                PRED_A,
                G_LIVE,
                TIP,
                "props_context_residual",
                "v1",
                "pts",
                pid,
                _pred(8.0),
            ],
        )
    w.close()


def _teams(out: Path) -> dict[int, tuple[Any, Any]]:
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    got = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT player_id, team_id, team_source FROM prop_grids WHERE run_id = 'r_early'"
        ).fetchall()
    }
    con.close()
    return got


def test_opening_night_with_no_current_season_box_rows_places_from_the_roster(
    src: Path, tmp_path: Path
) -> None:
    w = duckdb.connect(str(src))
    w.execute("DELETE FROM player_game_stats WHERE game_id IN "
              "(SELECT game_id FROM games WHERE season = 2026)")  # fmt: skip
    n_box = w.execute("SELECT count(*) FROM player_game_stats pgs JOIN games g USING (game_id) "
                      "WHERE g.season = 2026").fetchone()  # fmt: skip
    w.close()
    assert n_box == (0,)
    _add_props(src, [2001, 2002, 2003])
    roots = tmp_path / "rosters"
    ok = PRED_A + timedelta(seconds=90)  # fetched by the same run, a minute after made_at
    _roster(roots, DAY, HOME, [1000, 2001], ok)
    _roster(roots, DAY, AWAY, [2002], ok)
    before = _build(src, tmp_path / "o0")  # no roster dir: nothing is placed
    assert set(_teams(tmp_path / "o0").values()) == {(None, None)}
    m = _build(src, tmp_path / "o1", roster_dir=roots)
    got = _teams(tmp_path / "o1")
    assert got[1000] == (HOME, "official_roster")
    assert got[2001] == (HOME, "official_roster")
    assert got[2002] == (AWAY, "official_roster")
    assert got[2003] == (None, None)  # on no roster: unplaced, never guessed
    assert m["row_counts"]["prop_grids"] == before["row_counts"]["prop_grids"]


def test_mid_season_trade_uses_the_new_team_not_the_stale_one(src: Path, tmp_path: Path) -> None:
    w = duckdb.connect(str(src))
    w.executemany(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, starter) VALUES (?,?,?,?,?,?,?,?,?)",
        [(G_PREV, 1002, 1610612999, 20.0, 5, 1, 1, 0, False),  # earlier team plays nobody here
         (G_PREV, 1003, HOME, 20.0, 5, 1, 1, 0, False)],  # unchanged: prior_game still wins
    )  # fmt: skip
    w.close()
    _add_props(src, [1002, 1003])
    roots = tmp_path / "rosters"
    ok = PRED_A + timedelta(minutes=1)
    _roster(roots, DAY, AWAY, [1002], ok)
    _roster(roots, DAY, HOME, [1003], ok)
    _build(src, tmp_path / "out", roster_dir=roots)
    got = _teams(tmp_path / "out")
    assert got[1002] == (AWAY, "official_roster")
    assert got[1003] == (HOME, "prior_game")  # precedence: an earlier box row beats the roster


def test_roster_stamped_after_made_at_or_after_tip_is_refused(src: Path, tmp_path: Path) -> None:
    _add_props(src, [3001, 3002, 3003, 3004])
    roots = tmp_path / "rosters"
    _roster(roots, DAY, HOME, [3001], PRED_A + timedelta(hours=1))  # after made_at: not known
    _roster(roots, DAY, AWAY, [3002], TIP + timedelta(minutes=5))  # after tip
    _roster(roots, DAY, HOME, [3003], PRED_A - timedelta(hours=3))  # earlier day's cache run: ok
    _roster(roots, DAY, AWAY, [3003], PRED_A - timedelta(hours=3))  # listed by both teams
    _roster(roots, DAY - timedelta(days=1), AWAY, [3004], PRED_A - timedelta(hours=3))  # wrong day
    _build(src, tmp_path / "out", roster_dir=roots)
    got = _teams(tmp_path / "out")
    assert got[3001] == (None, None)
    assert got[3002] == (None, None)
    assert got[3003] == (None, None)  # ambiguous between the two teams: unplaced
    assert got[3004] == (None, None)  # only today's roster counts
