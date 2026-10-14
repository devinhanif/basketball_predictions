# ruff: noqa: E501
"""COMBO_STOCKS_SHADOW reader and scorer (docs/prereg/COMBO_STOCKS_SHADOW.md, frozen; sha256 b9b38cbe), by the pattern of ``avg_shadow_live``.

Read-only. ``load_live`` builds the intention-to-treat rows from the stored 199-point grids (S, RECENT, INDEP all scored with the SAME exact
integer CRPS, ``nba.daily.combo_shadow.settle.crps_rows``); ``integrity_gates``, ``score_target``, ``decide_target`` are the frozen arithmetic.
The ONE cluster bootstrap (``cluster_boot`` / ``one_sided``) and ``bh_adjust`` are imported, not copied; G1 and the slice guard are
``nba.props.combo_g1.g1`` / ``slice_guard``, so this reader is the rule's text and nothing else (NOT ``nba.eval.avg_shadow_live.calibration``).
No look is computed before its date: ``look_due`` decides, ``read_look`` refuses otherwise and ``daily_display`` prints only counts and gates
(no delta). Servability and the missingness table are printed at every look.

Decisions made before any live row (the rule leaves them open): the later half of the rows by date is the second half of the rows sorted by
(game_date, game_id, player_id); a slice mask is evaluated on the scored rows of its target; FAIL needs n >= n_min and the larger of the game-
and date-clustered one-sided half-widths <= floor_t; a target below n_min at its final look is INCONCLUSIVE; the second look replaces the
bootstrap bound of (b) by ``delta + 1.96 se`` and the half-width likewise (rule 7). Teammate-OUT uses the latest official report usable at the
cutoff (``as_of <= tip - 60 min``, ET clock), as the other readers do.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.daily.combo_shadow.config import FROZEN_BASE_SHA, TARGETS, ComboConfig, load_config
from nba.daily.combo_shadow.settle import crps_rows
from nba.eval.context_residual_eval import bh_adjust
from nba.eval.full_shadow_live import Z1, cluster_boot, one_sided
from nba.props import combo_g1
from nba.props.combo_copula import q19_of_grid199
from nba.props.stock_features import _DI, ew_states

__all__ = ["cluster_boot", "one_sided"]  # the shared bootstrap, re-exported for the tests

LEAD_MINUTES = 60
SEASON = 2026
REGULAR = "002"
MIN_PRIOR = 5
STOCKS = ("blk", "stl")
COMBOS = ("pra", "pr", "pa", "ra", "bs")
INDEP_REQUIRED = (
    "pra",
    "pr",
    "pa",
    "ra",
)  # (g): COPULA minus INDEP < 0 must hold; reported only for bs
N_MIN = 3000
N_INTERIM = 1500
Z2 = 1.959964  # the second look
INTERIM_DATE = date(2026, 11, 15)
FINAL_DATE = date(2027, 1, 10)
BH_Q = 0.05
COVERAGE_ALL, COVERAGE_SLICE, COVERAGE_KILL = 0.95, 0.90, 0.80
MISSING_GAP_PP = 2.0
STALE_DAYS, STALE_SHARE_MAX = 3, 0.10
NOT_SERVABLE_FLAG = 0.05
Y_SQL = {
    "blk": "b.blk", "stl": "b.stl", "pra": "b.pts + b.reb + b.ast", "pr": "b.pts + b.reb",
    "pa": "b.pts + b.ast", "ra": "b.reb + b.ast", "bs": "b.blk + b.stl",
}  # fmt: skip


def y_case(target: str) -> str:
    """SQL expression of the box quantity of ``target`` (the combos are sums of the same box columns)."""
    return f"CASE '{target}' " + " ".join(f"WHEN '{t}' THEN {e}" for t, e in Y_SQL.items()) + " END"


class LookNotDue(RuntimeError):
    """A look was asked for before its date (or before its row count): nothing is computed."""


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


# --------------------------------------------------------------------------- reading the tables
_SQL = """
WITH prior AS (
    SELECT s.game_id, s.player_id,
           count(*) OVER (PARTITION BY s.player_id ORDER BY g.game_date, g.game_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS n_prior
    FROM player_game_stats s JOIN games g USING (game_id) WHERE s.minutes > 0
), shadow AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_combo_shadow WHERE target = ? AND made_at <= tipoff_utc - ? * INTERVAL 1 MINUTE
), late AS (
    SELECT game_id, player_id, count(*) AS n FROM forward_combo_shadow
    WHERE target = ? AND made_at > tipoff_utc - ? * INTERVAL 1 MINUTE GROUP BY 1, 2
)
SELECT f.game_id, g.game_date, f.tipoff_utc, f.player_id, b.team_id,
       CAST(f.s_pmf199 AS VARCHAR), CAST(f.s_q19 AS VARCHAR), CAST(f.recent_pmf199 AS VARCHAR), CAST(f.indep_pmf199 AS VARCHAR),
       f.fit_through, f.n_prior, COALESCE(late.n, 0) AS n_late, COALESCE(p.n_prior, 0) AS n_prior_box,
       b.played, b.val, b.minutes,
       (g.home_team = b.team_id AND g.home_pts > g.away_pts) OR (g.away_team = b.team_id AND g.away_pts > g.home_pts) AS won,
       EXISTS (SELECT 1 FROM games g2 WHERE g2.game_date = g.game_date - 1 AND (g2.home_team = b.team_id OR g2.away_team = b.team_id)) AS second_night,
       (SELECT count(*) FROM games g3 WHERE g3.season = g.season AND g3.game_date < g.game_date
                                            AND (g3.home_team = b.team_id OR g3.away_team = b.team_id)) AS team_game_no
FROM shadow f
JOIN games g ON g.game_id = f.game_id
LEFT JOIN late ON late.game_id = f.game_id AND late.player_id = f.player_id
LEFT JOIN prior p ON p.game_id = f.game_id AND p.player_id = f.player_id
JOIN (SELECT game_id, player_id, max(team_id) AS team_id, max(CASE WHEN minutes > 0 THEN 1.0 ELSE 0.0 END) AS played,
             max({ycase}) AS val, max(minutes) AS minutes
      FROM player_game_stats b GROUP BY 1, 2) b ON b.game_id = f.game_id AND b.player_id = f.player_id
WHERE f.rn = 1 AND g.season = ? AND g.home_pts > 0 AND g.away_pts > 0 AND f.game_id LIKE '002%'
ORDER BY g.game_date, f.game_id, f.player_id
"""  # fixed row order: G1's randomised PIT assigns its draws by row (rule 6(d))


def starter10_map(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, int], float]:
    """``starter10`` (production's: the decay-10 weighted starter rate over the player's PLAYED games strictly before the row) per (game, player)."""
    import polars as pl

    df = con.execute(
        """SELECT s.player_id, s.game_id, g.game_date, CAST(s.starter AS DOUBLE) AS starter
           FROM player_game_stats s JOIN games g USING (game_id) WHERE s.minutes > 0"""
    ).pl()
    if df.is_empty():
        return {}
    df = df.with_columns(pl.col("player_id").cast(pl.Int64), pl.col("starter").fill_null(0.0)).sort(
        ["player_id", "game_date", "game_id"]
    )
    pre, _post, _n = ew_states(df, ("starter",))
    s10 = pre[:, _DI[10.0], 0]
    return {
        (str(g), int(p)): float(v)
        for g, p, v in zip(df["game_id"], df["player_id"], s10, strict=True)
    }


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
                  (SELECT min(f.tipoff_utc) FROM forward_combo_shadow f WHERE f.game_id = a.game_id) AS tip
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
        out.sort_values("game_date"), box.sort_values("game_date").rename(columns={"game_date": "d"}),
        left_on="game_date", right_on="d", by="player_id", allow_exact_matches=False,
    )  # fmt: skip
    return {
        (str(g), int(t)) for g, t in zip(m["game_id"], m["team_id"], strict=True) if pd.notna(t)
    }


def load_live(
    con: duckdb.DuckDBPyConnection, target: str, season: int = SEASON, lead: int = LEAD_MINUTES
) -> dict[str, np.ndarray]:
    """Every (game, player) with an on-time shadow row for ``target`` (latest ``made_at <= tipoff - lead``), regular season, final game, in the fixed
    order (game_date, game_id, player_id). ``late_only``: shadow rows exist but none is on time (counted, excluded)."""
    if not _has_table(con, "forward_combo_shadow"):
        return _empty_rows()
    rows = con.execute(
        _SQL.replace("{ycase}", y_case(target)), [target, lead, target, lead, season]
    ).fetchall()
    n = len(rows)
    if n == 0:
        return _empty_rows()
    st10 = starter10_map(con)
    tm = teammate_out_pairs(con, season, lead)

    def arr(i: int) -> np.ndarray:
        return np.array([np.nan if r[i] is None else r[i] for r in rows], dtype=float)

    s = np.array([json.loads(r[5]) for r in rows], dtype=np.int16)
    s_q19 = np.array([json.loads(r[6]) for r in rows], dtype=np.int16)
    rec = np.array([json.loads(r[7]) for r in rows], dtype=np.int16)
    has_i = np.array([r[8] is not None for r in rows], dtype=bool)
    ind = np.zeros_like(s)
    for i in np.flatnonzero(has_i):
        ind[i] = json.loads(rows[i][8])
    gid = np.array([r[0] for r in rows], dtype=str)
    pid = np.array([r[3] for r in rows], dtype=np.int64)
    team = np.array([-1 if r[4] is None else r[4] for r in rows], dtype=np.int64)
    return {
        "gid": gid, "day": np.array([r[1] for r in rows], dtype="datetime64[D]"), "tip": np.array([r[2] for r in rows], dtype="datetime64[s]"),
        "pid": pid, "team": team, "s": s, "s_q19": s_q19, "recent": rec, "indep": ind, "has_indep": has_i,
        "fit_through": np.array([r[9] for r in rows], dtype="datetime64[D]"), "n_prior": arr(10), "n_late": arr(11), "n_prior_box": arr(12),
        "played": np.nan_to_num(arr(13)) > 0, "y": arr(14), "minutes": arr(15), "won": np.nan_to_num(arr(16)) > 0,
        "b2b": np.nan_to_num(arr(17)) > 0, "tgn": arr(18),
        "starter10": np.array([st10.get((str(g), int(p)), np.nan) for g, p in zip(gid, pid, strict=True)], dtype=float),
        "tm_out": np.array([(str(g), int(t)) in tm for g, t in zip(gid, team, strict=True)], dtype=bool),
    }  # fmt: skip


def _empty_rows() -> dict[str, np.ndarray]:
    z = np.zeros(0)
    return {
        "gid": np.array([], dtype=str), "day": np.array([], dtype="datetime64[D]"), "tip": np.array([], dtype="datetime64[s]"),
        "pid": z.astype(np.int64), "team": z.astype(np.int64), "s": np.zeros((0, 199), np.int16), "s_q19": np.zeros((0, 19), np.int16),
        "recent": np.zeros((0, 199), np.int16), "indep": np.zeros((0, 199), np.int16), "has_indep": z.astype(bool),
        "fit_through": np.array([], dtype="datetime64[D]"), "n_prior": z, "n_late": z, "n_prior_box": z, "played": z.astype(bool),
        "y": z, "minutes": z, "won": z.astype(bool), "b2b": z.astype(bool), "tgn": z, "starter10": z, "tm_out": z.astype(bool),
    }  # fmt: skip


def eligible(rows: dict[str, np.ndarray]) -> np.ndarray:
    """Rule 4: played, regular season (by the SQL), ``n_prior >= 5``, an on-time shadow row (by the SQL), a finite label."""
    return np.asarray(rows["played"] & (rows["n_prior"] >= MIN_PRIOR) & np.isfinite(rows["y"]))


# --------------------------------------------------------------------------- servability, coverage, missingness
_SERVABLE_SQL = """
WITH prior AS (
    SELECT s.game_id, s.player_id,
           count(*) OVER (PARTITION BY s.player_id ORDER BY g.game_date, g.game_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS n_prior
    FROM player_game_stats s JOIN games g USING (game_id) WHERE s.minutes > 0
), prod AS (
    SELECT p.game_id, p.player_id, count(DISTINCT p.target) AS n_stats
    FROM (SELECT game_id, player_id, target, model_name,
                 row_number() OVER (PARTITION BY game_id, player_id, model_name, target ORDER BY made_at DESC, run_id DESC) rn
          FROM forward_predictions
          WHERE player_id > 0 AND made_at <= tipoff - ? * INTERVAL 1 MINUTE
            AND ((model_name = 'props_context_residual_lt' AND target = 'pts')
              OR (model_name = 'props_context_residual' AND target IN ('reb', 'ast', 'fg3m')))) p
    WHERE p.rn = 1 GROUP BY 1, 2
), anyprod AS (
    SELECT DISTINCT game_id, player_id FROM forward_predictions
    WHERE player_id > 0 AND model_name IN ('props_context_residual', 'props_context_residual_lt')
)
SELECT b.game_id, b.player_id, g.game_date, b.minutes, COALESCE(b.starter, false) AS starter,
       (g.home_team = b.team_id AND g.home_pts > g.away_pts) OR (g.away_team = b.team_id AND g.away_pts > g.home_pts) AS won,
       b.pts + b.reb + b.ast AS pra, COALESCE(pr.n_stats, 0) AS n_stats, (a.player_id IS NOT NULL) AS rostered
FROM player_game_stats b JOIN games g USING (game_id)
JOIN prior p ON p.game_id = b.game_id AND p.player_id = b.player_id
LEFT JOIN prod pr ON pr.game_id = b.game_id AND pr.player_id = b.player_id
LEFT JOIN anyprod a ON a.game_id = b.game_id AND a.player_id = b.player_id
WHERE g.season = ? AND g.home_pts > 0 AND g.away_pts > 0 AND b.game_id LIKE '002%' AND b.minutes > 0 AND p.n_prior >= ?
"""


def servability(
    con: duckdb.DuckDBPyConnection, season: int = SEASON, lead: int = LEAD_MINUTES
) -> dict[str, Any]:
    """Played regular-season players with ``n_prior >= 5``: how many were rostered (any production context row) and how many were SERVABLE (an on-time
    row for each of pts (lt), reb, ast, fg3m). The share of played rostered players that are not servable is flagged above 5% (rule 4)."""
    if not _has_table(con, "forward_predictions"):
        return {
            "played": 0,
            "rostered": 0,
            "servable": 0,
            "not_servable_share": None,
            "flag": False,
            "rows": [],
        }
    r = con.execute(_SERVABLE_SQL, [lead, season, MIN_PRIOR]).fetchall()
    arr = {
        k: np.array([x[i] for x in r])
        for i, k in enumerate(
            ("gid", "pid", "day", "minutes", "starter", "won", "pra", "n_stats", "rostered")
        )
    }
    if not r:
        return {
            "played": 0,
            "rostered": 0,
            "servable": 0,
            "not_servable_share": None,
            "flag": False,
            "rows": [],
        }
    rostered = arr["rostered"].astype(bool)
    serv = arr["n_stats"].astype(int) == 4
    share = float(1 - serv[rostered].mean()) if rostered.any() else None
    return {
        "played": len(r), "rostered": int(rostered.sum()), "servable": int(serv.sum()), "not_servable_share": share,
        "flag": bool(share is not None and share > NOT_SERVABLE_FLAG), "rows": r,
    }  # fmt: skip


def _share(have: np.ndarray, m: np.ndarray) -> float | None:
    return float(have[m].mean()) if m.any() else None


def coverage_and_missingness(
    con: duckdb.DuckDBPyConnection,
    serv: dict[str, Any],
    rows_by_target: dict[str, dict[str, np.ndarray]],
) -> dict[str, Any]:
    """G-COV: per target, servable played rows with an on-time shadow row over servable played rows (overall, starter, bench), and the
    missingness table (share of servable rows with NO on-time shadow row) by realised minutes < 5, starter, win/loss, above/below the player's median PRA."""
    out: dict[str, Any] = {"by_target": {}, "missingness": {}}
    sv = [x for x in serv["rows"] if int(x[7]) == 4]
    if not sv:
        return out
    gid = np.array([x[0] for x in sv])
    pid = np.array([int(x[1]) for x in sv])
    key = np.array([f"{g}|{p}" for g, p in zip(gid, pid, strict=True)])
    minutes = np.array([float(x[3]) for x in sv])
    started = np.array([bool(x[4]) for x in sv])
    won = np.array([bool(x[5]) for x in sv])
    pra = np.array([float(x[6]) for x in sv])
    med = {int(p): float(np.median(pra[pid == p])) for p in np.unique(pid)}
    above = np.array([pra[i] > med[int(pid[i])] for i in range(len(pid))])
    st10 = starter10_map(con)
    s10 = np.array([st10.get((g, int(p)), np.nan) for g, p in zip(gid, pid, strict=True)])
    starter_h, bench_h = s10 >= 0.5, s10 < 0.5
    for t in TARGETS:
        r = rows_by_target[t]
        have_keys = {f"{g}|{p}" for g, p in zip(r["gid"], r["pid"], strict=True)}
        have = np.array([k in have_keys for k in key])
        overall = float(have.mean())
        starter, bench = _share(have, starter_h), _share(have, bench_h)
        ok = (
            overall >= COVERAGE_ALL
            and (starter is None or starter >= COVERAGE_SLICE)
            and (bench is None or bench >= COVERAGE_SLICE)
        )
        out["by_target"][t] = {
            "overall": overall,
            "starter": starter,
            "bench": bench,
            "ok": bool(ok),
            "kill": bool(overall < COVERAGE_KILL),
            "n_servable": len(key),
        }
        if t == "pra":
            miss = ~have
            splits = {
                "minutes<5": (minutes < 5, minutes >= 5), "starter": (started, ~started), "team_win": (won, ~won),
                "pra_above_median": (above, ~above),
            }  # fmt: skip
            for name, (a, b) in splits.items():
                ra = float(miss[a].mean()) if a.any() else None
                rb = float(miss[b].mean()) if b.any() else None
                gap = None if ra is None or rb is None else abs(ra - rb) * 100
                out["missingness"][name] = {
                    "missing_a": ra, "n_a": int(a.sum()), "missing_b": rb, "n_b": int(b.sum()), "gap_pp": gap,
                    "flag": bool(gap is not None and gap > MISSING_GAP_PP),
                }  # fmt: skip
    return out


# --------------------------------------------------------------------------- integrity
def integrity_gates(
    con: duckdb.DuckDBPyConnection,
    rows_by_target: dict[str, dict[str, np.ndarray]],
    cfg: ComboConfig | None = None,
    season: int = SEASON,
) -> dict[str, Any]:
    """Stamp, late-row, reproducibility and staleness gates straight from ``forward_combo_shadow`` and the loaded rows."""
    if not _has_table(con, "forward_combo_shadow"):
        return {
            "rows": 0,
            "rows_after_tip": 0,
            "late_rows": 0,
            "tip_source_not_real": 0,
            "repro_q19_fail": 0,
            "id_mismatch": 0,
            "stale_share": None,
            "stale_ok": True,
        }
    cfg = cfg or load_config()

    def q(sql: str, a: list[Any] | None = None) -> int:
        row = con.execute(sql, a or []).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    out: dict[str, Any] = {
        "rows": q("SELECT count(*) FROM forward_combo_shadow s JOIN games g USING (game_id) WHERE g.season = ?", [season]),
        "rows_after_tip": q("SELECT count(*) FROM forward_combo_shadow WHERE made_at >= tipoff_utc"),
        "late_rows": q(f"SELECT count(*) FROM forward_combo_shadow WHERE made_at > tipoff_utc - {LEAD_MINUTES} * INTERVAL 1 MINUTE"),
        "tip_source_not_real": q("SELECT count(*) FROM forward_combo_shadow WHERE tip_source <> 'real'"),
        "id_mismatch": q("SELECT count(*) FROM forward_combo_shadow WHERE r_id <> ? OR base_id <> ?", [cfg.r_id, cfg.base_id])
        + int(cfg.base_id != FROZEN_BASE_SHA),
    }  # fmt: skip
    fail = 0
    for r in rows_by_target.values():
        if len(r["s"]):
            fail += int((q19_of_grid199(r["s"]) != r["s_q19"]).any(axis=1).sum())
    out["repro_q19_fail"] = fail
    stale = n = 0
    for r in rows_by_target.values():
        if len(r["s"]):
            age = (r["day"] - r["fit_through"]).astype(int)
            stale += int((age > STALE_DAYS).sum())
            n += len(age)
    out["stale_share"] = (stale / n) if n else None
    out["stale_ok"] = bool(n == 0 or stale / n <= STALE_SHARE_MAX)
    return out


# --------------------------------------------------------------------------- scoring one target
def slice_masks(rows: dict[str, np.ndarray], m: np.ndarray) -> dict[str, np.ndarray]:
    """Rule 6(e) slices over the scored rows: starter / bench (``starter10 >= 0.5``), realised minutes < 10, 10-20, > 20, teammate OUT on the report,
    second night of a back-to-back, cold start (``n_prior < 10``), first 15 team games."""
    r = {k: v[m] for k, v in rows.items()}
    mn, s10 = r["minutes"], r["starter10"]
    return {
        "starter": s10 >= 0.5, "bench": s10 < 0.5, "min<10": mn < 10, "min10-20": (mn >= 10) & (mn <= 20), "min>20": mn > 20,
        "teammate_out": r["tm_out"], "second_night": r["b2b"], "cold_start": r["n_prior"] < 10, "first15": r["tgn"] < 15,
    }  # fmt: skip


def _upper(res: dict[str, Any], z: float) -> float:
    return float(res["upper95"]) if z == Z1 else float(res["delta"]) + z * float(res["se"])


def _half(res: dict[str, Any], z: float) -> float:
    return float(res["half_width_one_sided"]) if z == Z1 else z * float(res["se"])


def score_target(
    rows: dict[str, np.ndarray], target: str, floor: float, z: float = Z1
) -> dict[str, Any]:
    """The frozen arithmetic for one target: S minus RECENT (and S minus INDEP for the combos) on the exact integer CRPS, both clusterings, G1,
    the slice guard and the later-half condition. BH and the integrity gates are applied by ``score_live`` / ``decide_target``."""
    m = eligible(rows)
    n = int(m.sum())
    out: dict[str, Any] = {"target": target, "n": n, "floor": floor, "z": z}
    if n < 2:
        return out
    y = rows["y"][m]
    c_s, c_r = crps_rows(rows["s"][m], y), crps_rows(rows["recent"][m], y)
    delta = c_s - c_r
    gid, day = rows["gid"][m], rows["day"][m].astype(str)
    out.update(
        crps_s=float(c_s.mean()), crps_recent=float(c_r.mean()),
        game=one_sided(delta, gid), date=one_sided(delta, day), n_games=int(len(np.unique(gid))), n_dates=int(len(np.unique(day))),
    )  # fmt: skip
    out["g1"] = combo_g1.g1(rows["s"][m], y, rows["day"][m].astype(str), gid, rows["pid"][m])
    out["slice_guard"] = combo_g1.slice_guard(delta, slice_masks(rows, m), floor)
    o = np.lexsort(
        (rows["pid"][m], gid, day)
    )  # later half by date: the second half of the rows in the fixed order
    late = delta[o][n // 2 :]
    out["later_half"] = {
        "n": int(len(late)),
        "delta": float(late.mean()),
        "ok": bool(late.mean() <= -floor),
    }
    if m.any() and rows["has_indep"][m].all():
        d_i = c_s - crps_rows(rows["indep"][m], y)
        out["s_minus_indep"] = one_sided(d_i, gid)
        out["crps_indep"] = float(crps_rows(rows["indep"][m], y).mean())
    return out


def decide_target(
    res: dict[str, Any], bh_p: float | None, gates_ok: bool, look: str, n_min: int = N_MIN
) -> dict[str, Any]:
    """Rule 7 for one target. ``look``: interim | final | second. The interim look can only KILL; the final and second looks PASS, FAIL or are INCONCLUSIVE."""
    t, n = res["target"], res.get("n", 0)
    z = Z2 if look == "second" else Z1
    if res.get("game", {}).get("delta") is None:
        return {"target": t, "look": look, "n": n, "verdict": "NO_DATA"}
    g, d = res["game"], res["date"]
    if look == "interim":
        kill = bool(g["delta"] > 0 and g["lower95"] > 0)
        return {
            "target": t,
            "look": look,
            "n": n,
            "verdict": "KILL" if (kill or not gates_ok) else "CONTINUE",
            "integrity_kill": not gates_ok,
        }
    floor = res["floor"]
    conds = {
        "a_floor": bool(g["delta"] <= -floor),
        "b_upper_game_and_date": bool(_upper(g, z) < 0 and _upper(d, z) < 0),
        "c_bh": bool(bh_p is not None and bh_p <= BH_Q),
        "d_g1": bool(res["g1"]["ok"]),
        "e_slices": bool(res["slice_guard"]["ok"]),
        "f_later_half": bool(res["later_half"]["ok"]),
        "h_integrity": bool(gates_ok),
    }
    if t in INDEP_REQUIRED:
        si = res.get("s_minus_indep")
        conds["g_indep"] = bool(
            si is not None and si.get("delta") is not None and _upper(si, z) < 0
        )
    if n < n_min:
        return {
            "target": t,
            "look": look,
            "n": n,
            "verdict": "INCONCLUSIVE",
            "reason": f"n {n} < n_min {n_min}",
            "conditions": conds,
        }
    if all(conds.values()):
        verdict = "PASS"
    elif max(_half(g, z), _half(d, z)) <= floor:
        verdict = "FAIL"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "target": t, "look": look, "n": n, "verdict": verdict, "conditions": conds,
        "half_widths": {"game": _half(g, z), "date": _half(d, z)},
    }  # fmt: skip


def score_live(
    con: duckdb.DuckDBPyConnection,
    look: str,
    season: int = SEASON,
    lead: int = LEAD_MINUTES,
    cfg: ComboConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    z = Z2 if look == "second" else Z1
    rows = {t: load_live(con, t, season, lead) for t in TARGETS}
    res: dict[str, Any] = {
        "look": look,
        "targets": {t: score_target(rows[t], t, cfg.floors[t], z) for t in TARGETS},
    }
    ps = np.array(
        [res["targets"][t].get("game", {}).get("p_less", 1.0) for t in TARGETS], dtype=float
    )
    adj = bh_adjust(
        ps
    )  # family F = the seven targets, game-clustered one-sided p of S minus RECENT, m = 7
    serv = servability(con, season, lead)
    cov = coverage_and_missingness(con, serv, rows)
    gates = integrity_gates(con, rows, cfg, season)
    res["bh_adjusted_p"] = {t: float(a) for t, a in zip(TARGETS, adj, strict=True)}
    res["servability"] = {k: v for k, v in serv.items() if k != "rows"}
    res["coverage"], res["integrity"] = cov["by_target"], gates
    res["missingness"] = cov["missingness"]
    gate_ok = (
        gates["rows_after_tip"] == 0 and gates["tip_source_not_real"] == 0 and gates["repro_q19_fail"] == 0
        and gates["id_mismatch"] == 0 and gates["stale_ok"]
    )  # fmt: skip
    res["decisions"] = {
        t: decide_target(
            res["targets"][t],
            float(adj[i]),
            bool(gate_ok and cov["by_target"].get(t, {}).get("ok", False)),
            look,
        )
        for i, t in enumerate(TARGETS)
    }
    return res


# --------------------------------------------------------------------------- when a look is due
def sundays(start: date, end: date) -> list[date]:
    d = start + timedelta(days=(6 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def look_due(
    today: date,
    n_by_sunday: dict[str, dict[date, int]],
    last_regular_sunday: date,
    history: dict[str, list[str]] | None = None,
) -> dict[str, str | None]:
    """Which look, if any, is due TODAY for each target (``interim`` | ``final`` | ``second`` | None). Fixed calendar dates; counts decide only whether
    a Sunday qualifies. interim: Sun 2026-11-15 if EVERY target has >= 1,500 rows, else the first Sunday after that is true; final: the first Sunday on
    or after 2027-01-10 with the target at >= 3,000 rows (else the end-of-season look on the last regular-season Sunday, no second look); second:
    once, after an INCONCLUSIVE final, at the end of the regular season."""
    history = history or {}
    out: dict[str, str | None] = dict.fromkeys(TARGETS)
    if today.weekday() != 6:
        return out
    every_interim = all(n_by_sunday.get(t, {}).get(today, 0) >= N_INTERIM for t in TARGETS)
    for t in TARGETS:
        h = history.get(t, [])
        n = n_by_sunday.get(t, {}).get(today, 0)
        if "interim" not in h and "final" not in h and today >= INTERIM_DATE and every_interim:
            out[t] = "interim"
        elif (
            "final" not in h
            and today >= FINAL_DATE
            and (n >= N_MIN or today >= last_regular_sunday)
        ):
            out[t] = "final"
        elif "final_inconclusive" in h and "second" not in h and today >= last_regular_sunday:
            out[t] = "second"
    return out


def n_by_sunday(
    con: duckdb.DuckDBPyConnection, first: date, last: date, season: int = SEASON
) -> dict[str, dict[date, int]]:
    """Eligible on-time rows per target settled by each Sunday (cumulative); counts only, no delta."""
    out: dict[str, dict[date, int]] = {}
    for t in TARGETS:
        rows = load_live(con, t, season)
        days = rows["day"][eligible(rows)]
        out[t] = {s: int((days <= np.datetime64(s)).sum()) for s in sundays(first, last)}
    return out


def daily_display(con: duckdb.DuckDBPyConnection, season: int = SEASON) -> dict[str, Any]:
    """Counts and gates only (n per target, coverage, stamp and integrity gates); deltas from non-look days are not published."""
    rows = {t: load_live(con, t, season) for t in TARGETS}
    serv = servability(con, season)
    cov = coverage_and_missingness(con, serv, rows)
    g = integrity_gates(con, rows, None, season)
    return {
        "n_eligible": {t: int(eligible(rows[t]).sum()) for t in TARGETS},
        "servability": {k: v for k, v in serv.items() if k != "rows"},
        "coverage": cov["by_target"], "missingness": cov["missingness"], "gates": g,
    }  # fmt: skip


def read_look(
    con: duckdb.DuckDBPyConnection,
    today: date,
    last_regular_sunday: date,
    history: dict[str, list[str]] | None = None,
    season: int = SEASON,
) -> dict[str, Any]:
    """Compute a look ONLY if one is due today; otherwise raise ``LookNotDue`` before touching a single score."""
    counts = (
        n_by_sunday(con, INTERIM_DATE, max(last_regular_sunday, today), season)
        if today.weekday() == 6
        else {}
    )
    due = look_due(today, counts, last_regular_sunday, history)
    looks = sorted({v for v in due.values() if v})
    if not looks:
        raise LookNotDue(
            f"no look is due on {today} (calendar: interim {INTERIM_DATE}, final {FINAL_DATE}); daily_display() only"
        )
    res = {look: score_live(con, look, season) for look in looks}
    for look, r in res.items():
        r["due_targets"] = [t for t, v in due.items() if v == look]
    return {"due": due, "looks": res}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--last-regular-sunday", type=date.fromisoformat, default=date(2027, 4, 11))
    ap.add_argument("--history", default="", help='JSON, e.g. {"blk": ["interim"]}')
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    con = duckdb.connect(a.db, read_only=True)
    try:
        res = read_look(
            con, a.today, a.last_regular_sunday, json.loads(a.history) if a.history else None
        )
    except LookNotDue as e:
        print(
            json.dumps(
                {"look": None, "message": str(e), "display": daily_display(con)},
                indent=1,
                default=str,
            )
        )
        return 0
    finally:
        con.close()
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt)
    print(
        json.dumps(
            {
                k: {t: d["verdict"] for t, d in v["decisions"].items()}
                for k, v in res["looks"].items()
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
