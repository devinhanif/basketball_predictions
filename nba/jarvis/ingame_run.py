"""Compose and post the in-game signals (``ingame.py``) through the existing sinks.

Each signal posts once per player per game (once per game for the game-level ones); the key is
recorded in ``sends`` so a restart cannot repeat it. A named bet is label ``live_none``:
entertainment, EV unknown, the $1 ladder size, with what it pays if right. It is recorded in the
paper book (inside the nightly cap) but never scored against a pre-tip close: its entry is live.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from nba.jarvis import edge as E
from nba.jarvis import render as R
from nba.jarvis.ingame import (
    LiveMarket,
    PlayerLine,
    Signal,
    Snapshot,
    detect,
    kalshi_fallback,
    kalshi_live,
    load_snapshots,
    norm_name,
)
from nba.jarvis.models import Game, Play, PlayLeg
from nba.jarvis.runner import Runtime, _deliver, _given_up

LABEL = "live_none"
KINDS = ("benched", "window", "foul", "back", "returned")
STAT_WORD = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "threes"}


def _c(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}c"


def header(kind: str, s: Snapshot) -> str:
    lead = f"{s.leader} +{s.margin}" if s.margin else "level"
    return f"**JARVIS live: {kind}** {s.matchup} {s.when()}, {lead}"


def _markets_for(markets: list[LiveMarket], p: PlayerLine) -> list[LiveMarket]:
    n = norm_name(p.name)
    return sorted((m for m in markets if m.name == n), key=lambda m: (m.stat, m.threshold))


def _above(markets: list[LiveMarket], p: PlayerLine) -> list[LiveMarket]:
    """The player's YES markets whose line is above his current number (still to be won)."""
    return [m for m in _markets_for(markets, p) if m.threshold > p.stat(m.stat)]


def market_line(m: LiveMarket) -> str:
    return f"{m.threshold}+ {STAT_WORD[m.stat]}: bid {_c(m.yes_bid)} / ask {_c(m.yes_ask)}"


def ladder_line(markets: list[LiveMarket], p: PlayerLine, n: int = 3) -> str | None:
    """Prices as printed by the venue, no commentary: 'Kalshi pts ladder now: 20+ 50c/66c ...'."""
    ms = sorted(_above(markets, p), key=lambda m: (m.stat, m.threshold))[:n]
    if not ms:
        return None
    return "  Kalshi ladder now (bid/ask): " + "; ".join(
        f"{m.threshold}+ {STAT_WORD[m.stat]} {_c(m.yes_bid)}/{_c(m.yes_ask)}" for m in ms
    )


def _player_benched(sig: Signal, markets: list[LiveMarket]) -> list[str]:
    s, p = sig.snap, sig.players[0]
    frozen_min = int(float(sig.detail.get("frozen_s", 0.0)) // 60)
    lines = [
        f"{p.name} may be done for the night ({sig.detail['minutes']} min played, off the floor "
        f"for {frozen_min}+ min of game clock; margin {s.margin}). "
        f"He is at {p.pts} pts / {p.ast} ast / {p.reb} reb."
    ]
    ladder = ladder_line(markets, p)
    if ladder:
        lines.append(ladder)
    elif not markets:
        lines.append("  (no live Kalshi price was captured in the last few minutes)")
    return lines


def benched_lines(sigs: list[Signal], markets: list[LiveMarket]) -> list[str]:
    """One post for every starter newly flagged on this tick (each keeps its own dedupe key)."""
    lines = [header("BENCHED", sigs[0].snap)]
    for sg in sigs:
        lines += _player_benched(sg, markets)
    lines.append("Inference from minutes, not a certainty; no price is compared to fair here.")
    return lines


def returned_lines(sig: Signal) -> list[str]:
    p = sig.players[0]
    was = sig.detail.get("was")
    now = "on the floor" if p.active else f"MIN {was} -> {p.minutes}"
    when = sig.detail.get("when") or "earlier"
    return [
        header("CORRECTION", sig.snap),
        f"{p.name} is back ({now}); he was posted as 'may be done' at {when}.",
    ]


def candidate_bets(
    snap: Snapshot, markets: list[LiveMarket], frozen_ids: set[str]
) -> list[tuple[float, PlayerLine, LiveMarket]]:
    """Reserve players (non-starters, or starters not frozen... never the frozen ones) with a live
    over market above their number and a price worth a look (3c to 60c), most reachable first."""
    mins_left = max(snap.remaining_s / 60.0, 0.1)
    out = []
    for p in snap.players:
        if p.starter or p.espn_id in frozen_ids:
            continue
        for m in _above(markets, p):
            if m.yes_ask is None or not 0.03 <= m.yes_ask <= 0.60:
                continue
            out.append(((m.threshold - p.stat(m.stat)) / mins_left, p, m))
    ranked = sorted(out, key=lambda t: (not t[1].active, t[0], -(t[2].yes_ask or 0)))
    seen: set[str] = set()
    best: list[tuple[float, PlayerLine, LiveMarket]] = []
    for t in ranked:  # one line per player: his most reachable market
        if t[1].espn_id not in seen:
            seen.add(t[1].espn_id)
            best.append(t)
    return best


def window_lines(
    cfg: Any,
    sig: Signal,
    markets: list[LiveMarket],
    bet: Play | None,
    cands: list[tuple[float, PlayerLine, LiveMarket]],
) -> list[str]:
    s = sig.snap
    done = ", ".join(p.name for p in sig.players)
    mins = max(int(round(s.remaining_s / 60.0)), 0)
    lines = [
        header("BENCH WINDOW", s),
        f"Starters out ({done}); reserves are playing. About {mins} min left. Reserves with a live "
        "over (label none: entertainment, EV unknown):",
    ]
    for _need, p, m in cands[: int(cfg.ingame["max_window_lines"])]:
        where = "on the floor" if p.active else "on the bench"
        lines.append(
            f"- {p.name} ({p.team}, {where}): {p.stat(m.stat)} {STAT_WORD[m.stat]} now, "
            f"{m.threshold}+ ask {_c(m.yes_ask)} (needs {m.threshold - p.stat(m.stat)} more)"
        )
    if not cands:
        lines.append("- no reserve over market with a price between 3c and 60c was captured")
    if bet is not None:
        lines.append("\n".join(x for x in R.play_lines(bet, cfg.display_tz)[1:3]))
    lines.append("No price comparison is made here, so this is not an edge.")
    return lines


def foul_lines(sig: Signal, markets: list[LiveMarket]) -> list[str]:
    s, p = sig.snap, sig.players[0]
    pool = [x for x in s.players if x.team == p.team and not x.starter and x.minutes is not None]
    same = [x for x in pool if x.position == p.position and x.position] or pool
    backup = max(same, key=lambda x: x.minutes or 0) if same else None
    who = backup.name if backup else "his backup"
    lines = [
        header("FOUL TROUBLE", s),
        f"{p.name} has {p.pf} fouls ({p.minutes} min): his minutes may be cut, and {who} may play "
        "more minutes.",
    ]
    for pl in (p, backup):
        if pl is None:
            continue
        ladder = ladder_line(markets, pl, 2)
        if ladder:
            lines.append(f"{pl.name}:{ladder.strip().removeprefix('Kalshi ladder now (bid/ask):')}")
    lines.append("Foul counts are a fact about the game; no price is compared to fair here.")
    return lines


def back_lines(sig: Signal) -> list[str]:
    s = sig.snap
    names = ", ".join(p.name for p in sig.players)
    m, sec = divmod(int(s.remaining_s), 60)
    return [
        header("STARTERS BACK?", s),
        f"The lead is down to {s.margin} with {m}:{sec:02d} left: {names} (flagged earlier) may "
        "return to the floor.",
    ]


def _bet(
    rt: Runtime,
    game: Game | None,
    snap: Snapshot,
    cand: tuple[float, PlayerLine, LiveMarket],
    room: float,
) -> Play | None:
    _need, p, m = cand
    if game is None or m.yes_ask is None:
        return None
    cfg = rt.cfg
    series = m.series or m.ticker.split("-")[0]
    n = E.contracts_for(
        cfg, "kalshi", m.yes_ask, cfg.ladder["none"], room, series, cfg.none_max_contracts
    )
    if n < 1:
        return None  # a $0 play is never recorded
    cost = E.order_cost(cfg, "kalshi", m.yes_ask, n, series)
    leg = PlayLeg(
        "kalshi", m.ticker, "yes", m.yes_ask, "prop",
        f"{p.name}: {m.threshold}+ {STAT_WORD[m.stat]}",
        stat=m.stat, threshold=float(m.threshold), series=series, quote_ts=m.ts,
    )  # fmt: skip
    return Play(
        game.game_id, game.matchup, game.tipoff, (leg,), LABEL, float("nan"), None,
        E.fee_per_contract_raw(cfg, "kalshi", m.yes_ask, series) * 100.0, m.yes_ask, n,
        round(cost, 2), "live reserve over: entertainment, EV unknown",
        notes=("live price from the capture; not compared to fair",),
        inputs={"leg_costs": [round(cost, 4)], "espn_game": snap.espn_game},
    )  # fmt: skip


def replay(
    cfg: Any, slate: date, start: datetime, end: datetime, *, game: str | None = None,
    step_min: int = 2, scratch: Any = None,
) -> list[tuple[str, dict[str, Any]]]:  # fmt: skip
    """Walk the clock over a finished game's captured files and return the posts that WOULD have
    fired, as (HH:MM UTC, result). Uses a throwaway paper book and a recording sender: nothing is
    sent and nothing real is written."""
    import tempfile
    from datetime import timedelta
    from pathlib import Path

    from nba.jarvis.book import PaperBook
    from nba.jarvis.imessage import SendResult
    from nba.jarvis.knowledge import Knowledge
    from nba.jarvis.sources import load_games

    tmp = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="jarvis_replay_"))
    rt = Runtime(
        cfg=cfg, book=PaperBook(tmp / "replay_book.duckdb"), kb=Knowledge(cfg), dry_run=False,
        sender=lambda text, **kw: SendResult(True, "sent"),
    )  # fmt: skip
    games = load_games(cfg, slate)
    out: list[tuple[str, dict[str, Any]]] = []
    t = start
    while t <= end:
        rt.clock = lambda t=t: t  # type: ignore[misc]
        for r in run_ingame(rt, slate, games):
            if game is None or game in r["texts_out"][0]:
                out.append((t.strftime("%H:%M"), r))
        t += timedelta(minutes=step_min)
    return out


def name_mismatches(snap: Snapshot, markets: list[LiveMarket]) -> list[str]:
    """Kalshi names for this game that match no ESPN player (a spelling difference between the
    venues): their prices are never attached to anyone, so they are counted and reported."""
    have = {norm_name(p.name) for p in snap.players}
    return sorted({m.display for m in markets if m.name not in have})


def _flags(rt: Runtime, slate: date) -> dict[str, tuple[int, str, datetime]]:
    out = dict(rt.book.ingame_flags(slate))
    out.update(rt.ingame_flags)
    return out


def _one_game(
    rt: Runtime, slate: date, hist: list[Snapshot], games: list[Game], now: datetime
) -> list[dict[str, Any]]:
    cfg = rt.cfg
    seen = rt.ingame_seen
    cur = hist[-1]
    already = {k: set(seen[k]) | rt.book.sent_keys(f"ingame_{k}", slate) for k in KINDS}
    flags = _flags(rt, slate)
    signals = detect(cfg, hist, already, flags)
    if not signals:
        return []
    markets = kalshi_live(
        cfg.root, slate, cur.away, cur.home, now, float(cfg.ingame["kalshi_max_age_min"])
    ) or kalshi_fallback(cfg, slate, cur.away, cur.home, now)
    miss = name_mismatches(cur, markets)
    game = next((g for g in games if (g.away_abbr, g.home_abbr) == (cur.away, cur.home)), None)
    real, other = rt.book.spent_split(slate)
    room = max(
        0.0,
        min(cfg.nightly_cap - real - other, cfg.nightly_cap - cfg.reserve_for_strong - other),
    )
    frozen_ids = {p.espn_id for sg in signals if sg.kind == "window" for p in sg.players}
    benched = [sg for sg in signals if sg.kind == "benched"]
    todo: list[tuple[str, list[Signal]]] = []
    if benched:
        todo.append(("benched", benched))
    todo += [(sg.kind, [sg]) for sg in signals if sg.kind != "benched"]
    results: list[dict[str, Any]] = []
    for kind_name, group in todo:
        kind = f"ingame_{kind_name}"
        keys = [sg.key for sg in group]
        if not rt.dry_run and any(_given_up(rt, kind, slate, k) for k in keys):
            continue
        sig = group[0]
        bet = None
        if kind_name == "benched":
            lines = benched_lines(group, markets)
        elif kind_name == "window":
            cands = candidate_bets(sig.snap, markets, frozen_ids)
            bet = _bet(rt, game, sig.snap, cands[0], room) if cands else None
            lines = window_lines(cfg, sig, markets, bet, cands)
        elif kind_name == "foul":
            lines = foul_lines(sig, markets)
        elif kind_name == "returned":
            lines = returned_lines(sig)
        else:
            lines = back_lines(sig)
        if miss and kind_name in ("benched", "window"):
            lines.append(
                f"({len(miss)} Kalshi name(s) matched no ESPN player: {', '.join(miss[:3])})"
            )
        texts = R.chunk(lines, cfg.max_chars, cfg.max_texts)
        res = _deliver(
            rt, kind=kind, texts=texts, plays=[bet] if bet is not None else [], slate=slate,
            inputs=[keys, sig.snap.when()], explanations={}, keys=keys,
            record_source="ingame" if bet is not None else None,
        )  # fmt: skip
        if res["mode"] != "failed":  # a failed post is retried on the next tick, not marked seen
            for k in keys:
                seen[kind_name].add(k)
            if kind_name == "benched":
                for sg in group:
                    rt.ingame_flags[sg.key] = (
                        int(sg.detail["minutes"]),
                        sg.snap.when(),
                        sg.snap.ts,
                    )
                    rt.book.note_flag(
                        sg.key, slate, int(sg.detail["minutes"]), sg.snap.when(), rt.dry_run,
                        sg.snap.ts,
                    )  # fmt: skip
        res["signal"] = kind_name
        res["key"] = ",".join(keys)
        res["at"] = now.isoformat(timespec="seconds")
        res["texts_out"] = texts
        res["name_mismatches"] = miss
        results.append(res)
    return results


def run_ingame(rt: Runtime, slate: date, games: list[Game]) -> list[dict[str, Any]]:
    """One pass over every live game's recent snapshots; posts each new signal once. A game that
    raises is reported and skipped, never silencing the others."""
    cfg = rt.cfg
    if not cfg.ingame.get("enabled", True):
        return []
    now = rt.clock()
    snaps = load_snapshots(cfg.root, slate, now, float(cfg.ingame["lookback_min"]))
    for k in KINDS:
        rt.ingame_seen.setdefault(k, set())
    results: list[dict[str, Any]] = []
    for hist in snaps.values():
        cur = hist[-1]
        if (now - cur.ts).total_seconds() > float(cfg.ingame["snapshot_max_age_min"]) * 60.0:
            continue  # the feed for this game is stale: say nothing rather than quote an old score
        try:
            results += _one_game(rt, slate, hist, games, now)
        except Exception as exc:
            results.append(
                {"ok": False, "signal": "error", "error": type(exc).__name__, "game": cur.matchup}
            )
    return results
