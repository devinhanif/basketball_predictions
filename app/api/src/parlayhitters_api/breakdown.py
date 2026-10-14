"""The leg breakdown: everything a bettor checks on one prop, as of before tip.

Rules this module keeps:
- As-of: only games strictly before the slate date feed any number. A game on the slate date
  (the one being bet) and every later game are in the fixture on purpose, and are excluded here.
- Every number is a count, a mean or a rank of stored box-score rows, or the stored forecast
  read at the line. Nothing is invented; a combo's descriptive numbers are the sum of its parts
  for each game, and a combo has no model chance (single-stat forecasts are correlated, so
  adding or multiplying them would be a made-up number).
- Small samples are flagged (n < SMALL_N) and printed with their n; a hit rate carries a Wilson
  95% interval. A streak is never presented as an edge.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from .dissect.types import Dissection
from .forecast import forecast_probs, reference_line
from .history import (
    MODEL_STATS,
    POSITION_NOUN,
    STAT_LABEL,
    History,
    Row,
    days_between,
)
from .role_stats import OnOff, Per36, Steadiness, onoff, per36, steadiness

Json = dict[str, Any]
Side = Literal["over", "under"]
State = Literal["hit", "miss", "push"]

SMALL_N = 10  # fewer prior games than this is a small sample (also in role_stats)
LAST_N = 10
RECENT_N = 5
HEADS_UP_GAP = 0.25  # recent hit rate and model chance this far apart is worth a heads-up
ROTATION_MIN = 15.0  # a teammate counts as a rotation player at this many average minutes
ROSTER_WINDOW = (
    20  # team games either side in which a teammate must have played to be "on the team"
)
MAX_TEAMMATES = 4
REST_GAP_MAX = 10  # a longer gap between team games is an offseason, not rest
AVAIL_LEAD = timedelta(minutes=60)  # reports count if published at least an hour before tip
SHOWN_AVAIL = ("out", "doubtful", "questionable")


# ----------------------------------------------------------------------------- response shapes


class Tally(BaseModel):
    n: int
    hits: int
    misses: int
    pushes: int
    rate: float | None
    ci_lo: float | None
    ci_hi: float | None
    avg: float | None
    avg_minutes: float | None
    small: bool


class Chip(BaseModel):
    game_id: str
    date: str
    opp: str
    home: bool | None  # None when the export holds no schedule row for that older game
    value: int
    minutes: float
    starter: bool
    state: State


class Split(BaseModel):
    key: str
    label: str
    tonight: bool
    this_season: Tally
    since_last_season: Tally


class TeammateOut(BaseModel):
    player_id: int
    name: str
    status: str
    reason: str | None
    avg_minutes: float
    without: Tally
    with_: Tally
    # His minutes and per-36 points, rebounds and assists (assists from the box score) in the
    # same two sets of games, whatever the stat on screen.
    per36_without: Per36
    per36_with: Per36
    note: str


class Teammates(BaseModel):
    report_as_of_utc: str | None
    player_status: str | None
    player_status_reason: str | None
    items: list[TeammateOut]
    omitted_unnamed: int
    note: str


class OppAllowed(BaseModel):
    opp: str
    position: str
    position_noun: str
    avg_allowed: float
    rank: int
    of: int
    n_games: int
    league_avg: float
    note: str


class Model(BaseModel):
    available: bool
    p_hit: float | None
    p_over: float | None
    p_under: float | None
    p_push: float | None
    mean: float | None
    typical: float | None
    extrapolated: bool
    stamp: str | None
    say: str
    reason: str


class Role(BaseModel):
    n: int
    avg_minutes: float | None
    starts: int
    minutes_forecast: float | None
    p_play: float | None
    say: str


class BookRow(BaseModel):
    """One book's stored quote at the line on screen. A price that is not usable (a placeholder
    with no bid behind it) is never given as a number: it is None and `*_unusable` is set."""

    book: str
    over_price: int | None
    under_price: int | None
    over_unusable: bool
    under_unusable: bool
    alternate: bool  # the quote came from the alternate-line ladder
    minutes_before_tip: float | None


class Prices(BaseModel):
    posted_line: float | None
    over_price: int | None
    under_price: int | None
    on_posted_line: bool
    snapshot_utc: str | None
    say: str
    books: list[BookRow] = []


class LadderRow(BaseModel):
    line: float
    l10_over: int
    l10_under: int
    l10_push: int
    l5_over: int
    l5_under: int
    l5_push: int
    model_over: float | None
    model_under: float | None
    over_price: int | None
    under_price: int | None


class StatTab(BaseModel):
    stat: str
    label: str
    has_model: bool
    posted_line: float | None
    default_line: float
    default_source: Literal["posted", "last10_middle", "model_middle"]


class Breakdown(BaseModel):
    game_id: str
    player_id: int
    player: str
    team: str
    opponent: str
    home: bool
    tip_utc: str
    as_of_date: str
    stat: str
    label: str
    side: Side
    line: float
    line_source: Literal["posted", "last10_middle", "model_middle"]
    prior_games: int
    last10: Tally
    last5: Tally
    chips: list[Chip]
    season: Tally
    splits: list[Split]
    teammates: Teammates
    opponent_allowed: OppAllowed | None
    model: Model
    role: Role
    steadiness: Steadiness
    onoff: OnOff
    prices: Prices
    heads_up: str | None
    notes: list[str]
    ladder: list[LadderRow]
    ladder_n10: int
    ladder_n5: int
    tabs: list[StatTab]
    dissection: Dissection | None = None


# ----------------------------------------------------------------------------- pure math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k hits in n games; None when there are no games."""
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def hit_state(value: float, line: float, side: Side) -> State:
    """Over means strictly above the line; a value exactly on a whole-number line is a push."""
    if value == line:
        return "push"
    if side == "over":
        return "hit" if value > line else "miss"
    return "hit" if value < line else "miss"


def tally(rows: list[Row], stat: str, line: float, side: Side) -> Tally:
    n = len(rows)
    states = [hit_state(r.value(stat), line, side) for r in rows]
    hits = states.count("hit")
    pushes = states.count("push")
    ci = wilson(hits, n)
    return Tally(
        n=n,
        hits=hits,
        misses=states.count("miss"),
        pushes=pushes,
        rate=None if n == 0 else hits / n,
        ci_lo=None if ci is None else ci[0],
        ci_hi=None if ci is None else ci[1],
        avg=None if n == 0 else sum(r.value(stat) for r in rows) / n,
        avg_minutes=None if n == 0 else sum(r.minutes for r in rows) / n,
        small=n < SMALL_N,
    )


def default_line(values: list[int]) -> float:
    """Starting line when the book posted none: just under the middle of the last ten games."""
    if not values:
        return 0.5
    return math.floor(statistics.median(values)) + 0.5


def half_grid(lo: float, hi: float) -> list[float]:
    start = math.floor(lo * 2) / 2
    n = int(round((hi - start) * 2))
    return [start + 0.5 * i for i in range(n + 1)]


# ----------------------------------------------------------------------------- the builder


class Context:
    """One game and one player, with the as-of rule applied once."""

    def __init__(self, hist: History, game: Json, player: Json, stat: str | None = None) -> None:
        self.h = hist
        self.game = game
        self.player = player
        self.pid = int(player["player_id"])
        self.gid = str(game["game_id"])
        self.date = str(game["date"])
        self.season = int(game["season"])
        home_id, away_id = int(game["home"]["id"]), int(game["away"]["id"])
        self.team = home_id if player["side"] == "home" else away_id
        self.opp = away_id if player["side"] == "home" else home_id
        self.is_home = player["side"] == "home"
        # THE as-of filter: strictly before the slate date. Same-day and later rows never enter.
        self.all_prior: list[Row] = [r for r in hist.logs.get(self.pid, []) if r.date < self.date]
        # A game whose box score lacks a column this stat needs (steals, blocks) is left out of
        # that stat's counts, never counted as zero.
        self.prior: list[Row] = [r for r in self.all_prior if stat is None or hist.usable(r, stat)]
        self.prior_season = [r for r in self.prior if r.season == self.season]

    def last(self, n: int) -> list[Row]:
        return self.prior[-n:][::-1]  # most recent first

    def last_for(self, stat: str, n: int) -> list[Row]:
        return [r for r in self.all_prior if self.h.usable(r, stat)][-n:][::-1]

    def rest_days(self, team: int, gid: str) -> int | None:
        """Full days off before this team game, from the team's own schedule; None if unknown."""
        idx = self.h.team_index.get(team, {}).get(gid)
        if idx is None or idx == 0:
            return None
        games = self.h.team_games[team]
        gap = days_between(games[idx - 1][0], games[idx][0]) - 1
        return None if gap > REST_GAP_MAX else gap


def _bucket(rest: int | None) -> str | None:
    if rest is None:
        return None
    return "b2b" if rest == 0 else ("rest1" if rest == 1 else "rest2")


def _splits(ctx: Context, stat: str, line: float, side: Side) -> list[Split]:
    tonight_rest = _bucket(ctx.rest_days(ctx.team, ctx.gid))
    opp = ctx.h.team_abbr(ctx.opp)
    specs: list[tuple[str, str, bool, Any]] = [
        ("home", "At home", ctx.is_home, lambda r: r.home is True),
        ("away", "On the road", not ctx.is_home, lambda r: r.home is False),
        (
            "b2b",
            "Second night of a back-to-back",
            tonight_rest == "b2b",
            lambda r: _bucket(ctx.rest_days(r.team, r.game_id)) == "b2b",
        ),
        (
            "rest1",
            "One day off",
            tonight_rest == "rest1",
            lambda r: _bucket(ctx.rest_days(r.team, r.game_id)) == "rest1",
        ),
        (
            "rest2",
            "Two or more days off",
            tonight_rest == "rest2",
            lambda r: _bucket(ctx.rest_days(r.team, r.game_id)) == "rest2",
        ),
        ("vs_opp", f"Against {opp}", True, lambda r: r.opp == ctx.opp),
    ]
    out: list[Split] = []
    for key, label, tonight, pred in specs:
        allrows = [r for r in ctx.prior if pred(r)]
        season_rows = [r for r in allrows if r.season == ctx.season]
        out.append(
            Split(
                key=key,
                label=label,
                tonight=tonight,
                this_season=tally(season_rows, stat, line, side),
                since_last_season=tally(allrows, stat, line, side),
            )
        )
    return out


def _opponent_allowed(ctx: Context, stat: str) -> OppAllowed | None:
    pg = ctx.h.pos(ctx.pid)
    if pg is None:
        return None
    per_team: dict[int, list[int]] = {}
    for gid, (d, season, home, away) in ctx.h.games.items():
        if season != ctx.season or d >= ctx.date:
            continue
        by_team = ctx.h.played.get(gid)
        if not by_team:
            continue
        for defender, attacker in ((home, away), (away, home)):
            total = sum(
                row.value(stat)
                for pid, row in by_team.get(attacker, {}).items()
                if ctx.h.pos(pid) == pg
            )
            per_team.setdefault(defender, []).append(total)
    mine = per_team.get(ctx.opp)
    if not mine:
        return None
    avgs = {t: sum(v) / len(v) for t, v in per_team.items()}
    ordered = sorted(avgs, key=lambda t: (-avgs[t], t))
    rank = ordered.index(ctx.opp) + 1
    league = sum(avgs.values()) / len(avgs)
    opp = ctx.h.team_abbr(ctx.opp)
    noun = POSITION_NOUN[pg]
    return OppAllowed(
        opp=opp,
        position=pg,
        position_noun=noun,
        avg_allowed=avgs[ctx.opp],
        rank=rank,
        of=len(avgs),
        n_games=len(mine),
        league_avg=league,
        note=(
            f"Total {STAT_LABEL[stat].lower()} by opposing {noun} per game, this season, "
            f"{len(mine)} games. Rank 1 allows the most of {len(avgs)} teams."
        ),
    )


def _cutoff(game: Json) -> datetime:
    return datetime.fromisoformat(str(game["tip_utc"]).replace("Z", "+00:00")) - AVAIL_LEAD


def _latest_report(ctx: Context) -> tuple[dict[int, tuple[str, str | None]], str | None]:
    """Latest availability status per player, from reports published by tip minus an hour."""
    cutoff = _cutoff(ctx.game)
    latest: dict[int, tuple[str, str | None, str]] = {}
    for pid, status, reason, as_of in ctx.h.availability.get(ctx.gid, []):
        if datetime.fromisoformat(as_of) > cutoff:
            continue  # rows are as_of-ordered; a later report never leaks backwards
        prev = latest.get(int(pid))
        if prev is None or as_of >= prev[2]:
            latest[int(pid)] = (status, reason, as_of)
    stamp = max((v[2] for v in latest.values()), default=None)
    return {p: (v[0], v[1]) for p, v in latest.items()}, stamp


@dataclass(frozen=True)
class Mate:
    """A rotation teammate on tonight's injury report, and his team's games with and without him
    (every game he was on the team around, before tonight; not stat-specific)."""

    pid: int
    name: str
    status: str
    reason: str | None
    avg_minutes: float
    with_rows: list[Row]
    without_rows: list[Row]


@dataclass(frozen=True)
class ReportMates:
    mates: list[Mate]  # listed out, doubtful or questionable, most important first
    unnamed: int  # rotation players left out for want of a name
    reports: dict[int, tuple[str, str | None]]  # the whole report: player -> (status, reason)
    stamp: str | None


def teammates_on_report(ctx: Context) -> ReportMates:
    reports, stamp = _latest_report(ctx)
    items: list[Mate] = []
    unnamed = 0
    for pid, (status, reason) in reports.items():
        if pid == ctx.pid or status not in SHOWN_AVAIL:
            continue
        xrows = [r for r in ctx.h.logs.get(pid, []) if r.date < ctx.date]
        if not xrows or xrows[-1].team != ctx.team:
            continue  # not (known to be) a teammate before tonight
        recent = xrows[-LAST_N:]
        avg_min = sum(r.minutes for r in recent) / len(recent)
        if avg_min < ROTATION_MIN:
            continue
        name = ctx.h.name(pid)
        if name is None:
            unnamed += 1
            continue
        x_idx = {
            ctx.h.team_index[ctx.team][r.game_id]
            for r in xrows
            if r.team == ctx.team and r.game_id in ctx.h.team_index[ctx.team]
        }
        with_rows: list[Row] = []
        without_rows: list[Row] = []
        for r in ctx.all_prior:
            if r.team != ctx.team:
                continue
            i = ctx.h.team_index[ctx.team][r.game_id]
            if i in x_idx:
                with_rows.append(r)
            elif any(abs(i - j) <= ROSTER_WINDOW for j in x_idx):
                without_rows.append(r)  # on the team around then, and did not play this night
        items.append(Mate(pid, name, status, reason, avg_min, with_rows, without_rows))
    items.sort(key=lambda m: (SHOWN_AVAIL.index(m.status), -m.avg_minutes))
    return ReportMates(items[:MAX_TEAMMATES], unnamed, reports, stamp)


def mate_note(n_without: int) -> str:
    if n_without == 0:
        return "No earlier game in our data with him out, so there is nothing to compare."
    if n_without < SMALL_N:
        return f"Only {n_without} earlier games with him out: a small sample, not a pattern."
    return f"{n_without} earlier games with him out."


def _teammates(ctx: Context, stat: str, line: float, side: Side) -> Teammates:
    rep = teammates_on_report(ctx)
    mine = rep.reports.get(ctx.pid)
    items: list[TeammateOut] = []
    for m in rep.mates:
        use_with = [r for r in m.with_rows if ctx.h.usable(r, stat)]
        use_without = [r for r in m.without_rows if ctx.h.usable(r, stat)]
        wo, wi = tally(use_without, stat, line, side), tally(use_with, stat, line, side)
        items.append(
            TeammateOut(
                player_id=m.pid,
                name=m.name,
                status=m.status,
                reason=m.reason,
                avg_minutes=m.avg_minutes,
                without=wo,
                with_=wi,
                per36_without=per36(m.without_rows),
                per36_with=per36(m.with_rows),
                note=mate_note(wo.n),
            )
        )
    return Teammates(
        report_as_of_utc=rep.stamp,
        player_status=mine[0] if mine else None,
        player_status_reason=mine[1] if mine else None,
        items=items,
        omitted_unnamed=rep.unnamed,
        note=(
            "From the official injury report published at least an hour before tip. Only "
            f"rotation players (about {ROTATION_MIN:.0f}+ minutes a game lately) are shown."
        ),
    )


def _model(ctx: Context, stat: str, line: float, side: Side) -> Model:
    prop = ctx.player["props"].get(stat) if stat in MODEL_STATS else None
    if prop is None:
        return Model(
            available=False,
            p_hit=None,
            p_over=None,
            p_under=None,
            p_push=None,
            mean=None,
            typical=None,
            extrapolated=False,
            stamp=None,
            say="No model forecast for this stat yet.",
            reason="",
        )
    q = [float(v) for v in prop["forecast"]["q"]]
    lp = forecast_probs(prop["forecast"], line)
    p_hit = lp.p_over if side == "over" else lp.p_under
    return Model(
        available=True,
        p_hit=p_hit,
        p_over=lp.p_over,
        p_under=lp.p_under,
        p_push=lp.p_push,
        mean=prop["forecast"].get("mean"),
        typical=q[9],
        extrapolated=lp.extrapolated,
        stamp=prop["forecast"]["stamp"],
        say="",
        reason="",
    )


def _role(ctx: Context) -> Role:
    last = ctx.last(LAST_N)
    n = len(last)
    starts = sum(1 for r in last if r.starter)
    avg_min = sum(r.minutes for r in last) / n if n else None
    if avg_min is None:
        say = "No earlier games in our data."
    else:
        say = (
            f"Averaged {avg_min:.1f} minutes over his last {n} games and started {starts} of them."
        )
    proj, p_play = _stored_minutes(ctx)
    if proj is not None:
        chance = "" if p_play is None else f" He is given a {round(p_play * 100)}% chance to play."
        say += f" The model projects about {proj:.1f} minutes.{chance}"
    return Role(
        n=n, avg_minutes=avg_min, starts=starts, minutes_forecast=proj, p_play=p_play, say=say
    )


def _stored_minutes(ctx: Context) -> tuple[float | None, float | None]:
    """The projected minutes and chance to play stored with this player's forecasts, if any."""
    for st in MODEL_STATS:
        f = (ctx.player["props"].get(st) or {}).get("forecast") or {}
        if f.get("proj_minutes") is not None:
            p = f.get("p_play")
            return float(f["proj_minutes"]), None if p is None else float(p)
    return None, None


def _prices(ctx: Context, stat: str, line: float) -> Prices:
    rows = ctx.h.prices.get(ctx.gid, {}).get(str(ctx.pid), {}).get(stat, [])
    main = next((x for x in rows if x["main"]), None)
    at = next((x for x in rows if x["point"] == line), None)
    books = _books_at(ctx, stat, line)
    if main is None:
        return Prices(
            posted_line=None,
            over_price=None,
            under_price=None,
            on_posted_line=False,
            snapshot_utc=None,
            say=(
                "The book posted no line for this stat, so there is no price."
                if ctx.h.market
                else "This data holds no sportsbook lines or prices, so none is shown."
            ),
            books=books,
        )
    if at is None:
        return Prices(
            posted_line=float(main["point"]),
            over_price=None,
            under_price=None,
            on_posted_line=False,
            snapshot_utc=main.get("snapshot_utc"),
            say="No price is stored for this line.",
            books=books,
        )
    return Prices(
        posted_line=float(main["point"]),
        over_price=at["over_price"],
        under_price=at["under_price"],
        on_posted_line=bool(at["main"]),
        snapshot_utc=at.get("snapshot_utc"),
        say=_price_say(at),
        books=books,
    )


BOOK_NAMES = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "espnbet": "ESPN Bet",
    "fanatics": "Fanatics",
    "kalshi": "Kalshi",
    "novig": "Novig",
    "polymarket": "Polymarket",
    "polymarket_us": "Polymarket US",
}


def book_name(key: str) -> str:
    return BOOK_NAMES.get(key, key.replace("_", " ").capitalize())


def _price_say(entry: Json) -> str:
    """Who quoted the price on screen (fixture entries name no book: DraftKings)."""
    ob, ub = entry.get("over_book"), entry.get("under_book")
    if ob is None and ub is None:
        return "Posted price, DraftKings about an hour before tip."
    if ob is not None and ub is not None and ob != ub:
        return (
            f"Posted prices, over from {book_name(ob)} and under from {book_name(ub)}, "
            "captured before tip."
        )
    return f"Posted price from {book_name(str(ob or ub))}, captured before tip."


def _books_at(ctx: Context, stat: str, line: float) -> list[BookRow]:
    """Every book's stored quote at this exact line, main and alternate ladders together."""
    quotes = ctx.h.quotes.get(ctx.gid, {}).get(str(ctx.pid), {}).get(stat, [])
    by_book: dict[str, dict[str, Json]] = {}
    for q in quotes:
        if float(q["line"]) == line:
            by_book.setdefault(q["book"], {})[q["side"]] = q
    out: list[BookRow] = []
    for book in sorted(by_book):
        o, u = by_book[book].get("over"), by_book[book].get("under")
        mins = [q["minutes_before_tip"] for q in (o, u) if q and q.get("minutes_before_tip")]
        out.append(
            BookRow(
                book=book,
                over_price=None if o is None else o["price"],
                under_price=None if u is None else u["price"],
                over_unusable=bool(o and o["unusable"]),
                under_unusable=bool(u and u["unusable"]),
                alternate=any(q["alt"] for q in (o, u) if q),
                minutes_before_tip=min(mins) if mins else None,
            )
        )
    return out


def _ladder(ctx: Context, stat: str, line: float, center: float) -> list[LadderRow]:
    last10 = ctx.last(LAST_N)
    last5 = last10[:RECENT_N]
    vals = [r.value(stat) for r in last10] + [center, line]
    lo = max(0.5, math.floor(min(vals)) - 1.5)
    hi = math.ceil(max(vals)) + 1.5
    prop = ctx.player["props"].get(stat) if stat in MODEL_STATS else None
    rows = ctx.h.prices.get(ctx.gid, {}).get(str(ctx.pid), {}).get(stat, [])
    by_point = {float(x["point"]): x for x in rows}
    out: list[LadderRow] = []
    for ln in half_grid(lo, hi):
        t10 = [hit_state(r.value(stat), ln, "over") for r in last10]
        t5 = [hit_state(r.value(stat), ln, "over") for r in last5]
        lp = forecast_probs(prop["forecast"], ln) if prop else None
        px = by_point.get(ln)
        out.append(
            LadderRow(
                line=ln,
                l10_over=t10.count("hit"),
                l10_under=t10.count("miss"),
                l10_push=t10.count("push"),
                l5_over=t5.count("hit"),
                l5_under=t5.count("miss"),
                l5_push=t5.count("push"),
                model_over=None if lp is None else lp.p_over,
                model_under=None if lp is None else lp.p_under,
                over_price=None if px is None else px["over_price"],
                under_price=None if px is None else px["under_price"],
            )
        )
    return out


def _tabs(ctx: Context) -> list[StatTab]:
    tabs: list[StatTab] = []
    for st in ctx.h.stats:
        last10 = ctx.last_for(st, LAST_N)
        rows = ctx.h.prices.get(ctx.gid, {}).get(str(ctx.pid), {}).get(st, [])
        main = next((x for x in rows if x["main"]), None)
        posted = None if main is None else float(main["point"])
        has_model = st in MODEL_STATS and st in ctx.player["props"]
        start: float
        source: Literal["posted", "last10_middle", "model_middle"]
        if posted is not None:
            start, source = posted, "posted"
        elif has_model:
            # No sportsbook line is stored for this prop: start at the middle of the model's own
            # forecast, the same reference line the game page shows. Not a market line.
            start, source = reference_line(ctx.player["props"][st]["forecast"]), "model_middle"
        else:
            start, source = default_line([r.value(st) for r in last10]), "last10_middle"
        tabs.append(
            StatTab(
                stat=st,
                label=STAT_LABEL[st],
                has_model=has_model,
                posted_line=posted,
                default_line=start,
                default_source=source,
            )
        )
    return tabs


def _heads_up(line: float, side: Side, l10: Tally, model: Model, name: str) -> str | None:
    if not model.available or model.p_hit is None or l10.rate is None or l10.n == 0:
        return None
    gap = l10.rate - model.p_hit
    if abs(gap) < HEADS_UP_GAP:
        return None
    word = "over" if side == "over" else "under"
    recent = f"{name} went {word} {line:g} in {l10.hits} of his last {l10.n}"
    ours = f"our model gives {round(model.p_hit * 100)}%"
    if gap > 0:
        tail = "Recent results are running well ahead of the forecast."
    else:
        tail = "Recent results are running well behind the forecast."
    small = " That is a small sample." if l10.small else ""
    return (
        f"Heads up: {recent}, but {ours}. {tail}{small} They measure different things, and "
        "neither is a promise."
    )


def _notes(l10: Tally, ctx: Context) -> list[str]:
    notes: list[str] = []
    if l10.n == 0:
        notes.append("No earlier games in our data for this player, so there is nothing to show.")
    elif l10.small:
        notes.append(
            f"Small sample: only {l10.n} earlier games, fewer than {SMALL_N}. "
            "A rate over so few games can swing a lot."
        )
    notes.append(
        "Last-10 hit rates are noisy. Six of ten is not far from a coin flip, and a streak is not "
        "an edge."
    )
    return notes


def build(
    hist: History,
    game: Json,
    player: Json,
    stat: str,
    line: float | None,
    side: Side,
) -> Breakdown:
    if stat not in hist.stats:
        raise ValueError(f"unknown stat {stat}")
    ctx = Context(hist, game, player, stat)
    last10 = ctx.last(LAST_N)
    last5 = last10[:RECENT_N]
    tabs = _tabs(ctx)
    tab = next(t for t in tabs if t.stat == stat)
    use = tab.default_line if line is None else line
    source = tab.default_source

    l10 = tally(last10, stat, use, side)
    l5 = tally(last5, stat, use, side)
    model = _model(ctx, stat, use, side)
    role = _role(ctx)
    name = str(player["name"])
    if model.available and model.p_hit is not None:
        model.say = f"Our model gives {name} {round(model.p_hit * 100)}% to go {side} {use:g}" + (
            " (the forecast does not reach this far into the tail)." if model.extrapolated else "."
        )
        typical = f"a typical night near {model.typical:.1f} {STAT_LABEL[stat].lower()}"
        recent = (
            f"; his last {l10.n} averaged {l10.avg:.1f}" if l10.avg is not None and l10.n else ""
        )
        no_minutes = (
            " We do not forecast minutes yet, so minutes and role are context here, not a "
            "number the model hands us."
            if role.minutes_forecast is None
            else ""
        )
        model.reason = f"It expects {typical}{recent}. {role.say}{no_minutes}"
    chips = [
        Chip(
            game_id=r.game_id,
            date=r.date,
            opp=hist.team_abbr(r.opp),
            home=r.home,
            value=r.value(stat),
            minutes=r.minutes,
            starter=r.starter,
            state=hit_state(r.value(stat), use, side),
        )
        for r in last10
    ]
    return Breakdown(
        game_id=ctx.gid,
        player_id=ctx.pid,
        player=name,
        team=hist.team_abbr(ctx.team),
        opponent=hist.team_abbr(ctx.opp),
        home=ctx.is_home,
        tip_utc=str(game["tip_utc"]),
        as_of_date=ctx.date,
        stat=stat,
        label=STAT_LABEL[stat],
        side=side,
        line=use,
        line_source=source,
        prior_games=len(ctx.prior),
        last10=l10,
        last5=l5,
        chips=chips,
        season=tally(ctx.prior_season, stat, use, side),
        splits=_splits(ctx, stat, use, side),
        teammates=_teammates(ctx, stat, use, side),
        opponent_allowed=_opponent_allowed(ctx, stat),
        model=model,
        role=role,
        steadiness=steadiness(ctx.all_prior),
        onoff=onoff(hist, ctx.pid, ctx.all_prior, ctx.season),
        prices=_prices(ctx, stat, use),
        heads_up=_heads_up(use, side, l10, model, name),
        notes=_notes(l10, ctx),
        ladder=_ladder(ctx, stat, use, tab.default_line),
        ladder_n10=len(last10),
        ladder_n5=len(last5),
        tabs=tabs,
    )
