"""Pre-registered screen: do as-of tracking/hustle features improve context-residual props?

PRE-REGISTRATION: docs/TRACKING_SCREEN.md (frozen before any tracking value was
read). Summary of the rule implemented here:

* Arm A = production ``props_context_residual`` (``configs/context_residual.yaml``,
  production feature names). Arm B = identical config/seed/rows/folds plus one
  family's as-of columns (``research.features.tracking_features``). Cells = family x
  target stat (m = 9; hustle-dependent families fall back to T-only subsets if
  hustle is not loaded).
* Walk-forward calendar-month blocks over 2023-2024 (2022 warm-up); REPORT season
  2024; nothing is selected on either season. Season 2025 is never loaded.
* Metric: per-row CRPS delta B - A, game-clustered 95% bootstrap CI (2000), BH over
  the cells at q = 0.05. PASS needs: delta <= -0.005, CI upper < 0, BH q <= 0.05,
  coverage guard (|cov80_B - .8| <= |cov80_A - .8| + .03), bias guard
  (|bias_B| <= |bias_A| + .05 and <= .5), cold-start guard (5 <= prior played < 10,
  n >= 300: CI lower bound <= 0), and a clean missingness audit.
* Missingness audit before any fit: asymmetry of NULL rate between rows with
  minutes >= 5 and 0 < minutes < 5 (datamanifest threshold); >= 0.5 STOPS the run.

A PASS never wires anything into production; it only recommends review plus a
pre-registered holdout touch.

Run (maintainer):
    uv run python -m research.eval.tracking_screen --out-dir reports/tracking_screen
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
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    stat_feature_names,
    stat_frame,
)
from nba.props.metrics import interval_coverage, mean_bias_ci, paired_score_delta_ci
from research.features.tracking_features import (
    ALL_FAMILIES,
    FAMILIES,
    FAMILIES_T_ONLY,
    build_tracking_features,
    family_cells,
    load_tracking_frames,
    own_game_coverage,
)

TEST_SEASONS: tuple[int, ...] = (2023, 2024)
REPORT_SEASON = 2024
MAX_SEASON = 2024
CRPS_FLOOR = 0.005
COV_SLACK = 0.03
BIAS_SLACK = 0.05
BIAS_MAX = 0.5
COLD_MAX_PRIOR = 10
COLD_MIN_N = 300
ASYMMETRY_STOP = 0.5
PLAYED_MINUTES = 5.0
BH_Q = 0.05
N_BOOT = 2000


# --------------------------------------------------------------------------- audit


def missingness_audit(
    feats: pl.DataFrame, minutes: pl.DataFrame, columns: list[str], has_row: pl.Series
) -> dict[str, Any]:
    """NULL-rate asymmetry (minutes >= 5 vs 0 < minutes < 5) per tracking column.

    ``feats`` rows are all PLAYED rows (minutes > 0); ``minutes`` carries
    (game_id, player_id, minutes). ``has_row`` is the diagnostic own-game
    coverage flag aligned to ``feats``. Returns per-column rates and a STOP flag."""
    df = feats.join(minutes, on=["game_id", "player_id"], how="left").with_columns(
        has_row.alias("_has_row")
    )
    played = df["minutes"] >= PLAYED_MINUTES
    out: dict[str, Any] = {}
    worst = 0.0
    n_p, n_u = int(played.sum()), int((~played).sum())
    for c in [*columns, "_has_row"]:
        s = df[c]
        null = ~s if c == "_has_row" else s.is_null()
        nul = null.to_numpy()
        pl_mask = played.to_numpy()
        rp = float(nul[pl_mask].mean()) if n_p else float("nan")
        ru = float(nul[~pl_mask].mean()) if n_u else float("nan")
        asym = abs(rp - ru) if n_p and n_u else float("nan")
        worst = max(worst, asym if asym == asym else 0.0)
        lab = None
        if "pts" in df.columns and null.sum() > 0 and (~null).sum() > 0:
            ind = null.cast(pl.Float64).to_numpy()
            y = df["pts"].cast(pl.Float64).to_numpy()
            lab = float(np.corrcoef(ind, y)[0, 1])
        out[c] = {"null_played": rp, "null_unplayed": ru, "asymmetry": asym, "corr_null_pts": lab}
    return {
        "n_played": n_p,
        "n_unplayed": n_u,
        "columns": out,
        "max_asymmetry": worst,
        "stop": bool(worst >= ASYMMETRY_STOP),
    }


# --------------------------------------------------------------------------- walk-forward


def walk_forward_arm(
    feats: pl.DataFrame,
    stat: str,
    names: list[str],
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> pl.DataFrame:
    """Month-block walk-forward (the production procedure) for one feature set."""
    sf = stat_feature_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    parts: list[pl.DataFrame] = []
    for start, end in month_blocks(test_all):
        block = test_all.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        mean, q199 = model.predict(block, CRPS_TAUS)
        _, q2 = model.predict(block, np.array([0.1, 0.9]))
        parts.append(
            block.select(
                "game_id", "player_id", "game_date", "season", "n_prior", "y"
            ).with_columns(
                pl.Series("mean", mean),
                pl.Series("crps", crps_from_quantiles(q199, block["y"].to_numpy())),
                pl.Series("q10", q2[:, 0]),
                pl.Series("q90", q2[:, 1]),
            )
        )
    return pl.concat(parts) if parts else pl.DataFrame()


def stat_feature_frame(feats: pl.DataFrame, stat: str) -> pl.DataFrame:
    return stat_frame(feats, stat).sort(["game_date", "game_id", "player_id"])


# --------------------------------------------------------------------------- scoring


def _bias(res: pl.DataFrame) -> float:
    return float((res["mean"] - res["y"]).mean())  # type: ignore[arg-type]


def compare_arms(
    a: pl.DataFrame, b: pl.DataFrame, n_boot: int = N_BOOT, report_season: int = REPORT_SEASON
) -> dict[str, Any]:
    """Paired comparison of arm B vs arm A on the SAME rows (asserted)."""
    keys = ["game_id", "player_id"]
    if a.height != b.height or not a.select(keys).equals(b.select(keys)):
        raise ValueError("arms were scored on different rows")
    out: dict[str, Any] = {}
    for label, sel in (
        ("report", pl.col("season") == report_season),
        ("descr_2023", pl.col("season") == 2023),
    ):
        ma, mb = a.filter(sel), b.filter(sel)
        if ma.is_empty():
            continue
        gid = ma["game_id"].to_numpy()
        ca, cb = ma["crps"].to_numpy(), mb["crps"].to_numpy()
        ci = paired_score_delta_ci(cb, ca, n_boot=n_boot, cluster_ids=gid)
        ent: dict[str, Any] = {
            "n": int(ma.height),
            "n_games": int(len(np.unique(gid))),
            "crps_A": float(ca.mean()),
            "crps_B": float(cb.mean()),
            "delta": [ci.point, ci.lo, ci.hi],
            "p": boot_p_value(cb - ca, gid, n_boot),
            "cov80_A": interval_coverage(
                ma["q10"].to_numpy(), ma["q90"].to_numpy(), ma["y"].to_numpy()
            ),
            "cov80_B": interval_coverage(
                mb["q10"].to_numpy(), mb["q90"].to_numpy(), mb["y"].to_numpy()
            ),
            "bias_A": _bias(ma),
            "bias_B": _bias(mb),
        }
        if label == "report":
            cold = (ma["n_prior"] < COLD_MAX_PRIOR).to_numpy()
            ent["cold_n"] = int(cold.sum())
            if cold.sum() > 0:
                cci = paired_score_delta_ci(
                    cb[cold], ca[cold], n_boot=n_boot, cluster_ids=gid[cold]
                )
                ent["cold_delta"] = [cci.point, cci.lo, cci.hi]
            bci = mean_bias_ci(
                mb["mean"].to_numpy(), mb["y"].to_numpy(), n_boot=500, cluster_ids=gid
            )
            ent["bias_B_ci"] = [bci.point, bci.lo, bci.hi]
        out[label] = ent
    return out


def apply_rule(cells: dict[str, dict[str, Any]], audit_stop: bool) -> dict[str, dict[str, Any]]:
    """Pre-registered decision rule over all cells (BH across the cells that ran)."""
    keys = list(cells)
    adj = bh_adjust(np.array([float(cells[k]["report"]["p"]) for k in keys]))
    verdicts: dict[str, dict[str, Any]] = {}
    for k, q in zip(keys, adj, strict=True):
        r = cells[k]["report"]
        pt, _lo, hi = r["delta"]
        cold_ok: bool | None
        if r.get("cold_n", 0) >= COLD_MIN_N and "cold_delta" in r:
            cold_ok = bool(r["cold_delta"][1] <= 0)
        else:
            cold_ok = None  # not judged (too few rows)
        checks = {
            "floor": bool(pt <= -CRPS_FLOOR),
            "ci_excludes_0": bool(hi < 0),
            "bh_q": bool(q <= BH_Q),
            "cov80_guard": bool(abs(r["cov80_B"] - 0.8) <= abs(r["cov80_A"] - 0.8) + COV_SLACK),
            "bias_guard": bool(
                abs(r["bias_B"]) <= abs(r["bias_A"]) + BIAS_SLACK and abs(r["bias_B"]) <= BIAS_MAX
            ),
            "cold_start_guard": True if cold_ok is None else cold_ok,
            "audit_clean": not audit_stop,
        }
        verdicts[k] = {
            "bh_adj_p": float(q),
            "checks": checks,
            "cold_judged": cold_ok is not None,
            "pass": all(checks.values()),
        }
    return verdicts


# --------------------------------------------------------------------------- orchestration


def build_screen_frame(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    elo_params: dict[str, float],
    tracking: pl.DataFrame,
    hustle: pl.DataFrame | None,
    cfg: ContextResidualConfig,
    family_names: tuple[str, ...],
) -> tuple[pl.DataFrame, list[str], pl.Series]:
    """Production feature frame left-joined with the as-of tracking columns.

    Also returns the tracking column names and the diagnostic own-game coverage."""
    games_d = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = pgs.join(games_d.select(["game_id", "game_date"]), on="game_id").filter(
        pl.col("minutes") > 0
    )
    tfeat = build_tracking_features(played, tracking, hustle, static, family_names)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo_params, tracking_feats=tfeat)
    cols = [c for c in tfeat.columns if c not in ("game_id", "player_id")]
    cover = own_game_coverage(feats, tracking)
    return feats, cols, cover


def run_screen_frames(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    elo_params: dict[str, float],
    tracking: pl.DataFrame,
    hustle: pl.DataFrame | None,
    cfg: ContextResidualConfig | None = None,
    n_boot: int = N_BOOT,
    cache_dir: Path | None = None,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    stats: tuple[str, ...] = PROP_STATS,
    report_season: int = REPORT_SEASON,
    families: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    cfg = cfg or ContextResidualConfig()
    mx = games["season"].max()
    if mx is not None and int(mx) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    fam_names = families or (
        tuple(FAMILIES) if hustle is not None else ("passing", "shooting", *FAMILIES_T_ONLY)
    )
    cells_def = [c for c in family_cells(fam_names) if c[1] in stats]
    feats, tcols, cover = build_screen_frame(
        games, pgs, static, avail, elo_params, tracking, hustle, cfg, fam_names
    )
    audit = missingness_audit(
        feats,
        pgs.select("game_id", "player_id", "minutes").unique(["game_id", "player_id"]),
        tcols,
        cover,
    )
    result: dict[str, Any] = {
        "families_run": list(fam_names),
        "families_not_run": [f for f in ALL_FAMILIES if f not in fam_names],
        "hustle_loaded": hustle is not None,
        "audit": audit,
    }
    if audit["stop"]:
        result["status"] = "STOP: missingness asymmetry >= 0.5 (possible leak); no model fit"
        return result
    feats = feats.with_columns(cover.alias("_own_has_trk"))
    arm_a: dict[str, pl.DataFrame] = {}
    cells: dict[str, dict[str, Any]] = {}
    for fam, stat in cells_def:
        if stat not in arm_a:
            arm_a[stat] = _cached(
                cache_dir,
                f"A_{stat}",
                lambda s=stat: walk_forward_arm(feats, s, stat_feature_names(s), cfg, test_seasons),
            )
        extra = tuple(ALL_FAMILIES[fam].feature_names)
        b = _cached(
            cache_dir,
            f"B_{fam}_{stat}",
            lambda s=stat, e=extra: walk_forward_arm(
                feats, s, stat_feature_names(s, e), cfg, test_seasons
            ),
        )
        cmp = compare_arms(arm_a[stat], b, n_boot, report_season)
        # descriptive sensitivity: test rows whose own game has tracking rows
        has = feats.select("game_id", "player_id", "_own_has_trk")
        sel = arm_a[stat].join(has, on=["game_id", "player_id"], how="left")["_own_has_trk"]
        mask = sel.fill_null(False).to_numpy() & (arm_a[stat]["season"] == report_season).to_numpy()
        if mask.sum() > 50:
            ca, cb = arm_a[stat]["crps"].to_numpy()[mask], b["crps"].to_numpy()[mask]
            ci = paired_score_delta_ci(
                cb, ca, n_boot=500, cluster_ids=arm_a[stat]["game_id"].to_numpy()[mask]
            )
            cmp["sensitivity_own_game_tracked"] = {
                "n": int(mask.sum()),
                "delta": [ci.point, ci.lo, ci.hi],
            }
        cells[f"{fam}|{stat}"] = cmp
    result["cells"] = cells
    result["verdicts"] = apply_rule(cells, audit["stop"]) if cells else {}
    result["status"] = "ok"
    return result


def _cached(cache_dir: Path | None, name: str, fn: Any) -> pl.DataFrame:
    if cache_dir is None:
        return fn()  # type: ignore[no-any-return]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.parquet"
    if path.exists():
        return pl.read_parquet(path)
    df: pl.DataFrame = fn()
    df.write_parquet(path)
    return df


def format_report(res: dict[str, Any]) -> str:
    lines = [f"status: {res['status']}", f"hustle loaded: {res['hustle_loaded']}"]
    a = res["audit"]
    lines.append(
        f"missingness audit: max asymmetry {a['max_asymmetry']:.4f} (stop at {ASYMMETRY_STOP})"
    )
    if "cells" not in res:
        return "\n".join(lines)
    lines.append(
        "cell | n | CRPS A | CRPS B | delta [95% CI] | BH p | cov80 A/B | bias A/B | "
        "cold n, delta | 2023 delta | checks failed | PASS"
    )
    for k, c in res["cells"].items():
        r, v = c["report"], res["verdicts"][k]
        d = r["delta"]
        cd = r.get("cold_delta")
        d23 = c.get("descr_2023", {}).get("delta", [float("nan")] * 3)
        failed = [n for n, ok in v["checks"].items() if not ok]
        lines.append(
            f"{k} | {r['n']} | {r['crps_A']:.4f} | {r['crps_B']:.4f} | "
            f"{d[0]:+.4f} [{d[1]:+.4f},{d[2]:+.4f}] | {v['bh_adj_p']:.3g} | "
            f"{r['cov80_A']:.3f}/{r['cov80_B']:.3f} | {r['bias_A']:+.3f}/{r['bias_B']:+.3f} | "
            f"{r.get('cold_n', 0)}, "
            f"{'n/a' if cd is None else f'{cd[0]:+.4f} [{cd[1]:+.4f},{cd[2]:+.4f}]'} | "
            f"{d23[0]:+.4f} | {failed or '-'} | {v['pass']}"
        )
    return "\n".join(lines)


def run(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: str = "reports/tracking_screen",
    with_hustle: bool = True,
    families: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Real-data entrypoint (read-only DB; season <= 2024 only)."""
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db, max_season=MAX_SEASON)
    tracking, hustle = load_tracking_frames(db_path, with_hustle)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = run_screen_frames(
        games,
        pgs,
        static,
        avail,
        elo_params,
        tracking,
        hustle,
        cfg,
        cache_dir=out / "cache",
        families=families,
    )
    (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    text = format_report(res)
    (out / "report.txt").write_text(text)
    print(text)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--injury-db", default=None)
    ap.add_argument("--out-dir", default="reports/tracking_screen")
    ap.add_argument("--no-hustle", action="store_true", help="run the pre-declared T-only fallback")
    ap.add_argument(
        "--families", default=None, help="comma list, e.g. passing,shooting,rebounding_T"
    )
    a = ap.parse_args(argv)
    fams = tuple(a.families.split(",")) if a.families else None
    run(a.db_path, a.injury_db, out_dir=a.out_dir, with_hustle=not a.no_hustle, families=fams)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
