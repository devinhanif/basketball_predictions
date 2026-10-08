"""As-of game-context features: national TV, playoff positioning, tanking.

Travel is implemented in :mod:`nba.features.team_features`
(``travel_miles_asof``) since it slots naturally into that module's
per-team-game SQL (it needs the same ``LAG(...) OVER (PARTITION BY
team_id ORDER BY game_date, game_id)`` machinery already used for
``rest_days``). Everything else -- national TV, as-of standings/seeding,
and the tanking-incentive score -- lives here and is merged into
:func:`nba.features.team_features.build_matchup_features`'s output.

No-leakage discipline, same as ``team_features.py``:
* national TV is a known-in-advance schedule fact (CLAUDE.md: "Known in
  advance (schedule) so it's leakage-safe to use at prediction time");
  it does not depend on any rolling window at all.
* standings/seeding/tanking are built from games with a strictly earlier
  ``(game_date, game_id)`` than the target row. The as-of boundary for
  *other* conference teams is enforced by shifting the lookup date back
  one day before an as-of (``join_asof``, ``strategy="backward"``) join,
  so a same-calendar-date game played by another team can never leak into
  the target row's seeding snapshot -- see
  :func:`_standings_asof_for_targets` docstring for the exact mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import yaml

from nba.features.team_features import build_team_game_features

#: Static team_id -> conference mapping (CLAUDE.md asks for this to be
#: documented). Conferences are a fixed league alignment, not derived from
#: arena geography, even though they mostly correlate with it. Keyed by
#: the same franchise ``team_id`` range as ``nba.ingest.arenas.ARENA_COORDS``.
TEAM_CONFERENCE: dict[int, str] = {
    1610612737: "E",  # Atlanta Hawks
    1610612738: "E",  # Boston Celtics
    1610612739: "E",  # Cleveland Cavaliers
    1610612740: "W",  # New Orleans Pelicans
    1610612741: "E",  # Chicago Bulls
    1610612742: "W",  # Dallas Mavericks
    1610612743: "W",  # Denver Nuggets
    1610612744: "W",  # Golden State Warriors
    1610612745: "W",  # Houston Rockets
    1610612746: "W",  # LA Clippers
    1610612747: "W",  # LA Lakers
    1610612748: "E",  # Miami Heat
    1610612749: "E",  # Milwaukee Bucks
    1610612750: "W",  # Minnesota Timberwolves
    1610612751: "E",  # Brooklyn Nets
    1610612752: "E",  # New York Knicks
    1610612753: "E",  # Orlando Magic
    1610612754: "E",  # Indiana Pacers
    1610612755: "E",  # Philadelphia 76ers
    1610612756: "W",  # Phoenix Suns
    1610612757: "W",  # Portland Trail Blazers
    1610612758: "W",  # Sacramento Kings
    1610612759: "W",  # San Antonio Spurs
    1610612760: "W",  # Oklahoma City Thunder
    1610612761: "E",  # Toronto Raptors
    1610612762: "W",  # Utah Jazz
    1610612763: "W",  # Memphis Grizzlies
    1610612764: "E",  # Washington Wizards
    1610612765: "E",  # Detroit Pistons
    1610612766: "E",  # Charlotte Hornets
}

#: League-average fallback used when a team (or the whole conference) has
#: no as-of games yet -- consistent with ``team_features.FEATURE_DEFAULTS``.
DEFAULT_WIN_PCT = 0.5


@dataclass
class GameContextConfig:
    """Tunable knobs for standings/tanking features -- see
    ``configs/game_context_default.yaml`` for the documented defaults."""

    playoff_seed_cutoff: int = 6
    playin_seed_cutoff: int = 10
    tanking_games_into_season_threshold: int = 60
    tanking_bad_win_pct_threshold: float = 0.40
    travel_default_miles: float = 0.0
    seed: int = 0


def load_game_context_config(path: str | Path) -> GameContextConfig:
    """Load a :class:`GameContextConfig` from YAML. Unknown keys fail loudly."""
    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return GameContextConfig(**raw)


def _conference_lookup() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "team_id": list(TEAM_CONFERENCE.keys()),
            "conference": list(TEAM_CONFERENCE.values()),
        }
    ).with_columns(pl.col("team_id").cast(pl.Int64))


def build_national_tv_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per ``game_id``: ``is_national_tv`` (schedule fact, known pre-game).

    True iff ``games.national_tv`` is non-null/non-empty (same rule as
    ``nba.ingest.national_tv.is_national``, re-implemented here as a plain
    SQL predicate so this module has no runtime dependency on the ingest
    package).
    """
    rows = con.execute(
        "SELECT game_id, (national_tv IS NOT NULL AND national_tv != '') AS is_national_tv "
        "FROM games"
    ).fetchall()
    return pl.DataFrame(
        rows, schema={"game_id": pl.Utf8, "is_national_tv": pl.Boolean}, orient="row"
    )


def _standings_events(team_feats: pl.DataFrame) -> pl.DataFrame:
    """One row per (team, game): exact cumulative W-L *inclusive* of that game.

    Inclusive (not strictly-prior) by design -- these rows are only ever
    read back through an as-of (``join_asof``) lookup from a *different,
    later* target date in :func:`_standings_asof_for_targets`, which is
    where the strictly-prior boundary is actually enforced.
    """
    df = team_feats.select(
        ["team_id", "game_id", "game_date", "season", "team_pts", "opp_pts"]
    ).join(_conference_lookup(), on="team_id", how="left")
    df = df.with_columns(won=(pl.col("team_pts") > pl.col("opp_pts")).cast(pl.Int64))
    df = df.sort(["team_id", "game_date", "game_id"])
    df = df.with_columns(
        wins_cum=pl.col("won").cum_sum().over("team_id"),
        gp_cum=pl.col("won").cum_count().over("team_id"),
    )
    return df.select(
        ["team_id", "conference", "season", "game_id", "game_date", "wins_cum", "gp_cum"]
    )


def _dense_conference_timeline(events: pl.DataFrame) -> pl.DataFrame:
    """Forward-fill every team's cumulative record onto every date any team
    in its conference/season played (a dense "league calendar" grid).

    Ranking requires comparing *all* conference teams' current records on
    the same calendar date -- but each team only has an ``events`` row on
    the dates *it itself* played. A team that last played three days ago
    still has a current record today; this densifies the sparse per-team
    event rows into one row per (conference, season, calendar date, team)
    via a per-team backward ``join_asof`` onto the full conference/season
    date index, so :func:`_rank_standings_events` ranks the *whole* league
    on every date, not just the handful of teams who happened to play that
    exact day.
    """
    out_frames: list[pl.DataFrame] = []
    for (conference, season), conf_events in events.group_by(
        ["conference", "season"], maintain_order=True
    ):
        date_index = conf_events.select("game_date").unique().sort("game_date")
        for team_id in conf_events.select("team_id").unique().to_series().to_list():
            team_events = (
                conf_events.filter(pl.col("team_id") == team_id)
                .select(["game_date", "wins_cum", "gp_cum"])
                .sort("game_date")
            )
            filled = date_index.join_asof(team_events, on="game_date", strategy="backward")
            filled = filled.with_columns(
                team_id=pl.lit(team_id),
                conference=pl.lit(conference),
                season=pl.lit(season),
                wins_cum=pl.col("wins_cum").fill_null(0),
                gp_cum=pl.col("gp_cum").fill_null(0),
            )
            out_frames.append(filled)
    if not out_frames:
        return pl.DataFrame(
            schema={
                "game_date": pl.Date,
                "team_id": pl.Int64,
                "conference": pl.Utf8,
                "season": pl.Int64,
                "wins_cum": pl.Int64,
                "gp_cum": pl.UInt32,
            }
        )
    return pl.concat(out_frames, how="diagonal_relaxed")


def _rank_standings_events(dense_events: pl.DataFrame) -> pl.DataFrame:
    """Add ``win_pct`` and a deterministic per-(conference, season, date) rank.

    ``dense_events`` must already be densified by
    :func:`_dense_conference_timeline` so every conference team has a row
    on every relevant date -- otherwise the rank partition silently
    shrinks to whichever teams happened to play that exact day. Rank 1 =
    best record. Ties are broken by more wins, then lower ``team_id``
    (arbitrary but deterministic, matters only in the all-0.5-win-pct,
    zero-games-played-yet case).
    """
    df = dense_events.with_columns(
        win_pct=pl.when(pl.col("gp_cum") > 0)
        .then(pl.col("wins_cum") / pl.col("gp_cum"))
        .otherwise(pl.lit(DEFAULT_WIN_PCT))
    )
    df = df.sort(
        ["conference", "season", "game_date", "win_pct", "wins_cum", "team_id"],
        descending=[False, False, False, True, True, False],
    )
    df = df.with_columns(
        conf_rank=pl.int_range(1, pl.len() + 1).over(["conference", "season", "game_date"])
    )
    return df


def _standings_asof_for_targets(team_feats: pl.DataFrame) -> pl.DataFrame:
    """For every (team, game) row in ``team_feats``, the team's own
    conference rank and win_pct using *strictly earlier* games only.

    Mechanics (see module docstring): build each team's inclusive
    cumulative-record timeline, rank every (conference, season, game_date)
    snapshot across that conference, then for each target row look up the
    *target team's own* history as of ``game_date - 1 day`` via a backward
    ``join_asof`` -- i.e. the most recent snapshot strictly before the
    target's own game date. Shifting the lookup key back a day (rather
    than asof-matching on the target's own game_date) is what keeps
    same-day league games from leaking into each other, and what keeps the
    target game's own result out of its own seeding.
    """
    events = _standings_events(team_feats)
    dense = _dense_conference_timeline(events)
    ranked = _rank_standings_events(dense)

    targets = team_feats.select(["team_id", "game_id", "game_date", "season"]).with_columns(
        lookup_date=pl.col("game_date") - pl.duration(days=1)
    )

    out_frames: list[pl.DataFrame] = []
    for (team_id, season), team_targets in targets.group_by(
        ["team_id", "season"], maintain_order=True
    ):
        team_history = (
            ranked.filter((pl.col("team_id") == team_id) & (pl.col("season") == season))
            .select(["game_date", "win_pct", "wins_cum", "gp_cum", "conf_rank"])
            .sort("game_date")
        )
        joined = team_targets.sort("lookup_date").join_asof(
            team_history, left_on="lookup_date", right_on="game_date", strategy="backward"
        )
        out_frames.append(joined)

    if not out_frames:
        return pl.DataFrame(
            schema={
                "team_id": pl.Int64,
                "game_id": pl.Utf8,
                "game_date": pl.Date,
                "season": pl.Int64,
                "win_pct_asof": pl.Float64,
                "conf_rank_asof": pl.Int64,
                "gp_cum_asof": pl.Int64,
            }
        )

    merged = pl.concat(out_frames, how="diagonal_relaxed")
    # join_asof's right frame contributes its own "game_date" (the matched
    # history snapshot's date); the left frame's own "game_date" (the
    # target game's date) gets suffixed "_right" since both sides carry
    # that column name -- keep the left (target) one explicitly.
    merged = merged.with_columns(
        win_pct_asof=pl.col("win_pct").fill_null(DEFAULT_WIN_PCT),
        conf_rank_asof=pl.col("conf_rank"),
        gp_cum_asof=pl.col("gp_cum").fill_null(0),
    )
    return merged.select(
        [
            "team_id",
            "game_id",
            "game_date",
            "season",
            "win_pct_asof",
            "conf_rank_asof",
            "gp_cum_asof",
        ]
    )


#: Per-team columns produced by :func:`build_standings_features`, merged
#: home_*/away_* into the matchup frame.
STANDINGS_TEAM_COLUMNS: list[str] = [
    "win_pct_asof",
    "conf_rank_asof",
    "games_into_season",
    "in_playoff_pos",
    "in_playin",
    "tanking_incentive",
]


def build_standings_features(
    con: duckdb.DuckDBPyConnection, config: GameContextConfig | None = None
) -> pl.DataFrame:
    """One row per (game, team): as-of conference seed + tanking incentive.

    ``games_into_season`` and ``win_pct_asof`` are strictly-prior and
    SEASON-SCOPED (``team_feats``'s ``games_played_prior`` and raw
    ``season_win_pct_prior``; both reset to 0 games / 0.5 at each team's
    first game of a season -- see ``team_features.py``'s module docstring),
    so the tanking threshold counts games in the current season only;
    ``conf_rank_asof`` is strictly-prior via the ``join_asof``
    boundary described in :func:`_standings_asof_for_targets`.
    """
    cfg = config or GameContextConfig()
    team_feats = build_team_game_features(con)
    standings = _standings_asof_for_targets(team_feats)

    df = team_feats.select(
        [
            "team_id",
            "game_id",
            "game_date",
            "season",
            "is_home",
            "games_played_prior",
            "season_win_pct_prior",
        ]
    ).join(
        standings.select(["team_id", "game_id", "conf_rank_asof"]),
        on=["team_id", "game_id"],
        how="left",
    )

    df = df.with_columns(
        games_into_season=pl.col("games_played_prior"),
        win_pct_asof=pl.col("season_win_pct_prior"),
        conf_rank_asof=pl.col("conf_rank_asof").fill_null(999),
    )
    df = df.with_columns(
        in_playoff_pos=pl.col("conf_rank_asof") <= cfg.playoff_seed_cutoff,
        in_playin=(pl.col("conf_rank_asof") > cfg.playoff_seed_cutoff)
        & (pl.col("conf_rank_asof") <= cfg.playin_seed_cutoff),
    )
    tanking_flag = (
        (pl.col("games_into_season") >= cfg.tanking_games_into_season_threshold)
        & (pl.col("win_pct_asof") < cfg.tanking_bad_win_pct_threshold)
        & (pl.col("conf_rank_asof") > cfg.playin_seed_cutoff)
    )
    df = df.with_columns(
        tanking_flag=tanking_flag,
        tanking_incentive=pl.when(tanking_flag).then(1.0 - pl.col("win_pct_asof")).otherwise(0.0),
    )
    return df.select(
        [
            "team_id",
            "game_id",
            "game_date",
            "season",
            "is_home",
            *STANDINGS_TEAM_COLUMNS,
            "tanking_flag",
        ]
    )


#: Columns :func:`build_game_context_matchup_features` adds to
#: ``nba.features.team_features.MATCHUP_FEATURE_COLUMNS`` (travel_diff is
#: computed in ``team_features.py`` itself, not here -- it reuses that
#: module's own home/away join, so it is not part of this list).
GAME_CONTEXT_FEATURE_COLUMNS: list[str] = [
    "is_national_tv",
    "conf_rank_diff",
    "win_pct_asof_diff",
    "home_in_playoff_pos",
    "away_in_playoff_pos",
    "home_in_playin",
    "away_in_playin",
    "tanking_incentive_diff",
]


def build_game_context_matchup_features(
    con: duckdb.DuckDBPyConnection, config: GameContextConfig | None = None
) -> pl.DataFrame:
    """One row per ``game_id``: national TV + home/away standings + diffs.

    Meant to be left-joined onto
    ``nba.features.team_features.build_matchup_features``'s output on
    ``game_id`` (that frame already carries ``home_team``/``away_team`` and
    the as-of ``travel_miles_asof`` per side).
    """
    national_tv = build_national_tv_features(con)
    standings = build_standings_features(con, config)
    home_side = standings.filter(pl.col("is_home")).drop(
        ["team_id", "game_date", "season", "is_home"]
    )
    away_side = standings.filter(~pl.col("is_home")).drop(
        ["team_id", "game_date", "season", "is_home"]
    )

    home = home_side.rename({c: f"home_{c}" for c in home_side.columns if c not in ("game_id",)})
    away = away_side.rename({c: f"away_{c}" for c in away_side.columns if c not in ("game_id",)})
    merged = home.join(away, on="game_id", how="inner").join(national_tv, on="game_id", how="left")
    merged = merged.with_columns(
        is_national_tv=pl.col("is_national_tv").fill_null(False),
        conf_rank_diff=pl.col("away_conf_rank_asof") - pl.col("home_conf_rank_asof"),
        win_pct_asof_diff=pl.col("home_win_pct_asof") - pl.col("away_win_pct_asof"),
        tanking_incentive_diff=pl.col("home_tanking_incentive") - pl.col("away_tanking_incentive"),
    )
    return merged
