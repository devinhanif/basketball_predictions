# QUESTIONABLE_CASCADE: does the report's Questionable / Doubtful status, as a categorical input, move the props of the tagged player and of his teammates? (pre-registration)

Status: **DRAFT, written by the modeler Wed Oct 21 2026 11:20-12:10 CDT (clock read by `date`; priors committed 11:25 CDT in 3940016, before any gate number), branch `agent/modeler-questionable-cascade`, base main 65db073. NOT FROZEN, and NOT FREEZABLE AS WRITTEN: the minimum-data gate FAILS on the report season (section 9).** Model class: gradient-boosted trees (the LightGBM context-residual heads of RECENT_ROLE_FULL, T467) read through the stored lower-tail mixture.
- **Advice or momentum.** Devin's request (2026-10-21 ~11:20: "do something to coin flip", "making certain things categorical that are features"). It goes AGAINST the modeler's own earlier recommendation (docs/research/questionable_status_scope_2026-10-15.md, "Do not draft a new historical test on Q/D/P now": a historical test cannot resolve effects below about 0.1 CRPS on the tagged player's own rows). That advice still holds for the own-row half; the teammate half (the cascade) is new and has more rows. Log as Devin's override in DECISIONS if drafted further.
- **How it becomes the rule:** the main session commits `prereg(QUESTIONABLE_CASCADE): <sha256>` of everything above the line `=== RESULTS BELOW ===`, with the status line changed from DRAFT to FROZEN in the same commit (the harness refuses a status line that does not start with FROZEN). Hash: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/QUESTIONABLE_CASCADE.md | shasum -a 256`. Not before Devin picks a design from section 9 and the adversary has read it.
- **Season labels** are `games.season`: 2023 = 2023-24 (select), 2024 = 2024-25 (report). 2025 = 2025-26 is the frozen holdout and this rule never touches it.
- **No outcome-scored comparison of any arm has been run.** Label uses in the gate, all disclosed: (1) the play flag (minutes > 0) of Questionable/Doubtful players on the SELECT season, to measure r_status; (2) realised minutes to bucket the missingness audit and one descriptive ratio (section 9, G7); (3) the stored FULL-minus-A0 per-row CRPS deltas (already frozen and scored, T467) for per-row sd and design effect only. The synthetic rehearsal never loads `row_y`.

## 1. The rule in eight lines
1. Arms, all walk-forward month blocks through the stored `pi` / `w_q` exactly as RECENT_ROLE_FULL (2022 warm-up): **A0** = production's stored heads; **C1** = production features plus the tagged player's own status code and reason class (2 columns); **C2** = C1 plus the teammate cascade (7 columns, section 4); **PL** = C2's nine columns moved jointly among the rows of the same game date (placebo); **CTRL** = A0 refit (must reproduce A0 to 1e-6 before any C arm is built).
2. Rows (stored A0 rows: played, `n_prior >= 5`): **TM** (affected teammate) = the row's player is not himself Questionable/Doubtful and at least one other ROTATION teammate is; **OWN** = the row's player is Questionable or Doubtful himself (and played); **REST** = everything else (including games with no usable snapshot).
3. Primary, per stat (pts, reb, ast, fg3m), report season 2024: integer CRPS of **C2 minus A0 on TM rows**, floor **-0.005** (the ledger standard), paired, 95% CI = the envelope of game, player-season and ISO-week cluster bootstraps (B = 5000, seed 2026; week decisive only with >= 20 weeks); BH over the four stats on the worst-case one-sided p, q = 0.05.
4. PASS for a stat needs all of: point <= -0.005 with binding CI upper < 0; BH <= 0.05; the select season's point is negative; C2 minus PL <= -0.0025 with CI upper < 0; PL does not itself beat CTRL by the permutation-null tolerance; no harm demonstrated on REST rows; mid-coverage at tau 0.10/0.20/0.80/0.90 moves by <= 0.01 against A0 (all rows); no slice with n >= 300 worse than +0.01. CLOSED = report-season point > -0.002. Else INCONCLUSIVE; GUARD_FAIL = the first three hold and a guard fails.
5. The OWN-row contrast (C1 minus A0 on OWN rows; the tagged player's conditional distribution given that he plays) is reported with its own word and never enters a stat's word: OWN_INCONCLUSIVE when its half-width exceeds 0.005 (the gate says it will, for pts, reb and ast), else OWN_PASS / OWN_NULL by the same floor test.
6. Secondary (no vote): C2 minus C1 on TM rows (what the cascade adds beyond the player's own tag), C1 minus A0 on TM rows, the P(play)-weighted unconditional mean (section 6), the slices, PL and CTRL contrasts.
7. Select 2023 / report 2024. 2023 is used for r_status and for the sign condition only. **No 2025 touch is earned or requested.** The live 2026-27 log (true T-60 tag) is the confirming test.
8. One pass per stat, no reruns, no variants. A PASS is a proposal to Devin. A new question gets a new id.

Constants mirrored by `research/eval/questionable_cascade_rule.py` (a test fails if this block and the module disagree):
```
FLOOR = -0.005
CLOSE_AT = -0.002
PL_MARGIN = -0.0025
NOHARM_POINT = 0.002
SLICE_TOL = 0.01
MIN_SLICE_N = 300
COV_TOL = 0.01
N_BOOT = 5000
SEED = 2026
SELECT = 2023
REPORT = 2024
BH_Q = 0.05
DESIGN = 'SELECT_REPORT'
```

## 2. Hypothesis, null, and what a result would mean
- **H1 (cascade).** At T-60 a Questionable or Doubtful rotation player is a coin flip (Q played 0.514 over 2022-24, n 5,106; Doubtful 0.02; docs/research/questionable_status_scope_2026-10-15.md). Production's props features count only OUT teammates (`n_out_rot`, `vac_*`), so on the night a Questionable star sits, his teammates' minutes and usage rise and A0 has not seen it coming. A model that is told the expected vacated minutes (weight 1 - r_status) should shrink that error on the teammates' rows.
- **H2 (own tag).** The tagged player's own status and reason class change his conditional mean and minutes given that he plays. The scope note found the tag mostly moves WHETHER he plays (median minutes/recent average 0.97 vs 1.00 among those who play), so the expected size is about zero; H2 is registered because Devin asked and it is cheap, not because it is expected.
- **Null.** A0's recency averages and OUT vacancy already carry what the tag carries on stat CRPS, or the tag's information is a post-tip fact (a coin flip resolved after the cutoff). C2 then sits on A0 and pays a small overfitting cost.
- **A descriptive that motivates, not evidence (gate G7, uses realised minutes):** teammates of a Questionable/Doubtful rotation player play 1.054 times their prior minutes average in those games, against 0.997 for REST rows. The direction is the hypothesis; it says nothing about whether A0 already absorbs it.
- **If PASS:** the first evidence that the report's coin-flip tag carries stat information beyond OUT, a proposal for a production feature (Devin's call; production counts OUT only because P(play) was the thing tested). **If not:** the expected answer, recorded, and the live log (section 9, option D) is the only remaining venue.

## 3. Lineage verdict, stated first (BEST_PRACTICES 2026-10-12; MODEL_PLAYBOOK question 4)
**New in information: partly (the Q/D status of TEAMMATES as a stat input; the own tag as a conditional-mean input). New in construction: no (it is RECENT_ROLE_FULL's walk with different columns). Expected result: a null the data can barely resolve.**

| ancestor | what it did | numbers (quoted from the ledger and drafts) | guards that disqualified it or limited it | carried or dropped here |
|---|---|---|---|---|
| PPLAY_CONTEXT, T316-T317 | P(play) log loss with the report tag and context | P2 beats the honest comparator; dropping the tag costs -0.0084 / -0.0124 log loss (2023 / 2024) | T-60 stored snapshot, real tips | the tag matters for P(play); NOT re-asked. This rule never touches P(play) |
| PPLAY_MINUTES_VALID, T482 / T484 | R19 vs production P(play) on served rows | -0.0854 / -0.0922 log loss, WOUNDED by T484 (the denominator: 27-32% of listed Q/D have no box row; production removes only OUT) | outcome-dependent population (listed players with no box row are never scored) | carried: every row here is a PLAYED row, so this rule cannot say anything about the players who did not play; OWN rows are "given he plays" |
| PPLAY_INTO_PROPS_LIVE (frozen 3a305482, live) | does P(play) R improve the UNCONDITIONAL props distribution (point mass at 0 + conditional grid), live log only | pts unreachable (~138k rows), fg3m n_min 2,200 | uses the same conditional grid for both arms, so conditional-on-playing metrics cannot move | **not re-asked.** That rule changes only p; this one changes the conditional distribution (own) and the teammates' distributions (cascade). No overlap in what moves; the same live rows will score both |
| RETURNING_TEAMMATE_2 (draft, gate FAIL by 1%) | teammates of a returning rotation player; pooled option B | 17,056 affected pts rows pooled, half-width 0.00505 vs 0.005 | the effect (-0.81) is post-hoc and conditional on the star playing; power | the construction (affected rows, expected-effect sizing from T476, stored FULL-minus-A0 for sd) is carried. **Overlap:** a Questionable player who "returns" (listed on the previous game's report, not OUT tonight) is a returner in RT2 and a tagged player here. Unmeasured; if both rules are ever frozen, the second to run must report the overlap |
| WOWY_STRUCT, T499 | per-possession on/off structure for the OUT-teammate effect | pts leader out 2023 -0.0096 [-0.0170, -0.0021], 2024 -0.0037 [-0.0097, +0.0023] FAIL_ONE_SEASON | both seasons required | the closest structural ancestor: it refined the OUT vacancy effect and one season failed. C2 is the same vacancy idea driven by Q/D; its prior is no better |
| RECENT_ROLE_FULL, T467 | recent-role columns into heads, integer CRPS vs A0 | pts -0.0084 / -0.0083 (n 27,619 / 27,701) | past-only placebo, planted future | the A0 heads, the walk, the placebo family, the integer grid |
| WORKLOAD T451 | P(play) from load features | cell P -0.0167 / -0.0157 log loss | LOAD family carries it | context only |
| QUESTIONABLE_STREAK (draft, gate FAIL, not frozen) | streak/change refinement of the tag | 105-227 rows a season, power about 0 | n | its lesson is the reason this rule uses ROTATION players only and counts rows by team-game |
| GAME_MARKET_CONTEXT T389-T414 / T501 | market game context | every arm within +-0.001 of its within-date placebo | within-date derangement | carried: PL is a within-date permutation; and the null gets a positive control and an "arm saw it" check (BEST_PRACTICES 2026-10-21): section 8 and G6/G7 |

## 4. Arms and features (as-of; `research/eval/questionable_cascade_features.py`, unit-tested)
**Status** = the status on the latest usable official-report snapshot of the game, stamped `<= tip - 60` on the REAL tip (`tip_source="real"`), source `nba_official_report`. A player absent from that snapshot is "unlisted"; a game with no usable snapshot is NaN in every new column (the production NULL pattern, never imputed as "nobody listed").
- **Own columns (C1, C2).** `qc_status` in {0 unlisted, 1 available, 2 probable, 3 questionable, 4 doubtful, 5 out}; `qc_reason` in {0 unlisted, 1 injury/illness, 2 G League, 3 rest, 4 personal, 5 other} from the report's free text (case, spaces and hyphens ignored). The snapshot LEAD (minutes before tip) is never a feature: history is a cadence mixture (about 180 minutes in 2022-24) and the live pull is 60-100, so a lead column would be an era flag.
- **Cascade columns (C2 only).** For the row of player i of team T in game g, over rotation teammates j != i who are Questionable or Doubtful on the snapshot (rotation = production's cut: at least 5 games and long-run minutes `min40 >= 12`, from the state after j's latest game strictly before g, the same join as `vacated_features`): `qc_nq`, `qc_nd` (counts), `qc_evac_min` = sum (1 - r_status(j)) x min10(j), and `qc_evac_{pts,reb,ast,fg3m}` = sum (1 - r_status(j)) x (decayed 10-game stat average of j). **r_status** is the share of rotation players with that status who got a box row (minutes > 0), measured on the SELECT season only: 2023 gives **r_Q = 0.580** (n 1,631) and **r_D = 0.0096** (n 209) (fallback 0.5 / 0.025 if a status has no select rows). Self is removed (a Questionable player's own row does not count himself). OUT teammates stay in production's `n_out_rot` / `vac_*`; nothing is double counted.
- **Why expected vacated minutes and not one-hot flags per teammate.** A GBM on ~11k affected rows a season cannot learn "star j at 33 minutes" from a per-teammate encoding; the expected vacancy is one number with a clear sign. The weights come from one season so the arm cannot see the report season's rates.
- **Rate drift is a disclosed weakness.** r_Q was 0.531 / 0.580 / 0.507 in 2022 / 2023 / 2024 (n 1,435 / 1,631 / 1,672), so 2023's 0.580 overstates how often a Questionable player plays in 2024 by 0.07. The cascade then under-weights vacated minutes in 2024. This is a bias toward the null.

## 5. Known-at (outside audit P1): what "T-60" is in history, and what that means for Questionable
- **What the stored snapshot is.** The latest report stamped `<= real tip - 60`. Measured on the 5,327 rotation Questionable/Doubtful player-games of 2022-24 (G8): lead p10 / p50 / p90 = 120 / 180 / 300 minutes in every season. It is "T-180" in practice. Report stamps of 3.8% (2023) and 4.8% (2024) of games are up to 30 minutes early (BEST_PRACTICES 2026-10-15), so a few games feed a report a hair stale, never a later one.
- **Nothing later exists in history.** Between that snapshot and the last one stamped `<= tip - 5` the status of a rotation Questionable player changed in 0.2% of cases (2022 0.4%, 2023 0.2%, 2024 0.2%; Doubtful 0.6% / 0% / 0%). 2022-24 reports were published two or three times a game, the last one at about T-180 or earlier. So the data cannot show how much of the Questionable mass resolves between T-180 and T-60; the live pull (60-100 minutes before tip) will see a tag that is partly resolved. **Train/serve skew, direction:** the live tag is MORE informative than the stored one (some Questionable players are upgraded or ruled out by then), so a historical null is weak evidence about live information; a historical PASS is not inflated by it. This is descriptive (G8) and uses post-cutoff report rows, never as an input.
- **Information the model may know:** only report rows stamped `<= tip - 60` and the box rows of strictly earlier games. The planted-future unit test puts a row stamped 30 minutes before tip in the table and asserts it is ignored (`tests/research/questionable_cascade/test_features.py`).

## 6. The secondary: P(play)-weighted unconditional mean
Descriptive only, from the shadow's R19 P(play) (T482) and the C-arms' conditional means on played rows: no new fit, no verdict. It is reported so the per-row conditional gain can be read next to the point mass at zero. The live PPLAY_INTO_PROPS_LIVE log is the unconditional venue; this rule does not duplicate it.

## 7. The decision rule in full, and what each choice protects against
- **Teammate rows are the primary; OWN rows are not.** The gate (section 9) says OWN rows (927-1,005 a season) have a pts half-width of 0.031 against a floor of 0.005 and the expected effect is about zero. A verdict on them would be a coin flip on noise. They get a word that says so (OWN_INCONCLUSIVE) and cannot rescue or fail a stat.
- **Integer CRPS on the integer grid for every arm** (T114-T118: a continuous arm lost 92-126% of its gain to the support artifact). A0, CTRL, C1, C2 and PL are all scored from the same 199-point integer grids.
- **Floor -0.005 and CI upper < 0, BH over four stats.** A gain too small to use, or noise; four stats share the rows and the cascade columns, so they are corrected together. The floor is the ledger's standard and is not tied to a stat's scale (fg3m CRPS sits an order of magnitude below pts; the floor is a bigger relative demand there).
- **Three clusterings, the binding one is the widest.** Game (players of one game share the game-level miss), player-season (the tagged player's effect is constant within him; PRESEASON_ROLE_PRIOR cleared three cells on game clustering that vanished by player, BEST_PRACTICES 2026-10-20) and ISO week (calendar shocks; 32-35 weeks a season, decisive). The top-five player-season share of TM rows is 2.5-3.0% (reported in the gate; a rule that reports it can see a star-driven result).
- **PL = the C2 block permuted jointly among rows of one game date.** Protects against a gain from "any nine extra columns" or from the date's mix; it keeps marginals, loses the matching of status to player and team. The guard threshold is sized from a permutation null on the select season (`null --k 50`) and written into the rule before the freeze (BEST_PRACTICES 2026-10-12): `PLACEBO_TOL` is None until then and the real run refuses.
- **No-harm is a harm detector, not a proof.** The gate shows the REST rows' pts half-width is 0.0056 in 2024 alone (week clustering binds), so "upper CI <= 0.005" cannot pass at a point estimate of zero. Harm is declared only if the REST point is >= +0.002 and the binding CI lower bound is > 0 (so only harm larger than about 0.006 can be seen). Found by the gate's REST half-width, not by a result.
- **Mid-coverage, not inclusive coverage.** On the integer lattice the inclusive share sits above tau by the tie mass; an upward shift is charged for it (the first planted run failed this guard at 30 of 30 seeds). Mid-coverage 0.5 (P(y < q) + P(y <= q)) removes it. Changed before any real outcome.
- **Slices (n >= 300, declared before any outcome):** vacated minutes >= 20 vs < 20; doubtful-only vs questionable present; back-to-back; bench (starter10 < 0.5). A gain concentrated where it cannot be used would show here.
- **Select 2023 / report 2024, rates from 2023 only.** Protects against fitting the status rates to the season that is judged. A 2023-only pass is weak evidence (a tree trained on one season; no tree verdict has flipped on that alone).
- **Kill criteria.** (1) The gate fails on data after the capture audit: stop, no freeze (the state of this draft). (2) The control walk does not reproduce the stored A0 to 1e-6, the planted-future test fails, or the production OUT tie differs: stop, no result. (3) After the freeze, PLACEBO_TOL unsized or any C arm scored before the freeze commit: rule void. (4) A stat CLOSED at the report season closes that stat; all four CLOSED closes the rule. (5) One pre-registered follow-up at most: the live T-30 version (Questionable resolved by the lineup), under its own id, then closed.

## 8. Positive control: a planted status effect the harness must detect (BEST_PRACTICES 2026-10-21)
`research/eval/questionable_cascade_eval.py rehearse` (synthetic outcomes drawn from the stored A0 integer grid; `row_y` never loaded; 30 seeds, 1,000 bootstrap draws, pts, final run Wed Oct 21 11:51-11:53 CDT 2026, deterministic: two 4-seed runs give identical JSON; `reports/prereg_questionable_cascade/rehearsal/results.json`):
- **Truth:** every Questionable/Doubtful rotation player sits with probability 1 - r_status (independently per game and player); his teammates then gain `f x 0.81 x min(vacated minutes / 33, 2)` points on rotation rows (T476's magnitude with the sign flipped; f scales it).
- **ORACLE** (C2 slot) shifts by the EXPECTED vacated minutes (what a model that knows the tag could at best do); **WRONG** (PL slot, and also tried in the C2 slot) moves the same shifts to other affected rows of the same date.

| truth | ORACLE as C2: words over 30 seeds | WRONG rows in the C2 slot | mean report-season C2 - A0, TM rows |
|---|---|---|---|
| null (candidate shifts where nothing is) | CLOSED 30 | CLOSED 30 | +0.0207 |
| planted f = 1.0 | **PASS 30** | CLOSED 24, INCONCLUSIVE 6 | -0.0183 |
| planted f = 0.5 | PASS 7, INCONCLUSIVE 22, CLOSED 1 | CLOSED 30 | -0.0041 |

Reading: the scoring path detects a planted effect at the generous size (PASS 30 of 30), does not pass the null (0 of 30) and is not blind to WHERE the effect is. At half the size (mean planted -0.0041, above the floor) it passes 23% of the time: the floor is reachable only if the real effect is at least about 0.5-0.6 of T476's magnitude. This is an upper bound (an oracle candidate; a real refit adds estimation noise). A guard design error and the coverage tie issue (section 7) were found here and fixed before any real outcome.
"Arm saw it" evidence in this draft: the unit tests assert the C2 columns are built as defined (self removal, the OUT tie to production's `vacated_features`, NaN pattern, the as-of rotation state) and the gate shows 40% of A0 rows are TM rows with a non-NaN cascade. A predict-time knockout (C2 with the nine columns set to their means) is NOT implemented; the run's substitutes are the `C2 - PL` and `C2 - C1` contrasts. Add the knockout before a freeze if Devin wants it (open item).

## 9. Minimum-data gate (non-negotiable 8), measured on the real data
Final run: `measured_at` in `gate.json` (Wed Oct 21 2026, CDT; clock from the program), about 30 s on CPU, read-only on `nba.duckdb` (mtime 2026-10-21 11:20 CDT; every query `season <= 2024`; `docs/data_manifests/latest.json` `data_version` reads a55164376845 dated 2026-10-12 and is older than the DB, so the data identity here is the DB mtime plus the component hashes below). Command (from the worktree, which links `nba.duckdb` and the stored components of the main checkout):
`uv run --no-sync python -W ignore -m research.eval.questionable_cascade_gate --data-root /Users/devin/Downloads/nba-prediction --out reports/prereg_questionable_cascade/gate.json --history`
Output `reports/prereg_questionable_cascade/gate.json`, sha256 `3dff9f02c647e690c0b1538fafae8bee4d95a366fad58a324697951a3fb17ac5` (final run Wed Oct 21 11:47 CDT; the rehearsal JSON is `04d139ca...`). Bars set in the module before the first number: 2024 affected-row half-width <= 0.005 (pts, reb, ast, fg3m), rows >= rows needed, pass-line power >= 0.5, REST-row half-width <= 0.005; informational: 2023 and pooled.

**Counts at the stored snapshot (rotation = production's cut; Q/D listed on the latest usable snapshot):**

| season | games (usable snapshot / real tip) | rotation Questionable | rotation Doubtful | rotation Probable | affected team-games |
|---|---|---|---|---|---|
| 2022 | 1,320 (1,318 / 1,320) | 1,435 | 164 | 591 | 1,027 |
| 2023 | 1,318 (1,317 / 1,318) | 1,631 | 209 | 613 | 1,158 |
| 2024 | 1,320 (1,317 / 1,320) | 1,672 | 216 | 721 | 1,157 |

**Rows and sizing, A0 rows (identical across stats; played, `n_prior >= 5`), TM = affected teammate. Sizing uses the stored FULL-minus-A0 deltas for per-row sd and design effect (the RETURNING_TEAMMATE_2 convention); hw = 95% half-width at the binding clustering; need = rows for hw 0.005:**

| stat, season | A0 rows | TM rows (games) | TM hw | TM rows needed | binding | REST hw | OWN rows | OWN hw |
|---|---|---|---|---|---|---|---|---|
| pts 2023 | 27,619 | 11,046 (875) | 0.00665 | 19,522 | player-season | 0.00600 | 1,005 | 0.0217 |
| **pts 2024** | 27,701 | **11,181 (882)** | **0.00620** | **17,169** | game | **0.00558** | 927 | 0.0308 |
| pts pooled | 55,320 | 22,227 (1,757) | 0.00449 | 17,886 | game | 0.00407 | 1,932 | 0.0184 |
| reb 2024 | 27,701 | 11,181 | 0.00280 | 3,514 | week | 0.00229 | 927 | 0.0099 |
| ast 2024 | 27,701 | 11,181 | 0.00219 | 2,139 | week | 0.00197 | 927 | 0.0077 |
| fg3m 2024 | 27,701 | 11,181 | 0.00121 | 653 | game | 0.00103 | 927 | 0.0050 |

Clusters for pts 2024 TM rows: 882 games, 538 player-seasons, 32 ISO weeks; design effects 1.02 / 0.97 / 0.92 (sd per row 0.331). 2023: 875 / 538 / 35. Top five player-seasons hold 2.5% (2024) and 3.0% (2023) of TM rows, 6.6% / 16.2% of OWN rows.

**Power from a label-free proxy (pts, TM rows, expected effect E[CRPS(A0) - CRPS(G)] where G shifts the A0 grid by f x 0.81 x min(expected vacated minutes / 33, 2) on rotation rows; f = 1 is the T476-sized, post-hoc, generous world):**

| season | f = 1.0: effect / power of the pass line | f = 0.5 | f = 0.25 | 80%-power effect (MDE) |
|---|---|---|---|---|
| 2023 | -0.0210 / 1.00 | -0.0049 / 0.31 | -0.0010 / 0.05 | 0.0095 |
| 2024 | -0.0212 / 1.00 | -0.0053 / 0.39 | -0.0010 / 0.05 | 0.0089 |
| pooled | -0.0211 / 1.00 | -0.0051 / 0.52 | -0.0010 / 0.04 | 0.0069 |

**Gate table (report season 2024 alone, as the brief's select/report design requires):**

| check | value | bar | pass |
|---|---|---|---|
| pts TM rows vs rows needed for hw 0.005 | 11,181 vs 17,169 (65%) | rows >= needed | **FAIL** |
| pts TM half-width | **0.00620** | <= 0.005 | **FAIL by 24%** |
| reb / ast / fg3m TM half-width | 0.00280 / 0.00219 / 0.00121 | <= 0.005 | pass |
| pts TM power of the pass line (f = 1) | 1.00 (f = 0.5: 0.39) | >= 0.5 | pass at f = 1 only |
| pts REST half-width (no-harm resolvable) | 0.00558 | <= 0.005 | **FAIL** (hence the harm detector, section 7) |
| reb / ast / fg3m REST half-width | 0.00229 / 0.00197 / 0.00103 | <= 0.005 | pass |
| OWN pts / reb / ast / fg3m half-width (secondary) | 0.0308 / 0.0099 / 0.0077 / 0.0050 | <= 0.005 | fail / fail / fail / borderline (by design not a stat's vote) |
| informational: pts TM half-width 2023 | 0.00665 | <= 0.005 | fail |
| informational: pts TM half-width pooled 2023-24 | 0.00449 (22,227 rows vs 17,886 needed) | <= 0.005 | pass |

**Other checks (all pass):**
- **A0 reproduction (G5).** The rebuilt production frame reproduces the stored A0 components row for row (55,320 rows, 0 stored rows missing in the frame, all four stats) and each stored feature column (`min10, m, n_prior, since_flag, gap_days, abs_margin, starter10`) with max absolute difference **0.0** (bar 1e-9). The CTRL heads walk is the 1e-6 gate of the real run (not executed here).
- **Production OUT tie (G5).** My `out` rotation set reproduces production's `n_out_rot` and `vac_min` for all 6,253 reported team-games (3,952 games with a usable snapshot = 3,952 with a production report): 0 differences.
- **Feature-frame alignment (G6).** Every A0 row has a feature row; the new columns are NaN on 73 of 55,320 rows (0.13%: games with no usable snapshot, as `has_report = 0`).
- **Status-vs-outcome missingness audit (G7).** NaN share by realised-minutes bucket (<5 / 5-10 / 10-20 / >=20): 0.06% / 0.15% / 0.10% / 0.15% (flat, no outcome pattern). Share of rows in TM by realised-minutes bucket: 42.2% / 40.4% / 39.7% / 40.1% (flat: the affected flag is not related to how long the row's player played). Share of OWN rows rises with realised minutes (2.1% / 2.3% / 2.9% / 4.0%): this is the selection the rule names, a Questionable player is in the stored rows only if he played, and the longer he plays the likelier he is a row. **OWN-row results are conditional on playing and carry that selection.** Mean realised/min10 minutes: TM 1.054, OWN 0.957, REST 0.997 (descriptive).
- **Staleness (G8).** Section 5.
- **Rates by season (G1/G7).** Rotation play rates: Questionable 0.531 / 0.580 / 0.507, Doubtful 0.043 / 0.010 / 0.023, Probable 0.929 / 0.945 / 0.946 (2022 / 2023 / 2024).

**Data-capture audit (non-negotiable 8; a gate is not declared failed on data until this is written).**
- *Seasons held and used:* production DB 2022-24 (the gate), history DB 2019-21 (select-style context only, `--history`; box scores and reports exist). Rotation Q/D counts with history states: 2019 685/170, 2020 914/152, 2021 426/39, 2022 1,555/194, 2023 1,653/216, 2024 1,677/213 (rotation Q/D per season; history seasons have no stored A0 components, so they add no rows to a CRPS contrast), Q play rates 0.533 / 0.545 / 0.678 / 0.527 / 0.574 / 0.507. 2021-22 has hourly reports and an era-specific rate (0.678): flagged, not pooled.
- *Why more seasons do not rescue the report season:* power is set by the report season's affected rows (11,181); training rows help the fit, not the report half-width (BEST_PRACTICES 2026-10-12). A0 components exist only for 2023-24 (2022 is the warm-up), so a third pooled season is not available without a stored-A0 rebuild (the CTRL walk would have to reproduce it, about a day of CPU; 2019-21 also lack the lower-tail components).
- *Sources considered:* the official PDF report (the only source in the tables, 99.7-100% of games 2019-24); beat-reporter feed (forward only, no history, needs Devin's pay-per-use token: not used); post-game inactive list (post-game, validation only); Pro Sports Transactions (403, skipped). No source adds pre-tip status at the T-60 cadence for past seasons.
- *Repairable gaps:* 1-3 games a season with no usable snapshot (immaterial); the 2021-22 cadence match (about a day, select-only value); the true-T-60 history cannot be bought, only logged live.
- *Conclusion:* the gate fails on the slice (rows and half-width at the floor in the report season), not on capture.

**Gate verdict: FAIL for the brief's design (select 2023 / report 2024) on pts: half-width 0.0062 against 0.005, 11,181 affected rows against 17,169 needed, no-harm guard unresolvable at point 0. reb, ast and fg3m pass. The OWN-row contrast cannot be resolved (pts 0.031) and is demoted. The rule is not frozen. The bar, the population, N and the floor were not changed after the number was seen.**

**Options for Devin (each is a new decision; none is taken here; the floor is NOT changed in this draft):**
- **A. Close at the draft.** Prior P(pass) is 0.04. The honest record is "the coin-flip tag cannot be tested against stat CRPS on 2022-24 at the floor; the live log is the venue".
- **B. Pooled 2023+2024 single test (RETURNING_TEAMMATE_2's option B, Devin's override precedent).** pts TM half-width 0.00449, 22,227 rows vs 17,886 needed, REST 0.0041: the gate passes. Costs: no select/report split (r_status would be measured on 2023 and applied to both seasons, in-sample for 2023); a PASS means "consistent with a half-strength T476 effect" (power of the pass line 0.52 at f = 0.5, 1.00 at f = 1, 0.04 at f = 0.25); confirmation only from the 2026-27 log. Changes to the text: `DESIGN = 'POOLED'`, the primary on 2023+2024, the select-sign condition dropped. Recommended if Devin wants a historical run at all.
- **C. Keep the split with a report-season pts floor equal to the resolvable half-width (about -0.0065).** Changes the floor for one stat; reb, ast, fg3m stay at -0.005. Not recommended: a bar chosen from the gate's own half-width is the quiet change the brief forbids unless Devin signs it.
- **D. Log the nine columns live and test at a pre-declared n.** True T-60 tag, no cadence skew; roughly 11k TM rows a season (the 2026-27 supply matches one historical season), so pts resolves only when pooled with 2024 at the end of the season (RETURNING_TEAMMATE_2's fallback C). Cheapest and the only option that sees the live tag.
- *My recommendation:* D, with B as the historical rehearsal if Devin wants a number now. In either case drop the OWN-row stat verdict (it is registered as descriptive).

## 10. Playbook check (docs/MODEL_PLAYBOOK.md; class: gradient-boosted trees, with a distribution layer)
1. **Information or capacity?** Information: the Q/D status of teammates is a pre-tip fact production does not read (OUT only). No new architecture. But the information is a coin flip at T-180 whose resolution (the live T-60) is not in history, so the information the historical test sees is the stale part.
2. **Enough data for this class?** Trees: 2-4 seasons usually enough; the 2023 select season trains on about one season, so a 2023-only fail is weak (no tree verdict has flipped on that). The binding constraint is the report season's affected rows, not training rows (section 9).
3. **Test validity.** Real tips (1,320 / 1,318 / 1,320 of 1,320 / 1,318 / 1,320 games), integer grid for every arm, played-games handling inherited from A0 (every scored row is a PLAYED row: no row for a listed player who did not play), planted future (unit test), missingness audit (G7), within-date placebo with a null-sized guard, OUT tie and A0 reproduction. Open validity risk, named: scoring only played rows means the TM set is conditioned on the teammate playing (when a star sits, bench players who play are over-represented), the same selection A0 has; and the OWN set is conditioned on the tagged player playing. Both are stated in the rule; neither can be removed without the P(play) side, which is PPLAY_INTO_PROPS_LIVE.
4. **Lineage and inherited guards.** Section 3: PPLAY_CONTEXT / T482 / T484 (carried: played rows only, no claim on non-players), PPLAY_INTO_PROPS_LIVE (not re-asked), RETURNING_TEAMMATE_2 (sizing convention, overlap flagged), WOWY_STRUCT (structural ancestor), RECENT_ROLE_FULL (walk, placebo, integer grid), WORKLOAD T451 (context).
5. **What it scores against.** CRPS of the whole integer distribution (A0). Not the line: our CRPS gains have not reached the line (T477); a CRPS PASS here would not be a bet. No CLV or decision score is claimed.
- **Class checks.** Leakage through missingness: audited (G7, flat); duplicated signals: C2's `qc_evac_*` mirror `vac_*` for Q/D instead of OUT and can interact with it inside the GBM; the PL and C1 contrasts bound this. Does the arm only re-express the base? The `C2 - C1` contrast on TM rows says what the cascade adds beyond the tag; there is no knockout (section 8).

## 11. Priors (committed 11:25 CDT in 3940016 before any gate number; scored here against the gate, not edited)
Written before the gate (binary): P(report-season pts TM half-width <= 0.005) 0.15 -> **no (0.0062)**, hit by the letter (the 0.15 was the probability of passing). P(pooled <= 0.005) 0.45 -> **yes (0.00449)**. P(OWN pts half-width <= 0.005) 0.02 -> **no (0.0308)**. P(whole gate passes as written, one season) 0.15 -> **no**; pooled 0.40 -> **yes on pts/reb/ast/fg3m**. P(rotation Q/D player-games per season in 1,200-2,200) 0.75 -> **yes (1,599 / 1,840 / 1,888)**. P(Q rotation play rate in 0.45-0.65) 0.80 -> **yes (0.531 / 0.580 / 0.507)**. For the run, if ever frozen: P(C2 beats A0 on TM pts by the floor with CI upper < 0) 0.04; P(C1 beats A0 on OWN pts by the floor) 0.03; P(any stat passes BH) 0.06; P(C2 beats PL by <= -0.0025) 0.05; expected C2 - A0 pooled TM pts -0.0008 (80% -0.0040 to +0.0020); P("no resolvable edge, the live log is the test") 0.85. The rehearsal (oracle, f = 1) passes 30 of 30; that is the harness, not a prior on the world.

## 12. Unstated details chosen before scoring
Rotation = production's cut (`n_after >= 5`, `min40 >= 12`); "played" = minutes > 0; TM and OWN are disjoint (a Questionable player is OWN even if another teammate is Questionable); OWN includes non-rotation Questionable/Doubtful players who played; a player listed `out` who plays anyway stays in the A0 rows (REST unless otherwise tagged); week key = ISO year-week of `game_date`; bootstrap seeds `[2026, i]` per clustering, +1 for the pooled block; one-sided p = share of replicates >= 0 floored at 1/B; per-row sd and design effect of the sizing come from the stored FULL-minus-A0 deltas on the rows of the set (a proxy for a refit's per-row noise, as RETURNING_TEAMMATE_2); the expected-effect model caps the shift at twice (vacated/33); the placebo permutation seed is `[SEED + i, date]`; pinned input hashes and the snapshot-source hash are below.

## 13. Harness (pinned) and the maintainer's commands
- Files: `research/eval/questionable_cascade_{features,gate,rule,eval}.py`; tests `tests/research/questionable_cascade/` (pin = sha256 of the sorted `path:blob_sha256` lines; a `harness(QUESTIONABLE_CASCADE): <pin>` commit after the freeze and the adversary's read, before any outcome is read). The real run refuses: a status line that does not start with FROZEN; an unsized `PLACEBO_TOL`; no `prereg(QUESTIONABLE_CASCADE): <sha>` commit carrying this text; an unpinned or edited harness; any season > 2024; a checkpoint older than the freeze; a control walk that does not reproduce the stored A0.
- All commands run from a checkout that has `nba.duckdb`, `configs/`, `data/` and `reports/lower_tail/components_*.npz` (the main checkout, or the worktree with those links). Wrap heavy runs in `mkdir data/ops/heavy.lock` (retry every 120 s; `rmdir` only a lock whose pid is yours, in `finally`).
  1. `check` (label-free, about 3 s): `.venv/bin/python -W ignore -m research.eval.questionable_cascade_eval check` prints the OUT tie, the A0 reproduction and the pinned counts (A0 rows 27,619 / 27,701; TM 11,046 / 11,181; OWN 1,005 / 927).
  2. `rehearse` (synthetic, about 2 minutes for 30 seeds): `... questionable_cascade_eval rehearse --seeds 30`.
  3. `null --k 50` (reads training labels, builds no C arm; **not run here**: 1 CTRL walk plus 50 pts heads walks, each one fit per month block of 2023-24, 24 fits; about the cost of RETURNING_TEAMMATE_2's null; checkpoints `reports/prereg_questionable_cascade/null/null_NNN.json`): `... questionable_cascade_eval null --k 50`. It prints `proposed_PLACEBO_TOL`; write it into `questionable_cascade_rule.py` and this text, re-pin.
  4. `run --sha <64 hex>` (the real thing; **not run, not runnable until frozen and pinned**): CTRL walks (reproduction gate) then C1 / C2 / PL walks for four stats, then integer-CRPS scoring.
- Dry-run count for `run`: 4 arms x 4 stats = 16 heads walks, each 24 monthly fits (2023-24), one at a time (8 GB box); checkpoints per walk in `data/ops/questionable_cascade_cache/`.

Pinned inputs (checked by `check` and `run`; each line is `NAME: sha256`):
COMPONENTS_SHA256_PTS: 1d38ac0ca7188b8c32c604169f87e7fdb47ed7ce759da1a1f0fad9bd60805269
COMPONENTS_SHA256_REB: 18846ced207326fe40bc0c5f6840ab22ce648918501cff58abb62fc4eee510bc
COMPONENTS_SHA256_AST: 024c15eedd2d090d7f38c081525aa14ab4b0a9939e0a0c05f5faddadf380f72e
COMPONENTS_SHA256_FG3M: 1ace9fbcefe6b051053059d51db23b9f4c5dc42561aa8446b074124a73703d8e
COMPONENTS_SHA256_PI: 0d3fdf4b9c68e0947d84d8bf17d8441d589813ea0c5f472d8bc2da1f3cd4614b
SNAPSHOT_SOURCE_SHA256: b5abb3e6abc3155fa2d343a4864906c0a8b3b485fe742e09b23c4383718e9708

=== RESULTS BELOW ===

(not run)
