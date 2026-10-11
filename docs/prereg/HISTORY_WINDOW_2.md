# Training-window extension, repaired audit (HISTORY_WINDOW_2): pre-registration

Status: **DRAFT 2026-10-11. Do not freeze: the gate section below records that the repaired audit still STOPS.**
This is **a repair of a STOP rule after it fired (T315), per the era-asymmetry diagnosis of 2026-10-11**
(`reports/era_asymmetry_2026-10-11.md`). Everything from `## Hypothesis` down to `## Implementation` is
HISTORY_WINDOW.md verbatim (arms W0-W3 and W1r, metrics, floors, pass rule, kill criteria, G0 gates) EXCEPT audit
item 3 of the coverage audit, whose two label-correlation statistics are replaced as marked, and the command line in
`## Implementation`. Report-feature definitions stay production's (status OUT trigger, game-level `has_report`);
production's definition is unchanged unless the extension passes and Devin promotes. Because no report definition
was adopted, the control arm W0 (the brief's A0) is the unchanged production path and no re-fit deviation arises;
it must still reproduce pts integer CRPS 3.1528 / 3.1884 (tol 1e-4). Season 2025 is never loaded. The freeze sha256
is that of the text above the results marker, as in HISTORY_WINDOW.

## Why this rule exists, and what each repair protects against

* The original played-rate gap is game-level and rests on 9 reportless games (0-3 per season; 175 rows in 2022-24). It
  fails its own placebo (2022 and 2023 sit about 7 points from the pooled value). (a) replaces it with a player-level
  check that holds the status mix fixed, because P(played | status) is stable across eras and the mix is what moves.
* The original phi of `gap_days` is dominated by left-censoring at the window start (15.8% NULL before mid-November
  2019). (b) gives every season the same cold start. `gap_days` is a played-history column, not a report column.
* Both new checks carry the placebo requirement so a check cannot stop a history season for a deviation that a current
  season also shows against its own pool.
* Caution written into the rule: the threshold is a maximum over three seasons, so for exchangeable seasons a history
  season exceeds it with probability 1/4 per statistic by chance; with three history seasons and two statistics a
  false STOP is likely (about 1 - (3/4)^6 = 82% if all six were independent). The threshold is as specified by the
  maintainer; the gate below shows its effect on the real data.

## Minimum data gate (checked against the real data; G0 unchanged)

Measured 2026-10-11 by `python -W ignore -m research.eval.history_window_eval gate --doc docs/prereg/HISTORY_WINDOW_2.md --draft`
(both DBs read-only, seasons <= 2024, real tips, harness frames identical to the original gate; audit.json sha256
`5219faa04b94e8ba441c8ebf3df7fce6502444f37f66f53a105476d1a0bd75ba`, `reports/history_window_2/`). **The gate FAILS and this
rule must not be frozen as it stands (non-negotiable 8).**

| check | measured | pass |
|---|---|---|
| G0.1 games/box/DNP | 3,637/3,637 games, 100% box coverage, no duplicates, DNP share history 0.160 / 0.206 / 0.171 vs current 0.185 / 0.190 / 0.195 | yes |
| G0.2 era flags | 100% coverage, 2022+ all False | yes |
| G0.3 possessions | MAE 2.09 / 2.03 / 2.01 (<= 3.0) | yes |
| G0.4 | 8 of 707 manifest dates have no rows (2020-02-14/15/16, 2020-07-28, 2021-03-06/07, 2022-02-19/20); the history DB has no games on any of them (All-Star breaks and a bubble off-day), so the literal "every manifest date has rows" check cannot pass by construction; unresolved-name parity passes (0.0% vs 0.0%). Same 8 dates failed the original gate. | **no** (calendar-definition defect, not missing games) |
| G0.5 real tips | 3,637/3,637 | yes |
| Coverage parity (|history - pooled| <= 2 pts) | -0.02 / +0.07 / +0.15 points | yes; W1r not required |
| Leg (c) classifier | AUC 0.7996, no sign flips | not a STOP |
| Leg (a) standardised played rate, listed rows n = 17,819 | pooled 0.6428; weights out 0.191 / uncertain 0.393 / probable 0.203 / available 0.214. S_s [95% CI]: 2019 0.6319 [0.612, 0.651] n=2404; 2020 0.6372 [0.621, 0.653] n=2581; 2021 0.6796 [0.658, 0.699] n=2648; 2022 0.6634 n=2764; 2023 0.6447 n=3403; 2024 0.6264 n=4019. D_s: -0.011 / -0.006 / **+0.037** / +0.021 / +0.002 / -0.016. Placebo T = 0.0206 | **STOP: 2021 (0.0367 > 0.0206)** |
| Leg (b) cold-start phi, rostered rows n = 197,773 | baseline 0.1046; phi_s [CI]: 2019 0.1267 [0.103, 0.150] (n 28,655); 2020 0.1147 [0.095, 0.136]; 2021 0.0953 [0.075, 0.116]; 2022 0.0984; 2023 0.1048; 2024 0.1100. D_s: +0.022 / +0.010 / -0.009 / -0.006 / +0.000 / +0.005. Placebo T = 0.0062 | **STOP: 2019, 2020, 2021** |

Reading: the original statistics' failure modes are repaired (the standardised history rates sit at 0.632 / 0.637 / 0.680
against current 0.626-0.663, and cold-start phi 0.095-0.127 against 0.098-0.110), but the placebo threshold as specified
is 0.0206 and 0.0062, below the sampling noise of the statistics themselves (CI half-widths about 0.02 and 0.02), so
the checks stop history seasons whose deviation is inside the CI of a current season's deviation. The 2021 played-rate
deviation is the one nominally real-looking item (CI of D_2021 [+0.015, +0.056] excludes 0); 2021-22 is the hourly
report season with 59% "available" listings and the protocol era. Nothing was re-tried with another threshold.

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
3. **(REPAIRED, HISTORY_WINDOW_2)** Label-correlation checks. Three legs; any one fires => **STOP**, no arm is fitted.
   Population for legs (a) and (b): rostered player rows (box-score rows, DNP rows kept) of games with a usable
   report (latest snapshot with `as_of <= real tip - 60`), seasons 2019-2024.
   (a) *Status-standardised played rate (player level).* A rostered row is "listed" if the player appears in the
   latest usable snapshot (strongest status if repeated), else unlisted. Status strata: out; uncertain
   (doubtful + questionable); probable; available. Weights `w_k` = the pooled 2022-24 share of listed rows in
   stratum k. For a season s: `S_s = sum_k w_k * P_s(played | listed, stratum k)` (strata with no rows are dropped and
   the weights renormalised, and this is reported). `S_pool` = the pooled 2022-24 rate. `D_s = S_s - S_pool`.
   *Placebo requirement:* `T = max(|D_2022|, |D_2023|, |D_2024|)`. STOP iff `|D_s| > T` for any history season s in
   {2019, 2020, 2021}. CIs of `S_s` and `D_s`: game-clustered bootstrap (B = 2000, seed 0), reported; the STOP
   uses the point estimates.
   (b) *Cold-start-matched NULL pattern.* `phi(gap_days NULL, minutes IS NULL)` where `gap_days` is NULL iff the player
   has no earlier PLAYED game in the same season (every season, history and current, counts prior games from its
   own opener). Baseline `phi_pool` = phi over the pooled 2022-24 rows so counted; `D_s = phi_s - phi_pool`;
   placebo `T` as in (a); STOP iff `|D_s| > T` for any history season. (The report-derived NULL pattern,
   `has_report` = 0 vs DNP, is reported descriptively only: it rests on 9 reportless games in six seasons.)
   (c) *Distinguishability*, unchanged from HISTORY_WINDOW: a classifier on the report columns alone (has_report,
   n_out_rot, vac_*, since_flag, flag_dir, gap_days) separating history from current rows with AUC > 0.75 AND a
   sign difference of those columns' correlation with the residual label between eras.
   The original game-level played-rate gap and the audit-start phi are computed and logged as descriptive only.
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
imports them. `python -W ignore -m research.eval.history_window_eval gate|run --doc docs/prereg/HISTORY_WINDOW_2.md --sha <64 hex>` (the document id selects the repaired audit; outputs go to `reports/history_window_2/`). CPU only, take
`data/ops/heavy.lock` for the heavy fits. Tests: planted-future rows, DNP rows, key-set equality across arms,
flag-off byte identity, weight application, history DB opened read-only.

=== RESULTS BELOW ===

(none; no arm fitted)
