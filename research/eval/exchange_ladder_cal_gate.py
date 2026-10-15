# ruff: noqa: E501
"""EXCHANGE_LADDER_CAL minimum-data gate (rule: docs/prereg/EXCHANGE_LADDER_CAL.md; DRAFT).

    .venv/bin/python -W ignore -m research.eval.exchange_ladder_cal_gate \
        --data-root /Users/devin/Downloads/nba-prediction

Counts only (non-negotiable 8). No outcome is read: ``kalshi_markets.result`` is only tested for
NULL (settled or not), never compared to ``yes``/``no``; no forecast is scored; no model is fit.
Read-only everywhere: ``data/kalshi/kalshi.duckdb`` belongs to the launchd writer, so it is COPIED
(with its WAL) to a temporary directory and the copy is opened read-only. ``nba.duckdb`` is opened
read-only; only ``forward_predictions`` rows of preseason game ids (``0012...``) are touched.

What it measures:
1. Kalshi player-prop ladders captured so far (preseason + the 2026 Finals), listing rate per game,
   pricing (two-sided quote before the market closed) and the quoted spread.
2. How many of those ladders have a stored model forecast for the same game (paired legs).
3. Whether any Kalshi/Polymarket player-prop rows from the ``us_ex`` capture exist yet.
4. The 2026-27 schedule: games per 7-day block and cumulative counts at the pre-declared look dates.
5. Three listing scenarios, each projected to the number of games with a usable settled leg at each
   look date, and the first date each scenario reaches the minimum.
6. The fee object (configs/parlay.yaml) as a verified-or-not flag; whether a Polymarket entry exists.
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
import polars as pl
import yaml

MAX_SEASON = 2024  # asserted before any read; this gate touches no historical season at all
SCHEDULE_FILE = "data/schedule/live_tips_2026-27.parquet"
KALSHI_DB = "data/kalshi/kalshi.duckdb"
NBA_DB = "nba.duckdb"
PARLAY_CFG = "configs/parlay.yaml"
INPUT_FILES = (SCHEDULE_FILE, KALSHI_DB, PARLAY_CFG, "configs/odds_live.yaml")

# Frozen-in-draft design constants (docs/prereg/EXCHANGE_LADDER_CAL.md "Rule").
N_MIN_GAMES = 400  # games with >= 1 usable settled priced leg, pooled venues
N_MIN_LEGS = 1500  # usable settled priced legs, pooled venues
USABLE_FRACTION = 0.80  # assumption, chosen before any scoring (alias matched x forecast present x priced two-sided x in z-support)
LOOK_DATES = (dt.date(2026, 12, 15), dt.date(2027, 2, 15), dt.date(2027, 4, 15))
SETTLE_LAG_DAYS = 1  # a game counts at a look date if it tipped at least this many days before
PROP_SERIES = ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT")
# preseason ends on the eve of the regular season; the regular season opens 2026-10-20
SEASON_OPEN = dt.date(2026, 10, 20)
FINALS_PREFIX = "26JUN"
FLOOR = -0.005  # draft floor on per-leg log loss (CAL minus raw)
Z_ONE_SIDED_95 = 1.6449
# Per-game SD of the CAL-minus-A0 per-leg log-loss contrast, back-derived from the ledger's T493 CI (cited, not recomputed):
# half-width 0.0022 at 1,260 games => se 0.00112 => sd = se * sqrt(1260). Pinnacle main lines, about 30 legs per game.
T493_SD_PER_GAME = 0.0011225 * math.sqrt(1260)
SD_INFLATION = (1.0, 1.5)  # ladders have fewer, nested legs per game; sensitivity only
EFFECTS = {"T493_size": -0.0117, "half_of_T493": -0.006, "CAL_minus_C0_size": -0.0037}
MIN_PASS_POWER = 0.50  # draft convention (see WINNER_RECAL gate)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_live_db(src: Path, tmp: Path) -> Path:
    """Copy a writer-owned DuckDB file (and its WAL) so the copy can be opened read-only."""
    dst = tmp / src.name
    shutil.copy2(src, dst)
    wal = src.with_name(src.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, tmp / wal.name)
    return dst


def event_game_code(event_ticker: str) -> str:
    """'KXNBAPTS-26OCT05NYKPHI' -> '26OCT05NYKPHI'."""
    return event_ticker.split("-", 1)[1]


def kalshi_listing(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    m = con.execute(
        "select ticker, series_ticker, event_ticker, player_id, stat, threshold, open_time, close_time, (result is not null) as settled "
        "from kalshi_markets"
    ).pl()
    game = m.filter(pl.col("series_ticker") == "KXNBAGAME")
    props = m.filter(pl.col("series_ticker").is_in(PROP_SERIES))
    game_codes = {event_game_code(e) for e in game["event_ticker"].unique().to_list()}
    pre_game_codes = {c for c in game_codes if c.startswith("26OCT") and c < "26OCT20"}
    props = props.with_columns(
        pl.col("event_ticker").map_elements(event_game_code, return_dtype=pl.Utf8).alias("code")
    )
    pre_props = props.filter(pl.col("code").is_in(sorted(pre_game_codes)))
    finals_props = props.filter(pl.col("code").str.starts_with(FINALS_PREFIX))
    out: dict[str, Any] = {
        "preseason_game_events": len(pre_game_codes),
        "preseason": _ladder_stats(pre_props),
        "finals_2026": _ladder_stats(finals_props),
        "preseason_listing_rate_games": (pre_props["code"].n_unique() / len(pre_game_codes))
        if len(pre_game_codes)
        else None,
    }
    # pricing and spread (no result column used beyond NULL test above)
    tick = pre_props["ticker"].to_list()
    if tick:
        q = con.execute(
            "select p.ticker, p.ts, p.yes_bid, p.yes_ask from kalshi_prices p join kalshi_markets k using (ticker) "
            "where p.ticker in (select unnest(?)) and p.ts <= k.close_time",
            [tick],
        ).pl()
        two = q.filter(
            pl.col("yes_bid").is_not_null()
            & pl.col("yes_ask").is_not_null()
            & (pl.col("yes_ask") > 0)
        )
        last = (
            two.sort("ts")
            .group_by("ticker")
            .agg(
                pl.col("yes_bid").last(),
                pl.col("yes_ask").last(),
                pl.col("ts").last().alias("last_ts"),
            )
        )
        out["preseason_priced_two_sided_markets"] = last.height
        out["preseason_markets"] = len(tick)
        if last.height:
            sp = (last["yes_ask"] - last["yes_bid"]).to_list()
            out["preseason_last_quote_spread_mean"] = float(sum(sp) / len(sp))
            out["preseason_last_quote_spread_max"] = float(max(sp))
    return out


def _ladder_stats(p: pl.DataFrame) -> dict[str, Any]:
    if p.height == 0:
        return {"markets": 0, "events": 0, "ladders": 0}
    lad = p.group_by("event_ticker", "player_id", "stat").agg(
        pl.len().alias("rungs"),
        pl.col("threshold").min().alias("lo"),
        pl.col("threshold").max().alias("hi"),
    )
    return {
        "markets": p.height,
        "events": p["event_ticker"].n_unique(),
        "settled_markets": int(p["settled"].sum()),
        "series_counts": {
            r["series_ticker"]: r["len"] for r in p.group_by("series_ticker").len().to_dicts()
        },
        "ladders_with_player_id": int(lad.filter(pl.col("player_id").is_not_null()).height),
        "ladders_without_player_id": int(lad.filter(pl.col("player_id").is_null()).height),
        "rungs_per_ladder_min": int(lad["rungs"].min()),  # type: ignore[arg-type]
        "rungs_per_ladder_max": int(lad["rungs"].max()),  # type: ignore[arg-type]
        "threshold_range": [float(p["threshold"].min()), float(p["threshold"].max())],  # type: ignore[arg-type]
        "markets_per_event_median": float(p.group_by("event_ticker").len()["len"].median()),  # type: ignore[arg-type]
    }


def paired_forecasts(nba_db: Path, kalshi_events: list[str]) -> dict[str, Any]:
    """Preseason forecast rows (game id prefix 0012...) and whether any shares a date with a ladder event."""
    con = duckdb.connect(str(nba_db), read_only=True)
    rows = con.execute(
        "select game_id, count(*), count(distinct player_id), min(made_at), min(tipoff) from forward_predictions where game_id like '001%' group by 1 order by 1"
    ).fetchall()
    dates = {r[0]: r[4].date().isoformat() for r in rows}
    ladder_dates = sorted({_code_date(c) for c in kalshi_events})
    overlap = [g for g, d in dates.items() if d in ladder_dates]
    return {
        "forecast_games": [
            {
                "game_id": r[0],
                "rows": r[1],
                "players": r[2],
                "made_at": str(r[3]),
                "tipoff_date": dates[r[0]],
            }
            for r in rows
        ],
        "ladder_event_dates": ladder_dates,
        "games_with_both_forecast_and_ladder": len(overlap),
        "paired_settled_legs": 0 if not overlap else None,
    }


def _code_date(code: str) -> str:
    return dt.datetime.strptime(code[:7], "%y%b%d").date().isoformat()


def us_ex_capture(data_root: Path) -> dict[str, Any]:
    """Live the-odds-api.com raw files that carry us_ex (none before opening night)."""
    base = data_root / "data/odds/raw/the_odds_api"
    live = (
        [p.name for p in base.iterdir() if p.is_dir() and p.name.isdigit() and int(p.name) >= 2026]
        if base.exists()
        else []
    )
    live_dirs = (
        sorted(p.name for p in (data_root / "data/odds/raw").iterdir())
        if (data_root / "data/odds/raw").exists()
        else []
    )
    return {
        "season_2026_raw_dirs": live,
        "raw_root_children": live_dirs,
        "kalshi_polymarket_player_prop_rows_via_us_ex": 0 if not live else None,
        "note": "market_asof.duckdb odds_snapshots holds 2026-10-10 h2h rows for kalshi/polymarket from the OLD vendor theoddsapi.com (different vendor); no player-prop rows.",
        "capture_start": "2026-10-20 (live per-game capture; NBA_CLOSE_CAPTURE=1 required)",
    }


def schedule_blocks(data_root: Path) -> pl.DataFrame:
    s = pl.read_parquet(data_root / SCHEDULE_FILE).with_columns(
        pl.col("gameDateTimeUTC").str.to_datetime("%Y-%m-%dT%H:%M:%SZ").alias("tip")
    )
    return s.with_columns(
        ((pl.col("tip").dt.date() - pl.lit(SEASON_OPEN)).dt.total_days() // 7).alias("block")
    ).sort("tip")


def scenarios(
    pre_rate: float, pre_markets_per_listed_game: float, finals_markets_per_game: float
) -> dict[str, dict[str, float]]:
    """Listing scenarios. ``listed_rate`` = share of games with any ladder; ``markets`` = rung markets per listed game."""
    return {
        "L_preseason_rate": {
            "listed_rate": pre_rate,
            "markets_per_listed_game": pre_markets_per_listed_game,
        },
        "M_every_game_preseason_depth": {
            "listed_rate": 1.0,
            "markets_per_listed_game": pre_markets_per_listed_game,
        },
        "H_every_game_finals_depth": {
            "listed_rate": 1.0,
            "markets_per_listed_game": finals_markets_per_game,
        },
    }


def project(sched: pl.DataFrame, scen: dict[str, dict[str, float]]) -> dict[str, Any]:
    tips = sched["tip"].dt.date().to_list()
    out: dict[str, Any] = {}
    for name, sc in scen.items():
        legs_per_game = sc["listed_rate"] * sc["markets_per_listed_game"] * USABLE_FRACTION
        games_per_game = sc["listed_rate"] * USABLE_FRACTION
        at_look = {}
        for d in LOOK_DATES:
            n_games = sum(1 for t in tips if (d - t).days >= SETTLE_LAG_DAYS)
            at_look[d.isoformat()] = {
                "schedule_games_settled": n_games,
                "usable_games": round(n_games * games_per_game, 1),
                "usable_legs": round(n_games * legs_per_game, 1),
                "meets_games_min": n_games * games_per_game >= N_MIN_GAMES,
                "meets_legs_min": n_games * legs_per_game >= N_MIN_LEGS,
            }
        first = None
        for i, t in enumerate(tips):
            g = (i + 1) * games_per_game
            legs = (i + 1) * legs_per_game
            if g >= N_MIN_GAMES and legs >= N_MIN_LEGS:
                first = (t + dt.timedelta(days=SETTLE_LAG_DAYS)).isoformat()
                break
        out[name] = {
            **sc,
            "usable_legs_per_schedule_game": round(legs_per_game, 3),
            "usable_games_per_schedule_game": round(games_per_game, 3),
            "look_dates": at_look,
            "first_date_both_minima_met": first,
        }
    return out


def power_table() -> dict[str, Any]:
    out: dict[str, Any] = {"sd_per_game_assumed_from_T493": T493_SD_PER_GAME, "floor": FLOOR}
    for infl in SD_INFLATION:
        for n in (N_MIN_GAMES, 600, 960):
            se = T493_SD_PER_GAME * infl / math.sqrt(n)
            thr = min(FLOOR, -Z_ONE_SIDED_95 * se)
            row = {"se": se, "pass_line": thr}
            for name, e in EFFECTS.items():
                row[f"power_{name}"] = 0.5 * (1.0 + math.erf(((thr - e) / se) / math.sqrt(2.0)))
            out[f"sd_x{infl}_games_{n}"] = row
    return out


def fee_object(data_root: Path) -> dict[str, Any]:
    cfg = yaml.safe_load((data_root / PARLAY_CFG).read_text())["fee_model"]
    return {
        "kalshi": {
            k: cfg.get(k)
            for k in (
                "name",
                "formula",
                "coefficient",
                "maker_coefficient",
                "verified",
                "source",
                "default_multiplier",
            )
        },
        "polymarket_entry_in_config": "polymarket" in json.dumps(cfg).lower(),
        "polymarket_docs_note": "docs.polymarket.us/fees.md fetched 2026-10-15: taker Fee = theta*C*p*(1-p), theta 0.0695, banker's rounding to $0.01, effective 2026-10-07; maker rebate tiers change 2026-10-19. NOT in configs; EV on Polymarket stays uncomputed until Devin adds a verified entry.",
    }


def run_gate(data_root: Path, hash_inputs: bool = True) -> dict[str, Any]:
    assert MAX_SEASON <= 2024  # no historical season is read at all
    res: dict[str, Any] = {
        "rule": "docs/prereg/EXCHANGE_LADDER_CAL.md (DRAFT)",
        "run_at_local": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "constants": {
            "N_MIN_GAMES": N_MIN_GAMES,
            "N_MIN_LEGS": N_MIN_LEGS,
            "USABLE_FRACTION": USABLE_FRACTION,
            "LOOK_DATES": [d.isoformat() for d in LOOK_DATES],
            "SETTLE_LAG_DAYS": SETTLE_LAG_DAYS,
        },
    }
    if hash_inputs:
        res["input_sha256"] = {f: sha256_file(data_root / f) for f in INPUT_FILES}
    with tempfile.TemporaryDirectory() as td:
        kdb = copy_live_db(data_root / KALSHI_DB, Path(td))
        con = duckdb.connect(str(kdb), read_only=True)
        listing = kalshi_listing(con)
        last_snap = con.execute("select max(ts) from kalshi_prices").fetchone()
        events = [
            r[0]
            for r in con.execute(
                "select distinct event_ticker from kalshi_markets where series_ticker in ('KXNBAPTS','KXNBAREB','KXNBAAST','KXNBA3PT') and event_ticker like '%-26OCT%'"
            ).fetchall()
        ]
    res["kalshi_snapshot_db_last_ts"] = str(last_snap[0]) if last_snap else None
    res["kalshi_listing"] = listing
    res["paired_forecasts"] = paired_forecasts(
        data_root / NBA_DB, [event_game_code(e) for e in events]
    )
    res["us_ex_capture"] = us_ex_capture(data_root)
    sched = schedule_blocks(data_root)
    per_block = (
        sched.group_by("block")
        .agg(pl.len().alias("games"), pl.col("tip").min().dt.date().alias("start"))
        .sort("block")
    )
    res["schedule"] = {
        "games": sched.height,
        "first_tip": str(sched["tip"].min()),
        "last_tip": str(sched["tip"].max()),
        "note": "the schedule file lists 1,200 games, not 1,230; reported as found",
        "games_per_7day_block_min": int(per_block["games"].min()),  # type: ignore[arg-type]
        "games_per_7day_block_median": float(per_block["games"].median()),  # type: ignore[arg-type]
        "games_per_7day_block_max": int(per_block["games"].max()),  # type: ignore[arg-type]
        "blocks": [
            {"block": r["block"], "start": r["start"].isoformat(), "games": r["games"]}
            for r in per_block.to_dicts()
        ],
    }
    pre = listing["preseason"]
    fin = listing["finals_2026"]
    pre_mpg = pre["markets"] / pre["events"] if pre.get("events") else 8.0
    fin_mpg = fin["markets_per_event_median"] if fin.get("events") else 50.0
    scen = scenarios(
        float(listing["preseason_listing_rate_games"] or 0.0), float(pre_mpg), float(fin_mpg)
    )
    res["scenarios"] = project(sched, scen)
    res["fee_object"] = fee_object(data_root)
    res["power_table"] = power_table()
    meets = {k: v["first_date_both_minima_met"] for k, v in res["scenarios"].items()}
    res["gate"] = {
        "status": "NOT_MEASURABLE_YET",
        "reason": "regular-season listing rate on Kalshi and Polymarket is unknown; preseason (3 of ~50 games, stars only) is not representative; no us_ex row exists before 2026-10-20",
        "first_measurable_date": "2026-10-21 (first night's us_ex capture: listing rate, depth, Polymarket structure)",
        "confirmed_measurable_date": "2026-10-27 (first full 7-day block: per-week rate; decision whether the minima can be met by 2027-04-15)",
        "first_date_both_minima_met_by_scenario": meets,
        "passes_if": "listing rate over 2026-10-20..2026-10-26 projects >= N_MIN_GAMES usable games by 2027-04-15 (scenario M or H); otherwise closed as DATA after the non-negotiable-8 audit",
    }
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument(
        "--out", type=Path, default=Path("reports/prereg_exchange_ladder_cal/gate.json")
    )
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, hash_inputs=not args.no_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
