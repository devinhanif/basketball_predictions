"""Card -> Discord embed, inside Discord's limits.

Limits (Discord API reference, embed object): title 256, description 4096, at most 25 fields, field
name 256, field value 1024, footer text 2048, and 6000 characters across title, description, field
names and values, and footer. ``enforce`` trims to those limits (long values are cut at a line
boundary with a "+N more lines" note; the lowest-priority fields are dropped last-first), so no
card can be rejected for size.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from nba.jarvis.cards import text as T
from nba.jarvis.cards.colors import color
from nba.jarvis.cards.model import CardData

TITLE_MAX, DESC_MAX, FIELDS_MAX = 256, 4096, 25
NAME_MAX, VALUE_MAX, FOOTER_MAX, TOTAL_MAX = 256, 1024, 2048, 6000
HASH_FOOTER = "<as-of>"


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _cut_lines(value: str, n: int) -> str:
    """Cut at a line boundary, saying how many lines were left out."""
    if len(value) <= n:
        return value
    lines = value.split("\n")
    kept: list[str] = []
    for i, ln in enumerate(lines):
        note = f"\n(+{len(lines) - i} more lines)"
        if len("\n".join([*kept, ln])) + len(note) > n:
            return _cut("\n".join(kept) + note if kept else ln, n)
        kept.append(ln)
    return _cut("\n".join(kept), n)


def total_chars(e: dict[str, Any]) -> int:
    n = len(e.get("title", "")) + len(e.get("description", ""))
    n += sum(len(f["name"]) + len(f["value"]) for f in e.get("fields", []))
    n += len(e.get("footer", {}).get("text", ""))
    return n


def enforce(e: dict[str, Any]) -> dict[str, Any]:
    e["title"] = _cut(e.get("title", ""), TITLE_MAX)
    e["description"] = _cut(e.get("description", ""), DESC_MAX)
    e["footer"] = {"text": _cut(e.get("footer", {}).get("text", ""), FOOTER_MAX)}
    fields = [
        {
            "name": _cut(f["name"], NAME_MAX) or "-",
            "value": _cut_lines(f["value"], VALUE_MAX) or "-",
            "inline": False,
        }
        for f in e.get("fields", [])[:FIELDS_MAX]
    ]
    e["fields"] = fields
    while total_chars(e) > TOTAL_MAX and len(e["fields"]) > 1:
        e["fields"].pop()  # lowest priority is last
    for _ in range(4):  # still too long with one field left: shrink description, field, footer
        over = total_chars(e) - TOTAL_MAX
        if over <= 0:
            break
        d = e["description"]
        take = min(over, max(0, len(d) - 200))
        e["description"] = _cut(d, len(d) - take) if take else d
        over -= take
        if over > 0 and e["fields"]:
            v = e["fields"][0]["value"]
            take = min(over, max(0, len(v) - 100))
            e["fields"][0]["value"] = _cut_lines(v, len(v) - take) if take else v
            over -= take
        if over > 0:
            t = e["footer"]["text"]
            e["footer"] = {"text": _cut(t, max(40, len(t) - over))}
    return e


def build_embed(
    card: CardData, channel_team: str, style: T.Style, tip_in: str = ""
) -> dict[str, Any]:
    """Fields in priority order: overs, halftime check, what the game knows, recap (final)."""
    fields: list[dict[str, Any]] = []
    for name_value in (
        T.overs_field(card, style),
        T.half_field(card, style),
        T.recap_field(card),
        T.knows_field(card, style, channel_team),
    ):
        if name_value is not None:
            fields.append({"name": name_value[0], "value": name_value[1], "inline": False})
    desc = T.status_line(card, tip_in)
    if card.alert_note:
        desc += f"\n🔔 {card.alert_note}"
    if (
        card.feed_age_min is not None
        and card.feed_age_min > 6.0
        and card.phase in ("live", "halftime")
    ):
        desc += f"\n(live feed is {card.feed_age_min:.0f} min old: treat the score as stale)"
    e = {
        "title": T.title(card, style),
        "description": desc,
        "color": color(channel_team),
        "fields": fields,
        "footer": {"text": T.footer(card, style)},
    }
    return enforce(e)


AGE = re.compile(r"\d+ min (?:old|ago)")


def content_hash(embed: dict[str, Any]) -> str:
    """Hash of what the card SAYS, so it is edited only when something changed. The as-of footer
    and every "N min old / ago" age are left out: they tick every minute and are not news."""
    body = {**embed, "footer": {"text": HASH_FOOTER}}
    blob = AGE.sub("<age>", json.dumps(body, sort_keys=True))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
