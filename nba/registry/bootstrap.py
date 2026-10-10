"""One-shot, idempotent registration of the current champions.

``bootstrap-production`` (maintainer runs it ONCE against the real DB):

1. Registers the GA-tuned MOV-adjusted Elo (``configs/mov_elo_tuned.yaml``)
   as ``rung0_mov_elo`` with its walk-forward out-of-fold metrics on the
   tunable seasons. The frozen holdout is NOT re-scored here (it is a spent
   touch; see docs/HOLDOUT_ACCESS_LOG.md): the holdout figures recorded
   in the yaml header are stored verbatim and labelled as documented, not
   recomputed.
2. Promotes it to production if (and only if) it passes the registry's
   multi-season evidence rule.
3. Considers the props candidate; promotes it only if it passes the same
   rule AND its own calibration checks, otherwise prints why it did not.

Re-running creates no duplicate versions: the registered row carries a
fingerprint of (params, seed, holdout season) and is reused.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.features.team_features import build_matchup_features
from nba.models.rung0_baselines import MovEloBaseline
from nba.registry.local import LocalRegistry
from nba.registry.metadata import build_run_metadata, build_tags
from nba.registry.ops import (
    check_promotable,
    fetch_versions,
    open_readonly,
    promote_checked,
)
from nba.truth.metrics import (
    accuracy,
    bootstrap_ci,
    brier_score,
    calibration_curve,
    log_loss,
)
from nba.truth.walkforward import make_walk_forward_folds, split_frozen_holdout

REPO_ROOT = Path(__file__).resolve().parents[2]
MOV_ELO_MODEL = "rung0_mov_elo"
PROPS_MODEL = "props_minutes_distributions"
MOV_ELO_CONFIG = "configs/mov_elo_tuned.yaml"
PARAM_NAMES = ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div")

#: Figures documented in the configs/mov_elo_tuned.yaml header (one confirmatory
#: touch of the frozen 2025-26 holdout, made at tuning time). Stored, not recomputed.
DOCUMENTED_HOLDOUT = {"holdout_log_loss": 0.6010, "holdout_brier": 0.2069}
DOCUMENTED_PLAIN_ELO_OOF_LOG_LOSS = 0.6271

#: Props promotion calibration limits (CLAUDE.md success criteria).
PROPS_MAX_ABS_BIAS = 0.5
PROPS_TARGET_COVERAGE = 0.80
PROPS_COVERAGE_TOL = 0.10


def compute_mov_elo_oof_metrics(
    tunable_df: pl.DataFrame, params: dict[str, float], seed: int = 0
) -> dict[str, Any]:
    """Pooled walk-forward OOF metrics for MOV-Elo on ``tunable_df`` (holdout excluded)."""
    folds = make_walk_forward_folds(tunable_df, min_train_games=1)
    ys: list[np.ndarray] = []
    ps: list[np.ndarray] = []
    ids: list[str] = []
    for fold in folds:
        model = MovEloBaseline(seed=seed, **params)
        model.fit(fold.train_df, fold.train_df.select("y").to_series().to_numpy())
        ps.append(model.predict(fold.test_df))
        ys.append(fold.test_df.select("y").to_series().to_numpy().astype(float))
        ids.extend(fold.test_df.select("game_id").to_series().to_list())
    if not ys:
        return {"n_games": 0, "backtest_scope": "none", "n_seasons": 0}
    y, p = np.concatenate(ys), np.concatenate(ps)
    per_game_ll = np.array([log_loss(y[i : i + 1], p[i : i + 1]) for i in range(len(y))])
    per_game_br = np.array([brier_score(y[i : i + 1], p[i : i + 1]) for i in range(len(y))])
    ll_ci = bootstrap_ci(per_game_ll, n_boot=1000, seed=seed)
    br_ci = bootstrap_ci(per_game_br, n_boot=1000, seed=seed)
    seasons = (
        tunable_df.filter(pl.col("game_id").is_in(ids)).select("season").unique().height
        if "season" in tunable_df.columns
        else 0
    )
    return {
        "log_loss": ll_ci.point,
        "log_loss_ci": [ll_ci.lo, ll_ci.hi],
        "brier": br_ci.point,
        "brier_ci": [br_ci.lo, br_ci.hi],
        "accuracy": accuracy(y, p),
        "ece": calibration_curve(y, p).ece,
        "n_games": int(len(y)),
        "n_seasons": int(seasons),
        "backtest_scope": "multi_season" if seasons >= 3 else "partial",
    }


def _fingerprint(params: dict[str, float], seed: int, holdout_season: int) -> str:
    blob = json.dumps({"params": params, "seed": seed, "holdout": holdout_season}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_params(config_path: Path) -> dict[str, float]:
    raw = yaml.safe_load(config_path.read_text())
    return {k: float(raw[k]) for k in PARAM_NAMES}


def _props_gate(metrics: dict[str, Any]) -> tuple[bool, str]:
    stats = metrics.get("stats")
    if not isinstance(stats, list) or not stats:
        return False, "no per-stat metrics"
    problems: list[str] = []
    for s in stats:
        if abs(s.get("mean_bias", float("inf"))) > PROPS_MAX_ABS_BIAS:
            problems.append(f"{s['stat']} bias {s['mean_bias']:+.3f}")
        cov = s.get("coverage")
        if cov is None or abs(cov - PROPS_TARGET_COVERAGE) > PROPS_COVERAGE_TOL:
            problems.append(f"{s['stat']} interval coverage {cov:.3f} vs target 0.80")
    return (not problems), "; ".join(problems)


def bootstrap_production(
    db_path: Path,
    store_dir: Path,
    *,
    config_path: Path | None = None,
    holdout_season: int = 2025,
    seed: int = 0,
    dry_run: bool = False,
    out: list[str] | None = None,
) -> list[str]:
    """Run the bootstrap; returns (and appends to ``out``) the lines of what was done."""
    log: list[str] = out if out is not None else []
    cfg = config_path or REPO_ROOT / MOV_ELO_CONFIG
    params = load_params(cfg)
    fp = _fingerprint(params, seed, holdout_season)

    ro = open_readonly(db_path)
    try:
        existing = [
            r for r in fetch_versions(ro, MOV_ELO_MODEL) if r.metadata.get("fingerprint") == fp
        ]
        metrics: dict[str, Any] = {}
        date_min: dt.date | None = None
        date_max: dt.date | None = None
        runtime = 0.0
        if not existing:
            t0 = time.time()
            split = split_frozen_holdout(build_matchup_features(ro), holdout_season)
            tunable = split.tunable_df
            metrics = compute_mov_elo_oof_metrics(tunable, params, seed)
            metrics.update(DOCUMENTED_HOLDOUT)
            metrics["holdout_source"] = (
                "documented in configs/mov_elo_tuned.yaml header; not recomputed"
            )
            metrics["plain_elo_oof_log_loss_documented"] = DOCUMENTED_PLAIN_ELO_OOF_LOG_LOSS
            dates = tunable.select("game_date").to_series()
            date_min, date_max = cast("dt.date", dates.min()), cast("dt.date", dates.max())
            runtime = time.time() - t0
        props_rows = fetch_versions(ro, PROPS_MODEL)
    finally:
        ro.close()

    if dry_run:
        log.append(f"[dry-run] mov_elo existing={bool(existing)} metrics={metrics or 'n/a'}")
        return log

    con = duckdb.connect(str(db_path))  # short-lived write connection
    try:
        reg = LocalRegistry(con, store_dir)
        if existing:
            version = existing[-1].version
            log.append(f"{MOV_ELO_MODEL} {version}: already registered (fingerprint {fp}); reused")
        else:
            version = reg.next_version(MOV_ELO_MODEL)
            with tempfile.TemporaryDirectory() as tmp:
                art = Path(tmp)
                (art / "config.yaml").write_text(cfg.read_text())
                (art / "report.md").write_text(
                    f"# {MOV_ELO_MODEL} {version}\n\nGA-tuned MOV-adjusted Elo.\n\n"
                    f"Walk-forward OOF (tunable seasons, holdout excluded): "
                    f"log loss {metrics.get('log_loss', float('nan')):.4f}, "
                    f"n={metrics.get('n_games')}.\n"
                    f"Holdout figures are the documented one-time confirmatory touch.\n"
                )
                meta = build_run_metadata(
                    game_date_min=date_min,
                    game_date_max=date_max,
                    possession_count=0,
                    seed=seed,
                    runtime_seconds=runtime,
                )
                meta["fingerprint"] = fp
                tags = build_tags(
                    rung=0,
                    model_method="mov_elo_ga_tuned",
                    source_config=MOV_ELO_CONFIG,
                )
                reg.log_model(MOV_ELO_MODEL, version, str(art), metrics, meta, tags)
            log.append(
                f"{MOV_ELO_MODEL} {version}: registered candidate "
                f"(OOF log loss {metrics.get('log_loss', float('nan')):.4f}, "
                f"n={metrics.get('n_games')}, seasons={metrics.get('n_seasons')}, "
                f"scope={metrics.get('backtest_scope')})"
            )
        try:
            log.append(promote_checked(reg, con, MOV_ELO_MODEL, version))
        except PermissionError as e:
            log.append(f"NOT PROMOTED: {e}")

        # props: promote only on evidence + calibration
        if not props_rows:
            log.append(f"{PROPS_MODEL}: no registered versions; nothing to do")
        else:
            best = props_rows[-1]
            if best.stage == "production":
                log.append(f"{PROPS_MODEL} {best.version}: already production")
            else:
                ok_ev, why_ev = check_promotable(best)
                ok_cal, why_cal = _props_gate(best.metrics)
                if ok_ev and ok_cal:
                    log.append(promote_checked(reg, con, PROPS_MODEL, best.version))
                else:
                    reason = why_cal if not ok_cal else why_ev
                    log.append(f"{PROPS_MODEL} {best.version}: NOT promoted ({reason})")
    finally:
        con.close()
    return log
