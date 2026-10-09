"""Draft-slot rookie minutes prior, blended into the minutes projection.

Motivation (docs/CONTEXT_SCREEN_PLAYERS_2026-10-08.md, Screen B): a ridge on draft slot beat a
position-only mean for first-season minutes per game by 0.75 min/g. The minutes model's
cold-start branch shrinks a rookie toward the league default (24 min); this module replaces that
prior, for rookie rows only, with a pedigree-based prediction.

PRE-REGISTERED RULE (written 2026-10-08, BEFORE the first run; mirrored in
docs/BACKLOG_SMALL_2026-10-08.md; not edited after results)
------------------------------------------------------------------------------------------
* Rookie row: season s in {2023, 2024}; the player's first played game in the DB is in s
  (knowable as-of: no earlier played game exists); undrafted (both draft fields NULL) or
  0 <= s - draft_year <= 3 with a known pick (class 2022: draft_year == 2022 only, because
  undrafted 2022 rookies are unidentifiable); and ``n_played_prior < 40``. Every other row is
  untouched by construction (asserted: predictions are identical).
* Prior: ridge (alpha by 5-fold CV over a fixed grid) on standardised ln(pick) (undrafted =
  ln 61), undrafted flag, age on 1 Oct of the season, height, weight and primary-position
  dummies. Target: a rookie's first-season mean minutes per played game, trained only on rookies
  with >= 5 played games from classes strictly BEFORE the evaluated class (walk-forward by
  class: 2023 <- class 2022; 2024 <- classes 2022 and 2023). The >= 5 game filter keeps
  rotation survivors (a known upward bias for low picks).
* Blend: ``mu_new = mu_current + k/(n+k) * (prior - default_mu)``, n = ``n_played_prior``,
  k = ``k_mu``: the existing empirical-Bayes shrinkage with the prior replacing the league
  default; all other adjustments (context, role change) are preserved. N and k are not tuned.
* Metric: minutes MAE of ``mu`` on played rookie rows (n_played_prior < 40), paired against the
  current minutes model (``predict_minutes``, default config), game-clustered 95% bootstrap CI.
* KEEP iff the pooled (2023 + 2024) paired MAE delta (ridge - current) has CI upper bound < 0,
  AND its point estimate is < 0 in each class separately, AND non-rookie predictions are
  unchanged. Secondary (descriptive, never decides): delta vs a position-only prior, a
  player-clustered CI, bias. Downstream props CRPS is not run (needs the full recency/context
  pipeline); a KEEP is a minutes-level result only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge, RidgeCV

from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist
from nba.props.metrics import paired_score_delta_ci

ROOKIE_MAX_PLAYED = 40  # N: prior applies while n_played_prior < N
MIN_TRAIN_GAMES = 5  # a training rookie needs >= this many played games
EVAL_CLASSES = (2023, 2024)
FIRST_IDENTIFIABLE_UNDRAFTED = 2023  # data start 2022: earlier "first seasons" are veterans
RIDGE_ALPHAS = (1.0, 3.0, 10.0, 30.0, 100.0, 300.0)
POS_LEVELS = ("Guard", "Forward", "Center", "Unknown")
MAX_SEASON = 2024  # season 2025 is never read
DESIGN_COLS = ("pick", "age", "body", "pos")


def first_seasons(played: pl.DataFrame) -> pl.DataFrame:
    """Per player: first season with a played game. ``played``: player_id, season, minutes."""
    p = played.filter(pl.col("minutes") > 0)
    return p.group_by("player_id").agg(pl.col("season").min().alias("first_season"))


def class_member_expr() -> pl.Expr:
    """Rookie-class membership from first_season, draft_year, draft_pick and undrafted."""
    fs, dy = pl.col("first_season"), pl.col("draft_year")
    drafted = (
        (pl.col("undrafted") == 0)
        & pl.col("draft_pick").is_not_null()
        & dy.is_not_null()
        & (pl.when(fs == 2022).then(dy == 2022).otherwise((fs - dy >= 0) & (fs - dy <= 3)))
    )
    undrafted = (pl.col("undrafted") == 1) & (fs >= FIRST_IDENTIFIABLE_UNDRAFTED)
    return drafted | undrafted


def with_class_flag(df: pl.DataFrame, static: pl.DataFrame, firsts: pl.DataFrame) -> pl.DataFrame:
    """Add ``first_season`` and ``is_class`` (rookie-class member) to ``df`` (has player_id)."""
    cols = ["player_id", "draft_year", "draft_pick", "undrafted"]
    out = df.join(firsts, on="player_id", how="left").join(
        static.select(cols), on="player_id", how="left", suffix="_st"
    )
    return out.with_columns(class_member_expr().fill_null(False).alias("is_class"))


def _age_season_start(df: pl.DataFrame, season_col: str) -> pl.Series:
    """Age (years) on 1 October of the season; NULL without a birth date."""
    seasons = df[season_col].to_list()
    births = df["birth_date"].to_list()
    vals = [
        None if b is None else (date(int(s), 10, 1) - b).days / 365.25
        for s, b in zip(seasons, births, strict=True)
    ]
    return pl.Series("age_ss", vals, dtype=pl.Float64)


def design_matrix(df: pl.DataFrame, season_col: str, cols: tuple[str, ...] = DESIGN_COLS) -> Any:
    """Raw (unimputed, unscaled) design matrix; NaN where a field is missing."""
    parts: list[np.ndarray] = []
    if "pick" in cols:
        parts += [
            df["log_pick"].cast(pl.Float64).to_numpy(),
            df["undrafted"].cast(pl.Float64).to_numpy(),
        ]
    if "age" in cols:
        parts.append(_age_season_start(df, season_col).to_numpy())
    if "body" in cols:
        parts += [df["height_in"].cast(pl.Float64).to_numpy(), df["weight_lb"].to_numpy()]
    if "pos" in cols:
        parts += [(df["position_primary"] == c).to_numpy().astype(float) for c in POS_LEVELS]
    return np.column_stack([np.asarray(p, dtype=float) for p in parts])


@dataclass
class RookieMinutesPrior:
    """Ridge prior for a rookie's mean minutes per played game."""

    cols: tuple[str, ...] = DESIGN_COLS
    alphas: tuple[float, ...] = RIDGE_ALPHAS
    mean_: float = float("nan")
    n_train: int = 0
    alpha_: float = float("nan")
    _med: Any = None
    _mu: Any = None
    _sd: Any = None
    _ridge: Any = None

    def _prep(self, x: np.ndarray) -> np.ndarray:
        x = x.copy()
        idx = np.where(np.isnan(x))
        x[idx] = np.take(self._med, idx[1])
        return np.asarray((x - self._mu) / self._sd)

    def fit(self, train: pl.DataFrame, y: np.ndarray) -> RookieMinutesPrior:
        """``train``: one row per rookie with pedigree columns and a ``season`` column."""
        x = design_matrix(train, "season", self.cols)
        self._med = np.nanmedian(x, axis=0)
        filled = x.copy()
        idx = np.where(np.isnan(filled))
        filled[idx] = np.take(self._med, idx[1])
        self._mu = filled.mean(axis=0)
        sd = filled.std(axis=0)
        self._sd = np.where(sd > 0, sd, 1.0)
        z = (filled - self._mu) / self._sd
        self.n_train = len(y)
        self.mean_ = float(np.mean(y))
        cv = min(5, max(2, len(y) // 2))
        self.alpha_ = float(RidgeCV(alphas=self.alphas, cv=cv).fit(z, y).alpha_)
        self._ridge = Ridge(alpha=self.alpha_).fit(z, y)
        return self

    def predict(self, df: pl.DataFrame, season_col: str = "season") -> np.ndarray:
        x = design_matrix(df, season_col, self.cols)
        return np.asarray(self._ridge.predict(self._prep(x)), dtype=float)

    def get_config(self) -> dict[str, Any]:
        return {
            "cols": list(self.cols),
            "alpha": self.alpha_,
            "n_train": self.n_train,
            "train_mean": self.mean_,
        }


def rookie_training_table(
    played: pl.DataFrame, static: pl.DataFrame, class_season: int
) -> pl.DataFrame:
    """One row per rookie of ``class_season``: pedigree + ``mpg`` (mean minutes per played game).

    ``played``: player_id, season, minutes (minutes > 0 rows only are used)."""
    p = played.filter((pl.col("minutes") > 0) & (pl.col("season") == class_season))
    agg = p.group_by("player_id").agg(
        pl.len().alias("gp"), pl.col("minutes").mean().alias("mpg"), pl.col("season").first()
    )
    firsts = first_seasons(played.filter(pl.col("season") <= class_season))
    agg = with_class_flag(agg, static, firsts)
    agg = agg.filter(
        pl.col("is_class")
        & (pl.col("first_season") == class_season)
        & (pl.col("gp") >= MIN_TRAIN_GAMES)
    )
    ped = static.select(
        "player_id",
        "log_pick",
        "undrafted",
        "height_in",
        "weight_lb",
        "birth_date",
        "position_primary",
    )
    return agg.drop("undrafted").join(ped, on="player_id", how="left").sort("player_id")


def position_prior(train: pl.DataFrame, target: pl.DataFrame) -> np.ndarray:
    """Position-only comparator: training-class mean mpg by primary position (grand mean else)."""
    g = train.group_by("position_primary").agg(pl.col("mpg").mean().alias("m"))
    mp = dict(zip(g["position_primary"].to_list(), g["m"].to_list(), strict=True))
    grand = float(train["mpg"].mean())  # type: ignore[arg-type]
    return np.array([mp.get(p, grand) for p in target["position_primary"].to_list()], dtype=float)


def apply_prior(
    dists: list[MinutesHurdleDist],
    n_played: np.ndarray,
    prior_mu: np.ndarray,
    rookie: np.ndarray,
    cfg: MinutesModelConfig,
) -> list[MinutesHurdleDist]:
    """Rookie rows: ``mu += k/(n+k) * (prior - default_mu)``; other rows returned unchanged."""
    k = cfg.k_mu
    w = k / (n_played + k)
    out: list[MinutesHurdleDist] = []
    for i, d in enumerate(dists):
        if not rookie[i]:
            out.append(d)
            continue
        mu = d.mu + w[i] * (prior_mu[i] - cfg.default_mu)
        out.append(MinutesHurdleDist(d.p_play, float(mu), d.sigma, d.max_minutes))
    return out


def _ci(a: np.ndarray, b: np.ndarray, cluster: np.ndarray, n_boot: int) -> list[float]:
    ci = paired_score_delta_ci(a, b, n_boot=n_boot, cluster_ids=cluster)
    return [float(ci.point), float(ci.lo), float(ci.hi)]


def evaluate_rookie_minutes(
    features: pl.DataFrame,
    actual: pl.DataFrame,
    played: pl.DataFrame,
    static: pl.DataFrame,
    cfg: MinutesModelConfig | None = None,
    n_boot: int = 2000,
) -> dict[str, Any]:
    """Walk-forward-by-class evaluation. ``features``: ``build_minutes_features`` output;
    ``actual``: game_id, player_id, minutes (evaluation only); ``played``: player_id, season,
    minutes; ``static``: ``static_pedigree`` output. Rows of season > 2024 are dropped."""
    from nba.props.minutes import predict_minutes

    cfg = cfg or MinutesModelConfig()
    feats = features.filter(pl.col("season") <= MAX_SEASON)
    played = played.filter(pl.col("season") <= MAX_SEASON)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    firsts = first_seasons(played)
    base = predict_minutes(feats, cfg)
    n_played = feats["n_played_prior"].fill_null(0.0).to_numpy()
    f2 = with_class_flag(feats.select("player_id", "season"), static, firsts)
    is_rookie = (
        f2["is_class"].to_numpy()
        & (f2["first_season"] == f2["season"]).fill_null(False).to_numpy()
        & (n_played < ROOKIE_MAX_PLAYED)
        & feats["season"].is_in(list(EVAL_CLASSES)).to_numpy()
    )
    ped = feats.select("player_id", "season").join(
        static.select(
            "player_id",
            "log_pick",
            "undrafted",
            "height_in",
            "weight_lb",
            "birth_date",
            "position_primary",
        ),  # fmt: skip
        on="player_id",
        how="left",
        maintain_order="left",
    )
    ridge_mu = np.full(feats.height, np.nan)
    pos_mu = np.full(feats.height, np.nan)
    configs: dict[str, Any] = {}
    for cls in EVAL_CLASSES:
        tr = pl.concat(
            [rookie_training_table(played, static, c) for c in range(2022, cls)],
            how="diagonal_relaxed",
        )
        prior = RookieMinutesPrior().fit(tr, tr["mpg"].to_numpy())
        configs[str(cls)] = prior.get_config()
        m = feats["season"].to_numpy() == cls
        ridge_mu[m] = prior.predict(ped.filter(pl.col("season") == cls))
        pos_mu[m] = position_prior(tr, ped.filter(pl.col("season") == cls))
    safe_ridge = np.where(np.isnan(ridge_mu), cfg.default_mu, ridge_mu)
    safe_pos = np.where(np.isnan(pos_mu), cfg.default_mu, pos_mu)
    d_ridge = apply_prior(base, n_played, safe_ridge, is_rookie, cfg)
    d_pos = apply_prior(base, n_played, safe_pos, is_rookie, cfg)
    mu_b = np.array([d.mu for d in base])
    mu_r = np.array([d.mu for d in d_ridge])
    mu_p = np.array([d.mu for d in d_pos])
    untouched = bool(np.array_equal(mu_b[~is_rookie], mu_r[~is_rookie]))

    act = feats.select("game_id", "player_id", "season").join(
        actual.select("game_id", "player_id", pl.col("minutes").alias("act")),
        on=["game_id", "player_id"],
        how="left",
        maintain_order="left",
    )
    y = act["act"].fill_null(0.0).to_numpy()
    sel = is_rookie & (y > 0)
    gid = feats["game_id"].to_numpy()
    pid = feats["player_id"].to_numpy()
    seas = feats["season"].to_numpy()

    def block(mask: np.ndarray) -> dict[str, Any]:
        eb, er, ep = (np.abs(m[mask] - y[mask]) for m in (mu_b, mu_r, mu_p))
        return {
            "n_rows": int(mask.sum()),
            "n_players": int(len(set(pid[mask].tolist()))),
            "mae_current": float(eb.mean()),
            "mae_position_prior": float(ep.mean()),
            "mae_ridge_prior": float(er.mean()),
            "bias_current": float((mu_b[mask] - y[mask]).mean()),
            "bias_ridge": float((mu_r[mask] - y[mask]).mean()),
            "delta_ridge_vs_current": _ci(er, eb, gid[mask], n_boot),
            "delta_ridge_vs_position": _ci(er, ep, gid[mask], n_boot),
            "delta_position_vs_current": _ci(ep, eb, gid[mask], n_boot),
            "delta_ridge_vs_current_player_clustered": _ci(er, eb, pid[mask], n_boot),
        }

    res: dict[str, Any] = {"pooled": block(sel), "by_class": {}, "prior_configs": configs}
    for cls in EVAL_CLASSES:
        res["by_class"][str(cls)] = block(sel & (seas == cls))
    pooled = res["pooled"]["delta_ridge_vs_current"]
    per_class = [res["by_class"][str(c)]["delta_ridge_vs_current"][0] for c in EVAL_CLASSES]
    res["non_rookie_unchanged"] = untouched
    res["keep"] = bool(pooled[2] < 0 and all(v < 0 for v in per_class) and untouched)
    return res
