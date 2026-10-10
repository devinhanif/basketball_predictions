# ROUTER_TIME_WEIGHTED results

Rule: docs/prereg/ROUTER_TIME_WEIGHTED.md, frozen sha256 bb633f7a3176f7cc3709a9c7e8581461eacd1b04f06e293dfbd8998138213c17 (first 8: bb633f7a), commit 918e03c. Run git sha 64738a706c5fbc110824d567bac504b6979755e0. Seeds {"model": 0, "bootstrap": 0, "pit": 0}; bootstrap 2000 game-clustered. Season 2025 never loaded (max season in frames 2024).

## Minimum-data checks

| test | ok |
|---|---|
| 1 every candidate (C0-C3) has OOF rows for >= 95% of production rows, both seasons | True |
| 2 all cells >= 300 rows in 2023 (after the declared merge up a tier) | True |
| 3 planted future: shifting a block's rows one day later changes no weight for that block | True |
| 4 A0 reproduces production integer CRPS and n to 1e-6 | True |

## Verdict per stat

| stat | A1 pass | A2 pass | stat pass | A1 rule1 (2023/2024) | A2 rule1 (2023/2024) | A1-A4 (2024) | A2-A4 (2024) | one-candidate-better A1/A2 | A4 candidate |
|---|---|---|---|---|---|---|---|---|---|
| pts | False | False | False | True/True | True/True | +0.0010 | +0.0029 | True/True | C4 |
| reb | False | False | False | True/True | True/True | +0.0000 | +0.0055 | True/True | C4 |
| ast | False | False | False | True/True | True/True | +0.0001 | +0.0045 | True/True | C4 |
| fg3m | False | False | False | False/False | False/False | +0.0005 | +0.0027 | False/False | C4 |

Close (no stat passes rule 1 on 2023 under either router): False

## Arms table

| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 2023 | A0 | 27619 | 1318 | 3.1528 | n/a | n/a | n/a | n/a | n/a | n/a | -0.149 | 0.772 | 0.132 | 0.222 | 0.4179 |
| pts | 2023 | A1 | 27619 | 1318 | 3.1192 | -0.0336 | -0.0403 | -0.0273 | 0.000 | 0.000 | 0.0065 | -0.121 | 0.773 | 0.130 | 0.221 | 0.4122 |
| pts | 2023 | A2 | 27619 | 1318 | 3.1160 | -0.0369 | -0.0421 | -0.0320 | 0.000 | 0.000 | 0.0051 | -0.110 | 0.774 | 0.129 | 0.221 | 0.4119 |
| pts | 2023 | A3 | 27619 | 1318 | 3.1102 | -0.0426 | -0.0491 | -0.0361 | 0.000 | n/a | 0.0065 | -0.119 | 0.773 | 0.130 | 0.221 | 0.4119 |
| pts | 2023 | A4 | 27619 | 1318 | 3.1148 | -0.0380 | -0.0444 | -0.0320 | 0.000 | n/a | 0.0062 | -0.087 | 0.772 | 0.131 | 0.221 | 0.4121 |
| pts | 2023 | C1 | 27619 | 1318 | 3.2396 | +0.0867 | +0.0758 | +0.0982 | 0.000 | n/a | 0.0112 | -0.234 | 0.759 | 0.123 | 0.243 | 0.4345 |
| pts | 2023 | C2 | 27619 | 1318 | 3.3275 | +0.1747 | +0.1595 | +0.1902 | 0.000 | n/a | 0.0153 | -0.177 | 0.721 | 0.138 | 0.256 | 0.4567 |
| pts | 2023 | C3 | 27619 | 1318 | 3.4753 | +0.3225 | +0.3011 | +0.3425 | 0.000 | n/a | 0.0207 | -0.180 | 0.670 | 0.163 | 0.271 | 0.4982 |
| pts | 2023 | C4 | 27619 | 1318 | 3.1148 | -0.0380 | -0.0444 | -0.0320 | 0.000 | n/a | 0.0062 | -0.087 | 0.772 | 0.131 | 0.221 | 0.4121 |
| pts | 2024 | A0 | 27583 | 1315 | 3.1884 | n/a | n/a | n/a | n/a | n/a | n/a | -0.113 | 0.762 | 0.137 | 0.226 | 0.4326 |
| pts | 2024 | A1 | 27583 | 1315 | 3.1461 | -0.0424 | -0.0495 | -0.0351 | 0.000 | 0.000 | 0.0072 | -0.131 | 0.764 | 0.136 | 0.227 | 0.4252 |
| pts | 2024 | A2 | 27583 | 1315 | 3.1480 | -0.0404 | -0.0459 | -0.0350 | 0.000 | 0.001 | 0.0054 | -0.104 | 0.765 | 0.134 | 0.226 | 0.4254 |
| pts | 2024 | A3 | 27583 | 1315 | 3.1436 | -0.0448 | -0.0517 | -0.0377 | 0.000 | n/a | 0.0070 | -0.129 | 0.764 | 0.136 | 0.227 | 0.4248 |
| pts | 2024 | A4 | 27583 | 1315 | 3.1450 | -0.0434 | -0.0503 | -0.0359 | 0.000 | n/a | 0.0072 | -0.123 | 0.763 | 0.137 | 0.227 | 0.4251 |
| pts | 2024 | C1 | 27583 | 1315 | 3.2980 | +0.1096 | +0.0977 | +0.1219 | 0.000 | n/a | 0.0121 | -0.069 | 0.752 | 0.120 | 0.238 | 0.4553 |
| pts | 2024 | C2 | 27583 | 1315 | 3.3888 | +0.2004 | +0.1845 | +0.2161 | 0.000 | n/a | 0.0158 | -0.120 | 0.717 | 0.137 | 0.251 | 0.4775 |
| pts | 2024 | C3 | 27583 | 1315 | 3.5425 | +0.3541 | +0.3332 | +0.3746 | 0.000 | n/a | 0.0207 | -0.159 | 0.672 | 0.160 | 0.267 | 0.5202 |
| pts | 2024 | C4 | 27583 | 1315 | 3.1450 | -0.0434 | -0.0503 | -0.0359 | 0.000 | n/a | 0.0072 | -0.123 | 0.763 | 0.137 | 0.227 | 0.4251 |
| reb | 2023 | A0 | 27619 | 1318 | 1.2928 | n/a | n/a | n/a | n/a | n/a | n/a | -0.043 | 0.792 | 0.113 | 0.211 | 0.4625 |
| reb | 2023 | A1 | 27619 | 1318 | 1.2774 | -0.0154 | -0.0181 | -0.0128 | 0.000 | 0.000 | 0.0027 | -0.028 | 0.792 | 0.112 | 0.209 | 0.4566 |
| reb | 2023 | A2 | 27619 | 1318 | 1.2806 | -0.0122 | -0.0143 | -0.0103 | 0.000 | 0.000 | 0.0020 | -0.022 | 0.793 | 0.109 | 0.209 | 0.4581 |
| reb | 2023 | A3 | 27619 | 1318 | 1.2753 | -0.0175 | -0.0204 | -0.0148 | 0.000 | n/a | 0.0028 | -0.027 | 0.790 | 0.112 | 0.209 | 0.4561 |
| reb | 2023 | A4 | 27619 | 1318 | 1.2761 | -0.0167 | -0.0194 | -0.0140 | 0.000 | n/a | 0.0027 | -0.021 | 0.790 | 0.112 | 0.208 | 0.4560 |
| reb | 2023 | C1 | 27619 | 1318 | 1.3268 | +0.0340 | +0.0295 | +0.0384 | 0.000 | n/a | 0.0045 | -0.064 | 0.778 | 0.103 | 0.227 | 0.4804 |
| reb | 2023 | C2 | 27619 | 1318 | 1.3482 | +0.0554 | +0.0493 | +0.0614 | 0.000 | n/a | 0.0060 | +0.006 | 0.765 | 0.115 | 0.212 | 0.4872 |
| reb | 2023 | C3 | 27619 | 1318 | 1.3962 | +0.1034 | +0.0954 | +0.1114 | 0.000 | n/a | 0.0080 | +0.001 | 0.746 | 0.118 | 0.216 | 0.5067 |
| reb | 2023 | C4 | 27619 | 1318 | 1.2761 | -0.0167 | -0.0194 | -0.0140 | 0.000 | n/a | 0.0027 | -0.021 | 0.790 | 0.112 | 0.208 | 0.4560 |
| reb | 2024 | A0 | 27583 | 1315 | 1.3140 | n/a | n/a | n/a | n/a | n/a | n/a | -0.051 | 0.788 | 0.115 | 0.214 | 0.4726 |
| reb | 2024 | A1 | 27583 | 1315 | 1.2964 | -0.0176 | -0.0207 | -0.0147 | 0.000 | 0.000 | 0.0030 | -0.058 | 0.789 | 0.115 | 0.213 | 0.4671 |
| reb | 2024 | A2 | 27583 | 1315 | 1.3019 | -0.0121 | -0.0140 | -0.0101 | 0.000 | 0.001 | 0.0019 | -0.037 | 0.789 | 0.112 | 0.213 | 0.4687 |
| reb | 2024 | A3 | 27583 | 1315 | 1.2955 | -0.0185 | -0.0213 | -0.0156 | 0.000 | n/a | 0.0028 | -0.057 | 0.789 | 0.115 | 0.213 | 0.4667 |
| reb | 2024 | A4 | 27583 | 1315 | 1.2963 | -0.0176 | -0.0207 | -0.0147 | 0.000 | n/a | 0.0030 | -0.059 | 0.789 | 0.116 | 0.213 | 0.4671 |
| reb | 2024 | C1 | 27583 | 1315 | 1.3583 | +0.0443 | +0.0394 | +0.0494 | 0.000 | n/a | 0.0050 | -0.045 | 0.774 | 0.103 | 0.226 | 0.4951 |
| reb | 2024 | C2 | 27583 | 1315 | 1.3831 | +0.0691 | +0.0622 | +0.0759 | 0.000 | n/a | 0.0069 | +0.016 | 0.758 | 0.115 | 0.212 | 0.5038 |
| reb | 2024 | C3 | 27583 | 1315 | 1.4289 | +0.1149 | +0.1063 | +0.1234 | 0.000 | n/a | 0.0086 | +0.011 | 0.740 | 0.120 | 0.216 | 0.5231 |
| reb | 2024 | C4 | 27583 | 1315 | 1.2963 | -0.0176 | -0.0207 | -0.0147 | 0.000 | n/a | 0.0030 | -0.059 | 0.789 | 0.116 | 0.213 | 0.4671 |
| ast | 2023 | A0 | 27619 | 1318 | 0.9059 | n/a | n/a | n/a | n/a | n/a | n/a | -0.050 | 0.788 | 0.118 | 0.214 | 0.4311 |
| ast | 2023 | A1 | 27619 | 1318 | 0.8968 | -0.0091 | -0.0110 | -0.0073 | 0.000 | 0.000 | 0.0019 | -0.033 | 0.787 | 0.115 | 0.212 | 0.4253 |
| ast | 2023 | A2 | 27619 | 1318 | 0.8996 | -0.0063 | -0.0076 | -0.0051 | 0.000 | 0.000 | 0.0013 | -0.027 | 0.787 | 0.114 | 0.211 | 0.4270 |
| ast | 2023 | A3 | 27619 | 1318 | 0.8941 | -0.0118 | -0.0139 | -0.0099 | 0.000 | n/a | 0.0020 | -0.036 | 0.788 | 0.116 | 0.212 | 0.4254 |
| ast | 2023 | A4 | 27619 | 1318 | 0.8955 | -0.0104 | -0.0122 | -0.0088 | 0.000 | n/a | 0.0017 | -0.031 | 0.788 | 0.117 | 0.210 | 0.4256 |
| ast | 2023 | C1 | 27619 | 1318 | 0.9333 | +0.0274 | +0.0242 | +0.0306 | 0.000 | n/a | 0.0032 | -0.055 | 0.765 | 0.113 | 0.234 | 0.4502 |
| ast | 2023 | C2 | 27619 | 1318 | 0.9439 | +0.0380 | +0.0337 | +0.0421 | 0.000 | n/a | 0.0042 | +0.008 | 0.770 | 0.108 | 0.207 | 0.4525 |
| ast | 2023 | C3 | 27619 | 1318 | 0.9783 | +0.0724 | +0.0667 | +0.0784 | 0.000 | n/a | 0.0059 | +0.003 | 0.752 | 0.111 | 0.210 | 0.4731 |
| ast | 2023 | C4 | 27619 | 1318 | 0.8955 | -0.0104 | -0.0122 | -0.0088 | 0.000 | n/a | 0.0017 | -0.031 | 0.788 | 0.117 | 0.210 | 0.4256 |
| ast | 2024 | A0 | 27583 | 1315 | 0.9010 | n/a | n/a | n/a | n/a | n/a | n/a | -0.066 | 0.779 | 0.126 | 0.223 | 0.4377 |
| ast | 2024 | A1 | 27583 | 1315 | 0.8916 | -0.0094 | -0.0113 | -0.0076 | 0.000 | 0.000 | 0.0019 | -0.060 | 0.782 | 0.124 | 0.222 | 0.4327 |
| ast | 2024 | A2 | 27583 | 1315 | 0.8960 | -0.0050 | -0.0063 | -0.0038 | 0.000 | 0.001 | 0.0012 | -0.047 | 0.782 | 0.120 | 0.220 | 0.4345 |
| ast | 2024 | A3 | 27583 | 1315 | 0.8908 | -0.0102 | -0.0120 | -0.0083 | 0.000 | n/a | 0.0018 | -0.059 | 0.782 | 0.123 | 0.221 | 0.4324 |
| ast | 2024 | A4 | 27583 | 1315 | 0.8915 | -0.0095 | -0.0114 | -0.0076 | 0.000 | n/a | 0.0019 | -0.060 | 0.782 | 0.124 | 0.221 | 0.4327 |
| ast | 2024 | C1 | 27583 | 1315 | 0.9339 | +0.0329 | +0.0296 | +0.0363 | 0.000 | n/a | 0.0034 | -0.073 | 0.768 | 0.114 | 0.236 | 0.4605 |
| ast | 2024 | C2 | 27583 | 1315 | 0.9488 | +0.0478 | +0.0437 | +0.0521 | 0.000 | n/a | 0.0042 | +0.004 | 0.767 | 0.110 | 0.208 | 0.4649 |
| ast | 2024 | C3 | 27583 | 1315 | 0.9802 | +0.0792 | +0.0734 | +0.0849 | 0.000 | n/a | 0.0057 | +0.002 | 0.749 | 0.112 | 0.209 | 0.4837 |
| ast | 2024 | C4 | 27583 | 1315 | 0.8915 | -0.0095 | -0.0114 | -0.0076 | 0.000 | n/a | 0.0019 | -0.060 | 0.782 | 0.124 | 0.221 | 0.4327 |
| fg3m | 2023 | A0 | 27619 | 1318 | 0.5690 | n/a | n/a | n/a | n/a | n/a | n/a | -0.015 | 0.792 | 0.112 | 0.214 | 0.4900 |
| fg3m | 2023 | A1 | 27619 | 1318 | 0.5665 | -0.0025 | -0.0037 | -0.0013 | 0.000 | 0.000 | 0.0012 | -0.018 | 0.790 | 0.112 | 0.214 | 0.4868 |
| fg3m | 2023 | A2 | 27619 | 1318 | 0.5666 | -0.0024 | -0.0034 | -0.0015 | 0.000 | 0.000 | 0.0010 | -0.013 | 0.792 | 0.110 | 0.212 | 0.4867 |
| fg3m | 2023 | A3 | 27619 | 1318 | 0.5646 | -0.0045 | -0.0058 | -0.0032 | 0.000 | n/a | 0.0013 | -0.021 | 0.791 | 0.111 | 0.213 | 0.4862 |
| fg3m | 2023 | A4 | 27619 | 1318 | 0.5659 | -0.0031 | -0.0040 | -0.0022 | 0.000 | n/a | 0.0009 | -0.009 | 0.790 | 0.113 | 0.213 | 0.4871 |
| fg3m | 2023 | C1 | 27619 | 1318 | 0.5879 | +0.0188 | +0.0167 | +0.0210 | 0.000 | n/a | 0.0022 | -0.092 | 0.774 | 0.119 | 0.239 | 0.5151 |
| fg3m | 2023 | C2 | 27619 | 1318 | 0.5897 | +0.0207 | +0.0179 | +0.0235 | 0.000 | n/a | 0.0028 | +0.004 | 0.776 | 0.103 | 0.204 | 0.5124 |
| fg3m | 2023 | C3 | 27619 | 1318 | 0.6138 | +0.0447 | +0.0407 | +0.0487 | 0.000 | n/a | 0.0040 | +0.002 | 0.758 | 0.107 | 0.207 | 0.5509 |
| fg3m | 2023 | C4 | 27619 | 1318 | 0.5659 | -0.0031 | -0.0040 | -0.0022 | 0.000 | n/a | 0.0009 | -0.009 | 0.790 | 0.113 | 0.213 | 0.4871 |
| fg3m | 2024 | A0 | 27583 | 1315 | 0.5923 | n/a | n/a | n/a | n/a | n/a | n/a | -0.001 | 0.784 | 0.115 | 0.217 | 0.4948 |
| fg3m | 2024 | A1 | 27583 | 1315 | 0.5887 | -0.0036 | -0.0047 | -0.0024 | 0.000 | 0.000 | 0.0011 | -0.005 | 0.785 | 0.115 | 0.217 | 0.4914 |
| fg3m | 2024 | A2 | 27583 | 1315 | 0.5908 | -0.0015 | -0.0024 | -0.0005 | 0.002 | 0.002 | 0.0009 | -0.001 | 0.785 | 0.112 | 0.215 | 0.4925 |
| fg3m | 2024 | A3 | 27583 | 1315 | 0.5872 | -0.0051 | -0.0062 | -0.0039 | 0.000 | n/a | 0.0012 | -0.005 | 0.785 | 0.115 | 0.216 | 0.4907 |
| fg3m | 2024 | A4 | 27583 | 1315 | 0.5882 | -0.0041 | -0.0052 | -0.0030 | 0.000 | n/a | 0.0011 | -0.004 | 0.785 | 0.116 | 0.217 | 0.4914 |
| fg3m | 2024 | C1 | 27583 | 1315 | 0.6136 | +0.0213 | +0.0192 | +0.0234 | 0.000 | n/a | 0.0021 | -0.069 | 0.769 | 0.117 | 0.237 | 0.5205 |
| fg3m | 2024 | C2 | 27583 | 1315 | 0.6168 | +0.0245 | +0.0216 | +0.0274 | 0.000 | n/a | 0.0029 | +0.014 | 0.777 | 0.102 | 0.204 | 0.5200 |
| fg3m | 2024 | C3 | 27583 | 1315 | 0.6461 | +0.0538 | +0.0499 | +0.0578 | 0.000 | n/a | 0.0040 | +0.008 | 0.757 | 0.108 | 0.209 | 0.5645 |
| fg3m | 2024 | C4 | 27583 | 1315 | 0.5882 | -0.0041 | -0.0052 | -0.0030 | 0.000 | n/a | 0.0011 | -0.004 | 0.785 | 0.116 | 0.217 | 0.4914 |

## Mechanism (dCRPS vs A0; A1/A2 minus A3 and minus A4 with CI)

| stat | season | A1 | A2 | A3 ceiling | A4 | A1 share of A3 | A1-A4 [lo,hi] | A2-A4 [lo,hi] | A1-A3 [lo,hi] |
|---|---|---|---|---|---|---|---|---|---|
| pts | 2023 | -0.0336 | -0.0369 | -0.0426 | -0.0380 | 0.789 | +0.0044 [+0.0014,+0.0073] | +0.0011 [-0.0021,+0.0043] | +0.0090 [+0.0050,+0.0128] |
| pts | 2024 | -0.0424 | -0.0404 | -0.0448 | -0.0434 | 0.945 | +0.0010 [-0.0009,+0.0030] | +0.0029 [+0.0004,+0.0054] | +0.0025 [+0.0004,+0.0045] |
| reb | 2023 | -0.0154 | -0.0122 | -0.0175 | -0.0167 | 0.879 | +0.0013 [+0.0005,+0.0021] | +0.0044 [+0.0026,+0.0061] | +0.0021 [+0.0010,+0.0033] |
| reb | 2024 | -0.0176 | -0.0121 | -0.0185 | -0.0176 | 0.954 | +0.0000 [-0.0002,+0.0002] | +0.0055 [+0.0037,+0.0074] | +0.0008 [+0.0001,+0.0016] |
| ast | 2023 | -0.0091 | -0.0063 | -0.0118 | -0.0104 | 0.765 | +0.0013 [+0.0002,+0.0025] | +0.0040 [+0.0027,+0.0055] | +0.0028 [+0.0016,+0.0039] |
| ast | 2024 | -0.0094 | -0.0050 | -0.0102 | -0.0095 | 0.927 | +0.0001 [-0.0002,+0.0003] | +0.0045 [+0.0031,+0.0060] | +0.0007 [+0.0001,+0.0014] |
| fg3m | 2023 | -0.0025 | -0.0024 | -0.0045 | -0.0031 | 0.558 | +0.0006 [-0.0002,+0.0016] | +0.0007 [-0.0003,+0.0018] | +0.0020 [+0.0009,+0.0032] |
| fg3m | 2024 | -0.0036 | -0.0015 | -0.0051 | -0.0041 | 0.706 | +0.0005 [+0.0000,+0.0011] | +0.0027 [+0.0015,+0.0038] | +0.0015 [+0.0007,+0.0023] |

## Per-cell table (best chosen on 2023; descriptive unless the cell rule holds)

| stat | season | cell | n | n_games | best | dcrps_best_vs_c0 | ci_lo | ci_hi | p_bh | eligible | claim |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pts | 2023 | <10|warm | 13495 | 1318 | C4 | -0.0485 | -0.0585 | -0.0393 | 0.001 | True | False |
| pts | 2023 | 10-17|warm | 7275 | 1318 | C4 | -0.0375 | -0.0496 | -0.0260 | 0.001 | True | False |
| pts | 2023 | 17-24|warm | 3489 | 1284 | C4 | -0.0139 | -0.0286 | +0.0011 | 0.106 | True | False |
| pts | 2023 | 24+|warm | 1730 | 1015 | C1 | -0.0109 | -0.0712 | +0.0477 | 0.715 | True | False |
| pts | 2023 | <10+10-17+17-24+24+|cold | 1630 | 795 | C4 | -0.0481 | -0.0739 | -0.0228 | 0.001 | True | False |
| pts | 2024 | <10|warm | 14130 | 1315 | C4 | -0.0576 | -0.0689 | -0.0457 | 0.001 | True | True |
| pts | 2024 | 10-17|warm | 7064 | 1315 | C4 | -0.0374 | -0.0488 | -0.0254 | 0.001 | True | True |
| pts | 2024 | 17-24|warm | 3563 | 1263 | C4 | -0.0091 | -0.0217 | +0.0031 | 0.191 | True | False |
| pts | 2024 | 24+|warm | 1503 | 953 | C1 | +0.0663 | +0.0077 | +0.1250 | 0.046 | True | False |
| pts | 2024 | <10+10-17+17-24+24+|cold | 1323 | 723 | C4 | -0.0623 | -0.0906 | -0.0334 | 0.001 | True | True |
| reb | 2023 | <10|warm | 13495 | 1318 | C4 | -0.0252 | -0.0299 | -0.0206 | 0.001 | True | False |
| reb | 2023 | 10-17|warm | 7275 | 1318 | C4 | -0.0093 | -0.0136 | -0.0053 | 0.001 | True | False |
| reb | 2023 | 17-24|warm | 3489 | 1284 | C4 | -0.0013 | -0.0055 | +0.0027 | 0.664 | True | False |
| reb | 2023 | 24+|warm | 1730 | 1015 | C0 | +0.0000 | +0.0000 | +0.0000 | 1.000 | True | False |
| reb | 2023 | <10+10-17+17-24+24+|cold | 1630 | 795 | C4 | -0.0297 | -0.0414 | -0.0187 | 0.001 | True | False |
| reb | 2024 | <10|warm | 14130 | 1315 | C4 | -0.0258 | -0.0306 | -0.0211 | 0.001 | True | True |
| reb | 2024 | 10-17|warm | 7064 | 1315 | C4 | -0.0107 | -0.0152 | -0.0061 | 0.001 | True | True |
| reb | 2024 | 17-24|warm | 3563 | 1263 | C4 | -0.0024 | -0.0058 | +0.0010 | 0.232 | True | False |
| reb | 2024 | 24+|warm | 1503 | 953 | C0 | +0.0000 | +0.0000 | +0.0000 | 1.000 | True | False |
| reb | 2024 | <10+10-17+17-24+24+|cold | 1323 | 723 | C4 | -0.0268 | -0.0403 | -0.0129 | 0.002 | True | True |
| ast | 2023 | <10|warm | 13495 | 1318 | C4 | -0.0132 | -0.0158 | -0.0108 | 0.001 | True | False |
| ast | 2023 | 10-17|warm | 7275 | 1318 | C4 | -0.0077 | -0.0111 | -0.0044 | 0.001 | True | False |
| ast | 2023 | 17-24|warm | 3489 | 1284 | C4 | -0.0076 | -0.0118 | -0.0035 | 0.001 | True | False |
| ast | 2023 | 24+|warm | 1730 | 1015 | C1 | -0.0049 | -0.0206 | +0.0110 | 0.664 | True | False |
| ast | 2023 | <10+10-17+17-24+24+|cold | 1630 | 795 | C4 | -0.0138 | -0.0208 | -0.0066 | 0.001 | True | False |
| ast | 2024 | <10|warm | 14130 | 1315 | C4 | -0.0138 | -0.0169 | -0.0108 | 0.001 | True | True |
| ast | 2024 | 10-17|warm | 7064 | 1315 | C4 | -0.0054 | -0.0090 | -0.0019 | 0.001 | True | True |
| ast | 2024 | 17-24|warm | 3563 | 1263 | C4 | -0.0022 | -0.0063 | +0.0019 | 0.352 | True | False |
| ast | 2024 | 24+|warm | 1503 | 953 | C1 | +0.0198 | +0.0024 | +0.0375 | 0.033 | True | False |
| ast | 2024 | <10+10-17+17-24+24+|cold | 1323 | 723 | C4 | -0.0154 | -0.0240 | -0.0074 | 0.001 | True | True |
| fg3m | 2023 | <10|warm | 13495 | 1318 | C4 | -0.0031 | -0.0044 | -0.0018 | 0.001 | True | False |
| fg3m | 2023 | 10-17|warm | 7275 | 1318 | C4 | -0.0045 | -0.0063 | -0.0026 | 0.001 | True | False |
| fg3m | 2023 | 17-24|warm | 3489 | 1284 | C4 | -0.0034 | -0.0060 | -0.0008 | 0.014 | True | False |
| fg3m | 2023 | 24+|warm | 1730 | 1015 | C1 | -0.0041 | -0.0182 | +0.0099 | 0.664 | True | False |
| fg3m | 2023 | <10+10-17+17-24+24+|cold | 1630 | 795 | C4 | -0.0008 | -0.0043 | +0.0027 | 0.696 | True | False |
| fg3m | 2024 | <10|warm | 14130 | 1315 | C4 | -0.0061 | -0.0078 | -0.0045 | 0.001 | True | True |
| fg3m | 2024 | 10-17|warm | 7064 | 1315 | C4 | -0.0007 | -0.0028 | +0.0016 | 0.620 | True | False |
| fg3m | 2024 | 17-24|warm | 3563 | 1263 | C4 | -0.0026 | -0.0050 | -0.0002 | 0.049 | True | False |
| fg3m | 2024 | 24+|warm | 1503 | 953 | C1 | -0.0025 | -0.0164 | +0.0127 | 0.804 | True | False |
| fg3m | 2024 | <10+10-17+17-24+24+|cold | 1323 | 723 | C4 | -0.0059 | -0.0096 | -0.0020 | 0.009 | True | True |

## Switch rate (R1 leaves C0)

| stat | season | cell | n | switch_rate |
|---|---|---|---|---|
| pts | 2023 | <10|warm | 13495 | 0.965 |
| pts | 2023 | 10-17|warm | 7275 | 0.955 |
| pts | 2023 | 17-24|warm | 3489 | 0.815 |
| pts | 2023 | 24+|warm | 1730 | 0.661 |
| pts | 2023 | <10+10-17+17-24+24+|cold | 1630 | 0.691 |
| pts | 2024 | <10|warm | 14130 | 1.000 |
| pts | 2024 | 10-17|warm | 7064 | 1.000 |
| pts | 2024 | 17-24|warm | 3563 | 0.825 |
| pts | 2024 | 24+|warm | 1503 | 0.552 |
| pts | 2024 | <10+10-17+17-24+24+|cold | 1323 | 1.000 |
| reb | 2023 | <10|warm | 13495 | 0.965 |
| reb | 2023 | 10-17|warm | 7275 | 0.955 |
| reb | 2023 | 17-24|warm | 3489 | 0.336 |
| reb | 2023 | 24+|warm | 1730 | 0.402 |
| reb | 2023 | <10+10-17+17-24+24+|cold | 1630 | 0.691 |
| reb | 2024 | <10|warm | 14130 | 1.000 |
| reb | 2024 | 10-17|warm | 7064 | 1.000 |
| reb | 2024 | 17-24|warm | 3563 | 1.000 |
| reb | 2024 | 24+|warm | 1503 | 0.480 |
| reb | 2024 | <10+10-17+17-24+24+|cold | 1323 | 1.000 |
| ast | 2023 | <10|warm | 13495 | 0.965 |
| ast | 2023 | 10-17|warm | 7275 | 0.781 |
| ast | 2023 | 17-24|warm | 3489 | 0.815 |
| ast | 2023 | 24+|warm | 1730 | 0.616 |
| ast | 2023 | <10+10-17+17-24+24+|cold | 1630 | 0.691 |
| ast | 2024 | <10|warm | 14130 | 1.000 |
| ast | 2024 | 10-17|warm | 7064 | 1.000 |
| ast | 2024 | 17-24|warm | 3563 | 1.000 |
| ast | 2024 | 24+|warm | 1503 | 0.390 |
| ast | 2024 | <10+10-17+17-24+24+|cold | 1323 | 1.000 |
| fg3m | 2023 | <10|warm | 13495 | 0.965 |
| fg3m | 2023 | 10-17|warm | 7275 | 0.955 |
| fg3m | 2023 | 17-24|warm | 3489 | 0.815 |
| fg3m | 2023 | 24+|warm | 1730 | 0.776 |
| fg3m | 2023 | <10+10-17+17-24+24+|cold | 1630 | 0.406 |
| fg3m | 2024 | <10|warm | 14130 | 1.000 |
| fg3m | 2024 | 10-17|warm | 7064 | 0.885 |
| fg3m | 2024 | 17-24|warm | 3563 | 1.000 |
| fg3m | 2024 | 24+|warm | 1503 | 0.762 |
| fg3m | 2024 | <10+10-17+17-24+24+|cold | 1323 | 1.000 |

## C4 fallback share (rows without a T-30 row fall back to C0)

- pts: 2023 0.0000, 2024 0.0000
- reb: 2023 0.0000, 2024 0.0000
- ast: 2023 0.0000, 2024 0.0000
- fg3m: 2023 0.0000, 2024 0.0000

## Cell merges (check 2)

```
{
 "pts": {
  "cold=0": {
   "counts_2023": [
    13495,
    7275,
    3489,
    1730
   ],
   "groups": [
    [
     0
    ],
    [
     1
    ],
    [
     2
    ],
    [
     3
    ]
   ]
  },
  "cold=1": {
   "counts_2023": [
    1492,
    91,
    47,
    0
   ],
   "groups": [
    [
     0,
     1,
     2,
     3
    ]
   ]
  },
  "n_2023": {
   "<10|warm": 13495,
   "10-17|warm": 7275,
   "17-24|warm": 3489,
   "24+|warm": 1730,
   "<10+10-17+17-24+24+|cold": 1630
  }
 },
 "reb": {
  "cold=0": {
   "counts_2023": [
    13495,
    7275,
    3489,
    1730
   ],
   "groups": [
    [
     0
    ],
    [
     1
    ],
    [
     2
    ],
    [
     3
    ]
   ]
  },
  "cold=1": {
   "counts_2023": [
    1492,
    91,
    47,
    0
   ],
   "groups": [
    [
     0,
     1,
     2,
     3
    ]
   ]
  },
  "n_2023": {
   "<10|warm": 13495,
   "10-17|warm": 7275,
   "17-24|warm": 3489,
   "24+|warm": 1730,
   "<10+10-17+17-24+24+|cold": 1630
  }
 },
 "ast": {
  "cold=0": {
   "counts_2023": [
    13495,
    7275,
    3489,
    1730
   ],
   "groups": [
    [
     0
    ],
    [
     1
    ],
    [
     2
    ],
    [
     3
    ]
   ]
  },
  "cold=1": {
   "counts_2023": [
    1492,
    91,
    47,
    0
   ],
   "groups": [
    [
     0,
     1,
     2,
     3
    ]
   ]
  },
  "n_2023": {
   "<10|warm": 13495,
   "10-17|warm": 7275,
   "17-24|warm": 3489,
   "24+|warm": 1730,
   "<10+10-17+17-24+24+|cold": 1630
  }
 },
 "fg3m": {
  "cold=0": {
   "counts_2023": [
    13495,
    7275,
    3489,
    1730
   ],
   "groups": [
    [
     0
    ],
    [
     1
    ],
    [
     2
    ],
    [
     3
    ]
   ]
  },
  "cold=1": {
   "counts_2023": [
    1492,
    91,
    47,
    0
   ],
   "groups": [
    [
     0,
     1,
     2,
     3
    ]
   ]
  },
  "n_2023": {
   "<10|warm": 13495,
   "10-17|warm": 7275,
   "17-24|warm": 3489,
   "24+|warm": 1730,
   "<10+10-17+17-24+24+|cold": 1630
  }
 }
}
```

## Unstated details (chosen before scoring)

- C1 is the OOF equivalent in the harness: Normal(m10_<stat>, sd10_<stat>) from the production player states (decaying weights 0.5**(age/10) over ALL prior played games, same weighted-variance * n/(n-1) formula as nba.props.forward.recency_weighted_dist, halflife 10 games = ForwardConfig default; equivalence is unit-tested), Normal for all four stats as the production fallback is, quantiles at the 199 CRPS taus, then to_integer_support
- C2/C3 windows are the player's last 10 / 5 PLAYED games (minutes > 0) strictly before the row's (game_date, game_id), crossing seasons like the production states; mean = window mean, dispersion = window sample variance (ddof 1); pts Normal (std 0 -> league default std), reb/ast/fg3m NegBin by method of moments (variance floored at mean*(1+1e-6), mean floored at 1e-6, i.e. near-Poisson when under-dispersed); every row has n_prior >= 5 so the window has >= 5 games
- C4 is REBUILT on the integer grid (the stored reports/lineups_known rows hold continuous 19-quantile q10/q90 and a continuous CRPS, not an integer grid): same harness as C0 (collect_arm, month blocks, seed 0), feature names stat_feature_names(stat, lineups_known=True) on build_features(..., lineups_known=True). The t30_* columns are the box-score starter PROXY of docs/LINEUPS_KNOWN.md (no real T-30 snapshots exist for seasons <= 2024)
- C4 'has a T-30 row' = t30_starter is not null on that row; rows without one fall back to the C0 grid; the fallback share is reported
- block = calendar month ([start, end) as month_blocks); trailing rows for a scored block are ALL rows with game_date < block start from both seasons (the 2023-10 block therefore routes to C0 everywhere); weight of a trailing row = 0.5 ** ((start - game_date).days / 60)
- '>= 300 trailing rows' is the raw (unweighted) count of trailing rows in the cell; trailing CRPS is the weighted mean integer CRPS of each candidate on those rows
- R1 picks the lowest weighted trailing CRPS; ties break toward the lower candidate index (C0 first). R2 weights = softmax(-trailing_crps / 0.05) per cell; cells under 300 trailing rows use weight 1 on C0
- R2 'quantile mixture' = the repo's pool convention (research/stack/scoring.py pool): weighted average of the 199-quantile grids (Vincentization), then to_integer_support
- cells are per stat: ppg tier of m10_pts (the production recency mean of prior pts, halflife 10) with edges [<10, 10-17, 17-24, 24+) x cold (n_prior < 20); 8 cells per stat, 32 in all
- check 2 merge: counted on 2023 rows of each stat; within each cold flag the lowest tier group with < 300 rows merges into the next tier up; the top group, if still short, merges down into its neighbour; the merged definition is fixed from 2023 and used for 2024; check 2 passes if every merged cell has >= 300 rows in 2023
- check 1: coverage = share of production (A0) rows with a finite candidate forecast; C0-C3 gated at >= 0.95 in both seasons; C4's coverage (t30 non-null share) is reported separately, not gated
- check 3: for every block of every stat, rows dated on/after the block start get dates +1 day and garbage CRPS; the trailing counts, trailing CRPS, R1 picks and R2 weights for the block must be bit-identical; and a prior-row perturbation must change them (not inert)
- A3 oracle: per month block and cell, the candidate with the lowest mean integer CRPS on that block's rows (leak by construction); A4: per stat the candidate with the lowest mean integer CRPS over all 2023 rows
- per-cell table: best = candidate with the lowest mean CRPS among C0-C4 in that cell over the 2023 rows (selection season); dcrps_best_vs_c0 is best minus C0 per season (2023 is in-sample, 2024 is the confirmation); eligibility (>= 500 rows and >= 150 distinct games) is evaluated per season; BH over all eligible cells of that season (all stats); claim = 2024 only, eligible, CI upper < 0, BH p < 0.05 and point <= -0.005 (same floor as pass rule 1); 2023 claims are never made
- pass rule 1 for A1 and A2 is evaluated in BOTH seasons; the BH family is the four stats within a season, separately for A1 and A2; guards, slice guard (cells with n >= 300 in 2024, arm minus A0 point > +0.01 fails) and rule 3 (arm minus A4 point <= -0.003, 2024 all rows) use 2024; rule 3 additionally needs checks 1-4 ok; a stat passes if A1 or A2 passes
- pit_q10/pit_q20 = P(PIT <= 0.10 / 0.20), cov80 = P(0.10 < PIT <= 0.90) with the FOUR_FACTORS randomized integer PIT (seed 0); tll = the FOUR_FACTORS thresholds (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2)
- bias = mean(y - mean of the 199-quantile integer grid); n_games = distinct game_id; MDE = half the CI width of the arm minus A0 delta
- the 2023 / 2024 split is games.season of the row; the walk-forward OOF rows (2023-10 .. 2025-06 calendar months with games) are exactly the A0 cache rows (55202)

## Proposed ledger rows (draft, unnumbered; maintainer appends)

| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | pts 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0336 | -0.0403 | -0.0273 | game | 0.000 | MDE 0.0065; bias -0.149 -> -0.121; cov80 0.772 -> 0.773; A3 oracle -0.0426, A4 best-single -0.0380 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | pts 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0369 | -0.0421 | -0.0320 | game | 0.000 | MDE 0.0051; bias -0.149 -> -0.110; cov80 0.772 -> 0.774; A3 oracle -0.0426, A4 best-single -0.0380 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | pts 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0424 | -0.0495 | -0.0351 | game | 0.000 | MDE 0.0072; bias -0.113 -> -0.131; cov80 0.762 -> 0.764; A3 oracle -0.0448, A4 best-single -0.0434 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | pts 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0404 | -0.0459 | -0.0350 | game | 0.001 | MDE 0.0054; bias -0.113 -> -0.104; cov80 0.762 -> 0.765; A3 oracle -0.0448, A4 best-single -0.0434 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | reb 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0154 | -0.0181 | -0.0128 | game | 0.000 | MDE 0.0027; bias -0.043 -> -0.028; cov80 0.792 -> 0.792; A3 oracle -0.0175, A4 best-single -0.0167 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | reb 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0122 | -0.0143 | -0.0103 | game | 0.000 | MDE 0.0020; bias -0.043 -> -0.022; cov80 0.792 -> 0.793; A3 oracle -0.0175, A4 best-single -0.0167 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | reb 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0176 | -0.0207 | -0.0147 | game | 0.000 | MDE 0.0030; bias -0.051 -> -0.058; cov80 0.788 -> 0.789; A3 oracle -0.0185, A4 best-single -0.0176 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | reb 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0121 | -0.0140 | -0.0101 | game | 0.001 | MDE 0.0019; bias -0.051 -> -0.037; cov80 0.788 -> 0.789; A3 oracle -0.0185, A4 best-single -0.0176 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | ast 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0091 | -0.0110 | -0.0073 | game | 0.000 | MDE 0.0019; bias -0.050 -> -0.033; cov80 0.788 -> 0.787; A3 oracle -0.0118, A4 best-single -0.0104 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | ast 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0063 | -0.0076 | -0.0051 | game | 0.000 | MDE 0.0013; bias -0.050 -> -0.027; cov80 0.788 -> 0.787; A3 oracle -0.0118, A4 best-single -0.0104 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | ast 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0094 | -0.0113 | -0.0076 | game | 0.000 | MDE 0.0019; bias -0.066 -> -0.060; cov80 0.779 -> 0.782; A3 oracle -0.0102, A4 best-single -0.0095 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | ast 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0050 | -0.0063 | -0.0038 | game | 0.001 | MDE 0.0012; bias -0.066 -> -0.047; cov80 0.779 -> 0.782; A3 oracle -0.0102, A4 best-single -0.0095 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | fg3m 2023 A1 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0025 | -0.0037 | -0.0013 | game | 0.000 | MDE 0.0012; bias -0.015 -> -0.018; cov80 0.792 -> 0.790; A3 oracle -0.0045, A4 best-single -0.0031 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | fg3m 2023 A2 vs A0 all rows, integer-support CRPS, n=27619 rows / 1318 games | -0.0024 | -0.0034 | -0.0015 | game | 0.000 | MDE 0.0010; bias -0.015 -> -0.013; cov80 0.792 -> 0.792; A3 oracle -0.0045, A4 best-single -0.0031 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | fg3m 2024 A1 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0036 | -0.0047 | -0.0024 | game | 0.000 | MDE 0.0011; bias -0.001 -> -0.005; cov80 0.784 -> 0.785; A3 oracle -0.0051, A4 best-single -0.0041 | not holdout (<=2024) |
| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a) | fg3m 2024 A2 vs A0 all rows, integer-support CRPS, n=27583 rows / 1315 games | -0.0015 | -0.0024 | -0.0005 | game | 0.002 | MDE 0.0009; bias -0.001 -> -0.001; cov80 0.784 -> 0.785; A3 oracle -0.0051, A4 best-single -0.0041 | not holdout (<=2024) |
