# F11 lineup context: results (docs/prereg/F11_LINEUP_CONTEXT.md)

## Hash verification

`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F11_LINEUP_CONTEXT.md | shasum -a 256` = `2e2632794d2612764cc2d1f13fbf0c39d07a267e361df24d43160a1ffcd511f3`; frozen prefix `2e263279` matches: True. Freeze commit b5a1dab. git SHA at run: `afc914633c63274e6a7fcc45363c515f01914842`. Seeds: {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}. CPU only. Seasons <= 2024 only (max season loaded: 2024); nba.duckdb read-only. Check record sha256 (written before any arm): `6a3d7dc3f68863e3b8e8b1a9ff052d39a94095427272e50f4fd0fa0f244cefab`.

## Minimum data checks (each vs its threshold)

| check | value | threshold | ok |
|---|---|---|---|
| 1 stints cover regular-season games (both teams) | 1 | >= 0.95 | ok |
| 1 possessions cover regular-season games (both teams) | 1 | >= 0.95 | ok |
| 2 max NULL-rate gap across buckets / terciles (pp) | 0.159158 | <= 2.0 | ok |
| 3 planted same-game: lc_* byte-identical | 1 | == 1 | ok |
| 3 planted same-game is not inert (later games do change) | 1 | == 1 | ok |
| 3 teammate moved to OUT after the real tip: no change | 1 | == 1 | ok |
| 3 control: same teammate OUT BEFORE the tip does change lc_* | 1 | == 1 | ok |
| 4 planted future (>= game date): lc_* unchanged | 1 | == 1 | ok |
| 4 planted future is not inert (later games do change) | 1 | == 1 | ok |
| 6 sum_q c(p,q) = 4 for rows with >= 4 available | 2.66454e-15 | <= 1e-09 | ok |
| 5 A0 pts integer CRPS vs LOWER_TAIL 2023 (|delta|) | 0 | <= 1e-6 | ok |
| 5 A0 pts integer CRPS vs LOWER_TAIL 2024 (|delta|) | 0 | <= 1e-6 | ok |
| 5 flag off: A0 feature list contains no lc_* column | 1 | == 1 | ok |

Check 1 detail:

```
{
 "n_regular_games": 3685,
 "stints_cov": 1.0,
 "poss_cov": 1.0,
 "by_season": {
  "2022": {
   "games": 1230,
   "stints_both": 1230,
   "poss_both": 1230
  },
  "2023": {
   "games": 1230,
   "stints_both": 1230,
   "poss_both": 1230
  },
  "2024": {
   "games": 1225,
   "stints_both": 1225,
   "poss_both": 1225
  }
 }
}
```

Check 2 (gaps in pp per column|split):

```
{
 "gaps_pp": {
  "lc_thr3|minutes": 0.15915827897664245,
  "lc_thr3|pts": 0.10749570978688694,
  "lc_thr3|reb": 0.04663043743804462,
  "lc_thr3|ast": 0.09682084022378401,
  "lc_thr3|fg3m": 0.059856359901974125,
  "lc_lift|minutes": 0.15915827897664245,
  "lc_lift|pts": 0.10749570978688694,
  "lc_lift|reb": 0.04663043743804462,
  "lc_lift|ast": 0.09682084022378401,
  "lc_lift|fg3m": 0.059856359901974125,
  "lc_rpm|minutes": 0.15915827897664245,
  "lc_rpm|pts": 0.10749570978688694,
  "lc_rpm|reb": 0.04663043743804462,
  "lc_rpm|ast": 0.09682084022378401,
  "lc_rpm|fg3m": 0.059856359901974125,
  "lc_top3|minutes": 0.15915827897664245,
  "lc_top3|pts": 0.10749570978688694,
  "lc_top3|reb": 0.04663043743804462,
  "lc_top3|ast": 0.09682084022378401,
  "lc_top3|fg3m": 0.059856359901974125,
  "lc_cover|minutes": 0.15915827897664245,
  "lc_cover|pts": 0.10749570978688694,
  "lc_cover|reb": 0.04663043743804462,
  "lc_cover|ast": 0.09682084022378401,
  "lc_cover|fg3m": 0.059856359901974125
 },
 "max_gap_pp": 0.15915827897664245,
 "overall_null_rate": {
  "lc_thr3": 0.004085368791251144,
  "lc_lift": 0.004085368791251144,
  "lc_rpm": 0.004085368791251144,
  "lc_top3": 0.004085368791251144,
  "lc_cover": 0.004085368791251144
 }
}
```

Check 2 audit, `[col, bucket, null_rate]` (minutes buckets; terciles in results.json):

| col | bucket | null_rate | n |
|---|---|---|---|
| lc_thr3 | DNP | 0.00481 | 19733 |
| lc_thr3 | <5 | 0.00419 | 7394 |
| lc_thr3 | 5-10 | 0.00322 | 6206 |
| lc_thr3 | 10-20 | 0.00431 | 19485 |
| lc_thr3 | >20 | 0.00381 | 50967 |
| lc_lift | DNP | 0.00481 | 19733 |
| lc_lift | <5 | 0.00419 | 7394 |
| lc_lift | 5-10 | 0.00322 | 6206 |
| lc_lift | 10-20 | 0.00431 | 19485 |
| lc_lift | >20 | 0.00381 | 50967 |
| lc_rpm | DNP | 0.00481 | 19733 |
| lc_rpm | <5 | 0.00419 | 7394 |
| lc_rpm | 5-10 | 0.00322 | 6206 |
| lc_rpm | 10-20 | 0.00431 | 19485 |
| lc_rpm | >20 | 0.00381 | 50967 |
| lc_top3 | DNP | 0.00481 | 19733 |
| lc_top3 | <5 | 0.00419 | 7394 |
| lc_top3 | 5-10 | 0.00322 | 6206 |
| lc_top3 | 10-20 | 0.00431 | 19485 |
| lc_top3 | >20 | 0.00381 | 50967 |
| lc_cover | DNP | 0.00481 | 19733 |
| lc_cover | <5 | 0.00419 | 7394 |
| lc_cover | 5-10 | 0.00322 | 6206 |
| lc_cover | 10-20 | 0.00431 | 19485 |
| lc_cover | >20 | 0.00381 | 50967 |

Check 6:

```
{
 "n_rows_ge4": 103361,
 "max_abs_dev": 2.6645352591003757e-15,
 "n_rows_lt4_with_values": 0
}
```

A0 reproduction (check 5):

```
{
 "seasons": {
  "2023": {
   "got": 3.152829990762183,
   "lower_tail_exact": 3.152829990762183,
   "delta_exact": 0.0,
   "doc_4dp": 3.1528,
   "delta_doc_4dp": 2.999076218301866e-05,
   "n": 27619,
   "n_ref": 27619
  },
  "2024": {
   "got": 3.1884109851549947,
   "lower_tail_exact": 3.1884109851549947,
   "delta_exact": 0.0,
   "doc_4dp": 3.1884,
   "delta_doc_4dp": 1.0985154994536117e-05,
   "n": 27583,
   "n_ref": 27583
  }
 },
 "ok_1e-6": true,
 "a0_has_no_lc_columns": true
}
```

## Results (dCRPS = arm - A0, integer support, game-clustered 95% CI, 2000 resamples)

mde = 1.96 x clustered SE. bias = stat - forecast mean. cov80 = central 80% PIT coverage (0.10 < PIT <= 0.90). tll = mean threshold log loss at the declared thresholds (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2). p_bh: BH over the 4 stats of the A1 all-rows p, per season.

| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 2023 | A0 | 27619 | 1318 | 3.1528 |  |  |  |  |  |  | -0.149 | 0.774 | 0.129 | 0.221 | 0.4179 |
| pts | 2023 | A1 | 27619 | 1318 | 3.1545 | +0.0016 | -0.0012 | +0.0045 | 0.238 | 0.795 | 0.0029 | -0.155 | 0.774 | 0.131 | 0.221 | 0.4181 |
| pts | 2023 | A2 | 27619 | 1318 | 3.1532 | +0.0004 | -0.0023 | +0.0031 | 0.762 |  | 0.0027 | -0.157 | 0.776 | 0.129 | 0.220 | 0.4179 |
| pts | 2023 | A3 | 27619 | 1318 | 3.0930 | -0.0598 | -0.0663 | -0.0531 | 0.000 |  | 0.0066 | -0.167 | 0.779 | 0.125 | 0.227 | 0.4126 |
| pts | 2023 | A4 | 27619 | 1318 | 3.1540 | +0.0012 | -0.0015 | +0.0040 | 0.359 |  | 0.0027 | -0.166 | 0.774 | 0.130 | 0.221 | 0.4183 |
| pts | 2024 | A0 | 27583 | 1315 | 3.1884 |  |  |  |  |  |  | -0.113 | 0.764 | 0.134 | 0.225 | 0.4326 |
| pts | 2024 | A1 | 27583 | 1315 | 3.1864 | -0.0020 | -0.0040 | +0.0002 | 0.079 | 0.278 | 0.0021 | -0.110 | 0.762 | 0.135 | 0.225 | 0.4324 |
| pts | 2024 | A2 | 27583 | 1315 | 3.1885 | +0.0001 | -0.0018 | +0.0022 | 0.886 |  | 0.0020 | -0.117 | 0.764 | 0.134 | 0.226 | 0.4324 |
| pts | 2024 | A3 | 27583 | 1315 | 3.1304 | -0.0580 | -0.0642 | -0.0516 | 0.000 |  | 0.0063 | -0.137 | 0.768 | 0.130 | 0.231 | 0.4264 |
| pts | 2024 | A4 | 27583 | 1315 | 3.1879 | -0.0005 | -0.0026 | +0.0018 | 0.690 |  | 0.0022 | -0.109 | 0.765 | 0.134 | 0.226 | 0.4323 |
| reb | 2023 | A0 | 27619 | 1318 | 1.2928 |  |  |  |  |  |  | -0.043 | 0.792 | 0.113 | 0.209 | 0.4625 |
| reb | 2023 | A1 | 27619 | 1318 | 1.2926 | -0.0002 | -0.0015 | +0.0011 | 0.795 | 0.795 | 0.0013 | -0.053 | 0.792 | 0.114 | 0.210 | 0.4625 |
| reb | 2023 | A2 | 27619 | 1318 | 1.2940 | +0.0012 | +0.0000 | +0.0024 | 0.048 |  | 0.0012 | -0.041 | 0.793 | 0.112 | 0.209 | 0.4626 |
| reb | 2023 | A3 | 27619 | 1318 | 1.2593 | -0.0335 | -0.0370 | -0.0301 | 0.000 |  | 0.0034 | -0.070 | 0.792 | 0.114 | 0.215 | 0.4534 |
| reb | 2023 | A4 | 27619 | 1318 | 1.2924 | -0.0004 | -0.0017 | +0.0009 | 0.556 |  | 0.0013 | -0.047 | 0.792 | 0.113 | 0.208 | 0.4622 |
| reb | 2024 | A0 | 27583 | 1315 | 1.3140 |  |  |  |  |  |  | -0.051 | 0.787 | 0.116 | 0.215 | 0.4726 |
| reb | 2024 | A1 | 27583 | 1315 | 1.3136 | -0.0004 | -0.0015 | +0.0006 | 0.450 | 0.450 | 0.0010 | -0.047 | 0.787 | 0.115 | 0.214 | 0.4726 |
| reb | 2024 | A2 | 27583 | 1315 | 1.3140 | +0.0000 | -0.0008 | +0.0009 | 0.913 |  | 0.0009 | -0.053 | 0.788 | 0.115 | 0.215 | 0.4729 |
| reb | 2024 | A3 | 27583 | 1315 | 1.2828 | -0.0312 | -0.0345 | -0.0278 | 0.000 |  | 0.0033 | -0.059 | 0.788 | 0.114 | 0.219 | 0.4638 |
| reb | 2024 | A4 | 27583 | 1315 | 1.3136 | -0.0004 | -0.0015 | +0.0007 | 0.467 |  | 0.0011 | -0.050 | 0.787 | 0.115 | 0.215 | 0.4728 |
| ast | 2023 | A0 | 27619 | 1318 | 0.9059 |  |  |  |  |  |  | -0.050 | 0.787 | 0.118 | 0.214 | 0.4311 |
| ast | 2023 | A1 | 27619 | 1318 | 0.9058 | -0.0001 | -0.0010 | +0.0008 | 0.772 | 0.795 | 0.0009 | -0.054 | 0.787 | 0.118 | 0.216 | 0.4310 |
| ast | 2023 | A2 | 27619 | 1318 | 0.9059 | -0.0000 | -0.0009 | +0.0009 | 0.982 |  | 0.0009 | -0.056 | 0.788 | 0.118 | 0.215 | 0.4312 |
| ast | 2023 | A3 | 27619 | 1318 | 0.8969 | -0.0090 | -0.0106 | -0.0075 | 0.000 |  | 0.0015 | -0.056 | 0.787 | 0.118 | 0.215 | 0.4264 |
| ast | 2023 | A4 | 27619 | 1318 | 0.9059 | -0.0000 | -0.0009 | +0.0009 | 0.953 |  | 0.0009 | -0.047 | 0.788 | 0.117 | 0.212 | 0.4310 |
| ast | 2024 | A0 | 27583 | 1315 | 0.9010 |  |  |  |  |  |  | -0.066 | 0.784 | 0.123 | 0.219 | 0.4377 |
| ast | 2024 | A1 | 27583 | 1315 | 0.9014 | +0.0004 | -0.0003 | +0.0011 | 0.350 | 0.450 | 0.0007 | -0.055 | 0.784 | 0.121 | 0.217 | 0.4374 |
| ast | 2024 | A2 | 27583 | 1315 | 0.9013 | +0.0003 | -0.0004 | +0.0010 | 0.455 |  | 0.0007 | -0.058 | 0.784 | 0.121 | 0.218 | 0.4378 |
| ast | 2024 | A3 | 27583 | 1315 | 0.8920 | -0.0090 | -0.0103 | -0.0076 | 0.000 |  | 0.0014 | -0.061 | 0.784 | 0.121 | 0.220 | 0.4321 |
| ast | 2024 | A4 | 27583 | 1315 | 0.9009 | -0.0001 | -0.0008 | +0.0006 | 0.730 |  | 0.0007 | -0.061 | 0.783 | 0.123 | 0.218 | 0.4372 |
| fg3m | 2023 | A0 | 27619 | 1318 | 0.5690 |  |  |  |  |  |  | -0.015 | 0.791 | 0.113 | 0.212 | 0.4900 |
| fg3m | 2023 | A1 | 27619 | 1318 | 0.5689 | -0.0002 | -0.0008 | +0.0005 | 0.623 | 0.795 | 0.0007 | -0.013 | 0.791 | 0.113 | 0.211 | 0.4895 |
| fg3m | 2023 | A2 | 27619 | 1318 | 0.5689 | -0.0001 | -0.0008 | +0.0005 | 0.754 |  | 0.0006 | -0.012 | 0.789 | 0.115 | 0.211 | 0.4902 |
| fg3m | 2023 | A3 | 27619 | 1318 | 0.5622 | -0.0068 | -0.0078 | -0.0059 | 0.000 |  | 0.0010 | -0.021 | 0.788 | 0.115 | 0.213 | 0.4823 |
| fg3m | 2023 | A4 | 27619 | 1318 | 0.5690 | -0.0000 | -0.0007 | +0.0006 | 0.918 |  | 0.0006 | -0.015 | 0.792 | 0.113 | 0.211 | 0.4902 |
| fg3m | 2024 | A0 | 27583 | 1315 | 0.5923 |  |  |  |  |  |  | -0.001 | 0.784 | 0.114 | 0.212 | 0.4948 |
| fg3m | 2024 | A1 | 27583 | 1315 | 0.5927 | +0.0004 | -0.0001 | +0.0010 | 0.139 | 0.278 | 0.0006 | -0.000 | 0.784 | 0.115 | 0.211 | 0.4952 |
| fg3m | 2024 | A2 | 27583 | 1315 | 0.5925 | +0.0002 | -0.0003 | +0.0008 | 0.360 |  | 0.0005 | +0.002 | 0.784 | 0.115 | 0.209 | 0.4950 |
| fg3m | 2024 | A3 | 27583 | 1315 | 0.5871 | -0.0052 | -0.0061 | -0.0042 | 0.000 |  | 0.0010 | -0.006 | 0.783 | 0.115 | 0.214 | 0.4883 |
| fg3m | 2024 | A4 | 27583 | 1315 | 0.5926 | +0.0003 | -0.0003 | +0.0009 | 0.299 |  | 0.0006 | -0.003 | 0.784 | 0.115 | 0.212 | 0.4947 |

## Slices (A1 - A0 dCRPS, game-clustered CI)

| stat | season | slice | n | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|
| pts | 2023 | all | 27619 | +0.0016 | -0.0012 | +0.0045 |
| pts | 2023 | n_prior<20 | 1630 | +0.0095 | +0.0011 | +0.0188 |
| pts | 2023 | starter | 12267 | +0.0005 | -0.0046 | +0.0060 |
| pts | 2023 | bench | 15352 | +0.0025 | -0.0004 | +0.0056 |
| pts | 2023 | first15 | 4586 | +0.0005 | -0.0071 | +0.0080 |
| pts | 2023 | teammate_out | 23246 | +0.0009 | -0.0022 | +0.0040 |
| pts | 2023 | traded | 1847 | +0.0041 | -0.0064 | +0.0143 |
| pts | 2023 | lc_cover<0.5 | 817 | +0.0043 | -0.0084 | +0.0170 |
| pts | 2024 | all | 27583 | -0.0020 | -0.0040 | +0.0002 |
| pts | 2024 | n_prior<20 | 1323 | +0.0050 | -0.0009 | +0.0114 |
| pts | 2024 | starter | 12057 | -0.0031 | -0.0068 | +0.0009 |
| pts | 2024 | bench | 15526 | -0.0011 | -0.0033 | +0.0012 |
| pts | 2024 | first15 | 4621 | -0.0013 | -0.0067 | +0.0041 |
| pts | 2024 | teammate_out | 23115 | -0.0017 | -0.0041 | +0.0006 |
| pts | 2024 | traded | 1672 | -0.0031 | -0.0116 | +0.0053 |
| pts | 2024 | lc_cover<0.5 | 771 | -0.0092 | -0.0190 | +0.0003 |
| reb | 2023 | all | 27619 | -0.0002 | -0.0015 | +0.0011 |
| reb | 2023 | n_prior<20 | 1630 | +0.0005 | -0.0043 | +0.0051 |
| reb | 2023 | starter | 12267 | -0.0017 | -0.0040 | +0.0007 |
| reb | 2023 | bench | 15352 | +0.0010 | -0.0005 | +0.0026 |
| reb | 2023 | first15 | 4586 | -0.0000 | -0.0036 | +0.0038 |
| reb | 2023 | teammate_out | 23246 | -0.0005 | -0.0019 | +0.0010 |
| reb | 2023 | traded | 1847 | +0.0005 | -0.0047 | +0.0055 |
| reb | 2023 | lc_cover<0.5 | 817 | -0.0087 | -0.0146 | -0.0026 |
| reb | 2024 | all | 27583 | -0.0004 | -0.0015 | +0.0006 |
| reb | 2024 | n_prior<20 | 1323 | -0.0030 | -0.0080 | +0.0020 |
| reb | 2024 | starter | 12057 | -0.0000 | -0.0019 | +0.0017 |
| reb | 2024 | bench | 15526 | -0.0007 | -0.0020 | +0.0005 |
| reb | 2024 | first15 | 4621 | -0.0006 | -0.0034 | +0.0021 |
| reb | 2024 | teammate_out | 23115 | -0.0005 | -0.0017 | +0.0007 |
| reb | 2024 | traded | 1672 | +0.0008 | -0.0039 | +0.0053 |
| reb | 2024 | lc_cover<0.5 | 771 | -0.0053 | -0.0124 | +0.0015 |
| ast | 2023 | all | 27619 | -0.0001 | -0.0010 | +0.0008 |
| ast | 2023 | n_prior<20 | 1630 | +0.0009 | -0.0021 | +0.0039 |
| ast | 2023 | starter | 12267 | -0.0013 | -0.0030 | +0.0005 |
| ast | 2023 | bench | 15352 | +0.0008 | -0.0001 | +0.0017 |
| ast | 2023 | first15 | 4586 | +0.0019 | -0.0009 | +0.0044 |
| ast | 2023 | teammate_out | 23246 | +0.0001 | -0.0009 | +0.0011 |
| ast | 2023 | traded | 1847 | +0.0009 | -0.0025 | +0.0045 |
| ast | 2023 | lc_cover<0.5 | 817 | -0.0026 | -0.0075 | +0.0024 |
| ast | 2024 | all | 27583 | +0.0004 | -0.0003 | +0.0011 |
| ast | 2024 | n_prior<20 | 1323 | +0.0002 | -0.0020 | +0.0025 |
| ast | 2024 | starter | 12057 | +0.0001 | -0.0013 | +0.0015 |
| ast | 2024 | bench | 15526 | +0.0006 | -0.0002 | +0.0013 |
| ast | 2024 | first15 | 4621 | +0.0004 | -0.0018 | +0.0025 |
| ast | 2024 | teammate_out | 23115 | +0.0003 | -0.0005 | +0.0011 |
| ast | 2024 | traded | 1672 | -0.0022 | -0.0050 | +0.0008 |
| ast | 2024 | lc_cover<0.5 | 771 | -0.0026 | -0.0068 | +0.0015 |
| fg3m | 2023 | all | 27619 | -0.0002 | -0.0008 | +0.0005 |
| fg3m | 2023 | n_prior<20 | 1630 | +0.0023 | +0.0005 | +0.0039 |
| fg3m | 2023 | starter | 12267 | -0.0013 | -0.0025 | -0.0001 |
| fg3m | 2023 | bench | 15352 | +0.0007 | +0.0000 | +0.0014 |
| fg3m | 2023 | first15 | 4586 | +0.0006 | -0.0009 | +0.0023 |
| fg3m | 2023 | teammate_out | 23246 | -0.0001 | -0.0009 | +0.0006 |
| fg3m | 2023 | traded | 1847 | +0.0003 | -0.0020 | +0.0025 |
| fg3m | 2023 | lc_cover<0.5 | 817 | +0.0011 | -0.0013 | +0.0037 |
| fg3m | 2024 | all | 27583 | +0.0004 | -0.0001 | +0.0010 |
| fg3m | 2024 | n_prior<20 | 1323 | +0.0006 | -0.0010 | +0.0021 |
| fg3m | 2024 | starter | 12057 | +0.0006 | -0.0004 | +0.0017 |
| fg3m | 2024 | bench | 15526 | +0.0003 | -0.0003 | +0.0009 |
| fg3m | 2024 | first15 | 4621 | +0.0019 | +0.0003 | +0.0034 |
| fg3m | 2024 | teammate_out | 23115 | +0.0005 | -0.0001 | +0.0011 |
| fg3m | 2024 | traded | 1672 | +0.0003 | -0.0018 | +0.0024 |
| fg3m | 2024 | lc_cover<0.5 | 771 | +0.0018 | -0.0009 | +0.0043 |

## Mechanism check (descriptive; expected monotone if the story is real)

| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|---|
| pts | 2023 | lc_thr3 | T1 | 9207 | +0.0009 | -0.0045 | +0.0061 |
| pts | 2023 | lc_thr3 | T2 | 9206 | +0.0028 | -0.0016 | +0.0077 |
| pts | 2023 | lc_thr3 | T3 | 9206 | +0.0012 | -0.0034 | +0.0060 |
| pts | 2024 | lc_thr3 | T1 | 9195 | -0.0013 | -0.0052 | +0.0029 |
| pts | 2024 | lc_thr3 | T2 | 9194 | -0.0036 | -0.0071 | +0.0001 |
| pts | 2024 | lc_thr3 | T3 | 9194 | -0.0010 | -0.0044 | +0.0023 |
| reb | 2023 | lc_rpm | T1 | 9207 | +0.0009 | -0.0023 | +0.0040 |
| reb | 2023 | lc_rpm | T2 | 9206 | -0.0002 | -0.0021 | +0.0016 |
| reb | 2023 | lc_rpm | T3 | 9206 | -0.0011 | -0.0028 | +0.0006 |
| reb | 2024 | lc_rpm | T1 | 9195 | -0.0013 | -0.0039 | +0.0013 |
| reb | 2024 | lc_rpm | T2 | 9194 | +0.0004 | -0.0011 | +0.0019 |
| reb | 2024 | lc_rpm | T3 | 9194 | -0.0004 | -0.0017 | +0.0009 |
| fg3m | 2023 | lc_thr3 | T1 | 9207 | +0.0000 | -0.0013 | +0.0013 |
| fg3m | 2023 | lc_thr3 | T2 | 9206 | -0.0006 | -0.0016 | +0.0006 |
| fg3m | 2023 | lc_thr3 | T3 | 9206 | -0.0000 | -0.0010 | +0.0010 |
| fg3m | 2024 | lc_thr3 | T1 | 9195 | +0.0007 | -0.0005 | +0.0018 |
| fg3m | 2024 | lc_thr3 | T2 | 9194 | -0.0001 | -0.0012 | +0.0009 |
| fg3m | 2024 | lc_thr3 | T3 | 9194 | +0.0007 | -0.0000 | +0.0015 |

## Attack gate inputs (all rows; paired dCRPS, A1 minus other)

| stat | season | A1-A2 [CI] | A1-A3 [CI] | A1-A4 [CI] |
|---|---|---|---|---|
| pts | 2023 | +0.0012 [-0.0014, +0.0040] | +0.0615 [+0.0547, +0.0682] | +0.0004 [-0.0021, +0.0029] |
| pts | 2024 | -0.0021 [-0.0040, -0.0002] | +0.0560 [+0.0496, +0.0621] | -0.0015 [-0.0036, +0.0006] |
| reb | 2023 | -0.0013 [-0.0026, -0.0001] | +0.0334 [+0.0299, +0.0367] | +0.0002 [-0.0009, +0.0013] |
| reb | 2024 | -0.0005 [-0.0015, +0.0005] | +0.0308 [+0.0273, +0.0342] | -0.0000 [-0.0009, +0.0008] |
| ast | 2023 | -0.0001 [-0.0010, +0.0008] | +0.0089 [+0.0074, +0.0104] | -0.0001 [-0.0009, +0.0007] |
| ast | 2024 | +0.0001 [-0.0006, +0.0007] | +0.0094 [+0.0080, +0.0107] | +0.0005 [-0.0002, +0.0012] |
| fg3m | 2023 | -0.0001 [-0.0007, +0.0006] | +0.0066 [+0.0057, +0.0076] | -0.0001 [-0.0008, +0.0005] |
| fg3m | 2024 | +0.0002 [-0.0004, +0.0008] | +0.0056 [+0.0047, +0.0066] | +0.0001 [-0.0004, +0.0006] |

## Gates

| stat | season | g1 floor/CI/BH | g2 guards | g3 attacks | pass |
|---|---|---|---|---|---|
| pts | 2023 | False | False | False | False |
| pts | 2024 | False | False | False | False |
| reb | 2023 | False | True | False | False |
| reb | 2024 | False | True | False | False |
| ast | 2023 | False | True | False | False |
| ast | 2024 | False | False | False | False |
| fg3m | 2023 | False | True | False | False |
| fg3m | 2024 | False | True | False | False |

Gate detail:

```
pts 2023: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": false, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
pts 2024: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": false, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
reb 2023: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": true, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": true, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
reb 2024: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": true, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": true, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
ast 2023: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": true, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
ast 2024: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": false, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
fg3m 2023: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": true, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
fg3m 2024: guards={"abs_bias<=0.5": true, "cov80_in_[.75,.85]": true, "pit_q10_within_.02": true, "no_slice_worse_than_+0.01": true} slices_worse={} attacks={"A1_minus_A2<=-0.003": false, "A4_keeps_>=50%_of_A1_gain": false, "A1_gain_smaller_than_A3": true, "planted_tests_pass": true} leak_flag=False
```

## Verdicts

```
{
 "pts": {
  "passes_2023": false,
  "confirmed_2024": false,
  "audit_leak_flag": false
 },
 "reb": {
  "passes_2023": false,
  "confirmed_2024": false,
  "audit_leak_flag": false
 },
 "ast": {
  "passes_2023": false,
  "confirmed_2024": false,
  "audit_leak_flag": false
 },
 "fg3m": {
  "passes_2023": false,
  "confirmed_2024": false,
  "audit_leak_flag": false
 },
 "any_pass_2023": false,
 "close_no_stat_passes_2023": true
}
```

## Proposed ledger rows (draft, unnumbered; maintainer reviews and appends)

Columns follow docs/TEST_LEDGER.md: id | date | test | result | effect | CI lo | CI hi | cluster | p(BH) | notes | holdout status.

| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | pts 2023 A1 vs A0, integer-support CRPS, n=27619 rows / 1318 games | +0.0016 | -0.0012 | +0.0045 | game | 0.795 | MDE 0.0029; bias -0.149 -> -0.155; cov80 0.774 -> 0.774; A2 +0.0004, A3 oracle -0.0598, A4 +0.0012 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | pts 2024 A1 vs A0, integer-support CRPS, n=27583 rows / 1315 games | -0.0020 | -0.0040 | +0.0002 | game | 0.278 | MDE 0.0021; bias -0.113 -> -0.110; cov80 0.764 -> 0.762; A2 +0.0001, A3 oracle -0.0580, A4 -0.0005 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | reb 2023 A1 vs A0, integer-support CRPS, n=27619 rows / 1318 games | -0.0002 | -0.0015 | +0.0011 | game | 0.795 | MDE 0.0013; bias -0.043 -> -0.053; cov80 0.792 -> 0.792; A2 +0.0012, A3 oracle -0.0335, A4 -0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | reb 2024 A1 vs A0, integer-support CRPS, n=27583 rows / 1315 games | -0.0004 | -0.0015 | +0.0006 | game | 0.450 | MDE 0.0010; bias -0.051 -> -0.047; cov80 0.787 -> 0.787; A2 +0.0000, A3 oracle -0.0312, A4 -0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | ast 2023 A1 vs A0, integer-support CRPS, n=27619 rows / 1318 games | -0.0001 | -0.0010 | +0.0008 | game | 0.795 | MDE 0.0009; bias -0.050 -> -0.054; cov80 0.787 -> 0.787; A2 -0.0000, A3 oracle -0.0090, A4 -0.0000 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | ast 2024 A1 vs A0, integer-support CRPS, n=27583 rows / 1315 games | +0.0004 | -0.0003 | +0.0011 | game | 0.450 | MDE 0.0007; bias -0.066 -> -0.055; cov80 0.784 -> 0.784; A2 +0.0003, A3 oracle -0.0090, A4 -0.0001 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | fg3m 2023 A1 vs A0, integer-support CRPS, n=27619 rows / 1318 games | -0.0002 | -0.0008 | +0.0005 | game | 0.795 | MDE 0.0007; bias -0.015 -> -0.013; cov80 0.791 -> 0.791; A2 -0.0001, A3 oracle -0.0068, A4 -0.0000 | not holdout (<=2024) |
| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 2e263279) | fg3m 2024 A1 vs A0, integer-support CRPS, n=27583 rows / 1315 games | +0.0004 | -0.0001 | +0.0010 | game | 0.278 | MDE 0.0006; bias -0.001 -> -0.000; cov80 0.784 -> 0.784; A2 +0.0002, A3 oracle -0.0052, A4 +0.0003 | not holdout (<=2024) |

## Drafted holdout row text (not appended)

- No stat confirmed; no holdout row is requested.

## Draft results-section text (for docs/prereg/F11_LINEUP_CONTEXT.md; not appended)

Frozen sha256 (first 8): 2e263279. Checks 1-6: all passed; seasons <= 2024 only, nba.duckdb read-only, CPU, seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}.

* pts: 2023: n=27619 dCRPS +0.0016 [-0.0012, +0.0045] p_BH 0.795 MDE 0.0029 | 2024: n=27583 dCRPS -0.0020 [-0.0040, +0.0002] p_BH 0.278 MDE 0.0021
* reb: 2023: n=27619 dCRPS -0.0002 [-0.0015, +0.0011] p_BH 0.795 MDE 0.0013 | 2024: n=27583 dCRPS -0.0004 [-0.0015, +0.0006] p_BH 0.450 MDE 0.0010
* ast: 2023: n=27619 dCRPS -0.0001 [-0.0010, +0.0008] p_BH 0.795 MDE 0.0009 | 2024: n=27583 dCRPS +0.0004 [-0.0003, +0.0011] p_BH 0.450 MDE 0.0007
* fg3m: 2023: n=27619 dCRPS -0.0002 [-0.0008, +0.0005] p_BH 0.795 MDE 0.0007 | 2024: n=27583 dCRPS +0.0004 [-0.0001, +0.0010] p_BH 0.278 MDE 0.0006

Verdict: confirmed on 2024 = []; passes on 2023 = []; kill criterion 'no stat passes 2023: close' = True. Nothing promoted.

Implementation readings fixed before any arm was fitted (not rule changes): see the module docstring of research/eval/f11_lineup_context.py. The doc's `ContextResidualConfig.lineup_context` flag was not added (nba/ is out of scope); the columns enter through explicit feature-name lists, so production is untouched.

## Reading

F11 is an adequately powered null: no stat reaches the -0.005 floor in either season (pts 2023 +0.0016 [-0.0012, +0.0045], pts 2024 -0.0020 [-0.0040, +0.0002]; reb/ast/fg3m within +/-0.0004 with MDE 0.0006-0.0013, pts MDE 0.0021-0.0029), the placebo (A2) and the no-lift ablation (A4) tie A1, and the mechanism terciles are not monotone, so projected teammate traits weighted by prior-10-game co-play add nothing beyond production's vac_*/n_out_rot features. The oracle built from tonight's actual stints gains -0.058 / -0.031 / -0.009 / -0.005 (pts / reb / ast / fg3m) in 2024 (similar in 2023), which is the size of the leak a careless build would show and says the information is who shares the floor tonight (minutes and rotation), knowable only at T-30; by the pre-registered kill criterion the hypothesis closes with no variant chased.

## Run notes

* nba.duckdb was written by another job at 13:50 (after the lc cache at 13:43 and during the arms stage). The five lc_* columns were rebuilt from the DB afterwards and are byte-identical to the cached ones used by every arm (stints and possessions inputs unchanged for the columns F11 reads).
* The doc's `ContextResidualConfig.lineup_context` flag does not exist (nba/ out of scope): the columns enter through explicit name lists, so "flag off byte-identical" holds by construction and A0 reproduces LOWER_TAIL pts integer CRPS with delta 0.0 (3.152829990762183 / 3.1884109851549947).
* The A0 pts lower-tail PIT (q0.10 = 0.129 / 0.134) already violates the +/-0.02 guard, so the pts guard cannot pass for any arm; it is a production property, not caused by the lc columns.
* Implementation readings (module docstring): window crosses season boundaries; eligible = stint seconds in window and last played team = this team; fallback for the whole row; lift guard (on < 1 or off < 50 decayed possessions -> 0); q* chosen among all eligible incl. OUT; games with no usable report = nobody OUT; rows with no available teammate NaN (0.41% of roster rows, first days of 2022 only).
* Check 2 passes trivially (max gap 0.16 pp): the builder never returns NaN for a reason that depends on playing. lc_cover differs by minutes bucket by design (DNP/bench players more often use the fallback) but it is not NULL.
* Walk-forward month blocks, calendar-month label of season 2024 runs Oct 2024 to Jun 2025; the filter is on season <= 2024, season 2025 (2025-26) never loaded.
