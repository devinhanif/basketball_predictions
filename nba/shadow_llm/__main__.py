"""CLI: ``python -m nba.shadow_llm run --date <ET date> [--dry-run] [--provider stub|anthropic]``.

Off unless ``NBA_LLM_SHADOW=1`` (environment): with the flag unset the command prints one line and
exits 0 without opening any database or file. Exit codes: 0 ok, 4 informational (key or monthly cap
not set, budget refused, schema reject, post-tip refusal), 5 degraded (provider error), 75 database
busy, 2 usage error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from nba.db.connect import DEFAULT_DB_PATH
from nba.shadow_llm.config import load_config
from nba.shadow_llm.provider import AnthropicProvider, Provider, StubProvider
from nba.shadow_llm.run import FLAG_ENV, RunSummary, default_paths, enabled, run
from nba.shadow_llm.runner import utcnow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.shadow_llm")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="one tick: forecast the games inside the T-60 window")
    r.add_argument("--date", required=True, type=date.fromisoformat, help="ET slate date")
    r.add_argument(
        "--dry-run", action="store_true", help="build payloads and call the provider; write no rows"
    )
    r.add_argument("--provider", choices=["stub", "anthropic"], default="anthropic")
    r.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    r.add_argument("--config", type=Path, default=None)
    r.add_argument("--window-min", type=int, nargs=2, metavar=("LO", "HI"), default=None,
                   help="minutes before tip; default from the config (60 70)")  # fmt: skip
    r.add_argument("--allow-stub-write", action="store_true",
                   help="let the stub write rows (tests and rehearsal copies only)")  # fmt: skip
    r.add_argument("--max-season", type=int, default=None,
                   help="cap seasons read for box lines/team context (estimates only)")  # fmt: skip
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not enabled():
        print(f"llm_shadow: disabled ({FLAG_ENV} is not 1); nothing to do")
        return 0
    cfg = load_config(args.config)
    if args.window_min:
        from dataclasses import replace

        cfg = replace(cfg, window_min=(args.window_min[0], args.window_min[1]))
    provider: Provider = StubProvider() if args.provider == "stub" else AnthropicProvider(cfg)
    s: RunSummary = run(
        args.date,
        provider=provider,
        cfg=cfg,
        paths=default_paths(cfg, args.db_path),
        dry_run=args.dry_run,
        allow_stub_write=args.allow_stub_write,
        max_season=args.max_season,
    )
    print(
        f"llm_shadow {args.date} provider={args.provider} dry_run={args.dry_run} "
        f"at {utcnow():%H:%M:%S}Z"
    )
    for n in s.notes:
        print(f"note: {n}")
    for r in s.results:
        print(
            f"  {r.game_id}: {r.status} rows={r.rows_written} tokens_in={r.tokens_in} "
            f"tokens_out={r.tokens_out} {r.detail}".rstrip()
        )
    return s.rc


if __name__ == "__main__":
    sys.exit(main())
