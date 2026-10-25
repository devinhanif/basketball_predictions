"""Public GET-only HTTP with a timeout, bounded retries, a rate limit and a per-tick budget.

No header besides a User-Agent is ever sent; there is no way to attach a credential here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
DEFAULT_RATE_PER_S = 8.0
USER_AGENT = "nba-prediction-live-capture/1 (public market data, read-only)"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    url: str


class Fetcher:
    """GET with bounded retries; ``get`` returns None when the tick budget or deadline is spent
    (the caller records the skip) or after the final failed attempt (the error is counted)."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        rate_per_s: float = DEFAULT_RATE_PER_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(TIMEOUT_S), headers={"User-Agent": USER_AGENT}
        )
        self.min_interval = 1.0 / rate_per_s
        self.clock = clock
        self.sleep = sleep
        self._next = 0.0  # earliest start of the next request (rate-limit slot reservation)
        self._lock = threading.Lock()
        self.avg_latency = 0.25  # EMA of measured request seconds; sizes the per-tick budget
        self.requests = 0  # all-time
        self.errors = 0
        self.tick_requests = 0
        self.tick_budget = 10**9
        self.tick_deadline = float("inf")
        self.last_error = ""

    def begin_tick(self, budget: int, deadline_s: float) -> None:
        self.tick_requests = 0
        self.tick_budget = budget
        self.tick_deadline = self.clock() + deadline_s

    def exhausted(self) -> bool:
        return self.tick_requests >= self.tick_budget or self.clock() >= self.tick_deadline

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        for attempt in range(MAX_ATTEMPTS):
            with self._lock:
                if self.exhausted():
                    return None
                slot = max(self.clock(), self._next)
                self._next = slot + self.min_interval
                self.tick_requests += 1
                self.requests += 1
            wait = slot - self.clock()
            if wait > 0:
                self.sleep(wait)
            t0 = self.clock()
            try:
                r = self.client.get(url, params=params)
            except httpx.HTTPError as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                if attempt == MAX_ATTEMPTS - 1:
                    with self._lock:
                        self.errors += 1
                    return None
                self.sleep(1.0 * (attempt + 1))
                continue
            self.avg_latency = 0.9 * self.avg_latency + 0.1 * (self.clock() - t0)
            if (r.status_code == 429 or r.status_code >= 500) and attempt < MAX_ATTEMPTS - 1:
                self.sleep(2.0 * (attempt + 1))
                continue
            return Response(r.status_code, r.content, str(r.url))
        with self._lock:
            self.errors += 1
        return None

    def budget_for(self, deadline_s: float, cap: int) -> int:
        """Requests that fit the deadline at the measured latency (sequential), capped."""
        return max(1, min(cap, int(deadline_s / max(self.min_interval, self.avg_latency + 0.01))))
