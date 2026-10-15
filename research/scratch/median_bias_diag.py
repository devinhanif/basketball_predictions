# ruff: noqa: E501
"""MEDIAN_BIAS descriptive diagnosis (SCRATCH, no rule, no claim). Seasons 2022-2024 only; season 2025 is never loaded.

    .venv/bin/python -W ignore -m research.scratch.median_bias_diag --data-root <main checkout> --out reports/median_bias_diag_2026-10-15.json

Inputs (read-only, by absolute path under --data-root): reports/odds_history/model_rows_{2023,2024}.parquet (production A0
out-of-fold integer survival vectors), reports/lower_tail/components_{stat}.npz (A0 centre/scale per row and the
walk-forward calibration arrays per month block), data/ops/model_clv_pairs/pairs_{2023,2024}.parquet (population label only:
does the player-game have a DraftKings T-60 line), nba.duckdb (read-only, season <= 2024, for recency windows and 2022).
No candidate arm is scored here; only the mechanism numbers that need no test label of a candidate.

Mid-quantile gap at the median (MIDMED): with S(k) = P(Y >= k), S(0) = 1, the integer-sample median is m = #{k >= 1: S(k) >= 100/199}
(the 199-point grid's 100th point). R = 1{Y > m} + 0.5 * 1{Y = m}; E = 0.5 * (S(m) + S(m + 1)); gap = mean(R - E), zero when the
distribution is calibrated at its median, in percentage points here. Positive = outcomes fall above the forecast median.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
MAX_SEASON = 2024
B_BOOT = 1000
SEED = 2026
MIN_EDGES = (15.0, 25.0)  # nba.props.lower_tail.MINUTES_EDGES
HL = (5.0, 10.0, 40.0, 100.0)


def grid_from_components(comp: dict[str, np.ndarray]) -> np.ndarray:
    """Production A0 integer-support grid [n, 199]: c + s * quantile(z_cal of the row's block), clipped, monotone, ceil(q - .5)."""
    from nba.eval.lower_tail_eval import production_grid
    from nba.props.context_residual import to_integer_support

    return to_integer_support(production_grid(comp))


def row_quantities(comp: dict[str, np.ndarray], stat: str) -> pd.DataFrame:
    """Per-row MIDMED, mid-PIT, residuals and the T494-style continuous-median hit, from the walk-forward components."""
    qi = grid_from_components(comp)
    n = len(qi)
    y = comp["row_y"].astype(int)

    def surv(k: np.ndarray) -> np.ndarray:
        return np.asarray((qi >= k[:, None]).sum(axis=1) / qi.shape[1])

    kk = np.arange(1, int(qi.max()) + 2)
    cnt = np.stack([(qi >= k).sum(axis=1) for k in kk], axis=1) / qi.shape[1]  # S(k), k = 1..K
    m = (cnt >= 100.0 / 199.0).sum(1)
    s_m = np.where(m == 0, 1.0, cnt[np.arange(n), np.maximum(m - 1, 0)])
    s_m1 = cnt[np.arange(n), np.minimum(m, cnt.shape[1] - 1)]
    e = 0.5 * (s_m + s_m1)
    r = (y > m) + 0.5 * (y == m)
    s_y = surv(np.maximum(y, 1))
    s_y = np.where(y == 0, 1.0, s_y)
    s_y1 = surv(y + 1)
    mean_int = cnt.sum(1)
    out = pd.DataFrame(
        {
            "game_id": comp["row_gid"].astype(str), "player_id": comp["row_pid"].astype(np.int64), "stat": stat,
            "season": comp["row_season"], "blk": comp["row_blk"], "minutes": comp["row_minutes"], "min10": comp["row_min10"],
            "starter10": comp["row_starter10"], "n_prior": comp["row_n_prior"], "m_recency": comp["row_m"], "y": comp["row_y"],
            "c": comp["row_c"], "s": comp["row_s"], "mean_int": mean_int,
        }
    )  # fmt: skip
    out["m_int"] = m
    out["mm"] = r - e
    out["mid"] = 1 - 0.5 * (s_y + s_y1) - 0.5
    out["res"] = out.y - out.mean_int
    out["res_rec"] = out.y - out.m_recency
    out["res_c"] = out.y - out.c
    med = {
        int(b): float(np.median(comp["cal_z"][comp["cal_blk"] == b]))
        for b in np.unique(comp["cal_blk"])
    }
    out["z_med"] = [med[b] for b in out.blk]
    out["M_c"] = np.maximum(out.c.values + out.s.values * out.z_med.values, 0.0)
    out["over_Mc"] = (out.y > out.M_c).astype(float)
    kf = np.floor(out.M_c.values).astype(int) + 1
    out["impl_Mc"] = surv(kf)
    out["gap_Mc"] = out.over_Mc - out.impl_Mc
    return out


class Cluster:
    def __init__(self, gid: np.ndarray, b: int = B_BOOT, seed: int = SEED) -> None:
        u, self.code = np.unique(gid, return_inverse=True)
        self.G = len(u)
        self.W = (
            np.random.default_rng(seed)
            .multinomial(self.G, np.full(self.G, 1.0 / self.G), size=b)
            .astype(float)
        )

    def mean(self, v: np.ndarray, m: np.ndarray) -> tuple[float, float, float, int]:
        k = m & np.isfinite(v)
        ssum = np.bincount(self.code[k], weights=v[k], minlength=self.G)
        cnt = np.bincount(self.code[k], minlength=self.G).astype(float)
        n = int(cnt.sum())
        if n == 0:
            return float("nan"), float("nan"), float("nan"), 0
        den = self.W @ cnt
        bm = (self.W @ ssum) / np.where(den > 0, den, np.nan)
        lo, hi = np.nanpercentile(bm, [2.5, 97.5])
        return float(ssum.sum() / n), float(lo), float(hi), n


def table(
    df: pd.DataFrame, cl: Cluster, cuts: dict[str, pd.Series], cols: dict[str, str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cname, lab in cuts.items():
        for lv in sorted(lab.dropna().unique(), key=str):
            m = (lab == lv).values
            row: dict[str, Any] = {"cut": cname, "level": str(lv), "n": int(m.sum())}
            for k, c in cols.items():
                v, lo, hi, _ = cl.mean(df[c].values.astype(float), m)
                row[k] = round(v * (100.0 if k.endswith("_pp") else 1.0), 4)
                row[k + "_ci"] = [
                    round(lo * (100.0 if k.endswith("_pp") else 1.0), 4),
                    round(hi * (100.0 if k.endswith("_pp") else 1.0), 4),
                ]
            out.append(row)
    return out


def ewm_windows(db: str) -> pd.DataFrame:
    """Played-only exponentially weighted means of the four stats, windows HL, strictly prior games (seasons <= 2024)."""
    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        d = con.execute(
            "SELECT p.game_id, p.player_id, g.game_date, g.season, p.minutes, p.pts, p.reb, p.ast, p.fg3m "
            f"FROM player_game_stats_played p JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON} AND p.minutes > 0"
        ).df()
    finally:
        con.close()
    assert int(d.season.max()) <= MAX_SEASON
    d = d.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    first = np.r_[True, d.player_id.values[1:] != d.player_id.values[:-1]]
    out = d[["game_id", "player_id", "season", "game_date"]].copy()
    n_prior = np.zeros(len(d))
    for i in range(1, len(d)):
        n_prior[i] = 0 if first[i] else n_prior[i - 1] + 1
    out["n_prior_db"] = n_prior
    for st in STATS:
        x = d[st].values.astype(float)
        for h in HL:
            a = 0.5 ** (1.0 / h)
            num = np.zeros(len(d))
            den = np.zeros(len(d))
            for i in range(1, len(d)):
                if first[i]:
                    continue
                num[i] = x[i - 1] + a * num[i - 1]
                den[i] = 1.0 + a * den[i - 1]
            out[f"{st}_hl{int(h)}"] = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
        out[f"{st}_y"] = x
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    root = a.data_root
    pr = pd.concat(
        [
            pd.read_parquet(
                root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "player_id", "stat", "book", "snapshot_kind", "line"],
            )
            for s in SEASONS
        ]
    )
    dk = pr[(pr.book == "draftkings") & (pr.snapshot_kind == "t60")].drop_duplicates(
        ["game_id", "player_id", "stat"]
    )
    dk_keys = set(zip(dk.game_id.astype(str), dk.player_id.astype(int), dk.stat, strict=True))
    raw = pd.concat(
        [
            pd.read_parquet(
                root / f"reports/odds_history/model_rows_{s}.parquet",
                columns=["game_id", "player_id", "stat", "season", "mean_int"],
            )
            for s in SEASONS
        ],
        ignore_index=True,
    )
    assert int(raw.season.max()) <= MAX_SEASON
    ew = ewm_windows(str(root / "nba.duckdb"))
    res: dict[str, Any] = {
        "seasons": list(SEASONS),
        "boot": {"B": B_BOOT, "seed": SEED, "cluster": "game"},
        "stats": {},
    }
    all_rows: list[pd.DataFrame] = []
    for st in STATS:
        comp = {
            k: v
            for k, v in np.load(
                root / f"reports/lower_tail/components_{st}.npz", allow_pickle=True
            ).items()
        }
        assert int(comp["row_season"].max()) <= MAX_SEASON
        q = row_quantities(comp, st)
        old = raw[raw.stat == st][["game_id", "player_id", "season", "mean_int"]].rename(
            columns={"mean_int": "mean_int_old"}
        )
        jj = q.merge(old, on=["game_id", "player_id", "season"], how="inner")
        d_old = (jj.mean_int - jj.mean_int_old).abs()
        res["stats"][st] = {
            "rows_components": int(len(q)),
            "rows_model_rows_oct10": int(len(old)),
            "matched": int(len(jj)),
            "max_abs_mean_int_diff_2023": float(d_old[jj.season == 2023].max()),
            "share_2024_rows_mean_int_differs_1e-6": float(
                (d_old[jj.season == 2024] > 1e-6).mean()
            ),
        }
        q["hasDK"] = [
            (gi, int(p), st) in dk_keys
            for gi, p in zip(q.game_id.astype(str), q.player_id, strict=True)
        ]
        e = ew[
            [
                "game_id",
                "player_id",
                f"{st}_hl5",
                f"{st}_hl10",
                f"{st}_hl40",
                f"{st}_hl100",
                "n_prior_db",
                "game_date",
            ]
        ].rename(columns={f"{st}_hl{h}": f"h{h}" for h in (5, 10, 40, 100)})
        q = q.merge(e, on=["game_id", "player_id"], how="left")
        res["stats"][st]["hl10_equals_m_recency_max_abs"] = float(
            np.nanmax(np.abs(q.h10.values - q.m_recency.values))
        )
        all_rows.append(q)
    q = pd.concat(all_rows, ignore_index=True)
    cl = Cluster(q.game_id.astype(str).values)
    q["month"] = pd.to_datetime(q.game_date).dt.strftime("%Y-%m")
    q["min10_bucket"] = pd.cut(
        q.min10, [0, 15, 24, 30, 100], right=False, labels=["<15", "15-24", "24-30", "30+"]
    ).astype(str)
    q["starter"] = np.where(q.starter10 >= 0.5, "starter10>=.5", "bench")
    q["nprior_bucket"] = pd.cut(
        q.n_prior, [0, 20, 50, 1000], right=False, labels=["5-19", "20-49", "50+"]
    ).astype(str)
    dev = (q.h5 - q.h40) / np.maximum(q.s, 0.5)
    q["form_dev"] = pd.qcut(dev, 3, labels=["cold(h5<h40)", "flat", "hot(h5>h40)"]).astype(str)
    drift = (q.h10 - q.h100) / np.maximum(q.s, 0.5)
    q["trend_dev"] = pd.qcut(drift, 3, labels=["below long-run", "flat", "above long-run"]).astype(
        str
    )
    q["lvl_in_stat"] = (
        q.groupby("stat")
        .m_recency.transform(lambda x: pd.qcut(x, 3, labels=["low", "mid", "high"]))
        .astype(str)
    )
    q["pop"] = np.where(q.hasDK, "has DK T-60 line", "no DK line")
    q["season_s"] = q.season.astype(str)
    cols = {
        "midmed_pp": "mm",
        "midpit_pp": "mid",
        "mean_res": "res",
        "mean_res_rec": "res_rec",
        "gapMc_pp": "gap_Mc",
        "over_Mc": "over_Mc",
        "impl_Mc": "impl_Mc",
    }
    cuts_all = {
        k: q[k]
        for k in (
            "pop",
            "min10_bucket",
            "starter",
            "nprior_bucket",
            "form_dev",
            "trend_dev",
            "lvl_in_stat",
            "month",
            "season_s",
        )
    }
    res["by_stat"] = {}
    for st in STATS:
        m = q.stat == st
        sub = q[m].reset_index(drop=True)
        cls = Cluster(sub.game_id.astype(str).values)
        res["by_stat"][st] = {
            "all": table(sub, cls, {"all": pd.Series("all", index=sub.index)}, cols),
            "cuts": table(sub, cls, {k: sub[k] for k in cuts_all}, cols),
        }
    res["pooled"] = table(
        q, cl, {"all": pd.Series("all", index=q.index), **cuts_all, "stat": q.stat}, cols
    )
    # cross: population x min10 bucket (is it the DK label or the minutes?)
    q["pop_min"] = q["pop"] + " | " + q["min10_bucket"]
    res["pop_x_min10"] = table(
        q, cl, {"pop_x_min10": q["pop_min"]}, {"midmed_pp": "mm", "mean_res": "res"}
    )
    # mechanism: calibration-window z shape by minutes bucket (needs no test label)
    mech: dict[str, Any] = {}
    for st in STATS:
        comp = {
            k: v
            for k, v in np.load(
                root / f"reports/lower_tail/components_{st}.npz", allow_pickle=True
            ).items()
        }
        z, mn = comp["cal_z"], comp["cal_min10"]
        b = np.searchsorted(np.asarray(MIN_EDGES), mn, side="right")
        mech[st] = {
            "pooled": {
                "n": int(z.size),
                "median_z": round(float(np.median(z)), 4),
                "mean_z": round(float(z.mean()), 4),
            }
        }
        for k, name in enumerate(("<15", "15-25", "25+")):
            zk = z[b == k]
            sk = float(((zk - zk.mean()) ** 3).mean() / zk.std() ** 3)
            mech[st][name] = {
                "n": int(zk.size),
                "median_z": round(float(np.median(zk)), 4),
                "mean_z": round(float(zk.mean()), 4),
                "skew": round(sk, 3),
                "q10": round(float(np.quantile(zk, 0.1)), 4),
                "q90": round(float(np.quantile(zk, 0.9)), 4),
            }
    res["calibration_window_z_by_min10"] = mech
    # where does the DK-population shift sit: centre (mean z), median z, cell mix of the calibration medians
    decomp: dict[str, Any] = {}
    for st in STATS:
        comp = {
            k: v
            for k, v in np.load(
                root / f"reports/lower_tail/components_{st}.npz", allow_pickle=True
            ).items()
        }
        sub = q[q.stat == st].reset_index(drop=True)
        z_obs = ((sub.y - sub.c) / sub.s).values
        zmed_blk = np.array([np.median(comp["cal_z"][comp["cal_blk"] == b]) for b in sub.blk])
        cell = np.searchsorted(np.asarray(MIN_EDGES), sub.min10.values, side="right")
        cellmed = np.empty(len(sub))
        for b in np.unique(sub.blk):
            cm = comp["cal_blk"] == b
            cc = np.searchsorted(np.asarray(MIN_EDGES), comp["cal_min10"][cm], side="right")
            for k in range(3):
                cellmed[(sub.blk.values == b) & (cell == k)] = np.median(comp["cal_z"][cm][cc == k])
        for name, msk in (("dk", sub.hasDK.values), ("no_dk", ~sub.hasDK.values)):
            decomp[f"{st}_{name}"] = {
                "n": int(msk.sum()), "mean_z_obs": round(float(z_obs[msk].mean()), 4), "median_z_obs": round(float(np.median(z_obs[msk])), 4),
                "pooled_cal_median_z": round(float(zmed_blk[msk].mean()), 4), "cell_cal_median_z": round(float(cellmed[msk].mean()), 4),
            }  # fmt: skip
    res["z_decomposition_dk_vs_not"] = decomp
    # A0 gaps in the stratum the MEDIAN_BIAS rule will use (min10 >= 25) and in the DK population, per stat and season (A0 only)
    hs: dict[str, Any] = {}
    for st in STATS:
        sub = q[q.stat == st].reset_index(drop=True)
        cls = Cluster(sub.game_id.astype(str).values)
        for s_ in SEASONS:
            for name, msk in (("H_min10_ge25", (sub.min10 >= 25).values), ("dk", sub.hasDK.values)):
                m = msk & (sub.season == s_).values
                v, lo, hi, n = cls.mean(sub.mm.values, m)
                hs[f"{st}_{s_}_{name}"] = {
                    "n": n,
                    "midmed_pp": round(100 * v, 3),
                    "ci": [round(100 * lo, 3), round(100 * hi, 3)],
                }
    res["a0_gaps_H_and_dk_by_stat_season"] = hs
    # base-recency season trend 2022-2024 (the A0 center is not defined for 2022: warm-up season)
    ewq = ew[(ew.n_prior_db >= 5)]
    trend: dict[str, Any] = {}
    for st in STATS:
        d = ewq[["season", f"{st}_hl10", f"{st}_y"]].dropna()
        for s_, gg in d.groupby("season"):
            y_, m_ = gg[f"{st}_y"].values, gg[f"{st}_hl10"].values
            trend[f"{st}_{int(s_)}"] = {
                "n": int(len(gg)),
                "mean_y_minus_hl10": round(float((y_ - m_).mean()), 4),
                "P(y>hl10)-P(y<hl10)": round(float((y_ > m_).mean() - (y_ < m_).mean()), 4),
            }
    res["recency_base_trend_2022_2024"] = trend
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
