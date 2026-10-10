"""Render one ``report.md`` per experiment (CLAUDE.md "Evaluation").

Pure string formatting over :class:`research.eval.run.ExperimentResult` -- no
DuckDB/network access here, so it's trivially unit-testable.
"""

from __future__ import annotations

from typing import cast

from research.eval.run import ExperimentResult, RungMetricBundle


def _fmt(x: float, nd: int = 4) -> str:
    if x != x:  # NaN
        return "NaN"
    return f"{x:.{nd}f}"


def _ci_str(ci: object) -> str:
    # duck-typed: ConfidenceInterval
    point = _fmt(ci.point)  # type: ignore[attr-defined]
    lo = _fmt(ci.lo)  # type: ignore[attr-defined]
    hi = _fmt(ci.hi)  # type: ignore[attr-defined]
    note = f" ({ci.note})" if ci.note else ""  # type: ignore[attr-defined]
    return f"{point} [{lo}, {hi}], n={ci.n}{note}"  # type: ignore[attr-defined]


def _rung_table(bundles: list[RungMetricBundle]) -> str:
    lines = [
        "| model | rung | n | log loss (95% CI) | Brier (95% CI) | accuracy | ECE |",
        "|---|---|---|---|---|---|---|",
    ]
    for b in bundles:
        lines.append(
            f"| {b.name} | {b.rung} | {b.n_games} | {_ci_str(b.log_loss)} | "
            f"{_ci_str(b.brier)} | {_fmt(b.accuracy_point, 3)} | {_fmt(b.ece, 4)} |"
        )
    return "\n".join(lines)


def _slice_tables(bundles: list[RungMetricBundle]) -> str:
    slice_names: list[str] = []
    for b in bundles:
        for s in b.slice_metrics:
            if s not in slice_names:
                slice_names.append(s)
    if not slice_names:
        return "_No slice metrics available (no out-of-fold predictions produced)._"

    sections = []
    for slice_name in slice_names:
        lines = [
            f"**Slice: `{slice_name}`**",
            "",
            "| model | n | log loss | Brier |",
            "|---|---|---|---|",
        ]
        for b in bundles:
            slice_metrics = b.slice_metrics.get(slice_name, {"n": 0})
            n = slice_metrics.get("n", 0)
            if n == 0:
                lines.append(f"| {b.name} | 0 | - | - |")
            else:
                ll = cast(float, slice_metrics.get("log_loss", float("nan")))
                brier = cast(float, slice_metrics.get("brier", float("nan")))
                lines.append(f"| {b.name} | {n} | {_fmt(ll)} | {_fmt(brier)} |")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _holdout_table(bundles: list[RungMetricBundle], note: str) -> str:
    if note:
        return f"_Frozen holdout: {note}_"
    lines = ["| model | n | log loss | Brier |", "|---|---|---|---|"]
    for b in bundles:
        h = b.holdout
        if not h:
            lines.append(f"| {b.name} | - | - | - |")
            continue
        ll = cast(float, h["log_loss"])
        brier = cast(float, h["brier"])
        lines.append(f"| {b.name} | {h['n']} | {_fmt(ll)} | {_fmt(brier)} |")
    return "\n".join(lines)


def _ladder_table(result: ExperimentResult) -> str:
    if not result.ladder_comparisons:
        return "_No ladder comparisons available (insufficient out-of-fold data)._"
    lines = [
        "| comparison (A vs B, log loss) | delta (95% CI) | kept A over B? |",
        "|---|---|---|",
    ]
    for c in result.ladder_comparisons:
        kept = "yes" if c.kept_a_over_b else "no (see CI / n)"
        lines.append(f"| {c.metric_name} | {_ci_str(c.delta)} | {kept} |")
    return "\n".join(lines)


def render_report(result: ExperimentResult) -> str:
    date_range = (
        f"{result.game_date_min} to {result.game_date_max}"
        if result.game_date_min is not None
        else "no games"
    )
    warnings = []
    if result.walk_forward_note:
        warnings.append(f"- Walk-forward: {result.walk_forward_note}")
    for b in result.rungs:
        if b.n_games < 30:
            warnings.append(
                f"- {b.name}: only {b.n_games} out-of-fold games; log loss/Brier/ECE below are "
                "point-ish estimates, not statistically powered conclusions. If any number here "
                "looks implausibly good (e.g. near-zero log loss), treat it as a leakage smell, "
                "not a win -- see `tests/ml/test_no_leakage.py`."
            )

    parts = [
        "# Architecture-ladder backtest report (rungs 0-3)",
        "",
        f"- Seed: {result.seed}",
        f"- Game date range: {date_range}",
        f"- Walk-forward folds: {result.n_folds}",
        "",
        "## Data-sufficiency warnings",
        "\n".join(warnings) if warnings else "- None.",
        "",
        "## Walk-forward out-of-fold metrics (primary)",
        _rung_table(result.rungs),
        "",
        "## Ladder comparisons (paired per-game bootstrap, log loss)",
        _ladder_table(result),
        "",
        "## Metric slices",
        _slice_tables(result.rungs),
        "",
        "## Frozen final holdout (confirmatory only -- never tuned on)",
        _holdout_table(result.rungs, result.holdout_note),
        "",
        "## Notes",
        "- Rolling efficiency features (ORtg/DRtg/pace) feeding rungs 0-2 are "
        "box-score proxies, unchanged by rung 3 -- see `nba/features/team_features.py`. "
        "Rung 3 (`rung3_sim`) instead uses true as-of per-possession ratings built "
        "directly from the `possessions` table, shrunk toward a league-average prior "
        "(empirical-Bayes method 1) -- see `research/features/possession_features.py` and "
        "`research/sim/engine.py`. Per CLAUDE.md risk #1, rung 3 is only worth keeping if it "
        "beats rung 2 on win-prob calibration (see the ladder-comparison table above) "
        "or is justified later by player/joint outputs (not built in this milestone).",
        "- Closing-line implied probability is a stub (`ClosingLineStub`): no "
        "market-odds source is wired in yet (Phase 3).",
        "- Frozen holdout predictions are produced by rolling walk-forward "
        "through the holdout season (warm-started on all tunable data, "
        "hyperparameters fixed) so sequential models (Elo) update their "
        "state through the holdout exactly as a live run would, instead of "
        "predicting the whole season from one stale snapshot -- see "
        "`research/eval/run.py::_evaluate_holdout`. The holdout is still never "
        "used to tune or select a rung.",
        "- Cold-start bucket slice (`cold_start_bucket_low_career_poss` / "
        "`..._warm`) is keyed on a *proxy* for career possessions "
        "(minutes-based, since the possessions table is empty pending the "
        "PBP parser) -- see `research/features/player_features.py`. Not a true "
        "possession count yet.",
        "",
    ]
    return "\n".join(parts)
