# MARKET_SHRINK: calibrate the model toward the de-vigged market price and score it on decisions (pre-registration)

Status: DRAFT 2026-10-11. Not frozen. Nothing is fitted or scored by this document. Freeze by a hand that can commit before any fit.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_SHRINK.md | shasum -a 256`.
Seasons: 2023 = 2023-24 (select), 2024 = 2024-25 (report). Season 2025 is never loaded and never touched under this rule.

## Relation to ODDS_HISTORY H2 (T204-T214): partly a replication, and not independent evidence
H2 (docs/prereg/ODDS_HISTORY.md) already fit `logit p = logit p_mkt + w (logit p_model - logit p_mkt)`, w by log loss on 2023-24 per
(stat, book), scored on 2024-25, against Pinnacle and consensus at T-60. Results: log-loss delta vs market, reb/Pinnacle -0.0022
[-0.0035, -0.0009] (beats_market, Holm 0.007); every other cell indistinguishable (pts/Pinnacle -0.0004 [-0.0010, +0.0002]); the one passing
cell was rated WOUNDED by the red team (docs/reviews/redteam_odds_history_reb_blend_2026-10-10.md) and its money figure was
+0.84% [-1.65%, +3.38%] on the model's edge. The data here are the same two seasons, so this rule cannot count as fresh confirmation.
New here: (1) the book is **DraftKings** at its own actual prices, the book the decision score uses; (2) a **temperature on the model**
logit, because the model is over-confident (66.7% stated vs 57.0% realised among its 60%+ legs, decision scoring); (3) a **walk-forward**
fit (monthly expanding window) instead of one fit on the whole select season; (4) the **decision score** (ROI at the actual price) as a
co-primary and as the guard, which H2 lacked; (5) a market-alone **and** production-model comparator on the same legs. w-only (B1) is the
replication arm, so this rule says whether the H2 result survives a different book, a walk-forward fit and a money metric. A pass is
**exploratory** on this data; the only confirmation is the live 2026-27 log under a separate rule.

## Why
The market beats the model alone on log loss (H1 market_better in 12 of 16 cells). If the model carries a little information the
price lacks, a shrink toward the price should keep that information while removing the over-confidence, and its decisions
(EV after the actual price) should then earn more than the unshrunk model's. If not, the answer is "defer to the market".
Prior (mine) that any cell passes the full rule including the decision guard: about 10%; Devin's prior not yet stated.

## Fixed design
Legs: DraftKings `t60` rows of pairs frames `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` (sha256 below), stats pts/reb/ast/fg3m,
rows with both American prices, `p_model`, outcome `over`, de-vigged multiplicative market probability `m` (= `p_over_mult`). Lines are
half-points, so the over event is decided on the integer support; log loss and Brier are on that binary event (no pushes exist).
Pinnacle t60 is reported descriptively only (the same fit procedure, no pass role).
- **Arms**: M = market alone (`m`). P = production model alone (`p_model`). B1 = w-only blend (T = 1; the H2 replication). B2 = blend with
  temperature: `z = logit(p_model) / T`, `logit p_B = logit(m) + w (z - logit(m))`. Clip probabilities to [0.005, 0.995] before logit.
- **Parameter grids (fixed)**: w in {0, 0.05, ..., 1.0}; T in {1.0, 1.25, 1.5, 2.0}. Fit per stat by minimising binary log loss on the fit window.
- **Walk-forward**: for each calendar month X, parameters are fit on all DK t60 legs of that stat from months strictly before X (in 2024 this
  includes all of 2023), scored on month X, and frozen for X. Minimum 2,000 prior legs per stat else the month is skipped and counted
  (2023-10 and 2023-11 are expected burn-in). Playoff months are scored like any other. No leg is ever scored with parameters that saw it.
- **Scores**: (a) paired binary log loss delta, arm minus M, game-clustered 95% CI; (b) Brier delta, secondary; (c) **decision score**:
  `docs/DECISION_SCORING.md` with p = p_B (or p_model for P), EV >= 5% at the actual DK price, ROI per unit with a game-clustered CI,
  n, hit rate, per stat and pooled; 0%, 2%, 10% thresholds descriptive. The market-alone decision comparator is the market-lean arm of that
  doc at the same count (EV of the market's own de-vigged price is about minus the vig, so it places no bets at 5%).
- **Calibration** reported for each arm: reliability by decile with n, mean stated vs realised, ECE; B2 vs P is the over-confidence check.

## Hypotheses and pass condition (per stat cell, DraftKings; family = 4 stats, Holm over the log-loss test)
H: B2 improves on the market on both proper scores AND makes money at the actual price. A cell passes only if ALL hold on season 2024
(report), walk-forward:
1. Log loss: B2 minus M <= -0.0010 (practical floor) with the 95% CI upper bound < 0, Holm across the 4 stats at alpha 0.05.
2. Decision guard (not just log loss): B2 ROI at EV >= 5% is >= +1.0% with CI lower > 0 AND n >= 300 AND B2 ROI minus market-lean ROI (same
   count, games resampled jointly) has CI lower > 0. Because market-lean is about minus the vig, (2) alone is easy; the +1.0% / CI floor is the real bar.
3. No worse than the production model: B2 ROI >= P ROI (point estimate, EV >= 5%); otherwise the shrink adds nothing and the cell is "no_gain_over_P".
4. Select-season sign: on 2023 (walk-forward, same procedure) condition 1 has a negative point estimate and condition 2 a non-negative ROI.
5. A planted shuffle (p_model permuted within game, stat and book, default_rng(0)) run through the identical pipeline must show no passing cell and a
   log-loss delta >= 0 (the shuffled forecast cannot beat the market).
Verdict per cell: `passes`, `fails_logloss`, `fails_decision_guard`, `no_gain_over_P`, `not_in_both_seasons`. If no cell passes the verdict is
**`no_positive_ev_found` / defer to the market** and that is a result. A pass is reported as exploratory (see replication note) and
never promotes anything. Temperature ablation reported beside it: does B2 beat B1 (T matters) or is T = 1 chosen (w alone does the work).

## Gate (data availability, counted 2026-10-11; no arm scored, no parameter fit)
DraftKings t60 legs with both prices, `p_model` and the outcome: 2023 pts 14,354, reb 15,052, ast 9,650, fg3m 14,191; 2024 pts 15,882, reb 14,748,
ast 10,574, fg3m 12,548. Pinnacle (descriptive): reb 6,339 / 6,645, ast 6,714 / 6,771. All t60 snapshots precede tip (100%). Months with legs:
2023-10 to 2024-06 (9) and 2024-10 to 2025-06 (9); 2023-10 and 2023-11 fall under the 2,000-leg burn-in for the smaller stats. Every cell is far above the 300-leg floor.
The decision condition needs n >= 300 bets at EV >= 5% per stat in 2024; the 2024 counts of EV >= 5% bets for the unshrunk model are
28,017 pooled and 5,364 for the smallest stat (ast), but B2 will place fewer bets (shrinking reduces EV); if a cell has < 300 B2 bets its decision
test is `insufficient sample` and the cell cannot pass. Inputs: PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569;
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c (re-checked by `run`). No DuckDB is opened.

## What each choice protects against
- Walk-forward monthly fit: protects against a w chosen with knowledge of the months it is scored on (H2 fit once on the whole select season).
- Co-primary decision score with a +1.0% floor and CI: protects against a log-loss gain that makes no money at the actual price (H2's money was +0.84% [-1.65, +3.38]).
- B2 vs P condition: protects against calling a shrink useful when the unshrunk model decides as well.
- Temperature grid fixed and small, T ablation: protects against a flexible calibrator absorbing noise; shows whether T adds anything.
- Planted shuffle: protects against a leak in the join or the walk-forward windows.
- Same-data disclosure: protects against reading a second look at 2023-24 as independent confirmation.
- Pinnacle descriptive only: protects against book-shopping the pass cell.

## Outputs (exact; code to be written after the freeze, none exists yet)
`reports/market_shrink/results.json`, `reports/market_shrink.md`, ledger rows with T### placeholders; code under `research/eval/market_shrink_eval.py`
and tests; the decision scorer reuses `nba/markets/decision_scoring.py` unchanged. About 1 minute, light, takes no lock.

=== RESULTS BELOW ===
