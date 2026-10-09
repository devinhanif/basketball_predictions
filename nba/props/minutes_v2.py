"""Minutes model v2: a pre-tip distribution of ``minutes | plays`` (docs/MINUTES_V2.md).

Default OFF and opt-in: nothing in production imports this module. ``attach_minutes_v2`` is the
flag (``enabled=False`` returns the feature frame unchanged), and ``stat_feature_names(stat)`` is
untouched unless a caller appends :data:`MV2_FEATURES` through its ``extra=`` hook.

Every input is strictly as-of: player-sequence features use only games dated before the target
row (the row's own minutes / fouls / final margin enter the state only AFTER its features are
captured), team rotation features are shifted by one team game, the OUT list is the real-tip
gated pre-tip report, and standings / game-type columns are schedule facts known pre-tip. DNP
rows (``minutes`` NULL) never enter a state or a label.

Model: a two-component mixture of minutes given plays (regular LightGBM mean/scale heads with a
split-conformal standardized residual quantile function, plus a short-minutes component reusing
:mod:`nba.props.lower_tail`) or its single-component ablation. All constants are frozen in the
pre-registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from scipy import stats

from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    MIN_PRIOR_PLAYED,
    ROTATION_MIN40,
    ContextResidualConfig,
    _matrix,
    _params,
    _sel,
    player_states,
    split_calibration,
)
from nba.props.lower_tail import (
    fit_short_state,
    mixture_quantiles,
    predict_pi,
    short_flag,
)

#: Frozen V2 feature group (docs/MINUTES_V2.md).
V2_FEATURES: tuple[str, ...] = (
    "pf36_h20",
    "pf5_rate_h40",
    "age",
    "own_b2b_gap",
    "ret_n",
    "ret_mean_min",
    "ret_ramp",
    "own_blow_gap",
    "team_blow_rate",
    "rot_hhi10",
    "rot_n15_10",
    "rot_top5_10",
    "n_out_starters",
    "out_max_min40",
    "win_pct_asof",
    "opp_win_pct_asof",
    "tanking_incentive",
    "in_playoff_pos",
    "in_playin",
    "cal_month",
    "is_playoff",
    "is_playin",
)
#: Columns of the minutes distribution appended to the props model (downstream path).
MV2_FEATURES: tuple[str, ...] = (
    "mv2_mean",
    "mv2_q10",
    "mv2_q50",
    "mv2_q90",
    "mv2_sd",
    "mv2_pi",
)
BASE_MINUTES_FEATURES: tuple[str, ...] = COMMON_FEATURES
V2_MINUTES_FEATURES: tuple[str, ...] = (*COMMON_FEATURES, *V2_FEATURES)

DEC20 = 0.5 ** (1.0 / 20.0)
DEC40 = 0.5 ** (1.0 / 40.0)
RETURN_GAP_DAYS = 14
BLOWOUT_MARGIN = 15
SHRINK_K = 3.0
MAX_MINUTES = 60.0
SCALE_FLOOR = 1.0
MIN_Z_REGULAR = 500
MIN_TRAIN = 1500
NO_RETURN = 99.0
TRUNC_MAX = 48.0


# --------------------------------------------------------------------------- flag


def attach_minutes_v2(
    feats: pl.DataFrame, mv2: pl.DataFrame | None, enabled: bool = False
) -> pl.DataFrame:
    """The opt-in flag. ``enabled=False`` (default) returns ``feats`` unchanged (byte-identical
    production); ``enabled=True`` left-joins the :data:`MV2_FEATURES` columns of ``mv2``
    (keyed ``game_id, player_id``)."""
    if not enabled:
        return feats
    if mv2 is None:
        raise ValueError("minutes_v2 enabled but no mv2 frame supplied")
    return feats.join(
        mv2.select("game_id", "player_id", *MV2_FEATURES), on=["game_id", "player_id"], how="left"
    )


# --------------------------------------------------------------------------- features


def _shrunk_gap(
    s_a: float, w_a: float, s_b: float, w_b: float, s_all: float, w_all: float
) -> float:
    """Shrunk mean(group a) - mean(group b), each toward the all-games mean (pseudo-count 3)."""
    if w_all <= 0.0:
        return float("nan")
    m_all = s_all / w_all
    return (s_a + SHRINK_K * m_all) / (w_a + SHRINK_K) - (s_b + SHRINK_K * m_all) / (w_b + SHRINK_K)


def player_sequence_features(played: pl.DataFrame) -> pl.DataFrame:
    """Per played row, features from the player's STRICTLY PRIOR games.

    ``played`` must be sorted by (player_id, game_date, game_id) and carry: player_id, game_id,
    season, game_date, minutes (> 0), pf, b2b, margin (final |margin| of that game), min10
    (as-of recency minutes of the row). Returns game_id, player_id and the player-level
    V2 columns."""
    n = played.height
    pid = played["player_id"].to_numpy()
    seas = played["season"].to_numpy()
    dates = played["game_date"].cast(pl.Int32).to_numpy()
    mins = played["minutes"].cast(pl.Float64).to_numpy()
    pf = played["pf"].cast(pl.Float64).fill_null(0.0).to_numpy()
    b2b = played["b2b"].cast(pl.Float64).fill_null(0.0).to_numpy()
    blow = (played["margin"].cast(pl.Float64).fill_null(0.0) >= BLOWOUT_MARGIN).cast(pl.Float64)
    blow_a = blow.to_numpy()
    min10 = played["min10"].cast(pl.Float64).to_numpy()
    o_pf36 = np.full(n, np.nan)
    o_pf5 = np.full(n, np.nan)
    o_b2b = np.full(n, np.nan)
    o_blow = np.full(n, np.nan)
    o_ret_n = np.full(n, NO_RETURN)
    o_ret_mean = np.full(n, np.nan)
    o_ret_ramp = np.full(n, np.nan)
    pf_num = min_den = s5 = d40 = 0.0
    s_all = w_all = s_b = w_b = s_n = w_n = 0.0
    s_x = w_x = s_y = w_y = 0.0
    ret_active = False
    ret_sum = 0.0
    ret_cnt = 0
    pre_min = float("nan")
    cnt = 0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            pf_num = min_den = s5 = d40 = 0.0
            s_all = w_all = s_b = w_b = s_n = w_n = 0.0
            s_x = w_x = s_y = w_y = 0.0
            ret_active, ret_sum, ret_cnt, pre_min, cnt = False, 0.0, 0, float("nan"), 0
        elif seas[i] != seas[i - 1]:
            ret_active, ret_sum, ret_cnt = False, 0.0, 0
        elif dates[i] - dates[i - 1] >= RETURN_GAP_DAYS:
            ret_active, ret_sum, ret_cnt, pre_min = True, 0.0, 0, min10[i]
        # ---- features from the state BEFORE row i
        if cnt > 0:
            if min_den > 0.0:
                o_pf36[i] = 36.0 * pf_num / min_den
            if d40 > 0.0:
                o_pf5[i] = s5 / d40
            o_b2b[i] = _shrunk_gap(s_b, w_b, s_n, w_n, s_all, w_all)
            o_blow[i] = _shrunk_gap(s_x, w_x, s_y, w_y, s_all, w_all)
        if ret_active:
            o_ret_n[i] = float(min(ret_cnt, 30))
            if ret_cnt > 0:
                o_ret_mean[i] = ret_sum / ret_cnt
                if pre_min == pre_min and pre_min > 0.0:
                    o_ret_ramp[i] = o_ret_mean[i] / pre_min
        # ---- update with row i's own data (after its features were captured)
        pf_num = DEC20 * pf_num + pf[i]
        min_den = DEC20 * min_den + mins[i]
        s5 = DEC40 * s5 + (1.0 if pf[i] >= 5 else 0.0)
        d40 = DEC40 * d40 + 1.0
        s_all = DEC40 * s_all + mins[i]
        w_all = DEC40 * w_all + 1.0
        s_b = DEC40 * s_b + mins[i] * b2b[i]
        w_b = DEC40 * w_b + b2b[i]
        s_n = DEC40 * s_n + mins[i] * (1.0 - b2b[i])
        w_n = DEC40 * w_n + (1.0 - b2b[i])
        s_x = DEC40 * s_x + mins[i] * blow_a[i]
        w_x = DEC40 * w_x + blow_a[i]
        s_y = DEC40 * s_y + mins[i] * (1.0 - blow_a[i])
        w_y = DEC40 * w_y + (1.0 - blow_a[i])
        if ret_active:
            ret_sum += mins[i]
            ret_cnt += 1
        cnt += 1
    return pl.DataFrame(
        {
            "game_id": played["game_id"],
            "player_id": played["player_id"],
            "pf36_h20": o_pf36,
            "pf5_rate_h40": o_pf5,
            "own_b2b_gap": o_b2b,
            "ret_n": o_ret_n,
            "ret_mean_min": o_ret_mean,
            "ret_ramp": o_ret_ramp,
            "own_blow_gap": o_blow,
        }
    )


def team_rotation_features(played: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): rotation tightness and blowout rate over the team's PRIOR games
    (shifted by one team game). ``played`` needs game_id, team_id, game_date, minutes, margin."""
    tg = played.group_by(["game_id", "team_id"]).agg(
        pl.col("game_date").first().alias("game_date"),
        pl.col("margin").first().alias("margin"),
        pl.col("minutes").sum().alias("tot"),
        (pl.col("minutes") ** 2).sum().alias("sq"),
        (pl.col("minutes") >= 15).sum().alias("n15"),
        pl.col("minutes").sort(descending=True).head(5).sum().alias("top5"),
    )
    tg = (
        tg.with_columns(
            (pl.col("sq") / (pl.col("tot") ** 2)).alias("hhi"),
            (pl.col("top5") / pl.col("tot")).alias("top5s"),
            (pl.col("margin") >= BLOWOUT_MARGIN).cast(pl.Float64).alias("blow"),
        )
        .sort(["team_id", "game_date", "game_id"])
        .with_columns(
            pl.col("hhi")
            .shift(1)
            .rolling_mean(10, min_samples=3)
            .over("team_id")
            .alias("rot_hhi10"),
            pl.col("n15")
            .cast(pl.Float64)
            .shift(1)
            .rolling_mean(10, min_samples=3)
            .over("team_id")
            .alias("rot_n15_10"),
            pl.col("top5s")
            .shift(1)
            .rolling_mean(10, min_samples=3)
            .over("team_id")
            .alias("rot_top5_10"),
            pl.col("blow")
            .shift(1)
            .rolling_mean(20, min_samples=5)
            .over("team_id")
            .alias("team_blow_rate"),
        )
    )
    return tg.select(
        "game_id", "team_id", "rot_hhi10", "rot_n15_10", "rot_top5_10", "team_blow_rate"
    )


def out_teammate_features(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_dates: dict[str, Any],
) -> pl.DataFrame:
    """Per (game_id, team_id, player_id): count of OUT teammates who were starters (as-of
    ``starter40 >= 0.5``) and the largest as-of ``min40`` among them, from the pre-tip OUT list,
    restricted like production's vacated features to rotation players (``min40 >= 12``, >= 5
    prior games). The row's own player is excluded."""
    post = states["post"]
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "out_team": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "starter40": _sel(post, 40.0, "starter"),
        }
    ).sort("game_date")
    recs = [
        (gid, pid, game_dates[gid])
        for gid, pids in flagged.items()
        if gid in game_dates
        for pid in pids
    ]
    empty = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "n_out_starters": pl.Float64,
            "out_max_min40": pl.Float64,
        }
    )
    if not recs:
        return empty
    left = pl.DataFrame(
        recs,
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date},
        orient="row",
    ).with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key"))
    j = (
        left.sort("_key")
        .join_asof(
            st.rename({"game_date": "_pd"}),
            left_on="_key",
            right_on="_pd",
            by="player_id",
            strategy="backward",
        )
        .filter((pl.col("n_after") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= ROTATION_MIN40))
        .select(
            "game_id",
            pl.col("player_id").alias("out_player"),
            "out_team",
            "min40",
            "starter40",
        )
    )
    rows = played.select("game_id", "team_id", "player_id")
    m = (
        rows.join(j, left_on=["game_id", "team_id"], right_on=["game_id", "out_team"], how="inner")
        .filter(pl.col("player_id") != pl.col("out_player"))
        .group_by(["game_id", "team_id", "player_id"])
        .agg(
            (pl.col("starter40") >= 0.5).sum().cast(pl.Float64).alias("n_out_starters"),
            pl.col("min40").max().alias("out_max_min40"),
        )
    )
    return m


def build_v2_features(
    feats: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    flagged: dict[str, set[int]],
    standings: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """The :data:`V2_FEATURES` frame keyed (game_id, player_id) for the rows of ``feats``
    (``build_features`` output). ``pgs`` must carry ``pf``; ``standings`` is the as-of
    output of ``nba.features.game_context.build_standings_features`` (None -> NaN columns)."""
    if "pf" not in pgs.columns:
        raise ValueError("pgs needs a 'pf' (personal fouls) column")
    g = games.with_columns(pl.col("game_date").cast(pl.Date))
    margin = g.select("game_id", (pl.col("home_pts") - pl.col("away_pts")).abs().alias("margin"))
    played = (
        pgs.join(g.select("game_id", "game_date", "season", "home_team"), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    st = player_states(
        played.select(
            "player_id",
            "team_id",
            "season",
            "game_date",
            "pts",
            "reb",
            "ast",
            "fg3m",
            "minutes",
            "starter",
        )
    )
    keys = feats.select("game_id", "player_id", "team_id", "opp_id", "b2b", "min10", "has_report")
    pl_rows = (
        played.select("game_id", "player_id", "team_id", "season", "game_date", "minutes", "pf")
        .join(margin, on="game_id", how="left")
        .join(
            keys.select("game_id", "player_id", "b2b", "min10"),
            on=["game_id", "player_id"],
            how="left",
        )
        .sort(["player_id", "game_date", "game_id"])
    )
    seq = player_sequence_features(pl_rows)
    team = team_rotation_features(
        played.select("game_id", "team_id", "game_date", "minutes").join(
            margin, on="game_id", how="left"
        )
    )
    game_dates = {str(r[0]): r[1] for r in g.select("game_id", "game_date").iter_rows()}
    out = out_teammate_features(played, st, flagged, game_dates)
    base = keys.join(seq, on=["game_id", "player_id"], how="left").join(
        team, on=["game_id", "team_id"], how="left"
    )
    base = base.join(out, on=["game_id", "team_id", "player_id"], how="left").with_columns(
        pl.when(pl.col("has_report") == 1)
        .then(pl.col("n_out_starters").fill_null(0.0))
        .otherwise(None)
        .alias("n_out_starters"),
        pl.when(pl.col("has_report") == 1)
        .then(pl.col("out_max_min40").fill_null(0.0))
        .otherwise(None)
        .alias("out_max_min40"),
    )
    if standings is not None:
        own = standings.select(
            "game_id",
            "team_id",
            pl.col("win_pct_asof").cast(pl.Float64),
            pl.col("tanking_incentive").cast(pl.Float64),
            pl.col("in_playoff_pos").cast(pl.Float64),
            pl.col("in_playin").cast(pl.Float64),
        )
        opp = standings.select(
            "game_id",
            pl.col("team_id").alias("opp_id"),
            pl.col("win_pct_asof").cast(pl.Float64).alias("opp_win_pct_asof"),
        )
        base = base.join(own, on=["game_id", "team_id"], how="left").join(
            opp, on=["game_id", "opp_id"], how="left"
        )
    else:
        base = base.with_columns(
            [
                pl.lit(None, dtype=pl.Float64).alias(c)
                for c in (
                    "win_pct_asof",
                    "opp_win_pct_asof",
                    "tanking_incentive",
                    "in_playoff_pos",
                    "in_playin",
                )
            ]
        )
    birth = static.select("player_id", "birth_date") if "birth_date" in static.columns else None
    gd = g.select("game_id", "game_date")
    base = base.join(gd, on="game_id", how="left")
    if birth is not None:
        base = base.join(birth.unique("player_id"), on="player_id", how="left").with_columns(
            (
                (pl.col("game_date") - pl.col("birth_date").cast(pl.Date)).dt.total_days() / 365.25
            ).alias("age")
        )
    else:
        base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias("age"))
    base = base.with_columns(
        pl.col("game_date").dt.month().cast(pl.Float64).alias("cal_month"),
        pl.col("game_id").str.starts_with("004").cast(pl.Float64).alias("is_playoff"),
        pl.col("game_id").str.starts_with("005").cast(pl.Float64).alias("is_playin"),
    )
    return base.select("game_id", "player_id", *[pl.col(c).cast(pl.Float64) for c in V2_FEATURES])


# --------------------------------------------------------------------------- model


@dataclass
class MinutesV2Model:
    """Mixture (``kind='mixture'``) or single-component (``'single'``) distribution of
    minutes given plays. ``fit`` takes PLAYED rows with ``minutes`` and ``min10`` columns."""

    names: list[str]
    kind: str = "mixture"
    cfg: ContextResidualConfig = field(default_factory=ContextResidualConfig)
    mean_head: lgb.LGBMRegressor | None = None
    scale_head: lgb.LGBMRegressor | None = None
    z_: np.ndarray | None = None
    state_: dict[str, Any] | None = None

    def fit(self, train: pl.DataFrame) -> MinutesV2Model:
        if self.kind not in ("mixture", "single"):
            raise ValueError(f"unknown kind {self.kind!r}")
        fit_df, cal_df = split_calibration(train, self.cfg)
        if fit_df.height < 200 or cal_df.height < 50:
            raise ValueError("not enough rows to fit + calibrate")
        short_fit = short_flag(fit_df["minutes"].to_numpy(), fit_df["min10"].to_numpy())
        short_cal = short_flag(cal_df["minutes"].to_numpy(), cal_df["min10"].to_numpy())
        if self.kind == "mixture":
            use_fit, use_cal = ~short_fit, ~short_cal
            if int(use_cal.sum()) < MIN_Z_REGULAR:
                use_cal = np.ones(cal_df.height, dtype=bool)
        else:
            use_fit = np.ones(fit_df.height, dtype=bool)
            use_cal = np.ones(cal_df.height, dtype=bool)
        fit_r = fit_df.filter(pl.Series(use_fit))
        r = fit_r["minutes"].to_numpy() - fit_r["min10"].to_numpy()
        x_fit = _matrix(fit_r, self.names)
        self.mean_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x_fit, r)  # type: ignore[arg-type]
        self.scale_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x_fit, np.abs(r))  # type: ignore[arg-type]
        cal_r = cal_df.filter(pl.Series(use_cal))
        x_cal = _matrix(cal_r, self.names)
        mu = np.asarray(self.mean_head.predict(x_cal))
        s = self._scale(x_cal)
        self.z_ = (cal_r["minutes"].to_numpy() - cal_r["min10"].to_numpy() - mu) / s
        if self.kind == "mixture":
            tr_short = short_flag(train["minutes"].to_numpy(), train["min10"].to_numpy())
            self.state_ = fit_short_state(
                _matrix(train, self.names),
                tr_short,
                train["minutes"].to_numpy(),
                train["min10"].to_numpy(),
                CRPS_TAUS,
                _params(self.cfg, "binary"),
            )
        return self

    def _scale(self, x: np.ndarray) -> np.ndarray:
        assert self.scale_head is not None
        return np.maximum(self.scale_head.predict(x), SCALE_FLOOR)

    def pi(self, df: pl.DataFrame) -> np.ndarray:
        if self.kind != "mixture" or self.state_ is None:
            return np.zeros(df.height)
        return predict_pi(self.state_, _matrix(df, self.names))

    def quantiles(self, df: pl.DataFrame, taus: np.ndarray = CRPS_TAUS) -> np.ndarray:
        """Quantiles ``[n, len(taus)]`` of minutes | plays, clipped to [0, 60]."""
        assert self.mean_head is not None and self.z_ is not None
        x = _matrix(df, self.names)
        min10 = df["min10"].to_numpy()
        c = min10 + np.asarray(self.mean_head.predict(x))
        s = self._scale(x)
        if self.kind == "single" or self.state_ is None:
            zq = np.asarray(np.quantile(self.z_, taus))
            q = np.maximum.accumulate(
                np.clip(c[:, None] + s[:, None] * zq[None, :], 0.0, None), axis=1
            )
        else:
            q = mixture_quantiles(
                c, s, min10, predict_pi(self.state_, x), self.z_, self.state_["w_q"], taus
            )
        return np.asarray(np.minimum(q, MAX_MINUTES))


def summarize_quantiles(q: np.ndarray, pi: np.ndarray) -> pl.DataFrame:
    """The :data:`MV2_FEATURES` columns from a 199-grid ``q`` (mean of the grid, three quantiles,
    sd of the grid) and ``pi``."""
    idx = {t: int(np.argmin(np.abs(CRPS_TAUS - t))) for t in (0.1, 0.5, 0.9)}
    return pl.DataFrame(
        {
            "mv2_mean": q.mean(axis=1),
            "mv2_q10": q[:, idx[0.1]],
            "mv2_q50": q[:, idx[0.5]],
            "mv2_q90": q[:, idx[0.9]],
            "mv2_sd": q.std(axis=1),
            "mv2_pi": pi,
        }
    )


# --------------------------------------------------------------------------- comparators


def recency_ratio_quantiles(
    cal_minutes: np.ndarray, cal_min10: np.ndarray, min10: np.ndarray, taus: np.ndarray = CRPS_TAUS
) -> np.ndarray:
    """Arm R: ``min10 * ratio_q``, ``ratio_q`` the empirical quantiles of
    ``minutes / max(min10, 0.5)`` over the calibration rows."""
    ratio = np.quantile(cal_minutes / np.maximum(cal_min10, 0.5), taus)
    return np.asarray(np.clip(np.maximum(min10, 0.5)[:, None] * ratio[None, :], 0.0, MAX_MINUTES))


def trunc_normal_quantiles(
    mu: np.ndarray, sigma: np.ndarray, taus: np.ndarray = CRPS_TAUS, upper: float = TRUNC_MAX
) -> np.ndarray:
    """Quantiles of Normal(mu, sigma) truncated to [0, upper] (production's conditional branch)."""
    sig = np.maximum(sigma, 1e-6)
    a = (0.0 - mu) / sig
    b = (upper - mu) / sig
    return np.asarray(
        stats.truncnorm.ppf(
            taus[None, :], a[:, None], b[:, None], loc=mu[:, None], scale=sig[:, None]
        )
    )
