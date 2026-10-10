"""Assemble the page model from stored rows. No fitting, no writing, no hidden state."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from nba.explain import data as D
from nba.explain.knowledge import WATCH_LIST, Taught, choose
from nba.explain.known import Known, load_known
from nba.explain.names import player_name, team_abbr

BIG_PROJ_MINUTES = 15.0  # "best/worst call" is judged among players projected for >= 15 minutes


@dataclass
class StatCell:
    stat: str
    median: float
    lo: float  # q10
    hi: float  # q90
    mean: float
    actual: int | None  # None = did not play
    status: str | None  # inside | above | below | None (dnp)
    line: D.MarketLine | None
    p_over_ours: float | None
    rps_ours: float | None
    rps_base: float | None
    pit: tuple[float, float] | None
    base_mean: float | None


@dataclass
class PlayerRow:
    player_id: int
    name: str
    team_id: int
    proj_minutes: float | None
    p_play: float | None
    played: bool
    minutes: float | None
    starter: bool
    bucket: str | None
    routed_to: str | None
    cells: dict[str, StatCell] = field(default_factory=dict)


@dataclass
class WinInfo:
    model: str
    made_at_utc: datetime
    p_home: float
    p_mov_elo: float | None
    fallback_reason: str | None
    market: D.MarketWin | None


@dataclass
class ScoreRow:
    stat: str
    n: int
    ours: float
    base: float
    diff: float
    lo: float
    hi: float
    n_unscorable: int


@dataclass
class Call:
    """A named player-stat call used for the paired best/worst line."""

    name: str
    stat: str
    median: float
    lo: float
    hi: float
    actual: int
    status: str


@dataclass
class Page:
    game: D.Game
    source_name: str
    has_forecasts: bool
    late_only_rows: int
    win: WinInfo | None
    report: D.Report
    report_names: dict[int, str]
    rows: list[PlayerRow]
    dnp_rows: list[PlayerRow]
    unforecast_played: list[tuple[str, int, float]]  # (name, team_id, minutes)
    scoring: list[ScoreRow]
    movers: list[tuple[str, float, float]]  # (name, ours mean, recency mean)
    best: Call | None
    worst: Call | None
    taught: Taught
    starter_minutes: dict[str, float | None]
    pts_cover: tuple[int, int, int, int]  # n played, inside, above, below (points)
    known: Known | None = None  # None = the caller did not ask for the fact-store section


def _num(x: Any) -> float | None:
    return None if x is None else float(x)


def _win_info(stored: list[D.Stored], market: D.MarketWin | None) -> WinInfo | None:
    wins = {s.model: s for s in stored if s.target == "win_prob_home"}
    s = wins.get("rung0_injury_elo") or wins.get("rung0_mov_elo")
    if s is None:
        return None
    return WinInfo(
        model=s.model,
        made_at_utc=s.made_at,
        p_home=float(s.pred["p_home"]),
        p_mov_elo=_num(s.pred.get("p_mov_elo")),
        fallback_reason=s.pred.get("fallback_reason"),
        market=market,
    )


def build_page(
    con: Any,
    odds: Any | None,
    game_id: str,
    *,
    source_name: str = "",
    facts_db: str | Path | None = None,
    with_known: bool = False,
) -> Page | None:
    game = D.load_game(con, game_id)
    if game is None:
        return None
    stored = D.load_forecasts(con, game_id)
    box = D.load_box(con, game_id)
    has_fc = bool(stored)
    market_win = D.load_market_win(odds, game_id) if odds is not None else None
    lines = D.load_market_lines(odds, game_id) if odds is not None else {}
    mkt_names = D.load_market_names(odds, game_id) if odds is not None else {}
    win = _win_info(stored, market_win)

    primary: dict[tuple[str, int], D.Stored] = {}
    base: dict[tuple[str, int], D.Stored] = {}
    for s in stored:
        if s.model == D.PRIMARY_PROPS:
            primary[(s.target, s.player_id)] = s
        elif s.model == D.BASELINE_PROPS:
            base[(s.target, s.player_id)] = s

    pids = sorted({pid for (_, pid) in primary})
    rows: list[PlayerRow] = []
    dnp_rows: list[PlayerRow] = []
    rps_pairs: dict[str, list[tuple[float, float]]] = {st: [] for st in D.STATS}
    unscorable: dict[str, int] = {st: 0 for st in D.STATS}
    for pid in pids:
        pts_pred = primary.get(("pts", pid))
        any_pred = pts_pred or next(primary[(st, pid)] for st in D.STATS if (st, pid) in primary)
        bx = box.get(pid)
        team = bx.team_id if bx else _guess_team(con, pid, game)
        played = bool(bx and bx.played)
        row = PlayerRow(
            player_id=pid,
            name=player_name(pid, mkt_names),
            team_id=team,
            proj_minutes=_num(any_pred.pred.get("proj_minutes")),
            p_play=_num(any_pred.pred.get("p_play")),
            played=played,
            minutes=bx.minutes if bx else None,
            starter=bool(bx and bx.starter),
            bucket=any_pred.pred.get("bucket"),
            routed_to=any_pred.pred.get("routed_to"),
        )
        for st in D.STATS:
            p = primary.get((st, pid))
            if p is None:
                continue
            pr = p.pred
            lo, hi = float(pr["q10"]), float(pr["q90"])
            surv = D.survival(pr.get("p_ge_full"))
            actual = bx.stats.get(st) if (bx and played) else None
            status = D.interval_status(actual, lo, hi) if actual is not None else None
            ml = lines.get((pid, st))
            b = base.get((st, pid))
            bsurv = D.survival(b.pred.get("p_ge_full")) if b else None
            rps_o = rps_b = None
            pit = None
            if actual is not None:
                if D.is_complete(surv) and D.is_complete(bsurv):
                    assert surv is not None and bsurv is not None
                    rps_o, rps_b = D.crps_int(surv, actual), D.crps_int(bsurv, actual)
                    rps_pairs[st].append((rps_o, rps_b))
                else:
                    unscorable[st] += 1
                if D.is_complete(surv):
                    assert surv is not None
                    pit = D.pit_interval(surv, actual)
            row.cells[st] = StatCell(
                stat=st,
                median=float(pr["q50"]),
                lo=lo,
                hi=hi,
                mean=float(pr["mean"]),
                actual=actual,
                status=status,
                line=ml,
                p_over_ours=D.p_over(surv, ml.line) if ml else None,
                rps_ours=rps_o,
                rps_base=rps_b,
                pit=pit,
                base_mean=_num(b.pred.get("mean")) if b else None,
            )
        (rows if played else dnp_rows).append(row)

    key = lambda r: -(r.proj_minutes if r.proj_minutes is not None else -1.0)  # noqa: E731
    rows.sort(key=key)
    dnp_rows.sort(key=key)

    scoring: list[ScoreRow] = []
    for st in D.STATS:
        pairs = rps_pairs[st]
        if not pairs:
            continue
        o = np.asarray([a for a, _ in pairs])
        bb = np.asarray([b for _, b in pairs])
        m, lo, hi = D.bootstrap_mean_ci(o - bb)
        scoring.append(
            ScoreRow(st, len(pairs), float(o.mean()), float(bb.mean()), m, lo, hi, unscorable[st])
        )

    forecast_ids = set(pids)
    unforecast = sorted(
        (
            (player_name(b.player_id, mkt_names), b.team_id, float(b.minutes or 0.0))
            for b in box.values()
            if b.played and b.player_id not in forecast_ids
        ),
        key=lambda t: -t[2],
    )

    # movers: biggest context-model-minus-recency gaps in points among players who were forecast
    movers = sorted(
        (
            (r.name, c.mean, c.base_mean)
            for r in rows + dnp_rows
            if (c := r.cells.get("pts")) is not None and c.base_mean is not None
        ),
        key=lambda t: -abs(t[1] - t[2]),
    )[:3]

    best, worst = _best_worst(rows)
    n_pts = [r.cells["pts"] for r in rows if "pts" in r.cells and r.cells["pts"].status]
    cover = (
        len(n_pts),
        sum(c.status == "inside" for c in n_pts),
        sum(c.status == "above" for c in n_pts),
        sum(c.status == "below" for c in n_pts),
    )

    starter_minutes: dict[str, float | None] = {}
    for tid in (game.home_team, game.away_team):
        ms = [b.minutes for b in box.values() if b.team_id == tid and b.starter and b.minutes]
        starter_minutes[team_abbr(tid)] = float(statistics.fmean(ms)) if ms else None
    played_ids = {r.player_id for r in rows}
    bias = {pid: D.player_points_bias(con, pid) for pid in WATCH_LIST if pid in played_ids}
    taught = choose(
        margin=game.margin,
        forecast_players=played_ids,
        bias=bias,
        starter_minutes=starter_minutes,
        names={pid: player_name(pid) for pid in WATCH_LIST},
    )

    report = D.load_report(con, game_id, game.tip_utc, game.game_date)
    report_names = {ln.player_id: player_name(ln.player_id, mkt_names) for ln in report.lines}
    return Page(
        game=game,
        source_name=source_name,
        has_forecasts=has_fc,
        late_only_rows=D.count_late_only(con, game_id),
        win=win,
        report=report,
        report_names=report_names,
        rows=rows,
        dnp_rows=dnp_rows,
        unforecast_played=unforecast,
        scoring=scoring,
        movers=movers,
        best=best,
        worst=worst,
        taught=taught,
        starter_minutes=starter_minutes,
        pts_cover=cover,
        known=(
            load_known(facts_db, con, game_id, game.home_team, game.away_team)
            if with_known
            else None
        ),
    )


def _guess_team(con: Any, pid: int, game: D.Game) -> int:
    """Team of a forecast player who did not play: the team on his latest earlier box score."""
    r = con.execute(
        "SELECT arg_max(p.team_id, g.game_date) FROM player_game_stats p "
        "JOIN games g USING (game_id) "
        "WHERE p.player_id = ? AND g.game_date <= ?",
        [pid, game.game_date],
    ).fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def _best_worst(rows: list[PlayerRow]) -> tuple[Call | None, Call | None]:
    """Best and worst points call among players projected for >= 15 minutes who played.

    Score = |actual - median| / half-width of the 80% interval (so a star and a role player are
    judged against their own stated uncertainty). Rule printed on the page."""
    cands: list[tuple[float, Call]] = []
    for r in rows:
        c = r.cells.get("pts")
        if c is None or c.actual is None or c.status is None:
            continue
        if (r.proj_minutes or 0.0) < BIG_PROJ_MINUTES:
            continue
        half = max((c.hi - c.lo) / 2.0, 0.5)
        z = abs(c.actual - c.median) / half
        cands.append((z, Call(r.name, "pts", c.median, c.lo, c.hi, c.actual, c.status)))
    if not cands:
        return None, None
    cands.sort(key=lambda t: t[0])
    return cands[0][1], cands[-1][1]
