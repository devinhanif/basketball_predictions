# MARKET_PRIOR_RESIDUAL: does our model know anything the Pinnacle line does not? (pre-registration)

Status: **DRAFT, revision 1, written by the modeler Tue Oct 20 2026 (priors committed 23:12 CDT in 7a52a37, before any gate number; clock read by `date`).**
- **How it becomes the rule:** the main session commits `prereg(MARKET_PRIOR_RESIDUAL): <sha256>` of everything above the line
  `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_PRIOR_RESIDUAL.md | shasum -a 256`.
  The status line is changed from DRAFT to FROZEN in that same commit (the harness refuses a DRAFT).
- **Harness (new files only):** `research/eval/market_prior_residual_gate.py` (label-free gate), `..._rule.py` (the frozen constants,
  refusals and verdict logic, mirrored from this text) and `..._eval.py` (the run). Tests: `tests/research/market_prior_residual/`.
  The run refuses a DRAFT doc, a missing `prereg(...)` commit, a harness that differs from its blob at that commit, and any season > 2024.
- **Origin:** outside-LLM audit row R1 (docs/research/outside_llm_audit_2026-10-20.md); NEXT_SESSION plan item C; DECISIONS 2026-10-20.
- **Season labels** are `games.season`: 2023 = 2023-24 (select), 2024 = 2024-25 (report). 2025 = 2025-26 is the frozen holdout and this rule never touches it.
- **No outcome-scored comparison of any arm has been run.** The gate below is label-free except G6, which recomputes ODDS_HISTORY's
  published A0 and market cells to prove the comparator reproduces (it scores no R, PL or contrast).

## 1. Hypothesis, and the question for Devin
- **Question (Devin's words, plan item C):** does our model know anything the Pinnacle line does not?
- **H1.** Conditional on Pinnacle's earliest stored pre-tip line and de-vigged P(over), production's as-of features still predict the
  outcome: the residual arm R (market prior plus a small learned delta from our features) lowers per-leg log loss at the line against the
  market alone, and its disagreement with the market predicts outcomes.
- **Null.** The line already contains everything our features carry. R then sits on the market and pays a small overfitting cost.
- **What a result would mean for the product.** A PASS would be the first evidence that the model carries pre-tip information the sharpest
  book lacks. Anything else says what the ledger already says (T204-T214, T415-T416, T493): the line is at least as good, and the budget tool
  keeps answering "keep your money" on lines until the live log proves otherwise.

## 2. Lineage verdict, stated first (BEST_PRACTICES 2026-10-12; MODEL_PLAYBOOK question 4)
**Distinct from ODDS_HISTORY H2 in construction: yes. Distinct in information: no. The expected result is H2's, with less power.**
- **What is different from H2.** H2 and MARKET_SHRINK blend two finished forecasts (`logit p = logit p_mkt + w (logit p_model - logit p_mkt)`).
  R is retrained: a regularised tree ensemble that starts from the market (offset `logit p_M`), never sees production's own P(over), and learns
  from production's as-of feature frame which way the market's error runs. It is a different object, and it is judged by a different
  instrument (the disagreement regression, week and player-season clustering, a feature-scrambled placebo, a walk-forward).
- **What is not different.** R's information is production's own features, which A0 already used with four times the rows (27.6k per
  stat-season against 6.3k-13k Pinnacle legs). The playbook's first question ("information or capacity?") has the same answer as for H2:
  nothing new is known before tip. If our features carry anything the line lacks, A0 carries it too, and H2's w already measures it.
- **Measured here without a single real outcome (gate G9):** in a world where H2 is true by construction (outcomes drawn from the market
  blended with A0 at w = 0.25, the largest fit in H2), R recovers 33% of the oracle gain; at w = 0.5 it recovers 63%. R can only lose to H2 in an
  H2 world. It could win only in a world where the signal is in the features but not expressible through A0, which has little prior mass.
- **Recommendation.** Closing without a run is a reasonable choice. The case for running is cheap and narrow: H2's only pass (reb, -0.0022
  [-0.0035, -0.0009], rated WOUNDED) was game-clustered only, was fitted once on 2023, and the rule had no placebo; this rule re-asks the
  question with week and player-season clustering and a placebo, and costs about 30 minutes of CPU. A PASS at the floor is close to
  unreachable (below); the realistic best outcome is `INFO_BELOW_FLOOR`. If Devin wants a rule that can say yes at the floor, the instrument is
  not R (see section 11, "Floor reachability").
- **Lineage table.**

| ancestor | what it did | numbers (quoted from the ledger) | guards that disqualified it | carried or dropped here |
|---|---|---|---|---|
| ODDS_HISTORY H2, T204-T214 | logit blend of A0 and the market, w fit on 2023, scored on 2024, Pinnacle and consensus | reb vs Pinnacle -0.0022 [-0.0035, -0.0009] (the one pass, WOUNDED); pts -0.0004 [-0.0010, +0.0002]; ast -0.0006 [-0.0010, -0.0001]; others indistinguishable | floor -0.002 plus Holm in a family of 16; red team: WOUNDED (docs/reviews/redteam_odds_history_reb_blend_2026-10-10.md); money +0.84% [-1.65%, +3.38%] | floor carried as -0.003 (stricter); family changed to 8 with BH (section 5); H2's w-only blend is re-run walk-forward as the reference arm H2W |
| MARKET_SHRINK, T415-T416 | DraftKings walk-forward blend plus a temperature on the model logit | pts B2 minus market -0.0004 [-0.0009, +0.0002] (fails log loss); reb -0.0014 [-0.0024, -0.0005] (fails the decision guard) | decision-score guard and the select-season sign; planted shuffle | decision guard dropped here (this is an information test, not a bet rule); select-season sign carried as the coefficient sign (section 6); DraftKings kept as a direction check only |
| GAME_MARKET_CONTEXT, T389-T414 | the T-60 market game lines (spread, total) as the props model's game context | no cell passes; every arm within +-0.001 of its within-date-shuffled placebo; CLOSED | within-date derangement placebo (R2) | carried: PL is a within-(date, stat) derangement of the production block; closed rules are not re-opened (no game-line columns here) |
| LINE_CALIBRATION, T493 | map of the model's P(over) at the line fit on outcomes | CALIBRATED; CAL minus A0 -0.0117 [-0.0139, -0.0096], n 37,528 legs / 1,260 games; CAL 0.6882 still trails the de-vigged market 0.6865 | sized permutation null, base-rate comparator | floor -0.003, stage structure, 365-day half-life weights, pairs pins and bootstrap code carried; the shuffled-label control is replaced by a feature derangement (section 4, PL) because shuffling labels breaks the offset |
| OWN_TAG harness / A0 frame | production's as-of feature frame and walk-forward | A0 reproduces 2023 exactly; 2024 stored p_model differs from today's frame on about half of legs (G6) | real tip times, season <= 2024 | frame carried (`stat_frame`, `stat_feature_names`); the A0 data-version difference is disclosed and handled (section 8) |

## 3. Arms
| arm | what | role |
|---|---|---|
| M | Pinnacle t60 de-vigged P(over) at the line, `p_over_mult` (the multiplicative de-vig ODDS_HISTORY chose; no other de-vig is considered) | the comparator for the claim |
| R | LightGBM binary with `init_score = logit p_M`: `logit p_R = logit p_M + f(x)`, f fitted on the outcome | **the claim arm** |
| PL | R refit with the production-feature block given to another leg of the same (date, stat); average over K = 50 draws | placebo (what R gains without our features' leg-specific content) |
| A0 | production's stored P(over) at the line, `p_model` | secondary comparator |
| H2W | walk-forward w-only blend of A0 and M (grid 0..1 by 0.05, per stat, unweighted, w = 0 under 2,000 earlier legs of the stat) | collapse reference, secondary |
| R_NR | R without the report columns (`has_report`, `since_flag`, `flag_dir`, `since_team`, `n_out_rot`, `vac_min`, `vac_own`) | first attack on a pass, secondary |

- **R in full.** Pooled over the four stats (one fit per month, features generic: own-stat names `m5_own, m40_own, m100_own, sd10_own,
  role_gap_own, rate_own, opp_allow_own, vac_own`, recency mean and std, a numeric stat code). Inputs: the 38 production names of
  `COMMON_FEATURES` and the four `m10_*`, the generic own-stat columns, and four market columns: `line`, `logit p_M`, `line - m`, `(line - m) / max(s, 0.5)`.
  R never receives production's P(over) or any function of production's quantile grid; the only route from A0 to R is the features A0 also reads.
- **Fixed capacity (never tuned):** `learning_rate 0.02, n_estimators 200, num_leaves 4, max_depth 3, min_child_samples 100, subsample 0.7,
  colsample_bytree 0.5, reg_lambda 10, seed 0, deterministic, 1 thread` (the constants `LGB_PARAMS`). Why this small: the gate's planted worlds
  show a bigger ensemble (400 trees, 8 leaves, learning rate 0.05) overfits the 6k-70k legs: in the w = 0.25 world it scored +0.0021 against the market,
  against -0.0004 for the fixed one (scratch, synthetic labels, seed 7). Fit rows carry weight `0.5 ** (age_days / 365)` (LINE_CALIBRATION's half-life).
- **CRPS against the market is dropped.** Pinnacle posts one point of the distribution (P(Y >= line + 0.5)) and two prices. Any CRPS needs an
  assumed family and a dispersion, and the only dispersion we have is the model's, so a "market-alone CRPS" would import the thing under test.
  There is also no R distribution: R emits a binary probability at the line. Everything here is scored as log loss and Brier at the line
  (MODEL_PLAYBOOK question 5). R minus A0 is reported on that scale only.

## 4. The rule
Let a leg be one Pinnacle t60 main line, two-sided, for a played player-game, with an x.5 line (no push), snapshot at least 60 minutes before the
real tip (hard filter, count dropped reported), stats pts, reb, ast, fg3m, unique per (game, player, stat).

**Primary, on the report season 2024 (walk-forward, 2023 as warm start). All three required for PASS:**
- **(a) Floor.** Pooled over stats, per-leg log loss R minus M <= **-0.003** with the binding 95% CI upper < 0.
  *Protects against:* announcing information that cannot pay for itself. Pinnacle's overround is 7.0% in both seasons (mean 0.0705 / 0.0700),
  so a bettor needs a 3.5 pp edge on a side just to break even; a uniform disagreement of delta pp is worth about 2 delta^2 in log loss,
  which is 0.0025 at 3.5 pp. -0.003 is that scale, and it is LINE_CALIBRATION's floor. Not a result-driven number.
- **(b) Disagreement.** In the report season, the logistic regression `P(over) ~ 1 + logit p_M + (logit p_R - logit p_M)` has a coefficient on the
  disagreement term that is positive and whose binding CI excludes 0. *Protects against:* a log-loss gain that comes from shrinking or re-centring
  M (a win with no information); b is zero if R's departure from the market is noise and is positive only if the departure points the right way.
- **(c) Placebo.** R minus PL <= -0.003 with the binding CI upper < 0, PL being the mean over K = 50 feature-derangement fits.
  *Protects against:* a gain that any refit with the market offset would show, the features' leg-specific content not mattering
  (GAME_MARKET_CONTEXT R2). PL keeps each row's market columns, label and offset and gives the production block (including the recency mean and std
  that enter `line - m`) to a different leg of the same (date, stat); the mixed columns are re-derived. It is not a label shuffle: shuffling labels breaks
  the offset (the market is informative of the outcome), so that placebo would be worse than M by construction.

**Binding CI.** Percentile 95% CI of a paired cluster bootstrap, B = 2,000 resamples, seed 0 (`research.eval.model_clv_eval.Boot`, one
multinomial resample matrix per clustering), under three clusterings: **game** (`game_id`), **player-season** (`player_id`, `season`) and **ISO week**
(ISO year-week of `game_date`). The binding CI is the envelope [min lower, max upper] over the decisive clusterings, which is at least as wide as the widest.
Week clustering is decisive only with >= 20 weeks in the season (`MIN_WEEKS`), otherwise reported only. Cluster counts per season are in section 8 (G3).
For (b) every resample reweights rows by their cluster's multinomial count and refits the regression from the point estimate.

**Verdict words** (`verdict()` in the rule module; unit-tested):
- `PASS`: (a), (b), (c) and the select-season sign (section 6).
- `INFO_BELOW_FLOOR`: (b) holds and R beats PL (point < 0, CI upper < 0), but (a) or (c) misses the floor. The line is not efficient with respect to
  our features, by less than the practical floor. Not a pass, not a bet.
- `PLACEBO_EXPLAINS`: (b) holds but R does not beat PL. `FAIL_NO_DISAGREEMENT_SIGNAL`: (a) holds, (b) does not.
  `FAIL_WORSE_THAN_MARKET`: R minus M has CI lower > 0. `FAIL_NO_GAIN`: none of the above. `FAIL_SELECT_SIGN`: all three hold in 2024 but the 2023 coefficient is not positive.

**Secondary (labels only, no pass).** BH over a declared family of **8**: per-stat R minus M (4, one-sided "R beats M", p = worst case over decisive
clusterings) and per-stat disagreement coefficient (4). Also reported without a decision weight: Brier R minus M; R minus A0 on the stored p_model
and on today's-vintage A0 (section 8); A0 minus M; the A0 disagreement coefficient `logit p_A0 - logit p_M` (H2's information in the b-test form);
H2W minus M and R minus H2W (the collapse check); R_NR minus M; the DraftKings direction check (the same R fit on DraftKings legs with its own market
prior; the 2024 point estimate sign only); slices of R minus M (n >= 300): starter vs bench (`starter10 >= 0.5`), `min10` cells (< 15, 15-25, >= 25),
first 15 team games (`team_game_no <= 15`), teammate-OUT (`n_out_rot >= 1`), report present vs absent; the placebo null mean and sd; the share of legs held
by the top five player-seasons. **T-60 lead buckets are dropped:** the stored lead is 64.4 minutes for 99% of legs (section 7), so there is no
variation to slice on.

## 5. Multiplicity
- Primary: three checks, all required, one stat-pooled contrast each; no correction needed (an intersection).
- Secondary family: the 8 tests above under BH at 0.05. Everything else is descriptive and carries no pass.
- Compared with H2 (family of 16 with Holm), this rule tests fewer things at a stricter floor.

## 6. Stages and the 2025 touch
- **Stage 1 (select, 2023).** Expanding month-block fits on Pinnacle t60 legs of the same season, starting at the first month with >= **3,000** prior
  pooled legs (`N_MIN_TRAIN`; chosen from the gate's leg counts before any outcome: the first month is Oct 2023, so Oct and Nov are unscored, 28,108 of
  35,146 legs are scored). Select-season sign condition: the disagreement coefficient on 2023 is positive (point estimate only). R minus M on 2023
  is deliberately not part of it: the gate's null rehearsal shows R costs +0.00053 per leg in 2023 by overfitting alone.
- **Stage 2 (report, 2024).** Each month fit on all 2023 legs plus the 2024 months before it, time-weighted; every month scored (35,146 legs of warm start, then 37,528 scored).
- **2025.** Never touched, never loaded. **This rule does not earn a 2025 touch, whatever the result** (recommendation: NO). The live 2026-27 log is
  the confirmation. A pass would be exploratory on 2023-24, the same two seasons as H2 and MARKET_SHRINK, so it cannot count as independent evidence.
  No HOLDOUT_ACCESS_LOG row is drafted.
- **Kill criteria.** (1) The gate fails on data after the capture audit: stop. (2) The planted future or the A0 reproduction fails in the run: stop, no result.
  (3) Stage 1 has a non-positive disagreement coefficient and Stage 2 has none of (a)-(c): closed. (4) After a result, nothing about the rule changes;
  one pre-registered follow-up at most (the live-capture version, under its own id), then closed.

## 7. Known-at (outside audit P1): what "t60" is, and what R may know
- **What the stored snapshot is.** `odds_history` stores exactly one `t60` snapshot per game for Pinnacle (raw audit: mean 1.0, max 1 distinct
  `snapshot_ts` per game and kind, both seasons): the 5-minute-grid snapshot requested for T-60 and taken as the closest at or before the request. There is no
  earlier stored snapshot, so "the earliest stored pre-tip snapshot" and "the t60 snapshot" are the same row. Its `snapshot_ts` is compared with the **real**
  `tipoff_utc` (never the 19:00 proxy).
- **Measured lead (label-free, legs; minutes before the real tip).** Pooled n 72,674: median 64.37, p10 64.33, p90 64.37, min 62.55, max 79.37.
  2023 (n 35,146): median 64.35, p10 64.32, p90 64.37, min 62.55, max 79.37. 2024 (n 37,528): median 64.37, p10 64.35, p90 64.38, min 64.33, max 64.38.
  Per game (1,249 / 1,260 games) the quantiles are the same to 0.01.
- **Hard filter.** A leg is kept only if `snapshot_ts <= tipoff_utc - 60 min`; a missing stamp or missing real tip is dropped, never imputed. Rows dropped:
  **0** of 35,146 (2023) and 0 of 37,528 (2024), because the pull always lands 62.5 or more minutes before tip. The filter is real code, unit-tested at 61, 60,
  59 and 5 minutes, NaT and missing tip.
- **Train/serve skew against the live T-60 capture.** History: the market is taken 64.4 minutes before tip, almost constant. Live: the pretip job runs at :20 and
  :50, so the live comparator is "the latest run with made_at <= tip - 60", between about 60 and 100 minutes before tip (docs/DAILY_PIPELINE.md, Timing). The live
  lead distribution has not been measured here (the live capture tables are outside this role's read scope); the skew is at most about 40 minutes, in the direction
  of a staler live market. A rule fitted on history therefore sees a slightly fresher market than it would be served.
- **Information the two sides hold at that snapshot.** The market quote has everything public at T-64, including news after our report snapshot. Our feature frame
  uses the latest usable official report stamped <= tip - 60, whose measured lead is p10 / p50 / p90 = 120 / 180 / 300 minutes in both seasons
  (docs/research/questionable_status_scope_2026-10-15.md). So the market is one to four hours fresher on injuries than R's features. That makes a
  spurious pass from "R saw later news than the line" unlikely, with one exception: the stored report stamps of 3.8% (2023) and 4.8% (2024) of games are
  up to 30 minutes early (BEST_PRACTICES 2026-10-15), so a few games feed R a report the line did not yet have. R_NR (report columns removed) is the first attack
  on any pass for exactly this. No lineup information after T-60 enters any column.
- **No price enters the features** other than the T-64 Pinnacle quote itself (`line`, `logit p_M`, two derived columns). The T-5 snapshot is never used.

## 8. Minimum-data gate, measured on the real data (non-negotiable 8)
Final run started 2026-10-20 23:47 CDT (clock read by `date`; harness commit b7675e6): `research/eval/market_prior_residual_gate.py` at the harness commit recorded in
`reports/prereg_market_prior_residual/gate.json`; read-only, seasons 2023-24, 290 s on CPU with K = 10 placebo draws (peak RSS not measured). **All 27 checks pass.**
Inputs (sha256 in the JSON): `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` equal LINE_CALIBRATION's pins (050dba3e, 560a3808);
`reports/odds_history/{model_rows,market_rows}_{2023,2024}.parquet`; `data/odds/odds_history.duckdb` (raw audit, `season <= 2024` in SQL).

| check | measured | bar | pass |
|---|---|---|---|
| **G1** Pinnacle t60 leg share of production OOF rows (all rows / rows with >= 20 minutes) | 2023: pts 0.441 / 0.664, reb 0.230 / 0.343, ast 0.243 / 0.378, fg3m 0.359 / 0.533. 2024: pts 0.472 / 0.693, reb 0.241 / 0.350, ast 0.245 / 0.373, fg3m 0.402 / 0.581. Model rows per stat-season 27,619 / 27,583 | reported (a coverage fact) | n/a |
| **G2** legs (games), 2023 | pooled 35,146 (1,249); pts 12,189, reb 6,339, ast 6,714, fg3m 9,904 | pooled >= 20,000; per stat >= 5,000 | yes |
| **G2** legs (games), 2024 | pooled 37,528 (1,260); pts 13,021, reb 6,645, ast 6,771, fg3m 11,091 | same | yes |
| **G3** clusters (game / player-season / ISO week), 2023 | 1,249 / 377 / 35; top five player-seasons hold 4.6% of legs, the largest 0.98% | >= 1,000 / >= 200 / >= 20 | yes |
| **G3** clusters, 2024 | 1,260 / 414 / 35; top five 4.3%, largest 0.93% | same | yes |
| **G4** projected 95% half-width of R minus M under an outcome drawn from M, real R, binding clustering | select 0.00048 (28,108 legs), report 0.00024 (37,528 legs); game / player-season / week in the JSON, all within 10% of each other | <= 0.0015 | yes |
| **G4** A0 minus M, same method (an upper bound: A0 differs from M by much more than R does) | half-width 0.0033 (2023), 0.0031 (2024), week clustering binding; power at -0.003 about 0.44-0.47 | reported | n/a |
| **G5** rows dropped by `snapshot_ts <= tip - 60`; min lead | 0 (both seasons); 62.55 min | count reported; min >= 60 | yes |
| **G6** n equals ODDS_HISTORY h1 for Pinnacle t60 (8 cells) | 0 differences | 0 | yes |
| **G6** A0 and market log loss equal ODDS_HISTORY h1 (8 cells) | max abs diff 1.1e-16 | <= 1e-9 | yes |
| **G6** P(over) at the line from the stored survival vectors equals the pairs `p_model` (8 cells) | max abs diff 0.0 | <= 1e-9 | yes |
| **G7** legs with no row in today's feature frame | 0 of 72,674 (both seasons, all four stats) | share <= 0.005 | yes |
| **G7** largest NULL rate of any production feature on legs | 0.048% (`n_out_rot`, `vac_min`, `vac_own`) | <= 5% | yes |
| **G8** planted future (labels flipped on/after a block's first day; synthetic) | 2 of 2 blocks identical; flipped inside the training window, 2 of 2 moved | all | yes |
| **G8** R minus M under an outcome drawn from M (overfit cost) | select +0.00053 [+0.00004, +0.00101]; report +0.00032 [+0.00007, +0.00055] | <= 0.0010 | yes |
| **G9a** planted w = 0.25 (outcome from the market blended with A0, w = H2's largest): disagreement coefficient, envelope CI lower | coefficient 0.632, CI lower 0.297 | > 0 | yes |
| **G9b** planted w = 0.5: R minus M recovers >= 40% of the oracle gain | R minus M -0.00389 [-0.00478, -0.00302]; oracle -0.00615; share 0.633 | >= 0.40 | yes |

- **Data vintage (G6 and G7, disclosed).** The pairs frames and stored OOF rows are the 2026-10-10 vintage; today's production frame (the one R's features come from)
  differs from them on about half of 2024 legs, because of the 118-row backfill (d872dc7; T493-T496 note). 2023: all four stats' P(over) at the line reproduce
  from today's A0 grids to 1e-9 on 100% of legs. 2024: 47-49% of legs per stat reproduce (max abs difference 0.12). The features R reads equal the 2026-10-11
  A0 cache values on all 72,674 legs (`min10, n_prior, starter10, team_game_no, n_out_rot, has_report`: 0 mismatches). Consequence: R's features and the stored
  A0 are different vintages in 2024. The primary contrasts (R vs M, PL) do not involve A0. The secondary R minus A0 is reported against both the stored `p_model`
  (published comparator) and today's-vintage A0 (from the GAME_MARKET_CONTEXT cache, with its own caveat that the cache is not in git).
- **Null rehearsal and placebo size (K = 10 here, K = 50 in the run).** Under an outcome drawn from the market, R minus PL is -0.00027 (select) and -0.00005 (report);
  the placebo's per-draw pooled log loss has sd 0.00020 (select) and 0.00008 (report). BEST_PRACTICES 2026-10-12 asks that a shuffle guard be sized from a permutation
  null: (c) compares R with the mean of K = 50 draws, so its null sd is about 0.00008 / sqrt(50) of that scale, three orders below the floor.

### Floor reachability (a finding from G9; read before freezing)
Outcomes planted from an H2-type world, report season, same legs, R with the production features only:

| planted w (A0 weight) | oracle minus M | R minus M [95% CI] | share recovered | disagreement coefficient [CI] | R reaches -0.003? |
|---|---|---|---|---|---|
| 0.25 (H2's largest fit; reb, fg3m) | -0.00110 | -0.00036 [-0.00076, +0.00005] | 0.33 | 0.632 [0.297, 0.958] game clustering | no |
| 0.5 | -0.00615 | -0.00389 [-0.00478, -0.00302] | 0.63 | 1.208 [1.015, 1.397] | yes |

(The scratch run with an independent seed gave -0.00036 at w = 0.25 and -0.00273 at w = 0.5; the planted outcome draw alone moves these by about 0.001.)
H2's fitted weights on 2023 were pts 0.15, reb 0.25, ast 0.10, fg3m 0.25. So even an oracle that knew the truth would gain only about -0.0011 per leg at the
largest observed weight, and R would show a third of that. **Check (a) and the floor half of (c) are unreachable at any weight H2 has observed; the instrument that can
detect such a signal is (b)** (it detects w = 0.25 with the CI lower bound at 0.30). This is why `INFO_BELOW_FLOOR` exists. P(PASS) is about 0.01 by
construction, not because the question is hard. Options for Devin before freeze: (i) freeze as written: a rule that can in practice only say `INFO_BELOW_FLOOR` or no;
(ii) change the floor of (a) and (c) to -0.0010 (a one-constant edit, `FLOOR`): still not reached at w = 0.25, so it buys little; (iii) close without a run.

## 9. Data-capture audit (BEST_PRACTICES 2026-10-12)
- **Used:** `odds_history` Pinnacle t60 for 2023 and 2024 (101,392 and 107,392 raw rows; 35,146 and 37,528 legs after two-sidedness, main line, join to a model row, played, x.5,
  known-at); production OOF rows 2023-24 (27.6k per stat-season); today's feature frame for seasons <= 2024 (it also feeds on 2019-22 box scores and reports through its
  rolling windows; no props lines exist for them).
- **Exist but unused, and why:** Pinnacle t5 (later than the production prediction time, leaks lineup news; never a prior); DraftKings t60 (53k-54k legs; direction
  check only, to avoid book shopping); FanDuel and consensus rows (not the sharp book; pooling books double-counts legs); the other Pinnacle markets (game lines closed by
  GAME_MARKET_CONTEXT).
- **Do not exist here:** `odds_history` has 0 rows before 2023 (`season < 2023`: 0). data/history (2019-21) holds box scores and injury reports, no prices.
  As far as I know a historical props archive before 2023 is a paid vendor product; **not verified, no free source was searched.** Rows with season > 2024 are excluded in SQL
  and never counted or described.
- **Repairable:** nothing that changes power. The gate passes with a wide margin on legs, games and clusters; power is set by the report season, which passes.
- **Era flags:** none. Both seasons are full regular seasons plus playoffs; June blocks have few legs (194 and 274 scored in the last month).

## 10. What each choice protects against
| choice | protects against |
|---|---|
| prior = the single stored Pinnacle t60 quote, real-tip hard filter | a prior that carries news the live pipeline cannot have; a proxy tip (the 19:00 incident) |
| R never receives production's P(over) | R collapsing into a re-weighted A0 (that is H2, already run) and leaking A0's data-richer fit into a small-sample arm |
| offset `logit p_M` plus small fixed capacity | an unconstrained model re-learning the market from 6k-70k legs and being scored against it; tuning on results |
| (b) the disagreement regression | a log-loss gain with no information (shrinkage or re-centring), which LINE_CALIBRATION showed is cheap (+0.008 for a base-rate map) |
| (c) with a feature derangement, not a label shuffle | a placebo that is worse than M by construction (shuffled labels break the offset); a refit effect mistaken for information |
| three clusterings, envelope CI | game-only CIs that missed player and week shocks (T500: 5 of 115 players carried 98.6% of a gain; outside audit P2) |
| floor -0.003 | calling information a pass when it cannot pay a 7.0% overround |
| select 2023 / report 2024, 2023 as warm start, 365-day weights | pooling stale seasons equally; fitting and scoring on the same rows |
| `N_MIN_TRAIN` = 3,000 fixed from leg counts | a first month with a few hundred legs dominating 2023 |
| select sign = coefficient only | masking a true small gain with 2023's measured overfit cost |
| H2W, R_NR, A0-disagreement as secondaries | a pass being explained away or believed without the collapse and timing attacks |
| no 2025 touch | spending the holdout on a rule that cannot pass by design; counting the same two seasons as confirmation |
| gate G9 positive control | freezing an instrument that cannot see a planted signal (an invalid test is not a null) |

## 11. Playbook check (docs/MODEL_PLAYBOOK.md; class: gradient-boosted trees on a market offset, with a quantile-layer scoring question)
1. **Information or capacity?** Capacity only. R reads production's own features; the one new input is the market line, which is a prior, not a feature of the
   player. The playbook's own finding (information beats architecture) predicts a null, and the ledger's market rows agree.
2. **Enough data for this class?** Moderate: 6k-13k legs per stat-season is small for 38 features. The fixed small capacity and the pooling over four stats are the answer; G8 shows
   the overfit cost is +0.0003 to +0.0005 per leg, and G9 shows it still detects w = 0.25 through (b). A learning curve is not run (R cannot get more legs: the lines exist for 2023-24 only).
3. **Valid test?** Real tips (lead measured), played games only (the books void the rest, as in ODDS_HISTORY), planted future and positive control run on real rows with
   synthetic labels, integer support moot (a binary event at an x.5 line), game, player-season and week clustering, placebo sized from a null, comparator = published A0 with
   its data-version caveat. Power is not the limit (half-width 0.00024 against a floor of 0.003); sensitivity is (G9). Known weakness: two seasons of retrospective data that
   H2 and MARKET_SHRINK already used.
4. **Lineage:** section 2.
5. **What it scores against:** log loss and Brier at the Pinnacle line, which is the question asked; CRPS vs the market is not defined (section 3); no decision score is computed
   because the rule is an information test (BEST_PRACTICES 2026-10-11 applies if a pass is ever turned into a bet rule).
- **Class checks (trees):** features as-of (production frame, planted future above); missingness audit (G7: 0 misses, max NULL rate 0.048%; the NULL pattern is not outcome-dependent
  because legs are selected by Pinnacle posting a line before tip, not by the result); duplicated signals (the report columns are knocked out in R_NR); does the arm only re-express the base?
  (the A0-disagreement and H2W secondaries answer this).

## 12. Hypothesis priors (PREDICTIONS_LEDGER rows; drafted, not appended)
Row 1, written Tue Oct 20 23:12 CDT 2026 (`date`), before any gate number (committed in 7a52a37): P(gate G1-G7 pass) 0.75; P(PASS) 0.02; P(a) 0.04; P(b) 0.12; P(c) 0.03; expected R minus M
pooled 2024 +0.0004 (80% -0.0020 to +0.0035); P(collapses to H2: R minus H2W within +-0.001) 0.55; P(keep-your-money outcome) 0.97.

Row 2, written Tue Oct 20 23:52 CDT 2026 (`date` in the same command), after the gate, not blind to G1-G9 and the floor-reachability table, blind to every real-outcome contrast:
P(gate passes) was 0.75, measured 27 of 27 pass. P(PASS) 0.01. P(a) 0.02 (needs an oracle near -0.005; H2's pooled best is about -0.0005). P(b: coefficient positive, binding CI excludes 0) 0.30
(H2 saw reb significant and the other three borderline; G9 detects w = 0.25). P(c at the floor) 0.01. P(INFO_BELOW_FLOOR) 0.15; P(PLACEBO_EXPLAINS) 0.10; P(FAIL_NO_GAIN or FAIL_WORSE_THAN_MARKET) 0.72;
P(FAIL_NO_DISAGREEMENT_SIGNAL) 0.01; P(FAIL_SELECT_SIGN) 0.01. Expected R minus M pooled 2024: +0.0000 (80% -0.0012 to +0.0010). P(R minus H2W has CI upper < 0) 0.04;
P(|R minus H2W| <= 0.001) 0.85. P(2023 disagreement coefficient positive) 0.65. P(Devin hears "keep your money" from this rule) 0.97.

## 13. Data exposure (honest)
- **Seen:** every number above; the ledger rows quoted; ODDS_HISTORY's published A0 and market cells (G6 recomputes them); the H2 weights; LINE_CALIBRATION's Pinnacle 2024 A0 log loss 0.6999 and market 0.6865.
- **Not seen:** any R, PL, H2W or R_NR number on a real outcome; the disagreement coefficient of A0 or R on real labels; any per-slice contrast. The synthetic worlds are label-free
  (outcomes drawn from the market or from a planted blend, seeds in the JSON).
- Both seasons were used for H2 (fit 2023, scored 2024) and MARKET_SHRINK; this rule cannot be fresh confirmation of either.

## 14. Unstated details fixed before any scored number
- **Leg:** a Pinnacle t60 row in the pairs frame with `status == 0` and not `_dnp`; lines are x.5 (0 pushes); one leg per (game, player, stat).
- **Pairs frame:** `data/ops/model_clv_pairs` (pins above), `over` is the label; the harness refuses a frame that differs from its pin. In a worktree the untracked data
  is read from the main checkout (`git rev-parse --git-common-dir`).
- **Months:** calendar month of `game_date`; fit rows strictly earlier (asserted: `time_weights` raises on age <= 0); weights `0.5 ** (age_days / 365)`.
- **Clipping:** the market probability is clipped at 1e-6 only to take a logit; log loss clips probabilities to [1e-12, 1 - 1e-12]; production P(over) is clipped to [0.5/199, 1 - 0.5/199].
- **LightGBM:** `init_score` = offset in training; the prediction is `offset + raw score` (unit-tested: the offset is not inside the booster); features contain NaN for any missing value (none observed).
- **Disagreement regression:** Newton steps, a 1e-8 ridge on the slopes only, B = 2,000 refits from the point estimate, one-sided p = (1 + #{coef* <= 0}) / (B + 1).
- **Placebo seeds:** draw k uses `default_rng(1000 + k)`; groups are (game_date, stat); singleton groups are pooled; the permutation is one cycle (a derangement).
- **Synthetic labels in the gate:** `default_rng(20261020)` for the market-drawn world; planted worlds seeds 7 (w = 0.25) and 8 (w = 0.5).
- **ISO week:** `iso_year-week` of `game_date`; a late-December date can fall in week 1 of the next ISO year (unit-tested).
- **Concurrency:** the gate and the run take `data/ops/heavy.lock` (mkdir, pid in `owner`, retry every 120 s, removed only if the pid is ours).
- **CI module:** `nba/truth/bootstrap.py`, named in the brief as the only allowed CI code, does not exist at base 85a928f; the harness uses `research.eval.model_clv_eval.Boot`
  (the LINE_CALIBRATION precedent), pinned with the other harness files.

## 15. Commands (maintainer)
```
M="-m research.eval.market_prior_residual_eval"
.venv/bin/python -W ignore -m research.eval.market_prior_residual_gate                   # re-measure the gate (about 5 min, takes data/ops/heavy.lock)
.venv/bin/python -W ignore $M run --sha <64 hex>                                         # after `prereg(MARKET_PRIOR_RESIDUAL): <sha>` is committed
```
- `run` refuses: a DRAFT doc, a hash that differs from the text above the marker, a missing freeze commit, a harness file differing from its blob at the freeze commit, a pairs
  frame that differs from its pin, any season > 2024.
- Dry-run count (the legs it will fit and score): 35,146 (2023; 28,108 scored) and 37,528 (2024) Pinnacle legs, 17 monthly fits for R, 17 x 50 for PL, 17 for R_NR, 17 for DraftKings
  (about 53k-54k legs a season), bootstraps 2,000 refits per cluster kind. Estimated 25-35 minutes on CPU from the gate's timings (R 1-2 s a fit; a placebo draw 8-11 s a season).
  Peak memory not measured; the frame is 72,674 rows by about 50 columns.
- Outputs: `reports/prereg_market_prior_residual/results.json`; ledger rows are drafted by the main session from it.

=== RESULTS BELOW ===
