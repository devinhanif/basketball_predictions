"""GET-only routes of the betting module, all under /api/betting/.

Registered straight on the app (``register``, as every other area is) rather than through an
included router, so each route is a plain ``APIRoute`` on ``app.routes`` and the existing
route-walking tests (GET only, every route fuzzed) see it.
"""

from __future__ import annotations

from fastapi import FastAPI

PREFIX = "/api/betting"
NOTE = "analysis only; no orders are placed; this service is read-only"


def register(app: FastAPI) -> None:
    @app.get(f"{PREFIX}/status")
    def betting_status() -> dict[str, object]:
        """The switch's state as the API sees it. Only reachable when the switch is on."""
        return {"enabled": True, "note": NOTE}
