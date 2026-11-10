"""What the game knew, and when: the tip-off timeline and the uncertainty map. Read-only.

The export keeps every forecast the daily runs stored before tip (several per prop on most nights)
and the official injury report lines with the time each report was current. This module lines the
two up. Nothing is predicted here and no cause is claimed: it shows when a forecast moved and what
had been published by then, and lets the reader draw the link. Where the data holds one forecast
and no report (the fixtures), it says so.

The uncertainty map is words built only from fields the data has: the forecast's own range, his
projected minutes, how steady his minutes have been, his chance to play, how often he started, and
how he did against tonight's opponent (with n).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .breakdown import Context
from .forecast import forecast_probs, shown_probs
from .minutes import MinutesInfo, minutes_info
from .role_stats import STEADY_WINDOW, steadiness

Json = dict[str, Any]
STAT_NAME = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}
ACTIVE = ("out", "doubtful", "questionable", "probable")


class Snap(BaseModel):
    made_at: str
    lead_minutes: float
    mean: float | None
    p10: float
    p50: float
    p90: float
    #: The chance of the over at the line on screen. Only the forecast the app shows carries the
    #: calibrated number; an earlier snapshot has none, so it is null unless the prop has no
    #: calibrated number at all (then every snapshot is the model's own number).
    p_over: float | None
    proj_minutes: float | None
    p_play: float | None
    shown: bool  # the forecast the rest of the app shows


class News(BaseModel):
    at_utc: str
    lead_minutes: float | None
    player_id: int
    name: str
    team: str | None
    status: str
    previous: str | None
    reason: str | None
    himself: bool
    #: His first line on the report we hold (no earlier line to compare), not a change.
    first_report: bool


class UncItem(BaseModel):
    key: str  # range | minutes | share | play | role | opponent | teammates
    text: str


class Uncertainty(BaseModel):
    p10: float
    p90: float
    mean: float | None
    items: list[UncItem]
    minutes: MinutesInfo


class PlayerKnows(BaseModel):
    #: Rows the export should never hold (a forecast or report at or after tip), dropped here.
    post_tip_dropped: int = 0
    n_reports: int = 0
    game_id: str
    player_id: int
    player: str
    team: str
    stat: str
    line: float
    line_source: str
    #: line_cal, rung_cal or model: what the over chance on the shown forecast is.
    p_over_source: str = "model"
    tip_utc: str
    final: bool
    snapshots: list[Snap]
    news: list[News]
    uncertainty: Uncertainty
    note: str


class Mover(BaseModel):
    player_id: int
    name: str
    team: str
    stat: str
    first_mean: float
    last_mean: float
    delta: float
    first_at: str
    last_at: str
    n_snapshots: int


class GameKnows(BaseModel):
    post_tip_dropped: int = 0
    game_id: str
    tip_utc: str
    news: list[News]
    movers: list[Mover]
    n_reports: int
    note: str


def _pct(p: float) -> str:
    """A chance in whole percent, never rounded to a certainty it is not."""
    if p >= 0.995:
        return "over 99%"
    if p < 0.005:
        return "under 1%"
    return f"{p:.0%}"


def clean_reason(raw: str | None) -> str | None:
    """ "Injury/Illness-RightAnkleSprain" -> "Injury/illness, right ankle sprain"."""
    if not raw:
        return None
    head, _, tail = raw.partition("-")
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail).lower().strip()
    first = head.replace("Injury/Illness", "Injury/illness")
    return f"{first}, {words}" if words else first


def _when(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def pre_tip_reports(store: Any, g: Json) -> tuple[list[Json], int]:
    """The game's injury report lines that were current before tip, and how many at or after tip
    (or with an unreadable time) were dropped. The exporter already refuses post-tip rows; this is
    a second check so a public "what the game knew" screen can never show one."""
    tip = _when(g["tip_utc"])
    keep: list[Json] = []
    dropped = 0
    for r in store.injury.get(g["game_id"], []):
        at = _when(r["at_utc"])
        if tip is None or at is None or at >= tip:
            dropped += 1
        else:
            keep.append(r)
    return keep, dropped


def pre_tip_snapshots(store: Any, key: tuple[str, int, str]) -> tuple[list[Json], int]:
    """The prop's stored forecasts made before tip (positive lead time), and how many were not."""
    snaps = store.snapshots.get(key, [])
    keep = [s for s in snaps if s["lead_minutes"] > 0]
    return keep, len(snaps) - len(keep)


def _minutes_before(tip_utc: str, at_utc: str) -> float | None:
    try:
        tip = datetime.fromisoformat(tip_utc.replace("Z", "+00:00"))
        at = datetime.fromisoformat(at_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (tip - at).total_seconds() / 60.0


def news_items(store: Any, g: Json, team_id: int | None, pid: int | None) -> list[News]:
    """Status changes on the official report, oldest first: a player's first line if it is not
    "available", and every later line whose status differs from his previous one. `team_id`
    limits it to one team (None: both)."""
    names: dict[int, str] = getattr(store, "names", {})
    last: dict[int, str] = {}
    out: list[News] = []
    for r in pre_tip_reports(store, g)[0]:
        if team_id is not None and r["team_id"] != team_id:
            continue
        prev = last.get(r["player_id"])
        last[r["player_id"]] = r["status"]
        if prev == r["status"] or (prev is None and r["status"] not in ACTIVE):
            continue
        team = next(
            (t["abbr"] for t in (g["home"], g["away"]) if t["id"] == r["team_id"]),
            None,
        )
        out.append(
            News(
                at_utc=r["at_utc"],
                lead_minutes=_minutes_before(g["tip_utc"], r["at_utc"]),
                player_id=r["player_id"],
                name=names.get(r["player_id"], f"Unnamed player (id {r['player_id']})"),
                team=team,
                status=r["status"],
                previous=prev,
                reason=clean_reason(r["reason"]),
                himself=pid is not None and r["player_id"] == pid,
                first_report=prev is None,
            )
        )
    return out


def _snap_over(raw: Json, fc: Json, line: float, pr: Json) -> float | None:
    shown = shown_probs(pr, line)
    if shown.source == "model":
        return forecast_probs(fc, line).p_over
    return shown.p_over if bool(raw["shown"]) else None


def _snap(raw: Json, line: float, pr: Json) -> Snap:
    fc = {"q": raw["q"], "surv": raw["surv"]}
    q = raw["q"]
    return Snap(
        made_at=raw["made_at"],
        lead_minutes=raw["lead_minutes"],
        mean=raw["mean"],
        p10=q[1],
        p50=q[9],
        p90=q[17],
        p_over=_snap_over(raw, fc, line, pr),
        proj_minutes=raw["proj_minutes"],
        p_play=raw["p_play"],
        shown=bool(raw["shown"]),
    )


def _uncertainty(store: Any, g: Json, pl: Json, stat: str, pr: Json) -> Uncertainty:
    fc = pr["forecast"]
    q = [float(v) for v in fc["q"]]
    p10, p90 = q[1], q[17]
    ctx = None if store.history is None else Context(store.history, g, pl, stat)
    mi = minutes_info(fc, ctx)
    name = STAT_NAME.get(stat, stat)
    items: list[UncItem] = []
    width = p90 - p10
    rel = "" if not mi.stat_mean else f", about {width / mi.stat_mean:.0%} of his typical number"
    items.append(
        UncItem(
            key="range",
            text=f"The forecast's middle 80% for {name} runs from {p10:.1f} to {p90:.1f}"
            f" (a width of {width:.1f}{rel}).",
        )
    )
    if mi.proj_minutes is not None:
        items.append(
            UncItem(
                key="minutes", text=f"The model projects {mi.proj_minutes:.0f} minutes if he plays."
            )
        )
    if ctx is not None:
        st = steadiness(ctx.all_prior)
        items.append(UncItem(key="minutes", text=st.say))
    if mi.minutes_share is not None:
        items.append(
            UncItem(
                key="share",
                text=f"Rough estimate: minutes alone account for about {mi.minutes_share:.0%} of "
                f"the spread in {name}.",
            )
        )
    if mi.p_play is not None:
        items.append(
            UncItem(
                key="play",
                text=f"Chance to play: {_pct(mi.p_play)}. The range above assumes he plays.",
            )
        )
    if ctx is not None:
        last = ctx.all_prior[-STEADY_WINDOW:]
        if last:
            started = sum(1 for r in last if r.starter)
            items.append(
                UncItem(
                    key="role",
                    text=f"He started {started} of his last {len(last)} games, so his role "
                    + ("has been fixed." if started in (0, len(last)) else "has been mixed."),
                )
            )
        vs = [r for r in ctx.prior if r.opp == ctx.opp]
        opp = store.history.team_abbr(ctx.opp) if store.history is not None else str(ctx.opp)
        if vs:
            avg = sum(r.value(stat) for r in vs) / len(vs)
            small = " Small sample." if len(vs) < 5 else ""
            items.append(
                UncItem(
                    key="opponent",
                    text=f"Against {opp} he has {avg:.1f} {name} a game over {len(vs)} "
                    f"earlier {'game' if len(vs) == 1 else 'games'}.{small}",
                )
            )
        else:
            items.append(
                UncItem(key="opponent", text=f"No earlier games against {opp} in our data.")
            )
    names: dict[int, str] = getattr(store, "names", {})
    team_id = g["home"]["id"] if pl["side"] == "home" else g["away"]["id"]
    all_rows = pre_tip_reports(store, g)[0]
    # Only reports published at or before the shown forecast were there for the model to see.
    shown_snap = next(
        (
            s
            for s in pre_tip_snapshots(store, (g["game_id"], int(pl["player_id"]), stat))[0]
            if s["shown"]
        ),
        None,
    )
    cutoff = None if shown_snap is None else _when(shown_snap["made_at"])
    rows = [r for r in all_rows if cutoff is None or (_when(r["at_utc"]) or cutoff) <= cutoff]
    if all_rows and not rows:
        items.append(
            UncItem(
                key="teammates",
                text="The injury report stored for this game was published after this forecast "
                "was made, so it is not counted here.",
            )
        )
    if rows:
        newest = max(r["at_utc"] for r in rows)
        out = [
            names.get(r["player_id"], str(r["player_id"]))
            for r in rows
            if r["at_utc"] == newest
            and r["team_id"] == team_id
            and r["status"] == "out"
            and r["player_id"] != pl["player_id"]
        ]
        if out:
            shown = ", ".join(out[:3]) + (f" and {len(out) - 3} more" if len(out) > 3 else "")
            items.append(
                UncItem(
                    key="teammates",
                    text=f"On the latest report before this forecast, {len(out)} teammate"
                    f"{'' if len(out) == 1 else 's'} listed out: {shown}.",
                )
            )
    return Uncertainty(p10=p10, p90=p90, mean=fc.get("mean"), items=items, minutes=mi)


def player_knows(store: Any, g: Json, pl: Json, stat: str) -> PlayerKnows:
    pr = pl["props"][stat]
    line = float(pr["line"])
    key = (g["game_id"], int(pl["player_id"]), stat)
    raw_snaps, dropped_snaps = pre_tip_snapshots(store, key)
    snaps = [_snap(s, line, pr) for s in raw_snaps]
    reports, dropped_rows = pre_tip_reports(store, g)
    n_reports = len({r["at_utc"] for r in reports})
    me, _ = (g["home"], g["away"]) if pl["side"] == "home" else (g["away"], g["home"])
    news = news_items(store, g, me["id"], int(pl["player_id"]))
    if len(snaps) >= 2:
        note = (
            "Each dot is one forecast the daily run stored before tip, all from the same model. "
            "The news is what the official injury report said; a change in the forecast and "
            "a report can be a coincidence of timing: read them side by side, "
            "not as cause and effect."
        )
    elif snaps:
        note = (
            "This data holds a single stored forecast for this prop, so there is no earlier "
            "one to compare it with."
        )
    else:
        note = (
            "This data holds one forecast per prop and no earlier ones, so there is no timeline "
            "to draw. The uncertainty notes below still apply."
        )
    if n_reports:
        note += (
            f" This data holds {n_reports} injury report{'' if n_reports == 1 else 's'} for this "
            "game, so earlier reports, if there were any, are not shown."
        )
    return PlayerKnows(
        post_tip_dropped=dropped_snaps + dropped_rows,
        n_reports=n_reports,
        game_id=g["game_id"],
        player_id=int(pl["player_id"]),
        player=pl["name"],
        team=me["abbr"],
        stat=stat,
        line=line,
        line_source=str(pr.get("line_source", "posted")),
        p_over_source=shown_probs(pr, line).source,
        tip_utc=g["tip_utc"],
        final=g.get("result") is not None,
        snapshots=snaps,
        news=news,
        uncertainty=_uncertainty(store, g, pl, stat, pr),
        note=note,
    )


def game_knows(store: Any, g: Json, top: int = 6) -> GameKnows:
    movers: list[Mover] = []
    for pl in g["players"]:
        if "pts" not in pl["props"]:
            continue
        snaps = pre_tip_snapshots(store, (g["game_id"], int(pl["player_id"]), "pts"))[0]
        first, last = (snaps[0], snaps[-1]) if snaps else (None, None)
        if len(snaps) < 2 or first is None or last is None:
            continue
        if first["mean"] is None or last["mean"] is None:
            continue
        team = (g["home"] if pl["side"] == "home" else g["away"])["abbr"]
        movers.append(
            Mover(
                player_id=int(pl["player_id"]),
                name=pl["name"],
                team=team,
                stat="pts",
                first_mean=first["mean"],
                last_mean=last["mean"],
                delta=last["mean"] - first["mean"],
                first_at=first["made_at"],
                last_at=last["made_at"],
                n_snapshots=len(snaps),
            )
        )
    movers.sort(key=lambda m: (-abs(m.delta), m.name))
    rows, dropped = pre_tip_reports(store, g)
    for pl in g["players"]:
        dropped += pre_tip_snapshots(store, (g["game_id"], int(pl["player_id"]), "pts"))[1]
    note = (
        "Biggest changes in the points forecast between the first and last forecast stored "
        "before tip, next to what the injury report said."
        if movers
        else "This data holds no earlier forecasts to compare, so no forecast moved on record."
    )
    return GameKnows(
        post_tip_dropped=dropped,
        game_id=g["game_id"],
        tip_utc=g["tip_utc"],
        news=news_items(store, g, None, None),
        movers=movers[:top],
        n_reports=len({r["at_utc"] for r in rows}),
        note=note,
    )
