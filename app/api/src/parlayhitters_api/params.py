"""One validation layer for the string path parameters every route takes.

Integers, floats and enumerations (player_id, line, side, seed, n, before, limit) are typed in
the route signatures, so FastAPI refuses a bad one with 422 ``bad_request`` naming the field.
The string parameters below are checked here, before any store is loaded or any file is looked
up, and a bad one is refused with the same status and words the route answered before (always a
404), now with a registry code and the field name:

* a slate date must be a real calendar date written YYYY-MM-DD;
* a game id is 1-32 letters, digits, ``_`` or ``-`` (NBA ids are ten digits);
* a live event id is 6-12 digits (the capture's ESPN ids);
* a stat is 1-8 lower-case letters or digits (whether it is a stat the route serves is the
  route's own check, since the breakdown serves more stats than the forecast routes).

Nothing that fails here ever reaches the disk or a dictionary lookup.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from .errors import ApiError

DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
GAME_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,32}")
EVENT_RE = re.compile(r"[0-9]{6,12}")
STAT_RE = re.compile(r"[a-z0-9]{1,8}")


def is_date(value: str) -> bool:
    if not DATE_RE.fullmatch(value):
        return False
    try:
        date_cls.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_game_id(value: str) -> bool:
    return GAME_ID_RE.fullmatch(value) is not None


def is_event_id(value: str) -> bool:
    return EVENT_RE.fullmatch(value) is not None


def is_stat(value: str) -> bool:
    return STAT_RE.fullmatch(value) is not None


async def _slate_date(date: str) -> str:
    if not is_date(date):
        raise ApiError("not_a_date", "no such slate", field="date")
    return date


async def _top_parlays_date(date: str) -> str:
    if not is_date(date):
        raise ApiError("not_a_date", "no top parlays file for that date", field="date")
    return date


async def _live_date(date: str) -> str:
    if not is_date(date):
        raise ApiError("not_a_date", "not a date", field="date")
    return date


async def _game_id(game_id: str) -> str:
    if not is_game_id(game_id):
        raise ApiError("unknown_game", "no such game", field="game_id")
    return game_id


async def _event_id(game_id: str) -> str:
    if not is_event_id(game_id):
        raise ApiError("unknown_game", "unknown game", field="game_id")
    return game_id


async def _stat(stat: str) -> str:
    if not is_stat(stat):
        raise ApiError("unknown_stat", "unknown stat", field="stat")
    return stat


SlateDate = Annotated[str, Depends(_slate_date)]
TopParlaysDate = Annotated[str, Depends(_top_parlays_date)]
LiveDate = Annotated[str, Depends(_live_date)]
GameId = Annotated[str, Depends(_game_id)]
EventId = Annotated[str, Depends(_event_id)]
Stat = Annotated[str, Depends(_stat)]


def contained(base: Path, path: Path) -> bool:
    """True when ``path`` resolves inside ``base`` (symlinks and ``..`` followed). Every file path
    built from request input is checked with this before it is opened."""
    try:
        return path.resolve().is_relative_to(base.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
