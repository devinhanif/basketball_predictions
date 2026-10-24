# ruff: noqa: E501
"""Label-free scope gate: which Kalshi N+ props fall inside the ALT_RUNG_CAL fit support?

Read-only scoping tool for docs/CALIBRATION_EVERYWHERE_PLAN.md. For every Kalshi player-prop market
on the forward regular-season slates it records, from stored forecasts and pre-tip quotes only,
whether the rung map (``nba.parlay.rung_cal``, DraftKings 2023-24 rungs) could be applied inside the
region it was fitted on. It counts settled markets (``result IS NOT NULL``) and never reads a result
value. Nothing is written to any store; all databases are opened ``read_only=True``.

Fit support (a fact about the frozen map, from ``reports/alt_rung_cal/arrays_draftkings.npz``): per
stat, the 1st and 99th percentile of the signed rung distance ``r`` and of ``zm`` over the scored
rungs, and the rule that the raw P(over) is at least 0.5 (``rung_cal.FIT_SUPPORT_MIN_P``).

Categories (mutually exclusive, in this order): ``no_forecast``, ``no_dk_main`` (no two-sided
DraftKings main line at the prediction clock, so the map's ``zm`` and ``r`` are undefined),
``at_main`` (the Kalshi line equals the DK main line: both maps apply), ``in_support`` (raw P >= 0.5,
r and zm inside the fit band), ``below_half`` (raw P < 0.5, outside the fit set), ``outside_band``
(P >= 0.5 but r or zm outside the fit's 1st-99th percentile).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np

from nba.parlay import rung_cal as rc
from nba.parlay.kalshi_map import parse_event

ET = ZoneInfo("America/New_York")
SERIES_STAT = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
BOOK_MARKET = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
PROD = "props_context_residual"
LEAD = timedelta(minutes=60)
CATEGORIES = ("no_forecast", "no_dk_main", "at_main", "in_support", "below_half", "outside_band")


@dataclass(frozen=True)
class Band:
    r_lo: float
    r_hi: float
    zm_lo: float
    zm_hi: float
    n: int


def fit_bands(arrays_npz: Path) -> dict[str, Band]:
    """Per-stat 1st/99th percentile of r and zm over the scored DraftKings rungs. ``zm`` is not stored
    in the arrays, so only ``r`` is banded from them; ``zm`` is banded by the clip the map applies
    (+-5, ``rung_cal.ZM_CLIP``). The fit set already holds only raw P >= 0.5."""
    z = np.load(arrays_npz, allow_pickle=False)
    out: dict[str, Band] = {}
    for stat in rc.STATS:
        m = z["stat"] == stat
        r = z["r"][m]
        out[stat] = Band(
            float(np.percentile(r, 1)),
            float(np.percentile(r, 99)),
            -rc.ZM_CLIP,
            rc.ZM_CLIP,
            int(m.sum()),
        )
    return out


def classify(
    p_raw: float | None,
    kalshi_line: float,
    main: float | None,
    r: float | None,
    zm: float | None,
    band: Band,
) -> str:
    """Category of one Kalshi rung (``kalshi_line`` = threshold - 0.5, the 'over' line)."""
    if p_raw is None:
        return "no_forecast"
    if main is None or r is None or zm is None:
        return "no_dk_main"
    if abs(kalshi_line - main) < 1e-9:
        return "at_main"
    if p_raw < rc.FIT_SUPPORT_MIN_P:
        return "below_half"
    inside = band.r_lo <= r <= band.r_hi and band.zm_lo <= zm <= band.zm_hi
    return "in_support" if inside else "outside_band"


def _tip_et(tip: datetime) -> date:
    return tip.replace(tzinfo=UTC).astimezone(ET).date()


def run(
    nba_db: Path,
    kalshi_db: Path,
    market_db: Path,
    arrays_npz: Path,
    since: date,
    until: date,
) -> dict[str, Any]:
    bands = fit_bands(arrays_npz)
    rmap = rc.load_map()
    nba = duckdb.connect(str(nba_db), read_only=True)
    kal = duckdb.connect(str(kalshi_db), read_only=True)
    mkt = duckdb.connect(str(market_db), read_only=True)
    # production rows, newest made_at <= tip - 60 min, per (ET date, player, stat)
    rows = nba.execute(
        """SELECT game_id, tipoff, made_at, target, player_id, CAST(prediction AS VARCHAR), run_id
           FROM forward_predictions
           WHERE model_name = ? AND target IN ('pts','reb','ast','fg3m') AND game_id LIKE '002%'
             AND made_at <= tipoff - INTERVAL 60 MINUTE
           ORDER BY made_at""",
        [PROD],
    ).fetchall()
    fc: dict[tuple[date, int, str], dict[str, Any]] = {}
    for gid, tip, made, stat, pid, pj, run_id in rows:
        d = _tip_et(tip)
        if not since <= d <= until:
            continue
        pred = json.loads(pj)
        if pred.get("comparison_only"):
            continue
        fc[(d, int(pid), str(stat))] = {
            "gid": gid, "tip": tip, "made": made, "pred": pred, "run_id": run_id,
        }  # fmt: skip
    grids = {
        (g, int(p), s, rid): q
        for g, p, s, rid, q in nba.execute(
            "SELECT game_id, player_id, stat, run_id, q199 FROM forward_prop_grid199"
        ).fetchall()
    }
    # DraftKings main-line boards at or before tip - 60 min: newest snapshot per game, two-sided pairs
    quotes = mkt.execute(
        """SELECT game_id, player_id, market, point, side, received_at, tipoff
           FROM live_book_odds
           WHERE book = 'draftkings' AND market IN ('player_points','player_rebounds',
                 'player_assists','player_threes') AND player_id IS NOT NULL
             AND point IS NOT NULL AND side IN ('over','under')"""
    ).fetchall()
    board: dict[tuple[str, int, str], dict[float, dict[str, datetime]]] = defaultdict(dict)
    for gid, pid, market, point, side, rec, tip in quotes:
        if tip is None or rec > tip - LEAD:
            continue
        slot = board[(gid, int(pid), BOOK_MARKET[market])].setdefault(float(point), {})
        if side not in slot or rec > slot[side]:
            slot[side] = rec

    def dk_main(gid: str, pid: int, stat: str) -> float | None:
        pairs = {
            ln: min(s["over"], s["under"])
            for ln, s in board.get((gid, pid, stat), {}).items()
            if len(s) == 2
        }
        if not pairs:
            return None
        best = max(pairs.values())
        at_best = [ln for ln, t in pairs.items() if t == best]
        return at_best[0] if len(at_best) == 1 else None

    mk = kal.execute(
        """SELECT ticker, series_ticker, event_ticker, player_id, stat, threshold,
                  (result IS NOT NULL) AS settled
           FROM kalshi_markets
           WHERE series_ticker IN ('KXNBAPTS','KXNBAREB','KXNBAAST','KXNBA3PT')
             AND player_id IS NOT NULL AND threshold IS NOT NULL"""
    ).fetchall()
    cat: Counter[tuple[str, str]] = Counter()
    settled: Counter[tuple[str, str]] = Counter()
    games: dict[str, set[str]] = defaultdict(set)
    dates: dict[str, set[date]] = defaultdict(set)
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    gaps: dict[str, list[float]] = defaultdict(list)
    detail: list[dict[str, Any]] = []
    why_no_main: Counter[str] = Counter()
    for ticker, series, event, pid, _stat, thr, is_settled in mk:
        ev = parse_event(str(event))
        stat = SERIES_STAT[str(series)]
        if ev is None or not since <= ev[0] <= until:
            continue
        f = fc.get((ev[0], int(pid), stat))
        if f is None:
            cat[(stat, "no_forecast")] += 1
            settled[(stat, "no_forecast")] += int(bool(is_settled))
            continue
        full = f["pred"].get("p_ge_full") or {}
        counts, n = full.get("c"), full.get("n")
        k = int(round(float(thr)))
        p_raw = None
        if counts and n:
            p_raw = (
                1.0 if k <= 0 else (float(counts[k - 1]) / float(n) if k <= len(counts) else 0.0)
            )
        g = grids.get((f["gid"], int(pid), stat, f["run_id"]))
        main = dk_main(f["gid"], int(pid), stat)
        r = zm = None
        if g is not None and main is not None:
            med, spr = rc.grid_med_spr(np.asarray(g, dtype=float))
            line = float(thr) - 0.5
            r = float(np.clip((line - main) / spr, -rc.R_CLIP, rc.R_CLIP))
            zm = float(np.clip((main - med) / spr, -rc.ZM_CLIP, rc.ZM_CLIP))
        elif main is not None:
            main = None  # no persisted grid: the map's median and spread are unavailable
            why_no_main["dk_main_but_no_grid199"] += 1
        else:
            why_no_main["no_two_sided_dk_main_before_t60"] += 1
        c = classify(p_raw, float(thr) - 0.5, main, r, zm, bands[stat])
        cat[(stat, c)] += 1
        settled[(stat, c)] += int(bool(is_settled))
        if c in ("at_main", "in_support", "below_half", "outside_band"):
            assert p_raw is not None and g is not None and main is not None
            starter = f["pred"].get("starter_rate")
            if starter is not None and rmap.has(stat):
                med, spr = rc.grid_med_spr(np.asarray(g, dtype=float))
                pc = float(
                    rmap.apply_many(
                        stat, np.array([p_raw]), np.array([float(thr) - 0.5]), main, med, spr,
                        float(starter),
                    )[0]
                )  # fmt: skip
                row = kal.execute(
                    "SELECT (yes_bid + yes_ask) / 2 FROM kalshi_prices WHERE ticker = ? "
                    "AND ts <= ? AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1",
                    [ticker, f["tip"] - LEAD],
                ).fetchone()
                mid = None if not row or row[0] is None else float(row[0])
                detail.append(
                    {"ticker": str(ticker), "game_id": str(f["gid"]), "date": ev[0].isoformat(), "player_id": int(pid), "stat": stat, "k": k,
                     "category": c, "p_raw": p_raw, "p_rung_cal": pc, "mid": mid, "r": r, "zm": zm}
                )  # fmt: skip
        if c in ("at_main", "in_support"):
            games[c].add(f["gid"])
            dates[c].add(ev[0])
            if detail and detail[-1]["ticker"] == str(ticker) and detail[-1]["mid"] is not None:
                d0 = detail[-1]
                s = sums[c]
                s[0] += 1
                s[1] += d0["p_raw"]
                s[2] += d0["p_rung_cal"]
                s[3] += d0["mid"]
                gaps[c].append(abs(d0["p_raw"] - d0["mid"]) - abs(d0["p_rung_cal"] - d0["mid"]))
    by_cat = {c: sum(v for (s, cc), v in cat.items() if cc == c) for c in CATEGORIES}
    by_stat = {s: {c: cat[(s, c)] for c in CATEGORIES} for s in rc.STATS}
    sett = {c: sum(v for (s, cc), v in settled.items() if cc == c) for c in CATEGORIES}
    n_dates = len({d for ds in dates.values() for d in ds})
    scoped = [d0 for d0 in detail if d0["category"] in ("at_main", "in_support")]
    union_games = {d0["game_id"] for d0 in scoped}
    all_games = {f["gid"] for f in fc.values()}
    return {
        "window": [since.isoformat(), until.isoformat()],
        "bands": {s: vars(b) for s, b in bands.items()},
        "markets_by_category": by_cat,
        "settled_by_category": sett,
        "by_stat": by_stat,
        "games_with_in_scope_leg": {c: len(g) for c, g in games.items()},
        "dates_with_in_scope_leg": n_dates,
        "games_with_any_in_scope_leg": len(union_games),
        "games_with_regular_season_forecast": len(all_games),
        "in_scope_legs_per_in_scope_game": (len(scoped) / len(union_games))
        if union_games
        else None,
        "descriptive_means": {
            c: {
                "n_with_mid": int(v[0]),
                "mean_raw": v[1] / v[0] if v[0] else None,
                "mean_rung_cal": v[2] / v[0] if v[0] else None,
                "mean_kalshi_mid": v[3] / v[0] if v[0] else None,
                "mean_abs_gap_reduction_vs_mid": float(np.mean(gaps[c])) if gaps[c] else None,
            }
            for c, v in sums.items()
        },
        "why_no_dk_main": dict(why_no_main),
        "detail": detail,
        "note": "label-free: result values are never read; settled = result IS NOT NULL",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--nba-db", type=Path, default=root / "nba.duckdb")
    ap.add_argument("--kalshi-db", type=Path, default=root / "data/kalshi/kalshi.duckdb")
    ap.add_argument("--market-db", type=Path, default=root / "data/markets/market_asof.duckdb")
    ap.add_argument(
        "--arrays", type=Path, default=root / "reports/alt_rung_cal/arrays_draftkings.npz"
    )
    ap.add_argument("--since", type=date.fromisoformat, default=date(2026, 10, 20))
    ap.add_argument("--until", type=date.fromisoformat, default=date(2026, 10, 23))
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = run(a.nba_db, a.kalshi_db, a.market_db, a.arrays, a.since, a.until)
    text = json.dumps(res, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
