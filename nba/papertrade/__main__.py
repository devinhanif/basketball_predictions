"""``python -m nba.papertrade {scan,monitor,settle,summary} --date D`` (public data, paper only)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nba.db.connect import connect_with_retry
from nba.kalshi.aliases import load_reviewed_aliases, propose_alias_candidates
from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher
from nba.live.store import RawStore
from nba.papertrade import config as C
from nba.papertrade import db, digest
from nba.papertrade.forecasts import load_forecasts
from nba.papertrade.monitor import monitor_once, open_positions
from nba.papertrade.runner import DayRunner
from nba.papertrade.scan import fetch_markets, run_scan
from nba.papertrade.settle import dump, settle_positions, summarize

ET = ZoneInfo("America/New_York")
ROOT = C.ROOT


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _write_candidates(day: date, names: list[str]) -> Path | None:
    if not names:
        return None
    path = ROOT / "data" / "papertrade" / f"alias_candidates_{day.isoformat()}.yaml"
    try:
        cands = propose_alias_candidates(names)
    except Exception:  # static player list unavailable: still list the names
        cands = {n: [] for n in names}
    lines = [
        f"# CANDIDATES for maintainer review, {day} (never applied automatically)",
        "candidates:",
    ]
    lines += [f'  "{n}": {json.dumps(ids)}' for n, ids in cands.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def _tips(nba_db: Path, day: date) -> list[datetime]:
    from nba.papertrade.forecasts import slate_bounds_utc

    lo, hi = slate_bounds_utc(day)
    con = connect_with_retry(nba_db, read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT tipoff FROM forward_predictions "
            "WHERE tipoff >= ? AND tipoff < ? AND tipoff IS NOT NULL",
            [lo, hi],
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.papertrade", description=__doc__)
    ap.add_argument("--db", type=Path, default=C.DB_PATH)
    ap.add_argument("--nba-db", type=Path, default=ROOT / "nba.duckdb")
    ap.add_argument("--config", type=Path, default=C.CONFIG_PATH)
    ap.add_argument(
        "--rule-version", default=None,
        help="override the config rule_version, e.g. paper-trader-v2-shakedown (never scored)",
    )  # fmt: skip
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "monitor", "settle", "summary", "run", "digest", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--date", type=date.fromisoformat, default=None, help="ET slate date")
        if name == "digest":
            p.add_argument("--write", action="store_true", help="write the freeze record")
        if name == "replay":
            p.add_argument(
                "--raw", type=Path, default=None, help="capture dir (default live_<date>)"
            )
            p.add_argument("--max-age-h", type=float, default=None, help="what-if age cap")
            p.add_argument(
                "--extra-aliases", type=Path, default=None, help="candidates yaml: unique_proposals"
            )
        if name == "monitor":
            p.add_argument("--interval-s", type=float, default=60.0)
            p.add_argument("--max-hours", type=float, default=9.0)
            p.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    if a.rule_version:
        cfg = cfg.with_rule_version(a.rule_version)
    day: date = a.date or datetime.now(ET).date()
    fetcher = Fetcher()

    def do_scan(now: datetime) -> int:
        raw = RawStore(ROOT / "data" / "papertrade" / "raw" / day.isoformat())
        fetcher.begin_tick(200, 90.0)
        markets = fetch_markets(fetcher, day, [*cfg.prop_series, "KXNBAGAME"], raw)
        con = connect_with_retry(a.nba_db, read_only=True)
        try:
            fc = load_forecasts(con, day, now)
        finally:
            con.close()

        def book(ticker: str) -> dict[str, Any] | None:
            return kp.parse_body(kp.get_orderbook(fetcher, ticker))

        fetcher.begin_tick(60, 30.0)
        status = digest.check() if cfg.scored else (True, "shakedown: digest not enforced")
        res = run_scan(
            day, now, cfg, markets=markets, fc=fc, aliases=load_reviewed_aliases(),
            db_path=a.db, legs_file=C.LEGS_FILE, book_fn=book, digest_status=status,
            digests=digest.compute(),
        )  # fmt: skip
        cand = _write_candidates(day, res.unmatched)
        print(
            f"scan {res.scan_id} {day}: verdict={res.verdict} markets={res.n_markets} "
            f"passing_legs={res.n_candidates} new_positions={len(res.positions)} "
            f"unmatched_names={len(res.unmatched)} refusals={len(res.refusals)}",
            flush=True,
        )
        print(f"notes: {json.dumps(res.notes)}", flush=True)
        if cand:
            print(f"alias candidates for review: {cand}", flush=True)
        return 0

    def getter(ticker: str) -> dict[str, Any] | None:
        fetcher.begin_tick(10, 20.0)
        return kp.get_market(fetcher, ticker)

    def do_monitor(now: datetime) -> dict[str, int]:
        c = monitor_once(day, now, cfg, getter, a.db, C.LEGS_FILE)
        print(f"{now:%H:%M:%S} monitor {c}", flush=True)
        return c

    def do_settle(now: datetime) -> None:
        print(f"settle {day}: {settle_positions(day, now, cfg, getter, a.db)}", flush=True)

    def do_replay() -> int:
        import tempfile

        import yaml

        from nba.papertrade.replay import replay_day

        rcfg = replace(cfg, max_forecast_age_h=a.max_age_h) if a.max_age_h else cfg
        raw = a.raw or ROOT / "data" / "raw" / f"live_{day.isoformat()}"
        aliases = load_reviewed_aliases()
        if a.extra_aliases:
            extra = yaml.safe_load(a.extra_aliases.read_text())["unique_proposals"]
            from nba.parse.availability import normalize_name

            aliases = {**aliases, **{normalize_name(k): int(v) for k, v in extra.items()}}

        def load_fc(now: datetime) -> Any:
            con = connect_with_retry(a.nba_db, read_only=True)
            try:
                return load_forecasts(con, day, now)
            finally:
                con.close()

        out = replay_day(
            day, raw, _tips(a.nba_db, day), rcfg, aliases, load_fc,
            Path(tempfile.mkdtemp()) / "replay.duckdb",
        )  # fmt: skip
        print(json.dumps(out, indent=1, default=str))
        return 0

    def do_summary() -> int:
        s = summarize(a.db, cfg)
        dump(s, ROOT / "data" / "papertrade" / f"summary_{day.isoformat()}.json")
        print(json.dumps(s, indent=2, sort_keys=True, default=str))
        return 0

    if a.cmd == "digest":
        if a.write:
            print(
                json.dumps(
                    digest.write_freeze(cfg.rule_version, cfg.freeze_date.isoformat()), indent=1
                )
            )
        else:
            print(json.dumps(digest.compute(), indent=1), digest.check())
        return 0
    if a.cmd == "replay":
        return do_replay()
    if a.cmd == "scan":
        return do_scan(_now())
    if a.cmd == "run":
        runner = DayRunner(day, _tips(a.nba_db, day), do_scan, do_monitor, do_settle)
        rc = runner.run()
        do_summary()
        return rc
    if a.cmd == "monitor":
        deadline = time.monotonic() + a.max_hours * 3600
        while True:
            t0 = time.monotonic()
            try:
                c = do_monitor(_now())
            except Exception as exc:  # the loop never dies on one bad tick
                print(f"monitor tick failed: {type(exc).__name__}: {exc}", flush=True)
                c = {"positions": 1}
            if a.once or time.monotonic() > deadline:
                return 0
            if c.get("positions", 1) == 0:
                with db.reader(a.db) as rcon:
                    if not open_positions(rcon, day):
                        print("no open positions left; monitor done", flush=True)
                        return 0
            time.sleep(max(1.0, a.interval_s - (time.monotonic() - t0)))
    if a.cmd == "settle":
        do_settle(_now())
    return do_summary()


if __name__ == "__main__":
    sys.exit(main())
