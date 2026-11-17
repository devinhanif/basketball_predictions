"""The quote board's selection rule, pure: which stored quotes are served, and the summaries the
provider list and the night's list are built from.

The rule (one quote per line, provider and side):
  * a quote is eligible only when it can be shown to be from before tip-off: it has a snapshot
    time, its stored ``minutes_before_tip`` is not zero or negative, its snapshot is earlier than
    the game's tip-off when the tip-off is known, and at least one of those two facts exists;
  * of the eligible quotes for a line, provider and side, the one with the newest snapshot is
    served; on an equal snapshot the main market's quote comes before an alternate ladder's, a
    usable one before a flagged one, then the export's own row sequence decides;
  * older snapshots are not returned (v1).
Quotes are the ones the store already keeps for each shown prop (the prop's own quote run).
Nothing here compares prices, ranks providers by price, or computes any probability.
"""

from __future__ import annotations

import math
import threading
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from weakref import WeakKeyDictionary

from ..export_store import ExportStore
from ..store import AnyStore
from .catalog import NOT_PROVIDERS

Json = dict[str, Any]
PropKey = tuple[str, int, str]


def _when(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _before_tip(q: Json, tip: datetime | None) -> bool:
    snap = _when(q.get("snapshot_utc"))
    if snap is None:
        return False
    mbt = q.get("minutes_before_tip")
    if mbt is not None and not (isinstance(mbt, int | float) and math.isfinite(mbt) and mbt > 0):
        return False
    if tip is not None:
        if snap.tzinfo is None or tip.tzinfo is None:
            return False  # a time that does not say it is UTC cannot be compared honestly
        if snap >= tip:
            return False
    return mbt is not None or tip is not None


def _implied(values: array[float] | None, i: int, usable: bool) -> float | None:
    if values is None or not usable:
        return None
    v = values[i]
    return v if math.isfinite(v) and 0.0 <= v <= 1.0 else None


def line_text(line: float) -> str:
    return f"{line:g}"


@dataclass(frozen=True)
class Quote:
    """One served quote, exactly the stored fields (an unusable quote carries no price)."""

    provider: str
    side: Literal["over", "under"]
    line: float
    price_american: int | None
    implied_prob_raw: float | None
    snapshot_utc: str
    minutes_before_tip: float | None
    is_alternate: bool
    usable: bool
    unusable_reason: str | None

    def quote_id(self, key: PropKey) -> str:
        gid, pid, stat = key
        return "|".join(
            (gid, str(pid), stat, line_text(self.line), self.provider, self.side, self.snapshot_utc)
        )


def served(quotes: Sequence[Json], implied: array[float] | None, tip_utc: Any) -> list[Quote]:
    """The quotes served for one prop under the rule above, sorted by line, provider id, side."""
    tip = _when(tip_utc)
    if implied is not None and len(implied) != len(quotes):
        implied = None  # not aligned: serve no implied value rather than a wrong one
    best: dict[tuple[float, str, str], tuple[tuple[str, bool, bool], Quote]] = {}
    for i, q in enumerate(quotes):
        provider = str(q["book"])
        side: Literal["over", "under"]
        if q["side"] == "over":
            side = "over"
        elif q["side"] == "under":
            side = "under"
        else:
            continue
        if provider in NOT_PROVIDERS:
            continue
        if not _before_tip(q, tip):
            continue
        usable = not q["unusable"] and q["price"] is not None
        mbt = q.get("minutes_before_tip")
        item = Quote(
            provider=provider,
            side=side,
            line=float(q["line"]),
            price_american=int(q["price"]) if usable else None,
            implied_prob_raw=_implied(implied, i, usable),
            snapshot_utc=str(q["snapshot_utc"]),
            minutes_before_tip=None if mbt is None else float(mbt),
            is_alternate=bool(q["alt"]),
            usable=usable,
            unusable_reason=None if usable else (q.get("reason") or "flagged_unusable"),
        )
        rank = (item.snapshot_utc, not item.is_alternate, item.usable)
        k = (item.line, provider, side)
        cur = best.get(k)
        if cur is None or rank > cur[0]:  # strictly newer/preferred; ties keep the earlier row
            best[k] = (rank, item)
    return [best[k][1] for k in sorted(best, key=lambda k: (k[0], k[1], k[2] != "over"))]


def prop_source(store: AnyStore, key: PropKey) -> tuple[list[Json], array[float] | None]:
    """The stored quotes the store keeps for a shown prop. The fixture replay keeps none."""
    if not isinstance(store, ExportStore):
        return [], None
    return store.quotes.get(key, []), store.quote_implied.get(key)


@dataclass(frozen=True)
class PropSummary:
    providers: frozenset[str]
    n_lines: int


@dataclass(frozen=True)
class Summary:
    """Counts over every served quote of a store, computed once per loaded store."""

    providers: dict[str, tuple[int, str]]  # id -> (n served quotes, newest snapshot)
    props: dict[PropKey, PropSummary]


_cache: WeakKeyDictionary[Any, Summary] = WeakKeyDictionary()
_lock = threading.Lock()


def summary(store: AnyStore) -> Summary:
    """The served-quote counts of this store (cached per store object; a new export is a new store,
    so nothing outlives the data it was read from)."""
    got = _cache.get(store)
    if got is not None:
        return got
    with _lock:
        got = _cache.get(store)
        if got is not None:
            return got
        prov: dict[str, tuple[int, str]] = {}
        props: dict[PropKey, PropSummary] = {}
        keys = list(store.quotes) if isinstance(store, ExportStore) else []
        for key in keys:
            g = store.game(key[0])
            if g is None:
                continue
            quotes, implied = prop_source(store, key)
            items = served(quotes, implied, g.get("tip_utc"))
            if not items:
                continue
            props[key] = PropSummary(
                providers=frozenset(q.provider for q in items),
                n_lines=len({q.line for q in items}),
            )
            for q in items:
                n, newest = prov.get(q.provider, (0, ""))
                prov[q.provider] = (n + 1, max(newest, q.snapshot_utc))
        got = Summary(providers=dict(sorted(prov.items())), props=props)
        _cache[store] = got
        return got
