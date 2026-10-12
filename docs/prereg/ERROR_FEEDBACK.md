# ERROR_FEEDBACK: a player's own recent errors against a report-free base, as a props input (pre-registration)

Status: **FREEZE CANDIDATE, written by the modeler Mon Oct 12 15:48 CDT 2026 (clock read by `date`).** It becomes the rule only when the main
session commits `prereg(ERROR_FEEDBACK): <sha256>` of everything above the line `=== RESULTS BELOW ===`
(`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ERROR_FEEDBACK.md | shasum -a 256`); the harness refuses any arm-bearing
command without that commit. Approved for freezing by Devin, 2026-10-12: "props yes, lets see this" (design: select on history, report
once on 2025, a pass is a production proposal for 2026-27; DECISIONS 2026-10-12 14:17 CDT). Idea: Devin, "recent errors would work"
(DECISIONS 2026-10-12 14:05 CDT). Earlier drafts (2023/24 design, stopped at their gate) are in git history (059f4de, c0614d5); nothing
from them is carried over except what is restated here. Season labels are `games.season` (2022 = 2022-23, 2025 = 2025-26, the frozen
holdout).

## The rule in six lines
1. **Claim arm EF** = production `context_residual` heads (production feature list, `configs/context_residual.yaml`, seed 0, no tuning)
   plus five as-of columns from the player's own recent errors against a **report-free walk-forward base**: `ef_res_l3`, `ef_res_l5`,
   `ef_res_l10`, `ef_pit_l5`, `ef_n` (below). **Comparator A0** = the same heads without the columns. Same rows, same training window
   (every row since 2019-20 dated before the month block), same readout (production quantiles mapped to integer support).
2. **Metric:** integer-support CRPS, **pts primary**; EF minus A0, paired, 95% percentile bootstrap clustered by `game_id` (2,000 draws,
   seed 0); date-clustered CI reported beside it. **Pass: point <= -0.005 AND CI upper < 0.**
3. **Stage 1 (select) = seasons 2022-2024 pooled**, run once after the freeze (`select --sha`). **Stage 2 (report) = season 2025, once**,
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
  (`has_report`, `n_out_rot`, `vac_min`, `vac_<stat>`), refit walk-forward in calendar-month blocks from the first block of 2020-21
  (December 2020); each block is fit on every played row (`n_prior >= 5`) dated before the block's first day, history included
  (2019-20 onward; no window cap, no weights). The base forecast of a row is the production quantile construction
  (`c + s * empirical z quantile` on 199 equal-mass taus) mapped to integer support (`ceil(q - 0.5)`). Per row: integer median
  (the 100th of 199 quantiles), residual `y - median`, mid-PIT `(F(y - 1) + F(y)) / 2` on the integer grid.
* **Columns of a row of player i dated D:** over i's base rows dated **strictly before D** (any team, crossing seasons; latest first,
  at most 10): `ef_res_l3/l5/l10` = mean residual over the latest 3/5/10 (or as many as exist), `ef_pit_l5` = mean mid-PIT over the
  latest 5, `ef_n` = how many (<= 10). NaN and `ef_n = 0` when none. Rows on the same date never feed each other; a value outside a
  row's window cannot move it (explicit window sums, no running cumulative sum).
* **Placebo PL:** the same five statistics computed after skipping the latest 10 earlier rows (rows 11-20 back), names `pl_*`.
* **Shuffled SH_k (null and guard):** base residuals and mid-PITs permuted together across rows within each season (seed 1000 + k),
  then the same five statistics, names `sh_*`. Timing and player of every base row are kept; the link to the player is broken.
* The base is stored for 2020-2024 only (`save_base` refuses any row with season > 2024, tested); the 2025 base is regenerated inside
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
   each season in Stage 1): EF - A0 <= +0.01.
6. Missingness: the gap, across realised-minutes buckets (<5, 5-10, 10-20, >=20; buckets with n >= 300), in the share of rows with
   `ef_n < 3` exceeds the same gap of the as-of indicator `n_prior < 8` by at most 0.01, AND among rows with `n_prior >= 10` it is at
   most 0.02 (`missingness` in the harness).
7. Stage 1 only: A0's centre and scale reproduce the pre-freeze null run's A0 to 1e-9 (same code, same data).
Stage 1 passes -> Stage 2 may run (Devin's words in the holdout row). Stage 2 passes -> production proposal. Any other outcome: closed.

## Why these choices (what each protects against)
* **Why a report-free base.** Production's heads read injury-report columns; in 2021-22 the report was hourly and the latest usable
  snapshot about 2 h fresher than in 2022-24 (T315; classifier AUC 0.80). Residuals of a report-reading base would carry that era into
  the feature. The base reads no report, so the column is built the same way in 2020-21, 2022-24, 2025 and live.
* **Select 2022-2024, not 2021-2024 as first proposed.** The minimum-data gate requires that at least half the training rows of the
  first select block carry the column (`ef_n >= 3`); at 2021-10-01 the share is 0.498 (measured, outcome-free), at 2022-10-01 it is
  0.681. 2021-22 stays in training. Side effect: no evaluation row comes from the hourly-report season.
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
  (no base yet): its missingness is a function of calendar time, audited by realised minutes.
* No column reads tonight's box, lineup or report. Labels and realised minutes are used only as targets and slice strata.

## Forking paths and data exposure (read first)
* **Seen on the select seasons before this freeze:** the persistence slope of production's residuals, 0.149 [0.120, 0.176] (2023) and
  0.102 [0.071, 0.133] (2024), game-clustered, and the FULL-overlap slopes 0.126 / 0.075 (the 13:57 and 14:08 gates; a look at the
  comparator's errors on 2023-24, disclosed then). The 5-game window was chosen before that look; Devin proposed the idea.
* **Computed before this freeze (no EF, PL arm fitted):** the base on 2020-2024 (fit on outcomes; no base score computed or printed);
  the permutation null (A0 and 50 SH arms on 2022-2024: only SH - A0 deltas and their spread are summarised; A0's own score level is
  not reported); training and evaluation coverage, missingness by realised minutes, the real-data planted-future check, and the
  harness reproduction of stored production forecasts (forecasts only). The 2021 first-block coverage (0.498) was measured before
  select was set to 2022-2024.
* **2025 is clean for this model:** no ERROR_FEEDBACK row in docs/HOLDOUT_ACCESS_LOG.md; no EF model has been fitted or scored on 2025.
  Stage 2 is the clean test; Stage 1 is a candidate filter on seasons whose comparator errors were partly seen.

## Data-capture audit (BEST_PRACTICES "Audit data capture before failing a gate")
* Used: box scores 2019-20 to 2021-22 (`data/history/nba_history.duckdb`, read-only) and 2022-23 to 2024-25 (`nba.duckdb`, read-only),
  every query `season <= 2024`; players_static from both; real tip times from both schedule stores; the official report only inside the
  production heads (A0, EF, PL, SH all read it identically).
* Exists, not used: 2018-19 reports and box scores (not loaded; the base needs only one warm-up season, and 2019-20 provides it);
  preseason box scores (not production rows); the 2021-22 report cadence fix (snapshot-time matching, about a day, unnecessary for a
  report-free column).
* Era handling: bubble games (2019-20) are training rows only (no evaluation row is from 2019 or 2020); 2020-21 (limited fans,
  compressed schedule) and 2021-22 are training rows only. No row is imputed or invented.
* The report season's power is set by one season of rows; more training rows do not rescue it (below).

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 14:42-15:46 CDT 2026
Commands (each under `data/ops/heavy.lock`, read-only on both databases, `season <= 2024`):
`.venv/bin/python -W ignore -m research.eval.error_feedback_eval base` (287 s, 14:42-14:47 CDT), `... null --k 50` (3,079 s, 14:48-15:39 CDT),
`... gate` (25 s of work after a 2.4 min wait on a lock held by another job, 15:46 CDT); JSON `reports/prereg_error_feedback/base_meta.json`, `null.json`, `gate_history_base.json`.
* **Data.** Played rows with `n_prior >= 5` (counted across seasons), pts: 2019 21,522; 2020 24,522; 2021 27,442; 2022 27,322;
  2023 27,708; 2024 27,721. Base: 134,715 rows per stat (2020-2024), 44 month blocks per stat; files `data/ops/error_feedback_cache/
  base_<stat>.npz`, sha256 prefixes pts f07ee5bb, reb 6c222498, ast 661fb78a, fg3m c0fb7b85 (full hashes in `base_meta.json`).
* **G1 training coverage, first select block (requirement >= 0.50):** 2022-10-01: 50,061 of 73,486 training rows carry `ef_n >= 3`
  = **0.681 PASS**. (2021-10-01: 22,940 / 46,044 = 0.498, fail, hence select 2022-2024; last block 2025-06: 0.845.) Evaluation rows
  with `ef_n >= 3`: 99.2% / 99.0% / 99.0% (2022 / 2023 / 2024).
* **G2 missingness (requirement: excess over the as-of cause <= 0.01, and gap <= 0.02 among `n_prior >= 10`):** the share of rows
  with `ef_n < 3` by realised-minutes bucket (<5, 5-10, 10-20, >=20) differs by 0.029 / 0.031 / 0.034, the same as the pure as-of
  indicator `n_prior < 8` (0.028 / 0.031 / 0.033): excess **0.001 / 0.000 / 0.001**; among `n_prior >= 10` rows the gap is
  **0.000-0.002**. **PASS.** Disclosure: the first run of this audit (15:42 CDT) used the unconditional gap <= 0.02 and FAILED at
  0.034. The missing rows are cold-start rows (base rows need `n_prior >= 5`, so `ef_n < 3` is essentially `n_prior < 8`) and
  cold-start players play fewer minutes; within `n_prior` strata the pattern vanishes. The audit was rewritten to test for missingness
  beyond its as-of cause, before any arm was fitted and with no stat outcome involved; the rewritten audit is the one frozen here.
* **G3 planted future, real frame:** every base residual dated on or after 2023-01-15 rewritten (+25, PIT flipped): the columns of all
  87,064 rows dated on or before the cut are unchanged; control (residuals before the cut rewritten) moves 99.9% of the rows of the
  following 7 days. **PASS.** Disclosure: the first run's control looked at rows more than 30 days after the cut, whose 10-row windows
  lie entirely after it (0.7% moved); the control window was corrected to the following 7 days; the inert half passed both times.
* **G4 harness reproduction:** production window, production feature list, pts 2023-2024: the harness reproduces the stored
  production forecasts (`reports/lower_tail/components_pts.npz`) on all 55,320 rows, max abs difference **0.0. PASS.**
* **G5 permutation null (K = 50, pts, 2022-2024, SH_k - A0, real labels):** pooled mean -0.00056, sd 0.00035, line
  (mean - 2 sd) **-0.00126**; by season 2022 -0.00156 (sd 0.00065), 2023 -0.00004 (0.00054), 2024 -0.00009 (0.00054). The 2022 noise
  arms gain because the columns' missing pattern marks the pre-base era (2019-20 rows have no base), an era indicator in training; the
  null guard absorbs it, and the floor (-0.005) binds, not the null line. **PASS** (K = 50).
* **G6 report half-width (2024 stands in for 2025: 27,721 rows, 1,320 games):** conservative (per-row sd 0.330 and deff 1.09 of a
  heads refit that really moves forecasts, the stored FULL - A0 of 2024): **0.0041 <= 0.005 PASS**; from SH_0 - A0 (sd 0.125, deff
  1.01): 0.0015, a lower bound (noise columns barely move the forecasts). Select pooled 2022-2024 (82,751 rows, 3,958 games): 0.0010
  (SH-based), about 0.0023 (conservative).
* **Verdict: the gate PASSES (6 of 6).** The rule can pass its own gate on the data it will run on.

## Expected effect and power (honest)
Expected effect (outcome-free, from the disclosed persistence slopes: the A0 grid shifted by slope x `ef_res_l5`): **-0.0096 if
2023's persistence (0.149) is the truth, -0.0045 if 2024's (0.102) is.** Power with the conservative se plus fit noise (0.0021) and
the pass line min(-0.005, -half-width, null line) = -0.005:
* Report stage (2025): **0.98** at 2023's slope, **0.41** at 2024's. Select stage (pooled 2022-2024, se 0.0006-0.0012): about 1.0 at
  2023's slope, about **0.2-0.35** at 2024's: an effect of -0.0045 sits above the floor, so more rows make a pass less likely, not
  more. Both stages: about 0.98 or about 0.1-0.2 depending on which season's persistence is the truth.
* Why the realised effect could be smaller: EF learns from base residuals, and part of the base's error is report-driven (a teammate
  out for a stretch) that A0's heads already explain; the GBM sees five summaries, not the linear shift the sizing assumes; FULL
  absorbed 15-26% of the persistence in 2023-24 (production does not carry FULL, so that part is still available here). Why it could
  be larger: the 3- and 10-game windows and the PIT carry information the 5-game mean does not.
* **Prior, stated now (mine): probability of a production proposal (both stages pass) about 0.3.**
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
max(1, game, date); a block is skipped below 1,500 training rows (none is, in 2022-2025); bootstrap percentile, 2,000 draws, seed 0.

## Commands (maintainer, in order; each takes `data/ops/heavy.lock`)
1. Freeze: main session commits this file as `prereg(ERROR_FEEDBACK): <sha256>`.
2. Stage 1: `.venv/bin/python -W ignore -m research.eval.error_feedback_eval select --sha <sha256>` (about 10 min; writes
   `reports/prereg_error_feedback/select/results.json`; refuses a second run; refuses unless the gate passed and the null files
   still hash to `null.json`).
3. If Stage 1 passes and Devin says so: commit the holdout row, then
   `.venv/bin/python -W ignore -m research.eval.error_feedback_eval run --sha <sha256>` (about 25 min).

=== RESULTS BELOW ===

(not run)
