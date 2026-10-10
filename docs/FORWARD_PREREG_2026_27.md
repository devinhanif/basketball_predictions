# FORWARD_PREREG_2026_27 - pre-registered scoring of the live shadow arms

FREEZE RECORD (outside the frozen section):
- Frozen on: 2026-10-09 (before any 2026-27 forward row exists or has been scored)
- sha256 of the frozen section: `e323aa4817589147598109424718913b7b881d289213dc2399dc655ac2bed10a`
- Verify: `awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' docs/FORWARD_PREREG_2026_27.md | shasum -a 256`
- The frozen section is every line strictly between the two marker comments. Anything outside it
  (this record, the Amendments list at the bottom) is not part of the rule. Amendments may only be
  appended below the frozen section, must be dated, and may not change any rule retroactively
  (a changed rule is a NEW arm/version with new counters).

<!-- FROZEN-BEGIN -->
## 0. Scope and what this document is

Binds how four live comparisons are scored on the first genuinely out-of-sample season
(`season = 2026`, i.e. 2026-27; `nba.daily.season.FORWARD_SEASON`). Written before any 2026-27 game
exists in the database. Opening-week numbers cannot change the rules; they can only trigger the
pre-declared checkpoint logic.

| Arm (model_name) | Compared with | Stats | Source doc |
|---|---|---|---|
| `props_context_residual_t30` | `props_context_residual` (T-60 production) | pts, reb, ast, fg3m | docs/LINEUPS_KNOWN.md, docs/reviews/redteam_lineups_known_2026-10-09.md |
| `props_context_residual_int` | `props_context_residual` (same run, as-is quantiles) | reb, ast, fg3m decision; pts descriptive | docs/INTEGER_QUANTILES.md |
| `props_context_residual_lt` | `props_context_residual` (same run) | pts only (reb/ast/fg3m failed the -0.005 floor in the backtest and are not shadowed) | docs/LOWER_TAIL.md |
| `rung0_injury_elo` | `rung0_mov_elo` | win_prob_home | T091 / holdout row 2026-10-08 |

All arms are SHADOW. Nothing here promotes, registers or wires anything. The most any outcome can do is
"promote-candidate", which means: send to red-team review and a maintainer decision.

## 1. Common rules

1. **Population.** Regular-season games of `season = 2026` only (playoffs and preseason excluded,
   decided now). A paired unit is `(stat, game_id, player_id)` scored (not DNP) for BOTH arms. DNP /
   not-in-box rows are excluded identically from both sides and counted.
2. **Identical player-games.** Pairs are formed by key intersection; one-sided rows are never
   dropped silently (counted and reported per arm: `only_arm`, `only_comparator`, `dnp`).
3. **Comparator version pinning.** The production row used in a pair is the latest
   `forward_predictions` row for the key with `made_at` <= tip-off minus 60 minutes (not the latest
   overall, which `nba.daily.settle` would pick). For `_int` and `_lt` the comparator is the row from
   the SAME `run_id`. A pair with no eligible comparator is excluded and counted.
4. **Primary metric: CRPS on the correct support.** For pts, reb, ast, fg3m (all integer valued)
   the primary score is the integer-support CRPS, `sum_k (F(k) - 1[y <= k])^2` over integers
   k = 0..Kmax, with `F(k) = 1 - P(Y >= k+1)` from the stored `p_ge` thresholds under the stored
   convention `P(Y >= N) = P(continuous >= N - 0.5)`. Delta = arm minus comparator, negative = arm
   better. The continuous 19-quantile pinball CRPS that `nba.daily.settle` currently stores
   (`quantile_crps`, taus 0.05..0.95) is NOT the primary: it rewards an integer-grid forecast for
   free (T114-T118), truncates tails, and is blind to the lower-tail mixture. It is reported as a
   secondary "pinball19" delta and labelled as such. Win model: log loss (primary) and Brier.
5. **CI method.** Percentile bootstrap that resamples whole GAME DATES (`game_date`, one cluster per
   calendar date; same-night slates share news, injury-report timing and rest), paired by
   construction (the statistic is the mean of per-pair deltas, so the same resample indexes both
   arms). B = 10,000 resamples, `numpy.random.default_rng(2026)` re-seeded at every checkpoint, seed
   logged. Same estimator as `nba.daily.report.cluster_bootstrap_mean` but with date clusters
   (already the report's convention) and B = 10,000, and with a two-sided p-value (item 7).
   A CI that excludes 0 is evidence, not proof, and not a p-value.
6. **Practical floor.** Per-row mean integer-support CRPS delta <= -0.005 for props (win model: no
   floor, tripwire only, section 7). A statistically clear gain below the floor is "keep shadowing /
   report", never promote-candidate.
7. **Multiplicity.** Fixed family of m = 9 tests, defined now and not shrunk when a test is not yet
   eligible: T30 x {pts, reb, ast, fg3m} (4), INT x {reb, ast, fg3m} (3), LT x {pts} (1), WIN (1).
   (INT pts is descriptive, not in the family.) Two-sided bootstrap p-value
   `p = min(1, 2 * min(P*(boot >= 0), P*(boot <= 0)))` with a floor of 1/(B+1). At every efficacy
   look the m = 9 p-values are adjusted together by Benjamini-Hochberg; the adjusted p is compared
   to the per-look level alpha_look (section 4). Tests not yet eligible still enter the BH list
   (conservative) and cannot be claimed.
8. **Guards (all must hold for any promote-candidate).** (a) mean bias |pred - actual| <= 0.5 and
   |bias arm| - |bias comparator| <= 0.05 (the T30 arm: also the comparator-relative check);
   (b) 80% interval coverage within 0.80 +/- 0.03 once n >= 3,000 pairs (not enforced earlier,
   reported); (c) no pre-declared slice with n >= 300 pairs and CRPS delta > +0.01 (slices: starter vs
   bench by `starter10`, teammate-out report status, first 15 team games vs rest, home/away);
   (d) leak audit: `made_at < tipoff` for 100% of rows (enforced by `append_predictions`), and for
   T30 the snapshot `fetched_at < tip - 30 min` for 100% of rows; (e) for `_lt`: PIT mass below
   q0.10 and q0.20 within [0.08, 0.12] and [0.17, 0.23] (the backtest values were 0.118/0.216).
9. **Frozen arms.** From the first 2026-27 prediction, each shadow arm's code, features,
   hyperparameters, calibration windows and training procedure are frozen. Walk-forward month-block
   retraining on newly played rows is the existing production procedure and is allowed because it
   is training, not tuning. Any other change creates a new `model_name`/`version` with fresh
   counters and a new pre-registration.
10. **Only checkpoint snapshots count.** The daily `report.md` keeps printing running CIs; those are
    descriptive and are NOT decision inputs. A decision uses only the checkpoint snapshot (item 3 of
    section 5), written once.
11. **Dates counted.** "Dates" = distinct `game_date` values on which the arm-stat has at least one
    paired scored row (regular season, season 2026), counted in calendar order.

## 2. Arm T30: `props_context_residual_t30`

- **Hypothesis.** Confirmed starting lineups at T-30 improve the per-player distribution of pts,
  reb, ast (fg3m expected below the floor) over the T-60 model, because starter status drives minutes.
  Backtest (2024, proxy-starter, upward-biased): pts -0.0412, reb -0.0182, ast -0.0089, fg3m -0.0041.
  Red-team-adjusted planning range for the forward effect: pts -0.023..-0.041, reb -0.009..-0.018,
  ast conditional (planning value -0.0045, half the proxy estimate), fg3m fails the floor (-0.004).
- **Bundled information.** The arm differs from production by the `t30_*` lineup features, and, if the
  T-30 run reads a newer injury report than T-60, by that too. The contrast is "everything knowable
  at T-30 vs everything knowable at T-60", not a pure lineup effect. Stated, not corrected.
- **Primary metric.** Paired integer-support CRPS delta per stat (rule 4).
- **Comparison population.** Games with a logged T-30 decision: `forward_t30_decisions.outcome =
  'logged'` (a confirmed lineup captured strictly before tip - 30 min). Skipped games
  (`outcome = 'skipped'`) have no T-30 row and are NOT in the paired test. The skipped share is
  reported at every checkpoint: `skipped / (logged + skipped)` with a Wilson 95% CI, the reason
  breakdown, and (descriptive) the production CRPS on skipped vs logged games to expose selection
  (early-confirming games may be systematically different). The claim is conditional on logged
  games; the operational value is the conditional gain times the logged share.
- **Operational gate (promote-candidate only).** Wilson 95% lower bound of the logged share >= 0.70
  at the checkpoint, otherwise the statistical result is reported but T-30 is "keep shadowing"
  (a second prediction time that covers fewer than 70% of games is not a usable product).
  Maintainer-adjustable default, fixed here.
- **Direction check.** An estimate more negative than the backtest upper end (pts < -0.041,
  reb < -0.018, ast < -0.0089) is treated as a suspected leak/pipeline fault and triggers an audit
  before any use (CLAUDE.md: too-good results are leaks until audited).
- **Unit of analysis note.** Players in the same game share the lineup news, so game-level and
  date-level dependence are both real; the date cluster contains the game cluster.

### 2.1 T-30 announcement-timing measurement (descriptive; no decision weight, rule-fixed definitions)

From `nba.lineups.analysis` (read-only), reported at every checkpoint, never tuned:
1. **Lead minutes.** For each (game, team): minutes between the first fetch that read
   `lineup_status = 'Confirmed'` and tip-off (`announcement_lead.lead_minutes`; p10/p50/p90, mean, n),
   and the same from the feed's own timestamp (`source_lead_minutes`). Also the shares
   confirmed before tip and confirmed before T-30 (with Wilson 95% CIs on the team-game share).
   Caveat fixed now: our-fetch lead is a LOWER bound on the true announcement lead, limited by the
   poll interval; the poll interval must be reported next to it. Report the full distribution
   (deciles) as well as p10/p50/p90.
2. **Open game-time-decision share.** `open_decisions`: among (game, player) pairs listed
   questionable or doubtful on the latest official report usable at T-60, the share whose team lineup
   was still `Expected` (or had no snapshot) at the latest snapshot strictly before T-30
   (`open_lineup_unconfirmed + no_snapshot_before_t30`). Report `k/n` with the Wilson 95% interval
   (as `analysis.wilson`). Because candidates cluster within games, Wilson on players is
   anti-conservative: ALSO report the game-level share (games with any open candidate / games with a
   candidate, Wilson 95%) and a date-clustered bootstrap 95% CI for the player-level share. The
   exclusions (`n_games_no_report`) are reported.
3. **Announced-vs-box skew.** `starter_skew` counts on logged games (announced five vs box-score
   starters; announced-but-did-not-play; box starters not announced), reported at each checkpoint.
   The T-30 model is fit on box-score starters and served announced starters; this is the measured
   train/serve skew.
4. **Reading rule.** The open-decision share is the pre-registered explanation for any shortfall
   versus the backtest: the backtest assumed GTD outcomes resolved by the time of the lineup. No
   decision threshold depends on it, except that it is reported next to every T30 result and a
   Wilson lower bound >= 0.50 is flagged "T-30 gain expected to be materially below the backtest".

## 3. Arm INT: `props_context_residual_int`

- **Hypothesis (and honest limit).** For reb, ast, fg3m, replacing each quantile q by `ceil(q-0.5)`
  changes the scoring convention, not the information. Under the stored convention
  `P(Y >= N) = P(continuous >= N - 0.5)` the threshold probabilities are exactly invariant, so the
  integer-support CRPS delta is 0 by construction. The backtest pinball gain (2023, 199-grid: pts
  -0.0053, reb about -0.011, ast -0.0166, fg3m -0.0243) is an artifact of scoring an integer outcome
  with a continuous quantile function. The forward test therefore verifies pipeline fidelity, it does
  not test skill.
- **Primary check (deterministic).** On 100% of paired rows, max over thresholds |p_ge(int) -
  p_ge(as-is)| <= 0.0025 and integer-support CRPS delta in [-1e-6, +1e-6] on average; mean
  (continuous) bias identical. Any violation is a pipeline fault, reported at once.
- **Secondary (artifact size).** pinball19 delta per stat with the date-clustered CI: expected
  negative with CI upper < 0. Floor -0.005. It is labelled "scoring-convention gain, not skill" in
  every table and may NEVER be quoted as model improvement or enter a skill claim.
- **Population.** All paired player-games of the same `run_id` (no skipped games; the arm shares
  production's inputs).
- **Why still in the family.** The pinball19 delta enters BH (m = 9) so that a claim about
  quantile-output quality cannot be cherry-picked; its p-value will be near the floor.

## 4. Arm LT: `props_context_residual_lt` (pts only)

- **Hypothesis.** The short-minutes lower-tail mixture lowers integer-support pts CRPS vs production.
  Backtest: 2023 (selection) -0.0126 [-0.0172, -0.0080]; 2024 (report) -0.0109 [-0.0155, -0.0061];
  the gain is concentrated in short games (about 10% of rows: -0.22/-0.24) and is +0.012/+0.015 on
  normal-minutes rows, i.e. heavy-tailed paired deltas.
- **Primary metric.** Paired integer-support pts CRPS delta vs production (same `run_id`),
  floor -0.005. Secondary: threshold log loss on low thresholds, PIT q0.10 / q0.20, and the
  short-minutes vs normal-minutes slice split (short = projected-minutes decision made in the
  backtest; exact definition from `docs/LOWER_TAIL.md`). reb/ast/fg3m mixtures are not shadowed.
- **Population.** All paired pts player-games.
- **Power caveat accepted up front.** The paired variance is large relative to the effect; this arm
  is the least powered (section 6). A "no claim" outcome before the END look is expected and is not
  evidence of no effect.

## 5. Looks, alpha spending and decision outcomes (props arms)

1. **Looks (fixed, in distinct-date count of the arm-stat).** L14, L30, L60, L120, END
   (END = all regular-season dates through the final regular-season game, evaluated once, within 7
   days of the end of the regular season). L14 is monitoring only (pipeline fidelity, skipped share,
   guards, direction check; no efficacy claim, no futility decision). Efficacy looks are L30, L60,
   L120, END (K = 4). No other look has decision weight.
2. **Alpha spending.** Conservative Pocock-style constant nominal level: two-sided
   alpha_look = 0.0182 at every efficacy look (overall about 0.05 over 4 looks), applied to the BH
   adjusted p-value (rule 7) and with a 98.18% date-clustered percentile interval as the decision CI.
   (Pocock constant for K = 4 at overall 0.05 is 0.0182.) No re-use of discarded looks, no extra looks.
3. **Checkpoint snapshot.** When an arm-stat first reaches a look's date count, the scorer
   (section 8) writes ONE immutable record `forward_checkpoint_<arm>_<L>.json` (counts, deltas, CIs,
   BH-adjusted p, guards, timing measurements, skipped share, git SHA, seed) and appends a line to a
   checkpoint log. It is never regenerated or overwritten. Decisions are read from it.
4. **Minimum sample before ANY claim (both conditions, per arm-stat).**

| Arm-stat | min dates | min paired player-games | first efficacy look allowed |
|---|---|---|---|
| T30 pts | 60 | 5,000 | L60 |
| T30 reb | 60 | 5,000 | L60 |
| T30 ast | 120 | 10,000 | L120 |
| T30 fg3m | 120 | 10,000 | L120 (expected to fail the floor) |
| INT reb, ast, fg3m | 14 | 1,500 | L30 (deterministic check at L14) |
| LT pts | 120 | 10,000 | L120 |
| WIN | none (tripwire only) | none | never a claim |

   An efficacy look reached before its arm-stat's minimum is recorded but yields "keep shadowing".
5. **Outcomes per arm-stat at an eligible efficacy look.**
   - **PROMOTE-CANDIDATE** iff ALL: delta <= -0.005 (point); the 98.18% CI upper bound < 0; BH-adjusted
     p <= 0.0182; all guards (rule 8) hold; arm-specific gates hold (T30: logged share Wilson lower
     bound >= 0.70; INT: the deterministic check passes and the result is labelled convention-only,
     so its promote-candidate means "adopt the integer quantile convention for outputs", never a
     skill claim; LT: PIT window passes). A promote-candidate is only a recommendation for red-team
     review plus a maintainer decision. A pts-only or per-stat promotion is allowed (this document
     declares the per-stat decision in advance, unlike the 4-way conjunction of LINEUPS_KNOWN).
   - **DROP** iff (a) at L60 or L120 or END the CI lower bound (98.18%) is >= -0.005 (even the
     optimistic end does not reach the floor), or (b) at any look >= L30 the point > +0.005 with the
     CI lower bound > 0 (harm), or (c) a guard/leak failure that is not fixed by a documented, not
     result-driven, pipeline repair. Drop = stop logging the arm and record the result.
   - **KEEP SHADOWING** otherwise (including: significant but below the floor; CI straddles both 0
     and the floor; not yet eligible). After END the arm is dropped unless the END look gave a
     promote-candidate.
6. **No second chances.** A stat that fails at END is not re-tested on the same season. A new
   variant is a new arm.

## 6. Power and minimum detectable effect (from backtest variances)

Method: per-pair deltas from `reports/lineups_known/rows_<stat>.parquet` (2024, n = 27,583; 212 game
dates). Date-clustered SE of the mean at the 212-date season, scaled as SE(D) = SE_212 * sqrt(212 / D),
inflated by 1/sqrt(0.85) for T30 (placeholder 85% logged share; re-estimate at L14, descriptive
only). Date clustering was within a few percent of game clustering in the backtest (pts 0.00313 vs
0.00320), so no extra design effect is added. LT: SE_212 = 0.0024 from the 2024 game-clustered CI
half-width (0.0047). WIN: per-game SD of the paired log-loss delta 0.14, 6.2 games per date. Two-sided
alpha_look 0.0182 (z = 2.36), 80% power MDE = (2.36 + 0.84) * SE.

| Arm-stat | SE @30 / 60 / 120 / 165 dates | MDE80 @60 / 120 / 165 | planning effect -> power @60 / 120 |
|---|---|---|---|
| T30 pts | .0090 / .0064 / .0045 / .0039 | .020 / .015 / .012 | -0.023 -> 0.89 / 1.00 |
| T30 reb | .0040 / .0029 / .0020 / .0017 | .009 / .0065 / .0055 | -0.009 -> 0.79 / 0.98 |
| T30 ast | .0025 / .0018 / .0012 / .0011 | .0056 / .0040 / .0034 | -0.0045 -> 0.58 / 0.90 |
| T30 fg3m | .0013 / .0009 / .0007 / .0006 | .0030 / .0021 / .0018 | -0.0041 -> 0.98 / 1.00 (but the floor -0.005 is not expected to be met) |
| LT pts | .0064 / .0045 / .0032 / .0027 | .0145 / .0102 / .0087 | -0.0109 -> 0.52 / 0.85; half effect (-0.0055) -> 0.13 / 0.26 |
| WIN | .0103 / .0073 / .0051 / .0044 | .023 / .016 / .014 | -0.008 -> 0.10 / 0.21 (165 dates: 0.30) |

Consequences, adopted now: (i) T30 pts and reb are decidable by L60; ast and fg3m need L120.
(ii) LT is decidable only at L120/END and only if the true effect is near the backtest value; at half
that value it cannot be detected in one season. (iii) The win-model comparison cannot confirm or
refute a -0.008 log-loss gain in one season (power <= 0.30 at 165 dates); it is a harm tripwire only.
(iv) INT needs about 14 dates because the paired delta is nearly deterministic (2023 pts CI half-width
0.0006 on 1,318 games). (v) All minimums in section 5 follow from this table: the first look at which
power at the planning effect is >= about 0.75, rounded to a look.
"CI includes 0" below these dates means "no power", not "no effect".

## 7. Win model: `rung0_injury_elo` vs `rung0_mov_elo` (tripwire)

- **Hypothesis.** Injury-adjusted Elo has lower log loss than MOV-Elo (backtest -0.0081 [-0.0121,
  -0.0038], n = 3,951; 2025 holdout -0.0102 [-0.0180, -0.0026], n = 1,316). Already confirmed
  historically; the forward season is a drift/regression monitor, not a re-test.
- **Metric and population.** Paired per-game log loss delta (injury minus MOV-Elo) on games scored by
  both (`report._paired_win_section`); fallback games (MOV-Elo only) counted and reported. Date
  clustered bootstrap as rule 5. Brier and ECE descriptive.
- **Rule.** Efficacy claims are not made (section 6: underpowered). TRIPWIRE only: at L30, L60, L120,
  END, if the point delta > 0 AND the 98.18% CI lower bound > 0 (injury-Elo significantly worse) =>
  "REVIEW" (open an incident to audit the injury feed/tip-time gating; no automatic change). Otherwise
  "KEEP". The point estimate and CI are published at every look regardless.
- It enters the BH list (m = 9) as the ninth p-value, two-sided.

## 8. Scorer specification for engineers (not implemented here)

The existing `nba.daily.report` / `nba.daily.settle` do not implement the rules above. Gaps found:
(a) `settle` stores only the continuous pinball19 CRPS, no integer-support score; (b) `settle` takes the
latest prediction per key, not the T-60-eligible one; (c) the bootstrap uses B = 2,000 and a fixed
`seed=0`; no p-values; no BH; (d) there is no checkpoint writer; (e) T30 pairing uses the latest prod row.
Required, as a new module `nba/daily/checkpoint.py` (and tests), reading `forward_predictions`,
`forward_scores`, `forward_t30_decisions`, `games`, `player_game_stats`, and the lineup DB read-only:

1. `crps_int(p_ge: dict[str, float], y: int, kmax: int) -> float`: integer-support CRPS from the
   stored thresholds. Requires `p_ge` to cover N = 1..Nmax with `P(Y >= Nmax) < 1e-3` (suggest
   pts 80, reb 35, ast 30, fg3m 18); rows violating this are counted as `unscorable` and listed, not
   dropped silently. Property tests: non-negative, zero for a point mass at y, invariant under the
   `ceil(q-0.5)` transform, equals the RPS definition on a hand example. If current predictions do
   not store `p_ge` to Nmax, extend the stored prediction (additively) BEFORE the first 2026-27
   prediction; arms cannot be re-scored retroactively without it.
2. `paired_deltas(arm, comparator, stat) -> DataFrame[game_id, game_date, player_id, delta_int,
   delta_pinball19, bias_arm, bias_cmp, cover_arm, cover_cmp, slice_cols]` applying rules 1-3.
3. `date_cluster_boot(delta, game_date, B=10000, seed=2026, level=0.9818) -> (point, lo, hi, p_two_sided,
   n, n_dates)`: sums and counts per date via `np.unique(return_inverse)` and `np.bincount`; draw
   `idx = rng.integers(0, k, (B, k))`; mean = `sums[idx].sum(1) / counts[idx].sum(1)` (ratio-of-sums
   to keep unequal slate sizes honest); `p = min(1, 2*min(mean(boot>=0), mean(boot<=0)))` floored at
   `1/(B+1)`. Return NaN CI when `k < 2`. Unit test: coverage of a known null in a simulation with
   date-shared noise, and that a game-level bootstrap on the same data is anti-conservative.
4. `bh_adjust(pvals: dict[str, float]) -> dict[str, float]` over the fixed 9 keys
   `t30_pts, t30_reb, t30_ast, t30_fg3m, int_reb, int_ast, int_fg3m, lt_pts, win`; missing/ineligible
   tests enter with p = 1.0 (never drop from m).
5. `evaluate_checkpoint(look)`: applies section 5 (eligibility table, floor, CI, BH, guards, gates,
   outcome) and the section 2.1 timing measurements; writes the immutable JSON (refuses to overwrite)
   and a markdown block; also writes, for each arm-stat, every number needed to re-derive the outcome
   from the stored per-pair arrays (store the pair table as parquet next to the JSON).
6. Look detection by distinct-date count of the arm-stat (rule 11), run from the nightly job; a look is
   evaluated on the first run in which the count is >= the look count (using only dates fully settled).
7. Report integration: `report.py` gains a "Pre-registered checkpoints" section that PRINTS the latest
   immutable snapshots and labels all running CIs "descriptive, not decision-bearing".
8. For section 2.1: add the date-clustered bootstrap CI for the open-decision player-level share, the
   game-level share with Wilson CI, lead-minute deciles and the poll interval to
   `render_collector_section`; the existing Wilson interval on (game, player) pairs ignores
   within-game clustering.

## 9. Holdout rollover interplay (HOLDOUT_ACCESS_LOG rule 3)

- Rule 3 fires when >= 20 completed 2026-27 games are ingested: `season = 2026` becomes the canonical
  props holdout and `season = 2025` rejoins the tuning pool. The forward report already prints the
  trigger (`rollover_status`).
- This preregistration is the FIRST confirmatory use of 2026 for each of the four arms listed in
  section 0; no other use of 2026 is made or implied. Because every efficacy look reads 2026 rows, each
  look consumes the arm's confirmatory touch only once per arm-stat, which is why looks are fixed and
  alpha is spent (section 5.2), and why the END look is the last. After the END look the arm-stat has
  used 2026 and any later model that is evaluated on 2026 is a repeated-use touch for the shared
  holdout unless it never saw these results.
- Rollover does not relax or reset anything here. In particular: (i) no feature, hyperparameter,
  threshold, floor or model choice may be informed by 2026 forward CRPS of these arms, except the
  outcomes defined in section 5; (ii) a model tuned or selected after viewing the 2026 shadow numbers
  is NOT eligible for a "clean first touch" on 2026; (iii) walk-forward retraining on played 2026 rows
  is training and is allowed; (iv) arms may be shadowed on 2026 data before the rollover count of 20
  games is reached; the date of the rollover does not change the scoring rules; (v) the 2025 rows
  rejoining the tuning pool are not used by any arm's evaluation here.
- Per the existing convention, 2026 confirmatory touches of new models must pre-register a row in
  `docs/HOLDOUT_ACCESS_LOG.md`; this document does not append such a row (the shadow scoring is
  logged through the immutable checkpoint snapshots and the test ledger).

## 10. Known threats to validity (declared in advance)

1. T30 gain is upward biased in the backtest (box-score starters as proxy for announced starters;
   GTD resolution after T-60 is bundled in): expect the forward effect to land at or below the lower
   planning range; the planning values already use that range.
2. The T-30 population (confirmed before T-30) is selected; conditional-on-logged effects do not
   transfer to skipped games.
3. Day-to-day dependence (injury waves, rest) is handled by date clustering but not by serial
   correlation between adjacent dates; ten or fewer dates are never decisive; no claim before the
   table minimums.
4. Heavy-tailed paired deltas (LT, T30 pts): bootstrap CIs with few dates are optimistic; the
   minimum-date rule is the guard.
5. Production could change underneath the comparison (retrain, version bump). Comparator version is
   recorded per pair; a mid-season change in the comparator's version splits the analysis into two
   epochs, both reported, and the pooled claim requires both epochs to have the same sign.
6. Four arms, nine tests, four looks: the BH + Pocock combination is conservative but not exact;
   anything that passes only marginally (adjusted p between 0.01 and 0.0182) is reported as
   "marginal" and goes to the red team before any use.
<!-- FROZEN-END -->

## Amendments (append only, dated)

- 2026-10-09 (engineering note, no rule changed): section 8 gaps implemented. (1) Every forward prop
  row (primary, recency, `_int`, `_lt`, `_t30`) now stores the additive JSON field `p_ge_full`
  `{"n": N, "c": [c_1..c_K]}` with `P(Y >= k) = c_k / N`, k = 1..K, cut after the first k with
  `P(Y >= k) <= 1e-3` (cap pts 80, reb 35, ast 30, fg3m 18); N = 199 exact counts for the context
  quantile samples, N = 10,000 for parametric rows. Existing fields are unchanged. Mass beyond the
  cut (<= 0.1%) is treated as 0, so integer-support CRPS is exact to about 2e-3 only for outcomes
  beyond K, identical for arm and comparator. (2) `nba.daily.settle` also writes
  `forward_scores_elig` (latest row with `made_at` <= tip - 60 min; the T-30 arm tip - 30 min) with
  the integer-support CRPS, pinball19, PIT bounds and, for the win models, log loss and Brier;
  `forward_scores` is unchanged. (3) `nba.daily.checkpoint` writes `data/checkpoints/<look>_<date>.json`
  (+ markdown, + a pair-table parquet per arm-stat, sha256 of each, one log line); a look is
  recorded per arm-stat when that arm-stat first reaches the look's date count, and arm-stats not
  yet at the count enter BH with p = 1.0. The `_int` decision metric is the pinball19 delta (section 3);
  its integer-support delta and the deterministic fidelity check are reported beside it. The
  teammate-out slice is approximated at game level (any `player_availability` row with status 'out'
  for the game) and the starter slice uses the stored `starter_rate >= 0.5`.

- 2026-10-09 (AMENDMENT, implementation correction; no rule changed, no forward data exists yet):
  section 2 and the eligibility rule above say the T-30 arm is scored on its latest row "made at or
  before tip - 30 min". The first implementation tested `made_at <= tip - 30 min`, but a T-30 row is
  by construction written at or after tip - 30 min (the run waits for the T-30 cutoff and then reads
  the lineup snapshot), so no live row could qualify and `forward_scores_elig` would hold zero T30
  pairs. The rule's intent (and guard (e): snapshot `fetched_at < tip - 30 min` for 100% of rows) is
  that the INFORMATION used is at least 30 min old. Implemented now: a `_t30` row is eligible iff
  `made_at < tip` AND its stored `snapshot_fetched_at < tip - 30 min`; every other arm is unchanged
  (`made_at <= tip - 60 min`). The frozen section is unchanged (sha256 verified,
  `e323aa4817589147598109424718913b7b881d289213dc2399dc655ac2bed10a`). Test:
  `tests/daily/test_review_fixes.py`.
- 2026-10-10: verification method clarified, frozen text unchanged. The recorded sha `e323aa48...` is the output of
  the original substring-marker awk, which also covered header lines 7-11 above the BEGIN marker (and not the sha
  line, which precedes the verify line here); it still reproduces on the current file. The exact-line form of the
  verify command (now shown in the header) hashes the block strictly between the markers:
  `d2c8183c885c1488...` (full value: run the command). Either value proves the rule is unchanged since 2026-10-09.
