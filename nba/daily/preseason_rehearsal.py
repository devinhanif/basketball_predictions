"""PRESEASON REHEARSAL: not scored, rotations unreliable (Devin, 2026-10-15).

Display-only dress rehearsal of the pretip chain on preseason games, against a COPY of
``nba.duckdb``. Nothing here touches the live database, a scheduler or ``ops/``; the production
path (``nba.daily.pipeline.run_daily``) is called unchanged.

Why game ids are remapped: the production path drops preseason (``001``) ids on purpose
(``schedule.is_scorable_game_id``, ``export.guard.SKIP_PREFIXES``). The rehearsal keeps those walls
intact and instead rewrites ``001YYnnnnn`` -> ``002YY9nnnn`` (a sequence range the regular season
never reaches) so the unchanged chain accepts the games. The map is written to
``<rehearsal dir>/id_map.json`` and every artifact carries the PRESEASON REHEARSAL label. The copy
must never be copied back or scored.

    python -m nba.daily.preseason_rehearsal prepare --schedule SCHEDULE.parquet --copy COPY.duckdb
    python -m nba.daily.preseason_rehearsal run --date 2026-10-16 --copy COPY.duckdb
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from nba.daily.pipeline import run_daily
from nba.daily.schedule import ET, ScheduledGame
from nba.db.connect import DEFAULT_DB_PATH, connect_with_retry
from nba.ingest.cache import RateLimiter

LABEL = "PRESEASON REHEARSAL: not scored, rotations unreliable"
REHEARSAL_DIR = Path("data/rehearsal")
PRESEASON_PREFIX = "001"
REMAP_PREFIX = "002"
REMAP_SEQ_LEAD = "9"


def remap_game_id(game_id: str) -> str:
    """``0012600007`` -> ``0022690007`` (``001YYnnnnn`` -> ``002YY9nnnn``)."""
    if len(game_id) != 10 or not game_id.startswith(PRESEASON_PREFIX) or not game_id.isdigit():
        raise ValueError(f"not a preseason game id: {game_id!r}")
    seq = int(game_id[5:])
    if seq >= 10_000:
        raise ValueError(f"preseason sequence out of range: {game_id!r}")
    return f"{REMAP_PREFIX}{game_id[3:5]}{REMAP_SEQ_LEAD}{seq:04d}"


def load_preseason_schedule(path: Path, *, only_unplayed: bool = True) -> list[ScheduledGame]:
    """Preseason games from a saved ScheduleLeagueV2 frame (``data/raw/preseason_<date>/``),
    ids remapped. ``only_unplayed`` keeps gameStatus == 1 (scheduled)."""
    raw = pl.read_parquet(path)
    out: list[ScheduledGame] = []
    for r in raw.iter_rows(named=True):
        gid = str(r["gameId"])
        if not gid.startswith(PRESEASON_PREFIX):
            continue
        if only_unplayed and int(r["gameStatus"]) != 1:
            continue
        ts = datetime.fromisoformat(str(r["gameDateTimeUTC"]).replace("Z", "+00:00"))
        out.append(
            ScheduledGame(
                remap_game_id(gid),
                ts.astimezone(UTC).replace(tzinfo=None),
                int(r["homeTeam_teamId"]),
                int(r["awayTeam_teamId"]),
            )
        )
    return sorted(out, key=lambda g: (g.tipoff, g.game_id))


def prepare(copy: Path, source: Path = DEFAULT_DB_PATH) -> None:
    """Copy the live database file (read-only use of the source) to the rehearsal copy."""
    if copy.resolve() == source.resolve():
        raise SystemExit("refusing: the copy path is the live database")
    copy.parent.mkdir(parents=True, exist_ok=True)
    if copy.exists():
        raise SystemExit(f"refusing to overwrite {copy}; delete it by hand to start over")
    shutil.copyfile(source, copy)


def run(
    copy: Path, schedule: Path, run_date: date, *, rate_limit_s: float = 2.5
) -> dict[str, object]:
    """One pretip pass for ``run_date`` on the copy; flags mirror ops/nba_daily.sh pretip."""
    from nba.registry import get_registry

    games = load_preseason_schedule(schedule)
    if copy.resolve() == DEFAULT_DB_PATH.resolve():
        raise SystemExit("refusing: the rehearsal never opens the live database")
    rdir = REHEARSAL_DIR
    con = connect_with_retry(copy)
    try:
        t0 = time.monotonic()
        s = run_daily(
            con,
            run_date,
            schedule_fn=lambda _season: games,
            registry=get_registry(con),
            skip_ingest=True,  # offseason: nothing to ingest; preseason box scores are not kept
            roster_source="official",
            roster_dir=rdir / "rosters",
            model_cache=rdir / "models" / "context_residual",
            log_int_variant=True,
            log_lower_tail_variant=True,
            with_pplay_shadow=True,
            with_full_shadow=True,
            with_avg_shadow=True,
            with_combo_shadow=True,
            avg_heartbeat_dir=rdir / "heartbeat",
            combo_heartbeat_dir=rdir / "heartbeat",
            rate_limiter=RateLimiter(rate_limit_s),
            tips_dir=None,  # never persist preseason tips next to the real ones
            schedule_cache_dir=None,
        )
        wall = time.monotonic() - t0
        n = con.execute(
            "SELECT target, count(*) FROM forward_predictions "
            "WHERE run_id = ? GROUP BY 1 ORDER BY 1",
            [s.run_id],
        ).fetchall()
    finally:
        con.close()
    info: dict[str, object] = {
        "label": LABEL,
        "run_id": s.run_id,
        "run_date": s.run_date.isoformat(),
        "slate": s.n_slate,
        "predicted_games": s.n_predicted_games,
        "refused_after_tipoff": s.n_refused_after_tipoff,
        "rows_written": s.n_rows_written,
        "rows_by_target": {str(k): int(v) for k, v in n},
        "injury_status": s.injury_status,
        "injury_report_et": s.injury_report_et,
        "model_status": s.model_status,
        "errors": s.errors,
        "shadow_errors": s.shadow_errors,
        "stamp_status": s.stamp_status,
        "wall_seconds": round(wall, 1),
        "slate_games_et": [
            {
                "game_id": g.game_id,
                "tip_et": g.tipoff.replace(tzinfo=UTC).astimezone(ET).isoformat(),
                "home": g.home_team,
                "away": g.away_team,
            }
            for g in games
            if g.game_date_et == run_date
        ],
    }
    return info


def seed_display(
    copy: Path, schedule: Path, roster_dir: Path = REHEARSAL_DIR / "rosters"
) -> dict[str, int]:
    """Make the exported rows displayable, on the COPY, after every ``run``.

    ``nba.export`` places a player on a team only through a 2026 box-score row or an earlier 2026
    game (the 2025 holdout is never read), and preseason has neither. This adds, for the remapped
    games only, a ``games`` row (season 2026, no score) and a team-only ``player_game_stats`` row
    (every stat NULL) per forecast player, team taken from the official roster files. Idempotent
    (deletes its own rows first). Run it last: those rows would otherwise look like played games to
    a later ``run``."""
    games = load_preseason_schedule(schedule)
    ids = [g.game_id for g in games]
    latest = sorted(p for p in roster_dir.iterdir() if p.is_dir())[-1]
    team_of = {
        int(r["player_id"]): int(r["team_id"])
        for f in latest.glob("*.parquet")
        for r in pl.read_parquet(f).iter_rows(named=True)
    }
    con = connect_with_retry(copy)
    try:
        marks = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM player_game_stats WHERE game_id IN ({marks})", ids)
        con.execute(f"DELETE FROM games WHERE game_id IN ({marks})", ids)
        for g in games:
            con.execute(
                "INSERT INTO games (game_id, game_date, season, home_team, away_team) "
                "VALUES (?, ?, 2026, ?, ?)",
                [g.game_id, g.game_date_et, g.home_team, g.away_team],
            )
        pairs = con.execute(
            f"SELECT DISTINCT game_id, player_id FROM forward_predictions "
            f"WHERE game_id IN ({marks}) AND player_id IS NOT NULL",
            ids,
        ).fetchall()
        n = 0
        for gid, pid in pairs:
            tid = team_of.get(int(pid))
            if tid is None:
                continue
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id) VALUES (?, ?, ?)",
                [gid, int(pid), tid],
            )
            n += 1
    finally:
        con.close()
    return {"games": len(games), "pairs": len(pairs), "seeded": n}


def _id_map(schedule: Path) -> dict[str, str]:
    raw = pl.read_parquet(schedule)
    return {str(g): remap_game_id(str(g)) for g in raw["gameId"].to_list()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.daily.preseason_rehearsal", description=LABEL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="copy nba.duckdb to the rehearsal copy")
    p.add_argument("--copy", type=Path, required=True)
    p.add_argument("--schedule", type=Path, required=True)
    r = sub.add_parser("run", help="one pretip pass for a date on the copy")
    r.add_argument("--copy", type=Path, required=True)
    r.add_argument("--schedule", type=Path, required=True)
    r.add_argument("--date", type=date.fromisoformat, required=True)
    r.add_argument("--rate-limit-s", type=float, default=2.5)
    r.add_argument("--out", type=Path, default=None, help="write the run summary json here")
    d = sub.add_parser(
        "seed-display", help="team-only rows on the copy so the export can place players"
    )
    d.add_argument("--copy", type=Path, required=True)
    d.add_argument("--schedule", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "seed-display":
        print(LABEL, seed_display(a.copy, a.schedule))
        return 0
    if a.cmd == "prepare":
        prepare(a.copy)
        mp = a.copy.parent / "id_map.json"
        mp.write_text(json.dumps({"label": LABEL, "map": _id_map(a.schedule)}, indent=1))
        print(f"{LABEL}\ncopy: {a.copy}\nid map: {mp}")
        return 0
    try:
        info = run(a.copy, a.schedule, a.date, rate_limit_s=a.rate_limit_s)
    except duckdb.IOException as exc:
        print(f"copy is locked: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(info, indent=1, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
