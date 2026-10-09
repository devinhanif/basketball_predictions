"""Only the snapshot job may write the live Kalshi DB; every other consumer is read-only."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import duckdb
import pytest

from nba.daily.replay_season import make_hidden_kalshi
from nba.db.connect import attach_read_only_with_retry
from nba.kalshi import __main__ as kmain
from nba.kalshi.client import DEFAULT_KALSHI_DB
from nba.markets.asof import open_read_only

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("cmd", ["backfill-historical", "cutoff", "markets", "candles"])
def test_non_snapshot_commands_refuse_the_live_db(cmd: str) -> None:
    extra = {
        "markets": ["--series", "X"],
        "candles": ["--ticker", "T", "--series", "X", "--start-ts", "1", "--end-ts", "2"],
    }.get(cmd, [])
    for db in ([], ["--db", str(DEFAULT_KALSHI_DB)]):  # default path and explicit path
        with pytest.raises(SystemExit, match="refusing"):
            kmain.main([*db, cmd, *extra])


def test_guard_allows_snapshot_and_separate_files(tmp_path: Path) -> None:
    kmain.guard_real_db("snapshot", DEFAULT_KALSHI_DB)
    kmain.guard_real_db("backfill-historical", tmp_path / "backfill.duckdb")
    kmain.guard_real_db("backfill-historical", ":memory:")
    # a relative spelling of the live path is still caught
    rel = os.path.relpath(DEFAULT_KALSHI_DB)
    with pytest.raises(SystemExit):
        kmain.guard_real_db("backfill-historical", rel)


def test_alias_candidates_never_opens_a_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("alias-candidates must not open DuckDB")

    monkeypatch.setattr(kmain, "connect", boom)
    assert kmain.main(["--db", str(tmp_path / "k.duckdb"), "alias-candidates"]) == 0


def test_replay_never_uses_live_db_as_working_copy(tmp_path: Path) -> None:
    master = tmp_path / "m.duckdb"
    master.write_bytes(b"x")
    with pytest.raises(SystemExit, match="live Kalshi DB"):
        make_hidden_kalshi(master, DEFAULT_KALSHI_DB)


def _readonly_db(tmp_path: Path) -> Path:
    p = tmp_path / "kalshi.duckdb"
    c = duckdb.connect(str(p))
    c.execute("CREATE TABLE kalshi_markets (ticker VARCHAR)")
    c.execute("INSERT INTO kalshi_markets VALUES ('A')")
    c.close()
    p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return p


def test_consumer_openers_are_read_only(tmp_path: Path) -> None:
    """A writable open of a 0444 file fails, so success proves the opener is read-only."""
    p = _readonly_db(tmp_path)
    if os.access(p, os.W_OK):
        pytest.skip("running with privileges that ignore file modes")
    with pytest.raises(duckdb.Error):
        duckdb.connect(str(p)).close()
    c = open_read_only(p, retries=1, wait_s=0.0)
    assert c.execute("SELECT count(*) FROM kalshi_markets").fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        c.execute("INSERT INTO kalshi_markets VALUES ('B')")
    c.close()
    m = duckdb.connect(":memory:")
    attach_read_only_with_retry(m, p, "kal", max_wait_s=0.0)
    assert m.execute("SELECT count(*) FROM kal.kalshi_markets").fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        m.execute("INSERT INTO kal.kalshi_markets VALUES ('B')")


def test_every_attach_in_the_package_is_read_only() -> None:
    """Static scan: no ATTACH of any file without READ_ONLY, and no direct connect() on a
    kalshi path outside the snapshot CLI."""
    bad: list[str] = []
    for f in (ROOT / "nba").rglob("*.py"):
        text = f.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"ATTACH\s+'", line) and "READ_ONLY" not in line.upper():
                bad.append(f"{f.relative_to(ROOT)}:{n}")
            if re.search(r"duckdb\.connect\([^)]*kalshi", line) and "read_only" not in line:
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert not bad, bad
