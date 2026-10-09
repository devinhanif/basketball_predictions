"""Forward capture of the as-of market state for every forward prediction (read-only inputs).

Reads ``forward_predictions`` (nba.duckdb, read-only) and ``kalshi.duckdb`` (read-only) and writes
``market_at_prediction`` into its OWN file (default ``data/markets/market_asof.duckdb``), so it
never contends for ``nba.duckdb``'s single writer. One row per forward prediction key
``(game_id, model_name, version, target, player_id, made_at)`` holding the Kalshi state strictly
before ``made_at``. Idempotent: keys already captured are skipped. Because the state is a pure
function of ``(prices, made_at)``, a late capture equals an on-time one as long as the raw price
history is retained.

``capture_kind``: ``forward`` (real ``made_at``) or ``backfill_proxy`` (past games with no logged
prediction; ``as_of`` is a 19:00 ET proxy for tip-off; in-sample plumbing check only, never a
pre-registered comparison input).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import duckdb

from nba.markets.asof import (
    ET,
    PROP_STATS,
    GameMarketState,
    PropMarketState,
    abbr_by_team_id,
    game_market_state,
    naive_utc,
    open_read_only,
    prop_market_state,
)

DEFAULT_OUT = Path("data/markets/market_asof.duckdb")
DEFAULT_KALSHI = Path("data/kalshi/kalshi.duckdb")
BACKFILL_TIP_PROXY_ET = time(19, 0)
NO_PLAYER = -1

DDL = """
CREATE TABLE IF NOT EXISTS market_at_prediction (
    capture_kind VARCHAR,      -- forward | backfill_proxy
    run_id VARCHAR,
    made_at TIMESTAMP,         -- as_of: market state is strictly before this (naive UTC)
    game_id VARCHAR,
    tipoff TIMESTAMP,
    model_name VARCHAR,
    version VARCHAR,
    target VARCHAR,
    player_id INT,             -- -1 for game-level targets
    event_date DATE,
    away_team INT,
    home_team INT,
    resolved BOOLEAN,          -- game mapped to a date + team pair
    reason VARCHAR,            -- ok | unresolved_game | no_kalshi_markets | no_quote_before_as_of
    captured_at TIMESTAMP,
    p_home_win DOUBLE,         -- de-vigged moneyline mid
    p_home_win_lo DOUBLE,
    p_home_win_hi DOUBLE,
    ml_age_s DOUBLE,
    implied_margin DOUBLE,     -- home margin (spread-ladder median)
    implied_total DOUBLE,
    implied_home_total DOUBLE,
    implied_away_total DOUBLE,
    n_spread_rungs INT,
    n_total_rungs INT,
    game_json JSON,            -- raw quotes: moneyline + spread/total ladders
    stat VARCHAR,              -- prop stat if target is a prop
    prop_n_rungs INT,
    prop_implied_median DOUBLE,
    prop_pge_json JSON,        -- {"N": monotone implied P(stat >= N)}
    prop_json JSON,            -- raw quotes per rung
    PRIMARY KEY (capture_kind, game_id, model_name, version, target, player_id, made_at)
);
"""


@dataclass(frozen=True)
class CaptureSummary:
    candidates: int
    already_captured: int
    written: int
    with_quote: int
    unresolved: int

    def line(self) -> str:
        return (
            f"market capture: candidates={self.candidates} already={self.already_captured} "
            f"written={self.written} with_quote={self.with_quote} unresolved={self.unresolved}"
        )


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def _reason(g: GameMarketState | None) -> str:
    if g is None:
        return "unresolved_game"
    if g.n_markets == 0:
        return "no_kalshi_markets"
    return "ok" if g.has_quote else "no_quote_before_as_of"


def _row(
    kind: str,
    key: tuple[str, str, str, str, str, int, datetime, datetime | None],
    teams: tuple[int, int, date] | None,
    g: GameMarketState | None,
    p: PropMarketState | None,
    stat: str | None,
    now: datetime,
) -> tuple[Any, ...]:
    run_id, game_id, model, version, target, pid, made_at, tipoff = key
    away, home, d = teams if teams is not None else (None, None, None)
    ml_age = None
    if g is not None:
        ages = [q.age_s for q in (g.ml_home, g.ml_away) if q is not None]
        ml_age = min(ages) if ages else None
    return (
        kind, run_id, made_at, game_id, tipoff, model, version, target, pid, d, away, home,
        teams is not None, _reason(g), now,
        g.p_home_win if g else None, g.p_home_win_lo if g else None, g.p_home_win_hi if g else None,
        ml_age,
        g.implied_margin if g else None, g.implied_total if g else None,
        g.implied_home_total if g else None, g.implied_away_total if g else None,
        g.n_spread_rungs if g else None, g.n_total_rungs if g else None,
        json.dumps(g.to_json(), sort_keys=True) if g else None,
        stat, len(p.rungs) if p else None, p.implied_median if p else None,
        json.dumps(p.pge_json(), sort_keys=True) if p else None,
        json.dumps(p.to_json(), sort_keys=True) if p else None,
    )  # fmt: skip


def _insert(out: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> None:
    if rows:
        ph = ",".join("?" * 31)
        out.executemany(
            f"INSERT INTO market_at_prediction VALUES ({ph}) ON CONFLICT DO NOTHING", rows
        )


def _captured_keys(
    out: duckdb.DuckDBPyConnection, kind: str
) -> set[tuple[str, str, str, str, int, datetime]]:
    rows = out.execute(
        "SELECT game_id, model_name, version, target, player_id, made_at "
        "FROM market_at_prediction WHERE capture_kind = ?",
        [kind],
    ).fetchall()
    return {(str(a), str(b), str(c), str(d), int(e), f) for a, b, c, d, e, f in rows}


def capture_forward(
    nba_con: duckdb.DuckDBPyConnection,
    kalshi_con: duckdb.DuckDBPyConnection,
    out: duckdb.DuckDBPyConnection,
    slate_date: date | None = None,
    now: datetime | None = None,
) -> CaptureSummary:
    """Capture market state for forward predictions (ET tip date == ``slate_date``; None = all)."""
    ensure_table(out)
    try:
        fp = nba_con.execute(
            "SELECT run_id, made_at, game_id, tipoff, model_name, version, target, player_id, "
            "json_extract_string(prediction, '$.home_team'), "
            "json_extract_string(prediction, '$.away_team') "
            "FROM forward_predictions ORDER BY made_at, game_id, model_name, target, player_id"
        ).fetchall()
    except duckdb.CatalogException:
        return CaptureSummary(0, 0, 0, 0, 0)
    teams_by_game: dict[str, tuple[int, int]] = {}
    for r in fp:
        if r[8] is not None and r[9] is not None:
            teams_by_game[str(r[2])] = (int(r[9]), int(r[8]))  # (away, home)
    abbr = abbr_by_team_id()
    done = _captured_keys(out, "forward")
    stamp = naive_utc(now or datetime.now(UTC))
    gcache: dict[tuple[str, datetime], GameMarketState | None] = {}
    pcache: dict[tuple[str, datetime, int, str], PropMarketState] = {}
    rows: list[tuple[Any, ...]] = []
    cand = skipped = with_q = unres = 0
    for run_id, made_at, gid, tipoff, model, ver, target, pid, _h, _a in fp:
        et_date = tipoff.replace(tzinfo=UTC).astimezone(ET).date()
        if slate_date is not None and et_date != slate_date:
            continue
        cand += 1
        if (str(gid), str(model), str(ver), str(target), int(pid), made_at) in done:
            skipped += 1
            continue
        tm = teams_by_game.get(str(gid))
        teams = None
        g: GameMarketState | None = None
        p: PropMarketState | None = None
        stat = str(target) if str(target) in PROP_STATS else None
        if tm is not None and tm[0] in abbr and tm[1] in abbr:
            teams = (tm[0], tm[1], et_date)
            gk = (str(gid), made_at)
            if gk not in gcache:
                gcache[gk] = game_market_state(
                    kalshi_con, et_date, abbr[tm[0]], abbr[tm[1]], made_at
                )
            g = gcache[gk]
            if stat is not None and int(pid) != NO_PLAYER:
                pk = (str(gid), made_at, int(pid), stat)
                if pk not in pcache:
                    pcache[pk] = prop_market_state(
                        kalshi_con, et_date, abbr[tm[0]], abbr[tm[1]], int(pid), stat, made_at
                    )
                p = pcache[pk]
        if g is None:
            unres += 1
        elif g.has_quote or (p is not None and any(r.quote for r in p.rungs)):
            with_q += 1
        key = (str(run_id), str(gid), str(model), str(ver), str(target), int(pid), made_at, tipoff)
        rows.append(_row("forward", key, teams, g, p, stat, stamp))
    _insert(out, rows)
    return CaptureSummary(cand, skipped, len(rows), with_q, unres)


def backfill_games(
    nba_con: duckdb.DuckDBPyConnection,
    kalshi_con: duckdb.DuckDBPyConnection,
    out: duckdb.DuckDBPyConnection,
    since: date,
    until: date | None = None,
    now: datetime | None = None,
) -> CaptureSummary:
    """Game-market state for past ``games`` rows at a 19:00 ET tip proxy (``backfill_proxy``)."""
    ensure_table(out)
    stamp = naive_utc(now or datetime.now(UTC))
    last = until or stamp.date()
    games = nba_con.execute(
        "SELECT game_id, game_date, away_team, home_team FROM games "
        "WHERE game_date >= ? AND game_date <= ? ORDER BY game_date, game_id",
        [since, last],
    ).fetchall()
    abbr = abbr_by_team_id()
    done = _captured_keys(out, "backfill_proxy")
    rows: list[tuple[Any, ...]] = []
    skipped = with_q = unres = 0
    for gid, gd, away, home in games:
        tip = naive_utc(datetime.combine(gd, BACKFILL_TIP_PROXY_ET, tzinfo=ET))
        if (str(gid), "__backfill__", "", "game_market", NO_PLAYER, tip) in done:
            skipped += 1
            continue
        g: GameMarketState | None = None
        if int(away) in abbr and int(home) in abbr:
            g = game_market_state(kalshi_con, gd, abbr[int(away)], abbr[int(home)], tip)
        if g is None:
            unres += 1
        elif g.has_quote:
            with_q += 1
        key = ("backfill", str(gid), "__backfill__", "", "game_market", NO_PLAYER, tip, tip)
        rows.append(_row("backfill_proxy", key, (int(away), int(home), gd), g, None, None, stamp))
    _insert(out, rows)
    return CaptureSummary(len(games), skipped, len(rows), with_q, unres)


def open_out(path: str | Path) -> duckdb.DuckDBPyConnection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(p))


__all__ = [
    "CaptureSummary",
    "backfill_games",
    "capture_forward",
    "ensure_table",
    "open_out",
    "open_read_only",
]
