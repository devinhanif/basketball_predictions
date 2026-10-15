"""Top-parlays panel: serves `top_parlays_<date>.json` (contract docs/contracts/top_parlays_v1.md).

Read-only. The file is validated against the contract's shape and an unknown schema is refused
(never passed through). The directory is APP_TOP_PARLAYS_DIR, else the committed illustration
fixtures. Paper only: nothing here prices, places or routes anything.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA = "top_parlays/1"
ENV = "APP_TOP_PARLAYS_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "top_parlays"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TopParlaysMissing(Exception):
    """No file for that date."""


class TopParlaysRefused(Exception):
    """A file exists but is not a top_parlays/1 object we can vouch for."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Leg(_Strict):
    game_id: str
    tipoff: str
    player_id: int
    player_name: str
    team: str
    opp: str
    stat: str
    side: Literal["over", "under"]
    line: float
    book: str
    price_american: int
    price_at: str
    p_raw: float = Field(ge=0, le=1)
    p_cal: float = Field(ge=0, le=1)
    model_source: str
    reason: str


class Ticket(_Strict):
    rank: int
    same_game: bool
    legs: list[Leg] = Field(min_length=2, max_length=3)
    p_joint_raw: float = Field(ge=0, le=1)
    p_joint_cal: float = Field(ge=0, le=1)
    p_joint_cal_80: tuple[float, float]
    book_implied: float = Field(ge=0, le=1)
    ev_per_dollar: float
    ev_low80: float
    why: str


class Calibration(_Strict):
    rule: str
    sha8: str
    fit_through: str
    status: str


class TopParlays(_Strict):
    schema_: Literal["top_parlays/1"] = Field(alias="schema")
    illustration: bool = False
    slate_date: str
    made_at: str
    calibration: Calibration
    ranking: str
    verdict: Literal["keep_your_money", "positive_ev_found"]
    empty_reason: str | None
    sizes: dict[Literal["2", "3"], list[Ticket]]
    caveats: list[str]


def top_parlays_dir() -> Path:
    return Path(os.environ.get(ENV) or DEFAULT_DIR)


def load_top_parlays(date: str, directory: Path | None = None) -> dict[str, object]:
    """The validated payload for `date`. Raises TopParlaysMissing / TopParlaysRefused."""
    if not _DATE.match(date):
        raise TopParlaysMissing(date)
    path = (directory or top_parlays_dir()) / f"top_parlays_{date}.json"
    if not path.is_file():
        raise TopParlaysMissing(date)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TopParlaysRefused(f"unreadable JSON: {e}") from e
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        got = raw.get("schema") if isinstance(raw, dict) else None
        raise TopParlaysRefused(f"unknown schema {got!r}; this app reads {SCHEMA!r} only")
    try:
        doc = TopParlays.model_validate(raw)
    except ValidationError as e:
        raise TopParlaysRefused(f"does not match {SCHEMA}: {e.error_count()} problem(s)") from e
    if doc.slate_date != date:
        raise TopParlaysRefused(f"file is for slate {doc.slate_date}, asked for {date}")
    return doc.model_dump(by_alias=True)
