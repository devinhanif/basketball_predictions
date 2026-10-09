"""``python -m nba.parlay assistant``: one-shot ``--ask`` or an interactive REPL."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.parlay.assistant.agent import Assistant
from nba.parlay.assistant.backend import LLMBackend, OllamaBackend, OllamaUnavailable
from nba.parlay.assistant.config import load_assistant_config
from nba.parlay.assistant.names import NameResolver
from nba.parlay.assistant.toolbox import EnginePaths, Toolbox
from nba.parlay.config import load_config
from nba.parlay.joint import DNP_POLICIES

BANNER = (
    "Parlay assistant (read-only, local LLM). Ask about a slate, a leg or a parlay. "
    "Every number comes from the engine. 'quit' to exit."
)


def add_arguments(p: argparse.ArgumentParser, root: Path) -> None:
    p.add_argument("--config", default=str(root / "configs" / "parlay.yaml"))
    p.add_argument("--nba-db", default=str(root / "nba.duckdb"))
    p.add_argument("--kalshi-db", default=str(root / "data" / "kalshi" / "kalshi.duckdb"))
    p.add_argument("--paper-db", default=str(root / "data" / "parlay" / "paper_trades.duckdb"))
    p.add_argument("--joint-model", default=str(root / "data" / "parlay" / "joint_model.json"))
    p.add_argument("--ask", help="one-shot question (otherwise an interactive REPL)")
    p.add_argument("--date", help="slate date YYYY-MM-DD (default: today, US/Eastern)")
    p.add_argument("--model", help="Ollama model (env PARLAY_ASSISTANT_MODEL; default qwen2.5:3b)")
    p.add_argument("--transcript", help="append a JSONL audit log of tool calls and outputs here")
    p.add_argument("--dnp-policy", choices=DNP_POLICIES, default="void")
    p.add_argument(
        "--allow-paper-log",
        action="store_true",
        help="expose the log_paper_trade tool (paper table only; off by default)",
    )


def build_assistant(a: argparse.Namespace, backend: LLMBackend | None = None) -> Assistant:
    ac = load_assistant_config(a.config, model=a.model)
    cfg = load_config(a.config)
    when = (
        date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo("America/New_York")).date()
    )
    tb = Toolbox(
        EnginePaths(Path(a.nba_db), Path(a.kalshi_db), Path(a.paper_db), Path(a.joint_model)),
        cfg,
        NameResolver.default(),
        default_date=when,
        dnp_policy=a.dnp_policy,
        allow_paper_log=a.allow_paper_log,
        top_n=ac.top_n,
    )
    if backend is None:
        ob = OllamaBackend(ac.model, ac.host, seed=ac.seed, timeout_s=ac.timeout_s)
        ob.ping()
        backend = ob
    return Assistant(
        backend,
        tb,
        max_steps=ac.max_tool_steps,
        transcript=Path(a.transcript) if a.transcript else None,
    )


def run(a: argparse.Namespace, backend: LLMBackend | None = None) -> int:
    try:
        bot = build_assistant(a, backend)
        if a.ask:
            print(bot.ask(a.ask).text)
            return 0
        print(BANNER)
        while True:
            try:
                q = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if q.lower() in {"quit", "exit", ":q"}:
                return 0
            if q:
                print(bot.ask(q).text)
    except OllamaUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
