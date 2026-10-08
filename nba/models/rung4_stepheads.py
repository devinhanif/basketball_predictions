"""Rung 4: learned multi-head possession-outcome step model (PyTorch).

CLAUDE.md architecture ladder, rung 4: "PyTorch multi-head net ... feeding
the sim heads". Per ``NEXT_SESSION.md`` item 4, this is scoped to the
possession-outcome heads only (NOT the DeepSets lineup/set encoder --
a prior experiment found lineup-archetype mix carried negligible signal,
R^2 ~= 0.016, and is not on this task's critical path). The eventual goal
is a THIRD router candidate alongside the possession sim (rung 3) and
season-average baseline in the props router, specifically for POINTS (the
one stat the sim does not currently beat season-average on -- see
``docs/RESULTS_2026-10-08.md``).

This module ships the model + the 3-method ``RungModel`` contract
(``nba/models/base.py``) today; it is NOT wired into
``nba.eval.run.RUNG_SPECS`` yet (that happens once real Colab-trained
weights exist -- this box is memory-bound/GPU-less, see
``nba/models/colab/README.md``). ``predict()`` below is therefore
deliberately scoped as a best-effort, honestly-documented approximation
(see its docstring) rather than a full possession-by-possession game
simulation using the learned heads -- that full sim-wiring is a follow-up
milestone once the heads are actually trained on real data.

Five heads (the "duration, outcome, ... zone, make, rebound, FT" steps
from CLAUDE.md's rung-3 description, minus "shooter" -- see above):

- ``outcome``: 6-way softmax over ``OUTCOME_CLASSES``.
- ``zone``: 3-way softmax over ``ZONE_CLASSES``, trained/scored only on
  shot-attempt rows (``is_shot_attempt`` mask) -- a non-shot possession
  has no zone.
- ``make``: binary (make/miss), same shot-attempt mask as ``zone``.
- ``rebound``: binary (offensive rebound occurred during the trip),
  trained/scored only on missed-shot rows (an ``oreb`` label is only
  meaningful after a miss).
- ``duration``: regression, seconds elapsed in the trip.

All five heads share one small MLP trunk over the as-of numeric features
in ``nba.features.possession_step_features.POSSESSION_STEP_FEATURE_COLUMNS``
-- see that module for the no-leakage proof of every feature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
import torch
from scipy.stats import norm
from torch import nn

from nba.features.possession_step_features import (
    OUTCOME_CLASSES,
    POSSESSION_STEP_FEATURE_COLUMNS,
    SHOT_ATTEMPT_OUTCOMES,
    ZONE_CLASSES,
)
from nba.models.base import RungModelBase
from nba.sim.possession_model import BASE_MEAN_PPP

#: Points value assigned to each outcome class for the margin
#: approximation in ``predict()`` -- FGM2=2, FGM3=3, a free-throw trip
#: averages ``LEAGUE_FT_PCT_DEFAULT`` (0.7822, see
#: ``nba.features.player_possession_features``) made shots at ~1.85 shots
#: per trip (And-1s/2-shot/3-shot fouls blended); documented, round
#: approximation -- FT_trip point value is NOT separately modeled by this
#: rung (see module docstring "scope").
_OUTCOME_POINT_VALUES: dict[str, float] = {
    "FGM2": 2.0,
    "FGM3": 3.0,
    "FGA_miss": 0.0,
    "TOV": 0.0,
    "FT_trip": 1.45,  # ~1.85 FTA/trip * 0.7822 FT% (both league-wide defaults)
    "other": 0.0,
}
_OUTCOME_POINT_VALUE_VEC: list[float] = [_OUTCOME_POINT_VALUES[c] for c in OUTCOME_CLASSES]

#: Margin standard deviation used by the Normal-CDF win-probability
#: approximation in ``predict()`` -- CLAUDE.md "Secondary" metrics target
#: "margin SD (target ~12)" for a full-game margin distribution; reused
#: here as a documented constant rather than a fitted one (this rung has
#: no margin-level training data yet -- see module docstring).
_MARGIN_SD_DEFAULT = 12.0

#: Neutral intra-game state substituted at game-level predict time, since
#: ``predict()`` is not (yet) a possession-by-possession simulation that
#: tracks real period/clock/score state -- see module docstring. Values
#: are "typical mid-game, tied" possession context: period 2 (first half),
#: clock_start at the midpoint of a 12-minute period, score_diff 0.
_NEUTRAL_PERIOD = 2.0
_NEUTRAL_CLOCK_START = 360.0
_NEUTRAL_SCORE_DIFF = 0.0

#: Columns ``predict()`` expects on its input ``df`` -- matchup-level as-of
#: team ratings, same convention/names as
#: ``nba.models.rung3_sim.POSSESSION_SIM_FEATURE_COLUMNS`` so this rung can
#: be dropped into the same eval-harness feature frame
#: (``nba.features.possession_features.build_possession_matchup_features``)
#: without a separate feature-join step.
PREDICT_FEATURE_COLUMNS: list[str] = [
    "home_off_rtg_prior",
    "home_def_rtg_prior",
    "home_pace_prior",
    "away_off_rtg_prior",
    "away_def_rtg_prior",
    "away_pace_prior",
    "league_avg_ppp_asof",
]


@dataclass
class StepHeadsBatch:
    """Tensors for one training batch -- see :func:`frame_to_tensors`."""

    x: torch.Tensor  # (N, n_features), standardized
    outcome_idx: torch.Tensor  # (N,) long, index into OUTCOME_CLASSES
    is_shot: torch.Tensor  # (N,) bool
    zone_idx: torch.Tensor  # (N,) long, index into ZONE_CLASSES (0 if not a shot; masked)
    made: torch.Tensor  # (N,) float 0/1
    is_miss: torch.Tensor  # (N,) bool, oreb is only scored on misses
    oreb: torch.Tensor  # (N,) float 0/1
    duration: torch.Tensor  # (N,) float seconds


class StepHeadsNet(nn.Module):
    """Shared MLP trunk + five possession-outcome heads."""

    def __init__(self, n_features: int, hidden: int = 32) -> None:
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.outcome_head = nn.Linear(hidden, len(OUTCOME_CLASSES))
        self.zone_head = nn.Linear(hidden, len(ZONE_CLASSES))
        self.make_head = nn.Linear(hidden, 1)
        self.rebound_head = nn.Linear(hidden, 1)
        self.duration_head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        h = self.trunk(x)
        return {
            "outcome_logits": self.outcome_head(h),
            "zone_logits": self.zone_head(h),
            "make_logit": self.make_head(h).squeeze(-1),
            "rebound_logit": self.rebound_head(h).squeeze(-1),
            "duration": self.duration_head(h).squeeze(-1),
        }


def feature_matrix(
    df: pl.DataFrame, feature_mean: np.ndarray | None = None, feature_std: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standardize ``POSSESSION_STEP_FEATURE_COLUMNS`` into a numpy matrix.

    Returns ``(x, mean, std)``. Pass ``feature_mean``/``feature_std`` back
    in at predict time to standardize against the *training* set's
    statistics, not the (possibly tiny/different-distribution) predict-time
    set -- the usual train/serve standardization discipline.
    """
    raw = df.select(POSSESSION_STEP_FEATURE_COLUMNS).to_numpy().astype(np.float64)
    mean = raw.mean(axis=0) if feature_mean is None else feature_mean
    std = raw.std(axis=0) if feature_std is None else feature_std
    std = np.where(std < 1e-8, 1.0, std)
    return (raw - mean) / std, mean, std


def frame_to_tensors(
    df: pl.DataFrame, feature_mean: np.ndarray | None = None, feature_std: np.ndarray | None = None
) -> tuple[StepHeadsBatch, np.ndarray, np.ndarray]:
    """Build one :class:`StepHeadsBatch` from a possession-step training frame.

    ``df`` must have the columns produced by
    ``nba.features.possession_step_features.build_possession_step_training_frame``.
    """
    x, mean, std = feature_matrix(df, feature_mean, feature_std)

    outcome_to_idx = {c: i for i, c in enumerate(OUTCOME_CLASSES)}
    zone_to_idx = {c: i for i, c in enumerate(ZONE_CLASSES)}

    outcomes = df.get_column("outcome").to_list()
    outcome_idx = torch.tensor([outcome_to_idx[o] for o in outcomes], dtype=torch.long)
    is_shot = torch.tensor([o in SHOT_ATTEMPT_OUTCOMES for o in outcomes], dtype=torch.bool)
    is_miss = torch.tensor([o == "FGA_miss" for o in outcomes], dtype=torch.bool)

    zones = df.get_column("shot_zone").to_list()
    zone_idx = torch.tensor([zone_to_idx.get(z, 0) for z in zones], dtype=torch.long)

    made = torch.tensor(df.get_column("made_shot").to_numpy().astype(np.float32))
    oreb = torch.tensor(df.get_column("oreb").to_numpy().astype(np.float32))
    duration = torch.tensor(df.get_column("duration_s").to_numpy().astype(np.float32))

    batch = StepHeadsBatch(
        x=torch.tensor(x, dtype=torch.float32),
        outcome_idx=outcome_idx,
        is_shot=is_shot,
        zone_idx=zone_idx,
        made=made,
        is_miss=is_miss,
        oreb=oreb,
        duration=duration,
    )
    return batch, mean, std


#: Relative weight on the duration regression loss, so its (much larger,
#: seconds-scale) MSE doesn't drown out the four classification losses
#: (all O(1) cross-entropy/BCE). Documented, round default -- not tuned.
DURATION_LOSS_WEIGHT = 0.001


def compute_losses(net: StepHeadsNet, batch: StepHeadsBatch) -> dict[str, torch.Tensor]:
    """Per-head losses plus ``total`` -- masked heads return 0 if their mask is empty."""
    out = net(batch.x)
    zero = torch.tensor(0.0)

    outcome_loss = nn.functional.cross_entropy(out["outcome_logits"], batch.outcome_idx)

    if batch.is_shot.any():
        zone_loss = nn.functional.cross_entropy(
            out["zone_logits"][batch.is_shot], batch.zone_idx[batch.is_shot]
        )
        make_loss = nn.functional.binary_cross_entropy_with_logits(
            out["make_logit"][batch.is_shot], batch.made[batch.is_shot]
        )
    else:
        zone_loss, make_loss = zero, zero

    if batch.is_miss.any():
        rebound_loss = nn.functional.binary_cross_entropy_with_logits(
            out["rebound_logit"][batch.is_miss], batch.oreb[batch.is_miss]
        )
    else:
        rebound_loss = zero

    duration_loss = nn.functional.mse_loss(out["duration"], batch.duration)

    total = (
        outcome_loss + zone_loss + make_loss + rebound_loss + DURATION_LOSS_WEIGHT * duration_loss
    )
    return {
        "outcome": outcome_loss,
        "zone": zone_loss,
        "make": make_loss,
        "rebound": rebound_loss,
        "duration": duration_loss,
        "total": total,
    }


def seed_everything(seed: int) -> None:
    """Seeds numpy + torch (CPU and CUDA) -- CLAUDE.md "deterministic: seed everything"."""
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


class StepHeadsRung(RungModelBase):
    """Rung-4 step-head net, wrapped in the ``RungModel`` 3-method contract.

    ``fit``/``predict`` here operate at a DIFFERENT granularity than rungs
    0-3's ``(matchup_df, y)`` game-level contract: ``fit`` trains on a
    POSSESSION-level training frame (see module docstring), and ``y`` is
    unused/ignored (the five step-head labels live inside ``train_df``
    itself, not in a separate ``y`` array) -- documented here rather than
    silently reinterpreted, per CLAUDE.md "every rung implements
    predict()/get_metrics()/get_config()". ``predict`` still returns a
    1-D array of P(home win) in [0, 1] as the Protocol requires, via the
    documented Normal-CDF margin approximation (see module docstring and
    ``PREDICT_FEATURE_COLUMNS``) -- NOT a full possession simulation using
    the learned heads; that wiring is a follow-up milestone once the heads
    are trained on real (Colab) data.
    """

    def __init__(
        self,
        seed: int = 0,
        hidden: int = 32,
        lr: float = 1e-3,
        n_epochs: int = 1,
        margin_sd: float = _MARGIN_SD_DEFAULT,
    ) -> None:
        super().__init__(seed=seed)
        self.hidden = hidden
        self.lr = lr
        self.n_epochs = n_epochs
        self.margin_sd = margin_sd
        self.net: StepHeadsNet | None = None
        self.feature_mean_: np.ndarray | None = None
        self.feature_std_: np.ndarray | None = None
        self.n_train_possessions_: int = 0
        self.last_losses_: dict[str, float] = {}

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:  # noqa: ARG002 (y unused, see docstring)
        seed_everything(self.seed)
        self.n_train_possessions_ = train_df.height
        if train_df.height == 0:
            self.net = None
            return
        batch, mean, std = frame_to_tensors(train_df)
        self.feature_mean_, self.feature_std_ = mean, std
        net = StepHeadsNet(n_features=len(POSSESSION_STEP_FEATURE_COLUMNS), hidden=self.hidden)
        optimizer = torch.optim.Adam(net.parameters(), lr=self.lr)
        losses: dict[str, torch.Tensor] = {}
        for _ in range(self.n_epochs):
            optimizer.zero_grad()
            losses = compute_losses(net, batch)
            losses["total"].backward()  # type: ignore[no-untyped-call]
            optimizer.step()
        self.net = net
        self.last_losses_ = {k: float(v.detach()) for k, v in losses.items()}

    def _predicted_ppp(self, off_rtg: np.ndarray, def_rtg: np.ndarray) -> np.ndarray:
        """Expected points-per-possession for one side, from the learned outcome head.

        Builds a neutral-context possession-feature row per game (see
        ``_NEUTRAL_*`` constants), runs it through the trained ``outcome``
        head, and takes the expectation of ``_OUTCOME_POINT_VALUE_VEC``
        under the predicted class distribution. Falls back to the
        documented league-average PPP if the net hasn't been trained yet.
        """
        if self.net is None or self.feature_mean_ is None or self.feature_std_ is None:
            return np.full(off_rtg.shape, BASE_MEAN_PPP)
        n = off_rtg.shape[0]
        raw = np.column_stack(
            [
                np.full(n, _NEUTRAL_PERIOD),
                np.full(n, _NEUTRAL_CLOCK_START),
                np.full(n, _NEUTRAL_SCORE_DIFF),
                np.ones(n),  # off_is_home -- irrelevant to the offense's own PPP read here
                off_rtg,
                def_rtg,
                np.full(n, 99.6),  # off_pace_prior -- not used by the outcome head's point value
                def_rtg,
                off_rtg,
                np.full(n, BASE_MEAN_PPP),
            ]
        )
        x = (raw - self.feature_mean_) / self.feature_std_
        with torch.no_grad():
            logits = self.net(torch.tensor(x, dtype=torch.float32))["outcome_logits"]
            probs = torch.softmax(logits, dim=-1).numpy()
        point_values = np.asarray(_OUTCOME_POINT_VALUE_VEC)
        return probs @ point_values

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        for col in PREDICT_FEATURE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(BASE_MEAN_PPP, dtype=pl.Float64).alias(col))
        rows = df.select(PREDICT_FEATURE_COLUMNS).fill_null(BASE_MEAN_PPP)
        home_off = rows.get_column("home_off_rtg_prior").to_numpy()
        home_def = rows.get_column("home_def_rtg_prior").to_numpy()
        away_off = rows.get_column("away_off_rtg_prior").to_numpy()
        away_def = rows.get_column("away_def_rtg_prior").to_numpy()
        home_pace = rows.get_column("home_pace_prior").to_numpy()
        away_pace = rows.get_column("away_pace_prior").to_numpy()

        home_ppp = self._predicted_ppp(home_off, away_def)
        away_ppp = self._predicted_ppp(away_off, home_def)
        pace = (home_pace + away_pace) / 2.0
        margin_mean = (home_ppp - away_ppp) * pace
        return np.asarray(norm.cdf(margin_mean / self.margin_sd), dtype=float)

    def get_config(self) -> dict[str, object]:
        return {
            "model_name": "rung4_stepheads",
            "rung": 4,
            "seed": self.seed,
            "hidden": self.hidden,
            "lr": self.lr,
            "n_epochs": self.n_epochs,
            "margin_sd": self.margin_sd,
            "n_train_possessions": self.n_train_possessions_,
            "outcome_classes": OUTCOME_CLASSES,
            "zone_classes": ZONE_CLASSES,
            "feature_columns": POSSESSION_STEP_FEATURE_COLUMNS,
            "last_losses": self.last_losses_,
            "scope_note": (
                "shooter head deferred (needs DeepSets lineup encoder, found negligible "
                "signal in a prior experiment); predict() uses a Normal-CDF margin "
                "approximation at neutral intra-game context, not a full possession sim "
                "-- see module docstring"
            ),
        }
