"""Game-level head coach assignment (``team_coach_games``).

Why this exists: ``team_coaches`` (CommonTeamRoster) holds ONE head coach per team-season, the
last occupant of the job including summer hires. It is wrong in 14 of 87 reference team-seasons
(11 name the NEXT season's coach) and cannot hold a mid-season change. See
docs/reviews/data_audit_2026-10-11.md 7b.

Definition. One row per (game_id, team_id) for every regular-season, play-in and playoff game of
a season the reference covers (2022-2025; 2025 = 2025-26):

* ``coach_name`` / ``coach_id``: the head coach of that game. Regular season: the team's
  regular-season game number (date, then game_id order; ``games`` includes the 10 neutral-site
  games) against the reference ``head_coach_games_reference.csv`` (Basketball-Reference team
  pages, fetched by ``nba.data.coaches_br``, W+L per coach in tenure order). A change takes effect
  on the first game the new coach ran. Play-in and playoffs: the coach of the team's last
  regular-season game. ``coach_id`` is the ``team_coaches`` id matched on a diacritic-free name;
  NULL when the coach has no staff row there (listed by the loader, never guessed).
* ``known_at``: the real scheduled tip (naive UTC) of that game. A coach is public by his first
  tip, so this is a conservative bound; it is per game, never a season start.

Imputation: none. The build raises, listing every team-season whose coach games do not sum to its
regular-season games, rather than guess a change date.

Dry run (read-only) and load:

    uv run python -m nba.data.coaches
    mkdir data/ops/heavy.lock && uv run python -m nba.data.coaches --apply
    rmdir data/ops/heavy.lock
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path
from typing import Any

import polars as pl

REFERENCE_PATH = Path(__file__).with_name("head_coach_games_reference.csv")
HANDCOPIED_PATH = Path(__file__).with_name("head_coach_reference_handcopied_2026-10-10.csv")
POST_PATTERN = "^(004|005|006)"  # playoffs, play-in, NBA Cup final
REG_PREFIX = "002"

_SCHEMA_DICT: dict[str, Any] = {
    "game_id": pl.Utf8,
    "team_id": pl.Int64,
    "coach_name": pl.Utf8,
    "coach_id": pl.Int64,
    "source": pl.Utf8,
    "known_at": pl.Datetime("us"),
}
SCHEMA = pl.Schema(_SCHEMA_DICT)


class ReferenceMismatch(ValueError):
    """The reference does not tile a team-season's regular-season games."""


def load_reference(path: Path = REFERENCE_PATH) -> pl.DataFrame:
    return pl.read_csv(
        path, schema_overrides={"team_id": pl.Int64, "season": pl.Int64, "fetched_date": pl.Utf8}
    )


def norm(name: str) -> str:
    """Diacritic-free, letters-only lowercase key ('Jordi Fernandez' == 'Jordi Fernández')."""
    s = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in s.lower() if ch.isalpha() and ord(ch) < 128)


def coach_id_map(team_coaches: pl.DataFrame) -> dict[str, int]:
    """Name key -> team_coaches coach_id over every staff row. An ambiguous key raises."""
    out: dict[str, int] = {}
    for r in team_coaches.select("coach_id", "name").unique().iter_rows(named=True):
        if r["coach_id"] is None or r["name"] is None:
            continue
        k = norm(r["name"])
        if k in out and out[k] != int(r["coach_id"]):
            raise ValueError(f"name key {k!r} maps to two coach ids ({out[k]}, {r['coach_id']})")
        out[k] = int(r["coach_id"])
    return out


def _team_games(games: pl.DataFrame) -> pl.DataFrame:
    return pl.concat(
        [
            games.select("game_id", "game_date", "season", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", "season", pl.col("away_team").alias("team_id")),
        ]
    )


def reference_problems(games: pl.DataFrame, reference: pl.DataFrame) -> list[str]:
    """Every way the reference fails to tile the regular season (empty list = clean)."""
    tg = _team_games(games).filter(pl.col("game_id").str.starts_with(REG_PREFIX))
    n_reg = {
        (r["season"], r["team_id"]): r["n"]
        for r in tg.group_by("season", "team_id").agg(pl.len().alias("n")).iter_rows(named=True)
    }
    seasons = set(reference["season"].to_list())
    out: list[str] = []
    ref_keys = set()
    for (season, team_id), g in reference.sort("season", "team_id", "seq").group_by(
        ["season", "team_id"], maintain_order=True
    ):
        key = (int(season), int(team_id))
        ref_keys.add(key)
        expect = 1
        for r in g.iter_rows(named=True):
            if r["first_game"] != expect or r["last_game"] - r["first_game"] + 1 != r["games"]:
                out.append(
                    f"{key}: {r['coach_name']} games {r['first_game']}-{r['last_game']} "
                    f"do not follow {expect - 1}"
                )
            expect = r["last_game"] + 1
        have = n_reg.get(key)
        if have is None or have != expect - 1:
            out.append(f"{key}: coach games sum to {expect - 1}, regular-season games {have}")
    for key in n_reg:
        if key[0] in seasons and key not in ref_keys:
            out.append(f"{key}: no reference rows")
    return sorted(out)


def build_team_coach_games(
    games: pl.DataFrame,
    reference: pl.DataFrame,
    tips: pl.DataFrame,
    team_coaches: pl.DataFrame,
) -> pl.DataFrame:
    """``games``: game_id, game_date, season, home_team, away_team. ``tips``: game_id, tipoff_utc.
    ``team_coaches``: coach_id, name (any staff rows). Raises ``ReferenceMismatch`` on a problem."""
    problems = reference_problems(games, reference)
    if problems:
        raise ReferenceMismatch("\n".join(problems))
    ids = coach_id_map(team_coaches)
    seasons = sorted(set(reference["season"].to_list()))
    tg = _team_games(games).filter(pl.col("season").is_in(seasons))
    reg = (
        tg.filter(pl.col("game_id").str.starts_with(REG_PREFIX))
        .sort("season", "team_id", "game_date", "game_id")
        .with_columns((pl.int_range(pl.len()).over(["season", "team_id"]) + 1).alias("reg_no"))
    )
    steps = {
        k: g.sort("seq").select("coach_name", "last_game", "source_url").rows()
        for k, g in reference.group_by(["season", "team_id"], maintain_order=True)
    }
    out: list[dict[str, object]] = []
    last: dict[tuple[int, int], tuple[str, str]] = {}
    for r in reg.iter_rows(named=True):
        key = (r["season"], r["team_id"])
        nm, url = next((s[0], s[2]) for s in steps[key] if r["reg_no"] <= s[1])
        out.append(
            {"game_id": r["game_id"], "team_id": r["team_id"], "coach_name": nm, "source": url}
        )
        last[key] = (nm, url)  # reg is date-ordered per team: the final write is the last game
    post = tg.filter(pl.col("game_id").str.contains(POST_PATTERN))
    for r in post.iter_rows(named=True):
        key = (r["season"], r["team_id"])
        nm, url = last[key]
        out.append(
            {"game_id": r["game_id"], "team_id": r["team_id"], "coach_name": nm,
             "source": f"{url} (post-season: last regular-season coach)"}
        )  # fmt: skip
    df = pl.DataFrame(
        out,
        schema={"game_id": pl.Utf8, "team_id": pl.Int64, "coach_name": pl.Utf8, "source": pl.Utf8},
    )
    df = df.with_columns(
        pl.col("coach_name")
        .map_elements(lambda n: ids.get(norm(n)), return_dtype=pl.Int64)
        .alias("coach_id")
    ).join(tips.select("game_id", "tipoff_utc"), on="game_id", how="left")
    return (
        df.rename({"tipoff_utc": "known_at"})
        .select(list(SCHEMA))
        .cast(SCHEMA)
        .sort("game_id", "team_id")
    )


def coach_changes(games: pl.DataFrame, reference: pl.DataFrame) -> pl.DataFrame:
    """Mid-season changes: the first game (number, id, date) each non-first coach ran."""
    reg = (
        _team_games(games)
        .filter(pl.col("game_id").str.starts_with(REG_PREFIX))
        .sort("season", "team_id", "game_date", "game_id")
        .with_columns((pl.int_range(pl.len()).over(["season", "team_id"]) + 1).alias("reg_no"))
    )
    later = reference.filter(pl.col("seq") > 0).select(
        "season", "team_id", "team_abbr", "coach_name", "first_game"
    )
    return (
        later.join(
            reg,
            left_on=["season", "team_id", "first_game"],
            right_on=["season", "team_id", "reg_no"],
        )
        .select("season", "team_abbr", "coach_name", "first_game", "game_id", "game_date")
        .sort("season", "team_abbr", "first_game")
    )


def compare_references(new: pl.DataFrame, old: pl.DataFrame) -> list[str]:
    """Differences between two references over common team-seasons (name key, games, order)."""

    def tenure(df: pl.DataFrame) -> dict[tuple[int, int], list[tuple[str, int]]]:
        out: dict[tuple[int, int], list[tuple[str, int]]] = {}
        for r in df.sort("season", "team_id", "seq").iter_rows(named=True):
            key = (int(r["season"]), int(r["team_id"]))
            out.setdefault(key, []).append((norm(r["coach_name"]), int(r["games"])))
        return out

    a, b = tenure(new), tenure(old)
    return sorted(f"{k}: new {a[k]} vs old {b[k]}" for k in sorted(set(a) & set(b)) if a[k] != b[k])


def _sources(db: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    import duckdb

    from nba.features.game_tipoff import build_game_tipoff

    con = duckdb.connect(str(db), read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team "
            "FROM games WHERE season <= 2025"
        ).pl()
        staff = con.execute("SELECT coach_id, name FROM team_coaches").pl()
    finally:
        con.close()
    return games, build_game_tipoff(), staff


def main(argv: list[str] | None = None) -> int:
    from nba.db.connect import DEFAULT_DB_PATH

    ap = argparse.ArgumentParser(prog="python -m nba.data.coaches")
    ap.add_argument(
        "--apply", action="store_true", help="write team_coach_games (needs heavy.lock)"
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)
    games, tips, staff = _sources(args.db)
    ref = load_reference()
    print("reference problems:", reference_problems(games, ref) or "none")
    old = load_reference(HANDCOPIED_PATH)
    print("differences from the 2026-10-10 hand-copied reference (2022-24):",
          compare_references(ref, old) or "none")  # fmt: skip
    df = build_team_coach_games(games, ref, tips, staff)
    print(
        f"rows {df.height}; known_at NULL {df['known_at'].null_count()}; "
        f"coach_id NULL {df['coach_id'].null_count()} "
        f"({sorted(df.filter(pl.col('coach_id').is_null())['coach_name'].unique().to_list())})"
    )
    print(coach_changes(games, ref))
    if not args.apply:
        return 0
    lock = Path("data/ops/heavy.lock")
    if not lock.is_dir():
        print("refusing to write: data/ops/heavy.lock is not held (mkdir it first)")
        return 2
    import time
    from datetime import datetime

    from nba.db.connect import connect_with_retry

    now = datetime.now()
    if 15 <= now.minute < 25 or 45 <= now.minute < 55:
        print("refusing to write inside a pretip window (xx:15-25, xx:45-55)")
        return 3
    w = connect_with_retry(args.db)
    try:
        backup = (
            Path("data/backups")
            / f"team_coach_games_pre_br_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.parquet"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        w.execute(f"COPY (SELECT * FROM team_coach_games) TO '{backup}' (FORMAT PARQUET)")
        w.execute("BEGIN")
        w.execute("DROP TABLE IF EXISTS team_coach_games")
        w.execute(
            "CREATE TABLE team_coach_games (game_id VARCHAR, team_id INT, coach_name VARCHAR, "
            "coach_id INT, source VARCHAR, known_at TIMESTAMP, PRIMARY KEY (game_id, team_id))"
        )
        w.register("tcg_df", df.to_arrow())
        w.execute("INSERT INTO team_coach_games SELECT * FROM tcg_df")
        w.execute("COMMIT")
        print(f"wrote {df.height} rows; backup {backup}")
    finally:
        w.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
