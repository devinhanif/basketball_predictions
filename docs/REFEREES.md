# Referee crew tendencies: pre-registration

Status: PRE-REGISTERED 2026-10-09 BEFORE the data exist and before any referee number was computed.
Everything above the line `=== RESULTS BELOW ===` is frozen. Its sha256 is
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/REFEREES.md | shasum -a 256` and is recorded in the
ledger notice and the hand-off message. (The author may not commit; the maintainer commits. Freeze
evidence = sha256 + git blob.)

## What was checked before writing (2026-10-09)

* `nba.duckdb` (opened `read_only=True`): `game_officials` has **0 rows** (table exists, never loaded;
  `games` has 5,269 rows, seasons 2022-2025 = 1,320 / 1,318 / 1,315 / 1,316). `data/ops/ingest_queue_status.md`
  shows `cur:officials` and `load:officials` as `pending`. A per-game fetch cache `data/officials/` holds 3
  parquet files (games 0022200001-3); they were not opened and nothing was loaded.
* No referee-based feature, score, or plot has been computed by anyone in this repo (grep: no module reads
  `game_officials`; `player_game_stats.pf` and `fta` exist but no crew aggregation does).
* Season 2025 is NEVER loaded by this experiment (in-memory filter `season <= 2024`, as in docs/MINUTES_V2.md).
  Hence no row in `HOLDOUT_ACCESS_LOG.md` is needed. 2023/2024 are the repeatedly-seen non-holdout seasons: a
  pass here is evidence for shadow logging only.

## Hypothesis

H1: officiating crews differ persistently in how many fouls they call, how many free throws result, and the
pace of the games they work; a pre-tip crew summary therefore carries information about tonight's scoring
environment that the production features (player/team recency, pre-tip injury report, Elo margin) do not.

* (a) props. Stats and reasons, fixed now:
  * **pts** (primary): whistle -> FTA -> points, and pace -> possessions -> points.
  * **fg3m** (declared pace-only pathway): no free-throw channel; any effect must come through possessions.
    Expected small. Included because pace is one of the three crew features and it is a cheap, distinct test.
  * **reb, ast**: run as descriptive controls, NOT in the confirmatory family (rebounds/assists have a weak
    whistle channel and the same pace channel; a "win" there without pts would suggest a generic pace
    artefact; reported, cannot rescue or fail the verdict).
* (b) game total (points of both teams). Whistle + pace act directly on it. This is the cleanest test.
* (c) win probability: sanity check only. A crew should not tell us who wins beyond home-court noise.
  EXPECTED NULL. A "win" here is a red flag for a leak, never a result (see tripwire).

Null: no change. Prior expectation, stated up front: published crew effects on totals are about +/-1 point
between extreme crews after shrinkage, and the production props model already conditions on pace-related
recency; the expected CRPS gain is tiny and a null on props is the most likely outcome. The total head is
the only place a measurable effect is plausible, and it is under-powered (see Power).

## Data: exactly which tables and columns (all `read_only=True`, season <= 2024)

| table | columns used | role |
|---|---|---|
| `game_officials` | `game_id`, `official_id`, `name`, `jersey` | crew membership (mapping name -> `official_id` is 1:1; duplicates by name are a stop) |
| `games` | `game_id`, `game_date`, `season`, `home_team`, `away_team`, `home_pts`, `away_pts` | order, labels (total, win), team ids |
| `player_game_stats` | `game_id`, `team_id`, `player_id`, `minutes`, `pts`, `reb`, `ast`, `fg3m`, `fga`, `fta`, `oreb`, `tov`, `pf` | official's game-level rates (team sums over all rows with non-NULL minutes; DNP rows excluded) and prop labels |
| everything `nba.props.context_residual.build_features` already reads | unchanged | production control arm (no new columns) |
| `player_availability`, schedule parquet (`game_tipoff`) | unchanged | production real-tip gate (`tip_source="real"`) |

No other table or column. `game_era_flags`, tracking, hustle, matchups, coaches are not used.

## Frozen feature definitions

Game-level quantities, from box scores, both teams combined: `poss_g = 0.5*[(FGA+0.44*FTA+TOV-OREB)_home +
(...)_away]`; `pf100_g = 100 * (PF_home+PF_away) / (2*poss_g)`; `fta100_g = 100 * (FTA_home+FTA_away) /
(2*poss_g)`; `pace_g = poss_g`. (Team sums from `player_game_stats` rows with `minutes > 0`.)

Official rate: for official `o` and game `G` on date `d`, over all games `o` worked with `game_date < d`
(strictly earlier DATE; same-day games are excluded because they are not complete pre-tip), within the
loaded window (2022-10-18 onward; there is no pre-2022 data), `x_o = (n*mean_o + K*mean_league)/(n+K)` where
`n` = games `o` worked, `mean_o` the unweighted mean of the game-level quantity, `mean_league` the unweighted
mean over ALL games strictly before `d`, and **K = 30 games** (fixed, no tuning). No decay, no team
adjustment (stated limitation: crew rates are confounded with the teams they worked; the production
features already contain team/player rates). Before the first prior game of the league (opening day 2022)
`mean_league` = NaN and rows are warm-up.

Crew features (three columns, frozen names): `crew_pf100`, `crew_fta100`, `crew_pace` = unweighted mean of
`x_o` over the officials of `G` with non-NULL `official_id`, requiring >= 2 such officials, else NaN. An
official never seen before gets `x_o = mean_league` (so newness never creates NULLs). Win-sanity feature
`crew_home_margin` = same construction on the game's home margin (K = 30), used only in (c).

## Models and arms (fixed; no tuning; constants final)

Props: production `ContextResidualModel` (docs `nba/props/context_residual.py`; LightGBM 200 trees, lr 0.04,
15 leaves, min_child 100, ff/bagging 0.8, seed 0; month-block walk-forward, 2022 = warm-up,
`tip_source="real"`; integer-support quantiles for reb/ast/fg3m via `ceil(q-0.5)`; the pts arm as production
default). Arms, identical rows and seeds:
* **A0** production (control; must reproduce production pts integer CRPS 3.1528 / 3.1884 for 2023 / 2024 as in
  docs/LOWER_TAIL.md and docs/MINUTES_V2.md, tolerance 1e-4, else STOP);
* **A1** production + the three crew columns appended via `stat_feature_names(stat, extra=...)` behind a
  default-off flag;
* **A2** (noise control, descriptive) A1 with the three crew columns permuted jointly across games within season;
* **A3** (leak tripwire, descriptive) A1 with the crew features built from games ON/BEFORE `d` instead of strictly
  before; if A3 beats A1 materially, the strict as-of cut matters and the gain is attributable to same-day info.

Total head (new, defined here because none exists in production): target `T = home_pts + away_pts` per game.
Baseline `m` = recency estimate `(f_h + a_a)/2 + (f_a + a_h)/2` with `f_t` / `a_t` the exponentially weighted
(half-life 10 games, `0.5 ** (games_ago/10)`, season-scoped, shrunk to the as-of league mean with pseudo-count
10 games) points scored / allowed by team `t`. Distribution: `T ~ m + mu_hat(x) + s_hat(x)*Z`, same recipe as
`ContextResidualModel` (LightGBM mean-residual head, |residual| scale head, empirical standardized residuals
on the newest-15% calibration window), quantiles on the 199-grid, **integer support** (`ceil(q-0.5)`) for both
arms because totals are integers. Features x (all as-of / pre-tip): `m`, both teams' rest days and b2b,
`abs_margin` and `exp_margin` from the injury-Elo/MOV-Elo module, home and away `n_out_rot` from the pre-tip
report (real tip), day-of-season, game type. T0 = this head; T1 = T0 + the three crew columns; T2 = noise
permutation control (descriptive). Month-block walk-forward, rows = games with both teams >= 10 prior games
in the season or the prior season (warm-up 2022).

Win (sanity): injury-Elo logit (production, `nba/models/injury_elo.py`) vs the same logit plus `b*crew_home_margin`,
`b` a ridge-logistic (fixed lambda as injury-Elo coefficient fit) on prior games, offset fixed. Missing crew ->
feature 0 (the league mean after centring).

## Missing crew rows (must be handled IDENTICALLY in both arms)

* The evaluation row set is fixed by the CONTROL arm (A0/T0/win-base) and never filtered by crew coverage.
  The treatment arm sees the SAME rows; games without a usable crew have NaN crew columns (LightGBM native
  missing) / 0 (win ridge). No `has_crew` indicator is added (it would let the model learn from the
  missingness itself).
* Primary results are on all rows; "covered-only" (rows with usable crew) is reported as a second column. If the
  two disagree in sign on the point estimate, flag as fragile.
* Missingness audit (before any model; its output hash is logged first): per season and per month the
  share of games with no usable crew; the share by home team; the share among games whose total is in the top/bottom
  quartile; for player rows, the share by played/unplayed (DNP) and by starter. Tests: two-sample difference in
  mean total, mean |margin|, mean `pf100_g`, and DNP rate between missing and non-missing games (game-clustered
  bootstrap), and AUC of missingness predicted from {total, margin, pace, month, home team} by logistic
  regression (blocked by season). **STOP** (no experiment) if any of: the missingness AUC is outside [0.44, 0.56]; any of the outcome means differs by > 0.1 SD with CI excluding 0;
  the played/unplayed (DNP) rate differs by > 2 percentage points between missing and non-missing games, or the
  NULL pattern of crew columns is correlated with `minutes IS NULL` in any way; or any outcome-correlated NULL asymmetry
  that was not enumerated here.

## Minimum data completeness before ANY model is run (gate G0)

1. `game_officials` loaded for 2022-2024 with >= 98% of the 3,953 games having >= 3 distinct non-NULL
   `official_id`, >= 97% in each season, and no calendar month < 95%; <= 1% of games with 1 or 2 officials.
2. Name <-> `official_id` is 1:1 (no id with two names; same-name ids resolved by jersey, as in the collector).
3. `player_game_stats` for those games has non-NULL `pf` and `fta` for >= 99.5% of rows with minutes.
4. A0 reproduces production CRPS (see above).
G0 is computed, written to `reports/referees/g0.json`, and its sha256 logged BEFORE any arm is fitted. If G0
fails the experiment is not run (not "run anyway"): re-pull and re-audit.

## Metrics, inference, pass rules

* Props: integer-support CRPS for EVERY arm (BEST_PRACTICES); paired delta A1 - A0, game-clustered bootstrap
  (cluster = `game_id`; B = 2000, seed 0); secondary continuous CRPS and threshold log loss at the LOWER_TAIL
  thresholds.
* Total: integer-support CRPS delta T1 - T0, same bootstrap (cluster = game; one row per game); secondary
  log score of P(T >= round(m)), MAE of the mean.
* Win: log loss delta (game bootstrap), tripwire only.
* Selection season 2023, report season 2024: an arm is selected only if it passes on 2023; 2024 is the
  confirmation; if 2023 fails, 2024 is reported but cannot upgrade the verdict.
* **Confirmatory family** per season: {pts, fg3m, total} = 3 tests, Benjamini-Hochberg q = 0.05. reb, ast,
  A2/A3/T2, win, covered-only and slices are descriptive (named, not corrected, labelled so).
* Pass per (test, season): (1) dCRPS point <= **-0.005** (props, integer-support CRPS units) or <= **-0.05** points
  (total, ~0.5% of CRPS); (2) CI upper < 0; (3) BH p < 0.05; (4) guards: |bias| <= 0.5 (props) / <= 1.0 point
  (total), 80% interval PIT coverage in [0.75, 0.85], no slice (cold start `n_prior < 20`, starters/bench, first 15
  team games, crew-covered vs not, top/bottom quartile `crew_pace`) with n >= 300 rows (>= 100 games for total)
  worse by > +0.01 (props) / +0.10 (total); (5) A2 noise control does not show a gain of more than half
  the A1 gain.
* **Verdict**: PASS = at least one confirmatory test passes on 2023 AND 2024; consequence: shadow logging and
  red-team only, never promotion. FAIL otherwise, reasons check by check. **UNDERPOWERED** is a permitted verdict
  (see Power) and is not a negative result.
* Win tripwire: if the win delta has CI upper < 0 on both seasons, do not report a win; trigger a leak audit
  (A3 comparison, crew-date audit) before any other statement. A non-significant win is the expected result.

## Power / minimum detectable effect (stated before data)

Props n ~ 27.6k player-games / ~1,318 games per season. From prior experiments with the same design
(T166, T167: game-clustered CI half-width ~0.003 on pts, ~0.0012 on reb/fg3m) the SE is ~0.0015 (pts), so an
80%-power MDE of ~2.8*SE ~ 0.004 CRPS: just below the -0.005 floor, so the floor is roughly the detectable
threshold. For the total head n = ~1,315 games per season; with a CRPS of ~10.5 and paired-difference SD
assumed 1.0-1.5 points per game (to be replaced by the measured SD in the results), SE ~ 0.03-0.04, MDE ~
0.09-0.11 points: **above** the -0.05 floor. A true crew effect of +/-1 pt between extreme crews yields
an expected total-CRPS gain well under 0.05, so the total test is expected to be UNDERPOWERED: a CI spanning
0 there means "no power", not "no effect". Win: log-loss SE ~0.004 per season; anything below ~0.011 is
undetectable. Slice tests below n ~ 1,500 rows are descriptive.

## Kill criteria (stop and report, do not iterate)

* G0 fails, the missingness STOP fires, or A0 does not reproduce production.
* Any feature uses a game on/after the target date (planted-future unit test fails).
* A3 (same-day-inclusive) differs from A1 by more than the floor AND A1 passes: the result is declared
  contaminated.
* A feature, K, floor or family is changed after the first number is viewed: the run is void.
* No second pass on 2023/2024 with different constants. A different K or feature set is a new pre-registration.

## Tests required (before data): planted-future crew rows, same-day exclusion, `official_id` NULL handling,
flag-off byte-identity of production features and predictions, K -> infinity gives league mean, newness gives
league mean, permutation control keeps column marginals.

## Forward plan (data collection only; scoring needs a separate frozen addendum)

* `nba.ingest.referees collect` (hourly, in the `pretip` branch) stores assignments in `data/refs/refs.duckdb`
  from **2026-10-20** (first slates). A forward row's crew = the latest snapshot with `fetched_at < tip - 60 min`.
* Live mapping: the collector maps names to `official_id` via `game_officials` history; unmapped officials stay
  NULL and the CLI exits 2; `remap` fills them once `load:officials` has run. Under the frozen rule, a crew with
  fewer than 2 mapped officials yields NaN features (same code path as a backtest game with missing officials).
* Late crew changes: the backtest uses the crew recorded after the game (a replacement is possible and was
  unknown at publication, a mild optimism). Before any forward claim, measure assigned-vs-recorded agreement
  (the share of games whose assigned three equal the recorded three, and the share of official-slots
  changed); require >= 95% game-level agreement or report the degradation by re-scoring the 2022-24 backtest
  with a simulated 5% swap (swap official with a random official) as a stress test.
* Forward scoring on the 2026-27 slate would require its own addendum frozen before the first scored forward
  row, using FORWARD_PREREG_2026_27.md conventions. Nothing in this document is a forward claim.

=== RESULTS BELOW ===

(none yet)
