from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, reset_store

EXPORT_SMALL = Path(__file__).resolve().parents[2] / "fixtures" / "export_small"
EXPORT_DAY = "2024-11-12"
os.environ.pop("APP_EXPORT_DIR", None)  # tests choose their data source explicitly


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    reset_store()
    with TestClient(create_app()) as c:
        yield c


def seal(directory: Path, **manifest_changes: Any) -> None:
    """Recompute the sha256 of the two data files into manifest.json (as a build would), then
    apply `manifest_changes` (a value of None removes the key)."""
    mp = directory / "manifest.json"
    m = json.loads(mp.read_text())
    for name in ("export.duckdb", "parlay_of_day.json"):
        data = (directory / name).read_bytes()
        m["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    for k, v in manifest_changes.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    mp.write_text(json.dumps(m, indent=1, sort_keys=True))


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    """A private copy of the small committed export that a test may damage."""
    d = tmp_path / "export"
    shutil.copytree(EXPORT_SMALL, d)
    return d


def downgrade_to_v1(d: Path) -> None:
    """Make a private copy look like schema 1: no prop_lines, team_id, or stl/blk/tov."""
    con = duckdb.connect(str(d / "export.duckdb"))
    con.execute("DROP TABLE prop_lines")
    for col in ("team_id", "team_source"):
        con.execute(f"ALTER TABLE prop_grids DROP COLUMN {col}")
    for col in ("stl", "blk", "tov"):
        con.execute(f"ALTER TABLE box_scores DROP COLUMN {col}")
    con.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    con.execute("DELETE FROM meta WHERE key = 'lines_status'")
    con.execute("CHECKPOINT")
    con.close()
    seal(d, schema_version=1, lines=None)


def edit_db(d: Path, *statements: str) -> None:
    """Run SQL against a private copy of the export, then reseal the manifest."""
    con = duckdb.connect(str(d / "export.duckdb"))
    for sql in statements:
        con.execute(sql)
    con.execute("CHECKPOINT")
    con.close()
    seal(d)


@pytest.fixture
def v1_dir(export_dir: Path) -> Path:
    downgrade_to_v1(export_dir)
    return export_dir


@pytest.fixture
def v1_client(v1_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API on a schema 1 export (no lines, no stl/blk, no team ids)."""
    monkeypatch.setenv("APP_EXPORT_DIR", str(v1_dir))
    reset_store()
    with TestClient(create_app()) as c:
        yield c
    reset_store()


@pytest.fixture
def export_client(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API pointed at the small export through APP_EXPORT_DIR."""
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        yield c
    reset_store()
