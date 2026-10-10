"""Bulk possession rebuild (nba/parse/rebuild_possessions.py) on a two-game file DB.

Game A carries a post-hoc correction (a rebound filed after the period end); the stored
rows for it are built with the OLD ordering (``period, action_number``) so the table starts
with a negative-duration possession. Game B is clean and must come through unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl
import pytest

import nba.parse.possessions as possessions_mod
import nba.parse.rebuild_possessions as rp
from nba.db.connect import connect
from nba.parse.history import _starters
from nba.parse.lineups import attach_lineups_to_possessions, track_lineups_with_report
from nba.parse.loader import load_possession_lineups, load_possessions, load_stints
from nba.parse.possessions import parse_possessions
from tests.parse.helpers import AWAY, HOME, PbpBuilder
from tests.parse.test_ordering import _shot, appended_correction_game

GAME_A = "0099900001"
GAME_B = "0099900002"


def _clean_game() -> PbpBuilder:
    b = PbpBuilder(GAME_B)
    _shot(b, 1, "PT10M00.00S", HOME, 1, True, score_home="2", score_away="0")
    _shot(b, 1, "PT09M40.00S", AWAY, 11, True, score_home="2", score_away="2")
    _shot(b, 1, "PT09M20.00S", HOME, 2, False, score_home="2", score_away="2")
    return b


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """File DB with both games, stored possessions from the old ordering; PBP cache in tmp."""
    db = tmp_path / "t.duckdb"
    data = tmp_path / "data"
    (data / "pbp").mkdir(parents=True)
    con = connect(db)
    mp = pytest.MonkeyPatch()
    for builder, hp, ap in ((appended_correction_game(), 0, 2), (_clean_game(), 2, 2)):
        pbp = builder.df()
        gid = builder.game_id
        pbp.write_parquet(data / "pbp" / f"{gid}.parquet")
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
            "away_pts) VALUES (?, DATE '2023-10-25', 2023, ?, ?, ?, ?)",
            [gid, HOME, AWAY, hp, ap],
        )
        fga = {
            GAME_A: {1: 1, 2: 1, 11: 1},
            GAME_B: {1: 1, 2: 1, 11: 1},
        }[gid]
        for team, players in ((HOME, [1, 2, 3, 4, 5]), (AWAY, [11, 12, 13, 14, 15])):
            for pid in players:
                con.execute(
                    "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, starter, "
                    "fga, fta, oreb, tov) VALUES (?, ?, ?, 20.0, true, ?, 0, 0, 0)",
                    [gid, pid, team, fga.get(pid, 0)],
                )
        box = con.execute(
            "SELECT player_id, team_id, starter, minutes FROM player_game_stats WHERE game_id = ?",
            [gid],
        ).pl()
        starters = _starters(box)
        assert starters is not None
        roster = {t: g["player_id"].to_list() for (t,), g in box.group_by("team_id")}
        stints, _ = track_lineups_with_report(pbp, starters, roster)
        mp.setattr(
            possessions_mod, "in_game_order", lambda df: df.sort(["period", "action_number"])
        )
        old = parse_possessions(pbp)
        mp.undo()
        old = attach_lineups_to_possessions(old, stints)
        load_possessions(con, old)
        load_possession_lineups(con, old)
        load_stints(con, stints)
    con.close()
    return db, data


def test_rebuild_fixes_order_and_leaves_clean_game_alone(tmp_path: Path) -> None:
    db, data = _seed(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    old = rp.load_stored(con)
    rb = rp.compute_all(con, data)
    team_box, pts = rp._inputs(con)
    before = rp.measure(old, team_box, pts)
    after = rp.measure(rb.possessions, team_box, pts)
    renum = rp.renumbering(old, rb.possessions)
    con.close()

    assert before["neg_duration"] == 1 and after["neg_duration"] == 0
    assert after["games_with_defect"] == 0
    assert after["n_poss"] == before["n_poss"] == 6
    assert after["lineup_null_rows"] == 0
    assert renum["games_renumbered"] == 1 and renum["renumbered_game_ids"] == [GAME_A]
    assert renum["games_count_changed"] == 0 and renum["net_row_change"] == 0
    assert all(g.ok for g in rp.evaluate_gates(before, after))

    # clean game: identical rows including lineups
    b_old = old.filter(pl.col("game_id") == GAME_B).sort(rp._KEY)
    b_new = rb.possessions.filter(pl.col("game_id") == GAME_B).sort(rp._KEY)
    assert b_old.equals(b_new)
    # lineups re-attached at the corrected clock: every possession has a five on each side
    a_new = rb.possessions.filter(pl.col("game_id") == GAME_A)
    assert a_new["off_players"].list.len().to_list() == [5, 5, 5]


def test_backup_then_chunked_write_round_trip_is_idempotent(tmp_path: Path) -> None:
    db, data = _seed(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    rb = rp.compute_all(con, data)
    old = rp.load_stored(con)
    snaps = rp.snapshot_pre_fix(con, tmp_path / "bk", ts="t")
    con.close()

    backup = pl.read_parquet(snaps[0])
    assert snaps[0].name == "possessions_pre_order_fix_t_possessions.parquet"
    assert backup.height == old.height  # the whole table, all columns
    assert set(rp._SCHEMA) <= set(backup.columns)
    assert int((backup["clock_end"] > backup["clock_start"]).sum()) == 1  # the pre-fix defect

    w = duckdb.connect(str(db))
    rp.write_chunk(w, rb.possessions)
    rp.write_chunk(w, rb.possessions)  # idempotent: no duplicate keys
    n = w.execute("SELECT count(*) FROM possessions").fetchone()
    assert n is not None and n[0] == old.height
    got = rp.verify_written(w, rb.possessions)
    stints = w.execute("SELECT count(*) FROM stints").fetchone()
    w.close()
    assert got == {"rows_expected": 6, "rows_stored": 6, "equal": 1}
    assert stints is not None and stints[0] > 0  # stints are never touched


def test_write_chunked_in_two_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, data = _seed(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    rb = rp.compute_all(con, data)
    con.close()
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    assert rp.write_chunked(db, rb, chunk_games=1, pause_s=0.0) == 2
    con = duckdb.connect(str(db), read_only=True)
    assert rp.verify_written(con, rb.possessions)["equal"] == 1
    con.close()


def _main_args(tmp_path: Path, db: Path, data: Path, *extra: str) -> list[str]:
    return [
        "--db",
        str(db),
        "--data-dir",
        str(data),
        "--report-dir",
        str(tmp_path / "rep"),
        "--backup-dir",
        str(tmp_path / "bk"),
        "--no-lock",
        "--pause-s",
        "0",
        *extra,
    ]


def test_main_compute_only_writes_report_not_db(tmp_path: Path) -> None:
    db, data = _seed(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    before = rp.load_stored(con)
    con.close()
    assert rp.main(_main_args(tmp_path, db, data)) == 0
    summary = json.loads((tmp_path / "rep" / "summary.json").read_text())
    assert summary["before"]["neg_duration"] == 1 and summary["after"]["neg_duration"] == 0
    assert summary["renumbering"]["games_renumbered"] == 1
    assert summary["backup"] is None and not (tmp_path / "bk").exists()
    con = duckdb.connect(str(db), read_only=True)
    assert rp.load_stored(con).equals(before)
    con.close()


def test_main_write_refused_when_a_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, data = _seed(tmp_path)
    monkeypatch.setattr(rp, "evaluate_gates", lambda b, a: [rp.Gate("balance_mean", False, "x")])
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    con = duckdb.connect(str(db), read_only=True)
    before = rp.load_stored(con)
    con.close()
    assert rp.main(_main_args(tmp_path, db, data, "--write")) == 2
    assert not (tmp_path / "bk").exists()  # no backup, no write
    con = duckdb.connect(str(db), read_only=True)
    assert rp.load_stored(con).equals(before)
    con.close()


def test_main_write_passes_gates_backs_up_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, data = _seed(tmp_path)
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    assert rp.main(_main_args(tmp_path, db, data, "--write")) == 0
    backups = list((tmp_path / "bk").glob("possessions_pre_order_fix_*_possessions.parquet"))
    assert len(backups) == 1
    con = duckdb.connect(str(db), read_only=True)
    stored = rp.load_stored(con)
    con.close()
    assert rp.order_defects(stored)["neg_duration"] == 0
    # a second run is a no-op that still passes (defects 0 -> 0)
    assert rp.main(_main_args(tmp_path, db, data, "--write")) == 0


def _metrics(**kw: float) -> dict[str, float]:
    base = {
        "n_poss": 1000,
        "neg_duration": 10,
        "start_after_prev_end": 5,
        "balance_mean": 1.3,
        "balance_median": 1.0,
        "recon_mae": 2.0,
        "recon_signed": -1.5,
        "points_exact_rate": 0.99,
        "lineup_null_rows": 2,
    }
    return {**base, **kw}


def _failed(before: dict[str, float], after: dict[str, float]) -> set[str]:
    return {g.name for g in rp.evaluate_gates(before, after) if not g.ok}


def test_gates_pass_on_an_improvement() -> None:
    after = _metrics(neg_duration=0, start_after_prev_end=0, balance_mean=1.1, n_poss=998)
    assert _failed(_metrics(), after) == set()


@pytest.mark.parametrize(
    ("change", "gate"),
    [
        ({"lineup_null_rows": 2 + 500}, "lineup_attach"),
        ({"balance_mean": 1.5}, "balance_mean"),
        ({"balance_median": 3.0}, "balance_median"),
        ({"recon_mae": 2.6}, "recon_mae_ceiling"),
        ({"points_exact_rate": 0.95}, "points_exact"),
        ({"n_poss": 900}, "row_count"),
        ({"neg_duration": 10, "start_after_prev_end": 5}, "defects_fall"),
    ],
)
def test_each_gate_refuses(change: dict[str, float], gate: str) -> None:
    after = _metrics(neg_duration=0, start_after_prev_end=0, n_poss=1000)
    after.update(change)
    assert gate in _failed(_metrics(), after)


def test_order_defects_counts_planted_defects() -> None:
    rows = [
        # (poss_idx, period, clock_start, clock_end)
        (0, 1, 700.0, 680.0),
        (
            1,
            1,
            690.0,
            660.0,
        ),  # starts (690) after the previous ended (680) and before it started? no
        (2, 1, 650.0, 700.0),  # negative duration
        (3, 2, 700.0, 690.0),  # new period: not compared with the previous period
    ]
    f = pl.DataFrame(
        {
            "game_id": ["g"] * 4,
            "poss_idx": [r[0] for r in rows],
            "period": [r[1] for r in rows],
            "clock_start": [r[2] for r in rows],
            "clock_end": [r[3] for r in rows],
        }
    )
    d = rp.order_defects(f)
    assert d["neg_duration"] == 1
    assert d["start_after_prev_end"] == 1  # poss 1: 690 > 680
    assert d["start_before_prev_start"] == 0
    assert d["games_with_defect"] == 1
