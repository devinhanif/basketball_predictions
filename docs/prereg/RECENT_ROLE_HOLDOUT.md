# RECENT_ROLE_HOLDOUT: one touch of season 2025 for the recent-role P(play) model R against the current P(play) feature set (pre-registration)

Status: **FROZEN (Mon Oct 12 00:47 CDT 2026) by the main session on Devin's "go for it", accepting the disclosed shuffle tolerance -0.003 (set on the 2024 rehearsal, not 2025) and the gate-stop-may-repeat clause.** Earlier: **DRAFT (written Mon Oct 12 00:47 CDT 2026), modeler.** Devin chose this as the one allowed follow-up of RECENT_ROLE ("go for it", 2026-10-12 ~00:50 CDT). The main
session commits `prereg(RECENT_ROLE_HOLDOUT): <sha256>`, then writes and commits the HOLDOUT_ACCESS_LOG row (format below), then runs the command once. Everything above the
line `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RECENT_ROLE_HOLDOUT.md | shasum -a 256`.
Season labels as before (2025 = 2025-26, the frozen holdout). The modeler has not loaded or read any season-2025 row; the harness was rehearsed on season 2024 only.

## The rule in five lines
1. Candidate R = C' + the five recent-role columns, exactly as frozen in RECENT_ROLE (`research.eval.recent_role_eval.arm_columns()["R"]`); comparator **base = C**, WORKLOAD's P(play) feature set; C' reported as the secondary comparator.
2. Training: every eligible Cell-P row of seasons 2022-2024 (2022 is the warm-up), refit month by month as in the 2023/2024 evaluation: each calendar-month block of season 2025 is fit on all rows dated strictly before that month (so later 2025 months also train on earlier 2025 months, as 2024 blocks trained on earlier 2024 months); same feature builders, LightGBM binary hyper-parameters, seed 0, no tuning.
3. Score: season-2025 Cell-P rows (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report), P(play) log loss, paired R minus base, 95% game-clustered percentile bootstrap (2000 draws, seed 0).
4. PASS = R - base <= -0.010 with CI upper < 0, AND ECE(R) - ECE(base) <= +0.005, AND no slice with n >= 300 worse by more than +0.01, AND R beats its past-only placebo by <= -0.003, AND the shuffled-label check (both arms on one permutation) not failed, AND the run-time gate and plants clean.
5. One run, no reruns, no variants. A fail closes P(play) feature work for 2026-27. A pass is a production-change PROPOSAL only (Devin decides); the live forward log confirms or refutes it.

## Why -0.010
RECENT_ROLE measured R - base = -0.0169 (2023) and -0.0166 (2024), n 33,093 / 33,127, CIs far below zero, MDE about 0.0024. Two thirds of that, -0.010, allows for shrinkage on unseen data (the five R columns were chosen after the adversary's 2023-24 table; the 13 production columns and the learner are fixed), stays twice the house floor (-0.005), and is four MDEs from zero so noise cannot produce it. It protects against both extremes: a floor so close to the seen effect that ordinary shrinkage fails a real model, and a floor so loose that a one-season fluke passes.

## Honest prior and what a pass does and does not mean
My estimate of a pass: about 85% (a large, replicated effect on rows of the same definition; the main risks are the shuffle/placebo guards on a new season and a regime change in 2025-26). What it means: R beats the current P(play) feature set on unseen data. What it does NOT mean: that the recent-role columns alone are the gain. RECENT_ROLE T457: C' - base = -0.0094 / -0.0120 (production's own recent-minutes columns) and R - C' = -0.0075 / -0.0046 (the recent-role increment, which failed its own -0.005 floor by 0.0004 in 2024). The secondary R - C' is reported for exactly that reason and cannot rescue or fail the pass. Also: base = C is the LightGBM M2-p + report-tag feature set, not production's raw hurdle `p_play` (log loss 0.342 on these rows, PPLAY_CONTEXT); any proposal must say so.

## Secondaries (declared, not pass conditions)
* R - C' log loss with CI (above). Cp - base, R - placebo rows are printed.
* Props: the unconditional props CRPS test of RECENT_ROLE Stage 2a is NOT run. No 2025 A0 components exist (`reports/lower_tail/components_*.npz` are the 2023-24 walk by construction: `collect_stat` asserts `season <= 2024`; `WE.a0_arm` raises on any later season; checked by file listing and code, no outcome read). Running the live-refit route on 2025 would be a different, never-validated path; it is a new id if wanted. (Props CRPS conditional on playing cannot move with P(play) in any case: RECENT_ROLE, "Honest prior".)

## Metrics, guards, slices (as RECENT_ROLE, applied to 2025)
Slices: starter / bench (as-of `starter10 >= 0.5`), second night of a back-to-back, cold start (5 <= n_played_prior < 10), playoffs (game_id prefix 004), `wl_min_last < 5`. A slice under 300 rows is reported but cannot fail the guard. Past-only placebo = RECENT_ROLE's `PL` arm (C' + the five R columns replaced by a strictly-earlier-row draw within the player-season), so "R beats PL" is the recent-role increment over a hard null. Shuffled-label check: base and R both trained on ONE permutation of the training labels per block, scored on real labels; it FAILS if `R_shuf - base_shuf <= -0.003` with CI upper < 0. The tolerance is -0.003 (not RECENT_ROLE's -0.001) because the season-2024 rehearsal showed `-0.00085` [CI excluding 0] from pure fit noise between a 77-column and a 64-column model; the check exists to catch a pipeline-manufactured effect of material size, and a gate that fails on fit noise would fail a true model. This tolerance was set after seeing the 2024 rehearsal (a seen season), not 2025. Unit tests: both arms are fitted on identical permuted labels; on a planted-signal frame the check passes with the permutation and fails without it.

## Data exposure (honest)
Season 2025 is the frozen holdout. Seen elsewhere in the project: the injury-Elo and context-residual touches, the 2025 replay (systems test, sealed deltas), design inputs chosen with 2025 visible in other programmes (HOLDOUT_ACCESS_LOG NOTICE). R and C' (the P(play) arms) have never been evaluated on 2025 by anyone. The modeler read no 2025 row for this draft. The `rehearse` command ran the identical code on season 2024 and reproduced RECENT_ROLE's stored 2024 rows exactly (below).

## Minimum-data gates (non-negotiable 8), measured Mon Oct 12 00:45 CDT 2026 by `rehearse` (season 2024 stands in; cap stays 2024; `reports/prereg_recent_role_holdout/rehearsal/`)
* Same code path, season 2024: base 0.281514, Cp 0.269531, R 0.264910 (n 33,127, 1,320 games); R - base **-0.016603** [-0.018983, -0.014173], R - C' **-0.004618**: both equal RECENT_ROLE's stored 2024 numbers to the last digit. R - PL -0.00422 [-0.0057, -0.0027]; ECE delta (R - base) +0.00227; R_shuf - base_shuf -0.00085 [CI upper -0.00036] (hence the -0.003 tolerance above).
* Slice n, 2024: starter 12,462; bench 20,665; second night 5,519; cold start 742; playoffs 2,363; `wl_min_last < 5` 4,789. 2025 slice sizes cannot be measured without reading 2025 rows; each slice under 300 is skipped by the rule, and the run-time gate prints them.
* Run-time gate on the 2025 frame (before any arm is scored): Cell-P rows >= 25,000 and games >= 1,000 (2023/2024 had about 33,100 rows and 1,320 games; the injury-Elo holdout row logged 1,316 games for 2025); missingness of the added columns by realised-minutes bucket (DNP its own) within max(2 pp, 3 x permuted-bucket gap) (2024: 1.32 pp, limit 2.00 pp); planted same-game and future edits on the real 2025 played table with a control that must change. A gate failure stops the run BEFORE any arm is scored; `results.json` then records `gate_ok: false`, which does not count as the touch (nothing was scored), and the plumbing may be fixed and the run repeated once under the same rule. Any scored `results.json` blocks a rerun.

## Harness and refusals
`research/eval/recent_role_holdout.py` reuses (does not copy) the frozen RECENT_ROLE arms, walk, placebo and metrics. `run` refuses unless (1) a commit `prereg(RECENT_ROLE_HOLDOUT): <sha>` exists in git log and the text above the marker hashes to `<sha>`, and (2) `docs/HOLDOUT_ACCESS_LOG.md` holds a committed, unmodified row containing `RECENT_ROLE_HOLDOUT`, the first 8 hex of the sha, `PRE-REGISTERED` and `DEVIN CONFIRMED`; tests use fake git and temp files only. The repo's `MAX_SEASON = 2024` bound (loaders, asserts, `load_inputs` default) is raised to 2025 only inside `season_cap`, only after both checks, and restored on exit; `rehearse` never raises it.
Command (the main session, after the freeze commit and the committed log row; takes `data/ops/heavy.lock` itself, retry 120 s, removes only its own):
`.venv/bin/python -W ignore -m research.eval.recent_role_holdout run --sha <64 hex>`
Runtime estimate (from the rehearsal, 2024): about 2 minutes. Outputs `reports/prereg_recent_role_holdout/results.json` and `reports/prereg_recent_role_holdout.md`.

## What each choice protects against
* base = C with R unchanged: a holdout verdict on the model as frozen, not a re-tuned one. C' secondary: crediting the recent-role columns for what production's own columns do.
* Floor -0.010 (above), CI upper < 0, ECE, slices: a gain nobody could use, or one concentrated where it hurts.
* Past-only placebo and shuffle: capacity and pipeline artefacts. One run and the log row first: the holdout stays a holdout.

## Kill criteria
Fail on any pass condition: P(play) feature work closes for 2026-27, no variants, no reruns, no second follow-up. Gate failure after the freeze: stop, report, fix plumbing only (no rule change).

=== RESULTS BELOW ===

(not run)
