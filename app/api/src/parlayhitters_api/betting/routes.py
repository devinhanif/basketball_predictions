"""GET-only routes of the betting module, all under /api/betting/.

Registered straight on the app (``register``, as every other area is) rather than through an
included router, so each route is a plain ``APIRoute`` on ``app.routes`` and the existing
route-walking tests (GET only, every route fuzzed) see it.

Every route depends on the export store, so when the export is refused (a sha mismatch, a missing
manifest) the betting module refuses with it (503 ``export_refused``) instead of answering beside
data the app cannot vouch for.

The quote board (docs/contracts/betting_board_v1.md) serves stored quote snapshots side by side,
as stored. It serves no model number: ``model_ref`` names the existing routes that do, so their
display locks (LINE_CAL_LIVE, the teammate-sits rules) apply there and nowhere else.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from ..deps import StoreDep, get_store
from ..errors import ApiError
from ..export_verify import HOLDOUT_SEASON
from ..params import GameId, SlateDate, Stat
from ..store import AnyStore
from ..views import STATS
from . import board, catalog
from .models import (
    Board,
    BoardLine,
    BoardQuote,
    ModelRef,
    Provider,
    Providers,
    Slate,
    SlateGame,
    SlateProp,
)

PREFIX = "/api/betting"
NOTE = "analysis only; no orders are placed; this service is read-only"

Json = dict[str, Any]


def _as_of(store: AnyStore) -> tuple[str, str]:
    m = store.meta()
    return str(m["data_as_of"]), str(m["data_version"])


def _season(store: AnyStore, game_id: str) -> int | None:
    slate = store.slates.get(store.game_slate.get(game_id, ""))
    return None if slate is None else int(slate["season"])


def _game(store: AnyStore, game_id: str) -> Json:
    g = store.game(game_id)
    if g is None or _season(store, game_id) == HOLDOUT_SEASON:  # the holdout is never served
        raise ApiError("unknown_game", "no such game", field="game_id")
    return g


def _final(g: Json) -> bool:
    return bool(g.get("final", g.get("result") is not None))


def _abbr(team: Any) -> str | None:
    return str(team["abbr"]) if isinstance(team, dict) and team.get("abbr") else None


def _sides(g: Json, pl: Json) -> tuple[str | None, str | None]:
    if pl.get("side") == "home":
        return _abbr(g.get("home")), _abbr(g.get("away"))
    if pl.get("side") == "away":
        return _abbr(g.get("away")), _abbr(g.get("home"))
    return None, None


def _fixture_note(store: AnyStore, note: str) -> str:
    return note if store.mode == "export" else note + catalog.NO_SNAPSHOTS_NOTE


def register(app: FastAPI) -> None:
    @app.get(f"{PREFIX}/status", dependencies=[Depends(get_store)])
    def betting_status() -> dict[str, object]:
        """The switch's state as the API sees it. Only reachable when the switch is on."""
        return {"enabled": True, "note": NOTE}

    @app.get(f"{PREFIX}/providers", response_model=Providers)
    def providers(store: StoreDep) -> Providers:
        """Every provider with at least one served quote in this export, sorted by id."""
        as_of, version = _as_of(store)
        s = board.summary(store)
        return Providers(
            as_of=as_of,
            data_version=version,
            providers=[
                Provider(
                    id=pid,
                    label=catalog.label(pid),
                    kind=catalog.kind(pid),
                    price_unit="american",
                    newest_snapshot_utc=newest or None,
                    n_quotes=n,
                    fee=None,
                    settlement_rule_id=None,
                )
                for pid, (n, newest) in s.providers.items()
            ],
            unknown=list(catalog.UNKNOWN),
            note=catalog.PROVIDERS_NOTE,
        )

    @app.get(f"{PREFIX}/board/{{game_id}}/{{player_id}}/{{stat}}", response_model=Board)
    def quote_board(game_id: GameId, player_id: int, stat: Stat, store: StoreDep) -> Board:
        """One prop's stored quotes side by side: per line, provider and side, the newest snapshot
        stored before tip-off. No quote at all is an empty board, not an error."""
        g = _game(store, game_id)
        if stat not in STATS:
            raise ApiError("unknown_stat", "unknown stat", field="stat")
        pl = next((p for p in g["players"] if p["player_id"] == player_id), None)
        if pl is None:
            raise ApiError("unknown_player", "no such player in that game", field="player_id")
        if stat not in pl["props"]:
            raise ApiError("no_forecast", "no forecast for that player and stat", field="stat")
        key = (game_id, player_id, stat)
        quotes, implied = board.prop_source(store, key)
        items = board.served(quotes, implied, g.get("tip_utc"))
        lines: list[BoardLine] = []
        for q in items:
            if not lines or lines[-1].line != q.line:
                lines.append(BoardLine(line=q.line, quotes=[]))
            lines[-1].quotes.append(
                BoardQuote(
                    quote_id=q.quote_id(key),
                    provider=q.provider,
                    side=q.side,
                    price_american=q.price_american,
                    implied_prob_raw=q.implied_prob_raw,
                    snapshot_utc=q.snapshot_utc,
                    minutes_before_tip=q.minutes_before_tip,
                    is_alternate=q.is_alternate,
                    usable=q.usable,
                    unusable_reason=q.unusable_reason,
                )
            )
        here = {q.provider for q in items}
        team, opponent = _sides(g, pl)
        as_of, version = _as_of(store)
        tail = f"{game_id}/{player_id}/{stat}"
        return Board(
            game_id=game_id,
            date=str(store.game_slate.get(game_id, g.get("date", ""))),
            tip_utc=None if g.get("tip_utc") is None else str(g["tip_utc"]),
            final=_final(g),
            player_id=player_id,
            player=str(pl["name"]),
            team=team,
            opponent=opponent,
            stat=stat,
            as_of=as_of,
            data_version=version,
            provenance="delayed",
            lines=lines,
            providers_absent=[p for p in board.summary(store).providers if p not in here],
            model_ref=ModelRef(props=f"/api/props/{tail}", source=f"/api/record/{tail}"),
            unknown=list(catalog.UNKNOWN),
            note=_fixture_note(store, catalog.BOARD_NOTE),
        )

    @app.get(f"{PREFIX}/slate/{{date}}", response_model=Slate)
    def quote_slate(date: SlateDate, store: StoreDep) -> Slate:
        """The night's props that have at least one served quote (the find step)."""
        sl = store.slate(date)
        if sl is None or int(sl["season"]) == HOLDOUT_SEASON:
            raise ApiError("unknown_slate", "no such slate", field="date")
        s = board.summary(store)
        games: list[SlateGame] = []
        props: list[SlateProp] = []
        for g in sl["games"]:
            gid = str(g["game_id"])
            n = 0
            for pl in sorted(g["players"], key=lambda p: (str(p["name"]), int(p["player_id"]))):
                team, _ = _sides(g, pl)
                for st in STATS:
                    ps = s.props.get((gid, int(pl["player_id"]), st))
                    if ps is None or st not in pl["props"]:
                        continue
                    n += 1
                    props.append(
                        SlateProp(
                            game_id=gid,
                            player_id=int(pl["player_id"]),
                            player=str(pl["name"]),
                            team=team,
                            stat=st,
                            n_providers=len(ps.providers),
                            n_lines=ps.n_lines,
                        )
                    )
            games.append(
                SlateGame(
                    game_id=gid,
                    tip_utc=None if g.get("tip_utc") is None else str(g["tip_utc"]),
                    home=_abbr(g.get("home")) or "",
                    away=_abbr(g.get("away")) or "",
                    final=_final(g),
                    n_props_with_quotes=n,
                )
            )
        as_of, _ = _as_of(store)
        return Slate(date=date, as_of=as_of, games=games, props=props)
