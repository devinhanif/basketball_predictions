# ruff: noqa: E501
"""Q1 player points vs the live line, with confirmed starters (docs/prereg/Q1_STARTERS_LIVE_2026_27.md).

Weekly OFFLINE scorer, outside the nightly path. Read-only: it reads stored forecasts, stored prices and
stored outcomes; it never writes ``nba.duckdb`` or ``market_asof.duckdb`` and loads no season-2025 row.
The pure core (everything above ``load_*``) is what the tests exercise on fixtures.

    .venv/bin/python -m nba.eval.q1_starters_live score \
        --forecasts data/ops/q1_live/forecasts.parquet --outcomes data/ops/q1_live/q1_points.parquet \
        --params configs/q1_recal.json --out reports/q1_starters_live/week_YYYYMMDD.json

Stamped inputs (one row per game x player, written by the forecast stamper BEFORE the price is used):
``game_id, player_id, tipoff, base_made_at, base_q (199 integer Q1-points quantiles),
t15_made_at, t15_snapshot_fetched_at, start_status ('starter' | 'bench' | 'out' | null)``.

The three arms are deterministic functions of the stamped inputs and the frozen parameter file:
``base``  = P(Y >= ceil(line)) from the T-60 quantile grid, untouched;
``recal`` = ``base`` through one global logistic recalibration (a, b) frozen from 2023-24 history;
``t15``   = ``base`` through the recalibration of its start-status group (a_s, b_s), same history.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.markets.decision_scoring import Boot, american_to_decimal, choose_side, profit_per_unit

HISTORY_MAX_SEASON = 2024
LIVE_SEASON = 2026
KMAX = 40
CLIP = 1e-4
N_BOOT = 2000
SEED = 20262027
THRESHOLDS: tuple[float, ...] = (0.0, 0.02, 0.05, 0.10)
MIN_BETS = 300  # DECISION_SCORING: below this a cell is "insufficient sample"
GROUPS: tuple[str, ...] = ("starter", "bench")

# Frozen constants of the rule (mirrored in the prereg; a test pins them).
N_MIN_GAMES = 250
N_MIN_LEGS = 2000
N_MIN_DATES = 35
INTERIM_GAMES = 125
FLOOR_P1 = -0.005  # t15 minus recal log loss (information from confirmed starters)
FLOOR_P2 = -0.005  # t15 minus market log loss (beats the line)
PRIMARY_BOOKS: tuple[str, ...] = ("fanduel", "williamhill_us")
GATE_HALFWIDTH = 0.005  # half-width of the P2 game-clustered CI the final look needs


# ------------------------------------------------------------------------------------------ transforms
def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 0.02, 0.98)
    return np.log(p / (1.0 - p))


def expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def pge_from_quantiles(q: np.ndarray, kmax: int = KMAX) -> np.ndarray:
    """``out[i, k-1] = P(Y_i >= k)`` as the share of the quantile grid at or above integer k."""
    q = np.asarray(q)
    return np.stack([(q >= k).mean(axis=1) for k in range(1, kmax + 1)], axis=1)


def p_over(pge: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(Y > line) for half-point lines: ``L = k + 0.5`` gives ``P(Y >= k + 1)``. Whole or odd lines -> NaN."""
    line = np.asarray(line, dtype=float)
    twice = line * 2.0
    ok = (np.abs(twice - np.round(twice)) < 1e-9) & (np.abs(line - np.round(line)) > 1e-9)
    k = np.floor(line).astype(int) + 1
    idx = np.clip(k - 1, 0, pge.shape[1] - 1)
    v = pge[np.arange(len(line)), idx]
    v = np.where(k <= 0, 1.0, np.where(k > pge.shape[1], 0.0, v))
    return np.where(ok, v, np.nan)


def fit_platt(
    p: np.ndarray, y: np.ndarray, iters: int = 50, ridge: float = 1e-6
) -> dict[str, float]:
    """Logistic regression of y on logit(p): (a, b). Newton with a tiny ridge; identity if degenerate."""
    x = logit(p)
    y = np.asarray(y, dtype=float)
    if len(y) < 30 or y.min() == y.max():
        return {"a": 0.0, "b": 1.0, "n": int(len(y))}
    beta = np.array([0.0, 1.0])
    X = np.column_stack([np.ones_like(x), x])
    for _ in range(iters):
        mu = expit(X @ beta)
        W = mu * (1.0 - mu) + 1e-12
        g = X.T @ (y - mu) - ridge * (beta - np.array([0.0, 1.0]))
        H = (X * W[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(H, g)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return {"a": float(beta[0]), "b": float(beta[1]), "n": int(len(y))}


def apply_platt(par: dict[str, float], p: np.ndarray) -> np.ndarray:
    return expit(par["a"] + par["b"] * logit(p))


def fit_params(p: np.ndarray, y: np.ndarray, start: np.ndarray) -> dict[str, Any]:
    """Frozen parameter object: one global (a, b) and one (a_s, b_s) per start group."""
    out: dict[str, Any] = {"global": fit_platt(p, y), "groups": {}}
    for g in GROUPS:
        m = start == g
        out["groups"][g] = fit_platt(p[m], y[m])
    return out


def arm_probs(par: dict[str, Any], p_base: np.ndarray, start: np.ndarray) -> dict[str, np.ndarray]:
    """base / recal / t15 P(over). A row with no usable start status gets the global recal in t15 (and is
    excluded from the primary pairing by the guards, not silently kept)."""
    recal = apply_platt(par["global"], p_base)
    t15 = recal.copy()
    for g in GROUPS:
        m = start == g
        if m.any():
            t15[m] = apply_platt(par["groups"][g], p_base[m])
    return {"base": p_base, "recal": recal, "t15": t15}


# --------------------------------------------------------------------------------------------- metrics
def logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), CLIP, 1.0 - CLIP)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def devig_over(price_over: np.ndarray, price_under: np.ndarray) -> np.ndarray:
    """Proportional de-vig of a two-sided American price pair -> P(over)."""
    do, du = american_to_decimal(price_over), american_to_decimal(price_under)
    io, iu = 1.0 / do, 1.0 / du
    return io / (io + iu)


def cluster_ci(
    values: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict[str, Any]:
    """Mean of ``values`` with a percentile CI from resampling whole clusters (ratio of sums)."""
    values = np.asarray(values, dtype=float)
    _, inv = np.unique(cluster, return_inverse=True)
    G = int(inv.max()) + 1 if len(inv) else 0
    if G < 2:
        return {
            "mean": float(values.mean()) if len(values) else None,
            "lo": None,
            "hi": None,
            "n": len(values),
            "clusters": G,
        }
    s = np.bincount(inv, weights=values, minlength=G)
    c = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    W = rng.multinomial(G, np.full(G, 1.0 / G), size=n_boot).astype(float)
    b = (W @ s) / (W @ c)
    lo, hi = np.percentile(b, [2.5, 97.5])
    return {
        "mean": float(values.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "n": int(len(values)),
        "clusters": G,
    }


def gate_halfwidth(values: np.ndarray, game: np.ndarray) -> float | None:
    """Half-width of the game-clustered 95% CI of a paired per-leg difference (the gate quantity)."""
    ci = cluster_ci(values, game)
    if ci["lo"] is None:
        return None
    return float((ci["hi"] - ci["lo"]) / 2.0)


# ------------------------------------------------------------------------------------------ pbp helper
def q1_points_from_pbp(ev: pl.DataFrame) -> pl.DataFrame:
    """Q1 (period 1) player points from pbp events, the rule of research/eval/half_points.py:
    Made Shot -> shot_value; Free Throw not starting with 'MISS' -> 1."""
    e = ev.filter(
        (pl.col("period") == 1) & pl.col("action_type").is_in(["Made Shot", "Free Throw"])
    )
    pts = (
        pl.when(pl.col("action_type") == "Made Shot")
        .then(pl.col("shot_value"))
        .when(~pl.col("description").str.starts_with("MISS"))
        .then(1)
        .otherwise(0)
    )
    return e.group_by("game_id", "player_id").agg(pts.sum().alias("q1_pts"))


# ------------------------------------------------------------------------------------- legs assembly
@dataclass
class Guards:
    counts: dict[str, int]


def assemble_legs(
    fc: pl.DataFrame,
    odds: pl.DataFrame,
    outcomes: pl.DataFrame,
    books: tuple[str, ...] = PRIMARY_BOOKS,
    kind: str = "t15",
    market: str = "player_points_q1",
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Join stamped forecasts, one price capture kind and the realised Q1 points into one row per
    (game, player, book, line). Every drop is counted by reason; nothing is dropped silently.

    Guards (a row failing one is dropped and counted, never repaired):
      price_not_pre_tip      price ``received_at`` not strictly before tip
      forecast_not_pre_tip   base or t15 ``made_at`` not strictly before tip
      snapshot_not_pre_cutoff  starter snapshot ``fetched_at`` not strictly before tip - 15 min
      base_after_capture     base forecast ``made_at`` not before the price capture ``requested_at``
      no_status / out        start status missing, or the player is OUT at decision time (book voids)
      not_half_point / one_sided / no_outcome
    """
    d = odds.filter(
        (pl.col("snapshot_kind") == kind)
        & (pl.col("market") == market)
        & pl.col("book").is_in(list(books))
        & pl.col("player_id").is_not_null()
    )
    ov = d.filter(pl.col("side") == "over").select(
        "game_id",
        "player_id",
        "book",
        "point",
        "tipoff",
        "requested_at",
        "received_at",
        pl.col("price_american").alias("price_over"),
    )
    un = d.filter(pl.col("side") == "under").select(
        "game_id", "player_id", "book", "point", pl.col("price_american").alias("price_under")
    )
    n_over_rows = ov.height
    j = ov.join(un, on=["game_id", "player_id", "book", "point"], how="inner")
    cnt: dict[str, int] = {"over_rows": n_over_rows, "one_sided": n_over_rows - j.height}
    j = j.with_columns(pl.col("player_id").cast(pl.Int64))
    f = fc.with_columns(pl.col("player_id").cast(pl.Int64))
    j2 = j.join(f.drop("tipoff"), on=["game_id", "player_id"], how="inner")
    cnt["no_forecast"] = j.height - j2.height
    j = j2
    out = j.join(
        outcomes.with_columns(pl.col("player_id").cast(pl.Int64)),
        on=["game_id", "player_id"],
        how="left",
    )
    cnt["no_outcome"] = int(out["q1_pts"].is_null().sum())
    j = out.filter(pl.col("q1_pts").is_not_null())

    def drop(name: str, mask: pl.Expr) -> None:
        nonlocal j
        cnt[name] = int(j.select(mask.sum()).item())
        j = j.filter(~mask)

    drop("price_not_pre_tip", ~(pl.col("received_at") < pl.col("tipoff")))
    drop(
        "forecast_not_pre_tip",
        ~((pl.col("base_made_at") < pl.col("tipoff")) & (pl.col("t15_made_at") < pl.col("tipoff"))),
    )
    drop(
        "snapshot_not_pre_cutoff",
        ~(pl.col("t15_snapshot_fetched_at") < (pl.col("tipoff") - pl.duration(minutes=15))),
    )
    drop("base_after_capture", ~(pl.col("base_made_at") < pl.col("requested_at")))
    drop("no_status", pl.col("start_status").is_null())
    drop("out", pl.col("start_status") == "out")
    drop(
        "not_half_point",
        ((pl.col("point") * 2).round() != pl.col("point") * 2)
        | ((pl.col("point") - pl.col("point").round()).abs() < 1e-9),
    )
    cnt["legs"] = j.height
    return j, cnt


def score_arms(legs: pl.DataFrame, par: dict[str, Any]) -> dict[str, Any]:
    """All metrics for an assembled legs frame. Pure; deterministic given the frame and ``par``."""
    n = legs.height
    if n == 0:
        return {"n_legs": 0, "status": "no legs"}
    q = np.stack(legs["base_q"].to_list()).astype(float)
    line = legs["point"].to_numpy()
    pb = p_over(pge_from_quantiles(q), line)
    y = (legs["q1_pts"].to_numpy() > line).astype(float)
    start = legs["start_status"].to_numpy().astype(str)
    arms = arm_probs(par, pb, start)
    pm = devig_over(legs["price_over"].to_numpy(), legs["price_under"].to_numpy())
    game = legs["game_id"].to_numpy().astype(str)
    date = legs["game_date"].to_numpy().astype(str) if "game_date" in legs.columns else game
    ll = {k: logloss(v, y) for k, v in arms.items()}
    ll["market"] = logloss(pm, y)

    def pair(a: str, b: str) -> dict[str, Any]:
        v = ll[a] - ll[b]
        return {"game_clustered": cluster_ci(v, game), "date_clustered": cluster_ci(v, date)}

    res: dict[str, Any] = {
        "n_legs": int(n),
        "n_games": int(np.unique(game).size),
        "n_dates": int(np.unique(date).size),
        "mean_logloss": {k: float(v.mean()) for k, v in ll.items()},
        "mean_p_over": {
            **{k: float(v.mean()) for k, v in arms.items()},
            "market": float(pm.mean()),
        },
        "over_rate": float(y.mean()),
        "P1_t15_minus_recal": pair("t15", "recal"),
        "P2_t15_minus_market": pair("t15", "market"),
        "S_recal_minus_market": pair("recal", "market"),
        "S_base_minus_market": pair("base", "market"),
        "gate_halfwidth_P2": gate_halfwidth(ll["t15"] - ll["market"], game),
    }
    slices: dict[str, Any] = {}
    for g in GROUPS:
        m = start == g
        if m.sum() > 1:
            slices[f"status={g}"] = {
                "n": int(m.sum()),
                "P1": cluster_ci((ll["t15"] - ll["recal"])[m], game[m]),
                "P2": cluster_ci((ll["t15"] - ll["market"])[m], game[m]),
            }
    for b in sorted(set(legs["book"].to_list())):
        m = legs["book"].to_numpy() == b
        if m.sum() > 1:
            slices[f"book={b}"] = {
                "n": int(m.sum()),
                "P2": cluster_ci((ll["t15"] - ll["market"])[m], game[m]),
            }
    res["slices"] = slices
    res["decision"] = decision_score(legs, arms["t15"], y)
    return res


def decision_score(legs: pl.DataFrame, p_t15: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """DECISION_SCORING at the T-15 actual price: bet the larger-EV side when EV >= threshold; ROI per unit, game-clustered."""
    po, pu = legs["price_over"].to_numpy(), legs["price_under"].to_numpy()
    is_over, ev, _, dec = choose_side(p_t15, po, pu)
    profit = profit_per_unit(is_over, y, dec)
    game = legs["game_id"].to_numpy().astype(str)
    uniq, gi = np.unique(game, return_inverse=True)
    boot = Boot(gi, len(uniq))
    out: dict[str, Any] = {}
    for t in THRESHOLDS:
        sel = (ev >= t).astype(float)
        nb = int(sel.sum())
        if nb == 0:
            out[f"ev>={t}"] = {"n_bets": 0, "status": "no bets"}
            continue
        roi = boot.ratio(profit * sel, sel)
        out[f"ev>={t}"] = {
            "n_bets": nb,
            "roi": roi["est"],
            "roi_lo": roi["lo"],
            "roi_hi": roi["hi"],
            "status": "ok" if nb >= MIN_BETS else "insufficient sample",
        }
    return out


# ------------------------------------------------------------------------------------ frozen outcome
def verdict(res: dict[str, Any]) -> dict[str, Any]:
    """The frozen reading of one weekly result. The rule is in the prereg; this is its only code path.

    accumulating      n_games < INTERIM_GAMES: nothing is read.
    interim_kill_only INTERIM_GAMES <= n_games < the minimum: may only STOP the arm, never claim.
    final             n_games >= N_MIN_GAMES and n_legs >= N_MIN_LEGS and n_dates >= N_MIN_DATES.
    ``beats_the_line`` = P1 and P2 (an intersection of two tests, each at its own 95% CI, so no
    multiplicity correction); both the game- and the date-clustered CI must clear the floor.
    The decision-score cell is descriptive and carries no pass (it cannot pass its own gate).
    """
    ng, nl, nd = res.get("n_games", 0), res.get("n_legs", 0), res.get("n_dates", 0)
    out: dict[str, Any] = {"n_games": ng, "n_legs": nl, "n_dates": nd}
    if nl == 0 or ng < INTERIM_GAMES:
        out["state"] = "accumulating"
        return out

    def clears(block: dict[str, Any], floor: float) -> bool:
        cs = (block["game_clustered"], block["date_clustered"])
        return all(c["hi"] is not None and c["mean"] <= floor and c["hi"] < 0 for c in cs)

    p1, p2 = res["P1_t15_minus_recal"], res["P2_t15_minus_market"]
    if ng < N_MIN_GAMES or nl < N_MIN_LEGS or nd < N_MIN_DATES:
        kills: list[str] = []
        lo1, lo2 = p1["game_clustered"]["lo"], p2["game_clustered"]["lo"]
        if lo1 is not None and lo1 > 0:
            kills.append("P1: t15 reliably worse than recal (CI lower bound > 0)")
        if lo2 is not None and lo2 > 0:
            kills.append("P2: t15 reliably worse than the line (CI lower bound > 0)")
        out["state"] = "interim_kill_only"
        out["kill"] = kills
        return out
    out["state"] = "final"
    out["P1_pass"] = bool(clears(p1, FLOOR_P1))
    out["P2_pass"] = bool(clears(p2, FLOOR_P2))
    out["beats_the_line"] = bool(out["P1_pass"] and out["P2_pass"])
    out["gate_halfwidth_ok"] = bool(
        res.get("gate_halfwidth_P2") is not None and res["gate_halfwidth_P2"] <= GATE_HALFWIDTH
    )
    return out


# ------------------------------------------------------------------------------------- history fit
def fit_history(root: Path) -> dict[str, Any]:
    """Frozen recalibration parameters from 2024 history (the only season with Q1 player quotes),
    plus the descriptive expanding-window (month blocks) walk-forward result. Read-only; season <= 2024.

    Base grid: ``q_b3`` of data/ops/half_points_cache/forecasts.npz (half_points.py method (b)).
    Prices: odds_history ``player_points_q1`` t60, williamhill_us + fanduel. Start group in history is the
    box-score starter flag, a PROXY for the announced starter (live uses the confirmed lineup snapshot)."""
    import duckdb

    z = np.load(root / "data/ops/half_points_cache/forecasts.npz")
    keep = z["season"].astype(int) <= HISTORY_MAX_SEASON
    assert int(z["season"][keep].max()) <= HISTORY_MAX_SEASON
    fc = pl.DataFrame(
        {
            "game_id": z["gid"].astype(str),
            "player_id": z["pid"].astype(np.int64),
            "i": np.arange(len(z["gid"])),
            "q1": z["q1"].astype(int),
        }
    )
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    st = con.execute(
        "SELECT s.game_id, s.player_id, s.starter FROM player_game_stats s JOIN games g USING (game_id) "
        "WHERE g.season <= 2024"
    ).pl()
    gd = con.execute("SELECT game_id, game_date FROM games WHERE season <= 2024").pl()
    con.close()
    o = duckdb.connect(str(root / "data/odds/odds_history.duckdb"), read_only=True)
    d = o.execute(
        "SELECT game_id, player_id, book, side, point, price_american FROM odds_history "
        "WHERE market='player_points_q1' AND snapshot_kind='t60' AND season <= 2024 "
        "AND player_id IS NOT NULL AND book IN ('fanduel','williamhill_us')"
    ).pl()
    o.close()
    ov = d.filter(pl.col("side") == "over").select(
        "game_id", "player_id", "book", "point", pl.col("price_american").alias("po")
    )
    un = d.filter(pl.col("side") == "under").select(
        "game_id", "player_id", "book", "point", pl.col("price_american").alias("pu")
    )
    j = (
        ov.join(un, on=["game_id", "player_id", "book", "point"])
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .join(fc, on=["game_id", "player_id"])
        .join(
            st.with_columns(pl.col("player_id").cast(pl.Int64)),
            on=["game_id", "player_id"],
            how="left",
        )
        .join(gd, on="game_id", how="left")
        .filter(
            ((pl.col("point") * 2).round() == pl.col("point") * 2)
            & ((pl.col("point") - pl.col("point").round()).abs() > 1e-9)
        )
    )
    line = j["point"].to_numpy()
    pb = p_over(pge_from_quantiles(z["q_b3"][j["i"].to_numpy()].astype(float)), line)
    y = (j["q1"].to_numpy() > line).astype(float)
    start = np.where(j["starter"].to_numpy().astype(float) > 0.5, "starter", "bench").astype(str)
    game = j["game_id"].to_numpy().astype(str)
    pm = devig_over(j["po"].to_numpy(), j["pu"].to_numpy())
    mon = np.array([str(x)[:7] for x in j["game_date"].to_numpy()])
    par = fit_params(pb, y, start)
    wf = {k: np.full(len(y), np.nan) for k in ("recal", "t15")}
    for m in sorted(set(mon)):
        tr, te = mon < m, mon == m
        if tr.sum() < 1500:
            continue
        a = arm_probs(fit_params(pb[tr], y[tr], start[tr]), pb[te], start[te])
        for k in wf:
            wf[k][te] = a[k]
    ok = ~np.isnan(wf["recal"])
    ll = {
        "base": logloss(pb[ok], y[ok]),
        "recal": logloss(wf["recal"][ok], y[ok]),
        "t15": logloss(wf["t15"][ok], y[ok]),
        "market": logloss(pm[ok], y[ok]),
    }
    g = game[ok]
    desc: dict[str, Any] = {
        "legs": int(ok.sum()),
        "games": int(np.unique(g).size),
        "mean_logloss": {k: float(v.mean()) for k, v in ll.items()},
        "mean_p_over": {
            "base": float(pb[ok].mean()),
            "recal": float(wf["recal"][ok].mean()),
            "t15": float(wf["t15"][ok].mean()),
            "market": float(pm[ok].mean()),
        },
        "over_rate": float(y[ok].mean()),
        "starter_share_of_legs": float((start[ok] == "starter").mean()),
    }
    for x_arm, y_arm in (
        ("base", "market"),
        ("recal", "market"),
        ("t15", "market"),
        ("t15", "recal"),
        ("recal", "base"),
    ):
        desc[f"{x_arm}_minus_{y_arm}"] = cluster_ci(ll[x_arm] - ll[y_arm], g)
    return {
        "fit_seasons": [2024],
        "source": "q_b3 base grid (half_points method b); odds_history player_points_q1 t60 williamhill_us+fanduel; start = box-score starter flag (history proxy)",
        "n_legs": int(len(y)),
        "n_games": int(np.unique(game).size),
        "params": par,
        "walk_forward_descriptive": desc,
    }


# ---------------------------------------------------------------------------------------------- loaders
def load_params(path: Path) -> dict[str, Any]:
    par = json.loads(path.read_text())
    assert par.get("fit_seasons") and max(par["fit_seasons"]) <= HISTORY_MAX_SEASON
    return par["params"]  # type: ignore[no-any-return]


def run_score(args: argparse.Namespace) -> int:
    fc = pl.read_parquet(args.forecasts)
    outcomes = pl.read_parquet(args.outcomes)
    par = load_params(Path(args.params))
    import duckdb

    con = duckdb.connect(str(args.market_db), read_only=True)
    try:
        odds = con.execute(
            "SELECT game_id, snapshot_kind, market, book, player_id, side, point, price_american, "
            "requested_at, received_at, tipoff FROM live_book_odds "
            "WHERE market = 'player_points_q1' AND snapshot_kind = 't15' AND slate_date >= ?",
            [args.since],
        ).pl()
    finally:
        con.close()
    legs, cnt = assemble_legs(fc, odds, outcomes)
    if "game_date" not in legs.columns:
        legs = legs.with_columns(pl.col("tipoff").dt.date().cast(pl.Utf8).alias("game_date"))
    res = score_arms(legs, par)
    res["guards"] = cnt
    res["verdict"] = verdict(res)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res["verdict"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--forecasts", required=True)
    s.add_argument("--outcomes", required=True)
    s.add_argument("--params", default="configs/q1_recal.json")
    s.add_argument("--market-db", default="data/markets/market_asof.duckdb")
    s.add_argument("--since", default="2026-10-20")
    s.add_argument("--out", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--out", default="configs/q1_recal.json")
    a = ap.parse_args(argv)
    if a.cmd == "fit":
        root = Path(__file__).resolve().parents[2]
        res = fit_history(root)
        Path(a.out).write_text(json.dumps(res, indent=2))
        print(json.dumps(res["walk_forward_descriptive"]["t15_minus_market"]))
        return 0
    return run_score(a)


if __name__ == "__main__":
    raise SystemExit(main())
