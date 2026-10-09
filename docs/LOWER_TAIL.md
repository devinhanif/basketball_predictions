# Lower-tail calibration of production context_residual props: pre-registration

Status: PRE-REGISTERED 2026-10-09 after the descriptive diagnosis below and BEFORE any candidate
number was computed. Everything above the line `=== RESULTS BELOW ===` is frozen; its sha256 is
recorded in the results section and in the ledger rows.

## Origin and honest data-exposure statement

Source: `docs/PTS_TAIL.md` side observation: production pts coverage P(y <= q0.10) = 0.18 against
0.10 nominal in 2023 and 2024. Goal: find out how much of that is real, and fix it if possible, for
all four stats (pts, reb, ast, fg3m), conditional on playing (rows are played player-games with
>= 5 prior games, as in production).

What has been looked at before this document was frozen (all descriptive, seasons <= 2024 only,
season 2025 never loaded):
* Production OOF diagnostics for 2023 AND 2024, all four stats (`nba.eval.lower_tail_eval diagnose`,
  one walk-forward pass, month blocks, same protocol as the production eval). Both seasons are
  therefore SEEN for production. The earlier `reports/pts_tail` production diagnostics were also seen.
* Candidate arms: NOT computed. Only unit tests on synthetic data exist for the candidate code (written
  after this document is frozen).

### Diagnosis (production; n = 27,619 (2023) / 27,583 (2024) rows per stat; ~1,318 games per season)

Coverage is reported two ways. "naive" = P(y <= q_tau) with the continuous quantile (what the
0.18 figure was). "PIT" = continuity-corrected randomized PIT on the 199-grid (integer outcome y occupies
[y-0.5, y+0.5], PIT uniform inside; seed 0), P(PIT <= tau). Only the PIT is a valid calibration
measure on integer outcomes. Nominal 0.05 / 0.10 / 0.20.

| stat | season | naive q0.05/.10/.20 | PIT coverage 0.05/.10/.20 (all rows) | PIT, not-short rows | PIT, short rows |
|---|---|---|---|---|---|
| pts | 2023 | .152/.182/.240 | .076/.132/.222 | .059/.102/.179 | .216/.366/.580 |
| pts | 2024 | .150/.182/.245 | .079/.135/.225 | .061/.107/.182 | .229/.391/.600 |
| reb | 2023 | .139/.168/.233 | .060/.113/.211 | .048/.090/.174 | .171/.299/.516 |
| reb | 2024 | .137/.169/.238 | .064/.116/.215 | .050/.094/.180 | .173/.312/.516 |
| ast | 2023 | .273/.287/.318 | .066/.118/.214 | .061/.106/.191 | .127/.224/.395 |
| ast | 2024 | .261/.278/.315 | .070/.123/.223 | .063/.112/.203 | .125/.229/.405 |
| fg3m | 2023 | .443/.447/.462 | .060/.112/.214 | .058/.107/.200 | .094/.177/.328 |
| fg3m | 2024 | .425/.430/.448 | .063/.117/.214 | .059/.109/.204 | .092/.168/.322 |

"short" = realised minutes < 0.5 x projected minutes (as-of 10-game-decay mean `min10`); 10.6% / 10.2%
of rows in 2023 / 2024. Other strata (pts PIT q0.10, 2023): realised min < 10: 0.297; 10-20: 0.141;
> 20: 0.084; blowout (|final margin| >= 20): 0.135 vs no-blowout about 0.13; actual starters 0.113,
bench 0.146. Foul-outs are not derivable (no personal-fouls column in `player_game_stats`); early exits
are covered by the short-game stratum. Share of the PIT <= 0.10 mass carried by short games: pts
0.31 / 0.29, reb 0.29 / 0.25, ast 0.20 / 0.18, fg3m 0.16 / 0.15 (short games are 10% of rows).

Findings that shape this pre-registration:
1. Most of the "0.18" is the integer lattice, not miscalibration. After the continuity-corrected PIT
   the q0.10 coverage is 0.132 / 0.135 (pts), 0.113 / 0.116 (reb), 0.118 / 0.123 (ast), 0.112 / 0.117
   (fg3m). Only pts q0.10 (and pts q0.20 = 0.222 / 0.225, and ast q0.20 2024 = 0.223) are outside the
   +/- 0.02 guard band.
2. The remaining excess is a short-minutes effect: excluding short games the PIT coverage is nominal
   within 0.01 for pts (0.102 / 0.107) and within about 0.012 for the counts. Short games (10% of rows)
   have PIT <= 0.10 in 0.17-0.39 of cases. The hypothesis (a unimodal residual scale cannot represent a
   mixture of normal-minutes and short-minutes games) is supported descriptively.
3. Blowout and starter/bench differences are secondary to the short-minutes effect (blowouts matter
   mostly through short minutes).

## Hypothesis

H1: a pre-tip mixture that gives short-minutes games their own component, or a lower-side conformal
width that adapts to the row, lowers the integer-support CRPS (the lower tail dominates the gain) and brings
the lower-tail PIT coverage at q0.10 / q0.20 within +/- 0.02 of nominal for the stats that miss it.
Null: no change. The mean (hence mean bias) is not an objective of any candidate; it is reported.

## Fixed candidates (exactly 3; no tuning; constants are final)

All keep production's mean head, scale head and calibration window (newest 15% of training rows,
on a date boundary). `c = m + mu` (centre), `s` the scale head output, `z` the calibration standardized
residuals. 199-quantile grid `tau = (i - 0.5) / 199`. All candidates change only the predictive
distribution; the stored `mean` is untouched. Short game `S = 1[minutes < 0.5 * min10]`.

* **mixture** (a). Pre-tip short-minutes probability `pi(x) = P(S = 1 | plays, as-of features)`:
  one LightGBM binary classifier (production hyperparameters `_params(cfg, "binary")`, features =
  `COMMON_FEATURES`, which include the |Elo margin| blowout-risk proxy, injury-return `since_flag`/`flag_dir`/
  `gap_days`, `b2b`, rest, starter history, projected minutes, report/teammate-out features) fitted on
  all training rows before the block; `pi` clipped to [0.005, 0.5]. Components: normal-minutes
  `c + s * z_norm_q(tau)`, where `z_norm` are the calibration `z` of rows with `S = 0` (>= 500 such rows,
  else fall back to production); short-minutes `m_row * w_q(tau)`, where `m_row` is the row's recency mean of
  the stat (`m10_<stat>`, floored at 0.5) and `w_q` the empirical 199-quantiles of `y / max(m10_<stat>, 0.5)`
  over the training rows with `S = 1` (>= 100 such rows, else `pi = 0`). Predictive distribution
  `(1 - pi) * normal + pi * short`; its 199 quantiles are read off the pooled 398 point masses (weights
  `(1 - pi)/199`, `pi/199`; midpoint cumulative-weight interpolation). Clipped at 0, monotone.
* **mondrian_centre** (b). Asymmetric lower conformal widths: for `tau < 0.5` the standardized residual
  quantile comes from the calibration rows in the same predicted-centre tercile (edges = calibration
  terciles of `c`); `tau >= 0.5` is production's pooled quantile; bins with < 200 calibration rows fall
  back to pooled.
* **mondrian_minutes** (c). Quantile-specific conformal per projected-minutes bucket: for `tau < 0.5`
  the `z` quantile comes from the calibration rows in the same `min10` bucket (< 15, 15-25, > 25 minutes);
  `tau >= 0.5` pooled; bins with < 200 calibration rows fall back to pooled.

Implemented in `nba/props/lower_tail.py` behind `ContextResidualConfig.lower_tail` (default `"off"` =
byte-identical output, unchanged forward-cache fingerprint).

## Design

* Rows: production walk-forward (month blocks, train strictly before the block, 2022 warm-up), the four
  stats, played rows with >= 5 prior games; one fit pass for the production heads, one for the `pi` model
  and short ratios; candidates are pure functions of the stored components.
* **Selection season 2023, report season 2024.** A (candidate, stat) is selected on 2023 only if it passes
  all required checks there (lowest dCRPS among passers per stat); the 2024 result is the confirmation. All
  arms are reported on 2024 for transparency; only a selected candidate can be called a pass.
* **Support:** every arm's 199-grid is mapped to the integers with `ceil(q - 0.5)` (production's
  integer-support convention, BEST_PRACTICES) before any CRPS or threshold event is computed, for all four
  stats and for the production arm too. Continuous-support CRPS is reported as a secondary.
* **Primary metric:** per-row CRPS on integer support (2 x mean pinball over the 199 grid), paired delta
  (candidate - production), game-clustered bootstrap CI (2000 resamples, seed 0), per stat.
* **Required guard:** lower-tail PIT coverage (randomized, continuity-corrected, seed 0) of the candidate
  at q0.10 and q0.20 within +/- 0.02 of nominal (0.10, 0.20), on that season's rows. Reported also at q0.05
  and q0.80/q0.90/q0.95 (upper tail must not leave +/- 0.02 either; required).
* **Secondary:** threshold log loss at the low thresholds, as "under" bets, `P(stat >= N)` = share of the
  integer-mapped grid with value >= N, clipped to [0.5/199, 1 - 0.5/199]: pts N in {10, 15}, reb {4, 6},
  ast {2, 4}, fg3m {1, 2}; mean over the two thresholds, paired delta + clustered CI. Mean bias, naive and
  PIT coverage, and strata (short / not short, projected-minutes bucket, blowout) reported.
* **Multiplicity:** Benjamini-Hochberg over the 12 (candidate x stat) primary p-values within a season.
* **Pass (per candidate x stat):** dCRPS point <= -0.005 (floor, absolute integer-support CRPS units),
  clustered CI upper bound < 0, BH-adjusted p < 0.05, and the coverage guard above. Secondary metrics
  cannot rescue a failure.
* No promotion. A candidate confirmed on 2024 would only get a drafted (not appended) holdout row and a
  recommendation to shadow-log it live.
* Out of scope: upper tails (guard only), DNP/zero-minute risk (rows are conditional on playing), the
  stored means, season 2025.

=== RESULTS BELOW ===

## Results (run 2026-10-09; frozen-section sha256 prefix `673c14ec`)

Rows per stat: 2023 n=27,619, 2024 n=27,583 (about 1,318 / 1,315 games); CPU; one production
walk-forward pass (about 90 s for 4 stats) plus one pi/short-ratio pass (about 11 s); candidate screen
about 9 s. Full tables: `reports/lower_tail.md`, `reports/lower_tail/candidates.json`. Candidate minus
production; dCRPS on integer support (lower is better), game-clustered 95% CI, BH over 12 tests/season.

| stat | season | arm | dCRPS [CI] | p (BH) | dTLL low thr [CI] | PIT q.10 / q.20 | guard | pass |
|---|---|---|---|---|---|---|---|---|
| pts | 2023 | production (CRPS 3.1528) | | | | .132 / .222 | FAIL | |
| pts | 2023 | **mixture** | -0.0126 [-0.0172,-0.0080] | 0.001 | -0.0011 [-0.0017,-0.0005] | .115 / .216 | ok | **PASS** |
| pts | 2023 | mondrian_centre | -0.0034 [-0.0045,-0.0021] | 0.001 | -0.0019 [-0.0024,-0.0013] | .119 / .217 | ok | no (floor) |
| pts | 2023 | mondrian_minutes | -0.0032 [-0.0044,-0.0019] | 0.001 | -0.0016 [-0.0021,-0.0011] | .120 / .216 | FAIL | no |
| pts | 2024 | production (CRPS 3.1884) | | | | .135 / .225 | FAIL | |
| pts | 2024 | **mixture** | -0.0109 [-0.0155,-0.0061] | 0.001 | -0.0004 [-0.0010,+0.0001] | .118 / .216 | ok | **PASS** |
| pts | 2024 | mondrian_centre | -0.0031 [-0.0041,-0.0021] | 0.001 | -0.0015 [-0.0019,-0.0010] | .125 / .221 | FAIL | no |
| pts | 2024 | mondrian_minutes | -0.0027 [-0.0037,-0.0018] | 0.001 | -0.0013 [-0.0017,-0.0009] | .126 / .220 | FAIL | no |
| reb | 2023 / 2024 | mixture | -0.0046 [-0.0066,-0.0026] / -0.0029 [-0.0049,-0.0008] | .001 / .004 | -0.0007 / -0.0005 (CI touches 0) | .105/.208, .104/.206 | ok | no (floor -0.005) |
| ast | 2023 / 2024 | mixture | -0.0039 [-0.0050,-0.0028] / -0.0016 [-0.0026,-0.0006] | .001 / .002 | -0.0029 / -0.0016 (CI < 0) | .109/.204, .112/.211 | ok | no (floor) |
| fg3m | 2023 / 2024 | mixture | -0.0028 [-0.0036,-0.0021] / -0.0021 [-0.0028,-0.0015] | .001 / .001 | -0.0038 / -0.0031 (CI < 0) | .107/.208, .110/.206 | ok | no (floor) |

reb/ast/fg3m mondrian_centre and mondrian_minutes: dCRPS between -0.0015 and +0.0003 (all above the
floor; fg3m mondrian_centre is flat, BH p 0.24 / 0.31); every one fails only the -0.005 floor.

**Verdict.**
* **pts: `mixture` is selected on 2023 and confirmed on 2024** under every pre-registered check (dCRPS point
  -0.0126 / -0.0109, i.e. 0.40% / 0.34% of CRPS, CI excludes 0, BH p 0.001; coverage at q0.10 / q0.20 moves
  from 0.132 / 0.222 (2023) and 0.135 / 0.225 (2024), outside the guard band, to 0.115 / 0.216 and 0.118 /
  0.216, inside it; upper-tail coverage unchanged). The improvement is concentrated where the hypothesis
  says: on short games dCRPS is -0.22 / -0.24 (10% of rows), on normal-minutes rows it is +0.012 / +0.015
  (slightly worse). It is small in absolute terms and is not a mean-accuracy gain (the stored mean is
  untouched).
* **reb, ast, fg3m: not selected.** The mixture lowers CRPS significantly in both seasons (CI < 0) but by
  0.15-0.35%, below the -0.005 floor, and these stats already pass the coverage guard at baseline (only ast
  2024 q0.20 = 0.223 narrowly missed). Reported as a small real effect that does not clear the pre-registered
  bar, not as a pass.
* mondrian_centre / mondrian_minutes: significant but tiny CRPS gains (-0.003 pts, <= -0.0015 elsewhere);
  they do not fix pts coverage (guard FAIL in 2024). A unimodal bucketed width cannot represent the short-minutes mode.
* Low-threshold log loss ("under" bets) improves for every mixture cell, but is secondary and its CI touches 0
  for pts 2024 and reb.
* Pre-tip short-minutes model: AUC 0.855 on the 55k test rows, mean pi 0.091 vs observed 0.104
  (slightly under-forecast; top pi bin 0.31 predicted vs 0.35 observed), so the mixture under-weights the short
  component a little; a calibrated pi is a possible refinement that needs its own pre-registration.
* Fidelity: the config-flag model path (`lower_tail="mixture"`, one real block) reproduces the eval-stage
  quantiles exactly (max abs diff 0.0).
* No promotion; season 2025 never loaded. Production is unchanged with the flag off (tests: flag-off
  byte-identical; fingerprint drops the new keys while off).
* Draft holdout row (NOT appended; for the maintainer): "LOWER_TAIL pts mixture vs production, integer-support
  CRPS and PIT coverage at q0.10/q0.20, season 2025, pre-registered pass rule above, one-shot". Recommended
  next step is to shadow-log the mixture live for pts alongside production, not to promote: wiring needs the
  realised-minutes column on the training frame in `nba.props.forward` (not done; flag-on raises a clear error
  without it).

Reproduce: `uv run python -m nba.eval.lower_tail_eval collect && uv run python -m nba.eval.lower_tail_eval
diagnose && uv run python -m nba.eval.lower_tail_eval collect_pi && uv run python -m nba.eval.lower_tail_eval
candidates && uv run python -m nba.eval.lower_tail_eval report` (about 2 minutes CPU; read-only DuckDB).
