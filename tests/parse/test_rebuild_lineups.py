"""Bulk lineup rebuild (nba/parse/rebuild_lineups.py) on the committed fixture."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from nba.db.connect import connect
from nba.ingest.cache import cache_path_for
from nba.parse.history import parse_games
from nba.parse.rebuild_lineups import (
    compute_all,
    possession_agreement,
    snapshot_pre_fix,
    write_chunk,
    write_unresolved_csv,
)
from tests.parse.helpers import AWAY, HOME, PbpBuilder


def _synthetic_pbp() -> pl.DataFrame:
    b = PbpBuilder()
    for period in (1, 2):
        for i, (team, pid) in enumerate([(HOME, 1), (AWAY, 11), (HOME, 2), (AWAY, 12), (HOME, 3)]):
            b.add(
                period,
                f"PT11M{50 - 5 * i}.00S",
                team,
                pid,
                "Made Shot",
                sub_type="Jump Shot",
                description=f"P{pid} Jump Shot (2 PTS)",
                player_name=f"P{pid}",
                shot_value=2,
                shot_distance=15,
                is_field_goal=1,
                location="h" if team == HOME else "v",
            )
    b.add(
        1,
        "PT06M00.00S",
        HOME,
        4,
        "Substitution",
        description="SUB: Bench6 FOR P4",
        player_name="P4",
    )
    return b.df()


def _seed(tmp_path: Path):  # type: ignore[no-untyped-def]
    con = connect(":memory:")
    pbp = _synthetic_pbp()
    gid = PbpBuilder().game_id
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES (?, DATE '2023-10-25', 2023, ?, ?, 10, 6)",
        [gid, HOME, AWAY],
    )
    box = [(HOME, [1, 2, 3, 4, 5], 6), (AWAY, [11, 12, 13, 14, 15], None)]
    for team, starters, bench in box:
        for pid in starters + ([bench] if bench else []):
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, starter, "
                "fga, fta, oreb, tov) VALUES (?, ?, ?, 20.0, ?, 1, 0, 0, 0)",
                [gid, pid, team, pid in starters],
            )
    p = cache_path_for("pbp", gid, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    pbp.write_parquet(p)
    s = parse_games(con, data_dir=tmp_path)
    assert s.parsed == 1 and not s.errors, s.errors
    return con, s


def test_parse_summary_carries_minutes_reconciliation(tmp_path: Path) -> None:
    _, s = _seed(tmp_path)
    assert s.minutes_n > 0
    assert 0 <= s.minutes_within <= s.minutes_n


def test_rebuild_reproduces_and_writes_in_place(tmp_path: Path) -> None:
    con, _ = _seed(tmp_path)
    expected = con.execute(
        "SELECT game_id, poss_idx, off_players::BIGINT[] AS off_players, "
        "def_players::BIGINT[] AS def_players FROM possessions ORDER BY 1, 2"
    ).pl()
    n_stints = con.execute("SELECT count(*) FROM stints").fetchone()[0]  # type: ignore[index]

    snaps = snapshot_pre_fix(con, tmp_path / "bk", ts="t")
    assert all(p.exists() for p in snaps)

    rb = compute_all(con, tmp_path)
    assert rb.lineups.height == expected.height and not any(rb.skipped.values())

    # Scramble, then repair with the chunked writer: must restore exactly.
    con.execute("UPDATE possessions SET off_players = NULL, def_players = NULL")
    write_chunk(con, rb.stints, rb.lineups)
    write_chunk(con, rb.stints, rb.lineups)  # idempotent: no duplicate stints
    got = con.execute(
        "SELECT game_id, poss_idx, off_players::BIGINT[] AS off_players, "
        "def_players::BIGINT[] AS def_players FROM possessions ORDER BY 1, 2"
    ).pl()
    assert got.equals(expected)
    assert con.execute("SELECT count(*) FROM stints").fetchone()[0] == n_stints  # type: ignore[index]

    agree = possession_agreement(
        expected, rb.lineups, con.execute("SELECT game_id, poss_idx, period FROM possessions").pl()
    )
    assert agree["all_off"] == 1.0

    u, d = write_unresolved_csv(rb.reports, tmp_path / "rep")
    assert u.exists() and d.exists()
    assert pl.read_csv(u).columns[:2] == ["game_id", "team_id"]
