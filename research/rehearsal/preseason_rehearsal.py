"""Preseason rehearsal (2026-10-14): live schedule fetch + injury PDF + pretip on a DB COPY.

Every write is redirected to ``--work`` (the copy and a scratch data dir). Never touches
nba.duckdb, data/ops, ALERTS.md, data/schedule, data/schedule_cache or data/rosters.
Run: uv run python research/rehearsal/preseason_rehearsal.py --date 2026-10-14 --work data/rehearsal
"""

from __future__ import annotations

import argparse
import shutil
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from nba.daily.injury import pull_latest_report
from nba.daily.pipeline import run_daily
from nba.daily.schedule import (
    ScheduledGame,
    fetch_schedule_nba_api,
    is_scorable_game_id,
    parse_schedule_frame,
)
from nba.ingest.cache import RateLimiter


def raw_schedule(season: str) -> list[ScheduledGame]:
    """Unfiltered nba_api schedule (preseason 001 included) to exercise the slate filter."""
    from nba_api.stats.endpoints import scheduleleaguev2

    from nba.daily import schedule as sch

    frame = pl.from_pandas(scheduleleaguev2.ScheduleLeagueV2(season=season).get_data_frames()[0])
    keep = sch.KEPT_GAME_TYPE_PREFIXES
    try:
        sch.KEPT_GAME_TYPE_PREFIXES = keep | {"001"}  # type: ignore[misc]
        return parse_schedule_frame(frame)
    finally:
        sch.KEPT_GAME_TYPE_PREFIXES = keep  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=date.fromisoformat, required=True)
    ap.add_argument("--work", type=Path, required=True)
    a = ap.parse_args()
    work: Path = a.work
    copy = work / f"preseason_{a.date.isoformat()}.duckdb"
    scratch = work / f"preseason_{a.date.isoformat()}_files"
    t0 = time.time()
    if not copy.exists():
        shutil.copyfile("nba.duckdb", copy)
    scratch.mkdir(parents=True, exist_ok=True)
    print(f"copy {copy} ({time.time() - t0:.1f}s)")

    t = time.time()
    prod = fetch_schedule_nba_api("2026-27")
    print(
        f"[A] production schedule fetch: {len(prod)} games, "
        f"prefixes {dict(Counter(g.game_id[:3] for g in prod))}"
        f" ({time.time() - t:.1f}s)"
    )
    t = time.time()
    raw = raw_schedule("2026-27")
    pre = sorted((g for g in raw if not is_scorable_game_id(g.game_id)), key=lambda g: g.tipoff)
    slate = [g for g in pre if g.game_date_et == a.date]
    print(f"[A2] raw schedule incl. preseason: {len(pre)} preseason games ({time.time() - t:.1f}s)")
    for g in slate:
        print("   ", g.game_id, g.tipoff.isoformat(), g.home_team, g.away_team)

    con = duckdb.connect(str(copy))
    before = con.execute("SELECT count(*) FROM forward_predictions").fetchone()[0]  # type: ignore[index]
    now = datetime.now(UTC).replace(tzinfo=None)

    t = time.time()
    first_tip = min((g.tipoff for g in slate), default=None)
    if first_tip is not None:
        not_after = min(now, first_tip)
        n0 = con.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
        try:
            slot = pull_latest_report(
                con,
                not_after,
                data_dir=scratch,
                rate_limiter=RateLimiter(2.5),
                extra_games={(g.game_date_et, g.home_team, g.away_team): g.game_id for g in slate},
            )
            print(f"[B] injury report slot (ET): {slot} ({time.time() - t:.1f}s)")
            if slot:
                rows = con.execute(
                    "SELECT status, count(*), count(player_id), count(game_id) "
                    "FROM player_availability WHERE source='nba_official_report' AND as_of=? "
                    "GROUP BY 1 ORDER BY 2 DESC",
                    [slot],
                ).fetchall()
                print("    status, rows, with_player_id, with_game_id:", rows)
            n1 = con.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
            print(f"    player_availability rows {n0} -> {n1}")
        except Exception as exc:
            print(f"[B] injury FAILED: {type(exc).__name__}: {exc}")
    else:
        print(f"[B] no preseason games on {a.date}; injury fetch skipped")

    kw = dict(
        data_dir=scratch,
        tips_dir=scratch / "schedule",
        schedule_cache_dir=scratch / "schedule_cache",
        roster_dir=scratch / "rosters",
        roster_source="official",
        log_int_variant=True,
        log_lower_tail_variant=True,
        with_pplay_shadow=True,
        with_full_shadow=True,
        rate_limiter=RateLimiter(2.5),
    )
    t = time.time()
    s = run_daily(con, a.date, schedule_fn=fetch_schedule_nba_api, **kw)  # type: ignore[arg-type]
    print(f"[C] run_daily (production schedule fn) ({time.time() - t:.1f}s): {s}")
    t = time.time()
    s2 = run_daily(con, a.date, schedule_fn=lambda _s: raw, **kw)  # type: ignore[arg-type]
    print(f"[D] run_daily (schedule incl. 001 injected) ({time.time() - t:.1f}s): {s2}")
    after = con.execute("SELECT count(*) FROM forward_predictions").fetchone()[0]  # type: ignore[index]
    print(f"forward_predictions on copy: {before} -> {after}")
    con.close()
    print(f"total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
