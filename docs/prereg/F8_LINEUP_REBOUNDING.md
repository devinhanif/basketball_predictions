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

(not run)

Freeze: 2026-10-10 16:36 CT, commit e30b750, sha256 of everything above the line: 2a8e581e7f03943da4ea7410c10d516d7adca27f58e9c892b5b07d8f0e682240 (first 8: 2a8e581e). Verify with the awk command in the header.
