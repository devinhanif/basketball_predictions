# PPLAY_CONTEXT: team back-to-back habit and questionable-by-team-strength in the P(play) model (pre-registration)

Status: DRAFT 2026-10-11 ~10:50 CT, NOT FROZEN; awaiting Devin's "confirmed" (Devin: "draft it", 2026-10-11). Committed by the
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
* P0 production P(play) + Platt (reproduces MINUTES_V2's stored secondary numbers to 1e-6).
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
1. P0 reproduces MINUTES_V2's stored secondary log loss (2023/2024) to 1e-6.
2. Report snapshots: >= 95% of 2023/2024 games have a T-60 snapshot; the morning snapshot exists for >= 90% of game days
   (measured: morning is thin on b2b days, median 5 rows vs 10; the Morning family is reported with that caveat).
3. `team_b2b_sit` defined (>= 20 prior b2b games) for >= 95% of rows after the warm-up; the rest take the league rate (counted).
4. Missingness audit of the four terms by realised-minutes bucket; max gap <= 2 pp. Planted same-game and planted-future tests.

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

(not run)
