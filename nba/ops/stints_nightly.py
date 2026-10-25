"""Nightly stints + possession lineups for last night's final games (tracker v2, ADR 0002).

The tracker already exists (``nba.parse.lineups`` / ``nba.parse.rebuild_lineups``); this module only
schedules it for the live season. ``stints`` had 0 rows for ``season=2026`` because nothing fetched
play-by-play or ran the tracker after the 2019-2025 bulk rebuild. One run:

  1. picks the **scored, final** games: ``season = 2026``, both scores set, ``game_date`` before
     today (ET: a game still in progress carries a live score, so the date bound is what makes it
     final), a box score stored, inside the lookback window (a missed night heals on the next one);
  2. skips games that already have ``stints`` rows (**idempotent**: a second run writes nothing);
  3. fetches the missing play-by-play (``nba.ingest.pbp.pull_game_pbp``), parses and stores the
     possessions (``parse_possessions`` + ``load_possessions``) for games that have none;
  4. computes stints and ``possessions.off_players/def_players`` with the bulk tracker
     (``rebuild_lineups.compute_all``, the same name and alias books as the 5,269-game rebuild) and
     writes them with ``write_chunk`` in one transaction;
  5. reconciles: per game date, the share of player-games (box minutes > 0) whose stint minutes are
     within 1.0 minute of box minutes, over every eligible game that has stints. Logged to
     ``data/ops/stints_reconciliation.jsonl`` (one row per night, rewritten in place) and printed.

Exit codes (ops/nba_daily.sh runs this as an *advisory* step: any non-zero code alerts, none blocks
another morning step, the heartbeat rc or app-export):

  0   done (or nothing left to build) and every checked night is at or above the share target
  4   no eligible game in the window (off day): informational
  5   some game could not be built (no PBP, no starters, parse error); the rest were written
  6   a night that was built in this run reconciles below ``ALERT_SHARE`` (95%)
  75  nba.duckdb stayed write-locked

Backfill 2026-10-20 through yesterday, once (the maintainer runs it; same code as the nightly)::

    uv run python -m nba.ops.stints_nightly --since 2026-10-20 --until <YYYY-MM-DD>

``--dry-run`` lists what would be built and writes nothing. No model, no 2025 read:
``season = 2026`` is hard-coded in the eligibility query.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.db.connect import connect, connect_with_retry, is_lock_error
from nba.ingest.cache import RateLimiter, cache_path_for
from nba.ingest.pbp import pull_game_pbp
from nba.ops.exitcodes import RC_DB_BUSY, RC_DEGRADED, RC_INFORMATIONAL
from nba.parse.history import _starters
from nba.parse.lineups import (
    MINUTES_SHARE_TARGET,
    MINUTES_WITHIN_TOL,
    minutes_within_share,
    track_lineups_with_report,
)
from nba.parse.loader import load_possessions, load_stints
from nba.parse.possessions import parse_possessions
from nba.parse.rebuild_lineups import (
    build_name_map,
    build_sub_alias_map,
    compute_all,
    write_chunk,
)

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
SEASON = 2026
SEASON_START = date(2026, 10, 20)  # opening night; nothing earlier is ever eligible
LOOKBACK_DAYS = 7
ALERT_SHARE = MINUTES_SHARE_TARGET  # 0.95: Devin 2026-10-24, alert when a night is under it
RC_RECONCILE_LOW = 6
#: a game is final only when each team's box minutes reach this (240 regulation; the half-time box
#: a pretip run once stored sums to about half)
MIN_TEAM_MINUTES = 239.5
#: a night with fewer player-games than this is judged in the pooled window, not alone
MIN_NIGHT_PLAYER_GAMES = 150
#: env switch (option (b) of the review): 1 holds 2026 possessions writes; stints are still built,
#: possessions and their lineup columns are left untouched. Default (unset/0) writes them.
HOLD_POSSESSIONS_ENV = "NBA_STINTS_HOLD_POSSESSIONS"
DEFAULT_BUDGET_S = 1500.0  # fetch phase wall clock; the shell alarm (1800 s) is the backstop

Fetch = Callable[..., Any]


@dataclass
class Night:
    game_date: str
    games: int
    player_games: int
    within: int
    share: float
    built_now: int  # games of this night written by this run

    def as_row(self) -> dict[str, Any]:
        return {
            "game_date": self.game_date,
            "games": self.games,
            "player_games": self.player_games,
            "within_1min": self.within,
            "share": round(self.share, 4),
            "tolerance_min": MINUTES_WITHIN_TOL,
            "alert_below": ALERT_SHARE,
            "built_now": self.built_now,
        }


@dataclass
class Result:
    rc: int = 0
    eligible: int = 0
    already: int = 0
    built: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    nights: list[Night] = field(default_factory=list)
    low_nights: list[str] = field(default_factory=list)
    unresolved_subs: int = 0
    desync_subs: int = 0
    timings_s: dict[str, float] = field(default_factory=dict)
    pooled_low: bool = False
    held_possessions: bool = False


def today_et() -> date:
    return datetime.now(ET).date()


def eligible_games(
    con: duckdb.DuckDBPyConnection, today: date, since: date, until: date | None = None
) -> list[tuple[str, date]]:
    """Scored, final 2026 games: ``game_date`` in [since, today), each team's box >= 239.5 min."""
    last = today - timedelta(days=1)
    if until is not None:
        last = min(last, until)
    rows = con.execute(
        "SELECT g.game_id, g.game_date FROM games g "
        "WHERE g.season = ? AND g.home_pts IS NOT NULL AND g.away_pts IS NOT NULL "
        "AND g.game_date >= ? AND g.game_date <= ? "
        "AND (SELECT count(*) FROM (SELECT b.team_id FROM player_game_stats b "
        "WHERE b.game_id = g.game_id GROUP BY b.team_id HAVING sum(b.minutes) >= ?)) = 2 "
        "ORDER BY g.game_date, g.game_id",
        [SEASON, since, last, MIN_TEAM_MINUTES],
    ).fetchall()
    return [(str(g), d) for g, d in rows]


def _games_in(con: duckdb.DuckDBPyConnection, table: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    marks = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT DISTINCT game_id FROM {table} WHERE game_id IN ({marks})", ids
    ).fetchall()
    return {str(r[0]) for r in rows}


def reconcile(
    con: duckdb.DuckDBPyConnection, games: list[tuple[str, date]], built_now: set[str]
) -> list[Night]:
    """Per game date: share of player-games within 1.0 min of box, over games that have stints."""
    ids = [g for g, _ in games]
    have = sorted(_games_in(con, "stints", ids))
    if not have:
        return []
    marks = ", ".join("?" for _ in have)
    stints = con.execute(f"SELECT * FROM stints WHERE game_id IN ({marks})", have).pl()
    stints = stints.with_columns(
        pl.col("players").cast(pl.List(pl.Int64)), pl.col("team_id").cast(pl.Int64)
    )
    box = con.execute(
        f"SELECT game_id, player_id, minutes FROM player_game_stats WHERE game_id IN ({marks})",
        have,
    ).pl()
    box = box.with_columns(pl.col("player_id").cast(pl.Int64))
    date_of = dict(games)
    by_date: dict[date, list[str]] = {}
    for g in have:
        by_date.setdefault(date_of[g], []).append(g)
    nights: list[Night] = []
    for d in sorted(by_date):
        gids = by_date[d]
        share, n = minutes_within_share(
            stints.filter(pl.col("game_id").is_in(gids)),
            box.filter(pl.col("game_id").is_in(gids)),
        )
        if n == 0:
            continue
        nights.append(
            Night(
                game_date=d.isoformat(),
                games=len(gids),
                player_games=n,
                within=round(share * n),
                share=share,
                built_now=sum(1 for g in gids if g in built_now),
            )
        )
    return nights


def write_reconciliation_log(path: Path, nights: list[Night], run_at: str) -> None:
    """One JSON row per night, keyed by ``game_date`` (a re-run rewrites its row, no twin)."""
    rows: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[str(r["game_date"])] = r
    for n in nights:
        rows[n.game_date] = {**n.as_row(), "run_at": run_at}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(rows[k]) + "\n" for k in sorted(rows)))
    os.replace(tmp, path)


def low_nights(nights: list[Night]) -> tuple[list[str], bool]:
    """Nights to alert on. A night with >= 150 player-games is judged alone; the smaller nights are
    judged as one pool (the reconciled window is at most the 7-day lookback). Only nights built in
    this run can alert."""
    out = [
        n.game_date
        for n in nights
        if n.player_games >= MIN_NIGHT_PLAYER_GAMES and n.built_now and n.share < ALERT_SHARE
    ]
    small = [n for n in nights if n.player_games < MIN_NIGHT_PLAYER_GAMES]
    pooled = False
    if any(n.built_now for n in small):
        total = sum(n.player_games for n in small)
        within = sum(n.within for n in small)
        if total and within / total < ALERT_SHARE:
            pooled = True
            out.extend(n.game_date for n in small if n.built_now)
    return sorted(set(out)), pooled


def _stints_and_lineups(
    con: duckdb.DuckDBPyConnection, data_dir: Path, ready: list[str], res: Result
) -> None:
    try:
        rebuilt = compute_all(con, data_dir, only=set(ready))
    except ValueError as exc:
        if "empty" not in str(exc):  # polars' concat([]): every ready game was skipped
            raise
        res.failed.update({g: "tracker skipped" for g in ready})
        return
    for reason, gids in rebuilt.skipped.items():
        for g in gids:
            if g in ready:
                res.failed[g] = reason
    res.unresolved_subs = sum(len(r.unresolved) for r in rebuilt.reports)
    res.desync_subs = sum(len(r.desync) for r in rebuilt.reports)
    write_chunk(con, rebuilt.stints, rebuilt.lineups)
    con.execute("CHECKPOINT")
    res.built = sorted(rebuilt.stints["game_id"].unique().to_list())


def _stints_only(
    con: duckdb.DuckDBPyConnection, data_dir: Path, ready: list[str], res: Result
) -> None:
    """Hold switch on: the same tracker call per game, stints written, possessions untouched."""
    all_ids = [str(r[0]) for r in con.execute("SELECT game_id FROM games").fetchall()]
    names = build_name_map(data_dir, all_ids)
    aliases = build_sub_alias_map(data_dir, all_ids)
    frames: list[pl.DataFrame] = []
    for gid in ready:
        box = con.execute(
            "SELECT player_id, team_id, starter, minutes FROM player_game_stats WHERE game_id = ?",
            [gid],
        ).pl()
        starters = _starters(box)
        if starters is None:
            res.failed[gid] = "no_starters"
            continue
        roster = {
            int(t[0]): g["player_id"].to_list()
            for t, g in box.filter(pl.col("minutes") > 0).group_by("team_id")
        }
        stints, rep = track_lineups_with_report(
            pl.read_parquet(cache_path_for("pbp", gid, data_dir)), starters, roster, names, aliases
        )
        res.unresolved_subs += len(rep.unresolved)
        res.desync_subs += len(rep.desync)
        frames.append(stints)
    if not frames:
        return
    con.execute("BEGIN")
    try:
        load_stints(con, pl.concat(frames))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    con.execute("CHECKPOINT")
    res.built = sorted({g for f in frames for g in f["game_id"].unique().to_list()})


def run_nightly(
    db_path: Path,
    data_dir: Path,
    *,
    today: date,
    since: date,
    until: date | None = None,
    rate_limit_s: float = 1.0,
    budget_s: float = DEFAULT_BUDGET_S,
    recon_log: Path | None = None,
    dry_run: bool = False,
    hold_possessions: bool = False,
    fetch: Fetch = pull_game_pbp,
) -> Result:
    res = Result()
    res.held_possessions = hold_possessions
    con = (
        connect(db_path, read_only=True)
        if dry_run
        else connect_with_retry(db_path)  # waits (NBA_DB_LOCK_WAIT_S) for another writer
    )
    try:
        games = eligible_games(con, today, since, until)
        res.eligible = len(games)
        if not games:
            res.rc = RC_INFORMATIONAL
            return res
        ids = [g for g, _ in games]
        have = _games_in(con, "stints", ids)
        res.already = len(have)
        todo = [g for g in ids if g not in have]
        if dry_run:
            print(f"dry-run: {len(todo)} to build of {len(ids)} eligible: {todo}", flush=True)
            res.nights = reconcile(con, games, set())
            return res

        t0 = time.monotonic()
        t_fetch0 = t0
        limiter = RateLimiter(min_interval_s=rate_limit_s)
        have_poss = _games_in(con, "possessions", todo)
        ready: list[str] = []
        for gid in todo:
            path = cache_path_for("pbp", gid, data_dir)
            try:
                if not path.exists():
                    if time.monotonic() - t0 > budget_s:
                        res.failed[gid] = "fetch budget exhausted"
                        continue
                    fetch(con, gid, data_dir=data_dir, rate_limiter=limiter)
                if gid not in have_poss and not hold_possessions:
                    poss = parse_possessions(pl.read_parquet(path))
                    if poss.is_empty():
                        res.failed[gid] = "no possessions parsed"
                        continue
                    con.execute("BEGIN")
                    try:
                        load_possessions(con, poss)
                        con.execute("COMMIT")
                    except Exception:
                        con.execute("ROLLBACK")
                        raise
                ready.append(gid)
            except Exception as exc:  # one bad game must not stop the night
                res.failed[gid] = f"{type(exc).__name__}: {str(exc)[:120]}"

        res.timings_s["fetch_parse"] = round(time.monotonic() - t_fetch0, 1)
        if ready:
            t1 = time.monotonic()
            if hold_possessions:
                _stints_only(con, data_dir, ready, res)
            else:
                _stints_and_lineups(con, data_dir, ready, res)
            res.timings_s["compute_write"] = round(time.monotonic() - t1, 1)

        t2 = time.monotonic()
        res.nights = reconcile(con, games, set(res.built))
        res.timings_s["reconcile"] = round(time.monotonic() - t2, 1)
    finally:
        con.close()

    if recon_log is not None and res.nights:
        write_reconciliation_log(
            recon_log, res.nights, datetime.now(ET).isoformat(timespec="seconds")
        )
    res.low_nights, res.pooled_low = low_nights(res.nights)
    if res.low_nights:
        res.rc = RC_RECONCILE_LOW
    elif res.failed:
        res.rc = RC_DEGRADED
    return res


def summary(res: Result) -> dict[str, Any]:
    return {
        "eligible": res.eligible,
        "already_built": res.already,
        "built_now": res.built,
        "failed": res.failed,
        "unresolved_subs": res.unresolved_subs,
        "desync_subs": res.desync_subs,
        "nights": [n.as_row() for n in res.nights],
        "low_nights": res.low_nights,
        "low_nights_pooled": res.pooled_low,
        "possessions_held": res.held_possessions,
        "timings_s": res.timings_s,
        "rc": res.rc,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ops.stints_nightly")
    ap.add_argument("--db", type=Path, default=ROOT / "nba.duckdb")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help="ET date (default now)")
    ap.add_argument("--since", type=date.fromisoformat, default=None)
    ap.add_argument("--until", type=date.fromisoformat, default=None)
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--rate-limit-s", type=float, default=1.0)
    ap.add_argument("--budget-s", type=float, default=DEFAULT_BUDGET_S)
    ap.add_argument(
        "--recon-log", type=Path, default=ROOT / "data" / "ops" / "stints_reconciliation.jsonl"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--hold-possessions",
        action="store_true",
        help=f"do not write 2026 possessions (also {HOLD_POSSESSIONS_ENV}=1); stints only",
    )
    args = ap.parse_args(argv)
    today = args.today or today_et()
    since = args.since or max(SEASON_START, today - timedelta(days=args.lookback_days))
    try:
        res = run_nightly(
            args.db,
            args.data_dir,
            today=today,
            since=since,
            until=args.until,
            rate_limit_s=args.rate_limit_s,
            budget_s=args.budget_s,
            recon_log=None if args.dry_run else args.recon_log,
            dry_run=args.dry_run,
            hold_possessions=args.hold_possessions
            or os.environ.get(HOLD_POSSESSIONS_ENV, "") == "1",
        )
    except duckdb.IOException as exc:
        if not is_lock_error(exc):
            raise
        print(f"stints_nightly: nba.duckdb locked: {exc}", flush=True)
        return RC_DB_BUSY
    print(json.dumps(summary(res), indent=2), flush=True)
    for n in res.nights:
        print(
            f"RECON {n.game_date} games={n.games} player_games={n.player_games} "
            f"within_1min={n.within} share={n.share:.4f}"
            f"{'  BELOW ' + format(ALERT_SHARE, '.2f') if n.share < ALERT_SHARE else ''}",
            flush=True,
        )
    if res.rc == RC_RECONCILE_LOW:
        where = "pooled small nights" if res.pooled_low else "nights"
        print(
            f"ALERT reconciliation below {ALERT_SHARE:.0%} ({where}): {res.low_nights}; "
            f"failed games: {sorted(res.failed) or 'none'}",
            flush=True,
        )
    return res.rc


if __name__ == "__main__":
    raise SystemExit(main())
