# ruff: noqa: E501, E731
# mypy: ignore-errors
"""SCRATCH (not a rule, not a claim): descriptive P(over) gap, seasons 2023-24 only.

    .venv/bin/python -m research.scratch.pover_bias_scratch --out reports/pover_bias_scratch_2026-10-15.json

Reads data/ops/model_clv_pairs/pairs_{2023,2024}.parquet and reports/odds_history/model_rows_{2023,2024}.parquet.
Season 2025 is never loaded (hard assert). No DuckDB. Gap = mean(model P(over) - realised over), pp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval.model_clv_eval import Boot

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")


def load(root: Path) -> pl.DataFrame:
    parts = []
    for s in SEASONS:
        assert s != 2025
        pr = pl.read_parquet(root / f"data/ops/model_clv_pairs/pairs_{s}.parquet").filter(
            pl.col("book").is_in(["draftkings", "pinnacle"]) & ~pl.col("_dnp")
        )
        mr = pl.read_parquet(root / f"reports/odds_history/model_rows_{s}.parquet").select(
            "game_id", "player_id", "stat", "minutes", "min10", "p_ge"
        )
        parts.append(pr.join(mr, on=["game_id", "player_id", "stat"], how="inner"))
    d = pl.concat(parts)
    assert d["season"].max() <= 2024
    return d


def surv(p_ge: list[list[float]], k: np.ndarray, shift: float = 0.0) -> np.ndarray:
    """P(X > k - shift) with linear interpolation of the integer survival vector (shift=0 -> exact)."""
    out = np.empty(len(k))
    for i, (v, kk) in enumerate(zip(p_ge, k, strict=True)):
        x = kk - shift
        lo = int(np.floor(x))
        a = v[lo] if 0 <= lo < len(v) else (1.0 if lo < 0 else 0.0)
        b = v[lo + 1] if 0 <= lo + 1 < len(v) else (1.0 if lo + 1 < 0 else 0.0)
        out[i] = a + (x - lo) * (b - a)
    return out


def recal(d: pl.DataFrame) -> dict[str, Any]:
    """Descriptive one split: logit recalibration fit on 2023 DK T-60, scored on 2024 (paired log loss)."""

    def lg(p: np.ndarray) -> np.ndarray:
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))

    def ll(p: np.ndarray, o: np.ndarray) -> np.ndarray:
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return -(o * np.log(p) + (1 - o) * np.log(1 - p))

    def fit(x: np.ndarray, o: np.ndarray) -> np.ndarray:
        X = np.c_[np.ones(len(x)), x]
        b = np.array([0.0, 1.0])
        for _ in range(50):
            p = 1 / (1 + np.exp(-X @ b))
            w = p * (1 - p)
            b = b + np.linalg.solve(X.T @ (X * w[:, None]) + 1e-9 * np.eye(2), X.T @ (o - p))
        return b

    d = d.filter((pl.col("book") == "draftkings") & (pl.col("snapshot_kind") == "t60"))
    tr, te = d.filter(pl.col("season") == 2023), d.filter(pl.col("season") == 2024)
    _, gi = np.unique(te["game_id"].to_numpy(), return_inverse=True)
    bs = Boot(gi, int(gi.max()) + 1)
    o, pm, mk, st = (te[c].to_numpy() for c in ("over", "p_model", "p_over_mult", "stat"))
    out: dict[str, Any] = {}
    for s in ("all", *STATS):
        mt = (tr["stat"].to_numpy() == s) if s != "all" else np.ones(len(tr), bool)
        b = fit(lg(tr["p_model"].to_numpy()[mt]), tr["over"].to_numpy()[mt])
        m = (st == s) if s != "all" else np.ones(len(te), bool)
        pc = 1 / (1 + np.exp(-(b[0] + b[1] * lg(pm))))
        r: dict[str, Any] = {"intercept": float(b[0]), "slope": float(b[1])}
        for nm, x1, x2 in (
            ("cal_minus_raw", ll(pc, o), ll(pm, o)),
            ("mkt_minus_raw", ll(mk, o), ll(pm, o)),
            ("cal_minus_mkt", ll(pc, o), ll(mk, o)),
        ):
            g = bs.mean(x1 - x2, m)
            r[nm] = {q: g[q] for q in ("mean", "lo", "hi", "n")}
        g = bs.mean(pc - o, m)
        r["cal_gap_pp"] = 100 * g["mean"]
        out[s] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    d = load(Path(a.root))
    line = d["line"].to_numpy()
    y = d["y"].to_numpy()
    k = np.floor(line).astype(int)  # p_ge[j] = P(X > j); x.5 line: over <=> X > floor(line)
    over = (y > k).astype(float)
    assert np.array_equal(over, d["over"].to_numpy()), "over label mismatch"
    p_grid = surv(d["p_ge"].to_list(), k.astype(float))
    pm = d["p_model"].to_numpy()
    res: dict[str, Any] = {
        "n_rows": int(len(d)),
        "recompute_max_abs_diff_p_model_vs_grid": float(np.abs(p_grid - pm).max()),
        "pushes": int((y == line).sum()),
        "frac_line_not_half": float((line % 1 != 0.5).mean()),
    }
    ok = np.abs(line % 1 - 0.5) < 1e-9
    gid = d["game_id"].to_numpy()
    _, gi = np.unique(gid, return_inverse=True)
    bs = Boot(gi, int(gi.max()) + 1)
    book = d["book"].to_numpy()
    snap = d["snapshot_kind"].to_numpy()
    stat = d["stat"].to_numpy()
    st10 = d["starter10"].to_numpy()
    min10 = d["min10"].to_numpy()
    mins = d["minutes"].to_numpy()
    gap = pm - over
    mkt = d["p_over_mult"].to_numpy() - over
    base = ok

    def cell(m: np.ndarray) -> dict[str, Any]:
        g = bs.mean(gap, m & base)
        mk = bs.mean(mkt, m & base)
        r = bs.mean(over, m & base)
        f = lambda x: (
            None if x["mean"] is None else [round(100 * x[q], 2) for q in ("mean", "lo", "hi")]
        )  # noqa: E731
        return {"n": g["n"], "gap_pp": f(g), "mkt_gap_pp": f(mk), "over_rate_pp": f(r)}

    # tertiles of line within stat (line level)
    lvl = np.full(len(d), "", dtype=object)
    for s in STATS:
        ms = stat == s
        q1, q2 = np.quantile(line[ms], [1 / 3, 2 / 3])
        lvl[ms & (line <= q1)] = "low"
        lvl[ms & (line > q1) & (line <= q2)] = "mid"
        lvl[ms & (line > q2)] = "high"
    cuts = {s: [float(x) for x in np.quantile(line[stat == s], [1 / 3, 2 / 3])] for s in STATS}
    res["line_tertile_cuts"] = cuts
    out: dict[str, Any] = {}
    for b in ("draftkings", "pinnacle"):
        for sn in ("t60", "t5"):
            mb = (book == b) & (snap == sn)
            out[f"{b}/{sn}/all"] = cell(mb)
            for s in STATS:
                out[f"{b}/{sn}/stat={s}"] = cell(mb & (stat == s))
            if sn == "t60":
                for L in ("low", "mid", "high"):
                    for s in STATS:
                        out[f"{b}/{sn}/{s}/line={L}"] = cell(mb & (stat == s) & (lvl == L))
                for nm, m in (("starter", st10 >= 0.5), ("bench", st10 < 0.5)):
                    out[f"{b}/{sn}/role={nm}"] = cell(mb & m)
                for nm, lo, hi in (
                    ("min10<15", 0, 15),
                    ("15-24", 15, 24),
                    ("24-30", 24, 30),
                    ("30+", 30, 99),
                ):
                    out[f"{b}/{sn}/pregame_min10={nm}"] = cell(mb & (min10 >= lo) & (min10 < hi))
                # outcome-conditioned (diagnostic only; selects on the label)
                for nm, lo, hi in (
                    ("<12", 0, 12),
                    ("12-24", 12, 24),
                    ("24-32", 24, 32),
                    ("32+", 32, 99),
                ):
                    out[f"{b}/{sn}/ACTUAL_minutes={nm}(outcome-cond)"] = cell(
                        mb & (mins >= lo) & (mins < hi)
                    )
    res["cells"] = out

    # mean shift vs shape: one constant shift (stat units) per stat on DK t60 so mean gap = 0, then residual by line level
    mb = (book == "draftkings") & (snap == "t60") & base
    shifts: dict[str, Any] = {}
    for s in STATS:
        ms = mb & (stat == s)
        idx = np.where(ms)[0]
        pg = [d["p_ge"][int(i)] for i in idx]
        kk = k[idx].astype(float)
        tgt = over[idx].mean()
        lo_, hi_ = -3.0, 3.0
        for _ in range(30):
            mid = 0.5 * (lo_ + hi_)
            if surv(pg, kk, mid).mean() < tgt:
                lo_ = mid
            else:
                hi_ = mid
        sh = 0.5 * (lo_ + hi_)
        pshift = np.full(len(d), np.nan)
        pshift[idx] = surv(pg, kk, sh)
        r = {
            "shift_units": round(sh, 3),
            "mean_line": round(float(line[idx].mean()), 2),
            "by_level": {},
        }
        for L in ("low", "mid", "high"):
            m = ms & (lvl == L)
            g = bs.mean(pshift - over, m)
            r["by_level"][L] = {
                "n": g["n"],
                "resid_gap_pp": [round(100 * g[q], 2) for q in ("mean", "lo", "hi")],
            }
        for nm, m in (("starter", st10 >= 0.5), ("bench", st10 < 0.5)):
            g = bs.mean(pshift - over, ms & m)
            r["by_level"][nm] = {
                "n": g["n"],
                "resid_gap_pp": [round(100 * g[q], 2) for q in ("mean", "lo", "hi")],
            }
        shifts[s] = r
    res["const_shift_dk_t60"] = shifts

    # location of the production mean: mean_int - y (unique player-game-stat, DK t60)
    u = (book == "draftkings") & (snap == "t60")
    mi = d["mean_int"].to_numpy()
    sd = d["std_int"].to_numpy()
    loc: dict[str, Any] = {}
    for s in STATS:
        m = u & (stat == s)
        b1 = bs.mean(mi - y, m)
        b2 = bs.mean((mi - y) / np.maximum(sd, 1e-6), m)
        # share of mass below/above: realised y < mean_int vs y > mean_int (skew indicator)
        b3 = bs.mean((y < mi).astype(float), m)
        loc[s] = {
            "n": b1["n"],
            "mean_int_minus_y": [round(b1[q], 3) for q in ("mean", "lo", "hi")],
            "z_bias": [round(b2[q], 3) for q in ("mean", "lo", "hi")],
            "frac_y_below_mean_int": [round(b3[q], 3) for q in ("mean", "lo", "hi")],
        }
    res["location_dk_t60"] = loc

    # line vs model location: where is the book's line relative to the model's mean and median
    rel: dict[str, Any] = {}
    for s in STATS:
        m = u & (stat == s)
        rel[s] = {
            "mean_line_minus_mean_int": round(float((line - mi)[m].mean()), 3),
            "mean_line_minus_y": round(float((line - y)[m].mean()), 3),
            "median_y": float(np.median(y[m])),
            "mean_y": round(float(y[m].mean()), 3),
        }
    res["line_vs_model_dk_t60"] = rel

    # reliability of p_model by bins (DK t60)
    rb = []
    for lo, hi in (
        (0, 0.2),
        (0.2, 0.3),
        (0.3, 0.4),
        (0.4, 0.5),
        (0.5, 0.6),
        (0.6, 0.8),
        (0.8, 1.01),
    ):
        m = u & base & (pm >= lo) & (pm < hi)
        g = bs.mean(gap, m)
        rb.append(
            {
                "bin": [lo, hi],
                "n": g["n"],
                "mean_p": round(float(pm[m].mean()), 4) if m.any() else None,
                "gap_pp": None
                if g["mean"] is None
                else [round(100 * g[q], 2) for q in ("mean", "lo", "hi")],
            }
        )
    res["reliability_dk_t60"] = rb
    res["recal_2023_to_2024_dk_t60"] = recal(d)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(
        json.dumps(
            {
                k_: res[k_]
                for k_ in (
                    "n_rows",
                    "recompute_max_abs_diff_p_model_vs_grid",
                    "pushes",
                    "frac_line_not_half",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
