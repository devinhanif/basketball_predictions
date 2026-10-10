"""MINUTES_HAZARD evaluation (frozen rule: docs/prereg/MINUTES_HAZARD.md, sha256 65fced23).

    python -W ignore -m research.eval.minutes_hazard_eval gate    # minimum-data checks 1-5
    python -W ignore -m research.eval.minutes_hazard_eval smoke   # one month block, 50 paths
    python -W ignore -m research.eval.minutes_hazard_eval run     # gate, full run, report

``nba.duckdb`` is opened read-only with ``season <= 2024`` in every query (asserted); season 2025
is never loaded. Heavy stages take ``data/ops/heavy.lock`` (retry 120 s, released in ``finally``).
Checkpoints live in ``data/minutes_hazard/`` (git-ignored) and resume on rerun; outputs go to
``reports/prereg_minutes_hazard/`` and ``reports/prereg_minutes_hazard.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    month_blocks,
)
from nba.props.context_residual import CRPS_TAUS, stat_frame
from nba.props.lower_tail import short_flag
from research.eval.minutes_v2_eval import (
    _ci,
    build_frame,
    load_world,
    props_walk,
    score_minutes,
)
from research.minutes import hazard as hz
from research.props.minutes_v2 import (
    MIN_TRAIN,
    MV2_FEATURES,
    V2_MINUTES_FEATURES,
    MinutesV2Model,
    summarize_quantiles,
)

PREREG = Path("docs/prereg/MINUTES_HAZARD.md")
MARKER = "=== RESULTS BELOW ==="
FROZEN_SHA = "65fced234352f2cbf1f154add6f5c25e7014401bf9295cd852af3973c15f4283"
OUT_DIR = Path("reports/prereg_minutes_hazard")
REPORT_PATH = Path("reports/prereg_minutes_hazard.md")
CACHE = Path("data/minutes_hazard")
STORED = Path("reports/minutes_v2")
ODDS_DIR = Path("reports/odds_history")
PBP_DIR = Path("data/pbp")
LOCK = Path("data/ops/heavy.lock")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEASONS = (SELECT_SEASON, REPORT_SEASON)
N_BOOT = 2000
MIN_SLICE_N = 300
CRPS_FLOOR = -0.10
BIAS_TOL = 0.5
PIT_TOL = 0.02
SLICE_TOL = 0.10
H0_MARGIN = -0.05
PROPS_FLOOR = -0.005
M2_BEAT = -0.002
GAP_PP = 2.0
M2_TOL = 1e-6
BUCKETS = ((0.0, 10.0), (10.0, 20.0), (20.0, 30.0), (30.0, 1e9))
PROP_STATS = ("pts", "reb", "ast", "fg3m")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze + lock


def frozen_sha(path: Path = PREREG) -> str:
    """sha256 of everything above the marker line (the awk command in the doc header)."""
    h = hashlib.sha256()
    for line in path.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        h.update(line.encode())
    return h.hexdigest()


def require_frozen() -> str:
    sha = frozen_sha()
    if sha != FROZEN_SHA:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {FROZEN_SHA}")
    return sha


class HeavyLock:
    """``mkdir data/ops/heavy.lock`` with retry every 120 s; ``rmdir`` on exit."""

    def __enter__(self) -> HeavyLock:
        while True:
            try:
                LOCK.mkdir()
                break
            except FileExistsError:
                _log("heavy.lock held; retry in 120 s")
                time.sleep(120)
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(OSError):
            LOCK.rmdir()


# --------------------------------------------------------------------------- data


def assert_seasons(df: pl.DataFrame, col: str = "season") -> None:
    if col in df.columns and df.height and int(df[col].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")


def load_tables(db_path: str = "nba.duckdb") -> dict[str, pl.DataFrame]:
    """Stints, possessions and per-season coverage counts (read-only, season <= 2024)."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, season, home_team, away_team, home_pts, away_pts FROM games "
            f"WHERE season <= {MAX_SEASON}"
        ).pl()
        stints = con.execute(
            "SELECT s.game_id, s.team_id, s.period, s.start_clock, s.end_clock, s.players "
            f"FROM stints s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        poss = con.execute(
            "SELECT p.game_id, p.poss_idx, p.period, p.clock_start, p.clock_end, p.off_team, "
            "p.score_diff FROM possessions p JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert_seasons(g)
    return {"games": g, "stints": stints, "poss": poss}


def _date_int(d: pl.Series) -> np.ndarray:
    return np.asarray(d.cast(pl.Date).cast(pl.Int32).to_numpy(), dtype=np.int64)


def _gid_int(d: pl.Series) -> np.ndarray:
    return np.asarray(d.cast(pl.Int64).to_numpy(), dtype=np.int64)


def game_prob_table(frame: pl.DataFrame) -> pl.DataFrame:
    """Per game: pre-tip home win probability ``p_home`` (OOF rung0 injury-Elo, 2023/2024) and
    the as-of MOV-Elo home expected margin ``mu_elo`` (the 2022 fallback)."""
    parts = []
    for s in SEASONS:
        parts.append(
            pl.read_parquet(ODDS_DIR / f"game_rows_{s}.parquet").select(
                "game_id", pl.col("p").alias("p_home")
            )
        )
    p = pl.concat(parts)
    mu = (
        frame.select(
            "game_id",
            (pl.col("exp_margin") * (2 * pl.col("is_home_f") - 1)).alias("m"),
        )
        .group_by("game_id")
        .agg(pl.col("m").mean().alias("mu_elo"))
    )
    return mu.join(p, on="game_id", how="left")


def build_world(db_path: str = "nba.duckdb") -> dict[str, Any]:
    """Frame (MINUTES_V2's production + V2 features + realised labels), stints, possessions."""
    world = load_world(db_path)
    try:
        frame = build_frame(world)
        pf = world["pgs"].select("game_id", "player_id", "pf")
    finally:
        world["sc"].close()
    frame = frame.join(pf, on=["game_id", "player_id"], how="left").with_row_index("rid")
    assert_seasons(frame)
    tabs = load_tables(db_path)
    return {"frame": frame, "world": world, **tabs}


def hazard_arrays(w: dict[str, Any], cache: bool = True) -> dict[str, Any]:
    """Slot labels, parsed fouls, margins and as-of inputs aligned to ``frame`` rows."""
    frame: pl.DataFrame = w["frame"]
    keys = frame.select("game_id", "player_id")
    path = CACHE / "arrays.npz"
    if cache and path.exists():
        z = np.load(path)
        y, fouls, parsed = z["y"], z["fouls"], z["parsed"]
        _log(f"loaded cached slot arrays {y.shape}")
    else:
        t0 = time.time()
        y = hz.build_y(w["stints"], keys)
        _log(f"slot labels built {y.shape} in {time.time() - t0:.0f}s")
        t0 = time.time()
        events = hz.load_foul_events(PBP_DIR, sorted(set(frame["game_id"].to_list())))
        pf = frame["pf"].cast(pl.Float64).to_numpy()
        fouls, parsed = hz.build_fouls(events, keys, pf, y)
        _log(f"fouls parsed ({events.height} events) in {time.time() - t0:.0f}s")
        if cache:
            CACHE.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, y=y, fouls=fouls, parsed=parsed)
    gidx, marg, n_ot = hz.build_margins(w["poss"], w["games"])
    min10 = frame["min10"].cast(pl.Float64).to_numpy()
    st10 = frame["starter10"].cast(pl.Float64).to_numpy()
    role = hz.role_of(min10, st10)
    asof = hz.asof_inputs(
        frame["player_id"].to_numpy().astype(np.int64),
        frame["team_id"].to_numpy().astype(np.int64),
        frame["season"].to_numpy().astype(np.int64),
        _gid_int(frame["game_id"]),
        _date_int(frame["game_date"]),
        frame["minutes"].cast(pl.Float64).to_numpy(),
        frame["pf"].cast(pl.Float64).to_numpy(),
        role,
        y,
    )
    return {
        "y": y,
        "fouls": fouls,
        "parsed": parsed,
        "gidx": gidx,
        "margin": marg,
        "n_ot": n_ot,
        "asof": asof,
        "role": role,
    }


# --------------------------------------------------------------------------- M2 (comparator)


def run_m2(world: dict[str, Any], sf: pl.DataFrame) -> dict[str, Any]:
    """MINUTES_V2's frozen M2, refit by the same month-block harness (nothing else changed)."""
    cfg = world["cfg"]
    mv2_parts: list[pl.DataFrame] = []
    test_q: list[np.ndarray] = []
    test_rid: list[np.ndarray] = []
    blocks = month_blocks(sf)
    for bi, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        m2 = MinutesV2Model(list(V2_MINUTES_FEATURES), "mixture", cfg).fit(train)
        q2 = m2.quantiles(block)
        pi2 = m2.pi(block)
        mv2_parts.append(
            pl.concat(
                [block.select("game_id", "player_id"), summarize_quantiles(q2, pi2)],
                how="horizontal",
            )
        )
        msk = block["season"].is_in(list(TEST_SEASONS)).to_numpy()
        if msk.any():
            test_q.append(q2[msk])
            test_rid.append(block["rid"].to_numpy()[msk])
        _log(f"M2 block {bi + 1}/{len(blocks)} {start}")
    return {
        "mv2": pl.concat(mv2_parts),
        "q": np.concatenate(test_q),
        "rid": np.concatenate(test_rid),
    }


def m2_cached(world: dict[str, Any], sf: pl.DataFrame) -> dict[str, Any]:
    qp, mp = CACHE / "m2_q.npz", CACHE / "m2_mv2.parquet"
    if qp.exists() and mp.exists():
        z = np.load(qp)
        return {"q": z["q"], "rid": z["rid"], "mv2": pl.read_parquet(mp)}
    out = run_m2(world, sf)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(qp, q=out["q"], rid=out["rid"])
    out["mv2"].write_parquet(mp)
    return out


def m2_reproduction(sf: pl.DataFrame, m2: dict[str, Any]) -> dict[str, Any]:
    """Gate check 5c: M2 per-row CRPS against MINUTES_V2's stored scores."""
    z = np.load(STORED / "minutes_scores.npz", allow_pickle=True)
    rows = sf.select("rid", "game_id", "player_id", "season", "minutes")
    mine = rows.filter(pl.col("rid").is_in(m2["rid"].tolist()))
    order = {int(r): i for i, r in enumerate(m2["rid"])}
    mine = mine.with_columns(pl.Series("i", [order[int(r)] for r in mine["rid"]])).sort("i")
    y = mine["minutes"].cast(pl.Float64).to_numpy()
    crps = score_minutes(m2["q"][mine["i"].to_numpy()], y)["crps"]
    stored = pl.DataFrame(
        {
            "game_id": z["row_gid"].astype(str),
            "player_id": z["row_pid"].astype(np.int64),
            "crps_stored": z["M2_crps"],
        }
    )
    j = mine.with_columns(pl.Series("crps_new", crps)).join(
        stored, on=["game_id", "player_id"], how="inner"
    )
    d = (j["crps_new"] - j["crps_stored"]).to_numpy()
    out: dict[str, Any] = {
        "n_matched": int(j.height),
        "n_stored": int(len(z["M2_crps"])),
        "max_abs_row_diff": float(np.abs(d).max()) if len(d) else float("nan"),
    }
    for s in SEASONS:
        m = (j["season"] == s).to_numpy()
        out[f"mean_new_{s}"] = float(j["crps_new"].to_numpy()[m].mean())
        out[f"mean_stored_{s}"] = float(j["crps_stored"].to_numpy()[m].mean())
        out[f"abs_diff_{s}"] = abs(out[f"mean_new_{s}"] - out[f"mean_stored_{s}"])
    out["ok"] = bool(
        out["n_matched"] == out["n_stored"] and all(out[f"abs_diff_{s}"] <= M2_TOL for s in SEASONS)
    )
    return out


# --------------------------------------------------------------------------- simulation


def block_inputs(w: dict[str, Any], a: dict[str, Any], gp: Any, start: Any) -> dict[str, Any]:
    """Block-level constants from games strictly before ``start`` (training window)."""
    frame: pl.DataFrame = w["frame"]
    g = w["games"].join(frame.select("game_id", "game_date").unique("game_id"), on="game_id")
    prior = g.filter(pl.col("game_date") < start)
    margin = (prior["home_pts"] - prior["away_pts"]).cast(pl.Float64).to_numpy()
    sd = float(np.std(margin, ddof=1))
    pr = frame.filter(pl.col("game_date") < start).filter(pl.col("pf").is_not_null())
    lr = float(pr["pf"].sum()) / float(pr["minutes"].sum())
    return {"sd": sd, "league_rate": lr, "gp": gp}


def train_arrays(w: dict[str, Any], a: dict[str, Any], rids: np.ndarray) -> dict[str, np.ndarray]:
    """Slot-level training arrays for the rows ``rids`` (actual states, labels)."""
    frame: pl.DataFrame = w["frame"]
    sub = frame[rids.tolist()] if False else frame.filter(pl.col("rid").is_in(rids.tolist()))
    order = {int(r): i for i, r in enumerate(rids)}
    sub = sub.with_columns(pl.Series("i", [order[int(r)] for r in sub["rid"]])).sort("i")
    gi = np.array([a["gidx"][g] for g in sub["game_id"].to_list()])
    sign = np.where(sub["is_home_f"].to_numpy() == 1, 1.0, -1.0)
    margin_team = a["margin"][gi] * sign[:, None]
    n_ot = a["n_ot"][gi]
    valid = np.arange(hz.NSLOT)[None, :] < (hz.NREG + hz.N_OT_SLOTS * n_ot)[:, None]
    return {
        "logit": a["asof"].habit_logit[rids],
        "y": a["y"][rids],
        "valid": valid,
        "fouls": a["fouls"][rids],
        "margin": margin_team,
        "role": a["role"][rids],
        "vac": sub["vac_min"].cast(pl.Float64).to_numpy(),
    }


def simulate_rows(
    w: dict[str, Any],
    a: dict[str, Any],
    rows: pl.DataFrame,
    mode: str,
    params: hz.HazardParams | None,
    bi: dict[str, Any],
    n_paths: int,
) -> tuple[np.ndarray, np.ndarray]:
    """(199-quantile grid [n, 199], short-event share [n]) for ``rows`` (columns rid, game_id,
    min10, vac_min, is_home_f); rows of a game are simulated together."""
    n = rows.height
    q = np.zeros((n, len(CRPS_TAUS)))
    pi = np.zeros(n)
    gids = rows["game_id"].to_numpy()
    rid = rows["rid"].to_numpy()
    vac = rows["vac_min"].cast(pl.Float64).to_numpy()
    min10 = rows["min10"].cast(pl.Float64).to_numpy()
    asof = a["asof"]
    rate_all = hz.foul_rates(asof.fnum40[rid], asof.fden40[rid], bi["league_rate"])
    order = np.argsort(gids, kind="stable")
    starts = np.r_[0, np.nonzero(gids[order][1:] != gids[order][:-1])[0] + 1, n]
    gp: dict[str, tuple[float, float]] = bi["gp"]
    for s0, s1 in zip(starts[:-1], starts[1:], strict=True):
        ix = order[s0:s1]
        gid = str(gids[ix[0]])
        gnum = int(gid)
        p_home, mu_elo = gp[gid]
        sd = bi["sd"]
        mu = hz.margin_params_from_p(p_home, sd) if p_home == p_home else float(mu_elo)
        rng_m = np.random.default_rng([hz.SEED, gnum, 1])
        rng_p = np.random.default_rng([hz.SEED, gnum, 2])
        gi = a["gidx"][gid]
        sums = hz.simulate_game(
            mode,
            asof.habit_logit[rid[ix]],
            a["role"][rid[ix]],
            vac[ix],
            rate_all[ix],
            mu,
            sd,
            params,
            rng_m,
            rng_p,
            n_paths=n_paths,
            margin_actual=a["margin"][gi],
            n_ot_actual=int(a["n_ot"][gi]),
        )
        q[ix] = hz.quantile_grid(sums, CRPS_TAUS)
        pi[ix] = short_flag(sums, min10[ix][:, None]).mean(axis=1)
    return q, pi


def run_blocks(
    w: dict[str, Any],
    a: dict[str, Any],
    sf: pl.DataFrame,
    gp: pl.DataFrame,
    n_paths: int,
    only_start: str | None = None,
    ckpt: Path | None = None,
) -> list[dict[str, Any]]:
    gpd = {
        str(r[0]): (float(r[1]) if r[1] is not None else float("nan"), float(r[2]))
        for r in gp.select("game_id", "p_home", "mu_elo").iter_rows()
    }
    ckpt = ckpt or CACHE / "blocks"
    ckpt.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    blocks = month_blocks(sf)
    for bi_, (start, end) in enumerate(blocks):
        if only_start is not None and str(start) != only_start:
            continue
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        f = ckpt / f"blk_{start}_{n_paths}.npz"
        if f.exists():
            z = np.load(f, allow_pickle=True)
            out.append({k: z[k] for k in z.files})
            _log(f"block {bi_ + 1}/{len(blocks)} {start} loaded")
            continue
        t0 = time.time()
        const = block_inputs(w, a, gpd, start)
        const["gp"] = gpd
        tr = train_arrays(w, a, train["rid"].to_numpy())
        params = hz.fit_hazard(
            tr["logit"], tr["y"], tr["valid"], tr["fouls"], tr["margin"], tr["role"], tr["vac"]
        )
        del tr
        t_fit = time.time() - t0
        rows = block.select("rid", "game_id", "min10", "vac_min", "is_home_f", "season")
        q1, pi1 = simulate_rows(w, a, rows, "h1", params, const, n_paths)
        test = rows["season"].is_in(list(TEST_SEASONS)).to_numpy()
        trows = rows.filter(pl.Series(test))
        q0, _ = (
            simulate_rows(w, a, trows, "h0", None, const, n_paths) if test.any() else (q1[:0], None)
        )
        q2, _ = (
            simulate_rows(w, a, trows, "h2", params, const, n_paths)
            if test.any()
            else (q1[:0], None)
        )
        rec = {
            "rid": rows["rid"].to_numpy(),
            "q1": q1.astype(np.float32),
            "pi1": pi1,
            "test": test,
            "q0": q0.astype(np.float32),
            "q2": q2.astype(np.float32),
            "foul": params.foul,
            "margin": params.margin,
            "beta": np.array([params.beta]),
            "info": np.array([params.n_slots, params.n_iter, float(params.converged), const["sd"]]),
        }
        np.savez_compressed(f, **rec)  # type: ignore[arg-type]
        out.append(rec)
        _log(
            f"block {bi_ + 1}/{len(blocks)} {start} rows={rows.height} test={int(test.sum())} "
            f"fit={t_fit:.0f}s total={time.time() - t0:.0f}s iters={params.n_iter} "
            f"conv={params.converged} beta={params.beta:+.3f}"
        )
    return out


# --------------------------------------------------------------------------- gate


def bucket_name(lo: float, hi: float) -> str:
    return f"[{lo:g},{hi:g})" if hi < 1e8 else f"[{lo:g},inf)"


def missingness(
    w: dict[str, Any], a: dict[str, Any], gp: pl.DataFrame, sf: pl.DataFrame
) -> list[dict[str, Any]]:
    """Null rate of every input by realised-minutes bucket (eligible rows)."""
    rid = sf["rid"].to_numpy()
    gpd = {r[0]: (r[1], r[2]) for r in gp.select("game_id", "p_home", "mu_elo").iter_rows()}
    gids = sf["game_id"].to_list()
    asof = a["asof"]
    has_margin = np.array([bool(np.abs(a["margin"][a["gidx"][g]]).sum() > 0) for g in gids])
    mu_ok = np.array(
        [
            (gpd[g][0] is not None and gpd[g][0] == gpd[g][0]) or (gpd[g][1] == gpd[g][1])
            for g in gids
        ]
    )
    cols = {
        "vac_min": sf["vac_min"].is_null().to_numpy(),
        "min10": sf["min10"].is_null().to_numpy(),
        "starter10": sf["starter10"].is_null().to_numpy(),
        "pf": sf["pf"].is_null().to_numpy(),
        "habit_history(k=0)": asof.k_habit[rid] == 0,
        "foul_rate_history": asof.fden40[rid] <= 0,
        "stint_labels(sumY=0)": a["y"][rid].sum(axis=1) <= 0,
        "foul_timing_pbp(fallback)": ~a["parsed"][rid],
        "pretip_margin_mu": ~mu_ok,
        "actual_margin_path": ~has_margin,
    }
    mins = sf["minutes"].cast(pl.Float64).to_numpy()
    out: list[dict[str, Any]] = []
    for c, null in cols.items():
        for lo, hi in BUCKETS:
            m = (mins >= lo) & (mins < hi)
            out.append(
                {
                    "col": c,
                    "bucket": bucket_name(lo, hi),
                    "n": int(m.sum()),
                    "null_rate": float(null[m].mean()) if m.any() else float("nan"),
                }
            )
    return out


def max_gap_pp(audit: list[dict[str, Any]]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in audit:
        by.setdefault(r["col"], []).append(r["null_rate"])
    return {c: 100.0 * (max(v) - min(v)) for c, v in by.items()}


def planted_tests(
    w: dict[str, Any], a: dict[str, Any], gp: pl.DataFrame, sf: pl.DataFrame
) -> dict[str, bool]:
    """Same-game and future plants: pre-tip inputs and the simulated minutes must be
    byte-identical when tonight's stints, fouls, minutes and margin (and everything after)
    are edited."""
    frame: pl.DataFrame = w["frame"]
    rng = np.random.default_rng(7)
    pid = frame["player_id"].to_numpy().astype(np.int64)
    tid = frame["team_id"].to_numpy().astype(np.int64)
    season = frame["season"].to_numpy().astype(np.int64)
    gid = _gid_int(frame["game_id"])
    date = _date_int(frame["game_date"])
    minutes = frame["minutes"].cast(pl.Float64).to_numpy()
    pf = frame["pf"].cast(pl.Float64).to_numpy()
    role = a["role"]
    y = a["y"]
    base = a["asof"]

    def rerun(edit: np.ndarray) -> tuple[hz.AsOf, np.ndarray]:
        y2 = y.copy()
        y2[edit] = rng.random((int(edit.sum()), hz.NSLOT)).astype(np.float32)
        m2 = minutes.copy()
        m2[edit] = rng.uniform(0, 48, int(edit.sum()))
        p2 = pf.copy()
        p2[edit] = rng.integers(0, 7, int(edit.sum()))
        return hz.asof_inputs(pid, tid, season, gid, date, m2, p2, role, y2), y2

    test_rows = sf.filter(pl.col("season") == REPORT_SEASON).sample(300, seed=0)
    # same game: edit exactly one game's own stints / fouls / minutes at a time (three games);
    # that game's rows must be byte-identical (edits to other rows of the same game are tonight)
    ok_same = True
    asof_same = base
    gids_s = test_rows["game_id"].unique(maintain_order=True).to_list()[:3]
    game_rows = frame["game_id"].to_numpy()
    for g1 in gids_s:
        same_edit = game_rows == g1
        asof_same, _ = rerun(same_edit)
        ix = np.nonzero(same_edit & np.isin(np.arange(frame.height), sf["rid"].to_numpy()))[0]
        ok_same &= bool(
            np.array_equal(base.habit_logit[ix], asof_same.habit_logit[ix])
            and np.array_equal(base.fnum40[ix], asof_same.fnum40[ix])
            and np.array_equal(base.fden40[ix], asof_same.fden40[ix])
        )
    # future: edit every row dated on/after the cutoff; rows strictly before are unchanged
    cut = int(np.median(date[sf["rid"].to_numpy()]))
    fut_edit = date >= cut
    asof_fut, _ = rerun(fut_edit)
    before = np.nonzero(date < cut)[0]
    sample = rng.choice(before, size=min(5000, len(before)), replace=False)
    ok_fut = bool(
        np.array_equal(base.habit_logit[sample], asof_fut.habit_logit[sample])
        and np.array_equal(base.fnum40[sample], asof_fut.fnum40[sample])
        and np.array_equal(base.fden40[sample], asof_fut.fden40[sample])
    )
    # simulated minutes unchanged when tonight's margin path is edited (h1 ignores it)
    gpd = {
        str(r[0]): (float(r[1]) if r[1] is not None else float("nan"), float(r[2]))
        for r in gp.select("game_id", "p_home", "mu_elo").iter_rows()
    }
    train = sf.filter(pl.col("game_date") < test_rows["game_date"].min())
    tr = train_arrays(w, a, train["rid"].to_numpy()[-20000:])
    params = hz.fit_hazard(
        tr["logit"], tr["y"], tr["valid"], tr["fouls"], tr["margin"], tr["role"], tr["vac"]
    )
    const = block_inputs(w, a, gpd, test_rows["game_date"].min())
    const["gp"] = gpd
    rows = (
        sf.filter(pl.col("game_id") == gids_s[-1])
        .select("rid", "game_id", "min10", "vac_min", "is_home_f")
        .head(60)
    )
    q_a, _ = simulate_rows(w, a, rows, "h1", params, const, 50)
    a2 = dict(a)
    a2["margin"] = rng.normal(0, 20, a["margin"].shape).astype(np.float32)
    a2["n_ot"] = rng.integers(0, 3, a["n_ot"].shape)
    a2["asof"] = asof_same
    q_b, _ = simulate_rows(w, a2, rows, "h1", params, const, 50)
    ok_sim = bool(np.array_equal(q_a, q_b))
    return {
        "planted_same_game_inputs": ok_same,
        "planted_future_inputs": ok_fut,
        "planted_same_game_h1_minutes_byte_identical": ok_sim,
    }


def gate_checks(
    w: dict[str, Any], a: dict[str, Any], gp: pl.DataFrame, sf: pl.DataFrame, m2: dict[str, Any]
) -> dict[str, Any]:
    frame: pl.DataFrame = w["frame"]
    g = w["games"]
    res: dict[str, Any] = {}
    # 1. stints coverage of games with a result
    cov: dict[str, Any] = {}
    in_stints = set(w["stints"]["game_id"].unique().to_list())
    ok1 = True
    for s in (2022, 2023, 2024):
        gs = g.filter((pl.col("season") == s) & pl.col("home_pts").is_not_null())
        n, k = gs.height, sum(1 for x in gs["game_id"].to_list() if x in in_stints)
        cov[str(s)] = {"games": n, "with_stints": k, "share": k / n}
        ok1 &= k / n >= 0.95
    res["1_stints_coverage"] = {"ok": bool(ok1), "detail": cov}
    # 2. pf non-null on played rows; pbp parse share
    pf_share = float(frame["pf"].is_not_null().mean())  # type: ignore[arg-type]
    parsed = float(a["parsed"].mean())
    res["2_pf_nonnull"] = {
        "ok": bool(pf_share >= 0.99),
        "detail": {
            "pf_nonnull_share": pf_share,
            "pbp_timed_share": parsed,
            "fallback_share": 1.0 - parsed,
        },
    }
    # 3. possessions non-null
    p = w["poss"]
    ok_rows = (
        p["score_diff"].is_not_null()
        & p["clock_start"].is_not_null()
        & p["clock_end"].is_not_null()
    )
    nn = float(ok_rows.mean())
    res["3_possessions_nonnull"] = {"ok": bool(nn >= 0.99), "detail": {"share": nn, "n": p.height}}
    # 4. starter-games
    sg = int(frame["starter_act"].sum()) if "starter_act" in frame.columns else 0
    res["4_starter_games"] = {"ok": bool(sg >= 30000), "detail": {"starter_games": sg}}
    # 5. missingness, plants, M2 reproduction
    audit = missingness(w, a, gp, sf)
    gaps = max_gap_pp(audit)
    res["5a_missingness_gap"] = {
        "ok": bool(max(gaps.values()) <= GAP_PP),
        "detail": {"max_gap_pp_by_col": gaps},
        "audit": audit,
    }
    pl_t = planted_tests(w, a, gp, sf)
    res["5b_planted"] = {"ok": bool(all(pl_t.values())), "detail": pl_t}
    rep = m2_reproduction(sf, m2)
    res["5c_m2_reproduces"] = {"ok": bool(rep["ok"]), "detail": rep}
    res["all_ok"] = bool(all(v["ok"] for k, v in res.items() if k != "all_ok"))
    return res


# --------------------------------------------------------------------------- analysis


def _pit_rates(pit: np.ndarray) -> tuple[float, float, float]:
    return (
        float((pit <= 0.10).mean()),
        float((pit <= 0.90).mean()),
        float(((pit > 0.10) & (pit <= 0.90)).mean()),
    )


def returning_3plus(frame: pl.DataFrame) -> np.ndarray:
    f = frame.select("rid", "player_id", "season", "game_date", "game_id", "team_game_no")
    f = f.sort(["player_id", "game_date", "game_id"]).with_columns(
        pl.col("team_game_no").shift(1).over("player_id").alias("_pt"),
        pl.col("season").shift(1).over("player_id").alias("_ps"),
    )
    miss = (
        (pl.col("_ps") == pl.col("season")) & ((pl.col("team_game_no") - pl.col("_pt") - 1) >= 3)
    ).fill_null(False)
    f = f.with_columns(miss.alias("ret3")).sort("rid")
    return f["ret3"].to_numpy()


def analyze_minutes(
    sf: pl.DataFrame,
    m2: dict[str, Any],
    recs: list[dict[str, Any]],
    gp: pl.DataFrame,
    ret3: np.ndarray,
) -> dict[str, Any]:
    rid_h = np.concatenate([r["rid"][r["test"]] for r in recs])
    q1 = np.concatenate([r["q1"][r["test"]] for r in recs]).astype(np.float64)
    q0 = np.concatenate([r["q0"] for r in recs]).astype(np.float64)
    q2 = np.concatenate([r["q2"] for r in recs]).astype(np.float64)
    pos_m2 = {int(r): i for i, r in enumerate(m2["rid"])}
    qm = m2["q"][[pos_m2[int(r)] for r in rid_h]]
    tst = sf.filter(pl.col("rid").is_in(rid_h.tolist()))
    order = {int(r): i for i, r in enumerate(rid_h)}
    tst = tst.with_columns(pl.Series("i", [order[int(r)] for r in tst["rid"]])).sort("i")
    assert tst.height == len(rid_h), "hazard and M2 rows are not aligned"
    y = tst["minutes"].cast(pl.Float64).to_numpy()
    gid = tst["game_id"].to_numpy()
    season = tst["season"].to_numpy()
    gpd = {r[0]: r[1] for r in gp.select("game_id", "p_home").iter_rows()}
    ph = np.array([gpd.get(g) for g in tst["game_id"].to_list()], dtype=float)
    home = tst["is_home_f"].to_numpy() == 1
    p_team = np.where(home, ph, 1.0 - ph)
    arms = {"M2": qm, "H1": q1, "H0": q0, "H2": q2}
    sc = {k: score_minutes(v, y) for k, v in arms.items()}
    slices: dict[str, np.ndarray] = {
        "all": np.ones(len(y), dtype=bool),
        "starter": tst["starter10"].fill_null(0.0).to_numpy() >= 0.5,
        "bench": tst["starter10"].fill_null(0.0).to_numpy() < 0.5,
        "first15": tst["team_game_no"].to_numpy() < 15,
        "cold(n_prior<20)": tst["n_prior"].to_numpy() < 20,
        "teammate_out": (tst["has_report"].to_numpy() == 1)
        & (tst["n_out_rot"].fill_null(0).to_numpy() > 0),
        "elo_fav>=0.65": p_team >= 0.65,
        "elo_dog<=0.35": p_team <= 0.35,
        "returning_3plus": ret3[tst["rid"].to_numpy()],
    }
    elo_b = {
        "elo<=0.35": p_team <= 0.35,
        "elo(0.35,0.50]": (p_team > 0.35) & (p_team <= 0.50),
        "elo(0.50,0.65)": (p_team > 0.50) & (p_team < 0.65),
        "elo>=0.65": p_team >= 0.65,
    }
    out: dict[str, Any] = {"table": [], "slices": [], "mechanism": [], "pairs": []}
    for s in SEASONS:
        sm = season == s
        for arm in arms:
            d = sc[arm]["crps"][sm] - sc["M2"]["crps"][sm]
            ci = _ci(d, gid[sm]) if arm != "M2" else {"point": 0.0, "lo": 0.0, "hi": 0.0}
            pq10, pq90, cov80 = _pit_rates(sc[arm]["pit"][sm])
            out["table"].append(
                {
                    "season": s,
                    "arm": arm,
                    "n_rows": int(sm.sum()),
                    "n_games": int(len(np.unique(gid[sm]))),
                    "crps_min": float(sc[arm]["crps"][sm].mean()),
                    "dcrps": ci["point"],
                    "ci_lo": ci["lo"],
                    "ci_hi": ci["hi"],
                    "bias": float((sc[arm]["mean"][sm] - y[sm]).mean()),
                    "cov80": cov80,
                    "pit_q10": pq10,
                    "pit_q90": pq90,
                }
            )
        for a_, b_ in (("H1", "H0"), ("H2", "H1")):
            d = sc[a_]["crps"][sm] - sc[b_]["crps"][sm]
            ci = _ci(d, gid[sm])
            out["pairs"].append(
                {
                    "season": s,
                    "pair": f"{a_}-{b_}",
                    "dcrps": ci["point"],
                    "ci_lo": ci["lo"],
                    "ci_hi": ci["hi"],
                }
            )
        for name, mk in {**slices, **elo_b}.items():
            m = mk & sm
            if not m.any():
                continue
            for arm in ("H1", "H0", "H2"):
                d = sc[arm]["crps"][m] - sc["M2"]["crps"][m]
                ci = _ci(d, gid[m])
                rec = {
                    "season": s,
                    "slice": name,
                    "arm": arm,
                    "n": int(m.sum()),
                    "dcrps": ci["point"],
                    "ci_lo": ci["lo"],
                    "ci_hi": ci["hi"],
                }
                (out["mechanism"] if name in elo_b else out["slices"]).append(rec)
    return out


def minutes_verdict(res: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    for s in SEASONS:
        t = {r["arm"]: r for r in res["table"] if r["season"] == s}
        h1 = t["H1"]
        pr = {r["pair"]: r for r in res["pairs"] if r["season"] == s}
        bad = [
            r["slice"]
            for r in res["slices"]
            if r["season"] == s
            and r["arm"] == "H1"
            and r["n"] >= MIN_SLICE_N
            and r["dcrps"] > SLICE_TOL
        ]
        checks = {
            "crps_floor": h1["dcrps"] <= CRPS_FLOOR,
            "ci_upper_lt_0": h1["ci_hi"] < 0,
            "pit_q10": abs(h1["pit_q10"] - 0.10) <= PIT_TOL,
            "pit_q90": abs(h1["pit_q90"] - 0.90) <= PIT_TOL,
            "bias": abs(h1["bias"]) <= BIAS_TOL,
            "slices": not bad,
            "beats_H0": pr["H1-H0"]["dcrps"] <= H0_MARGIN,
        }
        v[str(s)] = {"checks": checks, "bad_slices": bad, "pass": all(checks.values())}
    return v


def run_props(
    frame: pl.DataFrame, cfg: Any, mv2_m2: pl.DataFrame, mv2_h1: pl.DataFrame, ckpt: Path
) -> dict[str, dict[str, np.ndarray]]:
    ckpt.mkdir(parents=True, exist_ok=True)
    arms: dict[str, tuple[pl.DataFrame, tuple[str, ...]]] = {
        "A0": (frame, ()),
        "A_M2": (frame.join(mv2_m2, on=["game_id", "player_id"], how="left"), MV2_FEATURES),
        "A1": (frame.join(mv2_h1, on=["game_id", "player_id"], how="left"), MV2_FEATURES),
    }
    out: dict[str, dict[str, np.ndarray]] = {}
    for stat in PROP_STATS:
        for arm, (fr, extra) in arms.items():
            f = ckpt / f"props_{stat}_{arm}.npz"
            if f.exists():
                z = np.load(f, allow_pickle=True)
                out[f"{stat}/{arm}"] = {k: z[k] for k in z.files}
                continue
            t0 = time.time()
            res = props_walk(fr, stat, extra, cfg)
            np.savez_compressed(f, **res)  # type: ignore[arg-type]
            out[f"{stat}/{arm}"] = res
            _log(f"props {stat} {arm} n={len(res['y'])} {time.time() - t0:.0f}s")
    return out


def analyze_props(pr: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        ps: dict[str, float] = {}
        for stat in PROP_STATS:
            a0, am, a1 = pr[f"{stat}/A0"], pr[f"{stat}/A_M2"], pr[f"{stat}/A1"]
            for o in (am, a1):
                if not (
                    np.array_equal(a0["gid"], o["gid"]) and np.array_equal(a0["pid"], o["pid"])
                ):
                    raise ValueError("props arms are not row-aligned")
            sm = a0["season"] == s
            gid = a0["gid"][sm]
            n, ng = int(sm.sum()), int(len(np.unique(gid)))
            for arm, o, ref in (
                ("A0", a0, None),
                ("A_M2", am, a0),
                ("A1", a1, a0),
                ("A1-A_M2", a1, am),
            ):
                crps = (a1 if arm == "A1-A_M2" else o)["crps_int"][sm]
                if ref is None:
                    ci = {"point": 0.0, "lo": 0.0, "hi": 0.0}
                    p = float("nan")
                    cr = float(crps.mean())
                else:
                    d = (
                        (a1["crps_int"][sm] - am["crps_int"][sm])
                        if arm == "A1-A_M2"
                        else (o["crps_int"][sm] - ref["crps_int"][sm])
                    )
                    ci = _ci(d, gid)
                    p = boot_p_value(d, gid)
                    cr = float(o["crps_int"][sm].mean())
                if arm == "A1":
                    ps[stat] = p
                rows.append(
                    {
                        "stat": stat,
                        "season": s,
                        "arm": arm,
                        "n_rows": n,
                        "n_games": ng,
                        "crps_int": cr,
                        "dcrps": ci["point"],
                        "ci_lo": ci["lo"],
                        "ci_hi": ci["hi"],
                        "p": p,
                        "p_bh": float("nan"),
                    }
                )
        adj = bh_adjust(np.array([ps[st] for st in PROP_STATS]))
        for st, ap in zip(PROP_STATS, adj, strict=True):
            for r in rows:
                if r["season"] == s and r["stat"] == st and r["arm"] == "A1":
                    r["p_bh"] = float(ap)
    verdict: dict[str, Any] = {}
    for st in PROP_STATS:
        for s in SEASONS:
            r1 = next(r for r in rows if r["stat"] == st and r["season"] == s and r["arm"] == "A1")
            rm = next(
                r for r in rows if r["stat"] == st and r["season"] == s and r["arm"] == "A1-A_M2"
            )
            chk = {
                "floor": r1["dcrps"] <= PROPS_FLOOR,
                "ci_upper_lt_0": r1["ci_hi"] < 0,
                "bh_p": r1["p_bh"] < 0.05,
                "beats_A_M2": rm["dcrps"] <= M2_BEAT,
            }
            verdict[f"{st}/{s}"] = {"checks": chk, "pass": all(chk.values())}
    return {"rows": rows, "verdict": verdict}


# --------------------------------------------------------------------------- report


def _ci_txt(d: float, lo: float, hi: float, nd: int = 4) -> str:
    return f"{d:+.{nd}f} [{lo:+.{nd}f},{hi:+.{nd}f}]"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render(res: dict[str, Any]) -> str:
    m, pr, g = res["minutes"], res["props"], res["gate"]
    o: list[str] = ["# MINUTES_HAZARD results (docs/prereg/MINUTES_HAZARD.md)", ""]
    o += [f"Frozen sha256 `{res['sha256']}`; git {res['git_sha']}; seeds {res['seeds']}.", ""]
    o += ["## Gate (minimum data checks 1-5)", ""]
    o += _table(
        ["test", "ok"],
        [[k, str(v["ok"])] for k, v in g.items() if isinstance(v, dict) and "ok" in v],
    )
    o += ["", "## Minutes (continuous CRPS, minutes; dcrps = arm - M2, game-clustered 95% CI)", ""]
    o += _table(
        [
            "season",
            "arm",
            "n (games)",
            "CRPS",
            "dCRPS vs M2 [CI]",
            "bias",
            "cov80",
            "PIT q10",
            "PIT q90",
        ],
        [
            [
                str(r["season"]),
                r["arm"],
                f"{r['n_rows']} ({r['n_games']})",
                f"{r['crps_min']:.4f}",
                _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"]),
                f"{r['bias']:+.3f}",
                f"{r['cov80']:.3f}",
                f"{r['pit_q10']:.3f}",
                f"{r['pit_q90']:.3f}",
            ]
            for r in m["table"]
        ],
    )
    o += ["", "Pairs:", ""]
    o += _table(
        ["season", "pair", "dCRPS [CI]"],
        [
            [str(r["season"]), r["pair"], _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"])]
            for r in m["pairs"]
        ],
    )
    o += ["", "Slices (arm - M2):", ""]
    o += _table(
        ["season", "slice", "arm", "n", "dCRPS [CI]"],
        [
            [
                str(r["season"]),
                r["slice"],
                r["arm"],
                str(r["n"]),
                _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"]),
            ]
            for r in m["slices"]
        ],
    )
    o += ["", "Mechanism by Elo bucket (arm - M2):", ""]
    o += _table(
        ["season", "bucket", "arm", "n", "dCRPS [CI]"],
        [
            [
                str(r["season"]),
                r["slice"],
                r["arm"],
                str(r["n"]),
                _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"]),
            ]
            for r in m["mechanism"]
        ],
    )
    o += ["", "H1 minutes verdict:", ""]
    for s, v in m["verdict"].items():
        chk = ", ".join(f"{k}={'ok' if x else 'FAIL'}" for k, x in v["checks"].items())
        o.append(f"* {s}: pass={v['pass']}; {chk}; bad slices {v['bad_slices']}")
    o += ["", "## Props (integer-support CRPS; arm - A0, A1-A_M2 is A1 minus A_M2)", ""]
    o += _table(
        ["stat", "season", "arm", "n (games)", "CRPS", "dCRPS [CI]", "p", "p_bh"],
        [
            [
                r["stat"],
                str(r["season"]),
                r["arm"],
                f"{r['n_rows']} ({r['n_games']})",
                f"{r['crps_int']:.4f}",
                _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"]),
                f"{r['p']:.3f}",
                f"{r['p_bh']:.3f}",
            ]
            for r in pr["rows"]
        ],
    )
    o += ["", "H2 props verdict:", ""]
    for k, v in pr["verdict"].items():
        chk = ", ".join(f"{c}={'ok' if x else 'FAIL'}" for c, x in v["checks"].items())
        o.append(f"* {k}: pass={v['pass']}; {chk}")
    o += ["", f"Overall: {res['verdict']}", ""]
    return "\n".join(o) + "\n"


def overall(mv: dict[str, Any], pv: dict[str, Any]) -> str:
    h1_ok = all(v["pass"] for v in mv.values())
    stats_ok = [st for st in PROP_STATS if all(pv[f"{st}/{s}"]["pass"] for s in SEASONS)]
    if h1_ok and stats_ok:
        return f"H1 PASS (minutes) and H2 PASS for {stats_ok}"
    if h1_ok:
        return "H1 PASS (better minutes shape), H2 FAIL: no props gain; direction closed for props"
    return "H1 FAIL on minutes; closed (no tuning)"


def ledger_rows(res: dict[str, Any]) -> str:
    sha = res["sha256"][:8]
    t = {(r["season"], r["arm"]): r for r in res["minutes"]["table"]}
    lines = ["| id | question | n | result | verdict |", "|---|---|---|---|---|"]
    for s in SEASONS:
        h1 = t[(s, "H1")]
        lines.append(
            f"| T### | MINUTES_HAZARD H1 minutes vs M2, {s} (rule {sha}) | "
            f"{h1['n_rows']} rows / {h1['n_games']} games | "
            f"dCRPS {_ci_txt(h1['dcrps'], h1['ci_lo'], h1['ci_hi'], 3)} min "
            f"(H1 {h1['crps_min']:.3f}, M2 {t[(s, 'M2')]['crps_min']:.3f}) | see report |"
        )
        for pair in res["minutes"]["pairs"]:
            if pair["season"] == s:
                lines.append(
                    f"| T### | MINUTES_HAZARD {pair['pair']} {s} (rule {sha}) | same | "
                    f"{_ci_txt(pair['dcrps'], pair['ci_lo'], pair['ci_hi'], 3)} | descriptive |"
                )
    for r in res["props"]["rows"]:
        if r["arm"] == "A1":
            ok = res["props"]["verdict"][r["stat"] + "/" + str(r["season"])]["pass"]
            ci = _ci_txt(r["dcrps"], r["ci_lo"], r["ci_hi"])
            lines.append(
                f"| T### | MINUTES_HAZARD props {r['stat']} A1-A0 {r['season']} (rule {sha}) | "
                f"{r['n_rows']} rows | {ci} p_bh={r['p_bh']:.3f} | {'pass' if ok else 'fail'} |"
            )
    lines += ["", f"Overall: {res['verdict']}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def prepare() -> dict[str, Any]:
    w = build_world()
    sf = stat_frame(w["frame"], "pts").sort(["game_date", "game_id", "player_id"])
    a = hazard_arrays(w)
    gp = game_prob_table(w["frame"])
    return {"w": w, "sf": sf, "a": a, "gp": gp, "cfg": w["world"]["cfg"]}


def do_gate(ctx: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    m2 = m2_cached(ctx["w"]["world"], ctx["sf"])
    res = gate_checks(ctx["w"], ctx["a"], ctx["gp"], ctx["sf"], m2)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gate.json").write_text(json.dumps(res, indent=1, default=float))
    for k, v in res.items():
        if isinstance(v, dict) and "ok" in v:
            _log(f"gate {k}: ok={v['ok']}")
    return res, m2


def do_report(
    ctx: dict[str, Any], gate: dict[str, Any], m2: dict[str, Any], recs: list[dict[str, Any]]
) -> dict[str, Any]:
    w, sf, gp = ctx["w"], ctx["sf"], ctx["gp"]
    ret3 = returning_3plus(w["frame"])
    mres = analyze_minutes(sf, m2, recs, gp, ret3)
    mres["verdict"] = minutes_verdict(mres)
    mv2_h = pl.concat(
        [
            pl.concat(
                [
                    w["frame"][r["rid"].tolist()].select("game_id", "player_id"),
                    summarize_quantiles(r["q1"].astype(np.float64), r["pi1"]),
                ],
                how="horizontal",
            )
            for r in recs
        ]
    )
    pr = run_props(w["frame"], ctx["cfg"], m2["mv2"], mv2_h, CACHE / "props")
    pres = analyze_props(pr)
    res = {
        "sha256": frozen_sha(),
        "git_sha": git_sha(),
        "seeds": {"sim": hz.SEED, "bootstrap": 0, "n_paths": hz.N_PATHS, "n_boot": N_BOOT},
        "gate": gate,
        "minutes": mres,
        "props": pres,
        "unstated_details": list(hz.UNSTATED_DETAILS),
        "audit": gate["5a_missingness_gap"]["audit"],
        "tests": [
            {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
        ],
        "block_fits": [
            {
                "n_slots": float(r["info"][0]),
                "n_iter": float(r["info"][1]),
                "converged": float(r["info"][2]),
                "margin_sd": float(r["info"][3]),
                "beta": float(r["beta"][0]),
            }
            for r in recs
        ],
    }
    res["verdict"] = overall(mres["verdict"], pres["verdict"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=float))
    REPORT_PATH.write_text(render(res))
    (OUT_DIR / "ledger_rows.md").write_text(ledger_rows(res))
    return res


def write_gate_stop(gate: dict[str, Any]) -> None:
    """Gate failed: no model was fit. Record the checks, the audit and the stop."""
    tests = [
        {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
    ]
    gaps = gate["5a_missingness_gap"]["detail"]["max_gap_pp_by_col"]
    res = {
        "status": "STOPPED_AT_GATE",
        "sha256": frozen_sha(),
        "git_sha": git_sha(),
        "seeds": {"sim": hz.SEED, "bootstrap": 0, "n_paths": hz.N_PATHS, "n_boot": N_BOOT},
        "tests": tests,
        "audit": gate["5a_missingness_gap"]["audit"],
        "max_gap_pp_by_col": gaps,
        "gate": gate,
        "unstated_details": list(hz.UNSTATED_DETAILS),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=float))
    o = ["# MINUTES_HAZARD: STOPPED AT THE GATE (no model result)", ""]
    o += [f"Frozen sha256 `{res['sha256']}`; git {res['git_sha']}.", ""]
    o += _table(["test", "ok"], [[t["test"], str(t["ok"])] for t in tests])
    o += [
        "",
        "Missingness audit: max gap in null rate across realised-minutes buckets (pp; limit 2):",
        "",
    ]
    o += _table(["input", "max gap pp"], [[c, f"{v:.2f}"] for c, v in gaps.items()])
    o += ["", "Null rate by bucket (inputs with any null):", ""]
    o += _table(
        ["input", "bucket", "n", "null rate"],
        [
            [r["col"], r["bucket"], str(r["n"]), f"{r['null_rate']:.4f}"]
            for r in res["audit"]
            if r["null_rate"] > 0
        ],
    )
    o.append("")
    REPORT_PATH.write_text("\n".join(o))
    sha = res["sha256"][:8]
    bad = [f"{c} {v:.2f}pp" for c, v in gaps.items() if v > GAP_PP]
    (OUT_DIR / "ledger_rows.md").write_text(
        "| id | question | n | result | verdict |\n|---|---|---|---|---|\n"
        f"| T### | MINUTES_HAZARD minimum-data gate (rule {sha}) | "
        f"{sum(r['n'] for r in res['audit'] if r['col'] == 'min10')} eligible rows 2022-24 | "
        f"check 5a missingness gap > {GAP_PP:g}pp: {', '.join(bad) or 'none'}; "
        f"5b planted ok={gate['5b_planted']['ok']}; "
        f"5c M2 reproduces ok={gate['5c_m2_reproduces']['ok']} | "
        "STOP: no fit, no model result |\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    args = ap.parse_args(argv)
    sha = require_frozen()
    _log(f"prereg sha256 verified {sha[:8]}")
    with HeavyLock():
        t0 = time.time()
        ctx = prepare()
        _log(f"prepared in {time.time() - t0:.0f}s")
        gate, m2 = do_gate(ctx)
        if not gate["all_ok"]:
            _log("GATE FAILED: stop, no model result")
            write_gate_stop(gate)
            return 2
        if args.stage == "gate":
            return 0
        if args.stage == "smoke":
            recs = run_blocks(
                ctx["w"],
                ctx["a"],
                ctx["sf"],
                ctx["gp"],
                50,
                only_start="2023-12-01",
                ckpt=CACHE / "smoke",
            )
            for r in recs:
                _log(
                    f"smoke block rows={len(r['rid'])} test={int(r['test'].sum())} "
                    f"mean H1 q50={float(np.median(r['q1'][:, 99])):.2f}"
                )
            return 0
        recs = run_blocks(ctx["w"], ctx["a"], ctx["sf"], ctx["gp"], hz.N_PATHS)
        res = do_report(ctx, gate, m2, recs)
        _log(f"verdict: {res['verdict']}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    raise SystemExit(main())
