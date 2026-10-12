# Confusion matrices: props, game lines, quarters and halves (2026-10-11)

**DESCRIPTIVE EXPLORATION, not a claim.** No pre-registration; nothing here may change production. Seasons 2023 (2023-24) and 2024 (2024-25) are scored. 2022 is only training history for the baselines. Season 2025 (frozen holdout) is never read. Every rate carries n and a game-clustered bootstrap 95% CI (B=2000, seed 2026). Pushes (the result lands exactly on a whole-number line) are counted and excluded.

Data snapshot: tables copied from `nba.duckdb` (file last modified 2026-10-11T20:20:13, copied 2026-10-11T20:23:54, repo HEAD 13ebb7e) while the archivist was rewriting possessions, stints and 10 neutral-site games. Game-level scores come from the `games` table and play-by-play files as they stood then. Odds come from `data/odds/odds_history.duckdb` and `reports/odds_history/*` (frozen pulls).

## Summary

Plain reading of the CI: if the model-minus-comparator interval spans zero we cannot say either is better.

| target | model | n | model accuracy [95% CI] | market / baseline accuracy [95% CI] | model minus comparator |
|---|---|---|---|---|---|
| prop over/under, pts | context-residual | 30236 | 51.7% [51.2, 52.3] | market lean (DK no-vig, rows with a lean): 52.7% [52.1, 53.3] | -1.0 pts [-1.8, -0.1] (n=25790) |
| prop over/under, pts | recency baseline | 30236 | 50.8% [50.3, 51.4] | always-under: 51.4% | vs context-residual +0.9 pts [+0.3, +1.5] (model minus recency) |
| prop over/under, reb | context-residual | 29800 | 54.9% [54.4, 55.5] | market lean (DK no-vig, rows with a lean): 54.8% [54.2, 55.4] | +0.4 pts [-0.2, +1.1] (n=27928) |
| prop over/under, reb | recency baseline | 29800 | 54.1% [53.5, 54.7] | always-under: 52.7% | vs context-residual +0.8 pts [+0.3, +1.3] (model minus recency) |
| prop over/under, ast | context-residual | 20224 | 54.1% [53.4, 54.7] | market lean (DK no-vig, rows with a lean): 55.2% [54.5, 55.9] | -0.8 pts [-1.6, -0.0] (n=19170) |
| prop over/under, ast | recency baseline | 20224 | 52.8% [52.1, 53.5] | always-under: 51.7% | vs context-residual +1.3 pts [+0.6, +2.0] (model minus recency) |
| prop over/under, fg3m | context-residual | 26739 | 57.3% [56.7, 57.9] | market lean (DK no-vig, rows with a lean): 58.2% [57.6, 58.8] | -0.6 pts [-1.3, -0.0] (n=25778) |
| prop over/under, fg3m | recency baseline | 26739 | 56.7% [56.1, 57.3] | always-under: 52.7% | vs context-residual +0.6 pts [+0.1, +1.1] (model minus recency) |
| prop over/under, pooled | context-residual | 106999 | 54.5% [54.1, 54.8] | market lean (DK no-vig, rows with a lean): 55.2% [54.9, 55.5] | -0.4 pts [-0.8, -0.1] (n=98666) |
| prop over/under, pooled | recency baseline | 106999 | 53.6% [53.3, 53.9] | always-under: 52.1% | vs context-residual +0.9 pts [+0.6, +1.2] (model minus recency) |
| game winner | injury-Elo (production) | 2633 | 67.3% [65.4, 69.0] | Pinnacle ML favourite: 68.6% [66.8, 70.5] | -1.3 pts [-2.6, -0.1] |
| game winner | MOV-Elo | 2633 | 65.2% [63.4, 67.0] | Pinnacle ML favourite: 68.6% [66.8, 70.5] | -3.4 pts [-4.7, -2.0] |
| spread (ATS cover) | injury-Elo margin baseline | 2587 | 49.9% [47.9, 51.8] | DK price lean: 50.1% [47.8, 52.4]; always-favourite: 49.9% [48.0, 51.8] | +0.1 pts [-3.3, +3.1] |
| spread (ATS cover) | MOV-Elo margin baseline | 2587 | 48.8% [46.9, 50.7] | DK price lean: 50.1% [47.8, 52.4]; always-favourite: 49.9% [48.0, 51.8] | -1.1 pts [-4.4, +2.1] |
| total (over/under) | team-recency baseline | 2605 | 50.2% [48.3, 52.1] | DK price lean: 50.7% [48.2, 53.2]; always-under: 48.6% | -1.6 pts [-5.0, +1.8] |
| Q1 winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 55.6% [53.7, 57.5] | always-home: 51.1% [49.2, 52.9]; NO MARKET | decisive-only acc 58.5% [56.4, 60.3] vs full game 67.3% [65.4, 69.0] |
| Q2 winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 55.3% [53.3, 57.2] | always-home: 49.4% [47.5, 51.3]; NO MARKET | decisive-only acc 58.2% [56.4, 60.3] vs full game 67.3% [65.4, 69.0] |
| Q3 winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 56.1% [54.2, 58.0] | always-home: 51.3% [49.3, 53.2]; NO MARKET | decisive-only acc 58.9% [56.9, 60.8] vs full game 67.3% [65.4, 69.0] |
| Q4 winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 51.5% [49.6, 53.4] | always-home: 47.2% [45.3, 49.1]; NO MARKET | decisive-only acc 54.6% [52.6, 56.5] vs full game 67.3% [65.4, 69.0] |
| 1H winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 59.7% [57.9, 61.6] | always-home: 52.4% [50.5, 54.3]; NO MARKET | decisive-only acc 61.8% [59.9, 63.7] vs full game 67.3% [65.4, 69.0] |
| 2H winner (3-class H/tie/A) | injury-Elo-mapped baseline | 2633 | 57.2% [55.3, 58.9] | always-home: 51.3% [49.3, 53.1]; NO MARKET | decisive-only acc 59.5% [57.6, 61.3] vs full game 67.3% [65.4, 69.0] |

### In plain words

- **Props.** Picking over/under from the model is right 54.5% [54.1, 54.8] of the time across 106999 legs; blindly picking under is right 52.1%, and the market's own lean (on rows where it has one) is right 55.2% [54.9, 55.5]. On those same rows the model is -0.4 pts [-0.8, -0.1] relative to the market, so the market is slightly better and the model adds nothing over it. The model beats the plain recency baseline by +0.9 pts [+0.6, +1.2]. The model says OVER on only 29.3% [28.8, 29.8] of legs (counts are right-skewed, so its median sits under the line), so it mostly says UNDER and its recall for overs is low. On the legs where it is confident (|P-0.5| >= 0.10; n=42456) accuracy is about 57.5%, above the 52.4% break-even; the market's no-vig probabilities rarely get that far from 0.5 (see the bins), so there is no like-for-like market number for those legs, and accuracy is not profit.

- **Game winners.** Injury-Elo picks the right team 67.3% [65.4, 69.0] of 2633 games versus 68.6% [66.8, 70.5] for always taking the Pinnacle favourite (-1.3 pts [-2.6, -0.1]). MOV-Elo is lower still. The market wins, as before.

- **Spread and total.** Against the line our baselines are coin flips (49.9% [47.9, 51.8] on 2587 spreads, 50.2% [48.3, 52.1] on 2605 totals), and their average error versus the real outcome is larger than the market's (spread 10.92 vs 10.64 points; total 14.95 vs 14.18). No stored production forecast exists for margin or total, so these are baselines only.

- **Quarters and halves.** Picking who wins Q1 is right 55.6% [53.7, 57.5] (ties count as misses and happen 4.9% [4.1, 5.7] of the time); the first half is 59.7% [57.9, 61.6]; Q4 is the hardest (51.5% [49.6, 53.4]). Full-game accuracy minus period accuracy is +9.0 pts [+6.6, +11.3] for Q1 and +5.6 pts [+3.5, +7.6] for the first half, restricted to games with a decisive period. No market exists for any of these.

**Where we have no market to compare:** quarter and half winners, period spreads and period totals (the odds history has `h2h`, `spreads`, `totals` and four player-prop markets only; no period lines were invented). Spreads and totals have no model-vs-market accuracy contest in the usual sense: the market line is set so each side is about 50/50, so the 'market' column is just the tiny lean in the no-vig prices (and trivial rules like always-favourite). The comparison that matters there is MAE of line vs outcome, given below.

Break-even note: at -110 both ways a bettor needs about 52.4% on decided legs.

## 1. Player props (DraftKings T-60 main line)

Model = stored production-style context-residual walk-forward forecasts (`reports/odds_history/model_rows_*`, month-block refit, integer support; this is the same set the ODDS_HISTORY test scored). P(over) is the integer-support probability of beating the line; on whole-number lines the push mass is removed. Recency baseline = same rows, recency mean and sd, turned into an integer-grid distribution (Gaussian-rounded for pts, negative binomial for reb/ast/fg3m), same push handling. `prop_predictions` in `nba.duckdb` was not used: it stores sparse tail thresholds from an older gamma run, not a full integer distribution. Predicted OVER means P(over) > 0.5. The market lean is the no-vig DK implied P(over) > 0.5; where no-vig is exactly 0.5 (even -110/-110) the market has no lean and those rows are left out of the market matrices and of the paired comparisons.

Quotes in the DK T-60 frame: 108685; no model row (player did not play, or no forecast row): 1686. Pushes: 0.0% because every DraftKings main prop line in this pull is a half-point (x.5), so a push cannot happen; spreads and totals below do have whole-number lines and their pushes are excluded.

| stat | quotes with model | scored | pushes | odd lines | model-certain-push |
|---|---|---|---|---|---|
| pts | 30236 | 30236 | 0 | 0 | 0 |
| reb | 29800 | 29800 | 0 | 0 | 0 |
| ast | 20224 | 20224 | 0 | 0 | 0 |
| fg3m | 26739 | 26739 | 0 | 0 | 0 |

### pts

n = 30236 scored legs over 2616 games. Base rate of overs: 48.6% [48.0, 49.1]. Rows where the market has a lean: 25790.

Context-residual model (all scored rows); rows = predicted, columns = actual:

| predicted \ actual | actual over | actual under |
|---|---|---|
| predict OVER | 4671 | 4583 |
| predict UNDER | 10010 | 10972 |

accuracy 51.7% [51.2, 52.3]; precision(over) 50.5% [49.5, 51.5]; recall(over) 31.8% [30.9, 32.7]; calls over on 30.6% [29.9, 31.3] of legs.

| rule | rows | accuracy | precision(over) | recall(over) | calls over |
|---|---|---|---|---|---|
| context-residual | 30236 | 51.7% [51.2, 52.3] | 50.5% [49.5, 51.5] | 31.8% [30.9, 32.7] | 30.6% [29.9, 31.3] |
| recency baseline | 30236 | 50.8% [50.3, 51.4] | 49.4% [48.7, 50.2] | 54.4% [53.3, 55.5] | 53.5% [52.5, 54.4] |
| context-residual (market-lean rows) | 25790 | 51.8% [51.2, 52.3] | 50.8% [49.8, 51.9] | 32.4% [31.4, 33.3] | 31.1% [30.3, 31.8] |
| recency baseline (market-lean rows) | 25790 | 50.9% [50.3, 51.5] | 49.7% [48.9, 50.5] | 54.9% [53.8, 56.0] | 53.8% [52.9, 54.7] |
| market no-vig lean (market-lean rows) | 25790 | 52.7% [52.1, 53.3] | 51.5% [50.6, 52.4] | 51.9% [50.9, 52.8] | 49.1% [48.5, 49.7] |

Model minus market (same market-lean rows): -1.0 pts [-1.8, -0.1]. When model and market disagree (n=10940), the model is right 48.9% [47.9, 49.8].

Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):

| bin | model acc (n) | recency acc (n) | market acc (n) |
|---|---|---|---|
| 0.00-0.03 | 50.2% [49.0, 51.4] (n=6578) | 50.3% [49.2, 51.5] (n=6583) | 52.2% [51.4, 52.9] (n=18943) |
| 0.03-0.06 | 50.2% [49.0, 51.5] (n=6263) | 49.7% [48.5, 51.0] (n=6005) | 54.1% [52.9, 55.4] (n=6680) |
| 0.06-0.10 | 52.1% [51.0, 53.3] (n=7023) | 50.7% [49.5, 52.0] (n=6545) | 58.1% [50.6, 65.9] (n=167) |
| 0.10-0.15 | 53.1% [51.7, 54.4] (n=5697) | 51.7% [50.4, 53.0] (n=5464) | - |
| 0.15-0.25 | 53.6% [52.1, 55.2] (n=3993) | 51.4% [49.9, 52.9] (n=4456) | - |
| 0.25-0.50 | 54.4% [50.4, 58.2] (n=682) | 53.8% [50.8, 56.5] (n=1183) | - |

### reb

n = 29800 scored legs over 2613 games. Base rate of overs: 47.3% [46.8, 47.9]. Rows where the market has a lean: 27928.

Context-residual model (all scored rows); rows = predicted, columns = actual:

| predicted \ actual | actual over | actual under |
|---|---|---|
| predict OVER | 4781 | 4104 |
| predict UNDER | 9324 | 11591 |

accuracy 54.9% [54.4, 55.5]; precision(over) 53.8% [52.8, 54.8]; recall(over) 33.9% [32.9, 34.8]; calls over on 29.8% [29.0, 30.5] of legs.

| rule | rows | accuracy | precision(over) | recall(over) | calls over |
|---|---|---|---|---|---|
| context-residual | 29800 | 54.9% [54.4, 55.5] | 53.8% [52.8, 54.8] | 33.9% [32.9, 34.8] | 29.8% [29.0, 30.5] |
| recency baseline | 29800 | 54.1% [53.5, 54.7] | 52.0% [51.1, 53.0] | 40.2% [39.1, 41.2] | 36.5% [35.7, 37.3] |
| context-residual (market-lean rows) | 27928 | 55.2% [54.6, 55.8] | 54.1% [53.0, 55.1] | 34.6% [33.6, 35.5] | 30.2% [29.4, 30.9] |
| recency baseline (market-lean rows) | 27928 | 54.4% [53.9, 55.0] | 52.3% [51.3, 53.2] | 40.5% [39.5, 41.5] | 36.6% [35.8, 37.5] |
| market no-vig lean (market-lean rows) | 27928 | 54.8% [54.2, 55.4] | 52.2% [51.3, 53.0] | 51.6% [50.8, 52.4] | 46.7% [46.2, 47.3] |

Model minus market (same market-lean rows): +0.4 pts [-0.2, +1.1]. When model and market disagree (n=8601), the model is right 50.7% [49.6, 51.8].

Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):

| bin | model acc (n) | recency acc (n) | market acc (n) |
|---|---|---|---|
| 0.00-0.03 | 52.4% [51.2, 53.6] (n=6271) | 51.7% [50.4, 52.9] (n=6289) | 51.3% [50.2, 52.3] (n=8342) |
| 0.03-0.06 | 53.0% [51.7, 54.3] (n=5989) | 51.5% [50.2, 52.8] (n=5854) | 54.5% [53.6, 55.3] (n=13260) |
| 0.06-0.10 | 54.6% [53.5, 55.8] (n=6948) | 53.9% [52.7, 55.2] (n=6471) | 59.6% [58.3, 60.9] (n=6050) |
| 0.10-0.15 | 56.7% [55.4, 57.9] (n=5679) | 55.5% [54.2, 56.8] (n=5536) | 67.4% [62.2, 72.8] (n=276) |
| 0.15-0.25 | 58.2% [56.7, 59.7] (n=4302) | 57.9% [56.5, 59.3] (n=4366) | - |
| 0.25-0.50 | 64.3% [60.4, 67.9] (n=611) | 60.8% [58.3, 63.4] (n=1284) | - |

### ast

n = 20224 scored legs over 2608 games. Base rate of overs: 48.3% [47.7, 49.0]. Rows where the market has a lean: 19170.

Context-residual model (all scored rows); rows = predicted, columns = actual:

| predicted \ actual | actual over | actual under |
|---|---|---|
| predict OVER | 3244 | 2755 |
| predict UNDER | 6532 | 7693 |

accuracy 54.1% [53.4, 54.7]; precision(over) 54.1% [52.9, 55.3]; recall(over) 33.2% [32.2, 34.2]; calls over on 29.7% [28.9, 30.4] of legs.

| rule | rows | accuracy | precision(over) | recall(over) | calls over |
|---|---|---|---|---|---|
| context-residual | 20224 | 54.1% [53.4, 54.7] | 54.1% [52.9, 55.3] | 33.2% [32.2, 34.2] | 29.7% [28.9, 30.4] |
| recency baseline | 20224 | 52.8% [52.1, 53.5] | 51.4% [50.3, 52.5] | 42.9% [41.7, 44.1] | 40.4% [39.5, 41.2] |
| context-residual (market-lean rows) | 19170 | 54.3% [53.7, 55.0] | 54.1% [52.9, 55.4] | 33.6% [32.6, 34.7] | 29.9% [29.1, 30.6] |
| recency baseline (market-lean rows) | 19170 | 53.0% [52.3, 53.7] | 51.4% [50.3, 52.5] | 43.1% [41.9, 44.3] | 40.4% [39.5, 41.3] |
| market no-vig lean (market-lean rows) | 19170 | 55.2% [54.5, 55.9] | 53.6% [52.6, 54.6] | 50.7% [49.7, 51.8] | 45.5% [44.8, 46.2] |

Model minus market (same market-lean rows): -0.8 pts [-1.6, -0.0]. When model and market disagree (n=5678), the model is right 48.6% [47.3, 50.0].

Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):

| bin | model acc (n) | recency acc (n) | market acc (n) |
|---|---|---|---|
| 0.00-0.03 | 51.3% [49.8, 52.7] (n=4368) | 49.7% [48.2, 51.1] (n=4415) | 50.8% [49.3, 52.2] (n=4564) |
| 0.03-0.06 | 51.8% [50.4, 53.5] (n=4019) | 50.9% [49.4, 52.4] (n=4038) | 54.5% [53.4, 55.5] (n=8337) |
| 0.06-0.10 | 54.3% [52.8, 55.7] (n=4473) | 53.9% [52.5, 55.4] (n=4313) | 58.7% [57.3, 60.1] (n=5306) |
| 0.10-0.15 | 55.4% [53.8, 56.9] (n=3935) | 54.0% [52.3, 55.6] (n=3629) | 62.6% [59.4, 65.7] (n=927) |
| 0.15-0.25 | 58.7% [56.9, 60.6] (n=2892) | 55.3% [53.5, 57.2] (n=2919) | 66.7% [51.4, 81.1] (n=36) |
| 0.25-0.50 | 57.2% [53.1, 61.3] (n=537) | 57.5% [54.3, 60.5] (n=910) | - |

### fg3m

n = 26739 scored legs over 2612 games. Base rate of overs: 47.3% [46.7, 47.9]. Rows where the market has a lean: 25778.

Context-residual model (all scored rows); rows = predicted, columns = actual:

| predicted \ actual | actual over | actual under |
|---|---|---|
| predict OVER | 4206 | 2969 |
| predict UNDER | 8447 | 11117 |

accuracy 57.3% [56.7, 57.9]; precision(over) 58.6% [57.5, 59.8]; recall(over) 33.2% [32.4, 34.1]; calls over on 26.8% [26.3, 27.4] of legs.

| rule | rows | accuracy | precision(over) | recall(over) | calls over |
|---|---|---|---|---|---|
| context-residual | 26739 | 57.3% [56.7, 57.9] | 58.6% [57.5, 59.8] | 33.2% [32.4, 34.1] | 26.8% [26.3, 27.4] |
| recency baseline | 26739 | 56.7% [56.1, 57.3] | 55.5% [54.5, 56.5] | 42.8% [41.8, 43.7] | 36.5% [35.8, 37.1] |
| context-residual (market-lean rows) | 25778 | 57.6% [57.0, 58.1] | 58.9% [57.8, 60.1] | 33.9% [33.0, 34.7] | 27.2% [26.6, 27.7] |
| recency baseline (market-lean rows) | 25778 | 57.0% [56.4, 57.6] | 55.9% [54.9, 56.9] | 43.2% [42.3, 44.2] | 36.6% [35.9, 37.3] |
| market no-vig lean (market-lean rows) | 25778 | 58.2% [57.6, 58.8] | 56.0% [55.2, 57.0] | 53.6% [52.7, 54.5] | 45.2% [44.6, 45.8] |

Model minus market (same market-lean rows): -0.6 pts [-1.3, -0.0]. When model and market disagree (n=6032), the model is right 48.7% [47.4, 50.0].

Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):

| bin | model acc (n) | recency acc (n) | market acc (n) |
|---|---|---|---|
| 0.00-0.03 | 50.5% [49.1, 52.1] (n=3991) | 50.8% [49.3, 52.2] (n=4669) | 50.9% [49.5, 52.4] (n=4274) |
| 0.03-0.06 | 53.8% [52.2, 55.3] (n=3817) | 53.9% [52.4, 55.3] (n=4558) | 55.1% [54.0, 56.3] (n=7435) |
| 0.06-0.10 | 55.3% [54.0, 56.7] (n=4803) | 54.7% [53.3, 55.9] (n=5601) | 59.2% [58.0, 60.4] (n=7418) |
| 0.10-0.15 | 57.9% [56.6, 59.2] (n=5621) | 59.2% [57.9, 60.5] (n=5675) | 63.4% [62.1, 64.7] (n=5238) |
| 0.15-0.25 | 62.3% [61.2, 63.4] (n=7384) | 61.6% [60.3, 62.9] (n=5149) | 69.8% [67.4, 72.2] (n=1275) |
| 0.25-0.50 | 66.1% [63.2, 68.8] (n=1123) | 68.1% [65.2, 71.0] (n=1087) | 83.3% [76.8, 89.1] (n=138) |

### pooled

n = 106999 scored legs over 2616 games. Base rate of overs: 47.9% [47.5, 48.2]. Rows where the market has a lean: 98666.

Context-residual model (all scored rows); rows = predicted, columns = actual:

| predicted \ actual | actual over | actual under |
|---|---|---|
| predict OVER | 16902 | 14411 |
| predict UNDER | 34313 | 41373 |

accuracy 54.5% [54.1, 54.8]; precision(over) 54.0% [53.4, 54.6]; recall(over) 33.0% [32.4, 33.5]; calls over on 29.3% [28.8, 29.8] of legs.

| rule | rows | accuracy | precision(over) | recall(over) | calls over |
|---|---|---|---|---|---|
| context-residual | 106999 | 54.5% [54.1, 54.8] | 54.0% [53.4, 54.6] | 33.0% [32.4, 33.5] | 29.3% [28.8, 29.8] |
| recency baseline | 106999 | 53.6% [53.3, 53.9] | 51.7% [51.2, 52.2] | 45.4% [44.7, 46.1] | 42.0% [41.4, 42.7] |
| context-residual (market-lean rows) | 98666 | 54.8% [54.4, 55.1] | 54.4% [53.8, 55.0] | 33.6% [33.0, 34.2] | 29.6% [29.1, 30.1] |
| recency baseline (market-lean rows) | 98666 | 53.9% [53.6, 54.2] | 52.1% [51.5, 52.6] | 45.5% [44.8, 46.3] | 41.8% [41.2, 42.5] |
| market no-vig lean (market-lean rows) | 98666 | 55.2% [54.9, 55.5] | 53.2% [52.7, 53.7] | 52.0% [51.5, 52.4] | 46.7% [46.4, 47.0] |

Model minus market (same market-lean rows): -0.4 pts [-0.8, -0.1]. When model and market disagree (n=31251), the model is right 49.3% [48.7, 49.9].

Confidence bins (|P(over) - 0.5| of each rule's own probability; accuracy in the bin, n):

| bin | model acc (n) | recency acc (n) | market acc (n) |
|---|---|---|---|
| 0.00-0.03 | 51.1% [50.4, 51.8] (n=21208) | 50.7% [50.0, 51.4] (n=21956) | 51.6% [51.1, 52.1] (n=36123) |
| 0.03-0.06 | 52.0% [51.4, 52.7] (n=20088) | 51.4% [50.7, 52.0] (n=20455) | 54.6% [54.0, 55.1] (n=35712) |
| 0.06-0.10 | 54.0% [53.3, 54.6] (n=23247) | 53.2% [52.5, 53.8] (n=22930) | 59.2% [58.5, 59.9] (n=18941) |
| 0.10-0.15 | 55.8% [55.1, 56.5] (n=20932) | 55.3% [54.5, 56.0] (n=20304) | 63.5% [62.3, 64.7] (n=6441) |
| 0.15-0.25 | 58.9% [58.2, 59.7] (n=18571) | 56.9% [56.1, 57.7] (n=16890) | 69.7% [67.3, 72.2] (n=1311) |
| 0.25-0.50 | 61.4% [59.6, 63.3] (n=2953) | 60.0% [58.5, 61.6] (n=4464) | 83.3% [76.8, 89.1] (n=138) |

**One real hit, one real miss (points, 2024-25 season, the model's most confident calls):**

- HIT: 2025-03-05 Sam Hauser pts, DK line 12.5; model P(over) 0.116, market no-vig P(over) 0.48; actual 5.0.
- MISS: 2024-11-08 Goga Bitadze pts, DK line 10.5; model P(over) 0.06, market no-vig P(over) 0.49; actual 11.0.

## 2. Game lines

Winner rows: 2633 games in 2023+2024 with both a model and a Pinnacle T-60 moneyline (87 used the consensus fallback, as in ODDS_HISTORY). Home-win base rate 54.7% [52.8, 56.7]. Rows = predicted, columns = actual (positive class = home win).

**injury-Elo (production)** accuracy 67.3% [65.4, 69.0]; precision(home) 68.2% [65.9, 70.6]; recall(home) 75.1% [72.7, 77.2]

| predicted \ actual | home won | away won |
|---|---|---|
| predict HOME | 1081 | 503 |
| predict AWAY | 358 | 691 |

**MOV-Elo** accuracy 65.2% [63.4, 67.0]; precision(home) 66.2% [63.9, 68.6]; recall(home) 74.4% [72.0, 76.6]

| predicted \ actual | home won | away won |
|---|---|---|
| predict HOME | 1070 | 546 |
| predict AWAY | 369 | 648 |

**Pinnacle moneyline favourite** accuracy 68.6% [66.8, 70.5]; precision(home) 68.9% [66.6, 71.2]; recall(home) 77.6% [75.4, 79.7]

| predicted \ actual | home won | away won |
|---|---|---|
| predict HOME | 1117 | 504 |
| predict AWAY | 322 | 690 |

Injury-Elo minus market: -1.3 pts [-2.6, -0.1]. MOV-Elo minus market: -3.4 pts [-4.7, -2.0].

| market confidence | market acc | injury-Elo acc |
|---|---|---|
| |P-0.5| 0.00-0.10 | 54.3% [50.5, 58.0] (n=709) | 51.9% [48.1, 55.7] |
| |P-0.5| 0.10-0.20 | 63.9% [60.6, 67.3] (n=794) | 61.7% [58.4, 65.1] |
| |P-0.5| 0.20-0.50 | 81.0% [78.6, 83.3] (n=1130) | 80.9% [78.5, 83.2] |

- HIT: 2025-03-21 Charlotte Hornets at Oklahoma City Thunder; injury-Elo P(home) 0.976, market 0.925; final 141-106 (home-away).
- MISS: 2025-03-15 Washington Wizards at Denver Nuggets; injury-Elo P(home) 0.924, market 0.837; final 123-126 (home-away).

### Spread (ATS) and total: baselines, not production forecasts

We have no stored production forecast of the margin or the total, so these are transparent baselines labelled as such. Spread: home margin regressed on logit(Elo win probability), fit on seasons strictly before the scored season (2022 only for 2023; 2022-23 for 2024), normal error; predicted cover = baseline margin + DK home spread > 0. The DK T-60 spread is the line. Total: for each game, average of (home points-for + away points-against)/2 and (away points-for + home points-against)/2 over each team's last 20 prior games, shifted by the mean error in prior seasons. Pushes excluded.

**Spread, injury_elo baseline**: n=2587 (pushes excluded 46); home covers 49.5% [47.7, 51.3]; always-favourite 49.9% [48.0, 51.8]; model accuracy 49.9% [47.9, 51.8]; market-price lean accuracy 50.1% [47.8, 52.4].

| predicted \ actual | home covered | away covered |
|---|---|---|
| predict HOME covers | 731 | 747 |
| predict AWAY covers | 549 | 560 |

MAE vs actual margin: market spread 10.64 pts, baseline 10.92 pts.

**Spread, mov_elo baseline**: n=2587 (pushes excluded 46); home covers 49.5% [47.7, 51.3]; always-favourite 49.9% [48.0, 51.8]; model accuracy 48.8% [46.9, 50.7]; market-price lean accuracy 50.1% [47.8, 52.4].

| predicted \ actual | home covered | away covered |
|---|---|---|
| predict HOME covers | 692 | 737 |
| predict AWAY covers | 588 | 570 |

MAE vs actual margin: market spread 10.64 pts, baseline 11.10 pts.

**Total**: n=2605 (pushes excluded 28); overs 51.4% [49.5, 53.3]; baseline accuracy 50.2% [48.3, 52.1]; DK price-lean accuracy 50.7% [48.2, 53.2]. MAE vs actual total: market line 14.18, baseline 14.95.

| predicted \ actual | went over | went under |
|---|---|---|
| predict OVER | 710 | 669 |
| predict UNDER | 629 | 597 |

## 3. Quarters and halves

Period scores come from the running scoreboard in `data/pbp/*.parquet` (score at the end of each period; 1H = Q1+Q2, 2H = Q3+Q4, regulation only so overtime is ignored). Reconciliation on 2022-2024 games in the snapshot (3958): pbp final vs box-score final mismatch in 0 games (0.00%); games without a pbp file: 0. Possession-table period points (pts summed by period) differ from the pbp scoreboard in 2 games (0.05%); by period {'Q1': 1, 'Q2': 0, 'Q3': 1, 'Q4': 2}, game ids ['0022200466', '0022200107']. Games excluded from this section (no pbp or pbp final differs from box): 0 (by season {}); ids: []. The possession table's sum of points equals the box final in every snapshot game; the pbp scoreboard is the independent period source.

Baseline: period margin = intercept + slope * logit(Elo p), fit on prior seasons only (2022 for 2023, 2022-23 for 2024), normal error with continuity correction: P(home wins) = P(M > 0.5), P(tie) = P(-0.5 < M <= 0.5). Predicted class = argmax of the three (a tie is almost never the argmax).

### injury_elo-mapped baseline

| target | n | tie rate | home-win rate | 3-class acc | decisive-only acc | full-game acc, same games | full minus period (decisive) | slope vs game 23/24 | sigma 23/24 |
|---|---|---|---|---|---|---|---|---|---|
| Q1 | 2633 | 4.9% [4.1, 5.7] | 51.1% [49.2, 52.9] | 55.6% [53.7, 57.5] | 58.5% [56.4, 60.3] | 67.3% [65.4, 69.0] | +9.0 pts [+6.6, +11.3] | 0.31/0.30 | 8.4/8.4 |
| Q2 | 2633 | 5.1% [4.3, 6.0] | 49.4% [47.5, 51.3] | 55.3% [53.3, 57.2] | 58.2% [56.4, 60.3] | 67.3% [65.4, 69.0] | +8.9 pts [+6.7, +11.2] | 0.27/0.29 | 8.0/8.2 |
| Q3 | 2633 | 4.7% [3.9, 5.5] | 51.3% [49.3, 53.2] | 56.1% [54.2, 58.0] | 58.9% [56.9, 60.8] | 67.3% [65.4, 69.0] | +8.4 pts [+6.3, +10.7] | 0.25/0.29 | 8.2/8.3 |
| Q4 | 2633 | 5.6% [4.7, 6.5] | 47.2% [45.3, 49.1] | 51.5% [49.6, 53.4] | 54.6% [52.6, 56.5] | 67.3% [65.4, 69.0] | +12.6 pts [+10.2, +15.0] | 0.17/0.12 | 7.6/7.7 |
| H1 | 2633 | 3.5% [2.7, 4.1] | 52.4% [50.5, 54.3] | 59.7% [57.9, 61.6] | 61.8% [59.9, 63.7] | 67.3% [65.4, 69.0] | +5.6 pts [+3.5, +7.6] | 0.58/0.59 | 10.9/11.1 |
| H2 | 2633 | 3.9% [3.1, 4.6] | 51.3% [49.3, 53.1] | 57.2% [55.3, 58.9] | 59.5% [57.6, 61.3] | 67.3% [65.4, 69.0] | +8.0 pts [+6.0, +10.0] | 0.41/0.41 | 10.6/10.8 |

### mov_elo-mapped baseline

| target | n | tie rate | home-win rate | 3-class acc | decisive-only acc | full-game acc, same games | full minus period (decisive) | slope vs game 23/24 | sigma 23/24 |
|---|---|---|---|---|---|---|---|---|---|
| Q1 | 2633 | 4.9% [4.1, 5.7] | 51.1% [49.2, 52.9] | 55.2% [53.3, 57.1] | 58.1% [56.2, 60.0] | 65.2% [63.4, 67.0] | +7.3 pts [+5.0, +9.5] | 0.32/0.31 | 8.4/8.4 |
| Q2 | 2633 | 5.1% [4.3, 6.0] | 49.4% [47.5, 51.3] | 54.3% [52.4, 56.3] | 57.3% [55.4, 59.2] | 65.2% [63.4, 67.0] | +7.9 pts [+5.6, +10.2] | 0.28/0.30 | 8.0/8.2 |
| Q3 | 2633 | 4.7% [3.9, 5.5] | 51.3% [49.3, 53.2] | 56.4% [54.5, 58.4] | 59.2% [57.2, 61.1] | 65.2% [63.4, 67.0] | +6.2 pts [+3.9, +8.4] | 0.20/0.29 | 8.3/8.3 |
| Q4 | 2633 | 5.6% [4.7, 6.5] | 47.2% [45.3, 49.1] | 51.0% [49.1, 53.0] | 54.0% [52.0, 56.0] | 65.2% [63.4, 67.0] | +11.3 pts [+8.9, +13.7] | 0.19/0.11 | 7.6/7.7 |
| H1 | 2633 | 3.5% [2.7, 4.1] | 52.4% [50.5, 54.3] | 58.9% [57.1, 60.8] | 61.1% [59.1, 62.9] | 65.2% [63.4, 67.0] | +4.2 pts [+2.1, +6.3] | 0.60/0.60 | 11.0/11.2 |
| H2 | 2633 | 3.9% [3.1, 4.6] | 51.3% [49.3, 53.1] | 56.8% [54.7, 58.6] | 59.1% [57.2, 60.9] | 65.2% [63.4, 67.0] | +6.3 pts [+4.3, +8.5] | 0.39/0.39 | 10.7/10.8 |

3-class confusion matrices for the production-mapped (injury-Elo) baseline, pooled 2023+2024. Rows = predicted [H, Tie, A], columns = actual [H, Tie, A]:

**Q1**

| predicted \ actual | H | Tie | A |
|---|---|---|---|
| H | 1006 | 80 | 701 |
| Tie | 0 | 0 | 0 |
| A | 339 | 49 | 458 |

**Q2**

| predicted \ actual | H | Tie | A |
|---|---|---|---|
| H | 901 | 92 | 643 |
| Tie | 0 | 0 | 0 |
| A | 400 | 43 | 554 |

**H1**

| predicted \ actual | H | Tie | A |
|---|---|---|---|
| H | 1038 | 50 | 630 |
| Tie | 0 | 0 | 0 |
| A | 341 | 41 | 533 |

Period totals, baseline-implied vs outcome only (no market): MAE of (game total baseline x prior-season period share) against MAE of the prior-season mean:

| period | n | MAE share x total_hat | MAE constant |
|---|---|---|---|
| Q1 | 2638 | 6.63 | 6.84 |
| Q2 | 2638 | 6.70 | 6.87 |
| H1 | 2638 | 9.83 | 10.29 |

## Caveats

- Descriptive. Accuracy on decided legs is not profit; payouts, fees and the 52.4% break-even are not modelled here.
- Prop forecasts are the stored walk-forward set, so they are as-of the T-60 production inputs; DNPs have no model row and are not in n.
- The recency prop baseline and the spread/total/period baselines are built for this exploration, not frozen models; they are transparent but untuned.
- Spread/period fits use whole prior seasons (no within-season refit), so 2023's baselines rest on a single season of fit data.
- Quarter rows exclude games where the pbp final disagrees with the box score; the list is above.
