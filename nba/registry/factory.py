"""Select a registry backend by the ``REGISTRY_BACKEND`` env var.

Per CLAUDE.md "Registry retrofit" -> "Backends": ``local`` is the default
and the only backend built now. Anything else raises ``NotImplementedError``
(an ``mlflow`` backend is explicitly deferred; a Snowflake backend is
explicitly out of scope and never built here).
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from nba.registry.local import DEFAULT_MAX_ARTIFACT_BYTES, LocalRegistry
from nba.registry.protocol import RegistryAdapter

#: Default location for the local artifact store (already gitignored).
DEFAULT_STORE_DIR = Path(__file__).resolve().parents[2] / "registry_store"


def get_registry(
    con: duckdb.DuckDBPyConnection,
    store_dir: Path | None = None,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> RegistryAdapter:
    """Build the registry adapter selected by ``REGISTRY_BACKEND`` (env var).

    Reads only the env var name -- never a credential -- and defaults to
    ``local`` when unset.
    """
    backend = os.environ.get("REGISTRY_BACKEND", "local")
    if backend == "local":
        resolved_store_dir = store_dir if store_dir is not None else DEFAULT_STORE_DIR
        return LocalRegistry(con, resolved_store_dir, max_artifact_bytes=max_artifact_bytes)
    raise NotImplementedError(
        f"REGISTRY_BACKEND={backend!r} is not supported; only 'local' is built "
        "(see CLAUDE.md 'Registry retrofit' -> 'Backends'). A Snowflake backend "
        "is explicitly out of scope."
    )
