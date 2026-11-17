"""Market-side stored rows: the parlay verdict, contract edges, the desk and the top-parlays
panel."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import FastAPI, Query, Request

from ..deps import StoreDep, _is_local_request
from ..desk import DeskMissing, DeskRefused, load_desk
from ..desk_shared import load_shared_desk
from ..edges import leg_edges, top_contracts
from ..errors import (
    ApiError,
)
from ..params import GameId, Stat, TopParlaysDate
from ..store import Json
from ..top_parlays import (
    TopParlaysMissing,
    TopParlaysRefused,
    TopParlaysUnavailable,
    load_top_parlays,
)
from ..views import STATS


def register(app: FastAPI) -> None:
    @app.get("/api/parlay-of-the-day")
    def parlay(store: StoreDep) -> Json:
        return store.parlay

    @app.get("/api/edges/{game_id}/{player_id}/{stat}")
    def edges(game_id: GameId, player_id: int, stat: Stat, store: StoreDep) -> Json:
        """Stored single-contract rows for one player and stat: model chance with its
        interval, the ask, the fee and the engine's own edge after fees. Copied, not recomputed."""
        if stat not in STATS:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        rows = leg_edges(getattr(store, "parlay_records", []), game_id, player_id, stat)
        return {
            "report_date": store.parlay.get("report_date"),
            "contracts": rows,
            "note": (
                "Prediction-market (Kalshi) contracts at the ask, with the taker fee, as the "
                "engine priced them. Edge is the engine's own number after fees. Paper only."
            ),
        }

    @app.get("/api/contracts/top")
    def contracts_top(store: StoreDep, n: Annotated[int, Query(ge=1, le=50)] = 12) -> Json:
        """The best-priced stored contracts by the engine's own edge after fees, with names."""
        names = getattr(store, "names", {})
        return {
            "report_date": store.parlay.get("report_date"),
            **top_contracts(getattr(store, "parlay_records", []), names, store.game, n),
        }

    @app.get("/api/desk")
    def desk(request: Request) -> Json:
        """The desk export: the private file only for PH_PRIVATE=1 AND a direct local request;
        everyone else, including anything arriving through a tunnel, gets the shared cut."""
        private = os.environ.get("PH_PRIVATE") == "1" and _is_local_request(request)
        try:
            return load_desk() if private else load_shared_desk()
        except DeskMissing:
            raise ApiError("no_desk", "no desk export has been written yet") from None
        except DeskRefused as e:
            raise ApiError("desk_refused", f"desk export refused: {e}") from None

    @app.get("/api/top-parlays/{date}")
    def top_parlays(date: TopParlaysDate) -> Json:
        try:
            return load_top_parlays(date)
        except TopParlaysMissing:
            raise ApiError(
                "no_top_parlays", f"no top parlays file for {date}", field="date"
            ) from None
        except TopParlaysUnavailable as e:
            raise ApiError("top_parlays_unavailable", f"top parlays unavailable: {e}") from None
        except TopParlaysRefused as e:
            raise ApiError("top_parlays_refused", f"top parlays file refused: {e}") from None
