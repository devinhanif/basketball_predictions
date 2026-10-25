"""CLI: ``python -m nba.markets.fastarb {run,dry-run,underdog} ...`` (read-only; never trades).

``run``      the pre-tip poller (refuses unless the fee objects are verified); writes raw files,
             ALERTS.md lines and a heartbeat; stops at each game's tip.
``dry-run``  one tick over a slate, printing the gaps found; writes nothing (no raw, no alerts).
``underdog`` the manual-file Underdog vs Pinnacle comparison (Underdog is never polled).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import httpx

from nba.live.http import Fetcher
from nba.markets.fastarb import sinks, underdog
from nba.markets.fastarb.config import DEFAULT_PATH, UnverifiedFeeError, load_config
from nba.markets.fastarb.pinnacle import FastPinnacleClient
from nba.markets.fastarb.runner import ET, USER_AGENT, GameStat, Runner, TickReport

KEY_ENV = "THE_ODDS_API_KEY"


def _fetcher(timeout: float) -> Fetcher:
    client = httpx.Client(timeout=httpx.Timeout(timeout), headers={"User-Agent": USER_AGENT})
    return Fetcher(client=client, rate_per_s=10.0)


def _pinnacle_client() -> FastPinnacleClient | None:
    if not (os.environ.get(KEY_ENV) or "").strip():
        return None
    from nba.markets.fastarb.pinnacle import make_client
    from nba.odds.live_close import load_live_config

    return make_client(load_live_config(), os.environ)


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}c"


def format_report(rep: TickReport, intl: bool) -> list[str]:
    lines = [
        f"dry-run tick {rep.now:%Y-%m-%d %H:%M:%S}Z: {rep.requests} requests, "
        f"{len(rep.games)} games"
    ]
    for label, g in sorted(rep.games.items(), key=lambda kv: kv[1].tip or rep.now):
        lines.append(_game_line(label, g, intl))
    lines.append(
        f"alertable arbs {rep.arbs_alertable}, thin {rep.arbs_thin}, lag alerts {rep.lags}, "
        f"news events in window {rep.news}"
    )
    if rep.skipped:
        lines.append(
            "skipped/unmatched: " + ", ".join(f"{k}={v}" for k, v in sorted(rep.skipped.items()))
        )
    verdict = (
        "ARB FOUND"
        if rep.arbs_alertable
        else "no_positive_ev_found (no arbitrage >= min gap after fees)"
    )
    lines.append(f"verdict: {verdict}")
    return lines


def _game_line(label: str, g: GameStat, intl: bool) -> str:
    b = g.best
    best = "none comparable"
    if b is not None:
        best = (
            f"max net {_pct(b.margin)}/ct at {b.n_eval} ct ({b.prop.label()}: {b.yes_ask.short} "
            f"{b.yes_ask.price:.2f} + {b.no_ask.short} {b.no_ask.price:.2f}; "
            f"gross {_pct(b.gross_gap)})"
        )
    extra = ""
    if intl and g.best_intl is not None:
        extra = f" | INTL(not tradable) max net {_pct(g.best_intl.margin)}"
    tip = f"{g.tip:%H:%MZ}" if g.tip else "?"
    return (
        f"  {label} tip {tip}: {g.n_props_both_venues} props on both venues, "
        f"{g.lines_booked} books, "
        f"{best}{extra}; pinnacle={g.pinnacle}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.markets.fastarb", description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="the pre-tip poller (stops at each tip)")
    r.add_argument(
        "--date", type=date.fromisoformat, default=None, help="ET slate date (default today)"
    )
    d = sub.add_parser("dry-run", help="one tick, print gaps, write nothing")
    d.add_argument("--date", type=date.fromisoformat, default=None)
    d.add_argument("--no-intl", action="store_true", help="skip the international gamma comparison")
    d.add_argument(
        "--with-pinnacle", action="store_true", help="also call Pinnacle (3 credits per game)"
    )
    u = sub.add_parser("underdog", help="Underdog vs Pinnacle from a hand-made CSV")
    u.add_argument("--csv", type=Path, required=True)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "underdog":
        ud = cfg.section("underdog")
        res = underdog.evaluate(underdog.read_rows(a.csv), ud)
        flagged = [x for x in res if x.flagged and x.plus_ev_2pick]
        print(f"payouts ASSUMED (verified={ud['verified_payouts']}): {ud['payout_table']}")
        print(
            f"rows {len(res)}, line-diff flagged {sum(x.flagged for x in res)}, "
            f"+EV at 2-pick {len(flagged)}"
        )
        for x in flagged:
            print(x.text)
        if not flagged:
            print("verdict: no_positive_ev_found")
        return 0

    day = a.date or datetime.now(ET).date()
    poll_to = float(cfg.section("poll")["request_timeout_s"])
    if a.cmd == "dry-run":
        intl = not a.no_intl
        pc = _pinnacle_client() if a.with_pinnacle else None
        runner = Runner(cfg, _fetcher(poll_to), pinnacle_client=pc, use_intl=intl)
        if intl:
            print("INTL fee object unverified: dry-run only, shown for comparison", file=sys.stderr)
        for line in format_report(runner.tick(day, force_active=True), intl):
            print(line)
        return 0

    intl = bool(cfg.section("polymarket_intl")["enabled"])
    try:
        cfg.require_verified_fees(intl=intl)
    except UnverifiedFeeError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2
    alert = cfg.section("alert")
    runner = Runner(
        cfg,
        _fetcher(poll_to),
        raw=sinks.RawWriter(cfg.path("raw_root"), day.isoformat()),
        sink=sinks.AlertSink(
            cfg.path("alerts"), cfg.path("dedup_state"), float(alert["dedup_minutes"])
        ),
        pinnacle_client=_pinnacle_client() if cfg.section("pinnacle")["enabled"] else None,
        use_intl=intl,
    )
    lock_path = cfg.path("heartbeat").with_name("fastarb_lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:  # one poller at a time; the OS drops the lock if we die
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another fastarb poller holds the lock; exiting 0", file=sys.stderr)
            return 0
        return runner.run(day)


if __name__ == "__main__":
    raise SystemExit(main())
