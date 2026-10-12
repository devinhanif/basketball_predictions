# ruff: noqa: E501
"""MARKET_SHRINK harness (rule: docs/prereg/MARKET_SHRINK.md, FROZEN).

    .venv/bin/python -W ignore -m research.eval.market_shrink_eval run --sha <64 hex>

Reads only the two pairs parquets (seasons 2023 and 2024, sha256 re-checked); season 2025 is never loaded; no DuckDB is
opened. Walk-forward monthly fit of (w, T) per stat on DraftKings T-60 legs; arms M, P, B1, B2; log loss, Brier, decision score
(nba.markets.decision_scoring), calibration, planted shuffle. Read-only on markets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.markets.decision_scoring import Boot, american_to_decimal, choose_side, profit_per_unit
from nba.markets.market_slow import diff

DOC = Path("docs/prereg/MARKET_SHRINK.md")
PAIRS_DIR = Path("data/ops/model_clv_pairs")
OUT_DIR = Path("reports/market_shrink")
REPORT_MD = Path("reports/market_shrink.md")
SHA = {
    2023: "050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569",
    2024: "560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c",
}
STATS = ("pts", "reb", "ast", "fg3m")
W_GRID = np.round(np.arange(0.0, 1.0001, 0.05), 2)
T_GRID = (1.0, 1.25, 1.5, 2.0)
CLIP = 0.005
MIN_FIT = 2000
THRESHOLD = 0.05
FLOOR_LL = -0.0010
FLOOR_ROI = 0.01
MIN_BETS = 300
ALPHA = 0.05
COLS = [
    "game_id",
    "stat",
    "book",
    "snapshot_kind",
    "game_date",
    "price_over",
    "price_under",
    "p_over_mult",
    "p_model",
    "over",
    "season",
]


def frozen_sha(doc: Path = DOC) -> str:
    lines = []
    for ln in doc.read_text().splitlines(keepends=True):
        if ln.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        lines.append(ln)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.asarray(np.log(p / (1 - p)))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-z)))


def blend(m: np.ndarray, pm: np.ndarray, w: float, t: float) -> np.ndarray:
    lm = logit(m)
    return np.asarray(np.clip(sigmoid(lm + w * (logit(pm) / t - lm)), CLIP, 1 - CLIP))


def ll_vec(p: np.ndarray, o: np.ndarray) -> np.ndarray:
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.asarray(-(o * np.log(p) + (1 - o) * np.log(1 - p)))


def fit_wt(
    m: np.ndarray, pm: np.ndarray, o: np.ndarray, temps: tuple[float, ...]
) -> tuple[float, float]:
    """Grid search of (w, T) by mean binary log loss on the fit window (ties: smaller T, then smaller w)."""
    lm, lp = logit(m), logit(pm)
    best = (np.inf, 0.0, 1.0)
    for t in temps:
        z = lp / t
        for w in W_GRID:
            p = sigmoid(lm + w * (z - lm))
            ll = float(ll_vec(p, o).mean())
            if ll < best[0] - 1e-12:
                best = (ll, float(w), float(t))
    return best[1], best[2]


def load_frames(pairs_dir: Path, book: str) -> pl.DataFrame:
    frames = []
    for s, sha in SHA.items():
        f = pairs_dir / f"pairs_{s}.parquet"
        assert hashlib.sha256(f.read_bytes()).hexdigest() == sha, (
            f"pairs_{s} sha256 differs from the frozen one"
        )
        d = pl.read_parquet(f, columns=COLS)
        assert int(d["season"].max()) <= 2024  # type: ignore[arg-type]
        frames.append(d)
    d = pl.concat(frames)
    return (
        d.filter(
            (pl.col("book") == book)
            & (pl.col("snapshot_kind") == "t60")
            & pl.col("price_over").is_not_null()
            & pl.col("price_under").is_not_null()
            & pl.col("p_model").is_not_null()
            & pl.col("over").is_not_null()
            & pl.col("p_over_mult").is_not_null()
        )
        .with_columns(pl.col("game_date").dt.strftime("%Y-%m").alias("month"))
        .sort(["stat", "game_date", "game_id"])
    )


def walk_forward(
    month: np.ndarray, m: np.ndarray, pm: np.ndarray, o: np.ndarray
) -> tuple[np.ndarray, dict[str, np.ndarray], list[dict[str, Any]]]:
    """Per month X: fit on legs of months < X, score month X. Returns scored mask, arm probabilities, parameter log."""
    n = m.size
    scored = np.zeros(n, dtype=bool)
    arms = {
        "M": np.clip(m, CLIP, 1 - CLIP),
        "P": np.clip(pm, CLIP, 1 - CLIP),
        "B1": np.full(n, np.nan),
        "B2": np.full(n, np.nan),
    }
    log: list[dict[str, Any]] = []
    for x in sorted(set(month.tolist())):
        tgt = month == x
        fit = month < x
        if int(fit.sum()) < MIN_FIT:
            log.append({"month": x, "n_fit": int(fit.sum()), "skipped": True})
            continue
        w1, _ = fit_wt(m[fit], pm[fit], o[fit], (1.0,))
        w2, t2 = fit_wt(m[fit], pm[fit], o[fit], T_GRID)
        arms["B1"][tgt] = blend(m[tgt], pm[tgt], w1, 1.0)
        arms["B2"][tgt] = blend(m[tgt], pm[tgt], w2, t2)
        scored |= tgt
        log.append(
            {
                "month": x,
                "n_fit": int(fit.sum()),
                "n_scored": int(tgt.sum()),
                "w_B1": w1,
                "w_B2": w2,
                "T_B2": t2,
                "skipped": False,
            }
        )
    return scored, arms, log


def decision(
    boot: Boot,
    mask: np.ndarray,
    p: np.ndarray,
    po: np.ndarray,
    pu: np.ndarray,
    went: np.ndarray,
    mult: np.ndarray,
    thr: float,
) -> dict[str, Any]:
    over, ev, _, dec = choose_side(p, po, pu)
    profit = profit_per_unit(over, went, dec)
    sel = mask & (ev >= thr)
    n = int(sel.sum())
    out: dict[str, Any] = {"threshold": thr, "n": n}
    if n == 0:
        return out
    s = sel.astype(float)
    r = boot.ratio(profit * s, s)
    h = boot.ratio((over == (went > 0.5)).astype(float) * s, s)
    out.update(
        roi=r["est"],
        roi_lo=r["lo"],
        roi_hi=r["hi"],
        hit=h["est"],
        mean_decimal=float(dec[sel].mean()),
    )
    # market lean, same count among the scored legs: most lopsided de-vigged price, favoured side
    idx = np.flatnonzero(mask)
    top = idx[np.argsort(-np.abs(mult[idx] - 0.5), kind="stable")[:n]]
    ml = np.zeros(mask.size, dtype=bool)
    ml[top] = True
    mk_over = mult >= 0.5
    dec_ml = np.where(mk_over, american_to_decimal(po), american_to_decimal(pu))
    pr_ml = profit_per_unit(mk_over, went, dec_ml)
    mls = ml.astype(float)
    rm = boot.ratio(pr_ml * mls, mls)
    out.update(ml_roi=rm["est"], ml_lo=rm["lo"], ml_hi=rm["hi"])
    out["roi_minus_ml"] = diff(boot, profit * s, s, pr_ml * mls, mls)
    return out


def calibration(p: np.ndarray, o: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    pm, om = p[mask], o[mask]
    edges = np.quantile(pm, np.linspace(0, 1, 11))
    b = np.clip(np.searchsorted(edges, pm, side="right") - 1, 0, 9)
    rows = []
    ece = 0.0
    for k in range(10):
        s = b == k
        if s.any():
            rows.append(
                {
                    "decile": k,
                    "n": int(s.sum()),
                    "stated": float(pm[s].mean()),
                    "realised": float(om[s].mean()),
                }
            )
            ece += s.mean() * abs(pm[s].mean() - om[s].mean())
    return {"ece": float(ece), "deciles": rows}


def run_book(d: pl.DataFrame, shuffle: bool = False) -> dict[str, Any]:
    games = sorted(d["game_id"].unique().to_list())
    gmap = {g: i for i, g in enumerate(games)}
    boot = Boot(np.array([gmap[g] for g in d["game_id"].to_list()], dtype=np.int64), len(games))

    def f(c: str) -> np.ndarray:
        return d[c].cast(pl.Float64).to_numpy()

    stat_a, season_a, month_a = d["stat"].to_numpy(), d["season"].to_numpy(), d["month"].to_numpy()
    m_a, pm_a, o_a, po_a, pu_a = (
        f("p_over_mult"),
        f("p_model"),
        f("over"),
        f("price_over"),
        f("price_under"),
    )
    if shuffle:
        rng = np.random.default_rng(0)
        grp = d.select(pl.concat_str(["game_id", "stat"], separator="|")).to_series().to_numpy()
        key = np.unique(grp, return_inverse=True)[1]
        base = np.lexsort((np.arange(grp.size), key))
        sh = np.lexsort((rng.random(grp.size), key))
        pm2 = np.empty_like(pm_a)
        pm2[base] = pm_a[sh]
        pm_a = pm2
    res: dict[str, Any] = {"stats": {}, "n_legs": d.height, "n_games": len(games)}
    for st in STATS:
        sm = stat_a == st
        ix = np.flatnonzero(sm)
        scored, arms, plog = walk_forward(month_a[ix], m_a[ix], pm_a[ix], o_a[ix])
        full = {k: np.full(d.height, np.nan) for k in arms}
        for k in arms:
            full[k][ix] = arms[k]
        sc = np.zeros(d.height, dtype=bool)
        sc[ix] = scored
        cell: dict[str, Any] = {"param_log": plog, "seasons": {}}
        for s in (2023, 2024):
            mk = sc & (season_a == s)
            n = int(mk.sum())
            rec: dict[str, Any] = {"n_scored": n, "n_games": int(np.unique(boot.gi[mk]).size)}
            if n == 0:
                cell["seasons"][str(s)] = rec
                continue
            mf = mk.astype(float)
            llM, brM = ll_vec(full["M"], o_a), (full["M"] - o_a) ** 2
            rec["mean_ll"] = {k: float(ll_vec(full[k], o_a)[mk].mean()) for k in arms}
            for k in ("P", "B1", "B2"):
                dl = np.where(mk, ll_vec(full[k], o_a) - llM, 0.0)
                db = np.where(mk, (full[k] - o_a) ** 2 - brM, 0.0)
                r = boot.ratio(dl * mf, mf)
                b = boot.ratio(db * mf, mf)
                # one-sided p for H0 delta >= 0 from the resample distribution
                ng = boot.per_game(dl * mf)
                dg = boot.per_game(mf)
                bs = (boot.W @ ng) / (boot.W @ dg)
                rec[f"dll_{k}"] = {**r, "p_one_sided": float((1 + np.sum(bs >= 0)) / (len(bs) + 1))}
                rec[f"dbrier_{k}"] = b
            bb = np.where(mk, ll_vec(full["B2"], o_a) - ll_vec(full["B1"], o_a), 0.0)
            rec["dll_B2_minus_B1"] = boot.ratio(bb * mf, mf)
            rec["decision"] = {
                k: [
                    decision(boot, mk, full[k], po_a, pu_a, o_a, m_a, t)
                    for t in ((0.0, 0.02, THRESHOLD, 0.10))
                ]
                for k in ("P", "B1", "B2")
            }
            rec["calibration"] = {k: calibration(full[k], o_a, mk) for k in ("M", "P", "B2")}
            cell["seasons"][str(s)] = rec
        res["stats"][st] = cell
    return res


def holm(ps: dict[str, float]) -> dict[str, float]:
    items = sorted(ps.items(), key=lambda kv: kv[1])
    out: dict[str, float] = {}
    run = 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (len(items) - i) * p))
        out[k] = run
    return out


def dec_at(rec: dict[str, Any], arm: str, thr: float) -> dict[str, Any]:
    return next(x for x in rec["decision"][arm] if abs(x["threshold"] - thr) < 1e-9)


def verdicts(res: dict[str, Any]) -> dict[str, Any]:
    """Pass condition 1-4 per stat (condition 5, the shuffle, is applied by the caller)."""
    p24 = {st: res["stats"][st]["seasons"]["2024"]["dll_B2"]["p_one_sided"] for st in STATS}
    hp = holm(p24)
    out: dict[str, Any] = {}
    for st in STATS:
        r24, r23 = res["stats"][st]["seasons"]["2024"], res["stats"][st]["seasons"]["2023"]
        d = r24["dll_B2"]
        c1 = bool(d["est"] <= FLOOR_LL and d["hi"] < 0 and hp[st] < ALPHA)
        b = dec_at(r24, "B2", THRESHOLD)
        pp = dec_at(r24, "P", THRESHOLD)
        enough = b["n"] >= MIN_BETS
        c2 = bool(
            enough
            and b.get("roi", -1) >= FLOOR_ROI
            and b.get("roi_lo", -1) > 0
            and b.get("roi_minus_ml", {}).get("lo", -1) > 0
        )
        c3 = bool(b.get("roi", -9) >= pp.get("roi", 9)) if pp["n"] else bool(enough)
        b23 = dec_at(r23, "B2", THRESHOLD)
        c4 = bool(r23["dll_B2"]["est"] < 0 and b23.get("roi", -1) >= 0)
        if not c1:
            v = "fails_logloss"
        elif not c2:
            v = "fails_decision_guard" if enough else "insufficient_sample"
        elif not c3:
            v = "no_gain_over_P"
        elif not c4:
            v = "not_in_both_seasons"
        else:
            v = "passes"
        out[st] = {
            "verdict": v,
            "c1": c1,
            "c2": c2,
            "c3": c3,
            "c4": c4,
            "holm_p_logloss": hp[st],
            "n_B2_bets_2024": b["n"],
        }
    return out


def run(pairs_dir: Path, sha: str, doc: Path = DOC, out: Path = OUT_DIR) -> dict[str, Any]:
    assert sha == frozen_sha(doc), "sha does not match the frozen block"
    assert "Status: FROZEN" in doc.read_text()
    real = run_book(load_frames(pairs_dir, "draftkings"))
    shuf = run_book(load_frames(pairs_dir, "draftkings"), shuffle=True)
    pin = run_book(load_frames(pairs_dir, "pinnacle"))
    v = verdicts(real)
    vs = verdicts(shuf)
    shuffle_ok = all(vs[s]["verdict"] != "passes" for s in STATS) and all(
        shuf["stats"][s]["seasons"]["2024"]["dll_B2"]["est"] >= 0 for s in STATS
    )
    for s in STATS:
        if v[s]["verdict"] == "passes" and not shuffle_ok:
            v[s]["verdict"] = "voided_by_shuffle"
    n_pass = sum(1 for s in STATS if v[s]["verdict"] == "passes")
    res = {
        "rule": "MARKET_SHRINK", "doc_sha256": sha, "draftkings": real, "shuffle": {"verdicts": vs, "ok": shuffle_ok, "dll_B2_2024": {s: shuf["stats"][s]["seasons"]["2024"]["dll_B2"] for s in STATS}},
        "pinnacle_descriptive": pin, "verdicts": v,
        "overall": f"passes_in_{n_pass}_cells" if n_pass else "no_positive_ev_found",
    }  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def fmt(x: float | None, nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}"


def render(res: dict[str, Any]) -> str:
    L = [f"# MARKET_SHRINK results (rule frozen sha256 {res['doc_sha256'][:8]})", "", f"Overall: **{res['overall']}**; planted shuffle ok: {res['shuffle']['ok']}", "",
         "## DraftKings, season 2024 (report), walk-forward", "",
         "| stat | verdict | n scored | dLL B2-M [95% CI] | dLL B1-M | dLL P-M | B2 ROI@5% (n) [CI] | B2 minus market-lean | P ROI@5% (n) |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for s in STATS:
        r = res["draftkings"]["stats"][s]["seasons"]["2024"]
        b, p = dec_at(r, "B2", THRESHOLD), dec_at(r, "P", THRESHOLD)

        def ci(d: dict[str, Any]) -> str:
            return f"{fmt(d['est'])} [{fmt(d['lo'])}, {fmt(d['hi'])}]"

        bs = (
            f"{100 * b['roi']:+.2f}% ({b['n']}) [{100 * b['roi_lo']:+.2f}%, {100 * b['roi_hi']:+.2f}%]"
            if b["n"]
            else f"n/a ({b['n']})"
        )
        mm = b["roi_minus_ml"]
        ml = (
            f"{100 * mm['est']:+.2f}% [{100 * mm['lo']:+.2f}%, {100 * mm['hi']:+.2f}%]"
            if b["n"] and mm.get("est") is not None
            else "n/a"
        )
        ps = f"{100 * p['roi']:+.2f}% ({p['n']})" if p["n"] else "n/a"
        L.append(
            f"| {s} | {res['verdicts'][s]['verdict']} | {r['n_scored']} | {ci(r['dll_B2'])} | {ci(r['dll_B1'])} | {ci(r['dll_P'])} | {bs} | {ml} | {ps} |"
        )
    L += [
        "",
        "## Season 2023 (select, walk-forward)",
        "",
        "| stat | n scored | dLL B2-M [CI] | B2 ROI@5% (n) |",
        "|---|---|---|---|",
    ]
    for s in STATS:
        r = res["draftkings"]["stats"][s]["seasons"]["2023"]
        b = dec_at(r, "B2", THRESHOLD)
        d = r["dll_B2"]
        L.append(
            f"| {s} | {r['n_scored']} | {fmt(d['est'])} [{fmt(d['lo'])}, {fmt(d['hi'])}] | "
            + (f"{100 * b['roi']:+.2f}% ({b['n']})" if b["n"] else "n/a")
            + " |"
        )
    L += ["", "## Planted shuffle (2024 dLL B2-M, must be >= 0)", ""]
    for s in STATS:
        d = res["shuffle"]["dll_B2_2024"][s]
        L.append(
            f"- {s}: {fmt(d['est'])} [{fmt(d['lo'])}, {fmt(d['hi'])}], verdict {res['shuffle']['verdicts'][s]['verdict']}"
        )
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--sha", required=True)
    ap.add_argument("--pairs-dir", type=Path, default=PAIRS_DIR)
    a = ap.parse_args(argv)
    res = run(a.pairs_dir, a.sha)
    REPORT_MD.write_text(render(res))
    print(res["overall"], {s: res["verdicts"][s]["verdict"] for s in STATS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
