"""Parlay diagnostics (docs/contracts/parlay_diagnostics_v1.md): the engine's stored joint chances,
as stored, a page at a time. No price, stake or expected value."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query

from ..deps import StoreDep
from ..errors import ApiError
from ..honesty_models import ParlayDiagnostics
from ..params import SlateDate
from ..parlay_diag import parlay_diagnostics


def register(app: FastAPI) -> None:
    @app.get("/api/parlay-diagnostics/{date}", response_model=ParlayDiagnostics)
    def parlay_diag(
        date: SlateDate,
        store: StoreDep,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ParlayDiagnostics:
        """The stored joint chances of the engine's records for that date, as stored."""
        raw = getattr(store, "parlay_raw", None)
        if raw is None:
            if store.slate(date) is None:
                raise ApiError("no_parlay_check", "no parlay check for that date", field="date")
            return parlay_diagnostics(date, None, None, offset, limit)
        if str(raw.get("date")) != date:
            raise ApiError("no_parlay_check", "no parlay check for that date", field="date")
        return parlay_diagnostics(date, raw, getattr(store, "parlay_records", []), offset, limit)
