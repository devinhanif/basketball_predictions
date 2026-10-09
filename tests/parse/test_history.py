"""History-mode parse orchestration, on the committed fixture (no network)."""

from __future__ import annotations

from pathlib import Path

from nba.ingest.cache import cache_path_for
from nba.parse.history import parse_games
from tests.fixtures.loader import build_fixture_db, load_pbp


def test_parse_games_on_fixture_resumes_and_reconciles(tmp_path: Path) -> None:
    con = build_fixture_db()
    con.execute("DELETE FROM possessions")
    con.execute("DELETE FROM stints")
    pbp = load_pbp()
    for gid, df in pbp.items():
        p = cache_path_for("pbp", gid, tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(p)
    s = parse_games(con, data_dir=tmp_path)
    assert s.parsed == len(pbp) and not s.errors
    assert s.gate_pass and s.mean_abs_poss_error <= 1.0
    n = con.execute("SELECT count(*) FROM possessions").fetchone()
    assert n is not None and n[0] > 0
    s2 = parse_games(con, data_dir=tmp_path)  # resumable: nothing reparsed
    assert s2.parsed == 0 and s2.skipped_existing == len(pbp)
    assert parse_games(con, data_dir=tmp_path / "nowhere").no_pbp == len(
        con.execute("SELECT game_id FROM games").fetchall()
    )
