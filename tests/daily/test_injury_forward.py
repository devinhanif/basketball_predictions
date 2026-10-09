"""Forward injury-Elo: real-tip leakage rule, coefficient as-of discipline, fallback,
paired report. Fixture DB only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from nba.daily.pipeline import run_daily
from nba.daily.predict import load_elo_params
from nba.daily.report import build_report
from nba.daily.settle import settle_pending
from nba.ingest.availability import ensure_player_availability_table
from nba.models.injury_elo import predict_games
from tests.daily.conftest import RUN_DATE, T1, T2, T3, T4, TIPOFF, before_tip, slate_games

SRC = "nba_official_report"
G1, G2 = "0022600001", "0022600002"
STAR = 1004  # high-composite T1 starter in the synthetic history


def _avail(con: duckdb.DuckDBPyConnection, rows: list[tuple[int, datetime, str, str]]) -> None:
    ensure_player_availability_table(con)
    for pid, as_of, gid, status in rows:
        con.execute(
            "INSERT INTO player_availability VALUES (?,?,?,?,NULL,?,?)",
            [pid, as_of, gid, status, SRC, as_of],
        )


def _seed_history_reports(con: duckdb.DuckDBPyConnection) -> None:
    """Home star OUT 17:00 ET in every other history game (gives coefficient signal)."""
    ids = con.execute(
        "SELECT game_id, game_date FROM games WHERE home_team = ? ORDER BY game_date", [T1]
    ).fetchall()
    _avail(
        con,
        [
            (STAR, datetime(d.year, d.month, d.day, 17, 0), gid, "out")
            for i, (gid, d) in enumerate(ids)
            if i % 2 == 0
        ],
    )


def _games(tip: datetime = TIPOFF) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [G1],
            "game_date": [RUN_DATE],
            "season": [2026],
            "home_team": [T1],
            "away_team": [T2],
            "tipoff": [tip],
        }
    )


def _rows(as_of: datetime) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [G1],
            "player_id": [STAR],
            "status": ["out"],
            "as_of": [as_of],
            "game_date": [RUN_DATE],
        },
        schema_overrides={"as_of": pl.Datetime("us")},
    )


def _predict(
    con: duckdb.DuckDBPyConnection,
    rows: pl.DataFrame,
    now: datetime | None = None,
    **kw: object,
) -> pl.DataFrame:
    return predict_games(
        con, now or before_tip(), _games(), rows, elo_params=load_elo_params(),
        min_signal_games=kw.pop("min_signal_games", 5), **kw,
    )  # type: ignore[arg-type]  # fmt: skip


# tip 23:30 UTC = 19:30 ET -> cutoff 18:30 ET
def test_report_after_tip_minus_lead_is_ignored(con: duckdb.DuckDBPyConnection) -> None:
    _seed_history_reports(con)
    now = TIPOFF - timedelta(minutes=10)  # "now" is late enough to see both rows
    late = _predict(con, _rows(datetime(2026, 10, 28, 18, 45)), now)
    r = late.row(0, named=True)
    assert not r["has_report"] and r["primary_model"] == "rung0_mov_elo"
    assert r["fallback_reason"] and r["d_out"] == 0.0
    assert r["p_injury_elo"] == pytest.approx(r["p_mov_elo"])
    ok = _predict(con, _rows(datetime(2026, 10, 28, 18, 30)), now).row(0, named=True)  # exactly -60
    assert ok["has_report"] and ok["primary_model"] == "rung0_injury_elo"
    assert ok["fallback_reason"] is None and ok["d_out"] < 0  # home player out hurts home


def test_report_after_now_is_ignored(con: duckdb.DuckDBPyConnection) -> None:
    # before_tip() = 120 min before tip = 17:30 ET; a 18:00 ET row is "from the future"
    r = _predict(con, _rows(datetime(2026, 10, 28, 18, 0))).row(0, named=True)
    assert not r["has_report"]


def test_unknown_tipoff_falls_back(con: duckdb.DuckDBPyConnection) -> None:
    g = _games().with_columns(pl.lit(None, dtype=pl.Datetime("us")).alias("tipoff"))
    out = predict_games(
        con, before_tip(), g, _rows(datetime(2026, 10, 28, 12, 0)),
        elo_params=load_elo_params(),
    )  # fmt: skip
    assert out["fallback_reason"][0] == "unknown_tipoff"


def test_injury_signal_moves_probability(con: duckdb.DuckDBPyConnection) -> None:
    _seed_history_reports(con)
    r = _predict(con, _rows(datetime(2026, 10, 28, 12, 0))).row(0, named=True)
    assert r["n_signal_games"] >= 5 and r["coef_out"] != 0.0
    assert r["p_injury_elo"] != pytest.approx(r["p_mov_elo"])
    assert 0 < r["p_injury_elo"] < 1


def test_future_games_do_not_affect_coefficients(con: duckdb.DuckDBPyConnection) -> None:
    _seed_history_reports(con)
    rows = _rows(datetime(2026, 10, 28, 12, 0))
    before = _predict(con, rows).row(0, named=True)
    # completed games ON and AFTER the slate day with extreme outcomes + reports
    for i in range(30):
        d = RUN_DATE + timedelta(days=i)
        gid = f"F{i:04d}"
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES (?,?,2026,?,?,60,140)",
            [gid, d, T1, T2],
        )
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast) "
            "VALUES (?, ?, ?, 40, 99, 20, 20)",
            [gid, STAR, T1],
        )
        _avail(con, [(STAR, datetime(d.year, d.month, d.day, 12, 0), gid, "out")])
    after = _predict(con, rows).row(0, named=True)
    for k in ("coef_out", "coef_doubt", "n_train_games", "n_signal_games", "p_mov_elo"):
        assert after[k] == pytest.approx(before[k])
    assert after["p_injury_elo"] == pytest.approx(before["p_injury_elo"])


def _slate_rows(con: duckdb.DuckDBPyConnection) -> None:
    for gid, h, a in [(G1, T1, T2), (G2, T3, T4)]:
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team) "
            "VALUES (?,?,2026,?,?)",
            [gid, RUN_DATE, h, a],
        )


def test_pipeline_writes_both_models_and_paired_report(con: duckdb.DuckDBPyConnection) -> None:
    _seed_history_reports(con)
    _slate_rows(con)
    # both reports are stamped 15:00 ET, well before either real tip-off
    _avail(
        con,
        [
            (STAR, datetime(2026, 10, 28, 15, 0), G1, "out"),
            (1104, datetime(2026, 10, 28, 15, 0), G2, "out"),
        ],
    )
    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: slate_games(), now=before_tip(),
        skip_ingest=True, skip_injury=True, with_props=False,
    )  # fmt: skip
    assert "rung0_injury_elo" in s.model_status
    rows = con.execute(
        "SELECT game_id, model_name, prediction FROM forward_predictions "
        "WHERE target='win_prob_home' ORDER BY game_id, model_name"
    ).fetchall()
    by = {(g, m): json.loads(p) for g, m, p in rows}
    assert (G1, "rung0_injury_elo") in by and (G1, "rung0_mov_elo") in by
    assert by[(G1, "rung0_injury_elo")]["primary"] is True
    assert by[(G1, "rung0_mov_elo")]["primary"] is False
    assert by[(G1, "rung0_mov_elo")]["fallback_reason"] is None
    assert (G2, "rung0_injury_elo") in by
    # settle both and read the paired report
    for gid, hp in [(G1, 120), (G2, 90)]:
        con.execute("UPDATE games SET home_pts = ?, away_pts = 100 WHERE game_id = ?", [hp, gid])
    r = settle_pending(con, TIPOFF + timedelta(days=1))
    assert r["win_scored"] == 4
    txt = build_report(con)
    assert "Paired: rung0_injury_elo minus rung0_mov_elo" in txt
    assert "games scored by both: 2" in txt


def test_pipeline_late_report_falls_back_to_mov_elo(con: duckdb.DuckDBPyConnection) -> None:
    _slate_rows(con)
    _avail(con, [(STAR, datetime(2026, 10, 28, 18, 45), G1, "out")])  # after G1 tip-60
    run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: slate_games(), now=before_tip(),
        skip_ingest=True, skip_injury=True, with_props=False,
    )  # fmt: skip
    rows = con.execute(
        "SELECT model_name, prediction FROM forward_predictions "
        "WHERE target='win_prob_home' AND game_id = ?",
        [G1],
    ).fetchall()
    assert [m for m, _ in rows] == ["rung0_mov_elo"]
    p = json.loads(rows[0][1])
    assert p["primary"] is True and p["fallback_reason"]


_ = date


def test_pipeline_live_slate_not_in_games_table_still_uses_injury_model(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Live shape: an upcoming game has NO ``games`` row (only played games are fetched), so a
    report row's game_id can only come from the schedule. The injury model must still see it
    (before the fix every live game silently fell back to MOV-Elo)."""
    _seed_history_reports(con)
    assert con.execute(
        "SELECT count(*) FROM games WHERE game_id IN (?,?)", [G1, G2]
    ).fetchone() == (0,)
    _avail(con, [(STAR, datetime(2026, 10, 28, 15, 0), G1, "out")])
    run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: slate_games(), now=before_tip(),
        skip_ingest=True, skip_injury=True, with_props=False,
    )  # fmt: skip
    by = {
        (g, m): json.loads(p)
        for g, m, p in con.execute(
            "SELECT game_id, model_name, prediction FROM forward_predictions "
            "WHERE target='win_prob_home'"
        ).fetchall()
    }
    inj = by[(G1, "rung0_injury_elo")]
    assert inj["primary"] is True and inj["fallback_reason"] is None and inj["d_out"] < 0
    assert by[(G1, "rung0_mov_elo")]["primary"] is False
    assert by[(G2, "rung0_mov_elo")]["primary"] is True  # no report rows for G2 -> fallback
    assert con.execute("SELECT count(*) FROM games WHERE game_id = ?", [G1]).fetchone() == (0,)


def test_report_game_id_resolved_from_schedule_when_not_in_games(
    con: duckdb.DuckDBPyConnection,
) -> None:
    from nba.parse.availability import resolve_game_id

    assert resolve_game_id(con, RUN_DATE, "BOS", "LAL") is None
    key = {(RUN_DATE, 1610612747, 1610612738): "G-X"}  # (date, LAL home, BOS away)
    assert resolve_game_id(con, RUN_DATE, "BOS", "LAL", key) == "G-X"
    assert resolve_game_id(con, RUN_DATE, "LAL", "BOS", key) == "G-X"  # swapped orientation
    assert resolve_game_id(con, RUN_DATE, "BOS", "NYK", key) is None
