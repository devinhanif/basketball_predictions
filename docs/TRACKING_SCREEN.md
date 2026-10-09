# Tracking / hustle as-of features: pre-registered screen

Status: PRE-REGISTERED 2026-10-08 (written before any tracking or hustle value was loaded,
read or summarised; only the ingest column lists in `nba/ingest/postgame.py` were consulted).
Everything above the line `=== RESULTS BELOW ===` is frozen; the sha256 of that frozen part
is recorded in the results section and in `reports/tracking_screen.md`.

## Hypothesis

H1: post-game player tracking and hustle data, used as strictly as-of features (only games
before the target game), carries information about a player's next-game pts / reb / ast /
fg3m that the production feature set of `props_context_residual`
(recency means, minutes, role, rest, Elo, injury-report vacated usage, opponent allowances)
does not already contain, and adding it lowers CRPS by a practically meaningful amount.

Null: no improvement (the recency box-score averages already summarise what the tracking
rates would add). A negative or null result is the expected, acceptable outcome.

## Fixed feature definitions (no tuning, no alternatives)

Source tables: `player_game_tracking` (T) and `player_game_hustle` (H), loaded into
`nba.duckdb`. Coverage gaps are by game (a game either has rows or not).

For a played row (player_game_stats `minutes > 0`) of player p in game g, using only p's
PLAYED games strictly before g (ordered by game_date, game_id):

* Per-game observation x_j of a base column and minutes m_j (player_game_stats minutes).
  A game j contributes to a column only if a tracking/hustle row exists for (j, p) and the
  column value is non-null.
* Recency weight of game j = 0.5 ** (a / 10), where a = number of PLAYED games of p between
  j and g counting j (the immediately preceding played game has a = 1). Half-life 10 played
  games. A played game without a tracking row has weight 0 for that column but still ages
  the others (the clock is played games, as in production `recency_avg`).
* Count-type columns: rate r = 36 * sum(w*x) / sum(w*m) (minutes-weighted per-36).
  Mean-type column (`speed`): r = sum(w*x*m) / sum(w*m) (minutes-weighted mean, no x36).
* Effective sample W = sum(w) over contributing games. Shrinkage toward the position-group
  mean with pseudo-count k = 5 games: r_shrunk = (W*r + k*mu_pos) / (W + k).
  mu_pos is the same minutes-weighted rate (equal weights, no decay) over ALL tracked
  player-games strictly before the calendar date of g, in p's position group (G/F/C from
  `players_static.position`, first letter; unknown = its own group). If no prior data exist
  at all (first day of the data) the feature is NaN; W = 0 otherwise gives mu_pos exactly.
* Shares are derived from the shrunk rates, not re-estimated.
* Each family also carries `trk_neff = log1p(W_tracking)` (T families) and/or
  `hus_neff = log1p(W_hustle)` (H families), W taken from the first count column of the
  source, so the model can learn how much to trust the rates (CLAUDE.md cold-start rule).
  Both are floats, never NULL.

Families (all column names are the ingest names):

| Family | Columns | Sources | Target stats |
|---|---|---|---|
| passing | per-36: `passes`, `touches`, `secondary_assists`, `free_throw_assists` | T | ast, pts |
| rebounding | per-36: `rebound_chances_offensive`, `rebound_chances_defensive` (T); `offensive_box_outs`, `defensive_box_outs` (H) | T+H | reb |
| shooting | per-36: `contested_field_goals_attempted`, `uncontested_field_goals_attempted`, `defended_at_rim_field_goals_attempted`; derived `contested_share = c/(c+u)` | T | pts, fg3m |
| activity | per-36: `distance`; mean: `speed` (T); per-36: `deflections`, `screen_assists`, `loose_balls_recovered_total` (H) | T+H | pts, reb, ast, fg3m |

Deviations from the task brief, forced by what the ingest actually stores (decided before
looking at data): `potential_assists`, `contested rebounds` and a `contested_fg` percent
are NOT in the ingested schema; passing uses passes/touches/secondary/FT assists,
rebounding uses rebound chances and box-outs. Nothing was added after seeing values.

Cells (family x target stat): passing x {ast, pts}; rebounding x
{reb}; shooting x {pts, fg3m}; activity x {pts, reb, ast, fg3m}. **m = 9 cells.**

Hustle fallback (pre-declared): if hustle is not fully loaded by about 7 h after the start
of the run, the H-dependent families (rebounding, activity) are run with their T-only
subset columns (rebound chances; distance, speed) and labelled `rebounding_T`,
`activity_T`; they REPLACE the full-definition cells (m stays 9). Hustle-only columns
would then be reported NOT RUN. If hustle is complete, only the full definitions run.

## Procedure

* Production arm A: `props_context_residual` exactly as in `configs/context_residual.yaml`
  (200 trees, lr 0.04, 15 leaves, min_child 100, seed 0; features
  `stat_feature_names(stat)`).
* Candidate arm B: identical config, hyperparameters, seed, rows and folds; features =
  `stat_feature_names(stat)` + the family columns (all columns of the family, for each
  of its target stats). One-family-at-a-time; no combined-family model.
* Walk-forward: calendar-month blocks over seasons 2023 and 2024, each block fit on all
  played rows strictly before it (2022 = warm-up), newest 15% of the training window is
  the conformal calibration window (the production procedure, `walk_forward_stat`).
* Rows: played rows with >= 5 prior played games; IDENTICAL row set in both arms (all
  such rows, regardless of whether the game or the player's history has tracking
  coverage). Tracking coverage gaps therefore enter both arms the same way: the
  tracking-derived columns are simply built from fewer prior games (or are shrunk fully
  to the position mean at W = 0); arm A never sees them. A sensitivity restricted to
  test rows whose own game has tracking data is descriptive only.
* Selection: nothing is selected. Definitions above are fixed. 2023 is reported
  descriptively (same-sign check) and is not used for any decision. **Report season: 2024.**
* Season 2025 (frozen holdout) is NEVER loaded: loaders filter `season <= 2024` in SQL
  and the code raises if 2025 rows appear. No holdout touch; a later one-touch
  confirmation needs a new row in `docs/HOLDOUT_ACCESS_LOG.md` written by the maintainer.
* Metric: per-row CRPS (199-quantile form, `nba.eval.context_residual_eval`), paired
  delta = CRPS(B) - CRPS(A), 2024 rows; 95% bootstrap CI clustered by game_id (2000
  resamples, seed 0); two-sided clustered-bootstrap p; Benjamini-Hochberg across all
  m = 9 cells at q = 0.05.

## Decision rule (all must hold for a cell to be a screen PASS)

1. Practical floor: mean CRPS delta <= -0.005.
2. Clustered 95% CI upper bound < 0.
3. BH-adjusted p <= 0.05 over the 9 cells.
4. 80% interval coverage guard: |cov80_B - 0.80| <= |cov80_A - 0.80| + 0.03.
5. Bias guard: |bias_B| <= |bias_A| + 0.05 and |bias_B| <= 0.5 (stat units).
6. Cold-start slice guard: rows with 5 <= prior played games < 10 (n >= 300 required to
   judge; below that reported as "n/a, not judged"): the slice delta's 95% clustered CI
   lower bound must be <= 0 (B is not significantly worse).
7. Missingness audit (below) passed.

A PASS means only "worth a red-team review and a pre-registered holdout touch". It is
never wired into production, registered or promoted from this screen. Verdicts are per
cell; a family is called "PASS" if at least one of its cells passes; the per-cell table
is the primary result.

## Missingness audit (run on the built feature frame BEFORE any model is fit)

For every tracking-derived feature column and `trk_neff`/`hus_neff`: NULL rate among rows
with minutes >= 5 versus rows with 0 < minutes < 5 (the `nba.datamanifest` played
threshold, `configs/datamanifest.yaml`), asymmetry = |difference|. Also the asymmetry of
"own game has a tracking row for this player" (diagnostic, uses post-game coverage only
to detect structural leaks, never as a feature). Any asymmetry >= 0.5 = STOP, report a leak,
run nothing further. Also reported: NULL-indicator vs label rank correlation per feature.

## Data / compute notes

* Fetch is running in the background under caffeinate and is not touched. Only after the
  tracking fetch's completion line is logged is `postgame-load --source tracking` run
  (and `hustle` if done), then `datamanifest snapshot` and `check`.
* CPU only, heavy-job lock `data/ops/heavy.lock`, `nba.duckdb` opened `read_only=True`.
* Operating constraint of this run: no git commit is made by the agent; the maintainer
  commits. The freeze is evidenced by the sha256 recorded below, taken before any load.

=== RESULTS BELOW ===

## Results (run 2026-10-09, data_version e2afd65697e7)

Frozen-part sha256 (everything above the marker, verified unchanged at run time):
`090e3a069da78992c849d00e0f192d23b69d4ce161f09aa697bea9343d0190ad`; pre-registration
commit c88788a.

**Amendment (made before any model result was seen, on the coordinator's instruction):**
only the tracking-only families were run: `passing`, `shooting`, and the pre-declared
T-only subset `rebounding_T` (rebound chances; box-outs need hustle). `activity` (full or
`activity_T`) and the full `rebounding` definition are NOT RUN YET (hustle fetch still
running). BH is therefore over the m = 5 cells that ran (the registered m = 9 applies when
all cells are run). Same family definitions, rule and floors otherwise.

Data: tracking loaded for 3953/3953 games of 2022-2024 (1320 / 1318 / 1315 per season; 103,785
player-game rows), 100% of played rows have a tracking row, so coverage gaps are nil and
both arms see identical rows. Datamanifest check: 0 flags.

Missingness audit: max NULL-rate asymmetry 0.0000 (threshold 0.5): features are shrunk to
the position prior and are never NULL after the first date; own-game row coverage is 100%
for both minutes >= 5 and < 5. No leak signal.

| cell | n (2024 played rows) | CRPS A (prod) | CRPS B (+family) | delta B-A [95% clustered CI] | raw p | BH p (m=5) | cov80 A/B | bias A/B | cold (<10 prior) n: delta [CI] | 2023 delta (descr.) | checks failed | PASS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| passing|ast | 27583 | 0.9164 | 0.9162 | -0.0002 [-0.0011, +0.0009] | 0.774 | 0.774 | 0.796/0.794 | +0.037/+0.015 | 463: -0.0139 [-0.0210, -0.0071] | -0.0015 | floor, ci_excludes_0, bh_q | NO |
| passing|pts | 27583 | 3.1890 | 3.1906 | +0.0016 [-0.0004, +0.0035] | 0.123 | 0.205 | 0.795/0.794 | +0.045/+0.044 | 463: -0.0023 [-0.0146, +0.0110] | +0.0007 | floor, ci_excludes_0, bh_q | NO |
| shooting|pts | 27583 | 3.1890 | 3.1909 | +0.0018 [-0.0002, +0.0039] | 0.097 | 0.205 | 0.795/0.790 | +0.045/+0.031 | 463: -0.0018 [-0.0126, +0.0099] | +0.0003 | floor, ci_excludes_0, bh_q | NO |
| shooting|fg3m | 27583 | 0.6150 | 0.6153 | +0.0003 [-0.0003, +0.0009] | 0.335 | 0.419 | 0.791/0.789 | -0.010/-0.011 | 463: -0.0039 [-0.0079, +0.0004] | +0.0002 | floor, ci_excludes_0, bh_q | NO |
| rebounding_T|reb | 27583 | 1.3249 | 1.3237 | -0.0013 [-0.0022, -0.0004] | 0.009 | 0.045 | 0.800/0.799 | +0.032/+0.031 | 463: -0.0018 [-0.0081, +0.0045] | +0.0006 | floor | NO |

Verdict: NO cell passes. Four of five cells have CIs spanning 0 (two point estimates are
slightly worse: passing->pts +0.0016, shooting->pts +0.0018). rebounding_T->reb is the only
BH-significant cell (-0.0013, BH 0.045) but is far above the -0.005 floor. The cold-start
slice for passing->ast shows -0.0139 (CI excludes 0) on only n = 463 rows, but that is a
slice of a null overall result, not a pass criterion. Guards (coverage, bias, cold start)
were not violated by any cell. Honest reading: recency box-score means already contain
what these as-of tracking rates carry; no signal. No holdout touch, nothing registered.


## Results: hustle families (2026-10-09, run after hustle loaded for 2022-2024)

All five hustle-dependent cells NOT KEPT (T174-T175; `reports/tracking_screen_h/`). Missingness audit max asymmetry 0.0. Only descriptive lead: rebounding features on the cold-start slice (n=463) -0.0126 [-0.0193,-0.0057]; would need its own pre-registration. Screen closed.
