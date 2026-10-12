# ADVANCED_RATES: as-of advanced rates, shot profile, defense context and play types as props context (pre-registration)

Status: DRAFT 2026-10-11, NOT FROZEN, no arm fitted. Devin confirms, the main session commits it (`prereg(ADVANCED_RATES): <sha256>`)
before any fit. Everything above the line `=== RESULTS BELOW ===` is frozen at that time; freeze evidence is the commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ADVANCED_RATES.md | shasum -a 256` (first 8 hex into the results
section and every ledger row). Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = frozen holdout, never loaded).
Companion draft by another modeler: HISTORY_WINDOW_3 (not touched here).

## What Devin said
> "use advanced stats best practices and create a usage x pace, and PPR" (2026-10-11), then: Game Score, TS%, assist ratio, TOV%,
> PPR, PPWS, OREB%, DREB%, pace (also on-court pace) computed exactly; each stat as a time series per player and per team stint;
> shot types (frequency and efficiency); defense context from tracking and hustle; a PLAY_TYPE family (transition, putbacks,
> assisted vs unassisted) with no in-game momentum feature.

## Honest prior (read before the rest)
This repo has already tested neighbours of every family here and found nothing: FOUR_FACTORS (as-of team pace, eFG/TOV/ORB/FTR for
offence and allowed; T264-T279, NULL on all four stats, reb slightly WORSE with CI above zero), TRACKING_SCREEN (passing,
shooting, rebounding tracking and hustle; T119-T123, T174-T175, no cell passes; best was reb -0.0013), F8 (expected on-court
rebounding environment adds nothing beyond teammate-OUT), the sim (usage, shot zones, possession rates; T001-T004, T061-T064).
Production already carries recency means of the four stats, minutes, role, rest, Elo, `r20_pts_for/against`, `opp_allow_<stat>`,
vacated usage. A TEAM-pace-only version of USAGE_PACE is exactly what FOUR_FACTORS A1 tested; what is new here is the PLAYER level:
on-court usage and per-100 rates, their trend/volatility/stint versions, shot and play-type mix, and matchup interactions.
Expected outcome: a null. The rule exists so that a null is a clean, closed one and a surprise is believable.

## Hypothesis
H1: as-of player-level advanced rates, represented as short time series (levels at three horizons, trend, volatility, current
team-stint level), lower integer-support CRPS of `props_context_residual` for their natural stats beyond the production features by
at least 0.005, one family at a time. Null: no change. Protects against: stacking seven families into one fit and crediting the
whole (one-family-at-a-time, no combined model); the repo's record that architecture-only ideas lose.

## T004: was the on-court usage rejection (+0.398) a bug or real?
Finding (code, git and data read; no outcome fitted): UNDETERMINED, and not reusable as evidence either way.
* It was a SIM-level number (`build_player_shot_rates(use_oncourt_usage=True)` into `player_points_sim_eval`, 15,350 player-games,
  pts CRPS +0.398 against a sim whose base is ~3.67), not a test of an on-court usage feature in the context-residual model. No
  CI bounds were stored (FDR audit family B: "CI excludes 0", no numbers; "cannot re-derive a p-value").
* It ran 2026-10-07/08, BEFORE three fixes that change exactly its inputs: the `played_only` fix (57d7a3c, 10-08 11:33; DNP rows
  were in numerators and denominators), the lineup tracker v2 rebuild of `stints` and possession lineups (43dba25, 10-09; v1 evicted
  players and mis-opened periods), and the stints rebuild for short appearances (10-10; 40% of sub-minute players and 5.8% of
  sub-10-minute appearances had no stint row). The DNP audit already says "on-court denominators were not DNP-audited".
* The recorded explanation ("over-concentrates the shot distribution once renormalized across the on-court five") was never
  tested. A +0.398 move (about 11% of CRPS, 10x any other effect in the ledger) is more typical of a scale or integration defect
  than of information loss: an on-court share averages ~0.19 and sums to about 1 across the five, a whole-game proxy is
  diluted by minutes, so swapping one for the other without re-scaling the sim's weights changes the distribution wholesale.
* Construction check on the CLEAN tables today (below, G3): its numerator and denominator both came from `possessions`, which
  collapses second-chance shots into one trip and undercounts team FGA by 12.6-13.5 per team-game (14%); so its denominator was
  not the box-consistent quantity either. This draft therefore does NOT reuse that SQL: on-court FGA/FGM/FTA/TOV come from PBP
  events x `stints` (reconciles to the box 100%), possession and rebound availability from `possessions` x lineups.
Verdict on reuse: the construction is rebuilt, not reused; T004 is neither cited as support nor as a prior against this rule. A
bug cannot be proven from the record and "real" cannot be either; a modern re-test would need its own rule (not this one).

## Fixed design (constants final)

Candidate frame: the production feature frame of `research/eval/four_factors_eval.load_study` (played rows, `n_prior >= 5`, same
rows/seed/month-block walk-forward, 2022 warm-up, 4 stats, integer support `ceil(q - 0.5)` for EVERY arm and every stat including
pts). Columns are appended through `stat_feature_names(stat, extra=...)` to the mean and scale heads; A0 names == production names
(asserted). No hyperparameter changes. All features are strictly as-of: played games of that player dated before the row's game
DATE (same-day excluded); non-played rows get features but never advance a state.

### Representation (identical for every stat; pre-declared, no sweep)
Per base stat `s` (a ratio `num/den` per played game, formulas below): `s_ew5, s_ew20, s_ew100` (ratio-of-sums EWMA at half-lives 5,
20, 100 played games, weight 0.5^(a/h), shrunk to the position-group as-of ratio with pseudo-count 5 games of the position's mean
denominator), `s_slope20` (OLS slope of the per-game ratio over the last 20 played games, valid games only, >= 6 valid else NaN),
`s_vol20` (sd, same window), `s_stint20` (half-life 20 EWMA over the CURRENT team stint only, restarts at a team change, shrunk to
the career-to-date ratio, itself shrunk to the position prior), and `adv_stint_n` (played games with the row's team). A game is
"valid" when `den >= 0.5 x` the position's as-of mean denominator. Position group = first letter of `players_static.position`
(G/F/C; unknown U). Code: `research/features/advanced_rates.py::asof_series`. Families with many base stats (SHOT_PROFILE
buckets) use the reduced representation named in the table. Team-level series (opponent allowed quantities) use `ew20` shrunk to
the league as-of ratio (pseudo-count 5 team-games), league = games dated strictly earlier.

### Stats: exact formulas and sources
`oc_*` = on-court team totals while the player is on court. Sources: BOX = `player_game_stats`; PBP = `data/pbp` events x `stints` by
rule R1 (an event at clock `sec` belongs to the stint with `end_clock <= sec < start_clock`; the own-player-in-own-stint check passes
99.99% for FGA); POSS = `possessions` x lineups at possession start. `oc_poss = (oc_poss_off + oc_poss_def)/2`.

| Stat | num | den | Source |
|---|---|---|---|
| usg (USG%) | FGA + 0.44 FTA + TOV | oc_FGA + 0.44 oc_FTA + oc_TOV | BOX / PBP x stints |
| pts100, reb100, ast100, fg3m100 | stat | oc_poss / 100 | BOX / POSS |
| pace48 (player on-court pace) | oc_poss | oc_min / 48 | POSS, stints |
| gmsc (Hollinger Game Score, per game) | PTS + 0.4 FGM - 0.7 FGA - 0.4 (FTA - FTM) + 0.7 OREB + 0.3 DREB + STL + 0.7 AST + 0.7 BLK - 0.4 PF - TOV | 1 | BOX |
| ts (TS%) | PTS | 2 (FGA + 0.44 FTA) | BOX |
| ppws | PTS | FGA + 0.475 FTA (0.475 is NOT verified against a primary source) | BOX |
| par3, ftr | FG3A ; FTA | FGA | BOX |
| ppr (Pure Point Rating, pace adjusted) | f x (2/3 AST - TOV), f = league possessions per 48 as-of / that team-game's possessions per 48 | MP / 100 | BOX, team-game possessions |
| ast_ratio (Hollinger assist ratio) | AST | FGA + 0.44 FTA + AST + TOV | BOX |
| tov_pct | TOV | FGA + 0.44 FTA + TOV | BOX |
| ast_pct (on-court) | AST | oc_FGM - FGM | BOX / PBP x stints |
| orb (OREB%) | OREB | oc team OREB + oc opponent DREB | BOX / POSS |
| drb (DREB%) | DREB | oc team DREB + oc opponent OREB | BOX / POSS |
| tr_share (transition share) | final-shot possessions of the player with length <= 7 s | all his final-shot possessions | POSS |
| tr_ppp | points of those fast possessions | their count | POSS |
| ast_share (assisted share of makes) | assisted makes | makes | POSS |
| putback_share | putback/tip FGA (PBP sub_type 'Putback...' or 'Tip...') | FGA | PBP / BOX |

Possession length = `clock_start - clock_end`; "fast" is the existing <= 7 s measure (FAN_KNOWLEDGE), used as the transition
definition. `possessions.shooter_id` is set on 100% of FG-ending possessions but only 10% of TOV rows, so no turnover-by-player
quantity is taken from `possessions` (TOV comes from BOX / PBP). PPR constants are from the Basketball-Reference glossary
(2/3 AST - TOV, per MP, x lg/team possessions per 48), as retrieved by a search summary, not read from Hollinger. Redundancy rule
(pre-declared, computed on features only): if the corr of `ppws_ew20` and `ts_ew20` exceeds 0.995 on 2024 played rows, `ppws` is
dropped. Measured 0.9994, so PPWS is DROPPED (it is TS% with a different FT weight); this is a feature-only finding, no outcome.

### Families, columns, natural stats (one-family-at-a-time arms; no combined model)
| Family | Columns | Target stats | Cells |
|---|---|---|---|
| USAGE_PACE | usg, pts100, reb100, ast100, fg3m100, pace48 (6 stats x 6) + adv_stint_n; team and opponent as-of pace48 (ew20); proj_poss = mean of the two; exp_plays = usg_ew20 x proj_poss x min10 / 48; exp_pts/reb/ast/fg3m = rate100_ew20 x proj_poss x min10 / 48 / 100 (46 cols) | pts, reb, ast, fg3m | 4 |
| EFFICIENCY | gmsc, ts, par3, ftr (4 x 6) + stint_n (25) | pts, fg3m | 2 |
| PLAYMAKING | ppr, ast_ratio, ast_pct, tov_pct (4 x 6) + stint_n (25) | ast | 1 |
| REBOUNDING | orb, drb (2 x 6) + stint_n; own team and opponent-allowed OREB%/DREB% (ew20) (17) | reb | 1 |
| SHOT_PROFILE | 5 bucket shares of FGA (rim, short mid, long mid, corner 3, above-break 3): ew20, ew100, slope20; 5 bucket FG%: ew100; 7 sub-type shares (dunk, hook, floater, stepback, pullup, layup, jump; putback excluded, see note): ew20; expected FG% from shot mix (league bucket FG% as-of, strictly earlier dates) ew20; shot-making (actual - expected) ew100; rim share and three share stint20; stint_n | pts, fg3m | 2 |
| DEFENSE_CONTEXT | opponent as-of allowed bucket shares (5) and allowed bucket FG% (5) (ew20); opponent defended-at-rim FG% and contested-FGA share (tracking); opponent contested shots per defensive possession (hustle, presence flag); interactions: exp_fg_vs_opp = sum_b share_b(player ew20) x (opp allowed FG%_b - league_b), rim_x_opp, three_x_opp (16) | pts, fg3m | 2 |
| PLAY_TYPE | tr_share, tr_ppp, ast_share, putback_share (4 x 6) + stint_n; opponent as-of fast-possession rate allowed (ew20); interactions tr_share_ew20 x (opp fast allowed - league), tr_ppp_ew20 x same (28) | pts, reb, fg3m | 3 |

Sub-type note (one home per column): the seven sub-type shares are dunk, hook, floater, stepback, pullup, layup, jump; the eighth,
putback (incl. tips), lives ONLY in PLAY_TYPE. Bucket and sub-type rules: `research/features/advanced_shots.py` (SQL and Python
mirrors tested equal). **m = 15 cells; seven families.** A family "passes" if at least one of its cells passes; the per-cell table
is the primary result. In-game momentum is not a feature (not knowable pre-tip); pre-game form is the EWMA/slope/vol columns.

Shot-location source (declared before the shots pull finishes, so it cannot be chosen after results): PRIMARY = PBP `shot_distance`
and `sub_type` (100% of 2022-24 field goals, 701,495 FGA). SWAP-IN = the x/y `shots` table (`game_event_id == action_number`, joined on
`is_field_goal = 1` rows only because block rows share the number) replacing the five buckets with the NBA zones (restricted area,
paint non-RA, mid-range, corner 3, above-break 3) ONLY if gate G9 passes before the freeze; Devin picks one at the freeze, never later.

Matchup defense (`player_game_matchups`, who guarded whom) is NOT used: 0 rows loaded, 3 cached game files. A pull is one request per
game (3,953 games for 2022-24; the endpoint, a free nba_api call, and its response shape are UNVERIFIED here), at the 3.5 s pacing
the coaches fetch uses about 3.8 hours, free, then a loader and a coverage gate. It is a separate rule once loaded.

## Arms, metrics, inference
* A0 production (reproduces the re-baselined references below). B(f, stat) = A0 + family f columns, per cell. P(f, stat) placebo =
  B with the family columns permuted across rows within player-season (keeps each column's marginal and its time-series shape, breaks
  the link to the game).
* Integer-support CRPS (199-quantile form, `ceil(q - 0.5)`) for every arm; paired dCRPS = CRPS(arm) - CRPS(A0); 95% bootstrap CI
  clustered by `game_id` (2000 draws, seed 0); two-sided clustered-bootstrap p; Benjamini-Hochberg across the 15 cells at q = 0.05,
  separately for 2023 and 2024. Bias, 80% PIT coverage, lower-tail PIT, threshold log loss reported (secondary, cannot rescue).
* Select 2023, report 2024: a cell advances only if 2023 point dCRPS <= -0.005 and its clustered CI upper < 0; the 2024 verdict
  needs the full rule. Season 2025 never loaded; `season <= 2024` asserted in SQL and code.
* Slices (descriptive plus the guard): starter / bench, cold start (5 <= n_prior < 10, n >= 300), first 15 team games, teammate-OUT,
  recent team change (adv_stint_n < 10, about 7% of rows), terciles of exp_plays (USAGE_PACE) and of opponent fast rate allowed.
* Per-cell MDE (1.96 x clustered SE) is printed beside each result; FOUR_FACTORS saw 0.0005-0.0028, below the floor, so the floor is
  testable.

## Pass rule (per cell; all must hold)
1. 2024 dCRPS point <= -0.005, CI upper < 0, BH p <= 0.05 over 15 cells; and the 2023 selection condition above.
2. Guards (match TRACKING_SCREEN): |cov80_B - 0.80| <= |cov80_A - 0.80| + 0.03; |bias_B| <= |bias_A| + 0.05 and <= 0.5; no
   slice with n >= 300 worse by more than +0.01; cold-start slice CI lower bound <= 0.
3. Attack gates: B beats the placebo P by a point <= -0.003 (else the gain is extra columns, not information); shuffled-label test
   (B refit with training residual labels permuted within month block) shows dCRPS >= 0 within MDE (run for every passing cell and
   for one control cell); planted-future and planted same-game tests pass in CI with non-inert controls; missingness audit (G6)
   passed.
4. Knockout and "single group carries the gain" red flag: for a passing cell, drop each base-stat group (all 7 columns of one stat)
   one at a time and add it alone; if dropping one group removes >= 80% of the gain, or one group alone keeps >= 80%, the cell is
   flagged "one group carries it" and is NOT a pass until the adversary clears it (the fingerprint of `opp_adjusted_ridge`).
5. Honest ceiling: only gain beyond the production columns counts (they already hold recency means, minutes, role, Elo, rolling
   team points, opponent allowed).
A PASS means only "worth a red-team review and a drafted one-touch holdout row". Nothing is registered, promoted or wired in; that
is Devin's.

## What each choice protects against
* One family at a time, 15 BH cells: a fishing expedition across ~170 columns.
* Fixed representation, no sweeps over half-lives, windows or priors: "tuning rarely matters" and variant chasing.
* Placebo permutation within player-season: extra columns helping through noise / capacity rather than information.
* Shuffled-label test and planted-future / same-game tests: pipeline leaks (the `opp_adjusted_ridge` NULL-pattern leak; the
  FOUR_FACTORS league-mean builder that carried one day of lag was found by exactly this test).
* Missingness audit by minutes bucket: hustle rows exist for 50% of sub-5-minute players and 98%+ of 10+ minute players (F9), and
  stints / possession lineups missed short appearances; any NULL that tracks tonight's minutes is a leak. Hustle is only ever read
  through `nba.data.side_tables.join_side_table` (presence flag) and only from strictly earlier games.
* On-court denominators from box-consistent sources: the T004 / possession-FGA undercount mismatch.
* Integer support for all arms, pts included: the T114-T118 artifact (continuous vs grid lost 92-126% of the "gain").
* Select 2023, report 2024 and the 2025 wall: selection bias.
* Floor -0.005 and the MDE column: a p-value on a gain nobody could use.

## Minimum-data gates (non-negotiable 8): measured 2026-10-11 on `nba.duckdb` (read-only) and `data/pbp`, seasons 2022-2024
No arm was fitted and no outcome beyond A0 was computed.
* G1 coverage: `possessions` games 1320/1320, 1318/1318, 1320/1320 (100%); lineups (`off_players` and `def_players`) present and
  5 distinct on 100% of possessions; `stints` games 1320/1318/1320 (100%). PASS. Note games season 2024 holds 1320 games.
* G2 on-court denominators exist: every played row has a stint row (null rate 0.0 in all minute buckets, mean |stint minutes - box
  minutes| <= 0.004); in `possessions` lineups 2.0-2.2% of sub-5-minute played rows have no possession (rebound/pace denominators
  are NULL there, never 0), 0.0% above 5 minutes. PASS (2 pp limit applied only above 5 minutes; the sub-5 gap is audited in G6).
* G3 reconciliation of sources: PBP FGA, TOV, FTA equal the box in 100% of 7,916 team-games; PBP event assigned to the stint of
  its own player 99.99% (FGA), 99.84% (TOV), 99.91% (FTA) under R1 (the other two boundary rules gave 99.7%/96.8%/99.7% and were
  rejected before use); sum over a team's players of oc_FGA equals 5 x team FGA in 99.96-100% of team-games; own FGA > oc_FGA in
  0.00-0.05% of player-games (sum 0.02%); usage = plays/oc plays mean 0.190, max 0.727, never above 1 (n = 76,754). `possessions`
  FGA is 12.6-13.5 per team-game BELOW the box (second chances collapsed): NOT used for FGA. `possessions` n_oreb / n_dreb equal the
  box team totals in 99.6-99.97% of team-games. Pace: on-court pace mean 98.4-99.3 per 48. PASS.
* G4 transition / assisted: `shooter_id` set on 100% of FG-ending possessions; shooter makes equal box FGM in 99.7-99.8% of
  player-games; assisted share of makes 55.0% / 57.1% / 58.4% (2022/23/24, plausible vs ~58% league). Fast (<= 7 s) share of
  possessions 59.5%. PASS.
* G5 A0 reproduction (run on today's data, 93 s on the Mac for all four stats, CPU): integer CRPS delta vs the re-baselined
  reference `reports/lower_tail/candidates.json` is 0.0 and n matches in all 8 cells: pts 2023 3.152830 (n 27,619), pts 2024
  3.187553 (n 27,701), reb 1.292817 / 1.314247, ast 0.905903 / 0.900215, fg3m 0.569049 / 0.592213. NOTE: Devin's quoted pts 2024
  3.1884 (n 27,583) is the pre-neutral-site-games value; production OOF was re-baselined on 2026-10-11 (DATA_CHANGELOG); the rule
  uses the new reference. PASS.
* G6 missingness audit (features only): all `*_ew20` columns built for 103,926 rows (17 stats, 8.4 s). NaN rate among played rows
  by minutes bucket (0-5, 5-20, 20+) and DNP rows by season: 2023 and 2024 max 0.0005 / 0.0003 (a first-career-game effect on DNP
  rows), 2022 (warm-up) up to 0.0027. Pre-declared threshold: max asymmetry between minute buckets <= 2 pp, and no more than 3x
  the spread of a placebo that permutes the played / DNP label within player-season (the placebo is run when the audit is run; the
  threshold is set before any outcome). Current max asymmetry 0.05 pp. PASS so far; the audit repeats for the team-level,
  tracking and hustle columns when they are built (they are not built yet).
* G7 tracking / hustle: `player_game_tracking` 1320 / 1318 / 1315 games, 34,024 / 34,815 / 34,946 rows; played rows with a tracking
  row 99.1-100%; `player_game_hustle` 1320 / 1318 / 1315 games, but the row exists for 50-54% of sub-5-minute played rows, 87-89%
  at 5-10, 98% at 10-20, 99.4-99.9% at 20+ (F9): hustle enters only as team-level sums over strictly earlier games through
  `join_side_table`. PASS with that constraint. Matchups: 0 rows loaded (see above).
* G8 per-team-stint sample sizes (test rows, `n_prior >= 5`): rows with a previous team in the data 32.9% (2023), 40.4% (2024); rows
  within the first 5 played games with the current team 3.8% / 3.6%, within 10: 7.0% / 6.9%, within 20: 12.8% / 12.4% (n = 27,619 /
  27,701). Team changes 223 (2023) and 212 (2024). The stint columns are NaN-free by construction (they fall back to career, then
  position). Enough rows for the recent-team-change slice (n ~ 1,000 for < 10 games). PASS.
* G9 shots (swap-in only): the pull is in progress (28 of 90 regular-season team-season files, 1,226 games). On those: event map to
  PBP 100% (on `is_field_goal = 1` rows), player agreement 100%, made agreement 100%, but `shot_distance` agrees with PBP within
  3 ft in only 93.8% of shots (the two buckets would not be identical). Not yet passable (coverage < 99% of 3,953 games); the
  PRIMARY rule does not wait on it. The swap-in gate is: all 90 RS files, >= 99% of games, map / player / made agreement >= 99.9%.
* G10 code gates (must be green before the freeze commit): on-court builder, play-type / shot counts and the time-series builder
  with hand-computed formula tests and planted-future / DNP / same-day / team-change tests exist (`research/features/advanced_*.py`,
  `tests/research/features/test_advanced_*.py`, 11 tests passing). NOT yet written: team-level series (team and opponent pace, PPR
  league pace factor, opponent allowed shares / FG% / fast rate, tracking and hustle sums), the three interaction groups, the
  assembly frame, and `research/eval/advanced_rates_eval.py` (gate, arms, report) with planted tests on the team-level pieces.
  The freeze should wait for them; none of them changes a number in this rule.

## Compute and runtime
Trees on the local CPU (Mac). Measured today: features for all 17 stats 8.4 s; A0 for four stats and two seasons 93 s including the
load; an arm is about 22-30 s per stat at 30 columns, expected 1-2 minutes with 40-50 columns. Plan: 4 A0 + 15 B + 15 P arms plus
conditional knockout / shuffle arms, about 35-60 arms, roughly 0.7-1.5 hours on the Mac, per-arm checkpoints, one process, under
`data/ops/heavy.lock`. Colab / GPU is not needed and not requested (no GPU job, no budget line).

## Kill criteria
* G6 or a planted test fails: stop, report a leak, run nothing further; the fix is a new rule, not an edit.
* No cell advances on 2023: close. Do not add half-lives, windows, priors, interactions, lineup-level versions or other stats
  afterwards. At most ONE pre-registered follow-up (a new id), then closed.
* A family that is NaN on more than 2 pp more of sub-5-minute rows than of 20+ minute rows is dropped before any fit.

## Holdout
Never touches 2025 inside this rule. A cell that passes everything and is cleared by the adversary earns a drafted (not appended)
one-touch holdout row and a shadow-log recommendation; the archivist logs it before any run.

## Unstated details (chosen before scoring)
* PPWS dropped by the redundancy rule (corr 0.9994); PPR league pace factor = league mean team possessions per 48 over games dated
  strictly before that game / the team's possessions per 48 in that game (overtime-adjusted as in FOUR_FACTORS).
* Rebound availability uses lineups at possession start (a mid-possession substitution is attributed to the starting five).
* Corner 3 = 3-pointer at <= 22 ft; rim = <= 4 ft; short mid 5-13 ft; long mid >= 14 ft (PBP distance).
* The first rows of 2022 have no position prior and are NaN; they are warm-up only.

=== RESULTS BELOW ===

(none; no fit has been run)
