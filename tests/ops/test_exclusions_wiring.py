"""Exclusion readers: the parlay shadow track record, the AVG split, the key-set helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from nba.ops import exclusions as ex
from nba.parlay.shadow import SCHEMA, TrackRecord, track_record

ENTRY = ex.Exclusion("e1", "stale_price", date(2026, 10, 23), 1800.0, ("shadow_predictions",))


def _kalshi(path: Path, snaps: list[tuple[datetime, int]]) -> Path:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT, "
        '"last" FLOAT, volume INT, open_interest INT, source VARCHAR)'
    )
    for ts, n in snaps:
        con.executemany(
            "INSERT INTO kalshi_prices VALUES (?, ?, 0.4, 0.5, 0.45, 1, 1, 's')",
            [(f"T{i}", ts) for i in range(n)],
        )
    con.close()
    return path


def _shadow(rows: list[tuple[str, datetime, float, float, bool]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    for sid, at, raw, mid, outcome in rows:
        con.execute(
            "INSERT INTO shadow_predictions VALUES (?,?,?,?,?,?,?,?,?,TRUE,?)",
            [sid, at, "T", "yes", '[{"game_id": "g1"}]', raw, mid, mid, "marginal", outcome],
        )
    return con


T0 = datetime(2026, 10, 23, 18, 0)


def test_stale_shadow_ids_by_age_since_full_snapshot(tmp_path: Path) -> None:
    kal = _kalshi(
        tmp_path / "k.duckdb",
        [(T0, 1500), (T0 + timedelta(minutes=15), 1500), (T0 + timedelta(hours=3), 200)],
    )
    con = _shadow(
        [
            ("2026-10-23|A|yes", T0 + timedelta(minutes=20), 0.6, 0.5, True),  # 5 min: fresh
            ("2026-10-23|B|yes", T0 + timedelta(hours=3, minutes=1), 0.6, 0.5, True),  # 165 min
            ("2026-10-22|C|yes", T0 + timedelta(hours=3), 0.6, 0.5, True),  # other slate
        ]
    )
    # the partial (200-ticker) snapshot does not refresh the clock
    assert ex.stale_shadow_ids(ENTRY, con, kal) == ["2026-10-23|B|yes"]


def test_stale_shadow_ids_absent_inputs(tmp_path: Path) -> None:
    con = _shadow([("2026-10-23|B|yes", T0, 0.6, 0.5, True)])
    assert ex.stale_shadow_ids(ENTRY, con, tmp_path / "missing.duckdb") == []
    assert ex.stale_shadow_ids(ENTRY, duckdb.connect(":memory:"), tmp_path / "x.duckdb") == []


def test_track_record_drops_excluded_and_counts() -> None:
    con = _shadow(
        [
            ("2026-10-22|A|yes", T0, 0.9, 0.5, True),
            ("2026-10-22|B|yes", T0, 0.9, 0.5, False),
            ("2026-10-23|C|yes", T0, 0.1, 0.5, True),  # stale: dropped
        ]
    )
    full = track_record(con, exclude={})
    cut = track_record(con, exclude={"r": {"2026-10-23|C|yes"}})
    assert full.n_settled == 3 and full.excluded_by == ()
    assert cut.n_settled == 2 and cut.n_excluded == 1 and cut.excluded_by == (("r", 1),)
    assert cut.n_dates == 1
    assert "1 settled rows dropped (1 by r)" in cut.describe()
    assert "0 settled rows" not in full.describe()


def test_track_record_empty_after_drop() -> None:
    con = _shadow([("2026-10-23|C|yes", T0, 0.1, 0.5, True)])
    tr = track_record(con, exclude={"r": {"2026-10-23|C|yes"}})
    assert tr.n_settled == 0 and tr.n_excluded == 1
    assert isinstance(tr, TrackRecord) and tr.gated_skill(1) == 0.0


def test_describe_dropped() -> None:
    assert ex.describe_dropped({}, "rows") == "data-quality exclusions: 0 rows dropped"
    assert "3 rows dropped (2 by a, 1 by b)" in ex.describe_dropped({"a": 2, "b": 1}, "rows")


def test_drop_excluded_by_market_key() -> None:
    k = ("g", "m", "v", "pts", 7, "2026-10-23T20:00:00")
    rows = [k, ("g2", "m", "v", "pts", 7, "2026-10-23T20:00:00")]
    kept, dropped = ex.drop_excluded(rows, lambda r: r, {"r": {k}})
    assert kept == [rows[1]] and dropped == {"r": 1}


def test_avg_split_counts_at_the_merge(tmp_path: Path) -> None:
    y = tmp_path / "e.yaml"
    y.write_text(
        "splits:\n  - id: s1\n    applies_to: [AVG_SHADOW_LIVE_2 scored-row summaries]\n"
        "    split_at_utc: 2026-10-25T04:00:00\n"
    )
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE forward_avg_scores (made_at TIMESTAMP, status VARCHAR)")
    con.execute(
        "INSERT INTO forward_avg_scores VALUES ('2026-10-24 20:00','scored'),"
        "('2026-10-25 03:59:59','scored'),('2026-10-25 04:00','scored'),"
        "('2026-10-25 05:00','not_in_box')"
    )
    (s,) = ex.avg_split(con, y)
    assert (s["id"], s["before"], s["after"]) == ("s1", 2, 1)
    assert ex.avg_split(duckdb.connect(":memory:"), y) == []


def test_real_yaml_loads_split() -> None:
    (sp,) = ex.load_splits()
    assert sp.split_at_utc == datetime(2026, 10, 25, 4, 0)
