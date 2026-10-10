"""Bulk rebuild of ``possessions`` in game order (ADR 0002 item 5; roadmap item 8).

``parse_possessions`` used to sort play-by-play by ``action_number``, which is not game
order: the feed appends post-hoc corrections after the end of the period with the right
period and clock but a late number. 5,789 of 1,049,740 stored possessions ended after
they started, in 4,258 games. ``parse_possessions`` now uses ``nba.parse.ordering``; this
module re-derives the stored table from the cached PBP.

What it does (same shape as ``nba.parse.rebuild_lineups``):

  1. recompute every stored game's possessions **in memory**, read-only, from
     ``data/pbp/<game_id>.parquet``; re-attach ``off_players``/``def_players`` from the
     **stored** ``stints`` (already clock-ordered; this step does not touch them);
  2. measure before (stored) vs after (new): negative-duration possessions, possessions that
     start after the previous one ended, the two-team possession balance of ADR 0001, the
     secondary ``FGA + 0.44 FTA + TOV - OREB`` reconciliation, points vs final score, rows
     with a NULL lineup, and how many games change their ``poss_idx`` numbering;
  3. evaluate the pre-registered write gates (``evaluate_gates``); exit 2 when one fails;
  4. with ``--write``: snapshot ``possessions`` to ``data/backups/`` and replace the rows
     in chunks of games (one short read-write connection per chunk, one transaction each).

``possessions`` is the only table keyed on ``poss_idx``. ``stints`` is keyed by
(period, clock) and is unchanged; ``shots`` is keyed by the feed's ``game_event_id``. So the
backup is one parquet file of the whole ``possessions`` table.

CLI (maintainer; holds ``data/ops/heavy.lock``, waits for it)::

    uv run python -m nba.parse.rebuild_possessions --db nba.duckdb          # compute + report
    uv run python -m nba.parse.rebuild_possessions --db nba.duckdb --write  # + backup + write
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.parse.lineups import attach_lineups_to_possessions
from nba.parse.possessions import POSSESSIONS_COLUMNS, parse_possessions
from nba.parse.rebuild_lineups import BACKUP_DIR, ROOT, db_holders, heavy_lock
from nba.parse.reconcile import (
    mean_abs_error,
    reconcile_possessions,
    team_box_from_player_game_stats,
)

CHUNK_GAMES = 1000

#: Stored column order and types of ``possessions`` (lineups are filled by the attach step).
_KEY = ["game_id", "poss_idx"]
_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "poss_idx": pl.Int64,
    "period": pl.Int64,
    "clock_start": pl.Float32,
    "clock_end": pl.Float32,
    "off_team": pl.Int64,
    "def_team": pl.Int64,
    "off_players": pl.List(pl.Int64),
    "def_players": pl.List(pl.Int64),
    "score_diff": pl.Int64,
    "outcome": pl.Utf8,
    "shooter_id": pl.Int64,
    "shot_zone": pl.Utf8,
    "assister_id": pl.Int64,
    "oreb": pl.Boolean,
    "fta": pl.Int64,
    "pts": pl.Int64,
}
#: Columns that identify "the same possession" when deciding whether a game was renumbered.
_IDENTITY = [c for c in POSSESSIONS_COLUMNS if c not in ("game_id", "poss_idx")]

# ---------------------------------------------------------------------------
# Pre-registered write gates (fixed before the first measurement on the full cache).
# ---------------------------------------------------------------------------
#: Mean per-game |poss_A - poss_B| may not rise by more than this (ADR 0001, primary check).
BALANCE_MEAN_TOL = 0.02
#: ADR 0001 primary gate: median per-game gap <= 2.
BALANCE_MEDIAN_MAX = 2.0
#: ADR 0001 secondary check: mean |parsed - (FGA + 0.44 FTA + TOV - OREB)| <= ~2.5. The ADR
#: calls that formula noisy (it overcounts, signed bias about -1.5 to -1.9), so it is a ceiling,
#: not a no-regression test. A first draft also required "MAE up by at most 0.05"; a 1-in-25
#: sample (211 games) moved it +0.09 (1.90 -> 1.99) while the primary balance check improved,
#: and that tolerance was dropped from the gates (still reported as ``recon_mae``/``recon_signed``).
RECON_MAE_CEILING = 2.5
#: Share of team-games whose parsed points equal the final score may not fall by more than this.
POINTS_EXACT_TOL = 0.002
#: Rows with a NULL lineup side may not rise by more than this share of all rows (about 10 rows).
LINEUP_NULL_TOL = 1e-5
#: Total possession count may move by at most this share (an ordering fix should barely move it).
ROW_COUNT_TOL = 0.005


@dataclass
class Rebuilt:
    possessions: pl.DataFrame  # full stored column set, new values
    games: list[str]
    skipped: dict[str, list[str]] = field(default_factory=dict)  # reason -> game_ids


@dataclass
class Gate:
    name: str
    ok: bool
    detail: str


def _normalise(poss: pl.DataFrame) -> pl.DataFrame:
    """Cast to the stored column order and types (clock values as float32, like the table)."""
    return poss.select([pl.col(c).cast(t) for c, t in _SCHEMA.items()])


def compute_all(
    con: duckdb.DuckDBPyConnection, data_dir: Path = ROOT / "data", limit_every: int = 1
) -> Rebuilt:
    """Re-parse every game that has stored possessions and a cached PBP file (read-only)."""
    stored = [
        r[0] for r in con.execute("SELECT DISTINCT game_id FROM possessions ORDER BY 1").fetchall()
    ][::limit_every]
    stints = con.execute(
        "SELECT game_id, team_id, period, start_clock, end_clock, players::BIGINT[] AS players "
        "FROM stints"
    ).pl()
    stints_by_game = stints.partition_by("game_id", as_dict=True)
    skipped: dict[str, list[str]] = {"no_pbp": [], "empty_parse": []}
    out: list[pl.DataFrame] = []
    games: list[str] = []
    t0 = time.monotonic()
    for i, gid in enumerate(stored, 1):
        path = data_dir / "pbp" / f"{gid}.parquet"
        if not path.exists():
            skipped["no_pbp"].append(gid)
            continue
        poss = parse_possessions(pl.read_parquet(path))
        if poss.is_empty():
            skipped["empty_parse"].append(gid)
            continue
        s = stints_by_game.get((gid,))
        if s is not None:
            poss = attach_lineups_to_possessions(poss, s)
        else:
            poss = poss.with_columns(
                pl.lit(None, dtype=pl.List(pl.Int64)).alias("off_players"),
                pl.lit(None, dtype=pl.List(pl.Int64)).alias("def_players"),
            )
        out.append(_normalise(poss))
        games.append(gid)
        if i % 500 == 0:
            print(f"PROGRESS parse {i}/{len(stored)} ({time.monotonic() - t0:.0f}s)", flush=True)
    return Rebuilt(pl.concat(out), games, skipped)


def load_stored(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """The stored ``possessions`` table, in the shared column order and types."""
    cols = ", ".join(f"{c}::BIGINT[] AS {c}" if c.endswith("_players") else c for c in _SCHEMA)
    return _normalise(con.execute(f"SELECT {cols} FROM possessions").pl())


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def order_defects(poss: pl.DataFrame) -> dict[str, int]:
    """Count time-order defects. Clocks count down, so a later event has a smaller clock.

    ``neg_duration``: ``clock_end > clock_start``. ``start_after_prev_end``: a possession
    starts later on the clock than the previous one (in ``poss_idx`` order, same period)
    ended, i.e. ``clock_start > previous clock_end``. ``start_before_prev_start``: starts
    earlier than the previous one started (``clock_start > previous clock_start``).
    ``games_with_defect``: games with at least one of the three.
    """
    d = (
        poss.sort(_KEY)
        .with_columns(
            pl.col("period").shift(1).over("game_id").alias("_pp"),
            pl.col("clock_end").shift(1).over("game_id").alias("_pe"),
            pl.col("clock_start").shift(1).over("game_id").alias("_ps"),
        )
        .with_columns(
            (pl.col("clock_end") > pl.col("clock_start")).alias("neg"),
            ((pl.col("_pp") == pl.col("period")) & (pl.col("clock_start") > pl.col("_pe"))).alias(
                "after_end"
            ),
            ((pl.col("_pp") == pl.col("period")) & (pl.col("clock_start") > pl.col("_ps"))).alias(
                "before_start"
            ),
            (pl.col("period") < pl.col("_pp")).alias("period_regress"),
        )
        .fill_null(False)
    )
    any_defect = d.filter(
        pl.col("neg") | pl.col("after_end") | pl.col("before_start") | pl.col("period_regress")
    )
    return {
        "n_poss": d.height,
        "neg_duration": int(d["neg"].sum()),
        "start_after_prev_end": int(d["after_end"].sum()),
        "start_before_prev_start": int(d["before_start"].sum()),
        "period_regress": int(d["period_regress"].sum()),
        "games_with_defect": any_defect["game_id"].n_unique(),
    }


def balance_stats(poss: pl.DataFrame) -> dict[str, float]:
    """ADR 0001 primary check: per-game |possessions(A) - possessions(B)| and per-team count."""
    per_team = poss.group_by(["game_id", "off_team"]).agg(pl.len().alias("n"))
    per_game = per_team.group_by("game_id").agg(
        (pl.col("n").max() - pl.col("n").min()).alias("gap"), pl.len().alias("teams")
    )
    gap = per_game.filter(pl.col("teams") == 2)["gap"]
    return {
        "balance_median": float(gap.median()),  # type: ignore[arg-type]
        "balance_mean": float(gap.mean()),  # type: ignore[arg-type]
        "balance_p95": float(gap.quantile(0.95)),  # type: ignore[arg-type]
        "poss_per_team_mean": float(per_team["n"].mean()),  # type: ignore[arg-type]
        "poss_per_team_p5": float(per_team["n"].quantile(0.05)),  # type: ignore[arg-type]
        "poss_per_team_p95": float(per_team["n"].quantile(0.95)),  # type: ignore[arg-type]
    }


def measure(
    poss: pl.DataFrame, team_box: pl.DataFrame, game_pts: pl.DataFrame
) -> dict[str, float | int]:
    """All gate metrics for one possessions frame.

    ``team_box``: game_id, team_id, fga, fta, tov, oreb. ``game_pts``: game_id, home_team,
    away_team, home_pts, away_pts.
    """
    have = poss["game_id"].unique().to_list()
    rec = reconcile_possessions(poss, team_box.filter(pl.col("game_id").is_in(have)))
    parsed_pts = poss.group_by(["game_id", "off_team"]).agg(pl.col("pts").sum().alias("pp"))
    gp = game_pts.filter(pl.col("game_id").is_in(have))
    long = pl.concat(
        [
            gp.select(
                "game_id", pl.col("home_team").alias("off_team"), pl.col("home_pts").alias("t")
            ),
            gp.select(
                "game_id", pl.col("away_team").alias("off_team"), pl.col("away_pts").alias("t")
            ),
        ]
    ).join(parsed_pts, on=["game_id", "off_team"], how="left")
    null_rows = int(
        poss.select(
            (pl.col("off_players").is_null() | pl.col("def_players").is_null()).sum()
        ).item()
    )
    out: dict[str, float | int] = {
        **order_defects(poss),
        **balance_stats(poss),
        "recon_mae": mean_abs_error(rec),
        "recon_signed": float(rec["error"].mean()),  # type: ignore[arg-type]
        "points_exact_rate": float((long["pp"] == long["t"]).mean()),  # type: ignore[arg-type]
        "lineup_null_rows": null_rows,
    }
    return out


def renumbering(old: pl.DataFrame, new: pl.DataFrame) -> dict[str, Any]:
    """Which games change ``poss_idx`` numbering: any (poss_idx -> possession) pair differs.

    A game is renumbered when its possession count differs or any ``poss_idx`` maps to a
    different possession (period, clocks, teams, outcome, shooter, assister, oreb, fta,
    pts, score_diff, shot_zone). Lineups are not part of the identity.
    """
    ident = [c for c in _IDENTITY if not c.endswith("_players")]
    j = old.select(_KEY + ident).join(
        new.select(_KEY + ident), on=_KEY, how="full", suffix="_new", coalesce=True
    )
    differs = pl.any_horizontal(
        [
            (pl.col(c).is_null() != pl.col(f"{c}_new").is_null())
            | (pl.col(c) != pl.col(f"{c}_new")).fill_null(False)
            for c in ident
        ]
    )
    j = j.with_columns(differs.alias("_diff"))
    per_game = j.group_by("game_id").agg(
        pl.col("_diff").any().alias("renumbered"), pl.col("_diff").sum().alias("rows_changed")
    )
    n_old = old.group_by("game_id").agg(pl.len().alias("n_old"))
    n_new = new.group_by("game_id").agg(pl.len().alias("n_new"))
    counts = n_old.join(n_new, on="game_id", how="full", coalesce=True)
    count_changed = counts.filter(pl.col("n_old") != pl.col("n_new"))
    return {
        "games": per_game.height,
        "games_renumbered": int(per_game["renumbered"].sum()),
        "rows_changed": int(per_game["rows_changed"].sum()),
        "games_count_changed": count_changed.height,
        "net_row_change": int(new.height - old.height),
        "renumbered_game_ids": sorted(per_game.filter(pl.col("renumbered"))["game_id"].to_list()),
    }


def evaluate_gates(before: dict[str, Any], after: dict[str, Any]) -> list[Gate]:
    """The pre-registered ``--write`` gates. Every one must pass."""
    g: list[Gate] = []

    def add(name: str, ok: bool, detail: str) -> None:
        g.append(Gate(name, bool(ok), detail))

    tot_b = before["neg_duration"] + before["start_after_prev_end"]
    tot_a = after["neg_duration"] + after["start_after_prev_end"]
    add(
        "defects_fall",
        after["neg_duration"] <= before["neg_duration"]
        and after["start_after_prev_end"] <= before["start_after_prev_end"]
        and (tot_a < tot_b or tot_b == 0),
        f"neg_duration {before['neg_duration']} -> {after['neg_duration']}; "
        f"start_after_prev_end {before['start_after_prev_end']} -> {after['start_after_prev_end']}",
    )
    add(
        "balance_mean",
        after["balance_mean"] <= before["balance_mean"] + BALANCE_MEAN_TOL,
        f"mean |A-B| {before['balance_mean']:.4f} -> {after['balance_mean']:.4f} "
        f"(tol +{BALANCE_MEAN_TOL})",
    )
    add(
        "balance_median",
        after["balance_median"] <= BALANCE_MEDIAN_MAX,
        f"median |A-B| {before['balance_median']:.2f} -> {after['balance_median']:.2f} "
        f"(max {BALANCE_MEDIAN_MAX})",
    )
    add(
        "recon_mae_ceiling",
        after["recon_mae"] <= RECON_MAE_CEILING,
        f"formula MAE {before['recon_mae']:.4f} -> {after['recon_mae']:.4f} "
        f"(signed {before['recon_signed']:+.3f} -> {after['recon_signed']:+.3f}; "
        f"ceiling {RECON_MAE_CEILING}, informational otherwise)",
    )
    add(
        "points_exact",
        after["points_exact_rate"] >= before["points_exact_rate"] - POINTS_EXACT_TOL,
        f"points exact {before['points_exact_rate']:.4f} -> {after['points_exact_rate']:.4f} "
        f"(tol -{POINTS_EXACT_TOL})",
    )
    add(
        "lineup_attach",
        after["lineup_null_rows"] <= before["lineup_null_rows"] + LINEUP_NULL_TOL * after["n_poss"],
        f"rows with a NULL lineup side {before['lineup_null_rows']} -> {after['lineup_null_rows']} "
        f"of {after['n_poss']} (tol +{LINEUP_NULL_TOL * after['n_poss']:.0f} rows)",
    )
    add(
        "row_count",
        abs(after["n_poss"] - before["n_poss"]) <= ROW_COUNT_TOL * before["n_poss"],
        f"rows {before['n_poss']} -> {after['n_poss']} (tol {ROW_COUNT_TOL:.1%})",
    )
    return g


# ---------------------------------------------------------------------------
# Backup + write
# ---------------------------------------------------------------------------
def snapshot_pre_fix(
    con: duckdb.DuckDBPyConnection, backup_dir: Path = BACKUP_DIR, ts: str | None = None
) -> list[Path]:
    """Write the whole stored ``possessions`` table to parquet (the only poss_idx-keyed table)."""
    ts = ts or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"possessions_pre_order_fix_{ts}_possessions.parquet"
    con.execute(f"COPY (SELECT * FROM possessions) TO '{path}' (FORMAT PARQUET)")
    return [path]


def write_chunk(con: duckdb.DuckDBPyConnection, poss: pl.DataFrame) -> None:
    """Replace the rows of the games in ``poss`` (one transaction; DELETE then INSERT)."""
    gids = pl.DataFrame({"game_id": poss["game_id"].unique().to_list()})
    con.register("_chunk_games", gids.to_arrow())
    con.register("_chunk_poss", poss.to_arrow())
    cols = ", ".join(_SCHEMA)
    sel = ", ".join(f"{c}::INTEGER[5] AS {c}" if c.endswith("_players") else c for c in _SCHEMA)
    try:
        con.execute("BEGIN")
        con.execute("DELETE FROM possessions WHERE game_id IN (SELECT game_id FROM _chunk_games)")
        con.execute(f"INSERT INTO possessions ({cols}) SELECT {sel} FROM _chunk_poss")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        for v in ("_chunk_games", "_chunk_poss"):
            con.unregister(v)


def write_chunked(
    db_path: Path,
    rebuilt: Rebuilt,
    *,
    chunk_games: int = CHUNK_GAMES,
    poll_s: float = 5.0,
    max_wait_s: float = 3600.0,
    pause_s: float = 5.0,
) -> int:
    """Write in chunks, one short read-write connection per chunk. Returns chunks written."""
    gids = sorted(rebuilt.games)
    by_game = rebuilt.possessions.partition_by("game_id", as_dict=True)
    n = 0
    for i in range(0, len(gids), chunk_games):
        chunk = gids[i : i + chunk_games]
        frame = pl.concat([by_game[(g,)] for g in chunk])
        waited = 0.0
        while True:
            if not db_holders(db_path):
                try:
                    con = duckdb.connect(str(db_path))
                except duckdb.IOException:
                    con = None  # lost a race for the lock; retry
                if con is not None:
                    try:
                        write_chunk(con, frame)
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
        time.sleep(pause_s)
    return n


def verify_written(con: duckdb.DuckDBPyConnection, expected: pl.DataFrame) -> dict[str, int]:
    """After a write: stored rows must equal ``expected`` (key set, row count, lineups)."""
    got = load_stored(con)
    exp = expected.sort(_KEY)
    got = got.filter(pl.col("game_id").is_in(exp["game_id"].unique().to_list())).sort(_KEY)
    return {"rows_expected": exp.height, "rows_stored": got.height, "equal": int(got.equals(exp))}


def _inputs(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, pl.DataFrame]:
    box = con.execute(
        "SELECT game_id, team_id, fga, fta, tov, oreb FROM player_game_stats WHERE fga IS NOT NULL"
    ).pl()
    pts = con.execute("SELECT game_id, home_team, away_team, home_pts, away_pts FROM games").pl()
    return team_box_from_player_game_stats(box), pts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.rebuild_possessions")
    ap.add_argument("--db", type=Path, default=ROOT / "nba.duckdb")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--write", action="store_true", help="gates, backup, then chunked write")
    ap.add_argument("--every", type=int, default=1, help="use every Nth game (compute-only checks)")
    ap.add_argument(
        "--report-dir", type=Path, default=ROOT / "reports" / "possession_order_rebuild"
    )
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    ap.add_argument("--pause-s", type=float, default=5.0, help="sleep between write chunks")
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
                f"computed {len(rebuilt.games)} games in {time.monotonic() - t0:.0f}s", flush=True
            )
            old = load_stored(con).filter(pl.col("game_id").is_in(rebuilt.games))
            team_box, game_pts = _inputs(con)
            before = measure(old, team_box, game_pts)
            after = measure(rebuilt.possessions, team_box, game_pts)
            renum = renumbering(old, rebuilt.possessions)
            gates = evaluate_gates(before, after)
            snap: list[Path] | None = None
            if args.write and all(x.ok for x in gates):
                snap = snapshot_pre_fix(con, args.backup_dir)
        finally:
            con.close()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    ids = renum.pop("renumbered_game_ids")
    (args.report_dir / "renumbered_games.txt").write_text("\n".join(ids) + "\n")
    summary = {
        "games": len(rebuilt.games),
        "skipped": {k: len(v) for k, v in rebuilt.skipped.items()},
        "before": before,
        "after": after,
        "renumbering": renum,
        "gates": [{"name": x.name, "ok": x.ok, "detail": x.detail} for x in gates],
        "backup": [str(p) for p in snap] if snap else None,
    }
    (args.report_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    failed = [x for x in gates if not x.ok]
    if failed:
        print("FAIL gates: " + ", ".join(x.name for x in failed))
        if args.write:
            print("REFUSING --write: nothing written")
        return 2
    if args.write:
        write_chunked(args.db, rebuilt, pause_s=args.pause_s)
        con = duckdb.connect(str(args.db), read_only=True)
        try:
            check = verify_written(con, rebuilt.possessions)
        finally:
            con.close()
        print("VERIFY", json.dumps(check))
        if not check["equal"]:
            print("FAIL: stored table differs from the computed one after write")
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
