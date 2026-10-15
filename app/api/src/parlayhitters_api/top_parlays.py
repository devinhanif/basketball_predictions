"""Top-parlays panel: serves `top_parlays_<date>.json` (contract docs/contracts/top_parlays_v2.md).

Read-only. The file is validated against the contract's shape and an unknown schema is refused
(never passed through). The directory is APP_TOP_PARLAYS_DIR, else the committed illustration
fixtures. Paper only: nothing here prices, places or routes anything.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date as date_cls
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

SCHEMA = "top_parlays/2"
ENV = "APP_TOP_PARLAYS_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "top_parlays"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_BANNED = re.compile(
    r"\b(locks?|locked|guarantee[ds]?|free[\s-]money|can'?t lose|cannot lose|hammer|best bets?"
    r"|easy money|sure thing|fade the public)\b",
    re.IGNORECASE,
)
JOINT_TOL = 0.05


class TopParlaysMissing(Exception):
    """No file for that date."""


class TopParlaysRefused(Exception):
    """A file exists but is not a top_parlays/2 object we can vouch for."""


class TopParlaysUnavailable(Exception):
    """The configured directory is missing, so absence of a file would be misleading."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


def _clean(text: str) -> str:
    if _BANNED.search(text.replace("\u2019", "'").replace("\u2018", "'")):
        raise ValueError("banned phrase in free text")
    return text


class Leg(_Strict):
    game_id: str
    tipoff: AwareDatetime
    player_id: int
    player_name: str
    team: str
    opp: str
    stat: str
    side: Literal["over", "under"]
    line: float
    book: str
    price_american: int
    price_at: AwareDatetime
    p_raw: float = Field(ge=0, le=1)
    p_cal: float = Field(ge=0, le=1)
    model_source: str
    reason: str
    tail_flag: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if -100 < self.price_american < 100:
            raise ValueError("price_american inside (-100, 100)")
        if self.price_at >= self.tipoff:
            raise ValueError("price stamped at or after tip")
        _clean(self.reason)
        return self


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
    tail_note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        lo, hi = self.p_joint_cal_80
        if not lo <= self.p_joint_cal <= hi:
            raise ValueError("p_joint_cal outside its own 80% range")
        if self.ev_low80 > self.ev_per_dollar:
            raise ValueError("ev_low80 above ev_per_dollar")
        if not self.same_game:
            prod = math.prod(leg.p_cal for leg in self.legs)
            if abs(self.p_joint_cal - prod) > JOINT_TOL:
                raise ValueError("p_joint_cal far from the product of independent legs")
        _clean(self.why)
        if self.tail_note is not None:
            _clean(self.tail_note)
        return self


class Calibration(_Strict):
    rule: str
    sha8: str
    fit_through: str
    status: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        _clean(self.status)
        return self


class Gate(_Strict):
    settled: int = Field(ge=0)
    settled_min: Literal[30]
    slate_dates: int = Field(ge=0)
    dates_min: Literal[14]
    skill_vs_market: float | None
    open: bool

    @model_validator(mode="after")
    def _check(self) -> Self:
        should = (
            self.settled >= self.settled_min
            and self.slate_dates >= self.dates_min
            and self.skill_vs_market is not None
            and self.skill_vs_market > 0
        )
        if self.open != should:
            raise ValueError("gate.open disagrees with its own counters")
        return self


class Fixture(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    kind: str = Field(max_length=60)
    label: str = Field(max_length=600)


class TopParlays(_Strict):
    schema_: Literal["top_parlays/2"] = Field(alias="schema")
    illustration: bool
    slate_date: str
    made_at: AwareDatetime
    calibration: Calibration
    gate: Gate
    ranking: str
    verdict: Literal["keep_your_money", "positive_ev_found"]
    empty_reason: str | None
    sizes: dict[Literal["2", "3"], list[Ticket]]
    caveats: list[str]
    # Optional: a hindsight illustration rebuilt from stored past rows says so (kind, label).
    fixture: Fixture | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.sizes) != {"2", "3"}:
            raise ValueError("both size keys '2' and '3' are required")
        _clean(self.ranking)
        if self.empty_reason is not None:
            _clean(self.empty_reason)
        tips = []
        for size, tickets in self.sizes.items():
            if len(tickets) > 3:
                raise ValueError("more than 3 tickets in a size")
            if [t.rank for t in tickets] != list(range(1, len(tickets) + 1)):
                raise ValueError("ranks are not 1..k in order")
            for t in tickets:
                if len(t.legs) != int(size):
                    raise ValueError("leg count does not match its size")
                tips += [leg.tipoff for leg in t.legs]
                if any(leg.price_at > self.made_at for leg in t.legs):
                    raise ValueError("a price is stamped after the file was made")
        if tips and self.made_at >= min(tips):
            raise ValueError("made at or after the earliest tip")
        if self.verdict == "positive_ev_found":
            low_ok = any(t.ev_low80 > 0 for ts in self.sizes.values() for t in ts)
            if self.illustration or not self.gate.open or not low_ok:
                raise ValueError("positive_ev_found without an open gate and a low bound above 0")
        for c in self.caveats:
            _clean(c)
        if self.fixture is not None:
            _clean(self.fixture.label)
        return self


def top_parlays_dir() -> Path:
    configured = os.environ.get(ENV)
    if configured is None:
        return DEFAULT_DIR
    d = Path(configured)
    if not configured or not d.is_dir():
        raise TopParlaysUnavailable(f"{ENV} is not an existing directory")
    return d


def _valid_date(date: str) -> bool:
    if not _DATE.fullmatch(date):
        return False
    try:
        date_cls.fromisoformat(date)
    except ValueError:
        return False
    return True


def load_top_parlays(date: str, directory: Path | None = None) -> dict[str, object]:
    """The validated payload for `date`. Raises TopParlaysMissing / Refused / Unavailable."""
    if not _valid_date(date):
        raise TopParlaysMissing(date)
    base = directory or top_parlays_dir()
    path = base / f"top_parlays_{date}.json"
    if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
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
    return doc.model_dump(by_alias=True, mode="json")
