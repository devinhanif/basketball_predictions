"""Prediction record and distribution of the shown forecast (docs/contracts/record_v1.md,
distribution_v1.md). Read-only; stored fields only."""

from __future__ import annotations

from fastapi import FastAPI

from ..deps import StoreDep
from ..errors import ApiError
from ..honesty_models import Distribution, Record
from ..params import GameId, Stat
from ..record import distribution, prop_record, win_record
from ..store import AnyStore, Json
from ..views import STATS, _need_game, _no_forecast


def _shown_forecast(store: AnyStore, game_id: str, player_id: int, stat: str) -> tuple[Json, Json]:
    """The game and the prop's shown forecast, or the same refusal the card route gives."""
    g = _need_game(store, game_id)
    if stat not in STATS:
        raise ApiError("unknown_stat", "unknown stat", field="stat")
    for pl in g["players"]:
        if pl["player_id"] == player_id and stat in pl["props"]:
            return g, pl["props"][stat]["forecast"]
    raise _no_forecast(g, player_id)


def register(app: FastAPI) -> None:
    @app.get("/api/record/{game_id}/{player_id}/{stat}", response_model=Record)
    def record_prop(game_id: GameId, player_id: int, stat: Stat, store: StoreDep) -> Record:
        """Where the shown forecast came from: model, weights, fit, times, T-60, snapshots."""
        g, _ = _shown_forecast(store, game_id, player_id, stat)
        out = prop_record(getattr(store, "records", None), game_id, player_id, stat)
        if out is None:
            raise _no_forecast(g, player_id)
        return out

    @app.get("/api/record/{game_id}", response_model=Record)
    def record_game(game_id: GameId, store: StoreDep) -> Record:
        """Where the shown game-winner forecast came from."""
        _need_game(store, game_id)
        out = win_record(getattr(store, "records", None), game_id)
        if out is None:
            raise ApiError(
                "no_game_forecast", "no stored winner forecast for that game", field="game_id"
            )
        return out

    @app.get("/api/distribution/{game_id}/{player_id}/{stat}", response_model=Distribution)
    def distribution_route(
        game_id: GameId, player_id: int, stat: Stat, store: StoreDep
    ) -> Distribution:
        """The stored distribution of the shown forecast; missing pieces named, none derived."""
        g, forecast = _shown_forecast(store, game_id, player_id, stat)
        return distribution(getattr(store, "records", None), g, player_id, stat, forecast)
