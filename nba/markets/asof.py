"""Strict as-of Kalshi market state (read-only).

Every query filters ``kalshi_prices.ts < as_of`` (strictly before; a snapshot stamped exactly at
``as_of`` is NOT used). Timestamps in ``kalshi_prices`` are naive UTC; aware datetimes are
converted. Nothing from after ``as_of`` can enter: the only time-dependent table read is
``kalshi_prices``, and ``kalshi_markets`` is used solely to enumerate which tickers belong to an
event (a ticker with no price before ``as_of`` yields no quote).

Derived quantities (documented method)
--------------------------------------
* Moneyline (``KXNBAGAME``): two contracts per game, one per team. Home win probability is the
  proportionally de-vigged mid, ``mid_home / (mid_home + mid_away)``. Bounds from the two books:
  ``lo = max(bid_home, 1 - ask_away)``, ``hi = min(ask_home, 1 - bid_away)``.
* Spread (``KXNBASPREAD``, "X wins by over t"): with home margin M, a home contract is
  ``P(M > t)``; an away contract is ``P(M < -t)`` i.e. ``P(M > -t) = 1 - p``. Add the point
  ``P(M > 0) = p_home_win``. Implied margin = median of the ladder (``nba.markets.implied``).
* Total (``KXNBATOTAL``, "over t"): ``P(T > t)``; implied total = ladder median.
* Implied team totals: ``(total +/- margin) / 2`` (home plus, away minus).
* Player props ("N+"): a contract is ``P(Y >= N)``. ``kalshi_markets.threshold`` is parsed from the
  title and equals ``N`` (verified on all 224 stored prop rows); a half-point strike ``N - 0.5``
  is also mapped to ``N`` via ``ceil``. Implied P(>=N) per listed N is the mid, plus a
  monotone-projected version
  across the ladder; the implied median is read from the projected ladder.

A quote contributes to a derived quantity only if it is usable: both sides present, not crossed,
spread <= ``max_spread`` and age <= ``max_age_s``. Raw quotes of every rung are always returned so
a downstream model can recompute with other rules.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.markets.implied import devig_two_way, ladder_median, monotone_survival
from nba.parlay.kalshi_map import load_team_abbr

ET = ZoneInfo("America/New_York")
MAX_SPREAD = 0.30  # quotes wider than this (bid-ask, in probability) are not used for derivations
MAX_AGE_S = 7200.0  # snapshots older than 2 h before as_of are not used for derivations
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_SPREAD_SUFFIX = re.compile(r"^(?P<team>[A-Z]{2,3}?)(?P<n>\d+)$")
PROP_STATS = ("pts", "reb", "ast", "fg3m")


def naive_utc(dt: datetime) -> datetime:
    """Naive-UTC form of ``dt`` (aware values are converted; naive are taken as UTC)."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def event_tag(d: date, away_abbr: str, home_abbr: str) -> str:
    """``2026-10-20, OKC, SAS`` -> ``26OCT20OKCSAS`` (suffix of every event ticker)."""
    return f"{d.year % 100:02d}{_MON[d.month - 1]}{d.day:02d}{away_abbr}{home_abbr}"


def open_read_only(
    path: str | Path, retries: int = 6, wait_s: float = 2.0, copy_fallback: bool = False
) -> duckdb.DuckDBPyConnection:
    """Read-only DuckDB connection; retry while a writer holds the lock.

    ``copy_fallback`` (only for small files such as ``kalshi.duckdb``): after the retries, read a
    private copy rather than fail. A copy may lag the writer's un-checkpointed WAL, which can only
    make the as-of state older, never leak the future.
    """
    import shutil
    import tempfile
    import time

    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:
            last = exc
            time.sleep(wait_s)
    if copy_fallback:
        tmp = Path(tempfile.mkdtemp(prefix="market_ro_")) / Path(path).name
        shutil.copy2(path, tmp)
        return duckdb.connect(str(tmp), read_only=True)
    assert last is not None
    raise last


@dataclass(frozen=True)
class Quote:
    """Latest top-of-book snapshot strictly before ``as_of`` for one contract."""

    ticker: str
    ts: datetime
    yes_bid: float | None
    yes_ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    source: str
    age_s: float

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None or self.yes_ask < self.yes_bid:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None or self.yes_ask < self.yes_bid:
            return None
        return self.yes_ask - self.yes_bid

    def usable_mid(
        self, max_spread: float = MAX_SPREAD, max_age_s: float = MAX_AGE_S
    ) -> float | None:
        m, s = self.mid, self.spread
        if m is None or s is None or s > max_spread or self.age_s > max_age_s:
            return None
        return m

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "ts": self.ts.isoformat(),
            "bid": self.yes_bid,
            "ask": self.yes_ask,
            "mid": self.mid,
            "last": self.last,
            "volume": self.volume,
            "oi": self.open_interest,
            "age_s": self.age_s,
            "source": self.source,
        }


def latest_quotes(
    con: duckdb.DuckDBPyConnection, tickers: Sequence[str], as_of: datetime
) -> dict[str, Quote]:
    """Latest price row per ticker with ``ts < as_of`` (strict). Ties: live first."""
    if not tickers:
        return {}
    cut = naive_utc(as_of)
    rows = con.execute(
        """
        SELECT ticker, ts, yes_bid, yes_ask, last, volume, open_interest, source
        FROM kalshi_prices
        WHERE ticker IN (SELECT unnest(?)) AND ts < ?
        QUALIFY row_number() OVER (
            PARTITION BY ticker ORDER BY ts DESC, (source = 'live') DESC, source) = 1
        """,
        [list(tickers), cut],
    ).fetchall()
    out: dict[str, Quote] = {}
    for tk, ts, bid, ask, last, vol, oi, src in rows:
        out[str(tk)] = Quote(
            str(tk),
            ts,
            None if bid is None else float(bid),
            None if ask is None else float(ask),
            None if last is None else float(last),
            None if vol is None else int(vol),
            None if oi is None else int(oi),
            str(src),
            (cut - ts).total_seconds(),
        )
    return out


@dataclass(frozen=True)
class LadderRung:
    """One contract on a ladder. ``x`` is the cut point (see the module docstring)."""

    ticker: str
    x: float
    side: str  # 'home' | 'away' | '' (totals/props)
    quote: Quote | None


@dataclass(frozen=True)
class GameMarketState:
    as_of: datetime
    event_date: date
    away_abbr: str
    home_abbr: str
    n_markets: int  # tickers that exist for this event (any time); 0 = Kalshi never listed it
    ml_home: Quote | None
    ml_away: Quote | None
    p_home_win: float | None
    p_home_win_lo: float | None
    p_home_win_hi: float | None
    implied_margin: float | None  # home margin, median of the spread ladder
    implied_total: float | None
    implied_home_total: float | None
    implied_away_total: float | None
    spread_ladder: list[LadderRung] = field(default_factory=list)
    total_ladder: list[LadderRung] = field(default_factory=list)

    @property
    def n_spread_rungs(self) -> int:
        return sum(1 for r in self.spread_ladder if r.quote is not None)

    @property
    def n_total_rungs(self) -> int:
        return sum(1 for r in self.total_ladder if r.quote is not None)

    @property
    def has_quote(self) -> bool:
        return bool(self.ml_home or self.ml_away or self.n_spread_rungs or self.n_total_rungs)

    def to_json(self) -> dict[str, Any]:
        def lad(rs: list[LadderRung]) -> list[dict[str, Any]]:
            return [
                {
                    "ticker": r.ticker,
                    "x": r.x,
                    "side": r.side,
                    **(r.quote.to_json() if r.quote else {}),
                }
                for r in rs
            ]

        return {
            "ml_home": self.ml_home.to_json() if self.ml_home else None,
            "ml_away": self.ml_away.to_json() if self.ml_away else None,
            "spread_ladder": lad(self.spread_ladder),
            "total_ladder": lad(self.total_ladder),
        }


def _markets(
    con: duckdb.DuckDBPyConnection, series: str, tag: str
) -> list[tuple[str, float | None]]:
    rows = con.execute(
        "SELECT ticker, threshold FROM kalshi_markets WHERE event_ticker = ? ORDER BY ticker",
        [f"{series}-{tag}"],
    ).fetchall()
    return [(str(t), None if th is None else float(th)) for t, th in rows]


def game_market_state(
    con: duckdb.DuckDBPyConnection,
    event_date: date,
    away_abbr: str,
    home_abbr: str,
    as_of: datetime,
    max_spread: float = MAX_SPREAD,
    max_age_s: float = MAX_AGE_S,
) -> GameMarketState:
    """Moneyline / spread / total state for one game, strictly before ``as_of``."""
    tag = event_tag(event_date, away_abbr, home_abbr)
    ml = _markets(con, "KXNBAGAME", tag)
    sp = _markets(con, "KXNBASPREAD", tag)
    tt = _markets(con, "KXNBATOTAL", tag)
    quotes = latest_quotes(con, [t for t, _ in ml + sp + tt], as_of)

    ml_home = quotes.get(f"KXNBAGAME-{tag}-{home_abbr}")
    ml_away = quotes.get(f"KXNBAGAME-{tag}-{away_abbr}")
    mh = ml_home.usable_mid(max_spread, max_age_s) if ml_home else None
    ma = ml_away.usable_mid(max_spread, max_age_s) if ml_away else None
    p_home = devig_two_way(mh, ma)
    lo_c = [q.yes_bid for q in (ml_home,) if q and q.yes_bid is not None]
    hi_c = [q.yes_ask for q in (ml_home,) if q and q.yes_ask is not None]
    if ml_away is not None and ml_away.yes_ask is not None:
        lo_c.append(1.0 - ml_away.yes_ask)
    if ml_away is not None and ml_away.yes_bid is not None:
        hi_c.append(1.0 - ml_away.yes_bid)
    p_lo = max(lo_c) if lo_c and p_home is not None else None
    p_hi = min(hi_c) if hi_c and p_home is not None else None

    spread_ladder: list[LadderRung] = []
    pts: list[tuple[float, float]] = []
    for tk, th in sp:
        m = _SPREAD_SUFFIX.match(tk[len(f"KXNBASPREAD-{tag}-") :])
        if m is None or th is None or m["team"] not in (home_abbr, away_abbr):
            continue
        is_home = m["team"] == home_abbr
        q = quotes.get(tk)
        spread_ladder.append(
            LadderRung(tk, th if is_home else -th, "home" if is_home else "away", q)
        )
        mid = q.usable_mid(max_spread, max_age_s) if q else None
        if mid is not None:
            pts.append((th, mid) if is_home else (-th, 1.0 - mid))
    if p_home is not None:
        pts.append((0.0, p_home))
    margin = ladder_median(pts) if len(pts) >= 2 else None

    total_ladder: list[LadderRung] = []
    tpts: list[tuple[float, float]] = []
    for tk, th in tt:
        if th is None:
            continue
        q = quotes.get(tk)
        total_ladder.append(LadderRung(tk, th, "", q))
        mid = q.usable_mid(max_spread, max_age_s) if q else None
        if mid is not None:
            tpts.append((th, mid))
    total = ladder_median(tpts) if len(tpts) >= 2 else None

    both = margin is not None and total is not None
    return GameMarketState(
        as_of=naive_utc(as_of),
        event_date=event_date,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
        n_markets=len(ml) + len(sp) + len(tt),
        ml_home=ml_home,
        ml_away=ml_away,
        p_home_win=p_home,
        p_home_win_lo=p_lo,
        p_home_win_hi=p_hi,
        implied_margin=margin,
        implied_total=total,
        implied_home_total=(total + margin) / 2.0
        if both and total is not None and margin is not None
        else None,
        implied_away_total=(total - margin) / 2.0
        if both and total is not None and margin is not None
        else None,
        spread_ladder=sorted(spread_ladder, key=lambda r: r.x),
        total_ladder=sorted(total_ladder, key=lambda r: r.x),
    )


@dataclass(frozen=True)
class PropRung:
    n: int  # contract is P(stat >= n)
    ticker: str
    quote: Quote | None
    p_mid: float | None  # raw usable mid
    p_mono: float | None  # monotone-projected across the ladder (usable rungs only)


@dataclass(frozen=True)
class PropMarketState:
    as_of: datetime
    player_id: int
    stat: str
    n_markets: int
    rungs: list[PropRung]
    implied_median: float | None

    def implied_pge(self, n: int) -> float | None:
        """Monotone-projected implied P(stat >= n) at a listed rung, else None."""
        for r in self.rungs:
            if r.n == n:
                return r.p_mono
        return None

    def pge_json(self) -> dict[str, float]:
        return {str(r.n): r.p_mono for r in self.rungs if r.p_mono is not None}

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "n": r.n,
                "ticker": r.ticker,
                "p_mid": r.p_mid,
                "p_mono": r.p_mono,
                **(r.quote.to_json() if r.quote else {}),
            }
            for r in self.rungs
        ]


def prop_market_state(
    con: duckdb.DuckDBPyConnection,
    event_date: date,
    away_abbr: str,
    home_abbr: str,
    player_id: int,
    stat: str,
    as_of: datetime,
    max_spread: float = MAX_SPREAD,
    max_age_s: float = MAX_AGE_S,
) -> PropMarketState:
    """Implied P(stat >= N) ladder for one player-stat in one game, strictly before ``as_of``."""
    tag = event_tag(event_date, away_abbr, home_abbr)
    rows = con.execute(
        """
        SELECT ticker, threshold FROM kalshi_markets
        WHERE player_id = ? AND stat = ? AND threshold IS NOT NULL
          AND event_ticker LIKE ? ORDER BY threshold
        """,
        [player_id, stat, f"KX%-{tag}"],
    ).fetchall()
    quotes = latest_quotes(con, [str(t) for t, _ in rows], as_of)
    base: list[tuple[int, str, Quote | None, float | None]] = []
    for tk, th in rows:
        q = quotes.get(str(tk))
        base.append(
            (
                math.ceil(float(th) - 1e-9),
                str(tk),
                q,
                q.usable_mid(max_spread, max_age_s) if q else None,
            )
        )
    usable = [(n - 0.5, p) for n, _, _, p in base if p is not None]
    mono = dict(monotone_survival(usable))
    rungs = [
        PropRung(n, tk, q, p, mono.get(n - 0.5) if p is not None else None) for n, tk, q, p in base
    ]
    med = ladder_median(usable) if len(usable) >= 2 else None
    return PropMarketState(naive_utc(as_of), player_id, stat, len(rows), rungs, med)


@dataclass(frozen=True)
class GameKey:
    game_id: str
    event_date: date  # ET calendar date (the date in Kalshi event tickers)
    away_team: int
    home_team: int


def resolve_game_key(nba_con: duckdb.DuckDBPyConnection, game_id: str) -> GameKey | None:
    """(ET date, teams) for a game id: ``games`` table first, else the forward win-prob rows."""
    r = nba_con.execute(
        "SELECT game_date, away_team, home_team FROM games WHERE game_id = ?", [game_id]
    ).fetchone()
    if r is not None:
        return GameKey(game_id, r[0], int(r[1]), int(r[2]))
    try:
        f = nba_con.execute(
            "SELECT tipoff, prediction FROM forward_predictions "
            "WHERE game_id = ? AND target = 'win_prob_home' LIMIT 1",
            [game_id],
        ).fetchone()
    except duckdb.CatalogException:
        return None
    if f is None:
        return None
    p = json.loads(f[1])
    d = f[0].replace(tzinfo=UTC).astimezone(ET).date()
    return GameKey(game_id, d, int(p["away_team"]), int(p["home_team"]))


def abbr_by_team_id() -> dict[int, str]:
    return {v: k for k, v in load_team_abbr().items()}


def asof_state(
    kalshi_con: duckdb.DuckDBPyConnection,
    nba_con: duckdb.DuckDBPyConnection,
    game_id: str,
    as_of: datetime,
    player_id: int | None = None,
    stat: str | None = None,
) -> tuple[GameMarketState, PropMarketState | None] | None:
    """Top-level query: market state for a game (and optionally one player-stat), before ``as_of``.

    Returns None if the game cannot be resolved to a date and team pair.
    """
    key = resolve_game_key(nba_con, game_id)
    abbr = abbr_by_team_id()
    if key is None or key.away_team not in abbr or key.home_team not in abbr:
        return None
    a, h = abbr[key.away_team], abbr[key.home_team]
    g = game_market_state(kalshi_con, key.event_date, a, h, as_of)
    p = None
    if player_id is not None and stat is not None:
        p = prop_market_state(kalshi_con, key.event_date, a, h, player_id, stat, as_of)
    return g, p
