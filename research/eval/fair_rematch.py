"""Fair rematch: sim (played-only, current-season rates) vs a FAIR season average.

PRE-REGISTRATION (written before the first run; mirrors section (e) of
docs/TEST_PLAN_2026-10-08b.md -- if the two ever disagree, the test plan wins).

Why: docs/DNP_AUDIT_2026-10-08.md found that DNP rows (minutes NULL, stats 0,
18.6% of rows) (1) drag the historical season-average baseline low on played
rows and (2) deflate the sim's shot-share / rebound / assist rates through
team-game denominators of games the player missed. The historical
"sim vs season-average" verdicts compared two differently-contaminated
estimands. This rematch gives each side its best-faith, DNP-aware form.

Design (fixed now):
- Scored season: 2024 only (never 2025, enforced). ~600 games sampled evenly
  across the 2024 schedule (deterministic linspace over sorted game ids),
  identical game set for every arm. Rates use the full prior history.
- Arms (stats pts/reb/ast):
    A  old sim as shipped (played_only OFF, equal-weight pooled rates).
    B  sim with played_only rates and current-season-only shot rates
       (TimeDecayConfig(carryover_decay_weight=1.0)). NOTE: the reb/ast
       builder has no time-decay path, so for reb/ast B is played_only with
       full-history rates; only the shot (points) rates are current-season-only.
    S  fair season average: played-only, exponentially recency-weighted
       (half-life 10 played games, the very same ``recency_weighted_dist``
       forward.py uses), career-to-date, strictly as-of.
- Minutes: the sims use projected minutes exactly as shipped (no actual
  minutes). Rosters are the rows present in the box score for the game (the
  documented set-membership limitation of the existing evals; identical across
  arms, so it cannot favour one arm over another).
- Scored on PLAYED rows only (minutes > 0) for the primary family. Separately,
  on ALL rows with the unconditional mixture: for B and S the conditional
  distribution is mixed with a point mass at 0 of weight 1 - p_play, where
  p_play = (played in prior 20 rows + 0.8*5) / (rows in prior 20 + 5), as-of;
  A is scored as shipped. The all-rows table is descriptive, not in the family.
- Metric: CRPS (lower is better), paired per player-game.

PRIMARY FAMILY (m = 3): B vs S on played rows, one cell per stat in
{pts, reb, ast}; delta = CRPS_B - CRPS_S. CIs clustered by game_id
(n_boot >= 2000, seed logged); p from the normal approximation to the CI
(se = (hi - lo) / 3.92); Benjamini-Hochberg q = 0.05 across the 3 cells.
DECISION RULE per stat:
    "B beats S"   iff CI upper < 0 AND BH-survives AND delta <= -0.005
    "S beats B"   iff CI lower > 0 AND BH-survives AND delta >= +0.005
    otherwise     "no verdict" (with MDE = 2.8 * se reported; if MDE > 0.010
                  the label is "underpowered, cannot conclude", never "no effect").
A player-clustered sensitivity CI is reported for each primary cell; a
verdict that flips under it is downgraded to PROVISIONAL (not in the family).
Descriptive only (outside the family, no verdicts): B vs A and A vs S on
played rows; all-rows (mixture) versions; and B vs S sliced by the played-only
SB bucket (ADI over games played, as-of, min_games=10).
Nothing here touches season 2025. Any positive finding is PROVISIONAL until
the forward evaluation (family FWD) replicates it.

ENTRY POINT (maintainer; read-only DB; ~10-20 min at n_sims=1000, 8GB box):

    uv run python -m research.eval.fair_rematch --db nba.duckdb --n-sims 1000 \\
        --sample-games 600 --out docs/fair_rematch_2024.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy import stats

from nba.features.possession_features import build_team_possession_rates
from nba.features.sb_classification import build_sb_classification
from nba.props.distributions import Distribution
from nba.props.metrics import ConfidenceInterval, crps_array, paired_score_delta_ci
from nba.props.minutes import build_minutes_features, predict_minutes
from research.eval.player_points_sim_eval import run_sim_vs_baseline_eval
from research.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
from research.eval.usage_redistribution_eval import benjamini_hochberg
from research.features.played_baseline import (
    FORWARD_RECENCY_HALFLIFE_GAMES,
    recency_played_baseline,
)
from research.features.time_decay import TimeDecayConfig

STATS = ("pts", "reb", "ast")
FORBIDDEN_SEASON = 2025
EFFECT_FLOOR = 0.005
Q = 0.05
_TAUS = (np.arange(1, 200) - 0.5) / 199


@dataclass
class FairRematchConfig:
    season: int = 2024
    sample_games: int = 600
    n_sims: int = 1000
    seed: int = 0
    n_boot: int = 2000
    halflife_games: float = FORWARD_RECENCY_HALFLIFE_GAMES
    min_bucket_games: int = 10


@dataclass
class FairRematchResult:
    config: dict[str, Any]
    cells: list[dict[str, Any]] = field(default_factory=list)
    runtime_s: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"config": self.config, "cells": self.cells, "runtime_s": self.runtime_s})

    def markdown(self) -> str:
        lines = [
            "| family | stat | subset | comparison | n | games | delta | lo | hi | q | verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in self.cells:
            q = "" if c.get("q") is None else f"{c['q']:.3f}"
            sub = c["subset"] + (f" / {c['bucket']}" if c.get("bucket") else "")
            lines.append(
                f"| {c['family']} | {c['stat']} | {sub} | {c['comparison']} | {c['n']} | "
                f"{c['n_games']} | {c['delta']:+.4f} | {c['lo']:+.4f} | {c['hi']:+.4f} | {q} | "
                f"{c['verdict']} |"
            )
        return "\n".join(lines)


# ----------------------------------------------------------------- helpers


def sample_game_ids(
    con: duckdb.DuckDBPyConnection, season: int, n: int, available: set[str]
) -> list[str]:
    """Evenly spaced, deterministic sample of ``season``'s games (sorted by
    date, id) restricted to games the feature tables cover."""
    if season == FORBIDDEN_SEASON:
        raise ValueError("season 2025 is the frozen holdout and must not be scored here")
    ids = [
        str(r[0])
        for r in con.execute(
            "SELECT game_id FROM games WHERE season = ? ORDER BY game_date, game_id", [season]
        ).fetchall()
        if str(r[0]) in available
    ]
    if len(ids) > n:
        pick = np.linspace(0, len(ids) - 1, n).round().astype(int)
        ids = [ids[i] for i in sorted(set(pick.tolist()))]
    return ids


def mixture_crps(base: list[Distribution], p_play: np.ndarray, y: np.ndarray) -> np.ndarray:
    """CRPS of ``(1 - p) * delta_0 + p * base`` via the same 199-tau quantile
    approximation as ``nba.props.metrics.crps_array`` (mixture quantile at tau
    is 0 for tau <= 1 - p, else base quantile at (tau - (1 - p)) / p)."""
    n = len(base)
    out = np.full(n, np.nan)
    for i, (d, p) in enumerate(zip(base, p_play, strict=True)):
        p0 = 1.0 - float(p)
        u = np.clip((_TAUS - p0) / max(1.0 - p0, 1e-12), 0.0, 1.0)
        if hasattr(d, "samples"):
            qb = np.quantile(d.samples, u)
        elif d.family == "normal":
            qb = stats.norm.ppf(u, loc=d.mean_, scale=d.std)  # type: ignore[attr-defined]
        else:
            qb = np.array([d.ppf(float(t)) for t in u])
        q = np.where(p0 >= _TAUS, 0.0, qb)
        pin = np.where(y[i] >= q, _TAUS * (y[i] - q), (1 - _TAUS) * (q - y[i]))
        out[i] = 2.0 * float(pin.mean())
    return out


def _played_and_pplay(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Per box-score row: ``played`` (minutes > 0; label-side, evaluation only)
    and as-of ``p_play`` from the PRIOR 20 rows only (1 PRECEDING frame)."""
    rows = con.execute(
        """
        WITH x AS (
            SELECT pgs.game_id, pgs.player_id, g.game_date,
                   CASE WHEN pgs.minutes IS NOT NULL AND pgs.minutes > 0 THEN 1.0 ELSE 0.0 END AS pl
            FROM player_game_stats pgs JOIN games g USING (game_id)
        )
        SELECT game_id, player_id, pl,
               COALESCE(SUM(pl) OVER w, 0.0) AS s, COUNT(pl) OVER w AS c
        FROM x
        WINDOW w AS (PARTITION BY player_id ORDER BY game_date, game_id
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
        """
    ).fetchall()
    df = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "pl": pl.Float64,
            "s": pl.Float64,
            "c": pl.Int64,
        },
        orient="row",
    )
    return df.select(
        "game_id",
        "player_id",
        (pl.col("pl") > 0.5).alias("played"),
        ((pl.col("s") + 0.8 * 5.0) / (pl.col("c") + 5.0)).alias("p_play"),
    )


def _cell(
    family: str,
    stat: str,
    subset: str,
    comparison: str,
    a: np.ndarray,
    b: np.ndarray,
    game_ids: np.ndarray,
    cfg: FairRematchConfig,
    bucket: str = "",
) -> dict[str, Any]:
    ci: ConfidenceInterval = paired_score_delta_ci(
        a, b, n_boot=cfg.n_boot, seed=cfg.seed, cluster_ids=game_ids
    )
    se = (ci.hi - ci.lo) / 3.92 if np.isfinite(ci.hi - ci.lo) else float("nan")
    z = ci.point / se if se and np.isfinite(se) and se > 0 else 0.0
    return {
        "family": family,
        "stat": stat,
        "subset": subset,
        "bucket": bucket,
        "comparison": comparison,
        "n": int(len(a)),
        "n_games": int(len(np.unique(game_ids))),
        "delta": float(ci.point),
        "lo": float(ci.lo),
        "hi": float(ci.hi),
        "se": se,
        "mde": 2.8 * se if np.isfinite(se) else float("nan"),
        "p": float(2 * (1 - stats.norm.cdf(abs(z)))),
        "q": None,
        "verdict": "descriptive",
    }


def decide_primary(cell: dict[str, Any]) -> str:
    """Pre-registered decision rule (see module docstring)."""
    q = cell["q"]
    if q is not None and q < Q and cell["hi"] < 0 and cell["delta"] <= -EFFECT_FLOOR:
        return "B beats S"
    if q is not None and q < Q and cell["lo"] > 0 and cell["delta"] >= EFFECT_FLOOR:
        return "S beats B"
    if cell["mde"] == cell["mde"] and cell["mde"] > 2 * EFFECT_FLOOR:
        return "no verdict (underpowered, cannot conclude)"
    return "no verdict"


# ------------------------------------------------------------------- driver


def run_fair_rematch(
    con: duckdb.DuckDBPyConnection, cfg: FairRematchConfig | None = None
) -> FairRematchResult:
    cfg = cfg or FairRematchConfig()
    if cfg.season == FORBIDDEN_SEASON:
        raise ValueError("season 2025 is the frozen holdout and must not be scored here")
    t0 = time.time()
    team_rates = build_team_possession_rates(con)
    ids = sample_game_ids(
        con, cfg.season, cfg.sample_games, set(team_rates["game_id"].unique().to_list())
    )
    feats = build_minutes_features(con)
    mdists = predict_minutes(feats)
    minutes_proj = feats.select(["game_id", "player_id"]).with_columns(
        pl.Series("projected_minutes", [d.mean() for d in mdists])
    )
    common: dict[str, Any] = {
        "n_sims": cfg.n_sims,
        "seed": cfg.seed,
        "n_boot": 50,
        "return_raw": True,
        "only_game_ids": ids,
        "team_rates": team_rates,
        "minutes_proj": minutes_proj,
    }
    td = TimeDecayConfig(carryover_decay_weight=1.0)

    pts_a = run_sim_vs_baseline_eval(con, **common)
    pts_b = run_sim_vs_baseline_eval(
        con, **common, played_only=True, use_time_decay=True, time_decay_config=td,
        return_dists=True,
    )  # fmt: skip
    ra_a = run_reb_ast_sim_vs_baseline_eval(con, **common)
    ra_b = run_reb_ast_sim_vs_baseline_eval(
        con, **common, played_only=True, return_dists=True
    )  # fmt: skip
    arms: dict[str, tuple[Any, Any]] = {
        "pts": (pts_a, pts_b),
        "reb": (ra_a[0], ra_b[0]),
        "ast": (ra_a[1], ra_b[1]),
    }
    pp = _played_and_pplay(con)
    result = FairRematchResult(config={**cfg.__dict__, "n_game_ids": len(ids)})
    primary: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []

    for stat in STATS:
        ra, rb = arms[stat]
        if ra.raw is None or rb.raw is None or rb.dists is None:
            continue
        keys = rb.raw.select("game_id", "player_id")
        a_df = ra.raw.select("game_id", "player_id", pl.col("crps_sim").alias("crps_a"))
        df = (
            keys.with_columns(
                crps_b=rb.raw["crps_sim"], y=rb.raw["y"], _i=pl.Series(range(keys.height))
            )
            .join(a_df, on=["game_id", "player_id"], how="inner")
            .join(pp, on=["game_id", "player_id"], how="left")
            .sort("_i")
        )
        idx = df["_i"].to_numpy()
        s_dists = recency_played_baseline(
            con, stat, df.select("game_id", "player_id"), cfg.halflife_games
        )
        y = df["y"].to_numpy().astype(float)
        crps_s = crps_array(list(s_dists), y)
        played = df["played"].fill_null(False).to_numpy()
        p_play = df["p_play"].fill_null(0.8).to_numpy()
        b_dists = [rb.dists[i] for i in idx]
        crps_b_mix = mixture_crps(b_dists, p_play, y)
        crps_s_mix = mixture_crps(list(s_dists), p_play, y)
        crps_a = df["crps_a"].to_numpy()
        crps_b = df["crps_b"].to_numpy()
        gid = df["game_id"].to_numpy()
        pid = df["player_id"].to_numpy()

        m = played
        cell = _cell("primary", stat, "played", "B-S", crps_b[m], crps_s[m], gid[m], cfg)
        sens = paired_score_delta_ci(
            crps_b[m], crps_s[m], n_boot=cfg.n_boot, seed=cfg.seed, cluster_ids=pid[m]
        )
        cell["player_clustered_ci"] = [float(sens.lo), float(sens.hi)]
        primary.append(cell)
        others.append(_cell("desc", stat, "played", "B-A", crps_b[m], crps_a[m], gid[m], cfg))
        others.append(_cell("desc", stat, "played", "A-S", crps_a[m], crps_s[m], gid[m], cfg))
        others.append(_cell("desc", stat, "all(mixture)", "B-S", crps_b_mix, crps_s_mix, gid, cfg))
        others.append(_cell("desc", stat, "all(A as shipped)", "B-A", crps_b_mix, crps_a, gid, cfg))
        others.append(_cell("desc", stat, "all(A as shipped)", "A-S", crps_a, crps_s_mix, gid, cfg))

        sb = build_sb_classification(con, stat, min_games=cfg.min_bucket_games, played_only=True)
        bucket = (
            df.join(
                sb.select("game_id", "player_id", "sb_class"),
                on=["game_id", "player_id"],
                how="left",
            )["sb_class"]
            .fill_null("insufficient_history")
            .to_numpy()
        )
        for b in sorted(set(bucket[m].tolist())):
            mb = m & (bucket == b)
            if mb.sum() < 30:
                continue
            others.append(
                _cell("desc", stat, "played", "B-S", crps_b[mb], crps_s[mb], gid[mb], cfg, bucket=b)
            )

    qs = benjamini_hochberg([c["p"] for c in primary]) if primary else []
    for c, q in zip(primary, qs, strict=True):
        c["q"] = q
        c["verdict"] = decide_primary(c)
        lo, hi = c["player_clustered_ci"]
        flipped = (c["verdict"] == "B beats S" and hi >= 0) or (
            c["verdict"] == "S beats B" and lo <= 0
        )
        if flipped:
            c["verdict"] += " (PROVISIONAL: flips under player-clustered CI)"
    result.cells = primary + others
    result.runtime_s = time.time() - t0
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--n-sims", type=int, default=1000)
    ap.add_argument("--sample-games", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="docs/fair_rematch_2024.json")
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    res = run_fair_rematch(
        con,
        FairRematchConfig(
            season=args.season, sample_games=args.sample_games, n_sims=args.n_sims, seed=args.seed
        ),
    )
    Path(args.out).write_text(res.to_json())
    print(res.markdown())
    print(f"runtime_s={res.runtime_s:.0f} seed={args.seed} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
