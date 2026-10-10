# F8 lineup rebounding: expected on-court rebounding environment as props features (pre-registration)

Status: FROZEN 2026-10-10 16:36 CT on Devin's confirmation (message: "confirm everyone!!!", covering F8 among four pending items;
see docs/DECISIONS.md 2026-10-10). Draft history: DRAFT 2026-10-10 14:35 CT, decisions filled by Claude's recommendations
(Devin: "then F8"). Settled: (a) the rebound flag was fixed first (`possessions.n_oreb`/`n_dreb`, data_version
1c5b0901afdf), so G3 is re-measured below and group C is IN; (b) big = 6'10" (height_in >= 82) or Center, fixed;
(c) F11 ran first and closed as a null (T244-T251), so A0 stays production v2; (d) the motivating evidence comes
from the report season and the holdout and is declared as such in "Data exposure"; (e) primary slice = bigs
(`is_big_p`), pooled rows as the no-harm guard (pass rule 1). Committed by the maintainer's hand BEFORE any fit.
Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F8_LINEUP_REBOUNDING.md | shasum -a 256`
(first 8 hex chars into the results section and every ledger row). No candidate exists yet. Season labels
are the repo's `games.season`: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (frozen holdout, never loaded).

## What Devin said
> "Rebounding is a lineup property": with Adams and Capela in double/triple-big lineups, Houston led the
> league in offensive rebounding; our props model attributes boards to individuals and over-predicted Rockets
> rebounds (+0.16) when the lineup changed. Think in usage, RB%, BLK%. (FAN_KNOWLEDGE, F8)

## Question
Does the expected size and rebounding makeup of the five a player will share the floor with lower the
integer-support CRPS of individual `reb` (and, via second chances, `pts`) beyond production
`props_context_residual` v2, and is that gain more than a SUM of teammates' individual rebound rates?
Null: no change. The second clause is the "not a sum of individuals" claim and has its own arm (A2).

## Data exposure (honest)
Seen: descriptive HOU team OREB% 25.1% (2023-24) -> 31.6% (2024-25) -> 34.5% (2025-26) and the +0.16 HOU reb
bias in the 2025-26 replay (a descriptive, season-2025 observation that motivated the question; no model was
fit on 2025 and nothing here loads it). The report season 2024 contains the HOU shift year, so the
hypothesis was suggested by data adjacent to the report season. Also seen: the gate counts below (coverage
only, no outcome model). Not seen: any lineup-conditional model result for reb or pts on 2023/2024.
`nba.duckdb` opened `read_only=True`; `season <= 2024` only.

## Minimum-data gate (non-negotiable 8), measured 2026-10-09 on `nba.duckdb` read-only, seasons 2022-2024
Played = `player_game_stats.minutes > 0`. "Computable" = the player has >= 500 on-court possessions (offence +
defence, from `possessions.off_players/def_players`) in games STRICTLY before the game date, and a height in
`players_static` (height null on 0 played rows). 2022 is the warm-up season (no history before 2022-10), so its
share is reported but is not a gate season.

| Season | played player-games | with reb > 0 | computable (>= 500 prior poss) | ... and `n_prior >= 5` (production row filter) | computable at >= 1,500 poss |
|---|---|---|---|---|---|
| 2022 (warm-up) | 27,737 | 24,637 | 84.8% | 93.2% (n 25,110) | 65.7% |
| 2023 (select) | 28,197 | 24,802 | 94.7% | 96.7% (n 27,619) | 88.9% |
| 2024 (report) | 28,118 | 24,846 | 95.3% | 97.2% (n 27,583) | 90.0% |

Among rows with reb > 0 the computable share is 87.0% / 96.5% / 96.7% (2022/23/24). Stint coverage (no
possessions in the game): 0.8% / 1.2% / 1.0% of played rows.

Projected big OUT (team-games; big = `height_in >= 82` or position contains Center; projected = averaged >= 20
minutes over his previous 10 games with that team; OUT = any `player_availability.status = 'out'` row for the
game; NOT tip-gated, so this is a coverage count, the build gates on the real tip). Team-games with at least
one: 638 / 748 / 759 of 2,640 / 2,636 / 2,630 (2022/23/24), i.e. about 28-29%. Player-games (played teammates)
inside them: 6,601 / 7,775 / 7,897, of which reb > 0: 5,857 / 6,940 / 7,111. With the 15-minute cut: 651 / 789 / 809
game_ids. HOU player-games: 884 / 883 / 923 (reb > 0: 798 / 792 / 816). Bigs (height >= 82 or Center) played
rows: 6,484 / 6,528 / 6,601.

Gate rules (fixed here, before any fit):
* G1 coverage: computable share of played, `n_prior >= 5` rows >= 90% in each of 2023 and 2024. Measured 96.7% /
  97.2%: PASS. Non-computable rows keep the feature NaN (LightGBM native), and a missingness audit (G4) is run.
* G2 slice power: >= 300 team-games with a projected big OUT in 2024 and >= 300 HOU-or-bigs rows. Measured 759
  and 6,601: PASS.
* G3 rebound reconciliation (re-measured 2026-10-10 on `possessions.n_oreb` / `n_dreb`, seasons <= 2024, 7,906
  team-games): possession-derived team OREB and DREB per team-game must match box-score team `oreb` / `dreb`:
  aggregate ratio within 0.95-1.05 AND team-game correlation >= 0.95. MEASURED: n_oreb sums to 27,524 / 27,741 /
  29,219 (2022/23/24) against box OREB 27,534 / 27,749 / 29,223 (ratio 0.9996-0.9999, team-game correlation
  0.9999); n_dreb sums to 87,096 / 86,748 / 86,506 against box DREB 87,100 / 86,753 / 86,507. **G3 PASSES.** History:
  the first measurement (2026-10-09) used the old boolean `oreb`, which counted team/dead-ball rebounds (1.33x box,
  correlation 0.80) and failed; the parser was corrected and the counts backfilled (docs/DATA_CHANGELOG.md
  1c5b0901afdf) before this draft was finalised and before any fit. Group C is therefore IN all arms. Stints
  reconcile to box minutes at 98.4-99.1%, which supports groups A and B.
* G4 missingness: NULL rate of every `lr_*` column by realised-minutes bucket (<5, 5-10, 10-20, >20) and by reb
  tercile; max difference between buckets <= 2 pp. Features are built for ALL active-roster rows, then
  filtered to played rows, so NaN cannot depend on playing.

## Feature definitions (as-of; appended via `stat_feature_names(stat, extra=...)`)
Config flag `ContextResidualConfig.lineup_reb` in {`off` (default, byte-identical), `on`}. Columns go to the
mean and scale heads of `reb` and `pts` only (the declared family); ast and fg3m are untouched.

Player traits, as-of the game date `d`, using only box rows and stints with `game_date < d`, decayed half-life
40 games, empirical-Bayes shrunk to the league mean:
* `oreb_pm_q`, `dreb_pm_q`: OREB and DREB per minute (pseudo-count 300 minutes each).
* `is_big_q` = `players_static.height_in >= 82` (6'10"), fixed; `h_q` = `height_in`.

Co-play weights `c(p,q)`: identical construction to F11 (`docs/prereg/F11_LINEUP_CONTEXT.md` Step 2): share of
p's floor seconds shared with teammate q over the team's PRIOR 10 games (half-life 10 team games), teammates OUT
on the real-tip-gated official report get c = 0 with the mass redistributed, `sum_q c = 4`, minutes-share
fallback when p has < 300 stint seconds. Tonight's box score, stints and minutes are never read.

Projected five `S(p)`: at T-60, p plus the four teammates with the largest `c` among those not OUT (ties by
prior-10 starts); at T-30, if the confirmed starting five is published (`lineups_known`, known-at stamped before
the real tip), `S` is the confirmed five and `c` is re-weighted with starters at their prior-10 co-play share. A
row is never scored from a T-30 feature if it was generated after the real tip (refused). Both snapshots are
evaluated; T-60 is primary, T-30 is secondary and reported separately.

Group A, additive (the "sum of individuals" benchmark):
* `lr_oreb_sum = sum_q c(p,q) * oreb_pm_q`, `lr_dreb_sum = sum_q c(p,q) * dreb_pm_q`.
Group B, lineup structure (non-additive; the F8 claim):
* `lr_nbig` = `is_big_p + sum_q c(p,q) * is_big_q` (expected bigs on the floor with p, 0-5).
* `lr_dbl` = 1 if `S(p)` contains >= 2 bigs else 0 (double-big starting five); `lr_h5` = mean `h_q` over `S(p)`;
  `lr_h2` = sum of the two largest `h_q` in `S(p)`.
* `lr_p_gap` = `lr_nbig` minus the league-average expected bigs for p's team-season position slot (so a team that
  always plays two bigs is not flagged as "changed"), and `lr_chg` = `lr_nbig` minus the same team's mean `lr_nbig`
  over its previous 10 games (the lineup-change term; zero for a stable lineup).
Group C (G3 passes): `lr_oreb_pct`, `lr_dreb_pct` = shrunk as-of team OREB% / DREB% over p's prior on-court
possessions (OREB% = sum n_oreb / offensive rebound chances = possessions with a missed FG or last FT; DREB% the
mirror on defence; pseudo-count 1,500 possessions toward the team-season mean), per `possessions`.
`lr_cover` = share of `c` mass from stints (not the fallback). Unseen lineups still get every value because
nothing keys on lineup IDs.

Overlap with F11: F11's `lc_rpm` is group A in a single column. F11 ran on 2026-10-10 and closed as a powered
null (T244-T251), so A0 for F8 is production v2 and the A2 arm (group A alone) is what says whether structure
adds anything beyond a sum. F11's oracle (actual stints, -0.058 pts) is the reason A4 here is descriptive only.

## Arms (identical rows, seeds, walk-forward month blocks, 2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU)
* A0 production v2 (reproduces stored OOF integer CRPS to 1e-6; flag `off` byte-identical).
* A1 candidate = A0 + groups A, B and C.
* A2 additive control = A0 + group A only.
* A3 placebo = A1 with the lineup columns permuted across rows within team-season (keeps marginals, breaks the link).
* A4 ORACLE (descriptive, leaks by construction, never a model): `S(p)` and `c` from tonight's ACTUAL stints.

## Metric, support, inference
Integer support: every quantile mapped by `ceil(q - 0.5)` (`to_integer_support`, docs/INTEGER_QUANTILES.md) for
every arm; CRPS = RPS over integer thresholds (`nba/props/metrics.py`). Primary: paired dCRPS (A1 - A0) on
`reb` and on `pts`, per season, all rows, game-clustered bootstrap (2000 resamples, seed 0), 95% CI.
Secondary (cannot rescue a failure): mean bias, 80% PIT coverage, lower-tail PIT (q0.10, q0.20), threshold log
loss (reb 4/6/8/10, pts 10/15/20), per-stat MDE (1.96 x clustered SE) beside each result. Family
`{reb, pts}`, BH at q = 0.05 over the two clustered p-values, per season.

## Pass rule (per stat; 2023 selects, 2024 reports; all must hold)
1. PRIMARY on the bigs slice (`is_big_p` = height_in >= 82 or Center; played rows, n reported, must be >= 3,000 per
   season): dCRPS (A1 - A0) on 2024 <= -0.005, game-clustered 95% CI upper bound < 0, BH p < 0.05 over {reb, pts};
   and the 2023 point estimate has the same sign (selection season; no tuning is permitted on it). NO-HARM guard on
   all rows: pooled dCRPS point <= +0.001 and CI upper <= +0.003 per stat and season (a bigs gain that costs the
   rest of the roster is not a pass). Rationale (fixed before any fit): bigs are about a quarter of rows, so a
   pooled floor of -0.005 could be unreachable for a real lineup-rebounding effect; the slice is the mechanism.
2. Structure beyond a sum: A1 beats A2 by point <= -0.003 on the same stat (else the gain is just the sum).
3. Guards: |mean bias| <= 0.5; 80% PIT coverage in [0.75, 0.85]; q0.10 lower-tail PIT within 0.10 +/- 0.02; no
   slice with n >= 300 worse than A0 by more than +0.01 CRPS.
4. Attack gates: A1 beats A3 by point <= -0.003; A1 gain < A4 gain (A1 >= A4 means audit for leakage first);
   the planted tests pass; knockout leaves a consistent story (below).
`pts` is a secondary member of the family: its mechanism (second-chance points) is weaker, and a null there with
a `reb` pass is reported as exactly that.

## Slices (n >= 300 enforced; descriptive except the guard in rule 3)
Bigs (`is_big`) vs guards (height < 78) vs others; HOU; lineup-change games = a projected big (>= 20 prior-10
minutes) is OUT on the real-tip-gated report; `lr_chg` != 0; first 15 team games; teammate-OUT status; starter vs
bench; T-60 vs T-30. Mechanism check (descriptive): dCRPS for `reb` by tercile of `lr_nbig` (expected monotone if the story
is real) and the mean `reb` bias of A0 vs A1 for HOU, 2024.

## Red-team plan (run by the adversary, before anyone believes a pass)
1. Shuffled lineups: A3 above, plus a second placebo permuting only the projected five across games within team.
2. Planted future: edit tonight's stints/box score/starter flags and add a teammate to the OUT list AFTER the real
   tip; every `lr_*` value for that game must be byte-identical. Any stint dated `>= game_date` is ignored. CI tests.
3. Knockout: drop group B height columns (`lr_h5`, `lr_h2`); drop `lr_chg`; drop group A. The A1 gain must not rest
   on a single group (one group carrying >= 80% of the gain is a red flag; report it).
4. Missingness audit G4, and a cold-start check: gain must not come from rows where `lr_cover` < 0.5.
5. Replication: the sign of the 2023 dCRPS agrees with 2024; HOU-excluded run (the motivating team may not carry
   the result).

## What closes it
* Pass: a confirmed stat gets a drafted (not appended) holdout row and a shadow-log recommendation; no promotion
  (Devin's decision); season 2025 stays untouched until a logged row exists.
* Null (rule 1 or 2 fails on 2024): record in the ledger and close. Allowed follow-up, at most ONE pre-registered
  rule (candidate: learned player-trait embeddings for lineup composition), then F8 is closed. Do not add
  usage/BLK%/position columns or alternative big thresholds after seeing results.
* G3 was fixed BEFORE freeze (counts backfilled 2026-10-10), so group C is part of this rule; there is no F8c.
* Any planted test, G4 or A1 >= A4 fails: stop, treat as leak, fix = new rule.

## Outputs (exact)
`reports/prereg_f8/results.json`, `reports/prereg_f8.md`: `[stat, season, arm, snapshot, n_rows, n_games, crps_int,
dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`; `[stat, season, slice, n, dcrps, ci_lo,
ci_hi]`; `[col, bucket, null_rate]`; `[test, ok]` for G1-G4 and the planted tests; seeds, SHA, frozen sha256.

=== RESULTS BELOW ===

Run 2026-10-10 by the modeler agent; frozen sha256 verified before the run: 2a8e581e7f03943da4ea7410c10d516d7adca27f58e9c892b5b07d8f0e682240 (first 8: 2a8e581e), freeze commit e30b750. git SHA at run 1eff0f03ca96c6034554ed5b16a59d2e7f671339. data_version 1c5b0901afdf. Seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0} (LightGBM seed 0, bootstrap 2000 resamples seed 0, PIT seed 0, placebo seed 0). CPU only. Seasons <= 2024 only (max season loaded 2024); nba.duckdb opened read_only. Run time: gate 25s, arms 363s. Code: research/features/lineup_rebounding.py, research/eval/f8_lineup_rebounding.py (nba/ untouched; no `lineup_reb` config flag exists, A0 is the unmodified production list). Full tables: reports/prereg_f8.md, reports/prereg_f8/results.json.

### Verdict

**NULL on both stats; F8 does not pass rule 1 or rule 2 and closes.** No knockout was run (it is conditional on rules 1-4 otherwise passing). No holdout row is requested. Allowed follow-up: at most ONE pre-registered rule (candidate in the doc: learned player-trait embeddings), then F8 is closed.

### Gate (re-run on the real data before any arm)

| test | value | threshold | ok |
|---|---|---|---|
| G3 oreb ratio | 0.9997 | 0.95-1.05 | ok |
| G3 dreb ratio | 1.0000 | 0.95-1.05 | ok |
| G3 oreb team-game corr | 0.9999 | >= 0.95 | ok |
| G3 dreb team-game corr | 1.0000 | >= 0.95 | ok |
| G1 computable share 2023 | 0.9666 | >= 0.9 | ok |
| G1 computable share 2024 | 0.9715 | >= 0.9 | ok |
| G2 team-games with projected big OUT (2024, not tip-gated) | 562 | >= 300 | ok |
| G2 HOU-or-bigs rows (2024) | 7157 | >= 300 | ok |
| R1 bigs rows 2023 (rule 1 power) | 6431 | >= 3000 | ok |
| R1 bigs rows 2024 (rule 1 power) | 6499 | >= 3000 | ok |
| G4 max NULL-rate gap across minutes buckets / reb terciles (pp) | 0.2968 | <= 2.0 | ok |
| PLANT same-game box/stints/possessions/starters edited: lr_* identical through the day | 1 | == 1 | ok |
| PLANT same-game is not inert (later games change) | 1 | == 1 | ok |
| PLANT lr30_* identical for games before the day | 1 | == 1 | ok |
| CONTROL lr30_* (T-30) reacts to flipped starter flags on the day | 1 | == 1 | ok |
| PLANT teammate moved to OUT after the real tip: no change | 1 | == 1 | ok |
| CONTROL same teammate OUT before the tip does change lr_* | 1 | == 1 | ok |
| PLANT future (>= cutoff date) stints/box/possessions: earlier lr_* unchanged | 1 | == 1 | ok |
| PLANT future is not inert (later games change) | 1 | == 1 | ok |

G1 detail (computable = >= 500 prior on-court possessions and a height, played rows with n_prior >= 5): 2022: n=25110, 0.9321; 2023: n=27619, 0.9666; 2024: n=27583, 0.9715.
G2: team-games with a projected big OUT in 2024 (not tip-gated) = 562 (the doc's coverage count of 759 used a different projection; both exceed 300), HOU-or-bigs rows 2024 = 7157, bigs rows {'2023': 6431, '2024': 6499}.
G3 (re-measured): OREB ratio 0.9997, corr 0.9999; DREB ratio 1.0000, corr 1.0000, n=7906 team-games.
G4: max NULL-rate gap 0.297 pp across minutes buckets and reb terciles (all lr_* and lr30_* columns; audit rows in reports/prereg_f8.md).
A0 reproduction: integer CRPS equals the stored OOF (reports/lower_tail/candidates.json 'off') to 0.0 (<= 1e-6) with identical n, reb and pts, 2023 and 2024.

### Primary: A1 - A0 on the bigs slice (integer-support CRPS, game-clustered 95% CI, 2000 resamples)

| stat | season | n (bigs) | dCRPS | CI | p | p_BH over {reb, pts} | MDE |
|---|---|---|---|---|---|---|---|
| reb | 2023 | 6431 | -0.0002 | [-0.0046, +0.0042] | 0.949 | 0.949 | 0.0044 |
| reb | 2024 | 6499 | -0.0009 | [-0.0047, +0.0025] | 0.643 | 0.643 | 0.0036 |
| pts | 2023 | 6431 | -0.0027 | [-0.0080, +0.0029] | 0.346 | 0.692 | 0.0054 |
| pts | 2024 | 6499 | +0.0038 | [-0.0005, +0.0080] | 0.086 | 0.172 | 0.0042 |

### Pooled (all rows) and arms

| stat | season | arm | n_rows | n_games | crps_int | dCRPS | CI | p | p_bh | bias | cov80 | pit_q10 | pit_q20 | tll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| reb | 2023 | A0 (T60) | 27619 | 1318 | 1.2928 |  |  |  |  | -0.043 | 0.792 | 0.113 | 0.209 | 0.3440 |
| reb | 2023 | A1 (T60) | 27619 | 1318 | 1.2927 | -0.0001 | [-0.0015, +0.0013] | 0.885 | 0.885 | -0.058 | 0.792 | 0.114 | 0.210 | 0.3441 |
| reb | 2023 | A2 (T60) | 27619 | 1318 | 1.2926 | -0.0002 | [-0.0015, +0.0011] | 0.796 |  | -0.055 | 0.791 | 0.115 | 0.209 | 0.3440 |
| reb | 2023 | A3 (T60) | 27619 | 1318 | 1.2935 | +0.0007 | [-0.0005, +0.0019] | 0.255 |  | -0.049 | 0.793 | 0.113 | 0.210 | 0.3442 |
| reb | 2023 | A4 (T60) | 27619 | 1318 | 1.2768 | -0.0160 | [-0.0182, -0.0137] | 0.000 |  | -0.061 | 0.793 | 0.113 | 0.211 | 0.3419 |
| reb | 2023 | A0_30 (T30) | 27619 | 1318 | 1.2761 |  |  |  |  | -0.021 | 0.793 | 0.112 | 0.208 | 0.3392 |
| reb | 2023 | A1_30 (T30) | 27619 | 1318 | 1.2753 | -0.0008 | [-0.0022, +0.0005] | 0.213 |  | -0.030 | 0.792 | 0.113 | 0.208 | 0.3391 |
| reb | 2024 | A0 (T60) | 27583 | 1315 | 1.3140 |  |  |  |  | -0.051 | 0.787 | 0.116 | 0.215 | 0.3531 |
| reb | 2024 | A1 (T60) | 27583 | 1315 | 1.3141 | +0.0002 | [-0.0010, +0.0013] | 0.757 | 0.757 | -0.048 | 0.787 | 0.116 | 0.213 | 0.3530 |
| reb | 2024 | A2 (T60) | 27583 | 1315 | 1.3140 | -0.0000 | [-0.0011, +0.0011] | 0.989 |  | -0.046 | 0.789 | 0.114 | 0.213 | 0.3530 |
| reb | 2024 | A3 (T60) | 27583 | 1315 | 1.3151 | +0.0011 | [+0.0002, +0.0020] | 0.015 |  | -0.046 | 0.788 | 0.114 | 0.214 | 0.3534 |
| reb | 2024 | A4 (T60) | 27583 | 1315 | 1.3012 | -0.0128 | [-0.0149, -0.0108] | 0.000 |  | -0.056 | 0.790 | 0.113 | 0.215 | 0.3513 |
| reb | 2024 | A0_30 (T30) | 27583 | 1315 | 1.2963 |  |  |  |  | -0.059 | 0.788 | 0.115 | 0.214 | 0.3483 |
| reb | 2024 | A1_30 (T30) | 27583 | 1315 | 1.2965 | +0.0001 | [-0.0010, +0.0013] | 0.827 |  | -0.055 | 0.789 | 0.114 | 0.213 | 0.3483 |
| pts | 2023 | A0 (T60) | 27619 | 1318 | 3.1528 |  |  |  |  | -0.149 | 0.774 | 0.129 | 0.221 | 0.3699 |
| pts | 2023 | A1 (T60) | 27619 | 1318 | 3.1563 | +0.0035 | [+0.0008, +0.0063] | 0.017 | 0.034 | -0.171 | 0.774 | 0.131 | 0.222 | 0.3705 |
| pts | 2023 | A2 (T60) | 27619 | 1318 | 3.1538 | +0.0010 | [-0.0018, +0.0035] | 0.440 |  | -0.181 | 0.773 | 0.132 | 0.223 | 0.3699 |
| pts | 2023 | A3 (T60) | 27619 | 1318 | 3.1547 | +0.0019 | [-0.0010, +0.0047] | 0.183 |  | -0.163 | 0.774 | 0.131 | 0.222 | 0.3705 |
| pts | 2023 | A4 (T60) | 27619 | 1318 | 3.1201 | -0.0327 | [-0.0376, -0.0279] | 0.000 |  | -0.194 | 0.776 | 0.129 | 0.225 | 0.3682 |
| pts | 2023 | A0_30 (T30) | 27619 | 1318 | 3.1148 |  |  |  |  | -0.087 | 0.773 | 0.129 | 0.220 | 0.3651 |
| pts | 2023 | A1_30 (T30) | 27619 | 1318 | 3.1168 | +0.0020 | [-0.0008, +0.0047] | 0.150 |  | -0.103 | 0.772 | 0.131 | 0.221 | 0.3654 |
| pts | 2024 | A0 (T60) | 27583 | 1315 | 3.1884 |  |  |  |  | -0.113 | 0.764 | 0.134 | 0.225 | 0.3818 |
| pts | 2024 | A1 (T60) | 27583 | 1315 | 3.1890 | +0.0006 | [-0.0017, +0.0029] | 0.628 | 0.757 | -0.116 | 0.763 | 0.135 | 0.226 | 0.3818 |
| pts | 2024 | A2 (T60) | 27583 | 1315 | 3.1884 | -0.0000 | [-0.0021, +0.0021] | 0.986 |  | -0.124 | 0.764 | 0.135 | 0.227 | 0.3816 |
| pts | 2024 | A3 (T60) | 27583 | 1315 | 3.1904 | +0.0020 | [-0.0003, +0.0042] | 0.086 |  | -0.102 | 0.764 | 0.134 | 0.226 | 0.3820 |
| pts | 2024 | A4 (T60) | 27583 | 1315 | 3.1592 | -0.0292 | [-0.0339, -0.0245] | 0.000 |  | -0.122 | 0.767 | 0.131 | 0.226 | 0.3804 |
| pts | 2024 | A0_30 (T30) | 27583 | 1315 | 3.1450 |  |  |  |  | -0.123 | 0.764 | 0.135 | 0.226 | 0.3755 |
| pts | 2024 | A1_30 (T30) | 27583 | 1315 | 3.1441 | -0.0010 | [-0.0032, +0.0011] | 0.357 |  | -0.120 | 0.766 | 0.134 | 0.226 | 0.3754 |

dCRPS is arm - A0 at T-60 and A1_30 - A0_30 at T-30 (A0_30 = A0 + the production t30_* columns; T-30 uses tonight's box-score starter flags as the documented, optimistic proxy of the announced five). T-30 information alone (A0_30 vs A0) moves integer CRPS by -0.0176 (reb) and -0.0434 (pts) in 2024: the lever is the confirmed lineup, not lineup features built from it.

### Pass rule, per stat (2024 reports, 2023 selects)

| rule | reb | pts |
|---|---|---|
| 1 primary: bigs dCRPS 2024 <= -0.005, CI hi < 0, BH p < 0.05, n >= 3000, 2023 same sign | FAIL (-0.0009, CI hi +0.0025, p_BH 0.643; 2023 -0.0002) | FAIL (+0.0038, CI hi +0.0080, p_BH 0.172; 2023 -0.0027) |
| 1 no-harm: pooled point <= +0.001 and CI hi <= +0.003, both seasons | PASS (2023 -0.0001 hi +0.0013; 2024 +0.0002 hi +0.0013) | FAIL (2023 +0.0035 hi +0.0063; 2024 +0.0006 hi +0.0029) |
| 2 structure beyond a sum: A1 - A2 (bigs, 2024) <= -0.003 | FAIL (+0.0013 [-0.0015, +0.0042]; all rows +0.0002) | FAIL (+0.0055 [+0.0016, +0.0095]; all rows +0.0006) |
| 3 guards (A1, 2024 all rows): |bias| <= 0.5, cov80 in [0.75, 0.85], q0.10 PIT in 0.10 +/- 0.02, no slice (n >= 300) worse than +0.01 | PASS (bias -0.048, cov80 0.787, pit_q10 0.116 vs A0 0.116, worse slices none) | FAIL (bias -0.116, cov80 0.763, pit_q10 0.135 vs A0 0.134, worse slices none) |
| 4 attacks: A1 - A3 (bigs, 2024) <= -0.003 | PASS (-0.0032 [-0.0067, +0.0003]) | FAIL (+0.0010 [-0.0032, +0.0048]) |
| 4 attacks: A1 gain < A4 oracle gain (bigs, 2024) | PASS (A1 -0.0009, A4 -0.0170; leak flag False) | PASS (A1 +0.0038, A4 -0.0192; leak flag False) |
| 4 attacks: planted tests | PASS | PASS |
| 4 attacks: knockout | not run (conditional on rules 1-4 otherwise passing) | not run (conditional on rules 1-4 otherwise passing) |
| OVERALL | FAIL (null) | FAIL (null) |

Reading. The rule-1 floor (-0.005) is missed by a wide margin on both stats: reb bigs 2024 is -0.0009 with MDE 0.0036, pts bigs 2024 is +0.0038 (wrong sign) with MDE 0.0042; the pooled and bigs reb effects are inside +/-0.001, so this is an adequately powered null for any effect near the floor (the bigs-slice MDE of about 0.004 is close to the floor, so a true effect of -0.003 or smaller on bigs would not have been detected; the pooled MDE is 0.0012). A2 (sum of teammates' rates) ties A0 and A1, A3 (placebo) is at or above A0, so neither the additive nor the structural lineup columns carry projected-lineup information beyond production v2. The ORACLE (tonight's actual stints) gains -0.0128 (reb) and -0.0292 (pts) in 2024: the information is who actually shares the floor tonight, which T-60 cannot know and the projected five does not recover (same finding as F11). T-30: A1_30 - A0_30 is +0.0001 (reb) and -0.0010 (pts) with CIs spanning 0; confirmed starters already enter via the production t30_* columns. pts at T-60 is slightly worse than A0 in 2023 (+0.0035, CI excludes 0, BH p 0.034) and the pts q0.10 PIT guard fails for A0 itself (0.134), so pts guard failure is inherited from the baseline.

### Slices (A1 - A0, T-60; n >= 300 enforced; full table in reports/prereg_f8.md)

| stat | season | slice | n | dCRPS | CI |
|---|---|---|---|---|---|
| reb | 2023 | bigs | 6431 | -0.0002 | [-0.0046, +0.0042] |
| reb | 2023 | guards | 10854 | +0.0008 | [-0.0006, +0.0024] |
| reb | 2023 | others | 10334 | -0.0010 | [-0.0029, +0.0009] |
| reb | 2023 | HOU | 863 | +0.0005 | [-0.0077, +0.0090] |
| reb | 2023 | lineup_change | 4607 | -0.0014 | [-0.0054, +0.0026] |
| reb | 2023 | lr_chg!=0 | 27619 | -0.0001 | [-0.0015, +0.0013] |
| reb | 2023 | first15 | 4586 | +0.0011 | [-0.0029, +0.0051] |
| reb | 2023 | teammate_out | 23246 | -0.0004 | [-0.0020, +0.0011] |
| reb | 2023 | starter | 12267 | -0.0029 | [-0.0054, -0.0005] |
| reb | 2023 | bench | 15352 | +0.0021 | [+0.0006, +0.0037] |
| reb | 2024 | bigs | 6499 | -0.0009 | [-0.0047, +0.0025] |
| reb | 2024 | guards | 10873 | -0.0005 | [-0.0016, +0.0007] |
| reb | 2024 | others | 10211 | +0.0015 | [-0.0001, +0.0032] |
| reb | 2024 | HOU | 909 | +0.0054 | [-0.0028, +0.0139] |
| reb | 2024 | lineup_change | 5608 | -0.0006 | [-0.0036, +0.0025] |
| reb | 2024 | lr_chg!=0 | 27583 | +0.0002 | [-0.0010, +0.0013] |
| reb | 2024 | first15 | 4621 | +0.0003 | [-0.0029, +0.0033] |
| reb | 2024 | teammate_out | 23115 | +0.0003 | [-0.0010, +0.0017] |
| reb | 2024 | starter | 12057 | -0.0000 | [-0.0021, +0.0020] |
| reb | 2024 | bench | 15526 | +0.0003 | [-0.0011, +0.0017] |
| pts | 2023 | bigs | 6431 | -0.0027 | [-0.0080, +0.0029] |
| pts | 2023 | guards | 10854 | +0.0062 | [+0.0012, +0.0112] |
| pts | 2023 | others | 10334 | +0.0045 | [+0.0002, +0.0087] |
| pts | 2023 | HOU | 863 | +0.0055 | [-0.0113, +0.0227] |
| pts | 2023 | lineup_change | 4607 | +0.0157 | [+0.0086, +0.0228] |
| pts | 2023 | lr_chg!=0 | 27619 | +0.0035 | [+0.0008, +0.0063] |
| pts | 2023 | first15 | 4586 | +0.0097 | [+0.0021, +0.0175] |
| pts | 2023 | teammate_out | 23246 | +0.0035 | [+0.0005, +0.0066] |
| pts | 2023 | starter | 12267 | +0.0025 | [-0.0025, +0.0078] |
| pts | 2023 | bench | 15352 | +0.0043 | [+0.0012, +0.0072] |
| pts | 2024 | bigs | 6499 | +0.0038 | [-0.0005, +0.0080] |
| pts | 2024 | guards | 10873 | -0.0011 | [-0.0051, +0.0031] |
| pts | 2024 | others | 10211 | +0.0003 | [-0.0033, +0.0039] |
| pts | 2024 | HOU | 909 | -0.0048 | [-0.0186, +0.0100] |
| pts | 2024 | lineup_change | 5608 | +0.0015 | [-0.0038, +0.0068] |
| pts | 2024 | lr_chg!=0 | 27583 | +0.0006 | [-0.0017, +0.0029] |
| pts | 2024 | first15 | 4621 | +0.0071 | [+0.0003, +0.0138] |
| pts | 2024 | teammate_out | 23115 | +0.0004 | [-0.0022, +0.0030] |
| pts | 2024 | starter | 12057 | -0.0025 | [-0.0068, +0.0015] |
| pts | 2024 | bench | 15526 | +0.0030 | [+0.0002, +0.0056] |

Note: `lr_chg != 0` equals all rows because `lr_chg` is the row's `lr_nbig` minus the team's prior-10-game mean of played rows (the literal definition), which differs from zero for nearly every row; it is not a clean lineup-change indicator. `lineup_change` (projected big OUT on the real-tip-gated report) is the informative change slice.

### Mechanism (descriptive)

| stat | season | lr_nbig tercile | n | dCRPS | CI |
|---|---|---|---|---|---|
| reb | 2023 | T1 | 9207 | -0.0007 | [-0.0027, +0.0015] |
| reb | 2023 | T2 | 9206 | -0.0005 | [-0.0030, +0.0020] |
| reb | 2023 | T3 | 9206 | +0.0009 | [-0.0018, +0.0035] |
| reb | 2024 | T1 | 9195 | +0.0013 | [-0.0004, +0.0030] |
| reb | 2024 | T2 | 9194 | -0.0006 | [-0.0028, +0.0016] |
| reb | 2024 | T3 | 9194 | -0.0002 | [-0.0022, +0.0019] |
| pts | 2023 | T1 | 9207 | +0.0045 | [-0.0000, +0.0092] |
| pts | 2023 | T2 | 9206 | +0.0006 | [-0.0038, +0.0051] |
| pts | 2023 | T3 | 9206 | +0.0053 | [-0.0002, +0.0110] |
| pts | 2024 | T1 | 9195 | -0.0006 | [-0.0042, +0.0034] |
| pts | 2024 | T2 | 9194 | +0.0011 | [-0.0030, +0.0052] |
| pts | 2024 | T3 | 9194 | +0.0013 | [-0.0027, +0.0055] |

reb terciles are not monotone (2023: -0.0007, -0.0005, +0.0009; 2024: +0.0013, -0.0006, -0.0002). HOU mean reb bias, A0 -> A1: 2023 +0.024 -> +0.059 (n=863); 2024 -0.105 -> +0.016 (n=909). HOU's 2024 bias moves toward 0 but its 2024 dCRPS is +0.0054 (CI [-0.0028, +0.0139]) and 2023 moves the other way, so the Rockets story is not carried by this feature set.

### Unstated details, chosen before scoring

* big = height_in >= 82 or position contains Center, used for is_big_p, is_big_q and the bigs slice (header item (b)); guards = height_in < 78; others = every remaining played row (incl. unknown height)
* co-play weights c(p,q): F11 construction (prior 10 team games, HL 10, eligible = stint seconds in the window and last team before the date = this team, OUT removed, minutes-share fallback when p has < 300 window seconds, sum c = 4)
* S(p) T-60 = p + the 4 available teammates with the largest c (ties: more starts in the prior 10 team games, then lower player id); shorter when fewer than 4 are available
* T-30: tonight's box-score starter flags are the proxy of the confirmed five (docs/LINEUPS_KNOWN.md, optimistic); used only when exactly five are flagged for the team-game, else T-60 S. Starter p: the five starters; bench p: p + the 4 starters with the largest prior-10 co-play weight. c is NOT re-weighted at T-30 (groups A, C and lr_nbig identical), only lr_dbl, lr_h5, lr_h2 change
* T-30 comparison arm A0_30 = A0 + the production t30_* columns (stat_feature_names(lineups_known=True)); A1_30 = A0_30 + A1 columns with the three lr30_* replacements; dCRPS at T-30 is A1_30 - A0_30
* group C: OREB% = sum n_oreb / sum (n_oreb + n_dreb) over the possessions p was on the floor on offence (every rebound event of those possessions is a rebound chance), DREB% the same on defence, cumulative over all prior games (no decay), pseudo-count 1,500 chances toward the as-of team-season rate (league as-of rate if the team has no earlier game that season)
* lr_p_gap baseline = as-of mean lr_nbig of played rows with the same position code (G/F/C/other) in the same season (all seasons when < 200 rows)
* lr_chg = lr_nbig minus the mean lr_nbig of the team's played rows over its previous 10 games
* projected big OUT (lineup-change slice) = a big whose mean minutes over the games he played among the team's previous 10 is >= 20 is on the real-tip-gated OUT list
* A2 = A0 + lr_oreb_sum + lr_dreb_sum; A3 permutes all lr_* / lr30_* columns jointly across rows within team-season (seed 0); A4 uses tonight's actual stints for c and S
* BH family {reb, pts} per season: the table p / p_bh use the all-rows A1-A0 p; rule 1 uses the bigs-slice p with BH over {reb, pts}
* rules 2 and 4 (A1 vs A2, A1 vs A3, A1 vs A4) are evaluated on the bigs slice in 2024 (the slice of rule 1); the all-rows values are reported beside them
* rule 3 guards use A1 all rows in 2024 (2023 reported); the slice guard uses the T-60 A1-A0 slice table (declared slices only, all excluded)
* knockout (rule 4) is run only if rules 1-4 otherwise pass; a group 'carries' the gain when the A1 bigs-slice gain without it is <= 20% of the A1 gain (>= 80% lost)
* G2 team-games use the not-tip-gated union of player_availability 'out' rows (coverage count)
* G4 gap over the four realised-minutes buckets (<5, 5-10, 10-20, >20) and the three reb terciles per season; DNP rows are audited but outside the gap
* tll = mean threshold log loss of P(y >= n) at reb 4/6/8/10 and pts 10/15/20 on the integer grid
* A0 reproduction: integer CRPS vs reports/lower_tail/candidates.json 'off' (n and crps_int) to 1e-6

Not run here (adversary's list or conditional): second placebo (projected five permuted across games within team), HOU-excluded and `lr_cover < 0.5` replications, knockout arms. None is needed to read a null.

### Draft ledger rows

See docs/TEST_LEDGER.md T252-T263 (appended 2026-10-10, not committed).

Freeze: 2026-10-10 16:36 CT, commit e30b750, sha256 of everything above the line: 2a8e581e7f03943da4ea7410c10d516d7adca27f58e9c892b5b07d8f0e682240 (first 8: 2a8e581e). Verify with the awk command in the header.
