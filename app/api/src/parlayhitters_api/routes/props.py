"""One player and stat: the chance at a line, the card, the forecast timeline and the leg
breakdown."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query

from ..breakdown import Breakdown, Context, Side
from ..breakdown import build as build_breakdown
from ..deps import StoreDep
from ..dissect.providers import default_router
from ..dissect.service import dissect_leg
from ..errors import (
    ApiError,
)
from ..forecast import (
    TAUS,
    dist_grid,
    forecast_method,
    no_cal_here,
    shown_probs,
)
from ..knows import PlayerKnows, player_knows
from ..minutes import minutes_info
from ..models import (
    CardStats,
    CardView,
    Dist,
    GameDot,
    HitTen,
    MateOut,
    ProbAtLine,
)
from ..params import GameId, Stat
from ..views import (
    STATS,
    _need_game,
    _no_forecast,
    _prop_view,
)


def register(app: FastAPI) -> None:
    @app.get("/api/props/{game_id}/{player_id}/{stat}", response_model=ProbAtLine)
    def prop_at_line(
        game_id: GameId,
        player_id: int,
        stat: Stat,
        store: StoreDep,
        line: Annotated[float, Query(ge=0, le=200)],
    ) -> ProbAtLine:
        g = _need_game(store, game_id)
        if stat not in STATS:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        for pl in g["players"]:
            if pl["player_id"] == player_id and stat in pl["props"]:
                forecast = pl["props"][stat]["forecast"]
                q = [float(v) for v in forecast["q"]]
                posted = pl["props"][stat]
                lp = shown_probs(posted, line)
                hide = no_cal_here(posted, lp)
                priced = posted["over_price"] is not None
                is_main = float(posted["line"]) == line
                return ProbAtLine(
                    game_id=game_id,
                    player_id=player_id,
                    stat=stat,
                    line=line,
                    p_over=None if hide else lp.p_over,
                    p_under=None if hide else lp.p_under,
                    p_push=None if hide else lp.p_push,
                    extrapolated=lp.extrapolated,
                    p_source=lp.source,  # type: ignore[arg-type]
                    no_cal_here=hide,
                    method=forecast_method(forecast),
                    taus=list(TAUS),
                    q=q,
                    main_line=float(posted["line"]),
                    is_main_line=is_main,
                    over_price=int(posted["over_price"]) if is_main and priced else None,
                    under_price=int(posted["under_price"]) if is_main and priced else None,
                )
        raise _no_forecast(g, player_id)

    @app.get("/api/card/{game_id}/{player_id}/{stat}", response_model=CardView)
    def card(game_id: GameId, player_id: int, stat: Stat, store: StoreDep) -> CardView:
        """Quick stats and the full distribution for one prop, as of before tip. Fetched only when
        the user opens it, so the blind swipe deck itself never carries a forecast."""
        g = _need_game(store, game_id)
        if stat not in STATS:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        pl = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if pl is None or stat not in pl["props"]:
            raise _no_forecast(g, player_id)
        pr = pl["props"][stat]
        view = _prop_view(stat, pr)
        me, opp = (g["home"], g["away"]) if pl["side"] == "home" else (g["away"], g["home"])
        stats: CardStats | None = None
        if store.history is not None and stat in store.history.stats:
            b = build_breakdown(store.history, g, pl, stat, float(pr["line"]), "over")
            t = b.teammates
            stats = CardStats(
                as_of_date=b.as_of_date,
                prior_games=b.prior_games,
                last5=[c.value for c in b.chips[:5]],
                last10=[c.value for c in b.chips],
                last10_games=[
                    GameDot(
                        game_id=c.game_id,
                        date=c.date,
                        opp=c.opp,
                        home=c.home,
                        value=c.value,
                        linkable=store.game(c.game_id) is not None,
                    )
                    for c in b.chips
                ],
                avg5=b.last5.avg,
                avg10=b.last10.avg,
                avg_season=b.season.avg,
                n_season=b.season.n,
                min5=b.last5.avg_minutes,
                min_season=b.season.avg_minutes,
                hit10_over=HitTen(
                    n=b.last10.n, hits=b.last10.hits, pushes=b.last10.pushes, line=b.line
                ),
                teammates_out=[MateOut(name=m.name, status=m.status) for m in t.items],
                teammates_known=t.report_as_of_utc is not None,
            )
        return CardView(
            prop_id=view.prop_id,
            game_id=game_id,
            player_id=player_id,
            player=pl["name"],
            team=me["abbr"],
            opponent=opp["abbr"],
            home=pl["side"] == "home",
            stat=stat,
            line=view.line,
            line_source=view.line_source,
            over_price=view.over_price,
            under_price=view.under_price,
            price_book=view.price_book,
            book_main_line=view.book_main_line,
            rung_cal=view.rung_cal,
            line_cal=view.line_cal,
            line_cal_null_reason=view.line_cal_null_reason,
            dist=Dist(**dist_grid(pr["forecast"])),
            stats=stats,
            forecast_stamp=view.forecast_stamp,
            model_label=view.model_label,
            model_source=view.model_source,
            tip_utc=str(g["tip_utc"]),
            minutes=minutes_info(
                pr["forecast"],
                None if store.history is None else Context(store.history, g, pl, stat),
            ),
        )

    @app.get("/api/knows/{game_id}/{player_id}/{stat}", response_model=PlayerKnows)
    def knows_player(game_id: GameId, player_id: int, stat: Stat, store: StoreDep) -> PlayerKnows:
        """One prop's forecast snapshots before tip, the news beside them, and why the range is
        as wide as it is, in words built from fields the data holds."""
        g = _need_game(store, game_id)
        if stat not in STATS:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        pl = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if pl is None or stat not in pl["props"]:
            raise _no_forecast(g, player_id)
        return player_knows(store, g, pl, stat)

    @app.get("/api/breakdown/{game_id}/{player_id}/{stat}", response_model=Breakdown)
    def breakdown(
        game_id: GameId,
        player_id: int,
        stat: Stat,
        store: StoreDep,
        line: Annotated[float | None, Query(ge=0, le=200)] = None,
        side: Annotated[Side, Query()] = "over",
    ) -> Breakdown:
        """Everything checked before a leg, as of before tip. Deterministic; no model is run."""
        g = _need_game(store, game_id)
        if store.history is None:
            raise ApiError("no_history", "no breakdown history loaded")
        if stat not in store.history.stats:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        player = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if player is None:
            raise ApiError("unknown_player", "no such player in that game", field="player_id")
        b = build_breakdown(store.history, g, player, stat, line, side)
        for c in b.chips:
            c.linkable = store.game(c.game_id) is not None
        b.dissection = dissect_leg(b, default_router())
        return b
