"""What /api/health adds beside ``status`` and ``mode``: how old the data is and what the export's
own manifest says about its parts, for a system-health strip. Every value is copied from the
manifest the store already verified (or the fixture manifest); nothing is fetched or recomputed
except the age, which is now minus the manifest's ``made_at``.

A refused export never reaches here: the route answers 503 ``export_refused`` and its ``detail``
says which check failed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

Json = dict[str, Any]

#: What the store checked before serving anything (export_store.verify and ExportStore.__init__).
EXPORT_CHECKS = (
    "manifest readable, schema 1 or 2, data_version and made_at present",
    "no season 2025 (holdout) in manifest seasons or games",
    "sha256 of export.duckdb and parlay_of_day.json match the manifest",
    "export.duckdb meta matches the manifest",
)


def _age_s(made_at: str | None, now: datetime) -> float | None:
    if not isinstance(made_at, str):
        return None
    try:
        t = datetime.fromisoformat(made_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None  # a naive stamp cannot be placed in time honestly
    return round((now - t).total_seconds(), 1)


def _pick(d: Any, *keys: str) -> Json | None:
    if not isinstance(d, dict):
        return None
    return {k: d.get(k) for k in keys}


def health_detail(store: Any, now: datetime | None = None) -> Json:
    now = now or datetime.now(UTC)
    m: Json = getattr(store, "manifest", {}) or {}
    if store.mode != "export":
        return {
            "data_made_at": m.get("built_at"),
            "data_age_s": None,  # a frozen replay: its age says nothing about freshness
            "data_version": m.get("source_data_version"),
            "export": None,
        }
    lines = m.get("lines")
    return {
        "data_made_at": m.get("made_at"),
        "data_age_s": _age_s(m.get("made_at"), now),
        "data_version": m.get("data_version"),
        "export": {
            "schema_version": m.get("schema_version"),
            "export_date": m.get("export_date"),
            "seasons": m.get("seasons"),
            "git_sha": None if m.get("git_sha") is None else str(m.get("git_sha"))[:12],
            "git_dirty": m.get("git_dirty"),
            "checks_passed": list(EXPORT_CHECKS),
            "lines": _pick(lines, "status", "n_quotes", "n_unusable"),
            "parlay": _pick(m.get("parlay"), "status", "verdict", "n_evaluated"),
            "primary_sources": m.get("primary_sources"),
            "teammate_sits": _pick(m.get("teammate_sits"), "status", "reason", "n_games"),
            "line_cal": _pick(m.get("line_cal"), "fit_through", "map_sha8", "n_line_cal"),
            "rung_cal": _pick(m.get("rung_cal"), "map_sha8", "n_rung_cal", "source"),
            "excluded": m.get("excluded"),
            "row_counts": m.get("row_counts"),
            "skipped_games": getattr(store, "skipped_games", None),
            "unplaced_players": getattr(store, "unplaced_total", None),
        },
    }
