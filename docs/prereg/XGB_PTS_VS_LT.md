# XGB_PTS_VS_LT: does the leak-free XGBoost quantile pts model beat production-as-it-now-stands? (pre-registration)

Status: FROZEN 2026-10-11 ~23:55 CT under Devin's approval of the day ("they run as soon as you're done, i approve"; docs/DECISIONS.md 2026-10-11 ~22:55). Pre-freeze decisions by the main session after the gate measure, all labelled in the checks section: check 3 amended (a column is exempt iff its NULL status is constant within player or within calendar month, computed by the harness, never by name; the ridge leak of 2026-10-08 would not be exempt); check 5 amended (contrasts on the per-season key intersection; A0-only keys <= 0.5%, labels equal); P6, P7, P8 and the 25% / 0.5% bounds KEPT (they guard the failure modes the red team found); P3 as a point-estimate non-inferiority KEPT; A0 = the shadow-logged lower-tail mixture on integer support, labelled as such. Gate measured pre-freeze, section "Minimum data checks". Committed by the maintainer's hand BEFORE any fit. Seasons: 2023 = 2023-24, 2024 = 2024-25,
2025 = 2025-26 (frozen holdout, never loaded). Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/XGB_PTS_VS_LT.md | shasum -a 256`.
Id: `XGB_PTS_VS_LT`. Harness: `research/eval/xgb_pts_vs_lt_eval.py` (stages gate / smoke / run / report). Gate output:
`reports/prereg_xgb_pts_vs_lt/gate.json`.

## Why
Devin, 2026-10-11: "can you rerun the XGBoost arms now that they are uncontaminated, because we should see if that was accurately
thrown away." The ledger: T073-T080 (leaky) are invalid; the leak-free rerun T102-T113 replicated the gains; the red team
(docs/reviews/redteam_ctxres_v3_leakfree_2026-10-09.md, T114-T118) showed the reb/ast/fg3m gains were integer-support scoring artefacts
(BROKEN: 92-126% of the margin is a one-line `ceil(q - 0.5)` on production) and that pts survived WOUNDED at -0.0078 [-0.0128, -0.0029]
on integer support for both arms: the whole 2024 gain sat on rows where the player ends up under 5 minutes (-0.112, n 2,304; +0.0017
[-0.0035, +0.0068] elsewhere), it reverses in the 2024 postseason (+0.024), a third of it is one team (PHI), about 70% of it is the quantile
family rather than the v2 features (same recipe on v1 features: -0.0055), and its upper tail is too thin (worse threshold log loss).
Since then production gained exactly what targets those rows: the lower-tail short-minutes mixture for pts (T136-T159, dCRPS -0.011)
and integer support (T124-T127). The one open, fair question: against that stronger comparator, does the XGBoost pts model still win?

## Hypothesis
H1: the leak-free XGBoost quantile pts model (A1) has lower integer-support CRPS than production's lower-tail mixture on integer support
(A0), by at least the practical floor, on 2023 and on 2024, and is not worse on threshold log loss at the DraftKings T-60 main lines.
Null: A1 - A0 >= -0.005 in at least one season, or a guard fails. A pass yields a SHADOW ARM candidate for the live season only.

**Prior pass probability: about 4% (plausible range 2-8%); the maintainer's prior was ~25%.** Reasons, from numbers already in the ledger and
computed before this rule was written (no A1 - A0 contrast has been computed): A1 - v1_prod was -0.0145 (2023) and -0.0078 (2024) on
integer support; A0 - production(off) was -0.0126 (2023) and -0.0109 (2024), and `v1_prod` is a faithful stand-in for production (q50
correlation 0.9995). Chaining, A1 - A0 is about -0.002 in 2023 and about +0.003 in 2024, so the 2024 floor of -0.005 sits more than three
standard errors away (SE about 0.0025, section "Power"), and the 2023 floor about one SE away. The residual probability is model error in
that chaining (v1_prod is not production; A0 also sharpens the middle, not only the tail) plus the chance that a quantile head that
natively learns the zero pile plus the own-status features beats a mixture bolted on top. The lower-tail mixture should have absorbed
the under-5-minute gain; this rule tests whether it did.

## Data exposure (honest)
* Seen: A1 - v1_prod (both seasons, integer support), A2 - v1_prod, A1 - v1_prod threshold log loss at pts 10/15/20/25/30 (worse, +0.0015 in 2024), the slice anatomy above, A0 - production(off) for both seasons, all
  A0 and A1 levels separately. **Not seen: any A1 - A0 contrast, on any rows, in any season, and the threshold log loss of A1 at the
  DraftKings lines.** The gate stage computes no A1 - A0 quantity (by construction, not by restraint).
* 2023 is NOT independent for A1: its XGBoost parameters came from the exp-2 Optuna search `search_v12` selected on 2023 blocks with the
  leaky feature set, and its per-stat family was chosen from 2023 and 2024 leaderboards (docs/CTXRES_V3_LEAKFREE.md section 1). A pass in
  2023 is therefore a consistency gate that is favourable to A1; the kill criterion (no pass in 2023 closes the question) is lenient to A1,
  and a pass earns only a shadow arm. 2024 has been seen for A1 - v1_prod and for A0.
* Season 2025 is not loaded, not read, not touched. `season <= 2024` is asserted in every frame; `nba.duckdb` is opened read-only.

## Arms (identical rows, identical scorer)
* **A0 = production lower-tail mixture on integer support** (`props_context_residual_lt` + integer quantiles, shadow-logged live). No refit:
  the stored components `reports/lower_tail/components_pts.npz` + `components_pi.npz` (collected 2026-10-11 on the current DB), pushed
  through the production `arm_grid("mixture")` and `to_integer_support`. Stored values: 2023 CRPS 3.140236 (n 27,619), 2024 3.176840
  (n 27,701); production `off` 3.152830 / 3.187553.
* **A1 = hybrid_lf pts, refit identically**: the ridge_v2 engine arm `ref_fixed` (v1 features + v2 groups a-h, j + the leak-free
  `rg_f3b8321a_{p,o}_pts` ridge columns; 95 columns, no `ridge_*` column), XGBoost 3.4.1 CPU hist `reg:quantileerror` at 19 levels
  (0.05..0.95), target = pts minus the as-of recency mean, `ext_tails` linear extrapolation to the 199 grid, clip >= 0, params of
  `data/colab/ridge_v2/base_config.json` (lr 0.0558198665, depth 3, subsample 0.71681787, colsample 0.71444285, min_child_weight 116.4037,
  alpha 0.1740, lambda 4.1387, 600 rounds cap, early stopping 20 on the newest 12%), seed 20261008, 18 month blocks, each fit on all rows
  strictly before the block. No search, no new column, no re-tuning.
* **A2 (descriptive, never in the rule) = the same recipe on the 42 v1 feature columns** (the red team's `v1only`, the family-only arm).

Every arm is mapped to the integer grid by the production `to_integer_support` (`max(ceil(q - 0.5), 0)`, running maximum) and scored by
`crps_from_quantiles` on the 199-point grid. Stored descriptive extras (never refit here, never in the rule): the literal stored
`hybrid_lf` checkpoints (a determinism cross-check of the refit) and the red team's `fixsched` arm (A1 with the two postseason-look-ahead
columns neutralised; effect on A1 - v1_prod was +0.0002 in 2023 and -0.0003 in 2024).

## Scored rows
Played player-games with n_prior >= 5, seasons 2023 and 2024. A1 and A2 come from the frozen export (`data/colab/ctxres_v2` +
`data/colab/ridge_v2`, 2026-10-08/09) and share one key set; A0 comes from the current DB. The 2026-10-11 clean-up added 118 scored
rows in 2024 to A0 that the export does not contain. **Every contrast is scored on the key intersection per season** (2023: 27,619;
2024: 27,583); the A0-only keys are counted and must stay within 0.5% of A0 (check 5). Labels must agree on every shared key.

## Metrics, support, inference
* Primary: integer-support CRPS, paired delta d = CRPS(A1) - CRPS(A0) per row. Point = mean(d); 95% CI = game-clustered percentile bootstrap,
  2,000 resamples, seed 0 (`paired_score_delta_ci`); two-sided clustered-bootstrap p-value (`boot_p_value`, seed 0).
* Multiplicity: BH over the family {A1 2023, A1 2024} (m = 2), `bh_adjust`. A2 and every slice are descriptive and outside the family.
* Secondary (in the rule): threshold log loss at the DraftKings T-60 main lines (book `draftkings`, `snapshot_kind` t60, stat pts, the
  ODDS_HISTORY pairs frames; one x.5 line per player-game): row log loss of P(pts >= ceil(line)) = share of the 199 integer quantiles
  >= ceil(line), clipped to [0.5/199, 1 - 0.5/199]; delta A1 - A0 mean over rows in the scored intersection, game-clustered CI reported.
* Calibration (integer support, lower-tail PIT): mean bias y - mean(q), continuity-corrected randomized PIT (seed 0) 80%-interval
  coverage, and the lower-tail `guard` (coverage within 0.02 of nominal at 0.10, 0.20, 0.80, 0.90, 0.95).
* Required slices, each with n and a clustered CI (A1 - A0; a slice below n = 300 is listed, not scored): minutes >= 5 vs < 5 tonight
  (post-hoc, descriptive of where any gain sits; realised minutes are a stratifier, never an input), regular season vs postseason
  (game_id not starting `002`: playoffs and play-in), without PHI (team 1610612755), first 15 team games vs the rest, starter vs bench
  (starter10 >= 0.5), proxy-safe (real tip at or after 19:00 ET) vs proxy-exposed (earlier), actual starter. Also reported: the largest
  single-team share of the 2024 gain and the delta without that team; A2 - A0, A1 - A2, threshold log loss at pts >= 10/15/20/25/30.

## PASS RULE (frozen text)
The question PASSES, giving a SHADOW ARM candidate only, iff the gate passes, the refit matches the stored checkpoints (max |dq| <= 1e-3
over all quantiles and blocks, both arms), and ALL of the following hold, with d = A1 - A0 on the key intersection:
* **P1 (2023 selects).** mean d <= -0.005, clustered 95% CI upper bound < 0, BH-adjusted p < 0.05.
* **P2 (2024 confirms).** The same three conditions on 2024.
* **P3 (threshold log loss, both seasons).** mean(log loss A1 - log loss A0) at the DraftKings T-60 main lines <= +0.002.
* **P4 (calibration, 2024).** |bias| <= 0.5; 80% coverage in [0.75, 0.85]; the lower-tail `guard` ok.
* **P5 (slices, 2024).** None of regular season, postseason, starter, bench, first 15 team games (each with n >= 300) has d > +0.01.
* **P6 (no harm where props live, both seasons).** The minutes >= 5 slice has mean d <= 0.
* **P7 (concentration, 2024).** No single team carries more than 25% of the summed 2024 gain.
* **P8 (proxy-safe, 2024).** On games whose real tip is at or after 19:00 ET (where the 19:00 ET report-time proxy of the frozen frame cannot
  admit a post-tip report), mean d <= -0.005.
Otherwise CLOSED. If P1 fails the question is closed without follow-up (kill criterion). A pass is never a promotion and never earns a
2025 touch by itself: it earns the live shadow log and a conversation with Devin.

## What each choice protects against
| choice | protects against |
|---|---|
| A0 = lower-tail mixture on integer support, not v1_prod | comparing to a weaker baseline than we would actually ship (the red-team wounds were all on rows the mixture targets) |
| both arms on the same integer grid and scorer | the T114-T118 support artefact (92-126% of a "gain") |
| key intersection, labels checked equal | a data-clean-up row-count mismatch posing as model difference (OWN_TAG check 4 precedent) |
| floor -0.005 plus CI upper < 0 plus BH | a significant-but-useless CRPS delta; p-hacking across the two seasons |
| P1 on 2023 with the "tuned-on" caveat in the text | reading an in-sample season as independent evidence; closure is the default |
| P3 threshold log loss | CRPS gains that are shape artefacts and do not move P(>= N) (the thin upper tail) |
| P4 calibration guards | a sharper-but-miscalibrated head winning CRPS by luck |
| P5 postseason and role slices | the postseason reversal and low-minute concentration found by the red team |
| P6 minutes >= 5 no-harm | a gain located only on rows nobody can bet (under-5-minute games are not knowable at T-60) |
| P7 team share | one quirky team (PHI was 33%) carrying the result |
| P8 proxy-safe subset | the frozen A1 frame gates reports at a 19:00 ET proxy; for early tips that admits post-tip reports and flatters A1 (A0 uses real tips) |
| gate computes no A1 - A0 | shaping the rule after seeing the contrast |

## Minimum data checks (fail = stop)
(1) A0 reproduces the stored lower-tail pts OOF CRPS to 1e-6 on identical keys. (2) A1 reproduces the red team's fair 2024 delta vs
v1_prod to 1e-4 on identical keys (stored hybrid_lf and v1_prod checkpoints, both arms on integer support). (3) Missingness audit of
every A1 pts feature by tonight's-minutes bucket (<5, 5-20, >20): no NULL rate differs by more than 2 pp across buckets [as amended pre-freeze, below]. (4) Planted-future
and planted-same-game tests on the A1 feature build. (5) Row keys identical across arms [as amended pre-freeze, below]. (6) [added pre-freeze] enough DraftKings T-60 pts
main-line rows (>= 5,000 per season) inside the scored intersection for the secondary metric.

### Gate measurement: Measured 2026-10-11 16:28 CT by `gate --allow-draft` (reports/prereg_xgb_pts_vs_lt/gate.json; real data, read-only, season <= 2024; no arm fitted; no A1 - A0 computed)
1. **PASS.** A0 reproduces the stored lower-tail components EXACTLY (delta 0.0 on all four cells): 2023 mixture 3.140236466 / production(off) 3.152829990 (n 27,619); 2024 mixture 3.176839517 / off 3.187552574 (n 27,701). Caveat: the stored components were collected today; this check certifies the harness path from components to the stored numbers, not a fresh walk-forward of production (the OWN_TAG gate reproduced `off` from a fresh production refit on 2026-10-11).
2. **PASS.** Stored `hybrid_lf` and `v1_prod` pts checkpoints, both on integer support, game-clustered (2000, seed 0): 2024 -0.0078 [-0.0127, -0.0027] (n 27,583; red team -0.0078229, |diff| 2.2e-07); 2023 -0.0145 [-0.0203, -0.0086] (n 27,619; red team -0.0144985, |diff| 1.3e-07). `to_integer_support` equals the red team's `ceil(q - 0.5)` exactly (max abs diff 0.0). Descriptive, stored: red-team `fixsched` (A1 with the postseason look-ahead columns neutralised) minus `hybrid_lf` = +0.0002 [+0.0000, +0.0004] (2023), -0.0003 [-0.0005, -0.0001] (2024); `v1only` (A2) minus `v1_prod` = -0.0066 [-0.0123, -0.0010] / -0.0055 [-0.0099, -0.0010]; `v1only` minus `hybrid_lf` = +0.0079 [+0.0055, +0.0103] / +0.0023 [+0.0003, +0.0042].
3. **FAIL AS DRAFTED; PASS AS AMENDED (pre-freeze amendment, below).** 95 A1 pts feature columns; frame 80,312 rows, buckets of tonight's minutes <5 / 5-20 / >20 = 6,435 / 24,272 / 49,605 (scored 2023-24 rows 4,687 / 16,808 / 33,707). Max NULL-rate gap 19.74 pp = `yrs_since_draft` (NULL 30.5% / 23.0% / 10.8%): a static draft fact, NULL status constant within player (undrafted and international players), so it cannot carry tonight's minutes beyond who the player is; it is the one flag the red team also found. Second: `rg_f3b8321a_p_pts` and `rg_f3b8321a_o_pts`, 2.75 pp over the whole frame (NULL 3.4% / 5.9% / 6.2%), NULL status constant within calendar month (the 2022 ridge warm-up months), 0.0 pp on the scored 2023-24 rows. Every other column <= 0.07 pp.
   **PRE-FREEZE AMENDMENT to check 3 (was: "no feature's NULL rate differs by more than 2 pp across buckets"):** a column is exempt iff its NULL status is constant within player or constant within calendar month (computed by the harness, never by name); every non-exempt column must stay within 2 pp across the three buckets, over the whole frame. Amended result: 0 non-exempt columns above 2 pp. Protects against: the leak that bit before (`ridge_*`, NULL iff tonight's minutes < 5) is neither player- nor month-constant, so it would not be exempt.
4. **PASS** (both builder settings). The real builders (v1 + v2 groups + the leak-free ridge for the 2023-12 and 2024-01 months) are run twice, the second time with every box line, minute count, starter flag, final score and possession count of the 2,049 games dated on or after 2024-01-15 randomised and a planted post-tip 'Out' report (23:30) for every player of the 11 games on 2024-01-15. All 95 columns compared (none missing from the rebuilt frame). Planted future: 40,334 rows dated before 2024-01-15 unchanged except float-summation noise <= 2.3e-16 relative in `ret_*`/`vac_*`. Planted same game: the 229 rows of 2024-01-15 itself unchanged exactly. Not inert: `m10_pts`, `pr_usage`, `n48_pts`, `opp_pos_pts`, `game_total_feat` change on 98.9-100% of rows after the date. Report gate set to `proxy19` (the frozen frame's setting) and to `real`: same result. Limit: a label-scramble test cannot see the postseason schedule look-ahead (`sch_days_to_next`, `sch_b2b_first` depend on whether a next game exists); it is known, bounded by the red team at 0.0005, and the stored `fixsched` arm is reported as a descriptive contrast.
5. **FAIL AS DRAFTED; PASS AS AMENDED (pre-freeze amendment, below).** 2023 key sets identical (27,619). 2024: A0 27,701 vs frozen frame 27,583; 118 A0-only keys in 10 games (0.426% of A0; including 0022400147, 0022400621, 0022400633, 0022401229, 0022401230, restored by the 2026-10-11 clean-up), 0 frame-only keys. A1 and A2 share one frame, so they are identical by construction. Labels agree on every shared key.
   **PRE-FREEZE AMENDMENT to check 5 (was: "identical row keys across arms"):** every contrast is scored on the per-season key intersection of A0 and the frozen frame; the frame's keys must all be in A0's, labels must agree on shared keys, and the A0-only keys must be at most 0.5% of A0's rows in each season. Observed 0.426%. The 0.5% bound is a round number above the observed share, chosen before any contrast exists; if Devin prefers, regenerating the export on the current DB is the alternative and is a heavy rebuild of ctxres_v2 and ridge_v2.
6. **PASS.** DraftKings T-60 pts main-line rows inside the scored intersection: 14,354 (2023), 15,882 (2024); one x.5 line per player-game, no pushes. The subset is cached at `data/ops/xgb_pts_vs_lt_cache/dk_t60_pts.parquet` and pinned below (source: the ODDS_HISTORY pairs frames built 2026-10-10).
Context for P8: 11.4% (2023) and 12.2% (2024) of scored rows are in games whose real tip is before 19:00 ET (0 missing tips); for those games the frozen frame's 19:00 ET proxy admits reports stamped after the real T-60 cutoff.

## Power (from the gate; nothing about A1 - A0)
From the paired A1 - v1_prod comparison on the same rows (the closest paired structure available without computing A1 - A0): 2024 game-clustered SE 0.0025, 80%-power minimum detectable effect 0.0071; 2023 SE 0.0030, MDE 0.0084. The floor (0.005) is below the MDE: a true effect exactly at the floor passes the CI condition in a given season about half the time, a true effect of -0.0075 about 85-90%. Consequence: a CLOSED verdict from this rule means "no effect of practical size demonstrated"; it cannot rule out a true effect near -0.005. This is why the floor, not the p-value, carries the decision and why the prior above matters.

## Kill criteria
No pass on 2023 (P1): closed, no follow-up, no re-encodings, no feature or tuning variant. A pass on both seasons with any other condition
failing: closed, reported with the failing condition; one pre-registered follow-up at most (it must be a new id). A full pass: a shadow
arm in the live log beside production, scored under FORWARD_PREREG_2026_27; promotion is Devin's and needs the live log.

## 2025 touch
None under this rule. The A1 model has never been evaluated on 2025 (it is eligible for its one touch), but this rule does not authorise it;
a touch needs a separate logged row, committed before any run.

## Frozen inputs (sha256, recomputed and compared by `run`)
- input base_config.json: 000764e10bccc3fed6620a40b0a680f143066b6a318d589653119eba15a58133
- input candidates.json: eb778381b034320de6cd2dc13b37ce1e664221dad2dea4c83094a199cd34431d
- input components_pi.npz: 0d3fdf4b9c68e0947d84d8bf17d8441d589813ea0c5f472d8bc2da1f3cd4614b
- input components_pts.npz: 1d38ac0ca7188b8c32c604169f87e7fdb47ed7ce759da1a1f0fad9bd60805269
- input ctxres_v2.parquet: 6586f4aff1d6f060dd69e8efb214478fabf13c562ad109cc7f610ad576a217d6
- input dk_t60_pts.parquet: c53ba62e1acf49e7eea20223c05830a92dae1a5ad0b3380c3a6d88976ccdce38
- input ridge_v2.parquet: e6b70007311b2b6d552a4826957fde918b54f2a296ef3ae99416db4dc93e8776
- input units_fixsched_pts: 069d96d4699fd0571e10b2f19061ccc33f0051b99c3f97cb270652689175edd0
- input units_hybrid_lf_pts: d937c78299a346fe4aa16697cce57b0d1367f6d7e82fbb4d9c28c381dd351d6d
- input units_v1_prod_pts: 53c394597f982de9878da76e44fb8ad7225a66ebd419cdd84c3347255ee49ab4
- input units_v1only_pts: 50e002ae0fc14101d826b9362f40013be4ba9af4e0b5f43fdb36678bbb54f4d1

## Unstated details (fixed before scoring)
* A0 = the stored lower-tail components (reports/lower_tail/components_pts.npz + components_pi.npz, collected 2026-10-11 on the current DB) pushed through the production arm_grid('mixture') and to_integer_support; no refit. The three input files are pinned by sha256 in the document.
* A1/A2 rows = the frozen ctxres_v2/ridge_v2 export of 2026-10-08/09 (2023 n 27,619; 2024 n 27,583). The 2026-10-11 data clean-up added 118 scored rows (10 games) to the 2024 production set that the export does not contain. Every contrast is scored on the key INTERSECTION per season; the dropped A0-only keys are counted and bounded (<= 0.5% of A0 rows).
* integer support for every arm: to_integer_support(q) = max(ceil(q - 0.5), 0) then a running maximum along the grid, on the 199-point grid (CRPS_TAUS), scored by crps_from_quantiles (the engine's crps_rows is the same formula).
* A1 = the engine arm 'ref_fixed' (v1 + v2 groups a-h,j + the leak-free rg_f3b8321a ridge columns; 95 pts columns), reg:quantileerror at 19 levels, ext_tails to the 199 grid, XGBoost 3.4.1 CPU hist, seed 20261008, n_jobs 4. A2 = same recipe on the 42 v1 columns (red team 'v1only').
* the engine file has been moved by the research/ restructure (two docstring path strings differ); the pin in the job (4952cc9c) is verified against the engine with those two strings reverted, then the pin is replaced by the current file's sha for the call (code is byte-identical otherwise).
* the frozen A1 frame still carries the postseason look-ahead in sch_days_to_next / sch_b2b_first (red team: effect <= 0.0005) and the 19:00 ET report-time proxy; both are descriptive-flagged, the proxy through the proxy-safe subset guard (games with a real tip at or after 19:00 ET).
* main lines: DraftKings, snapshot_kind t60, stat pts, one line per (game, player), x.5 lines only (status 0 = no pushes), from the ODDS_HISTORY pairs frame; only rows whose key is in the scored intersection count. P(over) = share of the 199 integer quantiles >= ceil(line); log loss clipped to [0.5/199, 1 - 0.5/199] (the lower-tail convention).
* bootstrap: game-clustered, 2000 resamples, seed 0, via paired_score_delta_ci / boot_p_value (the production helpers); BH over the two-member family {A1 2023, A1 2024}.
* minutes buckets for the missingness audit: <5 (including DNP / NULL), 5-20, >20 of tonight's stored minutes; rows = the whole frozen frame (2022-2024) and, separately, the 2023-2024 scored rows.
* starter / bench = starter10 >= 0.5 (pre-tip role, as add_slice_columns); first15 = team_game_no < 15; postseason = game_id not starting with '002' (playoffs and play-in); minutes >= 5 uses tonight's realised minutes (a post-hoc stratifier, never an input).
* planted tests rebuild v1 + v2 + two ridge months through the real builders with report tip_source='proxy19' (the setting of the frozen frame) and, separately, 'real'; tolerance 1e-12 relative for float-summation columns.
* the gate stage computes NO A1 - A0 quantity of any kind and no threshold log loss of A1; the harness `run` refuses a draft document, a wrong hash, a non-FROZEN status line, a gate record that is a draft or failed, and any stored input whose sha256 differs from the pins below.
* the run takes `data/ops/heavy.lock` (`mkdir`, retry 120 s, `rmdir` in `finally`); refits are checkpointed per block under `data/ops/xgb_pts_vs_lt_cache/units/` and resume.

## Outputs (exact)
`reports/prereg_xgb_pts_vs_lt/gate.json`, `smoke.json`, `results.json`, `ledger_rows.md` (T### placeholders; rows drafted, not appended),
`reports/prereg_xgb_pts_vs_lt.md`. Code under `research/` only.

## Maintainer commands
```
cd /Users/devin/Downloads/nba-prediction
M="-m research.eval.xgb_pts_vs_lt_eval"
DOC=docs/prereg/XGB_PTS_VS_LT.md
SHA=$(awk '/^=== RESULTS BELOW ===$/{exit} {print}' $DOC | shasum -a 256 | cut -d' ' -f1)
uv run --offline --with xgboost python -W ignore $M smoke  --doc $DOC --sha $SHA   # two blocks per arm, ~3 min; compares to stored units
uv run --offline --with xgboost python -W ignore $M run    --doc $DOC --sha $SHA   # gate again, then refit A1 and A2 (18 blocks each), score
uv run --offline --with xgboost python -W ignore $M report --doc $DOC --sha $SHA   # reports/prereg_xgb_pts_vs_lt.md and ledger_rows.md
```
Estimated CPU wall time of `run`: about 25 minutes on an idle machine with 4 threads (A1 about 13 min and A2 about 8 min from the per-block file times of run_20261009 and the red-team v1only log, plus about 2-4 min for loading, the gate and the bootstraps); allow 35-40 minutes if the box swaps. Start outside the xx:15-25 and xx:45-55 pre-tip windows. `run` and `smoke` take `data/ops/heavy.lock`. xgboost is supplied by `uv run --offline --with xgboost` (cached 3.4.1, the version of run_20261009); no GPU is needed.

=== RESULTS BELOW ===
