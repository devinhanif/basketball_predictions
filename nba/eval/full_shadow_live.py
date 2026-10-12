# ruff: noqa: E501
"""Scorer for docs/prereg/FULL_SHADOW_LIVE_2026_27.md (DRAFT until the main session freezes it).

Reads the live 2026-27 shadow tables (``forward_full_shadow``, ``forward_full_scores``, ``forward_pplay_shadow``,
``forward_predictions``) with a READ-ONLY connection and applies the frozen rule: primary = conditional integer CRPS,
FULL minus production, pts; secondary = reb / ast / fg3m conditional and two pts joint contrasts (BH over those five).
Pure arithmetic over stored grids; nothing is fitted here and nothing is written.

    .venv/bin/python -W ignore -m nba.eval.full_shadow_live [--db nba.duckdb] [--out reports/full_shadow_live/<date>.json]

Integer support for every arm (``to_integer_support``); percentile bootstrap over whole clusters (game, and date), 2000 draws,
seed 0; one-sided because the hypothesis is directional (FULL lower is better).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.daily.full_shadow.settle import mixture_crps
from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME
from nba.eval.context_residual_eval import bh_adjust
from nba.props.context_residual import to_integer_support
from nba.props.forward import QUANTILE_TAUS

STATS = ("pts", "reb", "ast", "fg3m")
LEAD_MINUTES = 60
FLOOR = -0.005  # conditional pts integer CRPS, FULL minus production (house floor)
PLACEBO_MARGIN = (
    -0.0025
)  # FULL must beat the stale-adjustment placebo by this much (half the floor)
SLICE_TOL = 0.005  # no slice (n >= MIN_SLICE_N) worse than production by more than this
MIN_SLICE_N = 300
BENCH_MINUTES = (
    20.0  # bench = production proj_minutes < 20 (from forward_pplay_shadow, same cutoff)
)
LOW_P = 0.9  # low availability = production p_play < 0.9
N_BOOT = 2000
SEED = 0
Z1 = 1.6448536  # one-sided 95%
#: sd of SHUF_FULL - SHUF_CTRL (pts integer CRPS) across permutation seeds, pooled 2023 + 2024, K = 48
#: (reports/shuffle_guard_diagnostic_2026-10-12.json, pooled_both.sd); the fit-noise term the game bootstrap ignores.
NULL_SD_PTS = 0.00199  # K=100 permutation null, pooled 2023+2024 (FULL_SHADOW_LIVE_2026_27, frozen)
N_MIN = 14_600  # eligible conditional pts rows at the final look
N_INTERIM = 7_300  # kill-only look
LATE_PAIR_SHARE = 0.02  # more than 2% of shadow pairs without a pre-cutoff row => no verdict
REPRO_TOL = 1e-9
TAUS = np.asarray(QUANTILE_TAUS, dtype=float)
PROD_MODEL = CONTEXT_PROPS_MODEL_NAME
LT_MODEL = CONTEXT_LT_PROPS_MODEL_NAME  # pts only: production + the lower-tail short-minutes mixture = the offline A0
REGULAR_PREFIXES = (
    "002",
    "005",
)  # regular season and play-in; preseason (001), playoffs (004), Cup final (006) excluded

# secondary family, declared in the rule (BH over exactly these five)
SECONDARY = (
    "cond_reb",
    "cond_ast",
    "cond_fg3m",
    "joint_pts_full_vs_rprod",
    "joint_pts_full_vs_prod",
)


# --------------------------------------------------------------------------- arithmetic
def crps_rows(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row integer CRPS of (n, 19) integer quantile grids: 2 x mean pinball (``nba.daily.lt_live.integer_crps``)."""
    d = y[:, None] - q
    return np.asarray(2.0 * np.mean(np.maximum(TAUS * d, (TAUS - 1.0) * d), axis=1))


def to_int_grid(grid: list[float]) -> np.ndarray:
    return np.asarray(to_integer_support(np.asarray(grid, dtype=float)), dtype=np.int16)


def cluster_boot(
    delta: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> np.ndarray:
    """Bootstrap means with whole clusters resampled (ratio of sums); shape (n_boot,)."""
    _, inv = np.unique(cluster, return_inverse=True)
    g = int(inv.max()) + 1
    s = np.bincount(inv, weights=delta, minlength=g)
    c = np.bincount(inv, minlength=g).astype(float)
    w = np.random.default_rng(seed).multinomial(g, np.full(g, 1.0 / g), size=n_boot).astype(float)
    return np.asarray((w @ s) / (w @ c))


def one_sided(
    delta: np.ndarray,
    cluster: np.ndarray,
    sigma_fit: float = 0.0,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> dict[str, Any]:
    """Mean, bootstrap se, one-sided 95% bounds and p-values (H1: delta < 0) for one clustering.

    ``upper95``/``lower95``: percentiles 95 / 5 of the cluster bootstrap. ``upper95_fit``: delta + Z1 * sqrt(se^2 + sigma_fit^2),
    the bound after adding the fit-noise variance measured from the permutation null (equals the plain normal bound when sigma_fit = 0).
    ``p_less`` = P(boot mean >= 0) with +1 smoothing (small = FULL better); ``p_greater`` the mirror."""
    n = int(len(delta))
    g = int(len(np.unique(cluster))) if n else 0
    if n == 0 or g < 2:
        return {"n": n, "clusters": g, "delta": None}
    b = cluster_boot(delta, cluster, n_boot, seed)
    mu = float(delta.mean())
    se = float(b.std(ddof=1))
    return {
        "n": n,
        "clusters": g,
        "delta": mu,
        "se": se,
        "upper95": float(np.percentile(b, 95)),
        "lower95": float(np.percentile(b, 5)),
        "upper95_fit": mu + Z1 * math.sqrt(se**2 + sigma_fit**2),
        "half_width_one_sided": float(np.percentile(b, 95)) - mu,
        "p_less": float((1 + np.sum(b >= 0)) / (n_boot + 1)),
        "p_greater": float((1 + np.sum(b <= 0)) / (n_boot + 1)),
    }


def stale_adjust_placebo(
    pid: np.ndarray, order_key: np.ndarray, qf: np.ndarray, qp: np.ndarray
) -> np.ndarray:
    """Past-only placebo grid: today's production grid plus the player's PREVIOUS (FULL - production) quantile-wise adjustment,
    integer, floored at 0, running max. A first row (no earlier row for the player) keeps the production grid.
    If FULL's gain were a persistent per-player offset rather than today's information, this stale copy would match it."""
    order = np.lexsort((order_key, pid))
    adj = (qf.astype(np.int32) - qp.astype(np.int32))[order]
    p_sorted = pid[order]
    prev = np.zeros_like(adj)
    same = np.r_[False, p_sorted[1:] == p_sorted[:-1]]
    prev[1:] = adj[:-1]
    prev[~same] = 0
    out = np.empty_like(qp)
    sorted_pl = np.maximum(qp[order].astype(np.int32) + prev, 0)
    out[order] = np.maximum.accumulate(sorted_pl, axis=1).astype(qp.dtype)
    return out


def shuffle_grids_within_day(q: np.ndarray, day: np.ndarray, seed: int = SEED) -> np.ndarray:
    """Non-inert shuffle: FULL grids permuted across the rows of one calendar day (same stat); the match to the player goes."""
    rng = np.random.default_rng(seed)
    out = q.copy()
    for d in np.unique(day):
        ix = np.flatnonzero(day == d)
        out[ix] = q[rng.permutation(ix)]
    return out


def n_required(sd_row: float, deff: float, half: float = 0.005, z: float = Z1) -> int:
    """Rows needed for a one-sided 95% clustered half-width <= ``half`` (default: the house floor)."""
    return int(math.ceil((z * sd_row * math.sqrt(deff) / half) ** 2))


def week_reached(
    n_rows: int, rows_per_game: float = 21.0, games_per_week: float = 49.0, burn_weeks: float = 2.0
) -> float:
    """Season week (1 = opening week, Tue 2026-10-20) at which ``n_rows`` eligible conditional rows have accumulated."""
    return burn_weeks + n_rows / (rows_per_game * games_per_week)


# --------------------------------------------------------------------------- reading the tables
_LIVE_SQL = """
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_full_shadow WHERE stat = ? AND made_at <= tipoff - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, e.tipoff, e.made_at, e.player_id, e.run_id,
       CAST(e.q_grid AS VARCHAR), CAST(e.q_grid_prod AS VARCHAR), e.p_play_prod, e.in_prod_log,
       s.played, s.y, s.y_uncond, s.p_play_r, s.crps_prod, s.crps_full,
       s.crps_joint_prod, s.crps_joint_full, s.crps_joint_rprod,
       {proj} AS proj_minutes,
       EXISTS (SELECT 1 FROM games g2 WHERE g2.game_date = g.game_date - 1
               AND (g2.home_team = e.team_id OR g2.away_team = e.team_id)) AS second_night
FROM elig e
JOIN forward_full_scores s ON s.game_id = e.game_id AND s.player_id = e.player_id AND s.stat = e.stat
                          AND s.run_id = e.run_id AND s.status = 'scored'
JOIN games g ON g.game_id = e.game_id
{pp_join}
WHERE e.rn = 1 AND g.season = ?
"""
_PP_JOIN = """LEFT JOIN (SELECT game_id, player_id, proj_minutes,
                       row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
                FROM forward_pplay_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE) pp
       ON pp.game_id = e.game_id AND pp.player_id = e.player_id AND pp.rn = 1"""


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, args: list[Any] | None = None) -> int:
    row = con.execute(sql, args or []).fetchone()
    return int(row[0]) if row else 0


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return (
        _scalar(con, "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name])
        > 0
    )


def load_live(
    con: duckdb.DuckDBPyConnection, stat: str, season: int = 2026, lead: int = LEAD_MINUTES
) -> dict[str, np.ndarray]:
    """Eligible settled rows of one stat: the latest shadow row with ``made_at <= tipoff - lead`` that settle scored.
    Regular season and play-in only (``REGULAR_PREFIXES``); a row with a missing or short grid is dropped and counted."""
    has_pp = _has_table(con, "forward_pplay_shadow")
    sql = _LIVE_SQL.format(
        proj="pp.proj_minutes" if has_pp else "CAST(NULL AS DOUBLE)",
        pp_join=_PP_JOIN if has_pp else "",
    )
    args: list[Any] = [stat, lead] + ([lead] if has_pp else []) + [season]
    cols = ["gid", "day", "tip", "made_at", "pid", "run_id", "qf", "qp", "p_prod", "logged", "played", "y", "y_u",
            "p_r", "c_prod", "c_full", "j_prod", "j_full", "j_rprod", "proj", "b2b"]  # fmt: skip
    keep: dict[str, list[Any]] = {c: [] for c in cols}
    qf: list[np.ndarray] = []
    qp: list[np.ndarray] = []
    dropped = excluded = 0
    for r in con.execute(sql, args).fetchall():
        d = dict(zip(cols, r, strict=True))
        if not str(d["gid"]).startswith(REGULAR_PREFIXES):
            excluded += 1
            continue
        gf, gp = json.loads(d["qf"]), json.loads(d["qp"])
        if len(gf) != 19 or len(gp) != 19:
            dropped += 1
            continue
        for c in cols:
            if c not in ("qf", "qp"):
                keep[c].append(d[c])
        qf.append(to_int_grid(gf))
        qp.append(to_int_grid(gp))

    def f(k: str) -> np.ndarray:
        return np.array([np.nan if v is None else v for v in keep[k]], dtype=float)

    # pts only: the lower-tail arm (production + short-minutes mixture), latest row made at or before the cutoff
    n = len(qf)
    qlt = np.zeros((n, 19), dtype=np.int16)
    has_lt = np.zeros(n, dtype=bool)
    if stat == "pts" and n:
        lt = {
            (str(g), int(p)): gl
            for g, p, gl in con.execute(
                """SELECT game_id, player_id, grid FROM (
                       SELECT game_id, player_id, prediction ->> '$.q_grid' AS grid,
                              row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
                       FROM forward_predictions
                       WHERE model_name = ? AND target = 'pts' AND made_at <= tipoff - ? * INTERVAL 1 MINUTE) WHERE rn = 1""",
                [LT_MODEL, lead],
            ).fetchall()
        }
        for i, (g, p) in enumerate(zip(keep["gid"], keep["pid"], strict=True)):
            grid = lt.get((str(g), int(p)))
            if grid is not None and len(json.loads(grid)) == 19:
                qlt[i], has_lt[i] = to_int_grid(json.loads(grid)), True
    y_u, p_r = f("y_u"), f("p_r")
    j_rlt = np.full(n, np.nan)
    for k in np.flatnonzero(has_lt):
        if np.isfinite(p_r[k]):
            j_rlt[k] = mixture_crps(qlt[k].astype(float), float(p_r[k]), float(y_u[k]))

    return {
        "gid": np.array(keep["gid"], dtype=str),
        "day": np.array(keep["day"], dtype="datetime64[D]"),
        "tip": np.array(keep["tip"], dtype="datetime64[s]"),
        "pid": np.array(keep["pid"], dtype=np.int64),
        "played": np.array(keep["played"], dtype=bool),
        "y": f("y"), "y_u": f("y_u"), "p_prod": f("p_prod"), "p_r": f("p_r"), "proj": f("proj"),
        "c_prod": f("c_prod"), "c_full": f("c_full"),
        "j_prod": f("j_prod"), "j_full": f("j_full"), "j_rprod": f("j_rprod"),
        "b2b": np.array(keep["b2b"], dtype=bool),
        "qf": np.array(qf, dtype=np.int16).reshape(len(qf), 19),
        "qp": np.array(qp, dtype=np.int16).reshape(len(qp), 19),
        "qlt": qlt, "has_lt": has_lt, "j_rlt": j_rlt,
        "counts": np.array([dropped, excluded]),
    }  # fmt: skip


def integrity_gates(
    con: duckdb.DuckDBPyConnection, lead: int = LEAD_MINUTES, season: int = 2026
) -> dict[str, Any]:
    """Stamp and shadow-integrity counts. ``prod_mismatch`` compares, for every ``in_prod_log`` shadow row, the stored production
    p_play and 19-quantile grid with the latest ``props_context_residual`` row of ``forward_predictions`` made at or before the shadow
    row (production is write-on-change, so the same run need not hold a row), same game, player, target (0 compared => reported
    unverifiable, the steward's byte-identical production check stands in)."""

    def q(sql: str, a: list[Any] | None = None) -> int:
        return _scalar(con, sql, a)

    pairs = q(
        "SELECT count(*) FROM (SELECT 1 FROM forward_full_shadow s JOIN games g USING (game_id) WHERE g.season = ? GROUP BY s.game_id, s.player_id, s.stat)",
        [season],
    )
    late = q(
        """SELECT count(*) FROM (SELECT 1 FROM forward_full_shadow s JOIN games g USING (game_id) WHERE g.season = ?
           GROUP BY s.game_id, s.player_id, s.stat HAVING min(s.made_at) > min(s.tipoff) - ? * INTERVAL 1 MINUTE)""",
        [season, lead],
    )
    after_tip = q("SELECT count(*) FROM forward_full_shadow WHERE made_at >= tipoff")
    rows = con.execute(
        """SELECT p_play_prod, gs, pp, qg FROM (
               SELECT s.p_play_prod, CAST(s.q_grid_prod AS VARCHAR) AS gs,
                      p.prediction ->> '$.p_play' AS pp, p.prediction ->> '$.q_grid' AS qg,
                      row_number() OVER (PARTITION BY s.run_id, s.game_id, s.player_id, s.stat
                                         ORDER BY p.made_at DESC, p.run_id DESC) rn
               FROM forward_full_shadow s JOIN games g USING (game_id)
               JOIN forward_predictions p
                 ON p.game_id = s.game_id AND p.player_id = s.player_id AND p.target = s.stat
                    AND p.model_name = ? AND p.made_at <= s.made_at
               WHERE s.in_prod_log AND g.season = ?) WHERE rn = 1""",
        [PROD_MODEL, season],
    ).fetchall()
    mism = 0
    for p_shadow, g_shadow, p_pred, g_pred in rows:
        bad = p_pred is not None and abs(float(p_shadow) - float(p_pred)) > 1e-12
        if g_pred is not None and g_shadow is not None:
            a, b = (
                np.asarray(json.loads(g_shadow), dtype=float),
                np.asarray(json.loads(g_pred), dtype=float),
            )
            bad = bad or a.shape != b.shape or bool(np.max(np.abs(a - b)) > 1e-9)
        mism += int(bad)
    return {
        "shadow_pairs": int(pairs), "late_only_pairs": int(late), "late_share": (late / pairs) if pairs else None,
        "rows_after_tip": int(after_tip), "prod_compared": len(rows), "prod_mismatch": mism,
    }  # fmt: skip


# --------------------------------------------------------------------------- scoring
def _both(
    delta: np.ndarray, gid: np.ndarray, day: np.ndarray, sigma_fit: float = 0.0
) -> dict[str, Any]:
    return {
        "game": one_sided(delta, gid, sigma_fit),
        "date": one_sided(delta, day.astype("int64"), sigma_fit),
    }


def score_stat(rows: dict[str, np.ndarray], stat: str) -> dict[str, Any]:
    """Primary / secondary / guard arithmetic for one stat from ``load_live`` output.

    The comparator of ``cond`` and of every guard is the lower-tail arm for pts (production + the short-minutes mixture, the offline A0
    of T467-T473, so FULL is not credited with the mixture's own gain) and production's grid for reb / ast / fg3m (no live mixture arm
    exists there, so those contrasts are the whole package)."""
    sigma = NULL_SD_PTS if stat == "pts" else 0.0
    use_lt = stat == "pts"
    n_all = len(rows["gid"])
    have = rows["has_lt"] if use_lt else np.ones(n_all, dtype=bool)
    stored = rows["played"] & np.isfinite(rows["c_prod"]) & np.isfinite(rows["c_full"])
    played = stored & have
    out: dict[str, Any] = {
        "comparator": "lt_arm" if use_lt else "production",
        "dropped_short_grid": int(rows["counts"][0]),
        "excluded_not_regular": int(rows["counts"][1]),
        "played_rows_without_comparator": int(stored.sum() - played.sum()),
    }
    # the scorer reproduces settle's stored conditional scores from the stored grids (all played rows)
    repro = 0.0
    if stored.any():
        ys = rows["y"][stored]
        repro = max(
            float(np.max(np.abs(crps_rows(rows["qf"][stored], ys) - rows["c_full"][stored]))),
            float(np.max(np.abs(crps_rows(rows["qp"][stored], ys) - rows["c_prod"][stored]))),
        )
    gid, day, pid = rows["gid"][played], rows["day"][played], rows["pid"][played]
    y, qf = rows["y"][played], rows["qf"][played]
    qb = (rows["qlt"] if use_lt else rows["qp"])[played]
    c_full, c_base = crps_rows(qf, y), crps_rows(qb, y)
    d = c_full - c_base
    out["cond"] = {"n": int(played.sum()), "n_games": int(len(np.unique(gid))), "n_dates": int(len(np.unique(day))),
                   "repro_max_abs": repro, **_both(d, gid, day, sigma)}  # fmt: skip
    if (
        use_lt
    ):  # descriptive package: FULL against production's own grid (includes the mixture's gain)
        gp, dp = rows["gid"][stored], rows["day"][stored]
        out["cond_vs_prod"] = {
            "n": int(stored.sum()),
            **_both(rows["c_full"][stored] - rows["c_prod"][stored], gp, dp),
        }
    if played.sum() >= 2:
        pl = stale_adjust_placebo(pid, rows["tip"][played].astype("int64"), qf, qb)
        out["placebo_full_minus_stale"] = one_sided(c_full - crps_rows(pl, y), gid)
        sh = shuffle_grids_within_day(qf, day)
        out["shuffled_full_minus_base"] = one_sided(crps_rows(sh, y) - c_base, gid)
        sl = {
            "bench": rows["proj"][played] < BENCH_MINUTES,
            "low_p": rows["p_prod"][played] < LOW_P,
            "second_night": rows["b2b"][played],
        }
        out["slices"] = {
            k: {"n": int(m.sum()), **(one_sided(d[m], gid[m]) if m.sum() >= MIN_SLICE_N else {"note": "under 300 rows: reported, cannot fail"})}
            for k, m in sl.items()
        }  # fmt: skip
    # joint (unconditional) contrasts on rows that have a P(play) R; package level (the production grid has no mixture)
    jm = np.isfinite(rows["j_full"]) & np.isfinite(rows["j_rprod"]) & np.isfinite(rows["j_prod"])
    jg, jd = rows["gid"][jm], rows["day"][jm]
    a, b, c = rows["j_full"][jm], rows["j_rprod"][jm], rows["j_prod"][jm]
    out["joint"] = {
        "n": int(jm.sum()), "n_games": int(len(np.unique(jg))),
        "full_vs_rprod": _both(a - b, jg, jd, sigma), "full_vs_prod": _both(a - c, jg, jd, sigma),
        "rprod_vs_prod_owned_by_PPLAY_INTO_PROPS_LIVE": one_sided(b - c, jg),
        "identity_max_abs": float(np.max(np.abs((a - c) - ((a - b) + (b - c))))) if jm.any() else 0.0,
    }  # fmt: skip
    if use_lt:  # descriptive attribution: FULL on top of R AND the mixture
        lm = jm & np.isfinite(rows["j_rlt"])
        out["joint"]["full_vs_rlt"] = {
            "n": int(lm.sum()),
            **_both(rows["j_full"][lm] - rows["j_rlt"][lm], rows["gid"][lm], rows["day"][lm]),
        }
    return out


def secondary_family(res: dict[str, Any]) -> dict[str, Any]:
    """BH over the five declared secondary tests, using the game-clustered one-sided p of each contrast (H1: FULL better)."""
    items: dict[str, dict[str, Any]] = {
        "cond_reb": res["stats"]["reb"]["cond"]["game"],
        "cond_ast": res["stats"]["ast"]["cond"]["game"],
        "cond_fg3m": res["stats"]["fg3m"]["cond"]["game"],
        "joint_pts_full_vs_rprod": res["stats"]["pts"]["joint"]["full_vs_rprod"]["game"],
        "joint_pts_full_vs_prod": res["stats"]["pts"]["joint"]["full_vs_prod"]["game"],
    }
    names = [k for k in SECONDARY if items[k].get("delta") is not None]
    if not names:
        return {}
    adj = bh_adjust(np.array([items[k]["p_less"] for k in names]))
    out: dict[str, Any] = {}
    for k, pb in zip(names, adj, strict=True):
        s = items[k]
        verdict = (
            "improves" if s["delta"] < 0 and pb <= 0.05
            else "harms" if s["delta"] > 0 and s["lower95"] > 0
            else "no difference detected"
        )  # fmt: skip
        out[k] = {"delta": s["delta"], "n": s["n"], "upper95": s["upper95"], "p_less": s["p_less"], "p_bh": float(pb),
                  "verdict": verdict, "meets_floor": bool(s["delta"] <= FLOOR)}  # fmt: skip
    return out


def decide(res: dict[str, Any]) -> dict[str, Any]:
    """The frozen verdict logic for the primary (pts conditional)."""
    g, st = res["gates"], res["stats"]["pts"]
    cond = st["cond"]
    n = cond["n"]
    look = "final" if n >= N_MIN else "interim" if n >= N_INTERIM else "none"
    gd = cond.get("game", {})
    dd = cond.get("date", {})
    if gd.get("delta") is None:
        return {"look": look, "n": n, "verdict": "NO_DATA"}
    integrity_ok = (
        g["rows_after_tip"] == 0
        and g["prod_mismatch"] == 0
        and (g["late_share"] is None or g["late_share"] <= LATE_PAIR_SHARE)
        and cond["repro_max_abs"] <= REPRO_TOL
    )
    kill = bool(gd["delta"] > 0 and gd["lower95"] > 0)
    out: dict[str, Any] = {
        "look": look, "n": n, "integrity_ok": integrity_ok, "prod_compared": g["prod_compared"],
        "kill_recommended": kill,
    }  # fmt: skip
    if look != "final":
        out["verdict"] = (
            "KILL_RECOMMENDED"
            if kill and look in ("interim", "final")
            else f"NO_VERDICT_{look.upper()}_LOOK_ONLY_KILLS"
        )
        return out
    pl, sh = st.get("placebo_full_minus_stale", {}), st.get("shuffled_full_minus_base", {})
    checks = {
        "floor": gd["delta"] <= FLOOR,
        "bound_game_fit_inflated": gd["upper95_fit"] < 0,
        "bound_date_fit_inflated": dd.get("upper95_fit", 1.0) < 0,
        "placebo": pl.get("delta") is not None and pl["delta"] <= PLACEBO_MARGIN and pl["upper95"] < 0,
        "shuffle_alive": sh.get("delta") is not None and sh["lower95"] > 0,
        "slices": all(
            (s.get("delta") is None) or s["delta"] <= SLICE_TOL for s in st.get("slices", {}).values()
        ),
        "integrity": integrity_ok,
    }  # fmt: skip
    out["checks"] = {k: bool(v) for k, v in checks.items()}
    if not integrity_ok:
        out["verdict"] = "NO_VERDICT_INTEGRITY"
    elif all(checks.values()):
        out["verdict"] = "PASS"
    elif gd["half_width_one_sided"] <= 0.005:
        out["verdict"] = "FAIL"
    else:
        out["verdict"] = "INCONCLUSIVE"
    return out


def score_live(
    con: duckdb.DuckDBPyConnection, season: int = 2026, lead: int = LEAD_MINUTES
) -> dict[str, Any]:
    res: dict[str, Any] = {"gates": integrity_gates(con, lead, season), "stats": {}}
    for stat in STATS:
        res["stats"][stat] = score_stat(load_live(con, stat, season, lead), stat)
    res["secondary"] = secondary_family(res)
    res["decision"] = decide(res)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    res = score_live(con, a.season)
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt)
    print(json.dumps(res["decision"], indent=1, default=str))


if __name__ == "__main__":
    main()
