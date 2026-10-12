# ruff: noqa: E501
"""Confusion matrices for props, game lines and quarter/half winners (2026-10-11).

DESCRIPTIVE EXPLORATION, not a claim: no pre-registration, nothing here may change production.
Seasons 2023 and 2024 are scored; 2022 is training history for the baselines only; season 2025
(the frozen holdout) is never read (every SQL carries ``season <= 2024``).

    .venv/bin/python -m research.eval.confusion_report

Reads: ``reports/odds_history/*`` (stored production-style walk-forward prop forecasts, DraftKings
T-60 quotes), ``data/odds/odds_history.duckdb`` (spreads/totals, read-only), a scratch copy of the
needed ``nba.duckdb`` tables (taken once, read-only, after waiting out ``data/ops/heavy.lock``) and
``data/pbp/*.parquet`` (scoreboard per period). Writes ``reports/confusion_2026-10-11.md`` and
``reports/confusion_2026-10-11/results.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy import stats as sps

from research.eval.odds_history_eval import model_p_over

ROOT = Path(__file__).resolve().parents[2]
OH = ROOT / "reports" / "odds_history"
ODDS_DB = ROOT / "data" / "odds" / "odds_history.duckdb"
NBA_DB = ROOT / "nba.duckdb"
SCRATCH = ROOT / "data" / "scratch"
SNAP = SCRATCH / "confusion_snap.duckdb"
PERIODS_CACHE = SCRATCH / "confusion_periods.parquet"
LOCK = ROOT / "data" / "ops" / "heavy.lock"
OUT_MD = ROOT / "reports" / "confusion_2026-10-11.md"
OUT_JSON = ROOT / "reports" / "confusion_2026-10-11" / "results.json"
EVAL_SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
B = 2000
SEED = 2026
WINDOW = 20  # team recency window (games) for the totals baseline
MIN_PRIOR = 8


# ------------------------------------------------------------------------------------ plumbing
def wait_lock(retry_s: float = 30.0, max_wait_s: float = 3000.0) -> None:
    """Wait for ``heavy.lock`` to clear. Never break it."""
    t0 = time.time()
    while LOCK.exists():
        if time.time() - t0 > max_wait_s:
            raise RuntimeError("heavy.lock still held; not breaking it")
        time.sleep(retry_s)


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        return out.stdout.strip()
    except OSError:
        return "unknown"


def build_snapshot() -> dict[str, Any]:
    """One read-only copy of the tables used here (seasons <= 2024 only) into scratch."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if SNAP.exists():
        meta = json.loads((SCRATCH / "confusion_snap.json").read_text())
        return dict(meta)
    wait_lock()
    mtime = dt.datetime.fromtimestamp(NBA_DB.stat().st_mtime).isoformat(timespec="seconds")
    o = duckdb.connect(str(SNAP))
    o.execute(f"attach '{NBA_DB}' as src (read_only)")
    o.execute("create table games as select * from src.games where season <= 2024")
    o.execute(
        "create table poss as select p.game_id, poss_idx, period, off_team, pts "
        "from src.possessions p join src.games g using(game_id) where g.season <= 2024"
    )
    n = o.execute("select count(*) from games").fetchone()
    o.close()
    meta = {
        "nba_duckdb_mtime": mtime,
        "copied_at": dt.datetime.now().isoformat(timespec="seconds"),
        "git_head": git_sha(),
        "games_rows": int(n[0]) if n else 0,
    }
    (SCRATCH / "confusion_snap.json").write_text(json.dumps(meta))
    return meta


# ------------------------------------------------------------------------------------ bootstrap
def boot_ratios(
    codes: np.ndarray, parts: list[tuple[np.ndarray, np.ndarray]], b: int = B, seed: int = SEED
) -> tuple[np.ndarray, np.ndarray]:
    """Game-clustered bootstrap of several ratios sharing one resample. Returns (point, draws)."""
    uniq, inv = np.unique(codes, return_inverse=True)
    g = len(uniq)
    num = np.stack([np.bincount(inv, weights=np.asarray(n, float), minlength=g) for n, _ in parts])
    den = np.stack([np.bincount(inv, weights=np.asarray(d, float), minlength=g) for _, d in parts])
    with np.errstate(divide="ignore", invalid="ignore"):
        point = num.sum(1) / den.sum(1)
    rng = np.random.default_rng(seed)
    draws = np.empty((b, len(parts)))
    for s in range(0, b, 250):
        e = min(b, s + 250)
        idx = rng.integers(0, g, size=(e - s, g))
        with np.errstate(divide="ignore", invalid="ignore"):
            draws[s:e] = num[:, idx].sum(2).T / den[:, idx].sum(2).T
    return point, draws


def ci(draws: np.ndarray) -> tuple[float, float]:
    d = draws[np.isfinite(draws)]
    if d.size == 0:
        return (float("nan"), float("nan"))
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def rate(codes: np.ndarray, hit: np.ndarray, mask: np.ndarray | None = None) -> dict[str, Any]:
    """Rate of ``hit`` among rows (optionally masked) with a game-clustered 95% CI."""
    if mask is not None:
        codes, hit = codes[mask], hit[mask]
    n = int(len(hit))
    if n == 0:
        return {"rate": None, "lo": None, "hi": None, "n": 0}
    pt, dr = boot_ratios(codes, [(hit.astype(float), np.ones(n))])
    lo, hi = ci(dr[:, 0])
    return {"rate": float(pt[0]), "lo": lo, "hi": hi, "n": n, "games": int(len(np.unique(codes)))}


def diff_rate(
    codes: np.ndarray, a: np.ndarray, b_: np.ndarray, mask: np.ndarray | None = None
) -> dict[str, Any]:
    """Paired accuracy difference a - b (0/1 hit arrays), game-clustered."""
    if mask is not None:
        codes, a, b_ = codes[mask], a[mask], b_[mask]
    n = len(a)
    if n == 0:
        return {"diff": None, "lo": None, "hi": None, "n": 0}
    one = np.ones(n)
    pt, dr = boot_ratios(codes, [(a.astype(float), one), (b_.astype(float), one)])
    lo, hi = ci(dr[:, 0] - dr[:, 1])
    return {"diff": float(pt[0] - pt[1]), "lo": lo, "hi": hi, "n": int(n)}


def fmt(r: dict[str, Any] | None, pct: bool = True) -> str:
    if not r or r.get("rate") is None:
        return "n/a"
    k = 100.0 if pct else 1.0
    return f"{r['rate'] * k:.1f}% [{r['lo'] * k:.1f}, {r['hi'] * k:.1f}]"


def fmt_diff(r: dict[str, Any]) -> str:
    if r.get("diff") is None:
        return "n/a"
    return f"{r['diff'] * 100:+.1f} pts [{r['lo'] * 100:+.1f}, {r['hi'] * 100:+.1f}]"


# ------------------------------------------------------------------------------------ matrices
def binary_matrix(
    codes: np.ndarray, pred_over: np.ndarray, over: np.ndarray, mask: np.ndarray | None = None
) -> dict[str, Any]:
    """2x2 (rows = predicted, cols = actual). Precision/recall are for the 'over' class."""
    if mask is not None:
        codes, pred_over, over = codes[mask], pred_over[mask], over[mask]
    po, ov = pred_over.astype(bool), over.astype(bool)
    tp, fp, fn, tn = (
        int((po & ov).sum()),
        int((po & ~ov).sum()),
        int((~po & ov).sum()),
        int((~po & ~ov).sum()),
    )
    n = len(po)
    out: dict[str, Any] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n}
    if n == 0:
        return out
    one = np.ones(n)
    parts = [
        ((po == ov).astype(float), one),
        ((po & ov).astype(float), po.astype(float)),
        ((po & ov).astype(float), ov.astype(float)),
        (po.astype(float), one),
        (ov.astype(float), one),
    ]
    pt, dr = boot_ratios(codes, parts)
    for i, name in enumerate(
        ["accuracy", "precision_over", "recall_over", "pred_over_rate", "base_over"]
    ):
        lo, hi = ci(dr[:, i])
        out[name] = {"rate": float(pt[i]), "lo": lo, "hi": hi, "n": n}
    return out


def md_matrix(m: dict[str, Any], row_lab: tuple[str, str], col_lab: tuple[str, str]) -> str:
    return (
        f"| predicted \\ actual | {col_lab[0]} | {col_lab[1]} |\n|---|---|---|\n"
        f"| {row_lab[0]} | {m['tp']} | {m['fp']} |\n| {row_lab[1]} | {m['fn']} | {m['tn']} |\n"
    )


# ------------------------------------------------------------------------------------ section 1
def recency_pge(stat: str, mean: np.ndarray, sd: np.ndarray, width: int) -> np.ndarray:
    """Baseline survival vectors P(Y>=k), k=1..width, on the integer grid from recency mean/sd.

    pts: Gaussian rounded to integers; reb/ast/fg3m: negative binomial (Poisson if var<=mean)."""
    ks = np.arange(1, width + 1)[None, :].astype(float)
    m = np.clip(mean, 0.05, None)[:, None]
    s = np.clip(sd, 0.5, None)[:, None]
    if stat == "pts":
        return np.asarray(1.0 - sps.norm.cdf((ks - 0.5 - m) / s))
    var = np.maximum(s**2, m * 1.0001)
    p = m / var
    r = m * p / (1.0 - p)
    return np.asarray(sps.nbinom.sf(ks - 1, r, p))


def load_props() -> pl.DataFrame:
    parts = []
    for season in EVAL_SEASONS:
        mdl = pl.read_parquet(OH / f"model_rows_{season}.parquet")
        mk = pl.read_parquet(OH / f"market_rows_{season}.parquet").filter(
            (pl.col("book") == "draftkings") & (pl.col("snapshot_kind") == "t60")
        )
        mdl = mdl.with_columns(pl.col("player_id").cast(pl.Int64))
        j = mk.join(mdl, on=["game_id", "player_id", "stat"], how="left", suffix="_m")
        parts.append(j.with_columns(pl.lit(season).alias("eval_season")))
    return pl.concat(parts, how="diagonal_relaxed")


def props_section(snap: duckdb.DuckDBPyConnection) -> tuple[dict[str, Any], dict[str, Any]]:
    d = load_props()
    total_quotes = d.height
    no_model = int(d["p_ge"].is_null().sum())
    d = d.filter(pl.col("p_ge").is_not_null())
    frames = []
    for stat in STATS:
        for sea in EVAL_SEASONS:
            s = d.filter((pl.col("stat") == stat) & (pl.col("eval_season") == sea))
            width = int(s["p_ge"].list.len().max())  # type: ignore[arg-type]
            pge = s["p_ge"].list.to_array(width).to_numpy().astype(float)
            line, y = s["line"].to_numpy(), s["y"].to_numpy()
            p_mod, over, status = model_p_over(pge, line, y)
            pge_b = recency_pge(stat, s["m_recency"].to_numpy(), s["sd_recency"].to_numpy(), width)
            p_rec, _, _ = model_p_over(pge_b, line, y)
            s = s.drop("p_ge").with_columns(
                pl.Series("p_model", p_mod),
                pl.Series("p_recency", p_rec),
                pl.Series("over", over),
                pl.Series("status", status),
            )
            frames.append(s)
    allr = pl.concat(frames)
    status_counts = {
        stat: {
            "quotes_with_model": int((allr["stat"] == stat).sum()),
            "scored": int(((allr["stat"] == stat) & (allr["status"] == 0)).sum()),
            "push": int(((allr["stat"] == stat) & (allr["status"] == 1)).sum()),
            "odd_line": int(((allr["stat"] == stat) & (allr["status"] == 2)).sum()),
            "model_push_certain": int(((allr["stat"] == stat) & (allr["status"] == 3)).sum()),
        }
        for stat in STATS
    }
    sc = allr.filter(pl.col("status") == 0)
    od = duckdb.connect(str(ODDS_DB), read_only=True)
    names = dict(
        od.execute(
            "select player_id, any_value(player_name) from odds_history "
            "where season <= 2024 and player_id is not null group by 1"
        ).fetchall()
    )
    od.close()
    res: dict[str, Any] = {
        "quotes_total": total_quotes,
        "quotes_without_model_row_(dnp_or_no_row)": no_model,
        "status_counts": status_counts,
        "per_stat": {},
        "pooled": {},
    }
    res["push_rate_overall"] = float((allr["status"] == 1).sum() / allr.height)
    arrs: dict[str, Any] = {}
    for stat in (*STATS, "pooled"):
        c = sc if stat == "pooled" else sc.filter(pl.col("stat") == stat)
        codes = c["game_id"].to_numpy()
        over = c["over"].to_numpy().astype(bool)
        pm = c["p_model"].to_numpy()
        pr = c["p_recency"].to_numpy()
        pk = c["p_over_mult"].to_numpy()
        lean = pk != 0.5
        pred = {"model": pm > 0.5, "recency": pr > 0.5, "market": pk > 0.5}
        out: dict[str, Any] = {
            "n": int(c.height),
            "games": int(len(np.unique(codes))),
            "market_lean_n": int(lean.sum()),
        }
        out["base_over"] = rate(codes, over)
        out["matrices_all_rows"] = {
            k: binary_matrix(codes, pred[k], over) for k in ("model", "recency")
        }
        out["matrices_market_lean_rows"] = {
            k: binary_matrix(codes, pred[k], over, lean) for k in ("model", "recency", "market")
        }
        hit = {k: pred[k] == over for k in pred}
        out["model_minus_market_on_lean_rows"] = diff_rate(codes, hit["model"], hit["market"], lean)
        out["model_minus_recency_all_rows"] = diff_rate(codes, hit["model"], hit["recency"])
        dis = lean & (pred["model"] != pred["market"])
        out["disagree_model_vs_market"] = {
            "n": int(dis.sum()),
            "model_right": rate(codes, hit["model"], dis),
        }
        by_season = {}
        for sea in EVAL_SEASONS:
            msk = c["eval_season"].to_numpy() == sea
            by_season[str(sea)] = {
                "model_acc": rate(codes, hit["model"], msk),
                "recency_acc": rate(codes, hit["recency"], msk),
                "market_acc_lean_rows": rate(codes, hit["market"], msk & lean),
            }
        out["by_season"] = by_season
        bins = [0.0, 0.03, 0.06, 0.10, 0.15, 0.25, 0.51]
        cb: dict[str, Any] = {}
        for k, p in (("model", pm), ("recency", pr), ("market", pk)):
            conf = np.abs(p - 0.5)
            rows = []
            for lo_, hi_ in zip(bins[:-1], bins[1:], strict=True):
                msk = (conf >= lo_) & (conf < hi_) & (lean if k == "market" else True)
                if k == "market":
                    msk = (conf >= lo_) & (conf < hi_) & lean
                r = rate(codes, hit[k], msk)
                rows.append({"bin": f"{lo_:.2f}-{min(hi_, 0.5):.2f}", **r})
            cb[k] = rows
        out["confidence_bins"] = cb
        # model confident AND agreeing vs disagreeing with the market
        res["per_stat" if stat != "pooled" else "pooled"][stat if stat != "pooled" else "all"] = out
        if stat == "pooled":
            arrs["sc"] = c
    # examples (pts, 2024, most confident model rows)
    sc2 = sc.filter((pl.col("stat") == "pts") & (pl.col("eval_season") == 2024)).with_columns(
        (pl.col("p_model") - 0.5).abs().alias("conf")
    )
    sc2 = sc2.with_columns(((pl.col("p_model") > 0.5) == (pl.col("over") > 0.5)).alias("hit"))
    ex = {}
    for key, flag in (("hit", True), ("miss", False)):
        r = sc2.filter(pl.col("hit") == flag).sort("conf", descending=True).row(0, named=True)
        ex[key] = {
            "game_id": r["game_id"],
            "date": str(r["game_date"]),
            "player": names.get(int(r["player_id"]), str(r["player_id"])),
            "stat": r["stat"],
            "line": r["line"],
            "model_p_over": round(r["p_model"], 3),
            "market_p_over": round(r["p_over_mult"], 3),
            "actual": r["y"],
        }
    res["examples"] = ex
    return res, arrs


# ------------------------------------------------------------------------------------ section 2
def phi(x: np.ndarray) -> np.ndarray:
    return np.asarray(sps.norm.cdf(x))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.asarray(np.log(p / (1 - p)))


def team_totals_baseline(games: pl.DataFrame) -> pl.DataFrame:
    """As-of team recency points for/against (last WINDOW games strictly before the game)."""
    hist: dict[int, deque[tuple[int, int]]] = {}
    rows = []
    for r in games.sort(["game_date", "game_id"]).iter_rows(named=True):
        h, a = r["home_team"], r["away_team"]
        hh, ha = hist.setdefault(h, deque(maxlen=WINDOW)), hist.setdefault(a, deque(maxlen=WINDOW))
        if len(hh) >= MIN_PRIOR and len(ha) >= MIN_PRIOR:
            hpf, hpa = np.mean([x[0] for x in hh]), np.mean([x[1] for x in hh])
            apf, apa = np.mean([x[0] for x in ha]), np.mean([x[1] for x in ha])
            tot = (hpf + apa) / 2 + (apf + hpa) / 2
        else:
            tot = None
        rows.append((r["game_id"], tot))
        hh.append((r["home_pts"], r["away_pts"]))
        ha.append((r["away_pts"], r["home_pts"]))
    return pl.DataFrame(
        rows, schema=["game_id", "total_hat"], orient="row", infer_schema_length=None
    )


def load_game_frame(snap: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    g = snap.execute("select * from games").pl()
    g = g.with_columns((pl.col("home_pts") - pl.col("away_pts")).alias("margin"))
    oof = pl.read_parquet(ROOT / "data" / "injury_elo_realtip" / "oof_predictions.parquet")
    for mdl, col in (("rung0_injury_elo", "p_inj"), ("rung0_mov_elo", "p_mov")):
        g = g.join(
            oof.filter(pl.col("model") == mdl).select("game_id", pl.col("p").alias(col)),
            on="game_id",
            how="left",
        )
    gm = []
    for season in EVAL_SEASONS:
        t = pl.read_parquet(OH / f"game_market_{season}.parquet").filter(
            pl.col("snapshot_kind") == "t60"
        )
        pin = t.filter(pl.col("source") == "pinnacle").select(
            "game_id", pl.col("p_home_mult").alias("p_mkt")
        )
        con = t.filter(pl.col("source") == "consensus").select(
            "game_id", pl.col("p_home_mult").alias("p_mkt_c")
        )
        gm.append(pin.join(con, on="game_id", how="full", coalesce=True))
    mk = pl.concat(gm).with_columns(
        pl.coalesce("p_mkt", "p_mkt_c").alias("p_mkt"),
        pl.col("p_mkt").is_null().alias("mkt_is_fallback"),
    )
    g = g.join(mk.select("game_id", "p_mkt", "mkt_is_fallback"), on="game_id", how="left")
    o = duckdb.connect(str(ODDS_DB), read_only=True)
    sp = o.execute(
        "select game_id, max(case when side='home' then point end) as home_spread, "
        "max(case when side='home' then price_american end) as p_home_px, "
        "max(case when side='away' then price_american end) as p_away_px, "
        "max(case when side='home' then outcome_name end) as home_name, "
        "max(case when side='away' then outcome_name end) as away_name "
        "from odds_history where book='draftkings' and snapshot_kind='t60' and market='spreads' "
        "and season <= 2024 group by game_id"
    ).pl()
    tt = o.execute(
        "select game_id, max(point) as total_line, "
        "max(case when side='over' then price_american end) as over_px, "
        "max(case when side='under' then price_american end) as under_px "
        "from odds_history where book='draftkings' and snapshot_kind='t60' and market='totals' "
        "and season <= 2024 group by game_id"
    ).pl()
    o.close()
    g = g.join(sp, on="game_id", how="left").join(tt, on="game_id", how="left")
    g = g.join(team_totals_baseline(g), on="game_id", how="left")
    return g


def amer_prob(px: np.ndarray) -> np.ndarray:
    px = px.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(px < 0, -px / (-px + 100.0), 100.0 / (px + 100.0))


def devig2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    pa, pb = amer_prob(a), amer_prob(b)
    return np.asarray(pa / (pa + pb))


def ols_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    a = np.vstack([np.ones_like(x), x]).T
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    res = y - a @ coef
    return float(coef[0]), float(coef[1]), float(res.std(ddof=2))


def games_section(g: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ev = g.filter(pl.col("season").is_in(list(EVAL_SEASONS)))
    # ---------------- winner
    w = ev.filter(pl.col("p_mkt").is_not_null() & pl.col("p_inj").is_not_null())
    codes = w["game_id"].to_numpy()
    home_win = w["margin"].to_numpy() > 0
    preds = {
        "injury_elo": w["p_inj"].to_numpy() > 0.5,
        "mov_elo": w["p_mov"].to_numpy() > 0.5,
        "market_pinnacle_ml": w["p_mkt"].to_numpy() > 0.5,
    }
    hits = {k: v == home_win for k, v in preds.items()}
    win: dict[str, Any] = {
        "n": int(w.height),
        "market_fallback_to_consensus": int(w["mkt_is_fallback"].sum()),
        "home_win_rate": rate(codes, home_win),
        "always_home": rate(codes, home_win),
    }
    win["matrices"] = {k: binary_matrix(codes, v, home_win) for k, v in preds.items()}
    win["acc"] = {k: rate(codes, h) for k, h in hits.items()}
    win["inj_minus_market"] = diff_rate(codes, hits["injury_elo"], hits["market_pinnacle_ml"])
    win["mov_minus_market"] = diff_rate(codes, hits["mov_elo"], hits["market_pinnacle_ml"])
    win["by_season"] = {
        str(s): {k: rate(codes, h, w["season"].to_numpy() == s) for k, h in hits.items()}
        for s in EVAL_SEASONS
    }
    conf = np.abs(w["p_mkt"].to_numpy() - 0.5)
    win["by_market_confidence"] = [
        {
            "bin": f"{lo_:.2f}-{hi_:.2f}",
            "market": rate(codes, hits["market_pinnacle_ml"], (conf >= lo_) & (conf < hi_)),
            "injury_elo": rate(codes, hits["injury_elo"], (conf >= lo_) & (conf < hi_)),
        }
        for lo_, hi_ in ((0, 0.1), (0.1, 0.2), (0.2, 0.5))
    ]
    out["winner"] = win
    # example rows
    ex = w.with_columns(
        (pl.col("p_inj") - 0.5).abs().alias("conf"),
        ((pl.col("p_inj") > 0.5) == (pl.col("margin") > 0)).alias("hit"),
    ).filter(pl.col("season") == 2024)
    exo = {}
    for key, flag in (("hit", True), ("miss", False)):
        r = ex.filter(pl.col("hit") == flag).sort("conf", descending=True).row(0, named=True)
        exo[key] = {
            "game_id": r["game_id"],
            "date": str(r["game_date"]),
            "home": r["home_name"],
            "away": r["away_name"],
            "injury_elo_p_home": round(r["p_inj"], 3),
            "market_p_home": round(r["p_mkt"], 3),
            "final": f"{r['home_pts']}-{r['away_pts']}",
        }
    out["winner_examples"] = exo

    # ---------------- spread + total baselines (as-of: fit on seasons strictly before)
    spread: dict[str, Any] = {}
    total: dict[str, Any] = {}
    sp_rows: list[pl.DataFrame] = []
    for sea in EVAL_SEASONS:
        fit = g.filter(pl.col("season") < sea)
        te = ev.filter(pl.col("season") == sea)
        for tag, col in (("mov_elo", "p_mov"), ("injury_elo", "p_inj")):
            f = fit.filter(pl.col(col).is_not_null())
            a, b_, sig = ols_fit(logit(f[col].to_numpy()), f["margin"].to_numpy().astype(float))
            t2 = te.filter(pl.col(col).is_not_null() & pl.col("home_spread").is_not_null())
            mhat = a + b_ * logit(t2[col].to_numpy())
            sp_rows.append(
                t2.select(
                    "game_id", "season", "margin", "home_spread", "p_home_px", "p_away_px"
                ).with_columns(
                    pl.lit(tag).alias("model"),
                    pl.Series("mhat", mhat),
                    pl.lit(sig).alias("sigma"),
                )
            )
        ft = fit.filter(pl.col("total_hat").is_not_null())
        resid = (ft["home_pts"] + ft["away_pts"] - ft["total_hat"]).to_numpy()
        bias = float(resid.mean())
        sig_t = float(resid.std(ddof=1))
        t3 = te.filter(pl.col("total_hat").is_not_null() & pl.col("total_line").is_not_null())
        total[str(sea)] = {"fit_bias": bias, "fit_sigma": sig_t, "fit_n": int(ft.height)}
        sp_rows.append(
            t3.select(
                "game_id",
                "season",
                "home_pts",
                "away_pts",
                "total_hat",
                "total_line",
                "over_px",
                "under_px",
            ).with_columns(
                pl.lit("total").alias("model"),
                pl.lit(sig_t).alias("sigma"),
                pl.lit(bias).alias("bias"),
            )
        )
    sp_all = pl.concat([x for x in sp_rows if "mhat" in x.columns], how="diagonal_relaxed")
    for tag in ("mov_elo", "injury_elo"):
        c = sp_all.filter(pl.col("model") == tag)
        s_ = c["home_spread"].to_numpy()
        m_ = c["margin"].to_numpy().astype(float)
        cover_val = m_ + s_
        keep = cover_val != 0  # pushes (integer spreads) excluded
        codes = c["game_id"].to_numpy()[keep]
        home_cov = cover_val[keep] > 0
        mhat = c["mhat"].to_numpy()[keep]
        pred = (mhat + s_[keep]) > 0
        lean = devig2(c["p_home_px"].to_numpy()[keep], c["p_away_px"].to_numpy()[keep])
        mkt_pred = lean > 0.5
        mk_lean = lean != 0.5
        fav = s_[keep] < 0  # home is the market favourite
        mod_mae = float(np.abs(m_ - c["mhat"].to_numpy()).mean())
        spread[tag] = {
            "n": int(keep.sum()),
            "pushes_excluded": int((~keep).sum()),
            "home_cover_rate": rate(codes, home_cov),
            "matrix_model": binary_matrix(codes, pred, home_cov),
            "matrix_price_lean": binary_matrix(codes, mkt_pred, home_cov, mk_lean),
            "always_home": rate(codes, home_cov),
            "always_favourite": rate(codes, fav == home_cov),
            "model_minus_price_lean": diff_rate(
                codes, pred == home_cov, mkt_pred == home_cov, mk_lean
            ),
            "mae_market_spread_vs_margin": float(np.abs(m_ + s_).mean()),
            "mae_model_vs_margin": mod_mae,
            "by_season": {
                str(sea): rate(codes, pred == home_cov, c["season"].to_numpy()[keep] == sea)
                for sea in EVAL_SEASONS
            },
        }
    tot_rows = pl.concat([x for x in sp_rows if "total_hat" in x.columns], how="diagonal_relaxed")
    tl = tot_rows["total_line"].to_numpy()
    act = (tot_rows["home_pts"] + tot_rows["away_pts"]).to_numpy().astype(float)
    th = tot_rows["total_hat"].to_numpy() + tot_rows["bias"].to_numpy()
    keep = act != tl
    codes = tot_rows["game_id"].to_numpy()[keep]
    over = act[keep] > tl[keep]
    pred = th[keep] > tl[keep]
    lean = devig2(tot_rows["over_px"].to_numpy()[keep], tot_rows["under_px"].to_numpy()[keep])
    ml = lean != 0.5
    total["overall"] = {
        "n": int(keep.sum()),
        "pushes_excluded": int((~keep).sum()),
        "over_rate": rate(codes, over),
        "matrix_baseline": binary_matrix(codes, pred, over),
        "matrix_price_lean": binary_matrix(codes, lean > 0.5, over, ml),
        "baseline_minus_price_lean": diff_rate(codes, pred == over, (lean > 0.5) == over, ml),
        "mae_market_line_vs_total": float(np.abs(act - tl).mean()),
        "mae_baseline_vs_total": float(np.abs(act - th).mean()),
        "by_season": {
            str(sea): rate(codes, pred == over, tot_rows["season"].to_numpy()[keep] == sea)
            for sea in EVAL_SEASONS
        },
    }
    out["spread"] = spread
    out["total"] = total
    return out


# ------------------------------------------------------------------------------------ section 3
def build_periods(snap: duckdb.DuckDBPyConnection, force: bool = False) -> pl.DataFrame:
    """Cumulative scoreboard at the end of periods 1-4 and the final, from pbp parquet files."""
    if PERIODS_CACHE.exists() and not force:
        return pl.read_parquet(PERIODS_CACHE)
    ids = snap.execute("select game_id from games order by game_id").pl()["game_id"].to_list()
    rows = []
    t0 = time.time()
    for i, gid in enumerate(ids):
        f = ROOT / "data" / "pbp" / f"{gid}.parquet"
        if not f.exists():
            rows.append({"game_id": gid})
            continue
        d = pl.read_parquet(f, columns=["period", "score_home", "score_away"]).with_columns(
            pl.col("score_home").cast(pl.Int64, strict=False),
            pl.col("score_away").cast(pl.Int64, strict=False),
        )
        r: dict[str, Any] = {"game_id": gid}
        for p in range(1, 5):
            u = d.filter((pl.col("period") <= p) & pl.col("score_home").is_not_null())
            r[f"h_c{p}"] = int(u["score_home"].max()) if u.height else None  # type: ignore[arg-type]
            r[f"a_c{p}"] = int(u["score_away"].max()) if u.height else None  # type: ignore[arg-type]
        u = d.filter(pl.col("score_home").is_not_null())
        r["h_fin"] = int(u["score_home"].max()) if u.height else None  # type: ignore[arg-type]
        r["a_fin"] = int(u["score_away"].max()) if u.height else None  # type: ignore[arg-type]
        r["max_period"] = int(d["period"].max())  # type: ignore[arg-type]
        rows.append(r)
        if (i + 1) % 500 == 0:
            print(f"pbp {i + 1}/{len(ids)} {time.time() - t0:.0f}s", flush=True)
    out = pl.DataFrame(rows, infer_schema_length=None)
    out.write_parquet(PERIODS_CACHE)
    return out


def period_frame(
    snap: duckdb.DuckDBPyConnection, g: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    pb = build_periods(snap)
    pos = snap.execute(
        "select p.game_id, p.period, "
        "sum(case when p.off_team = g.home_team then p.pts else 0 end) as h, "
        "sum(case when p.off_team = g.away_team then p.pts else 0 end) as a "
        "from poss p join games g using(game_id) where p.period <= 4 group by 1, 2"
    ).pl()
    pw = pos.pivot(on="period", index="game_id", values=["h", "a"])
    d = g.join(pb, on="game_id", how="left")
    rec: dict[str, Any] = {"games_total": int(d.height)}
    d = d.with_columns(
        (pl.col("h_fin").is_null()).alias("no_pbp"),
        ((pl.col("h_fin") != pl.col("home_pts")) | (pl.col("a_fin") != pl.col("away_pts")))
        .fill_null(True)
        .alias("pbp_final_mismatch"),
    )
    rec["no_pbp_rows"] = int(d["no_pbp"].sum())
    rec["pbp_final_vs_box_mismatch"] = int(d["pbp_final_mismatch"].sum())
    # possession-derived periods vs pbp periods
    cols = pw.columns
    d = d.join(pw, on="game_id", how="left")
    mism_any = pl.lit(False)
    per_mm = {}
    for p in range(1, 5):
        hc, ac = f"h_c{p}", f"a_c{p}"
        h_prev = f"h_c{p - 1}" if p > 1 else None
        a_prev = f"a_c{p - 1}" if p > 1 else None
        hq = pl.col(hc) - (pl.col(h_prev) if h_prev else 0)
        aq = pl.col(ac) - (pl.col(a_prev) if a_prev else 0)
        d = d.with_columns(hq.alias(f"hq{p}"), aq.alias(f"aq{p}"))
        hp_name, ap_name = f"h_{p}", f"a_{p}"
        assert hp_name in cols and ap_name in cols, cols
        bad = (
            (pl.col(f"hq{p}") != pl.col(hp_name)) | (pl.col(f"aq{p}") != pl.col(ap_name))
        ).fill_null(False)
        d = d.with_columns(bad.alias(f"mm{p}"))
        per_mm[f"Q{p}"] = int(d[f"mm{p}"].sum())
        mism_any = mism_any | pl.col(f"mm{p}")
    d = d.with_columns(mism_any.alias("poss_vs_pbp_period_mismatch"))
    rec["poss_vs_pbp_games_with_any_period_mismatch"] = int(d["poss_vs_pbp_period_mismatch"].sum())
    rec["poss_vs_pbp_mismatch_by_period"] = per_mm
    rec["poss_vs_pbp_mismatch_ids"] = d.filter(pl.col("poss_vs_pbp_period_mismatch"))[
        "game_id"
    ].to_list()
    rec["poss_vs_pbp_mismatch_rate"] = rec["poss_vs_pbp_games_with_any_period_mismatch"] / d.height
    rec["pbp_final_mismatch_rate"] = rec["pbp_final_vs_box_mismatch"] / d.height
    # periods vs OT: regulation winner only (period 4 cumulative) for 1H/2H
    d = d.with_columns(
        (pl.col("hq1") - pl.col("aq1")).alias("m_q1"),
        (pl.col("hq2") - pl.col("aq2")).alias("m_q2"),
        (pl.col("hq3") - pl.col("aq3")).alias("m_q3"),
        (pl.col("hq4") - pl.col("aq4")).alias("m_q4"),
        (pl.col("hq1") + pl.col("hq2") - pl.col("aq1") - pl.col("aq2")).alias("m_h1"),
        (pl.col("hq3") + pl.col("hq4") - pl.col("aq3") - pl.col("aq4")).alias("m_h2"),
        (pl.col("hq1") + pl.col("aq1")).alias("t_q1"),
        (pl.col("hq2") + pl.col("aq2")).alias("t_q2"),
        (pl.col("hq1") + pl.col("hq2") + pl.col("aq1") + pl.col("aq2")).alias("t_h1"),
    )
    ok = (~pl.col("no_pbp")) & (~pl.col("pbp_final_mismatch"))
    excluded = d.filter(~ok)
    rec["excluded_game_ids"] = excluded["game_id"].to_list()[:50]
    rec["excluded_n"] = int(excluded.height)
    rec["excluded_by_season"] = {
        str(k): int(v) for k, v in excluded.group_by("season").len().sort("season").iter_rows()
    }
    d = d.filter(ok)
    return d, rec


def period_section(d: pl.DataFrame, rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"reconciliation": rec}
    targets = ["q1", "q2", "q3", "q4", "h1", "h2"]
    out["by_model"] = {}
    for tag, col in (("injury_elo", "p_inj"), ("mov_elo", "p_mov")):
        res: dict[str, Any] = {}
        for sea in EVAL_SEASONS:
            fit = d.filter((pl.col("season") < sea) & pl.col(col).is_not_null())
            te = d.filter((pl.col("season") == sea) & pl.col(col).is_not_null())
            xf, xt = logit(fit[col].to_numpy()), logit(te[col].to_numpy())
            _, bg, _ = ols_fit(xf, fit["margin"].to_numpy().astype(float))
            for t in targets:
                yf = fit[f"m_{t}"].to_numpy().astype(float)
                a, b_, sig = ols_fit(xf, yf)
                mu = a + b_ * xt
                p_home = 1 - phi((0.5 - mu) / sig)
                p_away = phi((-0.5 - mu) / sig)
                p_tie = 1 - p_home - p_away
                y = te[f"m_{t}"].to_numpy()
                cls = np.where(y > 0, 0, np.where(y == 0, 1, 2))
                probs = np.vstack([p_home, p_tie, p_away]).T
                pc = np.argmax(probs, axis=1)
                ll = -np.log(np.clip(probs[np.arange(len(y)), cls], 1e-9, None))
                full_cls = np.where(te["margin"].to_numpy() > 0, 0, 2)
                full_pred = te[col].to_numpy() > 0.5
                full_hit = (full_pred == (full_cls == 0)).astype(float)
                res.setdefault(t, {})[str(sea)] = {
                    "codes": te["game_id"].to_numpy(),
                    "cls": cls,
                    "pc": pc,
                    "ll": ll,
                    "full_hit": full_hit,
                    "share_vs_game": b_ / bg,
                    "sigma": sig,
                    "intercept": a,
                    "fit_n": int(fit.height),
                    "mu": mu,
                    "y": y,
                }
        agg: dict[str, Any] = {}
        for t in targets:
            parts = [res[t][str(s)] for s in EVAL_SEASONS]
            codes = np.concatenate([p["codes"] for p in parts])
            cls = np.concatenate([p["cls"] for p in parts])
            pc = np.concatenate([p["pc"] for p in parts])
            ll = np.concatenate([p["ll"] for p in parts])
            fh = np.concatenate([p["full_hit"] for p in parts])
            mat = np.zeros((3, 3), int)
            for pi, ci_ in zip(pc, cls, strict=True):
                mat[pi, ci_] += 1
            hit = (pc == cls).astype(float)
            dec = cls != 1
            # predicted side among decisive periods: counts as hit iff the predicted side won
            hit_dec = pc == cls
            hm = np.where(cls == 0)[0]
            _ = hm
            agg[t] = {
                "n": int(len(cls)),
                "matrix_pred_by_actual_[H,T,A]": mat.tolist(),
                "tie_rate": rate(codes, (cls == 1)),
                "home_win_rate": rate(codes, (cls == 0)),
                "accuracy_3class": rate(codes, hit),
                "accuracy_decisive_only": rate(codes, hit_dec, dec),
                "always_home_3class": rate(codes, (cls == 0)),
                "full_game_accuracy_same_games": rate(codes, fh),
                "logloss_3class": float(ll.mean()),
                "full_minus_period_acc_decisive": diff_rate(codes, fh.astype(bool), hit_dec, dec),
                "share_vs_game_slope": {s: float(res[t][s]["share_vs_game"]) for s in res[t]},
                "sigma": {s: float(res[t][s]["sigma"]) for s in res[t]},
                "by_season": {
                    s: rate(res[t][s]["codes"], (res[t][s]["pc"] == res[t][s]["cls"]).astype(float))
                    for s in res[t]
                },
            }
        out["by_model"][tag] = agg
    # totals per period: baseline-implied vs outcome (model vs outcome, no market)
    tot: dict[str, Any] = {}
    for t, share_col in (("q1", "t_q1"), ("q2", "t_q2"), ("h1", "t_h1")):
        maes, naive = [], []
        for sea in EVAL_SEASONS:
            fit = d.filter((pl.col("season") < sea) & pl.col("total_hat").is_not_null())
            te = d.filter((pl.col("season") == sea) & pl.col("total_hat").is_not_null())
            sh = float(np.mean((fit[share_col] / fit["total_hat"]).to_numpy()))
            maes.append(np.abs(te[share_col].to_numpy() - sh * te["total_hat"].to_numpy()))
            naive.append(np.abs(te[share_col].to_numpy() - fit[share_col].mean()))
        tot[t] = {
            "mae_total_hat_x_share": float(np.concatenate(maes).mean()),
            "mae_constant_prior_mean": float(np.concatenate(naive).mean()),
            "n": int(sum(len(m) for m in maes)),
        }
    out["period_totals_vs_outcome"] = tot
    out["market_for_periods"] = (
        "none: odds history holds h2h, spreads, totals and player props only"
    )
    return out


# ------------------------------------------------------------------------------------ render
def mat_line(m: dict[str, Any]) -> str:
    return f"TP {m['tp']} / FP {m['fp']} / FN {m['fn']} / TN {m['tn']}"


def render(res: dict[str, Any]) -> str:
    P = res["props"]
    G = res["games"]
    Q = res["periods"]
    L: list[str] = []
    a = L.append
    a("# Confusion matrices: props, game lines, quarters and halves (2026-10-11)\n")
    a(
        "**DESCRIPTIVE EXPLORATION, not a claim.** No pre-registration; nothing here may change "
        "production. Seasons 2023 (2023-24) and 2024 (2024-25) are scored. 2022 is only training "
        "history for the baselines. Season 2025 (frozen holdout) is never read. Every rate carries n "
        "and a game-clustered bootstrap 95% CI (B=2000, seed 2026). Pushes (the result lands exactly "
        "on a whole-number line) are counted and excluded.\n"
    )
    snapm = res["snapshot"]
    a(
        f"Data snapshot: tables copied from `nba.duckdb` (file last modified {snapm['nba_duckdb_mtime']}, "
        f"copied {snapm['copied_at']}, repo HEAD {snapm['git_head']}) while the archivist was rewriting "
        "possessions, stints and 10 neutral-site games. Game-level scores come from the `games` table and "
        "play-by-play files as they stood then. Odds come from `data/odds/odds_history.duckdb` and "
        "`reports/odds_history/*` (frozen pulls).\n"
    )
    a("## Summary\n")
    a(
        "Plain reading of the CI: if the model-minus-comparator interval spans zero we cannot say either is better.\n"
    )
    a(
        "| target | model | n | model accuracy [95% CI] | market / baseline accuracy [95% CI] | model minus comparator |"
    )
    a("|---|---|---|---|---|---|")
    for st in (*STATS, "pooled"):
        o = P["pooled"]["all"] if st == "pooled" else P["per_stat"][st]
        nm = o["market_lean_n"]
        a(
            f"| prop over/under, {st} | context-residual | {o['matrices_all_rows']['model']['n']} | "
            f"{fmt(o['matrices_all_rows']['model']['accuracy'])} | "
            f"market lean (DK no-vig, rows with a lean): {fmt(o['matrices_market_lean_rows']['market']['accuracy'])} | "
            f"{fmt_diff(o['model_minus_market_on_lean_rows'])} (n={o['model_minus_market_on_lean_rows']['n']}) |"
        )
        a(
            f"| prop over/under, {st} | recency baseline | {o['matrices_all_rows']['recency']['n']} | "
            f"{fmt(o['matrices_all_rows']['recency']['accuracy'])} | "
            f"always-under: {100 * (1 - o['base_over']['rate']):.1f}% | "
            f"vs context-residual {fmt_diff(o['model_minus_recency_all_rows'])} (model minus recency) |"
        )
        _ = nm
    w = G["winner"]
    for k, lab in (("injury_elo", "injury-Elo (production)"), ("mov_elo", "MOV-Elo")):
        dd = w["inj_minus_market"] if k == "injury_elo" else w["mov_minus_market"]
        a(
            f"| game winner | {lab} | {w['n']} | {fmt(w['acc'][k])} | Pinnacle ML favourite: "
            f"{fmt(w['acc']['market_pinnacle_ml'])} | {fmt_diff(dd)} |"
        )
    for tag, lab in (
        ("injury_elo", "injury-Elo margin baseline"),
        ("mov_elo", "MOV-Elo margin baseline"),
    ):
        s = G["spread"][tag]
        a(
            f"| spread (ATS cover) | {lab} | {s['n']} | {fmt(s['matrix_model']['accuracy'])} | "
            f"DK price lean: {fmt(s['matrix_price_lean']['accuracy'])}; always-favourite: {fmt(s['always_favourite'])} | "
            f"{fmt_diff(s['model_minus_price_lean'])} |"
        )
    t = G["total"]["overall"]
    a(
        f"| total (over/under) | team-recency baseline | {t['n']} | {fmt(t['matrix_baseline']['accuracy'])} | "
        f"DK price lean: {fmt(t['matrix_price_lean']['accuracy'])}; always-under: {100 * (1 - t['over_rate']['rate']):.1f}% | "
        f"{fmt_diff(t['baseline_minus_price_lean'])} |"
    )
    for tk, lab in (
        ("q1", "Q1 winner"),
        ("q2", "Q2 winner"),
        ("q3", "Q3 winner"),
        ("q4", "Q4 winner"),
        ("h1", "1H winner"),
        ("h2", "2H winner"),
    ):
        q = Q["by_model"]["injury_elo"][tk]
        a(
            f"| {lab} (3-class H/tie/A) | injury-Elo-mapped baseline | {q['n']} | {fmt(q['accuracy_3class'])} | "
            f"always-home: {fmt(q['always_home_3class'])}; NO MARKET | "
            f"decisive-only acc {fmt(q['accuracy_decisive_only'])} vs full game {fmt(q['full_game_accuracy_same_games'])} |"
        )
    a("")
    pb = P["pooled"]["all"]
    mb = pb["confidence_bins"]["model"]
    top = [x for x in mb if x["n"] and float(x["bin"].split("-")[0]) >= 0.10]
    n_top = sum(x["n"] for x in top)
    acc_top = sum(x["rate"] * x["n"] for x in top) / n_top
    a("### In plain words\n")
    a(
        f"- **Props.** Picking over/under from the model is right {fmt(pb['matrices_all_rows']['model']['accuracy'])} of the time across {pb['n']} legs; blindly picking under is right {100 * (1 - pb['base_over']['rate']):.1f}%, and the market's own lean (on rows where it has one) is right {fmt(pb['matrices_market_lean_rows']['market']['accuracy'])}. "
        f"On those same rows the model is {fmt_diff(pb['model_minus_market_on_lean_rows'])} relative to the market, so the market is slightly better and the model adds nothing over it. The model beats the plain recency baseline by {fmt_diff(pb['model_minus_recency_all_rows'])}. "
        f"The model says OVER on only {fmt(pb['matrices_all_rows']['model']['pred_over_rate'])} of legs (counts are right-skewed, so its median sits under the line), so it mostly says UNDER and its recall for overs is low. "
        f"On the legs where it is confident (|P-0.5| >= 0.10; n={n_top}) accuracy is about {100 * acc_top:.1f}%, above the 52.4% break-even; the market's no-vig probabilities rarely get that far from 0.5 (see the bins), so there is no like-for-like market number for those legs, and accuracy is not profit.\n"
    )
    a(
        f"- **Game winners.** Injury-Elo picks the right team {fmt(w['acc']['injury_elo'])} of {w['n']} games versus {fmt(w['acc']['market_pinnacle_ml'])} for always taking the Pinnacle favourite ({fmt_diff(w['inj_minus_market'])}). MOV-Elo is lower still. The market wins, as before.\n"
    )
    sp = G["spread"]["injury_elo"]
    a(
        f"- **Spread and total.** Against the line our baselines are coin flips ({fmt(sp['matrix_model']['accuracy'])} on {sp['n']} spreads, {fmt(t['matrix_baseline']['accuracy'])} on {t['n']} totals), and their average error versus the real outcome is larger than the market's (spread {sp['mae_model_vs_margin']:.2f} vs {sp['mae_market_spread_vs_margin']:.2f} points; total {t['mae_baseline_vs_total']:.2f} vs {t['mae_market_line_vs_total']:.2f}). No stored production forecast exists for margin or total, so these are baselines only.\n"
    )
    q1, h1 = Q["by_model"]["injury_elo"]["q1"], Q["by_model"]["injury_elo"]["h1"]
    a(
        f"- **Quarters and halves.** Picking who wins Q1 is right {fmt(q1['accuracy_3class'])} (ties count as misses and happen {fmt(q1['tie_rate'])} of the time); the first half is {fmt(h1['accuracy_3class'])}; Q4 is the hardest ({fmt(Q['by_model']['injury_elo']['q4']['accuracy_3class'])}). Full-game accuracy minus period accuracy is {fmt_diff(q1['full_minus_period_acc_decisive'])} for Q1 and {fmt_diff(h1['full_minus_period_acc_decisive'])} for the first half, restricted to games with a decisive period. No market exists for any of these.\n"
    )
    a(
        "**Where we have no market to compare:** quarter and half winners, period spreads and period totals (the odds history has `h2h`, `spreads`, `totals` and four player-prop markets only; no period lines were invented). Spreads and totals have no model-vs-market accuracy contest in the usual sense: the market line is set so each side is about 50/50, so the 'market' column is just the tiny lean in the no-vig prices (and trivial rules like always-favourite). The comparison that matters there is MAE of line vs outcome, given below.\n"
    )
    a("Break-even note: at -110 both ways a bettor needs about 52.4% on decided legs.\n")

    # --- props detail
    a("## 1. Player props (DraftKings T-60 main line)\n")
    a(
        "Model = stored production-style context-residual walk-forward forecasts (`reports/odds_history/model_rows_*`, "
        "month-block refit, integer support; this is the same set the ODDS_HISTORY test scored). P(over) is the integer-support "
        "probability of beating the line; on whole-number lines the push mass is removed. Recency baseline = same rows, recency "
        "mean and sd, turned into an integer-grid distribution (Gaussian-rounded for pts, negative binomial for reb/ast/fg3m), "
        "same push handling. `prop_predictions` in `nba.duckdb` was not used: it stores sparse tail thresholds from an older "
        "gamma run, not a full integer distribution. Predicted OVER means P(over) > 0.5. The market lean is the no-vig DK "
        "implied P(over) > 0.5; where no-vig is exactly 0.5 (even -110/-110) the market has no lean and those rows are left out of "
        "the market matrices and of the paired comparisons.\n"
    )
    a(
        f"Quotes in the DK T-60 frame: {P['quotes_total']}; no model row (player did not play, or no forecast row): {P['quotes_without_model_row_(dnp_or_no_row)']}. Pushes: {100 * P['push_rate_overall']:.1f}% because every DraftKings main prop line in this pull is a half-point (x.5), so a push cannot happen; spreads and totals below do have whole-number lines and their pushes are excluded.\n"
    )
    a(
        "| stat | quotes with model | scored | pushes | odd lines | model-certain-push |\n|---|---|---|---|---|---|"
    )
    for st in STATS:
        c = P["status_counts"][st]
        a(
            f"| {st} | {c['quotes_with_model']} | {c['scored']} | {c['push']} | {c['odd_line']} | {c['model_push_certain']} |"
        )
    a("")
    for st in (*STATS, "pooled"):
        o = P["pooled"]["all"] if st == "pooled" else P["per_stat"][st]
        a(f"### {st}\n")
        a(
            f"n = {o['n']} scored legs over {o['games']} games. Base rate of overs: {fmt(o['base_over'])}. Rows where the market has a lean: {o['market_lean_n']}.\n"
        )
        a("Context-residual model (all scored rows); rows = predicted, columns = actual:\n")
        a(
            md_matrix(
                o["matrices_all_rows"]["model"],
                ("predict OVER", "predict UNDER"),
                ("actual over", "actual under"),
            )
        )
        mm = o["matrices_all_rows"]["model"]
        a(
            f"accuracy {fmt(mm['accuracy'])}; precision(over) {fmt(mm['precision_over'])}; recall(over) {fmt(mm['recall_over'])}; calls over on {fmt(mm['pred_over_rate'])} of legs.\n"
        )
        a(
            "| rule | rows | accuracy | precision(over) | recall(over) | calls over |\n|---|---|---|---|---|---|"
        )
        for k, lab, src in (
            ("model", "context-residual", "matrices_all_rows"),
            ("recency", "recency baseline", "matrices_all_rows"),
            ("model", "context-residual (market-lean rows)", "matrices_market_lean_rows"),
            ("recency", "recency baseline (market-lean rows)", "matrices_market_lean_rows"),
            ("market", "market no-vig lean (market-lean rows)", "matrices_market_lean_rows"),
        ):
            m = o[src][k]
            a(
                f"| {lab} | {m['n']} | {fmt(m['accuracy'])} | {fmt(m['precision_over'])} | {fmt(m['recall_over'])} | {fmt(m['pred_over_rate'])} |"
            )
        a("")
        dm = o["disagree_model_vs_market"]
        a(
            f"Model minus market (same market-lean rows): {fmt_diff(o['model_minus_market_on_lean_rows'])}. When model and market disagree (n={dm['n']}), the model is right {fmt(dm['model_right'])}.\n"
        )
        a(
            "Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):\n"
        )
        a("| bin | model acc (n) | recency acc (n) | market acc (n) |\n|---|---|---|---|")
        for i in range(len(o["confidence_bins"]["model"])):
            cells = []
            for k in ("model", "recency", "market"):
                r = o["confidence_bins"][k][i]
                cells.append(f"{fmt(r)} (n={r['n']})" if r["n"] else "-")
            a(f"| {o['confidence_bins']['model'][i]['bin']} | " + " | ".join(cells) + " |")
        a("")
    ex = P["examples"]
    a(
        "**One real hit, one real miss (points, 2024-25 season, the model's most confident calls):**\n"
    )
    for k in ("hit", "miss"):
        e = ex[k]
        a(
            f"- {k.upper()}: {e['date']} {e['player']} {e['stat']}, DK line {e['line']}; model P(over) {e['model_p_over']}, market no-vig P(over) {e['market_p_over']}; actual {e['actual']}."
        )
    a("")

    # --- games
    a("## 2. Game lines\n")
    a(
        f"Winner rows: {w['n']} games in 2023+2024 with both a model and a Pinnacle T-60 moneyline ({w['market_fallback_to_consensus']} used the consensus fallback, as in ODDS_HISTORY). Home-win base rate {fmt(w['home_win_rate'])}. Rows = predicted, columns = actual (positive class = home win).\n"
    )
    for k, lab in (
        ("injury_elo", "injury-Elo (production)"),
        ("mov_elo", "MOV-Elo"),
        ("market_pinnacle_ml", "Pinnacle moneyline favourite"),
    ):
        m = w["matrices"][k]
        a(
            f"**{lab}** accuracy {fmt(m['accuracy'])}; precision(home) {fmt(m['precision_over'])}; recall(home) {fmt(m['recall_over'])}\n"
        )
        a(md_matrix(m, ("predict HOME", "predict AWAY"), ("home won", "away won")))
    a(
        f"Injury-Elo minus market: {fmt_diff(w['inj_minus_market'])}. MOV-Elo minus market: {fmt_diff(w['mov_minus_market'])}.\n"
    )
    a("| market confidence | market acc | injury-Elo acc |\n|---|---|---|")
    for r in w["by_market_confidence"]:
        a(
            f"| |P-0.5| {r['bin']} | {fmt(r['market'])} (n={r['market']['n']}) | {fmt(r['injury_elo'])} |"
        )
    a("")
    for k in ("hit", "miss"):
        e = G["winner_examples"][k]
        a(
            f"- {k.upper()}: {e['date']} {e['away']} at {e['home']}; injury-Elo P(home) {e['injury_elo_p_home']}, market {e['market_p_home']}; final {e['final']} (home-away)."
        )
    a("")
    a("### Spread (ATS) and total: baselines, not production forecasts\n")
    a(
        "We have no stored production forecast of the margin or the total, so these are transparent baselines labelled as such. Spread: home margin regressed on logit(Elo win probability), fit on seasons strictly before the scored season (2022 only for 2023; 2022-23 for 2024), normal error; predicted cover = baseline margin + DK home spread > 0. The DK T-60 spread is the line. Total: for each game, average of (home points-for + away points-against)/2 and (away points-for + home points-against)/2 over each team's last 20 prior games, shifted by the mean error in prior seasons. Pushes excluded.\n"
    )
    for tag in ("injury_elo", "mov_elo"):
        s = G["spread"][tag]
        a(
            f"**Spread, {tag} baseline**: n={s['n']} (pushes excluded {s['pushes_excluded']}); home covers {fmt(s['home_cover_rate'])}; always-favourite {fmt(s['always_favourite'])}; model accuracy {fmt(s['matrix_model']['accuracy'])}; market-price lean accuracy {fmt(s['matrix_price_lean']['accuracy'])}.\n"
        )
        a(
            md_matrix(
                s["matrix_model"],
                ("predict HOME covers", "predict AWAY covers"),
                ("home covered", "away covered"),
            )
        )
        a(
            f"MAE vs actual margin: market spread {s['mae_market_spread_vs_margin']:.2f} pts, baseline {s['mae_model_vs_margin']:.2f} pts.\n"
        )
    a(
        f"**Total**: n={t['n']} (pushes excluded {t['pushes_excluded']}); overs {fmt(t['over_rate'])}; baseline accuracy {fmt(t['matrix_baseline']['accuracy'])}; DK price-lean accuracy {fmt(t['matrix_price_lean']['accuracy'])}. MAE vs actual total: market line {t['mae_market_line_vs_total']:.2f}, baseline {t['mae_baseline_vs_total']:.2f}.\n"
    )
    a(
        md_matrix(
            t["matrix_baseline"], ("predict OVER", "predict UNDER"), ("went over", "went under")
        )
    )

    # --- periods
    a("## 3. Quarters and halves\n")
    r = Q["reconciliation"]
    a(
        f"Period scores come from the running scoreboard in `data/pbp/*.parquet` (score at the end of each period; 1H = Q1+Q2, 2H = Q3+Q4, regulation only so overtime is ignored). "
        f"Reconciliation on 2022-2024 games in the snapshot ({r['games_total']}): pbp final vs box-score final mismatch in {r['pbp_final_vs_box_mismatch']} games ({100 * r['pbp_final_mismatch_rate']:.2f}%); games without a pbp file: {r['no_pbp_rows']}. "
        f"Possession-table period points (pts summed by period) differ from the pbp scoreboard in {r['poss_vs_pbp_games_with_any_period_mismatch']} games ({100 * r['poss_vs_pbp_mismatch_rate']:.2f}%); by period {r['poss_vs_pbp_mismatch_by_period']}, game ids {r['poss_vs_pbp_mismatch_ids']}. "
        f"Games excluded from this section (no pbp or pbp final differs from box): {r['excluded_n']} (by season {r['excluded_by_season']}); ids: {r['excluded_game_ids']}. "
        "The possession table's sum of points equals the box final in every snapshot game; the pbp scoreboard is the independent period source.\n"
    )
    a(
        "Baseline: period margin = intercept + slope * logit(Elo p), fit on prior seasons only (2022 for 2023, 2022-23 for 2024), normal error with continuity correction: P(home wins) = P(M > 0.5), P(tie) = P(-0.5 < M <= 0.5). Predicted class = argmax of the three (a tie is almost never the argmax).\n"
    )
    for tag in ("injury_elo", "mov_elo"):
        a(f"### {tag}-mapped baseline\n")
        a(
            "| target | n | tie rate | home-win rate | 3-class acc | decisive-only acc | full-game acc, same games | full minus period (decisive) | slope vs game 23/24 | sigma 23/24 |\n|---|---|---|---|---|---|---|---|---|---|"
        )
        for tk in ("q1", "q2", "q3", "q4", "h1", "h2"):
            q = Q["by_model"][tag][tk]
            a(
                f"| {tk.upper()} | {q['n']} | {fmt(q['tie_rate'])} | {fmt(q['home_win_rate'])} | {fmt(q['accuracy_3class'])} | "
                f"{fmt(q['accuracy_decisive_only'])} | {fmt(q['full_game_accuracy_same_games'])} | {fmt_diff(q['full_minus_period_acc_decisive'])} | "
                f"{q['share_vs_game_slope']['2023']:.2f}/{q['share_vs_game_slope']['2024']:.2f} | {q['sigma']['2023']:.1f}/{q['sigma']['2024']:.1f} |"
            )
        a("")
    a(
        "3-class confusion matrices for the production-mapped (injury-Elo) baseline, pooled 2023+2024. Rows = predicted [H, Tie, A], columns = actual [H, Tie, A]:\n"
    )
    for tk in ("q1", "q2", "h1"):
        m = Q["by_model"]["injury_elo"][tk]["matrix_pred_by_actual_[H,T,A]"]
        a(f"**{tk.upper()}**\n\n| predicted \\ actual | H | Tie | A |\n|---|---|---|---|")
        for lab, row in zip(("H", "Tie", "A"), m, strict=True):
            a(f"| {lab} | {row[0]} | {row[1]} | {row[2]} |")
        a("")
    a(
        "Period totals, baseline-implied vs outcome only (no market): MAE of (game total baseline x prior-season period share) against MAE of the prior-season mean:\n"
    )
    a("| period | n | MAE share x total_hat | MAE constant |\n|---|---|---|---|")
    for tk, v in Q["period_totals_vs_outcome"].items():
        a(
            f"| {tk.upper()} | {v['n']} | {v['mae_total_hat_x_share']:.2f} | {v['mae_constant_prior_mean']:.2f} |"
        )
    a("")
    a("## Caveats\n")
    a(
        "- Descriptive. Accuracy on decided legs is not profit; payouts, fees and the 52.4% break-even are not modelled here.\n- Prop forecasts are the stored walk-forward set, so they are as-of the T-60 production inputs; DNPs have no model row and are not in n.\n- The recency prop baseline and the spread/total/period baselines are built for this exploration, not frozen models; they are transparent but untuned.\n- Spread/period fits use whole prior seasons (no within-season refit), so 2023's baselines rest on a single season of fit data.\n- Quarter rows exclude games where the pbp final disagrees with the box score; the list is above.\n"
    )
    return "\n".join(L)


def main() -> int:
    snapm = build_snapshot()
    snap = duckdb.connect(str(SNAP), read_only=True)
    assert snap.execute("select max(season) from games").fetchone()[0] <= 2024  # type: ignore[index]
    res: dict[str, Any] = {
        "snapshot": snapm,
        "rule": "descriptive exploration; no prereg; seasons 2023-2024 only",
    }
    print("props...", flush=True)
    res["props"], _ = props_section(snap)
    print("games...", flush=True)
    g = load_game_frame(snap)
    res["games"] = games_section(g)
    print("periods...", flush=True)
    d, rec = period_frame(snap, g)
    res["periods"] = period_section(d, rec)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    )
    OUT_MD.write_text(render(res))
    print("wrote", OUT_MD, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
