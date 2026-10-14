"""Response shapes. Every number here is read from, or computed from, a fixture field."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Stat = str


class Team(BaseModel):
    id: int
    abbr: str
    city: str
    name: str


class WinProbability(BaseModel):
    model_home: float | None
    model_name: str
    model_stamp: str | None
    market_home: float | None
    market_name: str
    market_snapshot_utc: str | None


class PropView(BaseModel):
    prop_id: str
    stat: Stat
    line: float
    # "posted": a sportsbook line. "reference": the middle of the model's own forecast, used when
    # the data holds no sportsbook line (the nightly export); then there are no prices.
    line_source: Literal["posted", "reference"] = "posted"
    over_price: int | None
    under_price: int | None
    line_snapshot_utc: str | None
    forecast_mean: float | None
    q: list[float]
    taus: list[float]
    model_p_over: float
    model_p_under: float
    model_p_push: float
    extrapolated: bool
    market_p_over: float | None
    forecast_stamp: str


class PlayerView(BaseModel):
    player_id: int
    name: str
    side: str
    props: dict[Stat, PropView]


class GameSummary(BaseModel):
    game_id: str
    date: str
    tip_utc: str
    arena: str | None
    home: Team
    away: Team
    win_probability: WinProbability
    key_props: list[dict[str, Any]]


class SlateView(BaseModel):
    date: str
    label: str
    clock_utc: str
    mode: str
    counts: dict[str, Any]
    games: list[GameSummary]


class GameView(BaseModel):
    game_id: str
    date: str
    tip_utc: str
    arena: str | None
    home: Team
    away: Team
    win_probability: WinProbability
    clock_utc: str
    final: bool = True  # False until the game is over (an export holds tonight's games too)
    players: list[PlayerView]
    notes: list[str]


class ProbAtLine(BaseModel):
    game_id: str
    player_id: int
    stat: Stat
    line: float
    p_over: float
    p_under: float
    p_push: float
    extrapolated: bool
    method: str
    taus: list[float]
    q: list[float]
    # The posted line for this prop and, only when `line` is exactly that line, its stored prices.
    # For any other line there is no stored price, and none is made up.
    main_line: float
    is_main_line: bool
    over_price: int | None
    under_price: int | None


class SwipeCard(BaseModel):
    prop_id: str
    game_id: str
    player_id: int
    player: str
    team: str
    opponent: str
    stat: Stat
    line: float
    over_price: int
    under_price: int
    tip_utc: str


class SwipeDeck(BaseModel):
    date: str
    clock_utc: str
    seed: int
    cards: list[SwipeCard]
    note: str
