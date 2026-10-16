# ruff: noqa: E501
"""WOWY_STRUCT, arm STRUCT_REPL: teammates' on/off per possession, measured ABOVE a replacement-level fill-in.

Candidate columns for docs/prereg/WOWY_STRUCT.md (DRAFT). Devin's twist (2026-10-15): when a star sits, his possessions go
to a fill-in. A teammate's RAW on/off delta mixes "teammates absorb the star's usage" with "the fill-in produces at
replacement level". Each teammate's on/off delta is therefore set against the change expected if a REPLACEMENT-level player
took the star's possessions, and the noisy per-pair history is shrunk toward that expectation instead of toward zero.

For a scored player i (played tonight), tonight's OUT rotation player j (the report, production's OUT set), stat s in
{pts, ast}, everything as-of the game date g (strictly earlier box rows only):

* ``d_ij(s)`` = i's stat per possession on the floor in his past games WITHOUT j minus WITH j (``wowy.py`` window: the
  team's previous ``WINDOW_GAMES`` games, j's current stint with the team, only games i played), possessions on the floor from
  the box (``repl_delta.prep_box``). ``lam_ij = n_wo / (n_wo + SHRINK_K)`` (``wowy.py``'s weight, unchanged: no new tuning),
  zero when either side of the pair is empty.
* ``E_ij(s)`` = the change in i's per-possession rate expected if a replacement-level fill-in takes j's possessions and the
  vacated usage ``(u_j - u_repl) x P_j`` goes to the remaining pool by usage weight (``repl_delta``'s pool and weights, as-of
  empirical-Bayes rates and the fringe replacement rate): ``E_ij(s) = r_is x V_j / den``, ``V_j = (u_j - u_repl) P_j``,
  ``den`` = the pool's usage-weight sum (plus i's own when he is not in the pool).
* Columns (stat units per game; x ``P_exp_i``, i's expected possessions = production ``min10`` x the team's as-of pace):
  ``rb_s = sum_j E_ij P_exp_i`` (the replacement-fill-in baseline, no own history),
  ``rx_s = sum_j lam_ij (d_ij - E_ij) P_exp_i`` (the on/off EXCESS above the replacement baseline),
  ``rr_s = sum_j lam_ij d_ij P_exp_i`` (the RAW on/off, the un-normalised twin; ``rr = rx + sum lam E``), ``ro_conf = sum_j lam_ij``.
* NULL rule (production's): no usable report -> NULL, never "nobody out"; reported with nobody rotation-OUT -> explicit 0.

Placebo (identity): each OUT player's identity is replaced by a uniformly drawn same-team as-of pool member who is NOT out
tonight (his own history, rates and usage), keeping the OUT player's expected possessions; same code path, columns ``pq_*``.
Deterministic per (seed, game, team).
"""

from __future__ import annotations

import zlib
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import numpy as np
import polars as pl

from nba.props import repl_delta as RD
from nba.props import wowy as W

STATS = ("pts", "ast")
WINDOW_GAMES = W.WINDOW_GAMES
SHRINK_K = W.SHRINK_K
QUANTS = ("P", "pts", "ast")
COLUMNS: tuple[str, ...] = (
    *(f"rb_{s}" for s in STATS),
    *(f"rx_{s}" for s in STATS),
    *(f"rr_{s}" for s in STATS),
    "ro_conf",
)
PLACEBO_COLUMNS: tuple[str, ...] = tuple(f"pq_{c}" for c in COLUMNS)
PAIR_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "team_id": pl.Int64,
    "player_id": pl.Int64,
    "j": pl.Int64,
    "n_wo": pl.Float64,
    "n_wi": pl.Float64,
    **{f"d_{s}": pl.Float64 for s in STATS},
}


class _Cube:
    """One team's global game order and a (game x player x quantity) cube of P, pts, ast for played rows."""

    def __init__(self, games: list[tuple[str, Any]], rows: pl.DataFrame) -> None:
        self.gid = [g for g, _ in games]
        self.date = [d for _, d in games]
        self.k_of = {g: k for k, g in enumerate(self.gid)}
        pids = sorted({int(p) for p in rows["player_id"].to_list()})
        self.pix = {p: i for i, p in enumerate(pids)}
        self.played = np.zeros((len(games), len(pids)), dtype=bool)
        self.cube = np.zeros((len(games), len(pids), len(QUANTS)))
        gk = np.array([self.k_of[str(g)] for g in rows["game_id"].to_list()])
        pk = np.array([self.pix[int(p)] for p in rows["player_id"].to_list()])
        self.played[gk, pk] = True
        for qi, q in enumerate(QUANTS):
            self.cube[gk, pk, qi] = rows[q].cast(pl.Float64).fill_null(0.0).to_numpy()


def pair_history(
    b: pl.DataFrame,
    out_pairs: pl.DataFrame,
    scored: pl.DataFrame,
    window: int = WINDOW_GAMES,
) -> pl.DataFrame:
    """On/off history for every (game, team, i, j): ``scored`` (game_id, team_id, player_id) x ``out_pairs`` (game_id, team_id, j).

    ``b``: prepped box of played rows (``repl_delta.prep_box``: ``P``, ``pts``, ``ast``, ``game_date``). Reads only box rows of
    games strictly before g in the team's order; j needs a current stint with the team (``wowy.py``'s rule). Returns
    ``PAIR_SCHEMA`` rows for pairs with at least one game without and one with j (others carry no history)."""
    bb = b.with_columns(pl.col("game_id").cast(pl.Utf8))
    apps = (
        bb.sort(["player_id", "game_date", "game_id"])
        .select("player_id", "team_id", "game_date", "game_id")
        .rows()
    )
    p_dates: dict[int, list[Any]] = defaultdict(list)
    p_teams: dict[int, list[int]] = defaultdict(list)
    p_games: dict[int, list[str]] = defaultdict(list)
    for pid, tid, d, g in apps:
        p_dates[int(pid)].append(d)
        p_teams[int(pid)].append(int(tid))
        p_games[int(pid)].append(str(g))
    p_run = {p: W._run_starts(t) for p, t in p_teams.items()}
    outs: dict[tuple[str, int], list[int]] = defaultdict(list)
    for g, t, j in out_pairs.select("game_id", "team_id", "j").iter_rows():
        outs[(str(g), int(t))].append(int(j))
    tr: dict[tuple[str, int], list[int]] = defaultdict(list)
    for g, t, i in scored.select("game_id", "team_id", "player_id").iter_rows():
        tr[(str(g), int(t))].append(int(i))
    rows: list[tuple[Any, ...]] = []
    for tid_raw, grp in bb.group_by("team_id", maintain_order=True):
        tid = int(tid_raw[0] if isinstance(tid_raw, tuple) else tid_raw)
        gl = grp.select("game_id", "game_date").unique().sort(["game_date", "game_id"]).rows()
        if not any(outs.get((str(g), tid)) for g, _ in gl):
            continue
        th = _Cube([(str(g), d) for g, d in gl], grp)
        for g, gdate in zip(th.gid, th.date, strict=True):
            js = outs.get((g, tid))
            if not js:
                continue
            kg = th.k_of[g]
            lo = max(0, kg - window)
            stints: dict[int, int] = {}
            for j in js:
                ds = p_dates.get(j)
                if not ds:
                    continue
                pos = bisect_left(ds, gdate) - 1  # last appearance strictly before g's date
                if pos < 0 or p_teams[j][pos] != tid:
                    continue
                s0 = th.k_of.get(p_games[j][p_run[j][pos]])
                if s0 is not None and j in th.pix:
                    stints[j] = s0
            for i in tr.get((g, tid), []):
                if i not in th.pix:
                    continue
                ii = th.pix[i]
                for j, s0 in stints.items():
                    if j == i:
                        continue
                    a = max(lo, s0)
                    if a >= kg:
                        continue
                    pi_ = th.played[a:kg, ii]
                    pj_ = th.played[a:kg, th.pix[j]]
                    wo, wi = pi_ & ~pj_, pi_ & pj_
                    n_wo, n_wi = int(wo.sum()), int(wi.sum())
                    if n_wo == 0 or n_wi == 0:
                        continue
                    cube = th.cube[a:kg, ii, :]
                    s_wo, s_wi = cube[wo].sum(axis=0), cube[wi].sum(axis=0)
                    if s_wo[0] <= 0 or s_wi[0] <= 0:
                        continue
                    d = [s_wo[1 + c] / s_wo[0] - s_wi[1 + c] / s_wi[0] for c in range(len(STATS))]
                    rows.append((g, tid, i, j, float(n_wo), float(n_wi), *map(float, d)))
    return pl.DataFrame(rows, schema=PAIR_SCHEMA, orient="row")


def _rng_for(seed: int, game_id: str, team_id: int) -> np.random.Generator:
    """Deterministic per (seed, game, team), independent of row order."""
    return np.random.default_rng([seed, zlib.crc32(f"{game_id}|{team_id}".encode())])


def placebo_out(o: pl.DataFrame, pk: pl.DataFrame, seed: int) -> pl.DataFrame:
    """Identity placebo: per (game, team) replace each OUT rotation player by a uniformly drawn not-out pool member (``pk``
    carries ``r_usage``/``rep_usage``), keeping the OUT player's expected possessions. Returns (game_id, team_id, j, P_j, V_j)."""
    cols = {
        "game_id": pl.Utf8,
        "team_id": pl.Int64,
        "j": pl.Int64,
        "P_j": pl.Float64,
        "V_j": pl.Float64,
    }
    if o.is_empty():
        return pl.DataFrame(schema=cols)
    pool_by = {
        key: grp
        for key, grp in pk.sort(["game_id", "team_id", "player_id"]).group_by(
            ["game_id", "team_id"], maintain_order=True
        )
    }
    rec: list[tuple[Any, ...]] = []
    for (gid, tid), grp in o.sort(["game_id", "team_id", "player_id"]).group_by(
        ["game_id", "team_id"], maintain_order=True
    ):
        cand = pool_by.get((gid, tid))
        if cand is None or cand.height == 0:
            continue
        n = grp.height
        pick = _rng_for(seed, str(gid), int(tid)).choice(
            cand.height, size=n, replace=cand.height < n
        )
        ru = cand["r_usage"].to_numpy()[pick]
        pj = grp["P_exp"].to_numpy()
        rep_u = grp["rep_usage"].to_numpy()
        pid = cand["player_id"].to_numpy()[pick]
        for a in range(n):
            rec.append(
                (gid, int(tid), int(pid[a]), float(pj[a]), float((ru[a] - rep_u[a]) * pj[a]))
            )
    return pl.DataFrame(rec, schema=cols, orient="row") if rec else pl.DataFrame(schema=cols)


def _assemble(
    prefix: str,
    j_frame: pl.DataFrame,
    sc: pl.DataFrame,
    b: pl.DataFrame,
    scored_keys: pl.DataFrame,
    rep_flag: pl.Expr,
) -> pl.DataFrame:
    """The ``prefix``-ed columns for the scored rows from a j-level frame (game_id, team_id, j, V_j [may be null]).
    ``sc`` carries r_<stat>, P_exp (i's expected possessions) and ``den`` per scored row."""
    vt = j_frame.group_by(["game_id", "team_id"]).agg(
        pl.col("V_j").sum().alias("Vtot"), pl.col("V_j").is_null().sum().alias("nv")
    )
    pairs = pair_history(
        b,
        j_frame.select("game_id", "team_id", "j"),
        scored_keys,
    )
    s = sc.join(vt, on=["game_id", "team_id"], how="left")
    if pairs.height:
        pj = pairs.join(
            j_frame.select("game_id", "team_id", "j", "V_j"),
            on=["game_id", "team_id", "j"],
            how="left",
        ).join(
            sc.select(
                "game_id", "team_id", "player_id", "den", "P_exp", *[f"r_{x}" for x in STATS]
            ),
            on=["game_id", "team_id", "player_id"],
            how="left",
        )
        lam = pl.col("n_wo") / (pl.col("n_wo") + SHRINK_K)
        aggs: list[pl.Expr] = [lam.sum().alias("conf")]
        for x in STATS:
            e = pl.col(f"r_{x}") * pl.col("V_j") / pl.col("den")
            aggs.append((lam * (pl.col(f"d_{x}") - e)).sum().alias(f"sx_{x}"))
            aggs.append((lam * pl.col(f"d_{x}")).sum().alias(f"sr_{x}"))
        pa = pj.group_by(["game_id", "team_id", "player_id"]).agg(aggs)
        s = s.join(pa, on=["game_id", "team_id", "player_id"], how="left")
    else:
        s = s.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("conf"),
            *[pl.lit(None, dtype=pl.Float64).alias(f"sx_{x}") for x in STATS],
            *[pl.lit(None, dtype=pl.Float64).alias(f"sr_{x}") for x in STATS],
        )
    ok_p = pl.col("P_exp").is_not_null() & pl.col("den").is_not_null() & (pl.col("den") > 0)
    cols: list[pl.Expr] = []
    for x in STATS:
        base = pl.col(f"r_{x}") * pl.col("Vtot").fill_null(0.0) / pl.col("den") * pl.col("P_exp")
        cols.append(pl.when(rep_flag & ok_p).then(base).otherwise(None).alias(f"{prefix}rb_{x}"))
        cols.append(
            pl.when(rep_flag & ok_p)
            .then(pl.col(f"sx_{x}").fill_null(0.0) * pl.col("P_exp"))
            .otherwise(None)
            .alias(f"{prefix}rx_{x}")
        )
        cols.append(
            pl.when(rep_flag & pl.col("P_exp").is_not_null())
            .then(pl.col(f"sr_{x}").fill_null(0.0) * pl.col("P_exp"))
            .otherwise(None)
            .alias(f"{prefix}rr_{x}")
        )
    cols.append(
        pl.when(rep_flag)
        .then(pl.col("conf").fill_null(0.0))
        .otherwise(None)
        .alias(f"{prefix}ro_conf")
    )
    keep = ["game_id", "player_id", *[c.meta.output_name() for c in cols]]
    return s.with_columns(cols).select(keep)


def build_wowy_repl(
    b: pl.DataFrame,
    st: pl.DataFrame,
    out_rows: pl.DataFrame,
    out_all: pl.DataFrame,
    scored: pl.DataFrame,
    reported: set[str],
    repl: Mapping[int, Mapping[str, float]],
    k: Mapping[str, float],
    placebo_seed: int = 0,
) -> pl.DataFrame:
    """The STRUCT_REPL columns (``COLUMNS`` and ``PLACEBO_COLUMNS``) for the ``scored`` rows (game_id, team_id, player_id,
    game_date, season, one per played row). Arguments as :func:`nba.props.repl_delta.build_repl_delta` (``b`` prepped box of
    every played row of every season the caller may see; ``st`` the state table; ``out_rows`` the OUT rotation rows;
    ``out_all`` every player on the game's OUT list; ``reported`` game ids with a usable report; ``repl``/``k`` the pinned
    replacement rates and prior strengths). Row order of the result is not part of the contract."""
    cum = RD.player_cum(b)
    pace = RD.team_pace(b)
    pool = (
        RD.rotation_pool(b)
        .join(
            out_all.with_columns(pl.lit(True).alias("_o")), on=["game_id", "player_id"], how="left"
        )
        .filter(pl.col("_o").is_null())
        .drop("_o")
    )
    gm = b.select("game_id", "season", "game_date").unique("game_id")
    o = out_rows.drop("min10").join(gm.select("game_id", "season"), on="game_id", how="left")
    o = RD._rates_at(o, cum, st, pace, repl, k)
    jf = o.select(
        "game_id",
        "team_id",
        pl.col("player_id").alias("j"),
        ((pl.col("r_usage") - pl.col("rep_usage")) * pl.col("P_exp")).alias("V_j"),
    )
    pk = pool.join(gm, on="game_id", how="left")
    pk = RD._rates_at(pk, cum, st, pace, repl, k).with_columns(
        (pl.col("r_usage") * pl.col("P_exp")).alias("w")
    )
    wsum = pk.group_by(["game_id", "team_id"]).agg(pl.col("w").sum().alias("W"))
    skeys = scored.select("game_id", "team_id", "player_id", "game_date", "season")
    sc = RD._rates_at(skeys, cum, st, pace, repl, k).with_columns(
        (pl.col("r_usage") * pl.col("P_exp")).alias("w_i")
    )
    in_pool = pool.select("game_id", "team_id", "player_id").with_columns(pl.lit(1).alias("_in"))
    sc = sc.join(in_pool, on=["game_id", "team_id", "player_id"], how="left").join(
        wsum, on=["game_id", "team_id"], how="left"
    )
    sc = sc.with_columns(
        pl.when(pl.col("W").is_not_null())
        .then(pl.col("W") + pl.when(pl.col("_in").is_null()).then(pl.col("w_i")).otherwise(0.0))
        .otherwise(None)
        .alias("den")
    )
    rep_flag = pl.col("game_id").is_in(sorted(reported))
    scored_keys = skeys.select("game_id", "team_id", "player_id")
    real = _assemble("", jf, sc, b, scored_keys, rep_flag)
    pq_o = placebo_out(o, pk, placebo_seed)
    pq_j = pq_o.select("game_id", "team_id", "j", pl.col("V_j"))
    pq = _assemble("pq_", pq_j, sc, b, scored_keys, rep_flag)
    out = real.join(pq, on=["game_id", "player_id"], how="left")
    if out.height != scored.height:
        raise RuntimeError(
            f"feature rows {out.height} != scored rows {scored.height}: a join duplicated or dropped rows"
        )
    return out.select("game_id", "player_id", *COLUMNS, *PLACEBO_COLUMNS)
