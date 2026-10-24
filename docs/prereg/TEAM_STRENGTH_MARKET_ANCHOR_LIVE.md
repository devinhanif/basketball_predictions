# TEAM_STRENGTH_MARKET_ANCHOR_LIVE: the market as the base, our information as a learned residual, scored forward with a freshness covariate (pre-registration)

Status: **DRAFT (modeler, 2026-10-23 ~20:30 CDT, after Devin's 20:05 scope update). NOT FROZEN. RETROSPECTIVE GATE: STOPPED (power, label-free). No fit, no outcome read, season 2025 never loaded.**
Not a rule until the main session commits `prereg(TEAM_STRENGTH_MARKET_ANCHOR_LIVE): <sha256>` on `main`. Everything above `=== RESULTS BELOW ===` freezes.
Reading-log note: `docs/research/team_strength_and_movers_2026-10-23.md` (Part A, scope-update section). Companion draft: `docs/prereg/TEAM_STRENGTH_MARKET_STATE.md` (the
market-implied rating, the base for games with no own-game line). Props anchoring belongs to the other agent (MARKET_ANCHORED_PROPS*); this rule covers game winners, margins and totals.
Work label: Devin's override/direction ("incorporate the market rates into the model instead of trying to beat it; use anything that has a relationship").

## Devin's words and what they change
> "incorporate the market rates into the model instead of trying to beat it; use anything that has a relationship." (2026-10-23 ~20:05 CDT)
Claude agrees with the direction and has argued it before (CLAUDE.md: markets are a prior, not a target). Two things are not new and one is: the pooled form
logit p = a*logit p_mkt + b*logit p_model + c is ODDS_HISTORY H2/H3's blend (T204, T212, T235-T236) and T472's w = 0; the closed rule was MARKET_PRIOR_RESIDUAL (T502);
the new piece is the freshness covariate and the separating controls below.

## Why T502 was closed, and how this rule separates the two stories
T502 (draft f4c750e3, CLOSE_WITHOUT_RUN): in synthetic-label worlds on 37,528 real Pinnacle 2024 legs, a planted real signal (w = 0.25) came out PLACEBO_EXPLAINS,
mis-calibration alone with inert features fired the same check (0.511 [0.166, 0.869]), and PASS at the -0.003 floor was reachable only at w = 0.5. The reachable verdicts could
not tell "our features know something" from "the market is mis-calibrated". Here:
* K0 market as is (a = 1, b = 0, c = 0).
* **K1 calibration-only: a and c fitted, b = 0** (favourite-longshot, home bias, line-vs-price shape). This is the comparator for every information claim; K1 - K0 measures mis-calibration only.
* **K2 full pool: a, b, c fitted.** The information claim is **K2 - K1**, never K2 - K0.
* K3 placebo pool: K2 with the model logit replaced by its value for a random other game in the same 14-day block (teams scrambled) - it must give nothing beyond K1.
* K4 past-only model: the model logit computed with the report snapshot one slate earlier - it isolates "stale information", the T502 mechanism (the report is older than the line).
* Every contrast is also computed on the freshness strata below. The separating prediction: mis-calibration helps in all strata equally (K1 - K0 stable); information helps only where the model knows something the line did not (K2 - K1 concentrated in the fresh stratum, zero in the stale one).

## The key covariate: information freshness
F_g = (time of the last market move of game g's line before our prediction) - (as_of time of the newest injury-report snapshot we use), in minutes. F > 0: the line moved
after our information (our news is stale; we should receive weight about 0). F < 0: our report/lineup/news is newer than the line's last move (our news could be new).
Measured, retrospective, label-free (2023 / 2024, games with Pinnacle h2h at T-60 and T-5 and report rows, nba.duckdb `player_availability.as_of`, `odds_history.snapshot_ts`):
latest report snapshot older than the T-60 line by p10 / p50 / p90 = 296 / 386 / 536 min (2023, n 1,250) and 296 / 386 / 506 min (2024, n 1,256); games with ANY report snapshot between the
T-60 and T-5 line captures: **5 of 1,251 (2023) and 5 of 1,257 (2024)**. (The 386 min median is larger than T503's "120 / 180 / 300 min before tip" for the T-60 snapshot; the two use different
columns/definitions and I have not reconciled them; recorded as a discrepancy, not resolved.) So historically **our information is stale relative to the line in essentially every game**, which is
the same fact as T502's "the report is 2-5 h older than the line" and the reason b is expected to be about 0 retrospectively. The fresh stratum exists only live: `data/markets/market_asof.duckdb`
`live_book_odds` holds Pinnacle h2h/spreads/totals ladders at t180, t120, t90, t60, t45, t30, t20, t15, t10, t5 and close (a few games per kind so far: t60 17 games, t30 15, close 12 on 2026-10-20..23),
plus `market_at_prediction` (`ml_age_s` p10 / p50 / p90 = 51 / 895 / 12,556 s, n 542 win-probability rows) - the lines' last move and age are recorded.

## Hypothesis
H1 (information in games): in the fresh stratum (F < 0, report/lineup news timestamped after the line's last move), the pool K2 beats the calibration-only market K1 in log loss.
H2 (margins and totals): the same pool in Gaussian form, m = a*y_mkt + b*m_model + c (and the total likewise), K2 beats K1 in Gaussian CRPS in the fresh stratum.
H0: b = 0 everywhere (T472 found w = 0 in every block for totals; T235-T236 found the market better for winners).
Counter-hypothesis up front: T-30/T-15 news is priced by the sharp books within minutes; a stale-model pool cannot add, and a fresh model has too few games.

## Power: the retrospective test cannot be run (rule 8, label-free; the number that matters)
Oracle gain of K2 over K1 and the half-width of the paired difference, truth = market logit + w*(injury-Elo logit - market logit), stored OOF injury-Elo probabilities and Pinnacle T-60
de-vigged moneyline (n 1,267 / 1,279), outcomes drawn from the truth, none read:
w = 0.05: gain 0.00003 / 0.00004, half-width 0.00045 / 0.00048 (about 460,000 / 400,000 games for 2.8 standard errors); w = 0.10: 0.00014 / 0.00016, 0.00091 / 0.00097 (about 115,000 / 100,000 games);
w = 0.25: 0.00085 / 0.00098, 0.00227 / 0.00243 (about 18,400 / 16,000 games, i.e. 13 seasons). **A game-winner residual weight of 0.1 is invisible in any retrospective sample we hold
(two seasons of 1,300 games), and in a live season.** (The same arithmetic is why T502 could not reach its floor; it is also why H3 shows the market better, not why the market is
unbeatable.) Therefore: no retrospective test; the live rule below is **estimation with a stated MDE, not a significance hunt**, and the shipped weights are shrinkage-regularised.

## Fixed design (constants final)
**Production form (a design, not a claim):** logit p = a*logit p_mkt + b*logit p_inj + c, with a, c fitted by month walk-forward on the market alone (K1) and b fitted by ridge
with prior mean 0 and penalty lambda_b = 400 (so a b estimated from ~1,000 games with sd 0.1 is shrunk below 0.05): the weight of our information is learned and starts at zero.
p_mkt = Pinnacle (else median of >= 5 books) de-vigged T-5 close for the retrospective numbers and the latest capture strictly before our prediction stamp live; p_inj = production
`rung0_injury_elo` (the model with the report).
**Kalman form (the same idea as an update, to be compared, not added):** the prior is the market margin y_mkt with variance V_mkt (assumed constant 1.0 pt^2, not fitted), each post-line news item i (a player's status/lineup change with as_of strictly after the line's last move) enters as an observation of the margin shift s_i = beta * V_i (V_i = production
as-of player value in points, beta = production injury-Elo coefficient, frozen) with noise variance r = 1.0 pt^2; posterior margin = y_mkt + sum K_i (s_i - 0); weights come from V_mkt and r, not fitted on outcomes.
Compared with the pool in the same table; neither is promoted.
**Arms scored forward (all tip-gated at the prediction stamp; the own-game line used is only the latest capture before the stamp):** K0, K1, K2, K3, K4, Kal (Kalman), and the market-implied state rating
of `TEAM_STRENGTH_MARKET_STATE` as the base for games with no line at the stamp.
**Targets:** game winner (primary: log loss, K2 - K1, fresh stratum), margin (Gaussian CRPS with sd fitted on K1), total (same).
**Strata:** fresh (F < 0 by >= 10 min), stale (F >= 0), plus tip-hour groups (T-60, T-30, T-15 stamps as produced by the live pipeline). Games with no news item after the line are in the stale stratum by definition.

## Declared n and inference
The primary contrast K2 - K1 in the fresh stratum is scored when **>= 150 fresh games** have settled (declared; MDE stated now, below) or at season end, whichever is first; the secondary full-sample contrasts are reported at 400 games.
Inference: paired per-game differences, game-clustered bootstrap B = 10,000, seed 2026 (FORWARD_PREREG convention), date-block (14 days) CI as sensitivity.
**MDE stated before any row:** at 150 games with a per-game paired sd of about 0.05 (the sd for a true b = 0.25 in the label-free computation above), the half-width is about 0.008 log loss; the largest plausible fresh-stratum gain is a few thousandths. So the primary is
INCONCLUSIVE unless the effect is several times what the average game shows, which is possible only because news is concentrated in the fresh stratum (a news item moves the line 1-3 points; the model's disagreement in that stratum is far larger than the 0.37 average). Floor: log loss -0.003 and CI upper < 0.
Pass also requires: K3 - K1 within +-0.001; K4 - K1 within +-0.001 (stale information gives nothing); K1 - K0 reported (mis-calibration), not credited to the model; freshness monotone (K2 - K1 in the fresh stratum <= K2 - K1 in the stale stratum).

## Minimum data checks (fail = stop, no scoring)
L1. Known-at: every capture carries `received_at` < prediction stamp; every news item `as_of` is compared with the line's `last_update` (not `received_at`); planted future: shifting a news item forward by 60 min across the line removes it from the fresh stratum.
L2. Fresh-stratum count: the maintainer reports fresh games per week from `market_at_prediction`/`live_book_odds` by 2026-11-10; if the projection is < 150 by 2027-02-28 the rule stops as UNDERPOWERED before freeze (not measured: the live log holds 28 game rows).
L3. K0 reproduces the logged `p_home_win` of `market_at_prediction` to 1e-9 on 20 dates; K1 fitted on market data only (no outcomes of games in the fit window that are later scored).
L4. Retrospective sample for the ONLY thing it can answer: K1 - K0 (mis-calibration of Pinnacle T-60 / T-5), 2023 select, 2024 report, n 1,318 / 1,315, descriptive (a, c, with CIs); no claim about information.
The retrospective information gate is closed by the power table above (rule 8: 2019-21 and 2013-2018 hold no market lines in the store; the paid historical-lines repair is a different rule).

## Pass rule (all at the declared n)
1. K2 - K1, fresh stratum, log loss <= -0.003 and game-clustered CI upper < 0.
2. K3 - K1 and K4 - K1 within +-0.001 (placebo and stale information give nothing).
3. K2 - K1 in the stale stratum within +-0.001 (no information where there was none).
4. Calibration of K2 in ten bins: ECE within +0.005 of K1.
5. Margin/total (H2): reported with the same structure; a pass in winners is not read across.
PASS = a shrinkage weight b for the fresh stratum may be PROPOSED to Devin (production is his; no 2025 touch; no execution code). Verdicts: PASS; INCONCLUSIVE (the expected outcome); FAIL_B_ZERO (CI upper of the b estimate < 0.05 on >= 300 fresh games); LEAK_SUSPECT (K3 or K4 gain > 0.001).

## What each choice protects against
* K1 as the comparator, never K0: T502's failure (mis-calibration passing as information).
* K4 and the freshness strata: the report is older than the line in essentially every historical game (5 of 1,251 / 5 of 1,257); a pooled average would conclude b = 0 from stale data.
* b shrunk to 0 with lambda_b: noise in b is the real risk of anchoring (the pool should be the market until the data say otherwise).
* The Kalman form with frozen beta and r: a second, parameter-light implementation of the same idea; if the two disagree, the pool's fit is suspect.
* Stating the MDE: F9 and F12 could not pass their own gates; INCONCLUSIVE is declared as the expected verdict.
* First 15 live games excluded: found before the rule.

## Playbook check
Class: Linear/logistic pool (+ Gaussian form for margins/totals) on a market base. 1 Information or capacity: information (post-line news) - the freshness covariate is the point; the pool alone is capacity. 2 Data enough: no for the fresh stratum retrospectively (5 games/season), yes for K1; hence live. 3 Test valid: tip-gated, K1/K3/K4 controls, planted future, K0 reproduction, game clustering; the average-game test is invalid for information (stale). 4 Lineage: T204/T212 H2, T235-T236 H3, T472, T501, T502 (closed), T160-T161 (T-30 inactive list). 5 Scored against: the calibrated market (K1), not the raw price.

## Priors (also in docs/PREDICTIONS_LEDGER.md)
P(freezable) 0.5; P(>= 150 fresh games by 2027-02-28) 0.6; at the declared n: P(PASS) 0.10, P(INCONCLUSIVE) 0.78, P(FAIL_B_ZERO) 0.08, P(LEAK_SUSPECT) 0.04; expected fitted b in the fresh stratum 0.10 (80% -0.05 to 0.30); expected K1 - K0 -0.0010 (80% -0.0035 to +0.0005).

=== RESULTS BELOW ===
(none)
