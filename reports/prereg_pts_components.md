# PTS_COMPONENTS: results (docs/prereg/PTS_COMPONENTS.md)

Frozen sha256 `7e6fd8f019985032b712b71fb275f4bf6841862a09e8850dfd1c11913cbfafc8` (first 8 `7e6fd8f0`, prefix ok: True). git SHA at run `3a729d73f252dba327bbaba72139125d192f280f`. Seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}. CPU. Seasons <= 2024 only (max loaded 2024); nba.duckdb read-only.

## Checks `[test, ok]`

| test | value | threshold | ok |
|---|---|---|---|
| C1 six box columns non-null (played 2022-2024) | 1 | >= 0.999 | ok |
| C1 pts == 2 fg2m + 3 fg3m + ftm | 1 | >= 0.999 | ok |
| C1 rows dropped from all arms (count) | 0 | reported | ok |
| C2 max NULL-rate gap across minutes buckets (pp) | 0.0711448 | <= 2.0 | ok |
| C3 PLANT same-game box edited: component features identical through the day | 1 | == 1 | ok |
| C3 PLANT same-game is not inert (later games change) | 1 | == 1 | ok |
| C3 PLANT future (>= 2024-01-15) box edited: earlier features unchanged | 1 | == 1 | ok |
| C3 PLANT future is not inert (later games change) | 1 | == 1 | ok |
| C4a nba/props/context_residual.py and lower_tail.py unchanged vs HEAD | 1 | == 1 | ok |
| C5 grid sums to 1 (max abs deviation, all scored rows, fixed params) | 3.73035e-14 | <= 1e-09 | ok |
| C4 A0 reproduces production integer CRPS to 1e-6 (n and crps_int) | 1 | == 1 | ok |
| C5 fitted grids sum to 1 (max abs deviation over scored rows) | 2.21326e-11 | <= 1e-09 | ok |

Missingness audit `[col, bucket, null_rate]`:

| col | bucket | null_rate | n |
|---|---|---|---|
| m10_fg2a | <5 | 0.00000 | 6435 |
| m10_fg2a | 5-10 | 0.00000 | 5706 |
| m10_fg2a | 10-20 | 0.00000 | 18566 |
| m10_fg2a | >20 | 0.00000 | 49605 |
| m5_fg2a | <5 | 0.00000 | 6435 |
| m5_fg2a | 5-10 | 0.00000 | 5706 |
| m5_fg2a | 10-20 | 0.00000 | 18566 |
| m5_fg2a | >20 | 0.00000 | 49605 |
| m40_fg2a | <5 | 0.00000 | 6435 |
| m40_fg2a | 5-10 | 0.00000 | 5706 |
| m40_fg2a | 10-20 | 0.00000 | 18566 |
| m40_fg2a | >20 | 0.00000 | 49605 |
| m100_fg2a | <5 | 0.00000 | 6435 |
| m100_fg2a | 5-10 | 0.00000 | 5706 |
| m100_fg2a | 10-20 | 0.00000 | 18566 |
| m100_fg2a | >20 | 0.00000 | 49605 |
| sd10_fg2a | <5 | 0.00000 | 6435 |
| sd10_fg2a | 5-10 | 0.00000 | 5706 |
| sd10_fg2a | 10-20 | 0.00000 | 18566 |
| sd10_fg2a | >20 | 0.00000 | 49605 |
| rate_fg2a | <5 | 0.00000 | 6435 |
| rate_fg2a | 5-10 | 0.00000 | 5706 |
| rate_fg2a | 10-20 | 0.00000 | 18566 |
| rate_fg2a | >20 | 0.00000 | 49605 |
| opp_allow_fg2a | <5 | 0.00016 | 6435 |
| opp_allow_fg2a | 5-10 | 0.00018 | 5706 |
| opp_allow_fg2a | 10-20 | 0.00086 | 18566 |
| opp_allow_fg2a | >20 | 0.00087 | 49605 |
| vac_fg2a | <5 | 0.00124 | 6435 |
| vac_fg2a | 5-10 | 0.00140 | 5706 |
| vac_fg2a | 10-20 | 0.00086 | 18566 |
| vac_fg2a | >20 | 0.00099 | 49605 |
| m10_fg3a | <5 | 0.00000 | 6435 |
| m10_fg3a | 5-10 | 0.00000 | 5706 |
| m10_fg3a | 10-20 | 0.00000 | 18566 |
| m10_fg3a | >20 | 0.00000 | 49605 |
| m5_fg3a | <5 | 0.00000 | 6435 |
| m5_fg3a | 5-10 | 0.00000 | 5706 |
| m5_fg3a | 10-20 | 0.00000 | 18566 |
| m5_fg3a | >20 | 0.00000 | 49605 |
| m40_fg3a | <5 | 0.00000 | 6435 |
| m40_fg3a | 5-10 | 0.00000 | 5706 |
| m40_fg3a | 10-20 | 0.00000 | 18566 |
| m40_fg3a | >20 | 0.00000 | 49605 |
| m100_fg3a | <5 | 0.00000 | 6435 |
| m100_fg3a | 5-10 | 0.00000 | 5706 |
| m100_fg3a | 10-20 | 0.00000 | 18566 |
| m100_fg3a | >20 | 0.00000 | 49605 |
| sd10_fg3a | <5 | 0.00000 | 6435 |
| sd10_fg3a | 5-10 | 0.00000 | 5706 |
| sd10_fg3a | 10-20 | 0.00000 | 18566 |
| sd10_fg3a | >20 | 0.00000 | 49605 |
| rate_fg3a | <5 | 0.00000 | 6435 |
| rate_fg3a | 5-10 | 0.00000 | 5706 |
| rate_fg3a | 10-20 | 0.00000 | 18566 |
| rate_fg3a | >20 | 0.00000 | 49605 |
| opp_allow_fg3a | <5 | 0.00016 | 6435 |
| opp_allow_fg3a | 5-10 | 0.00018 | 5706 |
| opp_allow_fg3a | 10-20 | 0.00086 | 18566 |
| opp_allow_fg3a | >20 | 0.00087 | 49605 |
| vac_fg3a | <5 | 0.00124 | 6435 |
| vac_fg3a | 5-10 | 0.00140 | 5706 |
| vac_fg3a | 10-20 | 0.00086 | 18566 |
| vac_fg3a | >20 | 0.00099 | 49605 |
| m10_fta | <5 | 0.00000 | 6435 |
| m10_fta | 5-10 | 0.00000 | 5706 |
| m10_fta | 10-20 | 0.00000 | 18566 |
| m10_fta | >20 | 0.00000 | 49605 |
| m5_fta | <5 | 0.00000 | 6435 |
| m5_fta | 5-10 | 0.00000 | 5706 |
| m5_fta | 10-20 | 0.00000 | 18566 |
| m5_fta | >20 | 0.00000 | 49605 |
| m40_fta | <5 | 0.00000 | 6435 |
| m40_fta | 5-10 | 0.00000 | 5706 |
| m40_fta | 10-20 | 0.00000 | 18566 |
| m40_fta | >20 | 0.00000 | 49605 |
| m100_fta | <5 | 0.00000 | 6435 |
| m100_fta | 5-10 | 0.00000 | 5706 |
| m100_fta | 10-20 | 0.00000 | 18566 |
| m100_fta | >20 | 0.00000 | 49605 |
| sd10_fta | <5 | 0.00000 | 6435 |
| sd10_fta | 5-10 | 0.00000 | 5706 |
| sd10_fta | 10-20 | 0.00000 | 18566 |
| sd10_fta | >20 | 0.00000 | 49605 |
| rate_fta | <5 | 0.00000 | 6435 |
| rate_fta | 5-10 | 0.00000 | 5706 |
| rate_fta | 10-20 | 0.00000 | 18566 |
| rate_fta | >20 | 0.00000 | 49605 |
| opp_allow_fta | <5 | 0.00016 | 6435 |
| opp_allow_fta | 5-10 | 0.00018 | 5706 |
| opp_allow_fta | 10-20 | 0.00086 | 18566 |
| opp_allow_fta | >20 | 0.00087 | 49605 |
| vac_fta | <5 | 0.00124 | 6435 |
| vac_fta | 5-10 | 0.00140 | 5706 |
| vac_fta | 10-20 | 0.00086 | 18566 |
| vac_fta | >20 | 0.00099 | 49605 |
| pr_fg2 | <5 | 0.00000 | 6435 |
| pr_fg2 | 5-10 | 0.00000 | 5706 |
| pr_fg2 | 10-20 | 0.00000 | 18566 |
| pr_fg2 | >20 | 0.00000 | 49605 |
| pr_fg3 | <5 | 0.00000 | 6435 |
| pr_fg3 | 5-10 | 0.00000 | 5706 |
| pr_fg3 | 10-20 | 0.00000 | 18566 |
| pr_fg3 | >20 | 0.00000 | 49605 |
| pr_ft | <5 | 0.00000 | 6435 |
| pr_ft | 5-10 | 0.00000 | 5706 |
| pr_ft | 10-20 | 0.00000 | 18566 |
| pr_ft | >20 | 0.00000 | 49605 |

## A0 reproduction

```
{
 "ok": true,
 "detail": {
  "pts_2023": {
   "got": 3.152829990762183,
   "stored": 3.152829990762183,
   "delta": 0.0,
   "n": 27619,
   "n_ref": 27619,
   "ok": true
  },
  "pts_2024": {
   "got": 3.1884109851549947,
   "stored": 3.1884109851549947,
   "delta": 0.0,
   "n": 27583,
   "n_ref": 27583,
   "ok": true
  }
 }
}
```

## Results `[season, arm, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p, mde, bias, cov80, pit_q80, pit_q90, tll20, tll25, tll30]`

dcrps = arm - A0, integer support, game-clustered 95% CI, 2000 resamples, seed 0. One primary test per season (pts), p_bh = p.

| season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | mde | bias | cov80 | pit_q80 | pit_q90 | tll20 | tll25 | tll30 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | A0 | 27619 | 1318 | 3.1528 |  |  |  |  |  | -0.149 | 0.772 | 0.804 | 0.904 | 0.2738 | 0.1791 | 0.1104 |
| 2023 | A1 | 27619 | 1318 | 3.1857 | +0.0329 | +0.0261 | +0.0397 | 0.000 | 0.0068 | -0.424 | 0.777 | 0.822 | 0.912 | 0.2752 | 0.1805 | 0.1124 |
| 2023 | A2 | 27619 | 1318 | 3.1798 | +0.0270 | +0.0201 | +0.0339 | 0.000 | 0.0069 | -0.433 | 0.740 | 0.811 | 0.897 | 0.2741 | 0.1787 | 0.1100 |
| 2023 | A3 | 27619 | 1318 | 3.1517 | -0.0011 | -0.0048 | +0.0027 | 0.593 | 0.0038 | -0.153 | 0.774 | 0.805 | 0.905 | 0.2732 | 0.1785 | 0.1099 |
| 2023 | A4 | 27619 | 1318 | 3.3691 | +0.2163 | +0.2023 | +0.2307 | 0.000 | 0.0142 | -0.199 | 0.789 | 0.820 | 0.919 | 0.2888 | 0.1877 | 0.1153 |
| 2024 | A0 | 27583 | 1315 | 3.1884 |  |  |  |  |  | -0.113 | 0.764 | 0.800 | 0.898 | 0.2803 | 0.1797 | 0.1053 |
| 2024 | A1 | 27583 | 1315 | 3.2184 | +0.0300 | +0.0239 | +0.0361 | 0.000 | 0.0061 | -0.191 | 0.771 | 0.803 | 0.903 | 0.2832 | 0.1818 | 0.1069 |
| 2024 | A2 | 27583 | 1315 | 3.2162 | +0.0278 | +0.0217 | +0.0342 | 0.000 | 0.0062 | -0.196 | 0.735 | 0.791 | 0.886 | 0.2831 | 0.1812 | 0.1056 |
| 2024 | A3 | 27583 | 1315 | 3.1896 | +0.0012 | -0.0016 | +0.0040 | 0.432 | 0.0028 | -0.109 | 0.764 | 0.800 | 0.899 | 0.2799 | 0.1799 | 0.1056 |
| 2024 | A4 | 27583 | 1315 | 3.4179 | +0.2295 | +0.2141 | +0.2452 | 0.000 | 0.0155 | +0.003 | 0.784 | 0.802 | 0.908 | 0.2963 | 0.1893 | 0.1114 |

## Slices `[season, slice, arm, n, dcrps, ci_lo, ci_hi]` (arm - A0; CI only for n >= 300; the 3pt_share terciles are the mechanism table)

| season | slice | arm | n | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|
| 2023 | all | A1 | 27619 | +0.0329 | +0.0261 | +0.0397 |
| 2023 | all | A2 | 27619 | +0.0270 | +0.0201 | +0.0339 |
| 2023 | all | A3 | 27619 | -0.0011 | -0.0048 | +0.0027 |
| 2023 | all | A4 | 27619 | +0.2163 | +0.2023 | +0.2307 |
| 2023 | tier_<10 | A1 | 14987 | +0.0352 | +0.0282 | +0.0421 |
| 2023 | tier_<10 | A2 | 14987 | +0.0527 | +0.0450 | +0.0604 |
| 2023 | tier_<10 | A3 | 14987 | +0.0009 | -0.0030 | +0.0047 |
| 2023 | tier_<10 | A4 | 14987 | +0.2199 | +0.2009 | +0.2386 |
| 2023 | tier_10-17 | A1 | 7366 | -0.0067 | -0.0202 | +0.0073 |
| 2023 | tier_10-17 | A2 | 7366 | -0.0056 | -0.0206 | +0.0103 |
| 2023 | tier_10-17 | A3 | 7366 | -0.0026 | -0.0101 | +0.0051 |
| 2023 | tier_10-17 | A4 | 7366 | +0.1423 | +0.1157 | +0.1686 |
| 2023 | tier_17-24 | A1 | 3536 | +0.0409 | +0.0115 | +0.0676 |
| 2023 | tier_17-24 | A2 | 3536 | -0.0089 | -0.0388 | +0.0180 |
| 2023 | tier_17-24 | A3 | 3536 | -0.0100 | -0.0246 | +0.0051 |
| 2023 | tier_17-24 | A4 | 3536 | +0.2617 | +0.2149 | +0.3072 |
| 2023 | tier_24+ | A1 | 1730 | +0.1650 | +0.1162 | +0.2111 |
| 2023 | tier_24+ | A2 | 1730 | +0.0160 | -0.0297 | +0.0594 |
| 2023 | tier_24+ | A3 | 1730 | +0.0061 | -0.0196 | +0.0298 |
| 2023 | tier_24+ | A4 | 1730 | +0.4069 | +0.3346 | +0.4770 |
| 2023 | 3pt_share_T1 | A1 | 9207 | +0.0480 | +0.0360 | +0.0601 |
| 2023 | 3pt_share_T1 | A2 | 9207 | +0.0288 | +0.0170 | +0.0404 |
| 2023 | 3pt_share_T1 | A3 | 9207 | -0.0032 | -0.0093 | +0.0033 |
| 2023 | 3pt_share_T1 | A4 | 9207 | +0.2191 | +0.1958 | +0.2429 |
| 2023 | 3pt_share_T2 | A1 | 9206 | +0.0321 | +0.0188 | +0.0459 |
| 2023 | 3pt_share_T2 | A2 | 9206 | +0.0211 | +0.0074 | +0.0352 |
| 2023 | 3pt_share_T2 | A3 | 9206 | +0.0008 | -0.0060 | +0.0078 |
| 2023 | 3pt_share_T2 | A4 | 9206 | +0.2273 | +0.2017 | +0.2522 |
| 2023 | 3pt_share_T3 | A1 | 9206 | +0.0186 | +0.0078 | +0.0295 |
| 2023 | 3pt_share_T3 | A2 | 9206 | +0.0311 | +0.0193 | +0.0434 |
| 2023 | 3pt_share_T3 | A3 | 9206 | -0.0009 | -0.0069 | +0.0054 |
| 2023 | 3pt_share_T3 | A4 | 9206 | +0.2024 | +0.1792 | +0.2258 |
| 2023 | starter | A1 | 12267 | +0.0362 | +0.0227 | +0.0492 |
| 2023 | starter | A2 | 12267 | +0.0021 | -0.0107 | +0.0156 |
| 2023 | starter | A3 | 12267 | -0.0041 | -0.0110 | +0.0031 |
| 2023 | starter | A4 | 12267 | +0.2090 | +0.1862 | +0.2309 |
| 2023 | bench | A1 | 15352 | +0.0302 | +0.0235 | +0.0374 |
| 2023 | bench | A2 | 15352 | +0.0469 | +0.0392 | +0.0552 |
| 2023 | bench | A3 | 15352 | +0.0013 | -0.0025 | +0.0048 |
| 2023 | bench | A4 | 15352 | +0.2221 | +0.2034 | +0.2420 |
| 2023 | cold_start_n_prior<20 | A1 | 1630 | +0.0595 | +0.0351 | +0.0842 |
| 2023 | cold_start_n_prior<20 | A2 | 1630 | +0.0794 | +0.0519 | +0.1065 |
| 2023 | cold_start_n_prior<20 | A3 | 1630 | -0.0036 | -0.0182 | +0.0111 |
| 2023 | cold_start_n_prior<20 | A4 | 1630 | +0.2017 | +0.1488 | +0.2578 |
| 2024 | all | A1 | 27583 | +0.0300 | +0.0239 | +0.0361 |
| 2024 | all | A2 | 27583 | +0.0278 | +0.0217 | +0.0342 |
| 2024 | all | A3 | 27583 | +0.0012 | -0.0016 | +0.0040 |
| 2024 | all | A4 | 27583 | +0.2295 | +0.2141 | +0.2452 |
| 2024 | tier_<10 | A1 | 15363 | +0.0197 | +0.0129 | +0.0268 |
| 2024 | tier_<10 | A2 | 15363 | +0.0340 | +0.0267 | +0.0417 |
| 2024 | tier_<10 | A3 | 15363 | +0.0006 | -0.0027 | +0.0040 |
| 2024 | tier_<10 | A4 | 15363 | +0.2175 | +0.1977 | +0.2384 |
| 2024 | tier_10-17 | A1 | 7146 | +0.0094 | -0.0024 | +0.0207 |
| 2024 | tier_10-17 | A2 | 7146 | +0.0163 | +0.0032 | +0.0290 |
| 2024 | tier_10-17 | A3 | 7146 | +0.0000 | -0.0060 | +0.0057 |
| 2024 | tier_10-17 | A4 | 7146 | +0.1721 | +0.1428 | +0.2051 |
| 2024 | tier_17-24 | A1 | 3571 | +0.0596 | +0.0363 | +0.0811 |
| 2024 | tier_17-24 | A2 | 3571 | +0.0171 | -0.0057 | +0.0378 |
| 2024 | tier_17-24 | A3 | 3571 | +0.0113 | +0.0006 | +0.0219 |
| 2024 | tier_17-24 | A4 | 3571 | +0.3090 | +0.2666 | +0.3517 |
| 2024 | tier_24+ | A1 | 1503 | +0.1617 | +0.1132 | +0.2073 |
| 2024 | tier_24+ | A2 | 1503 | +0.0449 | +0.0054 | +0.0837 |
| 2024 | tier_24+ | A3 | 1503 | -0.0110 | -0.0336 | +0.0109 |
| 2024 | tier_24+ | A4 | 1503 | +0.4363 | +0.3659 | +0.5031 |
| 2024 | 3pt_share_T1 | A1 | 9195 | +0.0366 | +0.0259 | +0.0475 |
| 2024 | 3pt_share_T1 | A2 | 9195 | +0.0194 | +0.0093 | +0.0293 |
| 2024 | 3pt_share_T1 | A3 | 9195 | -0.0018 | -0.0068 | +0.0032 |
| 2024 | 3pt_share_T1 | A4 | 9195 | +0.2304 | +0.2074 | +0.2540 |
| 2024 | 3pt_share_T2 | A1 | 9194 | +0.0403 | +0.0281 | +0.0518 |
| 2024 | 3pt_share_T2 | A2 | 9194 | +0.0413 | +0.0288 | +0.0536 |
| 2024 | 3pt_share_T2 | A3 | 9194 | +0.0051 | -0.0002 | +0.0103 |
| 2024 | 3pt_share_T2 | A4 | 9194 | +0.2554 | +0.2251 | +0.2848 |
| 2024 | 3pt_share_T3 | A1 | 9194 | +0.0130 | +0.0037 | +0.0225 |
| 2024 | 3pt_share_T3 | A2 | 9194 | +0.0227 | +0.0127 | +0.0330 |
| 2024 | 3pt_share_T3 | A3 | 9194 | +0.0002 | -0.0045 | +0.0050 |
| 2024 | 3pt_share_T3 | A4 | 9194 | +0.2027 | +0.1776 | +0.2297 |
| 2024 | starter | A1 | 12057 | +0.0420 | +0.0303 | +0.0529 |
| 2024 | starter | A2 | 12057 | +0.0179 | +0.0064 | +0.0287 |
| 2024 | starter | A3 | 12057 | +0.0012 | -0.0040 | +0.0062 |
| 2024 | starter | A4 | 12057 | +0.2276 | +0.2059 | +0.2501 |
| 2024 | bench | A1 | 15526 | +0.0206 | +0.0138 | +0.0275 |
| 2024 | bench | A2 | 15526 | +0.0356 | +0.0278 | +0.0433 |
| 2024 | bench | A3 | 15526 | +0.0012 | -0.0021 | +0.0045 |
| 2024 | bench | A4 | 15526 | +0.2310 | +0.2091 | +0.2536 |
| 2024 | cold_start_n_prior<20 | A1 | 1323 | +0.0484 | +0.0256 | +0.0720 |
| 2024 | cold_start_n_prior<20 | A2 | 1323 | +0.0685 | +0.0432 | +0.0957 |
| 2024 | cold_start_n_prior<20 | A3 | 1323 | +0.0029 | -0.0083 | +0.0150 |
| 2024 | cold_start_n_prior<20 | A4 | 1323 | +0.1403 | +0.0888 | +0.1946 |

## Attack inputs (crps difference, all rows)

| season | A1-A2 | A1-A3 | A1-A4 | shared factor did nothing |
|---|---|---|---|---|
| 2023 | +0.0059 [+0.0032, +0.0085] | +0.0340 [+0.0270, +0.0405] | -0.1834 [-0.1973, -0.1694] | True |
| 2024 | +0.0021 [+0.0000, +0.0041] | +0.0288 [+0.0231, +0.0348] | -0.1995 [-0.2154, -0.1844] | True |

## Secondary: fg3m head alone vs production fg3m (descriptive, not tested)

| season | n_rows | crps_int prod | crps_int components | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|
| 2023 | 27619 | 0.5690 | 0.5663 | -0.0027 | -0.0042 | -0.0012 |
| 2024 | 27583 | 0.5923 | 0.5918 | -0.0004 | -0.0021 | +0.0012 |

## Rules

```
{
 "2023": {
  "rule1_floor_and_ci": false,
  "rule2_guards": {
   "abs_bias<=0.5": true,
   "cov80_in_[.75,.85]": true,
   "pit_q90_within_.02": true,
   "no_slice_worse_than_+0.01": false
  },
  "rule2": false,
  "slices_worse": {
   "tier_<10": 0.035189249232704944,
   "tier_17-24": 0.04090033091398917,
   "tier_24+": 0.16495445245514762,
   "3pt_share_T1": 0.04796466179487622,
   "3pt_share_T2": 0.03210711125466535,
   "3pt_share_T3": 0.018568097502546625,
   "starter": 0.036204710092737284,
   "bench": 0.030224298740015018,
   "cold_start_n_prior<20": 0.059515507679904586
  },
  "rule3_attack_gates": {
   "A1_minus_A4<=-0.005": true,
   "A1_minus_A3<=-0.003": false
  },
  "rule3": false,
  "A1_minus_A2_reported": {
   "point": 0.005881946247045013,
   "lo": 0.003214074019576413,
   "hi": 0.008543286007389154
  },
  "shared_factor_did_nothing": true,
  "attempt_features_help_not_structure": true,
  "A3_dcrps": -0.0010891747392485294,
  "A3_rule1": false,
  "season_pass": false
 },
 "2024": {
  "rule1_floor_and_ci": false,
  "rule2_guards": {
   "abs_bias<=0.5": true,
   "cov80_in_[.75,.85]": true,
   "pit_q90_within_.02": true,
   "no_slice_worse_than_+0.01": false
  },
  "rule2": false,
  "slices_worse": {
   "tier_<10": 0.019722044716886714,
   "tier_17-24": 0.05961696995013352,
   "tier_24+": 0.16168089735699098,
   "3pt_share_T1": 0.03661824963939164,
   "3pt_share_T2": 0.040252871094848715,
   "3pt_share_T3": 0.012999209753796174,
   "starter": 0.04197474016636006,
   "bench": 0.02062443607953395,
   "cold_start_n_prior<20": 0.04843451371497199
  },
  "rule3_attack_gates": {
   "A1_minus_A4<=-0.005": true,
   "A1_minus_A3<=-0.003": false
  },
  "rule3": false,
  "A1_minus_A2_reported": {
   "point": 0.0021303692748317446,
   "lo": 2.352309788259269e-05,
   "hi": 0.004121529080899732
  },
  "shared_factor_did_nothing": true,
  "attempt_features_help_not_structure": true,
  "A3_dcrps": 0.0011744897073281489,
  "A3_rule1": false,
  "season_pass": false
 },
 "final_pass": false,
 "fails_on_2023_close": true
}
```

## Fitted activity parameters per block (A1)

```
{"r": [1114.362018588321, 39.32566137532383, 1.8247587142375539], "beta": [0.3723157519795543, 0.31653132210814405, 0.5214579688748331], "kappa": [927.6119209634195, 415.24845137468435, 87.65859886399193]} 2023-10-01
{"r": [977.328599914386, 46.03737292031341, 1.8862802265378131], "beta": [0.37766018713204536, 0.3072902028881226, 0.5477685448592732], "kappa": [290.13399583998824, 4999.975680548049, 116.67960291002366]} 2023-11-01
{"r": [494.5114133859153, 84.22746957645114, 2.1265137322230085], "beta": [0.2989090871436309, 0.18519401013164336, 0.4434597342195126], "kappa": [590.742391828108, 4999.9824454081845, 61.53679178423017]} 2023-12-01
{"r": [1464.2655731085651, 47.32920482492583, 2.1047609164498793], "beta": [0.2818411903101072, 0.18883951863955983, 0.4249739975949403], "kappa": [404.7332947141372, 4999.971110450628, 44.61817778491984]} 2024-01-01
{"r": [392.6405449790756, 155.10759397602646, 1.9702462500623226], "beta": [0.28773644128519815, 0.20801523186345483, 0.41289148410045706], "kappa": [4999.982348703818, 4999.97209057034, 64.91355391981351]} 2024-02-01
{"r": [1551.2354325788929, 496.47724407369293, 1.8731701489485209], "beta": [0.2690062522323204, 0.2096847787760364, 0.449230867698353], "kappa": [4823.354574377361, 4999.977471769187, 52.301276767660994]} 2024-03-01
{"r": [1948.5031624072042, 1008.2232333245906, 1.8065162950729072], "beta": [0.2853701030124296, 0.21219623398728824, 0.40798526687133974], "kappa": [4999.970070481981, 4999.967239454535, 65.11295044926466]} 2024-04-01
{"r": [1781.8215169401483, 383.74716772118416, 1.9557361577738166], "beta": [0.3196747024415207, 0.2247820222774723, 0.4169024076886103], "kappa": [4999.972384769619, 4999.823545757636, 78.74977242528163]} 2024-05-01
{"r": [859.3936597164069, 249.39593387669208, 1.982566682996899], "beta": [0.3103750955691288, 0.22368602695172748, 0.428965054947799], "kappa": [4999.974285152242, 1062.6249236651488, 116.39052385306978]} 2024-06-01
{"r": [1999.2624741649645, 433.40092238086123, 1.9801050023453026], "beta": [0.315700404212125, 0.22538420299765924, 0.4336991952958432], "kappa": [4999.971483604809, 709.6189808876901, 122.0889884447879]} 2024-10-01
{"r": [1355.567701485038, 72.48062446790979, 2.013020774895726], "beta": [0.3226551946732001, 0.2103760845996343, 0.43219283505132283], "kappa": [4999.965921974609, 523.8201288916393, 86.89604459481988]} 2024-11-01
{"r": [1298.2681313882035, 56.73174357972933, 2.082403595830582], "beta": [0.3166494090986329, 0.21329818368901332, 0.43692821333785703], "kappa": [4999.9707195759975, 1289.9029749672964, 77.22641540380621]} 2024-12-01
{"r": [965.1543570845773, 254.50314061499736, 1.9550738932759777], "beta": [0.2843541433890488, 0.18764057316119795, 0.44774080945742656], "kappa": [4999.979563829801, 4999.980149995842, 120.985107403219]} 2025-01-01
{"r": [1497.6589126917902, 326.3227878218608, 2.0606224947458047], "beta": [0.2720420801957694, 0.18626971502976764, 0.44471696483167944], "kappa": [4999.972047507809, 4999.969462960433, 77.20295138346017]} 2025-02-01
{"r": [1940.6457262656938, 1824.806068920427, 2.027376842444684], "beta": [0.2802787450029664, 0.1931683891593875, 0.4300693467715171], "kappa": [4999.979543242193, 4999.97218981463, 57.09715511465524]} 2025-03-01
{"r": [1371.3788793906979, 448.21730744701784, 2.1694061625042047], "beta": [0.2968670545604498, 0.21241377920250643, 0.4274731124690824], "kappa": [4999.973272427885, 4999.969679329726, 37.15154496985414]} 2025-04-01
{"r": [1394.4496380340108, 459.73508976532423, 2.055606118342885], "beta": [0.3299904960632662, 0.23798309622604286, 0.4396686345750191], "kappa": [4999.967958487744, 4999.979362653614, 44.79327707581742]} 2025-05-01
{"r": [1934.6098687698843, 141.25002299138072, 2.052870884820912], "beta": [0.33328096129813406, 0.23261598565868508, 0.440710035152566], "kappa": [4999.98108582487, 4999.979041049632, 60.61755474999027]} 2025-06-01
```

## Unstated details, chosen before scoring

* no ContextResidualConfig.pts_components flag is added (nba/ is untouched; the layering test forbids nba -> research): 'flag off byte-identical' is checked as nba/props/context_residual.py unchanged vs HEAD plus the A0 reproduction to 1e-6
* attempt mean = recency m10 + gradient-boosted residual (production learner, hyper-parameters, seed 0) floored at 0.05; every attempt head uses the full feature list (production pts + the 27 component columns)
* NegBin size r_k, activity loadings beta_k and Beta-Binomial concentrations kappa_k are fit on the calibration window (the newest 15% of the training window, rows the heads never saw: production's split_calibration), not on the head-fit rows
* beta_k and r_k jointly by marginal likelihood with z integrated by 15-node Gauss-Hermite quadrature (L-BFGS-B, 60 iterations, start beta=0.15, r from the independent fit); the factor is mean-one, exp(beta z - beta^2/2), so the head mean stays mu_k.  CHOSEN AFTER the one-block smoke (Oct 2023, n=1062) showed the literal exp(beta z) inflates the pmf mean by exp(beta^2/2), about +0.75 points (A1 bias -1.87 there); the choice rests on that arithmetic and on the doc calling mu_k the head's mean, not on any comparison of CRPS across variants
* A2 (independent sum) reuses the A1 heads; r_k is its own 1-D MLE with beta = 0
* makes given attempts: p = the shrunk prior-40 rate itself (no learner on the make rate); kappa_k by 1-D MLE
* prior-40 window = the player's previous 40 PLAYED games (shifted; tonight excluded); league rate = all rows on strictly earlier dates (fixed constants only on the first date); pseudo-count 50 attempts
* attempt mass above the caps 40 / 25 / 30 is folded onto the cap cell (the grid sums to one exactly); fta exceeds 30 in a handful of rows, whose labels are untouched
* sd10 for attempts: 1.0 when degenerate (production's per-stat default std has no attempt analogue)
* points quantile grid = smallest k with CDF(k) >= tau on production's 199-tau grid; every arm is scored by the same CRPS-from-quantiles, randomized-PIT and mean-of-grid bias code on integer quantile grids
* A3 appends all 27 component columns (24 attempt columns + 3 shrunk make rates) to production's pts list
* A4 permutes the six component targets JOINTLY within player over the whole 2022-2024 frame (seed 0); the evaluation label y is real points
* slices: scoring tier by m10_pts (<10, 10-17, 17-24, 24+); three-point share = 3 m10_fg3m / max(m10_pts, 0.1) in per-season terciles; starter = starter10 >= 0.5; cold start n_prior < 20; slice CIs only for n >= 300
* pass rule evaluated in each season and required in BOTH (2023 selects, 2024 confirms); 'A1 beats A4 by <= -0.005' = crps(A1) - crps(A4) point <= -0.005 on all rows (likewise A3 at -0.003); the A1 vs A2 line reports shared_factor_did_nothing = crps(A2) <= crps(A1)
* one primary test per season (pts only): the family has size 1, so p_bh = p
* upper PIT = share with randomized PIT <= 0.80 and <= 0.90; cov80 = share with PIT in (0.10, 0.90]; tll = mean log loss of P(y >= n) at n = 20, 25, 30 reported separately
* check 2 audits the 27 component columns plus both make-rate inputs on the scored rows (n_prior >= 5) by realised-minutes bucket (<5, 5-10, 10-20, >20); max gap over buckets and columns <= 2 pp
* check 3 plants edits to the box columns (+9 on every shot column for one mid-season day; +5 for every game dated >= 2024-01-15) and requires earlier rows' component features to be unchanged and later rows to move
* check 5 at the gate uses fixed synthetic parameters on all 2023-2024 scored rows (no fitted model); the arms stage asserts the same bound for every fitted grid
* secondary fg3m: the fg3m marginal of A1 versus production's fg3m arm (same rows, same integer support), descriptive
* rows failing check 1 would be dropped from ALL arms before any fit (counted; 0 on the real data)

Seeds: {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}; git SHA 3a729d73f252dba327bbaba72139125d192f280f; frozen sha256 7e6fd8f019985032b712b71fb275f4bf6841862a09e8850dfd1c11913cbfafc8.