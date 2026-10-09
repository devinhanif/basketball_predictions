# ctxres_v2 experiment 2 -- DESCRIPTIVE report (not a decision)

generated 2026-10-08T19:00:28; run `20261008_171446`; tag `ctxres_v2_exp2_full_breadth`
bootstrap: game-clustered, n_boot=2000, seed=0; season-2024 played rows.
Only the recorded candidate `xgb_v12_poisson_nb` has a confirmatory verdict (`nba.eval.ctxres_v2_eval`); every p, q and CI below is exploratory.
Run's own 2023 best: `xgb_v12_poisson_nb`; recorded candidate in run: `xgb_v12_poisson_nb`; catboost_available=`True`; budget `full`.

Family sizes: F1=52, F2=56, F3=48 (BH q=0.05).

## 0. Confirmatory verdict (copied from the pre-registered eval, unaltered)

| stat | n | dCRPS [CI] | bias | PIT cov80 | BH q (m=4) | keep | failed |
|---|---|---|---|---|---|---|---|
| pts | 27583 | -0.0299 [-0.0388,-0.0209] | +0.172 | 0.716 | 0.0005 | False | ['no_slice_regression', 'cov80_in_0.75_0.85'] |
| reb | 27583 | -0.0505 [-0.0541,-0.0466] | +0.045 | 0.793 | 0.0005 | True | [] |
| ast | 27583 | -0.0303 [-0.0324,-0.0281] | +0.048 | 0.792 | 0.0005 | True | [] |
| fg3m | 27583 | -0.0321 [-0.0336,-0.0306] | +0.024 | 0.794 | 0.0005 | True | [] |

v2 kept overall: **False** (candidate `xgb_v12_poisson_nb`, coverage check `pit`).

## 1. Leaderboard, 2024, every arm vs v1_prod (DESCRIPTIVE; F1 = CRPS family)

delta = arm - v1_prod (negative is better). q_fam = BH over all F1 cells; q_stat = BH within the stat. `gate` = floor -0.005 & CI<0 & q_fam<=0.05 (checks 1-3 analogue). folds = months with negative delta / months. `seen1` = arm's 2024 numbers were viewed in experiment 1 (not independent).

### pts

| arm | seen1 | n | CRPS | dCRPS vs v1 [CI] | q_fam | q_stat | MDE80 | gate | folds | bias | PIT cov80 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| xgb_v12_quantile | Y | 27583 | 3.1142 | -0.0758 [-0.0825,-0.0686] | 0.001 | 0.001 | 0.0099 | survives | 9/9 | +0.019 | n/a |
| blend_top3 | Y | 27583 | 3.1161 | -0.0739 [-0.0803,-0.0672] | 0.001 | 0.001 | 0.0094 | survives | 9/9 | +0.074 | 0.769 |
| xgb_v12_prodcfg | Y | 27583 | 3.1205 | -0.0695 [-0.0756,-0.0634] | 0.001 | 0.001 | 0.0087 | survives | 9/9 | +0.032 | n/a |
| catboost_v12 | N | 27583 | 3.1210 | -0.0690 [-0.0753,-0.0620] | 0.001 | 0.001 | 0.0095 | survives | 9/9 | -0.015 | n/a |
| xgb_v12_tuned | Y | 27583 | 3.1238 | -0.0662 [-0.0726,-0.0597] | 0.001 | 0.001 | 0.0092 | survives | 9/9 | +0.005 | n/a |
| xgb_v12_rel | Y | 27583 | 3.1245 | -0.0655 [-0.0720,-0.0590] | 0.001 | 0.001 | 0.0093 | survives | 9/9 | +0.030 | n/a |
| xgb_v12_both | Y | 27583 | 3.1246 | -0.0654 [-0.0717,-0.0591] | 0.001 | 0.001 | 0.0090 | survives | 9/9 | +0.017 | n/a |
| xgb_v12_pruned | Y | 27583 | 3.1295 | -0.0605 [-0.0670,-0.0537] | 0.001 | 0.001 | 0.0095 | survives | 9/9 | +0.011 | n/a |
| mlp_quantile | N | 27583 | 3.1348 | -0.0552 [-0.0645,-0.0453] | 0.001 | 0.001 | 0.0137 | survives | 9/9 | +0.158 | n/a |
| xgb_v12_poisson_nb (CAND) | N | 27583 | 3.1601 | -0.0299 [-0.0388,-0.0209] | 0.001 | 0.001 | 0.0128 | survives | 8/9 | +0.172 | 0.716* |
| xgb_v1_prodcfg | Y | 27583 | 3.1881 | -0.0019 [-0.0040,+0.0003] | 0.110 | 0.099 | 0.0031 | no | 6/9 | +0.039 | n/a |
| v1_prod | Y | 27583 | 3.1900 | n/a | n/a | n/a | n/a | n/a | n/a | +0.045 | 0.771 |
| xgb_v1_tuned | Y | 27583 | 3.1935 | +0.0035 [+0.0001,+0.0070] | 0.051 | 0.049 | 0.0049 | no | 3/9 | -0.002 | n/a |
| glm_poisson_nb | N | 27583 | 3.2965 | +0.1065 [+0.0953,+0.1176] | 0.001 | 0.001 | 0.0160 | no | 0/9 | +0.011 | n/a |
| logit_thr | N | 27583 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### reb

| arm | seen1 | n | CRPS | dCRPS vs v1 [CI] | q_fam | q_stat | MDE80 | gate | folds | bias | PIT cov80 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| xgb_v12_poisson_nb (CAND) | N | 27583 | 1.2744 | -0.0505 [-0.0541,-0.0466] | 0.001 | 0.001 | 0.0054 | survives | 9/9 | +0.045 | 0.793* |
| blend_top3 | Y | 27583 | 1.2795 | -0.0454 [-0.0485,-0.0421] | 0.001 | 0.001 | 0.0046 | survives | 9/9 | +0.022 | 0.799 |
| xgb_v12_quantile | Y | 27583 | 1.2852 | -0.0397 [-0.0430,-0.0362] | 0.001 | 0.001 | 0.0049 | survives | 9/9 | +0.000 | n/a |
| xgb_v12_prodcfg | Y | 27583 | 1.2870 | -0.0379 [-0.0410,-0.0347] | 0.001 | 0.001 | 0.0045 | survives | 9/9 | +0.023 | n/a |
| catboost_v12 | N | 27583 | 1.2886 | -0.0362 [-0.0393,-0.0330] | 0.001 | 0.001 | 0.0045 | survives | 9/9 | +0.023 | n/a |
| xgb_v12_rel | Y | 27583 | 1.2896 | -0.0352 [-0.0383,-0.0321] | 0.001 | 0.001 | 0.0044 | survives | 9/9 | +0.034 | n/a |
| xgb_v12_tuned | Y | 27583 | 1.2899 | -0.0349 [-0.0381,-0.0318] | 0.001 | 0.001 | 0.0045 | survives | 9/9 | +0.022 | n/a |
| xgb_v12_both | Y | 27583 | 1.2905 | -0.0344 [-0.0373,-0.0313] | 0.001 | 0.001 | 0.0043 | survives | 9/9 | +0.028 | n/a |
| xgb_v12_pruned | Y | 27583 | 1.2928 | -0.0321 [-0.0354,-0.0284] | 0.001 | 0.001 | 0.0050 | survives | 9/9 | +0.010 | n/a |
| mlp_quantile | N | 27583 | 1.2985 | -0.0264 [-0.0307,-0.0218] | 0.001 | 0.001 | 0.0064 | survives | 9/9 | +0.076 | n/a |
| xgb_v1_prodcfg | Y | 27583 | 1.3244 | -0.0004 [-0.0014,+0.0005] | 0.414 | 0.390 | 0.0014 | no | 5/9 | +0.030 | n/a |
| v1_prod | Y | 27583 | 1.3249 | n/a | n/a | n/a | n/a | n/a | n/a | +0.031 | 0.792 |
| xgb_v1_tuned | Y | 27583 | 1.3256 | +0.0008 [-0.0007,+0.0021] | 0.339 | 0.339 | 0.0020 | no | 3/9 | +0.036 | n/a |
| glm_poisson_nb | N | 27583 | 1.3326 | +0.0077 [+0.0029,+0.0125] | 0.001 | 0.001 | 0.0069 | no | 2/9 | -0.014 | n/a |
| logit_thr | N | 27583 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### ast

| arm | seen1 | n | CRPS | dCRPS vs v1 [CI] | q_fam | q_stat | MDE80 | gate | folds | bias | PIT cov80 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| xgb_v12_poisson_nb (CAND) | N | 27583 | 0.8858 | -0.0303 [-0.0324,-0.0281] | 0.001 | 0.001 | 0.0031 | survives | 9/9 | +0.048 | 0.792* |
| blend_top3 | Y | 27583 | 0.8950 | -0.0211 [-0.0227,-0.0194] | 0.001 | 0.001 | 0.0023 | survives | 9/9 | +0.029 | 0.798 |
| xgb_v12_quantile | Y | 27583 | 0.9002 | -0.0160 [-0.0178,-0.0142] | 0.001 | 0.001 | 0.0026 | survives | 7/9 | +0.015 | n/a |
| xgb_v12_prodcfg | Y | 27583 | 0.9034 | -0.0128 [-0.0143,-0.0113] | 0.001 | 0.001 | 0.0022 | survives | 9/9 | +0.025 | n/a |
| xgb_v12_both | Y | 27583 | 0.9049 | -0.0113 [-0.0128,-0.0098] | 0.001 | 0.001 | 0.0021 | survives | 9/9 | +0.011 | n/a |
| catboost_v12 | N | 27583 | 0.9050 | -0.0112 [-0.0128,-0.0095] | 0.001 | 0.001 | 0.0023 | survives | 7/9 | +0.019 | n/a |
| xgb_v12_rel | Y | 27583 | 0.9052 | -0.0110 [-0.0125,-0.0094] | 0.001 | 0.001 | 0.0023 | survives | 9/9 | +0.008 | n/a |
| xgb_v12_tuned | Y | 27583 | 0.9058 | -0.0103 [-0.0118,-0.0087] | 0.001 | 0.001 | 0.0022 | survives | 8/9 | +0.009 | n/a |
| xgb_v12_pruned | Y | 27583 | 0.9068 | -0.0093 [-0.0109,-0.0076] | 0.001 | 0.001 | 0.0023 | survives | 8/9 | +0.011 | n/a |
| mlp_quantile | N | 27583 | 0.9099 | -0.0062 [-0.0089,-0.0035] | 0.001 | 0.001 | 0.0039 | survives | 7/9 | +0.108 | n/a |
| xgb_v1_prodcfg | Y | 27583 | 0.9154 | -0.0007 [-0.0015,-0.0000] | 0.051 | 0.048 | 0.0010 | no | 6/9 | +0.037 | n/a |
| v1_prod | Y | 27583 | 0.9162 | n/a | n/a | n/a | n/a | n/a | n/a | +0.037 | 0.787 |
| xgb_v1_tuned | Y | 27583 | 0.9163 | +0.0002 [-0.0008,+0.0012] | 0.758 | 0.743 | 0.0014 | no | 4/9 | +0.009 | n/a |
| glm_poisson_nb | N | 27583 | 0.9208 | +0.0046 [+0.0003,+0.0087] | 0.044 | 0.044 | 0.0059 | no | 1/9 | +0.132 | n/a |
| logit_thr | N | 27583 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### fg3m

| arm | seen1 | n | CRPS | dCRPS vs v1 [CI] | q_fam | q_stat | MDE80 | gate | folds | bias | PIT cov80 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| xgb_v12_poisson_nb (CAND) | N | 27583 | 0.5830 | -0.0321 [-0.0336,-0.0306] | 0.001 | 0.001 | 0.0021 | survives | 9/9 | +0.024 | 0.794* |
| blend_top3 | Y | 27583 | 0.5946 | -0.0205 [-0.0215,-0.0193] | 0.001 | 0.001 | 0.0016 | survives | 9/9 | +0.000 | 0.796 |
| xgb_v12_quantile | Y | 27583 | 0.5949 | -0.0202 [-0.0215,-0.0189] | 0.001 | 0.001 | 0.0018 | survives | 9/9 | -0.022 | n/a |
| glm_poisson_nb | N | 27583 | 0.6026 | -0.0125 [-0.0148,-0.0103] | 0.001 | 0.001 | 0.0032 | survives | 9/9 | -0.009 | n/a |
| mlp_quantile | N | 27583 | 0.6070 | -0.0081 [-0.0099,-0.0063] | 0.001 | 0.001 | 0.0026 | survives | 8/9 | +0.049 | n/a |
| xgb_v12_prodcfg | Y | 27583 | 0.6080 | -0.0071 [-0.0080,-0.0061] | 0.001 | 0.001 | 0.0014 | survives | 8/9 | -0.002 | n/a |
| xgb_v12_tuned | Y | 27583 | 0.6081 | -0.0070 [-0.0081,-0.0060] | 0.001 | 0.001 | 0.0015 | survives | 8/9 | +0.000 | n/a |
| catboost_v12 | N | 27583 | 0.6081 | -0.0070 [-0.0081,-0.0059] | 0.001 | 0.001 | 0.0016 | survives | 9/9 | +0.004 | n/a |
| xgb_v12_rel | Y | 27583 | 0.6082 | -0.0069 [-0.0079,-0.0059] | 0.001 | 0.001 | 0.0015 | survives | 8/9 | -0.003 | n/a |
| xgb_v12_pruned | Y | 27583 | 0.6089 | -0.0062 [-0.0072,-0.0051] | 0.001 | 0.001 | 0.0015 | survives | 8/9 | -0.001 | n/a |
| xgb_v12_both | Y | 27583 | 0.6090 | -0.0061 [-0.0071,-0.0051] | 0.001 | 0.001 | 0.0014 | survives | 8/9 | -0.004 | n/a |
| v1_prod | Y | 27583 | 0.6151 | n/a | n/a | n/a | n/a | n/a | n/a | -0.010 | 0.786 |
| xgb_v1_tuned | Y | 27583 | 0.6151 | +0.0000 [-0.0008,+0.0008] | 0.983 | 0.983 | 0.0011 | no | 3/9 | -0.013 | n/a |
| xgb_v1_prodcfg | Y | 27583 | 0.6153 | +0.0002 [-0.0003,+0.0007] | 0.510 | 0.531 | 0.0007 | no | 4/9 | -0.008 | n/a |
| logit_thr | N | 27583 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

`*` integer-grid arm: A1.1 coverage has about +/-0.035 estimator uncertainty.
PIT cov80 n/a = arm stored no q19 grid (light arm); naive coverage is in metrics.json.

## 2. Threshold log loss vs v1_prod (DESCRIPTIVE; F2)

### pts

| arm | thr logloss | d vs v1 [CI] | q_fam |
|---|---|---|---|
| blend_top3 | 0.2826 | -0.0031 [-0.0037,-0.0025] | 0.001 |
| catboost_v12 | 0.2835 | -0.0022 [-0.0028,-0.0017] | 0.001 |
| glm_poisson_nb | 0.2973 | +0.0115 [+0.0091,+0.0142] | 0.001 |
| logit_thr | 0.2854 | -0.0004 [-0.0014,+0.0007] | 0.512 |
| mlp_quantile | 0.2892 | +0.0034 [+0.0022,+0.0048] | 0.001 |
| xgb_v12_both | 0.2838 | -0.0019 [-0.0024,-0.0014] | 0.001 |
| xgb_v12_poisson_nb | 0.2870 | +0.0013 [+0.0002,+0.0024] | 0.026 |
| xgb_v12_prodcfg | 0.2835 | -0.0022 [-0.0027,-0.0017] | 0.001 |
| xgb_v12_pruned | 0.2844 | -0.0014 [-0.0019,-0.0008] | 0.001 |
| xgb_v12_quantile | 0.2851 | -0.0006 [-0.0015,+0.0003] | 0.216 |
| xgb_v12_rel | 0.2838 | -0.0019 [-0.0025,-0.0014] | 0.001 |
| xgb_v12_tuned | 0.2838 | -0.0020 [-0.0025,-0.0014] | 0.001 |
| xgb_v1_prodcfg | 0.2855 | -0.0003 [-0.0005,-0.0000] | 0.057 |
| xgb_v1_tuned | 0.2859 | +0.0001 [-0.0003,+0.0005] | 0.512 |

### reb

| arm | thr logloss | d vs v1 [CI] | q_fam |
|---|---|---|---|
| blend_top3 | 0.3455 | -0.0073 [-0.0081,-0.0066] | 0.001 |
| catboost_v12 | 0.3480 | -0.0048 [-0.0056,-0.0041] | 0.001 |
| glm_poisson_nb | 0.3578 | +0.0050 [+0.0032,+0.0067] | 0.001 |
| logit_thr | 0.3526 | -0.0002 [-0.0014,+0.0010] | 0.751 |
| mlp_quantile | 0.3541 | +0.0013 [-0.0002,+0.0027] | 0.100 |
| xgb_v12_both | 0.3483 | -0.0046 [-0.0053,-0.0039] | 0.001 |
| xgb_v12_poisson_nb | 0.3458 | -0.0071 [-0.0080,-0.0060] | 0.001 |
| xgb_v12_prodcfg | 0.3475 | -0.0053 [-0.0061,-0.0046] | 0.001 |
| xgb_v12_pruned | 0.3492 | -0.0036 [-0.0045,-0.0028] | 0.001 |
| xgb_v12_quantile | 0.3486 | -0.0043 [-0.0053,-0.0033] | 0.001 |
| xgb_v12_rel | 0.3482 | -0.0046 [-0.0054,-0.0039] | 0.001 |
| xgb_v12_tuned | 0.3482 | -0.0047 [-0.0055,-0.0040] | 0.001 |
| xgb_v1_prodcfg | 0.3527 | -0.0001 [-0.0004,+0.0002] | 0.409 |
| xgb_v1_tuned | 0.3531 | +0.0002 [-0.0003,+0.0006] | 0.448 |

### ast

| arm | thr logloss | d vs v1 [CI] | q_fam |
|---|---|---|---|
| blend_top3 | 0.3025 | -0.0057 [-0.0063,-0.0050] | 0.001 |
| catboost_v12 | 0.3052 | -0.0030 [-0.0036,-0.0023] | 0.001 |
| glm_poisson_nb | 0.3123 | +0.0040 [+0.0027,+0.0056] | 0.001 |
| logit_thr | 0.3082 | +0.0000 [-0.0010,+0.0011] | 0.968 |
| mlp_quantile | 0.3102 | +0.0020 [+0.0007,+0.0034] | 0.003 |
| xgb_v12_both | 0.3052 | -0.0031 [-0.0036,-0.0025] | 0.001 |
| xgb_v12_poisson_nb | 0.3030 | -0.0052 [-0.0061,-0.0043] | 0.001 |
| xgb_v12_prodcfg | 0.3044 | -0.0039 [-0.0044,-0.0032] | 0.001 |
| xgb_v12_pruned | 0.3058 | -0.0024 [-0.0030,-0.0018] | 0.001 |
| xgb_v12_quantile | 0.3056 | -0.0026 [-0.0034,-0.0017] | 0.001 |
| xgb_v12_rel | 0.3052 | -0.0030 [-0.0036,-0.0024] | 0.001 |
| xgb_v12_tuned | 0.3055 | -0.0027 [-0.0033,-0.0021] | 0.001 |
| xgb_v1_prodcfg | 0.3078 | -0.0004 [-0.0007,-0.0001] | 0.018 |
| xgb_v1_tuned | 0.3084 | +0.0002 [-0.0002,+0.0006] | 0.376 |

### fg3m

| arm | thr logloss | d vs v1 [CI] | q_fam |
|---|---|---|---|
| blend_top3 | 0.3950 | -0.0084 [-0.0093,-0.0076] | 0.001 |
| catboost_v12 | 0.3993 | -0.0040 [-0.0048,-0.0032] | 0.001 |
| glm_poisson_nb | 0.4110 | +0.0076 [+0.0059,+0.0092] | 0.001 |
| logit_thr | 0.4049 | +0.0016 [+0.0003,+0.0029] | 0.021 |
| mlp_quantile | 0.4041 | +0.0007 [-0.0008,+0.0023] | 0.392 |
| xgb_v12_both | 0.3995 | -0.0039 [-0.0046,-0.0031] | 0.001 |
| xgb_v12_poisson_nb | 0.3947 | -0.0087 [-0.0098,-0.0077] | 0.001 |
| xgb_v12_prodcfg | 0.3988 | -0.0046 [-0.0053,-0.0039] | 0.001 |
| xgb_v12_pruned | 0.3997 | -0.0037 [-0.0045,-0.0028] | 0.001 |
| xgb_v12_quantile | 0.3986 | -0.0048 [-0.0058,-0.0037] | 0.001 |
| xgb_v12_rel | 0.3991 | -0.0043 [-0.0050,-0.0035] | 0.001 |
| xgb_v12_tuned | 0.3991 | -0.0043 [-0.0051,-0.0036] | 0.001 |
| xgb_v1_prodcfg | 0.4034 | +0.0001 [-0.0003,+0.0005] | 0.751 |
| xgb_v1_tuned | 0.4040 | +0.0006 [+0.0001,+0.0013] | 0.039 |

## 3. Arms vs the candidate (DESCRIPTIVE; F3, non-inferiority screen)

delta = arm - `xgb_v12_poisson_nb` CRPS. `close` = CI upper bound <= +0.005 (cannot be ruled worse than the floor); `better` = CI upper < 0. These are router/stack screens, not decisions.

| arm | pts | reb | ast | fg3m |
|---|---|---|---|---|
| blend_top3 | -0.0440 [-0.0477,-0.0405] better | +0.0051 [+0.0037,+0.0064] worse? | +0.0092 [+0.0083,+0.0102] worse? | +0.0116 [+0.0109,+0.0122] worse? |
| catboost_v12 | -0.0391 [-0.0449,-0.0336] better | +0.0142 [+0.0121,+0.0162] worse? | +0.0192 [+0.0176,+0.0208] worse? | +0.0250 [+0.0241,+0.0261] worse? |
| glm_poisson_nb | +0.1364 [+0.1236,+0.1492] worse? | +0.0582 [+0.0525,+0.0637] worse? | +0.0350 [+0.0308,+0.0389] worse? | +0.0196 [+0.0173,+0.0218] worse? |
| logit_thr | n/a | n/a | n/a | n/a |
| mlp_quantile | -0.0253 [-0.0343,-0.0161] better | +0.0241 [+0.0206,+0.0278] worse? | +0.0241 [+0.0217,+0.0266] worse? | +0.0240 [+0.0226,+0.0253] worse? |
| xgb_v12_both | -0.0355 [-0.0415,-0.0299] better | +0.0161 [+0.0141,+0.0181] worse? | +0.0190 [+0.0174,+0.0206] worse? | +0.0260 [+0.0249,+0.0271] worse? |
| xgb_v12_prodcfg | -0.0397 [-0.0454,-0.0341] better | +0.0126 [+0.0106,+0.0145] worse? | +0.0175 [+0.0159,+0.0192] worse? | +0.0250 [+0.0239,+0.0261] worse? |
| xgb_v12_pruned | -0.0307 [-0.0366,-0.0250] better | +0.0184 [+0.0161,+0.0206] worse? | +0.0210 [+0.0193,+0.0227] worse? | +0.0259 [+0.0248,+0.0270] worse? |
| xgb_v12_quantile | -0.0460 [-0.0521,-0.0399] better | +0.0108 [+0.0084,+0.0131] worse? | +0.0143 [+0.0128,+0.0159] worse? | +0.0118 [+0.0106,+0.0130] worse? |
| xgb_v12_rel | -0.0356 [-0.0414,-0.0302] better | +0.0153 [+0.0131,+0.0172] worse? | +0.0193 [+0.0178,+0.0209] worse? | +0.0252 [+0.0242,+0.0261] worse? |
| xgb_v12_tuned | -0.0363 [-0.0420,-0.0307] better | +0.0155 [+0.0136,+0.0175] worse? | +0.0200 [+0.0184,+0.0216] worse? | +0.0250 [+0.0241,+0.0261] worse? |
| xgb_v1_prodcfg | +0.0280 [+0.0194,+0.0365] worse? | +0.0501 [+0.0463,+0.0537] worse? | +0.0296 [+0.0274,+0.0317] worse? | +0.0322 [+0.0308,+0.0337] worse? |
| xgb_v1_tuned | +0.0334 [+0.0247,+0.0420] worse? | +0.0512 [+0.0474,+0.0548] worse? | +0.0305 [+0.0283,+0.0326] worse? | +0.0321 [+0.0306,+0.0335] worse? |

### blend_top3 minus `xgb_v12_poisson_nb` (single-model vs blend)

| stat | dCRPS [CI] | p | q (F6, m=4) |
|---|---|---|---|
| pts | -0.0440 [-0.0477,-0.0405] | 0.0005 | 0.0005 |
| reb | +0.0051 [+0.0037,+0.0064] | 0.0005 | 0.0005 |
| ast | +0.0092 [+0.0083,+0.0102] | 0.0005 | 0.0005 |
| fg3m | +0.0116 [+0.0109,+0.0122] | 0.0005 | 0.0005 |

## 4. Drop-one feature-group ablation (DESCRIPTIVE; F5, all 40 cells)

value = CRPS(drop group) - CRPS(full xgb_v12_tuned), 2024; positive = group helps. CIs come from metrics.json (200 resamples); p is CI-implied normal approx. `useless` = CI includes 0 or value < floor.

| group | stat | drop - full [CI] | q (F5) | reading |
|---|---|---|---|---|
| a own status | pts | -0.0007 [-0.0028,+0.0014] | 0.703 | useless/unclear |
| a own status | reb | -0.0001 [-0.0011,+0.0007] | 0.916 | useless/unclear |
| a own status | ast | -0.0000 [-0.0005,+0.0005] | 0.947 | useless/unclear |
| a own status | fg3m | -0.0002 [-0.0006,+0.0001] | 0.342 | useless/unclear |
| b returning teammates | pts | +0.0001 [-0.0017,+0.0018] | 0.947 | useless/unclear |
| b returning teammates | reb | -0.0004 [-0.0013,+0.0003] | 0.634 | useless/unclear |
| b returning teammates | ast | -0.0007 [-0.0013,-0.0002] | 0.108 | useless/unclear |
| b returning teammates | fg3m | +0.0003 [-0.0002,+0.0007] | 0.34 | useless/unclear |
| c possession rates | pts | +0.0025 [+0.0006,+0.0049] | 0.102 | useless/unclear |
| c possession rates | reb | -0.0014 [-0.0023,-0.0005] | 0.0214 | useless/unclear |
| c possession rates | ast | -0.0009 [-0.0016,-0.0002] | 0.0539 | useless/unclear |
| c possession rates | fg3m | +0.0003 [-0.0002,+0.0007] | 0.34 | useless/unclear |
| d schedule | pts | +0.0012 [-0.0008,+0.0032] | 0.473 | useless/unclear |
| d schedule | reb | -0.0009 [-0.0019,+0.0000] | 0.19 | useless/unclear |
| d schedule | ast | -0.0002 [-0.0008,+0.0003] | 0.703 | useless/unclear |
| d schedule | fg3m | -0.0001 [-0.0005,+0.0002] | 0.635 | useless/unclear |
| e opponent vs position | pts | +0.0031 [+0.0009,+0.0052] | 0.0346 | useless/unclear |
| e opponent vs position | reb | -0.0003 [-0.0014,+0.0006] | 0.703 | useless/unclear |
| e opponent vs position | ast | -0.0003 [-0.0009,+0.0004] | 0.691 | useless/unclear |
| e opponent vs position | fg3m | +0.0001 [-0.0003,+0.0005] | 0.851 | useless/unclear |
| f game environment | pts | +0.0006 [-0.0013,+0.0025] | 0.703 | useless/unclear |
| f game environment | reb | -0.0000 [-0.0009,+0.0007] | 0.951 | useless/unclear |
| f game environment | ast | -0.0003 [-0.0009,+0.0003] | 0.473 | useless/unclear |
| f game environment | fg3m | -0.0003 [-0.0006,+0.0000] | 0.24 | useless/unclear |
| g draft | pts | +0.0023 [+0.0003,+0.0044] | 0.11 | useless/unclear |
| g draft | reb | +0.0003 [-0.0003,+0.0009] | 0.621 | useless/unclear |
| g draft | ast | -0.0001 [-0.0005,+0.0005] | 0.916 | useless/unclear |
| g draft | fg3m | +0.0003 [-0.0001,+0.0006] | 0.402 | useless/unclear |
| h TV | pts | +0.0010 [-0.0006,+0.0025] | 0.449 | useless/unclear |
| h TV | reb | -0.0002 [-0.0010,+0.0005] | 0.848 | useless/unclear |
| h TV | ast | -0.0006 [-0.0011,-0.0000] | 0.11 | useless/unclear |
| h TV | fg3m | -0.0000 [-0.0003,+0.0003] | 0.947 | useless/unclear |
| i opp-adjusted ridge | pts | +0.0664 [+0.0610,+0.0721] | 0 | helps |
| i opp-adjusted ridge | reb | +0.0345 [+0.0317,+0.0373] | 0 | helps |
| i opp-adjusted ridge | ast | +0.0099 [+0.0088,+0.0111] | 0 | helps |
| i opp-adjusted ridge | fg3m | +0.0064 [+0.0056,+0.0072] | 0 | helps |
| j overtime normalisation | pts | -0.0004 [-0.0022,+0.0014] | 0.851 | useless/unclear |
| j overtime normalisation | reb | -0.0009 [-0.0016,-0.0002] | 0.0539 | useless/unclear |
| j overtime normalisation | ast | -0.0001 [-0.0006,+0.0004] | 0.848 | useless/unclear |
| j overtime normalisation | fg3m | +0.0000 [-0.0003,+0.0003] | 0.947 | useless/unclear |

## 5. Calibrators

### Pre-registered calibrator rule on `xgb_v12_poisson_nb` (recomputed; decision lives in eval)

| calibrator | stat | d thr-LL [CI] | ECE old->new | rule keep |
|---|---|---|---|---|
| platt | pts | -0.0016 [-0.0021,-0.0010] | 0.0119 -> 0.0096 | True |
| platt | reb | +0.0000 [-0.0001,+0.0002] | 0.0036 -> 0.0037 | False |
| platt | ast | -0.0002 [-0.0004,-0.0000] | 0.0069 -> 0.0041 | True |
| platt | fg3m | -0.0001 [-0.0003,+0.0001] | 0.0079 -> 0.0042 | False |
| iso | pts | -0.0037 [-0.0045,-0.0028] | 0.0119 -> 0.0042 | True |
| iso | reb | +0.0012 [+0.0008,+0.0016] | 0.0036 -> 0.0032 | False |
| iso | ast | +0.0004 [-0.0000,+0.0009] | 0.0069 -> 0.0020 | False |
| iso | fg3m | +0.0012 [+0.0007,+0.0017] | 0.0079 -> 0.0040 | False |
| conf | pts | +0.0025 [+0.0017,+0.0033] | 0.0119 -> 0.0170 | False |
| conf | reb | +0.0048 [+0.0040,+0.0056] | 0.0036 -> 0.0082 | False |
| conf | ast | +0.0115 [+0.0103,+0.0126] | 0.0069 -> 0.0146 | False |
| conf | fg3m | +0.0316 [+0.0297,+0.0335] | 0.0079 -> 0.0461 | False |
| pit | pts | +0.0038 [+0.0033,+0.0044] | 0.0119 -> 0.0174 | False |
| pit | reb | +0.0012 [+0.0009,+0.0015] | 0.0036 -> 0.0063 | False |
| pit | ast | +0.0035 [+0.0029,+0.0040] | 0.0069 -> 0.0133 | False |
| pit | fg3m | +0.0088 [+0.0079,+0.0098] | 0.0079 -> 0.0406 | False |

### All calibrator variants (F4, 232 cells; approx p): 61 significantly improve thr-LL, 82 significantly worsen it.

| variant | stat | d thr-LL [CI] | q (F4) |
|---|---|---|---|
| blend_top3|conf | ast | +0.0022 [+0.0017,+0.0028] | 2.27e-14 |
| blend_top3|conf | fg3m | +0.0125 [+0.0114,+0.0136] | 0 |
| blend_top3|iso | reb | +0.0013 [+0.0009,+0.0017] | 5.28e-09 |
| blend_top3|iso | ast | +0.0006 [+0.0002,+0.0010] | 0.00665 |
| blend_top3|iso | fg3m | +0.0009 [+0.0004,+0.0013] | 0.000403 |
| blend_top3|pit | pts | +0.0020 [+0.0016,+0.0025] | 0 |
| blend_top3|pit | reb | +0.0008 [+0.0004,+0.0011] | 6.73e-05 |
| blend_top3|pit | ast | +0.0032 [+0.0027,+0.0038] | 0 |
| blend_top3|pit | fg3m | +0.0062 [+0.0054,+0.0070] | 0 |
| catboost_v12|conf | pts | -0.0004 [-0.0006,-0.0003] | 6.98e-09 |
| catboost_v12|conf | reb | -0.0008 [-0.0009,-0.0007] | 0 |
| catboost_v12|conf | ast | -0.0004 [-0.0006,-0.0001] | 0.00299 |
| catboost_v12|conf | fg3m | +0.0021 [+0.0016,+0.0025] | 0 |
| catboost_v12|iso | pts | +0.0007 [+0.0003,+0.0011] | 0.00192 |
| catboost_v12|iso | reb | +0.0007 [+0.0003,+0.0012] | 0.00392 |
| catboost_v12|iso | fg3m | -0.0016 [-0.0023,-0.0010] | 8.76e-07 |
| catboost_v12|pit | ast | +0.0020 [+0.0012,+0.0028] | 6.1e-07 |
| catboost_v12|pit | fg3m | +0.0036 [+0.0025,+0.0047] | 1.72e-10 |
| catboost_v12|platt | fg3m | -0.0016 [-0.0020,-0.0012] | 0 |
| glm_poisson_nb|conf | reb | +0.0047 [+0.0038,+0.0057] | 0 |
| glm_poisson_nb|conf | ast | +0.0110 [+0.0098,+0.0120] | 0 |
| glm_poisson_nb|conf | fg3m | +0.0264 [+0.0243,+0.0282] | 0 |
| glm_poisson_nb|iso | pts | -0.0076 [-0.0090,-0.0062] | 0 |
| glm_poisson_nb|iso | fg3m | -0.0035 [-0.0043,-0.0026] | 1.68e-14 |
| glm_poisson_nb|pit | pts | +0.0033 [+0.0026,+0.0040] | 0 |
| glm_poisson_nb|pit | reb | +0.0008 [+0.0004,+0.0012] | 0.000117 |
| glm_poisson_nb|pit | ast | +0.0024 [+0.0018,+0.0031] | 2.29e-12 |
| glm_poisson_nb|pit | fg3m | +0.0046 [+0.0039,+0.0055] | 0 |
| glm_poisson_nb|platt | pts | -0.0052 [-0.0062,-0.0042] | 0 |
| glm_poisson_nb|platt | reb | -0.0010 [-0.0015,-0.0005] | 0.000188 |
| glm_poisson_nb|platt | ast | -0.0007 [-0.0011,-0.0003] | 0.000671 |
| glm_poisson_nb|platt | fg3m | -0.0006 [-0.0009,-0.0003] | 3.99e-05 |
| logit_thr|iso | pts | +0.0008 [+0.0004,+0.0012] | 0.000109 |
| logit_thr|iso | fg3m | -0.0008 [-0.0015,-0.0003] | 0.0132 |
| logit_thr|platt | reb | +0.0001 [+0.0000,+0.0002] | 0.00449 |
| mlp_quantile|conf | pts | -0.0037 [-0.0045,-0.0030] | 0 |
| mlp_quantile|conf | reb | -0.0035 [-0.0043,-0.0027] | 0 |
| mlp_quantile|conf | ast | -0.0029 [-0.0038,-0.0021] | 1.62e-11 |
| mlp_quantile|iso | pts | -0.0027 [-0.0035,-0.0021] | 9.16e-15 |
| mlp_quantile|iso | reb | -0.0026 [-0.0034,-0.0018] | 5.43e-10 |
| mlp_quantile|iso | ast | -0.0036 [-0.0044,-0.0027] | 1.68e-14 |
| mlp_quantile|iso | fg3m | -0.0032 [-0.0041,-0.0024] | 3.48e-13 |
| mlp_quantile|pit | pts | +0.0064 [+0.0053,+0.0076] | 0 |
| mlp_quantile|pit | reb | +0.0061 [+0.0048,+0.0073] | 0 |
| mlp_quantile|pit | ast | +0.0051 [+0.0038,+0.0064] | 1.91e-13 |
| mlp_quantile|pit | fg3m | +0.0083 [+0.0068,+0.0096] | 0 |
| mlp_quantile|platt | pts | -0.0011 [-0.0016,-0.0006] | 2.01e-05 |
| mlp_quantile|platt | reb | -0.0009 [-0.0014,-0.0004] | 0.00111 |
| mlp_quantile|platt | ast | -0.0015 [-0.0021,-0.0009] | 1.51e-06 |
| mlp_quantile|platt | fg3m | -0.0010 [-0.0015,-0.0004] | 0.00109 |
| v1_prod|conf | ast | +0.0005 [+0.0003,+0.0007] | 1.29e-05 |
| v1_prod|conf | fg3m | +0.0033 [+0.0029,+0.0037] | 0 |
| v1_prod|iso | reb | +0.0006 [+0.0002,+0.0010] | 0.0138 |
| v1_prod|iso | fg3m | -0.0012 [-0.0018,-0.0006] | 0.000197 |
| v1_prod|pit | ast | +0.0021 [+0.0013,+0.0028] | 3.73e-07 |
| v1_prod|pit | fg3m | +0.0031 [+0.0022,+0.0041] | 2.83e-09 |
| v1_prod|platt | fg3m | -0.0009 [-0.0012,-0.0006] | 4.41e-07 |
| xgb_v12_both|conf | pts | -0.0003 [-0.0004,-0.0002] | 4.67e-05 |
| xgb_v12_both|conf | reb | -0.0007 [-0.0008,-0.0006] | 0 |
| xgb_v12_both|conf | ast | -0.0003 [-0.0005,-0.0000] | 0.0302 |
| xgb_v12_both|conf | fg3m | +0.0021 [+0.0017,+0.0025] | 0 |
| xgb_v12_both|iso | reb | +0.0009 [+0.0004,+0.0014] | 0.00067 |
| xgb_v12_both|iso | fg3m | -0.0019 [-0.0025,-0.0013] | 1.32e-08 |
| xgb_v12_both|pit | ast | +0.0022 [+0.0015,+0.0029] | 2.88e-09 |
| xgb_v12_both|pit | fg3m | +0.0033 [+0.0023,+0.0043] | 1.72e-10 |
| xgb_v12_both|platt | fg3m | -0.0015 [-0.0019,-0.0011] | 1.23e-14 |
| xgb_v12_poisson_nb|conf | pts | +0.0025 [+0.0016,+0.0033] | 2.42e-08 |
| xgb_v12_poisson_nb|conf | reb | +0.0048 [+0.0039,+0.0055] | 0 |
| xgb_v12_poisson_nb|conf | ast | +0.0115 [+0.0104,+0.0126] | 0 |
| xgb_v12_poisson_nb|conf | fg3m | +0.0316 [+0.0298,+0.0335] | 0 |
| xgb_v12_poisson_nb|iso | pts | -0.0037 [-0.0046,-0.0028] | 1.42e-14 |
| xgb_v12_poisson_nb|iso | reb | +0.0012 [+0.0008,+0.0016] | 8.6e-08 |
| xgb_v12_poisson_nb|iso | fg3m | +0.0012 [+0.0007,+0.0016] | 6.28e-06 |
| xgb_v12_poisson_nb|pit | pts | +0.0038 [+0.0034,+0.0043] | 0 |
| xgb_v12_poisson_nb|pit | reb | +0.0012 [+0.0009,+0.0015] | 7.37e-13 |
| xgb_v12_poisson_nb|pit | ast | +0.0035 [+0.0029,+0.0039] | 0 |
| xgb_v12_poisson_nb|pit | fg3m | +0.0088 [+0.0080,+0.0097] | 0 |
| xgb_v12_poisson_nb|platt | pts | -0.0016 [-0.0021,-0.0010] | 6.22e-08 |
| xgb_v12_poisson_nb|platt | ast | -0.0002 [-0.0004,-0.0000] | 0.0391 |
| xgb_v12_prodcfg|conf | pts | -0.0004 [-0.0005,-0.0002] | 1.79e-08 |
| xgb_v12_prodcfg|conf | reb | -0.0009 [-0.0011,-0.0008] | 0 |
| xgb_v12_prodcfg|conf | ast | -0.0004 [-0.0006,-0.0002] | 0.000203 |
| xgb_v12_prodcfg|conf | fg3m | +0.0021 [+0.0017,+0.0025] | 0 |
| xgb_v12_prodcfg|iso | reb | +0.0008 [+0.0004,+0.0013] | 0.00027 |
| xgb_v12_prodcfg|iso | fg3m | -0.0017 [-0.0023,-0.0010] | 1.59e-06 |
| xgb_v12_prodcfg|pit | pts | +0.0005 [+0.0001,+0.0010] | 0.0461 |
| xgb_v12_prodcfg|pit | ast | +0.0023 [+0.0016,+0.0030] | 2.21e-09 |
| xgb_v12_prodcfg|pit | fg3m | +0.0031 [+0.0020,+0.0041] | 1.79e-08 |
| xgb_v12_prodcfg|platt | fg3m | -0.0015 [-0.0019,-0.0012] | 4.06e-14 |
| xgb_v12_pruned|conf | pts | -0.0002 [-0.0004,-0.0001] | 0.00165 |
| xgb_v12_pruned|conf | reb | -0.0007 [-0.0009,-0.0006] | 0 |
| xgb_v12_pruned|conf | fg3m | +0.0016 [+0.0012,+0.0020] | 2.73e-14 |
| xgb_v12_pruned|iso | pts | +0.0005 [+0.0001,+0.0009] | 0.0241 |
| xgb_v12_pruned|iso | reb | +0.0007 [+0.0002,+0.0011] | 0.00659 |
| xgb_v12_pruned|iso | fg3m | -0.0018 [-0.0025,-0.0012] | 5.36e-08 |
| xgb_v12_pruned|pit | ast | +0.0022 [+0.0014,+0.0029] | 9.17e-09 |
| xgb_v12_pruned|pit | fg3m | +0.0030 [+0.0019,+0.0040] | 1.61e-07 |
| xgb_v12_pruned|platt | fg3m | -0.0016 [-0.0020,-0.0013] | 0 |
| xgb_v12_quantile|conf | pts | -0.0013 [-0.0020,-0.0006] | 0.000496 |
| xgb_v12_quantile|conf | reb | -0.0016 [-0.0023,-0.0010] | 5.56e-06 |
| xgb_v12_quantile|conf | fg3m | +0.0160 [+0.0145,+0.0174] | 0 |
| xgb_v12_quantile|iso | pts | -0.0006 [-0.0010,-0.0001] | 0.0149 |
| xgb_v12_quantile|iso | reb | -0.0007 [-0.0012,-0.0001] | 0.0204 |
| xgb_v12_quantile|pit | pts | +0.0071 [+0.0061,+0.0080] | 0 |
| xgb_v12_quantile|pit | reb | +0.0051 [+0.0042,+0.0059] | 0 |
| xgb_v12_quantile|pit | ast | +0.0084 [+0.0074,+0.0094] | 0 |
| xgb_v12_quantile|pit | fg3m | +0.0083 [+0.0074,+0.0092] | 0 |
| xgb_v12_quantile|platt | pts | -0.0004 [-0.0006,-0.0002] | 0.000134 |
| xgb_v12_quantile|platt | reb | -0.0002 [-0.0004,-0.0000] | 0.0313 |
| xgb_v12_rel|conf | pts | -0.0003 [-0.0004,-0.0001] | 0.000188 |
| xgb_v12_rel|conf | reb | -0.0007 [-0.0008,-0.0006] | 0 |
| xgb_v12_rel|conf | fg3m | +0.0018 [+0.0014,+0.0022] | 0 |
| xgb_v12_rel|iso | pts | +0.0004 [+0.0000,+0.0008] | 0.0338 |
| xgb_v12_rel|iso | reb | +0.0007 [+0.0002,+0.0012] | 0.00613 |
| xgb_v12_rel|iso | fg3m | -0.0014 [-0.0019,-0.0007] | 1.99e-05 |
| xgb_v12_rel|pit | pts | +0.0005 [+0.0001,+0.0010] | 0.0407 |
| xgb_v12_rel|pit | ast | +0.0023 [+0.0016,+0.0030] | 1.9e-09 |
| xgb_v12_rel|pit | fg3m | +0.0036 [+0.0026,+0.0046] | 6.6e-12 |
| xgb_v12_rel|platt | fg3m | -0.0014 [-0.0017,-0.0010] | 2.62e-13 |
| xgb_v12_tuned|conf | pts | -0.0003 [-0.0004,-0.0002] | 2.42e-05 |
| xgb_v12_tuned|conf | reb | -0.0007 [-0.0009,-0.0006] | 0 |
| xgb_v12_tuned|conf | ast | -0.0003 [-0.0005,-0.0000] | 0.0313 |
| xgb_v12_tuned|conf | fg3m | +0.0022 [+0.0017,+0.0026] | 0 |
| xgb_v12_tuned|iso | pts | +0.0006 [+0.0002,+0.0010] | 0.00372 |
| xgb_v12_tuned|iso | reb | +0.0010 [+0.0005,+0.0015] | 0.000239 |
| xgb_v12_tuned|iso | fg3m | -0.0017 [-0.0024,-0.0011] | 3.31e-07 |
| xgb_v12_tuned|pit | ast | +0.0021 [+0.0013,+0.0027] | 8.88e-08 |
| xgb_v12_tuned|pit | fg3m | +0.0033 [+0.0023,+0.0042] | 2.72e-10 |
| xgb_v12_tuned|platt | fg3m | -0.0016 [-0.0020,-0.0013] | 0 |
| xgb_v1_prodcfg|conf | ast | +0.0004 [+0.0002,+0.0006] | 0.000329 |
| xgb_v1_prodcfg|conf | fg3m | +0.0032 [+0.0028,+0.0037] | 0 |
| xgb_v1_prodcfg|iso | reb | +0.0008 [+0.0003,+0.0012] | 0.00377 |
| xgb_v1_prodcfg|iso | fg3m | -0.0016 [-0.0021,-0.0009] | 8.36e-07 |
| xgb_v1_prodcfg|pit | ast | +0.0020 [+0.0012,+0.0027] | 2.43e-07 |
| xgb_v1_prodcfg|pit | fg3m | +0.0030 [+0.0020,+0.0040] | 1.02e-08 |
| xgb_v1_prodcfg|platt | fg3m | -0.0010 [-0.0013,-0.0006] | 4.93e-08 |
| xgb_v1_tuned|conf | ast | +0.0005 [+0.0003,+0.0007] | 4.02e-06 |
| xgb_v1_tuned|conf | fg3m | +0.0028 [+0.0024,+0.0032] | 0 |
| xgb_v1_tuned|iso | reb | +0.0010 [+0.0005,+0.0015] | 9.18e-05 |
| xgb_v1_tuned|iso | fg3m | -0.0019 [-0.0025,-0.0012] | 1.23e-07 |
| xgb_v1_tuned|pit | ast | +0.0019 [+0.0012,+0.0026] | 1.48e-06 |
| xgb_v1_tuned|pit | fg3m | +0.0024 [+0.0014,+0.0035] | 1.76e-05 |
| xgb_v1_tuned|platt | fg3m | -0.0014 [-0.0017,-0.0010] | 1.91e-13 |

## 6. fg3m zero-inflation check (2024)

| arm | observed P(0) | predicted P(0) | gap |
|---|---|---|---|
| xgb_v12_tuned | 0.423 | 0.383 | -0.039 |
| xgb_v12_poisson_nb | 0.423 | 0.406 | -0.017 |
| glm_poisson_nb | 0.423 | 0.413 | -0.010 |

## 7. Drift table (season means; shift = in prior-season std units)

| season | rows | pace_proxy_game_total | three_pa_share_proxy | usage_proxy | pts_mean | reb_mean | ast_mean | fg3m_mean | pts_shift_in_std_vs_prev | reb_shift_in_std_vs_prev | ast_shift_in_std_vs_prev | fg3m_shift_in_std_vs_prev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022 | 25110.000 | 228.240 | 0.394 | 0.215 | 11.152 | 4.222 | 2.456 | 1.200 | n/a | n/a | n/a | n/a |
| 2023 | 27619.000 | 228.997 | 0.401 | 0.215 | 10.773 | 4.106 | 2.507 | 1.210 | -0.041 | -0.033 | 0.019 | 0.007 |
| 2024 | 27583.000 | 226.209 | 0.414 | 0.215 | 10.754 | 4.160 | 2.490 | 1.276 | -0.002 | 0.016 | -0.006 | 0.044 |

## 8. Slices (DESCRIPTIVE; arm - v1_prod CRPS; flag > +0.01 with n >= 300)

| arm | stat | slice | n | dCRPS | flag |
|---|---|---|---|---|---|
| xgb_v12_poisson_nb | pts | sl_tm_out=report_none_out | 4337 | -0.0252 |  |
| xgb_v12_poisson_nb | pts | sl_tm_out=teammate_out | 23228 | -0.0307 |  |
| xgb_v12_poisson_nb | pts | sl_min_change=minutes_down | 3733 | -0.0800 |  |
| xgb_v12_poisson_nb | pts | sl_min_change=minutes_flat | 19504 | -0.0226 |  |
| xgb_v12_poisson_nb | pts | sl_min_change=minutes_up | 4346 | -0.0191 |  |
| xgb_v12_poisson_nb | pts | sl_phase=first15 | 4621 | -0.0318 |  |
| xgb_v12_poisson_nb | pts | sl_phase=rest | 22962 | -0.0295 |  |
| xgb_v12_poisson_nb | pts | sl_role=bench | 15526 | -0.0645 |  |
| xgb_v12_poisson_nb | pts | sl_role=starter | 12057 | +0.0147 | REGRESSION |
| xgb_v12_poisson_nb | reb | sl_tm_out=report_none_out | 4337 | -0.0469 |  |
| xgb_v12_poisson_nb | reb | sl_tm_out=teammate_out | 23228 | -0.0511 |  |
| xgb_v12_poisson_nb | reb | sl_min_change=minutes_down | 3733 | -0.0889 |  |
| xgb_v12_poisson_nb | reb | sl_min_change=minutes_flat | 19504 | -0.0453 |  |
| xgb_v12_poisson_nb | reb | sl_min_change=minutes_up | 4346 | -0.0408 |  |
| xgb_v12_poisson_nb | reb | sl_phase=first15 | 4621 | -0.0498 |  |
| xgb_v12_poisson_nb | reb | sl_phase=rest | 22962 | -0.0506 |  |
| xgb_v12_poisson_nb | reb | sl_role=bench | 15526 | -0.0785 |  |
| xgb_v12_poisson_nb | reb | sl_role=starter | 12057 | -0.0144 |  |
| xgb_v12_poisson_nb | ast | sl_tm_out=report_none_out | 4337 | -0.0279 |  |
| xgb_v12_poisson_nb | ast | sl_tm_out=teammate_out | 23228 | -0.0308 |  |
| xgb_v12_poisson_nb | ast | sl_min_change=minutes_down | 3733 | -0.0465 |  |
| xgb_v12_poisson_nb | ast | sl_min_change=minutes_flat | 19504 | -0.0276 |  |
| xgb_v12_poisson_nb | ast | sl_min_change=minutes_up | 4346 | -0.0285 |  |
| xgb_v12_poisson_nb | ast | sl_phase=first15 | 4621 | -0.0260 |  |
| xgb_v12_poisson_nb | ast | sl_phase=rest | 22962 | -0.0312 |  |
| xgb_v12_poisson_nb | ast | sl_role=bench | 15526 | -0.0405 |  |
| xgb_v12_poisson_nb | ast | sl_role=starter | 12057 | -0.0172 |  |
| xgb_v12_poisson_nb | fg3m | sl_tm_out=report_none_out | 4337 | -0.0306 |  |
| xgb_v12_poisson_nb | fg3m | sl_tm_out=teammate_out | 23228 | -0.0323 |  |
| xgb_v12_poisson_nb | fg3m | sl_min_change=minutes_down | 3733 | -0.0353 |  |
| xgb_v12_poisson_nb | fg3m | sl_min_change=minutes_flat | 19504 | -0.0319 |  |
| xgb_v12_poisson_nb | fg3m | sl_min_change=minutes_up | 4346 | -0.0299 |  |
| xgb_v12_poisson_nb | fg3m | sl_phase=first15 | 4621 | -0.0313 |  |
| xgb_v12_poisson_nb | fg3m | sl_phase=rest | 22962 | -0.0322 |  |
| xgb_v12_poisson_nb | fg3m | sl_role=bench | 15526 | -0.0369 |  |
| xgb_v12_poisson_nb | fg3m | sl_role=starter | 12057 | -0.0258 |  |

## 9. Reconciliation (parquet recomputation vs metrics.json leaderboard)

all CRPS means agree within 1e-4

## 10. 2023 selection scores (ratio to recency-Normal; selection season)

| arm | ratio |
|---|---|
| xgb_v12_poisson_nb | 0.9331 |
| xgb_v12_quantile | 0.9384 |
| xgb_v12_prodcfg | 0.9472 |
| catboost_v12 | 0.9482 |
| xgb_v12_tuned | 0.9490 |
| xgb_v12_both | 0.9493 |
| xgb_v12_rel | 0.9494 |
| xgb_v12_pruned | 0.9498 |
| mlp_quantile | 0.9519 |
| xgb_v1_prodcfg | 0.9611 |
| v1_prod | 0.9622 |
| xgb_v1_tuned | 0.9626 |
| glm_poisson_nb | 0.9676 |

Bias limit used in confirmatory rule: +/-0.5; coverage window 0.75-0.85.