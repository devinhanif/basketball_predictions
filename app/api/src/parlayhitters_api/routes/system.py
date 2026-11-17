"""Health, the data's own description, and the evidence stub."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..deps import StoreDep
from ..health import health_detail
from ..store import Json


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health(store: StoreDep) -> dict[str, Any]:
        """``status`` and ``mode`` as always, then the data's age and what its manifest says."""
        return {"status": "ok", "mode": store.mode, **health_detail(store)}

    @app.get("/api/meta")
    def meta(store: StoreDep) -> dict[str, Any]:
        return store.meta()

    @app.get("/api/evidence")
    def evidence(store: StoreDep) -> Json:
        return store.evidence
