# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var,var-annotated"
"""SEQ_FULL_MIX descriptive outputs (docs/prereg/SEQ_FULL_MIX.md, "Descriptive outputs (no bearing on the verdict)").

Runs ONLY after the verdict JSON exists, inside the same single run, reads it and never writes it. Everything here is descriptive: per-row export,
confusion matrices, a spot-check pack and a user simulation. No rule, no claim, no pass criterion. It is the only code that reads season-2025 prop
prices from ``data/odds/odds_history.duckdb`` (the market loaders of ``odds_history_eval`` refuse 2025 by design; this path is separate and guarded
by the verdict file).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from nba.eval.context_residual_eval import crps_from_quantiles
from nba.eval.lower_tail_eval import randomized_pit
from research.eval import model_clv_eval as MC
from research.eval import odds_history_eval as OH

ARMS = ("A0", "FULL", "AVG")
STATS = ("pts", "reb", "ast", "fg3m")
BOOKS = ("pinnacle", "draftkings")
QTAUS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-0.05", 0.0, 0.05),
    ("0.05-0.10", 0.05, 0.10),
    ("0.10-0.20", 0.10, 0.20),
    ("0.20-0.50", 0.20, 0.51),
)
PERSONAS = ("P0", "P1", "P2", "P3", "P4")
FLAT_STAKE = 20.0
EDGE_P1, EDGE_P2 = 0.03, 0.06
BANKROLL0, KELLY_FRAC, KELLY_CAP = 500.0, 0.25, 20.0
PARLAY_STAKE = 10.0
N_BOOT, SEED = 2000, 0
SLICES = ("all", "starters", "bench", "p_play<0.9", "second_night_b2b", "teammate_returns")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] descriptive: {msg}", flush=True)


# --------------------------------------------------------------------------- pure helpers
def grid_q(qi: np.ndarray, tau: float) -> np.ndarray:
    """Quantile ``tau`` of an equal-mass integer grid (n, K)."""
    k = qi.shape[1]
    return qi[:, int(np.clip(np.ceil(tau * k) - 1, 0, k - 1))]


def p_over_line(qi: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(Y > line) on the integer grid (lines are x.5, so no push)."""
    return (qi > np.asarray(line, dtype=float)[:, None]).mean(axis=1)


def dec_odds(american: np.ndarray) -> np.ndarray:
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, 1.0 + a / 100.0, 1.0 + 100.0 / np.abs(a))


def confusion(pred_over: np.ndarray, actual_over: np.ndarray) -> dict[str, Any]:
    """Counts with 'over' as the positive class."""
    p, a = np.asarray(pred_over, dtype=bool), np.asarray(actual_over, dtype=bool)
    tp, fp, fn, tn = (
        int((p & a).sum()),
        int((p & ~a).sum()),
        int((~p & a).sum()),
        int((~p & ~a).sum()),
    )
    n = tp + fp + fn + tn
    return {
        "n": n, "tp_over_over": tp, "fp_pred_over_was_under": fp, "fn_pred_under_was_over": fn, "tn_under_under": tn,
        "accuracy": (tp + tn) / n if n else None,
        "precision_over": tp / (tp + fp) if tp + fp else None, "recall_over": tp / (tp + fn) if tp + fn else None,
        "precision_under": tn / (tn + fn) if tn + fn else None, "recall_under": tn / (tn + fp) if tn + fp else None,
    }  # fmt: skip


def acc_ci(boot: MC.Boot, hit: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    m = boot.mean(hit.astype(float), mask)
    return {k: m.get(k) for k in ("mean", "lo", "hi", "n")}


def choose_side(
    p_arm_over: np.ndarray, pm_over: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(bet over?, edge = |p_arm - p_market| on the chosen side, arm P of the chosen side)."""
    over = p_arm_over >= pm_over
    return over, np.abs(p_arm_over - pm_over), np.where(over, p_arm_over, 1.0 - p_arm_over)


def ratio_ci(gi: np.ndarray, n_games: int, num: np.ndarray, den: np.ndarray) -> dict[str, Any]:
    """sum(num) / sum(den) with a game-clustered percentile bootstrap (games resampled, 2000 draws, seed 0)."""
    if len(num) == 0 or den.sum() <= 0:
        return {"roi": None, "lo": None, "hi": None}
    w = (
        np.random.default_rng(SEED)
        .multinomial(n_games, np.full(n_games, 1.0 / n_games), size=N_BOOT)
        .astype(float)
    )
    s, c = (
        np.bincount(gi, weights=num, minlength=n_games),
        np.bincount(gi, weights=den, minlength=n_games),
    )
    d = w @ c
    b = (w @ s) / np.where(d > 0, d, np.nan)
    b = b[np.isfinite(b)]
    lo, hi = np.percentile(b, [2.5, 97.5]) if len(b) else (None, None)
    return {
        "roi": float(num.sum() / den.sum()),
        "lo": None if lo is None else float(lo),
        "hi": None if hi is None else float(hi),
    }


# --------------------------------------------------------------------------- personas
def simulate(
    legs: dict[str, np.ndarray], p_arm_over: np.ndarray, persona: str
) -> dict[str, np.ndarray]:
    """Bets of one persona on one book's legs. ``legs``: po, pu (american prices), pm (de-vigged P(over)), over (1/0), night (sortable int), gi (game cluster), idx
    (stable order). Returns arrays night, gi, stake, pnl in chronological order. One bet per leg (a leg is one player-stat-night at the T-60 price)."""
    n = len(p_arm_over)
    order = np.lexsort((legs["idx"], legs["night"]))
    over_side, edge, p_side = choose_side(p_arm_over, legs["pm"])
    dec_side = dec_odds(np.where(over_side, legs["po"], legs["pu"]))
    won = np.where(over_side, legs["over"] == 1, legs["over"] == 0)
    empty = {k: np.zeros(0) for k in ("night", "gi", "stake", "pnl")}
    if persona == "P0" or n == 0:
        return empty
    if persona in ("P1", "P2"):
        thr = EDGE_P1 if persona == "P1" else EDGE_P2
        sel = order[edge[order] >= thr - 1e-12]
        stake = np.full(len(sel), FLAT_STAKE)
        pnl = np.where(won[sel], stake * (dec_side[sel] - 1.0), -stake)
        return {"night": legs["night"][sel], "gi": legs["gi"][sel], "stake": stake, "pnl": pnl}
    if persona == "P3":
        f_over = (((dec_odds(legs["po"]) - 1.0) * p_arm_over) - (1.0 - p_arm_over)) / (
            dec_odds(legs["po"]) - 1.0
        )
        f_under = (((dec_odds(legs["pu"]) - 1.0) * (1.0 - p_arm_over)) - p_arm_over) / (
            dec_odds(legs["pu"]) - 1.0
        )
        use_over = f_over >= f_under
        f = np.where(use_over, f_over, f_under)
        dec_k = dec_odds(np.where(use_over, legs["po"], legs["pu"]))
        won_k = np.where(use_over, legs["over"] == 1, legs["over"] == 0)
        bank = BANKROLL0
        out: dict[str, list[np.ndarray]] = {k: [] for k in ("night", "gi", "stake", "pnl")}
        nights = legs["night"][order]
        for nt in np.unique(nights):
            idx = order[nights == nt]
            idx = idx[f[idx] > 0]
            if len(idx) == 0:
                continue
            stake = np.minimum(KELLY_CAP, KELLY_FRAC * f[idx] * bank)
            if stake.sum() > bank:  # a night cannot stake more than the bankroll held at its start
                stake = stake * (bank / stake.sum())
            pnl = np.where(won_k[idx], stake * (dec_k[idx] - 1.0), -stake)
            bank += float(pnl.sum())
            for k, v in (
                ("night", legs["night"][idx]),
                ("gi", legs["gi"][idx]),
                ("stake", stake),
                ("pnl", pnl),
            ):
                out[k].append(v)
        return {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in out.items()}
    if persona == "P4":
        res: dict[str, list[float]] = {k: [] for k in ("night", "gi", "stake", "pnl")}
        nights = legs["night"][order]
        for nt in np.unique(nights):
            idx = order[nights == nt]
            idx = idx[edge[idx] > 0]
            if len(idx) < 2:
                continue
            idx = idx[np.argsort(-edge[idx], kind="stable")]
            first = idx[0]
            rest = [j for j in idx[1:] if legs["gi"][j] != legs["gi"][first]]
            if not rest:
                continue
            second = rest[0]
            dec = dec_side[first] * dec_side[second]
            ok = bool(won[first] and won[second])
            res["night"].append(float(nt))
            res["gi"].append(
                float(legs["gi"][first])
            )  # a parlay spans two games; clustered on the first leg's game (disclosed)
            res["stake"].append(PARLAY_STAKE)
            res["pnl"].append(PARLAY_STAKE * (dec - 1.0) if ok else -PARLAY_STAKE)
        return {k: np.array(v) for k, v in res.items()} | {
            "gi": np.array(res["gi"], dtype=np.int64)
        }
    raise ValueError(persona)


def summarize_bets(
    bets: dict[str, np.ndarray], n_games: int, persona: str
) -> tuple[dict[str, Any], pd.DataFrame]:
    n = len(bets["pnl"])
    stake, pnl = bets["stake"], bets["pnl"]
    rec: dict[str, Any] = {
        "persona": persona, "n_bets": int(n), "staked": float(stake.sum()), "pnl": float(pnl.sum()),
        **(ratio_ci(bets["gi"].astype(np.int64), n_games, pnl, stake) if n else {"roi": None, "lo": None, "hi": None}),
    }  # fmt: skip
    if n == 0:
        return {
            **rec,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "share_months_profit": None,
            "n_months": 0,
        }, pd.DataFrame(columns=["night", "pnl", "cum_pnl", "bankroll"])
    df = (
        pd.DataFrame({"night": bets["night"].astype(np.int64), "pnl": pnl, "stake": stake})
        .groupby("night", as_index=False)
        .sum()
    )
    df["cum_pnl"] = df["pnl"].cumsum()
    df["bankroll"] = (BANKROLL0 + df["cum_pnl"]) if persona == "P3" else np.nan
    path = np.concatenate([[0.0], df["cum_pnl"].to_numpy()])
    rec["max_drawdown"] = float((np.maximum.accumulate(path) - path).max())
    streak = best = 0
    for v in pnl:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)
    rec["longest_losing_streak"] = int(best)
    month = pd.to_datetime(df["night"], unit="D").dt.to_period("M")
    mp = df.groupby(month)["pnl"].sum()
    rec["share_months_profit"] = float((mp > 0).mean())
    rec["n_months"] = int(len(mp))
    return rec, df[["night", "pnl", "cum_pnl", "bankroll"]]


# --------------------------------------------------------------------------- market, names, context
def load_quotes_any(season: int, odds_db: Path) -> pl.DataFrame:
    """Prop quotes of any season (the study loader asserts 2023/24). Prices only; no model information."""
    import duckdb

    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        markets = ",".join(f"'{m}'" for m in OH.MARKET_STAT)
        return con.execute(
            "SELECT game_id, snapshot_kind, snapshot_ts, requested_at, book, market, player_name, player_id, side, point, price_american, implied_prob "
            f"FROM odds_history WHERE season = {int(season)} AND snapshot_kind = 't60' AND book IN ('pinnacle','draftkings') AND market IN ({markets})"
        ).pl()
    finally:
        con.close()


def market_legs(season: int, odds_db: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    q = load_quotes_any(season, odds_db)
    names = (
        q.filter(pl.col("player_id").is_not_null())
        .select(pl.col("player_id").cast(pl.Int64), "player_name")
        .unique("player_id")
    )
    built = OH.build_lines(q)
    t = OH.market_table(built["good"]).filter(
        pl.col("book").is_in(list(BOOKS)) & (pl.col("snapshot_kind") == "t60")
    )
    t = t.select(
        "game_id",
        pl.col("player_id").cast(pl.Int64),
        "stat",
        "book",
        "line",
        "price_over",
        "price_under",
        pl.col("p_over_mult").alias("pm"),
    )
    return t.filter(pl.col("line") % 1 == 0.5), names


def context_flags(pgs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Per (game_id, player_id) of ``pgs``: second night of a back-to-back (the team played the previous calendar day) and 'a teammate who averaged >= 30
    minutes over his last 10 appearances is back after >= 3 missed team games'. The latter is ex-post (needs who played) and is a descriptive slice only.
    pgs: game_id, player_id, team_id, minutes; games: game_id, game_date, home_team, away_team."""
    g = games.copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    tg = pd.concat(
        [
            g[["game_id", "game_date", "home_team"]].rename(columns={"home_team": "team_id"}),
            g[["game_id", "game_date", "away_team"]].rename(columns={"away_team": "team_id"}),
        ]
    )
    tg = tg.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    tg["tgi"] = tg.groupby("team_id").cumcount()
    tg["b2b"] = (tg["game_date"] - tg.groupby("team_id")["game_date"].shift(1)).dt.days == 1
    p = pgs.merge(tg, on=["game_id", "team_id"], how="left")
    p["played"] = p["minutes"].fillna(0) > 0
    a = p[p["played"]].sort_values(["player_id", "game_date", "game_id"]).copy()
    a["prev_tgi"] = a.groupby("player_id")["tgi"].shift(1)
    a["prev_team"] = a.groupby("player_id")["team_id"].shift(1)
    a["avg10"] = a.groupby("player_id")["minutes"].transform(
        lambda s: s.shift(1).rolling(10, min_periods=1).mean()
    )
    a["missed"] = np.where(a["prev_team"] == a["team_id"], a["tgi"] - a["prev_tgi"] - 1, 0)
    a["returning"] = (a["missed"] >= 3) & (a["avg10"] >= 30) & a["prev_tgi"].notna()
    ret = a.groupby(["game_id", "team_id"])["returning"].sum().rename("n_ret").reset_index()
    a = a.merge(ret, on=["game_id", "team_id"], how="left")
    a["teammate_returns"] = (a["n_ret"] - a["returning"].astype(int)) > 0
    out = p[["game_id", "player_id", "b2b", "team_id"]].merge(
        a[["game_id", "player_id", "teammate_returns"]], on=["game_id", "player_id"], how="left"
    )
    out["teammate_returns"] = out["teammate_returns"].fillna(False).astype(bool)
    out["b2b"] = out["b2b"].fillna(False).astype(bool)
    return out.rename(columns={"b2b": "second_night_b2b"})


def load_pplay(cache_dir: Path) -> pl.DataFrame | None:
    """P(play) of the RECENT_ROLE_HOLDOUT model R (2025 only, Cell P rows), else None. Predictions only."""
    fs = sorted(cache_dir.glob("block_*.npz"))
    if not fs:
        return None
    parts = [
        pl.DataFrame(
            {
                "game_id": z["gid"].astype(str),
                "player_id": z["pid"].astype(np.int64),
                "p_play": z["p_R"].astype(float),
            }
        )
        for z in (np.load(f, allow_pickle=True) for f in fs)
    ]
    return pl.concat(parts).unique(["game_id", "player_id"])


# --------------------------------------------------------------------------- per-row table
def build_rows(
    world: dict[str, Any],
    stat: str,
    season: int,
    mk: pl.DataFrame,
    names: pl.DataFrame,
    ctx: pd.DataFrame,
    pplay: pl.DataFrame | None,
    games: pd.DataFrame,
) -> pl.DataFrame:
    b = world["base"][stat]
    m = b["season"] == season
    arms = {a: world["_arms"][stat][a][m] for a in ARMS}
    y = b["y"][m]
    df: dict[str, Any] = {
        "game_id": b["game_id"][m], "date": world["date_days"][m].astype("datetime64[D]").astype(str), "player_id": b["player_id"][m].astype(np.int64),
        "team_id": b["team_id"][m].astype(np.int64), "stat": stat, "actual": y, "minutes": b["minutes"][m], "starter10": b["starter10"][m],
        "starter": b["starter10"][m] >= 0.5,
    }  # fmt: skip
    for a, qi in arms.items():
        for t in QTAUS:
            df[f"{a}_q{int(round(t * 100)):02d}"] = grid_q(qi, t)
        df[f"{a}_mean"] = qi.mean(axis=1)
        df[f"{a}_crps"] = crps_from_quantiles(qi.astype(float), y)
        df[f"{a}_pit"] = randomized_pit(qi.astype(float), y, 0)
    t = pl.DataFrame(df)
    g = pl.from_pandas(games[["game_id", "home_team", "away_team"]]).unique("game_id")
    t = (
        t.join(g, on="game_id", how="left")
        .with_columns(
            pl.when(pl.col("team_id") == pl.col("home_team"))
            .then(pl.col("away_team"))
            .otherwise(pl.col("home_team"))
            .alias("opp_id")
        )
        .drop("home_team", "away_team")
    )
    t = t.join(names, on="player_id", how="left").rename({"player_name": "player"})
    t = t.join(
        pl.from_pandas(ctx[["game_id", "player_id", "second_night_b2b", "teammate_returns"]]),
        on=["game_id", "player_id"],
        how="left",
    )
    t = (
        t.join(pplay, on=["game_id", "player_id"], how="left")
        if pplay is not None
        else t.with_columns(pl.lit(None, dtype=pl.Float64).alias("p_play"))
    )
    for book in BOOKS:
        k = mk.filter((pl.col("book") == book) & (pl.col("stat") == stat)).select(
            "game_id",
            "player_id",
            pl.col("line").alias(f"{book}_line"),
            pl.col("price_over").alias(f"{book}_price_over"),
            pl.col("price_under").alias(f"{book}_price_under"),
            pl.col("pm").alias(f"{book}_p_over_devig"),
        )
        t = t.join(k, on=["game_id", "player_id"], how="left")
        line = t[f"{book}_line"].to_numpy().astype(float)
        has = np.isfinite(line)
        for a, qi in arms.items():
            p = np.full(len(t), np.nan)
            if has.any():
                p[has] = p_over_line(qi[has], line[has])
            t = t.with_columns(pl.Series(f"{a}_p_over_{book}", p))
    return t


# --------------------------------------------------------------------------- confusion matrices
def confusion_report(rows: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    gid = rows["game_id"].to_numpy()
    _, gi = np.unique(gid, return_inverse=True)
    boot = MC.Boot(gi, int(gi.max()) + 1)
    for book in BOOKS:
        line = rows[f"{book}_line"].to_numpy().astype(float)
        y = rows["actual"].to_numpy().astype(float)
        has = np.isfinite(line)
        over = y > line
        pm = rows[f"{book}_p_over_devig"].to_numpy().astype(float)
        for stat in (*STATS, "all"):
            sm = has & ((rows["stat"].to_numpy() == stat) if stat != "all" else True)
            rec: dict[str, Any] = {"n_rows": int(sm.sum())}
            preds = {a: rows[f"{a}_p_over_{book}"].to_numpy().astype(float) for a in ARMS} | {
                "market_devig": pm
            }
            for name, p in preds.items():
                pred = p > 0.5
                c1 = confusion(pred[sm], over[sm])
                c1["accuracy_ci"] = acc_ci(boot, pred == over, sm)
                rec[f"{name}_at_line"] = c1
            for a in ARMS:  # (ii) direction implied by the arm's own median against the line
                med = rows[f"{a}_q50"].to_numpy().astype(float)
                pred = med > line
                c2 = confusion(pred[sm], over[sm])
                c2["accuracy_ci"] = acc_ci(boot, pred == over, sm)
                rec[f"{a}_median_vs_line"] = c2
            for (
                name,
                p,
            ) in preds.items():  # (iii) by confidence |P - 0.5| of the arm's own P(over line)
                conf = np.abs(p - 0.5)
                bk: dict[str, Any] = {}
                for lab, lo, hi in BUCKETS:
                    mk_ = sm & (conf >= lo) & (conf < hi)
                    pred = p > 0.5
                    c = confusion(pred[mk_], over[mk_])
                    c["hit_ci"] = acc_ci(boot, pred == over, mk_)
                    bk[lab] = c
                rec[f"{name}_by_confidence"] = bk
            out.setdefault(book, {})[stat] = rec
    return out


# --------------------------------------------------------------------------- spot-check pack
def spot_md(rows: pl.DataFrame, k: int = 10) -> str:
    df = rows.to_pandas()
    df["line"] = df["pinnacle_line"].fillna(df["draftkings_line"])
    df["name"] = df["player"].fillna(df["player_id"].astype(str))
    sl = {
        "all": np.ones(len(df), bool), "starters": df["starter"].to_numpy(), "bench": ~df["starter"].to_numpy(),
        "p_play<0.9": (df["p_play"] < 0.9).fillna(False).to_numpy(), "second_night_b2b": df["second_night_b2b"].fillna(False).astype(bool).to_numpy(),
        "teammate_returns": df["teammate_returns"].fillna(False).astype(bool).to_numpy(),
    }  # fmt: skip
    lines = [
        "# SEQ_FULL_MIX spot-check pack (DESCRIPTIVE; no bearing on the verdict)",
        "",
        "diff = CRPS(AVG) - CRPS(comparator) on the integer grid; negative = AVG better. q = integer quantiles of AVG and of the comparator; line = Pinnacle T-60 else DK T-60 (blank if none).",
        "",
    ]
    for stat in STATS:
        for comp in ("FULL", "A0"):
            d = df["AVG_crps"] - df[f"{comp}_crps"]
            for sname, sm in sl.items():
                sel = (df["stat"] == stat).to_numpy() & sm
                n = int(sel.sum())
                lines += [f"## {stat}: AVG vs {comp}, slice {sname} (n rows {n})", ""]
                if n == 0:
                    lines += ["_no rows_", ""]
                    continue
                for title, asc in (
                    (f"AVG beat {comp} most", True),
                    (f"AVG lost to {comp} most", False),
                ):
                    top = df[sel].assign(diff=d[sel]).sort_values("diff", ascending=asc).head(k)
                    lines += [
                        f"**{title}**",
                        "",
                        f"| date | player | team vs opp | line | actual | min | AVG q10/q50/q90 | {comp} q10/q50/q90 | diff |",
                        "|---|---|---|---|---|---|---|---|---|",
                    ]
                    for r in top.itertuples():
                        ln = "" if pd.isna(r.line) else f"{r.line:g}"
                        lines.append(
                            f"| {r.date} | {r.name} | {r.team_id} vs {r.opp_id} | {ln} | {r.actual:g} | {r.minutes:.0f} | {r.AVG_q10:g}/{r.AVG_q50:g}/{r.AVG_q90:g} | {getattr(r, comp + '_q10'):g}/{getattr(r, comp + '_q50'):g}/{getattr(r, comp + '_q90'):g} | {r.diff:+.3f} |"
                        )
                    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- simulation
def sim_legs(
    rows_by_stat: dict[str, pl.DataFrame], book: str
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
    """Legs of one book over all stats of a season: arrays for ``simulate`` and the arm P(over) per arm. Cluster = game."""
    parts = []
    for rows in rows_by_stat.values():
        parts.append(
            rows.filter(
                pl.col(f"{book}_line").is_not_null()
                & pl.col(f"{book}_price_over").is_not_null()
                & pl.col(f"{book}_price_under").is_not_null()
            )
        )
    d = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()
    if d.height == 0:
        return {}, {}, 0
    _, gi = np.unique(d["game_id"].to_numpy(), return_inverse=True)
    legs = {
        "po": d[f"{book}_price_over"].to_numpy().astype(float), "pu": d[f"{book}_price_under"].to_numpy().astype(float), "pm": d[f"{book}_p_over_devig"].to_numpy().astype(float),
        "over": (d["actual"].to_numpy() > d[f"{book}_line"].to_numpy()).astype(int), "night": d["date"].to_numpy().astype("datetime64[D]").astype(np.int64), "gi": gi.astype(np.int64),
        "idx": np.arange(d.height),
    }  # fmt: skip
    return (
        legs,
        {a: d[f"{a}_p_over_{book}"].to_numpy().astype(float) for a in ARMS},
        int(gi.max()) + 1,
    )


def simulation_report(
    rows_by_season: dict[int, dict[str, pl.DataFrame]], out_dir: Path, seen: tuple[int, ...]
) -> dict[str, Any]:
    res: dict[str, Any] = {}
    paths: list[pd.DataFrame] = []
    for season, rbs in rows_by_season.items():
        for book in BOOKS:
            legs, p_by_arm, ng = sim_legs(rbs, book)
            if not legs:
                continue
            for arm in ARMS:
                for persona in PERSONAS:
                    bets = simulate(legs, p_by_arm[arm], persona)
                    rec, path = summarize_bets(bets, ng, persona)
                    rec.update(
                        season=season,
                        book=book,
                        arm=arm,
                        n_legs=int(len(legs["pm"])),
                        already_seen_season=season in seen,
                    )
                    res[f"{season}/{book}/{arm}/{persona}"] = rec
                    if len(path):
                        paths.append(
                            path.assign(season=season, book=book, arm=arm, persona=persona)
                        )
    pd.concat(paths).to_csv(out_dir / "sim_bankroll_paths.csv", index=False) if paths else None
    return res


def sim_md(res: dict[str, Any]) -> str:
    f = lambda x, nd=3: "" if x is None else f"{x:.{nd}f}"  # noqa: E731
    lines = [
        "# SEQ_FULL_MIX user simulation (DESCRIPTIVE)", "",
        "Personas fixed in the rule before any 2025 number existed. P0 keep your money. P1 flat $20 on every leg where the arm's P(side) minus the de-vigged market P(side) >= 0.03, at the stored vigged T-60 price. P2 the same at >= 0.06. P3 quarter-Kelly on a $500 bankroll, at most $20 per bet, nightly stakes scaled to the bankroll held at the start of the night. P4 one 2-leg same-night parlay per night from the two highest-edge legs on different games, $10, legs assumed independent (the price is the product of the legs' prices; clustered on the first leg's game).",
        "", "**This is descriptive, not a test of edge. Hit rates and ROI on one season are noisy; no persona is a recommendation.** Seasons marked seen=True (2023, 2024) are already-seen: the construction was chosen on them. One bet per (player, stat, night, book) at the T-60 price; Pinnacle and DK separately. ROI = P&L / staked with a game-clustered 95% CI.", "",
        "| season | seen | book | arm | persona | bets | staked | P&L | ROI [CI] | max drawdown | longest losing streak | months in profit |", "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for r in res.values():
        lines.append(
            f"| {r['season']} | {r['already_seen_season']} | {r['book']} | {r['arm']} | {r['persona']} | {r['n_bets']} | {f(r['staked'], 0)} | {f(r['pnl'], 1)} | {f(r['roi'])} [{f(r['lo'])}, {f(r['hi'])}] | {f(r['max_drawdown'], 1)} | {r['longest_losing_streak']} | {f(r['share_months_profit'], 2)} |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- entry
def run_descriptive(
    world: dict[str, Any],
    target: int,
    out_dir: Path,
    verdict_path: Path,
    nba_db: str,
    odds_db: Path,
    pplay_dir: Path | None,
    seen: tuple[int, ...] = (2023, 2024),
) -> dict[str, Any]:
    """Post-verdict step. Refuses unless the verdict JSON exists and holds a verdict; reads it, never writes it; writes only under ``out_dir/descriptive``."""
    if not verdict_path.exists():
        raise RuntimeError(
            f"{verdict_path} does not exist: the descriptive step runs only after the verdict is written"
        )
    if "verdict" not in json.loads(verdict_path.read_text()):
        raise RuntimeError(f"{verdict_path} holds no verdict")
    out = out_dir / "descriptive"
    if out.resolve() == verdict_path.resolve().parent or verdict_path.resolve().is_relative_to(
        out.resolve()
    ):
        raise RuntimeError("the descriptive output directory must be separate from the verdict")
    out.mkdir(parents=True, exist_ok=True)
    import duckdb

    con = duckdb.connect(nba_db, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, home_team, away_team FROM games WHERE season >= 2022"
        ).df()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season >= 2022"
        ).df()
    finally:
        con.close()
    ctx = context_flags(pgs, games)
    seasons = sorted({int(target), *[s for s in seen if s <= target]})
    rows_by_season: dict[int, dict[str, pl.DataFrame]] = {}
    mk_t = names_t = None
    for s in seasons:
        mk, names = market_legs(s, odds_db)
        if s == target:
            mk_t, names_t = mk, names
        pp = load_pplay(pplay_dir) if (pplay_dir is not None and s == 2025) else None
        rows_by_season[s] = {
            st: build_rows(world, st, s, mk, names, ctx, pp, games) for st in STATS
        }
        _log(
            f"season {s}: rows {sum(r.height for r in rows_by_season[s].values())}, market legs {mk.height}"
        )
    assert mk_t is not None and names_t is not None
    full = pl.concat(list(rows_by_season[target].values()), how="diagonal_relaxed")
    full.write_parquet(out / "rows.parquet")
    full.write_csv(out / "rows.csv")
    conf = confusion_report(full)
    (out / "confusion.json").write_text(json.dumps(conf, indent=1, default=float))
    (out / "spot_check.md").write_text(spot_md(full))
    sim = simulation_report(rows_by_season, out, seen)
    (out / "simulation.json").write_text(json.dumps(sim, indent=1, default=float))
    (out / "simulation.md").write_text(sim_md(sim))
    meta = {
        "target": target,
        "rows": full.height,
        "files": sorted(p.name for p in out.iterdir()),
        "odds_db": str(odds_db),
        "note": "descriptive; computed after the verdict JSON was written; cannot change it",
    }
    (out / "README.json").write_text(json.dumps(meta, indent=1))
    return meta
