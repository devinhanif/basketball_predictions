# PPLAY_CONTEXT: team back-to-back habit and questionable-by-team-strength in the P(play) model (pre-registration)

Status: FROZEN 2026-10-11 10:44 CT on Devin's confirmation ("Run everything, confirmed confirmed", 2026-10-11 ~11:05 CT; docs/DECISIONS.md). Two gate items amended before the freeze after the gate measure (checks 1 and 3; nothing scored). Committed by the
maintainer's hand BEFORE any fit. Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit
hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PPLAY_CONTEXT.md | shasum -a 256` (first 8 hex chars into the
results section and every ledger row). Season labels: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (frozen holdout, never loaded).

## What Devin said and what the data showed (reports/fan_answers_2026-10-11.md; FAN_KNOWLEDGE 2026-10-11)
Load management is a general practice that varies by team; late scratches are common; "questionable" is the tag; whether a
questionable player plays depends on whether the team is a lottery team. Measured: stars sit the second night of a back-to-back
12.4% vs 6.8% (diff +5.6 [+4.1, +7.0]); the team rate persists across seasons (r 0.73); the excess survives excluding the four
injury-prone stars (+1.6 points on a narrower star definition) and has nothing to do with size (height/weight terciles 2.0-2.6%,
flat). Questionable plays 47.9%; on weak teams 40.8% vs contenders 50.2% (−9.4 [−13.3, −5.1]). 93% of star b2b sit-outs are on
the T-60 report; the model's props path already sees that. What does NOT see it is P(play), the probability the parlay engine uses
for unconditional legs: production P(play) is the minutes model's hurdle plus a Platt map (`forward.P_PLAY_PLATT`), with no team
habit, no b2b-by-star and no tag-by-team-strength term.

## Hypothesis
H1: adding four pre-tip-knowable terms to the P(play) model lowers its log loss on rostered rows: (1) `team_b2b_sit`: the team's
as-of rate of star sit-outs on b2b second nights (prior 82 team games, shrunk to the league rate with pseudo-count 20 b2b games);
(2) `is_b2b_second` × star (prior-10 pts >= 20 or top-3 prior-10 minutes); (3) report tag at T-60 × team-strength tercile
(prior-season win%); (4) `on_morning_report` (listed on the earliest same-day snapshot) and `on_evening_report`. Null: no change.
Counter-hypothesis: the T-60 report already resolves most sit-outs (93%), so the residual information is small; prior pass
probability ~35% for the T-60 version, higher for a morning-time version (secondary) where the report is thin.

## Data exposure (honest)
Seen: the descriptive tables above (2023 and 2024, both seasons pooled; no model fit); MINUTES_V2's secondary P(play) arms
(production raw and Platt, M2-p LightGBM with rest/b2b/dnp_last5; descriptive, no floor). Not seen: any P(play) number with the
four terms. Season 2025 never loaded; `season <= 2024` asserted; nba.duckdb read_only. Select on 2023, report on 2024.

## Fixed design (constants final)
Rows: `build_minutes_features` rows (played or DNP) in 2023/2024 with `n_played_prior >= 5`, excluding players OUT on the
real-tip-gated T-60 report (as MINUTES_V2 secondary). Label: `minutes > 0`. Two timing variants, each its own family:
* **T-60** (primary): features as of the T-60 report.
* **Morning** (secondary): features as of the earliest same-day report snapshot; the T-60 tag is NOT available; `is_b2b_second`,
  `team_b2b_sit`, strength and the morning tag are.
Arms (identical rows/seeds; month-block walk-forward, train strictly before the block, 2022 warm-up, production LightGBM
hyper-parameters, seed 0, CPU):
* P0 production P(play) + Platt (reproduces MINUTES_V2's stored secondary numbers: 2023 to 1e-6; 2024 within 0.005 log loss,
  the stored arrays predating the 2026-10-11 data clean-up; the raw production p is reported beside it as a reference row).
* P1 = M2-p (MINUTES_V2's LightGBM binary; the honest no-new-information comparator).
* P2 = P1 + the four terms (candidate).
* P3 placebo = P2 with `team_b2b_sit` permuted across teams within season.
* P4 ablation = P2 without the tag × strength term.
Terms are as-of; the team rate uses games strictly before the game date; strength = prior-season win% tercile, fixed on the day
the season starts; no tonight's box score or stint is read.

## Metrics, support, inference
Paired log loss delta (arm − P1) on the same rows, game-clustered bootstrap (2000, seed 0); Brier and ECE (10 bins) secondary;
P2 − P0 reported (the production gap). Families: {T-60, Morning} each one primary test (P2 vs P1); BH over the two. MDE: MINUTES_V2
secondary SEs imply ~0.003 log loss per season. Slices (descriptive plus guard): b2b second nights; questionable rows; star rows;
team-strength terciles; first 15 team games.

## Minimum data checks (fail = stop, no model result). To be measured before the freeze on nba.duckdb read-only:
1. P0 reproduces MINUTES_V2's stored secondary log loss: 2023 to 1e-6; 2024 within 0.005 (amended before the freeze after the
   gate measure: 2024 differs by 0.0002 because 12 games' rows changed in the data clean-up; HAZARD_2 precedent).
2. Report snapshots: >= 95% of 2023/2024 games have a T-60 snapshot; the morning snapshot exists for >= 90% of game days
   (measured: morning is thin on b2b days, median 5 rows vs 10; the Morning family is reported with that caveat).
3. `team_b2b_sit` defined (>= 10 prior b2b second nights inside the 82-game window; teams play 13-16 per 82 games, so the
   draft's 20 was impossible and is amended before the freeze after the gate measure) for >= 95% of rows after the warm-up; the
   rest take the league rate (counted). The shrinkage pseudo-count stays 20.
4. Missingness audit of the four terms by realised-minutes bucket; max gap <= 2 pp. Planted same-game and planted-future tests.

### Measured gate (2026-10-11, DRAFT; nba.duckdb read-only, season <= 2024, no arm fitted, no P2 number)
Harness `research/eval/pplay_context_eval.py gate --allow-draft`; full numbers in `reports/prereg_pplay_context/gate.json`.
Eligible rows 2023/2024: 66,220 (33,093 + 33,127).
1. **P0 reproduction: 2023 passes exactly, 2024 FAILS as written.** 2023: 33,093 rows, Platt log loss 0.414712 and raw 0.341926,
   both identical to MINUTES_V2's stored values (abs diff 0). 2024: 33,127 rows now vs 32,986 stored; 12 games have different row
   counts (5 are absent from the stored arrays entirely, 7 differ by 1-3 rows): the stored arrays predate the 2026-10-11 data
   clean-up. Platt abs diff 0.000212 (0.000153 on the 32,817 rows of matching games), raw 0.000241 (0.000247 on matching games); all
   far under the 0.005 that MINUTES_HAZARD_2 check 5c used for 2024 after the same clean-up, none under 1e-6. Whether to amend the
   2024 tolerance (precedent: 2023 1e-6, 2024 0.005) or rerun MINUTES_V2's pplay stage on the cleaned data is Devin's decision.
   Separately: P0 as defined (Platt) is much worse than the raw production p on these rows (0.4147/0.4281 vs 0.3419/0.3556 log
   loss; ECE 0.14), so the "P2 − P0 production gap" will mostly be the Platt map's miscalibration on this row universe, not the four
   terms; P1 (0.2933/0.2981 stored) is the only honest comparator, as the draft already says.
2. **Snapshot coverage: passes.** T-60 snapshot for 2,634 of 2,638 games (99.85%; 2023 99.92%, 2024 99.77%). Morning snapshot (11:00
   stamp, usable under the real-tip gate) on 417 of 420 game days (99.29%; 2023 206/207, 2024 211/213); per game 94.96%. Morning
   thinness confirmed: median 5 rows on games with a team on a second night vs 9 on other games (draft text said 10). A
   previous-evening snapshot exists for only 57.5% of games, so `on_evening_report` is null (never 0) for 42.5% of games.
3. **team_b2b_sit defined: FAILS, and cannot pass as written.** 0.0% of 66,220 rows have >= 20 prior b2b games in the 82-game
   window (median 13, 95th pct 16, max 19; a team plays about 13-16 second nights per 82 games), so every row would take the
   league rate: P2's team term would be a date trend, P3 (placebo) would equal it, and "P2 beats P3 by <= −0.003" could not hold.
   Information only (no rule changed): share of rows with n_b2b_prior >= 10 in the 82-game window is 96.2% (2023 97.1%, 2024
   95.2%), >= 15 is 24.7%; with a 123-game window >= 20 is 46.3%, with 164 games 75.9% (2023 52.2%, 2024 99.6%, because 2022 is
   the only warm-up season). Options for Devin: (a) threshold 10 with the pseudo-count 20 unchanged; (b) a longer window, which
   still fails 2023 at 20; (c) drop term 1 and keep terms 2-4.
4. **Missingness and plants: pass.** Max gap in null rate across realised-minutes buckets [0,10),[10,20),[20,30),[30,inf):
   on_evening_report 0.91 pp, morning terms 0.51 pp, T-60 tag 0.14 pp, all others 0.00 pp (limit 2 pp). Planted same-game box
   score, planted future box scores and reports, planted post-tip snapshots: byte-identical term frames; two positive controls
   (past box-score edit, snapshot edit) do change them, so the plants can fail. Sanity: the harness's own star table reproduces
   the descriptive tables (2,688 b2b star opportunities, sit rate 0.1243; 12,498 other, 0.0695; descriptive 0.124 and 0.068), and
   `is_b2b_second` agrees with the production b2b feature on 100% of rows.
Result: **STOP at the gate** (checks 1 for 2024 and 3 fail as written). The rule is not frozen and no model has been fit.

### Definitions the harness fixed (confirm at the freeze; chosen before any score)
* star = played the team's previous game AND (prior-10 pts >= 20 including that game, min 5 games, OR top-3 prior-10 minutes among
  that team's players of that game); a player who missed the previous game is not a star row. Model gets `star` and `b2b_star`
  (b2b itself is already a P1 feature).
* team rate: each prior b2b second-night team game contributes the share of its star opportunities who sat (traded-away players
  excluded); (sum + 20·L) / (n_b2b + 20), L = league mean of that share over b2b games strictly before the date; 82-game window
  over all team games (playoffs included, crossing seasons); undefined (< 20) rows take L.
* tercile = prior-season regular-season win% rank among 30 teams, ties by team_id, 2 = contender (2022 from data/history).
* tag code unlisted 0, available 1, probable 2, questionable 3, doubtful 4 (null when the game has no such snapshot); morning =
  earliest same-day usable snapshot stamped before 15:00 ET; evening = latest snapshot stamped the previous calendar day.
* Term 3 bundles the tag main effect with its interaction with strength, so P4 (drop term 3) cannot separate "the report tag" from
  "the tag × strength interaction"; the rule keeps it as written.

## Pass rule (per family; 2023 selects, 2024 confirms; all must hold)
P2 − P1 <= −0.005 log loss with CI upper < 0 and BH p < 0.05; ECE not worse by more than +0.005; no slice with n >= 300 worse by
more than +0.01; P2 beats P3 by <= −0.003 (else the team term is noise); P4 reported (which term carries it). A T-60 pass makes P2 a
shadow candidate for the parlay engine's P(play) (logged beside production from the next game day; no promotion); a Morning-only
pass changes the morning briefing's wording on b2b nights, nothing in the engine.

## Kill criteria
No family passes on 2023: close. Do not add terms (travel distance, altitude, national TV, age), change the shrinkage or the
star definition afterwards; at most ONE pre-registered follow-up, then closed.

## Outputs (exact)
`reports/prereg_pplay_context/results.json`, `reports/prereg_pplay_context.md`: `[family, season, arm, n_rows, n_games, logloss,
dlogloss, ci_lo, ci_hi, p, p_bh, brier, ece]`; `[family, season, slice, arm, n, dlogloss, ci_lo, ci_hi]`; `[col, bucket, null_rate]`;
`[test, ok]` for checks 1-4; seeds, SHA, sha256. Code under `research/` only (`research/eval/pplay_context_eval.py`).

=== RESULTS BELOW ===

## Results (run 2026-10-11 10:48-10:50 CT; harness `research/eval/pplay_context_eval.py`, git fb29604 + harness conformance edit below)

Verdict: **NO FAMILY PASSES.** Both fail the placebo requirement in both seasons (P2 does not beat P3 by <= -0.003; P2 - P3 is +0.0002 to
+0.0003, CI spanning 0). Morning 2023 also fails the floor (-0.0028 > -0.005). Kill criterion: closed on the pass rule; no shadow candidate.
At most one pre-registered follow-up remains (new id).

Honest reading: P2 beats P1 by large margins (T-60 -0.0099 / -0.0162) but the team-habit term carries none of it (placebo does as well). The gain
comes from the report-tag terms (P4 without term 3 gives back -0.0084 / -0.0124 of it at T-60), i.e. P1 (M2-p) did not see the T-60 report tag
at all; this confirms the report is the information, not the team habit or star-by-b2b.

Gate (as amended, unmodified rule): all four pass (P0 2023 exact; 2024 diff 0.000212 < 0.005; T-60 99.85%, morning days 99.29%; team_b2b_sit
defined 96.17% of rows; max missingness gap 0.91 pp; plants pass).

Per-family P2 - P1 log loss (game-clustered 95% CI, 2000 resamples, seed 0; BH over {T-60, Morning} within season):
| family | season | n rows (games) | P2 - P1 [CI] | BH p | ECE P1 -> P2 | P2 - P3 [CI] | P2 - P4 [CI] | P2 - P0 [CI] |
|---|---|---|---|---|---|---|---|---|
| T-60 | 2023 | 33093 (1318) | -0.0099 [-0.0121,-0.0078] | 0.0005 | 0.0092 -> 0.0123 | +0.0002 [-0.0005,+0.0008] | -0.0084 [-0.0103,-0.0067] | -0.1313 [-0.1359,-0.1265] |
| T-60 | 2024 | 33127 (1320) | -0.0162 [-0.0189,-0.0136] | 0.0005 | 0.0030 -> 0.0049 | +0.0003 [-0.0001,+0.0006] | -0.0124 [-0.0146,-0.0102] | -0.1465 [-0.1508,-0.1419] |
| Morning | 2023 | 33093 (1318) | -0.0028 [-0.0040,-0.0015] | 0.0005 | 0.0092 -> 0.0105 | +0.0002 [-0.0004,+0.0009] | -0.0013 [-0.0020,-0.0005] | -0.1241 [-0.1288,-0.1197] |
| Morning | 2024 | 33127 (1320) | -0.0073 [-0.0091,-0.0057] | 0.0005 | 0.0030 -> 0.0043 | +0.0000 [-0.0003,+0.0004] | -0.0035 [-0.0045,-0.0026] | -0.1376 [-0.1420,-0.1328] |
Log loss P0 / P1 / P2 / P3 / P4: T-60 2023 0.4147 / 0.2933 / 0.2835 / 0.2833 / 0.2918; T-60 2024 0.4281 / 0.2978 / 0.2816 / 0.2813 / 0.2940;
Morning 2023 P2 0.2906, 2024 P2 0.2905. P0 is the Platt-mapped production p; the raw production p (reference only, gate.json) is 0.3419 (2023)
and 0.3556 (2024), so the P2 - P0 gap is mostly the Platt map's miscalibration (ECE 0.138/0.144), not the terms. ECE stays within +0.005 of P1 in
every cell (max +0.0031). Slices (n >= 300): none worse than +0.01 in any cell; every slice delta is <= 0 except Morning b2b-second-night
2023 (+0.0004 [-0.0016,+0.0024]) and Morning first-15-games 2023 (+0.0004 [-0.0027,+0.0034]), both null. Largest gains: questionable rows
(T-60 -0.156 / -0.267; Morning -0.043 / -0.126). P4 (drop term 3) is the carrier of the gain; term 3 bundles the tag main effect with tag x
strength, so the rule cannot separate those two (stated before the run).

Harness conformance edit (not a rule change): the committed harness still carried the pre-amendment constants (MIN_B2B = 20, 2024 tolerance 1e-6),
so the gate stage failed on the first run (check 1 on 2024 at 0.000212; check 3 at 0%). Before any arm was fit, `MIN_B2B` was set to 10 and the
P0 tolerance to 1e-6 (2023) / 0.005 (2024), exactly as the frozen text states; two unit tests that encoded the old thresholds were updated.
No other code, arm, seed or definition changed.

Unstated details (simplest reading, chosen before scoring): the eight "Definitions the harness fixed" above plus: the placebo permutes
team_b2b_sit across team-games within season (seed 0); P0 reported against both p_platt and p_raw; month blocks start 2023-10-01, each block's
training set is all rows strictly before it including 2022 warm-up; ECE 10 equal-width bins; slices with n < 300 are not guarded.

Artifacts: `reports/prereg_pplay_context/results.json`, `reports/prereg_pplay_context.md`, `reports/prereg_pplay_context/gate.json`,
`reports/prereg_pplay_context/ledger_rows.md` (draft), logs `data/ops/pplay_context_run_20261011.log`. Runtime: gate 5 s, run 1 min 40 s,
report 2 s (CPU, 8 GB box, no swap issue). Seed 0 throughout; season 2025 never loaded.

Freeze: 2026-10-11 10:44 CT, commit 5b07734, sha256 of everything above the line: 3310de87746012b4a9c2a89793e50a1e59b8f4b9527b2a61761bb6f714ebf153 (first 8: 3310de87). Verify with the awk command in the header.
