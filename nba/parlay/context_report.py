# ruff: noqa: E501
"""Run the context-copula sizing end to end and write ``reports/parlay/context_copula_sizing.{json,md}``.

    uv run python -W ignore -m nba.parlay.context_report \
        --data /path/to/data/scratch/context_copula --out reports/parlay --config configs/parlay.yaml

``--data`` is the output directory of ``nba.parlay.context_data`` (2020-22 walk-forward PIT frames). Read-only on
everything except ``--out``. Nothing here is frozen, no model is touched, no price is read, no stake is suggested.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.special import ndtr

from nba.parlay import context_corr as C
from nba.parlay import context_money as M
from nba.parlay.config import load_config
from nba.parlay.ev import fee_per_contract

#: Costs the context shift has to beat, SUPPLIED data with provenance (docs/TEST_LEDGER.md rows; the Kalshi fee is read
#: from the verified config, never typed here). ``cost`` = loss per unit staked at the model's own fair probability.
COST_REFERENCES: list[dict[str, Any]] = [
    {
        "name": "single-leg alternate lines, sportsbook (ALT_LINE_70, T489)",
        "cost": 0.070,
        "source": "ledger T489: pooled ROI -0.070 [-0.095, -0.044], n 4,546 rungs",
    },
    {
        "name": "single-leg DraftKings alternate ladders (ALT_LADDER_HIST, T491)",
        "cost": 0.057,
        "source": "ledger T491: pooled ROI -0.057 [-0.062, -0.053], n 76,109 rungs",
    },
    {
        "name": "cross-game 2-leg parlay of 70%+ overs (ALT_PARLAY_X, T490)",
        "cost": 0.129,
        "source": "ledger T490: ge70 2-leg ROI -0.129 [-0.188, -0.069], n 3,337 parlays (pooled cells -0.122 to -0.222)",
    },
    {
        "name": "retail 2-leg hold, marketing figure (unverified)",
        "cost": 0.07,
        "source": "docs/research/lines_everywhere_2026-10-14.md B3: 6-8% for two legs; 15-25% for four-leg SGPs",
    },
]

#: Post-hoc ceiling cells (chosen after the broad table, to see how large the largest plausible shift is).
NARROW_CELLS = (
    (
        "pts-ast: assist leader OUT, player is a top-2 assister vs assist leader present",
        "pts",
        "ast",
    ),
    (
        "pts-reb: scoring leader OUT, player is a top-2 scorer vs scoring leader present",
        "pts",
        "reb",
    ),
    (
        "pts-ast: scoring leader OUT, player is a top-2 scorer vs scoring leader present",
        "pts",
        "ast",
    ),
    ("pts-ast: points AND assists leaders OUT with VAC48, vs VAC < 24", "pts", "ast"),
    ("pts-reb: points AND assists leaders OUT with VAC48, vs VAC < 24", "pts", "reb"),
)


def _narrow_masks(panel: C.Panel) -> list[tuple[np.ndarray, np.ndarray]]:
    return [
        ((panel.lead["ast"] == 1) & (panel.rank["ast"] <= 2), panel.lead["ast"] == 0),
        ((panel.lead["pts"] == 1) & (panel.rank["pts"] <= 2), panel.lead["pts"] == 0),
        ((panel.lead["pts"] == 1) & (panel.rank["pts"] <= 2), panel.lead["pts"] == 0),
        ((panel.tv48 == 1) & (panel.lead["ast"] == 1) & (panel.lead["pts"] == 1), panel.tv == 0),
        ((panel.tv48 == 1) & (panel.lead["ast"] == 1) & (panel.lead["pts"] == 1), panel.tv == 0),
    ]


def marginal_check(data: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in C.STATS:
        d = pl.read_parquet(data / f"pit_{s}.parquet")
        u = ndtr(d["z"].to_numpy())
        out.append(
            {
                "stat": s,
                "n": d.height,
                "by_season": {int(k): int(v) for k, v in d.group_by("season").len().iter_rows()},
                "pit_below": {str(t): float((u < t).mean()) for t in (0.1, 0.2, 0.8, 0.9)},
                "mean_p_line": float(d["p_line"].mean()),  # type: ignore[arg-type]
                "mean_hit": float(d["hit"].mean()),  # type: ignore[arg-type]
            }
        )
    return out


def run(data: Path, out_dir: Path, config: Path) -> dict[str, Any]:
    t0 = time.time()
    panel = C.load_panel(data)
    rows = C.run_classes(panel)
    placebo = []
    for sd in range(3):
        pr = C.run_classes(C.shuffle_labels(panel, 1000 + sd), n_boot=300)
        cells = [c for r in pr for c in r["cells"]]
        placebo.append(
            {
                "seed": 1000 + sd,
                "cells": len(cells),
                "z_ci_excludes_0": int(sum(c["corr_z"]["excludes_0"] for c in cells)),
                "phi_ci_excludes_0": int(sum(c["phi"]["excludes_0"] for c in cells if "phi" in c)),
                "max_abs_phi_diff": float(
                    max((abs(c["phi"]["diff"]) for c in cells if "phi" in c), default=0.0)
                ),
            }
        )
    cells_all = [c for r in rows for c in r["cells"]]
    narrow = []
    for (name, a, b), (m1, m0) in zip(NARROW_CELLS, _narrow_masks(panel), strict=True):
        narrow.append({"name": name, **M.narrow_cell(panel, m1, m0, a, b)})
    money = M.money_scenarios(rows)
    fee = load_config(config).fee
    price = 0.28
    fee_c = fee_per_contract(price, fee, contracts=100)
    fee_note: dict[str, Any] = {
        "fee_config": fee.name,
        "fee_verified": fee.verified,
        "coefficient": fee.coefficient,
        "price": price,
        "fee_per_contract_100_lot": fee_c,
        "fee_share_of_price": M.fee_share(price, fee_c),
    }
    costs = [{**c, "required_lift": M.required_lift(float(c["cost"]))} for c in COST_REFERENCES]
    costs.append(
        {
            "name": "Kalshi taker fee at a 28-cent contract, 100-lot order (verified config)",
            "cost": fee_note["fee_share_of_price"],
            "source": f"configs/parlay.yaml fee_model {fee.name} (verified={fee.verified}); excludes the bid/ask spread",
            "required_lift": M.required_lift(float(fee_note["fee_share_of_price"])),
        }
    )
    res: dict[str, Any] = {
        "seasons": [2020, 2021, 2022],
        "n_player_games": panel.n,
        "n_games": panel.n_games,
        "blowout_cuts_abs_elo_margin": panel.cuts,
        "marginals": marginal_check(data),
        "classes": rows,
        "cells_total": len(cells_all),
        "cells_z_ci_excludes_0": int(sum(c["corr_z"]["excludes_0"] for c in cells_all)),
        "cells_phi_ci_excludes_0": int(
            sum(c["phi"]["excludes_0"] for c in cells_all if "phi" in c)
        ),
        "max_abs_phi_diff_observed": float(
            max(abs(c["phi"]["diff"]) for c in cells_all if "phi" in c)
        ),
        "placebo": placebo,
        "narrow_cells": narrow,
        "money": money,
        "costs": costs,
        "fee": fee_note,
        "max_rel_swing": float(max(abs(m["rel_ctx_vs_ref"]) for m in money)),
        "max_rel_swing_upper_ci": float(max(abs(m["rel_ci_hi_vs_ref"]) for m in money)),
        "max_narrow_rel_swing": float(max(abs(n["joint_rel_change"]) for n in narrow)),
        "wall_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "context_copula_sizing.json").write_text(json.dumps(res, indent=1))
    (out_dir / "context_copula_sizing.md").write_text(render(res))
    return res


def _pc(x: float, nd: int = 1) -> str:
    return f"{100 * x:+.{nd}f}%"


def render(res: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# Context and cross-leg correlation: sizing on 2020-22 (2026-10-14)\n")
    L.append(
        "SIZING, not a test. Nothing is frozen, no rule exists, no model or live path is touched, no price is read, no stake is suggested. "
        "Outcomes used: seasons 2020, 2021, 2022 only (walk-forward, production-equivalent marginals). Seasons 2023-24 (the select/report seasons of any later rule) and 2025 (frozen holdout) were never loaded. "
        "Code: `nba/parlay/context_data.py` (PIT residuals), `context_corr.py` (pair estimation), `context_money.py`, `context_report.py` (this file).\n"
    )
    L.append("## Question\n")
    L.append(
        "A feature that is real but does not improve a player's marginal forecast is redundant for the marginal. For a parlay a different quantity matters: does context change the correlation BETWEEN legs' residuals? "
        "The production joint (`nba/parlay/joint.py`: one correlation per relation x stat pair, pooled; `nba/props/combo_copula.py`: one 6x6 matrix) is NOT conditioned on any context. This file measures how much it would matter if it were.\n"
    )
    L.append("## Data\n")
    L.append(
        f"{res['n_player_games']:,} played player-games over {res['n_games']:,} games (labels are `games.season`; 2022 = 2022-23). "
        "Marginals: the production props model (`context_residual`, same config, same month-block refit, integer support), trained only on earlier games from 2019-20 on; randomised PIT -> normal score `z`; `hit` = outcome >= the integer line whose model probability is nearest 0.5.\n"
    )
    L.append(
        "| stat | n | P(PIT<.1) | P(PIT<.2) | P(PIT<.8) | P(PIT<.9) | mean p(line) | mean hit |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for m in res["marginals"]:
        pb = m["pit_below"]
        L.append(
            f"| {m['stat']} | {m['n']:,} | {pb['0.1']:.3f} | {pb['0.2']:.3f} | {pb['0.8']:.3f} | {pb['0.9']:.3f} | {m['mean_p_line']:.3f} | {m['mean_hit']:.3f} |"
        )
    L.append(
        "\nLower tails are a touch heavy (0.111-0.128 at 0.10), the same pattern as production in 2023-24 (docs/reviews, minutes_spread_eda). Marginals are production-like, not better.\n"
    )
    L.append("## How the contexts were defined\n")
    L.append(
        "Declared with the task before any cell was read: `vac24`, `lead_out`, `compete`, `blowout`, `fav`. Added after the first table was read: `vac48` (VAC24 is true for 75% of rows) and `lead_x_compete`, plus the narrow ceiling cells below. The first table was also run without stratum centring (not shown; the centred version moves some `phi` differences by 0.01 to 0.015).\n"
        "- `vac24` / `vac48`: team's OUT rotation players' as-of minutes >= 24 (about one starter) / >= 48, vs < 24 (report required; the production `vac_min`). "
        "VAC24 is true for about 75% of rows, so its complement is small and special.\n"
        "- `lead_out`: the team's highest as-of per-36 rate in the pair's stat(s) (rotation players with long-run minutes >= 20) belongs to an OUT player. Per-possession rates are not in the feature frame; per-36 minutes is the stand-in.\n"
        "- `compete` (teammates only): both legs are the team's top-3 by as-of `m10` in the stat, ranked among the players who played (a mild look-ahead on who plays; the legs are conditional on playing anyway). `lead_x_compete` stacks the two.\n"
        f"- `blowout`: top tercile (|Elo margin| >= {res['blowout_cuts_abs_elo_margin']['abs_margin_hi']:.1f}) vs bottom tercile (<= {res['blowout_cuts_abs_elo_margin']['abs_margin_lo']:.1f}) of the pre-game Elo margin (the only pre-game spread we have for 2020-22).\n"
        "- `fav`: the player's team is the Elo favourite vs underdog (margin / total classes).\n"
        "Pair classes: same player (pts-reb, pts-ast, ast-reb, pts-fg3m); teammates same stat (4) and cross stat (3, both orientations); opponents same stat (4); player x own-team margin (4); player x game total (4). "
        "Statistics: normal-score correlation of the PIT residuals `corr_z` (copula scale; randomised-PIT noise attenuates it for count stats, so differences are lower bounds) and `phi` of the median-line hit indicators (what a parlay pays on). "
        "Both are centred INSIDE each stratum so a context-specific marginal bias is not counted as dependence. CI: 1,000 resamples of whole games.\n"
    )
    L.append("## Result 1: how many cells move, and by how much\n")
    L.append(
        f"{res['cells_total']} (class x context) cells with >= 300 pairs and >= 40 games on each side. Game-clustered CI of the correlation difference excludes 0 in {res['cells_z_ci_excludes_0']} cells for `corr_z` and {res['cells_phi_ci_excludes_0']} for `phi`. "
        "Placebo (the same procedure with the team-game context labels permuted across team-games, player ranks re-drawn at random, 3 shuffles at 300 resamples): "
        + "; ".join(
            f"seed {p['seed']}: {p['z_ci_excludes_0']} / {p['phi_ci_excludes_0']}"
            for p in res["placebo"]
        )
        + " cells excluding 0 (z / phi) out of "
        f"{res['placebo'][0]['cells']}, largest placebo |phi diff| {max(p['max_abs_phi_diff'] for p in res['placebo']):.3f} (noise alone reaches that). "
        f"So there is real context structure beyond noise in the normal-score correlations (about 36 cells against about 5 under the placebo), and it is small: the largest |phi difference| over all cells is {res['max_abs_phi_diff_observed']:.3f}, and phi differences under about 0.035 are inside the placebo range. The cells overlap and 100+ were examined; read them as a map, not as findings.\n"
    )
    cells: list[tuple[float, str, dict[str, Any]]] = []
    for r in res["classes"]:
        for c in r["cells"]:
            if "phi" in c and (c["phi"]["excludes_0"] or c["corr_z"]["excludes_0"]):
                cells.append((abs(c["phi"]["diff"]), r["class"], c))
    cells.sort(key=lambda t: -t[0])
    L.append("Cells where either CI excludes 0, largest |phi difference| first (top 24):\n")
    L.append(
        "| class | context | n pairs ctx / ref | games ctx / ref | corr_z ctx / ref | diff [95% CI] | phi ctx / ref | diff [95% CI] |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for _, cl, c in cells[:24]:
        z, p = c["corr_z"], c["phi"]
        L.append(
            f"| {cl} | {c['ctx']} | {c['n_pairs_ctx']:,} / {c['n_pairs_ref']:,} | {c['n_games_ctx']:,} / {c['n_games_ref']:,} | "
            f"{z['ctx']:+.3f} / {z['ref']:+.3f} | {z['diff']:+.3f} [{z['ci'][0]:+.3f}, {z['ci'][1]:+.3f}] | "
            f"{p['ctx']:+.3f} / {p['ref']:+.3f} | {p['diff']:+.3f} [{p['ci'][0]:+.3f}, {p['ci'][1]:+.3f}] |"
        )
    L.append(
        "\nReadings. (0) VAC24 alone moves little: same-player points-rebounds +0.020 and points-assists +0.017 (VAC48: +0.021, +0.026), everything else within +-0.01. (1) Leader OUT raises the same-player points-assists, points-rebounds and assists-rebounds co-movement by phi +0.02 to +0.05 (shared minutes and usage move together). "
        "(2) Teammates who are both top-3 in a stat: points(A)-assists(B) co-move MORE (stars rise together), assists-assists co-move LESS (the competition the hypothesis predicted), by phi +0.02 to +0.03 and -0.025. "
        "(3) Same-stat teammates: points/rebounds/threes show no usable shift in any context (|phi diff| <= 0.011, CIs span 0). (4) Opponents: nothing (|phi diff| <= 0.008, every CI spans 0). "
        "(5) Blowout: nothing for any pair class (largest |phi diff| 0.014; every phi CI spans 0 except teammates rebounds-rebounds at -0.009 [-0.016, -0.001]). "
        "(6) Favourite vs underdog: a player's rebounds are tied to his own team's result for underdogs (phi +0.072) and barely for favourites (+0.021); points -0.020, assists/threes ~0. "
        "(7) 'Shared vacated usage makes teammates compete' is not what the data show for the leader-out context: points-points and assists-assists among teammates become slightly LESS negative when the leader is out (z +0.013, +0.014), phi unchanged.\n"
    )
    L.append("## Result 2: post-hoc ceiling cells\n")
    L.append(
        "Chosen after the table, to see how large the largest plausible shift is (each is one look among many, and a cell picked for its size is shrunk in practice; WOWY's coefficient fell inside exactly the slices chosen for size). Same-player `phi` with stratum centring.\n"
    )
    L.append("| cell | n ctx / ref | phi ctx / ref | diff [95% CI] | 2-leg joint prob change |")
    L.append("|---|---|---|---|---|")
    for n in res["narrow_cells"]:
        L.append(
            f"| {n['name']} | {n['n_ctx']:,} / {n['n_ref']:,} | {n['phi_ctx']:+.3f} / {n['phi_ref']:+.3f} | {n['diff']:+.3f} [{n['ci'][0]:+.3f}, {n['ci'][1]:+.3f}] | {_pc(n['joint_rel_change'])} |"
        )
    L.append(
        "\nThe Trae-Young cell (assist leader OUT, the remaining top-2 assister's points and assists) shows no change at all (phi +0.010, CI spans 0). "
        "The largest swing is the points-rebounds pair of a top-2 scorer when the scoring leader is out: they DECOUPLE (phi -0.080).\n"
    )
    L.append("## Result 3: in money (legs at p = 0.5, Gaussian copula, Sheppard map from phi)\n")
    L.append(
        "| parlay | independent | pooled copula | reference group | context group | fair odds pooled -> context | context vs pooled | context vs reference | at far end of every CI |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|")
    for m in res["money"]:
        L.append(
            f"| {m['scenario']} | {m['p_independent']:.3f} | {m['p_pooled']:.4f} ({_pc(m['rel_pooled_vs_independent'])} vs indep.) | {m['p_ref']:.4f} | {m['p_ctx']:.4f} | "
            f"{m['fair_odds_pooled']:.2f} -> {m['fair_odds_ctx']:.2f} | {_pc(m['rel_ctx_vs_pool'])} | {_pc(m['rel_ctx_vs_ref'])} | {_pc(m['rel_ci_hi_vs_ref'])} |"
        )
    L.append(
        "\nWhat a parlay costs, from data already in the repo (cost = loss per unit at the model's own fair probability; required lift = how far the true joint probability must exceed the price-implied one to offset it):\n"
    )
    L.append("| cost reference | cost | required lift | source |")
    L.append("|---|---|---|---|")
    for c in res["costs"]:
        L.append(
            f"| {c['name']} | {100 * c['cost']:.1f}% | {_pc(c['required_lift'])} | {c['source']} |"
        )
    L.append(
        f"\nThe largest swing between the context group and its reference group over the seven typical parlays is {_pc(res['max_rel_swing'])} "
        f"({_pc(res['max_rel_swing_upper_ci'])} if every pair sits at the far end of its CI at once); the largest post-hoc ceiling cell is {_pc(res['max_narrow_rel_swing'])}. "
        f"Expected log loss saved per parlay by conditioning (inside the context segment; `log_loss_gain_ctx_segment` in the JSON): between {min(m['log_loss_gain_ctx_segment'] for m in res['money']):.0e} and {max(m['log_loss_gain_ctx_segment'] for m in res['money']):.0e} nats, below anything the 2023-24 joint evaluation could resolve (Gaussian vs independence there was -0.00053 [-0.00106, -0.00003] on 15,798 parlays).\n"
    )
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Context-conditioned copula sizing (read-only; writes under --out only)."
    )
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="reports/parlay")
    ap.add_argument("--config", default="configs/parlay.yaml")
    a = ap.parse_args(argv)
    res = run(Path(a.data), Path(a.out), Path(a.config))
    print(
        json.dumps(
            {
                k: res[k]
                for k in (
                    "cells_total",
                    "cells_z_ci_excludes_0",
                    "cells_phi_ci_excludes_0",
                    "max_abs_phi_diff_observed",
                    "max_rel_swing",
                    "max_rel_swing_upper_ci",
                    "max_narrow_rel_swing",
                )
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
