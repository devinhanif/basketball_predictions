# ruff: noqa: E501
"""Write configs/combo_stocks_shadow.yaml from the committed select.json and the P1 reference (review B1, B5c).

The config is the canonical home of R (4 dp); the P1 reference is built with THAT R, not the full-precision one, because live reads the config.
    python reports/prereg_combo_stocks_shadow/make_config_and_p1.py --a ... --db ... (same arguments as stage_b.py) --out <select dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import stage_b as B  # noqa: E402

from nba.props import combo_copula as C  # noqa: E402

SEL = HERE / "select_2023"
CFG = ROOT / "configs/combo_stocks_shadow.yaml"
REF = HERE / "p1_reference.npz"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_config() -> None:
    d = json.loads((SEL / "select.json").read_text())
    r = d["matrices_2023"]["pooled_latent"]
    fl = d["floors_and_n_required"]
    base_sha = hashlib.sha256(C.base_normals().tobytes()).hexdigest()
    rows = "\n".join("    - [" + ", ".join(f"{v:.4f}" for v in row) + "]" for row in r)
    text = (
        f"""# COMBO_STOCKS_SHADOW model config (docs/prereg/COMBO_STOCKS_SHADOW.md, DRAFT). Every value was fixed on the 2023 select phase
# (reports/prereg_combo_stocks_shadow/select_2023/select.json) before the 2024 report phase was run on this design. This file is hashed into
# the freeze (the rule text carries its sha256); R to 4 dp below is canonical. A change to any value is a new rule id.
version: combo_stocks_v2_joint
# ONE within-player joint (Devin, 2026-10-14, before the freeze): a single Gaussian copula over all six stat marginals; every combo is a sum
# of the SAME simulated games of a player-game (fixed common draws), so PRA, PR, PA, RA and blk+stl are mutually coherent.
stat_order: [pts, reb, ast, fg3m, blk, stl]
targets: [blk, stl, pra, pr, pa, ra, bs]
combos:
  pra: [pts, reb, ast]
  pr: [pts, reb]
  pa: [pts, ast]
  ra: [reb, ast]
  bs: [blk, stl]
marginals:
  pts: props_context_residual_lt      # production lower-tail mixture row (the A0 of the other shadows), p_ge_full
  reb: props_context_residual         # production primary row, p_ge_full
  ast: props_context_residual
  fg3m: props_context_residual        # enters the joint for coherence; no scored target uses it
  blk: nb_count_head                  # nba.props.count_head.NBCountModel on nba.props.stock_features columns
  stl: nb_count_head
nb_head:
  objective: poisson
  dispersion_bins: 4
  # learner settings = configs/context_residual.yaml (n_estimators 200, lr 0.04, 15 leaves, min_child 100, seed 0)
copula:
  estimator: rpit                     # randomised-PIT correlation matched by simulation (nba.props.combo_copula.estimate_latent_corr)
  scheme: pooled                      # one matrix; by-position had a 1,583-row group, by-starter gained 0.0008 < 0.005
  shrinkage: none                     # none; the only repair is nearest_corr (eigenvalue floor 1e-6), shift {d["matrices_2023"]["pooled_info"]["max_abs_psd_shift"]:.1e}, smallest eigenvalue of R below
  n_draws: 10000
  base_seed: 20261014                 # whitened + antithetic base normals (combo_copula.base_normals)
  base_sha256: {base_sha}
  # latent correlation, all of 2023 regular season (n = {d["n_2023_regular"]:,} played rows with n_prior >= 5), stat_order above
  R:
{rows}
recent:                               # the RECENT comparator (all seven targets)
  window: 40                          # last 40 played games
  halflife: 10.0                      # production recency weights
  min_prior: 5
floors:                               # max(0.005, 1% of RECENT's 2023 regular-season integer CRPS)
"""
        + "\n".join(
            f"  {t}: {fl[t]['floor']:.4f}" for t in ("blk", "stl", "pra", "pr", "pa", "ra", "bs")
        )
        + """
n_min: 3000                           # per target at the final look (also the G1 minimum)
n_interim: 1500
g1:                                   # rule 6(d), implemented by nba.props.combo_g1 (NOT nba.eval.avg_shadow_live.calibration)
  row_order: [game_date, game_id, player_id]
  pit: randomized_pit(q, y, seed=0), one call per target over that target's scored rows
  required_taus: [0.10, 0.20, 0.80, 0.90]
  tolerance: 0.02
  cov80: [0.75, 0.85]
  relative_bias: 0.05
  min_rows: 3000
select_json: reports/prereg_combo_stocks_shadow/select_2023/select.json
p1_reference: reports/prereg_combo_stocks_shadow/p1_reference.npz
"""
    )
    CFG.write_text(text)
    r_arr = np.array(yaml.safe_load(text)["copula"]["R"], dtype=float)
    ev = float(np.linalg.eigvalsh(r_arr).min())
    CFG.write_text(
        text.replace(
            "smallest eigenvalue of R below", f"smallest eigenvalue of the 4-dp R {ev:.3f}"
        )
    )
    print("config written; min eigenvalue", ev, "sha256", sha(CFG))


def write_p1(args: argparse.Namespace) -> None:
    cfg = yaml.safe_load(CFG.read_text())
    r = np.array(cfg["copula"]["R"], dtype=float)
    w = B.World(args)
    z = np.load(SEL / "R_chosen.npz")
    w.set_head({"blk": str(z["head"][0]), "stl": str(z["head"][1])})
    w.set_choice({s: str(v) for s, v in zip(B.STATS, z["choice"], strict=True)})
    rep = np.flatnonzero(w.reg & (w.season == B.REPORT))
    pick = rep[::128][:200]
    sub = w.sub(pick)
    base = C.base_normals()
    exp = C.joint_grids(sub, r, base)
    ind = C.joint_grids(sub, np.eye(len(B.STATS)), base)
    out: dict[str, np.ndarray] = {f"cdf_{s}": sub[s].astype(np.float64) for s in B.STATS}
    out.update({f"expected_{c}": exp[c] for c in C.COMBOS})
    out.update({f"expected_indep_{c}": ind[c] for c in C.COMBOS})
    out.update({f"y_{c}": w.y[c][pick].astype(np.int16) for c in C.COMBOS})
    out["R"] = r
    out["game_id"] = w.gid[pick].astype(str)
    out["player_id"] = w.keys["player_id"].to_numpy()[pick]
    out["base_sha256"] = np.array(hashlib.sha256(base.tobytes()).hexdigest())
    np.savez_compressed(REF, **out)
    print("p1 reference written", REF, len(pick), "rows; sha256", sha(REF))


def _main() -> None:
    ap = argparse.ArgumentParser()
    for k in ("--a", "--db"):
        ap.add_argument(k, required=True)
    for k in ("--a2", "--a3", "--a4", "--a5"):
        ap.add_argument(k, default="none")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    write_config()
    write_p1(args)


LOCK = Path("/Users/devin/Downloads/nba-prediction/data/ops/heavy.lock")


def main() -> None:
    from research.eval import workload_eval as WE

    with WE.WorkloadLock(LOCK):  # the cooperative lock: take it or wait, never skip it
        _main()


if __name__ == "__main__":
    main()
