# ruff: noqa: E501
"""In-game checkpoint scorer: T508 arm M and glm_e at every 5 game-minutes, from the live capture.

Extends the halftime scorer (``halftime_scorer``, e = 24) to the checkpoints 5, 10, ..., 45 elapsed game
minutes. Reads the write-once capture (``halftime_entry`` readers), the stored artifact and a recency
table from ``nba.duckdb`` opened READ ONLY; appends to its own sibling store (``checkpoint_store``).
Paper and display only: no order, no credential, no network, no outcome read, no new alert.

Checkpoint definition (draft rule HALFTIME_OVERS_VS_PRICE.md, section 12; chosen before any result):

* elapsed game time ``E = 720 (period - 1) + (720 - clock seconds)`` for periods 1-4, from the ESPN
  summary header (period, displayClock). Checkpoint ``c`` (5, 10, ..., 45) is "reached" by the first
  summary of the game with ``E >= 60 c``. Overtime (period > 4) is EXCLUDED: no checkpoint fires in it,
  and a checkpoint whose first at/after state is in overtime is refused (``overtime_excluded``).
* The tick: the first Kalshi page of the series received at or after that summary and within 90 s of
  it (every page of the series within 30 s of it is the same tick). ``known_at`` = that page's
  ``received_at`` (the summary came first or at the same instant).
* Refused, counted, never retried: the state is later than ``c`` by more than 150 s (a missed tick,
  ``checkpoint_missed_late``); status not in play (halftime, final, scheduled); overtime; the newest
  summary received by the quote page already shows the game over (``game_over_before_quote``); the
  summary's newest play is more than 180 s older than ``known_at`` (``state_stale``) or absent; the
  live write comes more than 180 s after ``known_at`` (``refused_late_write``).
* One row set per (game, series, checkpoint), written once; a restart recomputes the same tick and the
  store's key makes it a no-op.

M is served at the ACTUAL elapsed minutes (``e_used``). T508's artifact was trained at e in
{6, 12, ..., 42} pooled, so ``model_support`` is 'trained' for 6 <= e <= 42 and 'extrapolated'
otherwise (checkpoints 5 and 45 mostly). glm_e was fitted at e = 24 only; its probability is stored
at every checkpoint but is OFF its fitted support away from the half (the draft rule says what that
costs). Fee: the verified ``FeeModel`` object pinned in configs/jarvis.yaml, one contract, rounded up.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.markets import halftime_entry as he
from nba.markets.checkpoint_store import CheckpointStore
from nba.markets.halftime_model import Artifact, load_artifact
from nba.markets.halftime_scorer import RecencyProvider, check_fee, load_fee_pin, score_snapshot
from nba.parlay.config import FeeModel

CHECKPOINTS: tuple[int, ...] = tuple(range(5, 50, 5))  # elapsed game minutes
REGULATION_S = 2880.0
LATE_MAX_S = 150.0  # the first state at/after a checkpoint may be at most this far past it
QUOTE_MAX_AFTER_S = 90.0  # the quote page: at or after the state, at most this much later
STALE_PLAY_S = 180.0  # newest play older than this at known_at: stale (3 min)
LIVE_MAX_LAG_S = 180.0  # a live row must be written within this of known_at
LIVE_WINDOW_S = (
    300.0  # live: only summaries received within this of now are read (+ one predecessor)
)
TRAINED_E = (6.0, 42.0)  # T508's pooled checkpoint range
PRIMARY_MIN = 30  # the pre-declared late-game family: checkpoints >= 30 elapsed minutes
#: the primary family H2 of the draft rule (s12): late-game checkpoints inside T508's trained range
#: (6 <= e <= 42); the 45-minute checkpoint is served by extrapolation and stays a descriptive slice
LATE_FAMILY: tuple[int, ...] = (30, 35, 40)
LIVE_STATUS = ("STATUS_IN_PROGRESS", "STATUS_END_PERIOD")
OVER_STATUS = ("STATUS_FINAL", "STATUS_POSTPONED", "STATUS_CANCELED")
#: skip reasons that can still resolve on a later live pass; every other reason is final
TRANSIENT = ("tick_not_closed", "series_no_page_in_window")


def clock_seconds(txt: Any) -> float | None:
    """ESPN displayClock ("7:41", "12.8", "0.0") as seconds remaining in the period."""
    s = str(txt or "").strip()
    try:
        if ":" in s:
            m, sec = s.split(":", 1)
            return float(m) * 60 + float(sec)
        return float(s)
    except ValueError:
        return None


def elapsed_seconds(period: Any, clock: Any) -> float | None:
    """Elapsed game seconds (regulation 0-2880; overtime counted past 2880), None if unreadable."""
    try:
        p = int(period)
    except (TypeError, ValueError):
        return None
    rem = clock_seconds(clock)
    if rem is None or p < 1:
        return None
    if p <= 4:
        return 720.0 * (p - 1) + (720.0 - rem)
    return REGULATION_S + 300.0 * (p - 5) + (300.0 - rem)


def support_of(e: float) -> str:
    return "trained" if TRAINED_E[0] <= e <= TRAINED_E[1] else "extrapolated"


@dataclass(frozen=True)
class State:
    event_id: str
    received_at: datetime
    path: Path
    status: str
    period: int
    clock: str
    elapsed_s: float | None
    away_espn: str | None
    home_espn: str | None
    away_score: int | None
    home_score: int | None
    last_play_at: datetime | None

    @property
    def live(self) -> bool:
        return self.status in LIVE_STATUS


def parse_state(entry: he.Entry) -> State | None:
    ts, path, key = entry
    try:
        body = he.read_gz(path)
        comp = body["header"]["competitions"][0]
        st = comp["status"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    teams: dict[str, tuple[str, int | None]] = {}
    for c in comp.get("competitors") or []:
        try:
            teams[c["homeAway"]] = (c["team"]["abbreviation"], he._int(c.get("score")))
        except (KeyError, TypeError):
            continue
    plays = body.get("plays") or []
    lp: datetime | None = None
    if plays and plays[-1].get("wallclock"):
        try:
            lp = he.parse_ts(str(plays[-1]["wallclock"]).replace("Z", "+00:00"))
        except ValueError:
            lp = None
    try:
        period = int(st.get("period") or 0)
    except (TypeError, ValueError):
        period = 0
    clock = str(st.get("displayClock") or "")
    away, home = teams.get("away"), teams.get("home")
    return State(
        key, ts, path, str(st.get("type", {}).get("name") or ""), period, clock,
        elapsed_seconds(period, clock) if period else None,
        None if away is None else away[0], None if home is None else home[0],
        None if away is None else away[1], None if home is None else home[1], lp,
    )  # fmt: skip


@dataclass
class Fire:
    """One (game, series, checkpoint) decision: a snapshot to score, or the reason there is none."""

    event_id: str
    game: str
    checkpoint: int
    series: str
    state: State
    known_at: datetime | None = None
    snap: he.Snapshot | None = None
    reason: str | None = None
    counts: Counter[str] = field(default_factory=Counter)


class CheckpointReader:
    """One capture night as of ``as_of`` (None = everything: a replay)."""

    def __init__(self, night: Path, as_of: datetime | None = None) -> None:
        self.night = night
        self.as_of = None if as_of is None else he.utc(as_of)
        man = he.load_manifest(night, self.as_of)
        self.summaries: dict[str, list[he.Entry]] = defaultdict(list)
        for e in man.get("espn_summary", []):
            self.summaries[e[2]].append(e)
        self.kalshi: dict[str, list[he.Entry]] = defaultdict(list)
        for e in man.get("kalshi", []):
            series = e[2].split("_p")[0]
            if series in he.SERIES:
                self.kalshi[series].append(e)
        self._cache: dict[Path, State | None] = {}

    def state(self, e: he.Entry) -> State | None:
        if e[1] not in self._cache:
            self._cache[e[1]] = parse_state(e)
        return self._cache[e[1]]

    def states(self, event_id: str) -> list[State]:
        """Parsed summaries of one game, oldest first. Live: only the recent window plus one earlier
        summary (so "first at/after" is judged against its predecessor), never the whole night."""
        ents = self.summaries.get(event_id, [])
        if self.as_of is not None:
            cut = self.as_of - timedelta(seconds=LIVE_WINDOW_S)
            lo = next((i for i, e in enumerate(ents) if e[0] >= cut), len(ents))
            ents = ents[max(0, lo - 1) :]
        return [s for s in (self.state(e) for e in ents) if s is not None]

    def latest(self, event_id: str) -> State | None:
        ents = self.summaries.get(event_id, [])
        for e in reversed(ents[-5:]):
            s = self.state(e)
            if s is not None:
                return s
        return None

    def first_at_or_after(self, states: list[State]) -> dict[int, State]:
        """checkpoint -> the first state with elapsed >= 60 c (any status), judged in time order."""
        out: dict[int, State] = {}
        peak = -1.0
        for s in states:
            if s.elapsed_s is None:
                continue
            for c in CHECKPOINTS:
                if c not in out and s.elapsed_s >= 60.0 * c and peak < 60.0 * c:
                    out[c] = s
            peak = max(peak, s.elapsed_s)
        return out

    def _over_by(self, event_id: str, t: datetime) -> bool:
        """The newest summary received at or before ``t`` already shows the game over (or in overtime)."""
        best: he.Entry | None = he.state_before(self.summaries.get(event_id, []), t)
        s = None if best is None else self.state(best)
        return s is not None and (
            s.status in OVER_STATUS or s.period > 4 or (s.elapsed_s or 0.0) >= REGULATION_S
        )

    def fires(
        self,
        only_games: set[str] | None,
        done: Callable[[str, str, int], bool],
        events: set[str] | None = None,
    ) -> Iterator[Fire]:
        for eid in sorted(self.summaries):
            if events is not None and eid not in events:
                continue
            last = self.latest(eid)
            if last is None or last.away_espn is None or last.home_espn is None:
                continue
            if self.as_of is not None and not last.live:
                continue  # live: a game that is not in play at ``now`` scores nothing
            sts = self.states(eid)
            for c, s in sorted(self.first_at_or_after(sts).items()):
                yield from self._fires_for(eid, c, s, only_games, done)

    def _fires_for(
        self,
        eid: str,
        c: int,
        s: State,
        only_games: set[str] | None,
        done: Callable[[str, str, int], bool],
    ) -> Iterator[Fire]:
        assert s.away_espn is not None and s.home_espn is not None
        away = he.ESPN_TO_KALSHI.get(s.away_espn, s.away_espn)
        home = he.ESPN_TO_KALSHI.get(s.home_espn, s.home_espn)
        game = f"{away}@{home}"
        if only_games is not None and game not in only_games:
            return
        assert s.elapsed_s is not None
        for series in he.SERIES:
            if done(eid, series, c):
                continue
            f = Fire(eid, game, c, series, s)
            f.reason = self._state_reason(s, c)
            if f.reason is None:
                self._quote(f, s, away, home)
            yield f

    @staticmethod
    def _state_reason(s: State, c: int) -> str | None:
        assert s.elapsed_s is not None
        if s.period > 4:
            return "overtime_excluded"
        if s.elapsed_s - 60.0 * c > LATE_MAX_S:
            return "checkpoint_missed_late"
        if not s.live:
            return "state_not_live"
        return None

    def _quote(self, f: Fire, s: State, away: str, home: str) -> None:
        pages = [p for p in self.kalshi.get(f.series, []) if p[0] >= s.received_at]
        if not pages or (pages[0][0] - s.received_at).total_seconds() > QUOTE_MAX_AFTER_S:
            f.reason = "series_no_page_in_window"
            return
        first_ts = pages[0][0]
        if self.as_of is not None and self.as_of < first_ts + timedelta(
            seconds=he.SAME_TICK_S + he.TICK_CLOSE_MARGIN_S
        ):
            f.reason = "tick_not_closed"
            return
        f.known_at = first_ts
        if self._over_by(f.event_id, first_ts):
            f.reason = "game_over_before_quote"
            return
        if s.last_play_at is None:
            f.reason = "state_no_plays"
            return
        if (first_ts - s.last_play_at).total_seconds() > STALE_PLAY_S:
            f.reason = "state_stale"
            return
        tick = [p for p in pages if (p[0] - first_ts).total_seconds() < he.SAME_TICK_S]
        ht = he.Halftime(
            f.event_id, away, home, str(s.away_espn), str(s.home_espn), s.received_at, None,
            s.away_score, s.home_score,
        )  # fmt: skip
        snap = he.Snapshot(
            f.event_id, f.game, f.series, s.received_at, first_ts, first_ts, str(tick[0][1].name),
            s.received_at, str(s.path.name),
        )  # fmt: skip
        box = he.box_players(he.read_gz(s.path))
        stat = he.SERIES[f.series]
        for _, path, _key in tick:
            for m in he.read_gz(path).get("markets", []):
                he.consider_market(ht, m, f.series, stat, box, snap)
        f.snap = snap
        f.counts.update(snap.excluded)


@dataclass
class CheckpointReport:
    rows: list[dict[str, Any]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    by_checkpoint: dict[int, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    stored: int = 0


_TALLIES = ("scored_candidates", "markets_at_snapshot")


def _final(reason: str, as_of: datetime | None, f: Fire) -> bool:
    """Final skip reasons are noted once and never retried; a transient one can resolve live."""
    if as_of is None or reason not in TRANSIENT:
        return True
    return reason == "series_no_page_in_window" and as_of > f.state.received_at + timedelta(
        seconds=QUOTE_MAX_AFTER_S + 30
    )


def score_night_checkpoints(
    night: Path,
    art: Artifact,
    rec: RecencyProvider,
    fee: FeeModel,
    store: CheckpointStore,
    slate: date,
    now: datetime | None,
    fee_pin: dict[str, Any],
    only_games: set[str] | None = None,
    min_prior: int = 5,
) -> CheckpointReport:
    """Score and store the checkpoints one capture night has reached.

    ``now`` given (live): the capture is read as of ``now``, every row is stamped ``now`` and refused
    when ``now - known_at`` exceeds 180 s; only games in play are read. ``now`` None (replay): each
    (game, series, checkpoint) is stamped at its own ``known_at`` into a replay store.
    """
    check_fee(fee, fee_pin, tuple(he.SERIES))
    if (now is None) != (store.mode == "replay"):
        raise ValueError("a live store needs the clock (now); a replay store must not get one")
    as_of = None if now is None else he.utc(now)
    rep = CheckpointReport()
    reader = CheckpointReader(night, as_of)

    def done(eid: str, series: str, c: int) -> bool:
        return store.has(eid, series, c) or store.noted(eid, series, c)

    for f in reader.fires(only_games, done):
        bc = rep.by_checkpoint[f.checkpoint]
        rep.counts["fires"] += 1
        bc["fires"] += 1
        reason = f.reason
        lag = None if as_of is None or f.known_at is None else (as_of - f.known_at).total_seconds()
        if reason is None and lag is not None and lag > LIVE_MAX_LAG_S:
            reason = "refused_late_write"
        if reason is not None:
            rep.counts[reason] += 1
            bc[reason] += 1
            if _final(reason, as_of, f):
                store.note_skip(
                    as_of or f.known_at or f.state.received_at,
                    f.event_id,
                    f.series,
                    f.checkpoint,
                    reason,
                    1,
                )
            continue
        assert f.snap is not None and f.known_at is not None and f.state.elapsed_s is not None
        made = as_of if as_of is not None else f.known_at
        e_used = f.state.elapsed_s / 60.0
        sc: Counter[str] = Counter()
        rows = score_snapshot(f.snap, art, rec, fee, made, slate, sc, min_prior, e=e_used)
        for k, v in f.snap.excluded.items():
            rep.counts["unmatched_names" if k.startswith("unmatched_names:") else k] += v
        rep.counts.update(sc)
        for reason2, n in [*sc.items(), *f.snap.excluded.items()]:
            if reason2 not in _TALLIES and not reason2.startswith("unmatched_names:"):
                store.note_skip(made, f.event_id, f.series, f.checkpoint, reason2, n)
        if (
            not rows
        ):  # fired and complete, nothing scoreable: mark it done so a restart does not redo it
            store.note_skip(made, f.event_id, f.series, f.checkpoint, "no_scoreable_candidates", 1)
            bc["no_rows"] += 1
            continue
        out = []
        for r in rows:
            r.pop("t_ht", None)
            r.pop("t_star", None)
            r.update(
                checkpoint=f.checkpoint, e_used=e_used, model_support=support_of(e_used),
                period=f.state.period, clock=f.state.clock, known_at=f.known_at,
                last_play_at=f.state.last_play_at,
                play_age_s=None if f.state.last_play_at is None else (f.known_at - f.state.last_play_at).total_seconds(),
                lateness_s=f.state.elapsed_s - 60.0 * f.checkpoint,
            )  # fmt: skip
            out.append(r)
        n = store.append(out, made, min_prior)
        rep.stored += n
        bc["stored"] += n
        rep.rows.extend(out)
    return rep


# ------------------------------------------------------------------------------ label-free gate counts


def gate_counts(night: Path) -> dict[str, Any]:
    """Counts per checkpoint on one capture night, as a replay (no model, no outcome, no final score).

    ``reached``: games whose summaries show elapsed >= 60 c in regulation. ``fired``: (game, series)
    decisions; ``valid``: those with a snapshot; ``strikes``: on-grid tradeable Yes quotes in them;
    ``units``: distinct (game, player, stat). A refused reason is counted by name.
    """
    reader = CheckpointReader(night, None)
    out: dict[int, Counter[str]] = {c: Counter() for c in CHECKPOINTS}
    units: dict[int, set[tuple[str, str, str]]] = {c: set() for c in CHECKPOINTS}
    for eid in sorted(reader.summaries):
        sts = reader.states(eid)
        for c, s in reader.first_at_or_after(sts).items():
            if s.period <= 4:
                out[c]["reached"] += 1
    for f in reader.fires(None, lambda *_: False):
        o = out[f.checkpoint]
        o["fired"] += 1
        if f.reason is not None:
            o["x_" + f.reason] += 1
            continue
        assert f.snap is not None
        o["valid"] += 1
        o["strikes"] += len(f.snap.candidates)
        for cand in f.snap.candidates:
            units[f.checkpoint].add((f.event_id, cand.player_name, cand.stat))
        for k, v in f.snap.excluded.items():
            if k.startswith("x_"):
                o[k] += v
    return {
        "night": night.name,
        "by_checkpoint": {c: {**dict(out[c]), "units": len(units[c])} for c in CHECKPOINTS},
    }


def replay_main(argv: list[str] | None = None) -> int:
    from nba.parlay.config import load_config

    ap = argparse.ArgumentParser(prog="checkpoint_scorer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="label-free counts per checkpoint on stored capture nights")
    g.add_argument("--night", type=Path, nargs="+", required=True)
    r = sub.add_parser("replay", help="score a stored capture night (no network, no send)")
    r.add_argument(
        "--night", type=Path, nargs="+", required=True, help="one or more capture nights, pooled"
    )
    r.add_argument("--artifact", type=Path, required=True)
    r.add_argument("--nba-db", type=Path, required=True)
    r.add_argument("--db", default=":memory:")
    r.add_argument("--parlay-config", type=Path, default=Path("configs/parlay.yaml"))
    r.add_argument(
        "--min-game-date", required=True, help="YYYY-MM-DD or 'none' (prior seasons as features)"
    )
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        print(json.dumps([gate_counts(n) for n in a.night], indent=1, default=str))
        return 0
    mgd = (
        None if a.min_game_date.lower() in ("none", "null") else date.fromisoformat(a.min_game_date)
    )
    cfg = load_config(a.parlay_config)
    store = CheckpointStore.open(a.db, mode="replay")
    art, pin = load_artifact(a.artifact), load_fee_pin()
    per_night: dict[str, Any] = {}
    unresolved: Counter[str] = Counter()
    for night in a.night:
        slate = date.fromisoformat(night.name.removeprefix("live_"))
        rec = RecencyProvider(a.nba_db, slate, min_game_date=mgd)
        rep = score_night_checkpoints(night, art, rec, cfg.fee, store, slate, None, pin)
        per_night[night.name] = {"stored": rep.stored, "counts": dict(rep.counts)}
        unresolved.update(rec.unresolved)
    print(json.dumps({
        "mode": "replay (measurement only; not a forward row)",
        "recency_min_game_date": a.min_game_date, "per_night": per_night,
        "by_checkpoint": store.counts_by_checkpoint(),
        "late_family_first_trigger": store.entry_profile(LATE_FAMILY),
        "late_family_first_trigger_glm_e": store.entry_profile(LATE_FAMILY, column="edge_glm_e"),
        "unresolved_players": dict(unresolved),
    }, indent=1, default=str))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(replay_main())
