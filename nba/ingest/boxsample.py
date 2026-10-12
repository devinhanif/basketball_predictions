"""Fetch the stats.nba.com box scores that validate the play-by-play-derived box (2015-2019).

The ids come from ``data/history/boxsample/boxsample_game_ids.txt`` (one per line, picked by
``python -m nba.parse.history_reconcile pick``). Fetches go to their own cache and DuckDB
(``data/history/boxsample/``), never into ``nba_history.duckdb``, so the sample cannot be
mistaken for loaded data. Same puller as every other history fetch: BoxScoreTraditionalV3,
rate-limited, resumable (cached ids are not refetched), empty responses are failures, circuit
breaker on streaks, and it yields to the daily jobs. Run by the ingest queue (step
``hist:boxsample``); safe to run by hand when the queue is idle:

    uv run python -m nba.ingest.boxsample --rate-limit-s 6.0
"""

from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path

from nba.ingest.boxscores import pull_game_boxscore
from nba.ingest.cache import CircuitBreaker, RateLimiter, open_db, pull_ids_with_breaker

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "history" / "boxsample"
IDS_FILE = SAMPLE_DIR / "boxsample_game_ids.txt"
DB_PATH = SAMPLE_DIR / "boxsample.duckdb"


def read_ids(path: Path = IDS_FILE) -> list[str]:
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.boxsample")
    ap.add_argument("--rate-limit-s", type=float, default=6.0)
    ap.add_argument("--ids-file", type=Path, default=IDS_FILE)
    ap.add_argument("--breaker-cooldown-s", type=float, default=600.0)
    ap.add_argument("--breaker-max-trips", type=int, default=6)
    args = ap.parse_args(argv)
    if args.rate_limit_s < 2.5:
        ap.error("--rate-limit-s must be >= 2.5 (stats.nba.com quota)")
    ids = read_ids(args.ids_file)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    con = open_db(DB_PATH)
    try:
        limiter = RateLimiter(min_interval_s=args.rate_limit_s)
        loop = pull_ids_with_breaker(
            con,
            "boxscore",
            ids,
            partial(pull_game_boxscore, con, data_dir=SAMPLE_DIR, rate_limiter=limiter),
            breaker=CircuitBreaker(
                cooldown_s=args.breaker_cooldown_s, max_trips=args.breaker_max_trips
            ),
            label="boxsample",
            log_every=25,
        )
    finally:
        con.close()
    print(
        f"boxsample: {loop.total} games, {loop.cached} cached, {loop.fetched} fetched, "
        f"{len(loop.failed)} failed{' (stopped early: breaker)' if loop.stopped_early else ''}"
    )
    return 1 if loop.failed or loop.stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
