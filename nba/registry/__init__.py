"""Local model registry adapter (see CLAUDE.md "Registry retrofit").

Only the ``local`` backend (DuckDB ``experiments`` table + a filesystem
artifact store) is implemented. Select a backend via ``get_registry()``,
which reads the ``REGISTRY_BACKEND`` env var (default ``"local"``).
"""

from __future__ import annotations

from nba.registry.cli_guard import validate_cli_flags
from nba.registry.factory import get_registry
from nba.registry.local import LocalRegistry
from nba.registry.metadata import build_run_metadata, build_tags
from nba.registry.protocol import RegistryAdapter

__all__ = [
    "LocalRegistry",
    "RegistryAdapter",
    "build_run_metadata",
    "build_tags",
    "get_registry",
    "validate_cli_flags",
]
