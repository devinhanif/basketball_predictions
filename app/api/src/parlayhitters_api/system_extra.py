"""The error-code registry as served JSON, and the keys /api/health adds for the system strip
(newest stored quote per book, stored stale marks, and what the strip wants but the export lacks).
Contracts: docs/contracts/errors_v1.md, health_strip_v1.md.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from .errors import REGISTRY_PATH
from .honesty_data import StripFacts

Json = dict[str, Any]


@cache
def error_registry() -> Json:
    """The shared registry file as it is on disk (both sides), so help and API cannot drift."""
    data: Json = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {
        "version": data["version"],
        "note": data["note"],
        "codes": [
            {k: c[k] for k in ("code", "status", "side", "title", "what", "why", "todo")}
            for c in data["codes"]
        ],
    }


STRIP_MISSING_ALWAYS = (
    "odds capture status per book (whether the capture ran and succeeded): not in the export",
    "per-row stale_model flag on production rows: not in the export (only AVG rows carry "
    "freshness)",
)


def strip_fields(strip: StripFacts | None, manifest: Json) -> Json:
    """Keys added to /api/health. Stored timestamps and counts only."""
    if strip is None:
        return {
            "newest_quote": None,
            "stale_marks": None,
            "strip_missing": ["fixture replay: no export facts", *STRIP_MISSING_ALWAYS],
        }
    by_book = dict(strip.newest_quote_by_book)
    missing = list(STRIP_MISSING_ALWAYS)
    if strip.quotes_reason:
        missing.append(f"newest quote per book: {strip.quotes_reason}")
    if "pinnacle" not in by_book:
        missing.append("Pinnacle: no Pinnacle quote is stored in this export")
    if strip.stale_reason:
        missing.append(f"stale marks: {strip.stale_reason}")
    ps = manifest.get("primary_sources")
    return {
        "newest_quote": {
            "label": "newest quote in this export",
            "by_book": by_book,
            "pinnacle": by_book.get("pinnacle"),
            "source": "max(prop_lines.snapshot_ts) per book; stored times, not odds capture status",
        },
        "stale_marks": {
            "source": (
                "prop_grids rows with freshness = 'stale' (nba/export/best_model.py:76-87; "
                "'writer_flag' is the writer's stale_model flag)"
            ),
            "by_model": strip.stale_by_model,
            "primary_fallback_stale": None
            if not isinstance(ps, dict)
            else ps.get("fallback_stale"),
        },
        "strip_missing": missing,
    }
