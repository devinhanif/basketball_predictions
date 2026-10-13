"""Repair history player box and possessions after the free-throw wording fix (2013-17 text).

A few missed free throws in the 2013-14 .. 2016-17 play-by-play carry neither the ``MISS`` token
nor a points total ("Free Throw 2 of 2"); the old test counted them as makes, so the shooter's
``pts`` and ``ftm`` were one too high (SEQ_LEARNING_CURVE_2 gate D3). ``nbastats_box.ft_made``
now decides, and the adapter writes the miss mark into the V3 text.

This module compares a staging directory rebuilt with the fix against the one that was loaded,
then (``apply``) corrects exactly the changed rows in ``nba_history.duckdb``:

* ``player_game_stats``: ``pts`` and ``ftm`` only, by (game_id, player_id);
* ``possessions``: the rows of the changed games are replaced by the rebuilt ones (same count);
* ``data/history/pbp/<game>.parquet``: the old file is moved (not deleted) to
  ``data/history/nbastats_stage/superseded_pbp_v3_ft_text/`` and a copy with the miss mark in the
  affected descriptions is written.

    uv run python -m nba.parse.history_ft_fix diff  --old <stage> --new <stage> --season 2013 ...
    uv run python -m nba.parse.history_ft_fix apply --old <stage> --new <stage> --season 2013 ...
    uv run python -m nba.parse.history_ft_fix pbp --extracted <dir> --season 2013 ...

``apply`` holds ``data/ops/heavy.lock``, backs up the DB first and rolls back on any gate failure.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from nba.ingest.history_static_fix import BACKUP_DIR, heavy_lock
from nba.ingest.nbastats_box import ft_made
from nba.ingest.nbastats_pbp import game_id10, read_nbastats

HISTORY_DB = Path("data/history/nba_history.duckdb")
PBP_DIR = Path("data/history/pbp")
SUPERSEDED = Path("data/history/nbastats_stage/superseded_pbp_v3_ft_text")
KEY = ["game_id", "player_id"]


def _box_changes(old: pl.DataFrame, new: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """Rows whose pts/ftm differ, and the other columns (names) that differ anywhere."""
    j = old.join(new, on=KEY, how="full", suffix="_n", coalesce=True)
    cols = [c for c in old.columns if c not in KEY]
    other = [
        c
        for c in cols
        if c not in ("pts", "ftm") and j.filter(pl.col(c).ne_missing(pl.col(f"{c}_n"))).height > 0
    ]
    changed = j.filter(
        pl.col("pts").ne_missing(pl.col("pts_n")) | pl.col("ftm").ne_missing(pl.col("ftm_n"))
    )
    return changed, other


def diff_season(old_dir: Path, new_dir: Path) -> dict[str, Any]:
    ob, nb = pl.read_parquet(old_dir / "box.parquet"), pl.read_parquet(new_dir / "box.parquet")
    changed, other = _box_changes(ob, nb)
    rows_ok = (
        ob.height == nb.height
        and changed.filter(
            (pl.col("pts_n") - pl.col("pts") != pl.col("ftm_n") - pl.col("ftm"))
            | (pl.col("ftm_n") - pl.col("ftm") != -1)
        ).height
        == 0
    )
    op, np_ = (pl.read_parquet(d / "possessions.parquet") for d in (old_dir, new_dir))
    pk = ["game_id", "poss_idx"]
    pj = op.join(np_, on=pk, how="full", suffix="_n", coalesce=True)
    pcols = [c for c in op.columns if c not in pk]
    pchanged = pj.filter(
        pl.any_horizontal(*[pl.col(c).ne_missing(pl.col(f"{c}_n")) for c in pcols])
    )
    same = {
        name: pl.read_parquet(old_dir / f"{name}.parquet").equals(
            pl.read_parquet(new_dir / f"{name}.parquet")
        )
        for name in ("games", "stints", "shots")
    }
    return {
        "box_rows": ob.height,
        "box_rows_equal_count": ob.height == nb.height,
        "box_rows_changed": changed.height,
        "box_changed_each_ftm_minus_1_and_pts_equal_delta": rows_ok,
        "box_other_columns_changed": other,
        "box_games_changed": sorted(changed["game_id"].unique().to_list()),
        "possessions_rows": op.height,
        "possessions_rows_equal_count": op.height == np_.height,
        "possessions_rows_changed": pchanged.height,
        "possessions_games_changed": sorted(pchanged["game_id"].unique().to_list()),
        "unchanged_tables_equal": same,
        "changed_examples": changed.select(*KEY, "pts", "pts_n", "ftm", "ftm_n").head(5).rows(),
    }


def unmarked_ft_events(extracted: Path, season: int) -> pl.DataFrame:
    """(game_id, action_number) of free-throw events that are neither a make nor marked MISS."""
    names = [f"nbastats_{season}", f"nbastats_po_{season}"]
    raw = read_nbastats([extracted / n / f"{n}.csv" for n in names])
    out: list[tuple[str, int]] = []
    for r in raw.filter(pl.col("EVENTMSGTYPE") == "3").iter_rows(named=True):
        d = r["HOMEDESCRIPTION"] or r["VISITORDESCRIPTION"] or r["NEUTRALDESCRIPTION"] or ""
        if not d.startswith("MISS") and not ft_made(d):
            out.append((game_id10(r["GAME_ID"]), int(r["EVENTNUM"])))
    return pl.DataFrame(out, schema={"game_id": pl.Utf8, "action_number": pl.Int64}, orient="row")


def rewrite_pbp(events: pl.DataFrame, pbp_dir: Path, superseded: Path) -> dict[str, int]:
    """Move each affected pbp file to ``superseded`` and write it back with the miss mark."""
    superseded.mkdir(parents=True, exist_ok=True)
    done = rows = 0
    for gid in sorted(events["game_id"].unique().to_list()):
        path = pbp_dir / f"{gid}.parquet"
        if not path.exists():
            continue
        acts = events.filter(pl.col("game_id") == gid)["action_number"].to_list()
        old = pl.read_parquet(path)
        hit = pl.col("action_number").is_in(acts) & ~pl.col("description").str.starts_with("MISS")
        new = old.with_columns(
            pl.when(hit)
            .then(pl.concat_str([pl.lit("MISS "), pl.col("description")]).str.strip_chars())
            .otherwise(pl.col("description"))
            .alias("description")
        )
        n = int(old.height - old.filter(~hit).height)
        if n == 0:
            continue
        dest = superseded / path.name
        if dest.exists():
            raise RuntimeError(f"{dest} already exists; refusing to overwrite")
        shutil.move(str(path), str(dest))
        new.write_parquet(path)
        done += 1
        rows += n
    return {"pbp_files_rewritten": done, "pbp_events_marked": rows}


def apply_fix(
    db: Path, old: Path, new: Path, seasons: list[int], *, backup_dir: Path = BACKUP_DIR
) -> dict[str, Any]:
    """Back up, then correct pts/ftm and replace possessions rows in one transaction."""
    from nba.db.connect import connect_with_retry

    box_changes: list[pl.DataFrame] = []
    poss_games: list[str] = []
    for s in seasons:
        ch, _ = _box_changes(
            pl.read_parquet(old / str(s) / "box.parquet"),
            pl.read_parquet(new / str(s) / "box.parquet"),
        )
        box_changes.append(ch.select(*KEY, "pts", "ftm", "pts_n", "ftm_n"))
        d = diff_season(old / str(s), new / str(s))
        if (
            d["box_other_columns_changed"]
            or not d["box_changed_each_ftm_minus_1_and_pts_equal_delta"]
        ):
            raise RuntimeError(f"season {s}: unexpected box change {d}")
        poss_games += d["possessions_games_changed"]
    chg = pl.concat(box_changes)
    newposs = pl.concat([pl.read_parquet(new / str(s) / "possessions.parquet") for s in seasons])
    newposs = newposs.filter(pl.col("game_id").is_in(poss_games))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"nba_history_pre_ft_wording_fix_{stamp}.duckdb"
    shutil.copy2(db, backup)
    if backup.stat().st_size != db.stat().st_size:
        raise RuntimeError("backup size mismatch; aborting before any write")
    w = connect_with_retry(db)
    try:
        n_box = w.execute("SELECT count(*) FROM player_game_stats").fetchone()[0]  # type: ignore[index]
        n_pos = w.execute("SELECT count(*) FROM possessions").fetchone()[0]  # type: ignore[index]
        n_games = w.execute("SELECT count(*) FROM games").fetchone()[0]  # type: ignore[index]
        w.register("chg", chg.to_arrow())
        w.register("np", newposs.to_arrow())
        stale = w.execute(
            "SELECT count(*) FROM chg c JOIN player_game_stats s USING (game_id, player_id) "
            "WHERE s.pts <> c.pts OR s.ftm <> c.ftm"
        ).fetchone()[0]  # type: ignore[index]
        w.execute("BEGIN")
        w.execute(
            "UPDATE player_game_stats SET pts = c.pts_n, ftm = c.ftm_n FROM chg c "
            "WHERE player_game_stats.game_id = c.game_id "
            "AND player_game_stats.player_id = c.player_id"
        )
        w.execute("DELETE FROM possessions WHERE game_id IN (SELECT DISTINCT game_id FROM np)")
        cols = ", ".join(newposs.columns)
        sel = ", ".join(
            f"CAST({c} AS INTEGER[5])" if c in ("off_players", "def_players") else c
            for c in newposs.columns
        )
        w.execute(f"INSERT INTO possessions ({cols}) SELECT {sel} FROM np")
        after = {
            "box": w.execute("SELECT count(*) FROM player_game_stats").fetchone()[0],  # type: ignore[index]
            "poss": w.execute("SELECT count(*) FROM possessions").fetchone()[0],  # type: ignore[index]
            "games": w.execute("SELECT count(*) FROM games").fetchone()[0],  # type: ignore[index]
            "unmatched": w.execute(
                "SELECT count(*) FROM chg c JOIN player_game_stats s USING (game_id, player_id) "
                "WHERE s.pts <> c.pts_n OR s.ftm <> c.ftm_n"
            ).fetchone()[0],  # type: ignore[index]
        }
        ok = (
            stale == 0
            and after["box"] == n_box
            and after["poss"] == n_pos
            and after["games"] == n_games
            and after["unmatched"] == 0
        )
        w.execute("COMMIT" if ok else "ROLLBACK")
        return {
            "backup": str(backup),
            "ok": ok,
            "rows_updated": chg.height,
            "possession_games_replaced": len(poss_games),
            "counts_before": {"box": n_box, "poss": n_pos, "games": n_games},
            "counts_after": after,
            "rows_not_matching_loaded_stage": stale,
        }
    finally:
        w.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.history_ft_fix")
    ap.add_argument("cmd", choices=["diff", "apply", "pbp"])
    ap.add_argument("--old", type=Path, default=Path("data/history/nbastats_stage"))
    ap.add_argument("--new", type=Path, default=Path("data/history/nbastats_stage_ftfix"))
    ap.add_argument("--season", type=int, action="append", required=True)
    ap.add_argument("--db", type=Path, default=HISTORY_DB)
    ap.add_argument("--extracted", type=Path, default=None, help="for pbp: dir with nbastats_<s>")
    args = ap.parse_args(argv)
    if args.cmd == "pbp":
        if args.extracted is None:
            ap.error("pbp needs --extracted")
        for s in args.season:
            ev = unmarked_ft_events(args.extracted, s)
            print(s, ev.height, "events", rewrite_pbp(ev, PBP_DIR, SUPERSEDED))
        return 0
    for s in args.season:
        print(s, diff_season(args.old / str(s), args.new / str(s)))
    if args.cmd == "diff":
        return 0
    with heavy_lock():
        res = apply_fix(args.db, args.old, args.new, args.season)
    print(res)
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
