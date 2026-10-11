# Model-miss questions Q6-Q10 (descriptive, 2026-10-10)

DRAFT, descriptive only: no rule, no claim, no fit. Seasons 2023 and 2024 only (season 2025 never loaded; asserted). Residual = actual - OOF integer mean (positive = we were low). CIs are 95% percentile bootstraps clustered by game_id (1000 resamples, seed 2026); rows from the same game are not independent. 'Diff vs rest' is the level mean minus the mean of all other rows in that table's universe. Script: data/scratch/model_miss_questions_2026-10-10.py. No multiplicity correction; with this many cells, expect a few spurious 'moves'.

Common definitions. Prior-10 = mean over the player's previous 10 played games (minutes>0), at least 5, crossing season boundaries (2022 lookback loaded). OUT at T-60 = latest official-report snapshot with as_of <= real tip - 60 min (nba/features/injury_report.py: latest_pretip_flagged, tip_source=real), rotation = prior mean minutes >= 20 (n>=5) via rotation_flagged_by_team, the production rule. Note the production model's stored n_out_rot uses a much looser rotation (long-run 40-game minutes >= 12, ROTATION_MIN40) and so agrees with my prior-10 >= 20 count on only 51.1% of rows; the main splits use the stricter >= 20 rotation (the module default), and a production-definition (>= 12) robustness table is given for Q6(a) and Q8. Games without a usable report are excluded from OUT splits (never treated as 'nobody out').

## Q6 Stars (prior-10 mean pts >= 20)

Universe: 7022 star pts rows (127 players); overall mean residual 0.066 pts (positive = we were low). Opponent allowed-points terciles cut at 110.9 / 116.4 pts per game over the opponent's prior 10 team games.

### (a) rotation (prior-10 >= 20 min) teammates OUT at T-60 (reports present only)

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 2457 | 0.030 | -0.302 | 0.331 | -0.055 | -0.415 | 0.312 | 0.192 | 1345 | -0.165 | 1112 |
| 1 | 2189 | 0.191 | -0.164 | 0.517 | 0.182 | -0.236 | 0.590 | -0.070 | 1202 | 0.509 | 987 |
| 2+ | 2367 | -0.014 | -0.358 | 0.324 | -0.120 | -0.518 | 0.281 | -0.247 | 1123 | 0.197 | 1244 |

### (a2) production definition of rotation (long-run minutes >= 12), reports present

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1345 | 0.043 | -0.367 | 0.452 | -0.027 | -0.447 | 0.462 | 0.137 | 624 | -0.038 | 721 |
| 1 | 1949 | 0.311 | -0.045 | 0.650 | 0.340 | -0.086 | 0.747 | 0.293 | 1108 | 0.335 | 841 |
| 2+ | 3719 | -0.055 | -0.310 | 0.200 | -0.257 | -0.639 | 0.102 | -0.265 | 1938 | 0.173 | 1781 |

### (b) rotation teammate RETURNING (first game back after >=3 team games absent, played tonight)

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| teammate returning | 685 | -1.168 | -1.767 | -0.598 | -1.368 | -1.998 | -0.702 | -1.060 | 316 | -1.260 | 369 |
| no | 6337 | 0.200 | -0.011 | 0.431 | 1.368 | 0.710 | 1.970 | 0.072 | 3359 | 0.344 | 2978 |

### (c) opponent defence (points allowed per game, prior 10)

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 stingy | 2382 | 0.048 | -0.270 | 0.403 | -0.028 | -0.426 | 0.360 | -0.138 | 1207 | 0.238 | 1175 |
| T2 | 2335 | 0.092 | -0.237 | 0.400 | 0.038 | -0.370 | 0.446 | 0.186 | 1154 | -0.001 | 1181 |
| T3 leaky | 2305 | 0.060 | -0.286 | 0.403 | -0.009 | -0.429 | 0.403 | -0.107 | 1314 | 0.283 | 991 |

### (d) rest

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| b2b | 1094 | 0.025 | -0.499 | 0.572 | -0.049 | -0.599 | 0.494 | -0.441 | 552 | 0.500 | 542 |
| 1 day off | 4320 | 0.044 | -0.194 | 0.302 | -0.059 | -0.453 | 0.330 | 0.018 | 2265 | 0.072 | 2055 |
| 2+ days off | 1511 | 0.175 | -0.246 | 0.557 | 0.138 | -0.283 | 0.567 | 0.180 | 807 | 0.169 | 704 |

### (e) home/away

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| home | 3515 | -0.112 | -0.370 | 0.147 | -0.357 | -0.716 | -0.018 | -0.143 | 1832 | -0.078 | 1683 |
| away | 3507 | 0.245 | 0.001 | 0.507 | 0.357 | 0.021 | 0.704 | 0.092 | 1843 | 0.415 | 1664 |

### (f) first 15 team games vs rest

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | n_2023 | mean_2024 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| first15 | 1204 | 0.113 | -0.351 | 0.582 | 0.056 | -0.477 | 0.559 | -0.368 | 632 | 0.644 | 572 |
| later | 5818 | 0.057 | -0.157 | 0.266 | -0.056 | -0.550 | 0.468 | 0.046 | 3043 | 0.069 | 2775 |

Flag: levels with n >= 500 whose mean residual differs from the rest of the table's universe by >= 0.5 pts.

| cond | n | mean_resid | diff_vs_rest | ci |
|---|---|---|---|---|
| (b) teammate returning | 685 | -1.168 | -1.368 | [-2.00,-0.70] |
| (b) no | 6337 | 0.200 | 1.368 | [0.71,1.97] |

Largest absolute diffs, any n >= 200 (for context):

| cond | n | diff_vs_rest | ci |
|---|---|---|---|
| (b) teammate returning | 685 | -1.368 | [-2.00,-0.70] |
| (b) no | 6337 | 1.368 | [0.71,1.97] |
| (e) home | 3515 | -0.357 | [-0.72,-0.02] |
| (e) away | 3507 | 0.357 | [0.02,0.70] |
| (a2 1 | 1949 | 0.340 | [-0.09,0.75] |
| (a2 2+ | 3719 | -0.257 | [-0.64,0.10] |

### Stars by mean residual (n >= 40; 63 stars qualify)

Top 10 (we were lowest on them):

| player_id | name | n | mean_resid | se | avg_prior10_pts |
|---|---|---|---|---|---|
| 1629029 |  | 147 | 1.960 | 0.674 | 30.865 |
| 1630178 |  | 129 | 1.932 | 0.845 | 26.384 |
| 1630560 |  | 72 | 1.885 | 1.013 | 23.815 |
| 1630595 |  | 129 | 1.468 | 0.692 | 24.566 |
| 1630162 |  | 188 | 1.453 | 0.679 | 26.885 |
| 1641705 |  | 83 | 1.417 | 0.871 | 23.718 |
| 1631094 |  | 116 | 1.340 | 0.770 | 24.812 |
| 1626181 |  | 47 | 1.193 | 1.155 | 23.394 |
| 203999 |  | 175 | 1.004 | 0.663 | 27.881 |
| 1630169 |  | 59 | 1.003 | 1.179 | 23.032 |

Bottom 10 (we were highest on them):

| player_id | name | n | mean_resid | se | avg_prior10_pts |
|---|---|---|---|---|---|
| 1627742 |  | 60 | -3.020 | 0.926 | 23.210 |
| 202710 |  | 56 | -2.601 | 1.087 | 21.996 |
| 204001 |  | 50 | -1.986 | 1.045 | 21.600 |
| 1628374 |  | 75 | -1.880 | 0.781 | 23.097 |
| 203081 |  | 136 | -1.871 | 0.713 | 25.153 |
| 1627734 |  | 69 | -1.300 | 0.668 | 21.281 |
| 202696 |  | 40 | -1.048 | 1.056 | 21.098 |
| 1627783 |  | 139 | -1.031 | 0.557 | 22.173 |
| 2544 |  | 151 | -1.012 | 0.574 | 25.278 |
| 1628991 |  | 122 | -0.989 | 0.717 | 22.908 |

Persistence check: among 48 stars with n >= 20 in both seasons, the correlation of their per-season mean residuals is 0.24. Near zero means the top/bottom lists are mostly noise (the extreme of ~63 noisy means is selected). The sd of per-star means is 1.04 vs a mean SE of 0.84.

### Candidate questions for Devin (Q6)

1. (probably yes) Should star pts forecasts be lowered by about 1 pt when a rotation teammate (>= 20 prior min) is returning from >= 3 missed team games? Stars scored 1.17 pts below the model there (n=685, CI of diff vs rest [-2.00,-0.70], -1.06 in 2023 and -1.26 in 2024). Caveat to settle in the rule: 'returning' here is defined by the teammate playing tonight; the pre-tip version (not on the OUT list, absent the last 3 games) needs its own check.
2. (probably no) Does the NUMBER of rotation teammates OUT (0/1/2+) still leave a star residual? Means 0.03 / 0.19 / -0.01 with CIs overlapping zero and the 2023 and 2024 signs disagreeing on 1 and 2+; nothing reaches 0.5 pts.
3. (probably no) Is there a stable per-star bias worth a star-specific offset? Season-to-season correlation of per-star mean residuals is only 0.24 (48 stars); the spread of per-star means (sd 1.04) is only a little above sampling noise (mean SE 0.84), so the top/bottom-10 lists are mostly luck. Home/away (away +0.25 vs home -0.11, diff 0.36 [0.02,0.70], same sign both seasons) is below the 0.5 floor, a maybe rather than a yes.

## Q7 Short nights

Universe: played rows with prior-10 mean minutes >= 24 (n=27072, seasons (2023, 2024)). Short = actual minutes < 0.5 x prior-10 mean. Overall short rate 0.0215 [0.0197,0.0233], n_short=581. Cuts: prior foul rate (pf per 36, prior 10) terciles at 2.17/2.89; Elo team win prob terciles at 0.39/0.61 (injury-Elo p from game_rows, pre-tip).

Note on timing: pf>=5, blowout and early-exit are outcomes (post-game); starter is the box-score flag (nearly known at T-30, not at T-60); foul tercile, b2b, first-15, Elo tercile, returning-from-absence and the pre-tip questionable/doubtful tag are knowable at T-60. Because a player fouling out or a blowout cuts minutes, those rows are causes/consequences, not predictors. The early-exit proxy implies short by construction (minutes <= 12 < 0.5 x 24), so its 100% is a definition, not a finding; what matters is its 40% share of short nights.

### Coincidence and lift per factor

| factor | knowable | n_with | share_of_all_nights | n_short_with | share_of_short_nights | short_rate_with | short_rate_without | rate_diff | d_lo | d_hi | rate_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pf >= 5 | post | 1837 | 0.068 | 7 | 0.012 | 0.004 | 0.023 | -0.019 | -0.022 | -0.015 | 0.168 |
| blowout |margin|>=20 | post | 5559 | 0.205 | 134 | 0.231 | 0.024 | 0.021 | 0.003 | -0.001 | 0.008 | 1.160 |
| back-to-back | pre | 4252 | 0.157 | 91 | 0.157 | 0.021 | 0.021 | -0.000 | -0.005 | 0.005 | 0.997 |
| own prior foul rate T3 (high) | pre | 9015 | 0.333 | 212 | 0.365 | 0.024 | 0.020 | 0.003 | -0.001 | 0.007 | 1.151 |
| own prior foul rate T2 | pre | 9042 | 0.334 | 211 | 0.363 | 0.023 | 0.021 | 0.003 | -0.001 | 0.006 | 1.137 |
| own prior foul rate T1 (low) | pre | 9015 | 0.333 | 158 | 0.272 | 0.018 | 0.023 | -0.006 | -0.009 | -0.003 | 0.748 |
| early exit proxy (min<=12, pf<=2, |margin|<15) | post | 230 | 0.008 | 230 | 0.396 | 1.000 | 0.013 | 0.987 | 0.985 | 0.988 | 76.473 |
| starter (box) | starter | 22170 | 0.819 | 267 | 0.460 | 0.012 | 0.064 | -0.052 | -0.059 | -0.045 | 0.188 |
| first 15 team games | pre | 4671 | 0.173 | 135 | 0.232 | 0.029 | 0.020 | 0.009 | 0.004 | 0.015 | 1.452 |
| returning after >=3 team games absent | pre | 447 | 0.017 | 21 | 0.036 | 0.047 | 0.021 | 0.026 | 0.008 | 0.046 | 2.234 |
| questionable/doubtful on T-60 report | pre | 1307 | 0.048 | 36 | 0.062 | 0.028 | 0.021 | 0.006 | -0.002 | 0.015 | 1.302 |
| Elo favorite T3 (high win prob) | pre | 9018 | 0.333 | 157 | 0.270 | 0.017 | 0.023 | -0.006 | -0.010 | -0.002 | 0.741 |
| Elo underdog T1 (low win prob) | pre | 9018 | 0.333 | 231 | 0.398 | 0.026 | 0.019 | 0.006 | 0.002 | 0.010 | 1.321 |

Short nights with none of pf>=5 / blowout / early-exit proxy: 212 of 581 (36.5%). Short with pf>=5 and not blowout: 5.

### Pre-tip factors ranked by short-rate ratio (knowable before tip only)

| factor | n_with | short_rate_with | short_rate_without | rate_ratio | rate_diff | d_lo | d_hi |
|---|---|---|---|---|---|---|---|
| returning after >=3 team games absent | 447 | 0.047 | 0.021 | 2.234 | 0.026 | 0.008 | 0.046 |
| first 15 team games | 4671 | 0.029 | 0.020 | 1.452 | 0.009 | 0.004 | 0.015 |
| Elo underdog T1 (low win prob) | 9018 | 0.026 | 0.019 | 1.321 | 0.006 | 0.002 | 0.010 |
| questionable/doubtful on T-60 report | 1307 | 0.028 | 0.021 | 1.302 | 0.006 | -0.002 | 0.015 |
| own prior foul rate T3 (high) | 9015 | 0.024 | 0.020 | 1.151 | 0.003 | -0.001 | 0.007 |
| own prior foul rate T2 | 9042 | 0.023 | 0.021 | 1.137 | 0.003 | -0.001 | 0.006 |
| back-to-back | 4252 | 0.021 | 0.021 | 0.997 | -0.000 | -0.005 | 0.005 |
| own prior foul rate T1 (low) | 9015 | 0.018 | 0.023 | 0.748 | -0.006 | -0.009 | -0.003 |
| Elo favorite T3 (high win prob) | 9018 | 0.017 | 0.023 | 0.741 | -0.006 | -0.010 | -0.002 |
| starter (box) | 22170 | 0.012 | 0.064 | 0.188 | -0.052 | -0.059 | -0.045 |

Short rate by prior-10 minutes band:

| band | n | mean | ci_lo | ci_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|
| 24-30 | 11671 | 0.032 | 0.029 | 0.035 | 0.031 | 0.033 |
| 30-34 | 8405 | 0.015 | 0.013 | 0.018 | 0.013 | 0.018 |
| 34+ | 6996 | 0.011 | 0.008 | 0.013 | 0.011 | 0.010 |

Joint logistic (descriptive, one fit, no CI): log-odds of a short night

| factor | coef | odds_ratio |
|---|---|---|
| b2b | 0.017 | 1.017 |
| foul_T3 | -0.073 | 0.930 |
| foul_T1 | -0.169 | 0.845 |
| first15 | 0.330 | 1.391 |
| returning | 0.304 | 1.355 |
| questionable_tag | 0.323 | 1.381 |
| elo_win_prob (per +0.1) | -0.052 | 0.949 |
| m10 (per +5 min) | -0.178 | 0.837 |
| starter(box) | -1.534 | 0.216 |

### Candidate questions for Devin (Q7)

1. (probably no) Can anything known before tip flag short nights well enough to change a forecast? The short rate is 2.15% (n_short=581); the strongest pre-tip factor, returning after >= 3 absent team games, is 4.7% vs 2.1% (ratio 2.2) but covers only 21 short nights (3.6% of them). Back-to-back (ratio 1.00) and high prior foul rate (1.15, CI includes zero) do nothing.
2. (probably no) Do foul-outs explain short nights? Only 7 of 581 short nights (1.2%) have pf >= 5; the explained ones are early exits (40%, likely injury/ejection, unforecastable) and blowouts (23%, a base rate of 20%, so no lift); 36% have none of the three.
3. (probably yes, modest) Should the minutes spread be widened for lower-minute players and underdogs? Short rate is 3.2% for prior 24-30 min vs 1.1% for 34+, and 2.6% for Elo underdogs (T1) vs 1.7% for favorites; first 15 games 2.9% vs 2.0%. These move variance, not the mean.

## Q8 Deep bench

Universe: played rows with prior-10 mean minutes < 12 OR prior-10 mean pts < 5 (n=13559; 13559 of them are in the production pts model rows). Minutes residual = actual minutes - prior-10 mean minutes (there is no stored OOF minutes forecast in these files; this is the recency baseline). Pts residual = actual - OOF mean_int.

| measure | n | mean | ci_lo | ci_hi |
|---|---|---|---|---|
| pts residual (model) | 13559 | -0.319 | -0.402 | -0.233 |
| minutes residual (vs prior-10) | 13559 | 1.070 | 0.884 | 1.264 |

Share of deep-bench games with >= 15 actual minutes: 0.3269 [0.3172,0.3364] (n=13559).

### Share with >= 15 minutes, split by condition

**rotation (>=20 min) teammate OUT at T-60**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| rotation OUT | 9535 | 0.372 | 0.360 | 0.384 | 0.151 | 0.132 | 0.167 | 0.359 | 0.384 |
| none OUT | 4020 | 0.221 | 0.207 | 0.235 | -0.151 | -0.168 | -0.133 | 0.244 | 0.192 |
| no report | 4 | 0.250 | 0.000 | 0.250 | -0.077 | -0.335 | -0.068 | 0.250 |  |

**production-definition (>=12 long-run min) teammate OUT at T-60**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| prod-rotation OUT | 11512 | 0.348 | 0.338 | 0.359 | 0.140 | 0.119 | 0.161 | 0.337 | 0.360 |
| prod none OUT | 2043 | 0.208 | 0.190 | 0.228 | -0.140 | -0.161 | -0.119 | 0.231 | 0.186 |
| no report (prod def) | 4 | 0.250 | 0.000 | 0.250 | -0.077 | -0.335 | -0.069 | 0.250 |  |

**blowout (post-game)**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| blowout | 4028 | 0.302 | 0.284 | 0.321 | -0.035 | -0.056 | -0.012 | 0.310 | 0.295 |
| not blowout | 9531 | 0.337 | 0.325 | 0.349 | 0.035 | 0.014 | 0.056 | 0.326 | 0.349 |

**first 15 team games**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| first15 | 2102 | 0.355 | 0.333 | 0.377 | 0.033 | 0.008 | 0.058 | 0.340 | 0.368 |
| later | 11457 | 0.322 | 0.312 | 0.332 | -0.033 | -0.056 | -0.009 | 0.318 | 0.326 |

**team on back-to-back**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| b2b | 2451 | 0.382 | 0.360 | 0.407 | 0.068 | 0.045 | 0.092 | 0.381 | 0.384 |
| rested | 11108 | 0.315 | 0.305 | 0.325 | -0.068 | -0.092 | -0.042 | 0.309 | 0.321 |

**rotation teammate returning**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| rot teammate returning | 1328 | 0.282 | 0.258 | 0.310 | -0.049 | -0.078 | -0.022 | 0.295 | 0.272 |
| no | 12231 | 0.332 | 0.321 | 0.342 | 0.049 | 0.021 | 0.081 | 0.324 | 0.340 |

**own prior minutes**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| prior min >= 8 | 9272 | 0.409 | 0.399 | 0.421 | 0.261 | 0.245 | 0.276 | 0.405 | 0.414 |
| prior min < 8 | 4287 | 0.149 | 0.135 | 0.162 | -0.261 | -0.276 | -0.245 | 0.133 | 0.163 |

**Elo win prob**

| level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi | mean_2023 | mean_2024 |
|---|---|---|---|---|---|---|---|---|---|
| Elo fav >= .65 | 4148 | 0.296 | 0.282 | 0.312 | -0.044 | -0.063 | -0.025 | 0.301 | 0.292 |
| Elo mid | 5840 | 0.296 | 0.282 | 0.311 | -0.054 | -0.073 | -0.034 | 0.284 | 0.310 |
| Elo dog <= .35 | 3571 | 0.413 | 0.395 | 0.430 | 0.117 | 0.096 | 0.137 | 0.408 | 0.419 |

Pts and minutes residual split by T-60 OUT condition (reports present):

| measure | level | n | mean | ci_lo | ci_hi | diff_vs_rest | d_lo | d_hi |
|---|---|---|---|---|---|---|---|---|
| resid | rotation OUT | 9535 | -0.310 | -0.411 | -0.205 | 0.029 | -0.133 | 0.186 |
| resid | none OUT | 4020 | -0.339 | -0.463 | -0.197 | -0.029 | -0.183 | 0.139 |
| min_resid | rotation OUT | 9535 | 1.878 | 1.642 | 2.129 | 2.729 | 2.384 | 3.059 |
| min_resid | none OUT | 4020 | -0.851 | -1.089 | -0.581 | -2.729 | -3.069 | -2.428 |

Ranking by absolute difference in real-minutes share (pre-tip knowable conditions marked *):

| condition | level | n | share | diff_vs_rest | ci |
|---|---|---|---|---|---|
| own prior minutes * | prior min >= 8 | 9272 | 0.409 | 0.261 | [0.245,0.276] |
| rotation (>=20 min) teammate OUT at T-60 * | rotation OUT | 9535 | 0.372 | 0.151 | [0.132,0.167] |
| production-definition (>=12 long-run min) teammate OUT at T-60 * | prod none OUT | 2043 | 0.208 | -0.140 | [-0.161,-0.119] |
| Elo win prob * | Elo dog <= .35 | 3571 | 0.413 | 0.117 | [0.096,0.137] |
| team on back-to-back * | b2b | 2451 | 0.382 | 0.068 | [0.045,0.092] |
| rotation teammate returning * | rot teammate returning | 1328 | 0.282 | -0.049 | [-0.078,-0.022] |
| blowout (post-game) | blowout | 4028 | 0.302 | -0.035 | [-0.056,-0.012] |
| first 15 team games * | first15 | 2102 | 0.355 | 0.033 | [0.008,0.058] |

### Candidate questions for Devin (Q8)

1. (probably yes) Is a rotation teammate OUT at T-60 the pre-tip signal for deep-bench minutes? 37.2% of deep-bench games reach 15 minutes with one OUT vs 22.1% with none (diff 15 pts, CI 13-17, same in both seasons); the player's own prior minutes (>= 8 vs < 8: 41% vs 15%) is bigger but is already in the baseline. The pts model already carries the OUT effect (pts residual -0.31 vs -0.34), while the prior-10 minutes baseline misses by +1.9 vs -0.9 min.
2. (probably yes) Is the production pts mean too high for deep-bench players? Mean residual -0.32 pts (CI -0.40,-0.23, n=13559), identical with and without a teammate OUT; a small bench-specific shrink is plausible but is a calibration question (pre-register as a one-line fix with a practical floor, not a new model).
3. (probably no) Does the blowout/garbage-time story dominate? Blowouts (post-game) lower the 15-minute share (30% vs 34%); back-to-back raises it (38% vs 32%), Elo underdogs raise it (41% vs 30%): small, and the OUT flag is 2x larger.

## Q9 Absorption

Team-games with exactly one OUT player (T-60 report) whose prior-10 mean minutes >= 24 (as of the day before): 1651 (of 3175 team-games with at least one such OUT player; others have 2+). Teammates = players with minutes>0 and a prior-10 mean on that team in that game (so only teammates who actually played are candidates: 'who plays' is not known at T-60, so everything below is a description of what happened, not a pre-tip forecast).

Analysed team-games (>=3 teammates with prior-10 means): 1651.

Share of the vacated minutes (OUT player's prior-10 mean) taken by the single largest gainer: median 0.307, quartiles 0.217 / 0.424; mean 0.336. Median vacated minutes 29.5; median largest gain 9.3 min; median total net teammate gain -0.6 min (net gain can be below the vacated minutes because other teammates lose minutes to the extra rotation shuffling, blowouts or the OUT player's own backup eating them, and above it in overtime).

Fresh vs stale absence. If the OUT player also missed his team's previous game (stale/long-term absence), the teammates' own prior-10 means already contain the absorbed minutes, so 'gain vs prior-10' and the share of the vacated minutes are biased down for those. Split:

| out player | n_teamgames | share_q1 | share_median | share_q3 | median_vacated_min | median_largest_gain | median_net_team_gain | same_pos | same_pos_chance | top_bench | top_bench_chance | hot_hand | hot_chance |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fresh (played the team's previous game) | 305 | 0.251 | 0.330 | 0.481 | 30.607 | 10.217 | 4.287 | 0.456 | 0.391 | 0.118 | 0.179 | 0.102 | 0.099 |
| stale (missed the previous game too) | 1346 | 0.210 | 0.301 | 0.414 | 29.210 | 8.971 | -1.867 | 0.423 | 0.379 | 0.083 |  | 0.111 | 0.101 |

How often the largest gainer is...

| largest gainer is | n_teamgames | rate | ci_lo | ci_hi | chance_if_random |
|---|---|---|---|---|---|
| same position bucket as the OUT player | 1651 | 0.429 | 0.406 | 0.452 | 0.382 |
| top bench player by prior minutes (st10<0.5) | 1649 | 0.089 | 0.075 | 0.103 | 0.193 |
| hot hand (prior-5 pts mean minus mean of games 6-20 back, largest) | 1651 | 0.110 | 0.094 | 0.124 | 0.100 |
| a starter (prior-10 starter share >= .5) | 1651 | 0.330 | 0.307 | 0.355 |  |

(chance_if_random = the rate if the gainer were picked uniformly among the eligible teammates of that team-game.)

Residual on pts (actual - OOF mean) for the largest gainer vs the other teammates (pts model rows). Caution: the largest gainer is chosen from actual minutes after the game, so more minutes mechanically means more points and a positive residual; this shows how big the miss is when the minutes land on one man, not a forecastable edge.

| group | n | mean_resid | ci_lo | ci_hi |
|---|---|---|---|---|
| largest gainer | 1651 | 4.326 | 4.035 | 4.601 |
| other teammates | 15850 | -0.439 | -0.523 | -0.355 |
| difference |  | 4.766 | 4.464 | 5.072 |

| season | n_top | resid_top | n_other | resid_other |
|---|---|---|---|---|
| 2023 | 818 | 4.249 | 7790 | -0.486 |
| 2024 | 833 | 4.402 | 8060 | -0.394 |

Median share of vacated minutes by whether the gainer shares the OUT player's position:

| same_pos | n | median_share |
|---|---|---|
| False | 942 | 0.300 |
| True | 709 | 0.320 |

### Candidate questions for Devin (Q9)

1. (probably no) Can the largest minutes-gainer be named before tip from position, hot hand or top-bench status? Same position as the OUT player 43% vs 38% by chance; hot hand 11% vs 10% by chance; top bench player 9% vs 19% by chance (lower than random: starters, 33% of gainers, take the minutes). Median top gainer takes 30.7% (quartiles 21.7-42.4%) of the vacated minutes, 9.3 min.
2. (probably yes) Is absorption concentrated enough that a one-man bump is worth modelling if the next-lineup (T-30) information arrives? The largest gainer beats the model by +4.3 pts (CI 4.0-4.6) vs -0.44 for the others, in both seasons; but this is selected on actual minutes, so it only says the miss is large when minutes land on one man; the lever is the T-30 lineup, not a pre-tip identifier.
3. (probably no) Should we trust 'share of vacated minutes' as a headline? Median net teammate gain is small (+4 fresh, -2 stale absences) because prior-10 windows already contain long absences and rookies without priors are excluded; prefer the absolute largest gain (median 9 min) and fresh-absence cases only (n=305).

## Q10 Lines (Pinnacle main line, T-60, vs model integer mean)

Index convention verified on all model rows: p_ge[i] = P(Y >= i+1) (max |sum(p_ge) - mean_int| = 7.11e-14); the meta text says 'P(Y>=k) vector' but the vector starts at k=1.

Pairs: model rows joined to Pinnacle T-60 main-line rows and actuals; 72674 joined before cleaning (0 duplicate keys collapsed), 72674 after dropping non-multiple-of-0.5 lines, lines <= 0 and pushes (whole-number lines: model P(over|no push) = P(Y>=k+1)/(P(Y>=k+1)+P(Y<=k-1)), the production push rule). Model P clipped to [0.5/199, 1-0.5/199]; Pinnacle = multiplicative de-vig. z = (line - mean_int)/std_int. 'Positive z' means the line sits above the model mean (an over is a ceiling bet), 'negative z' means a floor bet. Brier/logloss diff = model minus Pinnacle on identical rows (negative = model closer); CI is game-clustered.

Coverage: Pinnacle quotes only a subset of players (about 43% of played pts rows at T-60 in 2023, higher for 20+ pt players), so this is the market's best-covered slice. Rows are pooled over 2023 and 2024.

The requested buckets are coarse: almost all pairs fall in |z| < 0.5 (the z < -1 bucket has too few pairs, < 30, to report). Finer bins inside it, pooled over stats and then for pts:

| zfine | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a: -0.5..-0.25 | 10348 | 0.540 | 0.567 | 0.536 | 0.026 | -0.005 | 0.250 | 0.247 | 0.003 | 0.002 | 0.004 |
| b: -0.25..0 | 24782 | 0.490 | 0.473 | 0.501 | -0.017 | 0.011 | 0.250 | 0.248 | 0.002 | 0.001 | 0.003 |
| c: 0..0.25 | 23558 | 0.452 | 0.386 | 0.472 | -0.066 | 0.020 | 0.252 | 0.246 | 0.005 | 0.004 | 0.007 |
| d: 0.25..0.5 | 9554 | 0.423 | 0.308 | 0.454 | -0.115 | 0.031 | 0.257 | 0.243 | 0.014 | 0.011 | 0.017 |

| zfine | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a: -0.5..-0.25 | 3262 | 0.511 | 0.583 | 0.510 | 0.072 | -0.001 | 0.254 | 0.249 | 0.005 | 0.002 | 0.008 |
| b: -0.25..0 | 9014 | 0.490 | 0.491 | 0.502 | 0.000 | 0.011 | 0.251 | 0.249 | 0.001 | 0.000 | 0.002 |
| c: 0..0.25 | 8362 | 0.477 | 0.405 | 0.493 | -0.072 | 0.017 | 0.255 | 0.249 | 0.006 | 0.004 | 0.008 |
| d: 0.25..0.5 | 3153 | 0.464 | 0.325 | 0.487 | -0.138 | 0.023 | 0.269 | 0.249 | 0.019 | 0.013 | 0.025 |

### Overall by stat

| stat_ | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 25210 | 0.485 | 0.448 | 0.498 | -0.036 | 0.013 | 0.257 | 0.249 | 0.008 | 0.006 | 0.009 | 0.017 | 0.014 | 0.020 |
| reb | 12984 | 0.467 | 0.423 | 0.491 | -0.044 | 0.023 | 0.252 | 0.247 | 0.005 | 0.003 | 0.007 | 0.011 | 0.007 | 0.015 |
| ast | 13485 | 0.484 | 0.444 | 0.492 | -0.040 | 0.007 | 0.253 | 0.246 | 0.007 | 0.005 | 0.009 | 0.017 | 0.013 | 0.020 |
| fg3m | 20995 | 0.463 | 0.412 | 0.480 | -0.051 | 0.017 | 0.248 | 0.243 | 0.006 | 0.004 | 0.007 | 0.013 | 0.010 | 0.015 |

### Pooled and per stat, by line-position bucket

| all_ | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 72674 | 0.475 | 0.433 | 0.490 | -0.043 | 0.015 | 0.253 | 0.246 | 0.006 | 0.006 | 0.007 | 0.015 | 0.013 | 0.017 |

**pts**

| zbk | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2: -1..-0.5 | 503 | 0.549 | 0.695 | 0.515 | 0.146 | -0.034 | 0.272 | 0.246 | 0.026 | 0.009 | 0.044 | 0.060 | 0.023 | 0.099 |
| 3: -0.5..0.5 | 23791 | 0.485 | 0.451 | 0.498 | -0.034 | 0.013 | 0.255 | 0.249 | 0.006 | 0.005 | 0.007 | 0.012 | 0.010 | 0.014 |
| 4: 0.5..1 | 853 | 0.448 | 0.241 | 0.487 | -0.207 | 0.039 | 0.292 | 0.249 | 0.043 | 0.028 | 0.061 | 0.106 | 0.070 | 0.145 |
| 5: z>1 (line far above mean) | 53 | 0.453 | 0.124 | 0.490 | -0.329 | 0.037 | 0.351 | 0.252 | 0.098 | -0.014 | 0.205 | 0.304 | 0.044 | 0.573 |

**reb**

| zbk | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2: -1..-0.5 | 184 | 0.549 | 0.698 | 0.538 | 0.149 | -0.011 | 0.269 | 0.246 | 0.022 | -0.001 | 0.048 | 0.051 | -0.003 | 0.107 |
| 3: -0.5..0.5 | 12119 | 0.471 | 0.430 | 0.492 | -0.042 | 0.021 | 0.251 | 0.247 | 0.004 | 0.002 | 0.005 | 0.008 | 0.005 | 0.011 |
| 4: 0.5..1 | 632 | 0.377 | 0.236 | 0.460 | -0.140 | 0.084 | 0.255 | 0.241 | 0.014 | -0.004 | 0.032 | 0.039 | -0.002 | 0.080 |
| 5: z>1 (line far above mean) | 46 | 0.370 | 0.121 | 0.469 | -0.248 | 0.100 | 0.299 | 0.245 | 0.054 | -0.040 | 0.158 | 0.212 | -0.097 | 0.517 |

**ast**

| zbk | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2: -1..-0.5 | 506 | 0.563 | 0.684 | 0.554 | 0.121 | -0.009 | 0.262 | 0.243 | 0.019 | 0.007 | 0.032 | 0.043 | 0.016 | 0.068 |
| 3: -0.5..0.5 | 12474 | 0.483 | 0.443 | 0.490 | -0.039 | 0.008 | 0.251 | 0.246 | 0.005 | 0.003 | 0.006 | 0.010 | 0.007 | 0.013 |
| 4: 0.5..1 | 451 | 0.430 | 0.223 | 0.456 | -0.208 | 0.025 | 0.286 | 0.241 | 0.045 | 0.023 | 0.065 | 0.110 | 0.063 | 0.160 |
| 5: z>1 (line far above mean) | 45 | 0.578 | 0.116 | 0.463 | -0.462 | -0.115 | 0.456 | 0.251 | 0.204 | 0.104 | 0.305 | 0.606 | 0.331 | 0.885 |

**fg3m**

| zbk | n | realised_over | model_P | pinn_P | model_bias | pinn_bias | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2: -1..-0.5 | 572 | 0.636 | 0.695 | 0.620 | 0.058 | -0.017 | 0.236 | 0.231 | 0.005 | -0.002 | 0.013 | 0.014 | -0.003 | 0.032 |
| 3: -0.5..0.5 | 19858 | 0.461 | 0.410 | 0.478 | -0.051 | 0.018 | 0.248 | 0.243 | 0.005 | 0.004 | 0.006 | 0.011 | 0.008 | 0.014 |
| 4: 0.5..1 | 553 | 0.371 | 0.212 | 0.400 | -0.158 | 0.030 | 0.258 | 0.232 | 0.026 | 0.012 | 0.042 | 0.069 | 0.033 | 0.109 |

**pts: tier (recency-mean terciles of the stat among Pinnacle-quoted pairs; cuts 11.9/17.9) and role**

| tier | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tier1 low | 8395 | 0.488 | 0.448 | 0.496 | 0.257 | 0.249 | 0.008 | 0.006 | 0.010 | 0.018 | 0.013 | 0.022 |
| tier2 | 8420 | 0.493 | 0.462 | 0.499 | 0.259 | 0.249 | 0.009 | 0.007 | 0.012 | 0.021 | 0.016 | 0.025 |
| tier3 high | 8395 | 0.473 | 0.435 | 0.498 | 0.255 | 0.249 | 0.006 | 0.003 | 0.008 | 0.013 | 0.008 | 0.018 |

| role | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench | 5731 | 0.493 | 0.440 | 0.497 | 0.259 | 0.250 | 0.009 | 0.006 | 0.012 | 0.021 | 0.015 | 0.028 |
| starter | 19479 | 0.482 | 0.451 | 0.498 | 0.256 | 0.249 | 0.007 | 0.006 | 0.009 | 0.016 | 0.013 | 0.019 |

**reb: tier (recency-mean terciles of the stat among Pinnacle-quoted pairs; cuts 6.0/7.9) and role**

| tier | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tier1 low | 4324 | 0.451 | 0.401 | 0.484 | 0.249 | 0.245 | 0.004 | 0.001 | 0.007 | 0.010 | 0.003 | 0.018 |
| tier2 | 4336 | 0.481 | 0.443 | 0.493 | 0.253 | 0.248 | 0.006 | 0.003 | 0.008 | 0.012 | 0.006 | 0.019 |
| tier3 high | 4324 | 0.471 | 0.425 | 0.495 | 0.252 | 0.248 | 0.004 | 0.001 | 0.008 | 0.010 | 0.003 | 0.018 |

| role | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench | 2164 | 0.458 | 0.383 | 0.485 | 0.257 | 0.247 | 0.010 | 0.004 | 0.016 | 0.026 | 0.012 | 0.039 |
| starter | 10820 | 0.469 | 0.431 | 0.492 | 0.250 | 0.247 | 0.004 | 0.002 | 0.005 | 0.008 | 0.004 | 0.012 |

**ast: tier (recency-mean terciles of the stat among Pinnacle-quoted pairs; cuts 4.2/5.5) and role**

| tier | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tier1 low | 4491 | 0.485 | 0.429 | 0.483 | 0.255 | 0.246 | 0.010 | 0.007 | 0.013 | 0.023 | 0.016 | 0.030 |
| tier2 | 4503 | 0.491 | 0.463 | 0.496 | 0.251 | 0.246 | 0.006 | 0.003 | 0.009 | 0.013 | 0.007 | 0.019 |
| tier3 high | 4491 | 0.478 | 0.441 | 0.495 | 0.254 | 0.247 | 0.006 | 0.003 | 0.009 | 0.014 | 0.008 | 0.021 |

| role | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench | 2064 | 0.463 | 0.402 | 0.483 | 0.261 | 0.246 | 0.016 | 0.010 | 0.022 | 0.039 | 0.026 | 0.052 |
| starter | 11421 | 0.488 | 0.452 | 0.493 | 0.252 | 0.246 | 0.006 | 0.004 | 0.007 | 0.013 | 0.009 | 0.016 |

**fg3m: tier (recency-mean terciles of the stat among Pinnacle-quoted pairs; cuts 1.6/2.2) and role**

| tier | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tier1 low | 6992 | 0.440 | 0.383 | 0.445 | 0.247 | 0.240 | 0.006 | 0.004 | 0.009 | 0.015 | 0.010 | 0.020 |
| tier2 | 7011 | 0.485 | 0.452 | 0.504 | 0.247 | 0.243 | 0.004 | 0.002 | 0.006 | 0.008 | 0.004 | 0.012 |
| tier3 high | 6992 | 0.465 | 0.401 | 0.492 | 0.252 | 0.245 | 0.007 | 0.004 | 0.010 | 0.016 | 0.010 | 0.021 |

| role | n | realised_over | model_P | pinn_P | brier_model | brier_pinn | dBrier | dB_lo | dB_hi | dLogloss | dL_lo | dL_hi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench | 5458 | 0.453 | 0.396 | 0.469 | 0.248 | 0.241 | 0.007 | 0.004 | 0.009 | 0.016 | 0.009 | 0.023 |
| starter | 15537 | 0.467 | 0.418 | 0.484 | 0.248 | 0.243 | 0.005 | 0.004 | 0.007 | 0.012 | 0.008 | 0.015 |

### Cells: stat x z-bucket x role (dBrier model-minus-Pinnacle; n >= 100 shown), sorted

Best 8 (model closest relative to Pinnacle):

| stat | role | zbucket | n | realised_over | model_P | pinn_P | dBrier | dB_lo | dB_hi |
|---|---|---|---|---|---|---|---|---|---|
| reb | starter | 4: 0.5..1 | 348 | 0.351 | 0.244 | 0.454 | 0.001 | -0.020 | 0.022 |
| reb | starter | 3: -0.5..0.5 | 10302 | 0.472 | 0.433 | 0.493 | 0.003 | 0.001 | 0.005 |
| ast | starter | 3: -0.5..0.5 | 10703 | 0.486 | 0.447 | 0.491 | 0.004 | 0.002 | 0.006 |
| pts | bench | 3: -0.5..0.5 | 5245 | 0.493 | 0.450 | 0.497 | 0.004 | 0.002 | 0.006 |
| fg3m | starter | 2: -1..-0.5 | 401 | 0.633 | 0.691 | 0.618 | 0.005 | -0.004 | 0.013 |
| fg3m | starter | 3: -0.5..0.5 | 14814 | 0.464 | 0.415 | 0.482 | 0.005 | 0.003 | 0.007 |
| fg3m | bench | 3: -0.5..0.5 | 5044 | 0.450 | 0.394 | 0.467 | 0.005 | 0.002 | 0.008 |
| reb | bench | 3: -0.5..0.5 | 1817 | 0.467 | 0.409 | 0.488 | 0.006 | 0.001 | 0.011 |

Worst 8 (model furthest behind Pinnacle):

| stat | role | zbucket | n | realised_over | model_P | pinn_P | dBrier | dB_lo | dB_hi |
|---|---|---|---|---|---|---|---|---|---|
| pts | bench | 4: 0.5..1 | 331 | 0.489 | 0.231 | 0.490 | 0.069 | 0.042 | 0.098 |
| ast | bench | 4: 0.5..1 | 200 | 0.435 | 0.215 | 0.457 | 0.048 | 0.013 | 0.082 |
| ast | starter | 4: 0.5..1 | 251 | 0.426 | 0.229 | 0.455 | 0.043 | 0.015 | 0.073 |
| fg3m | bench | 4: 0.5..1 | 234 | 0.389 | 0.210 | 0.400 | 0.038 | 0.013 | 0.064 |
| pts | bench | 2: -1..-0.5 | 111 | 0.514 | 0.687 | 0.513 | 0.034 | 0.001 | 0.068 |
| reb | bench | 4: 0.5..1 | 284 | 0.408 | 0.227 | 0.468 | 0.030 | 0.002 | 0.058 |
| pts | starter | 4: 0.5..1 | 522 | 0.421 | 0.247 | 0.484 | 0.027 | 0.007 | 0.048 |
| pts | starter | 2: -1..-0.5 | 392 | 0.559 | 0.697 | 0.516 | 0.024 | 0.005 | 0.043 |

Cells with CI upper < 0 (model better): 0 of 22; CI lower > 0 (Pinnacle better): 17 of 22. With ~22 cells expect ~1 of each tail by chance at 95%.

### Candidate questions for Devin (Q10)

1. (probably no) Is the model closer than Pinnacle anywhere? Model Brier is worse in every stat (pts +0.008, reb +0.005, ast +0.007, fg3m +0.006, all CIs above 0), every tier and role, and 17 of 22 stat x z x role cells (none the other way). Closest: reb/ast starters near the line (+0.003/+0.004).
2. (probably yes) Is the model overconfident about where the line sits relative to its mean? Across the finer central bins realised over-rate falls only from 0.54 to 0.42 as the line goes from -0.5 to +0.5 sd above the model mean, while model P falls from 0.57 to 0.31 and Pinnacle from 0.54 to 0.45. When the model disagrees with the line, the line is right; a pre-registered test of a one-parameter pull of model P toward the market (or a variance inflation) is the natural follow-up, if the goal is calibration rather than edge.
3. (probably yes) Is the worst place the thin 0.5-1 sd 'ceiling' lines on bench players? pts bench z 0.5-1: model P 0.23, realised 0.49, Pinnacle 0.49, dBrier +0.069 (n=331); ast and 3PM bench and reb show the same pattern. Limited by n (<= 850 per such bucket) and by Pinnacle's selective coverage.
