# ruff: noqa: E501
"""BEST_PRICE_FORWARD minimum-data gate (rule: docs/prereg/BEST_PRICE_FORWARD.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.best_price_forward_gate \\
        --data-root /Users/devin/Downloads/nba-prediction --until 2026-11-08 \\
        --out reports/prereg_best_price_forward/gate.json

Label-free, counts only. It never selects ``y``, ``crps``, ``brier``, ``log_loss`` or ``status`` from
``forward_scores`` (it does not open that table), never reads a box-score stat, and never reads Kalshi's
``result``, ``settled_ts`` or any settled page. Picks are built from pre-tip inputs only (production's
stored forecast, the T-60 sportsbook snapshot, Kalshi top-of-book at or before the entry stamp). Power
uses synthetic outcomes drawn from declared truths on the real pick and game structure.
Season 2026 only (``season = 2026`` in SQL); season 2025 is never read. Every database is opened
``read_only=True`` with a retry loop (the live files have launchd writers).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts

RULE = "BEST_PRICE_FORWARD"
SEASON = 2026
SINCE = date(2026, 10, 20)
PROD = "props_context_residual"
STATS = ("pts", "reb", "ast", "fg3m")
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
KALSHI_SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
# Primary venue universe U1: US-licensed sportsbooks the-odds-api returns in region `us`, plus Kalshi.
US_BOOKS = ("betmgm", "betrivers", "draftkings", "fanatics", "fanduel", "williamhill_us")
KALSHI = "kalshi"
U1 = (*US_BOOKS, KALSHI)
# Captured but outside U1 (counted for coverage only): offshore / non-US, and exchanges without a
# verified fee object for the venue the vendor labels (rule Section 2).
OTHER_VENUES = ("pinnacle", "bovada", "betonlineag", "polymarket", "novig", "prophetx", "betopenly")
SNAPSHOT = "t60"
MAP_SHA = "443743a1543fe5fbaaf97bd072bf6fba56e172edbca5839cd1eb85d711349b25"
CAL_BOOK = "pinnacle"
DELTA = 0.03  # pick iff p_model(side) - p_consensus(side) >= DELTA
STALE_DEV = (
    0.07  # a venue whose de-vigged P(over) is this far from the median of the others is dropped
)
BOOK_MAX_LAST_UPDATE_MIN = (
    60.0  # book quote whose own last_update is older than this before E is dropped
)
KALSHI_MAX_AGE_MIN = 20.0  # Kalshi entry snapshot age at E (15-minute capture cadence)
MIN_P_PLAY = 0.90
FEE_COEF = 0.07  # Kalshi taker, configs/parlay.yaml fee_model (verified); pinned in the rule text
MAKER_FEE_COEF = 0.0175  # applied to the paper maker arm even where Kalshi charges no maker fee
MIN_VENUES = 2
GAMES_GRID = (250, 331, 414, 500, 832, 1040)
N_SYNTH = 200
SEED = 0
READ1_LAST_TIP = date(2027, 1, 13)
READ2_LAST_TIP = date(2027, 4, 12)
CUP_GAP = (date(2026, 12, 4), date(2026, 12, 11))


# ----------------------------------------------------------------------------- pure helpers


def american_to_prob(a: float) -> float:
    """Raw (vigged) implied probability of an American price."""
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def american_to_decimal(a: float) -> float:
    """Decimal odds (stake included) of an American price."""
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def devig_over(p_over_raw: float, p_under_raw: float) -> float:
    """Proportional de-vig of a two-sided pair: the fair P(over)."""
    return p_over_raw / (p_over_raw + p_under_raw)


def kalshi_fee(p: float, coef: float = FEE_COEF) -> float:
    """Per-contract fee, unrounded (rule Section 4): coef * P * (1 - P)."""
    return coef * p * (1.0 - p)


def kalshi_cost(side: str, yes_bid: float, yes_ask: float, coef: float = FEE_COEF) -> float:
    """All-in cost of one contract paying $1 on ``side`` at the taker price (cents rounded first).
    over = buy YES at the ask; under = buy NO at 1 - yes_bid."""
    bid_c, ask_c = round(100 * yes_bid), round(100 * yes_ask)
    p = ask_c / 100.0 if side == "over" else (100 - bid_c) / 100.0
    return p + kalshi_fee(p, coef)


def kalshi_decimal(side: str, yes_bid: float, yes_ask: float, coef: float = FEE_COEF) -> float:
    """Net decimal odds of a 1-unit stake at the Kalshi taker price, fee included."""
    return 1.0 / kalshi_cost(side, yes_bid, yes_ask, coef)


def kalshi_mid_over(yes_bid: float, yes_ask: float) -> float:
    bid_c, ask_c = round(100 * yes_bid), round(100 * yes_ask)
    return (bid_c + ask_c) / 200.0


def kalshi_quotable(yes_bid: float | None, yes_ask: float | None) -> bool:
    """bid >= 1c, ask <= 99c, ask > bid (integer cents)."""
    if yes_bid is None or yes_ask is None:
        return False
    bid_c, ask_c = round(100 * yes_bid), round(100 * yes_ask)
    return bid_c >= 1 and ask_c <= 99 and ask_c > bid_c


def maker_price(side: str, yes_bid: float, yes_ask: float) -> float | None:
    """Paper maker price on ``side``: one cent better than the best bid on that side, only if it
    stays strictly below that side's ask (else it would cross; no maker arm). In dollars."""
    bid_c, ask_c = round(100 * yes_bid), round(100 * yes_ask)
    if side == "over":
        p_c, side_ask_c = bid_c + 1, ask_c
    else:
        p_c, side_ask_c = (100 - ask_c) + 1, 100 - bid_c
    return p_c / 100.0 if p_c < side_ask_c else None


def reference_point(book_pairs: dict[str, dict[float, tuple[float, float]]]) -> float | None:
    """The consensus main point across U1 sportsbooks: each book's own main point (two-sided pair
    whose raw over/under probabilities are closest; tie lower point), then the point that is main
    at the most books; tie: the point whose median de-vigged P(over) is closest to 0.5; tie: lower.
    ``book_pairs`` maps book -> point -> (over_raw_prob, under_raw_prob), x.5 points only."""
    mains: dict[str, float] = {}
    for b, pairs in book_pairs.items():
        if not pairs:
            continue
        mains[b] = min(pairs, key=lambda pt: (abs(pairs[pt][0] - pairs[pt][1]), pt))
    if not mains:
        return None
    cnt = Counter(mains.values())
    top = max(cnt.values())
    cands = [pt for pt, c in cnt.items() if c == top]

    def closeness(pt: float) -> tuple[float, float]:
        ps = [devig_over(*book_pairs[b][pt]) for b in book_pairs if pt in book_pairs[b]]
        return (abs(statistics.median(ps) - 0.5), pt)

    return min(cands, key=closeness)


def stale_filter(p_over: dict[str, float], dev: float = STALE_DEV) -> dict[str, float]:
    """Drop a venue whose de-vigged P(over) differs from the median of ALL quoting venues (itself
    included) by more than ``dev``. With exactly two venues that disagree by more than ``dev`` both
    are dropped (no majority to say which is stale)."""
    if len(p_over) < 2:
        return dict(p_over)
    if len(p_over) == 2:
        a, b = p_over.values()
        return dict(p_over) if abs(a - b) <= dev else {}
    med = statistics.median(p_over.values())
    return {v: p for v, p in p_over.items() if abs(p - med) <= dev}


def pick_side(p_model_over: float, p_cons_over: float, delta: float = DELTA) -> str | None:
    """One side or none: over iff p_model - p_cons >= delta; under iff (1-p_model) - (1-p_cons) >= delta."""
    d = p_model_over - p_cons_over
    if d >= delta:
        return "over"
    if -d >= delta:
        return "under"
    return None


def best_venue(net_decimal: dict[str, float]) -> tuple[str, float]:
    """Highest net decimal odds; tie: a sportsbook before Kalshi (void protection), then alphabetical."""
    return max(
        net_decimal.items(), key=lambda kv: (kv[1], kv[0] != KALSHI, [-ord(c) for c in kv[0]])
    )


def clustered_se(d: np.ndarray, gi: np.ndarray, n_groups: int) -> float:
    """SE of a row mean under cluster sampling (ratio linearisation)."""
    gs = np.bincount(gi, d, n_groups)
    gn = np.bincount(gi, None, n_groups)
    m = float(d.mean())
    return float(np.sqrt(np.sum((gs - m * gn) ** 2)) / gn.sum())


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ----------------------------------------------------------------------------- loading


def connect_ro(
    path: Path, wait_s: float = 900.0, retry_s: float = 15.0
) -> duckdb.DuckDBPyConnection:
    t0 = time.time()
    while True:
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException:
            if time.time() - t0 > wait_s:
                raise
            print(f"[gate] {path.name} locked by a writer; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)


@dataclass
class Pick:
    game_id: str
    game_date: date
    stat: str
    player_id: int
    point: float
    side: str
    p_raw: float
    p_cal: float
    p_cons: float
    venues: dict[str, float]  # venue -> net decimal on the pick side (after stale filter)
    kalshi_maker: float | None = None
    raw_side: str | None = None


@dataclass
class GateState:
    why: Counter[str] = field(default_factory=Counter)
    venue_quotes_any: Counter[str] = field(default_factory=Counter)
    venue_at_ref: Counter[str] = field(default_factory=Counter)
    book_lu_age: list[float] = field(default_factory=list)
    kalshi_age: list[float] = field(default_factory=list)
    stale_dropped: Counter[str] = field(default_factory=Counter)
    n_venues_hist: Counter[int] = field(default_factory=Counter)


def load_entry_stamps(
    odds: duckdb.DuckDBPyConnection, since: date, until: date
) -> dict[str, tuple[datetime, datetime]]:
    """E per game = the earliest received_at of the game's t60 snapshot (one vendor call per game);
    returns game -> (E, tipoff). Only rows strictly before tip exist (writer-enforced)."""
    rows = odds.execute(
        """SELECT game_id, min(received_at), min(tipoff) FROM live_book_odds
           WHERE snapshot_kind = ? AND slate_date BETWEEN ? AND ? GROUP BY game_id""",
        [SNAPSHOT, since, until],
    ).fetchall()
    return {
        r[0]: (r[1], r[2]) for r in rows if r[1] is not None and r[2] is not None and r[1] < r[2]
    }


def load_book_quotes(
    odds: duckdb.DuckDBPyConnection,
    since: date,
    until: date,
    entries: dict[str, tuple[datetime, datetime]],
) -> tuple[dict[tuple[str, int, str], list[tuple[Any, ...]]], int]:
    """``t60`` rows of the four main markets with ``received_at <= E`` of their game (rule 4.2, M2).
    Returns the rows grouped by (game, player, stat) and the count of refused post-entry rows."""
    mm = ",".join("?" for _ in MARKET_STAT)
    frame = pl.DataFrame(
        [(g, e) for g, (e, _) in entries.items()], schema=["game_id", "e"], orient="row"
    )
    odds.register("bp_book_e", frame.to_arrow())
    raw = odds.execute(
        f"""SELECT o.game_id, o.player_id, o.market, o.book, o.side, o.point, o.price_american,
                   o.received_at, o.last_update, o.received_at <= b.e AS admissible
            FROM live_book_odds o JOIN bp_book_e b ON b.game_id = o.game_id
            WHERE o.snapshot_kind = ? AND o.slate_date BETWEEN ? AND ? AND o.market IN ({mm})
              AND o.player_id IS NOT NULL AND o.point IS NOT NULL AND o.price_american IS NOT NULL
              AND o.side IN ('over', 'under')""",  # noqa: S608
        [SNAPSHOT, since, until, *MARKET_STAT],
    ).fetchall()
    odds.unregister("bp_book_e")
    grp: dict[tuple[str, int, str], list[tuple[Any, ...]]] = defaultdict(list)
    refused = 0
    for r in raw:
        if not r[9]:
            refused += 1
            continue
        grp[(r[0], int(r[1]), MARKET_STAT[r[2]])].append(r[:9])
    return grp, refused


def choose_quotes(
    rows: list[tuple[Any, ...]],
) -> dict[str, dict[tuple[str, float], tuple[Any, ...]]]:
    """One admissible row per (book, side, point): latest ``received_at``, then latest ``last_update``,
    then the worse price for the bettor (lower decimal odds). Deterministic regardless of fetch order."""
    best: dict[tuple[str, str, float], tuple[Any, ...]] = {}

    def key(r: tuple[Any, ...]) -> tuple[datetime, datetime, float]:
        lu = r[8] if r[8] is not None else datetime.min
        return (r[7], lu, -american_to_decimal(int(r[6])))

    for r in rows:
        k = (r[3], r[4], float(r[5]))
        if k not in best or key(r) > key(best[k]):
            best[k] = r
    out: dict[str, dict[tuple[str, float], tuple[Any, ...]]] = defaultdict(dict)
    for (book, side, pt), r in best.items():
        out[book][(side, pt)] = r
    return dict(out)


def _ticker_date(event_ticker: str) -> date | None:
    try:
        return datetime.strptime(event_ticker.split("-")[1][:7].title(), "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def load_kalshi(
    kal: duckdb.DuckDBPyConnection,
    entry_by_pd: dict[tuple[int, date], tuple[str, datetime]],
) -> dict[tuple[str, int, str], dict[float, tuple[float, float, float]]]:
    """(game, player, stat) -> point -> (yes_bid, yes_ask, age_min) at the latest live snapshot with
    E - 20 min <= ts <= E. Reads ticker metadata and prices only (no result, no settlement)."""
    sm = ",".join("?" for _ in KALSHI_SERIES)
    mk = kal.execute(
        f"""SELECT ticker, series_ticker, event_ticker, player_id, threshold FROM kalshi_markets
            WHERE series_ticker IN ({sm}) AND player_id IS NOT NULL AND threshold IS NOT NULL""",  # noqa: S608
        list(KALSHI_SERIES),
    ).fetchall()
    rows = []
    for tk, ser, ev, pid, thr in mk:
        d = _ticker_date(ev)
        if d is None or (int(pid), d) not in entry_by_pd:
            continue
        gid, e = entry_by_pd[(int(pid), d)]
        rows.append(
            (
                tk,
                gid,
                int(pid),
                KALSHI_SERIES[ser],
                float(thr) - 0.5,
                e - timedelta(minutes=KALSHI_MAX_AGE_MIN),
                e,
            )
        )
    if not rows:
        return {}
    frame = pl.DataFrame(
        rows, schema=["ticker", "game_id", "player_id", "stat", "point", "lo", "e"], orient="row"
    )
    kal.register("bp_entry", frame.to_arrow())
    px = kal.execute(
        """SELECT b.game_id, b.player_id, b.stat, b.point, p.yes_bid, p.yes_ask,
                  date_diff('second', p.ts, b.e) / 60.0 AS age
           FROM bp_entry b JOIN kalshi_prices p ON p.ticker = b.ticker
           WHERE p.source = 'live' AND p.ts >= b.lo AND p.ts <= b.e
           QUALIFY row_number() OVER (PARTITION BY b.ticker ORDER BY p.ts DESC, p.yes_bid, p.yes_ask) = 1"""
    ).fetchall()
    kal.unregister("bp_entry")
    out: dict[tuple[str, int, str], dict[float, tuple[float, float, float]]] = defaultdict(dict)
    for gid, pid, st, pt, yb, ya, age in px:
        if yb is None or ya is None:
            continue
        out[(gid, int(pid), st)][float(pt)] = (float(yb), float(ya), float(age))
    return out


def load_predictions(
    nba: duckdb.DuckDBPyConnection,
    entries: dict[str, tuple[datetime, datetime]],
    since: date,
    until: date,
) -> tuple[list[tuple[Any, ...]], dict[str, date], int]:
    """Latest production row per (game, player, stat) with made_at <= E. Returns rows, game dates and
    the number of scheduled season-2026 games in the window."""
    games = nba.execute(
        "SELECT game_id, game_date FROM games WHERE season = ? AND game_date BETWEEN ? AND ?",
        [SEASON, since, until],
    ).fetchall()
    gdate = {g: d for g, d in games}
    frame = pl.DataFrame(
        [(g, e) for g, (e, _) in entries.items() if g in gdate],
        schema=["game_id", "e"],
        orient="row",
    )
    nba.register("bp_e", frame.to_arrow())
    rows = nba.execute(
        """SELECT p.game_id, p.target, p.player_id, p.made_at, CAST(p.prediction AS VARCHAR)
           FROM forward_predictions p JOIN bp_e e ON e.game_id = p.game_id
           JOIN games g ON g.game_id = p.game_id
           WHERE p.model_name = ? AND g.season = ? AND p.target IN ('pts','reb','ast','fg3m')
             AND p.made_at <= e.e
           QUALIFY row_number() OVER (PARTITION BY p.game_id, p.target, p.player_id ORDER BY p.made_at DESC, CAST(p.prediction AS VARCHAR)) = 1""",
        [PROD, SEASON],
    ).fetchall()
    nba.unregister("bp_e")
    return rows, gdate, len(games)


# ----------------------------------------------------------------------------- assembly


def build_picks(
    preds: list[tuple[Any, ...]],
    gdate: dict[str, date],
    entries: dict[str, tuple[datetime, datetime]],
    books: dict[tuple[str, int, str], list[tuple[Any, ...]]],
    kalshi: dict[tuple[str, int, str], dict[float, tuple[float, float, float]]],
    cmap: lc.CalMap,
    st: GateState,
) -> tuple[list[Pick], list[Pick]]:
    """Returns (CAL picks, RAW picks), each with >= MIN_VENUES priced on the pick side."""
    cal_picks: list[Pick] = []
    raw_picks: list[Pick] = []
    for gid, stat, pid, _made, js in preds:
        st.why["pred_rows"] += 1
        pred = json.loads(js)
        pp = pred.get("p_play")
        if pp is None or float(pp) < MIN_P_PLAY:
            st.why["p_play_below_0.90_or_missing"] += 1
            continue
        e = entries[gid][0]
        key = (gid, int(pid), stat)
        qs = books.get(key, [])
        pairs: dict[str, dict[float, tuple[float, float]]] = defaultdict(dict)
        prices: dict[str, dict[tuple[str, float], int]] = defaultdict(dict)
        for book, chosen in choose_quotes(qs).items():
            for (side, pt), r in chosen.items():
                if r[8] is not None:
                    age = (e - r[8]).total_seconds() / 60.0
                    if book in US_BOOKS:
                        st.book_lu_age.append(age)
                    if age > BOOK_MAX_LAST_UPDATE_MIN:
                        st.stale_dropped[f"{book}_last_update"] += 1
                        continue
                prices[book][(side, pt)] = int(r[6])
        for book, pr in prices.items():
            for (side, pt), a in pr.items():
                if side == "over" and ("under", pt) in pr and abs(pt - math.floor(pt) - 0.5) < 1e-9:
                    pairs[book][pt] = (american_to_prob(a), american_to_prob(pr[("under", pt)]))
        for v in pairs:
            st.venue_quotes_any[v] += 1
        kq = kalshi.get(key, {})
        if kq:
            st.venue_quotes_any[KALSHI] += 1
        us_pairs = {b: p for b, p in pairs.items() if b in US_BOOKS}
        ref = reference_point(us_pairs)
        if ref is None:
            st.why["no_us_book_two_sided_x5"] += 1
            continue
        for v, p in pairs.items():
            if ref in p:
                st.venue_at_ref[v] += 1
        p_over: dict[str, float] = {
            b: devig_over(*us_pairs[b][ref]) for b in us_pairs if ref in us_pairs[b]
        }
        k = kq.get(ref)
        if k is not None and kalshi_quotable(k[0], k[1]):
            st.venue_at_ref[KALSHI] += 1
            st.kalshi_age.append(k[2])
            p_over[KALSHI] = kalshi_mid_over(k[0], k[1])
        kept = stale_filter(p_over)
        for v in p_over:
            if v not in kept:
                st.stale_dropped[f"{v}_consensus_dev"] += 1
        st.n_venues_hist[len(kept)] += 1
        if len(kept) < MIN_VENUES:
            st.why["fewer_than_2_u1_venues_after_stale"] += 1
            continue
        st.why["rows_ge2_venues"] += 1
        p_cons = statistics.median(kept.values())
        po = ts.p_over(pred, ref)
        quart = ts.quartiles({"pred": pred})
        sr = pred.get("starter_rate")
        if po is None or quart is None or sr is None:
            st.why["no_model_p_at_ref"] += 1
            continue
        p_raw = float(np.clip(po, lc.EPS, 1 - lc.EPS))
        p_cal = cmap.apply(
            CAL_BOOK, stat, po, ref, quart[1], (quart[2] - quart[0]) / lc.IQR_TO_SD, float(sr)
        )
        for arm, pm, out in (("cal", p_cal, cal_picks), ("raw", p_raw, raw_picks)):
            pside = pick_side(pm, p_cons)
            if pside is None:
                continue
            net: dict[str, float] = {}
            for v in kept:
                if v == KALSHI:
                    assert k is not None
                    net[v] = kalshi_decimal(pside, k[0], k[1])
                else:
                    am = prices[v].get((pside, ref))
                    if am is not None:
                        net[v] = american_to_decimal(am)
            if len(net) < MIN_VENUES:
                st.why[f"{arm}_pick_side_lt2_venues"] += 1
                continue
            mk = maker_price(pside, k[0], k[1]) if (k is not None and KALSHI in net) else None
            out.append(
                Pick(gid, gdate[gid], stat, int(pid), ref, pside, p_raw, p_cal, p_cons, net, mk,
                     raw_side=pick_side(p_raw, p_cons))
            )  # fmt: skip
    return cal_picks, raw_picks


def implied_roi(picks: list[Pick], truth: str) -> tuple[float, np.ndarray]:
    """Expected ROI per pick at the best price if the truth is ``truth`` ('consensus' or 'cal').
    Label-free: an expectation over the declared truth, not an outcome."""
    r = []
    for x in picks:
        _, d = best_venue(x.venues)
        p_over = x.p_cons if truth == "consensus" else x.p_cal
        p = p_over if x.side == "over" else 1 - p_over
        r.append(p * d - 1.0)
    a = np.asarray(r, dtype=float)
    return float(a.mean()), a


def _ids(keys: list[Any]) -> tuple[np.ndarray, int]:
    _, inv = np.unique(np.asarray(keys, dtype=object).astype(str), return_inverse=True)
    return inv.astype(np.int64), int(inv.max()) + 1


def season_structure(picks: list[Pick], target_games: int, seed: int) -> dict[str, Any]:
    """A synthetic season built from the gate's real nights: whole dates are drawn with replacement
    (seeded) until ``target_games`` games; each draw is a new date and new games, players keep their
    identity, so a player's picks accumulate as they would over a season. Picks must be sorted (M6)."""
    rng = np.random.default_rng(seed)
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, x in enumerate(picks):
        by_date[str(x.game_date)].append(i)
    dates = sorted(by_date)
    ngames = {d: len({picks[i].game_id for i in by_date[d]}) for d in dates}
    idx: list[int] = []
    gkey: list[str] = []
    dkey: list[int] = []
    rep, total = 0, 0
    while total < target_games:
        d = dates[int(rng.integers(len(dates)))]
        for i in by_date[d]:
            idx.append(i)
            gkey.append(f"{rep}:{picks[i].game_id}")
            dkey.append(rep)
        total += ngames[d]
        rep += 1
    pkey = [picks[i].player_id for i in idx]
    gi, n_g = _ids(gkey)
    di, n_d = _ids(dkey)
    pi, n_p = _ids(pkey)
    gpi, n_gp = _ids([f"{g}|{q}" for g, q in zip(gkey, pkey, strict=True)])
    return {
        "idx": np.asarray(idx, dtype=np.int64), "gi": gi, "n_g": n_g, "di": di, "n_d": n_d,
        "pi": pi, "n_p": n_p, "gpi": gpi, "n_gp": n_gp,
    }  # fmt: skip


def synthetic_power(
    picks: list[Pick],
    games_grid: tuple[int, ...] = GAMES_GRID,
    n_synth: int = N_SYNTH,
    seed: int = SEED,
) -> dict[str, Any]:
    """Power of the best-price ROI under synthetic outcomes on the real pick structure, with the
    rule's correlation model (10.9(a), M1): latent z = sqrt(0.3) u_(game,player) + sqrt(0.7) e,
    over iff Phi(z) < p_over, and a player-persistent shift N(0, 0.03) on every p_over of that player
    (clipped to [0.01, 0.99]). SEs clustered by game, by date and by player; the widest decides (M4).
    Truths: 'consensus' (market right) and 'half' (p_cons + 0.5 (p_cal - p_cons)). Picks must be
    sorted (M6) so the draws reproduce."""
    from scipy.special import ndtr

    side_over = np.array([x.side == "over" for x in picks])
    dec_all = np.array([best_venue(x.venues)[1] for x in picks])
    base = {
        "consensus": np.array([x.p_cons for x in picks]),
        "half": np.array([x.p_cons + 0.5 * (x.p_cal - x.p_cons) for x in picks]),
    }
    z1, z2 = 2.5758, 2.0537  # one-sided 0.005 (Read 1) and 0.02 (Read 2)
    out: dict[str, Any] = {
        "model": "corr 0.3 within (game, player); player shift sd 0.03; widest of game/date/player",
        "n_synth": n_synth,
        "seed": seed,
    }
    for gg in games_grid:
        sk = season_structure(picks, gg, seed + gg)
        idx = sk["idx"]
        so, dec = side_over[idx], dec_all[idx]
        cell: dict[str, Any] = {
            "picks": len(idx), "games": sk["n_g"], "dates": sk["n_d"], "players": sk["n_p"],
            "picks_per_player_median": float(np.median(np.bincount(sk["pi"]))),
        }  # fmt: skip
        for truth, b in base.items():
            rng = np.random.default_rng(seed + 7 * gg + (0 if truth == "consensus" else 1))
            bo = b[idx]
            rows = []
            for _ in range(n_synth):
                shift = rng.normal(0.0, 0.03, sk["n_p"])[sk["pi"]]
                po = np.clip(bo + shift, 0.01, 0.99)
                u = rng.standard_normal(sk["n_gp"])[sk["gpi"]]
                z = math.sqrt(0.3) * u + math.sqrt(0.7) * rng.standard_normal(len(idx))
                over = ndtr(z) < po
                win = over == so
                r = np.where(win, dec - 1.0, -1.0)
                truth_roi = float(np.mean(np.where(so, po, 1 - po) * dec - 1.0))
                se = [
                    clustered_se(r, sk["gi"], sk["n_g"]),
                    clustered_se(r, sk["di"], sk["n_d"]),
                    clustered_se(r, sk["pi"], sk["n_p"]),
                ]
                rows.append((float(r.mean()), truth_roi, *se))
            a = np.asarray(rows)
            m, t, w = a[:, 0], a[:, 1], a[:, 2:].max(axis=1)
            cell[truth] = {
                "expected_roi": round(float(t.mean()), 4),
                "se_game": round(float(a[:, 2].mean()), 4),
                "se_date": round(float(a[:, 3].mean()), 4),
                "se_player": round(float(a[:, 4].mean()), 4),
                "se_widest": round(float(w.mean()), 4),
                "widest_is_player_share": round(
                    float(np.mean(a[:, 4] >= a[:, 2:4].max(axis=1))), 3
                ),
                "half_width_99": round(float(z1 * w.mean()), 4),
                "half_width_96": round(float(z2 * w.mean()), 4),
                "p_npef_read1_level": round(float(np.mean(m + z1 * w < 0.01)), 3),
                "p_npef_read2_level": round(float(np.mean(m + z2 * w < 0.01)), 3),
                "p_pass_read1_level": round(float(np.mean((m >= 0.01) & (m - z1 * w > 0))), 3),
                "p_pass_read2_level": round(float(np.mean((m >= 0.01) & (m - z2 * w > 0))), 3),
                "normal_lower_above_truth_read1": round(float(np.mean(m - z1 * w > t)), 3),
                "normal_lower_above_truth_read2": round(float(np.mean(m - z2 * w > t)), 3),
            }
        out[str(gg)] = cell
    return out


def structure_counts(picks: list[Pick]) -> dict[str, Any]:
    """Label-free structure the review asked for: player persistence (M4), the overs-only slice
    (D_OVER), the best-vs-second-best gap and the expected ROI at the second-best price under the
    consensus truth (S7), and counts by the best venue's deviation from consensus (S11)."""
    by_player: dict[int, list[Pick]] = defaultdict(list)
    for x in picks:
        by_player[x.player_id].append(x)
    sizes = sorted((len(v) for v in by_player.values()), reverse=True)
    same_side = [
        max(sum(y.side == "over" for y in v), sum(y.side == "under" for y in v)) / len(v)
        for v in by_player.values()
        if len(v) >= 5
    ]
    gp = Counter((x.game_id, x.player_id) for x in picks)
    gaps: list[float] = []
    roi2: list[float] = []
    dev_bins: Counter[str] = Counter()
    for x in picks:
        ds = sorted(x.venues.values(), reverse=True)
        gaps.append(ds[0] - ds[1])
        p_side = x.p_cons if x.side == "over" else 1 - x.p_cons
        roi2.append(p_side * ds[1] - 1.0)
    overs = [x for x in picks if x.side == "over"]
    for x in picks:
        bv, _ = best_venue(x.venues)
        dev_bins["best_is_kalshi" if bv == KALSHI else "best_is_book"] += 1
    return {
        "players": len(by_player),
        "picks_per_player_median": float(np.median(sizes)),
        "top5_players_pick_share": round(sum(sizes[:5]) / len(picks), 4),
        "same_side_share_players_ge5_picks_mean": round(float(np.mean(same_side)), 4)
        if same_side
        else None,
        "share_picks_on_game_player_with_ge2_picks": round(
            sum(c for c in gp.values() if c >= 2) / len(picks), 4
        ),
        "best_minus_second_net_decimal": _q(gaps, 4),
        "share_gap_gt_0.10": round(float(np.mean(np.asarray(gaps) > 0.10)), 4),
        "expected_roi_second_best_if_consensus_true": round(float(np.mean(roi2)), 4),
        "d_over": {"picks": len(overs), "games": len({x.game_id for x in overs})},
        "best_venue_kind": dict(dev_bins),
    }


def sort_picks(picks: list[Pick]) -> list[Pick]:
    """M6: the one order used before any seeded draw."""
    return sorted(picks, key=lambda x: (x.game_date, x.game_id, x.player_id, x.stat))


def projection(root: Path, start: date, picks_per_game: float) -> dict[str, Any]:
    """Scheduled regular-season games and dates from ``start`` to each read cutoff (Cup-week gap
    excluded), times capture fractions. Counts only."""
    s = pl.read_parquet(root / "data/schedule/live_tips_2026-27.parquet")
    est = [
        datetime.fromisoformat(x.replace("Z", "")).date() for x in s["gameDateTimeEst"].to_list()
    ]
    ids = s["gameId"].to_list()
    rows = [(g, d) for g, d in zip(ids, est, strict=True) if g.startswith("00226") and d >= start]
    rows = [(g, d) for g, d in rows if not (CUP_GAP[0] <= d <= CUP_GAP[1])]
    out: dict[str, Any] = {"window_start": str(start)}
    for name, cut in (("read1", READ1_LAST_TIP), ("read2", READ2_LAST_TIP)):
        gs = [d for _, d in rows if d <= cut]
        nd = len(set(gs))
        out[name] = {
            "last_tip_date": str(cut),
            "scheduled_games": len(gs),
            "scheduled_dates": nd,
            "by_capture_fraction": {
                str(f): {
                    "games": round(len(gs) * f),
                    "dates": round(nd * f),
                    "picks": round(len(gs) * f * picks_per_game),
                }
                for f in (1.0, 0.8, 0.72, 0.6)
            },
        }
    return out


def summarise(picks: list[Pick], arm: str) -> dict[str, Any]:
    if not picks:
        return {"arm": arm, "n": 0}
    g = {x.game_id for x in picks}
    d = sorted({x.game_date for x in picks})
    nv = Counter(len(x.venues) for x in picks)
    best = Counter(best_venue(x.venues)[0] for x in picks)
    cov = {v: sum(v in x.venues for x in picks) for v in U1}
    dk = [x for x in picks if "draftkings" in x.venues]
    gain = [best_venue(x.venues)[1] / x.venues["draftkings"] - 1.0 for x in dk]
    k_any = [x for x in picks if KALSHI in x.venues]
    k_mk = [x for x in k_any if x.kalshi_maker is not None]
    e_cons, _ = implied_roi(picks, "consensus")
    e_cal, _ = implied_roi(picks, "cal")
    per_date = Counter(str(x.game_date) for x in picks)
    games_per_date: dict[str, set[str]] = defaultdict(set)
    for x in picks:
        games_per_date[str(x.game_date)].add(x.game_id)
    return {
        "arm": arm,
        "n_picks": len(picks),
        "n_games": len(g),
        "n_dates": len(d),
        "first_date": str(d[0]),
        "last_date": str(d[-1]),
        "picks_per_game": round(len(picks) / len(g), 3),
        "by_stat": dict(Counter(x.stat for x in picks)),
        "by_side": dict(Counter(x.side for x in picks)),
        "venues_per_pick": {str(k): v for k, v in sorted(nv.items())},
        "venue_coverage_of_picks": {
            v: {"n": c, "share": round(c / len(picks), 4)} for v, c in cov.items()
        },
        "best_venue_counts": dict(best),
        "draftkings_comparator": {
            "n": len(dk),
            "share": round(len(dk) / len(picks), 4),
            "mean_price_gain_best_over_dk": round(float(np.mean(gain)), 4) if gain else None,
            "share_best_strictly_better_than_dk": round(
                float(np.mean([x > 1e-12 for x in gain])), 4
            )
            if gain
            else None,
        },
        "kalshi": {
            "n_picks_kalshi_quoted": len(k_any),
            "n_maker_possible": len(k_mk),
        },
        "label_free_expectations": {
            "roi_if_consensus_true": round(e_cons, 4),
            "roi_if_cal_true": round(e_cal, 4),
            "note": "expectations over declared truths, not outcomes",
        },
        "per_date": {
            k: {"picks": v, "games": len(games_per_date[k])} for k, v in sorted(per_date.items())
        },
        "pick_side_agrees_raw_vs_cal": round(
            float(np.mean([x.raw_side == x.side for x in picks])), 4
        )
        if arm == "cal"
        else None,
    }


def _q(xs: list[float], nd: int = 1) -> dict[str, float] | None:
    if not xs:
        return None
    a = np.asarray(xs, dtype=float)
    return {f"p{p}": round(float(np.percentile(a, p)), nd) for p in (5, 25, 50, 75, 95)} | {
        "n": float(len(a))
    }


def data_version() -> str:
    try:
        from nba.datamanifest.manifest import latest_data_version

        return str(latest_data_version())
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({type(exc).__name__})"


def run(root: Path, since: date, until: date, window_start: date) -> dict[str, Any]:
    if since < SINCE or until.year > 2027:
        raise ValueError("window outside season 2026")
    t0 = time.time()
    st = GateState()
    print("[gate] opening market_asof (read-only)", flush=True)
    odds = connect_ro(root / "data/markets/market_asof.duckdb")
    entries = load_entry_stamps(odds, since, until)
    books, post_entry_refused = load_book_quotes(odds, since, until, entries)
    cap_max = odds.execute(
        "SELECT max(received_at) FROM live_book_odds WHERE slate_date <= ?", [until]
    ).fetchone()
    odds.close()
    print(
        f"[gate] {len(entries)} games with a t60 stamp; {len(books)} (game, player, stat) book keys; {time.time() - t0:.0f}s",
        flush=True,
    )
    nba = connect_ro(root / "nba.duckdb")
    preds, gdate, n_sched = load_predictions(nba, entries, since, until)
    nba.close()
    entries = {g: v for g, v in entries.items() if g in gdate}
    print(
        f"[gate] {len(preds)} production rows at or before E; {time.time() - t0:.0f}s", flush=True
    )
    entry_by_pd: dict[tuple[int, date], tuple[str, datetime]] = {}
    for gid, _stat, pid, _m, _js in preds:
        entry_by_pd[(int(pid), gdate[gid])] = (gid, entries[gid][0])
    kal = connect_ro(root / "data/kalshi/kalshi.duckdb")
    kalshi = load_kalshi(kal, entry_by_pd)
    kmax = kal.execute(
        "SELECT max(ts) FROM kalshi_prices WHERE source = 'live' AND ts < ?",
        [datetime(until.year, until.month, until.day) + timedelta(days=2)],
    ).fetchone()
    kal.close()
    print(
        f"[gate] {len(kalshi)} (game, player, stat) Kalshi keys at E; {time.time() - t0:.0f}s",
        flush=True,
    )
    cmap = lc.load_map()
    if cmap.meta["map_sha256"] != MAP_SHA:
        raise ValueError("served LINE_CALIBRATION map differs from the frozen one")
    cal_picks, raw_picks = build_picks(preds, gdate, entries, books, kalshi, cmap, st)
    cal_picks, raw_picks = sort_picks(cal_picks), sort_picks(raw_picks)
    st.why["post_entry_quote"] = post_entry_refused
    print(
        f"[gate] picks: cal {len(cal_picks)}, raw {len(raw_picks)}; {time.time() - t0:.0f}s",
        flush=True,
    )
    s_cal = summarise(cal_picks, "cal")
    s_raw = summarise(raw_picks, "raw")
    ppg = s_cal.get("picks_per_game", 0.0) or 0.0
    games_with_pred = len({p[0] for p in preds})
    out: dict[str, Any] = {
        "rule": RULE,
        "status": "DRAFT gate; counts and label-free expectations only; no outcome read",
        "data_version": data_version(),
        "capture_versions": {
            "live_book_odds_max_received_at": str(cap_max[0]) if cap_max else None,
            "kalshi_prices_max_ts": str(kmax[0]) if kmax else None,
        },
        "window_counted": {"since": str(since), "until": str(until)},
        "params": {
            "delta": DELTA,
            "stale_dev": STALE_DEV,
            "book_max_last_update_min": BOOK_MAX_LAST_UPDATE_MIN,
            "kalshi_max_age_min": KALSHI_MAX_AGE_MIN,
            "min_p_play": MIN_P_PLAY,
            "min_venues": MIN_VENUES,
            "u1": list(U1),
            "other_venues_counted_only": list(OTHER_VENUES),
            "cal_book": CAL_BOOK,
            "map_sha256": MAP_SHA,
            "snapshot": SNAPSHOT,
            "fee_coef": FEE_COEF,
        },  # fmt: skip
        "games": {
            "scheduled_season_2026_in_window": n_sched,
            "with_t60_stamp": len(entries),
            "with_production_rows_at_or_before_E": games_with_pred,
        },
        "rows": dict(st.why),
        "venue_rows_with_any_two_sided_quote": dict(st.venue_quotes_any),
        "venue_rows_quoting_reference_point": dict(st.venue_at_ref),
        "u1_venues_after_stale_filter_hist": {
            str(k): v for k, v in sorted(st.n_venues_hist.items())
        },
        "stale_dropped": dict(st.stale_dropped),
        "book_last_update_age_min_at_E": _q(st.book_lu_age),
        "kalshi_snapshot_age_min_at_E": _q(st.kalshi_age),
        "cal_arm": s_cal,
        "raw_arm": s_raw,
        "power_cal_arm": synthetic_power(cal_picks) if cal_picks else None,
        "projection": projection(root, window_start, float(ppg)),
        "structure_cal_arm": structure_counts(cal_picks) if cal_picks else None,
    }
    print(f"[gate] done in {time.time() - t0:.1f}s", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("/Users/devin/Downloads/nba-prediction"))
    ap.add_argument("--since", type=date.fromisoformat, default=SINCE)
    ap.add_argument("--until", type=date.fromisoformat, required=True)
    ap.add_argument("--window-start", type=date.fromisoformat, default=date(2026, 11, 11))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    res = run(a.data_root, a.since, a.until, a.window_start)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, default=str, sort_keys=True) + "\n")
    print(f"[gate] wrote {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
