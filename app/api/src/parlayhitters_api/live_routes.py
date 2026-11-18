"""Routes for the live play-by-play view. Separate from the store-backed routes on purpose: nothing
here touches ``get_store``, so a slow export cold start never delays a live request."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse

from .errors import ApiError
from .live import (
    ATTRIBUTION,
    HEARTBEAT_S,
    IDLE_CLOSE_S,
    STALE_AFTER_S,
    STREAM_MAX_S,
    STREAM_PADDING,
    HubFull,
    LiveHub,
    UnknownGame,
    games_on,
    plays_before,
    read_checkpoint,
    snapshot_payload,
    sse,
)
from .live_card_routes import register_live_card
from .params import EventId, LiveDate


def register_live(app: FastAPI, hub: LiveHub) -> None:
    app.state.live_hub = hub
    register_live_card(app, hub)

    @app.get("/api/live/{date}")
    async def live_games(date: LiveDate) -> dict[str, Any]:
        """Every game the capture holds for a slate date with its latest state and how old it is."""
        return await asyncio.to_thread(games_on, hub.root, date)

    @app.get("/api/live/{game_id}/snapshot")
    async def live_snapshot(game_id: EventId) -> dict[str, Any]:
        """The same first message the stream sends, for clients that cannot hold a stream."""
        gs = await hub.current(game_id)
        if gs is None:
            raise ApiError("no_live_capture", "no live capture for that game", field="game_id")
        gw = hub.games.get(game_id)
        return snapshot_payload(gs, 0 if gw is None else gw.seq, datetime.now(UTC))

    @app.get("/api/live/{game_id}/plays")
    async def live_plays(
        game_id: EventId,
        before: Annotated[int, Query(ge=0)] = 1_000_000,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        """Older plays, for 'load earlier' (the stream and snapshot carry only the newest)."""
        gs = await hub.current(game_id)
        if gs is None:
            raise ApiError("no_live_capture", "no live capture for that game", field="game_id")
        rows = plays_before(gs, before, limit)
        return {
            "game_id": game_id,
            "plays": rows,
            "more": bool(rows) and any(k < rows[0]["n"] for k in gs.plays),
            "attribution": ATTRIBUTION,
        }

    @app.get("/api/live/{game_id}/checkpoint")
    async def live_checkpoint(game_id: EventId) -> dict[str, Any]:
        """In-game checkpoint model numbers, only when the JARVIS store exists and holds rows."""
        gs = await hub.current(game_id)
        state = "pre" if gs is None else gs.state
        return await asyncio.to_thread(read_checkpoint, game_id, state)

    @app.get("/api/live/{game_id}/stream")
    async def live_stream(game_id: EventId) -> StreamingResponse:
        """Server-Sent Events: a snapshot, then deltas as new capture bodies land; a heartbeat
        comment every 15 s; closes after the final event.

        Bounded on the server: a ``stale`` event once no new capture body has landed for
        STALE_AFTER_S (the capture may have stopped; the client can say so), an ``end`` event and
        a clean close once none has landed for IDLE_CLOSE_S (``capture_stopped``) or the stream
        has run STREAM_MAX_S (``max_duration``; the browser reconnects to a fresh snapshot)."""
        try:
            sub, first, final = await hub.subscribe(game_id)
        except UnknownGame:
            raise ApiError(
                "no_live_capture", "no live capture for that game", field="game_id"
            ) from None
        except HubFull:
            raise ApiError(
                "too_many_viewers", "too many viewers right now; try again shortly"
            ) from None

        async def body() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            opened = last_body = loop.time()
            stale_sent = False
            try:
                yield STREAM_PADDING + "retry: 2000\n\n" + first[0]
                for m in first[1:]:
                    yield m
                if final:
                    return
                while True:
                    now = loop.time()
                    if now - opened >= STREAM_MAX_S:
                        yield sse("end", {"game_id": game_id, "reason": "max_duration"})
                        return
                    if now - last_body >= IDLE_CLOSE_S:
                        idle = round(now - last_body, 1)
                        yield sse(
                            "end",
                            {"game_id": game_id, "reason": "capture_stopped", "idle_s": idle},
                        )
                        return
                    wait = min(
                        HEARTBEAT_S,
                        IDLE_CLOSE_S,
                        STALE_AFTER_S,
                        STREAM_MAX_S,
                        STREAM_MAX_S - (now - opened),
                        IDLE_CLOSE_S - (now - last_body),
                    )
                    try:
                        item = await asyncio.wait_for(sub.queue.get(), max(0.01, wait))
                    except TimeoutError:
                        idle = loop.time() - last_body
                        if not stale_sent and idle >= STALE_AFTER_S:
                            stale_sent = True
                            yield sse(
                                "stale",
                                {"game_id": game_id, "idle_s": round(idle, 1),
                                 "note": "No new capture body has landed for a while."},
                            )  # fmt: skip
                        else:
                            yield ": hb\n\n"
                        continue
                    if item is None:
                        return
                    last_body, stale_sent = loop.time(), False
                    yield item
            finally:
                hub.unsubscribe(game_id, sub)

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
