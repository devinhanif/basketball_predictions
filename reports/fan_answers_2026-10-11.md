# Fan answers A-E (descriptive, 2026-10-11)

**DESCRIPTIVE ONLY: no rule, no claim, no model fit.** Seasons 2023 (2023-24) and 2024 (2024-25) only; season 2025 is the frozen holdout and was never loaded (every SQL has season <= 2024 and we assert). nba.duckdb opened read_only (plus data/history/nba_history.duckdb read_only for the 2021-22 win% only). All CIs are 95% percentile bootstraps clustered by game_id (500 resamples, seed 20261011, one shared resample matrix over 3958 games of 2022-24; rows from the same game are not independent). n is on every row. No multiplicity correction: these are many descriptive cells, so expect a few spurious moves. A pattern here is a question for a pre-registration, not a finding. Script: data/scratch/fan_answers_2026-10-11.py.

## Definitions fixed before any number was looked at

- Regular season only (game_id prefix 002) for every analysis row; as-of features roll over all games (playoffs included) so they are continuous.
- Team strength tercile = rank of the team's PRIOR-season regular-season win% among 30 teams (10 / 10 / 10): T1 weak/lottery-ish, T2 middle, T3 contender. Knowable in the offseason. Win% ties are broken by team_id (deterministic). 2021-22 win% for the 2022 season comes from data/history (used for question D peers). Question E also uses the game's pre-tip Elo p (reports/odds_history/game_rows, tercile cut pooled over 2023-24).
- Prior-10 = mean of the player's previous 10 played games (minutes > 0), min 5, crossing seasons (2022 look-back loaded). 'Played' = minutes > 0.
- Report snapshots: T-60 = latest official-report snapshot with as_of <= real tip - 60 min (tip_source=real). Morning = the earliest snapshot stamped on the game's own date, kept only when it is the 11:00 snapshot (hour < 15); 2328 of 2460 games have one (the rest have only the 17:00 snapshot or none). A player not listed in a snapshot is 'not listed'. Games with no snapshot are excluded from that snapshot's denominators, never treated as 'nobody listed'.
- Model residuals come from reports/odds_history/model_rows_<season>.parquet (OOF production context-residual): pts residual = actual - mean_int (positive = we were low). Those rows exist only for played rows with n_prior >= 5. There is no stored minutes forecast, so the 'minutes residual' is actual minutes minus the prior-10 mean (the recency baseline).

## A. Load management: stars sitting the second night of a back-to-back

Universe: 'star opportunities' = (player, team-game) where the player played the team's PREVIOUS game and was then a star as of that game: prior-10 pts >= 20 (including that game) OR top-3 in prior-10 minutes among that team's players in that game. Sat = no minutes in the team-game. Conditioning on having played the previous game removes most multi-game injuries (symmetric across b2b and other nights); traded-away players (played the same date for another team) are dropped. b2b = the team's previous game was yesterday (rest_gap 1); other nights = rest_gap >= 2. A sit-out here is any sit-out (injury, rest, personal), not only 'load management'. Opportunities 15013 (2688 on b2b second nights); 334 players.

### A1. League-wide star sit rate, b2b second night vs other nights

| universe | n_b2b | sit_rate_b2b | ci_b2b | n_other | sit_rate_other | ci_other | diff | ci_diff |
|---|---|---|---|---|---|---|---|---|
| all | 2688 | 0.124 | [0.110, 0.138] | 12325 | 0.068 | [0.063, 0.072] | 0.056 | [0.041, 0.070] |
| season 2023 | 1292 | 0.113 | [0.093, 0.132] | 6187 | 0.059 | [0.054, 0.065] | 0.054 | [0.034, 0.073] |
| season 2024 | 1396 | 0.135 | [0.112, 0.156] | 6138 | 0.077 | [0.069, 0.084] | 0.058 | [0.034, 0.080] |
| pts-star (prior-10 pts>=20) | 1170 | 0.136 | [0.115, 0.155] | 5285 | 0.078 | [0.070, 0.085] | 0.058 | [0.037, 0.079] |
| top-3 minutes only | 1518 | 0.115 | [0.096, 0.131] | 7040 | 0.061 | [0.055, 0.066] | 0.055 | [0.036, 0.071] |

Reading: Stars sit the second night of a back-to-back 12.4% of the time vs 6.8% on other nights (diff 5.6 pts, CI 4.1 to 7.0, n=2688 vs 12325), same in both seasons; the lift is the same for pts-stars and top-3-minutes players.

### A2. Star b2b sit rate by team (both seasons pooled, with per-season rates)

Sorted by pooled b2b star sit rate. Per-team n is small (about 40 star-b2b opportunities per team-season pair), so each CI is wide.

| team | n_b2b_2023 | sits_b2b_2023 | rate_b2b_2023 | rate_other_2023 | n_b2b_2024 | sits_b2b_2024 | rate_b2b_2024 | rate_other_2024 | n_b2b | sits_b2b | rate_b2b | ci_b2b | rate_other | excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BOS | 46 | 13 | 0.283 | 0.084 | 40 | 12 | 0.300 | 0.121 | 86 | 25 | 0.291 | [0.158, 0.435] | 0.102 | 0.189 |
| NOP | 42 | 11 | 0.262 | 0.055 | 51 | 15 | 0.294 | 0.118 | 93 | 26 | 0.280 | [0.198, 0.372] | 0.085 | 0.194 |
| UTA | 40 | 6 | 0.150 | 0.060 | 43 | 15 | 0.349 | 0.129 | 83 | 21 | 0.253 | [0.145, 0.380] | 0.093 | 0.160 |
| TOR | 42 | 9 | 0.214 | 0.070 | 46 | 10 | 0.217 | 0.141 | 88 | 19 | 0.216 | [0.120, 0.323] | 0.105 | 0.111 |
| PHI | 43 | 6 | 0.140 | 0.097 | 47 | 12 | 0.255 | 0.127 | 90 | 18 | 0.200 | [0.119, 0.282] | 0.112 | 0.088 |
| DAL | 42 | 7 | 0.167 | 0.090 | 41 | 8 | 0.195 | 0.110 | 83 | 15 | 0.181 | [0.104, 0.263] | 0.100 | 0.081 |
| MEM | 42 | 7 | 0.167 | 0.100 | 45 | 7 | 0.156 | 0.069 | 87 | 14 | 0.161 | [0.083, 0.246] | 0.084 | 0.077 |
| LAL | 46 | 6 | 0.130 | 0.054 | 44 | 7 | 0.159 | 0.064 | 90 | 13 | 0.144 | [0.066, 0.243] | 0.059 | 0.086 |
| MIA | 39 | 5 | 0.128 | 0.088 | 45 | 7 | 0.156 | 0.085 | 84 | 12 | 0.143 | [0.067, 0.222] | 0.087 | 0.056 |
| CHA | 46 | 5 | 0.109 | 0.086 | 47 | 8 | 0.170 | 0.120 | 93 | 13 | 0.140 | [0.069, 0.219] | 0.103 | 0.037 |
| DEN | 39 | 3 | 0.077 | 0.038 | 50 | 8 | 0.160 | 0.049 | 89 | 11 | 0.124 | [0.050, 0.205] | 0.044 | 0.080 |
| PHX | 42 | 5 | 0.119 | 0.050 | 48 | 6 | 0.125 | 0.072 | 90 | 11 | 0.122 | [0.064, 0.189] | 0.060 | 0.062 |
| BKN | 42 | 2 | 0.048 | 0.038 | 45 | 8 | 0.178 | 0.136 | 87 | 10 | 0.115 | [0.051, 0.176] | 0.086 | 0.029 |
| GSW | 52 | 7 | 0.135 | 0.047 | 44 | 4 | 0.091 | 0.065 | 96 | 11 | 0.115 | [0.053, 0.179] | 0.056 | 0.059 |
| LAC | 45 | 6 | 0.133 | 0.059 | 51 | 5 | 0.098 | 0.062 | 96 | 11 | 0.115 | [0.043, 0.206] | 0.060 | 0.054 |
| MIL | 42 | 4 | 0.095 | 0.065 | 49 | 6 | 0.122 | 0.045 | 91 | 10 | 0.110 | [0.042, 0.199] | 0.055 | 0.055 |
| CLE | 45 | 3 | 0.067 | 0.080 | 49 | 7 | 0.143 | 0.086 | 94 | 10 | 0.106 | [0.046, 0.179] | 0.083 | 0.023 |
| WAS | 39 | 4 | 0.103 | 0.048 | 47 | 5 | 0.106 | 0.055 | 86 | 9 | 0.105 | [0.046, 0.176] | 0.051 | 0.053 |
| POR | 42 | 3 | 0.071 | 0.109 | 39 | 5 | 0.128 | 0.052 | 81 | 8 | 0.099 | [0.042, 0.156] | 0.080 | 0.019 |
| HOU | 43 | 6 | 0.140 | 0.020 | 54 | 3 | 0.056 | 0.059 | 97 | 9 | 0.093 | [0.040, 0.148] | 0.039 | 0.054 |
| OKC | 42 | 3 | 0.071 | 0.030 | 48 | 5 | 0.104 | 0.061 | 90 | 8 | 0.089 | [0.030, 0.156] | 0.045 | 0.044 |
| DET | 45 | 5 | 0.111 | 0.075 | 45 | 3 | 0.067 | 0.060 | 90 | 8 | 0.089 | [0.040, 0.149] | 0.068 | 0.021 |
| IND | 39 | 3 | 0.077 | 0.054 | 43 | 4 | 0.093 | 0.045 | 82 | 7 | 0.085 | [0.020, 0.177] | 0.049 | 0.036 |
| ATL | 47 | 4 | 0.085 | 0.063 | 52 | 4 | 0.077 | 0.081 | 99 | 8 | 0.081 | [0.027, 0.138] | 0.072 | 0.009 |
| SAS | 44 | 3 | 0.068 | 0.059 | 48 | 4 | 0.083 | 0.041 | 92 | 7 | 0.076 | [0.022, 0.141] | 0.050 | 0.026 |
| CHI | 44 | 3 | 0.068 | 0.034 | 40 | 3 | 0.075 | 0.091 | 84 | 6 | 0.071 | [0.012, 0.147] | 0.063 | 0.009 |
| NYK | 41 | 3 | 0.073 | 0.035 | 58 | 2 | 0.034 | 0.051 | 99 | 5 | 0.051 | [0.012, 0.092] | 0.044 | 0.006 |
| MIN | 40 | 2 | 0.050 | 0.034 | 42 | 2 | 0.048 | 0.010 | 82 | 4 | 0.049 | [0.011, 0.099] | 0.022 | 0.027 |
| ORL | 45 | 2 | 0.044 | 0.035 | 42 | 2 | 0.048 | 0.059 | 87 | 4 | 0.046 | [0.011, 0.094] | 0.048 | -0.002 |
| SAC | 46 | 0 | 0.000 | 0.025 | 53 | 1 | 0.019 | 0.045 | 99 | 1 | 0.010 | [0.000, 0.033] | 0.036 | -0.026 |

Reading: Team b2b star sit rates run from 1% (SAC) to 29% (BOS) on about 85-99 star-b2b opportunities each; every team CI is wide (typically +/-8 to 15 pts), so single-team ranks are soft.

### A3. The 8 teams with the highest star b2b sit rate (pooled 2023-24, teams with >= 30 star-b2b opportunities)

| rank | team | n_b2b | sits | rate_b2b | ci | rate_2023 | rate_2024 | rate_other_nights |
|---|---|---|---|---|---|---|---|---|
| 1 | BOS | 86 | 25 | 0.291 | [0.158, 0.435] | 0.283 | 0.300 | 0.102 |
| 2 | NOP | 93 | 26 | 0.280 | [0.198, 0.372] | 0.262 | 0.294 | 0.085 |
| 3 | UTA | 83 | 21 | 0.253 | [0.145, 0.380] | 0.150 | 0.349 | 0.093 |
| 4 | TOR | 88 | 19 | 0.216 | [0.120, 0.323] | 0.214 | 0.217 | 0.105 |
| 5 | PHI | 90 | 18 | 0.200 | [0.119, 0.282] | 0.140 | 0.255 | 0.112 |
| 6 | DAL | 83 | 15 | 0.181 | [0.104, 0.263] | 0.167 | 0.195 | 0.100 |
| 7 | MEM | 87 | 14 | 0.161 | [0.083, 0.246] | 0.167 | 0.156 | 0.084 |
| 8 | LAL | 90 | 13 | 0.144 | [0.066, 0.243] | 0.130 | 0.159 | 0.059 |

Selection-noise yardstick: the mean of the top-8 team rates is 0.216. If every team had the same sit probability (labels shuffled across the 2688 b2b star opportunities, cell sizes kept, 500 shuffles), the mean top-8 rate would be 0.167 (95% range 0.155 to 0.180). The league-wide b2b star sit rate is 0.124.

Reading: Highest star b2b sit rates: BOS 29.1%, NOP 28.0%, UTA 25.3%, TOR 21.6%, PHI 20.0%, DAL 18.1%, MEM 16.1%, LAL 14.4%; their mean (21.6%) is above the 16.7% (15.5-18.0) the top 8 would average if all teams were identical, so the spread is more than pure noise and the top teams are plausibly driven by a few stars (A5 lists several of them).

### A4. Year-to-year stability of team rates (30 teams, 2023 vs 2024)

fisher_ci_30_teams treats the 30 teams as the units (it ignores that each team rate is itself noisy). game_boot_*: the game-clustered resample re-draws the noisy team rates, which attenuates a correlation (its mean sits below the point estimate), so that interval can exclude the point estimate; read it as the noise scale, not a CI centred on the point. null_*: correlation expected if the sit probability were identical for every team within season and night type (labels shuffled; sampling noise only). A real team trait should push the observed correlation above that range.

| team statistic | n_teams | corr_2023_vs_2024 | spearman | fisher_ci_30_teams | game_boot_mean | game_boot_95 | null_corr_mean | null_95_range |
|---|---|---|---|---|---|---|---|---|
| b2b star sit rate | 30 | 0.725 | 0.610 | [0.494, 0.861] | 0.475 | [0.176, 0.697] | -0.000 | [-0.370, 0.346] |
| other-night star sit rate | 30 | 0.375 | 0.408 | [0.017, 0.648] | 0.266 | [0.030, 0.522] | 0.009 | [-0.375, 0.372] |
| excess (b2b minus other) | 30 | 0.552 | 0.473 | [0.239, 0.761] | 0.293 | [-0.063, 0.577] | 0.004 | [-0.370, 0.354] |

Reading: Team b2b star sit rates correlate 0.73 across the two seasons (30 teams; Fisher CI 0.49-0.86; shuffle-null range -0.37 to 0.35), and the b2b-minus-other excess 0.55 (0.24-0.76): there is a persistent team trait, partly roster-specific (the other-night rate also correlates 0.38, which is injury-proneness of the same stars).

### A5. Individual stars with the most b2b sit-outs (>= 8 b2b opportunities)

| player | team | n_b2b | sits_b2b | rate_b2b | n_other | rate_other |
|---|---|---|---|---|---|---|
| Zion Williamson | NOP | 18 | 10 | 0.556 | 80 | 0.163 |
| Rj Barrett | TOR | 18 | 8 | 0.444 | 83 | 0.157 |
| Joel Embiid | PHI | 12 | 7 | 0.583 | 46 | 0.217 |
| Jaylen Brown | BOS | 20 | 7 | 0.350 | 108 | 0.111 |
| Lauri Markkanen | UTA | 20 | 7 | 0.350 | 81 | 0.111 |
| Luka Doncic | DAL | 23 | 7 | 0.304 | 97 | 0.103 |
| John Collins | UTA | 14 | 6 | 0.429 | 48 | 0.104 |
| Christian James McCollum | NOP | 20 | 6 | 0.300 | 91 | 0.033 |
| Lamelo Ball | CHA | 10 | 5 | 0.500 | 53 | 0.170 |
| Bradley Beal | PHX | 17 | 5 | 0.294 | 68 | 0.118 |
| Victor Wembanyama | SAS | 18 | 5 | 0.278 | 89 | 0.079 |
| Kyrie Irving | DAL | 19 | 5 | 0.263 | 89 | 0.112 |

Reading: The leaders are a handful of injury-prone or managed stars (Zion Williamson 10 of 18 b2b nights, Embiid 7 of 12, Barrett 8 of 18, Jaylen Brown 7 of 20), so 'team practice' and 'star availability' are not separable here.

### A6. Knowability of star sit-outs: were they on the official report?

Of star-opportunity sit-outs, the player's status in the latest T-60 snapshot and in the same-day 11:00 morning snapshot. 'Any status' counts a listed row of any kind (including 'available'); 'flagged' = out/doubtful/questionable/probable; the right three shares are the 'knowable before tip' share at three strengths. First block: games having BOTH snapshots (apples to apples); second: all games with a T-60 snapshot.

| group | snapshot | n_sitouts | out | doubtful | questionable | probable | available | not listed | share_on_report_any_status | share_on_report_any_status_ci | share_flagged_out_dbt_q_prob | share_flagged_out_dbt_q_prob_ci | share_out_or_doubtful | share_out_or_doubtful_ci |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| b2b second night [both-snapshot games] | s_t60 | 241 | 142 | 9 | 69 | 3 | 0 | 18 | 0.925 | [0.891, 0.956] | 0.925 | [0.891, 0.956] | 0.627 | [0.568, 0.693] |
| b2b second night [both-snapshot games] | s_m | 241 | 3 | 0 | 1 | 1 | 0 | 236 | 0.021 | [0.004, 0.044] | 0.021 | [0.004, 0.044] | 0.012 | [0.000, 0.035] |
| b2b second night [all games w/ T-60] | s_t60 | 334 | 199 | 11 | 97 | 3 | 0 | 24 | 0.928 | [0.897, 0.954] | 0.928 | [0.897, 0.954] | 0.629 | [0.572, 0.688] |
| other nights [both-snapshot games] | s_t60 | 838 | 461 | 25 | 268 | 15 | 2 | 67 | 0.920 | [0.902, 0.938] | 0.918 | [0.898, 0.936] | 0.580 | [0.542, 0.614] |
| other nights [both-snapshot games] | s_m | 838 | 305 | 37 | 288 | 20 | 4 | 184 | 0.780 | [0.752, 0.811] | 0.776 | [0.746, 0.805] | 0.408 | [0.370, 0.442] |
| other nights [all games w/ T-60] | s_t60 | 838 | 461 | 25 | 268 | 15 | 2 | 67 | 0.920 | [0.902, 0.938] | 0.918 | [0.898, 0.936] | 0.580 | [0.542, 0.614] |
| b2b second night [games w/ prior-evening report] | s_t60 | 131 | 77 | 4 | 38 | 2 | 0 | 10 | 0.924 | [0.876, 0.967] | 0.924 | [0.876, 0.967] | 0.618 | [0.530, 0.712] |
| b2b second night [games w/ prior-evening report] | s_p | 131 | 0 | 0 | 0 | 0 | 0 | 131 | 0.000 | [0.000, 0.000] | 0.000 | [0.000, 0.000] | 0.000 | [0.000, 0.000] |
| other nights [games w/ prior-evening report] | s_t60 | 498 | 281 | 13 | 156 | 8 | 2 | 38 | 0.924 | [0.900, 0.946] | 0.920 | [0.896, 0.942] | 0.590 | [0.543, 0.637] |
| other nights [games w/ prior-evening report] | s_p | 498 | 145 | 16 | 151 | 17 | 3 | 166 | 0.667 | [0.623, 0.709] | 0.661 | [0.618, 0.704] | 0.323 | [0.281, 0.366] |

Snapshot size check (rows listed per game, median): morning 11:00 snapshot, games where either team is on a back-to-back second night: 5 (n=617 games) vs other games 10 (n=1711); T-60 snapshot: 11 vs 11.

s_p = the latest snapshot stamped on a date before the game date (the evening-before 17:00 report); 1408 of 2460 games have one. Games without one are excluded from that row, not counted as unlisted.

Reading: At T-60, 93% of star sit-outs are on the report (63% as out/doubtful, 30% as questionable/probable) on both night types, but on b2b second nights only 2% were on the same-day 11:00 report and 0 of 131 on the evening-before report (vs 78% and 67% on other nights): the b2b sit-out is announced on the afternoon of the game, and the morning report is also thinner on b2b days (median 5 rows vs 10).

## B. Late scratches: P(played | status at T-60)

Universe: 26430 (game, player) rows listed at T-60 with those five statuses; 25576 have an identifiable team (played that game, or last/next team in the box-score history that is one of the game's two teams); the rest (two-way/G-League names with no NBA box score) are dropped. Player team for a non-player is inferred from box-score history around the date (a post-hoc identity lookup, not a result).

### B1. P(played) by status and season

| status_T60 | season | n | p_played | ci |
|---|---|---|---|---|
| out | pooled | 18294 | 0.002 | [0.001, 0.002] |
| out | 2023 | 8798 | 0.001 | [0.000, 0.002] |
| out | 2024 | 9496 | 0.002 | [0.001, 0.003] |
| doubtful | pooled | 484 | 0.019 | [0.008, 0.030] |
| doubtful | 2023 | 226 | 0.013 | [0.000, 0.029] |
| doubtful | 2024 | 258 | 0.023 | [0.008, 0.041] |
| questionable | pooled | 3863 | 0.479 | [0.464, 0.494] |
| questionable | 2023 | 1942 | 0.485 | [0.463, 0.508] |
| questionable | 2024 | 1921 | 0.472 | [0.451, 0.497] |
| probable | pooled | 1394 | 0.915 | [0.898, 0.930] |
| probable | 2023 | 630 | 0.919 | [0.893, 0.941] |
| probable | 2024 | 764 | 0.911 | [0.888, 0.932] |
| available | pooled | 1541 | 0.781 | [0.759, 0.807] |
| available | 2023 | 770 | 0.755 | [0.718, 0.793] |
| available | 2024 | 771 | 0.808 | [0.776, 0.840] |

Reading: Out means out (0.2% played), doubtful 1.9%, questionable 47.9% (CI 46.4 to 49.4, n=3863, same both seasons), probable 91.5%, 'available' 78%: 'questionable' is a coin flip, not a tag that carries information beyond that.

### B2. P(played) by status and team strength tercile (prior-season win%; T1 weak/lottery-ish, T2 middle, T3 contender; pooled 2023-24)

| status_T60 | n [T1] | p [T1] | ci [T1] | n [T2] | p [T2] | ci [T2] | n [T3] | p [T3] | ci [T3] | T1 minus T3 | ci_diff |
|---|---|---|---|---|---|---|---|---|---|---|---|
| out | 6695 | 0.001 | [0.000, 0.003] | 5541 | 0.002 | [0.001, 0.004] | 6058 | 0.001 | [0.000, 0.003] | 0.000 | [-0.002, 0.002] |
| doubtful | 170 | 0.012 | [0.000, 0.029] | 171 | 0.029 | [0.006, 0.054] | 143 | 0.014 | [0.000, 0.035] | -0.002 | [-0.025, 0.019] |
| questionable | 1438 | 0.408 | [0.384, 0.435] | 1275 | 0.538 | [0.509, 0.568] | 1150 | 0.502 | [0.470, 0.534] | -0.094 | [-0.133, -0.051] |
| probable | 282 | 0.933 | [0.901, 0.961] | 576 | 0.899 | [0.872, 0.926] | 536 | 0.922 | [0.896, 0.944] | 0.011 | [-0.026, 0.052] |
| available | 502 | 0.797 | [0.757, 0.838] | 643 | 0.782 | [0.750, 0.816] | 396 | 0.760 | [0.701, 0.812] | 0.037 | [-0.031, 0.105] |

Reading: Questionable players on weak teams (prior-season bottom third) play 40.8% vs 50.2% on contenders (diff -9.4 pts, CI -13.3 to -5.1); no other status differs by team strength.

### B3. 'Testing the waters': minutes minus prior-10 mean, among players who played (prior-10 >= 15 min, games with a T-60 snapshot)

Same support for every row (prior-10 >= 15, played). 'not listed' = played and absent from the T-60 report (the reference). Players who played are a selected group (those flagged and scratched are absent), so this is minutes conditional on playing.

| status_T60 | season | n_played | mean_min_minus_prior10 | ci | diff_vs_not_listed | ci_diff |
|---|---|---|---|---|---|---|
| questionable | pooled | 1613 | -1.570 | [-1.909, -1.260] | -1.318 | [-1.635, -1.007] |
| questionable | 2023 | 827 | -1.253 | [-1.682, -0.765] | -1.019 | [-1.460, -0.559] |
| questionable | 2024 | 786 | -1.903 | [-2.377, -1.455] | -1.633 | [-2.125, -1.185] |
| doubtful | pooled | 7 | -6.219 | [-10.862, -1.178] | -5.966 | [-10.612, -0.907] |
| doubtful | 2023 | 2 | -10.569 | [-19.188, 0.000] | -10.335 | [-19.041, 0.335] |
| doubtful | 2024 | 5 | -4.478 | [-7.764, -0.375] | -4.208 | [-7.529, -0.084] |
| probable | pooled | 1164 | -1.053 | [-1.451, -0.681] | -0.801 | [-1.183, -0.414] |
| probable | 2023 | 522 | -0.982 | [-1.638, -0.372] | -0.748 | [-1.430, -0.108] |
| probable | 2024 | 642 | -1.112 | [-1.640, -0.600] | -0.841 | [-1.342, -0.330] |
| available | pooled | 947 | -1.008 | [-1.492, -0.561] | -0.756 | [-1.218, -0.282] |
| available | 2023 | 440 | -0.945 | [-1.721, -0.198] | -0.711 | [-1.487, 0.063] |
| available | 2024 | 507 | -1.064 | [-1.648, -0.481] | -0.794 | [-1.372, -0.236] |
| not listed | pooled | 35464 | -0.252 | [-0.364, -0.152] |  |  |
| not listed | 2023 | 17736 | -0.234 | [-0.382, -0.089] |  |  |
| not listed | 2024 | 17728 | -0.270 | [-0.424, -0.149] |  |  |

Reading: Questionable players who played got 1.6 fewer minutes than their prior-10 mean (CI -1.9 to -1.3, n=1613), 1.3 fewer than unlisted players who played (CI -1.6 to -1.0); probable and 'available' players about 0.8 fewer: a real but modest 'testing the waters' discount, minutes conditional on playing.

### B4. Rotation players (prior-10 >= 20 min, played the previous team game) who did not play: what did the T-60 report say?

n rotation opportunities in games with a T-60 snapshot: 31074; did not play: 2351. 'Late scratch' = anything other than out/doubtful on the T-60 report.

| status_T60 | n_dnp_rotation | share_of_rotation_dnps | ci |
|---|---|---|---|
| out | 1242 | 0.528 | [0.507, 0.552] |
| doubtful | 85 | 0.036 | [0.026, 0.047] |
| questionable | 666 | 0.283 | [0.263, 0.304] |
| probable | 30 | 0.013 | [0.008, 0.018] |
| available | 4 | 0.002 | [0.000, 0.003] |
| not listed | 324 | 0.138 | [0.124, 0.152] |

Reading: Of 2351 rotation-player DNPs, 53% were out and 4% doubtful at T-60, so about 44% were not flagged out: 28% questionable, 14% not on the report at all (genuine late scratches), 1% probable.

## C. Rookie minutes: lottery-ish teams vs the rest

Rookie = players_static.first_season equals the game season. Rookie rows (played, regular season): 5706, 193 players (2023: 94, 2024: 99). 'Early' = the team's first 15 games of the season (team_game_no 0-14); 'later' = games 16-82. Rows are games the rookie PLAYED (a rookie who sat on the bench with a DNP is not a row, so mean minutes are conditional on playing). Minutes and >=15 share use all rookie rows; the minutes residual (minutes - prior-10) exists only with >= 5 prior games and the pts residual only for model rows (n_prior >= 5), so those columns carry their own n.

### C1. By team strength tercile, early vs later

| period | terc | n_rows | n_players | mean_min | ci_min | share_ge15 | ci_ge15 | n_dmin | min_resid | ci_dmin | n_pts | pts_resid | ci_pts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| games 1-15 | T1 weak/lottery-ish | 445 | 49 | 19.473 | [18.430, 20.525] | 0.661 | [0.610, 0.707] | 246 | 1.047 | [0.185, 1.926] | 243 | -0.448 | [-1.121, 0.112] |
| games 1-15 | T2 middle | 254 | 38 | 13.853 | [12.622, 15.290] | 0.398 | [0.345, 0.457] | 115 | 3.302 | [1.707, 4.758] | 113 | 1.238 | [0.238, 2.593] |
| games 1-15 | T3 contender | 244 | 39 | 9.908 | [8.860, 10.859] | 0.262 | [0.208, 0.321] | 95 | 3.261 | [1.919, 4.639] | 95 | -0.133 | [-1.011, 0.788] |
| games 16-82 | T1 weak/lottery-ish | 2231 | 70 | 21.336 | [20.880, 21.812] | 0.741 | [0.723, 0.762] | 2113 | 1.046 | [0.671, 1.372] | 2110 | 0.169 | [-0.070, 0.415] |
| games 16-82 | T2 middle | 1390 | 65 | 15.342 | [14.723, 15.983] | 0.481 | [0.452, 0.510] | 1224 | 0.682 | [0.209, 1.171] | 1220 | -0.297 | [-0.575, -0.034] |
| games 16-82 | T3 contender | 1142 | 58 | 12.929 | [12.138, 13.713] | 0.378 | [0.343, 0.410] | 1014 | 1.008 | [0.414, 1.632] | 1013 | -0.468 | [-0.786, -0.169] |

Reading: Rookies on weak teams play 19.5 min (66% of games with >=15 min) in the team's first 15 games vs 9.9 min (26%) on contenders, and the gap persists in games 16-82 (21.3 vs 12.9); the lottery effect is a whole-season level difference, not just an early one.

### C2. Weak-minus-contender differences (T1 minus T3)

| period | measure | T1 minus T3 | ci | n_T1 | n_T3 |
|---|---|---|---|---|---|
| games 1-15 | minutes | 9.565 | [8.225, 10.911] | 445 | 244 |
| games 1-15 | share>=15 | 0.398 | [0.323, 0.469] | 445 | 244 |
| games 1-15 | min_resid | -2.214 | [-3.756, -0.597] | 246 | 95 |
| games 1-15 | pts_resid | -0.315 | [-1.338, 0.719] | 243 | 95 |
| games 16-82 | minutes | 8.407 | [7.555, 9.288] | 2231 | 1142 |
| games 16-82 | share>=15 | 0.363 | [0.329, 0.405] | 2231 | 1142 |
| games 16-82 | min_resid | 0.038 | [-0.623, 0.680] | 2113 | 1014 |
| games 16-82 | pts_resid | 0.637 | [0.280, 1.047] | 2110 | 1013 |

Reading: Weak minus contender: +9.6 min (CI 8.2 to 10.9) and +40 pts of >=15-min share early; the minutes residual vs prior-10 is lower for weak-team rookies early (-2.2, CI -3.8 to -0.6, n=246 vs 95: contender rookies ramp up faster than their own recent average), and the pts residual gap is only visible later (+0.64, CI 0.28 to 1.05).

### C3. By draft-slot band (the 'lottery pick' reading of the same question), early vs later

| period | band | n_rows | n_players | mean_min | ci_min | share_ge15 | ci_ge15 | n_dmin | min_resid | ci_dmin | n_pts | pts_resid | ci_pts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| games 1-15 | 1-5 | 108 | 8 | 25.411 | [23.775, 26.907] | 0.880 | [0.819, 0.933] | 69 | 1.489 | [-0.102, 2.726] | 68 | 0.679 | [-0.827, 2.119] |
| games 1-15 | 6-14 | 175 | 15 | 20.098 | [18.882, 21.430] | 0.686 | [0.617, 0.754] | 105 | 0.589 | [-0.727, 1.788] | 104 | -0.180 | [-1.059, 0.823] |
| games 1-15 | 15-30 | 255 | 30 | 16.179 | [14.851, 17.453] | 0.518 | [0.451, 0.573] | 127 | 3.983 | [2.701, 5.176] | 125 | 1.006 | [-0.061, 2.076] |
| games 1-15 | 31-60 | 284 | 42 | 11.556 | [10.603, 12.653] | 0.335 | [0.281, 0.399] | 124 | 1.493 | [0.064, 2.887] | 123 | -0.938 | [-1.698, -0.117] |
| games 1-15 | undrafted | 121 | 31 | 7.707 | [6.423, 9.055] | 0.140 | [0.085, 0.202] | 31 | 2.950 | [0.940, 5.326] | 31 | -0.628 | [-2.089, 0.909] |
| games 16-82 | 1-5 | 437 | 8 | 25.607 | [24.861, 26.382] | 0.874 | [0.843, 0.903] | 436 | 0.341 | [-0.264, 0.999] | 433 | 0.884 | [0.248, 1.513] |
| games 16-82 | 6-14 | 722 | 16 | 20.019 | [19.289, 20.657] | 0.723 | [0.689, 0.752] | 712 | 0.214 | [-0.327, 0.703] | 712 | -0.140 | [-0.520, 0.227] |
| games 16-82 | 15-30 | 1274 | 31 | 19.925 | [19.331, 20.505] | 0.665 | [0.639, 0.690] | 1250 | 0.875 | [0.427, 1.340] | 1250 | 0.105 | [-0.189, 0.413] |
| games 16-82 | 31-60 | 1346 | 51 | 15.310 | [14.712, 15.904] | 0.494 | [0.465, 0.520] | 1253 | 1.275 | [0.811, 1.680] | 1250 | -0.195 | [-0.447, 0.074] |
| games 16-82 | undrafted | 984 | 81 | 12.251 | [11.456, 13.072] | 0.343 | [0.308, 0.383] | 700 | 1.537 | [0.891, 2.164] | 698 | -0.932 | [-1.269, -0.582] |

Reading: Draft slot spreads minutes more than team strength does: picks 1-5 play 25.4 min early (88% >=15) vs undrafted 7.7 (14%); the gradient holds in games 16-82 (25.6 vs 12.3).

### C4. Does the lottery split explain the cold-start error? (rookie rows with 5 <= n_prior <= 19, production model rows)

| measure | tercile | n | mean | ci | eta2_between_terciles | eta2_ci |
|---|---|---|---|---|---|---|
| pts residual (model) | all | 1854 | -0.181 | [-0.414, 0.031] |  |  |
| pts residual (model) | T1 weak/lottery-ish | 750 | -0.176 | [-0.578, 0.186] | 0.001 | [0.000, 0.006] |
| pts residual (model) | T2 middle | 543 | -0.032 | [-0.389, 0.377] |  |  |
| pts residual (model) | T3 contender | 561 | -0.331 | [-0.698, 0.081] |  |  |
| minutes residual (vs prior-10) | all | 1854 | 1.621 | [1.228, 1.970] |  |  |
| minutes residual (vs prior-10) | T1 weak/lottery-ish | 750 | 1.498 | [0.920, 1.987] | 0.000 | [0.000, 0.005] |
| minutes residual (vs prior-10) | T2 middle | 543 | 1.608 | [1.016, 2.241] |  |  |
| minutes residual (vs prior-10) | T3 contender | 561 | 1.798 | [1.186, 2.382] |  |  |

Reading: No: on rows the model scores (n=1854), team-strength tercile explains about 0.1% of the pts-residual variance (eta2 0.001, CI 0 to 0.006) and none of the minutes residual (0.000); cold-start rookies are out-played their prior-10 by +1.6 min (CI 1.2 to 2.0) in every tercile.

## D. A cold start's effect should come from players in his situation: three minutes priors

Targets: rookie-class players (first NBA season 2023 or 2024) in games with 5 <= n_prior <= 19 (production model rows, true prior-game count), regular season, with prior-10 minutes and starter share defined: 2100 rows (0 model rows dropped for missing prior-10 inputs), 173 players. Rows with n_prior < 5 have no recency baseline and are not scored. Peers (strictly earlier): 2022-23 rookies (first NBA season 2022) in their own games 6-20 (5 <= n_prior <= 19): 807 rows, 65 players. 2022-class players are excluded from targets so nobody is their own peer. Both target seasons use the same 2022-23 pool (instruction), so 2024 targets do not use 2023 cold starts.

Priors for minutes in the game: (1) league prior by role = mean minutes of ALL players in 2022-23 in the same role (prior-10 starter share >= .5 = starter, else bench): starter 30.3, bench 17.4; (2) peer group = mean minutes of the 2022 rookie peers in the same team-strength tercile (prior-season win%; 2021-22 win% for 2022 teams) x draft band (1-14 / 15-60 / undrafted) x position (first token of the listed position), falling back to strength x band, then band, then all peers when a cell has < 30 rows or < 3 players; (2b) an added reference, the cold-start-only prior by role = the same 2022 rookie peers pooled by role only (no team, draft or position; starter 26.4 min n=108, bench 13.5 min n=699), to separate 'cold-start population' from 'situation'; (3) production recency baseline = production's min10 (decayed prior minutes) from model_rows; the plain prior-10 mean is shown too.

Peer fallback usage: full cell 1450, strength x band 383, band 267, all peers 0 of 2100 rows.

### D1. Minutes MAE of the three priors on identical rows

| prior | n | MAE_min | ci_mae | bias (actual - prior) | ci_bias | MAE minus production baseline | ci_paired |
|---|---|---|---|---|---|---|---|
| league prior by role | 2100 | 8.969 | [8.718, 9.247] | -4.544 | [-4.973, -4.110] | 3.268 | [2.897, 3.584] |
| cold-start-only prior by role (2022 rookie peers, no team/draft/position) | 2100 | 7.955 | [7.738, 8.161] | -0.643 | [-1.072, -0.208] | 2.254 | [2.009, 2.503] |
| peer group (strength x draft band x position) | 2100 | 8.123 | [7.876, 8.374] | -0.431 | [-0.907, 0.020] | 2.422 | [2.137, 2.679] |
| production recency baseline (min10) | 2100 | 5.701 | [5.489, 5.947] | 1.853 | [1.521, 2.188] | 0.000 | [0.000, 0.000] |
| plain prior-10 mean | 2100 | 5.733 | [5.521, 5.984] | 1.611 | [1.266, 1.934] | 0.032 | [-0.013, 0.072] |

Reading: The closest prior is the production recency baseline (MAE 5.70 min, n=2100): the situation peer group is 2.42 min worse (CI 2.14 to 2.68) and the generic league-role prior 3.27 worse (2.90 to 3.58); peers beat the generic league prior by 0.85 min (CI 0.58 to 1.12) but do no better than simply pooling 2022 rookies by role (peers 0.17 min worse, CI -0.03 to 0.37), so the extra team x draft x position detail adds nothing here.

### D2. Same comparison by slice (paired MAE difference vs the production baseline; negative = the prior is closer)

| slice | n | MAE league prior by role | MAE cold-start-only prior by role | MAE peer group | MAE production recency baseline | peer minus prod (CI) | league minus prod (CI) |
|---|---|---|---|---|---|---|---|
| season 2023 | 890 | 8.956 | 8.133 | 8.269 | 5.932 | 2.337 [1.868, 2.808] | 3.024 [2.544, 3.513] |
| season 2024 | 1210 | 8.978 | 7.825 | 8.016 | 5.532 | 2.485 [2.132, 2.819] | 3.447 [2.958, 3.841] |
| starter role | 269 | 7.452 | 6.313 | 8.176 | 5.073 | 3.103 [2.410, 3.842] | 2.379 [1.782, 3.061] |
| bench role | 1831 | 9.192 | 8.197 | 8.116 | 5.794 | 2.322 [2.015, 2.592] | 3.398 [3.008, 3.738] |
| T1 weak/lottery-ish | 855 | 7.769 | 7.622 | 7.816 | 5.913 | 1.903 [1.410, 2.369] | 1.856 [1.357, 2.323] |
| T2 middle | 588 | 9.562 | 8.237 | 8.811 | 5.764 | 3.047 [2.580, 3.496] | 3.799 [3.227, 4.432] |
| T3 contender | 657 | 9.999 | 8.137 | 7.908 | 5.370 | 2.537 [2.105, 2.894] | 4.629 [3.973, 5.245] |
| n_prior 5-9 | 788 | 9.050 | 7.923 | 7.986 | 5.671 | 2.315 [1.846, 2.724] | 3.379 [2.858, 3.933] |
| n_prior 10-19 | 1312 | 8.920 | 7.975 | 8.206 | 5.720 | 2.486 [2.187, 2.787] | 3.200 [2.803, 3.551] |

Reading: The recency baseline is closest in every slice (both seasons, both roles, all three strength terciles, both n_prior bands), by 1.7 to 4.6 min over the other priors; among the other priors the order varies (the peer group is worse than the league prior for starters, 8.2 vs 7.5, and the rookie-role pool is best for starters at 6.3).

Peer group minus cold-start-only prior by role (isolates team strength x draft band x position beyond role among rookies), paired MAE difference: 0.168 min [-0.027, 0.372] (n=2100); positive means the extra situation detail did not help.

Peer group minus league prior by role, paired MAE difference: -0.845 min [-1.120, -0.579] (n=2100); negative means the situation-matched peers beat the generic role prior. This is descriptive and is not the F9b rule (F9b was null).

## E. Whistles: home-minus-away free-throw rate and foul differential vs opponent strength

Per game: home team FTA/FGA minus away team FTA/FGA (team totals from the box score) and home PF minus away PF. n games = 2460 (2455 with an Elo p). Elo terciles are of the strength of the team named in the table (away team's pre-tip win probability 1-p cut at 0.334/0.549; home p cut at 0.451/0.666). T1 = weakest. The whistle hypothesis predicts a larger home FT-rate edge and a more negative PF differential (home fouls fewer) when the AWAY team is good.

Overall home-minus-away FTA/FGA: 0.0042 [-0.000, 0.008]; home-minus-away PF: -0.126 [-0.307, 0.066] (n=2460).

### E1. Home-minus-away whistle measures by strength tercile

| strength of | measure | mean [T1] | n [T1] | ci [T1] | mean [T2] | n [T2] | ci [T2] | mean [T3] | n [T3] | ci [T3] | T3 minus T1 | ci_T3-T1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AWAY team, Elo p | home-away FTA/FGA | 0.018 | 818 | [0.011, 0.026] | 0.009 | 819 | [0.002, 0.017] | -0.016 | 818 | [-0.023, -0.008] | -0.034 | [-0.044, -0.023] |
| AWAY team, Elo p | home-away PF | -0.903 | 818 | [-1.200, -0.572] | -0.346 | 819 | [-0.680, 0.013] | 0.867 | 818 | [0.577, 1.179] | 1.770 | [1.336, 2.184] |
| AWAY team, prior-season win% | home-away FTA/FGA | 0.022 | 822 | [0.014, 0.029] | -0.005 | 820 | [-0.012, 0.003] | -0.005 | 818 | [-0.012, 0.004] | -0.027 | [-0.037, -0.015] |
| AWAY team, prior-season win% | home-away PF | -0.788 | 822 | [-1.096, -0.423] | 0.167 | 820 | [-0.171, 0.474] | 0.246 | 818 | [-0.066, 0.575] | 1.034 | [0.540, 1.489] |
| HOME team (control), Elo p | home-away FTA/FGA | -0.016 | 818 | [-0.023, -0.008] | 0.009 | 819 | [0.002, 0.017] | 0.018 | 818 | [0.011, 0.026] | 0.034 | [0.023, 0.044] |
| HOME team (control), Elo p | home-away PF | 0.867 | 818 | [0.577, 1.179] | -0.346 | 819 | [-0.680, 0.013] | -0.903 | 818 | [-1.200, -0.572] | -1.770 | [-2.184, -1.336] |
| HOME team (control), prior-season win% | home-away FTA/FGA | -0.012 | 818 | [-0.020, -0.005] | 0.015 | 820 | [0.007, 0.022] | 0.010 | 822 | [0.003, 0.017] | 0.023 | [0.011, 0.033] |
| HOME team (control), prior-season win% | home-away PF | 0.749 | 818 | [0.398, 1.094] | -0.696 | 820 | [-1.034, -0.387] | -0.428 | 822 | [-0.738, -0.100] | -1.178 | [-1.629, -0.664] |

Reading: Opposite to the hypothesis: when the away team is strong the home FT-rate edge is smaller (-0.027 FTA/FGA, CI -0.037 to -0.015 by win%; -0.034 by Elo) and the home-minus-away PF differential moves from -0.8 (weak away team) to +0.2 (contender), a shift of +1.0 (CI 0.5 to 1.5); the home-strength rows show the mirror image, so this is a team-quality effect (strong teams draw more and foul less) and the Elo 'control' is the same games reversed.

### E2. Away tercile x home tercile (prior-season win%) to separate the two strengths

| measure | AWAY tercile (win%) | HOME T1: mean | HOME T1 n | HOME T2: mean | HOME T2 n | HOME T3: mean | HOME T3 n |
|---|---|---|---|---|---|---|---|
| home-away FTA/FGA | T1 weak/lottery-ish | 0.006 | 251 | 0.032 | 285 | 0.026 | 286 |
| home-away FTA/FGA | T2 middle | -0.022 | 289 | 0.007 | 253 | 0.003 | 278 |
| home-away FTA/FGA | T3 contender | -0.019 | 278 | 0.004 | 282 | 0.001 | 258 |
| home-away PF | T1 weak/lottery-ish | 0.386 | 251 | -1.589 | 285 | -1.021 | 286 |
| home-away PF | T2 middle | 0.976 | 289 | -0.577 | 253 | 0.004 | 278 |
| home-away PF | T3 contender | 0.842 | 278 | 0.099 | 282 | -0.236 | 258 |

Reading: Holding home strength fixed, a stronger away team lowers the home edge in FT rate (e.g. home T2: +0.032 vs weak away, +0.004 vs contender away); no cell supports a friendlier whistle for the home team against good visitors.

### E2b. Stratified contrasts (prior-season win% terciles), one team's strength with the other's held fixed

The hypothesis predicts the first contrast is POSITIVE for FTA/FGA (bigger home edge vs good away teams) and NEGATIVE for PF. A pure 'good teams draw more fouls and commit fewer' effect predicts the two contrasts have opposite signs (home strength raises home edge, away strength lowers it). Note the Elo-p tables in E1 are exact mirrors of each other (away p = 1 - home p, so away T3 is the same games as home T1); only the win% tables carry two separate strengths.

| measure | contrast | diff | ci | n_hi | n_lo |
|---|---|---|---|---|---|
| home-away FTA/FGA | AWAY T3 minus T1, within home tercile (equal-weight avg) | -0.026 | [-0.037, -0.015] | 818 | 822 |
| home-away FTA/FGA | HOME T3 minus T1, within away tercile (equal-weight avg) | 0.022 | [0.010, 0.032] | 822 | 818 |
| home-away PF | AWAY T3 minus T1, within home tercile (equal-weight avg) | 0.976 | [0.505, 1.431] | 818 | 822 |
| home-away PF | HOME T3 minus T1, within away tercile (equal-weight avg) | -1.153 | [-1.610, -0.649] | 822 | 818 |

Reading: Stratified: away T3 minus T1 within home tercile is -0.026 FTA/FGA (CI -0.037 to -0.015) and +0.98 PF (0.51 to 1.43); home T3 minus T1 within away tercile is +0.022 and -1.15: equal and opposite, i.e. quality of the team, not a venue-specific favour.

### E3. Model pts residual (actual - OOF mean; positive = we were low) by the relevant team's strength

Elo-p rows for 'HOME vs HOME-team strength (control)' are the same games as 'HOME vs AWAY-team strength' with the terciles reversed, so they are a mirror, not an independent control; the win% rows differ.

| rows | strength by | resid [T1] | n [T1] | ci [T1] | resid [T2] | n [T2] | ci [T2] | resid [T3] | n [T3] | ci [T3] | T3 minus T1 | ci_T3-T1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HOME players vs AWAY-team strength | Elo p | 0.017 | 8874 | [-0.091, 0.116] | -0.094 | 8554 | [-0.194, 0.009] | -0.192 | 8417 | [-0.315, -0.087] | -0.210 | [-0.367, -0.062] |
| HOME players vs AWAY-team strength | prior-season win% | -0.031 | 8728 | [-0.143, 0.081] | -0.096 | 8532 | [-0.201, 0.008] | -0.137 | 8585 | [-0.247, -0.029] | -0.106 | [-0.274, 0.052] |
| AWAY players vs HOME-team strength (mirror) | Elo p | -0.095 | 8691 | [-0.186, -0.010] | -0.082 | 8483 | [-0.193, 0.027] | -0.287 | 8445 | [-0.408, -0.173] | -0.192 | [-0.339, -0.025] |
| AWAY players vs HOME-team strength (mirror) | prior-season win% | -0.094 | 8540 | [-0.200, 0.003] | -0.201 | 8559 | [-0.310, -0.096] | -0.167 | 8520 | [-0.280, -0.059] | -0.072 | [-0.225, 0.068] |
| HOME players vs HOME-team strength (control) | Elo p | -0.192 | 8417 | [-0.315, -0.087] | -0.094 | 8554 | [-0.194, 0.009] | 0.017 | 8874 | [-0.091, 0.116] | 0.210 | [0.062, 0.367] |
| HOME players vs HOME-team strength (control) | prior-season win% | -0.023 | 8569 | [-0.136, 0.083] | -0.148 | 8630 | [-0.252, -0.050] | -0.091 | 8646 | [-0.201, 0.022] | -0.068 | [-0.213, 0.089] |

Reading: The model's pts residual for home players does not rise with away strength: it falls (T3 minus T1 -0.21 pts by Elo, CI -0.37 to -0.06; -0.11, CI -0.27 to 0.05 by win%), and the mirror rows for away players look the same, so it is a general under-adjustment for strong opponents, not a home-whistle signature.

### E3b. Same residual contrast with the other team's strength held fixed

| contrast (win% terciles, pts residual) | diff | ci | n_hi | n_lo |
|---|---|---|---|---|
| HOME players: AWAY T3 minus T1 within home tercile | -0.109 | [-0.275, 0.049] | 8585 | 8728 |
| AWAY players: HOME T3 minus T1 within away tercile | -0.072 | [-0.224, 0.066] | 8520 | 8540 |

Reading: With the other team's strength held fixed, home players' residual vs away strength is -0.11 pts (CI -0.28 to 0.05, n=8585 vs 8728), no rise; away players -0.07 (CI -0.22 to 0.07).

## Scope and caveats

- Everything above is descriptive; no cell has a frozen rule, floor or multiplicity correction. A pattern is, at most, a question for a pre-registration.
- A: sit-outs include injuries and personal reasons; the data cannot separate 'load management' from injury. Star status is as-of the previous game; stars who missed the previous game are not opportunities, so long injuries are excluded but so are stars returning from a rest game.
- B: statuses come from the official report only; the post-9am/17:00 snapshot structure (11:00 and 17:00) limits how fine 'late' can be. Team of a non-playing listed player is inferred from box-score history.
- C/D: rookies are the cold-start group with a known draft slot; model residuals exist only for n_prior >= 5, so each rookie's first five appearances are never scored by the production model. The peer pool is 2022-23 rookies only (small).
- E: foul and FT rates depend on team style as well as officiating; terciles of strength are correlated with style. The residual table is a model-miss description, not a causal estimate.
