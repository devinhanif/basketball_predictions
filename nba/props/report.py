"""Render ``report.md`` for a props experiment (CLAUDE.md "Metrics").

Pure string formatting over :class:`nba.props.run.PropsExperimentResult` --
no DuckDB/network access, trivially unit-testable.
"""

from __future__ import annotations

from nba.props.run import MIN_RELIABLE_N, PropsExperimentResult, StatResult


def _fmt(x: float, nd: int = 4) -> str:
    if x != x:  # NaN
        return "NaN"
    return f"{x:.{nd}f}"


def _ci_str(ci: object) -> str:
    point = _fmt(ci.point)  # type: ignore[attr-defined]
    lo = _fmt(ci.lo)  # type: ignore[attr-defined]
    hi = _fmt(ci.hi)  # type: ignore[attr-defined]
    note = f" ({ci.note})" if ci.note else ""  # type: ignore[attr-defined]
    return f"{point} [{lo}, {hi}], n={ci.n}{note}"  # type: ignore[attr-defined]


def _stat_table(stats: list[StatResult]) -> str:
    lines = [
        "| stat | n | family | mean bias (95% CI) | 80% coverage | pooled ECE | CRPS |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        lines.append(
            f"| {s.stat} | {s.n} | {s.dist_family} | {_ci_str(s.mean_bias)} | "
            f"{_fmt(s.coverage, 3)} | {_fmt(s.pooled_ece)} | {_fmt(s.crps_point)} |"
        )
    return "\n".join(lines)


def _baseline_table(stats: list[StatResult]) -> str:
    lines = [
        "| stat | CRPS vs season-avg (delta CI) | CRPS vs last-10 (delta CI) | "
        "log loss vs season-avg (delta CI) | log loss vs last-10 (delta CI) |",
        "|---|---|---|---|---|",
    ]
    for s in stats:
        lines.append(
            f"| {s.stat} | {_ci_str(s.crps_vs_season_avg)} | {_ci_str(s.crps_vs_last10_avg)} | "
            f"{_ci_str(s.log_loss_vs_season_avg)} | {_ci_str(s.log_loss_vs_last10_avg)} |"
        )
    return "\n".join(lines)


def _threshold_table(stats: list[StatResult]) -> str:
    sections = []
    for s in stats:
        lines = [
            f"**{s.stat} threshold log loss / ECE**",
            "",
            "| N | n | log loss | ECE |",
            "|---|---|---|---|",
        ]
        for t in s.threshold_calibration:
            ece = _fmt(t.calibration.ece)
            lines.append(f"| >={int(t.threshold)} | {t.n} | {_fmt(t.log_loss)} | {ece} |")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def render_props_report(result: PropsExperimentResult) -> str:
    date_range = (
        f"{result.game_date_min} to {result.game_date_max}"
        if result.game_date_min is not None
        else "no games"
    )
    warnings = []
    for s in result.stats:
        if s.data_sufficiency_note:
            warnings.append(f"- {s.stat}: {s.data_sufficiency_note}")
        if s.zero_inflation_detected:
            warnings.append(f"- {s.stat}: zero-inflation detected and modeled (ZINB).")
        if s.baseline_note:
            warnings.append(f"- {s.stat} (baseline comparison): {s.baseline_note}")
    if not warnings:
        warnings.append("- None.")

    parts = [
        "# Props backtest report (minutes model + points/reb/ast/3PM distributions)",
        "",
        f"- Seed: {result.seed}",
        f"- Game date range: {date_range}",
        f"- Reliable-sample threshold: n >= {MIN_RELIABLE_N} player-games "
        "(CLAUDE.md milestone 1: >=500 per stat)",
        "",
        "## Data-sufficiency / model warnings",
        "\n".join(warnings),
        "",
        "## Per-stat metrics (primary)",
        _stat_table(result.stats),
        "",
        "## Baseline comparisons (paired per-game bootstrap, CRPS and avg threshold log loss)",
        _baseline_table(result.stats),
        "",
        "## Threshold calibration (P(stat >= N))",
        _threshold_table(result.stats) if result.stats else "_No stats evaluated._",
        "",
        "## Notes",
        "- Points use a frequency-severity (insurance-style) decomposition: "
        "implied scoring-event count x value-per-event, since player_game_stats has no "
        "attempts/misses columns yet -- see nba/props/stat_models.py docstring.",
        "- Rebounds/assists/3PM are modeled as Negative Binomial (NegBin2); 3PM is checked "
        "for zero-inflation and switches to a zero-inflated NegBin automatically when "
        "detected -- see nba/props/stat_models.py::fit_zero_inflation.",
        "- Minutes model is a hurdle (DNP point mass + truncated-Normal minutes|plays); "
        "see nba/props/minutes.py. It never reads the target game's own minutes -- see "
        "tests/props/test_minutes_no_leakage.py.",
        "- Combos (PRA etc.), hierarchical team-total coherence, role-change detection, "
        "and conformal intervals are explicitly out of scope for this milestone.",
        "",
    ]
    return "\n".join(parts)
