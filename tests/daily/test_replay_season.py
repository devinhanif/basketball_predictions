"""Season systems replay: run clock, real-tip schedule, hiding discipline, resumable state."""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.daily.pipeline import RunSummary
from nba.daily.replay_season import (
    ReplayConfig,
    ReplayState,
    init_replay_db,
    lineups_available,
    make_hidden_kalshi,
    pretip_run_times,
    process_date,
    real_tip_schedule,
    run_chunk,
    season_dates,
    slot_floor,
)
from nba.daily.schedule import ScheduledGame
from tests.daily.conftest import T1, T2, add_history
from tests.fixtures.loader import build_fixture_db


def _g(gid: str, tip: datetime) -> ScheduledGame:
    return ScheduledGame(gid, tip, T1, T2)


def test_slot_floor_is_on_the_live_job_cadence() -> None:
    assert slot_floor(datetime(2025, 10, 22, 21, 50)) == datetime(2025, 10, 22, 21, 50)
    assert slot_floor(datetime(2025, 10, 22, 22, 19)) == datetime(2025, 10, 22, 21, 50)
    assert slot_floor(datetime(2025, 10, 22, 22, 20)) == datetime(2025, 10, 22, 22, 20)
    assert slot_floor(datetime(2025, 10, 22, 22, 0)) == datetime(2025, 10, 22, 21, 50)


def test_pretip_run_times_one_per_tip_group_and_always_70_min_ahead() -> None:
    tips = [
        datetime(2025, 10, 22, 23, 0),
        datetime(2025, 10, 22, 23, 0),
        datetime(2025, 10, 22, 23, 30),
        datetime(2025, 10, 23, 0, 10),  # odd tip: still >= 70 min ahead, on the slot grid
        datetime(2025, 10, 23, 2, 0),
    ]
    slate = [_g(f"g{i}", t) for i, t in enumerate(tips)]
    runs = pretip_run_times(slate)
    assert runs == sorted(set(runs)) and len(runs) == 4
    for g in slate:
        # every game has a run at least 70 min before its tip
        assert any(r <= g.tipoff - timedelta(minutes=70) for r in runs)
    assert all(r.minute in (20, 50) for r in runs)
    assert runs[0] == datetime(2025, 10, 22, 21, 50)


def test_real_tip_schedule_uses_real_tips_and_falls_back_to_proxy() -> None:
    con = build_fixture_db(":memory:")
    add_history(con, 2)
    gids = [
        r[0]
        for r in con.execute(
            "SELECT game_id FROM games WHERE season = 2025 ORDER BY game_date, game_id"
        ).fetchall()
    ]
    real = datetime(2026, 1, 1, 17, 5)
    tips = pl.DataFrame(
        {"game_id": [gids[0]], "tipoff_utc": [real], "tip_et": [real - timedelta(hours=5)]}
    )
    fn = real_tip_schedule(con, tips)
    sched = {g.game_id: g for g in fn("2025-26")}
    assert sched[gids[0]].tipoff == real
    assert sched[gids[1]].tipoff == datetime(2026, 1, 2, 0, 0)  # 19:00 ET proxy
    assert fn.tip_map == {gids[0]: real}  # type: ignore[attr-defined]


def test_lineups_available_false_without_data(tmp_path: Path) -> None:
    assert not lineups_available(None, date(2025, 10, 21))
    assert not lineups_available(tmp_path / "missing.duckdb", date(2025, 10, 21))
    p = tmp_path / "l.duckdb"
    c = duckdb.connect(str(p))
    c.execute("CREATE TABLE lineup_snapshots (game_date DATE)")
    c.execute("INSERT INTO lineup_snapshots VALUES ('2025-10-22')")
    c.close()
    assert lineups_available(p, date(2025, 10, 22))
    assert not lineups_available(p, date(2025, 10, 21))


def test_hidden_kalshi_copy_hides_results_and_keeps_master(tmp_path: Path) -> None:
    master, dst = tmp_path / "m.duckdb", tmp_path / "d.duckdb"
    c = duckdb.connect(str(master))
    c.execute("CREATE TABLE kalshi_markets (ticker VARCHAR, result VARCHAR, settled_ts TIMESTAMP)")
    c.execute("CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP)")
    c.execute("INSERT INTO kalshi_markets VALUES ('A', 'yes', now())")
    c.execute("INSERT INTO kalshi_prices VALUES ('A', now())")
    c.close()
    info = make_hidden_kalshi(master, dst)
    assert info == {"markets": 1, "price_rows": 1, "priced_tickers": 1}
    assert duckdb.connect(str(dst)).execute("SELECT result FROM kalshi_markets").fetchone() == (
        None,
    )
    assert duckdb.connect(str(master)).execute("SELECT result FROM kalshi_markets").fetchone() == (
        "yes",
    )


@pytest.fixture
def replay_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReplayConfig:
    monkeypatch.setattr(
        "nba.daily.replay_season.build_game_tipoff",
        lambda: pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "tipoff_utc": pl.Datetime("us"),
                "tip_et": pl.Datetime("us"),
            }
        ),
    )
    src = tmp_path / "src.duckdb"
    con = build_fixture_db(":memory:")
    add_history(con, 6)
    con.execute(f"ATTACH '{src}' AS out")
    for t in ("games", "player_game_stats"):
        con.execute(f"CREATE TABLE out.{t} AS SELECT * FROM {t}")
    con.execute("DETACH out")
    cfg = ReplayConfig(
        season=2025,
        work_db=tmp_path / "work.duckdb",
        full_db=tmp_path / "full.duckdb",
        out_dir=tmp_path / "out",
        model_cache=tmp_path / "cache",
        with_props=False,
    )
    init_replay_db(src, cfg)
    return cfg


def _fake_pretip_factory(seen: list[tuple[date, int, int]]):  # type: ignore[no-untyped-def]
    def fake(cfg: ReplayConfig, d: date, rt: datetime) -> RunSummary:
        con = duckdb.connect(str(cfg.work_db), read_only=True)
        try:
            row = con.execute(
                "SELECT count(*) FILTER (WHERE game_date < ? AND home_pts IS NOT NULL), "
                "count(*) FILTER (WHERE game_date >= ? AND home_pts IS NOT NULL) FROM games",
                [d, d],
            ).fetchone()
            n_stats_future = con.execute(
                "SELECT count(*) FROM player_game_stats WHERE game_id IN "
                "(SELECT game_id FROM games WHERE game_date >= ?)",
                [d],
            ).fetchone()
        finally:
            con.close()
        assert row is not None and n_stats_future is not None
        seen.append((d, int(row[0]), int(row[1]) + int(n_stats_future[0])))
        return RunSummary(run_id="fake", run_date=d, n_slate=2)

    return fake


def test_results_hidden_at_predict_time_and_restored_after(replay_cfg: ReplayConfig) -> None:
    seen: list[tuple[date, int, int]] = []
    fake = _fake_pretip_factory(seen)
    con = duckdb.connect(str(replay_cfg.work_db), read_only=True)
    dates = season_dates(con, 2025)
    con.close()
    state = ReplayState.load(replay_cfg.state_path)
    for d in dates[:3]:
        process_date(replay_cfg, state, d, pretip=fake)
    assert seen and all(future == 0 for _, _, future in seen)  # nothing on/after d is visible
    assert [past for _, past, _ in seen][-1] > [past for _, past, _ in seen][0]  # grows as restored
    con = duckdb.connect(str(replay_cfg.work_db), read_only=True)
    try:
        n_vis = con.execute("SELECT count(*) FROM games WHERE home_pts IS NOT NULL").fetchone()
        mx = con.execute("SELECT max(game_date) FROM games WHERE home_pts IS NOT NULL").fetchone()
    finally:
        con.close()
    assert n_vis is not None and mx is not None and mx[0] == dates[2]
    assert all(
        v["phase"] == "done" and v["t30"].startswith("skipped")
        for v in state.data["dates"].values()
    )
    assert replay_cfg.full_db.exists()


def test_resume_skips_done_dates_and_survives_a_crash(replay_cfg: ReplayConfig) -> None:
    calls: list[date] = []

    def crashing(cfg: ReplayConfig, d: date, rt: datetime) -> RunSummary:
        if len(calls) >= 2:
            raise KeyboardInterrupt  # simulated kill mid-chunk
        calls.append(d)
        return RunSummary(run_id="x", run_date=d)

    with pytest.raises(KeyboardInterrupt):
        run_chunk(replay_cfg, 10, pretip=crashing)
    st = ReplayState.load(replay_cfg.state_path)
    done = [k for k, v in st.data["dates"].items() if v["phase"] == "done"]
    assert len(done) == 2
    calls.clear()

    def recording(cfg: ReplayConfig, d: date, rt: datetime) -> RunSummary:
        calls.append(d)
        return RunSummary("y", d)

    remaining = run_chunk(replay_cfg, 2, pretip=recording)
    assert len(set(calls)) == 2 and not set(map(str, calls)) & set(done)
    assert remaining >= 1


def test_step_failure_is_recorded_and_replay_advances(replay_cfg: ReplayConfig) -> None:
    def boom(cfg: ReplayConfig, d: date, rt: datetime) -> RunSummary:
        raise RuntimeError("model blew up")

    state = ReplayState.load(replay_cfg.state_path)
    con = duckdb.connect(str(replay_cfg.work_db), read_only=True)
    d0 = season_dates(con, 2025)[0]
    con.close()
    st = process_date(replay_cfg, state, d0, pretip=boom)
    assert st["phase"] == "done" and st["failures"][0]["step"].startswith("pretip@")
    assert "model blew up" in st["failures"][0]["error"]


def test_init_refuses_real_database_name(tmp_path: Path) -> None:
    src = tmp_path / "src.duckdb"
    shutil.copy2(__file__, src)  # content irrelevant: refusal happens before any read
    cfg = ReplayConfig(
        season=2025,
        work_db=tmp_path / "nba.duckdb",
        full_db=tmp_path / "f.duckdb",
        out_dir=tmp_path,
        model_cache=tmp_path,
    )
    with pytest.raises(SystemExit):
        init_replay_db(src, cfg)


def test_parlay_evaluate_stamps_shadow_rows_with_the_replay_clock(tmp_path: Path) -> None:
    """A replayed ``evaluate --now`` must not stamp rows with the wall clock."""
    from tests.parlay.test_joint import _run

    _run(tmp_path, ["--now", "2026-10-08T20:00"])
    paper = duckdb.connect(str(tmp_path / "paper.duckdb"))
    rows = paper.execute("SELECT DISTINCT created_at FROM shadow_predictions").fetchall()
    assert rows == [(datetime(2026, 10, 8, 20, 0),)]


def test_descriptive_metrics_reads_only_production_primary(tmp_path: Path) -> None:
    from nba.daily.replay_season import descriptive_metrics
    from nba.daily.settle import ELIG_DDL
    from nba.daily.store import ensure_tables

    full = tmp_path / "full.duckdb"
    f = duckdb.connect(str(full))
    f.execute("CREATE TABLE games (game_id VARCHAR, season INT, home_pts INT)")
    f.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes DOUBLE)")
    f.execute("INSERT INTO games VALUES ('g1', 2025, 100), ('g2', 2025, 90)")
    f.execute("INSERT INTO player_game_stats VALUES ('g1', 1, 30.0), ('g1', 2, 10.0)")
    f.close()
    con = duckdb.connect(str(tmp_path / "work.duckdb"))
    con.execute("CREATE TABLE games (game_id VARCHAR, season INT, home_pts INT)")
    con.execute("INSERT INTO games VALUES ('g1', 2025, 100), ('g2', 2025, 90)")
    ensure_tables(con)
    con.execute(ELIG_DDL)
    ins = (
        "INSERT INTO forward_scores_elig VALUES (now(), ?, DATE '2026-01-01', 2025, ?, 'v', ?, ?, "
        "'r', now(), now(), 60, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL, NULL, 'scored')"
    )
    # win: injury row for g1 (primary), mov rows for g1 and g2 (g2 has no injury row -> primary)
    con.execute(
        ins,
        ["g1", "rung0_injury_elo", "win_prob_home", -1, 1.0, 0.8, None, None, None, 0.223, 0.04],
    )
    con.execute(
        ins, ["g1", "rung0_mov_elo", "win_prob_home", -1, 1.0, 0.6, None, None, None, 0.51, 0.16]
    )
    con.execute(
        ins, ["g2", "rung0_mov_elo", "win_prob_home", -1, 0.0, 0.3, None, None, None, 0.357, 0.09]
    )
    # a shadow arm row must never be read
    con.execute(
        ins, ["g1", "props_context_residual_int", "pts", 1, 10.0, 9.0, 2.0, 12.0, 1.5, None, None]
    )
    con.execute(
        ins, ["g1", "props_context_residual", "pts", 1, 10.0, 9.0, 2.0, 12.0, 1.5, None, None]
    )
    out = descriptive_metrics(con, full, 2025)
    assert out["win_primary"]["n_games"] == 2 and out["win_primary"]["n_from_injury_elo"] == 1
    assert abs(out["win_primary"]["log_loss"]["mean"] - (0.223 + 0.357) / 2) < 1e-9
    assert out["props_primary"]["pts"]["n_scored"] == 1  # the _int arm is excluded
    assert out["props_primary"]["pts"]["bias_pred_minus_actual"]["mean"] == -1.0
    assert out["coverage"]["player_games_played"] == 2
