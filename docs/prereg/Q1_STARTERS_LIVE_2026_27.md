# Q1_STARTERS_LIVE_2026_27: do confirmed starters at T-15 improve first-quarter player-points forecasts enough to beat the Q1 line? (pre-registration)

Status: **DRAFT (written Mon Oct 12 10:06 CDT 2026), modeler. Not frozen.** Approved as item B (DECISIONS e24b56c). Nothing live is fitted or scored by this document.
It freezes only by a hand that can commit, after Devin reads it, before 2026-10-20: `prereg(Q1_STARTERS_LIVE_2026_27): <sha256>` of everything above the line
`=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/Q1_STARTERS_LIVE_2026_27.md | shasum -a 256`).
Scope: **live 2026-27 regular season only; no history verdict.** Season 2025 is never loaded and never touched under this rule. Read-only everywhere.

## The rule in 5 lines
1. Unit: one (game, player, book, half-point line) with a two-sided `player_points_q1` quote at the **t15** capture from fanduel or williamhill_us, a stamped base forecast, a confirmed T-15 start status, and the realised Q1 points.
2. Arms: **base** (T-60 Q1 quantile grid, half_points method (b)), **recal** (base through one frozen global logistic recalibration), **t15** (base through the frozen recalibration of its confirmed start group, starter or bench). Market = proportional de-vig of the t15 price.
3. P1 (information) = log loss t15 minus recal; P2 (the line) = log loss t15 minus market. Pass each: mean <= -0.005 AND the 95% CI upper bound < 0, under both game- and date-clustered percentile bootstrap. **beats_the_line = P1 and P2** (intersection of two tests, no correction needed).
4. Final look at n >= 250 games AND >= 2,000 legs AND >= 35 dates (and P2 half-width <= 0.005); one kill-only interim at >= 125 games; weekly offline scorer; no look is a claim before the final.
5. The decision score at the t15 price (DECISION_SCORING) is reported, never pass-carrying: it cannot pass its own gate (below). A pass of the rule is a recommendation for red-team and Devin, never a bet.

## Why
Hypothesis: at T-15 the lineup feed says who starts; a starter's Q1 minutes are near-deterministic and a bench player's are near zero, so a Q1 points forecast that conditions on confirmed status should improve, and perhaps beat the line. The pace/period study (reports/pace_periods_2026-10-12.md B3, 2024 history, 7,305 legs, 902 games, two books) found our Q1 forecast ties the line on MAE and loses on log loss by +0.0144 [0.0092, 0.0197], leaning under.
**Prior, stated now (mine): probability the rule passes about 3%.** Reasons, measured in 2024 history before this was written (descriptive, not a verdict):
- Recalibration alone removes almost all of the gap: walk-forward recal minus market +0.0009 [-0.0014, +0.0032] (n 5,799 legs, 760 games). The "loss to the line" is mostly the under-lean, not a missing lineup signal.
- Starters add about nothing on the legs the books list: t15 minus recal -0.0004 [-0.0015, +0.0007]. 92% of quoted legs are box-score starters; books list regulars, so the lever (a bench player starting, a starter sitting) acts on about 8% of legs.
- The book prices at T-15 with the same lineup news, so a price win is not expected.
A clean null here is a finding we publish, and costs one stamper and one weekly scorer.

## Recalibration: yes, frozen, fitted on 2024 history before the live period (descriptive, allowed)
Our Q1 forecast leans under: mean P(over) 0.437 vs market 0.498, over hit 48.8%. Rule includes a recalibration, and it is the difference between arms, not hidden in them:
- Form: `logit p' = a + b * logit(clip(p, .02, .98))` on the base P(over) at the quoted line. Global (a, b) for **recal**; one (a_s, b_s) per start group for **t15**. Fitted by Newton logistic regression on **all** 2024 legs (n = 7,305, 902 games; 2024-25 is the only season with Q1 player quotes in history, 2023-24 has none). Parameters: `configs/q1_recal.json`, sha256 `6c08a5b22044c8368f762ce34afb7f28bd7210c9995102c389d6af9dc68e2a43`, command `.venv/bin/python -W ignore -m nba.eval.q1_starters_live fit`. Global a=0.042, b=0.355; starter a=0.084, b=0.435; bench a=-0.167, b=0.125 (the slope is far below 1: the base grid is wide relative to what it knows at the line).
- Walk-forward evidence, descriptive: expanding monthly blocks on 2024, min 1,500 fit legs (scored 5,799 legs / 760 games): mean P(over) base 0.434, recal 0.489, t15 0.492, market 0.498, hit 0.485; log loss base 0.7010, recal 0.6903, t15 0.6899, market 0.6893; recal minus base -0.0108 [-0.0154, -0.0060].
- The final parameter fit is in-sample on 2024 (no hold-out inside it); the live period is out of sample by construction. Parameters never refit during the live period. The base arm stays in every table, so recalibration is never credited to lineups: **P1 is the lineup effect, P2 is recal plus lineups against the line, and recal minus market is reported beside both.**
- Limits, stated: history start group is the **box-score starter flag**, a proxy for the announced starter (live uses the confirmed snapshot; announced-vs-box skew is measured by the FORWARD_T15 section 7 timing); the base grid is the walk-forward OOF method (b) with a 2022 warm-up, so the live stamper must run the same method; history prices are t60, live t15.
- Drift guard (kill criterion a): the live mean base P(over) over the first 125 games must lie in [0.39, 0.47] (history 0.434 +- 0.04). Outside it the stamper is not the same forecaster and the frozen slope does not transfer.

## Dependencies the rule does not build (flagged for the main session)
1. **Q1 stamper** (new, outside the nightly path or a pretip shadow): at T-60 write `base_q` (199 integer quantiles, method (b), as-of) per player and `base_made_at`; at T-15 write `t15_made_at`, `t15_snapshot_fetched_at` and `start_status` ('starter' | 'bench' | 'out') from the **frozen lineup rule** (`nba/lineups/store.py::dedup_snapshot_rows`; the latest snapshot with `fetched_at` strictly before tip minus 15 min, both fives Confirmed with 5 announced starters; starter = announced in winning-status rows; bench = Active, not announced; Inactive = out). It reads **no odds table** (a test pins that no arm touches a price). Schema in the `nba/eval/q1_starters_live.py` docstring. Without it the opening games are lost: forward-only, no back-fill.
2. **Q1 actuals**: period-1 player points from `data/pbp/<game>.parquet` (rule of `half_points.py`, `q1_points_from_pbp`). Not verified that live pbp is ingested nightly.
3. **`t15` capture of `player_points_q1`**: `configs/odds_live.yaml` marks `t15` as `extended: true`; if it is dropped the rule is void and says so. Not verified that fanduel or williamhill_us quote the market live (history: yes, 2024).

## Fixed design (what each choice protects against)
- **Forecast before price.** Guards, each counted by reason and never repaired: price `received_at` < tip; base and t15 `made_at` < tip; starter snapshot `fetched_at` < tip minus 15 min (strict); base `made_at` < price `requested_at`; start status present; OUT players dropped and counted (the book voids). Protects against a stamp written after the fact and a snapshot newer than the stated cutoff, the only leak path in a timing arm. The t15 stamp can legitimately post-date a t15 price captured at tip minus 17; arms use no price, so the share where it does is reported and a sensitivity on the strictly-ordered subset is shown, descriptive.
- **Prices and books.** Primary books fanduel and williamhill_us only (the 2024 books, so history and live match); other books descriptive, never pooled into the primary (duplicate quotes of one player). `implied_prob` proportional de-vig, as everywhere. Protects against a favourable book choice after the fact.
- **Support.** P(over) = P(Y >= ceil(line)) on the integer grid from the quantile share, for every arm; whole or odd lines are excluded and counted. Protects against the continuous-vs-integer artifact (T114-T118).
- **Same legs, same rows, same calibration form** for every arm; the market is the de-vigged price of the very leg. Protects against a baseline that is not fair.
- **CI: game-clustered (primary, as DECISION_SCORING) and date-clustered (sensitivity that must also clear)**, percentile, B = 2,000, seed 20262027, ratio of sums. Protects against teammate correlation (game) and same-night news (date).
- **Floors are practical**: -0.005 log loss on P1 and P2. Protects against a significant 0.0005.
- **Slices (no pass role)**: start status starter/bench; book; stamped-before-price subset; first vs second half of the look. Bench legs are about 8%: that slice is expected to be thin and is not interpreted below 100 legs.
- **Decision score**: DECISION_SCORING at the t15 actual price, thresholds 0, 2, 5, 10%, game-clustered ROI, "insufficient sample" below 300 bets. 2024 walk-forward (descriptive, at t60 prices): ROI -0.024 [-0.061, +0.014] n 2,675; -0.013 [-0.055, +0.031] n 1,952 at 2%; +0.015 [-0.044, +0.073] n 1,236 at 5%; +0.048 [-0.041, +0.142] n 516 at 10%. At about 700 bets (the 2% cell at 2,000 legs) the ROI half-width is about 0.07, so a pass needs a true ROI above about +7%: the cell **cannot pass its own gate and carries no pass**; it is published as the honest way to read what a lean would have earned. No stake, no recommendation.

## Gate (checked against real data, Non-negotiable 8)
- Real density (2024 history, two books at t60): 7.6 quoted legs per game; 760 of 760 walk-forward games scored; 902 of ~1,320 games (68%) have any Q1 player quote at these books.
- Measured spread: P2 game-clustered half-width 0.0024 at 760 games, so about 0.0042 at 250 games (the unrecalibrated gap in B3 has 0.0052 at 902 games, which is the "+-0.01 at ~250 games" planning figure; recalibration removes the variance of the under-lean). P1 half-width 0.0011 at 760 games, about 0.0019 at 250. Both can clear a -0.005 floor at 250 games; the minimum for a 80%-power look at a true -0.005 is about 350 games for P2 (stated, not required: the rule is a floor-and-CI pass, and the likely truth is null).
- Minimums: **250 games, 2,000 legs (about 263 games at 7.6 legs), 35 dates, P2 half-width <= 0.005**, all four at one look. Interim: 125 games.
- Where it lands (planning, recomputed from the real densities, not the optimistic 12 point capture): 1,230 games over about 175 days = 49 per week; share with confirmed fives at T-15 about 0.8 (FORWARD_T15 prior 0.70-0.90); share with the two-book Q1 quote about 0.68 (2024). Eligible about 27 games per week: **interim (125 games) about 4.7 weeks, week ending 2026-11-22; final (263 games / 2,000 legs) about 9.8 weeks, week ending 2026-12-27 (range: 5.4 weeks, week ending 2026-11-29, if every game is covered; 13.2 weeks, week ending 2027-01-24, if only 0.6 of games have confirmed fives).** Not 5-6 weeks unless coverage is near complete. Scorer runs Sundays; a look is the first Sunday on which the counts are met.
- If the live Q1 quote share at these two books is below 0.4 by the interim, the final cannot land before the all-star break and the arm is dropped (kill b).

## Kill criteria (the arm stops and the result is recorded; nothing else can stop it)
(a) a guard failure or a drift-guard breach not fixed by a documented, result-blind repair; (b) projected final later than 2027-02-15 at the interim, or the t15 capture or stamper unavailable for more than 10% of games; (c) at the interim (>= 125 games): P1 or P2 game-clustered CI lower bound > 0 (reliably worse than recal or the line); (d) deadline: `run_after_tipoff` counted as missing, never dropped, > 10% of decisions. No interim outcome is a claim; the interim cannot pass anything. A stat/arm that fails at the final is not retested: one pre-registered follow-up at most, then closed. A new question is a new id.

## Multiplicity and holdout
Family = {P1, P2}, a conjunction (both must pass), so each is tested at its own 95% and the joint size is at most 0.05; no BH needed. Select season = 2024 (the recalibration fit only), report season = 2026-27 live. Earns **no** 2025 touch, ever; season 2026 is the live log, read forward only. The decision cell and every slice are descriptive.

## Hardware and cost
Trees and logistic fits on the local CPU. The scorer is a few seconds; no `heavy.lock` is needed. Credits: none beyond the already-approved live capture. No paid API added.

## Run
`.venv/bin/python -W ignore -m nba.eval.q1_starters_live score --forecasts data/ops/q1_live/forecasts.parquet --outcomes data/ops/q1_live/q1_points.parquet --params configs/q1_recal.json --out reports/q1_starters_live/week_YYYYMMDD.json` (read-only on `market_asof.duckdb`; writes only the report JSON). Tests: `tests/eval/test_q1_starters_live.py` (13: guards counted per reason, planted-future outcomes cannot move a probability, no price in any arm, verdict states, read-only connections).

=== RESULTS BELOW ===
