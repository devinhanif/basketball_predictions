# ruff: noqa: E501
"""``book_main_line`` and ``rung_cal`` per prop row (docs/contracts/rung_cal_export_v1.md).

Display only, additive, optional: a row that cannot get the exact inputs gets NULL and the reason is
counted in the manifest; nothing is approximated. The frozen CAL-rung map (``nba.parlay.rung_cal``,
map_sha8 116f4480, DraftKings only) is a map of the PRODUCTION model's P(over), so ``rung_cal`` is built
only from the production (``props_context_residual``) row of the prop: its persisted continuous
199-point grid (``forward_prop_grid199``; median and spread via ``grid_med_spr``), its ``p_ge_full``
survival and its stored ``starter_rate``, plus the DraftKings main line. This holds even when the
displayed primary is an AVG row; AVG grids are never used. The grid must reproduce the row's stored
``p_ge_full`` exactly (else ``grid_mismatch``); a recency-routed row or a run before the grid was
persisted has no grid (``no_production_grid``).

``book_main_line``: the newest two-sided (over and under) non-alternate DraftKings quote of the row's
slate run, received strictly before the row's ``made_at`` and the tip.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from nba.parlay import rung_cal as rc
from nba.props import full_support

BOOK = rc.BOOK
PRODUCTION_PROP_MODEL = "props_context_residual"
SOURCE = PRODUCTION_PROP_MODEL
REASONS = (
    "no_book_line",
    "ambiguous_main_line",
    "no_production_row",
    "no_production_grid",
    "grid_mismatch",
    "no_survival",
    "no_starter_rate",
    "no_map",
    "dropped_nonmonotone",
    "no_lines_in_support",
)


def _iso_z(ts: datetime) -> str:
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def main_line(
    quotes: list[dict[str, Any]], made_at: datetime, tipoff: datetime
) -> tuple[float, datetime] | str:
    """(line, pair timestamp) of the newest pre-tip two-sided DraftKings main line, or a reason."""
    sides: dict[float, dict[str, datetime]] = defaultdict(dict)
    for q in quotes:
        if q["book"] != BOOK or q["is_alternate"] or q["unusable"]:
            continue
        ts = q["snapshot_ts"]
        if not (ts < made_at and ts < tipoff) or q["side"] not in ("over", "under"):
            continue
        cur = sides[float(q["line"])].get(q["side"])
        if cur is None or ts > cur:
            sides[float(q["line"])][q["side"]] = ts
    pairs = {ln: min(s["over"], s["under"]) for ln, s in sides.items() if len(s) == 2}
    if not pairs:
        return "no_book_line"
    best = max(pairs.values())
    at_best = [ln for ln, ts in pairs.items() if ts == best]
    return (at_best[0], best) if len(at_best) == 1 else "ambiguous_main_line"


def _production_rows(props: list[dict[str, Any]]) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Newest pre-tip production (context-residual, non-comparison) row per (game, player, stat)."""
    newest: dict[tuple[str, int, str], dict[str, Any]] = {}
    for p in props:
        if p["model_name"] != PRODUCTION_PROP_MODEL or p.get("comparison_only"):
            continue
        k = (p["game_id"], int(p["player_id"]), p["stat"])
        if k not in newest or (p["made_at"], p["run_id"]) > (
            newest[k]["made_at"],
            newest[k]["run_id"],
        ):
            newest[k] = p
    return newest


def _starter_rate(row: dict[str, Any]) -> float | None:
    try:
        sr = json.loads(row["prediction_json"]).get("starter_rate")
    except (TypeError, ValueError, AttributeError):
        return None
    return None if sr is None else float(sr)


def _survival_matches(grid: np.ndarray, stat: str, counts: list[int], n: int) -> bool:
    got = full_support.from_samples(grid, stat)
    return bool(got["n"] == n and list(got["c"]) == [int(c) for c in counts])


def attach(
    props: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    grids: dict[tuple[str, int, str], tuple[datetime, str, list[float]]] | None = None,
    rmap: rc.RungCalMap | None = None,
) -> dict[str, Any]:
    """Set ``book_main_line`` and ``rung_cal`` (a dict or None) on every prop row, in place; return the
    counts for the manifest. ``grids``: ``reader.prod_grids`` (production's persisted q199)."""
    m = rmap if rmap is not None else rc.load_map()
    grids = grids or {}
    by_run: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        by_run[(q["run_id"], q["game_id"], int(q["player_id"]), q["stat"])].append(q)
    prod = _production_rows(props)
    why: dict[str, int] = dict.fromkeys(REASONS, 0)
    n_main = n_cal = n_lines = 0
    for p in props:
        p["book_main_line"], p["rung_cal"] = None, None
        key = (p.get("lines_run_id") or p["run_id"], p["game_id"], int(p["player_id"]), p["stat"])
        got = main_line(by_run.get(key, []), p["made_at"], p["tipoff"])
        if isinstance(got, str):
            why[got] += 1
            continue
        line, at = got
        p["book_main_line"] = line
        n_main += 1
        pk = (p["game_id"], int(p["player_id"]), p["stat"])
        row = prod.get(pk)
        grid = grids.get(pk)
        reason = None
        counts: list[int] = []
        n = 0
        starter = None
        if row is None:
            reason = "no_production_row"
        elif grid is None:
            reason = "no_production_grid"
        else:
            counts, n = row.get("p_ge_full_counts") or [], int(row.get("p_ge_full_n") or 0)
            starter = _starter_rate(row)
            if not counts or not n:
                reason = "no_survival"
            elif not _survival_matches(np.asarray(grid[2], dtype=float), p["stat"], counts, n):
                reason = "grid_mismatch"
            elif starter is None:
                reason = "no_starter_rate"
            elif not m.has(p["stat"]):
                reason = "no_map"
        if reason:
            why[reason] += 1
            continue
        assert grid is not None and starter is not None  # narrowed by the reason checks above
        med, spr = rc.grid_med_spr(np.asarray(grid[2], dtype=float))
        c: dict[str, int] = {}
        lines = rc.rung_cal_lines(
            np.asarray(counts, dtype=float) / float(n), line, starter, p["stat"], med, spr,
            rmap=m, counts=c,
        )  # fmt: skip
        if lines is None:  # served lines only: none in the fit support, or not monotone
            why["dropped_nonmonotone" if c["dropped_nonmonotone"] else "no_lines_in_support"] += 1
            continue
        p["rung_cal"] = {
            "book": BOOK, "main_line": line, "price_at": _iso_z(at), "source": SOURCE,
            "map_version": rc.MAP_VERSION, "map_sha8": m.map_sha8, "lines": lines,
        }  # fmt: skip
        n_cal += 1
        n_lines += len(lines)
    return {
        "n_props": len(props), "n_book_main_line": n_main, "n_rung_cal": n_cal,
        "n_rung_cal_lines": n_lines, "null_reasons": why, "map_sha8": m.map_sha8,
        "source": SOURCE,
    }  # fmt: skip
