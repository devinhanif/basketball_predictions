"""Role facts for the game page: for each listed player, how steady his minutes have been, his
team's on/off numbers, and how he has done without each rotation teammate who is out tonight.

Built on the same as-of rule as the leg breakdown (games strictly before the slate date) and the
same injury-report cut-off (published at least an hour before tip).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .breakdown import Context, mate_note, teammates_on_report
from .history import History
from .role_stats import OnOff, Per36, Steadiness, onoff, per36, steadiness

Json = dict[str, Any]


class MateOut(BaseModel):
    player_id: int
    name: str
    status: str
    reason: str | None
    avg_minutes: float
    without: Per36
    with_: Per36
    note: str


class PlayerRole(BaseModel):
    steadiness: Steadiness
    onoff: OnOff
    teammates_out: list[MateOut]


def player_role(hist: History, game: Json, player: Json) -> PlayerRole:
    ctx = Context(hist, game, player, None)
    rep = teammates_on_report(ctx)
    return PlayerRole(
        steadiness=steadiness(ctx.all_prior),
        onoff=onoff(hist, ctx.pid, ctx.all_prior, ctx.season),
        teammates_out=[
            MateOut(
                player_id=m.pid,
                name=m.name,
                status=m.status,
                reason=m.reason,
                avg_minutes=m.avg_minutes,
                without=per36(m.without_rows),
                with_=per36(m.with_rows),
                note=mate_note(len(m.without_rows)),
            )
            for m in rep.mates
        ],
    )
