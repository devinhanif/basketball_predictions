# ruff: noqa: E501
"""MODEL_CLV harness (rule: docs/prereg/MODEL_CLV.md; DRAFT until frozen).

    M="-m research.eval.model_clv_eval"
    .venv/bin/python -W ignore $M gate   --allow-draft --doc docs/prereg/MODEL_CLV.md   # checks 1-5, no arm result
    .venv/bin/python -W ignore $M smoke  --doc docs/prereg/MODEL_CLV.md --sha <64 hex>  # one month block, counts + M1 CLV
    .venv/bin/python -W ignore $M run    --doc docs/prereg/MODEL_CLV.md --sha <64 hex>  # gate -> arms -> results.json
    .venv/bin/python -W ignore $M report --doc docs/prereg/MODEL_CLV.md --sha <64 hex>  # reports/prereg_model_clv.md

``--sha`` must equal the sha256 of everything above the ``=== RESULTS BELOW ===`` line; ``--allow-draft``
skips that check and is for the gate stage only (``run`` refuses unless the doc status line says FROZEN).
Inputs are two parquet frames (``--pairs-dir``, season 2023 and 2024 only; their sha256 are recorded and,
on ``run``, must equal the ones declared in the doc). Season 2025 is never loaded: the frames are asserted
``season <= 2024``. No DuckDB is opened by this harness. ``data/ops/heavy.lock`` is checked absent (the job
is light: numpy bootstraps and one small LightGBM fit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import bh_adjust
from research.eval.f11_lineup_context import _jd, git_sha
from research.eval.minutes_hazard_eval import frozen_sha
from research.eval.odds_history_eval import blend_p

DOC = Path("docs/prereg/MODEL_CLV.md")
OUT_DIR = Path("reports/prereg_model_clv")
REPORT_MD = Path("reports/prereg_model_clv.md")
LOCK = Path("data/ops/heavy.lock")
PAIRS_DIR = Path("data/ops/model_clv_pairs")
MAX_SEASON = 2024
SEASONS = (2023, 2024)
SELECT_SEASON, REPORT_SEASON = 2023, 2024
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
BOOKS: tuple[str, ...] = ("pinnacle", "draftkings", "fanduel")
FAMILY_BOOKS: tuple[str, ...] = ("pinnacle", "draftkings")
N_BOOT = 2000
SEED = 0
W_BLEND = 0.25
FLOOR = 0.005  # half a point of probability
COVERAGE_MIN = 0.80
UNCHANGED_WARN = 0.50
MIN_CELL_N = 300
ALPHA = 0.05
ARMS: dict[str, tuple[str, float]] = {
    "M1": ("model", 0.03),
    "M2": ("model", 0.05),
    "M3": ("blend", 0.03),
}
K3 = ["game_id", "player_id", "stat"]
K4 = [*K3, "book"]

UNSTATED: list[str] = [
    "closing proxy = the t5 capture (stamped about tip - 9 min, requested for tip - 5); T-60 capture is stamped about tip - 64 min. The live companion rule will use the real last-before-tip capture",
    "a 'leg' = one t60 row of pinnacle / draftkings / fanduel (consensus excluded) with both prices, p_model and over present (all are in 2023-2024); 'joined' = a t5 row with the same (game_id, player_id, stat, book) AND a non-null de-vigged probability; 'unchanged' = joined with identical line",
    "edge uses the book's own RAW (vigged) implied probability of the side at T-60, as written; de-vigged probabilities are used only for CLV and for the Pinnacle fair price in the fade rule. De-vig = multiplicative (p_over_mult); p_under_mult = 1 - p_over_mult",
    "ties in side choice (edge_over == edge_under) go to over; shorter-price side = over when p_over_mult >= 0.5",
    "(b) line-move agreement: +1 if the chosen side's number improved by the close (over chosen and the line rose, or under chosen and the line fell: the bettor holds a number the market later moved past), -1 if it moved against, scored as the mean over legs whose line changed; unchanged legs are excluded from (b) and counted in the reported unchanged share",
    "M3: p_blend = blend_p(own-book de-vigged T-60 over probability, p_model, 0.25); edge = side probability of the blend minus the raw implied probability of that side; threshold 0.03",
    "fade-retail comparator (DK, FD only): needs Pinnacle's T-60 quote on the SAME line for the same player/stat; gap_over = pinnacle p_over_mult - retail p_over_raw, gap_under = (1 - pinnacle p_over_mult) - retail p_under_raw; side = argmax gap, bet when gap >= the arm's threshold (0.03 for M1/M3, 0.05 for M2); CLV on the retail book's own T-60 -> T-5 de-vigged move, unchanged lines only",
    "vs-fade test: difference of two means (arm cell minus fade cell at the matched threshold, same book, stat, season), game-clustered bootstrap resampling games jointly; the fade cell is not restricted to the arm's legs",
    "game clustering = game_id; one multinomial resample matrix per season (seed 0, 2000 draws) shared by every statistic; percentile 95% CI; one-sided p = (1 + #{boot <= 0}) / (2000 + 1) for H0 'CLV <= 0'",
    "n for the cell-size gate = primary-metric legs: edge >= the arm's own threshold, joined, unchanged line (the literal 'edge >= 0.03 legs' is reported beside it); a cell is dropped from the family if that n < 300 in 2023; the same cells are used in 2024",
    "BH family: the surviving {arm x book x stat} cells of the book set {pinnacle, draftkings}, per season; 2024 confirmation BH runs over the cells that passed on 2023. FanDuel cells are descriptive (not in the family)",
    "placebo (check 3) CI is Bonferroni-corrected over the number of tested cells (normal approximation with the bootstrap SE), because 'every one of ~30 cells has a 95% CI containing 0' fails by chance about 3 times in 4",
    "planted (check 4): p_model is permuted within (game_id, stat, book) with default_rng(0); the arms are rebuilt; PASS = no shuffled cell clears the primary floor-CI-BH test, and every shuffled cell's CLV minus the 20-shuffle null (seeds 1..20) has a Bonferroni CI containing 0. The 'collapses to the random-side value' wording FAILS by construction (the shuffled arm still chooses the side by the market's own price level) and is kept as a non-fatal diagnostic",
    "shuffle null (A1, proposed): per arm and cell, the mean CLV of 20 shuffled-p_model arms (same edge rule, same book/stat/legs rules); pass needs arm minus null with a CI lower bound > 0 (it removes CLV earned by selecting on the price level rather than on the model)",
    "random side: one seeded uniform per leg (default_rng(0) over legs sorted by game_id, player_id, stat, book), side = over when u < 0.5; the same draw is used by every cell",
    "mix-adjusted CLV (secondary): chosen-side CLV minus (2 s - 1) x (over de-vigged move), s = the cell's share of over bets; the expected CLV of a random side with the cell's own over/under mix",
    "realised return per $1 at the T-60 price and at the t5 price of the same side and line; push impossible (all lines are x.5); American price to decimal in the usual way",
    "L1: Pinnacle legs with unchanged line and a non-zero de-vigged move; target = move > 0; LightGBM fixed params (300 trees, lr 0.03, 15 leaves, min_child_samples 100, subsample 0.8 / colsample 0.8, seed 0), fit on 2023, scored on 2024; DK features are NaN where DK has no same-line T-60 quote; the gap-only logistic is scored on the rows where the gap exists (both models and the constant on those same rows)",
]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze + lock + doc


def assert_no_heavy_lock() -> None:
    if LOCK.exists():
        raise SystemExit("STOP: data/ops/heavy.lock is held; retry later (this job never takes it)")


def require_frozen(doc: Path, expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(doc)
    if allow_draft:
        return sha
    status = next((ln for ln in doc.read_text().splitlines() if ln.startswith("Status:")), "")
    if "FROZEN" not in status:
        raise SystemExit("STOP: the doc status line does not say FROZEN")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def declared(doc: Path) -> dict[str, Any]:
    """Machine-readable lines inside the frozen block: FAMILY_CELLS and PAIRS_SHA256_<season>."""
    # the marker as a whole line only: the freeze command quoted in the header also contains the marker text
    head = re.split(r"^=== RESULTS BELOW ===$", doc.read_text(), maxsplit=1, flags=re.M)[0]
    fam = re.search(r"^FAMILY_CELLS: (.*)$", head, re.M)
    out: dict[str, Any] = {
        "family": sorted(x.strip() for x in fam.group(1).split(",") if x.strip()) if fam else None
    }
    for s in SEASONS:
        m = re.search(rf"^PAIRS_SHA256_{s}: ([0-9a-f]{{64}})$", head, re.M)
        out[f"pairs_sha_{s}"] = m.group(1) if m else None
    return out


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- data

NEED_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "line", "price_over", "price_under",
    "p_over_raw", "p_under_raw", "p_over_mult", "p_model", "over", "tipoff_utc", "starter10", "has_report",
    "n_out_rot", "season", "game_date",
]  # fmt: skip


def load_season(pairs_dir: Path, season: int) -> dict[str, Any]:
    if season > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    d = pl.read_parquet(pairs_dir / f"pairs_{season}.parquet", columns=NEED_COLS)
    assert int(d["season"].max()) <= MAX_SEASON and d["season"].n_unique() == 1  # type: ignore[arg-type]
    assert int(d["season"][0]) == season
    d = d.filter(pl.col("book").is_in(list(BOOKS)))
    a = d.filter(pl.col("snapshot_kind") == "t60")
    b = d.filter(pl.col("snapshot_kind") == "t5")
    assert a.group_by(K4).len()["len"].max() == 1 and b.group_by(K4).len()["len"].max() == 1
    t5 = b.select([
        *K4,
        pl.col("line").alias("line5"),
        pl.col("p_over_mult").alias("mult5"),
        pl.col("price_over").alias("price_over5"),
        pl.col("price_under").alias("price_under5"),
        pl.col("snapshot_ts").alias("ts5"),
    ])  # fmt: skip
    pin = a.filter(pl.col("book") == "pinnacle").select(
        [*K3, pl.col("line").alias("pin_line"), pl.col("p_over_mult").alias("pin_mult")]
    )
    dk = a.filter(pl.col("book") == "draftkings").select(
        [
            *K3,
            pl.col("line").alias("dk_line"),
            pl.col("p_over_mult").alias("dk_mult"),
            pl.col("p_over_raw").alias("dk_raw"),
        ]
    )
    legs = (
        a.join(t5, on=K4, how="left")
        .join(pin, on=K3, how="left")
        .join(dk, on=K3, how="left")
        .sort(K4)
    )  # fmt: skip
    games = sorted(legs["game_id"].unique().to_list())
    gmap = {g: i for i, g in enumerate(games)}

    def f(c: str) -> np.ndarray:
        return legs[c].cast(pl.Float64).to_numpy()

    n = legs.height
    rng = np.random.default_rng(SEED)
    L: dict[str, Any] = {
        "season": season,
        "n": n,
        "G": len(games),
        "gi": np.array([gmap[g] for g in legs["game_id"].to_list()], dtype=np.int64),
        "book": legs["book"].to_numpy(),
        "stat": legs["stat"].to_numpy(),
        "line": f("line"),
        "line5": f("line5"),
        "mult": f("p_over_mult"),
        "mult5": f("mult5"),
        "raw_o": f("p_over_raw"),
        "raw_u": f("p_under_raw"),
        "pm": f("p_model"),
        "over": f("over"),
        "po": f("price_over"),
        "pu": f("price_under"),
        "po5": f("price_over5"),
        "pu5": f("price_under5"),
        "pin_line": f("pin_line"),
        "pin_mult": f("pin_mult"),
        "dk_line": f("dk_line"),
        "dk_mult": f("dk_mult"),
        "dk_raw": f("dk_raw"),
        "starter10": f("starter10"),
        "has_report": f("has_report"),
        "n_out_rot": f("n_out_rot"),
        "u_rand": rng.random(n),
        "game_id": legs["game_id"].to_numpy(),
        "month": legs["game_date"].dt.strftime("%Y-%m").to_numpy(),
        "ts60": legs["snapshot_ts"],
        "ts5": legs["ts5"],
        "tip": legs["tipoff_utc"],
    }
    L["joined"] = np.isfinite(L["line5"]) & np.isfinite(L["mult5"])
    L["unch"] = L["joined"] & (L["line5"] == L["line"])
    L["dm"] = np.where(L["joined"], L["mult5"] - L["mult"], np.nan)
    return L


def subset(L: dict[str, Any], mask: np.ndarray) -> dict[str, Any]:
    """Row subset (smoke); games reindexed so the bootstrap stays dense."""
    out: dict[str, Any] = {}
    for k, v in L.items():
        out[k] = v[mask] if isinstance(v, np.ndarray) and v.shape[:1] == (L["n"],) else v
    out["n"] = int(mask.sum())
    uniq, inv = np.unique(out["gi"], return_inverse=True)
    out["gi"] = inv.astype(np.int64)
    out["G"] = len(uniq)
    return out


# --------------------------------------------------------------------------- bootstrap


class Boot:
    def __init__(self, gi: np.ndarray, G: int, seed: int = SEED, B: int = N_BOOT) -> None:
        self.gi, self.G, self.B = gi, G, B
        self.W = (
            np.random.default_rng(seed)
            .multinomial(G, np.full(G, 1.0 / G), size=B)
            .astype(np.float64)
        )

    def _sc(self, v: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = m & np.isfinite(v)
        return (
            np.bincount(self.gi[k], weights=v[k], minlength=self.G),
            np.bincount(self.gi[k], minlength=self.G).astype(np.float64),
        )

    def _pack(self, point: float, bm: np.ndarray, n: int) -> dict[str, Any]:
        bm = bm[np.isfinite(bm)]
        if n == 0 or len(bm) == 0:
            return {"mean": None, "lo": None, "hi": None, "se": None, "p": None, "n": int(n)}
        lo, hi = np.percentile(bm, [2.5, 97.5])
        return {
            "mean": float(point),
            "lo": float(lo),
            "hi": float(hi),
            "se": float(bm.std(ddof=1)),
            "p": float((1 + np.sum(bm <= 0)) / (self.B + 1)),
            "n": int(n),
        }

    def mean(self, v: np.ndarray, m: np.ndarray) -> dict[str, Any]:
        S, C = self._sc(v, m)
        n = int(C.sum())
        if n == 0:
            return self._pack(0.0, np.array([]), 0)
        den = self.W @ C
        bm = (self.W @ S) / np.where(den > 0, den, np.nan)
        return self._pack(S.sum() / n, bm, n)

    def diff(
        self, v1: np.ndarray, m1: np.ndarray, v2: np.ndarray, m2: np.ndarray
    ) -> dict[str, Any]:
        S1, C1 = self._sc(v1, m1)
        S2, C2 = self._sc(v2, m2)
        n1, n2 = int(C1.sum()), int(C2.sum())
        if n1 == 0 or n2 == 0:
            return self._pack(0.0, np.array([]), 0) | {"n1": n1, "n2": n2}
        d1, d2 = self.W @ C1, self.W @ C2
        bm = (self.W @ S1) / np.where(d1 > 0, d1, np.nan) - (self.W @ S2) / np.where(
            d2 > 0, d2, np.nan
        )
        return self._pack(S1.sum() / n1 - S2.sum() / n2, bm, min(n1, n2)) | {"n1": n1, "n2": n2}


def _diff_null(
    bs: Boot, v1: np.ndarray, m1: np.ndarray, nulls: list[tuple[np.ndarray, np.ndarray]]
) -> dict[str, Any]:
    """Cell mean minus the mean over K shuffled-model nulls (each with its own selected legs)."""
    S1, C1 = bs._sc(v1, m1)
    n1 = int(C1.sum())
    d1 = bs.W @ C1
    bm1 = (bs.W @ S1) / np.where(d1 > 0, d1, np.nan)
    bms: list[np.ndarray] = []
    pts: list[float] = []
    for v, m in nulls:
        S, C = bs._sc(v, m)
        if C.sum() == 0:
            continue
        d = bs.W @ C
        bms.append((bs.W @ S) / np.where(d > 0, d, np.nan))
        pts.append(float(S.sum() / C.sum()))
    if n1 == 0 or not bms:
        return bs._pack(0.0, np.array([]), 0) | {"null_mean": None}
    null_pt = float(np.mean(pts))
    out = bs._pack(S1.sum() / n1 - null_pt, bm1 - np.mean(bms, axis=0), n1)
    out["null_mean"] = null_pt
    out["k_null"] = len(bms)
    return out


def bonf_ci(rec: dict[str, Any], n_tests: int) -> tuple[float, float]:
    z = NormalDist().inv_cdf(1 - ALPHA / (2 * max(n_tests, 1)))
    return rec["mean"] - z * rec["se"], rec["mean"] + z * rec["se"]


# --------------------------------------------------------------------------- legs, sides, metrics


def dec(american: np.ndarray) -> np.ndarray:
    return np.where(american > 0, 1.0 + american / 100.0, 1.0 + 100.0 / np.abs(american))


def arm_choice(
    L: dict[str, Any], arm: str, pm: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(chosen_over, edge, selected) for every leg."""
    kind, thr = ARMS[arm]
    p = L["pm"] if pm is None else pm
    if kind == "blend":
        p = blend_p(L["mult"], p, W_BLEND)
    e_o = p - L["raw_o"]
    e_u = (1.0 - p) - L["raw_u"]
    c = e_o >= e_u
    edge = np.where(c, e_o, e_u)
    return c, edge, edge >= thr


def side_arrays(L: dict[str, Any], c: np.ndarray) -> dict[str, np.ndarray]:
    """Per-leg quantities of the chosen side c (True = over)."""
    sgn = np.where(c, 1.0, -1.0)
    clv = np.where(L["unch"], sgn * L["dm"], np.nan)
    mv = np.where(L["line5"] > L["line"], 1.0, np.where(L["line5"] < L["line"], -1.0, np.nan)) * sgn
    mv = np.where(L["joined"], mv, np.nan)
    win = np.where(c, L["over"] == 1.0, L["over"] == 0.0)
    d60 = dec(np.where(c, L["po"], L["pu"]))
    d5 = dec(np.where(c, L["po5"], L["pu5"]))
    ret60 = np.where(win, d60 - 1.0, -1.0)
    retc = np.where(L["unch"], np.where(win, d5 - 1.0, -1.0), np.nan)
    return {"clv": clv, "move": mv, "ret60": ret60, "retc": retc}


def fade_choice(L: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Fade-retail-toward-Pinnacle side and gap (retail books only, Pinnacle on the same line)."""
    ok = (L["book"] != "pinnacle") & (L["pin_line"] == L["line"])
    g_o = L["pin_mult"] - L["raw_o"]
    g_u = (1.0 - L["pin_mult"]) - L["raw_u"]
    c = g_o >= g_u
    gap = np.where(ok, np.where(c, g_o, g_u), np.nan)
    return c, gap


def cell_mask(L: dict[str, Any], book: str, stat: str) -> np.ndarray:
    return np.asarray((L["book"] == book) & (L["stat"] == stat))


N_NULL = 20
NullStore = dict[str, list[tuple[np.ndarray, np.ndarray]]]


def make_nulls(L: dict[str, Any], k: int = N_NULL) -> NullStore:
    """Per arm: K shuffled-p_model nulls (seeds 1..K) as (chosen-side CLV, primary-leg mask) over all legs."""
    store: NullStore = {a: [] for a in ARMS}
    for i in range(1, k + 1):
        pm = shuffle_model(L, i)
        for a in ARMS:
            c, _, sel = arm_choice(L, a, pm)
            store[a].append((side_arrays(L, c)["clv"], sel & L["unch"]))
    return store


def score_cell(
    L: dict[str, Any],
    bs: Boot,
    arm: str,
    book: str,
    stat: str,
    pm: np.ndarray | None,
    full: bool = True,
    nulls: NullStore | None = None,
) -> dict[str, Any]:
    c, edge, sel = arm_choice(L, arm, pm)
    base = cell_mask(L, book, stat)
    sel_c = sel & base
    prim = sel_c & L["unch"]
    S = side_arrays(L, c)
    rec: dict[str, Any] = {
        "arm": arm,
        "book": book,
        "stat": stat,
        "n_legs": int(base.sum()),
        "n_edge03_literal": int(
            (np.where(c, L["pm"] - L["raw_o"], (1 - L["pm"]) - L["raw_u"]) >= 0.03)[base].sum()
        )
        if pm is None
        else None,
        "n_sel": int(sel_c.sum()),
        "n_sel_joined": int((sel_c & L["joined"]).sum()),
        "n_prim": int(prim.sum()),
        "over_share": float(c[prim].mean()) if prim.any() else None,
        "unchanged_share_sel": float(prim.sum() / max((sel_c & L["joined"]).sum(), 1)),
        "model": bs.mean(S["clv"], prim),
    }
    if not full:
        return rec
    rand_c = L["u_rand"] < 0.5
    short_c = L["mult"] >= 0.5
    s = float(c[prim].mean()) if prim.any() else 0.5
    rec["rand"] = bs.mean(side_arrays(L, rand_c)["clv"], prim)
    rec["short"] = bs.mean(side_arrays(L, short_c)["clv"], prim)
    rec["always_under"] = bs.mean(side_arrays(L, np.zeros(L["n"], bool))["clv"], prim)
    rec["always_over"] = bs.mean(side_arrays(L, np.ones(L["n"], bool))["clv"], prim)
    adj = S["clv"] - (2 * s - 1) * L["dm"]
    rec["mix_adj"] = bs.mean(adj, prim)
    rec["rand_diff"] = bs.mean(S["clv"] - side_arrays(L, rand_c)["clv"], prim)
    if nulls is not None:
        rec["vs_shuffle"] = _diff_null(bs, S["clv"], prim, [(v, m & base) for v, m in nulls[arm]])
    rec["move"] = bs.mean(S["move"], sel_c & ~L["unch"] & L["joined"])
    rec["ret60"] = bs.mean(S["ret60"], prim)
    rec["retc"] = bs.mean(S["retc"], prim)
    if book != "pinnacle":
        fc, gap = fade_choice(L)
        thr = ARMS[arm][1]
        fsel = base & np.isfinite(gap) & (gap >= thr) & L["unch"]
        FS = side_arrays(L, fc)
        rec["fade"] = bs.mean(FS["clv"], fsel)
        rec["vs_fade"] = bs.diff(S["clv"], prim, FS["clv"], fsel)
        rec["fade_move"] = bs.mean(
            FS["move"], base & np.isfinite(gap) & (gap >= thr) & ~L["unch"] & L["joined"]
        )
    return rec


def unconditioned(L: dict[str, Any], bs: Boot) -> list[dict[str, Any]]:
    """No-model controls over every unchanged leg of a book x stat."""
    out: list[dict[str, Any]] = []
    rand_c = L["u_rand"] < 0.5
    short_c = L["mult"] >= 0.5
    for book in BOOKS:
        for stat in STATS:
            base = cell_mask(L, book, stat) & L["unch"]
            r: dict[str, Any] = {
                "book": book,
                "stat": stat,
                "n_unch": int(base.sum()),
                "rand": bs.mean(side_arrays(L, rand_c)["clv"], base),
                "short": bs.mean(side_arrays(L, short_c)["clv"], base),
                "always_over": bs.mean(side_arrays(L, np.ones(L["n"], bool))["clv"], base),
            }
            if book != "pinnacle":
                fc, gap = fade_choice(L)
                FS = side_arrays(L, fc)
                for thr in (0.03, 0.05):
                    r[f"fade_{thr}"] = bs.mean(
                        FS["clv"],
                        cell_mask(L, book, stat) & np.isfinite(gap) & (gap >= thr) & L["unch"],
                    )
            out.append(r)
    return out


def all_cells(
    L: dict[str, Any],
    bs: Boot,
    pm: np.ndarray | None = None,
    full: bool = True,
    books: tuple[str, ...] = BOOKS,
    nulls: NullStore | None = None,
) -> list[dict[str, Any]]:
    return [
        score_cell(L, bs, arm, b, s, pm, full, nulls) for arm in ARMS for b in books for s in STATS
    ]


def cell_id(r: dict[str, Any]) -> str:
    return f"{r['arm']}|{r['book']}|{r['stat']}"


def shuffle_model(L: dict[str, Any], seed: int = SEED) -> np.ndarray:
    key = np.char.add(
        np.char.add(L["game_id"].astype(str), "|"),
        np.char.add(L["stat"].astype(str), np.char.add("|", L["book"].astype(str))),
    )
    _, code = np.unique(key, return_inverse=True)
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.random(L["n"]), code))
    base = np.argsort(code, kind="stable")
    out = np.empty(L["n"])
    out[base] = L["pm"][order]
    return out


# --------------------------------------------------------------------------- pass rule


def passes_primary(r: dict[str, Any], q: float) -> bool:
    m = r["model"]
    return bool(m["mean"] is not None and m["mean"] >= FLOOR and m["lo"] > 0 and q < ALPHA)


def add_q(recs: list[dict[str, Any]], family: set[str]) -> None:
    fam = [r for r in recs if cell_id(r) in family and r["model"]["p"] is not None]
    adj: Any = bh_adjust(np.array([r["model"]["p"] for r in fam])) if fam else []
    for r in recs:
        r["q"] = None
    for r, a in zip(fam, adj, strict=True):
        r["q"] = float(a)


def evaluate_rules(res: dict[int, list[dict[str, Any]]], family: list[str]) -> dict[str, Any]:
    fam = set(family)
    r23 = [r for r in res[SELECT_SEASON] if cell_id(r) in fam]
    add_q(res[SELECT_SEASON], fam)
    out: dict[str, Any] = {"cells": {}}
    passers23: list[str] = []
    for r in r23:
        cid = cell_id(r)
        beat = True
        if r["book"] == "draftkings":
            v = r["vs_fade"]
            beat = bool(v["mean"] is not None and v["lo"] is not None and v["lo"] > 0)
        bsh = bool(r["vs_shuffle"]["lo"] is not None and r["vs_shuffle"]["lo"] > 0)
        ok = passes_primary(r, r["q"]) and beat and bsh
        out["cells"][cid] = {
            "pass_2023_primary": passes_primary(r, r["q"]),
            "pass_2023_vs_fade": beat,
            "pass_2023_vs_shuffle": bsh,
            "pass_2023": ok,
        }
        if ok:
            passers23.append(cid)
    out["close_no_cell_passes_2023"] = not passers23
    r24 = {cell_id(r): r for r in res[REPORT_SEASON]}
    add_q(res[REPORT_SEASON], set(passers23))
    for cid in passers23:
        r = r24[cid]
        beat = True
        if r["book"] == "draftkings":
            v = r["vs_fade"]
            beat = bool(v["mean"] is not None and v["lo"] is not None and v["lo"] > 0)
        bsh = bool(r["vs_shuffle"]["lo"] is not None and r["vs_shuffle"]["lo"] > 0)
        ok = passes_primary(r, r["q"]) and beat and bsh
        out["cells"][cid] |= {
            "pass_2024_primary": passes_primary(r, r["q"]),
            "pass_2024_vs_fade": beat,
            "pass_2024_vs_shuffle": bsh,
            "pass_2024": ok,
            "pass": ok,
        }
    for v in out["cells"].values():
        v.setdefault("pass", False)
    out["passing_cells"] = sorted(c for c, v in out["cells"].items() if v["pass"])
    out["n_family"] = len(fam)
    return out


# --------------------------------------------------------------------------- gate


def chk(name: str, value: Any, threshold: str, ok: Any, fatal: bool = True) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok), "fatal": fatal}


def check_1(data: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    for s, L in data.items():
        for b in BOOKS:
            m = L["book"] == b
            n = int(m.sum())
            cov = float((m & L["joined"]).sum() / n)
            unch_all = float((m & L["unch"]).sum() / n)
            unch_joined = float((m & L["unch"]).sum() / max((m & L["joined"]).sum(), 1))
            info[f"{s}_{b}"] = {
                "n_t60_legs": n,
                "n_joined": int((m & L["joined"]).sum()),
                "coverage": cov,
                "n_unchanged": int((m & L["unch"]).sum()),
                "unchanged_share_of_t60": unch_all,
                "unchanged_share_of_joined": unch_joined,
            }
            tests.append(
                chk(
                    f"1 t60->t5 join coverage {s} {b}",
                    cov,
                    f">= {COVERAGE_MIN}",
                    cov >= COVERAGE_MIN,
                )
            )
            tests.append(
                chk(
                    f"1 unchanged-line share of t60 legs {s} {b} (warn: < 0.5 = under-powered)",
                    unch_all,
                    f">= {UNCHANGED_WARN}",
                    unch_all >= UNCHANGED_WARN,
                    fatal=False,
                )
            )
    return tests, info


def check_2(data: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    for s, L in data.items():
        j = L["joined"]
        gap5 = (L["tip"] - L["ts5"]).dt.total_seconds().to_numpy().astype(float) / 60.0
        gap60 = (L["tip"] - L["ts60"]).dt.total_seconds().to_numpy().astype(float) / 60.0
        before = bool(np.all(gap5[j] > 0))
        t60_ok = bool(np.all(gap60 >= 60))
        info[str(s)] = {
            "t5_min_before_tip": [
                float(np.nanmin(gap5[j])),
                float(np.nanmedian(gap5[j])),
                float(np.nanmax(gap5[j])),
            ],
            "t60_min_before_tip": [float(gap60.min()), float(np.median(gap60)), float(gap60.max())],
            "n_joined": int(j.sum()),
        }
        tests.append(
            chk(
                f"2 t5 snapshot_ts strictly before tipoff_utc, 100% of joined rows {s}",
                float(np.mean(gap5[j] > 0)),
                "== 1.0",
                before,
            )
        )
        tests.append(
            chk(
                f"2 t60 snapshot_ts at least 60 min before tip, 100% of legs {s}",
                float(np.mean(gap60 >= 60)),
                "== 1.0",
                t60_ok,
            )
        )
    return tests, info


def check_3(
    data: dict[int, dict[str, Any]],
    boots: dict[int, Boot],
    cells: dict[int, list[dict[str, Any]]],
    family: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Random-side CLV: Bonferroni CI contains 0 in every cell (arm legs) and every book x stat (all unchanged legs)."""
    tests: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    for s, L in data.items():
        recs = [r["rand"] for r in cells[s] if cell_id(r) in set(family)]
        unc = unconditioned(L, boots[s])
        recs += [u["rand"] for u in unc]
        k = len(recs)
        bad95 = sum(1 for r in recs if r["lo"] is not None and not (r["lo"] <= 0 <= r["hi"]))
        badB = 0
        worst = 0.0
        for r in recs:
            lo, hi = bonf_ci(r, k)
            if not (lo <= 0 <= hi):
                badB += 1
            worst = max(worst, abs(r["mean"]) / r["se"] if r["se"] else 0.0)
        info[str(s)] = {
            "n_tests": k,
            "n_outside_plain95": bad95,
            "n_outside_bonferroni": badB,
            "max_abs_z": worst,
            "unconditioned_always_over": {
                f"{u['book']}|{u['stat']}": u["always_over"] for u in unc
            },
        }
        tests.append(
            chk(
                f"3 placebo random-side CLV: cells whose Bonferroni CI excludes 0, {s} ({k} tests; plain-95% misses {bad95})",
                badB,
                "== 0",
                badB == 0,
            )
        )
    return tests, info


def check_4(
    data: dict[int, dict[str, Any]],
    boots: dict[int, Boot],
    family: list[str],
    nulls: dict[int, NullStore],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Planted: p_model shuffled within (game, stat, book) (seed 0, not among the null seeds 1..20)."""
    tests: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    fam = set(family)
    for s, L in data.items():
        pm = shuffle_model(L, SEED)
        recs = [
            r for r in all_cells(L, boots[s], pm, True, FAMILY_BOOKS, nulls[s]) if cell_id(r) in fam
        ]
        add_q(recs, fam)
        clear = [cell_id(r) for r in recs if r["q"] is not None and passes_primary(r, r["q"])]
        k = len(recs)
        off_null = [
            cell_id(r)
            for r in recs
            if r["vs_shuffle"]["mean"] is not None
            and not (lambda b: b[0] <= 0 <= b[1])(bonf_ci(r["vs_shuffle"], k))
        ]
        off_rand = [
            cell_id(r)
            for r in recs
            if r["rand_diff"]["mean"] is not None
            and not (lambda b: b[0] <= 0 <= b[1])(bonf_ci(r["rand_diff"], k))
        ]
        info[str(s)] = {
            "n_cells": k,
            "clear_floor_ci_bh": clear,
            "planted_vs_null_outside_bonferroni": off_null,
            "diagnostic_planted_vs_random_side_outside_bonferroni": off_rand,
            "planted_mean_clv_over_cells": float(np.mean([r["model"]["mean"] for r in recs])),
            "shuffle_null_mean_clv_over_cells": float(
                np.mean([r["vs_shuffle"]["null_mean"] for r in recs])
            ),
            "random_side_mean_clv_over_cells": float(np.mean([r["rand"]["mean"] for r in recs])),
        }
        tests.append(
            chk(
                f"4 planted shuffle: shuffled-p_model cells that clear floor+CI+BH, {s}",
                len(clear),
                "== 0",
                not clear,
            )
        )
        tests.append(
            chk(
                f"4 planted shuffle vs the 20-shuffle null: cells whose Bonferroni CI excludes 0, {s}",
                len(off_null),
                "== 0",
                not off_null,
            )
        )
        tests.append(
            chk(
                f"4 DIAGNOSTIC planted shuffle vs random side (does NOT collapse: selection on price level): cells outside, {s}",
                len(off_rand),
                "reported",
                True,
                fatal=False,
            )
        )
    return tests, info


def check_5(
    cells23: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    keep: list[str] = []
    table: list[dict[str, Any]] = []
    for r in cells23:
        if r["book"] not in FAMILY_BOOKS:
            continue
        ok = r["n_prim"] >= MIN_CELL_N
        table.append(
            {
                "cell": cell_id(r),
                "n_edge03_literal": r["n_edge03_literal"],
                "n_sel_own_threshold": r["n_sel"],
                "n_primary": r["n_prim"],
                "kept": ok,
            }
        )
        if ok:
            keep.append(cell_id(r))
    tests = [
        chk(
            "5 cells with >= 300 primary legs in 2023 (family size)",
            len(keep),
            ">= 1",
            len(keep) >= 1,
        )
    ]
    return tests, sorted(keep), table


def load_all(pairs_dir: Path) -> tuple[dict[int, dict[str, Any]], dict[int, Boot], dict[str, str]]:
    data: dict[int, dict[str, Any]] = {}
    boots: dict[int, Boot] = {}
    shas: dict[str, str] = {}
    for s in SEASONS:
        data[s] = load_season(pairs_dir, s)
        boots[s] = Boot(data[s]["gi"], data[s]["G"])
        shas[str(s)] = file_sha(pairs_dir / f"pairs_{s}.parquet")
        _log(f"loaded {s}: {data[s]['n']} legs, {data[s]['G']} games")
    return data, boots, shas


def run_gate(
    pairs_dir: Path, doc: Path, sha: str, allow_draft: bool, out_dir: Path = OUT_DIR
) -> dict[str, Any]:
    assert_no_heavy_lock()
    t0 = time.time()
    data, boots, shas = load_all(pairs_dir)
    tests: list[dict[str, Any]] = []
    t1, i1 = check_1(data)
    t2, i2 = check_2(data)
    tests += t1 + t2
    nulls = {s: make_nulls(data[s]) for s in SEASONS}
    _log(f"nulls built {time.time() - t0:.0f}s")
    cells = {s: all_cells(data[s], boots[s], None, True, BOOKS, nulls[s]) for s in SEASONS}
    _log(f"cells scored {time.time() - t0:.0f}s")
    t5, family, table5 = check_5(cells[SELECT_SEASON])
    t3, i3 = check_3(data, boots, cells, family)
    t4, i4 = check_4(data, boots, family, nulls)
    tests += t3 + t4 + t5
    dropped = [t["cell"] for t in table5 if not t["kept"]]
    always_over = {
        f"{s}|{b}": boots[s].mean(data[s]["dm"], (data[s]["book"] == b) & data[s]["unch"])
        for s in SEASONS
        for b in BOOKS
    }
    # arm size at alternative thresholds, for the pre-freeze amendment discussion only
    alt: dict[str, int] = {}
    for thr in (0.01, 0.02, 0.03):
        c, _, _ = arm_choice(data[SELECT_SEASON], "M3")
        L = data[SELECT_SEASON]
        p = blend_p(L["mult"], L["pm"], W_BLEND)
        e = np.maximum(p - L["raw_o"], (1 - p) - L["raw_u"])
        for b in FAMILY_BOOKS:
            alt[f"M3 edge>={thr} {b} unchanged legs 2023"] = int(
                ((e >= thr) & L["unch"] & (L["book"] == b)).sum()
            )
        del c
    res: dict[str, Any] = {
        "status": "GATE_PASSED"
        if all(t["ok"] or not t["fatal"] for t in tests)
        else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "doc": str(doc),
        "sha256": sha,
        "git_sha": git_sha(),
        "seeds": {"bootstrap": SEED, "random_side": SEED, "shuffle": SEED, "lgbm": SEED},
        "pairs_sha256": shas,
        "check_1": i1,
        "check_2": i2,
        "check_3": i3,
        "check_4": i4,
        "check_5_table": table5,
        "family_cells": family,
        "dropped_cells": dropped,
        "drift_over_move_unchanged": always_over,
        "m3_alt_thresholds": alt,
        "tests": tests,
        "gate_ok": all(t["ok"] or not t["fatal"] for t in tests),
        "max_season_loaded": max(int(d["season"]) for d in data.values()),
        "unstated_details": UNSTATED,
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1, default=_jd, sort_keys=True))
    return res


# --------------------------------------------------------------------------- secondary: regression and L1


def cluster_ols(X: np.ndarray, y: np.ndarray, gi: np.ndarray) -> dict[str, Any]:
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    e = y - X @ beta
    _, inv = np.unique(gi, return_inverse=True)
    G = int(inv.max()) + 1
    sc = np.zeros((G, X.shape[1]))
    np.add.at(sc, inv, X * e[:, None])
    V = XtX_inv @ (sc.T @ sc) @ XtX_inv * (G / max(G - 1, 1))
    se = np.sqrt(np.diag(V))
    return {"beta": beta.tolist(), "se": se.tolist(), "n": len(y), "games": int(G)}


def regression(L: dict[str, Any], book: str = "draftkings") -> dict[str, Any]:
    c, edge, sel = arm_choice(L, "M1")
    S = side_arrays(L, c)
    m = (
        sel
        & (L["book"] == book)
        & L["unch"]
        & (L["pin_line"] == L["line"])
        & np.isfinite(L["pin_mult"])
    )
    if m.sum() < 50:
        return {"n": int(m.sum())}
    sgn = np.where(c, 1.0, -1.0)
    gap = sgn * (L["pin_mult"] - L["mult"])
    y, e, g, gi = S["clv"][m], edge[m], gap[m], L["gi"][m]
    one = np.ones_like(y)
    r_edge = cluster_ols(np.column_stack([one, e]), y, gi)
    r_gap = cluster_ols(np.column_stack([one, g]), y, gi)
    r_both = cluster_ols(np.column_stack([one, e, g]), y, gi)
    resid_mean = float(y.mean() - r_gap["beta"][1] * g.mean())
    return {
        "book": book,
        "n": int(m.sum()),
        "mean_clv": float(y.mean()),
        "mean_gap": float(g.mean()),
        "edge_only": r_edge,
        "gap_only": r_gap,
        "edge_and_gap": r_both,
        "mean_clv_after_removing_gap_term": resid_mean,
        "share_surviving": resid_mean / float(y.mean()) if y.mean() != 0 else None,
        "edge_coef_ratio_with_gap_over_without": r_both["beta"][1] / r_edge["beta"][1]
        if r_edge["beta"][1] != 0
        else None,
    }


L1_FEATS = [
    "p_model",
    "raw_o",
    "mult",
    "dk_raw",
    "dk_mult",
    "gap",
    "stat_code",
    "line",
    "starter10",
    "has_report",
    "n_out_rot",
]


def l1_frame(L: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = (L["book"] == "pinnacle") & L["unch"] & (L["dm"] != 0)
    dk_ok = L["dk_line"] == L["line"]
    dk_raw = np.where(dk_ok, L["dk_raw"], np.nan)
    dk_mult = np.where(dk_ok, L["dk_mult"], np.nan)
    codes = np.array([STATS.index(s) for s in L["stat"]], dtype=float)
    X = np.column_stack(
        [
            L["pm"],
            L["raw_o"],
            L["mult"],
            dk_raw,
            dk_mult,
            L["mult"] - dk_mult,
            codes,
            L["line"],
            L["starter10"],
            L["has_report"],
            L["n_out_rot"],
        ]
    )
    return X[m], (L["dm"][m] > 0).astype(float), L["gi"][m]


def l1(data: dict[int, dict[str, Any]], boots: dict[int, Boot]) -> dict[str, Any]:
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression

    X0, y0, _ = l1_frame(data[SELECT_SEASON])
    X1, y1, g1 = l1_frame(data[REPORT_SEASON])
    clf = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=15,
        min_child_samples=100,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=SEED,
        n_jobs=4,
        deterministic=True,
        verbose=-1,
    )
    clf.fit(X0, y0)
    p_l1 = np.clip(np.asarray(clf.predict_proba(X1))[:, 1], 1e-6, 1 - 1e-6)
    base_rate = float(y0.mean())
    gi_ = L1_FEATS.index("gap")
    ok0, ok1 = np.isfinite(X0[:, gi_]), np.isfinite(X1[:, gi_])
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(X0[ok0][:, [gi_]], y0[ok0])
    p_gap = np.clip(lr.predict_proba(X1[ok1][:, [gi_]])[:, 1], 1e-6, 1 - 1e-6)

    def ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.asarray(-(y * np.log(p) + (1 - y) * np.log1p(-p)))

    bs = boots[REPORT_SEASON]
    all_rows = np.ones(len(y1), bool)
    l_l1, l_c = ll(p_l1, y1), ll(np.full(len(y1), base_rate), y1)
    # gap rows are a subset: build full-length arrays (NaN outside) so that Boot indexes by game
    gaprow = np.where(ok1, 1.0, np.nan)
    l_gap_full = np.full(len(y1), np.nan)
    l_gap_full[ok1] = ll(p_gap, y1[ok1])
    gb = Boot(g1, int(bs.G))  # rows are Pinnacle legs only; index by the season game index
    return {
        "n_train_2023": int(len(y0)),
        "n_score_2024": int(len(y1)),
        "n_score_2024_with_gap": int(ok1.sum()),
        "base_rate_2023": base_rate,
        "all_rows": {
            "logloss_l1": float(l_l1.mean()),
            "logloss_constant": float(l_c.mean()),
            "l1_minus_constant": gb.mean(l_l1 - l_c, all_rows),
        },
        "gap_rows": {
            "logloss_l1": float(l_l1[ok1].mean()),
            "logloss_constant": float(l_c[ok1].mean()),
            "logloss_gap_logistic": float(l_gap_full[ok1].mean()),
            "l1_minus_constant": gb.mean(l_l1 - l_c, ok1),
            "l1_minus_gap_logistic": gb.mean(l_l1 - l_gap_full, ok1 & np.isfinite(gaprow)),
            "gap_minus_constant": gb.mean(l_gap_full - l_c, ok1),
        },
        "importance_gain": dict(
            zip(L1_FEATS, [float(x) for x in clf.booster_.feature_importance("gain")], strict=True)
        ),
    }


# --------------------------------------------------------------------------- run / smoke


def run_arms(pairs_dir: Path, doc: Path, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    assert_no_heavy_lock()
    gate = json.loads((out_dir / "gate.json").read_text())
    if gate.get("draft") or not gate.get("gate_ok") or gate.get("sha256") != sha:
        raise RuntimeError("no passing gate recorded for this frozen sha: no arm may be scored")
    dec_ = declared(doc)
    for s in SEASONS:
        if dec_[f"pairs_sha_{s}"] != gate["pairs_sha256"][str(s)]:
            raise RuntimeError(f"pairs_{s}.parquet sha256 does not match the frozen doc")
    if dec_["family"] != gate["family_cells"]:
        raise RuntimeError(
            "family cells measured by the gate differ from the frozen FAMILY_CELLS line"
        )
    t0 = time.time()
    data, boots, _ = load_all(pairs_dir)
    res: dict[int, list[dict[str, Any]]] = {}
    unc: dict[int, list[dict[str, Any]]] = {}
    for s in SEASONS:
        res[s] = all_cells(data[s], boots[s], None, True, BOOKS, make_nulls(data[s]))
        unc[s] = unconditioned(data[s], boots[s])
        _log(f"[{time.time() - t0:.0f}s] season {s} scored")
    rules = evaluate_rules(res, dec_["family"])
    reg = {str(s): regression(data[s]) for s in SEASONS}
    learner = l1(data, boots)
    _log(f"[{time.time() - t0:.0f}s] L1 done")
    out = {
        "cells": {str(s): res[s] for s in SEASONS},
        "unconditioned": {str(s): unc[s] for s in SEASONS},
        "rules": rules,
        "regression": reg,
        "l1": learner,
        "seeds": gate["seeds"],
        "git_sha": git_sha(),
        "sha256": sha,
        "n_boot": N_BOOT,
        "run_seconds": time.time() - t0,
    }
    (out_dir / "results.json").write_text(json.dumps(out, indent=1, default=_jd))
    return out


def run_smoke(pairs_dir: Path) -> None:
    assert_no_heavy_lock()
    L0 = load_season(pairs_dir, SELECT_SEASON)
    L = subset(L0, L0["month"] == "2023-11")
    bs = Boot(L["gi"], L["G"])
    for r in all_cells(L, bs, None, False, FAMILY_BOOKS):
        if r["arm"] == "M1":
            m = r["model"]
            print(
                cell_id(r),
                "n_prim",
                r["n_prim"],
                "clv",
                None if m["mean"] is None else round(m["mean"], 4),
                flush=True,
            )


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _ci(m: dict[str, Any]) -> str:
    return f"{_f(m['mean'])} [{_f(m['lo'])}, {_f(m['hi'])}] n {m['n']}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    fam = set(r["rules"]["cells"])
    L = [
        f"# MODEL_CLV results (sha256 `{r['sha256']}`, git `{r['git_sha']}`)",
        "",
        "Closing proxy = t5 (tip - 9 min). CLV in probability points, game-clustered 95% CI.",
        "",
    ]
    for s in SEASONS:
        L += [
            f"## {s}",
            "",
            "| cell | in family | n legs sel / primary | over share | model CLV | random | shorter price | mix-adj | line-move | vs fade | vs shuffle null | q |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in r["cells"][str(s)]:
            cid = cell_id(c)
            vf = _ci(c["vs_fade"]) if "vs_fade" in c else "n/a"
            q = c.get("q")
            L.append(
                f"| {cid} | {cid in fam} | {c['n_sel']} / {c['n_prim']} | {_f(c['over_share'], 2, False)} | {_ci(c['model'])} | {_f(c['rand']['mean'])} | {_f(c['short']['mean'])} | {_f(c['mix_adj']['mean'])} | {_f(c['move']['mean'], 3)} n {c['move']['n']} | {vf} | {_ci(c['vs_shuffle'])} | {_f(q, 3, False)} |"
            )
        L.append("")
    L += [
        "## Rules",
        "",
        "```",
        json.dumps(r["rules"], indent=1),
        "```",
        "",
        "## Secondary",
        "",
        "```",
        json.dumps({"regression": r["regression"], "l1": r["l1"]}, indent=1),
        "```",
        "",
    ]
    return "\n".join(L)


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    body = ["| id | test | result |", "|---|---|---|"]
    fam = set(r["rules"]["cells"])
    for s in SEASONS:
        for c in r["cells"][str(s)]:
            cid = cell_id(c)
            if cid not in fam:
                continue
            m = c["model"]
            body.append(
                f"| T### | MODEL_CLV {cid} {s} (n {m['n']} legs) | price CLV {_ci(m)}; q {_f(c.get('q'), 3, False)}; vs fade {_ci(c['vs_fade']) if 'vs_fade' in c else 'n/a'} |"
            )
    rules = r["rules"]
    body.append(
        f"| T### | MODEL_CLV verdict | passing cells {rules['passing_cells']}; close (none pass 2023) {rules['close_no_cell_passes_2023']} |"
    )
    l1r = r["l1"]["gap_rows"]
    body.append(
        f"| T### | MODEL_CLV L1 learner (descriptive; n {r['l1']['n_score_2024_with_gap']}) | logloss L1 {l1r['logloss_l1']:.4f}, constant {l1r['logloss_constant']:.4f}, gap logistic {l1r['logloss_gap_logistic']:.4f} |"
    )
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--pairs-dir", type=Path, default=PAIRS_DIR)
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.doc, a.sha, a.allow_draft)
    if a.stage == "gate":
        res = run_gate(a.pairs_dir, a.doc, sha, a.allow_draft)
        print(
            json.dumps(
                {k: res[k] for k in ("sha256", "gate_ok", "family_cells", "dropped_cells")},
                indent=1,
                default=_jd,
            )
        )
        for t in res["tests"]:
            print(
                ("PASS " if t["ok"] else ("WARN " if not t["fatal"] else "FAIL ")) + t["test"],
                t["value"],
            )
        return 0 if res["gate_ok"] else 1
    if a.stage == "smoke":
        run_smoke(a.pairs_dir)
    elif a.stage == "run":
        g = run_gate(a.pairs_dir, a.doc, sha, False)
        if not g["gate_ok"]:
            print("GATE FAILED: stop, no model result", flush=True)
            return 2
        res = run_arms(a.pairs_dir, a.doc, sha)
        REPORT_MD.write_text(render_report())
        write_ledger()
        print(json.dumps(res["rules"], indent=1))
    else:
        REPORT_MD.write_text(render_report())
        write_ledger()
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
