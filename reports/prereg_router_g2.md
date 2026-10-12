# ROUTER_G2: results under rule sha256 2dc26443 (TAU 0.9)

| cell | season | n | A0 | arm | delta [95% CI, game-clustered] | BH p | cov80 | guards |
|---|---|---|---|---|---|---|---|---|
| E1p|pts | 2023 | 27619 | 3.1402 | 3.1309 | -0.0093 [-0.0145, -0.0044] | 0.0010 | 0.793 | FAIL g2b_minutes<5 |
| E1p|pts | 2024 | 27701 | 3.1768 | 3.1710 | -0.0059 [-0.0110, -0.0010] | 0.0260 | 0.788 | FAIL g3_concentration,g5_proxy_safe,g2b_minutes<5 |
| E2p|pts | 2023 | 27619 | 3.1402 | 3.1292 | -0.0111 [-0.0147, -0.0072] | 0.0007 | 0.787 | ok |
| E2p|pts | 2024 | 27701 | 3.1768 | 3.1651 | -0.0118 [-0.0151, -0.0084] | 0.0007 | 0.785 | ok |
| E4p|pts | 2023 | 27619 | 3.1402 | 3.1322 | -0.0081 [-0.0133, -0.0027] | 0.0010 | 0.791 | FAIL g2b_minutes<5 |
| E4p|pts | 2024 | 27701 | 3.1768 | 3.1707 | -0.0061 [-0.0112, -0.0012] | 0.0216 | 0.788 | FAIL g3_concentration,g5_proxy_safe,g2b_minutes<5 |
| E2p|reb | 2023 | 27619 | 1.2882 | 1.2854 | -0.0028 [-0.0039, -0.0016] | 0.0007 | 0.802 | ok |
| E2p|reb | 2024 | 27701 | 1.3114 | 1.3085 | -0.0029 [-0.0038, -0.0020] | 0.0007 | 0.801 | ok |
| E2p|ast | 2023 | 27619 | 0.9020 | 0.9003 | -0.0017 [-0.0024, -0.0009] | 0.0007 | 0.795 | FAIL g3_concentration |
| E2p|ast | 2024 | 27701 | 0.8985 | 0.8965 | -0.0020 [-0.0026, -0.0014] | 0.0007 | 0.795 | FAIL g3_concentration |
| E2p|fg3m | 2023 | 27619 | 0.5662 | 0.5639 | -0.0023 [-0.0030, -0.0016] | 0.0007 | 0.801 | FAIL g3_concentration |
| E2p|fg3m | 2024 | 27701 | 0.5902 | 0.5875 | -0.0026 [-0.0033, -0.0019] | 0.0007 | 0.796 | FAIL g3_concentration |

| cell | verdict | placebo ok | shuffle ok | flag |
|---|---|---|---|---|
| E1p|pts | FAIL_GUARD | {'PL_G': False, 'PL_A1': True, 'CONST': False} | True | False |
| E2p|pts | FAIL_GUARD | {'PL_G': False, 'PL_SEQ': True, 'CONST': False} | True | False |
| E4p|pts | FAIL_GUARD | {'PL_G': False, 'PL_A1': True, 'PL_SEQ': False, 'CONST': False} | True | True |
| E2p|reb | FAIL | {'PL_G': False, 'PL_SEQ': True, 'CONST': False} | True | False |
| E2p|ast | FAIL | {'PL_G': False, 'PL_SEQ': True, 'CONST': False} | True | False |
| E2p|fg3m | FAIL | {'PL_G': False, 'PL_SEQ': True, 'CONST': False} | True | False |

Eligible for a holdout row (Devin's word): none
