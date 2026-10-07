"""Render ``report.md`` for a props experiment (CLAUDE.md "Metrics").

Pure string formatting over :class:`nba.props.run.PropsExperimentResult` --
no DuckDB/network access, trivially unit-testable.
"""

from __future__ import annotations

from nba.props.run import MIN_RELIABLE_N, ComboStatResult, PropsExperimentResult, StatResult


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
        "| stat | n_valid/n | family | mean bias (95% CI) | 80% coverage | pooled ECE | CRPS |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        lines.append(
            f"| {s.stat} | {s.n_valid}/{s.n} | {s.dist_family} | {_ci_str(s.mean_bias)} | "
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


def _combo_table(stats: list[ComboStatResult]) -> str:
    lines = [
        "| combo | n_valid/n | family | mean bias (95% CI) | 80% coverage | pooled ECE | CRPS | "
        "corr(pts,reb)/(pts,ast)/(reb,ast) used |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        corr = s.correlation_used
        corr_str = (
            f"{corr.get('pts_reb', float('nan')):.2f} / "
            f"{corr.get('pts_ast', float('nan')):.2f} / "
            f"{corr.get('reb_ast', float('nan')):.2f}"
        )
        lines.append(
            f"| {s.stat} | {s.n_valid}/{s.n} | {s.dist_family} | {_ci_str(s.mean_bias)} | "
            f"{_fmt(s.coverage, 3)} | {_fmt(s.pooled_ece)} | {_fmt(s.crps_point)} | {corr_str} |"
        )
    return "\n".join(lines)


def _coherence_table(result: PropsExperimentResult) -> str:
    if not result.coherence_checks:
        return "_Coherence reconciliation disabled or no data._"
    lines = [
        "| stat | (game,team) groups | max |sum(player) - team_total| | mean |gap| |",
        "|---|---|---|---|",
    ]
    for c in result.coherence_checks:
        lines.append(
            f"| {c.stat} | {c.n_groups} | {_fmt(c.max_abs_gap)} | {_fmt(c.mean_abs_gap)} |"
        )
    return "\n".join(lines)


def _conformal_table(stats: list[StatResult]) -> str:
    rows = [s for s in stats if s.conformal is not None]
    if not rows:
        return "_Conformal wrapper disabled._"
    lines = [
        "| stat | n_cal | n_test | adjustment | 80% coverage (parametric) | "
        "80% coverage (conformal) |",
        "|---|---|---|---|---|---|",
    ]
    for s in rows:
        c = s.conformal
        assert c is not None
        lines.append(
            f"| {s.stat} | {c.n_cal} | {c.n_test} | {_fmt(c.adjustment)} | "
            f"{_fmt(c.coverage_parametric, 3)} | {_fmt(c.coverage_conformal, 3)} |"
        )
    return "\n".join(lines)


def _volatility_table(stats: list[StatResult]) -> str:
    rows = [(s.stat, b) for s in stats for b in s.volatility_buckets]
    if not rows:
        return "_Volatility-bucket slicing disabled or no data._"
    lines = [
        "| stat | cv variant | bucket | n | mean bias (95% CI) | 80% coverage | "
        "CRPS vs season-avg (delta CI) | CRPS vs last-10 (delta CI) | "
        "log loss vs season-avg (delta CI) | log loss vs last-10 (delta CI) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for stat, b in rows:
        if not b.sufficient:
            insufficient = f"insufficient data for a trustworthy CI (n={b.n})"
            lines.append(
                f"| {stat} | {b.cv_variant} | {b.bucket} | {b.n} | {insufficient} | "
                f"{insufficient} | {insufficient} | {insufficient} | {insufficient} | "
                f"{insufficient} |"
            )
            continue
        lines.append(
            f"| {stat} | {b.cv_variant} | {b.bucket} | {b.n} | {_ci_str(b.mean_bias)} | "
            f"{_fmt(b.coverage, 3)} | {_ci_str(b.crps_vs_season_avg)} | "
            f"{_ci_str(b.crps_vs_last10_avg)} | {_ci_str(b.log_loss_vs_season_avg)} | "
            f"{_ci_str(b.log_loss_vs_last10_avg)} |"
        )
    return "\n".join(lines)


def _minutes_eval_section(result: PropsExperimentResult) -> str:
    me = result.minutes_eval
    if me is None:
        return "_No data._"
    lines = [
        f"- Game-context features used in the minutes model "
        f"(`MinutesModelConfig.use_game_context`): **{me.used_game_context}**",
        f"- DNP/play classification log loss: {_fmt(me.dnp_log_loss)} (n={me.dnp_n})",
        f"- Minutes MAE among players who played: {_fmt(me.minutes_mae)} (n={me.minutes_n})",
        f"- Minutes bias (predicted - actual) among players who played: "
        f"{_fmt(me.minutes_bias)} (n={me.minutes_n})",
    ]
    return "\n".join(lines)


def _role_change_section(result: PropsExperimentResult) -> str:
    rc = result.role_change_summary
    if rc is None:
        return "_Role-change detection disabled._"
    lines = [
        f"- CUSUM-flagged rows: {rc.n_flagged} / {rc.n_rows} player-games "
        "(a flagged row's cold-start pseudo-count k is temporarily multiplied up, "
        "per nba.props.role_change).",
    ]
    if rc.flagged_examples:
        lines.append(
            "- Examples (up to 5): "
            + "; ".join(
                f"game={ex['game_id']} player={ex['player_id']} k_mult={ex['k_multiplier']:.2f}"
                for ex in rc.flagged_examples
            )
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
        if s.exclusion_note:
            warnings.append(f"- {s.stat} (data-quality): {s.exclusion_note}")
        if s.data_sufficiency_note:
            warnings.append(f"- {s.stat}: {s.data_sufficiency_note}")
        if s.zero_inflation_detected:
            warnings.append(f"- {s.stat}: zero-inflation detected and modeled (ZINB).")
        if s.baseline_note:
            warnings.append(f"- {s.stat} (baseline comparison): {s.baseline_note}")
    for c in result.combo_stats:
        if c.exclusion_note:
            warnings.append(f"- {c.stat} (data-quality): {c.exclusion_note}")
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
        "## Combo stats (PRA, P+R, P+A, R+A)",
        "Moment-matched sum of pts/reb/ast marginals under a within-player pairwise "
        "correlation (estimated from as-of history, falling back to a documented default "
        "below the sample-size floor) -- NOT an independence assumption. See "
        "nba/props/combos.py module docstring for the exact method and its stated limits.",
        "",
        _combo_table(result.combo_stats) if result.combo_stats else "_Combos disabled or no data._",
        "",
        "## Hierarchical coherence (team-total reconciliation)",
        "Forecast-proportions (MinT special case) reconciliation: each (game, team) group's "
        "player means are rescaled so they sum exactly to that team's own as-of trailing-average "
        "total. Gap should be ~0 (floating point) for every stat below.",
        "",
        _coherence_table(result),
        "",
        "## Minutes model evaluation (DNP classification + minutes MAE/bias)",
        "Scored directly against the minutes model's own output (independent of the "
        "downstream stat distributions), so the as-of game-context A/B "
        "(`MinutesModelConfig.use_game_context`) is measurable at its most direct point -- "
        "CLAUDE.md motivation: rest/travel/tanking plausibly move WHO plays and HOW MUCH "
        "more than they move game win probability. Toggle the flag and re-run to compare "
        "these numbers; this report only shows one side of that A/B.",
        "",
        _minutes_eval_section(result),
        "",
        "## Role-change detection (CUSUM on minutes)",
        _role_change_section(result),
        "",
        "## Volatility buckets",
        "Tests the hypothesis that the model's edge over season-avg/last-10 baselines "
        "concentrates in high-volatility players. Bucket = coefficient of variation "
        "(SD/mean) of the stat (or minutes, per `VolatilityConfig.cv_variant`) over each "
        "player's strictly-prior games (as-of; see nba/props/volatility.py). Deltas use the "
        "same paired per-game bootstrap as the baseline-comparison table above, scoped to "
        "each bucket; buckets below `min_bucket_n` player-games show an explicit "
        "insufficient-data note instead of a point estimate.",
        "",
        _volatility_table(result.stats),
        "",
        "## Split-conformal interval coverage (80%, parametric vs. conformal)",
        "Chronological calibration/test split (never random); conformal intervals should land "
        "closer to the nominal 80% than the raw parametric quantiles when the parametric "
        "assumption is wrong -- **see the sample-size caveat below before reading these numbers "
        "as evidence of anything**.",
        "",
        _conformal_table(result.stats),
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
        "- DNP rows: `player_game_stats` stores NULL pts/reb/ast/fg3m/minutes for games a "
        "player did not play. These are coalesced to 0 at the source (nba/props/run.py::"
        "_target_frame) -- DNP really is 0 production, not a missing value -- so every "
        "pooled metric below is computed against the real stat, not a NaN-poisoned one. "
        "The n_valid/n column reports any row still excluded for a genuinely new reason.",
        "- SAMPLE SIZE CAVEAT: this report's data is the 3-game / 18-player-game fixture "
        "(or whatever tiny slice was passed in) -- every coverage number, correlation "
        "estimate, and CUSUM flag above is a smoke test of the mechanism, not a claim that "
        "coverage is actually ~80%, that the combo correlations are the real ones, or that "
        "any role change here is real signal. CLAUDE.md's own reliability floor is >=500 "
        "player-games per stat; treat every number on a smaller sample as unverified.",
        "",
    ]
    return "\n".join(parts)
