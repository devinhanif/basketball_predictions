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


# ---------------------------------------------------------------------------
# --flags-only (oreb rule change, F8 gate G3)
# ---------------------------------------------------------------------------
def _poss_frame(**cols: list[object]) -> pl.DataFrame:
    n = len(next(iter(cols.values())))
    base: dict[str, list[object]] = {
        "game_id": ["g"] * n,
        "poss_idx": list(range(n)),
        "period": [1] * n,
        "clock_start": [700.0] * n,
        "clock_end": [690.0] * n,
        "off_team": [1] * n,
        "def_team": [2] * n,
        "off_players": [[1, 2, 3, 4, 5]] * n,
        "def_players": [[6, 7, 8, 9, 10]] * n,
        "score_diff": [0] * n,
        "outcome": ["FGM2"] * n,
        "shooter_id": [1] * n,
        "shot_zone": ["mid"] * n,
        "assister_id": [None] * n,
        "oreb": [False] * n,
        "fta": [0] * n,
        "pts": [2] * n,
    }
    base.update(cols)
    return pl.DataFrame(base, schema=rp._SCHEMA)


def test_structure_diff_counts_only_oreb_when_only_oreb_changes() -> None:
    old = _poss_frame(oreb=[True, True, False])
    new = _poss_frame(oreb=[True, False, False])
    d = rp.structure_diff(old, new)
    assert d["key_mismatch"] == 0 and sum(d["changed"].values()) == 0
    assert (d["oreb_added"], d["oreb_removed"]) == (0, 1)


def test_structure_diff_catches_a_changed_count_or_key() -> None:
    old = _poss_frame(oreb=[True, False, False])
    moved = _poss_frame(oreb=[True, False, False], pts=[2, 3, 2], fta=[0, 0, 1])
    d = rp.structure_diff(old, moved)
    assert d["changed"]["pts"] == 1 and d["changed"]["fta"] == 1
    fewer = _poss_frame(oreb=[True, False])
    assert rp.structure_diff(old, fewer)["key_mismatch"] == 1
    gates = {g.name: g for g in rp.evaluate_oreb_gates(_ok_stats(), d)}
    assert not gates["structure_unchanged"].ok


def _ok_stats(**kw: float) -> dict[str, dict[str, dict[str, float]]]:
    row = {"team_games": 100, "flagged": 98, "box": 100, "ratio": 0.98, "corr": 0.97}
    row.update(kw)
    return {"flag": {"2023": dict(row), "2024": dict(row), "all": dict(row)}}


def _clean_diff() -> dict[str, object]:
    return {
        "rows_old": 3,
        "rows_new": 3,
        "key_mismatch": 0,
        "changed": {"pts": 0},
        "oreb_added": 0,
        "oreb_removed": 1,
    }


def test_oreb_gates_pass_on_a_reconciling_flag() -> None:
    assert all(g.ok for g in rp.evaluate_oreb_gates(_ok_stats(), _clean_diff()))


@pytest.mark.parametrize(
    ("change", "gate"),
    [
        ({"ratio": 0.878}, "oreb_ratio_2023"),
        ({"ratio": 1.35}, "oreb_ratio_2024"),
        ({"corr": 0.80}, "oreb_corr_all"),
        ({"ratio": float("nan")}, "oreb_ratio_2023"),
    ],
)
def test_each_oreb_gate_refuses(change: dict[str, float], gate: str) -> None:
    failed = {
        g.name for g in rp.evaluate_oreb_gates(_ok_stats(**change), _clean_diff()) if not g.ok
    }
    assert gate in failed


def test_oreb_gate_refuses_when_a_flag_would_be_added() -> None:
    diff = {**_clean_diff(), "oreb_added": 1}
    failed = {g.name for g in rp.evaluate_oreb_gates(_ok_stats(), diff) if not g.ok}
    assert failed == {"flags_only_removed"}


def test_oreb_stats_sums_flags_and_counts_per_season() -> None:
    poss = _poss_frame(
        game_id=["g1", "g1", "g1", "g2", "g2"],
        poss_idx=[0, 1, 2, 0, 1],
        off_team=[1, 1, 2, 1, 2],
        oreb=[True, True, False, False, True],
        outcome=["FGM2", "TOV", "FGM2", "FGM2", "FGA_miss"],
    )
    counts = pl.DataFrame(
        {"game_id": poss["game_id"], "poss_idx": poss["poss_idx"], "n_oreb": [2, 1, 0, 0, 1]}
    )
    box = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g2"],
            "team_id": [1, 2, 1, 2],
            "season": [2023, 2023, 2024, 2024],
            "box_oreb": [3, 0, 0, 1],
        }
    )
    st = rp.oreb_stats(poss, box, counts)
    assert st["flag"]["2023"]["flagged"] == 2 and st["flag"]["2023"]["box"] == 3
    assert st["flag"]["2024"]["flagged"] == 1 and st["flag"]["all"]["flagged"] == 3
    assert st["count"]["all"]["flagged"] == 4 and st["count"]["all"]["box"] == 4
    assert st["mix"]["TOV"] == {"trips": 1, "oreb_true": 1, "share": 1.0}
    assert st["oreb_true_total"] == 3
    assert st["share_oreb_on_fgm_tov"] == pytest.approx(2 / 4)


def _plant_stale_flag(db: Path) -> None:
    w = duckdb.connect(str(db))
    w.execute("UPDATE possessions SET oreb = TRUE WHERE game_id = ? AND poss_idx = 0", [GAME_B])
    w.close()


def test_flags_only_writes_only_oreb_and_backs_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, data = _seed(tmp_path)
    _plant_stale_flag(db)
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    monkeypatch.setattr(rp, "evaluate_oreb_gates", lambda s, d: [rp.Gate("ok", True, "x")])
    con = duckdb.connect(str(db), read_only=True)
    before = rp.load_stored(con)
    con.close()
    assert int(before["oreb"].sum()) == 1

    assert rp.main(_main_args(tmp_path, db, data, "--flags-only", "--write")) == 0

    con = duckdb.connect(str(db), read_only=True)
    after = rp.load_stored(con)
    con.close()
    assert int(after["oreb"].sum()) == 0  # the stale flag is gone
    others = [c for c in rp._SCHEMA if c != "oreb"]
    assert after.sort(rp._KEY).select(others).equals(before.sort(rp._KEY).select(others))
    backups = list((tmp_path / "bk").glob("possessions_pre_oreb_fix_*_possessions.parquet"))
    assert len(backups) == 1 and pl.read_parquet(backups[0]).height == before.height
    summary = json.loads((tmp_path / "rep" / "summary.json").read_text())
    assert summary["structure"]["oreb_removed"] == 1 and summary["structure"]["oreb_added"] == 0
    # idempotent: nothing left to change
    assert rp.main(_main_args(tmp_path, db, data, "--flags-only", "--write")) == 0
    assert (
        json.loads((tmp_path / "rep" / "summary.json").read_text())["structure"]["oreb_removed"]
        == 0
    )


def test_flags_only_refuses_when_the_reconciliation_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeded box has OREB 0 everywhere, so the ratio is undefined and the gate refuses."""
    db, data = _seed(tmp_path)
    _plant_stale_flag(db)
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    assert rp.main(_main_args(tmp_path, db, data, "--flags-only", "--write")) == 2
    assert not (tmp_path / "bk").exists()
    con = duckdb.connect(str(db), read_only=True)
    assert int(rp.load_stored(con)["oreb"].sum()) == 1  # untouched
    con.close()


def test_flags_only_refuses_when_something_else_would_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The _seed table holds the OLD ordering for game A: a re-segmentation, not a flag fix."""
    db, data = _seed(tmp_path)
    monkeypatch.setattr(rp, "db_holders", lambda _p: [])
    rc = rp.main(_main_args(tmp_path, db, data, "--flags-only", "--write"))
    assert rc == 2 and not (tmp_path / "bk").exists()
    gates = {
        g["name"]: g["ok"]
        for g in json.loads((tmp_path / "rep" / "summary.json").read_text())["gates"]
    }
    assert gates["structure_unchanged"] is False
