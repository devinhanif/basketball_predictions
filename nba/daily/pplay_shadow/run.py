# ruff: noqa: E501
"""Compute and log the recent-role P(play) shadow for tonight's slate (read-only on every input).

Inputs are production's own wide roster frame (every rostered, not-OUT player, no minutes / P(play) /
roster-cap filter; see ``nba.daily.pipeline._wide_roster_frame``) so the shadow covers the players
production's forward log drops. Nothing here touches ``forward_predictions``.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.daily.pplay_shadow import model as M
from nba.daily.pplay_shadow import rivalry, store
from nba.daily.pplay_shadow.features import FEATURES, SlateGame, build_frame
from nba.features.game_tipoff import build_game_tipoff

ET = ZoneInfo("America/New_York")
PROP_STATS = ("pts", "reb", "ast", "fg3m")


def to_et_naive(t: dt.datetime) -> dt.datetime:
    """Naive UTC -> naive US-Eastern wall clock (the clock of the report ``as_of`` stamps)."""
    return t.replace(tzinfo=dt.UTC).astimezone(ET).replace(tzinfo=None)


def slate_tips(slate: list[SlateGame], base: pl.DataFrame) -> pl.DataFrame:
    mine = pl.DataFrame(
        {
            "game_id": [g.game_id for g in slate],
            "tipoff_utc": [g.tipoff for g in slate],
            "tip_et": [to_et_naive(g.tipoff) for g in slate],
        },
        schema=base.schema,
    )
    return pl.concat([base.filter(~pl.col("game_id").is_in(mine["game_id"].to_list())), mine])


def props_cond(wide: pl.DataFrame) -> dict[tuple[str, int], str]:
    """{(game, player): JSON {stat: {model, mean, q_grid}}} from production's own output rows."""
    acc: dict[tuple[str, int], dict[str, Any]] = {}
    for r in wide.iter_rows(named=True):
        acc.setdefault((str(r["game_id"]), int(r["player_id"])), {})[str(r["stat"])] = {
            "model": str(r["model"]),
            "mean": float(r["mean"]),
            "q_grid": json.loads(r["q_grid"]) if r["q_grid"] is not None else None,
        }
    return {k: json.dumps(v, sort_keys=True) for k, v in acc.items()}


def drop_reason(r: dict[str, Any], min_p_play: float, min_proj: float) -> str:
    if float(r["p_play_raw"]) < min_p_play:
        return "p_play_raw<min_p_play"
    if float(r["proj_minutes"]) < min_proj:
        return "proj_minutes<min"
    return "roster_cap"


def run_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    made_at: dt.datetime,
    run_id: str,
    wide: pl.DataFrame,
    prod_logged: set[tuple[str, int]],
    elo_params: dict[str, float],
    *,
    tips_dir: Any = None,
    min_p_play: float = 0.5,
    min_proj_minutes: float = 2.0,
    min_train: int = M.MIN_TRAIN,
) -> dict[str, Any]:
    """Build features, refit R on rows before ``run_date``, write one shadow row per wide-frame player.

    ``wide``: production-format rows (``nba.props.forward.OUTPUT_COLUMNS``) for every rostered not-OUT
    player. ``prod_logged``: (game_id, player_id) pairs production wrote primary rows for."""
    t0 = time.perf_counter()
    if wide.is_empty():
        return {"rows": 0, "seconds": 0.0, "note": "no rostered players"}
    store.ensure_tables(con)
    cfg = M.load_cfg()
    per_player = (
        wide.group_by(["game_id", "player_id"], maintain_order=True)
        .agg(
            pl.col("team_id").first(),
            pl.col("p_play").first(),
            pl.col("p_play_raw").first(),
            pl.col("proj_minutes").first(),
        )
        .sort(["game_id", "player_id"])
    )
    targets = per_player.select("game_id", "player_id", "team_id")
    base_tips = build_game_tipoff(tips_dir) if tips_dir is not None else build_game_tipoff()
    frame, info = build_frame(
        con,
        run_date,
        slate,
        targets,
        elo_params=elo_params,
        known_at_et=to_et_naive(made_at),
        tips=slate_tips(slate, base_tips),
    )
    fit = M.fit(frame, run_date, cfg, min_train)
    # comparators C and C' (the live confirmation of R against them): same refit rule and learner
    comp_fit = {
        k: M.fit(frame, run_date, cfg, min_train, cols) for k, cols in M.COMPARATOR_FEATURES.items()
    }
    sl = frame.filter(pl.col("is_slate"))
    p_comp: dict[str, dict[tuple[str, int], float]] = {"c": {}, "cp": {}}
    if sl.height:
        for k, cf in comp_fit.items():
            if cf is not None:
                for g, p, pc in zip(sl["game_id"], sl["player_id"], M.predict(cf, sl), strict=True):
                    p_comp[k][(str(g), int(p))] = float(pc)
    p_r: dict[tuple[str, int], float] = {}
    sha_row: dict[tuple[str, int], str] = {}
    if fit is not None and sl.height:
        preds = M.predict(fit, sl)
        fps = M.row_fingerprints(sl)
        for g, p, pr, fp in zip(sl["game_id"], sl["player_id"], preds, fps, strict=True):
            p_r[(str(g), int(p))] = float(pr)
            sha_row[(str(g), int(p))] = fp
    cond = props_cond(wide)
    tip = {g.game_id: g.tipoff for g in slate}
    riv = {g.game_id: rivalry.flags(g.home_team, g.away_team, run_date) for g in slate}
    fsha = M.feature_set_sha(cfg)
    rows: list[dict[str, object]] = []
    for r in per_player.iter_rows(named=True):
        key = (str(r["game_id"]), int(r["player_id"]))
        logged = key in prod_logged
        if key in p_r:
            reason: str | None = None
        elif fit is None:
            reason = "no_model"
        else:
            reason = info["reasons"].get(key, "no_features")
        rows.append(
            {
                "run_id": run_id,
                "made_at": made_at,
                "game_id": key[0],
                "tipoff": tip[key[0]],
                "player_id": key[1],
                "team_id": int(r["team_id"]),
                "model_version": M.MODEL_VERSION,
                "p_play_prod": float(r["p_play"]),
                "p_play_r": p_r.get(key),
                "r_reason": reason,
                "in_prod_log": logged,
                "prod_drop_reason": None
                if logged
                else drop_reason(r, min_p_play, min_proj_minutes),
                "proj_minutes": float(r["proj_minutes"]),
                "props_cond": cond[key],
                "train_through": fit.train_through if fit else None,
                "n_train": fit.n_train if fit else 0,
                "features_through": run_date,
                "feature_set_sha": fsha,
                "features_sha": sha_row.get(key),
                "t60_report_as_of": info["t60_snapshot_ts"].get(key[0]),
                "div_rival": riv[key[0]]["div_rival"],
                "hist_rival": riv[key[0]]["hist_rival"],
                "p_play_c": p_comp["c"].get(key) if key in p_r else None,
                "p_play_cp": p_comp["cp"].get(key) if key in p_r else None,
            }
        )
    written, deduped, refused = store.append_rows(con, rows, made_at)
    n_r = sum(1 for r in rows if r["p_play_r"] is not None)
    return {
        "rows": len(rows),
        "rows_with_r": n_r,
        "written": written,
        "deduped": deduped,
        "refused_after_tip": refused,
        "n_train": fit.n_train if fit else 0,
        "n_features": len(FEATURES),
        "seconds": round(time.perf_counter() - t0, 1),
    }
