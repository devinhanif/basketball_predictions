"""Build route specs from the OOF store (2023-24 evidence only; season >= 2025 never loaded).

For each target and candidate set:

1. align the candidates' OOF rows with as-of context and outcomes
   (:func:`research.stack.data.build_stack_data`);
2. score every candidate (walk-forward OOF scores, no fitting);
3. walk-forward softmax (and, for non-threshold targets, LightGBM) gates in 30/60-day
   blocks, judged by the pre-registered ROUTER_V2 rule via ``evaluate_stack``;
4. descriptive per-cell evidence with the cell guardrails (:mod:`research.stack.cells`);
5. freeze a softmax gate fit on ALL fit-window rows and emit a :class:`RouteSpec`
   (``kind = router`` iff the walk-forward softmax router was KEPT in 2023-24, else
   ``champion`` = best single candidate).

Entry point: :func:`build_specs`. Nothing here writes to ``nba.duckdb``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from research.registry.routing import (
    CANDIDATES,
    EXTRA_FIT_SEASONS,
    FROZEN_SEASON,
    RouteSpec,
    candidate_ids_for,
)
from research.stack import FROZEN_SEASON as STACK_FROZEN
from research.stack.cells import cell_evidence
from research.stack.data import StackData, build_stack_data
from research.stack.evaluate import evaluate_stack, standard_slices
from research.stack.frozen import freeze_softmax
from research.stack.oof import read_oof
from research.stack.populate import (
    PROPS_GATE_FEATURES,
    PROPS_GATE_FEATURES_ARCH,
    attach_archetypes,
    props_gate_context,
    props_outcomes,
    win_outcomes_and_context,
)
from research.stack.router import (
    LGBMGate,
    RouterResult,
    SoftmaxGate,
    WalkForwardConfig,
    hard_bucket_walk_forward,
    walk_forward,
)
from research.stack.threshold import expand_thresholds, region_slices

assert FROZEN_SEASON == STACK_FROZEN

#: Fit windows follow the frozen season: bumping ``FROZEN_SEASON`` at rollover widens them.
FIT_SEASONS = {
    "full": tuple(range(2023, FROZEN_SEASON)),
    "ext": tuple(range(2024, FROZEN_SEASON)),
}
MAX_FIT_SEASON = FROZEN_SEASON - 1


@dataclass
class BuildInputs:
    """Everything loaded once and shared across targets."""

    hist: pd.DataFrame  # populate.load_history output (seasons <= 2024)
    gate_ctx: pd.DataFrame  # game_id, player_id + PROPS gate features (+ arch/pos)
    slice_tbl: pd.DataFrame  # game_id, player_id + slice labels
    with_arch: bool
    win: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None  # outcomes, ctx, slices


def prepare_inputs(
    db: str,
    meta_path: str | None,
    *,
    oof_path: str | None = None,
    arch_table: pd.DataFrame | None = None,
) -> BuildInputs:
    from research.stack.populate import load_history

    hist = load_history(db, MAX_FIT_SEASON)
    gate, sl = props_gate_context(hist, meta_path)
    with_arch = arch_table is not None
    if arch_table is not None:
        gate, sl = attach_archetypes(gate, sl, hist, arch_table)
    inp = BuildInputs(hist, gate, sl, with_arch)
    # win context uses the two Elo candidates' own as-of probabilities
    ids = candidate_ids_for("win", "full")
    p_by = {}
    for cid in ids:
        c = CANDIDATES[cid]
        f = read_oof(c.oof_name("win"), c.oof_version, "win", oof_path)
        p_by[cid] = f[["game_id", "p"]]
    inp.win = win_outcomes_and_context(db, p_by, MAX_FIT_SEASON)
    return inp


def _f(x: Any) -> float:
    return float(x)


def _restrict(data: StackData, seasons: tuple[int, ...]) -> StackData:
    return data.subset(data.keys["season"].isin(seasons).to_numpy())


def _slice_dict(
    keys: pd.DataFrame, ctx: pd.DataFrame, tbl: pd.DataFrame, *, props: bool
) -> dict[str, np.ndarray]:
    m = keys[["game_id", "player_id"]].merge(tbl, on=["game_id", "player_id"], how="left")
    out = standard_slices(m)
    if props:
        trend = ctx["min_trend"].to_numpy(dtype=float)
        out["minutes_trend"] = np.where(trend > 2, "up", np.where(trend < -2, "down", "flat"))
        if "teammate" in m:
            out["teammate"] = m["teammate"].fillna("unknown").astype(str).to_numpy()
        for col in ("archetype", "pos_group"):
            if col in m:
                out[col] = m[col].fillna("unk").astype(str).to_numpy()
    else:
        if "favorite" in m:
            out["favorite"] = m["favorite"].astype(str).to_numpy()
    return out


def _evidence(
    data: StackData,
    routers: dict[str, RouterResult],
    hard: RouterResult,
    slices: dict[str, np.ndarray],
    cfg: WalkForwardConfig,
    n_boot: int,
    required: tuple[str, ...],
) -> tuple[dict[str, Any], bool, str, Any]:
    rep = evaluate_stack(data, routers, hard, slices, n_boot=n_boot, required_slices=required)
    sc = data.cand_scores()
    seasons = data.keys["season"].to_numpy()
    cands: dict[str, Any] = {}
    for j, cid in enumerate(data.candidates):
        cands[cid] = {
            "mean_score": _f(sc[:, j].mean()),
            "n": int(data.n),
            "by_season": {
                str(s): _f(sc[seasons == s, j].mean()) for s in sorted(set(seasons.tolist()))
            },
        }
    kept = next(d for d in rep.decisions if d.router == "softmax").kept
    soft = next(c for c in rep.comparisons if c.router == "softmax" and c.versus == rep.best_single)
    ev = {
        "n_rows": int(data.n),
        "n_scored": rep.n_scored,
        "n_games": int(data.keys["game_id"].nunique()),
        "candidates": cands,
        "best_single": rep.best_single,
        "best_single_mean_score": rep.mean_scores[rep.best_single],
        "router_mean_score": rep.mean_scores["softmax"],
        "equal_pool_mean_score": rep.mean_scores["equal_pool"],
        "hard_route_mean_score": rep.mean_scores["hard_route"],
        "router_delta_vs_best": [soft.delta, soft.lo, soft.hi],
        "router_q": soft.q,
        "router_kept": kept,
        "decisions": {d.router: {"kept": d.kept, "reasons": d.reasons} for d in rep.decisions},
        "block_days": cfg.block_days,
    }
    return ev, kept, rep.best_single, rep


def _make_spec(
    target: str,
    cset: str,
    stat_for_names: str,
    data: StackData,
    ev: dict[str, Any],
    kept: bool,
    best: str,
    cells: list[dict[str, Any]],
    seasons: tuple[int, ...],
    wf_blocks: int,
) -> RouteSpec:
    frozen = freeze_softmax(data, max_season=MAX_FIT_SEASON)
    cands: list[dict[str, Any]] = []
    for cid in data.candidates:
        c = CANDIDATES[cid]
        cands.append(
            {
                "id": cid,
                "registry_model": c.registry_model,
                "registry_version": c.registry_version,
                "oof_model": c.oof_name(stat_for_names),
                "oof_version": c.oof_version,
                "holdout_status": c.holdout_status,
                "holdout_basis": c.holdout_basis,
                "note": c.note,
            }
        )
    fit = {
        "seasons": list(seasons),
        "date_min": frozen.fit_info["date_min"],
        "date_max": frozen.fit_info["date_max"],
        "n_rows": frozen.fit_info["n_rows"],
    }
    return RouteSpec(
        target=target,
        cset=cset,
        kind="router" if kept else "champion",
        champion=best,
        candidates=cands,
        holdout_season=FROZEN_SEASON,
        fit_window=fit,
        gate=frozen.to_dict() if kept else None,
        context_features=frozen.features if kept else [],
        evidence=ev,
        cells=cells,
        walk_forward={"gate": "softmax", "blocks": wf_blocks, "min_train_rows_rule": "see cfg"},
    )


def _cfg(n_rows: int, *, thr: bool) -> WalkForwardConfig:
    return WalkForwardConfig(
        block_days=60 if thr else 30, min_train_rows=max(500, min(3000, n_rows // 10))
    )


def _run_one(
    target: str,
    cset: str,
    data: StackData,
    slices: dict[str, np.ndarray],
    hard_bucket: np.ndarray,
    seasons: tuple[int, ...],
    stat_for_names: str,
    n_boot: int,
    required: tuple[str, ...],
    use_lgbm: bool,
) -> RouteSpec:
    cfg = _cfg(data.n, thr=target.startswith("thr_"))
    routers: dict[str, RouterResult] = {
        "softmax": walk_forward(data, SoftmaxGate, cfg, name="softmax")
    }
    if use_lgbm:
        routers["lgbm"] = walk_forward(data, lambda: LGBMGate(), cfg, name="lgbm")
    hard = hard_bucket_walk_forward(data, hard_bucket, cfg)
    ev, kept, best, _rep = _evidence(data, routers, hard, slices, cfg, n_boot, required)
    champ, cells = cell_evidence(data, slices)
    cell_dicts = [c.to_dict() for c in cells]
    ev["cell_champion"] = champ
    return _make_spec(
        target,
        cset,
        stat_for_names,
        data,
        ev,
        kept,
        best,
        cell_dicts,
        seasons,
        len(routers["softmax"].blocks),
    )


def build_specs(
    inp: BuildInputs,
    *,
    oof_path: str | None,
    stats: tuple[str, ...] = ("pts", "reb", "ast", "fg3m"),
    sets: tuple[str, ...] = ("full", "ext"),
    thresholds: bool = True,
    win: bool = True,
    n_boot: int = 1000,
    progress: Callable[[str], None] = lambda s: None,
) -> list[RouteSpec]:
    specs: list[RouteSpec] = []
    gate_cols = PROPS_GATE_FEATURES_ARCH if inp.with_arch else PROPS_GATE_FEATURES
    gate_ctx = inp.gate_ctx[["game_id", "player_id", *gate_cols]]
    if win and inp.win is not None:
        outcomes, wctx, wsl = inp.win
        ids = candidate_ids_for("win", "full")
        frames = {}
        for cid in ids:
            c = CANDIDATES[cid]
            f = read_oof(c.oof_name("win"), c.oof_version, "win", oof_path)
            frames[cid] = f[f["season"].isin(FIT_SEASONS["full"])]
        data = _restrict(
            build_stack_data("win", frames, outcomes, wctx, max_season=MAX_FIT_SEASON),
            FIT_SEASONS["full"],
        )
        sl = _slice_dict(data.keys, data.context, wsl, props=False)
        hard = np.asarray(sl["favorite"])
        progress("win")
        specs.append(
            _run_one(
                "win", "full", data, sl, hard, FIT_SEASONS["full"], "win", n_boot,
                ("season_phase",), False,
            )
        )  # fmt: skip
    for stat in stats:
        outc = props_outcomes(inp.hist, stat)
        for cset in sets:
            ids = candidate_ids_for(stat, cset)
            seasons = FIT_SEASONS.get(cset) or EXTRA_FIT_SEASONS[cset]
            frames = {}
            for cid in ids:
                c = CANDIDATES[cid]
                f = read_oof(c.oof_name(stat), c.oof_version, stat, oof_path)
                frames[cid] = f[f["season"].isin(seasons)]
            data = _restrict(
                build_stack_data(stat, frames, outc, gate_ctx, max_season=MAX_FIT_SEASON), seasons
            )
            sl = _slice_dict(data.keys, data.context, inp.slice_tbl, props=True)
            progress(f"{stat}/{cset} n={data.n}")
            specs.append(
                _run_one(
                    stat, cset, data, sl, sl["cold_start"], seasons, stat, n_boot,
                    ("cold_start", "season_phase", "starter"), True,
                )
            )  # fmt: skip
            if thresholds:
                tdata, src = expand_thresholds(data, stat)
                tsl = {k: np.asarray(v)[src] for k, v in sl.items()}
                tsl.update(region_slices(tdata.context))
                progress(f"thr_{stat}/{cset} n={tdata.n}")
                specs.append(
                    _run_one(
                        f"thr_{stat}", cset, tdata, tsl, tsl["line_region"], seasons, stat,
                        n_boot, ("cold_start", "season_phase", "starter"), False,
                    )
                )  # fmt: skip
    return specs


def joint_parlay_spec() -> RouteSpec:
    """Champion-only spec for the joint-parlay target: no OOF exists per candidate, so no
    router can be fit; evidence is carried as text from reports/parlay/."""
    from research.registry.routing import JOINT_CANDIDATES

    cands = [
        {
            "id": j["id"],
            "registry_model": None,
            "registry_version": None,
            "oof_model": "-",
            "oof_version": "-",
            "holdout_status": j["holdout_status"],
            "holdout_basis": "parlay work used 2023-24 only; 2025 not loaded",
            "note": j["evidence"],
        }
        for j in JOINT_CANDIDATES
    ]
    return RouteSpec(
        target="joint_parlay",
        cset="full",
        kind="champion",
        champion="gaussian_copula",
        candidates=cands,
        holdout_season=FROZEN_SEASON,
        fit_window={"seasons": [2023, 2024], "date_min": None, "date_max": None, "n_rows": 15798},
        evidence={"n_scored": 15798, "n_games": 2633, "candidates": {}},
    )
