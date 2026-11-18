"""The live score card routes (GET only): one game's card, and every game's cards for a night.

Same independence as ``live_routes``: nothing waits on the export store. The live box comes from
the hub (or the newest capture body on disk, parsed in a worker thread and cached); the stored
lines come from the export store only once it is loaded, else ``lines_state`` is ``"loading"``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .errors import ApiError
from .live import GameState, LiveHub, _load_newest, list_event_ids
from .live_card import (
    build_card,
    find_stored_game,
    ingame_model_on,
    read_ingame_v2,
    static_for,
    store_if_ready,
)
from .params import EventId, LiveDate
from .store import store_key


def _stored_for(
    gs: GameState,
) -> tuple[tuple[list[dict[str, Any]], dict[str, int]] | None, str]:
    store, lines_state = store_if_ready()
    if store is None:
        return None, lines_state
    game = find_stored_game(store, gs.date, gs.home["abbr"], gs.away["abbr"])
    if game is None:
        return None, "ok"
    return static_for(store_key(), game), "ok"


def card_for(gs: GameState, now: datetime) -> dict[str, Any]:
    """Blocking part (store lookup, join, optional snapshot read); always run in a worker thread."""
    stored, lines_state = _stored_for(gs)
    model = read_ingame_v2(gs.event_id, gs.state, now) if ingame_model_on() else None
    return build_card(gs, stored, lines_state, now, model)


async def _state_for(hub: LiveHub, root: Path, date: str, event_id: str) -> GameState | None:
    gw = hub.games.get(event_id)
    if gw is not None and gw.state is not None:
        return gw.state
    return await asyncio.to_thread(_load_newest, root, date, event_id)


def register_live_card(app: FastAPI, hub: LiveHub) -> None:
    @app.get("/api/live/{game_id}/card")
    async def live_card(game_id: EventId) -> dict[str, Any]:
        """One game's score card: the posted lines stored for this game against the live box."""
        gs = await hub.current(game_id)
        if gs is None:
            raise ApiError("no_live_capture", "no live capture for that game", field="game_id")
        return await asyncio.to_thread(card_for, gs, datetime.now(UTC))

    @app.get("/api/live/{date}/cards")
    async def live_cards(date: LiveDate) -> dict[str, Any]:
        """Every game's card for a night: the slate view of every tracked player."""
        ids = await asyncio.to_thread(list_event_ids, hub.root, date)
        states = await asyncio.gather(*[_state_for(hub, hub.root, date, i) for i in ids])
        now = datetime.now(UTC)
        games = [s for s in states if s is not None]

        def build() -> list[dict[str, Any]]:
            return [card_for(s, now) for s in games]

        cards = await asyncio.to_thread(build)
        cards.sort(key=lambda c: (str(c["tip_utc"]), c["game_id"]))
        out: dict[str, Any] = {"date": date, "server_now": now.isoformat(), "games": cards}
        if not cards:
            out["note"] = "No live capture for this date."
        return out
