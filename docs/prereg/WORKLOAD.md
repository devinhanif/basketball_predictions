# WORKLOAD: accumulated load, schedule density and travel as predictors of who sits, who is cut, and what they produce (pre-registration)

Status: **FROZEN (Sun Oct 11 23:25 CDT 2026) by the main session on Devin's "yes to all": Cell P tests all not-OUT absences (rest-DNP cell descriptive only); Cell P is the one PPLAY_CONTEXT follow-up and the P(play) family closes after it; minutes floor -0.10 min kept.** Earlier: DRAFT (Sun Oct 11 22:58 CDT 2026), written by the modeler. Not frozen. The main session reads it with Devin and commits
`prereg(WORKLOAD): <sha256>` BEFORE any arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the rule. Freeze evidence once
committed: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/WORKLOAD.md | shasum -a 256` (first 8 hex into the
results section and every ledger row). Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = frozen holdout, never loaded).
No arm exists. The only model computation run is the A0 reproduction (stored components, no fit).

## What Devin said
> "run" (2026-10-11), on a rule for player workload and schedule density: accumulated workload predicts rest days and minutes cuts
> (who sits or is limited), not shooting efficiency.

## Honest prior (read first)
* Information, not architecture, has won here. Workload features are pre-tip and as-of, but production already holds recency minutes,
  rest days, back-to-back, role, and the official report. Nearest neighbours, all closed: TRACKING_SCREEN (T119-T123, T174-T175: passing,
  shooting, rebounding and hustle rates add nothing; the ACTIVITY family, distance and speed, was never run: this rule is that family),
  ADVANCED_RATES (T424-T439: seven families null, the placebo beat the real columns in 14 of 15 cells), CONTEXT_SCREEN_GAMES (T180:
  travel and altitude null for game winners; only national TV survived), MINUTES_V2 (T162-T173: minutes CRPS -0.83 min yet props only
  -0.0053 / -0.0018 for pts, floor missed in 2024), PPLAY_CONTEXT (T316-T317: the team back-to-back habit term is noise; the gain was the
  T-60 report tag), MINUTES_HAZARD (T300-T314: null).
* The decisive measured fact (gate G2): rest and management absences are rare and almost all of them are on the T-60 report already.
  Rest/management-tagged DNPs: 49 / 79 / 115 per season (2022 / 23 / 24); 43 / 72 / 110 of them OUT on the T-60 report; only 6 / 7 / 5
  were not. Production reads the report; the model cannot learn a rest signal from 5-7 unannounced events a season. See G2 for what that
  does to the first target.
* Plausible channel left: a smaller, fuzzier one. Heavy recent minutes, a dense or long-haul stretch, or a fading speed profile could shift
  the minutes of a played game (a coach limiting a tired star) and P(play) for questionable players. This is testable on 33k P(play) rows,
  27.6k minutes rows and 27.6k props rows per season. My estimate that any cell clears its floor: about 10% (minutes most likely, props
  least). The rule exists so a null is clean and closed.
* The "not shooting efficiency" part of the story is a prediction, not a hope: if the features help props, they must help through minutes.
  Check D3 (oracle minutes) says whether that is true.

## Hypothesis
H1: as-of workload, schedule and travel features lower (a) log loss of P(play) for rostered, not-OUT rows, (b) continuous CRPS of minutes
given play, and (c) integer-support CRPS of the production props, each beyond a baseline that already holds every fact it can see. Null: no
change. Protects against: crediting extra columns or capacity (placebo), crediting the report tag (the P(play) comparator carries it), and
crediting a stat model for what only minutes explain (D3).

## Data exposure (honest)
Seen: only the gate statistics below and feature marginals (means, sd, null rates, slice counts) on the A0 rows of 2023-24. No stat outcome,
no log loss, no CRPS of any arm. The first missingness audit FAILED on the first design (see G6) and the design was changed before any
outcome. Season 2025 never loaded (one `games` row count touched the table in exploration; no player or outcome row); `season <= 2024`
asserted in SQL and code; `nba.duckdb` read-only; select 2023, report 2024.

## Features (as-of; `research/workload/asof.py`, 13 unit tests)
Every value for a row uses games dated STRICTLY EARLIER than the row's game date. Tonight's box, minutes, tracking, result, and schedule rows
AFTER tonight never enter; the tip instant and arena of tonight (published in the schedule) enter only through `early_tip`, `miles_*`,
`tz_cross_hr`, `alt_*`. Real tips (`tip_source="real"`; 3,958 of 3,958 games 2022-24 have one; no proxy). Model columns carry the prefix
`wl_`; 27 columns in three families:
* LOAD (player; 15). LOAD_MIN (8, box only): minutes in the prior 3 and 7 calendar days (`min_3d`, `min_7d`), played games in them
  (`g_3d`, `g_7d`), mean minutes of the last 3 and 5 played games, minutes of the last game, last-5 mean minus the mean of played games
  ranked 6-25 back (0 when fewer than 5 base games). LOAD_TRACK (7): tracked distance in the same windows (0 when he played no game in the
  window; NaN only when he played and none of them has a tracking row), distance per minute and minute-weighted speed over the last 3 played
  games, `dpm_decline` and `speed_decline` = `w/(w+30) x (last-3 level minus own baseline)`, baseline = tracked played games ranked 4-30 back,
  w = tracked minutes of the last 3, 0 when the baseline has < 100 tracked minutes (so no NaN by construction), and `track_n_l3`.
* SCHEDULE (team; 6): 3-in-4 (tonight is the 3rd game in 4 calendar days), 5-in-7, prior games in the last 10 days, road streak including
  tonight (consecutive away games), road trip of 4+ games (streak >= 4 at tonight; "end of the trip" would need tomorrow's schedule and is
  NOT used), early tip (local tip before 18:00 at tonight's arena). The second night of a back-to-back and days off are in the frame for
  SLICING only: production (`rest_days`, `b2b`) and M2 already hold them.
* TRAVEL (team; 6): miles from the previous game's arena (haversine on `configs/team_markets.yaml` lat/lon, the T180 source; arena = the
  home team's), miles over legs dated d-6..d, time zones crossed (tonight's arena UTC offset minus the previous arena's, at tonight's tip),
  eastbound flag, tonight's arena altitude and the altitude change. Off-season is not a leg: a team's first game of a season has NULL
  schedule/travel values (1.1% of A0 rows). Neutral-site games (Paris, Mexico City) use the home team's coordinates (a handful; recorded).
* Allowed to change nothing: windows (3, 7, 10 days; 3, 5 games), K = 30 minutes, 100 baseline minutes, the 18:00 cut. No sweeps.

## Arms (one hypothesis; 6 primary cells)
Same rows, DNP handling, month-block walk-forward, 2022 warm-up, hyper-parameters and seeds as the comparator in each cell (trees on the Mac CPU).
* **Cell P (P(play))**: rows = MINUTES_V2 secondary / PPLAY_CONTEXT rows (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report;
  33,093 / 32,986 rows, DNP rate 16.6% / 16.4%). Comparator C = P1 (M2-p) + the report-tag-by-strength and morning/evening flags of PPLAY_CONTEXT
  (`build_terms`, without the b2b-habit terms), i.e. everything the best known P(play) sees. Candidate W = C + 27 columns. Metric: log loss.
* **Cell M (minutes given play)**: comparator M2 (MINUTES_V2); candidate M2 + 27 columns. Metric: continuous CRPS in minutes; also versus
  production P and strong simple R (reported, as MINUTES_V2).
* **Cells S (pts, reb, ast, fg3m)**: A0 = production + lower-tail mixture (reproduced: G4); B = A0 + 27 columns appended through
  `stat_feature_names(stat, extra=...)`; integer support `ceil(q - 0.5)` for every arm, pts included (the T114-T118 artifact).
* Placebos: LOAD columns permuted across rows within player-season; SCHEDULE and TRAVEL columns permuted across a team's games within
  team-season (one permutation per team-game; `permute_within_player_season`, `permute_within_team_season`). Knockout per family (LOAD,
  SCHEDULE, TRAVEL removed one at a time), and LOAD-without-tracking (LOAD_MIN only) to say whether tracking adds beyond box minutes.
  Placebo and knockouts run for every cell; shuffled-label (residual labels permuted within month block) only for cells that pass rules 1-2.
* Descriptive, outside the family: **D1** the rest-DNP target (G2: untestable, counts and rates only); **D2** decision score
  (`docs/DECISION_SCORING.md`, DraftKings T-60 legs on A0 rows, 53,247 / 53,752, thresholds 0 / 2 / 5 / 10%, "insufficient sample" < 300 bets;
  cannot rescue a failure); **D3** oracle-minutes pair for pts and reb: A3 (A0 with realised minutes) versus A3 + 27 columns. If B beats A0
  but A3+W does NOT beat A3, the effect runs through minutes (the story); if A3+W also gains, the features carry rate information, which
  contradicts the story and is flagged.

## Metrics, support, inference
Paired difference (candidate minus comparator) on the same rows, 95% percentile bootstrap clustered by `game_id` (2000 draws, seed 0, both teams
of a game share a cluster), two-sided clustered-bootstrap p, Benjamini-Hochberg across the **m = 6 primary cells {P, M, S-pts, S-reb, S-ast,
S-fg3m}** at q = 0.05, separately for 2023 and 2024. Per-cell MDE (1.96 x clustered SE) printed beside each result (neighbours on these rows:
props 0.0006-0.0029, minutes about 0.04 min, P(play) about 0.003). Secondary, cannot rescue a failure: bias, 80% PIT coverage, lower-tail PIT,
threshold log loss, ECE (cell P).
* Floors (practical, absolute, chosen before any arm): props **-0.005** CRPS (house floor); minutes **-0.10 min** CRPS (MINUTES_V2's floor against P;
  at MDE about 0.04 it is testable; note 0.10 min moves pts by only about 0.0006, so a minutes-only pass is a finding about minutes, not a props
  edge); P(play) **-0.005** log loss (PPLAY_CONTEXT's floor, MDE about 0.003).
* Slices (descriptive plus the guard; n >= 300 rows): starter vs bench (as-of `start_rate >= 0.5`); star (as-of top-3 team minutes per appearance
  over 10 games) vs rest; team status contending / middle / out (`research/budget/status.py`, as TEAM_BUDGET); late season (>= 60 team games);
  second night of a back-to-back; 3-in-4; road trip 4+; eastbound >= 2 zones; miles >= 1500; early tip; speed-decline bottom decile; cold start
  (5 <= n_prior < 10); first 15 team games.

## Pass rule (per cell; 2023 selects, 2024 confirms; all must hold)
1. 2023: point effect at or beyond the floor and clustered CI upper < 0. 2024: same, plus BH p <= 0.05 over the 6 cells.
2. Guards: |cov80_arm - 0.80| <= |cov80_base - 0.80| + 0.03 (CRPS cells); |bias_arm| <= |bias_base| + 0.05 and <= 0.5 (minutes: <= 0.5 min); ECE not
   worse by more than +0.005 (cell P); no slice with n >= 300 worse by more than +0.01 (props, P) or +0.05 min (minutes); cold-start slice CI lower
   bound <= 0 (a gain only where there is no history is a flag).
3. Attack gates: the arm beats its family placebo by a point <= -0.003 (props, P) / -0.05 min (minutes); the shuffled-label arm gives an effect >= -MDE;
   planted-future and planted same-game tests pass in CI with non-inert controls; the missingness audit (G6) passed on the frozen frames; the arm does
   not beat its own oracle bound (A3 for props, realised minutes for M).
4. Knockout: if removing one family removes >= 80% of the gain, or one family alone keeps >= 80%, the cell is flagged "one family carries it" and is NOT
   a pass until the adversary clears it (the `opp_adjusted_ridge` fingerprint); the claim is then named for that family. A tracking claim needs
   LOAD(full) - LOAD_MIN <= floor/2 on the same rows.
5. Honest ceiling: only the gain beyond the comparator counts. A PASS means a red-team review and a drafted one-touch holdout row. Nothing is promoted
   or wired in; that is Devin's.

## What each choice protects against
* C carries the report tag and M2 carries rest/b2b: the PPLAY_CONTEXT lesson (a "gain" that was the T-60 tag the comparator had not seen).
* As-of windows over played games, dates strictly earlier: the box-score-membership leak; same-day information. Real tips: the proxy-tip leak.
* No NaN by construction in the decline columns, schedule over regular season AND post-season: the ADVANCED_RATES G6 NaN-follows-minutes fingerprint,
  and the first design here (below). Caveat: with ~zero NaNs the audit cannot see a value-level dependence; the placebo, shuffled-label and plant
  tests do.
* Placebo within player-season / team-season: capacity and noise (14 of 15 ADVANCED_RATES cells were beaten by their placebo).
* Knockout and the 80% rule: one group carrying everything. D3: crediting rate when only minutes can move. Integer grid for every arm: T114-T118.
* Select 2023, report 2024, 2025 untouched; the floor and the MDE: a p-value on a gain nobody could use. BH over the 6 cells: six chances to win.
* Demoting the rest-DNP target (G2): a cell that cannot reach any minimum n would be a coin flip dressed as a test.

## Kill criteria
* G6 or a planted test fails after the freeze: stop, report a leak, fit nothing further; the fix is a new id.
* No cell selects on 2023: closed. No other windows, thresholds, decline definitions, interactions, or opponent versions afterwards. At most ONE
  pre-registered follow-up (new id), only after a PASS.
* A column whose NULL rate differs by more than max(2 pp, 3 x placebo) across minutes buckets on the frozen frames is dropped before any fit.
* Season 2025: no touch is requested by this rule.

## Minimum-data gates (non-negotiable 8): measured Sun Oct 11 22:57-22:58 CDT 2026 on `nba.duckdb` (read-only), seasons 2022-2024
No arm was fitted. Reproduce: `.venv/bin/python -W ignore -m research.eval.workload_gates all` (about 30 s, light, never takes the lock; JSON in
`reports/prereg_workload/gates.json`). Limits were set before the numbers were read except where noted.
* **G1 tracking coverage: PASS.** `player_game_tracking` has rows for 1320 / 1318 / 1315 of 1320 / 1318 / 1320 games (2022 / 23 / 24; five 2024
  games have none); every game has >= 8 rows except those five. Played rows (minutes > 0) with a tracking row, by minutes bucket (<5 / 5-10 / 10-20 /
  >20): 100% in 2022 and 2023, 99.1 / 99.8 / 99.5 / 99.7% in 2024. This is NOT the hustle shape (F9: 14% / 32% / 65% / 88%+): tracking rows exist for
  low-minute players. Units: distance in miles (max 4.16; 0.074 mi/min mean), speed in mph (mean 4.25, max 5.66), tracking minutes equal box
  minutes (gap 0.0); 0.4% of >= 10-minute rows have zero distance. 2025 has none and is never loaded.
* **G2 rest-DNP events: FAIL for the first target; target demoted to descriptive.** Rest/management-tagged player-games on any official report (2022 /
  23 / 24): 50 / 85 / 117, of which DNP 49 / 79 / 115; OUT on the T-60 report 43 / 72 / 110; **not OUT at T-60: 6 / 7 / 5** (limit 150 per season for
  a tested cell). The latest-snapshot reason class among OUT box DNPs is rest/management for only 2 / 7 / 38. So "P(DNP for rest)" is not a testable
  cell: Cell P tests ALL DNPs of not-OUT rostered players (5,481 and 5,415 events, 16.6% and 16.4% of 33,093 / 32,986 rows), which is the same quantity
  production's P(play) serves; the rest-specific count is D1. Box DNPs by latest T-60 status, 2023: out 536, questionable 314, doubtful 52,
  probable 43, available 154, unlisted 4,893. The rest-specific question needs a morning-timing family (the report is thin then) and a regime with
  more rest absences; not in this rule.
* **G3 arena coordinates, tz, tips, schedule: PASS.** 30 / 30 franchises have lat, lon, IANA tz and altitude in `configs/team_markets.yaml`; all 3,958
  games (3,690 regular season, 268 post-season) of 2022-24 carry a real tip; 7,916 team-games, 7,826 with a prior game in the season. `team_context`
  has 0 rows for these seasons, so it cannot validate distances; five public great-circle reference distances (from my recollection, NOT verified
  against a source) agree within 1.0% (worst ATL-MIA). Rates: second night of a back-to-back 17.5% of team-games, 3-in-4 25.9%, 5-in-7 1.7%, road
  trip 4+ 7.5%, early tip 9.4%, eastbound 16.6%, mean 519 miles per leg.
* **G4 A0 reproduction (stored components, no fit, 6 s): PASS.** 16 of 16 cells match `reports/lower_tail/candidates.json` to 0.0 with the same n
  (27,619 / 27,701): pts mixture 3.140236 / 3.176840 (raw 3.152830 / 3.187553), reb 1.288179 / 1.311401, ast 0.901954 / 0.898517, fg3m 0.566212 /
  0.590155. Devin's quoted pts numbers are the mixture.
* **G5 slice sizes on the 55,320 A0 rows (2023 / 2024 rows, games): PASS for the guards, thin for two flags.** Second night 4,422 / 4,694 rows (365 / 379
  games); 3-in-4 6,417 / 6,884 (512 / 536); road trip 4+ 2,020 / 1,837; early tip 2,661 / 2,744 (125 / 133 games); eastbound >= 2 zones 1,177 / 1,356; miles
  >= 1500 1,727 / 1,885; speed-decline bottom decile 2,819 / 2,714. 5-in-7: 375 / 608 rows but only 34 / 57 games: a clustered CI there is wide, descriptive.
* **G6 missingness audit by realised-minutes bucket: PASS after a design change made before any outcome.** First run FAILED: the schedule builder
  covered regular-season games only while the A0 rows include 3,488 playoff and 250 play-in rows, so the schedule columns were NULL on 6.8% of rows,
  concentrated in high-minute (starter, post-season) rows: gap 4.0 pp against a permuted-label placebo gap of 0.4 pp. Fix: the schedule chain includes
  regular season, play-in and playoffs of the same season. Re-run on 55,320 rows, all 29 native columns: max gap 0.19 pp (placebo 0.33 pp; limit
  max(2 pp, 3 x placebo)); no column over the limit. Tracked-distance windows are NaN on 0.08% / 0.12% of rows; a team's first game of a season leaves the rest, miles, tz and density columns NULL on 1.1% of rows (gap across buckets under 0.2 pp); the others have no NaN.
* **G7 as-of replay on real data: PASS.** For 300 sampled rows the 15 LOAD columns recomputed from only games dated before the row equal the full-frame
  value (0 mismatches); for 100 rows the 12 SCHEDULE/TRAVEL columns recomputed from only games dated up to the row's date equal the full-frame value
  (0 mismatches). Unit tests: planted future (box, minutes, distance, speed set to nonsense for all games >= D: byte-identical), same-day exclusion, a
  control (editing the previous game moves the feature), hand-computed windows, road streak, tz sign, 3-in-4 and 5-in-7, placebo structure.
* **G8 power and reach: PASS.** Feature sd is non-trivial (min_7d 60.7 +- 39.2, dist_7d 4.4 +- 2.8 mi, speed_decline 0.014 +- 0.151 mph, miles_7d 1,902 +-
  1,105). Estimated MDE (clustered SE x 1.96 from neighbouring studies) is below each floor, so a floor-sized effect is detectable; a smaller true effect
  is not, and the rule makes no claim for it.

## Outputs and harness (written after the freeze, under the committed sha)
`research/eval/workload_eval.py` with stages `gate`, `arms`, `attack`, `report`, refusing unless the sha256 matches; `research/workload/` (builders, 13
tests, done); `research/eval/workload_gates.py` (done). Exact command after the freeze (the maintainer; the modeler never takes `heavy.lock`):
`mkdir data/ops/heavy.lock` (retry 120 s; `rmdir` in `finally`), then `.venv/bin/python -W ignore -m research.eval.workload_eval run --doc docs/prereg/WORKLOAD.md --sha <64 hex>`.
Estimated runtime on the Mac CPU, single process: features 5 s; Cell P about 8 arms x 20 s = 3 min; Cell M about 8 arms x 1 min = 8 min; Cells S 4 stats x (A0, B,
placebo, 3 knockouts, LOAD_MIN) about 7 arms x 1-2 min = 30-55 min; D3 4 arms about 6 min; clustered bootstrap and report 5 min; shuffled-label arms only for
passing cells. About 55-80 minutes, per-stage checkpoints in `data/ops/workload_cache/`, progress printed per arm. Output `reports/prereg_workload/results.json`
and `reports/prereg_workload.md`: `[cell, season, arm, n_rows, n_games, metric, delta, ci_lo, ci_hi, p, p_bh, mde, bias, cov80]`, slice, audit and check rows.

## Questions for Devin before the freeze (safe defaults chosen)
1. The rest-DNP target cannot be tested (G2). Default: demote to descriptive D1 and test P(play) for all DNPs instead. Alternative: drop Cell P.
2. PPLAY_CONTEXT's kill criteria say no more P(play) terms (travel distance, altitude named) and at most ONE follow-up. Default: Cell P here IS that one follow-up and
   the P(play) family closes after it; if you would rather keep it for something else, drop Cell P (m becomes 5).
3. Minutes floor -0.10 min is MINUTES_V2's; it will not move props. Default kept so a minutes-only pass is labelled as such.

=== RESULTS BELOW ===

(not run)
