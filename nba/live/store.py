"""Write-once gzip JSON store with a manifest (sha256, bytes, received_at, http_status).

One file per (source, key, UTC second); an existing path is never overwritten (a numeric suffix is
added instead). The manifest line is appended after the file is renamed into place, so a manifest
row always points at a complete file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.files = 0
        self.bytes_gz = 0

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.jsonl"

    def save(
        self,
        source: str,
        key: str,
        body: bytes,
        *,
        http_status: int,
        received_at: datetime | None = None,
        note: dict[str, Any] | None = None,
    ) -> Path:
        now = received_at or datetime.now(UTC)
        directory = self.root / source / key
        directory.mkdir(parents=True, exist_ok=True)
        stamp = f"{now:%Y%m%dT%H%M%S}Z"
        path = directory / f"{stamp}.json.gz"
        n = 0
        while path.exists():  # write-once: never replace
            n += 1
            path = directory / f"{stamp}_{n}.json.gz"
        tmp = path.with_name(path.name + ".tmp")
        blob = gzip.compress(body)
        tmp.write_bytes(blob)
        tmp.rename(path)
        row: dict[str, Any] = {
            "path": str(path.relative_to(self.root)),
            "source": source,
            "key": key,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "gz_bytes": len(blob),
            "received_at": now.isoformat(),
            "http_status": http_status,
        }
        if note:
            row["note"] = note
        with self.manifest_path.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        self.files += 1
        self.bytes_gz += len(blob)
        return path


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Heartbeat-style small file: replaced atomically (not part of the write-once raw tree)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True))
    tmp.replace(path)
