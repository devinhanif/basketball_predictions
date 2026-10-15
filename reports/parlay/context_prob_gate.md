# PARLAY_CONTEXT_PROB minimum-data gate (counts and power; no contrast computed)

Real legs scored per season (DraftKings t60 over main lines, played, OOF marginal): {'2023': 53267, '2024': 53758}; games: {'2023': 1316, '2024': 1300}.

| season set | cells | meet inventory floor (>=300 pairs, >=40 games per side) | inventory z-CI excludes 0 | best upper-bound power (point estimate) | best upper-bound power (half effect) | best lower-bound power (point) | cells with upper-bound power >= 0.8 |
|---|---|---|---|---|---|---|---|
| select_2023 | 114 | 114 | 36 | 0.237 | 0.119 | 0.068 | 0 |
| report_2024 | 114 | 114 | 36 | 0.238 | 0.120 | 0.067 | 0 |
| pooled_2023_24 | 114 | 114 | 36 | 0.371 | 0.162 | 0.076 | 0 |

Power = one-sided alpha 0.05, paired log loss, truth = the 2020-22 correlation shift (scale 1) or half of it.
Upper bound treats units as independent; lower bound treats all units of a game as one (perfectly dependent).

## Top 12 cells by upper-bound power, report season 2024

| class | context | pairs ctx | games ctx | expected mean d per treated unit | median abs dp | power up | power lo | MDE up | MDE lo |
|---|---|---|---|---|---|---|---|---|---|
| teammate_cross:pts_i-ast_j | compete | 16404 | 1294 | -1.05e-04 | 0.0059 | 0.238 | 0.066 | 8.0e-05 | 5.4e-04 |
| teammate_cross:pts_i-reb_j | lead_x_compete | 5213 | 621 | -1.39e-04 | 0.0065 | 0.158 | 0.059 | 3.9e-05 | 3.1e-04 |
| teammate_cross:pts_i-ast_j | lead_x_compete | 4348 | 598 | -1.59e-04 | 0.0071 | 0.152 | 0.060 | 5.3e-05 | 3.6e-04 |
| teammate_cross:pts_i-reb_j | compete | 17977 | 1297 | -3.99e-05 | 0.0036 | 0.150 | 0.058 | 3.7e-05 | 3.0e-04 |
| teammate:ast-ast | compete | 6888 | 1291 | -6.42e-05 | 0.0045 | 0.121 | 0.064 | 1.2e-04 | 4.6e-04 |
| own_margin:reb | fav | 7547 | 1284 | -2.85e-05 | 0.0032 | 0.120 | 0.066 | 1.6e-04 | 5.3e-04 |
| same_player:pts-ast | lead_out | 2669 | 598 | -7.81e-05 | 0.0053 | 0.112 | 0.067 | 2.0e-04 | 5.8e-04 |
| same_player:pts-reb | lead_out | 3996 | 625 | -5.22e-05 | 0.0044 | 0.108 | 0.064 | 1.4e-04 | 4.7e-04 |
| same_player:pts-reb | vac48 | 6461 | 904 | -1.54e-05 | 0.0024 | 0.099 | 0.062 | 1.2e-04 | 4.1e-04 |
| teammate:pts-pts | lead_x_compete | 1493 | 456 | -1.39e-04 | 0.0065 | 0.096 | 0.056 | 3.9e-05 | 2.3e-04 |
| same_player:pts-reb | vac24 | 10299 | 1177 | -3.89e-06 | 0.0012 | 0.092 | 0.060 | 1.1e-04 | 3.6e-04 |
| same_player:ast-reb | lead_out | 2855 | 623 | -3.79e-05 | 0.0037 | 0.090 | 0.062 | 1.5e-04 | 4.1e-04 |
