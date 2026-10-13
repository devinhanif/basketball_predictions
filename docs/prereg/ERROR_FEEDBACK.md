# ERROR_FEEDBACK: a player's own recent errors against a report-free base, as a props input (pre-registration)

Status: **FREEZE CANDIDATE, written by the modeler Mon Oct 12 15:48 CDT 2026, revised Mon Oct 12 19:42 CDT 2026 (clocks read by `date`).** It becomes the rule only when the main
session commits `prereg(ERROR_FEEDBACK): <sha256>` of everything above the line `=== RESULTS BELOW ===`
(`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ERROR_FEEDBACK.md | shasum -a 256`); the harness refuses any arm-bearing
command without that commit. Approved for freezing by Devin, 2026-10-12: "props yes, lets see this" (design: select on history, report
once on 2025, a pass is a production proposal for 2026-27; DECISIONS 2026-10-12 14:17 CDT). Idea: Devin, "recent errors would work"
(DECISIONS 2026-10-12 14:05 CDT). Revision (Devin, 2026-10-12: "do recent errors to do max data usage"): the base now starts in
2019-20, the earliest season with complete box scores we hold, and the select stage is 2021-2024 because 2021's first block now
clears the unchanged coverage gate (0.876 vs 0.50). Earlier drafts (2023/24 design, stopped at their gate) are in git history (059f4de, c0614d5); nothing
from them is carried over except what is restated here. Season labels are `games.season` (2022 = 2022-23, 2025 = 2025-26, the frozen
holdout).

## The rule in six lines
1. **Claim arm EF** = production `context_residual` heads (production feature list, `configs/context_residual.yaml`, seed 0, no tuning)
   plus five as-of columns from the player's own recent errors against a **report-free walk-forward base**: `ef_res_l3`, `ef_res_l5`,
   `ef_res_l10`, `ef_pit_l5`, `ef_n` (below). **Comparator A0** = the same heads without the columns. Same rows, same training window
   (every row since 2019-20 dated before the month block), same readout (production quantiles mapped to integer support).
2. **Metric:** integer-support CRPS, **pts primary**; EF minus A0, paired, 95% percentile bootstrap clustered by `game_id` (2,000 draws,
   seed 0); date-clustered CI reported beside it. **Pass: point <= -0.005 AND CI upper < 0.**
3. **Stage 1 (select) = seasons 2021-2024 pooled**, run once after the freeze (`select --sha`). **Stage 2 (report) = season 2025, once**,
   only after a Stage 1 pass and a committed HOLDOUT_ACCESS_LOG row quoting Devin's words (`run --sha`). Both pass = a production
   proposal for 2026-27 (Devin's decision). Either fails = closed.
4. **Guards, each stage, pts:** past-only placebo PL (the same five statistics over the player's base rows 11-20 back): EF - PL <= -0.003;
   shuffled-residual null: EF - A0 <= (null mean - 2 null sd) over K = 50 permutation draws (Stage 1 null measured BEFORE the freeze,
   Stage 2 null computed inside the touch); coverage at tau 0.10/0.20/0.80/0.90 not worse than A0 by > 0.01; slices (n >= 300) not worse
   by > +0.01; missingness no larger than its as-of cause (G2 below); Stage 1 only: A0 reproduces the pre-freeze null run's A0 to
   1e-9.
5. **Secondary:** reb, ast, fg3m EF - A0 on the same stage, BH over m = 3, labels only ("improves" / "worse" / "no label"); they carry
   no pass.
6. A null is closed; at most one pre-registered follow-up; a new question is a new id. Nothing about this rule changes after a result.

## Hypothesis
Production's errors persist within a player: a player the model has been under-forecasting keeps beating it for a while (role growth,
a hot stretch, an unmodelled matchup or usage trend), and feeding the forecaster his own recent misses corrects part of that. The
errors are measured against a report-free base so that the column means the same thing in every season (see "Why a report-free base").

## Columns (as-of; `research/features/error_feedback.py`, unit-tested)
* **Base.** For each stat, the production `context_residual` mean and scale heads with the injury-report columns dropped
  (`has_report`, `n_out_rot`, `vac_min`, `vac_<stat>`), refit walk-forward in calendar-month blocks over seasons 2019-20 to 2024-25;
  each block is fit on every played row (`n_prior >= 5`) dated before the block's first day (no window cap, no weights) and is fit only
  when at least 1,500 such rows exist, so the first base block is December 2019 (3,927 training rows from October-November 2019;
  October and November 2019 rows have no base forecast). Bubble games (2019-20) get base rows like any other game; they are never
  evaluation rows. The base forecast of a row is the production quantile construction
  (`c + s * empirical z quantile` on 199 equal-mass taus) mapped to integer support (`ceil(q - 0.5)`). Per row: integer median
  (the 100th of 199 quantiles), residual `y - median`, mid-PIT `(F(y - 1) + F(y)) / 2` on the integer grid.
* **Columns of a row of player i dated D:** over i's base rows dated **strictly before D** (any team, crossing seasons; latest first,
  at most 10): `ef_res_l3/l5/l10` = mean residual over the latest 3/5/10 (or as many as exist), `ef_pit_l5` = mean mid-PIT over the
  latest 5, `ef_n` = how many (<= 10). NaN and `ef_n = 0` when none. Rows on the same date never feed each other; a value outside a
  row's window cannot move it (explicit window sums, no running cumulative sum).
* **Placebo PL:** the same five statistics computed after skipping the latest 10 earlier rows (rows 11-20 back), names `pl_*`.
* **Shuffled SH_k (null and guard):** base residuals and mid-PITs permuted together across rows within each season (seed 1000 + k),
  then the same five statistics, names `sh_*`. Timing and player of every base row are kept; the link to the player is broken.
* The base is stored for 2019-2024 only (`save_base` refuses any row with season > 2024, tested); the 2025 base is regenerated inside
  the touch by the same code, every 2025 block fit on all rows dated before it.

## Arms
| arm | heads | stage | role |
|---|---|---|---|
| A0 | production names | 1, 2 | comparator |
| **EF** | + `ef_*` (5) | 1, 2 | **the claim** |
| PL | + `pl_*` (5) | 1, 2 (pts) | past-only placebo guard |
| SH_k, k = 0..49 | + `sh_*` (5) | 1 (pre-freeze), 2 (inside the touch), pts | permutation null and noise guard |
| A0P | production names, production window (2022 onward features and training) | 1 (2023-24), 2, pts | descriptive only: EF and A0 against what production actually runs, on common rows |

All arms: month blocks, a block is fit when >= 1,500 training rows exist (production's floor), production hyper-parameters, seed 0,
no tuning, no early stopping, calibration window = the newest 15% of training rows (production `split_calibration`).

## Pass rule (pts; every item must hold in the stage)
1. EF - A0 <= -0.005 and game-clustered CI upper < 0.
2. EF - PL <= -0.003 (point).
3. EF - A0 <= mean(SH_k - A0) - 2 x sd(SH_k - A0) over the stage's K = 50 draws (the null line).
4. Randomised-PIT coverage on the integer grid at tau 0.10, 0.20, 0.80, 0.90: |cov_EF - tau| <= |cov_A0 - tau| + 0.01.
5. Slices with n >= 300 (bench `starter10 < 0.5`, starters, cold start `n_prior < 10`, realised minutes < 5, playoffs, `ef_n < 3`,
   each season in Stage 1, so 2021-22, the hourly-report season, is its own slice): EF - A0 <= +0.01.
6. Missingness: the gap, across realised-minutes buckets (<5, 5-10, 10-20, >=20; buckets with n >= 300), in the share of rows with
   `ef_n < 3` exceeds the same gap of the as-of indicator `n_prior < 8` by at most 0.01, AND among rows with `n_prior >= 10` it is at
   most 0.02 (`missingness` in the harness).
7. Stage 1 only: A0's centre and scale reproduce the pre-freeze null run's A0 to 1e-9 (same code, same data).
Stage 1 passes -> Stage 2 may run (Devin's words in the holdout row). Stage 2 passes -> production proposal. Any other outcome: closed.

## Why these choices (what each protects against)
* **Why a report-free base.** Production's heads read injury-report columns; in 2021-22 the report was hourly and the latest usable
  snapshot about 2 h fresher than in 2022-24 (T315; classifier AUC 0.80). Residuals of a report-reading base would carry that era into
  the feature. The base reads no report, so the column is built the same way in 2019-21, 2022-24, 2025 and live.
* **Base from 2019-20, select 2021-2024.** The minimum-data gate requires that at least half the training rows of the first select
  block carry the column (`ef_n >= 3`). With the base from 2020-21 (first version of this draft) 2021-10-01 measured 0.498 and select
  was cut to 2022-2024; Devin asked for maximum data use, the base was extended to 2019-20, and the same unchanged threshold now reads
  0.876 at 2021-10-01 (outcome-free). The 2021-22 rows are evaluation rows again: the column is report-free, but A0's and EF's heads
  read the hourly 2021-22 report identically (T315); the contrast is paired, so the era moves both arms, and the 2021 slice is shown.
* **Readout = production quantiles on integer support, not the lower-tail mixture of the earlier draft.** The stored mixture (pi, w_q)
  exists only for 2023-24, production serves the plain quantiles (the mixture is a shadow), and a second walk-forward classifier would
  read report columns. Both arms share the readout, so the contrast isolates the heads. Every arm is scored on the integer grid
  (T114-T118).
* **Both arms on the 2019-start window, A0P descriptive.** A fair comparison needs the same rows and training set for both arms; 2022-23
  cannot be evaluated with production's own window (it is production's warm-up). HISTORY_WINDOW_3 found the 2019-start window itself
  +0.0035 [+0.0000, +0.0073] worse than production on 2024 pts (T421), so A0P is reported beside every result: a production proposal is
  EF on the 2019 window, and Devin sees EF against production's own window before deciding.
* **Floor -0.005 with CI upper < 0:** the house props floor; a significant 0.001 is not worth a production change.
* **Placebo shifted by 10 rows:** if EF works through a stable player bias (identity, not recency), the stale window carries it too;
  EF must beat it by 0.003. Deterministic (no draw), same construction as EF.
* **Shuffled-residual null sized from K = 50 draws:** a GBM given five more columns can gain from capacity; RECENT_ROLE_FULL's fixed
  shuffle threshold fired by chance on 2025 (T473; BEST_PRACTICES 2026-10-12). The guard compares EF against the spread of noise arms
  with the same column structure, under real labels.
* **Game clustering, date CI reported:** teammates share a game; same-night news shares a date.
* **Missingness audit:** the one leak that passed three experiments was a NULL pattern tracking tonight's minutes
  (`opp_adjusted_ridge`).

## Leakage design
* Base forecasts are walk-forward: the forecast of a row comes from a model fit on rows dated before its month block; the column of a
  row dated D reads base rows dated before D only (planted-future tests on synthetic and real data; non-inert past controls).
* Inside a refit, a training row's column was built from base rows before THAT row; the column is NaN for rows before December 2020
  (October-November 2019 rows: no base yet): its missingness is a function of calendar time and history length, audited below.
* No column reads tonight's box, lineup or report. Labels and realised minutes are used only as targets and slice strata.

## Forking paths and data exposure (read first)
* **Seen on the select seasons before this freeze:** the persistence slope of production's residuals, 0.149 [0.120, 0.176] (2023) and
  0.102 [0.071, 0.133] (2024), game-clustered, and the FULL-overlap slopes 0.126 / 0.075 (the 13:57 and 14:08 gates; a look at the
  comparator's errors on 2023-24, disclosed then). The 5-game window was chosen before that look; Devin proposed the idea.
* **Computed before this freeze (no EF, PL arm fitted):** the base on 2019-2024 (fit on outcomes; no base score computed or printed);
  the permutation null (A0 and SH arms on 2021-2024, 19 of 50 draws so far: only SH - A0 deltas and their spread are summarised; A0's own score level is
  not reported); training and evaluation coverage, missingness by realised minutes, the real-data planted-future check, and the
  harness reproduction of stored production forecasts (forecasts only). History of this draft, all before any arm: base from 2020-21
  -> 2021 coverage 0.498 -> select 2022-2024 and a 2022-2024 null (15:39 CDT); Devin's revision -> base from 2019-20 -> 2021 coverage
  0.876 -> select 2021-2024 and a new 2021-2024 null; the first base and null are kept aside in
  `data/ops/error_feedback_cache_v1_base2020/` and were not used by this version.
* **2025 is clean for this model:** no ERROR_FEEDBACK row in docs/HOLDOUT_ACCESS_LOG.md; no EF model has been fitted or scored on 2025.
  Stage 2 is the clean test; Stage 1 is a candidate filter on seasons whose comparator errors were partly seen.

## Playbook check (docs/MODEL_PLAYBOOK.md; class: gradient-boosted trees, LightGBM context residual)
1. **Information or capacity?** Not new pre-tip information in the report/lineup sense: the columns are a function of past box scores
   that the heads already see as recency means. What is new to the heads is their own (report-free) past errors, which a GBM cannot
   compute from its inputs. The class check "does the arm only re-express the base?" is answered by design, not assumed: the past-only
   placebo (same statistics, rows 11-20 back) and the shuffled-residual null with the same column structure must both be beaten, and
   FULL (recent-role minutes) was measured to absorb only 15-26% of the persistence. Our record says re-expressions tie; this rule is
   built so that a tie is what it reports.
2. **Was the training data enough?** Trees need 2-4 seasons; every select block trains on at least 46,044 rows (2021-10) and up to
   about 150,000 (2025-06), five seasons by the end. The base itself starts with 3,927 rows (December 2019) and has about 21,500 by
   2020-21; its early forecasts are weaker, which makes early residuals noisier, not leakier. No learning curve needed for the claim.
3. **Was the test valid?** Real tips (the heads' report columns use `tip_source="real"` through the production path, reproduced to 0.0);
   played rows only (`n_prior >= 5`, DNP excluded); planted future on synthetic and real data with non-inert controls; missingness
   audited beyond its as-of cause (G2, restated with the adversary check below); integer support for every arm and every stat; power
   stated for the effect sizes actually expected; the shuffle guard sized from a 50-draw permutation null (BEST_PRACTICES 2026-10-12).
4. **Lineage:** next section.
5. **What does it score against?** CRPS of the whole distribution against production's own heads. A pass says nothing about beating a
   market line (T477: our CRPS gains have not reached the line); it is not a betting rule and carries no decision score.

## Lineage (what this essentially is, and what was carried or dropped)
* **RECENT_ROLE / WORKLOAD / PI_RECENT_ROLE / RECENT_ROLE_FULL (T451-T473):** the nearest relatives, recent-minutes columns added to the
  same heads. FULL passed its primary on 2023-24 and on 2025 (-0.0116) but failed the frozen fixed-threshold shuffle guard (T473), which
  BEST_PRACTICES traced to a guard not sized from a null. Carried: past-only placebo, integer support, game clustering, Stage 2 touch
  mechanics; changed: the shuffle guard is now the null line. Overlap measured: FULL absorbs 15-26% of the error persistence.
* **Production CUSUM role change (`since_flag`, `flag_dir`) and recency windows:** production already reacts to role shifts in minutes;
  EF reacts to shifts in the model's errors whatever their cause. Kept as A0's features, not removed.
* **`opp_adjusted_ridge` (the NULL-pattern leak, experiments 1-2):** a column whose missingness tracked tonight's minutes. Carried: the
  missingness audit, now conditional on the as-of cause.
* **HISTORY_WINDOW / HISTORY_WINDOW_2 / HISTORY_WINDOW_3 (T315, T325, T420-T423):** the 2019-21 report era identity and the 2019-start
  training window. Carried: the report-free base (the era cannot enter the column) and the A0P arm (EF judged against production's own
  window as well); dropped: report columns from the base.
* **Stacks, routers and blends (WINPROB stack T178, ENSEMBLE T440-T444, ROUTER_G2 T464-T466):** combining models' outputs either
  tied or failed pre-registered guards (calibration, slices, era probes).
  EF differs in feeding a model's realised past errors back as a feature, but the risk is the same (capacity, not information), which is
  why the null and placebo guards carry the pass.
* **The earlier ERROR_FEEDBACK drafts (059f4de, c0614d5):** used stored A0 residuals (2023-24 only, training coverage 0% in the first
  block) and a mixture readout; dropped for the report-free base and the production readout (above).

## Adversary-style check of the rewritten missingness audit (restated)
The first audit (unconditional: share of rows with `ef_n < 3` may differ by at most 0.02 across realised-minutes buckets) failed at
0.029-0.034. Claim: the failure is fully explained by an as-of cause, history length. Evidence (pts, the same rows; numbers from the
2020-21 base run of 15:42 CDT, re-measured below for the 2019-20 base):
* Mechanism: a base row needs `n_prior >= 5`; with the player's earlier games inside the base window, `ef_n < 3` means `n_prior < 8`.
  Cold-start players play fewer minutes, so ANY cold-start indicator shows a minutes gradient.
* Reference: the pure as-of indicator `n_prior < 8` has an unconditional gap of 0.028 / 0.031 / 0.033 (2022 / 2023 / 2024) against
  EF's 0.029 / 0.031 / 0.034: excess 0.001 / 0.000 / 0.001.
* Stratified: within `n_prior` 10-29 the missing share is 0.000 in every bucket; within `n_prior >= 30` it is 0.000-0.002; within
  `n_prior` 5-9 (n 373-456 per season, buckets of 55-134 rows) it is 0.55-0.75 with no monotone pattern in minutes.
* What would have shown a leak: a gap that survives inside `n_prior` strata or exceeds the as-of reference (the planted-leak unit test,
  "missing whenever tonight's minutes < 5", fails the rewritten audit; the as-of pattern passes it).
* Re-measured on the 2019-20 base (G2 below): pts, reb, ast and fg3m give identical figures (the column depends on base rows, which are the same player-games for every stat). Unconditional
  gap across realised-minutes buckets 0.0401 / 0.0276 / 0.0313 / 0.0330 (2021 / 2022 / 2023 / 2024) against the pure as-of indicator
  `n_prior < 8` 0.0396 / 0.0276 / 0.0313 / 0.0330: excess **0.0005 / 0.0000 / 0.0000 / 0.0000**; among `n_prior >= 10` rows the gap
  is **0.0005 / 0 / 0 / 0**. 2021-22 is larger in level (more cold-start players after the 2020 compression) but entirely explained by the
  same as-of cause. (The 2020-21-base figures quoted above, 0.029-0.034, are superseded for the select seasons by this line.)

## Data-capture audit (BEST_PRACTICES "Audit data capture before failing a gate")
* Used: box scores 2019-20 to 2021-22 (`data/history/nba_history.duckdb`, read-only) and 2022-23 to 2024-25 (`nba.duckdb`, read-only),
  every query `season <= 2024`; players_static from both; real tip times from both schedule stores; the official report only inside the
  production heads (A0, EF, PL, SH all read it identically).
* Exists, not used: 2018-19 (not in the history store: box scores and reports are not loaded for it; the report PDF archive reaches
  only part of 2018-19, DECISIONS 2026-10-12; a 2018-19 box-score backfill would add base rows to the first months of 2019-20 but no
  select row, since coverage already passes);
  preseason box scores (not production rows); the 2021-22 report cadence fix (snapshot-time matching, about a day, unnecessary for a
  report-free column).
* Era handling: bubble games (2019-20) and 2020-21 (limited fans, compressed schedule) are base and training rows only, never
  evaluation rows; 2021-22 (hourly report) is an evaluation season with its own slice. No row is imputed or invented.
* The report season's power is set by one season of rows; more training rows do not rescue it (below).

## Minimum-data gate (non-negotiable 8), revised design
Commands (each under `data/ops/heavy.lock`, read-only on both databases, `season <= 2024`). First version of this gate (base from 2020-21,
select 2022-2024) passed 6 of 6 on Mon Oct 12 15:46 CDT; it is superseded and kept in git history with its null aside in
`data/ops/error_feedback_cache_v1_base2020/`. For the revised design: `... base` (355 s, built 16:23-16:29 CDT, 52 month blocks per stat),
then the items below. `require_gate()` now also refuses a gate file whose select seasons or base hashes differ from the rule, so the
stale 15:44 gate file cannot stand in.
* **Data.** Played rows with `n_prior >= 5` (counted across seasons), pts: 2019 21,522; 2020 24,522; 2021 27,442; 2022 27,322;
  2023 27,708; 2024 27,721. Base: 152,310 rows per stat (2019-2024: 17,595 / 24,522 / 27,442 / 27,322 / 27,708 / 27,721), 52 month
  blocks per stat, first block December 2019 (3,927 training rows, floor 1,500); files `data/ops/error_feedback_cache/base_<stat>.npz`,
  sha256 prefixes pts b1e62d8e, reb 02fefb52, ast aba40ccd, fg3m 2e96fc11 (full hashes in `base_meta.json`).
* **G1 training coverage, first select block (requirement >= 0.50), MEASURED Mon Oct 12 19:41-19:43 CDT:** 2021-10-01: 40,338 of 46,044
  training rows carry `ef_n >= 3` = **0.876 PASS**; the minimum over all 36 select blocks is this first one (2021-11 0.880, 2022-01 0.896;
  last block 2025-06 0.957). Evaluation rows with `ef_n >= 3`: 98.9% / 99.2% / 99.0% / 99.0% (2021 / 2022 / 2023 / 2024). 2021-22
  has 27,442 rows in 1,323 games (a full season, the same size as the others, so the hourly-report slice is not underpowered by size).
* **G2 missingness, MEASURED (requirement: excess over the as-of cause <= 0.01, and gap <= 0.02 among `n_prior >= 10`, every stat, every
  select season):** excess 0.0005 / 0.0000 / 0.0000 / 0.0000 (2021-2024), gap among `n_prior >= 10` 0.0005 / 0 / 0 / 0, identical
  for the four stats. **PASS** (detail in the adversary check above). Disclosure kept from the first version: the unconditional audit
  (gap <= 0.02) failed at 0.034 and was rewritten to test beyond the as-of cause before any arm was fitted; the rewritten audit is the
  one frozen.
* **G3 planted future, real frame, MEASURED:** every base residual dated on or after 2023-01-15 rewritten: the columns of all 87,064 rows
  dated on or before the cut are unchanged; control moves 99.9% of the following 7 days. Repeated at the 2021-22 cut 2021-12-15
  (54,915 rows on or before the cut): inert, control moves 99.1%. **PASS.**
* **G4 harness reproduction (production window, pts 2023-2024, stored production forecasts, max abs diff <= 1e-6):** re-run by `gate`
  at 20:34 CDT on the revised base: 0.0 on all 55,320 rows. **PASS.**
* **G5 permutation null (K = 50, pts, 2021-2024, SH_k - A0, real labels; finished 20:34 CDT, reports/prereg_error_feedback/null.json):**
  pooled mean +0.00010, sd 0.00031, line (mean - 2 sd) -0.00052; by season 2021 +0.00034 / 0.00083 / -0.00132, 2022 -0.00014 / 0.00056 /
  -0.00125, 2023 +0.00004 / 0.00056 / -0.00108, 2024 +0.00015 / 0.00044 / -0.00073. No noise arm gains beyond the line. **PASS.**
* **G6 half-width:** report (2024 stands in for 2025: 27,721 rows, 1,320 games): conservative 0.0041 <= 0.005 **PASS**; SH-based se_total
  0.00087, half-width 0.0015. Select pooled 2021-2024 (110,193 rows, 5,281 games, deff_game 1.05): half-width 0.0009, se_total 0.00056.
  Power to clear the -0.005 floor: about 1.00 at 2023's persistence slope (0.149) and **0.18 pooled / 0.28 on the report season at 2024's
  slope (0.102)**: if 2024's persistence is the truth, the test is expected to fail by the floor, not by noise.
* **Verdict: G1-G6 all PASS (gate_history_base.json, measured 20:34 CDT, gate_pass true). Ready for Devin to freeze.**

## Expected effect and power (honest)
Expected effect (outcome-free, from the disclosed persistence slopes: the A0 grid shifted by slope x `ef_res_l5`): **-0.0096 if
2023's persistence (0.149) is the truth, -0.0045 if 2024's (0.102) is.** Power with the conservative se plus fit noise (0.0021) and
the pass line min(-0.005, -half-width, null line) = -0.005:
* Report stage (2025): **0.98** at 2023's slope, **0.41** at 2024's. Select stage (pooled 2021-2024, 110,193 rows; se and power to be refreshed from the new null, the figures here are from the 2022-2024 null): about 1.0 at
  2023's slope, about **0.2-0.35** at 2024's: an effect of -0.0045 sits above the floor, so more rows make a pass less likely, not
  more. Both stages: about 0.98 or about 0.1-0.2 depending on which season's persistence is the truth.
* Why the realised effect could be smaller: EF learns from base residuals, and part of the base's error is report-driven (a teammate
  out for a stretch) that A0's heads already explain; the GBM sees five summaries, not the linear shift the sizing assumes; FULL
  absorbed 15-26% of the persistence in 2023-24 (production does not carry FULL, so that part is still available here). Why it could
  be larger: the 3- and 10-game windows and the PIT carry information the 5-game mean does not.
* **Prior, stated now (mine): probability of a production proposal (both stages pass) about 0.3.**
  Restated for the revised design (19:42 CDT, before any arm): unchanged at **0.3** (Stage 1 about 0.45, Stage 2 given Stage 1 about 0.65).
  The base from 2019-20 gives early residuals a little more history and adds one select season (2021-22, hourly report, its own slice
  at +0.01), which adds variance to Stage 1 but does not change the report stage's power (the 2025 sizing still uses 2024).
* The 2023-24 slopes were seen (disclosed above): Stage 1 is partly a look-informed filter; Stage 2 (2025) is the clean test.

## Stage 2 procedure (the one 2025 touch)
* Preconditions in code (`research/eval/error_feedback_rule.py`): the doc hashes to `--sha` and a commit `prereg(ERROR_FEEDBACK): <sha>`
  carries it; a committed, unmodified HOLDOUT_ACCESS_LOG row names ERROR_FEEDBACK, the sha8, PRE-REGISTERED and `DEVIN CONFIRMED (...
  "<his words>")`; the Stage 1 results exist under the same sha and passed; the stored base files hash to `base_meta.json`.
* The touch: the cap is raised to 2025 only inside `run` (a token only `run` creates; `season > 2025` is refused always); the marker
  `reports/prereg_error_feedback/stage2/touch_started.json` (sha, HEAD, code diff hash, input hashes) is the first write after the load;
  the 2025 base is regenerated (4 stats), then A0/EF (4 stats), PL, SH_0..49 and A0P (pts) are fit on the 2025 month blocks, each
  block on all rows dated before it; scored once; `results.json` written; no reruns. A run-time gate failure after the marker writes
  `gate_failed.json` and consumes the touch (remedy: a new id). A crash may be resumed only with `--resume` and an identical sha, HEAD,
  code diff and input hashes; checkpoints are per arm.
* Rehearsed end to end with 2024 standing in for 2025 (`rehearse`): cap never raised, every frame asserted `season <= 2024`, scores
  BLIND (labels permuted within game) so no arm's skill was seen; the regenerated 2024 base must equal the stored one exactly and the
  touch path's A0 on 2024 must equal the null run's A0 exactly. **Rehearsal Mon Oct 12 15:46-15:52 CDT 2026 (`rehearse`, 6 min,
  K = 3 draws): regenerated 2024 base equals the stored base for all four stats (max abs difference 0.0); the touch path's A0 on 2024
  equals the null run's A0 (0.0); every frame capped at 2024; arms A0/EF (4 stats), PL, SH_0..2 and A0P fitted and scored blind.
  `reports/prereg_error_feedback/rehearsal/results.json` (blind numbers, no information about any arm).**

## If both stages pass (proposal only; Devin decides)
Production would refit the report-free base daily beside the production heads (four more head pairs, same cost class), keep each
player's base residuals from the live log, and serve EF on the 2019-start window. A0P's numbers decide whether the proposal also asks
for the 2019 window itself (HISTORY_WINDOW_3 says it does not help on its own). A pass is a candidate in the registry until Devin
promotes it; the live 2026-27 log scores it forward beside production.

## Kill criteria
* A gate, plant, reproduction or identity check fails after the freeze: stop, fit nothing further, report; a fix is a new id.
* Any EF or PL arm scored before the freeze commit: rule void.
* Stage 1 fails: closed; no other windows, lags, bases or seasons. Stage 2 fails: closed; the 2025 touch is spent for this model.
* One pre-registered follow-up at most (candidate named now: the forward 2026-27 SHIFT design of the earlier draft, `A0 grid + beta x
  ef_res_l5` with beta refit walk-forward, scored on the live log), and only if Devin asks.

## Unstated details chosen before scoring
Median = the 100th of 199 integer quantiles; mid-PIT on the integer grid; history crosses seasons and teams; playoff and play-in rows
count (they are production rows); windows over the latest 10 base rows; `ef_n` capped at 10; the PL shift is 10 rows; the null
permutes residual and PIT together within season; coverage uses the randomised integer PIT (seed 0); the slice table uses as-of
`starter10` and `n_prior`, realised minutes for the minutes slice; BH with m = 3 for reb/ast/fg3m within each stage; deff for sizing =
max(1, game, date); a block is skipped below 1,500 training rows (October-November 2019 in the base; none in the select seasons or in 2025); bootstrap percentile, 2,000 draws, seed 0.

## Commands (maintainer, in order; each takes `data/ops/heavy.lock`)
1. Freeze: main session commits this file as `prereg(ERROR_FEEDBACK): <sha256>`.
2. Stage 1: `.venv/bin/python -W ignore -m research.eval.error_feedback_eval select --sha <sha256>` (about 10 min; writes
   `reports/prereg_error_feedback/select/results.json`; refuses a second run; refuses unless the gate passed and the null files
   still hash to `null.json`).
3. If Stage 1 passes and Devin says so: commit the holdout row, then
   `.venv/bin/python -W ignore -m research.eval.error_feedback_eval run --sha <sha256>` (about 25 min).

=== RESULTS BELOW ===

(not run)
