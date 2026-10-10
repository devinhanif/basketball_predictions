# F11 lineup context: projected on-court teammates' traits as props features (pre-registration)

Status: DRAFT 2026-10-09, to be committed by the maintainer BEFORE any fit. Everything above the line
`=== RESULTS BELOW ===` is frozen. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F11_LINEUP_CONTEXT.md | shasum -a 256`
(first 8 hex chars into the results section and every ledger row). No candidate exists yet.

## What Devin said
> "Good shooters create gravity ... Steph and Klay let KD and Draymond score. Teams with many rebounds
> play differently." / "Everything is a lineup property": a player's prop distribution conditional on the
> five around him, not his own history alone. (FAN_KNOWLEDGE, round 4)

## Hypothesis
H1: a player's stat distribution depends on who is expected to be on the floor with him. Features composed
from teammates' as-of traits (shooting gravity, rebounding), weighted by projected co-play, lower
integer-support CRPS of context_residual beyond production's existing teammate-OUT/vacated features.
Composing from player traits (not lineup IDs) means unseen lineups are handled. Null: no change.
Descriptive support (not tested here): Draymond make rate .540 with Curry vs .501 (984 vs 407 poss.);
league +0.039 ppp with the team's top 3PM shooter on (600k vs 450k poss.).
Protects against: the repo's own record, where every own-history feature went null and every win was about
who is on the floor (injury report). Also against a lineup feature that is secretly tonight's lineup.

## Data exposure (honest)
Seen: the descriptive on/off numbers above (stints + possessions, 2022-24, no model). Production OOF
aggregate for 2023/2024. Not seen: any lineup-conditional model result. Season 2025 is not loaded; only
`season <= 2024`; `nba.duckdb` opened `read_only=True`. Selection 2023, report 2024; walk-forward month
blocks, 2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU, 4 stats.

## Fixed design (constants final)
Flag `ContextResidualConfig.lineup_context` (default `off`, byte-identical). Five columns appended via
`stat_feature_names(stat, extra=...)` to the mean and scale heads of ALL four stats.

Step 1, player traits (all as-of the game date, decayed half-life 40 games, empirical-Bayes shrunk):
* `thr3_q` 3-point threat: 3PM per 36 min, shrunk to the league mean with pseudo-count 300 minutes.
* `lift_q` on-court ppp lift: team points per possession with q on minus off over prior stints/possessions,
  shrunk toward 0 with pseudo-count 1,500 on-court possessions.
* `rpm_q` rebounds per minute, shrunk with pseudo-count 300 minutes.
Step 2, projected co-play weight `c(p,q)` for teammate q of p, from stints in the team's PRIOR 10 games only
(decay half-life 10 team games): share of p's floor seconds with q also on the floor. Teammates who
are OUT on the real-tip-gated official report (`tip_source="real"`) get `c = 0`; the freed mass is
redistributed over remaining teammates in proportion to `c`, so `sum_q c(p,q) = 4`. If p has < 300 stint
seconds in the window, or q is new to the team, fall back to `c(p,q) ∝ min10_q` over the available
roster, rescaled to 4. Only players in a prior-10-game team stint, or on the pre-tip active roster,
are eligible; tonight's starters, minutes, box score and stints are never read.
Step 3, columns: `lc_thr3 = sum_q c * thr3_q`; `lc_lift = sum_q c * lift_q`; `lc_rpm = sum_q c * rpm_q`;
`lc_top3 = c(p, q*)` with q* the team's top as-of 3PM-per-game teammate among the available (0 if q* is OUT
or p = q*); `lc_cover` = share of `c` mass that came from stints (not the fallback).
Own closer share (named in Devin's list) is NOT in this study: it belongs to F12 (`docs/prereg/F12_CLOSING_RISK.md`)
so the two features are not tested twice in one family.

## Arms (identical rows/seeds/calibration windows)
* A0 production (reproduces pts integer CRPS 3.1528 / 3.1884).
* A1 candidate = A0 + the 5 columns.
* A2 placebo: A1 with the 5 columns permuted across rows within team-season (keeps marginals, breaks lineup link).
* A3 ORACLE (descriptive; leak by construction, never a model): same construction with tonight's ACTUAL
  stints for `c`; measures the ceiling and the size of the leak a careless build would produce.
* A4 ablation: A1 without `lc_lift` (the noisiest trait) to show it is not carrying the result.

## Metrics, support, inference
Integer support (`ceil(q - 0.5)`) for every arm. Primary: paired dCRPS (A1 - A0) per stat per season, all
rows, game-clustered bootstrap (2000, seed 0). Mean bias, A1.1 80% PIT coverage and lower-tail PIT
(q0.10, q0.20), threshold log loss (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2) secondary. BH over the 4 stats
per season. Per-stat MDE (1.96 x clustered SE) printed beside each result. Slices (descriptive plus
guard): cold start `n_prior < 20`, starter/bench, first 15 team games, teammate-OUT (>= 1 rotation OUT),
traded this season, `lc_cover < 0.5`. Mechanism check (descriptive): dCRPS for reb by tercile of `lc_rpm`,
for pts/fg3m by tercile of `lc_thr3`; expected monotone if the story is real.

## Minimum data checks (fail = stop, no model result)
1. `stints` covers >= 95% of 2022-24 regular-season games (both teams); `possessions` coverage for
   `lift_q` the same; otherwise drop `lc_lift` for ALL arms (declared fallback) and report coverage.
2. Missingness audit: NULL rate of every `lc_*` column by realised-minutes bucket (<5, 5-10, 10-20, >20)
   and by outcome tercile; max pp difference between buckets <= 2. Features are built for ALL
   active-roster rows including those that did not play, then filtered (so NULL cannot depend on playing).
3. Planted same-game test: edit the target game's stints, its box score and starter flags (insert fake
   stints, zero a teammate's minutes); every `lc_*` value for that game must be byte-identical. A second
   plant moves a teammate to the OUT list AFTER the real tip: no change. Both are CI tests.
4. Planted-future: any stint dated >= game date is ignored (feature unchanged).
5. A0 reproduces production to 1e-6; flag `off` byte-identical.
6. `sum_q c(p,q) = 4` within 1e-9 for every row with >= 4 available teammates.

## Pass rule (per stat; 2023 selects, 2024 confirms; all must hold)
1. dCRPS point <= -0.005, CI upper < 0, BH p < 0.05.
2. Guards: |bias| <= 0.5; 80% PIT coverage in [0.75, 0.85]; lower-tail PIT q0.10 within +/- 0.02 of 0.10;
   no slice with n >= 300 worse by more than +0.01 CRPS.
3. Attack gates: A1 beats A2 by point <= -0.003 (else the gain is just extra columns); dropping
   `lc_lift` (A4) leaves >= 50% of the A1 gain; A1 gain is smaller than A3 (if A1 >= A3 something is
   wrong: audit for leakage before anything else); the planted tests of checks 3-4 pass.
4. Honest ceiling note: production already has `vac_*`, `n_out_rot`, `vac_min`; the gain beyond them is
   the only thing counted, because A0 contains them.
Secondary metrics cannot rescue a failure. A stat can pass alone; no promotion; a confirmed stat gets a
drafted (not appended) holdout row and a shadow-log recommendation. Season 2025 untouched.

## Kill criteria
* Check 2 or 3 fails: stop; the build is a leak or asymmetric; fix is a new rule, not an edit.
* No stat passes on 2023: close. Do not add traits (assist share, position, pace, opponent matchup) or
  other windows afterwards; at most ONE pre-registered follow-up (e.g. learned trait embeddings), then closed.
* A1 beats A3: treated as a presumed leak and audited by qa before any number is reported as a win.

## Outputs (exact)
`reports/prereg_f11/results.json`, `reports/prereg_f11.md`: `[stat, season, arm, n_rows, n_games, crps_int,
dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`; `[stat, season, slice, n, dcrps,
ci_lo, ci_hi]`; `[col, bucket, null_rate]` for the audit; `[test, ok]` for checks 1-6; seeds, SHA, sha256.

=== RESULTS BELOW ===

(not run)

Run 2026-10-10; frozen sha256 2e263279 (verified against b5a1dab). Ledger T244-T251.

Frozen sha256 (first 8): 2e263279. Checks 1-6: all passed; seasons <= 2024 only, nba.duckdb read-only, CPU, seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}.

* pts: 2023: n=27619 dCRPS +0.0016 [-0.0012, +0.0045] p_BH 0.795 MDE 0.0029 | 2024: n=27583 dCRPS -0.0020 [-0.0040, +0.0002] p_BH 0.278 MDE 0.0021
* reb: 2023: n=27619 dCRPS -0.0002 [-0.0015, +0.0011] p_BH 0.795 MDE 0.0013 | 2024: n=27583 dCRPS -0.0004 [-0.0015, +0.0006] p_BH 0.450 MDE 0.0010
* ast: 2023: n=27619 dCRPS -0.0001 [-0.0010, +0.0008] p_BH 0.795 MDE 0.0009 | 2024: n=27583 dCRPS +0.0004 [-0.0003, +0.0011] p_BH 0.450 MDE 0.0007
* fg3m: 2023: n=27619 dCRPS -0.0002 [-0.0008, +0.0005] p_BH 0.795 MDE 0.0007 | 2024: n=27583 dCRPS +0.0004 [-0.0001, +0.0010] p_BH 0.278 MDE 0.0006

Verdict: confirmed on 2024 = []; passes on 2023 = []; kill criterion 'no stat passes 2023: close' = True. Nothing promoted.

Implementation readings fixed before any arm was fitted (not rule changes): see the module docstring of research/eval/f11_lineup_context.py. The doc's `ContextResidualConfig.lineup_context` flag was not added (nba/ is out of scope); the columns enter through explicit feature-name lists, so production is untouched.
