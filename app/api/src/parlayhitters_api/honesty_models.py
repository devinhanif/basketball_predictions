"""Response shapes of the honesty routes (docs/contracts/*_v1.md, 2026-11-17). Every value is a
stored field, a count, or a mean of stored scores with its n and a date-clustered interval."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# --------------------------------------------------------------------------- record


class SnapshotRef(BaseModel):
    """One stored forecast for the same prop or game: when, how long before tip, which model.
    Never its numbers."""

    made_at: str | None
    lead_minutes: float | None
    meets_t60: bool | None
    model_name: str
    version: str
    is_primary: bool
    comparison_only: bool
    is_latest: bool


class Record(BaseModel):
    kind: str  # "prop" | "game_winner"
    game_id: str
    player_id: int | None
    stat: str | None
    available: bool
    reason: str | None
    model_name: str | None
    version: str | None
    weights_sha256: str | None
    weights_sha256_short: str | None
    weights_scope: str | None
    fit_through: str | None
    n_train: int | None
    made_at: str | None
    tipoff: str | None
    lead_minutes: float | None
    meets_t60: bool | None
    t60_rule: str
    data_version: str | None
    git_sha: str | None
    git_dirty: bool | None
    stamped_at: str | None
    primary_source: str | None
    primary_detail: str | None
    fallback_reason: str | None
    source_table: str | None
    bundle: dict[str, Any] | None
    snapshots: list[SnapshotRef]
    snapshots_note: str
    missing: list[str]


# --------------------------------------------------------------------------- distribution


class SurvivalStored(BaseModel):
    n: int
    counts: list[int]
    k_from: int
    meaning: str


class PlayStored(BaseModel):
    value: float | None
    source: str | None
    detail: str | None


class ConditionalPieces(BaseModel):
    """The stored pieces behind "P(plays) x P(over | plays)", exactly as the row stores them."""

    p_play_used: float | None
    p_ge_given_plays: dict[str, float] | None
    p_ge_unconditional: dict[str, float] | None
    mean_given_plays: float | None
    mean_unconditional: float | None
    source: str


class Support(BaseModel):
    """Read off the stored values: are the 19 quantiles whole numbers, and is an integer
    survival table (p_ge_full) stored."""

    q_grid: str  # "integer" | "continuous"
    survival: str | None  # "integer" when p_ge_full is stored, else None
    words: str


class Distribution(BaseModel):
    game_id: str
    player_id: int
    stat: str
    available: bool
    locked: bool
    lock_text: str | None
    model_name: str | None
    version: str | None
    made_at: str | None
    taus: list[float]
    q_grid: list[float] | None
    mean: float | None
    std: float | None
    support: Support | None
    p_ge_full: SurvivalStored | None
    p_play: PlayStored | None
    proj_minutes: float | None
    conditional: ConditionalPieces | None
    kalshi_dnp_note: str
    missing: list[str]


# --------------------------------------------------------------------------- scorecard


class MeanCI(BaseModel):
    value: float
    lo: float | None
    hi: float | None
    n: int
    n_dates: int
    call: str | None = None  # comparisons only: better | worse | same | too_few_dates | no_interval


class ModelScore(BaseModel):
    model_name: str
    role: str  # production | baseline | frozen_arm | other
    shown: bool
    withheld_reason: str | None
    n_scored: int
    n_dates: int
    n_dnp: int
    n_late_excluded: int
    n_t60_unknown: int
    n_scored_locked: int
    n_dates_locked: int
    n_dnp_locked: int
    metrics: dict[str, MeanCI]


class Comparison(BaseModel):
    arm: str
    comparator: str
    shown: bool
    withheld_reason: str | None
    n_pairs: int
    n_dates: int
    n_pairs_locked: int
    n_dates_locked: int
    metrics: dict[str, MeanCI]


class TargetCard(BaseModel):
    target: str
    models: list[ModelScore]
    comparisons: list[Comparison]
    sentence: str


class ScoreRule(BaseModel):
    table: str
    rule: str  # latest_row | t60_eligible
    source: str
    t60_filter: str
    n_rows_read: int
    n_late_excluded: int
    n_t60_unknown: int
    limitation: str


class NotAvailable(BaseModel):
    metric: str
    needs: str
    why: str


class Scorecard(BaseModel):
    as_of: str | None
    data_version: str | None
    available: bool
    reason: str | None
    seasons: list[int]
    population: str
    score_rule: ScoreRule
    method: str
    lock_from: str
    lock_text: str
    market: dict[str, Any]
    targets: list[TargetCard]
    not_available: list[NotAvailable]


# --------------------------------------------------------------------------- forward tests


class ForwardTest(BaseModel):
    id: str
    title: str
    source: str
    as_of: str
    scheduled_read: str
    n_needed: str
    arms: list[str]
    n_so_far: dict[str, dict[str, int]] | None
    n_so_far_reason: str | None


class ForwardTests(BaseModel):
    version: int
    source: str
    as_of: str
    note: str
    count_rule: str
    tests: list[ForwardTest]


# --------------------------------------------------------------------------- parlay diagnostics


class ParlayLeg(BaseModel):
    game_id: str | None
    player_id: int | None
    stat: str | None
    side: str | None
    threshold: float | None


class ParlayDiag(BaseModel):
    ticker: str | None
    title: str | None
    engine: str | None
    n_legs: int
    legs: list[ParlayLeg]
    joint_stored: float | None
    joint_stored_field: str
    joint_independent: float | None
    joint_copula: float | None
    shrunk_prob: float | None
    model_weight: float | None
    verdict: str | None
    missing: list[str]


class ParlayDiagnostics(BaseModel):
    date: str
    available: bool
    reason: str | None
    source: str | None
    status: str | None
    verdict: str | None
    n_records: int
    offset: int
    limit: int
    n_returned: int
    engines: dict[str, int]
    n_with_independent: int
    n_with_copula: int
    records: list[ParlayDiag]
    note: str
