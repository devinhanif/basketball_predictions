"""Response shapes of the leg breakdown (see breakdown.py for the rules behind every field)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .dissect.types import Dissection
from .role_stats import OnOff, Per36, Steadiness
from .wowy_shrink import Beyond

Json = dict[str, Any]
Side = Literal["over", "under"]
State = Literal["hit", "miss", "push"]


class Tally(BaseModel):
    n: int
    hits: int
    misses: int
    pushes: int
    rate: float | None
    ci_lo: float | None
    ci_hi: float | None
    avg: float | None
    avg_minutes: float | None
    small: bool


class Chip(BaseModel):
    game_id: str
    date: str
    opp: str
    home: bool | None  # None when the export holds no schedule row for that older game
    value: int
    minutes: float
    starter: bool
    state: State
    # True when the data holds that game's page, so the app may link to it (set by the route).
    linkable: bool = False


class Split(BaseModel):
    key: str
    label: str
    tonight: bool
    this_season: Tally
    since_last_season: Tally


class TeammateOut(BaseModel):
    player_id: int
    name: str
    status: str
    reason: str | None
    avg_minutes: float
    without: Tally
    with_: Tally
    # His minutes and per-36 points, rebounds and assists (assists from the box score) in the
    # same two sets of games, whatever the stat on screen.
    per36_without: Per36
    per36_with: Per36
    # The headline; the tallies and per-36 numbers above are the raw history, not the lead.
    beyond: Beyond
    note: str


class Teammates(BaseModel):
    report_as_of_utc: str | None
    player_status: str | None
    player_status_reason: str | None
    items: list[TeammateOut]
    omitted_unnamed: int
    note: str


class OppAllowed(BaseModel):
    opp: str
    position: str
    position_noun: str
    avg_allowed: float
    rank: int
    of: int
    n_games: int
    league_avg: float
    note: str


class Model(BaseModel):
    available: bool
    p_hit: float | None
    p_over: float | None
    p_under: float | None
    p_push: float | None
    mean: float | None
    typical: float | None
    extrapolated: bool
    # calibrated (line_cal, rung_cal) or the model's own number with no calibrated value (model)
    p_source: str = "model"
    # The prop has a calibrated number elsewhere but none at this line: no chance is given here.
    no_cal_here: bool = False
    stamp: str | None
    # Which model the numbers came from, in plain words (see model_source).
    source: str
    source_label: str
    say: str
    reason: str


class Role(BaseModel):
    n: int
    avg_minutes: float | None
    starts: int
    minutes_forecast: float | None
    p_play: float | None
    p_play_label: str | None
    say: str


class BookRow(BaseModel):
    """One book's stored quote at the line on screen. A price that is not usable (a placeholder
    with no bid behind it) is never given as a number: it is None and `*_unusable` is set."""

    book: str
    over_price: int | None
    under_price: int | None
    over_unusable: bool
    under_unusable: bool
    alternate: bool  # the quote came from the alternate-line ladder
    minutes_before_tip: float | None


class Prices(BaseModel):
    posted_line: float | None
    over_price: int | None
    under_price: int | None
    on_posted_line: bool
    snapshot_utc: str | None
    say: str
    books: list[BookRow] = []
    # The book's chance of an over at the line on screen, its margin taken out (multiplicative), and
    # whose price it is. Null unless one book quoted both sides (a mixed pair is not de-vigged).
    market_p_over: float | None = None
    market_book: str | None = None


class LadderRow(BaseModel):
    line: float
    l10_over: int
    l10_under: int
    l10_push: int
    l5_over: int
    l5_under: int
    l5_push: int
    model_over: float | None
    model_under: float | None
    model_source: str = "model"
    over_price: int | None
    under_price: int | None


class StatTab(BaseModel):
    stat: str
    label: str
    has_model: bool
    posted_line: float | None
    default_line: float
    default_source: Literal["posted", "last10_middle", "model_middle"]


class Breakdown(BaseModel):
    game_id: str
    player_id: int
    player: str
    team: str
    opponent: str
    home: bool
    tip_utc: str
    as_of_date: str
    stat: str
    label: str
    side: Side
    line: float
    line_source: Literal["posted", "last10_middle", "model_middle"]
    prior_games: int
    last10: Tally
    last5: Tally
    chips: list[Chip]
    season: Tally
    splits: list[Split]
    teammates: Teammates
    opponent_allowed: OppAllowed | None
    model: Model
    role: Role
    steadiness: Steadiness
    onoff: OnOff
    prices: Prices
    heads_up: str | None
    notes: list[str]
    ladder: list[LadderRow]
    ladder_n10: int
    ladder_n5: int
    tabs: list[StatTab]
    dissection: Dissection | None = None
