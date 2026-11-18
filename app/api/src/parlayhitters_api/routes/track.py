"""The track record: calibration of settled games, and the guess-the-outcome deck."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query

from ..deps import StoreDep, _cache
from ..errors import (
    ApiError,
)
from ..params import GameId, Stat
from ..trackrecord import (
    Calibration,
    GuessDeck,
    GuessReveal,
    calibration,
    guess_deck,
    guess_reveal,
)
from ..views import (
    _guess_field,
)


def register(app: FastAPI) -> None:
    @app.get("/api/track-record/calibration", response_model=Calibration)
    def track_calibration(store: StoreDep) -> Calibration:
        """When the model said X%, how often it happened, from the settled games loaded here."""
        hit = _cache.get("calibration")
        if hit is not None and hit[0] is store:
            cached: Calibration = hit[1]
            return cached
        result = calibration(store)
        _cache["calibration"] = (store, result)
        return result

    @app.get("/api/track-record/guess", response_model=GuessDeck)
    def track_guess(
        store: StoreDep,
        seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 1,
        n: Annotated[int, Query(ge=1, le=50)] = 12,
    ) -> GuessDeck:
        """Finished props with a posted line, drawn at random. No outcome and no forecast."""
        return guess_deck(store, seed, n)

    @app.get("/api/track-record/guess/{game_id}/{player_id}/{stat}", response_model=GuessReveal)
    def track_guess_reveal(
        game_id: GameId, player_id: int, stat: Stat, store: StoreDep
    ) -> GuessReveal:
        """What happened and what the model said, for one finished prop."""
        out = guess_reveal(store, f"{game_id}:{player_id}:{stat}")
        if out is None:
            raise ApiError(
                "no_posted_line",
                "no finished prop with a posted line for that player and stat",
                field=_guess_field(store, game_id, player_id, stat),
            )
        return out
