"""Best-evidenced model for the app (DECISIONS 2026-10-14 21:30): AVG shadow rows and P(play) R
rows are exported read-only next to production, one is_primary row per (game, player, stat).
Fixture DB only; AVG rows are built to the writer's schema (nba.daily.avg_shadow.store)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest

from nba.daily.avg_shadow import store as avg_store
from nba.daily.pplay_shadow import store as pplay_store
from nba.export import ExportRefused, Window, forward_predictions, resolve_seasons
from nba.export.best_model import (
    AVG_MODEL_NAME,
    avg_freshness,
    avg_to_prediction,
)
from nba.export.build import DB_NAME, build_export
from nba.export.reader import open_read_only
from tests.fixtures.loader import build_fixture_db

DAY = date(2026, 11, 10)
TIP = datetime(2026, 11, 10, 23, 30)  # naive UTC
G_REG, G_POST, G_HOLD = "0022600050", "0042600101", "0022500400"
PROD = "props_context_residual"
BUNDLE = "b" * 64
Q19_PROD = [float(i) for i in range(2, 21)]
PMF = [int(v) for v in np.repeat(np.arange(0, 40), 5)[:199]]  # 199 equal-mass integers, 0..39
Q19_AVG = [int(PMF[int(np.ceil(t * 199 - 1e-9)) - 1]) for t in np.linspace(0.05, 0.95, 19)]
MADE = TIP - timedelta(minutes=90)


def _prod_pred(mean: float = 12.5) -> str:
    return json.dumps(
        {"mean": mean, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q10": 4.0, "q50": mean,
         "q90": 14.0, "q_grid": Q19_PROD, "p_ge": {"10": 0.4},
         "p_ge_full": {"c": [198, 0], "n": 199}}
    )  # fmt: skip


def _avg_row(
    gid: str = G_REG, pid: int = 1000, stat: str = "pts", made: datetime = MADE,
    month_block: str | None = "2026-11", installed: datetime | None = datetime(2026, 11, 1, 15),
    fit_through: date | None = date(2026, 10, 31), stale: bool = False, run: str = "r_avg",
    pmf: Any = PMF, tip: datetime = TIP,
) -> tuple[Any, ...]:  # fmt: skip
    return (
        made, run, gid, pid, stat, tip, "real", True, 12, BUNDLE, fit_through, month_block,
        installed, "gate", date(2026, 10, 31), stale, json.dumps(pmf), json.dumps(pmf),
        json.dumps(Q19_AVG), json.dumps(Q19_AVG), json.dumps([1.0] * 19), json.dumps(Q19_AVG),
        json.dumps(Q19_PROD), "props_context_residual",
    )  # fmt: skip


def _insert_avg(con: duckdb.DuckDBPyConnection, *rows: tuple[Any, ...]) -> None:
    con.executemany(
        f"INSERT INTO forward_avg_shadow ({', '.join(avg_store.COLUMNS)}) "
        f"VALUES ({','.join('?' for _ in avg_store.COLUMNS)})",
        rows,
    )


def _insert_pplay(
    con: duckdb.DuckDBPyConnection, gid: str, pid: int, r: float | None, *,
    made: datetime = MADE, prod: float | None = 0.8, reason: str | None = None, run: str = "r_pp",
) -> None:  # fmt: skip
    con.execute(
        "INSERT INTO forward_pplay_shadow (run_id, made_at, game_id, tipoff, player_id, team_id, "
        "model_version, p_play_prod, p_play_r, r_reason, proj_minutes, train_through, n_train) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [run, made, gid, TIP, pid, 1610612738, "pplay_r_v1", prod, r, reason, 28.0,
         date(2026, 11, 9), 5000],
    )  # fmt: skip


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "nba.duckdb"
    con = build_fixture_db(path)
    avg_store.ensure_tables(con)
    pplay_store.ensure_tables(con)
    con.executemany(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team) VALUES (?,?,?,?,?)",
        [(G_REG, DAY, 2026, 1610612738, 1610612755), (G_POST, DAY, 2026, 1610612738, 1610612755),
         (G_HOLD, DAY - timedelta(days=1), 2025, 1610612738, 1610612755)],
    )  # fmt: skip
    fp = "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)"
    rows = []
    for gid in (G_REG, G_POST):
        for pid in (1000, 1001, 1002):
            rows.append(("r_prod", MADE, gid, TIP, PROD, "ctxres-v1", "pts", pid, _prod_pred()))
    # the recency comparison row must never be primary
    rows.append(("r_prod", MADE, G_REG, TIP, "props_recency_v1", "recency-v1", "pts", 1000,
                 _prod_pred(9.0)))  # fmt: skip
    con.executemany(fp, rows)
    con.close()
    return path


def _fwd(db: Path, **kw: Any) -> Any:
    con = open_read_only(db)
    try:
        return forward_predictions(con, Window(DAY, 7, (2026,)))
    finally:
        con.close()


def _w(db: Path, mutate: Any) -> None:
    con = duckdb.connect(str(db))
    mutate(con)
    con.close()


def _primary(fwd: Any, gid: str, pid: int, stat: str = "pts") -> dict[str, Any]:
    ps = [
        p for p in fwd.prop_grids
        if (p["game_id"], p["player_id"], p["stat"]) == (gid, pid, stat) and p["is_primary"]
    ]  # fmt: skip
    assert len(ps) == 1, f"expected exactly one primary, got {len(ps)}"
    return ps[0]


def test_avg_present_fresh_is_primary(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row()))
    fwd = _fwd(db)
    p = _primary(fwd, G_REG, 1000)
    assert (p["model_name"], p["primary_source"], p["freshness"]) == (
        AVG_MODEL_NAME,
        "avg",
        "fresh",
    )
    assert p["bundle_id"] == BUNDLE and p["bundle_made_at"] == datetime(2026, 11, 1, 15)
    assert p["bundle_month"] == "2026-11" and p["stamp_fit_through"] == date(2026, 10, 31)
    # other players of the game had no AVG row: production v2, reason recorded
    q = _primary(fwd, G_REG, 1001)
    assert (q["model_name"], q["primary_source"]) == (PROD, "fallback_missing")
    # the comparison row is never primary
    rec = [p for p in fwd.prop_grids if p["model_name"] == "props_recency_v1"]
    assert rec and not any(r["is_primary"] for r in rec)


def test_avg_row_has_the_production_shape(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row()))
    fwd = _fwd(db)
    avg = next(p for p in fwd.prop_grids if p["model_name"] == AVG_MODEL_NAME)
    prod = next(p for p in fwd.prop_grids if p["model_name"] == PROD and p["player_id"] == 1000)
    for k in prod:  # every column the app reads from a production row exists on the AVG row
        assert k in avg, k
    assert len(avg["q_grid"]) == len(prod["q_grid"]) == 19
    assert set(avg["p_ge"]) == {"10", "15", "20", "25", "30"}  # pts THRESHOLDS, as production
    s = np.asarray(PMF, dtype=float)
    assert avg["p_ge"]["20"] == pytest.approx(float(np.mean(s > 19.5)))
    assert avg["p_ge_full_n"] == 199 and avg["p_ge_full_counts"][0] == int(np.sum(s > 0.5))
    assert avg["q_grid"] == [float(v) for v in Q19_AVG]
    assert (avg["q10"], avg["q50"], avg["q90"]) == (Q19_AVG[1], Q19_AVG[9], Q19_AVG[17])
    assert avg["mean"] == pytest.approx(float(s.mean()))
    pred = avg_to_prediction("pts", [float(v) for v in PMF], [float(v) for v in Q19_AVG])
    assert {"mean", "std", "q10", "q50", "q90", "q_grid", "p_ge", "p_ge_full"} <= set(pred)
    assert json.loads(avg["prediction_json"])["p_ge_full"]["n"] == 199


def test_missing_avg_falls_back_to_v2(db: Path) -> None:
    fwd = _fwd(db)  # no AVG rows at all (table empty)
    p = _primary(fwd, G_REG, 1000)
    assert (p["model_name"], p["version"], p["primary_source"]) == (
        PROD,
        "ctxres-v1",
        "fallback_missing",
    )
    assert p["freshness"] == "n/a"


def test_missing_avg_table_is_not_an_error(db: Path) -> None:
    _w(db, lambda c: c.execute("DROP TABLE forward_avg_shadow"))
    _w(db, lambda c: c.execute("DROP TABLE forward_pplay_shadow"))
    p = _primary(_fwd(db), G_REG, 1000)
    assert p["primary_source"] == "fallback_missing" and p["p_play_source"] == "production"


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"stale": True}, "writer_flag"),
        ({"month_block": "2026-10"}, "month_block_mismatch"),
        ({"installed": datetime(2026, 11, 10, 23)}, "installed_after_made"),
        ({"installed": None}, "installed_after_made"),
        ({"fit_through": date(2026, 11, 1)}, "fit_through_not_before_month"),
        ({"fit_through": None}, "fit_through_not_before_month"),
    ],
)
def test_stale_bundle_falls_back_to_v2(db: Path, kw: dict[str, Any], reason: str) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(**kw)))
    p = _primary(_fwd(db), G_REG, 1000)
    assert (p["model_name"], p["primary_source"]) == (PROD, "fallback_stale")
    assert reason in str(p["primary_detail"])
    avg = next(r for r in _fwd(db).prop_grids if r["model_name"] == AVG_MODEL_NAME)
    assert (avg["freshness"], avg["freshness_reason"], avg["is_primary"]) == (
        "stale", reason, False,
    )  # fmt: skip


def test_freshness_is_pure() -> None:
    ok = {"made_at": MADE, "installed_at": datetime(2026, 11, 1, 15), "stale_flag": False,
          "fit_through": date(2026, 10, 31), "month_block": "2026-11",
          "game_date": DAY}  # fmt: skip
    assert avg_freshness(**ok) == ("fresh", None)  # type: ignore[arg-type]
    late = avg_freshness(**{**ok, "game_date": date(2026, 12, 1)})  # type: ignore[arg-type]
    assert late[1] == "month_block_mismatch"


def test_postseason_uses_v2_even_with_a_fresh_avg_row(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(gid=G_POST)))
    fwd = _fwd(db)
    p = _primary(fwd, G_POST, 1000)
    assert (p["model_name"], p["primary_source"]) == (PROD, "postseason")
    # the AVG row is still exported, as information, never primary
    assert any(r["model_name"] == AVG_MODEL_NAME and not r["is_primary"] for r in fwd.prop_grids)


def test_after_tip_avg_row_is_ignored(db: Path) -> None:
    late = _avg_row(made=TIP + timedelta(minutes=5), run="r_late")
    _w(db, lambda c: _insert_avg(c, late))  # bypasses the writer's refusal on purpose
    fwd = _fwd(db)
    assert not any(r["model_name"] == AVG_MODEL_NAME for r in fwd.prop_grids)
    assert fwd.excluded_avg_post_tip == 1
    assert _primary(fwd, G_REG, 1000)["primary_source"] == "fallback_missing"
    # a fresh pre-tip row plus a later post-tip row: the pre-tip one is shown
    _w(db, lambda c: _insert_avg(c, _avg_row(run="r_pre")))
    fwd = _fwd(db)
    p = _primary(fwd, G_REG, 1000)
    assert p["run_id"] == "r_pre" and p["made_at"] == MADE


def test_made_exactly_at_tip_is_ignored(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(made=TIP)))
    assert _primary(_fwd(db), G_REG, 1000)["primary_source"] == "fallback_missing"


def test_latest_pre_tip_avg_row_wins(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(run="r1"), _avg_row(made=TIP - timedelta(minutes=30),
                                                                 run="r2")))  # fmt: skip
    fwd = _fwd(db)
    assert _primary(fwd, G_REG, 1000)["run_id"] == "r2"
    avg = [r for r in fwd.prop_grids if r["model_name"] == AVG_MODEL_NAME]
    assert sorted((r["run_id"], r["is_latest"]) for r in avg) == [("r1", False), ("r2", True)]


def test_unreadable_avg_row_falls_back(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(pmf=[1, 2, 3])))
    fwd = _fwd(db)
    assert fwd.excluded_avg_unreadable == 1
    assert _primary(fwd, G_REG, 1000)["primary_source"] == "fallback_missing"


def test_one_primary_per_triple(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(), _avg_row(pid=1002, month_block="2026-10")))
    fwd = _fwd(db)
    keys = [(p["game_id"], p["player_id"], p["stat"]) for p in fwd.prop_grids if p["is_primary"]]
    assert len(keys) == len(set(keys)) == 6  # 2 games x 3 players, pts
    assert _primary(fwd, G_REG, 1002)["primary_source"] == "fallback_stale"


def test_season_guard_still_refuses_2025(db: Path, tmp_path: Path) -> None:
    for s in ([2025], [2025, 2026]):
        with pytest.raises(ExportRefused):
            resolve_seasons(s)
        with pytest.raises(ExportRefused):
            build_export(db, tmp_path / "o", DAY, seasons=s, parlay_dir=tmp_path / "np")
    # a 2025 game's AVG and R rows, even inside the date window, never reach the export
    _w(db, lambda c: _insert_avg(c, _avg_row(gid=G_HOLD, tip=TIP - timedelta(days=1),
                                             made=MADE - timedelta(days=1))))  # fmt: skip
    _w(db, lambda c: c.execute(
        "INSERT INTO forward_pplay_shadow (run_id, made_at, game_id, tipoff, player_id, "
        "p_play_prod, p_play_r) VALUES ('x', ?, ?, ?, 1000, 0.8, 0.9)",
        [MADE - timedelta(days=1), G_HOLD, TIP - timedelta(days=1)]))  # fmt: skip
    fwd = _fwd(db)
    assert not any(p["game_id"] == G_HOLD for p in fwd.prop_grids)
    assert not any(p["game_id"] == G_HOLD for p in fwd.p_play)
    assert not any(g["game_id"] == G_HOLD for g in fwd.games)


def test_pplay_r_primary_else_production(db: Path) -> None:
    def ins(c: duckdb.DuckDBPyConnection) -> None:
        _insert_pplay(c, G_REG, 1000, 0.97)
        _insert_pplay(c, G_REG, 1001, None, reason="out_on_report", prod=0.4)
        # R at T-90, then a newer row with no R: the old R is not reused
        _insert_pplay(c, G_REG, 1002, 0.95, run="old")
        _insert_pplay(c, G_REG, 1002, None, made=MADE + timedelta(minutes=30), run="new",
                      reason="out_on_report", prod=0.1)  # fmt: skip

    _w(db, ins)
    fwd = _fwd(db)
    by = {p["player_id"]: p for p in fwd.p_play if p["game_id"] == G_REG}
    assert (by[1000]["p_play_primary"], by[1000]["p_play_source"]) == (0.97, "r")
    assert (by[1001]["p_play_primary"], by[1001]["p_play_source"], by[1001]["p_play_detail"]) == (
        0.0, "out_on_report", "out_on_report",
    )  # fmt: skip
    assert by[1001]["p_play_prod"] == 0.4  # production's own value is kept beside it
    assert (by[1002]["p_play_primary"], by[1002]["p_play_source"]) == (0.0, "out_on_report")
    # a non-out reason (R simply has no row) still falls back to production's value
    _w(db, lambda c: _insert_pplay(c, G_REG, 1001, None, reason="no_model", prod=0.4, run="nm",
                                   made=MADE + timedelta(minutes=5)))  # fmt: skip
    by = {p["player_id"]: p for p in _fwd(db).p_play if p["game_id"] == G_REG}
    assert (by[1001]["p_play_primary"], by[1001]["p_play_source"]) == (0.4, "production")
    p = _primary(fwd, G_REG, 1000)
    assert (p["p_play_primary"], p["p_play_source"]) == (0.97, "r")
    assert p["p_play"] == 0.9  # the production row's own stored value is left verbatim
    # a player with no shadow row keeps the stored production number
    _w(db, lambda c: c.execute("DELETE FROM forward_pplay_shadow"))
    q = _primary(_fwd(db), G_REG, 1000)
    assert (q["p_play_primary"], q["p_play_source"]) == (0.9, "production")


def test_pplay_after_tip_ignored(db: Path) -> None:
    _w(db, lambda c: _insert_pplay(c, G_REG, 1000, 0.99, made=TIP + timedelta(minutes=1)))
    fwd = _fwd(db)
    assert fwd.p_play == []
    assert _primary(fwd, G_REG, 1000)["p_play_source"] == "production"


def test_avg_row_takes_r_p_play_and_production_minutes(db: Path) -> None:
    def ins(c: duckdb.DuckDBPyConnection) -> None:
        _insert_avg(c, _avg_row())
        _insert_pplay(c, G_REG, 1000, 0.97)

    _w(db, ins)
    p = _primary(_fwd(db), G_REG, 1000)
    assert p["model_name"] == AVG_MODEL_NAME
    assert (p["p_play"], p["p_play_primary"], p["p_play_source"]) == (0.97, 0.97, "r")
    assert p["proj_minutes"] == 30.0


def test_build_export_carries_the_new_columns(db: Path, tmp_path: Path) -> None:
    _w(db, lambda c: (_insert_avg(c, _avg_row()), _insert_pplay(c, G_REG, 1000, 0.97)))
    out = tmp_path / "out"
    m = build_export(
        db, out, DAY, git=lambda: {"git_sha": "x", "git_dirty": False}, version=lambda: "dv",
        now=lambda: datetime(2026, 11, 10, 20, 0), parlay_dir=tmp_path / "np",
    )  # fmt: skip
    assert m["schema_version"] == 2 and m["primary_sources"] == {
        "avg": 1, "fallback_missing": 2, "postseason": 3,
    }  # fmt: skip
    assert m["row_counts"]["p_play"] == 1 and m["excluded"]["avg_post_tip"] == 0
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    assert dict(con.execute("SELECT key, value FROM meta").fetchall())["best_model_schema"] == "1"
    row = con.execute(
        "SELECT model_name, primary_source, freshness, bundle_id, p_play_source FROM prop_grids "
        "WHERE is_primary AND game_id = ? AND player_id = 1000",
        [G_REG],
    ).fetchone()
    assert row == (AVG_MODEL_NAME, "avg", "fresh", BUNDLE, "r")
    assert con.execute("SELECT count(*) FROM p_play").fetchone() == (1,)


def test_avg_rows_are_comparison_only_unless_chosen(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(), _avg_row(pid=1002, month_block="2026-10"),
                                 _avg_row(gid=G_POST, pid=1001)))  # fmt: skip
    avg = [p for p in _fwd(db).prop_grids if p["model_name"] == AVG_MODEL_NAME]
    assert len(avg) == 3
    for p in avg:  # an app reading the old way (comparison_only filter) never shows these
        assert p["comparison_only"] == (not p["is_primary"])
    assert sum(not p["comparison_only"] for p in avg) == 1  # only the fresh regular-season row


def test_avg_older_than_production_is_not_primary(db: Path) -> None:
    _w(db, lambda c: _insert_avg(c, _avg_row(made=MADE - timedelta(minutes=30), run="r_old")))
    p = _primary(_fwd(db), G_REG, 1000)  # production row is r_prod at MADE
    assert (p["model_name"], p["primary_source"]) == (PROD, "fallback_stale")
    assert p["primary_detail"] == "avg_run_behind_production"
    # the heartbeat of the production run finishing ok says the AVG run was current (unchanged row)
    _w(db, lambda c: c.execute(
        "INSERT INTO forward_avg_heartbeat (slate_date, run_id, status) VALUES (?, 'r_prod', 'ok')",
        [DAY]))  # fmt: skip
    assert _primary(_fwd(db), G_REG, 1000)["primary_source"] == "avg"
    _w(db, lambda c: c.execute("UPDATE forward_avg_heartbeat SET status = 'failed'"))
    assert _primary(_fwd(db), G_REG, 1000)["primary_source"] == "fallback_stale"


def test_month_boundary_without_a_games_row(db: Path) -> None:
    """Tip 2026-12-01 02:00 UTC is 21:00 ET on 11-30: the game date (et_date fallback, no games
    row) is November, so a November bundle is fresh and a December one is stale."""
    gid, tip = "0022600777", datetime(2026, 12, 1, 2, 0)
    made = tip - timedelta(minutes=90)
    day = date(2026, 11, 30)

    win = json.dumps({"p_home": 0.5, "home_team": 1610612738, "away_team": 1610612755,
                      "primary": True, "fallback_reason": None})  # fmt: skip
    _w(db, lambda c: c.execute(
        "INSERT INTO forward_predictions VALUES ('r_w', ?, ?, ?, 'rung0_injury_elo', 'v', "
        "'win_prob_home', -1, ?)", [made, gid, tip, win]))  # fmt: skip

    def go(block: str) -> dict[str, Any]:
        _w(db, lambda c: c.execute("DELETE FROM forward_avg_shadow"))
        _w(db, lambda c: _insert_avg(c, _avg_row(gid=gid, made=made, tip=tip, month_block=block,
                                                 fit_through=date(2026, 10, 31))))  # fmt: skip
        con = open_read_only(db)
        try:
            fwd = forward_predictions(con, Window(day, 2, (2026,)))
        finally:
            con.close()
        return next(p for p in fwd.prop_grids if p["game_id"] == gid)

    assert go("2026-11")["freshness"] == "fresh"
    row = go("2026-12")
    assert (row["freshness"], row["freshness_reason"]) == ("stale", "month_block_mismatch")


def test_avg_primary_row_gets_the_production_rows_lines(db: Path, tmp_path: Path) -> None:
    """An AVG row from an older run (write-on-change) names the production run's quotes."""
    odds = tmp_path / "odds.duckdb"
    oc = duckdb.connect(str(odds))
    oc.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, player_id INT, market VARCHAR, "
        "book VARCHAR, side VARCHAR, point DOUBLE, price_american INT, implied_prob_raw DOUBLE, "
        "received_at TIMESTAMP, snapshot_kind VARCHAR, last_update TIMESTAMP, "
        "tipoff TIMESTAMP, commence_time TIMESTAMP)"
    )
    oc.execute(
        "INSERT INTO live_book_odds VALUES (?, 1000, 'player_points', 'bk', 'over', 20.5, -110, "
        "0.52, ?, 'snap', ?, ?, ?)",
        [G_REG, MADE - timedelta(minutes=5), MADE, TIP, TIP],
    )
    oc.close()
    _w(db, lambda c: _insert_avg(c, _avg_row(made=MADE - timedelta(minutes=60), run="r_old")))
    _w(db, lambda c: c.execute(
        "INSERT INTO forward_avg_heartbeat (slate_date, run_id, status) VALUES (?, 'r_prod', 'ok')",
        [DAY]))  # fmt: skip
    out = tmp_path / "out"
    build_export(
        db, out, DAY, git=lambda: {"git_sha": "x", "git_dirty": False}, version=lambda: "dv",
        now=lambda: datetime(2026, 11, 10, 20, 0), parlay_dir=tmp_path / "np", odds_db=odds,
    )  # fmt: skip
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    row = con.execute(
        "SELECT model_name, run_id, lines_run_id FROM prop_grids "
        "WHERE is_primary AND game_id = ? AND player_id = 1000", [G_REG]).fetchone()  # fmt: skip
    assert row == (AVG_MODEL_NAME, "r_old", "r_prod")
    n = con.execute(
        "SELECT count(*) FROM prop_lines WHERE run_id = ? AND game_id = ? AND player_id = 1000",
        ["r_prod", G_REG],
    ).fetchone()
    assert n is not None and n[0] == 1  # the quote exists under the key the row names
    # production rows are unchanged: lines key is their own run
    assert con.execute(
        "SELECT DISTINCT lines_run_id = run_id FROM prop_grids WHERE model_name = ?", [PROD]
    ).fetchall() == [(True,)]


def test_avg_only_game_without_teams_is_dropped_and_counted(db: Path, tmp_path: Path) -> None:
    gid, tip = "0022600778", datetime(2026, 11, 10, 23, 0)
    _w(db, lambda c: _insert_avg(c, _avg_row(gid=gid, tip=tip, made=tip - timedelta(minutes=90))))
    fwd = _fwd(db)
    assert not any(p["game_id"] == gid for p in fwd.prop_grids)
    assert not any(g["game_id"] == gid for g in fwd.games)
    assert fwd.excluded_avg_no_game == 1
    m = build_export(
        db, tmp_path / "o", DAY, git=lambda: {"git_sha": "x", "git_dirty": False},
        version=lambda: "dv", now=lambda: datetime(2026, 11, 10, 20, 0), parlay_dir=tmp_path / "np",
    )  # fmt: skip
    assert m["excluded"]["avg_no_game_teams"] == 1
    con = duckdb.connect(str(tmp_path / "o" / DB_NAME), read_only=True)
    assert con.execute("SELECT count(*) FROM games WHERE home_team IS NULL").fetchone() == (0,)


def test_r_run_behind_production_uses_production(db: Path) -> None:
    def ins(c: duckdb.DuckDBPyConnection) -> None:
        # R at T-120 says 0.93; production (T-90 rows, p_play 0.9) moved on
        _insert_pplay(c, G_REG, 1000, 0.93, made=MADE - timedelta(minutes=30), prod=0.5, run="rr")
        # unchanged shadow row from an earlier run: its p_play_prod equals production's -> R kept
        _insert_pplay(c, G_REG, 1001, 0.95, made=MADE - timedelta(minutes=30), prod=0.9, run="r2")
        # R at the same instant as production -> R kept
        _insert_pplay(c, G_REG, 1002, 0.97)

    _w(db, ins)
    fwd = _fwd(db)
    by = {p["player_id"]: p for p in fwd.p_play if p["game_id"] == G_REG}
    assert (by[1000]["p_play_primary"], by[1000]["p_play_source"], by[1000]["p_play_detail"]) == (
        0.9, "production", "r_run_behind_production",
    )  # fmt: skip
    assert (by[1001]["p_play_primary"], by[1001]["p_play_source"]) == (0.95, "r")
    assert (by[1002]["p_play_primary"], by[1002]["p_play_source"]) == (0.97, "r")
    p = _primary(fwd, G_REG, 1000)  # same reconciled value on the prop row
    assert (p["p_play_primary"], p["p_play_source"]) == (0.9, "production")


def test_r_behind_production_zero(db: Path) -> None:
    """R T-31 0.93, production later at 0.0 (player ruled out): production's 0.0 wins."""
    _w(db, lambda c: c.execute(
        "UPDATE forward_predictions SET prediction = ?, made_at = ? WHERE player_id = 1000 "
        "AND game_id = ? AND model_name = ?",
        [_prod_pred().replace('"p_play": 0.9', '"p_play": 0.0'), TIP - timedelta(minutes=10),
         G_REG, PROD]))  # fmt: skip
    _w(
        db,
        lambda c: _insert_pplay(c, G_REG, 1000, 0.93, made=TIP - timedelta(minutes=31), prod=0.9),
    )
    fwd = _fwd(db)
    row = next(p for p in fwd.p_play if p["player_id"] == 1000)
    assert (row["p_play_primary"], row["p_play_source"]) == (0.0, "production")


def test_avg_inputs_changed_falls_back(db: Path) -> None:
    changed = [v + 1.0 for v in Q19_PROD]
    _w(db, lambda c: c.execute(
        "UPDATE forward_predictions SET prediction = ? WHERE player_id = 1000 AND game_id = ? "
        "AND model_name = ?",
        [_prod_pred().replace(json.dumps(Q19_PROD), json.dumps(changed)), G_REG,
         PROD]))  # fmt: skip
    _w(db, lambda c: _insert_avg(c, _avg_row(), _avg_row(pid=1001)))
    fwd = _fwd(db)
    p = _primary(fwd, G_REG, 1000)
    assert (p["model_name"], p["primary_source"], p["primary_detail"]) == (
        PROD, "fallback_stale", "avg_inputs_changed",
    )  # fmt: skip
    assert _primary(fwd, G_REG, 1001)["primary_source"] == "avg"  # unchanged inputs: AVG stays


def test_cup_final_and_other_non_regular_games_use_production(db: Path) -> None:
    cup = "0062600001"
    _w(db, lambda c: c.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team) VALUES (?,?,?,?,?)",
        [cup, DAY, 2026, 1610612738, 1610612755]))  # fmt: skip
    _w(db, lambda c: c.execute(
        "INSERT INTO forward_predictions VALUES "
        "('r_prod', ?, ?, ?, ?, 'ctxres-v1', 'pts', 1000, ?)",
        [MADE, cup, TIP, PROD, _prod_pred()]))  # fmt: skip
    _w(db, lambda c: _insert_avg(c, _avg_row(gid=cup)))
    p = _primary(_fwd(db), cup, 1000)
    assert (p["model_name"], p["primary_source"]) == (PROD, "non_regular_season")
