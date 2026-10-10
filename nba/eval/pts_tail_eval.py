"""Points upper-tail calibration screen for the production context_residual model.

Pre-registration: docs/PTS_TAIL.md (frozen before any candidate number was computed).
Two stages, both read-only on the DuckDBs and refusing season 2025:

    uv run python -m nba.eval.pts_tail_eval collect      # one walk-forward fit pass (pts only)
    uv run python -m nba.eval.pts_tail_eval production   # production-only diagnostics
    uv run python -m nba.eval.pts_tail_eval candidates   # 2023 selection, 2024 report

``collect`` stores, per month block, the production heads' centre/scale for the test rows and the
held-out calibration arrays; candidates are pure functions of those, so no model is refitted per
candidate. Season 2023 is the selection season, 2024 the report season.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
)
from nba.props.metrics import mean_bias_ci, paired_score_delta_ci
from nba.props.pts_tail import tail_quantiles

OUT_DIR = Path("reports/pts_tail")
THRESHOLDS: tuple[int, ...] = (15, 20, 25, 30, 35)
EXTRA_THRESHOLDS: tuple[int, ...] = (10,)
EPS = 0.5 / 199  # one half grid step: the smallest non-zero P(>= N) production can emit
COVER_TAUS: tuple[float, ...] = (0.01, 0.05, 0.10, 0.90, 0.95, 0.99)
CANDIDATES: tuple[str, ...] = ("sqrt", "mondrian_up", "gamma_tail")
TLL_FLOOR = -0.002  # required threshold-log-loss improvement
CRPS_TOL = 0.002  # CRPS may not worsen by more than this
N_BOOT = 2000
SELECT_SEASON, REPORT_SEASON = 2023, 2024


# --------------------------------------------------------------------------- collect


def calibration_arrays(model: ContextResidualModel, train: pl.DataFrame) -> dict[str, np.ndarray]:
    """Calibration-window arrays (y, centre, scale, z) the tail arms are built from.

    Recomputed from the fitted model and its own calibration split, so the production
    model class carries no tail-specific state (the pts_tail flag was removed from it on
    2026-10-09; these arms reached their verdict in T129-T135 and live here only).
    """
    _fit_df, cal_df = split_calibration(train, model.cfg)
    c, s = model.predict_components(cal_df)
    y = cal_df["y"].to_numpy()
    return {"y": y, "c": c, "s": s, "z": (y - c) / s}


def collect(
    feats: pl.DataFrame, cfg: ContextResidualConfig, test_seasons: tuple[int, ...] = TEST_SEASONS
) -> dict[str, np.ndarray]:
    """Walk-forward (month blocks, same protocol as the production eval), pts only."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, "pts")
    names = stat_feature_names("pts")
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk")
    }
    cal: dict[str, list[np.ndarray]] = {k: [] for k in ("y", "c", "s", "z", "blk")}
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel("pts", cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        rows["gid"].append(block["game_id"].to_numpy())
        rows["pid"].append(block["player_id"].to_numpy())
        rows["season"].append(block["season"].to_numpy())
        rows["y"].append(block["y"].to_numpy())
        rows["c"].append(c)
        rows["s"].append(s)
        rows["blk"].append(np.full(n, b))
        arrays = calibration_arrays(model, train)
        for k in ("y", "c", "s", "z"):
            cal[k].append(arrays[k])
        cal["blk"].append(np.full(len(arrays["y"]), b))
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


# --------------------------------------------------------------------------- arms


def arm_quantiles(
    kind: str, comp: dict[str, np.ndarray], taus: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """Quantiles ``[n_masked, len(taus)]`` of arm ``kind`` for the masked rows (block-wise)."""
    idx = np.flatnonzero(mask)
    out = np.empty((len(idx), len(taus)))
    blk = comp["row_blk"][idx]
    for b in np.unique(blk):
        sel = blk == b
        cm = comp["cal_blk"] == b
        out[sel] = tail_quantiles(
            kind,
            y_cal=comp["cal_y"][cm],
            c_cal=comp["cal_c"][cm],
            s_cal=comp["cal_s"][cm],
            z_cal=comp["cal_z"][cm],
            c=comp["row_c"][idx[sel]],
            s=comp["row_s"][idx[sel]],
            taus=taus,
        )
    return out


def p_ge(q199: np.ndarray, n: int) -> np.ndarray:
    """Production definition: share of the 199-grid above N - 0.5 (nba.props.forward)."""
    return np.asarray((q199 > n - 0.5).mean(axis=1))


def ll_rows(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def ece_eq_mass(p: np.ndarray, ev: np.ndarray, bins: int = 10) -> float:
    o = np.argsort(p)
    pp, ee = p[o], ev[o].astype(float)
    parts = np.array_split(np.arange(len(pp)), bins)
    return float(sum(len(g) * abs(pp[g].mean() - ee[g].mean()) for g in parts) / len(pp))


def arm_scores(
    kind: str, comp: dict[str, np.ndarray], mask: np.ndarray, seed: int = 0
) -> dict[str, Any]:
    """Per-row scores and diagnostics for one arm on the masked rows."""
    y = comp["row_y"][mask]
    q199 = arm_quantiles(kind, comp, CRPS_TAUS, mask)
    qc = arm_quantiles(kind, comp, np.array(COVER_TAUS), mask)
    thr = THRESHOLDS + EXTRA_THRESHOLDS
    p = {n: p_ge(q199, n) for n in thr}
    ll = {n: ll_rows(p[n], y >= n) for n in thr}
    # randomized PIT on the 199-grid with the N-0.5 continuity convention
    rng = np.random.default_rng(seed)
    f_lo = (q199 < (y - 0.5)[:, None]).mean(axis=1)
    f_hi = (q199 <= (y + 0.5)[:, None]).mean(axis=1)
    pit = f_lo + rng.random(len(y)) * (f_hi - f_lo)
    return {
        "y": y,
        "crps": crps_from_quantiles(q199, y),
        "tll": np.mean([ll[n] for n in THRESHOLDS], axis=0),
        "ll": ll,
        "p": p,
        "cover": {t: (y <= qc[:, i]).astype(float) for i, t in enumerate(COVER_TAUS)},
        "pit": pit,
    }


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def describe_arm(sc: dict[str, Any], gid: np.ndarray) -> dict[str, Any]:
    y = sc["y"]
    pooled_p = np.concatenate([sc["p"][n] for n in THRESHOLDS])
    pooled_e = np.concatenate([y >= n for n in THRESHOLDS])
    return {
        "n": int(len(y)),
        "n_games": int(len(np.unique(gid))),
        "crps": float(sc["crps"].mean()),
        "tll": float(sc["tll"].mean()),
        "ll_by_thr": {str(n): float(sc["ll"][n].mean()) for n in THRESHOLDS + EXTRA_THRESHOLDS},
        "p_mean_by_thr": {str(n): float(sc["p"][n].mean()) for n in THRESHOLDS + EXTRA_THRESHOLDS},
        "obs_by_thr": {str(n): float((y >= n).mean()) for n in THRESHOLDS + EXTRA_THRESHOLDS},
        "n_events_by_thr": {str(n): int((y >= n).sum()) for n in THRESHOLDS + EXTRA_THRESHOLDS},
        "ece_pooled": ece_eq_mass(pooled_p, pooled_e),
        "cover_le_q": {str(t): float(v.mean()) for t, v in sc["cover"].items()},
        "pit_hist10": np.histogram(sc["pit"], bins=10, range=(0.0, 1.0))[0].tolist(),
    }


def compare(
    cand: dict[str, Any], base: dict[str, Any], gid: np.ndarray, n_boot: int = N_BOOT
) -> dict[str, Any]:
    d_tll = cand["tll"] - base["tll"]
    d_crps = cand["crps"] - base["crps"]
    return {
        "d_tll": _ci(d_tll, gid, n_boot),
        "p_tll": boot_p_value(d_tll, gid, n_boot),
        "d_crps": _ci(d_crps, gid, n_boot),
        "d_ll_by_thr": {
            str(n): _ci(cand["ll"][n] - base["ll"][n], gid, n_boot)
            for n in THRESHOLDS + EXTRA_THRESHOLDS
        },
    }


# --------------------------------------------------------------------------- stages


def _season_mask(comp: dict[str, np.ndarray], season: int) -> np.ndarray:
    if season > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    return np.asarray(comp["row_season"] == season)


def production_diagnostics(comp: dict[str, np.ndarray], n_boot: int = N_BOOT) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        m = _season_mask(comp, season)
        sc = arm_scores("off", comp, m)
        gid = comp["row_gid"][m]
        d = describe_arm(sc, gid)
        q_mean = comp["row_c"][m]  # centre only; the stored mean adds s * mean(z)
        b = mean_bias_ci(q_mean, sc["y"], n_boot=n_boot, cluster_ids=gid)
        d["centre_bias"] = {"point": float(b.point), "lo": float(b.lo), "hi": float(b.hi)}
        out[str(season)] = d
    return out


def candidate_screen(comp: dict[str, np.ndarray], n_boot: int = N_BOOT) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        m = _season_mask(comp, season)
        gid = comp["row_gid"][m]
        base = arm_scores("off", comp, m)
        season_res: dict[str, Any] = {"off": describe_arm(base, gid)}
        for kind in CANDIDATES:
            sc = arm_scores(kind, comp, m)
            season_res[kind] = {**describe_arm(sc, gid), **compare(sc, base, gid, n_boot)}
        adj = bh_adjust(np.array([season_res[k]["p_tll"] for k in CANDIDATES]))
        for k, a in zip(CANDIDATES, adj, strict=True):
            season_res[k]["p_tll_bh"] = float(a)
            r = season_res[k]
            r["checks"] = {
                "tll_floor": r["d_tll"]["point"] <= TLL_FLOOR,
                "tll_ci_below_0": r["d_tll"]["hi"] < 0.0,
                "bh_p": r["p_tll_bh"] < 0.05,
                "crps_tol": r["d_crps"]["point"] <= CRPS_TOL,
            }
            r["pass"] = all(r["checks"].values())
        res[str(season)] = season_res
    sel = [k for k in CANDIDATES if res[str(SELECT_SEASON)][k]["pass"]]
    chosen = min(sel, key=lambda k: res[str(SELECT_SEASON)][k]["d_tll"]["point"]) if sel else None
    res["selected_on_2023"] = chosen
    res["confirmed_on_2024"] = bool(chosen and res[str(REPORT_SEASON)][chosen]["pass"])
    return res


def run_collect(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: Path = OUT_DIR,
) -> Path:
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo_params)
    comp = collect(feats, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "components.npz"
    np.savez_compressed(path, **comp)  # type: ignore[arg-type]
    return path


def _load(out_dir: Path = OUT_DIR) -> dict[str, np.ndarray]:
    z = np.load(out_dir / "components.npz", allow_pickle=True)
    return {k: z[k] for k in z.files}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("collect", "production", "candidates"))
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.stage == "collect":
        print(run_collect(a.db_path, out_dir=out))
        return 0
    comp = _load(out)
    res = production_diagnostics(comp) if a.stage == "production" else candidate_screen(comp)
    (out / f"{a.stage}.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
