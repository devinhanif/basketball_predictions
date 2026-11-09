# ruff: noqa: E501
"""The orchestrator tick: serve open pull requests with the EXISTING per-event fetch path.

``tick(now)`` runs every 2 minutes (ops/nba_pulls.sh). For each open request it decides, in order:

1. past ``need_by`` -> ``expired``; the game has tipped -> ``refused_post_tip`` (the known-at rule: no
   pre-game pull is ever made at or after tip, ours or the vendor's);
2. the game already has a Pinnacle row young enough -> ``done`` (nothing spent);
3. budget (:func:`ondemand_allowed`): the on-demand caps, then the close reserve below ->
   ``refused_budget``;
4. otherwise ONE per-event pull through ``nba.odds.live_close`` (its client, ledger, event matching,
   raw-before-parse, ``write_snapshot`` known-at check), snapshot kind ``ondemand<N>`` ->
   ``done`` / ``failed`` / ``refused_*``.

Budget priority is close > on-demand > scheduled, inside ``daily_max_credits``: an on-demand call may
run only while the credits left today, after the call, still cover one close pull for every game that
has not tipped and has no stored close (``ondemand_allowed``). The call cost is the fetcher's own
upper bound (``LiveConfig.expected_cost``), the cap and the server floor are the ledger's. Every
ledger line the pull causes carries ``request_id``.

Read-only toward markets: GET only (via the existing client). The key is read from the environment by
that client, never here. Off unless ``NBA_CLOSE_CAPTURE=1``. Never opens ``nba.duckdb`` for writing and
never takes ``heavy.lock``; the one store it writes besides ``pulls.duckdb`` is
``market_asof.duckdb`` through ``live_close.write_snapshot``.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import duckdb

from nba.odds import live_close as lc
from nba.odds.the_odds_api import HistoryError, default_resolver, redact
from nba.odds.theoddsapi import NameResolver
from nba.pulls.config import PullsConfig, load_pulls_config
from nba.pulls.freshness import newest_pinnacle
from nba.pulls.store import PullRequest, PullStore, utc_naive

log = logging.getLogger("nba.pulls")
SUPPORTED_SOURCES = ("pinnacle",)


def ondemand_allowed(
    *,
    cap: int,
    spent_today: int,
    call_cost: int,
    outstanding_closes: int,
    close_slots: int,
    server_remaining: int | None = None,
    server_floor: int = 0,
) -> tuple[bool, str]:
    """The close reserve. ``reserve = outstanding_closes x close_slots x call_cost`` where
    ``outstanding_closes`` counts games that have not tipped and have no stored close (the game this
    request is for included). Allowed iff, after this call, the daily cap still holds the reserve
    (``spent + cost + reserve <= cap``) and so does the server floor
    (``server_remaining - cost - reserve >= floor``). Pure; tested."""
    reserve = outstanding_closes * close_slots * call_cost
    if spent_today + call_cost + reserve > cap:
        return False, (
            f"close reserve: spent {spent_today} + call {call_cost} + reserve {reserve} "
            f"({outstanding_closes} close(s) x {close_slots} x {call_cost}) > daily cap {cap}"
        )
    if server_remaining is not None and server_remaining - call_cost - reserve < server_floor:
        return False, (
            f"close reserve: server remaining {server_remaining} - call {call_cost} - reserve "
            f"{reserve} < floor {server_floor}"
        )
    return True, ""


@dataclass
class TickReport:
    at: datetime
    disabled: str = ""  # why nothing ran
    seen: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    credits: int = 0
    messages: list[str] = field(default_factory=list)
    degraded: bool = False  # a request failed for a system reason: rc 4

    def lines(self) -> list[str]:
        if self.disabled:
            return [f"pulls tick: {self.disabled}; no-op"]
        st = ", ".join(f"{k}={v}" for k, v in sorted(self.statuses.items())) or "none"
        out = [
            f"pulls tick {self.at:%Y-%m-%dT%H:%M:%SZ}: open={self.seen} credits={self.credits} [{st}]"
        ]
        return out + [f"  {m}" for m in self.messages]


def _next_kind(con: duckdb.DuckDBPyConnection, game_id: str) -> str:
    """``ondemand<N>`` with N one past the highest used for the game in either table (a failed
    attempt logged under N is never reused: ``done_games`` and ``write_snapshot`` key on the kind)."""
    used = 0
    for sql in (
        "SELECT DISTINCT snapshot_kind FROM live_book_odds WHERE game_id = ?",
        "SELECT DISTINCT snapshot_kind FROM live_capture_log WHERE game_id = ?",
    ):
        for (k,) in con.execute(sql, [game_id]).fetchall():
            m = re.fullmatch(rf"{lc.ONDEMAND_PREFIX}([0-9]+)", str(k))
            if m:
                used = max(used, int(m.group(1)))
    return f"{lc.ONDEMAND_PREFIX}{used + 1}"


def _ondemand_usage(
    con: duckdb.DuckDBPyConnection, game_ids: list[str], game_id: str
) -> tuple[int, int]:
    """(on-demand pulls made for this game, on-demand credits spent on the slate's games)."""
    like = f"{lc.ONDEMAND_PREFIX}%"
    per_game = con.execute(
        "SELECT count(*) FROM live_capture_log WHERE game_id = ? AND snapshot_kind LIKE ? "
        "AND status IN ('ok', 'empty')",
        [game_id, like],
    ).fetchone()
    if not game_ids:
        return int(per_game[0]) if per_game else 0, 0
    ph = ",".join("?" for _ in game_ids)
    credits = con.execute(
        f"SELECT coalesce(sum(credits), 0) FROM live_capture_log WHERE snapshot_kind LIKE ? "  # noqa: S608
        f"AND game_id IN ({ph})",
        [like, *game_ids],
    ).fetchone()
    return int(per_game[0]) if per_game else 0, int(credits[0]) if credits else 0


def tick(
    now: datetime | None = None,
    *,
    cfg: PullsConfig | None = None,
    live_cfg: lc.LiveConfig | None = None,
    store: PullStore | None = None,
    environ: Mapping[str, str] | None = None,
    games_fn: Callable[[date], list[lc.LiveGame]] | None = None,
    client: lc.LiveClient | None = None,
    resolver: NameResolver | None = None,
    sleep: Callable[[float], None] | None = None,
    http_get: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TickReport:
    """One orchestration pass. A no-op (no store, key or network touched) when disabled in
    ``configs/pulls.yaml`` or ``NBA_CLOSE_CAPTURE`` is not ``1``. ``clock`` times the pull itself
    (request/response stamps); it defaults to the wall clock and is injected in tests."""
    pc = cfg or load_pulls_config()
    t = utc_naive(now)
    rep = TickReport(at=t)
    if not pc.enabled:
        rep.disabled = "disabled in configs/pulls.yaml"
        return rep
    if not lc.enabled(environ):
        rep.disabled = f"disabled ({lc.FLAG_ENV} != 1)"
        return rep
    st = store or PullStore(pc.requests_db)
    try:
        opens = st.open_requests()
    except duckdb.Error as exc:
        rep.degraded = True
        rep.messages.append(f"requests store unreadable: {type(exc).__name__}")
        return rep
    rep.seen = len(opens)
    if not opens:
        return rep
    lcfg = live_cfg or lc.load_live_config()
    slates: dict[date, list[lc.LiveGame]] = {}

    def slate_games(d: date) -> list[lc.LiveGame]:
        if d not in slates:
            try:
                slates[d] = (games_fn or (lambda s: lc.read_slate(lcfg.tips_db, s, lcfg.games_db)))(
                    d
                )
            except Exception as exc:  # noqa: BLE001 - one unreadable slate must not stop the tick
                rep.messages.append(f"slate {d}: read failed ({type(exc).__name__})")
                slates[d] = []
        return slates[d]

    def finish(req: PullRequest, status: str, detail: str) -> None:
        st.set_status(req.request_id, status, t, detail)
        rep.statuses[status] += 1
        if status == "failed":
            rep.degraded = True

    # 1. cheap decisions that need no market store
    work: list[tuple[PullRequest, lc.LiveGame]] = []
    for req in opens:
        if req.source not in SUPPORTED_SOURCES:
            finish(req, "failed", f"source {req.source!r} not supported in P1")
            continue
        game = next(
            (g for g in slate_games(req.slate_date) if g.game_id == req.scope_game_id), None
        )
        if game is not None and t >= game.tipoff:
            finish(req, "refused_post_tip", f"tip {game.tipoff:%H:%M:%S}Z has passed; never pulled")
        elif t >= req.need_by_utc:
            finish(req, "expired", f"not served by need_by {req.need_by_utc:%H:%M:%S}Z")
        elif game is None:
            finish(req, "failed", "game has no real tip in the slate (nothing to pull against)")
        else:
            work.append((req, game))
    if not work:
        return rep

    # 2. the market store: freshness check, reserve, pull. Opened once, closed before returning.
    try:
        con = lc.open_store(lcfg.out_db, retries=pc.store_retries, wait_s=pc.store_wait_s)
    except (duckdb.Error, OSError) as exc:
        rep.messages.append(f"market store busy ({type(exc).__name__}); requests stay open")
        return rep  # the next tick retries; need_by expires them if it never frees
    try:
        _serve(rep, st, pc, lcfg, con, work, slate_games, finish, t, environ, client, resolver,
               sleep, http_get, clock)  # fmt: skip
    finally:
        con.close()
    return rep


def _serve(
    rep: TickReport,
    st: PullStore,
    pc: PullsConfig,
    lcfg: lc.LiveConfig,
    con: duckdb.DuckDBPyConnection,
    work: list[tuple[PullRequest, lc.LiveGame]],
    slate_games: Callable[[date], list[lc.LiveGame]],
    finish: Callable[[PullRequest, str, str], None],
    t: datetime,
    environ: Mapping[str, str] | None,
    client: lc.LiveClient | None,
    resolver: NameResolver | None,
    sleep: Callable[[float], None] | None,
    http_get: Callable[..., Any] | None,
    clock: Callable[[], datetime] | None,
) -> None:
    wall = clock or (lambda: datetime.now(UTC))
    cli = client
    res = resolver
    # earliest tip first: the game that tips soonest has the least time left
    for req, game in sorted(work, key=lambda w: (w[1].tipoff, w[0].created_at_utc)):
        newest = newest_pinnacle(con, [game.game_id], t).get(game.game_id)
        if newest is not None:
            age = (t - newest.requested_at).total_seconds() / 60
            if age <= req.max_age_min:
                finish(
                    req,
                    "done",
                    f"already fresh: {newest.kind} row {age:.0f} min old; nothing spent",
                )
                continue
        day_games = slate_games(req.slate_date)
        per_game, od_credits = _ondemand_usage(con, [g.game_id for g in day_games], game.game_id)
        if per_game >= pc.ondemand_max_per_game:
            finish(req, "refused_budget", f"per-game cap: {per_game} on-demand pulls today")
            continue
        if pc.ondemand_daily_credit_cap and od_credits >= pc.ondemand_daily_credit_cap:
            finish(
                req, "refused_budget", f"on-demand credit cap {pc.ondemand_daily_credit_cap} used"
            )
            continue
        if cli is None:
            try:
                cli = lc.LiveClient(
                    lcfg, environ=environ, sleep=sleep or time.sleep, http_get=http_get,
                    day=wall().astimezone(lc.ET).date(),
                )  # fmt: skip
            except HistoryError as exc:  # key missing / bad config
                finish(req, "failed", f"{type(exc).__name__}: {redact(str(exc))}")
                continue
        if res is None:
            res = default_resolver()
        done_closes = lc.done_games(con, "close", lcfg.max_attempts_per_game)
        outstanding = sum(1 for g in day_games if g.tipoff > t and g.game_id not in done_closes)
        ok, why = ondemand_allowed(
            cap=lcfg.daily_max_credits,
            spent_today=cli.ledger.spent(),
            call_cost=lcfg.expected_cost(),
            outstanding_closes=max(
                outstanding, 1
            ),  # this game's own close, even if the tip list lags
            close_slots=pc.close_slots_reserved,
            server_remaining=cli.ledger.server_remaining(),
            server_floor=lcfg.min_server_remaining,
        )
        if not ok:
            finish(req, "refused_budget", why)
            continue
        kind = _next_kind(con, game.game_id)
        cli.request_id = req.request_id
        sub = lc.LiveReport(slate=req.slate_date, kind=kind)
        try:
            lc._pull_all(sub, con, cli, res, [game], req.slate_date, kind, lcfg, wall)
        except HistoryError as exc:
            finish(req, "failed", f"{type(exc).__name__}: {redact(str(exc))}")
            continue
        finally:
            cli.request_id = None
        rep.credits += sub.credits
        rep.messages.extend(redact(m) for m in sub.messages)
        _settle(finish, req, game, sub, kind, con)


def _settle(
    finish: Callable[[PullRequest, str, str], None],
    req: PullRequest,
    game: lc.LiveGame,
    sub: lc.LiveReport,
    kind: str,
    con: duckdb.DuckDBPyConnection,
) -> None:
    s = sub.statuses
    tag = f"{kind}, {sub.credits} credits"
    if s["credit_guard"]:
        finish(req, "refused_budget", f"ledger guard refused the call ({tag})")
    elif s["post_tip_refused"]:
        finish(req, "refused_post_tip", f"known-at check refused the write ({tag})")
    elif s["unmatched"]:
        finish(req, "failed", f"no unique vendor event for the game ({tag})")
    elif s["error"]:
        finish(req, "failed", f"vendor call failed ({tag})")
    elif s["ok"] or s["empty"]:
        row = con.execute(
            "SELECT count(*) FROM live_book_odds WHERE game_id = ? AND snapshot_kind = ? "
            "AND book = 'pinnacle'",
            [game.game_id, kind],
        ).fetchone()
        if s["ok"] and row is not None and int(row[0]) > 0:
            finish(req, "done", f"pulled {kind}, {sub.credits} credits, {sub.inserted} rows")
        else:
            finish(req, "failed", f"pulled {kind} ({tag}) but it holds no Pinnacle row")
    else:
        finish(req, "failed", f"no outcome recorded ({tag})")
