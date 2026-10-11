# Realised joint atlas of same-game prop-leg pairs (2026-10-11)

Descriptive scratch job. No rule, no claim, no production use, no holdout. Seasons 2023 and 2024 only (2023-24, 2024-25); season 2025 never loaded. Pinnacle t60, one row per player-game-stat. Script: `data/scratch/joint_atlas_2026-10-11.py` (+ `data/scratch/joint_atlas_report_2026-10-11.py` for this file). Numbers: `reports/joint_atlas_2026-10-11/results.json`. No fees, no stakes, no recommendation: this is a map of where dependence is, not a bet list.

## Method in brief

- Legs: 72674 Pinnacle t60 prop legs over 2509 games (p = de-vigged `p_over_mult`; outcome `over`; all lines are half-integers, no pushes). Leg pairs are all unordered pairs of distinct legs in the same game. Game-level legs: Pinnacle t60 spread (favourite covers, p from the favourite's de-vigged price) and total (over), settled from final scores in `nba.duckdb` (read-only); 2481 games have both.
- Pair types: (a) same player, different stats; (b) teammates same stat; (c) teammates cross stat; (d) opponents same stat; (e) opponents cross stat; (f) player over x favourite covers; (g) player over x total over. Teammate = same `is_home_f`. Starter split for (b)/(d) uses `starter10 > 0.5` (a probability, so a proxy): SS both starters, BB both bench, SB mixed. (f)/(g) are split by whether the player's team is the favourite or the underdog.
- Deviation: `dev = mean(1[A and B] - p1*p2)` per pair, with p the Pinnacle marginals (both-under uses (1-p1)(1-p2); over x under averages the two orientations). Joint ratio = sum realised / sum independent. phi = sum (A-p1)(B-p2) / sqrt(sum p1(1-p1) * sum p2(1-p2)), i.e. the correlation of the residual over-indicators. cents per $1 on a fair-odds 2-leg parlay = (ratio - 1) x 100: if the pair were priced as independent at fair marginals, that is the expected return per $1 staked, before fee and spread.
- Calibration adjustment. Raw deviations carry a baseline offset from marginal miscalibration, not dependence: Pinnacle overs hit less often than the de-vigged P(over) (table below). So the headline columns are `adj`: each leg's p is shifted by its stat's in-sample (over-rate minus mean p) before forming the independent baseline. Raw (unadjusted) dev and ratios are in results.json (`dev_oo`, `ratio_oo`, ...). The adjustment is in-sample and descriptive.
- CIs: game-clustered bootstrap, 2000 resamples of games (all games resampled, sums are linear so cells with few games are handled honestly). Percentile 95%.
- Multiplicity: 67 cells x 3 outcomes (OO, UU, OU) x several statistics are shown with no correction. Cells overlap heavily (fine cells nest in coarse ones). Read 'CI excludes zero' as a pointer, not a finding; with ~200 unadjusted intervals roughly 10 would exclude zero by chance alone.

## Sanity checks

**Calibration of the marginals (why adjustment is needed).**

| stat | legs | over rate | mean de-vigged p |
|---|---|---|---|
| pts | 25210 | 0.4846 | 0.4978 |
| reb | 12984 | 0.4675 | 0.4909 |
| ast | 13485 | 0.4843 | 0.4916 |
| fg3m | 20995 | 0.4632 | 0.4802 |

Game level: favourite covers 0.5014 vs mean p 0.5012; total over 0.5139 vs mean p 0.5008 (n = 2481 games).

**Shuffle control (outcomes permuted within game x stat, 5 shuffles; unadjusted deviation).** It does not give 0, and the reason is instructive: it gives the calibration offset (no dependence left to find), which is what the `adj` columns remove. The cross-stat residual covariance is slightly positive under this shuffle because the shuffle breaks the p-outcome link while the p's of one player's different stats co-vary; it is an artifact of the control, not of the data.

| pair type | mean raw dev under shuffle | sd over 5 shuffles | mean residual cov |
|---|---|---|---|
| a_same_player_cross_stat | -0.0097 | 0.0005 | +0.0060 |
| b_teammates_same_stat | -0.0152 | 0.0005 | -0.0016 |
| c_teammates_cross_stat | -0.0095 | 0.0004 | +0.0057 |
| d_opponents_same_stat | -0.0159 | 0.0005 | -0.0016 |
| e_opponents_cross_stat | -0.0103 | 0.0004 | +0.0058 |

**Cross-game control (the stronger null).** Leg 1 in game g paired with legs of a different random game, 3 independent draws, classified like (b)-(e). After the calibration adjustment the deviation should be 0 and phi should be 0 if cross-game pairs are independent, which is the project's standing belief. Mean over draws; min lower / max upper CI across draws.

| cross-game pair class | pairs per draw | mean adj dev OO | widest CI | mean phi | phi CI envelope |
|---|---|---|---|---|---|
| X_cross_game_oppside_cross_stat | 382125 | -0.0005 | [-0.0047, +0.0038] | +0.0016 | [-0.0033, +0.0063] |
| X_cross_game_oppside_same_stat | 140628 | -0.0006 | [-0.0059, +0.0045] | +0.0034 | [-0.0026, +0.0097] |
| X_cross_game_teamside_cross_stat | 382043 | +0.0009 | [-0.0035, +0.0058] | +0.0011 | [-0.0034, +0.0062] |
| X_cross_game_teamside_same_stat | 140707 | +0.0005 | [-0.0064, +0.0062] | +0.0014 | [-0.0088, +0.0101] |

Every cross-game class has an adjusted deviation within about +-0.005 and phi within about +-0.01, with every draw's CI containing 0 for the deviation (the 'teamside_same_stat' phi in one draw is borderline). Cross-game independence holds to the resolution of this data. Same-player pts x fg3m, by contrast, is the largest cell in the atlas (see below), as it must be.

## Coarse table (all pair types)

| cell | pairs | games | dev both-over (adj) [95% CI] | dev both-under (adj) [95% CI] | dev over x under (adj) [95% CI] | joint ratio OO (adj) | phi [CI] | cents/$1 OO (adj) | CI excl 0 (OO) |
|---|---|---|---|---|---|---|---|---|---|
| a_same_player_cross_stat | 68256 | 2506 | +0.0483 [+0.0433, +0.0535] | +0.0507 [+0.0451, +0.0565] | -0.0495 [-0.0518, -0.0473] | 1.213 [1.191, 1.236] | +0.200 [+0.191, +0.209] | +21.3c [+19.1, +23.6] | yes |
| b_teammates_same_stat | 124905 | 2502 | -0.0057 [-0.0101, -0.0013] | -0.0054 [-0.0107, -0.0002] | +0.0056 [+0.0041, +0.0070] | 0.975 [0.955, 0.994] | -0.022 [-0.028, -0.016] | -2.5c [-4.5, -0.6] | yes |
| c_teammates_cross_stat | 348061 | 2504 | +0.0026 [-0.0013, +0.0069] | +0.0031 [-0.0019, +0.0079] | -0.0029 [-0.0040, -0.0018] | 1.012 [0.994, 1.031] | +0.012 [+0.008, +0.017] | +1.2c [-0.6, +3.1] | no |
| d_opponents_same_stat | 144911 | 2386 | +0.0025 [-0.0021, +0.0071] | +0.0032 [-0.0019, +0.0082] | -0.0029 [-0.0041, -0.0017] | 1.011 [0.991, 1.031] | +0.012 [+0.008, +0.017] | +1.1c [-0.9, +3.1] | no |
| e_opponents_cross_stat | 390601 | 2387 | -0.0012 [-0.0055, +0.0032] | +0.0019 [-0.0032, +0.0068] | -0.0003 [-0.0013, +0.0006] | 0.995 [0.976, 1.014] | +0.002 [-0.002, +0.006] | -0.5c [-2.4, +1.4] | no |
| f_player_over_x_fav_covers | 70804 | 2446 | -0.0105 [-0.0207, -0.0004] | -0.0094 [-0.0203, +0.0018] | +0.0099 [+0.0078, +0.0122] | 0.956 [0.913, 0.998] | -0.040 [-0.049, -0.031] | -4.4c [-8.7, -0.2] | yes |
| g_player_over_x_total_over | 70804 | 2446 | +0.0164 [+0.0053, +0.0268] | +0.0187 [+0.0075, +0.0309] | -0.0176 [-0.0197, -0.0154] | 1.067 [1.022, 1.110] | +0.070 [+0.062, +0.078] | +6.7c [+2.2, +11.0] | yes |

Reading the 'cents' column: 2-leg parlay at fair independent odds on both-over; e.g. +21.3c means that parlay returns about $1.21 per $1 in expectation if priced as independent and the legs are those pairs (not a bet; before fee, spread, and with in-sample adjustment).

## Plain-language reading, by pair type

**(a) Same player, different stats.** n = 68256 pairs / 2506 games. Both-over happens 21% more often than independence (1.213 [1.191, 1.236]), both-under 18% more (1.185 [1.165, 1.206]), and over x under correspondingly less (-0.0495 [-0.0518, -0.0473]). Pooled phi +0.200. This is almost entirely the pts x fg3m pair (phi ~ +0.50, a three is three points; mechanical, and a pass on the sanity check), then pts x reb and ast x reb (shared minutes and pace). ast x pts (ratio 1.05), ast x fg3m (1.04, CI touches 1) and fg3m x reb (1.05) are small but positive. So the dependence a same-player parlay carries is real, large, and concentrated in a few stat pairs, and a book that prices it as independent is being generous to the bettor; whether any book does is a separate question this file does not test.

**(b) Teammates, same stat.** n = 124905 pairs / 2502 games. Slightly negative: both-over 0.975 [0.955, 0.994], phi -0.022 [-0.028, -0.016]. Two teammates both going over the same stat is about 2.5% rarer than independence (one usage is another's loss), and both-under is similarly rarer. The effect is small and similar in starter/starter, starter/bench and bench/bench pairs (table below); it is a cannibalisation signal, not a lever.

**(c) Teammates, cross stat.** n = 348061 pairs / 2504 games. Pooled adj both-over deviation +0.0026 [-0.0013, +0.0069]: indistinguishable from zero; the over x under row (-0.0029 [-0.0040, -0.0018]) is marginally significant, which with this many cells I would not read as a finding. Within the stat pairs, ast x pts and ast x fg3m among teammates are positive with CIs excluding zero (ratio about 1.05 and 1.06, +5 to +6c, phi +0.05 to +0.06: passer and shooter co-move), while ast x reb, fg3m x reb and pts x reb are at zero and pts x fg3m is mildly negative (ratio 0.97) (see fine table); they cancel in the pooled row.

**(d) Opponents, same stat.** n = 144911 pairs / 2386 games. Adj both-over +0.0025 [-0.0021, +0.0071], phi +0.012: essentially independent. A small positive phi is what shared pace would produce; it does not survive the calibration adjustment as a joint-probability effect.

**(e) Opponents, cross stat.** n = 390601 pairs / 2387 games. Adj both-over -0.0012 [-0.0055, +0.0032], phi +0.002 [-0.002, +0.006]: independent within resolution.

**(f) Player over x favourite covers.** n = 70804 pairs / 2446 games. Pooled adj both-over -0.0105 [-0.0207, -0.0004] (ratio 0.956 [0.913, 0.998]) hides a split. For a player on the underdog, over and 'favourite covers' are negatively dependent (a good night for the underdog player is a good night for the underdog): cells with CI excluding zero, ratio about 0.80-0.86, phi about -0.13 to -0.16 (see fine table for stat-by-side cells). For a player on the favourite the relation is near zero to mildly positive (ratio 1.03-1.09, mostly CIs touching zero). The market's spread and the player's line are set separately, so this is a real, one-sided dependence, and notably also the pairing most likely to be constructed by hand ('star over plus his team to cover').

**(g) Player over x game total over.** n = 70804 pairs / 2446 games. Pooled adj both-over +0.0164 [+0.0053, +0.0268] (ratio 1.067 [1.022, 1.110], phi +0.070); positive for pts, fg3m, ast (ratio 1.09-1.13) and negative for rebounds (ratio about 0.93: rebounds are driven by misses, and high totals are not high-miss games). The sign pattern is the intuitive one and holds on both favourite and underdog teams.

## Starter vs bench, teammates and opponents, same stat

| cell | pairs | games | dev both-over (adj) [95% CI] | dev both-under (adj) [95% CI] | dev over x under (adj) [95% CI] | joint ratio OO (adj) | phi [CI] | cents/$1 OO (adj) | CI excl 0 (OO) |
|---|---|---|---|---|---|---|---|---|---|
| b_teammates_same_stat:BB | 7102 | 1607 | -0.0030 [-0.0171, +0.0108] | -0.0079 [-0.0227, +0.0070] | +0.0054 [-0.0009, +0.0118] | 0.986 [0.922, 1.049] | -0.025 [-0.050, +0.000] | -1.4c [-7.8, +4.9] | no |
| b_teammates_same_stat:SB | 47231 | 2290 | -0.0066 [-0.0121, -0.0009] | -0.0079 [-0.0148, -0.0011] | +0.0073 [+0.0049, +0.0096] | 0.970 [0.946, 0.996] | -0.029 [-0.038, -0.019] | -3.0c [-5.4, -0.4] | yes |
| b_teammates_same_stat:SS | 70572 | 2496 | -0.0054 [-0.0108, +0.0002] | -0.0035 [-0.0098, +0.0029] | +0.0045 [+0.0025, +0.0065] | 0.976 [0.953, 1.001] | -0.017 [-0.025, -0.010] | -2.4c [-4.7, +0.1] | no |
| d_opponents_same_stat:BB | 8282 | 1351 | +0.0138 [+0.0003, +0.0276] | -0.0113 [-0.0260, +0.0041] | -0.0012 [-0.0073, +0.0048] | 1.063 [1.001, 1.126] | +0.007 [-0.016, +0.032] | +6.3c [+0.1, +12.6] | yes |
| d_opponents_same_stat:SB | 49074 | 2218 | -0.0011 [-0.0066, +0.0053] | +0.0027 [-0.0038, +0.0090] | -0.0008 [-0.0031, +0.0016] | 0.995 [0.970, 1.024] | +0.004 [-0.005, +0.013] | -0.5c [-3.0, +2.4] | no |
| d_opponents_same_stat:SS | 87555 | 2364 | +0.0035 [-0.0019, +0.0090] | +0.0049 [-0.0012, +0.0110] | -0.0042 [-0.0059, -0.0026] | 1.015 [0.992, 1.040] | +0.018 [+0.011, +0.024] | +1.5c [-0.8, +4.0] | no |

## Fine cells (stat pair level, n >= 2000 pairs)

| cell | pairs | games | dev both-over (adj) [95% CI] | dev both-under (adj) [95% CI] | dev over x under (adj) [95% CI] | joint ratio OO (adj) | phi [CI] | cents/$1 OO (adj) | CI excl 0 (OO) |
|---|---|---|---|---|---|---|---|---|---|
| a_same_player_cross_stat:ast-fg3m | 10655 | 2483 | +0.0081 [-0.0003, +0.0170] | +0.0097 [+0.0003, +0.0180] | -0.0089 [-0.0135, -0.0039] | 1.036 [0.999, 1.075] | +0.034 [+0.015, +0.053] | +3.6c [-0.1, +7.5] | no |
| a_same_player_cross_stat:ast-pts | 12870 | 2500 | +0.0115 [+0.0038, +0.0197] | +0.0106 [+0.0024, +0.0190] | -0.0111 [-0.0157, -0.0065] | 1.049 [1.016, 1.084] | +0.042 [+0.024, +0.060] | +4.9c [+1.6, +8.4] | yes |
| a_same_player_cross_stat:ast-reb | 6064 | 2405 | +0.0317 [+0.0206, +0.0432] | +0.0254 [+0.0138, +0.0369] | -0.0286 [-0.0349, -0.0225] | 1.139 [1.091, 1.190] | +0.115 [+0.090, +0.140] | +13.9c [+9.1, +19.0] | yes |
| a_same_player_cross_stat:fg3m-pts | 19195 | 2490 | +0.1209 [+0.1146, +0.1270] | +0.1248 [+0.1176, +0.1316] | -0.1229 [-0.1259, -0.1199] | 1.534 [1.506, 1.561] | +0.500 [+0.488, +0.512] | +53.4c [+50.6, +56.1] | yes |
| a_same_player_cross_stat:fg3m-reb | 7492 | 2426 | +0.0104 [+0.0005, +0.0203] | +0.0204 [+0.0100, +0.0312] | -0.0154 [-0.0213, -0.0098] | 1.048 [1.002, 1.094] | +0.063 [+0.041, +0.086] | +4.8c [+0.2, +9.4] | yes |
| a_same_player_cross_stat:pts-reb | 11980 | 2500 | +0.0396 [+0.0316, +0.0478] | +0.0431 [+0.0346, +0.0515] | -0.0414 [-0.0458, -0.0369] | 1.175 [1.140, 1.211] | +0.167 [+0.149, +0.185] | +17.5c [+14.0, +21.1] | yes |
| b_teammates_same_stat:BB | 7102 | 1607 | -0.0030 [-0.0171, +0.0108] | -0.0079 [-0.0227, +0.0070] | +0.0054 [-0.0009, +0.0118] | 0.986 [0.922, 1.049] | -0.025 [-0.050, +0.000] | -1.4c [-7.8, +4.9] | no |
| b_teammates_same_stat:SB | 47231 | 2290 | -0.0066 [-0.0121, -0.0009] | -0.0079 [-0.0148, -0.0011] | +0.0073 [+0.0049, +0.0096] | 0.970 [0.946, 0.996] | -0.029 [-0.038, -0.019] | -3.0c [-5.4, -0.4] | yes |
| b_teammates_same_stat:SS | 70572 | 2496 | -0.0054 [-0.0108, +0.0002] | -0.0035 [-0.0098, +0.0029] | +0.0045 [+0.0025, +0.0065] | 0.976 [0.953, 1.001] | -0.017 [-0.025, -0.010] | -2.4c [-4.7, +0.1] | no |
| b_teammates_same_stat:ast | 14345 | 2460 | -0.0109 [-0.0199, -0.0015] | -0.0048 [-0.0147, +0.0059] | +0.0078 [+0.0038, +0.0117] | 0.954 [0.915, 0.994] | -0.032 [-0.048, -0.016] | -4.6c [-8.5, -0.6] | yes |
| b_teammates_same_stat:fg3m | 39996 | 2482 | -0.0013 [-0.0082, +0.0055] | -0.0024 [-0.0102, +0.0056] | +0.0018 [-0.0006, +0.0043] | 0.994 [0.962, 1.026] | -0.008 [-0.018, +0.002] | -0.6c [-3.8, +2.6] | no |
| b_teammates_same_stat:pts | 57199 | 2501 | -0.0084 [-0.0144, -0.0024] | -0.0087 [-0.0154, -0.0020] | +0.0086 [+0.0067, +0.0104] | 0.964 [0.939, 0.990] | -0.033 [-0.041, -0.026] | -3.6c [-6.1, -1.0] | yes |
| b_teammates_same_stat:reb | 13365 | 2463 | -0.0020 [-0.0120, +0.0079] | -0.0010 [-0.0125, +0.0102] | +0.0015 [-0.0028, +0.0056] | 0.991 [0.945, 1.036] | -0.005 [-0.021, +0.012] | -0.9c [-5.5, +3.6] | no |
| c_teammates_cross_stat:ast-fg3m | 50759 | 2483 | +0.0132 [+0.0073, +0.0195] | +0.0153 [+0.0082, +0.0220] | -0.0143 [-0.0164, -0.0121] | 1.059 [1.032, 1.087] | +0.057 [+0.049, +0.066] | +5.9c [+3.2, +8.7] | yes |
| c_teammates_cross_stat:ast-pts | 60055 | 2499 | +0.0115 [+0.0058, +0.0173] | +0.0155 [+0.0087, +0.0221] | -0.0135 [-0.0154, -0.0116] | 1.049 [1.024, 1.074] | +0.054 [+0.047, +0.062] | +4.9c [+2.4, +7.4] | yes |
| c_teammates_cross_stat:ast-reb | 31390 | 2496 | -0.0026 [-0.0090, +0.0040] | +0.0007 [-0.0066, +0.0081] | +0.0009 [-0.0017, +0.0037] | 0.988 [0.960, 1.018] | -0.001 [-0.012, +0.010] | -1.2c [-4.0, +1.8] | no |
| c_teammates_cross_stat:fg3m-pts | 96183 | 2486 | -0.0058 [-0.0109, -0.0005] | -0.0067 [-0.0128, -0.0010] | +0.0062 [+0.0047, +0.0077] | 0.974 [0.951, 0.998] | -0.024 [-0.030, -0.018] | -2.6c [-4.9, -0.2] | yes |
| c_teammates_cross_stat:fg3m-reb | 51287 | 2486 | +0.0006 [-0.0053, +0.0062] | -0.0003 [-0.0069, +0.0063] | -0.0001 [-0.0024, +0.0021] | 1.003 [0.976, 1.029] | +0.001 [-0.008, +0.009] | +0.3c [-2.4, +2.9] | no |
| c_teammates_cross_stat:pts-reb | 58387 | 2501 | +0.0029 [-0.0028, +0.0087] | +0.0003 [-0.0064, +0.0066] | -0.0016 [-0.0036, +0.0004] | 1.013 [0.987, 1.038] | +0.007 [-0.001, +0.014] | +1.3c [-1.3, +3.8] | no |
| d_opponents_same_stat:BB | 8282 | 1351 | +0.0138 [+0.0003, +0.0276] | -0.0113 [-0.0260, +0.0041] | -0.0012 [-0.0073, +0.0048] | 1.063 [1.001, 1.126] | +0.007 [-0.016, +0.032] | +6.3c [+0.1, +12.6] | yes |
| d_opponents_same_stat:SB | 49074 | 2218 | -0.0011 [-0.0066, +0.0053] | +0.0027 [-0.0038, +0.0090] | -0.0008 [-0.0031, +0.0016] | 0.995 [0.970, 1.024] | +0.004 [-0.005, +0.013] | -0.5c [-3.0, +2.4] | no |
| d_opponents_same_stat:SS | 87555 | 2364 | +0.0035 [-0.0019, +0.0090] | +0.0049 [-0.0012, +0.0110] | -0.0042 [-0.0059, -0.0026] | 1.015 [0.992, 1.040] | +0.018 [+0.011, +0.024] | +1.5c [-0.8, +4.0] | no |
| d_opponents_same_stat:ast | 18406 | 2344 | +0.0001 [-0.0091, +0.0089] | +0.0037 [-0.0058, +0.0134] | -0.0019 [-0.0052, +0.0014] | 1.000 [0.961, 1.038] | +0.007 [-0.006, +0.021] | +0.0c [-3.9, +3.8] | no |
| d_opponents_same_stat:fg3m | 45147 | 2354 | +0.0025 [-0.0043, +0.0094] | +0.0018 [-0.0062, +0.0098] | -0.0021 [-0.0045, +0.0003] | 1.012 [0.980, 1.044] | +0.010 [+0.001, +0.019] | +1.2c [-2.0, +4.4] | no |
| d_opponents_same_stat:pts | 64144 | 2385 | +0.0030 [-0.0035, +0.0093] | +0.0034 [-0.0028, +0.0101] | -0.0032 [-0.0048, -0.0016] | 1.013 [0.985, 1.040] | +0.014 [+0.007, +0.020] | +1.3c [-1.5, +4.0] | no |
| d_opponents_same_stat:reb | 17214 | 2337 | +0.0032 [-0.0062, +0.0131] | +0.0059 [-0.0048, +0.0168] | -0.0045 [-0.0084, -0.0011] | 1.014 [0.972, 1.060] | +0.018 [+0.004, +0.033] | +1.4c [-2.8, +6.0] | no |
| e_opponents_cross_stat:ast-fg3m | 57403 | 2362 | -0.0008 [-0.0068, +0.0055] | +0.0040 [-0.0029, +0.0106] | -0.0016 [-0.0036, +0.0003] | 0.996 [0.970, 1.024] | +0.006 [-0.001, +0.014] | -0.4c [-3.0, +2.4] | no |
| e_opponents_cross_stat:ast-pts | 68612 | 2386 | +0.0008 [-0.0052, +0.0071] | +0.0042 [-0.0023, +0.0106] | -0.0025 [-0.0043, -0.0008] | 1.003 [0.978, 1.030] | +0.010 [+0.004, +0.018] | +0.3c [-2.2, +3.0] | no |
| e_opponents_cross_stat:ast-reb | 35492 | 2381 | -0.0056 [-0.0119, +0.0013] | -0.0000 [-0.0076, +0.0075] | +0.0028 [+0.0004, +0.0054] | 0.975 [0.947, 1.006] | -0.011 [-0.022, -0.002] | -2.5c [-5.3, +0.6] | no |
| e_opponents_cross_stat:fg3m-pts | 107330 | 2371 | +0.0016 [-0.0036, +0.0070] | +0.0027 [-0.0030, +0.0088] | -0.0021 [-0.0036, -0.0007] | 1.007 [0.984, 1.031] | +0.009 [+0.004, +0.015] | +0.7c [-1.6, +3.1] | no |
| e_opponents_cross_stat:fg3m-reb | 55535 | 2370 | -0.0034 [-0.0092, +0.0023] | +0.0009 [-0.0058, +0.0076] | +0.0012 [-0.0008, +0.0033] | 0.984 [0.957, 1.011] | -0.003 [-0.011, +0.005] | -1.6c [-4.3, +1.1] | no |
| e_opponents_cross_stat:pts-reb | 66229 | 2387 | -0.0042 [-0.0099, +0.0016] | -0.0017 [-0.0080, +0.0047] | +0.0030 [+0.0012, +0.0047] | 0.981 [0.956, 1.007] | -0.010 [-0.017, -0.003] | -1.9c [-4.4, +0.7] | no |
| f_player_over_x_fav_covers:ast | 13142 | 2439 | -0.0083 [-0.0192, +0.0026] | -0.0062 [-0.0182, +0.0057] | +0.0072 [+0.0033, +0.0112] | 0.966 [0.921, 1.011] | -0.030 [-0.046, -0.014] | -3.4c [-7.9, +1.1] | no |
| f_player_over_x_fav_covers:dogTeam | 33778 | 2374 | -0.0391 [-0.0487, -0.0298] | -0.0327 [-0.0433, -0.0221] | +0.0359 [+0.0330, +0.0390] | 0.836 [0.795, 0.875] | -0.144 [-0.156, -0.132] | -16.4c [-20.5, -12.5] | yes |
| f_player_over_x_fav_covers:favTeam | 37026 | 2397 | +0.0157 [+0.0046, +0.0270] | +0.0119 [-0.0008, +0.0244] | -0.0138 [-0.0164, -0.0109] | 1.066 [1.019, 1.113] | +0.055 [+0.044, +0.066] | +6.6c [+1.9, +11.3] | yes |
| f_player_over_x_fav_covers:fg3m | 20454 | 2430 | -0.0050 [-0.0159, +0.0056] | -0.0069 [-0.0190, +0.0051] | +0.0060 [+0.0026, +0.0095] | 0.978 [0.931, 1.024] | -0.024 [-0.037, -0.011] | -2.2c [-6.9, +2.4] | no |
| f_player_over_x_fav_covers:pts | 24566 | 2443 | -0.0135 [-0.0238, -0.0031] | -0.0101 [-0.0212, +0.0011] | +0.0118 [+0.0088, +0.0148] | 0.945 [0.902, 0.987] | -0.047 [-0.059, -0.035] | -5.5c [-9.8, -1.3] | yes |
| f_player_over_x_fav_covers:reb | 12642 | 2442 | -0.0159 [-0.0270, -0.0045] | -0.0152 [-0.0274, -0.0031] | +0.0155 [+0.0113, +0.0199] | 0.932 [0.885, 0.981] | -0.063 [-0.080, -0.046] | -6.8c [-11.5, -1.9] | yes |
| g_player_over_x_total_over:ast | 13142 | 2439 | +0.0228 [+0.0102, +0.0349] | +0.0256 [+0.0134, +0.0387] | -0.0242 [-0.0283, -0.0200] | 1.091 [1.041, 1.140] | +0.095 [+0.079, +0.112] | +9.1c [+4.1, +14.0] | yes |
| g_player_over_x_total_over:dogTeam | 33778 | 2374 | +0.0135 [+0.0017, +0.0245] | +0.0215 [+0.0092, +0.0342] | -0.0175 [-0.0207, -0.0142] | 1.055 [1.007, 1.100] | +0.069 [+0.056, +0.082] | +5.5c [+0.7, +10.0] | yes |
| g_player_over_x_total_over:favTeam | 37026 | 2397 | +0.0190 [+0.0075, +0.0302] | +0.0162 [+0.0041, +0.0289] | -0.0176 [-0.0203, -0.0148] | 1.078 [1.031, 1.124] | +0.071 [+0.060, +0.082] | +7.8c [+3.1, +12.4] | yes |
| g_player_over_x_total_over:fg3m | 20454 | 2430 | +0.0230 [+0.0113, +0.0344] | +0.0226 [+0.0103, +0.0362] | -0.0228 [-0.0262, -0.0194] | 1.097 [1.048, 1.145] | +0.092 [+0.079, +0.105] | +9.7c [+4.8, +14.5] | yes |
| g_player_over_x_total_over:pts | 24566 | 2443 | +0.0251 [+0.0127, +0.0366] | +0.0287 [+0.0169, +0.0415] | -0.0269 [-0.0297, -0.0240] | 1.101 [1.051, 1.147] | +0.107 [+0.096, +0.118] | +10.1c [+5.1, +14.7] | yes |
| g_player_over_x_total_over:reb | 12642 | 2442 | -0.0177 [-0.0293, -0.0072] | -0.0140 [-0.0262, -0.0018] | +0.0159 [+0.0115, +0.0203] | 0.926 [0.878, 0.970] | -0.064 [-0.082, -0.047] | -7.4c [-12.2, -3.0] | yes |

## Deviations with CI excluding zero, sorted by |adj both-over deviation| (cells with >= 2000 pairs)

Same-game dependence appears in the three families that the pair structure predicts: same player, player x game total, player x spread (underdog side). Cells nest, so the rows are not independent evidence. CI excludes zero for both-over; columns show the under and over x under companions.

| rank | cell | pairs | games | dev OO (adj) [CI] | dev UU (adj) [CI] | dev OU (adj) [CI] | ratio OO | cents/$1 OO |
|---|---|---|---|---|---|---|---|---|
| 1 | a_same_player_cross_stat:fg3m-pts | 19195 | 2490 | +0.1209 [+0.1146, +0.1270] | +0.1248 [+0.1176, +0.1316] | -0.1229 [-0.1259, -0.1199] | 1.534 | +53.4 |
| 2 | f_player_over_x_fav_covers:dogTeam:pts | 11918 | 2372 | -0.0435 [-0.0538, -0.0335] | -0.0358 [-0.0464, -0.0252] | +0.0397 [+0.0357, +0.0437] | 0.821 | -17.9 |
| 3 | f_player_over_x_fav_covers:dogTeam:ast | 6189 | 2339 | -0.0413 [-0.0531, -0.0296] | -0.0287 [-0.0418, -0.0158] | +0.0350 [+0.0294, +0.0407] | 0.830 | -17.0 |
| 4 | a_same_player_cross_stat:pts-reb | 11980 | 2500 | +0.0396 [+0.0316, +0.0478] | +0.0431 [+0.0346, +0.0515] | -0.0414 [-0.0458, -0.0369] | 1.175 | +17.5 |
| 5 | f_player_over_x_fav_covers:dogTeam:reb | 5923 | 2340 | -0.0392 [-0.0517, -0.0266] | -0.0344 [-0.0474, -0.0217] | +0.0368 [+0.0308, +0.0428] | 0.833 | -16.7 |
| 6 | f_player_over_x_fav_covers:dogTeam | 33778 | 2374 | -0.0391 [-0.0487, -0.0298] | -0.0327 [-0.0433, -0.0221] | +0.0359 [+0.0330, +0.0390] | 0.836 | -16.4 |
| 7 | f_player_over_x_fav_covers:dogTeam:fg3m | 9748 | 2348 | -0.0324 [-0.0432, -0.0219] | -0.0305 [-0.0421, -0.0180] | +0.0315 [+0.0267, +0.0362] | 0.860 | -14.0 |
| 8 | a_same_player_cross_stat:ast-reb | 6064 | 2405 | +0.0317 [+0.0206, +0.0432] | +0.0254 [+0.0138, +0.0369] | -0.0286 [-0.0349, -0.0225] | 1.139 | +13.9 |
| 9 | g_player_over_x_total_over:favTeam:ast | 6953 | 2384 | +0.0292 [+0.0153, +0.0431] | +0.0192 [+0.0052, +0.0332] | -0.0242 [-0.0297, -0.0185] | 1.117 | +11.7 |
| 10 | g_player_over_x_total_over:favTeam:pts | 12648 | 2394 | +0.0278 [+0.0146, +0.0400] | +0.0269 [+0.0141, +0.0404] | -0.0274 [-0.0309, -0.0237] | 1.111 | +11.1 |
| 11 | g_player_over_x_total_over:favTeam:fg3m | 10706 | 2374 | +0.0251 [+0.0126, +0.0374] | +0.0202 [+0.0064, +0.0348] | -0.0227 [-0.0274, -0.0183] | 1.105 | +10.5 |
| 12 | g_player_over_x_total_over:pts | 24566 | 2443 | +0.0251 [+0.0127, +0.0366] | +0.0287 [+0.0169, +0.0415] | -0.0269 [-0.0297, -0.0240] | 1.101 | +10.1 |
| 13 | g_player_over_x_total_over:fg3m | 20454 | 2430 | +0.0230 [+0.0113, +0.0344] | +0.0226 [+0.0103, +0.0362] | -0.0228 [-0.0262, -0.0194] | 1.097 | +9.7 |
| 14 | g_player_over_x_total_over:ast | 13142 | 2439 | +0.0228 [+0.0102, +0.0349] | +0.0256 [+0.0134, +0.0387] | -0.0242 [-0.0283, -0.0200] | 1.091 | +9.1 |
| 15 | g_player_over_x_total_over:dogTeam:pts | 11918 | 2372 | +0.0222 [+0.0093, +0.0347] | +0.0305 [+0.0177, +0.0438] | -0.0264 [-0.0305, -0.0222] | 1.089 | +8.9 |
| 16 | f_player_over_x_fav_covers:favTeam:ast | 6953 | 2384 | +0.0211 [+0.0080, +0.0349] | +0.0139 [-0.0001, +0.0276] | -0.0175 [-0.0229, -0.0119] | 1.087 | +8.7 |
| 17 | g_player_over_x_total_over:dogTeam:fg3m | 9748 | 2348 | +0.0206 [+0.0079, +0.0331] | +0.0252 [+0.0115, +0.0389] | -0.0229 [-0.0279, -0.0180] | 1.087 | +8.7 |
| 18 | f_player_over_x_fav_covers:favTeam:fg3m | 10706 | 2374 | +0.0200 [+0.0071, +0.0331] | +0.0145 [+0.0001, +0.0286] | -0.0172 [-0.0217, -0.0128] | 1.086 | +8.6 |
| 19 | g_player_over_x_total_over:favTeam | 37026 | 2397 | +0.0190 [+0.0075, +0.0302] | +0.0162 [+0.0041, +0.0289] | -0.0176 [-0.0203, -0.0148] | 1.078 | +7.8 |
| 20 | g_player_over_x_total_over:dogTeam:reb | 5923 | 2340 | -0.0178 [-0.0314, -0.0047] | -0.0147 [-0.0284, -0.0000] | +0.0163 [+0.0099, +0.0227] | 0.926 | -7.4 |
| 21 | g_player_over_x_total_over:reb | 12642 | 2442 | -0.0177 [-0.0293, -0.0072] | -0.0140 [-0.0262, -0.0018] | +0.0159 [+0.0115, +0.0203] | 0.926 | -7.4 |
| 22 | g_player_over_x_total_over:favTeam:reb | 6719 | 2380 | -0.0177 [-0.0306, -0.0058] | -0.0134 [-0.0267, -0.0001] | +0.0155 [+0.0100, +0.0216] | 0.926 | -7.4 |
| 23 | f_player_over_x_fav_covers:reb | 12642 | 2442 | -0.0159 [-0.0270, -0.0045] | -0.0152 [-0.0274, -0.0031] | +0.0155 [+0.0113, +0.0199] | 0.932 | -6.8 |
| 24 | f_player_over_x_fav_covers:favTeam | 37026 | 2397 | +0.0157 [+0.0046, +0.0270] | +0.0119 [-0.0008, +0.0244] | -0.0138 [-0.0164, -0.0109] | 1.066 | +6.6 |
| 25 | g_player_over_x_total_over:dogTeam:ast | 6189 | 2339 | +0.0156 [+0.0012, +0.0293] | +0.0328 [+0.0186, +0.0478] | -0.0242 [-0.0303, -0.0183] | 1.063 | +6.3 |
| 26 | f_player_over_x_fav_covers:favTeam:pts | 12648 | 2394 | +0.0148 [+0.0025, +0.0268] | +0.0141 [+0.0008, +0.0269] | -0.0145 [-0.0182, -0.0108] | 1.061 | +6.1 |
| 27 | d_opponents_same_stat:BB | 8282 | 1351 | +0.0138 [+0.0003, +0.0276] | -0.0113 [-0.0260, +0.0041] | -0.0012 [-0.0073, +0.0048] | 1.063 | +6.3 |
| 28 | g_player_over_x_total_over:dogTeam | 33778 | 2374 | +0.0135 [+0.0017, +0.0245] | +0.0215 [+0.0092, +0.0342] | -0.0175 [-0.0207, -0.0142] | 1.055 | +5.5 |
| 29 | f_player_over_x_fav_covers:pts | 24566 | 2443 | -0.0135 [-0.0238, -0.0031] | -0.0101 [-0.0212, +0.0011] | +0.0118 [+0.0088, +0.0148] | 0.945 | -5.5 |
| 30 | c_teammates_cross_stat:ast-fg3m | 50759 | 2483 | +0.0132 [+0.0073, +0.0195] | +0.0153 [+0.0082, +0.0220] | -0.0143 [-0.0164, -0.0121] | 1.059 | +5.9 |
| 31 | a_same_player_cross_stat:ast-pts | 12870 | 2500 | +0.0115 [+0.0038, +0.0197] | +0.0106 [+0.0024, +0.0190] | -0.0111 [-0.0157, -0.0065] | 1.049 | +4.9 |
| 32 | c_teammates_cross_stat:ast-pts | 60055 | 2499 | +0.0115 [+0.0058, +0.0173] | +0.0155 [+0.0087, +0.0221] | -0.0135 [-0.0154, -0.0116] | 1.049 | +4.9 |
| 33 | b_teammates_same_stat:ast | 14345 | 2460 | -0.0109 [-0.0199, -0.0015] | -0.0048 [-0.0147, +0.0059] | +0.0078 [+0.0038, +0.0117] | 0.954 | -4.6 |
| 34 | a_same_player_cross_stat:fg3m-reb | 7492 | 2426 | +0.0104 [+0.0005, +0.0203] | +0.0204 [+0.0100, +0.0312] | -0.0154 [-0.0213, -0.0098] | 1.048 | +4.8 |
| 35 | b_teammates_same_stat:pts | 57199 | 2501 | -0.0084 [-0.0144, -0.0024] | -0.0087 [-0.0154, -0.0020] | +0.0086 [+0.0067, +0.0104] | 0.964 | -3.6 |
| 36 | b_teammates_same_stat:SB | 47231 | 2290 | -0.0066 [-0.0121, -0.0009] | -0.0079 [-0.0148, -0.0011] | +0.0073 [+0.0049, +0.0096] | 0.970 | -3.0 |
| 37 | c_teammates_cross_stat:fg3m-pts | 96183 | 2486 | -0.0058 [-0.0109, -0.0005] | -0.0067 [-0.0128, -0.0010] | +0.0062 [+0.0047, +0.0077] | 0.974 | -2.6 |

## What this does and does not say

- It maps realised dependence between legs against the Pinnacle marginals. It says nothing about what any retail parlay product charges for that dependence; that needs the product's quoted price, and nothing here is a price comparison.
- A parlay that is priced as independent when dependence is positive is cheap for the bettor on the positively dependent side; the table gives the size of the gap in fair-odds terms. After any fee or spread the gap shrinks or reverses; the largest cell (same player pts x fg3m) is the one place the gap is big enough to matter, and it is also the cell every book already knows about.
- The edge, if any, for 'the model' is not here: these are properties of the market marginals and outcomes, not of our forecasts. No claim, no stake, no recommendation. Keep your money remains the answer until the live log says otherwise.

## Data problems and caveats

- Pinnacle overs hit less than the de-vigged P(over) in every stat (pts -1.3pt, reb -2.3pt, ast -0.7pt, fg3m -1.7pt). Adjustment removes it for the joint baseline; the bias itself is worth a separate look (de-vig method, half-line rounding, or selection).
- `_dnp` is False on every row: legs for players who did not play appear to have been dropped upstream, so these pairs are conditional on the player playing. That is a selection on a post-hoc outcome and can bias dependence (minutes-driven co-movement is partly removed). Do not read these as unconditional parlay odds.
- 26 of 2509 games have no usable spread/total pair (2481 have both), so (f)/(g) cover slightly fewer games.
- Pairs within a game share outcomes, so n of pairs overstates information; the CI is clustered on games (~2,500) and is the number to trust, not the pair count.
- Starter flag is a probability thresholded at 0.5, not the actual starting five.
- Both seasons pooled; no check that the pattern is stable across 2023-24 vs 2024-25. Descriptive only.
