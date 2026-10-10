# F8 lineup rebounding: results (docs/prereg/F8_LINEUP_REBOUNDING.md)

Frozen sha256 `2a8e581e7f03943da4ea7410c10d516d7adca27f58e9c892b5b07d8f0e682240` (first 8 `2a8e581e`, prefix ok: True). git SHA at run `1eff0f03ca96c6034554ed5b16a59d2e7f671339`. data_version 1c5b0901afdf. Seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}. CPU. Seasons <= 2024 only (max loaded 2024); nba.duckdb read-only.

## Gate and tests `[test, ok]`

| test | value | threshold | ok |
|---|---|---|---|
| G3 oreb ratio | 0.99974 | 0.95-1.05 | ok |
| G3 dreb ratio | 0.999962 | 0.95-1.05 | ok |
| G3 oreb team-game corr | 0.999902 | >= 0.95 | ok |
| G3 dreb team-game corr | 0.999969 | >= 0.95 | ok |
| G1 computable share 2023 | 0.966581 | >= 0.9 | ok |
| G1 computable share 2024 | 0.971468 | >= 0.9 | ok |
| G2 team-games with projected big OUT (2024, not tip-gated) | 562 | >= 300 | ok |
| G2 HOU-or-bigs rows (2024) | 7157 | >= 300 | ok |
| R1 bigs rows 2023 (rule 1 power) | 6431 | >= 3000 | ok |
| R1 bigs rows 2024 (rule 1 power) | 6499 | >= 3000 | ok |
| G4 max NULL-rate gap across minutes buckets / reb terciles (pp) | 0.296797 | <= 2.0 | ok |
| PLANT same-game box/stints/possessions/starters edited: lr_* identical through the day | 1 | == 1 | ok |
| PLANT same-game is not inert (later games change) | 1 | == 1 | ok |
| PLANT lr30_* identical for games before the day | 1 | == 1 | ok |
| CONTROL lr30_* (T-30) reacts to flipped starter flags on the day | 1 | == 1 | ok |
| PLANT teammate moved to OUT after the real tip: no change | 1 | == 1 | ok |
| CONTROL same teammate OUT before the tip does change lr_* | 1 | == 1 | ok |
| PLANT future (>= cutoff date) stints/box/possessions: earlier lr_* unchanged | 1 | == 1 | ok |
| PLANT future is not inert (later games change) | 1 | == 1 | ok |

G1 detail:

```
{
 "2022": {
  "n": 25110,
  "computable": 0.9320987654320988,
  "nan_lr_nbig": 0.0
 },
 "2023": {
  "n": 27619,
  "computable": 0.9665809768637532,
  "nan_lr_nbig": 0.0
 },
 "2024": {
  "n": 27583,
  "computable": 0.9714679331472283,
  "nan_lr_nbig": 0.0
 }
}
```

G2:

```
{
 "team_games_big_out_2024": 562,
 "hou_or_bigs_rows_2024": 7157,
 "bigs_rows": {
  "2023": 6431,
  "2024": 6499
 }
}
```

G3:

```
{
 "n_team_games": 7906,
 "oreb_ratio": 0.9997396634558493,
 "oreb_corr": 0.9999017547534611,
 "dreb_ratio": 0.9999615916423413,
 "dreb_corr": 0.9999694618799116
}
```

Missingness audit `[col, bucket, null_rate]`:

| col | bucket | null_rate | n |
|---|---|---|---|
| lr_oreb_sum | DNP | 0.00481 | 19733 |
| lr_oreb_sum | <5 | 0.00419 | 7394 |
| lr_oreb_sum | 5-10 | 0.00322 | 6206 |
| lr_oreb_sum | 10-20 | 0.00431 | 19485 |
| lr_oreb_sum | >20 | 0.00381 | 50967 |
| lr_oreb_sum | reb:T1 | 0.00384 | 32541 |
| lr_oreb_sum | reb:T2 | 0.00368 | 20647 |
| lr_oreb_sum | reb:T3 | 0.00415 | 30864 |
| lr_dreb_sum | DNP | 0.00481 | 19733 |
| lr_dreb_sum | <5 | 0.00419 | 7394 |
| lr_dreb_sum | 5-10 | 0.00322 | 6206 |
| lr_dreb_sum | 10-20 | 0.00431 | 19485 |
| lr_dreb_sum | >20 | 0.00381 | 50967 |
| lr_dreb_sum | reb:T1 | 0.00384 | 32541 |
| lr_dreb_sum | reb:T2 | 0.00368 | 20647 |
| lr_dreb_sum | reb:T3 | 0.00415 | 30864 |
| lr_nbig | DNP | 0.00481 | 19733 |
| lr_nbig | <5 | 0.00419 | 7394 |
| lr_nbig | 5-10 | 0.00322 | 6206 |
| lr_nbig | 10-20 | 0.00431 | 19485 |
| lr_nbig | >20 | 0.00381 | 50967 |
| lr_nbig | reb:T1 | 0.00384 | 32541 |
| lr_nbig | reb:T2 | 0.00368 | 20647 |
| lr_nbig | reb:T3 | 0.00415 | 30864 |
| lr_dbl | DNP | 0.00481 | 19733 |
| lr_dbl | <5 | 0.00419 | 7394 |
| lr_dbl | 5-10 | 0.00322 | 6206 |
| lr_dbl | 10-20 | 0.00431 | 19485 |
| lr_dbl | >20 | 0.00381 | 50967 |
| lr_dbl | reb:T1 | 0.00384 | 32541 |
| lr_dbl | reb:T2 | 0.00368 | 20647 |
| lr_dbl | reb:T3 | 0.00415 | 30864 |
| lr_h5 | DNP | 0.00481 | 19733 |
| lr_h5 | <5 | 0.00419 | 7394 |
| lr_h5 | 5-10 | 0.00322 | 6206 |
| lr_h5 | 10-20 | 0.00431 | 19485 |
| lr_h5 | >20 | 0.00381 | 50967 |
| lr_h5 | reb:T1 | 0.00384 | 32541 |
| lr_h5 | reb:T2 | 0.00368 | 20647 |
| lr_h5 | reb:T3 | 0.00415 | 30864 |
| lr_h2 | DNP | 0.00481 | 19733 |
| lr_h2 | <5 | 0.00419 | 7394 |
| lr_h2 | 5-10 | 0.00322 | 6206 |
| lr_h2 | 10-20 | 0.00431 | 19485 |
| lr_h2 | >20 | 0.00381 | 50967 |
| lr_h2 | reb:T1 | 0.00384 | 32541 |
| lr_h2 | reb:T2 | 0.00368 | 20647 |
| lr_h2 | reb:T3 | 0.00415 | 30864 |
| lr_p_gap | DNP | 0.00537 | 19733 |
| lr_p_gap | <5 | 0.00460 | 7394 |
| lr_p_gap | 5-10 | 0.00354 | 6206 |
| lr_p_gap | 10-20 | 0.00446 | 19485 |
| lr_p_gap | >20 | 0.00406 | 50967 |
| lr_p_gap | reb:T1 | 0.00415 | 32541 |
| lr_p_gap | reb:T2 | 0.00392 | 20647 |
| lr_p_gap | reb:T3 | 0.00434 | 30864 |
| lr_chg | DNP | 0.00988 | 19733 |
| lr_chg | <5 | 0.00825 | 7394 |
| lr_chg | 5-10 | 0.00596 | 6206 |
| lr_chg | 10-20 | 0.00893 | 19485 |
| lr_chg | >20 | 0.00748 | 50967 |
| lr_chg | reb:T1 | 0.00774 | 32541 |
| lr_chg | reb:T2 | 0.00707 | 20647 |
| lr_chg | reb:T3 | 0.00826 | 30864 |
| lr_oreb_pct | DNP | 0.00481 | 19733 |
| lr_oreb_pct | <5 | 0.00419 | 7394 |
| lr_oreb_pct | 5-10 | 0.00322 | 6206 |
| lr_oreb_pct | 10-20 | 0.00431 | 19485 |
| lr_oreb_pct | >20 | 0.00381 | 50967 |
| lr_oreb_pct | reb:T1 | 0.00384 | 32541 |
| lr_oreb_pct | reb:T2 | 0.00368 | 20647 |
| lr_oreb_pct | reb:T3 | 0.00415 | 30864 |
| lr_dreb_pct | DNP | 0.00481 | 19733 |
| lr_dreb_pct | <5 | 0.00419 | 7394 |
| lr_dreb_pct | 5-10 | 0.00322 | 6206 |
| lr_dreb_pct | 10-20 | 0.00431 | 19485 |
| lr_dreb_pct | >20 | 0.00381 | 50967 |
| lr_dreb_pct | reb:T1 | 0.00384 | 32541 |
| lr_dreb_pct | reb:T2 | 0.00368 | 20647 |
| lr_dreb_pct | reb:T3 | 0.00415 | 30864 |
| lr_cover | DNP | 0.00481 | 19733 |
| lr_cover | <5 | 0.00419 | 7394 |
| lr_cover | 5-10 | 0.00322 | 6206 |
| lr_cover | 10-20 | 0.00431 | 19485 |
| lr_cover | >20 | 0.00381 | 50967 |
| lr_cover | reb:T1 | 0.00384 | 32541 |
| lr_cover | reb:T2 | 0.00368 | 20647 |
| lr_cover | reb:T3 | 0.00415 | 30864 |
| lr30_dbl | DNP | 0.00481 | 19733 |
| lr30_dbl | <5 | 0.00419 | 7394 |
| lr30_dbl | 5-10 | 0.00322 | 6206 |
| lr30_dbl | 10-20 | 0.00431 | 19485 |
| lr30_dbl | >20 | 0.00381 | 50967 |
| lr30_dbl | reb:T1 | 0.00384 | 32541 |
| lr30_dbl | reb:T2 | 0.00368 | 20647 |
| lr30_dbl | reb:T3 | 0.00415 | 30864 |
| lr30_h5 | DNP | 0.00481 | 19733 |
| lr30_h5 | <5 | 0.00419 | 7394 |
| lr30_h5 | 5-10 | 0.00322 | 6206 |
| lr30_h5 | 10-20 | 0.00431 | 19485 |
| lr30_h5 | >20 | 0.00381 | 50967 |
| lr30_h5 | reb:T1 | 0.00384 | 32541 |
| lr30_h5 | reb:T2 | 0.00368 | 20647 |
| lr30_h5 | reb:T3 | 0.00415 | 30864 |
| lr30_h2 | DNP | 0.00481 | 19733 |
| lr30_h2 | <5 | 0.00419 | 7394 |
| lr30_h2 | 5-10 | 0.00322 | 6206 |
| lr30_h2 | 10-20 | 0.00431 | 19485 |
| lr30_h2 | >20 | 0.00381 | 50967 |
| lr30_h2 | reb:T1 | 0.00384 | 32541 |
| lr30_h2 | reb:T2 | 0.00368 | 20647 |
| lr30_h2 | reb:T3 | 0.00415 | 30864 |

## A0 reproduction

```
{
 "ok": true,
 "detail": {
  "reb_2023": {
   "got": 1.2928172686712307,
   "stored": 1.2928172686712307,
   "delta": 0.0,
   "n": 27619,
   "n_ref": 27619,
   "ok": true
  },
  "reb_2024": {
   "got": 1.3139946716237645,
   "stored": 1.3139946716237645,
   "delta": 0.0,
   "n": 27583,
   "n_ref": 27583,
   "ok": true
  },
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

## Results `[stat, season, arm, snapshot, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`

dcrps = arm - A0 (T-30 arms: A1_30 - A0_30), integer support, game-clustered 95% CI, 2000 resamples, seed 0.

| stat | season | arm | snap | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| reb | 2023 | A0 | T60 | 27619 | 1318 | 1.2928 |  |  |  |  |  |  | -0.043 | 0.792 | 0.113 | 0.209 | 0.3440 |
| reb | 2023 | A1 | T60 | 27619 | 1318 | 1.2927 | -0.0001 | -0.0015 | +0.0013 | 0.885 | 0.885 | 0.0014 | -0.058 | 0.792 | 0.114 | 0.210 | 0.3441 |
| reb | 2023 | A2 | T60 | 27619 | 1318 | 1.2926 | -0.0002 | -0.0015 | +0.0011 | 0.796 |  | 0.0013 | -0.055 | 0.791 | 0.115 | 0.209 | 0.3440 |
| reb | 2023 | A3 | T60 | 27619 | 1318 | 1.2935 | +0.0007 | -0.0005 | +0.0019 | 0.255 |  | 0.0012 | -0.049 | 0.793 | 0.113 | 0.210 | 0.3442 |
| reb | 2023 | A4 | T60 | 27619 | 1318 | 1.2768 | -0.0160 | -0.0182 | -0.0137 | 0.000 |  | 0.0023 | -0.061 | 0.793 | 0.113 | 0.211 | 0.3419 |
| reb | 2023 | A0_30 | T30 | 27619 | 1318 | 1.2761 |  |  |  |  |  |  | -0.021 | 0.793 | 0.112 | 0.208 | 0.3392 |
| reb | 2023 | A1_30 | T30 | 27619 | 1318 | 1.2753 | -0.0008 | -0.0022 | +0.0005 | 0.213 |  | 0.0013 | -0.030 | 0.792 | 0.113 | 0.208 | 0.3391 |
| reb | 2024 | A0 | T60 | 27583 | 1315 | 1.3140 |  |  |  |  |  |  | -0.051 | 0.787 | 0.116 | 0.215 | 0.3531 |
| reb | 2024 | A1 | T60 | 27583 | 1315 | 1.3141 | +0.0002 | -0.0010 | +0.0013 | 0.757 | 0.757 | 0.0012 | -0.048 | 0.787 | 0.116 | 0.213 | 0.3530 |
| reb | 2024 | A2 | T60 | 27583 | 1315 | 1.3140 | -0.0000 | -0.0011 | +0.0011 | 0.989 |  | 0.0011 | -0.046 | 0.789 | 0.114 | 0.213 | 0.3530 |
| reb | 2024 | A3 | T60 | 27583 | 1315 | 1.3151 | +0.0011 | +0.0002 | +0.0020 | 0.015 |  | 0.0009 | -0.046 | 0.788 | 0.114 | 0.214 | 0.3534 |
| reb | 2024 | A4 | T60 | 27583 | 1315 | 1.3012 | -0.0128 | -0.0149 | -0.0108 | 0.000 |  | 0.0021 | -0.056 | 0.790 | 0.113 | 0.215 | 0.3513 |
| reb | 2024 | A0_30 | T30 | 27583 | 1315 | 1.2963 |  |  |  |  |  |  | -0.059 | 0.788 | 0.115 | 0.214 | 0.3483 |
| reb | 2024 | A1_30 | T30 | 27583 | 1315 | 1.2965 | +0.0001 | -0.0010 | +0.0013 | 0.827 |  | 0.0011 | -0.055 | 0.789 | 0.114 | 0.213 | 0.3483 |
| pts | 2023 | A0 | T60 | 27619 | 1318 | 3.1528 |  |  |  |  |  |  | -0.149 | 0.774 | 0.129 | 0.221 | 0.3699 |
| pts | 2023 | A1 | T60 | 27619 | 1318 | 3.1563 | +0.0035 | +0.0008 | +0.0063 | 0.017 | 0.034 | 0.0028 | -0.171 | 0.774 | 0.131 | 0.222 | 0.3705 |
| pts | 2023 | A2 | T60 | 27619 | 1318 | 3.1538 | +0.0010 | -0.0018 | +0.0035 | 0.440 |  | 0.0027 | -0.181 | 0.773 | 0.132 | 0.223 | 0.3699 |
| pts | 2023 | A3 | T60 | 27619 | 1318 | 3.1547 | +0.0019 | -0.0010 | +0.0047 | 0.183 |  | 0.0028 | -0.163 | 0.774 | 0.131 | 0.222 | 0.3705 |
| pts | 2023 | A4 | T60 | 27619 | 1318 | 3.1201 | -0.0327 | -0.0376 | -0.0279 | 0.000 |  | 0.0048 | -0.194 | 0.776 | 0.129 | 0.225 | 0.3682 |
| pts | 2023 | A0_30 | T30 | 27619 | 1318 | 3.1148 |  |  |  |  |  |  | -0.087 | 0.773 | 0.129 | 0.220 | 0.3651 |
| pts | 2023 | A1_30 | T30 | 27619 | 1318 | 3.1168 | +0.0020 | -0.0008 | +0.0047 | 0.150 |  | 0.0027 | -0.103 | 0.772 | 0.131 | 0.221 | 0.3654 |
| pts | 2024 | A0 | T60 | 27583 | 1315 | 3.1884 |  |  |  |  |  |  | -0.113 | 0.764 | 0.134 | 0.225 | 0.3818 |
| pts | 2024 | A1 | T60 | 27583 | 1315 | 3.1890 | +0.0006 | -0.0017 | +0.0029 | 0.628 | 0.757 | 0.0023 | -0.116 | 0.763 | 0.135 | 0.226 | 0.3818 |
| pts | 2024 | A2 | T60 | 27583 | 1315 | 3.1884 | -0.0000 | -0.0021 | +0.0021 | 0.986 |  | 0.0021 | -0.124 | 0.764 | 0.135 | 0.227 | 0.3816 |
| pts | 2024 | A3 | T60 | 27583 | 1315 | 3.1904 | +0.0020 | -0.0003 | +0.0042 | 0.086 |  | 0.0022 | -0.102 | 0.764 | 0.134 | 0.226 | 0.3820 |
| pts | 2024 | A4 | T60 | 27583 | 1315 | 3.1592 | -0.0292 | -0.0339 | -0.0245 | 0.000 |  | 0.0047 | -0.122 | 0.767 | 0.131 | 0.226 | 0.3804 |
| pts | 2024 | A0_30 | T30 | 27583 | 1315 | 3.1450 |  |  |  |  |  |  | -0.123 | 0.764 | 0.135 | 0.226 | 0.3755 |
| pts | 2024 | A1_30 | T30 | 27583 | 1315 | 3.1441 | -0.0010 | -0.0032 | +0.0011 | 0.357 |  | 0.0022 | -0.120 | 0.766 | 0.134 | 0.226 | 0.3754 |

## Slices `[stat, season, slice, n, dcrps, ci_lo, ci_hi]` (A1 - A0; n >= 300 enforced)

| stat | season | snap | slice | n | dcrps | ci_lo | ci_hi | p | p_bh |
|---|---|---|---|---|---|---|---|---|---|
| reb | 2023 | T60 | all | 27619 | -0.0001 | -0.0015 | +0.0013 | 0.885 |  |
| reb | 2023 | T60 | bigs | 6431 | -0.0002 | -0.0046 | +0.0042 | 0.949 | 0.949 |
| reb | 2023 | T60 | guards | 10854 | +0.0008 | -0.0006 | +0.0024 | 0.299 |  |
| reb | 2023 | T60 | others | 10334 | -0.0010 | -0.0029 | +0.0009 | 0.282 |  |
| reb | 2023 | T60 | HOU | 863 | +0.0005 | -0.0077 | +0.0090 | 0.921 |  |
| reb | 2023 | T60 | lineup_change | 4607 | -0.0014 | -0.0054 | +0.0026 | 0.490 |  |
| reb | 2023 | T60 | lr_chg!=0 | 27619 | -0.0001 | -0.0015 | +0.0013 | 0.885 |  |
| reb | 2023 | T60 | first15 | 4586 | +0.0011 | -0.0029 | +0.0051 | 0.581 |  |
| reb | 2023 | T60 | teammate_out | 23246 | -0.0004 | -0.0020 | +0.0011 | 0.607 |  |
| reb | 2023 | T60 | starter | 12267 | -0.0029 | -0.0054 | -0.0005 | 0.024 |  |
| reb | 2023 | T60 | bench | 15352 | +0.0021 | +0.0006 | +0.0037 | 0.006 |  |
| reb | 2023 | T30 | all | 27619 | -0.0008 | -0.0022 | +0.0005 | 0.213 |  |
| reb | 2023 | T30 | bigs | 6431 | -0.0009 | -0.0049 | +0.0030 | 0.665 |  |
| reb | 2024 | T60 | all | 27583 | +0.0002 | -0.0010 | +0.0013 | 0.757 |  |
| reb | 2024 | T60 | bigs | 6499 | -0.0009 | -0.0047 | +0.0025 | 0.643 | 0.643 |
| reb | 2024 | T60 | guards | 10873 | -0.0005 | -0.0016 | +0.0007 | 0.406 |  |
| reb | 2024 | T60 | others | 10211 | +0.0015 | -0.0001 | +0.0032 | 0.066 |  |
| reb | 2024 | T60 | HOU | 909 | +0.0054 | -0.0028 | +0.0139 | 0.213 |  |
| reb | 2024 | T60 | lineup_change | 5608 | -0.0006 | -0.0036 | +0.0025 | 0.698 |  |
| reb | 2024 | T60 | lr_chg!=0 | 27583 | +0.0002 | -0.0010 | +0.0013 | 0.757 |  |
| reb | 2024 | T60 | first15 | 4621 | +0.0003 | -0.0029 | +0.0033 | 0.887 |  |
| reb | 2024 | T60 | teammate_out | 23115 | +0.0003 | -0.0010 | +0.0017 | 0.677 |  |
| reb | 2024 | T60 | starter | 12057 | -0.0000 | -0.0021 | +0.0020 | 0.991 |  |
| reb | 2024 | T60 | bench | 15526 | +0.0003 | -0.0011 | +0.0017 | 0.680 |  |
| reb | 2024 | T30 | all | 27583 | +0.0001 | -0.0010 | +0.0013 | 0.827 |  |
| reb | 2024 | T30 | bigs | 6499 | +0.0001 | -0.0035 | +0.0037 | 0.991 |  |
| pts | 2023 | T60 | all | 27619 | +0.0035 | +0.0008 | +0.0063 | 0.017 |  |
| pts | 2023 | T60 | bigs | 6431 | -0.0027 | -0.0080 | +0.0029 | 0.346 | 0.692 |
| pts | 2023 | T60 | guards | 10854 | +0.0062 | +0.0012 | +0.0112 | 0.013 |  |
| pts | 2023 | T60 | others | 10334 | +0.0045 | +0.0002 | +0.0087 | 0.041 |  |
| pts | 2023 | T60 | HOU | 863 | +0.0055 | -0.0113 | +0.0227 | 0.537 |  |
| pts | 2023 | T60 | lineup_change | 4607 | +0.0157 | +0.0086 | +0.0228 | 0.000 |  |
| pts | 2023 | T60 | lr_chg!=0 | 27619 | +0.0035 | +0.0008 | +0.0063 | 0.017 |  |
| pts | 2023 | T60 | first15 | 4586 | +0.0097 | +0.0021 | +0.0175 | 0.014 |  |
| pts | 2023 | T60 | teammate_out | 23246 | +0.0035 | +0.0005 | +0.0066 | 0.015 |  |
| pts | 2023 | T60 | starter | 12267 | +0.0025 | -0.0025 | +0.0078 | 0.343 |  |
| pts | 2023 | T60 | bench | 15352 | +0.0043 | +0.0012 | +0.0072 | 0.003 |  |
| pts | 2023 | T30 | all | 27619 | +0.0020 | -0.0008 | +0.0047 | 0.150 |  |
| pts | 2023 | T30 | bigs | 6431 | +0.0019 | -0.0032 | +0.0069 | 0.440 |  |
| pts | 2024 | T60 | all | 27583 | +0.0006 | -0.0017 | +0.0029 | 0.628 |  |
| pts | 2024 | T60 | bigs | 6499 | +0.0038 | -0.0005 | +0.0080 | 0.086 | 0.172 |
| pts | 2024 | T60 | guards | 10873 | -0.0011 | -0.0051 | +0.0031 | 0.589 |  |
| pts | 2024 | T60 | others | 10211 | +0.0003 | -0.0033 | +0.0039 | 0.810 |  |
| pts | 2024 | T60 | HOU | 909 | -0.0048 | -0.0186 | +0.0100 | 0.537 |  |
| pts | 2024 | T60 | lineup_change | 5608 | +0.0015 | -0.0038 | +0.0068 | 0.605 |  |
| pts | 2024 | T60 | lr_chg!=0 | 27583 | +0.0006 | -0.0017 | +0.0029 | 0.628 |  |
| pts | 2024 | T60 | first15 | 4621 | +0.0071 | +0.0003 | +0.0138 | 0.043 |  |
| pts | 2024 | T60 | teammate_out | 23115 | +0.0004 | -0.0022 | +0.0030 | 0.736 |  |
| pts | 2024 | T60 | starter | 12057 | -0.0025 | -0.0068 | +0.0015 | 0.242 |  |
| pts | 2024 | T60 | bench | 15526 | +0.0030 | +0.0002 | +0.0056 | 0.032 |  |
| pts | 2024 | T30 | all | 27583 | -0.0010 | -0.0032 | +0.0011 | 0.357 |  |
| pts | 2024 | T30 | bigs | 6499 | -0.0002 | -0.0046 | +0.0040 | 0.881 |  |

## Mechanism (descriptive)

| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi | bias_A0 | bias_A1 |
|---|---|---|---|---|---|---|---|---|---|
| reb | 2023 | lr_nbig | T1 | 9207 | -0.0007 | -0.0027 | +0.0015 |  |  |
| reb | 2023 | lr_nbig | T2 | 9206 | -0.0005 | -0.0030 | +0.0020 |  |  |
| reb | 2023 | lr_nbig | T3 | 9206 | +0.0009 | -0.0018 | +0.0035 |  |  |
| reb | 2023 | HOU_bias | HOU | 863 |  |  |  | +0.024 | +0.059 |
| reb | 2024 | lr_nbig | T1 | 9195 | +0.0013 | -0.0004 | +0.0030 |  |  |
| reb | 2024 | lr_nbig | T2 | 9194 | -0.0006 | -0.0028 | +0.0016 |  |  |
| reb | 2024 | lr_nbig | T3 | 9194 | -0.0002 | -0.0022 | +0.0019 |  |  |
| reb | 2024 | HOU_bias | HOU | 909 |  |  |  | -0.105 | +0.016 |
| pts | 2023 | lr_nbig | T1 | 9207 | +0.0045 | -0.0000 | +0.0092 |  |  |
| pts | 2023 | lr_nbig | T2 | 9206 | +0.0006 | -0.0038 | +0.0051 |  |  |
| pts | 2023 | lr_nbig | T3 | 9206 | +0.0053 | -0.0002 | +0.0110 |  |  |
| pts | 2023 | HOU_bias | HOU | 863 |  |  |  | +0.004 | -0.054 |
| pts | 2024 | lr_nbig | T1 | 9195 | -0.0006 | -0.0042 | +0.0034 |  |  |
| pts | 2024 | lr_nbig | T2 | 9194 | +0.0011 | -0.0030 | +0.0052 |  |  |
| pts | 2024 | lr_nbig | T3 | 9194 | +0.0013 | -0.0027 | +0.0055 |  |  |
| pts | 2024 | HOU_bias | HOU | 909 |  |  |  | -0.040 | -0.139 |

## Attack inputs (A1 minus other; arm gains vs A0)

| stat | season | scope | A1-A2 | A1-A3 | A1-A4 | A2 gain | A3 gain | A4 gain |
|---|---|---|---|---|---|---|---|---|
| reb | 2023 | all | +0.0001 [-0.0010, +0.0012] | -0.0008 [-0.0021, +0.0005] | +0.0159 [+0.0137, +0.0181] | -0.0002 | +0.0007 | -0.0160 |
| reb | 2023 | bigs | +0.0001 [-0.0032, +0.0034] | -0.0027 [-0.0066, +0.0012] | +0.0157 [+0.0107, +0.0209] | -0.0003 | +0.0025 | -0.0159 |
| reb | 2024 | all | +0.0002 [-0.0007, +0.0011] | -0.0009 [-0.0020, +0.0001] | +0.0130 [+0.0111, +0.0149] | -0.0000 | +0.0011 | -0.0128 |
| reb | 2024 | bigs | +0.0013 [-0.0015, +0.0042] | -0.0032 [-0.0067, +0.0003] | +0.0162 [+0.0112, +0.0217] | -0.0022 | +0.0023 | -0.0170 |
| pts | 2023 | all | +0.0025 [-0.0002, +0.0053] | +0.0016 [-0.0014, +0.0043] | +0.0362 [+0.0313, +0.0409] | +0.0010 | +0.0019 | -0.0327 |
| pts | 2023 | bigs | -0.0023 [-0.0075, +0.0029] | +0.0014 [-0.0042, +0.0068] | +0.0195 [+0.0115, +0.0276] | -0.0004 | -0.0041 | -0.0223 |
| pts | 2024 | all | +0.0006 [-0.0015, +0.0027] | -0.0014 [-0.0037, +0.0007] | +0.0298 [+0.0253, +0.0346] | -0.0000 | +0.0020 | -0.0292 |
| pts | 2024 | bigs | +0.0055 [+0.0016, +0.0095] | +0.0010 [-0.0032, +0.0048] | +0.0229 [+0.0152, +0.0312] | -0.0017 | +0.0028 | -0.0192 |

## Rules

```
{
 "reb": {
  "rule1_primary_bigs": false,
  "rule1_no_harm": {
   "2023": true,
   "2024": true
  },
  "rule1": false,
  "rule2_structure_beyond_sum": false,
  "rule3_guards": {
   "abs_bias<=0.5": true,
   "cov80_in_[.75,.85]": true,
   "pit_q10_within_.02": true,
   "no_slice_worse_than_+0.01": true
  },
  "rule3": true,
  "slices_worse": {},
  "rule4_attacks_ex_knockout": {
   "A1_minus_A3<=-0.003 (bigs 2024)": true,
   "A1_gain_smaller_than_A4_gain (bigs 2024)": true,
   "planted_tests_pass": true
  },
  "rule4a": true,
  "leak_flag_A1_not_below_A4": false,
  "all_ex_knockout": false,
  "knockout_needed": false,
  "final_pass": false
 },
 "pts": {
  "rule1_primary_bigs": false,
  "rule1_no_harm": {
   "2023": false,
   "2024": true
  },
  "rule1": false,
  "rule2_structure_beyond_sum": false,
  "rule3_guards": {
   "abs_bias<=0.5": true,
   "cov80_in_[.75,.85]": true,
   "pit_q10_within_.02": false,
   "no_slice_worse_than_+0.01": true
  },
  "rule3": false,
  "slices_worse": {},
  "rule4_attacks_ex_knockout": {
   "A1_minus_A3<=-0.003 (bigs 2024)": false,
   "A1_gain_smaller_than_A4_gain (bigs 2024)": true,
   "planted_tests_pass": true
  },
  "rule4a": false,
  "leak_flag_A1_not_below_A4": false,
  "all_ex_knockout": false,
  "knockout_needed": false,
  "final_pass": false
 }
}
```

## Unstated details, chosen before scoring

* big = height_in >= 82 or position contains Center, used for is_big_p, is_big_q and the bigs slice (header item (b)); guards = height_in < 78; others = every remaining played row (incl. unknown height)
* co-play weights c(p,q): F11 construction (prior 10 team games, HL 10, eligible = stint seconds in the window and last team before the date = this team, OUT removed, minutes-share fallback when p has < 300 window seconds, sum c = 4)
* S(p) T-60 = p + the 4 available teammates with the largest c (ties: more starts in the prior 10 team games, then lower player id); shorter when fewer than 4 are available
* T-30: tonight's box-score starter flags are the proxy of the confirmed five (docs/LINEUPS_KNOWN.md, optimistic); used only when exactly five are flagged for the team-game, else T-60 S. Starter p: the five starters; bench p: p + the 4 starters with the largest prior-10 co-play weight. c is NOT re-weighted at T-30 (groups A, C and lr_nbig identical), only lr_dbl, lr_h5, lr_h2 change
* T-30 comparison arm A0_30 = A0 + the production t30_* columns (stat_feature_names(lineups_known=True)); A1_30 = A0_30 + A1 columns with the three lr30_* replacements; dCRPS at T-30 is A1_30 - A0_30
* group C: OREB% = sum n_oreb / sum (n_oreb + n_dreb) over the possessions p was on the floor on offence (every rebound event of those possessions is a rebound chance), DREB% the same on defence, cumulative over all prior games (no decay), pseudo-count 1,500 chances toward the as-of team-season rate (league as-of rate if the team has no earlier game that season)
* lr_p_gap baseline = as-of mean lr_nbig of played rows with the same position code (G/F/C/other) in the same season (all seasons when < 200 rows)
* lr_chg = lr_nbig minus the mean lr_nbig of the team's played rows over its previous 10 games
* projected big OUT (lineup-change slice) = a big whose mean minutes over the games he played among the team's previous 10 is >= 20 is on the real-tip-gated OUT list
* A2 = A0 + lr_oreb_sum + lr_dreb_sum; A3 permutes all lr_* / lr30_* columns jointly across rows within team-season (seed 0); A4 uses tonight's actual stints for c and S
* BH family {reb, pts} per season: the table p / p_bh use the all-rows A1-A0 p; rule 1 uses the bigs-slice p with BH over {reb, pts}
* rules 2 and 4 (A1 vs A2, A1 vs A3, A1 vs A4) are evaluated on the bigs slice in 2024 (the slice of rule 1); the all-rows values are reported beside them
* rule 3 guards use A1 all rows in 2024 (2023 reported); the slice guard uses the T-60 A1-A0 slice table (declared slices only, all excluded)
* knockout (rule 4) is run only if rules 1-4 otherwise pass; a group 'carries' the gain when the A1 bigs-slice gain without it is <= 20% of the A1 gain (>= 80% lost)
* G2 team-games use the not-tip-gated union of player_availability 'out' rows (coverage count)
* G4 gap over the four realised-minutes buckets (<5, 5-10, 10-20, >20) and the three reb terciles per season; DNP rows are audited but outside the gap
* tll = mean threshold log loss of P(y >= n) at reb 4/6/8/10 and pts 10/15/20 on the integer grid
* A0 reproduction: integer CRPS vs reports/lower_tail/candidates.json 'off' (n and crps_int) to 1e-6