# ruff: noqa: E501
"""WINNER_RECAL minimum-data gate (rule: docs/prereg/WINNER_RECAL.md; DRAFT).

    .venv/bin/python -W ignore -m research.eval.winner_recal_gate \
        --data-root /Users/devin/Downloads/nba-prediction

Counts and y-free sizing only (non-negotiable 8). No outcome is read: ``home_win`` / ``y`` are never
selected (asserted), no calibrator is fitted, no score is computed. Season 2025 is never loaded:
parquet files are the 2023 and 2024 season files; DuckDB reads carry ``season <= 2024`` (odds) or
``close_time < 2025-08-01`` (Kalshi historical copy) in SQL.

Sections:
1. Price audit: which seasons have game-winner prices, from which source, and are they known-at
   (``snapshot_ts`` before the real tip; model ``made_with_data_through`` before the price).
2. Pair counts: production P(home) x Pinnacle / consensus de-vigged moneyline, per season.
3. Kalshi: historical hourly candles for season 2024 playoffs; live 15-minute snapshots so far.
4. Schedule projection: regular-season games per 7-day block and at the look dates.
5. Y-free sizing. Under "the market probability is the truth" the per-game variance of a log-loss
   difference between two probabilities is ``q(1-q) * (logit p_b - logit p_a)^2`` plus the variance
   of the per-game expected difference; this sizes the paired standard error of
   (a) a recalibration against raw production (a small logit shift) and
   (b) raw production against the market (the gap the recalibration must narrow).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

MAX_SEASON = 2024
SEASONS = (2023, 2024)
LOOK_DATES = (dt.date(2027, 2, 15), dt.date(2027, 4, 15))
N_MIN_GAMES = 600  # games with a pre-tip two-sided Kalshi quote and a stored forecast
PRICE_COVERAGE = 0.85  # assumption chosen before scoring: replay 2025-26 matched 1312/1316 (0.997); live preseason 50/64 (0.78)
FLOOR = -0.002  # draft floor on log loss (recal minus raw)
MIN_PASS_POWER = (
    0.50  # draft convention; ROAD_STARS and RETURNING_TEAMMATE failed their gates at 0.13-0.19
)
SETTLE_LAG_DAYS = 1
SEASON_OPEN = dt.date(2026, 10, 20)
SCHEDULE_FILE = "data/schedule/live_tips_2026-27.parquet"
ODDS_DB = "data/odds/odds_history.duckdb"
KALSHI_HIST_DB = "data/rehearsal/kalshi_2025.duckdb"
KALSHI_LIVE_DB = "data/kalshi/kalshi.duckdb"
INPUT_FILES = (
    "reports/odds_history/game_rows_2023.parquet",
    "reports/odds_history/game_rows_2024.parquet",
    "reports/odds_history/game_market_2023.parquet",
    "reports/odds_history/game_market_2024.parquet",
    SCHEDULE_FILE,
)
KALSHI_HIST_CUTOFF = (
    "2025-08-01"  # season 2024 (2024-25) ended 2025-06-22; 2025-26 starts October 2025
)
Z_ONE_SIDED_95 = 1.6449
Z_POWER_80 = 0.8416
# assumed recalibration maps for sizing only (logit' = a + b*logit); NOT fitted
SIZING_MAPS = {
    "slope_0.90": (0.0, 0.90),
    "slope_1.10": (0.0, 1.10),
    "intercept_+0.10": (0.10, 1.0),
    "intercept_-0.10": (-0.10, 1.0),
}
GAP_CITED = {
    "2023": 0.0212,
    "2024": 0.0107,
}  # T235 / T236, log loss model minus Pinnacle (cited, not recomputed here)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.asarray(np.log(p / (1 - p)))


def inv_logit(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-x)))


def load_rows(data_root: Path, season: int) -> pl.DataFrame:
    assert season <= MAX_SEASON
    cols = ["game_id", "game_date", "season", "p", "made_with_data_through", "tipoff_utc"]
    assert "home_win" not in cols
    return pl.read_parquet(
        data_root / f"reports/odds_history/game_rows_{season}.parquet", columns=cols
    )


def load_market(data_root: Path, season: int) -> pl.DataFrame:
    assert season <= MAX_SEASON
    return pl.read_parquet(data_root / f"reports/odds_history/game_market_{season}.parquet")


def pair_table(rows: pl.DataFrame, mkt: pl.DataFrame, kind: str, source: str) -> pl.DataFrame:
    m = mkt.filter((pl.col("snapshot_kind") == kind) & (pl.col("source") == source)).select(
        "game_id", "snapshot_ts", pl.col("p_home_mult").alias("q")
    )
    return rows.join(m, on="game_id", how="inner").with_columns(
        ((pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_minutes()).alias("lead_min"),
        ((pl.col("snapshot_ts") - pl.col("made_with_data_through")).dt.total_minutes()).alias(
            "price_after_model_min"
        ),
    )


def price_audit(data_root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in SEASONS:
        rows = load_rows(data_root, s)
        mkt = load_market(data_root, s)
        per: dict[str, Any] = {
            "model_games": rows.height,
            "model_made_before_tip_min": float(
                ((rows["tipoff_utc"] - rows["made_with_data_through"]).dt.total_minutes()).median()  # type: ignore[arg-type]
            ),
        }
        for kind in ("t60", "t5"):
            for src in ("pinnacle", "consensus"):
                t = pair_table(rows, mkt, kind, src)
                per[f"{src}_{kind}"] = {
                    "pairs": t.height,
                    "snapshot_before_tip": int((t["lead_min"] > 0).sum()),
                    "lead_min_median": float(t["lead_min"].median()),  # type: ignore[arg-type]
                    "lead_min_min": int(t["lead_min"].min()),  # type: ignore[arg-type]
                    "price_not_before_model": int((t["price_after_model_min"] < 0).sum()),
                }
        # Pinnacle with consensus fallback, as T235/T236 did
        pin = pair_table(rows, mkt, "t60", "pinnacle")
        con = pair_table(rows, mkt, "t60", "consensus").filter(
            ~pl.col("game_id").is_in(pin["game_id"])
        )
        per["pinnacle_or_consensus_t60_pairs"] = pin.height + con.height
        per["consensus_fallback_games"] = con.height
        out[str(s)] = per
    return out


def odds_store_counts(data_root: Path) -> dict[str, Any]:
    con = duckdb.connect(str(data_root / ODDS_DB), read_only=True)
    r = con.execute(
        "select season, snapshot_kind, count(distinct game_id), count(distinct book) from odds_history where market='h2h' and season <= 2024 group by 1,2 order by 1,2"
    ).fetchall()
    return {f"{a}_{b}": {"games": c, "books": d} for a, b, c, d in r}


def kalshi_hist(data_root: Path) -> dict[str, Any]:
    con = duckdb.connect(str(data_root / KALSHI_HIST_DB), read_only=True)
    cut = KALSHI_HIST_CUTOFF
    m = con.execute(
        "select count(*), count(distinct event_ticker), min(close_time), max(close_time) from kalshi_markets where series_ticker='KXNBAGAME' and close_time < ?",
        [cut],
    ).fetchone()
    c = con.execute(
        "select count(*), count(distinct p.ticker), min(p.ts), max(p.ts) from kalshi_prices p join kalshi_markets k using (ticker) where k.series_ticker='KXNBAGAME' and k.close_time < ?",
        [cut],
    ).fetchone()
    assert m is not None and c is not None
    return {
        "scope": "season 2024 (2024-25) playoffs and play-in only; close_time < 2025-08-01 enforced in SQL; hourly candles, ts is the END of the hour",
        "markets": m[0],
        "events_games": m[1],
        "first_close": str(m[2]),
        "last_close": str(m[3]),
        "candle_rows": c[0],
        "markets_with_candles": c[1],
        "candle_first_ts": str(c[2]),
        "candle_last_ts": str(c[3]),
        "regular_season_2024_25_prices": "none: the backfill starts 2025-04 (playoffs); 2024-25 regular season has no Kalshi game-winner price",
    }


def kalshi_live(data_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        src = data_root / KALSHI_LIVE_DB
        dst = Path(td) / src.name
        shutil.copy2(src, dst)
        wal = src.with_name(src.name + ".wal")
        if wal.exists():
            shutil.copy2(wal, Path(td) / wal.name)
        con = duckdb.connect(str(dst), read_only=True)
        ev = con.execute(
            "select event_ticker, max(close_time) from kalshi_markets where series_ticker='KXNBAGAME' group by 1"
        ).pl()
        two = con.execute(
            "select k.event_ticker, count(*) n_quotes, min(p.ts) first_ts from kalshi_prices p join kalshi_markets k using (ticker) "
            "where k.series_ticker='KXNBAGAME' and p.yes_bid is not null and p.yes_ask is not null and p.ts <= k.close_time group by 1"
        ).pl()
        pre = ev.filter(
            pl.col("event_ticker").str.contains("-26OCT")
            & (pl.col("event_ticker").str.slice(-10, 2) != "")
        ).with_columns(pl.col("event_ticker").str.split("-").list.get(1).alias("code"))
        pre = pre.filter(pl.col("code") < "26OCT20")
        priced = pre.join(two, on="event_ticker", how="inner")
        last_ts = con.execute("select max(ts), min(ts) from kalshi_prices").fetchone()
        assert last_ts is not None
        return {
            "game_events_total": ev.height,
            "preseason_events": pre.height,
            "preseason_events_with_two_sided_quote_before_close": priced.height,
            "snapshot_first_ts": str(last_ts[1]),
            "snapshot_last_ts": str(last_ts[0]),
            "cadence": "launchd every 15 min; Mac sleep loses slots (readiness note: 88 of 90 runs ok in the first day)",
        }


def schedule(data_root: Path) -> dict[str, Any]:
    s = pl.read_parquet(data_root / SCHEDULE_FILE).with_columns(
        pl.col("gameDateTimeUTC").str.to_datetime("%Y-%m-%dT%H:%M:%SZ").alias("tip")
    )
    tips = [t.date() for t in s["tip"].to_list()]
    blocks: dict[int, int] = {}
    for t in tips:
        b = (t - SEASON_OPEN).days // 7
        blocks[b] = blocks.get(b, 0) + 1
    at_look = {
        d.isoformat(): sum(1 for t in tips if (d - t).days >= SETTLE_LAG_DAYS) for d in LOOK_DATES
    }
    return {
        "games": len(tips),
        "first_tip": str(s["tip"].min()),
        "last_tip": str(s["tip"].max()),
        "regular_season_only": "the file is the regular season; playoffs are not in it",
        "games_per_7day_block": [
            {"start": (SEASON_OPEN + dt.timedelta(days=7 * b)).isoformat(), "games": n}
            for b, n in sorted(blocks.items())
        ],
        "games_settled_by_look_date": at_look,
    }


def _first_date(data_root: Path) -> str | None:
    s = (
        pl.read_parquet(data_root / SCHEDULE_FILE)
        .with_columns(pl.col("gameDateTimeUTC").str.to_datetime("%Y-%m-%dT%H:%M:%SZ").alias("tip"))
        .sort("tip")
    )
    for i, t in enumerate(s["tip"].to_list()):
        if (i + 1) * PRICE_COVERAGE >= N_MIN_GAMES:
            return str((t.date() + dt.timedelta(days=SETTLE_LAG_DAYS)).isoformat())
    return None


def sizing(rows: pl.DataFrame, mkt: pl.DataFrame, n_final: int) -> dict[str, Any]:
    """Y-free standard errors of paired log-loss differences, market probability taken as the truth."""
    pin = pair_table(rows, mkt, "t60", "pinnacle")
    p = pin["p"].to_numpy()
    q = pin["q"].to_numpy()
    lp, lq = logit(p), logit(q)
    n = len(p)

    def se_of_diff(la: np.ndarray, lb: np.ndarray, n_games: int) -> dict[str, float]:
        # difference LL(b) - LL(a) per game: expectation under y~Bern(q) and variance
        pa, pb = inv_logit(la), inv_logit(lb)
        a_ = np.log(pb / pa)
        b_ = np.log((1 - pb) / (1 - pa))
        e = -(q * a_ + (1 - q) * b_)
        v = q * (1 - q) * (lb - la) ** 2
        per_game_var = float(v.mean() + e.var())
        se = float(np.sqrt(per_game_var / n_games))
        return {
            "expected_diff_under_market_truth": float(e.mean()),
            "per_game_sd": float(np.sqrt(per_game_var)),
            "se_at_n": se,
            "mde_80pct_one_sided_95": (Z_ONE_SIDED_95 + Z_POWER_80) * se,
        }

    res: dict[str, Any] = {
        "pairs_used": n,
        "market": "pinnacle t60 multiplicative de-vig (consensus-fallback games excluded here)",
        "disagreement_logit_sd": float((lp - lq).std()),
    }
    for n_games in (N_MIN_GAMES, 1000, n_final):
        block: dict[str, Any] = {}
        for name, (a, b) in SIZING_MAPS.items():
            block[f"recal_{name}_minus_raw"] = se_of_diff(lp, a + b * lp, n_games)
        # best monotone two-parameter map if the market probability were the truth (upper bound on any
        # intercept-slope recalibration of p; uses the market, so it is a sizing ceiling, never a candidate)
        slope, icpt = np.polyfit(lp, lq, 1)
        block["recal_ceiling_toward_market_minus_raw"] = {
            **se_of_diff(lp, icpt + slope * lp, n_games),
            "intercept": float(icpt),
            "slope": float(slope),
        }
        block["raw_minus_market_gap"] = se_of_diff(lq, lp, n_games)
        ce = block["recal_ceiling_toward_market_minus_raw"]
        thr = min(FLOOR, -Z_ONE_SIDED_95 * ce["se_at_n"])
        ce["pass_line"] = thr
        ce["pass_power_at_ceiling"] = 0.5 * (
            1.0
            + math.erf(
                ((thr - ce["expected_diff_under_market_truth"]) / ce["se_at_n"]) / math.sqrt(2.0)
            )
        )
        res[f"n_{n_games}"] = block
    # pass power at the ceiling effect, at the projected final N (set by the caller via n_final)
    res["note"] = (
        "the 'expected_diff' under market truth is a sizing device, not a result; no outcome was read"
    )
    return res


def run_gate(data_root: Path, hash_inputs: bool = True) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON
    res: dict[str, Any] = {
        "rule": "docs/prereg/WINNER_RECAL.md (DRAFT)",
        "run_at_local": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seasons_read": list(SEASONS),
        "season_2025": "never loaded; the 0.0064 Brier gap (REPLAY_2025_26) came from it and is disclosed as exposure",
        "gap_cited_not_recomputed": GAP_CITED,
    }
    if hash_inputs:
        res["input_sha256"] = {f: sha256_file(data_root / f) for f in INPUT_FILES}
    res["price_audit"] = price_audit(data_root)
    res["odds_store_h2h_counts_le_2024"] = odds_store_counts(data_root)
    res["kalshi_historical"] = kalshi_hist(data_root)
    res["kalshi_live"] = kalshi_live(data_root)
    res["schedule"] = schedule(data_root)
    n_final_sched = res["schedule"]["games_settled_by_look_date"][LOOK_DATES[-1].isoformat()]
    n_final = int(n_final_sched * PRICE_COVERAGE)
    res["projected"] = {
        "price_coverage_assumed": PRICE_COVERAGE,
        "games_with_price_by_look": {
            k: int(v * PRICE_COVERAGE)
            for k, v in res["schedule"]["games_settled_by_look_date"].items()
        },
        "n_min_games": N_MIN_GAMES,
        "first_date_n_min_met": _first_date(data_root),
    }
    sz: dict[str, Any] = {}
    for s in SEASONS:
        sz[str(s)] = sizing(load_rows(data_root, s), load_market(data_root, s), n_final)
    res["sizing_y_free"] = sz
    powers = {
        s: sz[s][f"n_{n_final}"]["recal_ceiling_toward_market_minus_raw"]["pass_power_at_ceiling"]
        for s in sz
    }
    res["pass_power_at_ceiling_by_season"] = powers
    gate_pass = min(powers.values()) >= MIN_PASS_POWER
    res["gate"] = {
        "status": "PASS" if gate_pass else "FAIL_POWER",
        "criterion": f"pass power >= {MIN_PASS_POWER} at the ceiling effect (best monotone intercept-slope map if the market were truth), floor {FLOOR}, projected final N {n_final}",
        "historical_venue": "none unexposed: 2023 and 2024 Pinnacle t60 pairs exist with known-at times, but the model-vs-market gap (T235/T236) and the calibrators on production OOF (T178) were already read on them",
        "confirmatory_venue": f"2026-27 live log; priced regular-season games projected at the final look: {n_final} of {res['schedule']['games']}",
        "first_look_with_enough_n": "interim 2027-02-15 (kill-only), binding 2027-04-15",
    }
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_winner_recal/gate.json"))
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, hash_inputs=not args.no_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
