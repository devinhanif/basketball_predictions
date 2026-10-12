# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""Which inputs matter, per prop (descriptive, seasons 2023-2024 only; no claim, no production change).

Drop-column importance for the production context-residual model: for each feature GROUP the model is refit
walk-forward (production month blocks, 2022 = warm-up, same config, seed 0) WITHOUT that group's columns, and
the integer-support CRPS of every OOF row is compared with the full model's, paired by row, clustered by game.
A refit (not a row shuffle) is used on purpose: a plain shuffle of one column across rows carries future,
player-level information (docs/reviews/ledger_audit_2026-10-12.md, placebo defects).

    .venv/bin/python -W ignore -m research.eval.feature_significance arms   # heavy: refits, cached per (stat, block, arm)
    .venv/bin/python -W ignore -m research.eval.feature_significance score  # light: tables + results.json + report

Season 2025 is never loaded (``WE.assert_seasons``); ``nba.duckdb`` is opened read-only by the loaders.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    month_blocks,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualModel,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.metrics import _clustered_boot_means
from research.eval import pi_recent_role_eval as PR
from research.eval import workload_eval as WE

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/ops/feature_significance_cache"
OUT_JSON = ROOT / "reports/feature_significance_2026-10-12.results.json"
R_COLS = PR.R_COLS
N_BOOT = 2000
MIN_TRAIN = 1500
FLOOR = 0.005  # practical floor for "matters": removal costs >= 0.005 integer CRPS


def groups_for(stat: str) -> dict[str, list[str]]:
    """Feature groups (near-duplicate columns removed together). Every column of ``stat_feature_names(stat)`` is in exactly one group."""
    g: dict[str, list[str]] = {
        "own_avg_windows": [f"m5_{stat}", f"m40_{stat}", f"m100_{stat}", f"role_gap_{stat}"],
        "own_m10_all_stats": [f"m10_{s}" for s in PROP_STATS],
        "own_rate_spread": [f"rate_{stat}", f"sd10_{stat}"],
        "minutes_avgs": ["min5", "min10", "min40", "min_gap", "min_gap10"],
        "starter_flags": ["starter10", "starter40", "last_starter"],
        "games_history": ["n_prior", "n_season", "gap_days"],
        "role_change_flags": ["since_flag", "flag_dir", "since_team"],
        "rest_b2b": ["rest_days", "b2b", "opp_rest_days", "opp_b2b"],
        "season_timing": ["team_game_no"],
        "elo_margin": ["exp_margin", "abs_margin"],
        "team_r20": ["r20_pts_for", "r20_pts_against"],
        "opp_r20_and_allowed": ["opp_r20_pts_for", "opp_r20_pts_against", f"opp_allow_{stat}"],
        "injury_vacated": ["n_out_rot", "vac_min", "has_report", f"vac_{stat}"],
        "position": ["pos_code"],
        "home": ["is_home_f"],
    }
    allc = stat_feature_names(stat)
    flat = [c for v in g.values() for c in v]
    if sorted(flat) != sorted(allc) or len(set(flat)) != len(flat):
        raise AssertionError(
            f"groups do not partition the production features: {set(allc) ^ set(flat)}"
        )
    return g


def arms_for(stat: str) -> dict[str, tuple[list[str], int]]:
    """arm -> (feature names, lightgbm seed). full, one drop per group, +recent-role, and a second-seed full model (refit-noise reference)."""
    full = stat_feature_names(stat)
    out: dict[str, tuple[list[str], int]] = {"full": (full, 0), "full_seed1": (full, 1)}
    for name, cols in groups_for(stat).items():
        out[f"drop:{name}"] = ([c for c in full if c not in cols], 0)
    out["add:recent_role"] = ([*full, *R_COLS], 0)
    return out


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_frame() -> tuple[pl.DataFrame, Any]:
    fr, cfg, _aux = PR.build_frame()
    WE.assert_seasons(fr)
    return fr, dataclasses.replace(
        cfg, n_jobs=2
    )  # LightGBM is deterministic=True; fewer threads on the shared box


def run_arms(max_blocks: int | None = None, stats: tuple[str, ...] = PROP_STATS) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    fr, cfg = build_frame()
    for stat in stats:
        sf = stat_frame(fr, stat)
        test_all = sf.filter(pl.col("season").is_in(list(TEST_SEASONS)))
        blocks = month_blocks(test_all)
        arms = arms_for(stat)
        imp_path = CACHE / f"gain_{stat}.json"
        imps: dict[str, dict[str, float]] = (
            json.loads(imp_path.read_text()) if imp_path.exists() else {}
        )
        for bi, (start, end) in enumerate(blocks):
            if max_blocks is not None and bi >= max_blocks:
                break
            block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
                pl.col("season").is_in(list(TEST_SEASONS))
            )
            train = sf.filter(pl.col("game_date") < start).sort(
                ["game_date", "game_id", "player_id"]
            )
            if block.is_empty() or train.height < MIN_TRAIN:
                continue
            y = block["y"].to_numpy()
            t0 = time.time()
            n_new = 0
            for arm, (names, seed) in arms.items():
                p = CACHE / f"{stat}_{start}_{arm.replace(':', '-')}.npz"
                if p.exists():
                    continue
                model = ContextResidualModel(stat, dataclasses.replace(cfg, seed=seed), names).fit(
                    train
                )
                _, q = model.predict(block, CRPS_TAUS)
                qi = to_integer_support(q)
                payload: dict[str, np.ndarray] = {
                    "crps": crps_from_quantiles(qi.astype(float), y).astype(np.float32)
                }
                if arm == "full":
                    payload.update(
                        qi=qi.astype(np.int16),
                        y=y.astype(np.int16),
                        gid=block["game_id"].to_numpy().astype(str),
                        pid=block["player_id"].to_numpy().astype(np.int64),
                        season=block["season"].to_numpy().astype(np.int16),
                    )
                    imps[str(start)] = model.importance_
                np.savez(p, **payload)
                n_new += 1
            if imps:
                imp_path.write_text(json.dumps(imps))
            _log(
                f"{stat} block {bi + 1}/{len(blocks)} {start} n={block.height} train={train.height} new_fits={n_new} {time.time() - t0:.0f}s"
            )


def load_stat(stat: str) -> dict[str, Any]:
    arms = arms_for(stat)
    starts = sorted({p.name.split("_")[1] for p in CACHE.glob(f"{stat}_*_full.npz")})
    cols: dict[str, list[np.ndarray]] = {a: [] for a in arms}
    meta: dict[str, list[np.ndarray]] = {k: [] for k in ("gid", "pid", "season", "y")}
    for s in starts:
        paths = {a: CACHE / f"{stat}_{s}_{a.replace(':', '-')}.npz" for a in arms}
        if not all(p.exists() for p in paths.values()):
            continue
        for a, p in paths.items():
            z = np.load(p)
            cols[a].append(z["crps"].astype(float))
            if a == "full":
                for k in meta:
                    meta[k].append(z[k])
    return {
        "crps": {a: np.concatenate(v) for a, v in cols.items()},
        **{k: np.concatenate(v) for k, v in meta.items()},
        "n_blocks": len(meta["gid"]),
    }


def score_stat(stat: str) -> dict[str, Any]:
    d = load_stat(stat)
    full = d["crps"]["full"]
    gid = d["gid"]
    rows: list[dict[str, Any]] = []
    rng_seed = 20261012
    for arm, c in d["crps"].items():
        if arm == "full":
            continue
        delta = c - full
        b = _clustered_boot_means(delta, gid, N_BOOT, np.random.default_rng(rng_seed))
        rows.append(
            {
                "arm": arm,
                "delta": float(delta.mean()),
                "lo": float(np.quantile(b, 0.025)),
                "hi": float(np.quantile(b, 0.975)),
                "p": boot_p_value(delta, gid, N_BOOT, rng_seed),
            }
        )
    fam = [r for r in rows if r["arm"] != "full_seed1"]
    adj = bh_adjust(np.array([r["p"] for r in fam]))
    for r, a in zip(fam, adj, strict=True):
        r["q_bh"] = float(a)
        r["significant"] = bool(a < 0.05)
    noise = next(r for r in rows if r["arm"] == "full_seed1")
    imp_path = CACHE / f"gain_{stat}.json"
    gains: dict[str, float] = {}
    if imp_path.exists():
        per_block = list(json.loads(imp_path.read_text()).values())
        gains = {n: float(np.mean([b[n] for b in per_block])) for n in per_block[0]}
    grp = groups_for(stat)
    gain_by_group = {g: float(sum(gains.get(c, 0.0) for c in cols)) for g, cols in grp.items()}
    return {
        "stat": stat,
        "n_rows": int(len(full)),
        "n_games": int(len(np.unique(gid))),
        "n_blocks": d["n_blocks"],
        "full_crps": float(full.mean()),
        "rows": sorted(fam, key=lambda r: -r["delta"]),
        "refit_noise": noise,
        "gain_by_group": gain_by_group,
        "gain_by_column": gains,
        "groups": grp,
    }


def run_score() -> dict[str, Any]:
    res = {s: score_stat(s) for s in PROP_STATS if list(CACHE.glob(f"{s}_*_full.npz"))}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=1))
    for s, r in res.items():
        print(
            f"== {s}: n={r['n_rows']} games={r['n_games']} blocks={r['n_blocks']} full CRPS {r['full_crps']:.4f} refit-noise {r['refit_noise']['delta']:+.4f}"
        )
        for x in r["rows"]:
            print(
                f"  {x['arm']:<28} {x['delta']:+.4f} [{x['lo']:+.4f},{x['hi']:+.4f}] q={x['q_bh']:.3f} {'SIG' if x['significant'] else ''}"
            )
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["arms", "score"])
    ap.add_argument("--max-blocks", type=int, default=None)
    ap.add_argument("--stats", default=",".join(PROP_STATS))
    a = ap.parse_args(argv)
    if a.stage == "arms":
        run_arms(a.max_blocks, tuple(a.stats.split(",")))
    else:
        run_score()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
