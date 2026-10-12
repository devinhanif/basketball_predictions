# NEAR_MISS_BUNDLE: one combined candidate per stat from the three fixed near-miss members (pre-registration)

Status: **DRAFT, superseded by ROUTER_G2 before freeze (main session, DECISIONS 6b1c782). Never frozen, never run.** Original status: DRAFT (Mon Oct 12 2026), modeler; to be frozen only after PI_RECENT_ROLE had its verdict.
BEFORE any arm is fitted (Devin, 2026-10-12: "lets do that, in ur order", then "run, run, run": freeze and run as soon as the gates pass, after RECENT_ROLE has run).
Everything above the line `=== RESULTS BELOW ===` is the rule. Freeze evidence once committed: the commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/NEAR_MISS_BUNDLE.md | shasum -a 256` (first 8 hex into the results and every ledger row).
Season labels are `games.season` (2023 = 2023-24 select, 2024 = 2024-25 report, 2025 = frozen holdout, never loaded). No bundle arm has been scored.
Membership is fixed by `docs/DECISIONS.md` (2026-10-12 00:23, commit b91893e); this document may not add or remove a member.

## What Devin said
> "lets do that, in ur order" (2026-10-12), on testing the near-miss ideas together against production once, after RECENT_ROLE; then "run, run, run".

## Honest prior (read first)
* Information, not architecture, has won here. The three members are a sequence model, a minutes-gated tree router and workload columns. Two are architecture; the third
  (last-game and recent minutes, renamed "recent-role minutes" by the adversary) is information production partly holds. Every one of them tied or failed its own rule.
* Where each one failed, and why a bundle may not repair it:
  * E2 SEQ blend, learned gate (T442): pts -0.0110 / -0.0131, reb -0.0028 / -0.0043, ast -0.0016 / -0.0025, fg3m -0.0027 / -0.0032. It failed calibration (5% quantile covers
    7.5% in 2023) and slices: realised minutes < 5 **+0.055 in 2024** and postseason **+0.016**. The member that carries the gain is the one that fails the guards.
  * E1 router, pts (T440): -0.0053 / -0.0012. Fails the floor in 2024 and the CI in both seasons.
  * LOAD columns, pts (T452, AL_LOAD arm): -0.0034 [-0.0072, +0.0001] / -0.0065; the 2023 within-player-season placebo matched the 27-column candidate. reb / ast / fg3m
    (27-column arm): -0.0017 / -0.0046, -0.0020 / -0.0022, -0.0005 / -0.0004.
* My estimate that any stat passes every condition: **about 5%** (pts about 4%, the others about 1%: their members cannot reach -0.005 together unless SEQ and LOAD are
  nearly additive, and the 80% flag below stops a SEQ-carried result from being a pass). A likely outcome is "bundle better than A0, SEQ carries it, guards fail", which is
  a clean negative that closes this family. If a stat survives, the claim is named for the member that carries it, not for the bundle.
* Order of questions this rule answers: does the combination clear the floor on integer support; does every added input beat its own past-only placebo; does the retrained
  pipeline stay at zero when labels carry no information; does one member carry it.

## Hypothesis
H1: the bundle (production + LOAD columns, routed with E1's g on pts, blended with SEQ by a walk-forward learned gate) lowers integer-support CRPS against A0 =
production + lower-tail mixture by at least 0.005 in both seasons, in at least one of {pts, reb, ast, fg3m}, with every guard holding. Null: no stat does.
Protects against: crediting a combination for one member's gain (knockout and 80% flag), for a refit artefact (placebo, shuffle), or for a gate that saw the future.

## Members (fixed; not negotiable here)
1. **SEQ**: ENSEMBLE E2 sequence transformer, stored out-of-fold forecasts `data/colab/runs/seq_ensemble/20261011_215342/oof_E2.npz` (walk-forward, run under frozen sha e26b1657), all four stats.
2. **ROUTER**: ENSEMBLE E1, pts only: pmf mixture `g * A1 + (1 - g) * (first-stage grid)`, A1 = stored XGBoost grid `data/ensemble/a1_pts_aligned.npz`, g = P(minutes >= 5 | plays)
   from the production minutes path (`data/ensemble/g_pts.npz`). g is a parameter-free as-of function; there is nothing to fit.
3. **LOAD**: the 15 WORKLOAD LOAD columns (LOAD_MIN 8 + LOAD_TRACK 7, `research/workload/asof.py`, prefix `wl_`) appended to the production props model through
   `stat_feature_names`, then the same lower-tail mixture as A0 (workload_eval arm `AL_LOAD`), all four stats. Reading: the 15-column LOAD family, not the 27-column B arm,
   because the review and the knockouts named LOAD as the carrier. Cost of that reading: the AL_LOAD pts number (-0.0034 / -0.0065) is weaker than B's (-0.0071 / -0.0082).

## The bundle (declared order; each stage's weights depend on earlier data only)
1. **LOAD**: `L` = production + 15 LOAD columns -> mixture -> integer support (month-block walk-forward LightGBM, as A0; 2022 warm-up).
2. **ROUTER** (pts): `R` = pmf mixture `g * A1 + (1 - g) * L` on the integer grid. The 118 pts rows (all 2024) absent from the XGBoost export take weight 0 (R = L). Other stats: `R = L`.
3. **BLEND**: `B` = walk-forward softmax gate over [R, SEQ-E2]: 30-day blocks, a block's gate fit on rows dated strictly before it, at least 3000 earlier rows, gate context
   `log1p(n_prior), starter10, n_rep_out, has_report, team_game_no` (ENSEMBLE E2's), rows before the first fit weigh 0.5 / 0.5; pooled 199-grid -> integer support `ceil(q - 0.5)`.
   (Re-implemented from `ensemble_eval.learned_pool` so weights keep the input row order; reproduces T442 to the last digit.)
Comparator everywhere: **A0** (production + lower-tail mixture, integer support), the same 55,320 rows (27,619 / 27,701), same DNP handling and window.

## Baseline A0: two alternatives, chosen by a condition fixed here (before any result)
* **A0-current** (default): the stored lower-tail mixture in `reports/lower_tail/` (pts 3.140236 / 3.176840).
* **A0-PI**: the lower-tail mixture refit with the 5 recent-role columns in the pi model, in a directory with the same `components_*.npz` schema, used **if and only if** the
  PI_RECENT_ROLE verdict is PASS in a committed ledger row before this rule is frozen; otherwise A0-current. The freeze commit names which one. Selected by
  `--baseline-dir <dir>` (omitted = current). Under A0-PI every arm (LOAD, shuffle bases, refit-base reproduction) is rebuilt on that mixture, caches and outputs get a
  `_<dirname>` suffix, and the stored-reference gates (G1 numbers, G3 member reproduction against the ledger) are replaced by alignment, season-bound and the refit-base
  reproduction against the A0-PI components themselves. The stored members (SEQ, XGBoost, g) are unchanged; they were built against A0-current, which is a stated limit.
  Nothing else in the rule changes with the baseline; there is one verdict per baseline run, and only the chosen one is run.

## Primary metric, floor, inference
Integer-support CRPS (199-grid, `crps_from_quantiles`), paired difference bundle minus A0 per row, 95% percentile bootstrap **clustered by `game_id`** (2000 draws, seed 0),
two-sided clustered-bootstrap p. **Floor -0.005** per stat (house props floor). Family: **m = 4** (pts, reb, ast, fg3m), Benjamini-Hochberg q = 0.05, computed separately for
2023 and 2024. Select 2023, report 2024; **both seasons** must clear point <= -0.005, CI upper < 0 and BH p < 0.05. Season 2025 is never loaded.

## Pass rule (per stat; all must hold; otherwise no pass)
1. Core (above), both seasons.
2. Guards, both seasons, as ENSEMBLE had them, plus minutes < 5: calibration (|bias| <= 0.5, 80% coverage in [0.75, 0.85], lower-tail guard: coverage within 0.02 of nominal at
   0.10 / 0.20 / 0.80 / 0.90 / 0.95); no slice with n >= 300 worse than A0 by more than +0.01 among **regular season, postseason (game_id not 002), starter, bench, cold start
   (n_prior < 20), first 15 team games, teammate OUT, realised minutes < 5**; concentration (no team carries more than 25% of the summed gain and the delta without the
   largest team is <= -0.0025); pts only: DK T-60 threshold log loss within +0.002 at the main lines and +0.005 at fixed 20 / 25 / 30, and the gain on rows with a tip hour
   >= 19 ET (proxy-safe slice) <= -0.005 (E1-like, as ENSEMBLE).
3. **Placebo for every added input** (past-only, defined below): bundle minus bundle-with-that-input's-placebo <= -0.001 with CI upper < 0, in EACH season, for LOAD, SEQ and
   (pts) ROUTER. The placebo is judged in both seasons (WORKLOAD's pts pass rested on 2024 only; that reading is retired).
4. **Shuffled-label check that retrains and can fail**: SHUF-M and SHUF-C below, both seasons, effect must stay above **-0.0025** (half the floor).
5. **Knockout and carrier flag**: per member, the bundle with the member removed and the member alone added to production. If any member alone keeps >= 80% of the bundle's gain
   (2023 or 2024), or removing any member leaves <= 20% of it, the stat is **FLAG_ONE_MEMBER**: not a pass until the adversary clears it, and the claim is named for that member.
6. Reproduction gates of the refit pipeline (below) held at the run, or the stat is a FAIL.

A PASS means: a red-team review and a drafted one-touch holdout row. Nothing is promoted or wired in. A holdout touch needs Devin's word and a committed
`docs/HOLDOUT_ACCESS_LOG.md` row marked `PRE-REGISTERED` and `DEVIN CONFIRMED` naming `NEAR_MISS_BUNDLE` and the sha8 BEFORE any run; the `holdout` stage refuses otherwise and,
even then, contains no 2025 loader (the touch is separate code written after the row). Verdicts: PASS, FLAG_ONE_MEMBER, FAIL_GUARD, FAIL, HARM (delta >= +0.005 with CI lower > 0).

## Guards in detail (fixed constructions; see docs/reviews/workload_review_2026-10-12.md)
* **Past-only placebo.** Each row takes the vector of ONE uniformly drawn **strictly earlier** row of the same player in the frame (LOAD: the 15 columns jointly, in the
  props frame 2022-24, then the model is **refit** on them; ROUTER: the stored A1 grid; SEQ: the stored SEQ grid). A player's first row draws from a strictly earlier row of any
  player; rows on the earliest date keep their own (45 of 84,170 frame rows, on the earliest date). Seed 0 (+1, +2 for the stored members). Replaces the full-season
  within-player permutation, which put future values on earlier rows.
* **SHUF-M** (model layer, retrained): production and production + LOAD are refit with the SAME permutation of the training residuals (`collect_components(shuffle=True)`, one
  permutation per month block, shared by both arms) and scored on the REAL labels; the effect (LOAD' minus production') must stay above -0.0025 per stat and season. Both arms are
  shuffled, which the WORKLOAD check was not (its base was unshuffled and trained, so it could not fail).
* **SHUF-C** (combination layer): labels are drawn from **A0's own predictive distribution** (one uniform rank per row on its integer equal-mass grid), so A0 is the truth and no
  member can know more. Every stored member is left as is, the gate is refit on those labels, and the bundle is scored against A0 on them. A gain beyond 0.0025 can only come from
  the pipeline (an in-sample gate, a peeking router). **Why not permuted labels:** the unit test showed that with permuted labels any arm whose forecast locations are tighter
  than A0's "wins" by -0.15 on a synthetic world with no leak, so that null would void a genuine result. SEQ (Colab, 22.5 min on a T4) and XGBoost cannot be retrained here; that is
  stated, not hidden, and the unit test plants a peeking blend and shows SHUF-C fails it.
* **Knockout / alone** arms: K_LOAD (L replaced by production's grid), K_ROUTER (pts; no router), K_SEQ (no blend = R); AL_LOAD (= L), AL_ROUTER (router on production), AL_SEQ (E2 learned).
* **Secondary, cannot rescue a failure**: decision score at DraftKings T-60 prices (`docs/DECISION_SCORING.md`, legs from `data/ops/model_clv_pairs`, A0 versus bundle, thresholds
  0 / 2 / 5 / 10%, "insufficient sample" below 300 bets); bundle minus its best single member; SEQ share of the final gate by season.

## Slices (descriptive plus the guard; n >= 300)
regular / postseason / starter / bench / cold start / first 15 team games / teammate OUT / realised minutes < 5 / 5-20 / > 20 / tip >= 19 ET. (Realised-minute slices stratify
where a gain sits; they are never an input.)

## What each choice protects against
| choice | protects against |
|---|---|
| membership fixed in DECISIONS before any further look | choosing members after seeing which combination wins |
| declared order, weights from earlier data only, planted-future-label test | a gate that learns from the block it scores |
| A0 is the comparator for every arm; refit-base must reproduce A0 (1e-6) | a refit pipeline that differs from the stored A0 and calls the difference a gain |
| past-only placebo per added input, refit, both seasons | capacity or structure credited to information; the WORKLOAD 2023 placebo tie |
| SHUF-M retrained on both arms, SHUF-C in the A0-null world, with a unit test that fails a planted leak | an inert check that cannot fail (WORKLOAD), and a null that rewards tighter forecasts |
| slice guards incl. minutes < 5 and postseason | the E2 failure (+0.055 on minutes < 5, +0.016 postseason) |
| knockout and 80% flag | the `opp_adjusted_ridge` fingerprint: one member carrying a bundle |
| integer grid for every arm | the T114-T118 artefact |
| BH over 4 stats, floor -0.005, CI upper < 0, clustered by game | four chances to win; a p-value on a gain nobody could use; row-level CIs |
| holdout stage refuses without a committed row and has no loader | an accidental 2025 touch |

## Kill criteria
* A reproduction gate fails at the run (refit base != stored A0, LOAD null gap > 2 pp): stop before any candidate arm; the fix is a new id.
* A planted-future or shuffle control that cannot fail: stop; a leak is reported, nothing is read.
* No stat passes: closed. No other members, orders, windows, placebo draws, margins or gates afterwards. At most ONE pre-registered follow-up (new id), only after a PASS.
* Season 2025: no touch is requested by this rule.

## Minimum-data gates (non-negotiable 8), measured Mon Oct 12 2026 (CDT) on the stored artefacts and `nba.duckdb` (read-only); no bundle arm scored
Reproduce: `.venv/bin/python -W ignore -m research.eval.near_miss_bundle_eval gate --refit-repro pts,reb,ast,fg3m --recompute-g` (about 3 minutes; the refit part takes `heavy.lock`;
JSON in `reports/prereg_near_miss_bundle/gate.json`). Limits were set before the numbers were read except where noted.
* **G1 A0 reproduction: PASS.** 8 of 8 cells match the WORKLOAD gate to 1e-6 with n 27,619 / 27,701 (pts 3.140236 / 3.176840, reb 1.288179 / 1.311401, ast 0.901954 / 0.898517,
  fg3m 0.566212 / 0.590155); the four stats share one row set; max season 2024.
* **G2 alignment: PASS.** The stored AL_LOAD pts arm (WORKLOAD cache) has exactly the A0 keys and labels; the SEQ E2 output covers 55,320 of 55,320 A0 rows for every stat; the XGBoost
  export covers 55,202 (118 absent, all 2024, 0.21%); absence by realised-minutes bucket 0.47 / 0.22 / 0.17% (gap 0.29 pp, limit 2 pp); g finite in [0, 1] on every row.
* **G3 members reproduce: PASS.** E1 pts -0.005313 / -0.001244 equals the stored `e1_results.json` to the last digit (n 27,619 / 27,583: the 118 absent rows are not in E1's
  own scoring); E2 learned, all four stats, equals `score_results.json` to the last digit (pts -0.010954 / -0.013140, reb -0.002789 / -0.004287, ast -0.001577 / -0.002508, fg3m
  -0.002728 / -0.003160); AL_LOAD pts -0.003424 / -0.006491 equals the WORKLOAD number. So the artefacts the bundle reads are the ones the ledger rows describe.
* **G4 production-minutes drift (COACH_PHASE, T450): handled by using only artefacts built after the 2026-10-11 clean-up.** The drift (up to 1.53 min in 2024 from game 0022400155)
  is against the 2026-10-09 table. This rule reads, all stored after the 15:08 re-baseline of the lower-tail components: `reports/lower_tail/components_*.npz` (15:08; A0, reproduces
  to 0.0), `data/ensemble/g_pts.npz` (21:52), the XGBoost export (21:22), SEQ E2 (trained on the 21:47 export built from the cleaned database; OOF file time 21:58 on this Mac; I could not verify the Colab VM's own clock), AL_LOAD pts (00:08). g was also
  **recomputed from the current database and equals the stored g exactly** (max |diff| 0.0 in both seasons, 55,320 rows). Not used: `reports/minutes_v2/*` (2026-10-09 table).
* **G5 power: PASS.** Estimated MDE (half the 95% clustered CI width of the E2-learned delta, the member with the most variance) is pts 0.0039 / 0.0039, reb 0.0013 / 0.0012, ast
  0.0008 / 0.0007, fg3m 0.0007 / 0.0008, all below the 0.005 floor, so a floor-sized effect is detectable; a smaller true effect is not, and no claim is made for it.
* **G6 guards are testable: PASS.** A0 itself passes the absolute calibration guard in all 8 cells (pts coverage at the 5% quantile 6.2 / 6.3%, within the 2-point tolerance), so the
  absolute guard is not impossible by construction. Guard-slice sizes in both seasons are all >= 1,325 (minutes < 5: 2,383 / 2,326; postseason 1,804 / 1,934; cold start 1,630 / 1,325).
  DK T-60 pts rows: 14,354 / 15,882.
* **G7 gate warm-up.** With 3000 minimum earlier rows the first fit is at the second 30-day block of the 2023-24 season: 4,482 2023 rows weigh 0.5 / 0.5 and none in 2024 (a mild handicap to the select season,
  identical to ENSEMBLE E2).
* **G8 refit pipeline reproduces A0: PASS for all four stats, and enforced again at the start of `arms`.** The no-extra-column refit equals the stored A0 exactly (diff 0.0 in both
  seasons, 100% of rows identical, pts / reb / ast / fg3m; about 24 s per stat). WORKLOAD never checked this (its base was the stored A0, not a refit).
  LOAD columns: NaN rate by realised-minutes bucket, max gap 0.10 pp (limit 2 pp); tracked-distance windows are the only columns with NaN (0.07-0.19%).
* **G9 planted-future and can-fail controls**: 18 unit tests on a synthetic world: past-only placebo never reads a row dated on or after the target's; the blend's weights for rows
  before a cut date are byte-identical when every later label is replaced by 99; a peeking blend fails SHUF-C (effect far below -0.0025) while the honest pipeline does not;
  freeze and holdout refusals; membership constants.

## Unstated details chosen before scoring
LOAD = 15 columns not 27; ROUTER's first stage is L not A0; the 118 pts rows without an A1 row use L; the gate's context columns and 30 / 3000 settings are ENSEMBLE E2's; placebo seeds
0 / 1 / 2; placebo margin -0.001 (a fifth of the floor: with three members layered, WORKLOAD's -0.003 for a lone input is not attainable by an additive small member); shuffle threshold
-0.0025 (half the floor, KO_FLOOR); "playoffs" = game_id not 002 (includes play-in); per-stat BH within season; the flag is computed on both seasons; unit tests, not real data,
establish that the shuffled-label check can fail.

## Outputs and harness
`research/eval/near_miss_bundle_eval.py` (stages `gate`, `arms`, `score`, `run`, `holdout`), tests `tests/research/near_miss_bundle/`. Cache `data/ops/near_miss_bundle_cache/`
(per stat and arm: base, L, PL_L, SH_base, SH_L; resumable), outputs `reports/prereg_near_miss_bundle/{gate,repro,results_partial,results}.json` and
`reports/prereg_near_miss_bundle.md`.
Exact commands after the freeze (the maintainer; the harness takes `data/ops/heavy.lock` itself in `arms`, retry 120 s, pid file, removed only by its creator; `score` is numpy only):
```
M="-m research.eval.near_miss_bundle_eval"
.venv/bin/python -W ignore $M gate --refit-repro pts,reb,ast,fg3m --recompute-g      # re-records the frozen sha; about 3 min
.venv/bin/python -W ignore $M run --sha <64 hex> [--baseline-dir <PI_RECENT_ROLE dir>]   # arms -> score -> report (flag only if the condition above holds)
```
Runtime estimate (Mac CPU, one process, about 2-3 GB): the frame and workload columns about 1-2 min; 20 LightGBM walk-forwards (5 per stat; the stored-base pts fit took 24 s)
about 10-20 min; scoring about 1.5-3 min per stat (a pts build of all 13 pipeline variants took 19 s) plus the decision score; total about 25-35 minutes, per-arm progress printed,
per-arm checkpoints, so a killed run resumes.

## Questions for Devin before the freeze (safe defaults chosen)
1. LOAD means the 15-column family (default) rather than the 27-column B arm. B is stronger on pts but includes schedule and travel columns whose knockouts did nothing.
2. A FLAG_ONE_MEMBER stat is not a pass until the adversary clears it (default, your rule from WORKLOAD). Expect SEQ to trigger it on pts.
3. Placebo margin -0.001 and shuffle threshold -0.0025 are my choices; say so if you want other values before the freeze.

=== RESULTS BELOW ===

(not run)
