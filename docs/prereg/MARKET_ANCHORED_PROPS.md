# MARKET_ANCHORED_PROPS: split "the model knows something" from "the market is mis-calibrated" in a market-anchored prop forecast (pre-registration)

Status: **DRAFT, written by the modeler Fri 2026-10-23 (clock read by `date` at the gate runs, 20:26-20:45 CDT). NOT frozen. No outcome of any real leg has been read by this rule or its harness.**
- **Required before the freeze:** an adversary pre-freeze review (BEST_PRACTICES 2026-10-12: a hard stop, not a citation). Questions I would put to it: (i) is the market word's guard (C_mkt_after < 0) too lenient or too strict; (ii) is the `u1` soft-book gap a sound market-side covariate when books copy Pinnacle; (iii) is `tmout` (n_out_rot >= 3) the right depletion flag or a team-quality proxy; (iv) does the 2024 stored-`p_model` vintage difference change the placebo.
- **How it becomes the rule:** the main session commits `prereg(MARKET_ANCHORED_PROPS): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_ANCHORED_PROPS.md | shasum -a 256`.
  The status line is changed from DRAFT to FROZEN in that same commit (the harness refuses a DRAFT).
- **Harness (written):** `research/eval/market_anchored_props_core.py` (legs, arms, walk-forward, bootstrap, verdict) and `research/eval/market_anchored_props_gate.py`
  (label-free gate; all outcomes synthetic), tests `tests/research/market_anchored_props/`. **Not yet written (after the freeze, must implement this text exactly):**
  `market_anchored_props_rule.py` (the frozen constants and refusals, mirrored from this text), `market_anchored_props_eval.py` (the `run --sha` entrypoint, label loading,
  the Stage 2 ladder arms, the secondary family, slices, `results.json`). The run refuses a DRAFT doc, a missing `prereg(...)` commit, a harness that differs from its blob at that
  commit, a pairs frame that differs from its pin, and any season > 2024.
- **Origin:** Devin, 2026-10-23 ~20:05 CDT ("do as much advanced math related to incorporating the market rates into the model instead of trying to beat it; you can use literally
  anything to predict this that would have a relationship"). **Advice or momentum: Devin's override.** Claude had recommended leaving MARKET_PRIOR_RESIDUAL (T502) closed; this rule does not
  re-open T502 (a different construction, below) and says in section 2 what it shares with it. Research note: `docs/research/market_anchored_props_2026-10-23.md`.
- **Season labels** are `games.season`: 2023 = 2023-24 (select), 2024 = 2024-25 (report). 2025 = 2025-26 is the frozen holdout and this rule never touches it, loads it, or counts it.
- **Not owned here:** team strength, margins, totals and movers (another agent: `docs/prereg/TEAM_STRENGTH_*`, `MOVER_*`). The only game-level input here is Pinnacle's absolute spread as a market-side covariate.

## 1. Hypothesis, and the question for Devin
- **Question (Devin's words):** use the market's rates inside the forecast instead of trying to beat the market; use anything that has a relationship.
- **Operational question.** On Pinnacle t60 main-line prop legs (the Kalshi "N+" event is the same event as the over at the half-point line), how much of the log-loss gap between the market alone
  and a market-plus-model pool is (a) **re-scaling the price** (mis-calibration of the price proper), (b) **market-side information** (soft-book gap, high lines, blowout risk), (c) **model information**
  given the best market-only forecast? Each is a different component, scored on the same legs by nested arms; (c) is the claim.
- **H1.** C_mod = LL(MM) - LL(MR) < 0: the model's disagreement with the market, weighted per stat and per covariate (bench, heavy depletion, book disagreement), predicts outcomes beyond everything the market side can explain,
  and beats a placebo that keeps every column but the leg-specific model content.
- **H2.** C_mkt = LL(MR) - LL(M) < 0 and the gain survives the model going first (C_mkt_after = LL(MM) - LL(MD) < 0): the price is mis-scaled, or the soft books / line level / spread carry information the price lacks, and the model's
  disagreement does not explain it away.
- **Null.** The price already contains the model's information and is calibrated; MM sits on M and pays a small overfit cost (+0.00016 per leg in the gate's null world).
- **What a result would mean for the product.** A model word other than NONE is the first evidence that a *pool* (not the model alone) improves the displayed probability, and says which
  covariates earn the model its weight. A market word says the displayed price needs a re-scaling. Neither is a bet rule; the standing answer stays "keep your money" unless the model word is
  BET_FLOOR_PASS (prior 0.01) and the forward follow-up confirms it.

## 2. Lineage verdict, stated first (BEST_PRACTICES 2026-10-12; MODEL_PLAYBOOK question 4)
**Distinct from the closed rules in construction and in what each verdict can say; not distinct in model information. The expected result is the ledger's: a small or null model component.**
- **What is not new.** The model's information is production's own `p_model` (A0), so ODDS_HISTORY H2 (T203-T214: w 0.10-0.35, one pass in 16 cells, reb -0.0022 [-0.0035, -0.0009], WOUNDED) and
  MARKET_SHRINK (T415-T419: reb -0.0014 [-0.0024, -0.0005], fg3m -0.0012 [-0.0022, -0.0001]) already measured the largest part of what a pool can gain, on the same two seasons. MM nests both (a stat-specific
  gamma on delta is H2 with free scale). This rule cannot count as independent evidence for either.
- **What is new.** (1) T502 failed because its verdicts could not separate the two explanations (synthetic worlds: market mis-calibration with inert features fired check (b) and PLACEBO_EXPLAINS; the null returned
  FAIL_WORSE_THAN_MARKET; PASS at -0.003 unreachable). Here the explanations live in **different components with guarded credit**: the model word is the increment over the recalibrated market (C_mod = MM - MR); the
  market word is the market-only gain (C_mkt = MR - M) kept only if it survives the model going first (C_mkt_after = MM - MD < 0, otherwise `MODEL_EXPLAINS`), and the gate shows each synthetic world lands in its own word. (2) A market block H2 never had: the soft-book gap `u1`, book
  dispersion (as a weight covariate), high lines, Pinnacle's spread. (3) Per-covariate weights (bench, heavy depletion, dispersion) with a ridge. (4) A three-tier floor ladder instead of one unreachable floor.
  (5) A placebo (K = 50 derangements) on the model terms.
- **Lineage table.**

| ancestor | what it did | numbers (from the ledger) | guards that disqualified it | carried or dropped here |
|---|---|---|---|---|
| ODDS_HISTORY H2, T203-T214 | logit blend of A0 and the market, w fit on 2023, scored 2024 | reb vs Pinnacle -0.0022 [-0.0035, -0.0009] (WOUNDED); pts -0.0004 [-0.0010, +0.0002] | Holm over 16, red team WOUNDED | the model term nests it; family changed to BH over 10; walk-forward monthly instead of one fit |
| MARKET_SHRINK, T415-T419 | DK walk-forward blend + temperature | reb -0.0014, fg3m -0.0012, no cell passes | decision guard, select sign | free gamma replaces w and T; decision guard dropped (this is an information test) |
| T502 MARKET_PRIOR_RESIDUAL | GBM with offset logit p_M on the feature frame | closed at the draft; G9: oracle at w = 0.25 is -0.0011, R recovers 33% | CLOSE_WITHOUT_RUN | trees dropped; synthetic-world separation test (gate G7) is the answer to its defect |
| LINE_CALIBRATION, T493 | market-free logistic map of A0 at the line | CAL-C0 -0.0037 [-0.0047, -0.0027]; CAL 0.6882 vs market 0.6865 | sized shuffles, C0 | the model's own re-scaling is not repeated; pins, 365-day weights, bootstrap code carried |
| GAME_MARKET_CONTEXT, T389-T414, T501 | spread / total into the props model | no cell passes; no positive control | within-date placebo | spread appears only as a market-side covariate (`blowout`); the closed question is not re-asked |
| MARKET_LINE_CLASSIFIER, T320-T324 | GBM on book prob, line, spread, total, forecast | worse than the book in 2023 | 2023 kill | capacity dropped |
| ALT_RUNG_CAL, T494 | rung map | 92% of the gain is the book main line | WOUNDED | Stage 2 (Jeffrey anchoring) is the principled form; descriptive, no pass role |
| Parlay engine gate | `shrink_to_market` linear pool, skill gate on the model alone | n/(n+100) | n/a | not edited; research note section 6 states the defect |

## 3. Arms and constants (fixed; never tuned on a real outcome)
Leg = one Pinnacle t60 main line, two-sided, x.5 line, played player-game (`status == 0`, not `_dnp`), snapshot at least 60 minutes before the real tip, unique per (game, player, stat),
stats pts, reb, ast, fg3m. This is the MARKET_PRIOR_RESIDUAL / LINE_CALIBRATION leg set exactly (gate G1 reproduces its counts). Lines at x.5 only: no push. The Kalshi event "N+" is
Y >= N, the over at line N - 1/2; a player who is inactive or never takes the court is settled at a fair price by Kalshi (rules text, `kalshi_rules/KXNBAPTS`) and voided by the books, so
played-only legs are the matching population.

Notation: `m` = logit of Pinnacle's multiplicative de-vigged P(over) (`p_over_mult`); `a` = logit of production's stored P(over) at the line (`p_model`, clipped to [0.5/199, 1 - 0.5/199]);
`delta = clip(a - m, -3, 3)`; `hi` = line >= {pts 24.5, reb 9.5, ast 6.5, fg3m 2.5}; `u1` = clip(mean over >= 3 two-sided non-Pinnacle books at the same line of logit(m_b) - m, -1, 1) (0 and `miss_u1` = 1 otherwise);
`disp10` = 10 x sd across those books of logit(m_b) (0 if unavailable); `blowout` = |Pinnacle-median spread at t60| / 10 (0 if unavailable); `bench` = `starter10` < 0.5; `tmout` = `n_out_rot` >= 3 (heavy depletion, 31% of legs).
Every column is as-of the same Pinnacle snapshot or production's as-of frame. De-vig is multiplicative; power and Shin are sensitivity only (measured difference 3.5e-5 to 3.7e-5 log loss).

| arm | logit p | free params | role |
|---|---|---|---|
| M | m | 0 | the market alone (comparator) |
| B0 | stat intercept + stat slope on m | 8 | pure re-scaling of the price (C_cal) |
| MR | B0 + {hi, u1, miss_u1, blowout} | 12 | everything the market side alone can explain |
| MD | m + {stat-specific delta (4), delta x bench, delta x tmout, delta x disp10} (offset m, no re-scaling) | 7 | the model credited first |
| MM | MR + the 7 model terms | 19 | the pool |
| PL | MM refit with delta taken from another leg of the same (game_date, stat); mean over K = 50 draws | 19 | placebo for the model terms |

- **Fitting.** Weighted logistic, Newton, ridge `LAM = 50` on every coefficient except the four stat intercepts, centred on the arm's starting point (stat slope 1, all else 0), fixed, not tuned. Fit rows carry weight
  `0.5 ** (age_days / 365)` (LINE_CALIBRATION's half-life). One pooled fit per calendar month on all prior legs of the training seasons (report: 2023 + earlier 2024 months; select: earlier 2023 months), minimum **3,000** prior
  legs (`N_MIN_TRAIN`, MARKET_PRIOR_RESIDUAL's), scored on that month, parameters frozen for it. No leg is ever scored with parameters that saw it.
- **Components (paired per-leg log loss, arm minus arm).** C_cal = B0 - M; C_mktx = MR - B0; C_mkt = MR - M; **C_mod = MM - MR (the claim)**; C_mod_first = MD - M; **C_mkt_after = MM - MD (the market word's leakage guard)**;
  C_pl = MM - mean_k PL_k; C_total = MM - M. C_total = C_mkt + C_mod = C_mod_first + C_mkt_after exactly; the part credited to whichever side goes first (shared variance) is C_mod_first - C_mod = C_mkt - C_mkt_after, reported, assigned to neither word.
- **CRPS against the market is dropped** (one posted point defines no market distribution; MARKET_PRIOR_RESIDUAL section 3). Everything is binary log loss at the line, the same object H2 scored; Brier is secondary.

## 4. The rule
**Primary, on the report season 2024 (walk-forward with 2023 as warm start; 37,528 scored legs).** One decomposition, two words.
- **Binding CI.** Percentile 95% CI of a paired cluster bootstrap, B = 2,000, seed 0 (`research.eval.model_clv_eval.Boot`) under three clusterings: **game**, **player-season**, **ISO week**; the binding CI is the envelope
  [min lower, max upper] (at least as wide as the widest).
- **Model word** (from C_mod and C_pl; evaluated in this order):
  1. C_mod CI upper >= 0 -> `NONE`.
  2. C_pl CI upper >= 0 (the placebo does as well) -> `PLACEBO_EXPLAINS`.
  3. The 2023 select-season C_mod point estimate >= 0 -> `FAIL_SELECT_SIGN`.
  4. C_mod <= -0.003 and C_pl <= -0.003 -> `BET_FLOOR_PASS`. 5. C_mod <= -0.001 and C_pl <= -0.001 -> `INFO`. 6. else `DETECTED_BELOW_INFO_FLOOR`.
- **Market word** (from C_mkt, guarded by C_mkt_after; evaluated in this order): C_mkt CI upper >= 0 -> `NONE`; C_mkt_after point >= 0 (the model terms explain the market-side gain when they go first) -> `MODEL_EXPLAINS`;
  2023 C_mkt point >= 0 -> `FAIL_SELECT_SIGN`; C_mkt <= -0.001 -> `MISCALIBRATION_INFO`; else `MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR`.
- **Override** `WORSE_THAN_MARKET`: C_total mean >= +0.0005 with CI lower > 0 (the gate's null overfit cost is +0.00016, so this fires only on a practical loss).
- **Tiers and what they protect against.** Detection (any size, CI below 0) answers "does the model know anything"; -0.001 is the smallest gain that matters inside a pool (the oracle gain at H2's largest weight w = 0.25 is -0.0011,
  T502 gate G9); -0.003 is the scale of a 7.0% overround (a bettor needs 3.5 pp, about 2 delta^2 = 0.0025). Reporting all three keeps the T502 defect (a single unreachable floor) out.
- **What the tiers can see (gate G7):** C_mod recovers about 55-70% of an oracle gain (oracle 0.0011 -> C_mod -0.0006, model word DETECTED_BELOW_INFO_FLOOR in 9 of 10 replicates; oracle 0.0030 -> -0.0020, INFO 10 of 10). So INFO needs a true oracle gain of roughly
  0.0017 or more and BET_FLOOR_PASS roughly 0.0045 or more (a disagreement coefficient about 1.25 and 2 times the 0.222 that gives H2's largest oracle). The BET tier is, by design, close to unreachable; it is there so that no smaller gain is ever sold as money.
- **Consequence mapping (fixed now, Devin's call to act):** model word INFO or BET_FLOOR_PASS, or DETECTED with the live follow-up passing its gate -> propose to Devin replacing the linear `shrink_to_market` with the fitted logit pool,
  forward-confirmed first. Market word MISCALIBRATION_* -> propose a market-only re-scaling of the displayed price (a correctness item worth about the measured gain, not money). Everything else -> `model_weight` stays 0 and
  "keep your money" stays the answer. **Nothing is promoted by this rule.**

**Secondary (labels only, no pass).** BH at 0.05 over a declared family of **10**: C_mod by stat (4, one-sided "MM beats MR", p = worst case over the decisive clusterings), C_mkt by stat (4, one-sided "MR beats M"), C_mod on the bench slice (1) and on the heavy-depletion
slice (1). Descriptive without a decision weight: C_cal, C_mktx, C_mod_first, C_total, Brier deltas; the fitted coefficients (stat deltas, `delta x bench`, `delta x tmout`, `delta x disp10`, `u1`, `hi`, `blowout`, B0 slopes) by month with the
slope's departure from 1 as the extremising/shrinking diagnostic; slices (n >= 300 else n only): stat, starter vs bench, `min10` < 15 / 15-25 / >= 25, teammate-OUT (`n_out_rot` >= 1 and >= 3), first 15 team games, regular season vs playoffs
(game-id prefix 002 vs 004/005), high vs low line, soft-book count < 6 vs >= 6, tip-hour bucket (the only freshness proxy history has: **labelled a tip-hour slice, not freshness**), month; the share of C_mod carried by the top five
player-seasons; the DraftKings direction check (same arms with DK's own prior, sign only); an exchange-venue slice (novig / prophetx two-sided on Pinnacle's line, 2024-12-19 onward, report-only, no select season).
**Stage 2 (descriptive, no pass role): ladder anchoring.** FanDuel two-sided alternate rungs off Pinnacle's line at t60: venue price V (multiplicative de-vig), Jeffrey-anchored production grid J (S_J(k) = m0 S_A(k) / S_A(k0) for k >= k0,
S_J(k) = 1 - (1 - m0)(1 - S_A(k)) / (1 - S_A(k0)) for k < k0, with m0 Pinnacle's de-vigged P(over) and S_A the stored survival grid), and a re-scaled venue price V_cal (B0-type, walk-forward). Reported: J - V, J - V_cal, rung log loss by distance from the line,
game-clustered; it mixes the venue difference with the shape and cannot support a model claim.

## 5. Multiplicity
- Primary: one decomposition on one season, two words from two contrasts that answer two different questions; no correction is applied between them because neither verdict is "the best of the two".
- Secondary: 10 tests under BH at 0.05. Everything else is descriptive.
- Compared with H2 (16 cells, Holm) this rule tests fewer things; the family is declared before any fit.

## 6. Stages and the 2025 touch
- **Stage 1 (select, 2023).** Expanding month-block fits within 2023; first scored month = first with >= 3,000 prior legs (28,108 of 35,146 legs scored). Select-season conditions: the 2023 point estimate of C_mod (model word)
  and of C_mkt (market word) must be < 0 (signs only: the gate's null rehearsal shows MM costs +0.0004 per leg in 2023 by overfitting alone). **Kill criterion K1:** 2023 C_total point estimate > +0.0010 (2.4 times the null cost) -> stop,
  2024 is not scored, closed (T507 precedent).
- **Stage 2 (report, 2024).** Each month fit on all 2023 legs plus earlier 2024 months, time-weighted; every month scored (37,528 legs).
- **Stage 3 (forward, named, own id, NOT part of this rule's verdict).** `MARKET_ANCHORED_PROPS_LIVE`: the same arms with m = logit of the Kalshi mid (two-sided, spread <= 0.10), coefficients frozen from the 2024 report fit, one rung per
  player-stat-game nearest 0.50, date-clustered; plus the freshness covariate the history cannot supply (minutes between the newest report / lineup the model used and the quote). It is written only if Stage 1's model word is not NONE or the
  market word is MISCALIBRATION_*. Power (synthetic labels on the real 2024 legs, a scratch script not committed; indicative, to be redone inside the Stage 3 gate): with 14 random dates (177 legs per date) the sd of the mean C_mod is 0.00013 under the null and 0.0007 when an oracle gain of 0.0011 is planted (C_mod about -0.0006),
  so 14 dates detect only a C_mod of about -0.0014 or more (an oracle gain of about 0.002); at 60 dates the planted 0.0011 world has sd 0.0003. The follow-up horizon therefore needs the date count its own gate computes, not T510's 14.
- **2025.** Never touched, never loaded. **This rule does not earn a 2025 touch, whatever the result** (recommendation: NO). Both seasons were used by H2, MARKET_SHRINK, LINE_CALIBRATION and T502's gate, so a pass is exploratory.
  The live 2026-27 log is the confirmation. No HOLDOUT_ACCESS_LOG row is drafted.
- **Kill criteria.** (1) The gate fails on data after the capture audit: stop. (2) The planted future, the leg reproduction or the pin check fails in the run: stop, no result. (3) K1 above. (4) After a result, nothing about the rule changes;
  one pre-registered follow-up at most (Stage 3), then closed.

## 7. Known-at
- **What the stored snapshot is.** `odds_history` has exactly one `t60` and one `t5` snapshot per game and book (2023: 5,536,656 / 2,006,441 rows, 1,318 games; 2024: 6,153,536 / 1,369,178 rows, 1,320 / 1,315 games). The t60 request is the 5-minute grid point at or before tip - 60; the measured lead on the Pinnacle legs is
  median 64.37, min 62.55, max 79.37 minutes. The hard filter keeps a leg only if `snapshot_ts <= tipoff_utc - 60 min` with the **real** tip; rows dropped: **0** (both seasons).
- **`last_update` is not usable.** It is later than `snapshot_ts` in 13,598 of 71,272 (19%, 2023) and 25,880 of 76,678 (34%, 2024) Pinnacle t60 prop rows and 0-5 minutes before it otherwise: it is the API's refresh time, not the time the line last moved.
  It enters no column.
- **Information each side holds.** The market quote has everything public at T-64, including news after our report snapshot. Production's report is the latest usable official stamp <= tip - 60 (stored stamps are exactly 11:00 and 17:00 ET in 2023-24; filename-clock lead
  120 / 180 / 300 minutes, p10 / p50 / p90, REV 3 of the timing note: 30 minutes too long). So the market is one to four hours fresher on injuries than `p_model`; a spurious pass from "the model saw later news than the line" is unlikely.
  The `n_out_rot` column is as-of the same stamp; 3.8% / 4.8% of games carry a stamp up to 30 minutes early (BEST_PRACTICES 2026-10-15), which could feed a few legs a report the line did not yet have; the DraftKings direction check and the
  tip-hour slice are the first attack on any pass.
- **No price enters the features** other than the T-64 quotes (Pinnacle for `m`; the same-snapshot soft books for `u1` and `disp10`; the same-snapshot spread). The t5 snapshot is never used.
- **Train / serve skew vs the live Kalshi T-60:** history uses the market 64.4 minutes before tip (nearly constant); live is the latest run with `made_at <= tip - 60`, between about 60 and 100 minutes. Not measured for Kalshi; it matters for Stage 3 only.

## 8. Minimum-data gate, measured on the real data (non-negotiable 8)
Final run `Fri 2026-10-23 20:4x CDT` (clock read by `date`; harness commit `be9d6f2`): `research/eval/market_anchored_props_gate.py` -> `reports/prereg_market_anchored_props/gate.json`; read-only, seasons 2023-24, label columns never selected,
all outcomes synthetic (`default_rng(20261023 + offset + replicate)`), 10 replicate label draws per world, K = 20 placebo draws, B = 1,000 (the run uses K = 50, B = 2,000). Inputs: the pairs frames equal LINE_CALIBRATION's pins (`050dba3e...`,
`560a3808...`, checked); `data/odds/odds_history.duckdb` (`season = s` for s in {2023, 2024} in every SQL).

| check | measured | bar | pass |
|---|---|---|---|
| G1 legs 2023 pooled equals MARKET_PRIOR_RESIDUAL G2 | 35146 | == 35146 | yes |
| G1 per-stat legs 2023 >= 5,000 | {pts: 12189, reb: 6339, ast: 6714, fg3m: 9904} | >= 5000 | yes |
| G1 legs 2024 pooled equals MARKET_PRIOR_RESIDUAL G2 | 37528 | == 37528 | yes |
| G1 per-stat legs 2024 >= 5,000 | {pts: 13021, reb: 6645, ast: 6771, fg3m: 11091} | >= 5000 | yes |
| G2 clusters 2023 (game/player-season/week) | {games: 1249, player_seasons: 377, weeks: 35} | >= 1000 / 200 / 20 | yes |
| G2 clusters 2024 (game/player-season/week) | {games: 1260, player_seasons: 414, weeks: 36} | >= 1000 / 200 / 20 | yes |
| G3 smallest bench / teammate-out cell over stat-seasons | 886 | >= 300 | yes |
| G3 soft-book gap u1 available (min share over stat-seasons) | 0.9325 | >= 0.80 | yes |
| G3 game spread available (min share) | 0.9935 | >= 0.95 | yes |
| G3 hi-line share in [0.10, 0.60] for every stat-season | {2023_pts: 0.146, 2023_reb: 0.246, 2023_ast: 0.251, 2023_fg3m: 0.408, 2024_pts: 0.11, 2024_reb: 0.239, 2024_ast: 0.241, 2024_fg3m: 0.379} | in [0.10, 0.60] | yes |
| G4 share of legs with |a-m| beyond the clip | 0.0001 | <= 0.10 | yes |
| G5 minimum lead of the Pinnacle snapshot before the real tip | 62.55 | >= 60 min | yes |
| G6 planted future: predictions of the test month unchanged when labels on/after it are flipped (MM and MR) | [0, 0] | == 0 | yes |
| G6 flipping labels inside the training window moves them | 0.9094 | > 0.01 | yes |
| G7a null world: false-positive rate of any word (replicates) | [0, 10] | <= 0.20 | yes |
| G7a null world: overfit cost C_total (MM minus M), mean over replicates, 2024 / 2023 | [0.00016, 0.00041] | <= +0.0010 | yes |
| G7b market-only world: market word fires / model word falsely fires (rates) | [1, 0, 0.00101] | fires >= 0.80 and false attribution <= 0.20 | yes |
| G7f model-informed worlds: the market word falsely fires (max rate over the two worlds) | 0 | <= 0.20 | yes |
| G7c planted model information at oracle 0.0011 (H2's largest weight): detection rate; mean C_mod; oracle | [1, -0.00064, 0.0011] | detection >= 0.80 | yes |
| G7d planted model information at oracle 0.0030 (the bet floor): reaches INFO or BET (rate); mean C_mod | [1, -0.00202] | >= 0.80 | yes |
| G7e both planted: both words fire (rate); mean C_mkt, C_mod | [1, -0.00223, -0.00079] | >= 0.80 | yes |
| G8 binding half-width of C_mod under the null (envelope of game / player-season / week) | 9e-05 | <= 0.0010 (a third of the bet floor) | yes |

Synthetic worlds, 10 replicate label draws each, report season 2024, mean over replicates (per-leg log loss; negative = better than the arm named second):

| world | oracle | C_cal | C_mktx | C_mkt | C_mkt_after | C_mod | C_mod_first | C_pl | C_total | 2023 C_total | model words | market words |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W0_null | 0.00000 | +0.00009 | +0.00003 | +0.00012 | +0.00011 | +0.00005 | +0.00005 | -0.00000 | +0.00016 | +0.00041 | {'NONE': 10} | {'NONE': 10} |
| W_mkt_only | 0.00101 | -0.00065 | -0.00012 | -0.00077 | -0.00046 | +0.00007 | -0.00024 | +0.00002 | -0.00070 | -0.00061 | {'NONE': 10} | {'MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR': 9, 'MISCALIBRATION_INFO': 1} |
| W_mod_oracle_0.0011 | 0.00110 | -0.00015 | -0.00004 | -0.00020 | +0.00010 | -0.00064 | -0.00094 | -0.00068 | -0.00084 | -0.00095 | {'DETECTED_BELOW_INFO_FLOOR': 9, 'INFO': 1} | {'MODEL_EXPLAINS': 1, 'NONE': 9} |
| W_mod_oracle_0.0030 | 0.00300 | -0.00054 | -0.00008 | -0.00063 | +0.00013 | -0.00202 | -0.00277 | -0.00205 | -0.00264 | -0.00291 | {'INFO': 10} | {'MODEL_EXPLAINS': 7, 'NONE': 3} |
| W_both | 0.00319 | -0.00181 | -0.00043 | -0.00223 | -0.00054 | -0.00079 | -0.00249 | -0.00084 | -0.00303 | -0.00332 | {'DETECTED_BELOW_INFO_FLOOR': 8, 'INFO': 2} | {'MISCALIBRATION_INFO': 10} |

Binding half-widths under the null world (envelope of the three clusterings, mean over replicates): C_mod 9e-05, C_mkt 0.00015, C_mkt_after 0.00015, C_total 0.00018, C_pl 9e-05.
Planted gammas: {'oracle_0.0011': 0.22238596095762359, 'oracle_0.0030': 0.3688974313288297}. Planted future: {'test_month': '2025-01', 'n_rows': 6552, 'max_abs_diff_future_flip_mm': 0.0, 'max_abs_diff_future_flip_mr': 0.0, 'max_abs_diff_train_flip_mm': 0.9093968885678948}.
Information budget (label-free): sd of delta 0.374 pooled (p01 -1.223, p99 0.663; share clipped 0.00010); an oracle with gamma 0.25 gains per stat-season 2023_pts -0.00173, 2023_reb -0.00190, 2023_ast -0.00160, 2023_fg3m -0.00155, 2024_pts -0.00140, 2024_reb -0.00153, 2024_ast -0.00138, 2024_fg3m -0.00133.
Covariate support (n legs; share u1 available; share spread available; hi share; bench n; heavy-depletion n): 2023_pts: 12189, 0.981, 0.9975, 0.146, 2463, 3485; 2023_reb: 6339, 0.991, 0.9975, 0.246, 964, 1827; 2023_ast: 6714, 0.992, 0.9979, 0.251, 886, 1949; 2023_fg3m: 9904, 0.995, 0.9982, 0.408, 2282, 2729; 2024_pts: 13021, 0.932, 0.9939, 0.110, 3268, 4478; 2024_reb: 6645, 0.941, 0.9935, 0.239, 1200, 2262; 2024_ast: 6771, 0.950, 0.9956, 0.241, 1178, 2154; 2024_fg3m: 11091, 0.964, 0.9961, 0.379, 3176, 3640.
Clusters: {'2023': {'games': 1249, 'player_seasons': 377, 'weeks': 35}, '2024': {'games': 1260, 'player_seasons': 414, 'weeks': 36}}. Lead minutes before the real tip: {'min': 62.55, 'median': 64.36666666666666, 'max': 79.36666666666666}. Pin check: 2023: equal True; 2024: equal True.
Stage 2 counts (FanDuel two-sided t60 alternate rungs off Pinnacle's line, rungs / player-games / games, season-stat): 2023 assists: 7022 / 3944 / 809; 2023 points: 44488 / 6710 / 808; 2023 rebounds: 11515 / 3967 / 822; 2023 threes: 10225 / 5221 / 814; 2024 assists: 14910 / 4447 / 949; 2024 points: 83951 / 8994 / 969; 2024 rebounds: 19938 / 4666 / 967; 2024 threes: 20663 / 6741 / 953.

## 9. Data-capture audit (non-negotiable 8; "gate fails on data only after the audit")
- **Used.** `odds_history` Pinnacle t60 main-line prop legs 2023 and 2024 (35,146 and 37,528 legs after two-sidedness, x.5, played, known-at); the same snapshot's non-Pinnacle two-sided books at Pinnacle's line (u1, disp10) and the game spread;
  production OOF `p_model` and as-of columns from the pairs frames.
- **Exist but unused here, and why.** Pinnacle t5 (later than the forecast, leaks lineup news; MODEL_CLV / MARKET_UPDATE / T510 own it); `last_update` (API refresh clock, section 7); DraftKings / FanDuel alternate rungs (Stage 2, descriptive; DraftKings alternates are over-only,
  2024 under side 802 rows against 383,892 over); the 12 other books as separate weights (not identified on 6k legs per stat); exchange books novig / prophetx (2024-12-19 onward: two-sided on Pinnacle's line novig 20,211 legs / 862 games, prophetx 7,966 legs / 779 games; descriptive slice only, no select season); the other markets (steals, blocks, combos: owned by COMBO_*).
- **Do not exist here.** `odds_history` has 0 rows before 2023 (`season < 2023`: 0); no free source for 2022-23 player props was found (the vendor scout, `docs/research/historical_odds_2026-10-09.md`, says The Odds API props start 2023-05-03 and the only 2022-23 route is a quote-only vendor; **not re-verified today**);
  Kalshi player-prop history starts 2025-12-25 per the same scout (inside the holdout season, not loaded); no intraday report sequence for 2023-24 (stored stamps 11:00 and 17:00 only); no history of line movement before t60; no historical Kalshi quotes outside 2025-26.
- **Repairable, and the cost.** (i) Report freshness: 2023-24 intraday reports would need the NBA CDN's hourly PDFs (a 250-request pilot is drafted at `research/eval/injury_timing_pilot_probe.py`; CDN reachability from the agent shell was not verified); it would date the
  model's information but not the market's, so the freshness covariate would still lack the line's side. (ii) Line movement before t60: a paid Odds API historical pull at extra timestamps (the START 5M plan is already $119 a month; extra snapshots are credits, not a new vendor) -
  cost not estimated here, a decision for Devin. (iii) 2022-23 props: quote-only vendor, price unknown. None changes the power of Stage 1 (the gate passes with margin); (i) and (ii) are what would make the freshness lever testable in history.
- **Era flags.** None: both seasons are full regular seasons plus playoffs; June blocks have few legs.

## 10. What each choice protects against
| choice | protects against |
|---|---|
| nested arms B0 / MR / MD / MM with guarded credit (model after market; market gain kept only if it survives the model going first) | reading a mis-calibration gain as model knowledge, or the reverse (the T502 defect); shared variance between delta and m going to whichever arm is listed first |
| the market block gets every market-side input the model arm gets | crediting the model for what the soft books, the line level or the spread already say |
| PL: derangement within (date, stat), all other columns real | a gain any refit with the same columns would show; a label-shuffle placebo that is worse than M by construction (the shuffled outcome breaks the offset) |
| three-tier floor ladder (detect / -0.001 / -0.003) | one floor no result can reach (T502); announcing a pass that cannot pay a 7.0% overround |
| pooled ridge logistic of 19 parameters, not per-stat 36 or trees | the overfit cost (+0.0005, read as "worse than the market") the first gate draft showed; capacity standing in for information |
| `WORSE_FLOOR` +0.0005 | a verdict of "worse than the market" that is only the overfit cost |
| select sign = sign only; K1 at +0.0010 | masking a small true gain with 2023's data-poor overfit cost (+0.0004) while still stopping a rule that is clearly worse |
| three clusterings, envelope CI | game-only CIs that missed player and week shocks (T500: 5 of 115 players carried 98.6% of a gain) |
| `last_update` and tip-hour not used as freshness | a clock proxy sold as information |
| freshness deferred to Stage 3 on the Kalshi capture | testing the key covariate on data that cannot contain it |
| walk-forward months, 365-day weights, `N_MIN_TRAIN` 3,000 | pooling stale seasons equally; a first month with a few hundred legs dominating |
| no 2025 touch; same-two-seasons disclosure | spending the holdout on a rule whose expected gain is below the floor; reading a second look at 2023-24 as independent |
| positive and negative controls in both directions (gate G7) | freezing an instrument that cannot see a planted signal, or that attributes one explanation's signal to the other |

## 11. Playbook check (docs/MODEL_PLAYBOOK.md; class: linear / logistic pool on a market offset, with a quantile-layer scoring question at the line)
1. **Information or capacity?** The model's information is unchanged; the new information is market-side (soft books, spread, line level) and the covariates that decide when the model's difference counts. Capacity is 19 ridge parameters. The one information lever that
   matters most (report / lineup freshness) cannot be measured on history and is deferred, not simulated.
2. **Enough data for the class?** Yes: 28,108 scored 2023 legs and 37,528 2024 legs for 19 ridge parameters; the unpooled 36-parameter draft was too heavy (+0.0005 null cost) and was cut on synthetic worlds only.
3. **Valid test?** Real tips (min lead 62.55), played legs, x.5 lines, planted future exact, derangement placebo sized from a null (placebo draw sd reported), positive controls in both directions, three clusterings, power measured (binding half-width of C_mod under the null 0.00009 against floors of
   0.001 and 0.003). Weakness: two seasons already used; the stored 2024 `p_model` differs from today's frame on about half the legs (118-row backfill, T493-T496 note); `tmout` = `n_out_rot` >= 3 and the high-line cuts were fixed from leg shares (label-free) before any outcome.
4. **Lineage:** section 2.
5. **What it scores against:** log loss at the Pinnacle line (the T477 / T480 question). It does not score CRPS vs the market (undefined) and does not compute a decision score (BEST_PRACTICES 2026-10-11 applies if a pass becomes a bet rule).
- **Class checks (linear models):** features as-of (every column from the same snapshot or production's as-of frame; planted future above); missingness (`u1` missing 0.5%-6.8% by stat-season, spread missing 0.2%-0.7%, a flag for u1, none outcome-dependent: availability is set by book coverage before tip);
  era flags (none: no column is constant across a date; `blowout`, `disp10`, `u1` vary within date); duplicated signals (MR vs MM isolates delta; MD vs MM isolates the market block).

## 12. Hypothesis priors (PREDICTIONS_LEDGER rows; appended to docs/PREDICTIONS_LEDGER.md in this branch)
Written Fri 2026-10-23 before any real-outcome contrast; blind to every real C_* number (the only outcome-like numbers seen are the gate's synthetic worlds and Devin's exploratory live week, which is not used to set a constant).
No prior was stated for the gate before it ran (not scorable): the first draft failed six checks and the design below passes 22 of 22 after the synthetic-only revisions of section 13. P(model word = BET_FLOOR_PASS) 0.01; INFO 0.04; DETECTED_BELOW_INFO_FLOOR 0.20; PLACEBO_EXPLAINS 0.08; FAIL_SELECT_SIGN 0.06; NONE 0.61. P(market word = MISCALIBRATION_INFO) 0.10;
MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR 0.30; MODEL_EXPLAINS 0.04; FAIL_SELECT_SIGN 0.05; NONE 0.51. P(override WORSE_THAN_MARKET) 0.04. P(K1 fires) 0.05. Expected 2024 pooled C_mod -0.0002 (80% -0.0010 to +0.0003); C_mkt -0.0005 (80% -0.0015 to +0.0001);
C_total -0.0006 (80% -0.0018 to +0.0002). P(reb or fg3m is the stat with the largest |C_mod|) 0.55 (H2's fitted weights were highest there). P(`delta x bench` coefficient negative in 2024, i.e. the model should count less on bench legs) 0.60.
P(Devin hears "keep your money" from this rule) 0.97.

## 13. Data exposure (honest)
- **Seen:** every ledger number cited; MARKET_PRIOR_RESIDUAL's gate (leg counts, overround, G9); the label-free shapes of every column; Devin's 2026-10-23 live-week reading (slope 0.46, under-lean 59.4%, bench minutes -4.7, p_play 0.72 -> 0.41, assists Brier) which is **why `delta x bench` and `delta x tmout` are in the
  model** and why the market block has `blowout`; that is a design influence, disclosed, with no constant set from it.
- **Design iterations before this text (all on synthetic worlds, no real outcome):** the first gate draft fitted 36 parameters per stat (six replicates): null-world cost C_total +0.00052 (2024) and +0.00112 (2023), so `WORSE_THAN_MARKET` fired in the null; the market-only
  world was too weak to detect (oracle 0.00063). Changes: pooled fit with stat-specific intercept / market slope / delta (19 parameters), ridge `LAM` chosen from {1, 20, 100, 400} by null cost and planted-world recovery on synthetic labels (50 as a round midpoint, not an optimum), the market-only
  world strengthened to oracle 0.0010, the `tmout` threshold moved from >= 1 (83% of legs) to >= 3 (31%), the fg3m high-line cut from 3.5 (10%) to 2.5 (38-41%), the market word first read from MR - M (the planted model world leaked into it in 7 of 10 replicates), then from MM - MD alone (detected the market-only world in only 6 of 10 replicates at oracle 0.0010: too conservative), and finally C_mkt kept only if
  C_mkt_after < 0 (the guard blocks the model-world leak); `WORSE_FLOOR` introduced after the null world fired the override in 6 of 10 replicates. None of these used a real label.
- **Not seen:** any M, B0, MR, MD, MM or PL number on a real outcome; any disagreement coefficient on real labels; any per-slice contrast; season 2025.

## 14. Unstated details fixed before any scored number
- **Months:** calendar month of `game_date`; fit rows strictly earlier; weights `0.5 ** (age_days / 365)`, age measured from the first day of the scored month; `time_weights` must raise on age <= 0.
- **Clipping:** the market probability is clipped at 1e-6 only to take a logit; `delta` clipped to [-3, 3] (affects 0.01% of legs); `u1` to [-1, 1]; log loss uses the logit form (`logaddexp`), unclipped.
- **`u1` / `disp10`:** soft books = every book except Pinnacle with both sides quoted at exactly Pinnacle's line at t60; at least 3 required; mean and sample sd of the de-vigged logit `ln(r_o / r_u)`.
- **`blowout`:** the median of |home point| over all books' `spreads` rows at t60 for the game.
- **Derangements:** group (game_date, stat); a cyclic shift of a random order of the group; singleton groups pooled per stat; draw k uses `default_rng(1000 + k)`.
- **Newton:** up to 40 steps, tolerance 1e-8 on the step, a 1e-9 ridge on the Hessian diagonal, start at the arm's starting point.
- **Synthetic worlds (gate):** null: y ~ Bernoulli(sigma(m)); market-only: eta = -0.06 + 0.85 m + 0.40 u1 - 0.08 hi (oracle 0.0010); model: eta = m + gamma delta with gamma 0.2224 (oracle 0.0011) and 0.3689 (oracle 0.0030) from bisection on the 2024 legs; both: market-only eta plus 0.2224 delta.
- **ISO week:** `iso_year-week` of `game_date`.
- **Concurrency:** the run takes `data/ops/heavy.lock` (mkdir, pid in `owner`, retry every 120 s, removed only if the pid is ours). The fits are small (about 12 months x 2 seasons x (4 + 50 placebo) fits, seconds); the bootstraps are the cost.
- **CI module:** `nba/truth/bootstrap.py` does not exist at this base; the harness uses `research.eval.model_clv_eval.Boot` (the LINE_CALIBRATION / MARKET_PRIOR_RESIDUAL precedent), pinned with the other harness files.

## 15. Commands (maintainer)
```
G="-m research.eval.market_anchored_props_gate"
.venv/bin/python -W ignore $G --k-null 20 --boot 1000 --reps 10      # re-measure the gate (about 3 minutes, one process, ~1 GB)
M="-m research.eval.market_anchored_props_eval"                       # NOT YET WRITTEN: after the freeze
.venv/bin/python -W ignore $M run --sha <64 hex>                       # after `prereg(MARKET_ANCHORED_PROPS): <sha>` is committed
```
- Dry-run count (what `run` fits and scores): 35,146 (2023; 28,108 scored) and 37,528 (2024) Pinnacle legs; per scored month 4 fits (B0, MR, MD, MM) plus K = 50 placebo refits = 54 small logistic fits (<= 19 columns, <= 72,674 rows);
  7 scored months in 2023 (Oct and Nov are burn-in) and 9 in 2024, so about 16 x 54 = 864 fits; bootstraps 2,000 x 3 clusterings per contrast (about 14 contrasts). Estimated 5-10 minutes on CPU (the gate's 50 replicate worlds with K = 20 took about 125 s).
- Outputs: `reports/prereg_market_anchored_props/results.json`; ledger rows are drafted by the main session from it.

=== RESULTS BELOW ===
