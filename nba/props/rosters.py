"""Pre-tip team rosters for the forward props path (opening-week coverage).

Why: the forward roster is "players in a team's last N games minus injury-report OUTs".
In the offseason that is last spring's roster, so in a replay of the 2024-25 opening week
41% of the players who played had no prediction (movers, rookies, same-team players outside
the last-10 window). The official roster for the season (nba_api ``CommonTeamRoster``, one
call per team) is public before tip-off and is legitimately known at prediction time.

Two sources share one frame schema, ``(team_id, player_id)``:

* ``load_official_rosters`` -- the live path. One call per team, cached on disk PER AS-OF DATE
  (``<root>/<as_of>/<team_id>.parquet``) so a rerun on the same day makes zero requests and a
  later day never reads an earlier day's file. nba_api is imported lazily; the fetcher is
  injectable so tests and replays never touch the network. Callers must route live calls
  through the shared request budget (rate limiter argument, default 0.6 s).
* ``proxy_rosters_from_first_games`` -- the REPLAY proxy. A season-end ``CommonTeamRoster`` is
  NOT a valid pre-tip roster (it contains later trades), so a replay instead takes players who
  appeared for the team in the first ``days`` days of that season. This peeks at who actually
  played after the replay date, so it is an OPTIMISTIC proxy: it cannot contain roster members
  who never play, and it contains players acquired after the replay date. Coverage measured
  with it is an upper bound.

``merge_rosters`` combines an official roster with the recent-games candidates. Nothing here
reads game results on or after the prediction date except the explicitly named replay proxy.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter

DEFAULT_ROSTER_DIR = DEFAULT_DATA_DIR / "rosters"
ROSTER_COLUMNS = ("team_id", "player_id")
_MAX_ATTEMPTS = 3
_CIRCUIT_BREAKER = 2  # consecutive failed teams before the rest are skipped
_RETRY_BACKOFF_S = 5.0

RosterFetcher = Callable[[int, str], pl.DataFrame]


def parse_common_team_roster(raw: pl.DataFrame, team_id: int) -> pl.DataFrame:
    """Normalise the ``CommonTeamRoster`` data frame to ``(team_id, player_id)``.

    Keeps ``exp`` (``'R'`` = rookie) when present so callers can tell no-history players apart.
    Drops rows without a player id and de-duplicates. ``team_id`` comes from the request, not
    the payload (the payload's ``TeamID`` is a string in some nba_api versions)."""
    cols = {c.upper(): c for c in raw.columns}
    if "PLAYER_ID" not in cols:
        raise ValueError(f"roster payload for team {team_id} has no PLAYER_ID column")
    out = raw.select(pl.col(cols["PLAYER_ID"]).cast(pl.Int64, strict=False).alias("player_id"))
    if "EXP" in cols:
        out = out.with_columns(raw[cols["EXP"]].cast(pl.Utf8).alias("exp"))
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("exp"))
    if "PLAYER" in cols:  # display name; lets the injury-report resolver see debutants
        out = out.with_columns(raw[cols["PLAYER"]].cast(pl.Utf8).alias("player_name"))
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("player_name"))
    out = out.filter(pl.col("player_id").is_not_null()).unique(subset="player_id", keep="first")
    return out.with_columns(pl.lit(int(team_id), dtype=pl.Int64).alias("team_id")).select(
        "team_id", "player_id", "exp", "player_name"
    )


def roster_names(roster: pl.DataFrame) -> list[tuple[str, int]]:
    """``(display name, player_id)`` pairs from an official-roster frame (caches written before
    ``player_name`` existed simply yield fewer pairs)."""
    if "player_name" not in roster.columns:
        return []
    sub = roster.filter(pl.col("player_name").is_not_null()).select("player_name", "player_id")
    return [(str(n), int(p)) for n, p in sub.unique().rows()]


def fetch_team_roster_nba_api(team_id: int, season: str) -> pl.DataFrame:
    """One ``CommonTeamRoster`` call (lazy import; network). ``season`` like ``2026-27``."""
    from nba_api.stats.endpoints import commonteamroster  # lazy import

    resp = commonteamroster.CommonTeamRoster(team_id=team_id, season=season)
    return parse_common_team_roster(pl.from_pandas(resp.get_data_frames()[0]), team_id)


def roster_cache_path(root: Path, as_of: date, team_id: int) -> Path:
    return root / as_of.isoformat() / f"{int(team_id)}.parquet"


def load_official_rosters(
    as_of: date,
    season: str,
    team_ids: list[int],
    *,
    cache_root: Path = DEFAULT_ROSTER_DIR,
    fetch: RosterFetcher | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[pl.DataFrame, list[int]]:
    """Official rosters for ``team_ids`` as known on ``as_of``: ``(frame, missing_team_ids)``.

    A team with a cache file for ``as_of`` costs no request. Otherwise the fetcher is called
    (up to 3 attempts with backoff) and the result cached; a team that still fails is reported
    in ``missing_team_ids`` and the run continues without it (the caller falls back to the
    recent-games roster for that team). Never raises for a network failure."""
    fetcher = fetch or fetch_team_roster_nba_api
    nap = sleep or time.sleep
    limiter = rate_limiter or RateLimiter()
    frames: list[pl.DataFrame] = []
    missing: list[int] = []
    consecutive_failures = 0
    for tid in team_ids:
        path = roster_cache_path(cache_root, as_of, tid)
        if path.exists():
            frames.append(pl.read_parquet(path))
            continue
        if consecutive_failures >= _CIRCUIT_BREAKER:  # endpoint is down/blocked: stop asking
            missing.append(int(tid))
            continue
        got: pl.DataFrame | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                limiter.wait()
                got = fetcher(int(tid), season)
                if not got.is_empty():
                    break
            except Exception:
                got = None
            if attempt < _MAX_ATTEMPTS - 1:
                nap(_RETRY_BACKOFF_S * (attempt + 1))
        if got is None or got.is_empty():
            missing.append(int(tid))
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        got.write_parquet(tmp)
        tmp.replace(path)
        frames.append(got)
    if not frames:
        schema = {
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "exp": pl.Utf8,
            "player_name": pl.Utf8,
        }
        return pl.DataFrame(schema=schema), missing
    return pl.concat(frames, how="diagonal_relaxed"), missing


def proxy_rosters_from_first_games(
    con: duckdb.DuckDBPyConnection, season: int, days: int = 14
) -> pl.DataFrame:
    """REPLAY PROXY (optimistic; see module docstring): players who played (minutes > 0) for
    a team in regular-season games within ``days`` days of that season's first game.

    Reads results after any replay date by design; never use it for a live prediction.
    A player who changed teams inside the window is assigned the team of his first appearance."""
    df = con.execute(
        """
        WITH start AS (
            SELECT min(game_date) AS d0 FROM games WHERE season = ? AND game_id LIKE '002%'
        )
        SELECT arg_min(s.team_id, g.game_date) AS team_id, s.player_id
        FROM player_game_stats s JOIN games g USING (game_id), start
        WHERE g.season = ? AND g.game_id LIKE '002%' AND s.minutes > 0
          AND g.game_date < start.d0 + ? * INTERVAL 1 DAY
        GROUP BY s.player_id
        ORDER BY 1, 2
        """,
        [season, season, int(days)],
    ).pl()
    return df.with_columns(
        pl.col("team_id").cast(pl.Int64),
        pl.col("player_id").cast(pl.Int64),
        pl.lit(None, dtype=pl.Utf8).alias("exp"),
    )


def merge_rosters(
    recent: pl.DataFrame,
    official: pl.DataFrame,
    slate_teams: list[int],
    exclude: set[int],
    *,
    drop_unlisted_recent: bool = False,
) -> pl.DataFrame:
    """Forward roster = official roster (slate teams) union recent-games players, minus OUTs.

    Conflict rule: a player listed on ANY official roster belongs to the official team (a
    recent-games row under his old team is dropped, so a mover is never projected for both).
    A recent-games player listed on no official roster is kept (union) unless
    ``drop_unlisted_recent`` and his recent team has an official roster loaded (then he has
    been cut or left; the strict variant). Returns ``(team_id, player_id, source)`` with
    ``source`` in ``official`` / ``recent``."""
    slate = set(int(t) for t in slate_teams)
    off = official.select("team_id", "player_id").unique()
    off_players = set(off["player_id"].to_list())
    loaded_teams = set(off["team_id"].to_list())
    off_slate = off.filter(pl.col("team_id").is_in(sorted(slate))).with_columns(
        pl.lit("official").alias("source")
    )
    rec = (
        recent.select("team_id", "player_id")
        .filter(pl.col("team_id").is_in(sorted(slate)))
        .filter(~pl.col("player_id").is_in(off_players))
    )
    if drop_unlisted_recent:
        rec = rec.filter(~pl.col("team_id").is_in(sorted(loaded_teams)))
    rec = rec.with_columns(pl.lit("recent").alias("source"))
    out = pl.concat([off_slate, rec], how="diagonal_relaxed").with_columns(
        pl.col("team_id").cast(pl.Int64), pl.col("player_id").cast(pl.Int64)
    )
    if exclude:
        out = out.filter(~pl.col("player_id").is_in(sorted(exclude)))
    return out.unique(subset=["player_id"], keep="first", maintain_order=True).sort(
        "team_id", "player_id"
    )


def missing_static_ids(con: duckdb.DuckDBPyConnection, roster: pl.DataFrame) -> list[int]:
    """Rostered players with NO ``players_static`` row (no draft slot -> the rookie minutes
    prior cannot apply and they fall back to the league default). ``players_static`` is only
    pulled for players already seen in a box score, so a debutant is missing until pulled."""
    have = {int(r[0]) for r in con.execute("SELECT player_id FROM players_static").fetchall()}
    ids = {int(p) for p in roster["player_id"].to_list()}
    return sorted(ids - have)
