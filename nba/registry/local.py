"""The ``local`` registry backend: DuckDB ``experiments`` table + a
filesystem artifact store under ``./registry_store/<model_name>/<version>/``.

This is the *only* backend built per CLAUDE.md ("Registry retrofit" ->
"Backends"). It never talks to Snowflake or any employer system; it is a
from-scratch re-implementation of the alias/promotion pattern against
DuckDB and local disk.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

#: Column name -> DuckDB type for the registry-retrofit columns. schema.sql
#: (owned by the data engineer) already defines these on `experiments`; this
#: migration is purely defensive so the adapter also works against an older
#: or hand-rolled `experiments` table without editing schema.sql.
_RETROFIT_COLUMNS: dict[str, str] = {
    "version": "VARCHAR",
    "alias": "VARCHAR",
    "tags": "JSON",
    "metadata": "JSON",
}

#: File suffixes that must never be copied into the registry store (raw data
#: / the project database). See CLAUDE.md "Cheapness rules".
_FORBIDDEN_SUFFIXES = {".parquet", ".duckdb"}

#: Default cap on total artifact size logged per version (~100 MB).
DEFAULT_MAX_ARTIFACT_BYTES = 100_000_000

_VALID_STAGES = {"candidate", "production", "deprecated"}


class LocalRegistry:
    """``RegistryAdapter`` backed by DuckDB + a local artifact directory."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        store_dir: Path,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        self._con = con
        self._store_dir = store_dir
        self._max_artifact_bytes = max_artifact_bytes
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        """Idempotently ensure the retrofit columns exist on ``experiments``.

        Defensive only: schema.sql already ships these columns. Never edits
        schema.sql; only runs ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``.
        """
        for column, col_type in _RETROFIT_COLUMNS.items():
            self._con.execute(
                f"ALTER TABLE experiments ADD COLUMN IF NOT EXISTS {column} {col_type}"
            )

    # -- RegistryAdapter -----------------------------------------------

    def next_version(self, model_name: str) -> str:
        rows = self._con.execute(
            "SELECT version FROM experiments WHERE model_name = ? AND version IS NOT NULL",
            [model_name],
        ).fetchall()
        max_seen = 0
        for (version,) in rows:
            if isinstance(version, str) and version.startswith("v") and version[1:].isdigit():
                max_seen = max(max_seen, int(version[1:]))
        return f"v{max_seen + 1}"

    def log_model(
        self,
        model_name: str,
        version: str,
        model_path: str,
        metrics: dict[str, object],
        metadata: dict[str, object],
        tags: dict[str, object],
    ) -> None:
        source = Path(model_path)
        if not source.exists():
            raise FileNotFoundError(f"model_path does not exist: {source}")

        if source.is_file():
            files = [source]
        else:
            files = sorted(p for p in source.rglob("*") if p.is_file())
        forbidden = [p for p in files if p.suffix in _FORBIDDEN_SUFFIXES]
        if forbidden:
            raise ValueError(
                "refusing to log raw data / database artifacts into the registry "
                f"store: {[str(p) for p in forbidden]}"
            )

        total_bytes = sum(p.stat().st_size for p in files)
        if total_bytes > self._max_artifact_bytes:
            raise ValueError(
                f"artifact for {model_name} {version} is {total_bytes} bytes, exceeding the "
                f"{self._max_artifact_bytes}-byte cheapness cap"
            )

        target_dir = self._store_dir / model_name / version
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        if source.is_file():
            shutil.copy2(source, target_dir / source.name)
        else:
            for f in files:
                rel = f.relative_to(source)
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        run_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        rung = tags.get("rung")
        self._con.execute(
            """
            INSERT INTO experiments
                (run_id, created_at, rung, model_name, config, metrics,
                 artifact_path, stage, version, alias, tags, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                created_at,
                rung if isinstance(rung, int) else None,
                model_name,
                json.dumps(tags.get("source_config")),
                json.dumps(metrics),
                str(target_dir),
                "candidate",
                version,
                "candidate",
                json.dumps(tags),
                json.dumps(metadata),
            ],
        )

    def set_alias(self, model_name: str, version: str, alias: str) -> None:
        self._require_version(model_name, version)
        self._con.execute(
            "UPDATE experiments SET alias = ? WHERE model_name = ? AND version = ?",
            [alias, model_name, version],
        )

    def promote(self, model_name: str, version: str) -> None:
        self._require_version(model_name, version)
        # Demote whatever is currently production (excluding the version
        # being promoted, in case promote() is called twice on the same
        # version -- promotion must stay idempotent).
        self._con.execute(
            """
            UPDATE experiments
            SET stage = 'deprecated', alias = 'deprecated'
            WHERE model_name = ? AND stage = 'production' AND version != ?
            """,
            [model_name, version],
        )
        self._con.execute(
            "UPDATE experiments SET stage = 'production', alias = 'production' "
            "WHERE model_name = ? AND version = ?",
            [model_name, version],
        )

    def load_model(self, model_name: str, alias: str = "production") -> str:
        row = self._con.execute(
            """
            SELECT artifact_path FROM experiments
            WHERE model_name = ? AND alias = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            [model_name, alias],
        ).fetchone()
        if row is None:
            raise LookupError(f"no {alias!r} version registered for model {model_name!r}")
        path: str = row[0]
        return path

    # -- internal --------------------------------------------------------

    def _require_version(self, model_name: str, version: str) -> None:
        row = self._con.execute(
            "SELECT 1 FROM experiments WHERE model_name = ? AND version = ?",
            [model_name, version],
        ).fetchone()
        if row is None:
            raise LookupError(f"no registered version {version!r} for model {model_name!r}")
