"""One-shot evaluation of a frozen route on the holdout season (pre-registered; docs/ROUTING.md).

NOT run during development. The CLI refuses unless

1. the route spec validates and its ``route_hash`` is quoted in
   ``docs/HOLDOUT_ACCESS_LOG.md`` (the pre-registration row exists);
2. no touch marker exists for that hash (``--allow-repeat`` relabels the run
   "repeated-use" and is never a confirmation);
3. every candidate of the spec has its OWN predictions for the holdout season in the OOF
   store under version ``<oof_version>+h<season>`` (a silent subset is refused).

The marker is written BEFORE any scoring, so a crash still spends the touch.

Keep rule (per target, router specs only; fixed before any holdout number exists):

* point delta vs the best single candidate (hindsight, on the holdout rows) <= -0.005 and the
  game-clustered 95% CI upper bound < 0;
* Benjamini-Hochberg q < 0.05 over the family (all targets of the same kind in one run);
* calibration not worse than the best single: props |cov80 - 0.80| <= best's + 0.01;
  binary targets (win, thresholds) ECE(10 bins) <= best's + 0.005;
* no slice (cold_start, season_phase, starter; n >= 200) regresses by more than +0.01.

A ``champion`` spec makes no routing claim; it is reported descriptively.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba.props.metrics import _clustered_boot_means, paired_score_delta_ci
from nba.registry.routing import (
    CANDIDATES,
    DEFAULT_LEDGER,
    read_spec,
    validate_spec,
)
from nba.stack.data import StackData, build_stack_data
from nba.stack.evaluate import benjamini_hochberg
from nba.stack.frozen import FrozenGate
from nba.stack.oof import read_oof
from nba.stack.scoring import is_binary, score_rows

EFFECT_FLOOR = 0.005
COV_TOL = 0.01
ECE_TOL = 0.005
SLICE_TOL = 0.01
MIN_SLICE_N = 200
ALPHA = 0.05
REQUIRED = ("cold_start", "season_phase", "starter")
TOUCH_DIR = Path("registry_store/holdout_touches")


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((np.asarray(p) * bins).astype(int), bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.sum() * abs(float(p[m].mean()) - float(y[m].mean()))
    return float(tot / len(p))


def cov80(q: np.ndarray, y: np.ndarray) -> float:
    return float(((y >= q[:, 1]) & (y <= q[:, 17])).mean())


def calibration_gap(target: str, pred: np.ndarray, y: np.ndarray) -> float:
    """Lower is better: ECE for binary targets, |cov80 - 0.8| for grids."""
    return ece(pred, y) if is_binary(target) else abs(cov80(pred, y) - 0.80)


@dataclass
class TargetResult:
    target: str
    route_hash: str
    kind: str
    n: int
    games: int
    best_single: str
    mean_scores: dict[str, float]
    delta_vs_best: float
    ci: tuple[float, float]
    p: float
    q: float = float("nan")
    calib: dict[str, float] = field(default_factory=dict)
    slice_regressions: list[str] = field(default_factory=list)
    vs_candidates: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    kept: bool = False
    reasons: list[str] = field(default_factory=list)


def score_route(
    spec: dict[str, Any],
    data: StackData,
    slices: dict[str, np.ndarray],
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> TargetResult:
    """Apply the FROZEN gate to the holdout rows and compare with each candidate."""
    target = spec["target"]
    gid = data.keys["game_id"].to_numpy()
    sc = data.cand_scores()
    means = {c: float(sc[:, j].mean()) for j, c in enumerate(data.candidates)}
    best = min(means, key=lambda c: means[c])
    bj = data.candidates.index(best)
    nan = float("nan")
    res = TargetResult(
        target=target,
        route_hash=spec["route_hash"],
        kind=spec["kind"],
        n=data.n,
        games=int(len(np.unique(gid))),
        best_single=best,
        mean_scores=dict(means),
        delta_vs_best=nan,
        ci=(nan, nan),
        p=nan,
    )
    if spec["kind"] != "router":
        res.reasons.append("champion route: no routing claim; candidates reported descriptively")
        return res
    gate = FrozenGate.from_dict(spec["gate"])
    pooled = gate.apply(data.context, data.cand)
    rs = score_rows(data.target, pooled, data.y)
    res.mean_scores["router"] = float(rs.mean())
    d = rs - sc[:, bj]
    ci = paired_score_delta_ci(rs, sc[:, bj], n_boot=n_boot, seed=seed, cluster_ids=gid)
    boot = _clustered_boot_means(d, gid, n_boot, np.random.default_rng(seed))
    res.delta_vs_best = float(ci.point)
    res.ci = (float(ci.lo), float(ci.hi))
    res.p = float(max(2.0 * min((boot >= 0).mean(), (boot <= 0).mean()), 1.0 / (n_boot + 1)))
    for j, c in enumerate(data.candidates):
        cj = paired_score_delta_ci(rs, sc[:, j], n_boot=n_boot, seed=seed, cluster_ids=gid)
        res.vs_candidates[c] = (float(cj.point), float(cj.lo), float(cj.hi))
    res.calib = {
        "router": calibration_gap(data.target, pooled, data.y),
        "best_single": calibration_gap(data.target, data.cand[:, bj], data.y),
    }
    for name, lab in slices.items():
        if name not in REQUIRED:
            continue
        for g in sorted(set(np.asarray(lab).astype(str).tolist())):
            m = np.asarray(lab).astype(str) == g
            if m.sum() >= MIN_SLICE_N and float(d[m].mean()) > SLICE_TOL:
                res.slice_regressions.append(f"{name}={g} {float(d[m].mean()):+.4f}")
    return res


def decide(results: list[TargetResult]) -> list[TargetResult]:
    """Apply BH over the routers in ``results`` and fill ``kept`` / ``reasons``."""
    routers = [r for r in results if r.kind == "router"]
    if routers:
        qs = benjamini_hochberg(np.array([r.p for r in routers]))
        for r, q in zip(routers, qs, strict=True):
            r.q = float(q)
    for r in routers:
        ok = True
        if not (r.delta_vs_best <= -EFFECT_FLOOR and r.ci[1] < 0):
            ok = False
            r.reasons.append(
                f"delta {r.delta_vs_best:+.4f} CI [{r.ci[0]:+.4f},{r.ci[1]:+.4f}] "
                f"fails floor {EFFECT_FLOOR} / CI<0 vs {r.best_single}"
            )
        if not r.q < ALPHA:
            ok = False
            r.reasons.append(f"BH q={r.q:.3f} >= {ALPHA}")
        tol = ECE_TOL if is_binary(r.target) else COV_TOL
        if r.calib["router"] > r.calib["best_single"] + tol:
            ok = False
            r.reasons.append(
                f"calibration worse: {r.calib['router']:.4f} vs {r.calib['best_single']:.4f}"
            )
        if r.slice_regressions:
            ok = False
            r.reasons += [f"slice regression {s}" for s in r.slice_regressions]
        if ok:
            r.reasons.append("all criteria met")
        r.kept = ok
    return results


def build_holdout_data(
    spec: dict[str, Any],
    gate_ctx: pd.DataFrame,
    outcomes: pd.DataFrame,
    oof_path: str | None,
) -> StackData:
    """Align each candidate's OWN holdout predictions (store version ``<v>+h<season>``)."""
    hs = int(spec["holdout_season"])
    stat = spec["target"].removeprefix("thr_")
    frames: dict[str, pd.DataFrame] = {}
    for c in spec["candidates"]:
        ver = f"{c['oof_version']}+h{hs}"
        f = read_oof(c["oof_model"], ver, stat, oof_path)
        if f.empty:
            raise LookupError(
                f"candidate {c['id']}: no holdout predictions in the store as "
                f"{c['oof_model']}/{ver}; refusing to evaluate a silent subset"
            )
        if not (f["season"] == hs).all():
            raise ValueError(f"candidate {c['id']}: rows outside holdout season {hs}")
        frames[c["id"]] = f
    data = build_stack_data(stat, frames, outcomes, gate_ctx, max_season=hs)
    if spec["target"].startswith("thr_"):
        from nba.stack.threshold import expand_thresholds

        data, _src = expand_thresholds(data, stat)
    return data


def touch(spec: dict[str, Any], touch_dir: Path, *, allow_repeat: bool) -> str:
    """Spend the touch (write the marker). Returns the status label for the run."""
    touch_dir.mkdir(parents=True, exist_ok=True)
    marker = touch_dir / f"{spec['route_hash']}.json"
    if marker.exists():
        if not allow_repeat:
            raise PermissionError(
                f"route {spec['route_hash']} already touched the holdout ({marker}); "
                "use --allow-repeat for a labelled repeated-use re-run"
            )
        return "repeated-use"
    marker.write_text(
        json.dumps(
            {
                "route_hash": spec["route_hash"],
                "target": spec["target"],
                "at": datetime.now(UTC).isoformat(),
            }
        )
    )
    return "clean-first-touch"


def require_preregistration(spec: dict[str, Any], ledger: Path) -> None:
    text = ledger.read_text() if ledger.exists() else ""
    if spec["route_hash"] not in text:
        raise PermissionError(
            f"route_hash {spec['route_hash']} is not quoted in {ledger}; pre-register first"
        )


def verdict_dict(r: TargetResult, status: str) -> dict[str, Any]:
    d = asdict(r)
    d["touch_status"] = status
    d["candidates_holdout_status"] = {
        c: CANDIDATES[c].holdout_status for c in r.mean_scores if c in CANDIDATES
    }
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("specs", nargs="+", help="route.json paths (same family: all props or win)")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--oof", default=None)
    ap.add_argument("--meta", default="data/colab/seq_props/seq_props_meta.parquet")
    ap.add_argument("--out", default="reports/routing")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--touch-dir", default=str(TOUCH_DIR))
    ap.add_argument("--allow-repeat", action="store_true")
    ap.add_argument("--n-boot", type=int, default=2000)
    a = ap.parse_args(argv)

    from nba.stack.populate import load_history, props_gate_context, props_outcomes

    specs = [read_spec(Path(p)) for p in a.specs]
    for s in specs:
        errs = validate_spec(s, ledger=Path(a.ledger), oof_path=a.oof)
        if errs:
            raise ValueError(f"{s['target']}: " + "; ".join(errs))
        require_preregistration(s, Path(a.ledger))
    if any(s["target"] == "win" for s in specs):
        raise NotImplementedError("win holdout loader: add with the win pre-registration run")
    hs = int(specs[0]["holdout_season"])
    status = [touch(s, Path(a.touch_dir), allow_repeat=a.allow_repeat) for s in specs]
    hist = load_history(a.db, hs)
    gate, sl = props_gate_context(hist, a.meta)
    results: list[TargetResult] = []
    for s in specs:
        stat = s["target"].removeprefix("thr_")
        data = build_holdout_data(s, gate, props_outcomes(hist, stat), a.oof)
        from nba.stack.evaluate import standard_slices

        m = data.keys[["game_id", "player_id"]].merge(sl, on=["game_id", "player_id"], how="left")
        results.append(score_route(s, data, standard_slices(m), n_boot=a.n_boot))
    decide(results)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for r, st in zip(results, status, strict=True):
        (out / f"verdict_{r.target}_{r.route_hash}.json").write_text(
            json.dumps(verdict_dict(r, st), indent=1, default=str)
        )
        print(r.target, "KEPT" if r.kept else "not kept", r.reasons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
