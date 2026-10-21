# ruff: noqa: E501
"""``line_cal`` and ``line_cal_null_reason`` per prop row (docs/contracts/line_cal_export_v1.md).

The frozen LINE_CALIBRATION map (T493, ``nba.parlay.line_cal``; the parlay producer's ``p_cal``) at the
DraftKings main line. Unlike ``rung_cal`` it has no raw-P(over) >= 0.5 support restriction, so a card
can show a calibrated P(over) at the main line wherever the book has one. Display only, additive,
optional: a row that cannot get the exact inputs gets NULL and a reason (counted in the manifest and
written per row); nothing is approximated.

Exactness: ``p_over_cal`` is ``CalMap.apply("draftkings", stat, p, line, q50, (q75 - q25) / IQR_TO_SD,
starter_rate)`` with ``p = top_source.p_over`` (stored ``p_ge_full`` counts) and ``(q25, q50, q75) =
top_source.quartiles`` (the production row's stored 19-point ``q_grid``), the very functions and inputs the
parlay producer uses for a DraftKings leg (``nba.parlay.top_source.load_legs``), so the card and the parlay
panel cannot disagree on a leg. The persisted 199-point grid is NOT the median/spread source here (its
quantiles differ from the 19-point grid by up to ~0.005); it is used for the same reproduction check as
``rung_cal``: the grid must reproduce the row's stored ``p_ge_full`` exactly (else ``grid_mismatch``).
Built from the production (``props_context_residual``) row only, even when the displayed primary is AVG.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from typing import Any

import numpy as np

from nba.export import rung_cal_fields as rcf
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts

BOOK = rcf.BOOK
MAP_NAME = lc.RULE
REASONS = (
    "no_book_line",
    "ambiguous_main_line",
    "no_production_row",
    "no_production_grid",
    "grid_mismatch",
    "no_survival",
    "no_starter_rate",
    "map_unavailable",
    "no_quartiles",
    "whole_number_line",
)


def attach(
    props: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    grids: dict[tuple[str, int, str], Any] | None = None,
    cmap: lc.CalMap | None = None,
) -> dict[str, Any]:
    """Set ``line_cal`` (dict or None) and ``line_cal_null_reason`` (str or None) on every prop row, in
    place; return the manifest counts. ``grids``: ``reader.prod_grids``."""
    grids = grids or {}
    try:
        m: lc.CalMap | None = cmap if cmap is not None else lc.load_map()
    except (OSError, ValueError, KeyError):
        m = None
    meta = m.meta if m is not None else {}
    sha8 = str(meta.get("map_sha256", ""))[:8] or None
    by_run: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        by_run[(q["run_id"], q["game_id"], int(q["player_id"]), q["stat"])].append(q)
    prod = rcf._production_rows(props)
    why: dict[str, int] = dict.fromkeys(REASONS, 0)
    # ONE value per (game, player, stat), from the PRODUCTION row's own run and made_at (the producer's
    # cutoff: a quote received at or before production's made_at, `<=`), copied to every row of the prop
    done: dict[tuple[str, int, str], tuple[dict[str, Any] | None, str | None]] = {}
    for pk in {(p["game_id"], int(p["player_id"]), p["stat"]) for p in props}:
        done[pk] = _one(prod.get(pk), grids.get(pk), pk, by_run, m, sha8, meta)
    n_cal = 0
    for p in props:
        cal, reason = done[(p["game_id"], int(p["player_id"]), p["stat"])]
        p["line_cal"], p["line_cal_null_reason"] = cal, reason
        if reason:
            why[reason] += 1
        else:
            n_cal += 1
    reasons_d: dict[str, int] = dict.fromkeys(REASONS, 0)
    for _cal, reason in done.values():
        if reason:
            reasons_d[reason] += 1
    return {
        "n_props": len(props), "n_line_cal": n_cal, "null_reasons": why, "map_sha8": sha8,
        "fit_through": meta.get("fit_through"),
        "n_props_distinct": len(done),
        "n_line_cal_distinct": sum(1 for cal, _ in done.values() if cal is not None),
        "null_reasons_distinct": reasons_d,
    }  # fmt: skip


def _one(
    row: dict[str, Any] | None,
    grid: Any,
    pk: tuple[str, int, str],
    by_run: dict[tuple[str, str, int, str], list[dict[str, Any]]],
    m: lc.CalMap | None,
    sha8: str | None,
    meta: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """(line_cal, None) or (None, reason) for one prop, from its production row."""
    if row is None:
        return None, "no_production_row"
    key = (row.get("lines_run_id") or row["run_id"], pk[0], pk[1], pk[2])
    got = rcf.main_line(
        by_run.get(key, []), row["made_at"] + timedelta(microseconds=1), row["tipoff"]
    )
    if isinstance(got, str):
        return None, got
    line, at = got
    if grid is None:
        return None, "no_production_grid"
    counts, n = row.get("p_ge_full_counts") or [], int(row.get("p_ge_full_n") or 0)
    starter = rcf._starter_rate(row)
    if not counts or not n:
        return None, "no_survival"
    if not rcf._survival_matches(np.asarray(grid[2], dtype=float), pk[2], counts, n):
        return None, "grid_mismatch"
    if starter is None:
        return None, "no_starter_rate"
    if m is None or sha8 is None or not m.has(BOOK, pk[2]):
        return None, "map_unavailable"
    qs = ts.quartiles({"pred": _pred(row)})
    po = ts.p_over({"p_ge_full": {"c": counts, "n": n}}, line)
    if qs is None:
        return None, "no_quartiles"
    if po is None:
        return None, "whole_number_line"
    pc = m.apply(BOOK, pk[2], po, line, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, starter)
    return {
        "book": BOOK, "main_line": line, "price_at": rcf._iso_z(at),
        "p_over_raw": po, "p_over_cal": pc, "map": MAP_NAME, "map_sha8": sha8,
        "fit_through": str(meta["fit_through"]),
    }, None  # fmt: skip


def _pred(row: dict[str, Any]) -> dict[str, Any]:
    try:
        out = json.loads(row["prediction_json"])
    except (TypeError, ValueError):
        return {}
    g = out.get("q_grid") if isinstance(out, dict) else None
    if isinstance(out, dict) and isinstance(g, str):  # tolerate a JSON-encoded string
        try:
            out["q_grid"] = json.loads(g)
        except ValueError:
            out["q_grid"] = None
    return out if isinstance(out, dict) else {}
