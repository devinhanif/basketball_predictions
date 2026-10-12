# ERROR_FEEDBACK: a player's own recent forecast errors as a props input (pre-registration)

Status: **DRAFT (written by Mon Oct 12 14:10 CDT 2026, clock read by `date`), modeler. Not frozen. The minimum-data gate FAILS as briefed (a walk-forward GBM refit has no feature-bearing training rows for the first 2023 block, and 2024 power is 0.41); a simpler residual-shift design is close but the persistence it relies on was measured on these same seasons by this gate. The modeler recommends NOT freezing a 2023/24 rule; options below.** Devin (2026-10-12): "recent errors would work" (DECISIONS 2026-10-12 14:05 CDT). If frozen, the main session commits `prereg(ERROR_FEEDBACK): <sha256>` BEFORE any arm is fitted. Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ERROR_FEEDBACK.md | shasum -a 256`. Seasons: 2023 select, 2024 report, 2025 never loaded.

## The rule in five lines (as briefed)
1. Candidate EF = production `context_residual` heads refit walk-forward WITH `ef_res_l3`, `ef_res_l5`, `ef_res_l10`, `ef_pit_l5`, `ef_n` (below), read out through the stored lower-tail mixture (as RECENT_ROLE_FULL's HEADS arm). Comparator A0 (stored, reproduced to 1e-6).
2. Integer CRPS, pts primary (reb/ast/fg3m secondary, BH m = 3, labelled only); paired, game-clustered percentile CI (2000 draws, seed 0); floor -0.005, CI upper < 0; select 2023, report 2024, both must pass.
3. Guards: past-only placebo (columns of a uniformly drawn strictly earlier row of the same player) EF - PL <= -0.003; noise-column control and shared-permutation shuffle sized from a permutation null (K >= 50 per season, measured before the freeze); FULL-overlap contrast EF+FULL vs FULL reported; coverage and slices (bench, starters, cold start, minutes < 5, playoffs; n >= 300) not worse by > 0.01; planted future, control and missingness gates.
4. Stated alternative design (see Recommendation): SHIFT = A0 grid + beta x `ef_res_l5`, beta refit walk-forward on earlier A0 rows only (block 0 of 2023: beta = 0).
5. A pass is a candidate; Stage 2 = the 2026-27 forward log or one logged 2025 touch, Devin's call. Null closed.

## Hypothesis
Production's residuals persist within a player: a player the model has been under-forecasting keeps beating it for a while (role growth, a hot stretch, an unmodelled matchup trend), and feeding the forecaster its own recent misses corrects part of that.

## Leakage design (the main risk)
* Residuals come ONLY from the stored walk-forward A0 forecasts (`reports/lower_tail/components_*.npz`): each was made by a model trained strictly before its month block, so no forecast saw its own row; the feature for a game dated D uses rows dated **strictly before D** (same-date rows never feed each other; unit-tested with a planted future and a non-inert past control).
* Inside a refit, a training row's feature must also come from a forecast made before THAT row, which is why 2022 is the problem: no stored 2022 forecast exists (A0 starts 2023), so the GBM's training rows carry the feature only for earlier 2023-24 rows. The missingness of the feature in training is then a function of time (era), not of the outcome; it must still pass the missingness audit by realised-minutes bucket.
* Residual-on-residual feedback can amplify a scorer bias; the placebo and noise controls exist for that.

## Overlap with RECENT_ROLE / CUSUM
Production already carries CUSUM role-change detection on minutes (`since_flag`) and recency windows; RECENT_ROLE_FULL adds last-game and last-3/5 minutes. Measured descriptively for this gate: the slope of tonight's residual on the mean of the previous 5 is 0.149 / 0.102 under A0 and **0.126 / 0.075 under FULL** (FULL absorbs about 15-26% of the persistence); corr(A0 lag-5 residual, FULL's median shift) 0.12 / 0.13. So error feedback is mostly NOT the same information as recent-role minutes; part of it is.

## Forking paths and data exposure
This gate computed the descriptive persistence slope on the 2023/24 A0 residuals (the comparator's errors) and the FULL-overlap slopes above; that is a look at the outcome on the select/report seasons. The idea is Devin's; the 5-game window used for sizing was chosen before the look; no EF/SHIFT arm was fitted or scored.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 14:08-14:10 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.error_feedback_gate` (read-only, `season <= 2024`; 16 s; JSON `reports/prereg_error_feedback/gate.json`).
* Evaluation rows with >= 3 earlier stored rows: 25,988 of 27,619 (94.1%) / 27,383 of 27,701 (98.9%). sd of `ef_res_l5` 2.91 / 2.83 pts.
* **Training coverage for the briefed GBM refit** (training rows with the feature / all training rows, 2022 played rows counted from the box): block 0 (first 2023 month) **0.0%**, block 2 15%, block 4 34%, block 9 (first 2024 month, trained on all of 2023) 49%, block 17 66%. The 2023 select season is mostly fitted without the feature: **FAIL** (requirement >= 50% in the first block).
* Persistence (descriptive): slope 0.149 [0.120, 0.176] (2023) / 0.102 [0.071, 0.133] (2024), game-clustered.
* Expected effect if the forecast were shifted by slope x `ef_res_l5` (outcome-free CRPS divergence given that slope): **-0.0096 / -0.0045** on all rows. sd_row 0.340 / 0.330 (stored FULL-minus-A0), deff 1.00 / 1.09, half-width 0.0040 / 0.0041 (PASS). Power 0.99 / 0.41 per season, **about 0.40 for both**: 2024 FAIL.

## Gate verdict and recommendation
**FAIL as briefed** (training coverage 0% in the first select block; 2024 power 0.41). The persistence is real-looking (both CIs clear zero) and only partly duplicates RECENT_ROLE_FULL, so this is the most promising of today's six drafts, but the 2023/24 seasons are now spent on the slope. Options, Devin's call:
1. **SHIFT design, forward only:** freeze the simple shift (beta refit walk-forward on earlier rows, no GBM) and score it on the 2026-27 live log against the lower-tail arm (needs stored live forecasts, which the pretip log keeps). Power about 0.4-0.99 per season depending on which season's slope is true. My preference.
2. **One logged 2025 touch of SHIFT**, select = 2023-24 (beta's form fixed now), report = 2025; no EF model has touched 2025. Spends the touch.
3. Build stored 2022 walk-forward forecasts first (needs 2021 history in `data/history`, outside production), then run the briefed GBM rule on 2023/24; the slope look makes that a candidate-only result anyway.

## Unstated details chosen before scoring
Median = the 100th of 199 integer quantiles of the A0 mixture; mid-PIT = (F(y-1) + F(y)) / 2 on the integer grid; history crosses seasons (2024 rows use 2023 rows); playoff rows count; windows over the player's last 10 stored played rows.

=== RESULTS BELOW ===

(not run)
