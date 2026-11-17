"""Health (with the system strip's stored facts), the data's own description, the error registry
and the evidence stub."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..deps import StoreDep
from ..health import health_detail
from ..store import Json
from ..system_extra import error_registry, strip_fields


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health(store: StoreDep) -> dict[str, Any]:
        """``status`` and ``mode`` as always, then the data's age and what its manifest says, then
        the strip's stored facts (newest stored quote per book, stale marks, what is missing)."""
        return {
            "status": "ok",
            "mode": store.mode,
            **health_detail(store),
            **strip_fields(getattr(store, "strip", None), getattr(store, "manifest", {}) or {}),
        }

    @app.get("/api/meta")
    def meta(store: StoreDep) -> dict[str, Any]:
        return store.meta()

    @app.get("/api/errors")
    def errors_route() -> dict[str, Any]:
        """The shared error-code registry (app/error_codes.json), as the web help page reads it.
        Not backed by the export store, so it answers even when the export is refused."""
        return error_registry()

    @app.get("/api/evidence")
    def evidence(store: StoreDep) -> Json:
        return store.evidence
