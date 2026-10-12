# RECENT_ROLE_FULL: recent-role minutes in the props heads AND the short-minutes classifier together (pre-registration)

Status: **FROZEN (Mon Oct 12 03:09 CDT 2026) by the main session under Devin's standing overnight approval ("we missed something and many tests were close"; "run in loops"); forking paths disclosed; a 2023/24 pass is a candidate only; Stage 2 only on Devin's words.** Earlier: **DRAFT (Mon Oct 12 03:08 CDT 2026), written by the modeler. Not frozen.** Queue item Q2 of the 2026-10-12 overnight loop (Devin: "we missed something and many tests were close").
The main session commits `prereg(RECENT_ROLE_FULL): <sha256>` BEFORE any FULL, HEADS, placebo or shuffled arm is fitted, then runs Stage 1. Everything above the line `=== RESULTS BELOW ===` is the rule.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RECENT_ROLE_FULL.md | shasum -a 256`. Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = the frozen holdout, never loaded in Stage 1).
No FULL / HEADS-in-the-mixture / placebo / shuffled arm has been fitted or scored; see Data exposure.

## The rule in five lines
1. Candidate FULL = production `context_residual` stat heads refit WITH the five columns `wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5` + the lower-tail `pi` refit with them (exactly PI_RECENT_ROLE's CAND `pi`), read out through the stored lower-tail mixture construction (stored `w_q`, production month blocks, production LightGBM, seed 0, no tuning). Nothing else changes (P(play) is not part of this rule).
2. Comparator A0 = stored production + stored mixture (`reports/lower_tail/components_*.npz`; pts integer CRPS 3.140236 / 3.176840), reproduced to 1e-6 first.
3. Metric: integer-support CRPS on played rows, per stat, FULL minus A0, paired, 95% percentile bootstrap clustered by `game_id` (2000 draws, seed 0); floor **-0.005**, CI upper < 0, BH over the m = 4 stats within each season (p_BH <= 0.05); select 2023, report 2024; both seasons must pass. Only FULL is the claim; the HEADS-only, PI-only (= T460) and FULL-minus-parts arms say where the gain comes from and cannot rescue or fail the rule.
4. Guards (fixed constructions): past-only placebo in BOTH components (FULL - PL_FULL <= -0.003 each season); the non-inert shared-permutation shuffle on BOTH components (unit-tested to fail on a planted signal) must not fire for any stat; pi ECE no worse by > +0.005; forecast coverage at tau 0.10/0.20/0.80/0.90 no worse than A0's by > 0.01; no slice (minutes < 5, bench, playoffs, cold start; n >= 300) worse by > +0.01; planted-future, control and missingness gates clean.
5. Stage 2 (declared now): if >= 1 stat passes, ONE logged 2025 touch for this never-evaluated model, only with a committed `HOLDOUT_ACCESS_LOG.md` row naming `RECENT_ROLE_FULL`, the sha8, `PRE-REGISTERED` and `DEVIN CONFIRMED`; the harness refuses otherwise and has no 2025 loader until then. A pass on 2023/24 is a CANDIDATE only (forking paths, below). A null is closed; at most one follow-up (new id), only after a pass.

## What Devin said
> "we missed something and many tests were close" (queue note, docs/reviews/overnight_2026-10-12.md).

## Hypothesis
Recent-role minutes (last game, last 3 and 5 games, minutes in the last 3 and 7 days) are real pre-tip information that production's trailing windows (`min5/min10/min40`) hold only as stale averages. Four tests have each found a real but individually sub-floor piece of it:
* P(play) (RECENT_ROLE_HOLDOUT, season 2025, T458): R minus base -0.0205 [-0.0232, -0.0179]. PASS, but P(play) never enters the conditional props score and is NOT in this rule.
* `pi` (PI_RECENT_ROLE, T460/T461): integer CRPS vs A0 pts -0.0059 / -0.0046, reb -0.0030 / -0.0026, ast -0.0011 / -0.0012, fg3m -0.0009 / -0.0003 (2023 / 2024); pts missed the 2024 floor by 0.0004.
* Heads (reports/feature_significance_2026-10-12.md, DESCRIPTIVE, pooled 2023-24, heads read out alone, not through the mixture): adding the five columns to the heads -0.0086 [-0.0111, -0.0062] pts, -0.0029 reb, -0.0020 ast, -0.0007 fg3m.
* WORKLOAD LOAD columns in the heads (T452): pts -0.0071 / -0.0082, wounded by the placebo.
The diagnostic reports/pi_transfer_diagnostic_2026-10-12.md found no structural cap in the pi-to-CRPS transfer: the gain is proportional to how far the classifier closes the oracle gap. The two components act on different parts of the distribution (the heads move the centre and scale of the played-normally component, `pi` moves the weight of the short component), so the conjecture is that the pieces add. The rule tests exactly that, once, with the guards that broke earlier near-misses.

## Honest prior (read first)
* Sum of the pieces, as a crude additive guess (pieces were measured in different constructions, so this is a ceiling-ish guess, not a forecast): pts about -0.012 to -0.015 (floor -0.005 would be cleared by a wide margin), reb about -0.005 to -0.006 (on the floor), ast about -0.003, fg3m about -0.0015.
* My probabilities that a stat clears the full rule in both seasons: **pts 0.75** (P(floor in both seasons) about 0.9; the placebo, shuffle and slice guards cost the rest), **reb 0.30** (the -0.003 placebo margin and the floor are both close), **ast 0.05, fg3m 0.01** (below the floor even if the pieces add fully; their oracle gaps are 6x and 10x smaller than pts). Probability at least one stat passes 0.75.
* Oracle context: pi := realised short flag gives pts -0.150 / -0.146, reb -0.060 / -0.061, ast -0.0245 / -0.0235, fg3m -0.0140 / -0.0120 against A0 (reproduced in the harness smoke run, descriptive). A pts gain of -0.012 would be about 8% of the pi-oracle gap, consistent with the transfer diagnostic (pi AUC 0.855 to 0.864 closed 2.4% of the gap).
* The heads' -0.0086 is a pooled, descriptive figure that came from reading the head out alone; inside the mixture the heads' share of the lower tail is lower (a row with `pi` near 0.2 has 20% of its mass on the short component). I expect the heads gain through the mixture to be somewhat smaller than -0.0086; the rule does not depend on this guess.
* Forking paths (disclosure, not a defence): the five columns were chosen after seeing the 2023-24 P(play) results ("the next-ranked columns on the adversary's 2023-24 table", RECENT_ROLE), and combining them into the heads AND `pi` was chosen after seeing T460 and the feature-significance table on the same 2023-24 rows. 2023 and 2024 are therefore NOT clean confirmation seasons for this combination; the select/report split protects against tuning inside this rule, not against the earlier looks. A pass on 2023/24 is a candidate; the 2025 touch (Stage 2) is the clean test, and 2025 has been seen only for the P(play) model with these columns (rows 2026-10-12 of the holdout log: the RECENT_ROLE_HOLDOUT touch and the adversary's diagnostic P(play) arms), never for a stat head or `pi`.

## Arms (per stat; heads walks and one shared pi walk per arm; all on the production month blocks, 2022 = warm-up)
| arm | stat heads | pi | role |
|---|---|---|---|
| A0 | stored | stored | comparator |
| **FULL** | + 5 columns | + 5 columns (CAND) | **the claim** |
| HEADS | + 5 columns | stored | descriptive: where the gain comes from |
| PI | stored | + 5 columns | descriptive: must reproduce T460 exactly |
| PL_FULL | + past-only placebo of the 5 | + past-only placebo of the 5 | placebo guard |
| SHUF_CTRL | production features, resid permuted | COMMON, labels permuted | shuffle guard control |
| SHUF_FULL | + 5 columns, SAME permuted resid | + 5 columns, SAME permuted labels | shuffle guard candidate |
| ORACLE | stored | realised short flag | descriptive ceiling |

* Heads: `ContextResidualModel(stat, cfg, stat_feature_names(stat) + extra)` fit on all played rows strictly before each month block (`collect_heads`, a line-for-line copy of `lower_tail_eval.collect_stat` with an explicit feature list and the shuffle; a unit test proves it bit-identical to `workload_eval.collect_components`, and the gate proves the no-column refit equals the stored components). Only the mean and scale heads and the calibration z change; the short component `w_q` is stored and unchanged (it depends on the real short flag and y/m of training rows, not on either head or the classifier).
* `pi`: `PR.walk_pi` of `research/eval/pi_recent_role_eval.py` with `COMMON_FEATURES + 5 columns` (the frozen T460 construction).
* Past-only placebo: `research/recent_role/placebo.py` (each row takes the five columns of one uniformly drawn STRICTLY EARLIER row of its player-season; NaN for the first row, 2.2%), as in RECENT_ROLE and PI_RECENT_ROLE; used in both components.
* Shuffle: per block ONE permutation of the training residuals (heads, `default_rng([0, block])`) and of the training short labels (pi, same seeds), identical for SHUF_CTRL and SHUF_FULL (label hashes asserted equal at run time), scored against the real outcomes. Unit tests: identical hashes, real labels do not fit, and on a planted-signal frame the check passes with the permutation and FAILS with `permute=False`.
* Metric: `to_integer_support` of the 199-point mixture grid, CRPS on the integer grid for EVERY arm (the support artifact, T114-T118); per-row differences, game-clustered percentile CI, clustered-bootstrap two-sided p (`WE.delta_stats`); MDE = half the CI width. BH (`bh_adjust`) over the four stats within each season; family m = 4, declared. Only FULL - A0 is in the family; every other contrast is descriptive.

## Pass rule (all must hold, per stat)
1. 2023 (select): FULL - A0 <= -0.005, CI upper < 0, p_BH <= 0.05.
2. 2024 (report): the same.
3. `pi` calibration: ECE(FULL pi) - ECE(A0 pi) <= +0.005 in both seasons (10 equal-width bins; one guard, `pi` is shared by the stats).
4. Forecast calibration: randomised-PIT coverage P(PIT < tau) at tau 0.10, 0.20, 0.80, 0.90; |coverage_FULL - tau| - |coverage_A0 - tau| <= 0.01 at every tau, per stat and season.
5. Slices (per stat, per season; FULL - A0; a slice with n < 300 is reported and cannot fail): minutes < 5 (realised), bench (as-of `starter10 < 0.5`), playoffs (`game_id` prefix `004`), cold start (`n_prior < 10`). None worse than +0.01. Descriptive also: starter, short, not short, minutes < 10.
6. Placebo: FULL - PL_FULL <= -0.003 in each season for that stat.
7. Shuffled-label check: FAILS if SHUF_FULL - SHUF_CTRL <= -0.001 with CI upper < 0 for ANY stat in ANY season; one failure disqualifies every stat.
8. Gate, plants and missingness pass (below).
Naming: a pass is "the recent-role set in the heads and the short-minutes classifier together". No attribution to one component is made from the rule; HEADS, PI and the partial contrasts are reported descriptively. The claim covers props CONDITIONAL on playing; it says nothing about P(play).

## Secondary and descriptive (cannot rescue or fail the rule)
* HEADS - A0, PI - A0, FULL - HEADS, FULL - PI per stat and season (is the gain additive? sub-additive?); the share of the oracle gap captured.
* `pi` itself: short-flag log loss, AUC, ECE for A0 / FULL / PL_FULL with FULL - A0 game-clustered CI.
* Decision score at DraftKings T-60 (`nba.markets.decision_scoring`, legs file `data/ops/model_clv_pairs`, 2023-24): A0 and FULL integer grids re-priced. Descriptive; a props edge needs the live log.
* Calibration table (coverage) for A0 and FULL per stat and season; the three-line summary of where the gain sits (slice table).

## Slices, protections and what each choice guards against
* Control refits equal the stored components to 1e-6 (gate): crediting refit noise, thread nondeterminism or the 2026-10-11 clean-up (COACH_PHASE found stored inputs drifting) to the new columns.
* Floor -0.005 and the stated prior: a statistically clear gain nobody could use (the recent-role gains in pieces were all clear and sub-floor); naming the likely outcome in advance keeps a sub-floor result from being rescued.
* Integer-grid CRPS for every arm: the support artifact. Game-clustered CI and BH in both seasons: row-level anti-conservatism and four stats.
* Past-only placebo in both components and the shuffle in both components: capacity, player-level scale, and a pipeline-manufactured gain (the two WORKLOAD harness defects, T454/T459). The shuffle applying to the heads AND `pi` closes the route where only one component is checked.
* Coverage and slice guards: a gain bought by mis-calibrating the forecast or by hurting near-DNP rows (SEQ blends failed exactly this slice, T442).
* Select 2023 / report 2024, both must pass: a gain found in one season. The forking-paths disclosure above: the 2023/24 split is not clean for this combination, so the claim stops at "candidate" until 2025.
* The freeze-commit and holdout-row refusals in the harness: starting from an uncommitted rule; an unlogged 2025 touch.

## Stage 2 (2025, declared now, not run)
Only if Stage 1 passes for >= 1 stat AND Devin confirms with a committed `HOLDOUT_ACCESS_LOG.md` row naming `RECENT_ROLE_FULL`, the sha8, `PRE-REGISTERED` and `DEVIN CONFIRMED`. One touch for the never-evaluated model "context-residual heads and short-minutes classifier both refit with the five recent-role columns": A0 and FULL both refit live on the 2025 walk with the same code under a season cap (as `research/eval/recent_role_holdout.py`; no stored 2025 components exist and none may be read), same rule on the carried stats (floor -0.005, CI upper < 0, same guards, BH over the carried stats), one run, no reruns. Verified by reading `docs/HOLDOUT_ACCESS_LOG.md` (2026-10-12): the only 2025 rows touching these columns are the P(play) model (RECENT_ROLE_HOLDOUT, R and C') and the adversary's P(play) diagnostic arms; no stat head or lower-tail `pi` with them has been evaluated on 2025, and the harness gate asserts the log names no `RECENT_ROLE_FULL` row. The 2025 loader is written in the commit that follows the confirmed row and does not change the rule. A 2025 pass is a production-change PROPOSAL only (Devin's decision); the live log is still the only truly clean test.

## Data exposure (honest)
Seen by me before this draft: the stored components and A0 reproduction; T460 (the PI arm) and T452/T453 (WORKLOAD LOAD in the heads); the feature-significance table (heads + five columns, descriptive, direct read-out); the oracle gaps; the gate numbers below; and the plumbing smoke run (`score --smoke`, every non-A0 arm replaced by A0, no candidate information: all FULL/HEADS/PI/placebo/shuffle deltas exactly 0.0, the realised-flag ORACLE arm real; writes only `reports/prereg_recent_role_full/smoke/`). The gate also fits the `pi` CAND walk and scores the PI arm, because that arm is T460, already frozen and scored; nothing new is learned from it. NOT seen: any FULL, HEADS-through-the-mixture, placebo or shuffled arm score.
Choices fixed before scoring that a reader could not guess: BH in both seasons; one shuffle failure disqualifies all stats; the placebo-missingness gap is reported, not a criterion (as PI_RECENT_ROLE); `bench` is as-of, `min<5` is realised; coverage tolerance 0.01; ECE 10 equal-width bins; ORACLE uses the unclipped flag; the same permutation seeds for heads and `pi`.

## Minimum-data gates (non-negotiable 8): measured Mon Oct 12 03:02-03:06 CDT 2026 on `nba.duckdb` (read-only), seasons 2022-2024; scored arms: the control refits and the PI arm (= T460)
Command: `.venv/bin/python -W ignore -m research.eval.recent_role_full_eval gate --allow-draft` (201.5 s wall, JSON `reports/prereg_recent_role_full/gate_draft.json`). **All PASS (16 of 16).**
* A0 reproduction from stored components: max abs diff 0.0 over 4 stats x 2 seasons, n equal; pts 3.140236 / 3.176840.
* The five columns attach to every A0 row: 55,320 of 55,320; null or NaN count 0 for each.
* **Heads control refit** (production features only, `collect_heads`, all four stats) equals the stored components: rows equal, max abs diff **0.0** in centre, scale and calibration z/centre/scale for pts, reb, ast and fg3m. So a FULL - A0 delta is a pure feature effect.
* Control refit of `pi` equals the stored `pi`: max abs diff 0.0, n 55,320.
* **PI arm reproduces T460** (frozen PI_RECENT_ROLE deltas, all four stats x two seasons): max abs diff 0.0 (pts -0.005914 / -0.004618).
* Missingness of the five columns by realised-minutes bucket (<5, 5-10, 10-20, >20): max gap 0.0 pp (limit 2.00 pp). The past-only placebo columns have NaN rows (2.2% / 1.6%) whose share differs by bucket by 3.77 pp (permuted-bucket gap 0.20 pp); reported, not a criterion (conservative for FULL: a NaN can act as a flag for the placebo).
* Plants (real tables, reused from WORKLOAD): same-game 308 rows identical, control 13,770 rows change, future 14,466 identical, schedule 6,618 identical. Walk plant for `pi`: 13,691 rows before the cutoff bit-identical, 12,790 later rows change. **Heads plant (new, pts):** labels (`pts`, `minutes`) and the five columns of rows dated on/after 2025-01-24 overwritten; centre and scale of the 10,064 rows in earlier blocks bit-identical (max abs diff 0.0); 12,790 rows in later blocks change (max 97.4): the walk is as-of and the control is not inert. Nothing scored against a label.
* Slice n (rows), 2023 / 2024, identical for the four stats: all 27,619 / 27,701; minutes < 5 2,383 / 2,326; bench 15,352 / 15,589; playoffs 1,684 / 1,804; cold start 574 / 464 (smallest, above 300).
* Holdout log: no row names `RECENT_ROLE_FULL`.
* Unit tests (`tests/research/recent_role_full`, 9): freeze and holdout refusals with fake git, no 2025 loader, heads walk bit-identical to `workload_eval.collect_components` (equivalence proof), shuffle shares one permutation, the shuffle check can fail (planted signal), as-of planted future with a non-inert control, the decision rule failing each way (including coverage), the arms and floors as declared.

## Kill criteria
* A gate or plant fails after the freeze: stop, fit nothing further, report; the fix is a new id.
* Any FULL, HEADS-through-the-mixture, placebo or shuffled arm scored before the freeze commit: the rule is void (this draft has none).
* Stage 1 not a pass for any stat: closed. No other columns, windows, learners or component combinations; at most ONE follow-up (new id), only after a pass.
* Shuffle check fires for any stat/season: no stat passes; investigate the pipeline under a new id, do not re-run.

## Outputs and the one command
`research/eval/recent_role_full_eval.py` (gate, arms, score, run, holdout refusal), tests in `tests/research/recent_role_full/`. Outputs `reports/prereg_recent_role_full/{gate,results}.json` and `reports/prereg_recent_role_full.md`; per-(stat, arm) checkpoints in `data/ops/recent_role_full_cache/` (small; a rerun resumes; heads walks print one line per block).
The command (the main session, after the freeze commit; it takes `data/ops/heavy.lock` itself with retry 120 s and removes only its own):
`.venv/bin/python -W ignore -m research.eval.recent_role_full_eval run --sha <64 hex>`
runs gate -> arms (4 `pi` walks, 16 heads walks) -> score (A0/FULL/HEADS/PI/PL_FULL/shuffled/oracle for four stats, guards, decision score) -> report. Runtime on the Mac CPU, single process: gate 3.4 min measured; arms about 6-8 min (estimate: `pi` walks about 15 s each, a heads walk 18 blocks at about 1-2 s per block); score about 1.5 min (smoke measured 61 s with A0 in every arm); about 12 min in total, not measured end to end.

=== RESULTS BELOW ===

(not run)
