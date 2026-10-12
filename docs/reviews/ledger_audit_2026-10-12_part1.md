# Ledger audit, part 1 (T001-T149, with the T150-T159 rows that belong to the same experiments), 2026-10-12

Read-only audit. No fit, no season-2025 outcome query, `nba.duckdb` not opened. Two descriptive recomputations used only stored
2023/2024 component arrays (`reports/lower_tail/components_*.npz`); scripts live in the session scratchpad, not in the repo. Ask: where did a
"failed" or "tied" result hide a RECENT_ROLE-shaped finding (a component missing information a sibling component already has)?
Patterns P1-P7 are the ones in the request. Priors are mine, stated before any new test exists, and are priors that the candidate is real
AND clears its own floor, not that it moves money (the market beats us on every props cell, T203-T237, so even a real candidate here is a
model-quality lever first).

## What the slice contains (so the table can be read)

T001-T061 are the 2026-10-08 back-fill: sim vs season-average, routing, matchup, minutes 1A. Mostly row-level or point-only, all on pre-clean-up
possessions and a DNP-contaminated baseline. T062-T064 is the fair rematch. T065-T149 are the real experiments: SEQ (T065-72), CTXRES V2/V3
and its red team (T073-80, T102-118), JOINT, PBP_GPT, LINEUPS_KNOWN (T085-90, T096-97), REALTIP (T091-95, T128), RIDGE_V2, TRACKING,
INTEGER_QUANTILES, PTS_TAIL, LOWER_TAIL (T136-159).

## Candidates

| # | Pattern | T rows | Evidence (n, CI) | Mechanism | Cheapest honest next test | Venue | Prior real |
|---|---|---|---|---|---|---|---|
| C1 | **P1 + P5 + P7**: the short-minutes model `pi` lacks the recent-role columns the P(play) fix just proved | T136-T159 (LOWER_TAIL); links T440, T442, T452, T455-T458 | `pi` is fit on `COMMON_FEATURES` only (`nba/props/context_residual.py` lines 690-721; `_fit_lower_tail` passes exactly that list). It holds `min5/min10/min40` but no last-game minutes, and the WORKLOAD LOAD columns went into the stat head, not into `pi`. pts mixture passes both seasons: -0.0126 [-0.0171,-0.0080] (2023, n=27,619) and -0.0107 [-0.0150,-0.0064] (2024, n=27,701), reproduced from the stored arrays (T136, T148; ledger -0.0109 at n=27,583 before the clean-up, so the clean-up did not move it). **Oracle gap (my recomputation, descriptive):** replacing `pi` with the realised short flag gives -0.1626 [-0.1742,-0.1506] / -0.1564 [-0.1674,-0.1454]; the real `pi` (AUC 0.855, mean 0.091 vs observed 0.104) captures about 8% of it. Same shape for reb (-0.0646/-0.0638 oracle vs -0.0046/-0.0028), ast (-0.0285/-0.0252 vs -0.0039/-0.0017), fg3m (-0.0168/-0.0140 vs -0.0028/-0.0021). Four other results point at the same hole: LOAD pts gain is 60-63% on realised minutes < 10 (T452 review); SEQ blends pass the floor on pts but fail the minutes < 5 slice, +0.055 / +0.089 (T442); the E1 minutes-gated router has an oracle of -0.0191 / -0.0136 against -0.0053 / -0.0012 realised (T440); the P(play) model lacked last-game minutes until T455-T458 (-0.0205 on 2025 for P(play)). The `short` label is "minutes < 0.5 x min10", i.e. relative to a stale projection, so a player whose role just collapsed is exactly the row it cannot see. | A bench player who got 2 minutes last night is a short game or a DNP tonight; `min10` still says 18. Same carrier as RECENT_ROLE, applied to the model that sets the lower tail. | Refit `pi` only (11 s per pass, `collect_pi`) with the RECENT_ROLE five columns appended; metrics: short-flag log loss (primary for the component), then pts integer CRPS vs A0 = production + mixture, m=4 stats, floor -0.005, select 2023 / report 2024, game-clustered, with RECENT_ROLE's non-inert shuffle and past-only placebo. Stored `components_*.npz` make the CRPS step minutes of CPU. Needs a draft prereg first (modeler). The LOWER_TAIL doc itself names a calibrated/richer `pi` as the follow-up that "needs its own pre-registration"; none has been spent. | 2023/2024 for the rule. If it passes: this is a never-evaluated model, so one 2025 touch is available (the lower-tail mixture has no holdout row; the 2025 replay shadow deltas are SEALED and must stay unread). Live: the `_lt` arm is pts only and cannot look before 120 dates. | `pi` log loss improves with CI excluding 0 in both seasons: 0.65. pts CRPS beats A0 by -0.005 in both seasons: 0.15. reb/ast/fg3m reach the floor: 0.04 each. |
| C2 | **P1 + P6**: the T-30 lineup gain was measured against comparators that lack recent-role minutes | T085-T090, T096-T097 | P(play) T-30 minus T-60 (+OUT report, "fair"): -0.0376 all rows, -0.0176 [-0.021,-0.014] bench-tonight (T096-97, 2024, n = 32,642 / 19,578 before the red-team rebuild). The T-60 comparator is "recalibrated production P(play)", the model whose lack of last-game minutes cost -0.0094 [-0.0113,-0.0074] / -0.0120 [-0.0137,-0.0102] (T457) and -0.0205 on 2025 (T458). The bench is where both gains live (RECENT_ROLE: bench carries 86-91%). So part of the "T-30 is the lever" P(play) number is the same missing column. Props side: pts -0.0412 (-0.0230 worst case), reb -0.0182 (-0.0087), ast -0.0089 (-0.0050), fg3m -0.0041 (T085-88 and red team); the pts head also has no `min_last`. | Two signals about who sits in the rotation; they overlap. | Re-baseline only: P(play) with the R columns (RECENT_ROLE harness) vs R + the T-30 starter flag, 2023/2024, same rows. Starters are still the box-score proxy (optimistic), so this sizes the overlap, it does not confirm T-30. | 2023/2024 descriptive; the clean test is the live T-30 shadow (reader dedupe shipped e06763d; 4 of 6 early decisions were skipped by the old reader, data audit F1). No holdout. | Gain survives at >= 50% of its size: 0.70. It is a calibration of expectations, not a new lever. |
| C3 | **P3 + P2**: fg3m zero mass (a descriptive line that named a lever, with a same-sign bias in both seasons) | T113, T117, T118 (red team focus question 6) | Red team: hybrid threshold log loss fg3m >= 1 improves -0.0076 [-0.0099,-0.0054] (2024) because NB P(0) is better calibrated; all-threshold fg3m -0.0028 [-0.0038,-0.0019] (T113). **My recomputation from the stored production grid (descriptive, no CI):** predicted P(fg3m >= 1) = 0.590 vs observed 0.559 (2023, n=27,619) and 0.607 vs 0.577 (2024, n=27,701), ECE 0.031 / 0.030; P(>= 2..4) are close (bias -0.015 to -0.021, ECE 0.009-0.022). reb and ast thresholds are within 0.006 of the observed rates. 42.3% of fg3m rows are 0 and only 16.6% of the quantile grid sits at <= 0. | A location-scale quantile head cannot hold a 42% atom; the loss is in P(0), which is the "1+ threes" and 0.5-line probability. | Pre-register a one-parameter zero-mass correction (or NB P(0) from the production mean) for fg3m only, scored on threshold log loss at >= 1 and the mean over >= 1/2/3, floor 0.002, with CALIBRATED_GRID's lesson built in: a shuffled-window control arm must lose (T339 isotonic gains were generic shrinkage, the shuffled control gained more). | 2023 select, 2024 report. Forward: the `_int` shadow does not change P(>=N), so a new arm would be needed. Never a 2025 touch before a pass. | Bias real and persistent: 0.9 (seen in both seasons). Beats a shuffled control and clears 0.002 at >= 1 in both seasons: 0.30. Worth money: low, because the book is better calibrated in every cell (T339). |
| C4 | **P6**: sim ast, never re-run on rebuilt possessions and never compared with the current production ast head | T001-T003, T062-T064, `docs/fair_rematch_2024.json` | In the fair rematch (600 games of 2024) the sim (mixture on all rows, n=15,933) beat the season average on assists by -0.0191 [-0.0254,-0.0130] and on "intermittent" played assists by -0.0198 [-0.0277,-0.0122] (n=5,254), but lost on played rows overall (+0.0143 [+0.0052,+0.0229], T064), lost pts (+0.268) and reb (+0.123) badly. The comparator was season-average, not the current context_residual (which is -0.0229 better than recency_conformal for ast, T094). Inputs predate the possession fixes (event order 2026-10-10, Missi free throws, team-0 rows, stint rebuild: `docs/reviews/possession_order_2026-10-10.md` lists "sim heads" as stale). | Assists depend on who is on the floor with whom; the sim carries lineup structure the GBM only sees through `vac_ast`. | Do not rebuild the sim. Only if Devin wants it: ast-only, 600-game rerun on the rebuilt tables vs current production on the same DNP handling, with a blend arm. | 2024 (600-game sample, already seen once); no holdout. | Adds anything over production ast: 0.04. Listed because the earlier win was a baseline artifact on one side and a data defect on the other, and nobody re-ran it clean. |

Ranking by (prior x value): C1 >> C2 (a re-baseline that changes what we expect from the lever we are betting on) > C3 > C4.

## Honest connection (P7) that none of the single rows says

One missing input, "how many minutes did he actually play last time", explains the P(play) fix (T455-T458), the bench concentration of the T-30
gain (T097), the LOAD pts gain on near-DNP rows (T452), the `pi` shortfall against its oracle (my recomputation: realised `pi` gets 8% of the
oracle gain), the SEQ blend's near-DNP failure (T442) and the E1 router's gap (T440). The NEAR_MISS_BUNDLE (draft) feeds LOAD to the stat head and
uses the production minutes path for the gate `g`; neither carries the recent-role columns into the component that decides the lower tail. If C1
is run before the bundle, the bundle's gate and its SEQ-with-near-DNP-routing option (the reversal clause in the ENSEMBLE closure, DECISIONS
2026-10-11 22:38) inherit a better `pi`. Sequencing advice, not a rule change: C1, then the bundle.

A second, smaller connection: INJURY_ELO_T30 (T160-T161, null at game level) and LINEUPS_KNOWN (large at player level) agree. T-30 information
is about who plays and how long, which the T-60 OUT report already settles for the game total. Do not build a T-30 win-probability model.

## Holdout state (docs/HOLDOUT_ACCESS_LOG.md), per model, so each candidate names an honest venue

| Model | 2025 touch | Note |
|---|---|---|
| rung0_injury_elo | spent (2026-10-08, proxy gate) | -0.0102 [-0.0180,-0.0026], n=1,316 games. Real-tip v2 not re-touched |
| props_context_residual | spent (2026-10-08, proxy gate) | pts figure likely overstated by 0.003-0.004 (red team); v2 carried on 2022-24 only |
| ctxres hybrid (CTXRES_V3) | VOID, not run | cancelled for the `opp_adjusted_ridge` leak; the model is still untouched, but the red team broke it (T114-T117), so it has no reason to be spent |
| P(play) R and C' (RECENT_ROLE_HOLDOUT) | spent (2026-10-12, PASS, T458) | first touch for R and C' only |
| lower-tail mixture, integer-quantile arm, T-30 arm | none logged | the 2026-10-09 systems replay computed their 2025 shadow deltas on a DB copy and SEALED them; do not read them |
| anything else in T001-T149 | "burned" pooled/ad hoc (T001-T061) | not confirmatory; ignore as a venue |

Season 2025 has no virgin status project-wide ("NO VIRGIN HOLDOUT", 2026-10-08). Every venue above that says "one touch" means one logged touch for a
never-evaluated model, as RECENT_ROLE_HOLDOUT did. The live 2026-27 log is the only clean test.

## Nothing here (checked and dismissed)

- **Sim vs season average, routing, archetype clusters, volatility buckets (T001-T033, T005-T019 wins in "intermittent / insufficient history").** The
  baseline averaged in DNP zeros and the Syntetos-Boylan buckets are DNP-frequency labels (DNP audit: 25-36% DNP in non-smooth buckets vs 5-7%).
  The fair rematch reverses them (T062-T064, season average wins pts +0.268 [+0.225,+0.312], reb +0.123, ast +0.014). Not a slice win; a baseline artifact (P6, wrong baseline).
- **Matchup 3A and team-level opponent adjust (T034-T041, T048-T060).** Point-only, worse or +/-0.0004; the "props pipeline +0.27 worse than season average" rows are
  the same played-rows/DNP-handling mismatch and are explicitly unreconciled. Closed twice.
- **On-court usage (T004, +0.398) and archetype-mix non-additivity (T042, R2 = 0.016).** Ran on pre-rebuild lineups (P6 technically), but the usage failure is a
  renormalisation mechanism, not a data defect, and F11/F8 re-ran lineup context on the fixed lineups with nulls (T244-T262). The R2 is "real but tiny".
- **Usage redistribution (T047, n = 0).** Not a test, but production already carries `vac_*`, `n_out_rot`, `vac_min` for OUT teammates; nothing is missing.
- **Minutes 1A (T043-T046).** The ungated cold-start gain (-1.47 min MAE) is already exploited by shipping the gate; the rookie prior was tested separately (T181).
- **Set transformer / SEQ alone (T065-T068).** Lost or tied. The pts blends (T069, T071, -0.0058) were "KEPT, never followed up" on the weaker old comparator; ENSEMBLE E2 did follow
  them up on current production (T442: pts -0.0110 / -0.0131, fails calibration and near-DNP slices). Already closed with a named reversal; see C1 for the one way it reopens.
- **CTXRES V2/V3 hybrid (T073-T080, T102-T117).** The T102-T109 wins are a support artifact (continuous production vs integer NB); fair deltas are pts -0.0078 [-0.0128,-0.0029],
  reb +0.0022 [0.0000,+0.0045], ast -0.0012, fg3m -0.0019 [-0.0031,-0.0007], all below the floor, and the shuffled-label arm shows the recipe, not information, drives it. The pts
  gain sits on low-minute rows, which the lower-tail mixture now covers. The one real remnant is C3.
- **INTEGER_QUANTILES (T118, T124-T127).** A scoring-convention gain; P(>=N) is invariant to 0.0025, so it changes no probability and no bet. Nothing to follow up.
- **PTS_TAIL (T129-T135).** sqrt and gamma tails null on threshold log loss; mondrian_up has CRPS -0.0022 [-0.0037,-0.0007] in both seasons but below the floor by half and stacks to at most
  about -0.002 on top of the mixture. Not worth a rule.
- **RIDGE_V2 (T098-T101).** All |delta| <= 0.0004, CIs span 0. **JOINT (T081)** tie vs Gaussian copula (+0.00003 [-0.0014,+0.0016], n=7,890 synthetic parlays): the copula is enough.
  **PBP_GPT (T082-T084)**: lost everywhere except two tiny live-prop cells; there is no live product.
- **REALTIP (T091-T095, T128).** Validation of production (gain unchanged or -3.5% on pts). The proxy-gate caveat (T073-T090 ran with the 19:00 proxy) shifts a delta by at most
  0.003 and both arms share it.
- **TRACKING_SCREEN (T119-T123, T174-T175 cold slices).** P4 look: passing->ast on rows with < 10 prior games -0.0139 [-0.0210,-0.0071] and rebounding hustle -0.0126 [-0.0193,-0.0057], each
  n=463 of 27,583 (1.7% of rows), one season, two of ten cells found by looking. The whole-sample effect is -0.0002; the slice contributes about 0.0002 to the overall CRPS even if real.
  The hustle table's row existence depends on tonight's minutes (data audit F9), which is another reason not to chase it. Dismissed on value, not on truth.
- **Market spread as a better blowout proxy (props use plain MOV-Elo for `exp_margin`, not injury-Elo).** Looked like a P1 gap; GAME_MARKET_CONTEXT (T389-T414) already put the market
  line into the props heads and came back null, so a better margin has no demonstrated value.
- **Injury-Elo player value built from recent role (P1 against props).** The red team's post-hoc DNP oracle is weaker than the report (-0.0020 vs -0.0079), so it is not a usable upper bound,
  and the whole injury-Elo effect (-0.0081, n=3,951) leaves an MDE (~0.004) that no value refinement could resolve. Untestable at this n, not listed.

## Not verified

- Whether `pi` features are the only place `min_last` is missing: I read the lower-tail path and the feature list, not the minutes hurdle (`nba/props/minutes.py`) beyond the RECENT_ROLE note that it uses trailing-150-game columns.
- The fg3m P(0) bias is an uncorrected descriptive (no clustered CI, ECE with five bins); the confirmation CI belongs to the rule.
- The oracle-`pi` numbers use the stored components dated 2026-10-11 (after the clean-up); I did not re-derive the components.
- T001-T061 numbers are as back-filled (many "n/r"); I did not open the source scratch scripts.
- The sealed 2025 replay shadow deltas were not read.
