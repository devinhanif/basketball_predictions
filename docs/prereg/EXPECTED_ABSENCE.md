# EXPECTED_ABSENCE: injury-Elo with expected absent value from P(play), questionable players included (pre-registration)

Status: **DRAFT (written by Mon Oct 12 13:57 CDT 2026, clock read by `date`), modeler. Not frozen. The minimum-data gate FAILS (effect ceiling about a third of the floor, and the stored P(play) R predictions do not cover the players the rule needs); the modeler recommends NOT freezing.** Queue item 3 of DECISIONS 2026-10-12 13:27 CDT. If Devin still wants it, the main session commits `prereg(EXPECTED_ABSENCE): <sha256>` BEFORE any arm is fitted. Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/EXPECTED_ABSENCE.md | shasum -a 256`. Seasons: 2023 = 2023-24 (select), 2024 = 2024-25 (report); 2025 never loaded.

## The rule in five lines (as it would be frozen; not recommended)
1. Candidate EA = production `rung0_injury_elo` (MOV-Elo logit + ridge-fitted injury terms, refit monthly on games before the slate, real-tip T-60 gating) with the `d_out`/`d_doubt` terms replaced by one term `d_exp` = away-minus-home sum over every player listed on the latest T-60 snapshot of (1 - P(play)) x as-of value / 10, P(play) = 0 for OUT and P(play) R (RECENT_ROLE_HOLDOUT, T458) for every other listed status.
2. Comparator = stored production injury-Elo OOF (`data/injury_elo_realtip/oof_predictions.parquet`), reproduced to 1e-9 by the harness first; MOV-Elo is reported.
3. Metric: game log loss (primary) and Brier, EA minus production, paired, percentile bootstrap clustered by game (2000 draws, seed 0; date clustering reported); floor **-0.002**, CI upper < 0, select 2023, report 2024, both must pass. No family (one test); Brier and ECE reported.
4. Slices (reported, not rescuing): close games (|p_prod - 0.5| < 0.2, where the 1.3-point accuracy gap to Pinnacle sits, reports/confusion_2026-10-11.md), games with >= 1 questionable rotation player, first 15 team games. Guards: past-only placebo P(play) (each listed player takes the P(play) of one strictly earlier listed row of his own) must lose to EA by <= -0.001; ECE no worse by > +0.005.
5. A pass is a candidate; Stage 2 = the 2026-27 forward log. Null closed; no follow-up.

## Hypothesis
Production counts OUT players fully and DOUBTFUL at a coefficient that fitted to about zero (final coefficients b_out 0.316, g_doubt 0.0116; the OUT-only variant scores the same, `data/injury_elo_realtip/results.json`). QUESTIONABLE players are ignored, yet about half of them sit. Weighting every listed player by his probability of missing (from the holdout-confirmed P(play) R) should move win probabilities in the games where a valuable player is a game-time decision.

## Forking paths and data exposure
* Suggested by the confusion matrices (reports/confusion_2026-10-11.md: injury-Elo 67.3% vs Pinnacle 68.6%, gap -1.3 [-2.6, -0.1] pts, n 2,633) and by P(play) R's holdout pass (T458). The close-game slice was named after seeing that gap. Seen for this draft: coverage counts, the logit change the hypothesised arm would make, KL ceilings, per-game sd of the stored injury-Elo-minus-MOV-Elo deltas (frozen and scored in T128). No EA log loss computed.

## As-of inputs
* Listed players and statuses: `player_availability` (source `nba_official_report`), the latest snapshot per game stamped <= real tip - 60 min (`nba.features.game_tipoff`, the `latest_pretip_flagged` rule).
* Value: `nba.models.injury_elo.asof_values` (games strictly before the date; production `ValueConfig`).
* P(play): the R model's walk-forward predictions for that (game, player). **Stored source: `data/ops/recent_role_cache/scores.npz` (`p_R`, 33,093 / 33,127 rows in 2023 / 2024).**

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 13:54 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.expected_absence_gate` (read-only, `season <= 2024`; JSON `reports/prereg_expected_absence/gate.json`). Sizing is outcome-free: the hypothesised arm (production logit, OUT/DOUBTFUL terms replaced by `b_out x d_exp`, statuses without a P(play) R row given a status default: doubtful 0.05, questionable 0.55, probable 0.90, available 0.95) is never scored; its expected gain if it were the truth is KL(p_EA || p_prod) per game, an optimistic ceiling.
* **Coverage of P(play) R for listed not-OUT players (count share / value share):** questionable 64.5% / 76.8% (2023, n 2,045), 68.2% / 72.6% (2024, n 1,972); doubtful 23.7% / 15.5%, 26.7% / 26.1%; probable 98.2% / 99.5%, 96.9% / 98.8%; available 87.7% / 96.5%, 90.6% / 97.7%. Games needing a status default for a valued player: 532 of 1,318 / 546 of 1,315. **What is missing and why:** 90.3% of the uncovered listed players have no box row in that game (inactive), because the R population is box-score rows with >= 5 prior played games. So the stored R predictions exist mainly for listed players who dressed: **coverage depends on the outcome**, the NULL-pattern leak of `opp_adjusted_ridge` in another form. A valid EA needs R re-served on a roster-based population (official report + roster as-of), which does not exist for 2023-24 and would be a new, never-evaluated P(play) population.
* **Effect ceiling:** mean |logit change| 0.047 / 0.051, mean |probability change| 0.009 / 0.010 (29% / 32% of games move by > 0.05 logit). KL ceiling **-0.00066 / -0.00085 log loss per game** (floor -0.002). Close-game slice: n 772 / 731, ceiling -0.0008.
* **Precision:** n 1,318 / 1,315 games, date deff 1.08 / 1.23. With the stored injury-minus-MOV per-game sd (0.142 / 0.131) the half-width is 0.0080 / 0.0078; with the sd implied by the arm itself (0.036 / 0.041) it is 0.0020 / 0.0025. Power of the pass line at the ceiling <= 0.10 per season, **<= 0.01 for both seasons**, under either sd. n needed for a half-width of 0.002: about 1,360-2,020 games per season with the small sd (available), but the effect is a third of the floor regardless.
* A lower floor does not help: at -0.001 the ceiling still sits at or below the half-width.

## Gate verdict and recommendation
**FAIL** on effect size (ceiling one third of the floor, power <= 0.01) and on coverage (72.6-76.8% of questionable value, with outcome-dependent missingness). **Recommend not freezing.** What would reverse it: a roster-based P(play) R served for every listed player (the live pipeline does serve P(play) for official-roster players from opening night, `forward_pplay_shadow`), and a forward rule on 2026-27 sized for a -0.001 floor, which needs roughly 4x the games of one season: not reachable in 2026-27. Honest reading: the injury layer's information beyond OUT is small (production's DOUBTFUL coefficient already fitted to about 0); the gap to Pinnacle is not where this idea can reach.

## Kill criteria
Not frozen; nothing runs. If frozen against this advice: gate failure (production OOF not reproduced to 1e-9, coverage < 90% of questionable value) stops the run before any arm.

=== RESULTS BELOW ===

(not run)
