"""Discord post bodies that are not a play: the halftime check-in and the morning recap.

Everything here is Python-rendered from the paper book, the live capture's files and the config; no
model text. Results are shown in one layout whether a bet won or lost (a hit is annotated, never
celebrated).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from nba.jarvis.book import KillState, PaperBook, render_weekly, weekly_summary
from nba.jarvis.config import JarvisConfig
from nba.jarvis.exitwarn import clean_book, exit_warning, no_bid_line, venue_fair
from nba.jarvis.live import LiveGame
from nba.jarvis.render import VENUE, cents, scored_line
from nba.jarvis.sources import open_ro

log = logging.getLogger(__name__)


def todays_rows(book: PaperBook, slate: date, game_id: str) -> list[dict[str, Any]]:
    if not book.path.exists():
        return []
    import duckdb

    con = duckdb.connect(str(book.path), read_only=True)
    try:
        cur = con.execute(
            "SELECT * FROM plays WHERE slate_date = ? AND game_id = ? ORDER BY ts, leg_idx",
            [slate, game_id],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def _price_now(row: dict[str, Any], book: Book) -> float | None:
    """Latest captured ask for the bought side (None: other venue / no snapshot / a side empty)."""
    q = book.get(str(row["ticker"]))
    if row["venue"] != "kalshi" or q is None or q[0] is None or q[1] is None:
        return None
    return float(q[1]) if row["side"] == "yes" else 1.0 - float(q[0])


Book = dict[str, tuple[float | None, float | None, datetime]]  # ticker -> YES bid, ask, ts


def _kalshi_book(cfg: JarvisConfig, tickers: list[str], now: datetime) -> Book:
    """Latest YES (bid, ask, ts) per ticker AS OF ``now`` (a later row is never read), one open."""
    if not tickers or not cfg.paths["kalshi_db"].exists():
        return {}
    con = open_ro(cfg.paths["kalshi_db"], copy_ok=True)
    try:
        marks = ",".join("?" * len(tickers))
        rs = con.execute(
            "SELECT ticker, arg_max(yes_bid, ts), arg_max(yes_ask, ts), max(ts) "
            f"FROM kalshi_prices WHERE ticker IN ({marks}) AND ts <= ? GROUP BY ticker",
            [*tickers, now],
        ).fetchall()
    finally:
        con.close()
    return {
        str(t): (None if b is None else float(b), None if a is None else float(a), ts)
        for t, b, a, ts in rs
    }


def leg_exit_line(cfg: JarvisConfig, row: dict[str, Any], book: Book, now: datetime) -> str | None:
    """One line for a single-leg Kalshi position, or None. The empty-side convention (bid 0.00 /
    ask 1.00) is cleaned on the YES side first, then flipped for a NO leg."""
    q = book.get(str(row["ticker"]))
    if row["venue"] != "kalshi" or q is None:
        return None
    s = cfg.exit_warning
    if not s.get("enabled", False):
        return None
    yb, ya = clean_book(q[0], q[1])
    bid, ask = (yb, ya) if row["side"] == "yes" else (
        None if ya is None else 1.0 - ya, None if yb is None else 1.0 - yb
    )  # fmt: skip
    age = (now - q[2]).total_seconds() / 60.0
    if age < 0.0 or age > float(s["max_age_min"]):
        return None  # a stale or future-dated book is not evidence either way
    label = str(row["leg_desc"] or row["ticker"])
    if bid is None:
        floor = float(s["min_fair"])
        return None if ask is None or ask < floor else no_bid_line(ask, age, label)
    if ask is None:
        return None
    fair = venue_fair(bid, ask, float(s["max_spread"]))
    if fair is None:
        return None
    n = row["contracts"]
    w = exit_warning(cfg, bid, fair, float(n) if n else None, age_min=age)
    return None if w is None else f"{label}: {w.text}"


COMBO_NOTE = (
    "  !! combo: a cash-out offer is not captured here. Before selling, run "
    "`python -m nba.jarvis exit-check --bid <offer in cents> --fair <cents>` "
    "(or --legs-hit N --legs-total M)."
)


def halftime_lines(
    cfg: JarvisConfig,
    lg: LiveGame,
    rows: list[dict[str, Any]],
    feed_age: float | None,
    at: datetime | None = None,
) -> list[str]:
    lines = [
        f"**JARVIS halftime check-in {lg.matchup}**: {lg.away} {lg.away_score} - "
        f"{lg.home} {lg.home_score} at the half"
    ]
    if feed_age is not None and feed_age > 6.0:
        lines.append(f"(live feed is {feed_age:.0f} min old: treat the score as stale)")
    clock = at if at is not None else datetime.now(UTC).replace(tzinfo=None)
    tickers = sorted({str(x["ticker"]) for x in rows if x["venue"] == "kalshi"})
    try:  # fail soft: a price read never costs the check-in (prices, then the warning)
        book = _kalshi_book(cfg, tickers, clock)
    except Exception as exc:
        log.warning("exit warning: kalshi book unreadable (%s)", type(exc).__name__)
        book = {}
    seen: set[str] = set()
    for r in rows:
        if r["bundle_id"] in seen or r["leg_idx"] != 0:
            continue
        seen.add(r["bundle_id"])
        legs = [x for x in rows if x["bundle_id"] == r["bundle_id"]]
        what = " + ".join(
            f"BUY {x['leg_desc'] or x['ticker']} on {VENUE.get(x['venue'], x['venue'])}"
            for x in legs
        )
        tag = {"pick": "your pick", "daily_dollar": "Daily Dollar"}.get(
            r["edge_label"], r["edge_label"]
        )
        status = ""
        if r["kind"] == "game_win" and len(legs) == 1 and r["team_id"] is not None:
            status = _lead_text(lg, r)
        npx = _price_now(r, book)
        px = "" if npx is None else f"; price now {cents(npx)} vs your {cents(float(r['price']))}"
        pay = f"; pays ${float(r['contracts']):.2f} if right" if r["contracts"] else ""
        lines.append(f"- [{tag}] {what}{status}{px}{pay}")
        if r["edge_label"] == "daily_dollar" or r["kind"] == "combo":
            if cfg.exit_warning.get("enabled", False):
                lines.append(COMBO_NOTE)  # the held instrument is the combo, not its legs
            continue
        if len(legs) > 1:
            continue  # e.g. an arbitrage pair: legs are not sold one by one against a fair
        try:
            ex = leg_exit_line(cfg, r, book, clock)
        except Exception as exc:
            log.warning("exit warning failed (%s)", type(exc).__name__)
            ex = None
        if ex is not None:
            lines.append(f"  !! {ex}")
    if not seen:
        lines.append("- no plays logged on this game")
    return lines


def _lead_text(lg: LiveGame, r: dict[str, Any]) -> str:
    """'; your side (LAC) up 4' / 'down 4' / 'level', from the bought team and the half score."""
    from nba.jarvis.sources import team_tables

    _, abbr_of, _ = team_tables()
    team = abbr_of.get(int(r["team_id"]), "?")
    bought = team if r["side"] == "yes" else (lg.home if team == lg.away else lg.away)
    mine = lg.away_score if bought == lg.away else lg.home_score
    other = lg.home_score if bought == lg.away else lg.away_score
    if mine == other:
        return f"; {bought} level"
    return f"; {bought} {'up' if mine > other else 'down'} {abs(mine - other)}"


def recap_lines(
    cfg: JarvisConfig,
    book: PaperBook,
    day: date,
    weight_lines: list[str],
    kill: KillState,
) -> list[str]:
    lines = [f"**JARVIS morning recap, games of {day}**"]
    rows = book._rows(
        "SELECT p.slate_date, p.matchup, p.venue, p.side, p.leg_desc, p.price, p.edge_label, "
        "p.edge_cents, s.won, s.pnl_usd, p.source, p.leg_idx "
        "FROM plays p JOIN scores s USING (play_id) WHERE p.slate_date = ? "
        "AND s.status = 'settled' "
        "AND p.leg_idx = 0 ORDER BY p.ts",
        [day],
    )
    groups: dict[str, float] = {"JARVIS": 0.0, "Devin's picks": 0.0, "Daily Dollar": 0.0}
    cols = ["slate_date", "matchup", "venue", "side", "leg_desc", "price", "edge_label",
            "edge_cents",
            "won", "pnl_usd", "source", "leg_idx"]  # fmt: skip
    for r in rows:
        d = dict(zip(cols, r, strict=True))
        lines.append("- " + scored_line(d))
        g = {"pick": "Devin's picks", "daily_dollar": "Daily Dollar"}.get(
            str(d["edge_label"]), "JARVIS"
        )
        groups[g] += float(d["pnl_usd"])
    if not rows:
        lines.append("- nothing settled yet for that date")
    lines.append(
        "P&L that day: "
        + ", ".join(f"{k} {v:+.2f}" for k, v in groups.items())
        + " (paper where the balance is at the floor)"
    )
    gap = cfg.balance_current - cfg.balance_start
    lines.append(
        f"Balance (the value you set in configs/jarvis.yaml): ${cfg.balance_current:.2f} vs "
        f"${cfg.balance_start:.0f} start ({gap:+.2f}); floor ${cfg.balance_floor:.0f}"
        + (" -- AT OR BELOW THE FLOOR: paper-only" if cfg.paper_only else "")
    )
    s = weekly_summary(cfg, book, until=day, days=7)
    lines.append("Records, last 7 days (n, hit rate, CLV; descriptive below the minimum n):")
    for label, d in s["labels"].items():
        if d["n"] == 0:
            continue
        hit = "n/a" if d["hit_rate"] is None else f"{d['hit_rate'] * 100:.0f}%"
        clv = "n/a" if d["clv_mean_cents"] is None else f"{d['clv_mean_cents']:+.2f}c"
        lines.append(
            f"  {label}: n {d['n']}, hit {hit}, CLV {clv}, P&L {d['pnl_usd']:+.2f}"
            + ("" if d["claim_ok"] else " (insufficient sample)")
        )
    if kill.active:
        lines.append(kill.message())
    elif kill.n:
        lines.append(f"Kill switch off: mean CLV {kill.mean_clv:+.2f}c over the last {kill.n} bets")
    else:
        lines.append(
            f"Kill switch: needs {cfg.kill_min_bets} settled bets with a close (has {kill.n})"
        )
    lines += weight_lines
    flagged, returned = book.ingame_counts(day)
    if flagged:
        lines.append(
            f"In-game BENCHED ('may be done') posts that night: flagged {flagged}, "
            f"back on the floor {returned}. The rule was chosen in-sample (1 of 46 returned), "
            "so it is judged on these forward nights."
        )
    return lines


__all__ = ["VENUE", "halftime_lines", "recap_lines", "render_weekly", "todays_rows"]
