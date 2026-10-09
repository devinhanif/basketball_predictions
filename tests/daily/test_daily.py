from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.daily.ingest_step import incremental_ingest, missing_boxscore_game_ids
from nba.daily.injury import candidate_slots, out_players, pull_latest_report, to_report_dt
from nba.daily.pipeline import run_daily
from nba.daily.predict import fit_mov_elo, load_elo_params, resolve_production
from nba.daily.report import build_report, cluster_bootstrap_mean
from nba.daily.schedule import parse_schedule_frame, slate_for_date
from nba.daily.season import season_int_for_date, season_str, season_str_for_date
from nba.daily.settle import normal_crps, settle_pending
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions, ensure_tables
from nba.ingest.games import season_to_int
from tests.daily.conftest import RUN_DATE, T1, T2, TIPOFF, before_tip, slate_games

GAMES_INS = "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
GAMES_INS += "home_pts, away_pts) VALUES"
STATS_INS = "INSERT INTO player_game_stats "
STATS_INS += "(game_id, player_id, team_id, minutes, pts, reb, ast, fg3m)"
TIP_ALL = lambda season: slate_games()  # noqa: E731


def _run(con: duckdb.DuckDBPyConnection, now: datetime, **kw: object) -> object:
    return run_daily(
        con, RUN_DATE, schedule_fn=TIP_ALL, now=now, skip_ingest=True, skip_injury=True, **kw
    )  # type: ignore[arg-type]


# -- season encoding ---------------------------------------------------------


def test_season_encoding() -> None:
    assert season_to_int("2025-26") == 2025
    assert season_to_int("2026-27") == 2026
    assert season_str(2026) == "2026-27" and season_str(1999) == "1999-00"
    assert season_int_for_date(date(2026, 10, 28)) == 2026
    assert season_int_for_date(date(2027, 4, 1)) == 2026
    assert season_str_for_date(date(2026, 6, 10)) == "2025-26"


# -- schedule ----------------------------------------------------------------


def test_parse_schedule_and_slate() -> None:
    raw = pl.DataFrame(
        {
            "gameId": ["0022600001", "0012600001", "0022600002"],
            "gameDateTimeUTC": [
                "2026-10-28T23:30:00Z",
                "2026-10-28T23:30:00Z",
                "2026-10-29T02:00:00Z",
            ],
            "homeTeam_teamId": [T1, T1, T1],
            "awayTeam_teamId": [T2, T2, T2],
        }
    )  # fmt: skip
    games = parse_schedule_frame(raw)
    assert [g.game_id for g in games] == ["0022600001", "0022600002"]  # preseason dropped
    # 02:00Z on the 29th is still the 28th in Eastern time
    assert len(slate_for_date(games, date(2026, 10, 28))) == 2


# -- leakage: tip-off guard ---------------------------------------------------


def test_store_refuses_made_at_not_before_tipoff(con: duckdb.DuckDBPyConnection) -> None:
    ensure_tables(con)
    p = ForwardPrediction("g", TIPOFF, "m", "v1", "win_prob_home", {"p_home": 0.6})
    for bad in (TIPOFF, TIPOFF + timedelta(minutes=1)):
        with pytest.raises(LeakageError):
            append_predictions(con, "r", bad, [p])
    assert con.execute("SELECT count(*) FROM forward_predictions").fetchone() == (0,)
    assert append_predictions(con, "r", TIPOFF - timedelta(seconds=1), [p]) == 1


def test_batch_with_one_late_game_writes_nothing(con: duckdb.DuckDBPyConnection) -> None:
    ensure_tables(con)
    ok = ForwardPrediction("g1", TIPOFF + timedelta(hours=3), "m", "v", "t", {})
    late = ForwardPrediction("g2", TIPOFF, "m", "v", "t", {})
    with pytest.raises(LeakageError):
        append_predictions(con, "r", TIPOFF, [ok, late])
    assert con.execute("SELECT count(*) FROM forward_predictions").fetchone() == (0,)


def test_run_after_tipoff_predicts_nothing_and_flags(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, TIPOFF + timedelta(hours=1))
    assert s.n_slate == 2 and s.n_refused_after_tipoff == 1  # 2nd game tips 3h later
    assert s.n_predicted_games == 1
    s2 = _run(con, TIPOFF + timedelta(hours=5))
    assert s2.n_predicted_games == 0 and s2.n_refused_after_tipoff == 2
    n = con.execute("SELECT count(*) FROM forward_predictions WHERE made_at >= tipoff").fetchone()
    assert n == (0,)


# -- leakage: features as-of --------------------------------------------------


def test_elo_ignores_slate_day_and_future_games(con: duckdb.DuckDBPyConnection) -> None:
    params = load_elo_params()
    m0, n0 = fit_mov_elo(con, RUN_DATE, 2026, params)
    # plant an extreme same-day and a future result: must not change the fit
    con.execute(
        GAMES_INS + " ('X1', ?, 2026, ?, ?, 150, 60), ('X2', ?, 2026, ?, ?, 150, 60)",
        [RUN_DATE, T1, T2, RUN_DATE + timedelta(days=3), T1, T2],
    )
    m1, n1 = fit_mov_elo(con, RUN_DATE, 2026, params)
    assert n0 == n1 and m0.ratings_ == m1.ratings_


def test_elo_applies_season_boundary_regression(con: duckdb.DuckDBPyConnection) -> None:
    params = load_elo_params()
    same, _ = fit_mov_elo(con, date(2026, 6, 1), 2025, params)
    new, _ = fit_mov_elo(con, date(2026, 6, 1), 2026, params)
    c = params["season_carryover"]
    assert new.ratings_[T1] == pytest.approx(c * same.ratings_[T1] + (1 - c) * 1500.0)


def test_props_features_exclude_slate_day(con: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.predict import rolling_prop_baseline

    a = rolling_prop_baseline(con, RUN_DATE, [T1, T2], set())
    con.execute(
        GAMES_INS + " ('X1', ?, 2026, ?, ?, 90, 80)",
        [RUN_DATE, T1, T2],
    )
    con.execute(
        STATS_INS + " VALUES ('X1', 1000, ?, 40, 99, 99, 99, 99)",
        [T1],
    )
    b = rolling_prop_baseline(con, RUN_DATE, [T1, T2], set())
    assert a.equals(b) and a.height > 0
    out = rolling_prop_baseline(con, RUN_DATE, [T1, T2], {1000})
    assert 1000 not in out["player_id"].to_list()


def test_run_writes_only_pre_tipoff_rows_with_asof_marker(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, before_tip())
    assert s.n_predicted_games == 2 and s.n_refused_after_tipoff == 0
    assert s.model_status["rung0_mov_elo"].startswith("unregistered")
    rows = con.execute(
        "SELECT made_at, tipoff, target, prediction FROM forward_predictions"
    ).fetchall()
    assert rows and all(r[0] < r[1] for r in rows)
    for _, _, tgt, pj in rows:
        assert json.loads(pj)["features_as_of_before"] == RUN_DATE.isoformat() or tgt
    wins = [json.loads(r[3]) for r in rows if r[2] == "win_prob_home"]
    assert len(wins) == 2 and all(0 < w["p_home"] < 1 for w in wins)
    # DNP-ish bench player (minutes 0 in history) is never a prop subject
    pids = {
        r[0] for r in con.execute("SELECT DISTINCT player_id FROM forward_predictions").fetchall()
    }
    assert 1005 not in pids and 1000 in pids


def test_second_run_is_append_only(con: duckdb.DuckDBPyConnection) -> None:
    _run(con, before_tip(120), with_props=False, dedup=False)
    _run(con, before_tip(60), with_props=False, dedup=False)
    assert con.execute("SELECT count(*) FROM forward_predictions").fetchone() == (4,)


def test_second_identical_run_is_deduped_and_reported(con: duckdb.DuckDBPyConnection) -> None:
    s1 = _run(con, before_tip(120), with_props=False)
    s2 = _run(con, before_tip(60), with_props=False)
    assert (s1.n_rows_written, s1.n_rows_deduped) == (2, 0)  # type: ignore[attr-defined]
    assert (s2.n_rows_written, s2.n_rows_deduped) == (0, 2)  # type: ignore[attr-defined]
    assert con.execute("SELECT count(*) FROM forward_predictions").fetchone() == (2,)


# -- injury ------------------------------------------------------------------


def test_candidate_slots_strictly_before_limit() -> None:
    limit = datetime(2026, 10, 28, 23, 30)  # 19:30 ET
    slots = candidate_slots(limit)
    assert slots[0] < to_report_dt(limit) and slots[0].minute % 15 == 0
    assert slots[0] == datetime(2026, 10, 28, 19, 15)
    assert all(a > b for a, b in zip(slots, slots[1:], strict=False))


def test_pull_latest_report_takes_newest_existing(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seen: list[datetime] = []
    newest_ok = datetime(2026, 10, 28, 17, 0)

    def probe(url: str) -> bool:
        return "2026-10-28_05_00PM" in url

    def pull(c: object, slot: datetime, **kw: object) -> None:
        seen.append(slot)

    got = pull_latest_report(con, TIPOFF, data_dir=tmp_path, probe=probe, pull=pull, name_index={})
    assert got == newest_ok and seen == [newest_ok]
    assert (
        pull_latest_report(
            con, TIPOFF, data_dir=tmp_path, probe=lambda u: False, pull=pull, name_index={}
        )
        is None
    )


def test_out_players_uses_latest_prior_snapshot(con: duckdb.DuckDBPyConnection) -> None:
    from nba.ingest.availability import ensure_player_availability_table

    ensure_player_availability_table(con)
    src = "nba_official_report"
    con.execute(
        "INSERT INTO player_availability VALUES "
        "(1, '2026-10-28 15:00', NULL, 'out', NULL, ?, now()),"
        "(2, '2026-10-28 15:00', NULL, 'questionable', NULL, ?, now()),"
        "(3, '2026-10-28 18:00', NULL, 'out', NULL, ?, now()),"  # published after the cutoff
        "(4, '2026-10-20 15:00', NULL, 'out', NULL, ?, now())",  # stale
        [src, src, src, src],
    )
    assert out_players(con, datetime(2026, 10, 28, 20, 0)) == {1}  # cutoff 16:00 ET


def test_injury_failure_does_not_block_predictions(con: duckdb.DuckDBPyConnection) -> None:
    def boom(c: object, slot: datetime, **kw: object) -> None:
        raise RuntimeError("pdf parse")

    s = run_daily(
        con, RUN_DATE, schedule_fn=TIP_ALL, now=before_tip(), skip_ingest=True,
        probe=lambda u: True, pull_report=boom, name_index={}, with_props=False,
    )  # fmt: skip
    assert s.injury_status.startswith("failed") and s.n_predicted_games == 2


# -- ingest ------------------------------------------------------------------


def test_incremental_ingest_pulls_only_missing(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        GAMES_INS + " ('NEWG', '2026-10-27', 2026, ?, ?, 100, 90)",
        [T1, T2],
    )
    assert missing_boxscore_game_ids(con, 2026) == ["NEWG"]
    pulled: list[str] = []
    fresh = pl.DataFrame(
        {"game_id": ["NEWG2"], "game_date": [date(2026, 10, 27)], "season": [2026],
         "home_team": [T3_], "away_team": [T2], "home_pts": [None], "away_pts": [None]}
    ).cast({"home_pts": pl.Int64, "away_pts": pl.Int64})  # fmt: skip

    out = incremental_ingest(
        con, "2026-27", 2026, fetch_games=lambda s: fresh, pull_box=lambda c, g: pulled.append(g)
    )
    assert pulled == ["NEWG"] and out["boxscores_pulled"] == 1
    assert missing_boxscore_game_ids(con, 2026) == ["NEWG"]  # fake puller wrote nothing


T3_ = 1610612747


# -- registry ----------------------------------------------------------------


def test_resolve_production_fallback_and_hit(
    con: duckdb.DuckDBPyConnection, tmp_path: object
) -> None:
    from pathlib import Path

    from nba.registry import get_registry

    reg = get_registry(con, store_dir=Path(str(tmp_path)))
    r = resolve_production(reg, "rung0_mov_elo")
    assert r.status == "unregistered_config_fallback"
    art = Path(str(tmp_path)) / "w.bin"
    art.write_bytes(b"x")
    v = reg.next_version("rung0_mov_elo")
    reg.log_model("rung0_mov_elo", v, str(art), {"log_loss": 0.6}, {}, {})
    reg.promote("rung0_mov_elo", v)
    r2 = resolve_production(reg, "rung0_mov_elo")
    assert r2.status == "registry" and r2.artifact_path is not None


# -- settle + report ----------------------------------------------------------


def _complete_slate(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        GAMES_INS + " ('0022600001', ?, 2026, ?, ?, 120, 100)",
        [RUN_DATE, T1, T2],
    )
    for pid, pts in [(1000, 30), (1001, 12)]:
        con.execute(
            STATS_INS + " VALUES ('0022600001', ?, ?, 30, ?, 5, 5, 2)",
            [pid, T1, pts],
        )


def test_settle_scores_and_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    _run(con, before_tip())
    assert settle_pending(con, TIPOFF) == {"win_scored": 0, "prop_scored": 0, "prop_dnp": 0}
    _complete_slate(con)
    r = settle_pending(con, TIPOFF + timedelta(days=1))
    assert r["win_scored"] == 1 and r["prop_scored"] > 0 and r["prop_dnp"] > 0
    assert settle_pending(con, TIPOFF + timedelta(days=1)) == {
        "win_scored": 0, "prop_scored": 0, "prop_dnp": 0,
    }  # fmt: skip
    ll, br = con.execute(
        "SELECT log_loss, brier FROM forward_scores WHERE target='win_prob_home'"
    ).fetchone()  # type: ignore[misc]
    p = con.execute(
        "SELECT json_extract(prediction,'$.p_home')::DOUBLE FROM forward_predictions "
        "WHERE game_id='0022600001' AND target='win_prob_home'"
    ).fetchone()[0]  # type: ignore[index]
    assert ll == pytest.approx(-np.log(p)) and br == pytest.approx((1 - p) ** 2)


def test_normal_crps_properties() -> None:
    assert normal_crps(10, 3, 10) < normal_crps(10, 3, 16)
    assert normal_crps(0, 1, 0) == pytest.approx(0.2337, abs=1e-3)


def test_cluster_bootstrap_widens_vs_iid() -> None:
    rng = np.random.default_rng(1)
    clusters = np.repeat(np.arange(20), 10)
    v = rng.normal(size=20)[clusters] + 0.01 * rng.normal(size=200)  # strong cluster effect
    ci = cluster_bootstrap_mean(v, clusters)
    assert ci.n == 200 and ci.n_clusters == 20 and ci.lo < ci.point < ci.hi
    assert ci.hi - ci.lo > 0.3  # iid CI would be ~0.14
    assert np.isnan(cluster_bootstrap_mean(v[:10], clusters[:10]).lo)  # single cluster


def test_report_and_rollover_trigger(con: duckdb.DuckDBPyConnection) -> None:
    _run(con, before_tip())
    _complete_slate(con)
    settle_pending(con, TIPOFF + timedelta(days=1))
    txt = build_report(con)
    assert "n=1" in txt and "ROLLOVER" not in txt
    for i in range(20):  # 21 completed 2026 games now
        con.execute(
            GAMES_INS + " (?, ?, 2026, ?, ?, 100, 90)",
            [f"R{i}", RUN_DATE + timedelta(days=i + 1), T1, T2],
        )
    assert "ROLLOVER TRIGGER FIRED" in build_report(con)


def test_pull_latest_report_default_uses_team_aware_resolver(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Live default must NOT build the last-name-keyed pbp index (it raises on real data:
    'ambiguous player names'); it hands the puller a NameResolver and flushes unmatched."""
    from nba.parse.availability import NameResolver

    got: dict[str, object] = {}

    def pull(c: object, slot: datetime, **kw: object) -> None:
        got.update(kw)
        resolver = kw["resolver"]
        assert isinstance(resolver, NameResolver)
        resolver.unmatched.append(("Nobody Known", "Boston Celtics", "2026-10-28", "no_candidate"))

    slot = pull_latest_report(
        con, TIPOFF, data_dir=tmp_path, probe=lambda u: "2026-10-28_05_00PM" in u, pull=pull
    )
    assert slot == datetime(2026, 10, 28, 17, 0)
    assert "name_index" not in got and isinstance(got["resolver"], NameResolver)
    assert "Nobody Known" in (tmp_path / "availability_backfill" / "unmatched.csv").read_text()
