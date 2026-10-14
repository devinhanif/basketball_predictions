from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


@pytest.fixture
def export_client(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API pointed at the small export through APP_EXPORT_DIR."""
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        yield c
    reset_store()
