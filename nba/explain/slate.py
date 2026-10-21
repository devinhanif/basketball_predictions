"""The plain-language slate briefing: one Markdown page per ET slate date, from stored rows.

``python -m nba.explain slate --date 2026-10-21 --db nba.duckdb \
--out reports/briefing/<date>_<edition>.md --edition early|report``

Read-only: the database is opened with ``read_only=True`` through ``connect_with_retry`` (the
bounded lock wait the daily jobs use). The page says what the daily job stored before tip
(``made_at <= tip - 60 min``, the cutoff the settle step and the game explainer use). Every number
on it is a model forecast, labelled as one; a forecast is not a claim, so it carries no interval.

Team membership comes from the official roster cache the forecast reads,
``data/rosters/<slate date>/<team_id>.parquet`` (read only, never fetched). Where a team's cache is
missing, the player's latest box score in the current season stands in, and the page says so.
The holdout season is never read.

Players the official report lists OUT are not forecast on the page: the live path drops them from
later runs, so an old row would otherwise look current. A game's shown set is its latest usable row
per player, minus players OUT on the report that run used.

Exit codes: 0 page written; 4 no forecast rows for the date (a short page is written); 1 the
database is missing or unreadable, or the page could not be built. On 1 nothing is written: the
previous page stays in place and the reason goes to stderr. A page is only replaced on 0 or 4.
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

from nba.daily.season import season_int_for_date, season_str
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
    "(2025-26 replay: Brier, a probability error where lower is better, 0.0064 worse; "
    "CI +0.0023 to +0.0104; n 1,311 games in 207 date clusters; exploratory, and that season "
    "is the holdout already spent). Player props are where the system might know something "
    "the market does not; that is unproven on the live log."
)
ONE_SECOND = timedelta(seconds=1)


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
    report_missing: bool = False  # a report time is stored but no lines were found for the game


@dataclass(frozen=True)
class RosterRead:
    membership: dict[int, set[int]]  # player -> teams on a readable cached roster for the date
    cached: set[int]  # team ids whose roster file was read
    names: dict[int, str]  # player -> roster display name
    cache_time: datetime | None  # earliest file time among the cached rosters (CDT)
    unreadable: list[int]  # team ids whose file exists but could not be read


@dataclass
class Placer:
    """Which of a game's two teams a player belongs to, and how we know."""

    membership: dict[int, set[int]]
    cached: set[int]
    latest: dict[int, int]  # player -> latest current-season box-score team (fallback only)
    names: dict[int, str]

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

    def roster_team(self, pid: int) -> str:
        teams = self.membership.get(pid)
        return team_abbr(next(iter(teams))) if teams and len(teams) == 1 else "team not known"


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
    out_players: list[tuple[str, int, ReportLine]] = field(default_factory=list)  # OUT, no forecast


@dataclass
class Changes:
    compared_at: datetime | None  # naive UTC: the earliest first run stored among the games
    single_run: bool
    n_games: int = 0
    n_players: int = 0
    no_baseline: int = 0  # games with no stored run before the early baseline
    game_moves: list[str] = field(default_factory=list)
    player_moves: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


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
    """A stored report time (naive ET) in any ISO-like form; None when it does not parse."""
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).strip())
    except ValueError:
        return None


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


def _likely(p_play: float | None) -> str:
    return f"plays {round(p_play * 100)}%" if p_play is not None else "P(play) not stored"


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


def first_usable_by_game(con: Any, game_ids: list[str]) -> dict[str, datetime]:
    """Each game's earliest stored run made before its own T-60 cutoff: its baseline."""
    if not game_ids:
        return {}
    rows = con.execute(
        f"SELECT game_id, min(made_at) FROM forward_predictions "
        f"WHERE game_id IN ({_marks(len(game_ids))}) AND model_name IN ({_marks(len(MODELS))}) "
        f"AND made_at <= tipoff - {D.LEAD_MINUTES} * INTERVAL 1 MINUTE GROUP BY game_id",
        [*game_ids, *MODELS],
    ).fetchall()
    return {str(g): m for g, m in rows if m is not None}


def withdrawn_keys(con: Any) -> set[tuple[str, str, str, int]]:
    """Forecast keys withdrawn because the official roster places the player elsewhere."""
    if not _has_table(con, "forward_withdrawals"):
        return set()
    rows = con.execute(
        "SELECT DISTINCT game_id, model_name, target, player_id FROM forward_withdrawals"
    ).fetchall()
    return {(str(g), str(m), str(t), int(p)) for g, m, t, p in rows}


def latest_teams(con: Any, pids: set[int], slate: date) -> dict[int, int]:
    """Each player's latest team on a box score before the slate date, in the slate's own season.

    The holdout season is excluded, and an earlier season is never used: last season's team is not
    this season's team, so a player who has not yet played this season is left unplaced."""
    if not pids:
        return {}
    season = season_int_for_date(slate)
    assert season != HOLDOUT_SEASON, "the holdout season is never read for team lookups"
    ids = sorted(pids)
    rows = con.execute(
        f"SELECT p.player_id, arg_max(p.team_id, g.game_date) FROM player_game_stats p "
        f"JOIN games g USING (game_id) WHERE p.player_id IN ({_marks(len(ids))}) "
        f"AND g.game_date < ? AND g.season = ? GROUP BY p.player_id",
        [*ids, slate, season],
    ).fetchall()
    return {int(p): int(t) for p, t in rows if t is not None}


def read_rosters(slate: date, team_ids: set[int], root: Path) -> RosterRead:
    """Membership from the cached official rosters for ``slate`` (read only; never fetched).

    A cache file that exists but cannot be read is reported and treated as absent."""
    membership: dict[int, set[int]] = {}
    names: dict[int, str] = {}
    cached: set[int] = set()
    unreadable: list[int] = []
    times: list[datetime] = []
    for tid in sorted(team_ids):
        path = roster_cache_path(root, slate, tid)
        if not path.exists():
            continue
        try:
            df = pl.read_parquet(path)
            ids = df["player_id"].to_list()
            labels = (
                df["player_name"].to_list() if "player_name" in df.columns else [None] * len(ids)
            )
        except Exception:
            unreadable.append(tid)
            continue
        cached.add(tid)
        times.append(datetime.fromtimestamp(path.stat().st_mtime, CDT))
        for pid, label in zip(ids, labels, strict=True):
            if pid is None:
                continue
            membership.setdefault(int(pid), set()).add(tid)
            if label:
                names.setdefault(int(pid), str(label))
    return RosterRead(membership, cached, names, min(times) if times else None, unreadable)


def report_at(con: Any, game_id: str, as_of: datetime) -> tuple[dict[int, ReportLine], bool]:
    """The report snapshot the model used, for this game. Matched within one second, so a
    format difference in the stored stamp does not silently drop it. Returns (lines, missing)."""
    if not _has_table(con, "player_availability"):
        return {}, True
    rows = con.execute(
        "SELECT player_id, status, reason FROM player_availability "
        "WHERE game_id = ? AND as_of BETWEEN ? AND ? ORDER BY player_id",
        [game_id, as_of - ONE_SECOND, as_of + ONE_SECOND],
    ).fetchall()
    lines = {int(p): ReportLine(int(p), str(s).lower(), r) for p, s, r in rows}
    return lines, not rows


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
        raw = inj.pred.get("report_as_of_et")
        rep_at = _parse_et(raw)
    else:
        assert mov is not None
        p_home = float(mov.pred["p_home"])
        p_mov = p_home
        fallback = "no injury-adjusted forecast was stored for this game"
        raw, rep_at = None, None
    report, missing = report_at(con, game_id, rep_at) if rep_at is not None else ({}, False)
    if raw and rep_at is None:
        missing = True  # a report time is stored but it does not parse
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
        report=report,
        report_missing=missing,
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
    """The game's shown forecasts: latest row per player, minus players OUT on the report used.

    Top points per team (mean if he plays, ranked by that mean), and the single largest rebounds and
    assists projection. An OUT player gets no projection; he is listed as OUT instead."""
    view = GameView(game_id=game_id, tip=tip, win=win)
    if win is None:
        return view
    sides = {win.home, win.away}
    by_team: dict[int, list[PropView]] = {}
    best: dict[str, PropView] = {}
    left: set[int] = set()
    out_seen: dict[int, tuple[str, int, ReportLine]] = {}
    for r in game_rows:
        if r.model != PRIMARY or r.target not in STAT_WORDS or r.player_id <= 0:
            continue
        if "mean" not in r.pred or (r.game_id, r.model, r.target, r.player_id) in withdrawn:
            continue
        side = placer.side(r.player_id, sides)
        if side is None:
            left.add(r.player_id)
            continue
        line = win.report.get(r.player_id)
        if line is not None and line.status == "out":
            if r.target == "pts":
                out_seen[r.player_id] = (placer.name(r.player_id), side, line)
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
    view.out_players = sorted(out_seen.values(), key=lambda t: (t[1], t[0]))
    return view


# ------------------------------------------------------------------------------- changes


def _changes(
    game_ids: list[str],
    wins_cur: dict[str, Win | None],
    wins_base: dict[str, Win | None],
    cur: dict[tuple[str, str, str, int], Row],
    base: dict[tuple[str, str, str, int], Row],
    placer: Placer,
    withdrawn: set[tuple[str, str, str, int]],
    first_by_game: dict[str, datetime],
    latest_by_game: dict[str, datetime],
) -> Changes:
    compared = min(first_by_game.values()) if first_by_game else None
    single = all(first_by_game[g] >= latest_by_game.get(g, first_by_game[g]) for g in first_by_game)
    ch = Changes(compared_at=compared, single_run=single)
    for gid in game_ids:
        cw, bw = wins_cur.get(gid), wins_base.get(gid)
        if cw is None or not cw.has_injury_row:
            continue
        if bw is None or not bw.has_injury_row:
            ch.no_baseline += 1
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
        cw, bw = wins_cur.get(gid), wins_base.get(gid)
        if cw is None or key in withdrawn:
            continue
        side = placer.side(pid, {cw.home, cw.away})
        b = base.get(key)
        if side is None or b is None:
            continue  # not on either roster, or not in the baseline: not compared
        ch.n_players += 1
        team = team_abbr(side)
        cur_line = cw.report.get(pid)
        was_out = bw is not None and pid in bw.report and bw.report[pid].status == "out"
        now_out = cur_line is not None and cur_line.status == "out"
        if cur_line is not None and now_out and not was_out:
            ch.player_moves.append(
                f"{placer.name(pid)} ({team}): now OUT on the {cw.report_at:%H:%M} ET report "
                f"({_reason(cur_line.reason)}); his last stored forecast was "
                f"{float(c.pred['mean']):.1f} points, {_likely(_f(c.pred.get('p_play')))}."
            )
            continue
        if now_out:
            continue
        dm = float(c.pred["mean"]) - float(b.pred["mean"])
        bp, cp = _f(b.pred.get("p_play")), _f(c.pred.get("p_play"))
        crossed = bp is not None and cp is not None and (bp >= PLAY_CUT) != (cp >= PLAY_CUT)
        if abs(dm) < MOVE_PROJECTION and not crossed:
            continue
        parts: list[str] = []
        if abs(dm) >= MOVE_PROJECTION:
            parts.append(
                f"points projection {float(b.pred['mean']):.1f} to {float(c.pred['mean']):.1f} "
                f"({_signed(dm)})"
            )
        if crossed and bp is not None and cp is not None:
            parts.append(f"P(play) from {bp:.0%} to {cp:.0%}")
        why = (
            f"On the report: {cur_line.status}, {_reason(cur_line.reason)}."
            if cur_line is not None and cur_line.status != "available"
            else "No reason on the report."
        )
        ch.player_moves.append(f"{placer.name(pid)} ({team}): {'; '.join(parts)}. {why}")
    for key, b in base.items():
        gid, model, target, pid = key
        if gid not in game_ids or model != PRIMARY or target != "pts" or key not in withdrawn:
            continue
        ch.dropped.append(
            f"{placer.name(pid)} ({placer.roster_team(pid)}): no longer forecast (withdrawn: the "
            f"official roster places him on another team); his early forecast was "
            f"{float(b.pred['mean']):.1f} points."
        )
    return ch


def _who(ln: ReportLine, side: int, placer: Placer) -> str:
    return f"{placer.name(ln.player_id)} ({team_abbr(side)}, {_reason(ln.reason)})"


# ------------------------------------------------------------------------------- build


def _roster_note(slate: date, team_ids: set[int], rr: RosterRead) -> str:
    season = season_str(season_int_for_date(slate))
    m = len(team_ids)
    when = rr.cache_time.strftime("%Y-%m-%d %H:%M") if rr.cache_time else "unknown time"
    if m and team_ids <= rr.cached:
        text = (
            f"Team membership: official roster cache for {slate.isoformat()}, written {when} CDT "
            f"(all {m} teams on this slate)."
        )
    elif rr.cached:
        text = (
            f"Team membership: official roster cache for {slate.isoformat()} on file for "
            f"{len(rr.cached)} of {m} teams on this slate (earliest file {when} CDT); the rest "
            "from "
            f"each player's latest box score in the {season} season."
        )
    else:
        text = (
            f"Team membership: no official roster cache for {slate.isoformat()}; each player's "
            f"latest box score in the {season} season is used (players with no game yet this "
            "season are left out)."
        )
    if rr.unreadable:
        ids = ", ".join(str(t) for t in rr.unreadable)
        text += f" Unreadable cache file(s) for team {ids} were treated as absent."
    return text


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
    rr = read_rosters(
        slate, team_ids, roster_root if roster_root is not None else DEFAULT_ROSTER_DIR
    )
    pids = {pid for (_, m, _, pid) in cur if m == PRIMARY and pid > 0}
    pids |= {ln.player_id for w in wins_cur.values() if w is not None for ln in w.report.values()}
    fallback_pids = {p for p in pids if p not in rr.membership}
    placer = Placer(
        membership=rr.membership,
        cached=rr.cached,
        latest=latest_teams(con, fallback_pids, slate) if fallback_pids else {},
        names=rr.names,
    )
    withdrawn = withdrawn_keys(con)
    by_game: dict[str, list[Row]] = {}
    latest_by_game: dict[str, datetime] = {}
    for r in cur.values():
        by_game.setdefault(r.game_id, []).append(r)
        latest_by_game[r.game_id] = max(latest_by_game.get(r.game_id, r.made_at), r.made_at)
    views = [
        game_view(g, tip, wins_cur[g], by_game.get(g, []), placer, withdrawn) for g, tip in games
    ]
    out = Briefing(
        slate=slate,
        edition=edition,
        written=written,
        has_rows=True,
        games=views,
        placer=placer,
        newest_run=max(latest_by_game.values(), default=None),
        roster_note=_roster_note(slate, team_ids, rr),
    )
    if edition == "report":
        first_by_game = first_usable_by_game(con, gids)
        base: dict[tuple[str, str, str, int], Row] = {}
        for g, stamp in first_by_game.items():
            base.update(latest_rows(con, [g], stamp))
        wins_base = {g: win_from_rows(con, g, base) for g in gids}
        out.changes = _changes(
            gids, wins_cur, wins_base, cur, base, placer, withdrawn, first_by_game, latest_by_game
        )
    return out


# ---------------------------------------------------------------------------------- render


def _header(b: Briefing) -> list[str]:
    stamp = b.written.strftime("%Y-%m-%d %H:%M")
    day = b.slate.strftime("%A %Y-%m-%d")
    used = [g.win.report_at for g in b.games if g.win and g.win.report_at is not None]
    if b.edition == "early":
        if used:
            ed = (
                f"**Early edition**, written {stamp} CDT. Injury report used: the "
                f"{max(used):%H:%M} ET report; later reports can move these numbers."
            )
        else:
            ed = (
                f"**Early edition**, written {stamp} CDT. No official injury report was used for "
                "these games yet; numbers move after about 13:00 ET."
            )
    elif used:
        ed = (
            f"**Report edition**, written {stamp} CDT after the {max(used):%H:%M} ET injury report."
        )
    else:
        ed = (
            f"**Report edition**, written {stamp} CDT; no official injury report was usable for "
            "these games yet, so no absences are counted."
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
        elif w.report_missing:
            absent = (
                f"No report lines were found for this game at {w.report_at:%H:%M} ET, so no "
                "absences are counted."
            )
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
    return f"- {v.name}: {v.mean:.1f} {word} ({rec}; {mins}; {_likely(v.p_play)})"


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
    out += ["", "Player forecasts (model averages if he plays):"]
    for tid in (w.away, w.home):
        out.append(f"**{team_name(tid)}**")
        top = g.team_top.get(tid, [])
        if not top:
            out.append("- No points forecasts stored for this team.")
        out.extend(_prop_line(v) for v in top)
    if g.out_players:
        listed = "; ".join(
            f"{name} ({team_abbr(side)}, {_reason(line.reason)})"
            for name, side, line in g.out_players
        )
        out.append(f"OUT on the report, no forecast shown: {listed}.")
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
    if c.single_run:
        at = (
            c.compared_at.replace(tzinfo=D.UTC).astimezone(CDT).strftime("%Y-%m-%d %H:%M")
            if c.compared_at
            else "unknown"
        )
        return title + [
            f"Only one forecast run is stored for these games so far (first at {at} CDT), so there "
            "is nothing to compare yet.",
            "",
        ]
    at = (
        c.compared_at.replace(tzinfo=D.UTC).astimezone(CDT).strftime("%Y-%m-%d %H:%M")
        if c.compared_at
        else "unknown"
    )
    out = title + [
        f"Compared with the first forecast stored for each game (earliest {at} CDT). "
        f"Games compared: "
        f"{c.n_games}; games with no earlier run: {c.no_baseline}; player projections compared: "
        f"{c.n_players}. Thresholds: home win 3 points, points projection 2, P(play) crossing 0.5 "
        "either way.",
        "",
    ]
    if not (c.game_moves or c.player_moves or c.dropped):
        return out + ["Nothing moved past those thresholds.", ""]
    out.append(
        f"Games: {len(c.game_moves)} moved."
        if c.game_moves
        else "Games: none moved 3 points or more."
    )
    out += [f"- {m}" for m in c.game_moves]
    out.append("")
    players = c.player_moves + c.dropped
    out.append(f"Players: {len(players)} changed." if players else "Players: none moved.")
    out += [f"- {m}" for m in players]
    return out + [""]


def render(b: Briefing) -> str:
    lines = _header(b)
    if not b.has_rows or b.placer is None:
        lines += [
            f"No forecast rows are stored for the {b.slate.isoformat()} slate yet (checked "
            f"{b.written.strftime('%Y-%m-%d %H:%M')} CDT). Nothing to brief yet.",
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
    """Write one briefing page and return the exit code.

    0: page written. 4: no forecast rows for the date (a short page is written). 1: the database is
    missing or unreadable (after the bounded lock wait), or the page could not be built. On 1
    nothing is written, the previous page stays in place, and the reason goes to stderr."""
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}")
    path, target = Path(db), Path(out)
    if not path.exists():
        print(
            f"slate: database {path} not found; the previous page at {target} is left in place",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    target.parent.mkdir(parents=True, exist_ok=True)
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
    except Exception as exc:  # any failure: nothing is written, the previous page stays
        print(
            f"slate: could not build the page from {path} ({type(exc).__name__}: {exc}); "
            f"the previous page at {target} is left in place",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    finally:
        con.close()
    _publish(target, text)
    return EXIT_OK if briefing.has_rows else EXIT_NO_ROWS
