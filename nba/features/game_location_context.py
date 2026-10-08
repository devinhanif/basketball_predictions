"""Game-location / tip-time / market-size context features (schedule-known, pre-game).

Every field here is a fact published in the schedule before tip-off (tip instant,
arena, neutral-site flag) or a static franchise attribute (altitude, time zone,
metro population from ``configs/team_markets.yaml``). Nothing depends on a game
outcome, so each row is trivially ``as_of``-safe: it is knowable at any time
before the game. The one derived-from-history field, ``days_into_season``, uses
only the schedule's own first regular-season date for that season.

Feature data card (all keyed by ``game_id``):

* ``home_altitude_km``      home arena elevation / 1000 (source: team_markets.yaml).
* ``altitude_diff_km``      home minus away team's *home* elevation (acclimatisation proxy).
* ``home_log_pop`` / ``away_log_pop`` / ``log_pop_gap``  log metro population (Census 2020 MSA,
  StatCan 2021 CMA for Toronto); gap = home - away.
* ``away_bodyclock_hr``     tip time in the *away team's home* time zone, decimal hours (a
  7pm-eastern tip is 16.0 for a Pacific-zone visitor).
* ``tz_shift_east_hr``      home UTC offset minus away UTC offset at tip (positive = the visitor
  is playing east of home; uses home-zone-to-home-zone only, not the actual prior-night city).
* ``west_to_east_early``    1 if tz_shift_east_hr >= 2 and away_bodyclock_hr < 17.
* ``tip_bucket``            local (home-arena) tip: matinee <17h, early [17,19), standard [19,21),
  late >=21.
* ``is_neutral``            schedule ``isNeutral`` flag OR arena state/country differs from the
  home franchise's modal arena state (Paris, Mexico City, Las Vegas). The raw ``isNeutral`` flag
  is unreliable before 2024-25 (False for 2022-24 Paris/Mexico City), hence the second rule.
  Same-state alternates (Spurs in Austin, Clippers in Inglewood) are NOT neutral. Local-time
  features (tip bucket, body-clock) are home-zone approximations and wrong for international
  venues; those are a handful of games.
* ``days_into_season``      days since that season's first regular-season (``002``) tip.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import yaml

SEASONS = ("2022-23", "2023-24", "2024-25")
TIP_BUCKETS = ("matinee", "early", "standard", "late")


def load_team_markets(path: str | Path) -> dict[int, dict[str, object]]:
    """Read ``team_markets.yaml`` and assert the contract (30 teams, required keys)."""
    raw = yaml.safe_load(Path(path).read_text())["teams"]
    teams = {int(k): dict(v) for k, v in raw.items()}
    need = {"abbr", "metro_pop", "altitude_m", "tz", "lat", "lon"}
    assert len(teams) == 30, f"expected 30 franchises, got {len(teams)}"
    for tid, row in teams.items():
        assert need <= set(row), f"team {tid} missing {need - set(row)}"
        assert float(str(row["metro_pop"])) > 0
        ZoneInfo(str(row["tz"]))  # raises on bad zone
    return teams


def fetch_raw_schedules(
    out_dir: str | Path,
    seasons: tuple[str, ...] = SEASONS,
    fetch: Callable[[str], pl.DataFrame] | None = None,
    sleep_s: float = 1.0,
) -> list[Path]:
    """Cache raw ScheduleLeagueV2 'SeasonGames' per season (idempotent; skips cached)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for s in seasons:
        p = out / f"raw_{s}.parquet"
        if not p.exists():
            if fetch is None:
                from nba_api.stats.endpoints import scheduleleaguev2  # lazy; network

                frame = pl.from_pandas(
                    scheduleleaguev2.ScheduleLeagueV2(season=s, timeout=60).get_data_frames()[0]
                )
            else:
                frame = fetch(s)
            frame.write_parquet(p)
            time.sleep(sleep_s)
        paths.append(p)
    return paths


_KEEP = [
    "gameId",
    "gameDateTimeUTC",
    "arenaName",
    "arenaCity",
    "arenaState",
    "gameLabel",
    "gameSubLabel",
    "gameSubtype",
    "isNeutral",
    "homeTeam_teamId",
    "awayTeam_teamId",
]


def build_schedule_table(raw_dir: str | Path) -> pl.DataFrame:
    """Normalise cached raw schedules to one row per NBA team-vs-team game.

    Keeps game-id prefixes 002 (regular), 003 (all-star excluded below by team-id check),
    004 (playoffs), 005 (play-in); preseason (001) is dropped. Asserts ``game_id`` unique.
    """
    frames = [
        pl.read_parquet(p, columns=[c for c in _KEEP])
        for p in sorted(Path(raw_dir).glob("raw_*.parquet"))
    ]
    d = pl.concat(frames, how="vertical_relaxed").rename(
        {"gameId": "game_id", "homeTeam_teamId": "home_team", "awayTeam_teamId": "away_team"}
    )
    d = d.filter(
        pl.col("game_id").str.slice(0, 3).is_in(["002", "004", "005"])
        & pl.col("home_team").is_between(1610612737, 1610612766)
        & pl.col("away_team").is_between(1610612737, 1610612766)
    ).with_columns(
        pl.col("gameDateTimeUTC")
        .str.replace("Z$", "")
        .str.to_datetime("%Y-%m-%dT%H:%M:%S")
        .alias("tip_utc"),
        pl.col("home_team").cast(pl.Int64),
        pl.col("away_team").cast(pl.Int64),
        pl.col("isNeutral").fill_null(False),
    )
    d = d.unique(subset=["game_id"], keep="first").drop("gameDateTimeUTC")
    assert d["game_id"].n_unique() == d.height, "game_id must be unique"
    assert d["tip_utc"].null_count() == 0
    return d.sort("tip_utc", "game_id")


def _local_hours(tip_utc: datetime, tz: str) -> tuple[float, float]:
    """(decimal local hour, UTC offset hours) of a naive-UTC instant in ``tz``."""
    loc = tip_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))
    off = loc.utcoffset()
    assert off is not None
    return loc.hour + loc.minute / 60.0, off.total_seconds() / 3600.0


def bucket_for_hour(h: float) -> str:
    """Local tip-time bucket: matinee <17, early [17,19), standard [19,21), late >=21."""
    if h < 17.0:
        return "matinee"
    if h < 19.0:
        return "early"
    if h < 21.0:
        return "standard"
    return "late"


def build_context_features(
    sched: pl.DataFrame, teams: dict[int, dict[str, object]]
) -> pl.DataFrame:
    """Derive the schedule-known context features, one row per ``game_id``."""
    modal_city = (
        sched.group_by("home_team", "arenaState")
        .len()
        .sort("len", descending=True)
        .group_by("home_team", maintain_order=True)
        .first()
        .select("home_team", pl.col("arenaState").alias("_modal_state"))
    )
    s = sched.join(modal_city, on="home_team", how="left")
    first_reg = (
        s.filter(pl.col("game_id").str.starts_with("002"))
        .with_columns(pl.col("tip_utc").dt.date().alias("_d"))
        .with_columns(
            pl.when(pl.col("tip_utc").dt.month() >= 8)
            .then(pl.col("tip_utc").dt.year())
            .otherwise(pl.col("tip_utc").dt.year() - 1)
            .alias("_sy")
        )
        .group_by("_sy")
        .agg(pl.col("_d").min().alias("_start"))
    )
    rows = []
    for r in s.iter_rows(named=True):
        h, a = teams[r["home_team"]], teams[r["away_team"]]
        tip = r["tip_utc"]
        home_hr, home_off = _local_hours(tip, str(h["tz"]))
        away_hr, away_off = _local_hours(tip, str(a["tz"]))
        shift = home_off - away_off
        hp, ap = float(str(h["metro_pop"])), float(str(a["metro_pop"]))
        rows.append(
            {
                "game_id": r["game_id"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "tip_utc": tip,
                "home_local_hr": home_hr,
                "home_altitude_km": float(str(h["altitude_m"])) / 1000.0,
                "altitude_diff_km": (float(str(h["altitude_m"])) - float(str(a["altitude_m"])))
                / 1000.0,
                "home_log_pop": math.log(hp),
                "away_log_pop": math.log(ap),
                "log_pop_gap": math.log(hp) - math.log(ap),
                "away_bodyclock_hr": away_hr,
                "tz_shift_east_hr": shift,
                "west_to_east_early": int(shift >= 2 and away_hr < 17.0),
                "tip_bucket": bucket_for_hour(home_hr),
                "is_neutral": int(bool(r["isNeutral"]) or r["arenaState"] != r["_modal_state"]),
                "arena_city": r["arenaCity"],
                "arena_state": r["arenaState"],
                "game_subtype": r["gameSubtype"],
            }
        )
    f = pl.DataFrame(rows)
    f = (
        f.with_columns(
            pl.when(pl.col("tip_utc").dt.month() >= 8)
            .then(pl.col("tip_utc").dt.year())
            .otherwise(pl.col("tip_utc").dt.year() - 1)
            .alias("_sy")
        )
        .join(first_reg, on="_sy", how="left")
        .with_columns(
            (pl.col("tip_utc").dt.date() - pl.col("_start"))
            .dt.total_days()
            .alias("days_into_season")
        )
        .drop("_sy", "_start")
    )
    assert f["game_id"].n_unique() == f.height
    return f
