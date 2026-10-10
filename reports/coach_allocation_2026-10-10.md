# Coach allocation: profiles, absorption clusters, and what the coach alone explains (DESCRIPTIVE ONLY)

**DESCRIPTIVE ONLY. No rule, no claim, no promotion, no model fit beyond k-means.** Seasons 2022, 2023, 2024 only (season label = start year; season 2025, the frozen holdout, is never loaded: every SQL carries `season <= 2024` and the script asserts it). Regular season only (`game_id` starting `002`). nba.duckdb read-only. CIs (absence-handling shares and the log-loss difference only) are 95% percentile bootstraps clustered by game_id, 300 resamples, seed 2026; no multiplicity correction, so with ~60 coach-seasons expect a few spurious differences. Script: `data/scratch/coach_allocation_2026-10-10.py` (data construction through the absorption cases is copied from `role_clusters_2026-10-10.py`, which copies Q9 of `model_miss_questions_2026-10-10.py`).

## Definitions

- **Coach**. The DB table `team_coaches` is NOT used: it lists one 'Head Coach' per team-season, and it names a coach who did not coach that season for 14 of 87 team-seasons (compared with the coach with the most games that season; e.g. 2022-23 Milwaukee is 'Adrian Griffin', 2022-23 Houston 'Ime Udoka', 2022-23 Toronto 'Darko Rajakovic'; some team-seasons are missing). The coach of every team-game was instead rebuilt from Basketball-Reference's per-season coaches pages (free; fetched 2026-10-10 through a summarising fetcher, so I could not verify cell values beyond every team-season summing to 82 games), assigned by regular-season team game number in the listed chronological order, so mid-season changes (BKN, MIL, WAS, ATL, DEN, MEM, SAC, SAS...) are handled; 5 regular-season games missing from `games` could shift an in-season cutoff by a game or so. A coach's profile uses only the games he coached. Keep rule: coach-seasons with >= 40 regular-season games.
- **Rotation depth**: mean count of players with >= 10 min / >= 1 min per team-game (box score).
- **Starters' share**: minutes of players flagged `starter` in the box score / team minutes. **Top-7 share**: the 7 largest minute totals / team minutes. **Bench Gini**: Gini coefficient of the minutes of non-starters who played, per game, averaged (0 = bench minutes evenly spread, higher = concentrated on one or two men).
- **First substitution**: game seconds (regulation clock, quarter = 720 s) of the team's first change in the on-court five, from the rebuilt `stints` table (team-games whose stint durations do not sum to the game length +-5 s are skipped). **Starter first stint**: mean over the five opening players of the seconds until each first leaves the floor (continuous across quarter breaks; a player never subbed out is credited the game length).
- **Blowout / close**: starter minutes = mean minutes of box-score starters in that team-game; blowout = final |margin| >= 20, close = < 10 (post-game margins, labels only). Needs >= 3 such games to report.
- **Close-game top-5 share**: in games with final |margin| <= 5, the share of player-seconds in the last 6 minutes of the 4th quarter (stints in period 4 with clock <= 360) played by the 5 players with the largest prior-10 mean minutes among players who appeared in the game. Overtime excluded. Post-game selection of the game set (margin known after the fact), as asked.
- **Consistency**: for rotation players (prior-10 mean minutes >= 15, played tonight), sum |minutes - prior-10 mean| / sum prior-10 mean, per coach-season (DNPs are not in the denominator: it measures how much minutes move for a player who plays).
- **Absorption cases** (identical to the 1,651-case set of the role-cluster note, restricted to the regular season and to teams with a listed head coach): team-games with exactly one OUT player (T-60 report) whose prior-10 mean minutes >= 24; teammates are those who played with a prior-10 mean and a k=8 role (roles fit on 2023, as in the role-cluster note; names from `role_names_2026-10-10.json`); at least 3 such teammates. 1651 cases before the regular-season/coach filter, 1567 after. gain = minutes - prior-10 mean. vacated = the OUT player's prior-10 mean minutes. **Share to starters** = sum of gains of teammates whose prior-10 starter share >= 0.5, / vacated; **share to bench** = same for the others (these are NET gains, so a shuffle that costs one starter minutes lowers the starter share; the two add to net gain / vacated, which can differ from 1). **Largest gainer share** = max gain / vacated. **Concentration** = fraction of the coach-season's cases in which the largest gainer is that coach-season's most frequent largest gainer (with m rotation teammates the chance level is about 1/m ~ 0.1-0.15; n_distinct_gainers shown). **Same role** = largest gainer's k=8 role equals the OUT player's role (chance ~ 0.11).

## Part 1. Coach-season profiles

90 coach-seasons with >= 40 regular-season games (n_games is the number of team-games used). Closing games (|margin| < 10) n and blowouts (>= 20) n shown so thin cells are visible. No CIs on these (descriptive spread); the CI-carrying shares are in the absence table.

| coach | season | n_games | n>=10 | n>=1 | starter_share | top7 | bench_gini | 1st_sub_s | 1st_stint_s | blow20_n | starter_min_blow20 | close_n | starter_min_lt10 | close5_games | top5_last6 | consistency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Billy Donovan | 2022 | 82 | 8.878 | 10.183 | 0.662 | 0.858 | 0.255 | 373.037 | 489.432 | 15 | 27.568 | 39 | 33.828 | 20 | 0.769 | 0.159 |
| Chauncey Billups | 2022 | 82 | 8.768 | 9.976 | 0.668 | 0.862 | 0.228 | 335.756 | 471.532 | 14 | 28.336 | 36 | 34.022 | 18 | 0.784 | 0.199 |
| Chris Finch | 2022 | 82 | 8.756 | 10.439 | 0.661 | 0.856 | 0.266 | 362.671 | 491.902 | 10 | 27.184 | 51 | 32.743 | 25 | 0.827 | 0.173 |
| Darvin Ham | 2022 | 82 | 9.000 | 10.341 | 0.621 | 0.845 | 0.275 | 322.659 | 446.739 | 4 | 25.637 | 44 | 30.964 | 23 | 0.728 | 0.183 |
| Doc Rivers | 2022 | 82 | 8.878 | 10.720 | 0.656 | 0.849 | 0.285 | 375.780 | 513.846 | 14 | 27.718 | 45 | 33.470 | 23 | 0.783 | 0.189 |
| Dwane Casey | 2022 | 82 | 9.232 | 10.098 | 0.610 | 0.825 | 0.187 | 366.427 | 509.635 | 21 | 28.136 | 39 | 29.708 | 13 | 0.634 | 0.193 |
| Erik Spoelstra | 2022 | 82 | 8.427 | 9.366 | 0.661 | 0.888 | 0.247 | 374.207 | 498.278 | 9 | 28.404 | 57 | 32.764 | 38 | 0.778 | 0.176 |
| Gregg Popovich | 2022 | 82 | 9.585 | 10.793 | 0.586 | 0.794 | 0.205 | 322.756 | 424.768 | 22 | 26.310 | 34 | 29.417 | 11 | 0.743 | 0.190 |
| J.B. Bickerstaff | 2022 | 82 | 8.451 | 10.024 | 0.668 | 0.875 | 0.274 | 347.476 | 469.333 | 12 | 27.535 | 38 | 34.182 | 19 | 0.769 | 0.197 |
| Jacque Vaughn | 2022 | 75 | 8.627 | 10.627 | 0.651 | 0.855 | 0.290 | 350.240 | 503.948 | 11 | 26.394 | 39 | 32.149 | 23 | 0.708 | 0.198 |
| Jamahl Mosley | 2022 | 82 | 9.305 | 10.280 | 0.621 | 0.827 | 0.210 | 340.561 | 488.898 | 10 | 26.475 | 42 | 31.360 | 21 | 0.759 | 0.172 |
| Jason Kidd | 2022 | 82 | 8.671 | 10.500 | 0.634 | 0.858 | 0.284 | 345.146 | 503.184 | 10 | 27.532 | 59 | 31.580 | 32 | 0.738 | 0.189 |
| Joe Mazzulla | 2022 | 82 | 8.500 | 10.329 | 0.662 | 0.869 | 0.299 | 344.524 | 491.037 | 14 | 28.575 | 40 | 34.019 | 24 | 0.833 | 0.194 |
| Mark Daigneault | 2022 | 82 | 9.500 | 10.927 | 0.612 | 0.813 | 0.231 | 388.585 | 503.174 | 9 | 26.646 | 46 | 30.574 | 24 | 0.760 | 0.201 |
| Michael Malone | 2022 | 82 | 8.890 | 10.890 | 0.643 | 0.841 | 0.292 | 366.683 | 497.168 | 16 | 27.650 | 38 | 32.243 | 23 | 0.803 | 0.179 |
| Mike Brown | 2022 | 82 | 9.061 | 11.280 | 0.650 | 0.835 | 0.306 | 339.939 | 463.132 | 14 | 27.837 | 50 | 32.679 | 25 | 0.809 | 0.166 |
| Mike Budenholzer | 2022 | 82 | 9.329 | 10.878 | 0.614 | 0.819 | 0.259 | 288.366 | 404.403 | 16 | 26.045 | 37 | 31.447 | 12 | 0.687 | 0.189 |
| Monty Williams | 2022 | 82 | 9.244 | 10.927 | 0.632 | 0.826 | 0.237 | 388.171 | 518.890 | 16 | 27.046 | 37 | 31.882 | 23 | 0.795 | 0.215 |
| Nate McMillan | 2022 | 59 | 8.712 | 10.424 | 0.664 | 0.861 | 0.271 | 332.695 | 483.673 | 9 | 27.030 | 33 | 33.719 | 15 | 0.813 | 0.167 |
| Nick Nurse | 2022 | 82 | 8.293 | 10.159 | 0.699 | 0.885 | 0.269 | 388.744 | 524.719 | 7 | 30.589 | 43 | 35.502 | 21 | 0.834 | 0.168 |
| Rick Carlisle | 2022 | 82 | 9.354 | 10.500 | 0.602 | 0.818 | 0.229 | 335.463 | 457.987 | 10 | 26.269 | 48 | 29.995 | 25 | 0.764 | 0.198 |
| Stephen Silas | 2022 | 82 | 9.207 | 10.756 | 0.652 | 0.835 | 0.244 | 416.512 | 530.564 | 17 | 29.590 | 40 | 33.067 | 17 | 0.812 | 0.150 |
| Steve Clifford | 2022 | 82 | 8.780 | 9.927 | 0.661 | 0.861 | 0.229 | 358.037 | 477.225 | 12 | 29.099 | 39 | 33.008 | 16 | 0.831 | 0.178 |
| Steve Kerr | 2022 | 82 | 9.049 | 10.305 | 0.638 | 0.843 | 0.246 | 343.524 | 475.494 | 12 | 26.877 | 42 | 32.610 | 18 | 0.803 | 0.172 |
| Taylor Jenkins | 2022 | 82 | 9.439 | 10.720 | 0.618 | 0.815 | 0.231 | 312.366 | 472.565 | 15 | 26.029 | 34 | 31.574 | 18 | 0.744 | 0.199 |
| Tom Thibodeau | 2022 | 82 | 8.793 | 9.878 | 0.667 | 0.867 | 0.247 | 398.317 | 555.730 | 13 | 29.037 | 46 | 34.184 | 22 | 0.805 | 0.184 |
| Tyronn Lue | 2022 | 82 | 9.049 | 10.451 | 0.610 | 0.829 | 0.246 | 399.341 | 523.659 | 13 | 25.163 | 41 | 31.317 | 18 | 0.720 | 0.203 |
| Wes Unseld | 2022 | 82 | 8.866 | 10.573 | 0.633 | 0.849 | 0.274 | 331.659 | 466.889 | 12 | 28.189 | 42 | 31.539 | 18 | 0.772 | 0.187 |
| Will Hardy | 2022 | 82 | 9.341 | 10.671 | 0.620 | 0.822 | 0.239 | 325.329 | 445.416 | 10 | 26.099 | 53 | 30.894 | 27 | 0.738 | 0.165 |
| Willie Green | 2022 | 82 | 9.061 | 10.329 | 0.638 | 0.839 | 0.223 | 342.585 | 483.231 | 14 | 28.058 | 37 | 32.086 | 17 | 0.690 | 0.198 |
| Adrian Griffin | 2023 | 43 | 9.023 | 10.814 | 0.643 | 0.841 | 0.295 | 317.140 | 464.044 | 6 | 27.511 | 25 | 32.373 | 12 | 0.748 | 0.179 |
| Billy Donovan | 2023 | 82 | 8.610 | 10.049 | 0.700 | 0.880 | 0.274 | 323.305 | 462.895 | 8 | 30.218 | 45 | 35.218 | 20 | 0.864 | 0.152 |
| Chauncey Billups | 2023 | 82 | 8.780 | 10.159 | 0.642 | 0.860 | 0.259 | 325.207 | 478.182 | 16 | 27.614 | 39 | 32.545 | 20 | 0.747 | 0.246 |
| Chris Finch | 2023 | 82 | 8.671 | 10.720 | 0.657 | 0.856 | 0.295 | 355.427 | 497.799 | 15 | 28.607 | 40 | 33.371 | 21 | 0.821 | 0.156 |
| Darko Rajakovic | 2023 | 82 | 8.963 | 10.427 | 0.650 | 0.849 | 0.249 | 348.500 | 491.206 | 19 | 28.124 | 42 | 32.992 | 20 | 0.781 | 0.161 |
| Darvin Ham | 2023 | 82 | 8.390 | 10.354 | 0.673 | 0.875 | 0.317 | 370.805 | 500.938 | 11 | 26.829 | 37 | 34.356 | 24 | 0.774 | 0.181 |
| Erik Spoelstra | 2023 | 82 | 8.659 | 9.951 | 0.625 | 0.868 | 0.277 | 375.793 | 505.930 | 10 | 27.406 | 46 | 31.227 | 19 | 0.788 | 0.192 |
| Frank Vogel | 2023 | 82 | 8.610 | 10.756 | 0.676 | 0.862 | 0.295 | 372.146 | 527.485 | 9 | 29.759 | 39 | 33.206 | 23 | 0.821 | 0.175 |
| Gregg Popovich | 2023 | 82 | 9.573 | 10.951 | 0.602 | 0.806 | 0.242 | 332.415 | 448.336 | 17 | 25.653 | 39 | 30.845 | 23 | 0.813 | 0.190 |
| Ime Udoka | 2023 | 82 | 8.878 | 10.659 | 0.667 | 0.848 | 0.260 | 363.732 | 503.246 | 15 | 27.219 | 42 | 33.880 | 21 | 0.758 | 0.199 |
| J.B. Bickerstaff | 2023 | 82 | 8.671 | 10.000 | 0.654 | 0.866 | 0.270 | 332.110 | 467.609 | 15 | 28.340 | 42 | 32.967 | 17 | 0.815 | 0.174 |
| Jacque Vaughn | 2023 | 54 | 8.907 | 10.074 | 0.637 | 0.849 | 0.241 | 344.185 | 472.268 | 11 | 26.251 | 26 | 32.121 | 16 | 0.737 | 0.196 |
| Jamahl Mosley | 2023 | 82 | 9.622 | 11.622 | 0.591 | 0.799 | 0.254 | 379.646 | 508.818 | 14 | 27.282 | 35 | 29.151 | 21 | 0.701 | 0.209 |
| Jason Kidd | 2023 | 82 | 8.878 | 11.134 | 0.623 | 0.837 | 0.319 | 281.671 | 428.730 | 18 | 27.017 | 30 | 31.562 | 15 | 0.803 | 0.198 |
| Joe Mazzulla | 2023 | 82 | 8.756 | 10.293 | 0.672 | 0.859 | 0.268 | 354.341 | 489.954 | 20 | 27.912 | 35 | 35.509 | 19 | 0.856 | 0.181 |
| Mark Daigneault | 2023 | 82 | 9.866 | 11.817 | 0.608 | 0.783 | 0.245 | 364.537 | 489.451 | 21 | 25.483 | 40 | 31.416 | 17 | 0.763 | 0.171 |
| Michael Malone | 2023 | 82 | 8.878 | 10.963 | 0.661 | 0.840 | 0.282 | 392.159 | 525.875 | 15 | 27.105 | 44 | 33.517 | 22 | 0.901 | 0.169 |
| Mike Brown | 2023 | 82 | 8.707 | 11.341 | 0.650 | 0.847 | 0.334 | 383.768 | 496.991 | 18 | 27.172 | 37 | 33.469 | 23 | 0.831 | 0.183 |
| Monty Williams | 2023 | 82 | 9.378 | 10.524 | 0.621 | 0.827 | 0.232 | 345.988 | 459.680 | 15 | 26.841 | 35 | 30.957 | 10 | 0.786 | 0.228 |
| Nick Nurse | 2023 | 82 | 9.012 | 11.000 | 0.644 | 0.837 | 0.283 | 352.695 | 482.666 | 14 | 27.454 | 37 | 33.335 | 17 | 0.787 | 0.205 |
| Quin Snyder | 2023 | 82 | 8.451 | 9.744 | 0.668 | 0.877 | 0.250 | 323.695 | 432.183 | 14 | 29.523 | 46 | 33.256 | 21 | 0.755 | 0.142 |
| Rick Carlisle | 2023 | 82 | 9.573 | 10.951 | 0.597 | 0.803 | 0.233 | 353.988 | 450.422 | 15 | 25.338 | 36 | 30.104 | 21 | 0.722 | 0.191 |
| Steve Clifford | 2023 | 82 | 8.573 | 10.183 | 0.660 | 0.864 | 0.258 | 357.171 | 494.265 | 20 | 28.808 | 34 | 32.857 | 13 | 0.793 | 0.177 |
| Steve Kerr | 2023 | 82 | 9.378 | 11.146 | 0.588 | 0.808 | 0.256 | 319.512 | 417.296 | 13 | 26.030 | 46 | 29.007 | 26 | 0.709 | 0.183 |
| Taylor Jenkins | 2023 | 82 | 8.939 | 9.829 | 0.630 | 0.847 | 0.203 | 355.927 | 493.045 | 18 | 27.910 | 40 | 31.314 | 13 | 0.660 | 0.227 |
| Tom Thibodeau | 2023 | 82 | 8.329 | 9.915 | 0.686 | 0.889 | 0.300 | 391.012 | 595.817 | 16 | 30.438 | 34 | 33.552 | 18 | 0.813 | 0.181 |
| Tyronn Lue | 2023 | 82 | 8.756 | 10.402 | 0.630 | 0.850 | 0.262 | 369.512 | 513.080 | 12 | 27.276 | 37 | 31.225 | 17 | 0.712 | 0.179 |
| Wes Unseld | 2023 | 43 | 9.419 | 11.628 | 0.595 | 0.799 | 0.287 | 349.419 | 490.776 | 10 | 25.931 | 20 | 30.380 | 10 | 0.741 | 0.186 |
| Will Hardy | 2023 | 82 | 9.476 | 10.732 | 0.580 | 0.806 | 0.236 | 330.024 | 451.407 | 18 | 25.169 | 34 | 29.761 | 17 | 0.705 | 0.210 |
| Willie Green | 2023 | 82 | 9.012 | 10.500 | 0.631 | 0.838 | 0.258 | 343.549 | 474.042 | 22 | 27.181 | 32 | 32.194 | 15 | 0.707 | 0.183 |
| Billy Donovan | 2024 | 82 | 9.293 | 11.134 | 0.637 | 0.826 | 0.271 | 286.585 | 432.879 | 17 | 27.304 | 33 | 32.055 | 17 | 0.812 | 0.161 |
| Brian Keefe | 2024 | 81 | 8.802 | 10.704 | 0.610 | 0.834 | 0.273 | 361.012 | 485.052 | 24 | 27.512 | 24 | 30.430 | 14 | 0.651 | 0.210 |
| Charles Lee | 2024 | 82 | 9.500 | 10.561 | 0.605 | 0.817 | 0.207 | 309.610 | 423.001 | 16 | 26.360 | 38 | 30.386 | 27 | 0.744 | 0.226 |
| Chauncey Billups | 2024 | 82 | 8.683 | 10.732 | 0.650 | 0.857 | 0.304 | 294.085 | 467.326 | 22 | 27.813 | 33 | 33.454 | 16 | 0.812 | 0.212 |
| Chris Finch | 2024 | 82 | 8.207 | 10.268 | 0.660 | 0.877 | 0.310 | 366.207 | 494.939 | 12 | 26.771 | 42 | 33.526 | 26 | 0.786 | 0.152 |
| Darko Rajakovic | 2024 | 82 | 9.439 | 10.451 | 0.620 | 0.817 | 0.193 | 310.146 | 430.412 | 18 | 28.231 | 38 | 30.802 | 21 | 0.667 | 0.191 |
| Doc Rivers | 2024 | 81 | 8.778 | 10.802 | 0.625 | 0.850 | 0.284 | 361.444 | 501.360 | 17 | 26.786 | 37 | 30.910 | 19 | 0.708 | 0.170 |
| Doug Christie | 2024 | 51 | 8.039 | 10.608 | 0.710 | 0.885 | 0.311 | 340.039 | 490.040 | 12 | 30.208 | 22 | 37.370 | 12 | 0.842 | 0.173 |
| Erik Spoelstra | 2024 | 81 | 8.704 | 10.012 | 0.617 | 0.860 | 0.268 | 364.889 | 501.755 | 15 | 27.871 | 42 | 31.185 | 25 | 0.760 | 0.209 |
| Ime Udoka | 2024 | 81 | 8.556 | 10.444 | 0.674 | 0.866 | 0.272 | 370.593 | 504.598 | 15 | 28.755 | 44 | 34.133 | 24 | 0.869 | 0.170 |
| J.B. Bickerstaff | 2024 | 82 | 9.537 | 10.695 | 0.610 | 0.815 | 0.225 | 330.756 | 469.814 | 13 | 26.363 | 42 | 30.454 | 21 | 0.735 | 0.180 |
| JJ Redick | 2024 | 82 | 8.756 | 10.305 | 0.665 | 0.862 | 0.257 | 333.988 | 481.809 | 20 | 29.601 | 39 | 33.317 | 21 | 0.795 | 0.176 |
| Jamahl Mosley | 2024 | 82 | 9.366 | 11.256 | 0.606 | 0.811 | 0.245 | 356.427 | 469.886 | 16 | 26.697 | 40 | 30.749 | 20 | 0.724 | 0.208 |
| Jason Kidd | 2024 | 82 | 9.000 | 10.549 | 0.610 | 0.838 | 0.274 | 282.720 | 415.764 | 17 | 25.493 | 41 | 31.150 | 23 | 0.755 | 0.209 |
| Joe Mazzulla | 2024 | 82 | 8.366 | 10.354 | 0.662 | 0.872 | 0.313 | 358.146 | 497.753 | 24 | 28.308 | 34 | 34.415 | 17 | 0.820 | 0.172 |
| Jordi Fernandez | 2024 | 82 | 9.500 | 10.110 | 0.595 | 0.812 | 0.174 | 306.622 | 416.598 | 14 | 26.179 | 43 | 29.950 | 27 | 0.729 | 0.183 |
| Kenny Atkinson | 2024 | 82 | 9.268 | 11.122 | 0.599 | 0.806 | 0.259 | 306.683 | 416.696 | 19 | 25.242 | 34 | 30.524 | 18 | 0.758 | 0.164 |
| Mark Daigneault | 2024 | 81 | 9.136 | 11.074 | 0.624 | 0.823 | 0.287 | 358.222 | 496.876 | 23 | 27.505 | 23 | 32.095 | 6 | 0.732 | 0.175 |
| Michael Malone | 2024 | 79 | 8.190 | 10.329 | 0.696 | 0.888 | 0.306 | 381.215 | 527.408 | 18 | 30.351 | 32 | 36.020 | 21 | 0.821 | 0.164 |
| Mike Budenholzer | 2024 | 82 | 9.012 | 10.659 | 0.623 | 0.841 | 0.269 | 325.500 | 484.078 | 12 | 26.182 | 43 | 31.293 | 19 | 0.711 | 0.190 |
| Mitch Johnson | 2024 | 75 | 9.307 | 11.040 | 0.605 | 0.817 | 0.263 | 343.987 | 446.966 | 12 | 26.353 | 31 | 30.563 | 20 | 0.700 | 0.173 |
| Nick Nurse | 2024 | 82 | 8.720 | 10.061 | 0.658 | 0.861 | 0.236 | 362.756 | 499.317 | 11 | 28.440 | 37 | 33.540 | 19 | 0.784 | 0.229 |
| Quin Snyder | 2024 | 81 | 9.049 | 9.815 | 0.613 | 0.847 | 0.203 | 322.667 | 421.899 | 13 | 28.458 | 40 | 30.190 | 24 | 0.766 | 0.159 |
| Rick Carlisle | 2024 | 80 | 9.137 | 10.988 | 0.633 | 0.827 | 0.273 | 340.775 | 455.423 | 10 | 26.742 | 40 | 31.835 | 19 | 0.785 | 0.174 |
| Steve Kerr | 2024 | 82 | 9.829 | 11.402 | 0.580 | 0.790 | 0.243 | 306.793 | 417.565 | 17 | 24.145 | 41 | 29.763 | 21 | 0.739 | 0.201 |
| Taylor Jenkins | 2024 | 73 | 9.877 | 11.247 | 0.565 | 0.785 | 0.254 | 278.767 | 382.755 | 18 | 25.728 | 31 | 28.209 | 22 | 0.641 | 0.204 |
| Tom Thibodeau | 2024 | 82 | 7.915 | 9.951 | 0.738 | 0.910 | 0.308 | 405.866 | 594.864 | 19 | 32.601 | 32 | 37.570 | 18 | 0.912 | 0.151 |
| Tyronn Lue | 2024 | 82 | 9.110 | 10.780 | 0.634 | 0.831 | 0.272 | 414.524 | 533.345 | 22 | 26.511 | 37 | 32.975 | 22 | 0.748 | 0.191 |
| Will Hardy | 2024 | 82 | 9.610 | 10.122 | 0.601 | 0.815 | 0.181 | 312.463 | 437.343 | 21 | 27.823 | 37 | 29.638 | 20 | 0.734 | 0.197 |
| Willie Green | 2024 | 82 | 9.073 | 10.061 | 0.628 | 0.836 | 0.206 | 328.366 | 474.783 | 17 | 27.743 | 38 | 31.433 | 22 | 0.732 | 0.196 |

League-wide spread (SD across coach-seasons) and mean:

| measure | mean | sd_across_coach_seasons | min | max | n_coach_seasons |
|---|---|---|---|---|---|
| players>=10min | 8.973 | 0.418 | 7.915 | 9.877 | 90 |
| players>=1min | 10.550 | 0.468 | 9.366 | 11.817 | 90 |
| starters' share of minutes | 0.637 | 0.032 | 0.565 | 0.738 | 90 |
| top-7 share | 0.842 | 0.026 | 0.783 | 0.910 | 90 |
| bench-minutes Gini | 0.259 | 0.033 | 0.174 | 0.334 | 90 |
| first sub (game s) | 347.209 | 30.084 | 278.767 | 416.512 | 90 |
| starter 1st stint (s) | 479.702 | 37.733 | 382.755 | 595.817 | 90 |
| starter min, margin>=20 | 27.450 | 1.433 | 24.145 | 32.601 | 90 |
| starter min, margin<10 | 32.179 | 1.794 | 28.209 | 37.570 | 90 |
| top-5 share, last 6 min, |m|<=5 | 0.767 | 0.054 | 0.634 | 0.912 | 90 |
| |min - prior10| / prior10 (rotation) | 0.186 | 0.020 | 0.142 | 0.246 | 90 |

### Absence handling (exactly-one-OUT cases; coach-seasons with >= 5 cases; game-clustered 95% CI)

Shares are of the vacated minutes (net gains). `modal` = concentration. Cases are team-games, so n per coach-season is small (median 24): the CIs are wide and that is the finding about how much one coach-season can say.

| coach | season | n_cases | to_starters | to_bench | largest_gainer | modal_gainer | distinct_gainers | modal_player | gainer_same_role |
|---|---|---|---|---|---|---|---|---|---|
| Adrian Griffin | 2023 | 24 | -0.07 [-0.33, 0.18] | -0.15 [-0.43, 0.07] | 0.35 [0.29, 0.40] | 0.21 [0.17, 0.40] | 11 | Malik Beasley | 0.50 [0.29, 0.71] |
| Billy Donovan | 2023 | 15 | 0.15 [-0.03, 0.33] | 0.21 [0.03, 0.41] | 0.31 [0.26, 0.38] | 0.27 [0.27, 0.53] | 6 | Ayo Dosunmu | 0.07 [0.00, 0.20] |
| Brian Keefe | 2023 | 11 | 0.09 [-0.27, 0.47] | 0.59 [0.23, 1.01] | 0.53 [0.42, 0.66] | 0.18 [0.18, 0.45] | 8 | Richaun Holmes | 0.09 [0.00, 0.27] |
| Chauncey Billups | 2023 | 40 | -0.09 [-0.32, 0.11] | 0.22 [0.01, 0.42] | 0.41 [0.36, 0.47] | 0.25 [0.15, 0.39] | 15 | Malcolm Brogdon | 0.17 [0.07, 0.30] |
| Chris Finch | 2023 | 21 | 0.10 [-0.07, 0.28] | 0.11 [-0.23, 0.42] | 0.29 [0.22, 0.37] | 0.19 [0.19, 0.38] | 8 | Jordan Mclaughlin | 0.29 [0.10, 0.48] |
| Darko Rajakovic | 2023 | 11 | 0.09 [-0.23, 0.39] | 0.36 [0.12, 0.62] | 0.41 [0.30, 0.53] | 0.36 [0.27, 0.64] | 7 | Thaddeus Young | 0.09 [0.00, 0.32] |
| Darvin Ham | 2023 | 51 | -0.17 [-0.32, 0.02] | -0.02 [-0.20, 0.14] | 0.32 [0.29, 0.36] | 0.16 [0.14, 0.27] | 12 | Max Christie | 0.22 [0.12, 0.33] |
| Doc Rivers | 2023 | 21 | -0.16 [-0.40, 0.07] | -0.11 [-0.40, 0.16] | 0.27 [0.20, 0.35] | 0.19 [0.19, 0.38] | 11 | Pat Connaughton | 0.19 [0.05, 0.33] |
| Erik Spoelstra | 2023 | 35 | -0.18 [-0.37, 0.06] | -0.01 [-0.17, 0.15] | 0.29 [0.25, 0.34] | 0.26 [0.20, 0.40] | 13 | Haywood Highsmith | 0.11 [0.03, 0.21] |
| Frank Vogel | 2023 | 27 | 0.03 [-0.18, 0.24] | -0.22 [-0.38, -0.03] | 0.28 [0.24, 0.32] | 0.11 [0.15, 0.30] | 14 | Grayson Allen | 0.15 [0.04, 0.30] |
| Gregg Popovich | 2023 | 15 | 0.13 [-0.21, 0.39] | 0.12 [-0.25, 0.42] | 0.34 [0.29, 0.39] | 0.27 [0.20, 0.47] | 8 | Zach Collins | 0.20 [0.00, 0.40] |
| Ime Udoka | 2023 | 23 | -0.29 [-0.63, 0.01] | -0.31 [-0.55, -0.06] | 0.33 [0.28, 0.39] | 0.22 [0.17, 0.39] | 10 | Jalen Green | 0.17 [0.04, 0.30] |
| J.B. Bickerstaff | 2023 | 16 | 0.11 [-0.16, 0.38] | -0.28 [-0.74, 0.13] | 0.31 [0.24, 0.38] | 0.12 [0.19, 0.44] | 10 | Georges Niang | 0.25 [0.06, 0.50] |
| Jacque Vaughn | 2023 | 32 | -0.15 [-0.45, 0.09] | -0.13 [-0.27, 0.03] | 0.32 [0.28, 0.37] | 0.22 [0.16, 0.38] | 12 | Dorian Finney Smith | 0.06 [0.00, 0.16] |
| Jamahl Mosley | 2023 | 21 | -0.24 [-0.60, 0.09] | 0.25 [-0.05, 0.56] | 0.42 [0.35, 0.49] | 0.43 [0.24, 0.62] | 8 | Anthony Black | 0.00 [0.00, 0.00] |
| Jason Kidd | 2023 | 18 | -0.30 [-0.64, -0.00] | 0.25 [-0.13, 0.63] | 0.37 [0.27, 0.45] | 0.17 [0.17, 0.39] | 12 | Josh Green | 0.00 [0.00, 0.00] |
| Joe Mazzulla | 2023 | 28 | -0.10 [-0.48, 0.23] | 0.22 [-0.05, 0.48] | 0.32 [0.27, 0.38] | 0.14 [0.14, 0.30] | 13 | Sam Hauser | 0.07 [0.00, 0.16] |
| Kevin Ollie | 2023 | 14 | 0.28 [-0.08, 0.69] | 0.23 [-0.08, 0.63] | 0.43 [0.35, 0.52] | 0.21 [0.21, 0.50] | 7 | Jalen Wilson | 0.14 [0.00, 0.29] |
| Mark Daigneault | 2023 | 10 | -0.15 [-0.64, 0.38] | 0.35 [0.10, 0.64] | 0.38 [0.31, 0.47] | 0.30 [0.20, 0.60] | 8 | Isaiah Joe | 0.00 [0.00, 0.00] |
| Michael Malone | 2023 | 17 | 0.05 [-0.19, 0.28] | 0.15 [-0.01, 0.33] | 0.32 [0.28, 0.36] | 0.24 [0.18, 0.47] | 9 | Reggie Jackson | 0.06 [0.00, 0.21] |
| Mike Brown | 2023 | 17 | -0.01 [-0.36, 0.28] | 0.11 [-0.10, 0.30] | 0.39 [0.32, 0.47] | 0.24 [0.18, 0.47] | 9 | Keon Ellis | 0.00 [0.00, 0.00] |
| Monty Williams | 2023 | 37 | -0.25 [-0.44, -0.03] | 0.34 [0.14, 0.55] | 0.40 [0.35, 0.45] | 0.19 [0.14, 0.32] | 15 | Marcus Sasser | 0.24 [0.12, 0.41] |
| Nick Nurse | 2023 | 27 | -0.20 [-0.49, 0.06] | 0.59 [0.30, 0.95] | 0.44 [0.37, 0.52] | 0.15 [0.15, 0.33] | 12 | Nicolas Batum | 0.19 [0.07, 0.33] |
| Quin Snyder | 2023 | 30 | -0.03 [-0.17, 0.10] | 0.02 [-0.12, 0.18] | 0.22 [0.19, 0.25] | 0.23 [0.17, 0.40] | 10 | Onyeka Okongwu | 0.17 [0.05, 0.30] |
| Rick Carlisle | 2023 | 36 | 0.02 [-0.16, 0.23] | -0.01 [-0.18, 0.17] | 0.35 [0.31, 0.40] | 0.19 [0.14, 0.33] | 14 | Andrew Nembhard | 0.00 [0.00, 0.00] |
| Steve Clifford | 2023 | 14 | 0.10 [-0.10, 0.30] | -0.19 [-0.40, 0.01] | 0.26 [0.21, 0.32] | 0.36 [0.29, 0.64] | 5 | Ish Smith | 0.14 [0.00, 0.36] |
| Steve Kerr | 2023 | 43 | -0.12 [-0.31, 0.05] | 0.09 [-0.06, 0.25] | 0.35 [0.29, 0.40] | 0.26 [0.17, 0.40] | 12 | Trayce Jackson Davis | 0.21 [0.09, 0.33] |
| Taylor Jenkins | 2023 | 16 | -0.36 [-0.61, -0.09] | 0.59 [0.27, 0.96] | 0.51 [0.42, 0.62] | 0.25 [0.19, 0.44] | 9 | Jaylen Nowell | 0.25 [0.06, 0.50] |
| Tom Thibodeau | 2023 | 30 | -0.11 [-0.31, 0.07] | 0.16 [0.00, 0.31] | 0.37 [0.30, 0.45] | 0.20 [0.17, 0.37] | 11 | Miles McBride | 0.27 [0.10, 0.43] |
| Tyronn Lue | 2023 | 18 | -0.11 [-0.42, 0.13] | 0.53 [0.24, 0.89] | 0.40 [0.31, 0.53] | 0.22 [0.17, 0.44] | 9 | Amir Coffey | 0.06 [0.00, 0.17] |
| Wes Unseld | 2023 | 5 | -0.51 [-1.42, 0.39] | 0.15 [-0.27, 0.70] | 0.46 [0.25, 0.69] | 0.40 [0.40, 0.80] | 4 | Marvin Bagley | 0.20 [0.00, 0.60] |
| Will Hardy | 2023 | 18 | 0.03 [-0.31, 0.32] | 0.39 [0.16, 0.66] | 0.40 [0.34, 0.46] | 0.22 [0.22, 0.44] | 8 | Ochai Agbaji | 0.11 [0.00, 0.28] |
| Willie Green | 2023 | 26 | 0.17 [0.03, 0.30] | 0.24 [0.02, 0.60] | 0.35 [0.30, 0.42] | 0.12 [0.15, 0.31] | 12 | Herb Jones | 0.00 [0.00, 0.00] |
| Billy Donovan | 2024 | 30 | 0.04 [-0.11, 0.19] | 0.21 [0.00, 0.42] | 0.37 [0.30, 0.46] | 0.20 [0.13, 0.35] | 15 | Matas Buzelis | 0.10 [0.00, 0.20] |
| Brian Keefe | 2024 | 42 | -0.17 [-0.37, 0.01] | -0.08 [-0.35, 0.16] | 0.33 [0.27, 0.37] | 0.17 [0.12, 0.27] | 16 | Justin Champagnie | 0.19 [0.07, 0.33] |
| Charles Lee | 2024 | 12 | -0.33 [-0.81, 0.15] | -0.02 [-0.32, 0.26] | 0.35 [0.26, 0.46] | 0.25 [0.25, 0.50] | 7 | Mark Williams | 0.00 [0.00, 0.00] |
| Chauncey Billups | 2024 | 36 | -0.33 [-0.69, 0.03] | -0.13 [-0.38, 0.14] | 0.34 [0.28, 0.39] | 0.17 [0.14, 0.31] | 13 | Kris Murray | 0.22 [0.10, 0.36] |
| Chris Finch | 2024 | 11 | -0.03 [-0.31, 0.27] | 0.10 [-0.30, 0.47] | 0.26 [0.19, 0.31] | 0.36 [0.27, 0.64] | 6 | Rob Dillingham | 0.27 [0.04, 0.59] |
| Darko Rajakovic | 2024 | 15 | -0.21 [-0.54, 0.08] | 0.25 [-0.10, 0.57] | 0.42 [0.34, 0.50] | 0.27 [0.20, 0.47] | 10 | Orlando Robinson | 0.00 [0.00, 0.00] |
| Doc Rivers | 2024 | 42 | -0.16 [-0.29, -0.03] | -0.14 [-0.30, 0.05] | 0.27 [0.23, 0.32] | 0.19 [0.17, 0.32] | 11 | AJ Green | 0.00 [0.00, 0.00] |
| Doug Christie | 2024 | 14 | 0.07 [-0.35, 0.46] | 0.40 [0.10, 0.66] | 0.41 [0.31, 0.50] | 0.36 [0.29, 0.64] | 6 | Trey Lyles | 0.00 [0.00, 0.00] |
| Erik Spoelstra | 2024 | 19 | -0.33 [-0.61, -0.06] | 0.02 [-0.31, 0.33] | 0.38 [0.31, 0.46] | 0.21 [0.16, 0.37] | 10 | Kelel Ware | 0.11 [0.00, 0.26] |
| Ime Udoka | 2024 | 30 | -0.03 [-0.21, 0.15] | -0.02 [-0.24, 0.22] | 0.29 [0.24, 0.35] | 0.17 [0.13, 0.35] | 13 | Steven Adams | 0.13 [0.03, 0.27] |
| J.B. Bickerstaff | 2024 | 59 | -0.04 [-0.16, 0.09] | -0.06 [-0.20, 0.06] | 0.29 [0.25, 0.33] | 0.17 [0.14, 0.25] | 13 | Tim Hardaway | 0.05 [0.00, 0.12] |
| JJ Redick | 2024 | 15 | 0.17 [-0.06, 0.44] | 0.12 [-0.14, 0.44] | 0.35 [0.28, 0.41] | 0.20 [0.20, 0.47] | 10 | Max Christie | 0.07 [0.00, 0.20] |
| Jamahl Mosley | 2024 | 32 | -0.11 [-0.32, 0.11] | -0.16 [-0.41, 0.05] | 0.35 [0.30, 0.41] | 0.12 [0.16, 0.28] | 11 | Caleb Houstan | 0.00 [0.00, 0.00] |
| Jason Kidd | 2024 | 26 | 0.03 [-0.20, 0.22] | 0.01 [-0.27, 0.28] | 0.33 [0.29, 0.38] | 0.19 [0.15, 0.35] | 12 | Olivier Maxence Prosper | 0.00 [0.00, 0.00] |
| Joe Mazzulla | 2024 | 26 | -0.12 [-0.49, 0.18] | 0.15 [-0.17, 0.43] | 0.38 [0.30, 0.45] | 0.19 [0.15, 0.38] | 10 | Payton Pritchard | 0.12 [0.00, 0.27] |
| Jordi Fernandez | 2024 | 34 | -0.15 [-0.34, 0.05] | -0.02 [-0.20, 0.17] | 0.29 [0.24, 0.33] | 0.18 [0.12, 0.32] | 14 | Jalen Wilson | 0.06 [0.00, 0.15] |
| Kenny Atkinson | 2024 | 37 | -0.11 [-0.24, 0.05] | 0.12 [-0.02, 0.28] | 0.25 [0.21, 0.30] | 0.16 [0.16, 0.31] | 11 | Georges Niang | 0.00 [0.00, 0.00] |
| Mark Daigneault | 2024 | 37 | -0.00 [-0.19, 0.19] | -0.13 [-0.34, 0.07] | 0.28 [0.24, 0.31] | 0.14 [0.14, 0.30] | 16 | Aaron Wiggins | 0.00 [0.00, 0.00] |
| Michael Malone | 2024 | 28 | 0.01 [-0.14, 0.17] | 0.53 [0.27, 0.79] | 0.45 [0.37, 0.52] | 0.29 [0.18, 0.45] | 10 | Peyton Watson | 0.11 [0.00, 0.25] |
| Mike Brown | 2024 | 5 | -0.40 [-0.79, 0.06] | 0.33 [-0.28, 0.75] | 0.41 [0.31, 0.50] | 0.40 [0.40, 0.80] | 4 | Isaac Jones | 0.00 [0.00, 0.00] |
| Mike Budenholzer | 2024 | 24 | -0.14 [-0.37, 0.10] | 0.05 [-0.21, 0.32] | 0.33 [0.26, 0.40] | 0.21 [0.17, 0.42] | 12 | Collin Gillespie | 0.00 [0.00, 0.00] |
| Mitch Johnson | 2024 | 22 | -0.06 [-0.25, 0.09] | 0.06 [-0.21, 0.31] | 0.31 [0.26, 0.37] | 0.23 [0.18, 0.41] | 10 | Stephon Castle | 0.14 [0.00, 0.27] |
| Nick Nurse | 2024 | 17 | -0.65 [-0.90, -0.42] | -0.04 [-0.27, 0.17] | 0.29 [0.24, 0.35] | 0.35 [0.24, 0.59] | 7 | Andre Drummond | 0.18 [0.00, 0.35] |
| Quin Snyder | 2024 | 33 | 0.04 [-0.08, 0.15] | -0.07 [-0.24, 0.08] | 0.30 [0.25, 0.35] | 0.18 [0.15, 0.30] | 15 | Vit Krejci | 0.03 [0.00, 0.09] |
| Rick Carlisle | 2024 | 32 | -0.04 [-0.23, 0.15] | 0.09 [-0.17, 0.44] | 0.31 [0.25, 0.37] | 0.25 [0.19, 0.41] | 9 | Jarace Walker | 0.06 [0.00, 0.19] |
| Steve Kerr | 2024 | 29 | -0.03 [-0.19, 0.14] | 0.17 [-0.04, 0.38] | 0.31 [0.27, 0.35] | 0.17 [0.14, 0.34] | 13 | Gui Santos | 0.10 [0.00, 0.24] |
| Taylor Jenkins | 2024 | 18 | -0.30 [-0.52, -0.10] | -0.27 [-0.54, -0.03] | 0.24 [0.20, 0.29] | 0.22 [0.17, 0.44] | 11 | Scotty Pippen | 0.11 [0.00, 0.28] |
| Tom Thibodeau | 2024 | 16 | -0.23 [-0.51, 0.02] | 0.43 [0.30, 0.59] | 0.33 [0.28, 0.38] | 0.38 [0.25, 0.62] | 7 | Miles McBride | 0.00 [0.00, 0.00] |
| Tyronn Lue | 2024 | 41 | -0.08 [-0.27, 0.09] | -0.10 [-0.24, 0.07] | 0.26 [0.22, 0.30] | 0.17 [0.15, 0.31] | 15 | Amir Coffey | 0.20 [0.07, 0.32] |
| Will Hardy | 2024 | 12 | -0.39 [-0.82, -0.07] | -0.54 [-0.77, -0.31] | 0.25 [0.20, 0.30] | 0.25 [0.25, 0.50] | 7 | Walker Kessler | 0.17 [0.00, 0.42] |

All cases pooled (n=1567): starters -0.097, bench 0.071, largest gainer 0.334, same role 0.116.

### Season-to-season stability (Pearson r across coaches present in both seasons)

Pairs are the same coach in consecutive seasons (2022->2023, 2023->2024), pooled r across both pairs and each pair separately; 95% CI by Fisher z (treats pairs as independent, which they are not quite: a coach appears in both pairs; and the same team often keeps the same roster, so a stable 'coach trait' may be a stable roster or team style, which this table cannot separate). Absence measures only exist for 2023 and 2024 (reports), restricted to coach-seasons with >= 5 cases in both seasons.

| measure | r 22->23 | n 22->23 | ci 22->23 | r 23->24 | n 23->24 | ci 23->24 | r pooled | n pooled | ci pooled |
|---|---|---|---|---|---|---|---|---|---|
| players>=10min | 0.643 | 25 | [0.33, 0.83] | 0.472 | 21 | [0.05, 0.75] | 0.548 | 46 | [0.31, 0.72] |
| players>=1min | 0.523 | 25 | [0.16, 0.76] | 0.365 | 21 | [-0.08, 0.69] | 0.409 | 46 | [0.13, 0.62] |
| starters' share of minutes | 0.599 | 25 | [0.27, 0.80] | 0.615 | 21 | [0.25, 0.83] | 0.608 | 46 | [0.39, 0.76] |
| top-7 share | 0.652 | 25 | [0.35, 0.83] | 0.498 | 21 | [0.09, 0.77] | 0.569 | 46 | [0.33, 0.74] |
| bench-minutes Gini | 0.631 | 25 | [0.31, 0.82] | 0.415 | 21 | [-0.02, 0.72] | 0.479 | 46 | [0.22, 0.68] |
| first sub (game s) | 0.305 | 25 | [-0.10, 0.62] | 0.786 | 21 | [0.54, 0.91] | 0.576 | 46 | [0.34, 0.74] |
| starter 1st stint (s) | 0.500 | 25 | [0.13, 0.75] | 0.801 | 21 | [0.56, 0.92] | 0.681 | 46 | [0.49, 0.81] |
| starter min, margin>=20 | 0.456 | 25 | [0.07, 0.72] | 0.408 | 21 | [-0.03, 0.71] | 0.432 | 46 | [0.16, 0.64] |
| starter min, margin<10 | 0.587 | 25 | [0.25, 0.80] | 0.559 | 21 | [0.17, 0.80] | 0.571 | 46 | [0.34, 0.74] |
| top-5 share, last 6 min, |m|<=5 | 0.532 | 25 | [0.17, 0.77] | 0.542 | 21 | [0.14, 0.79] | 0.528 | 46 | [0.28, 0.71] |
| |min - prior10| / prior10 (rotation) | 0.350 | 25 | [-0.05, 0.65] | 0.696 | 21 | [0.38, 0.87] | 0.535 | 46 | [0.29, 0.71] |
| ABS share to starters |  | 0 |  | 0.184 | 23 | [-0.25, 0.55] |  | 0 |  |
| ABS share to bench |  | 0 |  | -0.224 | 23 | [-0.58, 0.21] |  | 0 |  |
| ABS largest-gainer share |  | 0 |  | -0.094 | 23 | [-0.49, 0.33] |  | 0 |  |
| ABS concentration (modal gainer) |  | 0 |  | -0.241 | 23 | [-0.59, 0.19] |  | 0 |  |
| ABS gainer same role |  | 0 |  | 0.336 | 23 | [-0.09, 0.66] |  | 0 |  |

## Part 2. Absorption cases clustered by what the coach did

One row per exactly-one-OUT team-game, regular season, head coach known: 1567 cases (seasons 2023: 768, 2024: 799). Features (standardised, equal weight): share to starters; share to top bench man; share to largest gainer; players gaining >=5 min; bench man promoted to starter; gainer shares OUT role; OUT star (prior pts>=20); fresh absence; Elo win prob. KMeans(k=5, n_init=20, random_state=2026); clusters renumbered by size. Post-game blowout margin is NOT a clustering feature (read 'label only' literally): it is reported per cluster as a label. Elo win prob is the pre-tip injury-Elo probability for the team in question. Features computed from actual minutes (shares, n gaining, promotion, same role) describe what happened after the game; star, fresh and Elo are pre-tip. Fresh = the OUT player played his team's previous game; a stale (long-term) absence's absorption is already inside teammates' prior-10 means, which biases its shares down (see the Q9 note).

### Centroids (raw units)

| cluster | n | share_of_cases | sh_starters | sh_topbench | sh_largest | n_gain5 | promoted | same_role | star | fresh | elo | mean_vacated_min | mean_|margin|_label | blowout>=20_label |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 543 | 0.347 | -0.203 | -0.067 | 0.250 | 1.208 | 0.230 | 0.000 | 0.000 | 0.002 | 0.522 | 28.743 | 13.420 | 0.212 |
| 1 | 338 | 0.216 | 0.105 | 0.129 | 0.496 | 3.450 | 0.891 | 0.012 | 0.059 | 0.021 | 0.487 | 29.236 | 10.536 | 0.121 |
| 2 | 263 | 0.168 | -0.127 | 0.003 | 0.359 | 2.426 | 0.673 | 0.000 | 0.327 | 1.000 | 0.541 | 30.524 | 12.852 | 0.205 |
| 3 | 246 | 0.157 | -0.129 | -0.022 | 0.280 | 2.045 | 0.569 | 0.000 | 1.000 | 0.000 | 0.487 | 33.957 | 12.057 | 0.179 |
| 4 | 177 | 0.113 | -0.071 | -0.034 | 0.319 | 1.960 | 0.469 | 1.000 | 0.147 | 0.158 | 0.492 | 29.177 | 12.011 | 0.181 |

### Per-cluster description

#### Cluster 0 (n=543, 34.7% of cases)

- In words: The ordinary long-term absence. The OUT player is never a star and almost never a fresh absence (the new rotation is already in everyone's prior-10 means), nobody takes more than a quarter of the vacated minutes, about one man gains 5+ minutes, and starters lose minutes on net (blowouts are 21% of these). Nothing visible happens: the biggest cluster is the 'no story' cluster.

- Coach mix (top 5 by share of this cluster; in brackets the coach's share of ALL cases): J.B. Bickerstaff 7.4% (n=40; all-case share 4.8%); Quin Snyder 6.6% (n=36; all-case share 4.0%); Rick Carlisle 5.7% (n=31; all-case share 4.3%); Ime Udoka 5.5% (n=30; all-case share 3.4%); Mark Daigneault 5.3% (n=29; all-case share 3.0%)
- OUT-player role mix (top 4; in brackets the role's share of all cases): secondary scorer 26.2% (27.9%); starting 3-and-D wing 25.2% (20.0%); starting rim big 13.6% (12.9%); playmaker 13.4% (10.4%)
- Seasons: 2023 248, 2024 295. Vacated minutes mean 28.7. Post-game |margin| mean 13.4.
- Five named examples (the cases nearest the cluster centre, at most one per team). Each: date, team vs opponent, OUT player (prior-10 min), then the top 3 gainers as prior -> actual minutes (gain), pts actual vs forecast (forecast = production OOF pts mean on the integer grid; n/a where no model row):
  - 2024-11-23 DEN vs LAL, coach Michael Malone, OUT **Aaron Gordon** (32 min, starting 3-and-D wing); gainers: Peyton Watson 29 -> 35 (+6), pts 11 vs 9.1; Michael Porter 36 -> 36 (+0), pts 24 vs 16.7; Nikola Jokic 39 -> 37 (-2), pts 34 vs 26.6. Final margin +25.
  - 2025-01-22 HOU vs CLE, coach Ime Udoka, OUT **Jabari Smith** (33 min, starting 3-and-D wing); gainers: Steven Adams 12 -> 19 (+7), pts 3 vs 3.1; Fred Vanvleet 38 -> 40 (+2), pts 26 vs 15.8; Dillon Brooks 32 -> 32 (-0), pts 4 vs 12.6. Final margin +1.
  - 2023-12-05 LAL vs PHX, coach Darvin Ham, OUT **Gabe Vincent** (28 min, starting 3-and-D wing); gainers: Lebron James 33 -> 40 (+7), pts 31 vs 26.0; Anthony Davis 34 -> 39 (+5), pts 27 vs 23.7; Cam Reddish 27 -> 30 (+3), pts 4 vs 8.4. Final margin +3.
  - 2023-12-18 BKN vs UTA, coach Jacque Vaughn, OUT **Ben Simmons** (26 min, playmaker); gainers: Cameron Thomas 30 -> 37 (+7), pts 32 vs 19.7; Spencer Dinwiddie 34 -> 36 (+2), pts 17 vs 17.0; Mikal Bridges 35 -> 35 (+0), pts 13 vs 23.8. Final margin -17.
  - 2024-11-27 OKC vs GSW, coach Mark Daigneault, OUT **Chet Holmgren** (26 min, starting rim big); gainers: Cason Wallace 25 -> 33 (+8), pts 9 vs 6.6; Isaiah Joe 24 -> 31 (+7), pts 17 vs 8.7; Kenrich Williams 10 -> 15 (+5), pts 4 vs 4.6. Final margin +4.

#### Cluster 1 (n=338, 21.6% of cases)

- In words: The redraw. A bench man becomes a starter in 89% of these, the largest gainer takes about half the vacated minutes, three to four players gain 5+ minutes, and starters and the top bench man both gain on net. Mostly long-term, non-star absences. This is the cluster where a coach visibly rebuilds the rotation.

- Coach mix (top 5 by share of this cluster; in brackets the coach's share of ALL cases): Chauncey Billups 10.7% (n=36; all-case share 4.9%); J.B. Bickerstaff 5.6% (n=19; all-case share 4.8%); Michael Malone 5.3% (n=18; all-case share 2.9%); Brian Keefe 5.3% (n=18; all-case share 3.4%); Jamahl Mosley 4.7% (n=16; all-case share 3.4%)
- OUT-player role mix (top 4; in brackets the role's share of all cases): starting 3-and-D wing 26.3% (20.0%); secondary scorer 25.4% (27.9%); playmaker 13.9% (10.4%); starting rim big 13.6% (12.9%)
- Seasons: 2023 180, 2024 158. Vacated minutes mean 29.2. Post-game |margin| mean 10.5.
- Five named examples (the cases nearest the cluster centre, at most one per team). Each: date, team vs opponent, OUT player (prior-10 min), then the top 3 gainers as prior -> actual minutes (gain), pts actual vs forecast (forecast = production OOF pts mean on the integer grid; n/a where no model row):
  - 2023-11-17 PHI vs ATL, coach Nick Nurse, OUT **Kelly Oubre** (28 min, secondary scorer); gainers: Jaden Springer 8 -> 22 (+14), pts 8 vs 3.9; Danuel House 9 -> 18 (+9), pts 14 vs 4.0; Deanthony Melton 29 -> 36 (+7), pts 14 vs 10.5. Final margin +10.
  - 2024-12-28 ATL vs MIA, coach Quin Snyder, OUT **Onyeka Okongwu** (24 min, starting rim big); gainers: Garrison Mathews 13 -> 27 (+14), pts 18 vs 6.8; Vit Krejci 13 -> 23 (+11), pts 7 vs 3.3; Larry Nance 20 -> 27 (+6), pts 10 vs 7.6. Final margin +10.
  - 2025-02-28 POR vs BKN, coach Chauncey Billups, OUT **Deandre Ayton** (32 min, starting rim big); gainers: Kris Murray 11 -> 30 (+19), pts 11 vs 3.6; Dalano Banton 13 -> 25 (+11), pts 23 vs 6.7; Shaedon Sharpe 25 -> 36 (+11), pts 25 vs 15.4. Final margin +19.
  - 2025-03-21 WAS vs ORL, coach Brian Keefe, OUT **Bilal Coulibaly** (31 min, starting 3-and-D wing); gainers: Anthony Gill 8 -> 22 (+14), pts 10 vs 4.2; Tristan Vukcevic 14 -> 21 (+6), pts 17 vs 10.2; AJ Johnson 17 -> 23 (+6), pts 14 vs 6.7. Final margin -15.
  - 2025-02-08 NYK vs BOS, coach Tom Thibodeau, OUT **OG Anunoby** (36 min, starting 3-and-D wing); gainers: Miles McBride 21 -> 35 (+14), pts 8 vs 8.7; Cameron Payne 11 -> 19 (+8), pts 14 vs 6.5; Precious Achiuwa 26 -> 33 (+7), pts 3 vs 8.2. Final margin -27.

#### Cluster 2 (n=263, 16.8% of cases)

- In words: First night without him. Every case is a fresh absence (he played the previous game), a third are stars, a bench man is promoted two times in three, the largest gainer takes about a third and two to three men gain 5+; starters lose minutes on net (the bench absorbs it). The coach is improvising, and the Elo prior is slightly higher (0.54).

- Coach mix (top 5 by share of this cluster; in brackets the coach's share of ALL cases): Joe Mazzulla 12.2% (n=32; all-case share 3.4%); Steve Kerr 4.9% (n=13; all-case share 4.6%); Jason Kidd 4.6% (n=12; all-case share 2.8%); Rick Carlisle 4.6% (n=12; all-case share 4.3%); Mark Daigneault 4.2% (n=11; all-case share 3.0%)
- OUT-player role mix (top 4; in brackets the role's share of all cases): secondary scorer 26.2% (27.9%); high-usage star creator 24.3% (19.7%); starting 3-and-D wing 19.8% (20.0%); starting rim big 14.1% (12.9%)
- Seasons: 2023 131, 2024 132. Vacated minutes mean 30.5. Post-game |margin| mean 12.9.
- Five named examples (the cases nearest the cluster centre, at most one per team). Each: date, team vs opponent, OUT player (prior-10 min), then the top 3 gainers as prior -> actual minutes (gain), pts actual vs forecast (forecast = production OOF pts mean on the integer grid; n/a where no model row):
  - 2025-01-03 BOS vs HOU, coach Joe Mazzulla, OUT **Al Horford** (28 min, bench spot-up shooter); gainers: Luke Kornet 16 -> 24 (+8), pts 9 vs 4.4; Neemias Queta 12 -> 20 (+8), pts 5 vs 4.5; Payton Pritchard 29 -> 32 (+3), pts 20 vs 11.8. Final margin +23.
  - 2024-03-22 NOP vs MIA, coach Willie Green, OUT **Brandon Ingram** (31 min, high-usage star creator); gainers: Jose Alvarado 20 -> 31 (+11), pts 17 vs 7.1; Herb Jones 31 -> 39 (+8), pts 10 vs 11.2; CJ Mccollum 30 -> 35 (+5), pts 30 vs 17.3. Final margin +23.
  - 2025-02-28 LAL vs LAC, coach JJ Redick, OUT **Rui Hachimura** (33 min, starting 3-and-D wing); gainers: Trey Jemison 9 -> 22 (+13), pts 5 vs 2.6; Gabe Vincent 21 -> 26 (+5), pts 7 vs 6.4; Luka Doncic 31 -> 35 (+5), pts 31 vs 23.5. Final margin +4.
  - 2023-11-26 ATL vs BOS, coach Quin Snyder, OUT **Jalen (2001) Johnson** (31 min, secondary scorer); gainers: Deandre Hunter 30 -> 40 (+10), pts 24 vs 13.2; Bogdan Bogdanovic 27 -> 32 (+5), pts 23 vs 12.3; Onyeka Okongwu 22 -> 27 (+5), pts 4 vs 8.7. Final margin -10.
  - 2023-11-08 GSW vs DEN, coach Steve Kerr, OUT **Draymond Green** (27 min, playmaker); gainers: Dario Saric 17 -> 26 (+9), pts 4 vs 6.2; Kevon Looney 23 -> 30 (+7), pts 10 vs 6.6; Jonathan Kuminga 16 -> 19 (+3), pts 10 vs 8.5. Final margin -3.

#### Cluster 3 (n=246, 15.7% of cases)

- In words: The star is out (all cases, long-term, 34 vacated minutes on average). The minutes are spread: the largest gainer takes 28%, two men gain 5+, a bench man is promoted 57% of the time, and starters lose minutes on net. Role mix of the OUT player: 57% high-usage creators, 38% secondary scorers.

- Coach mix (top 5 by share of this cluster; in brackets the coach's share of ALL cases): Tyronn Lue 11.8% (n=29; all-case share 3.8%); Doc Rivers 11.0% (n=27; all-case share 4.0%); Steve Kerr 7.7% (n=19; all-case share 4.6%); Frank Vogel 6.1% (n=15; all-case share 1.7%); Jason Kidd 5.7% (n=14; all-case share 2.8%)
- OUT-player role mix (top 4; in brackets the role's share of all cases): high-usage star creator 56.5% (19.7%); secondary scorer 38.2% (27.9%); starting rim big 4.5% (12.9%); playmaker 0.8% (10.4%)
- Seasons: 2023 93, 2024 153. Vacated minutes mean 34.0. Post-game |margin| mean 12.1.
- Five named examples (the cases nearest the cluster centre, at most one per team). Each: date, team vs opponent, OUT player (prior-10 min), then the top 3 gainers as prior -> actual minutes (gain), pts actual vs forecast (forecast = production OOF pts mean on the integer grid; n/a where no model row):
  - 2023-11-16 GSW vs OKC, coach Steve Kerr, OUT **Stephen Curry** (33 min, high-usage star creator); gainers: Moses Moody 16 -> 23 (+7), pts 12 vs 7.3; Jonathan Kuminga 20 -> 27 (+7), pts 21 vs 10.0; Cory Joseph 17 -> 20 (+3), pts 3 vs 8.1. Final margin -19.
  - 2023-12-27 DAL vs CLE, coach Jason Kidd, OUT **Kyrie Irving** (30 min, secondary scorer); gainers: Seth Curry 17 -> 28 (+11), pts 19 vs 4.9; Derrick Jones 28 -> 36 (+8), pts 12 vs 9.4; Dante Exum 31 -> 33 (+2), pts 13 vs 11.5. Final margin -3.
  - 2023-11-04 SAC vs HOU, coach Mike Brown, OUT **De'Aaron Fox** (38 min, high-usage star creator); gainers: Davion Mitchell 19 -> 26 (+8), pts 7 vs 6.8; Chris Duarte 19 -> 26 (+7), pts 10 vs 7.6; Javale Mcgee 11 -> 15 (+3), pts 12 vs 6.4. Final margin -18.
  - 2024-11-13 LAC vs HOU, coach Tyronn Lue, OUT **Kawhi Leonard** (33 min, secondary scorer); gainers: Kris Dunn 22 -> 31 (+9), pts 11 vs 6.6; Jordan Miller 4 -> 12 (+8), pts 8 vs 1.8; Amir Coffey 23 -> 26 (+3), pts 7 vs 8.5. Final margin -8.
  - 2023-11-27 UTA vs NOP, coach Will Hardy, OUT **Lauri Markkanen** (35 min, secondary scorer); gainers: Omer Yurtseven 9 -> 22 (+13), pts 7 vs 5.7; Simone Fontecchio 16 -> 28 (+12), pts 14 vs 8.5; Keyonte George 28 -> 31 (+3), pts 19 vs 11.6. Final margin +2.

#### Cluster 4 (n=177, 11.3% of cases)

- In words: Replace in kind. The largest gainer is in the OUT player's role in every case (by construction this is what separates it), with a mid-sized share (32%), about two men gaining 5+, bench promotion 47%, mostly long-term, 15% stars. Role-matched replacement happens in 11% of the cases, the chance level is about 11%, so this cluster is a feature of the data, not evidence of a coach habit.

- Coach mix (top 5 by share of this cluster; in brackets the coach's share of ALL cases): Chauncey Billups 8.5% (n=15; all-case share 4.9%); Adrian Griffin 6.8% (n=12; all-case share 1.5%); Steve Kerr 6.8% (n=12; all-case share 4.6%); Darvin Ham 6.2% (n=11; all-case share 3.3%); Monty Williams 5.1% (n=9; all-case share 2.4%)
- OUT-player role mix (top 4; in brackets the role's share of all cases): secondary scorer 26.0% (27.9%); starting 3-and-D wing 20.3% (20.0%); starting rim big 19.2% (12.9%); playmaker 13.6% (10.4%)
- Seasons: 2023 116, 2024 61. Vacated minutes mean 29.2. Post-game |margin| mean 12.0.
- Five named examples (the cases nearest the cluster centre, at most one per team). Each: date, team vs opponent, OUT player (prior-10 min), then the top 3 gainers as prior -> actual minutes (gain), pts actual vs forecast (forecast = production OOF pts mean on the integer grid; n/a where no model row):
  - 2023-12-23 NYK vs MIL, coach Tom Thibodeau, OUT **Mitchell Robinson** (29 min, starting rim big); gainers: Isaiah Hartenstein 27 -> 33 (+6), pts 12 vs 6.3; Taj Gibson 10 -> 15 (+5), pts 2 vs 3.2; Donte Divincenzo 21 -> 24 (+3), pts 11 vs 8.4. Final margin -19.
  - 2025-01-17 SAS vs MEM, coach Mitch Johnson, OUT **Jeremy Sochan** (30 min, starting rim big); gainers: Charles Bassey 10 -> 17 (+7), pts 12 vs 4.3; Stephon Castle 22 -> 25 (+3), pts 20 vs 12.2; Julian Champagnie 20 -> 23 (+3), pts 4 vs 8.7. Final margin -28.
  - 2025-01-25 MIN vs DEN, coach Chris Finch, OUT **Donte Divincenzo** (31 min, playmaker); gainers: Rob Dillingham 12 -> 20 (+8), pts 10 vs 5.4; Josh Minott 7 -> 10 (+3), pts 5 vs 2.4; Leonard Miller 2 -> 4 (+2), pts 0 vs 1.2. Final margin +29.
  - 2025-02-20 POR vs LAL, coach Chauncey Billups, OUT **Deandre Ayton** (32 min, starting rim big); gainers: Robert Williams 17 -> 25 (+8), pts 8 vs 4.5; Deni Avdija 29 -> 34 (+5), pts 28 vs 15.9; Scoot Henderson 26 -> 30 (+4), pts 12 vs 12.6. Final margin -8.
  - 2025-04-10 ATL vs BKN, coach Quin Snyder, OUT **Jalen (2001) Johnson** (33 min, secondary scorer); gainers: Zaccharie Risacher 25 -> 35 (+9), pts 38 vs 14.9; Mouhamed Gueye 15 -> 23 (+8), pts 5 vs 6.8; Terance Mann 22 -> 25 (+3), pts 14 vs 9.5. Final margin +24.

### Coach x cluster shares (coaches with >= 15 cases, pooled over seasons; row = share of that coach's cases)

| coach | n_cases | cl0 | cl1 | cl2 | cl3 | cl4 |
|---|---|---|---|---|---|---|
| Adrian Griffin | 24 | 0.333 | 0.000 | 0.125 | 0.042 | 0.500 |
| Billy Donovan | 45 | 0.467 | 0.267 | 0.133 | 0.044 | 0.089 |
| Brian Keefe | 53 | 0.302 | 0.340 | 0.170 | 0.038 | 0.151 |
| Chauncey Billups | 76 | 0.276 | 0.474 | 0.039 | 0.013 | 0.197 |
| Chris Finch | 32 | 0.312 | 0.250 | 0.156 | 0.000 | 0.281 |
| Darko Rajakovic | 26 | 0.385 | 0.308 | 0.231 | 0.038 | 0.038 |
| Darvin Ham | 51 | 0.529 | 0.235 | 0.020 | 0.000 | 0.216 |
| Doc Rivers | 63 | 0.349 | 0.048 | 0.111 | 0.429 | 0.063 |
| Erik Spoelstra | 54 | 0.444 | 0.167 | 0.130 | 0.148 | 0.111 |
| Frank Vogel | 27 | 0.074 | 0.037 | 0.185 | 0.556 | 0.148 |
| Gregg Popovich | 16 | 0.312 | 0.000 | 0.438 | 0.062 | 0.188 |
| Ime Udoka | 53 | 0.566 | 0.151 | 0.132 | 0.000 | 0.151 |
| J.B. Bickerstaff | 75 | 0.533 | 0.253 | 0.053 | 0.080 | 0.080 |
| JJ Redick | 15 | 0.067 | 0.267 | 0.467 | 0.133 | 0.067 |
| Jacque Vaughn | 32 | 0.594 | 0.250 | 0.094 | 0.000 | 0.062 |
| Jamahl Mosley | 53 | 0.453 | 0.302 | 0.094 | 0.151 | 0.000 |
| Jason Kidd | 44 | 0.250 | 0.159 | 0.273 | 0.318 | 0.000 |
| Joe Mazzulla | 54 | 0.167 | 0.111 | 0.593 | 0.037 | 0.093 |
| Jordi Fernandez | 34 | 0.147 | 0.235 | 0.206 | 0.353 | 0.059 |
| Kenny Atkinson | 37 | 0.514 | 0.189 | 0.270 | 0.027 | 0.000 |
| Mark Daigneault | 47 | 0.617 | 0.106 | 0.234 | 0.043 | 0.000 |
| Michael Malone | 45 | 0.244 | 0.400 | 0.200 | 0.067 | 0.089 |
| Mike Brown | 22 | 0.364 | 0.273 | 0.227 | 0.136 | 0.000 |
| Mike Budenholzer | 24 | 0.292 | 0.167 | 0.208 | 0.333 | 0.000 |
| Mitch Johnson | 22 | 0.136 | 0.045 | 0.273 | 0.409 | 0.136 |
| Monty Williams | 37 | 0.216 | 0.324 | 0.108 | 0.108 | 0.243 |
| Nick Nurse | 44 | 0.182 | 0.227 | 0.114 | 0.295 | 0.182 |
| Quin Snyder | 63 | 0.571 | 0.175 | 0.111 | 0.048 | 0.095 |
| Rick Carlisle | 68 | 0.456 | 0.235 | 0.176 | 0.118 | 0.015 |
| Steve Kerr | 72 | 0.222 | 0.167 | 0.181 | 0.264 | 0.167 |
| Taylor Jenkins | 34 | 0.235 | 0.294 | 0.088 | 0.206 | 0.176 |
| Tom Thibodeau | 46 | 0.326 | 0.217 | 0.065 | 0.217 | 0.174 |
| Tyronn Lue | 59 | 0.085 | 0.119 | 0.153 | 0.492 | 0.153 |
| Will Hardy | 30 | 0.367 | 0.167 | 0.133 | 0.200 | 0.133 |
| Willie Green | 27 | 0.296 | 0.185 | 0.259 | 0.259 | 0.000 |
| ALL CASES | 1567 | 0.347 | 0.216 | 0.168 | 0.157 | 0.113 |

## Part 3. Is the cluster predictable from the coach alone? (numbers, then the reading)

Baseline = multinomial with the 2023 cluster shares for every case. Coach-conditional = the coach's own 2023 cluster counts shrunk toward the 2023 shares with a Dirichlet pseudo-count alpha (alpha = 10 is the stated choice, fixed before the run; other alphas shown only as sensitivity, not selected from); a coach with no 2023 cases gets the baseline. Scored by log loss (nats per case) on 2024 cases; clusters are the Part 2 clusters (fit on all cases, so cluster DEFINITIONS saw 2024, coach SHARES did not; a second block refits k-means on 2023 only and assigns 2024). Difference = baseline - coach-conditional (positive = coach helps); CI is game-clustered, B = 300; threshold for 'yes' stated in the brief: >= 0.02 nats per case.

| clusters | alpha | n_2024_cases | n_with_coach_2023 | logloss_baseline | logloss_coach | gain_all_cases | ci_lo | ci_hi | gain_cases_with_coach |
|---|---|---|---|---|---|---|---|---|---|
| clusters fit on all cases | 5 | 799 | 638 | 1.545 | 1.584 | -0.039 | -0.071 | -0.008 | -0.048 |
| clusters fit on all cases | 10 | 799 | 638 | 1.545 | 1.558 | -0.013 | -0.039 | 0.010 | -0.016 |
| clusters fit on all cases | 20 | 799 | 638 | 1.545 | 1.539 | 0.006 | -0.013 | 0.024 | 0.007 |
| clusters fit on all cases | 40 | 799 | 638 | 1.545 | 1.531 | 0.014 | 0.001 | 0.029 | 0.017 |
| clusters fit on 2023 only, 2024 assigned | 5 | 799 | 638 | 1.513 | 1.549 | -0.036 | -0.067 | -0.006 | -0.046 |
| clusters fit on 2023 only, 2024 assigned | 10 | 799 | 638 | 1.513 | 1.522 | -0.009 | -0.035 | 0.017 | -0.012 |
| clusters fit on 2023 only, 2024 assigned | 20 | 799 | 638 | 1.513 | 1.503 | 0.009 | -0.012 | 0.028 | 0.012 |
| clusters fit on 2023 only, 2024 assigned | 40 | 799 | 638 | 1.513 | 1.496 | 0.017 | 0.004 | 0.031 | 0.021 |

In-sample association (coaches with >= 15 cases, n=1504 cases, 35 coaches): likelihood-ratio G = 631.5 versus a permutation null (cluster labels shuffled within season, 300 shuffles) mean 152.4, 95th percentile 186.1, permutation p = 0.003. Fraction of the cluster-assignment entropy 'explained' in-sample (G / (2 n H)) = 0.137, of which a share is just overfitting (null mean/obs = 0.24).

## Reading (10 lines; descriptive, nothing here is a claim or a rule)

1. Data warning first: the DB `team_coaches` table names the wrong head coach in 14 of 87 team-seasons it covers (and misses 3), with no mid-season changes; every coach number here uses a rebuilt game-by-game assignment from Basketball-Reference pages that I could not independently verify beyond each team-season summing to 82 games. If Devin knows a firing date I have wrong, that row is fixable in one dictionary in the script.
2. Stable-looking coach traits (point estimate of year-to-year r >= 0.5, pooled over 2022->23 and 2023->24, n = 46 coach pairs): starter's first-stint length (r 0.68), starters' share of minutes (0.61), first substitution time (0.58), top-7 share (0.57), starters' minutes in close games (0.57), minutes consistency (0.54), players with >= 10 min (0.55), top-5 share in the last 6 minutes of close games (0.53). Fisher CIs are wide (lower ends 0.3-0.5), so 'about 0.5-0.7', not precise.
3. Not clearly traits: bench Gini (0.48), starter minutes in blowouts (0.43), players with >= 1 min (0.41). These sit under the line with CIs that cross 0.5; calling them noise would be too strong, calling them traits too generous.
4. Caveat for all of 2: year-to-year stability for a coach is also stability of his roster and his team's style (pace, star load). The table cannot separate coach from roster; only coaches who change teams could, and there are too few here.
5. The absence-handling measures look like noise at the coach level: r (2023->2024, 23 coaches with >= 5 cases both years) is 0.18 for share to starters, -0.22 to bench, -0.09 for largest-gainer share, -0.24 for concentration, 0.34 for same-role gainer (all CIs include 0). With ~24 cases per coach-season and 10-15 different largest gainers, a coach's 'absorption style' is not measurable from these data.
6. The spread across coaches is real but small in the allocation measures: first substitution runs from about 279 s (Jenkins 2024) to 417 s (Silas 2022); starters' share of minutes from 0.565 (Jenkins 2024) to 0.738 (Thibodeau 2024); consistency from 0.142 (Snyder 2023) to 0.246 (Billups 2023). Houston, for Devin: Udoka 2023/2024 first sub 364/371 s, starters' share 0.667/0.674 (league mean 0.637), top-7 share 0.848/0.866 (mean 0.842), so a starter-heavy, ordinary-depth rotation; his team's largest gainer takes about 30% of vacated minutes, in line with the league.
7. The five clusters are dominated by the pre-tip binary features (fresh absence, star out, gainer-in-same-role), not by the coach's share choices: clusters 2 (fresh), 3 (star), 4 (same role) are almost pure flags. Only the split of cluster 0 (quiet, spread-thin) from cluster 1 (a bench man is promoted and takes half the minutes) is about what the coach did, and 'promotion' is partly mechanical when a starter is out (someone must start).
8. Coach x cluster shares look dramatic (Mazzulla 59% fresh-night improvisation, Lue 49% and Vogel 56% star-out, Billups 47% redraw) and the in-sample association is large (G = 631 vs a permutation null of 152, p = 0.003), but those rows mostly reflect which absences each team happened to have (who was injured, how often, fresh vs long-term), and repeated absences of the same star are not independent cases, which the permutation ignores.
9. Predicting a 2024 case's cluster from the coach's 2023 shares: no. Coach-conditional minus baseline log loss = -0.013 nats/case (CI -0.039 to +0.010) at the stated alpha = 10, and -0.009 when the clusters are fit on 2023 only; the best of the sensitivity alphas (40) reaches only +0.014 to +0.017, under the 0.02 line. The cluster a given absence lands in is not carried over from one season to the next by the coach.
10. What this leaves for Devin: the allocation measures that persist (how long starters stay in, how deep the rotation goes, how tight the close-game five is) describe a coach in a way that could be a prior on minutes-in-a-normal-game; they say nothing yet about who takes the minutes when someone is out. Any next step is a new question with a new id and a pre-registered rule; this note proposes none.

