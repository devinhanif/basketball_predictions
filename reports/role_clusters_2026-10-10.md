# Role clusters and who absorbs minutes (DESCRIPTIVE ONLY)

DESCRIPTIVE ONLY: no rule, no claim, no promotion. Seasons 2023 and 2024 only (season 2025 never loaded; every SQL carries season <= 2024 and the script asserts it). Game-clustered 95% percentile bootstrap, 200 resamples, seed 2026 (B=200 for speed: CI ends are coarse). No multiplicity correction. Script: data/scratch/role_clusters_2026-10-10.py (data construction copied verbatim from model_miss_questions_2026-10-10.py, Q9).

## Definitions

- Role profile (as-of, per player-game): over the player's previous 20 games with minutes>0 (shift(1), at least 8 games, crossing season boundaries, 2022 lookback), pts, reb, ast, stl, blk, tov per 36 minutes (sum of stat / sum of minutes * 36); fg3a share = sum fg3a / sum fga; fta rate = sum fta / sum fga; usage proxy = (fga + 0.44 fta + tov) / minute; minutes mean. 10 features.
- Standardised with mean/sd of the 27268 2023 player-game rows that have a profile; KMeans(n_init=20, random_state=2026) fit on 2023 ONLY; 2024 assigned with the fitted centroids (no refit). Rows with fewer than 8 prior games have no role and are dropped from eligible teammates.
- OUT player role = his profile through his last game before the target game (stale absences use his last profile).
- Event set (copied): team-games with exactly one OUT player at T-60 (latest official report with as_of <= real tip - 60 min) whose prior-10 mean minutes >= 24; teammates are those who played (minutes>0) with a prior-10 mean and a role; at least 3 such teammates. gain = minutes - prior-10 mean minutes. resid = pts actual - OOF mean_int (pts model rows).
- 'Largest gainer' = teammate with the largest gain (chosen on actual minutes, after the game: descriptive, not a forecast). chance = share of eligible teammates in the OUT player's role (or position bucket). The uniform-random rate is the baseline for each.

## k = 6

### Centroids (raw units) and sizes

| cluster | name | pts | reb | ast | fg3sh | ftr | stl | blk | tov | usg | min | n_2023 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | bench disruptor (steals, 16 min) | 12.016 | 5.853 | 3.225 | 0.442 | 0.209 | 1.886 | 0.753 | 1.530 | 0.350 | 15.946 | 2971 | 3901 |
| 1 | starting two-way wing (mid usage) | 17.243 | 5.911 | 2.825 | 0.463 | 0.209 | 0.979 | 0.582 | 1.610 | 0.461 | 27.071 | 5815 | 5737 |
| 2 | spot-up shooter | 11.801 | 5.075 | 2.664 | 0.609 | 0.141 | 0.888 | 0.504 | 1.178 | 0.329 | 17.510 | 6256 | 5651 |
| 3 | rim big | 14.924 | 10.962 | 2.358 | 0.129 | 0.375 | 0.909 | 1.750 | 1.834 | 0.388 | 19.192 | 4594 | 4346 |
| 4 | high-usage scorer | 24.358 | 6.430 | 5.573 | 0.319 | 0.317 | 1.105 | 0.629 | 2.808 | 0.653 | 33.051 | 4423 | 4466 |
| 5 | playmaker | 15.267 | 5.018 | 6.872 | 0.381 | 0.208 | 1.260 | 0.455 | 2.391 | 0.453 | 23.676 | 3209 | 3198 |

### (a) Is the largest gainer in the OUT player's role?  (k=6; 1651 team-games with >=3 role-bearing teammates)

| sample | largest gainer is | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi |
|---|---|---|---|---|---|---|---|---|---|
| pooled | same ROLE | 1651 | 0.173 | 0.156 | 0.191 | 0.163 | 0.010 | -0.007 | 0.029 |
| pooled | same POSITION bucket (same rows) | 1651 | 0.428 | 0.405 | 0.451 | 0.381 | 0.047 | 0.027 | 0.069 |
| season 2023 | same ROLE | 818 | 0.205 | 0.178 | 0.232 | 0.170 | 0.035 | 0.011 | 0.061 |
| season 2023 | same POSITION bucket (same rows) | 818 | 0.445 | 0.413 | 0.479 | 0.378 | 0.067 | 0.034 | 0.097 |
| season 2024 | same ROLE | 833 | 0.140 | 0.123 | 0.165 | 0.155 | -0.015 | -0.036 | 0.009 |
| season 2024 | same POSITION bucket (same rows) | 833 | 0.412 | 0.378 | 0.448 | 0.385 | 0.027 | -0.007 | 0.062 |

Only team-games where at least one same-role teammate played (1482): n_teamgames=1482, rate=0.192, rate_lo=0.174, rate_hi=0.210, chance=0.181, diff=0.011, diff_lo=-0.006, diff_hi=0.035

Same-role rate by the OUT player's role (pooled):

| OUT role | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi | thin(n<100) |
|---|---|---|---|---|---|---|---|---|---|
| 0 bench disruptor (steals, 16 min) | 67 | 0.134 | 0.045 | 0.221 | 0.100 | 0.034 | -0.030 | 0.115 | thin |
| 1 starting two-way wing (mid usage) | 436 | 0.188 | 0.151 | 0.218 | 0.208 | -0.020 | -0.052 | 0.019 |  |
| 2 spot-up shooter | 153 | 0.327 | 0.257 | 0.399 | 0.265 | 0.061 | -0.004 | 0.135 |  |
| 3 rim big | 213 | 0.300 | 0.244 | 0.349 | 0.142 | 0.159 | 0.100 | 0.212 |  |
| 4 high-usage scorer | 609 | 0.082 | 0.062 | 0.103 | 0.129 | -0.047 | -0.066 | -0.027 |  |
| 5 playmaker | 173 | 0.173 | 0.127 | 0.227 | 0.127 | 0.046 | -0.016 | 0.103 |  |

### (c) Replacement = same-role teammate with the highest prior-10 minutes (k=6)

| sample | is the largest gainer | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi |
|---|---|---|---|---|---|---|---|---|---|
| pooled | named same-role replacement | 1482 | 0.076 | 0.063 | 0.089 | 0.097 | -0.021 | -0.032 | -0.009 |
| season 2023 | named same-role replacement | 737 | 0.099 | 0.078 | 0.120 | 0.099 | 0.000 | -0.019 | 0.022 |
| season 2024 | named same-role replacement | 745 | 0.054 | 0.038 | 0.071 | 0.096 | -0.042 | -0.056 | -0.023 |
| pooled | highest-prior-minutes teammate, ANY role (baseline) | 1482 | 0.018 | 0.011 | 0.026 | 0.097 | -0.079 | -0.086 | -0.073 |

### (b) Role-by-role: when a role-R player is OUT, teammates by THEIR role (k=6, pooled seasons)

gain = minutes - prior-10 mean (all role-bearing teammates who played); resid = pts actual - OOF mean_int (rows with a model residual). CI = game-clustered 95%. thin = n < 100.

| OUT role | teammate role | same | n | gain | gain_lo | gain_hi | n_resid | pts_resid | res_lo | res_hi | thin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | * | 73 | 1.550 | 0.077 | 3.362 | 73 | 0.742 | -0.484 | 2.000 | thin |
| 0 | 1 |  | 159 | -1.803 | -2.995 | -0.640 | 159 | -0.818 | -1.427 | -0.080 |  |
| 0 | 2 |  | 171 | -0.462 | -1.633 | 0.698 | 171 | -0.576 | -1.217 | 0.113 |  |
| 0 | 3 |  | 105 | -0.679 | -2.017 | 0.631 | 105 | -0.063 | -1.063 | 1.034 |  |
| 0 | 4 |  | 131 | -0.488 | -1.751 | 0.697 | 131 | -0.092 | -1.634 | 1.247 |  |
| 0 | 5 |  | 64 | -1.487 | -3.715 | 0.522 | 64 | -0.890 | -2.020 | 0.517 | thin |
| 1 | 0 |  | 576 | 0.508 | -0.100 | 1.205 | 576 | -0.185 | -0.556 | 0.238 |  |
| 1 | 1 | * | 922 | -0.489 | -0.960 | -0.077 | 922 | 0.424 | -0.006 | 0.812 |  |
| 1 | 2 |  | 883 | 0.122 | -0.320 | 0.612 | 883 | 0.088 | -0.164 | 0.390 |  |
| 1 | 3 |  | 794 | 0.381 | 0.010 | 0.901 | 794 | -0.082 | -0.467 | 0.268 |  |
| 1 | 4 |  | 753 | -0.729 | -1.107 | -0.171 | 753 | -0.227 | -0.765 | 0.290 |  |
| 1 | 5 |  | 573 | -0.074 | -0.516 | 0.394 | 573 | 0.088 | -0.298 | 0.490 |  |
| 2 | 0 |  | 138 | -0.053 | -1.060 | 1.288 | 138 | -0.320 | -1.030 | 0.406 |  |
| 2 | 1 |  | 216 | -0.603 | -1.597 | 0.410 | 216 | 0.024 | -0.856 | 0.779 |  |
| 2 | 2 | * | 428 | -0.300 | -1.173 | 0.590 | 428 | -0.205 | -0.778 | 0.361 |  |
| 2 | 3 |  | 296 | -0.405 | -1.090 | 0.360 | 296 | -0.563 | -1.183 | 0.028 |  |
| 2 | 4 |  | 292 | -0.461 | -1.262 | 0.285 | 292 | -0.398 | -1.111 | 0.550 |  |
| 2 | 5 |  | 229 | -0.092 | -0.982 | 0.717 | 229 | 0.212 | -0.403 | 0.885 |  |
| 3 | 0 |  | 310 | 0.283 | -0.472 | 0.944 | 310 | 0.256 | -0.327 | 0.691 |  |
| 3 | 1 |  | 436 | 0.076 | -0.457 | 0.728 | 436 | 0.300 | -0.247 | 0.898 |  |
| 3 | 2 |  | 502 | 0.293 | -0.262 | 0.771 | 502 | 0.216 | -0.152 | 0.639 |  |
| 3 | 3 | * | 316 | 1.350 | 0.575 | 2.153 | 316 | 0.346 | -0.129 | 0.777 |  |
| 3 | 4 |  | 417 | 0.005 | -0.622 | 0.576 | 417 | 0.826 | 0.034 | 1.582 |  |
| 3 | 5 |  | 253 | -0.756 | -1.530 | -0.060 | 253 | -0.431 | -1.087 | 0.173 |  |
| 4 | 0 |  | 970 | 0.374 | -0.118 | 0.952 | 970 | 0.059 | -0.256 | 0.352 |  |
| 4 | 1 |  | 1382 | -0.136 | -0.448 | 0.211 | 1382 | 0.133 | -0.224 | 0.464 |  |
| 4 | 2 |  | 1502 | -0.166 | -0.476 | 0.184 | 1502 | -0.389 | -0.649 | -0.151 |  |
| 4 | 3 |  | 1065 | -0.243 | -0.644 | 0.148 | 1065 | -0.318 | -0.561 | -0.055 |  |
| 4 | 4 | * | 821 | 0.428 | 0.014 | 0.740 | 821 | 1.044 | 0.591 | 1.593 |  |
| 4 | 5 |  | 693 | -0.248 | -0.858 | 0.255 | 693 | -0.090 | -0.497 | 0.421 |  |
| 5 | 0 |  | 159 | 0.647 | -0.320 | 1.700 | 159 | 0.203 | -0.612 | 0.957 |  |
| 5 | 1 |  | 456 | -0.849 | -1.439 | -0.165 | 456 | -0.667 | -1.128 | -0.140 |  |
| 5 | 2 |  | 393 | 0.521 | -0.286 | 1.440 | 393 | -0.047 | -0.581 | 0.467 |  |
| 5 | 3 |  | 267 | -0.426 | -1.355 | 0.490 | 267 | -0.262 | -0.811 | 0.251 |  |
| 5 | 4 |  | 314 | 0.181 | -0.442 | 0.853 | 314 | 1.430 | 0.525 | 2.436 |  |
| 5 | 5 | * | 228 | 0.388 | -0.277 | 1.165 | 228 | -0.608 | -1.200 | 0.065 |  |

Same-role teammate vs other-role teammate: gain diff 0.244 (CI -0.019 to 0.491); pts-resid diff 0.487 (CI 0.181 to 0.741).

## k = 8

### Centroids (raw units) and sizes

| cluster | name | pts | reb | ast | fg3sh | ftr | stl | blk | tov | usg | min | n_2023 | n_2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | starting 3-and-D wing | 15.319 | 5.288 | 2.736 | 0.505 | 0.186 | 1.008 | 0.589 | 1.432 | 0.409 | 27.088 | 5150 | 5268 |
| 1 | high-usage star creator | 25.751 | 6.880 | 6.676 | 0.306 | 0.342 | 1.168 | 0.659 | 3.200 | 0.691 | 33.722 | 2347 | 2489 |
| 2 | starting rim big | 15.723 | 11.634 | 2.374 | 0.109 | 0.349 | 0.979 | 2.049 | 1.913 | 0.405 | 21.909 | 3315 | 3259 |
| 3 | secondary scorer | 21.610 | 6.049 | 3.951 | 0.367 | 0.260 | 1.023 | 0.544 | 2.173 | 0.580 | 30.314 | 3843 | 3538 |
| 4 | playmaker | 15.004 | 5.014 | 6.968 | 0.382 | 0.202 | 1.263 | 0.443 | 2.405 | 0.447 | 23.388 | 2924 | 2956 |
| 5 | bench spot-up shooter | 11.572 | 5.098 | 2.651 | 0.647 | 0.119 | 0.893 | 0.492 | 1.140 | 0.325 | 16.224 | 4716 | 4332 |
| 6 | bench big (13 min) | 13.069 | 8.107 | 2.488 | 0.265 | 0.389 | 0.830 | 0.850 | 1.591 | 0.360 | 13.154 | 2534 | 2065 |
| 7 | bench disruptor (steals) | 11.931 | 5.738 | 3.377 | 0.453 | 0.188 | 1.987 | 0.750 | 1.553 | 0.352 | 15.902 | 2439 | 3392 |

### (a) Is the largest gainer in the OUT player's role?  (k=8; 1651 team-games with >=3 role-bearing teammates)

| sample | largest gainer is | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi |
|---|---|---|---|---|---|---|---|---|---|
| pooled | same ROLE | 1651 | 0.115 | 0.102 | 0.127 | 0.113 | 0.002 | -0.013 | 0.018 |
| pooled | same POSITION bucket (same rows) | 1651 | 0.428 | 0.406 | 0.455 | 0.381 | 0.047 | 0.023 | 0.072 |
| season 2023 | same ROLE | 818 | 0.149 | 0.127 | 0.173 | 0.129 | 0.020 | -0.000 | 0.039 |
| season 2023 | same POSITION bucket (same rows) | 818 | 0.445 | 0.411 | 0.478 | 0.378 | 0.067 | 0.033 | 0.106 |
| season 2024 | same ROLE | 833 | 0.082 | 0.066 | 0.098 | 0.097 | -0.016 | -0.033 | 0.004 |
| season 2024 | same POSITION bucket (same rows) | 833 | 0.412 | 0.381 | 0.442 | 0.385 | 0.027 | 0.000 | 0.065 |

Only team-games where at least one same-role teammate played (1232): n_teamgames=1232, rate=0.154, rate_lo=0.133, rate_hi=0.179, chance=0.152, diff=0.003, diff_lo=-0.016, diff_hi=0.021

Same-role rate by the OUT player's role (pooled):

| OUT role | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi | thin(n<100) |
|---|---|---|---|---|---|---|---|---|---|
| 0 starting 3-and-D wing | 316 | 0.120 | 0.085 | 0.157 | 0.133 | -0.013 | -0.043 | 0.017 |  |
| 1 high-usage star creator | 348 | 0.026 | 0.012 | 0.043 | 0.080 | -0.054 | -0.069 | -0.033 |  |
| 2 starting rim big | 212 | 0.175 | 0.133 | 0.224 | 0.083 | 0.092 | 0.051 | 0.132 |  |
| 3 secondary scorer | 463 | 0.108 | 0.083 | 0.133 | 0.125 | -0.017 | -0.039 | 0.009 |  |
| 4 playmaker | 164 | 0.146 | 0.085 | 0.203 | 0.111 | 0.035 | -0.011 | 0.085 |  |
| 5 bench spot-up shooter | 62 | 0.339 | 0.226 | 0.468 | 0.243 | 0.095 | -0.013 | 0.201 | thin |
| 6 bench big (13 min) | 18 | 0.167 | 0.000 | 0.333 | 0.150 | 0.017 | -0.127 | 0.197 | thin |
| 7 bench disruptor (steals) | 68 | 0.118 | 0.043 | 0.217 | 0.083 | 0.035 | -0.030 | 0.128 | thin |

### (c) Replacement = same-role teammate with the highest prior-10 minutes (k=8)

| sample | is the largest gainer | n_teamgames | rate | rate_lo | rate_hi | chance | diff | diff_lo | diff_hi |
|---|---|---|---|---|---|---|---|---|---|
| pooled | named same-role replacement | 1232 | 0.087 | 0.074 | 0.101 | 0.097 | -0.010 | -0.025 | 0.005 |
| season 2023 | named same-role replacement | 621 | 0.098 | 0.076 | 0.122 | 0.099 | -0.000 | -0.024 | 0.019 |
| season 2024 | named same-role replacement | 611 | 0.075 | 0.056 | 0.096 | 0.096 | -0.020 | -0.039 | 0.002 |
| pooled | highest-prior-minutes teammate, ANY role (baseline) | 1232 | 0.019 | 0.012 | 0.028 | 0.097 | -0.078 | -0.085 | -0.069 |

### (b) Role-by-role: when a role-R player is OUT, teammates by THEIR role (k=8, pooled seasons)

gain = minutes - prior-10 mean (all role-bearing teammates who played); resid = pts actual - OOF mean_int (rows with a model residual). CI = game-clustered 95%. thin = n < 100.

| OUT role | teammate role | same | n | gain | gain_lo | gain_hi | n_resid | pts_resid | res_lo | res_hi | thin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | * | 427 | -0.909 | -1.546 | -0.255 | 427 | 0.059 | -0.547 | 0.637 |  |
| 0 | 1 |  | 272 | -0.651 | -1.398 | -0.074 | 272 | -0.074 | -0.964 | 0.888 |  |
| 0 | 2 |  | 446 | -0.211 | -0.842 | 0.339 | 446 | -0.231 | -0.738 | 0.227 |  |
| 0 | 3 |  | 495 | -0.287 | -0.871 | 0.235 | 495 | 0.169 | -0.486 | 0.799 |  |
| 0 | 4 |  | 431 | -0.328 | -0.941 | 0.221 | 431 | -0.048 | -0.559 | 0.508 |  |
| 0 | 5 |  | 550 | 0.479 | -0.155 | 1.049 | 550 | 0.113 | -0.278 | 0.428 |  |
| 0 | 6 |  | 312 | 0.557 | -0.187 | 1.461 | 312 | 0.016 | -0.520 | 0.586 |  |
| 0 | 7 |  | 337 | 0.510 | -0.263 | 1.141 | 337 | 0.165 | -0.259 | 0.657 |  |
| 1 | 0 |  | 795 | -0.193 | -0.675 | 0.265 | 795 | -0.168 | -0.561 | 0.256 |  |
| 1 | 1 | * | 288 | 0.348 | -0.215 | 0.879 | 288 | 1.278 | 0.380 | 2.073 |  |
| 1 | 2 |  | 483 | -0.170 | -0.706 | 0.408 | 483 | -0.315 | -0.649 | 0.153 |  |
| 1 | 3 |  | 382 | 0.445 | -0.163 | 1.112 | 382 | 0.218 | -0.506 | 0.940 |  |
| 1 | 4 |  | 334 | -0.227 | -0.828 | 0.381 | 334 | -0.258 | -0.851 | 0.267 |  |
| 1 | 5 |  | 649 | -0.051 | -0.595 | 0.577 | 649 | -0.638 | -0.957 | -0.305 |  |
| 1 | 6 |  | 268 | -0.076 | -0.984 | 0.705 | 268 | -0.498 | -0.969 | 0.014 |  |
| 1 | 7 |  | 460 | -0.135 | -0.774 | 0.534 | 460 | -0.026 | -0.411 | 0.338 |  |
| 2 | 0 |  | 440 | 0.068 | -0.488 | 0.638 | 440 | 0.499 | 0.046 | 1.088 |  |
| 2 | 1 |  | 231 | 0.215 | -0.541 | 0.871 | 231 | 1.164 | 0.018 | 2.252 |  |
| 2 | 2 | * | 183 | 1.779 | 0.898 | 2.701 | 183 | 0.569 | -0.191 | 1.397 |  |
| 2 | 3 |  | 325 | 0.231 | -0.420 | 0.997 | 325 | 0.624 | -0.155 | 1.238 |  |
| 2 | 4 |  | 223 | -1.308 | -2.014 | -0.674 | 223 | -0.978 | -1.640 | -0.145 |  |
| 2 | 5 |  | 377 | 0.212 | -0.445 | 0.865 | 377 | 0.168 | -0.270 | 0.629 |  |
| 2 | 6 |  | 187 | 1.558 | 0.576 | 2.600 | 187 | 0.086 | -0.559 | 0.741 |  |
| 2 | 7 |  | 259 | 0.277 | -0.675 | 1.228 | 259 | 0.123 | -0.330 | 0.628 |  |
| 3 | 0 |  | 1059 | -0.535 | -0.911 | -0.070 | 1059 | 0.378 | -0.009 | 0.774 |  |
| 3 | 1 |  | 383 | -0.134 | -0.668 | 0.392 | 383 | 0.520 | -0.238 | 1.530 |  |
| 3 | 2 |  | 602 | -0.188 | -0.654 | 0.328 | 602 | -0.195 | -0.630 | 0.166 |  |
| 3 | 3 | * | 587 | -0.081 | -0.715 | 0.482 | 587 | 0.168 | -0.507 | 0.802 |  |
| 3 | 4 |  | 519 | 0.084 | -0.510 | 0.714 | 519 | 0.187 | -0.274 | 0.670 |  |
| 3 | 5 |  | 738 | -0.102 | -0.667 | 0.446 | 738 | -0.164 | -0.457 | 0.136 |  |
| 3 | 6 |  | 379 | -0.064 | -0.829 | 0.890 | 379 | -0.569 | -1.021 | -0.117 |  |
| 3 | 7 |  | 577 | 0.537 | -0.147 | 1.089 | 577 | -0.114 | -0.480 | 0.268 |  |
| 4 | 0 |  | 310 | -0.995 | -1.672 | -0.256 | 310 | -0.827 | -1.513 | -0.266 |  |
| 4 | 1 |  | 156 | -0.058 | -0.839 | 0.956 | 156 | 0.455 | -1.068 | 1.737 |  |
| 4 | 2 |  | 182 | 0.137 | -0.776 | 1.205 | 182 | 0.268 | -0.281 | 0.893 |  |
| 4 | 3 |  | 301 | -0.289 | -0.989 | 0.326 | 301 | 0.694 | -0.228 | 1.481 |  |
| 4 | 4 | * | 189 | -0.023 | -1.098 | 0.845 | 189 | -0.742 | -1.540 | -0.058 |  |
| 4 | 5 |  | 319 | 0.183 | -0.649 | 1.009 | 319 | -0.141 | -0.598 | 0.463 |  |
| 4 | 6 |  | 142 | 0.169 | -1.073 | 1.089 | 142 | -0.427 | -1.099 | 0.225 |  |
| 4 | 7 |  | 126 | 0.566 | -0.674 | 2.215 | 126 | 0.123 | -0.754 | 1.153 |  |
| 5 | 0 |  | 80 | -0.233 | -2.076 | 1.251 | 80 | 0.242 | -1.051 | 1.567 | thin |
| 5 | 1 |  | 89 | -0.552 | -1.954 | 0.959 | 89 | -0.856 | -2.438 | 0.659 | thin |
| 5 | 2 |  | 86 | -0.662 | -1.888 | 0.917 | 86 | -0.917 | -2.112 | 0.290 | thin |
| 5 | 3 |  | 74 | -0.304 | -1.632 | 1.195 | 74 | -0.427 | -2.052 | 1.207 | thin |
| 5 | 4 |  | 64 | 0.237 | -1.262 | 1.846 | 64 | 0.195 | -1.020 | 1.561 | thin |
| 5 | 5 | * | 163 | 0.106 | -1.207 | 1.459 | 163 | -0.001 | -0.772 | 0.933 |  |
| 5 | 6 |  | 57 | 1.121 | -0.788 | 3.111 | 57 | 0.901 | -0.355 | 2.211 | thin |
| 5 | 7 |  | 51 | 0.783 | -1.420 | 3.577 | 51 | -0.691 | -1.777 | 0.790 | thin |
| 6 | 0 |  | 47 | 1.568 | 0.334 | 2.876 | 47 | 2.390 | 0.799 | 3.988 | thin |
| 6 | 1 |  | 6 | 0.599 | -3.152 | 4.073 | 6 | 6.760 | -0.667 | 13.438 | thin |
| 6 | 2 |  | 18 | 0.011 | -3.082 | 3.316 | 18 | -0.379 | -2.125 | 1.690 | thin |
| 6 | 3 |  | 25 | 0.345 | -1.633 | 2.327 | 25 | 1.425 | -1.482 | 4.168 | thin |
| 6 | 4 |  | 41 | 1.006 | -0.003 | 2.071 | 41 | 1.256 | -0.027 | 2.398 | thin |
| 6 | 5 |  | 11 | 3.372 | -1.060 | 11.483 | 11 | 0.424 | -1.313 | 2.933 | thin |
| 6 | 6 | * | 28 | -2.185 | -4.005 | -0.559 | 28 | 0.603 | -0.811 | 2.171 | thin |
| 6 | 7 |  | 8 | -0.734 | -4.900 | 5.322 | 8 | -0.558 | -3.067 | 1.868 | thin |
| 7 | 0 |  | 161 | -2.416 | -3.528 | -1.293 | 161 | -1.039 | -1.991 | -0.070 |  |
| 7 | 1 |  | 60 | -0.179 | -1.497 | 1.078 | 60 | 0.725 | -1.235 | 3.182 | thin |
| 7 | 2 |  | 79 | -0.834 | -2.255 | 0.837 | 79 | 0.260 | -1.013 | 1.682 | thin |
| 7 | 3 |  | 110 | -0.853 | -2.006 | 0.115 | 110 | -0.287 | -1.811 | 1.649 |  |
| 7 | 4 |  | 62 | -1.624 | -3.423 | 0.605 | 62 | -1.007 | -2.418 | 0.129 | thin |
| 7 | 5 |  | 113 | 0.132 | -1.319 | 1.768 | 113 | -0.411 | -1.184 | 0.417 |  |
| 7 | 6 |  | 69 | -0.176 | -1.492 | 1.253 | 69 | -0.660 | -1.531 | 0.415 | thin |
| 7 | 7 | * | 62 | 2.023 | -0.069 | 3.987 | 62 | 0.884 | -0.286 | 2.142 | thin |

Same-role teammate vs other-role teammate: gain diff 0.108 (CI -0.185 to 0.423); pts-resid diff 0.289 (CI -0.007 to 0.600).


## Reading (DESCRIPTIVE ONLY)

1. Role does not beat position as a pre-tip identifier here. Largest gainer shares the OUT player's ROLE 17.3% (chance 16.3%, diff +1.0 pts, CI -0.7 to +2.9) at k=6 and 11.5% (chance 11.3%, diff +0.2, CI -1.3 to +1.8) at k=8, pooled over 1,651 team-games. Position bucket on the same rows: 42.8% vs 38.1% chance (diff +4.7, CI +2.7 to +6.9).
2. Role rates are not "higher than 43%" because there are more roles than position buckets; compare the difference over chance, not the raw rate. Raw rate and difference are both small for role.
3. Season split: k=6 role diff +3.5 pts in 2023 (CI +1.1 to +6.1) and -1.5 pts in 2024 (CI -3.6 to +0.9); the k=8 clusters show the same sign flip (+2.0 / -1.6). Not stable across seasons (the 2024 clusters are assigned out of sample, so some drift is expected).
4. The best identifier is below 55% and within 10 points of chance in every cut: not a usable pre-tip rule. Neither role nor position names the minutes absorber.
5. Pockets (k=8, OUT role): starting rim big out, +9.2 pts over chance (17.5% vs 8.3%, n=212 team-games, CI +5.1 to +13.2); high-usage star out, the gainer is LESS often a star-creator (2.6% vs 8.0%): stars are replaced by lesser roles. Many cells, no multiplicity correction; treat as hypothesis-grade at most, and still far below 55%.
6. Named same-role replacement (highest prior-10 minutes in the role) is the top gainer 7.6% (k=6) / 8.7% (k=8) vs ~9.7% chance, i.e. at or below chance; the highest-minutes teammate of any role is the top gainer only 1.9% (high-minute starters have little room to gain).
7. Role-by-role cells (b): most gain cells have CIs spanning 0 with |gain| under ~1.5 min; the only broad pattern is that the same-role teammate gains about +0.1 to +0.2 min more than other-role teammates (diff CI crosses 0 at both k) and beats the pts model by about +0.3 to +0.5 pts more (k=6 CI +0.18 to +0.74; k=8 CI -0.01 to +0.60). Small, selected on nothing, but not a minutes story.
8. Cells with n < 100 are marked thin in the tables; bench-role OUT rows (k=8 roles 5-7) are mostly thin.
9. Clusters are k-means on 10 box-score-rate features; the names are my reading of the centroids and are approximate (role 1 at k=6 is a catch-all "starting wing"). Teammates with fewer than 8 prior games have no role and are excluded.
10. Caveat of kind: "largest gainer" is chosen on actual minutes after the game, so even a perfect identifier would only describe, not forecast; the lever remains the T-30 lineup. Devin's point may still hold for how coaches think, but these 10 box-score features do not recover it as a pre-tip signal.
