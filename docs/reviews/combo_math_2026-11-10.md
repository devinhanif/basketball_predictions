# Adversary review: combo mode math and evidence (agent/market-combo-math, 766fe15), 2026-11-10

Scope: `nba/parlay/combo.py`, `combo_build.py`, `combo_eval.py` (pre-stated rule fd81aaf, amended 4fa66a1), `combo_params_v1.json`,
`tests/parlay/test_combo.py` + golden vectors, `docs/contracts/combo_export_v1.md`, the MODEL_PLAYBOOK entry,
`reports/parlay/combo_eval_2026-11-09.json`. Seasons 2023-24 only; season 2025 was not loaded by any attack (every load went through
`combo_eval._refuse`, `nba.duckdb` opened `read_only=True` by the module's own `games_table`).

Classification: **WOUNDED**. The same-player lift is real and survives every attack. The `grind = calibrated` label does not stand: the
amendment that produced it was decided after the first run had failed all four scripts, on a diagnostic computed from test-season
outcomes, and it flipped grind from exploratory to calibrated; the pass is also fragile (fails under 1 of 5 alternate seeds and when
either of two months is dropped). The target-probability search has a real bug: it reports `feasible: false` when feasible combos exist
and does not return the max-lift answer for k = 2.

Verdict: BLOCKED

(Blocked for merge until findings B1 and M1 are fixed; everything else can follow.)

## Top finding

The script labels changed between runs because of the amendment. I re-ran the first-run code (fd81aaf) on the same data: **grind was
exploratory in run 1** (three failed conditions: fold A same-player joint ratio CI [1.012, 1.098] excludes 1; fold B
calibration-in-the-large CI [+0.0119, +0.0280] outside +-0.02; fold B ratio CI [1.070, 1.154] excludes 1). After the band, grind
passes. The contract says the first-run statuses are "kept only in git history"; they are not in git (no JSON was committed at fd81aaf
or 4fa66a1, and the first-run file was overwritten by the second run). This review's reproduction is the only record.

## Playbook check

- Model class: set / joint model (Gaussian copula over line-calibrated integer marginals, plus a per-(script, stat, role) latent shift and
  scale).
- Information or capacity? No new pre-tip information. It exposes structure (shared minutes for one player's stats). Fine for an
  explanatory mode; nothing here is a skill claim against a market, and the contract says so.
- Enough data for this class? Yes for the same-player correlations (about 27,600 player-games per season, 69,000 pairs per fold). Not
  for script calibration claims: 291 to 444 games per script per fold, and criterion (iii) has a CI half-width of about 0.04, so it can
  only detect a joint miscalibration larger than about 4%.
- Test valid? Partly. Shuffled labels and the train-only fits are sound. Invalid as a confirmatory test of the scripts: (a) the rule
  was changed after a result on the same test data; (b) criterion (iii) "CI contains 1" is a non-rejection test, so it is easier to
  pass with less data; (c) fold B is cross-fit, so "both folds" is one pair of seasons used twice with roles swapped, not two
  independent confirmations.
- Lineage and inherited guards: `joint_eval.py` copula, T081 (JOINT set transformer not kept), T490 (independence for different
  players). The holdout refusal is inherited and tested (`test_eval_refuses_season_2025_by_name`). No ledger row and no PREDICTIONS
  row exist for this run (see m5).
- Scored against: Brier / log loss of the joint event at DraftKings T-60 posted x.5 main lines, against independence with the same
  calibrated marginals. Same support on both arms (integer events), so no support artifact.
- Class checks: "is the effect large enough to ever be detected?" Yes for same-player lift (CIs about +-0.025 around 1.18). No for the
  script conditional-joint criterion at the 1 to 4% level.

## Per-check table

All attack scripts are in the scratchpad, never in `nba/`:
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/adv/attack.py`,
`.../adv/math_attack.py`, `.../adv/miss.py`; the code snapshots are `git archive fd81aaf nba` (run1) and `git archive 766fe15 nba`
(run2) under `.../scratchpad/run1` and `.../scratchpad/run2`. Commands were run from the repo root with `.venv-1/bin/python`.

| # | check | command | n | result (95% game-clustered CI) | pass/fail |
|---|---|---|---|---|---|
| R0 | Reproduce committed result | run2 `combo_eval.main([...--out run2/out.json])`, exit 0, 44 s | 2 folds | `script_labels` identical to the committed JSON; SP fold A 1.1971 [1.1739, 1.2217] identical | pass |
| R1 | Reproduce first run | run1 (fd81aaf) same args, exit 0, 37 s | 2 folds | all four exploratory; grind fails 3 conditions (above); blowout, close, shootout Brier CI upper > 0 in both folds | **finding B1** |
| 1 | Shuffled labels: script flags permuted within season, game level, train and test, 3 reps | `attack.py` section 3 | grind 291-295 / 409-410 games | grind dBrier A +0.00036 [+0.00005, +0.00065], +0.00018 [-0.00002, +0.00038], +0.00064 [+0.00025, +0.00105]; B +0.00036, +0.00069, +0.00014; all labels exploratory in 3 of 3 reps (true labels: -0.00351 [-0.00436, -0.00266] A, -0.00463 [-0.00568, -0.00356] B) | pass (the script effect is not a leak) |
| 2 | Planted future / as-of | code read: `fit_fold_map(train)` uses the train season block, `fit_script_params(seasons_data[train])`, `estimate_game_corr(res_train)`; `mu_t` intercept made train-only (`attack.py` section 4) | 247 / 467 grind games | mean(total - mu_t): 2022 +0.33, 2023 -2.43, 2024 +2.12. With the train-only intercept, grind still passes each fold: A obs-pred -0.0064 [-0.0158, +0.0029], ratio 0.961 [0.918, 1.002]; B +0.0059 [-0.0012, +0.0130], ratio 1.026 [0.993, 1.061] | pass (in-sample `mu_t` does not drive the label). No row-level planted-future injection was run (scripts are realised outcomes by design) |
| 3 | Missingness | `miss.py`: DK x.5 lines joined to OOF model rows | 53,247 (2023), 53,752 (2024) lines | 0 lines without a model row in either season, all four stats | pass, with a caveat: the pairs file already excludes DNPs upstream, so the DNP rate among posted-line players is never seen here; DNP-as-miss is untested against outcomes |
| 4 | Knockout / band sensitivity: `Z_BAND` in {0.5, 0.75, 1.0, 1.25, 1.5, 2, 3}; run 1 is `Z_BAND` = inf | `attack.py` section 1 | same | grind passes at 0.5-1.5, fails at 2.0 (B ratio [1.023, 1.102]), 3.0, inf. PIT sd (pts/reb/ast/fg3m, 2024): 0.5 -> 0.96/0.99/1.03/1.04; 1.0 -> 0.78/0.85/0.88/0.98; 3.0 -> 0.62/0.69/0.73/0.91 | mixed: 1.0 is not a knife edge inside [0.5, 1.5], but the label exists only because the band was added after run 1 |
| 5a | Replication: 5 alternate PIT-jitter + bootstrap seeds | `attack.py` section 2 | same | grind calibrated in 4 of 5; seed 5 fails on fold A ratio 0.959 [0.923, 0.999] | **fragile** |
| 5b | Replication: drop each test month (18) | `attack.py` section 6 | same | grind flips to exploratory when 2024-10 or 2024-11 is dropped (16 of 18 keep the label) | **fragile** |
| 5c | Replication: fold swap | committed JSON | 2 folds | SP lift 1.197 [1.174, 1.222] A, 1.179 [1.155, 1.203] B; residual r +0.172 [+0.163, +0.182], +0.174 [+0.165, +0.183] | pass |
| 6 | Integer support | code read + tests `test_over_half_line_is_survival_at_next_integer`, `test_whole_number_lines_are_refused` | n/a | both arms score the same integer event `Y >= x + 1` with the same calibrated marginals; no support mismatch | pass (minor m3 for game legs) |
| M | Math | `math_attack.py`, `pytest tests/parlay/test_combo.py` | see below | see findings | mixed |

## Findings (severity, exact line, exact fix)

### B1 (blocker): `grind = calibrated` is a post-result label
- Evidence: rule fd81aaf committed 21:06:46; amendment 4fa66a1 at 21:10:18 after run 1; run 1 (reproduced) labels all four exploratory;
  run 2 labels grind calibrated. The amendment was triggered by a PIT diagnostic computed on 2024, the fold A test season, using its
  outcomes. Thresholds did not change; the procedure that produces the scored probabilities did. Non-negotiable 4: "Nothing about a rule
  changes after a result is seen; a new question gets a new rule." The rule text in the `combo_eval.py` docstring itself changed
  between fd81aaf and 766fe15 (the AMENDMENT paragraph), so the rule file diff is not empty.
- Classification: the band is a legitimate bug fix in substance (it is justified by where main lines sit, a property of inputs, and the
  label holds for any band in [0.5, 1.5]). It is post-result tuning in procedure (decided after a failing result, on test-season
  outcomes, and it changed a label). A legitimate bug fix still spends the test data: the re-run is a second look, not a pre-registered
  pass. Add the fragility (5a, 5b) and criterion (iii)'s non-rejection logic, and the label is not earned.
- Fix: `nba/parlay/combo_params_v1.json` `script_status.grind` -> `{"status": "exploratory", "why_not": ["passed only after a
  post-result amendment (4fa66a1) on the same test seasons; fragile to seed and month; confirm under a new frozen rule"]}`; same change
  in `docs/contracts/combo_export_v1.md` section 7 table row "grind" and in the MODEL_PLAYBOOK entry ("`grind` passes the pre-stated
  check" -> "no script is calibrated; grind passed a post-amendment re-run only"); `script_label` in `combo_eval.py` stays as is (it
  is the frozen rule), the override is recorded in the params `note`. Confirmation: a new rule id, frozen before any look, on the 2026
  forward log (`season = 2026` games, once >= 20 games and a minimum of about 150 grind games for a +-0.04 ratio CI). Replace criterion
  (iii) in the new rule with an equivalence test (ratio CI inside [0.95, 1.05]).
- Also fix the contract sentence "kept only in git history" (section 7, last paragraph): commit the run-1 JSON (this review can supply
  it: `.../scratchpad/run1/out.json`) under `reports/parlay/combo_eval_2026-11-09_run1.json`, or say it was overwritten.

### M1 (major, blocks merge): `target_search` reports infeasible targets that are feasible and misses the max-lift answer
- Evidence (fixture `tests/parlay/combo_fixture.py`, k = 2, brute force over all 52 candidate legs):
  - target 0.55: brute force finds 146 feasible pairs (best p 0.572, lift 1.120); `target_search` returns `feasible: False`.
  - target 0.15: brute-force max lift 1.628 (legs p 0.371, 0.353); `target_search` returns lift 1.535 as the top answer.
  - Cause, `nba/parlay/combo.py` `target_search` (the loop `ranked = sorted(nxt.values(), key=lambda t: (-t[1], -t[2], str(t[0])))`):
    after leg 1 every partial has lift 1.0, so the beam keeps the 40 highest-p single legs, and partials are never pruned by
    reachability, although the docstring says "ranked by lift among partial combos that could still reach the target".
- Fix: (a) for k = 2 enumerate all pairs (52 legs -> 1,326 pairs, about 1 s at the full budget for k = 2 since it is exact); (b) for
  k >= 3 prune any partial with `p_joint < target` (adding an over leg can only lower the joint, so it is an exact bound) and rank
  partials by lift only among the survivors; (c) add a test that compares `target_search` with brute force for k = 2 at targets 0.15,
  0.35, 0.55 on the fixture, including the 0.55 feasibility case.
- Second-order: the max-lift objective pins one leg at the band floor (all top-5 answers at target 0.15 contain a leg at p 0.353). The
  band moved the degenerate answer from "near-certain filler" to "the 0.35 edge". State this in the contract and consider ranking by lift
  only after `p_joint - target` is small.

### M2 (major): legs the app will show at 0.70-0.85 are not calibrated
- Evidence (`attack.py` section 5, by CALIBRATED band): fold B cal 0.75-0.80: n 67, observed minus predicted -0.126 [-0.248, -0.013];
  cal 0.70-0.75: -0.063 [-0.133, +0.007] (n 188), fold A -0.047 [-0.160, +0.065] (n 68); cal >= 0.80: n 0 (A), 26 (B). The raw >= 0.80
  legs (the contract's -0.161, n 85) have calibrated values of median 0.66 (B) and 0.71 (A), max 0.89; so a cap keyed on calibrated
  >= 0.80 would miss most of them.
- Fix: `combo.py` `TARGET_P_MAX = 0.85` -> `0.75`; in the contract section 8 add: any leg with calibrated P > 0.75 OR `raw_at_main`
  >= 0.80 is shown with "above the tested range, likely too high" and is excluded from target search. Do not hard-cap the number (a
  cap invents a probability); label and exclude.

### M3 (major, documentation): the 1.197 headline mixes dependence with a marginal offset
- Evidence: fold A cross-game pairs (independent by construction, residual r +0.002) show realised / calibrated product 1.028 [1.006,
  1.051], so 2024 legs hit about 3% more often than the 2023-fitted map says. Dividing out that offset: SP lift about 1.164 (A) and
  1.198 (B; XG 0.984). The copula's predicted lift is 1.169 / 1.171 and realised / copula is 1.024 [1.004, 1.045] (A), 1.007 [0.987,
  1.027] (B).
- Fix: in the contract section 7 and the playbook entry, report the same-player lift as "about 1.17 to 1.20 (1.16 to 1.20 after removing
  the season-level marginal offset seen in cross-game pairs)"; keep the claim, drop the precision.

### m1 (minor): contract says the first run's pair numbers carry over unchanged
- Evidence: run 1 SP fold A 1.1960 [1.1730, 1.2205] vs run 2 1.1971 [1.1739, 1.2217]; main-line legs above `|z| = 1` are moved by the band
  and the bootstrap stream shifted when `resid_r` was added. Fix: "unchanged to the third decimal".

### m2 (minor): `p_joint_incl_dnp` when `p_play` is null
- `combo.py` `_p_all_play`: `p *= 1.0 if pp is None else float(pp)` makes `p_joint_incl_dnp = p_joint` for unknown availability, which
  reads as "includes DNP risk" when it does not. Fix: return `None` for `p_all_play` and `p_joint_incl_dnp` when any named player's
  `p_play` is null, and add a test.

### m3 (minor): game legs have no integer correction
- `combo.py` `merge_legs`, `team_margin` and `total` branches: `P(M > gt)` and `P(T > x)` on a continuous normal; `script_prior` does
  apply the half-point correction. Whole `gt` / whole totals are off by half a point. Fix: refuse whole numbers (as for player legs) or
  apply `gt + 0.5` / `x + 0.5` for whole values.

### m4 (minor): `script_prior` constant
- Contract: shootout and grind priors are "about 0.27" for every game; realised rates were 22.1% / 28.6% (shootout) and 31.1% / 22.7%
  (grind) because `mean(total - mu_t)` swings -2.4 to +2.1 between seasons. Fix: say "the realised share has ranged from 22% to 31%
  by season" next to the prior.

### m5 (minor, process): no ledger or predictions row; rule only in the docstring
- BEST_PRACTICES line 34 asks for the rule "in the module docstring plus a doc"; there is no `docs/prereg/` file, no TEST_LEDGER row
  and no PREDICTIONS_LEDGER row. Drafted rows below.

### m6 (minor): slow tests
- `tests/parlay/test_combo.py`: 32 passed in 124 s (stdlib QMC). Fine locally; mark the QMC-heavy tests or lower their budgets so the
  file stays under 30 s.

## Math checks (question 2)

| property | evidence | pass/fail |
|---|---|---|
| joint <= min marginal; Frechet lower bound | clamp in `evaluate_game_legs`; 0 violations in 400 fixture triples; `test_joint_is_bounded_by_min_marginal_and_frechet` | pass |
| 1 leg = marginal | `test_single_leg_reduces_to_marginal_exactly` | pass |
| exact bivariate for k = 2 | `test_exact_bivariate_matches_scipy`, `test_vectorised_bivariate_matches_reference` | pass |
| QMC error, k >= 3 | 12 random rectangles (k = 3 and 5) vs `scipy.stats.multivariate_normal.cdf`: max abs error 2.9e-5, max error / reported se 1.41; screening budget (150 x 4) max abs error 2.2e-4 | pass |
| integer support, over x.5 = P(Y >= x + 1) | `line_to_bounds`, tests | pass (game legs: m3) |
| same-player multi-stat | same stat merged comonotone (`merge_legs`); different stats via pooled rho (pts-fg3m 0.488, pts-reb 0.299); copula slightly under-predicts SP joints in fold A (realised / copula 1.024 [1.004, 1.045]), consistent with jitter-attenuated rho | pass, with a small known bias |
| PSD | shipped params, 10 players x 4 stats + margin + total (42 x 42): min eigenvalue 0.448; same-player 4 x 4 block min 0.479; repair path tested | pass |
| DNP as miss stated | contract section 2 and `test_dnp_is_reported_separately_and_counts_as_a_miss`; untested against outcomes (no DNP rows in the eval) | pass as stated; m2 |
| golden vectors reproduce | `test_golden_vectors_reproduce` passes | pass |
| target objective cannot degenerate | band prevents filler legs, but the search is wrong (M1) and the optimum sits on the band floor | **fail (M1)** |

## Answers to the six questions

1. Rule change: legitimate bug fix in substance, post-result change in procedure; it flipped grind. The label must not stand (B1).
2. Math: correct except the target search (M1) and two minor edge semantics (m2, m3).
3. Leakage: none found. Correlations, map and script parameters are train-season only. In-sample `mu_t` moves the season mean residual
   by up to 2.4 points but does not change the grind outcome when replaced by a train-only intercept. Fold B (train 2024, test 2023)
   counts as a stability check and a sign replication, not as forward evidence.
4. Six checks: shuffled labels kill the script gain (pass); planted future n/a by design, as-of verified by code read; missingness 0 of
   107,000 lines, DNPs excluded upstream; band knockout shows the label exists only for bands 0.5-1.5; replication fragile for grind
   (seed, month), solid for SP lift; integer support pass.
5. Above 0.80: yes, the app needs a rule, but keyed on calibrated > 0.75 or raw >= 0.80, not calibrated >= 0.80 (M2). Label and exclude
   from target search; do not cap the number.
6. Triples: label every k >= 3 joint "model estimate, not checked against results" and show `+- 2 se`. Never "calibrated". A cheap
   follow-up rule: same-player triples (pts, reb, ast at main lines) on 2023-24 OOF, realised / copula ratio with a game-clustered CI,
   frozen before running.

## Corrected family and power

- Family actually run for script labels: 4 scripts x 3 criteria x 2 folds x 2 runs (run 1 and run 2), plus the band choice. Labels are
  conjunctive, so BH does not apply per label, but two looks at the same test data double the chance of a false pass. After correction
  for the second look, no script label survives as confirmatory.
- Pair-class lift family (5 classes x 2 folds): the SP result survives any correction (CIs exclude 1 by more than 15 points); TM, OP, XG
  are nulls (residual r within +-0.01).
- Power: grind same-player joint ratio CI half-width about 0.04 at 15,000 to 21,500 pairs / 291 to 410 games; the smallest detectable
  joint miscalibration is about 4%. Calibration-in-the-large CI half-width about 0.008 to 0.009.

## Single most likely failure mode if the claims are wrong

The app labels grind "calibrated" and the grind joint is in fact about 4% miscalibrated (or worse in a different season level): the
criterion that passed it is a non-rejection test that could not have seen it, and the pass came from a re-run on the same seasons.

## Not verified

- No planted-future row injection into the pipeline (scripts are realised outcomes; as-of was checked by code read only).
- The browser TS port (not in this branch).
- Whether `game_rows_{s}.parquet` `p` (used for `mu_m`, own-margin correlations and blowout / close priors) is out-of-fold.
- Triples against outcomes (not run).
- DNP-as-miss against outcomes (no DNP rows in the eval data).
- Knockout of the script role split (starter / bench) and of `sigma` vs `mu`; the script sigmas (about 0.79 for pts) mostly mirror the
  unconditional PIT sd (0.76-0.78), so part of the script shift is a correction of the base latent, not a script effect. The shuffle test
  shows the gain does not survive without true labels, so this is a size question, not a leak.

## Recommended changes (not applied)

1. B1: grind -> exploratory in params, contract and playbook; commit the run-1 JSON or correct the sentence.
2. M1: exhaustive k = 2, reachability pruning for k >= 3, brute-force test.
3. M2: `TARGET_P_MAX` 0.75; "above the tested range" label for calibrated > 0.75 or raw >= 0.80.
4. M3: restate the SP lift with the marginal offset removed.
5. m1-m6 as listed.
6. New rule id for a forward script-calibration test (2026 games), with an equivalence criterion for the joint ratio.

## Drafted rows (not appended)

TEST_LEDGER (next free id, main session assigns):
`| T??? | 2026-11-09 | COMBO_MODE (combo_eval fd81aaf, amended 4fa66a1 after run 1) | same-player 2-leg lift, realised / calibrated product, DK T-60 main x.5 overs, train 2023 -> test 2024 (walk-forward); 2024 -> 2023 cross-fit | 1.197 (A), 1.179 (B) | 1.174 / 1.155 | 1.222 / 1.203 | game, 2,000 | n/a | descriptive; same-player lift kept; scripts: all four exploratory (run 1 all exploratory; grind passed only in run 2 after a post-result amendment; adversary combo_math_2026-11-10 WOUNDED) | not holdout (2023-24) |`

DECISIONS:
`| 2026-11-10 | Combo mode script labels: all four exploratory; grind's run-2 pass is not confirmatory | the band amendment was decided after run 1 on test-season outcomes and flipped grind; pass fragile to seed and month | a new frozen rule passing on 2026 forward games |`

PREDICTIONS_LEDGER (to score when a forward script rule runs): adversary prior P(grind passes a new frozen equivalence rule on >= 150
grind games of 2026) 0.45, because the point estimates are near 1 in both folds but the 2026 scoring level differs.
