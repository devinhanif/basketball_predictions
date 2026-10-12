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
    se_cl = math.sqrt(g / (g - 1) * float((s**2).sum())) / n
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
