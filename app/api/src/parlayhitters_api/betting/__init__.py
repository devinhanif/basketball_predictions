"""The betting tools module's API half. Included in the app only when the switch is on
(``features.betting_enabled`` at ``create_app``); with it off none of these paths exist and they
answer the ordinary typed 404 (``not_found``).

Read-only like the rest of the API: GET routes only, no state on the server, no outbound network,
no credentials. Analysis only; no orders are placed. Every betting path lives under /api/betting/.
"""

from __future__ import annotations

from .routes import register

__all__ = ["register"]
