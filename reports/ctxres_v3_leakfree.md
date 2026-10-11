# ctxres_v3 leak-free hybrid -- replication report

Pre-registration: `docs/CTXRES_V3_LEAKFREE.md`. Season 2024 was already seen for this recipe: a REPLICATION, not a confirmation. Season 2025 untouched.

Run header: prereg_sha256=d684efd134082e20606d00de189efd0d23088011b6ab4a1c9ec2d29ced5a5183, xgboost=3.4.1, budget=full, seed=20261008

## Season 2024 -- `hybrid_lf` - `v1_prod` (CRPS, negative = hybrid better)

| stat | n | dCRPS [95% CI, game-clustered] | BH q | bias | PIT cov80 | naive cov80 | slice regressions | checks failed | PASS |
|---|---|---|---|---|---|---|---|---|---|
| pts | 27583 | -0.0072 [-0.0120, -0.0021] | 0.0070 | +0.025 | 0.791 | 0.800 | - | - | True |
| reb | 27583 | -0.0086 [-0.0108, -0.0064] | 0.0007 | +0.038 | 0.785 | 0.872 | - | - | True |
| ast | 27583 | -0.0173 [-0.0190, -0.0155] | 0.0007 | +0.054 | 0.789 | 0.906 | - | - | True |
| fg3m | 27583 | -0.0249 [-0.0262, -0.0237] | 0.0007 | +0.020 | 0.793 | 0.937 | - | - | True |

## Season 2023 -- `hybrid_lf` - `v1_prod` (CRPS, negative = hybrid better)

| stat | n | dCRPS [95% CI, game-clustered] | BH q | bias | PIT cov80 | naive cov80 | slice regressions | checks failed | PASS |
|---|---|---|---|---|---|---|---|---|---|
| pts | 27619 | -0.0138 [-0.0196, -0.0080] | 0.0005 | +0.196 | 0.795 | 0.807 | - | - | True |
| reb | 27619 | -0.0072 [-0.0096, -0.0047] | 0.0005 | +0.077 | 0.785 | 0.874 | - | - | True |
| ast | 27619 | -0.0183 [-0.0202, -0.0163] | 0.0005 | +0.053 | 0.792 | 0.908 | - | - | True |
| fg3m | 27619 | -0.0274 [-0.0287, -0.0262] | 0.0005 | +0.025 | 0.800 | 0.941 | - | - | True |

## Classification (pre-registered)

| stat | pass 2024 | 2023 CI upper < 0 | replicates |
|---|---|---|---|
| pts | True | True | True |
| reb | True | True | True |
| ast | True | True | True |
| fg3m | True | True | True |

**Replicates overall: True** (a replication of a previously seen result; not a confirmation; no registration / promotion).

## Slices (n >= 300, 2024, dCRPS)

* pts: sl_tm_out=report_none_out n=4337 -0.0026; sl_tm_out=teammate_out n=23228 -0.0080; sl_min_change=minutes_down n=3733 -0.0021; sl_min_change=minutes_flat n=19504 -0.0082; sl_min_change=minutes_up n=4346 -0.0069; sl_phase=first15 n=4621 -0.0131; sl_phase=rest n=22962 -0.0060; sl_role=bench n=15526 -0.0042; sl_role=starter n=12057 -0.0110
* reb: sl_tm_out=report_none_out n=4337 -0.0007; sl_tm_out=teammate_out n=23228 -0.0101; sl_min_change=minutes_down n=3733 -0.0048; sl_min_change=minutes_flat n=19504 -0.0072; sl_min_change=minutes_up n=4346 -0.0183; sl_phase=first15 n=4621 -0.0147; sl_phase=rest n=22962 -0.0074; sl_role=bench n=15526 -0.0079; sl_role=starter n=12057 -0.0095
* ast: sl_tm_out=report_none_out n=4337 -0.0122; sl_tm_out=teammate_out n=23228 -0.0182; sl_min_change=minutes_down n=3733 -0.0215; sl_min_change=minutes_flat n=19504 -0.0158; sl_min_change=minutes_up n=4346 -0.0201; sl_phase=first15 n=4621 -0.0184; sl_phase=rest n=22962 -0.0170; sl_role=bench n=15526 -0.0184; sl_role=starter n=12057 -0.0158
* fg3m: sl_tm_out=report_none_out n=4337 -0.0241; sl_tm_out=teammate_out n=23228 -0.0251; sl_min_change=minutes_down n=3733 -0.0208; sl_min_change=minutes_flat n=19504 -0.0257; sl_min_change=minutes_up n=4346 -0.0252; sl_phase=first15 n=4621 -0.0257; sl_phase=rest n=22962 -0.0248; sl_role=bench n=15526 -0.0246; sl_role=starter n=12057 -0.0253

## Descriptive (no decision)

### 2024 threshold metrics (raw)

| stat | variant | n | thr log loss | Brier | ECE |
|---|---|---|---|---|---|
| pts | hybrid_lf | 27583 | 0.2873 | 0.0896 | 0.0048 |
| pts | v1_prod | 27583 | 0.2857 | 0.0898 | 0.0060 |
| pts | alt_lf | 27583 | 0.2926 | 0.0903 | 0.0121 |
| reb | hybrid_lf | 27583 | 0.3539 | 0.1125 | 0.0064 |
| reb | v1_prod | 27583 | 0.3529 | 0.1125 | 0.0038 |
| reb | alt_lf | 27583 | 0.3547 | 0.1125 | 0.0053 |
| ast | hybrid_lf | 27583 | 0.3075 | 0.0977 | 0.0072 |
| ast | v1_prod | 27583 | 0.3082 | 0.0978 | 0.0061 |
| ast | alt_lf | 27583 | 0.3095 | 0.0978 | 0.0067 |
| fg3m | hybrid_lf | 27583 | 0.4006 | 0.1307 | 0.0079 |
| fg3m | v1_prod | 27583 | 0.4034 | 0.1312 | 0.0137 |
| fg3m | alt_lf | 27583 | 0.4041 | 0.1316 | 0.0044 |

### 2024 paired CRPS / log-loss deltas

| stat | comparison | n | delta [95% CI] |
|---|---|---|---|
| pts | hybrid_lf-recency_normal | 27583 | -0.1194 [-0.1307, -0.1080] |
| pts | v1_prod-recency_normal | 27583 | -0.1122 [-0.1248, -0.0992] |
| pts | alt_lf-hybrid_lf | 27583 | +0.0675 [+0.0614, +0.0736] |
| pts | hybrid_lf-v1_prod_tll | 27583 | +0.0015 [+0.0007, +0.0024] |
| reb | hybrid_lf-recency_normal | 27583 | -0.0524 [-0.0576, -0.0473] |
| reb | v1_prod-recency_normal | 27583 | -0.0438 [-0.0489, -0.0387] |
| reb | alt_lf-hybrid_lf | 27583 | +0.0058 [+0.0037, +0.0080] |
| reb | hybrid_lf-v1_prod_tll | 27583 | +0.0011 [+0.0001, +0.0021] |
| ast | hybrid_lf-recency_normal | 27583 | -0.0500 [-0.0534, -0.0466] |
| ast | v1_prod-recency_normal | 27583 | -0.0327 [-0.0362, -0.0293] |
| ast | alt_lf-hybrid_lf | 27583 | +0.0131 [+0.0116, +0.0146] |
| ast | hybrid_lf-v1_prod_tll | 27583 | -0.0007 [-0.0016, +0.0002] |
| fg3m | hybrid_lf-recency_normal | 27583 | -0.0423 [-0.0442, -0.0405] |
| fg3m | v1_prod-recency_normal | 27583 | -0.0174 [-0.0195, -0.0153] |
| fg3m | alt_lf-hybrid_lf | 27583 | +0.0096 [+0.0085, +0.0108] |
| fg3m | hybrid_lf-v1_prod_tll | 27583 | -0.0028 [-0.0038, -0.0019] |

### 2024 cpu_vs_gpu_ref_fixed (CRPS delta, replication diagnostic)

* pts: n=27583 +0.00027 [-0.00005, +0.00055]
* reb: n=27583 -0.00052 [-0.00132, +0.00035]
* ast: n=27583 -0.00085 [-0.00159, -0.00010]
* fg3m: n=27583 +0.00018 [-0.00018, +0.00052]

### 2024 local_vs_stored_v1_prod (CRPS delta, replication diagnostic)

* pts: n=27583 +0.00000 [+0.00000, +0.00000]
* reb: n=27583 +0.00000 [+0.00000, +0.00000]
* ast: n=27583 +0.00000 [+0.00000, +0.00000]
* fg3m: n=27583 +0.00000 [+0.00000, +0.00000]

### 2024 per-block dCRPS (hybrid_lf - v1_prod)

* pts: 202410:-0.011(n=1407), 202411:-0.013(n=4624), 202412:-0.018(n=3970), 202501:-0.017(n=4793), 202502:+0.003(n=3704), 202503:-0.005(n=4985), 202504:+0.005(n=3099), 202505:+0.019(n=842), 202506:+0.038(n=159)
* reb: 202410:-0.013(n=1407), 202411:-0.013(n=4624), 202412:-0.007(n=3970), 202501:-0.007(n=4793), 202502:-0.008(n=3704), 202503:-0.010(n=4985), 202504:-0.005(n=3099), 202505:+0.000(n=842), 202506:-0.010(n=159)
* ast: 202410:-0.006(n=1407), 202411:-0.022(n=4624), 202412:-0.023(n=3970), 202501:-0.018(n=4793), 202502:-0.015(n=3704), 202503:-0.015(n=4985), 202504:-0.014(n=3099), 202505:-0.018(n=842), 202506:+0.004(n=159)
* fg3m: 202410:-0.023(n=1407), 202411:-0.027(n=4624), 202412:-0.024(n=3970), 202501:-0.023(n=4793), 202502:-0.028(n=3704), 202503:-0.027(n=4985), 202504:-0.024(n=3099), 202505:-0.016(n=842), 202506:-0.016(n=159)
### 2023 threshold metrics (raw)

| stat | variant | n | thr log loss | Brier | ECE |
|---|---|---|---|---|---|
| pts | hybrid_lf | 27619 | 0.2796 | 0.0873 | 0.0073 |
| pts | v1_prod | 27619 | 0.2796 | 0.0876 | 0.0074 |
| pts | alt_lf | 27619 | 0.2825 | 0.0878 | 0.0093 |
| reb | hybrid_lf | 27619 | 0.3445 | 0.1093 | 0.0053 |
| reb | v1_prod | 27619 | 0.3441 | 0.1093 | 0.0059 |
| reb | alt_lf | 27619 | 0.3448 | 0.1090 | 0.0057 |
| ast | hybrid_lf | 27619 | 0.3068 | 0.0974 | 0.0064 |
| ast | v1_prod | 27619 | 0.3086 | 0.0977 | 0.0069 |
| ast | alt_lf | 27619 | 0.3095 | 0.0976 | 0.0080 |
| fg3m | hybrid_lf | 27619 | 0.3859 | 0.1256 | 0.0066 |
| fg3m | v1_prod | 27619 | 0.3901 | 0.1265 | 0.0098 |
| fg3m | alt_lf | 27619 | 0.3896 | 0.1264 | 0.0053 |

### 2023 paired CRPS / log-loss deltas

| stat | comparison | n | delta [95% CI] |
|---|---|---|---|
| pts | hybrid_lf-recency_normal | 27619 | -0.1022 [-0.1119, -0.0931] |
| pts | v1_prod-recency_normal | 27619 | -0.0884 [-0.1000, -0.0770] |
| pts | alt_lf-hybrid_lf | 27619 | +0.0783 [+0.0707, +0.0852] |
| pts | hybrid_lf-v1_prod_tll | 27619 | -0.0000 [-0.0008, +0.0008] |
| reb | hybrid_lf-recency_normal | 27619 | -0.0406 [-0.0446, -0.0361] |
| reb | v1_prod-recency_normal | 27619 | -0.0334 [-0.0379, -0.0290] |
| reb | alt_lf-hybrid_lf | 27619 | +0.0020 [-0.0002, +0.0040] |
| reb | hybrid_lf-v1_prod_tll | 27619 | +0.0004 [-0.0006, +0.0015] |
| ast | hybrid_lf-recency_normal | 27619 | -0.0458 [-0.0488, -0.0428] |
| ast | v1_prod-recency_normal | 27619 | -0.0275 [-0.0307, -0.0243] |
| ast | alt_lf-hybrid_lf | 27619 | +0.0121 [+0.0105, +0.0136] |
| ast | hybrid_lf-v1_prod_tll | 27619 | -0.0019 [-0.0027, -0.0009] |
| fg3m | hybrid_lf-recency_normal | 27619 | -0.0427 [-0.0446, -0.0410] |
| fg3m | v1_prod-recency_normal | 27619 | -0.0153 [-0.0175, -0.0132] |
| fg3m | alt_lf-hybrid_lf | 27619 | +0.0094 [+0.0084, +0.0105] |
| fg3m | hybrid_lf-v1_prod_tll | 27619 | -0.0042 [-0.0051, -0.0032] |

### 2023 per-block dCRPS (hybrid_lf - v1_prod)

* pts: 202310:+0.045(n=1062), 202311:-0.011(n=4522), 202312:-0.039(n=4346), 202401:-0.007(n=4932), 202402:-0.009(n=3691), 202403:-0.008(n=4825), 202404:-0.020(n=3281), 202405:-0.038(n=846), 202406:-0.017(n=114)
* reb: 202310:-0.011(n=1062), 202311:-0.010(n=4522), 202312:-0.007(n=4346), 202401:-0.004(n=4932), 202402:-0.008(n=3691), 202403:-0.008(n=4825), 202404:+0.000(n=3281), 202405:-0.028(n=846), 202406:+0.007(n=114)
* ast: 202310:-0.013(n=1062), 202311:-0.023(n=4522), 202312:-0.018(n=4346), 202401:-0.020(n=4932), 202402:-0.021(n=3691), 202403:-0.016(n=4825), 202404:-0.014(n=3281), 202405:-0.016(n=846), 202406:-0.007(n=114)
* fg3m: 202310:-0.025(n=1062), 202311:-0.027(n=4522), 202312:-0.031(n=4346), 202401:-0.029(n=4932), 202402:-0.025(n=3691), 202403:-0.026(n=4825), 202404:-0.028(n=3281), 202405:-0.015(n=846), 202406:-0.024(n=114)
