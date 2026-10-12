# Training-history extension, 2019-20 and 2020-21 as extra training rows with the injury report MISSING (HISTORY_WINDOW_3): pre-registration

Status: **DRAFT 2026-10-11. Not frozen. One gate item fails on the real data (G1b below: 244 history players have no `players_static` row), so
this rule must not be frozen until the maintainer runs the free `fetch-static` pull and the gate is re-run.** Devin ("okay, go for it",
2026-10-11) reopened the history-window question as a NEW rule, per the "What would reverse it" line of DECISIONS 2026-10-11 15:14 CDT
(HISTORY_WINDOW_2 not frozen). Season labels: `season 2019` = 2019-20, `2020` = 2020-21, `2021` = 2021-22 (EXCLUDED here), `2022` = 2022-23,
`2023` = 2023-24 (select), `2024` = 2024-25 (report). Season 2025 is the frozen holdout and is never loaded (`season <= 2024` in every query).
Freeze evidence when frozen: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/HISTORY_WINDOW_3.md | shasum -a 256`
(the `Status:` line must be edited to FROZEN first; `run` refuses a document whose status line says DRAFT).

## Question

Does adding the 2019-20 and 2020-21 box-score history (49,150 played player-games, 46,044 with `n_prior >= 5`) as extra TRAINING rows of
the production props model `props_context_residual` (recency average + LightGBM residual, `ctxres-v1`) improve integer-support CRPS of
pts / reb / ast / fg3m on 2023 and 2024, when every injury-report feature of those rows is treated as missing exactly as production treats
a reportless game, and 2021-22 is left out?

## What is different from HISTORY_WINDOW (T315) and HISTORY_WINDOW_2 (T325), and why those results do not decide this

* HISTORY_WINDOW stopped on an audit of the history reports (played-rate gap, `gap_days` phi, a report-column classifier); HISTORY_WINDOW_2
  repaired the statistics and still stopped because its placebo threshold (0.0206, 0.0062) sat below sampling noise (CI half-widths
  about 0.02). Both asked "are the history INJURY REPORTS comparable to the current ones?". This rule does not use the history reports at
  all: they are set to missing. The audit question becomes "does the pattern of MISSING values identify the era?" (below), and the
  noise-aware threshold is derived from current-season-vs-current-season variation with the interval lower bound, never a point against a
  point.
* **Training rows only.** The history seasons enter as extra rows of the training set and nothing else. Their features are built in a
  separate pass over the 2019-20 and 2020-21 games alone, so (a) the gap season 2021-22 never touches a feature (no 15-month stale
  recency average, no 470-day `gap_days` on a 2022 opener) and (b) every evaluation row keeps the byte-identical W0 feature row (gate
  G3 asserts it). HISTORY_WINDOW instead let the extended arms' player states and Elo replay see 2019+; that carry-over is dropped, which
  also means the old "Elo warm-up" effect is not part of this treatment. This is a deliberate change, stated so nobody reads W0..W3 numbers
  across rules.
* The old W1 (four era flags as features) is dropped: in this design `has_report = 0` with the report columns NULL is already a perfect
  era indicator for the history rows (declared-columns AUC 0.9993, gate G5), so the flags add nothing identifiable. The old W1r (drop
  history rows without a report) is moot (every history row lacks one by design). The old W3 (plain extended window) becomes the primary
  arm A1.
* The W0 reference moves from the pre-clean-up pts 3.1884 (n 27,583) to **3.187553 (n 27,701)** for 2024, the value on today's data after
  the 2026-10-11 restoration of five neutral-site 2024 games (DATA_CHANGELOG; GAME_MARKET_CONTEXT and OWN_TAG used the same correction).
  2023 is unchanged, 3.152830 (n 27,619). The gate below measured both.

## Data exposure (honest)

Seen before this draft: T315 and T325 gate/audit numbers (report coverage, played rates, phi, classifier AUC), ELO_HISTORY_WINDOW (win
model; T318-T319, null), the era-asymmetry diagnosis. NO props CRPS with any history rows has ever been computed; the only fit run for this
draft is the W0 pts reproduction (arm A0 = production). Nothing about A1, A2, K0 or K1 has been fitted. Both databases are opened
`read_only=True`.

## Hypothesis

H1: more training rows from two earlier seasons lower the integer-support CRPS of the production props model on 2023 (select) and 2024
(report), in at least one stat, by at least the practical floor, with the gain surviving the knockout of the injury-report columns.
Counter-hypothesis stated first: 2019-20 and 2020-21 are a different data-generating process (shortened and bubble seasons, empty or
limited arenas, different 3-point volume and pace); the recency average already carries most of the signal; the GBM residual head may
learn era structure that does not transfer; the report-missing rows teach the report-conditioned splits nothing. A null or a small harm is
the expected outcome. Prior that an arm passes: ~15%. The question differs from the ELO one (T318-T319, null): the props fit is a 200-tree GBM on about 80k rows at the last block, and
the two seasons add +57% rows there; the Elo ratings forgot the extra seasons entirely (2024 delta exactly zero).

## Data: exactly which tables and columns (nothing else; no tracking, hustle, officials, matchups, pbp)

| source | table | columns | note |
|---|---|---|---|
| `nba.duckdb` (read-only) | `games`, `player_game_stats`, `players_static`, `player_availability` | as production `build_features` reads | seasons 2022-2024 only; availability only for W0 `flagged` (the production report path, real tip times) |
| `data/history/nba_history.duckdb` (read-only) | `games`, `player_game_stats`, `game_era_flags` | game_id, game_date, season, teams, pts; box columns of `PROD_PGS`; the four era flags | `season IN (2019, 2020)` only; `player_availability` is NOT read |
| `data/players_static/<player_id>.parquet` (read-only) | per-player CommonPlayerInfo cache | player_id, position, draft_year | completes `players_static` for history players the DBs lack (gate G1b); the history DB's own `players_static` is empty |

## Arms (training data differs; evaluation rows and their features are identical)

Evaluation rows (fixed by A0, never changed): played rows in 2023 and 2024 with `n_prior >= 5` under the current window (the production
props row set; n = 27,619 / 27,701 per stat). Every arm is scored on exactly these `(game_id, player_id)` keys per stat; an arm that cannot
score a key, or whose key hash differs from A0's, is a STOP.

| arm | training rows | feature columns | weights | role |
|---|---|---|---|---|
| **A0** | current window (games from 2022-10-18), production features | production `stat_feature_names(stat)` | none | control; reproduces pts integer CRPS 3.152830 / 3.187553 (tol 1e-4, n equal) |
| **A1** | A0 rows + history block (2019-20, 2020-21) | production names | none | **primary**: plain extension |
| **A2** | A1 rows | production names | LightGBM `sample_weight` 0.5 on every history row, 1.0 on current rows (all 2,314 history games carry `shortened_season`, 172 also `covid_bubble`/`no_fans`, 1,171 `limited_fans`: the weight is therefore uniform over the history block) | **primary**: HISTORY_WINDOW had a down-weight arm (W2), so this one stays; it is a uniform half weight, not bubble-specific |
| **K0** | A0 rows | production names minus the seven report columns (`has_report`, `n_out_rot`, `vac_min`, `vac_pts`, `vac_reb`, `vac_ast`, `vac_fg3m`) | none | knockout control (report columns removed for every season) |
| **K1** | A1 rows | same as K0 | none | knockout of A1 |

The history block's report treatment: `flagged = {}` in `build_features`, i.e. `has_report = 0` and `n_out_rot`, `vac_*` NULL (gate G3
checks all 49,150 rows, and that the 118 reportless CURRENT rows look the same in kind). Held fixed in every arm: the recency estimator
(half-life 10), hyper-parameters (200 trees, lr 0.04, 15 leaves, min_child 100, ff/bagging 0.8, seed 0), month-block walk-forward (train
strictly before the block's first day, minimum 1,500 rows, calibration window = newest 15% of training rows cut on a date boundary, always
2022+ and always unweighted; asserted for A1, A2, K1), `tip_source="real"`, stat definitions, eligibility. The sample weight applies to the
mean and scale heads only (production fits no other head at `lower_tail="off"`). Hyper-parameters are not tuned.

## Metrics, inference, pass rules

* Props, per stat in {pts, reb, ast, fg3m}: integer-support CRPS for EVERY arm (the harness `arm_scores`: pts as in production; reb/ast/fg3m
  `ceil(q-0.5)`), paired delta (arm - A0), game-clustered percentile bootstrap (B = 2000, seed 0; cluster = `game_id`), 95% CI, two-sided
  bootstrap p. Per season: 2023 selects, 2024 reports.
* **Confirmatory family** per season: {A1, A2} x {pts, reb, ast, fg3m} = 8 tests, Benjamini-Hochberg q = 0.05 within the season. K arms,
  continuous CRPS, threshold log loss, ECE of P(stat >= N), K0-A0 and slices are descriptive and uncorrected.
* Pass per (arm, stat, season): dCRPS <= **-0.005** (practical floor); CI upper < 0; BH p < 0.05; guards: |bias| <= 0.5; 80% interval PIT
  coverage in [0.75, 0.85]; no slice with n >= 300 worse than A0 by more than +0.01 CRPS.
* **Selection and report**: a (arm, stat) cell is selected if it passes on 2023 and reported if the same cell passes on 2024.
* **Knockout guard (a pass must survive it)**: for a confirmed stat, the K1 - K0 paired delta must be <= **-0.0025** (half the floor) in
  BOTH seasons. Protects against a gain that exists only because `has_report = 0` lets the model route history rows away from current
  rows (attribution to the missingness pattern rather than to the history). A confirmed stat that fails it is "FAIL_KNOCKOUT", not a pass.
* **Verdict per arm**: PASS if >= 1 stat is confirmed (2023 and 2024, same stat) and passes the knockout guard and no HARM; FAIL_KNOCKOUT; FAIL;
  **HARM** if any stat has dCRPS >= +0.005 with CI lower > 0 in either season (blocks any recommendation for that arm). If both A1 and A2
  pass, prefer A1 (no weight constant) unless A2 beats A1 with a CI excluding 0. PASS means a shadow-logging proposal and a red team,
  never promotion: an extended-window production refit changes every daily artefact and is Devin's decision.
* Slices (both seasons; binding at n >= 300): cold start (`n_prior < 20`, production definition, current window), true rookie (first season
  in the union of history block and current window equals the evaluated season, `draft_year` null or >= season - 1), injury-heavy
  (`n_out_rot >= 2`), teammate OUT, starter / bench, first 15 team games. Labels are identical across arms and are not features.
  (The old "< 82 prior games in union history" slice is dropped: `n_prior` is no longer arm-dependent.)
* 2025: this rule never earns a 2025 touch. A PASS would be followed by shadow logging on the live log; any later 2025 look is a separate
  model, a logged touch and its own commit.

## Leakage guards (the core risk) and what each protects against

The risk named by Devin: a "report missing" pattern that exists only in the history rows lets the model identify the era. Facts that
bound it: evaluation rows are byte-identical to A0's (G3), and every history row is dated before every evaluation row, so a gain cannot
come from information unavailable at tip-off; what the pattern can change is HOW the history rows are used (routing them to their own
leaves), and the danger is mis-attributing that to "history helps". Guards, all with thresholds set by formula before any outcome:

* **L1 treatment exactness (G3).** History block: `has_report == 0` on 100% of rows, `n_out_rot` and `vac_*` NULL on 100%, current part
  of the extended frame equal to the W0 frame row for row. Protects against a half-applied missingness treatment (a few history rows
  with real reports would be a era-specific, label-correlated pattern) and against drift between W0 and the extended arms' evaluation rows.
* **L2 missingness audit of every column (G4).** Population: eligible (`n_prior >= 5`) rows of 2019, 2020, 2022, 2023, 2024, every
  production feature column of any stat plus the four labels, and the stratum 'all' plus four realised-minutes buckets (<=10, 10-20, 20-30,
  >30); separately the 13 raw box-score columns on ALL rostered rows with strata played / DNP. Per (column, stratum): NULL-or-NaN rate by
  season; `D_s` = rate_s minus the reference rate (reference for a history season = pooled 2022-24; for a placebo current season = the pooled
  OTHER two current seasons). **Threshold, computed from current-vs-current variation**: `T = max over current seasons q of |D_q|`
  (point values). A history season is FLAGGED when the lower end of its 99% game-clustered bootstrap interval of `|D_s|` (B = 1000, seed 0)
  exceeds `max(T, 0.01)`; 0.01 is a materiality floor (a pattern on under 1% of rows cannot move CRPS by 0.005 with min_child 100).
  Using the interval LOWER bound against a point maximum is the noise-aware part (HISTORY_WINDOW_2 compared a point to a point). The seven
  report columns are declared (expected 100% NULL in history) and tabulated, never flagged. The audit must also pass its own placebo:
  each current season, treated as if it were history with `T` taken from the other two current seasons, is flagged on 0 cells. PASS =
  0 flagged history cells (non-declared) and 0 flagged placebo cells.
* **L3 era from the pattern of missing values (G5).** Classifier: LightGBM, 100 trees, 15 leaves, seed 0, game-level hold-out
  (`sha256(game_id) % 3 == 0`), features = the 0/1 NULL indicators of every non-declared column that has any NULL (so values play no
  role). AUC for history-vs-2022 (2019, 2020) and pooled history vs pooled current, with game-clustered 95% intervals (B = 200). **Stated
  AUC limit** = `max(max placebo AUC over the three current-season pairs, 0.505)`; 0.505 is the same 1-point materiality (a column NULL on
  a share r of one era only gives AUC 0.5 + r/2). A history pair is flagged when its interval LOWER end exceeds the limit; the placebo pairs
  are checked against each other the same way (must not self-flag). The value-based AUC (same classifier on feature VALUES) is reported
  but cannot be gated: on today's frames every pair of seasons has AUC 0.975-0.998 (even adjacent current seasons) because team- and
  date-smooth columns (Elo, rolling team points) identify the date in any game-level split.
* **L4 planted checks on the real frames (G7).** (i) 15% of `rest_days` set NULL in the history rows must be flagged by L2 and lift the
  L3 indicator AUC above 0.505 with the interval lower bound; the unplanted data must be flagged by neither. Protects against guards
  that cannot see anything.
* **L5 planted future (G6).** Features of history rows before 2020-03-01 built from games before that date only must equal the full
  build's features for the same rows, column by column (rtol 1e-9). Protects against an as-of defect in the history block (the
  production builder run on a block it was never run on).
* **L6 knockout arms K0/K1 (run stage).** The guard above; also K0 - A0 is reported (the cost of removing the report columns from
  production), descriptive.

## Minimum data gate: measured 2026-10-11 against the real data (non-negotiable 8)

Run: `python -W ignore -m research.eval.history_window3_eval gate --doc docs/prereg/HISTORY_WINDOW_3.md --draft --repro` (both DBs read-only,
`season <= 2024`; W0 pts is the only fit; `reports/history_window_3/gate.json`, `audit.json` sha256
`9b1e88b8af72c9bf83a225225a8a89e89c96f33d2b8d2f23a1237771d77b4be4`). **GATE: NOT PASSING (3 items: G1b, G4, G5, all caused by the same
column). Do not freeze until G1b is fixed and the gate re-run passes.**

| check | measured | pass |
|---|---|---|
| G1 data | history games 1,143 (2019) + 1,171 (2020) = 2,314 = the cached schedule exactly (0 missing, 0 extra, 0 duplicates); box rows for 100% of games; `pf/fta/fga` non-NULL on 100% of played rows; 0 DNP rows with non-zero stats; DNP share 0.160 / 0.206 vs current 0.185 / 0.190 / 0.195 (band 0.155-0.225); possession MAE 2.09 / 2.03 (<= 3.0); era flags 100% coverage and valid, 2022+ all False, all 2,314 history games `shortened_season` | yes |
| **G1b static** | **244 of 638 history players have no `players_static` row in either DB or in the per-player cache** (the history DB's table is empty; `nba.duckdb` covers players seen from 2022). Their `pos_code` is NULL | **no** |
| G2 rows (per stat; identical for the four stats) | history eligible rows 21,522 (2019) + 24,522 (2020) = 46,044; W0 training rows 25,110 at the first test block and 80,271 at the last; the block adds +183% (first) and **+57% (last)**; gate: >= 20,000 rows and >= +30% at the last block | yes |
| G3 treatment | 49,150 history rows, `has_report == 0` on 49,150, `n_out_rot` and all `vac_*` NULL on 49,150; extended frame's current part `equals` the W0 frame; the 118 reportless current rows are NULL in the same columns | yes |
| **G4 missingness** | 350 feature cells + 26 box cells (13 columns x played/DNP); **flagged: `pos_code`, all five strata, 2019 and 2020 (NULL 24.2% in 2019, 17.2% in 2020, 0.0% in 2022-24; lower bound of \|D\| 0.234 / 0.166 vs T = 0.0)**; no other column: the largest non-`pos_code` history deviation is 0.0009 against a placebo maximum of 0.0029 (the 0.01 floor binds); placebo (current seasons) false flags: 0; box columns (incl. minutes NULL in DNP rows 0.998-1.000 in every season): 0 flagged | **no** |
| **G5 NULL-indicator AUC** | placebo pairs 2022v2023 0.5009, 2022v2024 0.5009, 2023v2024 0.5000 (limit 0.505); history 2019v2022 **0.620** [0.616, 0.626], 2020v2022 **0.586** [0.582, 0.590], pooled **0.602** [0.599, 0.606]: flagged, entirely from `pos_code`; placebo pairs do not self-flag. Value AUC (descriptive): 0.960 / 0.997 / 0.996 / 0.998 / 0.975 (2019v22, 2020v22, 22v23, 22v24, 23v24); report columns alone (declared) 0.9993 | **no** |
| G6 planted future | 18,899 rows before 2020-03-01 compared, 0 columns differ | yes |
| G7 planted detection | 15% NULL plant in `rest_days`: flagged in 2019 and 2020 by L2; indicator AUC 0.573 (lower bound 0.570) vs 0.500 clean; the unplanted data flags nothing | yes |
| G8 W0 reproduction (pts) | 2023: 3.152830, n 27,619 (reference 3.152830, 27,619); 2024: 3.187553, n 27,701 (reference 3.187553, 27,701); both within 1e-4; the pre-clean-up 3.1884 would FAIL (8e-4 off), hence the amended reference; one pts walk-forward = 23 s, 18 blocks, train 25,110 -> 80,271 rows | yes |
| Diagnostic (not a gate item): what G4/G5 show with `pos_code` excluded | G4: 0 flagged history cells, 0 placebo false flags; G5: 2019v22 0.4994, 2020v22 0.5009, pooled 0.4996 vs limit 0.505, 0 flags, no placebo self-flag. Expectation: a NULL-free `pos_code` in the history block makes both pass | informational |

**The one finding that matters.** `pos_code` is the "NULL pattern that exists only in the history rows" Devin asked about, and it is not the
injury report: 244 players who played in 2019-21 and have since retired never entered `players_static`. Left alone, the model could read
era off `pos_code` being NULL (indicator AUC 0.60). Remedy (no model change): the maintainer pulls CommonPlayerInfo for those 244 players
(free `nba_api`, ~1 s per call with the harness limiter, about 5 minutes, resumable, writes only `data/players_static/<id>.parquet`; run it
while the ingest queue is paused because both use the stats.nba.com quota), then the gate is re-run. The history block reads the cache
read-only; no database is written. If the pull cannot complete (a player with no CommonPlayerInfo), the rule's choice is the one stated
now: the gate fails and the rule is not frozen; `pos_code` is NOT dropped from the production feature list or imputed.

## Power / MDE (stated before data)

Evaluation n = 27,619 / 27,701 rows and about 1,320 games per stat per season; the paired-delta SE has been about 0.0015 (pts) and
about 0.0006 (reb, ast, fg3m) in comparable designs (T166-T173), so the 80%-power MDE is about 0.004 (pts) and 0.002 (reb, ast, fg3m): the
floor -0.005 is near the detectable size for pts and conservative elsewhere. The knockout guard (-0.0025) is below the pts MDE, so it
protects mainly the count stats; it is a necessary condition, not a significance test. Slices with n < 1,000 are descriptive.

## Kill criteria

* Any gate item above fails at the freeze re-run, or any `Stop` fires (key hashes differ between arms, calibration window starts before
  2022-10-18, the W0 reproduction fails at run time), or the evaluation feature rows differ from A0's.
* A planted-future test fails, a production file is modified, a write connection is opened on either database, or season 2025 is read.
* Any constant (0.5 weight, floor -0.005, knockout -0.0025, window, family, materiality 0.01, AUC limit 0.505, B, seeds) changed after the
  first number is viewed: void. One run per arm; no re-run with another window (2019 only, 2020 only, adding 2021) under this rule: a new
  question is a new id. One pre-registered follow-up at most, then the history-window direction is closed regardless of result.

## Implementation

`research/eval/history_window3_eval.py` (opt-in research module; nothing in `nba/` imports it; `nba/` is untouched). Stages:
`fetch-static` (maintainer, network, free), `gate` (`--draft` allowed on an uncommitted document; `--repro` adds the one W0 fit), `run`
(refuses unless the document is frozen, the gate passed and `audit.json` still hashes to the logged value), `report`. `run` takes
`data/ops/heavy.lock` itself (retry 120 s, always released), prints progress per month block, caches each (arm, stat) to
`data/history_window_3/arm_<arm>_<stat>.npz` (resumable, one run per arm). CPU only.

```
cd /Users/devin/Downloads/nba-prediction
uv run python -W ignore -m research.eval.history_window3_eval fetch-static            # once, ~5 min, free nba_api, pause the ingest queue
uv run python -W ignore -m research.eval.history_window3_eval gate --doc docs/prereg/HISTORY_WINDOW_3.md --draft --repro
# after Devin confirms and the main session commits `prereg(HISTORY_WINDOW_3): <sha256>`:
uv run python -W ignore -m research.eval.history_window3_eval run --doc docs/prereg/HISTORY_WINDOW_3.md --sha <64 hex>
```

Runtime (measured and extrapolated, not a result): one pts walk-forward of A0 = 23 s on the Mac CPU (18 blocks, up to 80k training
rows). A1, A2, K1 train on 1.6x (last block) to 2.8x (first block) the rows, about 45-60 s per (arm, stat); per stat about 3.5 min for the
five arms; four stats about 14 min; the paired bootstraps and slice tables add about 3-5 min. **Estimate 15-20 minutes end to end on the
Mac CPU (8 GB; peak well under 2 GB; no process fan-out).** Colab is not needed and would not be faster (trees; DECISIONS 2026-10-09 Q5).

## What each choice protects against

| choice | protects against |
|---|---|
| training rows only, history features built in a separate pass, 2021-22 excluded | stale recency state and 470-day `gap_days` across the gap season; a treatment that changes evaluation rows; confounding of "more training rows" with "better Elo warm-up" |
| report treated as missing (`flagged = {}`) rather than the parsed history PDFs | the T315/T325 era asymmetry (cadence, bubble hours, status mix), with no new parsing or threshold dispute |
| evaluation rows byte-identical across arms (G3, key hashes) | silent row loss, a fair-baseline violation (same rows, same DNP handling, same calibration window) |
| calibration window always 2022+ and unweighted | calibrating on the old era, which would flatter or hurt every arm for the wrong reason |
| NULL audit with placebo and interval lower bound | the HISTORY_WINDOW_2 failure (threshold below noise) and a history-only NULL pattern slipping through (it caught `pos_code`) |
| NULL-indicator AUC (not value AUC) | a guard with no power: value AUC is 0.96-0.998 for every pair of seasons |
| planted NULL and planted-future checks on the real frames | guards that pass because they see nothing; an as-of defect in the history block |
| knockout K0/K1 at half the floor, required in both seasons | attributing to history a gain that is the model routing history rows by `has_report = 0` |
| {A1, A2} x 4 stats, BH per season, 2023 selects and 2024 reports on the same cell | selecting the best of 8 on one season and reporting it |
| floor -0.005 plus CI upper < 0 plus bias, coverage and slice guards | calling a p-value a result; gains bought by miscalibration or by one slice |
| HARM tripwire | recommending an extension that hurts |
| no 2025 touch, `season <= 2024` | spending the holdout on an idea with a ~15% prior |

## Unstated details fixed before scoring

History block pass order: games of 2019 and 2020 only, `static` = union of both DBs plus the read-only per-player cache. A2 weights come
from `game_era_flags` (`any flag -> 0.5`). Knockout removes exactly the seven report columns listed above (the stat-specific `vac_<stat>`
included); `since_flag`, `flag_dir`, `gap_days`, `since_team` are played-history columns and stay. Null audit counts NaN as NULL; it runs on
eligible rows for features and on all rostered rows for the box columns. Bootstrap B: 2000 (CRPS), 1000 (NULL rates, 99%), 200 (AUC, 95%),
seed 0. Verdicts follow the same-stat reading (HISTORY_WINDOW). Open items for Devin: (1) approve the `fetch-static` pull (244 players,
free, ~5 min); (2) confirm the knockout level -0.0025; (3) confirm that A2 is a uniform 0.5 over the history block (every history game is
flagged `shortened_season`), not a bubble-specific weight.

=== RESULTS BELOW ===

(none; no arm fitted. The only fit is the A0 reproduction recorded in the gate above.)
