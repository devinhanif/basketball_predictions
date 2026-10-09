"""Immutable checkpoint writer and scorer for the pre-registered forward shadow arms.

Implements docs/FORWARD_PREREG_2026_27.md section 8 (rules in sections 1-7). Reads
``forward_scores_elig`` (``nba.daily.settle``: eligible-prediction rows scored with
integer-support CRPS), ``forward_t30_decisions``, ``games``, ``player_game_stats``,
``player_availability`` and, when present, the lineup collector database read-only.
Nothing here writes to the DuckDB file.

Only the snapshots written here are decision inputs (rule 10). A snapshot is written once:
``data/checkpoints/<look>_<date>.json`` (plus one pair-table parquet per arm-stat and a markdown
summary) and is never overwritten. ``--descriptive`` writes elsewhere and spends nothing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import duckdb
import numpy as np
import polars as pl

from nba.daily.season import FORWARD_SEASON
from nba.lineups.analysis import wilson
from nba.props.full_support import crps_int  # noqa: F401  (spec section 8.1 entry point)

DEFAULT_OUT_DIR = Path("data/checkpoints")
ARM_T30 = "props_context_residual_t30"
ARM_INT = "props_context_residual_int"
ARM_LT = "props_context_residual_lt"
PROD = "props_context_residual"
WIN_ARM = "rung0_injury_elo"
WIN_BASE = "rung0_mov_elo"
WIN_TARGET = "win_prob_home"

B_DEFAULT = 10_000
SEED = 2026
ALPHA_LOOK = 0.0182
LEVEL = 1.0 - ALPHA_LOOK
FLOOR = -0.005
HARM = 0.005
SLICE_MIN_N = 300
SLICE_HARM = 0.01
COVERAGE_MIN_N = 3000
LOGGED_SHARE_LB = 0.70
OPEN_SHARE_FLAG_LB = 0.50
INT_MAX_DP = 0.0025
INT_MEAN_TOL = 1e-6

#: The fixed multiplicity family (rule 7). Order is the report order.
FAMILY: tuple[str, ...] = (
    "t30_pts", "t30_reb", "t30_ast", "t30_fg3m",
    "int_reb", "int_ast", "int_fg3m",
    "lt_pts",
    "win",
)  # fmt: skip

#: key -> (arm model, comparator model, stat or target, decision metric column)
SPEC: dict[str, tuple[str, str, str, str]] = {
    "t30_pts": (ARM_T30, PROD, "pts", "delta_int"),
    "t30_reb": (ARM_T30, PROD, "reb", "delta_int"),
    "t30_ast": (ARM_T30, PROD, "ast", "delta_int"),
    "t30_fg3m": (ARM_T30, PROD, "fg3m", "delta_int"),
    "int_reb": (ARM_INT, PROD, "reb", "delta_pinball19"),
    "int_ast": (ARM_INT, PROD, "ast", "delta_pinball19"),
    "int_fg3m": (ARM_INT, PROD, "fg3m", "delta_pinball19"),
    "lt_pts": (ARM_LT, PROD, "pts", "delta_int"),
    "win": (WIN_ARM, WIN_BASE, WIN_TARGET, "delta_ll"),
}
SAME_RUN = frozenset({"int_reb", "int_ast", "int_fg3m", "lt_pts"})

#: (min dates, min paired player-games); win has none (tripwire only).
MIN_GATE: dict[str, tuple[int, int]] = {
    "t30_pts": (60, 5000), "t30_reb": (60, 5000), "t30_ast": (120, 10000),
    "t30_fg3m": (120, 10000),
    "int_reb": (14, 1500), "int_ast": (14, 1500), "int_fg3m": (14, 1500),
    "lt_pts": (120, 10000),
    "win": (0, 0),
}  # fmt: skip
LOOK_DATES: dict[str, int | None] = {"L14": 14, "L30": 30, "L60": 60, "L120": 120, "END": None}
EFFICACY_LOOKS = ("L30", "L60", "L120", "END")
DROP_LOOKS = ("L60", "L120", "END")
#: T30 direction check (more negative than the backtest upper end => suspected leak).
DIRECTION: dict[str, float] = {"t30_pts": -0.041, "t30_reb": -0.018, "t30_ast": -0.0089}
PIT_WINDOWS = ((0.10, 0.08, 0.12), (0.20, 0.17, 0.23))


class GateNotMet(RuntimeError):
    """The minimum-n gate for the requested look is not met (use --descriptive to inspect)."""


class BootResult(NamedTuple):
    point: float
    lo: float
    hi: float
    p: float
    n: int
    n_dates: int


# ----------------------------------------------------------------------------- statistics


def date_cluster_boot(
    delta: np.ndarray,
    game_date: np.ndarray,
    B: int = B_DEFAULT,
    seed: int = SEED,
    level: float = LEVEL,
) -> BootResult:
    """Percentile bootstrap of the mean resampling whole game dates (ratio of sums, rule 5).

    ``p`` is the two-sided ``min(1, 2 * min(P*(boot >= 0), P*(boot <= 0)))`` floored at
    ``1 / (B + 1)``. NaN interval and p when fewer than two dates."""
    v = np.asarray(delta, dtype=float)
    nan = float("nan")
    if v.size == 0:
        return BootResult(nan, nan, nan, nan, 0, 0)
    _, inv = np.unique(np.asarray(game_date).astype(str), return_inverse=True)
    k = int(inv.max()) + 1
    point = float(v.mean())
    if k < 2:
        return BootResult(point, nan, nan, nan, int(v.size), k)
    sums = np.bincount(inv, weights=v, minlength=k)
    cnts = np.bincount(inv, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(B, k))
    boot = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    a = (1.0 - level) / 2.0
    lo, hi = np.quantile(boot, [a, 1.0 - a])
    p = min(1.0, 2.0 * min(float(np.mean(boot >= 0)), float(np.mean(boot <= 0))))
    return BootResult(point, float(lo), float(hi), max(p, 1.0 / (B + 1)), int(v.size), k)


def bh_adjust(pvals: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg adjusted p-values over the FIXED family of m = 9 tests.

    Missing, NaN or ineligible tests enter with p = 1.0 (never dropped from m)."""
    unknown = set(pvals) - set(FAMILY)
    if unknown:
        raise KeyError(f"not in the pre-registered family: {sorted(unknown)}")
    p = np.array([_pv(pvals.get(k)) for k in FAMILY], dtype=float)
    m = p.size
    order = np.argsort(p, kind="stable")
    ranked = p[order] * m / (np.arange(m) + 1)
    adj_sorted = np.minimum(1.0, np.minimum.accumulate(ranked[::-1])[::-1])
    adj = np.empty(m)
    adj[order] = adj_sorted
    return {k: float(adj[i]) for i, k in enumerate(FAMILY)}


def _pv(v: float | None) -> float:
    return 1.0 if v is None or not np.isfinite(v) else float(v)


# ----------------------------------------------------------------------------- pairing

_PAIR_SCHEMA = [
    "game_id", "game_date", "player_id", "delta_int", "delta_pinball19", "bias_arm", "bias_cmp",
    "cover_arm", "cover_cmp", "y", "pred_arm", "pred_cmp", "run_arm", "run_cmp", "version_cmp",
    "made_at_arm", "tipoff", "snapshot_fetched_at", "starter", "is_home", "early_season",
    "any_out", "pit_lo", "pit_hi",
]  # fmt: skip

_SETTLED_DATES = """
game_date NOT IN (SELECT game_date FROM games WHERE season = {season} AND game_id LIKE '002%'
                  AND (coalesce(home_pts, 0) <= 0 OR coalesce(away_pts, 0) <= 0))
"""


def _slice_meta(
    con: duckdb.DuckDBPyConnection, season: int
) -> dict[tuple[str, int], tuple[Any, ...]]:
    """(game_id, player_id) -> (box starter, is_home, team game number, any player listed out)."""
    try:
        out_exists = "EXISTS (SELECT 1 FROM player_availability a WHERE a.game_id = b.game_id "
        out_exists += "AND lower(a.status) = 'out')"
        rows = con.execute(
            f"""
            WITH tg AS (
                SELECT game_id, home_team AS team_id, season, game_date FROM games
                UNION ALL SELECT game_id, away_team, season, game_date FROM games),
            n AS (SELECT game_id, team_id, row_number() OVER (
                    PARTITION BY team_id, season ORDER BY game_date, game_id) AS tgn FROM tg)
            SELECT b.game_id, b.player_id, coalesce(b.starter, false), b.team_id = g.home_team,
                   n.tgn, {out_exists}
            FROM player_game_stats b JOIN games g USING (game_id)
            LEFT JOIN n ON n.game_id = b.game_id AND n.team_id = b.team_id
            WHERE g.season = ? AND g.game_id LIKE '002%'
            """,
            [season],
        ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {(str(r[0]), int(r[1])): (bool(r[2]), bool(r[3]), r[4], bool(r[5])) for r in rows}


class PairCounts(NamedTuple):
    n_pairs: int
    only_arm: int
    only_comparator: int
    dnp: int
    unscorable: int
    run_mismatch: int


def _elig_rows(
    con: duckdb.DuckDBPyConnection, model: str, target: str, season: int
) -> dict[tuple[str, int], tuple[Any, ...]]:
    rows = con.execute(
        f"""
        SELECT game_id, player_id, game_date, status, rps, pinball19, pred, y, q10, q90, run_id,
               version, made_at, tipoff, snapshot_fetched_at, starter_rate, pit_lo, pit_hi,
               log_loss, brier
        FROM forward_scores_elig
        WHERE model_name = ? AND target = ? AND season = ? AND game_id LIKE '002%'
          AND {_SETTLED_DATES.format(season=int(season))}
        """,
        [model, target, season],
    ).fetchall()
    return {(str(r[0]), int(r[1])): r for r in rows}


def paired_deltas(
    con: duckdb.DuckDBPyConnection,
    arm: str,
    comparator: str,
    stat: str,
    *,
    season: int = FORWARD_SEASON,
    same_run: bool = False,
    meta: dict[tuple[str, int], tuple[Any, ...]] | None = None,
) -> tuple[pl.DataFrame, PairCounts]:
    """Paired prop table for ``arm`` minus ``comparator`` on one stat (rules 1-3).

    Pairs are the key intersection of scored (non-DNP, scorable) eligible rows. Everything else is
    counted, never dropped silently. ``same_run`` additionally requires the same ``run_id``
    (the ``_int`` / ``_lt`` arms)."""
    a = _elig_rows(con, arm, stat, season)
    c = _elig_rows(con, comparator, stat, season)
    meta = meta if meta is not None else _slice_meta(con, season)
    rows: list[dict[str, Any]] = []
    only_arm = only_cmp = dnp = unscorable = mismatch = 0
    for key in sorted(set(a) | set(c)):
        ra, rc = a.get(key), c.get(key)
        if ra is None:
            only_cmp += 1
            continue
        if rc is None:
            only_arm += 1
            continue
        if ra[3] == "dnp" or rc[3] == "dnp":
            dnp += 1
            continue
        if ra[3] != "scored" or rc[3] != "scored":
            unscorable += 1
            continue
        if same_run and ra[10] != rc[10]:
            mismatch += 1
            continue
        m = meta.get(key)
        sr = ra[15]
        starter = bool(sr >= 0.5) if sr is not None else (m[0] if m else None)
        y = float(ra[7])
        rows.append(
            {
                "game_id": key[0], "game_date": str(ra[2]), "player_id": key[1],
                "delta_int": float(ra[4]) - float(rc[4]),
                "delta_pinball19": (
                    float(ra[5]) - float(rc[5]) if ra[5] is not None and rc[5] is not None
                    else float("nan")
                ),
                "bias_arm": float(ra[6]) - y, "bias_cmp": float(rc[6]) - y,
                "cover_arm": _cover(ra, y), "cover_cmp": _cover(rc, y), "y": y,
                "pred_arm": float(ra[6]), "pred_cmp": float(rc[6]),
                "run_arm": str(ra[10]), "run_cmp": str(rc[10]), "version_cmp": str(rc[11]),
                "made_at_arm": ra[12], "tipoff": ra[13], "snapshot_fetched_at": ra[14],
                "starter": starter, "is_home": m[1] if m else None,
                "early_season": (m[2] <= 15) if m and m[2] is not None else None,
                "any_out": m[3] if m else None,
                "pit_lo": ra[16], "pit_hi": ra[17],
            }
        )  # fmt: skip
    df = _frame(rows)
    return df, PairCounts(len(rows), only_arm, only_cmp, dnp, unscorable, mismatch)


def _cover(r: tuple[Any, ...], y: float) -> float:
    return float(r[8] <= y <= r[9]) if r[8] is not None and r[9] is not None else float("nan")


def _frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame({c: [] for c in _PAIR_SCHEMA})
    return pl.DataFrame(rows, infer_schema_length=None).select(_PAIR_SCHEMA)


def paired_win(
    con: duckdb.DuckDBPyConnection, season: int = FORWARD_SEASON
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Injury-Elo minus MOV-Elo per-game log loss on games scored by both (rule 7 / section 7)."""
    a = _elig_rows(con, WIN_ARM, WIN_TARGET, season)
    b = _elig_rows(con, WIN_BASE, WIN_TARGET, season)
    both = sorted(set(a) & set(b))
    rows = [
        {
            "game_id": k[0], "game_date": str(a[k][2]), "player_id": k[1],
            "delta_ll": float(a[k][18]) - float(b[k][18]),
            "delta_brier": float(a[k][19]) - float(b[k][19]),
            "p_arm": float(a[k][6]), "p_cmp": float(b[k][6]), "y": float(a[k][7]),
            "run_arm": str(a[k][10]), "run_cmp": str(b[k][10]), "version_cmp": str(b[k][11]),
            "made_at_arm": a[k][12], "tipoff": a[k][13],
        }
        for k in both
    ]  # fmt: skip
    df = (
        pl.DataFrame(rows, infer_schema_length=None)
        if rows
        else pl.DataFrame({"game_id": [], "game_date": [], "delta_ll": []})
    )
    return df, {"n_pairs": len(both), "mov_elo_only": len(set(b) - set(a))}


# ----------------------------------------------------------------------------- guards


def pit_values(pairs: pl.DataFrame, seed: int = 0) -> np.ndarray:
    """Randomized continuity-corrected PIT (docs/LOWER_TAIL.md): uniform on [F(y-1), F(y)]."""
    d = pairs.sort(["game_id", "player_id"])
    lo = d["pit_lo"].to_numpy().astype(float)
    hi = d["pit_hi"].to_numpy().astype(float)
    u = np.random.default_rng(seed).random(lo.size)
    return np.asarray(lo + u * (hi - lo))


def _slice_guard(pairs: pl.DataFrame, metric: str) -> dict[str, Any]:
    out: dict[str, Any] = {"violations": [], "slices": {}}
    for col in ("starter", "any_out", "early_season", "is_home"):
        if col not in pairs.columns:
            continue
        for val in (True, False):
            sub = pairs.filter(pl.col(col) == val)
            n = sub.height
            mean = float(sub[metric].mean()) if n else float("nan")  # type: ignore[arg-type]
            name = f"{col}={val}"
            out["slices"][name] = {"n": n, "mean_delta": mean}
            if n >= SLICE_MIN_N and mean > SLICE_HARM:
                out["violations"].append(name)
    return out


def _guards(
    key: str,
    pairs: pl.DataFrame,
    metric: str,
    int_fidelity: dict[str, Any] | None,
    t30_share_lb: float | None,
) -> dict[str, Any]:
    n = pairs.height
    g: dict[str, Any] = {}
    if key == "win":
        return g
    ba, bc = float(pairs["bias_arm"].mean()), float(pairs["bias_cmp"].mean())  # type: ignore[arg-type]
    g["bias_abs_ok"] = abs(ba) <= 0.5
    g["bias_rel_ok"] = abs(ba) - abs(bc) <= 0.05
    g["bias_arm"], g["bias_cmp"] = ba, bc
    ca = pairs["cover_arm"].drop_nans().mean()
    cc = pairs["cover_cmp"].drop_nans().mean()
    g["coverage_arm"], g["coverage_cmp"] = _num(ca), _num(cc)
    g["coverage_enforced"] = n >= COVERAGE_MIN_N
    g["coverage_ok"] = (
        True if n < COVERAGE_MIN_N or ca is None else abs(float(ca) - 0.80) <= 0.03  # type: ignore[arg-type]
    )
    sl = _slice_guard(pairs, metric)
    g["slices"], g["slice_violations"] = sl["slices"], sl["violations"]
    g["slices_ok"] = not sl["violations"]
    leak = bool((pairs["made_at_arm"] < pairs["tipoff"]).all()) if n else True
    if key.startswith("t30"):
        snap = pairs["snapshot_fetched_at"]
        cutoff = pairs["tipoff"] - timedelta(minutes=30)
        leak = leak and bool(snap.is_not_null().all()) and bool((snap < cutoff).all())
    g["leak_ok"] = leak
    if key == "lt_pts":
        pit = pit_values(pairs)
        shares = {f"{t:.2f}": float(np.mean(pit <= t)) for t, _, _ in PIT_WINDOWS}
        g["pit_shares"] = shares
        g["pit_ok"] = all(lo <= shares[f"{t:.2f}"] <= hi for t, lo, hi in PIT_WINDOWS)
    if key.startswith("t30"):
        g["logged_share_lb"] = t30_share_lb
        g["logged_share_ok"] = t30_share_lb is not None and t30_share_lb >= LOGGED_SHARE_LB
        if key in DIRECTION:
            point = float(pairs["delta_int"].mean())  # type: ignore[arg-type]
            g["direction_suspect"] = point < DIRECTION[key]
    if key.startswith("int"):
        g["int_fidelity"] = int_fidelity
        g["int_fidelity_ok"] = bool(int_fidelity and int_fidelity.get("ok", False))
    return g


def _num(v: Any) -> float | None:
    return None if v is None or not np.isfinite(float(v)) else float(v)


def int_fidelity_check(
    con: duckdb.DuckDBPyConnection, pairs: pl.DataFrame, arm: str, comparator: str, stat: str
) -> dict[str, Any]:
    """Section 3 deterministic check: max |P(Y>=k) arm - comparator| <= 0.0025 on 100% of pairs,
    mean integer-support delta within +/-1e-6, identical mean bias."""
    from nba.props.full_support import FIELD, survival

    runs = sorted(set(pairs["run_arm"].to_list()) | set(pairs["run_cmp"].to_list()))
    if not runs or pairs.is_empty():
        return {"ok": False, "n": 0}
    marks = ",".join("?" for _ in runs)
    rows = con.execute(
        f"SELECT run_id, model_name, game_id, player_id, prediction FROM forward_predictions "
        f"WHERE target = ? AND model_name IN (?, ?) AND run_id IN ({marks})",
        [stat, arm, comparator, *runs],
    ).fetchall()
    surv: dict[tuple[str, str, str, int], np.ndarray | None] = {}
    for run_id, model, gid, pid, pj in rows:
        surv[(str(run_id), str(model), str(gid), int(pid))] = survival(json.loads(pj).get(FIELD))
    worst, n_ok, n = 0.0, 0, 0
    for r in pairs.iter_rows(named=True):
        sa = surv.get((r["run_arm"], arm, r["game_id"], r["player_id"]))
        sc = surv.get((r["run_cmp"], comparator, r["game_id"], r["player_id"]))
        n += 1
        if sa is None or sc is None:
            continue
        k = max(sa.size, sc.size)
        d = float(np.max(np.abs(np.pad(sa, (0, k - sa.size)) - np.pad(sc, (0, k - sc.size)))))
        worst = max(worst, d)
        n_ok += int(d <= INT_MAX_DP)
    mean_d = float(pairs["delta_int"].mean())  # type: ignore[arg-type]
    bias_gap = abs(float(pairs["bias_arm"].mean()) - float(pairs["bias_cmp"].mean()))  # type: ignore[arg-type]
    ok = n_ok == n and abs(mean_d) <= INT_MEAN_TOL and bias_gap <= 1e-9
    return {
        "ok": ok, "n": n, "n_within_tol": n_ok, "max_abs_dp": worst,
        "mean_delta_int": mean_d, "bias_gap": bias_gap,
    }  # fmt: skip


# ----------------------------------------------------------------------------- decisions


def decide(
    key: str, look: str, res: dict[str, Any], p_bh: float, guards: dict[str, Any]
) -> tuple[str, list[str]]:
    """Section 5.5 / 7 outcome for one arm-stat. Returns (outcome, notes)."""
    notes: list[str] = []
    if look == "L14":
        return "MONITOR", ["L14 is monitoring only (no efficacy claim, no futility decision)"]
    point, lo, hi = res["point"], res["lo"], res["hi"]
    if key == "win":
        if np.isfinite(point) and point > 0 and np.isfinite(lo) and lo > 0:
            return "REVIEW", [
                "injury-Elo significantly worse than MOV-Elo: audit injury feed/tip gating"
            ]
        return "KEEP", ["tripwire only; no efficacy claim is ever made for the win model"]
    if not res["eligible"]:
        return "KEEP_SHADOWING", [res["ineligible_reason"]]
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return "KEEP_SHADOWING", ["fewer than 2 dates: no interval"]
    checks = ("bias_abs_ok", "bias_rel_ok", "coverage_ok", "slices_ok", "leak_ok", "pit_ok")
    fails = [k for k in checks if k in guards and not guards[k]]
    if guards.get("direction_suspect"):
        return "AUDIT", ["estimate more negative than the backtest upper end: suspected leak/fault"]
    if look in DROP_LOOKS and lo >= FLOOR:
        return "DROP", [f"98.18% CI lower bound {lo:.5f} >= floor {FLOOR}: floor unreachable"]
    if point > HARM and lo > 0:
        return "DROP", ["harm: point > +0.005 with CI lower bound > 0"]
    if fails:
        return "KEEP_SHADOWING", [
            f"guard failure(s): {fails}; manual review (a leak is never auto-passed)"
        ]
    gate_fails = []
    if key.startswith("t30") and not guards.get("logged_share_ok"):
        gate_fails.append("T-30 logged-share Wilson lower bound < 0.70")
    if key in SAME_RUN and key.startswith("int") and not guards.get("int_fidelity_ok"):
        gate_fails.append("deterministic integer-convention check failed")
    if gate_fails:
        return "KEEP_SHADOWING", gate_fails
    if point <= FLOOR and hi < 0 and p_bh <= ALPHA_LOOK:
        if 0.01 <= p_bh <= ALPHA_LOOK:
            notes.append("marginal (BH-adjusted p in [0.01, 0.0182]): red team before any use")
        if key.startswith("int"):
            notes.append("convention-only: adopt integer quantile outputs; never a skill claim")
        return "PROMOTE_CANDIDATE", notes
    if point <= FLOOR:
        return "KEEP_SHADOWING", ["floor met but CI/BH test not significant"]
    return "KEEP_SHADOWING", ["below the practical floor or CI straddles 0 / the floor"]


# ----------------------------------------------------------------------------- T-30 share


def t30_share(con: duckdb.DuckDBPyConnection, season: int, B: int, seed: int) -> dict[str, Any]:
    """Logged vs skipped T-30 decisions (regular season): Wilson and date-clustered CIs."""
    try:
        rows = con.execute(
            """
            SELECT d.game_id, coalesce(g.game_date, CAST(d.tipoff AS DATE)), d.outcome,
                   split_part(split_part(coalesce(d.reason, ''), ':', 1), ';', 1)
            FROM forward_t30_decisions d LEFT JOIN games g USING (game_id)
            WHERE d.game_id LIKE '002%' AND (g.season = ? OR g.season IS NULL)
            """,
            [season],
        ).fetchall()
    except duckdb.CatalogException:
        rows = []
    logged = sum(1 for r in rows if r[2] == "logged")
    n = len(rows)
    out: dict[str, Any] = {"n_decisions": n, "logged": logged, "skipped": n - logged}
    reasons: dict[str, int] = {}
    for r in rows:
        if r[2] != "logged":
            reasons[r[3] or "unspecified"] = reasons.get(r[3] or "unspecified", 0) + 1
    out["skipped_reasons"] = dict(sorted(reasons.items()))
    if not n:
        return out | {"logged_share": None, "logged_wilson": None, "logged_date_ci": None}
    lo, hi = wilson(logged, n)
    out["logged_share"] = logged / n
    out["logged_wilson"] = [lo, hi]
    out["skipped_wilson"] = [1 - hi, 1 - lo]
    v = np.array([1.0 if r[2] == "logged" else 0.0 for r in rows])
    boot = date_cluster_boot(v, np.array([str(r[1]) for r in rows]), B, seed, 0.95)
    out["logged_date_ci"] = [boot.lo, boot.hi]
    out["n_dates"] = boot.n_dates
    try:  # selection exposure (descriptive): production rps on skipped vs logged games
        prod = con.execute(
            """
            SELECT d.outcome, count(*), avg(s.rps) FROM forward_scores_elig s
            JOIN forward_t30_decisions d USING (game_id)
            WHERE s.model_name = ? AND s.status = 'scored' AND s.season = ?
            GROUP BY 1
            """,
            [PROD, season],
        ).fetchall()
        out["prod_rps_by_decision"] = {
            str(o): {"n": int(c), "mean_rps": _num(m)} for o, c, m in prod
        }
    except duckdb.CatalogException:
        pass
    return out


def timing_measurements(
    con: duckdb.DuckDBPyConnection, lineups_db: Path | None, B: int, seed: int
) -> dict[str, Any]:
    """Section 2.1 (descriptive): lead deciles, poll interval, open-decision shares with Wilson and
    date-clustered CIs, game-level share, starter skew. Read-only on the lineup database."""
    from nba.lineups.analysis import announcement_lead, open_decisions, starter_skew
    from nba.lineups.store import DEFAULT_LINEUPS_DB, connect_lineups

    path = lineups_db or DEFAULT_LINEUPS_DB
    if not Path(path).exists():
        return {"available": False}
    lcon = connect_lineups(path, read_only=True, retries=2)
    try:
        lead = announcement_lead(lcon)
        od = open_decisions(lcon, con)
        sk = starter_skew(con, lcon)
        fetches = [
            r[0] for r in lcon.execute(
                "SELECT DISTINCT fetched_at FROM lineup_snapshots ORDER BY 1"
            ).fetchall()
        ]  # fmt: skip
    finally:
        lcon.close()
    gaps = (
        np.diff(np.array(fetches, dtype="datetime64[s]")).astype(float) / 60.0
        if len(fetches) > 1
        else np.array([])
    )
    deciles = list(np.arange(0.1, 1.0, 0.1))

    def dq(v: list[float]) -> list[float] | None:
        return [float(x) for x in np.quantile(np.asarray(v), deciles)] if v else None

    lo_w, hi_w = wilson(od.n_open, od.n_candidates)
    g_lo, g_hi = wilson(od.n_games_with_open, od.n_games_with_candidates)
    vals: list[float] = []
    dates: list[str] = []
    for d, n_c, n_o in od.per_game:
        vals += [1.0] * n_o + [0.0] * (n_c - n_o)
        dates += [d] * n_c
    boot = date_cluster_boot(np.array(vals), np.array(dates), B, seed, 0.95)
    return {
        "available": True,
        "poll_interval_min_median": float(np.median(gaps)) if gaps.size else None,
        "n_team_games": lead.n_team_games,
        "confirmed_before_tip": lead.n_confirmed_before_tip,
        "confirmed_before_t30": lead.n_confirmed_before_t30,
        "confirmed_before_t30_wilson": list(wilson(lead.n_confirmed_before_t30, lead.n_team_games)),
        "lead_minutes_deciles": dq(lead.lead_minutes),
        "source_lead_minutes_deciles": dq(lead.source_lead_minutes),
        "lead_n": len(lead.lead_minutes),
        "open": {
            "k": od.n_open, "n": od.n_candidates, "wilson": [lo_w, hi_w],
            "date_clustered_ci": [boot.lo, boot.hi], "n_dates": boot.n_dates,
            "game_level": {"k": od.n_games_with_open, "n": od.n_games_with_candidates,
                           "wilson": [g_lo, g_hi]},
            "n_games_no_report": od.n_games_no_report, "states": dict(sorted(od.by_state.items())),
            "flag_materially_below_backtest": bool(
                od.n_candidates and np.isfinite(lo_w) and lo_w >= OPEN_SHARE_FLAG_LB
            ),
        },
        "starter_skew": {
            "n_games": sk.n_games, "n_announced": sk.n_announced,
            "match": sk.n_box_starters_match, "announced_not_played": sk.n_announced_not_played,
            "box_starters_not_announced": sk.n_box_starters_not_announced,
        },
    }  # fmt: skip


# ----------------------------------------------------------------------------- evaluation


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except Exception:
        return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_keys(out_dir: Path, look: str) -> set[str]:
    """Arm-stat keys already recorded (immutably) for ``look``."""
    done: set[str] = set()
    for f in sorted(out_dir.glob(f"{look}_*.json")):
        done |= set(json.loads(f.read_text()).get("recorded_keys", []))
    return done


def _epochs(pairs: pl.DataFrame, metric: str) -> dict[str, Any]:
    """Threat 5: comparator-version epochs; the pooled claim needs the same sign in every epoch."""
    out: dict[str, Any] = {}
    if pairs.is_empty() or "version_cmp" not in pairs.columns:
        return {"epochs": out, "same_sign": True}
    for ver, sub in pairs.group_by("version_cmp", maintain_order=True):
        out[str(ver[0])] = {"n": sub.height, "mean_delta": float(sub[metric].mean())}  # type: ignore[arg-type]
    signs = {np.sign(v["mean_delta"]) for v in out.values() if v["n"] > 0}
    return {"epochs": out, "same_sign": len(signs) <= 1}


def _arm_stat(
    con: duckdb.DuckDBPyConnection,
    key: str,
    look: str,
    season: int,
    B: int,
    seed: int,
    meta: dict[tuple[str, int], tuple[Any, ...]],
    share_lb: float | None,
) -> tuple[dict[str, Any], pl.DataFrame, dict[str, Any]]:
    arm, comp, stat, metric = SPEC[key]
    if key == "win":
        pairs, wc = paired_win(con, season)
        counts: dict[str, Any] = wc
    else:
        pairs, pc = paired_deltas(
            con, arm, comp, stat, season=season, same_run=key in SAME_RUN, meta=meta
        )
        counts = pc._asdict()
    boot = date_cluster_boot(
        pairs[metric].to_numpy() if not pairs.is_empty() else np.array([]),
        pairs["game_date"].to_numpy() if not pairs.is_empty() else np.array([]),
        B, seed, LEVEL,
    )  # fmt: skip
    min_dates, min_n = MIN_GATE[key]
    res: dict[str, Any] = {
        "key": key, "arm": arm, "comparator": comp, "target": stat, "metric": metric,
        "counts": counts, "n": boot.n, "n_dates": boot.n_dates,
        "point": boot.point, "lo": boot.lo, "hi": boot.hi, "p": boot.p,
        "ci_level": LEVEL, "floor": None if key == "win" else FLOOR,
        "min_dates": min_dates, "min_n": min_n,
    }  # fmt: skip
    efficacy = look in EFFICACY_LOOKS
    ok_n = boot.n_dates >= min_dates and boot.n >= min_n and boot.n > 0
    res["eligible"] = bool(efficacy and ok_n)
    if not efficacy:
        res["ineligible_reason"] = "monitoring look"
    elif not ok_n:
        res["ineligible_reason"] = (
            f"below minimum before any claim: {boot.n_dates}/{min_dates} dates, "
            f"{boot.n}/{min_n} pairs"
        )
    if key not in ("win",) and not pairs.is_empty():
        other = "delta_int" if metric != "delta_int" else "delta_pinball19"
        ob = date_cluster_boot(
            pairs[other].to_numpy(), pairs["game_date"].to_numpy(), B, seed, LEVEL
        )
        res["secondary"] = {"metric": other, "point": ob.point, "lo": ob.lo, "hi": ob.hi, "p": ob.p}
    if key == "win" and not pairs.is_empty():
        bb = date_cluster_boot(
            pairs["delta_brier"].to_numpy(), pairs["game_date"].to_numpy(), B, seed, LEVEL
        )
        res["secondary"] = {
            "metric": "delta_brier",
            "point": bb.point,
            "lo": bb.lo,
            "hi": bb.hi,
            "p": bb.p,
        }
    res["epochs"] = (
        _epochs(pairs, metric) if not pairs.is_empty() else {"epochs": {}, "same_sign": True}
    )
    fid = None
    if key in SAME_RUN and key.startswith("int") and not pairs.is_empty():
        fid = int_fidelity_check(con, pairs, arm, comp, stat)
    guards = _guards(key, pairs, metric, fid, share_lb) if not pairs.is_empty() else {}
    if guards and not res["epochs"]["same_sign"]:
        guards["epoch_sign_ok"] = False
    return res, pairs, guards


def evaluate_checkpoint(
    con: duckdb.DuckDBPyConnection,
    look: str,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    lineups_db: Path | None = None,
    season: int = FORWARD_SEASON,
    descriptive: bool = False,
    B: int = B_DEFAULT,
    seed: int = SEED,
    asof: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate ``look`` and write the immutable snapshot (or a descriptive one).

    Raises ``GateNotMet`` unless at least one not-yet-recorded arm-stat has reached the look's
    date count (``--descriptive`` bypasses this and writes under ``<out_dir>/descriptive``).
    Raises ``FileExistsError`` rather than overwrite anything."""
    if look not in LOOK_DATES:
        raise ValueError(f"unknown look {look!r}; expected one of {sorted(LOOK_DATES)}")
    out_dir = Path(out_dir)
    done = set() if descriptive else snapshot_keys(out_dir, look)
    meta = _slice_meta(con, season)
    share = t30_share(con, season, B, seed)
    wl = share.get("logged_wilson")
    share_lb = float(wl[0]) if wl else None
    results: dict[str, dict[str, Any]] = {}
    pair_tables: dict[str, pl.DataFrame] = {}
    guards: dict[str, dict[str, Any]] = {}
    for key in FAMILY:
        results[key], pair_tables[key], guards[key] = _arm_stat(
            con, key, look, season, B, seed, meta, share_lb
        )
    need = LOOK_DATES[look]
    due = [
        k for k in FAMILY
        if k not in done and results[k]["n"] > 0 and (need is None or results[k]["n_dates"] >= need)
    ]  # fmt: skip
    if not due and not descriptive:
        raise GateNotMet(
            f"{look}: no arm-stat with >= {need} settled dates that is not already recorded "
            f"(dates: { {k: results[k]['n_dates'] for k in FAMILY} }); use --descriptive to inspect"
        )
    if descriptive:
        due = [k for k in FAMILY if results[k]["n"] > 0]
    p_raw = {k: (results[k]["p"] if k in due and results[k]["eligible"] else 1.0) for k in FAMILY}
    if look not in EFFICACY_LOOKS:
        p_raw = dict.fromkeys(FAMILY, 1.0)
    p_bh = bh_adjust(p_raw)
    for k in FAMILY:
        results[k]["p_bh"] = p_bh[k]
        results[k]["guards"] = guards[k]
        results[k]["in_snapshot"] = k in due
        if k in due:
            outcome, notes = decide(k, look, results[k], p_bh[k], guards[k])
        else:
            outcome, notes = "NOT_IN_THIS_SNAPSHOT", ["look count not reached or already recorded"]
        results[k]["outcome"], results[k]["notes"] = outcome, notes
    asof = asof or _max_date(pair_tables) or date.today()
    ts = now or datetime.now(UTC).replace(tzinfo=None)
    snap: dict[str, Any] = {
        "look": look, "asof_date": asof.isoformat(), "created_at": ts.isoformat(),
        "git_sha": _git_sha(), "seed": seed, "B": B, "alpha_look": ALPHA_LOOK, "ci_level": LEVEL,
        "season": season, "descriptive": descriptive, "family": list(FAMILY),
        "recorded_keys": [] if descriptive else due,
        "bh_adjusted": p_bh, "p_raw_used": p_raw, "t30_share": share,
        "timing": timing_measurements(con, lineups_db, B, seed),
        "results": results, "slice_definitions": SLICE_DEFINITIONS,
    }  # fmt: skip
    base = out_dir / "descriptive" if descriptive else out_dir
    base.mkdir(parents=True, exist_ok=True)
    stem = f"{look}_{asof.isoformat()}"
    json_path = base / f"{stem}.json"
    md_path = base / f"{stem}.md"
    if descriptive:  # scratch area: later descriptive runs for the same date may replace it
        for stale in base.glob(f"{stem}*"):
            stale.unlink()
    elif json_path.exists() or md_path.exists():
        raise FileExistsError(f"refusing to overwrite existing checkpoint {json_path}")
    hashes: dict[str, str] = {}
    for k in due:
        pq = base / f"{stem}_pairs_{k}.parquet"
        if pq.exists() and not descriptive:
            raise FileExistsError(f"refusing to overwrite existing pair table {pq}")
        pair_tables[k].write_parquet(pq)
        hashes[k] = _sha256(pq)
    snap["pair_tables_sha256"] = hashes
    text = json.dumps(_clean(snap), indent=2, sort_keys=True, default=str, allow_nan=False)
    with md_path.open("x") as f:
        f.write(render_markdown(snap))
    with json_path.open("x") as f:  # 'x': fails if it exists -- the commit marker is written last
        f.write(text)
    if not descriptive:
        with (out_dir / "checkpoint_log.jsonl").open("a") as f:
            entry = {
                "look": look,
                "asof": asof.isoformat(),
                "file": json_path.name,
                "sha256": _sha256(json_path),
                "keys": due,
                "git_sha": snap["git_sha"],
            }
            f.write(json.dumps(entry) + "\n")
    snap["paths"] = {"json": str(json_path), "md": str(md_path)}
    return snap


def _clean(o: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.floating, float)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    return o


SLICE_DEFINITIONS = {
    "starter": "stored starter_rate >= 0.5 (box starter flag when absent); proxy for starter10",
    "any_out": "game has any player_availability row with status 'out' (game-level proxy)",
    "early_season": "player's team has played <= 15 regular-season games (this one included)",
    "is_home": "player's team is the home team",
}


def _max_date(pair_tables: dict[str, pl.DataFrame]) -> date | None:
    ds = [str(d) for t in pair_tables.values() if not t.is_empty() for d in [t["game_date"].max()]]
    return date.fromisoformat(max(ds)) if ds else None


def due_looks(
    con: duckdb.DuckDBPyConnection, out_dir: Path = DEFAULT_OUT_DIR, season: int = FORWARD_SEASON
) -> list[str]:
    """Looks (not END) that some not-yet-recorded arm-stat has reached (rule 11 date counts)."""
    meta: dict[tuple[str, int], tuple[Any, ...]] = {}
    nd: dict[str, int] = {}
    for key in FAMILY:
        arm, comp, stat, _ = SPEC[key]
        if key == "win":
            pairs, _c = paired_win(con, season)
        else:
            pairs, _pc = paired_deltas(
                con, arm, comp, stat, season=season, same_run=key in SAME_RUN, meta=meta
            )
        nd[key] = int(pairs["game_date"].n_unique()) if not pairs.is_empty() else 0
    out = []
    for look, need in LOOK_DATES.items():
        if need is None:
            continue
        done = snapshot_keys(Path(out_dir), look)
        if any(nd[k] >= need and k not in done for k in FAMILY):
            out.append(look)
    return out


# ----------------------------------------------------------------------------- markdown


def _ci(r: dict[str, Any], d: int = 4) -> str:
    return f"{r['point']:.{d}f} [{r['lo']:.{d}f}, {r['hi']:.{d}f}]"


def render_markdown(snap: dict[str, Any]) -> str:
    """Markdown block for the checkpoint (also what the report prints)."""
    tag = "DESCRIPTIVE (not a decision input)" if snap["descriptive"] else "IMMUTABLE SNAPSHOT"
    lines = [
        f"## Pre-registered checkpoint {snap['look']} as of {snap['asof_date']} - {tag}",
        "",
        f"git {snap['git_sha'][:10]}; seed {snap['seed']}; B={snap['B']}; "
        f"CI level {snap['ci_level']:.4f} (Pocock alpha_look {snap['alpha_look']}); "
        "dates are resampled as clusters; delta = arm minus comparator (negative = arm better).",
        "",
        "| test | metric | n pairs | dates | delta [98.18% CI] | p | BH p | outcome |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k in snap["family"]:
        r = snap["results"][k]
        ci = _ci(r, 5) if r["n"] else "n/a"
        lines.append(
            f"| {k} | {r['metric']} | {r['n']} | {r['n_dates']} | {ci} | {r['p']:.4f} | "
            f"{r['p_bh']:.4f} | {r['outcome']} |"
        )
    for k in snap["family"]:
        r = snap["results"][k]
        if r["notes"] or r["guards"]:
            lines.append(f"- {k}: {'; '.join(r['notes'])}; counts {r['counts']}")
            gd = {a: b for a, b in r["guards"].items() if a.endswith("_ok")}
            if gd:
                lines.append(f"  - guards: {gd}")
    sh = snap["t30_share"]
    if sh.get("n_decisions"):
        lines.append(
            f"- T-30 logged share {sh['logged']}/{sh['n_decisions']} = {sh['logged_share']:.1%}, "
            f"Wilson 95% [{sh['logged_wilson'][0]:.1%}, {sh['logged_wilson'][1]:.1%}], "
            f"date-clustered 95% [{sh['logged_date_ci'][0]:.1%}, {sh['logged_date_ci'][1]:.1%}]; "
            f"skipped reasons {sh['skipped_reasons']}"
        )
    tm = snap["timing"]
    if tm.get("available"):
        o = tm["open"]
        lines.append(
            f"- open game-time decisions {o['k']}/{o['n']}, Wilson [{o['wilson'][0]:.1%}, "
            f"{o['wilson'][1]:.1%}], date-clustered [{o['date_clustered_ci'][0]:.1%}, "
            f"{o['date_clustered_ci'][1]:.1%}]; game-level "
            f"{o['game_level']['k']}/{o['game_level']['n']}; "
            f"poll interval median {tm['poll_interval_min_median']} min"
        )
    return "\n".join(lines) + "\n"


def latest_snapshots_section(out_dir: Path = DEFAULT_OUT_DIR) -> list[str]:
    """Report lines printing the immutable snapshots (``report.py`` integration, section 8.7)."""
    lines = ["", "## Pre-registered checkpoints (docs/FORWARD_PREREG_2026_27.md)"]
    files = sorted(Path(out_dir).glob("L*_*.json")) + sorted(Path(out_dir).glob("END_*.json"))
    if not files:
        lines.append("- no checkpoint snapshot written yet. Running CIs elsewhere in this report "
                     "are descriptive, not decision-bearing.")  # fmt: skip
        return lines
    for f in files:
        md = f.with_suffix(".md")
        lines.append(md.read_text() if md.exists() else f"- {f.name}")
    lines.append("Running CIs elsewhere in this report are descriptive, not decision-bearing.")
    return lines
