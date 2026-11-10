# ruff: noqa: E501
"""GAME_SCRIPTS_FORWARD harness (docs/prereg/GAME_SCRIPTS_FORWARD.md). Read-only everywhere.

Commands
  status  label-free: window counts, stamp coverage, the projected look date, optionally the family-S
          leg count (``--s-legs``). Reads no score value: finals are a boolean computed in SQL.
  dryrun  the whole scoring pipeline on SYNTHETIC games and legs only (no database is opened).
  run     the one look. Real ET clock only (no date argument exists). Refuses unless the rule text
          is frozen and matches ``RULE_SHA256``, the freeze commit is set, the look date D has passed
          and every game through D is settled, and no results file exists yet. Family S is computed
          only when LINE_CAL_LIVE's results file exists; otherwise it is written as DEFERRED and
          ``run-s`` computes it later at the same D.
  run-s   family S at the recorded D, once LINE_CAL_LIVE's results exist. Refuses a second time.

Guards
  * Only ``season = 2026``, game id prefix ``002``, is ever scored; any other season in a scored frame
    raises (``refuse_seasons``). Season 2025 rows enter only as the as-of total-feature input, as the
    live engine builds it (rule 4.4, Devin's O3a).
  * Every function that reads a score of a window game (outcomes, the as-of total feature, player stats)
    takes a ``LookState`` and raises unless it is open. A ``LookState`` comes only from ``look_state``;
    the CLI feeds it the real clock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl
from numpy.typing import NDArray

ET = ZoneInfo("America/New_York")
F64 = NDArray[np.float64]

RULE_ID = "GAME_SCRIPTS_FORWARD"
RULE_DOC = "docs/prereg/GAME_SCRIPTS_FORWARD.md"
MARKER = "=== RESULTS BELOW ==="
RULE_SHA256: str | None = None  # set in the freeze commit
FREEZE_COMMIT: str | None = None  # set right after the freeze commit (its sha)
OUT_REL = "reports/prereg_game_scripts_forward/results.json"
OUT_S_REL = "reports/prereg_game_scripts_forward/results_s.json"
LINE_CAL_LIVE_RESULTS = "reports/prereg_line_cal_live/results.json"
JOINT_MODEL_REL = "data/parlay/joint_model.json"
COMBO_PARAMS_REL = "nba/parlay/combo_params_v1.json"
JOINT_MODEL_SHA8 = "a2b4c4a9"
COMBO_PARAMS_SHA8 = "1a85bb31"

SEASON = 2026
PREFIX = "002"
SCRIPTS = ("blowout", "close", "shootout", "grind")
D_SCRIPTS = ("blowout", "close")
STATS = ("pts", "reb", "ast", "fg3m")
N_LOOK = 750
N_MIN = 600
BACKSTOP = date(2027, 3, 28)
SETTLE_H = 36
B = 2000
SEED = 20261110
TOL_P = 0.05
MISCAL_FLOOR = 0.02
D_FLOOR = -0.002
TOL_S = 0.03
S_MIN_GAMES = 150
S_MIN_LEGS = 2000
UNFORECAST_MAX = 0.05
WIN_MODEL = "rung0_injury_elo"
WIN_VERSION = "v2"  # production version in force at the draft; words use this segment only
REL_BINS = {"blowout": (0.0, 0.30, 0.35, 0.40, 0.50, 1.0), "close": (0.0, 0.20, 0.25, 0.30, 1.0)}
MU_M_BINS = (0.0, 5.0, 10.0, math.inf)
BLOWOUT_ABS, CLOSE_ABS, TOTAL_DELTA = 15, 5, 12.0


class Refused(RuntimeError):
    """The harness will not do this (rule guard)."""


# ---------------------------------------------------------------- guards


def refuse_seasons(seasons: Sequence[int]) -> None:
    bad = sorted({int(s) for s in seasons} - {SEASON})
    if bad:
        raise Refused(f"only season {SEASON} is scored; got {bad} (season 2025 is the holdout)")


def rule_text_sha(path: Path) -> str:
    text = path.read_text()
    head = text.split("\n" + MARKER + "\n", 1)[0] + "\n"
    return hashlib.sha256(head.encode()).hexdigest()


def verify_rule(root: Path) -> None:
    if RULE_SHA256 is None or FREEZE_COMMIT is None:
        raise Refused("rule not frozen: RULE_SHA256 / FREEZE_COMMIT unset")
    doc = root / RULE_DOC
    got = rule_text_sha(doc)
    if got != RULE_SHA256:
        raise Refused(f"rule text sha {got[:8]} != frozen {RULE_SHA256[:8]}")
    status = next((ln for ln in doc.read_text().splitlines() if ln.startswith("Status:")), "")
    if "FROZEN" not in status:
        raise Refused("status line does not say FROZEN")


def sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def freeze_et_date(root: Path, commit: str) -> date:
    out = subprocess.run(
        ["git", "-C", str(root), "show", "-s", "--format=%cI", commit],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return datetime.fromisoformat(out).astimezone(ET).date()


# ---------------------------------------------------------------- script priors and outcomes


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _ppf(p: float) -> float:
    from statistics import NormalDist

    return NormalDist().inv_cdf(min(max(p, 1e-12), 1 - 1e-12))


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def priors(mu_m: float, mu_t: float, sd_m: float, sd_t: float) -> dict[str, float]:
    """P(script) via the served function (``combo.script_prior``)."""
    from nba.parlay import combo

    shared = {
        "scripts": {
            "definitions": {
                "blowout_abs_margin": BLOWOUT_ABS,
                "close_abs_margin": CLOSE_ABS,
                "total_delta": TOTAL_DELTA,
            }
        }
    }
    game = {"game": {"mu_m": mu_m, "sd_m": sd_m, "mu_t": mu_t, "sd_t": sd_t}}
    return {s: float(combo.script_prior(s, game, shared)) for s in SCRIPTS}


def closed_form(mu_m: float, mu_t: float, sd_m: float, sd_t: float) -> dict[str, float]:
    t_b, t_c = BLOWOUT_ABS - 0.5, CLOSE_ABS + 0.5
    return {
        "blowout": _phi((-t_b - mu_m) / sd_m) + 1 - _phi((t_b - mu_m) / sd_m),
        "close": _phi((t_c - mu_m) / sd_m) - _phi((-t_c - mu_m) / sd_m),
        "shootout": 1 - _phi((math.ceil(mu_t + TOTAL_DELTA) - 0.5 - mu_t) / sd_t),
        "grind": _phi((math.floor(mu_t - TOTAL_DELTA) + 0.5 - mu_t) / sd_t),
    }


def realised(margin: int, total: int, mu_t: float) -> dict[str, bool]:
    return {
        "blowout": abs(margin) >= BLOWOUT_ABS,
        "close": abs(margin) <= CLOSE_ABS,
        "shootout": total >= math.ceil(mu_t + TOTAL_DELTA),
        "grind": total <= math.floor(mu_t - TOTAL_DELTA),
    }


# ---------------------------------------------------------------- statistics (pure)


def boot_means(vals: F64, clusters: Sequence[Any], rng: np.random.Generator, b: int = B) -> F64:
    """Cluster-bootstrap distribution of the row mean (clusters resampled with replacement)."""
    _, inv = np.unique(np.asarray([str(c) for c in clusters]), return_inverse=True)
    k = int(inv.max()) + 1
    sums = np.bincount(inv, weights=vals, minlength=k)
    cnt = np.bincount(inv, minlength=k).astype(float)
    idx = rng.integers(0, k, size=(b, k))
    return np.asarray(sums[idx].sum(1) / cnt[idx].sum(1), dtype=float)


def boot_ratio(
    num: F64, den: F64, clusters: Sequence[Any], rng: np.random.Generator, b: int = B
) -> F64:
    _, inv = np.unique(np.asarray([str(c) for c in clusters]), return_inverse=True)
    k = int(inv.max()) + 1
    sn = np.bincount(inv, weights=num, minlength=k)
    sd = np.bincount(inv, weights=den, minlength=k)
    idx = rng.integers(0, k, size=(b, k))
    return np.asarray(sn[idx].sum(1) / sd[idx].sum(1), dtype=float)


def ci95(bs: F64) -> tuple[float, float]:
    return float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


def p_two_sided(bs: F64) -> float:
    return float(min(1.0, 2.0 * min(np.mean(bs <= 0.0), np.mean(bs >= 0.0))))


def p_less_than_zero(bs: F64) -> float:
    """One-sided p for H1: statistic < 0."""
    return float(np.mean(bs >= 0.0))


def bh(pvals: Sequence[float]) -> list[float]:
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        adj[i] = prev
    return [float(x) for x in adj]


def rng_for(*key: int) -> np.random.Generator:
    return np.random.default_rng([SEED, *key])


def _logistic_fit(x: F64, y: F64, iters: int = 25) -> tuple[float, float]:
    """Intercept and slope of logit P(y) = a + b x (Newton, small ridge for separation)."""
    w = np.zeros(2)
    xx = np.column_stack([np.ones_like(x), x])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(xx @ w)))
        g = xx.T @ (y - p) - 1e-6 * w
        h = (xx.T * (p * (1 - p))) @ xx + 1e-6 * np.eye(2)
        w = w + np.linalg.solve(h, g)
    return float(w[0]), float(w[1])


# ---------------------------------------------------------------- families (pure)


def family_p(
    y: dict[str, F64], p: dict[str, F64], dates: Sequence[Any], games: Sequence[Any]
) -> dict[str, Any]:
    """Primary family: gap = realised share - mean P, date CI (binding) and game CI; words with BH."""
    out: dict[str, Any] = {}
    pv: list[float] = []
    for i, s in enumerate(SCRIPTS):
        gap = y[s] - p[s]
        bd = boot_means(gap, dates, rng_for(1, i))
        bg = boot_means(gap, games, rng_for(2, i))
        pv.append(p_two_sided(bd))
        out[s] = {
            "n_games": len(gap),
            "n_dates": len(set(map(str, dates))),
            "realised": int(y[s].sum()),
            "realised_share": float(y[s].mean()),
            "mean_p": float(p[s].mean()),
            "gap": float(gap.mean()),
            "ci_date": ci95(bd),
            "ci_game": ci95(bg),
            "p_two_sided_date": pv[-1],
            "brier": float(np.mean((p[s] - y[s]) ** 2)),
        }
    adj = bh(pv)
    for s, a in zip(SCRIPTS, adj, strict=True):
        r = out[s]
        lo, hi = r["ci_date"]
        cal = lo >= -TOL_P and hi <= TOL_P
        mis = a < 0.05 and abs(r["gap"]) >= MISCAL_FLOOR
        r["p_bh"] = a
        r["word"] = "CALIBRATED" if cal else ("MISCALIBRATED" if mis else "INCONCLUSIVE")
        r["offset_detected"] = bool(cal and mis)
    return out


def family_d(y: dict[str, F64], p: dict[str, F64], dates: Sequence[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pv: list[float] = []
    for i, s in enumerate(D_SCRIPTS):
        c = float(p[s].mean())
        diff = (p[s] - y[s]) ** 2 - (c - y[s]) ** 2
        bd = boot_means(diff, dates, rng_for(3, i))
        pv.append(p_less_than_zero(bd))
        out[s] = {
            "dbrier": float(diff.mean()),
            "ci_date": ci95(bd),
            "p_one_sided": pv[-1],
            "constant": c,
        }
    for s, a in zip(D_SCRIPTS, bh(pv), strict=True):
        r = out[s]
        r["p_bh"] = a
        r["word"] = (
            "INFORMATIVE"
            if (r["dbrier"] <= D_FLOOR and r["ci_date"][1] < 0 and a < 0.05)
            else "NOT_SHOWN"
        )
    return out


def cond_p(u: float, mu: float, sigma: float) -> float:
    """P(over | script) for a main-line over leg with calibrated marginal ``u``: the served one-leg
    computation (``combo._evaluate_game_legs_core``: a = Phi^-1(1 - u), shifted and rescaled)."""
    a = _ppf(1.0 - u)
    return 1.0 - _phi((a - mu) / sigma)


def family_s(legs: pl.DataFrame, realised_by_game: dict[str, dict[str, bool]]) -> dict[str, Any]:
    """legs: game_id, player_id, stat, y (0/1), u, c_<script>, a_<script> (shifted latent bound).
    Words per script on the legs of games that realised it."""
    out: dict[str, Any] = {}
    pv: list[float] = []
    for i, s in enumerate(SCRIPTS):
        keep = [realised_by_game.get(g, {}).get(s, False) for g in legs["game_id"].to_list()]
        d = legs.filter(pl.Series(keep, dtype=pl.Boolean))
        n_games = d["game_id"].n_unique() if d.height else 0
        r: dict[str, Any] = {"legs": d.height, "games": n_games}
        if d.height == 0:
            pv.append(1.0)
            r["word"] = "INSUFFICIENT_DATA"
            out[s] = r
            continue
        yv = d["y"].to_numpy().astype(float)
        u = d["u"].to_numpy().astype(float)
        c = d[f"c_{s}"].to_numpy().astype(float)
        g = d["game_id"].to_list()
        db = (c - yv) ** 2 - (u - yv) ** 2
        b1 = boot_means(db, g, rng_for(4, i))
        b2 = boot_means(yv - c, g, rng_for(5, i))
        pv.append(p_less_than_zero(b1))
        r.update(
            {
                "realised_over": float(yv.mean()),
                "mean_u": float(u.mean()),
                "mean_c": float(c.mean()),
                "dbrier": float(db.mean()),
                "ci_dbrier": ci95(b1),
                "p_one_sided": pv[-1],
                "gap_c": float((yv - c).mean()),
                "ci_gap_c": ci95(b2),
            }
        )
        out[s] = r
    for s, a in zip(SCRIPTS, bh(pv), strict=True):
        r = out[s]
        r["p_bh"] = a
        if r["games"] < S_MIN_GAMES or r["legs"] < S_MIN_LEGS:
            r["word"] = "INSUFFICIENT_DATA"
            continue
        s_i = r["ci_dbrier"][1] < 0 and a < 0.05
        s_ii = r["ci_gap_c"][0] >= -TOL_S and r["ci_gap_c"][1] <= TOL_S
        r["s_i"], r["s_ii"] = bool(s_i), bool(s_ii)
        r["word"] = (
            "CONDITIONAL_CALIBRATED"
            if (s_i and s_ii)
            else ("IMPROVES_BUT_OFF" if s_i else "NOT_SHOWN")
        )
    return out


# ---------------------------------------------------------------- descriptives (pure)


def descriptives(df: pl.DataFrame, sd_t: float | None = None) -> dict[str, Any]:
    """df: game_id, et_date, mu_m, mu_t, total, y_<s>, p_<s>. No word depends on these."""
    out: dict[str, Any] = {}
    dates = df["et_date"].to_list()
    rel: dict[str, Any] = {}
    for s in D_SCRIPTS:
        p, y = df[f"p_{s}"].to_numpy(), df[f"y_{s}"].to_numpy().astype(float)
        edges = REL_BINS[s]
        rows = []
        for j, (lo, hi) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
            m = (p >= lo) & (p < hi)
            if m.sum():
                bg = boot_means(y[m], df["game_id"].filter(pl.Series(m)).to_list(), rng_for(6, j))
                rows.append(
                    {
                        "bin": [lo, hi],
                        "n": int(m.sum()),
                        "mean_p": float(p[m].mean()),
                        "realised": float(y[m].mean()),
                        "ci_game": ci95(bg),
                    }
                )
        x = np.array([logit(v) for v in p])
        a, b_ = _logistic_fit(x, y)
        ud, inv = np.unique(np.asarray([str(d) for d in dates]), return_inverse=True)
        members = [np.flatnonzero(inv == k) for k in range(len(ud))]
        rng = rng_for(7, D_SCRIPTS.index(s))
        slopes = []
        for _ in range(B):
            pick = rng.integers(0, len(ud), len(ud))
            idx = np.concatenate([members[k] for k in pick])
            slopes.append(_logistic_fit(x[idx], y[idx])[1])
        rel[s] = {
            "reliability": rows,
            "intercept": a,
            "slope": b_,
            "slope_ci_date": ci95(np.asarray(slopes)),
        }
    out["reliability"] = rel
    resid = (df["total"] - df["mu_t"]).to_numpy().astype(float)
    bm = boot_means(resid, dates, rng_for(8, 0))
    out["total_minus_mu_t"] = {
        "mean": float(resid.mean()),
        "ci_date": ci95(bm),
        "sd": float(resid.std(ddof=1)),
        "sd_t_frozen": sd_t,
    }
    sl: dict[str, Any] = {}
    month = [str(d)[:7] for d in dates]
    half = np.arange(df.height) >= df.height / 2
    order = np.argsort(np.asarray([str(d) for d in dates]), kind="stable")
    second = np.zeros(df.height, dtype=bool)
    second[order[half]] = True
    absm = np.abs(df["mu_m"].to_numpy())
    for s in SCRIPTS:
        y, p = df[f"y_{s}"].to_numpy().astype(float), df[f"p_{s}"].to_numpy()
        cell: dict[str, Any] = {}
        for mo in sorted(set(month)):
            m = np.array([x == mo for x in month])
            cell[f"month_{mo}"] = {"n": int(m.sum()), "gap": float((y[m] - p[m]).mean())}
        for name, m in (("first_half", ~second), ("second_half", second)):
            cell[name] = {
                "n": int(m.sum()),
                "gap": float((y[m] - p[m]).mean()) if m.sum() else None,
            }
        if s in D_SCRIPTS:
            for lo, hi in zip(MU_M_BINS[:-1], MU_M_BINS[1:], strict=True):
                m = (absm >= lo) & (absm < hi)
                cell[f"abs_mu_m_{lo:g}_{hi:g}"] = {
                    "n": int(m.sum()),
                    "gap": float((y[m] - p[m]).mean()) if m.sum() else None,
                }
        sl[s] = cell
    out["slices"] = sl
    return out


# ---------------------------------------------------------------- data (read-only)


@dataclass(frozen=True)
class LookState:
    today: date
    freeze: date
    look_date: date | None
    n_at_look: int
    open: bool
    reason: str
    pending: tuple[str, ...] = field(default_factory=tuple)


def window_rows(
    con: duckdb.DuckDBPyConnection,
    facts: duckdb.DuckDBPyConnection | None,
    first: date,
    last: date | None = None,
) -> pl.DataFrame:
    """Label-free: one row per regular-season 2026 game dated in [first, last], with a FINAL flag (a
    boolean, no score value), the schedule tip and the latest pre-tip production win-prob stamp."""
    q = (
        "SELECT g.game_id, g.game_date AS et_date, g.season, g.home_team, g.away_team, "
        "coalesce(g.home_pts > 0 AND g.away_pts > 0, false) AS final "
        "FROM games g WHERE g.season = ? AND g.game_id LIKE '002%' AND g.game_date >= ?"
    )
    args: list[Any] = [SEASON, first]
    if last is not None:
        q += " AND g.game_date <= ?"
        args.append(last)
    g = con.execute(q, args).pl()
    refuse_seasons(g["season"].unique().to_list() if g.height else [])
    st = con.execute(
        "SELECT game_id, made_at, tipoff, version, json_extract(prediction, '$.p_home')::DOUBLE AS p_home "
        "FROM forward_predictions WHERE model_name = ? AND target = 'win_prob_home' AND made_at < tipoff",
        [WIN_MODEL],
    ).pl()
    st = st.sort(["game_id", "made_at"]).group_by("game_id", maintain_order=True).last()
    out = g.join(st, on="game_id", how="left")
    if facts is not None:
        sch = facts.execute(
            "SELECT substr(subject, 6) AS game_id, arg_max(value_text, known_at) AS tip_txt FROM facts "
            "WHERE predicate = 'tips_at' AND subject LIKE 'game:002%' GROUP BY 1"
        ).pl()
        sch = sch.with_columns(
            pl.col("tip_txt").str.to_datetime(time_unit="us").alias("tip_sched")
        ).drop("tip_txt")
        out = out.join(sch, on="game_id", how="left")
    else:
        out = out.with_columns(pl.col("tipoff").alias("tip_sched"))
    return out.with_columns(pl.col("p_home").is_not_null().alias("stamped"))


def look_state(rows: pl.DataFrame, freeze: date, today: date, now_utc: datetime) -> LookState:
    """Pure, label-free. ``rows`` from ``window_rows`` (window dates only)."""
    w = rows.filter(pl.col("et_date") > freeze)
    elig = w.filter(pl.col("final") & pl.col("stamped") & (pl.col("et_date") < today))
    per = elig.group_by("et_date").agg(pl.len().alias("n")).sort("et_date")
    cum, d_look, n_look = 0, None, 0
    for d, n in per.iter_rows():
        cum += int(n)
        if cum >= N_LOOK:
            d_look, n_look = d, cum
            break
    if d_look is None:
        if today > BACKSTOP:
            d_look = BACKSTOP
            n_look = int(elig.filter(pl.col("et_date") <= BACKSTOP).height)
        else:
            return LookState(
                today,
                freeze,
                None,
                cum,
                False,
                f"{cum} scored games, need {N_LOOK} (backstop {BACKSTOP})",
            )
    if not today > d_look:
        return LookState(today, freeze, d_look, n_look, False, "look date not passed")
    naive_now = now_utc.astimezone(UTC).replace(tzinfo=None)
    pend = []
    for gid, fin, tip in (
        w.filter(pl.col("et_date") <= d_look).select("game_id", "final", "tip_sched").iter_rows()
    ):
        if fin:
            continue
        if tip is None or naive_now < tip + timedelta(hours=SETTLE_H):
            pend.append(str(gid))
    if pend:
        return LookState(
            today,
            freeze,
            d_look,
            n_look,
            False,
            f"{len(pend)} games through D not settled",
            tuple(sorted(pend)),
        )
    return LookState(today, freeze, d_look, n_look, True, "open")


def _need_open(state: LookState) -> None:
    if not state.open:
        raise Refused(f"look not open: {state.reason}")


def load_scores(
    con: duckdb.DuckDBPyConnection, game_ids: Sequence[str], state: LookState
) -> pl.DataFrame:
    _need_open(state)
    if not game_ids:
        return pl.DataFrame({"game_id": [], "margin": [], "total": []})
    g = con.execute(
        "SELECT game_id, season, home_pts - away_pts AS margin, home_pts + away_pts AS total FROM games "
        "WHERE game_id IN (SELECT unnest(?))",
        [list(game_ids)],
    ).pl()
    refuse_seasons(g["season"].to_list())
    return g.drop("season")


def as_of_mu_t(
    con: duckdb.DuckDBPyConnection, rows: pl.DataFrame, params: Any, state: LookState
) -> dict[str, float]:
    """The live engine's feature (``nba/parlay/slate.py``): every final game dated before the slate."""
    _need_open(state)
    from nba.parlay.game_model import slate_total_feature

    last = max(rows["et_date"].to_list())
    hist = con.execute(
        "SELECT game_id, game_date, home_team, away_team, home_pts, away_pts FROM games WHERE game_date < ? AND home_pts IS NOT NULL",
        [last],
    ).pl()
    out: dict[str, float] = {}
    for gid, d, h, a in rows.select("game_id", "et_date", "home_team", "away_team").iter_rows():
        out[str(gid)] = params.total_mu(slate_total_feature(hist, params, int(h), int(a), d))
    return out


def s_legs(src: duckdb.DuckDBPyConnection, odds_db: Path, dates: Sequence[date]) -> pl.DataFrame:
    """Label-free: family-S legs exactly as the export's ``line_cal`` builds them, per slate date."""
    from nba.export import build, line_cal_fields, reader
    from nba.export import rung_cal_fields as rcf

    rows: list[dict[str, Any]] = []
    for day in dates:
        window = reader.Window(day, 0, (SEASON,))
        fwd = reader.forward_predictions(src, window)
        if not fwd.prop_grids:
            continue
        grids = reader.prod_grids(src, (g["game_id"] for g in fwd.games))
        _, cal = build._read_lines(fwd.prop_grids, window, odds_db, None)
        line_cal_fields.attach(fwd.prop_grids, cal.rows, grids)
        for (gid, pid, stat), r in rcf._production_rows(fwd.prop_grids).items():
            lc_ = r.get("line_cal")
            if not lc_ or stat not in STATS or not str(gid).startswith(PREFIX):
                continue
            rows.append(
                {
                    "game_id": str(gid), "player_id": int(pid), "stat": stat, "et_date": day,
                    "line": float(lc_["main_line"]), "u": float(lc_["p_over_cal"]), "raw": float(lc_["p_over_raw"]),
                    "starter_rate": rcf._starter_rate(r),
                }
            )  # fmt: skip
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "stat": pl.Utf8,
                "et_date": pl.Date,
                "line": pl.Float64,
                "u": pl.Float64,
                "raw": pl.Float64,
                "starter_rate": pl.Float64,
            }
        )
    )


def leg_outcomes(
    con: duckdb.DuckDBPyConnection, legs: pl.DataFrame, state: LookState
) -> pl.DataFrame:
    """Played legs only (DNP voided): y = stat >= line + 0.5."""
    _need_open(state)
    st = con.execute(
        "SELECT p.game_id, p.player_id, p.pts, p.reb, p.ast, p.fg3m, g.season FROM player_game_stats_played p "
        "JOIN games g USING (game_id) WHERE p.minutes > 0 AND p.game_id IN (SELECT unnest(?))",
        [legs["game_id"].unique().to_list()],
    ).pl()
    refuse_seasons(st["season"].unique().to_list() if st.height else [])
    long = st.unpivot(
        index=["game_id", "player_id"], on=list(STATS), variable_name="stat", value_name="value"
    )
    j = legs.join(
        long.with_columns(pl.col("player_id").cast(pl.Int64)),
        on=["game_id", "player_id", "stat"],
        how="inner",
    )
    return j.with_columns((pl.col("value") >= pl.col("line") + 0.5).cast(pl.Int8).alias("y"))


def add_conditionals(legs: pl.DataFrame, script_params: Sequence[Sequence[Any]]) -> pl.DataFrame:
    from nba.parlay import combo

    shared = {"scripts": {"params": script_params}}
    roles = [
        "starter" if (sr is not None and sr >= 0.5) else "bench"
        for sr in legs["starter_rate"].to_list()
    ]
    cols: dict[str, list[float]] = {}
    for s in SCRIPTS:
        cs, as_ = [], []
        for u, stat, role in zip(legs["u"].to_list(), legs["stat"].to_list(), roles, strict=True):
            mu, sg = combo.script_params(shared, s, stat, role)
            cs.append(cond_p(float(u), mu, sg))
            as_.append((_ppf(1.0 - float(u)) - mu) / sg)
        cols[f"c_{s}"], cols[f"a_{s}"] = cs, as_
    return legs.with_columns(
        [pl.Series(k, v) for k, v in cols.items()] + [pl.Series("role", roles)]
    )


def same_player_ratio(
    legs: pl.DataFrame, realised_by_game: dict[str, dict[str, bool]], corr: Sequence[Sequence[Any]]
) -> dict[str, Any]:
    """Descriptive only: realised / copula joint for same-player pairs (different stats) under s."""
    from nba.parlay import combo

    out: dict[str, Any] = {}
    for i, s in enumerate(SCRIPTS):
        num, den, cl = [], [], []
        for (gid, _pid), grp in legs.group_by(["game_id", "player_id"]):
            if not realised_by_game.get(str(gid), {}).get(s, False) or grp.height < 2:
                continue
            r = grp.to_dicts()
            for x in range(len(r)):
                for z in range(x + 1, len(r)):
                    rho = combo.corr_lookup(corr, "same_player", r[x]["stat"], r[z]["stat"])
                    den.append(
                        combo.bivariate_rect(
                            r[x][f"a_{s}"], math.inf, r[z][f"a_{s}"], math.inf, rho
                        )
                    )
                    num.append(float(r[x]["y"] * r[z]["y"]))
                    cl.append(str(gid))
        if not den:
            out[s] = {"pairs": 0}
            continue
        bs = boot_ratio(np.asarray(num), np.asarray(den), cl, rng_for(9, i))
        out[s] = {
            "pairs": len(den),
            "games": len(set(cl)),
            "ratio": float(sum(num) / sum(den)),
            "ci_game": ci95(bs),
        }
    return out


# ---------------------------------------------------------------- the scored frame


def scored_frame(
    rows: pl.DataFrame, scores: pl.DataFrame, mu_t: dict[str, float], params: Any
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Join, integrity checks, P(script) and outcomes. Returns (frame, integrity)."""
    fin = rows.filter(pl.col("final"))
    unforecast = fin.filter(~pl.col("stamped"))
    sc = fin.filter(pl.col("stamped"))
    bad_tip = sc.filter(
        pl.col("tip_sched").is_null()
        | (pl.col("tipoff") != pl.col("tip_sched"))
        | (pl.col("made_at") >= pl.col("tipoff"))
    )
    versions = {str(k): int(v) for k, v in sc.group_by("version").agg(pl.len()).iter_rows()}
    sc = sc.filter(pl.col("version") == WIN_VERSION).join(scores, on="game_id", how="inner")
    recs = []
    max_dev = 0.0
    for r in sc.iter_rows(named=True):
        mm = params.margin_mu(float(r["p_home"]))
        mt = mu_t[str(r["game_id"])]
        pr = priors(mm, mt, params.margin_sd, params.total_sd)
        cf = closed_form(mm, mt, params.margin_sd, params.total_sd)
        max_dev = max(max_dev, max(abs(pr[s] - cf[s]) for s in SCRIPTS))
        yy = realised(int(r["margin"]), int(r["total"]), mt)
        recs.append(
            {"game_id": str(r["game_id"]), "et_date": r["et_date"], "mu_m": mm, "mu_t": mt, "total": int(r["total"]),
             **{f"p_{s}": pr[s] for s in SCRIPTS}, **{f"y_{s}": int(yy[s]) for s in SCRIPTS}}
        )  # fmt: skip
    frame = pl.DataFrame(recs) if recs else pl.DataFrame()
    n_fin = fin.height
    share = (unforecast.height / n_fin) if n_fin else 0.0
    integ: dict[str, Any] = {
        "final_games": n_fin,
        "unforecast": unforecast.height,
        "unforecast_share": share,
        "bad_tip_or_stamp": bad_tip.height,
        "win_model_versions": versions,
        "closed_form_max_dev": max_dev,
    }
    integ["fail"] = bool(share > UNFORECAST_MAX or bad_tip.height > 0 or max_dev > 1e-9)
    return frame, integ


def score_games(frame: pl.DataFrame, sd_t: float | None = None) -> dict[str, Any]:
    dates, games = frame["et_date"].to_list(), frame["game_id"].to_list()
    y = {s: frame[f"y_{s}"].to_numpy().astype(float) for s in SCRIPTS}
    p = {s: frame[f"p_{s}"].to_numpy().astype(float) for s in SCRIPTS}
    fp = family_p(y, p, dates, games)
    fd = family_d(y, p, dates)
    if frame.height < N_MIN:
        for r in fp.values():
            r["word"] = "INSUFFICIENT_DATA"
        for r in fd.values():
            r["word"] = "INSUFFICIENT_DATA"
    return {"P": fp, "D": fd, "descriptive": descriptives(frame, sd_t)}


# ---------------------------------------------------------------- commands


def _params(root: Path) -> Any:
    from nba.parlay.game_model import GameModelParams

    return GameModelParams.from_json(
        json.dumps(json.loads((root / JOINT_MODEL_REL).read_text())["params"])
    )


def _check_pins(root: Path) -> None:
    got = (
        sha8(root / JOINT_MODEL_REL),
        sha8(Path(__file__).resolve().parents[2] / COMBO_PARAMS_REL),
    )
    if got != (JOINT_MODEL_SHA8, COMBO_PARAMS_SHA8):
        raise Refused(f"pinned inputs changed: {got} != {(JOINT_MODEL_SHA8, COMBO_PARAMS_SHA8)}")


def _open(root: Path) -> tuple[duckdb.DuckDBPyConnection, duckdb.DuckDBPyConnection]:
    return (
        duckdb.connect(str(root / "nba.duckdb"), read_only=True),
        duckdb.connect(str(root / "data/facts/facts.duckdb"), read_only=True),
    )


def _today_et() -> tuple[date, datetime]:
    now = datetime.now(UTC)
    return now.astimezone(ET).date(), now


def cmd_status(root: Path, assume_freeze: date | None, with_s: bool) -> dict[str, Any]:
    freeze = assume_freeze or (freeze_et_date(root, FREEZE_COMMIT) if FREEZE_COMMIT else None)
    if freeze is None:
        raise Refused("no freeze commit yet: pass --assume-freeze for a projection")
    today, now = _today_et()
    con, facts = _open(root)
    try:
        rows = window_rows(con, facts, date(2026, 10, 20))
    finally:
        facts.close()
    st = look_state(rows, freeze, today, now)
    w = rows.filter(pl.col("et_date") > freeze)
    out: dict[str, Any] = {
        "label_free": True, "today_et": today.isoformat(), "freeze_et_date": freeze.isoformat(),
        "window_games_listed": w.height, "window_final": int(w["final"].sum()) if w.height else 0,
        "window_final_and_stamped": int((w["final"] & w["stamped"]).sum()) if w.height else 0,
        "look_date": st.look_date.isoformat() if st.look_date else None, "look_open": st.open, "reason": st.reason,
        "pending_through_look": list(st.pending),
    }  # fmt: skip
    try:
        if with_s:
            dates = sorted(set(w.filter(pl.col("et_date") < today)["et_date"].to_list()))
            legs = s_legs(con, root / "data/markets/market_asof.duckdb", dates)
            out["s_legs"] = legs.height
            out["s_legs_games"] = legs["game_id"].n_unique() if legs.height else 0
    finally:
        con.close()
    return out


def _results_path(root: Path, rel: str) -> Path:
    return root / rel


def cmd_run(
    root: Path, repo: Path, clock: Callable[[], tuple[date, datetime]] = _today_et
) -> dict[str, Any]:
    verify_rule(repo)
    _check_pins(root)
    out_p = _results_path(repo, OUT_REL)
    if out_p.exists():
        raise Refused(f"{OUT_REL} exists: one look only")
    assert FREEZE_COMMIT is not None
    freeze = freeze_et_date(repo, FREEZE_COMMIT)
    today, now = clock()
    con, facts = _open(root)
    try:
        rows = window_rows(con, facts, date(2026, 10, 20))
        st = look_state(rows, freeze, today, now)
        _need_open(st)
        assert st.look_date is not None
        params = _params(root)
        res = _score_window(con, rows, freeze, st, params)
    finally:
        con.close()
        facts.close()
    s_ready = (repo / LINE_CAL_LIVE_RESULTS).exists()
    res["S"] = "COMPUTE_WITH_run-s" if s_ready else "DEFERRED_LINE_CAL_LIVE"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    return res


def _score_window(
    con: duckdb.DuckDBPyConnection, rows: pl.DataFrame, freeze: date, st: LookState, params: Any
) -> dict[str, Any]:
    assert st.look_date is not None
    win = rows.filter((pl.col("et_date") > freeze) & (pl.col("et_date") <= st.look_date))
    pre = rows.filter(pl.col("et_date") <= freeze)
    both = pl.concat([pre, win])
    scores = load_scores(con, both.filter(pl.col("final"))["game_id"].to_list(), st)
    mu_t = as_of_mu_t(con, both.filter(pl.col("final") & pl.col("stamped")), params, st)
    frame, integ = scored_frame(win, scores, mu_t, params)
    res: dict[str, Any] = {
        "rule": RULE_ID, "rule_sha256": RULE_SHA256, "freeze_commit": FREEZE_COMMIT,
        "freeze_et_date": freeze.isoformat(), "look_date": st.look_date.isoformat(), "run_et_date": st.today.isoformat(),
        "integrity": integ,
    }  # fmt: skip
    if integ["fail"]:
        res["verdict"] = "INTEGRITY_FAIL"
        res["printed"] = score_games(frame, params.total_sd) if frame.height else None
    else:
        res.update(score_games(frame, params.total_sd))
    pf, _ = scored_frame(pre, scores, mu_t, params)
    res["PRE_FREEZE"] = {
        "label": "PRE_FREEZE, no word",
        **(score_games(pf, params.total_sd) if pf.height else {}),
    }
    return res


def cmd_run_s(
    root: Path, repo: Path, clock: Callable[[], tuple[date, datetime]] = _today_et
) -> dict[str, Any]:
    verify_rule(repo)
    _check_pins(root)
    p_path, s_path = _results_path(repo, OUT_REL), _results_path(repo, OUT_S_REL)
    if not p_path.exists():
        raise Refused("run the primary look first")
    if s_path.exists():
        raise Refused(f"{OUT_S_REL} exists: one look only")
    if not (repo / LINE_CAL_LIVE_RESULTS).exists():
        raise Refused("LINE_CAL_LIVE's look is not recorded: family S stays deferred")
    prim = json.loads(p_path.read_text())
    assert FREEZE_COMMIT is not None
    freeze = freeze_et_date(repo, FREEZE_COMMIT)
    today, now = clock()
    con, facts = _open(root)
    try:
        rows = window_rows(con, facts, date(2026, 10, 20))
        st = look_state(rows, freeze, today, now)
        _need_open(st)
        if st.look_date is None or st.look_date.isoformat() != prim["look_date"]:
            raise Refused("look date moved since the primary run")
        params = _params(root)
        win = rows.filter(
            (pl.col("et_date") > freeze)
            & (pl.col("et_date") <= st.look_date)
            & pl.col("final")
            & pl.col("stamped")
        )
        scores = load_scores(con, win["game_id"].to_list(), st)
        mu_t = as_of_mu_t(con, win, params, st)
        realised_by_game = {
            str(g): realised(int(m), int(t), mu_t[str(g)])
            for g, m, t in scores.select("game_id", "margin", "total").iter_rows()
        }
        legs = s_legs(
            con, root / "data/markets/market_asof.duckdb", sorted(set(win["et_date"].to_list()))
        )
        legs = leg_outcomes(con, legs, st)
    finally:
        con.close()
        facts.close()
    res = score_legs(legs, realised_by_game)
    res.update({"rule": RULE_ID, "look_date": prim["look_date"], "run_et_date": today.isoformat()})
    s_path.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    return res


def score_legs(legs: pl.DataFrame, realised_by_game: dict[str, dict[str, bool]]) -> dict[str, Any]:
    cp = json.loads((Path(__file__).resolve().parents[2] / COMBO_PARAMS_REL).read_text())
    legs = add_conditionals(legs, cp["script_params"])
    out: dict[str, Any] = {"S": family_s(legs, realised_by_game)}
    out["same_player_ratio"] = same_player_ratio(legs, realised_by_game, cp["corr"])
    from nba.parlay.combo import TESTED_P_MAX, TESTED_RAW_MAX

    above = legs.filter((pl.col("u") > TESTED_P_MAX) | (pl.col("raw") >= TESTED_RAW_MAX))
    out["above_tested_range_slice"] = family_s(above, realised_by_game) if above.height else {}
    cells: dict[str, Any] = {}
    for stat in STATS:
        for role in ("starter", "bench"):
            d = legs.filter((pl.col("stat") == stat) & (pl.col("role") == role))
            cells[f"{stat}_{role}"] = (
                {s: v for s, v in family_s(d, realised_by_game).items()} if d.height else {}
            )
    out["by_stat_role"] = cells
    yv, u = legs["y"].to_numpy().astype(float), legs["u"].to_numpy().astype(float)
    out["all_window_legs_gap_u"] = float((yv - u).mean()) if legs.height else None
    return out


# ---------------------------------------------------------------- synthetic dry run


def synthetic(
    n_games: int = 800, n_dates: int = 120, gap: float = 0.0, seed: int = 7
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, dict[str, bool]]]:
    """Synthetic games (outcomes drawn from P + ``gap``) and legs; no database."""
    rng = np.random.default_rng(seed)
    d0 = date(2026, 11, 13)
    recs, legs, real = [], [], {}
    for i in range(n_games):
        gid = f"00226{i:05d}"
        d = d0 + timedelta(days=int(i * n_dates / n_games))
        mm, mt = float(rng.normal(0.6, 6.0)), float(rng.normal(228.0, 6.0))
        pr = closed_form(mm, mt, 13.63, 18.82)
        yy = {s: bool(rng.random() < min(1.0, max(0.0, pr[s] + gap))) for s in SCRIPTS}
        real[gid] = yy
        recs.append({"game_id": gid, "et_date": d, "mu_m": mm, "mu_t": mt, "total": int(round(mt + rng.normal(0, 18.82))),
                     **{f"p_{s}": pr[s] for s in SCRIPTS}, **{f"y_{s}": int(yy[s]) for s in SCRIPTS}})  # fmt: skip
        for j in range(16):
            u = float(rng.uniform(0.3, 0.7))
            legs.append({"game_id": gid, "player_id": j // 3, "stat": STATS[j % 4], "et_date": d, "line": 10.5,
                         "u": u, "raw": u, "starter_rate": 0.8 if j < 8 else 0.1, "y": int(rng.random() < u)})  # fmt: skip
    return pl.DataFrame(recs), pl.DataFrame(legs), real


def cmd_dryrun(n_games: int, gap: float) -> dict[str, Any]:
    frame, legs, real = synthetic(n_games=n_games, gap=gap)
    res = score_games(frame, 18.82)
    res.update(score_legs(legs, real))
    res["SYNTHETIC"] = True
    return res


def _words(res: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fam in ("P", "D", "S"):
        if isinstance(res.get(fam), dict):
            out[fam] = {k: v.get("word") for k, v in res[fam].items()}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="game_scripts_forward_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("status")
    s1.add_argument("--data-root", required=True)
    s1.add_argument(
        "--assume-freeze", default=None, help="projection only, before the freeze exists"
    )
    s1.add_argument("--s-legs", action="store_true")
    s2 = sub.add_parser("dryrun")
    s2.add_argument("--games", type=int, default=800)
    s2.add_argument("--gap", type=float, default=0.0)
    s2.add_argument("--out", default=None)
    for name in ("run", "run-s"):
        sp = sub.add_parser(name)
        sp.add_argument("--data-root", required=True)
    a = ap.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    try:
        if a.cmd == "status":
            res = cmd_status(
                Path(a.data_root),
                date.fromisoformat(a.assume_freeze) if a.assume_freeze else None,
                a.s_legs,
            )
            print(json.dumps(res, indent=1, default=str))
        elif a.cmd == "dryrun":
            res = cmd_dryrun(a.games, a.gap)
            if a.out:
                Path(a.out).write_text(
                    json.dumps(res, indent=1, sort_keys=True, default=str) + "\n"
                )
            print(json.dumps(_words(res), indent=1))
        elif a.cmd == "run":
            print(json.dumps(_words(cmd_run(Path(a.data_root), repo)), indent=1))
        else:
            print(json.dumps(cmd_run_s(Path(a.data_root), repo), indent=1, default=str)[:4000])
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
