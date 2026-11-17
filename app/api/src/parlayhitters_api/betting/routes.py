"""GET-only routes of the betting module, all under /api/betting/.

Registered straight on the app (``register``, as every other area is) rather than through an
included router, so each route is a plain ``APIRoute`` on ``app.routes`` and the existing
route-walking tests (GET only, every route fuzzed) see it.

Every route depends on the export store, so when the export is refused (a sha mismatch, a missing
manifest) the betting module refuses with it (503 ``export_refused``) instead of answering beside
data the app cannot vouch for.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from ..deps import get_store

PREFIX = "/api/betting"
NOTE = "analysis only; no orders are placed; this service is read-only"


def register(app: FastAPI) -> None:
    @app.get(f"{PREFIX}/status", dependencies=[Depends(get_store)])
    def betting_status() -> dict[str, object]:
        """The switch's state as the API sees it. Only reachable when the switch is on."""
        return {"enabled": True, "note": NOTE}
