"""Sanity checks against real play-by-play pulls.

``data/pbp/*.parquet`` is gitignored (multi-GB, never committed -- see
CLAUDE.md "data/ never committed") so this module skips itself entirely
in CI / on any checkout without a local nba_api pull. Run locally after
`uv run python -m nba.ingest pbp ...` to exercise the parser against real
games. These are the invariants reported in this agent's final summary
for games 0022200001-0022200003.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nba.parse.possessions import parse_possessions

DATA_PBP_DIR = Path(__file__).resolve().parents[2] / "data" / "pbp"
_SAMPLE_GAMES = ["0022200001", "0022200002", "0022200003"]
_AVAILABLE = [g for g in _SAMPLE_GAMES if (DATA_PBP_DIR / f"{g}.parquet").exists()]

pytestmark = pytest.mark.skipif(
    not _AVAILABLE, reason="data/pbp/*.parquet not present locally (gitignored, no network in CI)"
)


@pytest.mark.parametrize("game_id", _AVAILABLE or _SAMPLE_GAMES)
def test_real_game_possession_counts_are_plausible(game_id: str) -> None:
    pbp = pl.read_parquet(DATA_PBP_DIR / f"{game_id}.parquet")
    poss = parse_possessions(pbp)

    assert poss["off_team"].is_null().sum() == 0
    assert poss["off_team"].eq(0).sum() == 0

    by_team = poss.group_by("off_team").agg(pl.len().alias("n"))
    for n in by_team["n"].to_list():
        # A full NBA game is ~95-110 possessions per team at a normal pace.
        assert 80 <= n <= 130, f"{game_id}: implausible possession count {n}"

    outcomes = set(poss["outcome"].unique().to_list())
    assert outcomes <= {"FGM2", "FGM3", "FGA_miss", "TOV", "FT_trip", "other"}
    assert outcomes & {"FGM2", "FGM3", "FGA_miss", "TOV"}, "expected the common outcomes to appear"

    # Every made-shot row must carry a shooter.
    fgm = poss.filter(pl.col("outcome").is_in(["FGM2", "FGM3"]))
    assert fgm["shooter_id"].is_null().sum() == 0
