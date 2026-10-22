"""python -m nba.ride {backtest|watch|replay}"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from nba.ride import gate, sizing, watch
from nba.ride.capture_view import CaptureView
from nba.ride.features import FeatureLog
from nba.ride.notify import ENV_VAR, Notifier
from nba.ride.winprob import WinProbModel

RAW = Path("data/raw")
DERIVED = Path("data/derived")
REPORTS = Path("reports/ride")


def _pct(x: float | None) -> str:
    return "  na " if x is None else f"{x:5.1%}"


def _px(x: float | None) -> str:
    return " na " if x is None else f"{x:.2f}"


def print_timeline(eng: watch.Engine, event_id: str, title: str) -> list[str]:
    rows = eng.timeline.get(event_id, [])
    out = [
        title,
        "time(UTC) file(UTC)  qtr clock  away-home  ours_home espn_home  home_bid home_ask",
    ]
    last_file = None
    for r in rows:
        if r["file"] == last_file:
            continue
        last_file = r["file"]
        m, s = divmod(int(r["clock"]), 60)
        out.append(
            f"{r['ts']:%H:%M}  {r['file']:%H:%M:%S}   Q{r['period']}  {m:2d}:{s:02d}  "
            f"{r['score']:>8}  {_pct(r['ours_home'])}    {_pct(r['espn_home'])}     "
            f"{_px(r['home_bid'])}     {_px(r['home_ask'])}"
        )
    return out


def cmd_replay(a: argparse.Namespace) -> int:
    root = RAW / f"live_{a.date}"
    log = REPORTS / f"replay_{a.date}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    model = WinProbModel.load()
    events, eng = watch.run_replay(root, a.date, model, gate.RideConfig(), sizing.load_fee(), log)
    lines = [f"REPLAY {a.date} (no push; freshness limits 300 s: that capture ran ~2.3 min apart)"]
    n_ride = sum(1 for e in events if e.kind == "RIDE")
    n_plan = sum(1 for e in events if e.kind == "PLAN")
    n_watch = sum(1 for e in events if e.kind == "WATCH")
    lines.append(f"RIDE alerts that would have fired: {n_ride}; PLAN lines: {n_plan}")
    holds: dict[str, int] = {}
    for e in events:
        if e.kind == "HOLD":
            for r in e.text.split(", "):
                holds[r] = holds.get(r, 0) + 1
        elif e.kind in ("RIDE", "PLAN"):
            lines += [f"--- {e.ts:%H:%M}Z {e.kind} {e.game}", e.text]
    lines.append(
        f"WATCH entries: {n_watch}; ours >= 98% but NOT alerted, by reason (minute-ticks): {holds}"
    )
    view = CaptureView(root)
    names = {
        eid: s
        for eid in view.event_ids()
        if (s := view.snap(eid, datetime(2100, 1, 1, tzinfo=UTC))) is not None
    }
    for eid, s in names.items():
        if (s.away, s.home) == ("IND", "NOP"):
            lines += [""] + print_timeline(
                eng,
                eid,
                "PELICANS (IND @ NOP) timeline: ours/ESPN are P(NOP win); price = NOP YES market",
            )
    text = "\n".join(lines)
    (REPORTS / f"replay_{a.date}.txt").write_text(text + "\n")
    print(text)
    return 0


def cmd_notify_test() -> int:
    """Send one harmless test line through the real push path (iMessage, else notification)."""
    log = REPORTS / "notify_test.log"
    chan = Notifier(log).alert("TEST", "ride detector test: nothing to do")
    print(f"push channel used: {chan} (log {log})")
    return 0 if chan != "none" else 1


def cmd_watch(a: argparse.Namespace) -> int:
    root = Path(a.root) if a.root else RAW / f"live_{a.date}"
    log = Path(a.reports_dir) / f"{a.date}.log"
    notifier = Notifier(log, push=not a.no_push)
    derived = Path(a.derived_dir)
    feat_dir = derived / "ride_features" / a.date
    view = CaptureView(root)
    eng = watch.Engine(
        root,
        a.date,
        WinProbModel.load(),
        gate.RideConfig(credit=a.credit, heads_up_p=a.heads_up),
        sizing.load_fee(),
        notifier,
        state_path=derived / "ride" / f"state_{a.date}.json",
        features=None if a.no_features else FeatureLog(view, feat_dir, a.date),
    )
    chan = "iMessage" if os.environ.get(ENV_VAR) else "macOS notification (NBA_IMESSAGE_TO unset)"
    print(f"watching {root}; push via {chan}; log {log}; Ctrl-C to stop. Read-only: no orders.")
    return watch.run_live(root, a.date, eng, a.interval, max_steps=a.max_steps)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ride")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backtest")
    w = sub.add_parser("watch")
    w.add_argument("--date", required=True)
    w.add_argument("--root", default="")
    w.add_argument("--interval", type=float, default=60.0)
    w.add_argument("--credit", type=float, default=10.84)
    w.add_argument("--no-push", action="store_true")
    w.add_argument(
        "--heads-up", type=float, default=None, help="push once per game at this leader win prob"
    )
    w.add_argument("--no-features", action="store_true")
    w.add_argument("--derived-dir", default=str(DERIVED))
    w.add_argument("--reports-dir", default=str(REPORTS))
    w.add_argument("--max-steps", type=int, default=None)
    sub.add_parser("notify-test")
    r = sub.add_parser("replay")
    r.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "backtest":
        from nba.ride import backtest

        print(json.dumps(backtest.run(), indent=1))
        return 0
    if a.cmd == "watch":
        return cmd_watch(a)
    if a.cmd == "notify-test":
        return cmd_notify_test()
    return cmd_replay(a)
