# ROAD_STARS: a star x road term in the points heads (pre-registration)

Status: **DRAFT (Mon Oct 12 12:16 CDT 2026), written by the modeler. NOT frozen. Devin confirms before any freeze. The minimum-data gate below FAILS (power), so by non-negotiable 8 the rule as asked cannot be frozen; see "Gate verdict" and the options.**
Frozen on Devin's choice of option 1 despite the failed power gate G5 (non-negotiable 8 exception, Devin's decision); expected result null.
Everything above the line `=== RESULTS BELOW ===` is the rule. No ROAD, placebo or shuffled arm has been fitted or scored. Seasons are `games.season` (2023 = 2023-24, 2024 = 2024-25; 2025 never loaded).

## What "+0.36" measured (source: reports/model_miss_questions_2026-10-10.md, Q6(e) and Q6 candidate 3; DECISIONS 2026-10-12 12:12 "Road stars +0.36: draft a rule")
* Stat: points. Universe: 7,022 star pts rows (127 players), seasons 2023 and 2024, star = prior-10 mean pts >= 20.
* Residual = actual - OOF integer mean of the production props model (positive = the model was LOW), from the stored model_rows files (before the lower-tail mixture).
* Away (road) stars +0.245 pts (n 3,507), home stars -0.112 (n 3,515); difference 0.357 pts, 95% CI [0.02, 0.70], game-clustered bootstrap (1,000 draws). Same sign in both seasons (away +0.092 / +0.415, home -0.143 / -0.078).
* Status in the source: descriptive, no multiplicity correction across about 20 cells of Q6; below the report's 0.5 pt flag; labelled "a maybe rather than a yes". The CI lower bound is 0.02: with ~20 cells one such cell is expected by chance.
* Re-measured on the A0 mixture (this draft, gate G3 below): road-minus-home star residual 0.40 [0.04, 0.76] pooled, 0.32 [-0.18, 0.82] in 2023 and 0.50 [-0.05, 1.03] in 2024. The gap survives the mixture. Both cells sit BELOW zero (road -0.34, home -0.75 on the mean of the integer quantile grid; grid mean vs scoring mean not verified), so a road-minus-home term repairs only about +-0.2 pts around a common star offset.

## The rule in five lines
1. Hypothesis: stars are forecast too high at home and relatively too low on the road by about 0.4 pts, and a star x road term in the production points heads removes it. Candidate ROAD = production `context_residual` pts heads refit with two added columns, `star10` = 1[m10_pts >= 20] and `star10_away` = star10 x (1 - is_home_f), both as-of; lower-tail `w_q` and `pi` stored and unchanged (production mixture, month blocks, seed 0, no tuning). Pts only (the only stat in the source).
2. Comparator A0 = stored production + stored mixture (`reports/lower_tail/components_pts.npz`; integer CRPS 3.140236 / 3.176840, reproduced to 0.0).
3. Metric: integer-support CRPS on played rows, ROAD minus A0, paired, game-clustered 95% percentile bootstrap (2000 draws, seed 0). Primary cell = STAR rows (m10_pts >= 20): floor -0.005, CI upper < 0. Overall (all rows) is a NO-HARM guard (point <= +0.0005, CI upper <= +0.001), not a win condition (see Gate verdict: -0.005 overall is arithmetically unreachable). Select 2023, report 2024; both must pass. BH m = 1 (pts only; reb, ast, fg3m are descriptive no-harm checks and cannot rescue).
4. Guards: planted-future and control gates; past-only placebo (ROAD - PL_ROAD <= -0.003 on the star cell); permutation-null-sized noise guard (below); forecast coverage at tau 0.10/0.20/0.80/0.90 no worse than A0 by > 0.01 on stars and overall; slices (n >= 300) not worse by > +0.01: non-star, home stars, road stars, bench, playoffs, minutes < 5, cold start.
5. 2025: ROAD is a never-evaluated model and so has its own touch, but only if Stage 1 passes in both seasons, with a committed HOLDOUT_ACCESS_LOG row (`ROAD_STARS`, sha8, `PRE-REGISTERED`, `DEVIN CONFIRMED`) and Devin's yes. The plain production props model has NO unspent 2025 touch (context_residual: CLEAN touch spent 2026-10-08, CONFIRMED all four stats; RECENT_ROLE_FULL spent 2026-10-12; the 2025 replay shows production primary scores descriptively and seals shadow-arm deltas). The honest venue for a clean test is the 2026-27 live log as a shadow arm (docs/FORWARD_PREREG_2026_27.md already slices home/away).

## Hypothesis and honest prior
Production already has `is_home_f` as a feature, so a tree could in principle learn a home x star split; the residual that survives is exactly what the trees did not find. Two readings: (a) a real, small, stable asymmetry that needs an explicit term (sample too thin for the trees: 6,706 star rows of 55,320); (b) a noise cell (CI lower bound 0.02 among ~20 cells; persistence of per-star residuals only 0.24). My probabilities: gain on the star cell clears the floor -0.005 in both seasons **0.02**; direction negative and non-harming 0.45; any claim before the live log: none. Expected delta on stars if the full road-minus-home gap were removed: about -0.001 to -0.003 (gate G5), well under the floor.

## Star definition (as-of, leakage)
star10 = 1[m10_pts >= 20], where `m10_pts` is the production feature (mean over the player's previous 10 played games, rows with n_prior >= 5, crossing season boundaries, strictly before tonight; the existing as-of planted-future test covers it). Not "top-3 minutes" and not season average (both use or invite later information). is_home_f is known from the schedule. Missingness: m10_pts has 0 nulls on all 55,320 A0 rows (gate G2), so there is no NULL pattern to leak.

## Guards in detail and what each protects against
* Permutation-null-sized noise guard (BEST_PRACTICES 2026-10-12, T473): the frozen 2025 shuffle guard fired in 19% of honest shuffles. Here the guard is a random-flag placebo: K >= 50 arms in which the road flag is replaced by a fair coin per (team, game) (same coin for all teammates, so the star structure is kept; seed per arm), the same two columns built from it, scored against real outcomes on the SELECT season (2023, pts, star cell). This measures the null distribution of "PLACEBO - A0" including heads fit noise. ROAD must lie below null mean - 2 null sd AND at or below the floor on the star cell, in 2023; the same threshold (null sd carried over, K = 50 more draws allowed on 2024 if time permits) in 2024. The null sd is recorded in the result next to the bootstrap CI. The K arms contain no candidate information, so they are scored and the threshold frozen BEFORE the ROAD arm is scored (harness order: null -> freeze threshold in the results dir -> ROAD). Protects against calling a fit-noise gain a road effect, and against a guard that fires 1 time in 5 on pure noise.
* Past-only placebo (each star10_away takes the value of a strictly earlier row of its player-season): a capacity or player-level-scale gain; ROAD - PL_ROAD <= -0.003.
* Planted future: overwrite labels and flags on/after a cutoff; centre/scale of earlier blocks must be bit-identical, later blocks must change (non-inert control), as in RECENT_ROLE_FULL. Control refit with the two columns dropped must equal the stored components to 1e-6 (so the delta is a pure feature effect; RECENT_ROLE_FULL showed this equality, max abs diff 0.0).
* Floor and cell: a clearly-nonzero gain nobody could use; the star cell, because ROAD touches only 12% of rows and an all-rows number would dilute it to nothing.
* Integer-grid CRPS for every arm; game-clustered CI (row CIs were anti-conservative); select 2023 / report 2024.
* No-harm overall and slices: a star gain bought by hurting everyone else (the term moves trees' splits for all rows).

## Slices and sizes (gate G4, real data)
| season | rows | stars | star road | star home | star players | star games | non-star |
|---|---|---|---|---|---|---|---|
| 2023 | 27,619 | 3,533 (12.8%) | 1,757 | 1,776 | 71 | 1,300 | 24,086 |
| 2024 | 27,701 | 3,173 (11.5%) | 1,590 | 1,583 | 79 | 1,262 | 24,528 |
(The source's 7,022 star rows include rows with n_prior < 5 that the A0 frame drops; 6,706 here.) Every slice named above has n >= 300 except cold-start stars (reported, cannot fail).

## Minimum-data gates (non-negotiable 8), measured Mon Oct 12 12:14-12:16 CDT 2026, `nba.duckdb` read-only, seasons 2023-24, no model fit (scratch scripts in the session scratchpad: rs_gate.py, rs_ceiling.py)
* G1 A0 reproduction: mixture integer CRPS from stored components vs candidates.json, max abs diff 0.0 for pts, reb, ast, fg3m x 2023/2024 (pts 3.140236 / 3.176840). PASS.
* G2 as-of columns: `is_home_f` and `m10_pts` attach to all 55,320 rows, 0 null each. PASS.
* G3 the effect exists on A0 (descriptive): see "What +0.36 measured". Road-minus-home star residual 0.40 [0.04, 0.76] pooled. PASS (it is there; it is small).
* G4 slice sizes above. PASS (smallest cell 1,576).
* G5 **POWER, FAIL.** Using A0 itself with the star centre moved by the report offsets (+0.25 road / -0.11 home; no candidate information, arithmetic only): star-cell integer CRPS delta +0.0017 (2023) / -0.0011 (2024), pooled +0.0004; moving road stars by +0.36 alone: +0.0057 / +0.0023 (these move CRPS up in the wrong sense because both cells are already below zero on the mixture, i.e. the sign of the fix depends on the common star offset, not only on the gap). The magnitude is the point: best-case gains are of order 0.001-0.003 on stars and 0.0004 overall. Minimum detectable effect (half the game-clustered CI width): star cell about 0.0037 / 0.0038 per season (0.0026 pooled); all rows about 0.0004-0.0005. A jitter of +-0.3 pts on star centres has per-row CRPS-delta sd 0.175 (MDE 0.0061 on one season of stars). So the declared floor on stars (-0.005) exceeds both the plausible effect and the per-season MDE margin, and the overall floor (-0.005) is >10x the best case. A rule that cannot pass its own gate is not frozen (F9, F12).

## Gate verdict and options for Devin (decision needed; safe default = option 2)
1. Freeze as drafted: it can only produce a null (probability of a pass about 0.02); costs about 40 min of CPU (null arms K = 50 x about 20-40 s plus ROAD and placebo walks, estimate, not measured) and spends a 2025 touch only on a pass. Not recommended.
2. **Do not freeze; carry star x road as a declared descriptive slice of the 2026-27 live log** (no fit, no touch): pts residual by star x home/away with game-clustered CI, prior stated here (road-minus-home about +0.4 pts, no action below 0.5). With ~1,600 star-road rows per season the CI half-width is about 0.35-0.4 pts, so it resolves only a 0.5-pt gap; that is the honest sense in which this is a "maybe". Pre-register the slice in FORWARD_PREREG's slice list.
3. Freeze a cheaper, lower-power-honest version: a star-cell floor of -0.002 and an offsets-only arm (no refit). Changes the floor Devin named; MDE 0.0037 per season still exceeds -0.002, so it passes only if the true gain is about 0.004+. Not recommended.

## Kill criteria
* Any gate or plant fails after a freeze: stop, fit nothing, report; a fix is a new id.
* Any ROAD, placebo or null arm scored before the freeze commit: rule void (none scored in this draft).
* Stage 1 not a pass in both seasons: closed. At most one follow-up (new id), only after a pass.
* Null guard fires or ROAD sits within 2 null sd of the noise mean: no claim.

## Outputs and the command (not written yet; harness only after Devin chooses option 1 or 3)
Would be `research/eval/road_stars_eval.py` (gate, null, arms, score) cloning `recent_role_full_eval` with `collect_heads` and an explicit feature list; tests in `tests/research/road_stars/`. Heavy runs under `mkdir data/ops/heavy.lock` (retry 120 s, `rmdir` only a lock this job created).

=== RESULTS BELOW ===

(not run)
