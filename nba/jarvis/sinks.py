"""Delivery sinks: Discord webhook (default) and iMessage (optional).

The Discord webhook URL is a secret. It comes ONLY from the environment variable named in
``delivery.discord_env_var`` (``JARVIS_DISCORD_WEBHOOK``), is validated against Discord's webhook
shape, is never logged or stored, and is redacted from every error string (errors carry the
exception type and status code only). The one POST in this package is this outbound message to
the webhook; it is not a market or trading call. Tests inject a fake client and never touch the
network.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from nba.jarvis import imessage
from nba.jarvis.config import JarvisConfig
from nba.jarvis.imessage import SendResult

WEBHOOK = re.compile(
    r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+$"
)
DISCORD_LIMIT = 2000


def webhook_from_env(env_var: str, env: Mapping[str, str] | None = None) -> str | None:
    v = (env if env is not None else os.environ).get(env_var, "").strip()
    return v if WEBHOOK.match(v) else None


def redact(text: str, url: str | None) -> str:
    """Remove the webhook URL (and its token, id, and any webhook-shaped URL) from a string."""
    out = text
    if url:
        out = out.replace(url, "<webhook>")
        out = out.replace(url.rsplit("/", 1)[-1], "<token>")
    return re.sub(r"https://[^\s]*discord[^\s]*/api/webhooks/\S+", "<webhook>", out)


def send_discord(
    text: str,
    *,
    env_var: str,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    client: Any | None = None,
    out: Callable[[str], None] = print,
    timeout_s: float = 15.0,
) -> SendResult:
    if dry_run:
        out("[dry-run] would post to Discord:\n" + text)
        return SendResult(True, "dry_run")
    url = webhook_from_env(env_var, env)
    if url is None:
        return SendResult(False, "failed", f"{env_var} is not set to a Discord webhook URL")
    body = {"content": text[:DISCORD_LIMIT], "allowed_mentions": {"parse": []}}
    own = client is None
    http: Any = client or httpx.Client(timeout=timeout_s)
    try:
        r = http.post(url, json=body)
        code = int(r.status_code)
    except httpx.HTTPError as exc:  # the message of a transport error can carry the URL: type only
        return SendResult(False, "failed", f"discord {type(exc).__name__}")
    finally:
        if own:
            http.close()
    if code in (200, 204):
        return SendResult(True, "sent")
    return SendResult(False, "failed", redact(f"discord HTTP {code}", url))


def make_sender(cfg: JarvisConfig, discord_client: Any | None = None) -> Callable[..., SendResult]:
    """One callable ``(text, *, dry_run, env, out, **_) -> SendResult`` over the configured sinks.
    ok only if every configured sink delivered."""

    def send(
        text: str,
        *,
        dry_run: bool = False,
        env: Mapping[str, str] | None = None,
        out: Callable[[str], None] = print,
        **_: Any,
    ) -> SendResult:
        results = []
        for sink in cfg.sinks:
            if sink == "discord":
                results.append(
                    send_discord(
                        text,
                        env_var=cfg.discord_env_var,
                        dry_run=dry_run,
                        env=env,
                        client=discord_client,
                        out=out,
                    )  # fmt: skip
                )
            elif sink == "imessage":
                results.append(
                    imessage.send(text, env_var=cfg.env_var, dry_run=dry_run, env=env, out=out)
                )
        if not results:
            return SendResult(False, "failed", "no delivery sink configured")
        ok = all(r.ok for r in results)
        note = "; ".join(r.note for r in results if r.note)
        return SendResult(ok, results[0].mode if ok else "failed", note)

    return send
