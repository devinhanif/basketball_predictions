"""Market-surfer PAPER test (docs/prereg/MARKET_SURFER_PAPER.md, rule market-surfer-paper-v1).

Offline replay of a momentum-entry / take-profit / stop round-trip rule on captured Kalshi pages.
Read-only: it reads stored raw files and a read-only settlement table. There is no network call,
no credential, no order endpoint and nothing that can reach one (CLAUDE.md non-negotiable 5).
Fees come from the verified
``fee_model`` object in ``configs/parlay.yaml`` (never a number here). Parameters come from
``configs/market_surfer.yaml``; nothing is tuned after a result.
"""

from __future__ import annotations

import bisect
import gzip
import json
import math
import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from nba.parlay.config import FeeModel
from nba.parlay.ev import order_fee

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "market_surfer.yaml"
ESPN_TO_KALSHI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}
_EVENT = re.compile(r"^(KXNBA[A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
_MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC"
_MONTH = {m: i + 1 for i, m in enumerate(_MONTHS.split(" "))}
EPS = 1e-9


def load_params(path: Path = CONFIG_PATH) -> dict[str, Any]:
    out = yaml.safe_load(path.read_text())
    assert isinstance(out, dict)
    return out


# ----------------------------------------------------------------------------- data model


@dataclass(frozen=True)
class Rec:
    """One YES-side quote of one market at one series snapshot."""

    idx: int  # snapshot index within the series file list
    t: float  # epoch seconds
    yb: float  # yes bid (0 when missing)
    ya: float  # yes ask (1 or more when missing)
    ybs: float  # yes bid size
    yas: float  # yes ask size


def side_view(r: Rec, side: int) -> tuple[float, float, float, float]:
    """(bid, ask, bid_size, ask_size) executable on ``side`` (0 = YES, 1 = NO); NaN when missing."""
    nan = float("nan")
    if side == 0:
        bid = r.yb if r.yb > 0 else nan
        ask = r.ya if 0 < r.ya < 1 else nan
        return bid, ask, r.ybs, r.yas
    bid = 1.0 - r.ya if 0 < r.ya < 1 else nan
    ask = 1.0 - r.yb if r.yb > 0 else nan
    return bid, ask, r.yas, r.ybs


@dataclass
class Market:
    ticker: str
    series: str
    event: str  # e.g. 26OCT22DENOKC (game key)
    night: str  # YYYY-MM-DD from the event date
    result: str  # yes | no
    teams: tuple[str, str]  # (away, home) Kalshi abbreviations
    recs: list[Rec] = field(default_factory=list)
    inplay: list[bool] = field(default_factory=list)
    by_idx: dict[int, int] = field(default_factory=dict)  # snapshot idx -> position in recs


@dataclass(frozen=True)
class Trade:
    night: str
    event: str
    series: str
    ticker: str
    side: int
    t_decision: float
    t_fill: float
    entry_ask: float
    entry_mid: float
    exit_price: float
    exit_mid: float | None
    t_exit: float | None
    reason: str  # tp | stop | time | settle
    fee_entry: float  # per contract
    fee_exit: float
    pnl: float  # net per contract
    pnl_hold: float
    pnl_gross_mid: float
    spread_cost: float


def parse_event(event_ticker: str) -> tuple[str, str, tuple[str, str]] | None:
    """(game key like 26OCT22DENOKC, night YYYY-MM-DD, (away, home)) from an event ticker."""
    m = _EVENT.match(event_ticker)
    if not m:
        return None
    yy, mon, dd = m[2], m[3], m[4]
    if mon not in _MONTH:
        return None
    night = f"20{yy}-{_MONTH[mon]:02d}-{dd}"
    return f"{yy}{mon}{dd}{m[5]}{m[6]}", night, (m[5], m[6])


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------------- games


@dataclass
class Game:
    teams: frozenset[str]
    tip: float
    times: list[float] = field(default_factory=list)
    states: list[str] = field(default_factory=list)

    def state_at(self, t: float) -> str | None:
        k = bisect.bisect_right(self.times, t) - 1
        return self.states[k] if k >= 0 else None

    def in_play(self, t: float) -> bool:
        return t >= self.tip and self.state_at(t) != "post"


def parse_scoreboard_files(files: list[tuple[float, bytes]]) -> dict[frozenset[str], Game]:
    games: dict[frozenset[str], Game] = {}
    for ts, body in sorted(files, key=lambda x: x[0]):
        try:
            events = json.loads(body).get("events", [])
        except ValueError:
            continue
        for ev in events:
            try:
                comp = ev["competitions"][0]
                teams = frozenset(
                    ESPN_TO_KALSHI.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
                    for c in comp["competitors"]
                )
                tip = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                if tip.tzinfo is None:
                    tip = tip.replace(tzinfo=UTC)
                state = str(ev["status"]["type"]["state"])
            except (KeyError, IndexError, ValueError):
                continue
            g = games.setdefault(teams, Game(teams, tip.timestamp()))
            g.times.append(ts)
            g.states.append(state)
    return games


# ----------------------------------------------------------------------------- loading


def _ts_from_name(name: str) -> float:
    return datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp()


def load_night(
    root: Path,
    night: str,
    series: list[str],
    results: dict[str, str],
) -> tuple[dict[str, Market], dict[str, Any]]:
    """Parse one night's stored market pages. Returns (markets by ticker, audit counters)."""
    audit: dict[str, Any] = {"unmapped_events": [], "no_result_markets": 0, "scalar_or_empty": 0}
    sb_dir = root / "espn_scoreboard"
    sb_files: list[tuple[float, bytes]] = []
    for p in sorted(sb_dir.glob("*/*.json.gz")):
        sb_files.append((_ts_from_name(p.name), gzip.decompress(p.read_bytes())))
    games = parse_scoreboard_files(sb_files)
    markets: dict[str, Market] = {}
    skipped: set[str] = set()
    snap_times: dict[str, list[float]] = {}
    for s in series:
        files = sorted((root / "kalshi" / f"{s}_p0").glob("*.json.gz"))
        snap_times[s] = [_ts_from_name(p.name) for p in files]
        for idx, p in enumerate(files):
            t = snap_times[s][idx]
            body = json.loads(gzip.decompress(p.read_bytes()))
            for mk in body.get("markets", []):
                pe = parse_event(str(mk.get("event_ticker", "")))
                if pe is None or pe[1] != night:
                    continue
                tk = str(mk["ticker"])
                if tk in skipped:
                    continue
                m = markets.get(tk)
                if m is None:
                    res = results.get(tk, "")
                    if res not in ("yes", "no"):
                        skipped.add(tk)
                        audit["no_result_markets"] += 1
                        audit["scalar_or_empty"] += 1 if res else 0
                        continue
                    m = Market(tk, s, pe[0], pe[1], res, pe[2])
                    markets[tk] = m
                m.recs.append(
                    Rec(
                        idx,
                        t,
                        _f(mk.get("yes_bid_dollars")),
                        _f(mk.get("yes_ask_dollars")) or 1.0,
                        _f(mk.get("yes_bid_size_fp")),
                        _f(mk.get("yes_ask_size_fp")),
                    )
                )
    out: dict[str, Market] = {}
    for tk, m in markets.items():
        g = games.get(frozenset(m.teams))
        if g is None:
            if m.event not in audit["unmapped_events"]:
                audit["unmapped_events"].append(m.event)
            continue
        m.by_idx = {r.idx: k for k, r in enumerate(m.recs)}
        m.inplay = [g.in_play(r.t) for r in m.recs]
        out[tk] = m
    audit["markets_used"] = len(out)
    audit["snapshots"] = {s: len(v) for s, v in snap_times.items()}
    audit["snap_times"] = snap_times
    return out, audit


# ----------------------------------------------------------------------------- simulation


def _valid_side(r: Rec, side: int, P: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bid, ask, bs, as_ = side_view(r, side)
    if math.isnan(bid) or math.isnan(ask) or ask <= bid:
        return None
    return bid, ask, bs, as_


def entry_ok(m: Market, side: int, pos: int, P: dict[str, Any], momentum: bool) -> bool:
    e = P["entry"]
    if not m.inplay[pos]:
        return False
    r = m.recs[pos]
    v = _valid_side(r, side, P)
    if v is None:
        return False
    bid, ask, bs, as_ = v
    if not (e["ask_min"] - EPS <= ask <= e["ask_max"] + EPS):
        return False
    if ask - bid > e["max_spread"] + EPS:
        return False
    if bs < e["min_top_size"] or as_ < e["min_top_size"]:
        return False
    if not momentum:
        return True
    k0 = m.by_idx.get(r.idx - e["momentum_lookback_snapshots"])
    if k0 is None:
        return False
    r0 = m.recs[k0]
    if r.t - r0.t > e["lookback_max_span_s"]:
        return False
    v0 = _valid_side(r0, side, P)
    if v0 is None:
        return False
    return (bid + ask) / 2 - (v0[0] + v0[1]) / 2 >= e["momentum_min_rise"] - EPS


def fill_entry(m: Market, side: int, pos: int, P: dict[str, Any]) -> int | None:
    """Position of the fill record (decision at ``pos``, fill on the next snapshot) or None."""
    if pos + 1 >= len(m.recs):
        return None
    r, r1 = m.recs[pos], m.recs[pos + 1]
    f = P["fill"]
    if r1.idx != r.idx + f["latency_snapshots"] or r1.t - r.t > f["max_gap_s"]:
        return None
    v, v1 = _valid_side(r, side, P), _valid_side(r1, side, P)
    if v is None or v1 is None:
        return None
    if v1[1] > v[1] + f["max_slip"] + EPS or v1[3] < P["entry"]["min_top_size"]:
        return None
    return pos + 1


def simulate(
    m: Market,
    side: int,
    pos: int,
    P: dict[str, Any],
    fee: FeeModel,
    fpos: int | None = None,
) -> tuple[Trade | None, int]:
    """Run one trade decided at ``pos``: (trade or None if missed, position of the exit fill).

    The exit position is len(recs) for a settlement exit (nothing further can be entered).
    """
    fpos = fill_entry(m, side, pos, P) if fpos is None else fpos
    if fpos is None:
        return None, pos
    n = int(P["unit_contracts"])
    rf = m.recs[fpos]
    bid_f, ask_f, _, _ = side_view(rf, side)
    mid_f = (bid_f + ask_f) / 2
    e_fee = order_fee(ask_f, fee, n, order_type=P["exit"]["order_type"], series=m.series) / n
    ex = P["exit"]
    won = (m.result == "yes") if side == 0 else (m.result == "no")
    payout = 1.0 if won else 0.0
    reason, k_exit, x_price, x_mid, x_fee = "settle", None, payout, None, 0.0
    min_size = P["entry"]["min_top_size"]
    for u in range(fpos + 1, len(m.recs)):
        ru = m.recs[u]
        bid_u = side_view(ru, side)[0]
        if math.isnan(bid_u):
            continue
        trig = None
        if bid_u >= ask_f * (1 + ex["take_profit_rel"]) - EPS:
            trig = "tp"
        elif bid_u <= ask_f * (1 - ex["stop_rel"]) + EPS:
            trig = "stop"
        elif ru.t - rf.t >= ex["time_stop_s"]:
            trig = "time"
        if trig is None:
            continue
        for k in (u + 1, u + 2):  # fill on the next snapshot; wait one more if it is not fillable
            if k >= len(m.recs):
                break
            rk = m.recs[k]
            if rk.t - m.recs[k - 1].t > P["fill"]["max_gap_s"]:
                continue
            vk = _valid_side(rk, side, P)
            if vk is None or vk[2] < min_size or math.isnan(vk[0]):
                continue
            k_exit, reason = k, trig
            x_price, x_mid = vk[0], (vk[0] + vk[1]) / 2
            x_fee = order_fee(x_price, fee, n, order_type=ex["order_type"], series=m.series) / n
            break
        break  # a trigger fired: either it filled or it falls to settlement
    pnl = x_price - ask_f - e_fee - x_fee
    gross_mid = (x_mid if x_mid is not None else payout) - mid_f
    spread = (ask_f - mid_f) + ((x_mid - x_price) if x_mid is not None else 0.0)
    tr = Trade(
        m.night,
        m.event,
        m.series,
        m.ticker,
        side,
        m.recs[pos].t,
        rf.t,
        ask_f,
        mid_f,
        x_price,
        x_mid,
        m.recs[k_exit].t if k_exit is not None else None,
        reason,
        e_fee,
        x_fee,
        pnl,
        payout - ask_f - e_fee,
        gross_mid,
        spread,
    )
    return tr, (k_exit if k_exit is not None else len(m.recs))


def run_market(m: Market, P: dict[str, Any], fee: FeeModel) -> tuple[list[Trade], int]:
    """The frozen entry scan over one market. Returns (trades, missed fills)."""
    trades: list[Trade] = []
    missed = 0
    blocked_until = -1.0
    pos = 0
    n = len(m.recs)
    while pos < n:
        r = m.recs[pos]
        if r.t < blocked_until:
            pos += 1
            continue
        took = False
        for side in (0, 1):
            if not entry_ok(m, side, pos, P, momentum=True):
                continue
            tr, kx = simulate(m, side, pos, P, fee)
            if tr is None:
                missed += 1
                continue
            trades.append(tr)
            if kx >= n:
                return trades, missed  # settled: market done
            blocked_until = m.recs[kx].t + P["entry"]["cooldown_s"]
            pos = kx
            took = True
            break
        if not took:
            pos += 1
    return trades, missed


def run_all(
    markets: dict[str, Market], P: dict[str, Any], fee: FeeModel
) -> tuple[list[Trade], int]:
    trades: list[Trade] = []
    missed = 0
    for m in markets.values():
        tr, ms = run_market(m, P, fee)
        trades += tr
        missed += ms
    return trades, missed


# ----------------------------------------------------------------------------- placebo


def placebo_pools(
    markets: dict[str, Market], P: dict[str, Any]
) -> dict[tuple[str, str], list[tuple[str, int, int, int]]]:
    """Eligible draws (conditions 1, 2, valid fill) per (night, series)."""
    pools: dict[tuple[str, str], list[tuple[str, int, int, int]]] = defaultdict(list)
    for tk, m in markets.items():
        for pos in range(len(m.recs)):
            if not m.inplay[pos]:
                continue
            for side in (0, 1):
                if entry_ok(m, side, pos, P, momentum=False):
                    fp = fill_entry(m, side, pos, P)
                    if fp is not None:
                        pools[(m.night, m.series)].append((tk, side, pos, fp))
    return pools


def placebo_means(
    real: list[Trade],
    markets: dict[str, Market],
    pools: dict[tuple[str, str], list[tuple[str, int, int, int]]],
    P: dict[str, Any],
    fee: FeeModel,
) -> list[float]:
    rng = random.Random(P["placebo"]["seed"])
    cache: dict[tuple[str, int, int], float] = {}
    out: list[float] = []
    for _ in range(int(P["placebo"]["replications"])):
        vals: list[float] = []
        for t in real:
            pool = pools.get((t.night, t.series))
            if not pool:
                continue
            tk, side, pos, fp = pool[rng.randrange(len(pool))]
            key = (tk, side, pos)
            if key not in cache:
                tr, _ = simulate(markets[tk], side, pos, P, fee, fpos=fp)
                cache[key] = tr.pnl if tr is not None else float("nan")
            if not math.isnan(cache[key]):
                vals.append(cache[key])
        out.append(float(np.mean(vals)) if vals else float("nan"))
    return out


# ----------------------------------------------------------------------------- statistics


def game_bootstrap(
    trades: list[Trade], values: list[float], boot: int, seed: int, level: float
) -> tuple[float, float, float]:
    """(mean, lo, hi): percentile bootstrap over games (event pair) of the mean per-trade value."""
    by: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(trades, values, strict=True):
        by[t.night + t.event].append(v)
    sums = np.array([sum(v) for v in by.values()])
    cnts = np.array([len(v) for v in by.values()], dtype=float)
    g = len(sums)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, g, size=(boot, g))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    a = (1 - level) / 2
    return (
        float(sums.sum() / cnts.sum()),
        float(np.quantile(means, a)),
        float(np.quantile(means, 1 - a)),
    )


def night_t_interval(
    trades: list[Trade], values: list[float], level: float
) -> tuple[float, float, float, int]:
    """(mean of per-night means, lo, hi, k nights) with a t interval on k-1 df."""
    from scipy import stats

    by: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(trades, values, strict=True):
        by[t.night].append(v)
    mus = [statistics.fmean(v) for v in by.values()]
    k = len(mus)
    mu = statistics.fmean(mus)
    if k < 2:
        return mu, float("nan"), float("nan"), k
    se = statistics.stdev(mus) / math.sqrt(k)
    q = float(stats.t.ppf(1 - (1 - level) / 2, k - 1))
    return mu, mu - q * se, mu + q * se, k


def verdict(
    n_trades: int,
    nights: int,
    games: int,
    mean: float,
    night_ci: tuple[float, float],
    game_ci: tuple[float, float],
    placebo_hi: float,
    hold_mean: float,
    gate: dict[str, Any],
) -> str:
    """The frozen verdict rule."""
    if n_trades < gate["run_min_trades"] or nights < gate["run_min_nights"]:
        return "insufficient_sample"
    if (
        nights >= gate["claim_min_nights"]
        and n_trades >= gate["claim_min_trades"]
        and games >= gate["claim_min_games"]
        and mean > 0
        and night_ci[0] > 0
        and game_ci[0] > 0
        and mean > placebo_hi
        and mean > hold_mean
    ):
        return "positive_ev_candidate"
    if mean < 0 and game_ci[1] < 0 and games >= gate["claim_min_games"]:
        return "negative_ev_supported"
    return "no_positive_ev_found"
