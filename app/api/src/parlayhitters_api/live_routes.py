"""Routes for the live play-by-play view. Separate from the store-backed routes on purpose: nothing
here touches ``get_store``, so a slow export cold start never delays a live request."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from .live import (
    ATTRIBUTION,
    DATE_RE,
    EVENT_RE,
    HEARTBEAT_S,
    HubFull,
    LiveHub,
    UnknownGame,
    games_on,
    plays_before,
    read_checkpoint,
    snapshot_payload,
)


def _need_event(event_id: str) -> str:
    if not EVENT_RE.fullmatch(event_id):
        raise HTTPException(404, "unknown game")
    return event_id


def register_live(app: FastAPI, hub: LiveHub) -> None:
    app.state.live_hub = hub

    @app.get("/api/live/{date}")
    async def live_games(date: str) -> dict[str, Any]:
        """Every game the capture holds for a slate date with its latest state and how old it is."""
        if not DATE_RE.fullmatch(date):
            raise HTTPException(404, "not a date")
        return await asyncio.to_thread(games_on, hub.root, date)

    @app.get("/api/live/{game_id}/snapshot")
    async def live_snapshot(game_id: str) -> dict[str, Any]:
        """The same first message the stream sends, for clients that cannot hold a stream."""
        gs = await hub.current(_need_event(game_id))
        if gs is None:
            raise HTTPException(404, "no live capture for that game")
        gw = hub.games.get(game_id)
        return snapshot_payload(gs, 0 if gw is None else gw.seq, datetime.now(UTC))

    @app.get("/api/live/{game_id}/plays")
    async def live_plays(
        game_id: str,
        before: Annotated[int, Query(ge=0)] = 1_000_000,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        """Older plays, for 'load earlier' (the stream and snapshot carry only the newest)."""
        gs = await hub.current(_need_event(game_id))
        if gs is None:
            raise HTTPException(404, "no live capture for that game")
        rows = plays_before(gs, before, limit)
        return {
            "game_id": game_id,
            "plays": rows,
            "more": bool(rows) and any(k < rows[0]["n"] for k in gs.plays),
            "attribution": ATTRIBUTION,
        }

    @app.get("/api/live/{game_id}/checkpoint")
    async def live_checkpoint(game_id: str) -> dict[str, Any]:
        """In-game checkpoint model numbers, only when the JARVIS store exists and holds rows."""
        gs = await hub.current(_need_event(game_id))
        state = "pre" if gs is None else gs.state
        return await asyncio.to_thread(read_checkpoint, game_id, state)

    @app.get("/api/live/{game_id}/stream")
    async def live_stream(game_id: str) -> StreamingResponse:
        """Server-Sent Events: a snapshot, then deltas as new capture bodies land; a heartbeat
        comment every 15 s; closes after the final event."""
        _need_event(game_id)
        try:
            sub, first, final = await hub.subscribe(game_id)
        except UnknownGame:
            raise HTTPException(404, "no live capture for that game") from None
        except HubFull:
            raise HTTPException(503, "too many viewers right now; try again shortly") from None

        async def body() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n\n" + first[0]
                for m in first[1:]:
                    yield m
                if final:
                    return
                while True:
                    try:
                        item = await asyncio.wait_for(sub.queue.get(), HEARTBEAT_S)
                    except TimeoutError:
                        yield ": hb\n\n"
                        continue
                    if item is None:
                        return
                    yield item
            finally:
                hub.unsubscribe(game_id, sub)

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
