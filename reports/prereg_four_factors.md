# FOUR_FACTORS results

Rule: docs/prereg/FOUR_FACTORS.md, frozen sha256 0d87c2cea7ad257798918927b4065070ac8b6585d3fa67d5ec469f4d7a67f441 (first 8: 0d87c2ce), commit 918e03c. Run git sha 3a729d73f252dba327bbaba72139125d192f280f. Seeds {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}; bootstrap 2000 game-clustered. Season 2025 never loaded.

## Minimum-data checks

| test | value | threshold | ok |
|---|---|---|---|
| 1 possessions coverage 2022 | 1.0 | >= 0.95 | True |
| 1 possessions coverage 2023 | 1.0 | >= 0.95 | True |
| 1 possessions coverage 2024 | 1.0 | >= 0.95 | True |
| 2 team-game possession counts in [80,130] | 0.9998735137869972 | >= 0.995 | True |
| 3 box inputs non-null (team-game sums / played rows) | [0, 0] | == 0 | True |
| 4 possession points vs box points mean |diff| | {"mean": 0.016063749051353403, "p99": 0.0} | <= 0.5 | True |
| 5 max NULL-rate gap across minutes buckets / outcome terciles (pp) | 0.0 | <= 2.0 | True |
| 6 PLANT same-game possessions/box/overtime edited: ff identical through the day | 1 | == 1 | True |
| 6 PLANT same-game is not inert (later games change) | 1 | == 1 | True |
| 6 PLANT overtime count was actually edited | 1 | == 1 | True |
| 6 PLANT future (>= cutoff date) edits: earlier ff unchanged | 1 | == 1 | True |
| 6 PLANT future is not inert (later games change) | 1 | == 1 | True |
| 7 A0 names == production stat_feature_names (flag off) | 1 | == 1 | True |
| 7 A0 reproduces production integer CRPS and n to 1e-6 (4 stats x 2 seasons) | 1 | == 1 | True |

Possession counts outside [80,130]: 1 of 7906 team-games (league-mean fallback).

## Verdict per stat

| stat | A1 pass | A2 pass | stat pass | pace carries | rule1 A1 (2023/2024) | rule1 A2 (2023/2024) |
|---|---|---|---|---|---|---|
| pts | False | False | False | False | False/False | False/False |
| reb | False | False | False | False | False/False | False/False |
| ast | False | False | False | False | False/False | False/False |
| fg3m | False | False | False | False | False/False | False/False |

Close (no stat passes rule 1 on 2023 under either family): True

## Arms table

| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 2023 | A0 | 27619 | 1318 | 3.1528 | n/a | n/a | n/a | n/a | n/a | n/a | -0.149 | 0.772 | 0.132 | 0.222 | 0.4179 |
| pts | 2023 | A1 | 27619 | 1318 | 3.1539 | +0.0011 | -0.0013 | +0.0035 | 0.387 | 0.774 | 0.0024 | -0.166 | 0.772 | 0.133 | 0.223 | 0.4181 |
| pts | 2023 | A2 | 27619 | 1318 | 3.1544 | +0.0016 | -0.0012 | +0.0044 | 0.280 | 0.373 | 0.0028 | -0.171 | 0.770 | 0.134 | 0.224 | 0.4182 |
| pts | 2023 | A3 | 27619 | 1318 | 3.1535 | +0.0007 | -0.0022 | +0.0035 | 0.620 | n/a | 0.0029 | -0.149 | 0.774 | 0.130 | 0.221 | 0.4179 |
| pts | 2023 | A4 | 27619 | 1318 | 3.1541 | +0.0013 | -0.0014 | +0.0041 | 0.352 | n/a | 0.0028 | -0.171 | 0.771 | 0.134 | 0.224 | 0.4181 |
| pts | 2024 | A0 | 27583 | 1315 | 3.1884 | n/a | n/a | n/a | n/a | n/a | n/a | -0.113 | 0.764 | 0.135 | 0.225 | 0.4326 |
| pts | 2024 | A1 | 27583 | 1315 | 3.1899 | +0.0014 | -0.0005 | +0.0034 | 0.150 | 0.300 | 0.0020 | -0.141 | 0.764 | 0.136 | 0.227 | 0.4328 |
| pts | 2024 | A2 | 27583 | 1315 | 3.1883 | -0.0001 | -0.0025 | +0.0022 | 0.948 | 0.948 | 0.0024 | -0.136 | 0.763 | 0.137 | 0.227 | 0.4327 |
| pts | 2024 | A3 | 27583 | 1315 | 3.1907 | +0.0023 | -0.0000 | +0.0047 | 0.052 | n/a | 0.0023 | -0.106 | 0.763 | 0.136 | 0.227 | 0.4325 |
| pts | 2024 | A4 | 27583 | 1315 | 3.1885 | +0.0001 | -0.0021 | +0.0023 | 0.926 | n/a | 0.0022 | -0.109 | 0.764 | 0.135 | 0.226 | 0.4326 |
| reb | 2023 | A0 | 27619 | 1318 | 1.2928 | n/a | n/a | n/a | n/a | n/a | n/a | -0.043 | 0.792 | 0.113 | 0.211 | 0.4625 |
| reb | 2023 | A1 | 27619 | 1318 | 1.2943 | +0.0015 | +0.0004 | +0.0026 | 0.012 | 0.048 | 0.0011 | -0.044 | 0.793 | 0.112 | 0.211 | 0.4628 |
| reb | 2023 | A2 | 27619 | 1318 | 1.2941 | +0.0013 | +0.0000 | +0.0026 | 0.047 | 0.188 | 0.0013 | -0.049 | 0.793 | 0.113 | 0.211 | 0.4627 |
| reb | 2023 | A3 | 27619 | 1318 | 1.2944 | +0.0016 | +0.0003 | +0.0029 | 0.012 | n/a | 0.0013 | -0.039 | 0.793 | 0.112 | 0.210 | 0.4625 |
| reb | 2023 | A4 | 27619 | 1318 | 1.2932 | +0.0004 | -0.0009 | +0.0016 | 0.556 | n/a | 0.0013 | -0.048 | 0.791 | 0.114 | 0.211 | 0.4626 |
| reb | 2024 | A0 | 27583 | 1315 | 1.3140 | n/a | n/a | n/a | n/a | n/a | n/a | -0.051 | 0.785 | 0.116 | 0.215 | 0.4726 |
| reb | 2024 | A1 | 27583 | 1315 | 1.3148 | +0.0008 | -0.0000 | +0.0017 | 0.059 | 0.236 | 0.0009 | -0.052 | 0.786 | 0.115 | 0.214 | 0.4730 |
| reb | 2024 | A2 | 27583 | 1315 | 1.3153 | +0.0013 | +0.0003 | +0.0023 | 0.012 | 0.048 | 0.0010 | -0.048 | 0.787 | 0.115 | 0.214 | 0.4732 |
| reb | 2024 | A3 | 27583 | 1315 | 1.3144 | +0.0004 | -0.0006 | +0.0014 | 0.443 | n/a | 0.0010 | -0.046 | 0.787 | 0.115 | 0.214 | 0.4728 |
| reb | 2024 | A4 | 27583 | 1315 | 1.3148 | +0.0008 | -0.0002 | +0.0019 | 0.100 | n/a | 0.0010 | -0.047 | 0.786 | 0.115 | 0.214 | 0.4729 |
| ast | 2023 | A0 | 27619 | 1318 | 0.9059 | n/a | n/a | n/a | n/a | n/a | n/a | -0.050 | 0.788 | 0.118 | 0.214 | 0.4311 |
| ast | 2023 | A1 | 27619 | 1318 | 0.9060 | +0.0001 | -0.0007 | +0.0009 | 0.774 | 0.774 | 0.0008 | -0.051 | 0.787 | 0.118 | 0.215 | 0.4311 |
| ast | 2023 | A2 | 27619 | 1318 | 0.9059 | +0.0000 | -0.0009 | +0.0009 | 0.975 | 0.975 | 0.0009 | -0.048 | 0.787 | 0.118 | 0.214 | 0.4313 |
| ast | 2023 | A3 | 27619 | 1318 | 0.9062 | +0.0003 | -0.0006 | +0.0012 | 0.604 | n/a | 0.0009 | -0.052 | 0.788 | 0.118 | 0.213 | 0.4315 |
| ast | 2023 | A4 | 27619 | 1318 | 0.9061 | +0.0002 | -0.0006 | +0.0011 | 0.602 | n/a | 0.0009 | -0.050 | 0.788 | 0.118 | 0.213 | 0.4311 |
| ast | 2024 | A0 | 27583 | 1315 | 0.9010 | n/a | n/a | n/a | n/a | n/a | n/a | -0.066 | 0.781 | 0.123 | 0.223 | 0.4377 |
| ast | 2024 | A1 | 27583 | 1315 | 0.9014 | +0.0003 | -0.0003 | +0.0010 | 0.334 | 0.445 | 0.0007 | -0.065 | 0.781 | 0.123 | 0.222 | 0.4378 |
| ast | 2024 | A2 | 27583 | 1315 | 0.9014 | +0.0004 | -0.0004 | +0.0012 | 0.336 | 0.448 | 0.0008 | -0.071 | 0.781 | 0.123 | 0.223 | 0.4380 |
| ast | 2024 | A3 | 27583 | 1315 | 0.9016 | +0.0006 | -0.0001 | +0.0014 | 0.109 | n/a | 0.0008 | -0.056 | 0.781 | 0.122 | 0.222 | 0.4381 |
| ast | 2024 | A4 | 27583 | 1315 | 0.9006 | -0.0004 | -0.0012 | +0.0003 | 0.272 | n/a | 0.0008 | -0.066 | 0.781 | 0.123 | 0.222 | 0.4375 |
| fg3m | 2023 | A0 | 27619 | 1318 | 0.5690 | n/a | n/a | n/a | n/a | n/a | n/a | -0.015 | 0.792 | 0.112 | 0.214 | 0.4900 |
| fg3m | 2023 | A1 | 27619 | 1318 | 0.5689 | -0.0002 | -0.0008 | +0.0004 | 0.599 | 0.774 | 0.0006 | -0.013 | 0.791 | 0.113 | 0.213 | 0.4900 |
| fg3m | 2023 | A2 | 27619 | 1318 | 0.5686 | -0.0004 | -0.0011 | +0.0003 | 0.276 | 0.373 | 0.0007 | -0.013 | 0.791 | 0.113 | 0.214 | 0.4897 |
| fg3m | 2023 | A3 | 27619 | 1318 | 0.5688 | -0.0003 | -0.0009 | +0.0004 | 0.430 | n/a | 0.0007 | -0.009 | 0.790 | 0.112 | 0.212 | 0.4899 |
| fg3m | 2023 | A4 | 27619 | 1318 | 0.5690 | -0.0000 | -0.0007 | +0.0007 | 0.980 | n/a | 0.0007 | -0.011 | 0.791 | 0.113 | 0.214 | 0.4898 |
| fg3m | 2024 | A0 | 27583 | 1315 | 0.5923 | n/a | n/a | n/a | n/a | n/a | n/a | -0.001 | 0.783 | 0.117 | 0.214 | 0.4948 |
| fg3m | 2024 | A1 | 27583 | 1315 | 0.5923 | +0.0000 | -0.0005 | +0.0005 | 0.932 | 0.932 | 0.0005 | -0.002 | 0.784 | 0.117 | 0.214 | 0.4945 |
| fg3m | 2024 | A2 | 27583 | 1315 | 0.5927 | +0.0004 | -0.0002 | +0.0010 | 0.204 | 0.408 | 0.0006 | +0.002 | 0.784 | 0.117 | 0.213 | 0.4947 |
| fg3m | 2024 | A3 | 27583 | 1315 | 0.5926 | +0.0003 | -0.0002 | +0.0009 | 0.203 | n/a | 0.0005 | +0.005 | 0.783 | 0.117 | 0.213 | 0.4947 |
| fg3m | 2024 | A4 | 27583 | 1315 | 0.5931 | +0.0008 | +0.0003 | +0.0014 | 0.010 | n/a | 0.0006 | +0.001 | 0.783 | 0.117 | 0.213 | 0.4951 |

## Attack (dCRPS vs A0; A1/A2 minus A3 with CI)

| stat | season | A1 | A2 | A3 | A4 | A1-A3 [lo,hi] | A2-A3 [lo,hi] |
|---|---|---|---|---|---|---|---|
| pts | 2023 | +0.0011 | +0.0016 | +0.0007 | +0.0013 | +0.0004 [-0.0025,+0.0034] | +0.0009 [-0.0021,+0.0039] |
| pts | 2024 | +0.0014 | -0.0001 | +0.0023 | +0.0001 | -0.0009 [-0.0033,+0.0016] | -0.0024 [-0.0047,-0.0001] |
| reb | 2023 | +0.0015 | +0.0013 | +0.0016 | +0.0004 | -0.0001 [-0.0014,+0.0012] | -0.0003 [-0.0016,+0.0010] |
| reb | 2024 | +0.0008 | +0.0013 | +0.0004 | +0.0008 | +0.0004 [-0.0007,+0.0015] | +0.0009 [-0.0001,+0.0020] |
| ast | 2023 | +0.0001 | +0.0000 | +0.0003 | +0.0002 | -0.0001 [-0.0010,+0.0007] | -0.0002 [-0.0011,+0.0006] |
| ast | 2024 | +0.0003 | +0.0004 | +0.0006 | -0.0004 | -0.0003 [-0.0010,+0.0004] | -0.0002 [-0.0009,+0.0005] |
| fg3m | 2023 | -0.0002 | -0.0004 | -0.0003 | -0.0000 | +0.0001 [-0.0006,+0.0008] | -0.0001 [-0.0008,+0.0005] |
| fg3m | 2024 | +0.0000 | +0.0004 | +0.0003 | +0.0008 | -0.0003 [-0.0009,+0.0002] | +0.0000 [-0.0005,+0.0006] |

## Slices (arm - A0)

| stat | season | arm | slice | n | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|---|
| pts | 2023 | A1 | all | 27619 | +0.0011 | -0.0013 | +0.0035 |
| pts | 2023 | A1 | starter | 12267 | +0.0014 | -0.0033 | +0.0061 |
| pts | 2023 | A1 | bench | 15352 | +0.0008 | -0.0017 | +0.0033 |
| pts | 2023 | A1 | first15 | 4586 | +0.0042 | -0.0022 | +0.0115 |
| pts | 2023 | A1 | teammate_out | 23246 | +0.0008 | -0.0017 | +0.0035 |
| pts | 2023 | A1 | ff_pace:T1 | 9215 | +0.0001 | -0.0044 | +0.0044 |
| pts | 2023 | A1 | ff_pace:T2 | 9218 | +0.0006 | -0.0032 | +0.0048 |
| pts | 2023 | A1 | ff_pace:T3 | 9186 | +0.0026 | -0.0019 | +0.0067 |
| pts | 2023 | A1 | ff_def_efg:T1 | 9207 | +0.0003 | -0.0041 | +0.0046 |
| pts | 2023 | A1 | ff_def_efg:T2 | 9213 | +0.0039 | -0.0002 | +0.0082 |
| pts | 2023 | A1 | ff_def_efg:T3 | 9199 | -0.0010 | -0.0054 | +0.0036 |
| pts | 2023 | A2 | all | 27619 | +0.0016 | -0.0012 | +0.0044 |
| pts | 2023 | A2 | starter | 12267 | +0.0021 | -0.0029 | +0.0072 |
| pts | 2023 | A2 | bench | 15352 | +0.0012 | -0.0020 | +0.0040 |
| pts | 2023 | A2 | first15 | 4586 | +0.0005 | -0.0080 | +0.0094 |
| pts | 2023 | A2 | teammate_out | 23246 | +0.0008 | -0.0022 | +0.0039 |
| pts | 2023 | A2 | ff_pace:T1 | 9215 | +0.0026 | -0.0025 | +0.0076 |
| pts | 2023 | A2 | ff_pace:T2 | 9218 | -0.0004 | -0.0049 | +0.0043 |
| pts | 2023 | A2 | ff_pace:T3 | 9186 | +0.0024 | -0.0026 | +0.0072 |
| pts | 2023 | A2 | ff_def_efg:T1 | 9207 | -0.0043 | -0.0094 | +0.0008 |
| pts | 2023 | A2 | ff_def_efg:T2 | 9213 | +0.0065 | +0.0013 | +0.0116 |
| pts | 2023 | A2 | ff_def_efg:T3 | 9199 | +0.0025 | -0.0026 | +0.0072 |
| pts | 2024 | A1 | all | 27583 | +0.0014 | -0.0005 | +0.0034 |
| pts | 2024 | A1 | starter | 12057 | +0.0019 | -0.0018 | +0.0060 |
| pts | 2024 | A1 | bench | 15526 | +0.0011 | -0.0011 | +0.0031 |
| pts | 2024 | A1 | first15 | 4621 | +0.0009 | -0.0045 | +0.0062 |
| pts | 2024 | A1 | teammate_out | 23115 | +0.0014 | -0.0010 | +0.0036 |
| pts | 2024 | A1 | ff_pace:T1 | 9210 | +0.0018 | -0.0017 | +0.0054 |
| pts | 2024 | A1 | ff_pace:T2 | 9185 | +0.0014 | -0.0020 | +0.0046 |
| pts | 2024 | A1 | ff_pace:T3 | 9188 | +0.0011 | -0.0022 | +0.0045 |
| pts | 2024 | A1 | ff_def_efg:T1 | 9206 | +0.0019 | -0.0017 | +0.0057 |
| pts | 2024 | A1 | ff_def_efg:T2 | 9192 | +0.0028 | -0.0007 | +0.0062 |
| pts | 2024 | A1 | ff_def_efg:T3 | 9185 | -0.0004 | -0.0038 | +0.0027 |
| pts | 2024 | A2 | all | 27583 | -0.0001 | -0.0025 | +0.0022 |
| pts | 2024 | A2 | starter | 12057 | +0.0002 | -0.0041 | +0.0046 |
| pts | 2024 | A2 | bench | 15526 | -0.0003 | -0.0028 | +0.0022 |
| pts | 2024 | A2 | first15 | 4621 | -0.0008 | -0.0069 | +0.0052 |
| pts | 2024 | A2 | teammate_out | 23115 | +0.0007 | -0.0019 | +0.0033 |
| pts | 2024 | A2 | ff_pace:T1 | 9210 | -0.0023 | -0.0062 | +0.0017 |
| pts | 2024 | A2 | ff_pace:T2 | 9185 | +0.0019 | -0.0022 | +0.0058 |
| pts | 2024 | A2 | ff_pace:T3 | 9188 | +0.0002 | -0.0038 | +0.0042 |
| pts | 2024 | A2 | ff_def_efg:T1 | 9206 | +0.0022 | -0.0019 | +0.0065 |
| pts | 2024 | A2 | ff_def_efg:T2 | 9192 | -0.0022 | -0.0061 | +0.0018 |
| pts | 2024 | A2 | ff_def_efg:T3 | 9185 | -0.0002 | -0.0041 | +0.0036 |
| reb | 2023 | A1 | all | 27619 | +0.0015 | +0.0004 | +0.0026 |
| reb | 2023 | A1 | starter | 12267 | +0.0005 | -0.0014 | +0.0024 |
| reb | 2023 | A1 | bench | 15352 | +0.0023 | +0.0010 | +0.0036 |
| reb | 2023 | A1 | first15 | 4586 | +0.0029 | -0.0004 | +0.0057 |
| reb | 2023 | A1 | teammate_out | 23246 | +0.0016 | +0.0005 | +0.0028 |
| reb | 2023 | A1 | ff_pace:T1 | 9215 | +0.0012 | -0.0007 | +0.0030 |
| reb | 2023 | A1 | ff_pace:T2 | 9218 | +0.0022 | +0.0005 | +0.0040 |
| reb | 2023 | A1 | ff_pace:T3 | 9186 | +0.0011 | -0.0009 | +0.0032 |
| reb | 2023 | A1 | ff_def_efg:T1 | 9207 | +0.0022 | +0.0003 | +0.0040 |
| reb | 2023 | A1 | ff_def_efg:T2 | 9213 | +0.0013 | -0.0005 | +0.0033 |
| reb | 2023 | A1 | ff_def_efg:T3 | 9199 | +0.0010 | -0.0008 | +0.0029 |
| reb | 2023 | A2 | all | 27619 | +0.0013 | +0.0000 | +0.0026 |
| reb | 2023 | A2 | starter | 12267 | +0.0004 | -0.0019 | +0.0027 |
| reb | 2023 | A2 | bench | 15352 | +0.0020 | +0.0005 | +0.0036 |
| reb | 2023 | A2 | first15 | 4586 | +0.0057 | +0.0020 | +0.0092 |
| reb | 2023 | A2 | teammate_out | 23246 | +0.0012 | -0.0002 | +0.0026 |
| reb | 2023 | A2 | ff_pace:T1 | 9215 | +0.0006 | -0.0017 | +0.0028 |
| reb | 2023 | A2 | ff_pace:T2 | 9218 | +0.0014 | -0.0007 | +0.0035 |
| reb | 2023 | A2 | ff_pace:T3 | 9186 | +0.0019 | -0.0005 | +0.0042 |
| reb | 2023 | A2 | ff_def_efg:T1 | 9207 | +0.0020 | -0.0002 | +0.0043 |
| reb | 2023 | A2 | ff_def_efg:T2 | 9213 | -0.0004 | -0.0028 | +0.0017 |
| reb | 2023 | A2 | ff_def_efg:T3 | 9199 | +0.0023 | -0.0000 | +0.0044 |
| reb | 2024 | A1 | all | 27583 | +0.0008 | -0.0000 | +0.0017 |
| reb | 2024 | A1 | starter | 12057 | +0.0009 | -0.0006 | +0.0023 |
| reb | 2024 | A1 | bench | 15526 | +0.0008 | -0.0002 | +0.0019 |
| reb | 2024 | A1 | first15 | 4621 | +0.0017 | -0.0006 | +0.0039 |
| reb | 2024 | A1 | teammate_out | 23115 | +0.0008 | -0.0002 | +0.0017 |
| reb | 2024 | A1 | ff_pace:T1 | 9210 | -0.0006 | -0.0022 | +0.0010 |
| reb | 2024 | A1 | ff_pace:T2 | 9185 | +0.0017 | +0.0004 | +0.0031 |
| reb | 2024 | A1 | ff_pace:T3 | 9188 | +0.0014 | -0.0003 | +0.0030 |
| reb | 2024 | A1 | ff_def_efg:T1 | 9206 | +0.0010 | -0.0005 | +0.0025 |
| reb | 2024 | A1 | ff_def_efg:T2 | 9192 | +0.0001 | -0.0015 | +0.0017 |
| reb | 2024 | A1 | ff_def_efg:T3 | 9185 | +0.0014 | -0.0001 | +0.0030 |
| reb | 2024 | A2 | all | 27583 | +0.0013 | +0.0003 | +0.0023 |
| reb | 2024 | A2 | starter | 12057 | +0.0009 | -0.0008 | +0.0027 |
| reb | 2024 | A2 | bench | 15526 | +0.0016 | +0.0005 | +0.0028 |
| reb | 2024 | A2 | first15 | 4621 | +0.0022 | -0.0001 | +0.0044 |
| reb | 2024 | A2 | teammate_out | 23115 | +0.0016 | +0.0005 | +0.0027 |
| reb | 2024 | A2 | ff_pace:T1 | 9210 | +0.0011 | -0.0004 | +0.0028 |
| reb | 2024 | A2 | ff_pace:T2 | 9185 | +0.0014 | -0.0004 | +0.0032 |
| reb | 2024 | A2 | ff_pace:T3 | 9188 | +0.0014 | -0.0005 | +0.0032 |
| reb | 2024 | A2 | ff_def_efg:T1 | 9206 | +0.0006 | -0.0013 | +0.0025 |
| reb | 2024 | A2 | ff_def_efg:T2 | 9192 | +0.0020 | +0.0002 | +0.0036 |
| reb | 2024 | A2 | ff_def_efg:T3 | 9185 | +0.0015 | -0.0003 | +0.0032 |
| ast | 2023 | A1 | all | 27619 | +0.0001 | -0.0007 | +0.0009 |
| ast | 2023 | A1 | starter | 12267 | -0.0002 | -0.0018 | +0.0013 |
| ast | 2023 | A1 | bench | 15352 | +0.0004 | -0.0005 | +0.0012 |
| ast | 2023 | A1 | first15 | 4586 | +0.0010 | -0.0015 | +0.0033 |
| ast | 2023 | A1 | teammate_out | 23246 | +0.0000 | -0.0008 | +0.0009 |
| ast | 2023 | A1 | ff_pace:T1 | 9215 | +0.0002 | -0.0013 | +0.0017 |
| ast | 2023 | A1 | ff_pace:T2 | 9218 | -0.0003 | -0.0016 | +0.0010 |
| ast | 2023 | A1 | ff_pace:T3 | 9186 | +0.0004 | -0.0010 | +0.0018 |
| ast | 2023 | A1 | ff_def_efg:T1 | 9207 | -0.0007 | -0.0020 | +0.0008 |
| ast | 2023 | A1 | ff_def_efg:T2 | 9213 | +0.0009 | -0.0005 | +0.0024 |
| ast | 2023 | A1 | ff_def_efg:T3 | 9199 | +0.0001 | -0.0014 | +0.0015 |
| ast | 2023 | A2 | all | 27619 | +0.0000 | -0.0009 | +0.0009 |
| ast | 2023 | A2 | starter | 12267 | -0.0004 | -0.0021 | +0.0013 |
| ast | 2023 | A2 | bench | 15352 | +0.0004 | -0.0005 | +0.0013 |
| ast | 2023 | A2 | first15 | 4586 | +0.0007 | -0.0020 | +0.0033 |
| ast | 2023 | A2 | teammate_out | 23246 | +0.0001 | -0.0009 | +0.0011 |
| ast | 2023 | A2 | ff_pace:T1 | 9215 | +0.0009 | -0.0007 | +0.0024 |
| ast | 2023 | A2 | ff_pace:T2 | 9218 | -0.0006 | -0.0021 | +0.0009 |
| ast | 2023 | A2 | ff_pace:T3 | 9186 | -0.0002 | -0.0018 | +0.0014 |
| ast | 2023 | A2 | ff_def_efg:T1 | 9207 | -0.0009 | -0.0024 | +0.0006 |
| ast | 2023 | A2 | ff_def_efg:T2 | 9213 | +0.0006 | -0.0009 | +0.0022 |
| ast | 2023 | A2 | ff_def_efg:T3 | 9199 | +0.0005 | -0.0011 | +0.0020 |
| ast | 2024 | A1 | all | 27583 | +0.0003 | -0.0003 | +0.0010 |
| ast | 2024 | A1 | starter | 12057 | -0.0001 | -0.0014 | +0.0012 |
| ast | 2024 | A1 | bench | 15526 | +0.0007 | +0.0000 | +0.0013 |
| ast | 2024 | A1 | first15 | 4621 | +0.0017 | -0.0003 | +0.0037 |
| ast | 2024 | A1 | teammate_out | 23115 | +0.0005 | -0.0003 | +0.0012 |
| ast | 2024 | A1 | ff_pace:T1 | 9210 | +0.0011 | -0.0001 | +0.0022 |
| ast | 2024 | A1 | ff_pace:T2 | 9185 | -0.0005 | -0.0016 | +0.0006 |
| ast | 2024 | A1 | ff_pace:T3 | 9188 | +0.0004 | -0.0008 | +0.0017 |
| ast | 2024 | A1 | ff_def_efg:T1 | 9206 | +0.0003 | -0.0009 | +0.0015 |
| ast | 2024 | A1 | ff_def_efg:T2 | 9192 | +0.0001 | -0.0011 | +0.0012 |
| ast | 2024 | A1 | ff_def_efg:T3 | 9185 | +0.0006 | -0.0005 | +0.0017 |
| ast | 2024 | A2 | all | 27583 | +0.0004 | -0.0004 | +0.0012 |
| ast | 2024 | A2 | starter | 12057 | +0.0003 | -0.0012 | +0.0018 |
| ast | 2024 | A2 | bench | 15526 | +0.0005 | -0.0003 | +0.0013 |
| ast | 2024 | A2 | first15 | 4621 | +0.0004 | -0.0020 | +0.0028 |
| ast | 2024 | A2 | teammate_out | 23115 | +0.0004 | -0.0005 | +0.0012 |
| ast | 2024 | A2 | ff_pace:T1 | 9210 | +0.0012 | -0.0001 | +0.0024 |
| ast | 2024 | A2 | ff_pace:T2 | 9185 | -0.0003 | -0.0018 | +0.0010 |
| ast | 2024 | A2 | ff_pace:T3 | 9188 | +0.0003 | -0.0012 | +0.0018 |
| ast | 2024 | A2 | ff_def_efg:T1 | 9206 | +0.0002 | -0.0012 | +0.0016 |
| ast | 2024 | A2 | ff_def_efg:T2 | 9192 | -0.0001 | -0.0016 | +0.0013 |
| ast | 2024 | A2 | ff_def_efg:T3 | 9185 | +0.0011 | -0.0003 | +0.0024 |
| fg3m | 2023 | A1 | all | 27619 | -0.0002 | -0.0008 | +0.0004 |
| fg3m | 2023 | A1 | starter | 12267 | -0.0005 | -0.0015 | +0.0005 |
| fg3m | 2023 | A1 | bench | 15352 | +0.0001 | -0.0005 | +0.0008 |
| fg3m | 2023 | A1 | first15 | 4586 | -0.0005 | -0.0020 | +0.0010 |
| fg3m | 2023 | A1 | teammate_out | 23246 | +0.0000 | -0.0006 | +0.0007 |
| fg3m | 2023 | A1 | ff_pace:T1 | 9215 | -0.0002 | -0.0012 | +0.0008 |
| fg3m | 2023 | A1 | ff_pace:T2 | 9218 | -0.0007 | -0.0017 | +0.0003 |
| fg3m | 2023 | A1 | ff_pace:T3 | 9186 | +0.0004 | -0.0006 | +0.0014 |
| fg3m | 2023 | A1 | ff_def_efg:T1 | 9207 | -0.0008 | -0.0018 | +0.0002 |
| fg3m | 2023 | A1 | ff_def_efg:T2 | 9213 | -0.0003 | -0.0013 | +0.0007 |
| fg3m | 2023 | A1 | ff_def_efg:T3 | 9199 | +0.0006 | -0.0003 | +0.0017 |
| fg3m | 2023 | A2 | all | 27619 | -0.0004 | -0.0011 | +0.0003 |
| fg3m | 2023 | A2 | starter | 12267 | -0.0010 | -0.0024 | +0.0003 |
| fg3m | 2023 | A2 | bench | 15352 | +0.0001 | -0.0006 | +0.0008 |
| fg3m | 2023 | A2 | first15 | 4586 | -0.0006 | -0.0022 | +0.0012 |
| fg3m | 2023 | A2 | teammate_out | 23246 | -0.0004 | -0.0012 | +0.0003 |
| fg3m | 2023 | A2 | ff_pace:T1 | 9215 | -0.0000 | -0.0012 | +0.0012 |
| fg3m | 2023 | A2 | ff_pace:T2 | 9218 | -0.0001 | -0.0012 | +0.0011 |
| fg3m | 2023 | A2 | ff_pace:T3 | 9186 | -0.0011 | -0.0023 | +0.0002 |
| fg3m | 2023 | A2 | ff_def_efg:T1 | 9207 | -0.0008 | -0.0020 | +0.0003 |
| fg3m | 2023 | A2 | ff_def_efg:T2 | 9213 | -0.0007 | -0.0018 | +0.0004 |
| fg3m | 2023 | A2 | ff_def_efg:T3 | 9199 | +0.0003 | -0.0009 | +0.0016 |
| fg3m | 2024 | A1 | all | 27583 | +0.0000 | -0.0005 | +0.0005 |
| fg3m | 2024 | A1 | starter | 12057 | -0.0003 | -0.0012 | +0.0006 |
| fg3m | 2024 | A1 | bench | 15526 | +0.0002 | -0.0002 | +0.0008 |
| fg3m | 2024 | A1 | first15 | 4621 | +0.0004 | -0.0008 | +0.0017 |
| fg3m | 2024 | A1 | teammate_out | 23115 | +0.0001 | -0.0005 | +0.0006 |
| fg3m | 2024 | A1 | ff_pace:T1 | 9210 | +0.0006 | -0.0003 | +0.0014 |
| fg3m | 2024 | A1 | ff_pace:T2 | 9185 | -0.0008 | -0.0016 | +0.0001 |
| fg3m | 2024 | A1 | ff_pace:T3 | 9188 | +0.0002 | -0.0006 | +0.0011 |
| fg3m | 2024 | A1 | ff_def_efg:T1 | 9206 | -0.0001 | -0.0009 | +0.0007 |
| fg3m | 2024 | A1 | ff_def_efg:T2 | 9192 | +0.0000 | -0.0008 | +0.0009 |
| fg3m | 2024 | A1 | ff_def_efg:T3 | 9185 | +0.0001 | -0.0008 | +0.0010 |
| fg3m | 2024 | A2 | all | 27583 | +0.0004 | -0.0002 | +0.0010 |
| fg3m | 2024 | A2 | starter | 12057 | -0.0003 | -0.0014 | +0.0008 |
| fg3m | 2024 | A2 | bench | 15526 | +0.0009 | +0.0004 | +0.0016 |
| fg3m | 2024 | A2 | first15 | 4621 | +0.0016 | -0.0002 | +0.0032 |
| fg3m | 2024 | A2 | teammate_out | 23115 | +0.0007 | +0.0000 | +0.0013 |
| fg3m | 2024 | A2 | ff_pace:T1 | 9210 | +0.0002 | -0.0008 | +0.0012 |
| fg3m | 2024 | A2 | ff_pace:T2 | 9185 | -0.0000 | -0.0009 | +0.0009 |
| fg3m | 2024 | A2 | ff_pace:T3 | 9188 | +0.0009 | +0.0000 | +0.0019 |
| fg3m | 2024 | A2 | ff_def_efg:T1 | 9206 | +0.0006 | -0.0003 | +0.0015 |
| fg3m | 2024 | A2 | ff_def_efg:T2 | 9192 | +0.0007 | -0.0003 | +0.0016 |
| fg3m | 2024 | A2 | ff_def_efg:T3 | 9185 | -0.0001 | -0.0012 | +0.0010 |

## Mechanism (A2 - A0, descriptive)

| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi |
|---|---|---|---|---|---|---|---|
| pts | 2023 | ff_pace | T1 | 9215 | +0.0026 | -0.0025 | +0.0076 |
| pts | 2023 | ff_pace | T2 | 9218 | -0.0004 | -0.0049 | +0.0043 |
| pts | 2023 | ff_pace | T3 | 9186 | +0.0024 | -0.0026 | +0.0072 |
| pts | 2024 | ff_pace | T1 | 9210 | -0.0023 | -0.0062 | +0.0017 |
| pts | 2024 | ff_pace | T2 | 9185 | +0.0019 | -0.0022 | +0.0058 |
| pts | 2024 | ff_pace | T3 | 9188 | +0.0002 | -0.0038 | +0.0042 |
| reb | 2023 | ff_def_orb | T1 | 9207 | -0.0005 | -0.0027 | +0.0017 |
| reb | 2023 | ff_def_orb | T2 | 9214 | +0.0029 | +0.0008 | +0.0049 |
| reb | 2023 | ff_def_orb | T3 | 9198 | +0.0014 | -0.0008 | +0.0037 |
| reb | 2024 | ff_def_orb | T1 | 9202 | +0.0002 | -0.0015 | +0.0020 |
| reb | 2024 | ff_def_orb | T2 | 9192 | +0.0023 | +0.0006 | +0.0041 |
| reb | 2024 | ff_def_orb | T3 | 9189 | +0.0015 | -0.0003 | +0.0032 |
| fg3m | 2023 | ff_pace | T1 | 9215 | -0.0000 | -0.0012 | +0.0012 |
| fg3m | 2023 | ff_pace | T2 | 9218 | -0.0001 | -0.0012 | +0.0011 |
| fg3m | 2023 | ff_pace | T3 | 9186 | -0.0011 | -0.0023 | +0.0002 |
| fg3m | 2024 | ff_pace | T1 | 9210 | +0.0002 | -0.0008 | +0.0012 |
| fg3m | 2024 | ff_pace | T2 | 9185 | -0.0000 | -0.0009 | +0.0009 |
| fg3m | 2024 | ff_pace | T3 | 9188 | +0.0009 | +0.0000 | +0.0019 |

## Missingness audit (NULL/NaN rate of ff_* after the join, by bucket)

| col | bucket | null_rate |
|---|---|---|
| ff_pace | DNP | 0.00000 |
| ff_pace | <5 | 0.00000 |
| ff_pace | 5-10 | 0.00000 |
| ff_pace | 10-20 | 0.00000 |
| ff_pace | >20 | 0.00000 |
| ff_off_efg | DNP | 0.00000 |
| ff_off_efg | <5 | 0.00000 |
| ff_off_efg | 5-10 | 0.00000 |
| ff_off_efg | 10-20 | 0.00000 |
| ff_off_efg | >20 | 0.00000 |
| ff_off_tov | DNP | 0.00000 |
| ff_off_tov | <5 | 0.00000 |
| ff_off_tov | 5-10 | 0.00000 |
| ff_off_tov | 10-20 | 0.00000 |
| ff_off_tov | >20 | 0.00000 |
| ff_off_orb | DNP | 0.00000 |
| ff_off_orb | <5 | 0.00000 |
| ff_off_orb | 5-10 | 0.00000 |
| ff_off_orb | 10-20 | 0.00000 |
| ff_off_orb | >20 | 0.00000 |
| ff_off_ftr | DNP | 0.00000 |
| ff_off_ftr | <5 | 0.00000 |
| ff_off_ftr | 5-10 | 0.00000 |
| ff_off_ftr | 10-20 | 0.00000 |
| ff_off_ftr | >20 | 0.00000 |
| ff_def_efg | DNP | 0.00000 |
| ff_def_efg | <5 | 0.00000 |
| ff_def_efg | 5-10 | 0.00000 |
| ff_def_efg | 10-20 | 0.00000 |
| ff_def_efg | >20 | 0.00000 |
| ff_def_tov | DNP | 0.00000 |
| ff_def_tov | <5 | 0.00000 |
| ff_def_tov | 5-10 | 0.00000 |
| ff_def_tov | 10-20 | 0.00000 |
| ff_def_tov | >20 | 0.00000 |
| ff_def_orb | DNP | 0.00000 |
| ff_def_orb | <5 | 0.00000 |
| ff_def_orb | 5-10 | 0.00000 |
| ff_def_orb | 10-20 | 0.00000 |
| ff_def_orb | >20 | 0.00000 |
| ff_def_ftr | DNP | 0.00000 |
| ff_def_ftr | <5 | 0.00000 |
| ff_def_ftr | 5-10 | 0.00000 |
| ff_def_ftr | 10-20 | 0.00000 |
| ff_def_ftr | >20 | 0.00000 |

## Unstated details (chosen before scoring)

- no ContextResidualConfig.four_factors flag exists (nba/ is untouched, as in F8/F11): A0 is the unmodified production name list, so 'flag off byte-identical' holds by construction and is asserted as names(A0) == stat_feature_names(stat)
- all games in `games` (regular season and playoffs, season <= 2024) are team-games; check 1 coverage is computed on regular-season ids ('002...') with a result
- `games` stores no periods: overtime count = max(possessions.period) - 4, floored at 0
- league as-of mean uses team-games on STRICTLY EARLIER game_date (same-day games excluded, since tip order within a date is unknown); team windows use (game_date, game_id) order with shift(1)
- team with < 5 prior games takes the league mean (ff = 0); otherwise shrunk with n = games in the window (<= 20) and pseudo-count 10; the window crosses seasons
- out-of-range possession counts (<80 or >130): pace48 and tov_pct of the team-game (and the opponent's allowed tov copy) are replaced by the league as-of mean at that date
- the first date of the load has no prior league mean: every ff_* is 0.0 there
- check 5 outcome terciles: terciles of the realised stat (all four stats) among played rows per season; gap = max-min null rate in pp over minutes buckets (<5, 5-10, 10-20, >20; DNP audited, outside the gap) and over terciles, over all nine columns
- check 5 audits the ff columns AFTER the left join onto the production feature frame (a join miss would show as NULL); the builder itself zero-fills the undefined first-date values
- check 3 passes when the team-game raw series have zero NULLs and the played player rows have no NULL fga/fgm/fg3m/fta/tov/oreb/drb
- 'stat passes' = pass rule 1 (point <= -0.005, CI upper < 0, BH p < 0.05) in BOTH 2023 and 2024 for the arm; guards, A3 and A4 attack gates are evaluated on 2024 all rows (2023 reported)
- BH families: for each season, the four stats' all-rows p, separately for A1 and for A2; A3/A4 carry raw p only
- attack gate vs A3 uses (arm - A3) paired point on all 2024 rows for either arm (A3 is the placebo of the nine-column set); A4 keeps the gain when A4_dcrps <= 0.5 * A2_dcrps (2024); 'A1 must pass' means A1 passes every rule of its own family
- slice guard slices (n >= 300, 2024, arm - A0 > +0.01 fails): starter, bench, first15 (team_game_no < 15), teammate_out, ff_pace terciles, ff_def_efg terciles (terciles per season of the row's column)
- mechanism tables use the candidate arm A2 vs A0: pts and fg3m by ff_pace tercile, reb by ff_def_orb tercile; slice tables carry an `arm` field because A1 and A2 are both reported
- tll = mean threshold log loss of P(y >= n) on the integer grid at pts 10/15, reb 4/6, ast 2/4, fg3m 1/2
- A0 reproduction: integer CRPS and n against reports/lower_tail/candidates.json 'off' to 1e-6, all 4 stats x 2 seasons
- planted same-game test edits the target day's possessions (drop every other, one OT period added), box score (fga/tov/oreb inflated) and overtime count; planted-future edits every team-game on or after 2024-01-15; ff must be byte-identical for all games dated <= the day / <= cutoff
- possessions read: game_id, period, off_team, pts only (the other named columns are not needed by the frozen formulas)

## Proposed ledger rows (draft, unnumbered; maintainer appends)

| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | pts 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0011 | -0.0013 | +0.0035 | game | 0.774 | MDE 0.0024; bias -0.149 -> -0.166; cov80 0.772 -> 0.772; A3 placebo +0.0007, A4 no-pace +0.0013 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | pts 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0016 | -0.0012 | +0.0044 | game | 0.373 | MDE 0.0028; bias -0.149 -> -0.171; cov80 0.772 -> 0.770; A3 placebo +0.0007, A4 no-pace +0.0013 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | pts 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0014 | -0.0005 | +0.0034 | game | 0.300 | MDE 0.0020; bias -0.113 -> -0.141; cov80 0.764 -> 0.764; A3 placebo +0.0023, A4 no-pace +0.0001 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | pts 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0001 | -0.0025 | +0.0022 | game | 0.948 | MDE 0.0024; bias -0.113 -> -0.136; cov80 0.764 -> 0.763; A3 placebo +0.0023, A4 no-pace +0.0001 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | reb 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0015 | +0.0004 | +0.0026 | game | 0.048 | MDE 0.0011; bias -0.043 -> -0.044; cov80 0.792 -> 0.793; A3 placebo +0.0016, A4 no-pace +0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | reb 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0013 | +0.0000 | +0.0026 | game | 0.188 | MDE 0.0013; bias -0.043 -> -0.049; cov80 0.792 -> 0.793; A3 placebo +0.0016, A4 no-pace +0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | reb 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0008 | -0.0000 | +0.0017 | game | 0.236 | MDE 0.0009; bias -0.051 -> -0.052; cov80 0.785 -> 0.786; A3 placebo +0.0004, A4 no-pace +0.0008 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | reb 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0013 | +0.0003 | +0.0023 | game | 0.048 | MDE 0.0010; bias -0.051 -> -0.048; cov80 0.785 -> 0.787; A3 placebo +0.0004, A4 no-pace +0.0008 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | ast 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0001 | -0.0007 | +0.0009 | game | 0.774 | MDE 0.0008; bias -0.050 -> -0.051; cov80 0.788 -> 0.787; A3 placebo +0.0003, A4 no-pace +0.0002 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | ast 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | +0.0000 | -0.0009 | +0.0009 | game | 0.975 | MDE 0.0009; bias -0.050 -> -0.048; cov80 0.788 -> 0.787; A3 placebo +0.0003, A4 no-pace +0.0002 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | ast 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0003 | -0.0003 | +0.0010 | game | 0.445 | MDE 0.0007; bias -0.066 -> -0.065; cov80 0.781 -> 0.781; A3 placebo +0.0006, A4 no-pace -0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | ast 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0004 | -0.0004 | +0.0012 | game | 0.448 | MDE 0.0008; bias -0.066 -> -0.071; cov80 0.781 -> 0.781; A3 placebo +0.0006, A4 no-pace -0.0004 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | fg3m 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0002 | -0.0008 | +0.0004 | game | 0.774 | MDE 0.0006; bias -0.015 -> -0.013; cov80 0.792 -> 0.791; A3 placebo -0.0003, A4 no-pace -0.0000 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | fg3m 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0004 | -0.0011 | +0.0003 | game | 0.373 | MDE 0.0007; bias -0.015 -> -0.013; cov80 0.792 -> 0.791; A3 placebo -0.0003, A4 no-pace -0.0000 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | fg3m 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0000 | -0.0005 | +0.0005 | game | 0.932 | MDE 0.0005; bias -0.001 -> -0.002; cov80 0.783 -> 0.784; A3 placebo +0.0003, A4 no-pace +0.0008 | not holdout (<=2024) |
| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 0d87c2ce) | fg3m 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | +0.0004 | -0.0002 | +0.0010 | game | 0.408 | MDE 0.0006; bias -0.001 -> +0.002; cov80 0.783 -> 0.784; A3 placebo +0.0003, A4 no-pace +0.0008 | not holdout (<=2024) |
