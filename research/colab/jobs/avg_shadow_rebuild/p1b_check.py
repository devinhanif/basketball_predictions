# ruff: noqa: E501
"""Proof P1b (serving; docs/prereg/AVG_SHADOW_LIVE_2.md): CPU equals GPU on the SAME weights.

The notebook saved the 3 seed state dicts at four month blocks (``p1b/<YYYY-MM>/seed*.pt``: 2023-12, 2024-02, 2024-12, 2025-02; every predicted row has a
season label <= 2024). For each, the live CPU inference (``nba.daily.avg_shadow.infer.predict_bundle``, fp32, no autocast) scores that month's rows from the
SAME export arrays the GPU walk used; the GPU forecasts are the rebuild's OOF rows. PASS iff, on those months: median |dq| = 0 and the 99th percentile
|dq| <= 1 integer (quantiles on the integer grid), and the aggregate pts AVG minus FULL with the CPU forecasts is within 0.0005 of the GPU value.

    .venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/p1b_check.py --run <downloaded run folder> --export data/colab/avg_shadow_rebuild/seq_ensemble_rt_cur25.npz
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _world  # noqa: E402

from nba.daily.avg_shadow.infer import predict_bundle  # noqa: E402
from nba.eval.context_residual_eval import crps_from_quantiles  # noqa: E402
from nba.props.context_residual import to_integer_support  # noqa: E402
from research.eval import ensemble_eval as E  # noqa: E402
from research.eval import seq_full_mix_eval as S  # noqa: E402
from research.eval import workload_eval as WE  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
MONTHS = ("2023-12", "2024-02", "2024-12", "2025-02")
TOL_AGG = 0.0005
STATS = ("pts", "reb", "ast", "fg3m")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument(
        "--export",
        type=Path,
        required=True,
        help="the cur25 npz the notebook trained on (rows of seasons <= 2024 are the only ones used)",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "reports/prereg_avg_shadow_live/p1b.json")
    a = ap.parse_args(argv)
    ex = np.load(a.export, allow_pickle=False)
    keep = np.flatnonzero(ex["season"] <= _world.MAX_SEASON)
    arr = {
        k: ex[k][keep]
        for k in (
            "seq",
            "mask",
            "opp_step",
            "ctx",
            "player_id",
            "team_idx",
            "opp_idx",
            "base",
            "date_days",
            "game_id",
            "season",
        )
    }
    gpu = np.load(a.run / "oof_E2.npz", allow_pickle=True)
    gk = gpu["season"] <= _world.MAX_SEASON
    g_game, g_pid, g_q = (
        gpu["game_id"][gk].astype(str),
        gpu["player_id"][gk].astype(np.int64),
        gpu["q"][gk],
    )
    gidx = {(g, int(p)): i for i, (g, p) in enumerate(zip(g_game, g_pid, strict=True))}
    months = pd.PeriodIndex(arr["date_days"].astype("datetime64[D]"), freq="M").astype(str)
    cpu_q = np.full_like(g_q, np.nan)
    dq_all, per_month = [], {}
    for mo in MONTHS:
        rows = np.flatnonzero(months == mo)
        wdir = a.run / "p1b" / mo
        tmp = Path(tempfile.mkdtemp(prefix="avg_p1b_"))
        for s in (0, 1, 2):
            shutil.copy2(wdir / f"seed{s}.pt", tmp / f"seed{s}.pt")
        shutil.copy2(a.run / "vocab.json", tmp / "vocab.json")
        (tmp / "manifest.json").write_text(json.dumps({"seeds": [0, 1, 2]}))
        out = predict_bundle(
            tmp,
            {
                k: arr[k][rows]
                for k in (
                    "seq",
                    "mask",
                    "opp_step",
                    "ctx",
                    "player_id",
                    "team_idx",
                    "opp_idx",
                    "base",
                )
            },
        )
        pos = np.array(
            [gidx.get((str(arr["game_id"][r]), int(arr["player_id"][r])), -1) for r in rows]
        )
        ok = pos >= 0
        cpu_q[pos[ok]] = out["q"][ok]
        dq = np.abs(
            to_integer_support(out["q"][ok].reshape(-1, 19))
            - to_integer_support(g_q[pos[ok]].reshape(-1, 19))
        )
        dq_all.append(dq.ravel())
        per_month[mo] = {
            "rows": int(ok.sum()),
            "median_abs_dq": float(np.median(dq)),
            "p99_abs_dq": float(np.percentile(dq, 99)),
            "max_abs_dq": float(dq.max()),
        }
    dq_flat = np.concatenate(dq_all)
    sel = np.isfinite(cpu_q).all(axis=(1, 2))
    # aggregate pts AVG - FULL on those rows: GPU forecasts vs CPU forecasts through the same stored world and arms
    with WE.heavy():
        w, seq_gpu, tmpw = _world.world(a.run)
        b = w["base"]["pts"]
        seq_cpu = {"pts": seq_gpu["pts"].copy()}
        key = {
            (str(g), int(p)): i
            for i, (g, p) in enumerate(zip(b["game_id"], b["player_id"], strict=True))
        }
        for i in np.flatnonzero(sel):
            j = key.get((g_game[i], int(g_pid[i])))
            if j is not None:
                seq_cpu["pts"][j] = E.ext19(cpu_q[i, 0:1, :])[0]
        rows_sel = np.array(
            sorted(
                {
                    key[(g_game[i], int(g_pid[i]))]
                    for i in np.flatnonzero(sel)
                    if (g_game[i], int(g_pid[i])) in key
                }
            )
        )
        agg = {}
        for name, sq in (("gpu", seq_gpu["pts"]), ("cpu", seq_cpu["pts"])):
            arms = S.build_arms("pts", b, w["date_days"], w["ctx"], {"E2": sq})
            d = crps_from_quantiles(arms["AVG"].astype(float), b["y"]) - crps_from_quantiles(
                arms["FULL"].astype(float), b["y"]
            )
            agg[name] = float(d[rows_sel].mean())
    res: dict[str, Any] = {
        "months": per_month, "median_abs_dq": float(np.median(dq_flat)), "p99_abs_dq": float(np.percentile(dq_flat, 99)),
        "agg_avg_minus_full_gpu": agg["gpu"], "agg_avg_minus_full_cpu": agg["cpu"], "agg_abs_diff": abs(agg["gpu"] - agg["cpu"]), "tolerance_agg": TOL_AGG,
        "n_rows": int(len(rows_sel)), "run": str(a.run),
    }  # fmt: skip
    res["pass"] = bool(
        res["median_abs_dq"] == 0 and res["p99_abs_dq"] <= 1 and res["agg_abs_diff"] <= TOL_AGG
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
