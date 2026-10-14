# ruff: noqa: E501
"""AVG_SHADOW_LIVE_2 reader and scorer (docs/prereg/AVG_SHADOW_LIVE_2.md, frozen; sha256 9afbfeb6), by the pattern of ``full_shadow_live``.

Read-only. ``load_live`` builds the intention-to-treat population from the stored grids (FULL, A0 and AVG all scored from their 19 stored
quantiles on the integer grid); ``integrity_gates``, ``score_stat``, ``secondary_family``, ``decide`` are the frozen arithmetic. The ONE
cluster-bootstrap (``cluster_boot`` / ``one_sided``), the stale-adjustment placebo and the day-shuffle are imported from
``nba.eval.full_shadow_live``, not copied. No look is computed before its date: ``look_due`` decides, ``read_look`` refuses otherwise and
``daily_display`` prints only counts and gates (no delta).

Decisions made before any live row (the rule leaves them open): for a triple with no on-time AVG row the AVG_A0 control is also the FULL grid
(both arms are "served FULL" there, delta 0); the placebo and the day-shuffle are built from the ITT AVG grids (a missing triple has the
adjustment 0); AVG_A0 is scored from the mixture of the two 19-atom pmfs (38 atoms of mass 1/38; 19 quantiles at the same taus).

G1 (the only absolute limit) is read on the 199-point integer pmf stored in ``forward_avg_shadow.avg_pmf199``, never on the 19 atoms
(AVG_SHADOW_LIVE_2 G1/P3; ``g1_pmf199``). A triple without an on-time AVG row is read as its FULL grid under the intention-to-treat rule,
i.e. ``to_integer_support(ext19(stored FULL 19 quantiles))``. Every CRPS contrast and G2-G5 stay on the 19 atoms for every arm; the 19-atom
G1 reading is printed beside the real one, descriptive, and decides nothing.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME
from nba.eval.context_residual_eval import bh_adjust
from nba.eval.full_shadow_live import (
    Z1,
    cluster_boot,
    crps_rows,
    one_sided,
    shuffle_grids_within_day,
    stale_adjust_placebo,
    to_int_grid,
)
from nba.eval.lower_tail_eval import guard, randomized_pit
from nba.props.forward import QUANTILE_TAUS

__all__ = ["cluster_boot", "one_sided"]  # the shared bootstrap, re-exported for the tests

STATS = ("pts", "reb", "ast", "fg3m")
LEAD_MINUTES = 60
SEASON = 2026
REGULAR = "002"
MIN_PRIOR = 3
FLOOR = -0.005
SIGMA_F = 0.0010  # fit-noise term of (b)
PLACEBO_MARGIN = -0.0025
MIXTURE_MARGIN = -0.003
LATE_HALF_MARGIN = -0.003
N_MIN = {"pts": 6900, "reb": 3500, "ast": 1500, "fg3m": 1100}
N_INTERIM = 3450
HALF_WIDTH_FAIL = 0.005
Z2 = 1.959964  # the second look
INTERIM_DATE = date(2026, 11, 15)
FINAL_DATE = date(2027, 1, 10)
LATE_SHARE = 0.02
STALE_SHARE = 0.10
COVERAGE_ALL, COVERAGE_SLICE, COVERAGE_KILL = 0.95, 0.90, 0.80
MISSING_GAP_PP = 2.0
MAX_BIAS, COV_LO, COV_HI = 0.5, 0.75, 0.85
SLICE_WORSE, MIN_SLICE_N = 0.01, 300
TEAM_SHARE_MAX, KO_FLOOR = 0.25, -0.0025
TLL_MAX, THRESHOLDS = 0.005, (20, 25, 30)
G5_FLOOR = -0.005
BENCH_MINUTES, LOW_P = 20.0, 0.9
REPRO_TOL = 1e-9
G1_N = 199  # G1 is read on the 199 equal-mass integer values of ``avg_pmf199``
GRID_TOL = 0.95
TAUS = np.asarray(QUANTILE_TAUS, dtype=float)
SECONDARY = (
    "pts_avg_a0",
    "reb_avg_full",
    "ast_avg_full",
    "fg3m_avg_full",
    "reb_avg_a0",
    "ast_avg_a0",
    "fg3m_avg_a0",
)


class LookNotDue(RuntimeError):
    """A look was asked for before its date (or before its row count): nothing is computed."""


# --------------------------------------------------------------------------- arithmetic on the stored 19-atom grids
def mix_atoms(qa: np.ndarray, qb: np.ndarray) -> np.ndarray:
    """19 quantiles (taus 0.05..0.95) of the 50/50 mixture of two 19-atom pmfs (38 equal atoms, mass 1/38 each)."""
    s = np.sort(np.concatenate([qa, qb], axis=1), axis=1)
    pos = np.ceil(TAUS * s.shape[1] - 1e-9).astype(int) - 1
    return np.asarray(s[:, pos], dtype=np.int16)


def thr_ll(q: np.ndarray, y: np.ndarray, n: float) -> np.ndarray:
    """Log loss of P(y >= n) read from the 19 atoms (clipped at 0.5/19)."""
    p = np.clip((q >= n).mean(axis=1), 0.5 / 19, 1 - 0.5 / 19)
    return np.asarray(-np.where(y >= n, np.log(p), np.log(1 - p)))


# --------------------------------------------------------------------------- reading the tables
_SQL = """
WITH prior AS (
    SELECT s.game_id, s.player_id,
           count(*) OVER (PARTITION BY s.player_id ORDER BY g.game_date, g.game_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS n_prior
    FROM player_game_stats s JOIN games g USING (game_id) WHERE s.minutes > 0
), full_ AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_full_shadow WHERE stat = ? AND made_at <= tipoff - ? * INTERVAL 1 MINUTE
), a0 AS (
    SELECT game_id, player_id, CAST(prediction ->> '$.q_grid' AS VARCHAR) AS grid,
           row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_predictions WHERE model_name = ? AND target = ? AND player_id > 0 AND made_at <= tipoff - ? * INTERVAL 1 MINUTE
), avg_ AS (
    SELECT game_id, player_id, CAST(avg_q19 AS VARCHAR) AS avg_q, CAST(seqc_q19 AS VARCHAR) AS seqc_q, CAST(avg_pmf199 AS VARCHAR) AS avg_p, stale_model, made_at,
           row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_avg_shadow WHERE stat = ? AND made_at <= tipoff_utc - ? * INTERVAL 1 MINUTE
), late AS (
    SELECT game_id, player_id, count(*) AS n FROM forward_avg_shadow WHERE stat = ? AND made_at > tipoff_utc - ? * INTERVAL 1 MINUTE
    GROUP BY 1, 2
){pp_cte}
SELECT f.game_id, g.game_date, f.tipoff, f.player_id, f.team_id, CAST(f.q_grid AS VARCHAR), a0.grid,
       av.avg_q, av.seqc_q, av.avg_p, av.stale_model, COALESCE(late.n, 0) AS n_late, COALESCE(p.n_prior, 0) AS n_prior,
       b.played, b.val, b.minutes, b.starter,
       {proj} AS proj_minutes, f.p_play_prod,
       (g.home_team = f.team_id AND g.home_pts > g.away_pts) OR (g.away_team = f.team_id AND g.away_pts > g.home_pts) AS won,
       EXISTS (SELECT 1 FROM games g2 WHERE g2.game_date = g.game_date - 1 AND (g2.home_team = f.team_id OR g2.away_team = f.team_id)) AS second_night,
       (SELECT count(*) FROM games g3 WHERE g3.season = g.season AND g3.game_date < g.game_date
                                            AND (g3.home_team = f.team_id OR g3.away_team = f.team_id)) AS team_game_no
FROM full_ f
JOIN games g ON g.game_id = f.game_id
JOIN a0 ON a0.game_id = f.game_id AND a0.player_id = f.player_id AND a0.rn = 1
LEFT JOIN avg_ av ON av.game_id = f.game_id AND av.player_id = f.player_id AND av.rn = 1
LEFT JOIN late ON late.game_id = f.game_id AND late.player_id = f.player_id
LEFT JOIN prior p ON p.game_id = f.game_id AND p.player_id = f.player_id
LEFT JOIN (SELECT game_id, player_id, max(CASE WHEN minutes > 0 THEN 1.0 ELSE 0.0 END) AS played,
                  max(CASE ? WHEN 'pts' THEN pts WHEN 'reb' THEN reb WHEN 'ast' THEN ast WHEN 'fg3m' THEN fg3m END) AS val,
                  max(minutes) AS minutes, bool_or(starter) AS starter
           FROM player_game_stats GROUP BY 1, 2) b ON b.game_id = f.game_id AND b.player_id = f.player_id
{pp_join}
WHERE f.rn = 1 AND g.season = ? AND g.home_pts > 0 AND g.away_pts > 0 AND f.game_id LIKE '002%'
ORDER BY f.game_id, f.player_id
"""  # fixed row order: the randomised PIT (seed 0) assigns its draws by row, so G1 must not depend on the physical order of the tables
_PP_CTE = """, pp AS (
    SELECT game_id, player_id, proj_minutes,
           row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_pplay_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE
)"""
_PP_JOIN = "LEFT JOIN pp ON pp.game_id = f.game_id AND pp.player_id = f.player_id AND pp.rn = 1"


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def load_live(
    con: duckdb.DuckDBPyConnection, stat: str, season: int = SEASON, lead: int = LEAD_MINUTES
) -> dict[str, np.ndarray]:
    """Every triple with a FULL row and an A0 row at the cutoff (latest ``made_at <= tipoff - lead``), regular season, final game.
    ``has_avg``: an on-time AVG row exists; ``late_only``: AVG rows exist but none is on time. Arms are the 19 stored quantiles."""
    has_pp = _has_table(con, "forward_pplay_shadow")
    sql = _SQL.format(
        pp_cte=_PP_CTE if has_pp else "",
        pp_join=_PP_JOIN if has_pp else "",
        proj="pp.proj_minutes" if has_pp else "CAST(NULL AS DOUBLE)",
    )
    model = CONTEXT_LT_PROPS_MODEL_NAME if stat == "pts" else CONTEXT_PROPS_MODEL_NAME
    args: list[Any] = (
        [stat, lead, model, stat, lead, stat, lead, stat, lead]
        + ([lead] if has_pp else [])
        + [stat, season]
    )
    cols = ["gid", "day", "tip", "pid", "team", "qf", "qa0", "qavg", "qseqc", "pavg", "stale", "n_late", "n_prior", "played", "val", "minutes",
            "starter", "proj", "p_play", "won", "b2b", "tgn"]  # fmt: skip
    keep: dict[str, list[Any]] = {c: [] for c in cols}
    short = 0
    for r in con.execute(sql, args).fetchall():
        d = dict(zip(cols, r, strict=True))
        gf, ga = json.loads(d["qf"]), json.loads(d["qa0"])
        if len(gf) != 19 or len(ga) != 19:
            short += 1
            continue
        d["qf"], d["qa0"] = to_int_grid(gf), to_int_grid(ga)
        d["qavg"] = None if d["qavg"] is None else np.asarray(json.loads(d["qavg"]), dtype=np.int16)
        d["qseqc"] = (
            None if d["qseqc"] is None else np.asarray(json.loads(d["qseqc"]), dtype=np.int16)
        )
        d["pavg"] = None if d["pavg"] is None else np.asarray(json.loads(d["pavg"]), dtype=np.int16)
        if (d["qavg"] is None) != (d["pavg"] is None) or (
            d["pavg"] is not None and len(d["pavg"]) != G1_N
        ):
            raise ValueError(
                f"forward_avg_shadow row {d['gid']}/{d['pid']}: avg_q19 and a {G1_N}-value avg_pmf199 must be stored together; G1 reads avg_pmf199"
            )
        for c in cols:
            keep[c].append(d[c])
    n = len(keep["gid"])
    has = np.array([q is not None for q in keep["qavg"]], dtype=bool)
    qavg = np.zeros((n, 19), dtype=np.int16)
    qseqc = np.zeros((n, 19), dtype=np.int16)
    pavg = np.zeros((n, G1_N), dtype=np.int16)
    for i in np.flatnonzero(has):
        qavg[i], qseqc[i], pavg[i] = keep["qavg"][i], keep["qseqc"][i], keep["pavg"][i]

    def f(k: str) -> np.ndarray:
        return np.array([np.nan if v is None else v for v in keep[k]], dtype=float)

    tm_pairs = teammate_out_pairs(con, season, lead) if n else set()
    return {
        "tm_out": np.array([(str(g), int(t)) in tm_pairs for g, t in zip(keep["gid"], keep["team"], strict=True)], dtype=bool),
        "gid": np.array(keep["gid"], dtype=str), "day": np.array(keep["day"], dtype="datetime64[D]"),
        "tip": np.array(keep["tip"], dtype="datetime64[s]"), "pid": np.array(keep["pid"], dtype=np.int64),
        "team": np.array(keep["team"], dtype=np.int64),
        "qf": np.array(keep["qf"], dtype=np.int16).reshape(n, 19), "qa0": np.array(keep["qa0"], dtype=np.int16).reshape(n, 19),
        "qavg": qavg, "qseqc": qseqc, "pavg": pavg, "has_avg": has, "stale": np.array([bool(s) for s in keep["stale"]], dtype=bool) & has,
        "late_only": (~has) & (np.array(keep["n_late"], dtype=int) > 0),
        "n_prior": f("n_prior"), "played": np.nan_to_num(f("played")) > 0, "y": f("val"), "minutes": f("minutes"),
        "starter": np.nan_to_num(f("starter")) > 0, "proj": f("proj"), "p_play": f("p_play"),
        "won": np.nan_to_num(f("won")) > 0, "b2b": np.nan_to_num(f("b2b")) > 0, "tgn": f("tgn"),
        "short": np.array([short]),
    }  # fmt: skip


def integrity_gates(
    con: duckdb.DuckDBPyConnection, lead: int = LEAD_MINUTES, season: int = SEASON
) -> dict[str, Any]:
    """Stamp, shadow-integrity and refit-staleness counts straight from ``forward_avg_shadow``. ``a0_mismatch`` / ``full_mismatch``: for each
    ``in_prod_log`` row the stored A0 equals the latest production row at or before it and the stored FULL equals the latest
    ``forward_full_shadow`` row with ``made_at <=`` the AVG row's ``made_at`` (1e-9). ``grid_repro_*``: the stored AVG quantiles from the stored
    199 values (exact) and from the stored SEQc and FULL atoms (within 1 on at least 95% of cells)."""
    if not _has_table(con, "forward_avg_shadow"):
        return {
            "rows": 0,
            "rows_after_tip": 0,
            "a0_compared": 0,
            "a0_mismatch": 0,
            "full_compared": 0,
            "full_mismatch": 0,
            "invalid_rows": 0,
            "grid_repro_exact_fail": 0,
            "grid_repro_atoms_share": None,
        }

    def q(sql: str, a: list[Any] | None = None) -> int:
        row = con.execute(sql, a or []).fetchone()
        return int(row[0]) if row else 0

    rows = q(
        "SELECT count(*) FROM forward_avg_shadow a JOIN games g USING (game_id) WHERE g.season = ?",
        [season],
    )
    after_tip = q("SELECT count(*) FROM forward_avg_shadow WHERE made_at >= tipoff_utc")
    out: dict[str, Any] = {"rows": rows, "rows_after_tip": after_tip}
    a0_cmp = a0_bad = 0
    for stat in STATS:
        model = CONTEXT_LT_PROPS_MODEL_NAME if stat == "pts" else CONTEXT_PROPS_MODEL_NAME
        for ga, gp in con.execute(
            """SELECT CAST(s.a0_q19 AS VARCHAR), p.grid FROM forward_avg_shadow s JOIN games g USING (game_id)
               JOIN (SELECT game_id, player_id, made_at, run_id, CAST(prediction ->> '$.q_grid' AS VARCHAR) AS grid FROM forward_predictions
                     WHERE model_name = ? AND target = ?) p
                 ON p.game_id = s.game_id AND p.player_id = s.player_id AND p.made_at <= s.made_at
               WHERE s.in_prod_log AND s.stat = ? AND g.season = ?
               QUALIFY row_number() OVER (PARTITION BY s.run_id, s.game_id, s.player_id ORDER BY p.made_at DESC, p.run_id DESC) = 1""",
            [model, stat, stat, season],
        ).fetchall():
            a0_cmp += 1
            a, b = np.asarray(json.loads(ga), float), np.asarray(json.loads(gp), float)
            a0_bad += int(a.shape != b.shape or bool(np.max(np.abs(a - b)) > 1e-9))
    out.update(a0_compared=a0_cmp, a0_mismatch=a0_bad)
    f_cmp = f_bad = 0
    if _has_table(con, "forward_full_shadow"):
        for ga, gf in con.execute(
            """SELECT CAST(s.full_q19 AS VARCHAR), CAST(f.q_grid AS VARCHAR) FROM forward_avg_shadow s JOIN games g USING (game_id)
               JOIN forward_full_shadow f ON f.game_id = s.game_id AND f.player_id = s.player_id AND f.stat = s.stat AND f.made_at <= s.made_at
               WHERE s.in_prod_log AND g.season = ?
               QUALIFY row_number() OVER (PARTITION BY s.run_id, s.game_id, s.player_id, s.stat ORDER BY f.made_at DESC, f.run_id DESC) = 1""",
            [season],
        ).fetchall():
            f_cmp += 1
            a, b = np.asarray(json.loads(ga), float), np.asarray(json.loads(gf), float)
            f_bad += int(a.shape != b.shape or bool(np.max(np.abs(a - b)) > 1e-9))
    out.update(full_compared=f_cmp, full_mismatch=f_bad)
    # refit staleness: a row is valid iff its month_block is the game month (ET), installed_at <= made_at and fit_through < the 1st of that month
    out["invalid_rows"] = q(
        """SELECT count(*) FROM forward_avg_shadow s JOIN games g USING (game_id) WHERE g.season = ? AND NOT (
               s.month_block = strftime(g.game_date, '%Y-%m') AND s.installed_at <= s.made_at
               AND s.fit_through < date_trunc('month', g.game_date) )""",
        [season],
    )
    out["invalid_not_flagged"] = q(
        """SELECT count(*) FROM forward_avg_shadow s JOIN games g USING (game_id) WHERE g.season = ? AND NOT s.stale_model AND NOT (
               s.month_block = strftime(g.game_date, '%Y-%m') AND s.installed_at <= s.made_at
               AND s.fit_through < date_trunc('month', g.game_date) )""",
        [season],
    )
    # grid reproduction
    from nba.daily.avg_shadow.grids import q19_of_grid199

    exact_fail = cells = near = 0
    for ap, aq, sq, fq in con.execute(
        "SELECT CAST(avg_pmf199 AS VARCHAR), CAST(avg_q19 AS VARCHAR), CAST(seqc_q19 AS VARCHAR), CAST(full_q19 AS VARCHAR) FROM forward_avg_shadow s JOIN games g USING (game_id) WHERE g.season = ?",
        [season],
    ).fetchall():
        a = np.array(json.loads(aq))
        exact_fail += int(not np.array_equal(q19_of_grid199(np.array([json.loads(ap)]))[0], a))
        s_, f_ = np.array(json.loads(sq)), to_int_grid(json.loads(fq))
        near += int(((a >= np.minimum(s_, f_) - 1) & (a <= np.maximum(s_, f_) + 1)).sum())
        cells += 19
    out.update(
        grid_repro_exact_fail=exact_fail, grid_repro_atoms_share=(near / cells) if cells else None
    )
    return out


# --------------------------------------------------------------------------- the ITT population and its arms
def population(rows: dict[str, np.ndarray]) -> dict[str, Any]:
    """Eligible = played, n_prior >= 3 (FULL and A0 rows are the base of ``load_live``). Verdict population = eligible and not stale.
    Coverage counts triples with an on-time AVG row."""
    elig = rows["played"] & (rows["n_prior"] >= MIN_PRIOR) & np.isfinite(rows["y"])
    verdict = elig & ~rows["stale"]
    n = int(elig.sum())
    return {
        "eligible": elig, "verdict": verdict, "n_eligible": n, "n_verdict": int(verdict.sum()),
        "n_with_avg": int((elig & rows["has_avg"]).sum()), "n_stale": int((elig & rows["stale"]).sum()),
        "n_late_only": int((elig & rows["late_only"]).sum()),
        "coverage": float((elig & rows["has_avg"]).sum() / n) if n else None,
        "stale_share": float((elig & rows["stale"]).sum() / n) if n else None,
        "late_share": float((elig & rows["late_only"]).sum() / n) if n else None,
    }  # fmt: skip


def arms(rows: dict[str, np.ndarray], m: np.ndarray) -> dict[str, np.ndarray]:
    """ITT grids on the mask ``m``: AVG row where it exists and is on time, the FULL grid otherwise (delta 0), never dropped."""
    qf, qa0, ha = rows["qf"][m], rows["qa0"][m], rows["has_avg"][m]
    qavg = np.where(ha[:, None], rows["qavg"][m], qf)
    qseqc = np.where(ha[:, None], rows["qseqc"][m], qf)
    avg_a0 = np.where(
        ha[:, None], mix_atoms(qseqc, qa0), qf
    )  # control: mixture of SEQc and A0 where the sequence exists, FULL where AVG is served FULL
    return {"FULL": qf, "A0": qa0, "AVG": qavg, "SEQc": qseqc, "AVG_A0": avg_a0}


def both(
    delta: np.ndarray, gid: np.ndarray, day: np.ndarray, sigma_fit: float = 0.0
) -> dict[str, Any]:
    return {
        "game": one_sided(delta, gid, sigma_fit),
        "date": one_sided(delta, day.astype("int64"), sigma_fit),
    }


def per_protocol_delta(rows: dict[str, np.ndarray], m: np.ndarray) -> dict[str, Any]:
    """Descriptive only: AVG minus FULL on triples that have an on-time AVG row."""
    mm = m & rows["has_avg"]
    if mm.sum() < 2:
        return {"n": int(mm.sum())}
    d = crps_rows(rows["qavg"][mm], rows["y"][mm]) - crps_rows(rows["qf"][mm], rows["y"][mm])
    return {"n": int(mm.sum()), **one_sided(d, rows["gid"][mm])}


def g1_pmf199(rows: dict[str, np.ndarray], m: np.ndarray) -> np.ndarray:
    """The G1 object on the mask ``m`` (AVG_SHADOW_LIVE_2): the stored 199-point ``avg_pmf199`` where an on-time AVG row exists, and for an
    intention-to-treat triple without one (served FULL, rule 2) ``to_integer_support(ext19(stored FULL 19 quantiles))``, the construction the
    shadow uses for FULL inside the mixture. Never the 19 atoms."""
    from nba.daily.avg_shadow.grids import ext19
    from nba.props.context_residual import to_integer_support

    ha = rows["has_avg"][m]
    recon = to_integer_support(ext19(rows["qf"][m])).astype(np.int16)
    return np.where(ha[:, None], rows["pavg"][m], recon)


def calibration(q: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """G1 on an equal-mass integer grid with the randomised PIT, seed 0 (never an inclusive or strict P(y <= q)). The verdict object is the
    199-point ``avg_pmf199`` (``g1_pmf199``); the 19-atom reading (the 19 stored quantiles as equal-mass atoms) is invalid for integer
    forecasts (AVG_SHADOW_LIVE_2 review) and is printed only as a descriptive cross-check."""
    pit = randomized_pit(q.astype(float), y, 0)
    cov80 = float(((pit > 0.10) & (pit <= 0.90)).mean())
    bias = float((y - q.mean(axis=1)).mean())
    g = guard(pit)
    return {
        "bias": bias,
        "cov80": cov80,
        "guard": g,
        "ok": bool(abs(bias) <= MAX_BIAS and COV_LO <= cov80 <= COV_HI and g["ok"]),
    }


def tip_hour_et(tip_utc: np.ndarray) -> np.ndarray:
    """Real tip-off hour (US/Eastern, DST-aware) of naive-UTC tips; G5 reads games at or after 19:00 ET."""
    import pandas as pd

    idx = pd.DatetimeIndex(tip_utc).tz_localize("UTC").tz_convert("America/New_York")
    return np.asarray(idx.hour + idx.minute / 60.0, dtype=float)


def teammate_out_pairs(
    con: duckdb.DuckDBPyConnection, season: int = SEASON, lead: int = LEAD_MINUTES
) -> set[tuple[str, int]]:
    """{(game_id, team_id)} with a teammate OUT in the game's latest official report usable at the cutoff (``as_of <= tip - lead``, ET clock);
    an OUT player's team is the team of his latest box row before the game."""
    import pandas as pd

    from nba.daily.injury import to_report_dt

    if not _has_table(con, "player_availability"):
        return set()
    av = con.execute(
        """SELECT a.game_id, a.player_id, lower(a.status) AS status, CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date,
                  (SELECT min(f.tipoff) FROM forward_full_shadow f WHERE f.game_id = a.game_id) AS tip
           FROM player_availability a JOIN games g USING (game_id)
           WHERE g.season = ? AND a.source = 'nba_official_report' AND a.player_id IS NOT NULL""",
        [season],
    ).df()
    av = av[av["tip"].notna()]
    if av.empty:
        return set()
    av["cut"] = [to_report_dt(t.to_pydatetime()) - timedelta(minutes=lead) for t in av["tip"]]
    av = av[av["as_of"] <= av["cut"]]
    latest = av.groupby("game_id")["as_of"].transform("max")
    out = av[(av["as_of"] == latest) & (av["status"] == "out")].copy()
    if out.empty:
        return set()
    box = con.execute(
        "SELECT s.player_id, s.team_id, g.game_date FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season >= ?",
        [season - 1],
    ).df()
    box["game_date"] = pd.to_datetime(box["game_date"])
    out["game_date"] = pd.to_datetime(out["game_date"])
    m = pd.merge_asof(
        out.sort_values("game_date"),
        box.sort_values("game_date").rename(columns={"game_date": "d"}),
        left_on="game_date",
        right_on="d",
        by="player_id",
        allow_exact_matches=False,
    )
    return {
        (str(g), int(t)) for g, t in zip(m["game_id"], m["team_id"], strict=True) if pd.notna(t)
    }


def slice_masks(rows: dict[str, np.ndarray], m: np.ndarray) -> dict[str, np.ndarray]:
    r = {k: v[m] for k, v in rows.items() if k != "short"}
    mn = r["minutes"]
    return {
        "starter": r["starter"], "bench": np.nan_to_num(r["proj"], nan=99.0) < BENCH_MINUTES, "cold_start": r["n_prior"] < 10,
        "first15": r["tgn"] < 15, "teammate_out": r["tm_out"], "second_night": r["b2b"], "low_p": np.nan_to_num(r["p_play"], nan=1.0) < LOW_P,
        "minutes<5": mn < 5, "minutes5-20": (mn >= 5) & (mn <= 20), "minutes>20": mn > 20,
    }  # fmt: skip


def score_stat(
    rows: dict[str, np.ndarray], stat: str, pop: dict[str, Any] | None = None, z: float = Z1
) -> dict[str, Any]:
    """Primary / secondary / guard arithmetic for one stat. sigma_F enters the AVG minus FULL contrast only (for (b))."""
    pop = pop or population(rows)
    m = pop["verdict"]
    if m.sum() < 2:
        return {"n": int(m.sum()), "note": "no verdict rows"}
    gid, day, pid, y = rows["gid"][m], rows["day"][m], rows["pid"][m], rows["y"][m]
    a = arms(rows, m)
    c = {k: crps_rows(v, y) for k, v in a.items()}
    d_full, d_a0, d_ctrl = c["AVG"] - c["FULL"], c["AVG"] - c["A0"], c["AVG"] - c["AVG_A0"]
    out: dict[str, Any] = {
        "n": int(m.sum()), "n_games": int(len(np.unique(gid))), "n_dates": int(len(np.unique(day))),
        "avg_minus_full": both(d_full, gid, day, 0.0), "avg_minus_full_fit": both(d_full, gid, day, SIGMA_F),
        "avg_minus_a0": one_sided(d_a0, gid), "avg_minus_avg_a0": {"point": float(d_ctrl.mean()), **one_sided(d_ctrl, gid)},
        "per_protocol_avg_minus_full": per_protocol_delta(rows, m),
        "coverage": pop["coverage"], "n_eligible": pop["n_eligible"],
    }  # fmt: skip
    # reproduction of the stored settle scores from the stored grids
    out["half_width_fit"] = {
        k: float(z * math.sqrt(out["avg_minus_full"][k]["se"] ** 2 + SIGMA_F**2))
        for k in ("game", "date")
        if out["avg_minus_full"][k].get("delta") is not None
    }
    # placebo and day-shuffle on the ITT grids
    order = rows["tip"][m].astype("int64")
    placebo = stale_adjust_placebo(pid, order, a["FULL"], a["AVG"])
    out["avg_minus_placebo"] = one_sided(c["AVG"] - crps_rows(placebo, y), gid)
    shuf = shuffle_grids_within_day(a["AVG"], day)
    out["shuffled_minus_full"] = one_sided(crps_rows(shuf, y) - c["FULL"], gid)
    # later half by date
    med = np.median(day.astype("int64"))
    late_half = day.astype("int64") > med
    out["later_half_avg_minus_full"] = {
        "n": int(late_half.sum()),
        "point": float(d_full[late_half].mean()) if late_half.any() else None,
    }
    # guards (AVG against A0)
    pmf = g1_pmf199(rows, m)
    cal = calibration(pmf, y)
    cal19 = calibration(a["AVG"], y)  # descriptive only: the 19-atom reading decides nothing
    sl = slice_masks(rows, m)
    slices = {
        k: {
            "n": int(mk.sum()),
            "point": float(d_a0[mk].mean()) if mk.sum() >= MIN_SLICE_N else None,
        }
        for k, mk in sl.items()
    }
    slices["stale_model_rows"] = {"n": int((pop["eligible"] & rows["stale"]).sum()), "point": None}
    g2 = all(v["point"] <= SLICE_WORSE for v in slices.values() if v["point"] is not None)
    gain = -d_a0
    teams = rows["team"][m]
    per = {int(t): float(gain[teams == t].sum()) for t in np.unique(teams)}
    top = max(per, key=lambda t: per[t])
    tot = float(gain.sum())
    share = per[top] / tot if tot > 0 else float("nan")
    keep = teams != top
    g3 = bool(
        (not np.isfinite(share))
        or (share <= TEAM_SHARE_MAX and float(d_a0[keep].mean()) <= KO_FLOOR)
    )
    tll = {
        str(t): float((thr_ll(a["AVG"], y, t) - thr_ll(a["A0"], y, t)).mean()) for t in THRESHOLDS
    }
    g4 = all(v <= TLL_MAX for v in tll.values())
    late_tip = (
        (rows["tip"][m].astype("datetime64[h]").astype("int64") % 24 - 4) % 24 >= 19
    )  # 19:00 ET and later (tipoff_utc minus 4 h, an approximation of ET used only for G5)
    g5n = int(late_tip.sum())
    g5 = bool(g5n < MIN_SLICE_N or float(d_a0[late_tip].mean()) <= G5_FLOOR)
    out["guards"] = {
        "G1_calibration": {
            **cal, "n_ok": bool(len(y) >= 3000), "form": "avg_pmf199",
            "share_without_avg_row": float((~rows["has_avg"][m]).mean()), "atom19_descriptive": cal19,
        }, "G2_slices": {"ok": bool(g2), "slices": slices},
        "G3_concentration": {"ok": g3, "top_team": top, "top_share": share}, "G4_thresholds": {"ok": bool(g4), "delta": tll},
        "G5_proxy_safe": {"ok": g5, "n": g5n},
    }  # fmt: skip
    return out


def secondary_family(res: dict[str, Any]) -> dict[str, Any]:
    """BH over exactly the seven declared tests (game-clustered one-sided p). Labels: improves / harms / no difference detected; floor separate."""
    pick: dict[str, dict[str, Any]] = {}
    for key, (stat, contrast) in {
        "pts_avg_a0": ("pts", "avg_minus_a0"), "reb_avg_full": ("reb", "full"), "ast_avg_full": ("ast", "full"), "fg3m_avg_full": ("fg3m", "full"),
        "reb_avg_a0": ("reb", "avg_minus_a0"), "ast_avg_a0": ("ast", "avg_minus_a0"), "fg3m_avg_a0": ("fg3m", "avg_minus_a0"),
    }.items():  # fmt: skip
        s = res["stats"].get(stat, {})
        item = s.get("avg_minus_full", {}).get("game") if contrast == "full" else s.get(contrast)
        if item and item.get("delta") is not None and s.get("n", 0) >= N_MIN[stat]:
            pick[key] = item
    if not pick:
        return {}
    names = list(pick)
    adj = bh_adjust(np.array([pick[k]["p_less"] for k in names]))
    out: dict[str, Any] = {}
    for k, pb in zip(names, adj, strict=True):
        s = pick[k]
        label = (
            "improves"
            if s["delta"] < 0 and pb <= 0.05
            else "harms"
            if s["delta"] > 0 and s["lower95"] > 0
            else "no difference detected"
        )
        out[k] = {"delta": s["delta"], "n": s["n"], "upper95": s["upper95"], "p_less": s["p_less"], "p_bh": float(pb), "label": label,
                  "meets_floor": bool(s["delta"] <= FLOOR and s["upper95"] < 0)}  # fmt: skip
    return out


# --------------------------------------------------------------------------- looks
def sundays(start: date, end: date) -> list[date]:
    d = start + timedelta(days=(6 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def look_due(
    today: date,
    n_pts_by_sunday: dict[date, int],
    last_regular_sunday: date,
    history: list[str] | None = None,
) -> str | None:
    """Which look, if any, is due TODAY (``interim`` | ``final`` | ``second`` | None). Fixed calendar dates; counts decide only whether a Sunday qualifies.
    interim: Sun 2026-11-15 if pts triples >= 3,450, else the first Sunday after reaching it; final: the first Sunday on or after 2027-01-10 with
    pts >= 6,900 (else the end-of-season look on the last regular-season Sunday); second: once, after an INCONCLUSIVE final, at the end of the regular season."""
    history = history or []
    if today.weekday() != 6:
        return None
    n = n_pts_by_sunday.get(today, 0)
    if (
        "interim" not in history
        and today >= INTERIM_DATE
        and n >= N_INTERIM
        and "final" not in history
    ):
        return "interim"
    if (
        "final" not in history
        and today >= FINAL_DATE
        and (n >= N_MIN["pts"] or today >= last_regular_sunday)
    ):
        return "final"
    if "final_inconclusive" in history and "second" not in history and today >= last_regular_sunday:
        return "second"
    return None


def missingness_table(rows: dict[str, np.ndarray], stat: str = "pts") -> dict[str, Any]:
    """AVG-missing rate (no on-time AVG row) by the declared splits, among triples with n_prior >= 3; gaps over 2 points are flagged."""
    base = rows["n_prior"] >= MIN_PRIOR
    miss = ~rows["has_avg"]
    played = rows["played"]
    pts_med: dict[int, float] = {}
    if stat == "pts":
        for p in np.unique(rows["pid"][base & played]):
            pts_med[int(p)] = float(np.nanmedian(rows["y"][base & played & (rows["pid"] == p)]))
    above = np.array(
        [
            bool(played[i]) and rows["y"][i] > pts_med.get(int(rows["pid"][i]), np.inf)
            for i in range(len(played))
        ]
    )
    splits = {
        "played": (played, ~played), "minutes>=5": (played & (rows["minutes"] >= 5), played & (rows["minutes"] < 5)),
        "starter": (rows["starter"] & played, ~rows["starter"] & played), "team_win": (rows["won"] & played, ~rows["won"] & played),
        "pts_above_median": (above, played & ~above),
    }  # fmt: skip
    out: dict[str, Any] = {}
    for k, (a, b) in splits.items():
        ma, mb = a & base, b & base
        ra = float(miss[ma].mean()) if ma.any() else None
        rb = float(miss[mb].mean()) if mb.any() else None
        gap = None if ra is None or rb is None else abs(ra - rb) * 100
        out[k] = {
            "missing_a": ra,
            "n_a": int(ma.sum()),
            "missing_b": rb,
            "n_b": int(mb.sum()),
            "gap_pp": gap,
            "flag": bool(gap is not None and gap > MISSING_GAP_PP),
        }
    return out


def coverage_gate(rows: dict[str, np.ndarray], pop: dict[str, Any]) -> dict[str, Any]:
    e = pop["eligible"]
    starter, bench = (
        e & rows["starter"],
        e & (np.nan_to_num(rows["proj"], nan=99.0) < BENCH_MINUTES),
    )

    def cov(mm: np.ndarray) -> float | None:
        return float(rows["has_avg"][mm].mean()) if mm.any() else None

    c = {"overall": pop["coverage"], "starter": cov(starter), "bench": cov(bench)}
    ok = (
        (c["overall"] or 0) >= COVERAGE_ALL
        and (c["starter"] is None or c["starter"] >= COVERAGE_SLICE)
        and (c["bench"] is None or c["bench"] >= COVERAGE_SLICE)
    )  # a slice with no rows is unmeasured (reported as None), not a failure
    return {
        **c,
        "ok": bool(ok),
        "itt_bound_note": "a coverage of 95% cannot move the pts mean by more than 5% of the largest per-row delta",
    }


def gate_summary(
    g: dict[str, Any], pops: dict[str, dict[str, Any]], cov: dict[str, Any]
) -> dict[str, Any]:
    """Integrity: any failure => no verdict for the affected stat (reported)."""
    p = pops["pts"]
    late = p["late_share"] or 0.0
    stale = p["stale_share"] or 0.0
    return {
        "stamp_ok": g["rows_after_tip"] == 0, "late_ok": late <= LATE_SHARE, "late_share": late,
        "mismatch_ok": g["a0_mismatch"] == 0 and g["full_mismatch"] == 0,
        "stale_ok": stale <= STALE_SHARE, "stale_share": stale, "invalid_unflagged": g.get("invalid_not_flagged", 0),
        "repro_ok": g["grid_repro_exact_fail"] == 0 and (g["grid_repro_atoms_share"] is None or g["grid_repro_atoms_share"] >= GRID_TOL),
        "coverage_ok": cov["ok"],
    }  # fmt: skip


def decide(res: dict[str, Any], look: str) -> dict[str, Any]:
    """The frozen verdict logic for the primary (pts). ``look``: interim | final | second."""
    st = res["stats"]["pts"]
    gs = res["gates"]
    n = st.get("n", 0)
    z = Z2 if look == "second" else Z1
    if st.get("avg_minus_full", {}).get("game", {}).get("delta") is None:
        return {"look": look, "n": n, "verdict": "NO_DATA"}
    g, dt = st["avg_minus_full"]["game"], st["avg_minus_full"]["date"]
    gf, df = st["avg_minus_full_fit"]["game"], st["avg_minus_full_fit"]["date"]
    kill_stat = bool(g["delta"] - z * math.sqrt(g["se"] ** 2 + SIGMA_F**2) > 0)
    integrity_kill = (
        (not gs["stamp_ok"])
        or (not gs["mismatch_ok"])
        or ((res["coverage"]["overall"] or 0) < COVERAGE_KILL)
    )
    if look == "interim":
        return {
            "look": look,
            "n": n,
            "verdict": "KILL" if kill_stat or integrity_kill else "NO_KILL",
            "kill_statistical": kill_stat,
            "kill_integrity": bool(integrity_kill),
        }
    integrity_ok = all(
        gs[k] for k in ("stamp_ok", "late_ok", "mismatch_ok", "stale_ok", "repro_ok", "coverage_ok")
    )
    half = max(z * math.sqrt(g["se"] ** 2 + SIGMA_F**2), z * math.sqrt(dt["se"] ** 2 + SIGMA_F**2))
    a0, pl, sh = st["avg_minus_a0"], st["avg_minus_placebo"], st["shuffled_minus_full"]
    ctrl, lh = st["avg_minus_avg_a0"], st["later_half_avg_minus_full"]
    guards = st["guards"]
    checks = {
        "a_floor": bool(g["delta"] <= FLOOR),
        "b_bound_game_fit": bool(gf["delta"] + z * math.sqrt(gf["se"] ** 2 + SIGMA_F**2) < 0),
        "b_bound_date_fit": bool(df["delta"] + z * math.sqrt(df["se"] ** 2 + SIGMA_F**2) < 0),
        "c_vs_a0": bool(a0["delta"] <= FLOOR and a0["upper95"] < 0),
        "d_placebo": bool(pl["delta"] <= PLACEBO_MARGIN and pl["upper95"] < 0),
        "e_shuffle_alive": bool(sh["lower95"] > 0),
        "f_vs_avg_a0": bool(ctrl["point"] <= MIXTURE_MARGIN),
        "g_later_half": bool(lh["point"] is not None and lh["point"] <= LATE_HALF_MARGIN),
        "h_guards": bool(guards["G1_calibration"]["ok"] and guards["G1_calibration"]["n_ok"] and guards["G2_slices"]["ok"] and guards["G3_concentration"]["ok"]
                         and guards["G4_thresholds"]["ok"] and guards["G5_proxy_safe"]["ok"]),
        "i_integrity": bool(integrity_ok),
    }  # fmt: skip
    out: dict[str, Any] = {
        "look": look,
        "n": n,
        "checks": checks,
        "half_width": float(half),
        "integrity": gs,
        "z": z,
    }
    if n < N_MIN["pts"] and look == "final":
        out["note"] = "below n_min at the end-of-season look: INCONCLUSIVE by count"
    if not integrity_ok:
        out["verdict"] = "INCONCLUSIVE" if look != "second" else "CLOSED_NO_CLAIM"
    elif all(checks.values()) and n >= N_MIN["pts"]:
        out["verdict"] = "PASS"
    elif half <= HALF_WIDTH_FAIL and n >= N_MIN["pts"]:
        out["verdict"] = "FAIL"
    else:
        out["verdict"] = "INCONCLUSIVE" if look != "second" else "CLOSED_NO_CLAIM"
    return out


def score_live(
    con: duckdb.DuckDBPyConnection, look: str, season: int = SEASON, lead: int = LEAD_MINUTES
) -> dict[str, Any]:
    z = Z2 if look == "second" else Z1
    res: dict[str, Any] = {"stats": {}, "look": look}
    pops: dict[str, dict[str, Any]] = {}
    allrows: dict[str, dict[str, np.ndarray]] = {}
    for stat in STATS:
        allrows[stat] = load_live(con, stat, season, lead)
        pops[stat] = population(allrows[stat])
        res["stats"][stat] = score_stat(allrows[stat], stat, pops[stat], z)
    res["coverage"] = coverage_gate(allrows["pts"], pops["pts"])
    raw_gates = integrity_gates(con, lead, season)
    res["gates"] = gate_summary(raw_gates, pops, res["coverage"])
    res["integrity_raw"] = raw_gates
    res["missingness"] = {s: missingness_table(allrows[s], s) for s in STATS}
    res["secondary"] = secondary_family(res)
    res["decision"] = decide(res, look)
    return res


def pts_by_sunday(
    con: duckdb.DuckDBPyConnection, first: date, last: date, season: int = SEASON
) -> dict[date, int]:
    """Verdict-population pts triples settled by each Sunday (cumulative); counts only, no delta."""
    rows = load_live(con, "pts", season)
    pop = population(rows)
    days = rows["day"][pop["verdict"]]
    return {s: int((days <= np.datetime64(s)).sum()) for s in sundays(first, last)}


def daily_display(con: duckdb.DuckDBPyConnection, season: int = SEASON) -> dict[str, Any]:
    """Counts and gates only (n, coverage, stale share, stamp and integrity gates); deltas from non-look days are not published."""
    out: dict[str, Any] = {}
    for stat in STATS:
        rows = load_live(con, stat, season)
        p = population(rows)
        out[stat] = {
            k: p[k]
            for k in (
                "n_eligible",
                "n_verdict",
                "n_with_avg",
                "n_stale",
                "n_late_only",
                "coverage",
                "stale_share",
                "late_share",
            )
        }
    g = integrity_gates(con, LEAD_MINUTES, season)
    out["gates"] = {
        k: g[k]
        for k in (
            "rows",
            "rows_after_tip",
            "a0_mismatch",
            "full_mismatch",
            "invalid_rows",
            "grid_repro_exact_fail",
        )
    }
    return out


def read_look(
    con: duckdb.DuckDBPyConnection,
    today: date,
    last_regular_sunday: date,
    history: list[str] | None = None,
    season: int = SEASON,
) -> dict[str, Any]:
    """Compute a look ONLY if one is due today; otherwise raise ``LookNotDue`` before touching a single score."""
    counts = (
        pts_by_sunday(con, INTERIM_DATE, max(last_regular_sunday, today), season)
        if today.weekday() == 6
        else {}
    )
    look = look_due(today, counts, last_regular_sunday, history)
    if look is None:
        raise LookNotDue(
            f"no look is due on {today} (calendar: interim {INTERIM_DATE}, final {FINAL_DATE}); daily_display() only"
        )
    return score_live(con, look, season)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--last-regular-sunday", type=date.fromisoformat, default=date(2027, 4, 11))
    ap.add_argument(
        "--history", default="", help="comma list: interim,final,final_inconclusive,second"
    )
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    con = duckdb.connect(a.db, read_only=True)
    try:
        res = read_look(con, a.today, a.last_regular_sunday, [h for h in a.history.split(",") if h])
    except LookNotDue as e:
        print(
            json.dumps(
                {"look": None, "message": str(e), "display": daily_display(con)},
                indent=1,
                default=str,
            )
        )
        return 0
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt)
    print(json.dumps(res["decision"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
