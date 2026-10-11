# Joint calibration: independence vs Gaussian vs t copula (2023-24 OOF)

n parlays=15798, n games=2633, config={'n_sims': 10000, 'shrink_k': 200.0, 't_df': 6.0, 'seed': 20261008, 'n_boot': 2000}

## Level (all parlays)
| engine | log loss | Brier | mean pred |
|---|---|---|---|
| independence | 0.37225 | 0.11463 | 0.1480 |
| gaussian | 0.37173 | 0.11451 | 0.1490 |
| t | 0.37168 | 0.11451 | 0.1490 |

## Paired comparisons (A-B, negative = A better; game-clustered 95% CI)
| slice | n parlays | n games | metric | A | B | mean diff | 95% CI | A better (CI<0)? |
|---|---|---|---|---|---|---|---|---|
| all (cross-fit 2023+2024) | 15798 | 2633 | log_loss | gaussian | independence | -0.00053 | [-0.00106, -0.00003] | yes |
| all (cross-fit 2023+2024) | 15798 | 2633 | brier | gaussian | independence | -0.00012 | [-0.00025, -0.00000] | yes |
| all (cross-fit 2023+2024) | 15798 | 2633 | log_loss | t | independence | -0.00058 | [-0.00114, -0.00004] | yes |
| all (cross-fit 2023+2024) | 15798 | 2633 | brier | t | independence | -0.00012 | [-0.00025, +0.00000] | no |
| all (cross-fit 2023+2024) | 15798 | 2633 | log_loss | t | gaussian | -0.00005 | [-0.00021, +0.00011] | no |
| all (cross-fit 2023+2024) | 15798 | 2633 | brier | t | gaussian | -0.00000 | [-0.00003, +0.00003] | no |
| 2024 only (walk-forward) | 7890 | 1315 | log_loss | gaussian | independence | -0.00099 | [-0.00177, -0.00030] | yes |
| 2024 only (walk-forward) | 7890 | 1315 | brier | gaussian | independence | -0.00021 | [-0.00038, -0.00005] | yes |
| 2024 only (walk-forward) | 7890 | 1315 | log_loss | t | independence | -0.00093 | [-0.00173, -0.00021] | yes |
| 2024 only (walk-forward) | 7890 | 1315 | brier | t | independence | -0.00021 | [-0.00038, -0.00004] | yes |
| 2024 only (walk-forward) | 7890 | 1315 | log_loss | t | gaussian | +0.00006 | [-0.00015, +0.00027] | no |
| 2024 only (walk-forward) | 7890 | 1315 | brier | t | gaussian | +0.00001 | [-0.00003, +0.00005] | no |
| 2023 only (cross-fit) | 7908 | 1318 | log_loss | gaussian | independence | -0.00006 | [-0.00082, +0.00066] | no |
| 2023 only (cross-fit) | 7908 | 1318 | brier | gaussian | independence | -0.00003 | [-0.00020, +0.00015] | no |
| 2023 only (cross-fit) | 7908 | 1318 | log_loss | t | independence | -0.00022 | [-0.00098, +0.00053] | no |
| 2023 only (cross-fit) | 7908 | 1318 | brier | t | independence | -0.00004 | [-0.00021, +0.00014] | no |
| 2023 only (cross-fit) | 7908 | 1318 | log_loss | t | gaussian | -0.00016 | [-0.00039, +0.00007] | no |
| 2023 only (cross-fit) | 7908 | 1318 | brier | t | gaussian | -0.00001 | [-0.00004, +0.00004] | no |
| 2 legs | 7071 | 2568 | log_loss | gaussian | independence | -0.00082 | [-0.00161, -0.00005] | yes |
| 2 legs | 7071 | 2568 | brier | gaussian | independence | -0.00024 | [-0.00048, -0.00002] | yes |
| 2 legs | 7071 | 2568 | log_loss | t | independence | -0.00076 | [-0.00155, +0.00000] | no |
| 2 legs | 7071 | 2568 | brier | t | independence | -0.00022 | [-0.00045, -0.00000] | yes |
| 2 legs | 7071 | 2568 | log_loss | t | gaussian | +0.00006 | [-0.00014, +0.00024] | no |
| 2 legs | 7071 | 2568 | brier | t | gaussian | +0.00002 | [-0.00003, +0.00007] | no |
| 3 legs | 5558 | 2415 | log_loss | gaussian | independence | -0.00014 | [-0.00096, +0.00067] | no |
| 3 legs | 5558 | 2415 | brier | gaussian | independence | -0.00002 | [-0.00018, +0.00015] | no |
| 3 legs | 5558 | 2415 | log_loss | t | independence | -0.00032 | [-0.00119, +0.00056] | no |
| 3 legs | 5558 | 2415 | brier | t | independence | -0.00003 | [-0.00020, +0.00014] | no |
| 3 legs | 5558 | 2415 | log_loss | t | gaussian | -0.00017 | [-0.00048, +0.00009] | no |
| 3 legs | 5558 | 2415 | brier | t | gaussian | -0.00002 | [-0.00006, +0.00002] | no |
| 4 legs | 3169 | 1942 | log_loss | gaussian | independence | -0.00055 | [-0.00177, +0.00056] | no |
| 4 legs | 3169 | 1942 | brier | gaussian | independence | -0.00004 | [-0.00019, +0.00009] | no |
| 4 legs | 3169 | 1942 | log_loss | t | independence | -0.00062 | [-0.00193, +0.00055] | no |
| 4 legs | 3169 | 1942 | brier | t | independence | -0.00006 | [-0.00020, +0.00007] | no |
| 4 legs | 3169 | 1942 | log_loss | t | gaussian | -0.00007 | [-0.00045, +0.00029] | no |
| 4 legs | 3169 | 1942 | brier | t | gaussian | -0.00002 | [-0.00006, +0.00003] | no |
| game-only legs | 615 | 560 | log_loss | gaussian | independence | +0.00005 | [-0.00084, +0.00096] | no |
| game-only legs | 615 | 560 | brier | gaussian | independence | -0.00000 | [-0.00032, +0.00032] | no |
| game-only legs | 615 | 560 | log_loss | t | independence | +0.00013 | [-0.00082, +0.00106] | no |
| game-only legs | 615 | 560 | brier | t | independence | +0.00002 | [-0.00034, +0.00036] | no |
| game-only legs | 615 | 560 | log_loss | t | gaussian | +0.00008 | [-0.00039, +0.00054] | no |
| game-only legs | 615 | 560 | brier | t | gaussian | +0.00002 | [-0.00012, +0.00016] | no |
| has player leg | 15183 | 2633 | log_loss | gaussian | independence | -0.00055 | [-0.00109, -0.00004] | yes |
| has player leg | 15183 | 2633 | brier | gaussian | independence | -0.00013 | [-0.00026, -0.00000] | yes |
| has player leg | 15183 | 2633 | log_loss | t | independence | -0.00061 | [-0.00119, -0.00005] | yes |
| has player leg | 15183 | 2633 | brier | t | independence | -0.00013 | [-0.00026, +0.00000] | no |
| has player leg | 15183 | 2633 | log_loss | t | gaussian | -0.00006 | [-0.00022, +0.00011] | no |
| has player leg | 15183 | 2633 | brier | t | gaussian | -0.00000 | [-0.00003, +0.00003] | no |

## Reliability of joint probability
| engine | bin | n | mean pred | observed |
|---|---|---|---|---|
| independence | [0.00,0.05) | 3693 | 0.0284 | 0.0322 |
| independence | [0.05,0.10) | 3499 | 0.0737 | 0.0746 |
| independence | [0.10,0.20) | 4350 | 0.1441 | 0.1476 |
| independence | [0.20,0.40) | 3482 | 0.2794 | 0.2964 |
| independence | [0.40,1.00) | 774 | 0.4854 | 0.5013 |
| gaussian | [0.00,0.05) | 3574 | 0.0282 | 0.0311 |
| gaussian | [0.05,0.10) | 3552 | 0.0735 | 0.0718 |
| gaussian | [0.10,0.20) | 4400 | 0.1443 | 0.1482 |
| gaussian | [0.20,0.40) | 3492 | 0.2802 | 0.2964 |
| gaussian | [0.40,1.00) | 780 | 0.4858 | 0.4987 |
| t | [0.00,0.05) | 3534 | 0.0290 | 0.0311 |
| t | [0.05,0.10) | 3629 | 0.0734 | 0.0714 |
| t | [0.10,0.20) | 4380 | 0.1440 | 0.1491 |
| t | [0.20,0.40) | 3467 | 0.2800 | 0.2974 |
| t | [0.40,1.00) | 788 | 0.4875 | 0.4937 |

## Leg marginals (sanity; conditional on playing)
| type | n legs | mean pred | observed | log loss |
|---|---|---|---|---|
| total | 6233 | 0.4964 | 0.4890 | 0.6760 |
| player | 29421 | 0.4680 | 0.4786 | 0.6123 |
| spread | 3615 | 0.4566 | 0.4633 | 0.6087 |
| win | 4223 | 0.4919 | 0.4987 | 0.6101 |
