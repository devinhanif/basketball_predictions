"""Count APPARENT ladder violations in the stored Kalshi snapshots (read-only; nothing is claimed).

Input: a read-only copy of ``data/kalshi/kalshi.duckdb`` (``kalshi_markets``, ``kalshi_prices``).
Each ``ts`` is one 15-minute snapshot batch; reads inside a batch are not simultaneous and the
snapshot carries no order-book depth, so every count here is "apparent, unconfirmed", never profit.

For one snapshot: YES ask = ``yes_ask``; NO ask = ``1 - yes_bid`` (a binary book). A pair is
"apparent" when the two asks sum under $1 before fees, "net" when it is also positive after both
verified fees at ``alert.fee_eval_contracts`` contracts, and "net >= gap" when it clears the
ladder's ``min_gap`` too. Depth is treated as unlimited (unknown), which only flatters the counts.

Player-stat ladders are counted separately as EXCLUDED rungs (a DNP settles at a fair price).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.markets.fastarb import venues
from nba.markets.fastarb.config import FastArbConfig
from nba.markets.fastarb.ladder import (
    PROP_SERIES,
    LadderResult,
    find_ladder_violations,
    kalshi_prop_quotes,
    kalshi_team_total_quotes,
    leg_fee_fn,
)
from nba.markets.fastarb.props import Ask, GameKey, PropKey, QuoteBook
from nba.markets.fastarb.venues import K

ET = ZoneInfo("America/New_York")
BIG = 1_000_000.0
FAMILIES = ("margin", "total", "team_total", "prop")


def family(kind: str) -> str:
    return kind.split(":", 1)[0]


@dataclass
class Counts:
    snapshot_pairs_apparent: int = 0  # (snapshot, pair) with asks summing under $1
    snapshot_pairs_net_positive: int = 0  # ... and positive after both fees
    snapshot_pairs_net_ge_gap: int = 0  # ... and >= the ladder's min_gap
    distinct_pairs_apparent: set[tuple[str, str]] = field(default_factory=set)
    distinct_pairs_net_positive: set[tuple[str, str]] = field(default_factory=set)
    distinct_pairs_net_ge_gap: set[tuple[str, str]] = field(default_factory=set)
    best_net: float = float("-inf")


@dataclass
class DryRunReport:
    start: date
    end: date
    snapshots: int = 0
    markets_priced: int = 0
    by_family: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    by_phase: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    pregame_snapshots_with_tip: int = 0
    persistence: Counter[int] = field(default_factory=Counter)  # run lengths (snapshots) of net>0
    examples: list[str] = field(default_factory=list)
    unmatched: Counter[str] = field(default_factory=Counter)

    def lines(self) -> list[str]:
        out = [
            "ladder dry run over stored Kalshi snapshots "
            f"{self.start}..{self.end}: {self.snapshots} snapshot batches, "
            f"{self.markets_priced} market-price rows",
            "APPARENT, UNCONFIRMED: batches are not simultaneous, carry no depth, and are "
            "not profit. 'net' = after both verified fees at the evaluation size.",
        ]
        for title, table in (("by family", self.by_family), ("by phase", self.by_phase)):
            out.append(title + ":")
            for name in sorted(table):
                c = table[name]
                out.append(
                    f"  {name:<16} apparent {c.snapshot_pairs_apparent} "
                    f"(distinct pairs {len(c.distinct_pairs_apparent)}) | net>0 "
                    f"{c.snapshot_pairs_net_positive} ({len(c.distinct_pairs_net_positive)}) | "
                    f"net>=gap {c.snapshot_pairs_net_ge_gap} ({len(c.distinct_pairs_net_ge_gap)})"
                    f" | best net {c.best_net * 100:+.1f}c"
                    if c.best_net > float("-inf")
                    else f"  {name:<16} none"
                )
        if self.persistence:
            out.append(
                "persistence of net>0 pairs (run length in consecutive snapshots x count): "
                + ", ".join(f"{k}x{v}" for k, v in sorted(self.persistence.items()))
            )
        out += self.examples
        if self.unmatched:
            out.append("unmatched: " + ", ".join(f"{k}={v}" for k, v in self.unmatched.items()))
        return out


def _market_dict(ticker: str, event: str, title: str, strike: float | None) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": event,
        "title": title,
        "floor_strike": strike,
        "strike_type": "greater" if strike is not None else "structured",
        "status": "active",
        "yes_ask_dollars": "0.5",
        "no_ask_dollars": "0.5",
        "yes_ask_size_fp": "1",
        "yes_bid_size_fp": "1",
    }


def ticker_map(rows: list[tuple[str, str, str, float | None]]) -> dict[str, tuple[PropKey, bool]]:
    """ticker -> (canonical proposition, 'contract YES is canonical YES'). Parsed once through the
    live parsers with placeholder prices, so the dry run cannot disagree with the poller."""
    out: dict[str, tuple[PropKey, bool]] = {}
    for ticker, event, title, strike in rows:
        if ticker.split("-")[0] in PROP_SERIES and strike is not None:
            strike -= 0.5  # the table stores "X+" as X; Kalshi's floor_strike is X - 0.5
        m = _market_dict(ticker, event, title, strike)
        qb = venues.kalshi_quotes([m])
        qb.merge(kalshi_team_total_quotes([m]))
        qb.merge(kalshi_prop_quotes([m]))
        if len(qb.quotes) != 1:
            continue
        prop = next(iter(qb.quotes))
        yes_ask = qb.quotes[prop].get(K, {}).get("yes")
        if yes_ask is None:
            continue
        out[ticker] = (prop, yes_ask.short.endswith(" YES"))
    return out


def _book(
    prices: list[tuple[str, float, float]], tmap: dict[str, tuple[PropKey, bool]]
) -> QuoteBook:
    """One snapshot batch -> QuoteBook (YES ask = yes_ask, NO ask = 1 - yes_bid)."""
    book = QuoteBook()
    for ticker, bid, ask in prices:
        mapped = tmap.get(ticker)
        if mapped is None:
            continue
        prop, yes_is_canon = mapped
        for contract_yes, px in ((True, ask), (False, 1.0 - bid)):
            canon_yes = contract_yes == yes_is_canon
            book.add(
                prop,
                K,
                "yes" if canon_yes else "no",
                Ask(K, px, "", ticker, [(px, BIG)], f"{ticker} {'YES' if contract_yes else 'NO'}"),
            )
    return book


def _et_midnight_utc(d: date) -> datetime:
    local = datetime.combine(d, datetime.min.time(), tzinfo=ET)
    return local.astimezone(UTC).replace(tzinfo=None)


def _tips(lineups_copy: Path | None, start: date, end: date) -> dict[GameKey, datetime]:
    if lineups_copy is None or not lineups_copy.exists():
        return {}
    from nba.odds.live_close import read_slate

    tips: dict[GameKey, datetime] = {}
    d = start - timedelta(days=1)
    while d <= end + timedelta(days=1):
        for g in read_slate(lineups_copy, d):
            if g.home_team and g.away_team:
                tips[GameKey(g.away_team, g.home_team, d.isoformat())] = g.tipoff
        d += timedelta(days=1)
    return tips


def _record(
    c: Counts, r: LadderResult, gap: float, pair: tuple[str, str]
) -> tuple[bool, bool, bool]:
    c.snapshot_pairs_apparent += 1
    c.distinct_pairs_apparent.add(pair)
    c.best_net = max(c.best_net, r.margin)
    pos, ge = r.margin > 0.0, r.margin >= gap
    if pos:
        c.snapshot_pairs_net_positive += 1
        c.distinct_pairs_net_positive.add(pair)
    if ge:
        c.snapshot_pairs_net_ge_gap += 1
        c.distinct_pairs_net_ge_gap.add(pair)
    return True, pos, ge


def run(
    db: Path,
    cfg: FastArbConfig,
    start: date,
    end: date,
    lineups_copy: Path | None = None,
    max_examples: int = 5,
) -> DryRunReport:
    """Scan snapshot batches from ET midnight of ``start`` to ET midnight after ``end`` (``ts`` is
    naive UTC)."""
    fee = leg_fee_fn(cfg)
    gap = float(cfg.section("ladder")["min_gap"])
    n_ref = int(cfg.section("alert")["fee_eval_contracts"])
    rep = DryRunReport(start, end)
    con = duckdb.connect(str(db), read_only=True)
    try:
        mrows = con.execute(
            "SELECT ticker, event_ticker, title, threshold FROM kalshi_markets"
        ).fetchall()
        tmap = ticker_map(
            [(str(t), str(e), str(ti), None if s is None else float(s)) for t, e, ti, s in mrows]
        )
        rep.unmatched["markets_unmapped"] = len(mrows) - len(tmap)
        tips = _tips(lineups_copy, start, end)
        lo, hi = _et_midnight_utc(start), _et_midnight_utc(end + timedelta(days=1))
        stamps = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT ts FROM kalshi_prices WHERE ts >= ? AND ts < ? ORDER BY ts",
                [lo, hi],
            ).fetchall()
        ]
        prev_ge: set[tuple[str, str]] = set()
        run_len: Counter[tuple[str, str]] = Counter()
        for ts in stamps:
            rows = con.execute(
                "SELECT ticker, yes_bid, yes_ask FROM kalshi_prices "
                "WHERE ts = ? AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL",
                [ts],
            ).fetchall()
            rep.snapshots += 1
            rep.markets_priced += len(rows)
            book = _book([(str(t), float(b), float(a)) for t, b, a in rows], tmap)
            now_ge: set[tuple[str, str]] = set()
            for r in find_ladder_violations(book, K, cfg, fee, include_props=True, max_scan=n_ref):
                fam = family(r.lo.kind)
                tip = tips.get(r.lo.game)
                phase = "no_tip_known" if tip is None else ("pre_tip" if ts < tip else "after_tip")
                pair = (r.yes.ref, r.no.ref)
                _, pos, _ = _record(rep.by_family[fam], r, gap, pair)
                _record(rep.by_phase[f"{phase}/{fam}"], r, gap, pair)
                if pos:
                    now_ge.add(pair)
                    if len(rep.examples) < max_examples and fam != "prop":
                        rep.examples.append(
                            f"  e.g. {ts:%m-%d %H:%M}Z {phase} {r.label()}: {r.yes.ref} ask "
                            f"{r.yes.price:.2f} + {r.no.ref} ask {r.no.price:.2f}, gross "
                            f"{r.gross_gap * 100:+.1f}c, net {r.margin * 100:+.1f}c/pair at {n_ref}"
                        )
            for p in now_ge:
                run_len[p] += 1
            for p in prev_ge - now_ge:
                rep.persistence[run_len.pop(p)] += 1
            prev_ge = now_ge
        for p in prev_ge:
            rep.persistence[run_len.pop(p)] += 1
    finally:
        con.close()
    return rep


__all__ = ["DryRunReport", "run"]
