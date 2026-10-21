"""Research loaders filter season-2025 (holdout) rows at the source (MEDIAN_BIAS pre-freeze item 8).

A research caller passing ``max_season=2024`` must never read a 2025 availability row or a 2025
schedule file; the default (``None``) keeps the live behaviour of reading every file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.eval.context_residual_eval import load_inputs
from nba.features import game_tipoff
from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import OFFICIAL_REPORT_SOURCE, ReportTriggerConfig
from nba.props.context_residual import flagged_from_availability


def _schedule(d: Path) -> None:
    for name, gid, y in (
        ("raw_2023-24", "g24", 2024),
        ("raw_2025-26", "g25", 2025),
        ("live_tips_2026-27", "g26", 2026),
    ):
        pl.DataFrame(
            {
                "gameId": [gid],
                "gameDateTimeUTC": [f"{y}-01-15T18:00:00Z"],
                "gameDateTimeEst": [f"{y}-01-15T13:00:00Z"],
            }
        ).write_parquet(d / f"{name}.parquet")


def _db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, "
        "away_team INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, "
        "minutes DOUBLE, pts INT, reb INT, ast INT, fg3m INT, starter BOOLEAN)"
    )
    con.execute("CREATE TABLE players_static (player_id INT, position VARCHAR)")
    con.execute(
        "CREATE TABLE player_availability (player_id INT, as_of TIMESTAMP, game_id VARCHAR, "
        "status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO games VALUES ('g24', '2024-01-15', 2023, 1, 2, 100, 90), "
        "('g25', '2025-01-15', 2025, 1, 2, 100, 90)"
    )
    for gid in ("g24", "g25", None):
        con.execute(
            "INSERT INTO player_availability VALUES (7, '2024-01-15 10:00:00', ?, 'out', NULL, "
            "'official_report', NULL)",
            [gid],
        )
    con.close()


def test_load_inputs_never_materialises_2025_availability(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    _db(db)
    games, _pgs, _static, avail = load_inputs(str(db), None)
    assert set(games["game_id"]) == {"g24"}
    assert avail["game_id"].to_list() == ["g24"]  # g25 and the NULL-game row are not read


def test_load_inputs_separate_injury_db(tmp_path: Path) -> None:
    db, inj = tmp_path / "x.duckdb", tmp_path / "inj.duckdb"
    _db(db)
    _db(inj)
    _g, _p, _s, avail = load_inputs(str(db), str(inj))
    assert set(avail["game_id"]) == {"g24"}


def test_build_game_tipoff_max_season_skips_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _schedule(tmp_path)
    read: list[str] = []
    real = pl.read_parquet

    def spy(f, *a, **k):  # type: ignore[no-untyped-def]
        read.append(Path(f).name)
        return real(f, *a, **k)

    monkeypatch.setattr(game_tipoff.pl, "read_parquet", spy)
    out = build_game_tipoff(tmp_path, max_season=2024)
    assert out["game_id"].to_list() == ["g24"]
    assert read == ["raw_2023-24.parquet"]  # no 2025-26 or live-season file was opened
    read.clear()
    assert set(build_game_tipoff(tmp_path)["game_id"]) == {"g24", "g25", "g26"}  # live default
    assert len(read) == 3


def test_flagged_from_availability_passes_max_season(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _schedule(tmp_path)
    monkeypatch.setattr(game_tipoff, "SCHEDULE_DIR", tmp_path)
    read: list[str] = []
    real = pl.read_parquet

    def spy(f, *a, **k):  # type: ignore[no-untyped-def]
        read.append(Path(f).name)
        return real(f, *a, **k)

    monkeypatch.setattr(game_tipoff.pl, "read_parquet", spy)
    games = pl.DataFrame(
        {
            "game_id": ["g24"],
            "game_date": [dt.date(2024, 1, 15)],
            "home_team": [1],
            "away_team": [2],
            "season": [2023],
        }
    )
    avail = pl.DataFrame(
        {
            "game_id": ["g24"],
            "player_id": [7],
            "status": ["out"],
            "as_of": [dt.datetime(2024, 1, 15, 10, 0)],
            "source": [OFFICIAL_REPORT_SOURCE],
        }
    )
    cfg = ReportTriggerConfig(tip_source="real")
    flagged, _ = flagged_from_availability(avail, games, cfg, max_season=2024)
    assert flagged == {"g24": {7}}
    assert read == ["raw_2023-24.parquet"]


#: Research harnesses that must pass ``max_season`` on every schedule / availability read (T501:
#: the GAME_MARKET_CONTEXT harness read the 2025-26 schedule files through the default). The other
#: research callers are swept in a later change; add them here as they are fixed.
HOLDOUT_FILTERED_HARNESSES = (
    "research/eval/game_market_context_eval.py",
    "research/eval/own_tag_eval.py",
)


@pytest.mark.parametrize("rel", HOLDOUT_FILTERED_HARNESSES)
def test_harness_passes_max_season_on_every_source_read(rel: str) -> None:
    import re

    root = Path(__file__).resolve().parents[2]
    text = (root / rel).read_text()
    for fn in ("build_game_tipoff", "flagged_from_availability"):
        calls = [m for m in re.finditer(rf"\b{fn}\(([^()]*(?:\([^()]*\)[^()]*)*)\)", text)]
        calls = [m for m in calls if not text[: m.start()].rstrip().endswith("import")]
        assert calls, f"{rel}: expected at least one {fn}( call"
        unfiltered = [m.group(0) for m in calls if "max_season=" not in m.group(1)]
        assert not unfiltered, f"{rel}: {fn} called without max_season: {unfiltered}"
