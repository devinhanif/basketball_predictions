# Referee crew experiment (docs/REFEREES.md, frozen)

Run date 2026-10-10. Module `research/eval/referees_eval.py`. Season 2025 was never loaded (SQL `season <= 2024`, asserted in memory); `nba.duckdb` opened `read_only=True`; no model was fitted before gate G0 was written and hashed.

## 1. Hash verification

| item | value |
|---|---|
| frozen section sha256 now (`awk ... | shasum -a 256`, reproduced in python) | `14613c3a48d788e9c8dff0af2539b55667acee0c45a9e1bfb8f539403728975f` |
| recorded in docs/TEST_LEDGER.md (REFEREES notice, 2026-10-09) | `14613c3a48d788e9c8dff0af2539b55667acee0c45a9e1bfb8f539403728975f` |
| constant in the module | `14613c3a48d788e9c8dff0af2539b55667acee0c45a9e1bfb8f539403728975f` |
| freeze commit | `3dc6b6aa9b81da09b6557cf589dc5a29ead8dfb4 2026-10-10 06:30:01 -0500` |
| match | **yes** |

## 2. Gate G0 (values vs thresholds)

`reports/referees/g0.json` sha256 `3324a7402f2c64479abcbf42d02a2eec7cc856f8e13d1fb85a35e4616850de1a` (logged in `g0.sha256` before any arm); missingness audit `reports/referees/missingness.json` sha256 `88d99c285efdc2fb0e340fc6281df72576189a675a6e7ba6d53f30b793118f56` (embedded in g0.json).

| item | value | threshold | result |
|---|---|---|---|
| 1a games in 2022-24 | 3953 | 3953 (doc) | matches |
| 1b share of games with >= 3 distinct non-NULL official_id | 1.0000 | >= 0.98 | PASS |
| 1c season 2022 (1320 games) | 1.0000 | >= 0.97 | PASS |
| 1c season 2023 (1318 games) | 1.0000 | >= 0.97 | PASS |
| 1c season 2024 (1315 games) | 1.0000 | >= 0.97 | PASS |
| 1d worst calendar month (2022-10) | 1.0000 | >= 0.95 | PASS |
| 1e share with 1 or 2 officials | 0.0000 | <= 0.01 | PASS |
| 2a ids carrying two names | 0 | 0 | PASS |
| 2b names carrying > 1 official_id | 0 | 0 unresolved by jersey | PASS (0 unresolved) |
| 3a pf and fta non-NULL, rows with minutes > 0 (n=84052) | 1.00000 | >= 0.995 | PASS |
| 3b same, rows with non-NULL minutes (n=84067) | 1.00000 | >= 0.995 | PASS |
| 4 A0 reproduces production pts CRPS 3.1528 / 3.1884 (tol 1e-4) | not evaluated | kill criterion at the first arm fit | n/a (no arm is fitted) |

**G0 PASSED**

## 3. Missingness audit (before any model)

Games 3953, without a usable crew (< 2 non-NULL official_id): **0**. Audit tests: not estimable: 0 games without a usable crew (need >= 20 and as many non-missing); the audit is vacuous, no STOP fires from it. Player-row shares by played/unplayed/starter are all 0.0000/0.0000/0.0000. Crew-column NULL pattern vs `minutes IS NULL`: 56 player rows with a NULL crew column (opening-day warm-up only), minutes-NULL rate 0.196 vs 0.190 (diff +0.006; STOP threshold 0.02). STOP from the audit: **no**.

## 4. Results

A0 pts integer CRPS {'2023': 3.152829990762183, '2024': 3.1884109851549947} (expected {'2023': 3.1528, '2024': 3.1884}, tol 0.0001): reproduced = True.

Delta = arm - control (negative is better), integer-support CRPS, game-clustered 95% bootstrap CI (B=2000, seed 0). BH over {pts, fg3m, total} per season; everything else is descriptive.

| test | season | contrast | n rows (games) | control CRPS | arm CRPS | dCRPS [CI] | p | covered-only dCRPS [CI] (n) | bias arm | cov80 arm |
|---|---|---|---|---|---|---|---|---|---|---|
| ast | 2023 | A1-A0 | 27619 (1318) | 0.9059 | 0.9065 | +0.0006 [-0.0003, +0.0015] | 0.164 | +0.0006 [-0.0003, +0.0015] (27619) | +0.031 | 0.789 |
| ast | 2024 | A1-A0 | 27583 (1315) | 0.9010 | 0.9009 | -0.0001 [-0.0008, +0.0006] | 0.737 | -0.0001 [-0.0008, +0.0006] (27583) | +0.035 | 0.780 |
| fg3m (confirmatory) | 2023 | A1-A0 | 27619 (1318) | 0.5690 | 0.5687 | -0.0004 [-0.0010, +0.0003] | 0.275 | -0.0004 [-0.0010, +0.0003] (27619) | +0.015 | 0.790 |
| fg3m | 2023 | A2-A0 | 27619 (1318) | 0.5690 | 0.5689 | -0.0001 [-0.0008, +0.0005] | 0.676 | -0.0001 [-0.0008, +0.0005] (27619) | +0.015 | 0.793 |
| fg3m | 2023 | A3-A0 | 27619 (1318) | 0.5690 | 0.5688 | -0.0002 [-0.0008, +0.0004] | 0.519 | -0.0002 [-0.0008, +0.0004] (27619) | +0.020 | 0.789 |
| fg3m | 2023 | A3-A1 | 27619 (1318) | 0.5687 | 0.5688 | +0.0002 [-0.0003, +0.0006] | 0.531 | +0.0002 [-0.0003, +0.0006] (27619) | +0.020 | 0.789 |
| fg3m (confirmatory) | 2024 | A1-A0 | 27583 (1315) | 0.5923 | 0.5921 | -0.0002 [-0.0007, +0.0003] | 0.445 | -0.0002 [-0.0007, +0.0003] (27583) | -0.008 | 0.785 |
| fg3m | 2024 | A2-A0 | 27583 (1315) | 0.5923 | 0.5920 | -0.0003 [-0.0008, +0.0003] | 0.331 | -0.0003 [-0.0008, +0.0003] (27583) | -0.013 | 0.784 |
| fg3m | 2024 | A3-A0 | 27583 (1315) | 0.5923 | 0.5920 | -0.0003 [-0.0008, +0.0002] | 0.310 | -0.0003 [-0.0008, +0.0002] (27583) | -0.008 | 0.785 |
| fg3m | 2024 | A3-A1 | 27583 (1315) | 0.5921 | 0.5920 | -0.0001 [-0.0005, +0.0003] | 0.718 | -0.0001 [-0.0005, +0.0003] (27583) | -0.008 | 0.785 |
| pts (confirmatory) | 2023 | A1-A0 | 27619 (1318) | 3.1528 | 3.1514 | -0.0015 [-0.0042, +0.0013] | 0.322 | -0.0015 [-0.0042, +0.0013] (27619) | +0.063 | 0.774 |
| pts | 2023 | A2-A0 | 27619 (1318) | 3.1528 | 3.1562 | +0.0034 [+0.0006, +0.0063] | 0.020 | +0.0034 [+0.0006, +0.0063] (27619) | +0.040 | 0.776 |
| pts | 2023 | A3-A0 | 27619 (1318) | 3.1528 | 3.1548 | +0.0019 [-0.0012, +0.0052] | 0.218 | +0.0019 [-0.0012, +0.0052] (27619) | +0.033 | 0.770 |
| pts | 2023 | A3-A1 | 27619 (1318) | 3.1514 | 3.1548 | +0.0034 [+0.0010, +0.0057] | 0.004 | +0.0034 [+0.0010, +0.0057] (27619) | +0.033 | 0.770 |
| pts (confirmatory) | 2024 | A1-A0 | 27583 (1315) | 3.1884 | 3.1904 | +0.0020 [+0.0001, +0.0041] | 0.043 | +0.0020 [+0.0001, +0.0041] (27583) | +0.032 | 0.763 |
| pts | 2024 | A2-A0 | 27583 (1315) | 3.1884 | 3.1890 | +0.0006 [-0.0015, +0.0028] | 0.577 | +0.0006 [-0.0015, +0.0028] (27583) | +0.031 | 0.761 |
| pts | 2024 | A3-A0 | 27583 (1315) | 3.1884 | 3.1894 | +0.0009 [-0.0012, +0.0032] | 0.380 | +0.0009 [-0.0012, +0.0032] (27583) | +0.035 | 0.762 |
| pts | 2024 | A3-A1 | 27583 (1315) | 3.1904 | 3.1894 | -0.0011 [-0.0028, +0.0006] | 0.226 | -0.0011 [-0.0028, +0.0006] (27583) | +0.035 | 0.762 |
| reb | 2023 | A1-A0 | 27619 (1318) | 1.2928 | 1.2926 | -0.0002 [-0.0013, +0.0009] | 0.695 | -0.0002 [-0.0013, +0.0009] (27619) | +0.027 | 0.793 |
| reb | 2024 | A1-A0 | 27583 (1315) | 1.3140 | 1.3144 | +0.0004 [-0.0005, +0.0013] | 0.396 | +0.0004 [-0.0005, +0.0013] (27583) | +0.028 | 0.788 |
| total (confirmatory) | 2023 | T1-T0 | 1318 (1318) | 10.7132 | 10.6969 | -0.0163 [-0.1141, +0.0862] | 0.752 | -0.0163 [-0.1141, +0.0862] (1318) | -1.080 | 0.802 |
| total | 2023 | T2-T0 | 1318 (1318) | 10.7132 | 10.7472 | +0.0340 [-0.0484, +0.1203] | 0.439 | +0.0340 [-0.0484, +0.1203] (1318) | -1.536 | 0.801 |
| total (confirmatory) | 2024 | T1-T0 | 1315 (1315) | 10.9203 | 10.8612 | -0.0591 [-0.1816, +0.0522] | 0.327 | -0.0591 [-0.1816, +0.0522] (1315) | -0.068 | 0.789 |
| total | 2024 | T2-T0 | 1315 (1315) | 10.9203 | 10.9689 | +0.0486 [-0.0608, +0.1447] | 0.335 | +0.0486 [-0.0608, +0.1447] (1315) | -0.467 | 0.791 |

### Pass rules (confirmatory family)

| test | season | dCRPS | floor | CI hi | BH p | MDE (2.8 SE) | checks failed | pass |
|---|---|---|---|---|---|---|---|---|
| pts | 2023 | -0.0015 | -0.005 | +0.0013 | 0.483 | 0.0039 (powered) | 1_floor, 2_ci_upper_below_0, 3_bh_p_below_0.05 | no |
| pts | 2024 | +0.0020 | -0.005 | +0.0041 | 0.129 | 0.0029 (powered) | 1_floor, 2_ci_upper_below_0, 3_bh_p_below_0.05, 5_noise_control | no |
| fg3m | 2023 | -0.0004 | -0.005 | +0.0003 | 0.483 | 0.0009 (powered) | 1_floor, 2_ci_upper_below_0, 3_bh_p_below_0.05 | no |
| fg3m | 2024 | -0.0002 | -0.005 | +0.0003 | 0.445 | 0.0007 (powered) | 1_floor, 2_ci_upper_below_0, 3_bh_p_below_0.05, 5_noise_control | no |
| total | 2023 | -0.0163 | -0.05 | +0.0862 | 0.752 | 0.1431 (UNDERPOWERED) | 1_floor, 2_ci_upper_below_0, 3_bh_p_below_0.05, 4a_bias, 4c_slices ; slices: crew_pace_top_quartile | no |
| total | 2024 | -0.0591 | -0.05 | +0.0522 | 0.445 | 0.1670 (UNDERPOWERED) | 2_ci_upper_below_0, 3_bh_p_below_0.05 | no |

**Verdict: FAIL** {"fg3m": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "FAIL"}, "pts": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "FAIL"}, "total": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "UNDERPOWERED"}}

### Win sanity (expected null; tripwire only)

* 2023: n=1318 games, dLogLoss +0.0001 [-0.0012, +0.0015]
* 2024: n=1315 games, dLogLoss -0.0005 [-0.0017, +0.0006]
* tripwire fired (CI upper < 0 on both seasons): **False**

## Implementation choices the frozen text leaves open (fixed before any fit)

* Official ids are used as stored; the four same-name id pairs are NOT merged (merging would be a rule change).
* Games with any NULL game-level quantity (no box score) are excluded from the league and official running means (none exist in 2022-2024).
* Total head: scale-head floor 1.0 point; calibration window = newest 15% of training games cut on a date boundary (no min_cal_rows rule); a block is fitted only with >= 400 training games; recency weight 0.5 ** (games_ago / 10) with games_ago = 0 for the most recent prior game; the as-of league mean for the recency shrinkage is the mean points per team-game over ALL games strictly before the date (all loaded seasons). Elo margins are the MOV-Elo (configs/mov_elo_tuned.yaml) expected margin that production props use.
* Total head game type = game_id prefix (002 regular, 004 playoff, 005 play-in); day-of-season = days since the first game of the season in the schedule.
* 80% interval coverage guard = share of randomized, continuity-corrected PIT values on the integer grid inside [0.1, 0.9]; bias = mean(predictive mean - outcome).
* Pass rule (5) A2 noise control: gain(A2) <= 0.5 * gain(A1), gain = -(arm - A0) mean CRPS. A2 and A3 are fitted for pts and fg3m only (the confirmatory props); reb and ast get A0/A1.
* Quartiles for the crew_pace slices are computed per evaluation season over covered rows.
* Win sanity: crew_home_margin is centred by the as-of league mean home margin; missing -> 0; ridge lambda 5.0 and min_signal_games 150 as configs/injury_elo.yaml.
* Power: SE is taken from the game-clustered bootstrap CI half-width; MDE = 2.8 * SE.

## 5. Proposed ledger rows (draft; maintainer appends, unnumbered)

| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | pts 2023 A1/T1 vs control, integer-support CRPS, n=27619 rows / 1318 games | -0.0015 | -0.0042 | +0.0013 | game | 0.483 | verdict FAIL | not holdout (<=2024) |
| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | pts 2024 A1/T1 vs control, integer-support CRPS, n=27583 rows / 1315 games | +0.0020 | +0.0001 | +0.0041 | game | 0.129 | verdict FAIL | not holdout (<=2024) |
| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | fg3m 2023 A1/T1 vs control, integer-support CRPS, n=27619 rows / 1318 games | -0.0004 | -0.0010 | +0.0003 | game | 0.483 | verdict FAIL | not holdout (<=2024) |
| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | fg3m 2024 A1/T1 vs control, integer-support CRPS, n=27583 rows / 1315 games | -0.0002 | -0.0007 | +0.0003 | game | 0.445 | verdict FAIL | not holdout (<=2024) |
| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | total 2023 A1/T1 vs control, integer-support CRPS, n=1318 rows / 1318 games | -0.0163 | -0.1141 | +0.0862 | game | 0.752 | verdict UNDERPOWERED | not holdout (<=2024) |
| T### | 2026-10-10 | REFEREES (frozen sha256 14613c3a) | total 2024 A1/T1 vs control, integer-support CRPS, n=1315 rows / 1315 games | -0.0591 | -0.1816 | +0.0522 | game | 0.445 | verdict UNDERPOWERED | not holdout (<=2024) |

## 6. Draft results-section text for docs/REFEREES.md (not written there)

**Results (2026-10-10).** Verdict FAIL: {"fg3m": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "FAIL"}, "pts": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "FAIL"}, "total": {"confirmed_on_2024": false, "selected_on_2023": false, "status": "UNDERPOWERED"}}. Full tables in `reports/referees.md`; consequence for a PASS is shadow logging and red-team only.

## 7. Reading

Verdict FAIL. See the per-test table: a CI spanning 0 on the total head means no power, not no effect.
