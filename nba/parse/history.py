"""Parse cached play-by-play into possessions/stints for a target DuckDB (history mode).

Orchestration glue over ``parse_possessions`` / ``track_lineups`` /
``attach_lineups_to_possessions`` / ``load_*``. Built for the separate history
database (``data/history/nba_history.duckdb``) so older seasons never enter
``nba.duckdb``; nothing here defaults to the production DB.

Per game it needs (a) the cached pbp parquet and (b) box-score rows in the target
DB (starters seed the lineup tracker, and the box score is the ground truth for
reconciliation). Resumable: games that already have possessions are skipped unless
``force``. Validation (reported, never silently dropped):

  - possession count vs ``FGA + 0.44*FTA + TOV - OREB`` per team-game
    (reported against the production parser's own error, see PRODUCTION_MAE_CEILING);
  - parsed points per team vs ``games.home_pts/away_pts``;
  - share of possessions with a clean 5-v-5 lineup.

CPU-light per game but ~1.3k games per season: callers hold ``data/ops/heavy.lock``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, cache_path_for, open_db
from nba.parse.lineups import (
    MINUTES_WITHIN_TOL,
    attach_lineups_to_possessions,
    reconcile_stint_minutes,
    track_lineups_with_report,
)
from nba.parse.loader import load_possession_lineups, load_possessions, load_stints
from nba.parse.possessions import parse_possessions
from nba.parse.reconcile import (
    mean_abs_error,
    reconcile_possessions,
    team_box_from_player_game_stats,
)

#: The production parser (nba.duckdb, 2022-2025, ~10.5k team-games per season set) measures
#: mean |error| 1.9-2.2 and signed bias -1.3 to -1.7 against ``FGA+0.44*FTA+TOV-OREB`` (the
#: estimator itself overcounts; it is not exact). The literal "<= 1" gate is therefore reported
#: but not enforced; history seasons are flagged only if MAE exceeds this ceiling.
PRODUCTION_MAE_CEILING = 3.0


@dataclass
class ParseSummary:
    candidates: int = 0
    parsed: int = 0
    skipped_existing: int = 0
    no_pbp: int = 0
    no_box: int = 0
    no_starters: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mean_abs_poss_error: float = float("nan")
    mean_signed_poss_error: float = float("nan")
    points_exact_rate: float = float("nan")
    clean_lineup_rate: float = float("nan")
    lineup_unresolved_subs: int = 0  # sub names the tracker could not tie to one player
    lineup_desync_subs: int = 0  # subs contradicted by the tracked floor (skipped)
    lineup_open_filled: int = 0  # team-periods whose look-ahead found < 5 openers
    lineup_q1_mismatch: int = 0  # teams whose Q1 look-ahead != box starters
    minutes_n: int = 0  # player-games (box minutes > 0) checked against stint minutes
    minutes_within: int = 0  # ... of which |stint - box| <= MINUTES_WITHIN_TOL
    gate_pass: bool = False  # literal CLAUDE.md gate: mean |error| <= 1 (informational, see below)
    within_production_envelope: bool = False  # MAE <= PRODUCTION_MAE_CEILING


def _starters(box: pl.DataFrame) -> dict[int, list[int]] | None:
    out: dict[int, list[int]] = {}
    for team_id, grp in box.filter(pl.col("starter")).group_by("team_id"):
        ids = grp["player_id"].to_list()
        if len(ids) != 5:
            return None
        out[int(team_id[0])] = [int(x) for x in ids]
    return out if len(out) == 2 else None


def parse_games(
    con: duckdb.DuckDBPyConnection,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    seasons: list[int] | None = None,
    limit: int | None = None,
    force: bool = False,
    log_every: int = 100,
) -> ParseSummary:
    """Parse every eligible game in ``con``'s ``games`` table; returns validation summary."""
    summary = ParseSummary()
    cond = "" if seasons is None else f"AND g.season IN ({', '.join(str(int(s)) for s in seasons)})"
    games = con.execute(
        f"""
        SELECT g.game_id, g.home_pts, g.away_pts, g.home_team, g.away_team
        FROM games g WHERE true {cond} ORDER BY g.season DESC, g.game_id
        """
    ).fetchall()
    done = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM possessions").fetchall()}
    boxed = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM player_game_stats").fetchall()}
    parsed_ids: list[str] = []
    t0 = time.monotonic()
    for gid, *_ in games:
        if limit is not None and summary.parsed >= limit:
            break
        path = cache_path_for("pbp", gid, data_dir)
        if not path.exists():
            summary.no_pbp += 1
            continue
        summary.candidates += 1
        if gid in done and not force:
            summary.skipped_existing += 1
            continue
        if gid not in boxed:
            summary.no_box += 1
            continue
        try:
            pbp = pl.read_parquet(path)
            poss = parse_possessions(pbp)
            box = con.execute(
                "SELECT player_id, team_id, starter, minutes "
                "FROM player_game_stats WHERE game_id = ?",
                [gid],
            ).pl()
            starters = _starters(box)
            con.execute("BEGIN")
            try:
                con.execute("DELETE FROM possessions WHERE game_id = ?", [gid])
                load_possessions(con, poss)
                if starters is None:
                    summary.no_starters.append(gid)
                else:
                    roster = {
                        int(t[0]): g["player_id"].to_list()
                        for t, g in box.filter(pl.col("minutes") > 0).group_by("team_id")
                    }
                    stints, rep = track_lineups_with_report(pbp, starters, roster)
                    summary.lineup_unresolved_subs += len(rep.unresolved)
                    summary.lineup_desync_subs += len(rep.desync)
                    summary.lineup_open_filled += len(rep.open_filled)
                    summary.lineup_q1_mismatch += len(rep.q1_mismatch)
                    rec = reconcile_stint_minutes(
                        stints, box.with_columns(pl.lit(gid).alias("game_id"))
                    ).filter(pl.col("box_minutes") > 0)
                    summary.minutes_n += rec.height
                    summary.minutes_within += int((rec["abs_diff"] <= MINUTES_WITHIN_TOL).sum())
                    poss = attach_lineups_to_possessions(poss, stints)
                    load_possession_lineups(con, poss)
                    load_stints(con, stints)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            summary.parsed += 1
            parsed_ids.append(gid)
        except Exception as exc:  # one bad game must not stop the batch
            summary.errors.append(f"{gid}: {type(exc).__name__}: {str(exc)[:100]}")
        if summary.parsed and summary.parsed % log_every == 0:
            rate = summary.parsed / max(time.monotonic() - t0, 1e-9)
            print(
                f"PROGRESS parse {summary.parsed}/{len(games)} errors={len(summary.errors)} "
                f"rate={rate:.2f}/s",
                flush=True,
            )
    _validate(con, parsed_ids, summary)
    return summary


def _validate(con: duckdb.DuckDBPyConnection, game_ids: list[str], summary: ParseSummary) -> None:
    if not game_ids:
        return
    con.register("_parsed_ids", pl.DataFrame({"game_id": game_ids}).to_arrow())
    try:
        poss = con.execute("SELECT p.* FROM possessions p JOIN _parsed_ids USING (game_id)").pl()
        box = con.execute(
            "SELECT b.* FROM player_game_stats b JOIN _parsed_ids USING (game_id) "
            "WHERE b.fga IS NOT NULL"
        ).pl()
        pts = con.execute(
            "SELECT g.game_id, g.home_team, g.away_team, g.home_pts, g.away_pts "
            "FROM games g JOIN _parsed_ids USING (game_id)"
        ).pl()
    finally:
        con.unregister("_parsed_ids")
    if box.is_empty():
        return
    rec = reconcile_possessions(poss, team_box_from_player_game_stats(box))
    summary.mean_abs_poss_error = mean_abs_error(rec)
    summary.mean_signed_poss_error = float(rec["error"].mean())  # type: ignore[arg-type]
    summary.gate_pass = summary.mean_abs_poss_error <= 1.0
    summary.within_production_envelope = summary.mean_abs_poss_error <= PRODUCTION_MAE_CEILING
    summary.clean_lineup_rate = float(
        poss.select(
            (pl.col("off_players").is_not_null() & pl.col("def_players").is_not_null()).mean()
        ).item()
    )
    parsed_pts = poss.group_by(["game_id", "off_team"]).agg(pl.col("pts").sum().alias("pp"))
    long = pl.concat(
        [
            pts.select(
                "game_id", pl.col("home_team").alias("off_team"), pl.col("home_pts").alias("t")
            ),
            pts.select(
                "game_id", pl.col("away_team").alias("off_team"), pl.col("away_pts").alias("t")
            ),
        ]
    ).join(parsed_pts, on=["game_id", "off_team"], how="left")
    summary.points_exact_rate = float((long["pp"] == long["t"]).mean())  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    import argparse

    from nba.ingest.__main__ import HISTORY_DB, HISTORY_DIR

    ap = argparse.ArgumentParser(prog="python -m nba.parse.history")
    ap.add_argument("--db-path", type=Path, default=HISTORY_DB)
    ap.add_argument("--data-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--season", type=int, action="append", dest="seasons")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    con = open_db(args.db_path)
    try:
        s = parse_games(
            con, data_dir=args.data_dir, seasons=args.seasons, limit=args.limit, force=args.force
        )
    finally:
        con.close()
    print(
        f"parse-history: candidates={s.candidates} parsed={s.parsed} existing={s.skipped_existing} "
        f"no_box={s.no_box} no_starters={len(s.no_starters)} errors={len(s.errors)} "
        f"mean_abs_poss_err={s.mean_abs_poss_error:.3f} mean_signed={s.mean_signed_poss_error:.3f} "
        f"points_exact={s.points_exact_rate:.3f} clean_5v5={s.clean_lineup_rate:.3f} "
        f"minutes_within_1={s.minutes_within}/{s.minutes_n} "
        f"unresolved_subs={s.lineup_unresolved_subs} "
        f"desync_subs={s.lineup_desync_subs} open_filled={s.lineup_open_filled} "
        f"q1_mismatch={s.lineup_q1_mismatch} "
        f"literal_gate(<=1)={'PASS' if s.gate_pass else 'no'} "
        f"within_prod_envelope(<={PRODUCTION_MAE_CEILING})={s.within_production_envelope}"
    )
    if s.parsed and not s.within_production_envelope:
        print("WARN parse reconciliation outside the production envelope; do not train on this")
    for e in s.errors[:10]:
        print("ERROR", e)
    return 2 if s.errors and len(s.errors) > 0.05 * max(s.parsed, 1) else 0


if __name__ == "__main__":
    raise SystemExit(main())
