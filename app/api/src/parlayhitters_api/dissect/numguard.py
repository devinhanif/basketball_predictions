"""Numbers-only-from-the-packet check for written text.

Every number token in a piece of text must be reproduced by some number in the fact packet, at the
precision the token is written in:

* a plain integer ("7", "10") equals a packet number exactly;
* a decimal ("24.5") equals a packet number within half a unit of its last digit;
* a percentage ("70%", "61.5%") equals a packet number times 100, at its own precision.

There are no exemptions for small numbers: a "3" that no packet field carries is rejected. Text
copied verbatim from a packet string (a player name such as "Player 990001", a label such as
"3-pointers", a date) is not read as a number. A text that fails is rejected whole, never
patched, so a reader never sees a half-cleaned sentence.

Also rejected: betting recommendations and hype words (docs/APP_CONTENT_SPEC.md section 3).
This mirrors the idea of nba/parlay/assistant/numguard.py but is its own code: app/ never imports
nba/ beyond nba.export.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])")
_NUMERIC_ONLY = re.compile(r"[\s\d.,:%+\-$]*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_BANNED = re.compile(
    r"\b(?:you should (?:bet|take|place|play|back)|we recommend|i recommend|best bet|"
    r"lock(?:s|ed)?|guarantee[ds]?|free money|easy money|can'?t lose|sure thing|"
    r"hammer (?:it|the)|must[- ]bet|fade the public)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    checked: int
    unmatched: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)

    def reason(self) -> str:
        parts: list[str] = []
        if self.unmatched:
            parts.append("number not in the packet: " + ", ".join(self.unmatched))
        if self.banned:
            parts.append("banned phrase: " + ", ".join(self.banned))
        return "; ".join(parts)


def collect_numbers(obj: Any, out: list[float] | None = None) -> list[float]:
    """Every int/float leaf inside ``obj`` (booleans excluded)."""
    acc: list[float] = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return acc
    if isinstance(obj, int | float):
        if obj == obj and abs(obj) != float("inf"):
            acc.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, acc)
    elif isinstance(obj, list | tuple):
        for v in obj:
            collect_numbers(v, acc)
    return acc


def collect_strings(obj: Any, out: list[str] | None = None) -> list[str]:
    """Every non-numeric string leaf inside ``obj``, longest first (names, labels, dates)."""
    acc: list[str] = [] if out is None else out
    if isinstance(obj, str):
        if len(obj) >= 3 and (_ISO_DATE.fullmatch(obj) or not _NUMERIC_ONLY.fullmatch(obj)):
            acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_strings(v, acc)
    elif isinstance(obj, list | tuple):
        for v in obj:
            collect_strings(v, acc)
    return sorted(set(acc), key=lambda s: (-len(s), s))


def _decimals(body: str) -> int:
    return len(body.split(".")[1]) if "." in body else 0


def _matches(token: str, nums: Iterable[float]) -> bool:
    body = token.lstrip("+-").lstrip("$").replace(",", "")
    pct = body.endswith("%")
    body = body.rstrip("%")
    value = float(body)
    dec = _decimals(body)
    half = 0.5 * 10**-dec + 1e-9
    for x in nums:
        ax = abs(x)
        if pct:
            if abs(ax * 100 - value) <= half:
                return True
        elif dec == 0:  # a bare integer must be a packet number exactly
            if abs(ax - value) < 1e-9:
                return True
        elif abs(ax - value) <= half:
            return True
    return False


def check_text(text: str, packet: dict[str, Any]) -> GuardResult:
    """Check ``text`` against ``packet``; ok only if every number token is a packet number."""
    nums = collect_numbers(packet)
    masked = text
    for s in collect_strings(packet):
        masked = masked.replace(s, " " * len(s))  # a copied name or date is not a number
    unmatched: list[str] = []
    checked = 0
    for m in _TOKEN.finditer(masked):
        checked += 1
        if not _matches(m.group(0), nums):
            unmatched.append(text[m.start() : m.end()])
    banned = [m.group(0) for m in _BANNED.finditer(text)]
    return GuardResult(
        ok=not unmatched and not banned, checked=checked, unmatched=unmatched, banned=banned
    )
