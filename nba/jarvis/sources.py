"""Read-only price and schedule sources: Kalshi snapshots, Pinnacle (via the odds capture),
Polymarket's last captured prices, and the slate's games with their REAL tip times.

Every database is opened ``read_only=True``. A locked store is retried briefly, then a private copy
is read (small stores only); ``nba.duckdb`` is never copied and never written. Timestamps are naive
UTC throughout. Nothing here places or simulates an order.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from nba.jarvis.config import JarvisConfig
from nba.jarvis.edge import devig_pair
from nba.jarvis.models import Game, Quote, Ref
from nba.parlay.kalshi_map import load_team_abbr, parse_event

ET = ZoneInfo("America/New_York")
PROP_SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
PINNACLE_PROP_MARKET = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
STAT_WORD = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def et_today(now: datetime | None = None) -> date:
    n = (now or utcnow()).replace(tzinfo=UTC)
    return n.astimezone(ET).date()


def open_ro(
    path: Path, *, retries: int = 4, wait_s: float = 1.5, copy_ok: bool = False
) -> duckdb.DuckDBPyConnection:
    """Read-only connection with a short lock retry; ``copy_ok`` (small stores only) then reads a
    private copy, which can only be older than the writer's state, never newer."""
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:
            last = exc
            time.sleep(wait_s)
    if copy_ok:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis_ro_")) / path.name
        shutil.copy2(path, tmp)
        return duckdb.connect(str(tmp), read_only=True)
    assert last is not None
    raise last


def team_tables() -> tuple[dict[str, int], dict[int, str], dict[int, str]]:
    """(abbr -> id, id -> abbr, id -> full name) from configs/team_markets.yaml."""
    import yaml

    from nba.parlay.kalshi_map import TEAM_MARKETS

    abbr = load_team_abbr()
    raw = yaml.safe_load(TEAM_MARKETS.read_text())["teams"]
    names = {int(k): str(v["name"]) for k, v in raw.items()}
    return abbr, {v: k for k, v in abbr.items()}, names


# ----------------------------------------------------------------------------------- games


def load_games(cfg: JarvisConfig, slate: date) -> list[Game]:
    """The slate's scorable games (``nba.games`` for the date) with REAL tips (lineups store
    ``game_tips``, else the odds capture's tip). A game with no real tip is returned with
    ``tipoff=None`` and is never offered as a play."""
    _, abbr_of, _ = team_tables()
    path = cfg.paths["nba_db"]
    rows: list[tuple[str, int, int]] = []
    if path.exists():
        con = open_ro(path)
        try:
            rows = [
                (str(g), int(h), int(a))
                for g, h, a in con.execute(
                    "SELECT game_id, home_team, away_team FROM games WHERE game_date = ? "
                    "AND game_id NOT LIKE '001%' AND game_id NOT LIKE '003%' ORDER BY game_id",
                    [slate],
                ).fetchall()
            ]
        finally:
            con.close()
    tips = _tips(cfg, slate)
    out = []
    for gid, home, away in rows:
        out.append(
            Game(gid, away, home, abbr_of.get(away, "?"), abbr_of.get(home, "?"), tips.get(gid))
        )
    return out


def _tips(cfg: JarvisConfig, slate: date) -> dict[str, datetime]:
    tips: dict[str, datetime] = {}
    lp = cfg.paths["lineups_db"]
    if lp.exists():
        con = open_ro(lp, copy_ok=True)
        try:
            for gid, tip in con.execute(
                "SELECT game_id, arg_max(tipoff, recorded_at) FROM game_tips WHERE game_date = ? "
                "GROUP BY game_id",
                [slate],
            ).fetchall():
                tips[str(gid)] = tip
        except duckdb.Error:
            pass
        finally:
            con.close()
    mp = cfg.paths["market_db"]
    if mp.exists():
        con = open_ro(mp, copy_ok=True)
        try:
            for gid, tip in con.execute(
                "SELECT game_id, max(tipoff) FROM live_book_odds WHERE slate_date = ? "
                "GROUP BY game_id",
                [slate],
            ).fetchall():
                tips.setdefault(str(gid), tip)
        except duckdb.Error:
            pass
        finally:
            con.close()
    return tips


# ----------------------------------------------------------------------------------- Kalshi


def load_kalshi_quotes(
    cfg: JarvisConfig, games: list[Game], slate: date, now: datetime | None = None
) -> list[Quote]:
    """Latest Kalshi snapshot per open contract strictly before ``now``: game winners and the four
    core prop series. Unmatched games/players are skipped (counted by the caller via diff)."""
    path = cfg.paths["kalshi_db"]
    if not path.exists() or not games:
        return []
    abbr, _, _ = team_tables()
    by_pair = {(g.away_abbr, g.home_abbr): g for g in games}
    cut = now or utcnow()
    con = open_ro(path, copy_ok=True)
    try:
        rows = con.execute(
            "SELECT m.ticker, m.series_ticker, m.event_ticker, m.title, m.player_id, m.stat, "
            "m.threshold, p.yes_bid, p.yes_ask, p.ts FROM kalshi_markets m JOIN ("
            "SELECT ticker, arg_max(yes_bid, ts) yes_bid, arg_max(yes_ask, ts) yes_ask, max(ts) ts "
            "FROM kalshi_prices WHERE ts < ? GROUP BY ticker) p USING (ticker) "
            "WHERE m.result IS NULL AND m.series_ticker IN "
            "('KXNBAGAME','KXNBAPTS','KXNBAREB','KXNBAAST','KXNBA3PT')",
            [cut],
        ).fetchall()
    finally:
        con.close()
    out: list[Quote] = []
    for ticker, series, event, title, pid, _stat, thr, bid, ask, ts in rows:
        ev = parse_event(str(event))
        if ev is None or ev[0] != slate:
            continue
        g = by_pair.get((ev[1], ev[2]))
        if g is None:
            continue
        b = None if bid is None or float(bid) <= 0.0 else float(bid)
        a = None if ask is None or not 0.0 < float(ask) < 1.0 else float(ask)
        if series == "KXNBAGAME":
            suffix = str(ticker)[len(str(event)) + 1 :]
            tid = abbr.get(suffix)
            if tid is None:
                continue
            out.append(
                Quote(
                    "kalshi",
                    str(ticker),
                    g.game_id,
                    "game_win",
                    ts,
                    b,
                    a,
                    team_id=tid,
                    series=str(series),
                    label=f"{suffix} to win",
                )
            )
        elif series in PROP_SERIES and pid is not None and thr is not None:
            out.append(
                Quote(
                    "kalshi",
                    str(ticker),
                    g.game_id,
                    "prop",
                    ts,
                    b,
                    a,
                    player_id=int(pid),
                    stat=PROP_SERIES[str(series)],
                    threshold=float(thr),
                    series=str(series),
                    label=str(title),
                )
            )
    return out


# ---------------------------------------------------------------------------- Pinnacle / books


def _latest_rows(
    cfg: JarvisConfig, book: str, slate: date, now: datetime, markets: tuple[str, ...]
) -> list[tuple[str, datetime, str, str, int | None, str, float | None, float]]:
    mp = cfg.paths["market_db"]
    if not mp.exists():
        return []
    con = open_ro(mp, copy_ok=True)
    try:
        ph = ",".join("?" for _ in markets)
        return [
            (str(r[0]), r[1], str(r[2]), str(r[3]), r[4], str(r[5]), r[6], float(r[7]))
            for r in con.execute(
                "SELECT o.game_id, o.requested_at, o.market, o.outcome_name, o.player_id, o.side, "
                "o.point, o.implied_prob_raw FROM live_book_odds o JOIN ("
                "SELECT game_id, max(requested_at) r FROM live_book_odds "
                "WHERE book = ? AND slate_date = ? AND requested_at <= ? GROUP BY game_id) m "
                "ON o.game_id = m.game_id AND o.requested_at = m.r "
                f"WHERE o.book = ? AND o.slate_date = ? AND o.market IN ({ph}) "
                "AND o.implied_prob_raw IS NOT NULL",
                [book, slate, now, book, slate, *markets],
            ).fetchall()
        ]
    except duckdb.Error:
        return []
    finally:
        con.close()


class PinnacleRefs:
    """Pinnacle no-vig lookups for one slate: team win and player over/under at X.5 lines."""

    def __init__(self) -> None:
        self.win: dict[tuple[str, int], Ref] = {}
        self.prop: dict[tuple[str, int, str, float], Ref] = {}  # P(over) at the point

    def for_quote(self, q: Quote) -> Ref | None:
        if q.kind == "game_win" and q.team_id is not None:
            return self.win.get((q.game_id, q.team_id))
        if q.kind == "prop" and q.player_id and q.stat and q.threshold is not None:
            r = self.prop.get((q.game_id, q.player_id, q.stat, q.threshold - 0.5))
            if r is not None:  # Kalshi "N+" is Pinnacle "over N - 0.5"
                return Ref(r.p_yes, r.ts, f"o{q.threshold - 0.5:g}")
        return None


def load_pinnacle(
    cfg: JarvisConfig, games: list[Game], slate: date, now: datetime | None = None
) -> PinnacleRefs:
    cut = now or utcnow()
    refs = PinnacleRefs()
    by_id = {g.game_id: g for g in games}
    rows = _latest_rows(cfg, "pinnacle", slate, cut, ("h2h", *PINNACLE_PROP_MARKET))
    h2h: dict[str, dict[str, tuple[float, datetime]]] = {}
    props: dict[tuple[str, int, str, float], dict[str, tuple[float, datetime]]] = {}
    for gid, ts, market, _name, pid, side, point, p_raw in rows:
        if gid not in by_id:
            continue
        if market == "h2h" and side in ("home", "away"):
            h2h.setdefault(gid, {})[side] = (p_raw, ts)
        elif (
            market in PINNACLE_PROP_MARKET
            and pid
            and point is not None
            and side in ("over", "under")
        ):
            props.setdefault((gid, int(pid), PINNACLE_PROP_MARKET[market], float(point)), {})[
                side
            ] = (p_raw, ts)
    for gid, d in h2h.items():
        if "home" in d and "away" in d:
            ph, pa = devig_pair(d["home"][0], d["away"][0])
            g = by_id[gid]
            ts = d["home"][1]
            refs.win[(gid, g.home_team)] = Ref(ph, ts, "ml")
            refs.win[(gid, g.away_team)] = Ref(pa, ts, "ml")
    for k, d in props.items():
        if "over" in d and "under" in d:
            po, _pu = devig_pair(d["over"][0], d["under"][0])
            refs.prop[k] = Ref(po, d["over"][1], f"o{k[3]:g}")
    return refs


def load_book_indicative(
    cfg: JarvisConfig, games: list[Game], slate: date, book: str, now: datetime | None = None
) -> list[Quote]:
    """Last captured h2h price of ``book`` (e.g. polymarket) as an INDICATIVE quote: vigged
    implied probability as mid, no bid/ask. Shown, never offered as a tradable play."""
    cut = now or utcnow()
    by_id = {g.game_id: g for g in games}
    out: list[Quote] = []
    for gid, ts, _m, _name, _pid, side, _pt, p_raw in _latest_rows(cfg, book, slate, cut, ("h2h",)):
        g = by_id.get(gid)
        if g is None or side not in ("home", "away"):
            continue
        tid = g.home_team if side == "home" else g.away_team
        ab = g.home_abbr if side == "home" else g.away_abbr
        out.append(
            Quote(
                book,
                f"{book.upper()}:{gid}:{ab}",
                gid,
                "game_win",
                ts,
                None,
                None,
                team_id=tid,
                label=f"{ab} to win",
                indicative=True,
                mid=p_raw,
            )
        )
    return out


def age_min(ts: datetime, now: datetime) -> float:
    return (now - ts).total_seconds() / 60.0
