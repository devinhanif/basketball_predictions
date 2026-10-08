# As-of RAPM as the player value for injury-adjusted Elo

Status: PRE-REGISTERED 2026-10-08, before any real-data run of
`nba/eval/rapm_injury_elo_eval.py`.

## Question
Does a regularized adjusted plus-minus (RAPM) value, built from real
5-on-5 lineups in `possessions`, replace the box-composite player value used by
production `rung0_injury_elo v1` (MOV-Elo logit + ridge coefficient x
difference in value of players OUT on the pre-tip official report)?

## Method (frozen before running)
* Rows: one per possession (2022-2024 only; season 2025 is never loaded).
  Columns: +1 for each of the 5 offensive players, -1 for each of the 5
  defensive players, plus an unpenalized intercept and an unpenalized
  "offense is home" column. Target: points on the possession x 100.
* One coefficient per player (beta); published `rapm = 2 * beta` = net points
  per 100 possessions (offense + defense). Positive = good. Separate
  ORAPM/DRAPM were not built.
* Ridge toward 0 (league-average player) solved via the Gram matrix
  `X'WX` accumulated per month and season, so a refit takes well under a second.
* As-of: the snapshot for month M uses possessions strictly before the first
  day of M. Prior seasons enter with weight `history_decay ** (season gap)`
  (pre-set 0.5, NOT tuned; there is no earlier season to tune it on).
  The optional "shrink toward last-season RAPM" prior exists in the solver but
  is OFF (`prior_carry = 0`); a box-based prior is not built.
* Lambda: chosen by walk-forward CV on season 2022 only (the 2022-23 season):
  fit on months before month M, score possession MSE in month M. Grid fixed in
  the config. No tuning on 2023 or 2024.
* Plug-in: for each player flagged OUT (or DOUBTFUL at weight 0.5) on the
  latest official report at least 60 min before the 19:00 ET tip proxy,
  `value = max(rapm, 0) * as_of_minutes / 48` (as-of minutes = production's
  decayed, shrunk minutes/game), summed per side, differenced (away - home),
  divided by VALUE_SCALE=10. Same report rule, same MOV-Elo params (frozen),
  same monthly walk-forward refit of the logit coefficients as production.
* Variants: (a) production box value, (b) RAPM value, (c) both (4 features).
  Primary candidate = (b). (c) is secondary and cannot be substituted.

## Decision rule (primary candidate b vs production a)
KEEP RAPM replacing box value iff BOTH:
1. paired per-game bootstrap 95% CI of the log-loss delta (b - a), over all
   scored 2022-2024 games, lies entirely below 0;
2. no slice of rotation players OUT = 0 / 1 / 2+ (n >= 100) is worse than (a)
   by more than 0.005 log loss (point delta).
Otherwise REJECT (negative result stands). If KEEP, it earns one separate
pre-registered clean 2025 touch, run by someone else, not in this work.
Brier and ECE deltas with CIs are reported but are not gating.
One game = one row, so the per-game paired bootstrap is game-clustered.

## Results
See the bottom of this file (filled in after the run).

### Run 2026-10-08 (single run, 3,951 scored games, 2022-2024; ~6 s wall clock)
Command: `uv run python -m nba.eval.rapm_injury_elo_eval --db nba.duckdb --config configs/injury_elo.yaml --out-dir data/rapm`
(786,814 lineup-complete possessions; season 2025 not loaded; lambda CV on season
2022 chose 4000 (MSE 13952.2 vs 13961.5 for the intercept+home null: very small
gain); 26 monthly refits at ~0.03 s each.)

| variant | log loss | Brier | ECE | dLL vs (a) [95% CI] |
|---|---|---|---|---|
| MOV-Elo | 0.6245 | 0.2180 | 0.0166 | |
| (a) box value (production) | 0.6167 | 0.2144 | 0.0244 | |
| (b) RAPM value | 0.6198 | 0.2158 | 0.0134 | +0.0032 [-0.0003, +0.0066] |
| (c) both | ~0.6167 | ~0.2144 | ~0.0239 | ~0 (CI spans 0; see results.json) |

Slices dLL (b - a): rot_out=0 -0.0004 (n=541), =1 +0.0008 (n=800), =2+ +0.0046 (n=2610).
Verdict: REJECT. The CI does not exclude 0 and the point estimate is worse.
RAPM does not replace box value. ECE is lower for (b) but not gating and its CI
spans 0. corr(box feature, RAPM feature) = 0.69. Season-to-season RAPM
correlation (single-season fits, >=1500 possessions) is only 0.34-0.36:
the player ratings are noisy, which is the likely reason.
