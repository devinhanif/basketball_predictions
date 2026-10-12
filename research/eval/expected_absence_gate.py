# ruff: noqa: E501
"""EXPECTED_ABSENCE minimum-data gate (docs/prereg/EXPECTED_ABSENCE.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.expected_absence_gate

Checks that the as-of P(play) R predictions (stored walk-forward, ``data/ops/recent_role_cache/scores.npz``,
T455/T458) exist for the players the latest T-60 official report lists as not OUT, weights the coverage by
the injury-Elo player value, and sizes the rule outcome-free:

* comparator = stored production injury-Elo OOF (``data/injury_elo_realtip/oof_predictions.parquet``,
  real-tip gating, v2), per-game log loss;
* hypothesised arm (for sizing only, never scored) = the comparator's logit with the OUT/DOUBTFUL terms
  replaced by ``b * d_exp`` where ``d_exp`` = away-minus-home sum over listed players of (1 - P(play)) x
  value / 10 (OUT: P(play) = 0), and ``b`` = production's final OUT coefficient; the expected gain if that
  arm were the truth is KL(p_arm || p_prod) per game (an optimistic ceiling: it assumes the coefficient is
  right and the refinement is all signal);
* per-game sd and design effect from the stored injury-Elo-minus-MOV-Elo per-game deltas (the largest
  injury-term contrast we have; a refinement of that term moves less).
No arm is fitted or scored against an outcome. Writes ``reports/prereg_expected_absence/gate.json``.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_expected_absence/gate.json"
SCORES = C.ROOT / "data/ops/recent_role_cache/scores.npz"
OOF = C.ROOT / "data/injury_elo_realtip/oof_predictions.parquet"
RESULTS = C.ROOT / "data/injury_elo_realtip/results.json"
FLOOR = -0.002  # win log loss, injury-term refinement (INJURY_ELO_T30's floor); -0.004 (ELO_HISTORY_WINDOW) also reported
ALT_FLOOR = -0.004
CLOSE = 0.2  # close-game slice |p - 0.5| < 0.2
STATUS_FALLBACK = {"doubtful": 0.05, "questionable": 0.55, "probable": 0.9, "available": 0.95}


def expected_absence(
    listed: pl.DataFrame,
    games: pl.DataFrame,
    p_play: pl.DataFrame,
    fallback: dict[str, float] = STATUS_FALLBACK,
) -> pl.DataFrame:
    """Per game: away-minus-home sums of value/10 for OUT (d_out), DOUBTFUL (d_doubt) and the expected-absent
    value d_exp = sum (1 - P(play)) x value / 10 over every listed player (OUT -> P(play) 0).

    ``listed``: game_id, player_id, status, team_id, value (as-of; ``latest_t60`` statuses).
    ``p_play``: game_id, player_id, p (as-of P(play)); a listed non-OUT player without a p takes
    ``fallback[status]`` and is counted in ``n_fallback``."""
    x = listed.join(p_play, on=["game_id", "player_id"], how="left").join(
        games.select("game_id", "home_team", "away_team"), on="game_id"
    )
    fb = pl.col("status").replace_strict(fallback, default=None, return_dtype=pl.Float64)
    x = x.with_columns(
        pl.when(pl.col("status") == "out")
        .then(0.0)
        .otherwise(pl.coalesce(pl.col("p"), fb))
        .alias("p_used"),
    )
    x = x.with_columns(
        ((pl.col("status") != "out") & pl.col("p").is_null()).alias("is_fallback"),
        pl.when(pl.col("team_id") == pl.col("away_team"))
        .then(1.0)
        .when(pl.col("team_id") == pl.col("home_team"))
        .then(-1.0)
        .otherwise(0.0)
        .alias("side"),
    )
    v = pl.col("value") * pl.col("side") / 10.0
    return x.group_by("game_id").agg(
        (v * (pl.col("status") == "out")).sum().alias("d_out"),
        (v * (pl.col("status") == "doubtful")).sum().alias("d_doubt"),
        (v * (1.0 - pl.col("p_used").fill_null(1.0))).sum().alias("d_exp"),
        (pl.col("is_fallback") & (pl.col("value") > 0)).sum().alias("n_fallback"),
        ((pl.col("status") != "out") & (pl.col("value") > 0)).sum().alias("n_listed_valued"),
    )


def main() -> dict[str, Any]:
    from nba.models.injury_elo import (
        ValueConfig,
        asof_values,
        build_value_state,
        league_rate_by_date,
        load_stats_frame,
    )

    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    status = C.latest_t60(C.load_report(con), games)
    stats = load_stats_frame(con, C.MAX_SEASON)
    vcfg = ValueConfig()
    state = build_value_state(stats, vcfg)
    league = league_rate_by_date(stats)
    pairs = status.join(games.select("game_id", "game_date", "season"), on="game_id").filter(
        pl.col("season").is_in(C.SEASONS)
    )
    pq = pairs.select("game_id", pl.col("player_id").cast(pl.Int64), "game_date")
    vals = asof_values(pq, state, league, vcfg)
    pairs = pairs.with_columns(pl.col("player_id").cast(pl.Int64))
    listed = pairs.join(
        vals.select("game_id", "player_id", "team_id", "value"),
        on=["game_id", "player_id"],
        how="left",
    ).with_columns(pl.col("value").fill_null(0.0))
    print(f"[{time.time() - t0:5.1f}s] listed rows {listed.height}", flush=True)
    z = np.load(SCORES)
    pp = pl.DataFrame(
        {
            "game_id": z["gid"].astype(str),
            "player_id": z["pid"].astype(np.int64),
            "p": z["p_R"],
            "season": z["season"],
        }
    )
    if int(np.max(z["season"])) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in the P(play) cache")
    pp = pp.filter(pl.col("season").is_in(C.SEASONS)).drop("season")
    out: dict[str, Any] = {"measured_at": C.now_cdt(), "seasons": list(C.SEASONS)}
    # coverage of P(play) R for listed not-OUT players, by status, count- and value-weighted
    cov = (
        listed.filter(pl.col("status") != "out")
        .join(pp, on=["game_id", "player_id"], how="left")
        .with_columns(pl.col("p").is_not_null().alias("has_p"))
    )
    out["coverage"] = {
        f"{s}/{st}": {
            "n": int(n),
            "share_with_p": float(h),
            "value_share_with_p": float(vh) if vh is not None else None,
            "n_value_gt0": int(nv),
        }
        for s, st, n, h, vh, nv in cov.join(games.select("game_id", "season"), on="game_id")
        .group_by("season", "status")
        .agg(
            pl.len().alias("n"),
            pl.col("has_p").mean().alias("h"),
            ((pl.col("value") * pl.col("has_p")).sum() / pl.col("value").sum()).alias("vh"),
            (pl.col("value") > 0).sum().alias("nv"),
        )
        .sort("season", "status")
        .rows()
    }
    # missing-p reasons: not in the box (no row of tonight's game), or < 5 prior played games
    box = C.load_box(con).select("game_id", "player_id")
    miss = cov.filter(~pl.col("has_p")).join(
        box.with_columns(pl.lit(True).alias("in_box")), on=["game_id", "player_id"], how="left"
    )
    out["missing_p_in_box_share"] = (
        float(np.mean(miss["in_box"].fill_null(False).to_numpy())) if miss.height else 0.0
    )
    ea = expected_absence(listed, games, pp)
    oof = pl.read_parquet(OOF).filter(pl.col("season").is_in(C.SEASONS))
    prod = oof.filter(pl.col("model") == "rung0_injury_elo").select(
        "game_id", pl.col("p").alias("p_prod"), "season"
    )
    mov = oof.filter(pl.col("model") == "rung0_mov_elo").select(
        "game_id", pl.col("p").alias("p_mov")
    )
    coef = json.loads(RESULTS.read_text())["variants"]["injury_elo"]["final_coef"]
    b, g = float(coef[0]), float(coef[1])
    f = (
        prod.join(mov, on="game_id")
        .join(ea, on="game_id", how="left")
        .fill_null(0.0)
        .join(games.select("game_id", "game_date", "home_pts", "away_pts"), on="game_id")
    )
    pprod = np.clip(f["p_prod"].to_numpy(), 1e-6, 1 - 1e-6)
    zprod = np.log(pprod / (1 - pprod))
    zarm = (
        zprod - b * f["d_out"].to_numpy() - g * f["d_doubt"].to_numpy() + b * f["d_exp"].to_numpy()
    )
    parm = 1.0 / (1.0 + np.exp(-zarm))
    bg = C.bernoulli_gain(pprod, parm)
    y = (f["home_pts"].to_numpy() > f["away_pts"].to_numpy()).astype(float)
    pm = np.clip(f["p_mov"].to_numpy(), 1e-6, 1 - 1e-6)

    def ll(p: np.ndarray) -> np.ndarray:
        return np.asarray(-(y * np.log(p) + (1 - y) * np.log(1 - p)))

    d_proxy = ll(pprod) - ll(pm)  # stored, already-frozen contrast (T128): only its spread is used
    season = f["season"].to_numpy()
    dates = f["game_date"].cast(pl.Utf8).to_numpy()
    close = np.abs(pprod - 0.5) < CLOSE
    per: dict[str, Any] = {}
    for s in C.SEASONS:
        m = season == s
        cd = C.cluster_stats(d_proxy[m], dates[m])
        sd_bern = float(math.sqrt(bg["var"][m].mean()))
        e = float(-bg["kl"][m].mean())
        mc = m & close
        e_close = float(-bg["kl"][mc].mean())
        sd_close = float(np.std(d_proxy[mc], ddof=1))
        moved = np.abs(zarm - zprod)[m]
        per[str(s)] = {
            "games": int(m.sum()),
            "games_with_fallback_value": int((f["n_fallback"].to_numpy()[m] > 0).sum()),
            "games_with_listed_valued_not_out": int((f["n_listed_valued"].to_numpy()[m] > 0).sum()),
            "logit_change_mean_abs": float(moved.mean()),
            "logit_change_share_gt_0.05": float((moved > 0.05).mean()),
            "prob_change_mean_abs": float(np.abs(parm - pprod)[m].mean()),
            "proxy_sd_game_injury_minus_mov": float(np.std(d_proxy[m], ddof=1)),
            "bernoulli_sd_game_if_arm_true": sd_bern,
            "deff_date": cd["deff"],
            "primary": C.sizing_row(
                "all games, floor -0.002",
                float(np.std(d_proxy[m], ddof=1)),
                max(1.0, cd["deff"]),
                m.sum(),
                FLOOR,
                e,
            ),
            "primary_alt_floor": C.sizing_row(
                "all games, floor -0.004",
                float(np.std(d_proxy[m], ddof=1)),
                max(1.0, cd["deff"]),
                m.sum(),
                ALT_FLOOR,
                e,
            ),
            "primary_bernoulli_sd": C.sizing_row(
                "all games, floor -0.002, sd if the arm were true",
                sd_bern,
                max(1.0, cd["deff"]),
                m.sum(),
                FLOOR,
                e,
            ),
            "close_slice": C.sizing_row(
                "|p-0.5|<0.2, floor -0.002", sd_close, 1.0, mc.sum(), FLOOR, e_close
            ),
        }
    out["coef_used"] = {"b_out": b, "g_doubt": g}
    out["sizing"] = per
    gate: list[dict[str, Any]] = []
    for s in C.SEASONS:
        p = per[str(s)]["primary"]
        gate.append(
            {
                "check": f"{s} half-width <= 0.002",
                "value": p["halfwidth95"],
                "pass": p["hw_le_floor"],
            }
        )
        gate.append(
            {
                "check": f"{s} power >= 0.5 at the KL ceiling",
                "value": p["power_one_season"],
                "pass": p["power_one_season"] >= 0.5,
            }
        )
    qcov = [
        v["value_share_with_p"] for k, v in out["coverage"].items() if k.endswith("/questionable")
    ]
    gate.append(
        {
            "check": "P(play) R covers >= 90% of questionable value",
            "value": min(qcov),
            "pass": min(qcov) >= 0.9,
        }
    )
    out["gate"] = gate
    out["gate_pass"] = all(x["pass"] for x in gate)
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
