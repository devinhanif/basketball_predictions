"""Walk-forward test: RAPM player value vs box value inside injury-adjusted MOV-Elo.

PRE-REGISTERED DECISION RULE (written before any real-data run; see docs/RAPM.md)
-------------------------------------------------------------------------------
Variants (all: frozen MOV-Elo params, same pre-tip official-report rule,
coefficients refit monthly on strictly earlier games, as production):
  (a) production box value        (d_out, d_doubt)
  (b) RAPM value                  (r_out, r_doubt)   <- PRIMARY candidate
  (c) both                        (all four)         <- secondary, cannot be substituted
RAPM value of an OUT/DOUBTFUL player = max(rapm, 0) * as_of_minutes / 48,
rapm from the monthly snapshot whose cutoff is the first day of the game's
month (possessions strictly before it), history weight 0.5 per season,
ridge lambda from walk-forward CV on season 2022 only.

KEEP (b) as the replacement for (a) iff BOTH:
  1. paired per-game bootstrap 95% CI of the log-loss delta (b - a) over all
     scored 2022-2024 games is entirely below 0;
  2. no slice rotation-OUT = 0 / 1 / 2+ (n >= 100) is worse than (a) by more
     than 0.005 log loss (point delta).
Otherwise REJECT. Season 2025 is never loaded. One row per game, so the paired
bootstrap is game-clustered. Brier/ECE deltas are reported, not gating.

Maintainer command::

    uv run python -m research.eval.rapm_injury_elo_eval --db nba.duckdb \\
        --config configs/injury_elo.yaml --out-dir data/rapm
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.injury_elo_eval import (
    SLICE_MIN_N,
    SLICE_VETO,
    _cmp,
    _ece,
    load_config,
    walk_forward_probs,
)
from nba.models.injury_elo import (
    VALUE_SCALE,
    InjuryFeatureConfig,
    ValueConfig,
    _side_sums,
    asof_values,
    build_injury_features,
    build_value_state,
    feature_config_from,
    league_rate_by_date,
    load_games_frame,
    load_stats_frame,
    sequential_elo_logits,
    sigmoid,
)
from nba.truth.metrics import brier_score, log_loss
from research.features.rapm import (
    PossessionData,
    RapmConfig,
    build_snapshots,
    cv_ridge_lambda,
    load_possessions,
    season_end_rapm,
)
from research.sim.usage_redistribution import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
)

LAMBDA_GRID = (250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
HISTORY_DECAY = 0.5
MIN_POSS_LIST = 2000.0
MIN_POSS_STABILITY = 1500.0


def _asof_minutes(pairs: pl.DataFrame, state: pl.DataFrame, vc: ValueConfig) -> pl.DataFrame:
    """Production's as-of projected minutes/game (decayed, shrunk) per (game, player)."""
    key = (pl.col("game_date") - pl.duration(days=1)).alias("_key")
    left = pairs.with_columns(key).sort("_key")
    right = state.rename({"game_date": "_prior_date"}).sort("_prior_date")
    j = left.join_asof(
        right, left_on="_key", right_on="_prior_date", by="player_id", strategy="backward"
    )
    mins = (pl.col("s_m").fill_null(0.0) + vc.minutes_pseudo_games * vc.prior_minutes) / (
        pl.col("w").fill_null(0.0) + vc.minutes_pseudo_games
    )
    return j.select(["game_id", "player_id", mins.alias("mins")])


def rapm_side_features(
    games: pl.DataFrame,
    out_flag: dict[str, Any],
    dbt_flag: dict[str, Any],
    state: pl.DataFrame,
    league: pl.DataFrame,
    snaps: pl.DataFrame,
    fcfg: InjuryFeatureConfig,
) -> pl.DataFrame:
    """Per game: r_out, r_doubt (away minus home, / VALUE_SCALE) from RAPM value."""
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    recs = [
        (gid, pid, info[gid][0], status)
        for status, flag in (("out", out_flag), ("doubtful", dbt_flag))
        for gid, pids in flag.items()
        for pid in pids
    ]
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "status": pl.Utf8,
    }
    pairs = pl.DataFrame(recs, schema=schema, orient="row")
    base = asof_values(pairs.drop("status"), state, league, fcfg.value).join(
        pairs.select(["game_id", "player_id", "status"]), on=["game_id", "player_id"]
    )
    mins = _asof_minutes(pairs.drop("status"), state, fcfg.value)
    vals = (
        base.join(mins, on=["game_id", "player_id"])
        .with_columns(pl.col("game_date").dt.truncate("1mo").alias("cutoff"))
        .join(snaps.select(["cutoff", "player_id", "rapm"]), on=["cutoff", "player_id"], how="left")
        .with_columns(
            (pl.col("rapm").fill_null(0.0).clip(lower_bound=0.0) * pl.col("mins") / 48.0).alias(
                "value"
            )
        )
    )
    side = _side_sums(vals, info, fcfg.doubtful_weight)
    rows = []
    for gid in games["game_id"].to_list():
        vo_h, vo_a, vd_h, vd_a = side.get(gid, (0.0, 0.0, 0.0, 0.0))
        rows.append((gid, (vo_a - vo_h) / VALUE_SCALE, (vd_a - vd_h) / VALUE_SCALE))
    return pl.DataFrame(
        rows,
        schema={"game_id": pl.Utf8, "r_out": pl.Float64, "r_doubt": pl.Float64},
        orient="row",
    )


def decide_rapm(vs_prod: dict[str, Any], slices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    c1 = bool(vs_prod["log_loss"]["hi"] < 0.0)
    keys = [k for k in slices if k.startswith("rot_out=")]
    vetoes = [
        k
        for k in keys
        if slices[k]["n"] >= SLICE_MIN_N and slices[k]["log_loss"]["delta"] > SLICE_VETO
    ]
    return {
        "overall_ci_below_zero": c1,
        "slice_vetoes": vetoes,
        "verdict": "KEEP" if (c1 and not vetoes) else "REJECT",
    }


def _names(ids: list[int]) -> dict[int, str]:
    try:
        from nba_api.stats.static import players

        return {int(p["id"]): str(p["full_name"]) for p in players.get_players()}
    except Exception:  # offline static list unavailable
        return {}


def sanity_report(
    poss: PossessionData, seasons: list[int], lam: float, top_n: int = 15
) -> dict[str, Any]:
    """Single-season (no history) fits: top/bottom lists + season-to-season stability."""
    names = _names([])
    out: dict[str, Any] = {"top": {}, "bottom": {}, "stability": {}}
    fits: dict[int, pl.DataFrame] = {}
    for s in seasons:
        df = season_end_rapm(poss, s, lam)
        fits[s] = df
        big = df.filter(pl.col("n_poss") >= MIN_POSS_LIST)

        def fmt(d: pl.DataFrame) -> list[dict[str, Any]]:
            return [
                {
                    "player": names.get(int(r["player_id"]), str(r["player_id"])),
                    "rapm": round(float(r["rapm"]), 2),
                    "poss": int(r["n_poss"]),
                }
                for r in d.iter_rows(named=True)
            ]

        out["top"][s] = fmt(big.head(top_n))
        out["bottom"][s] = fmt(big.tail(top_n).sort("rapm"))
    for a, b in zip(seasons[:-1], seasons[1:], strict=True):
        j = (
            fits[a]
            .join(fits[b], on="player_id", suffix="_b")
            .filter(
                (pl.col("n_poss") >= MIN_POSS_STABILITY)
                & (pl.col("n_poss_b") >= MIN_POSS_STABILITY)
            )
        )
        r = float(np.corrcoef(j["rapm"].to_numpy(), j["rapm_b"].to_numpy())[0, 1])
        out["stability"][f"{a}->{b}"] = {"corr": round(r, 3), "n_players": j.height}
    return out


def run_rapm_eval(
    con: duckdb.DuckDBPyConnection,
    cfg: dict[str, Any],
    out_dir: str | Path | None = None,
    lambdas: tuple[float, ...] = LAMBDA_GRID,
) -> dict[str, Any]:
    holdout = int(cfg["holdout_season"])
    seasons = [int(s) for s in cfg["oof_seasons"]]
    if holdout in seasons or max(seasons) >= holdout:
        raise ValueError("oof_seasons must all be strictly before the burned holdout season")
    fcfg = feature_config_from(cfg)
    elo_params = {
        k: float(v)
        for k, v in load_config(cfg["mov_elo_config"]).items()
        if k in ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div")
    }
    through = max(seasons)
    res: dict[str, Any] = {"seasons": seasons}

    # ---- RAPM: CV, snapshots, sanity
    t0 = time.time()
    poss = load_possessions(con, through)
    assert int(poss.seasons.max()) < holdout
    res["n_possessions"] = len(poss)
    res["load_seconds"] = round(time.time() - t0, 1)
    cv = cv_ridge_lambda(poss, lambdas, seasons[0], HISTORY_DECAY)
    res["cv_season"] = seasons[0]
    res["cv"] = cv.to_dicts()
    finite = cv.filter(pl.col("lambda").is_finite())
    lam = float(finite.sort("mse")["lambda"][0])
    res["chosen_lambda"] = lam
    t1 = time.time()
    snaps = build_snapshots(poss, RapmConfig(ridge_lambda=lam, history_decay=HISTORY_DECAY))
    t_snap = time.time() - t1
    res["snapshot_build_seconds"] = round(t_snap, 1)
    res["n_snapshots"] = int(snaps["cutoff"].n_unique())
    res["seconds_per_refit"] = round(t_snap / max(res["n_snapshots"], 1), 2)
    res["sanity"] = sanity_report(poss, seasons, lam)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        snaps.write_parquet(Path(out_dir) / "snapshots.parquet")

    # ---- injury features: production (a) and RAPM (b)
    games = load_games_frame(con, through)
    stats = load_stats_frame(con, through)
    gids = games["game_id"].to_list()
    base_cfg = ReportTriggerConfig(
        statuses=("out",),
        tipoff_hour_et=fcfg.tipoff_hour_et,
        lead_minutes=fcfg.lead_minutes,
        table=fcfg.report_table,
    )
    dbt_cfg = ReportTriggerConfig(
        statuses=("doubtful",),
        tipoff_hour_et=fcfg.tipoff_hour_et,
        lead_minutes=fcfg.lead_minutes,
        table=fcfg.report_table,
    )
    rows = load_report_rows(con, base_cfg, game_ids=gids)
    feats = build_injury_features(con, games, stats, fcfg, report_rows=rows, with_oracle=False)
    out_flag, _ = latest_pretip_flagged(rows, base_cfg)
    dbt_flag, _ = latest_pretip_flagged(rows, dbt_cfg)
    state = build_value_state(stats, fcfg.value)
    league = league_rate_by_date(stats)
    rfeat = rapm_side_features(games, out_flag, dbt_flag, state, league, snaps, fcfg)

    elo_logit = sequential_elo_logits(games, elo_params)
    frame = (
        games.with_columns(pl.Series("elo_logit", elo_logit))
        .join(feats, on="game_id", how="left")
        .join(rfeat, on="game_id", how="left")
        .sort(["game_date", "game_id"])
    )
    offset = frame["elo_logit"].to_numpy()
    fit_cfg = cfg["fit"]
    rl, min_sig = float(fit_cfg["ridge_lambda"]), int(fit_cfg["min_signal_games"])
    variants = {
        "a_box_value": ("d_out", "d_doubt"),
        "b_rapm_value": ("r_out", "r_doubt"),
        "c_both": ("d_out", "d_doubt", "r_out", "r_doubt"),
    }
    probs = {
        k: walk_forward_probs(frame, offset, cols, rl, min_sig) for k, cols in variants.items()
    }
    p_elo = sigmoid(offset)

    first_date = frame["game_date"].min()
    keep = (frame["season"].is_in(seasons) & (frame["game_date"] > first_date)).to_numpy()
    y = frame["y"].to_numpy().astype(float)[keep]
    nrot = frame["n_rot_out"].to_numpy()[keep]
    masks = {"rot_out=0": nrot == 0, "rot_out=1": nrot == 1, "rot_out=2+": nrot >= 2}
    n_boot = int(cfg["bootstrap"]["n_boot"])
    seed = int(cfg["bootstrap"]["seed"])
    res["n_scored"] = int(keep.sum())
    res["mov_elo"] = {
        "log_loss": float(log_loss(y, p_elo[keep])),
        "brier": float(brier_score(y, p_elo[keep])),
        "ece": _ece(y, p_elo[keep]),
    }
    pa = probs["a_box_value"][0][keep]
    res["variants"] = {}
    for name, (p_all, coef_log) in probs.items():
        p = p_all[keep]
        v: dict[str, Any] = {
            "log_loss": float(log_loss(y, p)),
            "brier": float(brier_score(y, p)),
            "ece": _ece(y, p),
            "final_coef": coef_log[-1]["coef"],
            "vs_mov_elo": _cmp(y, p, p_elo[keep], n_boot, seed),
        }
        if name != "a_box_value":
            v["vs_production"] = _cmp(y, p, pa, n_boot, seed)
            v["slices_vs_production"] = {
                k: _cmp(y[m], p[m], pa[m], max(500, n_boot // 2), seed)
                for k, m in masks.items()
                if int(m.sum()) >= 30
            }
            v["decision"] = decide_rapm(v["vs_production"], v["slices_vs_production"])
        res["variants"][name] = v
    res["slice_n"] = {k: int(m.sum()) for k, m in masks.items()}
    res["corr_box_vs_rapm_features"] = float(
        np.corrcoef(frame["d_out"].to_numpy()[keep], frame["r_out"].to_numpy()[keep])[0, 1]
    )
    if out_dir is not None:
        (Path(out_dir) / "results.json").write_text(json.dumps(res, indent=2, default=str))
    return res


def _print_table(res: dict[str, Any]) -> None:
    print(
        f"n_scored={res['n_scored']}  lambda={res['chosen_lambda']}  "
        f"refit={res['seconds_per_refit']}s  "
        f"corr(d_out,r_out)={res['corr_box_vs_rapm_features']:.3f}"
    )
    print(f"{'variant':16s} {'logloss':>8s} {'brier':>8s} {'ece':>7s}  dLL vs prod [95% CI]")
    m = res["mov_elo"]
    print(f"{'mov_elo':16s} {m['log_loss']:8.4f} {m['brier']:8.4f} {m['ece']:7.4f}")
    for k, v in res["variants"].items():
        d = v.get("vs_production", {}).get("log_loss")
        tail = f"{d['delta']:+.4f} [{d['lo']:+.4f},{d['hi']:+.4f}]" if d else ""
        print(f"{k:16s} {v['log_loss']:8.4f} {v['brier']:8.4f} {v['ece']:7.4f}  {tail}")
        if "decision" in v:
            print("   decision:", v["decision"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RAPM vs box value in injury-adjusted MOV-Elo")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--config", default="configs/injury_elo.yaml")
    ap.add_argument("--out-dir", default="data/rapm")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    con = duckdb.connect(args.db, read_only=True)
    res = run_rapm_eval(con, cfg, args.out_dir)
    _print_table(res)
    print(json.dumps({k: res[k] for k in ("cv", "sanity")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
