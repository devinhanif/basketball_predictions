# FOUR_FACTORS: as-of pace and Dean Oliver's four factors as props context (pre-registration)

Status: FROZEN 2026-10-10 16:58 CT on Devin's confirmation (message: "do everything", 2026-10-10 ~19:30 CT; docs/DECISIONS.md). Drafted 2026-10-10 ~17:55 CT, NOT FROZEN. Committed by the maintainer's hand BEFORE
any fit. Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/FOUR_FACTORS.md | shasum -a 256` (first 8 hex chars into the
results section and every ledger row). Season labels are the repo's `games.season`: 2023 = 2023-24, 2024 = 2024-25,
2025 = 2025-26 (frozen holdout, never loaded).

## What Devin said
> "Are you incorporating the team's pace, and the four factors of advanced stats? Did you do that yet?" (2026-10-10)
Answer at the time: no. The game model carries a points-total pace proxy and team turnovers; the props model carries rolling
team points for/against, opponent allowed per stat, rest, b2b and the Elo margin. No possession-based pace, no eFG%, TOV%,
ORB% or FT rate anywhere.

## Hypothesis
H1: the expected possession count of tonight's game (pace) and the as-of efficiency profile of the player's team on offence
and of the opponent on defence (four factors) lower integer-support CRPS of `props_context_residual` beyond production's
existing team-context columns. Null: no change.
Protects against: the repo's record (every architecture-only idea lost; information wins) by testing a small, mechanistic,
public-information feature set once, with a declared ceiling, and closing it either way. Protects against the earlier
untested pace/opponent multiplier (ledger group F, never bootstrapped, rejected as evidence) and the ctxres_v2 opponent
ridge (a NULL-pattern leak) by building from our own possession parse with a missingness audit and planted tests.

## Data exposure (honest)
Seen: production OOF aggregates for 2023/2024; the descriptive model-miss analyses of 2026-10-10 (none used pace or factors);
the gate numbers below. Not seen: any pace- or factor-conditional model result on this harness. Season 2025 never loaded;
`season <= 2024` asserted; `nba.duckdb` opened `read_only=True`. Selection 2023, report 2024; walk-forward month blocks,
2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU, 4 stats.

## Fixed design (constants final)
Flag `ContextResidualConfig.four_factors` (default `off`, byte-identical). Columns appended via
`stat_feature_names(stat, extra=...)` to the mean and scale heads of ALL four stats.

Source tables: `possessions` (one row per offensive trip; `off_team`, `def_team`, `pts`, `fta`, `n_oreb`, `outcome`) and
`player_game_stats` summed to team-game (fga, fgm, fg3m, fta, tov, oreb, drb). All quantities are team-game level, as-of the
game date: rolling mean over the team's PRIOR 20 games (`shift(1)`, min 5), shrunk to the league rolling mean (all team-games
strictly before, same tie-break as `nba.features.team_features`) with pseudo-count 10 games. Season boundaries: windows cross
seasons (the same convention as production's `r20_*`).

Per team-game raw inputs: `poss` = count of possessions with the team on offence; `pace48 = poss * 48 / minutes_played`
(`minutes_played` = 48 + 5 per overtime period, from `games`); `efg = (fgm + 0.5 fg3m) / fga`; `tov_pct = tov / poss`;
`orb_pct = oreb / (oreb + opp_drb)`; `ftr = fta / fga`. The same four for the opponent's offence in that game define what
this team ALLOWED on defence.

Columns (9):
* `ff_pace`: expected game pace = mean of the two teams' as-of `pace48`, minus the league as-of mean (centred).
* `ff_off_efg`, `ff_off_tov`, `ff_off_orb`, `ff_off_ftr`: the player's team, offence, as-of, centred on the league mean.
* `ff_def_efg`, `ff_def_tov`, `ff_def_orb`, `ff_def_ftr`: the opponent's ALLOWED factors, as-of, centred.
No player-level, lineup or tonight-specific quantity enters; tonight's box score, stints and possessions are never read.

## Arms (identical rows/seeds/calibration windows)
* A0 production (reproduces pts integer CRPS 3.1528 / 3.1884 for 2023 / 2024).
* A1 pace only = A0 + `ff_pace`.
* A2 candidate = A0 + all 9 columns.
* A3 placebo: A2 with the 9 columns permuted across rows within team-season (keeps marginals, breaks the game link).
* A4 ablation: A2 without `ff_pace` (is the gain, if any, the factors or just pace?).

## Metrics, support, inference
Integer support (`ceil(q - 0.5)`) for every arm. Primary: paired dCRPS (arm - A0) per stat per season, all rows,
game-clustered bootstrap (2000, seed 0). Mean bias, A1.1 80% PIT coverage, lower-tail PIT (q0.10, q0.20) and threshold log
loss (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2) secondary. BH over the 4 stats per season, separately for A1 and A2 (two
declared families of 4; a stat that passes under A1 but not A2 is reported as "pace carries it"). Per-stat MDE
(1.96 x clustered SE) beside each result. Slices (descriptive plus the guard in pass rule 2): starter/bench, first 15 team
games, teammate-OUT (>= 1 rotation OUT), `ff_pace` terciles, opponent `ff_def_efg` terciles. Mechanism check (descriptive):
dCRPS for pts/fg3m by `ff_pace` tercile (expected largest in the fast tercile), for reb by `ff_def_orb` tercile.

## Minimum data checks (fail = stop, no model result). Measured 2026-10-10 on `nba.duckdb` read-only, seasons 2022-2024
1. `possessions` covers >= 95% of regular-season games with a result. Measured: 1320/1320, 1318/1318, 1315/1315 (pass).
2. Team-game possession counts in [80, 130] for >= 99.5% of team-games. Measured: 2 of 7,906 below 80 (99.97%; pass); those
   team-games take the league as-of mean for that game (declared fallback) and are counted in the results.
3. Box inputs (fga, fgm, fg3m, fta, tov, oreb) non-null for every team-game. Measured: 0 nulls of 7,906 (pass).
4. Possession points reconcile with box points per team-game: mean |diff| <= 0.5. Measured: 0.02, p99 0.0 (pass).
5. Missingness audit: NULL rate of every `ff_*` column by realised-minutes bucket (<5, 5-10, 10-20, >20) and by outcome
   tercile; max pp difference between buckets <= 2. Features are built for all active-roster rows, then filtered.
6. Planted same-game test (CI): editing the target game's possessions, box score and overtime count leaves every `ff_*`
   value byte-identical. Planted-future: any team-game dated >= game date is ignored.
7. A0 reproduces production to 1e-6; flag `off` byte-identical.

## Pass rule (per stat; 2023 selects, 2024 confirms; all must hold)
1. dCRPS point <= -0.005, CI upper < 0, BH p < 0.05 (within its family, A1 or A2).
2. Guards: |bias| <= 0.5; 80% PIT coverage in [0.75, 0.85]; lower-tail PIT q0.10 within +/- 0.02 of 0.10; no slice with
   n >= 300 worse by more than +0.01 CRPS.
3. Attack gates: the passing arm beats A3 by point <= -0.003 (else the gain is extra columns, not information); if A2 passes,
   A4 must keep >= 50% of the A2 gain OR A1 must pass (so we know what carried it); planted tests of check 6 pass.
4. Honest ceiling: A0 already has `r20_pts_for`, `r20_pts_against`, `opp_allow_<stat>`, `exp_margin`; those are crude
   pace-and-efficiency proxies, and only the gain beyond them counts.
Secondary metrics cannot rescue a failure. A stat can pass alone; no promotion; a confirmed stat gets a drafted (not
appended) holdout row and a shadow-log recommendation. Season 2025 untouched.

## Kill criteria
* Check 5 or 6 fails: stop; the build is a leak or asymmetric; the fix is a new rule, not an edit.
* No stat passes on 2023 under either family: close. Do not add windows, decay rates, lineup-level factors, opponent
  position-specific factors or home/away splits afterwards; at most ONE pre-registered follow-up, then closed.

## Outputs (exact)
`reports/prereg_four_factors/results.json`, `reports/prereg_four_factors.md`: `[stat, season, arm, n_rows, n_games,
crps_int, dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`; `[stat, season, slice, n, dcrps,
ci_lo, ci_hi]`; `[col, bucket, null_rate]` for the audit; `[test, ok]` for checks 1-7; seeds, SHA, sha256. Code under
`research/` only (`research/eval/four_factors_eval.py`, `research/features/four_factors.py`); the production flag is a
no-op until a promotion decision, which is Devin's.

=== RESULTS BELOW ===

(not run)

Freeze: 2026-10-10 16:58 CT, commit 918e03c, sha256 of everything above the line: 0d87c2cea7ad257798918927b4065070ac8b6585d3fa67d5ec469f4d7a67f441 (first 8: 0d87c2ce). Verify with the awk command in the header.
