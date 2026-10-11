# OWN_TAG: the player's own T-60 report status as a props feature (pre-registration)

Status: DRAFT 2026-10-11 ~17:50 CT, NOT FROZEN; gate to be measured before any freeze (Devin pre-approved "try everything",
2026-10-11 16:50; DECISIONS ~17:40). Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/OWN_TAG.md | shasum -a 256`. Seasons: 2023 = 2023-24, 2024 = 2024-25,
2025 = 2025-26 (frozen holdout, never loaded).

## Why
Production `props_context_residual` v2 carries the player's flag HISTORY (`since_flag`, `flag_dir`, `gap_days`) and the team's
OUT context (`n_out_rot`, `vac_min`, `has_report`), but not the player's OWN current status on the T-60 report. Measured
2026-10-11 (reports/fan_answers_2026-10-11.md; PPLAY_CONTEXT T316-T317): a questionable player who plays gets 1.6 minutes fewer
than his prior-10 mean (CI −1.9 to −1.3, n 1,613); the tag is the whole information in P(play). Devin: "fix the props injury
features." This is the smallest fix: information the model already has in hand but does not read.

## Hypothesis
H1: adding the player's own T-60 status code (none / available / probable / questionable / doubtful; OUT rows are excluded from the
props universe already) to the production feature list lowers integer-support CRPS for pts, reb, ast, fg3m on 2024-25 after
selecting on 2023-24. Null: no change. Prior: ~40% for pts/reb (minutes effect), lower for fg3m.

## Data exposure (honest)
Seen: the −1.6 min descriptive; PPLAY_CONTEXT results; production OOF aggregates. Not seen: any props CRPS with the tag. Season
2025 never loaded; `season <= 2024` asserted; nba.duckdb read_only. Select 2023, report 2024; the F8/F11 harness (month blocks,
2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU, integer support).

## Fixed design (constants final)
Columns appended via `stat_feature_names(stat, extra=...)`: `own_tag` (ordinal 0 none, 1 available, 2 probable, 3 questionable,
4 doubtful, from the latest real-tip-gated snapshot at or before tip − 60 min; no later snapshot) and `own_tag_hours` (hours
between that snapshot and tip, capped at 48). Nothing from tonight.
Arms: A0 production; A1 = A0 + the two columns; A2 placebo = A1 with `own_tag` permuted within (team, game date); A3 = A1 without
`own_tag_hours`.

## Metrics, support, inference
As F11: paired dCRPS (A1 − A0) per stat per season, game-clustered bootstrap (2000, seed 0); bias; A1.1 80% PIT coverage;
lower-tail PIT; threshold log loss; BH over the 4 stats per season; MDE printed. Slices: tag levels (none / probable /
questionable), starter/bench, first 15 team games; mechanism: dCRPS largest on questionable rows.

## Minimum data checks (fail = stop)
1. Snapshot at or before tip − 60 exists for ≥ 95% of 2023/2024 games (PPLAY_CONTEXT: 99.85%; re-measure).
2. Tag non-none on ≥ 3% of played rows (else no power); counts per level reported.
3. Missingness audit of both columns by realised-minutes bucket, max gap ≤ 2 pp (the tag is built for ALL active-roster rows then
   filtered); planted same-game and planted-future tests (a snapshot after tip − 60 never changes the tag).
4. A0 reproduces production to 1e-6.

## Pass rule (per stat; 2023 selects, 2024 confirms)
dCRPS point ≤ −0.005, CI upper < 0, BH p < 0.05; guards as F11 (|bias| ≤ 0.5, coverage in [0.75, 0.85], no slice n ≥ 300 worse
than +0.01); A1 beats A2 by ≤ −0.003. A pass → shadow candidate (logged live beside production), promotion is Devin's.

## Kill criteria
No stat passes on 2023: close; no tag re-encodings, no T-30 variant here (that is the lineups arm's job); one follow-up at most.

## Outputs (exact)
`reports/prereg_own_tag/results.json`, `reports/prereg_own_tag.md` with the F11 column layout; code under `research/` only
(`research/eval/own_tag_eval.py`).

=== RESULTS BELOW ===

(not run)
