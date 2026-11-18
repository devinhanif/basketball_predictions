"""One game: its players and forecasts, the teammate-sits scenarios, its result and its
tip-off timeline."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..deps import StoreDep
from ..errors import (
    ApiError,
)
from ..knows import GameKnows, game_knows
from ..models import (
    GameView,
    PlayerView,
)
from ..params import GameId
from ..role import player_role
from ..scenarios import ScenariosView, game_scenarios
from ..views import (
    _game_notes,
    _need_game,
    _prop_view,
    _team,
    _wp,
)


def register(app: FastAPI) -> None:
    @app.get("/api/games/{game_id}", response_model=GameView)
    def game(game_id: GameId, store: StoreDep) -> GameView:
        g = _need_game(store, game_id)
        players = [
            PlayerView(
                player_id=pl["player_id"],
                name=pl["name"],
                side=pl["side"],
                props={st: _prop_view(st, pr) for st, pr in pl["props"].items()},
                role=None if store.history is None else player_role(store.history, g, pl),
            )
            for pl in g["players"]
        ]
        return GameView(
            game_id=g["game_id"],
            date=g["date"],
            tip_utc=g["tip_utc"],
            arena=g.get("arena"),
            home=_team(g["home"]),
            away=_team(g["away"]),
            win_probability=_wp(g),
            clock_utc=store.clock(game_id),
            final=bool(g.get("final", True)),
            players=players,
            notes=_game_notes(store),
        )

    @app.get("/api/games/{game_id}/scenarios", response_model=ScenariosView)
    def game_scenarios_route(game_id: GameId, store: StoreDep) -> ScenariosView:
        """'If a teammate sits' for one game (read-only): points direction and a team-level size,
        rebounds direction only, nothing else. Says why when it has nothing."""
        return game_scenarios(store, _need_game(store, game_id))

    @app.get("/api/games/{game_id}/result")
    def game_result(game_id: GameId, store: StoreDep) -> dict[str, Any]:
        g = _need_game(store, game_id)
        if g.get("result") is None:
            raise ApiError("game_not_played", "that game has not been played yet", field="game_id")
        return {
            "game_id": game_id,
            "label": "result (replay)" if store.mode == "fixture_replay" else "final",
            "home_pts": g["result"]["home_pts"],
            "away_pts": g["result"]["away_pts"],
            "home_won": g["result"]["home_won"],
            "players": [
                {
                    "player_id": pl["player_id"],
                    "minutes": None if pl["result"] is None else pl["result"]["minutes"],
                    "starter": None if pl["result"] is None else pl["result"]["starter"],
                    "actual": {
                        st: pr["actual"]
                        for st, pr in pl["props"].items()
                        if pr["actual"] is not None
                    },
                }
                for pl in g["players"]
            ],
        }

    @app.get("/api/knows/{game_id}", response_model=GameKnows)
    def knows_game(game_id: GameId, store: StoreDep) -> GameKnows:
        """The game's tip-off timeline: news on the injury report and the biggest forecast moves."""
        return game_knows(store, _need_game(store, game_id))
