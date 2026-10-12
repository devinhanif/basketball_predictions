# ruff: noqa: E501
"""Compute and log the RECENT_ROLE_FULL shadow grids for tonight's slate (read-only on every input).

Same inputs as production's ``predict_slate_context``: the history frames, the slate placeholders, the
report-OUT map and ``build_features``; the only additions are the five recent-role columns and the FULL
refit (``nba.daily.full_shadow.model``). Rows are written for every player of ``wide`` (production's own
wide-roster output) that production routes to the context model. Nothing here touches ``forward_predictions``.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.full_shadow import model as M
from nba.daily.full_shadow import store
from nba.daily.pplay_shadow.features import SlateGame
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    predict_rows,
    to_integer_support,
)
from nba.props.forward import QUANTILE_TAUS, _history_frames, _with_slate_placeholders


def prod_cond(wide: pl.DataFrame) -> dict[tuple[str, int, str], tuple[str, float, float, bool]]:
    """{(game, player, stat): (q_grid JSON, mean, p_play, is_context)} from production's output rows."""
    out: dict[tuple[str, int, str], tuple[str, float, float, bool]] = {}
    for r in wide.iter_rows(named=True):
        out[(str(r["game_id"]), int(r["player_id"]), str(r["stat"]))] = (
            str(r["q_grid"]) if r["q_grid"] is not None else "null",
            float(r["mean"]),
            float(r["p_play"]),
            str(r["model"]) == "context_residual",
        )
    return out


def build_full_frame(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    wide: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    cfg: ContextResidualConfig,
) -> pl.DataFrame:
    """Production's feature frame (history before ``run_date`` + tonight's NULL-minutes placeholders) with
    the five recent-role columns attached. Every read is dated before ``run_date``."""
    h_games, h_pgs, static, avail = _history_frames(con, run_date)
    flagged, _ = flagged_from_availability(avail, h_games, cfg.report)
    flagged = {**flagged, **{str(g): set(v) for g, v in report_out.items()}}
    games = pl.DataFrame(
        {
            "game_id": [g.game_id for g in slate],
            "home_team": [g.home_team for g in slate],
            "away_team": [g.away_team for g in slate],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    roster = wide.select("game_id", "player_id", "team_id").unique()
    a_games, a_pgs = _with_slate_placeholders(h_games, h_pgs, run_date, games, roster)
    feats = build_features(a_games, a_pgs, static, flagged, elo_params)
    return M.attach_columns(feats, h_pgs, a_games)


def run_full_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    made_at: dt.datetime,
    run_id: str,
    wide: pl.DataFrame,
    report_out: dict[str, set[int]],
    prod_logged: set[tuple[str, int]],
    elo_params: dict[str, float],
    *,
    cache_root: Path | None = None,
    cfg: ContextResidualConfig | None = None,
) -> dict[str, Any]:
    """Refit FULL on rows before ``run_date`` (cached per date like production), write one shadow row per
    (game, player, stat) the context model forecasts. ``wide``: production-format rows for every rostered
    not-OUT player; ``prod_logged``: (game_id, player_id) pairs production wrote primary rows for."""
    t0 = time.perf_counter()
    if wide.is_empty() or not slate:
        return {"rows": 0, "seconds": 0.0, "note": "no rostered players"}
    store.ensure_tables(con)
    cfg = M.full_config(cfg)
    feats = build_full_frame(con, run_date, slate, wide, report_out, elo_params, cfg)
    t_feat = time.perf_counter() - t0
    cache_dir = None if cache_root is None else cache_root / f"{run_date.isoformat()}_full"
    models, info = M.load_or_fit(feats, run_date, cfg, cache_dir)
    slate_ids = [g.game_id for g in slate]
    tip = {g.game_id: g.tipoff for g in slate}
    slate_feats = feats.filter(pl.col("game_id").is_in(slate_ids))
    prod = prod_cond(wide)
    team = {
        (str(r["game_id"]), int(r["player_id"])): int(r["team_id"])
        for r in wide.select("game_id", "player_id", "team_id").unique().iter_rows(named=True)
    }
    fsha = M.feature_set_sha(cfg)
    train_through = dt.date.fromisoformat(str(info["train_through"]))
    taus19 = np.array(QUANTILE_TAUS)
    rows: list[dict[str, object]] = []
    for stat in PROP_STATS:
        er, mean, q19 = predict_rows(models, slate_feats, stat, taus19)
        if er.is_empty():
            continue
        model = models[stat]
        assert isinstance(model, M.FullModel)
        pi = model.pi(er)
        fps = M.row_fingerprints(er, stat)
        qi = to_integer_support(q19)
        for i, r in enumerate(er.iter_rows(named=True)):
            key = (str(r["game_id"]), int(r["player_id"]), stat)
            p = prod.get(key)
            if (
                p is None or not p[3]
            ):  # not a production context row (recency fallback / not rostered)
                continue
            rows.append(
                {
                    "run_id": run_id,
                    "made_at": made_at,
                    "game_id": key[0],
                    "tipoff": tip[key[0]],
                    "player_id": key[1],
                    "team_id": team[(key[0], key[1])],
                    "stat": stat,
                    "model_version": M.MODEL_VERSION,
                    "q_grid": json.dumps([float(v) for v in qi[i]]),
                    "mean_full": float(mean[i]),
                    "pi_full": float(pi[i]),
                    "q_grid_prod": p[0],
                    "mean_prod": p[1],
                    "p_play_prod": p[2],
                    "in_prod_log": (key[0], key[1]) in prod_logged,
                    "train_through": train_through,
                    "n_train": int(info["n_train_rows"]),
                    "features_through": run_date,
                    "feature_set_sha": fsha,
                    "features_sha": fps[i],
                }
            )
    written, deduped, refused = store.append_rows(con, rows, made_at)
    return {
        "rows": len(rows),
        "written": written,
        "deduped": deduped,
        "refused_after_tip": refused,
        "n_train": int(info["n_train_rows"]),
        "cache_hit": bool(info["cache_hit"]),
        "fit_seconds": round(float(info["fit_seconds"]), 1),
        "feature_seconds": round(t_feat, 1),
        "seconds": round(time.perf_counter() - t0, 1),
    }
