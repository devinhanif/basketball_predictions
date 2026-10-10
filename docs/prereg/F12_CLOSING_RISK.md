# F12 closing-lineup risk: a pre-tip feature group for the short-minutes event (pre-registration)

Status: DRAFT 2026-10-09, to be committed by the maintainer BEFORE any fit. Everything above the line
`=== RESULTS BELOW ===` is frozen. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F12_CLOSING_RISK.md | shasum -a 256`
(first 8 hex chars into the results section and every ledger row). No candidate exists yet.

## What Devin said
> "A young player shows potential AND clear deficiencies ... so he may not play the 4th quarter of a tight
> game. The spread comes from closing-lineup risk (minutes), pre-tip predictable from game closeness (Elo
> margin) and the player's closer share." (FAN_KNOWLEDGE, round 4)

## Hypothesis
H1: given pre-tip P(close game) and the player's as-of habit of finishing close games, the minutes
distribution and the pts lower tail of young players are better described. Mechanism: coaches trust
veterans to close (Q4/Q1-3 floor-time ratio .335 close vs .312 blowouts); young players do not benefit
(.333 vs .341). Null: no change. Protects against: calling a spread "youth" when it is a minutes-allocation
effect already captured by existing features (`abs_margin`, `min10`, `starter10`).
Honest power warning: the effect is about +/-7% of ~25% of the game, i.e. roughly 1-2 minutes at most for a
30-minute player, with a game-level noise SD of ~7. Expect a small effect; the floors below are set for that.

## Data exposure (honest)
Seen: the descriptive ratios above (stints by period, 2022-24, vets vs <= 2 seasons; no model). Production
OOF aggregate and the LOWER_TAIL/MINUTES_V2 results for 2023/2024 (so the mixture's behaviour is known).
Not seen: any closing-risk model result. Season 2025 not loaded; `season <= 2024`; `read_only=True`.
Selection 2023, report 2024; month-block walk-forward, 2022 warm-up, rows = played, `n_prior >= 5`, seed 0.

## Fixed design (one design; constants final)
Four as-of columns, `cl_*`, appended through `extra=` ONLY to the short-minutes classifier `pi` of the
LOWER_TAIL mixture and to the S-classifier of the MINUTES_V2 mixture M1. All other heads, constants and
the calibration are unchanged. So this is a FEATURE FOR pi, not a separate arm of the distribution.
* `cl_pclose`: pre-tip P(final |margin| <= 5) = `Phi((5.5 - m)/12) - Phi((-5.5 - m)/12)`, `m` = production
  `exp_margin` (injury-Elo expected home margin, signed for the player's team), 12 = fixed margin SD.
* `cl_cs_close`: the player's as-of closer share: decayed (half-life 10 of his prior CLOSE games, final
  margin <= 5) mean of `q4r = (Q4 floor minutes / 12) / (Q1-Q3 floor minutes / 36)` from `stints` by
  period (OT excluded), shrunk toward the league mean of his seasons bucket {1-2, 3-4, 5+} with
  pseudo-count 5 games.
* `cl_cs_blow`: the same over prior games with final margin >= 15 (pseudo-count 5).
* `cl_mult = p_c * cs_close + p_b * cs_blow + (1 - p_c - p_b) * 1`, `p_b = P(|margin| >= 15)` from the same
  Normal; expected Q4 intensity relative to Q1-3.
Seasons-played is computed independently of F9 (distinct prior seasons + 1) only for slicing.

## Arms (identical rows, seeds, windows)
* B0 = LOWER_TAIL mixture with base features (reproduces LOWER_TAIL mixture 2023/2024 pts integer CRPS
  to 1e-6; fixed constants: S = 1[minutes < 0.5 min10], pi in [0.005, 0.5]).
* B1 candidate = B0 with `cl_*` in the pi classifier only.
* B2 descriptive: `cl_*` in pi AND in the mean/scale heads (shows whether the feature acts via the event).
* B3 placebo: B1 with `cl_*` permuted across rows within season.
* Minutes: M1 (MINUTES_V2 mixture with base features, `nba/props/minutes_v2.py`) vs M1c = M1 with `cl_*` in its S classifier; the
  production minutes path P and recency baseline R are carried unchanged for context.

## Metrics, support, inference
* Props: integer-support pts CRPS per season (`ceil(q - 0.5)`), paired dCRPS (B1 - B0), game-clustered
  bootstrap (2000, seed 0); reb/ast/fg3m run as part of that family. Lower-tail A1.1 PIT coverage at
  q0.05/0.10/0.20 and upper-tail guards; mean bias. Threshold log loss pts 10/15 etc. secondary.
* Minutes (continuous support): dCRPS M1c - M1, same bootstrap. ONE BH family per season of 5 primary
  tests: the 4 props stats (B1 vs B0) plus the minutes test.
* Event quality of pi: log loss and Brier of `pi` for S (B1 vs B0), AUC; reliability of `pi` by decile.
* Slices: veterans (seasons index >= 5), young (seasons index <= 2), young starters, close games (|final margin| <= 5)
  vs blowouts (>= 15) as OUTCOME strata (descriptive only, outcomes not inputs), `n_prior < 20`, teammate-OUT.
  Young x close-game pts lower-tail PIT q0.10 is the named mechanism check.
* Power: MDE (1.96 x clustered SE) printed per test; young-player slice n reported.

## Minimum data checks (fail = stop)
1. `stints` covers >= 95% of 2022-24 games with period; Q1-Q3/Q4 minutes per player-game reconcile to box
   minutes within 1.0 minute on >= 95% of player-games.
2. Descriptive replication on 2022-24 stints reproduces the ratios (vets close >= blowout by >= 4%; young
   within +/- 4%). If not reproduced the premise is stale: stop, report, no model.
3. Missingness audit: `cl_cs_*` NULL rate by realised-minutes bucket (<5, 5-10, 10-20, >20) differs by <= 2 pp;
   features exist for all active-roster rows (not just those who played).
4. Planted same-game test: altering the target game's stints, final score, or box score leaves all `cl_*`
   unchanged; a prior-close-game flag uses only games with game_date < tonight (planted-future test). The
   final margin of PAST games is allowed (known), tonight's is not.
5. B0 and M1 reproduce their documented values; `cl_pclose` in [0,1]; monotone decreasing in |m|.

## Pass rule (2023 selects, 2024 confirms; all must hold)
Props (pts is the hypothesis stat; other stats pass independently):
1. B1 - B0 dCRPS point <= -0.003 (smaller floor than -0.005 because the mixture's lower tail is a thin slice
   of CRPS; declared before seeing), CI upper < 0, BH p < 0.05.
2. Lower-tail PIT q0.10 and q0.20 within +/- 0.02 of nominal and no worse than B0 by more than 0.005;
   upper-tail guards within +/- 0.02; |bias| <= 0.5.
3. B1 beats B3 (placebo) by point <= -0.002. Event check: pi log loss improves, point < 0, CI upper < 0.
Minutes: M1c - M1 dCRPS point <= -0.03 minutes, CI upper < 0, BH p < 0.05; mean bias <= 0.5 min.
The headline "F12 confirmed" requires pts gate AND the minutes gate in both seasons; passing only one is
reported as partial and not shipped. Secondary metrics cannot rescue a failure.
Nothing promoted; a confirmed arm gets a drafted (not appended) holdout row and a shadow-log
recommendation (arm `_lt` plus `cl_*`). Season 2025 untouched.

## Kill criteria
* Check 2 fails (premise not replicated) or check 3/4 fails: close.
* Null on 2023 pts and minutes: close. Do not try other margin cut-offs (5/10), OT handling or decay half-lives
  afterwards; at most ONE pre-registered follow-up, then closed.
* If B2 differs from B1 by more than B1 differs from B0 (the signal acts in the mean, not the event), report it
  and open a NEW pre-registration; do not reinterpret this one.

## Outputs (exact)
`reports/prereg_f12/results.json`, `reports/prereg_f12.md`: `[target, season, arm, n_rows, n_games, crps,
dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, pit_q05, pit_q10, pit_q20]`; `[season, pi_logloss_B0, pi_logloss_B1,
auc_B0, auc_B1]`; `[slice, n, dcrps]`; replication table for check 2; `[check, value, threshold, ok]`;
seeds, SHA, sha256.

=== RESULTS BELOW ===

(not run)
