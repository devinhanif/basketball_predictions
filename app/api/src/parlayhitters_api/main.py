"""Read-only parlayhitters.ai API over fixtures. No writes, no network, no credentials."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .deps import get_store, reset_store
from .errors import (
    RequestContext,
    configure_logging,
    error_response,
    install_error_handlers,
    request_id_of,
)
from .export_store import ExportRefused
from .forecast import american_to_raw_prob
from .live import LiveHub
from .live_routes import register_live
from .routes import games, market, props, record, scores, slates, system, track
from .views import STATS, _swipe_empty_note

LOCAL_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def create_app() -> FastAPI:
    app = FastAPI(
        title="parlayhitters.ai read-only API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOCAL_ORIGIN_REGEX,
        allow_methods=["GET"],
        allow_headers=[],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if "cache-control" not in resp.headers:  # the stream keeps its own (no-transform)
            resp.headers["Cache-Control"] = "no-cache"  # the service worker owns offline caching
        return resp

    @app.exception_handler(ExportRefused)
    async def export_refused(request: Request, exc: ExportRefused) -> JSONResponse:
        return error_response(
            "export_refused", f"the data export was refused: {exc}", request_id_of(request)
        )

    install_error_handlers(app)
    register(app)
    register_live(app, LiveHub())
    # Outermost: the request id is on every response, including the last-resort 500.
    app.add_middleware(RequestContext)
    configure_logging()
    return app


def register(app: FastAPI) -> None:
    """Every store-backed route, area by area."""
    for area in (system, slates, games, props, track, market, record, scores):
        area.register(app)


app = create_app()

#: ``get_store`` and ``reset_store`` live in deps.py (the routes depend on them there); they are
#: re-exported here because callers and tests have always imported them from main.
__all__ = [
    "STATS",
    "_swipe_empty_note",
    "american_to_raw_prob",
    "app",
    "create_app",
    "get_store",
    "reset_store",
]
