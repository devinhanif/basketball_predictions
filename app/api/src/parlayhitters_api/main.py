"""Read-only parlayhitters.ai API over fixtures. No writes, no network, no credentials."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .forecast import TAUS, american_to_raw_prob, devig_over, line_probs
from .models import (
    GameSummary,
    GameView,
    PlayerView,
    ProbAtLine,
    PropView,
    SlateView,
    SwipeCard,
    SwipeDeck,
    Team,
    WinProbability,
)
from .store import Json, Store

STATS = ("pts", "reb", "ast", "fg3m")
LOCAL_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
MODE = "fixture_replay"
METHOD = (
    "Linear interpolation of the stored 19 out-of-fold quantiles (0.05..0.95) with a "
    "continuity correction for integer counts. Approximate; tails beyond 5%/95% are clamped."
)


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store()


StoreDep = Annotated[Store, Depends(get_store)]


def create_app() -> FastAPI:
    app = FastAPI(
        title="parlayhitters.ai read-only API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOCAL_ORIGIN_REGEX,
        allow_methods=["GET"],
        allow_headers=[],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-cache"  # the service worker owns offline caching
        return resp

    register(app)
    return app


# ----------------------------------------------------------------------------- helpers


def _team(t: Json) -> Team:
    return Team(**t)


def _prop_view(stat: str, p: Json) -> PropView:
    q = [float(v) for v in p["forecast"]["q"]]
    lp = line_probs(q, float(p["line"]))
    return PropView(
        prop_id=p["prop_id"],
        stat=stat,
        line=float(p["line"]),
        over_price=int(p["over_price"]),
        under_price=int(p["under_price"]),
        line_snapshot_utc=p.get("line_snapshot_utc"),
        forecast_mean=p["forecast"].get("mean"),
        q=q,
        taus=list(TAUS),
        model_p_over=lp.p_over,
        model_p_under=lp.p_under,
        model_p_push=lp.p_push,
        extrapolated=lp.extrapolated,
        market_p_over=devig_over(int(p["over_price"]), int(p["under_price"])),
        forecast_stamp=p["forecast"]["stamp"],
    )


def _wp(g: Json) -> WinProbability:
    return WinProbability(**g["win_probability"])


def _need_slate(store: Store, date: str) -> Json:
    s = store.slate(date)
    if s is None:
        raise HTTPException(404, "no such slate")
    return s


def _need_game(store: Store, game_id: str) -> Json:
    g = store.game(game_id)
    if g is None:
        raise HTTPException(404, "no such game")
    return g


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
                "over_price": v.over_price,
                "under_price": v.under_price,
                "forecast_mean": v.forecast_mean,
                "model_p_over": v.model_p_over,
                "market_p_over": v.market_p_over,
                "extrapolated": v.extrapolated,
            }
        )
    return out


def _clock(store: Store, game_id: str) -> str:
    return str(store.slates[store.game_slate[game_id]]["demo_clock_utc"])


# ----------------------------------------------------------------------------- routes


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": MODE}

    @app.get("/api/meta")
    def meta(store: StoreDep) -> dict[str, Any]:
        m = store.manifest
        return {
            "mode": MODE,
            "mode_note": (
                "A replay of real 2024-25 nights, built from stored data. Nothing here is "
                "live, and nothing here is advice."
            ),
            "data_version": m["source_data_version"],
            "built_at": m["built_at"],
            "max_season": m["max_season"],
            "slates": [s["date"] for s in m["slates"]],
            "stubs": m["stubs"],
            "sources": m["sources"],
            "queries": m["queries"],
        }

    @app.get("/api/slates")
    def slates(store: StoreDep) -> list[dict[str, Any]]:
        return [
            {
                "date": s["date"],
                "label": s["label"],
                "games": s["counts"]["games"],
                "props": s["counts"]["props"],
            }
            for s in store.slates.values()
        ]

    @app.get("/api/slates/{date}", response_model=SlateView)
    def slate(date: str, store: StoreDep) -> SlateView:
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
            clock_utc=s["demo_clock_utc"],
            mode=MODE,
            counts=s["counts"],
            games=games,
        )

    @app.get("/api/games/{game_id}", response_model=GameView)
    def game(game_id: str, store: StoreDep) -> GameView:
        g = _need_game(store, game_id)
        players = [
            PlayerView(
                player_id=pl["player_id"],
                name=pl["name"],
                side=pl["side"],
                props={st: _prop_view(st, pr) for st, pr in pl["props"].items()},
            )
            for pl in g["players"]
        ]
        return GameView(
            game_id=g["game_id"],
            date=g["date"],
            tip_utc=g["tip_utc"],
            arena=g.get("arena"),
            home=_team(g["home"]),
            away=_team(g["away"]),
            win_probability=_wp(g),
            clock_utc=_clock(store, game_id),
            players=players,
            notes=[
                "Only players with a line from the sportsbook are listed. "
                "Every forecast assumes the player suits up.",
                "We do not forecast minutes yet. "
                "How long a player really played is part of the final result.",
            ],
        )

    @app.get("/api/games/{game_id}/result")
    def game_result(game_id: str, store: StoreDep) -> dict[str, Any]:
        g = _need_game(store, game_id)
        return {
            "game_id": game_id,
            "label": "result (replay)",
            "home_pts": g["result"]["home_pts"],
            "away_pts": g["result"]["away_pts"],
            "home_won": g["result"]["home_won"],
            "players": [
                {
                    "player_id": pl["player_id"],
                    "minutes": pl["result"]["minutes"],
                    "starter": pl["result"]["starter"],
                    "actual": {st: pr["actual"] for st, pr in pl["props"].items()},
                }
                for pl in g["players"]
            ],
        }

    @app.get("/api/props/{game_id}/{player_id}/{stat}", response_model=ProbAtLine)
    def prop_at_line(
        game_id: str,
        player_id: int,
        stat: str,
        store: StoreDep,
        line: Annotated[float, Query(ge=0, le=200)],
    ) -> ProbAtLine:
        g = _need_game(store, game_id)
        if stat not in STATS:
            raise HTTPException(404, "unknown stat")
        for pl in g["players"]:
            if pl["player_id"] == player_id and stat in pl["props"]:
                q = [float(v) for v in pl["props"][stat]["forecast"]["q"]]
                lp = line_probs(q, line)
                posted = pl["props"][stat]
                is_main = float(posted["line"]) == line
                return ProbAtLine(
                    game_id=game_id,
                    player_id=player_id,
                    stat=stat,
                    line=line,
                    p_over=lp.p_over,
                    p_under=lp.p_under,
                    p_push=lp.p_push,
                    extrapolated=lp.extrapolated,
                    method=METHOD,
                    taus=list(TAUS),
                    q=q,
                    main_line=float(posted["line"]),
                    is_main_line=is_main,
                    over_price=int(posted["over_price"]) if is_main else None,
                    under_price=int(posted["under_price"]) if is_main else None,
                )
        raise HTTPException(404, "no forecast for that player and stat")

    @app.get("/api/slates/{date}/swipe", response_model=SwipeDeck)
    def swipe(
        date: str,
        store: StoreDep,
        seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
        n: Annotated[int, Query(ge=1, le=60)] = 24,
    ) -> SwipeDeck:
        """Blind cards: line and price only, no forecast, no result. Random draw by seed."""
        s = _need_slate(store, date)
        clock = str(s["demo_clock_utc"])
        cards: list[SwipeCard] = []
        for g in s["games"]:
            if str(g["tip_utc"]) <= clock:  # refused at or after tip (ISO-8601 UTC sorts as text)
                continue
            for pl in g["players"]:
                me, opp = (g["home"], g["away"]) if pl["side"] == "home" else (g["away"], g["home"])
                for st, pr in pl["props"].items():
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
                            tip_utc=g["tip_utc"],
                        )
                    )
        rng = random.Random(seed)  # noqa: S311 - a shuffle, not a secret
        rng.shuffle(cards)
        return SwipeDeck(
            date=date,
            clock_utc=clock,
            seed=seed,
            cards=cards[:n],
            note=(
                "Blind cards: the line and the price only. Drawn at random from every posted line; "
                "players who do not play are void."
            ),
        )

    @app.get("/api/slates/{date}/results")
    def results(date: str, store: StoreDep) -> dict[str, Any]:
        """Outcomes plus the forecast and book probability at the time, for settling paper picks."""
        s = _need_slate(store, date)
        rows: list[dict[str, Any]] = []
        for g in s["games"]:
            for pl in g["players"]:
                for st, pr in pl["props"].items():
                    v = _prop_view(st, pr)
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
                            "model_p_over": v.model_p_over,
                            "model_p_under": v.model_p_under,
                            "market_p_over": v.market_p_over,
                            "market_p_under": 1 - v.market_p_over,
                        }
                    )
        return {
            "date": date,
            "label": "results and the forecasts that existed before tip (historical replay)",
            "rows": rows,
        }

    @app.get("/api/parlay-of-the-day")
    def parlay(store: StoreDep) -> Json:
        return store.parlay

    @app.get("/api/evidence")
    def evidence(store: StoreDep) -> Json:
        return store.evidence


app = create_app()

__all__ = ["app", "create_app", "american_to_raw_prob"]
