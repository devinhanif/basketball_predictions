"""The official pre-game injury report, as knowable before tip-off.

The one piece of information that has beaten every architecture idea in this project
(CLAUDE.md, "What the data has taught us"). Production reads it through this module:
``rung0_injury_elo`` (value of rotation players OUT), ``props_context_residual`` (stats vacated
by OUT teammates) and the daily pretip run (``serve_pretip_flagged``). The leakage rule lives
in exactly one place, :func:`usable_report_rows`: a report row is usable for a game iff it was
stamped at or before tip-off minus the lead.

Moved verbatim from ``nba.sim.usage_redistribution`` (research, usage-redistribution
experiment) on 2026-10-09 so that no production module imports from ``nba.sim``; that module
re-exports these names for the research code that still uses them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

import duckdb
import polars as pl

#: ``player_availability.source`` of every official-report row (the live puller
#: and the historical backfill both write this value; the backfill's own
#: ``ingest_log`` key is a different string and never lands in this column).
OFFICIAL_REPORT_SOURCE = "nba_official_report"

_DUCKDB_TO_POLARS: dict[str, type[pl.DataType]] = {
    "BIGINT": pl.Int64,
    "INTEGER": pl.Int64,
    "SMALLINT": pl.Int64,
    "HUGEINT": pl.Int64,
    "DOUBLE": pl.Float64,
    "FLOAT": pl.Float64,
    "DATE": pl.Date,
    "BOOLEAN": pl.Boolean,
    "VARCHAR": pl.Utf8,
}


def _query_to_polars(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None
) -> pl.DataFrame:
    """Run ``sql`` and materialize the result as a polars DataFrame (no pyarrow)."""
    con.execute(sql, params or [])
    columns = [d[0] for d in con.description]
    duckdb_types = [str(d[1]) for d in con.description]
    schema = {
        name: _DUCKDB_TO_POLARS.get(dt, pl.Float64)
        for name, dt in zip(columns, duckdb_types, strict=True)
    }
    rows = con.fetchall()
    if not rows:
        return pl.DataFrame(schema={c: schema[c] for c in columns})
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass(frozen=True)
class ReportTriggerConfig:
    """Which official-report rows may trigger 2A/2B, and by when.

    ``games`` carries a DATE only (no tip-off time), so tip-off is a PROXY:
    ``game_date`` at ``tipoff_hour_et`` (naive, same clock as the report
    ``as_of``). A report row is usable for a game iff
    ``as_of <= proxy_tip - lead_minutes`` (hence strictly before tip). Rows
    stamped later are ignored, never used. Default 19:00 ET puts the cutoff
    at 18:00, which admits the 17:45 backfill anchor and excludes anything
    after; matinee tips are a documented limitation (lower ``tipoff_hour_et``
    for a conservative sensitivity run).
    """

    statuses: tuple[str, ...] = ("out",)
    tipoff_hour_et: float = 19.0
    lead_minutes: int = 60
    sources: tuple[str, ...] = (OFFICIAL_REPORT_SOURCE,)
    table: str = "player_availability"
    #: "proxy19" (default, production): tip = game_date at ``tipoff_hour_et``.
    #: "real": tip = scheduled tip-off (``tip_et`` column on the rows, attached
    #: via ``nba.features.game_tipoff.attach_real_tips``); games whose real tip
    #: is missing fall back to the proxy.
    tip_source: str = "proxy19"

    @property
    def cutoff_minutes_after_midnight(self) -> float:
        return self.tipoff_hour_et * 60.0 - float(self.lead_minutes)


def load_report_rows(
    con: duckdb.DuckDBPyConnection,
    config: ReportTriggerConfig,
    game_ids: list[str] | None = None,
) -> pl.DataFrame:
    """All official-report rows attached to a ``games`` row, with its date.

    Columns: ``game_id, player_id, status (lower-cased), as_of, game_date``.
    Rows with NULL ``game_id`` (unresolved matchup) are excluded here and
    counted by the caller's coverage diagnostics. Applies NO time filter;
    that is :func:`latest_pretip_flagged`'s job so the leakage rule lives in
    exactly one place.
    """
    placeholders = ", ".join("?" for _ in config.sources)
    sql = (
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
        "CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date "
        f"FROM {config.table} a JOIN games g ON g.game_id = a.game_id "
        f"WHERE a.source IN ({placeholders}) AND a.player_id IS NOT NULL"
    )
    df = _query_to_polars(con, sql, list(config.sources))
    df = df.with_columns(pl.col("as_of").cast(pl.Datetime("us")))
    if game_ids is not None:
        df = df.filter(pl.col("game_id").is_in(game_ids))
    return df


def usable_report_rows(rows: pl.DataFrame, config: ReportTriggerConfig) -> pl.DataFrame:
    """Rows with ``as_of <= tip - lead`` (the leakage rule).

    ``tip`` is the proxy (``game_date + tipoff_hour_et``) unless
    ``config.tip_source == "real"``, in which case it is the row's ``tip_et``
    (proxy where null)."""
    if config.tip_source not in ("proxy19", "real"):
        raise ValueError(f"unknown tip_source {config.tip_source!r}")
    if rows.height == 0:
        return rows
    if config.tip_source == "real":
        if "tip_et" not in rows.columns:
            raise ValueError("tip_source='real' needs a tip_et column (attach_real_tips)")
        proxy = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(
            minutes=int(config.tipoff_hour_et * 60.0)
        )
        cut = pl.coalesce(pl.col("tip_et"), proxy) - pl.duration(minutes=int(config.lead_minutes))
        return rows.filter(pl.col("as_of") <= cut)
    cutoff = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(
        minutes=int(config.cutoff_minutes_after_midnight)
    )
    return rows.filter(pl.col("as_of") <= cutoff)


def latest_pretip_flagged(
    rows: pl.DataFrame, config: ReportTriggerConfig
) -> tuple[dict[str, set[int]], dict[str, dt.datetime]]:
    """Per game: players whose status in the LATEST usable report is flagged.

    "Latest usable report" = the greatest ``as_of`` among that game's rows that
    pass :func:`usable_report_rows`; only rows of THAT snapshot count, so a
    player flagged OUT in an earlier report but absent/upgraded in the latest
    one is not out. Games with no usable row are absent from both returned
    dicts (no report, no trigger; never imputed). The second dict maps game
    to the snapshot ``as_of`` actually used.
    """
    usable = usable_report_rows(rows, config)
    if usable.height == 0:
        return {}, {}
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_latest"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_latest"))
    flagged: dict[str, set[int]] = {gid: set() for gid in latest["game_id"].to_list()}
    wanted = {s.lower() for s in config.statuses}
    for gid, pid, status in snap.select(["game_id", "player_id", "status"]).iter_rows():
        if status in wanted:
            flagged[str(gid)].add(int(pid))
    used = {str(g): t for g, t in latest.select(["game_id", "_latest"]).iter_rows()}
    return flagged, used


def serve_pretip_flagged(
    rows: pl.DataFrame,
    tips_et: dict[str, dt.datetime],
    now_et: dt.datetime,
    config: ReportTriggerConfig,
    max_age_hours: float | None = None,
) -> tuple[dict[str, set[int]], dict[str, dt.datetime]]:
    """Serving-time twin of the training rule, built ON :func:`latest_pretip_flagged`.

    Per game: the latest snapshot among THAT game's own rows with
    ``as_of <= min(now, real tip - lead)`` (all naive US-Eastern), optionally
    ``<= max_age_hours`` older than that cutoff. ``tips_et`` maps game_id to
    its real tip (naive ET); a game without a tip is absent (never proxied).
    A game with no qualifying snapshot is absent from both dicts (has_report=0,
    never "nobody out"). Training uses the same function with ``tip_et``
    attached by ``attach_real_tips``, ``now`` unbounded and no age cap."""
    if rows.height == 0:
        return {}, {}
    cfg = replace(config, tip_source="real")
    keyed = rows.filter(
        pl.col("game_id").is_in(list(tips_et)) & (pl.col("as_of") <= pl.lit(now_et))
    ).with_columns(
        pl.col("game_id")
        .replace_strict(tips_et, return_dtype=pl.Datetime("us"), default=None)
        .alias("tip_et")
    )
    flagged, used = latest_pretip_flagged(keyed, cfg)
    if max_age_hours is not None:
        lead = dt.timedelta(minutes=int(cfg.lead_minutes))
        age = dt.timedelta(hours=max_age_hours)
        for gid in [g for g, t in used.items() if t < min(now_et, tips_et[g] - lead) - age]:
            used.pop(gid)
            flagged.pop(gid)
    return flagged, used


def prior_minutes_state(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Per (player, game played): cumulative mean minutes and games INCLUDING
    that game, plus the team, for as-of lookups (query with a date strictly
    after the game). Only games with ``minutes > 0`` count as played."""
    df = _query_to_polars(
        con,
        "SELECT s.player_id, s.team_id, g.game_date, s.minutes FROM player_game_stats s "
        "JOIN games g ON g.game_id = s.game_id WHERE COALESCE(s.minutes, 0) > 0",
    )
    df = df.sort(["player_id", "game_date"]).with_columns(
        (
            pl.col("minutes").cum_sum().over("player_id")
            / pl.col("minutes").cum_count().over("player_id")
        ).alias("avg_min_after"),
        pl.col("minutes").cum_count().over("player_id").alias("n_after"),
    )
    return df.select(["player_id", "team_id", "game_date", "avg_min_after", "n_after"])


def rotation_flagged_by_team(
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[dt.date, int, int]],
    state: pl.DataFrame,
    rotation_min_avg: float = 20.0,
    rotation_min_games: int = 5,
) -> dict[tuple[str, int], set[int]]:
    """Keep flagged players who are rotation players AS OF the game, keyed
    by ``(game_id, team_id)``.

    Rotation = mean minutes over games played STRICTLY BEFORE the game date
    >= ``rotation_min_avg`` with >= ``rotation_min_games`` such games (so a
    return-from-injury player keeps his pre-injury average). The player's
    team is his team in that last prior game; players whose team is not one
    of the game's two teams are dropped.
    """
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    if not recs or state.height == 0:
        return {}
    left = pl.DataFrame(
        recs, schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date}, orient="row"
    ).with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key"))
    right = state.rename({"game_date": "_prior_date"}).sort("_prior_date")
    joined = left.sort("_key").join_asof(
        right, left_on="_key", right_on="_prior_date", by="player_id", strategy="backward"
    )
    joined = joined.filter(
        (pl.col("avg_min_after") >= rotation_min_avg) & (pl.col("n_after") >= rotation_min_games)
    )
    out: dict[tuple[str, int], set[int]] = {}
    for gid, pid, team in joined.select(["game_id", "player_id", "team_id"]).iter_rows():
        _d, home, away = game_info[str(gid)]
        if int(team) in (home, away):
            out.setdefault((str(gid), int(team)), set()).add(int(pid))
    return out


__all__ = [
    "OFFICIAL_REPORT_SOURCE",
    "ReportTriggerConfig",
    "latest_pretip_flagged",
    "load_report_rows",
    "prior_minutes_state",
    "rotation_flagged_by_team",
    "serve_pretip_flagged",
    "usable_report_rows",
]
