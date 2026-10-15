# ALT_RUNG_CAL: does the frozen LINE_CALIBRATION map, carried to alternate rungs, fix the reliability pattern T491 found? (pre-registration)

Status: **DRAFT, revision 1, written by the modeler Thu Oct 15 00:46 CDT 2026 (clock read by `date`). Not frozen. No CAL map has been fit on a rung label and nothing has been scored.**
- **How it becomes the rule:** the main session reads it with Devin and commits `prereg(ALT_RUNG_CAL): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ALT_RUNG_CAL.md | shasum -a 256`.
- **Gate script (exists):** `research/eval/alt_rung_cal_gate.py`, counts and widths only; output `reports/prereg_alt_rung_cal/gate.json`.
- **Harness (does not exist yet):** `research/eval/alt_rung_cal_eval.py`, written only after the freeze; it must refuse to run without the `prereg(ALT_RUNG_CAL)` commit, reuse the fit machinery of `research/eval/line_calibration_eval.py` unchanged for CAL-main, and assert `season <= 2024` before any read.
- **Advice or momentum:** Devin's override (DECISIONS 2026-10-15 00:28, "Let's do 1, 2 3 4 and 5 now": all five follow-ups at once, where Claude recommended one at a time). This is item 1, the one Claude recommended doing first, so its content follows advice; the timing is Devin's override. It is not a recommendation to bet: nothing here uses a price, and the standing answer stays "keep your money until the live log proves skill".
- **Model class:** a logistic recalibration layer (5 or 7 terms) over a stored gradient-boosted residual forecast; playbook classes "Quantile and distribution layers" and "Linear and logistic models". Nothing neural, nothing tuned.
- **Season labels** are `games.season`: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (the frozen holdout, never loaded and never touched under this rule; alternate rungs were not pulled for it).

## What the gate found, before any rule text (the five facts that shaped it)
1. **The select-2023 / report-2024 split cannot be run as a test of the pattern.** DraftKings 2023 alone has 36,145 rungs every arm can be scored on (of 58,106), from February 2024 only: alternates start 2023-12-07, so December and January have too few earlier rungs to fit (9 of 28 blocks are identity). None of the five pooled bin cells passes the width gate in 2023 alone (conservative half-width 0.024 to 0.042 against 0.03). Pooled 2023-24 passes 5 of 5 (134,462 rungs, 1,818 games, half-widths 0.014 to 0.024). So the rule is **one walk-forward pass over both seasons with a per-season replication check**, not a select/report pair. This departs from "select on one season, report on the next"; it is flagged for Devin below.
2. **The frozen map has never seen a high-probability leg.** DraftKings main-line legs have x = logit(P) between -1.29 and +0.89 at the 1st to 99th percentile, i.e. P between 0.22 and 0.71. Of the rungs the map must score, **0% of bin 55, 13% of bin 65, 92% of bin 75, and 100% of bins 85 and 95 lie outside that band** (49% of all scored rungs; the z term is outside its band for 15%, 92%, 100%, 100% in bins 65 to 95). "CAL-main" at P >= 0.70 is an extrapolation test, not a test of what the map learned. That is stated in every verdict word.
3. **Where the effect is.** If the T491 gaps were the truth and a map recovered them exactly, the expected log-loss gain over A0 is 0.00421 per rung (pooled), and 62% of it sits in the 12,093 rungs with P >= 0.90 (9.0% of rows); bin 85 carries 20%, bins 55 and 65 together 17%, bin 75 under 1%.
4. **The test is powered for the effect.** Projected 95% half-width of the primary contrast, pooled: 0.00071 (game bootstrap on coupled synthetic labels, times sqrt(1.51)), against a floor of 0.002 (half of it is 0.001). The conservative bound (every rung of a game perfectly correlated) is 0.0034 and would not resolve the floor; the truth lies between, and the unit-level bound (rungs of a ladder perfectly correlated, units independent) is 0.00074.
5. **A plain reliability table is the strong baseline.** The pattern is a function of P alone in T491's table; a 10-bin walk-forward lookup recovers most of the oracle by construction. So the comparator C0 is that table, and the headline word separates "the map fixes it" from "a table fixes it".

## The rule in ten lines
1. **Rung set S.** DraftKings (`draftkings`), `t60` snapshot, the four player `*_alternate` markets (pts, reb, ast, fg3m), **over** rungs at half-point lines, de-duplicated by the ALT_LADDER_HIST rule, joined to a played model row, in the **primary arm of ALT_LADDER_HIST** (games with a report stamp in (snapshot, tip - 60] excluded, lead <= 66 minutes, the DraftKings points defect window 2023-12-07 to 2023-12-19 excluded, ladders failing either ladder-integrity test excluded), **with A0 P(over) >= 0.5** and a DraftKings main-market line for the same player and stat. The set is 162,745 rungs before the main-line and fit-size conditions (the T491 primary set with P >= 0.5, equal to the ALT_LADDER_HIST gate counts in all four book x season cells).
2. **A0** is the stored production P(over) at the rung: P(Y >= point + 0.5) on the integer grid, clipped to [0.5/199, 1 - 0.5/199].
3. **CAL-main** is the LINE_CALIBRATION CAL map, frozen at a5441242, applied **as is**: per (stat, calendar-month block), one time-weighted ridge logistic fit on DraftKings T-60 **main-line legs** strictly before the block's first day, terms `[1, x, z, bench, bench * x]` with `x = logit(A0)`, `z = clip((point - median) / max(IQR/1.349, 0.5), +-5)` from the continuous 199-point grid, `bench = starter10 < 0.5`; weight `0.5 ** (age_days / 365)`; ridge 1.0 on the four non-intercept terms; identity below 2,000 fit rows. The only change from LINE_CALIBRATION is the row it is applied to: the rung's `point`, not the book's main line.
4. **CAL-rung** is the same procedure refit on S **rung labels** strictly before each block (same book and stat, same weights, ridge 1.0, identity below 2,000 fit rows) with terms `[1, x, zm, r, r * x, bench, bench * x]`: `zm = clip((main - median) / spread, +-5)` is the position of the book's main line against the model's median (constant within a ladder), `r = clip((point - main) / spread, +-8)` is the rung's distance from the book's main line in model-spread units, and `spread = max(IQR/1.349, 0.5)`. The `r * x` term lets the slope change with distance, which is the shape T491 found (underconfident near the middle, overconfident high). No price enters any term.
5. **C0** is a walk-forward reliability table on the same fit rows: per (stat, block), 10 bins of A0 of width 0.05 on [0.5, 1.0], each bin's time-weighted hit rate (clipped), identity in a bin with fewer than 100 fit rungs. (LINE_CALIBRATION's C0, a constant base rate, is degenerate on rungs because the rung's own P carries its difficulty; it is kept as the reference row **BASE**.)
6. **Rows scored** are the rows every arm can score: the block has at least 2,000 fit rows for CAL-main (main-line legs) and CAL-rung (rungs), and the unit has a DraftKings main line. Rows dropped by either condition are counted, never hidden, and their A0 reliability is reported (S2, label only).
7. **Primary metric:** mean per-rung log loss, arm minus A0, on the scored rows, four stats pooled, every rung weighing the same. CI: paired 95% percentile bootstrap over game resamples (B = 5,000, seed 2026, one matrix shared by every statistic); the date-clustered CI (seed 2027) is reported beside it. A decision uses the game-clustered p.
8. **Walk-forward, not select/report:** one pass over the calendar months of 2023-12 to 2025-06, every month fit on strictly earlier months only; the verdict is read on the pooled scored rows; the same checks are read per season for replication (P8).
9. **Verdict words** are issued in a fixed order from the checks below (P1 to P8): MAIN_MAP_FIXES, RUNG_REFIT_FIXES, TABLE_FIXES, NOT_FIXED.
10. **The confirmatory test is the 2026-27 live log**; no 2025 touch, ever, under this id. A pass here earns a forward shadow rule (its own id, frozen before its first scored game), nothing more.

## Checks and words
Let X be CAL-main or CAL-rung (and, for TABLE_FIXES, C0). "Bins" are the five A0 bins **55** [0.5, 0.6), **65** [0.6, 0.7), **75** [0.7, 0.8), **85** [0.8, 0.9), **95** [0.9, 1.0], each pooled over the four stats (T491's Part 1S bins plus its descriptive top band). Rows are assigned to bins by A0, so every arm is scored on the same rows in each bin. gap_X = hit rate minus mean X over the bin's rows.
- **P1 (it helps):** X - A0 mean log loss <= **-0.002** with one-sided game-clustered p < 0.025 (Bonferroni over the two claim arms, CAL-main and CAL-rung).
- **P2 (the pattern is fixed):** in **each** of the five bins, |gap_X| <= 0.02 and the union (min lower, max upper) of the game- and date-clustered 95% intervals lies inside [-0.03, +0.03] (T491's CALIBRATED definition and margins).
- **P3 (it adds to a table):** X - C0 < 0 with one-sided game-clustered p < 0.025.
- **P4 (it adds to shuffling):** for CAL-rung only: X - mean(SH_k) <= -0.0005 and <= -2 null sd, and the paired game-clustered interval for X - mean(SH_k) has upper < 0. SH_k, k = 0..49, is CAL-rung refit after permuting the realised stat among the fit **units** of the same stat and block (a rung's hit follows its unit's permuted stat, so ladders stay nested). For CAL-main the shuffled-leg null is **inert by construction** (a map fit on shuffled main-line legs collapses to the main-line base rate near 0.5 and sends every high rung there), so CAL-main is not asked for P4; P3 stands in for the information question. SH for both arms is reported.
- **P5 (no stat is harmed):** no stat has X - A0 with BH-adjusted (m = 4) one-sided p < 0.05 for "worse".
- **P6 (no slice is harmed):** the starter and bench slices each have X - A0 <= 0 (point), and each of the five bins has X - A0 <= +0.001 (point).
- **P7 (a second book agrees):** on FanDuel's primary-arm rungs built the same way, X - A0 < 0 (point). FanDuel CAL-rung is fit on FanDuel rungs and its own main line; FanDuel CAL-main is the **DraftKings** map applied unchanged (a book swap), because LINE_CALIBRATION never fit FanDuel. FanDuel is pooled over both seasons.
- **P8 (replicates across seasons):** X - A0 < 0 (point) separately on the 2023 rows and the 2024 rows, and in every bin the per-season |gap_X| <= 0.03 (point).

**Words, in this order:**
- **MAIN_MAP_FIXES** iff CAL-main passes P1, P2, P5, P6, P7, P8. (If P3 fails the word carries "a table does as well".) Always printed with the share of rows outside the fit-leg band by bin; bins 75 to 95 are extrapolation.
- **RUNG_REFIT_FIXES** iff CAL-main does not, and CAL-rung passes P1 to P8.
- **TABLE_FIXES** iff neither of the above, and either C0 passes P1, P2, P5, P6, P7, P8 on its own, or CAL-rung passes P1, P2, P5, P6, P7, P8 but fails P3 or P4. The proposal then says in words that a reliability table, not the map, is what repairs the pattern.
- **NOT_FIXED** otherwise: no arm repairs it on these rows; the next candidate is the PIT/grid recalibration under a new id (LINE_CALIBRATION "If this fails" item 1), not a variant here.

**Multiplicity.** The primary family is the two claim-arm P1 contrasts (CAL-main, CAL-rung): Bonferroni, m = 2 (p < 0.025 each). The words are one fixed-order classification of pre-declared checks; no arm is added after a result. The per-stat contrasts (m = 4 per arm) carry BH and are labels. The 20 stat x bin cells carry T491's words only where the cell passes the width gate (17 of 20 do pooled; the three that cannot are bin 95 for ast 2,491, fg3m 1,780 and reb 2,793 rungs), BH over the cells that can issue one, labels only, not a pass.

**Secondary, label only (no pass):** per-stat contrasts; the full 10-bin reliability table per arm; calibration in the large by stat and role; slices by month, by rung distance from the main line (|r| tertiles) and by whether a row is inside the fit-leg band for CAL-main; knockouts of CAL-rung against C0 (terms `[1, x]`, without `r` and `r*x`, without `zm`); S1 the report-window games added back (arms A0, CAL-main, C0, same fits); S2 the rows with no DraftKings main line (A0, CAL-main, C0 only); S3 the defect-window and off-ladder rungs added back (A0, CAL-main, C0), showing what the integrity filters change; S4 unit-mean weighting instead of rung weighting; BASE; Brier.

## Seasons: why one pooled walk-forward pass, with the select-2023 alternative priced
- **Asked:** select 2023 and report 2024, or a walk-forward like LINE_CALIBRATION.
- **Chosen:** walk-forward over both seasons, pooled verdict, per-season replication (P8).
- **Why, from the gate:** (a) 2023 as a select season cannot issue the pattern words: 0 of 5 pooled cells pass the width gate in 2023 alone (the check "DraftKings 2023 alone" below fails), 9 of 28 blocks are identity, only 36,145 rungs from February; (b) 2024 alone passes the cells (5 of 5) but its fits borrow 2023 rungs either way, so "report 2024" is the pooled pass restricted to later rows; (c) **nothing is selected**: arms, terms, floor, bins, weights and half-life are all fixed above, so a select season would only be a rehearsal; (d) both seasons were already seen at the A0 level by T491 (every bin x stat gap), so neither is a clean report season, and the clean test is the 2026-27 live log either way.
- **What this gives up:** the "select on one season, report on the next" discipline. P8 (the sign and the per-bin gaps in each season) replaces it as the replication guard. If Devin prefers select-2023: the P1 projected half-width passes in 2023 alone (0.00097 against 0.001, borderline), the cell words cannot be issued there, and the select stage could only gate P1, P3 and P5. Not recommended.
- **2025:** never loaded, never touched: alternates were not pulled for it (DECISIONS 2026-10-14 12:31), the harness asserts `season <= 2024`, and no outcome under this id may come from it.

## Hypothesis, null and priors
- **H1 (the question as asked).** The frozen main-line map, applied to rungs as is, repairs the T491 pattern. **H2 (the repair if H1 fails).** A refit on rung labels with rung distance repairs it. **Null.** Neither repairs it beyond what a reliability table on A0 does.
- **Why I expect H1 to fail (reasoned, not measured):** the map's slope on x was about 0.46 where x lies within +-1.3 (the POVER_BIAS scratch; the LINE_CALIBRATION disclosure); carried to x of 2 to 5 it compresses high P toward 0.5 whatever the truth there is, and the z term, near-collinear with x on rungs (|corr| 0.98), has coefficients identified only inside a band that 92% to 100% of high-P rungs leave. I did not open the CAL coefficients or apply the map to any rung.
- **Binary priors (stated before any result; also appended to PREDICTIONS_LEDGER).** They are **not blind**: I know the T491 gaps and the T493 gain. Events are on the pooled 2023-24 scored DraftKings rows. P(CAL-main P1 passes) **0.25**; P(CAL-main P2 passes in all five bins) **0.05**; P(CAL-main point estimate worse than A0, i.e. CAL-main - A0 > 0) **0.50**; P(CAL-main raises bin-85 log loss over A0, point) **0.60**; P(CAL-rung P1 passes) **0.85**; P(CAL-rung P2 passes in all five bins) **0.60**; P(CAL-rung passes P3, beats the table) **0.30**; P(C0 passes P1, P2, P5, P6, P7, P8) **0.78**. Words: MAIN_MAP_FIXES **0.04**, RUNG_REFIT_FIXES **0.13**, TABLE_FIXES **0.70**, NOT_FIXED **0.13** (sum 1.00). Expected values with 80% intervals: CAL-rung - A0 **-0.0030** (-0.0042 to -0.0014); CAL-main - A0 **+0.0010** (-0.0040 to +0.0150); CAL-rung - C0 **-0.0001** (-0.0008 to +0.0006).

## Data exposure (honest)
- **Seen before this draft:**
  - **T491** (ALT_LADDER_HIST): A0's reliability on exactly these rungs, every bin x stat pooled gap and the per-season gaps, and the break-even table for P >= 0.90. This defines the pattern, and the published gaps size the oracle in the gate (`PUBLISHED_GAP` in the script).
  - **T489** (BetRivers t5 main-line ladders): fg3m OVERCONFIDENT at 70%+.
  - **T493**: CAL on main lines (Pinnacle -0.0117, DraftKings -0.0095 against A0). No CAL map has been applied to a rung, and CAL-rung has never been fit.
  - The POVER_BIAS scratch: a slope of 0.46 on 2023 DraftKings main legs scored on 2024.
- **Consequence.** The pattern is not a surprise and the A0 half of every contrast is fully exposed in both seasons; P1 for CAL-rung is close to foregone (an in-distribution refit of seven terms on 100,000 rungs against an oracle gain of 0.0042). What the rule can still say "no" to is MAIN_MAP_FIXES, P2 (all five bins) and P3. The clean evidence is the 2026-27 live log; this pass is a historical screen that costs nothing.
- **Not seen:** any CAL-main, CAL-rung, C0 or SH output on a rung; rung labels beyond the T491 published cells.

## Lineage (playbook question 4)
- **Ancestors:** LINE_CALIBRATION (the map, the fit machinery, the block and weight rules, the P1/P4 bars; frozen a5441242, T493 CALIBRATED on main lines), ALT_LADDER_HIST (the rung set, the integrity filters, the bins, the margins; T491), ALT_LINE_70 (the pattern on BetRivers, T489), CALIBRATED_GRID (T339: shrinkage passing as calibration).
- **Guards carried:** walk-forward by month with strictly earlier fit rows and decayed weights; shuffled-label null (here at the unit level, K = 50); game-clustered bootstrap; the identity rule below 2,000 fit rows; A0 reproduction (rung counts by bin equal the ALT_LADDER_HIST gate in four of four cells); continuous-grid P(over) equals the served P at every rung (0 mismatches); the defect-window and ladder-integrity exclusions.
- **Guards changed:** C0 is a reliability table, not a constant (the constant is degenerate on rungs); the null permutes units, not rungs (independent rung permutation would break ladder nesting and make the null too easy); one pooled pass instead of select/report (gate-driven).
- **Guards dropped:** the market comparators (no price is used; the de-vigged price is not a reference row here); the knockout "CAL without z" is replaced by the CAL-rung knockouts above.

## Minimum-data gate (non-negotiable 8): measured on the real data
- **Command:** `/Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.eval.alt_rung_cal_gate --data-root /Users/devin/Downloads/nba-prediction` (run from the checkout holding the code; reads the main checkout's data by absolute path, read-only; 21.6 s, peak RSS 0.9 GB, exit 0).
- **Quoted `date` output:** the same shell command printed `Thu Oct 15 00:42:53 CDT 2026` before and `Thu Oct 15 00:43:18 CDT 2026` after; `gate.json` records `date_cdt` = `Thu Oct 15 00:42:56 CDT 2026`.
- **data_version** (sha256, full 64 hex in `gate.json`): `data/odds/odds_history.duckdb` eec776b647ee, `reports/odds_history/model_rows_2023.parquet` 002e53ef1a5f, `model_rows_2024.parquet` ec47dcdf566e, `data/ops/model_clv_pairs/pairs_2023.parquet` 050dba3e250d, `pairs_2024.parquet` 560a3808d7d9, `reports/prereg_alt_ladder_hist/gate.json` 005919d445e6; `nba.duckdb` `player_availability` joined to `games` (report stamps only). Seasons 2023 and 2024 only; no `y`, `over`, `minutes` or price column is read; no map is fit; the only outcome-derived numbers are the published T491 gaps.
- **Result: 13 of 14 checks pass; the one failure is the select-2023 design and is the reason the rule is pooled.**

| check | measured | bar | pass |
|---|---|---|---|
| primary-arm rung counts by A0 bin equal the ALT_LADDER_HIST gate | DK 2023 / 2024 and FD 2023 / 2024: 4 of 4 equal (DK 2023: 16,682 / 15,598 / 13,716 / 8,787 / 3,323; 2024: 30,347 / 24,009 / 20,584 / 19,512 / 10,187 in bins 55 / 65 / 75 / 85 / 95) | 4 of 4 | yes |
| rungs with the snapshot < 60 min before the real tip | 0 (minimum lead 64.2 min) | 0 | yes |
| continuous-grid P(over) equals the served P; contradictory brackets; starter10 | 0 mismatches of 162,745 DK and 244,270 FD rungs; 0 of 72 grid blocks inconsistent per book; 0 missing | all | yes |
| DraftKings main-leg line equals the odds_history main line | 53,247 of 53,247 (2023), 53,752 of 53,752 (2024) | all | yes |
| DK pooled 2023-24, five bin cells: rungs / games / conservative hw | 55: 38,122 / 1,818 / 0.0239; 65: 31,992 / 1,817 / 0.0229; 75: 27,930 / 1,818 / 0.0209; 85: 24,325 / 1,814 / 0.0180; 95: 12,093 / 1,680 / 0.0143 | >= 3,000, >= 300, <= 0.03 | yes |
| **DK 2023 alone, five bin cells (the select-season test)** | hw 0.0420 / 0.0404 / 0.0372 / 0.0320 / 0.0244 (10,642 / 9,729 / 8,359 / 5,328 / 2,087 rungs) | 5 of 5 | **NO** |
| DK 2024 alone, five bin cells | hw 0.0289 / 0.0278 / 0.0252 / 0.0212 / 0.0165 | 5 of 5 | yes |
| DK pooled stat x bin cells that can issue a word (descriptive) | 17 of 20 (bin 95: ast 2,491, fg3m 1,780, reb 2,793 rungs) | reported | yes |
| P1 projected hw, DK pooled (oracle gain 0.00421, 134,462 rungs, 1,818 games) | bootstrap 0.00058, times sqrt(1.51) = 0.00071 (unit bound 0.00074, game bound 0.00341) | <= 0.001 | yes |
| P1 projected hw, DK 2023 alone (gain 0.00301, 36,145 rungs, 580 games) | 0.00097 (unit bound 0.0011, game bound 0.00482) | <= 0.001 | yes (borderline) |
| P1 projected hw, DK 2024 alone (gain 0.00465, 98,317 rungs, 1,238 games) | 0.00085 (unit 0.00092, game 0.00431) | <= 0.001 | yes |
| walk-forward blocks with >= 2,000 fit rows for every arm, pooled | 55 of 64; identity: all four stats in 2023-12 and 2024-01, fg3m 2024-02; smallest fit 5,174 main legs, 3,044 rungs; every 2024-season block fits | all 2024-season blocks fit | yes |
| rungs scored by every arm (pooled) | 134,462 of 162,745 (82.6%): 21,507 dropped for identity blocks (all 2023), 6,776 for no main line (454 in 2023, 6,322 in 2024) | >= 100,000 | yes |
| FanDuel pooled, five bin cells (P7 replicate) | 49,667 / 44,502 / 42,711 / 41,990 / 42,906 rungs; hw 0.0229 / 0.0222 / 0.0201 / 0.0166 / 0.0102; 221,776 scored rungs, 20 of 20 stat cells | 5 of 5 | yes |

- **Support of the frozen map (a fact, not a pass):** x of the DraftKings fit legs, 1st / 5th / 50th / 95th / 99th percentile -1.289 / -0.937 / -0.232 / +0.481 / +0.888 (P 0.22 to 0.71); z -0.464 / -0.284 / +0.148 / +0.665 / +0.931. Share of scored rungs with x outside the fit legs' 1st to 99th percentile band (per block): 0.0% (bin 55), 13.1% (65), 91.7% (75), 100% (85), 100% (95); 49.2% overall.
- **Identifiability (label-free):** CAL-rung design on the scored rungs, standardized condition number 19.7, largest pairwise |corr| 0.936 (ridge 1.0 handles it); CAL-main's design on rungs, 10.4 and 0.981 (x and z are near-collinear on rungs).
- **Ladder structure:** 64,984 units (player, game, stat) hold the 134,462 scored rungs, mean 2.07 per unit, at most 8; 74 scored rungs per game; bench 26,304 rungs, starter 108,158.
- **Power note.** The projection uses synthetic labels drawn from the oracle truth with one uniform per unit (so a ladder's rungs are nested) and real rows and games; it does not include between-player dependence inside a game beyond the factor 1.51 borrowed from LINE_CALIBRATION's measured design effect on legs. If the real half-width exceeds 0.001, P1 cannot clear its floor and the word is not issued from this pass.

## Data-capture audit (non-negotiable 8)
- **Used:** every alternate rung we hold for the non-holdout seasons from DraftKings and FanDuel at `t60` (the one stored alternate capture), the stored production OOF rows for 2023 and 2024, and the DraftKings main-line legs of the MODEL_CLV pairs.
- **Held and not used:** (a) the other eight books' alternate rungs (betrivers 2024 10,946, williamhill_us, bovada, betmgm, betonlineag and others): not in the family; several have rungs removed by the integrity tests; pooling books would give a book-agnostic map, a different question; (b) the main-line legs of Pinnacle and other books: LINE_CALIBRATION's primary book, not a rung book; (c) `data/history/` 2019-21 box scores and play-by-play: no odds of any kind.
- **Do not exist:** alternate rungs before 2023-12-07 (306 of 1,318 games of 2023-24 have none; the vendor history starts then; not repairable); any alternate rung for 2025 (not pulled; holdout); stored props lines before 2023.
- **Exposed in this audit:** one counts-and-timestamps query of the odds store grouped by season (row counts and first/last snapshot per season, alternate rows by season: they exist for 2023 and 2024 only) was run by the modeler during the audit and touched season 2025 rows as counts; no 2025 outcome or price was read into any statistic, and the gate's every query carries `season in (2023, 2024)`.
- **Repairable gaps and cost:** (1) a second alternate snapshot at or after the cutoff (about 485,000 credits; Devin declined 2026-10-14); it would not add pre-December 2023 rungs and is not needed for a price-free calibration; (2) the 6,322 no-main-line rungs of 2024 (6.0%) cannot be repaired from the store (the book posted no main market); (3) the 2023 identity blocks are not repairable (no earlier rungs exist); (4) the live capture is the only source that grows: `live_book_odds` holds 0 rows as of 2026-10-15 (live schedule starts about 2026-10-20), and the live schedule requests the `player_alternate` group on every capture.
- **Forward accumulation estimate (a plan, not a fact):** the smallest pooled cell (bin 95) holds about 8.7 rungs per game with a ladder, so 300 games with DraftKings or FanDuel ladders give about 2,600 rungs; a width-gated look at the top bin needs roughly 350 games, so no earlier than about December 2026 if the captures run as configured. Unverified until games are captured.

## Playbook check (docs/MODEL_PLAYBOOK.md; classes: quantile and distribution layers, linear and logistic models)
1. **Information or capacity?** Neither. No new pre-tip information. It re-expresses production's own information at a threshold, and it adds one piece of market information that is a line, not a price (the book's main line in CAL-rung). If CAL-rung beats the table, the extra information is that line; the knockouts say which term carries it.
2. **Enough data for this class?** Yes: 5 to 7 parameters on 3,000 to 120,000 fit rungs per (stat, block) after the first months; the class check asks for per-slice volume, gated: the pooled cells pass 5 of 5, stat cells 17 of 20.
3. **Valid test?** Real tip gating (0 rungs under 60 minutes lead; snapshot about 64 minutes); played rows only (P conditional on playing; a sportsbook voids a DNP); leak-free inputs (as-of production OOF, the book's own posted lines); integer support (rung P is exact on both grids, 0 mismatches); guards sized from a null (K = 50, unit-level permutation; its sd is measured inside the harness on the scored rows because no rung label may be read before the freeze, so the practical bar is the absolute margin 0.0005); the ladder-integrity and defect-window exclusions; the comparator is what production serves (A0) and a table. Risks listed: one book primary; rungs are posted by the book (selection by the book); within-ladder correlation (rungs per unit 2.07); 2023 identity blocks; the exposure above.
4. **Lineage:** above.
5. **What it scores against:** log loss and calibration in the large at a threshold, not CRPS, and not money. No claim of profit; T491 already found every strategy loses about the vig.
Class checks: the reliability table is at the points that matter (P 0.5 to 1.0 in five bins); slices fixed in advance (stat, role, bin, month, distance).

## What each choice protects against
| choice | protects against |
|---|---|
| fit rows strictly before the block, decayed weights, identity below 2,000 | a later outcome shaping an earlier fit; stale seasons pooled equally; thin early fits |
| CAL-main applied as is, extrapolation share printed in the word | reading an extrapolation as the map "working", or dismissing it as broken without saying where it had no data |
| CAL-rung as a separate arm with `r` and `r*x` | declaring the question closed because a main-line map does not transport, when a rung-trained map is the obvious repair |
| C0 is a reliability table | "beats shrinkage" being trivial (a constant is beaten by any P-aware arm on rungs) and a map credited for what a lookup does |
| the null permutes whole units | an independent-rung permutation breaking ladder nesting and making the null easy to beat |
| no market price in any term; the main line is a line | a "calibration" that is secretly the market; the line is stated as the one market input |
| rows scored are those every arm can score | comparing arms on different rows (fair baseline or no comparison) |
| P2 in all five bins pooled over stats, with T491's margins | a gain from the top bin alone (62% of the oracle) hiding a worse middle |
| P6 bin and role do-no-harm | an over-shrunk map fixing bin 95 while damaging bins 55 to 85 |
| floor -0.002 | a significant but empty gain: 47% of the oracle gain, 2.8 times the projected half-width |
| one pooled pass, P8 per-season replication, gate-driven | a select season too thin to issue a word; hiding the departure from select/report |
| FanDuel as a pass check through a book swap | a DraftKings-only artifact (selection of rungs by one book) |
| no 2025, live log is the confirmatory test | spending the holdout on an exposed question |
| priors stated now, not blind | calibrating our own forecasting; the score lands when the result does |

## Kill criteria and what a result opens
- **Any word is a finding.** No re-cut of the floor, bins, margins, terms, half-life or row filters after a result. A different map (PIT/grid recalibration, per-slice shrinkage, a rung-isotonic) is a new id.
- **One allowed follow-up:** a forward shadow of the winning object on the 2026-27 live ladders (own id, frozen before its first scored game, gate measured on real forward counts, width-gated look date).
- **If MAIN_MAP_FIXES:** attacks first (shuffled labels, planted future, knockouts, replication on FanDuel and by season), because a main-line map fixing rungs it never saw would be a surprise. **If TABLE_FIXES:** the table is the object; the briefing wording changes from "model P" to "calibrated P" only by Devin's decision. **If NOT_FIXED:** recorded; keep the raw wording "never present a model line probability as a hit rate without its calibration".

## Unstated details fixed before any scored number
- **Unit** = (game, player, stat); **block** = calendar month of `game_date`; fit rows are the same book and stat with `game_date` strictly before the block's first day, all loaded seasons.
- **Hit** = Y >= point + 0.5 (point 24.5 is "25+"), Y the stored realised stat, read only by the harness after the freeze. A0 is clipped to [0.5/199, 1 - 0.5/199] before log loss; bins use A0 with tolerance 1e-12.
- **CAL-main's continuous grid** is recovered from the stored artifacts exactly as in LINE_CALIBRATION (bracketing from the survival counts, midpoint, q25 / q50 / q75 by interpolation at 49.25 / 99 / 148.75).
- **Main line** of a unit = the DraftKings main-market line, which equals the MODEL_CLV leg line in 106,999 of 106,999 legs.
- **IRLS** with ridge 1.0 (sum-of-losses scale) on every non-intercept term, start 0, tolerance 1e-10, at most 100 iterations; `x` clipped at 0.5/199; `z` and `zm` clipped at +-5, `r` at +-8; spread floor 0.5.
- **C0** bins: width 0.05 on [0.5, 1.0], the last closed; a bin with fewer than 100 fit rungs is identity; BASE is the weighted constant.
- **SH_k** uses `default_rng([1000 + k, stat index, block index, book index])` to permute the realised stat among the fit units; the weights stay with the rows.
- **Bootstrap:** game resamples B = 5,000, seed 2026, one multinomial matrix shared by every statistic; date resamples seed 2027; the one-sided p for "arm beats comparator" is (1 + #{bootstrap draws in which arm minus comparator is >= 0}) / (B + 1); BH over 4 per arm.
- **Starter** = starter10 >= 0.5; bench otherwise. **Rung distance tertiles** are on |r| over the scored rows.
- **Order of rows** is fixed by (game_id, player_id, stat, point) so any randomised step is reproducible.
- **Scale:** the harness is light (numpy over about 135,000 rungs; 50 shuffled fits of up to 120,000 rows by 64 blocks); progress per block; no heavy.lock needed; no GPU.

## What Devin must sign (open questions)
1. **Seasons:** one pooled walk-forward pass with per-season replication (recommended, gate-driven) versus select 2023 / report 2024 (the select half cannot issue the pattern words; see the gate). 
2. **C0 is a reliability table**, not LINE_CALIBRATION's constant base rate (which stays as a reference row). Yes?
3. **Floor -0.002** (47% of the oracle gain) versus -0.003 (71%, would need a near-perfect repair). 
4. **CAL-main is scored on all rows**, with the extrapolation share printed, rather than only on bins 55 and 65 where it has support. Scoring only in support would test the map where it was trained but not where the pattern bites.
5. **P7 FanDuel as a pass check** (book swap for CAL-main) versus label only.
6. **Stat-level words** only for the 17 cells that pass the width gate; the three thin bin-95 cells reported without words.
7. **Live confirmation:** a forward rule id on the live ladders, frozen before its first scored game, look date not before about December 2026; the second historical pull stays declined.

=== RESULTS BELOW ===
(none: draft; no map fit, nothing scored)
