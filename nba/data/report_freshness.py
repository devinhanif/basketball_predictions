"""Age of the injury-report snapshot used per game under the training rule (N12), counts only.

Age = (real tip - 60 min) - as_of of the latest usable snapshot, in hours, per game that has a
usable snapshot. Uses the same ``usable_report_rows`` / ``latest_pretip_flagged`` as training
(real tips), read-only on ``nba.duckdb``; no outcomes are read. Also reports snapshot cadence:
distinct snapshot times per game date and their minute-of-hour mix.

    uv run python -m nba.data.report_freshness
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import duckdb
import polars as pl

from nba.features.game_tipoff import attach_real_tips
from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
)

_Q = (0.05, 0.25, 0.5, 0.75, 0.95)


def _qs(v: list[float]) -> dict[str, float]:
    s = pl.Series(v)
    out = {f"p{int(q * 100):02d}": round(float(s.quantile(q)), 3) for q in _Q}  # type: ignore[arg-type]
    out["mean"] = round(float(s.mean()), 3)  # type: ignore[arg-type]
    out["share_gt_3h"] = round(float((s > 3).mean()), 4)  # type: ignore[arg-type]
    out["share_gt_6h"] = round(float((s > 6).mean()), 4)  # type: ignore[arg-type]
    return out


def freshness(con: duckdb.DuckDBPyConnection, lead_minutes: int = 60) -> dict[str, Any]:
    cfg = ReportTriggerConfig(tip_source="real", lead_minutes=lead_minutes)
    rows = attach_real_tips(load_report_rows(con, cfg))
    _, used = latest_pretip_flagged(rows, cfg)
    tips = rows.select(["game_id", "game_date", "tip_et"]).unique("game_id")
    season = dict(con.execute("SELECT game_id, season FROM games").fetchall())
    ages: dict[int, list[float]] = {}
    for gid, asof in used.items():
        tip = tips.filter(pl.col("game_id") == gid)["tip_et"][0]
        if tip is None:
            continue
        ages.setdefault(int(season[gid]), []).append(
            (tip - asof).total_seconds() / 3600.0 - lead_minutes / 60.0
        )
    out: dict[str, Any] = {"age_hours_at_tip_minus_lead": {}, "cadence": {}}
    for s, v in sorted(ages.items()):
        out["age_hours_at_tip_minus_lead"][str(s)] = {"n_games": len(v), **_qs(v)}
    snaps = rows.select(["game_date", "as_of"]).unique()
    for s in sorted(ages):
        dates = {
            str(d)
            for (d,) in con.execute(
                "SELECT DISTINCT game_date FROM games WHERE season = ?", [s]
            ).fetchall()
        }
        sub = snaps.filter(pl.col("game_date").cast(pl.Utf8).is_in(list(dates)))
        per_day = sub.group_by("game_date").len()["len"].to_list()
        out["cadence"][str(s)] = {
            "game_dates_with_snapshots": len(per_day),
            "snapshots_per_game_date_mean": round(sum(per_day) / max(len(per_day), 1), 2),
            "minute_of_hour": dict(
                Counter(int(x.minute) for x in sub["as_of"].to_list()).most_common(6)
            ),
        }
    return out


def main() -> int:
    con = duckdb.connect("nba.duckdb", read_only=True)
    try:
        print(json.dumps(freshness(con), indent=1))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
