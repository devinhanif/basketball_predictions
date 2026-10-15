# ruff: noqa: E501
"""INJURY_TIMING minimum-data gate and data-capture audit (rule: docs/prereg/INJURY_TIMING.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.injury_timing_gate --data-root /Users/devin/Downloads/nba-prediction \
        [--repaired <path to a duckdb holding the repaired player_availability table>]

Counts and lead times only: no outcome (minutes, points) is read by this file. ``--data-root`` holds ``nba.duckdb`` and ``data/schedule``;
both are opened read-only with ``season <= 2024`` in SQL and ``max_season=2024`` on the schedule. Without ``--repaired`` the gate
measures the STORED table (the 11:00 / 17:00 ET pair) and reports ``gate_pass`` false when the stored table is too stale to test
anything. With ``--repaired`` it measures the repaired table (the intraday sequence) and the stored-vs-repaired OUT-set differences.
Output: ``reports/prereg_injury_timing/gate.json`` (or ``--out``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import ReportTriggerConfig, latest_pretip_flagged

SEASONS = (2023, 2024)
MAX_SEASON = 2024
LEAD_MINUTES = 60
#: gate thresholds, frozen with the rule
FRESH_LEAD = 90  # a usable snapshot with lead <= 90 minutes counts as "near T-60"
MIN_FRESH_SHARE = 0.80  # share of games whose used snapshot is near T-60
MIN_GAMES = 1_200  # games with a usable snapshot, per season
MIN_DIFF_GAMES = (
    300  # games whose OUT set differs between the stored and the repaired used snapshot, per season
)
CFG = ReportTriggerConfig(statuses=("out",), lead_minutes=LEAD_MINUTES, tip_source="real")


def rows_from_db(db: Path, tips: pl.DataFrame, seasons: tuple[int, ...] = SEASONS) -> pl.DataFrame:
    """Official-report rows of games in ``seasons`` (<= 2024) with the real tip attached (inner join: no proxy)."""
    assert max(seasons) <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        q = (
            "SELECT a.game_id, a.player_id, lower(a.status) AS status, CAST(a.as_of AS TIMESTAMP) AS as_of, "
            "g.game_date, g.season FROM player_availability a JOIN games g ON g.game_id = a.game_id "
            "WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL AND g.season IN ("
            + ", ".join(str(int(s)) for s in seasons)
            + ")"
        )
        rows = con.execute(q).fetchall()
    finally:
        con.close()
    df = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
            "season": pl.Int64,
        },
        orient="row",
    )
    df = df.join(tips.select(["game_id", "tip_et"]), on="game_id", how="inner")
    return df


def season_games(db: Path, seasons: tuple[int, ...] = SEASONS) -> dict[int, int]:
    assert max(seasons) <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT season, count(*) FROM games WHERE season IN ("
            + ", ".join(str(int(s)) for s in seasons)
            + ") GROUP BY 1"
        ).fetchall()
    finally:
        con.close()
    return {int(s): int(n) for s, n in rows}


def leads(rows: pl.DataFrame) -> tuple[dict[str, int], dict[str, set[int]]]:
    """Lead (minutes, tip minus the used snapshot) per game and the used snapshot's OUT set, by the production rule."""
    flagged, used = latest_pretip_flagged(rows, CFG)
    tip = {g: t for g, t in rows.group_by("game_id").agg(pl.col("tip_et").first()).iter_rows()}
    lead = {g: int((tip[g] - t).total_seconds() // 60) for g, t in used.items()}
    return lead, flagged


def season_block(
    rows: pl.DataFrame, n_games: int, other: pl.DataFrame | None = None
) -> dict[str, Any]:
    lead, flagged = leads(rows)
    arr = np.array(list(lead.values()), dtype=float)
    out: dict[str, Any] = {
        "games_total": n_games,
        "games_with_usable_snapshot": len(lead),
        "lead_min_p10_p50_p90": [float(np.percentile(arr, q)) for q in (10, 50, 90)]
        if len(arr)
        else None,
        "fresh_share_lead_le_90": float((arr <= FRESH_LEAD).mean()) if len(arr) else 0.0,
        "distinct_stamps_per_game_mean": float(
            rows.group_by("game_id")
            .agg(pl.col("as_of").n_unique().alias("n"))["n"]
            .cast(pl.Float64)
            .mean()  # type: ignore[arg-type]
            or 0.0
        ),
    }
    if other is not None:
        lead_o, flagged_o = leads(other)
        both = sorted(set(flagged) & set(flagged_o))
        diff = [g for g in both if flagged[g] != flagged_o[g]]
        out["vs_stored"] = {
            "games_in_both": len(both),
            "games_out_set_differs": len(diff),
            "median_lead_gain_min": float(np.median([lead[g] - lead_o[g] for g in both]))
            if both
            else None,
        }
    return out


def gate_verdict(blocks: dict[str, dict[str, Any]], repaired: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for s, b in blocks.items():
        checks.append(
            {
                "check": f"{s}: games with usable snapshot >= {MIN_GAMES}",
                "value": b["games_with_usable_snapshot"],
                "pass": b["games_with_usable_snapshot"] >= MIN_GAMES,
            }
        )
        checks.append(
            {
                "check": f"{s}: share of games with used lead <= {FRESH_LEAD} min >= {MIN_FRESH_SHARE}",
                "value": b["fresh_share_lead_le_90"],
                "pass": b["fresh_share_lead_le_90"] >= MIN_FRESH_SHARE,
            }
        )
        if repaired:
            n = b["vs_stored"]["games_out_set_differs"]
            checks.append(
                {
                    "check": f"{s}: games whose OUT set differs from stored >= {MIN_DIFF_GAMES}",
                    "value": n,
                    "pass": n >= MIN_DIFF_GAMES,
                }
            )
    return {"checks": checks, "gate_pass": bool(checks) and all(c["pass"] for c in checks)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--repaired", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    root = a.data_root
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    tips = build_game_tipoff(root / "data/schedule", max_season=MAX_SEASON)
    db = root / "nba.duckdb"
    counts = season_games(db)
    stored = rows_from_db(db, tips)
    repaired = rows_from_db(a.repaired, tips) if a.repaired else None
    blocks: dict[str, dict[str, Any]] = {}
    for s in SEASONS:
        base = repaired if repaired is not None else stored
        blocks[str(s)] = season_block(
            base.filter(pl.col("season") == s),
            counts.get(s, 0),
            stored.filter(pl.col("season") == s) if repaired is not None else None,
        )
    res = {
        "rule": "docs/prereg/INJURY_TIMING.md",
        "table": "repaired" if repaired is not None else "stored",
        "started": started,
        "ended": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "thresholds": {
            "FRESH_LEAD": FRESH_LEAD,
            "MIN_FRESH_SHARE": MIN_FRESH_SHARE,
            "MIN_GAMES": MIN_GAMES,
            "MIN_DIFF_GAMES": MIN_DIFF_GAMES,
        },
        "seasons": blocks,
        **gate_verdict(blocks, repaired is not None),
    }
    out = a.out or (Path(__file__).resolve().parents[2] / "reports/prereg_injury_timing/gate.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
