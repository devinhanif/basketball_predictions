"""History free-throw repair: only pts/ftm and the changed possessions move; pbp text is marked."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from nba.db.connect import connect
from nba.parse.history_ft_fix import apply_fix, diff_season, rewrite_pbp
from nba.parse.history_nbastats_load import _BOX_COLS

G1, G2 = "0021300001", "0021300002"


def _box(ftm_a: int) -> pl.DataFrame:
    zero = [0, 0, 0]
    return pl.DataFrame(
        {
            "game_id": [G1, G1, G2],
            "player_id": [1, 2, 3],
            "team_id": [10, 20, 10],
            "minutes": [30.0, 20.0, 10.0],
            "pts": [10 + ftm_a, 5, 8],
            "reb": zero,
            "ast": zero,
            "fg3m": zero,
            "stl": zero,
            "blk": zero,
            "tov": zero,
            "starter": [True, False, True],
            "fgm": zero,
            "fga": zero,
            "fg3a": zero,
            "ftm": [ftm_a, 1, 2],
            "fta": [3, 2, 2],
            "oreb": zero,
            "dreb": zero,
            "pf": zero,
        }
    )


def _poss(pts: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [G1, G1, G2],
            "poss_idx": [0, 1, 0],
            "off_players": [[1, 2, 3, 4, 5]] * 3,
            "def_players": [[6, 7, 8, 9, 10]] * 3,
            "pts": [0, pts, 2],
        }
    )


def _stage(root: Path, ftm_a: int, pts: int) -> Path:
    d = root / "2013"
    d.mkdir(parents=True)
    _box(ftm_a).write_parquet(d / "box.parquet")
    _poss(pts).write_parquet(d / "possessions.parquet")
    for name in ("games", "stints", "shots"):
        pl.DataFrame({"game_id": [G1, G2]}).write_parquet(d / f"{name}.parquet")
    return root


def test_fix_updates_only_changed_rows_and_marks_pbp(tmp_path: Path) -> None:
    old, new = _stage(tmp_path / "old", 3, 2), _stage(tmp_path / "new", 2, 1)
    d = diff_season(old / "2013", new / "2013")
    assert d["box_rows_changed"] == 1 and d["box_games_changed"] == [G1]
    assert d["possessions_games_changed"] == [G1]
    assert (
        d["box_changed_each_ftm_minus_1_and_pts_equal_delta"]
        and d["box_other_columns_changed"] == []
    )

    db = tmp_path / "h.duckdb"
    con = connect(db)
    con.execute("INSERT INTO games (game_id, season) VALUES (?, 2013), (?, 2013)", [G1, G2])
    cols = ", ".join(_BOX_COLS)
    con.register("df", _box(3).to_arrow())
    con.execute(f"INSERT INTO player_game_stats ({cols}) SELECT {cols} FROM df")
    con.unregister("df")
    con.register("df", _poss(2).to_arrow())
    con.execute(
        "INSERT INTO possessions (game_id, poss_idx, off_players, def_players, pts) "
        "SELECT game_id, poss_idx, CAST(off_players AS INTEGER[5]), "
        "CAST(def_players AS INTEGER[5]), pts FROM df"
    )
    con.unregister("df")
    con.close()

    res = apply_fix(db, old, new, [2013], backup_dir=tmp_path / "bk")
    assert res["ok"] and res["rows_updated"] == 1 and Path(res["backup"]).exists()
    assert "pre_ft_wording_fix" in Path(res["backup"]).name
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT player_id, pts, ftm FROM player_game_stats ORDER BY 1").fetchall()
    assert rows == [(1, 12, 2), (2, 5, 1), (3, 8, 2)]
    assert con.execute("SELECT sum(pts) FROM possessions").fetchone() == (3,)
    con.close()

    pbp = tmp_path / "pbp"
    pbp.mkdir()
    pl.DataFrame(
        {
            "action_number": [5, 6],
            "description": ["Free Throw 2 of 2", "X Free Throw 1 of 2 (3 PTS)"],
        }
    ).write_parquet(pbp / f"{G1}.parquet")
    ev = pl.DataFrame({"game_id": [G1], "action_number": [5]})
    out = rewrite_pbp(ev, pbp, tmp_path / "sup")
    assert out == {"pbp_files_rewritten": 1, "pbp_events_marked": 1}
    assert pl.read_parquet(pbp / f"{G1}.parquet")["description"].to_list() == [
        "MISS Free Throw 2 of 2",
        "X Free Throw 1 of 2 (3 PTS)",
    ]
    assert (tmp_path / "sup" / f"{G1}.parquet").exists()
