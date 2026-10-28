"""RAPM_V2 minimum-data gate (docs/prereg/RAPM_V2.md). Label-free: no RAPM fit, no outcome read.

    .venv/bin/python -m nba.eval.rapm_v2_gate --out reports/prereg_rapm_v2/gate.json

Counts (games, usable reports at real tip-60, lineup-complete possessions, stint reconciliation) per
season 2019-2024 on ``nba.duckdb`` and the history DB, the oracle movement ceiling from the stored
injury-Elo / MOV-Elo OOF probabilities (no labels), and the power formulas. ``season <= 2024`` in
every query; schedule files of later seasons are never linked. Read-only connections only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.stats import norm

import nba.features.game_tipoff as game_tipoff
from nba.models.injury_elo import InjuryFeatureConfig, build_injury_features, load_stats_frame

MAX_SEASON = 2024
Z = 1.959964
FLOOR = 0.002
RHO_T176 = 0.69
MIN_SCORED = 3900
MIN_POSS_COMPLETE = 0.99
MIN_RECON = 0.98
MIN_POWER = 0.80
SD_T176 = 0.1110

GAMES_SQL = (
    "SELECT game_id, game_date, season, home_team, away_team FROM games "
    "WHERE season <= ? AND season >= ? AND home_pts IS NOT NULL ORDER BY game_date, game_id"
)
REPORT_SQL = (
    "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
    "CAST(a.as_of AS TIMESTAMP) AS as_of, "
    "g.game_date FROM player_availability a JOIN games g ON g.game_id = a.game_id "
    "WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL AND g.season <= ?"
)
POSS_SQL = """
SELECT g.season, count(*) AS n,
       avg(CASE WHEN list_count(list_filter(off_players, x -> x IS NOT NULL)) = 5
                 AND list_count(list_filter(def_players, x -> x IS NOT NULL)) = 5
                THEN 1.0 ELSE 0 END) AS complete
FROM possessions p JOIN games g USING (game_id)
WHERE g.season <= ? AND g.season >= ? GROUP BY 1 ORDER BY 1
"""
RECON_SQL = """
WITH st AS (SELECT game_id, UNNEST(players) AS player_id, start_clock - end_clock AS d FROM stints),
pm AS (SELECT game_id, player_id, SUM(d) / 60.0 AS m FROM st GROUP BY 1, 2)
SELECT g.season, count(*) AS n,
       avg(CASE WHEN abs(pm.m - b.minutes) <= 1.0 THEN 1.0 ELSE 0 END) AS ok
FROM pm JOIN games g USING (game_id)
JOIN player_game_stats b ON b.game_id = pm.game_id AND b.player_id = pm.player_id
WHERE g.season <= ? AND g.season >= ? GROUP BY 1 ORDER BY 1
"""


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def power_true_floor(n: int, gain: float) -> dict[str, float]:
    """P(CI upper < 0) if the candidate is the truth with expected gain ``gain`` (var ~ 2|G|)."""
    g = abs(gain)
    se = math.sqrt(2.0 * g / n)
    return {"n": n, "gain": -g, "se": se, "power": float(norm.cdf(g / se - Z))}


def power_measured_sd(n: int, gain: float, sd: float) -> dict[str, float]:
    se = sd / math.sqrt(n)
    return {
        "n": n,
        "gain": -abs(gain),
        "sd": sd,
        "se": se,
        "power": float(norm.cdf(abs(gain) / se - Z)),
    }


def kl_ceiling(p_inj: np.ndarray, p_elo: np.ndarray) -> dict[str, float]:
    """Mean KL(injury || elo) and RMS logit shift: how far the whole injury term moves forecasts."""
    a = np.clip(p_elo, 1e-9, 1 - 1e-9)
    b = np.clip(p_inj, 1e-9, 1 - 1e-9)
    kl = b * np.log(b / a) + (1 - b) * np.log((1 - b) / (1 - a))
    dl = np.log(b / (1 - b)) - np.log(a / (1 - a))
    return {"n": len(a), "K": float(kl.mean()), "rms_logit_shift": float(np.sqrt((dl**2).mean()))}


def swap_ceiling(k: float, rho: float) -> float:
    """Oracle gain of an equal-variance value swap with correlation ``rho`` to the box shift."""
    return 2.0 * (1.0 - rho) * k


def _sched(src: Path, max_season: int) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rapm_v2_sched_"))
    for f in sorted(src.glob("raw_*.parquet")):
        fs = game_tipoff._file_season(f)
        if fs is not None and fs <= max_season:
            (tmp / f.name).symlink_to(f.resolve())
    return tmp


def _db_counts(db: str, sched_dir: Path, lo: int, hi: int, t0: float) -> dict[str, Any]:
    sched = _sched(sched_dir, hi)
    game_tipoff.SCHEDULE_DIR = sched
    con = duckdb.connect(db, read_only=True)
    try:
        games = con.execute(GAMES_SQL, [hi, lo]).pl()
        assert games.height == 0 or int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
        stats = load_stats_frame(con, hi)
        rows = con.execute(REPORT_SQL, [hi]).pl()
        rows = rows.with_columns(pl.col("as_of").cast(pl.Datetime("us")))
        rows = rows.filter(pl.col("game_id").is_in(games["game_id"].to_list()))
        tips = game_tipoff.build_game_tipoff(sched, max_season=hi)
        n_tip = games.join(tips.select("game_id"), on="game_id").height
        rows_t = game_tipoff.attach_real_tips(rows, tips=tips)
        _log(f"{db}: {games.height} games, {stats.height} box rows, {rows.height} report rows", t0)
        feats = build_injury_features(
            con,
            games,
            stats,
            InjuryFeatureConfig(),
            report_rows=rows_t.drop("tip_et"),
            with_oracle=False,
        )
        poss = con.execute(POSS_SQL, [hi, lo]).fetchall()
        recon = con.execute(RECON_SQL, [hi, lo]).fetchall()
    finally:
        con.close()
    f = games.join(feats, on="game_id")
    out: dict[str, Any] = {"real_tip_games": n_tip, "games": games.height, "seasons": {}}
    for s in sorted(set(games["season"].to_list())):
        fs = f.filter(pl.col("season") == s)
        out["seasons"][str(s)] = {
            "games": games.filter(pl.col("season") == s).height,
            "with_report": int(fs["has_report"].sum()),
            "rot_out_ge1": int((fs["n_rot_out"] >= 1).sum()),
            "rot_out_ge2": int((fs["n_rot_out"] >= 2).sum()),
        }
    for s, n, c in poss:
        out["seasons"][str(s)].update({"possessions": int(n), "poss_complete": float(c)})
    for s, n, ok in recon:
        out["seasons"][str(s)].update(
            {"recon_player_games": int(n), "recon_within_1min": float(ok)}
        )
    return out


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    t0 = time.time()
    res: dict[str, Any] = {"rule": "RAPM_V2 (DRAFT)", "max_season": MAX_SEASON, "floor": -FLOOR}
    latest = Path(args.manifest_dir) / "latest.json"
    if latest.exists():
        man = json.loads(latest.read_text())
        res["data_version"] = man.get("data_version")
        res["manifest_created_at"] = man.get("created_at")
    res["prod"] = _db_counts(args.db, Path(args.schedule_dir), 2022, MAX_SEASON, t0)
    res["history"] = _db_counts(args.history_db, Path(args.history_schedule_dir), 2019, 2021, t0)
    oof = pl.read_parquet(args.oof).filter(pl.col("season") <= MAX_SEASON)
    w = oof.pivot(on="model", index=["game_id", "season"], values="p")
    ceil: dict[str, Any] = {}
    parts = [("pooled", w)] + [
        (str(s), w.filter(pl.col("season") == s)) for s in (2022, 2023, 2024)
    ]
    for key, sub in parts:
        c: dict[str, Any] = dict(
            kl_ceiling(sub["rung0_injury_elo"].to_numpy(), sub["rung0_mov_elo"].to_numpy())
        )
        c["swap_ceiling"] = {str(r): swap_ceiling(c["K"], r) for r in (0.5, RHO_T176, 0.8, 0.9)}
        ceil[key] = c
    res["movement_ceiling"] = ceil
    n_scored = w.height
    seasons = {**res["history"]["seasons"], **res["prod"]["seasons"]}
    n_hist_rep = sum(seasons[s]["with_report"] for s in ("2019", "2020", "2021"))
    designs = {
        "season_2023": int(w.filter(pl.col("season") == 2023).height),
        "season_2024": int(w.filter(pl.col("season") == 2024).height),
        "pooled_2023_2024": int(w.filter(pl.col("season") >= 2023).height),
        "pooled_2022_2024": n_scored,
        "pooled_2019_2024_if_history_scored": n_scored + n_hist_rep,
    }
    k = ceil["pooled"]["K"]
    cap = swap_ceiling(k, RHO_T176)
    res["power"] = {
        d: {
            "self_consistent_at_floor": power_true_floor(n, FLOOR),
            "self_consistent_at_0.004": power_true_floor(n, 0.004),
            "self_consistent_at_ceiling_rho_0.69": power_true_floor(n, cap),
            "t176_sd_at_floor": power_measured_sd(n, FLOOR, SD_T176),
            "t176_sd_at_0.004": power_measured_sd(n, 0.004, SD_T176),
        }
        for d, n in designs.items()
    }
    p = res["power"]["pooled_2022_2024"]
    checks = [
        ("G-D1 scored games 2022-24 >= 3900", n_scored, n_scored >= MIN_SCORED),
        (
            "G-D2 lineup-complete possessions >= 0.99 every season",
            min(v["poss_complete"] for v in seasons.values()),
            all(v["poss_complete"] >= MIN_POSS_COMPLETE for v in seasons.values()),
        ),
        (
            "G-D3 stints vs box within 1 min >= 0.98 every season",
            min(v["recon_within_1min"] for v in seasons.values()),
            all(v["recon_within_1min"] >= MIN_RECON for v in seasons.values()),
        ),
        (
            "G-D4 real tips cover all games (prod, history)",
            [
                res["prod"]["real_tip_games"],
                res["prod"]["games"],
                res["history"]["real_tip_games"],
                res["history"]["games"],
            ],
            res["prod"]["real_tip_games"] == res["prod"]["games"]
            and res["history"]["real_tip_games"] == res["history"]["games"],
        ),
        (
            "G-P power at floor -0.002, pooled 2022-24, self-consistent >= 0.80",
            p["self_consistent_at_floor"]["power"],
            p["self_consistent_at_floor"]["power"] >= MIN_POWER,
        ),
        ("G-C1 swap ceiling at rho 0.69 >= 0.002", cap, cap >= FLOOR),
        (
            "G-C2 power at the ceiling (rho 0.69), pooled 2022-24 >= 0.80",
            p["self_consistent_at_ceiling_rho_0.69"]["power"],
            p["self_consistent_at_ceiling_rho_0.69"]["power"] >= MIN_POWER,
        ),
    ]
    res["checks"] = [{"check": c, "value": v, "ok": bool(o)} for c, v, o in checks]
    res["all_ok"] = all(c["ok"] for c in res["checks"])
    _log("gate done", t0)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--history-db", default="data/history/nba_history.duckdb")
    ap.add_argument("--schedule-dir", default="data/schedule")
    ap.add_argument("--history-schedule-dir", default="data/history/schedule")
    ap.add_argument("--oof", default="data/injury_elo_v2/oof_predictions.parquet")
    ap.add_argument("--manifest-dir", default="docs/data_manifests")
    ap.add_argument("--out", default="reports/prereg_rapm_v2/gate.json")
    args = ap.parse_args(argv)
    res = run_gate(args)
    text = json.dumps(res, indent=2, default=str)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(
        f"sha256 {hashlib.sha256(text.encode()).hexdigest()[:16]} -> {out}; all_ok={res['all_ok']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
