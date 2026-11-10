"""Settle the overs a card showed and total them: this game, and the season so far.

An over is scored at the price it was FIRST shown (prediction time). "Units": every over risks one
unit, so a hit at cost c (ask plus fee per contract) earns (1 - c) / c units and a miss loses 1.
A player who did not play is void (the venue's rule decides; nothing is guessed) and is excluded
from n. Outcomes come from ``book.OutcomeSource`` (Kalshi's own result first, else the box score),
read-only; a pick whose game has no result yet simply waits. The season line is DESCRIPTIVE (n,
hits, misses, units): no rule tests this population, so no interval and no claim is printed
(DECISIONS 2026-11-10: card_picks.jsonl is never an input to a frozen rule, the kill switch or
the model weights).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from nba.jarvis.book import OutcomeSource
from nba.jarvis.cards.model import Recap, SettledRow
from nba.jarvis.cards.state import CardState
from nba.jarvis.config import JarvisConfig

log = logging.getLogger("nba.jarvis.cards.recap")


def units_for(ask: float, fee_c: float, won: bool) -> float:
    cost = ask + fee_c / 100.0
    return (1.0 - cost) / cost if won else -1.0


def box_loaded(cfg: JarvisConfig, game_id: str) -> bool:
    """Has the game's player box score been ingested (read-only, 2026 games only)? A missing player
    row only means 'did not play' once the rest of the box is there."""
    from nba.jarvis.sources import open_ro

    path = cfg.paths["nba_db"]
    if not path.exists():
        return False
    try:
        con = open_ro(path)
        try:
            r = con.execute(
                "SELECT count(*) FROM player_game_stats s JOIN games g USING (game_id) "
                "WHERE s.game_id = ? AND g.season = 2026",
                [game_id],
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - unreadable: keep waiting
        return False
    return bool(r and int(r[0]) >= 10)


def settle_pending(
    cfg: JarvisConfig,
    state: CardState,
    now: datetime,
    *,
    source: OutcomeSource | None = None,
    game_ids: set[str] | None = None,
    min_slate: str | None = None,
) -> int:
    """Append a settlement for every logged over whose game now has a result. Idempotent."""
    src = source or OutcomeSource(cfg)
    n = 0
    for p in state.picks():
        if "settled" in p or (game_ids is not None and p["game_id"] not in game_ids):
            continue
        if min_slate is not None and str(p["slate"]) < min_slate:
            continue  # an old pick still waiting is not retried every tick
        tip = p.get("tip")
        if tip is not None and datetime.fromisoformat(str(tip)) > now:
            continue
        row: dict[str, Any] = {
            "venue": p["venue"], "ticker": p["ticker"], "side": str(p.get("side", "yes")),
            "kind": "prop",
            "game_id": p["game_id"], "stat": p["stat"], "player_id": p["player_id"],
            "threshold": p["line"], "team_id": None,
        }  # fmt: skip
        try:
            o = src.of(row)
        except Exception as exc:  # noqa: BLE001 - a busy store waits for the next tick
            log.warning("card settlement waits (%s)", type(exc).__name__)
            continue
        if o.status == "unresolved":
            continue
        if o.status == "void" and not box_loaded(cfg, str(p["game_id"])):
            continue  # a final score without its box rows is not proof he did not play: wait
        won = bool(o.won) if o.status == "settled" and o.won is not None else None
        units = 0.0 if won is None else units_for(float(p["ask"]), float(p["fee_c"]), won)
        state.add_settlement(
            str(p["pick_id"]),
            {"status": o.status, "won": won, "units": units, "settled_at": now.isoformat()},
        )
        n += 1
    return n


def recap_for_game(cfg: JarvisConfig, state: CardState, game_id: str) -> Recap:
    allp = state.picks()
    mine = [p for p in allp if p["game_id"] == game_id]
    rows: list[SettledRow] = []
    waiting = 0
    for p in sorted(mine, key=lambda p: (-float(p["edge_c"]), str(p["player"]))):
        s = p.get("settled")
        if s is None:
            waiting += 1
            continue
        rows.append(
            SettledRow(
                str(p["player"]),
                str(p["stat"]),
                float(p["line"]),
                float(p["ask"]),
                float(p["fee_c"]),
                float(p["edge_c"]),
                str(s["status"]),
                s.get("won"),
                float(s.get("units", 0.0)),
                str(p.get("side", "yes")),
            )  # fmt: skip
        )
    hit = sum(1 for r in rows if r.status == "settled" and r.won)
    miss = sum(1 for r in rows if r.status == "settled" and not r.won)
    done = [p for p in allp if p.get("settled", {}).get("status") == "settled"]
    dates = {str(p["slate"]) for p in done}
    return Recap(
        rows=tuple(rows),
        waiting=waiting,
        n_hit=hit,
        n_miss=miss,
        n_void=sum(1 for r in rows if r.status == "void"),
        units=sum(r.units for r in rows if r.status == "settled"),
        season_n=len(done),
        season_hit=sum(1 for p in done if p["settled"].get("won")),
        season_units=sum(float(p["settled"]["units"]) for p in done),
        season_dates=len(dates),
    )
