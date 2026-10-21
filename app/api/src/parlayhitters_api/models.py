"""Response shapes. Every number here is read from, or computed from, a fixture field."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .role import PlayerRole

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


class RungLine(BaseModel):
    line: float
    p_over: float = Field(ge=0, le=1)


class RungCal(BaseModel):
    """docs/contracts/rung_cal_export_v1.md: the frozen CAL-rung map applied to the model's own
    P(over) at each half-point line from the 5th to the 95th percentile. No price, no EV."""

    book: str
    main_line: float
    price_at: str | None = None
    map_version: str
    map_sha8: str
    lines: list[RungLine]


class LineCal(BaseModel):
    """docs/contracts/line_cal_export_v1.md: LINE_CALIBRATION (T493) applied to the model's P(over)
    at the DraftKings main line only. The under side is 1 - p_over_cal. No price, no EV."""

    book: str
    main_line: float
    price_at: str | None = None
    p_over_raw: float = Field(ge=0, le=1)
    p_over_cal: float = Field(ge=0, le=1)
    map: str
    map_sha8: str
    fit_through: str | None = None


class PropView(BaseModel):
    prop_id: str
    stat: Stat
    line: float
    # "posted": a sportsbook line. "reference": the middle of the model's own forecast, used when
    # the data holds no sportsbook line (the nightly export); then there are no prices.
    line_source: Literal["posted", "reference"] = "posted"
    over_price: int | None
    under_price: int | None
    # The one book whose over and under are shown (None when the prices are absent).
    price_book: str | None = None
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
    # Which model the forecast came from, in plain words, and the chance to play behind it.
    model_source: str = "fixture"
    model_label: str = ""
    model_detail: str | None = None
    p_play: float | None = None
    p_play_label: str | None = None
    # Optional export fields (contract rung_cal_export_v1): the newest pre-tip two-sided book main
    # line, and the rung-calibrated P(over) per line. Both null when the export holds none.
    book_main_line: float | None = None
    rung_cal: RungCal | None = None
    # Optional export fields (contract line_cal_export_v1); absent in older exports, read as null.
    line_cal: LineCal | None = None
    line_cal_null_reason: str | None = None


class PlayerView(BaseModel):
    player_id: int
    name: str
    side: str
    props: dict[Stat, PropView]
    # Minutes steadiness, on/off and without-teammate numbers; None when no history is loaded.
    role: PlayerRole | None = None


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
    book: str | None = None
    tip_utc: str


class SwipeDeck(BaseModel):
    date: str
    clock_utc: str
    seed: int
    cards: list[SwipeCard]
    note: str


class Dist(BaseModel):
    """The model's stored distribution as P(Y >= k) for k = 0, 1, 2, ... (integer counts), so
    P(over k.5) = p_ge[k+1] with no interpolation. `exact` means the full-support table; `quantile`
    means the 19-point quantile grid read by interpolation (fixtures), which is rougher."""

    method: Literal["exact", "quantile"]
    n: int | None
    p_ge: list[float]
    line_min: float
    line_max: float
    mean: float | None


class HitTen(BaseModel):
    n: int
    hits: int
    pushes: int
    line: float


class MateOut(BaseModel):
    name: str
    status: str


class GameDot(BaseModel):
    game_id: str
    date: str
    opp: str
    home: bool | None
    value: int
    linkable: bool


class CardStats(BaseModel):
    """Plain as-of-before-tip facts for one prop: nothing from tonight's game."""

    as_of_date: str
    prior_games: int
    last5: list[int]
    last10: list[int]
    last10_games: list[GameDot]
    avg5: float | None
    avg10: float | None
    avg_season: float | None
    n_season: int
    min5: float | None
    min_season: float | None
    hit10_over: HitTen
    teammates_out: list[MateOut]
    teammates_known: bool


class CardView(BaseModel):
    prop_id: str
    game_id: str
    player_id: int
    player: str
    team: str
    opponent: str
    home: bool
    stat: Stat
    line: float
    line_source: Literal["posted", "reference"]
    over_price: int | None
    under_price: int | None
    price_book: str | None
    book_main_line: float | None
    rung_cal: RungCal | None
    line_cal: LineCal | None = None
    line_cal_null_reason: str | None = None
    dist: Dist
    stats: CardStats | None
    forecast_stamp: str
    model_label: str
    model_source: str = ""
    tip_utc: str
