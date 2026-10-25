"""Orchestration: the pre-tip slate summary and immediate alerts, with dedupe and an audit trail.

Decision order (this is the safety property): prices -> computed plays (label, edge, stake) -> a
deterministic gate (``supports_alert`` / slate due) -> text composed by Python -> optional LLM
sentence that passed its own checks -> send. The language model sits after the gate: it cannot
create a play, change a label or number, or cause a send the computed numbers do not support.

``dry_run`` prints and writes NOTHING to the paper book (so a rehearsal cannot dedupe a real send or
pollute the record). A real send is logged with hashes of its inputs (``sends`` table).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import duckdb

from nba.jarvis import imessage
from nba.jarvis import render as R
from nba.jarvis.book import PaperBook, now_naive, sha
from nba.jarvis.config import JarvisConfig
from nba.jarvis.explain import explain, play_facts
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.models import Play
from nba.jarvis.slate import (
    ModelLike,
    SlateState,
    best_overall,
    gather,
    plan_slate,
    rank_key,
)
from nba.parlay.assistant.backend import LLMBackend

Sender = Callable[..., imessage.SendResult]


@dataclass
class Runtime:
    cfg: JarvisConfig
    book: PaperBook
    kb: Knowledge
    backend: LLMBackend | None = None
    dry_run: bool = True
    sender: Sender = imessage.send
    clock: Callable[[], datetime] = now_naive
    poly_client: object | None = None
    model_factory: Callable[[date], ModelLike | None] | None = None
    out: Callable[[str], None] = print
    env: dict[str, str] | None = None
    use_polymarket: bool = True
    report: dict[str, Any] = field(default_factory=dict)


def supports_alert(cfg: JarvisConfig, p: Play) -> bool:
    """The only gate for an immediate text: computed label and numbers say strong or arbitrage."""
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
    text, src = explain(p, facts, rt.backend)
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
) -> dict[str, Any]:
    """Send every text; record plays and the send unless dry-run. Returns a small report."""
    modes = []
    notes = []
    for t in texts:
        res = rt.sender(t, env_var=rt.cfg.env_var, dry_run=rt.dry_run, env=rt.env, out=rt.out)
        modes.append(res.mode)
        if res.note:
            notes.append(res.note)
    ok = all(m in ("sent", "dry_run") for m in modes)
    if not rt.dry_run:
        mode = "sent" if ok else "failed"
        if ok:
            rt.book.record(
                plays,
                source=kind,
                slate=slate,
                explanations=explanations,
                dedupe_minutes=rt.cfg.dedupe_minutes,
                now=rt.clock(),
            )
        rt.book.record_send(
            kind=kind,
            mode=mode,
            slate=slate,
            keys=[p.key for p in plays],
            play_ids=[sha(p.key) for p in plays],
            text="\n".join(texts),
            inputs=inputs,
            note="; ".join(notes),
            now=rt.clock(),
        )
    return {"kind": kind, "texts": len(texts), "ok": ok, "modes": modes, "notes": notes}


def run_slate(rt: Runtime, slate: date, *, force: bool = False) -> dict[str, Any]:
    """Compose (and send unless dry-run) the slate summary. Returns the report incl. the texts."""
    now = rt.clock()
    st = build_state(rt, slate)
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
        rep["skipped"] = "slate summary already sent for this date"
        return rep
    top = best_overall(plays)
    top_text, src = ("", "none")
    if top is not None:
        top_text, src, _facts = explain_one(rt, st, top)
    ordered = sorted(plays, key=rank_key, reverse=True)
    texts = R.slate_texts(
        rt.cfg,
        ordered,
        top_text,
        date_label=slate.strftime("%a %-m/%-d"),
        first_tip=first_tip,
        n_games=len(st.games),
    )
    rep["texts"] = texts
    rep["explanation_source"] = src
    inputs = {"plays": [p.inputs for p in ordered], "notes": st.notes}
    rep["send"] = _deliver(
        rt,
        kind="slate",
        texts=texts,
        plays=ordered,
        slate=slate,
        inputs=inputs,
        explanations={top.key: top_text} if top else {},
    )
    return rep


def slate_due(rt: Runtime, st: SlateState) -> bool:
    now = rt.clock()
    tips = sorted(g.tipoff for g in st.games if g.tipoff is not None and g.tipoff > now)
    if not tips:
        return False
    remaining = (tips[0] - now).total_seconds() / 60.0
    return rt.cfg.min_minutes_to_tip < remaining <= rt.cfg.slate_lead_min + 2


def run_tick(rt: Runtime, slate: date) -> dict[str, Any]:
    """One watch pass: log closing-line quotes, slate summary if due, alerts for new strong/arb.
    A store held by its writer (nba.duckdb in the pretip job) skips the pass; the next retries."""
    now = rt.clock()
    try:
        return _tick(rt, slate, now)
    except duckdb.IOException as exc:
        return {"slate": str(slate), "at": now.isoformat(timespec="seconds"),
                "idle": f"store busy, retry next tick ({str(exc)[:60]})", "alerts": []}  # fmt: skip


def _tick(rt: Runtime, slate: date, now: datetime) -> dict[str, Any]:
    st = build_state(rt, slate)
    rep: dict[str, Any] = {
        "slate": str(slate),
        "at": now.isoformat(timespec="seconds"),
        "notes": st.notes,
    }
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
    alerts = []
    for p in plays:
        if p.key in sent_keys or not supports_alert(rt.cfg, p):
            continue
        if not rt.dry_run and rt.book.sent_recently(p.key, rt.cfg.dedupe_minutes, now):
            continue
        text, src, _ = explain_one(rt, st, p)
        why = "new arbitrage" if p.label == "arbitrage" else "new strong edge"
        texts = R.alert_text(rt.cfg, p, text, why)
        alerts.append(
            _deliver(
                rt,
                kind="alert",
                texts=texts,
                plays=[p],
                slate=slate,
                inputs=p.inputs,
                explanations={p.key: text},
            )
        )
    rep["alerts"] = alerts
    return rep


def minutes_to_first_tip(st: SlateState, now: datetime) -> float | None:
    tips = sorted(g.tipoff for g in st.games if g.tipoff is not None and g.tipoff > now)
    return None if not tips else (tips[0] - now).total_seconds() / 60.0
