"""Recent-role P(play) shadow: planted-future guards, dedup, leakage, settle, identical primary."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest

from nba.daily import pipeline
from nba.daily.canonical_dump import canonical_frames
from nba.daily.pipeline import run_daily
from nba.daily.pplay_shadow import features as F
from nba.daily.pplay_shadow import model as M
from nba.daily.pplay_shadow.run import run_shadow
from nba.daily.pplay_shadow.settle import settle_pplay_shadow
from nba.daily.pplay_shadow.state import STAGE1_PROD_COLS
from nba.daily.predict import load_elo_params
from nba.ingest.cache import insert_rows
from tests.daily.conftest import RUN_DATE, STAT_COLS, T1, T2, T3, TIPOFF, _box, slate_games

GID = "0022600001"
ELO = load_elo_params()
MADE = TIPOFF - timedelta(minutes=120)


def _slate() -> list[F.SlateGame]:
    return [F.SlateGame(GID, T1, T2, TIPOFF)]


def _wide(extra: tuple[int, ...] = ()) -> pl.DataFrame:
    """Production-format rows for the T1 and T2 rosters (ids 1000-1005, 1100-1105)."""
    rows = []
    for pid in [*range(1000, 1006), *range(1100, 1106), *extra]:
        team = T1 if pid < 1100 or pid in extra else T2
        for stat in ("pts", "reb", "ast", "fg3m"):
            rows.append(
                {
                    "game_id": GID, "team_id": team, "player_id": pid, "stat": stat,
                    "model": "context_residual", "mean": 10.0,
                    "q_grid": json.dumps([float(i) for i in range(19)]),
                    "p_play": 0.9, "p_play_raw": 0.9, "proj_minutes": 25.0,
                }
            )  # fmt: skip
    return pl.DataFrame(rows)


def _run(
    con: duckdb.DuckDBPyConnection, made_at: datetime = MADE, run_id: str = "r1"
) -> dict[str, Any]:
    return run_shadow(
        con, RUN_DATE, _slate(), made_at, run_id, _wide(), {(GID, 1000)}, ELO, min_train=50
    )


def _rows(con: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return con.execute(
        "SELECT player_id, p_play_r, r_reason, in_prod_log, prod_drop_reason, features_sha, "
        "props_cond FROM forward_pplay_shadow ORDER BY run_id, player_id"
    ).fetchall()


def test_feature_list_is_the_frozen_recent_role_set() -> None:
    assert len(F.FEATURES) == len(set(F.FEATURES)) == 44
    assert F.FEATURES[-5:] == list(F.R_COLS)
    assert F.FEATURES[-18:-5] == list(STAGE1_PROD_COLS)


def test_writes_every_wide_row_with_reasons_and_flags(con: duckdb.DuckDBPyConnection) -> None:
    info = _run(con)
    assert info["rows"] == 12 and info["written"] == 12
    by = {r[0]: r for r in _rows(con)}
    # players 1000-1004 played every game; 1005 never played: fewer than 5 prior played games
    assert by[1000][1] is not None and by[1000][2] is None and by[1000][3] is True
    assert by[1001][3] is False and by[1001][4] == "roster_cap"  # logged set is only {1000}
    assert by[1005][1] is None and by[1005][2] == "n_played_prior<5"
    pc = json.loads(str(by[1000][6]))
    assert set(pc) == {"pts", "reb", "ast", "fg3m"} and len(pc["pts"]["q_grid"]) == 19
    assert 0.0 < float(by[1000][1]) < 1.0  # type: ignore[arg-type]


def test_planted_future_changes_nothing_and_past_edit_does(con: duckdb.DuckDBPyConnection) -> None:
    _run(con, run_id="a")
    base = {r[0]: (r[1], r[5]) for r in _rows(con)}
    # tonight's own box (a DNP player plays 48 minutes), a later game, a tonight report row and a
    # report stamped after the run's clock: none may reach any feature
    later = RUN_DATE + timedelta(days=3)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES ('{GID}x', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 90, 99), "
        f"('0022600099', DATE '{later}', 2026, {T1}, {T3}, 80, 70)"
    )
    box = pl.DataFrame(_box(f"{GID}x", T1, 1000, 40) + _box("0022600099", T1, 1000, 30))
    box = box.with_columns(
        pl.when(pl.col("player_id") == 1005)
        .then(48.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    insert_rows(con, "player_game_stats", STAT_COLS, box.select(STAT_COLS))
    con.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) VALUES "
        f"(1000, TIMESTAMP '{RUN_DATE} 23:00:00', '{GID}', 'out', 'nba_official_report')"
    )
    _run(con, run_id="b")
    after = {r[0]: (r[1], r[5]) for r in con.execute(
        "SELECT player_id, p_play_r, features_sha FROM forward_pplay_shadow WHERE run_id = 'b'"
    ).fetchall()}  # fmt: skip
    # run b is deduped to nothing new only when every row is equal; compare via a fresh write
    assert con.execute("SELECT count(*) FROM forward_pplay_shadow").fetchone()[0] == 12  # type: ignore[index]
    assert after == {}  # all 12 rows identical to run a: write-on-change skipped them
    assert base[1005][0] is None
    # control: edit a PRIOR game's minutes -> the row's feature fingerprint must change
    con.execute(
        "UPDATE player_game_stats SET minutes = 3.0 WHERE game_id = '002250"
        "0011' AND player_id = 1000"
    )
    _run(con, run_id="c")
    changed = con.execute(
        "SELECT features_sha FROM forward_pplay_shadow WHERE run_id = 'c' AND player_id = 1000"
    ).fetchall()
    assert changed and changed[0][0] != base[1000][1]


def test_report_after_known_at_is_ignored_but_before_is_used(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _run(con, run_id="a")
    con.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) VALUES "
        f"(1001, TIMESTAMP '{RUN_DATE} 12:00:00', '{GID}', 'questionable', 'nba_official_report')"
    )
    _run(con, run_id="b")  # MADE is 17:30 ET; 12:00 ET is before it and before tip - 60
    got = con.execute(
        "SELECT run_id FROM forward_pplay_shadow WHERE player_id = 1001 ORDER BY run_id"
    ).fetchall()
    assert [g[0] for g in got] == ["a", "b"]  # the report changed 1001's features
    con.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) VALUES "
        f"(1002, TIMESTAMP '{RUN_DATE} 22:00:00', '{GID}', 'doubtful', 'nba_official_report')"
    )
    _run(con, run_id="c")  # 22:00 ET is after the run clock: ignored
    got = con.execute(
        "SELECT run_id FROM forward_pplay_shadow WHERE player_id = 1002 ORDER BY run_id"
    ).fetchall()
    assert [g[0] for g in got] == ["a", "b"]  # nothing new in c: the 22:00 report never entered


def test_after_tip_is_refused_and_dedup_skips_repeats(con: duckdb.DuckDBPyConnection) -> None:
    info = _run(con, made_at=TIPOFF + timedelta(minutes=1))
    assert info["written"] == 0 and info["refused_after_tip"] == 12
    assert _rows(con) == []
    assert _run(con, run_id="x")["written"] == 12
    again = _run(con, run_id="y")
    assert again["written"] == 0 and again["deduped"] == 12


def test_too_few_training_rows_logs_production_only(con: duckdb.DuckDBPyConnection) -> None:
    info = run_shadow(con, RUN_DATE, _slate(), MADE, "r", _wide(), set(), ELO, min_train=10**6)
    assert info["rows_with_r"] == 0
    assert {r[2] for r in _rows(con)} == {"no_model"}


def test_settle_scores_both_columns_once(con: duckdb.DuckDBPyConnection) -> None:
    _run(con)
    assert settle_pplay_shadow(con, TIPOFF)["pplay_scored"] == 0  # game not final
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES ('{GID}', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 101, 99)"
    )
    box = pl.DataFrame(_box(GID, T1, 1000, 20))  # 1000-1004 play, 1005 DNP; T2 players absent
    insert_rows(con, "player_game_stats", STAT_COLS, box.select(STAT_COLS))
    out = settle_pplay_shadow(con, TIPOFF + timedelta(hours=3))
    assert out == {"pplay_scored": 6, "pplay_not_in_box": 6}
    row = con.execute(
        "SELECT y, ll_prod, ll_r FROM forward_pplay_scores WHERE player_id = 1000"
    ).fetchone()
    assert (
        row is not None and row[0] == 1.0 and row[1] == pytest.approx(-__import__("math").log(0.9))
    )
    assert row[2] is not None
    dnp = con.execute("SELECT y, ll_r FROM forward_pplay_scores WHERE player_id = 1005").fetchone()
    assert dnp == (0.0, None)  # DNP-only player: R has no row, production still scored
    assert settle_pplay_shadow(con, TIPOFF + timedelta(hours=4)) == {
        "pplay_scored": 0,
        "pplay_not_in_box": 0,
    }


def test_primary_rows_are_byte_identical_with_the_shadow_on(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.props import forward
    from tests.daily.conftest import add_history
    from tests.fixtures.loader import build_fixture_db

    seen: dict[str, object] = {}

    def fake(*a: object, **k: object) -> object:
        seen["config"] = k.get("config")
        return type("R", (), {"primary": _wide()})()

    monkeypatch.setattr(forward, "predict_slate_context", fake)

    def go(c: duckdb.DuckDBPyConnection, flag: bool) -> pipeline.RunSummary:
        return run_daily(
            c, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True,
            skip_injury=True, with_pplay_shadow=flag,
        )  # fmt: skip

    off = go(con, False)
    other = build_fixture_db(":memory:")
    add_history(other)
    on = go(other, True)
    a = canonical_frames(con)
    b = canonical_frames(other)
    assert a[0].equals(b[0]) and a[1].equals(b[1])
    assert off.n_rows_written == on.n_rows_written
    assert seen["config"] is pipeline._WIDE_CONFIG
    assert "pplay_r_shadow" in on.model_status or on.shadow_errors
    n = other.execute("SELECT count(*) FROM forward_pplay_shadow").fetchone()
    assert n is not None and n[0] == 12 and not on.shadow_errors


def test_shadow_failure_is_recorded_and_never_raises(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True,
        skip_injury=True, with_pplay_shadow=True,
    )  # fmt: skip  # the 12-game fixture cannot fit the context model
    assert s.n_rows_written > 0
    assert any(e.startswith("pplay_r shadow:") for e in s.shadow_errors)
    assert date(2026, 10, 28) == RUN_DATE and M.MODEL_VERSION == "pplay_r_v1"
