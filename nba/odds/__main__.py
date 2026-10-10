"""CLI: ``python -m nba.odds {capture,status} ...`` (read-only; never raises out of main).

Exit codes: 0 ok (including an empty / out-of-season feed), 4 informational (feed unreachable,
key missing, budget refused, or the store was busy), 5 an unexpected internal error. The daily job
must treat 4 and 5 as non-fatal.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from nba.odds.capture import run_capture, summary_lines
from nba.odds.theoddsapi import (
    OddsError,
    OddsKeyMissingError,
    TheOddsApiClient,
    read_envelope,
)
from nba.ops.exitcodes import RC_DEGRADED, RC_INFORMATIONAL

log = logging.getLogger("nba.odds")


def _status() -> int:
    cli = TheOddsApiClient()
    resp = cli.me()
    env = read_envelope(resp.body)
    d = env.data if isinstance(env.data, dict) else {}
    print(
        f"theoddsapi account: tier={d.get('tier')} requests_today={d.get('requests_today')} "
        f"daily_limit={d.get('daily_limit')} remaining={d.get('remaining')}"
    )
    now = cli.now()
    print(
        f"our ledger (UTC day): used={cli.ledger.used(now)} ceiling={cli.config.daily_budget} "
        f"server_remaining={cli.ledger.server_remaining(now)}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.odds", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="one pre-tip capture of the day's slate")
    c.add_argument("--date", type=date.fromisoformat, required=True, help="ET slate date")
    c.add_argument("--dry-run", action="store_true", help="call + cache raw + parse; no DB write")
    c.add_argument("--out", default=None, help="DuckDB file (default: configs/odds.yaml out_db)")
    sub.add_parser("status", help="print /me/ account usage")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:  # argparse usage error (2) is the caller's bug, not ours to raise
        return int(exc.code) if isinstance(exc.code, int) else 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines echo full request URLs
    try:
        if a.cmd == "status":
            return _status()
        rep = run_capture(a.date, dry_run=a.dry_run, out_db=Path(a.out) if a.out else None)
        for line in summary_lines(rep):
            print(line)
        if rep.unreachable or rep.store_failed:
            print("odds capture: degraded (feed unreachable or store busy); exit 4", flush=True)
            return RC_INFORMATIONAL
        if rep.empty:
            print("odds capture: feed empty (out of season or no events); nothing to store")
        return 0
    except OddsKeyMissingError as exc:
        print(f"odds: {exc}", file=sys.stderr)
        return RC_INFORMATIONAL
    except OddsError as exc:
        print(f"odds: {type(exc).__name__}: {exc}", file=sys.stderr)
        return RC_INFORMATIONAL
    except Exception:  # noqa: BLE001 - the daily job must never see a traceback from here
        log.exception("odds: unexpected internal error")
        return RC_DEGRADED


if __name__ == "__main__":
    sys.exit(main())
