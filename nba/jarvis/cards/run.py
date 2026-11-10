"""The card pass of one JARVIS tick: build each game's card, post it once per team channel, edit it
at halftime and at the final, and settle the overs it showed.

Delivery only. The pass reads prices, plays and halftime rows that other modules computed; it
never changes a label, edge, fee, stake or alert condition, and a card edit is silent. Any failure
is contained: a broken card costs that card, never the tick. A dry run builds everything, sends and
writes nothing.

Phases of a game, by the live scoreboard: ``pretip`` (post within ``post_lead_min`` of tip, refresh
at most every ``pretip_min_edit_interval_min``), ``live`` (no edit: calm), ``halftime`` (edit),
``final`` (edit, then edit again when the box score settles the overs).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.cards.discord import CardClient, CardResult, RateGate
from nba.jarvis.cards.embed import build_embed, content_hash
from nba.jarvis.cards.model import CardData, OverRow, Recap
from nba.jarvis.cards.recap import recap_for_game, settle_pending
from nba.jarvis.cards.state import CardRecord, CardState
from nba.jarvis.cards.webhooks import TeamWebhooks, fingerprint, load_team_webhooks
from nba.jarvis.config import JarvisConfig
from nba.jarvis.ingame import PlayerLine, load_snapshots
from nba.jarvis.live import LiveGame, feed_age_min, latest_scoreboard
from nba.jarvis.models import Game
from nba.jarvis.render import cents
from nba.jarvis.slate import SlateState

log = logging.getLogger("nba.jarvis.cards")

# The ET slate date rolls at 04:00/05:00 UTC; from then until mid-afternoon ET the previous slate is
# still checked, because late games finish after the roll and their box scores arrive overnight.
YESTERDAY_FROM_UTC_HOUR = 4
YESTERDAY_UNTIL_UTC_HOUR = 19

MainPoster = Callable[[str, list[str], list[str]], dict[str, Any]]  # (kind, texts, keys) -> result


def settings(cfg: JarvisConfig) -> dict[str, Any]:
    return dict(cfg.team_cards)


def enabled(cfg: JarvisConfig) -> bool:
    return bool(cfg.team_cards.get("enabled", False))


def style_of(cfg: JarvisConfig) -> T.Style:
    s = cfg.team_cards
    return T.Style(
        tz=str(s["tz"]), pace_fraction=float(s["pace_fraction"]), narrow_pts=float(s["narrow_pts"]),
        wide_pts=float(s["wide_pts"]), bar_width=int(s["bar_width"]),
        min_edge_c=100.0 * float(cfg.halftime_overs.get("min_edge", 0.05)),
    )  # fmt: skip


def resolve(cfg: JarvisConfig, key: str) -> Path:
    p = Path(str(cfg.team_cards[key]))
    return p if p.is_absolute() else cfg.root / p


def state_of(cfg: JarvisConfig) -> CardState:
    return CardState(resolve(cfg, "state"), resolve(cfg, "picks"))


def webhooks_of(cfg: JarvisConfig, env: Mapping[str, str] | None) -> TeamWebhooks:
    s = cfg.team_cards
    return load_team_webhooks(
        str(s["env_var"]), keychain_service=str(s.get("keychain_service", "")), env=env
    )


def phase_of(g: Game, lg: LiveGame | None, now: datetime, lead_min: float) -> str | None:
    """The card phase for a game now, or None (too early, or nothing to say)."""
    if g.tipoff is None:
        return None
    if lg is not None:
        if lg.status == "STATUS_FINAL":
            return "final"
        if lg.status == "STATUS_HALFTIME":
            return "halftime"
        if lg.status == "STATUS_IN_PROGRESS":
            return "live"
    if now < g.tipoff:
        return "pretip" if (g.tipoff - now) <= timedelta(minutes=lead_min) else None
    return "live" if (now - g.tipoff) < timedelta(hours=4) else None


@dataclass
class Built:
    game: Game
    card: CardData
    phase: str


@dataclass
class Deps:
    """What a card needs from the runtime, so the preview, dry-run and tick share one builder."""

    cfg: JarvisConfig
    st: SlateState
    kb: Any
    state: CardState
    clock: datetime
    sent_alert_keys: set[str] = field(default_factory=set)
    scorer_rows: list[dict[str, Any]] = field(default_factory=list)
    write: bool = True  # False: log nothing (dry run / preview)


def _box(
    cfg: JarvisConfig, slate: date, lg: LiveGame | None, now: datetime
) -> tuple[PlayerLine, ...]:
    if lg is None:
        return ()
    try:
        by = load_snapshots(cfg.root, slate, now, 15.0)
        for snaps in by.values():
            if snaps[-1].matchup == lg.matchup:
                return snaps[-1].players
    except Exception as exc:  # noqa: BLE001 - the box score is a convenience
        log.warning("card box score unreadable (%s)", type(exc).__name__)
    return ()


def build_card(
    d: Deps,
    g: Game,
    phase: str,
    lg: LiveGame | None,
    feed_age: float | None,
    slate: date | None = None,
) -> CardData:
    cfg, s = d.cfg, d.cfg.team_cards
    slate = slate or d.st.slate
    now = d.clock
    shown_at = now.isoformat(timespec="seconds")
    overs: list[OverRow] = []
    frozen, note = phase != "pretip", ""
    if phase == "pretip":
        overs, _priced = F.pregame_overs(cfg, d.st, g.game_id, int(s["top_overs"]), shown_at, d.kb)
    else:
        logged = [p for p in d.state.picks(slate) if p["game_id"] == g.game_id]
        overs = F.overs_from_picks(logged)[: int(s["top_overs"])] if logged else []
        if not logged and not _had_card(d.state, slate, g):
            note = "No pre-tip card was posted for this game, so there is no over to review."
    half: list[Any] = []
    if phase == "halftime":
        box = _box(cfg, slate, lg, now)
        half = F.half_legs(cfg, overs, d.scorer_rows, g.matchup, box)
    recap: Recap | None = None
    if phase == "final":
        recap = recap_for_game(cfg, d.state, g.game_id)
    alert = ""
    if any(k.startswith(f"{g.game_id}/") for k in d.sent_alert_keys):
        alert = "A strong-edge alert for this game went to the main channel."
    return CardData(
        game_id=g.game_id, away=g.away_abbr, home=g.home_abbr, tip=g.tipoff, phase=phase,
        as_of=now, away_score=None if lg is None else lg.away_score,
        home_score=None if lg is None else lg.home_score, clock=lg.clock if lg else "",
        overs=tuple(overs), overs_frozen=frozen, overs_note=note, half=tuple(half),
        knows=F.game_knowledge(d.kb, g, now), recap=recap, alert_note=alert, feed_age_min=feed_age,
    )  # fmt: skip


def _had_card(state: CardState, slate: date, g: Game) -> bool:
    return any(state.latest(slate, g.game_id, t) is not None for t in (g.away_abbr, g.home_abbr))


# ------------------------------------------------------------------------------ delivery


def need_edit(rec: CardRecord, phase: str, h: str, now: datetime, interval_min: float) -> bool:
    """Calm rule: silent edits at halftime and final (when the content changed or the phase moved),
    pre-tip refreshes at most every ``interval_min``, nothing while the game is live."""
    if phase in ("halftime", "final"):
        return rec.phase != phase or rec.content_hash != h
    if phase == "pretip":
        if rec.content_hash == h:
            return False
        try:
            last = datetime.fromisoformat(rec.ts)
        except ValueError:
            return True
        return (now - last) >= timedelta(minutes=interval_min)
    return False


def deliver_one(
    client: CardClient, state: CardState, url: str, rec: CardRecord | None, *, slate: date,
    game_id: str, team: str, phase: str, embed: dict[str, Any], now: datetime,
    interval_min: float, write: bool,
) -> str:  # fmt: skip
    """One (game, team) card: post, edit or nothing. Returns what happened (a word, never a URL)."""
    h = content_hash(embed)
    fp = fingerprint(url)
    body = {"embeds": [embed]}
    kind: str
    if rec is None or rec.fp != fp:
        kind = "post" if rec is None else "repost"
        if not write:
            return "would_" + kind
        res = client.post(url, body)
    elif need_edit(rec, phase, h, now, interval_min):
        kind = "edit"
        if not write:
            return "would_edit"
        res = client.edit(url, rec.message_id, body)
        if res.status in ("gone", "failed"):  # a definitive refusal: the old message is unusable
            kind = "repost"
            res = client.post(url, body)
    else:
        return "unchanged"
    return _record(state, res, slate, game_id, team, rec, phase, h, fp, kind, now)


def _record(
    state: CardState, res: CardResult, slate: date, game_id: str, team: str,
    rec: CardRecord | None, phase: str, h: str, fp: str, kind: str, now: datetime,
) -> str:  # fmt: skip
    if res.ok and res.message_id:
        state.record(
            CardRecord(str(slate), game_id, team, res.message_id, phase, h, fp, kind,
                       now.isoformat(timespec="seconds"))
        )  # fmt: skip
        return {"post": "posted", "edit": "edited", "repost": "reposted"}[kind]
    if res.ok:
        return "failed_no_message_id"
    return res.status if res.status in ("deferred", "failed") else "failed"


# ------------------------------------------------------------------------------ the pass


def _day_games(rt: Any, st: SlateState, day: date) -> list[Game]:
    if day == st.slate:
        return st.games
    from nba.jarvis.sources import load_games

    return load_games(rt.cfg, day)


def _quiet(d: Deps, day: date, g: Game, phase: str, wh: TeamWebhooks, interval_min: float) -> bool:
    """True when no post or edit is possible this tick, decided from the state file alone: every
    channel already has its card (same webhook), and the game is live (calm: no edits) or the
    pre-tip card was written less than ``interval_min`` ago."""
    if phase not in ("live", "pretip"):
        return False
    for t in (g.away_abbr, g.home_abbr):
        url = wh.urls.get(t)
        if url is None:
            continue
        rec = d.state.latest(day, g.game_id, t)
        if rec is None or rec.fp != fingerprint(url):
            return False
        if phase == "pretip":
            try:
                if d.clock - datetime.fromisoformat(rec.ts) >= timedelta(minutes=interval_min):
                    return False
            except ValueError:
                return False
    return True


def _final_done(d: Deps, day: date, g: Game) -> bool:
    """Every recorded final card is current and no over of this game waits on a result."""
    found = [d.state.latest(day, g.game_id, t) for t in (g.away_abbr, g.home_abbr)]
    recs = [r for r in found if r is not None]
    if not recs or any(r.phase != "final" for r in recs):
        return False
    return all("settled" in p for p in d.state.picks(day) if p["game_id"] == g.game_id)


def run_cards(
    rt: Any,
    st: SlateState,
    slate: date,
    now: datetime,
    post_main: MainPoster | None = None,
) -> dict[str, Any]:
    """The card pass. ``rt`` is the JARVIS ``Runtime`` (config, knowledge, book, clock, env)."""
    cfg: JarvisConfig = rt.cfg
    if not enabled(cfg):
        return {"skipped": "disabled"}
    env = rt.env if rt.env is not None else ({} if rt.sender is not None else dict(os.environ))
    wh = webhooks_of(cfg, env)
    rep: dict[str, Any] = {"webhooks": len(wh.urls)}
    if wh.invalid:
        rep["invalid_webhooks"] = sorted(wh.invalid)
    if not wh.urls:
        rep["skipped"] = "no team webhooks configured"
        return rep
    s = settings(cfg)
    style = style_of(cfg)
    state = state_of(cfg)
    write = not rt.dry_run
    d = Deps(cfg, st, rt.kb, state, now, write=write)
    try:
        d.sent_alert_keys = rt.book.sent_keys("alert", slate) if rt.book.path.exists() else set()
    except Exception as exc:  # noqa: BLE001
        log.warning("card alert note unavailable (%s)", type(exc).__name__)
    d.scorer_rows = list((getattr(rt, "ht_scored", None) or {}).get("rows") or [])
    http = rt.card_client
    gate = RateGate(float(s["rate_budget_s"]), rt.card_sleep or time.sleep)
    client = CardClient(http, gate, secrets=list(wh.urls.values()))
    counts: dict[str, int] = {}
    missing: set[str] = set()
    errors: list[str] = []
    try:
        days = [slate]
        if YESTERDAY_FROM_UTC_HOUR <= now.hour < YESTERDAY_UNTIL_UTC_HOUR:
            days = [slate - timedelta(days=1), slate]
        for day in days:
            _one_day(rt, d, client, wh, day, style, s, counts, missing, errors, now)
        if write:
            settle_pending(cfg, state, now)
        _overview(rt, d, slate, counts, post_main)
    finally:
        client.close()
    rep["cards"] = counts
    if missing:
        rep["no_webhook_teams"] = sorted(missing)
        log.warning(
            "cards: %d team(s) have no webhook and were skipped: %s",
            len(missing),
            ",".join(sorted(missing)),
        )
    if errors:
        rep["errors"] = errors
    rep["rate_429"] = gate.n_429
    return rep


def _one_day(
    rt: Any, d: Deps, client: CardClient, wh: TeamWebhooks, day: date, style: T.Style,
    s: dict[str, Any], counts: dict[str, int], missing: set[str], errors: list[str], now: datetime,
) -> None:  # fmt: skip
    cfg = d.cfg
    try:
        games = _day_games(rt, d.st, day)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"games:{type(exc).__name__}")
        return
    live = {(x.away, x.home): x for x in latest_scoreboard(cfg, day)}
    age = feed_age_min(cfg, day, now)
    for g in games:
        lg = live.get((g.away_abbr, g.home_abbr))
        phase = phase_of(g, lg, now, float(s["post_lead_min"]))
        if phase is None or (day != d.st.slate and phase != "final"):
            continue
        if phase == "final" and _final_done(d, day, g):
            continue
        teams = [t for t in (g.away_abbr, g.home_abbr)]
        for t in teams:
            if t not in wh.urls:
                missing.add(t)
        if not any(t in wh.urls for t in teams):
            continue
        if _quiet(d, day, g, phase, wh, float(s["pretip_min_edit_interval_min"])):
            n = sum(1 for t in teams if t in wh.urls)
            counts["unchanged"] = counts.get("unchanged", 0) + n
            continue  # nothing can be posted or edited: do not even read the knowledge stores
        try:
            if phase == "final" and d.write:
                settle_pending(cfg, d.state, now, game_ids={g.game_id})
            card = build_card(d, g, phase, lg, age, day)
            shown = False
            for t in teams:
                url = wh.urls.get(t)
                if url is None:
                    continue
                embed = build_embed(card, t, style)
                what = deliver_one(
                    client, d.state, url, d.state.latest(day, g.game_id, t), slate=day,
                    game_id=g.game_id, team=t, phase=phase, embed=embed, now=now,
                    interval_min=float(s["pretip_min_edit_interval_min"]), write=d.write,
                )  # fmt: skip
                counts[what] = counts.get(what, 0) + 1
                shown = shown or what in ("posted", "edited", "reposted")
            if shown and phase == "pretip" and d.write:
                for o in card.overs:  # logged only once a card showed it, at the price shown
                    d.state.add_pick(F.pick_record(o, day, g))
        except Exception as exc:  # noqa: BLE001 - one card never costs the others or the tick
            # the traceback can quote a URL: it is shown only when debugging by hand, redacted
            log.warning("card for %s failed (%s)", g.matchup, type(exc).__name__)
            log.debug("card failure detail: %s", wh.redact(repr(exc)))
            errors.append(f"{g.matchup}:{type(exc).__name__}")


def _overview(
    rt: Any, d: Deps, slate: date, counts: dict[str, int], post_main: MainPoster | None
) -> None:
    """One short note in the main channel the first time cards go up for the date: how many cards
    and the best over, or the honest no-over sentence. Not an alert; posted once."""
    s = d.cfg.team_cards
    if post_main is None or not s.get("overview_post", True) or not d.write:
        return
    if counts.get("posted", 0) == 0 or rt.book.kind_sent("cards_overview", slate, str(slate)):
        return
    best: list[OverRow] = []
    for g in d.st.games:
        if g.tipoff is None or g.tipoff <= d.clock:
            continue
        best += F.pregame_overs(d.cfg, d.st, g.game_id, 1, "", d.kb)[0]
    best.sort(key=lambda r: (-r.edge_c, r.player))
    head = (
        "**JARVIS cards** are up for tonight: one per game in each team's channel "
        f"({counts['posted']} posted)."
    )
    body = T.NO_OVER
    if best:
        b = best[0]
        body = (
            f"Top over by EV after fees: {b.player} {T.line_label(b.line, b.stat)} "
            f"at {cents(b.ask)}, {T.ev_phrase(b)}."
        )
    post_main("cards_overview", [f"{head}\n{body}\n{T.DISCLAIMER}"], [str(slate)])
