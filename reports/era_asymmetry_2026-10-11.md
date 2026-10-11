# Era asymmetry in the injury-report audit (HISTORY_WINDOW STOP): diagnosis, 2026-10-11

DESCRIPTIVE. No arm fitted, no model number, season 2025 never loaded, both DBs `read_only=True`. Code: scratch under
`data/scratch/era_asymmetry_2026-10-11/` (prep.py, analyze.py, stats_e.py, defs.py, phi_censor.py); it imports
`research/eval/history_window_eval.py` (`load_world`, `rows_with_real_tips`, `flagged_real`, `distinguishability`) and
`usable_report_rows`; nothing was duplicated except the player-level listing join, and the base flagged set was asserted
identical to the harness's (`flagged_ext`, 7,586 games, True). Population: 198,027 rostered player rows (box-score rows,
DNP rows kept), seasons 2019-24, report snapshot = latest with `as_of <= real tip - 60`. CIs are game-clustered bootstraps
(B = 1000 in the decomposition, 2000 in the definition tests, seed 0). n is on every row.

## Headline (plain)

1. **The frozen "played-rate gap" is not a player-level statistic.** In the harness `has_rep` is GAME-level ("the game has any
   usable snapshot"), which is how `has_report` is built in the features. The has_report=0 group is therefore whole games,
   and there are exactly **9 of them in six seasons** (2019: 2 games / 52 rows; 2020: 1 / 27; 2021: 0 / 0; 2022: 2 / 60;
   2023: 1 / 30; 2024: 3 / 85): bubble/playoff games, the 2023-10-25 opener, the 2024-12-14 NBA Cup final, 2025-05-05.
   The "0.136 in 2022-24" is 175 rows in 6 games; one-game clusters make the bootstrap degenerate (2020 CI [0.010, 0.023]
   from one cluster; 2019 [-0.125, 0.227]; 2022 [-0.055, 0.185]). The **placebo already fails inside the reference
   period**: 2023 gap 0.210 is 7.4 points above the pooled 0.136, and 2022 (0.065) is 7.1 points below it. The gap rule
   as written would STOP current seasons against their own pool. The history gaps (0.051, 0.016, n/a) sit inside the
   2022-24 spread (0.065-0.210). No change of report definition can move this statistic (it depends only on whether a
   usable snapshot exists), which is why H1-H4 are invariant on it.
2. **The real player-level question (listed vs unlisted) has a different answer and is driven by status mix, not by era
   differences in what a status means.** P(played | status) is stable across all six seasons (below). What moves
   P(played | listed) between 0.53 and 0.71 is the share of "out" and "available" listings (out 13%-33% of listings;
   "available" 59% in 2021-22 when the hourly report listed healthy players).
3. **The gap_days phi failure is left-censoring of the window, not a report effect.** `gap_days` is computed from played-game
   history, not from the report; no report definition touches it. 2019 phi 0.1265 [0.1045, 0.1496] vs pooled 0.0727 (diff
   +0.0538, limit 0.05) because 15.8% of 2019 rows before 2019-11-15 have no earlier played game in the loaded window (null
   share 3.2% vs 0.6%). Matched comparison, the same cold start applied to current seasons (history counted from each
   season's own opener): 2022 0.098 [0.078, 0.120], 2023 0.108, 2024 0.110 vs 2019 0.1265; diff +0.017 to +0.028, inside 0.05.
   The production W0 arm has the same cold start at 2022-10-18. 2020 (0.0748) and 2021 (0.0814) already pass; the first 40
   days of 2022 and 2023 show phi 0.150 and 0.130 vs 0.037 and 0.043 for the rest of those seasons.
4. **Verdict on the brief's question: no report-feature definition (H1-H4) brings every history season inside the rule's
   limits as the rule is written.** Gap: the frozen game-level statistic is invariant to H1-H4; on the player-level
   reading H1, H3, H4 and the baseline breach in 2020 (diff -0.100 to -0.118), and H2/H3 breach in all three history seasons (-0.147 to -0.190). Phi: invariant
   to every definition, 0.0538 in 2019. AUC/sign-flip: every definition passes (no sign flip), so that leg is not binding.
   Therefore **no HISTORY_WINDOW_2 pre-registration draft is written** (per the brief). What the evidence supports instead is
   a repair of the audit statistics (section "What this means"), which is a rule change that is Devin's call.

## (a) Status mix on the report and P(played | status), listed rows of reported games (latest pre-tip snapshot)

Share of listed rows, then P(played) [game-clustered 95% CI], n rows.

| status | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|
| out | 21.7% / 0.05 [0.03,0.07] n=522 | 33.0% / 0.00 [0.00,0.01] n=851 | 21.3% / 0.01 [0.00,0.01] n=564 | 12.9% / 0.06 [0.03,0.09] n=356 | 17.0% / 0.02 [0.01,0.03] n=578 | 25.1% / 0.02 [0.01,0.03] n=1009 |
| doubtful | 2.4% / 0.02 n=57 | 2.2% / 0.11 n=56 | 0.4% / 0.18 n=11 | 1.1% / 0.23 n=31 | 1.6% / 0.05 n=55 | 1.8% / 0.08 n=71 |
| questionable | 22.0% / 0.72 [0.68,0.76] n=529 | 29.1% / 0.71 [0.67,0.74] n=750 | 14.2% / 0.78 [0.74,0.83] n=376 | 40.3% / 0.78 [0.75,0.80] n=1115 | 39.6% / 0.75 [0.72,0.78] n=1349 | 34.3% / 0.68 [0.65,0.71] n=1379 |
| probable | 24.1% / 0.92 [0.90,0.94] n=580 | 18.4% / 0.92 [0.89,0.94] n=474 | 4.9% / 0.96 [0.93,0.99] n=129 | 23.7% / 0.93 [0.91,0.95] n=656 | 19.1% / 0.93 [0.91,0.95] n=651 | 18.9% / 0.93 [0.91,0.95] n=759 |
| available | 29.8% / 0.84 [0.81,0.87] n=716 | 17.4% / 0.89 [0.86,0.91] n=450 | 59.2% / 0.85 [0.83,0.87] n=1568 | 21.9% / 0.77 [0.72,0.80] n=606 | 22.6% / 0.79 [0.76,0.82] n=770 | 19.9% / 0.84 [0.81,0.86] n=801 |

Reading: within-status played rates agree across eras (out 0.00-0.06, probable 0.92-0.96, questionable 0.68-0.78; history
values lie inside the current-season range). Listed rows are 7.8%-11.5% of rostered rows. The mix differs: 2021 is 59%
"available" (hourly report lists healthy players), 2020 is 33% "out" (COVID absences), 2024 is 25% "out".
Overall P(played | listed): 0.642 / 0.532 / 0.665 / 0.713 / 0.658 / 0.582 (n = 2404 / 2581 / 2648 / 2764 / 3403 / 4019);
P(played | unlisted): 0.858 / 0.818 / 0.842 / 0.824 / 0.827 / 0.834. The listed-minus-unlisted gap is therefore negative
(about -0.11 to -0.29) in every era; the sign convention of the frozen text (a positive 0.136) only arises from the
9 unreported games.

## (b) Reason category (share of listed rows, P(played), n)

| category | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|
| injury/illness (incl. concussion) | 88.0% / 0.67 n=2115 | 88.5% / 0.55 n=2285 | 74.7% / 0.69 n=1977 | 80.3% / 0.75 n=2219 | 83.4% / 0.69 n=2837 | 83.9% / 0.60 n=3372 |
| G League / two-way | 7.6% / 0.53 n=182 | 0.2% / 0.40 n=5 | 5.6% / 0.38 n=148 | 6.9% / 0.40 n=190 | 7.5% / 0.41 n=254 | 7.6% / 0.47 n=307 |
| health & safety protocols | 0.1% n=2 | 6.4% / 0.41 n=165 | 7.9% / 0.66 n=208 | 6.1% / 0.75 n=168 | 1.7% / 0.79 n=57 | 1.4% / 0.63 n=57 |
| rest / coach's decision | 0.3% n=7 | 1.2% n=30 | 0.5% n=14 | 0.2% n=6 | 0.4% n=14 | 1.0% n=42 |
| not-with-team / personal / suspension / trade / reconditioning | 3.1% / 0.17 n=75 | 1.7% / 0.33 n=45 | 4.8% / 0.68 n=127 | 2.9% / 0.34 n=80 | 2.4% / 0.20 n=83 | 2.7% / 0.21 n=110 |
| unknown ("-"/blank) | 1.0% n=23 | 2.0% n=51 | 6.6% n=174 | 3.7% n=101 | 4.6% n=158 | 3.3% n=131 |

Injury x status: injury+out P(played) 0.00-0.01 in all six seasons (n = 411, 712, 460, 244, 471, 820); injury+questionable
0.72, 0.71, 0.79, 0.79, 0.79, 0.69; injury+probable 0.93-0.96. G League/two-way listings are 6-8% of listings in every
era except 2020 (bubble-era restart, 0.2%), so "two-way listings missing in 2019-20" is not the story: 2019 has 182 of
them. Protocols are a real era feature (6-8% of listings in 2020-22, 1-2% in 2023-24) with P(played) 0.41 / 0.66 / 0.75 in
2020 / 21 / 22. Reason strings need whitespace stripping (2022+ strings are concatenated, e.g. `gleague-twoway`); a naive
match would silently drop the current seasons.

## (c) Rostered but unlisted (the player-level has_report=0 group)

| season | unlisted DNP rate [CI] (n rows) | unlisted share of all DNPs [CI] (n DNP rows) |
|---|---|---|
| 2019 | 0.142 [0.137,0.148] (26251) | 0.813 [0.799,0.828] (4599) |
| 2020 | 0.182 [0.176,0.189] (28938) | 0.814 [0.800,0.827] (6487) |
| 2021 | 0.158 [0.151,0.164] (31200) | 0.847 [0.835,0.859] (5803) |
| 2022 | 0.176 [0.170,0.182] (31200) | 0.874 [0.863,0.884] (6272) |
| 2023 | 0.173 [0.167,0.180] (31382) | 0.824 [0.811,0.836] (6606) |
| 2024 | 0.166 [0.159,0.172] (30983) | 0.754 [0.738,0.768] (6821) |

The hypothesis that 2019-20 has a different base rate of unlisted DNPs is only weakly supported: 2019 is the LOWEST (0.142,
about 3 points below 2022-24's 0.166-0.176), 2020 is at the top (0.182, CI overlaps 2022), 2021 in between. So unlisted
DNPs are 14-18% in every era (rotation depth, DNP-CD, G League assignment are never on the report) and are 75-87% of all
DNPs; the report cannot see most DNPs in any era. Over 99.8% of these DNP rows have NULL minutes in every season (same
DNP handling in both DBs).

## (d) Snapshot used: latest pre-tip vs the morning snapshot (earliest on the game date)

| season | games with a morning snapshot | listed share latest / morning | gap latest [CI] | gap morning [CI] |
|---|---|---|---|---|
| 2019 | 1137 | 8.40% / 7.81% | -0.215 [-0.239,-0.188] | -0.203 [-0.229,-0.179] |
| 2020 | 1143 | 8.19% / 7.41% | -0.288 [-0.314,-0.264] | -0.262 [-0.288,-0.235] |
| 2021 | 1323 | 7.82% / 6.38% | -0.177 [-0.200,-0.155] | -0.141 [-0.167,-0.118] |
| 2022 | 1318 | 8.14% / 7.10% | -0.111 [-0.130,-0.092] | -0.095 [-0.116,-0.075] |
| 2023 | 1317 | 9.78% / 8.33% | -0.169 [-0.189,-0.148] | -0.151 [-0.172,-0.131] |
| 2024 | 1317 | 11.48% / 9.73% | -0.252 [-0.273,-0.233] | -0.236 [-0.257,-0.214] |

The snapshot choice shifts every season by a similar 1.5-3.6 points and does not change the ordering of eras
(2020 and 2024 most negative, 2022 least). The era pattern is not a snapshot-cadence effect. (Snapshots per covered game
in the audit: 3.9 in 2019, 3.8 in 2020, 20.5 in 2021, 2.4-2.5 in 2022-24; median lead before tip 150 / 150 / 60 / 180 min.)

## (e) Candidate definitions, no model fitted

Player-level reading of the STOP gap: gap = P(played | listed under the definition) - P(played | not), rostered rows of
reported games; diff = season gap minus pooled 2022-24 gap under the SAME definition; the rule's limit is |diff| <= 0.05.
The 2022-24 seasons are shown as placebos (each against its own pool); they should pass if the limit is reachable.

| definition | pooled 22-24 gap [CI] | 2019 diff | 2020 diff | 2021 diff | placebo 2022 / 2023 / 2024 diff | history all within 5 pts |
|---|---|---|---|---|---|---|
| D0 any listing (production notion) | -0.186 [-0.197,-0.173] | -0.030 OK | **-0.100** | +0.008 | **+0.074** / +0.017 / **-0.067** | NO (2020) |
| H1 injury/illness reason only | -0.150 [-0.163,-0.136] | -0.033 OK | **-0.118** | +0.001 | **+0.082** / +0.020 / **-0.073** | NO (2020) |
| H2 status out/doubtful/questionable | -0.345 [-0.361,-0.330] | **-0.147** | **-0.149** | **-0.182** | **+0.116** / +0.034 / **-0.100** | NO (all three) |
| H3 H1 and H2 | -0.318 [-0.336,-0.300] | **-0.154** | **-0.153** | **-0.190** | **+0.119** / +0.042 / **-0.107** | NO (all three) |
| H4 listing excl. protocol and two-way (separate flags) | -0.168 [-0.181,-0.155] | -0.036 OK | **-0.107** | +0.013 | **+0.081** / +0.020 / **-0.072** | NO (2020) |
| H5 (extra, found after seeing the table) status OUT only = the feature's own trigger | -0.800 [-0.809,-0.790] | -0.008 OK | -0.011 OK | -0.037 OK | +0.033 / -0.008 / -0.009 | YES (but see below) |

Cell n: D0 listed 2404 / 2581 / 2648 (history), 2764 / 3403 / 4019 (current), unlisted 26k-31k; H5 listed (out) 522 / 851 /
564 / 356 / 578 / 1009. H4 separate flags: `two_way_flag` rows n = 182 / 5 / 148 / 190 / 254 / 307 with P(played) 0.53 / 0.40
/ 0.38 / 0.40 / 0.41 / 0.47; `protocol_flag` n = 2 / 165 / 208 / 168 / 57 / 57 with P(played) 0.00 / 0.41 / 0.66 / 0.75 /
0.79 / 0.63. Diff CIs are in `data/scratch/era_asymmetry_2026-10-11/gaps_defs.json`.

H1-H4 do not help: restricting statuses (H2/H3) removes the "probable/available" listings that dilute the mix and makes
the cross-era differences larger (the out share is what varies). Every any-listing definition fails the PLACEBO (2022 and
2024 are 6.7-8.2 points from their own pool), i.e. the 5-point limit is tighter than ordinary season-to-season mix
variation and cannot be a valid criterion at the player level either. H5 passes everywhere, but nearly mechanically
(players listed OUT play 0-6% of the time, so the gap is about -(P(played | unlisted)) in every era); it shows the OUT trigger
the features use is honoured in all six seasons, nothing about the other statuses. H5 was added after seeing the table, so it
is a finding to design around, not a pre-selected definition.

Second STOP statistic, phi(gap_days NULL, DNP) [game-clustered CI], rows n, null share:

| | pooled 22-24 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|---|
| phi | 0.0727 [0.0632,0.0825] | 0.1265 [0.1045,0.1496] | 0.0748 [0.0591,0.0903] | 0.0814 [0.0620,0.0992] | 0.0735 | 0.0737 | 0.0708 |
| n | 103,926 | 28,707 | 31,546 | 33,848 | 34,024 | 34,815 | 35,087 |
| diff vs pooled | | +0.0538 | +0.0021 | +0.0087 | +0.0008 | +0.0011 | -0.0018 |

Invariant to H1-H5 by construction (gap_days comes from played history). Cold-start-matched (each season's history counted
from its own opener): 2019 0.1265, 2022 0.0983 [0.0775,0.1203], 2023 0.1079, 2024 0.1096 (null share 3.2% vs 2.7-2.9%).
The report-derived NULL pattern (`has_report`=0 vs DNP) phi is 0.006 / 0.001 / n/a / 0.007 / 0.016 / 0.020, all inside 0.05
of the pooled 0.014 (n_no_report 52 / 27 / 0 / 60 / 30 / 85, so uninformative).

Third STOP leg, report-column classifier (history vs current), harness code, `n_prior >= 5` rows:

| definition | AUC | sign flips | STOP |
|---|---|---|---|
| D0 (production) | 0.7996 | none | no |
| H1 / H3 | 0.8189 | none | no |
| H2 | 0.7996 (the six report-derived columns read status=out only, so identical to D0) | none | no |
| H4 | 0.8041 | none | no |

AUC is above 0.75 for every definition, but the rule is AND with a sign flip, and there is none. Column subsets (D0):
report-derived (has_report, n_out_rot, vac_*) 0.787; history-derived (since_flag, flag_dir, gap_days) 0.612; gap_days
alone 0.521; has_report alone 0.500. So the era is identifiable from the rotation-absence columns (2020-22 COVID absences, 2021 hourly
cadence), a real difference in the data-generating process rather than a leak; the sign check is what the rule uses to
decide whether it correlates with labels in opposite directions, and it does not.

## What this means

* **Parity under the rule as written: not reached by any definition.** The two statistics that fail cannot be fixed by
  redefining the report feature: the gap statistic is game-level on 9 games (and fails its own placebo), and the phi statistic
  is a window-start artifact of a non-report column.
* What I would put to Devin as a SEPARATE new rule (not a report-definition change, so not drafted here): replace the two
  broken STOP statistics with (i) status-standardised played rates, i.e. P(played | status) per season with the rule that
  each of out / questionable / probable agrees with the pooled 2022-24 value within the game-clustered CI or 0.05 (the history
  per-status rates in table (a) already lie inside the 2022-24 range or overlap it, but this was not formally tested here) and the protocol category reported not gated, and
  (ii) phi(gap_days NULL, DNP) computed against the same cold-start-matched baseline (each window's own opener), plus the
  placebo requirement that the rule must pass 2022, 2023 and 2024 against their own pool before it is allowed to stop
  anything. Argument: both changes are calibrated on the current seasons, not on the history ones. Cost: it is a change to a
  STOP rule after that rule fired, so it must be labelled exactly that, committed with its sha before any arm is fit, and
  the arms (W0-W3) stay untouched. The case against: it moves the goalposts after a stop; the case for: the frozen
  statistic fails its own control. Prior that the extension then passes its fit arms is unchanged at ~20% (the counter-
  hypothesis about COVID seasons stands).
* The era differences that ARE real and would remain: protocols (6-8% of listings in 2020-22), 59% "available" listings
  in 2021, 2020 absences (33% out), and unlisted DNP rate 0.142 (2019) vs 0.166-0.182. These are the data-generating
  process the W1/W2 era flags are for.

## Not verified

* Reason strings were mapped by substring (injury/illness, gleague, healthandsafety/covid, rest/coach, the rest); 5.1% of
  2021 listings are unknown ("-"/blank). The category tables move by a point or two if a string is mis-bucketed.
* The player-level gap and H1-H5 are my reading of the brief; the frozen audit statistic is game-level, and I did not alter
  the harness. The pre-tip snapshot join takes the strongest status if a player repeats in a snapshot.
* Rostered rows are box-score rows (DNP-CD included). Players inactive and absent from the box (most "out" listings)
  are not in the denominator, which is why "out" is only 13-33% of listings here though it is the commonest status in the raw tables.
* AUC and sign tests are the harness's single seed-0 split.

## Drafted rows (not appended)

* LEDGER: `T3xx HISTORY_WINDOW era-asymmetry diagnosis (descriptive): the gap STOP rests on 9 reportless games (175 rows 2022-24; 0-3 per season; CIs degenerate) and fails its own 2022-24 placebo (2022 -7.1 pts, 2023 +7.4 pts vs pooled); phi(gap_days NULL, DNP) 2019 is window-start left-censoring (cold-start-matched 2019 0.127 vs 0.098-0.110); H1-H4 do not move either statistic; AUC 0.80-0.82 but no sign flip under every definition. No definition reaches parity under the rule as written; no HISTORY_WINDOW_2 drafted. Report: reports/era_asymmetry_2026-10-11.md.`
* DECISIONS: `2026-10-11 era asymmetry: do not redefine report features; if the extension is to be revived, it is a new rule that repairs the two STOP statistics (status-standardised played rates; cold-start-matched phi; placebo on 2022-24). Reverse if: Devin prefers the direction stay closed (T315).`
