# ruff: noqa: E501
"""ERROR_FEEDBACK minimum-data gate (docs/prereg/ERROR_FEEDBACK.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.error_feedback_gate

Builds a player's own recent forecast errors from the STORED walk-forward A0 forecasts (``reports/lower_tail``; each
forecast was made by a model trained strictly before its month block, so it never saw its own row) and measures:
coverage on the evaluation rows, coverage on the TRAINING rows a walk-forward refit would need (2022 has no stored
forecast), the descriptive persistence of residuals (slope of tonight's residual on the mean of the previous 5; a look at
the comparator's errors, disclosed), and the outcome-free expected integer-CRPS gain if the forecast were shifted by that
slope times the recent mean residual. Per-row sd/deff from the stored FULL-minus-A0 deltas. Nothing is fitted or scored.
Writes ``reports/prereg_error_feedback/gate.json``.

Columns (as-of, for player i in game g dated D): over i's stored A0 rows (played games) dated strictly before D,
latest first: ``ef_res_l3/l5/l10`` = mean of (y - A0 integer median), ``ef_pit_l5`` = mean mid-PIT on the integer grid
(F(y-1) + F(y)) / 2, ``ef_n`` = number of such rows (capped at 10). NaN when there is no earlier row.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

import numpy as np

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_error_feedback/gate.json"
FLOOR = -0.005
WINDOWS = (3, 5, 10)


def mid_pit(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Mid-PIT of integer outcomes under an integer quantile grid on equal-mass taus: (F(y-1) + F(y)) / 2."""
    below = (q < y[:, None]).mean(axis=1)
    at_or_below = (q <= y[:, None]).mean(axis=1)
    return np.asarray(0.5 * (below + at_or_below))


def build_error_feedback(
    pid: np.ndarray, date: np.ndarray, res: np.ndarray, pit: np.ndarray
) -> dict[str, np.ndarray]:
    """Per row: means of ``res`` over the player's last 3/5/10 rows dated STRICTLY before the row's date, mean ``pit``
    over the last 5, and the count (<= 10). Rows on the same date never feed each other."""
    n = len(pid)
    out = {f"ef_res_l{w}": np.full(n, np.nan) for w in WINDOWS}
    out["ef_pit_l5"] = np.full(n, np.nan)
    out["ef_n"] = np.zeros(n)
    order = np.lexsort((np.arange(n), date, pid))
    hist: dict[int, list[tuple[Any, float, float]]] = defaultdict(list)
    for i in order:
        h = [x for x in hist[int(pid[i])] if x[0] < date[i]][-10:]
        if h:
            r = np.array([x[1] for x in h])
            for w in WINDOWS:
                out[f"ef_res_l{w}"][i] = r[-w:].mean()
            out["ef_pit_l5"][i] = np.mean([x[2] for x in h][-5:])
            out["ef_n"][i] = len(h)
        hist[int(pid[i])].append((date[i], float(res[i]), float(pit[i])))
    return out


def main() -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid, load_components
    from nba.props.context_residual import to_integer_support
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    c = load_components("pts", RF.LT_DIR)
    if int(c["row_season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in stored components")
    pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
    gid = c["row_gid"].astype(str)
    dmap = dict(games.select("game_id", "game_date").rows())
    date = np.array([np.datetime64(dmap[g]) for g in gid])
    pid = c["row_pid"].astype(np.int64)
    season = c["row_season"]
    y = c["row_y"]
    q0 = RF.arm_qi("pts", c, pic, pic["row_pi"]).astype(float)
    med = q0[:, q0.shape[1] // 2]
    res = y - med
    pit = mid_pit(q0, y)
    f = build_error_feedback(pid, date, res, pit)
    out: dict[str, Any] = {"measured_at": C.now_cdt(), "seasons": list(C.SEASONS)}
    # coverage on evaluation rows, and on the training rows a walk-forward refit would see before each month block
    played22 = C.query(
        con,
        "SELECT count(*) AS n FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024 AND g.season = 2022 AND s.minutes > 0",
    )["n"][0]
    blk = c["row_blk"]
    cov_blocks = []
    for b in np.unique(blk):
        before = blk < b
        defined = int((before & ~np.isnan(f["ef_res_l5"])).sum())
        cov_blocks.append(
            {
                "block": int(b),
                "train_rows_approx": int(before.sum()) + int(played22),
                "train_rows_with_feature": defined,
                "share": defined / (int(before.sum()) + int(played22)),
            }
        )
    out["training_coverage_by_block"] = cov_blocks
    # descriptive persistence (comparator residuals): slope of res on ef_res_l5, per season, game-clustered CI
    per: dict[str, Any] = {}
    grid = arm_grid("mixture", c, pic, "pts")
    q1 = RF.arm_qi(
        "pts", RF.load_heads("pts", "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
    )
    d = PR.crps_int(q1, y) - PR.crps_int(q0, y)
    rng = np.random.default_rng(0)
    for s in C.SEASONS:
        m = season == s
        ok = m & ~np.isnan(f["ef_res_l5"]) & (f["ef_n"] >= 3)
        x, r = f["ef_res_l5"][ok], res[ok]
        slope = float(np.cov(x, r)[0, 1] / np.var(x, ddof=1))
        ug, inv = np.unique(gid[ok], return_inverse=True)
        boots = []
        for _ in range(500):
            w = np.bincount(rng.integers(0, len(ug), len(ug)), minlength=len(ug))[inv].astype(float)
            mx, mr = np.average(x, weights=w), np.average(r, weights=w)
            boots.append(
                np.average((x - mx) * (r - mr), weights=w) / np.average((x - mx) ** 2, weights=w)
            )
        lo, hi = np.percentile(boots, [2.5, 97.5])
        b = slope * np.nan_to_num(f["ef_res_l5"])
        sel = ok & (b != 0)
        gain = np.zeros(len(y))
        gain[sel] = C.expected_crps_gain(q0[sel], to_integer_support(grid[sel] + b[sel, None]))
        cg = C.cluster_stats(d[m], gid[m])
        e = float(-gain[m].mean())
        per[str(s)] = {
            "rows": int(m.sum()),
            "rows_with_feature(ef_n>=3)": int(ok.sum()),
            "share_with_feature": float(ok.sum() / m.sum()),
            "ef_res_l5_sd": float(np.std(x)),
            "descriptive_slope_res_on_l5": slope,
            "slope_ci95_game": [float(lo), float(hi)],
            "expected_delta_all_rows": e,
            "primary": C.sizing_row(
                "all rows, floor -0.005", cg["sd_row"], max(1.0, cg["deff"]), m.sum(), FLOOR, e
            ),
        }
        print(f"[{time.time() - t0:5.1f}s] {s}: slope {slope:.4f}", flush=True)
    out["sizing"] = per
    gate: list[dict[str, Any]] = []
    for s in C.SEASONS:
        p = per[str(s)]["primary"]
        gate.append(
            {
                "check": f"{s} half-width <= 0.005",
                "value": p["halfwidth95"],
                "pass": p["hw_le_floor"],
            }
        )
        gate.append(
            {
                "check": f"{s} power >= 0.5 at the descriptive persistence",
                "value": p["power_one_season"],
                "pass": p["power_one_season"] >= 0.5,
            }
        )
    first23 = [x for x in cov_blocks if x["block"] == int(np.min(blk))][0]["share"]
    gate.append(
        {
            "check": "first 2023 block: training rows with the feature >= 0.5",
            "value": first23,
            "pass": first23 >= 0.5,
        }
    )
    out["gate"] = gate
    out["gate_pass"] = all(x["pass"] for x in gate)
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"gate": gate, "sizing": per}, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
