"""F9 young top-10 pick prior: pre-registered data checks (docs/prereg/F9_YOUNG_PICK_PRIOR.md).

    .venv/bin/python -m nba.eval.f9_young_pick checks

Runs minimum data checks 1 and 2 (and the missingness audit) on the real DB and writes
``reports/prereg_f9/results.json``. Per the pre-registration a failed check stops the study:
no arm is fitted and no model result is produced. Read-only on the DuckDB (opened briefly),
refuses season 2025.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import MAX_SEASON, load_inputs
from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
)

DOC = Path("docs/prereg/F9_YOUNG_PICK_PRIOR.md")
OUT_DIR = Path("reports/prereg_f9")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
MIN_PICK_COVERAGE = 0.90
MAX_MISSING_GAP_PP = 2.0
MIN_SLICE_ROWS = 300
SLICE_MIN10 = 20.0
SLICE_MAX_SEASONS = 2
TOP_PICK = 10


def frozen_sha256(doc: Path = DOC) -> str:
    """sha256 of the doc text above the ``=== RESULTS BELOW ===`` line (the freeze evidence)."""
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def pick_coverage(db_path: str) -> dict[str, Any]:
    """Check 1a: draft_pick non-null share among players with >= 20 played games in 2022-2024."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT s.player_id, COUNT(*) AS n, SUM(s.minutes) AS mins, "
            "ANY_VALUE(p.draft_pick) AS draft_pick, ANY_VALUE(p.draft_year) AS draft_year "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            "LEFT JOIN players_static p USING (player_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0 "
            "GROUP BY s.player_id HAVING COUNT(*) >= 20"
        ).pl()
    finally:
        con.close()
    miss = df.filter(pl.col("draft_pick").is_null()).sort("mins", descending=True)
    return {
        "n_players": df.height,
        "n_with_pick": int(df["draft_pick"].is_not_null().sum()),
        "share": float(df["draft_pick"].is_not_null().mean()),  # type: ignore[arg-type]
        "minutes_share_missing": float(miss["mins"].sum()) / float(df["mins"].sum()),
        "n_missing_with_draft_year": int(miss["draft_year"].is_not_null().sum()),
        "top_unmatched_by_minutes": miss.head(15).select("player_id", "n", "mins").to_dicts(),
    }


def study_rows(feats: pl.DataFrame, seasons: tuple[int, ...]) -> pl.DataFrame:
    """Rows of the study: played, ``n_prior >= 5``, test seasons (production row filter)."""
    return feats.filter(
        (pl.col("n_prior") >= MIN_PRIOR_PLAYED) & pl.col("season").is_in(list(seasons))
    )


def slice_s(df: pl.DataFrame) -> pl.Series:
    """Slice S (reporting only): yp_seasons <= 2, yp_pick <= 10, as-of min10 >= 20."""
    return (
        (df["yp_seasons"] <= SLICE_MAX_SEASONS)
        & (df["yp_pick"] <= TOP_PICK)
        & (df["min10"] >= SLICE_MIN10)
    ).fill_null(False)


def slice_counts(rows: pl.DataFrame) -> dict[str, Any]:
    """Check 2 plus a descriptive composition audit (draft_year based, NOT part of the rule)."""
    out: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        d = rows.filter(pl.col("season") == season)
        m = slice_s(d)
        s = d.filter(m)
        young = d.filter(pl.col("yp_seasons") <= SLICE_MAX_SEASONS)
        out[str(season)] = {
            "rows": d.height,
            "slice_rows": s.height,
            "slice_games": int(s["game_id"].n_unique()),
            "slice_players": int(s["player_id"].n_unique()),
            "other_young_rows": young.height - s.height,
            "veteran_rows_seasons_ge5": int((d["yp_seasons"] >= 5).sum()),
            "yp_seasons_counts": {
                str(int(k)): int(v)
                for k, v in d.group_by("yp_seasons").len().iter_rows()
                if k is not None
            },
        }
        if "draft_year" in d.columns:
            # descriptive: how many slice rows are genuinely <= 2 NBA seasons old
            true_young = (season - s["draft_year"]) <= 1
            out[str(season)]["slice_rows_draft_year_le_season_minus1"] = int(true_young.sum())
            out[str(season)]["slice_rows_draft_year_null"] = int(s["draft_year"].is_null().sum())
    return out


def missingness_audit(rows: pl.DataFrame) -> dict[str, Any]:
    """Check 1b: NaN rate of yp_pick vs realised minutes bucket and vs yp_seasons."""
    nan = rows["yp_pick"].is_null() | rows["yp_pick"].is_nan()
    short = rows["minutes"] < 10
    by_min = {
        "played<10": float(nan.filter(short).mean()),  # type: ignore[arg-type]
        "rest": float(nan.filter(~short).mean()),  # type: ignore[arg-type]
    }
    by_seasons = {
        str(int(k)): float(v)
        for k, v in rows.with_columns(nan.cast(pl.Float64).alias("_nan"))
        .group_by("yp_seasons")
        .agg(pl.col("_nan").mean())
        .iter_rows()
        if k is not None
    }
    gap = abs(by_min["played<10"] - by_min["rest"]) * 100.0
    return {"nan_rate_by_minutes": by_min, "gap_pp": gap, "nan_rate_by_yp_seasons": by_seasons}


def run_checks(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    from nba.eval.lower_tail_eval import attach_realised

    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    cov = pick_coverage(db_path)
    games, pgs, static, avail = load_inputs(db_path, None)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    con = duckdb.connect(db_path, read_only=True)
    try:
        st2 = con.execute(
            "SELECT player_id, position, draft_pick, draft_year FROM players_static"
        ).pl()
    finally:
        con.close()
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, st2, flagged, elo, young_pick=True)
    feats = attach_realised(feats, games, pgs).join(
        st2.select("player_id", "draft_year"), on="player_id", how="left"
    )
    rows = study_rows(feats, (SELECT_SEASON, REPORT_SEASON))
    counts = slice_counts(rows)
    miss = missingness_audit(rows)
    checks = [
        {
            "check": "1a draft_pick coverage (players >=20 games)",
            "value": cov["share"],
            "threshold": f">= {MIN_PICK_COVERAGE}",
            "ok": bool(cov["share"] >= MIN_PICK_COVERAGE),
        },
        {
            "check": "1b missingness gap, played<10 vs rest (pp)",
            "value": miss["gap_pp"],
            "threshold": f"<= {MAX_MISSING_GAP_PP}",
            "ok": bool(miss["gap_pp"] <= MAX_MISSING_GAP_PP),
        },
    ]
    for season in (SELECT_SEASON, REPORT_SEASON):
        n = counts[str(season)]["slice_rows"]
        checks.append(
            {
                "check": f"2 slice S rows {season}",
                "value": n,
                "threshold": f">= {MIN_SLICE_ROWS}",
                "ok": bool(n >= MIN_SLICE_ROWS),
            }
        )
    sha = frozen_sha256()
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover
        git_sha = "unknown"
    res = {
        "frozen_sha256": sha,
        "git_sha": git_sha,
        "seeds": {"model": cfg.seed, "bootstrap": 0},
        "coverage": cov,
        "slice_counts": counts,
        "missingness": miss,
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "checks_3_4": "3: tests/props/test_young_pick.py (planted future/same-game); "
        "4: flag-off identity tested there; A0 reproduction NOT run (study stopped).",
        "n_rows_study": int(rows.height),
        "cpu_seed_note": float(np.float64(0.0)),
    }
    res.pop("cpu_seed_note")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("checks",))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    res = run_checks(a.db_path)
    print(json.dumps({k: res[k] for k in ("frozen_sha256", "checks", "all_ok")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
