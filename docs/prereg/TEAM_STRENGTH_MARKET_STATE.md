# TEAM_STRENGTH_MARKET_STATE: does a team-strength state fed by the market's past closing margins predict the next winner better than injury-Elo, with the game's own line withheld? (pre-registration)

Status: **DRAFT (modeler, 2026-10-23 evening). NOT FROZEN. No fit, no outcome read.** Not a rule until the main session commits
`prereg(TEAM_STRENGTH_MARKET_STATE): <sha256>` on `main` before any arm is fitted. Everything above `=== RESULTS BELOW ===` freezes.
Season labels: 2023 = 2023-24, 2024 = 2024-25; 2025 = 2025-26 (frozen holdout, never loaded here). Reading-log note:
`docs/research/team_strength_and_movers_2026-10-23.md` (Part A). Work label: Devin's request (2026-10-23 ~20:00 CDT).

Scope update (Devin, 2026-10-23 ~20:05 CDT: "incorporate the market rates into the model instead of trying to beat it"): this rule is now the BASE layer
of a market-anchored design: the market-implied rating is the strength estimate for games with no own-game line, and its test (own-game line withheld) is the only
market-anchoring question that is powered retrospectively. The pooled residual on the own-game line (logit p = a logit p_mkt + b logit p_model + c) and the
freshness covariate are in `docs/prereg/TEAM_STRENGTH_MARKET_ANCHOR_LIVE.md` (live only: its retrospective power is nil).

## What Devin asked
> "find the best mathematical model to estimate team strength, and players on a new team."
The honest survey says: for a game that has a line, the line is the best estimate and we do not beat it (T235/T236; T472; CLAUDE.md
Brier 0.0064 worse than Kalshi). The one candidate that adds **new pre-tip information** to a strength rating is the market's own past
view of each team, because it already contains injuries, lineups and trades. This rule tests exactly that, and decomposes the gap.

## Hypothesis
H1: a Kalman-filtered team-strength state whose observations are the market-implied margins (closing spreads) of PAST games, mixed into
the production injury-Elo second stage as one extra feature (A2), has lower per-game log loss than production `rung0_injury_elo` (A0)
on 2023 and 2024 games, with the game's own line (every snapshot, every book) never an input.
H0: no change. Counter-hypothesis up front: the Elo-to-market disagreement (logit sd 0.37-0.40) is mostly game-day information
(rest, last-hours lineups, injuries) that no past-games rating carries; then A2 ties A0 and team-strength modelling is closed for the
winner target.
Expected size (Claude, before any run): -0.004 (80% interval -0.009 to 0.000) for A2 - A0; see `docs/PREDICTIONS_LEDGER.md`.

## Lineage (playbook question 4)
* T091/T128 injury-Elo = A0; T178 (Elo family wins, stack fails playoffs slice); T182, T318-T319 (Elo hyperparameters and history: null).
* T235/T236 H3 (injury-Elo vs Pinnacle at T-60: +0.0212 [+0.0126, +0.0300], +0.0107 [+0.0006, +0.0211]) is the gap; this rule measures how
  much of it is strength estimation. T472, T501, T502 (market as an input to props/totals: null/closed) used the OWN-game line for props
  and totals. This rule never uses an own-game line and scores game winners, so it is not a re-run; but it shares the T502 risk (cannot
  separate "features know something" from "market mis-calibrated"), handled below by the information-matched control and the placebo.
* ROSTER_CONTINUITY (a) outcome-free ceiling for roster-aware season starts was -0.0010 per early game: the season-start piece is small.
* Guards carried: planted-future identity, past-only placebo, shuffled-input control (OPP-ridge and PRESEASON_ROLE_PRIOR incidents),
  A0 reproduced to 1e-6 before any arm is scored, real-tip report gating, 2025 never loaded.

## Data exposure (honest)
Seen: stored OOF probabilities of A0 and Pinnacle T-60 moneylines in aggregate (T235/T236, and this draft's label-free gap table);
Pinnacle moneyline-to-spread slope (label-free). Not seen: any number where a rating built from past lines predicts a later game's
result. Season 2025 never loaded; `season <= 2024` asserted in every query; both DBs `read_only=True`.

## Fixed design (constants final; nothing here is fitted on outcomes)
**Inputs.** `data/odds/odds_history.duckdb` table `odds_history` (market `spreads`, `snapshot_kind='t5'`, `side in ('home','away')`),
`nba.duckdb` `games` (ids, dates, teams, scores for the update step of PAST games only; game ids starting `002`, `003`, `004` as in the
stored OOF), stored OOF `data/injury_elo_realtip/oof_predictions.parquet` (model `rung0_injury_elo` and `rung0_mov_elo`, probabilities only),
the production injury features (`nba/models/injury_elo.py`, `configs/injury_elo.yaml`, `tip_source: real`).
**Evaluation set.** Exactly the stored OOF game keys: 2023 n 1,318; 2024 n 1,315 (playoffs/play-in included, as in ELO_HISTORY_WINDOW).
Identical keys for every arm, asserted. (nba.duckdb now holds 1,320 games in 2024: the five restored neutral-site games post-date the
stored OOF and stay out.)
**Observation.** For game g with a spread row at T-5: y_g = -(point of the home side) + logit(p_cover)/0.1325 where p_cover is the home
side's de-vigged (multiplicative) cover probability and 0.1325 is the market's own logit-per-point (Pinnacle T-5 moneyline logit on spread
margin, 2023 slope 0.1331 n 1,302, 2024 0.1323 n 1,292, intercept 0.00, label-free). Book: Pinnacle; if absent, the median y over all books
with a T-5 spread for that game (>= 5 books in 1,318 / 1,315 games). The T-5 snapshot of game g is used only after g's date has passed.
**State.** theta_i in points, one scalar and one variance P_i per team. Per game date: predict every game of the date from theta as of the
end of the previous date; then, for each game of the date in game-id order, update (diagonal-covariance two-team Kalman):
S = P_h + P_a + R; innov = y_g - H_t - (theta_h - theta_a); theta_h += (P_h/S) innov; theta_a -= (P_a/S) innov;
P_h *= 1 - P_h/S; P_a *= 1 - P_a/S. Between dates P_i += q * (days since the team's last game)/2.
Constants (final, chosen from stated reasoning, not tuned): R = 6.25 (2.5 points: market-vs-effective-strength noise), q = 0.11 per game
(season drift sd about 3 points over 82 games), P0 (season start) = 16 (sd 4 points, roster churn), season boundary theta <- 0.609 * theta
(production Elo carryover, configs/mov_elo_tuned.yaml). Home edge H_t = as-of mean of y_g over all earlier lined games with prior
mean 2.5 and prior weight 50 games. 2023 season start (no earlier lines): theta_i = (Elo_i - 1500)/28 from the production Elo replay at the
last 2022 date, with P0 = 16 (Elo points per point of margin = 28, an outside constant, flagged: it only sets a prior the filter overrides
within ~10 games); 2024 start: carried theta from the end of 2023.
**Second stage (shared with A0, identical code path).** Walk-forward by calendar month as in production (coefficients for a game never saw
that game or any later one; ridge lambda 5.0 on standardised coefficients; refit_block month; min_signal_games 150):

    logit p(home) = E + c1 * (M - E) + b * (V_out_away - V_out_home)/10 + g * (V_doubt_away - V_doubt_home)/10

where E = production MOV-Elo logit, M = 0.1325 * (theta_h - theta_a + H_t) the market-state logit, b, g as in production.
**Arms (all on the same keys):**
* A0 production: c1 = 0 (reproduces the stored `rung0_injury_elo` OOF to 1e-6 first; stop if not).
* A1 market-state offset: c1 = 1 fixed (the pure replacement), b, g refit.
* **A2 market-state mix: c1 fitted walk-forward (the candidate).**
* C2 information-matched control: the same filter fed with realised margins (capped at +/-25) in place of y_g, c1 fitted. Isolates the
  value of the market stream from the value of the Kalman architecture.
* P2 past-only placebo: the filter fed with the game's y_g replaced by the y of a random other game in the same 14-day block (teams
  scrambled, seed 20261023), c1 fitted. Must give nothing.
* Reference (not an arm): Pinnacle T-60 de-vigged moneyline (own-game line), the bar nothing here is expected to clear.

## Metrics, support, inference
Per-game win log loss (primary), Brier (secondary); paired delta (arm - A0) per season; game-clustered bootstrap (2000, seed 0) and a
date-block bootstrap (14-day blocks) as the clustering sensitivity; reliability in ten bins, ECE. Support: binary game outcome (no integer
grid issue). Slices (fixed now, descriptive unless stated): first 15 team games of the season (burn-in; 2023 has no prior-season lines),
rest of season, playoffs + play-in (guard), home favourites, close games (|E| < 50 Elo), back-to-back second nights (the market's edge area),
games where either team has a rotation player OUT on the report, and games with more than 150 Elo points of disagreement between E and M.
**Family.** Primary = A2 - A0 in 2023 (select) and 2024 (report): an intersection (both must pass), single hypothesis, no BH needed;
descriptive contrasts {A1 - A0, C2 - A0, P2 - A0, A2 - C2} x 2 seasons = 8, BH q = 0.10, reported but not decision-bearing except where a
pass condition below names them.

## Minimum data checks (fail = stop, no model result). Measured 2026-10-23 evening, label-free, on odds_history.duckdb (mtime 2026-10-15 10:26,
19,105,364 rows), nba.duckdb (2026-10-23 20:03), oof_predictions.parquet (7,902 rows, 2022-24):
1. Spread coverage: Pinnacle T-5 rows 1,302 of 1,318 (98.8%) 2023, 1,292 of 1,315 (98.3%) 2024, one rung per game; >= 5 books at T-5 in 1,318 of 1,318 and 1,315 of 1,315 (min 16 / 14, median 20). PASS (needs >= 97% Pinnacle and 100% fallback).
2. Key identity: OOF injury-Elo keys vs odds_history keys, 2023 1,318 / 1,318 (0 / 0 unmatched), 2024 1,315 / 1,315 (0 / 0). PASS.
3. Burn-in: games with both teams >= 10 earlier games in the season 1,162 of 1,318 (2023), 1,165 of 1,320 (2024, plus carry). PASS (needs >= 80% so the first-15 slice is a slice, not the test).
4. Power, label-free (arm = Elo logit + lambda (Pinnacle T-60 logit - Elo logit); truth drawn as Bernoulli(Pinnacle); paired log loss; half-width 1.96 sd / sqrt(n); n 1,267 / 1,279 games with a Pinnacle T-60 moneyline): lambda 0.25: expected dLL -0.0059 / -0.0069, half-width 0.0023 / 0.0024; lambda 0.5: -0.0101 / -0.0118, 0.0045 / 0.0048; lambda 1.0: -0.0136 / -0.0157, 0.0091 / 0.0097. PASS at lambda <= 0.25 (half-width < floor 0.004), FAIL at lambda >= 0.5. Reading: an arm that moves the logit by more than about 0.1 on average has a half-width above the floor; the verdict is then INCONCLUSIVE, not null (stated before any run).
5. Planted future (to be proven by the harness before any score): replacing y_g and scores of every game dated on or after date d by random values changes no logit of any game dated <= d (byte-identical), 6 probe dates per season, positive control (later logits do move) true. Not yet run (no harness).
6. A0 reproduction: stored `rung0_injury_elo` OOF to 1e-6 on 3,951 keys (2022-24) and by season. Not yet run.
Gates 5 and 6 are harness tests; if either fails, stop, no model result. Gates 1-4 are measured above.

Data audit (non-negotiable 8) of what could still be repaired: 2022-23 lines are not in the store (odds_history holds seasons 2023-2025),
so 2023 has no earlier lines; the repair is The Odds API historical game lines from 2020-06 (**paid; about 50,000 credits for one season by the
2026-10-09 scout estimate, not re-verified; not authorised**), or a Kaggle game-line dump (licence and columns unverified). The rule does not
depend on either; the first-15-games slice is where it would matter.

## Pass rule (2023 selects, 2024 confirms; ALL must hold)
1. A2 - A0 log loss <= -0.004 with game-bootstrap CI upper < 0, in 2023 AND in 2024 (floor = the ELO_HISTORY_WINDOW floor; one season detects
   about 0.004-0.005 and gate 4 says the real arm's half-width must be <= 0.004, else INCONCLUSIVE).
2. Information check: A2 - C2 <= -0.002 with CI upper < 0 in both seasons (the market stream beats the same filter on results).
3. Placebo: P2 - A0 >= -0.001 (placebo gains nothing) in both seasons.
4. Reliability: ECE of A2 within +0.005 of A0; playoffs + play-in slice not worse by more than +0.01; first-15-games slice not worse by more than +0.01.
5. Date-block CI upper < 0 as well.
A pass makes A2 a **candidate comparison arm for the no-line fallback and the app's strength display** only. It does not touch production
injury-Elo (the live game always has its own line), no market claim is made, promotion is Devin's, and nothing earns a 2025 touch under this
rule (a 2025 confirmation would need its own logged holdout row first and Devin's words).

## Verdicts (named in advance)
* PASS: rule above.
* FAIL_MARKET_NOT_STRENGTH: A2 - A0 > -0.004 or CI includes 0 in 2024, with half-width <= 0.004: the gap to the market is game-day information; team-strength modelling is closed for the winner target.
* INCONCLUSIVE: realised half-width of the A2 - A0 paired delta > 0.004 in either season.
* ARCHITECTURE_NOT_INFORMATION: A2 - A0 passes but A2 - C2 does not (the gain is the filter, not the market): report, no market-stream claim.
* LEAK_SUSPECT: P2 - A0 < -0.001 or a planted-future difference: stop and attack.

## Kill criteria
A2 fails the 2023 floor with half-width <= 0.004: close. No constants tuning, no second filter, no extra books, no 2022-23 purchase to
"save" a null (a purchase may be proposed only before any run). One pre-registered follow-up at most: the same rule with 2022-23 lines
if Devin buys them AND the verdict is INCONCLUSIVE for burn-in reasons only (first-15 slice), then closed.

## What each choice protects against
* Own-game line withheld (never, all snapshots): the market's own price is a perfect predictor of its own outcome; any leak of it makes a fake win (T235/T236 scale).
* Past lines only after the game's date: same-day information ordering as production's Elo replay.
* Second stage shared with A0, one extra feature: the comparison differs only in the offset information (fair baseline: same rows, same calibration, same window).
* C2 (filter on results): the Kalman form could win by architecture; the market stream must beat it (T178, T182, T318-T319 say architecture ties).
* P2 (scrambled lines): a time-trend or era effect in lines (league-wide scoring drift) would still pass a plain placebo; scrambling teams inside 14-day blocks keeps the date structure and kills team information.
* Constants frozen (no tuning): 60-trial searches added nothing; tuned constants would spend the select season twice.
* Floor -0.004 and CI upper < 0: practical, not just a p-value; INCONCLUSIVE named so power cannot be passed off as a null (the F9/F12 failure).
* Both seasons required: three FAIL_ONE_SEASON results this month (T498-T500) show one-season passes do not replicate.
* Playoff guard: the T178 stack won overall and failed the playoffs slice.

## Outputs (exact; harness to be written after the freeze under research/eval/ by the modeler, not yet written)
`reports/prereg_team_strength_market/results.json`, `reports/prereg_team_strength_market.md`: `[season, arm, n_games, logloss, brier, dlogloss, ci_lo, ci_hi, ci_block_lo, ci_block_hi, p, p_bh, ece]`;
`[season, slice, arm, n, dlogloss, ci_lo, ci_hi]`; the fitted c1 per month; checks 1-6 `[test, ok]`; seeds, SHA, sha256.
Cost: seconds to minutes on CPU (daily Kalman over ~2,600 games, month-block ridge logistic), fixture-testable; no GPU, no heavy lock needed,
no process fan-out. Maintainer command: none yet (no harness); the harness entrypoint will be `python -W ignore -m research.eval.team_strength_market_eval {gate|run|report} --doc docs/prereg/TEAM_STRENGTH_MARKET_STATE.md --sha <64 hex>` with `--allow-draft` only for the gate stage.

## Playbook check (Ratings + Linear second stage)
1 Information or capacity: information (the market's past views); the C2 control isolates capacity. 2 Data for the class: ratings need one season of warm-up (T318); 2023 has none in lines, so burn-in is declared and sliced. 3 Test valid: own-game line withheld, planted future, A0 reproduction, identical keys, real-tip report gating, placebo with a scrambled-team structure; a power gate that fails at lambda >= 0.5 and says INCONCLUSIVE rather than null. 4 Lineage: T091/T128, T178, T182, T235/T236, T318-T319, T472, T501, T502, ROSTER_CONTINUITY. 5 Scored against: injury-Elo log loss (the reference is Pinnacle, which nothing here is expected to beat).

## Priors (also in docs/PREDICTIONS_LEDGER.md, written before any fit)
P(freezable as drafted after the adversary review) 0.60; P(PASS) 0.25; A2 - A0 expected -0.004 (80% -0.009 to 0.000), 2023 -0.003, 2024 -0.005;
P(C2 - A0 within +-0.002) 0.75; P(P2 - A0 >= -0.001) 0.92; P(A1 beats A0 at the point) 0.35; P(INCONCLUSIVE in at least one season) 0.30.

=== RESULTS BELOW ===
(none)
