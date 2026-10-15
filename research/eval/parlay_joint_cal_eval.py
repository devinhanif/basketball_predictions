# ruff: noqa: E501
"""PARLAY_JOINT_CAL scoring harness (rule: docs/prereg/PARLAY_JOINT_CAL.md).

    rehearse  synthetic labels only (no real outcome is read); allowed on the DRAFT; writes a rehearsal report
    score     real labels; refuses unless the rule is frozen and every harness file equals its blob at the freeze commit

    <python> research/eval/parlay_joint_cal_eval.py rehearse --data-root <main checkout> [--stride 10] [--books draftkings]
    <python> research/eval/parlay_joint_cal_eval.py score --data-root <main checkout> --sha <64 hex>

Everything here implements a sentence of the rule: the ticket construction is imported unchanged from
``parlay_joint_cal_gate``; the CAL legs come from LINE_CALIBRATION's own ``walk_forward``; the bootstrap is
``alt_harness_common.make_boot`` and friends; the copula is ``nba.parlay.copula`` with the pins of the rule.
Guards raise (never ``assert``) so ``python -O`` cannot strip them. Season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy import stats

from nba.parlay.copula import PairCorr, nearest_correlation
from research.eval import alt_harness_common as ahc
from research.eval import line_calibration_eval as lc
from research.eval import parlay_joint_cal_gate as gate
from research.eval import parlay_joint_cal_rule as rule

MAX_SEASON = 2024
SEASONS = (2023, 2024)
REPORT, CONSIST = 2024, 2023
BOOKS = ("draftkings", "pinnacle")
PRIMARY = "draftkings"
B_BOOT, SEED_BOOT = 5000, 2026
PIT_SEED = [2026, 7]
SHRINK_K = 200.0
STAT_ORDER = ("ast", "fg3m", "pts", "reb")  # sorted names: code order equals string order
REL = ("same_player", "teammate", "opponent")
ORTHANT_ABSEPS = ORTHANT_RELEPS = 1e-6
ORTHANT_SEED = 2026
CLIP = 1e-6
PERMILLE = 1e-9
RAW_EPS = 0.5 / 199
GAP_LIMIT, GAP_MARGIN, CONSIST_LIMIT = 0.02, 0.03, 0.03
D1_FLOOR, TOP_FLOOR = -0.005, -0.05
J_MIN_FIT = 2000
HALF_LIFE = 365.0
EXPOSED = "(2024, exposed: LC select season)"
EXPOSED_POOLED = "(2023-24, exposed: LC select season)"
SELECT_SHA = "2df7fb056f4b12de314b407eeaada816a87772fa544055edf639c529fcb85b6c"
ALT_COMMON_SHA = "44fd28db21c73769917f9c89008d476bd0648ad7444d4d9c7c8d2b89d74d8c5e"
OUT_DIR = Path("reports/prereg_parlay_joint_cal")
INPUT_PINS = {
    "data/ops/model_clv_pairs/pairs_2023.parquet": "050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569",
    "data/ops/model_clv_pairs/pairs_2024.parquet": "560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c",
    "reports/odds_history/model_rows_2023.parquet": "002e53ef1a5fae333e8ff6edc92654de9f449d29c9be1775017e65fc378eea85",
    "reports/odds_history/model_rows_2024.parquet": "ec47dcdf566eac29742f8269b8c3cf4a10845440c9b298b41a40282577b62d70",
    "reports/prereg_parlay_joint_cal/gate.json": "4b57377d6b21ebdad11714dead1dce64d809046b372a1dd848260a5132e51aeb",
}
PINNED_VERSIONS = {"numpy": "2.5.3", "polars": "1.44.2", "scipy": "1.18.1"}


def versions() -> dict[str, str]:
    import scipy

    return {"numpy": np.__version__, "polars": pl.__version__, "scipy": scipy.__version__}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------ guards (raise, never assert)


def guard_season(seasons: Sequence[int]) -> None:
    if max(seasons) > MAX_SEASON:
        raise RuntimeError(
            "season 2025 is the frozen holdout and is never loaded under PARLAY_JOINT_CAL"
        )


def guard_lead(lead_min: float) -> None:
    if lead_min < 60.0:
        raise RuntimeError(f"a T-60 snapshot is {lead_min:.2f} min before the real tip (< 60)")


def guard_fit_before(dates: np.ndarray, block_start: np.datetime64, what: str) -> None:
    """No fit row (residual row or J ticket) may be dated on or after the block's first day."""
    if dates.size and bool((dates >= block_start).any()):
        raise RuntimeError(f"{what}: a fit row is dated on or after the block start {block_start}")


@contextmanager
def heavy_lock(root: Path, enabled: bool) -> Iterator[None]:
    """``mkdir data/ops/heavy.lock`` (retry 120 s), ``rmdir`` in ``finally``."""
    if not enabled:
        yield
        return
    lock = root / "data/ops/heavy.lock"
    t0 = time.time()
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.time() - t0 > 120:
                raise RuntimeError(f"{lock} is held: retry later") from None
            time.sleep(5)
    try:
        yield
    finally:
        lock.rmdir()


# ------------------------------------------------------------------------------ residual rows and the pair correlations


def read_rows(root: Path, real: bool) -> pl.DataFrame:
    """All ``model_rows`` of both seasons, sorted (game_date, game_id, player_id, stat). ``y`` is read only when ``real``."""
    guard_season(SEASONS)
    cols = ["game_id", "player_id", "team_id", "game_date", "season", "stat", "p_ge"]
    if real:
        cols.append("y")
    parts = [
        pl.read_parquet(root / f"reports/odds_history/model_rows_{s}.parquet", columns=cols)
        for s in SEASONS
    ]
    d = pl.concat(parts, how="vertical_relaxed").with_columns(pl.col("player_id").cast(pl.Int64))
    return d.sort(["game_date", "game_id", "player_id", "stat"])


def cdf_pair(pge: list[Any], y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """F(y-1) and F(y) from the survival vector S = p_ge (S[k-1] = P(Y >= k), 0 beyond the vector):
    F(y) = 1 - S[y]; F(y-1) = 1 - S[y-1] with F(-1) = 0."""
    lo = np.zeros(len(pge))
    hi = np.zeros(len(pge))
    for i, s in enumerate(pge):
        yi = int(y[i])
        n = len(s)
        hi[i] = 1.0 - (s[yi] if yi < n else 0.0)
        lo[i] = 1.0 - (s[yi - 1] if 0 < yi <= n else 0.0) if yi >= 1 else 0.0
    return lo, hi


def pit_z(rows: pl.DataFrame, y: np.ndarray) -> np.ndarray:
    """Normal score of the randomized PIT: one ``default_rng([2026, 7]).random(N)`` over all rows (sorted order)."""
    lo, hi = cdf_pair(rows["p_ge"].to_list(), y)
    u = np.random.default_rng(PIT_SEED).random(rows.height)
    v = np.clip(lo + u * (hi - lo), 1e-9, 1 - 1e-9)
    return np.asarray(stats.norm.ppf(v))


def synthetic_y(rows: pl.DataFrame, temper: float | None = None) -> np.ndarray:
    """Rehearsal truth: the production pmf is exact, with a (game, team) factor (corr 0.09) and a (game, player)
    factor (corr 0.10) so that the copula has structure to find. No real outcome is read."""
    rng = np.random.default_rng([2026, 11])
    tkey = (rows["game_id"] + "|" + rows["team_id"].cast(pl.Utf8)).to_numpy()
    pkey = (rows["game_id"] + "|" + rows["player_id"].cast(pl.Utf8)).to_numpy()
    _, ti = np.unique(tkey, return_inverse=True)
    _, pi = np.unique(pkey, return_inverse=True)
    ft = rng.standard_normal(int(ti.max()) + 1)
    fp = rng.standard_normal(int(pi.max()) + 1)
    z = (
        0.3 * ft[ti]
        + np.sqrt(0.10) * fp[pi]
        + np.sqrt(1 - 0.09 - 0.10) * rng.standard_normal(rows.height)
    )
    u = stats.norm.cdf(z)
    y = np.zeros(rows.height)
    for i, s in enumerate(rows["p_ge"].to_list()):
        sv = np.asarray(s, dtype=float)
        if temper is not None and len(
            sv
        ):  # positive control: production is over-confident (slope ``temper``)
            sc = np.clip(sv, 1e-6, 1 - 1e-6)
            sv = lc.sigmoid(temper * lc.logit(sc))
        y[i] = float(np.sum(1.0 - sv < u[i])) if len(sv) else 0.0
    return y


class GameStats:
    """Per-game sufficient statistics of the same-game residual pairs, so the pair correlation of any month block
    is a cumulative sum over earlier games. Equals ``nba.parlay.copula.estimate_pair_corr`` on the same rows."""

    def __init__(
        self, game_date: np.ndarray, game_id: np.ndarray, player_id: np.ndarray, team_id: np.ndarray,
        stat: np.ndarray, z: np.ndarray,
    ) -> None:  # fmt: skip
        sc = np.searchsorted(np.array(STAT_ORDER), stat)
        order = np.argsort(
            game_id, kind="stable"
        )  # rows are already date-sorted; stable keeps row order inside a game
        gid = game_id[order]
        cuts = np.flatnonzero(np.r_[True, gid[1:] != gid[:-1], True])
        dates: list[np.datetime64] = []
        acc: list[np.ndarray] = []
        for a, b in zip(cuts[:-1], cuts[1:], strict=True):
            ix = order[a:b]
            n = len(ix)
            if n < 2:
                continue
            i, j = np.triu_indices(n, 1)
            si, sj = sc[ix][i], sc[ix][j]
            swap = si > sj
            f = np.where(swap, j, i)
            s2 = np.where(swap, i, j)
            zz = z[ix]
            pid, tid = player_id[ix], team_id[ix]
            rel = np.where(pid[i] == pid[j], 0, np.where(tid[i] == tid[j], 1, 2))
            c1 = sc[ix][f]
            c2 = sc[ix][s2]
            key = rel * 16 + c1 * 4 + c2
            x, y = zz[f], zz[s2]
            row = np.zeros((6, 48))
            for k, w in enumerate((np.ones_like(x), x, y, x * x, y * y, x * y)):
                row[k] = np.bincount(key, weights=w, minlength=48)
            dates.append(game_date[ix[0]])
            acc.append(row)
        d = np.array(dates, dtype="datetime64[D]")
        o = np.argsort(d, kind="stable")
        self.dates = d[o]
        self.cum = np.concatenate([np.zeros((1, 6, 48)), np.cumsum(np.stack(acc)[o], axis=0)])

    def paircorr(self, block_start: np.datetime64) -> PairCorr:
        k = int(np.searchsorted(self.dates, block_start, side="left"))
        guard_fit_before(self.dates[:k], block_start, "residual games")
        n, sx, sy, sxx, syy, sxy = self.cum[k]
        rho: dict[tuple[str, str, str], float] = {}
        cnt: dict[tuple[str, str, str], int] = {}
        for r in range(3):
            for c1 in range(4):
                for c2 in range(c1, 4):
                    key = r * 16 + c1 * 4 + c2
                    m = int(n[key])
                    if m == 0:
                        continue
                    kt = (REL[r], STAT_ORDER[c1], STAT_ORDER[c2])
                    cnt[kt] = m
                    if m < 3:
                        continue
                    vx = sxx[key] / m - (sx[key] / m) ** 2
                    vy = syy[key] / m - (sy[key] / m) ** 2
                    if vx < 1e-24 or vy < 1e-24:
                        continue
                    cov = sxy[key] / m - sx[key] / m * sy[key] / m
                    rho[kt] = float(cov / np.sqrt(vx * vy)) * m / (m + SHRINK_K)
        return PairCorr(rho=rho, n=cnt)


def build_game_stats(rows: pl.DataFrame, y: np.ndarray) -> GameStats:
    z = pit_z(rows, y)
    return GameStats(
        rows["game_date"].to_numpy().astype("datetime64[D]"), rows["game_id"].to_numpy(),
        rows["player_id"].to_numpy(), rows["team_id"].to_numpy(), rows["stat"].to_numpy().astype(str), z,
    )  # fmt: skip


# ------------------------------------------------------------------------------ joint probabilities


def ticket_R(
    stat: Sequence[str], pid: Sequence[int], tid: Sequence[int], gid: Sequence[str], pc: PairCorr
) -> np.ndarray:
    """``build_corr_matrix`` on the ticket's legs: per-ticket Higham repair, zero outside one game."""
    m = len(stat)
    R = np.eye(m)
    for i in range(m):
        for j in range(i + 1, m):
            if gid[i] != gid[j]:
                continue
            rel = (
                "same_player"
                if pid[i] == pid[j]
                else ("teammate" if tid[i] == tid[j] else "opponent")
            )
            R[i, j] = R[j, i] = pc.get(rel, stat[i], stat[j])
    if np.allclose(R, np.eye(m), atol=0.0):
        return R
    return nearest_correlation(R)


def orthant(q: np.ndarray, over: np.ndarray, R: np.ndarray) -> float:
    """P(all legs hit) = Phi_m(Phi^-1(q); D R D), D = diag(+1 over, -1 under). Identity R: the exact product.
    A fresh ``default_rng(2026)`` per call, abseps = releps = 1e-6."""
    q = np.clip(q, PERMILLE, 1 - PERMILLE)
    if np.allclose(R, np.eye(len(q)), atol=1e-12):
        return float(np.prod(q))
    d = np.where(over, 1.0, -1.0)
    cov = (d[:, None] * R) * d[None, :]
    val = stats.multivariate_normal.cdf(
        stats.norm.ppf(q), mean=np.zeros(len(q)), cov=cov, abseps=ORTHANT_ABSEPS, releps=ORTHANT_RELEPS,
        rng=np.random.default_rng(ORTHANT_SEED),
    )  # fmt: skip
    return float(np.clip(val, 0.0, 1.0))


# ------------------------------------------------------------------------------ legs with CAL


def read_pairs_cols(path: Path, season: int, real: bool) -> pl.DataFrame:
    """``lc.read_pairs`` without the outcome column unless ``real`` (rehearsal reads no real outcome)."""
    guard_season([season])
    cols = [c for c in lc.NEED_COLS if real or c != "over"]
    d = pl.read_parquet(path, columns=cols)
    d = d.filter((pl.col("snapshot_kind") == "t60") & pl.col("book").is_in(list(BOOKS)))
    if d.group_by(lc.K4).len()["len"].max() != 1:
        raise RuntimeError("more than one main line per game/player/stat/book")
    if not real:
        d = d.with_columns(pl.lit(0.0).alias("over"))
    return d.sort(lc.K4)


def load_cal(
    root: Path, real: bool, y_syn: dict[tuple[str, int, str], float] | None
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Every T-60 DraftKings/Pinnacle leg of both seasons with its walk-forward CAL probability and its outcome
    (real, or drawn from the synthetic truth). Returns the frame and the identity flags per (book, season, stat, block)."""
    frames = []
    for s in SEASONS:
        pr = read_pairs_cols(root / f"data/ops/model_clv_pairs/pairs_{s}.parquet", s, real)
        gr = lc.read_grid(root / f"reports/odds_history/model_rows_{s}.parquet", s)
        f, _ = lc.attach_grid(pr, gr)
        frames.append(f)
    d = pl.concat(frames, how="vertical_relaxed")
    D = lc.to_arrays(frames)
    if y_syn is not None:
        key = list(
            zip(d["game_id"].to_list(), d["player_id"].to_list(), d["stat"].to_list(), strict=True)
        )
        over = np.array([float(y_syn[k] > ln) for k, ln in zip(key, D["line"], strict=True)])
        D["over"] = over
        d = d.with_columns(pl.Series("over", over))
    y = D["over"]
    pcal = np.full(D["n"], np.nan)
    ident: dict[str, Any] = {}
    for book in BOOKS:
        for s in SEASONS:
            out, info = lc.walk_forward(D, y, s, book, "cal")
            m = ~np.isnan(out)
            pcal[m] = out[m]
            ident[f"{book}|{s}"] = {f"{r['stat']}|{r['block']}": bool(r["identity"]) for r in info}
    if np.isnan(pcal).any():
        raise RuntimeError("a leg without a CAL probability")
    lab = d.select(
        "game_id", "player_id", "stat", "book", "season", "over", "p_model"
    ).with_columns(pl.Series("p_cal", pcal))
    return lab, ident


def book_legs(
    root: Path, book: str, lab: pl.DataFrame, ident: dict[str, Any]
) -> tuple[dict[int, pl.DataFrame], dict[str, Any]]:
    """The gate's legs (CAL-served only) with p_cal, outcome, side, q and the raw probability of the CAL side."""
    legs_by: dict[int, pl.DataFrame] = {}
    for s in SEASONS:
        lg, dg = gate.load_legs(root, s, book)
        guard_lead(float(dg["lead_min_minutes"]))
        legs_by[s] = lg
    allv = pl.concat([legs_by[s].select(["game_date", "season", "stat"]) for s in SEASONS])
    out: dict[int, pl.DataFrame] = {}
    for s in SEASONS:
        gate_ident = {
            f"{b['stat']}|{str(b['mstart'])[:7]}": bool(b["identity"])
            for b in gate._blocks(allv, s)
        }
        if gate_ident != ident[f"{book}|{s}"]:
            raise RuntimeError(
                f"{book} {s}: the gate's identity blocks differ from LINE_CALIBRATION walk_forward's"
            )
        lg = legs_by[s].join(lab.filter((pl.col("book") == book) & (pl.col("season") == s)).drop("book", "season"),
                             on=["game_id", "player_id", "stat"], how="left")  # fmt: skip
        if lg["p_cal"].null_count() or lg["over"].null_count():
            raise RuntimeError(f"{book} {s}: a gate leg has no CAL probability or outcome")
        lg = gate.cal_served(allv, lg, s)
        pc = lg["p_cal"].to_numpy().astype(float)
        pm = lg["p_model"].to_numpy().astype(float)
        over = lg["over"].to_numpy().astype(float)
        side = pc >= 0.5
        out[s] = lg.with_columns(
            pl.Series("side_over", side),
            pl.Series("q_cal", np.where(side, pc, 1 - pc)),
            pl.Series("q_raw_side", np.clip(np.where(side, pm, 1 - pm), RAW_EPS, 1 - RAW_EPS)),
            pl.Series("hit_leg", np.where(side, over, 1 - over)),
            pl.Series("q_raw_rank", np.maximum(pm, 1 - pm)),
            pl.Series("raw_side_over", pm >= 0.5),
        )
    return out, {}


# ------------------------------------------------------------------------------ tickets


class Legs:
    def __init__(self, df: pl.DataFrame) -> None:
        self.df = df
        self.date = df["game_date"].to_numpy().astype("datetime64[D]")
        self.game = df["game_id"].to_numpy()
        self.pid = df["player_id"].to_numpy()
        self.tid = df["team_id"].to_numpy()
        self.stat = df["stat"].to_numpy().astype(str)
        self.q = df["q_cal"].to_numpy().astype(float)
        self.qraw = df["q_raw_side"].to_numpy().astype(float)
        self.over = df["side_over"].to_numpy().astype(bool)
        self.hit = df["hit_leg"].to_numpy().astype(float)
        self.pm = df["p_model"].to_numpy().astype(float)
        self.rank_raw = df["q_raw_rank"].to_numpy().astype(float)
        self.raw_over = df["raw_side_over"].to_numpy().astype(bool)
        self.hit_over = df["over"].to_numpy().astype(float)
        self.p_cal = df["p_cal"].to_numpy().astype(float)


class Copula:
    """Month-block pair correlations (cached) and the orthant prices of tickets."""

    def __init__(self, gs: GameStats) -> None:
        self.gs = gs
        self.cache: dict[Any, PairCorr] = {}
        self.calls = 0

    def pc(self, block: np.datetime64) -> PairCorr:
        if block not in self.cache:
            self.cache[block] = self.gs.paircorr(block)
        return self.cache[block]

    def price(self, L: Legs, idx: np.ndarray, tdate: np.ndarray, arm: str) -> np.ndarray:
        q_all = L.q if arm == "cal" else L.qraw
        out = np.empty(len(idx))
        for t in range(len(idx)):
            ix = idx[t]
            blk = tdate[t].astype("datetime64[M]").astype("datetime64[D]")
            R = ticket_R(L.stat[ix], L.pid[ix], L.tid[ix], L.game[ix], self.pc(blk))
            if not np.allclose(R, np.eye(len(ix)), atol=1e-12):
                self.calls += 1
            out[t] = orthant(q_all[ix], L.over[ix], R)
        return out


def priced(
    L: Legs, idx: np.ndarray, tdate: np.ndarray, kind: str, cop: Copula | None
) -> dict[str, np.ndarray]:
    hit = np.prod(L.hit[idx], axis=1)
    d = {
        "idx": idx, "tdate": tdate, "hit": hit,
        "cal_ind": np.prod(L.q[idx], axis=1), "raw_ind": np.prod(L.qraw[idx], axis=1),
    }  # fmt: skip
    if kind == "same" and cop is not None:
        d["cal_cop"] = cop.price(L, idx, tdate, "cal")
        d["raw_cop"] = cop.price(L, idx, tdate, "raw")
    else:
        d["cal_cop"], d["raw_cop"] = d["cal_ind"], d["raw_ind"]
    d["p_srv"] = d["cal_cop"]
    d["p_raw_srv"] = d["raw_cop"]
    return d


def top_union(L: Legs, m: int, cop: Copula, stride: int) -> dict[str, np.ndarray]:
    """The panel proxy: per date the TOP_N tickets with the highest p_joint_cal over cross-game candidates (ranked by
    the CAL product) and same-game candidates (ranked by the CAL copula). ``stride`` keeps every stride-th date (rehearsal)."""
    dates = np.unique(L.date)
    keep_dates = dates[::stride]
    sel = np.isin(L.date, keep_dates)
    sub = Legs(L.df.filter(pl.Series(sel)))
    parts = []
    for kind in ("cross", "same"):
        idx, td = gate.top_candidates(sub.date, sub.game, sub.q, m, kind)
        p = np.prod(sub.q[idx], axis=1) if kind == "cross" else cop.price(sub, idx, td, "cal")
        keep = gate.keep_top(idx, td, p)
        parts.append((kind, idx[keep], td[keep], p[keep]))
    idx_u = np.vstack([parts[0][1], parts[1][1]])
    td_u = np.concatenate([parts[0][2], parts[1][2]])
    p_u = np.concatenate([parts[0][3], parts[1][3]])
    is_same = np.r_[np.zeros(len(parts[0][1]), bool), np.ones(len(parts[1][1]), bool)]
    k = gate.keep_top(idx_u, td_u, p_u)
    idx_u, td_u, p_u, is_same = idx_u[k], td_u[k], p_u[k], is_same[k]
    # re-price raw arm and hit on the subframe
    hit = np.prod(sub.hit[idx_u], axis=1)
    raw_srv = np.empty(len(idx_u))
    for t in range(len(idx_u)):
        if is_same[t]:
            ix = idx_u[t]
            blk = td_u[t].astype("datetime64[M]").astype("datetime64[D]")
            R = ticket_R(sub.stat[ix], sub.pid[ix], sub.tid[ix], sub.game[ix], cop.pc(blk))
            raw_srv[t] = orthant(sub.qraw[ix], sub.over[ix], R)
        else:
            raw_srv[t] = float(np.prod(sub.qraw[idx_u[t]]))
    # the 8th-ranked-leg boundary count (m1): same-game tickets using a leg ranked 8th within its game on the date
    n8 = 0
    for tt in np.flatnonzero(is_same):
        ix = idx_u[tt]
        g = sub.game[ix[0]]
        d0 = sub.date[ix[0]]
        pool = np.flatnonzero((sub.game == g) & (sub.date == d0))
        order = pool[np.lexsort((pool, -sub.q[pool]))]
        ranks = {int(v): r for r, v in enumerate(order)}
        n8 += int(any(ranks[int(v)] == gate.TOP_UNITS_SAME - 1 for v in ix))
    return {
        "idx": idx_u, "tdate": td_u, "hit": hit, "p_srv": p_u, "p_raw_srv": raw_srv,
        "is_same": is_same, "n_boundary8": np.array([n8]),
    }  # fmt: skip


def raw_ranked_top(L: Legs, m: int, stride: int) -> dict[str, np.ndarray]:
    """What the panel would have shown without CAL: top 3 per date ranked by RAW-IND with the RAW side
    (cross-game candidates), scored under RAW-IND and CAL-IND (descriptive)."""
    dates = np.unique(L.date)
    sub = Legs(L.df.filter(pl.Series(np.isin(L.date, dates[::stride]))))
    idx, td = gate.top_candidates(sub.date, sub.game, sub.rank_raw, m, "cross")
    p = np.prod(sub.rank_raw[idx], axis=1)
    keep = gate.keep_top(idx, td, p)
    idx, td, p = idx[keep], td[keep], p[keep]
    side_over = sub.raw_over[idx]
    over = sub.hit_over[idx]
    hit = np.prod(np.where(side_over, over, 1 - over), axis=1)
    pc = sub.p_cal[idx]
    cal_side = np.prod(np.where(side_over, pc, 1 - pc), axis=1)
    return {"tdate": td, "hit": hit, "p_raw": p, "p_cal_same_tickets": cal_side}


# ------------------------------------------------------------------------------ statistics and words


class Clusters:
    def __init__(self, dates_by_season: dict[int, np.ndarray]) -> None:
        self.dates = dates_by_season
        self.mats = ahc.make_boot(
            {s: len(d) for s, d in dates_by_season.items()}, B_BOOT, SEED_BOOT
        )

    def totals(
        self, season: int, tdate: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        pos = np.searchsorted(self.dates[season], tdate)
        if (pos >= len(self.dates[season])).any() or (
            self.dates[season][np.minimum(pos, len(self.dates[season]) - 1)] != tdate
        ).any():
            raise RuntimeError("a ticket date is not a CAL-served slate date of its season")
        n = len(self.dates[season])
        return ahc.cluster_totals(pos, n, values), ahc.cluster_totals(pos, n)

    def stat(self, per_season: dict[int, tuple[np.ndarray, np.ndarray]]) -> dict[str, Any]:
        """per_season[s] = (tdate, values) -> ratio estimate of sum(values)/count on date totals; strata = given seasons."""
        parts = []
        for s, (td, v) in per_season.items():
            num, den = self.totals(s, td, v)
            parts.append((self.mats[s], num, den))
        point, boot = ahc.boot_ratio(parts)
        lo, hi = ahc.percentile_interval(boot)
        return {
            "point": point, "lo": lo, "hi": hi, "p_two_sided": ahc.p_two_sided(boot),
            "p_le0_of_minus": ahc.p_le0(-boot), "n": int(sum(len(v) for _, v in per_season.values())),
            "boot": boot,
        }  # fmt: skip


def ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    pc = np.clip(p, CLIP, 1 - CLIP)
    return np.asarray(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)))


def word_t1(g: dict[str, Any], g23: float) -> str:
    G, lo, hi = g["point"], g["lo"], g["hi"]
    if abs(G) <= GAP_LIMIT and lo >= -GAP_MARGIN and hi <= GAP_MARGIN and abs(g23) <= CONSIST_LIMIT:
        return "CALIBRATED"
    if G < -GAP_LIMIT and hi < 0 and g23 < 0:
        return "OVERCONFIDENT?"
    if G > GAP_LIMIT and lo > 0 and g23 > 0:
        return "UNDERCONFIDENT?"
    return "INCONCLUSIVE"


def word_t2(d: dict[str, Any], d23: float) -> str:
    D, lo, hi = d["point"], d["lo"], d["hi"]
    if D <= D1_FLOOR and hi < 0 and d23 < 0:
        return "CAL HELPS THE JOINT?"
    if lo > D1_FLOOR:
        return "CAL DOES NOT HELP"
    return "INCONCLUSIVE"


def word_t3(g: dict[str, Any], g23: float, g24: float) -> str:
    G, lo, hi = g["point"], g["lo"], g["hi"]
    if G <= TOP_FLOOR and hi < 0 and g23 < 0 and g24 < 0:
        return "TOP OVERCONFIDENT?"
    if G >= -TOP_FLOOR and lo > 0 and g23 > 0 and g24 > 0:
        return "TOP UNDERCONFIDENT?"
    return "INCONCLUSIVE"


def finalize(word: str, q: float, exposed: str) -> str:
    """Directional words need BH q < 0.05 (marked ``?`` until applied) and carry the exposure label."""
    if word.endswith("?"):
        word = word[:-1] if q < 0.05 else "INCONCLUSIVE"
    return f"{word} {exposed}"


def public(s: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in s.items() if k != "boot"}


def reliability(p: np.ndarray, hit: np.ndarray, bands: int = 10) -> list[dict[str, float]]:
    edges = np.quantile(p, np.linspace(0, 1, bands + 1))
    code = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bands - 1)
    return [
        {
            "band": b,
            "n": int((code == b).sum()),
            "mean_p": float(p[code == b].mean()),
            "hit": float(hit[code == b].mean()),
        }
        for b in range(bands)
        if (code == b).any()
    ]


def slope(p: np.ndarray, hit: np.ndarray) -> float:
    X = np.column_stack([np.ones(len(p)), lc.logit(np.clip(p, CLIP, 1 - CLIP))])
    return float(lc.fit_logistic(X, hit, ridge=1e-8)[1])


# ------------------------------------------------------------------------------ one book


def evaluate_book(
    book: str, legs_by: dict[int, pl.DataFrame], gs: GameStats, stride: int, gate_json: dict[str, Any] | None, out_dir: Path,
) -> dict[str, Any]:  # fmt: skip
    L = {s: Legs(legs_by[s]) for s in SEASONS}
    cl = Clusters({s: np.unique(L[s].date) for s in SEASONS})
    cop = {s: Copula(gs) for s in SEASONS}
    res: dict[str, Any] = {"book": book, "cells": {}, "top": {}}
    tk: dict[tuple[int, str, int], dict[str, np.ndarray]] = {}
    for s in SEASONS:
        for kind in ("cross", "same"):
            for m in gate.SIZES:
                t0 = time.time()
                seed = [gate.ROOT_SEED, s, m, 1 if kind == "cross" else 2]
                fn = gate.cross_tickets if kind == "cross" else gate.same_tickets
                idx, td = fn(L[s].date, L[s].game, m, gate.PER_DATE, seed)
                if gate_json is not None:
                    e = gate_json["books"][book]["cells"][f"{kind}_size{m}"]["seasons"][str(s)]
                    fp = gate.ticket_fingerprint(L[s].df, idx)
                    if len(idx) != e["tickets"] or fp != e["fingerprint_sha256"]:
                        raise RuntimeError(f"{book} {kind} {m} {s}: tickets differ from gate.json")
                tk[(s, kind, m)] = priced(L[s], idx, td, kind, cop[s])
                _log(
                    f"{book} {s} {kind} m={m}: {len(idx)} tickets priced in {time.time() - t0:.0f}s ({cop[s].calls} orthant calls so far)"
                )
    # ---- T1 / T2 per cell
    fam_a: list[tuple[str, float]] = []
    fam_b: list[tuple[str, float]] = []
    cells: dict[str, Any] = {}
    for kind in ("cross", "same"):
        for m in gate.SIZES:
            name = f"{kind}_size{m}"
            c: dict[str, Any] = {}
            for s in SEASONS:
                t = tk[(s, kind, m)]
                c[f"G_{s}"] = cl.stat({s: (t["tdate"], t["hit"] - t["p_srv"])})
                c[f"D1_{s}"] = cl.stat(
                    {s: (t["tdate"], ll(t["p_srv"], t["hit"]) - ll(t["p_raw_srv"], t["hit"]))}
                )
                c[f"G_raw_{s}"] = cl.stat({s: (t["tdate"], t["hit"] - t["p_raw_srv"])})
                c[f"tickets_{s}"] = len(t["hit"])
                c[f"dates_{s}"] = int(len(np.unique(t["tdate"])))
            cells[name] = c
            fam_a.append((name, c[f"G_{REPORT}"]["p_two_sided"]))
            fam_b.append((name, c[f"D1_{REPORT}"]["p_le0_of_minus"]))
    qa = dict(zip([n for n, _ in fam_a], ahc.bh_q([p for _, p in fam_a]), strict=True))
    qb = dict(zip([n for n, _ in fam_b], ahc.bh_q([p for _, p in fam_b]), strict=True))
    for name, c in cells.items():
        g, d = c[f"G_{REPORT}"], c[f"D1_{REPORT}"]
        c["T1_word"] = finalize(word_t1(g, c[f"G_{CONSIST}"]["point"]), qa[name], EXPOSED)
        c["T1_q"] = qa[name]
        c["T2_word"] = finalize(word_t2(d, c[f"D1_{CONSIST}"]["point"]), qb[name], EXPOSED)
        c["T2_q"] = qb[name]
    # ---- descriptive: D2 (copula vs independence), J, reliability, slopes, Brier
    for kind in ("same",):
        for m in gate.SIZES:
            name = f"{kind}_size{m}"
            t = tk[(REPORT, kind, m)]
            cells[name]["D2_cop_minus_ind_ll"] = public(
                cl.stat(
                    {REPORT: (t["tdate"], ll(t["cal_cop"], t["hit"]) - ll(t["cal_ind"], t["hit"]))}
                )
            )
    for name, c in cells.items():
        kind, m = name.split("_size")[0], int(name.split("_size")[1])
        t = tk[(REPORT, kind, m)]
        c["reliability_2024"] = reliability(t["p_srv"], t["hit"])
        c["slope_2024"] = slope(t["p_srv"], t["hit"])
        c["brier_2024"] = {
            "served": float(np.mean((t["p_srv"] - t["hit"]) ** 2)),
            "raw": float(np.mean((t["p_raw_srv"] - t["hit"]) ** 2)),
        }
        c["J"] = j_arm(cl, tk, kind, m)
        for key in list(c):
            if isinstance(c[key], dict) and "boot" in c[key]:
                c[key] = public(c[key])
    res["cells"] = cells
    # ---- slices on the 2024 U-ALL tickets (label only, no CI)
    res["slices_2024"] = slices(L[REPORT], tk)
    # ---- T3: the panel proxy
    fam_c: list[float] = []
    top: dict[str, Any] = {}
    for m in gate.SIZES:
        t0 = time.time()
        tt = {s: top_union(L[s], m, cop[s], stride) for s in SEASONS}
        _log(f"{book} TOP m={m} built in {time.time() - t0:.0f}s (stride {stride})")
        per = {s: (tt[s]["tdate"], tt[s]["hit"] - tt[s]["p_srv"]) for s in SEASONS}
        pooled = cl.stat(per)
        by_s = {s: cl.stat({s: per[s]}) for s in SEASONS}
        allm = {s: _uall_mix(tk, s, m) for s in SEASONS}
        wc = cl.stat({REPORT: (tt[REPORT]["tdate"], tt[REPORT]["hit"] - tt[REPORT]["p_srv"])})
        al = cl.stat({REPORT: allm[REPORT]})
        top[f"size{m}"] = {
            "pooled": public(pooled),
            "by_season": {str(s): public(v) for s, v in by_s.items()},
            "tickets": {str(s): int(len(tt[s]["hit"])) for s in SEASONS},
            "share_same_game": {str(s): float(tt[s]["is_same"].mean()) for s in SEASONS},
            "boundary8_same_game_tickets": {str(s): int(tt[s]["n_boundary8"][0]) for s in SEASONS},
            "winners_curse_2024_top_minus_uall_gap": float(wc["point"] - al["point"]),
            "reliability_2024": reliability(tt[REPORT]["p_srv"], tt[REPORT]["hit"], 5),
            "raw_ranked_top3_2024": _raw_ranked(L[REPORT], m, stride, cl),
        }
        top[f"size{m}"]["_p"] = pooled["p_two_sided"]
        top[f"size{m}"]["_g"] = (pooled, by_s[CONSIST]["point"], by_s[REPORT]["point"])
        fam_c.append(pooled["p_two_sided"])
    qc = ahc.bh_q(fam_c)
    for (_key, v), q in zip(top.items(), qc, strict=True):
        pooled, g23, g24 = v.pop("_g")
        v.pop("_p")
        v["T3_word"] = finalize(word_t3(pooled, g23, g24), q, EXPOSED_POOLED)
        v["T3_q"] = q
    res["top"] = top
    res["orthant_calls"] = {str(s): cop[s].calls for s in SEASONS}
    (out_dir / f"{book}.json").write_text(
        json.dumps(res, indent=1, sort_keys=True, default=str) + "\n"
    )
    return res


def _uall_mix(
    tk: dict[tuple[int, str, int], dict[str, np.ndarray]], s: int, m: int
) -> tuple[np.ndarray, np.ndarray]:
    td = np.concatenate([tk[(s, k, m)]["tdate"] for k in ("cross", "same")])
    v = np.concatenate([tk[(s, k, m)]["hit"] - tk[(s, k, m)]["p_srv"] for k in ("cross", "same")])
    return td, v


def _raw_ranked(L: Legs, m: int, stride: int, cl: Clusters) -> dict[str, Any]:
    r = raw_ranked_top(L, m, stride)
    return {
        "n": int(len(r["hit"])), "hit_rate": float(r["hit"].mean()), "mean_raw_ind": float(r["p_raw"].mean()),
        "mean_cal_ind_of_same_tickets": float(r["p_cal_same_tickets"].mean()),
        "gap_raw": public(cl.stat({REPORT: (r["tdate"], r["hit"] - r["p_raw"])})),
        "gap_cal": public(cl.stat({REPORT: (r["tdate"], r["hit"] - r["p_cal_same_tickets"])})),
    }  # fmt: skip


def j_arm(
    cl: Clusters, tk: dict[tuple[int, str, int], dict[str, np.ndarray]], kind: str, m: int
) -> dict[str, Any]:
    """Optional joint-level recalibration: logit P = a + b logit p_served, walk-forward by month on earlier tickets of
    the cell (both seasons), scored on 2024 only. Descriptive."""
    fit_t = np.concatenate([tk[(s, kind, m)]["tdate"] for s in SEASONS])
    fit_p = np.concatenate([tk[(s, kind, m)]["p_srv"] for s in SEASONS])
    fit_h = np.concatenate([tk[(s, kind, m)]["hit"] for s in SEASONS])
    t = tk[(REPORT, kind, m)]
    pj = np.empty(len(t["hit"]))
    blocks = t["tdate"].astype("datetime64[M]").astype("datetime64[D]")
    n_ident = 0
    for b0 in np.unique(blocks):
        sc = blocks == b0
        fm = fit_t < b0
        guard_fit_before(fit_t[fm], b0, "J fit tickets")
        if int(fm.sum()) < J_MIN_FIT:
            pj[sc] = t["p_srv"][sc]
            n_ident += int(sc.sum())
            continue
        age = (b0 - fit_t[fm]).astype("timedelta64[D]").astype(float)
        w = 0.5 ** (age / HALF_LIFE)
        X = np.column_stack([np.ones(int(fm.sum())), lc.logit(np.clip(fit_p[fm], CLIP, 1 - CLIP))])
        beta = lc.fit_logistic(X, fit_h[fm], ridge=1.0, w=w)
        pj[sc] = lc.sigmoid(beta[0] + beta[1] * lc.logit(np.clip(t["p_srv"][sc], CLIP, 1 - CLIP)))
    return {
        "identity_tickets": n_ident,
        "gap": public(cl.stat({REPORT: (t["tdate"], t["hit"] - pj)})),
        "ll_minus_served": public(
            cl.stat({REPORT: (t["tdate"], ll(pj, t["hit"]) - ll(t["p_srv"], t["hit"]))})
        ),
    }


def slices(L: Legs, tk: dict[tuple[int, str, int], dict[str, np.ndarray]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("cross", "same"):
        for m in gate.SIZES:
            t = tk[(REPORT, kind, m)]
            idx = t["idx"]
            gap = t["hit"] - t["p_srv"]
            sides = L.over[idx]
            masks = {
                "all_under": ~sides.any(axis=1), "all_over": sides.all(axis=1),
                "mixed_side": sides.any(axis=1) & ~sides.all(axis=1),
                "same_stat": (L.stat[idx] == L.stat[idx][:, [0]]).all(axis=1),
                "raw_joint_ge_0.50": t["p_raw_srv"] >= 0.5,
            }  # fmt: skip
            months = t["tdate"].astype("datetime64[M]")
            for mo in np.unique(months):
                masks[f"month_{str(mo)}"] = months == mo
            if kind == "same":
                pid = L.pid[idx]
                masks["has_same_player_pair"] = np.array([len(set(r)) < len(r) for r in pid])
            out[f"{kind}_size{m}"] = {
                k: {"n": int(v.sum()), "gap": float(gap[v].mean()) if v.any() else None}
                for k, v in masks.items()
            }
    return out


# ------------------------------------------------------------------------------ run


def run(
    root: Path,
    real: bool,
    stride: int,
    books: Sequence[str],
    sha: str | None,
    out_dir: Path,
    temper: float | None = None,
) -> dict[str, Any]:
    guard_season(SEASONS)
    pf = ahc.Preflight()
    pin: dict[str, str] = {}
    if real:
        s = rule.require_frozen(sha)
        pin = rule.require_harness_pinned(s)
    pf.add("season <= 2024", True, "")
    pf.add(
        "alt_harness_common.py sha256 pinned",
        ahc.file_sha256(Path(ahc.__file__)) == ALT_COMMON_SHA,
        ahc.file_sha256(Path(ahc.__file__))[:8],
    )
    sel_path = root / "reports/prereg_line_calibration/select.json"
    pf.add(
        "select.json sha256 pinned",
        ahc.file_sha256(sel_path) == SELECT_SHA,
        ahc.file_sha256(sel_path)[:8],
    )
    gate_path = root / "reports/prereg_parlay_joint_cal/gate.json"
    gate_json = json.loads(gate_path.read_text()) if gate_path.exists() else None
    pf.add("gate.json present", gate_json is not None, str(gate_path))
    if gate_json is not None:
        pf.add(
            "gate code sha256 equals gate.json",
            gate_json["code_sha256"].get(gate.CODE_FILES[0])
            == ahc.file_sha256(Path(gate.__file__)),
            "",
        )
    # R2: the inputs are pinned by sha256 before any label is read
    for rel, want in INPUT_PINS.items():
        got = ahc.file_sha256(root / rel)
        pf.add(f"{rel} sha256 pinned", got == want, got[:8])
    if real:
        pf.add(
            "scipy/numpy/polars versions equal the rule's",
            versions() == PINNED_VERSIONS,
            json.dumps(versions()),
        )
    # R1: in score mode every hash check and the freeze come first; labels are read only after pf.require()
    ysyn_map = None
    rows: pl.DataFrame | None = None
    y_arr = np.zeros(0)
    if real:
        pf.require()
        sel = json.loads(sel_path.read_text())
        lab, ident = load_cal(root, True, None)
        for book in BOOKS:
            sub = lab.filter((pl.col("book") == book) & (pl.col("season") == REPORT))
            over = sub["over"].to_numpy()
            cal_ll = float(lc.ll(sub["p_cal"].to_numpy(), over).mean())
            pm = np.clip(sub["p_model"].to_numpy().astype(float), lc.EPS, 1 - lc.EPS)
            a0_ll = float(lc.ll(pm, over).mean())
            pf.add(
                f"{book} 2024 CAL log loss equals select.json to 1e-9",
                abs(cal_ll - sel[book]["ll"]["CAL"]) < 1e-9,
                f"{cal_ll!r}",
            )
            pf.add(
                f"{book} 2024 A0 log loss equals select.json to 1e-9",
                abs(a0_ll - sel[book]["ll"]["A0"]) < 1e-9,
                f"{a0_ll!r}",
            )
        pf.require()
        rows = read_rows(root, True)
        y_arr = rows["y"].to_numpy().astype(float)
    else:
        rows = read_rows(root, False)
        y_arr = synthetic_y(rows, temper)
        ysyn_map = {
            (g, p, st): float(v)
            for g, p, st, v in zip(
                rows["game_id"].to_list(),
                rows["player_id"].to_list(),
                rows["stat"].to_list(),
                y_arr,
                strict=True,
            )
        }
        lab, ident = load_cal(root, False, ysyn_map)
    gs = build_game_stats(rows, y_arr)
    pf.require()
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "mode": "score" if real else "rehearse (synthetic labels; no real outcome read)",
        "stride": stride, "versions": versions(), "preflight": pf.as_dicts(), "harness_pin": pin,
        "started": time.strftime("%a %b %d %H:%M:%S %Z %Y"), "books": {},
    }  # fmt: skip
    for book in books:
        legs_by, _ = book_legs(root, book, lab, ident)
        result["books"][book] = evaluate_book(book, legs_by, gs, stride, gate_json, out_dir)
    result["finished"] = time.strftime("%a %b %d %H:%M:%S %Z %Y")
    return result


def rehearsal_verdict(res: dict[str, Any]) -> dict[str, Any]:
    """Rule step (5): no OVERCONFIDENT / UNDERCONFIDENT word in any cell (or TOP); CALIBRATED or INCONCLUSIVE otherwise."""
    bad: list[str] = []
    words: dict[str, str] = {}
    for book, b in res["books"].items():
        if book != PRIMARY:
            continue
        for name, c in b["cells"].items():
            words[f"{name}.T1"] = c["T1_word"]
            words[f"{name}.T2"] = c["T2_word"]
            if "OVERCONFIDENT" in c["T1_word"] or "UNDERCONFIDENT" in c["T1_word"]:
                bad.append(f"{name}.T1={c['T1_word']}")
        for name, t in b["top"].items():
            words[f"top_{name}.T3"] = t["T3_word"]
            if "OVERCONFIDENT" in t["T3_word"] or "UNDERCONFIDENT" in t["T3_word"]:
                bad.append(f"top_{name}={t['T3_word']}")
    return {"pass": not bad, "violations": bad, "words": words}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["rehearse", "score"])
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--sha", default=None)
    ap.add_argument(
        "--stride",
        type=int,
        default=1,
        help="TOP slice: keep every stride-th slate date (rehearsal only)",
    )
    ap.add_argument("--books", nargs="+", default=list(BOOKS))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--truth-slope",
        type=float,
        default=None,
        help="rehearsal positive control: the synthetic truth is production tempered to this logit slope",
    )
    a = ap.parse_args(argv)
    real = a.stage == "score"
    if real and a.stride != 1:
        raise SystemExit("score runs the full slate: --stride must be 1")
    out = a.out or (OUT_DIR / ("results" if real else "rehearsal"))
    with heavy_lock(a.data_root, enabled=real):
        res = run(a.data_root, real, a.stride, a.books, a.sha, out, a.truth_slope)
    if not real:
        res["rehearsal_verdict"] = rehearsal_verdict(res)
    (out / "report.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
