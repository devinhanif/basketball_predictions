# XGB_PTS_VS_LT results (sha256 `c74f1d1a88752103e6161b9e919d4a804aca20a101b387514999dd541418b8b7`)

| season | n | games | CRPS A0 | CRPS A1 | CRPS A2 | A1 - A0 [95% CI] | p | BH |
|---|---|---|---|---|---|---|---|---|
| 2023 | 27619 | 1318 | 3.1402 | 3.1366 | 3.1445 | -0.0036 [-0.0094, +0.0019] | 0.2150 | 0.4300 |
| 2024 | 27583 | 1315 | 3.1779 | 3.1778 | 3.1801 | -0.0000 [-0.0063, +0.0060] | 0.9850 | 0.9850 |

## Secondary and descriptive contrasts

| season | contrast | delta [95% CI] |
|---|---|---|
| 2023 | A2-A0 | +0.0043 [-0.0011, +0.0097] |
| 2023 | A1-A2 | -0.0079 [-0.0103, -0.0055] |
| 2023 | A1_stored-A0 | -0.0036 [-0.0094, +0.0019] |
| 2023 | A1_fixsched-A0 | -0.0034 [-0.0092, +0.0020] |
| 2023 | threshold log loss A1 - A0, DK T-60 main lines (n 14354) | -0.0021 [-0.0038, -0.0004] |
| 2023 | threshold log loss A2 - A0, DK T-60 main lines | -0.0020 [-0.0036, -0.0004] |
| 2023 | threshold log loss A1 - A0 at pts >= 10 (all rows) | +0.0002 [-0.0012, +0.0017] |
| 2023 | threshold log loss A1 - A0 at pts >= 15 (all rows) | -0.0010 [-0.0024, +0.0003] |
| 2023 | threshold log loss A1 - A0 at pts >= 20 (all rows) | +0.0005 [-0.0009, +0.0021] |
| 2023 | threshold log loss A1 - A0 at pts >= 25 (all rows) | +0.0003 [-0.0011, +0.0018] |
| 2023 | threshold log loss A1 - A0 at pts >= 30 (all rows) | +0.0010 [-0.0004, +0.0025] |
| 2023 | drop the top-share team (team 1610612737, share 0.265) | -0.0027 [-0.0087, +0.0029] |
| 2024 | A2-A0 | +0.0023 [-0.0037, +0.0079] |
| 2024 | A1-A2 | -0.0023 [-0.0042, -0.0003] |
| 2024 | A1_stored-A0 | -0.0000 [-0.0063, +0.0060] |
| 2024 | A1_fixsched-A0 | -0.0003 [-0.0067, +0.0057] |
| 2024 | threshold log loss A1 - A0, DK T-60 main lines (n 15882) | +0.0011 [-0.0003, +0.0024] |
| 2024 | threshold log loss A2 - A0, DK T-60 main lines | +0.0011 [-0.0001, +0.0023] |
| 2024 | threshold log loss A1 - A0 at pts >= 10 (all rows) | -0.0021 [-0.0037, -0.0005] |
| 2024 | threshold log loss A1 - A0 at pts >= 15 (all rows) | +0.0009 [-0.0007, +0.0025] |
| 2024 | threshold log loss A1 - A0 at pts >= 20 (all rows) | +0.0030 [+0.0010, +0.0047] |
| 2024 | threshold log loss A1 - A0 at pts >= 25 (all rows) | +0.0037 [+0.0021, +0.0054] |
| 2024 | threshold log loss A1 - A0 at pts >= 30 (all rows) | +0.0019 [+0.0007, +0.0033] |
| 2024 | drop the top-share team (team 1610612755, share 59.768) | +0.0022 [-0.0042, +0.0083] |

## Slices (A1 - A0)

| season | slice | n | delta [95% CI] |
|---|---|---|---|
| 2023 | all | 27619 | -0.0036 [-0.0094, +0.0019] |
| 2023 | regular | 25815 | -0.0052 [-0.0109, +0.0007] |
| 2023 | postseason | 1804 | +0.0197 [-0.0020, +0.0401] |
| 2023 | minutes>=5 | 25236 | -0.0209 [-0.0270, -0.0151] |
| 2023 | minutes<5 | 2383 | +0.1796 [+0.1616, +0.1974] |
| 2023 | starter | 12267 | -0.0132 [-0.0226, -0.0038] |
| 2023 | bench | 15352 | +0.0040 [-0.0037, +0.0111] |
| 2023 | first15 | 4586 | -0.0015 [-0.0173, +0.0151] |
| 2023 | rest_of_season | 23033 | -0.0040 [-0.0102, +0.0015] |
| 2023 | without_PHI | 26662 | -0.0037 [-0.0093, +0.0019] |
| 2023 | starter_actual | 13139 | -0.0177 [-0.0269, -0.0084] |
| 2023 | tip>=19ET(proxy-safe) | 24479 | -0.0034 [-0.0093, +0.0023] |
| 2023 | tip<19ET(proxy-exposed) | 3140 | -0.0053 [-0.0266, +0.0166] |
| 2024 | all | 27583 | -0.0000 [-0.0063, +0.0060] |
| 2024 | regular | 25649 | -0.0041 [-0.0108, +0.0020] |
| 2024 | postseason | 1934 | +0.0539 [+0.0319, +0.0752] |
| 2024 | minutes>=5 | 25279 | -0.0148 [-0.0214, -0.0085] |
| 2024 | minutes<5 | 2304 | +0.1620 [+0.1448, +0.1800] |
| 2024 | starter | 12057 | -0.0039 [-0.0120, +0.0037] |
| 2024 | bench | 15526 | +0.0030 [-0.0066, +0.0122] |
| 2024 | first15 | 4621 | +0.0128 [+0.0004, +0.0250] |
| 2024 | rest_of_season | 22962 | -0.0026 [-0.0099, +0.0041] |
| 2024 | without_PHI | 26785 | +0.0022 [-0.0042, +0.0083] |
| 2024 | starter_actual | 13112 | -0.0034 [-0.0119, +0.0048] |
| 2024 | tip>=19ET(proxy-safe) | 24220 | +0.0058 [+0.0002, +0.0117] |
| 2024 | tip<19ET(proxy-exposed) | 3363 | -0.0420 [-0.0762, -0.0150] |

## Calibration (integer support)

| season | arm | bias | cov80 | PIT guard ok |
|---|---|---|---|---|
| 2023 | A0 | -0.279 | 0.792 | True |
| 2023 | A1 | -0.193 | 0.791 | False |
| 2023 | A2 | -0.295 | 0.794 | False |
| 2024 | A0 | -0.196 | 0.783 | True |
| 2024 | A1 | -0.021 | 0.785 | True |
| 2024 | A2 | -0.065 | 0.787 | True |

## Rule

```
{
 "P1_2023": {
  "floor": false,
  "ci_hi<0": false,
  "bh<0.05": false
 },
 "P2_2024": {
  "floor": false,
  "ci_hi<0": false,
  "bh<0.05": false
 },
 "P3_tll_by_season": {
  "2023": true,
  "2024": true
 },
 "P4": {
  "abs_bias<=0.5": true,
  "cov80_in_[.75,.85]": true,
  "pit_guard": true
 },
 "P5_slices_worse": {
  "postseason": 0.05385316254561694,
  "first15": 0.012832631117557345
 },
 "P6_by_season": {
  "2023": true,
  "2024": true
 },
 "P7": {
  "top_team_share<=0.25": false
 },
 "P8": {
  "proxy_safe_delta<=floor": false
 },
 "conditions": {
  "P1_2023": false,
  "P2_2024": false,
  "P3_tll": true,
  "P4_calibration": true,
  "P5_slices": false,
  "P6_minutes>=5_no_harm": true,
  "P7_concentration": false,
  "P8_proxy_safe": false
 },
 "gate_ok": true,
 "refit_matches_stored": true,
 "pass": false,
 "closed_no_pass_2023": true,
 "verdict": "CLOSED"
}
```
