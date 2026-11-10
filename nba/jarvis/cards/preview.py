"""Render tonight's cards from real data to local files (JSON and HTML) without sending anything.

``python -m nba.jarvis cards`` prints the embeds as redacted JSON; ``--preview`` also writes
``cards.json`` and ``cards.html`` under ``data/jarvis/card_preview/<date>/`` so the cards can be
looked at in a browser before any webhook exists. Nothing here posts, edits or writes the card
state; the paper-pick log is only read.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import run as R
from nba.jarvis.cards.embed import build_embed
from nba.jarvis.cards.recap import settle_pending
from nba.jarvis.cards.state import CardState
from nba.jarvis.cards.webhooks import redact_all
from nba.jarvis.live import feed_age_min, latest_scoreboard


def read_scorer_rows(cfg: Any, slate: date) -> list[dict[str, Any]]:
    """The halftime scorer's paper rows for a date, read-only (empty when the store is absent)."""
    ho = cfg.halftime_overs
    path = Path(str(ho.get("store", "")))
    path = path if path.is_absolute() else cfg.root / path
    if not ho.get("store") or not path.exists():
        return []
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            cur = con.execute("SELECT * FROM halftime_scored WHERE slate_date = ?", [slate])
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
    except duckdb.Error:
        return []


def state_at(rt: Any, slate: date, at: datetime) -> Any:
    """The slate state as of ``at`` (prices read up to that moment only), for a replay."""
    from nba.jarvis.slate import gather

    model = rt.model_factory(slate) if rt.model_factory else None
    return gather(rt.cfg, slate, at, model=model, use_polymarket=False)


def render_all(
    rt: Any,
    st: Any,
    slate: date,
    now: datetime,
    *,
    phase: str | None = None,
    game: str | None = None,
    pretip_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Cards for every game. ``pretip_at`` replays a finished night: the overs are priced as of that
    moment into a THROWAWAY log (never the real one), then the halftime / final cards and the
    recap are built from it, so all three phases of a real game can be seen. Knowledge (injuries,
    lineups) is read as it is now, not as of ``pretip_at``: a view, not a score."""
    cfg = rt.cfg
    style = R.style_of(cfg)
    tmp = tempfile.TemporaryDirectory() if pretip_at is not None else None
    try:
        state = R.state_of(cfg)
        if tmp is not None:
            state = CardState(Path(tmp.name) / "s.jsonl", Path(tmp.name) / "p.jsonl")
        d = R.Deps(cfg, st, rt.kb, state, now, write=False)
        d.scorer_rows = read_scorer_rows(cfg, slate)
        try:
            d.sent_alert_keys = (
                rt.book.sent_keys("alert", slate) if rt.book.path.exists() else set()
            )
        except Exception:  # noqa: BLE001 - the alert note is optional
            d.sent_alert_keys = set()
        d0 = None
        if pretip_at is not None:
            d0 = R.Deps(cfg, state_at(rt, slate, pretip_at), rt.kb, state, pretip_at, write=False)
        live = {(x.away, x.home): x for x in latest_scoreboard(cfg, slate, until=now)}
        age = feed_age_min(cfg, slate, now)
        out: list[dict[str, Any]] = []
        for g in st.games:
            if game and g.matchup != game:
                continue
            lg = live.get((g.away_abbr, g.home_abbr))
            ph = phase or R.phase_of(g, lg, now, 24 * 60.0)
            if ph is None:
                continue
            use = d
            if d0 is not None:
                shown = pretip_at.isoformat(timespec="seconds")  # type: ignore[union-attr]
                overs, _ = F.pregame_overs(
                    cfg, d0.st, g.game_id, int(cfg.team_cards["top_overs"]), shown, rt.kb
                )
                unders, _ = F.pregame_unders(
                    cfg, d0.st, g.game_id, int(cfg.team_cards["top_overs"]), shown, rt.kb
                )
                for o in F.merge_ranked(overs, unders, int(cfg.team_cards["top_overs"])):
                    state.add_pick(F.pick_record(o, slate, g))
                if ph == "final":
                    settle_pending(cfg, state, now, game_ids={g.game_id})
                if ph == "pretip":
                    use = d0
            card = R.build_card(use, g, ph, lg, age, slate)
            out.append(
                {
                    "game": g.matchup,
                    "phase": ph,
                    "channels": [g.away_abbr, g.home_abbr],
                    "embeds": {t: build_embed(card, t, style) for t in (g.away_abbr, g.home_abbr)},
                }
            )
        return out
    finally:
        if tmp is not None:
            tmp.cleanup()


def dumps(cards: list[dict[str, Any]], secrets: list[str] | None = None) -> str:
    return redact_all(json.dumps({"cards": cards}, indent=2, ensure_ascii=False), secrets or [])


def _md(s: str) -> str:
    """The small markdown subset the cards use -> HTML (bold, code, quote lines, newlines)."""
    out = []
    for ln in s.split("\n"):
        quote = ln.startswith("> ")
        e = html.escape(ln[2:] if quote else ln)
        e = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e)
        e = re.sub(r"`(.+?)`", r"<code>\1</code>", e)
        out.append(f'<span class="q">{e}</span>' if quote else e)
    return "<br>".join(out)


def to_html(cards: list[dict[str, Any]]) -> str:
    css = (
        "body{background:#313338;color:#dbdee1;font:15px -apple-system,Segoe UI,sans-serif;"
        "max-width:560px;margin:24px auto}h2{color:#fff;font-size:13px;margin:28px 0 6px}"
        ".card{background:#2b2d31;border-left:5px solid var(--c);border-radius:4px;"
        "padding:10px 14px;margin:6px 0 18px}"
        ".t{color:#fff;font-weight:700;font-size:16px;margin-bottom:4px}"
        ".n{color:#fff;font-weight:700;margin-top:10px}"
        ".q{color:#b5bac1;display:inline-block;margin-left:10px}"
        ".f{color:#949ba4;font-size:12px;margin-top:10px}code{font-size:13px}"
    )
    parts = [f"<!doctype html><meta charset=utf-8><title>JARVIS cards</title><style>{css}</style>"]
    for c in cards:
        for team, e in c["embeds"].items():
            col = f"#{int(e['color']):06x}"
            parts.append(
                f"<h2>{html.escape(c['game'])} · {c['phase']} · channel #{team.lower()}</h2>"
            )
            body = [
                f'<div class="t">{html.escape(e["title"])}</div><div>{_md(e["description"])}</div>'
            ]
            for f in e["fields"]:
                body.append(
                    f'<div class="n">{html.escape(f["name"])}</div><div>{_md(f["value"])}</div>'
                )
            body.append(f'<div class="f">{html.escape(e["footer"]["text"])}</div>')
            parts.append(f'<div class="card" style="--c:{col}">{"".join(body)}</div>')
    return "\n".join(parts)


def write_preview(cards: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, hp = out_dir / "cards.json", out_dir / "cards.html"
    jp.write_text(dumps(cards), encoding="utf-8")
    hp.write_text(to_html(cards), encoding="utf-8")
    return jp, hp
