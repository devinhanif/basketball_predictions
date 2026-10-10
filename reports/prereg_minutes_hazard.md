# MINUTES_HAZARD: STOPPED AT THE GATE (no model result)

Frozen sha256 `65fced234352f2cbf1f154add6f5c25e7014401bf9295cd852af3973c15f4283`; git ca1379b52c936d8403608993d43fec99c6f29320.

| test | ok |
|---|---|
| 1_stints_coverage | True |
| 2_pf_nonnull | True |
| 3_possessions_nonnull | True |
| 4_starter_games | True |
| 5a_missingness_gap | False |
| 5b_planted | True |
| 5c_m2_reproduces | True |

Missingness audit: max gap in null rate across realised-minutes buckets (pp; limit 2):

| input | max gap pp |
|---|---|
| vac_min | 0.07 |
| min10 | 0.00 |
| starter10 | 0.00 |
| pf | 0.00 |
| habit_history(k=0) | 1.31 |
| foul_rate_history | 0.00 |
| stint_labels(sumY=0) | 5.79 |
| foul_timing_pbp(fallback) | 1.66 |
| pretip_margin_mu | 0.00 |
| actual_margin_path | 0.00 |

Null rate by bucket (inputs with any null):

| input | bucket | n | null rate |
|---|---|---|---|
| vac_min | [0,10) | 12141 | 0.0013 |
| vac_min | [10,20) | 18524 | 0.0009 |
| vac_min | [20,30) | 24308 | 0.0007 |
| vac_min | [30,inf) | 25339 | 0.0013 |
| habit_history(k=0) | [0,10) | 12141 | 0.0152 |
| habit_history(k=0) | [10,20) | 18524 | 0.0071 |
| habit_history(k=0) | [20,30) | 24308 | 0.0045 |
| habit_history(k=0) | [30,inf) | 25339 | 0.0020 |
| stint_labels(sumY=0) | [0,10) | 12141 | 0.0579 |
| stint_labels(sumY=0) | [10,20) | 18524 | 0.0001 |
| foul_timing_pbp(fallback) | [0,10) | 12141 | 0.0055 |
| foul_timing_pbp(fallback) | [10,20) | 18524 | 0.0161 |
| foul_timing_pbp(fallback) | [20,30) | 24308 | 0.0189 |
| foul_timing_pbp(fallback) | [30,inf) | 25339 | 0.0221 |
