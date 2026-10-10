"""Walk-forward evaluation: T-30 injury-Elo (inactive list known) vs production T-60.

Pre-registered in ``docs/INJURY_ELO_T30.md`` (written before any result). Season
2025 is never loaded. Maintainer command (real DB, ~1-2 min, CPU)::

    uv run python -m nba.eval.injury_elo_t30_eval --db nba.duckdb \\
        --config configs/injury_elo.yaml --out-dir data/injury_elo_t30 \\
        --report reports/injury_elo_t30.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.injury_elo_eval import (
    EARLY_GAMES,
    SLICE_MIN_N,
    _cmp,
    _ece,
    game_numbers,
    load_config,
    walk_forward_probs,
)
from nba.eval.metrics import brier_score, log_loss
from nba.models.injury_elo import (
    build_injury_features,
    feature_config_from,
    load_games_frame,
    load_stats_frame,
    sequential_elo_logits,
)
from nba.models.injury_elo_t30 import (
    T30_FEATURE_COLS,
    WC_FEATURE_COLS,
    build_t30_features,
    t30_config_from,
)

FLOOR = -0.002
ECE_GUARD = 0.005
SLICE_VETO = 0.005
MAX_SEASON = 2024


def decide_t30(overall: dict[str, Any], slices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pre-registered pass rule (see docs/INJURY_ELO_T30.md)."""
    ll = overall["log_loss"]
    c1 = bool(ll["delta"] <= FLOOR and ll["hi"] < 0.0)
    c2 = bool(overall["brier"]["delta"] <= 0.0 and overall["ece"]["delta"] <= ECE_GUARD)
    vetoes = [
        k
        for k, v in slices.items()
        if v["n"] >= SLICE_MIN_N and v["log_loss"]["delta"] > SLICE_VETO
    ]
    return {
        "floor_and_ci": c1,
        "brier_ece_guards": c2,
        "slice_vetoes": vetoes,
        "verdict": "PASS" if (c1 and c2 and not vetoes) else "FAIL",
    }


def classify_wc(wc: dict[str, Any]) -> str:
    ll = wc["log_loss"]
    if ll["delta"] <= FLOOR and ll["hi"] < 0.0:
        return "robust"
    return "conditional" if ll["delta"] < 0.0 else "headline only"


def run_t30_eval(
    con: duckdb.DuckDBPyConnection, cfg: dict[str, Any], out_dir: str | Path | None = None
) -> dict[str, Any]:
    holdout = int(cfg["holdout_season"])
    seasons = [int(s) for s in cfg["oof_seasons"]]
    if max(seasons) >= holdout or max(seasons) > MAX_SEASON:
        raise ValueError("oof_seasons must be strictly before the burned holdout season")
    fcfg = feature_config_from(cfg)
    elo_params = {
        k: float(v)
        for k, v in load_config(cfg["mov_elo_config"]).items()
        if k in ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div")
    }
    games = load_games_frame(con, max(seasons))
    stats = load_stats_frame(con, max(seasons))
    assert int(games["season"].max()) < holdout  # type: ignore[arg-type]
    t60 = build_injury_features(con, games, stats, fcfg, with_oracle=False)
    t30 = build_t30_features(con, games, stats, t30_config_from(fcfg))
    frame = (
        games.with_columns(pl.Series("elo_logit", sequential_elo_logits(games, elo_params)))
        .join(t60.select(["game_id", "d_out", "d_doubt", "n_rot_out"]), on="game_id", how="left")
        .join(t30, on="game_id", how="left")
        .sort(["game_date", "game_id"])
    )
    offset = frame["elo_logit"].to_numpy()
    lam = float(cfg["fit"]["ridge_lambda"])
    min_sig = int(cfg["fit"]["min_signal_games"])
    variants: dict[str, np.ndarray] = {
        "t60": walk_forward_probs(frame, offset, ("d_out", "d_doubt"), lam, min_sig)[0],
        "t30": walk_forward_probs(frame, offset, T30_FEATURE_COLS, lam, min_sig)[0],
        "t30_wc": walk_forward_probs(frame, offset, WC_FEATURE_COLS, lam, min_sig)[0],
        "t30_out_only": walk_forward_probs(frame, offset, ("d_out30",), lam, min_sig)[0],
    }
    first_date = frame["game_date"].min()
    gn = game_numbers(games)
    early_all = np.array([gn[g] <= EARLY_GAMES for g in frame["game_id"].to_list()])
    n_boot = int(cfg["bootstrap"]["n_boot"])
    seed = int(cfg["bootstrap"]["seed"])

    results: dict[str, Any] = {"seasons": {}}
    for season in seasons:
        keep = (frame["season"] == season).to_numpy() & (frame["game_date"] > first_date).to_numpy()
        if not keep.any():
            continue
        y = frame["y"].to_numpy().astype(float)[keep]
        base = variants["t60"][keep]
        n30 = frame["n_rot_out30"].to_numpy()[keep]
        masks = {
            "gtd>=1": frame["n_rot_gtd"].to_numpy()[keep] >= 1,
            "rot_out30=0": n30 == 0,
            "rot_out30=1": n30 == 1,
            "rot_out30>=2": n30 >= 2,
            "late_add>=1": frame["n_rot_late_add"].to_numpy()[keep] >= 1,
            "early(<=15g)": early_all[keep],
            "rest": ~early_all[keep],
        }
        res: dict[str, Any] = {
            "n": int(keep.sum()),
            "t60": {
                "log_loss": float(log_loss(y, base)),
                "brier": float(brier_score(y, base)),
                "ece": _ece(y, base),
            },
            "variants": {},
        }
        for name in ("t30", "t30_wc", "t30_out_only"):
            p = variants[name][keep]
            v: dict[str, Any] = {
                "log_loss": float(log_loss(y, p)),
                "brier": float(brier_score(y, p)),
                "ece": _ece(y, p),
                "overall": _cmp(y, p, base, n_boot, seed),
                "slices": {
                    k: _cmp(y[m], p[m], base[m], max(500, n_boot // 2), seed)
                    for k, m in masks.items()
                    if int(m.sum()) >= 30
                },
            }
            if name == "t30":
                v["decision"] = decide_t30(v["overall"], v["slices"])
            if name == "t30_wc":
                v["wc_class"] = classify_wc(v["overall"])
            res["variants"][name] = v
        res["slice_n"] = {k: int(m.sum()) for k, m in masks.items()}
        results["seasons"][str(season)] = res
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return results


def _fmt(c: dict[str, Any]) -> str:
    return f"{c['delta']:+.4f} [{c['lo']:+.4f}, {c['hi']:+.4f}]"


def render_report(results: dict[str, Any]) -> str:
    lines = ["# injury_elo_t30: T-30 (inactive list) vs production T-60", ""]
    lines.append(
        "Delta = candidate minus T-60 (negative = better). 95% paired bootstrap CI, one row "
        "per game. Pre-registration: docs/INJURY_ELO_T30.md. Season 2025 never loaded."
    )
    for season, res in results["seasons"].items():
        lines += ["", f"## Season {season} (n = {res['n']})", ""]
        t = res["t60"]
        lines.append(
            f"T-60 production: log loss {t['log_loss']:.4f}, Brier {t['brier']:.4f}, "
            f"ECE {t['ece']:.4f}"
        )
        lines += ["", "| variant | log loss d | Brier d | ECE d |", "|---|---|---|---|"]
        for name, v in res["variants"].items():
            o = v["overall"]
            lines.append(
                f"| {name} | {_fmt(o['log_loss'])} | {_fmt(o['brier'])} | {_fmt(o['ece'])} |"
            )
        for name in ("t30", "t30_wc"):
            v = res["variants"][name]
            lines += [
                "",
                f"Slices, {name} (log-loss delta):",
                "",
                "| slice | n | delta |",
                "|---|---|---|",
            ]
            for k, s in v["slices"].items():
                lines.append(f"| {k} | {s['n']} | {_fmt(s['log_loss'])} |")
        lines += [
            "",
            f"Decision (t30): {json.dumps(res['variants']['t30']['decision'])}",
            f"Worst-case class: {res['variants']['t30_wc']['wc_class']}",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T-30 injury-Elo walk-forward evaluation")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--config", default="configs/injury_elo.yaml")
    ap.add_argument("--out-dir", default="data/injury_elo_t30")
    ap.add_argument("--report", default="reports/injury_elo_t30.md")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    con = duckdb.connect(args.db, read_only=True)
    try:
        res = run_t30_eval(con, cfg, args.out_dir)
    finally:
        con.close()
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(render_report(res))
    print(json.dumps({s: r["variants"]["t30"]["decision"] for s, r in res["seasons"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
