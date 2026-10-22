"""The detector: one ``Engine.step(now)`` per minute over the newest capture files.

Live: ``python -m nba.ride watch --date 2026-10-22``. Replay: ``python -m nba.ride replay --date
2026-10-21`` (the same engine stepped through a finished capture, no push). Read-only throughout.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.parlay.config import FeeModel
from nba.ride import features as feat
from nba.ride import gate, plan, sizing
from nba.ride.capture_view import CaptureView, GameSnap, Quote
from nba.ride.notify import Notifier
from nba.ride.winprob import WinProbModel, leader_prob, seconds_left


@dataclass(frozen=True)
class Event:
    kind: str  # RIDE | PLAN | WATCH | HOLD
    game: str
    text: str
    ts: datetime


@dataclass
class Engine:
    root: Path
    date_key: str
    model: WinProbModel
    cfg: gate.RideConfig
    fee: FeeModel
    notifier: Notifier
    replay: bool = False
    state_path: Path | None = None
    features: feat.FeatureLog | None = None
    positions: dict[str, plan.Position] = field(default_factory=dict)  # event_id -> position
    slices: dict[str, float] = field(default_factory=dict)
    levels: dict[str, str] = field(default_factory=dict)  # event_id -> last logged level
    headsup_sent: set[str] = field(default_factory=set)  # event_ids that got the heads-up push
    reasons: dict[str, tuple[str, ...]] = field(default_factory=dict)
    timeline: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.view = CaptureView(self.root)
        if self.state_path is not None and self.state_path.exists():
            d = json.loads(self.state_path.read_text())
            self.positions = {
                k: plan.Position.from_dict(v) for k, v in d.get("positions", {}).items()
            }
            self.slices = {k: float(v) for k, v in d.get("slices", {}).items()}
            self.headsup_sent = set(d.get("headsup_sent", []))

    def save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "slices": self.slices,
            "headsup_sent": sorted(self.headsup_sent),
        }
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.state_path)

    # ------------------------------------------------------------------ quotes
    def _quote(self, snap: GameSnap, team: str, now: datetime) -> tuple[str, Quote] | None:
        found = self.view.game_market_ticker(snap, team, now)
        if found is None:
            return None
        ticker, market, mts = found
        return ticker, self.view.quote(ticker, market, mts, now, self.date_key)

    # ------------------------------------------------------------------ one minute
    def step(self, now: datetime) -> list[Event]:
        events: list[Event] = []
        hb = self.view.heartbeat_age_s(now, replay=self.replay)
        snaps = {
            eid: s for eid in self.view.event_ids() if (s := self.view.snap(eid, now)) is not None
        }
        rows: list[dict[str, Any]] = []
        for eid, s in sorted(snaps.items()):
            name = f"{s.away}@{s.home}"
            if s.state != "in":
                continue
            sl = seconds_left(s.period, s.clock_secs)
            p_home = self.model.p_home(s.margin, sl)
            side, ours = leader_prob(p_home)
            leader_team = s.home if side == "home" else s.away
            espn = (
                None
                if s.espn_home_wp is None
                else (s.espn_home_wp if side == "home" else 1.0 - s.espn_home_wp)
            )
            lq = self._quote(s, leader_team, now)
            hq = lq if side == "home" else self._quote(s, s.home, now)
            verdict = gate.evaluate(
                gate.GateInput(
                    "in",
                    side,
                    ours,
                    espn,
                    hb,
                    (now - s.file_time).total_seconds(),
                    quote_state(lq),
                    eid in self.positions,
                ),
                self.cfg,
            )
            self.notifier.log(
                "TICK",
                f"{name} Q{s.period} {s.clock_secs:.0f}s {s.away_score}-{s.home_score} "
                f"ours_{leader_team}={ours:.3f} espn={_f(espn)} hb={_f(hb, 0)}s "
                f"ask={_f(None if lq is None else lq[1].ask, 2)} {verdict.level}",
                now=now,
            )
            if (
                verdict.level == "WATCH"
                and self.levels.get(eid) != "WATCH"
                and eid not in self.positions
            ):
                text = (
                    f"{name} {leader_team} leads {abs(s.margin)}, ours {ours:.3f}, "
                    f"Q{s.period} {s.clock_secs:.0f}s"
                )
                self.notifier.log("WATCH", text, now=now)
                events.append(Event("WATCH", name, text, now))
            gin = gate.GateInput(
                "in",
                side,
                ours,
                espn,
                hb,
                (now - s.file_time).total_seconds(),
                quote_state(lq),
                eid in self.positions,
            )
            if gate.heads_up(gin, self.cfg, eid in self.headsup_sent):
                q = None if lq is None else lq[1]
                px = "" if q is None else f" Kalshi {leader_team} {_f(q.bid, 2)}/{_f(q.ask, 2)}."
                text = (
                    f"HEADS-UP {name}: {leader_team} leads {abs(s.margin)} "
                    f"({s.away_score}-{s.home_score}), Q{s.period} {s.clock_secs // 60:.0f}:"
                    f"{s.clock_secs % 60:02.0f}. Win prob ours {ours:.0%}, "
                    f"ESPN {(espn or 0.0):.0%}.{px} "
                    f"Info only; ride alert comes at {self.cfg.ride_p:.0%}."
                )
                self.notifier.alert("HEADS_UP", text, now=now)
                self.headsup_sent.add(eid)
                self.save()
                events.append(Event("HEADS_UP", name, text, now))
            if verdict.level in ("WATCH", "RIDE"):
                self.levels[eid] = verdict.level
            why = tuple(r for r in verdict.reasons if r != "already_alerted")
            if why and self.reasons.get(eid) != why and ours >= self.cfg.ride_p:
                detail = f"hb={hb} data_age={(now - s.file_time).total_seconds():.0f}s espn={espn}"
                self.notifier.log(
                    "NO_ALERT", f"{name} ours {ours:.3f}: {', '.join(why)} ({detail})", now=now
                )
                events.append(Event("HOLD", name, ", ".join(why), now))
            self.reasons[eid] = why
            if verdict.level == "RIDE" and lq is not None:
                n_able = sum(
                    1 for e, x in snaps.items() if x.state != "post" and e not in self.positions
                )
                events.append(
                    self._ride(eid, s, name, leader_team, ours, espn, lq, hb, n_able, now)
                )
            if self.features is not None:
                rows.append(
                    feat.game_row(
                        s,
                        now,
                        p_home,
                        None if hq is None else hq[1].ask,
                        None if hq is None else hq[1].bid,
                    )
                )
            self.timeline.setdefault(eid, []).append(
                {
                    "ts": now,
                    "file": s.file_time,
                    "period": s.period,
                    "clock": s.clock_secs,
                    "score": f"{s.away_score}-{s.home_score}",
                    "ours_home": p_home,
                    "espn_home": s.espn_home_wp,
                    "home_bid": None if hq is None else hq[1].bid,
                    "home_ask": None if hq is None else hq[1].ask,
                }
            )
        events += self._plans(snaps, now)
        if self.features is not None:
            self.features.ingest_trades(now)
            self.features.flush_candles(now)
            self.features.write_games(rows, now)
        self.save()
        return events

    # ------------------------------------------------------------------ RIDE alert
    def _ride(
        self,
        eid: str,
        s: GameSnap,
        name: str,
        team: str,
        ours: float,
        espn: float | None,
        q: tuple[str, Quote],
        hb: float | None,
        n_able: int,
        now: datetime,
    ) -> Event:
        ticker, quote = q
        assert quote.ask is not None
        committed = sum(self.slices.values())
        sl = sizing.credit_slice(self.cfg.credit, committed, n_able)
        sized = sizing.size_order(sl.slice_usd, quote.ask, ours, self.fee)
        self.slices[eid] = sl.slice_usd
        self.positions[eid] = plan.Position(
            name,
            ticker,
            team,
            quote.ask,
            sized.contracts,
            sized.fee,
            now.timestamp(),
            0.0,
            False,
            [(now.timestamp(), quote.bid)] if quote.bid is not None else [],
        )
        text = format_ride(s, name, team, ours, espn, ticker, quote, sl, sized, self.cfg)
        self.notifier.alert("RIDE", text, now=now)
        return Event("RIDE", name, text, now)

    # ------------------------------------------------------------------ position plan
    def _plans(self, snaps: dict[str, GameSnap], now: datetime) -> list[Event]:
        out: list[Event] = []
        for eid, pos in self.positions.items():
            s = snaps.get(eid)
            if s is None or pos.settled:
                continue
            sl = seconds_left(s.period, s.clock_secs)
            p_home = self.model.p_home(s.margin, sl)
            is_home = pos.side_team == s.home
            ours = p_home if is_home else 1.0 - p_home
            lead = s.margin if is_home else -s.margin
            final = s.state == "post"
            bid = None
            q = self._quote(s, pos.side_team, now) if not final else None
            if q is not None:
                bid = q[1].bid
            won = (lead > 0) if final else None
            for line in plan.check(
                pos, plan.Obs(now.timestamp(), bid, ours, lead, sl, final, won), self.cfg
            ):
                self.notifier.alert("PLAN", line, now=now)
                out.append(Event("PLAN", pos.game, line, now))
        return out


def _f(x: float | None, nd: int = 3) -> str:
    return "na" if x is None else f"{x:.{nd}f}"


def quote_state(q: tuple[str, Quote] | None) -> str:
    if q is None or q[1].ask is None:
        return "none"
    return "ok" if 0.0 < q[1].ask < 1.0 else "no_room"


def format_ride(
    s: GameSnap,
    name: str,
    team: str,
    ours: float,
    espn: float | None,
    ticker: str,
    q: Quote,
    sl: sizing.Slice,
    sized: sizing.Sized,
    cfg: gate.RideConfig,
) -> str:
    assert q.ask is not None
    lead = abs(s.margin)
    mins, secs = divmod(int(s.clock_secs), 60)
    bid = "na" if q.bid is None else f"{q.bid:.2f}"
    head = (
        f"RIDE {name}: {team} up {lead} ({s.away}{s.away_score}-{s.home}{s.home_score}), "
        f"Q{s.period} {mins}:{secs:02d}. "
        f"Ours {ours:.1%}, ESPN {'na' if espn is None else f'{espn:.1%}'}."
    )
    buy = f"Buy YES {ticker} at ask ${q.ask:.2f} (bid {bid}, {q.source})."
    if not sized.fits:
        size = (
            f"Slice ${sl.slice_usd:.2f} (${cfg.credit:.2f} over {sl.n_games_able} games) "
            f"is below one contract "
            f"(cost ${sized.cost:.2f}); skip or use a bigger slice by your call."
        )
    else:
        size = (
            f"Slice ${sl.slice_usd:.2f} (${cfg.credit:.2f} over {sl.n_games_able} games): "
            f"{sized.contracts} contracts, "
            f"cost ${sized.cost:.2f} incl fee ${sized.fee:.2f} (taker). "
            f"If it holds: pays ${sized.payout_if_hit:.2f} (profit ${sized.profit_if_hit:.2f}); "
            f"expected payout ${sized.expected_payout:.2f} at our prob."
        )
    tail = "One alert per game; plan alerts only on a real change. You place any order by hand."
    return f"{head}\n{buy}\n{size}\n{tail}"


def run_live(
    root: Path,
    date_key: str,
    engine: Engine,
    interval_s: float = 60.0,
    max_steps: int | None = None,
    now_fn: Any = lambda: datetime.now(UTC),
    sleep_fn: Any = time.sleep,
) -> int:
    engine.notifier.log(
        "START", f"watch {date_key} root={root} interval={interval_s:.0f}s", now=now_fn()
    )
    n = 0
    while True:
        now = now_fn()
        try:
            engine.step(now)
        except Exception:
            engine.notifier.log(
                "ERROR", traceback.format_exc().replace("\n", " | ")[:1500], now=now
            )
        n += 1
        if max_steps is not None and n >= max_steps:
            return 0
        snaps = [engine.view.snap(e, now) for e in engine.view.event_ids()]
        if (
            snaps
            and all(s is not None and s.state == "post" for s in snaps)
            and all(p.settled for p in engine.positions.values())
        ):
            engine.notifier.log("DONE", "all games final and all positions settled", now=now_fn())
            return 0
        sleep_fn(max(1.0, interval_s - (now_fn() - now).total_seconds()))


def replay_times(root: Path) -> tuple[datetime, datetime] | None:
    ts = [t for p in (root / "espn_summary").glob("*/*.json.gz") if (t := _ts(p)) is not None]
    return (min(ts), max(ts)) if ts else None


def _ts(p: Path) -> datetime | None:
    from nba.ride.capture_view import file_ts

    return file_ts(p)


def run_replay(
    root: Path,
    date_key: str,
    model: WinProbModel,
    cfg: gate.RideConfig,
    fee: FeeModel,
    log_path: Path,
    step_s: float = 60.0,
) -> tuple[list[Event], Engine]:
    rcfg = replace(cfg, heartbeat_max_age_s=300.0, data_max_age_s=300.0)
    eng = Engine(root, date_key, model, rcfg, fee, Notifier(log_path, push=False), replay=True)
    span = replay_times(root)
    events: list[Event] = []
    if span is None:
        return events, eng
    t0, t1 = span
    t = t0
    while t <= t1 + timedelta(minutes=3):
        events += eng.step(t)
        t += timedelta(seconds=step_s)
    return events, eng
