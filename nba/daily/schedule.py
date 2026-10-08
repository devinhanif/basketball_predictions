"""Forward schedule: which games tip on a date, and exactly when (UTC)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import polars as pl

from nba.ingest.games import KEPT_GAME_TYPE_PREFIXES, MAX_VALID_TEAM_ID, MIN_VALID_TEAM_ID

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScheduledGame:
    game_id: str
    tipoff: datetime  # naive UTC
    home_team: int
    away_team: int

    @property
    def game_date_et(self) -> date:
        return self.tipoff.replace(tzinfo=UTC).astimezone(ET).date()


ScheduleFn = Callable[[str], list[ScheduledGame]]


def parse_schedule_frame(raw: pl.DataFrame) -> list[ScheduledGame]:
    """Normalise nba_api ScheduleLeagueV2 'SeasonGames' columns."""
    out: list[ScheduledGame] = []
    for r in raw.iter_rows(named=True):
        gid = str(r["gameId"])
        if gid[:3] not in KEPT_GAME_TYPE_PREFIXES:
            continue
        home, away = int(r["homeTeam_teamId"]), int(r["awayTeam_teamId"])
        if not (MIN_VALID_TEAM_ID <= home <= MAX_VALID_TEAM_ID):
            continue
        if not (MIN_VALID_TEAM_ID <= away <= MAX_VALID_TEAM_ID):
            continue
        ts = datetime.fromisoformat(str(r["gameDateTimeUTC"]).replace("Z", "+00:00"))
        out.append(ScheduledGame(gid, ts.astimezone(UTC).replace(tzinfo=None), home, away))
    return out


def fetch_schedule_nba_api(season: str) -> list[ScheduledGame]:
    """One nba_api call for the season schedule (lazy import; network)."""
    from nba_api.stats.endpoints import scheduleleaguev2  # lazy import

    frames = scheduleleaguev2.ScheduleLeagueV2(season=season).get_data_frames()
    return parse_schedule_frame(pl.from_pandas(frames[0]))


def slate_for_date(games: list[ScheduledGame], d: date) -> list[ScheduledGame]:
    """Games whose US-Eastern tip-off calendar date is ``d`` (NBA game-date rule)."""
    return sorted((g for g in games if g.game_date_et == d), key=lambda g: (g.tipoff, g.game_id))
