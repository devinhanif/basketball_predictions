# seq_props evaluation (season 2024 played rows, vs context_residual)

Coverage: {"pts": {"seq_rows_2024": 28118, "common_rows": 27583}, "reb": {"seq_rows_2024": 28118, "common_rows": 27583}, "ast": {"seq_rows_2024": 28118, "common_rows": 27583}, "fg3m": {"seq_rows_2024": 28118, "common_rows": 27583}}

## Family A_seq

| stat | n | games | CRPS cand | CRPS prod | delta [95% CI] | BH q | cov80 c/p | keep |
|---|---|---|---|---|---|---|---|---|
| pts | 27583 | 1315 | 3.3671 | 3.3424 | +0.0247 [+0.0164, +0.0331] | 0.001 | 0.803/0.795 | False |
| reb | 27583 | 1315 | 1.4046 | 1.3888 | +0.0158 [+0.0122, +0.0191] | 0.001 | 0.799/0.800 | False |
| ast | 27583 | 1315 | 0.9721 | 0.9604 | +0.0117 [+0.0091, +0.0143] | 0.001 | 0.801/0.796 | False |
| fg3m | 27583 | 1315 | 0.6435 | 0.6445 | -0.0010 [-0.0027, +0.0006] | 0.220 | 0.808/0.791 | False |
- pts: MAE 4.596 vs 4.552; bias +0.221 [+0.148,+0.290] vs +0.045
  - [pts] career=cold_lt20         n=1323   d=+0.0094 [-0.0235,+0.0447]
  - [pts] career=established       n=26260  d=+0.0254 [+0.0163,+0.0340]
  - [pts] phase=first15            n=4621   d=+0.0078 [-0.0156,+0.0342]
  - [pts] phase=rest               n=22962  d=+0.0280 [+0.0201,+0.0363]
  - [pts] role=bench               n=15526  d=+0.0209 [+0.0110,+0.0313]
  - [pts] role=starter             n=12057  d=+0.0295 [+0.0152,+0.0439]
  - [pts] teammate=none_out        n=4187   d=+0.0521 [+0.0314,+0.0718]
  - [pts] teammate=teammate_out    n=23378  d=+0.0197 [+0.0114,+0.0294]
- reb: MAE 1.908 vs 1.896; bias +0.023 [-0.008,+0.053] vs +0.032
  - [reb] career=cold_lt20         n=1323   d=+0.0035 [-0.0131,+0.0200]
  - [reb] career=established       n=26260  d=+0.0165 [+0.0128,+0.0203]
  - [reb] phase=first15            n=4621   d=+0.0151 [+0.0062,+0.0239]
  - [reb] phase=rest               n=22962  d=+0.0160 [+0.0121,+0.0197]
  - [reb] role=bench               n=15526  d=+0.0166 [+0.0121,+0.0215]
  - [reb] role=starter             n=12057  d=+0.0148 [+0.0099,+0.0204]
  - [reb] teammate=none_out        n=4187   d=+0.0227 [+0.0146,+0.0306]
  - [reb] teammate=teammate_out    n=23378  d=+0.0146 [+0.0111,+0.0183]
- ast: MAE 1.331 vs 1.322; bias +0.057 [+0.035,+0.078] vs +0.037
  - [ast] career=cold_lt20         n=1323   d=+0.0047 [-0.0049,+0.0137]
  - [ast] career=established       n=26260  d=+0.0121 [+0.0094,+0.0148]
  - [ast] phase=first15            n=4621   d=+0.0091 [+0.0023,+0.0157]
  - [ast] phase=rest               n=22962  d=+0.0122 [+0.0091,+0.0154]
  - [ast] role=bench               n=15526  d=+0.0105 [+0.0078,+0.0137]
  - [ast] role=starter             n=12057  d=+0.0132 [+0.0090,+0.0176]
  - [ast] teammate=none_out        n=4187   d=+0.0226 [+0.0160,+0.0299]
  - [ast] teammate=teammate_out    n=23378  d=+0.0096 [+0.0067,+0.0123]
- fg3m: MAE 0.901 vs 0.894; bias +0.029 [+0.014,+0.044] vs -0.010
  - [fg3m] career=cold_lt20         n=1323   d=-0.0067 [-0.0122,-0.0010]
  - [fg3m] career=established       n=26260  d=-0.0007 [-0.0025,+0.0009]
  - [fg3m] phase=first15            n=4621   d=-0.0023 [-0.0062,+0.0020]
  - [fg3m] phase=rest               n=22962  d=-0.0007 [-0.0025,+0.0009]
  - [fg3m] role=bench               n=15526  d=+0.0001 [-0.0017,+0.0020]
  - [fg3m] role=starter             n=12057  d=-0.0024 [-0.0053,+0.0004]
  - [fg3m] teammate=none_out        n=4187   d=+0.0011 [-0.0036,+0.0054]
  - [fg3m] teammate=teammate_out    n=23378  d=-0.0014 [-0.0030,+0.0004]

## Family B_equal_blend

| stat | n | games | CRPS cand | CRPS prod | delta [95% CI] | BH q | cov80 c/p | keep |
|---|---|---|---|---|---|---|---|---|
| pts | 27583 | 1315 | 3.3366 | 3.3424 | -0.0058 [-0.0100, -0.0016] | 0.016 | 0.793/0.795 | True |
| reb | 27583 | 1315 | 1.3892 | 1.3888 | +0.0004 [-0.0015, +0.0020] | 0.671 | 0.793/0.800 | False |
| ast | 27583 | 1315 | 0.9608 | 0.9604 | +0.0004 [-0.0009, +0.0017] | 0.671 | 0.780/0.796 | False |
| fg3m | 27583 | 1315 | 0.6414 | 0.6445 | -0.0031 [-0.0040, -0.0023] | 0.002 | 0.775/0.791 | False |
- pts: MAE 4.557 vs 4.552; bias +0.133 [+0.062,+0.201] vs +0.045
  - [pts] career=cold_lt20         n=1323   d=-0.0120 [-0.0278,+0.0053]
  - [pts] career=established       n=26260  d=-0.0055 [-0.0101,-0.0012]
  - [pts] phase=first15            n=4621   d=-0.0191 [-0.0311,-0.0063]
  - [pts] phase=rest               n=22962  d=-0.0031 [-0.0074,+0.0010]
  - [pts] role=bench               n=15526  d=-0.0049 [-0.0096,+0.0005]
  - [pts] role=starter             n=12057  d=-0.0070 [-0.0144,+0.0000]
  - [pts] teammate=none_out        n=4187   d=+0.0081 [-0.0016,+0.0177]
  - [pts] teammate=teammate_out    n=23378  d=-0.0083 [-0.0126,-0.0035]
- reb: MAE 1.896 vs 1.896; bias +0.028 [-0.002,+0.056] vs +0.032
  - [reb] career=cold_lt20         n=1323   d=-0.0066 [-0.0145,+0.0013]
  - [reb] career=established       n=26260  d=+0.0007 [-0.0010,+0.0026]
  - [reb] phase=first15            n=4621   d=-0.0009 [-0.0052,+0.0034]
  - [reb] phase=rest               n=22962  d=+0.0006 [-0.0013,+0.0024]
  - [reb] role=bench               n=15526  d=+0.0004 [-0.0019,+0.0027]
  - [reb] role=starter             n=12057  d=+0.0003 [-0.0020,+0.0030]
  - [reb] teammate=none_out        n=4187   d=+0.0038 [+0.0001,+0.0078]
  - [reb] teammate=teammate_out    n=23378  d=-0.0002 [-0.0020,+0.0015]
- ast: MAE 1.321 vs 1.322; bias +0.047 [+0.026,+0.068] vs +0.037
  - [ast] career=cold_lt20         n=1323   d=-0.0027 [-0.0074,+0.0018]
  - [ast] career=established       n=26260  d=+0.0005 [-0.0007,+0.0018]
  - [ast] phase=first15            n=4621   d=-0.0016 [-0.0048,+0.0017]
  - [ast] phase=rest               n=22962  d=+0.0008 [-0.0007,+0.0024]
  - [ast] role=bench               n=15526  d=+0.0004 [-0.0010,+0.0020]
  - [ast] role=starter             n=12057  d=+0.0004 [-0.0017,+0.0024]
  - [ast] teammate=none_out        n=4187   d=+0.0057 [+0.0026,+0.0092]
  - [ast] teammate=teammate_out    n=23378  d=-0.0006 [-0.0021,+0.0008]
- fg3m: MAE 0.895 vs 0.894; bias +0.010 [-0.006,+0.025] vs -0.010
  - [fg3m] career=cold_lt20         n=1323   d=-0.0054 [-0.0081,-0.0025]
  - [fg3m] career=established       n=26260  d=-0.0030 [-0.0039,-0.0022]
  - [fg3m] phase=first15            n=4621   d=-0.0040 [-0.0061,-0.0018]
  - [fg3m] phase=rest               n=22962  d=-0.0029 [-0.0039,-0.0021]
  - [fg3m] role=bench               n=15526  d=-0.0020 [-0.0029,-0.0011]
  - [fg3m] role=starter             n=12057  d=-0.0046 [-0.0060,-0.0031]
  - [fg3m] teammate=none_out        n=4187   d=-0.0022 [-0.0045,-0.0000]
  - [fg3m] teammate=teammate_out    n=23378  d=-0.0033 [-0.0041,-0.0024]

## Family C_learned

| stat | n | games | CRPS cand | CRPS prod | delta [95% CI] | BH q | cov80 c/p | keep |
|---|---|---|---|---|---|---|---|---|
| pts | 22974 | 1091 | 3.3279 | 3.3336 | -0.0058 [-0.0091, -0.0025] | 0.006 | 0.795/0.800 | True |
| reb | 22974 | 1091 | 1.3924 | 1.3937 | -0.0013 [-0.0024, -0.0003] | 0.023 | 0.793/0.799 | False |
| ast | 22974 | 1091 | 0.9593 | 0.9599 | -0.0006 [-0.0016, +0.0004] | 0.235 | 0.783/0.797 | False |
| fg3m | 22974 | 1091 | 0.6389 | 0.6418 | -0.0029 [-0.0039, -0.0019] | 0.002 | 0.776/0.794 | False |
  - [pts] career=cold_lt20         n=964    d=-0.0113 [-0.0230,-0.0020]
  - [pts] career=established       n=22010  d=-0.0055 [-0.0090,-0.0023]
  - [pts] phase=first15            n=145    d=-0.0299 [-0.0771,+0.0170]
  - [pts] phase=rest               n=22829  d=-0.0056 [-0.0089,-0.0022]
  - [pts] role=bench               n=13072  d=-0.0054 [-0.0095,-0.0017]
  - [pts] role=starter             n=9902   d=-0.0062 [-0.0120,-0.0009]
  - [pts] teammate=none_out        n=3542   d=+0.0097 [+0.0016,+0.0168]
  - [pts] teammate=teammate_out    n=19414  d=-0.0086 [-0.0121,-0.0055]
  - [reb] career=cold_lt20         n=964    d=-0.0064 [-0.0112,-0.0018]
  - [reb] career=established       n=22010  d=-0.0011 [-0.0022,-0.0000]
  - [reb] phase=first15            n=145    d=-0.0020 [-0.0155,+0.0128]
  - [reb] phase=rest               n=22829  d=-0.0013 [-0.0024,-0.0002]
  - [reb] role=bench               n=13072  d=-0.0022 [-0.0035,-0.0008]
  - [reb] role=starter             n=9902   d=-0.0001 [-0.0017,+0.0015]
  - [reb] teammate=none_out        n=3542   d=+0.0011 [-0.0010,+0.0033]
  - [reb] teammate=teammate_out    n=19414  d=-0.0017 [-0.0028,-0.0007]
  - [ast] career=cold_lt20         n=964    d=+0.0013 [-0.0031,+0.0056]
  - [ast] career=established       n=22010  d=-0.0007 [-0.0018,+0.0003]
  - [ast] phase=first15            n=145    d=+0.0001 [-0.0130,+0.0101]
  - [ast] phase=rest               n=22829  d=-0.0007 [-0.0016,+0.0004]
  - [ast] role=bench               n=13072  d=-0.0003 [-0.0017,+0.0009]
  - [ast] role=starter             n=9902   d=-0.0011 [-0.0027,+0.0006]
  - [ast] teammate=none_out        n=3542   d=+0.0023 [-0.0000,+0.0046]
  - [ast] teammate=teammate_out    n=19414  d=-0.0012 [-0.0024,+0.0000]
  - [fg3m] career=cold_lt20         n=964    d=-0.0048 [-0.0087,-0.0009]
  - [fg3m] career=established       n=22010  d=-0.0028 [-0.0039,-0.0018]
  - [fg3m] phase=first15            n=145    d=-0.0042 [-0.0161,+0.0074]
  - [fg3m] phase=rest               n=22829  d=-0.0029 [-0.0039,-0.0019]
  - [fg3m] role=bench               n=13072  d=-0.0016 [-0.0029,-0.0005]
  - [fg3m] role=starter             n=9902   d=-0.0045 [-0.0064,-0.0028]
  - [fg3m] teammate=none_out        n=3542   d=-0.0017 [-0.0049,+0.0017]
  - [fg3m] teammate=teammate_out    n=19414  d=-0.0031 [-0.0043,-0.0020]

## Kept (stats passing the pre-registered rule): {"A_seq": [], "B_equal_blend": ["pts"], "C_learned": ["pts"]}