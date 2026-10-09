# Training-window extension (2019-20 .. 2021-22): pre-registration

Status: PRE-REGISTERED 2026-10-09 BEFORE the history box scores exist and before any extended-window number
was computed. Everything above `=== RESULTS BELOW ===` is frozen. sha256 of the frozen part:
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/HISTORY_WINDOW.md | shasum -a 256` (recorded in the ledger
notice and the hand-off). The maintainer commits; freeze evidence = sha256 + git blob.

## What was checked before writing (2026-10-09)

* `data/history/nba_history.duckdb` **does not exist** (read-only open fails: "database does not exist").
  `data/history/` holds only `availability_official/` (1,144 cached injury-report PDFs), `availability_backfill/`
  (70 per-date manifests), `history_injury_fetch.log`. The ingest queue shows `hist:games` / `hist:boxscore`
  for 2021, 2020, 2019 as `pending`. Hence there are no 2019-2021 box scores, games, flags or parsed reports.
* `nba.duckdb` has `game_era_flags` with 0 rows and contains seasons 2022-2025 only. Production never receives
  history rows (docs/NEW_DATA_SOURCES.md).
* **Real tip times for history seasons do not exist yet**: `data/schedule/raw_*.parquet` covers 2022-23 .. 2025-26
  only. See gate G0.5: this is the single largest leak trap for the report features.
* No window-extension result of any kind has been computed. Season 2025 is never loaded (filter `season <= 2024`
  in memory for both DBs); no HOLDOUT_ACCESS_LOG row needed. 2023/2024 are repeatedly seen non-holdout seasons.

## Hypothesis

H1: three more seasons of training rows (2019-20, 2020-21, 2021-22; +~3,500 games, roughly +85% rows) improve
the production props model `props_context_residual` (recency average + LightGBM residual) on 2023 and 2024
walk-forward, either with the era flags as features (arm W1) or with flagged games down-weighted (arm W2).
H2 (secondary, descriptive): the same extension improves the injury-Elo coefficients (win log loss).
Counter-hypothesis stated up front: the COVID seasons (shortened, bubble, empty/limited arenas, different
3-point volume and pace) are a different data-generating process; the GBM residual head may learn
era-specific structure that does not transfer, and the recency average already carries most of the signal, so
the expected gain is small and a null or a harm is plausible. Prior pass probability ~20%.

## Data: exactly which tables and columns (both DBs `read_only=True`; neither is ever written)

| source | table | columns | note |
|---|---|---|---|
| `data/history/nba_history.duckdb` | `games` | `game_id`, `game_date`, `season`, `home_team`, `away_team`, `home_pts`, `away_pts` | seasons 2019-2021 |
| | `player_game_stats` | every column `build_features` reads (`minutes`, `pts`, `reb`, `ast`, `fg3m`, `starter`, `pf`, `fta`, `fga`, `oreb`, `tov`, `team_id`, `player_id`) | DNP rows: `minutes` NULL, stats 0, as in production |
| | `player_availability` | `player_id`, `team_id`, `as_of`, status columns used by `latest_pretip_flagged`, `source='nba_official_report'` | the PDF backfill only |
| | `game_era_flags` | `game_id`, `season`, `covid_bubble`, `no_fans`, `limited_fans`, `shortened_season` | rule table of docs/NEW_DATA_SOURCES.md; `notes` unused |
| | `players_static` | `player_id`, `birth_date`, `draft_year`, `draft_pick`, `position` (read for slices and for existing features) | union with production |
| `nba.duckdb` | same tables, seasons 2022-2024 only | | `game_era_flags` rows for 2022+ are all-False by the rule (taken from `data/history/game_era_flags.parquet`, because the `nba.duckdb` table is empty) |
| schedule | `data/schedule/raw_*.parquet` + a NEW schedule pull for 2019-20..2021-22 | `gameId`, `gameDateTimeUTC`, `gameDateTimeEst` | via `nba.features.game_tipoff` (real tip) |

Nothing else. In particular no tracking/hustle/officials/matchups columns.

## Arms (training data differs; evaluation rows are identical)

Evaluation rows (fixed by the CURRENT-window arm and never changed): played rows in 2023 and 2024 with
>= 5 prior played games under the current window (the production props row set; ~27.6k per season per stat).
All arms are scored on exactly these `(game_id, player_id)` keys per stat. An arm that cannot score a key
is a STOP (no silent row loss; row counts and key hashes are logged).

* **W0** current window: games from 2022-10-18 only; production code, seeds, features (control; must reproduce
  production pts integer CRPS 3.1528 / 3.1884 for 2023 / 2024, tolerance 1e-4, else STOP).
* **W1** extended window (2019-20 onward), plain history + the four era flags as numeric 0/1 features
  (`covid_bubble`, `no_fans`, `limited_fans`, `shortened_season`) appended after the production columns.
  They are constant (0) on every 2022+ row, so on the evaluation rows they can only partition the training rows;
  this is the intent.
* **W2** extended window, NO era features, flagged training rows down-weighted: LightGBM sample weight
  **0.5** for any game with `covid_bubble OR no_fans OR limited_fans OR shortened_season`, 1.0 otherwise
  (fixed, no tuning; because `shortened_season` is asserted for all of 2019-20 and 2020-21, this halves the weight of
  every game of those two seasons and leaves 2021-22 and later at 1.0). Applied to the mean and scale heads and the
  mixture/classifier heads that production fits; the calibration window (newest 15% of training rows, always
  2022+ for the 2023/2024 folds) is unweighted.
* **W3** (descriptive, outside the family) extended window, plain: no flags, no weights. Decomposes whether the
  flags matter at all.
* **W1r** (descriptive sensitivity for the report-coverage trap, below) = W1 with history training rows that
  lack an official report dropped.

Held fixed in all arms: the recency estimator (`0.5 ** (games_ago/10)` played-only mean, `HALFLIFE = 10`), the
hyper-parameters (200 trees, lr 0.04, 15 leaves, min_child 100, ff/bagging 0.8, seed 0), month-block
walk-forward (train strictly before the block's first day, minimum 1,500 rows, calibration = newest 15% on a
date boundary), `tip_source="real"`, the stat definitions and the eligibility rule. Training rows of the
CURRENT arm are unweighted in production; there is no recency weighting of training rows to hold fixed beyond the
half-life-10 feature, and none is added. The window and feature history of an arm are one thing: the extended
arms' player states and Elo replays (`exp_margin`) see the 2019+ games (so a veteran's first 2022 games have
history), the Elo replay itself is not flag-weighted. This is the treatment, not a leak: test-row features
are still strictly as-of.

## Injury-report coverage audit (era-coverage asymmetry; gate G2 and G3)

The official-report PDFs for 2019-2021 differ by era (three snapshots a day in 2019-20 and 2020-21, hourly in
2021-22; bubble hours differ) and the pre-tip gate is `as_of <= real tip - 60 min`. If the fraction of rows with a
usable pre-tip report differs by era, the report features (`has_report`, `n_out_rot`, `vac_*`, `since_flag`,
`flag_dir`, `gap_days`) carry era identity, and since the era also moves outcomes (pace, 3PA rate), this can
manufacture a gain that is not information. Mechanics frozen:

1. Per season (2019-2024): share of GAMES with >= 1 report snapshot before `tip - 60`; share of PLAYER ROWS with
   `has_report = 1`; snapshots per game; median snapshot lead before tip. Report as a table.
2. `has_report` is ALREADY in production `COMMON_FEATURES`, so it is in every arm including W0; this is
   stated here as the requirement "has_report in BOTH arms". If any history season's game-level coverage
   differs from the 2022-24 pooled coverage by more than 2 percentage points, that fact is stated in the results
   and W1r is run (descriptive). No other report-feature change is made.
3. Label-correlation check (STOP rule): among rostered player rows, compare `P(played | has_report=1)` vs
   `P(played | has_report=0)` per season and the played-rate by report-snapshot count. If the history seasons'
   gap differs from the 2022-24 gap by > 5 percentage points, or the NULL pattern of any report feature is
   correlated with `minutes IS NULL` more strongly in history than in 2022-24 (|diff| of phi > 0.05), **STOP**:
   the extension cannot be evaluated cleanly. Also STOP if a classifier trained on the report columns alone
   (has_report, n_out_rot, vac_*, since_flag, flag_dir, gap_days) distinguishes history from current rows with
   AUC > 0.75 AND those columns' correlation with the residual label differs in sign between eras
   (the "distinguishable in a way correlated with labels" test).
4. Parsing parity: unresolved-name rate of the history parser <= the production value + 1 percentage point; the
   history `NameResolver` log is part of the audit. Spot-check 20 random dates against raw PDFs.
5. The audit is computed and its sha256 logged BEFORE any arm is fitted.

## Minimum data completeness before running (gate G0)

* G0.1 history DB: `games` for 2019-2021 complete versus the cached schedule (every `game_id` in the cached game
  list present, no duplicates), `player_game_stats` rows for >= 99.5% of those games, `pf`/`fta`/`fga` non-NULL
  on >= 99% of minutes>0 rows, DNP handling identical (NULL minutes + 0 stats), DNP share of rostered rows within
  +/-3 points of the 2022-24 range.
* G0.2 `game_era_flags` present for 100% of history games and 2022-24 games (all-False), rule version recorded.
* G0.3 possession reconciliation for history seasons within the production envelope (MAE <= 3.0).
* G0.4 PDF backfill: every manifest date in the 707-date calendar done; `player_availability` loaded in the
  HISTORY DB with unmatched-name rate <= production + 1 point; coverage audit table above complete.
* G0.5 REAL tip times for >= 99% of history games (new schedule pull for seasons 2019-21). **No fallback to the
  19:00 ET proxy for history games**: the proxy admits post-tip reports for ~7.7% of games (BEST_PRACTICES), and
  the same defect in history rows alone would again be era-correlated. If real tips are unavailable the
  experiment is not run.
* G0.6 W0 reproduces production (above).
If any gate fails: no arm is fitted.

## Metrics, inference, pass rules

* Props, per stat in {pts, reb, ast, fg3m}: integer-support CRPS for EVERY arm (pts as in production default;
  reb/ast/fg3m `ceil(q-0.5)`), paired delta (arm - W0), game-clustered bootstrap (B = 2000, seed 0; cluster = `game_id`);
  per season 2023 (selection) and 2024 (report).
* **Confirmatory family** per season: {W1, W2} x {pts, reb, ast, fg3m} = 8 tests, Benjamini-Hochberg q = 0.05.
  Secondary (descriptive, uncorrected, labelled): W3, W1r, continuous CRPS, threshold log loss at the LOWER_TAIL
  thresholds, calibration/ECE for P(stat >= N).
* Pass per (arm, stat, season): dCRPS <= **-0.005**; CI upper < 0; BH p < 0.05; guards: |bias| <= 0.5; 80% interval
  PIT coverage in [0.75, 0.85]; no slice with n >= 300 worse than W0 by > +0.01 CRPS.
* **Verdict**: an arm PASSES if >= 1 stat passes on 2023 AND 2024. PASS => shadow-logging proposal and red team,
  never automatic promotion (an extended-window production refit also changes every other daily artefact and needs
  its own review). If both pass, prefer the arm with fewer extra parameters (W2) unless W1 beats W2 with a CI
  excluding 0. **HARM tripwire**: any stat with dCRPS >= +0.005 and CI lower > 0 in either season is reported as
  "extension harms" and blocks any recommendation for that arm. FAIL otherwise, check by check.
* **Slices** (reported for both seasons; the no-harm guard applies at n >= 300, n smaller descriptive):
  * rookies / cold start: `n_prior < 20` played games under the CURRENT window (production definition), AND the
    separate "true rookie" slice (first NBA season = the evaluated season, from the union history + `draft_year`);
    the first is where the extension should help (more history for vets who look new), the second where it should
    not;
  * players with < 1 season of history: < 82 prior played games in the union history (a slice label, identical
    across arms; not a feature);
  * injury-heavy games: team has >= 2 rotation players OUT on the pre-tip report (`n_out_rot >= 2`) at the real tip;
  * plus the production guard slices (starters/bench, first 15 team games, teammate OUT).
* Secondary win (injury-Elo coefficients, descriptive, outside the family): `fit_injury_coefs` (ridge logistic on
  `d_out`, `d_doubt` with the Elo offset) fit on the arm's window; evaluation = log loss and Brier on 2023 and 2024
  games, W0 coefficients vs extended coefficients, game bootstrap (B = 2000, seed 0). Offsets are each arm's own
  Elo replay (start of its window); this bundles coefficient and Elo-warm-up effects and is stated as such. Floor
  -0.002 log loss, CI upper < 0 for a "supported" label; there is no promotion consequence.

## Power / MDE (stated before data)

Evaluation n ~ 27.6k player-games and ~1,318 games per season per stat; the paired-delta SE has been ~0.0015 (pts)
and ~0.0006 (reb/ast/fg3m) in comparable designs (T166-T173), so the 80%-power MDE is ~0.004 (pts) and
~0.002 (reb/ast/fg3m): the floor -0.005 is about the detectable size for pts and conservative elsewhere. The
rookie slices (a few thousand rows) have MDE ~0.01-0.02: descriptive. Win: SE ~0.004 per season, MDE ~0.011.
Arms W1/W2 differ from W0 by much more than they differ from each other, so W1-vs-W2 contrasts are
underpowered (CIs reported, no claim).

## Kill criteria

* Any gate G0.x fails, any STOP above fires, or the evaluation key sets differ between arms.
* Any feature uses game data on/after the target date (planted-future unit test), or any production file is modified
  with the flag off (flag-off identity test).
* The experiment writes to `nba.duckdb` (must never) or reads the history DB without `read_only=True`.
* Any constant (0.5 weight, flags, window start, family, floor) changed after the first number is viewed: void.
* One run per arm; no re-run with a different window start (e.g. 2020 or 2021 only) under this registration.

## Implementation (frozen)

Flag-gated opt-in code paths only (extension arm loader, sample weights, era features); nothing in production
imports them. `uv run python -m nba.eval.history_window_eval all` (name reserved). CPU only, take
`data/ops/heavy.lock` for the heavy fits. Tests: planted-future rows, DNP rows, key-set equality across arms,
flag-off byte identity, weight application, history DB opened read-only.

=== RESULTS BELOW ===

(none yet)
