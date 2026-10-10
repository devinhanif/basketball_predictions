"""Derive the fact store from existing tables. Idempotent: the out DB is dropped and rebuilt.

Every fact carries an honest ``known_at`` (naive UTC). The choices are written down here and in
docs/FACTS_STORE.md; none of them is a model output. The 2025 holdout is excluded in SQL
(``games.season <= max_season``).

* tips_at: schedule publish PROXY = 00:00 ET the day before the game (the schedule is out long
  before; this is deliberately late-ish and conservative).
* listed_status: the report's own ``as_of`` stamp (ET clock in the source, converted to UTC).
* announced_starter: the lineup snapshot's ``fetched_at``.
* player static: 1 Oct of ``first_season`` (players with no ``first_season`` get no static fact).
* head_coach: 1 Oct of the season, confidence 0.5 (team_coaches is wrong in 14 of 87 team-seasons).
* played_minutes: tip + 2h30 PROXY for game end; games with no real tip get no such fact.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import yaml

from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import ReportTriggerConfig, load_report_rows

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
GAME_LENGTH = dt.timedelta(hours=2, minutes=30)
ET = "America/New_York"

FACT_SCHEMA: dict[str, Any] = {
    "subject": pl.Utf8,
    "predicate": pl.Utf8,
    "object": pl.Utf8,
    "value": pl.Float64,
    "value_text": pl.Utf8,
    "known_at": pl.Datetime("us"),
    "valid_from": pl.Datetime("us"),
    "valid_to": pl.Datetime("us"),
    "source": pl.Utf8,
    "confidence": pl.Float64,
}


def et_to_utc(col: pl.Expr) -> pl.Expr:
    """Naive Eastern wall clock -> naive UTC."""
    return (
        col.dt.replace_time_zone(ET, ambiguous="earliest", non_existent="null")
        .dt.convert_time_zone("UTC")
        .dt.replace_time_zone(None)
    )


def _facts(df: pl.DataFrame, **const: Any) -> pl.DataFrame:
    """Fill every fact column: from ``df`` where present, else from ``const``, else NULL."""
    exprs = []
    for name, dtype in FACT_SCHEMA.items():
        if name in df.columns:
            exprs.append(pl.col(name).cast(dtype))
        else:
            exprs.append(pl.lit(const.get(name)).cast(dtype).alias(name))
    return df.select(exprs)


def _ent(prefix: str, col: str) -> pl.Expr:
    return pl.concat_str(pl.lit(prefix + ":"), pl.col(col).cast(pl.Utf8))


def _season_start(season: pl.Expr) -> pl.Expr:
    return pl.datetime(season, 10, 1)


def _day_before_et(day: pl.Expr) -> pl.Expr:
    """00:00 ET of the day before ``day`` (a date/datetime expr), as naive UTC."""
    return et_to_utc(day.dt.date().cast(pl.Datetime("us")) - pl.duration(days=1))


def allowed_games(
    src: duckdb.DuckDBPyConnection, max_season: int, since: str | None
) -> pl.DataFrame:
    sql = "SELECT game_id, game_date, season, home_team, away_team FROM games WHERE season <= ?"
    params: list[Any] = [max_season]
    if since:
        sql += " AND game_date >= ?"
        params.append(since)
    return src.execute(sql, params).pl()


def tips_facts(games: pl.DataFrame, tips: pl.DataFrame) -> pl.DataFrame:
    t = tips.join(games.select("game_id"), on="game_id")
    return _facts(
        t.select(
            _ent("game", "game_id").alias("subject"),
            pl.lit("tips_at").alias("predicate"),
            pl.col("tipoff_utc").alias("valid_from"),
            pl.col("tipoff_utc").dt.strftime("%Y-%m-%dT%H:%M:%S").alias("value_text"),
            _day_before_et(pl.col("tip_et")).alias("known_at"),
        ),
        source="schedule",
        confidence=1.0,
    )


def team_facts(games: pl.DataFrame) -> pl.DataFrame:
    known = _day_before_et(pl.col("game_date"))
    parts = []
    for pred, col in (("home_team", "home_team"), ("away_team", "away_team")):
        parts.append(
            games.select(
                _ent("game", "game_id").alias("subject"),
                pl.lit(pred).alias("predicate"),
                _ent("team", col).alias("object"),
                known.alias("known_at"),
            )
        )
    return _facts(pl.concat(parts), source="schedule", confidence=1.0)


def status_facts(src: duckdb.DuckDBPyConnection, games: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    rows = load_report_rows(src, ReportTriggerConfig())
    rows = rows.join(games.select("game_id"), on="game_id")
    df = rows.select(
        _ent("player", "player_id").alias("subject"),
        pl.lit("listed_status").alias("predicate"),
        _ent("game", "game_id").alias("object"),
        pl.col("status").alias("value_text"),
        et_to_utc(pl.col("as_of")).alias("known_at"),
    )
    n_bad = df.filter(pl.col("known_at").is_null()).height
    df = df.filter(pl.col("known_at").is_not_null())
    return _facts(df, source="official_report", confidence=1.0), n_bad


def lineup_facts(
    lcon: duckdb.DuckDBPyConnection, excluded: set[str], since: str | None
) -> pl.DataFrame:
    sql = (
        "SELECT game_id, team_id, is_home, player_id, lineup_status, announced_starter, "
        "fetched_at FROM lineup_snapshots"
    )
    params: list[Any] = []
    if since:
        sql += " WHERE game_date >= ?"
        params.append(since)
    d = lcon.execute(sql, params).pl()
    d = d.filter(~pl.col("game_id").is_in(list(excluded)))
    starters = d.select(
        _ent("player", "player_id").alias("subject"),
        pl.lit("announced_starter").alias("predicate"),
        _ent("game", "game_id").alias("object"),
        pl.col("announced_starter").cast(pl.Float64).alias("value"),
        pl.col("lineup_status").alias("value_text"),
        pl.col("fetched_at").alias("known_at"),
    )
    teams = (
        d.group_by("game_id", "team_id", "is_home")
        .agg(pl.col("fetched_at").min().alias("known_at"))
        .select(
            _ent("game", "game_id").alias("subject"),
            pl.when(pl.col("is_home"))
            .then(pl.lit("home_team"))
            .otherwise(pl.lit("away_team"))
            .alias("predicate"),
            _ent("team", "team_id").alias("object"),
            pl.col("known_at"),
        )
    )
    return pl.concat(
        [
            _facts(starters, source="lineups_feed", confidence=1.0),
            _facts(teams, source="lineups_feed", confidence=1.0),
        ]
    )


def lineup_tip_facts(
    lcon: duckdb.DuckDBPyConnection, excluded: set[str], since: str | None
) -> pl.DataFrame:
    sql = "SELECT game_id, tipoff, recorded_at FROM game_tips"
    params: list[Any] = []
    if since:
        sql += " WHERE game_date >= ?"
        params.append(since)
    d = lcon.execute(sql, params).pl().filter(~pl.col("game_id").is_in(list(excluded)))
    return _facts(
        d.select(
            _ent("game", "game_id").alias("subject"),
            pl.lit("tips_at").alias("predicate"),
            pl.col("tipoff").alias("valid_from"),
            pl.col("tipoff").dt.strftime("%Y-%m-%dT%H:%M:%S").alias("value_text"),
            pl.col("recorded_at").alias("known_at"),
        ),
        source="lineups_game_tips",
        confidence=1.0,
    )


def static_facts(src: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, int]:
    d = src.execute("SELECT player_id, position, height_in, first_season FROM players_static").pl()
    n_skipped = d.filter(pl.col("first_season").is_null()).height
    d = d.filter(pl.col("first_season").is_not_null()).with_columns(
        _season_start(pl.col("first_season")).alias("known_at"),
    )
    subj = _ent("player", "player_id").alias("subject")
    base = [subj, pl.col("known_at"), pl.col("known_at").alias("valid_from")]
    parts = [
        d.filter(pl.col("position").is_not_null()).select(
            *base, pl.lit("position").alias("predicate"), pl.col("position").alias("value_text")
        ),
        d.filter(pl.col("height_in").is_not_null()).select(
            *base, pl.lit("height_in").alias("predicate"), pl.col("height_in").alias("value")
        ),
        d.select(
            *base,
            pl.lit("first_season").alias("predicate"),
            pl.col("first_season").cast(pl.Float64).alias("value"),
        ),
    ]
    frames = [_facts(p, source="players_static", confidence=1.0) for p in parts]
    return pl.concat(frames), n_skipped


def coach_facts(
    src: duckdb.DuckDBPyConnection, max_season: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    d = src.execute(
        "SELECT season, team_id, coach_id, name FROM team_coaches "
        "WHERE coach_type = 'Head Coach' AND season <= ?",
        [max_season],
    ).pl()
    start = _season_start(pl.col("season"))
    facts = d.select(
        _ent("team", "team_id").alias("subject"),
        pl.lit("head_coach").alias("predicate"),
        _ent("coach", "coach_id").alias("object"),
        pl.col("season").cast(pl.Float64).alias("value"),
        pl.lit("team_coaches; known wrong in 14 of 87 team-seasons").alias("value_text"),
        start.alias("known_at"),
        start.alias("valid_from"),
        _season_start(pl.col("season") + 1).alias("valid_to"),
    )
    names = d.select(_ent("coach", "coach_id").alias("entity_id"), pl.col("name")).unique(
        "entity_id"
    )
    return _facts(facts, source="team_coaches", confidence=0.5), names


def minutes_facts(
    src: duckdb.DuckDBPyConnection, games: pl.DataFrame, tips: pl.DataFrame
) -> tuple[pl.DataFrame, int]:
    stats = src.execute("SELECT game_id, player_id, minutes FROM player_game_stats").pl()
    stats = stats.join(games.select("game_id"), on="game_id")
    with_tip = stats.join(tips.select("game_id", "tipoff_utc"), on="game_id", how="inner")
    n_no_tip = stats.select("game_id").unique().height - with_tip.select("game_id").unique().height
    df = with_tip.select(
        _ent("player", "player_id").alias("subject"),
        pl.lit("played_minutes").alias("predicate"),
        _ent("game", "game_id").alias("object"),
        pl.col("minutes").cast(pl.Float64).alias("value"),
        pl.when(pl.col("minutes").is_null()).then(pl.lit("DNP")).alias("value_text"),
        (pl.col("tipoff_utc") + pl.lit(GAME_LENGTH)).alias("known_at"),
    )
    return _facts(df, source="box_score", confidence=1.0), n_no_tip


def alias_entities(
    odds_path: Path, kalshi_path: Path
) -> dict[str, tuple[str, dict[str, list[str]]]]:
    """player entity id -> (canonical_name, source_ids). Kalshi (reviewed) names win."""
    out: dict[str, tuple[str, dict[str, list[str]]]] = {}
    odds = yaml.safe_load(odds_path.read_text()).get("aliases", {}) if odds_path.exists() else {}
    kal = yaml.safe_load(kalshi_path.read_text()).get("aliases", {}) if kalshi_path.exists() else {}
    names: dict[int, dict[str, list[str]]] = {}
    for name, pid in odds.items():
        names.setdefault(int(pid), {}).setdefault("odds_aliases", []).append(str(name))
    for name, pid in kal.items():
        names.setdefault(int(pid), {}).setdefault("kalshi_aliases", []).append(str(name))
    for pid, ids in names.items():
        canon = ids["kalshi_aliases"][0] if "kalshi_aliases" in ids else ids["odds_aliases"][0]
        out[f"player:{pid}"] = (canon, ids)
    return out


def build_entities(
    facts: pl.DataFrame,
    aliases: dict[str, tuple[str, dict[str, list[str]]]],
    coach_names: pl.DataFrame,
) -> pl.DataFrame:
    seen = set(facts["subject"].to_list())
    seen |= {o for o in facts["object"].to_list() if o}
    seen |= set(aliases)
    cname = dict(zip(coach_names["entity_id"], coach_names["name"], strict=True))
    rows = []
    for eid in sorted(seen):
        name, ids = None, None
        if eid in aliases:
            name, ids = aliases[eid][0], aliases[eid][1]
        elif eid in cname:
            name = cname[eid]
        rows.append(
            {
                "entity_id": eid,
                "kind": eid.split(":", 1)[0],
                "canonical_name": name,
                "source_ids": json.dumps(ids) if ids else None,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "entity_id": pl.Utf8,
            "kind": pl.Utf8,
            "canonical_name": pl.Utf8,
            "source_ids": pl.Utf8,
        },
    )


def _write(out: Path, facts: pl.DataFrame, entities: pl.DataFrame) -> None:
    """Drop and recreate ``out``; facts are numbered in a deterministic order."""
    for suffix in ("", ".wal"):
        Path(str(out) + suffix).unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    facts = facts.sort("predicate", "subject", "known_at", "object", "source", nulls_last=True)
    facts = facts.with_row_index("fact_id", offset=1).with_columns(pl.col("fact_id").cast(pl.Int64))
    con = duckdb.connect(str(out))
    try:
        con.execute(SCHEMA_PATH.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            fp, ep = Path(tmp) / "facts.parquet", Path(tmp) / "entities.parquet"
            facts.write_parquet(fp)
            entities.write_parquet(ep)
            cols = ", ".join(["fact_id", *FACT_SCHEMA])
            con.execute(f"INSERT INTO facts SELECT {cols} FROM read_parquet(?)", [str(fp)])
            con.execute(
                "INSERT INTO entities SELECT entity_id, kind, canonical_name, "
                "CAST(source_ids AS JSON) FROM read_parquet(?)",
                [str(ep)],
            )
    finally:
        con.close()


def build(
    src: duckdb.DuckDBPyConnection,
    lcon: duckdb.DuckDBPyConnection | None,
    out: Path,
    *,
    schedule_dir: Path | str = "data/schedule",
    odds_aliases: Path = Path("configs/odds_player_aliases.yaml"),
    kalshi_aliases: Path = Path("configs/kalshi_aliases.yaml"),
    since: str | None = None,
    max_season: int = 2024,
) -> dict[str, Any]:
    notes: list[str] = []
    games = allowed_games(src, max_season, since)
    over = src.execute("SELECT game_id FROM games WHERE season > ?", [max_season]).fetchall()
    excluded = {str(r[0]) for r in over}
    tips = build_game_tipoff(schedule_dir)

    parts = [tips_facts(games, tips), team_facts(games)]
    st, n_bad = status_facts(src, games)
    parts.append(st)
    if n_bad:
        notes.append(f"{n_bad} report rows dropped: report time falls in a DST gap")
    if lcon is not None:
        parts += [lineup_facts(lcon, excluded, since), lineup_tip_facts(lcon, excluded, since)]
    sf, n_skip = static_facts(src)
    parts.append(sf)
    notes.append(f"{n_skip} players_static rows have no first_season: no static facts for them")
    cf, coach_names = coach_facts(src, max_season)
    parts.append(cf)
    mf, n_no_tip = minutes_facts(src, games, tips)
    parts.append(mf)
    notes.append(f"{n_no_tip} games have no real tip: no played_minutes facts for them")

    facts = pl.concat(parts)
    entities = build_entities(facts, alias_entities(odds_aliases, kalshi_aliases), coach_names)
    _write(out, facts, entities)

    def by(col: str) -> dict[str, int]:
        return dict(facts.group_by(col).len().sort(col).iter_rows())

    return {
        "predicate": by("predicate"),
        "source": by("source"),
        "entities": entities.height,
        "notes": notes,
    }


def build_from_paths(
    source: str,
    out: str,
    lineups: str,
    *,
    since: str | None = None,
    max_season: int = 2024,
) -> dict[str, Any]:
    src = duckdb.connect(source, read_only=True)
    lcon = duckdb.connect(lineups, read_only=True) if Path(lineups).exists() else None
    try:
        return build(src, lcon, Path(out), since=since, max_season=max_season)
    finally:
        src.close()
        if lcon is not None:
            lcon.close()
