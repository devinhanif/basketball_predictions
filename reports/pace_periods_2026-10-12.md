# Pace in props, period markets vs our period forecasts, and the PACE_TOTALS gates (2026-10-12)

**DESCRIPTIVE, no claim, no production change, no ledger row.** Seasons 2023 (2023-24) and 2024 (2024-25) only; 2022 is warm-up history; season 2025 never loaded.
`nba.duckdb` and `odds_history.duckdb` opened read-only with `season <= 2024`. Every CI is a 2,000-resample bootstrap clustered by game (seed 20261012) unless stated.
Code: `research/eval/pace_periods.py` (A, entry `pace`) and `research/eval/pace_periods_b.py` (B and C); tests `tests/eval/test_pace_periods.py`; numbers in `reports/pace_periods_2026-10-12.results.json`.

## Summary for Devin

- **A. The props model does not under-use pace.** Residuals (actual minus forecast mean) of the production context-residual model are flat across expected-pace deciles and uncorrelated with pace after the model's own r20 points-for/against (team and opponent) and its forecast level: partial slope per 1 sd of expected pace (1.8 possessions per 48) is +0.060 points [-0.017, +0.138] for points, -0.021 [-0.055, +0.010] rebounds, +0.011 [-0.010, +0.036] assists, -0.010 [-0.027, +0.006] threes; partial r between +0.004 and +0.007 in absolute value (R^2 about 0.00003). Pace is mostly already in r20 points (corr 0.57), and expected pace barely varies game to game, so there is little left to use. The one decile gap that clears zero (rebounds, top minus bottom -0.104 [-0.196, -0.009]) is one of 12 looks, wrong-signed for "under-uses pace", and not present in 2024 (by-season slope -0.067 in 2023, +0.009 in 2024). No gain is available from adding it.
- **B. Period markets are efficient and we are slightly behind them.** Our Elo-mapped period forecast is 0.8-1.8 points less accurate than the de-vigged market at picking the winner of a decisive period in five of six periods (Q4 is +0.8 better, inside noise) and worse in log loss in all six (significantly in Q1, Q3, 1H; ties elsewhere), the market is well calibrated, and our naive period totals have a larger error than the line in every period (MAE +0.17 to +0.25 per quarter, +0.63 per half). Q1 player points (2024, 2 books): our forecast ties the line on MAE and loses on log loss by 0.014 [0.009, 0.020].
- **C. PACE_TOTALS gates pass on coverage** (100% of games have as-of pace and a market total) **but the binding fact is information:** the full-game line already has the pace (corr 0.78) and the 2023 total-minus-line has partial corr 0.025 [-0.029, +0.079] with expected pace. Draft rule: `docs/prereg/PACE_TOTALS.md`.

## A. Pace diagnostic

Model = the production context-residual `full` arm, walk-forward by month blocks (2022 warm-up), cached OOF integer quantiles of `feature_significance` (n = 55,320 played rows, 2,638 games per stat; coverage of as-of pace 100%). Residual = y minus the mean of the 199 integer-grid quantiles.
Expected game pace = mean of the two teams' rolling-20 (min 5 games, shift 1) possessions per 48, from the `possessions` table, OT-adjusted (pace mean 98.6, sd 1.78 across rows; the actual game pace sd is 4.7, corr with expected 0.53). Controls in the partial regression: the player's team r20 points-for and -against, the opponent's r20 points-for and -against (the model's own `r20_*` and `opp_r20_*`, rebuilt from `games` exactly as `team_game_context` does), and the forecast mean. The slope is points per 1 sd of expected pace; CI is a 300-resample game-cluster bootstrap of the whole regression.

| stat | n rows / games | corr(pace, r20 pf+pa) | raw slope per sd | partial slope per sd [95% CI] | partial r [95% CI] | partial R^2 | top minus bottom decile residual [95% CI] | slope 2023 / 2024 |
|---|---|---|---|---|---|---|---|---|
| pts | 55320 / 2638 | 0.57 | +0.020 | +0.060 [-0.017, +0.138] | +0.0065 [-0.0018, +0.0151] | 0.00004 | +0.016 [-0.217, +0.232] | +0.102 / +0.017 |
| reb | 55320 / 2638 | 0.57 | -0.017 | -0.021 [-0.055, +0.010] | -0.0055 [-0.0145, +0.0027] | 0.00003 | -0.104 [-0.196, -0.009] | -0.067 / +0.009 |
| ast | 55320 / 2638 | 0.57 | +0.017 | +0.011 [-0.010, +0.036] | +0.0041 [-0.0035, +0.0129] | 0.00002 | +0.036 [-0.032, +0.101] | +0.013 / +0.012 |
| fg3m | 55320 / 2638 | 0.57 | +0.005 | -0.010 [-0.027, +0.006] | -0.0051 [-0.0145, +0.0030] | 0.00003 | +0.007 [-0.041, +0.055] | +0.008 / -0.025 |

As a share of the forecast mean (rows with forecast mean >= 1): percent of mean per sd of pace: pts +0.69%, reb -0.54%, ast +0.68%, fg3m -1.20%. A proportional pace effect would be about +1.8% of the mean per sd; none of the four is near it, and the points interval upper bound (0.138 points, about 1.0% of a 14-point mean) excludes it.

### Residual and CRPS by expected-pace decile (integer-grid CRPS, lower is better; mean residual = actual minus forecast mean; 95% CI clustered by game)

**pts** (overall mean residual -0.129 -0.176..-0.080)

| decile | pace from | n | mean residual [CI] | CRPS [CI] | residual / forecast mean |
|---|---|---|---|---|---|
| 1 | 92.3 | 5514 | -0.192 [-0.352, -0.030] | 3.125 [3.045, 3.207] | -1.80% |
| 2 | 96.3 | 5540 | -0.111 [-0.254, +0.032] | 3.168 [3.099, 3.242] | -1.05% |
| 3 | 97.1 | 5531 | -0.070 [-0.215, +0.080] | 3.136 [3.059, 3.213] | -0.65% |
| 4 | 97.7 | 5533 | -0.219 [-0.362, -0.071] | 3.166 [3.090, 3.247] | -2.01% |
| 5 | 98.2 | 5530 | -0.173 [-0.301, -0.038] | 3.137 [3.065, 3.211] | -1.59% |
| 6 | 98.7 | 5537 | -0.158 [-0.301, -0.016] | 3.132 [3.052, 3.209] | -1.45% |
| 7 | 99.1 | 5535 | -0.049 [-0.193, +0.093] | 3.188 [3.119, 3.253] | -0.45% |
| 8 | 99.5 | 5523 | -0.076 [-0.214, +0.062] | 3.229 [3.154, 3.308] | -0.69% |
| 9 | 100.0 | 5519 | -0.070 [-0.230, +0.090] | 3.218 [3.137, 3.300] | -0.63% |
| 10 | 100.8 | 5558 | -0.176 [-0.320, -0.029] | 3.203 [3.131, 3.277] | -1.56% |

**reb** (overall mean residual -0.047 -0.068..-0.027)

| decile | pace from | n | mean residual [CI] | CRPS [CI] | residual / forecast mean |
|---|---|---|---|---|---|
| 1 | 92.3 | 5514 | -0.021 [-0.088, +0.046] | 1.288 [1.258, 1.320] | -0.50% |
| 2 | 96.3 | 5540 | -0.086 [-0.150, -0.022] | 1.279 [1.248, 1.312] | -2.08% |
| 3 | 97.1 | 5531 | -0.003 [-0.067, +0.064] | 1.306 [1.277, 1.337] | -0.08% |
| 4 | 97.7 | 5533 | -0.005 [-0.072, +0.063] | 1.317 [1.287, 1.345] | -0.12% |
| 5 | 98.2 | 5530 | -0.029 [-0.095, +0.044] | 1.311 [1.279, 1.346] | -0.68% |
| 6 | 98.7 | 5537 | -0.084 [-0.150, -0.024] | 1.285 [1.255, 1.319] | -2.03% |
| 7 | 99.1 | 5535 | -0.022 [-0.086, +0.044] | 1.331 [1.298, 1.364] | -0.53% |
| 8 | 99.5 | 5523 | -0.058 [-0.121, +0.009] | 1.296 [1.266, 1.328] | -1.39% |
| 9 | 100.0 | 5519 | -0.038 [-0.103, +0.027] | 1.324 [1.293, 1.356] | -0.91% |
| 10 | 100.8 | 5558 | -0.125 [-0.193, -0.059] | 1.299 [1.272, 1.326] | -2.92% |

**ast** (overall mean residual -0.057 -0.073..-0.042)

| decile | pace from | n | mean residual [CI] | CRPS [CI] | residual / forecast mean |
|---|---|---|---|---|---|
| 1 | 92.3 | 5514 | -0.076 [-0.121, -0.029] | 0.881 [0.857, 0.906] | -3.07% |
| 2 | 96.3 | 5540 | -0.082 [-0.123, -0.040] | 0.868 [0.846, 0.889] | -3.34% |
| 3 | 97.1 | 5531 | -0.036 [-0.082, +0.013] | 0.896 [0.873, 0.920] | -1.46% |
| 4 | 97.7 | 5533 | -0.086 [-0.134, -0.038] | 0.896 [0.875, 0.917] | -3.37% |
| 5 | 98.2 | 5530 | -0.110 [-0.153, -0.063] | 0.904 [0.883, 0.927] | -4.24% |
| 6 | 98.7 | 5537 | -0.032 [-0.077, +0.016] | 0.916 [0.895, 0.938] | -1.25% |
| 7 | 99.1 | 5535 | -0.014 [-0.064, +0.036] | 0.920 [0.897, 0.943] | -0.54% |
| 8 | 99.5 | 5523 | -0.060 [-0.103, -0.013] | 0.896 [0.873, 0.919] | -2.33% |
| 9 | 100.0 | 5519 | -0.033 [-0.078, +0.016] | 0.920 [0.900, 0.941] | -1.26% |
| 10 | 100.8 | 5558 | -0.040 [-0.088, +0.007] | 0.933 [0.910, 0.957] | -1.49% |

**fg3m** (overall mean residual -0.008 -0.018..+0.003)

| decile | pace from | n | mean residual [CI] | CRPS [CI] | residual / forecast mean |
|---|---|---|---|---|---|
| 1 | 92.3 | 5514 | -0.029 [-0.061, +0.005] | 0.584 [0.567, 0.601] | -2.27% |
| 2 | 96.3 | 5540 | -0.028 [-0.061, +0.006] | 0.573 [0.557, 0.589] | -2.28% |
| 3 | 97.1 | 5531 | +0.009 [-0.023, +0.042] | 0.568 [0.552, 0.586] | +0.71% |
| 4 | 97.7 | 5533 | -0.019 [-0.051, +0.015] | 0.573 [0.556, 0.590] | -1.49% |
| 5 | 98.2 | 5530 | -0.019 [-0.050, +0.012] | 0.580 [0.563, 0.598] | -1.50% |
| 6 | 98.7 | 5537 | +0.006 [-0.025, +0.037] | 0.582 [0.565, 0.600] | +0.47% |
| 7 | 99.1 | 5535 | +0.034 [+0.002, +0.065] | 0.593 [0.577, 0.610] | +2.67% |
| 8 | 99.5 | 5523 | +0.001 [-0.030, +0.033] | 0.584 [0.568, 0.601] | +0.06% |
| 9 | 100.0 | 5519 | -0.009 [-0.044, +0.025] | 0.583 [0.566, 0.601] | -0.73% |
| 10 | 100.8 | 5558 | -0.022 [-0.054, +0.011] | 0.585 [0.568, 0.602] | -1.73% |

CRPS rises mildly with pace for points and assists (fast games have bigger numbers, so bigger scale), which is spread, not bias: residuals stay near the overall mean in every decile. The overall level is slightly negative (points -0.13); it is about the same in every decile, so it is a level, not a pace effect (cause not investigated).

**Plain verdict:** the model does not under-use pace. Whatever pace information exists in recent possessions is already in r20 points-for/against and the Elo margin; a separate pace feature has nothing to explain. (Not tested: pace x player interactions, e.g. high-usage players in fast games; a single global slope would miss a mixture of signs. The deciles show no sign of one.)

## B. Period markets vs our period forecasts

**Market.** the-odds-api t60 snapshots (about tip minus 64 min), 2023-24 and 2024-25, 10-21 books per game. Each book's two prices are de-vigged proportionally; Pinnacle where it quotes the game and market, else the mean over books. Pinnacle quotes q1, q2, h1 (about 2,531 of 2,638 games) and **not** q3, q4, h2: those three are consensus only. Two-way period moneylines void ties (assumed from the market structure; the vendor rules were not verifiable here), so winners are scored on decisive periods only, with ties counted per row.
**Ours.** The confusion report's Elo-mapped forecast: OLS of period margin on logit(injury-Elo P(home)), fit on seasons before the test season (2023 fits on 2022; 2024 on 2022-23), converted to a Normal P(home), P(away) and conditioned on a decisive period. Period scores come from play-by-play (reconciled to the box score for 100% of the games).

### B1. Period winners (decisive periods; accuracy and log loss, higher accuracy / lower log loss is better)

| target | n scored (ties voided) | Pinnacle / consensus | accuracy ours [CI] | accuracy market [CI] | ours minus market | log loss ours | log loss market | ours minus market [CI] | market calibration intercept / slope | ours intercept / slope |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | 2504 (129) | 2405 / 99 | 58.5% [56.6, 60.3] | 59.2% [57.3, 61.1] | -0.8 [-2.1, +0.6] pts | 0.6667 | 0.6621 | +0.0046 [+0.0005, +0.0082] | +0.00 / 0.97 | -0.05 / 1.06 |
| Q2 | 2498 (135) | 2396 / 102 | 58.2% [56.3, 60.2] | 59.1% [57.1, 61.1] | -0.8 [-2.2, +0.4] pts | 0.6690 | 0.6670 | +0.0020 [-0.0010, +0.0051] | -0.03 / 1.12 | -0.04 / 1.11 |
| Q3 | 2510 (123) | 0 / 2510 | 58.9% [57.0, 60.8] | 59.9% [58.0, 61.8] | -1.0 [-2.2, +0.2] pts | 0.6724 | 0.6664 | +0.0060 [+0.0028, +0.0091] | +0.04 / 1.07 | +0.02 / 1.01 |
| Q4 | 2486 (147) | 0 / 2486 | 54.6% [52.7, 56.5] | 53.8% [51.9, 55.7] | +0.8 [-0.5, +2.2] pts | 0.6877 | 0.6875 | +0.0002 [-0.0019, +0.0025] | -0.05 / 0.86 | -0.08 / 0.99 |
| H1 | 2542 (91) | 2439 / 103 | 61.8% [59.9, 63.6] | 63.6% [61.7, 65.4] | -1.8 [-3.1, -0.6] pts | 0.6433 | 0.6360 | +0.0072 [+0.0023, +0.0120] | -0.02 / 1.02 | -0.06 / 1.05 |
| H2 | 2529 (102) | 0 / 2529 | 59.5% [57.6, 61.5] | 60.3% [58.4, 62.2] | -0.8 [-2.1, +0.4] pts | 0.6664 | 0.6630 | +0.0034 [-0.0001, +0.0072] | +0.01 / 0.96 | -0.02 / 1.02 |
| full game (ref.) | 2633 | consensus-first | 67.3% [65.5, 69.2] | 68.6% [66.8, 70.4] | | 0.6017 | 0.5858 | +0.0160 [+0.0093, +0.0226] | | |

Calibration intercept / slope = logistic fit of the home-win outcome on logit(probability); perfect is 0 / 1.00. Slopes are near 1 for both the market and ours (slope CIs were not computed).
Reading: the market is no worse calibrated than ours, and is ahead in log loss for every period; the gap clears zero for Q1, Q3 and 1H (the periods where the information is mostly lineups and rest, which the market has at T-60 and the Elo map does not), and Q2, Q4, 2H are indistinguishable. Accuracy differences are all inside their CIs individually (1H is the exception, -1.8 pts). Q4 is close to a coin flip for both (54-55%).

Market calibration by bin (Pinnacle where present, else consensus; P(home wins the decisive period) vs realised, game-clustered CI in the JSON):

- Q1: 0.0-0.3 n=53 stated 0.27 realised 0.25; 0.3-0.4 n=304 stated 0.36 realised 0.36; 0.4-0.5 n=573 stated 0.45 realised 0.49; 0.5-0.6 n=759 stated 0.55 realised 0.52; 0.6-0.7 n=562 stated 0.64 realised 0.64; 0.7-1.0 n=253 stated 0.74 realised 0.75
- Q2: 0.0-0.3 n=26 stated 0.28 realised 0.31; 0.3-0.4 n=266 stated 0.36 realised 0.31; 0.4-0.5 n=657 stated 0.45 realised 0.45; 0.5-0.6 n=944 stated 0.55 realised 0.54; 0.6-0.7 n=516 stated 0.64 realised 0.66; 0.7-1.0 n=89 stated 0.72 realised 0.72
- Q3: 0.0-0.3 n=28 stated 0.29 realised 0.39; 0.3-0.4 n=285 stated 0.36 realised 0.38; 0.4-0.5 n=658 stated 0.45 realised 0.44; 0.5-0.6 n=885 stated 0.55 realised 0.56; 0.6-0.7 n=570 stated 0.64 realised 0.66; 0.7-1.0 n=84 stated 0.72 realised 0.80
- Q4: 0.3-0.4 n=115 stated 0.38 realised 0.42; 0.4-0.5 n=867 stated 0.46 realised 0.46; 0.5-0.6 n=1246 stated 0.55 realised 0.51; 0.6-0.7 n=258 stated 0.62 realised 0.63
- H1: 0.0-0.3 n=194 stated 0.25 realised 0.23; 0.3-0.4 n=332 stated 0.36 realised 0.36; 0.4-0.5 n=420 stated 0.45 realised 0.45; 0.5-0.6 n=542 stated 0.56 realised 0.53; 0.6-0.7 n=581 stated 0.65 realised 0.63; 0.7-1.0 n=473 stated 0.76 realised 0.78
- H2: 0.0-0.3 n=75 stated 0.28 realised 0.32; 0.3-0.4 n=330 stated 0.36 realised 0.34; 0.4-0.5 n=573 stated 0.45 realised 0.46; 0.5-0.6 n=727 stated 0.55 realised 0.56; 0.6-0.7 n=615 stated 0.64 realised 0.63; 0.7-1.0 n=209 stated 0.73 realised 0.76

### B2. Period totals: our naive total (team recent scoring x period share) vs the market total

Ours = the confusion report's `total_hat` (rolling 20-game team points for/against, needs 8 prior games) times the period's share of the game total (fit on earlier seasons: 2023 on 2022, 2024 on 2022-23). Market = Pinnacle line where quoted (q1, q2, h1), else median line over books. Over/under accuracy excludes pushes; "market lean" is the de-vigged over price at its line where it is not exactly 0.5.

| period | n (pushes) | mean actual / line / ours | MAE ours [CI] | MAE market line [CI] | ours minus market [CI] | O/U accuracy ours [CI] | O/U market lean [CI] (n) | always under | bias line minus actual [CI] |
|---|---|---|---|---|---|---|---|---|---|
| q1 | 2638 (50) | 56.8 / 56.9 / 57.3 | 6.63 [6.43, 6.82] | 6.40 [6.21, 6.59] | +0.23 [+0.16, +0.30] | 48.9% [47.1, 50.8] | 50.5% [48.5, 52.7] (2294) | 52.4% | 0.08 [-0.23, 0.39] |
| q2 | 2638 (58) | 56.7 / 56.6 / 57.0 | 6.70 [6.51, 6.90] | 6.53 [6.35, 6.73] | +0.17 [+0.10, +0.24] | 49.9% [48.0, 51.9] | 53.0% [50.9, 54.9] (2347) | 50.3% | -0.11 [-0.42, 0.20] |
| q3 | 2638 (10) | 57.5 / 57.1 / 57.2 | 6.65 [6.47, 6.85] | 6.40 [6.22, 6.59] | +0.25 [+0.16, +0.33] | 49.2% [47.2, 51.1] | 51.5% [49.6, 53.6] (2385) | 48.4% | -0.36 [-0.64, -0.06] |
| q4 | 2638 (9) | 55.0 / 55.2 / 54.3 | 7.03 [6.82, 7.24] | 6.84 [6.64, 7.05] | +0.18 [+0.10, +0.26] | 50.1% [48.2, 52.1] | 51.4% [49.4, 53.5] (2362) | 51.2% | 0.19 [-0.14, 0.50] |
| h1 | 2638 (42) | 113.5 / 113.5 / 114.3 | 9.83 [9.55, 10.10] | 9.20 [8.93, 9.45] | +0.63 [+0.49, +0.78] | 48.3% [46.4, 50.3] | 51.4% [49.4, 53.4] (2260) | 50.2% | 0.03 [-0.42, 0.48] |
| h2 | 2638 (8) | 112.5 / 112.5 / 111.5 | 10.15 [9.85, 10.44] | 9.52 [9.25, 9.79] | +0.63 [+0.49, +0.77] | 48.5% [46.6, 50.4] | 51.0% [48.9, 53.1] (2241) | 50.0% | -0.03 [-0.48, 0.38] |
| full | 2633 (20) | 227.1 / 226.5 / 227.3 | 14.93 [14.54, 15.36] | 14.19 [13.81, 14.59] | +0.75 [+0.54, +0.94] | 49.8% [48.0, 51.7] | 49.2% [47.0, 51.5] (2098) | 48.6% | -0.62 [-1.32, 0.04] |

Reading: the line beats the naive total on error in every period, by a margin that clears zero (about 3-4% for a quarter, 7% for a half, 5% for the game). Over/under accuracy is a coin flip for everyone (the lines are set to be 50/50); no rule here beats 52.4%, the break-even at -110. The market's lean is 0-3 points above 50% and the CIs include 50%. Line bias vs actual is inside noise except Q3 (-0.36 [-0.64, -0.06], not corrected for 7 looks).

### B3. Q1 player points, 2024-25 (descriptive; williamhill_us and fanduel only, one-to-two-book prices, not a consensus)

Ours = `research/eval/half_points.py` method (b), the direct Q1 model (integer quantiles; P(over) = share of the 199 quantiles at or above the next integer). Legs: quotes with a forecast row.

- Legs 7305 (all half-point lines, no pushes) over 902 games, 324 players; books {'fanduel': 3367, 'williamhill_us': 3938}. Mean line 4.19, mean actual Q1 points 4.45, mean model median 3.64.
- MAE: model median 2.526 [2.472, 2.579] vs line 2.521 [2.473, 2.571] (tie).
- Over rate 48.8% [47.7, 49.9]; model mean P(over) 0.437, market mean P(over) 0.498: our Q1 forecast leans UNDER (median below the quoted line) while the market sits at 0.50 and over hit 48.8%.
- Accuracy: model 52.5% [51.3, 53.6]; market lean 54.1% [52.9, 55.3] (n 6547); always-under 51.2%.
- Log loss: model 0.7034 [0.6978, 0.7088] vs market 0.6889 [0.6868, 0.6910]; model minus market +0.0144 [+0.0092, +0.0197]. The market is better; the model is not calibrated to the line (too many unders).

**Caveats (B).** The market prices are the-odds-api snapshots, a book-by-book mixture whose `last_update` can precede the snapshot; per-quote known-at before tip was not re-verified here (documented capture about tip minus 64 min). Tie voiding is assumed. Q1 player props are one or two books, and williamhill_us/fanduel quote only players they list (selection toward regulars). Our period forecast is the simple Elo map, not a period-specific model that sees lineups; this is the baseline the market beats, not a verdict on what a lineup-aware period model could do.

## C. PACE_TOTALS: gates (measured, nothing fit)

| gate | 2023 | 2024 |
|---|---|---|
| games | 1318 | 1320 |
| games with possessions | 1318 | 1320 |
| median possessions per game | 197 | 198 |
| both teams as-of rolling-20 pace | 1318 | 1320 |
| both teams as-of rolling-20 points for/against | 1318 | 1320 |
| full-game market total (any book) | 1318 | 1315 |
| full-game total quoted by Pinnacle | 1267 | 1279 |
| median books on the full-game total | 20 | 21 |
| pbp period scores reconcile with box | 1318 | 1320 |
| period totals Q1 (same count for q2, q3, q4, h1, h2) | 1318 | 1320 |

Information gate (2023 select season only; 1,318 games; no 2024 outcome used): sd of the total 20.5; sd of total minus line 17.6; corr(line, expected pace) 0.78; corr(total minus line, expected pace) +0.003; partial corr given the line and both teams' r20 points +0.025 (Fisher 95% [-0.029, +0.079]). The line contains the pace. The rule is therefore built so that only the blend arm can pass.

The 5-line rule (full text with guards in `docs/prereg/PACE_TOTALS.md`, DRAFT):
1. P = (mean of both teams' as-of rolling-20 possessions per 48) x (each team's as-of points per 100, own offense plus opponent defense, home term) -> OLS to a total, Normal on the integer grid; M = Normal at the market line; B = line + w (P - line), w by month walk-forward.
2. Select 2023, report 2024, never 2025. Primary (BH over 3, q <= 0.05): B minus M integer CRPS <= -0.05 points, log loss at the line <= -0.002, and the decision score at the actual DraftKings price (EV >= 5%, ROI >= +1.0% with CI lower > 0, n >= 300, and above the market-lean comparator); all CIs game-clustered, B = 2000.
3. Scored against the full-game total; period totals q1..q4, h1, h2 secondary (BH over 6), never pass-carrying; select-season signs must agree.
4. Guards: planted-future (max difference 0), past-only placebo of the pace/efficiency histories, non-inert shuffle (shuffled P must worsen by >= 0.5 CRPS points), missingness audit, same rows and support for every arm.
5. Verdict `passes` or `the_line_already_has_the_pace` / defer to the market; my prior of a pass is about 5%.

## Not verified / limits
Per-quote known-at before tip for period prices; tie handling of two-way period moneylines; the possessions table was being rewritten by the archivist when the confusion snapshot was taken (here read live, 100% of 2023-24 games have rows, median 197-198 per game); the expected-pace measure is the mean of the two teams' rolling pace (a pace-above-average sum, `pace_A + pace_B - league`, gives the same ordering up to the league constant); the residual-pace regression is linear in pace only.

## Proposed rows (drafted, not appended)
- Ledger (descriptive, no claim): "Pace diagnostic: production props residuals vs as-of expected game pace, 2023-24 OOF, n=55,320 per stat, partial slope per sd +0.060 [-0.017, +0.138] pts; null, model does not under-use pace." and "Period markets 2023-24: de-vigged market ahead of the Elo-mapped period forecast in log loss in all 6 periods (clears zero for Q1, Q3, 1H); naive period totals lose to the line in every period; Q1 player points ties the line on MAE, worse log loss +0.014 [0.009, 0.020] (n=7,305 legs, 902 games)."
- DECISIONS: "PACE_TOTALS drafted, not frozen; only the blend arm can pass because the line already carries pace (corr 0.78). Reverse if Devin wants P-alone as a primary."
