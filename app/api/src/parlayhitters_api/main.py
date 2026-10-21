"""Read-only parlayhitters.ai API over fixtures. No writes, no network, no credentials."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .breakdown import Breakdown, Side
from .breakdown import build as build_breakdown
from .dissect.providers import default_router
from .dissect.service import dissect_leg
from .export_store import ExportRefused
from .forecast import (
    TAUS,
    american_to_raw_prob,
    clean_line_cal,
    clean_null_reason,
    clean_rung_cal,
    devig_over,
    dist_grid,
    forecast_method,
    forecast_probs,
)
from .model_source import model_detail, model_label, p_play_label
from .models import (
    CardStats,
    CardView,
    Dist,
    GameDot,
    GameSummary,
    GameView,
    HitTen,
    LineCal,
    MateOut,
    PlayerView,
    ProbAtLine,
    PropView,
    RungCal,
    SlateView,
    SwipeCard,
    SwipeDeck,
    Team,
    WinProbability,
)
from .role import player_role
from .store import AnyStore, Json, load_store, store_key
from .top_parlays import (
    TopParlaysMissing,
    TopParlaysRefused,
    TopParlaysUnavailable,
    load_top_parlays,
)

STATS = ("pts", "reb", "ast", "fg3m")
LOCAL_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
_cache: dict[str, Any] = {}


def get_store() -> AnyStore:
    """The data behind every route: the verified export if one is configured, else the fixtures.
    Reloaded when a new export lands (the manifest is replaced last), without a restart."""
    key = store_key()
    if _cache.get("key") != key or "store" not in _cache:
        _cache["store"] = load_store()  # raises ExportRefused for an export we cannot vouch for
        _cache["key"] = key
    store: AnyStore = _cache["store"]
    return store


def reset_store() -> None:
    _cache.clear()


StoreDep = Annotated[AnyStore, Depends(get_store)]


def create_app() -> FastAPI:
    app = FastAPI(
        title="parlayhitters.ai read-only API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
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

    @app.exception_handler(ExportRefused)
    async def export_refused(request: Request, exc: ExportRefused) -> JSONResponse:
        return JSONResponse({"detail": f"the data export was refused: {exc}"}, status_code=503)

    register(app)
    return app


# ----------------------------------------------------------------------------- helpers


def _team(t: Json) -> Team:
    return Team(**t)


def _prop_view(stat: str, p: Json) -> PropView:
    q = [float(v) for v in p["forecast"]["q"]]
    lp = forecast_probs(p["forecast"], float(p["line"]))
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
        raise HTTPException(404, "no such slate")
    return s


def _need_game(store: AnyStore, game_id: str) -> Json:
    g = store.game(game_id)
    if g is None:
        raise HTTPException(404, "no such game")
    return g


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
                "model_source": v.model_source,
                "model_label": v.model_label,
            }
        )
    return out


# ----------------------------------------------------------------------------- routes


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health(store: StoreDep) -> dict[str, str]:
        return {"status": "ok", "mode": store.mode}

    @app.get("/api/meta")
    def meta(store: StoreDep) -> dict[str, Any]:
        return store.meta()

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
            clock_utc=store.slate_clock(date),
            mode=store.mode,
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
                role=None if store.history is None else player_role(store.history, g, pl),
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
            clock_utc=store.clock(game_id),
            final=bool(g.get("final", True)),
            players=players,
            notes=_game_notes(store),
        )

    @app.get("/api/games/{game_id}/result")
    def game_result(game_id: str, store: StoreDep) -> dict[str, Any]:
        g = _need_game(store, game_id)
        if g.get("result") is None:
            raise HTTPException(404, "that game has not been played yet")
        return {
            "game_id": game_id,
            "label": "result (replay)" if store.mode == "fixture_replay" else "final",
            "home_pts": g["result"]["home_pts"],
            "away_pts": g["result"]["away_pts"],
            "home_won": g["result"]["home_won"],
            "players": [
                {
                    "player_id": pl["player_id"],
                    "minutes": None if pl["result"] is None else pl["result"]["minutes"],
                    "starter": None if pl["result"] is None else pl["result"]["starter"],
                    "actual": {
                        st: pr["actual"]
                        for st, pr in pl["props"].items()
                        if pr["actual"] is not None
                    },
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
                forecast = pl["props"][stat]["forecast"]
                q = [float(v) for v in forecast["q"]]
                lp = forecast_probs(forecast, line)
                posted = pl["props"][stat]
                priced = posted["over_price"] is not None
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
                    method=forecast_method(forecast),
                    taus=list(TAUS),
                    q=q,
                    main_line=float(posted["line"]),
                    is_main_line=is_main,
                    over_price=int(posted["over_price"]) if is_main and priced else None,
                    under_price=int(posted["under_price"]) if is_main and priced else None,
                )
        raise HTTPException(404, "no forecast for that player and stat")

    @app.get("/api/card/{game_id}/{player_id}/{stat}", response_model=CardView)
    def card(game_id: str, player_id: int, stat: str, store: StoreDep) -> CardView:
        """Quick stats and the full distribution for one prop, as of before tip. Fetched only when
        the user opens it, so the blind swipe deck itself never carries a forecast."""
        g = _need_game(store, game_id)
        if stat not in STATS:
            raise HTTPException(404, "unknown stat")
        pl = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if pl is None or stat not in pl["props"]:
            raise HTTPException(404, "no forecast for that player and stat")
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
        )

    @app.get("/api/breakdown/{game_id}/{player_id}/{stat}", response_model=Breakdown)
    def breakdown(
        game_id: str,
        player_id: int,
        stat: str,
        store: StoreDep,
        line: Annotated[float | None, Query(ge=0, le=200)] = None,
        side: Annotated[Side, Query()] = "over",
    ) -> Breakdown:
        """Everything checked before a leg, as of before tip. Deterministic; no model is run."""
        g = _need_game(store, game_id)
        if store.history is None:
            raise HTTPException(404, "no breakdown history loaded")
        if stat not in store.history.stats:
            raise HTTPException(404, "unknown stat")
        player = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if player is None:
            raise HTTPException(404, "no such player in that game")
        b = build_breakdown(store.history, g, player, stat, line, side)
        for c in b.chips:
            c.linkable = store.game(c.game_id) is not None
        b.dissection = dissect_leg(b, default_router())
        return b

    @app.get("/api/slates/{date}/swipe", response_model=SwipeDeck)
    def swipe(
        date: str,
        store: StoreDep,
        seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
        n: Annotated[int, Query(ge=1, le=60)] = 24,
    ) -> SwipeDeck:
        """Blind cards: line and price only, no forecast, no result. Random draw by seed."""
        s = _need_slate(store, date)
        clock = store.slate_clock(date)
        cards: list[SwipeCard] = []
        for g in s["games"]:
            if str(g["tip_utc"]) <= clock:  # refused at or after tip (ISO-8601 UTC sorts as text)
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
        return SwipeDeck(
            date=date,
            clock_utc=clock,
            seed=seed,
            cards=cards[:n],
            note=(
                "Blind cards: the line and the price only. Drawn at random from every posted line; "
                "players who do not play are void."
                if cards
                else "No cards: this data holds no sportsbook lines or prices to swipe on."
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
                            "model_p_over": v.model_p_over,
                            "model_p_under": v.model_p_under,
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

    @app.get("/api/parlay-of-the-day")
    def parlay(store: StoreDep) -> Json:
        return store.parlay

    @app.get("/api/top-parlays/{date}")
    def top_parlays(date: str) -> Json:
        try:
            return load_top_parlays(date)
        except TopParlaysMissing:
            raise HTTPException(status_code=404, detail=f"no top parlays file for {date}") from None
        except TopParlaysUnavailable as e:
            raise HTTPException(status_code=503, detail=f"top parlays unavailable: {e}") from None
        except TopParlaysRefused as e:
            raise HTTPException(status_code=422, detail=f"top parlays file refused: {e}") from None

    @app.get("/api/evidence")
    def evidence(store: StoreDep) -> Json:
        return store.evidence


app = create_app()

__all__ = ["app", "create_app", "american_to_raw_prob"]
