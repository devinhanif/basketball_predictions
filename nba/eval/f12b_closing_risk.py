"""F12B_CLOSING_RISK: closing-lineup premise, garbage time excluded.

Rule: docs/prereg/F12B_CLOSING_RISK.md.

    .venv/bin/python -m nba.eval.f12b_closing_risk gate --out reports/prereg_f12b/gate.json
    .venv/bin/python -m nba.eval.f12b_closing_risk premise --sha <frozen sha256> \
        --out reports/prereg_f12b/results.json

``gate`` is the minimum-data gate: coverage, possession-state checks, cell counts and the
projected spread of the young gap (from the no-exclusion form already measured by F12_RETEST).
It never computes a ratio with garbage time removed. ``premise`` computes the frozen descriptive
test and refuses to run unless the doc's sha256 above ``=== RESULTS BELOW ===`` equals ``--sha``.
Read-only; seasons 2023-2024 (gate: any seasons <= 2024 for the capture audit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

DOC = Path("docs/prereg/F12B_CLOSING_RISK.md")
SEASONS = (2023, 2024)
MAX_SEASON = 2024
CLOSE_MAX, BLOW_MIN = 5, 15
VET_MIN_IDX, YOUNG_MAX_IDX = 5, 2
BAND = 0.04
MIN_A = 1.0
B_BOOT, SEED = 2000, 0
# garbage tiers: (clock lower bound exclusive in seconds, margin threshold); clock <= 360 -> 10
TIERS = ((540.0, 25), (360.0, 20), (-1.0, 10))

GARBAGE_SQL_T = """
WITH q4 AS (
  SELECT p.game_id, p.poss_idx,
         CASE WHEN p.poss_idx = min(p.poss_idx) OVER (PARTITION BY p.game_id) THEN 720.0
              ELSE p.clock_start END AS clock_start,
         abs(p.score_diff - coalesce(p.pts, 0)) AS m,
         lead(p.clock_start) OVER (PARTITION BY p.game_id ORDER BY p.poss_idx) AS nxt
  FROM possessions p JOIN games g USING (game_id)
  WHERE p.period = 4 AND g.season IN ({seasons}) AND g.season <= 2024
), iv AS (
  SELECT game_id, clock_start AS s, greatest(0.0, least(clock_start, coalesce(nxt, 0.0))) AS e, m,
         CASE WHEN clock_start > 540 THEN m >= 25 WHEN clock_start > 360 THEN m >= 20
              ELSE m >= 10 END AS garbage
  FROM q4
)
SELECT game_id, s, e FROM iv WHERE garbage AND s > e
"""

PLAYER_SQL_T = """
WITH st AS (SELECT game_id, period, start_clock AS s, end_clock AS e, UNNEST(players) AS player_id
            FROM stints),
gb AS ({{garbage}}),
q4ov AS (SELECT st.game_id, st.player_id,
                sum(greatest(0.0, least(st.s, gb.s) - greatest(st.e, gb.e))) / 60.0 AS q4_garbage
         FROM st JOIN gb ON gb.game_id = st.game_id WHERE st.period = 4 GROUP BY 1, 2),
pm AS (SELECT game_id, player_id,
              sum(CASE WHEN period = 4 THEN s - e ELSE 0 END) / 60.0 AS q4,
              sum(CASE WHEN period < 4 THEN s - e ELSE 0 END) / 60.0 AS q13
       FROM st GROUP BY 1, 2)
SELECT pm.game_id, pm.player_id, pm.q4, pm.q13, coalesce(q4ov.q4_garbage, 0.0) AS q4_garbage,
       g.season, abs(g.home_pts - g.away_pts) AS margin, p.draft_year
FROM pm JOIN games g USING (game_id)
LEFT JOIN q4ov ON q4ov.game_id = pm.game_id AND q4ov.player_id = pm.player_id
LEFT JOIN players_static p ON p.player_id = pm.player_id
JOIN player_game_stats b ON b.game_id = pm.game_id AND b.player_id = pm.player_id
WHERE g.season IN ({seasons}) AND g.season <= 2024
"""


COV_SQL = """
SELECT g.season, count(*), count(s.game_id) FROM games g
LEFT JOIN (SELECT DISTINCT game_id FROM stints) s USING (game_id)
WHERE g.season IN ({lst}) GROUP BY 1
"""
Q4_SQL = """
WITH f AS (
  SELECT p.game_id, max(CASE WHEN p.period = 4 THEN p.clock_start END) AS first_start,
         bool_and(p.period <> 4 OR p.score_diff IS NOT NULL) AS sd_ok,
         arg_max(abs(p.score_diff), p.poss_idx) AS last_m
  FROM possessions p GROUP BY 1)
SELECT g.season, count(*),
       avg(CASE WHEN f.first_start >= 700 AND f.sd_ok THEN 1.0 ELSE 0 END),
       avg(CASE WHEN abs(f.last_m - abs(g.home_pts - g.away_pts)) <= 3 THEN 1.0 ELSE 0 END),
       avg(CASE WHEN f.first_start >= 680 AND f.sd_ok THEN 1.0 ELSE 0 END)
FROM games g LEFT JOIN f USING (game_id) WHERE g.season IN ({lst}) GROUP BY 1
"""
RECON_SQL = """
WITH st AS (SELECT game_id, UNNEST(players) AS player_id, start_clock - end_clock AS d FROM stints),
pm AS (SELECT game_id, player_id, sum(d) / 60.0 AS m FROM st GROUP BY 1, 2)
SELECT g.season, count(*), avg(CASE WHEN abs(pm.m - b.minutes) <= 1.0 THEN 1.0 ELSE 0 END)
FROM pm JOIN games g USING (game_id)
JOIN player_game_stats b ON b.game_id = pm.game_id AND b.player_id = pm.player_id
WHERE g.season IN ({lst}) GROUP BY 1
"""


def _sql(seasons: tuple[int, ...]) -> tuple[str, str]:
    assert seasons and max(seasons) <= MAX_SEASON
    lst = ", ".join(str(int(x)) for x in seasons)
    garbage = GARBAGE_SQL_T.format(seasons=lst)
    player = PLAYER_SQL_T.format(seasons=lst).format(garbage=garbage)
    return garbage, player


def frozen_sha256(doc: Path = DOC) -> str:
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def is_garbage(clock_start: float, margin: int) -> bool:
    """The frozen tiers: >= 25 above 9:00 left, >= 20 from 9:00 to 6:00, >= 10 from 6:00 on."""
    for lo, thr in TIERS:
        if clock_start > lo:
            return margin >= thr
    return margin >= TIERS[-1][1]


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load(db: str, seasons: tuple[int, ...] = SEASONS) -> tuple[pl.DataFrame, pl.DataFrame]:
    garbage, player = _sql(seasons)
    lst = ", ".join(str(int(x)) for x in seasons)
    con = duckdb.connect(db, read_only=True)
    try:
        df = con.execute(player).pl()
        gm = con.execute(
            f"WITH gb AS ({garbage}) SELECT g.game_id, g.season, abs(g.home_pts - g.away_pts) AS "
            "margin, coalesce(sum(gb.s - gb.e), 0) / 60.0 AS garbage_min FROM games g LEFT JOIN gb "
            f"USING (game_id) WHERE g.season IN ({lst}) AND g.season <= 2024 GROUP BY 1, 2, 3"
        ).pl()
    finally:
        con.close()
    assert int(df["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    df = df.join(gm.select(["game_id", "garbage_min"]), on="game_id", how="left").with_columns(
        (pl.col("season") - pl.col("draft_year") + 1).alias("idx"),
        (12.0 - pl.col("garbage_min").fill_null(0.0)).alias("a_g"),
        (pl.col("q4") - pl.col("q4_garbage")).alias("q4ng"),
    )
    return df, gm


def cells(df: pl.DataFrame) -> dict[tuple[str, str], pl.DataFrame]:
    grp = {"young": pl.col("idx") <= YOUNG_MAX_IDX, "vet": pl.col("idx") >= VET_MIN_IDX}
    cl = {"close": pl.col("margin") <= CLOSE_MAX, "blow": pl.col("margin") >= BLOW_MIN}
    return {(g, c): df.filter(ge & ce) for g, ge in grp.items() for c, ce in cl.items()}


def ratio_ng(sub: pl.DataFrame) -> float:
    """(sum q4ng / sum A_g) / (sum q13 / (36 N)); rows with A_g < 1 dropped by the caller."""
    n = sub.height
    q4ng, a_g, q13 = (float(sub[c].sum()) for c in ("q4ng", "a_g", "q13"))
    return (q4ng / a_g) / (q13 / (36.0 * n))


def _per_game(sub: pl.DataFrame, num: str, den: str) -> pl.DataFrame:
    return sub.group_by("game_id").agg(
        pl.col(num).sum().alias("num"), pl.col(den).sum().alias("den")
    )


def boot_gap(
    close: pl.DataFrame, blow: pl.DataFrame, games: list[str], num: str, den: str
) -> np.ndarray:
    """Game-clustered bootstrap of (sum num / sum den)_close / (..)_blow - 1 over ``games``."""
    idx = {g: i for i, g in enumerate(games)}
    rng = np.random.default_rng(SEED)
    out = np.empty(B_BOOT)
    mats = []
    for sub in (close, blow):
        pg = _per_game(sub, num, den)
        v = np.zeros((len(games), 2))
        for g, a, b in pg.iter_rows():
            v[idx[g]] = (a, b)
        mats.append(v)
    for b in range(B_BOOT):
        w = np.bincount(rng.integers(0, len(games), len(games)), minlength=len(games))
        rc = (w @ mats[0][:, 0]) / (w @ mats[0][:, 1])
        rb = (w @ mats[1][:, 0]) / (w @ mats[1][:, 1])
        out[b] = rc / rb - 1.0
    return out


def run_gate(db: str, seasons: tuple[int, ...] = SEASONS) -> dict[str, Any]:
    t0 = time.time()
    lst = ", ".join(str(int(x)) for x in seasons)
    df, gm = load(db, seasons)
    _log(f"loaded {df.height} player-games, {gm.height} games", t0)
    con = duckdb.connect(db, read_only=True)
    try:
        cov = con.execute(COV_SQL.format(lst=lst)).fetchall()
        q4 = con.execute(Q4_SQL.format(lst=lst)).fetchall()
        recon = con.execute(RECON_SQL.format(lst=lst)).fetchall()
    finally:
        con.close()
    res: dict[str, Any] = {"rule": "F12B_CLOSING_RISK (DRAFT)", "seasons": {}, "checks": []}
    for s in seasons:
        d = df.filter(pl.col("season") == s)
        g_s = gm.filter(pl.col("season") == s)
        games = sorted(g_s["game_id"].to_list())
        blow_games = g_s.filter(pl.col("margin") >= BLOW_MIN)
        sres: dict[str, Any] = {
            "games": g_s.height,
            "games_with_any_garbage": int((g_s["garbage_min"] > 0).sum()),
            "garbage_min_mean_all": float(g_s["garbage_min"].mean()),  # type: ignore[arg-type]
            "garbage_min_mean_blowout": float(blow_games["garbage_min"].mean()),  # type: ignore[arg-type]
            "blowout_games_a_lt_1": int((blow_games["garbage_min"] > 11.0).sum()),
            "cells": {},
        }
        cs = cells(d.filter(pl.col("a_g") >= MIN_A))
        for (g, c), sub in cs.items():
            sres["cells"][f"{g}_{c}"] = {
                "player_games": sub.height,
                "games": sub["game_id"].n_unique(),
                "sum_A_min": float(sub["a_g"].sum()),
            }
        # G5 projected spread: no-exclusion young gap (already measured in F12_RETEST), spread only
        cs_all = cells(d)
        bs = boot_gap(cs_all[("young", "close")], cs_all[("young", "blow")], games, "q4", "q13")
        hw = float((np.quantile(bs, 0.975) - np.quantile(bs, 0.025)) / 2.0)
        yb = cs[("young", "blow")]
        a_yb = float(yb["a_g"].sum())
        infl = float(np.sqrt(12.0 * yb.height / a_yb)) if yb.height else float("inf")
        sres["young_gap_halfwidth_no_exclusion"] = hw
        sres["inflation_sqrt_12_over_A"] = infl
        sres["young_gap_halfwidth_projected"] = hw * infl
        res["seasons"][str(s)] = sres
    covd = {int(s): (n, k) for s, n, k in cov}
    q4d = {int(s): (n, a, b, c) for s, n, a, b, c in q4}
    recd = {int(s): (n, a) for s, n, a in recon}
    for s in seasons:
        sr = res["seasons"][str(s)]
        cmin = min(v["player_games"] for v in sr["cells"].values())
        gmin = min(v["games"] for v in sr["cells"].values())
        add = res["checks"].append
        add(
            {
                "check": f"G1a stints cover games {s} >= 0.95",
                "value": covd[s][1] / covd[s][0],
                "ok": covd[s][1] / covd[s][0] >= 0.95,
            }
        )
        add(
            {
                "check": f"G1b stints vs box within 1 min {s} >= 0.95",
                "value": recd[s][1],
                "n": recd[s][0],
                "ok": recd[s][1] >= 0.95,
            }
        )
        add(
            {
                "check": f"G2 as written (first Q4 poss >= 700 s, score_diff non-null) {s} >= 0.98",
                "value": q4d[s][1],
                "ok": q4d[s][1] >= 0.98,
            }
        )
        add(
            {
                "check": f"G2-A1 amended (state from 720 s; first Q4 poss >= 680 s, "
                f"score_diff non-null) {s} >= 0.98",
                "value": q4d[s][3],
                "ok": q4d[s][3] >= 0.98,
            }
        )
        add(
            {
                "check": f"G3 last-possession margin (any period) within 3 of final {s} >= 0.95",
                "value": q4d[s][2],
                "ok": q4d[s][2] >= 0.95,
            }
        )
        add(
            {
                "check": f"G4 cells >= 500 player-games and >= 150 games {s}",
                "value": [cmin, gmin],
                "ok": cmin >= 500 and gmin >= 150,
            }
        )
        a_yb = sr["cells"]["young_blow"]["sum_A_min"]
        add(
            {
                "check": f"G4 young x blowout non-garbage Q4 minutes {s} >= 1000",
                "value": a_yb,
                "ok": a_yb >= 1000,
            }
        )
        add(
            {
                "check": f"G5 projected young-gap half-width {s} <= 0.04",
                "value": sr["young_gap_halfwidth_projected"],
                "ok": sr["young_gap_halfwidth_projected"] <= 0.04,
            }
        )
    res["all_ok"] = all(
        c["ok"] for c in res["checks"] if not c["check"].startswith("G2 as written")
    )
    _log("gate done", t0)
    return res


def run_premise(db: str, sha: str, seasons: tuple[int, ...] = SEASONS) -> dict[str, Any]:
    """The frozen descriptive test. Refuses unless the doc hash equals the committed ``sha``."""
    have = frozen_sha256()
    if not sha or have != sha:
        raise SystemExit(f"refused: doc sha256 {have} != --sha {sha!r} (commit the rule first)")
    df, gm = load(db, seasons)
    out: dict[str, Any] = {"frozen_sha256": have, "seasons": {}}
    for s in seasons:
        d = df.filter((pl.col("season") == s) & (pl.col("a_g") >= MIN_A))
        games = sorted(gm.filter(pl.col("season") == s)["game_id"].to_list())
        cs = cells(d)
        gaps: dict[str, Any] = {}
        for grp in ("young", "vet"):
            c, b = cs[(grp, "close")], cs[(grp, "blow")]
            gap = ratio_ng(c) / ratio_ng(b) - 1.0
            bs = _boot_ng(c, b, games)
            gaps[grp] = {
                "gap": gap,
                "ci": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))],
                "n_close": c.height,
                "n_blow": b.height,
            }
        p1 = gaps["vet"]["gap"] >= BAND
        p2 = abs(gaps["young"]["gap"]) <= BAND
        out["seasons"][str(s)] = {"gaps": gaps, "P1": p1, "P2": p2}
    out["PASS"] = all(v["P1"] and v["P2"] for v in out["seasons"].values())
    return out


def _boot_ng(close: pl.DataFrame, blow: pl.DataFrame, games: list[str]) -> np.ndarray:
    idx = {g: i for i, g in enumerate(games)}
    rng = np.random.default_rng(SEED)
    mats = []
    for sub in (close, blow):
        pg = sub.group_by("game_id").agg(
            pl.col("q4ng").sum(), pl.col("a_g").sum(), pl.col("q13").sum(), pl.len().alias("n")
        )
        v = np.zeros((len(games), 4))
        for g, a, b, c, n in pg.iter_rows():
            v[idx[g]] = (a, b, c, n)
        mats.append(v)
    out = np.empty(B_BOOT)
    for k in range(B_BOOT):
        w = np.bincount(rng.integers(0, len(games), len(games)), minlength=len(games))
        r = []
        for v in mats:
            q4ng, a, q13, n = (w @ v[:, j] for j in range(4))
            r.append((q4ng / a) / (q13 / (36.0 * n)))
        out[k] = r[0] / r[1] - 1.0
    return out


def _round(x: Any) -> Any:
    """10 significant digits: parallel float sums differ in the last bits between runs."""
    if isinstance(x, float):
        return float(f"{x:.10g}")
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_round(v) for v in x]
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["gate", "premise"])
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--sha", default="")
    ap.add_argument(
        "--seasons", default="2023,2024", help="gate only; premise uses the frozen seasons"
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.stage == "gate":
        res = run_gate(args.db, tuple(int(x) for x in args.seasons.split(",")))
    else:
        res = run_premise(args.db, args.sha)
    text = json.dumps(_round(res), indent=2, default=str)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"sha256 {hashlib.sha256(text.encode()).hexdigest()[:16]} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
