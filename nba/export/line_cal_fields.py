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
    n_cal = 0
    for p in props:
        p["line_cal"], p["line_cal_null_reason"] = None, None
        key = (p.get("lines_run_id") or p["run_id"], p["game_id"], int(p["player_id"]), p["stat"])
        got = rcf.main_line(by_run.get(key, []), p["made_at"], p["tipoff"])
        if isinstance(got, str):
            p["line_cal_null_reason"] = got
            why[got] += 1
            continue
        line, at = got
        pk = (p["game_id"], int(p["player_id"]), p["stat"])
        row, grid = prod.get(pk), grids.get(pk)
        reason, po, qs, starter = None, None, None, None
        if row is None:
            reason = "no_production_row"
        elif grid is None:
            reason = "no_production_grid"
        else:
            counts, n = row.get("p_ge_full_counts") or [], int(row.get("p_ge_full_n") or 0)
            starter = rcf._starter_rate(row)
            if not counts or not n:
                reason = "no_survival"
            elif not rcf._survival_matches(np.asarray(grid[2], dtype=float), p["stat"], counts, n):
                reason = "grid_mismatch"
            elif starter is None:
                reason = "no_starter_rate"
            elif m is None or sha8 is None or not m.has(BOOK, p["stat"]):
                reason = "map_unavailable"
            else:
                pred = _pred(row)
                qs = ts.quartiles({"pred": pred})
                po = ts.p_over({"p_ge_full": {"c": counts, "n": n}}, line)
                if qs is None:
                    reason = "no_quartiles"
                elif po is None:
                    reason = "whole_number_line"
        if reason:
            p["line_cal_null_reason"] = reason
            why[reason] += 1
            continue
        assert m is not None and po is not None and qs is not None and starter is not None
        pc = m.apply(BOOK, p["stat"], po, line, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, starter)
        p["line_cal"] = {
            "book": BOOK, "main_line": line, "price_at": rcf._iso_z(at),
            "p_over_raw": po, "p_over_cal": pc, "map": MAP_NAME, "map_sha8": sha8,
            "fit_through": str(meta["fit_through"]),
        }  # fmt: skip
        n_cal += 1
    return {
        "n_props": len(props), "n_line_cal": n_cal, "null_reasons": why, "map_sha8": sha8,
        "fit_through": meta.get("fit_through"),
    }  # fmt: skip


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
