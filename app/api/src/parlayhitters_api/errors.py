"""One shape for every refusal, and a request id on every response.

Every error the API answers is JSON::

    {"ok": false, "code": "<code>", "text": "<plain sentence>", "field": "<param>" | null,
     "request_id": "<id>", "detail": "<the string the route returned before>"}

The codes and their HTTP statuses come from ``app/error_codes.json`` (shared with the web app); a
code that is not in the registry, or a status that disagrees with it, fails at import or at the
raise site, never silently. ``detail`` keeps the string each route answered before the contract,
so an older client that reads ``detail`` keeps working.

``RequestContext`` (pure ASGI, outermost) stamps an ``X-Request-Id`` header on every response,
success or not, writes one log line per request with the same id, and turns any exception that
escapes the app into a 500 ``internal_error`` body: never a stack trace, never a raw message.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "error_codes.json"
REQUEST_ID_HEADER = "X-Request-Id"
log = logging.getLogger("parlayhitters.api")


def _load_registry(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {c["code"]: c for c in data["codes"] if c.get("side") == "api"}


REGISTRY: dict[str, dict[str, Any]] = _load_registry()


class ApiError(Exception):
    """A refusal with a registry code. ``detail`` is the route's own words (kept from before the
    contract); ``field`` names the path or query parameter at fault, when one is."""

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        field: str | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        entry = REGISTRY.get(code)
        if entry is None:
            raise ValueError(f"error code {code!r} is not in {REGISTRY_PATH.name}")
        self.code = code
        self.status: int = int(entry["status"])
        self.text = text or str(entry["what"])
        self.detail = detail or self.text
        self.field = field
        self.headers = headers
        super().__init__(f"{code}: {self.detail}")


def new_request_id() -> str:
    return secrets.token_hex(8)


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else "unknown"


_PLAIN_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def error_body(
    code: str, detail: Any, request_id: str, *, field: str | None = None, text: str | None = None
) -> dict[str, Any]:
    entry = REGISTRY[code]
    return {
        "ok": False,
        "code": code,
        "text": text or str(entry["what"]),
        "field": field,
        "request_id": request_id,
        "detail": detail,
    }


def error_response(
    code: str,
    detail: Any,
    request_id: str,
    *,
    status: int | None = None,
    field: str | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = error_body(code, detail, request_id, field=field, text=text)
    resp = JSONResponse(
        body,
        status_code=status if status is not None else int(REGISTRY[code]["status"]),
        headers=headers,
    )
    for k, v in _PLAIN_HEADERS.items():
        resp.headers.setdefault(k, v)
    resp.headers[REQUEST_ID_HEADER] = request_id
    return resp


#: FastAPI and Starlette's own refusals, by status.
_STATUS_CODE = {404: "not_found", 405: "method_not_allowed", 422: "bad_request"}


def _validation_field(exc: RequestValidationError) -> str | None:
    for err in exc.errors():
        loc: tuple[Any, ...] = tuple(err.get("loc") or ())
        if len(loc) >= 2 and loc[0] in ("path", "query"):
            return str(loc[-1])
    return None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            exc.code,
            exc.detail,
            request_id_of(request),
            field=exc.field,
            text=exc.text,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        field = _validation_field(exc)
        text = (
            f"The value given for {field} is not one this address accepts."
            if field
            else "The request was not in the form this address accepts."
        )
        # The validator's own messages echo the input back; the reply names the field instead.
        return error_response("bad_request", text, request_id_of(request), field=field, text=text)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODE.get(exc.status_code)
        if code is None:
            code = "internal_error" if exc.status_code >= 500 else "bad_request"
        return error_response(
            code,
            exc.detail,
            request_id_of(request),
            status=exc.status_code,
            headers=dict(exc.headers) if exc.headers else None,
        )


def _printable(path: str, limit: int = 200) -> str:
    """A request path made safe for one log line (no newlines or control characters)."""
    out = path.encode("unicode_escape", "backslashreplace").decode("ascii", "replace")
    return out if len(out) <= limit else out[:limit] + "..."


class RequestContext:
    """Outermost middleware: request id, one log line, and the last-resort 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = new_request_id()
        scope.setdefault("state", {})["request_id"] = rid
        started = False
        status = 0
        t0 = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            log.exception("request_id=%s unhandled error on %s", rid, _printable(scope["path"]))
            if started:
                return  # a stream already under way: nothing more can be said on it
            resp = error_response("internal_error", "internal error (see the server log)", rid)
            await resp(scope, receive, send_with_id)
        finally:
            log.info(
                "request_id=%s method=%s path=%s status=%s ms=%.1f",
                rid,
                scope.get("method"),
                _printable(scope["path"]),
                status,
                (time.perf_counter() - t0) * 1000,
            )


def configure_logging() -> None:
    """Make the per-request lines visible under uvicorn (which configures only its own loggers)."""
    if not any(getattr(h, "_ph_api", False) for h in log.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        h._ph_api = True  # type: ignore[attr-defined]
        log.addHandler(h)
    log.setLevel(logging.INFO)
