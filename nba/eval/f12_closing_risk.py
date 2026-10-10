"""F12 closing-lineup risk: pre-registered minimum data checks (docs/prereg/F12_CLOSING_RISK.md).

    .venv/bin/python -m nba.eval.f12_closing_risk checks

Runs minimum data checks 1 (stints coverage and box-minute reconciliation) and 2 (premise
replication: Q4/Q1-3 floor-time ratio by seasons-played x closeness) on seasons <= 2024 and writes
``reports/prereg_f12/results.json``. Per the pre-registration a failed check stops the study: no
``cl_*`` column is built and no arm is fitted. Read-only on the DuckDB, refuses season 2025.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

DOC = Path("docs/prereg/F12_CLOSING_RISK.md")
OUT_DIR = Path("reports/prereg_f12")
MAX_SEASON = 2024
MIN_COVERAGE = 0.95
RECON_TOL_MIN = 1.0
RECON_SHARE = 0.95
VET_MIN_IDX, YOUNG_MAX_IDX = 5, 2
CLOSE_MAX, BLOW_MIN = 5, 15
VET_GAP_MIN = 0.04
YOUNG_BAND = 0.04


def frozen_sha256(doc: Path = DOC) -> str:
    """sha256 of the doc text above the ``=== RESULTS BELOW ===`` line (the freeze evidence)."""
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def q4_ratio(df: pl.DataFrame) -> float:
    """Pooled Q4 floor minutes over Q1-Q3 floor minutes (baseline 12/36 = 0.333)."""
    return float(df["q4"].sum()) / float(df["q13"].sum())  # type: ignore[arg-type]


def load_player_games(db_path: str) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Per player-game Q4 and Q1-Q3 floor minutes from stints (OT excluded), box minutes, margin."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        cov = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN s.game_id IS NOT NULL THEN 1 ELSE 0 END) FROM games g "
            "LEFT JOIN (SELECT DISTINCT game_id FROM stints) s USING (game_id) WHERE g.season <= ?",
            [MAX_SEASON],
        ).fetchone()
        assert cov is not None
        df = con.execute(
            """
            WITH st AS (SELECT game_id, period, UNNEST(players) AS player_id,
                               start_clock - end_clock AS d FROM stints),
            pm AS (SELECT game_id, player_id,
                          SUM(CASE WHEN period = 4 THEN d ELSE 0 END) / 60.0 AS q4,
                          SUM(CASE WHEN period < 4 THEN d ELSE 0 END) / 60.0 AS q13,
                          SUM(CASE WHEN period > 4 THEN d ELSE 0 END) / 60.0 AS ot
                   FROM st GROUP BY 1, 2)
            SELECT pm.*, b.minutes, g.season, ABS(g.home_pts - g.away_pts) AS margin,
                   p.draft_year
            FROM pm JOIN games g USING (game_id)
            JOIN player_game_stats b ON b.game_id = pm.game_id AND b.player_id = pm.player_id
            LEFT JOIN players_static p ON p.player_id = pm.player_id
            WHERE g.season <= ?
            """,
            [MAX_SEASON],
        ).pl()
    finally:
        con.close()
    return df, {"games": int(cov[0]), "games_with_stints": int(cov[1])}


def run_checks(db_path: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    df, cov = load_player_games(db_path)
    df = df.with_columns(
        (pl.col("season") - pl.col("draft_year") + 1).alias("idx_draft"),
        (pl.col("q4") + pl.col("q13") + pl.col("ot") - pl.col("minutes")).alias("recon"),
    )
    # distinct prior seasons + 1 inside the DB (frozen definition): truncated at 3 by the 2022 start
    max_idx_db = int(df["season"].n_unique())
    recon_ok = float((df["recon"].abs() <= RECON_TOL_MIN).mean())  # type: ignore[arg-type]
    rep: list[dict[str, Any]] = []
    ratios: dict[tuple[str, str], float] = {}
    for gname, gexpr in (
        ("vet(idx>=5)", pl.col("idx_draft") >= VET_MIN_IDX),
        ("young(idx<=2)", pl.col("idx_draft") <= YOUNG_MAX_IDX),
    ):
        for cname, cexpr in (
            ("close(<=5)", pl.col("margin") <= CLOSE_MAX),
            ("blowout(>=15)", pl.col("margin") >= BLOW_MIN),
        ):
            sub = df.filter(gexpr & cexpr)
            ratios[(gname, cname)] = q4_ratio(sub)
            rep.append(
                {
                    "group": gname,
                    "closeness": cname,
                    "n": sub.height,
                    "ratio": ratios[(gname, cname)],
                }
            )
    v_c, v_b = ratios[("vet(idx>=5)", "close(<=5)")], ratios[("vet(idx>=5)", "blowout(>=15)")]
    y_c, y_b = ratios[("young(idx<=2)", "close(<=5)")], ratios[("young(idx<=2)", "blowout(>=15)")]
    vet_gap, young_gap = v_c / v_b - 1.0, y_c / y_b - 1.0
    checks = [
        {
            "check": "1a stints game coverage 2022-24",
            "value": cov["games_with_stints"] / cov["games"],
            "threshold": f">= {MIN_COVERAGE}",
            "ok": cov["games_with_stints"] / cov["games"] >= MIN_COVERAGE,
        },
        {
            "check": "1b Q1-Q4+OT stint minutes vs box within 1.0 min (share of player-games)",
            "value": recon_ok,
            "threshold": f">= {RECON_SHARE}",
            "ok": recon_ok >= RECON_SHARE,
        },
        {
            "check": "2a vets close vs blowout ratio gap",
            "value": vet_gap,
            "threshold": f">= +{VET_GAP_MIN}",
            "ok": vet_gap >= VET_GAP_MIN,
        },
        {
            "check": "2b young close vs blowout ratio gap",
            "value": young_gap,
            "threshold": f"within +/-{YOUNG_BAND}",
            "ok": abs(young_gap) <= YOUNG_BAND,
        },
    ]
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover
        git_sha = "unknown"
    res: dict[str, Any] = {
        "frozen_sha256": frozen_sha256(),
        "git_sha": git_sha,
        "seeds": {"model": 0, "bootstrap": 0},
        "n_player_games": int(df.height),
        "db_seasons_in_scope": max_idx_db,
        "seasons_index_note": "frozen DB-internal index (distinct prior seasons + 1) caps at 3 on "
        "2022-24, so 'veterans >= 5' is empty there; replication uses season - draft_year + 1 "
        "(drafted players only; undrafted excluded).",
        "replication": rep,
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "checks_3_4_5": "not run: study stopped at checks 1-2 (no cl_* built, no arm fitted)",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("checks",))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    res = run_checks(a.db_path)
    print(
        json.dumps(
            {k: res[k] for k in ("frozen_sha256", "replication", "checks", "all_ok")}, indent=1
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
