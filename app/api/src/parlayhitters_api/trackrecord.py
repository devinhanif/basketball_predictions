"""The track record, computed from settled games in the loaded data. Read-only, no prices.

Calibration: "when the model said X%, how often did it happen?", in bins, with the number of
cases in each bin and a 95% interval from a bootstrap that resamples whole games (the cases of one
game are not independent: teammates share a pace and an opponent). A bin with fewer than MIN_N
cases or MIN_GAMES games gets no interval and says "not enough to say".

What is counted:
  * player props: NOTHING from this season. The calibrated map is under a frozen live test
    (LINE_CAL_LIVE) that forbids interim looks at its outcomes until its single look, so no 2026
    prop calibration bin, count or interval is computed or served here. The tab carries the map's
    published 2024-25 record (T493, a committed file) and a plain note instead.
  * game winners: the favourite's chance (the larger of home and away), against whether the
    favourite won.

Guess deck: finished props with a posted half-point line, drawn at random with a seed from every
such prop (nothing is picked for being interesting). The deck carries no outcome and no forecast;
the reveal does.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .breakdown import Context
from .forecast import dist_grid, shown_probs
from .minutes import moments

Json = dict[str, Any]

MIN_N = 20
MIN_GAMES = 5
BOOT = 1000
SEED = 20261109
STATS = ("pts", "reb", "ast", "fg3m")


class Bin(BaseModel):
    lo: float
    hi: float
    n: int
    n_games: int
    said: float | None  # mean chance the model gave, over the cases in the bin
    happened: float | None  # share of those cases that happened
    lo95: float | None
    hi95: float | None
    enough: bool


class SourceRow(BaseModel):
    source: str
    label: str
    n: int
    overall: Bin


class Series(BaseModel):
    name: str
    what_said: str
    what_happened: str
    n: int
    n_games: int
    bins: list[Bin]
    #: All cases in one bin: the plain overall rate with its interval.
    overall: Bin
    #: Where the chances came from. More than one row means the bins above mix models.
    sources: list[SourceRow]
    excluded: dict[str, int]
    interval: str


class PubInterval(BaseModel):
    mean: float
    lo: float
    hi: float


class PubBin(BaseModel):
    lo: float
    n: int
    said: float
    happened: float


class PubBook(BaseModel):
    label: str
    n: int
    games: int
    log_loss_cal_minus_raw: PubInterval
    mean_cal_minus_realised: PubInterval
    bins: list[PubBin]


class PropsPublished(BaseModel):
    """The calibrated map's published record on a finished season. Copied from a committed
    report; nothing here is computed from this season's games."""

    ledger_id: str
    season_label: str
    rule_sha8: str
    verdict: str
    source: str
    books: dict[str, PubBook]
    interval: str
    this_season: str


PROPS_THIS_SEASON = (
    "This season's check is running as a locked test; results appear after it reads (about Dec 1)."
)


def props_published() -> PropsPublished:
    raw = json.loads(
        (Path(__file__).parent / "published" / "line_cal_t493.json").read_text(encoding="utf-8")
    )
    return PropsPublished(
        ledger_id=raw["ledger_id"],
        season_label=raw["season_label"],
        rule_sha8=raw["rule_sha8"],
        verdict=raw["verdict"],
        source=raw["source"],
        books={k: PubBook(**v) for k, v in raw["books"].items()},
        interval="95% interval from resampling whole games (2,000 resamples), as published",
        this_season=PROPS_THIS_SEASON,
    )


class Calibration(BaseModel):
    mode: str
    date_from: str | None
    date_to: str | None
    n_games_finished: int
    props: PropsPublished
    winners: Series
    note: str


@dataclass(frozen=True)
class Case:
    game_id: str
    p: float  # the chance the model gave
    hit: bool  # whether it happened
    source: str = ""  # which model made the chance (model_source key, or a win model's name)
    label: str = ""  # the stored sentence for that source


def finished_games(store: Any) -> list[Json]:
    games = [g for s in store.slates.values() for g in s["games"] if g.get("result") is not None]
    return sorted(games, key=lambda g: (g["date"], g["game_id"]))


def _is_half(line: float) -> bool:
    return abs(line - math.floor(line) - 0.5) < 1e-9


def winner_cases(games: list[Json]) -> tuple[list[Case], dict[str, int]]:
    out: list[Case] = []
    missing = 0
    for g in games:
        ph = g["win_probability"].get("model_home")
        if ph is None:
            missing += 1
            continue
        home_won = bool(g["result"]["home_won"])
        fav_home = ph >= 0.5
        name = str(g["win_probability"].get("model_name"))
        out.append(
            Case(
                g["game_id"],
                ph if fav_home else 1 - ph,
                home_won == fav_home,
                name,
                f"Win forecast model: {name}.",
            )
        )
    return out, {"no_forecast": missing}


def _interval(clusters: list[tuple[int, int]], rng: random.Random) -> tuple[float, float] | None:
    k = len(clusters)
    rates: list[float] = []
    for _ in range(BOOT):
        n = h = 0
        for i in rng.choices(range(k), k=k):
            n += clusters[i][0]
            h += clusters[i][1]
        rates.append(h / n)
    rates.sort()
    return rates[int(0.025 * BOOT)], rates[int(0.975 * BOOT) - 1]


def binned(cases: list[Case], edges: list[float], rng: random.Random) -> list[Bin]:
    out: list[Bin] = []
    for i, lo in enumerate(edges[:-1]):
        hi = edges[i + 1]
        last = i == len(edges) - 2
        rows = [c for c in cases if lo <= c.p < hi or (last and c.p == hi)]
        by_game: dict[str, list[int]] = {}
        for c in rows:
            by_game.setdefault(c.game_id, []).append(int(c.hit))
        n = len(rows)
        enough = n >= MIN_N and len(by_game) >= MIN_GAMES
        ci = None
        if enough:
            ci = _interval([(len(v), sum(v)) for v in by_game.values()], rng)
        out.append(
            Bin(
                lo=lo,
                hi=hi,
                n=n,
                n_games=len(by_game),
                said=None if n == 0 else sum(c.p for c in rows) / n,
                happened=None if n == 0 else sum(c.hit for c in rows) / n,
                lo95=None if ci is None else ci[0],
                hi95=None if ci is None else ci[1],
                enough=enough,
            )
        )
    return out


def by_source(cases: list[Case], edges: list[float], rng: random.Random) -> list[SourceRow]:
    rows: list[SourceRow] = []
    for src in sorted({c.source for c in cases}):
        mine = [c for c in cases if c.source == src]
        rows.append(
            SourceRow(
                source=src,
                label=mine[0].label,
                n=len(mine),
                overall=binned(mine, edges, rng)[0],
            )
        )
    return rows


def calibration(store: Any) -> Calibration:
    games = finished_games(store)
    rng = random.Random(SEED)  # noqa: S311 - a reproducible bootstrap, not security
    wcases, wex = winner_cases(games)
    fav = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    interval = "95% interval from resampling whole games (1,000 resamples)"
    return Calibration(
        mode=store.mode,
        date_from=games[0]["date"] if games else None,
        date_to=games[-1]["date"] if games else None,
        n_games_finished=len(games),
        props=props_published(),
        winners=Series(
            name="Game winners",
            what_said="the favourite's chance to win",
            what_happened="the favourite won",
            n=len(wcases),
            n_games=len({c.game_id for c in wcases}),
            bins=binned(wcases, fav, rng),
            overall=binned(wcases, [0.5, 1.0], rng)[0],
            sources=by_source(wcases, [0.5, 1.0], rng),
            excluded=wex,
            interval=interval,
        ),
        note="Game winners: the win model's own chance (it has no calibrated variant). "
        "Only games in the loaded data.",
    )


# ----------------------------------------------------------------------------- guess deck


class GuessForm(BaseModel):
    last5: list[int]
    avg_minutes5: float | None
    n_prior: int


class GuessCard(BaseModel):
    prop_id: str
    game_id: str
    date: str
    player: str
    team: str
    opponent: str
    home: bool
    stat: str
    line: float
    form: GuessForm | None


class GuessDeck(BaseModel):
    seed: int
    cards: list[GuessCard]
    n_available: int
    reason: str
    note: str


class GuessReveal(BaseModel):
    prop_id: str
    actual: int
    line: float
    went_over: bool
    model_p_over: float
    model_p_source: str  # line_cal | rung_cal | model
    model_call: str  # "over" | "under"
    minutes: float | None
    stat_mean: float
    forecast_stamp: str


def _eligible(games: list[Json]) -> list[tuple[Json, Json, str, Json]]:
    rows: list[tuple[Json, Json, str, Json]] = []
    for g in games:
        for pl in g["players"]:
            for st, pr in pl["props"].items():
                if (
                    pr.get("line_source", "posted") == "posted"
                    and _is_half(float(pr["line"]))
                    and pr["actual"] is not None
                ):
                    rows.append((g, pl, st, pr))
    return rows


def guess_deck(store: Any, seed: int, n: int) -> GuessDeck:
    rows = _eligible(finished_games(store))
    if not rows:
        return GuessDeck(
            seed=seed,
            cards=[],
            n_available=0,
            reason="no_props",
            note="This data holds no finished player props with a posted line, so there is "
            "nothing to guess on yet.",
        )
    rng = random.Random(seed)  # noqa: S311 - a shuffle for a game, not security
    pick = rng.sample(rows, min(n, len(rows)))
    cards: list[GuessCard] = []
    for g, pl, st, pr in pick:
        me, opp = (g["home"], g["away"]) if pl["side"] == "home" else (g["away"], g["home"])
        form: GuessForm | None = None
        if store.history is not None:
            ctx = Context(store.history, g, pl, st)
            last = ctx.last(5)
            mins = [r.minutes for r in last]
            form = GuessForm(
                last5=[r.value(st) for r in last],
                avg_minutes5=None if not mins else sum(mins) / len(mins),
                n_prior=len(ctx.prior),
            )
        cards.append(
            GuessCard(
                prop_id=pr["prop_id"],
                game_id=g["game_id"],
                date=g["date"],
                player=pl["name"],
                team=me["abbr"],
                opponent=opp["abbr"],
                home=pl["side"] == "home",
                stat=st,
                line=float(pr["line"]),
                form=form,
            )
        )
    return GuessDeck(
        seed=seed,
        cards=cards,
        n_available=len(rows),
        reason="ok",
        note="Drawn at random from every finished prop with a posted line. Nothing was picked "
        "for being easy or hard.",
    )


def guess_reveal(store: Any, prop_id: str) -> GuessReveal | None:
    parts = prop_id.split(":")
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit():
        return None
    gid, pid, st = parts[0], int(parts[1]), parts[2]
    g = store.game(gid)
    if g is None or g.get("result") is None:
        return None
    pl = next((p for p in g["players"] if p["player_id"] == pid), None)
    pr = None if pl is None else pl["props"].get(st)
    if (
        pl is None
        or pr is None
        or pr["actual"] is None
        or pr.get("line_source", "posted") != "posted"
        or not _is_half(float(pr["line"]))
    ):
        return None
    line = float(pr["line"])
    shown = shown_probs(pr, line)  # the calibrated chance where the export has one
    p = shown.p_over
    actual = int(pr["actual"])
    over = actual > line
    # The lean follows the chance shown. No field says whether the model was right: the frozen
    # LINE_CAL_LIVE rule forbids any model-versus-outcome verdict or tally on 2026 props until its
    # single look (about Dec 1). The reveal carries the chance and the outcome side by side.
    call = "over" if p >= 0.5 else "under"
    return GuessReveal(
        prop_id=prop_id,
        actual=actual,
        line=line,
        went_over=over,
        model_p_over=p,
        model_p_source=shown.source,
        model_call=call,
        minutes=None if pl["result"] is None else float(pl["result"]["minutes"]),
        stat_mean=moments(dist_grid(pr["forecast"])["p_ge"])[0],
        forecast_stamp=str(pr["forecast"]["stamp"]),
    )
