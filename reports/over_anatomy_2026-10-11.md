# Over anatomy, DraftKings main line, t60 (descriptive scratch; no rule, no claim)

Seasons 2023 and 2024 only (2025 never loaded). Played rows only (the pairs file has no DNP rows, so 'over' here is conditional on the player playing; a scratch is not an under in this file). CIs: game-clustered bootstrap, 500 reps, 95% percentile. `pm` = mean OOF production P(over) in the cell; over rate minus pm is what the model already prices.

## Plain-language summary

**Part 1.** Against the DraftKings t60 main line, whether an over hits is overwhelmingly decided by things nobody knows at t60. Decomposing y - line, the per-minute rate surprise carries 79-88% of the variance (pts 0.79, reb 0.81, ast 0.88, fg3m 0.88) and the minutes surplus only 7-20% (pts 0.20 [0.19,0.21], reb 0.17, ast 0.10, fg3m 0.07); the line-vs-recency gap is 1-5%. Minutes still matter as a *switch*: the pooled over rate is 0.18 [0.17,0.19] (n=8533) when a player plays 8+ minutes under his trailing-10 mean and 0.69 [0.68,0.70] (n=8531) when he plays 8+ over, versus 0.49 in the middle. Post-hoc context moves it too (team won 0.516 vs lost 0.439 pooled; margin >20 0.424 vs <=5 0.510; OT 0.574 vs 0.474), but none of that is knowable at t60. What is knowable pre-tip (home/away, starter proxy) moves the over rate by about 1 point and the production p_model already prices it (pooled AUC of p_model 0.556 against 0.653 for the unknowable minutes surplus and 0.93 for the rate surprise, which is nearly the outcome itself). The teammate-exit proxy shows no reliable lift (0.488 vs 0.477 pooled) once blowouts are separated; it is post-hoc and confounded with margin. Caveat: pm (mean p_model) sits near 0.45 against a realised 0.48 because these rows are conditional on playing, so p_model, which presumably carries DNP mass, is not directly comparable to the played-only over rate.

**Part 2.** There is no evidence that the model's upper tail is too thin for points: realised P(y > line+k) over predicted is 0.94, 0.94, 0.96, 1.03 for k = 3, 5, 8, 12 (n=30234, CIs mostly excluding 1 only at small k, where the model is slightly too HIGH), and the tail is if anything fatter than the model only for high-line players at 2x the line (0.0068 vs 0.0045, ~68 events; low power). For reb/ast/fg3m the model's tail is too fat, not too thin: ast realised/predicted falls to 0.85, 0.72, 0.55 at k=4, 6, 8; reb 0.90, 0.83 at k=6, 8; fg3m 0.88, 0.76 at k=3, 4. Raw z has positive skew (0.47-0.99) and excess kurtosis above a Gaussian (pts 0.43 [0.31,0.58], reb 0.60, ast 0.69, fg3m 1.31), but this is similar to what the model itself implies for the count stats (model-sim 0.52, 0.63, 1.75); points are heavier than their own model-sim reference (0.43 vs -0.18, Hill alpha 5.0 vs 6.1), a mild fat-tail signal that does not show up as an under-forecast of the realised exceedance frequencies at k>=3. The pts PIT is mildly U/tilted (0.112 in the first and last deciles, 0.087-0.091 in 0.5-0.9), consistent with a slight overweight of both tails relative to a sharper centre; the count-stat PITs are within ~0.01 of uniform. Taleb-style reading: the extreme events here are bounded by minutes and roster, and they are mostly already in the model's mass.

Data notes:
- pairs DK t60 rows 106999; rows with no box minutes 0.0000; y==box stat match 1.0000
- dropped for min_exp NaN (<3 prior games) 0; after min_exp>=5 filter rows 106997
- OT share of player rows 0.0476; tm_exit>0 share 0.1425
- games 2616, rows 106997, bootstrap reps 500, game-clustered

## Part 1. Anatomy

Expected minutes = trailing-10 mean of the player's own played-game minutes from strictly earlier game dates (min 3 prior games, min_exp >= 5; asserted no same-day/same-game rows). rate_exp = m_recency / min_exp (m_recency is the pipeline's recency stat mean, so min_exp x rate_exp = m_recency exactly). Identity: y - line = A + B + C, A = (min_real - min_exp) x rate_exp (minutes surplus), B = min_real x (rate_real - rate_exp) (per-minute rate surprise), C = m_recency - line (book line vs recency mean). Note minutes are post-hoc (includes DNP-adjacent short stints only if player played).

### 1a. Component means (stat units) for overs that hit vs unders that hit, and variance share

| stat | n (over/under) | y-line over / under | A minutes over / under | B rate over / under | C line-vs-recency over / under | Var share A [CI] | Var share B [CI] | Var share C [CI] |
|---|---|---|---|---|---|---|---|---|
| pts | 30234 (14679/15555) | +5.45 / -4.81 | +1.17 / -0.88 | +4.22 / -3.92 | +0.06 / -0.02 | 0.200 [0.194,0.207] | 0.791 [0.784,0.798] | 0.009 [0.006,0.012] |
| reb | 29800 (14105/15695) | +2.26 / -1.91 | +0.40 / -0.25 | +1.80 / -1.60 | +0.06 / -0.06 | 0.169 [0.163,0.176] | 0.807 [0.801,0.814] | 0.024 [0.020,0.027] |
| ast | 20224 (9776/10448) | +1.97 / -1.61 | +0.21 / -0.15 | +1.65 / -1.51 | +0.11 / +0.04 | 0.102 [0.095,0.109] | 0.877 [0.871,0.885] | 0.020 [0.016,0.024] |
| fg3m | 26739 (12653/14086) | +1.36 / -0.99 | +0.11 / -0.07 | +1.10 / -0.96 | +0.15 / +0.04 | 0.072 [0.067,0.075] | 0.884 [0.879,0.889] | 0.045 [0.042,0.048] |

Var share = Cov(component, y-line)/Var(y-line); the three sum to 1 (point estimates).

### 1b. Over rate by minutes surplus (min_real - trailing-10 min_exp) [NOT knowable pre-tip]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| <= -8 | 0.119 [0.106,0.135] pm 0.483 n=2415 | 0.177 [0.162,0.192] pm 0.471 n=2381 | 0.212 [0.190,0.234] pm 0.471 n=1560 | 0.233 [0.217,0.253] pm 0.449 n=2177 | 0.182 [0.172,0.192] pm 0.469 n=8533 |
| -8..-3 | 0.303 [0.291,0.314] pm 0.472 n=5605 | 0.336 [0.324,0.348] pm 0.465 n=5491 | 0.388 [0.373,0.405] pm 0.461 n=3558 | 0.367 [0.353,0.381] pm 0.443 n=4953 | 0.344 [0.336,0.352] pm 0.461 n=19607 |
| -3..+3 | 0.494 [0.486,0.501] pm 0.456 n=12742 | 0.482 [0.474,0.490] pm 0.453 n=12622 | 0.495 [0.486,0.505] pm 0.457 n=8976 | 0.480 [0.472,0.489] pm 0.433 n=11351 | 0.487 [0.483,0.492] pm 0.450 n=45691 |
| +3..+8 | 0.646 [0.635,0.657] pm 0.438 n=6975 | 0.600 [0.589,0.612] pm 0.437 n=6872 | 0.576 [0.563,0.590] pm 0.440 n=4701 | 0.569 [0.557,0.580] pm 0.423 n=6087 | 0.601 [0.595,0.607] pm 0.434 n=24635 |
| > +8 | 0.757 [0.737,0.773] pm 0.404 n=2497 | 0.671 [0.651,0.690] pm 0.401 n=2434 | 0.638 [0.612,0.662] pm 0.403 n=1429 | 0.653 [0.633,0.672] pm 0.409 n=2171 | 0.686 [0.675,0.696] pm 0.404 n=8531 |

### Overtime game [NOT knowable pre-tip; OT = team minutes > 252]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| no OT | 0.480 [0.474,0.485] pm 0.453 n=28788 | 0.467 [0.462,0.473] pm 0.449 n=28384 | 0.480 [0.473,0.486] pm 0.452 n=19271 | 0.470 [0.464,0.477] pm 0.432 n=25458 | 0.474 [0.470,0.477] pm 0.446 n=101901 |
| OT | 0.600 [0.580,0.619] pm 0.447 n=1446 | 0.596 [0.570,0.624] pm 0.449 n=1416 | 0.561 [0.530,0.596] pm 0.444 n=953 | 0.528 [0.499,0.555] pm 0.434 n=1281 | 0.574 [0.560,0.587] pm 0.444 n=5096 |

### Final margin |points| [NOT knowable pre-tip]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| <= 5 | 0.522 [0.511,0.531] pm 0.450 n=7494 | 0.516 [0.505,0.526] pm 0.451 n=7389 | 0.505 [0.490,0.518] pm 0.449 n=5002 | 0.494 [0.481,0.505] pm 0.432 n=6601 | 0.510 [0.504,0.516] pm 0.446 n=26486 |
| 6-12 | 0.506 [0.497,0.515] pm 0.452 n=10496 | 0.495 [0.485,0.504] pm 0.447 n=10309 | 0.494 [0.482,0.505] pm 0.451 n=6950 | 0.478 [0.467,0.488] pm 0.431 n=9265 | 0.494 [0.488,0.500] pm 0.445 n=37020 |
| 13-20 | 0.463 [0.452,0.474] pm 0.454 n=6743 | 0.457 [0.445,0.469] pm 0.449 n=6656 | 0.480 [0.468,0.494] pm 0.454 n=4583 | 0.467 [0.455,0.479] pm 0.433 n=5988 | 0.466 [0.459,0.473] pm 0.447 n=23970 |
| > 20 | 0.424 [0.412,0.436] pm 0.456 n=5501 | 0.395 [0.382,0.405] pm 0.448 n=5446 | 0.439 [0.426,0.455] pm 0.452 n=3689 | 0.445 [0.431,0.460] pm 0.431 n=4885 | 0.424 [0.416,0.432] pm 0.447 n=19521 |

### Home / away [knowable pre-tip]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| home | 0.488 [0.480,0.494] pm 0.451 n=15249 | 0.475 [0.467,0.483] pm 0.447 n=15019 | 0.492 [0.483,0.500] pm 0.451 n=10256 | 0.480 [0.472,0.488] pm 0.435 n=13495 | 0.483 [0.479,0.487] pm 0.446 n=54019 |
| away | 0.483 [0.476,0.491] pm 0.455 n=14985 | 0.472 [0.463,0.480] pm 0.450 n=14781 | 0.475 [0.466,0.484] pm 0.451 n=9968 | 0.467 [0.459,0.476] pm 0.429 n=13244 | 0.474 [0.469,0.480] pm 0.446 n=52978 |

### Starter vs bench: pre-tip proxy starter10 >= 0.5 [knowable pre-tip]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| starter10 >= .5 | 0.483 [0.476,0.490] pm 0.454 n=21879 | 0.479 [0.472,0.486] pm 0.455 n=21741 | 0.489 [0.482,0.497] pm 0.458 n=16264 | 0.472 [0.465,0.478] pm 0.432 n=19163 | 0.481 [0.477,0.485] pm 0.450 n=79047 |
| bench (starter10 < .5) | 0.492 [0.481,0.503] pm 0.450 n=8355 | 0.458 [0.448,0.468] pm 0.431 n=8059 | 0.459 [0.443,0.473] pm 0.422 n=3960 | 0.476 [0.465,0.487] pm 0.430 n=7576 | 0.473 [0.466,0.480] pm 0.435 n=27950 |

### Starter vs bench: box-score actual starter [post-hoc]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| started | 0.485 [0.479,0.491] pm 0.444 n=23077 | 0.481 [0.474,0.488] pm 0.446 n=22909 | 0.491 [0.484,0.499] pm 0.452 n=16977 | 0.475 [0.469,0.481] pm 0.426 n=20157 | 0.483 [0.479,0.487] pm 0.442 n=83120 |
| bench | 0.487 [0.476,0.499] pm 0.481 n=7157 | 0.448 [0.436,0.460] pm 0.458 n=6891 | 0.443 [0.425,0.458] pm 0.449 n=3247 | 0.466 [0.456,0.478] pm 0.449 n=6582 | 0.464 [0.456,0.472] pm 0.461 n=23877 |

### Player's team won / lost [NOT knowable pre-tip]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| won | 0.528 [0.521,0.535] pm 0.454 n=15507 | 0.504 [0.497,0.511] pm 0.446 n=15337 | 0.525 [0.516,0.533] pm 0.452 n=10465 | 0.510 [0.502,0.517] pm 0.435 n=13868 | 0.516 [0.512,0.521] pm 0.446 n=55177 |
| lost | 0.441 [0.433,0.448] pm 0.452 n=14727 | 0.440 [0.432,0.448] pm 0.452 n=14463 | 0.439 [0.430,0.449] pm 0.450 n=9759 | 0.434 [0.426,0.443] pm 0.429 n=12871 | 0.439 [0.434,0.443] pm 0.446 n=51820 |

### Teammate exit proxy: a teammate with >= 20 min in his previous game played 1..9 min this game [post-hoc, box minutes only; confounded with blowouts, so also split on margin <= 12]

| bucket | pts | reb | ast | fg3m | pooled |
|---|---|---|---|---|---|
| no exit | 0.483 [0.477,0.488] pm 0.452 n=25931 | 0.471 [0.465,0.477] pm 0.448 n=25584 | 0.483 [0.476,0.491] pm 0.450 n=17302 | 0.472 [0.466,0.479] pm 0.431 n=22932 | 0.477 [0.473,0.481] pm 0.445 n=91749 |
| exit >= 1 | 0.500 [0.487,0.514] pm 0.460 n=4303 | 0.486 [0.472,0.501] pm 0.453 n=4216 | 0.486 [0.469,0.504] pm 0.456 n=2922 | 0.479 [0.462,0.495] pm 0.435 n=3807 | 0.488 [0.480,0.498] pm 0.451 n=15248 |
| no exit, margin<=12 | 0.509 [0.502,0.516] pm 0.451 n=15824 | 0.499 [0.492,0.507] pm 0.448 n=15564 | 0.499 [0.490,0.509] pm 0.449 n=10493 | 0.485 [0.476,0.493] pm 0.432 n=13946 | 0.498 [0.494,0.503] pm 0.445 n=55827 |
| exit >= 1, margin<=12 | 0.541 [0.523,0.558] pm 0.456 n=2166 | 0.534 [0.513,0.553] pm 0.452 n=2134 | 0.491 [0.468,0.516] pm 0.454 n=1459 | 0.481 [0.456,0.502] pm 0.434 n=1920 | 0.515 [0.503,0.527] pm 0.449 n=7679 |

### 1d. How much of the over is pre-tip knowable

| stat | n | AUC of over: production p_model (pre-tip) | AUC: minutes surplus (post-hoc) | AUC: rate surprise component B (post-hoc) |
|---|---|---|---|---|
| pts | 30234 | 0.520 | 0.701 | 0.929 |
| reb | 29800 | 0.555 | 0.654 | 0.937 |
| ast | 20224 | 0.553 | 0.618 | 0.950 |
| fg3m | 26739 | 0.596 | 0.625 | 0.972 |
| pooled | 106997 | 0.556 | 0.653 | 0.932 |

(AUC point estimates only; with n of 10^4 the sampling SE is about 0.005.)

## Part 2. Upper tail / black swan

Distribution used: reconstructed, not the production object. pts = normal(mean_int, std_int) discretised on the integer grid (continuity-corrected, truncated at 0); reb/ast/fg3m = NegBin by method of moments (alpha=(var-mean)/mean^2, as `negbin_from_moments`). Reconstruction check (mean_int/std_int may already include the production calibration; p_model is the OOF calibrated probability, so the two need not match):
- reconstruction check pts: corr(my P(y>line), p_model)=0.983; mean mine 0.514 vs p_model 0.453 vs realised 0.486
- reconstruction check reb: corr(my P(y>line), p_model)=0.994; mean mine 0.461 vs p_model 0.449 vs realised 0.473
- reconstruction check ast: corr(my P(y>line), p_model)=0.990; mean mine 0.477 vs p_model 0.451 vs realised 0.483
- reconstruction check fg3m: corr(my P(y>line), p_model)=0.982; mean mine 0.456 vs p_model 0.432 vs realised 0.473

### 2a. pts: predicted vs realised P(y > line + k)

| k | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 3 | all | 30234 | 0.3318 | 0.3107 [0.3066,0.3159] | 0.94 |
| 3 | T1 low line | 10561 | 0.3447 | 0.2993 [0.2912,0.3084] | 0.87 |
| 3 | T2 mid line | 9693 | 0.3279 | 0.3109 [0.3021,0.3194] | 0.95 |
| 3 | T3 high line | 9980 | 0.3220 | 0.3225 [0.3143,0.3313] | 1.00 |
| 5 | all | 30234 | 0.2291 | 0.2154 [0.2114,0.2198] | 0.94 |
| 5 | T1 low line | 10561 | 0.2235 | 0.1944 [0.1875,0.2020] | 0.87 |
| 5 | T2 mid line | 9693 | 0.2271 | 0.2174 [0.2093,0.2249] | 0.96 |
| 5 | T3 high line | 9980 | 0.2370 | 0.2358 [0.2276,0.2440] | 0.99 |
| 8 | all | 30234 | 0.1176 | 0.1130 [0.1097,0.1161] | 0.96 |
| 8 | T1 low line | 10561 | 0.1004 | 0.0908 [0.0858,0.0963] | 0.90 |
| 8 | T2 mid line | 9693 | 0.1163 | 0.1113 [0.1043,0.1171] | 0.96 |
| 8 | T3 high line | 9980 | 0.1371 | 0.1380 [0.1316,0.1446] | 1.01 |
| 12 | all | 30234 | 0.0404 | 0.0417 [0.0395,0.0442] | 1.03 |
| 12 | T1 low line | 10561 | 0.0269 | 0.0276 [0.0249,0.0309] | 1.03 |
| 12 | T2 mid line | 9693 | 0.0386 | 0.0400 [0.0362,0.0441] | 1.04 |
| 12 | T3 high line | 9980 | 0.0564 | 0.0581 [0.0536,0.0627] | 1.03 |

Extreme multiples for pts (y >= c x line):

| multiple | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 1.5x | all | 30234 | 0.1708 | 0.1557 [0.1520,0.1597] | 0.91 |
| 1.5x | T1 low line | 10561 | 0.2769 | 0.2386 [0.2313,0.2468] | 0.86 |
| 1.5x | T2 mid line | 9693 | 0.1561 | 0.1495 [0.1417,0.1565] | 0.96 |
| 1.5x | T3 high line | 9980 | 0.0728 | 0.0739 [0.0685,0.0793] | 1.02 |
| 2.0x | all | 30234 | 0.0507 | 0.0471 [0.0449,0.0496] | 0.93 |
| 2.0x | T1 low line | 10561 | 0.1138 | 0.0989 [0.0934,0.1049] | 0.87 |
| 2.0x | T2 mid line | 9693 | 0.0295 | 0.0323 [0.0285,0.0356] | 1.09 |
| 2.0x | T3 high line | 9980 | 0.0045 | 0.0068 [0.0052,0.0085] | 1.51 |

### 2a. reb: predicted vs realised P(y > line + k)

| k | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 2 | all | 29800 | 0.2062 | 0.2080 [0.2033,0.2125] | 1.01 |
| 2 | T1 low line | 11432 | 0.1853 | 0.1753 [0.1677,0.1818] | 0.95 |
| 2 | T2 mid line | 9460 | 0.2029 | 0.2034 [0.1956,0.2115] | 1.00 |
| 2 | T3 high line | 8908 | 0.2365 | 0.2548 [0.2462,0.2646] | 1.08 |
| 4 | all | 29800 | 0.0772 | 0.0747 [0.0720,0.0777] | 0.97 |
| 4 | T1 low line | 11432 | 0.0530 | 0.0457 [0.0419,0.0493] | 0.86 |
| 4 | T2 mid line | 9460 | 0.0736 | 0.0706 [0.0651,0.0759] | 0.96 |
| 4 | T3 high line | 8908 | 0.1121 | 0.1162 [0.1099,0.1230] | 1.04 |
| 6 | all | 29800 | 0.0266 | 0.0239 [0.0222,0.0256] | 0.90 |
| 6 | T1 low line | 11432 | 0.0129 | 0.0098 [0.0080,0.0114] | 0.76 |
| 6 | T2 mid line | 9460 | 0.0234 | 0.0192 [0.0167,0.0222] | 0.82 |
| 6 | T3 high line | 8908 | 0.0476 | 0.0468 [0.0428,0.0509] | 0.98 |
| 8 | all | 29800 | 0.0090 | 0.0074 [0.0065,0.0084] | 0.83 |
| 8 | T1 low line | 11432 | 0.0029 | 0.0017 [0.0010,0.0026] | 0.60 |
| 8 | T2 mid line | 9460 | 0.0070 | 0.0054 [0.0040,0.0071] | 0.77 |
| 8 | T3 high line | 8908 | 0.0188 | 0.0170 [0.0145,0.0196] | 0.90 |

Extreme multiples for reb (y >= c x line):

| multiple | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 1.5x | all | 29800 | 0.1796 | 0.1776 [0.1732,0.1813] | 0.99 |
| 1.5x | T1 low line | 11432 | 0.2410 | 0.2342 [0.2258,0.2412] | 0.97 |
| 1.5x | T2 mid line | 9460 | 0.1719 | 0.1700 [0.1631,0.1781] | 0.99 |
| 1.5x | T3 high line | 8908 | 0.1090 | 0.1129 [0.1064,0.1191] | 1.04 |
| 2.0x | all | 29800 | 0.0792 | 0.0741 [0.0710,0.0768] | 0.94 |
| 2.0x | T1 low line | 11432 | 0.1392 | 0.1299 [0.1233,0.1358] | 0.93 |
| 2.0x | T2 mid line | 9460 | 0.0599 | 0.0576 [0.0528,0.0630] | 0.96 |
| 2.0x | T3 high line | 8908 | 0.0225 | 0.0199 [0.0170,0.0226] | 0.88 |

### 2a. ast: predicted vs realised P(y > line + k)

| k | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 2 | all | 20224 | 0.1953 | 0.1857 [0.1808,0.1914] | 0.95 |
| 2 | T1 low line | 10544 | 0.1699 | 0.1572 [0.1504,0.1642] | 0.93 |
| 2 | T2 mid line | 3489 | 0.2257 | 0.2066 [0.1934,0.2203] | 0.92 |
| 2 | T3 high line | 6191 | 0.2216 | 0.2224 [0.2129,0.2338] | 1.00 |
| 4 | all | 20224 | 0.0654 | 0.0558 [0.0528,0.0592] | 0.85 |
| 4 | T1 low line | 10544 | 0.0457 | 0.0360 [0.0324,0.0395] | 0.79 |
| 4 | T2 mid line | 3489 | 0.0813 | 0.0645 [0.0559,0.0719] | 0.79 |
| 4 | T3 high line | 6191 | 0.0898 | 0.0846 [0.0782,0.0922] | 0.94 |
| 6 | all | 20224 | 0.0195 | 0.0141 [0.0126,0.0159] | 0.72 |
| 6 | T1 low line | 10544 | 0.0108 | 0.0066 [0.0052,0.0082] | 0.61 |
| 6 | T2 mid line | 3489 | 0.0247 | 0.0169 [0.0131,0.0214] | 0.68 |
| 6 | T3 high line | 6191 | 0.0314 | 0.0254 [0.0216,0.0293] | 0.81 |
| 8 | all | 20224 | 0.0055 | 0.0030 [0.0023,0.0038] | 0.55 |
| 8 | T1 low line | 10544 | 0.0025 | 0.0011 [0.0006,0.0018] | 0.46 |
| 8 | T2 mid line | 3489 | 0.0068 | 0.0040 [0.0023,0.0064] | 0.59 |
| 8 | T3 high line | 6191 | 0.0099 | 0.0057 [0.0040,0.0075] | 0.57 |

Extreme multiples for ast (y >= c x line):

| multiple | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 1.5x | all | 20224 | 0.2120 | 0.1997 [0.1948,0.2052] | 0.94 |
| 1.5x | T1 low line | 10544 | 0.2588 | 0.2436 [0.2358,0.2520] | 0.94 |
| 1.5x | T2 mid line | 3489 | 0.2257 | 0.2066 [0.1934,0.2203] | 0.92 |
| 1.5x | T3 high line | 6191 | 0.1245 | 0.1208 [0.1133,0.1286] | 0.97 |
| 2.0x | all | 20224 | 0.1103 | 0.0974 [0.0930,0.1023] | 0.88 |
| 2.0x | T1 low line | 10544 | 0.1653 | 0.1514 [0.1448,0.1591] | 0.92 |
| 2.0x | T2 mid line | 3489 | 0.0813 | 0.0645 [0.0559,0.0719] | 0.79 |
| 2.0x | T3 high line | 6191 | 0.0331 | 0.0239 [0.0200,0.0284] | 0.72 |

### 2a. fg3m: predicted vs realised P(y > line + k)

| k | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 1 | all | 26739 | 0.2339 | 0.2362 [0.2311,0.2418] | 1.01 |
| 1 | T1 low line | 17913 | 0.2314 | 0.2320 [0.2251,0.2386] | 1.00 |
| 1 | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 1 | T3 high line | 8826 | 0.2389 | 0.2450 [0.2368,0.2535] | 1.03 |
| 2 | all | 26739 | 0.1094 | 0.1045 [0.1006,0.1083] | 0.96 |
| 2 | T1 low line | 17913 | 0.1009 | 0.0951 [0.0906,0.0998] | 0.94 |
| 2 | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 2 | T3 high line | 8826 | 0.1268 | 0.1236 [0.1170,0.1299] | 0.97 |
| 3 | all | 26739 | 0.0483 | 0.0424 [0.0400,0.0449] | 0.88 |
| 3 | T1 low line | 17913 | 0.0412 | 0.0357 [0.0329,0.0386] | 0.87 |
| 3 | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 3 | T3 high line | 8826 | 0.0627 | 0.0561 [0.0516,0.0606] | 0.90 |
| 4 | all | 26739 | 0.0205 | 0.0156 [0.0142,0.0171] | 0.76 |
| 4 | T1 low line | 17913 | 0.0162 | 0.0119 [0.0103,0.0135] | 0.74 |
| 4 | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 4 | T3 high line | 8826 | 0.0294 | 0.0229 [0.0200,0.0259] | 0.78 |

Extreme multiples for fg3m (y >= c x line):

| multiple | group | n | predicted | realised [CI] | realised / predicted |
|---|---|---|---|---|---|
| 1.5x | all | 26739 | 0.2875 | 0.2901 [0.2843,0.2955] | 1.01 |
| 1.5x | T1 low line | 17913 | 0.3238 | 0.3270 [0.3198,0.3341] | 1.01 |
| 1.5x | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 1.5x | T3 high line | 8826 | 0.2140 | 0.2152 [0.2067,0.2235] | 1.01 |
| 2.0x | all | 26739 | 0.2536 | 0.2542 [0.2490,0.2593] | 1.00 |
| 2.0x | T1 low line | 17913 | 0.3238 | 0.3270 [0.3198,0.3341] | 1.01 |
| 2.0x | T2 mid line | 0 | nan | nan [nan,nan] | nan |
| 2.0x | T3 high line | 8826 | 0.1112 | 0.1063 [0.1004,0.1123] | 0.96 |

Book's implicit view of these tails: not available (alternate lines are not in the pairs file); only the main line (P(y > line) ~ 0.5 by construction) is priced.

### 2b. Taleb-style checks on z = (y - mean_int)/std_int

Excess kurtosis (Gaussian = 0). 'model-sim' = same statistic on one seeded draw from the model's own distribution (NegBin is itself skewed/heavy, so the fair reference is the model, not 0). Hill tail index alpha on the top 5% of positive z (larger = thinner; Gaussian-like tails give large values; bootstrap 200 game-resamples). Skew: mean z^3.

| stat | n | skew z | excess kurt z [CI] | excess kurt model-sim [CI] | Hill alpha z [CI] | Hill alpha model-sim |
|---|---|---|---|---|---|---|
| pts | 30234 | 0.47 | 0.43 [0.31,0.58] | -0.18 [-0.23,-0.13] | 5.01 [4.73,5.29] | 6.09 |
| reb | 29800 | 0.63 | 0.60 [0.48,0.73] | 0.52 [0.35,0.69] | 4.87 [4.55,5.27] | 5.10 |
| ast | 20224 | 0.66 | 0.69 [0.49,0.87] | 0.63 [0.42,0.85] | 5.07 [4.67,5.51] | 4.97 |
| fg3m | 26739 | 0.99 | 1.31 [1.10,1.54] | 1.75 [1.44,2.11] | 4.67 [4.38,4.98] | 4.33 |

z mean / sd (should be ~0 / ~1): pts +0.029/1.023; reb +0.027/1.014; ast +0.004/0.997; fg3m +0.024/0.982

### 2c. Randomised PIT, 10 bins (uniform = 0.100 each)

| stat | n | 0.0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 30234 | 0.112 [0.109,0.115] | 0.110 [0.106,0.114] | 0.107 [0.103,0.111] | 0.105 [0.101,0.108] | 0.098 [0.094,0.101] | 0.091 [0.088,0.095] | 0.090 [0.087,0.094] | 0.088 [0.085,0.091] | 0.087 [0.084,0.090] | 0.112 [0.109,0.115] |
| reb | 29800 | 0.098 [0.095,0.102] | 0.095 [0.092,0.099] | 0.096 [0.093,0.100] | 0.096 [0.093,0.099] | 0.100 [0.096,0.103] | 0.103 [0.099,0.106] | 0.101 [0.097,0.105] | 0.102 [0.098,0.106] | 0.105 [0.102,0.109] | 0.103 [0.100,0.107] |
| ast | 20224 | 0.098 [0.093,0.102] | 0.098 [0.093,0.101] | 0.097 [0.093,0.101] | 0.097 [0.093,0.101] | 0.102 [0.098,0.106] | 0.102 [0.098,0.106] | 0.103 [0.099,0.107] | 0.103 [0.099,0.107] | 0.102 [0.098,0.107] | 0.098 [0.094,0.102] |
| fg3m | 26739 | 0.092 [0.089,0.096] | 0.092 [0.088,0.095] | 0.097 [0.093,0.100] | 0.098 [0.095,0.102] | 0.105 [0.101,0.108] | 0.101 [0.098,0.105] | 0.105 [0.102,0.109] | 0.105 [0.101,0.109] | 0.106 [0.102,0.110] | 0.099 [0.095,0.102] |

