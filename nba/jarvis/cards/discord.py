"""Discord webhook delivery for cards: post once (``?wait=true``), then edit that message.

Only two requests exist here, both against a webhook URL the operator created in his own server:
``POST {webhook}?wait=true`` (returns the message, whose id is kept) and
``PATCH {webhook}/messages/{id}`` (edits it, silently). Neither is a market call. Rate limits are
respected: a 429 waits ``retry_after`` (bounded by a per-tick budget), the per-webhook
``X-RateLimit-Remaining`` / ``Reset-After`` headers are honoured, and anything that would wait past
the budget is DEFERRED to the next tick rather than failed or duplicated. A URL never appears in
a result: errors carry the status code or exception type only.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from nba.jarvis.cards.webhooks import redact_all, webhook_id

SILENT = 1 << 12  # Discord message flag SUPPRESS_NOTIFICATIONS: cards never buzz a phone


@dataclass(frozen=True)
class CardResult:
    ok: bool
    status: str  # posted | edited | gone | deferred | failed
    message_id: str | None = None
    code: int | None = None
    note: str = ""


class RateGate:
    """In-process view of Discord's buckets: one budget of waiting seconds for the whole tick."""

    def __init__(
        self,
        budget_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        total_s: float = 25.0,
    ) -> None:
        self.budget_s = budget_s
        self.deadline = (
            clock() + total_s
        )  # ONE wall-clock budget for the whole pass, network included
        self.broken = (
            False  # circuit: after the first transport error or 5xx, nothing more is tried
        )
        self.spent_s = 0.0
        self._sleep = sleep
        self._clock = clock
        self._not_before: dict[str, float] = {}
        self._global_until = 0.0
        self.n_429 = 0

    def stopped(self) -> bool:
        return self.broken or self._clock() >= self.deadline

    def wait_needed(self, bucket: str) -> float:
        now = self._clock()
        return max(self._not_before.get(bucket, 0.0) - now, self._global_until - now, 0.0)

    def wait(self, seconds: float) -> bool:
        """Sleep if the budget allows; False means 'defer to the next tick'."""
        if seconds <= 0:
            return True
        if self.spent_s + seconds > self.budget_s or self._clock() + seconds >= self.deadline:
            return False
        self._sleep(seconds)
        self.spent_s += seconds
        return True

    def note(self, bucket: str, remaining: int | None, reset_after: float | None) -> None:
        if remaining is not None and remaining <= 0 and reset_after is not None:
            self._not_before[bucket] = self._clock() + reset_after

    def note_429(self, bucket: str, retry_after: float, is_global: bool) -> None:
        self.n_429 += 1
        until = self._clock() + retry_after
        if is_global:
            self._global_until = until
        else:
            self._not_before[bucket] = until


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CardClient:
    """``http`` is an ``httpx.Client`` or a test double with ``request(method, url, json=...)``."""

    def __init__(
        self,
        http: Any | None = None,
        gate: RateGate | None = None,
        *,
        timeout_s: float = 5.0,
        max_tries: int = 3,
        secrets: list[str] | None = None,
    ) -> None:
        self._own = http is None
        self.http: Any = (
            http
            if http is not None
            else httpx.Client(timeout=httpx.Timeout(timeout_s, connect=min(3.0, timeout_s)))
        )
        self.gate = gate or RateGate()
        self.max_tries = max_tries
        self.secrets = list(secrets or [])

    def close(self) -> None:
        if self._own:
            self.http.close()

    # ------------------------------------------------------------------ public
    def post(self, url: str, body: dict[str, Any]) -> CardResult:
        r = self._send("POST", url + "?wait=true", url, {**body, "flags": SILENT})
        if r.ok:
            return CardResult(True, "posted", r.message_id, r.code)
        return r

    def edit(self, url: str, message_id: str, body: dict[str, Any]) -> CardResult:
        r = self._send("PATCH", f"{url}/messages/{message_id}", url, body)
        if r.ok:
            return CardResult(True, "edited", message_id, r.code)
        return r

    # ------------------------------------------------------------------ internals
    def _send(self, method: str, target: str, url: str, body: dict[str, Any]) -> CardResult:
        bucket = webhook_id(url)
        payload = {**body, "allowed_mentions": {"parse": []}}
        for _try in range(self.max_tries):
            if self.gate.stopped():
                return CardResult(False, "deferred", note="card pass stopped for this tick")
            if not self.gate.wait(self.gate.wait_needed(bucket)):
                return CardResult(False, "deferred", note="rate limit wait over the tick budget")
            try:
                resp = self.http.request(method, target, json=payload)
                code = int(resp.status_code)
            except httpx.HTTPError as exc:  # a transport message can carry the URL: type only
                self.gate.broken = True
                return CardResult(False, "deferred", note=f"transport {type(exc).__name__}")
            hdr = getattr(resp, "headers", {}) or {}
            self.gate.note(
                bucket,
                None
                if hdr.get("x-ratelimit-remaining") is None
                else _int(hdr["x-ratelimit-remaining"]),
                _num(hdr.get("x-ratelimit-reset-after")),
            )
            if code in (200, 201, 204):
                return CardResult(True, "ok", _message_id(resp), code)
            if code == 429:
                retry, is_global = _retry(resp)
                self.gate.note_429(bucket, retry, is_global)
                if not self.gate.wait(retry):
                    return CardResult(False, "deferred", code=429, note="rate limited; next tick")
                continue
            if code >= 500:
                self.gate.broken = True
                return CardResult(False, "deferred", code=code, note=f"discord HTTP {code}")
            status = "gone" if code == 404 else "failed"
            return CardResult(
                False,
                status,
                code=code,
                note=redact_all(f"discord HTTP {code}", self.secrets + [url]),
            )
        return CardResult(False, "deferred", code=429, note="rate limited; next tick")


def _int(v: Any) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def _message_id(resp: Any) -> str | None:
    try:
        mid = resp.json().get("id")
    except (ValueError, AttributeError, TypeError):
        return None
    return None if mid is None else str(mid)


def _retry(resp: Any) -> tuple[float, bool]:
    """(seconds to wait, global?) from a 429 body, else its Retry-After header, else 1 s."""
    try:
        j = resp.json()
        ra = _num(j.get("retry_after"))
        glob = bool(j.get("global", False))
    except (ValueError, AttributeError, TypeError):
        ra, glob = None, False
    if ra is None:
        ra = _num((getattr(resp, "headers", {}) or {}).get("retry-after"))
    return (1.0 if ra is None else max(0.0, min(ra, 3600.0))), glob
