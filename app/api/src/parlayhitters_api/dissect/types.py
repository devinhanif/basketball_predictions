"""Response shape of the written dissection (no imports from the rest of the package)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Tier = Literal["leg", "parlay"]

DISCLOSURE = (
    "Written by an automated system from the numbers on this page. Analysis only, not advice."
)


class Dissection(BaseModel):
    """Two to four plain sentences about one leg (or one ticket), and how they were checked."""

    text: str  # empty when nothing safe could be written
    tier: Tier
    provider: str  # which writer produced `text` ("template" today)
    model: str | None  # the model a provider routed to, if any (None for the template)
    guard_ok: bool  # every number in `text` appears in the fact packet
    numbers_checked: int  # how many number tokens the guard looked at
    fell_back: bool  # True when another provider was tried first and refused or was rejected
    rejected: list[str]  # why earlier attempts were refused (provider name: reason)
    disclosure: str = DISCLOSURE
