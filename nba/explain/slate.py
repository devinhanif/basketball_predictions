"""The plain-language slate briefing: one Markdown page per ET slate date, from stored rows.

``python -m nba.explain slate --date 2026-10-21 --db nba.duckdb \
--out reports/briefing/<date>_<edition>.md --edition early|report``

Read-only: the database is opened with ``read_only=True`` (:func:`nba.explain.data.open_ro`) and
nothing is refit. The page says what the daily job stored before tip (``made_at <= tip - 60 min``,
the cutoff the settle step and the game explainer use). Every number on it is a model forecast,
labelled as one; a forecast is not a claim, so it carries no interval on the page. The one accuracy
figure on the page is a published replay number with its own interval and source.

Team membership comes from the official roster the forecast used: the per-date cache
``data/rosters/<slate date>/<team_id>.parquet`` (read only, never fetched). Where that cache is
missing, the latest current-season box score stands in, and the page says so. Season 2025 (the
holdout) is never read.

Exit codes: 0 page written; 4 no forecast rows for the date (a short page says so); 1 the database
exists but could not be read (a page says so). A missing optional source never fails the run.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.db.connect import connect_with_retry
from nba.explain import data as D
from nba.explain.names import player_name, team_abbr, team_name
from nba.props.rosters import DEFAULT_ROSTER_DIR, roster_cache_path

CDT = ZoneInfo("America/Chicago")
EXIT_OK = 0
EXIT_NO_ROWS = 4
EXIT_UNREADABLE = 1
EDITIONS = ("early", "report")
TOP_N = 3
MOVE_HOME_WIN = 0.03  # 3 points of home-win probability
MOVE_PROJECTION = 2.0  # points of projected scoring
PLAY_CUT = 0.5
#: Season 2025 is the frozen holdout (non-negotiable 2). Team lookups never read it.
HOLDOUT_SEASON = 2025
INJ = D.WIN_MODELS[0]  # rung0_injury_elo, primary
MOV = D.WIN_MODELS[1]  # rung0_mov_elo, comparison
PRIMARY = D.PRIMARY_PROPS
MODELS = (*D.WIN_MODELS, D.PRIMARY_PROPS, D.BASELINE_PROPS)
STAT_WORDS = {"pts": "points", "reb": "rebounds", "ast": "assists"}
FOOTER = "Generated automatically each night; not individually reviewed."
#: The one accuracy figure on the page. Source: docs/prereg/WINNER_RECAL.md line 10 (paired model
#: minus market Brier on the 2025-26 replay, n 1,311 games in 207 date clusters). That season is the
#: holdout already spent, so the figure is exploratory and the sentence says so.
SENTENCE = (
    "These are forecasts, not advice. The market beats this system on game winners "
    "(2025-26 replay, Brier 0.0064 worse, CI +0.0023 to +0.0104, n 1,311 games in 207 date "
    "clusters; exploratory, and that season is the holdout already spent). Player props are the "
    "only plausible place to beat the market, and that is unproven on the live log."
)


@dataclass(frozen=True)
class Row:
    """One stored prediction row: the latest for its key at or before the cutoff."""

    game_id: str
    model: str
    target: str
    player_id: int
    made_at: datetime  # naive UTC
    tipoff: datetime  # naive UTC
    pred: dict[str, Any]


@dataclass(frozen=True)
class ReportLine:
    player_id: int
    status: str  # lower-case, as the official report words it
    reason: str | None


@dataclass
class Win:
    game_id: str
    home: int
    away: int
    tip: datetime  # naive UTC
    p_home: float
    p_mov: float | None  # team strength alone (rung0_mov_elo)
    has_injury_row: bool
    fallback: str | None  # why the injury-adjusted forecast fell back, when it did
    report_at: datetime | None  # naive ET stamp of the snapshot the model used
    report: dict[int, ReportLine] = field(default_factory=dict)


@dataclass
class Placer:
    """Which of a game's two teams a player belongs to, and how we know."""

    membership: dict[int, set[int]]  # player -> teams on a cached official roster for the date
    cached: set[int]  # team ids whose roster file exists for the date
    latest: dict[int, int]  # player -> latest current-season box-score team (fallback)
    names: dict[int, str]  # player -> roster display name (fallback before "Player <id>")

    def side(self, pid: int, sides: set[int]) -> int | None:
        roster = self.membership.get(pid)
        if roster is not None:
            hit = roster & sides
            return next(iter(hit)) if len(hit) == 1 else None
        if sides <= self.cached:
            return None  # both rosters are on file and he is on neither
        team = self.latest.get(pid)
        return team if team in sides else None

    def name(self, pid: int) -> str:
        return player_name(pid, self.names)


@dataclass(frozen=True)
class PropView:
    player_id: int
    name: str
    stat: str
    mean: float
    mean_recency: float | None
    proj_minutes: float | None
    p_play: float | None


@dataclass
class GameView:
    game_id: str
    tip: datetime
    win: Win | None
    team_top: dict[int, list[PropView]] = field(default_factory=dict)  # points, top N per team
    best: dict[str, PropView] = field(default_factory=dict)  # reb, ast: the single largest
    left_out: int = 0  # players on neither team's roster


@dataclass
class Changes:
    compared_at: datetime  # naive UTC: the first run stored for the slate (the early baseline)
    single_run: bool
    n_games: int
    n_players: int
    game_moves: list[str] = field(default_factory=list)
    player_moves: list[str] = field(default_factory=list)


@dataclass
class Briefing:
    slate: date
    edition: str
    written: datetime  # aware CDT
    has_rows: bool
    games: list[GameView] = field(default_factory=list)
    placer: Placer | None = None
    changes: Changes | None = None
    newest_run: datetime | None = None  # naive UTC, newest usable row used
    roster_note: str = ""


# ----------------------------------------------------------------------------------- helpers


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def _parse_et(x: Any) -> datetime | None:
    return datetime.fromisoformat(str(x)) if x else None


def _has_table(con: Any, name: str) -> bool:
    r = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(r and r[0])


def _marks(n: int) -> str:
    return ",".join("?" for _ in range(n))


def _cdt(ts_utc: datetime) -> str:
    return ts_utc.replace(tzinfo=D.UTC).astimezone(CDT).strftime("%H:%M")


def _pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def _signed(x: float) -> str:
    s = f"{x:+.1f}"
    return "+0.0" if s == "-0.0" else s


def _reason(text: str | None) -> str:
    """Plain words for an official-report reason, e.g. 'Injury/Illness-RightKnee;Sprain'."""
    if not text:
        return "no reason given"
    s = text.replace("Injury/Illness-", "").replace(";", ", ")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s).lower()
    return s.replace("gleague", "G League").replace("-", " ")


# --------------------------------------------------------------------------------- loaders


def slate_games(con: Any, slate: date) -> list[tuple[str, datetime]]:
    """(game_id, tip naive UTC) for games with a stored row whose tip falls on the ET date.

    The query window covers EDT and EST; the ET date of each tip decides membership."""
    lo = datetime.combine(slate, time(4))
    hi = datetime.combine(slate + timedelta(days=1), time(6))
    rows = con.execute(
        f"SELECT game_id, max(tipoff) FROM forward_predictions "
        f"WHERE tipoff >= ? AND tipoff < ? AND model_name IN ({_marks(len(MODELS))}) "
        f"GROUP BY game_id",
        [lo, hi, *MODELS],
    ).fetchall()
    games = [(str(g), t) for g, t in rows if t is not None and D.utc_to_et(t).date() == slate]
    return sorted(games, key=lambda g: (g[1], g[0]))


def latest_rows(
    con: Any, game_ids: list[str], stamp_max: datetime | None
) -> dict[tuple[str, str, str, int], Row]:
    """Latest row per (game, model, target, player) made at or before its own T-60 cutoff and,
    when given, at or before ``stamp_max`` (the early-edition baseline)."""
    if not game_ids:
        return {}
    cap = stamp_max if stamp_max is not None else datetime(9999, 12, 31)
    rows = con.execute(
        f"""
        WITH e AS (
          SELECT game_id, model_name, target, player_id, made_at, tipoff, prediction,
                 row_number() OVER (PARTITION BY game_id, model_name, target, player_id
                                    ORDER BY made_at DESC, run_id DESC) AS rn
          FROM forward_predictions
          WHERE game_id IN ({_marks(len(game_ids))}) AND model_name IN ({_marks(len(MODELS))})
            AND made_at <= tipoff - {D.LEAD_MINUTES} * INTERVAL 1 MINUTE
            AND made_at <= ?
        )
        SELECT game_id, model_name, target, player_id, made_at, tipoff, prediction
        FROM e WHERE rn = 1
        """,
        [*game_ids, *MODELS, cap],
    ).fetchall()
    out: dict[tuple[str, str, str, int], Row] = {}
    for g, m, t, p, made, tip, pred in rows:
        out[(str(g), str(m), str(t), int(p))] = Row(
            str(g), str(m), str(t), int(p), made, tip, json.loads(pred)
        )
    return out


def first_usable_stamp(con: Any, game_ids: list[str]) -> datetime | None:
    """Earliest stored run made before the T-60 cutoff for these games: the early baseline."""
    if not game_ids:
        return None
    r = con.execute(
        f"SELECT min(made_at) FROM forward_predictions WHERE game_id IN ({_marks(len(game_ids))}) "
        f"AND model_name IN ({_marks(len(MODELS))}) "
        f"AND made_at <= tipoff - {D.LEAD_MINUTES} * INTERVAL 1 MINUTE",
        [*game_ids, *MODELS],
    ).fetchone()
    return r[0] if r and r[0] is not None else None


def withdrawn_keys(con: Any) -> set[tuple[str, str, str, int]]:
    """Forecast keys withdrawn because the official roster places the player elsewhere."""
    if not _has_table(con, "forward_withdrawals"):
        return set()
    rows = con.execute(
        "SELECT DISTINCT game_id, model_name, target, player_id FROM forward_withdrawals"
    ).fetchall()
    return {(str(g), str(m), str(t), int(p)) for g, m, t, p in rows}


def latest_teams(con: Any, pids: set[int], slate: date) -> dict[int, int]:
    """Each player's latest team on a box score before the slate date, current season only.

    Season ``HOLDOUT_SEASON`` is excluded in the query: its box scores are never read here."""
    if not pids:
        return {}
    ids = sorted(pids)
    rows = con.execute(
        f"SELECT p.player_id, arg_max(p.team_id, g.game_date) FROM player_game_stats p "
        f"JOIN games g USING (game_id) WHERE p.player_id IN ({_marks(len(ids))}) "
        f"AND g.game_date < ? AND g.season <> ? GROUP BY p.player_id",
        [*ids, slate, HOLDOUT_SEASON],
    ).fetchall()
    return {int(p): int(t) for p, t in rows if t is not None}


def read_rosters(
    slate: date, team_ids: set[int], root: Path
) -> tuple[dict[int, set[int]], set[int], dict[int, str]]:
    """Membership from the cached official rosters for ``slate`` (read only; never fetched)."""
    membership: dict[int, set[int]] = {}
    names: dict[int, str] = {}
    cached: set[int] = set()
    for tid in sorted(team_ids):
        path = roster_cache_path(root, slate, tid)
        if not path.exists():
            continue
        df = pl.read_parquet(path)
        cached.add(tid)
        ids = df["player_id"].to_list()
        labels = df["player_name"].to_list() if "player_name" in df.columns else [None] * len(ids)
        for pid, label in zip(ids, labels, strict=True):
            if pid is None:
                continue
            membership.setdefault(int(pid), set()).add(tid)
            if label:
                names.setdefault(int(pid), str(label))
    return membership, cached, names


def report_at(con: Any, game_id: str, as_of: datetime) -> dict[int, ReportLine]:
    """Every line of the report snapshot ``as_of`` for this game (the one the model used)."""
    if not _has_table(con, "player_availability"):
        return {}
    rows = con.execute(
        "SELECT player_id, status, reason FROM player_availability "
        "WHERE game_id = ? AND as_of = ? ORDER BY player_id",
        [game_id, as_of],
    ).fetchall()
    return {int(p): ReportLine(int(p), str(s).lower(), r) for p, s, r in rows}


# ----------------------------------------------------------------------------------- views


def win_from_rows(con: Any, game_id: str, rows: dict[tuple[str, str, str, int], Row]) -> Win | None:
    """Home win forecast for a game: the injury-adjusted row, else team strength alone."""
    inj = rows.get((game_id, INJ, "win_prob_home", -1))
    mov = rows.get((game_id, MOV, "win_prob_home", -1))
    src = inj or mov
    if src is None or "home_team" not in src.pred or "away_team" not in src.pred:
        return None
    home, away = int(src.pred["home_team"]), int(src.pred["away_team"])
    if inj is not None:
        p_home = float(inj.pred["p_home"])
        p_mov = _f(inj.pred.get("p_mov_elo"))
        if p_mov is None and mov is not None:
            p_mov = float(mov.pred["p_home"])
        fallback = inj.pred.get("fallback_reason") or None
        rep_at = _parse_et(inj.pred.get("report_as_of_et"))
    else:
        assert mov is not None
        p_home = float(mov.pred["p_home"])
        p_mov = p_home
        fallback = "no injury-adjusted forecast was stored for this game"
        rep_at = None
    return Win(
        game_id=game_id,
        home=home,
        away=away,
        tip=src.tipoff,
        p_home=p_home,
        p_mov=p_mov,
        has_injury_row=inj is not None,
        fallback=str(fallback) if fallback else None,
        report_at=rep_at,
        report=report_at(con, game_id, rep_at) if rep_at is not None else {},
    )


def _outs_on(w: Win, placer: Placer) -> tuple[list[tuple[ReportLine, int]], int]:
    """OUT lines tied to one of the game's two teams (with the team), and how many were not."""
    sides = {w.home, w.away}
    named: list[tuple[ReportLine, int]] = []
    other = 0
    for ln in sorted(w.report.values(), key=lambda r: r.player_id):
        if ln.status != "out":
            continue
        side = placer.side(ln.player_id, sides)
        if side is None:
            other += 1
        else:
            named.append((ln, side))
    return named, other


def game_view(
    game_id: str,
    tip: datetime,
    win: Win | None,
    game_rows: list[Row],
    placer: Placer,
    withdrawn: set[tuple[str, str, str, int]],
) -> GameView:
    """Points per team (top N), and the single largest rebounds and assists projection."""
    view = GameView(game_id=game_id, tip=tip, win=win)
    if win is None:
        return view
    sides = {win.home, win.away}
    by_team: dict[int, list[PropView]] = {}
    best: dict[str, PropView] = {}
    left: set[int] = set()
    for r in game_rows:
        if r.model != PRIMARY or r.target not in STAT_WORDS or r.player_id <= 0:
            continue
        if (r.game_id, r.model, r.target, r.player_id) in withdrawn:
            continue
        side = placer.side(r.player_id, sides)
        if side is None:
            left.add(r.player_id)
            continue
        pr = r.pred
        pv = PropView(
            player_id=r.player_id,
            name=placer.name(r.player_id),
            stat=r.target,
            mean=float(pr["mean"]),
            mean_recency=_f(pr.get("mean_recency")),
            proj_minutes=_f(pr.get("proj_minutes")),
            p_play=_f(pr.get("p_play")),
        )
        if r.target == "pts":
            by_team.setdefault(side, []).append(pv)
        elif r.target not in best or pv.mean > best[r.target].mean:
            best[r.target] = pv
    view.team_top = {t: sorted(v, key=lambda p: -p.mean)[:TOP_N] for t, v in by_team.items()}
    view.best = best
    view.left_out = len(left)
    return view


# ------------------------------------------------------------------------------- changes


def _changes(
    game_ids: list[str],
    wins_cur: dict[str, Win | None],
    wins_base: dict[str, Win | None],
    cur: dict[tuple[str, str, str, int], Row],
    base: dict[tuple[str, str, str, int], Row],
    placer: Placer,
    first: datetime,
    latest: datetime | None,
) -> Changes:
    ch = Changes(
        compared_at=first,
        single_run=latest is None or first >= latest,
        n_games=0,
        n_players=0,
    )
    for gid in game_ids:
        cw, bw = wins_cur.get(gid), wins_base.get(gid)
        if cw is None or bw is None or not cw.has_injury_row or not bw.has_injury_row:
            continue
        ch.n_games += 1
        d = cw.p_home - bw.p_home
        if abs(d) >= MOVE_HOME_WIN - 1e-9:
            before = {ln.player_id for ln, _ in _outs_on(bw, placer)[0]}
            new_out = [(ln, s) for ln, s in _outs_on(cw, placer)[0] if ln.player_id not in before]
            why = (
                "New OUT on the report: "
                + "; ".join(_who(ln, s, placer) for ln, s in new_out)
                + "."
                if new_out
                else "No new OUT player on the report."
            )
            ch.game_moves.append(
                f"{team_abbr(cw.away)} @ {team_abbr(cw.home)}: home win from {_pct(bw.p_home)} "
                f"to {_pct(cw.p_home)} ({_signed(d * 100)} points). {why}"
            )
    for key, c in cur.items():
        gid, model, target, pid = key
        if gid not in game_ids or model != PRIMARY or target != "pts" or pid <= 0:
            continue
        b = base.get(key)
        if b is None:
            continue
        ch.n_players += 1
        dm = float(c.pred["mean"]) - float(b.pred["mean"])
        bp, cp = _f(b.pred.get("p_play")), _f(c.pred.get("p_play"))
        crossed = bp is not None and cp is not None and bp >= PLAY_CUT > cp
        if abs(dm) < MOVE_PROJECTION and not crossed:
            continue
        cw = wins_cur.get(gid)
        line = cw.report.get(pid) if cw is not None else None
        why = (
            f"On the report: {line.status}, {_reason(line.reason)}."
            if line is not None and line.status != "available"
            else "No reason on the report."
        )
        side = placer.side(pid, {cw.home, cw.away}) if cw is not None else None
        team = team_abbr(side) if side is not None else "team not known"
        parts: list[str] = []
        if abs(dm) >= MOVE_PROJECTION:
            parts.append(
                f"points projection {float(b.pred['mean']):.1f} to {float(c.pred['mean']):.1f} "
                f"({_signed(dm)})"
            )
        if crossed and bp is not None and cp is not None:
            parts.append(f"P(play) from {bp:.0%} to {cp:.0%}")
        ch.player_moves.append(f"{placer.name(pid)} ({team}): {'; '.join(parts)}. {why}")
    return ch


# ------------------------------------------------------------------------------- build


def build_briefing(
    con: Any,
    slate: date,
    edition: str,
    now: datetime,
    roster_root: Path | None = None,
) -> Briefing:
    """Assemble the page model from stored rows. Nothing is fitted or fetched."""
    written = now.astimezone(CDT) if now.tzinfo else now.replace(tzinfo=CDT)
    games = slate_games(con, slate)
    if not games:
        return Briefing(slate, edition, written, has_rows=False)
    gids = [g for g, _ in games]
    cur = latest_rows(con, gids, None)
    wins_cur = {g: win_from_rows(con, g, cur) for g in gids}
    team_ids = {t for w in wins_cur.values() if w is not None for t in (w.home, w.away)}
    membership, cached, roster_names = read_rosters(
        slate, team_ids, roster_root if roster_root is not None else DEFAULT_ROSTER_DIR
    )
    pids = {pid for (_, m, _, pid) in cur if m == PRIMARY and pid > 0}
    pids |= {ln.player_id for w in wins_cur.values() if w is not None for ln in w.report.values()}
    fallback_pids = {p for p in pids if p not in membership}
    placer = Placer(
        membership=membership,
        cached=cached,
        latest=latest_teams(con, fallback_pids, slate) if fallback_pids else {},
        names=roster_names,
    )
    if team_ids and team_ids <= cached:
        note = (
            f"Team membership: official rosters cached for {slate.isoformat()} (the files the "
            "forecast reads)."
        )
    elif cached:
        note = (
            f"Team membership: official rosters on file for {len(cached)} of {len(team_ids)} "
            "teams on this slate; the rest from each player's latest current-season box score."
        )
    else:
        note = (
            f"Team membership: no official roster on file for {slate.isoformat()}; each player's "
            "latest current-season box score is used (players with no game yet are left out)."
        )
    withdrawn = withdrawn_keys(con)
    by_game: dict[str, list[Row]] = {}
    for r in cur.values():
        by_game.setdefault(r.game_id, []).append(r)
    views = [
        game_view(g, tip, wins_cur[g], by_game.get(g, []), placer, withdrawn) for g, tip in games
    ]
    latest = max((r.made_at for r in cur.values()), default=None)
    out = Briefing(
        slate=slate,
        edition=edition,
        written=written,
        has_rows=True,
        games=views,
        placer=placer,
        newest_run=latest,
        roster_note=note,
    )
    if edition == "report":
        first = first_usable_stamp(con, gids)
        if first is not None:
            base = latest_rows(con, gids, first)
            wins_base = {g: win_from_rows(con, g, base) for g in gids}
            out.changes = _changes(gids, wins_cur, wins_base, cur, base, placer, first, latest)
    return out


# ---------------------------------------------------------------------------------- render


def _who(ln: ReportLine, side: int, placer: Placer) -> str:
    return f"{placer.name(ln.player_id)} ({team_abbr(side)}, {_reason(ln.reason)})"


def _header(b: Briefing) -> list[str]:
    stamp = b.written.strftime("%H:%M")
    day = b.slate.strftime("%A %Y-%m-%d")
    if b.edition == "early":
        ed = (
            f"**Early edition**, written {stamp} CDT before the first official injury report "
            "(next report about 13:00 ET): numbers move."
        )
    else:
        used = [g.win.report_at for g in b.games if g.win and g.win.report_at is not None]
        if used:
            ed = (
                f"**Report edition**, written {stamp} CDT after the "
                f"{max(used).strftime('%H:%M')} ET injury report."
            )
        else:
            ed = (
                f"**Report edition**, written {stamp} CDT; no official injury report was usable "
                "for these games yet, so no absences are counted."
            )
    return [f"# Slate briefing, {day} (ET slate)", "", ed, "", FOOTER, "", SENTENCE, ""]


def _win_lines(w: Win, placer: Placer) -> list[str]:
    ha = team_abbr(w.home)
    fav_home = w.p_home >= 0.5
    fav_team = w.home if fav_home else w.away
    fav_p = w.p_home if fav_home else 1 - w.p_home
    fav = f"Favourite: {team_name(fav_team)}, {'at home' if fav_home else 'away'} ({_pct(fav_p)})."
    if w.fallback:
        first = (
            f"Home ({ha}) wins {_pct(w.p_home)} (model, team strength only). The forecast fell "
            f"back: {w.fallback}."
        )
    else:
        mov = w.p_mov if w.p_mov is not None else w.p_home
        delta = (w.p_home - mov) * 100
        d_txt = _signed(delta) if abs(delta) >= 0.05 else "0.0"
        if w.report_at is None:
            absent = "No absences on the report yet."
        else:
            named, other = _outs_on(w, placer)
            at = w.report_at.strftime("%H:%M")
            if named:
                absent = (
                    f"OUT on the {at} ET report: "
                    + "; ".join(_who(ln, s, placer) for ln, s in named)
                    + "."
                )
            else:
                absent = f"Report {at} ET: nobody from either roster listed OUT."
            if other:
                noun = "entry" if other == 1 else "entries"
                absent += f" {other} more OUT {noun} on the report not placed on either team."
        first = (
            f"Home ({ha}) wins {_pct(w.p_home)} (model), {_pct(mov)} from team strength alone, "
            f"{d_txt} points from the injury report. {absent}"
        )
    return [first, fav]


def _prop_line(v: PropView) -> str:
    word = STAT_WORDS[v.stat]
    rec = (
        f"recency average {v.mean_recency:.1f}, model {_signed(v.mean - v.mean_recency)}"
        if v.mean_recency is not None
        else "recency average not stored"
    )
    mins = (
        f"about {round(v.proj_minutes)} minutes"
        if v.proj_minutes is not None
        else "minutes not stored"
    )
    play = f"plays {round(v.p_play * 100)}%" if v.p_play is not None else "P(play) not stored"
    return f"- {v.name}: {v.mean:.1f} {word} ({rec}; {mins}; {play})"


def _game_md(g: GameView, placer: Placer) -> list[str]:
    if g.win is None:
        return [
            f"### Game {g.game_id}, tip {_cdt(g.tip)} CDT",
            "",
            "No win forecast was stored for this game before tip minus 60 minutes, so no player "
            "forecasts are shown for it.",
            "",
        ]
    w = g.win
    out = [f"### {team_abbr(w.away)} @ {team_abbr(w.home)}, tip {_cdt(w.tip)} CDT", ""]
    out += _win_lines(w, placer)
    out += ["", "Player forecasts (model means, not book lines):"]
    for tid in (w.away, w.home):
        out.append(f"**{team_name(tid)}**")
        top = g.team_top.get(tid, [])
        if not top:
            out.append("- No points forecasts stored for this team.")
        out.extend(_prop_line(v) for v in top)
    for stat in ("reb", "ast"):
        v = g.best.get(stat)
        if v is not None:
            out.append(f"Most {STAT_WORDS[stat]}: {_prop_line(v)[2:]}")
    if g.left_out:
        out.append(f"{g.left_out} player forecast(s) not shown: on neither team's roster.")
    return out + [""]


def _changes_md(b: Briefing) -> list[str]:
    if b.edition != "report":
        return []
    title = ["## What changed since the early edition", ""]
    c = b.changes
    if c is None:
        return title + [
            "No earlier forecast run is stored for these games, so nothing to compare.",
            "",
        ]
    at = c.compared_at.replace(tzinfo=D.UTC).astimezone(CDT).strftime("%H:%M")
    if c.single_run:
        return title + [
            f"Only one forecast run is stored for these games so far (at {at} CDT), so there is "
            "nothing to compare yet.",
            "",
        ]
    out = title + [
        f"Compared with the first forecast stored for this slate ({at} CDT). Games compared: "
        f"{c.n_games}; player projections compared: {c.n_players}. Thresholds: home win 3 points, "
        "points projection 2, P(play) crossing 0.5.",
        "",
    ]
    if not c.game_moves and not c.player_moves:
        return out + ["Nothing moved past those thresholds.", ""]
    out.append(
        f"Games: {len(c.game_moves)} moved."
        if c.game_moves
        else "Games: none moved 3 points or more."
    )
    out += [f"- {m}" for m in c.game_moves]
    out.append("")
    out.append(
        f"Players: {len(c.player_moves)} moved." if c.player_moves else "Players: none moved."
    )
    out += [f"- {m}" for m in c.player_moves]
    return out + [""]


def render(b: Briefing) -> str:
    lines = _header(b)
    if not b.has_rows or b.placer is None:
        lines += [
            f"No forecast rows are stored for the {b.slate.isoformat()} slate yet (checked at "
            f"{b.written.strftime('%H:%M')} CDT). Nothing to brief yet.",
            "",
        ]
        return "\n".join(lines)
    if b.newest_run is not None:
        lines += [
            f"Newest forecast run used: {_cdt(b.newest_run)} CDT (each game uses the latest run "
            "made before its tip minus 60 minutes).",
            "",
        ]
    if b.roster_note:
        lines += [b.roster_note, ""]
    lines += ["## Games, in tip order (CDT)", ""]
    for g in b.games:
        lines += _game_md(g, b.placer)
    lines += _changes_md(b)
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------------ entry point


def _no_rows_page(slate: date, edition: str, now: datetime, note: str | None = None) -> str:
    text = render(Briefing(slate, edition, now.astimezone(CDT), has_rows=False))
    return text + (f"\nNote: {note}.\n" if note else "")


def _publish(target: Path, text: str) -> None:
    """Write the page to a temp file, then ``os.replace`` it over the target: a reader sees the
    previous page or the new one, never a partial one."""
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, target)


def write_slate(
    db: str | Path,
    slate: date,
    edition: str,
    out: str | Path,
    now: datetime,
    roster_root: Path | None = None,
) -> int:
    """Write one briefing page; return the exit code.

    0: page written. 4: no forecast rows for the date, a short page is written. 1: the database
    could not be read (after the bounded lock wait) or the rows could not be read: nothing is
    written, the previous page stays in place, and the reason goes to stderr. The page is only ever
    replaced on 0 or 4, and always atomically."""
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}")
    path, target = Path(db), Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _publish(target, _no_rows_page(slate, edition, now, f"{path} not found"))
        return EXIT_NO_ROWS
    try:
        con = connect_with_retry(path, read_only=True)
    except duckdb.Error as exc:
        print(
            f"slate: {path} could not be opened read-only after the lock wait ({exc}); "
            f"the previous page at {target} is left in place",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    try:
        if _has_table(con, "forward_predictions"):
            briefing = build_briefing(con, slate, edition, now, roster_root)
        else:
            briefing = Briefing(slate, edition, now.astimezone(CDT), has_rows=False)
        text = render(briefing)
    except duckdb.Error as exc:
        print(
            f"slate: reading the forecast rows from {path} failed ({exc}); "
            f"the previous page at {target} is left in place",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    finally:
        con.close()
    _publish(target, text)
    return EXIT_OK if briefing.has_rows else EXIT_NO_ROWS
