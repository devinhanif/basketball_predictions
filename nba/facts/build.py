"""Derive the fact store from existing tables. Idempotent: the out DB is dropped and rebuilt.

Every fact carries an honest ``known_at`` (naive UTC). The choices are written down here and in
docs/FACTS_STORE.md; none of them is a model output. The 2025 holdout is excluded in SQL
(``games.season <= max_season``).

* tips_at: schedule publish PROXY = 00:00 ET the day before the game (the schedule is out long
  before; this is deliberately late-ish and conservative).
* listed_status: the report's own ``as_of`` stamp (ET clock in the source, converted to UTC).
* announced_starter: the lineup snapshot's ``fetched_at``.
* player static: 1 Oct of ``first_season`` (players with no ``first_season`` get no static fact).
* head_coach: per stint from team_coach_games (Basketball-Reference), known at the real tip of the
  first game he ran, confidence 0.9; seasons that table does not cover fall back to team_coaches
  (1 Oct, confidence 0.5, a last-occupant snapshot wrong in 14 of 87 reference team-seasons).
* played_minutes: tip + 2h30 PROXY for game end; games with no real tip get no such fact.

``seasons`` (CLI ``--seasons 2022,2023,2024,2026``, default none) is an explicit list of seasons
whose FULL facts load; when given it replaces the ``max_season`` bound everywhere (games, reports,
lineups, box, coaches, static). Anything unlisted (2025) stays absent.

``schedule_seasons`` (CLI ``--schedule-seasons 2026[,...]``, default none) is the one way a season
above ``max_season`` enters the store, and it admits ONLY ``tips_at``, ``home_team`` and
``away_team`` (``source = schedule``), read from ``data/schedule_cache/schedule_<season>.json`` (the
daily pipeline's last good fetch; falls back to ``data/schedule/raw_<season>.parquet``) with the
same known_at rule as above (00:00 ET the day before the game's ET date). No box score, report,
lineup, coach or starter fact is built for those seasons, and lineup-feed facts for any game whose
id says its season is above ``max_season`` are dropped (the ``games`` table does not hold future
games, so the old ``excluded`` set missed them). Season 2025 stays absent unless listed.
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

from nba.daily.schedule import ScheduledGame, load_schedule_cache, parse_schedule_frame
from nba.data.coaches import norm as coach_name_key
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


HOLDOUT_SEASON = 2025  # frozen holdout; lifting this guard is a logged, Devin-owned step


def guard_holdout(
    max_season: int, seasons: tuple[int, ...], schedule_seasons: tuple[int, ...]
) -> None:
    """Refuse every flag combination that would load season 2025 outcomes."""
    if HOLDOUT_SEASON in seasons or HOLDOUT_SEASON in schedule_seasons:
        raise ValueError(f"season {HOLDOUT_SEASON} is the frozen holdout and cannot be loaded")
    if not seasons and max_season >= HOLDOUT_SEASON:
        raise ValueError(f"max_season {max_season} would load the frozen holdout {HOLDOUT_SEASON}")


def season_ok(season: int | None, max_season: int, seasons: tuple[int, ...]) -> bool:
    """Is ``season`` loaded in full? ``seasons`` (when given) replaces the ``max_season`` bound."""
    if season is None:
        return False
    return season in seasons if seasons else season <= max_season


def _season_sql(max_season: int, seasons: tuple[int, ...]) -> tuple[str, list[Any]]:
    if seasons:
        return f"season IN ({', '.join('?' for _ in seasons)})", [int(x) for x in seasons]
    return "season <= ?", [max_season]


def allowed_games(
    src: duckdb.DuckDBPyConnection,
    max_season: int,
    since: str | None,
    seasons: tuple[int, ...] = (),
) -> pl.DataFrame:
    cond, params = _season_sql(max_season, seasons)
    sql = f"SELECT game_id, game_date, season, home_team, away_team FROM games WHERE {cond}"
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


def _season_label(season: int) -> str:
    return f"{season}-{(season + 1) % 100:02d}"


def game_id_season(game_id: str) -> int | None:
    """Start year from a 10-char NBA game id (``0022600001`` -> 2026); None if malformed."""
    g = str(game_id)
    return 2000 + int(g[3:5]) if len(g) == 10 and g[3:5].isdigit() else None


def load_schedule_games(
    season: int, cache_dir: Path, schedule_dir: Path | str
) -> tuple[list[ScheduledGame], str]:
    """Scheduled games of ``season`` and where they came from; raises if no source exists."""
    label = _season_label(season)
    cached = load_schedule_cache(label, cache_dir)
    if cached:
        return cached, f"{cache_dir}/schedule_{label}.json"
    raw = Path(schedule_dir) / f"raw_{label}.parquet"
    if raw.exists():
        return parse_schedule_frame(pl.read_parquet(raw)), str(raw)
    raise FileNotFoundError(f"no schedule source for {label}: {cache_dir} or {raw}")


def schedule_only_facts(games: list[ScheduledGame]) -> pl.DataFrame:
    """tips_at, home_team, away_team for scheduled games, same known_at rule as ``tips_facts``."""
    d = pl.DataFrame(
        {
            "game_id": [g.game_id for g in games],
            "tipoff_utc": [g.tipoff for g in games],
            "home": [g.home_team for g in games],
            "away": [g.away_team for g in games],
        },
        schema={
            "game_id": pl.Utf8,
            "tipoff_utc": pl.Datetime("us"),
            "home": pl.Int64,
            "away": pl.Int64,
        },
    ).unique("game_id", keep="last", maintain_order=True)
    tip_et = d["tipoff_utc"].dt.replace_time_zone("UTC").dt.convert_time_zone(ET)
    d = d.with_columns(tip_et.dt.replace_time_zone(None).alias("tip_et"))
    known = _day_before_et(pl.col("tip_et"))
    subj = _ent("game", "game_id").alias("subject")
    tips = d.select(
        subj,
        pl.lit("tips_at").alias("predicate"),
        pl.col("tipoff_utc").alias("valid_from"),
        pl.col("tipoff_utc").dt.strftime("%Y-%m-%dT%H:%M:%S").alias("value_text"),
        known.alias("known_at"),
    )
    teams = [
        d.select(
            subj,
            pl.lit(pred).alias("predicate"),
            _ent("team", col).alias("object"),
            known.alias("known_at"),
        )
        for pred, col in (("home_team", "home"), ("away_team", "away"))
    ]
    return _facts(pl.concat([tips, *teams], how="diagonal"), source="schedule", confidence=1.0)


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


def _drop_future_ids(
    d: pl.DataFrame, max_season: int, seasons: tuple[int, ...] = ()
) -> pl.DataFrame:
    """Keep rows whose game id says the season is loaded (<= ``max_season`` or in ``seasons``)."""
    keep = [season_ok(game_id_season(g), max_season, seasons) for g in d["game_id"].to_list()]
    return d.filter(pl.Series(keep, dtype=pl.Boolean))


def lineup_facts(
    lcon: duckdb.DuckDBPyConnection,
    excluded: set[str],
    since: str | None,
    max_season: int = 2024,
    seasons: tuple[int, ...] = (),
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
    d = _drop_future_ids(d, max_season, seasons)
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
    lcon: duckdb.DuckDBPyConnection,
    excluded: set[str],
    since: str | None,
    max_season: int = 2024,
    seasons: tuple[int, ...] = (),
) -> pl.DataFrame:
    sql = "SELECT game_id, tipoff, recorded_at FROM game_tips"
    params: list[Any] = []
    if since:
        sql += " WHERE game_date >= ?"
        params.append(since)
    d = lcon.execute(sql, params).pl().filter(~pl.col("game_id").is_in(list(excluded)))
    d = _drop_future_ids(d, max_season, seasons)
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


def static_facts(
    src: duckdb.DuckDBPyConnection, seasons: tuple[int, ...] = ()
) -> tuple[pl.DataFrame, int]:
    d = src.execute("SELECT player_id, position, height_in, first_season FROM players_static").pl()
    if seasons:  # a player whose first season is an unlisted one (2025) would be a 2025 fact
        lo = min(seasons)
        d = d.filter(
            pl.col("first_season").is_null()
            | (pl.col("first_season") < lo)
            | pl.col("first_season").is_in(list(seasons))
        )
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
    src: duckdb.DuckDBPyConnection, max_season: int, seasons: tuple[int, ...] = ()
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Head coach per stint from ``team_coach_games`` (Basketball-Reference reference).

    One fact per (team, season, uninterrupted run of the same coach): ``valid_from`` =
    ``known_at`` = the real tip of the first game he ran (a change is never known before he
    coaches), ``valid_to`` = the first tip of the next coach, else the next 1 October. Seasons the
    table does not cover keep the old ``team_coaches`` fact (1 Oct, confidence 0.5, a
    last-occupant snapshot).
    """
    cond, params = _season_sql(max_season, seasons)
    g = src.execute(
        "SELECT g.season, c.team_id, c.coach_id, c.coach_name AS name, c.known_at, g.game_date, "
        "c.game_id FROM team_coach_games c JOIN games g USING (game_id) "
        f"WHERE c.known_at IS NOT NULL AND g.{cond}",
        params,
    ).pl()
    runs = (
        g.sort("team_id", "season", "known_at", "game_id")
        .with_columns(
            (pl.col("name") != pl.col("name").shift(1))
            .fill_null(True)
            .cum_sum()
            .over(["team_id", "season"])
            .alias("run")
        )
        .group_by("team_id", "season", "run", maintain_order=True)
        .agg(pl.col("name").first(), pl.col("coach_id").first(), pl.col("known_at").min())
        .sort("team_id", "season", "run")
        .with_columns(
            pl.col("known_at").shift(-1).over(["team_id", "season"]).alias("next_start"),
        )
    )
    obj = (
        pl.when(pl.col("coach_id").is_not_null())
        .then(pl.concat_str(pl.lit("coach:"), pl.col("coach_id").cast(pl.Utf8)))
        .otherwise(
            pl.concat_str(
                pl.lit("coach:n:"),
                pl.col("name").map_elements(coach_name_key, return_dtype=pl.Utf8),
            )
        )
    )
    new = runs.select(
        _ent("team", "team_id").alias("subject"),
        pl.lit("head_coach").alias("predicate"),
        obj.alias("object"),
        pl.col("season").cast(pl.Float64).alias("value"),
        pl.lit("team_coach_games; basketball-reference.com").alias("value_text"),
        pl.col("known_at"),
        pl.col("known_at").alias("valid_from"),
        pl.coalesce(pl.col("next_start"), _season_start(pl.col("season") + 1)).alias("valid_to"),
        pl.col("name"),
        obj.alias("entity_id"),
    )
    parts = [_facts(new, source="team_coach_games", confidence=0.9)]
    names = new.select("entity_id", "name")
    covered = sorted(set(g["season"].to_list()))
    cond2 = "coach_type = 'Head Coach'"
    if covered:
        cond2 += f" AND season NOT IN ({', '.join(str(int(x)) for x in covered)})"
    d = src.execute(
        f"SELECT season, team_id, coach_id, name FROM team_coaches WHERE {cond2} AND {cond}",
        params,
    ).pl()
    if d.height:
        start = _season_start(pl.col("season"))
        old = d.select(
            _ent("team", "team_id").alias("subject"),
            pl.lit("head_coach").alias("predicate"),
            _ent("coach", "coach_id").alias("object"),
            pl.col("season").cast(pl.Float64).alias("value"),
            pl.lit("team_coaches; last-occupant snapshot, no reference for this season").alias(
                "value_text"
            ),
            start.alias("known_at"),
            start.alias("valid_from"),
            _season_start(pl.col("season") + 1).alias("valid_to"),
        )
        parts.append(_facts(old, source="team_coaches", confidence=0.5))
        names = pl.concat(
            [names, d.select(_ent("coach", "coach_id").alias("entity_id"), pl.col("name"))]
        )
    return pl.concat(parts), names.unique("entity_id")


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
    schedule_seasons: tuple[int, ...] = (),
    schedule_cache: Path = Path("data/schedule_cache"),
    seasons: tuple[int, ...] = (),
) -> dict[str, Any]:
    notes: list[str] = []
    guard_holdout(max_season, seasons, schedule_seasons)
    games = allowed_games(src, max_season, since, seasons)
    over = src.execute("SELECT game_id, season FROM games").fetchall()
    excluded = {str(g) for g, s in over if not season_ok(s, max_season, seasons)}
    if seasons:
        notes.append(f"explicit seasons {sorted(seasons)} replace max_season {max_season}")
    tips = build_game_tipoff(schedule_dir)

    parts = [tips_facts(games, tips), team_facts(games)]
    st, n_bad = status_facts(src, games)
    parts.append(st)
    if n_bad:
        notes.append(f"{n_bad} report rows dropped: report time falls in a DST gap")
    if lcon is not None:
        parts += [
            lineup_facts(lcon, excluded, since, max_season, seasons),
            lineup_tip_facts(lcon, excluded, since, max_season, seasons),
        ]
    sf, n_skip = static_facts(src, seasons)
    parts.append(sf)
    notes.append(f"{n_skip} players_static rows have no first_season: no static facts for them")
    cf, coach_names = coach_facts(src, max_season, seasons)
    parts.append(cf)
    mf, n_no_tip = minutes_facts(src, games, tips)
    parts.append(mf)
    notes.append(f"{n_no_tip} games have no real tip: no played_minutes facts for them")

    have = set(games["game_id"].to_list())
    have_tip = have & set(tips["game_id"].to_list())
    have_subj = {f"game:{g}" for g in have}
    for season in schedule_seasons:
        if not seasons and season <= max_season:
            notes.append(f"schedule season {season} <= max_season {max_season}: already built")
            continue
        sched, origin = load_schedule_games(season, schedule_cache, schedule_dir)
        sched = [
            g for g in sched if g.game_id not in have_tip and game_id_season(g.game_id) == season
        ]
        sf_ = schedule_only_facts(sched)
        # a game already in ``games`` has its teams from there; it only lacks a real tip
        sf_ = sf_.filter(
            ~(
                pl.col("predicate").is_in(["home_team", "away_team"])
                & pl.col("subject").is_in(list(have_subj))
            )
        )
        parts.append(sf_)
        notes.append(f"schedule-only season {season}: {len(sched)} games from {origin}")

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
    schedule_seasons: tuple[int, ...] = (),
    seasons: tuple[int, ...] = (),
) -> dict[str, Any]:
    src = duckdb.connect(source, read_only=True)
    lcon = duckdb.connect(lineups, read_only=True) if Path(lineups).exists() else None
    try:
        return build(
            src,
            lcon,
            Path(out),
            since=since,
            max_season=max_season,
            schedule_seasons=schedule_seasons,
            seasons=seasons,
        )
    finally:
        src.close()
        if lcon is not None:
            lcon.close()
