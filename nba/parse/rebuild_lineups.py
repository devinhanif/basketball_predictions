"""Bulk rebuild of ``stints`` and ``possessions.off_players/def_players`` (tracker v2).

Why a separate path: ``nba.parse.history.parse_games`` re-parses possessions and
writes per game (DELETE + INSERT + UPDATE inside one transaction). Re-running that
for 5k games bloats the WAL and re-derives ``poss_idx``. This module instead

  1. computes every game's new stints and possession lineups **in memory**, read-only
     (``compute_all``; keyed on the *stored* possessions, so ``poss_idx`` is untouched);
  2. snapshots the current lineup columns to parquet (``snapshot_pre_fix``) so the
     change is reversible;
  3. writes in a few set-based chunks (``write_chunk``): one short read-write
     connection per chunk -- stage the chunk's frames, DELETE/INSERT ``stints``,
     one ``UPDATE ... FROM`` on ``possessions`` -- then CHECKPOINT and close, so
     other DuckDB writers (the launchd jobs) can get in between chunks.

CLI (maintainer)::

    uv run python -m nba.parse.rebuild_lineups --db nba.duckdb          # compute + report only
    uv run python -m nba.parse.rebuild_lineups --db nba.duckdb --write  # + snapshot + chunked write

``--write`` waits (polling) until ``lsof`` shows no holder of the DB file before each
chunk. The compute phase holds ``data/ops/heavy.lock`` (mkdir lock, retry every 2 min,
removed in ``finally``/signal trap).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.parse.history import _starters
from nba.parse.lineups import (
    MINUTES_SHARE_FLOOR,
    MINUTES_SHARE_TARGET,
    LineupReport,
    attach_lineups_to_possessions,
    minutes_within_share,
    track_lineups_with_report,
)

ROOT = Path(__file__).resolve().parents[2]
HEAVY_LOCK = ROOT / "data" / "ops" / "heavy.lock"
BACKUP_DIR = ROOT / "data" / "backups"
CHUNK_GAMES = 1000


@dataclass
class Rebuilt:
    stints: pl.DataFrame
    lineups: pl.DataFrame  # game_id, poss_idx, off_players, def_players
    reports: list[LineupReport]
    skipped: dict[str, list[str]] = field(default_factory=dict)  # reason -> game_ids


def compute_all(
    con: duckdb.DuckDBPyConnection, data_dir: Path = ROOT / "data", limit_every: int = 1
) -> Rebuilt:
    """Recompute stints + possession lineups for every game with PBP, starters and possessions."""
    games = con.execute("SELECT game_id FROM games ORDER BY game_id").fetchall()[::limit_every]
    box = con.execute(
        "SELECT game_id, player_id, team_id, starter, minutes FROM player_game_stats"
    ).pl()
    poss = con.execute(
        "SELECT game_id, poss_idx, period, clock_start, off_team, def_team FROM possessions"
    ).pl()
    box_by_game = box.partition_by("game_id", as_dict=True)
    poss_by_game = poss.partition_by("game_id", as_dict=True)
    stints: list[pl.DataFrame] = []
    lineups: list[pl.DataFrame] = []
    reports: list[LineupReport] = []
    skipped: dict[str, list[str]] = {"no_pbp": [], "no_starters": [], "no_possessions": []}
    for (gid,) in games:
        path = data_dir / "pbp" / f"{gid}.parquet"
        b = box_by_game.get((gid,))
        p = poss_by_game.get((gid,))
        if not path.exists():
            skipped["no_pbp"].append(gid)
            continue
        starters = _starters(b) if b is not None else None
        if b is None or starters is None:
            skipped["no_starters"].append(gid)
            continue
        if p is None:
            skipped["no_possessions"].append(gid)
            continue
        roster = {
            int(t[0]): g["player_id"].to_list()
            for t, g in b.filter(pl.col("minutes") > 0).group_by("team_id")
        }
        s, rep = track_lineups_with_report(pl.read_parquet(path), starters, roster)
        att = attach_lineups_to_possessions(p, s)
        stints.append(s)
        lineups.append(att.select("game_id", "poss_idx", "off_players", "def_players"))
        reports.append(rep)
    return Rebuilt(pl.concat(stints), pl.concat(lineups), reports, skipped)


def snapshot_pre_fix(
    con: duckdb.DuckDBPyConnection, backup_dir: Path = BACKUP_DIR, ts: str | None = None
) -> list[Path]:
    """Write the current ``stints`` and possession lineup columns to parquet (reversibility)."""
    ts = ts or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name, sql in (
        ("stints", "SELECT * FROM stints"),
        (
            "possessions",
            "SELECT game_id, poss_idx, off_players, def_players FROM possessions",
        ),
    ):
        path = backup_dir / f"lineups_pre_fix_{ts}_{name}.parquet"
        con.execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")
        out.append(path)
    return out


def write_chunk(
    con: duckdb.DuckDBPyConnection, stints: pl.DataFrame, lineups: pl.DataFrame
) -> None:
    """Set-based replace of one chunk of games on a read-write connection (no per-row SQL)."""
    gids = pl.DataFrame({"game_id": stints["game_id"].unique().to_list()})
    con.register("_chunk_games", gids.to_arrow())
    con.register("_chunk_stints", stints.to_arrow())
    con.register("_chunk_lineups", lineups.to_arrow())
    try:
        con.execute("BEGIN")
        con.execute("DELETE FROM stints WHERE game_id IN (SELECT game_id FROM _chunk_games)")
        con.execute(
            "INSERT INTO stints SELECT game_id, team_id, period, start_clock, end_clock, "
            "players::INTEGER[5] FROM _chunk_stints"
        )
        con.execute(
            "UPDATE possessions SET off_players = l.off_players::INTEGER[5], "
            "def_players = l.def_players::INTEGER[5] FROM _chunk_lineups l "
            "WHERE possessions.game_id = l.game_id AND possessions.poss_idx = l.poss_idx"
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        for v in ("_chunk_games", "_chunk_stints", "_chunk_lineups"):
            con.unregister(v)


def db_holders(db_path: Path) -> list[str]:
    """Lines of ``lsof`` for ``db_path`` (empty list = nobody has it open)."""
    r = subprocess.run(["lsof", str(db_path)], capture_output=True, text=True, check=False)
    return [ln for ln in r.stdout.splitlines()[1:] if ln.strip()]


def write_chunked(
    db_path: Path,
    rebuilt: Rebuilt,
    *,
    chunk_games: int = CHUNK_GAMES,
    poll_s: float = 5.0,
    max_wait_s: float = 3600.0,
    pause_s: float = 5.0,
) -> int:
    """Write ``rebuilt`` in chunks, one short connection per chunk. Returns chunks written."""
    gids = sorted(rebuilt.stints["game_id"].unique().to_list())
    n = 0
    for i in range(0, len(gids), chunk_games):
        chunk = gids[i : i + chunk_games]
        s = rebuilt.stints.filter(pl.col("game_id").is_in(chunk))
        lu = rebuilt.lineups.filter(pl.col("game_id").is_in(chunk))
        waited = 0.0
        while True:
            if not db_holders(db_path):
                try:
                    con = duckdb.connect(str(db_path))
                except duckdb.IOException:
                    con = None  # lost a race for the lock; retry
                if con is not None:
                    try:
                        write_chunk(con, s, lu)
                        con.execute("CHECKPOINT")
                    finally:
                        con.close()
                    break
            time.sleep(poll_s)
            waited += poll_s
            if waited > max_wait_s:
                raise TimeoutError(f"{db_path} stayed locked for {max_wait_s:.0f}s")
        n += 1
        print(f"WROTE chunk {n}: games {i + 1}-{i + len(chunk)} of {len(gids)}", flush=True)
        time.sleep(pause_s)  # let other writers in between chunks
    return n


@contextlib.contextmanager
def heavy_lock(lock: Path = HEAVY_LOCK, retry_s: float = 120.0) -> Iterator[None]:
    """mkdir lock; retry every ``retry_s``; always removed on exit or SIGTERM/SIGINT."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            print(f"heavy.lock held; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)

    def _release(*_: Any) -> None:
        shutil.rmtree(lock, ignore_errors=True)
        raise SystemExit(143)

    old = {s: signal.signal(s, _release) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        shutil.rmtree(lock, ignore_errors=True)


def minutes_by_season(
    con: duckdb.DuckDBPyConnection, stints: pl.DataFrame
) -> dict[int, tuple[float, int]]:
    """Per season: (share of player-games within 1.0 min of box, n player-games)."""
    box = con.execute(
        "SELECT s.game_id, s.player_id, s.minutes, g.season FROM player_game_stats s "
        "JOIN games g USING (game_id)"
    ).pl()
    have = set(stints["game_id"].unique().to_list())
    box = box.filter(pl.col("game_id").is_in(have))
    out: dict[int, tuple[float, int]] = {}
    for (season,), grp in sorted(box.partition_by("season", as_dict=True).items()):
        gs = stints.filter(pl.col("game_id").is_in(grp["game_id"].unique().to_list()))
        out[int(season)] = minutes_within_share(gs, grp.select("game_id", "player_id", "minutes"))
    return out


def _mean(s: pl.Series) -> float:
    v = s.cast(pl.Float64).mean()
    return float(v) if isinstance(v, int | float) else float("nan")


def possession_agreement(
    old: pl.DataFrame, new: pl.DataFrame, period: pl.DataFrame
) -> dict[str, float]:
    """Share of possessions whose old offense/defense five equals the new one (by period group)."""
    j = (
        old.join(new, on=["game_id", "poss_idx"], suffix="_new")
        .join(period, on=["game_id", "poss_idx"])
        .filter(pl.col("off_players").is_not_null() & pl.col("off_players_new").is_not_null())
    )
    same_off = j["off_players"].list.sort() == j["off_players_new"].list.sort()
    same_def = j["def_players"].list.sort() == j["def_players_new"].list.sort()
    out = {
        "all_off": _mean(same_off),
        "all_def": _mean(same_def),
        "n": float(j.height),
    }
    for lab, cond in (("q1", j["period"] == 1), ("q2_4", (j["period"] >= 2) & (j["period"] <= 4))):
        out[f"{lab}_off"] = _mean(same_off.filter(cond))
    return out


def write_unresolved_csv(reports: list[LineupReport], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, attr in (("unresolved_subs.csv", "unresolved"), ("desync_subs.csv", "desync")):
        path = out_dir / name
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["game_id", "team_id", "period", "clock", "in_name", "out_id"])
            for r in reports:
                for u in getattr(r, attr):
                    w.writerow(
                        [
                            r.game_id,
                            u["team_id"],
                            u["period"],
                            u["clock"],
                            u["in_name"],
                            u["out_id"],
                        ]
                    )
        paths.append(path)
    return paths[0], paths[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.rebuild_lineups")
    ap.add_argument("--db", type=Path, default=ROOT / "nba.duckdb")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--write", action="store_true", help="snapshot, then chunked write")
    ap.add_argument("--every", type=int, default=1, help="use every Nth game (compute-only checks)")
    ap.add_argument("--report-dir", type=Path, default=ROOT / "reports" / "lineups_rebuild")
    ap.add_argument("--no-lock", action="store_true")
    args = ap.parse_args(argv)
    if args.write and args.every != 1:
        raise SystemExit("--write requires --every 1")

    lock = contextlib.nullcontext() if args.no_lock else heavy_lock()
    with lock:
        con = duckdb.connect(str(args.db), read_only=True)
        try:
            t0 = time.monotonic()
            rebuilt = compute_all(con, args.data_dir, args.every)
            print(
                f"computed {len(rebuilt.reports)} games in {time.monotonic() - t0:.0f}s", flush=True
            )
            old_stints = con.execute("SELECT * FROM stints").pl()
            old_stints = old_stints.filter(
                pl.col("game_id").is_in(rebuilt.stints["game_id"].unique().to_list())
            )
            before = minutes_by_season(
                con, old_stints.with_columns(pl.col("players").cast(pl.List(pl.Int64)))
            )
            after = minutes_by_season(con, rebuilt.stints)
            old_l = con.execute(
                "SELECT game_id, poss_idx, off_players::BIGINT[] AS off_players, "
                "def_players::BIGINT[] AS def_players FROM possessions"
            ).pl()
            per = con.execute("SELECT game_id, poss_idx, period FROM possessions").pl()
            agree = possession_agreement(old_l, rebuilt.lineups, per)
            u, d = write_unresolved_csv(rebuilt.reports, args.report_dir)
            snap = None
            if args.write:
                snap = snapshot_pre_fix(con)
        finally:
            con.close()

    summary = {
        "games": len(rebuilt.reports),
        "skipped": {k: len(v) for k, v in rebuilt.skipped.items()},
        "before": {s: {"share": v[0], "n": v[1]} for s, v in before.items()},
        "after": {s: {"share": v[0], "n": v[1]} for s, v in after.items()},
        "possession_agreement_old_vs_new": agree,
        "unresolved_subs": sum(len(r.unresolved) for r in rebuilt.reports),
        "desync_subs": sum(len(r.desync) for r in rebuilt.reports),
        "open_filled": sum(len(r.open_filled) for r in rebuilt.reports),
        "q1_mismatch": sum(len(r.q1_mismatch) for r in rebuilt.reports),
        "unresolved_csv": str(u),
        "desync_csv": str(d),
        "snapshot": [str(p) for p in snap] if snap else None,
        "target": MINUTES_SHARE_TARGET,
        "floor": MINUTES_SHARE_FLOOR,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    worst = min(v[0] for v in after.values())
    if worst < MINUTES_SHARE_FLOOR:
        print(f"FAIL: worst-season within-1.0-min share {worst:.3f} < floor {MINUTES_SHARE_FLOOR}")
        return 2
    if args.write:
        write_chunked(args.db, rebuilt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
