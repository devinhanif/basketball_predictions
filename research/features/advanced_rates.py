# ruff: noqa: E501
"""ADVANCED_RATES as-of time-series builder (docs/prereg/ADVANCED_RATES.md, DRAFT).

Every stat is a ratio ``num / den`` per played game (formulas in :data:`STAT_DEFS`). A stat is
represented ONE way for every family (the pre-declared representation, no sweep):

====================  ===============================================================
``{s}_ew5/_ew20/_ew100``  ratio-of-sums EWMA at half-lives 5, 20, 100 played games,
                          shrunk to the position-group as-of league rate
``{s}_slope20``           OLS slope of the per-game ratio over the last 20 played games (valid ones only)
                          (>= 6 valid games, else NaN), units of the stat per game
``{s}_vol20``             sample sd (ddof 1) of the same window (>= 6 valid games, else NaN)
``{s}_stint20``           half-life 20 EWMA over the CURRENT team stint only (restarts at a team
                          change), shrunk to the player's career-to-date rate (itself shrunk to the
                          position prior), so a new team starts at the career value
====================  ===============================================================

plus ``adv_stint_n`` (played games with the row's team since the last change; shared by all stats).

As-of rules (planted-future tests in ``tests/research/features/test_advanced_rates.py``):

* A row's features use PLAYED games of that player dated STRICTLY BEFORE the row's game date.
  Rows that are not played (DNP) get features but never advance a state.
* Weight of the j-th previous played game is ``0.5 ** (a / h)``, ``a = 1`` for the latest.
* Shrinkage: ``(S_num + k*mu) / (S_den + k)`` with ``k = PSEUDO * dbar`` where ``dbar`` is the
  position group's as-of mean ``den`` per played game and ``mu`` its as-of ratio of sums. The
  position prior at date ``d`` uses only played rows dated strictly before ``d``.
* A game is "valid" for slope/vol when ``den >= 0.5 * dbar`` (a ratio from a near-empty
  denominator is noise). Invalid games still contribute their (tiny) sums to the EWMA.
* Missing (NaN) ``num``/``den`` contribute nothing; no value is ever imputed with zero.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import polars as pl

HALF_LIVES: tuple[float, ...] = (5.0, 20.0, 100.0)
STINT_HALF_LIFE = 20.0
WINDOW = 20
MIN_VALID = 6
PSEUDO = 5.0  # games' worth of the position/career prior
REPR_SUFFIXES: tuple[str, ...] = ("ew5", "ew20", "ew100", "slope20", "vol20", "stint20")


# --------------------------------------------------------------------------- formulas
def game_score(
    pts: float,
    fgm: float,
    fga: float,
    ftm: float,
    fta: float,
    oreb: float,
    dreb: float,
    stl: float,
    ast: float,
    blk: float,
    pf: float,
    tov: float,
) -> float:
    """Hollinger Game Score: PTS + 0.4 FG - 0.7 FGA - 0.4 (FTA - FT) + 0.7 ORB + 0.3 DRB
    + STL + 0.7 AST + 0.7 BLK - 0.4 PF - TOV."""
    return (
        pts
        + 0.4 * fgm
        - 0.7 * fga
        - 0.4 * (fta - ftm)
        + 0.7 * oreb
        + 0.3 * dreb
        + stl
        + 0.7 * ast
        + 0.7 * blk
        - 0.4 * pf
        - tov
    )


def ts_pct(pts: float, fga: float, fta: float) -> float:
    """True shooting: PTS / (2 (FGA + 0.44 FTA))."""
    return pts / (2.0 * (fga + 0.44 * fta))


def ppws(pts: float, fga: float, fta: float) -> float:
    """Points per weighted shot: PTS / (FGA + 0.475 FTA). The 0.475 FT weight is NOT verified
    against a primary source (docs/prereg/ADVANCED_RATES.md); it is fixed here and a series
    correlated > 0.995 with TS% is dropped before any fit."""
    return pts / (fga + 0.475 * fta)


def pure_point_rating(
    ast: float, tov: float, minutes: float, lg_pace: float, tm_pace: float
) -> float:
    """Hollinger PPR as given in the Basketball-Reference glossary:
    100 * (lg possessions per 48 / team possessions per 48) * ((2/3 AST - TOV) / MP)."""
    return 100.0 * (lg_pace / tm_pace) * ((2.0 / 3.0 * ast - tov) / minutes)


def usage_oncourt(
    fga: float, fta: float, tov: float, oc_fga: float, oc_fta: float, oc_tov: float
) -> float:
    """Share of the team's plays used while on court:
    (FGA + 0.44 FTA + TOV) / (team FGA + 0.44 team FTA + team TOV while on court)."""
    return (fga + 0.44 * fta + tov) / (oc_fga + 0.44 * oc_fta + oc_tov)


def oreb_pct(oreb: float, oc_oreb: float, oc_opp_dreb: float) -> float:
    """OREB / (team OREB + opponent DREB while on court)."""
    return oreb / (oc_oreb + oc_opp_dreb)


def expected_plays(usg: float, proj_poss: float, exp_min: float) -> float:
    """usage share x projected game possessions x expected minutes / 48."""
    return usg * proj_poss * exp_min / 48.0


# num / den expressions over box columns, on-court columns (research.features.advanced_oncourt),
# ``oc_poss`` = (oc_poss_off + oc_poss_def) / 2 and ``pace_factor`` (lg pace as-of / team pace of
# that game). Each entry: name -> (num, den, family).
_PLAYS = pl.col("fga") + 0.44 * pl.col("fta")
_OC_POSS = (pl.col("oc_poss_off") + pl.col("oc_poss_def")) / 2.0
STAT_DEFS: dict[str, tuple[pl.Expr, pl.Expr, str]] = {
    "usg": (
        _PLAYS + pl.col("tov"),
        pl.col("oc_fga") + 0.44 * pl.col("oc_fta") + pl.col("oc_tov"),
        "USAGE_PACE",
    ),
    "pts100": (pl.col("pts"), _OC_POSS / 100.0, "USAGE_PACE"),
    "reb100": (pl.col("reb"), _OC_POSS / 100.0, "USAGE_PACE"),
    "ast100": (pl.col("ast"), _OC_POSS / 100.0, "USAGE_PACE"),
    "fg3m100": (pl.col("fg3m"), _OC_POSS / 100.0, "USAGE_PACE"),
    "pace48": (_OC_POSS, pl.col("oc_min") / 48.0, "USAGE_PACE"),
    "gmsc": (
        pl.col("pts")
        + 0.4 * pl.col("fgm")
        - 0.7 * pl.col("fga")
        - 0.4 * (pl.col("fta") - pl.col("ftm"))
        + 0.7 * pl.col("oreb")
        + 0.3 * pl.col("dreb")
        + pl.col("stl")
        + 0.7 * pl.col("ast")
        + 0.7 * pl.col("blk")
        - 0.4 * pl.col("pf")
        - pl.col("tov"),
        pl.lit(1.0),
        "EFFICIENCY",
    ),
    "ts": (pl.col("pts"), 2.0 * _PLAYS, "EFFICIENCY"),
    "ppws": (pl.col("pts"), pl.col("fga") + 0.475 * pl.col("fta"), "EFFICIENCY"),
    "par3": (pl.col("fg3a"), pl.col("fga"), "EFFICIENCY"),
    "ftr": (pl.col("fta"), pl.col("fga"), "EFFICIENCY"),
    "ppr": (
        pl.col("pace_factor") * (2.0 / 3.0 * pl.col("ast") - pl.col("tov")),
        pl.col("minutes") / 100.0,
        "PLAYMAKING",
    ),
    "ast_ratio": (pl.col("ast"), _PLAYS + pl.col("ast") + pl.col("tov"), "PLAYMAKING"),
    "tov_pct": (pl.col("tov"), _PLAYS + pl.col("tov"), "PLAYMAKING"),
    "ast_pct": (pl.col("ast"), pl.col("oc_fgm") - pl.col("fgm"), "PLAYMAKING"),
    "tr_share": (pl.col("tr_fsp"), pl.col("fsp"), "PLAY_TYPE"),
    "tr_ppp": (pl.col("tr_pts"), pl.col("tr_fsp"), "PLAY_TYPE"),
    "ast_share": (pl.col("ast_mk"), pl.col("mk"), "PLAY_TYPE"),
    "putback_share": (pl.col("pb_att"), pl.col("fga"), "PLAY_TYPE"),
    "orb": (pl.col("oreb"), pl.col("oc_oreb") + pl.col("oc_opp_dreb"), "REBOUNDING"),
    "drb": (pl.col("dreb"), pl.col("oc_dreb") + pl.col("oc_opp_oreb"), "REBOUNDING"),
}


def series_columns(stats: list[str]) -> list[str]:
    return [f"{s}_{x}" for s in stats for x in REPR_SUFFIXES]


# --------------------------------------------------------------------------- builder
@dataclass(frozen=True)
class SeriesInput:
    """Column names of the long frame ``asof_series`` reads."""

    entity: str = "player_id"
    team: str = "team_id"
    pos: str = "pos"
    played: str = "played"


DEFAULT_INPUT = SeriesInput()


def position_prior(
    rows: pl.DataFrame, stats: list[str], inp: SeriesInput = DEFAULT_INPUT
) -> pl.DataFrame:
    """Per row: ``{s}__mu`` (as-of ratio of sums) and ``{s}__dbar`` (as-of mean den per played
    game) of the row's position group, using played rows dated STRICTLY BEFORE the row's date."""
    played = rows.filter(pl.col(inp.played))
    aggs = []
    for s in stats:
        num = pl.col(f"{s}__num")
        den = pl.col(f"{s}__den")
        ok = num.is_not_null() & den.is_not_null() & num.is_finite() & den.is_finite()
        aggs += [
            pl.when(ok).then(num).otherwise(0.0).sum().alias(f"{s}__cn"),
            pl.when(ok).then(den).otherwise(0.0).sum().alias(f"{s}__cd"),
            pl.col(inp.entity).count().alias(f"{s}__cnt"),
        ]
    daily = (
        played.group_by([inp.pos, "game_date"])
        .agg(aggs)
        .sort([inp.pos, "game_date"])
        .with_columns(
            [
                pl.col(f"{s}__{x}").cum_sum().over(inp.pos)
                for s in stats
                for x in ("cn", "cd", "cnt")
            ]
        )
    )
    left = rows.select([inp.entity, "game_id", inp.pos, "game_date"]).with_columns(
        (pl.col("game_date") - dt.timedelta(days=1)).alias("_d")
    )
    joined = left.sort("_d").join_asof(
        daily.sort("game_date"),
        left_on="_d",
        right_on="game_date",
        by=inp.pos,
        strategy="backward",
        suffix="_r",
    )
    out = [joined[inp.entity], joined["game_id"]]
    for s in stats:
        cd = joined[f"{s}__cd"]
        cnt = joined[f"{s}__cnt"]
        out.append((joined[f"{s}__cn"] / cd).alias(f"{s}__mu"))
        out.append((cd / cnt).alias(f"{s}__dbar"))
    return pl.DataFrame(out)


def _clean(a: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(a), a, 0.0)


def _slope_vol(window: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per column OLS slope vs index (oldest 0 .. newest m-1) and ddof-1 sd of the valid
    entries (NaN = invalid); NaN when fewer than MIN_VALID valid entries."""
    m, s = window.shape
    slope = np.full(s, np.nan)
    vol = np.full(s, np.nan)
    x = np.arange(m, dtype=float)[:, None]
    ok = np.isfinite(window)
    n = ok.sum(axis=0)
    good = n >= MIN_VALID
    if not good.any():
        return slope, vol
    w = np.where(ok, 1.0, 0.0)
    y = np.where(ok, window, 0.0)
    xm = (w * x).sum(axis=0) / np.maximum(n, 1)
    ym = y.sum(axis=0) / np.maximum(n, 1)
    sxx = (w * (x - xm) ** 2).sum(axis=0)
    sxy = (w * (x - xm) * (y - ym)).sum(axis=0)
    syy = (w * (y - ym) ** 2).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = np.where(good & (sxx > 0), sxy / sxx, np.nan)
        vol = np.where(good, np.sqrt(syy / np.maximum(n - 1, 1)), np.nan)
    return slope, vol


def asof_series(
    rows: pl.DataFrame, stats: list[str], inp: SeriesInput = DEFAULT_INPUT
) -> pl.DataFrame:
    """The pre-declared representation (module docstring) for every stat in ``stats``.

    ``rows``: one row per (entity, game) INCLUDING non-played rows, with columns
    ``entity, game_id, game_date (Date), team, pos, played (bool)`` and ``{s}__num``,
    ``{s}__den`` for every stat. Returns ``entity, game_id, {s}_{suffix}..., adv_stint_n``.
    """
    n_stats = len(stats)
    prior = position_prior(rows, stats, inp)
    full = rows.join(prior, on=[inp.entity, "game_id"], how="left").sort(
        [inp.entity, "game_date", "game_id"]
    )
    ent = full[inp.entity].to_numpy()
    team = full[inp.team].to_numpy()
    played = full[inp.played].to_numpy().astype(bool)
    num = np.column_stack([full[f"{s}__num"].cast(pl.Float64).to_numpy() for s in stats])
    den = np.column_stack([full[f"{s}__den"].cast(pl.Float64).to_numpy() for s in stats])
    mu = np.column_stack([full[f"{s}__mu"].cast(pl.Float64).to_numpy() for s in stats])
    dbar = np.column_stack([full[f"{s}__dbar"].cast(pl.Float64).to_numpy() for s in stats])
    n = full.height
    lam = [0.5 ** (1.0 / h) for h in HALF_LIVES]
    lam_s = 0.5 ** (1.0 / STINT_HALF_LIFE)
    out = np.full((n, n_stats, len(REPR_SUFFIXES)), np.nan)
    stint_n = np.zeros(n)
    bounds = np.flatnonzero(np.r_[True, ent[1:] != ent[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        sn = [np.zeros(n_stats) for _ in HALF_LIVES]
        sd = [np.zeros(n_stats) for _ in HALF_LIVES]
        cn = np.zeros(n_stats)
        cd = np.zeros(n_stats)
        stn = np.zeros(n_stats)
        std = np.zeros(n_stats)
        cur_team = None
        n_stint = 0
        hist: list[np.ndarray] = []
        for i in range(a, b):
            if team[i] != cur_team:
                cur_team = team[i]
                stn = np.zeros(n_stats)
                std = np.zeros(n_stats)
                n_stint = 0
            k = PSEUDO * dbar[i]
            with np.errstate(divide="ignore", invalid="ignore"):
                for q in range(len(HALF_LIVES)):
                    out[i, :, q] = (sn[q] + k * mu[i]) / (sd[q] + k)
                career = (cn + k * mu[i]) / (cd + k)
                out[i, :, 5] = (stn + k * np.where(np.isfinite(career), career, mu[i])) / (std + k)
            window = np.array(hist[-WINDOW:]) if hist else np.empty((0, n_stats))
            if len(window):
                out[i, :, 3], out[i, :, 4] = _slope_vol(window)
            stint_n[i] = n_stint
            if played[i]:
                x, y = _clean(num[i]), _clean(den[i])
                for q in range(len(HALF_LIVES)):
                    sn[q] = lam[q] * (sn[q] + x)
                    sd[q] = lam[q] * (sd[q] + y)
                cn = cn + x
                cd = cd + y
                stn = lam_s * (stn + x)
                std = lam_s * (std + y)
                n_stint += 1
                valid = (
                    np.isfinite(num[i])
                    & np.isfinite(den[i])
                    & (den[i] >= 0.5 * dbar[i])
                    & (den[i] > 0)
                )
                with np.errstate(divide="ignore", invalid="ignore"):
                    hist.append(np.where(valid, num[i] / den[i], np.nan))
    cols = {
        f"{s}_{x}": out[:, j, q] for j, s in enumerate(stats) for q, x in enumerate(REPR_SUFFIXES)
    }
    return pl.DataFrame(
        {inp.entity: ent, "game_id": full["game_id"].to_numpy(), **cols, "adv_stint_n": stint_n}
    )
