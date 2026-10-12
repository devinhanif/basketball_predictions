# ruff: noqa: E501
"""Decision scoring: score the bets the model would place, at the actual price (descriptive).

Definition lives in docs/DECISION_SCORING.md. Read-only: it reads stored prices and stored walk-forward
forecasts; no order, portfolio or execution code exists here. Seasons <= 2024 only (asserted).

    .venv/bin/python -m nba.markets.decision_scoring run --pairs-dir data/ops/model_clv_pairs

A *leg* is one (game, player, stat) with a T-60 two-sided American price at one book and a stored
out-of-fold model P(over) at that book's line. For each leg the model picks the side with the larger
EV per unit staked::

    ev_side = p_side * decimal_odds_side - 1 - fee        (fee = 0 for a sportsbook; vig is in the price)

and the leg is *bet* when that EV >= threshold. The same count of legs is then drawn by two rivals:
the market lean (the legs whose de-vigged price is most lopsided, betting the favoured side) and a
random-selection placebo (random legs, random side). ROI is profit per unit staked; every interval is a
game-clustered bootstrap percentile interval.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

MAX_SEASON = 2024
SEASONS = (2023, 2024)
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
THRESHOLDS: tuple[float, ...] = (0.0, 0.02, 0.05, 0.10)
CONF_BINS: tuple[float, ...] = (0.0, 0.45, 0.50, 0.55, 0.60, 1.0001)
N_BOOT = 2000
N_PLACEBO = 200
SEED = 0
MIN_N = 300  # below this a cell is reported "insufficient sample" and nothing is claimed
BOOK = "draftkings"
OUT_DIR = Path("reports/decision_scoring_2026-10-11")
REPORT_MD = Path("reports/decision_scoring_2026-10-11.md")


@dataclass(frozen=True)
class FeeSpec:
    """Per-unit-stake explicit fee. A sportsbook charges none (the vig is inside the price)."""

    per_unit: float = 0.0
    source: str = "sportsbook: no explicit fee; the overround is in the quoted prices"


DK_FEE = FeeSpec()


def american_to_decimal(price: np.ndarray) -> np.ndarray:
    """American odds -> decimal odds (total return per unit staked on a win)."""
    p = np.asarray(price, dtype=np.float64)
    return np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))


def choose_side(
    p_over: np.ndarray,
    price_over: np.ndarray,
    price_under: np.ndarray,
    fee: FeeSpec = DK_FEE,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Model side per leg. Returns (is_over, ev, p_side, decimal_odds); ties go to the over."""
    do, du = american_to_decimal(price_over), american_to_decimal(price_under)
    ev_o = p_over * do - 1.0 - fee.per_unit
    ev_u = (1.0 - p_over) * du - 1.0 - fee.per_unit
    is_over = ev_o >= ev_u
    return (
        is_over,
        np.where(is_over, ev_o, ev_u),
        np.where(is_over, p_over, 1.0 - p_over),
        np.where(is_over, do, du),
    )


def profit_per_unit(is_over: np.ndarray, went_over: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """Profit on a one-unit stake: dec - 1 on a win, -1 on a loss (no pushes: lines end in .5)."""
    win = is_over == (went_over > 0.5)
    return np.where(win, dec - 1.0, -1.0)


class Boot:
    """Game-clustered bootstrap: one multinomial resample matrix shared by every statistic."""

    def __init__(self, gi: np.ndarray, n_games: int, n_boot: int = N_BOOT, seed: int = SEED):
        self.gi, self.G = gi, n_games
        rng = np.random.default_rng(seed)
        self.W = rng.multinomial(n_games, np.full(n_games, 1.0 / n_games), size=n_boot).astype(
            np.float64
        )

    def per_game(self, x: np.ndarray) -> np.ndarray:
        return np.bincount(self.gi, weights=x, minlength=self.G)

    def ratio(self, num: np.ndarray, den: np.ndarray) -> dict[str, Any]:
        ng, dg = self.per_game(num), self.per_game(den)
        d = float(dg.sum())
        if d <= 0:
            return {"est": None, "lo": None, "hi": None, "n": 0}
        est = float(ng.sum() / d)
        bd = self.W @ dg
        with np.errstate(divide="ignore", invalid="ignore"):
            b = np.where(bd > 0, (self.W @ ng) / bd, np.nan)
        lo, hi = np.nanpercentile(b, [2.5, 97.5])
        return {"est": est, "lo": float(lo), "hi": float(hi), "n": int(round(d))}


def _cell(
    boot: Boot, mask: np.ndarray, profit: np.ndarray, hit: np.ndarray, dec: np.ndarray
) -> dict[str, Any]:
    n = int(mask.sum())
    if n == 0:
        return {"n": 0, "status": "no bets"}
    m = mask.astype(np.float64)
    roi = boot.ratio(profit * m, m)
    hr = boot.ratio(hit * m, m)
    out: dict[str, Any] = {
        "n": n,
        "n_games": int(np.unique(boot.gi[mask]).size),
        "roi": roi["est"],
        "roi_lo": roi["lo"],
        "roi_hi": roi["hi"],
        "hit": hr["est"],
        "hit_lo": hr["lo"],
        "hit_hi": hr["hi"],
        "mean_decimal": float(dec[mask].mean()),
        "status": "ok" if n >= MIN_N else "insufficient sample",
    }
    return out


def score_threshold(
    boot: Boot,
    legs: dict[str, np.ndarray],
    elig: np.ndarray,
    threshold: float,
    rng: np.random.Generator,
    n_placebo: int = N_PLACEBO,
) -> dict[str, Any]:
    """Model arm, market-lean arm and placebo arm for one set of eligible legs and one threshold."""
    idx = np.flatnonzero(elig)
    sel = elig & (legs["ev"] >= threshold)
    n = int(sel.sum())
    model = _cell(boot, sel, legs["profit"], legs["hit"], legs["dec"])
    res: dict[str, Any] = {"threshold": threshold, "model": model}
    if n == 0:
        res["market_lean"] = {"n": 0, "status": "no bets"}
        res["placebo"] = {"n": 0, "status": "no bets"}
        return res
    # market lean: the n eligible legs whose de-vigged price is most lopsided, favoured side bet
    gap = np.abs(legs["mult"][idx] - 0.5)
    top = idx[np.argsort(-gap, kind="stable")[:n]]
    lean = np.zeros(elig.size, dtype=bool)
    lean[top] = True
    res["market_lean"] = _cell(boot, lean, legs["profit_mkt"], legs["hit_mkt"], legs["dec_mkt"])
    # placebo: n random eligible legs with a random side, repeated; ROI of each draw (point only)
    rois = np.empty(n_placebo)
    hits = np.empty(n_placebo)
    for k in range(n_placebo):
        pick = rng.choice(idx, size=n, replace=False)
        over = rng.random(n) < 0.5
        dec = np.where(over, legs["dec_o"][pick], legs["dec_u"][pick])
        win = over == (legs["went_over"][pick] > 0.5)
        rois[k] = float(np.where(win, dec - 1.0, -1.0).mean())
        hits[k] = float(win.mean())
    res["placebo"] = {
        "n": n,
        "roi": float(rois.mean()),
        "roi_lo": float(np.percentile(rois, 2.5)),
        "roi_hi": float(np.percentile(rois, 97.5)),
        "hit": float(hits.mean()),
        "draws": n_placebo,
        "model_roi_percentile_in_placebo": float((rois < (model["roi"] or 0.0)).mean()),
        "status": "ok" if n >= MIN_N else "insufficient sample",
    }
    return res


def load_legs(
    pairs_dir: Path, seasons: tuple[int, ...] = SEASONS, fee: FeeSpec = DK_FEE
) -> dict[str, Any]:
    """DraftKings T-60 legs with the model's chosen side, EV, outcome and market-lean columns."""
    frames = []
    for s in seasons:
        if s > MAX_SEASON:
            raise ValueError("season 2025 is the frozen holdout")
        d = pl.read_parquet(pairs_dir / f"pairs_{s}.parquet")
        assert int(d["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
        frames.append(d)
    d = pl.concat(frames, how="diagonal_relaxed")
    d = d.filter(
        (pl.col("book") == BOOK)
        & (pl.col("snapshot_kind") == "t60")
        & pl.col("price_over").is_not_null()
        & pl.col("price_under").is_not_null()
        & pl.col("p_model").is_not_null()
        & pl.col("over").is_not_null()
        & (pl.col("snapshot_ts") < pl.col("tipoff_utc"))  # known-at: the price precedes tip
    ).sort(["game_id", "player_id", "stat"])
    assert d.select(["game_id", "player_id", "stat"]).unique().height == d.height
    games = d["game_id"].unique().sort().to_list()
    gmap = {g: i for i, g in enumerate(games)}
    gi = np.array([gmap[g] for g in d["game_id"].to_list()], dtype=np.int64)

    def f(c: str) -> np.ndarray:
        return d[c].cast(pl.Float64).to_numpy()

    po, pu = f("price_over"), f("price_under")
    p = f("p_model")
    went = f("over")
    is_over, ev, p_side, dec = choose_side(p, po, pu, fee)
    mult = f("p_over_mult")
    mk_over = mult >= 0.5
    do, du = american_to_decimal(po), american_to_decimal(pu)
    dec_mkt = np.where(mk_over, do, du)
    return {
        "gi": gi,
        "G": len(games),
        "season": d["season"].to_numpy(),
        "stat": d["stat"].to_numpy(),
        "game_id": d["game_id"].to_numpy(),
        "player_id": d["player_id"].to_numpy(),
        "game_date": d["game_date"].to_list(),
        "line": f("line"),
        "price_over": po,
        "price_under": pu,
        "p_model": p,
        "mult": mult,
        "went_over": went,
        "is_over": is_over,
        "ev": ev,
        "p_side": p_side,
        "p_mkt_side": np.where(is_over, mult, 1.0 - mult),
        "dec": dec,
        "dec_o": do,
        "dec_u": du,
        "profit": profit_per_unit(is_over, went, dec),
        "hit": (is_over == (went > 0.5)).astype(np.float64),
        "dec_mkt": dec_mkt,
        "profit_mkt": profit_per_unit(mk_over, went, dec_mkt),
        "hit_mkt": (mk_over == (went > 0.5)).astype(np.float64),
    }


def run_all(legs: dict[str, Any]) -> dict[str, Any]:
    """Every (season set x stat x threshold) cell plus the confidence-bin table."""
    boot = Boot(legs["gi"], legs["G"])
    rng = np.random.default_rng(SEED + 1)
    out: dict[str, Any] = {"cells": [], "conf_bins": []}
    season_sets: dict[str, np.ndarray] = {"pooled": np.ones(legs["gi"].size, dtype=bool)}
    for s in SEASONS:
        season_sets[str(s)] = legs["season"] == s
    stat_sets: dict[str, np.ndarray] = {"all": np.ones(legs["gi"].size, dtype=bool)}
    for st in STATS:
        stat_sets[st] = legs["stat"] == st
    for sname, sm in season_sets.items():
        for tname, tm in stat_sets.items():
            elig = sm & tm
            out["cells"].append(
                {
                    "season": sname,
                    "stat": tname,
                    "n_eligible_legs": int(elig.sum()),
                    "by_threshold": [score_threshold(boot, legs, elig, t, rng) for t in THRESHOLDS],
                }
            )
    # confidence bins: model's chosen-side probability, all legs with EV >= 0 (pooled seasons)
    pos = legs["ev"] >= 0.0
    for lo, hi in zip(CONF_BINS[:-1], CONF_BINS[1:], strict=True):
        m = pos & (legs["p_side"] >= lo) & (legs["p_side"] < hi)
        c = _cell(boot, m, legs["profit"], legs["hit"], legs["dec"])
        if m.any():
            c["mean_p_model"] = float(legs["p_side"][m].mean())
            c["mean_p_market_devigged"] = float(legs["p_mkt_side"][m].mean())
        c["bin"] = f"[{lo:.2f}, {min(hi, 1.0):.2f})"
        out["conf_bins"].append(c)
    out["meta"] = {
        "book": BOOK,
        "fee": {"per_unit": DK_FEE.per_unit, "source": DK_FEE.source},
        "n_legs": int(legs["gi"].size),
        "n_games": int(legs["G"]),
        "seasons": list(SEASONS),
        "n_boot": N_BOOT,
        "n_placebo": N_PLACEBO,
        "min_n": MIN_N,
        "descriptive": True,
    }
    return out


def _pct(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:+.{nd}f}%"


def _row(label: str, c: dict[str, Any]) -> str:
    if c.get("n", 0) == 0:
        return f"| {label} | 0 | | | | |"
    roi = f"{_pct(c.get('roi'))} [{_pct(c.get('roi_lo'))}, {_pct(c.get('roi_hi'))}]"
    hit = f"{100 * c['hit']:.1f}%" if c.get("hit") is not None else ""
    dec = f"{c['mean_decimal']:.3f}" if "mean_decimal" in c else ""
    flag = "" if c.get("status") == "ok" else " (insufficient sample)"
    return f"| {label} | {c['n']} | {hit} | {dec} | {roi}{flag} |"


def render_tables(res: dict[str, Any]) -> str:
    L: list[str] = []
    for cell in res["cells"]:
        if cell["season"] != "pooled":
            continue
        L += [
            f"### Pooled 2023+2024, stat = {cell['stat']} ({cell['n_eligible_legs']} eligible legs)",
            "",
        ]
        L += [
            "| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |",
            "|---|---|---|---|---|",
        ]
        for bt in cell["by_threshold"]:
            t = f"{100 * bt['threshold']:.0f}%"
            L.append(_row(f"model, EV >= {t}", bt["model"]))
            L.append(_row("market lean, same count", bt["market_lean"]))
            p = bt["placebo"]
            if p.get("n", 0):
                L.append(
                    f"| random placebo, same count | {p['n']} | {100 * p['hit']:.1f}% | | "
                    f"{_pct(p['roi'])} [{_pct(p['roi_lo'])}, {_pct(p['roi_hi'])}] draws |"
                )
        L.append("")
    L += [
        "### By season (model arm only, all stats)",
        "",
        "| season | EV >= | bets | hit | ROI [95% CI] |",
        "|---|---|---|---|---|",
    ]
    for cell in res["cells"]:
        if cell["stat"] != "all" or cell["season"] == "pooled":
            continue
        for bt in cell["by_threshold"]:
            m = bt["model"]
            if m.get("n", 0):
                L.append(
                    f"| {cell['season']} | {100 * bt['threshold']:.0f}% | {m['n']} | {100 * m['hit']:.1f}% | "
                    f"{_pct(m['roi'])} [{_pct(m['roi_lo'])}, {_pct(m['roi_hi'])}] |"
                )
    L += [
        "",
        "### By confidence bin (model chosen-side probability; bets with EV >= 0; pooled)",
        "",
        "| bin | bets | hit rate | model mean p | market de-vigged mean p | ROI [95% CI] |",
        "|---|---|---|---|---|---|",
    ]
    for c in res["conf_bins"]:
        if c.get("n", 0) == 0:
            L.append(f"| {c['bin']} | 0 | | | | |")
            continue
        flag = "" if c["status"] == "ok" else " (insufficient sample)"
        L.append(
            f"| {c['bin']} | {c['n']} | {100 * c['hit']:.1f}% | {100 * c['mean_p_model']:.1f}% | "
            f"{100 * c['mean_p_market_devigged']:.1f}% | {_pct(c['roi'])} [{_pct(c['roi_lo'])}, {_pct(c['roi_hi'])}]{flag} |"
        )
    return "\n".join(L)


def example_bet(legs: dict[str, Any], threshold: float = 0.05) -> dict[str, Any]:
    """The bet closest to the median EV among EV >= threshold bets (deterministic, not cherry-picked)."""
    idx = np.flatnonzero(legs["ev"] >= threshold)
    med = np.median(legs["ev"][idx])
    k = int(idx[np.argmin(np.abs(legs["ev"][idx] - med))])
    return {
        "game_id": str(legs["game_id"][k]),
        "game_date": str(legs["game_date"][k]),
        "player_id": int(legs["player_id"][k]),
        "stat": str(legs["stat"][k]),
        "line": float(legs["line"][k]),
        "side": "over" if legs["is_over"][k] else "under",
        "price_over": int(legs["price_over"][k]),
        "price_under": int(legs["price_under"][k]),
        "p_model_over": float(legs["p_model"][k]),
        "market_devigged_over": float(legs["mult"][k]),
        "ev_per_unit": float(legs["ev"][k]),
        "went_over": bool(legs["went_over"][k] > 0.5),
        "profit_per_unit": float(legs["profit"][k]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--pairs-dir", type=Path, default=Path("data/ops/model_clv_pairs"))
    r.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    legs = load_legs(a.pairs_dir)
    res = run_all(legs)
    res["example"] = example_bet(legs)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "results.json").write_text(json.dumps(res, indent=2))
    (a.out / "tables.md").write_text(render_tables(res))
    print(f"wrote {a.out}/results.json and tables.md; legs={res['meta']['n_legs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
