# OWN_TAG: the player's own T-60 report status as a props feature (pre-registration)

Status: FROZEN 2026-10-11 15:05 CT under Devin's pre-approval ("try everything", 2026-10-11 16:50; DECISIONS ~17:40 and ~18:40). Gate measured;
check 4 amended before the freeze (reference key set). Freeze evidence: commit hash plus
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
4. A0 reproduces production to 1e-6 on the stored reference's key set (reports/lower_tail/candidates.json 'off', made before
   the 2026-10-11 data clean-up restored five 2024 neutral-site games); the 118 restored rows stay in the evaluation for every
   arm. (Amended before the freeze after the gate measure; precedent ELO_HISTORY_WINDOW and PPLAY_CONTEXT. Unstated details
   fixed before scoring: OUT-but-played rows coded 5; games with no usable snapshot get own_tag 0 and own_tag_hours 48.)

### Gate measurement (2026-10-11, `research/eval/own_tag_eval.py gate --allow-draft`; reports/prereg_own_tag/gate.json)
Real data, read-only, season <= 2024; no arm fitted (A0 only, for check 4).
1. PASS. Regular-season games with a usable snapshot at or before tip - 60: 2023 99.92% (1,229 of 1,230), 2024 99.84% (1,228 of 1,230);
   all games 99.92% / 99.77%; real tip for 100% of games (proxy fallback 0); the snapshot-game set equals the production `flagged` key set.
2. PASS. Non-none tag share of scored rows (n_prior >= 5): 2023 7.97% (n 27,619), 2024 8.27% (n 27,701). Counts, scored rows 2023 / 2024:
   available 584 / 648, probable 605 / 703, questionable 1,002 / 922, doubtful 3 / 5 (too few to learn; effectively a tail of the
   questionable level), OUT 7 / 12. OPEN ITEM: played rows carrying an OUT line (48 across 2022-2024; 9 in 2023, 19 in 2024)
   exist; the draft codes only 0-4; the harness codes them 5. Devin to choose: keep 5 (built) or amend.
3. PASS. Imputed-or-NULL rate (no usable snapshot for the game) by realised-minutes bucket, all roster rows 2023-2024 (n 69,902):
   DNP 0.31%, <5 0.06%, 5-10 0.14%, 10-20 0.10%, >20 0.15%; max gap 0.25 pp (0.09 pp played rows only; both columns identical);
   NULLs after the join onto the played feature frame: 0. Planted tests (5 of 5): a tip-30 and a tip-59 snapshot plus altered stored
   minutes leave the day's tags identical, a tip-90 snapshot changes them (not inert); rotating the statuses and shifting the stamps of
   every report row on/after 2024-01-15 leaves earlier tags identical and changes later ones. Description, not missingness: the non-none
   share rises with minutes among played rows (<5 5.8%, 5-10 5.5%, 10-20 6.6%, >20 9.4%) and is 21.1% among DNP rows.
4. FAIL AS WRITTEN. A0 reproduces the stored `reports/lower_tail/candidates.json` 'off' values EXACTLY for 2023 (delta 0.0, n identical,
   all 4 stats; pts 3.152830). For 2024 it does not: n 27,701 vs stored 27,583 (+118 scored rows, all from five 2024-25 games whose
   player_game_stats rows were added after the reference: 0022400147, 0022400621, 0022400633, 0022401229, 0022401230, plus 3 single
   rows), pts 3.187553 vs 3.188411 (delta -0.000858), reb +0.000252, ast -0.000808, fg3m -0.000065. Cause is database movement since
   2026-10-09/10 (the Oct-10 FOUR_FACTORS A0 cache has the stored n), not the harness: the code path is the production one. This check
   cannot pass until the reference is regenerated on the current DB (maintainer command in the result note) or the rule is amended to
   compare against a same-run production-path A0. Not changed here.

## Pass rule (per stat; 2023 selects, 2024 confirms)
dCRPS point ≤ −0.005, CI upper < 0, BH p < 0.05; guards as F11 (|bias| ≤ 0.5, coverage in [0.75, 0.85], no slice n ≥ 300 worse
than +0.01); A1 beats A2 by ≤ −0.003. A pass → shadow candidate (logged live beside production), promotion is Devin's.

## Kill criteria
No stat passes on 2023: close; no tag re-encodings, no T-30 variant here (that is the lineups arm's job); one follow-up at most.

## Outputs (exact)
`reports/prereg_own_tag/results.json`, `reports/prereg_own_tag.md` with the F11 column layout; code under `research/` only
(`research/eval/own_tag_eval.py`).

=== RESULTS BELOW ===

## Results (run 2026-10-11 ~19:45 CT; frozen sha256 2198c1f0 verified before and after; research/eval/own_tag_eval.py; reports/prereg_own_tag.md)

NULL on all four stats; the 2023 kill criterion fires. A1 − A0 integer CRPS (game-clustered 95% CI; BH over 4 stats): pts 2023 +0.0016 [−0.0009, +0.0041], 2024 −0.0001 [−0.0021, +0.0019]; reb +0.0011 [+0.0000, +0.0021] / −0.0002; ast −0.0001 / 0.0000; fg3m −0.0001 / +0.0001. No cell near the −0.005 floor; the real tag is no better than the shuffled one (A1 − A2 2024: pts −0.0008, others ≈ 0); dropping own_tag_hours changes nothing. Tag-level slices (descriptive): probable rows gain a little on pts/reb (pts −0.014 [−0.031, +0.002]; reb −0.009 [−0.015, −0.002]); questionable rows do not, and were worse on reb in 2023 (+0.0065 [+0.0003, +0.0122]). Guards pass. Gate: all four checks pass incl. A0 reproduced to 1e-6 on the re-baselined reference (2024 n 27,701). Unstated details fixed before scoring: OUT-but-played coded 5; no-snapshot rows 0 / 48; placebo permuted among scored rows (seed 0). Ledger T326–T338. Closed.


Freeze: 2026-10-11 15:05 CT, commit a44f876, sha256 of everything above the line: 2198c1f05e5500130e77adab3ac9d2318b66501dbd1ba2c3f5450761981d76db (first 8: 2198c1f0). Verify with the awk command in the header.
