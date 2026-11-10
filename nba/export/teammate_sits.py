# ruff: noqa: E501
"""Tonight's "if a teammate sits" scenario rows for the nightly export (docs/contracts/teammate_sits_v1.md).

Read-only against ``nba.duckdb`` and the production model cache; nothing is refitted. For each regular
starter on a game's roster who is not OUT on the latest usable report (one sitter at a time) and each
teammate with a production forecast, the production heads are re-run with the sitter added to the OUT
set, through ``vacated_features`` exactly as for a real OUT player (``nba.export.sits_core``, equal to
``research/teammate_sits/scenario.py``). Baseline and scenario come from the SAME recompute (same report,
same cached heads), so a row never mixes runs; the stored production rows are only compared against it
(``base_vs_production`` in the manifest), never substituted.

Which stats ride (decided from the frozen rule and its attack, T526 / T526-a): points and rebounds only.
Assists (INCONCLUSIVE) and threes (UNRESOLVED) get no word, so no row. ``RULE_WORDS`` carries the
frozen words per stat.

Failure is isolated: ``compute`` never raises. Any problem returns no rows and a ``reason`` the
manifest carries, and the export carries on without the table; the app says scenarios are absent.
"""

from __future__ import annotations

import json
import logging
import math
import pickle
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.export import sits_core

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
#: TEAMMATE_SITS frozen rule (docs/prereg/TEAMMATE_SITS.md), body sha256 first 8.
RULE_SHA8 = "c23f7b36"
#: The stat words recorded in T526 / T526-a. Only stats whose word allows a display are exported.
RULE_WORDS: dict[str, str] = {
    "pts": "HELPS",
    "reb": "HELPS",
    "ast": "INCONCLUSIVE",
    "fg3m": "UNRESOLVED",
}
EXPORT_STATS: tuple[str, ...] = ("pts", "reb")
#: Hard ceiling on rows per night (contract: median ~2,000 with four stats, ~5,000 at the largest slate;
#: two stats carry about half). Past it the table is withheld rather than truncated silently.
MAX_ROWS = 12_000
#: Wall-clock allowance for the whole scenario step; checked between stages (a stage is not interrupted).
DEFAULT_BUDGET_S = 150.0
DEFAULT_MODEL_ROOT = Path("data/models/context_residual")

COLUMNS: list[tuple[str, str]] = [
    ("schema_version", "INTEGER"),
    ("made_at", "TIMESTAMP"),
    ("game_id", "VARCHAR"),
    ("team_id", "BIGINT"),
    ("sitter_id", "BIGINT"),
    ("sitter_status", "VARCHAR"),
    ("player_id", "BIGINT"),
    ("stat", "VARCHAR"),
    ("base_mean", "DOUBLE"),
    ("scn_mean", "DOUBLE"),
    ("delta_mean", "DOUBLE"),
    ("base_q10", "DOUBLE"),
    ("base_q50", "DOUBLE"),
    ("base_q90", "DOUBLE"),
    ("scn_q10", "DOUBLE"),
    ("scn_q50", "DOUBLE"),
    ("scn_q90", "DOUBLE"),
    ("p_play", "DOUBLE"),
    ("rule_word", "VARCHAR"),
]


@dataclass
class SitsResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


def _absent(reason: str, t0: float, clock: Callable[[], float], **extra: Any) -> SitsResult:
    return SitsResult(
        [],
        {
            "status": "absent",
            "reason": reason,
            "n_rows": 0,
            "n_scenarios": 0,
            "n_games": 0,
            "rule_sha8": RULE_SHA8,
            "stats": list(EXPORT_STATS),
            "seconds": round(clock() - t0, 2),
            **extra,
        },
    )


def load_cached_models(model_root: Path, day: date) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The production head cache written by the morning run for ``day`` (``<root>/<day>``), or None.
    Never fits. The caller checks the fingerprint against the rebuilt history."""
    d = Path(model_root) / day.isoformat()
    meta_p, model_p = d / "meta.json", d / "models.pkl"
    if not (meta_p.exists() and model_p.exists()):
        return None
    meta = json.loads(meta_p.read_text())
    with model_p.open("rb") as f:
        models = pickle.load(f)  # noqa: S301  (local cache production wrote)
    return models, meta


def _status_map(report_rows: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    """Latest listed status per (game, player) from the exported injury report lines."""
    best: dict[tuple[str, int], tuple[Any, str]] = {}
    for r in report_rows:
        if r.get("player_id") is None or r.get("status") is None:
            continue
        k = (str(r["game_id"]), int(r["player_id"]))
        ts = r.get("report_as_of_et")
        if k not in best or (ts is not None and (best[k][0] is None or ts >= best[k][0])):
            best[k] = (ts, str(r["status"]).strip().lower())
    return {k: v[1] for k, v in best.items()}


def _p_play_map(
    p_play_rows: list[dict[str, Any]], prop_rows: list[dict[str, Any]]
) -> dict[tuple[str, int], float]:
    out: dict[tuple[str, int], tuple[Any, float]] = {}
    for r in p_play_rows:
        v = r.get("p_play_primary")
        if v is None:
            continue
        k = (str(r["game_id"]), int(r["player_id"]))
        if k not in out or (r.get("made_at") or datetime.min) >= (out[k][0] or datetime.min):
            out[k] = (r.get("made_at"), float(v))
    res = {k: v[1] for k, v in out.items()}
    for r in prop_rows:
        k = (str(r["game_id"]), int(r["player_id"]))
        if k not in res and r.get("p_play") is not None:
            res[k] = float(r["p_play"])
    return res


def _round(v: float, nd: int) -> float | None:
    return round(float(v), nd) if math.isfinite(float(v)) else None


def compute(
    con: duckdb.DuckDBPyConnection,
    day: date,
    games: list[dict[str, Any]],
    prop_rows: list[dict[str, Any]],
    p_play_rows: list[dict[str, Any]],
    report_rows: list[dict[str, Any]],
    made_at: datetime,
    *,
    model_root: Path | None,
    budget_s: float = DEFAULT_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
    max_rows: int = MAX_ROWS,
) -> SitsResult:
    """Scenario rows for ``day``'s unfinished games. Never raises: any failure is ``status: absent`` with a
    reason. ``made_at`` is the export's (naive UTC); ``con`` is the read-only ``nba.duckdb`` connection."""
    t0 = clock()
    try:
        return _compute(
            con, day, games, prop_rows, p_play_rows, report_rows, made_at,
            model_root, budget_s, clock, t0, max_rows,
        )  # fmt: skip
    except Exception as exc:  # the export must never fail because of scenarios
        log.warning("teammate_sits scenarios skipped: %s: %s", type(exc).__name__, exc)
        return _absent(f"error:{type(exc).__name__}", t0, clock, detail=str(exc)[:200])


def _compute(
    con: duckdb.DuckDBPyConnection,
    day: date,
    games: list[dict[str, Any]],
    prop_rows: list[dict[str, Any]],
    p_play_rows: list[dict[str, Any]],
    report_rows: list[dict[str, Any]],
    made_at: datetime,
    model_root: Path | None,
    budget_s: float,
    clock: Callable[[], float],
    t0: float,
    max_rows: int,
) -> SitsResult:
    if model_root is None:
        return _absent("disabled", t0, clock)
    slate = [g for g in games if g.get("game_date") == day and not g.get("final")]
    if not slate:
        return _absent("no_unfinished_games", t0, clock)
    cached = load_cached_models(model_root, day)  # cheap check first: no cache, no work
    if cached is None:
        return _absent("no_model_cache", t0, clock)
    models, meta = cached
    from nba.daily.predict import load_elo_params, slate_report_outs
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
        player_states,
    )
    from nba.props.forward import _history_frames, _with_slate_placeholders

    ids = [str(g["game_id"]) for g in slate]
    roster = pl.DataFrame(
        sorted(
            {
                (str(r["game_id"]), int(r["player_id"]), int(r["team_id"]))
                for r in prop_rows
                if str(r["game_id"]) in ids and r.get("team_id") is not None
            }
        ),
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "team_id": pl.Int64},
        orient="row",
    )
    if roster.is_empty():
        return _absent("no_roster_rows", t0, clock)

    cfg = ContextResidualConfig()
    tips = [(str(g["game_id"]), g["tipoff"]) for g in slate if g.get("tipoff") is not None]
    report_out = slate_report_outs(con, tips, made_at)
    h_games, h_pgs, static, avail = _history_frames(con, day)
    flagged, _ = flagged_from_availability(avail, h_games, cfg.report)
    flagged = {**flagged, **{str(g): set(v) for g, v in report_out.items()}}
    frame = pl.DataFrame(
        {
            "game_id": ids,
            "home_team": [int(g["home_team"]) for g in slate],
            "away_team": [int(g["away_team"]) for g in slate],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    a_games, a_pgs = _with_slate_placeholders(h_games, h_pgs, day, frame, roster)
    feats = build_features(a_games, a_pgs, static, flagged, load_elo_params())
    hist = feats.filter(pl.col("game_date") < day)
    fp = meta.get("fingerprint", {})
    if fp.get("n_rows") != hist.height or fp.get("max_date") != str(hist["game_date"].max()):
        return _absent(
            "model_cache_stale",
            t0,
            clock,
            cache_n_rows=fp.get("n_rows"),
            rebuilt_n_rows=hist.height,
        )
    if clock() - t0 > budget_s:
        return _absent("over_time_budget", t0, clock)

    played = sits_core.played_frame(a_games, a_pgs)
    states = player_states(played)
    info = sits_core.game_info_of(a_games)
    slate_roster = roster
    out_sets = sits_core.rostered_not_out(slate_roster, flagged)
    counted = sits_core.sitter_rows(played, states, out_sets, info)
    cand = counted.filter(pl.col("starter10") >= sits_core.REGULAR_STARTER10).sort(
        ["game_id", "team_id", "player_id"]
    )
    null_reasons = {
        "no_report": sum(1 for g in ids if g not in flagged),
        "sitter_already_out": sum(len(flagged.get(g, set())) for g in ids),
        "not_counted": sum(len(v) for v in out_sets.values()) - counted.height,
        "not_regular_starter": counted.height - cand.height,
        "no_context_row": 0,
    }
    if cand.is_empty():
        return _absent("no_candidate_sitters", t0, clock, null_reasons=null_reasons)

    sitters = cand.select("game_id", pl.col("player_id").alias("sitter_id"), "team_id")
    vac = sits_core.scenario_vacancy(
        played, states, flagged, info, sitters.select("game_id", "sitter_id").rows()
    )
    base = feats.filter(pl.col("game_id").is_in(ids))
    scn = sits_core.scenario_features(base, vac, sitters)
    long = sits_core.scenario_forecasts(models, base, scn, EXPORT_STATS)
    if long.is_empty():
        return _absent("no_forecast_rows", t0, clock, null_reasons=null_reasons)
    long = long.filter(
        pl.all_horizontal(
            [pl.col(c).is_finite() for c in ("base_mean", "scn_mean", "base_q10", "scn_q10")]
        )
    )
    n_pairs_expected = int(
        sitters.join(roster, on=["game_id", "team_id"])
        .filter(pl.col("player_id") != pl.col("sitter_id"))
        .height
    )
    n_pairs_got = long.select("sitter_id", "player_id", "game_id").unique().height
    null_reasons["no_context_row"] = max(n_pairs_expected - n_pairs_got, 0)
    if long.height > max_rows:
        return _absent(
            "over_row_budget", t0, clock, null_reasons=null_reasons, n_rows_would_be=long.height
        )

    status = _status_map(report_rows)
    pp = _p_play_map(p_play_rows, prop_rows)
    stamp = made_at
    rows: list[dict[str, Any]] = []
    for r in long.iter_rows(named=True):
        g, s, p = str(r["game_id"]), int(r["sitter_id"]), int(r["player_id"])
        rows.append(
            {
                "schema_version": SCHEMA_VERSION, "made_at": stamp, "game_id": g,
                "team_id": int(r["team_id"]), "sitter_id": s,
                "sitter_status": status.get((g, s), "unlisted"), "player_id": p,
                "stat": r["stat"],
                "base_mean": _round(r["base_mean"], 2), "scn_mean": _round(r["scn_mean"], 2),
                "delta_mean": _round(r["scn_mean"] - r["base_mean"], 2),
                "base_q10": _round(r["base_q10"], 1), "base_q50": _round(r["base_q50"], 1),
                "base_q90": _round(r["base_q90"], 1), "scn_q10": _round(r["scn_q10"], 1),
                "scn_q50": _round(r["scn_q50"], 1), "scn_q90": _round(r["scn_q90"], 1),
                "p_play": pp.get((g, p)), "rule_word": RULE_WORDS.get(str(r["stat"]), "UNTESTED"),
            }
        )  # fmt: skip

    prod = {
        (str(r["game_id"]), int(r["player_id"]), str(r["stat"])): float(r["mean"])
        for r in prop_rows
        if r.get("model_name") == "props_context_residual"
        and r.get("is_latest")
        and r.get("mean") is not None
    }
    diffs = [
        abs(float(r["base_mean"]) - prod[k])
        for r in rows
        if r["base_mean"] is not None and (k := (r["game_id"], r["player_id"], r["stat"])) in prod
    ]
    info_out: dict[str, Any] = {
        "status": "ok", "reason": None, "n_rows": len(rows),
        "n_scenarios": long.select("game_id", "sitter_id").unique().height,
        "n_games": long["game_id"].n_unique(), "null_reasons": null_reasons,
        "rule_sha8": RULE_SHA8, "stats": list(EXPORT_STATS), "rule_words": {s: RULE_WORDS[s] for s in EXPORT_STATS},
        "base_vs_production": {
            "n_compared": len(diffs),
            "max_abs_diff": round(max(diffs), 4) if diffs else None,
            "n_over_0_05": int(sum(d > 0.05 for d in diffs)),
        },
        "seconds": round(clock() - t0, 2),
    }  # fmt: skip
    return SitsResult(rows, info_out)
