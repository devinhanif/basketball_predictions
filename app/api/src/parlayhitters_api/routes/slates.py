"""One night: the list of nights, a slate, the blind swipe deck and the settled results."""

from __future__ import annotations

import random
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query

from ..deps import StoreDep
from ..models import (
    GameSummary,
    SlateView,
    SwipeCard,
    SwipeDeck,
)
from ..params import SlateDate
from ..views import (
    _key_props,
    _need_slate,
    _prop_view,
    _swipe_empty_note,
    _team,
    _wp,
)


def register(app: FastAPI) -> None:
    @app.get("/api/slates")
    def slates(store: StoreDep) -> list[dict[str, Any]]:
        return [
            {
                "date": s["date"],
                "label": s["label"],
                "games": s["counts"]["games"],
                "props": s["counts"]["props"],
                "finished": sum(1 for g in s["games"] if g.get("result") is not None),
            }
            for s in store.slates.values()
        ]

    @app.get("/api/slates/{date}", response_model=SlateView)
    def slate(date: SlateDate, store: StoreDep) -> SlateView:
        s = _need_slate(store, date)
        games = [
            GameSummary(
                game_id=g["game_id"],
                date=g["date"],
                tip_utc=g["tip_utc"],
                arena=g.get("arena"),
                home=_team(g["home"]),
                away=_team(g["away"]),
                win_probability=_wp(g),
                key_props=_key_props(g),
            )
            for g in s["games"]
        ]
        return SlateView(
            date=s["date"],
            label=s["label"],
            clock_utc=store.slate_clock(date),
            mode=store.mode,
            counts=s["counts"],
            games=games,
        )

    @app.get("/api/slates/{date}/swipe", response_model=SwipeDeck)
    def swipe(
        date: SlateDate,
        store: StoreDep,
        seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
        n: Annotated[int, Query(ge=1, le=60)] = 24,
    ) -> SwipeDeck:
        """Blind cards: line and price only, no forecast, no result. Random draw by seed."""
        s = _need_slate(store, date)
        clock = store.slate_clock(date)
        cards: list[SwipeCard] = []
        started = 0
        with_lines = 0
        for g in s["games"]:
            if any(
                pr["over_price"] is not None and pr["under_price"] is not None
                for pl in g["players"]
                for pr in pl["props"].values()
            ):
                with_lines += 1
            if str(g["tip_utc"]) <= clock:  # refused at or after tip (ISO-8601 UTC sorts as text)
                started += 1
                continue
            for pl in g["players"]:
                me, opp = (g["home"], g["away"]) if pl["side"] == "home" else (g["away"], g["home"])
                for st, pr in pl["props"].items():
                    if pr["over_price"] is None or pr["under_price"] is None:
                        continue  # no sportsbook line in this data: nothing to swipe on
                    cards.append(
                        SwipeCard(
                            prop_id=pr["prop_id"],
                            game_id=g["game_id"],
                            player_id=pl["player_id"],
                            player=pl["name"],
                            team=me["abbr"],
                            opponent=opp["abbr"],
                            stat=st,
                            line=float(pr["line"]),
                            over_price=int(pr["over_price"]),
                            under_price=int(pr["under_price"]),
                            book=pr.get("price_book"),
                            tip_utc=g["tip_utc"],
                        )
                    )
        rng = random.Random(seed)  # noqa: S311 - a shuffle, not a secret
        rng.shuffle(cards)
        total = len(s["games"])
        reason: Literal["ok", "no_games", "all_started", "no_lines"] = "ok"
        if not cards:
            reason = "no_games" if total == 0 else "all_started" if started == total else "no_lines"
        return SwipeDeck(
            date=date,
            clock_utc=clock,
            seed=seed,
            cards=cards[:n],
            note=(
                "Blind cards: the line and the price only. Drawn at random from every posted line; "
                "players who do not play are void."
                if cards
                else _swipe_empty_note(
                    reason, total, started, with_lines, store.meta().get("built_at")
                )
            ),
            reason=reason,
            games_total=total,
            games_started=started,
            games_with_lines=with_lines,
        )

    @app.get("/api/slates/{date}/results")
    def results(date: SlateDate, store: StoreDep) -> dict[str, Any]:
        """Outcomes plus the book probability at the time, for settling paper picks. No model chance
        is served beside an outcome: that pairing is the calibration's locked live test
        (LINE_CAL_LIVE), read once, not before."""
        s = _need_slate(store, date)
        rows: list[dict[str, Any]] = []
        for g in s["games"]:
            for pl in g["players"]:
                for st, pr in pl["props"].items():
                    v = _prop_view(st, pr)
                    if v.over_price is None or v.under_price is None or v.market_p_over is None:
                        continue  # settling a pick needs the line and price that were shown
                    if pr["actual"] is None:
                        continue  # not played yet, or did not play
                    rows.append(
                        {
                            "prop_id": pr["prop_id"],
                            "game_id": g["game_id"],
                            "player": pl["name"],
                            "stat": st,
                            "line": v.line,
                            "over_price": v.over_price,
                            "under_price": v.under_price,
                            "actual": pr["actual"],
                            "market_p_over": v.market_p_over,
                            "market_p_under": 1 - v.market_p_over,
                        }
                    )
        return {
            "date": date,
            "label": "results and the forecasts that existed before tip"
            + (" (historical replay)" if store.mode == "fixture_replay" else ""),
            "rows": rows,
        }
