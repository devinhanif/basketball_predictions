"""What the honesty pages read from the export, captured once while the store loads.

Everything here is a stored field, copied; nothing is fitted, priced or re-derived. Two parts:

* ``capture_records``: for the prop and game-winner rows the store already chose as primary
  (``ExportStore._latest_props`` and ``ExportStore._win_primary``; no second selection), the
  provenance and distribution fields as stored, plus a list of every other stored snapshot for the
  same prop or game (time, lead, model; no numbers).
* ``load_scores``: ``settled_scores`` summed per (model, target, date) and per (arm, comparator,
  target, date) in DuckDB, restricted to the export's forward seasons (never the 2025 holdout)
  and to regular-season games. Only sums and counts leave the database; the scorecard turns them
  into means and date-clustered intervals (scorecard.py).

``settled_scores`` is ``forward_scores`` (nba/export/reader.py:335-352), which
``nba.daily.settle.settle_with_refusals`` fills from the LATEST row per (game, model, target,
player) regardless of lead (nba/daily/settle.py:96-120). The T-60 rule lives in
``forward_scores_elig``, which the export does not carry. Each score row is therefore classed
here by its stored ``made_at`` against the game's stored tip-off: ``eligible`` (made at or before
tip minus 60 minutes), ``late`` (after it; dropped from every mean and counted) or ``unknown``
(the export holds no tip-off for the game: kept, counted and labelled).
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb

from .export_rows import _utc_iso
from .export_verify import HOLDOUT_SEASON

Json = dict[str, Any]
log = logging.getLogger("parlayhitters")

#: What the honesty routes say on the fixture replay, which has none of these tables.
FIXTURE_REASON = (
    "This is the fixture replay: it carries no model stamps or stored snapshots. Point the API at "
    "a nightly export to see them."
)
#: Lead (tip-off minus made_at, minutes) at or above which a row meets the T-60 rule
#: (docs/FORWARD_PREREG_2026_27.md:37-39; nba/export/reader.py:8).
T60_MINUTES = 60.0
WIN_TARGET = "win_prob_home"
PROP_TARGETS = ("pts", "reb", "ast", "fg3m")

#: The stamp fields the exporter copies from forward_model_stamps onto every row
#: (nba/export/reader.py:157-160).
STAMP_KEYS = (
    "weights_sha256", "weights_scope", "data_version", "fit_through", "n_train", "git_sha",
    "git_dirty", "stamped_at",
)  # fmt: skip


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return _utc_iso(v)
    return str(v)


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def forward_seasons(manifest: Json) -> list[int]:
    """The seasons a new aggregation may read: the manifest's own, never the holdout."""
    raw = manifest.get("seasons")
    seasons = raw if isinstance(raw, list) else []
    return sorted({int(s) for s in seasons if int(s) != HOLDOUT_SEASON})


# --------------------------------------------------------------------------- records


def _stamp(r: Json, shared: dict[tuple[Any, ...], Json]) -> Json:
    """The row's stamp fields; identical stamps (one per run and model) share one dict for the
    life of one capture."""
    vals = tuple(
        _iso(r.get(f"stamp_{k}")) if k in ("stamped_at", "fit_through") else r.get(f"stamp_{k}")
        for k in STAMP_KEYS
    )
    hit = shared.get(vals)
    if hit is None:
        hit = shared[vals] = dict(zip(STAMP_KEYS, vals, strict=True))
    return hit


#: A stored snapshot, compact (56k of them on a real export): (made_at ISO, lead_minutes,
#: model_name, version, is_primary, comparison_only, is_latest). Names are interned.
Snap = tuple[str | None, float | None, str, str, bool, bool, bool]
SNAP_FIELDS = (
    "made_at", "lead_minutes", "model_name", "version", "is_primary", "comparison_only",
    "is_latest",
)  # fmt: skip


def _snap(r: Json) -> Snap:
    return (
        _iso(r["made_at"]),
        _f(r.get("lead_minutes")),
        sys.intern(str(r["model_name"])),
        sys.intern(str(r["version"])),
        bool(r.get("is_primary")),
        bool(r.get("comparison_only")),
        bool(r.get("is_latest")),
    )


def _pred_json(r: Json) -> Json:
    raw = r.get("prediction_json")
    if not isinstance(raw, str):
        return {}
    try:
        out = json.loads(raw)
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _prop_record(r: Json, shared: dict[tuple[Any, ...], Json]) -> Json:
    pred = _pred_json(r)
    p_ge_raw = r.get("p_ge")
    try:
        p_ge = json.loads(p_ge_raw) if isinstance(p_ge_raw, str) else None
    except ValueError:
        p_ge = None
    p_ge_uncond = pred.get("p_ge_uncond")
    if isinstance(p_ge_uncond, str):
        try:
            p_ge_uncond = json.loads(p_ge_uncond)
        except ValueError:
            p_ge_uncond = None
    return {
        "run_id": r["run_id"],
        "made_at": _iso(r["made_at"]),
        "tipoff": _iso(r.get("tipoff")),
        "lead_minutes": _f(r.get("lead_minutes")),
        "model_name": r["model_name"],
        "version": r["version"],
        "primary_source": r.get("primary_source"),
        "primary_detail": r.get("primary_detail"),
        "source_table": r.get("source_table"),
        "bundle_id": r.get("bundle_id"),
        "bundle_month": r.get("bundle_month"),
        "bundle_made_at": _iso(r.get("bundle_made_at")),
        "freshness": r.get("freshness"),
        "freshness_reason": r.get("freshness_reason"),
        "stamp": _stamp(r, shared),
        "mean": _f(r.get("mean")),
        "std": _f(r.get("std")),
        "q_grid": [float(v) for v in r["q_grid"]],
        "p_ge_full_counts": None
        if r.get("p_ge_full_counts") is None
        else [int(c) for c in r["p_ge_full_counts"]],
        "p_ge_full_n": None if r.get("p_ge_full_n") is None else int(r["p_ge_full_n"]),
        "p_ge": p_ge if isinstance(p_ge, dict) else None,
        "p_ge_uncond": p_ge_uncond if isinstance(p_ge_uncond, dict) else None,
        "mean_uncond": _f(pred.get("mean_uncond")),
        "p_play_row": _f(r.get("p_play")),
        "p_play_primary": _f(r.get("p_play_primary")),
        "p_play_source": r.get("p_play_source"),
        "proj_minutes": _f(r.get("proj_minutes")),
    }


def _win_record(r: Json, shared: dict[tuple[Any, ...], Json]) -> Json:
    return {
        "run_id": r["run_id"],
        "made_at": _iso(r["made_at"]),
        "tipoff": _iso(r.get("tipoff")),
        "lead_minutes": _f(r.get("lead_minutes")),
        "model_name": r["model_name"],
        "version": r["version"],
        "is_primary": bool(r.get("is_primary")),
        "fallback_reason": r.get("fallback_reason"),
        "stamp": _stamp(r, shared),
    }


@dataclass
class Records:
    props: dict[tuple[str, int, str], Json] = field(default_factory=dict)
    prop_snaps: dict[tuple[str, int, str], list[Snap]] = field(default_factory=dict)
    wins: dict[str, Json] = field(default_factory=dict)
    win_snaps: dict[str, list[Snap]] = field(default_factory=dict)


def _snap_order(s: Snap) -> tuple[str, str]:
    return (str(s[0]), s[2])


def capture_records(
    grids: list[Json],
    props: dict[tuple[str, int, str], Json],
    win_rows: list[Json],
    win_primary: dict[str, Json],
) -> Records:
    """The primary rows' stored fields, and every stored snapshot's time, lead and model."""
    out = Records()
    shared: dict[tuple[Any, ...], Json] = {}
    out.props = {k: _prop_record(r, shared) for k, r in props.items()}
    snaps: dict[tuple[str, int, str], list[Snap]] = defaultdict(list)
    for r in grids:
        key = (str(r["game_id"]), int(r["player_id"]), str(r["stat"]))
        if key in props:
            snaps[key].append(_snap(r))
    out.prop_snaps = {k: sorted(v, key=_snap_order) for k, v in snaps.items()}
    out.wins = {gid: _win_record(r, shared) for gid, r in win_primary.items()}
    wsn: dict[str, list[Snap]] = defaultdict(list)
    for r in win_rows:
        wsn[str(r["game_id"])].append(_snap(r))
    out.win_snaps = {k: sorted(v, key=_snap_order) for k, v in wsn.items()}
    return out


# --------------------------------------------------------------------------- scores

#: (arm, comparator) pairs whose paired per-date sums are read. Which of them may be SHOWN is the
#: scorecard's decision (scorecard.py); here every pair is only summed.
PAIRS: tuple[tuple[str, str], ...] = (
    ("props_context_residual", "props_recency_v1"),
    ("props_context_residual_int", "props_context_residual"),
    ("props_context_residual_lt", "props_context_residual"),
    ("props_context_residual_t15", "props_context_residual"),
    ("props_context_residual_t30", "props_context_residual"),
    ("props_context_residual_late", "props_context_residual"),
    ("rung0_injury_elo", "rung0_mov_elo"),
)


@dataclass
class Scores:
    """Per-date sums. ``single[(model, target)]`` rows: (date, n_scored, n_dnp, n_late,
    n_unknown_t60, sum_crps, n_crps, sum_ll, n_ll, sum_brier, n_brier). ``paired[(arm, cmp,
    target)]`` rows: (date, n_pairs, sum_d_crps, n_d_crps, sum_d_ll, n_d_ll, sum_d_brier,
    n_d_brier). Dates are ISO strings, ascending."""

    available: bool
    reason: str | None
    seasons: list[int]
    single: dict[tuple[str, str], list[tuple[Any, ...]]] = field(default_factory=dict)
    paired: dict[tuple[str, str, str], list[tuple[Any, ...]]] = field(default_factory=dict)
    n_rows_read: int = 0
    n_late: int = 0
    n_unknown_t60: int = 0
    n_games_with_tip: int = 0


_CLASSED = """
WITH tips AS (
    SELECT game_id, max(tipoff) AS tip FROM (
        SELECT game_id, tipoff FROM games WHERE tipoff IS NOT NULL
        UNION ALL SELECT game_id, tipoff FROM prop_grids WHERE tipoff IS NOT NULL
        UNION ALL SELECT game_id, tipoff FROM win_probs WHERE tipoff IS NOT NULL
    ) GROUP BY game_id
),
s AS (
    SELECT s.*, CASE
        WHEN t.tip IS NULL THEN 'unknown'
        WHEN s.made_at <= t.tip - INTERVAL 60 MINUTE THEN 'eligible'
        ELSE 'late' END AS t60
    FROM settled_scores s LEFT JOIN tips t USING (game_id)
    WHERE s.season IN ({marks}) AND s.season <> {holdout} AND s.game_id LIKE '002%'
)
"""


_SINGLE = """
        SELECT model_name, target, CAST(game_date AS VARCHAR) AS d,
               count(*) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               count(*) FILTER (WHERE status = 'dnp'),
               count(*) FILTER (WHERE status = 'scored' AND t60 = 'late'),
               count(*) FILTER (WHERE status = 'scored' AND t60 = 'unknown'),
               sum(crps) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               count(crps) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               sum(log_loss) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               count(log_loss) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               sum(brier) FILTER (WHERE status = 'scored' AND t60 <> 'late'),
               count(brier) FILTER (WHERE status = 'scored' AND t60 <> 'late')
        FROM s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """
_PAIR = """
        , a AS (SELECT * FROM s WHERE status = 'scored' AND t60 <> 'late' AND model_name = ?),
          b AS (SELECT * FROM s WHERE status = 'scored' AND t60 <> 'late' AND model_name = ?)
        SELECT a.target, CAST(a.game_date AS VARCHAR) AS d, count(*),
               sum(a.crps - b.crps), count(a.crps - b.crps),
               sum(a.log_loss - b.log_loss), count(a.log_loss - b.log_loss),
               sum(a.brier - b.brier), count(a.brier - b.brier)
        FROM a JOIN b ON a.game_id = b.game_id AND a.player_id = b.player_id
                     AND a.target = b.target
        GROUP BY 1, 2 ORDER BY 1, 2
    """
_TOTALS = """
        SELECT count(*), count(*) FILTER (WHERE status = 'scored' AND t60 = 'late'),
               count(*) FILTER (WHERE status = 'scored' AND t60 = 'unknown'),
               (SELECT count(*) FROM tips)
        FROM s
    """


def load_scores(con: duckdb.DuckDBPyConnection, have: set[str], manifest: Json) -> Scores:
    """Sums and counts of the stored scores, by date. Absent table: ``available`` False."""
    seasons = forward_seasons(manifest)
    if "settled_scores" not in have:
        return Scores(False, "this export has no settled_scores table", seasons)
    if not seasons:
        return Scores(False, "the manifest names no forward season", seasons)
    cte = _CLASSED.format(marks=",".join("?" for _ in seasons), holdout=int(HOLDOUT_SEASON))
    out = Scores(True, None, seasons)
    try:
        for r in con.execute(cte + _SINGLE, seasons).fetchall():
            out.single.setdefault((str(r[0]), str(r[1])), []).append(tuple(r[2:]))
        for arm, cmp_ in PAIRS:
            for r in con.execute(cte + _PAIR, [*seasons, arm, cmp_]).fetchall():
                out.paired.setdefault((arm, cmp_, str(r[0])), []).append(tuple(r[1:]))
        tot = con.execute(cte + _TOTALS, seasons).fetchone()
    except duckdb.Error as exc:
        log.warning("settled_scores could not be summarised: %s", exc)
        return Scores(False, f"settled_scores could not be read ({type(exc).__name__})", seasons)
    if tot is not None:
        out.n_rows_read, out.n_late, out.n_unknown_t60, out.n_games_with_tip = (
            int(tot[0]), int(tot[1]), int(tot[2]), int(tot[3]),
        )  # fmt: skip
    return out


# --------------------------------------------------------------------------- health strip


@dataclass
class StripFacts:
    newest_quote_by_book: dict[str, str] = field(default_factory=dict)
    quotes_reason: str | None = None
    stale_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    stale_reason: str | None = None


def load_strip(con: duckdb.DuckDBPyConnection, have: set[str]) -> StripFacts:
    """Newest stored quote time per book, and the stored staleness marks on forecast rows."""
    out = StripFacts()
    try:
        if "prop_lines" in have:
            for book, ts in con.execute(
                "SELECT book, max(snapshot_ts) FROM prop_lines WHERE snapshot_ts IS NOT NULL "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall():
                out.newest_quote_by_book[str(book)] = str(_iso(ts))
        else:
            out.quotes_reason = "this export has no prop_lines table"
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'prop_grids'"
            ).fetchall()
        }
        if {"freshness", "freshness_reason"} <= cols:
            for model, reason, n in con.execute(
                "SELECT model_name, coalesce(freshness_reason, 'unstated'), count(*) "
                "FROM prop_grids WHERE freshness = 'stale' GROUP BY 1, 2 ORDER BY 1, 2"
            ).fetchall():
                out.stale_by_model.setdefault(str(model), {})[str(reason)] = int(n)
        else:
            out.stale_reason = "prop_grids has no freshness column in this export"
    except duckdb.Error as exc:
        log.warning("health strip facts could not be read: %s", exc)
        return StripFacts(quotes_reason=f"could not be read ({type(exc).__name__})",
                          stale_reason=f"could not be read ({type(exc).__name__})")  # fmt: skip
    return out
