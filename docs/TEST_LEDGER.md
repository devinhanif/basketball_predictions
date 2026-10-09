# Test ledger — append-only

Required by the "Standing multiplicity / FDR plan" in
[`ACCEPTANCE_CRITERIA_2026-10-08.md`](ACCEPTANCE_CRITERIA_2026-10-08.md). One row per
test ever run. Never edit or delete a row; corrections are new rows that cite the old
row id. A rerun of the same comparison is corrected against ALL prior rows with the
same `comparison key` (re-rank the union), not a fresh small family.

Backfilled 2026-10-08 from `FDR_AUDIT`, `RESULTS`, `MATCHUP_3A_RESULT`,
`MORNING_BRIEFING`, `ACCEPTANCE_CRITERIA`, `HOLDOUT_ACCESS_LOG`, `NEXT_SESSION.md`. No
number below was re-run; "not recorded" means the source doc never committed it.

Conventions
- Effect sign: delta = candidate - baseline CRPS (or MAE / log loss); negative = candidate better.
- p-approx: two-sided normal approx from the 95% CI, `se=(hi-lo)/3.92`, `z=|pt|/se`.
  "perm" = permutation p. "n/r" = not recorded.
- Boot: `row` = per-player-game resampling (anti-conservative, pre-fix); `clus` = per-`game_id`
  clustered; `none` = no bootstrap; `perm` = permutation test.
- Cap rules (standing plan #4, #5): no committed numeric CI => PROVISIONAL permanently;
  row-level bootstrap => PROVISIONAL (cannot be CONFIRMED until re-run clustered).
- Holdout: `burned` = touched `season=2025` directly or via pooled/ad hoc splits (no virgin
  holdout exists, see `HOLDOUT_ACCESS_LOG.md`); `n/a` = not a holdout-style test.
- BH verdict is WITHIN the family as originally declared (or, for families never
  pre-registered, as reconstructed; flagged "post-hoc family"). The FDR audit's pooled m=5
  (A+G) is NOT used here: standing plan #3 bans pooling unrelated families after the fact.

## Family summary (BH within family, q=0.05)

| family | description | m (tests) | tests with usable p | BH survivors | cap |
|---|---|---|---|---|---|
| A | props: sim vs season-avg, overall | 3 | 3 | reb (p=.0117 <= .0167) | row-level => PROVISIONAL |
| B | on-court usage denominator (feature reject) | 1 | 0 | n/a | no CI => PROVISIONAL (decision robust on effect size) |
| C | volatility-bucket routing grid | 15 | 0 | not computable | no numbers => PROVISIONAL |
| D | archetype-cluster routing | 18 (8 shown) | 0 | not computable | selective reporting => REJECTED as evidence |
| E | walk-forward vs holdout routing | 6 (up to 12) | 0 | not computable | PROVISIONAL |
| F | matchup/opponent adjust, original + 3A rerun | 8 | 0 (point only) | not computable | never bootstrapped => REJECTED as tested result |
| G | lineup archetype-mix non-additivity | 1 | 1 | yes (perm p=.004) | permutation, no cap |
| H | minutes 1A | 4 | 0 (bounds not given) | not computable | no bounds => PROVISIONAL |
| I | usage redistribution 2A/2B | 1 | n/a | n/a | inert null (n=0) |
| M3A | matchup-3A archetype, clustered (aux CIs + gate) | 4 pre-reg (+8 aux) | aux only | pre-reg paired CI never run | PROVISIONAL / deferred |
| R | points n_sims=2000 retest | 1 | 0 | pending | NOT COMPLETED |

## Ledger

| id | date | family | stat / cell | point | CI lo | CI hi | boot | p-approx | BH verdict (within family) | holdout status |
|---|---|---|---|---|---|---|---|---|---|---|
| T001 | <=2026-10-08 | A | reb: sim - savg CRPS | -0.014 | -0.024 | -0.003 | row | 0.0117 | SURVIVES (rank1, thr .0167); PROVISIONAL pending clustered rerun | burned (pooled 4 seasons) |
| T002 | <=2026-10-08 | A | ast: sim - savg CRPS | -0.006 | -0.014 | +0.002 | row | 0.142 | fails (thr .0333); near-null, MDE adequate | burned |
| T003 | <=2026-10-08 | A | pts: sim - savg CRPS (n_sims=500) | +0.011 | -0.019 | +0.043 | row | 0.448 | fails (thr .05); UNDERPOWERED (MDE ~0.044), not a null | burned |
| T004 | <=2026-10-08 | B | pts: on-court usage vs season-share proxy CRPS | +0.398 | n/r | n/r | row (unspecified) | n/r ("excludes 0") | n/a; feature kept OFF; PROVISIONAL by cap, decision robust on effect size | n/a (burned pooled) |
| T005-T019 | <=2026-10-08 | C | 15 cells = {pts,reb,ast} x {insufficient_history, intermittent, erratic, lumpy, smooth}; sim vs savg | n/r | n/r | n/r | row | n/r | not computable; PROVISIONAL. Reported labels: pts: IH sim, INT sim, ERR tie, LUM tie, SMO savg; reb: IH sim, INT sim, ERR sim, LUM tie, SMO tie; ast: IH tie, INT sim, ERR sim, LUM tie, SMO tie (8 of 15 "CI excludes 0") | burned |
| T020 | <=2026-10-08 | D | reb cluster 0 sim wins | n/r | n/r | n/r | row | n/r | REJECTED (curated subset) | burned |
| T021 | <=2026-10-08 | D | reb cluster 5 sim wins | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T022 | <=2026-10-08 | D | reb cluster 3 sim loses | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T023 | <=2026-10-08 | D | ast cluster 0 sim wins | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T024 | <=2026-10-08 | D | ast cluster 1 sim wins | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T025 | <=2026-10-08 | D | ast cluster 3 sim wins | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T026 | <=2026-10-08 | D | ast cluster 5 sim loses | n/r | n/r | n/r | row | n/r | REJECTED | burned |
| T027 | <=2026-10-08 | D | UNREPORTED remainder of 18-cell grid (6 clusters x 3 stats minus the 7 above; all of pts) | n/r | n/r | n/r | row | n/r | tested, never reported; counts toward m | burned |
| T028-T030 | <=2026-10-08 | E | {pts,reb,ast}, walk-forward split, routed vs savg/sim | n/r | n/r | n/r | row | n/r | PROVISIONAL. Labels: ast win, reb win, pts no win | burned (ad hoc split, unrecorded) |
| T031-T033 | <=2026-10-08 | E | {pts,reb,ast}, "holdout" split (ad hoc, not split_frozen_holdout) | n/r | n/r | n/r | row | n/r | PROVISIONAL. Labels: ast win, reb tie, pts no win | burned; reuse risk |
| T034 | 2026-10-08 | F | pts original 330f036: team-level adj - base | +0.006 | n/r | n/r | none | n/r | REJECTED as tested result (point only); flag stays OFF | burned |
| T035 | 2026-10-08 | F | reb original | +0.002 | n/r | n/r | none | n/r | same | burned |
| T036 | 2026-10-08 | F | ast original | +0.003 | n/r | n/r | none | n/r | same | burned |
| T037 | 2026-10-08 | F | fg3m original | +0.002 | n/r | n/r | none | n/r | same | burned |
| T038 | 2026-10-08 | F | pts 3A rerun f150583 | +0.0065 | n/r | n/r | none | n/r | same | burned |
| T039 | 2026-10-08 | F | reb 3A rerun | +0.0018 | n/r | n/r | none | n/r | same | burned |
| T040 | 2026-10-08 | F | ast 3A rerun | +0.0034 | n/r | n/r | none | n/r | same | burned |
| T041 | 2026-10-08 | F | fg3m 3A rerun | +0.0019 | n/r | n/r | none | n/r | same | burned |
| T042 | 2026-10-08 | G | lineup archetype-mix R2 (1,154 units >=100 poss) | R2=0.016 | n/a | n/a | perm | 0.004 (perm; null R2 0.005) | SURVIVES (m=1); real but practically tiny | burned (all seasons, explanatory) |
| T043 | 2026-10-08 | H | minutes gated: overall MAE (n=69,205) | -0.096 | n/r | n/r | n/r | n/r ("excludes 0") | PROVISIONAL (no bounds); large n, trusted directionally | burned (cal split likely incl. 2025) |
| T044 | 2026-10-08 | H | minutes gated: overall DNP log loss | -0.0017 | n/r | n/r | n/r | n/r | same | burned |
| T045 | 2026-10-08 | H | minutes ungated: cold-start MAE (n_played_prior<20) | -1.47 | n/r | n/r | n/r | n/r | same; ungated regressed established players, so shipped gated | burned |
| T046 | 2026-10-08 | H | minutes ungated: cold-start DNP log loss | -0.021 | n/r | n/r | n/r | n/r | same | burned |
| T047 | prior session | I | usage redistribution 2A/2B, restricted sample | n=0 | n/a | n/a | none | n/a | inert null; not a test | n/a |
| T048 | 2026-10-08 | M3A-aux | pts: baseline config CRPS - season-avg | +0.2730 | +0.2618 | +0.2845 | clus | <1e-6 | aux, not a decision test. Positive = props pipeline WORSE than season-avg point baseline in this pipeline (differs from Family A, which is sim); comparison definition unreconciled | burned (full 2022-10..2026-06) |
| T049 | 2026-10-08 | M3A-aux | pts: archetype config - season-avg | +0.3054 | +0.2943 | +0.3165 | clus | <1e-6 | aux | burned |
| T050 | 2026-10-08 | M3A-aux | reb: baseline - season-avg | +0.0811 | +0.0770 | +0.0856 | clus | <1e-6 | aux | burned |
| T051 | 2026-10-08 | M3A-aux | reb: archetype - season-avg | +0.0852 | +0.0812 | +0.0898 | clus | <1e-6 | aux | burned |
| T052 | 2026-10-08 | M3A-aux | ast: baseline - season-avg | +0.3467 | +0.3422 | +0.3517 | clus | <1e-6 | aux | burned |
| T053 | 2026-10-08 | M3A-aux | ast: archetype - season-avg | +0.3472 | +0.3427 | +0.3523 | clus | <1e-6 | aux | burned |
| T054 | 2026-10-08 | M3A-aux | fg3m: baseline - season-avg | +0.1291 | +0.1257 | +0.1321 | clus | <1e-6 | aux | burned |
| T055 | 2026-10-08 | M3A-aux | fg3m: archetype - season-avg | +0.1295 | +0.1262 | +0.1324 | clus | <1e-6 | aux | burned |
| T056 | 2026-10-08 | M3A | pts: archetype - baseline pooled CRPS | +0.0324 | n/r | n/r | none (paired CI deferred) | n/r | pre-reg m=4 test never completed; point-only | burned |
| T057 | 2026-10-08 | M3A | reb: archetype - baseline (HARD GATE) | +0.0041 | n/r | n/r | none | n/r | gate FAIL on point estimate (needs CI upper <=0); REJECT per pre-reg rule 1, stays PROVISIONAL (no CI) | burned |
| T058 | 2026-10-08 | M3A | ast: archetype - baseline | +0.0005 | n/r | n/r | none | n/r | not evaluated (gate failed) | burned |
| T059 | 2026-10-08 | M3A | fg3m: archetype - baseline | +0.0004 | n/r | n/r | none | n/r | not evaluated (gate failed) | burned |
| T060 | 2026-10-08 | M3A | team_level - baseline pooled (pts/reb/ast/fg3m) | -0.0001/-0.0004/-0.0004/-0.0001 | n/r | n/r | none | n/r | informational; negligible; contradicts earlier partial read per briefing | burned |
| T061 | 2026-10-08 | R | pts: sim - savg CRPS @ n_sims=2000 (comparison key = pts sim-vs-savg, joins Family A) | +0.1899 | +0.1747 | +0.2062 | clus | <1e-6 | SIM WORSE (CI excludes 0, wrong direction); ran on all 138,409 player-games / 5,269 games, not the 600-game Family-A sample; re-ranked with T001-T003 it is not an improving survivor. See docs/POINTS_RETEST_2026-10-08.md | burned (same rows; exploratory) |

Counts: 61 ledger rows, covering ~100 underlying tests once the grouped rows are
expanded (C=15, E=6, T027 hides >=10 more). Rows with a numeric CI: 11 (T001-T003, T048-T055).
Rows with a usable p: 12 (those 11 + T042).

## Survivors (BH within family, q=0.05)

| result | strict status under standing plan |
|---|---|
| Rebounds sim beats savg (T001) | BH-survivor within A (m=3) but row-level bootstrap => PROVISIONAL until re-run clustered. Expect to survive with margin only if clustered SE inflates < ~1.2x (p=.0117 vs thr .0167). |
| Lineup archetype-mix (T042) | CONFIRMED (permutation, m=1); effect R2=0.016, practically negligible |
| Everything else | PROVISIONAL, REJECTED-as-evidence, or inert; none BH-correctable |

Net: 2 survivors, 1 strictly confirmed (G) and 1 provisional-survivor (reb). The FDR
audit's "CONFIRMED" for rebounds, B, and H predate the strict standing-plan caps and are
downgraded here to PROVISIONAL on procedural grounds (no clustered CI / no committed
bounds), not because any effect is believed false.

## Run-order / ID notes
- IDs T005-T019, T028-T033 each stand for several tests; expand to individual rows when the
  per-cell JSON is first committed (standing plan #4).
- New rows start at T062.
| T062 | 2026-10-08 | E (fair rematch, TEST_PLAN §e) | pts: sim(played_only, cur-season pts rates) - played-only recency avg, played rows, 600 games of 2024 | +0.2680 | +0.2246 | +0.3122 | clus | <0.01 | SEASON-AVG WINS (CI excludes 0, wrong direction for sim); BH m=3 | not holdout (2024) |
| T063 | 2026-10-08 | E (fair rematch, TEST_PLAN §e) | reb: sim(played_only, cur-season pts rates) - played-only recency avg, played rows, 600 games of 2024 | +0.1225 | +0.1062 | +0.1391 | clus | <0.01 | SEASON-AVG WINS (CI excludes 0, wrong direction for sim); BH m=3 | not holdout (2024) |
| T064 | 2026-10-08 | E (fair rematch, TEST_PLAN §e) | ast: sim(played_only, cur-season pts rates) - played-only recency avg, played rows, 600 games of 2024 | +0.0143 | +0.0052 | +0.0229 | clus | <0.01 | SEASON-AVG WINS (CI excludes 0, wrong direction for sim); BH m=3 | not holdout (2024) |

**2026-10-08 note:** the fair rematch (T062-T064) overturns T001 ("sim beats season-avg on rebounds"): with DNP rows excluded from both sides, the played-only recency average beats the sim on pts, reb and ast. T001 is retracted as a DNP-zero artifact (see docs/DNP_AUDIT_2026-10-08.md). Production props = context_residual.
| T065 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | A seq alone pts: CRPS vs production context_residual, 2024 | +0.0247 | +0.0164 | +0.0331 | clus | — | LOSES | not holdout (2024) |
| T066 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | A seq alone reb: CRPS vs production context_residual, 2024 | +0.0158 | +0.0122 | +0.0191 | clus | — | LOSES | not holdout (2024) |
| T067 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | A seq alone ast: CRPS vs production context_residual, 2024 | +0.0117 | +0.0091 | +0.0143 | clus | — | LOSES | not holdout (2024) |
| T068 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | A seq alone fg3m: CRPS vs production context_residual, 2024 | -0.0010 | -0.0027 | +0.0006 | clus | — | tie | not holdout (2024) |
| T069 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | B 50/50 blend pts: CRPS vs production context_residual, 2024 | -0.0058 | -0.0100 | -0.0016 | clus | — | KEPT (passes floor 0.005, BH) | not holdout (2024) |
| T070 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | B 50/50 blend fg3m: CRPS vs production context_residual, 2024 | -0.0031 | -0.0040 | -0.0023 | clus | — | below floor | not holdout (2024) |
| T071 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | C learned blend pts: CRPS vs production context_residual, 2024 | -0.0058 | -0.0091 | -0.0025 | clus | — | KEPT | not holdout (2024) |
| T072 | 2026-10-08 | SEQ (seq_props Colab, docs/SEQ_PROPS.md) | C learned blend fg3m: CRPS vs production context_residual, 2024 | -0.0029 | -0.0039 | -0.0019 | clus | — | below floor | not holdout (2024) |
| T073 | 2026-10-08 | CTXRES_V2 exp1 (docs/CTXRES_V2.md) | pts: xgb_v12_quantile (2023-selected) - v1_prod CRPS, 2024 | -0.0744 | -0.0815 | -0.0668 | clus | 0.0005 | pass | not holdout (2024) |
| T074 | 2026-10-08 | CTXRES_V2 exp1 (docs/CTXRES_V2.md) | reb: xgb_v12_quantile (2023-selected) - v1_prod CRPS, 2024 | -0.0376 | -0.0411 | -0.0340 | clus | 0.0005 | pass | not holdout (2024) |
| T075 | 2026-10-08 | CTXRES_V2 exp1 (docs/CTXRES_V2.md) | ast: xgb_v12_quantile (2023-selected) - v1_prod CRPS, 2024 | -0.0154 | -0.0173 | -0.0135 | clus | 0.0005 | pass | not holdout (2024) |
| T076 | 2026-10-08 | CTXRES_V2 exp1 (docs/CTXRES_V2.md) | fg3m: xgb_v12_quantile (2023-selected) - v1_prod CRPS, 2024 | -0.0205 | -0.0218 | -0.0191 | clus | 0.0005 | FAIL cov80 0.867 (>0.85) | not holdout (2024) |

**CTXRES_V2 exp1 verdict (T073-T076):** NOT KEPT overall (rule requires all 4 stats; fg3m 80% coverage 0.867 outside 0.75-0.85). Isotonic threshold calibration passes all 4 stats. Completion run 20261008_140126 supplied the candidate's 2024 rows (procedural fix, documented).
| T077 | 2026-10-08 | CTXRES_V2 exp2 (docs/CTXRES_V2.md) | pts: xgb_v12_poisson_nb (recorded candidate) - v1_prod CRPS, 2024 | -0.0299 | -0.0388 | -0.0209 | clus | 0.0005 | FAIL (slice regression; PIT cov 0.716; bias +0.172) | not holdout (2024) |
| T078 | 2026-10-08 | CTXRES_V2 exp2 (docs/CTXRES_V2.md) | reb: xgb_v12_poisson_nb (recorded candidate) - v1_prod CRPS, 2024 | -0.0505 | -0.0541 | -0.0466 | clus | 0.0005 | pass | not holdout (2024) |
| T079 | 2026-10-08 | CTXRES_V2 exp2 (docs/CTXRES_V2.md) | ast: xgb_v12_poisson_nb (recorded candidate) - v1_prod CRPS, 2024 | -0.0303 | -0.0324 | -0.0281 | clus | 0.0005 | pass | not holdout (2024) |
| T080 | 2026-10-08 | CTXRES_V2 exp2 (docs/CTXRES_V2.md) | fg3m: xgb_v12_poisson_nb (recorded candidate) - v1_prod CRPS, 2024 | -0.0321 | -0.0336 | -0.0306 | clus | 0.0005 | pass | not holdout (2024) |

**CTXRES_V2 exp2 verdict (T077-T080):** NOT KEPT overall (pts fails). Rerun's own 2023 selection matched the recorded candidate. Descriptive (docs/CTXRES_V2_EXP2_RESULTS.md): best pts arm = xgb_v12_quantile (-0.0758); Poisson/NB best for reb/ast/fg3m; drop-one ablation: opponent-adjusted ridge (group i) carries nearly all of v2's gain (pts +0.066, reb +0.035 when dropped); other groups individually ~0.

**2026-10-08 19:35 — LEAK NOTICE (affects T073-T080, CTXRES_V2 exp1/exp2, and the exp-2 drop-one ablation):** the v2 feature `opp_adjusted_ridge` is NULL for exactly the rows where tonight's minutes < 5 (8.0% of rows; mean pts 0.86 vs 11.76), leaking same-game minutes information. All v2 vs v1 deltas and the 'ridge carries the gain' ablation are contaminated by an unknown amount; treat them as invalid until re-measured with the fixed builder (nba/features/opponent_ridge_v2.py, ref_fixed arm). Production models (context_residual v1, injury_elo) do not use this column. The leak audit (name allow-list, top-10 importance, label rank-correlation) did not detect a missingness-pattern leak; a missingness-vs-label audit is being added.
| T081 | 2026-10-08 | JOINT (docs/JOINT_GAME_SET.md) | set-transformer joint vs Gaussian copula, joint log loss on 7,890 synthetic same-game parlays, 2024 | +0.00003 | -0.0014 | +0.0016 | clus | — | NOT KEPT: R1 tie, R2 pass (single-leg ECE +0.0007), R3 fail (total CRPS CI hi +0.18 > 0.05) | not holdout (2024) |
| T082 | 2026-10-08 | PBP_GPT P1 (docs/PBP_GPT.md) | live win LL, GPT - score/clock/Elo logistic, 4 checkpoints, 2024 (n=300 games each) | +0.020..+0.038 | q2 +0.006 | q2 +0.070 | clus | 0.018 (BH, each) | NOT KEPT: significantly worse at all 4 checkpoints | not holdout (2024) |
| T083 | 2026-10-08 | PBP_GPT P2 (docs/PBP_GPT.md) | pregame win LL, GPT - injury-Elo, 2024 (n=150) | +0.0875 | +0.0403 | +0.1339 | clus | 0.0015 | NOT KEPT (margin/total CRPS also worse) | not holdout (2024) |
| T084 | 2026-10-08 | PBP_GPT P3 (docs/PBP_GPT.md) | live remaining pts/reb/ast CRPS, GPT - pro-rated recency NB, 12 cells, 2024 | 2/12 win (q4_5min pts -0.095, ast -0.012) | — | — | clus | BH 12 | 8 cells significantly worse | not holdout (2024) |

**PBP_GPT verdict (T082-T084):** NOT KEPT. G0 perplexity gate passed (learned play grammar); live and pregame probabilities worse than simple baselines.
| T085 | 2026-10-08 | LINEUPS_KNOWN (docs/LINEUPS_KNOWN.md; T-30 != T-60) | pts: T-30 (+starter features) - T-60 production context_residual CRPS, 2024 | -0.0412 | -0.0475 | -0.0346 | clus | 0.0005 | ADDS VALUE AT T-30 (all checks pass; proxy optimistic) | not holdout (2024) |
| T086 | 2026-10-08 | LINEUPS_KNOWN | reb: T-30 - T-60 CRPS, 2024 | -0.0182 | -0.0210 | -0.0154 | clus | 0.0005 | ADDS VALUE AT T-30 | not holdout (2024) |
| T087 | 2026-10-08 | LINEUPS_KNOWN | ast: T-30 - T-60 CRPS, 2024 | -0.0089 | -0.0106 | -0.0071 | clus | 0.0005 | ADDS VALUE AT T-30 | not holdout (2024) |
| T088 | 2026-10-08 | LINEUPS_KNOWN | fg3m: T-30 - T-60 CRPS, 2024 | -0.0041 | -0.0051 | -0.0030 | clus | 0.0005 | BELOW FLOOR (-0.005); significant but small | not holdout (2024) |
| T089 | 2026-10-08 | LINEUPS_KNOWN | P(play) log loss, T-30 logistic - T-60 recalibrated production P(play), 2024 (n=32,642) | -0.0538 | -0.0569 | -0.0509 | clus | 0.0005 | secondary; mostly mechanical (every box-score starter played) | not holdout (2024) |
| T090 | 2026-10-08 | LINEUPS_KNOWN | P(play) log loss, bench-tonight rows only, 2024 (n=19,578) | -0.0358 | -0.0399 | -0.0318 | clus | 0.0005 | secondary; non-mechanical part; 'listed active' NOT measurable (no historical inactive list) | not holdout (2024) |

**LINEUPS_KNOWN verdict (T085-T090):** at T-30, confirmed starters improve pts/reb/ast CRPS (and P(play)); fg3m is significant but below the practical floor. The gain is a PROXY-based, upward-biased estimate (box-score starter flag, not the announced five; a 5% synthetic scratch process attenuates it only ~3%). It is a different prediction time and says nothing about T-60. NOT wired into production; red-team review + maintainer decision needed. 2023 replication (descriptive): pts -0.0366, reb -0.0162, ast -0.0094, fg3m -0.0032.
