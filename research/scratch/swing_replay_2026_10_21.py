# ruff: noqa: E501
"""Exploratory hindsight replay of the 2026-10-21 KXNBAGAME moneylines (SCRATCH, not a claim).

PRIOR written before the grid was computed (2026-10-22 18:15Z):
  P(any rule in the grid beats hold-to-settle out of sample) = 0.15.

Read-only on data/raw/live_2026-10-21. No order, auth or execution code. Fees come from
configs/parlay.yaml (taker, charged on entry and exit; a held position pays the settlement payout).
Buy at the ask, sell at the bid, never trade with ask >= 1.00 or bid <= 0.
10-21 is the exploration night: it may not score a frozen swing rule.

Run: python research/scratch/swing_replay_2026_10_21.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nba.live import kalshi_pull as kp  # noqa: E402
from nba.papertrade.rules import plan_entry  # noqa: E402
from nba.parlay.config import FeeModel  # noqa: E402
from nba.parlay.ev import order_fee  # noqa: E402
from nba.ride import sizing  # noqa: E402
from nba.ride.capture_view import CaptureView, GameSnap, parse_summary, read_json  # noqa: E402
from nba.ride.winprob import WinProbModel, seconds_left  # noqa: E402

ROOT = Path("data/raw/live_2026-10-21")
OUT = Path("reports/ride")
STAKE = 10.0
SERIES = "KXNBAGAME"
FRESH_S = 300.0  # our/ESPN state older than this is not used


@dataclass
class Tick:
    t: datetime
    bid: dict[str, float]  # team -> yes bid
    ask: dict[str, float]
    p_home: float | None  # our prob, None when no fresh state
    espn_home: float | None
    period: int | None
    clock: float | None
    score: str
    in_game: bool


@dataclass
class Game:
    event_id: str
    away: str
    home: str
    tip: datetime
    winner: str
    ticks: list[Tick] = field(default_factory=list)  # pre-tip tick (last) + in-game ticks

    @property
    def name(self) -> str:
        return f"{self.away}@{self.home}"

    def p_team(self, tk: Tick, team: str) -> float | None:
        if tk.p_home is None:
            return None
        return tk.p_home if team == self.home else 1.0 - tk.p_home

    def espn_team(self, tk: Tick, team: str) -> float | None:
        if tk.espn_home is None:
            return None
        return tk.espn_home if team == self.home else 1.0 - tk.espn_home


# ------------------------------------------------------------------ data
def valid_buy(ask: float | None) -> bool:
    return ask is not None and 0.0 < ask < 1.0


def valid_sell(bid: float | None) -> bool:
    return bid is not None and bid > 0.0


def load_games(root: Path = ROOT) -> list[Game]:
    view = CaptureView(root)
    model = WinProbModel.load()
    tips: dict[str, datetime] = {}
    for _, path in view.files("espn_scoreboard", "2026-10-21")[-1:]:
        for ev in (read_json(path) or {}).get("events", []):
            tips[str(ev["id"])] = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
    # all kalshi KXNBAGAME listings once: time -> ticker -> (bid, ask)
    books: list[tuple[datetime, dict[str, tuple[float | None, float | None]]]] = []
    for sub in sorted((root / "kalshi").glob("KXNBAGAME_p*")):
        for ts, path in view.files("kalshi", sub.name):
            body = read_json(path)
            snap_: dict[str, tuple[float | None, float | None]] = {}
            for m in (body or {}).get("markets", []):
                snap_[str(m["ticker"])] = (
                    kp.to_f(m.get("yes_bid_dollars")),
                    kp.to_f(m.get("yes_ask_dollars")),
                )
            books.append((ts, snap_))
    books.sort(key=lambda x: x[0])
    games: list[Game] = []
    for eid in view.event_ids():
        snaps: list[GameSnap] = []
        for ts, path in view.files("espn_summary", eid):
            body = read_json(path)
            s = parse_summary(body, eid, ts) if isinstance(body, dict) else None
            if s is not None:
                snaps.append(s)
        if not snaps or snaps[-1].state != "post" or eid not in tips:
            continue
        last = snaps[-1]
        g = Game(
            eid,
            last.away,
            last.home,
            tips[eid],
            last.home if last.home_score > last.away_score else last.away,
        )
        t_post = next(s.file_time for s in snaps if s.state == "post")
        ev_tag = f"{g.away}{g.home}"
        tickers = {tm: f"KXNBAGAME-26OCT21{ev_tag}-{tm}" for tm in (g.away, g.home)}
        pre: Tick | None = None
        for ts, bk in books:
            if tickers[g.home] not in bk or tickers[g.away] not in bk:
                continue
            bid = {tm: bk[tk][0] for tm, tk in tickers.items()}
            ask = {tm: bk[tk][1] for tm, tk in tickers.items()}
            if any(v is None for v in (*bid.values(), *ask.values())):
                continue
            b2 = {k: float(v) for k, v in bid.items() if v is not None}
            a2 = {k: float(v) for k, v in ask.items() if v is not None}
            prior = [s for s in snaps if s.file_time <= ts]
            sn = prior[-1] if prior else None
            fresh = sn is not None and (ts - sn.file_time).total_seconds() <= FRESH_S
            if ts < g.tip:
                pre = Tick(ts, b2, a2, None, None, None, None, "", False)
                continue
            if ts >= t_post:
                continue
            tk = Tick(ts, b2, a2, None, None, None, None, "", True)
            if sn is not None and fresh and sn.state == "in":
                tk.p_home = model.p_home(sn.margin, seconds_left(sn.period, sn.clock_secs))
                tk.espn_home = sn.espn_home_wp
                tk.period, tk.clock = sn.period, sn.clock_secs
                tk.score = f"{sn.away_score}-{sn.home_score}"
            g.ticks.append(tk)
        if pre is not None:
            g.ticks.insert(0, pre)
        if len(g.ticks) >= 3:
            games.append(g)
    return sorted(games, key=lambda x: x.tip)


# ------------------------------------------------------------------ money
def entry(ask: float, fee: FeeModel) -> tuple[int, float, float]:
    n, f = plan_entry(ask, fee, STAKE, SERIES)
    return n, f, n * ask + f


def exit_proceeds(bid: float, n: int, fee: FeeModel) -> float:
    return n * bid - order_fee(bid, fee, n, series=SERIES)


@dataclass
class Trade:
    team: str
    t_in: datetime
    ask: float
    t_out: datetime | None  # None = settlement
    bid_out: float | None
    n: int
    pnl: float
    why: str = ""


def settle_trade(g: Game, tk_in: Tick, team: str, fee: FeeModel) -> Trade:
    n, _, cost = entry(tk_in.ask[team], fee)
    return Trade(team, tk_in.t, tk_in.ask[team], None, None, n, n * (g.winner == team) - cost)


def sell_trade(tk_in: Tick, tk_out: Tick, team: str, fee: FeeModel, why: str) -> Trade:
    n, _, cost = entry(tk_in.ask[team], fee)
    pnl = exit_proceeds(tk_out.bid[team], n, fee) - cost
    return Trade(team, tk_in.t, tk_in.ask[team], tk_out.t, tk_out.bid[team], n, pnl, why)


# ------------------------------------------------------------------ ceilings
def best_round_trip(g: Game, fee: FeeModel, only: str | None = None) -> dict[str, Any]:
    best: dict[str, Any] = {"pnl": 0.0, "note": "no trade"}
    for team in (only,) if only else (g.away, g.home):
        for i, a in enumerate(g.ticks):
            if not valid_buy(a.ask[team]):
                continue
            n, f_in, cost = entry(a.ask[team], fee)
            mid_in = 0.5 * (a.ask[team] + a.bid[team])
            cands: list[tuple[float, int | None]] = [(n * (g.winner == team) - cost, None)]
            for j in range(i + 1, len(g.ticks)):
                b = g.ticks[j]
                if valid_sell(b.bid[team]):
                    cands.append((exit_proceeds(b.bid[team], n, fee) - cost, j))
            pnl, jout = max(cands, key=lambda c: c[0])
            if pnl > best["pnl"]:
                if jout is None:
                    spread = n * (a.ask[team] - mid_in)
                    gross = n * ((g.winner == team) - mid_in)
                    f_out, t_out, px = 0.0, "settlement", float(g.winner == team)
                else:
                    b = g.ticks[jout]
                    mid_out = 0.5 * (b.ask[team] + b.bid[team])
                    spread = n * ((a.ask[team] - mid_in) + (mid_out - b.bid[team]))
                    gross = n * (mid_out - mid_in)
                    f_out = order_fee(b.bid[team], fee, n, series=SERIES)
                    t_out, px = f"{b.t:%H:%M:%S}Z", b.bid[team]
                best = {
                    "pnl": round(pnl, 4), "team": team, "t_in": f"{a.t:%H:%M:%S}Z",
                    "ask_in": a.ask[team], "t_out": t_out, "px_out": px, "contracts": n,
                    "gross_at_mid": round(gross, 4), "spread_cost": round(spread, 4),
                    "fees": round(f_in + f_out, 4),
                }  # fmt: skip
    return best


def dp_path(g: Game, fee: FeeModel, mode: str) -> dict[str, Any]:
    """Best multi-trade path (UNATTAINABLE): non-overlapping round trips, each risking a fresh
    $10 (profits are NOT reinvested, depth is ignored), fractional contracts, unrounded per-contract
    fees. mode: 'mid_nofee' (frictionless), 'spread_nofee', 'full' (spread + taker fee both ways)."""
    rate, mult = fee.rate("taker"), fee.multiplier(SERIES)

    def pc(p: float) -> float:
        return 0.0 if mode != "full" else mult * rate * p * (1.0 - p)

    def buy_px(tk: Tick, tm: str) -> float | None:
        if mode == "mid_nofee":
            return 0.5 * (tk.bid[tm] + tk.ask[tm]) if valid_buy(tk.ask[tm]) else None
        return tk.ask[tm] if valid_buy(tk.ask[tm]) else None

    def sell_px(tk: Tick, tm: str) -> float | None:
        if mode == "mid_nofee":
            return 0.5 * (tk.bid[tm] + tk.ask[tm]) if valid_sell(tk.bid[tm]) else None
        return tk.bid[tm] if valid_sell(tk.bid[tm]) else None

    teams = (g.away, g.home)
    T = len(g.ticks)
    best = [0.0] * (T + 1)  # best[i]: best pnl having ticks < i available, flat at tick i
    n_trips = [0] * (T + 1)
    for j in range(T + 1):
        best[j], n_trips[j] = (best[j - 1], n_trips[j - 1]) if j else (0.0, 0)
        for i in range(j):
            for tm in teams:
                a = buy_px(g.ticks[i], tm)
                if a is None:
                    continue
                k = STAKE / (a + pc(a))
                if j == T:  # settlement
                    v = k * float(g.winner == tm) - STAKE
                else:
                    sp = sell_px(g.ticks[j], tm)
                    if sp is None:
                        continue
                    v = k * (sp - pc(sp)) - STAKE
                if best[i] + v > best[j] + 1e-12:
                    best[j], n_trips[j] = best[i] + v, n_trips[i] + 1
    return {"pnl": round(best[T], 4), "round_trips": n_trips[T]}


# ------------------------------------------------------------------ rules
def mid(tk: Tick, team: str) -> float | None:
    if not (valid_buy(tk.ask[team]) and valid_sell(tk.bid[team])):
        return None
    return 0.5 * (tk.ask[team] + tk.bid[team])


def momentum_at(g: Game, i: int, team: str, n_min: int) -> float | None:
    tk = g.ticks[i]
    m = mid(tk, team)
    if m is None:
        return None
    for j in range(i - 1, -1, -1):
        age = (tk.t - g.ticks[j].t).total_seconds() / 60.0
        if age >= n_min:
            if age > n_min + 3.0:
                return None
            m0 = mid(g.ticks[j], team)
            return None if m0 is None else m - m0
    return None


EntryFn = Any  # (Game, int) -> list[tuple[team, strength]]


def entry_momentum(x: float, n_min: int) -> EntryFn:
    def f(g: Game, i: int) -> list[tuple[str, float]]:
        out = []
        for tm in (g.away, g.home):
            mo = momentum_at(g, i, tm, n_min)
            if mo is not None and mo >= x - 1e-9 and valid_buy(g.ticks[i].ask[tm]):
                out.append((tm, mo))
        return out

    return f


def entry_reversion(gap: float) -> EntryFn:
    def f(g: Game, i: int) -> list[tuple[str, float]]:
        tk = g.ticks[i]
        out = []
        for tm in (g.away, g.home):
            p = g.p_team(tk, tm)
            if p is not None and valid_buy(tk.ask[tm]) and p - tk.ask[tm] >= gap - 1e-9:
                out.append((tm, p - tk.ask[tm]))
        return out

    return f


def entry_watch_band() -> EntryFn:
    """Leader (ours) has crossed 65/35 earlier or now; buy the leader once its price rose >= 5c over 5 min."""

    def f(g: Game, i: int) -> list[tuple[str, float]]:
        crossed = None
        for j in range(i + 1):
            ph = g.ticks[j].p_home
            if ph is not None and max(ph, 1 - ph) >= 0.65:
                crossed = g.home if ph >= 0.5 else g.away
                break
        if crossed is None:
            return []
        mo = momentum_at(g, i, crossed, 5)
        if mo is not None and mo >= 0.05 - 1e-9 and valid_buy(g.ticks[i].ask[crossed]):
            return [(crossed, mo)]
        return []

    return f


def run_exit(g: Game, i: int, team: str, exit_name: str, fee: FeeModel) -> Trade:
    tk_in = g.ticks[i]
    ask = tk_in.ask[team]
    peak = tk_in.bid[team]
    for j in range(i + 1, len(g.ticks)):
        tk = g.ticks[j]
        bid = tk.bid[team]
        peak = max(peak, bid)
        if not valid_sell(bid):
            continue
        hit = False
        if exit_name == "trail70":
            hit = peak > ask and bid <= ask + 0.70 * (peak - ask)
        elif exit_name == "trail60":
            stop = 0.60 * peak if peak > ask else 0.35 * ask
            hit = bid <= stop
        elif exit_name == "fair":
            p = g.p_team(tk, team)
            hit = p is not None and bid >= p + 0.03
        elif exit_name == "eoq3":
            hit = tk.period is not None and tk.period >= 4
        if hit:
            return sell_trade(tk_in, tk, team, fee, exit_name)
    return settle_trade(g, tk_in, team, fee)


def run_rule(g: Game, entry_fn: EntryFn, exit_name: str, fee: FeeModel) -> Trade | None:
    for i, tk in enumerate(g.ticks):
        if not tk.in_game:
            continue
        sig = entry_fn(g, i)
        if sig:
            team = max(sig, key=lambda s: s[1])[0]
            if exit_name == "hold":
                return settle_trade(g, tk, team, fee)
            return run_exit(g, i, team, exit_name, fee)
    return None


def baseline_favourite(g: Game, fee: FeeModel) -> Trade | None:
    pre = [t for t in g.ticks if not t.in_game]
    if not pre:
        return None
    tk = pre[-1]
    tm = max((g.away, g.home), key=lambda x: 0.5 * (tk.bid[x] + tk.ask[x]))
    return settle_trade(g, tk, tm, fee) if valid_buy(tk.ask[tm]) else None


def baseline_ride98(g: Game, fee: FeeModel) -> Trade | None:
    for tk in g.ticks:
        if tk.p_home is None or tk.espn_home is None:
            continue
        tm = g.home if tk.p_home >= 0.5 else g.away
        ours, espn = g.p_team(tk, tm), g.espn_team(tk, tm)
        agree = ours is not None and espn is not None and ours >= 0.98 and abs(ours - espn) <= 0.03
        if agree and valid_buy(tk.ask[tm]):
            return settle_trade(g, tk, tm, fee)
    return None


def boot_ci(per_game: list[float], n: int = 4000, seed: int = 11) -> list[float]:
    rng = np.random.default_rng(seed)
    a = np.array(per_game)
    means = [a[rng.integers(0, len(a), len(a))].mean() for _ in range(n)]
    return [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)]


def summarise(games: list[Game], trades: dict[str, Trade | None]) -> dict[str, Any]:
    per = [trades[g.name].pnl if trades[g.name] else 0.0 for g in games]  # type: ignore[union-attr]
    done = [t for t in trades.values() if t is not None]
    return {
        "mean_pnl_per_game_per_10": round(float(np.mean(per)), 3),
        "ci95_game_bootstrap": boot_ci(per),
        "total_pnl": round(float(np.sum(per)), 2),
        "trades": len(done),
        "hit_rate": round(sum(t.pnl > 0 for t in done) / len(done), 3) if done else None,
        "n_games": len(games),
    }


# ------------------------------------------------------------------ main
def main() -> int:
    fee = sizing.load_fee()
    games = load_games()
    out: dict[str, Any] = {
        "prior": "P(any rule beats hold-to-settle out of sample) = 0.15 (written before the grid)",
        "n_games": len(games),
        "ticks_per_game": {g.name: len(g.ticks) for g in games},
    }
    ceilings = {}
    for g in games:
        ceilings[g.name] = {
            "best_round_trip": best_round_trip(g, fee),
            "dp_full": dp_path(g, fee, "full"),
            "dp_spread_only": dp_path(g, fee, "spread_nofee"),
            "dp_frictionless_mid": dp_path(g, fee, "mid_nofee"),
        }
    out["ceilings"] = ceilings
    out["ceiling_totals"] = {
        k: round(sum(c[k]["pnl"] for c in ceilings.values()), 2)
        for k in ("best_round_trip", "dp_full", "dp_spread_only", "dp_frictionless_mid")
    }
    entries: dict[str, EntryFn] = {
        f"momentum_x{int(x * 100)}c_n{n}m": entry_momentum(x, n)
        for x in (0.05, 0.10)
        for n in (5, 10)
    }
    entries |= {f"reversion_g{int(gap * 100)}c": entry_reversion(gap) for gap in (0.05, 0.10)}
    entries["watch65_then_momentum5c5m"] = entry_watch_band()
    exits = ("hold", "trail70", "trail60", "fair", "eoq3")
    grid: dict[str, Any] = {}
    all_trades: dict[str, dict[str, Trade | None]] = {}
    for en, fn in entries.items():
        for ex in exits:
            tr = {g.name: run_rule(g, fn, ex, fee) for g in games}
            key = f"{en}|{ex}"
            grid[key] = summarise(games, tr)
            all_trades[key] = tr
    for name, fn2 in (
        ("baseline_fav_hold", baseline_favourite),
        ("baseline_ride98_hold", baseline_ride98),
    ):
        tr = {g.name: fn2(g, fee) for g in games}
        grid[name] = summarise(games, tr)
        all_trades[name] = tr
    out["grid"] = grid
    vs_hold: dict[str, dict[str, Any]] = {}
    for en in entries:
        h = [all_trades[f"{en}|hold"][g.name] for g in games]
        for ex in exits[1:]:
            x = [all_trades[f"{en}|{ex}"][g.name] for g in games]
            diff = [
                (b.pnl if b else 0.0) - (a.pnl if a else 0.0) for a, b in zip(h, x, strict=True)
            ]
            vs_hold[f"{en}|{ex}"] = {
                "mean_diff_vs_hold": round(float(np.mean(diff)), 3),
                "ci95": boot_ci(diff),
            }
    out["vs_hold_same_entry"] = vs_hold
    out["n_exit_cells_beating_hold_mean"] = sum(
        float(v["mean_diff_vs_hold"]) > 0 for v in vs_hold.values()
    )
    out["n_rules_tried"] = len(grid)
    # hold-to-settle comparison: the same entries held
    out["best_cell_by_mean"] = max(grid, key=lambda k: grid[k]["mean_pnl_per_game_per_10"])
    # Pelicans
    nop = next(g for g in games if (g.away, g.home) == ("IND", "NOP"))
    out["pelicans"] = {
        "winner": nop.winner,
        "ceiling": ceilings[nop.name],
        "best_trip_NOP": best_round_trip(nop, fee, "NOP"),
        "best_trip_IND": best_round_trip(nop, fee, "IND"),
        "rules": {
            k: (
                None
                if tr[nop.name] is None
                else {
                    "team": tr[nop.name].team,  # type: ignore[union-attr]
                    "t_in": f"{tr[nop.name].t_in:%H:%M:%S}Z",  # type: ignore[union-attr]
                    "ask": tr[nop.name].ask,  # type: ignore[union-attr]
                    "t_out": None if tr[nop.name].t_out is None else f"{tr[nop.name].t_out:%H:%M:%S}Z",  # type: ignore[union-attr]
                    "bid_out": tr[nop.name].bid_out,  # type: ignore[union-attr]
                    "pnl": round(tr[nop.name].pnl, 3),  # type: ignore[union-attr]
                }
            )
            for k, tr in all_trades.items()
        },
        "timeline": [
            {
                "t": f"{tk.t:%H:%M:%S}Z", "score_away_home": tk.score,
                "period": tk.period, "clock": tk.clock,
                "ours_nop": None if tk.p_home is None else round(tk.p_home, 3),
                "espn_nop": tk.espn_home, "nop_bid": tk.bid["NOP"], "nop_ask": tk.ask["NOP"],
                "ind_bid": tk.bid["IND"], "ind_ask": tk.ask["IND"],
            }
            for tk in nop.ticks
        ],
    }  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "swing_explore_2026-10-21.json").write_text(json.dumps(out, indent=1, default=str))
    (OUT / "swing_explore_2026-10-21.md").write_text(render(out, games))
    print((OUT / "swing_explore_2026-10-21.md").read_text())
    return 0


def render(o: dict[str, Any], games: list[Game]) -> str:
    L = ["# Swing replay, 2026-10-21 moneylines (EXPLORATORY, hindsight, not a claim)", ""]
    L += [
        "Prior stated BEFORE computing the grid: P(any rule beats hold-to-settle out of sample) = 0.15.",
        "",
        "## Caveats (all apply)",
        "- One night, 11 games (n=11 clusters); ticks about every 2.3 min; no order books on 10-21, so fills "
        "are the listing bid/ask and real depth is unknown; buying at the ask and selling at the bid, taker fee "
        "(configs/parlay.yaml) on entry AND exit; never trade at ask 1.00 or bid 0.",
        "- The ESPN capture began at 01:00Z, so the first part of the early games has prices but no state: "
        "those ticks count for price-only rules and the ceilings, not for rules needing our prob.",
        f"- {o['n_rules_tried']} rules tried (7 entries x 5 exits + 2 baselines): the best cell "
        "is biased upward because it was picked after the fact.",
        "- The capture lag means a real person would be slower. 10-21 is now the exploration night and may "
        "not be used to score a frozen swing rule. Ceilings are UNATTAINABLE (hindsight).",
        "- Spreads and player props not done (time); only KXNBAGAME moneylines, both sides.",
        "",
        "## 1. Hindsight ceilings per game, $ per $10 stake (UNATTAINABLE)",
        "",
        "| game | winner | best single round trip | best path (DP, spread+fees) | DP spread only | DP frictionless mid |",
        "|---|---|---|---|---|---|",
    ]
    for g in games:
        c = o["ceilings"][g.name]
        b = c["best_round_trip"]
        desc = f"{b['pnl']:.2f}" + (
            f" ({b['team']} {b['t_in']} @{b['ask_in']:.2f} -> {b['t_out']} @{b['px_out']:.2f})"
            if "team" in b
            else ""
        )
        L.append(
            f"| {g.name} | {g.winner} | {desc} | {c['dp_full']['pnl']:.2f} | "
            f"{c['dp_spread_only']['pnl']:.2f} | {c['dp_frictionless_mid']['pnl']:.2f} |"
        )
    t = o["ceiling_totals"]
    L += [
        f"| **total (11 games, $110 staked)** | | **{t['best_round_trip']:.2f}** | **{t['dp_full']:.2f}** | "
        f"{t['dp_spread_only']:.2f} | {t['dp_frictionless_mid']:.2f} |",
        "",
        f"What spread and fees ate (DP paths): frictionless {t['dp_frictionless_mid']:.2f} -> spread only "
        f"{t['dp_spread_only']:.2f} (spread ate {t['dp_frictionless_mid'] - t['dp_spread_only']:.2f}) -> with fees "
        f"{t['dp_full']:.2f} (fees ate {t['dp_spread_only'] - t['dp_full']:.2f}). DP = non-overlapping round trips, each a fresh $10 (profits not reinvested, depth ignored), fractional contracts, "
        "unrounded fees; the frictionless line is a bound, not a price anyone can get.",
        "",
        "Per-trip decomposition (single best trips): gross at mid, spread cost, fees:",
    ]
    for g in games:
        b = o["ceilings"][g.name]["best_round_trip"]
        if "team" in b:
            L.append(
                f"- {g.name}: gross {b['gross_at_mid']:.2f}, spread {b['spread_cost']:.2f}, fees {b['fees']:.2f}, net {b['pnl']:.2f}"
            )
    L += [
        "",
        "## 2. Rule grid, mean P&L per game per $10 (games with no signal count 0)",
        "",
        "| rule | mean $/game | 95% CI (game bootstrap, n=11) | total $ | trades | hit rate |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in sorted(o["grid"].items(), key=lambda kv: -kv[1]["mean_pnl_per_game_per_10"]):
        L.append(
            f"| {k} | {v['mean_pnl_per_game_per_10']:.2f} | {v['ci95_game_bootstrap']} | {v['total_pnl']:.2f} | "
            f"{v['trades']} | {v['hit_rate']} |"
        )
    L += [
        "",
        "### Same entry, exit rule minus hold-to-settle (paired by game, $ per $10)",
        "",
        f"{o['n_exit_cells_beating_hold_mean']} of {len(o['vs_hold_same_entry'])} exit cells beat hold on the mean.",
        "",
        "| cell | mean diff | 95% CI |",
        "|---|---|---|",
    ]
    for k, v in sorted(o["vs_hold_same_entry"].items(), key=lambda kv: -kv[1]["mean_diff_vs_hold"]):
        L.append(f"| {k} | {v['mean_diff_vs_hold']:.2f} | {v['ci95']} |")
    L += [
        "",
        f"Best cell by mean (picked after the fact, biased upward): {o['best_cell_by_mean']}.",
        "",
    ]
    p = o["pelicans"]
    L += [
        "## 3. Pelicans game (IND @ NOP), winner " + p["winner"],
        "",
        f"Ceiling (UNATTAINABLE): best single trip {p['ceiling']['best_round_trip']}; DP full {p['ceiling']['dp_full']}.",
        f"Best NOP-only trip: {p['best_trip_NOP']}.",
        f"Best IND-only trip: {p['best_trip_IND']}.",
        "",
        "Annotation (from the price path below): NOP was an underdog early (0.33-0.35 around 00:36-00:43Z), rallied to "
        "0.69-0.70 by 01:13Z (halftime), swung 0.56-0.69 through Q3, hit 0.76 at 02:02Z with a 9-point lead, then "
        "collapsed to 0.54 at 02:14Z and lost. The hindsight best trades are buying NOP at the 00:36-00:43Z low and "
        "selling near 0.70 at halftime (or at 0.76 early Q4: the best NOP-only trip, +11), and buying IND at 0.24 at 02:02Z and holding to its win. Both needed prior "
        "knowledge of which swing was the top; the rules below did not have it. The momentum entries bought NOP into "
        "the early rise (or IND into the 00:20Z drop of NOP) and the state-based entries (reversion, watch band) bought "
        "NOP at 0.60-0.70 because our score-only model had it at 0.80+; the market's 0.70 was closer. Holding any NOP "
        "entry to settlement lost the full stake; end-of-Q3 and trailing exits cut some of that but also sold IND early.",
        "(Ticks between 01:52Z and 02:00Z and after 02:14Z have no state: the capture had gaps.)",
        "",
        "What each rule did (team, in, ask, out, bid_out, pnl; t_out None = held to settlement):",
        "",
    ]
    for k, r in p["rules"].items():
        L.append(f"- {k}: {r if r else 'no signal'}")
    L += [
        "",
        "Price path (ours/ESPN are P(NOP win)):",
        "",
        "| time | score IND-NOP | per | clock | ours | ESPN | NOP bid/ask | IND bid/ask |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in p["timeline"]:
        L.append(
            f"| {r['t']} | {r['score_away_home']} | {r['period']} | {r['clock']} | {r['ours_nop']} | {r['espn_nop']} | "
            f"{r['nop_bid']}/{r['nop_ask']} | {r['ind_bid']}/{r['ind_ask']} |"
        )
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
