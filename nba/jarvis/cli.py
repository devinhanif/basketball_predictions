"""``python -m nba.jarvis {slate,watch,ask,book,test-send}``. Paper-only, alert-only, read-only.

No command places an order or holds a credential. The one secret JARVIS reads is the iMessage
recipient, from the environment variable named in configs/jarvis.yaml (``JARVIS_IMESSAGE_TO``).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb

from nba.jarvis.agent import Jarvis
from nba.jarvis.book import (
    PaperBook,
    clear_kill_switch,
    kill_switch,
    render_weekly,
    score_book,
    weekly_summary,
)
from nba.jarvis.config import DEFAULT_PATH, PARLAY_PATH, ROOT, JarvisConfig, load_config
from nba.jarvis.forecasts import ModelView
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.picks import PickError, build_pick, log_pick
from nba.jarvis.runner import (
    Runtime,
    build_state,
    minutes_to_first_tip,
    run_recap,
    run_slate,
    run_tick,
)
from nba.jarvis.sinks import make_sender
from nba.jarvis.sources import et_today
from nba.jarvis.weights import load_weights, render_changes, update_weights
from nba.parlay.assistant.backend import LLMBackend, OllamaBackend, OllamaUnavailable
from nba.parlay.assistant.config import load_assistant_config
from nba.parlay.assistant.names import NameResolver


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=str(DEFAULT_PATH))
    p.add_argument(
        "--root", help="repo root for relative paths in the config (default: this checkout)"
    )
    p.add_argument("--date", help="slate date YYYY-MM-DD (default: today, US/Eastern)")
    p.add_argument("--no-llm", action="store_true", help="template explanations only (no Ollama)")
    p.add_argument("--no-model", action="store_true", help="skip our stored forecasts")
    p.add_argument(
        "--no-polymarket", action="store_true", help="do not call the Polymarket US gateway"
    )
    p.add_argument("--model", help="Ollama model (default qwen2.5:3b via configs/parlay.yaml)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nba.jarvis", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("slate", help="compose (and send) the pre-tip slate summary")
    _common(s)
    s.add_argument(
        "--dry-run", action="store_true", help="print instead of sending; writes nothing"
    )
    s.add_argument("--force", action="store_true", help="send even if one already went out today")

    w = sub.add_parser(
        "watch", help="poll: slate summary at T-90, immediate texts for new strong/arb"
    )
    _common(w)
    w.add_argument(
        "--dry-run", action="store_true", help="print instead of sending; writes nothing"
    )
    w.add_argument("--once", action="store_true", help="one pass then exit (launchd mode)")
    w.add_argument("--interval", type=float, default=120.0, help="seconds between passes")

    a = sub.add_parser("ask", help="ask JARVIS (always answers with a play)")
    _common(a)
    a.add_argument("question", nargs="?", help="one-shot question (otherwise a REPL)")
    a.add_argument("--log", action="store_true", help="record the answered play in the paper book")

    b = sub.add_parser("book", help="paper book: score, weekly, show")
    b.add_argument("action", choices=["score", "weekly", "show"])
    b.add_argument("--config", default=str(DEFAULT_PATH))
    b.add_argument("--root", help="repo root for relative paths in the config")
    b.add_argument("--date", help="end date for weekly/show (default today ET)")
    b.add_argument("--days", type=int, default=7)

    k = sub.add_parser(
        "pick", help="log your own read exactly as called (tracked apart from JARVIS)"
    )
    _common(k)
    k.add_argument("text", help='the read, e.g. "DET ML" (or use --ticker for another market)')
    k.add_argument("--venue", required=True, choices=["kalshi", "polymarket"])
    k.add_argument("--price", type=float, required=True, help="the price you called, e.g. 0.51")
    k.add_argument("--stake", type=float, required=True, help="dollars, as called")
    k.add_argument("--ticker", help="exact market ticker (needed for anything but 'TEAM ML')")
    k.add_argument("--side", choices=["yes", "no"], default="yes")
    k.add_argument("--dry-run", action="store_true", help="show the comparison, log nothing")

    r = sub.add_parser("recap", help="compose (and post) the morning recap now")
    _common(r)
    r.add_argument(
        "--dry-run", action="store_true", help="print instead of posting; writes nothing"
    )

    ks = sub.add_parser(
        "killswitch", help="show the CLV kill switch, or clear it (logs a dated row)"
    )
    ks.add_argument("--config", default=str(DEFAULT_PATH))
    ks.add_argument("--root", help="repo root for relative paths in the config")
    ks.add_argument(
        "--clear", action="store_true", help="Devin's reset: logs a dated row, restarts the count"
    )
    ks.add_argument("--note", default="", help="why it is cleared (kept in the log)")

    wt = sub.add_parser("weights", help="model weight by stat (show / run the frozen weekly rule)")
    wt.add_argument("action", choices=["show", "update"])
    wt.add_argument("--config", default=str(DEFAULT_PATH))
    wt.add_argument("--root", help="repo root for relative paths in the config")
    wt.add_argument(
        "--force", action="store_true", help="only DISPLAY the evaluation (never steps a weight)"
    )

    t = sub.add_parser("test-send", help="post one short test message (or --dry-run)")
    t.add_argument("--config", default=str(DEFAULT_PATH))
    t.add_argument("--dry-run", action="store_true")
    return ap


def _make_backend(cfg: JarvisConfig, a: argparse.Namespace) -> LLMBackend | None:
    if getattr(a, "no_llm", False) or not cfg.llm.get("enabled", True):
        return None
    ac = load_assistant_config(PARLAY_PATH, model=getattr(a, "model", None))
    ob = OllamaBackend(
        ac.model, ac.host, seed=ac.seed, timeout_s=float(cfg.llm.get("timeout_s", 90))
    )
    try:
        ob.ping()
    except OllamaUnavailable as exc:
        print(
            f"note: LLM off, template explanations only ({str(exc).splitlines()[0]})",
            file=sys.stderr,
        )
        return None
    return ob


def _runtime(a: argparse.Namespace, *, dry_run: bool) -> tuple[Runtime, date]:
    cfg = load_config(a.config, root=Path(a.root) if a.root else ROOT)
    slate = date.fromisoformat(a.date) if a.date else et_today()
    resolver = NameResolver.default()
    rt = Runtime(
        cfg=cfg,
        book=PaperBook(cfg.paths["paper_book"]),
        kb=Knowledge(cfg, display=resolver.display),
        backend=_make_backend(cfg, a),
        dry_run=dry_run,
        model_factory=None if a.no_model else (lambda d: ModelView(cfg, d)),
        use_polymarket=not a.no_polymarket,
    )
    return rt, slate


def cmd_slate(a: argparse.Namespace) -> int:
    rt, slate = _runtime(a, dry_run=a.dry_run)
    rep = run_slate(rt, slate, force=a.force)
    if rep.get("skipped"):
        print(rep["skipped"])
    if not a.dry_run:
        print(f"send: {rep.get('send')}")
    for n in rep.get("notes", []):
        print(f"note: {n}", file=sys.stderr)
    return 0 if rep.get("send", {}).get("ok", True) else 1


def cmd_watch(a: argparse.Namespace) -> int:
    rt, slate = _runtime(a, dry_run=a.dry_run)
    while True:
        rep = run_tick(rt, slate)
        print(
            f"tick {rep.get('at')}: plays={rep.get('plays')} alerts={len(rep.get('alerts', []))} "
            f"slate_send={'yes' if rep.get('slate_send') else 'no'} {rep.get('idle', '')}".strip()
        )
        if a.once:
            return 0
        from nba.jarvis.runner import build_state

        try:
            left = minutes_to_first_tip(build_state(rt, slate), rt.clock())
        except duckdb.IOException:  # a store held by its writer: try again next pass
            left = 0.0
        if left is None:  # no game left to tip today
            return 0
        time.sleep(a.interval)


def cmd_ask(a: argparse.Namespace) -> int:
    rt, _slate = _runtime(a, dry_run=False)
    bot = Jarvis(rt, rt.backend)
    if a.date:
        bot.tb.reset(date.fromisoformat(a.date))
    if a.question:
        print(bot.ask(a.question, log=a.log).text)
        return 0
    print("JARVIS (paper, read-only). Ask anything about tonight; 'quit' to exit.")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q.lower() in {"quit", "exit", ":q"}:
            return 0
        if q:
            bot.tb.reset(bot.tb.slate)
            print(bot.ask(q, log=a.log).text)


def cmd_book(a: argparse.Namespace) -> int:
    cfg = load_config(a.config, root=Path(a.root) if a.root else ROOT)
    book = PaperBook(cfg.paths["paper_book"])
    until = date.fromisoformat(a.date) if a.date else et_today()
    if a.action == "score":
        print(f"scored: {score_book(cfg, book)}")
        return 0
    if a.action == "weekly":
        print(render_weekly(weekly_summary(cfg, book, until=until, days=a.days)))
        return 0
    from nba.jarvis.render import scored_line

    con = book._con()
    try:
        cur = con.execute(
            "SELECT p.slate_date, p.matchup, p.venue, p.side, p.leg_desc, p.price, p.edge_label, "
            "p.edge_cents, s.won, s.pnl_usd FROM plays p JOIN scores s USING (play_id) "
            "WHERE p.slate_date BETWEEN ? AND ? ORDER BY p.ts",
            [until - timedelta(days=a.days - 1), until],
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            print(scored_line(dict(zip(cols, r, strict=True))))
    finally:
        con.close()
    return 0


def cmd_test_send(a: argparse.Namespace) -> int:
    cfg = load_config(a.config)
    res = make_sender(cfg)(
        "JARVIS test: paper-only, alert-only. No action needed.", dry_run=a.dry_run
    )
    print(f"test-send: {res.mode} {res.note}".strip())
    return 0 if res.ok else 1


def cmd_pick(a: argparse.Namespace) -> int:
    rt, slate = _runtime(a, dry_run=a.dry_run)
    st = build_state(rt, slate)
    try:
        res = build_pick(
            rt.cfg, st, a.text, venue=a.venue, price=a.price, stake=a.stake, ticker=a.ticker,
            side=a.side, now=rt.clock(),
        )  # fmt: skip
    except PickError as exc:
        print(f"pick not logged: {exc}", file=sys.stderr)
        return 2
    for ln in res.lines:
        print(ln)
    if a.dry_run:
        print("(dry-run: nothing logged)")
    else:
        log_pick(rt.book, res, slate, rt.clock())
        print("Logged under Devin's picks (tracked apart from JARVIS's plays).")
    return 0


def cmd_recap(a: argparse.Namespace) -> int:
    rt, slate = _runtime(a, dry_run=a.dry_run)
    rep = run_recap(rt, slate)
    print(rep if rep is not None else "nothing to recap: no plays were recorded for yesterday")
    return 0


def cmd_killswitch(a: argparse.Namespace) -> int:
    cfg = load_config(a.config, root=Path(a.root) if a.root else ROOT)
    book = PaperBook(cfg.paths["paper_book"])
    if a.clear:
        clear_kill_switch(cfg, book, et_today(), a.note)
        print(f"kill switch cleared on {et_today()} (dated row logged); the count restarts now")
        return 0
    ks = kill_switch(cfg, book)
    print(ks.message() if ks.active else f"kill switch off (n {ks.n} of {cfg.kill_min_bets} bets)")
    return 0


def cmd_weights(a: argparse.Namespace) -> int:
    cfg = load_config(a.config, root=Path(a.root) if a.root else ROOT)
    if a.action == "show":
        for k, v in load_weights(cfg).items():
            print(f"{k:>7}: w = {v:.1f}")
        return 0
    recs = update_weights(
        cfg, PaperBook(cfg.paths["paper_book"]), today=et_today(), display_only=a.force
    )
    if a.force:
        print(
            "(display only, nothing logged) "
            + "; ".join(f"{r['stat']}: {r['action']} n={r['n']}" for r in recs)
        )
        return 0
    print("\n".join(render_changes(recs)) if recs else "not due (at most one update per 7 days)")
    return 0


def main(argv: list[str] | None = None) -> int:
    for noisy in ("httpx", "httpcore"):  # their INFO lines carry full URLs (webhook token)
        logging.getLogger(noisy).setLevel(logging.WARNING)
    a = build_parser().parse_args(argv)
    handlers = {
        "slate": cmd_slate,
        "watch": cmd_watch,
        "ask": cmd_ask,
        "book": cmd_book,
        "test-send": cmd_test_send,
        "pick": cmd_pick,
        "recap": cmd_recap,
        "weights": cmd_weights,
        "killswitch": cmd_killswitch,
    }
    return handlers[a.cmd](a)


__all__ = ["main"]
