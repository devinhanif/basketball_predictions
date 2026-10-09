"""Offline tests for nba/ingest/postgame.py (recorded fixtures, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.ingest import postgame as pg
from nba.ingest.cache import cache_path_for
from tests.fixtures.loader import build_fixture_db

RAW = json.loads((Path(__file__).parents[1] / "fixtures/postgame/raw_samples.json").read_text())
GID = "0022500500"


def _df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_snake() -> None:
    assert pg.snake("reboundChancesOffensive") == "rebound_chances_offensive"
    assert pg.snake("GAME_EVENT_ID") == "game_event_id"
    assert pg.snake("personIdOff") == "person_id_off"
    assert pg.snake("contestedShots2pt") == "contested_shots2pt"


@pytest.mark.parametrize(
    ("source", "norm", "key"),
    [("tracking", pg.normalize_tracking, "tracking"), ("hustle", pg.normalize_hustle, "hustle")],
)
def test_player_frames(source: str, norm, key: str) -> None:  # type: ignore[no-untyped-def]
    out = norm(_df(RAW[key]), GID)
    _, cols, _ = pg.TABLES[source]
    assert out.columns == cols
    assert out.height == 4
    assert out["game_id"].unique().to_list() == [GID]
    assert out["minutes"].dtype == pl.Float64
    assert out["minutes"][0] == pytest.approx(31 + 33 / 60)


def test_matchups() -> None:
    out = pg.normalize_matchups(_df(RAW["matchups"]), GID)
    assert out.columns == pg.TABLES["matchups"][1]
    assert out["off_player_id"][0] == 1631099 and out["def_player_id"][0] == 203507
    assert out["matchup_minutes"][0] == pytest.approx(18 / 60)
    assert out["def_team_id"].null_count() == out.height  # filled at load time


def test_officials_picks_by_columns() -> None:
    frames = [_df(f) for f in RAW["summary_frames"] if f]
    out = pg.normalize_officials(frames, GID)
    assert out.columns == pg.TABLES["officials"][1]
    assert out.height == 3 and out["official_id"].n_unique() == 3
    with pytest.raises(pg.MalformedResponseError):
        pg.normalize_officials([_df(RAW["tracking"])], GID)


def test_shots() -> None:
    out = pg.normalize_shots(_df(RAW["shots"]))
    assert out.columns == pg.TABLES["shots"][1]
    assert out["made"].dtype == pl.Boolean
    assert out["clock_seconds"].max() <= 720  # type: ignore[operator]


def test_coaches() -> None:
    out = pg.normalize_coaches(_df(RAW["roster_coaches"]))
    assert out.columns == pg.TABLES["coaches"][1]
    assert out["season"].dtype == pl.Int64


def test_malformed_raises() -> None:
    with pytest.raises(pg.MalformedResponseError):
        pg.normalize_tracking(_df(RAW["tracking"]).drop("touches"), GID)


def test_with_retry_backoff_and_empty() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def flaky() -> pl.DataFrame:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("boom")
        return pl.DataFrame({"a": [1]})

    assert pg._with_retry(flaky, sleep=sleeps.append).height == 1
    assert sleeps == [2.0, 4.0]
    assert pg._with_retry(lambda: pl.DataFrame(), sleep=lambda s: None).is_empty()
    with pytest.raises(TimeoutError):
        pg._with_retry(lambda: (_ for _ in ()).throw(TimeoutError("x")), sleep=lambda s: None)


def _scoped_db(tmp_path: Path) -> Path:
    path = tmp_path / "nba.duckdb"
    con = build_fixture_db(path)
    con.close()
    return path


def test_fetch_never_caches_empty_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _scoped_db(tmp_path)
    ids = pg.scope_game_ids(db)
    assert ids
    calls: list[str] = []

    def fake(gid: str) -> pl.DataFrame:
        calls.append(gid)
        if gid == ids[0]:
            return pl.DataFrame()  # permanently empty -> must fail, not cache
        return pg.normalize_tracking(_df(RAW["tracking"]), gid)

    monkeypatch.setattr(pg, "_fetch_tracking", fake)
    s1 = pg.fetch_source("tracking", db, data_dir=tmp_path / "data", sleep=lambda s: None)
    assert s1.failed == [ids[0]] and s1.fetched == len(ids) - 1
    assert not cache_path_for("tracking", ids[0], tmp_path / "data").exists()
    calls.clear()
    s2 = pg.fetch_source("tracking", db, data_dir=tmp_path / "data", sleep=lambda s: None)
    assert s2.cached == len(ids) - 1  # never refetch cached
    assert set(calls) == {ids[0]}  # only the failed key is retried


def test_load_idempotent_no_duplicate_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _scoped_db(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(
        pg, "_fetch_matchups", lambda gid: pg.normalize_matchups(_df(RAW["matchups"]), gid)
    )
    monkeypatch.setattr(
        pg, "_fetch_tracking", lambda gid: pg.normalize_tracking(_df(RAW["tracking"]), gid)
    )
    for src in ("tracking", "matchups"):
        pg.fetch_source(src, db, data_dir=data, sleep=lambda s: None)
    con = duckdb.connect(str(db))
    for _ in range(2):  # idempotent
        for src in ("tracking", "matchups"):
            pg.load_source(con, src, data_dir=data)
    n_games = con.execute("SELECT count(*) FROM games").fetchone()[0]  # type: ignore[index]
    assert con.execute("SELECT count(*) FROM player_game_tracking").fetchone() == (4 * n_games,)
    for table, key in [
        ("player_game_tracking", "game_id, player_id"),
        ("player_game_matchups", "game_id, off_player_id, def_player_id"),
    ]:
        dup = con.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL HAVING count(*) > 1)"
        ).fetchone()
        assert dup == (0,)
    # def_team_id resolved from games: never equal to the offensive team
    bad = con.execute(
        "SELECT count(*) FROM player_game_matchups "
        "WHERE def_team_id IS NULL OR def_team_id = team_id"
    ).fetchone()
    assert bad == (0,)
    con.close()


def test_schema_columns_match_tables() -> None:
    con = build_fixture_db()
    for source, (table, cols, _) in pg.TABLES.items():
        got = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
        assert got == cols, source


def test_reconciliation_runs_on_fixture() -> None:
    con = build_fixture_db()
    out = pg.shots_fga_reconciliation(con)
    assert out["team_games_compared"] == 0.0  # no shots in fixture
    assert pg.coverage(con) == []
