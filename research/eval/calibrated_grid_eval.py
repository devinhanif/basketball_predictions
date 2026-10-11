# ruff: noqa: E501
"""CALIBRATED_GRID evaluation (rule: docs/prereg/CALIBRATED_GRID.md).

    python -W ignore -m research.eval.calibrated_grid_eval gate  --doc docs/prereg/CALIBRATED_GRID.md --allow-draft
    python -W ignore -m research.eval.calibrated_grid_eval smoke --doc docs/prereg/CALIBRATED_GRID.md --sha <64 hex>
    python -W ignore -m research.eval.calibrated_grid_eval run   --doc docs/prereg/CALIBRATED_GRID.md --sha <64 hex>
    python -W ignore -m research.eval.calibrated_grid_eval report --doc docs/prereg/CALIBRATED_GRID.md --sha <64 hex>

``--allow-draft`` (gate stage only) skips the hash equality check and records the computed hash.
Every other stage refuses unless ``--sha`` equals the sha256 of everything above the
``=== RESULTS BELOW ===`` line.

Inputs: the stored production OOF rows ``reports/odds_history/model_rows_<season>.parquet`` (the
ODDS_HISTORY stage-1 output; ``p_ge[i] = P(Y >= i+1)``, 199-sample integer grid) and the DK main-line
rows ``reports/odds_history/market_rows_<season>.parquet``. No database is opened except an optional
read-only cross-check of the pair set in the gate. Season 2025 is never loaded (asserted).

Maps (all on the exceedance probabilities p_k = P(Y >= k), k = 1..K; exact 0 and 1 are left as they are):
  C1 temperature  z' = z / T             (one scalar per stat), z = logit(clip(p))
  C2 Platt        z' = a + b z           (two parameters per stat)
  C3 isotonic     on P(over L) at the DK main lines only (not applied to the grid)
  A4 control      C1 fit on the SAME trailing window with the outcomes permuted
Fit objective for C1/C2: log loss over every interior (row, k) pair of the window, target 1[y >= k].
Window: rows with game_date in [block_start - 60 d, block_start), per stat; blocks are the calendar
months production refits on; fewer than 3,000 rows (or 3,000 pairs for C3) -> identity.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.optimize import minimize, minimize_scalar
from scipy.special import expit

DEFAULT_DOC = Path("docs/prereg/CALIBRATED_GRID.md")
PREREG = DEFAULT_DOC
MARKER = "=== RESULTS BELOW ==="
ROWS_DIR = Path("reports/odds_history")
OUT_DIR = Path("reports/prereg_calibrated_grid")
REPORT_PATH = Path("reports/prereg_calibrated_grid.md")
LOCK = Path("data/ops/heavy.lock")

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEASONS = (SELECT_SEASON, REPORT_SEASON)
MAX_SEASON = 2024  # season 2025 is the frozen holdout: never loaded
WINDOW_DAYS = 60
MIN_ROWS = 3000
C3_MIN_PAIRS = 2000  # frozen amendment: C3 is fit on DK pairs, minimum 2,000
N_Q = 199
MODEL_EPS = 0.5 / 199
T_BOUNDS = (0.25, 4.0)  # temperature
B_BOUNDS = (0.25, 4.0)  # Platt slope
A_BOUNDS = (-2.0, 2.0)  # Platt intercept
N_BOOT = 2000
BOOT_SEED = 0
A0_TOL = 1e-6
LINE_FLOOR = -0.002
CRPS_FLOOR = -0.005
CONTROL_TOL = -0.0005
ALPHA = 0.05
BOOK = "draftkings"
KINDS = ("t60", "t5")
PRIMARY_KIND = "t60"
SHUFFLE_SEED = 20261011
ARMS_GRID = ("A1", "A2")
ARMS_LINE = ("A1", "A2", "A3")
ARM_LABEL = {
    "A0": "production",
    "A1": "C1 temperature",
    "A2": "C2 Platt",
    "A3": "C3 isotonic (lines)",
    "A4": "control: C1 on shuffled window",
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze + guards


def frozen_sha(path: Path | None = None) -> str:
    """sha256 of everything above the marker line (the awk command in the doc header)."""
    h = hashlib.sha256()
    for line in (path or PREREG).read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        h.update(line.encode())
    return h.hexdigest()


def configure(doc: Path) -> None:
    global PREREG
    PREREG = doc


def require_frozen(expected: str | None, allow_draft: bool = False) -> str:
    sha = frozen_sha(PREREG)
    if allow_draft:
        return sha
    if not expected or len(expected) != 64:
        raise SystemExit("STOP: --sha <64 hex> (the committed freeze hash) is required")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def guard_season(season: int) -> None:
    if season not in SEASONS:
        raise SystemExit(
            f"STOP: season {season} is outside the study (2023, 2024); 2025 is the holdout"
        )


class HeavyLock:
    """mkdir lock (repo convention): retry every 120 s, always released."""

    def __init__(self, path: Path = LOCK, retry_s: float = 120.0) -> None:
        self.path, self.retry_s = path, retry_s

    def __enter__(self) -> HeavyLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                _log(f"heavy.lock held; retry in {self.retry_s:.0f}s")
                time.sleep(self.retry_s)

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(OSError):
            self.path.rmdir()


# --------------------------------------------------------------------------- data


@dataclass
class StatRows:
    """One stat, one season of production OOF rows (arrays aligned)."""

    stat: str
    season: int
    game_id: np.ndarray
    player_id: np.ndarray
    date: np.ndarray  # datetime64[D]
    y: np.ndarray
    pge: np.ndarray  # [n, K], pge[:, k-1] = P(Y >= k)


@dataclass
class PairRows:
    """DK main-line pairs of one (stat, season, kind) joined to the OOF row index."""

    kind: str
    row: np.ndarray  # index into StatRows
    line: np.ndarray
    p_mkt: np.ndarray  # multiplicative de-vig P(over)
    p0: np.ndarray  # A0 model P(over L) (push-free)
    over: np.ndarray
    status: np.ndarray


def load_stat_rows(season: int, stat: str, rows_dir: Path = ROWS_DIR) -> StatRows:
    guard_season(season)
    meta = json.loads((rows_dir / f"model_rows_{season}.meta.json").read_text())
    if not meta.get("a0_ok"):
        raise SystemExit(f"STOP: model rows {season}: the A0 self-check did not pass")
    df = pl.read_parquet(rows_dir / f"model_rows_{season}.parquet").filter(pl.col("stat") == stat)
    assert int(df["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    df = df.sort(["game_date", "game_id", "player_id"])
    width = int(df["p_ge"].list.len().max())  # type: ignore[arg-type]
    pge = df["p_ge"].list.to_array(width).to_numpy().astype(np.float64)
    return StatRows(
        stat=stat,
        season=season,
        game_id=df["game_id"].cast(pl.Utf8).to_numpy(),
        player_id=df["player_id"].cast(pl.Int64).to_numpy(),
        date=df["game_date"].cast(pl.Date).to_numpy().astype("datetime64[D]"),
        y=df["y"].to_numpy().astype(np.float64),
        pge=pge,
    )


def line_pairs(rows: StatRows, market: pl.DataFrame, kind: str, book: str = BOOK) -> PairRows:
    """The single place a prediction meets a price: model_p_over (ODDS_HISTORY's own function) on
    the A0 grid, status 0 pairs only (pushes and odd lines dropped, as in ODDS_HISTORY)."""
    from research.eval.odds_history_eval import model_p_over

    idx = pl.DataFrame(
        {"game_id": rows.game_id, "player_id": rows.player_id, "_ri": np.arange(len(rows.y))}
    )
    m = market.filter(
        (pl.col("stat") == rows.stat) & (pl.col("book") == book) & (pl.col("snapshot_kind") == kind)
    ).with_columns(pl.col("player_id").cast(pl.Int64))
    j = m.join(idx, on=["game_id", "player_id"], how="inner")
    if j.is_empty():
        z = np.zeros(0)
        return PairRows(kind, z.astype(int), z, z, z, z, z.astype(int))
    ri = j["_ri"].to_numpy()
    line = j["line"].to_numpy().astype(float)
    p, over, status = model_p_over(rows.pge[ri], line, rows.y[ri])
    keep = status == 0
    return PairRows(
        kind,
        ri[keep],
        line[keep],
        j["p_over_mult"].to_numpy().astype(float)[keep],
        p[keep],
        over[keep],
        status[keep],
    )


def line_pairs_dedup_check(pr: PairRows) -> int:
    """Number of duplicate (row, line) pairs (a second quote of the same player-game)."""
    return int(
        len(pr.row) - len(np.unique(np.stack([pr.row, pr.line]), axis=1, return_index=True)[1])
    )


# --------------------------------------------------------------------------- maps


def logit(p: np.ndarray) -> np.ndarray:
    pc = np.clip(p, MODEL_EPS, 1.0 - MODEL_EPS)
    return np.asarray(np.log(pc) - np.log1p(-pc))


@dataclass(frozen=True)
class GridMap:
    """z' = a + b z on interior probabilities; identity when ``identity``."""

    family: str
    a: float = 0.0
    b: float = 1.0
    identity: bool = True
    n_rows: int = 0
    n_pairs: int = 0
    reason: str = ""

    @property
    def temperature(self) -> float:
        return 1.0 / self.b


IDENTITY = GridMap("identity")


def interior_pairs(pge: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Logits and 0/1 targets 1[y >= k] of every (row, k) with 0 < p < 1."""
    ks = np.arange(1, pge.shape[1] + 1, dtype=float)
    m = (pge > 0.0) & (pge < 1.0)
    x = logit(pge[m])
    t = (y[:, None] >= ks[None, :])[m].astype(np.float64)
    return x, t


def _ll(z: np.ndarray, t: np.ndarray) -> float:
    return float(np.mean(np.logaddexp(0.0, z) - t * z))


def fit_temperature(pge: np.ndarray, y: np.ndarray, family: str = "C1") -> GridMap:
    x, t = interior_pairs(pge, y)
    if x.size == 0:
        return GridMap(family, reason="no interior pairs", n_rows=len(y))
    lo, hi = np.log(T_BOUNDS[0]), np.log(T_BOUNDS[1])
    # parametrise u = log T; z' = z / T
    res = minimize_scalar(
        lambda u: _ll(x * np.exp(-u), t), bounds=(lo, hi), method="bounded", options={"xatol": 1e-8}
    )
    return GridMap(
        family, a=0.0, b=float(np.exp(-res.x)), identity=False, n_rows=len(y), n_pairs=int(x.size)
    )


def fit_platt(pge: np.ndarray, y: np.ndarray) -> GridMap:
    x, t = interior_pairs(pge, y)
    if x.size == 0:
        return GridMap("C2", reason="no interior pairs", n_rows=len(y))

    def f(w: np.ndarray) -> tuple[float, np.ndarray]:
        z = w[0] + w[1] * x
        r = expit(z) - t
        return _ll(z, t), np.array([r.mean(), (r * x).mean()])

    res = minimize(
        f, x0=np.array([0.0, 1.0]), jac=True, method="L-BFGS-B", bounds=[A_BOUNDS, B_BOUNDS]
    )
    return GridMap(
        "C2",
        a=float(res.x[0]),
        b=float(res.x[1]),
        identity=False,
        n_rows=len(y),
        n_pairs=int(x.size),
    )


def renormalise(p: np.ndarray) -> np.ndarray:
    """Exceedance probabilities non-increasing in k and inside [0, 1]."""
    return np.asarray(np.clip(np.minimum.accumulate(p, axis=1), 0.0, 1.0))


def apply_map(pge: np.ndarray, m: GridMap) -> np.ndarray:
    if m.identity:
        return pge
    out = pge.copy()
    mask = (pge > 0.0) & (pge < 1.0)
    out[mask] = expit(m.a + m.b * logit(pge[mask]))
    return renormalise(out)


def crps_survival(pge: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Exact integer-support CRPS per row from the survival vector: sum_k (F(k) - 1[y <= k])^2,
    k = 0..K-1, plus (y - K)^+ beyond the grid (F = 1 there)."""
    K = pge.shape[1]
    F = 1.0 - pge
    ks = np.arange(0, K, dtype=float)
    ind = (y[:, None] <= ks[None, :]).astype(float)
    return np.asarray(((F - ind) ** 2).sum(axis=1) + np.maximum(y - K, 0.0))


def quantiles_from_pge(pge: np.ndarray, n_q: int = N_Q, chunk: int = 1000) -> np.ndarray:
    """Rebuild the production integer quantile grid [n, n_q] from the stored survival vector
    (P(Y >= k) = count/n_q exactly on the equal-weight sample)."""
    cnt = np.rint(pge * n_q).astype(np.int32)
    j = n_q - np.arange(n_q, dtype=np.int32)
    out = np.empty((pge.shape[0], n_q), dtype=np.int16)
    for s in range(0, pge.shape[0], chunk):
        out[s : s + chunk] = (cnt[s : s + chunk, :, None] >= j[None, None, :]).sum(axis=1)
    return out


def production_crps(pge: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Production's CRPS route: 2 * mean_tau pinball on the integer quantile grid."""
    from nba.eval.context_residual_eval import crps_from_quantiles

    return np.asarray(crps_from_quantiles(quantiles_from_pge(pge).astype(float), y))


# --------------------------------------------------------------------------- windows and blocks


def month_starts(date: np.ndarray) -> list[np.datetime64]:
    return [np.datetime64(m, "D") for m in np.unique(date.astype("datetime64[M]"))]


def month_end(start: np.datetime64) -> np.datetime64:
    return np.datetime64((start.astype("datetime64[M]") + 1), "D")


def window_mask(date: np.ndarray, start: np.datetime64, days: int = WINDOW_DAYS) -> np.ndarray:
    """Rows with game_date in [start - days, start): strictly before the block."""
    lo = start - np.timedelta64(days, "D")
    return np.asarray((date >= lo) & (date < start))


def fit_block_maps(
    rows_date: np.ndarray,
    y: np.ndarray,
    pge: np.ndarray,
    start: np.datetime64,
    seed: int = SHUFFLE_SEED,
    min_rows: int = MIN_ROWS,
) -> dict[str, GridMap]:
    """C1, C2 and the A4 control for one block, from the trailing window ONLY (the arrays may hold
    the whole season; the window selection below is the single as-of gate)."""
    w = window_mask(rows_date, start)
    n = int(w.sum())
    if n < min_rows:
        ident = GridMap("identity", n_rows=n, reason=f"window rows {n} < {min_rows}")
        return {"C1": ident, "C2": ident, "A4": ident}
    pw, yw = pge[w], y[w]
    rng = np.random.default_rng(seed + int(start.astype("datetime64[D]").astype(int)))
    ys = rng.permutation(yw)
    return {
        "C1": fit_temperature(pw, yw, "C1"),
        "C2": fit_platt(pw, yw),
        "A4": fit_temperature(pw, ys, "A4"),
    }


def fit_isotonic(p: np.ndarray, over: np.ndarray) -> Any:
    from sklearn.isotonic import IsotonicRegression

    return IsotonicRegression(
        y_min=MODEL_EPS, y_max=1.0 - MODEL_EPS, increasing=True, out_of_bounds="clip"
    ).fit(p, over)


# --------------------------------------------------------------------------- scoring one stat-season


def score_stat(
    rows: StatRows, pairs: dict[str, PairRows], market_ll: bool = True
) -> dict[str, Any]:
    """Walk the calendar-month blocks: fit on the trailing window, apply to the block, return the
    per-row CRPS of every arm and per-pair log-loss ingredients. Nothing here reads a label from the
    block being scored (the window is [start - 60 d, start))."""
    from research.eval.odds_history_eval import model_p_over

    n = len(rows.y)
    crps = {a: np.zeros(n) for a in ("A0", "A1", "A2", "A4")}
    pp = {
        k: {a: np.full(len(pr.row), np.nan) for a in ("A0", "A1", "A2", "A3", "A4")}
        for k, pr in pairs.items()
    }
    for k, pr in pairs.items():
        pp[k]["A0"] = pr.p0.copy()
    log: list[dict[str, Any]] = []
    for start in month_starts(rows.date):
        end = month_end(start)
        bi = np.where((rows.date >= start) & (rows.date < end))[0]
        maps = fit_block_maps(rows.date, rows.y, rows.pge, start)
        grids = {
            a: apply_map(rows.pge[bi], m)
            for a, m in (
                ("A0", IDENTITY),
                ("A1", maps["C1"]),
                ("A2", maps["C2"]),
                ("A4", maps["A4"]),
            )
        }
        for a, g in grids.items():
            if a != "A0":
                assert (np.diff(g, axis=1) <= 1e-12).all() and g.min() >= 0.0 and g.max() <= 1.0, (
                    "monotonicity"
                )
            crps[a][bi] = crps_survival(g, rows.y[bi])
        entry: dict[str, Any] = {
            "block": str(start),
            "n_block": int(len(bi)),
            "window_rows": maps["C1"].n_rows,
            "C1_T": None if maps["C1"].identity else maps["C1"].temperature,
            "C2_a": None if maps["C2"].identity else maps["C2"].a,
            "C2_b": None if maps["C2"].identity else maps["C2"].b,
            "A4_T": None if maps["A4"].identity else maps["A4"].temperature,
            "identity": maps["C1"].identity,
        }
        pos = {int(r): i for i, r in enumerate(bi)}
        for kind, pr in pairs.items():
            sel = np.where(np.isin(pr.row, bi))[0]
            if len(sel) == 0:
                continue
            loc = np.array([pos[int(r)] for r in pr.row[sel]])
            for a in ("A1", "A2", "A4"):
                p, _, _ = model_p_over(grids[a][loc], pr.line[sel], rows.y[pr.row[sel]])
                pp[kind][a][sel] = p
            # C3: isotonic on the A0 line probabilities of pairs in the trailing window
            wmask = window_mask(rows.date[pr.row], start) & (pr.p0 == pr.p0)
            if int(wmask.sum()) >= C3_MIN_PAIRS:
                iso = fit_isotonic(pr.p0[wmask], pr.over[wmask])
                pp[kind]["A3"][sel] = iso.predict(pr.p0[sel])
                entry[f"C3_pairs_{kind}"] = int(wmask.sum())
            else:
                pp[kind]["A3"][sel] = pr.p0[sel]
                entry[f"C3_pairs_{kind}"] = int(wmask.sum())
                entry[f"C3_identity_{kind}"] = True
        log.append(entry)
        _log(
            f"{rows.stat} {rows.season} block {start}: n={len(bi)} window={maps['C1'].n_rows} identity={maps['C1'].identity}"
        )
    return {"crps": crps, "pairs": pp, "blocks": log}


# --------------------------------------------------------------------------- inference


def boot(delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    from research.eval.odds_history_eval import boot_ci

    return boot_ci(delta, gid, n_boot, BOOT_SEED)


def bh(p: list[float]) -> list[float]:
    from nba.eval.context_residual_eval import bh_adjust

    return [float(x) for x in bh_adjust(np.asarray(p, dtype=float))]


def ll_vec(p: np.ndarray, o: np.ndarray, eps: float = MODEL_EPS) -> np.ndarray:
    pc = np.clip(p, eps, 1.0 - eps)
    return np.asarray(-np.where(o > 0.5, np.log(pc), np.log1p(-pc)))


def confident_bins(p: np.ndarray, o: np.ndarray) -> list[dict[str, float]]:
    conf = np.maximum(p, 1.0 - p)
    hit = np.where(p >= 0.5, o, 1.0 - o)
    out = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0001)):
        m = (conf >= lo) & (conf < hi)
        out.append(
            {
                "lo": lo,
                "hi": min(hi, 1.0),
                "n": int(m.sum()),
                "claimed": float(conf[m].mean()) if m.any() else float("nan"),
                "realised": float(hit[m].mean()) if m.any() else float("nan"),
            }
        )
    return out


def summarise_stat(
    rows: StatRows, pairs: dict[str, PairRows], scored: dict[str, Any], n_boot: int = N_BOOT
) -> dict[str, Any]:
    from research.eval.odds_history_eval import reliability

    gid = rows.game_id
    res: dict[str, Any] = {
        "n_rows": int(len(rows.y)),
        "blocks": scored["blocks"],
        "crps": {},
        "lines": {},
    }
    res["share_rows_mapped"] = float(
        sum(b["n_block"] for b in scored["blocks"] if not b["identity"]) / max(len(rows.y), 1)
    )
    base = scored["crps"]["A0"]
    res["crps"]["A0"] = {"mean": float(base.mean())}
    for a in ("A1", "A2", "A4"):
        d = scored["crps"][a] - base
        res["crps"][a] = {"mean": float(scored["crps"][a].mean()), "delta": boot(d, gid, n_boot)}
    for kind, pr in pairs.items():
        pg = rows.game_id[pr.row]
        o = pr.over
        pmat = scored["pairs"][kind]
        ll0 = ll_vec(pmat["A0"], o)
        cell: dict[str, Any] = {
            "n": int(len(o)),
            "n_games": int(len(np.unique(pg))),
            "ll_book": float(ll_vec(pr.p_mkt, o, 1e-9).mean()) if len(o) else float("nan"),
            "A0": {
                "ll": float(ll0.mean()),
                "rel": reliability(np.clip(pmat["A0"], MODEL_EPS, 1 - MODEL_EPS), o),
                "conf": confident_bins(pmat["A0"], o),
            },
        }
        for a in ("A1", "A2", "A3", "A4"):
            ll = ll_vec(pmat[a], o)
            cell[a] = {
                "ll": float(ll.mean()),
                "delta": boot(ll - ll0, pg, n_boot),
                "delta_vs_book": boot(ll - ll_vec(pr.p_mkt, o, 1e-9), pg, n_boot),
                "rel": reliability(np.clip(pmat[a], MODEL_EPS, 1 - MODEL_EPS), o),
                "conf": confident_bins(pmat[a], o),
            }
        se = (cell["A1"]["delta"]["hi"] - cell["A1"]["delta"]["lo"]) / 3.92
        cell["mde_80pct"] = float(2.8 * se)
        res["lines"][kind] = cell
    return res


# --------------------------------------------------------------------------- verdict


def decide(res: dict[str, Any]) -> dict[str, Any]:
    """BH over the 4 stats per (season, metric, arm), then the pass rule per stat: 2023 selects the
    arm (lowest point among the arms that pass on 2023), 2024 must confirm the SAME arm."""
    for s in SEASONS:
        st = res["seasons"][str(s)]["stats"]
        for a in ARMS_GRID:
            ps = bh([st[x]["crps"][a]["delta"]["p"] for x in STATS])
            for x, p in zip(STATS, ps, strict=True):
                st[x]["crps"][a]["p_bh"] = p
        for kind in KINDS:
            for a in ARMS_LINE:
                ps = bh([st[x]["lines"][kind][a]["delta"]["p"] for x in STATS])
                for x, p in zip(STATS, ps, strict=True):
                    st[x]["lines"][kind][a]["p_bh"] = p

    def line_pass(cell: dict[str, Any], a: str) -> bool:
        d = cell[a]["delta"]
        ctrl = cell["A4"]["delta"]["point"] >= CONTROL_TOL
        return bool(d["point"] <= LINE_FLOOR and d["hi"] < 0 and cell[a]["p_bh"] < ALPHA and ctrl)

    def crps_pass(c: dict[str, Any], a: str) -> bool:
        return bool(c[a]["delta"]["point"] <= CRPS_FLOOR and c[a]["delta"]["hi"] < 0)

    out: dict[str, Any] = {"lines": {}, "crps": {}}
    for x in STATS:
        sel = res["seasons"][str(SELECT_SEASON)]["stats"][x]
        rep = res["seasons"][str(REPORT_SEASON)]["stats"][x]
        cand = [a for a in ARMS_LINE if line_pass(sel["lines"][PRIMARY_KIND], a)]
        pick = (
            min(cand, key=lambda a: sel["lines"][PRIMARY_KIND][a]["delta"]["point"])
            if cand
            else None
        )
        out["lines"][x] = {
            "selected_on_2023": pick,
            "confirmed_on_2024": bool(pick and line_pass(rep["lines"][PRIMARY_KIND], pick)),
        }
        cand_c = [a for a in ARMS_GRID if crps_pass(sel["crps"], a)]
        pick_c = min(cand_c, key=lambda a: sel["crps"][a]["delta"]["point"]) if cand_c else None
        out["crps"][x] = {
            "selected_on_2023": pick_c,
            "confirmed_on_2024": bool(pick_c and crps_pass(rep["crps"], pick_c)),
        }
    out["any_line_pass"] = any(v["confirmed_on_2024"] for v in out["lines"].values())
    out["any_crps_pass"] = any(v["confirmed_on_2024"] for v in out["crps"].values())
    out["closed"] = not any(v["selected_on_2023"] for v in out["lines"].values()) and not any(
        v["selected_on_2023"] for v in out["crps"].values()
    )
    return out


# --------------------------------------------------------------------------- gate


def block_table(rows: StatRows, pairs: dict[str, PairRows]) -> dict[str, Any]:
    """Check 1: trailing-window sizes per block (dates and counts only; no labels, no fit)."""
    blocks: list[dict[str, Any]] = []
    first_mapped: int | None = None
    for i, start in enumerate(month_starts(rows.date)):
        w = window_mask(rows.date, start)
        n = int(w.sum())
        lo = rows.date[w].min() if n else None
        entry = {
            "block": str(start),
            "window_rows": n,
            "window_start": None if lo is None else str(lo),
            "window_days_covered": 0 if lo is None else int((start - lo) / np.timedelta64(1, "D")),
            "mapped": n >= MIN_ROWS,
            "window_max_date": None if n == 0 else str(rows.date[w].max()),
            "window_before_block": bool(n == 0 or rows.date[w].max() < start),
        }
        for kind, pr in pairs.items():
            entry[f"pairs_{kind}"] = int(window_mask(rows.date[pr.row], start).sum())
        blocks.append(entry)
        if first_mapped is None and n >= MIN_ROWS:
            first_mapped = i
    warm = len(blocks) if first_mapped is None else first_mapped
    post = blocks[warm:]
    share = float(np.mean([float(b["mapped"]) for b in post])) if post else 0.0
    n_block = {
        str(b["block"]): int(
            (
                (rows.date >= np.datetime64(str(b["block"])))
                & (rows.date < month_end(np.datetime64(str(b["block"]))))
            ).sum()
        )
        for b in blocks
    }
    warm_rows = sum(n_block[str(b["block"])] for b in blocks[:warm])
    return {
        "blocks": blocks,
        "n_blocks": len(blocks),
        "warm_up_blocks": warm,
        "post_warm_up_blocks": len(post),
        "share_post_warm_up_with_min_rows": share,
        "min_window_rows_post_warm_up": min((int(b["window_rows"]) for b in post), default=0),
        "share_of_rows_in_warm_up": float(warm_rows / max(len(rows.y), 1)),
        "all_windows_before_block": all(b["window_before_block"] for b in blocks),
        "ok": bool(post and share >= 0.95),
    }


def synthetic_labels(pge: np.ndarray, seed: int) -> np.ndarray:
    """Draw y from each row's own grid (a perfectly calibrated world) so the planted-future test
    exercises the real window geometry without a real outcome entering any fit."""
    u = np.random.default_rng(seed).uniform(size=pge.shape[0])
    return np.asarray((pge > np.asarray(u)[:, None]).sum(axis=1), dtype=np.float64)


def planted_future_probe(rows: StatRows, start: np.datetime64, seed: int = 7) -> dict[str, Any]:
    y = synthetic_labels(rows.pge, seed)
    base = fit_block_maps(rows.date, y, rows.pge, start)
    late = rows.date >= start
    y_future = y.copy()
    y_future[late] = np.random.default_rng(seed + 1).integers(0, 200, int(late.sum())).astype(float)
    planted = fit_block_maps(rows.date, y_future, rows.pge, start)
    inside = window_mask(rows.date, start)
    y_win = y.copy()
    y_win[inside] = (
        np.random.default_rng(seed + 2).integers(0, 200, int(inside.sum())).astype(float)
    )
    control = fit_block_maps(rows.date, y_win, rows.pge, start)
    same = all(base[k] == planted[k] for k in base)
    moved = any(base[k] != control[k] for k in base) if int(inside.sum()) >= MIN_ROWS else None
    return {
        "block": str(start),
        "stat": rows.stat,
        "season": rows.season,
        "future_rows_replaced": int(late.sum()),
        "window_rows": int(inside.sum()),
        "maps_identical_when_future_labels_planted": bool(same),
        "positive_control_window_labels_move_maps": moved,
    }


MONO_T = (0.25, 0.5, 0.8, 1.25, 2.0, 4.0)
MONO_PLATT = tuple((a, b) for a in (-2.0, -0.5, 0.0, 0.5, 2.0) for b in (0.25, 0.7, 1.0, 1.5, 4.0))


def monotonicity_check(rows: StatRows) -> dict[str, Any]:
    """Check 4 on the real grids: every map in the allowed class (T and Platt bounds, a grid of
    corner and interior values) applied to EVERY stored row stays non-increasing in k, inside
    [0, 1], and rank-preserving. No label is used."""
    worst_inc = 0.0
    worst_rank = 0
    n_maps = 0
    maps = [GridMap("C1", 0.0, 1.0 / t, False) for t in MONO_T] + [
        GridMap("C2", a, b, False) for a, b in MONO_PLATT
    ]
    p = rows.pge
    for m in maps:
        g = apply_map(p, m)
        worst_inc = max(worst_inc, float(np.diff(g, axis=1).max()))
        assert g.min() >= 0.0 and g.max() <= 1.0
        # ranking within a row among interior entries is preserved: g is non-decreasing in p along the sort
        o = np.argsort(p, axis=1, kind="stable")
        gs = np.take_along_axis(g, o, axis=1)
        worst_rank += int((np.diff(gs, axis=1) < -1e-12).sum())
        n_maps += 1
    return {
        "stat": rows.stat,
        "season": rows.season,
        "rows": int(p.shape[0]),
        "maps_checked": n_maps,
        "max_increase_in_k": worst_inc,
        "rank_violations": worst_rank,
        "ok": bool(worst_inc <= 1e-12 and worst_rank == 0),
    }


def load_market(season: int, rows_dir: Path = ROWS_DIR) -> pl.DataFrame:
    guard_season(season)
    return pl.read_parquet(rows_dir / f"market_rows_{season}.parquet")


def check2(
    season: int,
    rows: dict[str, StatRows],
    pairs: dict[str, dict[str, PairRows]],
    rows_dir: Path = ROWS_DIR,
) -> dict[str, Any]:
    meta = json.loads((rows_dir / f"model_rows_{season}.meta.json").read_text())
    a0 = {c["stat"]: c for c in meta["a0"]}
    res = json.loads((rows_dir / f"results_{season}.json").read_text())
    registry_ids = set(
        pl.read_parquet(rows_dir / f"game_rows_{season}.parquet")["game_id"].cast(pl.Utf8).to_list()
    )
    out: dict[str, Any] = {"crps": [], "lines": []}
    for x in STATS:
        r = rows[x]
        prod = float(production_crps(r.pge, r.y).mean())
        exact = float(crps_survival(r.pge, r.y).mean())
        ref = a0[x]
        out["crps"].append(
            {
                "stat": x,
                "n": int(len(r.y)),
                "n_stored": int(ref["n"]),
                "crps_production_route": prod,
                "crps_stored_a0": float(ref["crps_int"]),
                "crps_lower_tail_off": float(ref["lower_tail_crps_int"]),
                "abs_diff_stored": abs(prod - float(ref["crps_int"])),
                "abs_diff_lower_tail": abs(prod - float(ref["lower_tail_crps_int"])),
                "crps_survival_route": exact,
                "abs_diff_survival_vs_production": abs(exact - prod),
                "ok": bool(
                    abs(prod - float(ref["crps_int"])) <= A0_TOL
                    and abs(prod - float(ref["lower_tail_crps_int"])) <= A0_TOL
                    and len(r.y) == int(ref["n"])
                ),
            }
        )
        for kind in KINDS:
            pr = pairs[x][kind]
            h = res["h1"][f"{kind}/{x}/{BOOK}"]
            ll = float(ll_vec(pr.p0, pr.over).mean())
            llk = float(ll_vec(pr.p_mkt, pr.over, 1e-9).mean())
            keep = np.array([g in registry_ids for g in r.game_id[pr.row]], dtype=bool)
            ll_reg = float(ll_vec(pr.p0[keep], pr.over[keep]).mean())
            out["lines"].append(
                {
                    "stat": x,
                    "kind": kind,
                    "n": int(len(pr.over)),
                    "n_odds_history": int(h["n"]),
                    "ll_model": ll,
                    "ll_model_odds_history": float(h["ll_model"]),
                    "abs_diff": abs(ll - float(h["ll_model"])),
                    "ll_book": llk,
                    "ll_book_odds_history": float(h["ll_market"]),
                    "abs_diff_book": abs(llk - float(h["ll_market"])),
                    "n_registry_keys": int(keep.sum()),
                    "ll_model_registry_keys": ll_reg,
                    "ok": bool(
                        abs(ll - float(h["ll_model"])) <= A0_TOL and len(pr.over) == int(h["n"])
                    ),
                }
            )
    out["ok"] = bool(all(c["ok"] for c in out["crps"]) and all(c["ok"] for c in out["lines"]))
    out["pair_set_changed"] = bool(any(c["n"] != c["n_odds_history"] for c in out["lines"]))
    out["ok_registry_keys"] = bool(
        all(c["ok"] for c in out["crps"])
        and all(
            abs(c["ll_model_registry_keys"] - c["ll_model_odds_history"]) <= A0_TOL or c["ok"]
            for c in out["lines"]
        )
    )
    return out


def cross_check_pair_set(
    season: int,
    rows: dict[str, StatRows],
    pairs: dict[str, dict[str, PairRows]],
    market: pl.DataFrame,
    db: str,
) -> dict[str, Any]:
    """Optional: the pair set equals ODDS_HISTORY's own ``pairs_for_season`` (read-only DB)."""
    try:
        from research.eval import odds_history_eval as oh

        model = oh.load_model_rows(season, ROWS_DIR)
        played = oh.load_played(season, db)
        tips = oh.load_tips(season, db)
        ref, _ = oh.pairs_for_season(model, market, played, tips)
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ran": False, "error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, Any] = {"ran": True, "cells": []}
    for x in STATS:
        for kind in KINDS:
            c = ref.filter(
                (pl.col("stat") == x) & (pl.col("book") == BOOK) & (pl.col("snapshot_kind") == kind)
            )
            mine = pairs[x][kind]
            ll_ref = (
                float(ll_vec(c["p_model"].to_numpy(), c["over"].to_numpy()).mean())
                if c.height
                else float("nan")
            )
            ll_me = float(ll_vec(mine.p0, mine.over).mean())
            out["cells"].append(
                {
                    "stat": x,
                    "kind": kind,
                    "n_ref": c.height,
                    "n": len(mine.over),
                    "abs_ll_diff": abs(ll_ref - ll_me),
                }
            )
    out["ok"] = all(c["n_ref"] == c["n"] and c["abs_ll_diff"] <= A0_TOL for c in out["cells"])
    return out


def do_gate(
    sha: str, draft: bool, db: str = "nba.duckdb", rows_dir: Path = ROWS_DIR
) -> dict[str, Any]:
    t0 = time.time()
    res: dict[str, Any] = {"prereg_sha256": sha, "allow_draft": draft, "seasons": {}}
    c1: dict[str, Any] = {}
    c2: dict[str, Any] = {}
    c3: list[dict[str, Any]] = []
    c4: list[dict[str, Any]] = []
    cross: dict[str, Any] = {}
    for s in SEASONS:
        guard_season(s)
        market = load_market(s, rows_dir)
        rows = {x: load_stat_rows(s, x, rows_dir) for x in STATS}
        pairs = {x: {k: line_pairs(rows[x], market, k) for k in KINDS} for x in STATS}
        _log(
            f"season {s}: rows { {x: len(rows[x].y) for x in STATS} } pairs { {x: len(pairs[x][PRIMARY_KIND].over) for x in STATS} }"
        )
        c1[str(s)] = {x: block_table(rows[x], pairs[x]) for x in STATS}
        c2[str(s)] = check2(s, rows, pairs, rows_dir)
        cross[str(s)] = cross_check_pair_set(s, rows, pairs, market, db)
        _log(
            f"season {s}: check 2 ok={c2[str(s)]['ok']} pair_set_changed={c2[str(s)]['pair_set_changed']}"
        )
        for x in STATS:
            starts = month_starts(rows[x].date)
            mapped = [b for b in c1[str(s)][x]["blocks"] if b["mapped"]]
            probe = (
                [np.datetime64(mapped[0]["block"]), np.datetime64(mapped[-1]["block"])]
                if mapped
                else []
            )
            for st in dict.fromkeys(probe):
                c3.append(planted_future_probe(rows[x], st))
            _ = starts
            c4.append(monotonicity_check(rows[x]))
        _log(f"season {s}: planted-future and monotonicity done")
    res["check1_window_rows"] = {
        "ok": all(v["ok"] for s in c1.values() for v in s.values()),
        "detail": c1,
    }
    res["check2_a0_reproduction"] = {
        "ok": all(v["ok"] for v in c2.values()),
        "ok_registry_keys": all(v["ok_registry_keys"] for v in c2.values()),
        "detail": c2,
        "pair_set_cross_check": cross,
    }
    res["check3_planted_future"] = {
        "ok": bool(
            c3
            and all(
                p["maps_identical_when_future_labels_planted"]
                and p["positive_control_window_labels_move_maps"] is not False
                for p in c3
            )
        ),
        "probes": c3,
    }
    res["check4_monotonicity"] = {"ok": bool(c4 and all(m["ok"] for m in c4)), "detail": c4}
    res["all_ok"] = all(
        res[k]["ok"]
        for k in (
            "check1_window_rows",
            "check2_a0_reproduction",
            "check3_planted_future",
            "check4_monotonicity",
        )
    )
    res["wall_seconds"] = round(time.time() - t0, 1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gate.json").write_text(json.dumps(res, indent=1, default=float))
    for k, v in res.items():
        if isinstance(v, dict) and "ok" in v:
            _log(f"gate {k}: ok={v['ok']}")
    return res


# --------------------------------------------------------------------------- run / smoke / report


def do_run(sha: str, n_boot: int = N_BOOT, rows_dir: Path = ROWS_DIR) -> dict[str, Any]:
    gate = json.loads((OUT_DIR / "gate.json").read_text())
    if not gate.get("all_ok"):
        raise SystemExit("STOP: the gate did not pass; no model result")
    res: dict[str, Any] = {
        "prereg_sha256": sha,
        "seasons": {},
        "seeds": {"bootstrap": BOOT_SEED, "shuffle": SHUFFLE_SEED},
    }
    for s in SEASONS:
        guard_season(s)
        market = load_market(s, rows_dir)
        stats_out: dict[str, Any] = {}
        for x in STATS:
            rows = load_stat_rows(s, x, rows_dir)
            pairs = {k: line_pairs(rows, market, k) for k in KINDS}
            scored = score_stat(rows, pairs)
            stats_out[x] = summarise_stat(rows, pairs, scored, n_boot)
            _log(f"{x} {s}: done")
        res["seasons"][str(s)] = {"stats": stats_out}
    res["verdict"] = decide(res)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def make_synthetic(
    seed: int = 0, n_days: int = 150, per_day: int = 60, k: int = 30
) -> tuple[StatRows, dict[str, PairRows]]:
    """Over-confident synthetic grids (claimed spread too narrow) for wiring tests."""
    rng = np.random.default_rng(seed)
    n = n_days * per_day
    day = np.repeat(np.arange(n_days), per_day)
    date = (np.datetime64("2023-10-24") + day.astype("timedelta64[D]")).astype("datetime64[D]")
    mu = rng.uniform(2, 14, n)
    y = rng.poisson(mu * rng.uniform(0.7, 1.3, n)).astype(float)
    ks = np.arange(1, k + 1)
    from scipy.stats import poisson

    pge = 1.0 - poisson.cdf(ks[None, :] - 1, mu[:, None] * 1.0)
    pge = np.round(pge * N_Q) / N_Q
    gid = np.array([f"g{d}_{i // 12}" for d, i in zip(day, np.arange(n), strict=True)])
    rows = StatRows("pts", 2023, gid, np.arange(n), date, y, pge)
    line = np.floor(mu) + 0.5
    from research.eval.odds_history_eval import model_p_over

    p, over, status = model_p_over(pge, line, y)
    keep = status == 0
    ri = np.arange(n)[keep]
    pm = np.clip(0.5 + 0.0 * ri, 0.01, 0.99)
    pairs = {
        kd: PairRows(kd, ri, line[keep], pm, p[keep], over[keep], status[keep]) for kd in KINDS
    }
    return rows, pairs


def do_smoke() -> None:
    rows, pairs = make_synthetic()
    scored = score_stat(rows, pairs)
    summ = summarise_stat(rows, pairs, scored, n_boot=100)
    a1 = summ["crps"]["A1"]["delta"]
    _log(
        f"smoke synthetic: n={summ['n_rows']} mapped share={summ['share_rows_mapped']:.2f} CRPS A1 delta={a1['point']:+.5f} (wiring only, no claim)"
    )


def _f(x: float, nd: int = 4) -> str:
    return "n/a" if x is None or x != x else f"{x:+.{nd}f}"


def render(res: dict[str, Any]) -> str:
    o = [
        f"# CALIBRATED_GRID results (rule: docs/prereg/CALIBRATED_GRID.md, sha256 `{res['prereg_sha256']}`)",
        "",
    ]
    o.append(
        "Paired deltas arm minus A0 (negative is better). 95% percentile CI, game-clustered, B=2000, seed 0. BH over the 4 stats per (season, metric, arm)."
    )
    v = res["verdict"]
    for s in SEASONS:
        o += [
            "",
            f"## Season {s}",
            "",
            "### CRPS (integer support, survival route)",
            "",
            "| stat | n | A0 | arm | delta [CI] | p_bh |",
            "|---|---|---|---|---|---|",
        ]
        for x in STATS:
            st = res["seasons"][str(s)]["stats"][x]
            for a in ("A1", "A2", "A4"):
                d = st["crps"][a]["delta"]
                o.append(
                    f"| {x} | {d['n']} | {st['crps']['A0']['mean']:.4f} | {a} | {_f(d['point'], 5)} [{_f(d['lo'], 5)}, {_f(d['hi'], 5)}] | {st['crps'][a].get('p_bh', float('nan')):.3f} |"
                    if a != "A4"
                    else f"| {x} | {d['n']} | | {a} (control) | {_f(d['point'], 5)} [{_f(d['lo'], 5)}, {_f(d['hi'], 5)}] | |"
                )
        for kind in KINDS:
            o += [
                "",
                f"### Log loss of P(over L), DK main lines, {kind}",
                "",
                "| stat | n | games | A0 | book | arm | delta vs A0 [CI] | delta vs book [CI] | p_bh |",
                "|---|---|---|---|---|---|---|---|---|",
            ]
            for x in STATS:
                c = res["seasons"][str(s)]["stats"][x]["lines"][kind]
                for a in ("A1", "A2", "A3", "A4"):
                    d, b = c[a]["delta"], c[a]["delta_vs_book"]
                    o.append(
                        f"| {x} | {c['n']} | {c['n_games']} | {c['A0']['ll']:.4f} | {c['ll_book']:.4f} | {a} | {_f(d['point'])} [{_f(d['lo'])}, {_f(d['hi'])}] | {_f(b['point'])} [{_f(b['lo'])}, {_f(b['hi'])}] | {c[a].get('p_bh', float('nan')):.3f} |"
                    )
                o.append(f"| {x} | MDE (80% power) {c['mde_80pct']:.4f} | | | | | | | |")
            o += ["", "Confident bins (claimed -> realised, n), t60 only, A0 vs A1:", ""]
            if kind == PRIMARY_KIND:
                for x in STATS:
                    c = res["seasons"][str(s)]["stats"][x]["lines"][kind]
                    o.append(
                        f"- {x} A0: "
                        + "; ".join(
                            f"[{b['lo']:.1f},{b['hi']:.1f}) {b['claimed']:.3f}->{b['realised']:.3f} ({b['n']})"
                            for b in c["A0"]["conf"]
                        )
                    )
                    o.append(
                        f"- {x} A1: "
                        + "; ".join(
                            f"[{b['lo']:.1f},{b['hi']:.1f}) {b['claimed']:.3f}->{b['realised']:.3f} ({b['n']})"
                            for b in c["A1"]["conf"]
                        )
                    )
    o += [
        "",
        "## Verdict (select 2023, confirm 2024)",
        "",
        "```",
        json.dumps(v, indent=1),
        "```",
        "",
    ]
    return "\n".join(o)


def do_report() -> None:
    res = json.loads((OUT_DIR / "results.json").read_text())
    REPORT_PATH.write_text(render(res))
    _log(f"wrote {REPORT_PATH}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    ap.add_argument(
        "--sha", default=None, help="frozen sha256, 64 hex (required except gate --allow-draft)"
    )
    ap.add_argument(
        "--allow-draft", action="store_true", help="gate stage only: skip the hash check"
    )
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)
    if args.allow_draft and args.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    configure(args.doc)
    sha = require_frozen(args.sha, args.allow_draft)
    _log(f"prereg sha256 {'(DRAFT, not enforced) ' if args.allow_draft else 'verified '}{sha[:8]}")
    if args.stage == "smoke":
        do_smoke()
        return 0
    if args.stage == "report":
        do_report()
        return 0
    if args.stage == "gate":
        gate = do_gate(sha, args.allow_draft, args.db)
        return 0 if gate["all_ok"] else 2
    with HeavyLock():
        res = do_run(sha, args.n_boot)
        REPORT_PATH.write_text(render(res))
        _log(f"verdict: {json.dumps(res['verdict'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
