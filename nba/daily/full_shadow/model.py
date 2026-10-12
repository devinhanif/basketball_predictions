# ruff: noqa: E501
"""RECENT_ROLE_FULL candidate, live: production's props heads + lower-tail ``pi`` refit WITH the five columns.

Rule: docs/prereg/RECENT_ROLE_FULL.md ("Arms", FULL), harness ``research/eval/recent_role_full_eval.py``
(the live path may not import it; tests/test_layering.py). Construction, all production code:

* heads: ``ContextResidualModel(stat, cfg, stat_feature_names(stat) + R_COLS)`` fitted on the stat frame of
  played rows dated strictly before the slate date (``fit_for_date``'s rule), seed 0, production params;
* ``pi``: the short-minutes classifier (``nba.props.lower_tail.fit_short_state``) on ``COMMON_FEATURES + R_COLS``
  (PI_RECENT_ROLE's CAND), one classifier shared by the four stats (the target does not depend on the stat);
  ``w_q`` (short-ratio quantiles) and the calibration z are production's;
* read-out: the stored mixture construction (``lower_quantiles('mixture')``) then ``to_integer_support``.

With ``R_COLS`` removed from both feature lists this IS ``lower_tail='mixture'`` production (tests prove it
bit-identical to ``ContextResidualModel`` with ``lower_tail='mixture'``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pickle
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from nba.daily.pplay_shadow.features import KEYS, R_COLS, load_min_features
from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    stat_feature_names,
    stat_frame,
)
from nba.props.lower_tail import MIN_SHORT_TRAIN, fit_short_state, predict_pi, short_flag

MODEL_VERSION = "full_v1"
PI_FEATURES: tuple[str, ...] = (*COMMON_FEATURES, *R_COLS)
SORT = ["game_date", "game_id", "player_id"]


def full_config(cfg: ContextResidualConfig | None = None) -> ContextResidualConfig:
    return replace(
        cfg or ContextResidualConfig(), lower_tail="mixture", lower_tail_stats=PROP_STATS
    )


def head_names(stat: str) -> list[str]:
    return [*stat_feature_names(stat), *R_COLS]


def feature_set_sha(cfg: ContextResidualConfig) -> str:
    """Fingerprint of the recipe: ordered head names per stat, ordered pi names, learner params, lightgbm."""
    body = {
        "version": MODEL_VERSION,
        "heads": {s: head_names(s) for s in PROP_STATS},
        "pi": list(PI_FEATURES),
        "params": [_params(cfg), _params(cfg, "binary")],
        "lightgbm": lgb.__version__,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def row_fingerprints(frame: pl.DataFrame, stat: str) -> list[str]:
    """Per-row sha256 (first 16 hex) of the head and pi feature values the model saw."""
    xh, xp = _matrix(frame, head_names(stat)), _matrix(frame, list(PI_FEATURES))
    return [
        hashlib.sha256(
            np.ascontiguousarray(a, dtype=np.float64).tobytes()
            + np.ascontiguousarray(b, dtype=np.float64).tobytes()
        ).hexdigest()[:16]
        for a, b in zip(xh, xp, strict=True)
    ]


@dataclass
class FullModel(ContextResidualModel):
    """``ContextResidualModel`` whose lower-tail classifier reads ``pi_names`` (COMMON + the five columns)."""

    pi_names: list[str] = field(default_factory=lambda: list(PI_FEATURES))
    shared_clf: Any = None

    def _fit_lower_tail(
        self,
        train: pl.DataFrame,
        cal_df: pl.DataFrame,
        mu: np.ndarray,
        s: np.ndarray,
        z: np.ndarray,
    ) -> dict[str, Any]:
        min10 = cal_df["min10"].to_numpy()
        state: dict[str, Any] = {
            "z": z,
            "c": cal_df["m"].to_numpy() + mu,
            "min10": min10,
            "short": short_flag(cal_df["minutes"].to_numpy(), min10),
            "pi_model": None,
            "w_q": None,
        }
        tr_short = short_flag(train["minutes"].to_numpy(), train["min10"].to_numpy())
        state.update(
            fit_short_state(
                _matrix(train, self.pi_names),
                tr_short,
                train["y"].to_numpy(),
                train["m"].to_numpy(),
                CRPS_TAUS,
                _params(self.cfg, "binary"),
                clf=self.shared_clf,
            )
        )
        return state

    def lower_tail_quantiles(
        self, df: pl.DataFrame, c: np.ndarray, s: np.ndarray, taus: np.ndarray
    ) -> np.ndarray:
        from nba.props.lower_tail import lower_quantiles

        assert self.lt_ is not None
        lt = self.lt_
        return lower_quantiles(
            "mixture",
            z_cal=lt["z"], c_cal=lt["c"], min10_cal=lt["min10"], short_cal=lt["short"],
            c=c, s=s, m_row=df["m"].to_numpy(), min10_row=df["min10"].to_numpy(),
            pi=predict_pi(lt, _matrix(df, self.pi_names)), w_q=lt["w_q"], taus=taus,
        )  # fmt: skip

    def pi(self, df: pl.DataFrame) -> np.ndarray:
        assert self.lt_ is not None
        return predict_pi(self.lt_, _matrix(df, self.pi_names))


def attach_columns(feats: pl.DataFrame, pgs: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Add the five recent-role columns (strictly pre-row, ``load_min_features``) and the realised
    ``minutes`` (box scores of games already in ``pgs``; NULL for tonight's placeholder rows) to ``feats``."""
    date_of = games.select("game_id", "game_date")
    played = pgs.join(date_of, on="game_id").select(
        pl.col("player_id").cast(pl.Int64),
        "game_id",
        "game_date",
        pl.col("minutes").cast(pl.Float64),
    )
    tgt = feats.select("game_id", pl.col("player_id").cast(pl.Int64), "game_date")
    load = load_min_features(played, tgt)
    minutes = pgs.select("game_id", "player_id", "minutes").unique(["game_id", "player_id"])
    out = (
        feats.drop("minutes", strict=False)
        .join(minutes, on=KEYS, how="left")
        .join(load, on=KEYS, how="left")
    )
    if out.height != feats.height:
        raise ValueError("attaching the recent-role columns changed the row count")
    return out


def fit(
    feats: pl.DataFrame, as_of: dt.date, cfg: ContextResidualConfig
) -> dict[str, ContextResidualModel]:
    """Fit the four FULL models on PLAYED rows with ``game_date < as_of`` (``fit_for_date``'s rule)."""
    hist = feats.filter(pl.col("game_date") < as_of)
    params = _params(cfg, "binary")
    frames = {s: stat_frame(hist, s).sort(SORT) for s in PROP_STATS}
    base = frames[PROP_STATS[0]]
    short = short_flag(base["minutes"].to_numpy(), base["min10"].to_numpy())
    clf: Any = None
    if MIN_SHORT_TRAIN <= int(short.sum()) < len(short):
        clf = lgb.LGBMClassifier(**{**params, "objective": "binary"}).fit(  # type: ignore[arg-type]
            _matrix(base, list(PI_FEATURES)), short.astype(int)
        )
    models: dict[str, ContextResidualModel] = {}
    for s_ in PROP_STATS:
        m = FullModel(s_, cfg, head_names(s_), shared_clf=clf)
        m.fit(frames[s_])
        models[s_] = m
    return models


def load_or_fit(
    feats: pl.DataFrame, as_of: dt.date, cfg: ContextResidualConfig, cache_dir: Path | None
) -> tuple[dict[str, ContextResidualModel], dict[str, Any]]:
    """Same-date cache pattern as production's ``_load_or_fit_models``: key = row count + newest date + recipe."""
    hist = feats.filter(pl.col("game_date") < as_of)
    fp: dict[str, Any] = {
        "n_rows": hist.height,
        "max_date": str(hist["game_date"].max()),
        "cfg": asdict(cfg),
        "recipe": feature_set_sha(cfg),
    }
    fp["cfg"].pop("report", None)
    fp["tip_source"] = cfg.report.tip_source
    if cache_dir is not None:
        meta_p, model_p = cache_dir / "meta.json", cache_dir / "models.pkl"
        if meta_p.exists() and model_p.exists():
            meta = json.loads(meta_p.read_text())
            if meta.get("fingerprint") == json.loads(json.dumps(fp, default=str)):
                with model_p.open("rb") as f:
                    return pickle.load(f), {"cache_hit": True, "fit_seconds": 0.0, **meta["info"]}  # noqa: S301
    t0 = time.perf_counter()
    models = fit(feats, as_of, cfg)
    secs = time.perf_counter() - t0
    info = {"n_train_rows": hist.height, "train_through": fp["max_date"]}
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with (cache_dir / "models.pkl").open("wb") as f:
            pickle.dump(models, f)
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "fingerprint": json.loads(json.dumps(fp, default=str)),
                    "info": {**info, "fit_seconds": secs},
                },
                sort_keys=True,
            )
        )
    return models, {"cache_hit": False, "fit_seconds": secs, **info}
