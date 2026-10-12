# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""Pace diagnostic (A) for the production props model, 2023-24 only. Descriptive, no claim.

    .venv/bin/python -W ignore -m research.eval.pace_periods pace

Reads the cached OOF integer quantiles of the production context-residual model (``feature_significance`` ``full`` arm,
seasons 2023 and 2024 only; the cache also holds nothing from 2025) and the game / possessions / player_game_stats tables
(read-only, ``season <= 2024``). As-of pace: a team's rolling-20 (min 5, shift 1) possessions per 48 from the possessions table.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.props.metrics import _clustered_boot_means

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/ops/feature_significance_cache"
OUT = ROOT / "reports/pace_periods_2026-10-12.results.json"
DB = ROOT / "nba.duckdb"
STATS = ("pts", "reb", "ast", "fg3m")
SEED = 20261012
N_BOOT = 2000


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def team_games() -> pl.DataFrame:
    """One row per (game, team): as-of r20 pts for / against and r20 possessions per 48 (own and opponent)."""
    con = duckdb.connect(str(DB), read_only=True)
    g = con.execute(
        "select game_id, game_date, season, home_team, away_team, home_pts, away_pts from games where season <= 2024"
    ).pl()
    p = con.execute(
        "select p.game_id, count(*) n_poss, max(p.period) maxp from possessions p join games g using(game_id) where g.season <= 2024 group by 1"
    ).pl()
    con.close()
    return asof_rolls(g.join(p, on="game_id", how="left"))


def asof_rolls(g: pl.DataFrame) -> pl.DataFrame:
    """g: one row per game with game_id, game_date, season, home_team, away_team, home_pts, away_pts, n_poss, maxp. Pure (testable)."""
    mins = 48.0 + 5.0 * (pl.col("maxp") - 4).clip(0, 10)
    g = g.with_columns((pl.col("n_poss") / 2.0 * 48.0 / mins).alias("pace"))
    home = g.select(
        "game_id",
        "game_date",
        "season",
        pl.col("home_team").alias("team_id"),
        pl.col("away_team").alias("opp_id"),
        pl.col("home_pts").alias("pf"),
        pl.col("away_pts").alias("pa"),
        "pace",
    )
    away = g.select(
        "game_id",
        "game_date",
        "season",
        pl.col("away_team").alias("team_id"),
        pl.col("home_team").alias("opp_id"),
        pl.col("away_pts").alias("pf"),
        pl.col("home_pts").alias("pa"),
        "pace",
    )
    tg = pl.concat([home, away]).sort(["team_id", "game_date", "game_id"])
    tg = tg.with_columns(
        [
            pl.col(c).shift(1).rolling_mean(20, min_samples=5).over("team_id").alias(f"r20_{c}")
            for c in ("pf", "pa", "pace")
        ]
    )
    opp = tg.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("r20_pf").alias("o_pf"),
        pl.col("r20_pa").alias("o_pa"),
        pl.col("r20_pace").alias("o_pace"),
    )
    return tg.join(opp, on=["game_id", "opp_id"], how="left").with_columns(
        ((pl.col("r20_pace") + pl.col("o_pace")) / 2.0).alias("xpace")
    )


def load_oof(stat: str) -> dict[str, np.ndarray]:
    parts: dict[str, list[np.ndarray]] = {
        k: [] for k in ("crps", "qi", "y", "gid", "pid", "season")
    }
    for f in sorted(CACHE.glob(f"{stat}_*_full.npz")):
        z = np.load(f)
        for k in parts:
            parts[k].append(z[k])
    d = {k: np.concatenate(v) for k, v in parts.items()}
    keep = d["season"] <= 2024
    return {k: v[keep] for k, v in d.items()}


def cl_boot(values: np.ndarray, gid: np.ndarray, seed: int = SEED) -> list[float]:
    b = _clustered_boot_means(values, gid, N_BOOT, np.random.default_rng(seed))
    return [float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))]


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, y, rcond=None)[0]


def partial(xs: np.ndarray, resid: np.ndarray, pace: np.ndarray) -> tuple[float, float, float]:
    """Slope of resid on pace after the controls ``xs`` (with intercept), partial R, partial R^2 of the pace term."""
    c = np.column_stack([np.ones(len(resid)), xs])
    rr = resid - c @ ols(c, resid)
    pp = pace - c @ ols(c, pace)
    slope = float((pp @ rr) / (pp @ pp))
    r = float((pp @ rr) / np.sqrt((pp @ pp) * (rr @ rr)))
    return slope, r, r * r


def cluster_boot_partial(
    xs: np.ndarray, resid: np.ndarray, pace: np.ndarray, gid: np.ndarray, b: int = 300
) -> dict[str, list[float]]:
    u, inv = np.unique(gid, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    starts = np.searchsorted(inv[order], np.arange(len(u)))
    ends = np.append(starts[1:], len(inv))
    rng = np.random.default_rng(SEED)
    sl, rr_ = [], []
    for _ in range(b):
        pick = rng.integers(0, len(u), len(u))
        idx = np.concatenate([order[starts[i] : ends[i]] for i in pick])
        s, r, _ = partial(xs[idx], resid[idx], pace[idx])
        sl.append(s)
        rr_.append(r)
    q = lambda v: [float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))]  # noqa: E731
    return {"slope_ci": q(sl), "r_ci": q(rr_)}


def run_pace() -> dict[str, Any]:
    tg = team_games()
    con = duckdb.connect(str(DB), read_only=True)
    pt = con.execute(
        "select p.game_id, p.player_id, p.team_id from player_game_stats p join games g using(game_id) where g.season <= 2024"
    ).pl()
    con.close()
    cov: dict[str, Any] = {}
    res: dict[str, Any] = {}
    for stat in STATS:
        d = load_oof(stat)
        fr = pl.DataFrame(
            {
                "game_id": d["gid"],
                "player_id": d["pid"].astype(np.int32),
                "i": np.arange(len(d["y"])),
            }
        )
        fr = (
            fr.join(pt, on=["game_id", "player_id"], how="left")
            .join(
                tg.select(
                    "game_id",
                    "team_id",
                    "game_date",
                    "r20_pf",
                    "r20_pa",
                    "o_pf",
                    "o_pa",
                    "xpace",
                    "r20_pace",
                    "o_pace",
                ),
                on=["game_id", "team_id"],
                how="left",
            )
            .sort("i")
        )
        ok = (
            fr.select(
                pl.all_horizontal(pl.col("r20_pf", "r20_pa", "o_pf", "o_pa", "xpace").is_not_null())
            )
            .to_series()
            .to_numpy()
        )
        n_all = len(ok)
        cov[stat] = {"rows": n_all, "rows_with_asof_pace": int(ok.sum()), "share": float(ok.mean())}
        sel = ok
        y = d["y"][sel].astype(float)
        qi = d["qi"][sel].astype(float)
        crps = d["crps"][sel].astype(float)
        gid = d["gid"][sel]
        season = d["season"][sel]
        mean = qi.mean(axis=1)
        resid = y - mean
        f = fr.filter(pl.Series(sel))
        xp = f["xpace"].to_numpy()
        pz = (xp - xp.mean()) / xp.std()
        ctrl = np.column_stack(
            [f[c].to_numpy() for c in ("r20_pf", "r20_pa", "o_pf", "o_pa")] + [mean]
        )
        # deciles of expected pace (row-level qcut)
        edges = np.quantile(xp, np.linspace(0, 1, 11))
        dec = np.clip(np.searchsorted(edges, xp, side="right") - 1, 0, 9)
        dec_rows = []
        for k in range(10):
            m = dec == k
            dec_rows.append(
                {
                    "decile": k + 1,
                    "pace_lo": float(edges[k]),
                    "pace_hi": float(edges[k + 1]),
                    "n": int(m.sum()),
                    "games": int(len(np.unique(gid[m]))),
                    "mean_resid": float(resid[m].mean()),
                    "resid_ci": cl_boot(resid[m], gid[m]),
                    "mean_pred": float(mean[m].mean()),
                    "mean_y": float(y[m].mean()),
                    "crps": float(crps[m].mean()),
                    "crps_ci": cl_boot(crps[m], gid[m]),
                    "rel_resid": float(resid[m].sum() / mean[m].sum()),
                }
            )
        # top minus bottom decile of residual (difference of independent-game groups; cluster by game over the union)
        top, bot = dec == 9, dec == 0
        tb_vals = np.where(top, resid, np.nan)
        diff = float(resid[top].mean() - resid[bot].mean())
        # bootstrap the difference by resampling games
        u, inv = np.unique(gid, return_inverse=True)
        st = {}
        for nm, m in (("top", top), ("bot", bot)):
            st[nm] = (
                np.bincount(inv, weights=np.where(m, resid, 0.0), minlength=len(u)),
                np.bincount(inv, weights=m.astype(float), minlength=len(u)),
            )
        rng = np.random.default_rng(SEED)
        bd = []
        for _ in range(N_BOOT):
            pk = rng.integers(0, len(u), len(u))
            bd.append(
                st["top"][0][pk].sum() / st["top"][1][pk].sum()
                - st["bot"][0][pk].sum() / st["bot"][1][pk].sum()
            )
        del tb_vals
        # partial regression
        slope, r, r2 = partial(ctrl, resid, pz)
        bp = cluster_boot_partial(ctrl, resid, pz, gid)
        slope0, r0, _ = partial(np.zeros((len(y), 0)), resid, pz)
        # relative (percent of forecast mean) slope among rows with forecast mean >= 1
        mm = mean >= 1.0
        rel = resid[mm] / mean[mm]
        rs, rr_, _ = partial(ctrl[mm][:, :4], rel, pz[mm])
        # does the model's own r20 already carry pace? corr of xpace with r20_pf + r20_pa (as-of team scoring level)
        tot20 = f["r20_pf"].to_numpy() + f["r20_pa"].to_numpy()
        res[stat] = {
            "n_rows": int(len(y)),
            "n_games": int(len(np.unique(gid))),
            "overall_mean_resid": float(resid.mean()),
            "overall_mean_resid_ci": cl_boot(resid, gid),
            "pace_sd_poss_per48": float(xp.std()),
            "pace_mean": float(xp.mean()),
            "corr_pace_vs_r20_total": float(np.corrcoef(xp, tot20)[0, 1]),
            "raw_corr_resid_pace": r0,
            "raw_slope_per_sd": slope0,
            "partial_slope_per_sd": slope,
            "partial_slope_ci": bp["slope_ci"],
            "partial_r": r,
            "partial_r_ci": bp["r_ci"],
            "partial_r2": r2,
            "rel_slope_per_sd_pct_of_mean": float(rs * 100),
            "rel_partial_r": rr_,
            "rel_n": int(mm.sum()),
            "top_minus_bottom_decile_resid": diff,
            "top_minus_bottom_ci": [float(np.quantile(bd, 0.025)), float(np.quantile(bd, 0.975))],
            "deciles": dec_rows,
            "by_season_partial": {
                str(s): {
                    "slope": partial(ctrl[season == s], resid[season == s], pz[season == s])[0],
                    "n": int((season == s).sum()),
                }
                for s in (2023, 2024)
            },
        }
        _log(
            f"{stat}: n={len(y)} partial slope/sd {slope:+.4f} CI {bp['slope_ci']} r {r:+.4f} top-bottom {diff:+.4f}"
        )
    return {"coverage": cov, "stats": res}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["pace", "periods", "gates"])
    a = ap.parse_args(argv)
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    if a.stage == "pace":
        out["A_pace"] = run_pace()
    elif a.stage == "periods":
        from research.eval import pace_periods_b as B

        out["B_periods"] = B.run_periods()
    else:
        from research.eval import pace_periods_b as B

        out["C_gates"] = B.run_gates()
    OUT.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
