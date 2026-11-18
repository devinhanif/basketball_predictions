"""Label-free minimum-data gates for the calibration program drafts (2026-11-17).

Drafts served: docs/prereg/CAL_SERVING_CHECKS.md, CAL_BY_TIME.md, LADDER_CAL.md, PPLAY_CAL_LIVE.md.

Reads a COPY of the app export (``data/export/latest/export.duckdb``), read-only, and the schedule
parquet. It never reads an outcome: the tables ``box_scores`` and ``settled_scores`` and the columns
``home_pts`` / ``away_pts`` are refused by a guard on every SQL string. Power figures use synthetic
labels drawn from the real stated probabilities (as LINE_CAL_LIVE section 5 did). No season-2025 row
exists in the export (manifest ``seasons`` = [2026]); the script asserts it.

Usage (about 20 s, numpy + duckdb, no lock):
  cp data/export/latest/export.duckdb <scratch>/export.duckdb
  python -I reports/prereg_calibration_program/gate.py --export <scratch>/export.duckdb \
      --schedule data/schedule/live_tips_2026-27.parquet --out <scratch>/gate.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

FORBIDDEN = ("box_scores", "settled_scores", "home_pts", "away_pts")
# zero sportsbook lines (odds key outage); the export shows 0 lines on these slates
OUTAGE = (date(2026, 11, 14), date(2026, 11, 15))
LCL_START = date(2026, 10, 30)  # LINE_CAL_LIVE window opens with this slate
LCL_COV = 0.838  # LINE_CAL_LIVE gate: share of scored games with >= 1 Pinnacle t60 leg
SEASON_END = date(2027, 4, 11)


def q(c: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    low = sql.lower()
    for word in FORBIDDEN:
        if word in low:
            raise ValueError(f"label guard: '{word}' may not be read by a gate")
    return c.execute(sql).fetchall()


PROD = "model_name = 'props_context_residual' AND is_primary"
LATEST_PP = """(SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY game_id, player_id
  ORDER BY made_at DESC) rn FROM p_play WHERE lead_minutes >= 60) WHERE rn = 1)"""


def coverage(c: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Per slate: production props, served line_cal, and DK main x.5 lines under the extended
    (lines_through) pairing of the same run, with the paired line's lead."""
    rows = q(
        c,
        f"""
WITH p AS (SELECT game_id, player_id, stat, coalesce(lines_run_id, run_id) rid,
             line_cal IS NOT NULL hc, made_at, tipoff, CAST(tipoff - INTERVAL 5 HOUR AS DATE) d
           FROM prop_grids WHERE {PROD}),
dk AS (SELECT run_id rid, game_id, player_id, stat, line, max(snapshot_ts) ts FROM prop_lines
       WHERE book = 'draftkings' AND NOT is_alternate AND NOT unusable AND line % 1 = 0.5
       GROUP BY ALL HAVING count(DISTINCT side) = 2),
dkn AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY rid, game_id, player_id, stat
        ORDER BY ts DESC) rn FROM dk) WHERE rn = 1),
j AS (SELECT p.*, dkn.ts, epoch(p.tipoff - dkn.ts) / 60 line_lead FROM p
      LEFT JOIN dkn USING (rid, game_id, player_id, stat))
SELECT d, count(*), sum(hc::INT), sum((ts IS NOT NULL)::INT),
       median(line_lead) FILTER (WHERE ts IS NOT NULL), count(DISTINCT game_id)
FROM j GROUP BY d ORDER BY d""",
    )
    return [
        {
            "slate": str(r[0]),
            "games": int(r[5]),
            "props": int(r[1]),
            "line_cal_served": int(r[2]),
            "dk_main_x5_extended": int(r[3]),
            "median_line_lead_min_extended": None if r[4] is None else round(float(r[4]), 1),
        }
        for r in rows
    ]


def served_line_leads(c: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Lead before tip of the DK line behind each served line_cal value; served bench share."""
    rows = q(
        c,
        f"SELECT line_cal, tipoff, prediction_json FROM prop_grids "
        f"WHERE {PROD} AND line_cal IS NOT NULL",
    )
    leads, bench = [], 0
    for lc, tip, pj in rows:
        at = datetime.fromisoformat(json.loads(lc)["price_at"].rstrip("Z"))
        leads.append((tip - at).total_seconds() / 60)
        sr = json.loads(pj).get("starter_rate")
        bench += int(sr is not None and sr < 0.5)
    a = np.asarray(leads)
    return {
        "n": len(a),
        "lead_min_q05_q25_q50_q75_q95": [round(float(v), 1) for v in np.quantile(a, QS5)],
        "share_lead_ge_120": round(float((a >= 120).mean()), 3),
        "share_lead_45_90": round(float(((a >= 45) & (a < 90)).mean()), 3),
        "served_bench_starter_rate_lt_half": bench,
        "starter10_stored": False,
    }


QS5 = [0.05, 0.25, 0.5, 0.75, 0.95]


def slot_availability(c: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Props with a two-sided x.5 main line by book at each pairing time (all prop-model runs)."""
    rows = q(
        c,
        """
WITH g AS (SELECT game_id, any_value(tipoff) tipoff FROM prop_grids GROUP BY 1),
two AS (SELECT l.run_id, l.game_id, l.player_id, l.stat, l.book,
          epoch(any_value(g.tipoff) - any_value(l.paired_through)) / 60 thr
        FROM prop_lines l JOIN g USING (game_id)
        WHERE NOT l.is_alternate AND NOT l.unusable AND l.line % 1 = 0.5
        GROUP BY l.run_id, l.game_id, l.player_id, l.stat, l.book, l.line
        HAVING count(DISTINCT l.side) = 2),
b AS (SELECT *, CASE WHEN thr >= 240 THEN 'morning_ge240' WHEN thr >= 90 THEN 't90_240'
        WHEN thr >= 45 THEN 't60_45_90' WHEN thr >= 20 THEN 't30_20_45' ELSE 't15_lt20' END slot
      FROM two)
SELECT slot, book, count(DISTINCT (game_id, player_id, stat)), count(DISTINCT game_id) FROM b
WHERE book IN ('draftkings', 'pinnacle') GROUP BY ALL ORDER BY 1, 2""",
    )
    return [
        {"slot": r[0], "book": r[1], "props": int(r[2]), "games": int(r[3]),
         "per_game": round(int(r[2]) / max(int(r[3]), 1), 1)}
        for r in rows
    ]  # fmt: skip


def rungs(c: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Kalshi and DraftKings alternate rungs paired at the newest (< 20 min) pairing time."""
    rows = q(
        c,
        """
WITH g AS (SELECT game_id, any_value(tipoff) tipoff FROM prop_grids GROUP BY 1),
pl AS (SELECT l.* FROM prop_lines l JOIN g USING (game_id)
       WHERE epoch(g.tipoff - l.paired_through) / 60 < 20 AND NOT l.unusable)
SELECT CASE WHEN book = 'kalshi' THEN 'kalshi_rungs' ELSE 'dk_alt_over_rungs' END k, stat,
       count(DISTINCT (game_id, player_id, stat, line)), count(DISTINCT game_id),
       count(DISTINCT (game_id, player_id, stat))
FROM pl WHERE book = 'kalshi' OR (book = 'draftkings' AND is_alternate AND side = 'over')
GROUP BY ALL ORDER BY 1, 2""",
    )
    return [
        {"kind": r[0], "stat": r[1], "rungs": int(r[2]), "games": int(r[3]), "legs": int(r[4]),
         "rungs_per_game": round(int(r[2]) / max(int(r[3]), 1), 1)}
        for r in rows
    ]  # fmt: skip


def schedule(path: str) -> dict[date, int]:
    s = duckdb.connect()
    rows = s.execute(
        "SELECT CAST(CAST(gameDateTimeEst AS TIMESTAMP) AS DATE), count(*) FROM read_parquet(?) "
        "WHERE gameId LIKE '002%' GROUP BY 1",
        [path],
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def reach(
    days: dict[date, int], start: date, need: float, cov: float, skip: tuple[date, ...] = ()
) -> str:
    t = 0.0
    for d in sorted(days):
        if d < start or d in skip:
            continue
        t += days[d] * cov
        if t >= need:
            return str(d)
    return "not_reached"


def games_between(days: dict[date, int], start: date, end: date) -> int:
    return sum(n for d, n in days.items() if start <= d <= end)


def sim_gap(
    rng: np.random.Generator, p0: np.ndarray, G: int, L: int, shift: float, band: float, k: int
) -> dict[str, float]:
    """P(game-clustered 95% CI of mean(y - p) lies inside +-band), synthetic y ~ Bern(p + shift)."""
    ok, hw = 0, []
    for _ in range(k):
        p = rng.choice(p0, size=(G, L))
        y = rng.random((G, L)) < np.clip(p + shift, 0.0, 1.0)
        g = (y - p).mean(axis=1)
        se = g.std(ddof=1) / np.sqrt(G)
        hw.append(1.96 * se)
        ok += int(g.mean() - 1.96 * se > -band and g.mean() + 1.96 * se < band)
    return {"p_pass": round(ok / k, 3), "half_width": round(float(np.mean(hw)), 4)}


PP_BINS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.97), (0.97, 1.0001))


def sim_pplay(
    rng: np.random.Generator, p0: np.ndarray, G: int, shift: float, band: float, k: int
) -> dict[str, Any]:
    """P(every bin's game-clustered ratio-estimator CI lies inside +-band), 25 rows per game."""
    ok = 0
    hw: dict[str, list[float]] = {f"{a}-{min(b, 1.0)}": [] for a, b in PP_BINS}
    for _ in range(k):
        p = rng.choice(p0, size=(G, 25))
        y = rng.random((G, 25)) < np.clip(p + shift, 0.0, 1.0)
        good = True
        for a, b in PP_BINS:
            m_ = (p >= a) & (p < b)
            num, den = ((y - p) * m_).sum(1), m_.sum(1)
            keep = den > 0
            m = num[keep].sum() / den[keep].sum()
            r = num[keep] - m * den[keep]
            n_c = int(keep.sum())
            se = float(np.sqrt((r**2).sum()) / den[keep].sum() * np.sqrt(n_c / (n_c - 1)))
            hw[f"{a}-{min(b, 1.0)}"].append(1.96 * se)
            good &= bool(m - 1.96 * se > -band and m + 1.96 * se < band)
        ok += int(good)
    return {"p_pass": round(ok / k, 3), "half_width": {kk: round(float(np.mean(v)), 4)
                                                      for kk, v in hw.items()}}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--draws", type=int, default=400)
    a = ap.parse_args()
    t0 = time.monotonic()
    if "/nba-prediction/data/" in str(Path(a.export).resolve()):
        raise SystemExit("refusing: copy the export to a scratch directory first")
    c = duckdb.connect(a.export, read_only=True)
    meta = dict(q(c, "SELECT key, value FROM meta"))
    if meta.get("seasons") != "2026":
        raise SystemExit(f"refusing: export seasons {meta.get('seasons')!r}, expected 2026 only")
    rng = np.random.default_rng(0)
    days = schedule(a.schedule)
    out: dict[str, Any] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "export": {"path": a.export, "data_version": meta.get("data_version"),
                   "export_date": meta.get("export_date"), "made_at": meta.get("made_at")},
        "label_free": True,
        "coverage_by_slate": coverage(c),
        "served_line_leads": served_line_leads(c),
        "slot_availability": slot_availability(c),
        "rungs": rungs(c),
    }  # fmt: skip
    # schedule projections
    lcl = {
        "no_outage": reach(days, LCL_START, 200, LCL_COV),
        "11-14_15_lost": reach(days, LCL_START, 200, LCL_COV, OUTAGE),
        "11-14_16_lost": reach(days, LCL_START, 200, LCL_COV, (*OUTAGE, date(2026, 11, 16))),
    }
    starts = {"freeze_2026-11-25": date(2026, 11, 25), "after_D_2026-12-04": date(2026, 12, 4)}
    out["schedule"] = {
        "regular_season_games": sum(days.values()),
        "lcl_look_projection_200_games": lcl,
        "games_to_season_end": {k: games_between(days, v, SEASON_END) for k, v in starts.items()},
        "cal_by_time_200_games_from_2026-12-04": {
            slot: reach(days, date(2026, 12, 4), 200, cov)
            for slot, cov in (("pinnacle_t60_cov.838", 0.838), ("dk_t60_cov.64", 0.64),
                              ("dk_t15_cov.95", 0.95), ("dk_morning_cov.51", 0.51))
        },
        "pplay_600_games_from_2026-11-25": reach(days, date(2026, 11, 25), 600, 1.0),
    }  # fmt: skip
    cal = np.asarray(
        [
            json.loads(r[0])["p_over_cal"]
            for r in q(
                c,
                f"SELECT line_cal FROM prop_grids WHERE {PROD} AND line_cal IS NOT NULL "
                "ORDER BY game_id, player_id, stat",
            )
        ]
    )
    qs = [round(float(v), 4) for v in np.quantile(cal, [0.05, 0.95])]
    out["stated_cal"] = {"n": int(cal.size), "mean": round(float(cal.mean()), 4), "q05_q95": qs}
    k = a.draws
    out["power_cal_by_time"] = {
        f"L{L}_G{G}": {
            "gap0": sim_gap(rng, cal, G, L, 0.0, 0.03, k),
            "gap.015": sim_gap(rng, cal, G, L, 0.015, 0.03, k),
            "gap.04_false_pass": sim_gap(rng, cal, G, L, 0.04, 0.03, k),
        }
        for L in (20, 31, 51)
        for G in (100, 150, 200)
    }
    pp = np.asarray(
        [
            r[0]
            for r in q(
                c,
                f"SELECT p_play_primary FROM {LATEST_PP} WHERE p_play_primary IS NOT NULL "
                "ORDER BY game_id, player_id",
            )
        ]
    )
    out["pplay_stated"] = {
        "rows": int(pp.size),
        "share_by_bin": {f"{lo}-{min(hi, 1.0)}": round(float(((pp >= lo) & (pp < hi)).mean()), 3)
                         for lo, hi in PP_BINS},
        "source": dict(q(c, f"SELECT p_play_source, count(*) FROM {LATEST_PP} GROUP BY 1")),
    }  # fmt: skip
    out["power_pplay"] = {
        f"G{G}": {"calibrated": sim_pplay(rng, pp, G, 0.0, 0.05, k // 2),
                  "shift.025": sim_pplay(rng, pp, G, 0.025, 0.05, k // 2),
                  "shift.08_false_pass": sim_pplay(rng, pp, G, 0.08, 0.05, k // 2)}
        for G in (300, 600, 880)
    }  # fmt: skip
    wp = np.asarray(
        [
            r[0]
            for r in q(
                c,
                "SELECT p_home FROM (SELECT *, row_number() OVER (PARTITION BY game_id "
                "ORDER BY made_at DESC) rn FROM win_probs WHERE is_primary AND lead_minutes >= 60) "
                "WHERE rn = 1 ORDER BY game_id",
            )
        ]
    )
    out["power_win_equivalence_pm.03"] = {
        f"G{G}": sim_gap(rng, wp, G, 1, 0.0, 0.03, k) for G in (500, 900, 1100)
    }
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out["schedule"], indent=1, default=str))
    print("wrote", a.out, "seconds", round(time.monotonic() - t0, 1))


if __name__ == "__main__":
    main()
