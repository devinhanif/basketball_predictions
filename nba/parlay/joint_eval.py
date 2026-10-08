"""Joint calibration of independence vs Gaussian copula vs t copula on 2023-24 OOF.

PRE-REGISTERED DECISION RULE (written before the first run of this module):
  * Unit: a synthetic same-game parlay (2-4 legs: winner / spread / total / player N+ at typical
    lines) built from a historical game; outcome from the real box score.
  * Primary metric: mean joint log loss. Uncertainty: paired bootstrap resampling GAMES
    (parlays within a game share outcomes), 2000 resamples, 95% interval.
  * Gaussian copula is kept over independence iff its joint log loss improves
    (mean diff < 0) with the 95% CI excluding 0 (upper bound < 0). Same rule for the t copula
    vs the Gaussian copula (the t copula must beat the Gaussian copula to be kept).
  * Brier is secondary and never decides alone. Slices (legs count, game-only vs with-player)
    are descriptive; no engine is kept on a slice.
  * Cross-fit: a season is scored with game-model parameters and correlations fitted on the
    OTHER season(s) (season 2025 is never touched). Only the 2024 fold is strictly
    walk-forward (fit on <=2023); it is reported separately.
  * Player marginals are conditional on playing (OOF has no p_play); parlay legs are built only
    on players who played, so absolute joint calibration is conditional on playing. The
    comparison between engines is paired on identical parlays and identical marginals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy import stats

from nba.parlay.calibration import (
    PairedComparison,
    brier_vec,
    clustered_paired_bootstrap,
    log_loss_vec,
)
from nba.parlay.copula import PairCorr
from nba.parlay.game_model import GameModelParams, asof_total_features, fit_game_model
from nba.parlay.joint import STATS, GameCtx, GameResiduals, JointModel, estimate_game_corr
from nba.parlay.legs import Leg
from nba.parlay.qdist import QuantileGridDist

LADDERS: dict[str, tuple[int, ...]] = {
    "pts": (10, 15, 20, 25, 30, 35),
    "reb": (4, 6, 8, 10, 12),
    "ast": (2, 4, 6, 8, 10),
    "fg3m": (1, 2, 3, 4, 5),
}
ENGINES = ("independence", "gaussian", "t")
EVAL_SEASONS = (2023, 2024)


@dataclass
class EvalGame:
    ctx: GameCtx
    margin: float
    total: float
    actual: dict[tuple[int, str], float]
    pid: np.ndarray
    team: np.ndarray
    stat: np.ndarray
    z: np.ndarray
    z_margin: float
    z_total: float


def load_inputs(
    db_path: str | Path, ctx_oof: str | Path, elo_oof: str | Path
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            "FROM games WHERE season <= 2024 AND home_pts IS NOT NULL AND home_pts > 0"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.pts, s.reb, s.ast, s.fg3m "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            "WHERE g.season IN (2023, 2024) AND s.minutes > 0"
        ).pl()
    finally:
        con.close()
    elo = (
        pl.read_parquet(elo_oof)
        .filter(pl.col("model") == "rung0_injury_elo")
        .select("game_id", "p")
    )
    oof = pl.read_parquet(ctx_oof).filter(pl.col("season").is_in(list(EVAL_SEASONS)))
    return games, pgs, elo, oof


def build_eval_games(
    season: int,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    elo: pl.DataFrame,
    oof: pl.DataFrame,
    params: GameModelParams,
    feats: pl.DataFrame,
    seed: int,
) -> list[EvalGame]:
    rng = np.random.default_rng(seed + season)
    g = (
        games.filter(pl.col("season") == season)
        .join(elo, on="game_id")
        .join(feats, on="game_id", how="left")
    )
    o = oof.filter(pl.col("season") == season).join(
        pgs.unpivot(
            index=["game_id", "player_id", "team_id"],
            on=list(STATS),
            variable_name="target",
            value_name="y",
        ),
        on=["game_id", "player_id", "target"],
        how="inner",
    )
    by_game = {k[0]: d for k, d in o.group_by("game_id")}
    out: list[EvalGame] = []
    for r in g.iter_rows(named=True):
        d = by_game.get(r["game_id"])
        if d is None or d.height == 0:
            continue
        gm = float(r["home_pts"] - r["away_pts"])
        gt = float(r["home_pts"] + r["away_pts"])
        mu_m = params.margin_mu(float(r["p"]))
        mu_t = params.total_mu(r["total_feat"])
        dists: dict[tuple[int, str], QuantileGridDist] = {}
        team_of: dict[int, int] = {}
        actual: dict[tuple[int, str], float] = {}
        pid_l: list[int] = []
        team_l: list[int] = []
        stat_l: list[int] = []
        z_l: list[float] = []
        for row in d.iter_rows(named=True):
            pid, st = int(row["player_id"]), str(row["target"])
            qd = QuantileGridDist.from_grid(row["q_grid"])
            dists[(pid, st)] = qd
            team_of[pid] = int(row["team_id"])
            y = float(row["y"])
            actual[(pid, st)] = y
            lo, hi = qd.pit_bounds(y)
            v = lo + rng.random() * (hi - lo)
            pid_l.append(pid)
            team_l.append(int(row["team_id"]))
            stat_l.append(STATS.index(st))
            z_l.append(float(stats.norm.ppf(min(max(v, 1e-9), 1 - 1e-9))))
        ctx = GameCtx(
            str(r["game_id"]),
            int(r["home_team"]),
            int(r["away_team"]),
            mu_m,
            params.margin_sd,
            mu_t,
            params.total_sd,
            dists,
            team_of,
            {},
        )
        out.append(
            EvalGame(
                ctx,
                gm,
                gt,
                actual,
                np.asarray(pid_l, dtype=np.int64),
                np.asarray(team_l, dtype=np.int64),
                np.asarray(stat_l, dtype=np.int64),
                np.asarray(z_l),
                (gm - mu_m) / params.margin_sd,
                (gt - mu_t) / params.total_sd,
            )
        )
    return out


def residuals_of(gs: list[EvalGame]) -> list[GameResiduals]:
    return [
        GameResiduals(g.ctx.home_team, g.z_margin, g.z_total, g.pid, g.team, g.stat, g.z)
        for g in gs
    ]


def leg_outcome(leg: Leg, g: EvalGame) -> bool:
    if leg.stat in ("win", "spread"):
        sg = 1.0 if leg.team_id == g.ctx.home_team else -1.0
        thr = 0.0 if leg.stat == "win" else leg.threshold
        yes = sg * g.margin > thr
    elif leg.stat == "total":
        yes = g.total > leg.threshold
    else:
        yes = g.actual[(leg.player_id, leg.stat)] >= leg.threshold
    return yes if leg.side == "yes" else not yes


def synth_parlays(g: EvalGame, rng: np.random.Generator, n: int) -> list[list[Leg]]:
    """Realistic same-game parlays: lines near the middle of each leg's distribution."""
    from nba.parlay.joint import leg_marginal  # local: avoid cycle at import time

    ctx = g.ctx
    gid = ctx.game_id
    by_team: dict[int, list[tuple[float, int]]] = {ctx.home_team: [], ctx.away_team: []}
    for (pid, st), d in ctx.dists.items():
        if st == "pts":
            by_team[ctx.team_of[pid]].append((float(np.median(d.q)), pid))
    pool = [pid for t in by_team.values() for _, pid in sorted(t, reverse=True)[:5]]
    out: list[list[Leg]] = []
    for _ in range(n):
        k = int(rng.choice([2, 3, 4], p=[0.45, 0.35, 0.20]))
        legs: list[Leg] = []
        used_margin = used_total = False
        used_ps: set[tuple[int, str]] = set()
        tries = 0
        while len(legs) < k and tries < 40:
            tries += 1
            kind = rng.choice(["win", "spread", "total", "player"], p=[0.12, 0.13, 0.15, 0.60])
            side = "no" if rng.random() < 0.25 else "yes"
            leg: Leg | None = None
            if kind in ("win", "spread") and not used_margin:
                team = int(rng.choice([ctx.home_team, ctx.away_team]))
                thr = 0.0 if kind == "win" else float(rng.integers(0, 15)) + 0.5
                leg = Leg(gid, 0, str(kind), thr, side, team)  # type: ignore[arg-type]
            elif kind == "total" and not used_total:
                thr = float(round(ctx.mu_t)) + float(rng.integers(-8, 9)) + 0.5
                leg = Leg(gid, 0, "total", thr, side, None)  # type: ignore[arg-type]
            elif kind == "player" and pool:
                pid = int(rng.choice(pool))
                st = str(rng.choice(STATS))
                if (pid, st) in used_ps or (pid, st) not in ctx.dists:
                    continue
                d = ctx.dists[(pid, st)]
                rungs = [t for t in LADDERS[st] if 0.15 <= d.p_ge(float(t)) <= 0.85]
                if not rungs:
                    continue
                leg = Leg(gid, pid, st, float(rng.choice(rungs)), side, ctx.team_of[pid])  # type: ignore[arg-type]
                used_ps.add((pid, st))
            if leg is None:
                continue
            if (
                leg.stat in ("win", "spread", "total")
                and not 0.15 <= leg_marginal(leg, ctx) <= 0.85
            ):
                continue
            legs.append(leg)
            used_margin |= leg.stat in ("win", "spread")
            used_total |= leg.stat == "total"
        if len(legs) >= 2:
            out.append(legs)
    return out


def _kind(legs: list[Leg]) -> str:
    return (
        "game_only" if all(leg.stat in ("win", "spread", "total") for leg in legs) else "has_player"
    )


def run_eval(
    db_path: str | Path,
    ctx_oof: str | Path,
    elo_oof: str | Path,
    out_dir: str | Path,
    *,
    n_sims: int = 10000,
    parlays_per_game: int = 6,
    seed: int = 20261008,
    n_boot: int = 2000,
    shrink_k: float = 200.0,
    t_df: float = 6.0,
    max_games: int | None = None,
) -> dict[str, Any]:
    games, pgs, elo, oof = load_inputs(db_path, ctx_oof, elo_oof)
    feats = asof_total_features(games, 20.0, 8.0)
    built: dict[int, tuple[GameModelParams, list[EvalGame]]] = {}
    # params fitted excluding the OTHER eval season's perspective is handled below.
    for s in EVAL_SEASONS:
        p_s = fit_game_model(games, elo, exclude_seasons=(s,))
        built[s] = (p_s, build_eval_games(s, games, pgs, elo, oof, p_s, feats, seed))
    rng = np.random.default_rng(seed)
    rec: list[dict[str, Any]] = []
    marg_rec: list[dict[str, Any]] = []
    corr_by_season: dict[int, PairCorr] = {}
    for s in EVAL_SEASONS:
        other = [x for x in EVAL_SEASONS if x != s][0]
        # Correlations for scoring season s come from the other season's games, z-scored with
        # the parameters fitted without s (same params as used to score s).
        p_s = built[s][0]
        other_games = build_eval_games(other, games, pgs, elo, oof, p_s, feats, seed)
        corr_by_season[s] = estimate_game_corr(residuals_of(other_games), shrink_k)
        models = {
            "independence": JointModel("independence", corr_by_season[s]),
            "gaussian": JointModel("gaussian", corr_by_season[s], n_sims, seed),
            "t": JointModel("t", corr_by_season[s], n_sims, seed, t_df),
        }
        gs = built[s][1][:max_games]
        for g in gs:
            ps = synth_parlays(g, rng, parlays_per_game)
            if not ps:
                continue
            res = {name: m.joint_many(ps, {g.ctx.game_id: g.ctx}) for name, m in models.items()}
            for i, legs in enumerate(ps):
                y = int(all(leg_outcome(leg, g) for leg in legs))
                rec.append(
                    {
                        "season": s,
                        "game": g.ctx.game_id,
                        "n_legs": len(legs),
                        "kind": _kind(legs),
                        "y": y,
                        **{f"p_{n}": res[n][i].p_cond for n in ENGINES},
                    }
                )
                for leg, mp in zip(legs, res["gaussian"][i].marginals, strict=True):
                    marg_rec.append(
                        {
                            "type": "player" if leg.player_id else leg.stat,
                            "p": mp,
                            "y": int(leg_outcome(leg, g)),
                            "game": g.ctx.game_id,
                        }
                    )
    df = pl.DataFrame(rec)
    return summarize(
        df,
        pl.DataFrame(marg_rec),
        corr_by_season,
        built,
        out_dir,
        n_boot,
        n_sims,
        shrink_k,
        t_df,
        seed,
    )


def _cmp(df: pl.DataFrame, a: str, b: str, metric: str, n_boot: int, seed: int) -> PairedComparison:
    fn = log_loss_vec if metric == "log_loss" else brier_vec
    y = df["y"].to_list()
    return clustered_paired_bootstrap(
        fn(df[f"p_{a}"].to_list(), y),
        fn(df[f"p_{b}"].to_list(), y),
        df["game"].to_numpy(),
        metric=metric,
        names=(a, b),
        n_boot=n_boot,
        seed=seed,
    )


def summarize(
    df: pl.DataFrame,
    marg: pl.DataFrame,
    corr_by_season: dict[int, PairCorr],
    built: dict[int, tuple[GameModelParams, list[EvalGame]]],
    out_dir: str | Path,
    n_boot: int,
    n_sims: int,
    shrink_k: float,
    t_df: float,
    seed: int,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slices: dict[str, pl.DataFrame] = {"all (cross-fit 2023+2024)": df}
    slices["2024 only (walk-forward)"] = df.filter(pl.col("season") == 2024)
    slices["2023 only (cross-fit)"] = df.filter(pl.col("season") == 2023)
    for k in (2, 3, 4):
        slices[f"{k} legs"] = df.filter(pl.col("n_legs") == k)
    slices["game-only legs"] = df.filter(pl.col("kind") == "game_only")
    slices["has player leg"] = df.filter(pl.col("kind") == "has_player")
    pairs = [("gaussian", "independence"), ("t", "independence"), ("t", "gaussian")]
    rows: list[dict[str, Any]] = []
    for name, sdf in slices.items():
        if sdf.height < 50:
            continue
        for a, b in pairs:
            for metric in ("log_loss", "brier"):
                c = _cmp(sdf, a, b, metric, n_boot, seed)
                rows.append(
                    {
                        "slice": name,
                        "n_parlays": c.n,
                        "n_games": sdf["game"].n_unique(),
                        "metric": metric,
                        "A": a,
                        "B": b,
                        "mean_diff": c.mean_diff,
                        "ci_lo": c.ci_lo,
                        "ci_hi": c.ci_hi,
                        "A_better_sig": c.a_better_significant,
                    }
                )
    y = df["y"].to_list()
    level = {
        n: {
            "log_loss": float(log_loss_vec(df[f"p_{n}"].to_list(), y).mean()),
            "brier": float(brier_vec(df[f"p_{n}"].to_list(), y).mean()),
            "mean_pred": float(df[f"p_{n}"].mean()),  # type: ignore[arg-type]
        }
        for n in ENGINES
    }
    bins = [0.0, 0.05, 0.10, 0.20, 0.40, 1.01]
    rel: list[dict[str, Any]] = []
    for n in ENGINES:
        p = df[f"p_{n}"].to_numpy()
        for lo, hi in zip(bins[:-1], bins[1:], strict=True):
            m = (p >= lo) & (p < hi)
            if m.sum():
                rel.append(
                    {
                        "engine": n,
                        "bin": f"[{lo:.2f},{min(hi, 1):.2f})",
                        "n": int(m.sum()),
                        "mean_pred": float(p[m].mean()),
                        "observed": float(df["y"].to_numpy()[m].mean()),
                    }
                )  # noqa: E501
    mrow = []
    for t, d in marg.group_by("type"):
        py = d["p"].to_numpy()
        yy = d["y"].to_numpy()
        mrow.append(
            {
                "type": t[0],
                "n_legs": d.height,
                "mean_pred": float(py.mean()),
                "observed": float(yy.mean()),
                "log_loss": float(log_loss_vec(py.tolist(), yy.tolist()).mean()),
            }
        )
    params = {str(s): json.loads(built[s][0].to_json()) for s in built}
    rhos = {
        str(s): {
            "|".join(k): [round(v, 4), corr_by_season[s].n[k]]
            for k, v in corr_by_season[s].rho.items()
        }
        for s in corr_by_season
    }
    res: dict[str, Any] = {
        "config": {
            "n_sims": n_sims,
            "shrink_k": shrink_k,
            "t_df": t_df,
            "seed": seed,
            "n_boot": n_boot,
        },
        "n_parlays": df.height,
        "n_games": df["game"].n_unique(),
        "level": level,
        "comparisons": rows,
        "reliability": rel,
        "marginals": mrow,
        "params": params,
        "corr": rhos,
    }
    (out / "joint_calibration.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    (out / "joint_calibration.md").write_text(render_md(res))
    return res


def render_md(res: dict[str, Any]) -> str:
    L = ["# Joint calibration: independence vs Gaussian vs t copula (2023-24 OOF)", ""]
    L.append(f"n parlays={res['n_parlays']}, n games={res['n_games']}, config={res['config']}")
    L += [
        "",
        "## Level (all parlays)",
        "| engine | log loss | Brier | mean pred |",
        "|---|---|---|---|",
    ]
    for n, v in res["level"].items():
        L.append(f"| {n} | {v['log_loss']:.5f} | {v['brier']:.5f} | {v['mean_pred']:.4f} |")
    L += [
        "",
        "## Paired comparisons (A-B, negative = A better; game-clustered 95% CI)",
        "| slice | n parlays | n games | metric | A | B | mean diff | 95% CI | A better (CI<0)? |",  # noqa: E501
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["comparisons"]:
        L.append(
            f"| {r['slice']} | {r['n_parlays']} | {r['n_games']} | {r['metric']} | {r['A']} | "
            f"{r['B']} | {r['mean_diff']:+.5f} | [{r['ci_lo']:+.5f}, {r['ci_hi']:+.5f}] | "
            f"{'yes' if r['A_better_sig'] else 'no'} |"
        )
    L += [
        "",
        "## Reliability of joint probability",
        "| engine | bin | n | mean pred | observed |",
        "|---|---|---|---|---|",
    ]
    for r in res["reliability"]:
        L.append(
            f"| {r['engine']} | {r['bin']} | {r['n']} | {r['mean_pred']:.4f} | {r['observed']:.4f} |"  # noqa: E501
        )
    L += [
        "",
        "## Leg marginals (sanity; conditional on playing)",
        "| type | n legs | mean pred | observed | log loss |",
        "|---|---|---|---|---|",
    ]  # noqa: E501
    for r in res["marginals"]:
        L.append(
            f"| {r['type']} | {r['n_legs']} | {r['mean_pred']:.4f} | {r['observed']:.4f} | {r['log_loss']:.4f} |"  # noqa: E501
        )
    return "\n".join(L) + "\n"


def fit_production(
    db_path: str | Path,
    ctx_oof: str | Path,
    elo_oof: str | Path,
    out_path: str | Path,
    *,
    shrink_k: float = 200.0,
    seed: int = 20261008,
) -> dict[str, Any]:
    """Fit the game model on seasons 2022-24 and the correlations on 2023-24 OOF; write JSON."""
    from nba.parlay.joint import corr_to_json

    games, pgs, elo, oof = load_inputs(db_path, ctx_oof, elo_oof)
    params = fit_game_model(games, elo)
    feats = asof_total_features(games, params.halflife, params.prior_k)
    gs: list[EvalGame] = []
    for s in EVAL_SEASONS:
        gs += build_eval_games(s, games, pgs, elo, oof, params, feats, seed)
    pc = estimate_game_corr(residuals_of(gs), shrink_k)
    blob = {
        "params": json.loads(params.to_json()),
        "corr": corr_to_json(pc),
        "n_games_corr": len(gs),
        "shrink_k": shrink_k,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(blob, sort_keys=True))
    return blob
