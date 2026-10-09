from __future__ import annotations

from datetime import date

import duckdb
import polars as pl

from nba.ingest.era_flags import TORONTO, compute_era_flags, write_era_flags


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d", "e", "f"],
            "game_date": [
                date(2019, 12, 1),
                date(2020, 7, 29),
                date(2020, 7, 30),
                date(2021, 1, 5),
                date(2021, 12, 1),
                date(2023, 1, 1),
            ],
            "season": [2019, 2019, 2019, 2020, 2021, 2022],
            "home_team": [1, 1, 1, TORONTO, 1, 1],
        }
    )


def test_rules() -> None:
    f = {r["game_id"]: r for r in compute_era_flags(_games()).iter_rows(named=True)}
    assert f["a"]["shortened_season"] and not f["a"]["covid_bubble"] and not f["a"]["no_fans"]
    assert not f["b"]["covid_bubble"]  # day before the restart
    assert f["c"]["covid_bubble"] and f["c"]["no_fans"] and not f["c"]["limited_fans"]
    assert f["d"]["limited_fans"] and not f["d"]["no_fans"] and f["d"]["shortened_season"]
    assert "Tampa" in f["d"]["notes"]
    for k in "ef":
        r = f[k]
        assert not any(
            r[c] for c in ("covid_bubble", "no_fans", "limited_fans", "shortened_season")
        )
        assert r["notes"] is None


def test_write_idempotent() -> None:
    con = duckdb.connect(":memory:")
    flags = compute_era_flags(_games())
    write_era_flags(con, flags)
    write_era_flags(con, flags)
    assert con.execute("SELECT count(*) FROM game_era_flags").fetchone() == (6,)
    assert con.execute("SELECT count(*) FROM game_era_flags WHERE covid_bubble").fetchone() == (1,)
