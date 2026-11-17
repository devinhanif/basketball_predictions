"""The gate in front of the nightly export: the manifest, the checksums and the holdout check.

Nothing here opens the database; ``verify`` runs before a byte of it is read (see
export_store.py for the full list of checks)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

Json = dict[str, Any]


SCHEMA_VERSIONS = (1, 2)


DB_NAME = "export.duckdb"


PARLAY_NAME = "parlay_of_day.json"


MANIFEST_NAME = "manifest.json"


HOLDOUT_SEASON = 2025


class ExportRefused(ValueError):
    """The export on disk cannot be trusted or is not one this app may show."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_NAME


def has_export(directory: Path | None) -> bool:
    return directory is not None and manifest_path(directory).is_file()


def verify(directory: Path) -> Json:
    """Check the manifest and the two files it names; return the manifest or raise."""
    try:
        manifest = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportRefused(f"manifest.json is not readable JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ExportRefused("manifest.json is not a JSON object")
    if manifest.get("schema_version") not in SCHEMA_VERSIONS:
        raise ExportRefused(f"unsupported schema_version {manifest.get('schema_version')!r}")
    dv = manifest.get("data_version")
    if not isinstance(dv, str) or not dv.strip():
        raise ExportRefused("manifest has no data_version: cannot say what data this came from")
    made = manifest.get("made_at")
    if not isinstance(made, str):
        raise ExportRefused("manifest has no made_at: cannot say when this was built")
    try:
        datetime.fromisoformat(made.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportRefused(f"manifest made_at is not a timestamp: {made!r}") from exc
    seasons = manifest.get("seasons")
    if not isinstance(seasons, list) or not seasons or not all(isinstance(s, int) for s in seasons):
        raise ExportRefused("manifest has no list of seasons")
    if HOLDOUT_SEASON in seasons:
        raise ExportRefused(f"manifest lists season {HOLDOUT_SEASON}, the frozen holdout")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ExportRefused("manifest has no file checksums")
    for name in (DB_NAME, PARLAY_NAME):
        want = (files.get(name) or {}).get("sha256")
        if not isinstance(want, str) or len(want) != 64:
            raise ExportRefused(f"manifest has no sha256 for {name}")
        path = directory / name
        if not path.is_file():
            raise ExportRefused(f"{name} is missing")
        if sha256_file(path) != want:
            raise ExportRefused(f"{name} does not match the manifest sha256")
    return manifest
