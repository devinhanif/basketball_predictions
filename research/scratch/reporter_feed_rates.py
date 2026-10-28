# ruff: noqa: E501
# mypy: ignore-errors
"""REPORTER_FEED_LIVE sizing: descriptive event rates on 2023-24 (no rule, no fit, no outcome of any reporter test).

Two inputs a live-only reporter rule is powered from, because no reporter archive exists:
  (A) official-report status changes AFTER the T-60 list: latest report <= real tip - 60 min versus the last report published before tip,
      on the header-clock repaired table R (INJURY_TIMING rev 3: intraday v2 UNION stored, header-shifted). These are the events a reporter
      post at or before T-60 could front-run. Rotation = rotation_flagged_by_team (prior-10 avg >= 20 over >= 5 games, as-of).
  (B) rotation returners: first game back after the team played >= K straight games without him (K = 3, 5, 10), prior-10 played-games
      average >= 15 min, from box scores; per game rate, and production's minutes path P (reports/minutes_v2/minutes_scores.npz) error on
      those rows (mean y - P_mean, P CRPS), the population a "minutes cap" post is mostly about.
Read-only, every SQL `season <= 2024`; season 2025 never loaded. Seed 2026, B = 2000, game-clustered percentile bootstrap.
Run: PYTHONPATH=<worktree> <main>/.venv/bin/python research/scratch/reporter_feed_rates.py
Output: reports/reporter_feed/rates_2023_24.json
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from injury_timing_descriptive import (  # noqa: E402
    SEED,
    B,
    boot_mean,
    event_table,
    prior_state,
    transitions,
)
from injury_timing_gap_corrected import snapshot  # noqa: E402

from research.eval import injury_timing_gate as gate  # noqa: E402

MAIN = Path("/Users/devin/Downloads/nba-prediction")
SCRATCH = MAIN / "data/scratch/injury_intraday_v2.duckdb"
OUT = HERE.parents[1] / "reports/reporter_feed/rates_2023_24.json"
MIN_NPZ = MAIN / "reports/minutes_v2/minutes_scores.npz"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def part_a(res: dict, rng: np.random.Generator) -> None:
    b = gate.build_all(MAIN, SCRATCH)
    tips, R = b["tips"], b["R"]
    con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
    state = prior_state(con)
    for season in (2023, 2024):
        t0 = time.time()
        gl = b["games"][season]
        info = {
            g: (d, int(h), int(a))
            for g, d, h, a in con.execute(
                "SELECT game_id, game_date, home_team, away_team FROM games WHERE season=? AND season <= 2024",
                [season],
            ).fetchall()
        }
        Rs = R.filter(pl.col("season") == season)
        s60, t60 = snapshot(Rs, {g: tips[g] - dt.timedelta(minutes=60) for g in gl if g in tips})
        s0, t0s = snapshot(Rs, {g: tips[g] - dt.timedelta(microseconds=1) for g in gl if g in tips})
        both = sorted(g for g in t60 if g in t0s)  # fixed order: the seeded bootstrap depends on it
        later = [g for g in both if t0s[g] > t60[g]]
        lset = set(later)
        old = {k: v for k, v in s60.items() if k[0] in lset}
        new = {k: v for k, v in s0.items() if k[0] in lset}
        allc, rotc, mat = transitions(old, new, later, info, state)
        n_all = len(both)
        # per-game rates over ALL games with a T-60 list (games with no later report contribute zeros)
        pad = n_all - len(later)
        rot_all = {e: np.concatenate([rotc[e], np.zeros(pad)]) for e in rotc}
        al_all = {e: np.concatenate([allc[e], np.zeros(pad)]) for e in allc}
        gap = np.array([(t0s[g] - t60[g]).total_seconds() / 60 for g in later])
        q_at_60 = sum(1 for (g, _p), v in s60.items() if g in set(both) and v == "questionable")
        res[f"A_{season}"] = {
            "games_with_T60_list": n_all,
            "games_with_a_report_after_T60": len(later),
            "share_with_later_report": round(len(later) / max(n_all, 1), 4),
            "minutes_T60_to_last_pretip_report_p10_p50_p90": [
                float(np.percentile(gap, q)) for q in (10, 50, 90)
            ]
            if len(gap)
            else None,
            "events_per_game_over_all_T60_games": event_table(al_all, rot_all, n_all, rng),
            "questionable_listings_at_T60": q_at_60,
            "transition_counts_old_to_new": {
                f"{a}->{c}": int(n) for (a, c), n in mat.most_common(14)
            },
        }
        log(
            f"A {season}: {n_all} games, {len(later)} with a later report ({time.time() - t0:.1f}s)"
        )
    con.close()


def returners(con: duckdb.DuckDBPyConnection, season: int) -> pl.DataFrame:
    """One row per (game, player) where the player has a box row with minutes > 0, plus the count of his team's
    immediately preceding consecutive team games (same season) in which he had no minutes > 0, and his prior-10 average."""
    tg = con.execute(
        """
        SELECT g.game_id, g.game_date, t.team_id
        FROM games g, (SELECT home_team AS team_id, game_id FROM games WHERE season = ? AND season <= 2024
                       UNION ALL SELECT away_team, game_id FROM games WHERE season = ? AND season <= 2024) t
        WHERE g.game_id = t.game_id AND substr(g.game_id, 1, 3) = '002'
        """,
        [season, season],
    ).pl()
    tg = tg.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.int_range(pl.len()).over("team_id").alias("tgn")
    )
    box = con.execute(
        """
        SELECT s.game_id, s.player_id, s.team_id, s.minutes
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE g.season = ? AND g.season <= 2024 AND COALESCE(s.minutes, 0) > 0 AND substr(g.game_id, 1, 3) = '002'
        """,
        [season],
    ).pl()
    b = box.join(tg, on=["game_id", "team_id"]).sort(["player_id", "team_id", "tgn"])
    b = b.with_columns(
        (pl.col("tgn") - pl.col("tgn").shift(1).over(["player_id", "team_id"]) - 1).alias(
            "missed_before"
        ),
        pl.col("minutes")
        .shift(1)
        .rolling_mean(window_size=10, min_samples=5)
        .over(["player_id", "team_id"])
        .alias("min10_prior"),
    )
    return b


def part_b(res: dict, rng: np.random.Generator) -> None:
    con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
    z = np.load(MIN_NPZ, allow_pickle=True)
    key = {
        (str(g), int(p)): i for i, (g, p) in enumerate(zip(z["row_gid"], z["row_pid"], strict=True))
    }
    for season in (2023, 2024):
        t0 = time.time()
        b = returners(con, season)
        n_games = b["game_id"].n_unique()
        out = {"regular_season_games": n_games}
        for K in (3, 5, 10):
            r = b.filter((pl.col("missed_before") >= K) & (pl.col("min10_prior") >= 15))
            per_game = (
                r.group_by("game_id").len().sort("game_id")
            )  # fixed order for the seeded bootstrap
            counts = np.zeros(n_games)
            counts[: per_game.height] = per_game["len"].to_numpy()
            idx = [key.get((g, p)) for g, p in r.select(["game_id", "player_id"]).iter_rows()]
            idx = np.array([i for i in idx if i is not None], dtype=int)
            y = z["row_y"][idx]
            pm = z["P_mean"][idx]
            pc = z["P_crps"][idx]
            gid = z["row_gid"][idx]
            # game-clustered bootstrap of the mean minutes error on returner rows
            ug, inv = np.unique(gid, return_inverse=True)
            err = y - pm
            sums = np.bincount(inv, weights=err)
            cnts = np.bincount(inv)
            csum = np.bincount(inv, weights=pc)
            bs, bc = [], []
            for _ in range(B):
                d = rng.integers(0, len(ug), len(ug))
                bs.append(sums[d].sum() / cnts[d].sum())
                bc.append(csum[d].sum() / cnts[d].sum())
            out[f"K{K}"] = {
                "returner_rows": int(r.height),
                "per_game": boot_mean(counts, rng),
                "rows_matched_to_P": int(len(idx)),
                "mean_y_minus_P_mean_min": [
                    float(err.mean()),
                    float(np.percentile(bs, 2.5)),
                    float(np.percentile(bs, 97.5)),
                ],
                "P_crps_min": [
                    float(pc.mean()),
                    float(np.percentile(bc, 2.5)),
                    float(np.percentile(bc, 97.5)),
                ],
                "share_y_below_0p75_P_mean": float((y < 0.75 * pm).mean()) if len(idx) else None,
                "min10_prior_p50": float(r["min10_prior"].median()) if r.height else None,
                "y_p10_p50_p90": [float(np.percentile(y, q)) for q in (10, 50, 90)]
                if len(idx)
                else None,
            }
        # all-rows reference: P CRPS and per-row sd on the season
        sel = z["row_season"] == season
        out["all_rows_P_crps_mean"] = float(z["P_crps"][sel].mean())
        out["all_rows_n"] = int(sel.sum())
        res[f"B_{season}"] = out
        log(f"B {season}: {n_games} games ({time.time() - t0:.1f}s)")
    con.close()


GRID = np.arange(0.0, 48.0001, 0.1)
#: weight of the truncated component in the cap treatment (1.0 = hard truncation); both are reported
MIX_W = 1.0


def _tn_cdf(mu: float, sd: float, upper: float = 48.0) -> np.ndarray:
    from scipy.stats import norm

    lo, hi = norm.cdf((0 - mu) / sd), norm.cdf((upper - mu) / sd)
    c = (norm.cdf((GRID - mu) / sd) - lo) / max(hi - lo, 1e-12)
    return np.clip(np.where(upper <= GRID, 1.0, c), 0.0, 1.0)


def _crps(cdf: np.ndarray, y: float) -> float:
    step = (y <= GRID).astype(float)
    return float(((cdf - step) ** 2).sum() * 0.1)


def part_c(res: dict, rng: np.random.Generator) -> None:
    """Power for the cap arm. NOT a result: the cap N is SIMULATED from the realised minutes (no reporter archive exists).
    Rows: K = 3 rotation returners (part B) of both seasons. P ~ Normal(mu, sd) truncated to [0, 48], mu = (q10 + q90) / 2,
    sd = (q90 - q10) / 2.5631 (approximation to production's trunc-normal). Treatment T = P truncated to [0, N + 2].
    Scenario: with prob h the cap is honoured, N = y + U(0, 4); else broken, N = y - U(1, 6); N clipped to [6, 40]."""
    con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
    z = np.load(MIN_NPZ, allow_pickle=True)
    key = {
        (str(g), int(p)): i for i, (g, p) in enumerate(zip(z["row_gid"], z["row_pid"], strict=True))
    }
    idx = []
    for season in (2023, 2024):
        b = returners(con, season).filter(
            (pl.col("missed_before") >= 3) & (pl.col("min10_prior") >= 15)
        )
        idx += [key[k] for k in b.select(["game_id", "player_id"]).iter_rows() if k in key]
    con.close()
    idx = np.array(idx, dtype=int)
    y, q10, q90 = z["row_y"][idx], z["P_q10"][idx], z["P_q90"][idx]
    mu, sd = (q10 + q90) / 2, np.clip((q90 - q10) / 2.5631, 1.0, None)
    a0 = np.load(MAIN / "reports/minutes_v2/props_pts_A0.npz", allow_pickle=True)
    assert (a0["gid"][idx] == z["row_gid"][idx]).all() and (
        a0["pid"][idx] == z["row_pid"][idx]
    ).all()
    pm, py = a0["mean"][idx], a0["y"][idx]
    # pts approximation: Normal(mean, sd_i), sd_i = 5.6 * sqrt(mean_i / 11.0) floored at 1.5 (5.6 = sd implied by the all-rows 3.15 CRPS)
    psd = np.clip(5.6 * np.sqrt(np.clip(pm, 0.5, None) / 11.0), 1.5, None)
    out = {
        "rows": int(len(idx)),
        "assumption": part_c.__doc__,
        "pts_assumption": "production pts ~ Normal(A0 mean, 5.6*sqrt(mean/11)); T scales mean and sd by r = clip(E_T[min]/E_P[min], 0.5, 1.0); continuous CRPS",
    }
    base = np.array([_crps(_tn_cdf(m, s), yy) for m, s, yy in zip(mu, sd, y, strict=True)])
    out["P_crps_reconstructed_mean"] = float(base.mean())
    out["A0_pts_crps_int_mean_on_rows"] = float(a0["crps_int"][idx].mean())

    def ncrps(m: np.ndarray, s: np.ndarray, x: np.ndarray) -> np.ndarray:
        from scipy.stats import norm

        w = (x - m) / s
        return s * (w * (2 * norm.cdf(w) - 1) + 2 * norm.pdf(w) - 1 / np.sqrt(np.pi))

    pbase = ncrps(pm, psd, py)
    for h in (0.6, 0.8, 0.9):
        d_all, dp_all = [], []
        for _rep in range(5):
            honoured = rng.random(len(y)) < h
            n_cap = np.where(honoured, y + rng.uniform(0, 4, len(y)), y - rng.uniform(1, 6, len(y)))
            n_cap = np.clip(np.round(n_cap), 6, 40)
            cdfs_t = [_tn_cdf(m, s, upper=nc + 2) for m, s, nc in zip(mu, sd, n_cap, strict=True)]
            cdfs_p = [_tn_cdf(m, s) for m, s in zip(mu, sd, strict=True)]
            if MIX_W < 1.0:  # T = w * truncated + (1 - w) * P (the cap holds with probability w)
                cdfs_t = [
                    MIX_W * ct + (1 - MIX_W) * cp for ct, cp in zip(cdfs_t, cdfs_p, strict=True)
                ]
            t = np.array([_crps(c, yy) for c, yy in zip(cdfs_t, y, strict=True)])
            e_t = np.array([((1 - c) * 0.1).sum() for c in cdfs_t])
            e_p = np.array([((1 - c) * 0.1).sum() for c in cdfs_p])
            r = np.clip(e_t / np.clip(e_p, 1e-6, None), 0.5, 1.0)
            d_all.append(t - base)
            dp_all.append(ncrps(r * pm, r * psd, py) - pbase)
        d, dp = np.concatenate(d_all), np.concatenate(dp_all)
        sdr, sdp = float(d.std(ddof=1)), float(dp.std(ddof=1))
        out[f"h{h}"] = {
            "mean_delta_crps_min": float(d.mean()),
            "sd_per_row": sdr,
            "n_for_halfwidth_0p25": int(np.ceil((1.96 * sdr / 0.25) ** 2)),
            "n_for_halfwidth_0p50": int(np.ceil((1.96 * sdr / 0.50) ** 2)),
            "share_rows_worse": float((d > 0).mean()),
            "pts_mean_delta_crps": float(dp.mean()),
            "pts_sd_per_row": sdp,
            "pts_n_for_halfwidth_0p10": int(np.ceil((1.96 * sdp / 0.10) ** 2)),
            "pts_n_for_halfwidth_0p20": int(np.ceil((1.96 * sdp / 0.20) ** 2)),
        }
    res[f"C_cap_power_simulated_w{MIX_W}"] = out
    log(f"C: {len(idx)} rows")


def main() -> None:  # noqa: PLW0603
    rng = np.random.default_rng(SEED)
    res: dict = {
        "script": "research/scratch/reporter_feed_rates.py",
        "seed": SEED,
        "B": B,
        "scratch_db": str(SCRATCH),
        "run_clock_start": dt.datetime.now().isoformat(timespec="seconds"),
    }
    part_b(res, rng)
    part_a(res, rng)
    global MIX_W
    for w in (1.0, 0.8):
        MIX_W = w
        part_c(res, np.random.default_rng(SEED + 1))
    res["run_clock_end"] = dt.datetime.now().isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    log(f"wrote {OUT}")


if __name__ == "__main__":
    main()
