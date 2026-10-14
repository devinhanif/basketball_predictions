# ruff: noqa: E501
"""Monte-Carlo sensitivity of G1 (descriptive): the same 2024 rows and the same R, other base draws and more draws."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import stage_b as B  # noqa: E402

from nba.props import combo_copula as C  # noqa: E402
from nba.props import combo_g1 as G  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    for k in ("--a", "--db", "--out"):
        ap.add_argument(k, required=True)
    for k in ("--a2", "--a3", "--a4", "--a5"):
        ap.add_argument(k, default="none")
    args = ap.parse_args()
    w = B.World(args)
    z = np.load(Path(args.out) / "R_chosen.npz")
    w.set_head({"blk": str(z["head"][0]), "stl": str(z["head"][1])})
    w.set_choice({s: str(v) for s, v in zip(B.STATS, z["choice"], strict=True)})
    rep = np.flatnonzero(w.reg & (w.season == B.REPORT))
    pid = w.keys["player_id"].to_numpy()
    combos = {c: C.COMBOS[c] for c in ("pra", "pa")}
    for n_draws, seed in ((10_000, C.BASE_SEED), (10_000, 1), (10_000, 2), (40_000, 3)):
        base = C.base_normals(n_draws=n_draws, seed=seed)
        g = C.joint_grids(w.sub(rep), z["pooled"], base, combos=combos)
        for c in combos:
            r = G.g1(g[c], w.y[c][rep], w.day[rep], w.gid[rep], pid[rep])
            print(
                f"N={n_draws} seed={seed} {c}: q10 {r['cover'][0.1]:.4f} q20 {r['cover'][0.2]:.4f} ok={r['ok']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
