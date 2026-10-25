"""GET-only fetcher that honours ``Retry-After`` and slows down after a 429.

Same contract as ``nba.live.http.Fetcher`` (it subclasses it: tick budget, deadline, rate slots,
``Response``); the difference is the 429 path. A 429 waits ``Retry-After`` seconds when the
server sends it (capped), and halves the request rate for the rest of the tick, so one
rate-limited book does not burn the tick deadline on blind retries. No header besides a
User-Agent is ever sent.
"""

from __future__ import annotations

from typing import Any

import httpx

from nba.live.http import MAX_ATTEMPTS, Fetcher, Response

MAX_RETRY_AFTER_S = 10.0


class PoliteFetcher(Fetcher):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.base_interval = self.min_interval
        self.rate_limited = 0  # 429 responses seen, all time

    def begin_tick(self, budget: int, deadline_s: float) -> None:
        super().begin_tick(budget, deadline_s)
        self.min_interval = self.base_interval  # the slow-down lasts one tick

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
            if r.status_code == 429:
                self.rate_limited += 1
                self.min_interval = min(self.min_interval * 2.0, 1.0)
                if attempt < MAX_ATTEMPTS - 1:
                    try:
                        ra = float(r.headers.get("retry-after", ""))
                    except ValueError:
                        ra = 2.0 * (attempt + 1)
                    self.sleep(min(max(ra, 1.0), MAX_RETRY_AFTER_S))
                    continue
            elif r.status_code >= 500 and attempt < MAX_ATTEMPTS - 1:
                self.sleep(2.0 * (attempt + 1))
                continue
            return Response(r.status_code, r.content, str(r.url))
        with self._lock:
            self.errors += 1
        return None
