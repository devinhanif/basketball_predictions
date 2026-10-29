# ruff: noqa: E501
"""LINE_CAL_LIVE minimum-data gate (rule: docs/prereg/LINE_CAL_LIVE.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.line_cal_live_gate \\
        --data-root /Users/devin/Downloads/nba-prediction --until 2026-10-28 \\
        --out reports/prereg_line_cal_live/gate_draft.json

Label-free. It never selects ``y``, ``crps``, ``brier`` or ``log_loss`` from ``forward_scores`` and
never reads a box-score stat value; the only box column read is ``minutes`` of games strictly BEFORE
each row's date (the pre-tip minutes-volatility flag). No map is fitted: the frozen LINE_CALIBRATION
map (``nba/parlay/line_cal_map_v1.json``, sha 443743a1) is applied as served. Power uses synthetic
labels drawn from declared truth families on the real live row and game structure. Season 2026 only
(``season = 2026`` in SQL); season 2025 is never read.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.forward.scorecard import MARKET_STAT, _main_point
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts

RULE = "LINE_CAL_LIVE"
SEASON = 2026
SINCE = date(2026, 10, 20)
PROD = "props_context_residual"
STATS = ("pts", "reb", "ast", "fg3m")
BOOKS = ("pinnacle", "draftkings")
MAP_SHA = "443743a1543fe5fbaaf97bd072bf6fba56e172edbca5839cd1eb85d711349b25"
SNAPSHOT = "t60"
MIN_LEAD_MIN = 55.0
PPLAY_UNCERTAIN = 0.9
MIN_SD_HIGH = 6.0  # minutes; sd over the player's season-2026 games before this date
MIN_SD_GAMES = 4
FLOOR_BRIER = -0.0015
LAMBDAS = (0.0, 0.25, 0.4)
GAMES_GRID = (100, 150, 200, 250, 300, 400)
N_SYNTH = 200
SEED = 0


@dataclass(frozen=True)
class Leg:
    game_id: str
    game_date: date
    stat: str
    player_id: int
    book: str
    line: float
    lead_min: float
    made_before_line: bool
    p_raw: float
    p_cal: float
    p_cal_199: float | None
    starter: bool
    p_play: float | None
    proj_min: float | None
    min_sd: float | None
    mean: float
    p_mkt: float
    made_at: datetime | None = None


def side_under(p_raw: np.ndarray) -> np.ndarray:
    """The model's own side: under when raw P(over) < 0.5 (the scorecard's mean-vs-line side is a
    bridge row only)."""
    return np.asarray(p_raw < 0.5)


def minutes_sd(prior: list[float]) -> float | None:
    if len(prior) < MIN_SD_GAMES:
        return None
    return float(np.std(np.asarray(prior, dtype=float), ddof=1))


def clustered_se(d: np.ndarray, gi: np.ndarray, n_groups: int) -> float:
    """Standard error of a row mean under game clustering (ratio-estimator linearisation)."""
    gs = np.bincount(gi, d, n_groups)
    gn = np.bincount(gi, None, n_groups)
    m = float(d.mean())
    return float(np.sqrt(np.sum((gs - m * gn) ** 2)) / gn.sum())


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def synthetic_power(
    p_raw: np.ndarray,
    p_cal: np.ndarray,
    games: np.ndarray,
    lambdas: tuple[float, ...] = LAMBDAS,
    games_grid: tuple[int, ...] = GAMES_GRID,
    n_synth: int = N_SYNTH,
    seed: int = SEED,
    floor: float = FLOOR_BRIER,
) -> dict[str, Any]:
    """Brier (CAL - RAW) power at synthetic truth p_t = sigmoid((1 - lam) logit p_cal + lam logit p_raw).
    lam = 0: the frozen map transports exactly; lam = 0.5: about no gain. Labels are synthetic only."""
    rng = np.random.default_rng(seed)
    ug, gi = np.unique(games, return_inverse=True)
    n_g = len(ug)
    under = side_under(p_raw)
    out: dict[str, Any] = {}
    for lam in lambdas:
        pt = lc.sigmoid((1 - lam) * lc.logit(p_cal) + lam * lc.logit(p_raw))
        acc: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for _ in range(n_synth):
            y = (rng.random(len(pt)) < pt).astype(float)
            d = (p_cal - y) ** 2 - (p_raw - y) ** 2
            for name, k in (("all", np.ones_like(under)), ("under", under), ("over", ~under)):
                if not k.any():
                    continue
                dk = d[k]
                acc[name].append((float(dk.mean()), clustered_se(dk, gi[k], n_g) * math.sqrt(n_g)))
        cell: dict[str, Any] = {}
        for name, v in acc.items():
            eff, sd_g = (float(x) for x in np.mean(np.asarray(v), axis=0))
            by_g = {}
            for gg in games_grid:
                se = max(sd_g / math.sqrt(gg), 1e-12)
                by_g[str(gg)] = {
                    "half_width": round(1.96 * se, 5),
                    "p_ci_upper_lt_0": round(_phi((-eff - 1.96 * se) / se), 3),
                    "p_point_le_floor": round(_phi((floor - eff) / se), 3),
                    "p_pass_primary": round(_phi((min(floor, -1.96 * se) - eff) / se), 3),
                }
            cell[name] = {
                "expected": round(eff, 5),
                "per_game_sd": round(sd_g, 4),
                "by_games": by_g,
            }
        out[f"lam_{lam}"] = cell
    return out


def _pairs_x_band(root: Path) -> dict[str, list[float]]:
    """1st/99th percentile of x = logit p_model on the map's own Pinnacle fit legs (label-free)."""
    band: dict[str, list[float]] = {}
    xs: dict[str, list[np.ndarray]] = defaultdict(list)
    for s in (2023, 2024):
        d = pl.read_parquet(
            root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
            columns=["book", "stat", "p_model", "snapshot_kind", "season"],
        ).filter((pl.col("book") == "pinnacle") & (pl.col("snapshot_kind") == "t60"))
        if d["season"].max() != s:
            raise ValueError("unexpected season in pairs frame")
        for st in STATS:
            p = d.filter(pl.col("stat") == st)["p_model"].to_numpy()
            xs[st].append(lc.logit(np.clip(p, lc.EPS, 1 - lc.EPS)))
    for st in STATS:
        x = np.concatenate(xs[st])
        band[st] = [float(np.quantile(x, 0.01)), float(np.quantile(x, 0.99))]
    return band


def load_legs(
    root: Path, since: date, until: date, book: str, status: str = "scored"
) -> tuple[list[Leg], dict[str, int]]:
    """Label-free legs: forecast, book line, RAW and CAL. ``status`` 'scored' (played) or 'dnp'
    (voided; LINE_CAL_LIVE D3). Never selects y or any score column."""
    if until.year > 2027 or since < SINCE:
        raise ValueError("window outside season 2026")
    if status not in ("scored", "dnp"):
        raise ValueError("status must be 'scored' or 'dnp'")
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    rows = con.execute(
        """SELECT s.game_id, s.game_date, s.target, s.player_id, s.made_at, CAST(p.prediction AS VARCHAR)
           FROM forward_scores s JOIN forward_predictions p
             ON p.game_id = s.game_id AND p.model_name = s.model_name AND p.target = s.target
            AND p.player_id = s.player_id AND p.made_at = s.made_at
           WHERE s.status = ? AND s.model_name = ? AND s.season = ?
             AND s.game_date BETWEEN ? AND ? AND s.target IN ('pts','reb','ast','fg3m')""",
        [status, PROD, SEASON, since, until],
    ).fetchall()
    latest: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    for r in rows:
        k = (r[0], r[2], int(r[3]))
        if k not in latest or r[4] > latest[k][4]:
            latest[k] = r
    box = con.execute(
        """SELECT s.player_id, g.game_date, s.minutes FROM player_game_stats s JOIN games g USING (game_id)
           WHERE g.season = ? AND g.game_date < ? AND s.minutes > 0""",
        [SEASON, until],
    ).fetchall()
    mins: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for pid, gd, mn in box:
        mins[int(pid)].append((gd, float(mn)))
    grids = {
        (r[0], int(r[1]), r[2], r[3]): np.asarray(r[4], dtype=float)
        for r in con.execute(
            """SELECT game_id, player_id, stat, made_at, q199 FROM forward_prop_grid199
               WHERE model = ? AND made_at >= ?""",
            [PROD, datetime(since.year, since.month, since.day)],
        ).fetchall()
    }
    con.close()
    gids = sorted({k[0] for k in latest})
    odds = duckdb.connect(str(root / "data/markets/market_asof.duckdb"), read_only=True)
    gm = ",".join("?" for _ in gids)
    mm = ",".join("?" for _ in MARKET_STAT)
    raw = odds.execute(
        f"""SELECT game_id, player_id, market, book, side, point, price_american, received_at,
                   snapshot_kind, tipoff
            FROM live_book_odds WHERE game_id IN ({gm}) AND market IN ({mm}) AND book = ?
             AND snapshot_kind = ? AND player_id IS NOT NULL AND point IS NOT NULL
             AND price_american IS NOT NULL AND side IN ('over','under')
             AND slate_date BETWEEN ? AND ?""",  # noqa: S608
        [*gids, *MARKET_STAT, book, SNAPSHOT, since, until],
    ).fetchall()
    odds.close()
    grp: dict[tuple[str, int, str], list[tuple[Any, ...]]] = defaultdict(list)
    for r in raw:
        grp[(r[0], int(r[1]), MARKET_STAT[r[2]])].append(r)
    cmap = lc.load_map()
    if cmap.meta["map_sha256"] != MAP_SHA:
        raise ValueError("served LINE_CALIBRATION map differs from the frozen one")
    legs: list[Leg] = []
    why: dict[str, int] = defaultdict(int)
    why["scored_rows"] = len(latest)
    for (gid, stat, pid), r in sorted(latest.items()):
        pred = json.loads(r[5])
        pre = [x for x in grp.get((gid, pid, stat), []) if x[9] is not None and x[7] < x[9]]
        if not pre:
            why["no_t60_line"] += 1
            continue
        last = max(x[7] for x in pre)
        mp = _main_point([x for x in pre if x[7] == last])
        if mp is None:
            why["no_two_sided_main"] += 1
            continue
        line, po_m, pu_m = mp
        lead = (pre[0][9] - last).total_seconds() / 60.0
        if lead < MIN_LEAD_MIN:
            why["lead_below_55"] += 1
            continue
        po = ts.p_over(pred, line)
        if po is None:
            why["whole_number_line"] += 1
            continue
        qs = ts.quartiles({"pred": pred})
        sr = pred.get("starter_rate")
        if qs is None or sr is None:
            why["no_quartiles_or_starter_rate"] += 1
            continue
        pc = cmap.apply(book, stat, po, line, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, float(sr))
        g199 = grids.get((gid, pid, stat, r[4]))
        pc199 = None
        if g199 is not None and g199.size == 199:
            q25, q50, q75 = lc.sorted_grid_quantiles(g199)
            pc199 = cmap.apply(book, stat, po, line, q50, (q75 - q25) / lc.IQR_TO_SD, float(sr))
        prior = [m for d, m in mins.get(pid, []) if d < r[1]]
        pp = pred.get("p_play")
        pm = pred.get("proj_minutes")
        legs.append(
            Leg(
                game_id=gid, game_date=r[1], stat=stat, player_id=pid, book=book, line=float(line),
                lead_min=lead, made_before_line=bool(r[4] <= last),
                p_raw=float(np.clip(po, lc.EPS, 1 - lc.EPS)), p_cal=float(pc), p_cal_199=pc199,
                starter=float(sr) >= 0.5, p_play=None if pp is None else float(pp),
                proj_min=None if pm is None else float(pm), min_sd=minutes_sd(prior),
                mean=float(pred["mean"]), p_mkt=float(po_m / (po_m + pu_m)), made_at=r[4],
            )
        )  # fmt: skip
    return legs, dict(why)


def summarise(legs: list[Leg], band: dict[str, list[float]] | None) -> dict[str, Any]:
    if not legs:
        return {"n": 0}
    pr = np.array([x.p_raw for x in legs])
    pc = np.array([x.p_cal for x in legs])
    g = np.array([x.game_id for x in legs])
    st = np.array([x.stat for x in legs])
    under = side_under(pr)
    starter = np.array([x.starter for x in legs])
    pp = np.array([np.nan if x.p_play is None else x.p_play for x in legs])
    msd = np.array([np.nan if x.min_sd is None else x.min_sd for x in legs])
    mean_under = np.array([x.mean < x.line for x in legs])
    dates = sorted({x.game_date for x in legs})
    per_date_n: dict[str, int] = defaultdict(int)
    per_date_g: dict[str, set[str]] = defaultdict(set)
    for x in legs:
        per_date_n[str(x.game_date)] += 1
        per_date_g[str(x.game_date)].add(x.game_id)
    d199 = [abs(x.p_cal - x.p_cal_199) for x in legs if x.p_cal_199 is not None]
    sp_raw = np.where(under, 1 - pr, pr)
    sp_cal = np.where(under, 1 - pc, pc)
    out: dict[str, Any] = {
        "n": len(legs), "games": int(len(set(g))), "dates": len(dates),
        "first_date": str(dates[0]), "last_date": str(dates[-1]),
        "legs_per_game": round(len(legs) / len(set(g)), 2),
        "per_date": {k: {"legs": v, "games": len(per_date_g[k])} for k, v in sorted(per_date_n.items())},
        "lead_min": {"median": float(np.median([x.lead_min for x in legs])), "min": float(min(x.lead_min for x in legs))},
        "forecast_before_line_share": float(np.mean([x.made_before_line for x in legs])),
        "side_raw": {"under": int(under.sum()), "over": int((~under).sum())},
        "side_mean_vs_line_bridge": {"under": int(mean_under.sum()), "over": int((~mean_under).sum()),
                                     "disagree_with_raw_side": int((mean_under != under).sum())},
        "by_stat_side": {s: {"under": int((under & (st == s)).sum()), "over": int((~under & (st == s)).sum())} for s in STATS},
        "starter": int(starter.sum()), "bench": int((~starter).sum()),
        "bench_by_side": {"under": int((~starter & under).sum()), "over": int((~starter & ~under).sum())},
        "p_play_lt_0.9": int(np.nansum(pp < PPLAY_UNCERTAIN)), "p_play_missing": int(np.isnan(pp).sum()),
        "min_sd_high": int(np.nansum(msd >= MIN_SD_HIGH)), "min_sd_unknown": int(np.isnan(msd).sum()),
        "minutes_uncertain_any": int(np.sum((pp < PPLAY_UNCERTAIN) | (msd >= MIN_SD_HIGH))),
        "label_free_shift": {
            "stated_side_p_raw": {"all": round(float(sp_raw.mean()), 4), "under": round(float(sp_raw[under].mean()), 4), "over": round(float(sp_raw[~under].mean()), 4)},
            "stated_side_p_cal": {"all": round(float(sp_cal.mean()), 4), "under": round(float(sp_cal[under].mean()), 4), "over": round(float(sp_cal[~under].mean()), 4)},
            "mean_p_over": {"raw": round(float(pr.mean()), 4), "cal": round(float(pc.mean()), 4),
                            "book_devig": round(float(np.mean([x.p_mkt for x in legs])), 4)},
            "cal_flips_side_share": round(float(np.mean((pc >= 0.5) != (pr >= 0.5))), 4),
        },
        "p_cal_19_vs_199": {"n_with_grid": len(d199), "max_abs_diff": float(max(d199)) if d199 else None},
    }  # fmt: skip
    if band is not None:
        xr = lc.logit(pr)
        outside = np.zeros(len(legs), bool)
        for s in STATS:
            k = st == s
            outside[k] = (xr[k] < band[s][0]) | (xr[k] > band[s][1])
        out["x_outside_fit_band_1_99"] = {
            "n": int(outside.sum()),
            "share": round(float(outside.mean()), 4),
            "band": band,
        }
    return out


def project_games(root: Path, until: date, share: float) -> dict[str, Any]:
    """Date at which the window after ``until`` reaches each games target: scheduled games per ET
    date (schedule file) times the measured share of scored games that carry at least one leg."""
    s = pl.read_parquet(root / "data/schedule/live_tips_2026-27.parquet")
    days = (
        s.with_columns(pl.col("gameDateTimeEst").str.slice(0, 10).str.to_date().alias("d"))
        .group_by("d")
        .len()
        .sort("d")
    )
    sched = [(d, n) for d, n in days.iter_rows() if d > until]
    out: dict[str, Any] = {}
    for target in GAMES_GRID:
        acc = 0.0
        hit = None
        for d, n in sched:
            acc += float(n) * share
            if acc >= target:
                hit = str(d)
                break
        out[str(target)] = hit
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--until", type=date.fromisoformat, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    t0 = time.time()
    band = _pairs_x_band(a.data_root)
    rep: dict[str, Any] = {"rule": RULE, "status": "DRAFT gate, label-free", "map_sha256": MAP_SHA,
                           "window": [str(SINCE), str(a.until)], "season": SEASON}  # fmt: skip
    for book in BOOKS:
        legs, why = load_legs(a.data_root, SINCE, a.until, book)
        print(f"[{book}] legs {len(legs)}; drops {why}", flush=True)
        cell = summarise(legs, band if book == "pinnacle" else None)
        cell["drops"] = why
        if legs:
            pr = np.array([x.p_raw for x in legs])
            pc = np.array([x.p_cal for x in legs])
            g = np.array([x.game_id for x in legs])
            cell["power"] = synthetic_power(pr, pc, g)
            # share of scheduled games in the window with >= 1 leg
            con = duckdb.connect(str(a.data_root / "nba.duckdb"), read_only=True)
            n_sched = con.execute(
                "SELECT count(DISTINCT game_id) FROM forward_scores WHERE model_name = ? AND season = ? "
                "AND status = 'scored' AND game_date BETWEEN ? AND ?",
                [PROD, SEASON, SINCE, a.until],
            ).fetchone()
            con.close()
            share = cell["games"] / float(n_sched[0]) if n_sched and n_sched[0] else 0.0
            cell["share_of_scored_games_with_leg"] = round(share, 4)
            cell["projected_date_for_games"] = project_games(a.data_root, a.until, share)
        rep[book] = cell
    rep["wall_s"] = round(time.time() - t0, 1)
    txt = json.dumps(rep, indent=1, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
