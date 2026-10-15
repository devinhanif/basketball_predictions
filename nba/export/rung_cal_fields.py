# ruff: noqa: E501
"""``book_main_line`` and ``rung_cal`` per prop row (docs/contracts/rung_cal_export_v1.md).

Display only, additive, optional: a row that cannot get the exact inputs gets NULL and the reason is
counted in the manifest; nothing is approximated. The frozen CAL-rung map (``nba.parlay.rung_cal``,
map_sha8 116f4480, DraftKings only) needs the continuous 199-point grid's median and spread. That grid
is held only for AVG rows (``forward_avg_shadow.avg_pmf199``, carried here as ``_pmf199``); a production
context-residual row stores only the integer survival (``p_ge_full``) and a 19-point quantile grid, from
which the harness's median and spread cannot be recovered exactly, so those rows get ``rung_cal`` NULL
(reason ``no_continuous_grid``).

``book_main_line``: the newest two-sided (over and under) non-alternate DraftKings quote of the row's
slate run, received strictly before the row's ``made_at`` and the tip. ``starter10`` is the production
row's stored ``starter_rate`` (as ``nba.parlay.top_source`` reads it); without it the field is NULL.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from nba.parlay import rung_cal as rc

BOOK = rc.BOOK
PRODUCTION_PROP_MODEL = "props_context_residual"
REASONS = (
    "no_book_line",
    "ambiguous_main_line",
    "no_continuous_grid",
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


def _starter_rates(props: list[dict[str, Any]]) -> dict[tuple[str, int, str], float]:
    """Newest production row's stored ``starter_rate`` per (game, player, stat)."""
    newest: dict[tuple[str, int, str], tuple[datetime, str, float | None]] = {}
    for p in props:
        if p["model_name"] != PRODUCTION_PROP_MODEL or p.get("comparison_only"):
            continue
        try:
            sr = json.loads(p["prediction_json"]).get("starter_rate")
        except (TypeError, ValueError, AttributeError):
            sr = None
        k = (p["game_id"], int(p["player_id"]), p["stat"])
        if k not in newest or (p["made_at"], p["run_id"]) > newest[k][:2]:
            newest[k] = (p["made_at"], p["run_id"], None if sr is None else float(sr))
    return {k: v[2] for k, v in newest.items() if v[2] is not None}


def attach(
    props: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    rmap: rc.RungCalMap | None = None,
) -> dict[str, Any]:
    """Set ``book_main_line`` and ``rung_cal`` (a dict or None) on every prop row, in place; drop the
    private ``_pmf199``; return the counts for the manifest."""
    m = rmap if rmap is not None else rc.load_map()
    by_run: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        by_run[(q["run_id"], q["game_id"], int(q["player_id"]), q["stat"])].append(q)
    starter = _starter_rates(props)
    why: dict[str, int] = dict.fromkeys(REASONS, 0)
    n_main = n_cal = n_lines = 0
    cal_counts: dict[str, int] = {}
    for p in props:
        pmf = p.pop("_pmf199", None)
        p["book_main_line"], p["rung_cal"] = None, None
        key = (p.get("lines_run_id") or p["run_id"], p["game_id"], int(p["player_id"]), p["stat"])
        got = main_line(by_run.get(key, []), p["made_at"], p["tipoff"])
        if isinstance(got, str):
            why[got] += 1
            continue
        line, at = got
        p["book_main_line"] = line
        n_main += 1
        counts: list[int] | None = p.get("p_ge_full_counts")
        n: int | None = p.get("p_ge_full_n")
        reason = None
        if pmf is None:
            reason = "no_continuous_grid"
        elif not counts or not n or not isinstance(counts, list):
            reason = "no_survival"
        elif (p["game_id"], int(p["player_id"]), p["stat"]) not in starter:
            reason = "no_starter_rate"
        elif not m.has(p["stat"]):
            reason = "no_map"
        if reason:
            why[reason] += 1
            continue
        assert counts is not None and n is not None  # narrowed by the reason checks above
        med, spr = rc.grid_med_spr(np.asarray(pmf, dtype=float))
        lines = rc.rung_cal_lines(
            np.asarray(counts, dtype=float) / float(n),
            line,
            starter[(p["game_id"], int(p["player_id"]), p["stat"])],
            p["stat"],
            med,
            spr,
            rmap=m,
            counts=cal_counts,
        )
        if lines is None:
            why["dropped_nonmonotone"] += 1  # the only None left once the map and survival exist
            continue
        lines = [x for x in lines if _in_support(x, counts, n)]
        if not lines:
            why["no_lines_in_support"] += 1
            continue
        p["rung_cal"] = {
            "book": BOOK, "main_line": line, "price_at": _iso_z(at),
            "map_version": rc.MAP_VERSION, "map_sha8": m.map_sha8, "lines": lines,
        }  # fmt: skip
        n_cal += 1
        n_lines += len(lines)
    return {
        "n_props": len(props), "n_book_main_line": n_main, "n_rung_cal": n_cal,
        "n_rung_cal_lines": n_lines, "null_reasons": why, "map_sha8": m.map_sha8,
    }  # fmt: skip


def _in_support(entry: dict[str, float], counts: list[int], n: int) -> bool:
    """Raw model P(over) at the line (P(Y >= line + 0.5)) >= the map's fit-support floor."""
    k = int(round(entry["line"] + 0.5))
    raw = counts[k - 1] / n if k <= len(counts) else 0.0
    return raw >= rc.FIT_SUPPORT_MIN_P
