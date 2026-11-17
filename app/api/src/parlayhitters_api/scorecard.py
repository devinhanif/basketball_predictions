"""The scorecard: means of the scores the settle step stored, with n and a date-clustered interval.

Nothing here scores a forecast. ``settled_scores`` already holds one log loss, Brier or CRPS per
settled row (nba/daily/settle.py:122-154); honesty_data.load_scores sums them per date in DuckDB.
This module turns those sums into a mean, its n rows and n dates, and a percentile bootstrap
interval that resamples whole game dates (ratio of sums, so a big slate weighs as much as its
rows; the estimator of docs/FORWARD_PREREG_2026_27.md:281-285, here with B = 2000 and a 95% level
because nothing here is a checkpoint). The seed is fixed (2026) and re-set for every series, so
the same export always gives the same numbers.

What may be shown is decided here, conservatively:

* only the production props model (``props_context_residual``) and its declared baseline
  (``props_recency_v1``) are scored, and their paired difference;
* every row of a slate dated on or after ``MODEL_LOCK_FROM`` (LINE_CAL_LIVE, the same constant
  trackrecord.py and scenarios.py use) gives counts only;
* every arm that is the subject of a frozen forward test, and the game-winner pair (the WIN
  tripwire of FORWARD_PREREG section 7), gives counts only: n settled so far, never a score, a
  difference or a better/worse word, until its scheduled look; so does any model not declared;
* ten or fewer dates never decide (FORWARD_PREREG_2026_27.md:333-334): the call is
  ``too_few_dates`` whatever the interval says.
"""

from __future__ import annotations

import random
from typing import Any

from .honesty_data import PROP_TARGETS, WIN_TARGET, Scores
from .honesty_models import (
    Comparison,
    MeanCI,
    ModelScore,
    NotAvailable,
    Scorecard,
    ScoreRule,
    TargetCard,
)
from .trackrecord import MODEL_LOCK_FROM

SEED = 2026
RESAMPLES = 2000
LEVEL = 0.95
MIN_DECISIVE_DATES = 11  # "ten or fewer dates are never decisive"

PRODUCTION_PROPS = "props_context_residual"
BASELINE_PROPS = "props_recency_v1"
PRODUCTION_WIN = "rung0_injury_elo"
BASELINE_WIN = "rung0_mov_elo"

#: Models whose scores are under a frozen forward test (or treated as such): counts only.
_FP = "docs/FORWARD_PREREG_2026_27.md"
_WIN = f"WIN tripwire, FORWARD_PREREG_2026_27 section 7 ({_FP}:249-261)"
FROZEN: dict[str, str] = {
    "props_context_residual_int": f"FORWARD_PREREG_2026_27 arm INT ({_FP}:23)",
    "props_context_residual_lt": f"FORWARD_PREREG_2026_27 arm LT ({_FP}:24)",
    "props_context_residual_t30": f"FORWARD_PREREG_2026_27 arm T30 ({_FP}:22)",
    "props_context_residual_t15": "FORWARD_T15_2026_27 (docs/prereg/FORWARD_T15_2026_27.md:83-91)",
    "props_context_residual_late": (
        "LATE_REPORT shadow: no frozen rule was found for it; withheld as if frozen "
        "(conservative, see docs/DECISIONS.md 2026-11-02 21:01)"
    ),
    PRODUCTION_WIN: _WIN,
    BASELINE_WIN: _WIN,
}

NO_MARKET = "no market probability is stored beside settled rows in this export; none is derived"
LOCK_TEXT = (
    "Slates from 2026-10-30 on are inside the LINE_CAL_LIVE locked test until its single look "
    "(about Dec 1): their rows are counted, never scored here."
)

NOT_AVAILABLE: tuple[NotAvailable, ...] = (
    NotAvailable(
        metric="integer-support CRPS (rps), the pre-registered primary",
        needs="forward_scores_elig.rps (nba/daily/settle.py:264-418) in the export",
        why="the export carries forward_scores only (nba/export/reader.py:335-352)",
    ),
    NotAvailable(
        metric="T-60 eligible scores (the pre-registered population)",
        needs="forward_scores_elig rows, or a stored tip-off for every scored game",
        why="tip-off is exported only for the lookback window; older rows cannot be checked",
    ),
    NotAvailable(
        metric="pinball19",
        needs="forward_scores_elig.pinball19",
        why="not exported",
    ),
    NotAvailable(metric="PIT", needs="forward_scores_elig PIT bounds", why="not exported"),
    NotAvailable(
        metric="q10-q90 interval coverage",
        needs="forward_scores_elig q10/q90 hit flags, or the realised value beside each grid",
        why="not exported; the app does not re-score stored grids against outcomes",
    ),
    NotAvailable(
        metric="market probability per scored row",
        needs="a de-vigged market probability stored on each settled_scores row",
        why="no market probability is stored for scored rows; the app does not derive one",
    ),
    NotAvailable(
        metric="CLV (closing-line value)",
        needs="a closing price per pick and the pick's own price, stored by the engine",
        why="no closing line or pick ledger is in the export",
    ),
)


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def date_cluster_ci(
    sums: list[float], counts: list[int], seed: int = SEED, b: int = RESAMPLES
) -> tuple[float | None, float | None, float | None]:
    """(mean, lo, hi): the ratio-of-sums mean and its percentile interval over resampled dates.
    No interval with fewer than two dates; no mean with no rows."""
    pairs = [(s, c) for s, c in zip(sums, counts, strict=True) if c > 0]
    total = sum(c for _, c in pairs)
    if total == 0:
        return None, None, None
    mean = sum(s for s, _ in pairs) / total
    k = len(pairs)
    if k < 2:
        return mean, None, None
    rng = random.Random(seed)  # noqa: S311 - a fixed-seed resample, not a secret
    boots: list[float] = []
    for _ in range(b):
        ss = 0.0
        cc = 0
        for _ in range(k):
            s, c = pairs[rng.randrange(k)]
            ss += s
            cc += c
        boots.append(ss / cc)
    boots.sort()
    alpha = (1 - LEVEL) / 2
    return mean, _quantile(boots, alpha), _quantile(boots, 1 - alpha)


def _locked(date: str) -> bool:
    return date >= MODEL_LOCK_FROM


def _metric_cols(target: str) -> dict[str, tuple[int, int]]:
    """Positions of (sum, count) in a single-model row and in a paired row, per metric."""
    if target == WIN_TARGET:
        return {"log_loss": (7, 8), "brier": (9, 10)}
    return {"crps": (5, 6)}


def _pair_cols(target: str) -> dict[str, tuple[int, int]]:
    if target == WIN_TARGET:
        return {"log_loss": (4, 5), "brier": (6, 7)}
    return {"crps": (2, 3)}


def _role(model: str) -> str:
    if model in (PRODUCTION_PROPS, PRODUCTION_WIN):
        return "production"
    if model in (BASELINE_PROPS, BASELINE_WIN):
        return "baseline"
    if model in FROZEN:
        return "frozen_arm"
    return "other"


def _order(model: str) -> tuple[bool, bool, str]:
    return (_role(model) != "production", _role(model) != "baseline", model)


def _withheld(model: str) -> str | None:
    if model in FROZEN:
        return f"subject of a frozen forward test: {FROZEN[model]}; counts only until its look"
    if model not in (PRODUCTION_PROPS, BASELINE_PROPS):
        return "not a declared production model or baseline; counts only"
    return None


def model_score(model: str, target: str, rows: list[tuple[Any, ...]]) -> ModelScore:
    open_rows = [r for r in rows if not _locked(str(r[0]))]
    locked = [r for r in rows if _locked(str(r[0]))]
    withheld = _withheld(model)
    metrics: dict[str, MeanCI] = {}
    if withheld is None:
        for name, (si, ci) in _metric_cols(target).items():
            use = [r for r in open_rows if int(r[ci]) > 0]
            mean, lo, hi = date_cluster_ci([float(r[si]) for r in use], [int(r[ci]) for r in use])
            if mean is not None:
                metrics[name] = MeanCI(
                    value=mean, lo=lo, hi=hi, n=sum(int(r[ci]) for r in use), n_dates=len(use)
                )
    return ModelScore(
        model_name=model,
        role=_role(model),
        shown=withheld is None,
        withheld_reason=withheld,
        n_scored=sum(int(r[1]) for r in open_rows),
        n_dates=sum(1 for r in open_rows if int(r[1]) > 0),
        n_dnp=sum(int(r[2]) for r in open_rows),
        n_late_excluded=sum(int(r[3]) for r in rows),
        n_t60_unknown=sum(int(r[4]) for r in open_rows),
        n_scored_locked=sum(int(r[1]) for r in locked),
        n_dates_locked=sum(1 for r in locked if int(r[1]) > 0),
        n_dnp_locked=sum(int(r[2]) for r in locked),
        metrics=metrics,
    )


def _call(lo: float | None, hi: float | None, n_dates: int) -> str:
    if lo is None or hi is None:
        return "no_interval"
    if n_dates < MIN_DECISIVE_DATES:
        return "too_few_dates"
    if hi < 0:
        return "better"
    if lo > 0:
        return "worse"
    return "same"


def comparison(arm: str, cmp_: str, target: str, rows: list[tuple[Any, ...]]) -> Comparison:
    open_rows = [r for r in rows if not _locked(str(r[0]))]
    locked = [r for r in rows if _locked(str(r[0]))]
    shown = arm == PRODUCTION_PROPS and cmp_ == BASELINE_PROPS and target in PROP_TARGETS
    withheld = None if shown else (_withheld(arm) or _withheld(cmp_) or "counts only")
    metrics: dict[str, MeanCI] = {}
    if shown:
        for name, (si, ci) in _pair_cols(target).items():
            use = [r for r in open_rows if int(r[ci]) > 0]
            mean, lo, hi = date_cluster_ci([float(r[si]) for r in use], [int(r[ci]) for r in use])
            if mean is not None:
                metrics[name] = MeanCI(
                    value=mean, lo=lo, hi=hi, n=sum(int(r[ci]) for r in use), n_dates=len(use),
                    call=_call(lo, hi, len(use)),
                )  # fmt: skip
    return Comparison(
        arm=arm,
        comparator=cmp_,
        shown=shown,
        withheld_reason=withheld,
        n_pairs=sum(int(r[1]) for r in open_rows),
        n_dates=sum(1 for r in open_rows if int(r[1]) > 0),
        n_pairs_locked=sum(int(r[1]) for r in locked),
        n_dates_locked=sum(1 for r in locked if int(r[1]) > 0),
        metrics=metrics,
    )


def _sentence(target: str, comps: list[Comparison]) -> str:
    shown = [c for c in comps if c.shown]
    if target == WIN_TARGET:
        return (
            "Game winners: the production model and its baseline are the WIN tripwire of a frozen "
            "forward test, so only counts are shown; no market price is stored for these games, "
            "so whether the market wins cannot be said here."
        )
    if not shown or "crps" not in shown[0].metrics:
        return (
            f"{target}: no production-versus-baseline pair can be scored here yet (all settled "
            "rows are inside the locked window or none are stored). No market probability is "
            "stored for scored rows, so whether the market wins cannot be said here."
        )
    m = shown[0].metrics["crps"]
    where = {
        "better": "production's CRPS is lower than the recency baseline's (the baseline does not "
        "win here)",
        "worse": "the recency baseline wins: its CRPS is lower than production's",
        "same": "neither wins: the interval for the difference includes 0",
        "too_few_dates": "no call: ten or fewer dates are never decisive",
        "no_interval": "no call: fewer than two dates",
    }[m.call or "no_interval"]
    lo = "n/a" if m.lo is None else f"{m.lo:+.4f}"
    hi = "n/a" if m.hi is None else f"{m.hi:+.4f}"
    return (
        f"{target}: {where}. Production minus baseline CRPS {m.value:+.4f} "
        f"[{lo}, {hi}], n {m.n} pairs over {m.n_dates} dates (before {MODEL_LOCK_FROM}). "
        "No market probability is stored for scored rows, so whether the market wins cannot "
        "be said here."
    )


def build(scores: Scores, *, target: str | None, stat: str | None, as_of: str | None,
          data_version: str | None) -> Scorecard:  # fmt: skip
    rule = ScoreRule(
        table="settled_scores (forward_scores in nba.duckdb)",
        rule="latest_row",
        source=(
            "nba/export/reader.py:335-352 exports forward_scores; nba/daily/settle.py:96-120 "
            "fills it from the latest row per (game, model, target, player) regardless of lead"
        ),
        t60_filter=(
            "rows made after tip-off minus 60 minutes are dropped where the export holds the "
            "game's tip-off; rows whose tip-off is not in the export are kept and counted as "
            "t60_unknown"
        ),
        n_rows_read=scores.n_rows_read,
        n_late_excluded=scores.n_late,
        n_t60_unknown=scores.n_unknown_t60,
        limitation=(
            "This is not the pre-registered T-60 table (forward_scores_elig), which the export "
            "does not carry. crps is the score settle stored: the continuous 19-quantile CRPS "
            "(or the closed-form normal when a row has no grid; nba/daily/settle.py:57-83), "
            "not the integer-support CRPS the forward tests use. Descriptive, not decision-bearing."
        ),
    )
    targets: list[str]
    if target == "win":
        targets = [WIN_TARGET]
    elif stat is not None:
        targets = [stat]
    elif target == "props":
        targets = list(PROP_TARGETS)
    else:
        targets = [*PROP_TARGETS, WIN_TARGET]
    cards: list[TargetCard] = []
    if scores.available:
        for t in targets:
            models = sorted({m for (m, tt) in scores.single if tt == t}, key=_order)
            ms = [model_score(m, t, scores.single[(m, t)]) for m in models]
            comps = [
                comparison(a, c, t, rows)
                for (a, c, tt), rows in sorted(scores.paired.items())
                if tt == t
            ]
            comps.sort(key=lambda c: not c.shown)
            cards.append(TargetCard(target=t, models=ms, comparisons=comps,
                                    sentence=_sentence(t, comps)))  # fmt: skip
    return Scorecard(
        as_of=as_of,
        data_version=data_version,
        available=scores.available,
        reason=scores.reason,
        seasons=scores.seasons,
        population=(
            "regular-season games (game id 002...) of the export's forward seasons; season 2025 "
            "(the holdout) is never read"
        ),
        score_rule=rule,
        method=(
            f"mean of stored per-row scores; {int(LEVEL * 100)}% percentile bootstrap over whole "
            f"game dates (ratio of sums), {RESAMPLES} resamples, seed {SEED} re-set per series. "
            "Paired differences are arm minus comparator on the same (game, player, target) rows "
            "scored for both; lower is better for every score. better/worse/same: the interval "
            "excludes 0 below / above / includes it; ten or fewer dates never decide."
        ),
        lock_from=MODEL_LOCK_FROM,
        lock_text=LOCK_TEXT,
        market={"available": False, "reason": NO_MARKET},
        targets=cards,
        not_available=list(NOT_AVAILABLE),
    )
