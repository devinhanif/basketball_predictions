# ELO_HISTORY_WINDOW results (docs/prereg/ELO_HISTORY_WINDOW.md)

Frozen sha256 `d5432e50523f270b4fafc6a093b52d0fc7bef967d30e3fe1a49c052f66abc633`; git 12e63024e8db22f64bdfeafc7eeafddc85a478d5; seeds {'ga': 0, 'bootstrap': 0, 'n_boot': 2000, 'block_days': 14}.

| season | arm | n | logloss | brier | dlogloss [game CI] | block CI | p | p_bh | ece |
|---|---|---|---|---|---|---|---|---|---|
| 2023 | E0 | 1318 | 0.6114 | 0.2123 | +0.0000 [+0.0000,+0.0000] | [+0.0000,+0.0000] | nan | nan | 0.0325 |
| 2023 | E1 | 1318 | 0.6114 | 0.2123 | +0.0000 [-0.0002,+0.0004] | [-0.0001,+0.0002] | 0.750 | 0.750 | 0.0338 |
| 2023 | E2 | 1318 | 0.6122 | 0.2126 | +0.0008 [-0.0005,+0.0021] | [-0.0001,+0.0019] | 0.203 | 0.406 | 0.0340 |
| 2023 | E3 | 1318 | 0.6125 | 0.2126 | +0.0011 [-0.0006,+0.0028] | [-0.0009,+0.0035] | 0.204 | nan | 0.0287 |
| 2024 | E0 | 1320 | 0.6108 | 0.2120 | +0.0000 [+0.0000,+0.0000] | [+0.0000,+0.0000] | nan | nan | 0.0178 |
| 2024 | E1 | 1320 | 0.6108 | 0.2120 | +0.0000 [+0.0000,+0.0000] | [+0.0000,+0.0000] | 0.014 | 0.028 | 0.0178 |
| 2024 | E2 | 1320 | 0.6107 | 0.2119 | -0.0001 [-0.0017,+0.0015] | [-0.0016,+0.0015] | 0.915 | 0.915 | 0.0180 |
| 2024 | E3 | 1320 | 0.6112 | 0.2120 | +0.0004 [-0.0016,+0.0024] | [-0.0008,+0.0016] | 0.724 | nan | 0.0127 |

Slices (arm - E0):

| season | slice | arm | n | dlogloss [CI] |
|---|---|---|---|---|
| 2023 | first15 | E1 | 232 | +0.0000 [-0.0013,+0.0014] |
| 2023 | first15 | E2 | 232 | +0.0014 [-0.0014,+0.0040] |
| 2023 | first15 | E3 | 232 | +0.0048 [-0.0001,+0.0094] |
| 2023 | rest | E1 | 1086 | +0.0001 [-0.0002,+0.0003] |
| 2023 | rest | E2 | 1086 | +0.0007 [-0.0006,+0.0022] |
| 2023 | rest | E3 | 1086 | +0.0004 [-0.0016,+0.0022] |
| 2023 | home_favourite | E1 | 816 | -0.0001 [-0.0005,+0.0002] |
| 2023 | home_favourite | E2 | 816 | +0.0010 [-0.0003,+0.0022] |
| 2023 | home_favourite | E3 | 816 | +0.0012 [-0.0007,+0.0030] |
| 2023 | close(|elo diff|<50) | E1 | 377 | +0.0002 [-0.0005,+0.0008] |
| 2023 | close(|elo diff|<50) | E2 | 377 | +0.0017 [-0.0003,+0.0036] |
| 2023 | close(|elo diff|<50) | E3 | 377 | -0.0011 [-0.0030,+0.0009] |
| 2024 | first15 | E1 | 235 | +0.0001 [-0.0000,+0.0001] |
| 2024 | first15 | E2 | 235 | -0.0020 [-0.0063,+0.0023] |
| 2024 | first15 | E3 | 235 | +0.0017 [-0.0053,+0.0083] |
| 2024 | rest | E1 | 1085 | +0.0000 [-0.0000,+0.0000] |
| 2024 | rest | E2 | 1085 | +0.0003 [-0.0015,+0.0022] |
| 2024 | rest | E3 | 1085 | +0.0001 [-0.0020,+0.0021] |
| 2024 | home_favourite | E1 | 805 | +0.0000 [-0.0000,+0.0000] |
| 2024 | home_favourite | E2 | 805 | +0.0006 [-0.0013,+0.0023] |
| 2024 | home_favourite | E3 | 805 | +0.0001 [-0.0022,+0.0023] |
| 2024 | close(|elo diff|<50) | E1 | 309 | -0.0000 [-0.0000,+0.0000] |
| 2024 | close(|elo diff|<50) | E2 | 309 | +0.0033 [+0.0002,+0.0063] |
| 2024 | close(|elo diff|<50) | E3 | 309 | -0.0008 [-0.0030,+0.0015] |

GA parameters: {"E2": {"params": {"k_factor": 10.018044163017418, "home_advantage_elo": 42.65605586502626, "season_carryover": 0.5744306705538494, "mov_c": 3.956166139481889, "mov_div": 0.0021047254074218463}, "tune_loss": 0.6407889345560659, "n_tune_games": 4957}, "E3": {"params": {"k_factor": 7.928401849827729, "home_advantage_elo": 40.55932671879022, "season_carryover": 0.5128605754430284, "mov_c": 4.072618829367244, "mov_div": 0.0006040812684123711}, "tune_loss": 0.6398735054138726, "n_tune_games": 4957}}

Reference injury-Elo v2 (not an arm): {'2023': {'n': 1318, 'n_keys': 1318, 'logloss': 0.6055427981823148}, '2024': {'n': 1315, 'n_keys': 1320, 'logloss': 0.5979124922996117}}

Verdict: any arm passes = False; closed on select season = True
* E1: pass=False {"2023": {"floor": false, "ci_upper_lt_0": false, "bh_p_lt_0.05": false, "block_ci_upper_lt_0": false, "ece_not_worse": true, "first15_not_worse": true}, "2024": {"floor": false, "ci_upper_lt_0": false, "bh_p_lt_0.05": true, "block_ci_upper_lt_0": false, "ece_not_worse": true, "first15_not_worse": true}}
* E2: pass=False {"2023": {"floor": false, "ci_upper_lt_0": false, "bh_p_lt_0.05": false, "block_ci_upper_lt_0": false, "ece_not_worse": true, "first15_not_worse": true}, "2024": {"floor": false, "ci_upper_lt_0": false, "bh_p_lt_0.05": false, "block_ci_upper_lt_0": false, "ece_not_worse": true, "first15_not_worse": true}}
