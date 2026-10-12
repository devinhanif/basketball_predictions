# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc"
"""PPLAY_INTO_PROPS_LIVE (docs/prereg/PPLAY_INTO_PROPS_LIVE.md; DRAFT until the main session commits it).

Scorer for the live 2026-27 forward log: unconditional integer CRPS of the production props with production P(play) vs the same
conditional distribution with ``p_play_r``. ``rehearse`` runs the same arithmetic on 2024 walk-forward rows (DESCRIPTIVE, a code-path
proof and a power calculation; never a verdict). It reads season <= 2024 only, nba.duckdb read-only.

    .venv/bin/python -W ignore -m research.eval.pplay_into_props_live rehearse
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from research.eval.recent_role_props import uncond_crps

OUT = Path("reports/prereg_pplay_into_props_live")
STATS = ("pts", "reb", "ast", "fg3m")
HALF_WIDTH = 0.005  # target 95% half-width of the clustered CI (the house floor)
Z = 1.959964
ROWS_PER_GAME = (
    25.0  # eligible player-rows per game, both teams (2024 Cell-P: 33,127 / 1,320 = 25.1)
)
GAMES_PER_WEEK = 49.0
BURN_WEEKS = (
    2.0  # n_played_prior >= 5 empties most of the first two weeks (assumption, stated in the rule)
)


def row_delta(qi: np.ndarray, p_a: np.ndarray, p_b: np.ndarray, y_u: np.ndarray) -> np.ndarray:
    """Per-row unconditional CRPS(arm a) - CRPS(arm b) on the SAME integer quantile points (only p differs)."""
    ca = uncond_crps(qi, np.clip(p_a, 1e-6, 1 - 1e-6), y_u)
    cb = uncond_crps(qi, np.clip(p_b, 1e-6, 1 - 1e-6), y_u)
    return np.asarray(ca - cb)


def prev_row_p(player: np.ndarray, order_key: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Past-only placebo p: the same player's previous row's p (strictly earlier in ``order_key``); first rows keep their own p
    (counted by the caller; they cannot differ from the arm so they only dilute)."""
    idx = np.lexsort((order_key, player))
    out = p.copy()
    ps, pp = player[idx], p[idx]
    same = np.r_[False, ps[1:] == ps[:-1]]
    prev = np.r_[pp[:1], pp[:-1]]
    out[idx] = np.where(same, prev, pp)
    return out


def shuffle_p_within_day(p: np.ndarray, day: np.ndarray, seed: int = 0) -> np.ndarray:
    """Non-inert shuffle: permute p across the rows of one calendar day (the distribution of p stays; the match to the player goes)."""
    rng = np.random.default_rng(seed)
    out = p.copy()
    for d in np.unique(day):
        ix = np.flatnonzero(day == d)
        out[ix] = p[rng.permutation(ix)]
    return out


def cluster_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    """Row SD, game-cluster design effect and the clustered standard error of the mean (ratio-estimator linearisation)."""
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    n = len(delta)
    mu = float(delta.mean())
    r = delta - mu
    s = np.bincount(inv, weights=r, minlength=g)
    se_cl = math.sqrt(g / (g - 1) * float((s**2).sum())) / n if g > 1 else float("nan")
    sd = float(delta.std(ddof=1))
    se_iid = sd / math.sqrt(n)
    return {
        "mean": mu,
        "sd_row": sd,
        "se_cluster": se_cl,
        "se_iid": se_iid,
        "deff": (se_cl / se_iid) ** 2,
        "n": n,
        "games": g,
    }


def n_required(sd_row: float, deff: float, half: float = HALF_WIDTH) -> int:
    """Rows needed for a 95% clustered half-width <= ``half``: (Z * sd * sqrt(deff) / half)^2."""
    return int(math.ceil((Z * sd_row * math.sqrt(deff) / half) ** 2))


def week_reached(
    n_rows: int,
    rows_per_game: float = ROWS_PER_GAME,
    games_per_week: float = GAMES_PER_WEEK,
    burn_weeks: float = BURN_WEEKS,
) -> float:
    """Season week (1 = opening week, 2026-10-20) at which ``n_rows`` eligible rows have accumulated, after the burn-in weeks."""
    return burn_weeks + n_rows / (rows_per_game * games_per_week)


def week_reached_piecewise(
    n_rows: int, segments: list[tuple[float, float]], burn_weeks: float = BURN_WEEKS
) -> float:
    """Week at which ``n_rows`` accumulate when rows/week changes: ``segments`` = [(weeks_in_segment, rows_per_week), ...,
    (inf, rows_per_week)], counted after ``burn_weeks`` of no rows."""
    t, left = burn_weeks, float(n_rows)
    for w, rpw in segments:
        if left <= w * rpw:
            return t + left / rpw
        t, left = t + w, left - w * rpw
    return float("inf")


LIVE_SQL = """
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_pplay_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, e.tipoff, e.made_at, e.player_id, e.team_id, e.p_play_prod, e.p_play_r, e.proj_minutes,
       e.in_prod_log, e.props_cond, s.y AS played,
       b.pts, b.reb, b.ast, b.fg3m,
       EXISTS (SELECT 1 FROM games g2 WHERE g2.game_date = g.game_date - 1
               AND (g2.home_team = e.team_id OR g2.away_team = e.team_id)) AS second_night
FROM elig e
JOIN forward_pplay_scores s ON s.game_id = e.game_id AND s.player_id = e.player_id AND s.run_id = e.run_id AND s.status = 'scored'
JOIN games g ON g.game_id = e.game_id
LEFT JOIN player_game_stats b ON b.game_id = e.game_id AND b.player_id = e.player_id
WHERE e.rn = 1 AND e.p_play_r IS NOT NULL AND e.p_play_r > 0 AND e.p_play_r < 1
"""

BENCH_MINUTES = (
    20.0  # bench slice = production proj_minutes < 20 (the shadow table has no starter rate)
)
LOW_P = 0.9  # low-availability slice = production p_play < 0.9 (stands in for "questionable": the shadow table stores no report tag)


def load_live(con: Any, stat: str, lead_minutes: int = 60) -> dict[str, np.ndarray]:
    """Eligible settled rows for ``stat`` from ``forward_pplay_shadow`` + ``forward_pplay_scores`` (read-only connection):
    the latest shadow row per (game, player) with made_at <= tipoff - ``lead_minutes``, non-NULL ``p_play_r`` in (0,1), a settled
    label, and a 19-point ``props_cond[stat].q_grid``. The continuous conditional quantiles become integer support with
    ``to_integer_support`` (ceil(q - 0.5), floored at 0, running max), exactly as the stored 2023-24 components were scored.
    y_u = box-score stat if the player played else 0. Rows with a missing grid or a played row without a box stat are counted, not scored."""
    from nba.props.context_residual import to_integer_support

    rows = con.execute(LIVE_SQL, [lead_minutes]).fetchall()
    cols = [
        "gid",
        "day",
        "tip",
        "made_at",
        "pid",
        "team",
        "p_prod",
        "p_r",
        "proj",
        "logged",
        "props",
        "played",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "b2b",
    ]
    keep: dict[str, list[Any]] = {c: [] for c in cols}
    qis: list[np.ndarray] = []
    dropped = 0
    for r in rows:
        d = dict(zip(cols, r, strict=True))
        props = json.loads(d["props"]) if isinstance(d["props"], str) else d["props"]
        grid = (props or {}).get(stat, {}).get("q_grid") if isinstance(props, dict) else None
        val = d[stat]
        if grid is None or len(grid) != 19 or (d["played"] > 0.5 and val is None):
            dropped += 1
            continue
        for c in cols:
            keep[c].append(d[c])
        keep.setdefault("stat_val", []).append(float(val) if d["played"] > 0.5 else 0.0)
        qis.append(to_integer_support(np.asarray(grid, dtype=float)))
    out: dict[str, np.ndarray] = {
        "gid": np.array(keep["gid"], dtype=str), "day": np.array(keep["day"], dtype="datetime64[D]"),
        "pid": np.array(keep["pid"], dtype=np.int64), "p_prod": np.array(keep["p_prod"], dtype=float), "p_r": np.array(keep["p_r"], dtype=float),
        "proj": np.array([np.nan if v is None else v for v in keep["proj"]], dtype=float), "y_u": np.array(keep.get("stat_val", []), dtype=float),
        "b2b": np.array(keep["b2b"], dtype=bool), "made_at": np.array(keep["made_at"], dtype="datetime64[s]"), "tip": np.array(keep["tip"], dtype="datetime64[s]"),
        "qi": np.array(qis, dtype=np.int16).reshape(len(qis), 19), "dropped": np.array([dropped]),
    }  # fmt: skip
    return out


def stamp_and_shadow_gates(con: Any, lead_minutes: int = 60) -> dict[str, Any]:
    """Counts for the integrity gates: (game, player) pairs whose every shadow row was made after the T-minus cutoff (excluded),
    shadow rows after tip (must be 0: the writer refuses them), and the production-p equality check against ``forward_predictions``
    rows of the same run (``prediction->>'$.p_play'``); 0 compared means the check could not be made."""
    late = con.execute(
        "SELECT count(*) FROM (SELECT game_id, player_id FROM forward_pplay_shadow GROUP BY 1, 2 HAVING min(made_at) > min(tipoff) - ? * INTERVAL 1 MINUTE)",
        [lead_minutes],
    ).fetchone()[0]
    after_tip = con.execute(
        "SELECT count(*) FROM forward_pplay_shadow WHERE made_at >= tipoff"
    ).fetchone()[0]
    total = con.execute("SELECT count(*) FROM forward_pplay_shadow").fetchone()[0]
    with_r = con.execute(
        "SELECT count(*) FROM forward_pplay_shadow WHERE p_play_r IS NOT NULL"
    ).fetchone()[0]
    cmp_ = con.execute(
        """SELECT count(*), coalesce(sum(CASE WHEN abs(s.p_play_prod - CAST(p.pp AS DOUBLE)) > 1e-12 THEN 1 ELSE 0 END), 0)
           FROM forward_pplay_shadow s JOIN (SELECT DISTINCT run_id, game_id, player_id, prediction ->> '$.p_play' AS pp FROM forward_predictions
                                              WHERE prediction ->> '$.p_play' IS NOT NULL) p
             ON p.run_id = s.run_id AND p.game_id = s.game_id AND p.player_id = s.player_id
           WHERE s.in_prod_log"""
    ).fetchone()
    return {"late_only_pairs": int(late), "rows_after_tip": int(after_tip), "shadow_rows": int(total), "rows_with_p_r": int(with_r),
            "prod_p_compared": int(cmp_[0]), "prod_p_mismatch": int(cmp_[1])}  # fmt: skip


def score_live(rows: dict[str, np.ndarray], n_boot_seed: int = 0) -> dict[str, Any]:
    """Primary + guards for one stat from ``load_live`` output. Pure arithmetic; the verdict logic (PASS / INCONCLUSIVE / FAIL, BH over 4)
    is applied by the caller per the frozen rule."""
    from research.eval import workload_eval as WE

    qi, y, gid = rows["qi"], rows["y_u"], rows["gid"]
    if len(y) == 0:
        return {"n_rows": 0}
    days = rows["day"].astype("int64")
    order_key = rows["made_at"].astype("int64")
    d_main = row_delta(qi, rows["p_r"], rows["p_prod"], y)
    d_pl = row_delta(qi, rows["p_r"], prev_row_p(rows["pid"], order_key, rows["p_r"]), y)
    d_sh = row_delta(qi, shuffle_p_within_day(rows["p_r"], days, n_boot_seed), rows["p_prod"], y)
    out: dict[str, Any] = {
        "n_rows": int(len(y)), "n_games": int(len(np.unique(gid))), "dropped_no_grid_or_box": int(rows["dropped"][0]),
        "R_minus_prod": WE.delta_stats(d_main, gid), "R_minus_prev_p_placebo": WE.delta_stats(d_pl, gid), "shuffled_R_minus_prod": WE.delta_stats(d_sh, gid),
        "cluster": cluster_stats(d_main, gid),
    }  # fmt: skip
    for name, m in (
        ("bench", rows["proj"] < BENCH_MINUTES),
        ("low_p", rows["p_prod"] < LOW_P),
        ("second_night", rows["b2b"]),
    ):
        out[name] = {
            "n": int(m.sum()),
            **(
                WE.delta_stats(d_main[m], gid[m])
                if m.sum() >= 300
                else {"note": "under 300 rows: reported, cannot fail the guard"}
            ),
        }
    return out


def rehearse() -> dict[str, Any]:
    import polars as pl

    from research.eval import recent_role_eval as RR
    from research.eval import workload_eval as WE

    fr, _, _ = RR.build_frame()
    sc = dict(np.load(RR.CACHE / "scores.npz", allow_pickle=True))
    ps = pl.DataFrame(
        {
            "game_id": sc["gid"],
            "player_id": sc["pid"].astype(np.int64),
            "p_R": sc["p_R"],
            "p_PL": sc["p_PL"],
        }
    )
    q = (
        fr.filter(pl.col("season") == 2024)
        .select(
            "game_id",
            "player_id",
            "game_date",
            "played",
            "p_raw",
            "p_platt",
            "starter10",
            "is_b2b_second",
        )
        .join(ps, on=RR.KEYS, how="inner")
    )
    q = q.with_columns(pl.col("player_id").cast(pl.Int64))
    pic = WE.load_pic()
    res: dict[str, Any] = {
        "label": "REHEARSAL, descriptive, season 2024 walk-forward; not a verdict",
        "stats": {},
    }
    for s in STATS:
        a0 = WE.a0_arm(s, pic)
        m = a0["season"] == 2024
        A = pl.DataFrame(
            {
                "game_id": a0["gid"][m],
                "player_id": a0["pid"][m],
                "y": a0["y"][m],
                "_k": np.arange(int(m.sum())),
            }
        )
        qi_all = a0["qi"][m]
        pl_rows = q.join(A, on=RR.KEYS, how="left")
        played = pl_rows.filter(pl.col("_k").is_not_null())
        dnp = pl_rows.filter(pl.col("_k").is_null() & (pl.col("played") < 0.5))
        # DNP rows have no stored conditional distribution (components are played rows): proxy = that player's most recent earlier played row's quantiles.
        ref = played.select("player_id", pl.col("game_date").alias("ref_date"), "_k").sort(
            "ref_date"
        )
        dn = dnp.sort("game_date").join_asof(
            ref,
            left_on="game_date",
            right_on="ref_date",
            by="player_id",
            strategy="backward",
            allow_exact_matches=False,
        )
        dn = dn.filter(pl.col("_k_right").is_not_null())
        qi = np.concatenate([qi_all[played["_k"].to_numpy()], qi_all[dn["_k_right"].to_numpy()]])
        gid = np.concatenate([played["game_id"].to_numpy(), dn["game_id"].to_numpy()]).astype(str)
        pid = np.concatenate([played["player_id"].to_numpy(), dn["player_id"].to_numpy()])
        dt = np.concatenate([played["game_date"].to_numpy(), dn["game_date"].to_numpy()])
        y_u = np.concatenate([played["y"].to_numpy().astype(float), np.zeros(dn.height)])
        p_prod = np.concatenate([played["p_raw"].to_numpy(), dn["p_raw"].to_numpy()])
        p_pl = np.concatenate([played["p_platt"].to_numpy(), dn["p_platt"].to_numpy()])
        p_r = np.concatenate([played["p_R"].to_numpy(), dn["p_R"].to_numpy()])
        bench = np.concatenate([played["starter10"].to_numpy(), dn["starter10"].to_numpy()]) < 0.5
        b2b = np.concatenate(
            [played["is_b2b_second"].to_numpy(), dn["is_b2b_second"].to_numpy()]
        ).astype(bool)
        d_main = row_delta(qi, p_r, p_prod, y_u)
        d_platt = row_delta(
            qi, p_r, p_pl, y_u
        )  # fair-baseline view: R vs a calibrated production p (what the raw map lacks)
        d_prod_platt = row_delta(qi, p_pl, p_prod, y_u)
        d_pl = row_delta(qi, p_r, prev_row_p(pid, dt, p_r), y_u)
        d_sh = row_delta(qi, shuffle_p_within_day(p_r, dt, 0), p_prod, y_u)
        cs = cluster_stats(d_main, gid)
        n_need = n_required(cs["sd_row"], cs["deff"])
        el = (
            p_prod >= 0.5
        )  # the live log only holds rows with production raw p_play >= 0.5 (ForwardConfig.min_p_play): the real population
        cs_el = cluster_stats(d_main[el], gid[el])
        n_need_el = n_required(cs_el["sd_row"], cs_el["deff"])
        res["stats"][s] = {
            "n_rows": int(len(d_main)),
            "n_played_rows": int(played.height),
            "n_dnp_proxy_rows": int(dn.height),
            "n_games": int(cs["games"]),
            "crps_prod": float(uncond_crps(qi, np.clip(p_prod, 1e-6, 1 - 1e-6), y_u).mean()),
            "R_minus_prod": WE.delta_stats(d_main, gid),
            "R_minus_platt_prod": WE.delta_stats(d_platt, gid),
            "platt_prod_minus_prod": WE.delta_stats(d_prod_platt, gid),
            "R_minus_prev_p_placebo": WE.delta_stats(d_pl, gid),
            "shuffled_R_minus_prod": WE.delta_stats(d_sh, gid),
            "bench": {**WE.delta_stats(d_main[bench], gid[bench]), "n": int(bench.sum())},
            "b2b_second": {**WE.delta_stats(d_main[b2b], gid[b2b]), "n": int(b2b.sum())},
            "sd_row": cs["sd_row"],
            "deff": cs["deff"],
            "n_required_rows": n_need,
            "week_reached": week_reached(n_need),
            "live_eligible_p_raw_ge_0.5": {
                "n_rows": int(el.sum()),
                "rows_per_game": float(el.sum() / cs["games"]),
                **WE.delta_stats(d_main[el], gid[el]),
                "sd_row": cs_el["sd_row"],
                "deff": cs_el["deff"],
                "n_required_rows": n_need_el,
                "week_reached": week_reached(n_need_el, float(el.sum() / cs["games"])),
                "n_games_for_season": int(cs["games"]),
            },
        }
        print(
            s,
            json.dumps(
                {
                    k: v
                    for k, v in res["stats"][s].items()
                    if k
                    in (
                        "n_rows",
                        "R_minus_prod",
                        "sd_row",
                        "deff",
                        "n_required_rows",
                        "week_reached",
                        "live_eligible_p_raw_ge_0.5",
                    )
                },
                default=float,
            ),
            flush=True,
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rehearsal.json").write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["rehearse"]:
        raise SystemExit("usage: python -m research.eval.pplay_into_props_live rehearse")
    rehearse()
