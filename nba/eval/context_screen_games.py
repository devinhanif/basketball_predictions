"""Exploratory screen: does game location / tip time / market size explain what
injury-adjusted Elo (``rung0_injury_elo``) misses?  EXPLORATORY, seasons 2022-24 ONLY.

PRE-REGISTRATION (written before the first run of this script; do not edit after results)
------------------------------------------------------------------------------------------
Family ``CTX_GAMES_2026-10-08``; m = 14 tests; discovery rule BH at q = 0.10 (exploratory).

Target: home win ``y`` (home_pts > away_pts) against the out-of-fold probability ``p`` from
``data/injury_elo/oof_predictions.parquet`` (model ``rung0_injury_elo``, seasons 2022-2024;
season 2025 is the burned holdout and is asserted absent).

Test (identical for every feature x): fit  logit P(y=1) = a + b*logit(p) + c*z(x)  with
z() the standardised feature, against the base model  a + b*logit(p).  Statistic = c
(log-odds per 1 SD of the feature, "does the feature add information beyond the model").
Effect size = in-sample per-game log-loss improvement  dLL = LL(base) - LL(full)  (>0 good).
Inference: bootstrap, B = 2000, two schemes
  (G) game-clustered (each game is its own cluster, i.e. a row bootstrap) -> p-value used for BH;
  (T) home-team x season clustered (robustness; residuals share team strength) -> CI only.
Two-sided bootstrap p = 2*min(P*(c*<=0), P*(c*>=0)) with +1 smoothing.
Survivor = BH-reject on (G) at q=0.10  AND  dLL >= 0.001  (effect floor).  A survivor whose
(T) 95% CI includes 0 is labelled "fragile".  Survivors are CANDIDATE features only.

The 14 tests (feature definitions in nba/features/game_location_context.py):
  1 home_altitude_km        2 altitude_diff_km       3 home_log_pop         4 away_log_pop
  5 log_pop_gap             6 away_bodyclock_hr      7 tz_shift_east_hr     8 west_to_east_early
  9 tip_matinee            10 tip_early             11 tip_late            12 is_neutral
 13 days_into_season      14 is_national_tv         (tip_standard is the reference level)
Descriptive tables (home win rate / mean p / mean residual / log-loss by altitude group, tip
bucket, market-size tercile, west-to-east early flag) are NOT tests and are labelled so.

Run:  uv run python -m nba.eval.context_screen_games
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from nba.features.game_location_context import (
    build_context_features,
    build_schedule_table,
    fetch_raw_schedules,
    load_team_markets,
)

SEED = 20261008
B = 2000
Q = 0.10
FLOOR = 0.001
MODEL = "rung0_injury_elo"
FEATURES = [
    "home_altitude_km",
    "altitude_diff_km",
    "home_log_pop",
    "away_log_pop",
    "log_pop_gap",
    "away_bodyclock_hr",
    "tz_shift_east_hr",
    "west_to_east_early",
    "tip_matinee",
    "tip_early",
    "tip_late",
    "is_neutral",
    "days_into_season",
    "is_national_tv",
]
M_TESTS = len(FEATURES)
assert M_TESTS == 14


def _fit(
    X: np.ndarray, y: np.ndarray, w: np.ndarray, beta0: np.ndarray | None = None
) -> tuple[np.ndarray, float]:
    """Weighted logistic regression by Newton; returns (beta, weighted mean log-likelihood)."""
    beta = np.zeros(X.shape[1]) if beta0 is None else beta0.copy()
    for _ in range(30):
        eta = X @ beta
        pr = 1.0 / (1.0 + np.exp(-eta))
        g = X.T @ (w * (y - pr)) - 1e-8 * beta
        h = (X * (w * pr * (1 - pr))[:, None]).T @ X + 1e-8 * np.eye(X.shape[1])
        step = np.linalg.solve(h, g)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    eta = X @ beta
    ll = float(np.sum(w * (y * eta - np.logaddexp(0.0, eta))) / np.sum(w))
    return beta, ll


def _stat(lp: np.ndarray, z: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    ones = np.ones_like(lp)
    b0, ll0 = _fit(np.column_stack([ones, lp]), y, w)
    b1, ll1 = _fit(np.column_stack([ones, lp, z]), y, w, np.array([b0[0], b0[1], 0.0]))
    return float(b1[2]), ll1 - ll0


def _cluster_weights(rng: np.random.Generator, codes: np.ndarray, n_cl: int) -> np.ndarray:
    counts = np.bincount(rng.integers(0, n_cl, n_cl), minlength=n_cl)
    return np.asarray(counts[codes], dtype=float)


def _boot(
    lp: np.ndarray, z: np.ndarray, y: np.ndarray, codes: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    n_cl = int(codes.max()) + 1
    out = np.empty(B)
    for i in range(B):
        w = _cluster_weights(rng, codes, n_cl)
        out[i] = _stat(lp, z, y, w)[0]
    return out


def _p_two_sided(draws: np.ndarray) -> float:
    lo = (np.sum(draws <= 0) + 1) / (len(draws) + 1)
    hi = (np.sum(draws >= 0) + 1) / (len(draws) + 1)
    return float(min(1.0, 2 * min(lo, hi)))


def bh_reject(pvals: list[float], q: float) -> list[bool]:
    """Benjamini-Hochberg step-up; returns reject flags in the input order."""
    m = len(pvals)
    order = np.argsort(pvals)
    k = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= q * rank / m:
            k = rank
    rej = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= k:
            rej[idx] = True
    return rej


def load_frame(db_path: str, oof_path: str, raw_dir: str, markets_path: str) -> pl.DataFrame:
    """Join OOF predictions, outcomes, national TV and schedule context (2022-24 only)."""
    oof = pl.read_parquet(oof_path).filter(pl.col("model") == MODEL)
    assert set(oof["season"].unique().to_list()) <= {2022, 2023, 2024}, "holdout season present"
    assert oof["game_id"].n_unique() == oof.height
    con = duckdb.connect(db_path, read_only=True)
    g = pl.from_arrow(
        con.execute(
            "SELECT game_id, home_team AS home_id, (home_pts > away_pts)::INT AS y, "
            "(national_tv IS NOT NULL AND national_tv != '')::INT AS is_national_tv FROM games"
        ).arrow()
    )
    con.close()
    assert isinstance(g, pl.DataFrame)
    teams = load_team_markets(markets_path)
    feats = build_context_features(build_schedule_table(raw_dir), teams)
    df = oof.select("game_id", "game_date", "season", "p").join(g, on="game_id", how="inner")
    assert df.height == oof.height, "OOF games missing from games table"
    n0 = df.height
    df = df.join(feats, on="game_id", how="inner")
    assert df.height == n0, f"{n0 - df.height} OOF games missing from schedule"
    assert df["home_id"].to_list() == df["home_team"].to_list(), "home team mismatch"
    return df.with_columns(
        (pl.col("tip_bucket") == "matinee").cast(pl.Int8).alias("tip_matinee"),
        (pl.col("tip_bucket") == "early").cast(pl.Int8).alias("tip_early"),
        (pl.col("tip_bucket") == "late").cast(pl.Int8).alias("tip_late"),
    )


def run_tests(df: pl.DataFrame) -> list[dict[str, object]]:
    rng = np.random.default_rng(SEED)
    p = np.clip(df["p"].to_numpy(), 1e-6, 1 - 1e-6)
    lp = np.log(p / (1 - p))
    y = df["y"].to_numpy().astype(float)
    codes_g = np.arange(df.height)
    ts = (df["home_team"].cast(pl.Utf8) + "_" + df["season"].cast(pl.Utf8)).to_numpy()
    codes_t = np.unique(ts, return_inverse=True)[1]
    res: list[dict[str, object]] = []
    for f in FEATURES:
        x = df[f].to_numpy().astype(float)
        sd = x.std()
        z = (x - x.mean()) / sd if sd > 0 else x * 0.0
        c, dll = _stat(lp, z, y, np.ones(df.height))
        bg = _boot(lp, z, y, codes_g, rng)
        bt = _boot(lp, z, y, codes_t, rng)
        res.append(
            {
                "feature": f,
                "n_unique": int(len(np.unique(x))),
                "coef_per_sd": c,
                "dLL_per_game": dll,
                "ci_game": [float(np.percentile(bg, 2.5)), float(np.percentile(bg, 97.5))],
                "ci_teamseason": [float(np.percentile(bt, 2.5)), float(np.percentile(bt, 97.5))],
                "p_game": _p_two_sided(bg),
                "p_teamseason": _p_two_sided(bt),
            }
        )
    rej = bh_reject([float(r["p_game"]) for r in res], Q)  # type: ignore[arg-type]
    for r, rj in zip(res, rej, strict=True):
        ci_t = r["ci_teamseason"]
        assert isinstance(ci_t, list)
        surv = bool(rj) and float(r["dLL_per_game"]) >= FLOOR  # type: ignore[arg-type]
        r["bh_reject"] = bool(rj)
        r["survivor"] = surv
        r["fragile"] = surv and ci_t[0] <= 0 <= ci_t[1]
    return res


def _grp(
    df: pl.DataFrame, label: str, mask: np.ndarray, rng: np.random.Generator
) -> dict[str, object]:
    sub = df.filter(pl.Series(mask))
    y, p = sub["y"].to_numpy().astype(float), np.clip(sub["p"].to_numpy(), 1e-6, 1 - 1e-6)
    resid = y - p
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    n = len(y)
    if n < 2:
        return {"group": label, "n": n}
    idx = rng.integers(0, n, (1000, n))
    bs = resid[idx].mean(axis=1)
    return {
        "group": label,
        "n": n,
        "home_win_rate": float(y.mean()),
        "mean_p": float(p.mean()),
        "mean_resid": float(resid.mean()),
        "resid_ci": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
        "mean_logloss": float(ll.mean()),
    }


def descriptives(df: pl.DataFrame) -> list[dict[str, object]]:
    """DESCRIPTIVE only (not tests, not in the BH family)."""
    rng = np.random.default_rng(SEED + 1)
    out: list[dict[str, object]] = []
    alt = df["home_altitude_km"].to_numpy()
    out.append(_grp(df, "altitude: home >=1.0km (DEN, UTA)", alt >= 1.0, rng))
    out.append(_grp(df, "altitude: home <1.0km", alt < 1.0, rng))
    out.append(
        _grp(df, "altitude: home 0.3-1.0km (PHX, OKC, ATL)", (alt >= 0.3) & (alt < 1.0), rng)
    )
    tb = df["tip_bucket"].to_numpy()
    for b in ("matinee", "early", "standard", "late"):
        out.append(_grp(df, f"tip bucket: {b}", tb == b, rng))
    pop = df["home_log_pop"].to_numpy()
    t1, t2 = np.quantile(pop, [1 / 3, 2 / 3])
    out.append(_grp(df, "home market tercile: small", pop <= t1, rng))
    out.append(_grp(df, "home market tercile: mid", (pop > t1) & (pop <= t2), rng))
    out.append(_grp(df, "home market tercile: large", pop > t2, rng))
    gap = df["log_pop_gap"].to_numpy()
    out.append(_grp(df, "market gap: home much larger (gap>0.5)", gap > 0.5, rng))
    out.append(_grp(df, "market gap: home much smaller (gap<-0.5)", gap < -0.5, rng))
    wte = df["west_to_east_early"].to_numpy()
    out.append(_grp(df, "west->east early (visitor body-clock <17h, shift>=2h)", wte == 1, rng))
    out.append(_grp(df, "not west->east early", wte == 0, rng))
    out.append(_grp(df, "neutral site", df["is_neutral"].to_numpy() == 1, rng))
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    raw_dir = root / "data" / "schedule"
    fetch_raw_schedules(raw_dir)
    df = load_frame(
        str(root / "nba.duckdb"),
        str(root / "data" / "injury_elo" / "oof_predictions.parquet"),
        str(raw_dir),
        str(root / "configs" / "team_markets.yaml"),
    )
    feats = build_context_features(
        build_schedule_table(raw_dir), load_team_markets(root / "configs" / "team_markets.yaml")
    )
    feats.write_parquet(raw_dir / "game_context_features.parquet")
    print("n_games", df.height, "mean_y", df["y"].mean(), "mean_p", df["p"].mean())
    res = run_tests(df)
    desc = descriptives(df)
    out_dir = root / "reports" / "context_screen_games"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "B": B,
                "q": Q,
                "floor": FLOOR,
                "m": M_TESTS,
                "n": df.height,
                "tests": res,
                "descriptive": desc,
            },
            indent=2,
        )
    )
    for r in res:
        print(
            f"{r['feature']:20s} c={r['coef_per_sd']:+.4f} dLL={r['dLL_per_game']:+.5f} "
            f"pG={r['p_game']:.4f} pT={r['p_teamseason']:.4f} "
            f"{'SURVIVOR' if r['survivor'] else ''}{' (fragile)' if r['fragile'] else ''}"
        )
    for d in desc:
        print(d)


if __name__ == "__main__":
    main()
