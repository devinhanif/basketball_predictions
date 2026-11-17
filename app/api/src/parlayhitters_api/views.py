"""Pure helpers that shape stored rows into the views the routes return. No I/O."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .errors import (
    ApiError,
)
from .forecast import (
    TAUS,
    clean_line_cal,
    clean_null_reason,
    clean_rung_cal,
    devig_over,
    shown_probs,
)
from .model_source import model_detail, model_label, p_play_label
from .models import (
    LineCal,
    PropView,
    RungCal,
    Team,
    WinProbability,
)
from .store import AnyStore, Json

#: The stats the forecast routes serve (the breakdown serves more, from its history).
STATS = ("pts", "reb", "ast", "fg3m")


def _et_stamp(utc: str | None) -> str:
    """ "Nov 9, 4:35 PM ET" from an ISO UTC time, or "unknown time" when it cannot be read."""
    try:
        t = datetime.fromisoformat(str(utc).replace("Z", "+00:00"))
    except ValueError:
        return "unknown time"
    et = t.astimezone(ZoneInfo("America/New_York"))
    return f"{et:%b} {et.day}, {et.hour % 12 or 12}:{et:%M} {'AM' if et.hour < 12 else 'PM'} ET"


def _swipe_empty_note(
    reason: str, total: int, started: int, with_lines: int, built_at: str | None = None
) -> str:
    """Why a night has nothing to swipe on, stating only what this data holds."""
    if reason == "no_games":
        return "No games are on the schedule for this night."
    games = f"{total} game{'' if total == 1 else 's'}"
    if reason == "all_started":
        lines = (
            f" This data holds player lines for {with_lines} of them."
            if with_lines
            else " This data carries no player lines for any of them."
        )
        verb = "has" if total == 1 else "have"
        return f"All {games} {verb} already started, so picks are closed.{lines}"
    left = total - started
    still = f"{left} game{'' if left == 1 else 's'} still to play"
    if with_lines == 0:
        return f"This data (built {_et_stamp(built_at)}) carries no player lines."
    began = f"{started} of {games} {'has' if started == 1 else 'have'} started. " if started else ""
    return f"{began}This data (built {_et_stamp(built_at)}) holds no player lines for the {still}."


def _team(t: Json) -> Team:
    return Team(**t)


def _prop_view(stat: str, p: Json) -> PropView:
    q = [float(v) for v in p["forecast"]["q"]]
    lp = shown_probs(p, float(p["line"]))
    priced = p["over_price"] is not None and p["under_price"] is not None
    source, label = model_label(p["forecast"].get("model"))
    return PropView(
        prop_id=p["prop_id"],
        stat=stat,
        line=float(p["line"]),
        line_source=p.get("line_source", "posted"),
        price_book=p.get("price_book"),
        over_price=None if p["over_price"] is None else int(p["over_price"]),
        under_price=None if p["under_price"] is None else int(p["under_price"]),
        line_snapshot_utc=p.get("line_snapshot_utc"),
        forecast_mean=p["forecast"].get("mean"),
        q=q,
        taus=list(TAUS),
        model_p_over=lp.p_over,
        model_p_under=lp.p_under,
        model_p_push=lp.p_push,
        extrapolated=lp.extrapolated,
        model_p_source=lp.source,  # type: ignore[arg-type]
        market_p_over=devig_over(int(p["over_price"]), int(p["under_price"])) if priced else None,
        forecast_stamp=p["forecast"]["stamp"],
        model_source=source,
        model_label=label,
        model_detail=model_detail(p["forecast"].get("model")),
        p_play=p["forecast"].get("p_play"),
        p_play_label=p_play_label(p["forecast"].get("p_play_info")),
        book_main_line=None if p.get("book_main_line") is None else float(p["book_main_line"]),
        rung_cal=RungCal(**rc) if (rc := clean_rung_cal(p.get("rung_cal"))) else None,
        line_cal=LineCal(**lc) if (lc := clean_line_cal(p.get("line_cal"))) else None,
        line_cal_null_reason=clean_null_reason(p.get("line_cal_null_reason")),
    )


def _wp(g: Json) -> WinProbability:
    return WinProbability(**g["win_probability"])


def _need_slate(store: AnyStore, date: str) -> Json:
    s = store.slate(date)
    if s is None:
        raise ApiError("unknown_slate", "no such slate", field="date")
    return s


def _need_game(store: AnyStore, game_id: str) -> Json:
    g = store.game(game_id)
    if g is None:
        raise ApiError("unknown_game", "no such game", field="game_id")
    return g


def _no_forecast(g: Json, player_id: int) -> ApiError:
    """ "No forecast for that player and stat", naming the player when he is not in the game and the
    stat when he is but has no forecast for it."""
    known = any(p["player_id"] == player_id for p in g["players"])
    return ApiError(
        "no_forecast",
        "no forecast for that player and stat",
        field="stat" if known else "player_id",
    )


def _guess_field(store: AnyStore, game_id: str, player_id: int, stat: str) -> str | None:
    """Which part of a guess-reveal address has no finished prop behind it (None: all exist)."""
    g = store.game(game_id)
    if g is None or g.get("result") is None:
        return "game_id"
    pl = next((p for p in g["players"] if p["player_id"] == player_id), None)
    if pl is None:
        return "player_id"
    return "stat" if stat not in pl["props"] else None


def _game_notes(store: AnyStore) -> list[str]:
    if store.mode == "fixture_replay":
        return [
            "Only players with a line from the sportsbook are listed. "
            "Every forecast assumes the player suits up.",
            "We do not forecast minutes yet. "
            "How long a player really played is part of the final result.",
        ]
    lead = "Every forecast assumes the player suits up; the chance he plays and his projected "
    lead += "minutes are on his breakdown page."
    if getattr(store, "has_lines", False):
        return [
            lead,
            "Lines are the one most books carried before tip; the price beside it is one book's "
            "quote, and every book's quote is on the breakdown page. A line with no price beside "
            "it had no usable quote, so it starts at the middle of our own forecast.",
        ]
    return [
        lead,
        "There is no sportsbook line in this data. The line shown is the middle of our own "
        "forecast, a place to start, and you can move it on the breakdown page.",
    ]


def _key_props(g: Json, n: int = 3) -> list[dict[str, Any]]:
    """The n highest-forecast-mean points props of the game, with the model's P(over)."""
    rows: list[tuple[float, Json, Json]] = []
    for pl in g["players"]:
        pts = pl["props"].get("pts")
        if pts and pts["forecast"].get("mean") is not None:
            rows.append((float(pts["forecast"]["mean"]), pl, pts))
    rows.sort(key=lambda r: -r[0])
    out: list[dict[str, Any]] = []
    for _, pl, pts in rows[:n]:
        v = _prop_view("pts", pts)
        out.append(
            {
                "player_id": pl["player_id"],
                "name": pl["name"],
                "team": g["home"]["abbr"] if pl["side"] == "home" else g["away"]["abbr"],
                "stat": "pts",
                "line": v.line,
                "line_source": v.line_source,
                "over_price": v.over_price,
                "under_price": v.under_price,
                "forecast_mean": v.forecast_mean,
                # The middle and the middle half of the forecast, read from the stored quantiles.
                "q25": v.q[4],
                "q50": v.q[9],
                "q75": v.q[14],
                "model_p_over": v.model_p_over,
                "market_p_over": v.market_p_over,
                "extrapolated": v.extrapolated,
                "model_p_source": v.model_p_source,
                "model_source": v.model_source,
                "model_label": v.model_label,
            }
        )
    return out
