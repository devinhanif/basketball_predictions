"""The pull-orchestrator gate for JARVIS (docs/DESIGN_PULL_ORCHESTRATOR.md).

A play whose Pinnacle reference is older than ``pulls.pinnacle_alert_max_age_min`` is not sent on
that stale price: JARVIS files one deduped pull request for the game and leaves the play out of
THIS post (an instant alert, or the slate preview). Nothing here touches a label, an edge or a
stake.

The gate fails OPEN, always: it exists only while the orchestrator is demonstrably serving requests.

* flag off, ``pulls.yaml`` disabled or unreadable, store errors     -> send as before;
* the pulls job is not alive (no fresh heartbeat, or gated off)     -> send as before;
* the game's newest request ended refused / failed / expired        -> released, send as before
  (the existing ``max_ref_age_min`` cap still limits the label to ``thin`` past 120 minutes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nba.pulls.config import PullsConfig
from nba.pulls.freshness import pulls_alive
from nba.pulls.store import PullRequest, PullStore, RequestResult, request_pull

log = logging.getLogger("nba.jarvis.pullhold")
RELEASE_STATUSES = ("refused_budget", "refused_post_tip", "failed", "expired")
SOURCE = "pinnacle"


@dataclass
class Gate:
    pc: PullsConfig
    now: datetime  # naive UTC
    store: PullStore

    @property
    def limit(self) -> float:
        return self.pc.pinnacle_alert_max_age_min

    def released(self, game_id: str, slate: Any) -> PullRequest | None:
        """The game's newest request when it ended without data inside ``cooldown_min`` (the hold
        then lets the play go), else ``None``."""
        last = self.store.latest_for_game(SOURCE, game_id, slate)
        if last is None:
            return None
        if last.status == "open" and self.now >= last.need_by_utc:
            return last  # past its deadline unserved (a dying tick never expires it): JARVIS does
        if last.status in RELEASE_STATUSES and self.now - last.status_at_utc <= timedelta(
            minutes=self.pc.cooldown_min
        ):
            return last
        return None

    def request(
        self,
        game_id: str,
        slate: Any,
        reason: str,
        request: Any | None = None,
    ) -> RequestResult:
        return (request or request_pull)(
            source=SOURCE,
            scope_game_id=game_id,
            slate_date=slate,
            requester="jarvis",
            reason=reason,
            max_age_min=self.limit,
            now=self.now,
            cfg=self.pc,
        )


def open_gate(flag: bool, pc: PullsConfig | None, now: datetime) -> Gate | None:
    """The gate, or ``None`` (send as before): flag off, or the orchestrator is not alive."""
    if not flag or pc is None:
        return None
    alive, why = pulls_alive(pc, now)
    if not alive:
        log.info("pull gate off: %s; sending as before", why)
        return None
    return Gate(pc, now, PullStore(pc.requests_db))
