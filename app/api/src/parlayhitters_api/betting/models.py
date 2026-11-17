"""Response shapes of the quote board (docs/contracts/betting_board_v1.md). Stored fields only:
no probability, edge, expected value, fair or de-vigged price, price ranking, or model number."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .catalog import Kind, UnknownName

Unknown = list[UnknownName]


class Provider(BaseModel):
    id: str
    label: str
    kind: Kind
    price_unit: Literal["american"]
    newest_snapshot_utc: str | None
    n_quotes: int
    fee: None
    settlement_rule_id: None


class Providers(BaseModel):
    as_of: str
    data_version: str
    providers: list[Provider]
    unknown: Unknown
    note: str


class BoardQuote(BaseModel):
    quote_id: str
    provider: str
    side: Literal["over", "under"]
    price_american: int | None
    implied_prob_raw: float | None
    snapshot_utc: str
    minutes_before_tip: float | None
    is_alternate: bool
    usable: bool
    unusable_reason: str | None


class BoardLine(BaseModel):
    line: float
    quotes: list[BoardQuote]


class ModelRef(BaseModel):
    props: str
    source: str


class Board(BaseModel):
    game_id: str
    date: str
    tip_utc: str | None
    final: bool
    player_id: int
    player: str
    team: str | None
    opponent: str | None
    stat: str
    as_of: str
    data_version: str
    provenance: Literal["delayed"]
    lines: list[BoardLine]
    providers_absent: list[str]
    model_ref: ModelRef
    unknown: Unknown
    note: str


class SlateGame(BaseModel):
    game_id: str
    tip_utc: str | None
    home: str
    away: str
    final: bool
    n_props_with_quotes: int


class SlateProp(BaseModel):
    game_id: str
    player_id: int
    player: str
    team: str | None
    stat: str
    n_providers: int
    n_lines: int


class Slate(BaseModel):
    date: str
    as_of: str
    games: list[SlateGame]
    props: list[SlateProp]
