"""Combo mode (docs/contracts/combo_export_v1.md): the stored night file, unchanged, inside a small
envelope that adds the server's clock for the "not after tip" rule. Pure probability: no price."""

from __future__ import annotations

from fastapi import FastAPI

from ..combo import (
    ComboMissing,
    ComboRefused,
    ComboUnavailable,
    clock_for,
    list_nights,
    load_combo,
)
from ..deps import StoreDep
from ..errors import ApiError
from ..params import ComboDate
from ..store import Json


def register(app: FastAPI) -> None:
    @app.get("/api/combo")
    def combo_nights(store: StoreDep) -> Json:
        """The nights that have a combo file, oldest first: `{"nights": ["2024-11-12"]}`."""
        base = store.root if store.mode == "export" else store.root / "combo"
        try:
            return {"nights": list_nights(base)}
        except ComboUnavailable as e:
            raise ApiError("combo_unavailable", f"combo unavailable: {e}") from None

    @app.get("/api/combo/{date}")
    def combo(date: ComboDate, store: StoreDep) -> Json:
        """`{"mode", "clock_utc", "combo": <the file as stored>}`. In export mode the file is read
        from APP_EXPORT_DIR (404 when absent: never a sample); in fixture mode from the fixtures."""
        exporting = store.mode == "export"
        base = store.root if exporting else store.root / "combo"
        try:
            payload = load_combo(date, base)
        except ComboMissing:
            raise ApiError("no_combo", f"no combo file for {date}", field="date") from None
        except ComboUnavailable as e:
            raise ApiError("combo_unavailable", f"combo unavailable: {e}") from None
        except ComboRefused as e:
            raise ApiError("combo_refused", f"combo file refused: {e}") from None
        stored: str | None
        if exporting:
            stored = store.clock("")
        else:
            stored = store.slate_clock(date) if store.slate(date) is not None else None
        clock = clock_for(payload, stored)
        return {"mode": store.mode, "clock_utc": clock, "combo": payload}
