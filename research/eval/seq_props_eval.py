"""Pre-registered evaluation of the GPU sequence props model (``seq_props``).

PRE-REGISTRATION (written before any Colab run; also in docs/SEQ_PROPS.md)
--------------------------------------------------------------------------
Rows: season-2024 PLAYED rows present in BOTH the seq OOF and the production
``context_residual`` OOF (inner join on game_id, player_id, stat). Season 2025 is
never read. Production's rows need >= 5 prior played games, so the cold-start slice
here is 5-19 prior games; seq rows outside the intersection are only counted.

Score: 19-level grid CRPS (``research.stack.scoring.grid_crps``) for both models, so the
comparison is like for like. Delta = mean(score_candidate - score_production),
negative is better; 95% game-clustered bootstrap CI (``paired_score_delta_ci``,
cluster = game_id); two-sided clustered-bootstrap p-value.

Three families, each with Benjamini-Hochberg over its 4 stats (pts, reb, ast, fg3m):
  A. seq alone vs context_residual.
  B. equal-weight blend (0.5/0.5 Vincentized grid average) vs context_residual.
  C. learned blend (``research.stack.router`` softmax gate, strictly walk-forward in
     30-day blocks on 2024 only) vs context_residual, on rows the router scored.
A candidate in a family is KEPT for a stat only if ALL hold:
  1. point delta <= -0.005 (CRPS floor) and CI upper bound < 0;
  2. BH-adjusted q <= 0.05 within the family;
  3. no slice (cold-start <20 career games vs not, first-15 team games vs rest,
     starter vs bench, report-listed teammate OUT vs none) with n >= 300 regresses
     by more than +0.01 CRPS.
Router-candidate status: B or C may be KEPT even if A fails ("seq alone loses but
adds to the blend"). MAE, mean bias (CI) and 80% interval coverage are reported
descriptively for A (and B's mean); they do not enter the keep rule.
A negative result (no stat kept in any family) is a valid, reportable outcome.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba.eval.context_residual_eval import bh_adjust, boot_p_value
from nba.props.metrics import interval_coverage, mean_bias_ci, paired_score_delta_ci
from research.stack.scoring import grid_crps

STATS = ("pts", "reb", "ast", "fg3m")
TEST_SEASON = 2024
FROZEN_SEASON = 2025
CRPS_FLOOR = 0.005
SLICE_MAX_REGRESSION = 0.01
SLICE_MIN_N = 300
N_BOOT = 2000
COLD_START_GAMES = 20
Q10, Q90 = 1, 17  # indices of tau 0.10 / 0.90 in the 19-level grid
KEYS = ["game_id", "player_id"]


def _grid(col: pd.Series) -> np.ndarray:
    return np.vstack([np.asarray(v, dtype=float) for v in col])


def align(
    seq: pd.DataFrame, prod: pd.DataFrame, meta: pd.DataFrame, stat: str
) -> dict[str, Any] | None:
    """Rows of ``stat`` common to both OOFs (season 2024), with labels and slices."""
    if (seq["season"] >= FROZEN_SEASON).any() or (prod["season"] >= FROZEN_SEASON).any():
        raise ValueError("season 2025 is the frozen holdout; it must not be evaluated here")
    a = seq[(seq["target"] == stat) & (seq["season"] == TEST_SEASON)]
    b = prod[(prod["target"] == stat) & (prod["season"] == TEST_SEASON)]
    cols = ["game_id", "player_id", "mean", "q_grid"]
    df = a[[*cols, "game_date", "season"]].merge(
        b[cols], on=KEYS, suffixes=("_s", "_p"), validate="one_to_one"
    )
    m = meta[meta["season"] == TEST_SEASON]
    mcols = [*KEYS, f"y_{stat}", "n_prior", "team_game_no", "starter10", "has_report", "n_rep_out"]
    df = df.merge(m[mcols], on=KEYS, validate="one_to_one")
    if df.empty:
        return None
    df = df.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    tm = np.where(
        df["has_report"] == 0,
        "no_report",
        np.where(df["n_rep_out"] > 0, "teammate_out", "none_out"),
    )
    return {
        "df": df,
        "y": df[f"y_{stat}"].to_numpy(dtype=float),
        "qs": _grid(df["q_grid_s"]),
        "qp": _grid(df["q_grid_p"]),
        "ms": df["mean_s"].to_numpy(dtype=float),
        "mp": df["mean_p"].to_numpy(dtype=float),
        "gid": df["game_id"].to_numpy(),
        "slices": {
            "career": np.where(df["n_prior"] < COLD_START_GAMES, "cold_lt20", "established"),
            "phase": np.where(df["team_game_no"] < 15, "first15", "rest"),
            "role": np.where(df["starter10"] >= 0.5, "starter", "bench"),
            "teammate": tm,
        },
    }


def slice_deltas(
    cs: np.ndarray, cp: np.ndarray, gid: np.ndarray, slices: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, lab in slices.items():
        for g in sorted(set(lab.tolist())):
            m = lab == g
            if m.sum() < 50:
                continue
            ci = paired_score_delta_ci(cs[m], cp[m], n_boot=500, cluster_ids=gid[m])
            out.append(
                {
                    "slice": f"{name}={g}",
                    "n": int(m.sum()),
                    "delta": ci.point,
                    "lo": ci.lo,
                    "hi": ci.hi,
                }  # fmt: skip
            )
    return out


def summarize(
    q_cand: np.ndarray,
    q_prod: np.ndarray,
    y: np.ndarray,
    gid: np.ndarray,
    slices: dict[str, np.ndarray],
    mean_cand: np.ndarray | None = None,
    mean_prod: np.ndarray | None = None,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    cs, cp = grid_crps(q_cand, y), grid_crps(q_prod, y)
    ci = paired_score_delta_ci(cs, cp, n_boot=n_boot, cluster_ids=gid)
    sl = slice_deltas(cs, cp, gid, slices)
    out: dict[str, Any] = {
        "n": int(len(y)), "n_games": int(len(np.unique(gid))),
        "crps_cand": float(cs.mean()), "crps_prod": float(cp.mean()),
        "delta": [ci.point, ci.lo, ci.hi], "p_value": boot_p_value(cs - cp, gid, n_boot),
        "cov80_cand": interval_coverage(q_cand[:, Q10], q_cand[:, Q90], y),
        "cov80_prod": interval_coverage(q_prod[:, Q10], q_prod[:, Q90], y),
        "slices": sl,
        "slice_regressions": [
            s["slice"] for s in sl if s["n"] >= SLICE_MIN_N and s["delta"] > SLICE_MAX_REGRESSION
        ],
    }  # fmt: skip
    if mean_cand is not None and mean_prod is not None:
        bc = mean_bias_ci(mean_cand, y, n_boot=n_boot, cluster_ids=gid)
        bp = mean_bias_ci(mean_prod, y, n_boot=n_boot, cluster_ids=gid)
        out.update(
            mae_cand=float(np.abs(mean_cand - y).mean()),
            mae_prod=float(np.abs(mean_prod - y).mean()),
            bias_cand=[bc.point, bc.lo, bc.hi],
            bias_prod=[bp.point, bp.lo, bp.hi],
        )
    return out


def apply_rule(summaries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The pre-registered keep rule for one family (BH over its stats)."""
    stats = list(summaries)
    adj = bh_adjust(np.array([float(summaries[s]["p_value"]) for s in stats]))
    verdict: dict[str, dict[str, Any]] = {}
    for s, q in zip(stats, adj, strict=True):
        pt, _lo, hi = summaries[s]["delta"]
        checks = {
            "floor": bool(pt <= -CRPS_FLOOR),
            "ci_excludes_0": bool(hi < 0),
            "bh_q": bool(q <= 0.05),
            "no_slice_regression": not summaries[s]["slice_regressions"],
        }
        verdict[s] = {"bh_adj_p": float(q), "checks": checks, "keep": all(checks.values())}
    return verdict


def learned_blend(al: dict[str, Any], stat: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Walk-forward softmax-gate blend of (production, seq) on 2024 rows; returns
    (scored mask, pooled grid). None if the router has too little history."""
    from research.stack.data import build_stack_data
    from research.stack.router import SoftmaxGate, WalkForwardConfig, walk_forward

    df = al["df"]
    frames = {
        "prod": df[["game_id", "player_id", "game_date", "season"]].assign(q_grid=list(al["qp"])),
        "seq": df[["game_id", "player_id", "game_date", "season"]].assign(q_grid=list(al["qs"])),
    }
    outcomes = df[["game_id", "player_id"]].assign(y=al["y"])
    ctx = df[["game_id", "player_id"]].assign(
        log_n_prior=np.log1p(df["n_prior"]), starter10=df["starter10"],
        n_rep_out=df["n_rep_out"], has_report=df["has_report"], team_game_no=df["team_game_no"],
    )  # fmt: skip
    data = build_stack_data(stat, frames, outcomes, ctx, max_season=TEST_SEASON)
    if len(data.y) != len(df):
        raise ValueError("stack alignment dropped rows")
    res = walk_forward(data, SoftmaxGate, WalkForwardConfig(block_days=30, min_train_rows=3000))
    if not res.scored.any():
        return None
    return res.scored, res.pooled


def evaluate(
    seq: pd.DataFrame, prod: pd.DataFrame, meta: pd.DataFrame, n_boot: int = N_BOOT
) -> dict[str, Any]:
    fam: dict[str, dict[str, dict[str, Any]]] = {"A_seq": {}, "B_equal_blend": {}, "C_learned": {}}
    coverage: dict[str, Any] = {}
    for stat in STATS:
        al = align(seq, prod, meta, stat)
        n_seq = int(((seq["target"] == stat) & (seq["season"] == TEST_SEASON)).sum())
        if al is None:
            continue
        coverage[stat] = {"seq_rows_2024": n_seq, "common_rows": int(len(al["y"]))}
        y, gid, sl = al["y"], al["gid"], al["slices"]
        fam["A_seq"][stat] = summarize(al["qs"], al["qp"], y, gid, sl, al["ms"], al["mp"], n_boot)
        qb = 0.5 * (al["qs"] + al["qp"])
        mb = 0.5 * (al["ms"] + al["mp"])
        fam["B_equal_blend"][stat] = summarize(qb, al["qp"], y, gid, sl, mb, al["mp"], n_boot)
        lb = learned_blend(al, stat)
        if lb is not None:
            sc, pooled = lb
            sub = {k: v[sc] for k, v in sl.items()}
            fam["C_learned"][stat] = summarize(
                pooled[sc], al["qp"][sc], y[sc], gid[sc], sub, n_boot=n_boot
            )
    verdicts = {f: apply_rule(s) for f, s in fam.items() if s}
    return {"coverage": coverage, "families": fam, "verdicts": verdicts}


def format_report(res: dict[str, Any]) -> str:
    lines = ["# seq_props evaluation (season 2024 played rows, vs context_residual)", ""]
    lines.append(f"Coverage: {json.dumps(res['coverage'])}")
    for f, summ in res["families"].items():
        head = (
            "| stat | n | games | CRPS cand | CRPS prod | delta [95% CI] | BH q "
            "| cov80 c/p | keep |"
        )
        lines += ["", f"## Family {f}", "", head, "|---|---|---|---|---|---|---|---|---|"]
        for s, r in summ.items():
            v = res["verdicts"][f][s]
            d = r["delta"]
            lines.append(
                f"| {s} | {r['n']} | {r['n_games']} | {r['crps_cand']:.4f} | {r['crps_prod']:.4f} "
                f"| {d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}] | {v['bh_adj_p']:.3f} "
                f"| {r['cov80_cand']:.3f}/{r['cov80_prod']:.3f} | {v['keep']} |"
            )
        for s, r in summ.items():
            if "mae_cand" in r:
                lines.append(
                    f"- {s}: MAE {r['mae_cand']:.3f} vs {r['mae_prod']:.3f}; bias "
                    f"{r['bias_cand'][0]:+.3f} [{r['bias_cand'][1]:+.3f},{r['bias_cand'][2]:+.3f}] "
                    f"vs {r['bias_prod'][0]:+.3f}"
                )
            for sl in r["slices"]:
                lines.append(
                    f"  - [{s}] {sl['slice']:<24} n={sl['n']:<6} d={sl['delta']:+.4f} "
                    f"[{sl['lo']:+.4f},{sl['hi']:+.4f}]"
                )
    kept = {f: [s for s, v in vd.items() if v["keep"]] for f, vd in res["verdicts"].items()}
    lines += ["", f"## Kept (stats passing the pre-registered rule): {json.dumps(kept)}"]
    if not any(kept.values()):
        lines.append("No family passed: negative result (no stat kept).")
    return "\n".join(lines)


def run(
    oof_path: str,
    prod_path: str = "reports/context_residual/oof_context_residual.parquet",
    meta_path: str = "data/colab/seq_props/seq_props_meta.parquet",
    out_dir: str = "reports/seq_props",
    write_store: bool = False,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    seq = pd.read_parquet(oof_path)
    prod = pd.read_parquet(prod_path)
    meta = pd.read_parquet(meta_path)
    res = evaluate(seq, prod, meta, n_boot)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    text = format_report(res)
    (out / "report.md").write_text(text)
    if write_store:
        from nba.stack.oof import write_oof

        w = seq[seq["season"] == TEST_SEASON].copy()
        write_oof(w, "seq_props", "v1")
    print(text)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered seq_props evaluation")
    ap.add_argument("--oof", required=True, help="oof_2024.parquet pulled from Colab")
    ap.add_argument("--prod", default="reports/context_residual/oof_context_residual.parquet")
    ap.add_argument("--meta", default="data/colab/seq_props/seq_props_meta.parquet")
    ap.add_argument("--out", default="reports/seq_props")
    ap.add_argument("--write-store", action="store_true")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    a = ap.parse_args(argv)
    run(a.oof, a.prod, a.meta, a.out, a.write_store, a.n_boot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
