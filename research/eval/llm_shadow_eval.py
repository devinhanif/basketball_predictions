# ruff: noqa: E501
"""LLM_SHADOW_2026_27 harness (rule: docs/prereg/LLM_SHADOW_2026_27.md; DRAFT until frozen).

    M="-m research.eval.llm_shadow_eval"
    .venv/bin/python -W ignore $M gate   --allow-draft --doc docs/prereg/LLM_SHADOW_2026_27.md
    .venv/bin/python -W ignore $M report --doc docs/prereg/LLM_SHADOW_2026_27.md --sha <64 hex>

Stages: ``gate`` (checks 1-4 on a fixture with a STUB provider, 6-7 on the doc and on real leg counts) and ``report``
(live scoring of season 2026 rows; pure functions below). No model is ever called here; no past season is scored.
Exit codes: 0 pass, 1 a check failed, 2 the steward's module ``nba.shadow_llm`` is absent ("module pending"; the
doc is not failed). Real-data reads are counts only, ``season <= 2024``, ``read_only=True``. Season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.checkpoint import date_cluster_boot
from research.eval.minutes_hazard_eval import frozen_sha

DOC = Path("docs/prereg/LLM_SHADOW_2026_27.md")
MODEL = "llm_shadow"
PROD = "props_context_residual"
STATS = ("pts", "reb", "ast", "fg3m")
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
CLIP = (0.01, 0.99)
B = 10_000
SEED = 2026
LEVEL = 0.9818
FORWARD_SEASON = 2026
D_POINT = -0.002
KILL_POINT = 0.005
L30_KILL_POINT = 0.010
L30_MIN_LEGS = 2_000
MIN_LEGS_PER_GAME = 30
LOOKS = {"L60": (60, 5_000), "L120": (120, 10_000)}
PLACEHOLDERS = ("<<DEVIN FILLS>>", "<<FILLED AT FREEZE>>")
ODDS_DB = Path("data/odds/odds_history.duckdb")
STUB_SD = 0.03


# ----------------------------------------------------------------------------- metrics (pure)
def clip(p: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.clip(np.asarray(p, dtype=float), CLIP[0], CLIP[1])
    return out


def leg_ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = clip(p)
    y = np.asarray(y, dtype=float)
    return np.asarray(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)), dtype=float)


def blend_logit(p_a: np.ndarray, p_b: np.ndarray, w: float = 0.5) -> np.ndarray:
    a, b = clip(p_a), clip(p_b)
    z = w * np.log(a / (1 - a)) + (1 - w) * np.log(b / (1 - b))
    return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype=float)


def reliability(
    y: np.ndarray, p: np.ndarray, bins: int = 10
) -> tuple[float, list[dict[str, float]]]:
    """ECE and a 10-equal-width-bin reliability table (n per bin)."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    rows: list[dict[str, float]] = []
    ece = 0.0
    for k in range(bins):
        m = idx == k
        n = int(m.sum())
        if n == 0:
            continue
        mp, my = float(p[m].mean()), float(y[m].mean())
        ece += n / len(p) * abs(mp - my)
        rows.append({"bin": k, "n": n, "mean_p": mp, "freq": my})
    return float(ece), rows


def parse_p_ge_full(pred: str | dict[str, Any], line: float) -> float | None:
    """P(Y >= ceil(line)) from the stored ``p_ge_full`` ({"n", "c"}); beyond the stored range 0; None if absent."""
    d = json.loads(pred) if isinstance(pred, str) else pred
    full = d.get("p_ge_full")
    if not full or not full.get("c") or not full.get("n"):
        return None
    k = math.ceil(line)
    c = full["c"]
    if k < 1:
        return 1.0
    return float(c[k - 1]) / float(full["n"]) if k <= len(c) else 0.0


def clv_side(p: np.ndarray, p_over_raw: np.ndarray, p_under_raw: np.ndarray) -> np.ndarray:
    """+1 over, -1 under: argmax of edge_over = p - raw_over vs edge_under = (1-p) - raw_under; ties go over."""
    return np.where(p - p_over_raw >= (1.0 - p) - p_under_raw, 1, -1)


def side_edge(p: np.ndarray, p_over_raw: np.ndarray, p_under_raw: np.ndarray) -> np.ndarray:
    return np.asarray(np.maximum(p - p_over_raw, (1.0 - p) - p_under_raw), dtype=float)


def clv_value(side: np.ndarray, mult_t60: np.ndarray, mult_close: np.ndarray) -> np.ndarray:
    """CLV in probability points on unchanged-line legs: over +(close - t60), under the negative."""
    return np.asarray(side * (mult_close - mult_t60), dtype=float)


def _boot(delta: np.ndarray, dates: np.ndarray, level: float = LEVEL) -> dict[str, Any]:
    r = date_cluster_boot(delta, dates, B=B, seed=SEED, level=level)
    return {"point": r.point, "lo": r.lo, "hi": r.hi, "n": r.n, "n_dates": r.n_dates}


def summarize(legs: pl.DataFrame) -> dict[str, Any]:
    """The pre-registered quantities from a scored-leg table.

    Columns: game_id, game_date, player_id, stat, y, p_llm, p_prod, p_mkt, p_over_raw, p_under_raw,
    mult_t60, mult_close (null = no close or changed line)."""
    if legs.height == 0:
        return {"n": 0}
    y = legs["y"].to_numpy()
    pl_, pp, pm = legs["p_llm"].to_numpy(), legs["p_prod"].to_numpy(), legs["p_mkt"].to_numpy()
    d = legs["game_date"].to_numpy()
    ll_l, ll_p, ll_m = leg_ll(y, pl_), leg_ll(y, pp), leg_ll(y, pm)
    out: dict[str, Any] = {
        "n": int(legs.height),
        "n_dates": int(legs["game_date"].n_unique()),
        "ll_llm": float(ll_l.mean()),
        "ll_prod": float(ll_p.mean()),
        "ll_mkt": float(ll_m.mean()),
        "D_vs_prod": _boot(ll_l - ll_p, d),
        "D_vs_prod_ci95": _boot(ll_l - ll_p, d, 0.95),
        "llm_minus_mkt": _boot(ll_l - ll_m, d),
        "prod_minus_mkt": _boot(ll_p - ll_m, d),
    }
    pb = blend_logit(pl_, pp, 0.5)
    ll_b = leg_ll(y, pb)
    out["blend"] = {
        "ll": float(ll_b.mean()),
        "vs_prod": _boot(ll_b - ll_p, d),
        "vs_llm": _boot(ll_b - ll_l, d),
        "vs_mkt": _boot(ll_b - ll_m, d),
        "beats_both_point": bool(ll_b.mean() < min(ll_l.mean(), ll_p.mean())),
    }
    out["calibration"] = {}
    for name, p in (("llm", pl_), ("prod", pp), ("mkt", pm)):
        ece, tab = reliability(y, clip(p))
        out["calibration"][name] = {
            "ece": ece,
            "bins": tab,
            "bias": float(clip(p).mean() - y.mean()),
        }
    out["by_stat"] = {}
    for s in STATS:
        sub = legs.filter(pl.col("stat") == s)
        if sub.height:
            dd = leg_ll(sub["y"].to_numpy(), sub["p_llm"].to_numpy()) - leg_ll(
                sub["y"].to_numpy(), sub["p_prod"].to_numpy()
            )
            out["by_stat"][s] = _boot(dd, sub["game_date"].to_numpy())
    cl = legs.filter(pl.col("mult_close").is_not_null())
    if cl.height:
        o, u = cl["p_over_raw"].to_numpy(), cl["p_under_raw"].to_numpy()
        m0, m1 = cl["mult_t60"].to_numpy(), cl["mult_close"].to_numpy()
        c_l = clv_value(clv_side(cl["p_llm"].to_numpy(), o, u), m0, m1)
        c_p = clv_value(clv_side(cl["p_prod"].to_numpy(), o, u), m0, m1)
        out["clv"] = {
            "n": int(cl.height),
            "llm": float(c_l.mean()),
            "prod": float(c_p.mean()),
            "llm_minus_prod": _boot(c_l - c_p, cl["game_date"].to_numpy(), 0.95),
            "shuffle_null_llm": _shuffle_null(cl),
        }
    return out


def _shuffle_null(cl: pl.DataFrame, seeds: int = 20) -> float:
    """Mean CLV when the LLM's p is permuted within (game_date, stat): a side choice with no model information."""
    key = (cl["game_date"].cast(str) + "|" + cl["stat"]).to_numpy()
    groups = {k: np.flatnonzero(key == k) for k in np.unique(key)}
    p = cl["p_llm"].to_numpy()
    o, u = cl["p_over_raw"].to_numpy(), cl["p_under_raw"].to_numpy()
    m0, m1 = cl["mult_t60"].to_numpy(), cl["mult_close"].to_numpy()
    vals = []
    for s in range(1, seeds + 1):
        rng = np.random.default_rng(s)
        q = p.copy()
        for ix in groups.values():
            q[ix] = p[rng.permutation(ix)]
        vals.append(float(clv_value(clv_side(q, o, u), m0, m1).mean()))
    return float(np.mean(vals))


def decide(summary: dict[str, Any], look: str, guards: dict[str, bool]) -> dict[str, Any]:
    """Apply section 4 verbatim to a summary at a decision look (L60, L120, END). Information only."""
    if (
        look == "L30"
    ):  # monitoring plus the cost-only early stop (amendment A1); never an efficacy claim
        d30 = summary.get("D_vs_prod", {})
        if (
            summary.get("n_dates", 0) >= 30
            and summary.get("n", 0) >= L30_MIN_LEGS
            and d30.get("point", 0.0) >= L30_KILL_POINT
            and d30.get("lo", -1.0) > 0
        ):
            return {"look": look, "outcome": "KILL_COST"}
        return {"look": look, "outcome": "MONITOR"}
    if look not in LOOKS and look != "END":
        return {"look": look, "outcome": "MONITOR"}
    if look in LOOKS:
        dmin, nmin = LOOKS[look]
        if summary.get("n_dates", 0) < dmin or summary.get("n", 0) < nmin:
            return {"look": look, "outcome": "KEEP_RUNNING", "why": "below the n gate"}
    elif summary.get("n_dates", 0) < LOOKS["L120"][0]:
        return {"look": look, "outcome": "KEEP_RUNNING", "why": "END before the L120 date count"}
    D = summary["D_vs_prod"]
    kill = D["point"] >= KILL_POINT and D["lo"] > 0
    p1 = D["point"] <= D_POINT
    p2 = D["hi"] < 0
    p3 = summary["ll_llm"] <= summary["ll_mkt"]
    g_ok = all(guards.get(k, False) for k in ("G1", "G2", "G3"))
    if kill:
        return {"look": look, "outcome": "KILL", "P": [p1, p2, p3], "guards": guards}
    if p1 and p2 and p3 and g_ok:
        return {
            "look": look,
            "outcome": "PASS_INFORMATION_ONLY",
            "P": [p1, p2, p3],
            "guards": guards,
        }
    return {"look": look, "outcome": "KEEP_RUNNING", "P": [p1, p2, p3], "guards": guards}


# ----------------------------------------------------------------------------- live leg table
def build_legs(
    nba_con: duckdb.DuckDBPyConnection, market_con: duckdb.DuckDBPyConnection
) -> pl.DataFrame:
    """Scored legs for season 2026 from forward_predictions (llm_shadow v1 vs production) and live_book_odds.

    Rules (doc section 2): LLM row = latest with made_at <= tip - 30; production row = latest with
    made_at <= tip - 60; scored at the T-60 DraftKings line; played = minutes > 0; market = DK de-vigged
    over at the same point; CLV population = close row at the same point."""
    base = nba_con.execute(
        """
        WITH l AS (SELECT *, row_number() OVER (PARTITION BY game_id, target, player_id
                                                ORDER BY made_at DESC, run_id DESC) rn
                   FROM forward_predictions WHERE model_name = ? AND version = 'v1'
                     AND made_at < tipoff AND made_at <= tipoff - 30 * INTERVAL 1 MINUTE),
             p AS (SELECT *, row_number() OVER (PARTITION BY game_id, target, player_id
                                                ORDER BY made_at DESC, run_id DESC) rn
                   FROM forward_predictions WHERE model_name = ?
                     AND made_at <= tipoff - 60 * INTERVAL 1 MINUTE)
        SELECT l.game_id, g.game_date, l.player_id, l.target AS stat, l.prediction AS pl, p.prediction AS pp,
               l.tipoff, b.pts, b.reb, b.ast, b.fg3m
        FROM l JOIN p ON p.game_id = l.game_id AND p.player_id = l.player_id AND p.target = l.target AND p.rn = 1
        JOIN games g ON g.game_id = l.game_id
        JOIN player_game_stats b ON b.game_id = l.game_id AND b.player_id = l.player_id AND b.minutes > 0
        WHERE l.rn = 1 AND g.season = ? AND g.home_pts > 0 AND g.away_pts > 0
        """,
        [MODEL, PROD, FORWARD_SEASON],
    ).fetchall()
    cols = [
        "game_id",
        "game_date",
        "player_id",
        "stat",
        "pl",
        "pp",
        "tipoff",
        "pts",
        "reb",
        "ast",
        "fg3m",
    ]
    rows: list[dict[str, Any]] = []
    quotes = _dk_quotes(market_con)
    for r in base:
        rec = dict(zip(cols, r, strict=True))
        pj = json.loads(rec["pl"])
        line = float(pj["line"])
        pprod = parse_p_ge_full(rec["pp"], line)
        q = quotes.get((rec["game_id"], int(rec["player_id"]), rec["stat"]))
        if pprod is None or q is None or q["point_t60"] != line:
            continue
        stat_val = rec[rec["stat"]]
        if stat_val is None or float(stat_val) == line:
            continue
        unchanged = q.get("point_close") == line
        rows.append(
            {
                "game_id": rec["game_id"],
                "game_date": rec["game_date"],
                "player_id": int(rec["player_id"]),
                "stat": rec["stat"],
                "y": 1.0 if float(stat_val) > line else 0.0,
                "p_llm": float(pj["p_over"]),
                "p_prod": pprod,
                "p_mkt": q["mult_t60"],
                "p_over_raw": q["over_raw"],
                "p_under_raw": q["under_raw"],
                "mult_t60": q["mult_t60"],
                "mult_close": q["mult_close"] if unchanged else None,
            }
        )
    schema = {
        "game_id": pl.Utf8, "game_date": pl.Date, "player_id": pl.Int64, "stat": pl.Utf8,
        "y": pl.Float64, "p_llm": pl.Float64, "p_prod": pl.Float64, "p_mkt": pl.Float64,
        "p_over_raw": pl.Float64, "p_under_raw": pl.Float64, "mult_t60": pl.Float64,
        "mult_close": pl.Float64,
    }  # fmt: skip
    return pl.DataFrame(rows, schema=schema)


def _dk_quotes(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, int, str], dict[str, Any]]:
    """DraftKings (game, player, stat) -> the T-60 main line quote and, if any, the close quote."""
    df = con.execute(
        """
        SELECT game_id, player_id, market, snapshot_kind, side, point, implied_prob_raw, implied_prob
        FROM live_book_odds WHERE book = 'draftkings' AND player_id IS NOT NULL
          AND market IN ('player_points','player_rebounds','player_assists','player_threes')
          AND snapshot_kind IN ('t60','close') AND received_at < tipoff
        """
    ).pl()
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    if df.height == 0:
        return out
    ov = df.filter(pl.col("side") == "over")
    un = df.filter(pl.col("side") == "under").select(
        "game_id",
        "player_id",
        "market",
        "snapshot_kind",
        "point",
        pl.col("implied_prob_raw").alias("under_raw"),
    )
    pair = ov.join(un, on=["game_id", "player_id", "market", "snapshot_kind", "point"]).filter(
        pl.col("implied_prob").is_not_null() & (pl.col("point") % 1 != 0)
    )
    for kind in ("t60", "close"):
        sub = (
            pair.filter(pl.col("snapshot_kind") == kind)
            .with_columns((pl.col("implied_prob") - 0.5).abs().alias("_d"))
            .sort(["game_id", "player_id", "market", "_d", "point"])
        )
        for r in sub.unique(
            subset=["game_id", "player_id", "market"], keep="first", maintain_order=True
        ).iter_rows(named=True):
            key = (r["game_id"], int(r["player_id"]), MARKET_STAT[r["market"]])
            d = out.setdefault(key, {})
            if kind == "t60":
                d.update(
                    point_t60=r["point"],
                    mult_t60=r["implied_prob"],
                    over_raw=r["implied_prob_raw"],
                    under_raw=r["under_raw"],
                )
            else:
                d.update(point_close=r["point"], mult_close=r["implied_prob"])
    return {k: v for k, v in out.items() if "point_t60" in v}


# ----------------------------------------------------------------------------- gate
class Check:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name, self.status, self.detail = name, status, detail

    def line(self) -> str:
        return f"[{self.status:7}] {self.name}" + (f": {self.detail}" if self.detail else "")


def doc_checks(doc: Path, allow_draft: bool) -> list[Check]:
    text = doc.read_text()
    out = [
        Check(
            "doc has whole-line marker", "PASS" if "\n=== RESULTS BELOW ===\n" in text else "FAIL"
        )
    ]
    out.append(Check("frozen-section sha256", "INFO", frozen_sha(doc)))
    head = text.split("\n=== RESULTS BELOW ===\n")[0]
    left = [p for p in PLACEHOLDERS if p in head]
    out.append(
        Check(
            "open fields filled (check 6)",
            "PASS" if not left else ("PENDING" if allow_draft else "FAIL"),
            ", ".join(left),
        )
    )
    m = re.search(r"PROMPT_SHA256 = ([0-9a-f]{64})", head)
    pf = Path("configs/llm_shadow_prompt_v1.txt")
    if m and pf.exists():
        ok = hashlib.sha256(pf.read_bytes()).hexdigest() == m.group(1)
        out.append(Check("prompt sha256 equals file", "PASS" if ok else "FAIL"))
    out.append(
        Check(
            "real-provider cost check (check 5)",
            "PENDING",
            "measured live by the steward's job once the key exists",
        )
    )
    return out


def legcount_check(db: Path = ODDS_DB) -> Check:
    """Check 7: DraftKings main-line legs per game, season <= 2024, counts only (no outcomes)."""
    if not db.exists():
        return Check("leg-count projection (check 7)", "SKIP", f"{db} absent")
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            """
            WITH L AS (SELECT season, game_id, market, player_id,
                         sum((side='over')::int) o, sum((side='under')::int) u, any_value(point) pt
                       FROM odds_history WHERE season <= 2024 AND snapshot_kind = 't60' AND book = 'draftkings'
                         AND player_id IS NOT NULL
                         AND market IN ('player_points','player_rebounds','player_assists','player_threes')
                       GROUP BY ALL)
            SELECT season, count(DISTINCT game_id), sum((o >= 1 AND u >= 1 AND pt % 1 = 0.5)::int)
            FROM L GROUP BY 1 ORDER BY 1
            """
        ).fetchall()
    finally:
        con.close()
    per = {int(s): n / g for s, g, n in rows if g}
    ok = bool(per) and all(v >= MIN_LEGS_PER_GAME for v in per.values())
    return Check(
        "leg-count projection (check 7)",
        "PASS" if ok else "FAIL",
        ", ".join(f"{s}: {v:.1f}/game" for s, v in per.items()),
    )


def _import_module() -> tuple[Any, Any, Any] | None:
    try:
        prov = importlib.import_module("nba.shadow_llm.provider")
        pay = importlib.import_module("nba.shadow_llm.payload")
        run = importlib.import_module("nba.shadow_llm.runner")
    except ModuleNotFoundError:
        return None
    if not (
        hasattr(prov, "Provider") and hasattr(pay, "build_payload") and hasattr(run, "run_game")
    ):
        return None
    return prov, pay, run


class StubProvider:
    """Returns production's probability plus seeded noise for every requested leg (a fake, never a model)."""

    model_id = "stub"
    is_real = False  # never counts toward the budget or needs a key

    def __init__(self, mode: str = "ok", seed: int = 0) -> None:
        self.mode, self.rng, self.calls = mode, np.random.default_rng(seed), 0
        self.legs_out: list[dict[str, Any]] = []

    def forecast(self, game_payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        legs = [
            {
                "player_id": int(g["player_id"]),
                "stat": g["stat"],
                "line": float(g["line"]),
                "p_over": float(
                    np.clip(float(g["p_over_prod"]) + self.rng.normal(0, STUB_SD), 0.02, 0.98)
                ),
                "reason": "stub",
            }
            for g in game_payload["legs"]
        ]
        self.legs_out = legs
        body = {"forecasts": legs}
        text = {
            "ok": json.dumps(body),
            "prose": "I lean toward the over on most of these because the starters are rested.",
            "fenced": "```json\n" + json.dumps(body) + "\n```",
            "extra_key": json.dumps({**body, "note": "x"}),
            "missing_leg": json.dumps({"forecasts": legs[:-1]}),
            "duplicate_leg": json.dumps({"forecasts": [*legs, legs[0]]}),
            "p_one": json.dumps({"forecasts": [{**legs[0], "p_over": 1.0}, *legs[1:]]}),
        }[self.mode]
        return {
            "raw_text": text,
            "model_id": self.model_id,
            "prompt_sha256": "0" * 64,
            "usage": {"tokens_in": 100, "tokens_out": 50},
        }


TIP = dt.datetime(2026, 11, 3, 0, 0, 0)  # naive UTC
GAME = "0022600001"
PLAYERS = {101: 1, 102: 1, 103: 2, 104: 2}
LINE = {101: 24.5, 102: 17.5, 103: 20.5, 104: 11.5}


def build_fixture(root: Path) -> dict[str, Path]:
    """Facts, forward/nba, market and shadow DBs for one planted game (a stand-in schema, see doc section 7)."""
    from nba.daily.store import ensure_tables
    from nba.odds.live_close import DDL as MARKET_DDL

    paths = {k: root / f"{k}.duckdb" for k in ("facts", "nba", "market", "shadow")}
    f = duckdb.connect(str(paths["facts"]))
    f.execute(Path("nba/facts/schema.sql").read_text())
    ins = "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)"

    def t(m: float) -> dt.datetime:
        return TIP + dt.timedelta(minutes=m)

    rows = [
        (1, f"game:{GAME}", "tips_at", None, None, "tip", t(-2880), TIP, None, "schedule", 1.0),
        (2, f"game:{GAME}", "home_team", "team:1", None, None, t(-2880), None, None, "games", 1.0),
        (3, f"game:{GAME}", "away_team", "team:2", None, None, t(-2880), None, None, "games", 1.0),
        (
            4,
            "player:101",
            "listed_status",
            f"game:{GAME}",
            None,
            "questionable",
            t(-180),
            None,
            None,
            "nba_official_report",
            1.0,
        ),
        (
            5,
            "player:102",
            "announced_starter",
            f"game:{GAME}",
            1.0,
            "Confirmed",
            t(-30),
            None,
            None,
            "lineups",
            1.0,
        ),
        (
            6,
            "player:103",
            "listed_status",
            f"game:{GAME}",
            None,
            "out",
            t(5),
            None,
            None,
            "nba_official_report",
            1.0,
        ),
        (
            7,
            "player:104",
            "listed_status",
            f"game:{GAME}",
            None,
            "probable",
            t(-90),
            None,
            None,
            "nba_official_report",
            1.0,
        ),
    ]
    f.executemany(ins, rows)
    f.close()
    n = duckdb.connect(str(paths["nba"]))
    ensure_tables(n)
    n.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT,"
        " home_pts INT, away_pts INT, national_tv VARCHAR)"
    )
    n.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT,"
        " pts INT, reb INT, ast INT, fg3m INT)"
    )
    for i in range(10):  # ten prior games for trailing-10
        gid = f"00225{i:05d}"
        n.execute(
            "INSERT INTO games VALUES (?,?,?,?,?,?,?,NULL)",
            [gid, (TIP - dt.timedelta(days=20 - i)).date(), 2025, 1, 2, 100, 99],
        )
        for pid, team in PLAYERS.items():
            n.execute(
                "INSERT INTO player_game_stats VALUES (?,?,?,?,?,?,?,?)",
                [gid, pid, team, 30.0, 20, 6, 4, 2],
            )
    n.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,NULL)", [GAME, TIP.date(), 2026, 1, 2, 0, 0])
    for pid in PLAYERS:
        c = [int(199 * max(0.0, 1 - k / (LINE[pid] * 2))) for k in range(1, 40)]
        pred = {"mean": LINE[pid], "std": 5.0, "p_ge_full": {"n": 199, "c": c}}
        n.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["prod-run", t(-70), GAME, TIP, PROD, "v2", "pts", pid, json.dumps(pred)],
        )
    n.close()
    m = duckdb.connect(str(paths["market"]))
    m.execute(MARKET_DDL)
    for pid, line in LINE.items():
        for side, pr in (("over", 0.52), ("under", 0.48)):
            m.execute(
                "INSERT INTO live_book_odds VALUES ('the_odds_api_live',?,?, 't60',?,?,?,?,'e1','draftkings','player_points','x','P',?,?,?,-110,?,?,?,'f')",
                [GAME, TIP.date(), t(-64), t(-62), TIP, TIP, pid, side, line, pr, pr, t(-70)],
            )
    m.close()
    return paths


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def _count_rows(path: Path, where: str = "model_name = 'llm_shadow'") -> int:
    c = duckdb.connect(str(path), read_only=True)
    try:
        return int(_scalar(c, f"SELECT count(*) FROM forward_predictions WHERE {where}"))  # noqa: S608
    finally:
        c.close()


def _canon_lines(rows: list[tuple[str, int, str, float, str]]) -> str:
    return "\n".join(json.dumps(r, sort_keys=True) for r in sorted(rows))


class Env:
    """One fresh world for a check: DB paths, game id, tip, a valid request time and extra run_game kwargs."""

    def __init__(
        self,
        paths: dict[str, Path],
        game: str,
        tip: dt.datetime,
        now: dt.datetime,
        kw: dict[str, Any] | None = None,
    ) -> None:
        self.paths, self.game, self.tip, self.now, self.kw = paths, game, tip, now, kw or {}

    def run(self, runner: Any, prov: Any, now: dt.datetime | None = None) -> dict[str, Any]:
        kw = dict(self.kw)
        if "clock_at" in kw:  # the steward's runner reads its own clock for made_at
            at = kw.pop("clock_at")
            kw["clock"] = lambda: at
            kw["sleep"] = lambda _s: None
        out: dict[str, Any] = runner.run_game(
            self.game, provider=prov, now=now or self.now, paths=self.paths, **kw
        )
        return out

    def payload(self, payload_mod: Any) -> dict[str, Any]:
        kw = {k: v for k, v in self.kw.items() if k == "cfg"}
        out: dict[str, Any] = payload_mod.build_payload(
            self.game, now=self.now, paths=self.paths, **kw
        )
        return out


def own_env(root: Path) -> Env:
    """This harness's stand-in world (the reference interface), request time tip - 65 min."""
    return Env(build_fixture(root), GAME, TIP, TIP - dt.timedelta(minutes=65))


def steward_env(root: Path, worktree: Path) -> Env:
    """The steward's own fixture world (tests/shadow_llm/conftest.py::make_world), imported from a worktree path."""
    sys.path.insert(0, str(worktree))
    try:
        conf = importlib.import_module("tests.shadow_llm.conftest")
    finally:
        sys.path.remove(str(worktree))
    w = conf.make_world(root)
    return Env(
        dict(w.paths),
        conf.GAME,
        conf.TIP,
        conf.NOW,
        {"cfg": w.cfg, "clock_at": conf.NOW + dt.timedelta(seconds=20)},
    )


def fixture_checks(mod: tuple[Any, Any, Any], make_env: Any = own_env) -> list[Check]:
    from nba.daily.canonical_dump import canonical_csv
    from nba.facts.query import PostTipError, as_of

    _, payload_mod, runner = mod
    out: list[Check] = []

    def fresh() -> tuple[tempfile.TemporaryDirectory[str], Env]:
        td = tempfile.TemporaryDirectory()
        return td, make_env(Path(td.name))

    # (a) plumbing
    td, env = fresh()
    with td:
        prov = StubProvider("ok")
        res = env.run(runner, prov)
        n_rows = _count_rows(env.paths["nba"])
        con = duckdb.connect(str(env.paths["nba"]), read_only=True)
        try:
            late = _scalar(
                con,
                "SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow' AND NOT made_at < tipoff",
            )
            csv_text = canonical_csv(con)
            got = con.execute(
                "SELECT game_id, player_id, target, prediction FROM forward_predictions WHERE model_name='llm_shadow'"
            ).fetchall()
        finally:
            con.close()
        got_lines = _canon_lines(
            [
                (g, int(p), t, float(json.loads(j)["p_over"]), json.loads(j)["reason"])
                for g, p, t, j in got
            ]
        )
        want_lines = _canon_lines(
            [
                (env.game, int(x["player_id"]), x["stat"], float(x["p_over"]), x["reason"])
                for x in prov.legs_out
            ]
        )
        same = (
            hashlib.sha256(got_lines.encode()).hexdigest()
            == hashlib.sha256(want_lines.encode()).hexdigest()
        )
        ok_a = n_rows == len(prov.legs_out) > 0 and late == 0 and same and "llm_shadow" in csv_text
        out.append(
            Check(
                "(a) plumbing + canonical dump byte-identical",
                "PASS" if ok_a else "FAIL",
                f"rows={n_rows} legs={len(prov.legs_out)} late={late} same={same} status={res.get('status')}",
            )
        )
    # (b) as-of assertion
    td, env = fresh()
    with td:
        pl_ = env.payload(payload_mod)
        cutoff = env.tip - dt.timedelta(minutes=60)
        ks = [
            dt.datetime.fromisoformat(str(x["known_at"])).replace(tzinfo=None) for x in pl_["facts"]
        ]
        text = json.dumps(pl_, default=str)
        planted_absent = env.game != GAME or ("player:103" not in text and "Confirmed" not in text)
        fcon = duckdb.connect(str(env.paths["facts"]), read_only=True)
        try:
            try:
                as_of(fcon, env.game, env.tip + dt.timedelta(minutes=1))
                refused = False
            except PostTipError:
                refused = True
        finally:
            fcon.close()
        out.append(
            Check(
                "(b) every fact known_at <= tip-60; planted post-tip/T-30 facts absent; as_of refuses post-tip",
                "PASS" if ks and max(ks) <= cutoff and planted_absent and refused else "FAIL",
                f"n_facts={len(ks)}",
            )
        )
    # (c) refuse after tip
    td, env = fresh()
    with td:
        prov = StubProvider("ok")
        try:
            res = env.run(runner, prov, now=env.tip + dt.timedelta(minutes=1))
            status = str(res.get("status"))
        except Exception as e:  # LeakageError / PostTipError are acceptable refusals
            status = type(e).__name__
        refused_ok = _count_rows(env.paths["nba"]) == 0 and prov.calls == 0
        out.append(
            Check(
                "(c) refuse after tip: zero rows, zero provider calls",
                "PASS" if refused_ok else "FAIL",
                status,
            )
        )
    # (d) schema violations
    bad: list[str] = []
    for mode in ("prose", "fenced", "extra_key", "missing_leg", "duplicate_leg", "p_one"):
        td, env = fresh()
        with td:
            prov = StubProvider(mode)
            res = env.run(runner, prov)
            logged = False
            if env.paths["shadow"].exists():
                sc = duckdb.connect(str(env.paths["shadow"]), read_only=True)
                try:
                    logged = (
                        _scalar(
                            sc,
                            "SELECT count(*) FROM llm_shadow_calls WHERE status = 'schema_violation'",
                        )
                        == 1
                    )
                finally:
                    sc.close()
            if not (
                _count_rows(env.paths["nba"]) == 0
                and logged
                and prov.calls == 1
                and res.get("status") == "schema_violation"
            ):
                bad.append(f"{mode}:{res.get('status')}")
    out.append(
        Check(
            "(d) schema violations: discarded, logged, no row, no retry",
            "PASS" if not bad else "FAIL",
            ",".join(bad),
        )
    )
    return out


def run_gate(doc: Path, allow_draft: bool, module_path: Path | None = None) -> int:
    checks = doc_checks(doc, allow_draft)
    checks.append(legcount_check())
    if module_path is not None:  # gate only: import nba.shadow_llm from a worktree
        sys.path.insert(0, str(module_path))
        import nba

        nba.__path__.append(str(module_path / "nba"))  # main tree wins; only nba.shadow_llm is new
    mod = _import_module()
    if mod is None:
        checks.append(
            Check(
                "checks 1-4 (a-d) on the stub",
                "PENDING",
                "module pending: nba.shadow_llm not importable",
            )
        )
    else:
        if module_path is not None:
            checks.extend(fixture_checks(mod, lambda root: steward_env(root, module_path)))
        else:
            checks.extend(fixture_checks(mod))
    for c in checks:
        print(c.line())
    if any(c.status == "FAIL" for c in checks):
        return 1
    return 2 if mod is None else 0


def run_report(doc: Path, sha: str, nba_db: Path, market_db: Path, out_dir: Path) -> int:
    actual = frozen_sha(doc)
    if actual != sha:
        print(f"STOP: doc sha256 {actual} != --sha {sha}")
        return 1
    nba = duckdb.connect(str(nba_db), read_only=True)
    mkt = duckdb.connect(str(market_db), read_only=True)
    try:
        legs = build_legs(nba, mkt)
    finally:
        nba.close()
        mkt.close()
    summ = summarize(legs)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    (out_dir / f"weekly_{stamp}.json").write_text(json.dumps(summ, indent=2, default=str))
    print(
        json.dumps(
            {k: summ.get(k) for k in ("n", "n_dates", "ll_llm", "ll_prod", "ll_mkt", "D_vs_prod")},
            default=str,
        )
    )
    print("descriptive; decisions use only the immutable checkpoint at L60/L120/END")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="stage", required=True)
    g = sub.add_parser("gate")
    g.add_argument("--doc", type=Path, default=DOC)
    g.add_argument("--allow-draft", action="store_true")
    g.add_argument(
        "--module-path",
        type=Path,
        default=None,
        help="worktree root to import nba.shadow_llm and its fixture world from (gate only)",
    )
    r = sub.add_parser("report")
    r.add_argument("--doc", type=Path, default=DOC)
    r.add_argument("--sha", required=True)
    r.add_argument("--nba-db", type=Path, default=Path("nba.duckdb"))
    r.add_argument("--market-db", type=Path, default=Path("data/markets/market_asof.duckdb"))
    r.add_argument("--out", type=Path, default=Path("reports/llm_shadow"))
    a = ap.parse_args(argv)
    if a.stage == "gate":
        return run_gate(a.doc, a.allow_draft, a.module_path)
    return run_report(a.doc, a.sha, a.nba_db, a.market_db, a.out)


if __name__ == "__main__":
    sys.exit(main())
