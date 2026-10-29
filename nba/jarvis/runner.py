"""Orchestration: slate preview, instant alerts, halftime check-ins and the morning recap.

Decision order (the safety property): prices -> computed plays (label, edge, stake) -> a
deterministic gate (``supports_alert`` / slate due) -> text composed by Python -> optional LLM
sentence (strong/arbitrage only, hard-checked) -> post. The language model sits after the gate: it
cannot create a play, change a label or number, or cause a post the computed numbers do not support.

``dry_run`` prints and writes NOTHING to the paper book (a rehearsal cannot dedupe a real post or
pollute the record). A real post is logged with hashes of its inputs (``sends``). Plays are recorded
at decision time with a ``delivered`` status (sent / partial / failed); a post that partly failed is
never re-sent in full (the delivered part counts), and a key that keeps failing is given up on after
``max_attempts_per_key``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from nba.jarvis import dailydollar
from nba.jarvis import halftime_overs as HO
from nba.jarvis import render as R
from nba.jarvis.book import (
    PaperBook,
    kill_switch,
    latch_kill_switch,
    now_naive,
    score_book,
    sha,
)
from nba.jarvis.config import JarvisConfig
from nba.jarvis.explain import explain, play_facts
from nba.jarvis.imessage import SendResult
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.live import feed_age_min, latest_scoreboard
from nba.jarvis.models import Play
from nba.jarvis.posts import halftime_lines, recap_lines, todays_rows
from nba.jarvis.sinks import make_sinks
from nba.jarvis.slate import (
    ModelLike,
    SlateState,
    best_overall,
    gather,
    plan_slate,
    rank_key,
)
from nba.jarvis.sources import describe_store_busy, et_hour
from nba.jarvis.weights import render_changes, update_weights
from nba.parlay.assistant.backend import LLMBackend

log = logging.getLogger("nba.jarvis.runner")

Sender = Callable[..., SendResult]


@dataclass
class Runtime:
    cfg: JarvisConfig
    book: PaperBook
    kb: Knowledge
    backend: LLMBackend | None = None
    dry_run: bool = True
    sender: Sender | None = None  # default: the configured sinks (Discord, optional iMessage)
    clock: Callable[[], datetime] = now_naive
    poly_client: object | None = None
    model_factory: Callable[[date], ModelLike | None] | None = None
    out: Callable[[str], None] = print
    env: dict[str, str] | None = None
    use_polymarket: bool = True
    discord_client: Any | None = None
    ingame_flags: dict[str, tuple[int, str, Any]] = field(
        default_factory=dict
    )  # key -> (MIN, game time)
    ingame_seen: dict[str, set[str]] = field(
        default_factory=dict
    )  # keys posted this process (dry-runs too)
    report: dict[str, Any] = field(default_factory=dict)

    def send(self, text: str) -> SendResult:
        """Deliver one message. With the real sinks each is deduped on its own (a sink that already
        got this exact text is skipped), so one failing sink never causes repeats on the other."""
        if self.sender is not None:  # a custom single sender (tests)
            return self.sender(
                text, env_var=self.cfg.env_var, dry_run=self.dry_run, env=self.env, out=self.out
            )
        sinks = make_sinks(self.cfg, self.discord_client)
        if not sinks:
            return SendResult(False, "failed", "no delivery sink configured")
        th = sha(text)
        ok, notes, mode = True, [], "sent"
        for name, fn in sinks.items():
            if not self.dry_run and self.book.sink_done(name, th):
                continue
            r = fn(text, dry_run=self.dry_run, env=self.env, out=self.out)
            if r.ok and not self.dry_run:
                self.book.mark_sink(name, th, self.clock())
            if r.note:
                notes.append(f"{name}: {r.note}")
            ok = ok and r.ok
            mode = r.mode if r.ok else mode
        return SendResult(ok, mode if ok else "failed", "; ".join(notes))


def supports_alert(cfg: JarvisConfig, p: Play) -> bool:
    """The only gate for an immediate post: the computed label and numbers say strong or a
    CONFIRMED arbitrage. ``arb_candidate`` is not in ``alert_labels`` and never alerts."""
    if p.label not in cfg.alert_labels:
        return False
    if p.contracts <= 0 and not cfg.alert_when_cap_used:
        return False
    e = p.edge_cents
    if e != e:
        return False
    if p.label == "arbitrage":
        return e > cfg.arbitrage_min_cents
    return e >= cfg.strong_cents


def build_state(rt: Runtime, slate: date) -> SlateState:
    model = rt.model_factory(slate) if rt.model_factory else None
    return gather(
        rt.cfg,
        slate,
        rt.clock(),
        poly_client=rt.poly_client,
        model=model,
        use_polymarket=rt.use_polymarket,
    )


def explain_one(rt: Runtime, st: SlateState, p: Play) -> tuple[str, str, dict[str, Any]]:
    g = st.game(p.game_id)
    inj = rt.kb.injuries(p.game_id) if g else None
    lu = rt.kb.lineups(p.game_id) if g else None
    leg = p.legs[0]
    usage = rt.kb.usage_pace(
        team=leg.team_id or (g.home_team if g else None), player_id=leg.player_id
    )
    query = f"{p.matchup} {leg.desc}"
    fan = rt.kb.fan_knowledge(query, k=1)
    facts = play_facts(p, injuries=inj, lineups=lu, usage=usage, fan=fan)
    text, src = explain(p, facts, rt.backend)  # strong/arbitrage only; the rest use the template
    return text, src, facts


def _deliver(
    rt: Runtime,
    *,
    kind: str,
    texts: list[str],
    plays: list[Play],
    slate: date,
    inputs: Any,
    explanations: dict[str, str],
    keys: list[str] | None = None,
    record_source: str | None = None,
) -> dict[str, Any]:
    """Post every text; unless dry-run record the plays (with how far delivery got) and the post."""
    modes = []
    notes = []
    for t in texts:
        res = rt.send(t)
        modes.append(res.mode if res.ok else "failed")
        if res.note:
            notes.append(res.note)
    n_ok = sum(1 for m in modes if m in ("sent", "dry_run"))
    ok = n_ok == len(modes)
    mode = "sent" if ok else ("partial" if n_ok else "failed")
    if not rt.dry_run:
        if plays and record_source:
            rt.book.record(
                plays,
                source=record_source,
                slate=slate,
                explanations=explanations,
                delivered=mode,
                now=rt.clock(),
            )
        rt.book.record_send(
            kind=kind,
            mode=mode,
            slate=slate,
            keys=keys if keys is not None else [p.key for p in plays],
            play_ids=[sha(p.key) for p in plays],
            text="\n".join(texts),
            inputs=inputs,
            note="; ".join(notes),
            now=rt.clock(),
        )
    return {
        "kind": kind,
        "texts": len(texts),
        "ok": ok,
        "mode": mode,
        "modes": modes,
        "notes": notes,
    }


def _given_up(rt: Runtime, kind: str, slate: date, key: str | None = None) -> bool:
    return rt.book.failed_attempts(kind, slate, key) >= rt.cfg.max_attempts_per_key


def run_slate(rt: Runtime, slate: date, *, force: bool = False) -> dict[str, Any]:
    """Compose (and post unless dry-run) the slate preview. Returns the report incl. the texts."""
    now = rt.clock()
    st = build_state(rt, slate)
    if not rt.dry_run:
        latch_kill_switch(rt.cfg, rt.book, slate, now)
    plays = plan_slate(rt.cfg, st, rt.book)
    rep: dict[str, Any] = {
        "slate": str(slate),
        "games": len(st.games),
        "plays": len(plays),
        "notes": st.notes,
    }
    tips = sorted(g.tipoff for g in st.games if g.tipoff is not None and g.tipoff > now)
    first_tip = tips[0] if tips else None
    if not force and not rt.dry_run and rt.book.slate_summary_sent(slate):
        rep["skipped"] = "slate preview already posted for this date"
        return rep
    if not rt.dry_run and not force and _given_up(rt, "slate", slate):
        rep["skipped"] = "gave_up: the slate preview failed too many times today"
        return rep
    top = best_overall(plays)
    top_text, src = ("", "none")
    if top is not None:
        top_text, src, _facts = explain_one(rt, st, top)
    ordered = sorted(plays, key=rank_key, reverse=True)
    dd_play, dd_why = _daily_dollar(rt, st, slate)
    extra = dailydollar.lines(dd_play, dd_why) if rt.cfg.daily_dollar.get("enabled", True) else []
    for p in ordered:  # the plays this post carries count as announced for alert dedupe
        if supports_alert(rt.cfg, p) and not rt.dry_run:
            rt.book.note_alert(p.key, slate, p.edge_cents, now)
    texts = R.slate_texts(
        rt.cfg, ordered, top_text,
        date_label=slate.strftime("%a %-m/%-d"), first_tip=first_tip, n_games=len(st.games),
        extra_lines=extra,
    )  # fmt: skip
    rep["texts"] = texts
    rep["explanation_source"] = src
    rep["daily_dollar"] = None if dd_play is None else dd_play.key
    inputs = {"plays": [p.inputs for p in ordered], "notes": st.notes}
    posted = [*ordered, *([dd_play] if dd_play is not None else [])]
    expl = {top.key: top_text} if top else {}
    rep["send"] = _deliver(
        rt, kind="slate", texts=texts, plays=posted, slate=slate, inputs=inputs,
        explanations=expl, record_source="slate",
    )  # fmt: skip
    return rep


def _daily_dollar(rt: Runtime, st: SlateState, slate: date) -> tuple[Play | None, str]:
    if rt.cfg.daily_dollar.get("enabled", True) is not True:
        return None, "disabled"
    if not rt.dry_run and dailydollar.recorded_today(rt.book, slate):
        return None, "today's ticket was already posted"
    real, other = rt.book.spent_split(slate)
    room = min(
        rt.cfg.nightly_cap - real - other, rt.cfg.nightly_cap - rt.cfg.reserve_for_strong - other
    )
    return dailydollar.build(rt.cfg, st, rt.kb, room=max(0.0, room))


def slate_due(rt: Runtime, st: SlateState) -> bool:
    now = rt.clock()
    tips = sorted(g.tipoff for g in st.games if g.tipoff is not None and g.tipoff > now)
    if not tips:
        return False
    remaining = (tips[0] - now).total_seconds() / 60.0
    return rt.cfg.min_minutes_to_tip < remaining <= rt.cfg.slate_lead_min + 2


def run_tick(rt: Runtime, slate: date) -> dict[str, Any]:
    """One watch pass: log closing-line quotes, slate preview if due, alerts, halftime check-ins,
    the morning recap. A store held by its writer (nba.duckdb in the pretip job) skips the pass."""
    now = rt.clock()
    try:
        return _tick(rt, slate, now)
    except duckdb.IOException as exc:
        return {
            "slate": str(slate),
            "at": now.isoformat(timespec="seconds"),
            "idle": f"store busy, retry next tick ({describe_store_busy(exc)})",
            "alerts": [],
        }


def _tick(rt: Runtime, slate: date, now: datetime) -> dict[str, Any]:
    st = build_state(rt, slate)
    rep: dict[str, Any] = {
        "slate": str(slate),
        "at": now.isoformat(timespec="seconds"),
        "notes": st.notes,
        "alerts": [],
    }
    try:  # the recap is a courtesy: its bug must never take alerts and check-ins down with it
        rep["recap"] = _maybe_recap(rt, slate, now)
    except duckdb.IOException:
        raise  # a store held by its writer: run_tick reports it and the next tick retries
    except Exception as exc:
        log.exception("recap failed; the tick goes on (%s)", type(exc).__name__)
        rep["recap"] = {"kind": "recap", "ok": False, "error": type(exc).__name__}
    if not rt.dry_run:
        latch_kill_switch(rt.cfg, rt.book, slate, now)
    if not st.games:
        rep["idle"] = "no games"
        return rep
    plays = plan_slate(rt.cfg, st, rt.book)
    rep["plays"] = len(plays)
    if not rt.dry_run:
        wanted = rt.book.open_tickers(slate)
        rep["quotes_logged"] = rt.book.log_quotes(q for q in st.quotes if q.ticker in wanted)
    sent_keys: set[str] = set()
    if slate_due(rt, st) and (rt.dry_run or not rt.book.slate_summary_sent(slate)):
        sl = run_slate(rt, slate, force=True)
        rep["slate_send"] = sl.get("send")
        if sl.get("send", {}).get("ok"):
            sent_keys = {p.key for p in plays}
    for p in plays:
        if p.key in sent_keys or not supports_alert(rt.cfg, p):
            continue
        if not rt.dry_run:
            if rt.book.sent_recently(p.key, rt.cfg.dedupe_minutes, now):
                continue
            if _given_up(rt, "alert", slate, p.key):
                rep.setdefault("gave_up", []).append(p.key)
                continue
            if rt.book.kind_sent("alert", slate, p.key) and not rt.book.realert_due(
                p.key, slate, p.edge_cents, False, rt.cfg.realert_edge_cents
            ):
                continue
        text, _src, _ = explain_one(rt, st, p)
        why = "confirmed arbitrage" if p.label == "arbitrage" else "new strong edge"
        res = _deliver(
            rt, kind="alert", texts=R.alert_text(rt.cfg, p, text, why), plays=[p], slate=slate,
            inputs=p.inputs, explanations={p.key: text}, record_source="alert",
        )  # fmt: skip
        if res["ok"] and not rt.dry_run:
            rt.book.note_alert(p.key, slate, p.edge_cents, now)
        rep["alerts"].append(res)
    rep["halftime"] = _halftime(rt, st, slate, now, rep)
    try:  # in-game signals never break the tick; a busy store skips only this part
        from nba.jarvis.ingame_run import run_ingame

        rep["ingame"] = run_ingame(rt, slate, st.games)
    except Exception as exc:
        rep["ingame"] = [{"ok": False, "error": type(exc).__name__}]
    return rep


def tick_line(rep: dict[str, Any]) -> str:
    """The one launchd log line per tick. Anything swallowed on purpose (a failed recap, a refusing
    halftime scorer, a check-in error) is named here so it cannot hide behind ``plays=..``."""
    line = (
        f"tick {rep.get('at')}: plays={rep.get('plays')} alerts={len(rep.get('alerts', []))} "
        f"slate_send={'yes' if rep.get('slate_send') else 'no'} {rep.get('idle', '')}".strip()
    )
    flags = []
    recap = rep.get("recap")
    if isinstance(recap, dict) and recap.get("ok") is False:
        flags.append(f"recap_error={recap.get('error')}")
    for h in rep.get("halftime", []):
        if h.get("ok") is False:
            flags.append(f"{h.get('kind')}_error={h.get('error')}")
    ig = [r for r in rep.get("ingame", []) if isinstance(r, dict) and r.get("ok") is False]
    if ig:
        flags.append(f"ingame_error={ig[0].get('error')}")
    hs = rep.get("ht_scorer")
    if hs:
        flags.append(f"ht_scored={hs['stored']}rows/{hs['games']}games")
    return line + (" | " + " ".join(flags) if flags else "")


def _halftime(
    rt: Runtime, st: SlateState, slate: date, now: datetime, rep: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if not rt.cfg.halftime_checkins:
        return []
    out = []
    age = feed_age_min(rt.cfg, slate, now)
    live = latest_scoreboard(rt.cfg, slate)
    try:  # the paper scorer never costs a check-in
        scored = HO.score_halftimes(
            rt.cfg, slate, now, [lg.matchup for lg in live if lg.halftime], dry_run=rt.dry_run
        )
    except Exception as exc:
        scored = {"ok": False, "error": type(exc).__name__}
    if rep is not None and "stored" in scored:  # the scorer ran on halftime games: show that it did
        rep["ht_scorer"] = {
            "stored": scored["stored"], "refused": len(scored.get("refused", [])),
            "games": scored.get("counts", {}).get("games_halftime_seen", 0),
        }  # fmt: skip
    if scored.get("ok") is False:  # a persistent refusal must show in the tick report
        out.append({"kind": "halftime_scorer", "ok": False, "error": scored.get("error")})
    for lg in live:
        try:
            res = _halftime_one(rt, st, slate, lg, age)
        except Exception as exc:  # one bad game never blocks the others (or the tick)
            res = {"kind": "halftime", "ok": False, "error": type(exc).__name__, "game": lg.matchup}
        if res is not None:
            out.append(res)
        try:
            ov = _halftime_overs_one(rt, st, slate, lg, scored)
        except Exception as exc:  # the paper line never costs a tick
            ov = {"kind": "halftime_overs", "ok": False, "error": type(exc).__name__}
        if ov is not None:
            out.append(ov)
    return out


def _halftime_overs_one(
    rt: Runtime, st: SlateState, slate: date, lg: Any, scored: dict[str, Any]
) -> dict[str, Any] | None:
    """A short, labelled follow-up to the check-in: top_n scored overs at most (Yes only)."""
    if not lg.halftime or not scored.get("ok") or not scored.get("rows"):
        return None
    g = next((x for x in st.games if (x.away_abbr, x.home_abbr) == (lg.away, lg.home)), None)
    if g is None:
        return None
    s = HO.settings(rt.cfg)
    if not s.get("post_without_plays", False) and not todays_rows(rt.book, slate, g.game_id):
        return None
    picks = HO.top_overs(
        scored["rows"], lg.matchup, float(s["min_edge"]), int(s["top_n"]),
        int(s["display_min_bid_c"]), int(s["display_max_spread_c"]),
    )  # fmt: skip
    lines = HO.overs_lines(lg.matchup, picks)
    if not lines:
        return None
    if not rt.dry_run and rt.book.kind_sent("halftime_overs", slate, g.game_id):
        return None
    if not rt.dry_run and _given_up(rt, "halftime_overs", slate, g.game_id):
        return None
    return _deliver(
        rt,
        kind="halftime_overs",
        texts=R.chunk(lines, rt.cfg.max_chars, rt.cfg.max_texts),
        plays=[],
        slate=slate,
        inputs=[lg.matchup],
        explanations={},
        keys=[g.game_id],
    )


def _halftime_one(
    rt: Runtime, st: SlateState, slate: date, lg: Any, age: float | None
) -> dict[str, Any] | None:
    if not lg.halftime:
        return None
    g = next((x for x in st.games if (x.away_abbr, x.home_abbr) == (lg.away, lg.home)), None)
    if g is None:
        return None
    rows = todays_rows(rt.book, slate, g.game_id)
    if not rows:
        return None
    if not rt.dry_run and rt.book.kind_sent("halftime", slate, g.game_id):
        return None
    if not rt.dry_run and _given_up(rt, "halftime", slate, g.game_id):
        return None
    body = halftime_lines(rt.cfg, lg, rows, age, rt.clock())
    texts = R.chunk(body, rt.cfg.max_chars, rt.cfg.max_texts)
    return _deliver(
        rt,
        kind="halftime",
        texts=texts,
        plays=[],
        slate=slate,
        inputs=[lg.matchup],
        explanations={},
        keys=[g.game_id],
    )


def _maybe_recap(
    rt: Runtime, slate: date, now: datetime, force: bool = False
) -> dict[str, Any] | None:
    """Once per ET date after ``recap_after_et_hour``: score yesterday, run the weekly weight rule,
    post results, balance and records. Skipped silently when yesterday had no plays."""
    if not force and et_hour(now) < rt.cfg.recap_after_et_hour:
        return None
    day = slate - timedelta(days=1)
    if not force and not rt.dry_run and rt.book.kind_sent("recap", slate, str(day)):
        return None
    if not rt.book._rows("SELECT 1 FROM plays WHERE slate_date = ? LIMIT 1", [day]):
        return None
    if not rt.dry_run and _given_up(rt, "recap", slate, str(day)):
        return None
    if not rt.dry_run:
        score_book(rt.cfg, rt.book, now=now)
    recs = [] if rt.dry_run else update_weights(rt.cfg, rt.book, today=slate, now=now)
    lines = recap_lines(rt.cfg, rt.book, day, render_changes(recs), kill_switch(rt.cfg, rt.book))
    texts = R.chunk(lines, rt.cfg.max_chars, rt.cfg.max_texts)
    return _deliver(
        rt, kind="recap", texts=texts, plays=[], slate=slate, inputs=lines[:3],
        explanations={}, keys=[str(day)],
    )  # fmt: skip


def minutes_to_first_tip(st: SlateState, now: datetime) -> float | None:
    tips = sorted(g.tipoff for g in st.games if g.tipoff is not None and g.tipoff > now)
    return None if not tips else (tips[0] - now).total_seconds() / 60.0


def run_recap(rt: Runtime, slate: date, *, force: bool = True) -> dict[str, Any] | None:
    return _maybe_recap(rt, slate, rt.clock(), force=force)


__all__ = ["Runtime", "run_recap", "run_slate", "run_tick", "supports_alert", "tick_line"]
