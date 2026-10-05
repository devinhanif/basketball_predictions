"""The ``RegistryAdapter`` contract.

Ports the *design patterns* of a Snowflake-backed model registry into a
generic, backend-agnostic protocol (see CLAUDE.md "Registry retrofit").
Nothing here talks to Snowflake, employer infrastructure, or employer code;
it is a from-scratch, five-method contract that any backend (today: only
``local``) must satisfy.
"""

from __future__ import annotations

from typing import Protocol


class RegistryAdapter(Protocol):
    """Minimal contract every registry backend implements.

    Mirrors the work registry's ``ForecastModel``-adjacent registry API:
    auto-incrementing versions, alias-based promotion (``candidate`` ->
    ``production`` -> ``deprecated``), and metrics/metadata lookup -- but
    re-implemented from scratch against DuckDB + the local filesystem.
    """

    def next_version(self, model_name: str) -> str:
        """Return the next auto-incrementing version string (``v1``, ``v2``, ...)."""
        ...

    def log_model(
        self,
        model_name: str,
        version: str,
        model_path: str,
        metrics: dict[str, object],
        metadata: dict[str, object],
        tags: dict[str, object],
    ) -> None:
        """Register a new version as a ``candidate``. Never auto-promotes."""
        ...

    def set_alias(self, model_name: str, version: str, alias: str) -> None:
        """Point an alias at a specific version, without touching other rows."""
        ...

    def promote(self, model_name: str, version: str) -> None:
        """Promote ``version`` to ``production``; the prior ``production`` -> ``deprecated``."""
        ...

    def load_model(self, model_name: str, alias: str = "production") -> str:
        """Return the local artifact path for ``model_name`` at ``alias``."""
        ...
