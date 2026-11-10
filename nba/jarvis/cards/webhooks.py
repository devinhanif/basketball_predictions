"""Team webhook secrets: where they are read from, how they are validated, how they are redacted.

The thirty Discord webhook URLs are secrets. They are read ONLY from one environment variable that
holds a JSON object ``{"LAL": "https://discord.com/api/webhooks/<id>/<secret>", ...}`` or, when
that variable is unset and ``keychain_service`` is configured, from one macOS Keychain generic
password (``security find-generic-password -s <service> -w``) holding the same JSON. Each URL is
checked against the existing webhook pattern (``nba.jarvis.sinks.WEBHOOK``). A URL is never
logged, printed, stored in a file or put in an error: every message here names a team
abbreviation, a count or an exception type, and ``redact_all`` removes every known URL (and its
secret part) from any string before it leaves this process.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from nba.jarvis.cards.colors import ABBRS
from nba.jarvis.sinks import WEBHOOK, redact

log = logging.getLogger("nba.jarvis.cards.webhooks")

KeychainReader = Callable[[str], str | None]


@dataclass
class TeamWebhooks:
    """The teams that have a usable webhook, and an honest account of the ones that do not."""

    urls: dict[str, str] = field(default_factory=dict)  # team abbreviation -> valid URL
    invalid: list[str] = field(default_factory=list)  # teams whose value is not a webhook URL
    unknown: list[str] = field(default_factory=list)  # keys that are not one of the 30 teams
    shared: list[list[str]] = field(default_factory=list)  # teams that share one URL (one channel)
    source: str = "none"  # env | keychain | none
    error: str | None = None  # exception type or short reason, never content

    @property
    def missing(self) -> list[str]:
        return [t for t in ABBRS if t not in self.urls]

    def summary(self) -> str:
        """One redacted line: counts and team names only."""
        bits = [f"{len(self.urls)}/{len(ABBRS)} team webhooks configured (source: {self.source})"]
        if self.invalid:
            bits.append(f"invalid: {','.join(sorted(self.invalid))}")
        if self.unknown:
            bits.append(f"unknown keys: {len(self.unknown)}")
        if self.shared:
            bits.append(f"shared URLs: {len(self.shared)} group(s)")
        if self.error:
            bits.append(f"error: {self.error}")
        return "; ".join(bits)

    def redact(self, text: str) -> str:
        return redact_all(text, self.urls.values())


def redact_all(text: str, urls: object) -> str:
    """Remove every given URL (and its secret part), then any webhook-shaped URL, from ``text``."""
    out = text
    for u in urls:  # type: ignore[attr-defined]
        out = redact(out, str(u))
    return redact(out, None)


def parse_team_webhooks(raw: str | None, source: str) -> TeamWebhooks:
    """JSON text -> validated mapping. Never raises; a parse failure names the exception type."""
    tw = TeamWebhooks(source=source)
    if raw is None or not raw.strip():
        tw.source = "none"
        return tw
    try:
        obj = json.loads(raw)
    except ValueError as exc:  # the message can quote the text: type only
        tw.source, tw.error = "none", f"not JSON ({type(exc).__name__})"
        return tw
    if not isinstance(obj, dict):
        tw.source, tw.error = "none", "not a JSON object of team -> URL"
        return tw
    by_url: dict[str, list[str]] = {}
    for k, v in obj.items():
        team = str(k).strip().upper()
        if team not in ABBRS:
            tw.unknown.append(team[:8])
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            continue  # an empty slot is "not configured", not a failure
        url = v.strip() if isinstance(v, str) else ""
        if not WEBHOOK.match(url):
            tw.invalid.append(team)
            continue
        tw.urls[team] = url
        by_url.setdefault(url, []).append(team)
    tw.shared = [sorted(ts) for ts in by_url.values() if len(ts) > 1]
    return tw


def read_keychain(service: str) -> str | None:  # pragma: no cover - macOS only, not in tests
    """The generic password stored under ``service`` (the Keychain item's account is not used)."""
    if sys.platform != "darwin" or not service:
        return None
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def load_team_webhooks(
    env_var: str,
    *,
    keychain_service: str = "",
    env: Mapping[str, str] | None = None,
    keychain: KeychainReader | None = None,
) -> TeamWebhooks:
    e = env if env is not None else os.environ
    raw = e.get(env_var, "").strip()
    if raw:
        return parse_team_webhooks(raw, "env")
    if keychain_service:
        got = (keychain or read_keychain)(keychain_service)
        if got:
            return parse_team_webhooks(got, "keychain")
    return TeamWebhooks()


def webhook_id(url: str) -> str:
    """The public numeric id of a webhook (the part before the secret); used only as a rate-limit
    bucket key in memory, never displayed."""
    return url.rstrip("/").rsplit("/", 2)[-2]


def fingerprint(url: str) -> str:
    """A short one-way fingerprint stored with a message id so a rotated webhook is noticed (an
    edit through a new webhook cannot reach a message the old one posted)."""
    import hashlib

    return hashlib.sha256(url.encode()).hexdigest()[:12]
