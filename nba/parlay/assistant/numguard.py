"""Numbers-only-from-the-engine post-check for model prose.

Collect every number in the tool results (plus numbers the user typed and tool arguments),
then scan the prose: a number-looking token that no engine number reproduces at the token's
own precision is replaced by ``REMOVED``. Dates, tickers, and tiny counts (0-10, "3 legs")
are exempt; so are 4-digit years.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REMOVED = "[number removed: not from engine]"
RECO_REMOVED = "[recommendation removed: analysis only, never a betting recommendation]"
_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\$?\d[\d,]*(?:\.\d+)?(?:%|¢)?(?![A-Za-z0-9_])")
_SKIP = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\bKX[A-Z0-9]+(?:-[A-Za-z0-9]+)*|\b\d{6,}\b")
_RECO = re.compile(
    r"[^.!?\n]*\b(?:you should (?:bet|buy|take|place|wager)|i (?:recommend|suggest) "
    r"(?:you )?(?:bet|buy|plac|tak|wager)\w*|(?:place|make|take) (?:this|the|a|that) "
    r"(?:bet|wager|parlay)|(?:bet|wager) \$)[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)


def collect_numbers(obj: Any, out: list[float] | None = None) -> list[float]:
    """All int/float values (not bools) found anywhere inside ``obj``."""
    acc: list[float] = [] if out is None else out
    if isinstance(obj, bool):
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


def _matches(tok: str, nums: Iterable[float]) -> bool:
    body = tok.lstrip("+-").lstrip("$").replace(",", "")
    pct = body.endswith("%")
    cents = body.endswith("¢")
    body = body.rstrip("%¢")
    v = float(body)
    dec = len(body.split(".")[1]) if "." in body else 0
    half = 0.5 * 10**-dec + 1e-9
    for x in nums:
        ax = abs(x)
        if pct or cents:
            if abs(ax * 100 - v) <= half:
                return True
        elif dec == 0:  # bare integers must match exactly (counts, thresholds, n)
            if abs(ax - v) < 1e-9 or abs(ax * 100 - v) < 1e-9:
                return True
        elif abs(ax - v) <= half:
            return True
    return False


def check_prose(prose: str, allowed: Iterable[float]) -> tuple[str, int]:
    """Return (prose with foreign numbers/recommendations stripped, count removed)."""
    nums = list(allowed)
    skip = [m.span() for m in _SKIP.finditer(prose)]
    removed = 0

    def fix(m: re.Match[str]) -> str:
        nonlocal removed
        if any(a <= m.start() < b for a, b in skip):
            return m.group(0)
        tok = m.group(0)
        plain = tok.lstrip("+-")
        if re.fullmatch(r"\d{1,2}", plain) and int(plain) <= 10:
            return tok
        if re.fullmatch(r"(19|20|21)\d{2}", plain):
            return tok
        if _matches(tok, nums):
            return tok
        removed += 1
        return REMOVED

    out = _TOKEN.sub(fix, prose)

    def reco(m: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        return RECO_REMOVED

    return _RECO.sub(reco, out), removed
