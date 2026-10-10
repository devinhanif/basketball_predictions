"""Injury-adjusted MOV-Elo: win probability that knows who is OUT tonight.

Plain Elo only learns about an absence after the team starts losing. This
rung keeps the (frozen) MOV-Elo rating and adds a linear logit correction
for the as-of value of the players the official injury report lists as out::

    logit p(home) = elo_diff * ln(10)/400
                    + b * (V_out_away - V_out_home)/10
                    + g * (V_doubt_away - V_doubt_home)/10

``V`` sums the as-of value of flagged players on a side (doubtful players
enter at ``doubtful_weight`` = 0.5). ``b`` and ``g`` are the only fitted
parameters (2-parameter ridge logistic with the Elo logit as a fixed
offset), refit walk-forward so the coefficients used for a game never saw
that game or any later one.

As-of discipline (mirrors ``player_possession_features.py``): every number
that feeds a game on date ``D`` comes from games with ``game_date < D``
(player value state, league rate) and from the latest official report row
with ``as_of <= D + tip proxy - lead`` (``nba.sim.usage_redistribution``'s
leakage rule, reused unchanged). Nothing from the game itself enters.

Player value (cheap and robust, documented limitations)
-------------------------------------------------------
Box composite per game ``c = pts + reb + ast + stl + blk - tov`` (no shot
attempts are stored, so scoring efficiency is NOT captured). With
exponentially-decayed (per game played) sums ``S_c, S_m, W`` over a
player's prior games::

    rate  = (S_c + k_m * r_league) / (S_m + k_m)          # shrunk per-minute
    mins  = (S_m + k_g * m0) / (W + k_g)                  # shrunk minutes/game
    value = max(rate - rho * r_league, 0) * mins          # points/game above
                                                          # replacement

``r_league`` is the cumulative league per-minute composite over games
strictly before the date, so low-sample players are pulled to the league
rate (and hence to ~zero value) rather than fitting noise. Box-based rather
than on/off because lineup on/off from possessions is far noisier per
player at the sample sizes available before a game.

The post-hoc ORACLE (players who played the team's previous game but not
this one, from the box score) exists only to bound what is achievable in
principle. It uses the game's own outcome-side information and is never a
candidate.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, replace
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl
from scipy.optimize import minimize

from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
    rotation_flagged_by_team,
)
from nba.models.base import RungModelBase
from nba.models.common import clip_prob
from nba.models.rung0_baselines import MovEloBaseline

ELO_TO_LOGIT = math.log(10.0) / 400.0
VALUE_SCALE = 10.0  # features are value/VALUE_SCALE (value is points/game)
FEATURE_COLS: tuple[str, str] = ("d_out", "d_doubt")


@dataclass(frozen=True)
class ValueConfig:
    decay_per_game: float = 0.97
    rate_pseudo_minutes: float = 400.0
    minutes_pseudo_games: float = 8.0
    prior_minutes: float = 8.0
    replacement_frac: float = 0.80


@dataclass(frozen=True)
class InjuryFeatureConfig:
    value: ValueConfig = field(default_factory=ValueConfig)
    tipoff_hour_et: float = 19.0
    lead_minutes: int = 60
    tip_source: str = "real"  # "real" (production): scheduled tip; "proxy19": 19:00 ET proxy
    doubtful_weight: float = 0.5
    report_table: str = "player_availability"
    rotation_min_avg: float = 20.0
    rotation_min_games: int = 5


# --------------------------------------------------------------------------
# Elo replay (identical to the walk-forward MovEloBaseline fold harness)
# --------------------------------------------------------------------------


def sequential_elo_logits(games: pl.DataFrame, params: dict[str, float]) -> np.ndarray:
    """Pre-game MOV-Elo logit of every game, in the row order of ``games``.

    Reproduces the fold harness exactly: all games of a date are predicted
    from ratings built from STRICTLY earlier dates, then that date's games
    update the ratings (season regression fires on the first update of a new
    season, as in ``MovEloBaseline.fit``). ``games`` needs game_id,
    game_date, season, home_team, away_team, home_pts, away_pts.
    """
    model = MovEloBaseline(seed=0, **params)
    ordered = games.with_row_index("_row").sort(["game_date", "game_id"])
    rows = ordered.select(
        ["_row", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"]
    ).rows()
    out = np.zeros(games.height, dtype=float)
    i, n = 0, len(rows)
    current_season: object = None
    while i < n:
        j = i
        while j < n and rows[j][1] == rows[i][1]:
            j += 1
        group = rows[i:j]
        for r_idx, _d, _s, h, a, _hp, _ap in group:
            diff = (
                model.ratings_.get(h, model.initial_rating)
                + model.home_advantage_elo
                - model.ratings_.get(a, model.initial_rating)
            )
            out[r_idx] = diff * ELO_TO_LOGIT
        for _r, _d, season, h, a, hp, ap in group:
            if current_season is not None and season != current_season:
                model._regress_ratings_to_mean()
            current_season = season
            hr = model.ratings_.get(h, model.initial_rating)
            ar = model.ratings_.get(a, model.initial_rating)
            p_home = model._win_prob(h, a)
            actual = 1.0 if hp > ap else 0.0
            pre = hr + model.home_advantage_elo
            win_diff = (pre - ar) if actual >= 0.5 else (ar - pre)
            upd = model.k_factor * model._mov_multiplier(float(hp - ap), win_diff)
            upd *= actual - p_home
            model.ratings_[h] = hr + upd
            model.ratings_[a] = ar - upd
        i = j
    return out


# --------------------------------------------------------------------------
# As-of player value
# --------------------------------------------------------------------------


def build_value_state(stats: pl.DataFrame, cfg: ValueConfig) -> pl.DataFrame:
    """Per (player, game played): decayed state AFTER that game.

    ``stats``: player_id, team_id, game_date, minutes, comp (composite).
    Only rows with minutes > 0 count. Output columns: player_id, team_id,
    game_date, s_c, s_m, w, n_after, avg_min_after.
    """
    played = stats.filter(pl.col("minutes") > 0).sort(["player_id", "game_date"])
    recs: list[tuple[int, int, dt.date, float, float, float, int, float]] = []
    cur_pid: int | None = None
    s_c = s_m = w = 0.0
    n = 0
    tot_m = 0.0
    for pid, team, d, mins, comp in played.select(
        ["player_id", "team_id", "game_date", "minutes", "comp"]
    ).iter_rows():
        if pid != cur_pid:
            cur_pid, s_c, s_m, w, n, tot_m = int(pid), 0.0, 0.0, 0.0, 0, 0.0
        lam = cfg.decay_per_game
        s_c = s_c * lam + float(comp)
        s_m = s_m * lam + float(mins)
        w = w * lam + 1.0
        n += 1
        tot_m += float(mins)
        recs.append((int(pid), int(team), d, s_c, s_m, w, n, tot_m / n))
    return pl.DataFrame(
        recs,
        schema={
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "game_date": pl.Date,
            "s_c": pl.Float64,
            "s_m": pl.Float64,
            "w": pl.Float64,
            "n_after": pl.Int64,
            "avg_min_after": pl.Float64,
        },
        orient="row",
    )


def league_rate_by_date(stats: pl.DataFrame) -> pl.DataFrame:
    """Cumulative league composite/minute INCLUDING each date (query strictly after)."""
    daily = (
        stats.filter(pl.col("minutes") > 0)
        .group_by("game_date")
        .agg(pl.col("comp").sum().alias("c"), pl.col("minutes").sum().alias("m"))
        .sort("game_date")
    )
    return daily.with_columns(
        (pl.col("c").cum_sum() / pl.col("m").cum_sum()).alias("league_rate")
    ).select(["game_date", "league_rate"])


def asof_values(
    pairs: pl.DataFrame,
    state: pl.DataFrame,
    league: pl.DataFrame,
    cfg: ValueConfig,
) -> pl.DataFrame:
    """As-of value for (game_id, player_id, game_date) rows, from games strictly before.

    Adds team_id (the player's team in his last prior game), n_after and
    value. Players with no prior game get value 0 and n_after 0.
    """
    key = (pl.col("game_date") - pl.duration(days=1)).alias("_key")
    left = pairs.with_columns(key).sort("_key")
    right = state.rename({"game_date": "_prior_date"}).sort("_prior_date")
    j = left.join_asof(
        right, left_on="_key", right_on="_prior_date", by="player_id", strategy="backward"
    )
    lg = league.rename({"game_date": "_ld"}).sort("_ld")
    j = j.sort("_key").join_asof(lg, left_on="_key", right_on="_ld", strategy="backward")
    rl = pl.col("league_rate").fill_null(0.0)
    rate = (pl.col("s_c") + cfg.rate_pseudo_minutes * rl) / (
        pl.col("s_m") + cfg.rate_pseudo_minutes
    )
    mins = (pl.col("s_m") + cfg.minutes_pseudo_games * cfg.prior_minutes) / (
        pl.col("w") + cfg.minutes_pseudo_games
    )
    value = (rate - cfg.replacement_frac * rl).clip(lower_bound=0.0) * mins
    return j.with_columns(
        pl.when(pl.col("n_after").is_null()).then(0.0).otherwise(value).alias("value"),
        pl.col("n_after").fill_null(0),
    ).select(["game_id", "player_id", "game_date", "team_id", "n_after", "value"])


# --------------------------------------------------------------------------
# Per-game injury features
# --------------------------------------------------------------------------


def load_stats_frame(con: duckdb.DuckDBPyConnection, max_season: int) -> pl.DataFrame:
    """Box rows for games with season <= ``max_season`` (holdout never loaded)."""
    return con.execute(
        "SELECT s.player_id, s.team_id, g.game_id, g.game_date, COALESCE(s.minutes,0) AS minutes, "
        "COALESCE(s.pts,0)+COALESCE(s.reb,0)+COALESCE(s.ast,0)+COALESCE(s.stl,0)"
        "+COALESCE(s.blk,0)-COALESCE(s.tov,0) AS comp "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= ?",
        [max_season],
    ).pl()


def load_games_frame(con: duckdb.DuckDBPyConnection, max_season: int) -> pl.DataFrame:
    df = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE season <= ? AND home_pts IS NOT NULL ORDER BY game_date, game_id",
        [max_season],
    ).pl()
    return df.with_columns((pl.col("home_pts") > pl.col("away_pts")).cast(pl.Int64).alias("y"))


def build_injury_features(
    con: duckdb.DuckDBPyConnection,
    games: pl.DataFrame,
    stats: pl.DataFrame,
    cfg: InjuryFeatureConfig,
    *,
    report_rows: pl.DataFrame | None = None,
    with_oracle: bool = True,
) -> pl.DataFrame:
    """One row per game: official-report and oracle injury-value differentials.

    Columns: game_id, d_out, d_doubt (away minus home, so positive favors
    home), n_rot_out (rotation players flagged OUT, both teams), has_report,
    report_as_of (snapshot used), d_oracle (ORACLE, post-hoc box-score DNPs).
    ``con`` must expose ``cfg.report_table`` (e.g. an ATTACHed backfill).
    ``report_rows`` (optional) replaces the table read; ``with_oracle=False``
    skips the post-hoc oracle (d_oracle = 0). Defaults keep old behavior.
    """
    state = build_value_state(stats, cfg.value)
    league = league_rate_by_date(stats)
    gids = games["game_id"].to_list()
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }

    base = ReportTriggerConfig(
        statuses=("out",),
        tipoff_hour_et=cfg.tipoff_hour_et,
        lead_minutes=cfg.lead_minutes,
        table=cfg.report_table,
        tip_source=cfg.tip_source,
    )
    rows = load_report_rows(con, base, game_ids=gids) if report_rows is None else report_rows
    if cfg.tip_source == "real":
        from nba.features.game_tipoff import attach_real_tips

        rows = attach_real_tips(rows)
    out_flag, used = latest_pretip_flagged(rows, base)
    dbt_cfg = ReportTriggerConfig(
        statuses=("doubtful",),
        tipoff_hour_et=cfg.tipoff_hour_et,
        lead_minutes=cfg.lead_minutes,
        table=cfg.report_table,
        tip_source=cfg.tip_source,
    )
    dbt_flag, _ = latest_pretip_flagged(rows, dbt_cfg)

    recs: list[tuple[str, int, dt.date, str]] = []
    for status, flag in (("out", out_flag), ("doubtful", dbt_flag)):
        for gid, pids in flag.items():
            for pid in pids:
                recs.append((gid, pid, info[gid][0], status))
    if recs:
        pairs = pl.DataFrame(
            recs,
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "game_date": pl.Date,
                "status": pl.Utf8,
            },
            orient="row",
        )
        vals = asof_values(pairs.drop("status"), state, league, cfg.value).join(
            pairs.select(["game_id", "player_id", "status"]), on=["game_id", "player_id"]
        )
    else:
        vals = pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "game_date": pl.Date,
                "team_id": pl.Int64,
                "n_after": pl.Int64,
                "value": pl.Float64,
                "status": pl.Utf8,
            }
        )
    side = _side_sums(vals, info, cfg.doubtful_weight)

    rot_state = _rotation_state(state)
    rot = rotation_flagged_by_team(
        out_flag, info, rot_state, cfg.rotation_min_avg, cfg.rotation_min_games
    )
    n_rot: dict[str, int] = {}
    for (gid, _team), pids in rot.items():
        n_rot[gid] = n_rot.get(gid, 0) + len(pids)

    oracle = _oracle_diff(stats, state, league, games, cfg.value) if with_oracle else {}

    out_rows = []
    for gid in gids:
        vo_h, vo_a, vd_h, vd_a = side.get(gid, (0.0, 0.0, 0.0, 0.0))
        snap = used.get(gid)
        out_rows.append(
            (
                gid,
                (vo_a - vo_h) / VALUE_SCALE,
                (vd_a - vd_h) / VALUE_SCALE,
                n_rot.get(gid, 0),
                snap is not None,
                snap,
                oracle.get(gid, 0.0) / VALUE_SCALE,
            )
        )
    return pl.DataFrame(
        out_rows,
        schema={
            "game_id": pl.Utf8,
            "d_out": pl.Float64,
            "d_doubt": pl.Float64,
            "n_rot_out": pl.Int64,
            "has_report": pl.Boolean,
            "report_as_of": pl.Datetime("us"),
            "d_oracle": pl.Float64,
        },
        orient="row",
    )


def _rotation_state(state: pl.DataFrame) -> pl.DataFrame:
    return state.select(["player_id", "team_id", "game_date", "avg_min_after", "n_after"])


def _side_sums(
    vals: pl.DataFrame, info: dict[str, tuple[dt.date, int, int]], doubtful_weight: float
) -> dict[str, tuple[float, float, float, float]]:
    acc: dict[str, list[float]] = {}
    for gid, team, status, value in vals.select(
        ["game_id", "team_id", "status", "value"]
    ).iter_rows():
        _d, home, away = info[str(gid)]
        if team is None or int(team) not in (home, away):
            continue
        slot = acc.setdefault(str(gid), [0.0, 0.0, 0.0, 0.0])
        off = 0 if status == "out" else 2
        w = 1.0 if status == "out" else doubtful_weight
        slot[off + (0 if int(team) == home else 1)] += w * float(value)
    return {g: (s[0], s[1], s[2], s[3]) for g, s in acc.items()}


def _oracle_diff(
    stats: pl.DataFrame,
    state: pl.DataFrame,
    league: pl.DataFrame,
    games: pl.DataFrame,
    vcfg: ValueConfig,
) -> dict[str, float]:
    """ORACLE (post-hoc): away-minus-home value of players who played the
    team's previous game but have no minutes in this one."""
    played = stats.filter(pl.col("minutes") > 0)
    by_team_game: dict[tuple[int, str], set[int]] = {}
    for pid, team, gid in played.select(["player_id", "team_id", "game_id"]).iter_rows():
        by_team_game.setdefault((int(team), str(gid)), set()).add(int(pid))
    prev_game: dict[tuple[int, str], str] = {}
    last: dict[int, str] = {}
    for gid, h, a in (
        games.sort(["game_date", "game_id"])
        .select(["game_id", "home_team", "away_team"])
        .iter_rows()
    ):
        for t in (int(h), int(a)):
            if t in last:
                prev_game[(t, str(gid))] = last[t]
        last[int(h)] = str(gid)
        last[int(a)] = str(gid)
    date_of = dict(games.select(["game_id", "game_date"]).iter_rows())
    recs = []
    for gid, h, a in games.select(["game_id", "home_team", "away_team"]).iter_rows():
        for t in (int(h), int(a)):
            pg = prev_game.get((t, str(gid)))
            if pg is None:
                continue
            missing = by_team_game.get((t, pg), set()) - by_team_game.get((t, str(gid)), set())
            for pid in missing:
                recs.append((str(gid), pid, date_of[gid], t))
    if not recs:
        return {}
    pairs = pl.DataFrame(
        recs,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "game_date": pl.Date,
            "_team": pl.Int64,
        },
        orient="row",
    )
    vals = asof_values(pairs.drop("_team"), state, league, vcfg).join(
        pairs.select(["game_id", "player_id", "_team"]), on=["game_id", "player_id"]
    )
    home_of = {str(g): int(h) for g, h in games.select(["game_id", "home_team"]).iter_rows()}
    out: dict[str, float] = {}
    for gid, team, v in vals.select(["game_id", "_team", "value"]).iter_rows():
        sign = -1.0 if int(team) == home_of[str(gid)] else 1.0
        out[str(gid)] = out.get(str(gid), 0.0) + sign * float(v)
    return out


# --------------------------------------------------------------------------
# Coefficient fit + model
# --------------------------------------------------------------------------


def fit_injury_coefs(
    x: np.ndarray, offset: np.ndarray, y: np.ndarray, ridge_lambda: float
) -> np.ndarray:
    """Ridge logistic MLE of ``logit p = offset + x @ coef`` (offset fixed)."""
    k = x.shape[1]
    if x.shape[0] == 0:
        return np.zeros(k)

    def nll(c: np.ndarray) -> tuple[float, np.ndarray]:
        z = offset + x @ c
        # log(1+e^z) - y z, stable
        loss = float(np.sum(np.logaddexp(0.0, z) - y * z) + 0.5 * ridge_lambda * np.sum(c * c))
        p = 1.0 / (1.0 + np.exp(-z))
        grad = x.T @ (p - y) + ridge_lambda * c
        return loss, grad

    res = minimize(nll, np.zeros(k), jac=True, method="L-BFGS-B")
    return np.asarray(res.x, dtype=float)


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype=float)


class InjuryEloModel(RungModelBase):
    """Frozen MOV-Elo + ridge-fitted injury-value logit correction.

    ``fit(train_df, y)``: train_df needs the games columns of
    :func:`sequential_elo_logits` plus ``d_out`` / ``d_doubt``. ``predict``
    needs home_team, away_team, d_out, d_doubt; it uses the end-of-train
    ratings (never updated by the predicted rows).
    """

    def __init__(
        self,
        elo_params: dict[str, float],
        seed: int = 0,
        ridge_lambda: float = 5.0,
        feature_cols: tuple[str, ...] = FEATURE_COLS,
    ) -> None:
        super().__init__(seed=seed)
        self.elo_params = dict(elo_params)
        self.ridge_lambda = ridge_lambda
        self.feature_cols = feature_cols
        self.elo_ = MovEloBaseline(seed=seed, **self.elo_params)
        self.coef_: np.ndarray = np.zeros(len(feature_cols))

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        ordered = train_df.sort(["game_date", "game_id"])
        y_o = (ordered["home_pts"] > ordered["away_pts"]).cast(pl.Float64).to_numpy()
        self.elo_.fit(ordered, y_o.astype(int))
        offset = sequential_elo_logits(ordered, self.elo_params)
        x = ordered.select(list(self.feature_cols)).to_numpy()
        self.coef_ = fit_injury_coefs(x, offset, y_o, self.ridge_lambda)

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        elo_p = self.elo_.predict(df)
        logit = np.log(elo_p / (1.0 - elo_p))
        x = df.select(list(self.feature_cols)).to_numpy()
        return clip_prob(sigmoid(logit + x @ self.coef_))

    def get_config(self) -> dict[str, Any]:
        return {
            "model_name": "rung0_injury_elo",
            "rung": 0,
            "seed": self.seed,
            "elo_params": self.elo_params,
            "ridge_lambda": self.ridge_lambda,
            "feature_cols": list(self.feature_cols),
            "coef": [float(c) for c in self.coef_],
        }


# --------------------------------------------------------------------------
# Forward (live) prediction
# --------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")
_NO_CUTOFF_HOURS = 72.0  # disables the proxy-tip filter (real tip filter applied first)


def _utc_to_et_naive(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=dt.UTC).astimezone(_ET).replace(tzinfo=None)


def predict_games(
    con: duckdb.DuckDBPyConnection,
    as_of_ts: dt.datetime,
    games: pl.DataFrame,
    report_rows: pl.DataFrame,
    *,
    elo_params: dict[str, float],
    cfg: InjuryFeatureConfig | None = None,
    ridge_lambda: float = 5.0,
    min_signal_games: int = 150,
) -> pl.DataFrame:
    """Forward win probabilities for upcoming ``games`` (leak-free, stateless).

    ``as_of_ts``: naive UTC "now". ``games``: game_id, game_date (Date),
    home_team, away_team, tipoff (naive UTC; null = unknown). ``report_rows``:
    game_id, player_id, status, as_of (naive US-Eastern, the report clock),
    as in :func:`nba.sim.usage_redistribution.load_report_rows`.

    Leakage rules: (1) a report row counts for a game only if
    ``as_of <= real tip-off - lead_minutes`` and ``as_of <= now``; (2) Elo
    ratings, player values and the ridge coefficients ``(b, g)`` use only
    games with ``game_date < min(games.game_date)``; the coefficients are
    refit from scratch on that history on every call (month-block walk-forward
    would use the same data), so nothing stale or future can be carried.
    Games with no usable report fall back to MOV-Elo (``primary_model``).
    """
    cfg = cfg or InjuryFeatureConfig()
    cutoff = games["game_date"].min()
    hist = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_date < ? AND home_pts > 0 AND away_pts > 0 "
        "ORDER BY game_date, game_id",
        [cutoff],
    ).pl()
    stats = con.execute(
        "SELECT s.player_id, s.team_id, g.game_id, g.game_date, COALESCE(s.minutes,0) AS minutes, "
        "COALESCE(s.pts,0)+COALESCE(s.reb,0)+COALESCE(s.ast,0)+COALESCE(s.stl,0)"
        "+COALESCE(s.blk,0)-COALESCE(s.tov,0) AS comp "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.game_date < ?",
        [cutoff],
    ).pl()

    # coefficients: history only (proxy-tip features, same as the walk-forward eval)
    coef = np.zeros(len(FEATURE_COLS))
    n_signal = 0
    if hist.height:
        hist = hist.with_columns(
            (pl.col("home_pts") > pl.col("away_pts")).cast(pl.Int64).alias("y")
        )
        feats = build_injury_features(con, hist, stats, cfg, with_oracle=False)
        fr = hist.join(feats, on="game_id", how="left").sort(["game_date", "game_id"])
        x = fr.select(list(FEATURE_COLS)).fill_null(0.0).to_numpy()
        n_signal = int((np.abs(x).sum(axis=1) > 0).sum())
        if n_signal >= min_signal_games:
            offset = sequential_elo_logits(fr, elo_params)
            coef = fit_injury_coefs(x, offset, fr["y"].to_numpy().astype(float), ridge_lambda)

    # Elo state for the forward games (season-boundary regression if needed)
    elo = MovEloBaseline(seed=0, **elo_params)
    if hist.height:
        elo.fit(hist, hist["y"].to_numpy())
        if int(hist["season"].max()) < int(games["season"].max()):  # type: ignore[arg-type]
            elo._regress_ratings_to_mean()
    p_mov = np.asarray(elo.predict(games.select(["home_team", "away_team"])), dtype=float)

    # report rows: real tip-off cutoff per game
    now_et = _utc_to_et_naive(as_of_ts)
    tips = {
        str(g): (_utc_to_et_naive(t) if t is not None else None)
        for g, t in games.select(["game_id", "tipoff"]).iter_rows()
    }
    lead = dt.timedelta(minutes=cfg.lead_minutes)
    keep: list[bool] = []
    for gid, asof in report_rows.select(["game_id", "as_of"]).iter_rows():
        tip = tips.get(str(gid))
        keep.append(tip is not None and asof <= tip - lead and asof <= now_et)
    usable = (
        report_rows.filter(pl.Series(keep, dtype=pl.Boolean)) if report_rows.height else report_rows
    )
    # forward rows were already gated on the live tip above: no second tip gate here
    fcfg = replace(cfg, tipoff_hour_et=_NO_CUTOFF_HOURS, lead_minutes=0, tip_source="proxy19")
    fgames = games.select(["game_id", "game_date", "home_team", "away_team"])
    ff = build_injury_features(
        con, fgames, stats, fcfg, report_rows=usable.select(
            ["game_id", "player_id", "status", "as_of", "game_date"]
        ) if usable.height else usable, with_oracle=False,
    )  # fmt: skip
    fdf = (
        games.select(["game_id"])
        .with_columns(pl.Series("p_mov_elo", p_mov))
        .join(
            ff.select(["game_id", "d_out", "d_doubt", "has_report", "report_as_of"]),
            on="game_id",
            how="left",
        )
    )
    xf = fdf.select(list(FEATURE_COLS)).to_numpy()
    logit = np.log(p_mov / (1.0 - p_mov))
    p_inj = clip_prob(sigmoid(logit + xf @ coef))
    has = fdf["has_report"].to_list()
    reasons: list[str | None] = []
    for gid, h in zip(fdf["game_id"].to_list(), has, strict=True):
        if h:
            reasons.append(None)
        elif tips.get(str(gid)) is None:
            reasons.append("unknown_tipoff")
        else:
            reasons.append("no_report_published_by_tipoff_minus_lead")
    return fdf.with_columns(
        pl.Series("p_injury_elo", p_inj),
        pl.Series("primary_model", ["rung0_injury_elo" if h else "rung0_mov_elo" for h in has]),
        pl.Series("fallback_reason", reasons, dtype=pl.Utf8),
        pl.lit(float(coef[0])).alias("coef_out"),
        pl.lit(float(coef[1])).alias("coef_doubt"),
        pl.lit(hist.height).alias("n_train_games"),
        pl.lit(n_signal).alias("n_signal_games"),
    )
