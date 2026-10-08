# ruff: noqa: E501
"""Local staging and scoring of the joint game-set transformer (Colab job ``joint_game_set``).

PRE-REGISTERED DECISION RULE (written 2026-10-08 BEFORE any Colab run of the job; full text and
rationale in ``docs/JOINT_GAME_SET.md``; this rule is not changed after seeing results).

Question: does a set transformer over all rostered players of a game capture same-game
dependence that the pooled-correlation Gaussian copula (``nba.parlay.joint``) misses?

Design.
  * Seasons 2022-2024 only; season 2025 is never loaded. Model selection (hyper-parameters, early
    stopping) uses ONLY a time-ordered slice of season 2023 (the last 20% of 2023 games by date).
    The model is then fit through 2023 and predicts season 2024 (report season). The static
    2023-fit ensemble is THE candidate; the walk-forward monthly refit is a secondary arm.
  * Evaluation units are exactly the synthetic same-game parlays of ``nba.parlay.joint_eval``
    (2-4 legs, same generator, same legs and thresholds, same seed 20261008, same rng stream, 6
    per game) for season 2024, with the same walk-forward game-model parameters and the
    cross-fit correlation matrix estimated on 2023. Player marginals are conditional on playing;
    parlay legs are built only on players who played (so joint calibration is conditional on
    playing, identical for every engine).
  * Comparator: the Gaussian copula evaluated with the SAME total number of Monte-Carlo draws as
    the set model (``p_gaussian_hi``), so Monte-Carlo noise cannot favour either engine. The
    published 10k-draw Gaussian is reported as a reproduction check.

KEEP RULE. The set model is KEPT (promoted to a registry candidate, eligible for a later
separately pre-registered confirmatory touch) iff ALL THREE hold on the 2024 report season:
  R1  joint log loss: mean(set_full - gaussian copula) < 0 with the 95% game-clustered paired
      bootstrap CI (2000 resamples) upper bound < 0.
  R2  single-leg marginal calibration not worse: ECE(set_full) - ECE(production marginal)
      <= +0.005 (point estimate; 10 equal-width bins; ladder legs with production
      P in [0.15, 0.85], both on the SAME legs).
  R3  game-level CRPS non-inferior to ``nba.parlay.game_model`` (Normal): the upper 95% CI bound of
      mean(CRPS_set - CRPS_normal) is <= +0.05 points for the margin AND for the total.
Anything else is NOT KEPT. The negative result is reported as such.

Descriptive only (never used to keep, select or tune): Brier; the dependence-only variant
``set_copula`` (production marginals, set-model dependence); the walk-forward arm; reliability and
paired differences by parlay size and by leg-type mix (props-only, prop+result, teammates,
opponents, same-player); the z-space joint NLL gain over the copula; p_play log loss vs the
as-of play-rate heuristic. If the primary fails but a descriptive variant looks better, nothing is
kept on that basis.

Entrypoints:
  uv run python -m nba.eval.joint_game_set_eval --stage     # local: parlays, legs, singles, copula NLL
  uv run python -m nba.eval.joint_game_set_eval --run <pulled run dir>   # local: score + report
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy import stats

from nba.features.game_sets import N_TOK, STATS, GameSets, export_game_sets
from nba.parlay import joint_eval as je
from nba.parlay.calibration import brier_vec, clustered_paired_bootstrap, log_loss_vec
from nba.parlay.game_model import asof_total_features, fit_game_model
from nba.parlay.joint import (
    GameCtx,
    JointModel,
    _pair_rho,
    corr_to_json,
    estimate_game_corr,
    leg_marginal,
)
from nba.parlay.legs import Leg

SEED = 20261008
EVAL_SEASON = 2024
N_GAME_DIMS = 2
DIM_MARGIN = N_TOK * 4
DIM_TOTAL = N_TOK * 4 + 1
N_DIM = N_TOK * 4 + N_GAME_DIMS
N_SIMS_HI = 100_000  # same total draws for the Gaussian comparator and the set model
ECE_TOL = 0.005
CRPS_TOL = 0.05
SINGLE_BOUNDS = (0.15, 0.85)
GRID_STAGE_FILES = (
    "eval_parlays.parquet",
    "eval_legs.parquet",
    "eval_singles.parquet",
    "eval_games.parquet",
    "eval_meta.json",
)

# --------------------------------------------------------------------------- leg -> latent dim


def leg_dim(leg: Leg, ctx: GameCtx, slot_of: dict[int, int]) -> tuple[int, int, float]:
    """``(dim, dir, zthr)``: the leg is a YES hit iff ``dir * (z[dim] - zthr) > 0``.

    Player legs: z is the production-PIT normal score and ``zthr = Phi^-1(1 - P(stat >= N))``
    (the copula convention ``u > 1 - p_yes``). Margin legs use ``z = (margin - mu) / sd`` with the
    side sign of the leg's team; total legs ``z = (total - mu) / sd``. Identical event definitions
    to ``nba.parlay.joint.leg_hits``."""
    if leg.stat in ("win", "spread"):
        sg = 1.0 if leg.team_id == ctx.home_team else -1.0
        thr = 0.0 if leg.stat == "win" else leg.threshold
        c = (thr - sg * ctx.mu_m) / ctx.sd_m if sg > 0 else -(thr + ctx.mu_m) / ctx.sd_m
        # sg=+1: z > (thr - mu)/sd ;  sg=-1: -(mu + sd z) > thr  <=>  z < -(thr + mu)/sd
        return DIM_MARGIN, (1 if sg > 0 else -1), float(c)
    if leg.stat == "total":
        return DIM_TOTAL, 1, float((leg.threshold - ctx.mu_t) / ctx.sd_t)
    d = ctx.dists[(leg.player_id, leg.stat)]
    p = d.p_ge(leg.threshold)
    return slot_of[leg.player_id] * 4 + STATS.index(leg.stat), 1, float(stats.norm.ppf(1.0 - p))


def parlay_mix(legs: list[Leg], ctx: GameCtx) -> dict[str, Any]:
    pl_legs = [leg for leg in legs if leg.player_id]
    teams = {ctx.team_of[leg.player_id] for leg in pl_legs}
    pids = {leg.player_id for leg in pl_legs}
    per_player: dict[int, int] = {}
    for leg in pl_legs:
        per_player[leg.player_id] = per_player.get(leg.player_id, 0) + 1
    n_game = len(legs) - len(pl_legs)
    mix = "game_only" if not pl_legs else ("props_only" if n_game == 0 else "prop_plus_result")
    return {
        "n_player_legs": len(pl_legs),
        "n_game_legs": n_game,
        "mix": mix,
        "teammates": len(pids) >= 2 and len(teams) == 1,
        "opponents": len(teams) >= 2,
        "same_player": any(v >= 2 for v in per_player.values()),
    }


# --------------------------------------------------------------------------- copula z-space NLL


def _repair_corr(m: np.ndarray, floor: float = 1e-3) -> np.ndarray:
    """Cheap PSD repair (eigenvalue floor, re-unit diagonal) for the descriptive z-space NLL."""
    w, v = np.linalg.eigh((m + m.T) / 2.0)
    r = (v * np.clip(w, floor, None)) @ v.T
    d = 1.0 / np.sqrt(np.diag(r))
    r = r * d[:, None] * d[None, :]
    return np.asarray((r + r.T) / 2.0)


def copula_game_nll(gs: GameSets, gi: int, pc: Any) -> tuple[float, float]:
    """``(nll_copula, nll_independent)`` of the observed z vector of game ``gi`` (nats)."""
    a = gs.arrays
    gid = str(a["game_id"][gi])
    obs = a["tok_obs"][gi] & a["tok_mask"][gi][:, None]
    slots, ks = np.nonzero(obs)
    team_of = {int(a["tok_pid"][gi, s]): int(a["tok_team"][gi, s]) for s in np.unique(slots)}
    ctx = GameCtx(
        gid,
        int(a["home_team"][gi]),
        -1,
        float(a["mu_margin"][gi]),
        float(a["sd_margin"][gi]),
        float(a["mu_total"][gi]),
        float(a["sd_total"][gi]),
        {},
        team_of,
        {},
    )
    ents: list[tuple[str, str, int, str]] = [
        ("player", gid, int(a["tok_pid"][gi, s]), STATS[k]) for s, k in zip(slots, ks, strict=True)
    ]
    ents += [("margin", gid, 0, "margin"), ("total", gid, 0, "total")]
    z = np.concatenate(
        [
            a["tok_z"][gi][slots, ks].astype(float),
            [float(a["z_margin"][gi]), float(a["z_total"][gi])],
        ]
    )
    n = len(ents)
    m = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            m[i, j] = m[j, i] = _pair_rho(ents[i], ents[j], ctx, pc)
    r = _repair_corr(m)
    chol = np.linalg.cholesky(r)
    logdet = 2.0 * float(np.log(np.diag(chol)).sum())
    w = np.linalg.solve(chol, z)
    quad = float(w @ w)
    c = 0.5 * n * np.log(2 * np.pi)
    return float(c + 0.5 * logdet + 0.5 * quad), float(c + 0.5 * float(z @ z))


# --------------------------------------------------------------------------- staging


def stage_eval_inputs(
    db_path: str | Path,
    ctx_oof: str | Path,
    elo_oof: str | Path,
    out_dir: str | Path,
    *,
    seed: int = SEED,
    parlays_per_game: int = 6,
    n_sims_pub: int = 10000,
    n_sims_hi: int = N_SIMS_HI,
    shrink_k: float = 200.0,
    t_df: float = 6.0,
    max_games: int | None = None,
    rebuild_sets: bool = False,
) -> dict[str, Any]:
    """Write the exact 2024 parlays/legs/singles + per-game copula NLL for the Colab job.

    Replays ``nba.parlay.joint_eval.run_eval`` (including the 2023 rng consumption) so the 2024
    parlays are the very same events; adds Gaussian/independence/t probabilities at the
    published draw count and a Gaussian at ``n_sims_hi`` draws (matched Monte-Carlo noise)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if rebuild_sets or not (out / "game_sets.npz").exists():
        gs = export_game_sets(db_path, ctx_oof, elo_oof, out)
    else:
        gs = GameSets.load(out)
    gid_index = {str(g): i for i, g in enumerate(gs.arrays["game_id"])}
    games, pgs, elo, oof = je.load_inputs(db_path, ctx_oof, elo_oof)
    feats = asof_total_features(games, 20.0, 8.0)
    built: dict[int, tuple[Any, list[je.EvalGame]]] = {}
    for s in je.EVAL_SEASONS:
        p_s = fit_game_model(games, elo, exclude_seasons=(s,))
        built[s] = (p_s, je.build_eval_games(s, games, pgs, elo, oof, p_s, feats, seed))
    rng = np.random.default_rng(seed)
    for g in built[2023][1][:max_games]:  # replay: the 2023 draws precede the 2024 draws
        je.synth_parlays(g, rng, parlays_per_game)
    p24 = built[EVAL_SEASON][0]
    other = je.build_eval_games(2023, games, pgs, elo, oof, p24, feats, seed)
    pc = estimate_game_corr(je.residuals_of(other), shrink_k)
    models = {
        "independence": JointModel("independence", pc),
        "gaussian": JointModel("gaussian", pc, n_sims_pub, seed),
        "t": JointModel("t", pc, n_sims_pub, seed, t_df),
        "gaussian_hi": JointModel("gaussian", pc, n_sims_hi, seed),
    }
    parlays: list[dict[str, Any]] = []
    legs_rows: list[dict[str, Any]] = []
    singles: list[dict[str, Any]] = []
    pid_counter = 0
    for g in built[EVAL_SEASON][1][:max_games]:
        gi = gid_index[g.ctx.game_id]
        slot_of = {
            int(p): int(s)
            for s, p in enumerate(gs.arrays["tok_pid"][gi])
            if gs.arrays["tok_mask"][gi, s]
        }
        for tol_name, got, want in (
            ("mu_margin", gs.arrays["mu_margin"][gi], g.ctx.mu_m),
            ("mu_total", gs.arrays["mu_total"][gi], g.ctx.mu_t),
        ):
            if abs(float(got) - float(want)) > 1e-6:
                raise ValueError(f"game-model mismatch {tol_name} {g.ctx.game_id}: {got} {want}")
        ps = je.synth_parlays(g, rng, parlays_per_game)
        if ps:
            res = {n: m.joint_many(ps, {g.ctx.game_id: g.ctx}) for n, m in models.items()}
            for i, legs in enumerate(ps):
                y = int(all(je.leg_outcome(leg, g) for leg in legs))
                row = {
                    "parlay_id": pid_counter,
                    "game_idx": gi,
                    "game_id": g.ctx.game_id,
                    "n_legs": len(legs),
                    "kind": je._kind(legs),
                    "y": y,
                    "p_independence": res["independence"][i].p_cond,
                    "p_gaussian": res["gaussian"][i].p_cond,
                    "p_t": res["t"][i].p_cond,
                    "p_gaussian_hi": res["gaussian_hi"][i].p_cond,
                    **parlay_mix(legs, g.ctx),
                }
                parlays.append(row)
                for pos, leg in enumerate(legs):
                    dim, dr, zthr = leg_dim(leg, g.ctx, slot_of)
                    legs_rows.append(
                        {
                            "parlay_id": pid_counter,
                            "leg_pos": pos,
                            "game_idx": gi,
                            "dim": dim,
                            "dir": dr,
                            "zthr": zthr,
                            "side_no": int(leg.side == "no"),
                            "p_marg": leg_marginal(leg, g.ctx),
                            "y_leg": int(je.leg_outcome(leg, g)),
                            "leg_type": "player" if leg.player_id else leg.stat,
                        }
                    )
                pid_counter += 1
        for (pid, st), d in g.ctx.dists.items():
            for t in je.LADDERS[st]:
                p = d.p_ge(float(t))
                if SINGLE_BOUNDS[0] <= p <= SINGLE_BOUNDS[1]:
                    singles.append(
                        {
                            "single_id": len(singles),
                            "game_idx": gi,
                            "dim": slot_of[pid] * 4 + STATS.index(st),
                            "zthr": float(stats.norm.ppf(1.0 - p)),
                            "p_marg": p,
                            "y": int(g.actual[(pid, st)] >= t),
                            "stat": st,
                        }
                    )
    gidx = sorted({r["game_idx"] for r in parlays} | {s["game_idx"] for s in singles})
    games_rows = []
    for gi in (gid_index[g.ctx.game_id] for g in built[EVAL_SEASON][1][:max_games]):
        nll_c, nll_i = copula_game_nll(gs, gi, pc)
        a = gs.arrays
        row = {
            "game_idx": gi,
            "game_id": str(a["game_id"][gi]),
            "nll_copula": nll_c,
            "nll_indep": nll_i,
        }
        for nm, y, mu, sd in (
            ("margin", a["margin"][gi], a["mu_margin"][gi], a["sd_margin"][gi]),
            ("total", a["total"][gi], a["mu_total"][gi], a["sd_total"][gi]),
        ):
            row[f"crps_{nm}_normal"] = normal_crps(float(y), float(mu), float(sd))
        games_rows.append(row)
    pl.DataFrame(parlays).write_parquet(out / "eval_parlays.parquet")
    pl.DataFrame(legs_rows).write_parquet(out / "eval_legs.parquet")
    pl.DataFrame(singles).write_parquet(out / "eval_singles.parquet")
    pl.DataFrame(games_rows).write_parquet(out / "eval_games.parquet")
    meta = {
        "seed": seed,
        "parlays_per_game": parlays_per_game,
        "n_sims_published": n_sims_pub,
        "n_sims_hi": n_sims_hi,
        "n_parlays": len(parlays),
        "n_legs": len(legs_rows),
        "n_singles": len(singles),
        "n_games": len(games_rows),
        "game_idx_min": int(min(gidx)) if gidx else None,
        "corr": corr_to_json(pc),
        "shrink_k": shrink_k,
        "eval_season": EVAL_SEASON,
        "keep_rule": {"ece_tol": ECE_TOL, "crps_tol": CRPS_TOL},
    }
    (out / "eval_meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def normal_crps(y: float, mu: float, sd: float) -> float:
    w = (y - mu) / sd
    return float(
        sd * (w * (2 * stats.norm.cdf(w) - 1) + 2 * stats.norm.pdf(w) - 1 / np.sqrt(np.pi))
    )


# --------------------------------------------------------------------------- scoring


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    b = np.minimum((p * n_bins).astype(int), n_bins - 1)
    tot = 0.0
    for k in range(n_bins):
        m = b == k
        if m.any():
            tot += m.sum() * abs(float(p[m].mean()) - float(y[m].mean()))
    return tot / len(p) if len(p) else float("nan")


def ece_diff_bootstrap(
    p_a: np.ndarray,
    p_b: np.ndarray,
    y: np.ndarray,
    games: np.ndarray,
    *,
    n_boot: int = 1000,
    seed: int = 0,
    n_bins: int = 10,
) -> dict[str, float]:
    """ECE(a) - ECE(b) with a game-clustered bootstrap CI (descriptive; R2 uses the point)."""
    _, inv = np.unique(games, return_inverse=True)
    ng = int(inv.max()) + 1

    def tabs(p: np.ndarray) -> np.ndarray:
        b = np.minimum((p * n_bins).astype(int), n_bins - 1)
        out = np.zeros((3, ng, n_bins))
        for i, w in enumerate((np.ones_like(p), p, y.astype(float))):
            np.add.at(out[i], (inv, b), w)
        return out

    ta, tb = tabs(p_a), tabs(p_b)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(ng, np.full(ng, 1.0 / ng), size=n_boot).astype(float)

    def ece_from(t: np.ndarray, wts: np.ndarray) -> np.ndarray:
        n = np.einsum("bg,gk->bk", wts, t[0])
        sp = np.einsum("bg,gk->bk", wts, t[1])
        sy = np.einsum("bg,gk->bk", wts, t[2])
        gap = np.abs(sp - sy)  # n*|mean_pred - obs| per bin
        return np.asarray(gap.sum(axis=1) / np.maximum(n.sum(axis=1), 1.0))

    d = ece_from(ta, w) - ece_from(tb, w)
    lo, hi = np.quantile(d, [0.025, 0.975])
    return {
        "ece_a": ece(p_a, y),
        "ece_b": ece(p_b, y),
        "diff": ece(p_a, y) - ece(p_b, y),
        "ci_lo": float(lo),
        "ci_hi": float(hi),
    }


def _cmp(df: pl.DataFrame, a: str, b: str, metric: str, n_boot: int, seed: int) -> dict[str, Any]:
    fn = log_loss_vec if metric == "log_loss" else brier_vec
    y = df["y"].to_list()
    c = clustered_paired_bootstrap(
        fn(df[a].to_list(), y),
        fn(df[b].to_list(), y),
        df["game_id"].to_numpy(),
        metric=metric,
        names=(a, b),
        n_boot=n_boot,
        seed=seed,
    )
    return {
        "A": a,
        "B": b,
        "metric": metric,
        "n_parlays": c.n,
        "n_games": int(df["game_id"].n_unique()),
        "mean_diff": c.mean_diff,
        "ci_lo": c.ci_lo,
        "ci_hi": c.ci_hi,
        "A_better_sig": c.a_better_significant,
    }


@dataclass
class RuleOutcome:
    r1: bool
    r2: bool
    r3: bool

    @property
    def keep(self) -> bool:
        return self.r1 and self.r2 and self.r3


def apply_rule(
    r1_ci_hi: float, r1_mean: float, ece_diff: float, crps_hi_margin: float, crps_hi_total: float
) -> RuleOutcome:
    return RuleOutcome(
        r1=bool(r1_mean < 0.0 and r1_ci_hi < 0.0),
        r2=bool(ece_diff <= ECE_TOL),
        r3=bool(crps_hi_margin <= CRPS_TOL and crps_hi_total <= CRPS_TOL),
    )


def _slice_defs(par: pl.DataFrame) -> dict[str, pl.DataFrame]:
    s: dict[str, pl.DataFrame] = {"all 2024 parlays": par}
    for k in (2, 3, 4):
        s[f"{k} legs"] = par.filter(pl.col("n_legs") == k)
    s["props-only"] = par.filter(pl.col("mix") == "props_only")
    s["prop+result"] = par.filter(pl.col("mix") == "prop_plus_result")
    s["game-only"] = par.filter(pl.col("mix") == "game_only")
    s["teammates (>=2 players, one team)"] = par.filter(pl.col("teammates"))
    s["opponents (players on both teams)"] = par.filter(pl.col("opponents"))
    s["same player, 2+ stats"] = par.filter(pl.col("same_player"))
    return s


def _reliability(df: pl.DataFrame, cols: list[str]) -> list[dict[str, Any]]:
    bins = [0.0, 0.05, 0.10, 0.20, 0.40, 1.01]
    rows: list[dict[str, Any]] = []
    y = df["y"].to_numpy()
    for c in cols:
        p = df[c].to_numpy()
        for lo, hi in zip(bins[:-1], bins[1:], strict=True):
            m = (p >= lo) & (p < hi)
            if m.sum():
                rows.append(
                    {
                        "engine": c,
                        "bin": f"[{lo:.2f},{min(hi, 1):.2f})",
                        "n": int(m.sum()),
                        "mean_pred": float(p[m].mean()),
                        "observed": float(y[m].mean()),
                    }
                )
    return rows


def score_run(
    run_dir: str | Path,
    stage_dir: str | Path,
    out_dir: str | Path | None = None,
    *,
    n_boot: int = 2000,
    seed: int = SEED,
) -> dict[str, Any]:
    """Apply the pre-registered rule to a pulled Colab run and write ``report.md``/``result.json``."""
    run, st = Path(run_dir), Path(stage_dir)
    out = Path(out_dir) if out_dir else run
    out.mkdir(parents=True, exist_ok=True)
    gs = GameSets.load(st)
    par = pl.read_parquet(st / "eval_parlays.parquet").join(
        pl.read_parquet(run / "parlay_preds.parquet"), on="parlay_id"
    )
    sing = pl.read_parquet(st / "eval_singles.parquet").join(
        pl.read_parquet(run / "single_preds.parquet"), on="single_id"
    )
    gam = pl.read_parquet(st / "eval_games.parquet").join(
        pl.read_parquet(run / "game_preds.parquet"), on="game_idx"
    )
    cfg = json.loads((run / "metrics.json").read_text())
    has_wf = "p_set_wf_full" in par.columns and par["p_set_wf_full"].null_count() == 0
    engines = ["p_independence", "p_gaussian", "p_gaussian_hi", "p_t", "p_set_full", "p_set_copula"]
    if has_wf:
        engines += ["p_set_wf_full", "p_set_wf_copula"]
    y = par["y"].to_list()
    level = {
        e: {
            "log_loss": float(log_loss_vec(par[e].to_list(), y).mean()),
            "brier": float(brier_vec(par[e].to_list(), y).mean()),
            "mean_pred": float(par[e].mean()),  # type: ignore[arg-type]
        }
        for e in engines
    }
    primary = _cmp(par, "p_set_full", "p_gaussian_hi", "log_loss", n_boot, seed)
    comps: list[dict[str, Any]] = [primary]
    pairs = [
        ("p_set_full", "p_gaussian_hi"),
        ("p_set_full", "p_independence"),
        ("p_set_copula", "p_gaussian_hi"),
        ("p_gaussian_hi", "p_independence"),
        ("p_gaussian", "p_independence"),
        ("p_gaussian", "p_gaussian_hi"),
    ]
    if has_wf:
        pairs += [("p_set_wf_full", "p_gaussian_hi"), ("p_set_wf_copula", "p_gaussian_hi")]
    for a, b in pairs:
        for metric in ("log_loss", "brier"):
            if (a, b, metric) != ("p_set_full", "p_gaussian_hi", "log_loss"):
                comps.append(_cmp(par, a, b, metric, n_boot, seed))
    slice_rows: list[dict[str, Any]] = []
    for name, sdf in _slice_defs(par).items():
        if sdf.height < 50:
            continue
        for a, b in (("p_set_full", "p_gaussian_hi"), ("p_set_copula", "p_gaussian_hi")):
            r = _cmp(sdf, a, b, "log_loss", n_boot, seed)
            r["slice"] = name
            slice_rows.append(r)
        for e in ("p_gaussian_hi", "p_set_full"):
            slice_rows.append(
                {
                    "slice": name,
                    "A": e,
                    "B": "-",
                    "metric": "level_log_loss",
                    "n_parlays": sdf.height,
                    "n_games": int(sdf["game_id"].n_unique()),
                    "mean_diff": float(log_loss_vec(sdf[e].to_list(), sdf["y"].to_list()).mean()),
                    "ci_lo": float("nan"),
                    "ci_hi": float("nan"),
                    "A_better_sig": False,
                }
            )
    rel_all = _reliability(par, ["p_gaussian_hi", "p_set_full"])
    rel_by: dict[str, list[dict[str, Any]]] = {}
    for k in (2, 3, 4):
        rel_by[f"{k} legs"] = _reliability(
            par.filter(pl.col("n_legs") == k), ["p_gaussian_hi", "p_set_full"]
        )
    for nm, flt in (
        ("props-only", pl.col("mix") == "props_only"),
        ("prop+result", pl.col("mix") == "prop_plus_result"),
        ("teammates", pl.col("teammates")),
        ("opponents", pl.col("opponents")),
    ):
        sub = par.filter(flt)
        if sub.height >= 50:
            rel_by[nm] = _reliability(sub, ["p_gaussian_hi", "p_set_full"])
    # single-leg marginal calibration
    pm, ps_, ys = sing["p_marg"].to_numpy(), sing["p_set_full"].to_numpy(), sing["y"].to_numpy()
    g_s = gs.arrays["game_id"][sing["game_idx"].to_numpy()]
    ecd = ece_diff_bootstrap(ps_, pm, ys, g_s, n_boot=1000, seed=seed)
    ll_single = clustered_paired_bootstrap(
        log_loss_vec(ps_.tolist(), ys.tolist()),
        log_loss_vec(pm.tolist(), ys.tolist()),
        g_s,
        metric="log_loss",
        names=("set_full", "production"),
        n_boot=n_boot,
        seed=seed,
    )
    single: dict[str, Any] = {
        "n_legs": int(len(ys)),
        "n_games": int(len(np.unique(g_s))),
        "ece_set_full": ecd["ece_a"],
        "ece_production": ecd["ece_b"],
        "ece_diff": ecd["diff"],
        "ece_diff_ci": [ecd["ci_lo"], ecd["ci_hi"]],
        "logloss_diff": ll_single.mean_diff,
        "logloss_diff_ci": [ll_single.ci_lo, ll_single.ci_hi],
        "by_stat": {
            s: {
                "n": int((sing["stat"] == s).sum()),
                "ece_set_full": ece(
                    ps_[(sing["stat"] == s).to_numpy()], ys[(sing["stat"] == s).to_numpy()]
                ),
                "ece_production": ece(
                    pm[(sing["stat"] == s).to_numpy()], ys[(sing["stat"] == s).to_numpy()]
                ),
            }
            for s in STATS
            if (sing["stat"] == s).any()
        },
    }
    # game-level margin / total CRPS and z-space NLL
    gm: dict[str, Any] = {}
    for nm in ("margin", "total"):
        d = gam[f"crps_{nm}_set"].to_numpy() - gam[f"crps_{nm}_normal"].to_numpy()
        from nba.parlay.calibration import paired_bootstrap

        c = paired_bootstrap(
            gam[f"crps_{nm}_set"].to_numpy(),
            gam[f"crps_{nm}_normal"].to_numpy(),
            metric="crps",
            names=("set", "normal"),
            n_boot=n_boot,
            seed=seed,
        )
        gm[nm] = {
            "n_games": int(len(d)),
            "crps_set": float(gam[f"crps_{nm}_set"].mean()),  # type: ignore[arg-type]
            "crps_normal": float(gam[f"crps_{nm}_normal"].mean()),  # type: ignore[arg-type]
            "diff": c.mean_diff,
            "ci": [c.ci_lo, c.ci_hi],
        }
    nll: dict[str, Any] = {}
    from nba.parlay.calibration import paired_bootstrap as pb

    for a, b in (("nll_set", "nll_copula"), ("nll_set", "nll_indep"), ("nll_copula", "nll_indep")):
        c = pb(
            gam[a].to_numpy(),
            gam[b].to_numpy(),
            metric="nll",
            names=(a, b),
            n_boot=n_boot,
            seed=seed,
        )
        nll[f"{a}-{b}"] = {
            "mean_diff_nats_per_game": c.mean_diff,
            "ci": [c.ci_lo, c.ci_hi],
            "n": c.n,
        }
    # p_play
    play: dict[str, Any] = {}
    pp = pl.read_parquet(run / "play_preds.parquet")
    if pp.height:
        names = list(gs.meta["tok_features"])
        k20 = names.index("play20")
        gi_ = pp["game_idx"].to_numpy()
        sl_ = pp["slot"].to_numpy()
        base = gs.arrays["tok_x"][gi_, sl_, k20].astype(float)
        yb = gs.arrays["tok_played"][gi_, sl_].astype(int)
        lb = log_loss_vec(base.tolist(), yb.tolist())
        ls = log_loss_vec(pp["p_set"].to_list(), yb.tolist())
        c = clustered_paired_bootstrap(
            ls,
            lb,
            gs.arrays["game_id"][gi_],
            metric="log_loss",
            names=("set", "play20"),
            n_boot=n_boot,
            seed=seed,
        )
        play = {
            "n_tokens": int(len(yb)),
            "logloss_set": float(ls.mean()),
            "logloss_play20_heuristic": float(lb.mean()),
            "diff": c.mean_diff,
            "ci": [c.ci_lo, c.ci_hi],
        }
    crps_hi = {nm: gm[nm]["ci"][1] for nm in ("margin", "total")}
    outcome = apply_rule(
        primary["ci_hi"],
        primary["mean_diff"],
        single["ece_diff"],
        crps_hi["margin"],
        crps_hi["total"],
    )
    res = {
        "verdict": "KEEP" if outcome.keep else "NOT KEPT",
        "rules": {
            "R1_joint_logloss_vs_gaussian": {
                "pass": outcome.r1,
                "mean_diff": primary["mean_diff"],
                "ci": [primary["ci_lo"], primary["ci_hi"]],
                "n_parlays": primary["n_parlays"],
                "n_games": primary["n_games"],
            },
            "R2_single_leg_ece": {
                "pass": outcome.r2,
                "diff": single["ece_diff"],
                "tolerance": ECE_TOL,
            },
            "R3_game_crps_noninferior": {
                "pass": outcome.r3,
                "margin_ci_hi": crps_hi["margin"],
                "total_ci_hi": crps_hi["total"],
                "tolerance": CRPS_TOL,
            },
        },
        "level": level,
        "comparisons": comps,
        "slices": slice_rows,
        "reliability_all": rel_all,
        "reliability_by": rel_by,
        "single_leg": single,
        "game_crps": gm,
        "z_space_nll": nll,
        "p_play": play,
        "job_metrics_keys": sorted(cfg),
        "has_walk_forward": has_wf,
    }
    (out / "result.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "report.md").write_text(render_report(res))
    return res


def render_report(res: dict[str, Any]) -> str:
    r = res["rules"]
    L = ["# Joint game-set transformer: 2024 report (pre-registered rule)", ""]
    L.append(f"**Verdict: {res['verdict']}**")
    L.append("")
    L += [
        "| rule | pass | detail |",
        "|---|---|---|",
        f"| R1 joint log loss, set_full vs Gaussian copula | {r['R1_joint_logloss_vs_gaussian']['pass']} | "
        f"diff {r['R1_joint_logloss_vs_gaussian']['mean_diff']:+.5f} CI "
        f"[{r['R1_joint_logloss_vs_gaussian']['ci'][0]:+.5f}, {r['R1_joint_logloss_vs_gaussian']['ci'][1]:+.5f}] "
        f"(n={r['R1_joint_logloss_vs_gaussian']['n_parlays']} parlays, "
        f"{r['R1_joint_logloss_vs_gaussian']['n_games']} games) |",
        f"| R2 single-leg ECE not worse (<= +{ECE_TOL}) | {r['R2_single_leg_ece']['pass']} | "
        f"ECE diff {r['R2_single_leg_ece']['diff']:+.5f} |",
        f"| R3 margin/total CRPS non-inferior (CI hi <= +{CRPS_TOL}) | {r['R3_game_crps_noninferior']['pass']} | "
        f"margin hi {r['R3_game_crps_noninferior']['margin_ci_hi']:+.4f}, "
        f"total hi {r['R3_game_crps_noninferior']['total_ci_hi']:+.4f} |",
        "",
        "## Level (all 2024 parlays)",
        "| engine | log loss | Brier | mean pred |",
        "|---|---|---|---|",
    ]
    for e, v in res["level"].items():
        L.append(f"| {e} | {v['log_loss']:.5f} | {v['brier']:.5f} | {v['mean_pred']:.4f} |")
    L += [
        "",
        "## Paired comparisons (A-B, negative = A better; game-clustered 95% CI)",
        "| metric | A | B | n parlays | n games | mean diff | 95% CI | A better (CI<0) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in res["comparisons"]:
        L.append(
            f"| {c['metric']} | {c['A']} | {c['B']} | {c['n_parlays']} | {c['n_games']} | "
            f"{c['mean_diff']:+.5f} | [{c['ci_lo']:+.5f}, {c['ci_hi']:+.5f}] | {'yes' if c['A_better_sig'] else 'no'} |"
        )
    L += [
        "",
        "## Slices by parlay size and leg-type mix (descriptive)",
        "| slice | A | B | metric | n parlays | n games | value / diff | 95% CI |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in res["slices"]:
        ci = "" if c["metric"] == "level_log_loss" else f"[{c['ci_lo']:+.5f}, {c['ci_hi']:+.5f}]"
        L.append(
            f"| {c['slice']} | {c['A']} | {c['B']} | {c['metric']} | {c['n_parlays']} | {c['n_games']} | "
            f"{c['mean_diff']:+.5f} | {ci} |"
        )
    L += [
        "",
        "## Reliability of the joint probability (all parlays)",
        "| engine | bin | n | mean pred | observed |",
        "|---|---|---|---|---|",
    ]
    for q in res["reliability_all"]:
        L.append(
            f"| {q['engine']} | {q['bin']} | {q['n']} | {q['mean_pred']:.4f} | {q['observed']:.4f} |"
        )
    for nm, rows in res["reliability_by"].items():
        L += [
            "",
            f"### Reliability: {nm}",
            "| engine | bin | n | mean pred | observed |",
            "|---|---|---|---|---|",
        ]
        for q in rows:
            L.append(
                f"| {q['engine']} | {q['bin']} | {q['n']} | {q['mean_pred']:.4f} | {q['observed']:.4f} |"
            )
    s = res["single_leg"]
    L += [
        "",
        "## Single-leg marginal calibration (ladder legs, production P in [0.15, 0.85])",
        f"n legs {s['n_legs']} in {s['n_games']} games. ECE set_full {s['ece_set_full']:.5f} vs production "
        f"{s['ece_production']:.5f}; diff {s['ece_diff']:+.5f} (95% CI [{s['ece_diff_ci'][0]:+.5f}, "
        f"{s['ece_diff_ci'][1]:+.5f}]). Log loss diff {s['logloss_diff']:+.6f} "
        f"(CI [{s['logloss_diff_ci'][0]:+.6f}, {s['logloss_diff_ci'][1]:+.6f}]).",
        "",
        "| stat | n | ECE set_full | ECE production |",
        "|---|---|---|---|",
    ]
    for k, v in s["by_stat"].items():
        L.append(f"| {k} | {v['n']} | {v['ece_set_full']:.5f} | {v['ece_production']:.5f} |")
    L += [
        "",
        "## Game-level CRPS (points)",
        "| quantity | n games | set | normal (game_model) | diff | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in res["game_crps"].items():
        L.append(
            f"| {k} | {v['n_games']} | {v['crps_set']:.4f} | {v['crps_normal']:.4f} | {v['diff']:+.4f} | [{v['ci'][0]:+.4f}, {v['ci'][1]:+.4f}] |"
        )
    L += [
        "",
        "## z-space joint NLL (nats per game, A-B, negative = A better; descriptive)",
        "| pair | n games | mean diff | 95% CI |",
        "|---|---|---|---|",
    ]
    for k, v in res["z_space_nll"].items():
        L.append(
            f"| {k} | {v['n']} | {v['mean_diff_nats_per_game']:+.3f} | [{v['ci'][0]:+.3f}, {v['ci'][1]:+.3f}] |"
        )
    p = res["p_play"]
    if p:
        L += [
            "",
            "## p_play head (descriptive)",
            f"n tokens {p['n_tokens']}; log loss set {p['logloss_set']:.5f} vs as-of 20-row play-rate heuristic "
            f"{p['logloss_play20_heuristic']:.5f}; diff {p['diff']:+.5f} CI [{p['ci'][0]:+.5f}, {p['ci'][1]:+.5f}].",
        ]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="joint game-set: stage inputs / score a pulled run")
    ap.add_argument("--stage", action="store_true")
    ap.add_argument(
        "--run", default=None, help="pulled run dir (data/colab/runs/joint_game_set/<id>)"
    )
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--ctx-oof", default="reports/context_residual/oof_context_residual.parquet")
    ap.add_argument("--elo-oof", default="data/injury_elo/oof_predictions.parquet")
    ap.add_argument("--stage-dir", default="data/colab/joint_game_set")
    ap.add_argument("--max-games", type=int, default=None)
    ap.add_argument("--n-boot", type=int, default=2000)
    a = ap.parse_args(argv)
    if a.stage:
        meta = stage_eval_inputs(a.db, a.ctx_oof, a.elo_oof, a.stage_dir, max_games=a.max_games)
        print(json.dumps({k: v for k, v in meta.items() if k != "corr"}))
        return 0
    if a.run:
        res = score_run(a.run, a.stage_dir, n_boot=a.n_boot)
        print(res["verdict"], json.dumps(res["rules"], default=str))
        return 0
    ap.error("give --stage or --run")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
