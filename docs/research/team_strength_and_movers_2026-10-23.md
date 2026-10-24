# Team strength and players on a new team: reading-log note (2026-10-23, modeler)

Status: DRAFT note. No fit, no outcome read, season 2025 never loaded (every query `season <= 2024`, both DBs `read_only=True`).
Asked by Devin 2026-10-23 ~20:00 CDT: "find the best mathematical model to estimate team strength, and players on a new team."
Work label: **Devin's request** (not a Claude recommendation). Claude's standing advice, which this note repeats, is information
over architecture; the survey below is mostly a map of closed ground.
Outputs: this note; `docs/prereg/TEAM_STRENGTH_MARKET_STATE.md` (Part A base layer, DRAFT); `docs/prereg/TEAM_STRENGTH_MARKET_ANCHOR_LIVE.md` (Part A anchor + freshness, DRAFT, live-only); `docs/prereg/MOVER_DEPTH_LIVE.md` (Part B, DRAFT, live-only);
priors in `docs/PREDICTIONS_LEDGER.md`.

## 0. Prior-evidence sweep (done first; two re-proposals of closed ideas earlier this week)

Searched `docs/TEST_LEDGER.md`, `docs/DECISIONS.md`, `docs/MODEL_PLAYBOOK.md`, `docs/FAN_KNOWLEDGE.md`, `docs/prereg/`.

| Row | What it says | Bearing here |
|---|---|---|
| T091 / T128 | injury-Elo vs MOV-Elo, real-tip gate, OOF 2022-24, n 3,951: log loss -0.00812 [-0.01212, -0.00379] (game/row CI as recorded). Production rung0. | A0 for Part A; player-aggregated idea already lives here (V_out) |
| T176, RAPM_RETEST (DECISIONS 1154) | RAPM value in injury-Elo: +0.0032 [-0.0003, +0.0066] (worse); retest stops at power (80%-power effect 0.0086 select) | RAPM-style player-aggregated ratings: not testable at this n; do not re-propose |
| T178 | WINPROB_FAMILY: model-family and calibrator bake-off vs injury-Elo, n 3,951: best stack -0.0021 [-0.0037, -0.0005], fails playoffs slice; "Elo family wins" | logistic/GBM/BT-style arms lost |
| T179 | TIME_DECAY: season-decayed carryover as shrinkage target: untested mechanism | exponential time decay for team ratings is not tested here as a Massey arm |
| T180 | CONTEXT_SCREEN_GAMES: 14 game-context tests vs injury-Elo, sole survivor is_national_tv, dLL +0.00106/game in-sample (candidate only) | game-context features add almost nothing to Elo |
| T182, T318-T319 | GA-tuned MOV-Elo (k 9.49, home 40.4, carry 0.609); long replay from 2019 and re-tuned params: +0.0000 [-0.0002, +0.0004] / +0.0008 / -0.0001. "Tuning rarely matters", warm-up gone after ~30 games. | Elo hyperparameters and history are closed |
| T160-T161 | INJURY_ELO_T30 (T-30 inactive list): -0.0008 [-0.0044, +0.0026], FAIL | later info did not help the win model at this n |
| T235 / T236 | H3: injury-Elo vs de-vigged Pinnacle moneyline at T-60: +0.0212 [+0.0126, +0.0300] (2023, n 1,318) and +0.0107 [+0.0006, +0.0211] (2024, n 1,315): **market better** | the gap any strength model chases; reproduced label-free below |
| T320-T324 | MARKET_LINE_CLASSIFIER null | market as input to props |
| T472 | PACE_TOTALS: blend weight chose w = 0 in every block, B = the market line exactly | own-game market line dominates any model total |
| T501 | GAME_MARKET_CONTEXT null stands; "game context of any kind is below this model's resolution" | the own-game spread/total does not move props |
| T502 | MARKET_PRIOR_RESIDUAL **closed at the draft** (CLOSE_WITHOUT_RUN): rule cannot separate "our features know something" from "the market is mis-calibrated" | the market-as-prior idea for props is closed; this note's market-implied rating is a different target (game winners, no own-game line) |
| T083 | PBP_GPT pregame win LL +0.0875 [+0.0403, +0.1339] vs injury-Elo (n 150) | sequence/neural team strength lost |
| ROSTER_CONTINUITY (draft, DECISIONS 923/939) | team-specific Elo carryover from returning minutes: outcome-free ceiling -0.0010 per early game; FAIL on effect | roster-aware season start is real but small; the player-aggregated start-of-season prior was already sized |
| B2B_ELO (DECISIONS 923) | gate fail on power 0.26/0.23 | rest/travel is the market's edge area but not testable here |
| T513 | INGAME_WINPROB_V2 uses `rung0_injury_elo` as the as-of prior; Kalshi pre-tip prior "shadow only" | a better pre-tip prior would flow here |
| CLAUDE.md | 2025-26 replay: our Brier 0.0064 worse than Kalshi's price | we do not beat the market on winners |
| T500 | PRESEASON_ROLE_PRIOR, movers' first 10 games, integer CRPS, BH over 4 stats: 2023 pts -0.0098 [-0.0362, +0.0179], 2024 pts -0.0321 [-0.0624, -0.0032] (n 1,202 / 1,025 rows); FAIL_ONE_SEASON x3; by-player clustering the 2024 CIs cross 0; 5 of 115 players carry 98.6% of the pts gain; A1 minutes-only carries the whole gain. CLOSED | the information is real, power is the wall |
| T504 | ROLE_PRIOR_SALARY: stopped at the gate on power, all four stats: pts half-width 0.0167 / 0.0170 vs floor 0.005; pooled K=10 0.0132, K=20 0.0116; MDE(80%) about 0.024; Spearman salary rank vs minutes 0.715 [0.643, 0.777] n 251 (2023), 0.696 [0.622, 0.757] n 264 (2024) among non-movers | Part B power numbers; salary rank is a depth-chart input |
| T509 | TEAM_USAGE_CAP: stopped at the gate (pts 0.0067-0.0130 vs 0.005; no stored team forecast); label-free premise check negative | usage-competition: closed ground |
| T446-T449 | TEAM_BUDGET: raking to a team minutes budget: HARM in every cell (pts +0.1017 / +0.1179); roster minutes overcount 36 min per team-game | usage/minutes budgets at T-60 hurt |
| T512 | COLDSTART_SHAREBALL_T15: stopped at the gate: movers 1,202 / 1,025 rows, pts half-width 0.013-0.026, needs 7,700-32,000 rows; pooled 2019-21 + 2 seasons + 1 live season estimated 5,500-6,000 rows -> pts ~0.008, still failing | the retrospective-pooling estimate I update below |
| T515 | SEASON_BOUNDARY: returners' first 20 games 7,558 / 8,104 rows; minutes half-width 0.021 min | minutes-level tests are far better powered than pts-level ones |
| T499 | WOWY_STRUCT: FAIL_ONE_SEASON; T181 rookie-pedigree minutes prior +0.754 min/g MAE gain [+0.093, +1.399] (n 102 rookies, provisional); F9/F9b (T184-T201) young-pick prior: gate / floors failed | role priors from draft slot: small and underpowered |
| T264-T279 FOUR_FACTORS, T424-T439 ADVANCED_RATES | pace and the four factors as stat features: NULL | pace as a per-stat input is closed; **pace as an input to who plays is untested** (FAN_KNOWLEDGE 2026-10-23) |
| T476 (as cited in FAN_KNOWLEDGE) | early-season over-forecast on bottom-5 teams, descriptive | quality-conditioned depth |
| FAN_KNOWLEDGE F18 | trade-reason cold start: ~70-90 in-season team changes per season detectable from box scores | transactions are not in the repo (T512 audit) |
| Scorecard 2026-10-22 | "new team" 1.657 [1.430, 1.920] n=300 (15 games) vs "same team" 1.532 [1.425, 1.644] n=1012 | see section 6: not a detected difference |

Nothing below re-proposes a closed row. The two new things are (1) the market-implied *rating* as a game-winner target with the
own-game line withheld (Part A), and (2) a live-only mover rule that varies minutes inputs only (Part B).

## Part A. Team strength

### A1. What "best" can mean

A team-strength model has three possible jobs, and they have different best answers:

1. **Predict the winner/margin of a game that has a line.** The best estimate is the line (T235/T236; T472: blend weight 0). Nothing
   we fit from public data beats it; our Brier is 0.0064 worse than Kalshi's price.
2. **Predict where there is no line** (more than a day ahead, preseason, a rating shown in the app, a prior for derived quantities).
   Best = the cleanest estimate of the latent strength the market itself is using, from the information available to us.
3. **Measure how much of our gap to the market is strength-estimation error versus game-day information** (injuries, lineups, rest).
   This decides whether further pre-game strength modelling can ever matter. It is the most decision-useful single number.

Label-free reproduction of the gap (stored OOF probabilities from `data/injury_elo_realtip/oof_predictions.parquet` joined to Pinnacle
T-60 de-vigged moneyline in `data/odds/odds_history.duckdb`; no outcome column read): injury-Elo logit versus Pinnacle logit,
2023 n 1,267 games: mean |difference| 0.286, sd 0.366, correlation 0.934; 2024 n 1,279: 0.312, 0.399, 0.928. Pinnacle's own moneyline logit
is 0.1331 (2023, n 1,302) / 0.1323 (2024, n 1,292) times the spread-implied home margin, intercept ~0, residual sd 0.09/0.08:
**0.1325 logit per point** is the market's own points-to-logit rate, usable as a frozen constant.

### A2. Candidate models (formulas, appetite, what each can know)

Notation: teams i, games g on dates t, home h(g), away a(g), observed margin m_g, market-implied margin y_g (home minus away, from
the closing spread), strength theta_i(t) in points above average, home edge H, logistic rate s = 0.1325 logit/point.

| # | Model | Formula | Data appetite | Knows what the market cannot? | Fair test |
|---|---|---|---|---|---|
| 1 | **MOV-Elo (ours)** | update r_h <- r_h + k * ln(|m|+1) * c/(d_w * div + c) * (1[home won] - p); logit p = (r_h + H_elo - r_a) ln10/400; carryover 0.609 at season boundaries; k 9.49, H 40.4, c 3.69, div 6.7e-4 (configs/mov_elo_tuned.yaml) | low; one season of warm-up (T318) | no | log loss/Brier vs market, game-clustered (done: T235/T236) |
| 2 | **Massey / ridge least squares with time decay** | minimise sum_g w_g (m_g - (theta_h - theta_a + H))^2 + lambda sum theta_i^2, w_g = 2^{-(t - t_g)/tau}; solution (X'WX + lambda I) theta = X'W m; lambda = (sigma_m/sigma_theta)^2 about (12/4)^2 = 9 | low-moderate; ~30-40 games to be stable | no (same stream as Elo; Elo is the sequential, fixed-gain version of the same Gaussian model) | vs MOV-Elo on the same stream; tau and lambda are the only knobs ("tuning rarely matters") |
| 3 | **Bradley-Terry / regularised logistic** | P(home) = sigma(theta_h - theta_a + H), weights w_g, ridge | low | no, and it throws the margin away: for a Gaussian margin the sign carries ~58-64% of the Fisher information (phi(0)^2/0.25 = 0.64 at an even game) | already lost in T178 |
| 4 | **Bayesian state-space / Kalman (Glicko-style)** | theta_i(t+1) = theta_i(t) + eta, eta ~ N(0, q) (random walk), season boundary theta <- c*theta, P <- P0; y = theta_h - theta_a + H + eps, eps ~ N(0, R); gain K = P_i/(P_h + P_a + R); Elo is the steady-state fixed-gain case | low; the gain is adaptive (early season, post-trade) | **only if q or R depend on roster change, or the observation stream is richer than margins** | vs Elo with the SAME observation stream (architecture control) and then with a richer stream |
| 5 | **Player-aggregated** | theta_T(g) = sum_p mu_p(g) * v_p / 240-scale, mu_p = expected minutes (report + lineup), v_p shrunk per-minute value (RAPM/BPM-like, injury-adjusted) | high for RAPM (T176 gate), low for box value | **yes, in principle**: it can price a lineup before the market updates (T-30), and a roster at season start | vs injury-Elo (production IS the one-sided form: Elo + b*V_out); needs real-tip report gating |
| 6 | **Market-implied rating** | model 2 or 4 with y_g = closing spread-implied margin (de-vigged), not the realised margin; the own-game line is never an input | low (the signal per game is ~4x cleaner than a result: result noise sd ~12 pts vs market-vs-strength noise ~2-3) | **it knows what the market knew** (injuries, lineups, trades) about *past* games, and nothing else | vs injury-Elo on game winners with the own-game line withheld, plus an information-matched control (same filter on results) |
| 7 | Attack/defence (Dixon-Coles-type, Skellam) | points ~ Poisson/Normal(mu + att_i - def_j + pace), jointly with totals | moderate | no | not recommended: totals are priced (T472), no new information |
| 8 | Neural / sequence (set transformer, PBP GPT) | learned | very high | no | lost or tied (T083, ledger lineage) |

Reading of the table. Models 1-4 are one Gaussian state model under different gain rules; they use one information stream (results).
Everything we measured says the stream, not the gain rule, is the limit (T178, T182, T318-T319). The only candidates that change the
**information** are 5 (lineups/rosters: already in production in one-sided form, and the next increment needs T-30 data we do not
have as history) and 6 (the market's past views). Model 6 is the only one that can be tested in two seasons on stored data.

### A3. What would be *new* in model 6, honestly

The market's closing view of each past game compresses everything the market knew then: who was out, who returned, trades. A rating
built from those views is a **less noisy and less lagged** estimate of each team's effective strength than results alone. Order of
magnitude, label-free (stored OOF vs Pinnacle, section A1): if the Elo-to-market disagreement (logit sd 0.37-0.40, about 2.8-3 points)
is half team-strength error and half game-day information, and a market-fed filter removes 70% of the strength half, it closes about
a third of the variance and about 0.004-0.006 log loss. I do not know the split. That unknown *is* the experiment (use 3 above).

If the arm fails (closes < 0.004), that is informative: the gap is game-day information (rest, lineups, injuries within hours), which
only a later information stream can supply; team-strength modelling is then closed for the winner target and we say so.

### A4. What is it FOR (opinion)

Not for beating the market on winners (we cannot; the own-game line stays the best estimate whenever it exists). Worth a frozen test
because it answers use 3 and, if it passes, delivers: (a) a no-line fallback for games more than a day out (Kalshi lists game markets
a day ahead; the rating is the honest answer before a line exists); (b) the app's "team strength" display in points, which a stranger
can read; (c) a candidate sharper prior for INGAME_WINPROB_V2's as-of prior when no pre-tip price is captured; (d) spread/blowout
shape: the margin distribution given the implied margin is N(y, ~12) already, so a rating adds almost nothing there; not a goal;
(e) for the parlay joint model: nothing beyond what the lines carry (T501). I would not spend more than this one test on team strength.

### A5. Recommendation and gate (non-negotiable 8)

**PURSUE one frozen test: TEAM_STRENGTH_MARKET_STATE (model 6 on a Kalman/Massey state, with the Elo-fed filter as the
information-matched control and a time-shuffled-lines placebo).** Draft: `docs/prereg/TEAM_STRENGTH_MARKET_STATE.md`.
Gate measured now, label-free, on the data the run will use (section numbers in the draft):

| Check | Value | Pass |
|---|---|---|
| Pinnacle T-5 spread rows, 2023 / 2024 | 1,302 of 1,318 (98.8%) / 1,292 of 1,315 (98.3%); one rung per game | yes |
| Fallback: >= 5 books with a T-5 spread | 1,318 of 1,318 / 1,315 of 1,315 (min 16 / 14 books, median 20) | yes |
| Game keys: OOF injury-Elo vs odds_history | 1,318 = 1,318, 0 / 0 unmatched; 1,315 = 1,315, 0 / 0 (nba.duckdb has 1,320 in 2024: the five restored games are outside the stored OOF; keys follow the OOF) | yes |
| Burn-in (no 2022-23 lines in the store): games with both teams >= 10 prior games in season | 1,162 of 1,318 (2023); 1,165 of 1,320 (2024, plus the 2023 carry) | yes, with a slice |
| Power, label-free: arm = Elo logit + lambda * (Pinnacle logit - Elo logit), truth = Pinnacle, paired log loss, half-width 1.96*sd/sqrt(n) | lambda 0.25: expected dLL -0.0059 / -0.0069, half-width 0.0023 / 0.0024; lambda 0.5: -0.0101 / -0.0118, 0.0045 / 0.0048; lambda 1.0: -0.0136 / -0.0157, 0.0091 / 0.0097 (n 1,267 / 1,279) | passes at lambda <= 0.25, fails at >= 0.5; the real arm's half-width is measured at the run, verdict INCONCLUSIVE (not null) if it exceeds the floor |

Data audit (rule 8) for the gate's "what else could repair it": the only gap is 2022-23 lines (burn-in for 2023). Repairs, none done:
The Odds API historical game lines from 2020-06 (paid; the 2026-10-09 scout estimated ~210k credits for four seasons of slate pulls,
so ~50k for one season, about the 100K plan at $59 for a month: **paid, not authorised, Devin's call**); a Kaggle game-line dump
(licence and columns unverified); a free alternative is not needed for the gate. The draft does not depend on the repair.

Where the idea fails on lineage: it is close to H3's market comparator and to T502 (market as prior), but the target (game
winners), the comparison (injury-Elo with the own-game line withheld) and the information (past lines of other games) differ, and it
is scored as the gap decomposition, not as a bet. I could not verify whether published team-strength work uses past closing lines as
an observation stream; from memory (unverified) 538's RAPTOR/CARM-Elo and Glickman/Stern-style margin models use results and box stats,
not past market margins. I did not search the web for this; treat the novelty claim as unverified.

### A6. Playbook check per candidate (MODEL_PLAYBOOK cross-cutting questions: information or capacity / enough data for the class / test validity / lineage / scored against what)

| Candidate | Class | 1 Information or capacity | 2 Data enough for class | 3 Test valid | 4 Lineage | 5 Scored against |
|---|---|---|---|---|---|---|
| MOV-Elo | Ratings | baseline | yes (one season warm-up) | yes (T318 planted future) | production | market (T235/T236) |
| Massey + decay | Linear | capacity only (same stream) | yes | needs identical stream/rows to Elo | T178, T179 untested decay | MOV-Elo, then market |
| Bradley-Terry | Linear/logistic | capacity, loses margin info | yes | era flag caution: calendar features are era flags | T178 | MOV-Elo |
| Kalman/Glicko | Ratings | capacity unless the stream changes | yes | the architecture control is the same filter on results | T318-T319, T182 | Elo |
| Player-aggregated | Linear + RAPM | information (rosters/lineups) | RAPM: no (T176 invalid stint data then; RAPM_RETEST fails power); box value: yes | real-tip gating; V_out already tested | T091, T176, ROSTER_CONTINUITY | injury-Elo |
| **Market-implied** | Ratings (+ Linear second stage) | **information** (past market views) | yes: 1,300 games a season; two seasons only | own-game line withheld (planted future on the line), burn-in slice, information-matched control, shuffled-lines placebo | H3/T502/T501 are the nearest; target differs | injury-Elo (Elo in the same second stage) |
| Attack/defence | Count/Gaussian | capacity | yes | totals priced (T472) | T472 | totals line |
| Neural/sequence | Neural | capacity | no (n) | n/a | T083 | Elo |

Class checks for Ratings: does the arm add pre-tip information (yes, the filter's stream) and is the effect plausibly larger than about
0.002 log loss with one season detecting 0.004-0.005 (borderline: the draft's floor is -0.004, expected -0.004).

## Scope update (Devin, 2026-10-23 ~20:05 CDT): market-anchored team strength

> "incorporate the market rates into the model instead of trying to beat it; use anything that has a relationship."

Claude agrees; this is CLAUDE.md's "markets are a prior, not a target" applied to games. The recommendation is now a three-layer anchored design
(margins, totals and winners; props anchoring belongs to the other agent):

1. **Base for games with no own-game line**: the market-implied rating (model 6 above), draft `TEAM_STRENGTH_MARKET_STATE`. Retrospectively testable (own-game line withheld), gate passes.
2. **Anchor for games with a line**: logit p = a*logit p_mkt + b*logit p_model + c (Gaussian form for margin and total), a and c fitted on the market alone, b ridge-shrunk to 0.
   Design, not a claim. Equivalent Kalman form: the line is the prior margin, post-line injury news is the observation.
3. **Freshness covariate** F = (line's last move) - (as_of of the newest report/lineup we use). Draft `TEAM_STRENGTH_MARKET_ANCHOR_LIVE`.

What is already closed and why this is not a re-run: H2/H3 (T204, T212, T235-T236) are the blend; T472 found w = 0; T501 null; **T502 closed because its verdicts could not separate
information from mis-calibration**. Separation here: K1 (calibration-only, a and c fitted, b = 0) is the comparator, the information claim is K2 - K1, K3 (scrambled-teams model) and K4
(stale-report model) are the placebos, and freshness strata predict where the difference must live (fresh only).

Two label-free findings drive the verdict (counts only; no outcome read):
* **Freshness history is empty.** Newest stored report snapshot is older than the T-60 line by p10/p50/p90 296/386/536 min (2023, n 1,250) and 296/386/506 (2024, n 1,256); only 5 of 1,251 (2023) and 5 of 1,257 (2024)
  games have any report snapshot between the T-60 and T-5 line captures. (T503 quotes 120/180/300 min before tip for the stored snapshot; different definition, not reconciled.) Our information is stale versus the line in essentially every historical game.
* **A residual of realistic size is statistically invisible.** Oracle gain of the full pool over the calibrated market, truth = market + w*(injury-Elo - market) logit, n 1,267 / 1,279 (Pinnacle T-60 vs stored OOF):
  w 0.05 -> 0.00003 / 0.00004 (half-width 0.00045 / 0.00048); w 0.10 -> 0.00014 / 0.00016 (0.00091 / 0.00097); w 0.25 -> 0.00085 / 0.00098 (0.00227 / 0.00243); games needed for 2.8 standard errors about 460k / 115k / 18k.
  So no retrospective or single-season test of the winner residual can pass (same arithmetic as T502's "PASS unreachable"); the live rule is estimation with a stated MDE and INCONCLUSIVE as the expected verdict.

Live data that exist for the fresh stratum: `data/markets/market_asof.duckdb` `live_book_odds` Pinnacle h2h/spreads/totals ladders t180...t5/close (t60: 17 games, t30: 15, close: 12 so far), `market_at_prediction` (542 win-prob rows, `ml_age_s` p10/p50/p90 51/895/12,556 s). Reading as of 2026-10-23, 28 games; counts only.

Opinion: anchoring is the right default and costs nothing (a, c from the market alone gives a calibrated market, K1; its retrospective version is a descriptive check of Pinnacle's
mis-calibration, L4). The residual earns weight only on post-line news, only live, and not at a size we can certify this season. The most useful engineering product is the **freshness
log** (line last move vs report as_of, per game), which makes the 2026-27 fresh-stratum count measurable. Margins and totals: same pool; T472's w = 0 is the prior.

## Part B. Players on a new team

### B1. Decomposition: what travels, what does not

For mover i on team T in game g, the prop forecast is Y ~ f(minutes, per-minute rate, usage share). Each factor, its evidence here:

| Factor | Travels? | Evidence |
|---|---|---|
| Per-minute rates (pts/min, reb/min, ast/min), shrunk: rate = (S + k r_pos)/(n_min + k), k about 400 min (production) | mostly yes | live residuals: per-minute pts bias +0.03/min (FAN_KNOWLEDGE 2026-10-23); T500 stayers vs movers |
| Usage share (competition from new teammates) | partly; **closed ground** | TEAM_BUDGET harm (T446-T449), TEAM_USAGE_CAP gate stop (T509), SHAREBALL gate stop (T512), F11 null |
| Minutes (depth-chart slot on the new team) | **the whole mover effect** | T500: the minutes-only arm carries the 2024 pts gain (-0.0318); T504: salary rank vs minutes among non-movers 0.715 / 0.696; T181 draft/pedigree minutes prior |
| Team context on rotation depth (pace, quality) | real but second-order | Devin 2026-10-23 scratch (2022-24 close games, as-of prior-10 pace and prior win%): +1 SD pace -> +0.167 players >= 8 min [+0.141, +0.201]; +10 pts win% -> -0.072 [-0.092, -0.052]; n about 1,000 team-games by season; exploratory |
| Role from coach/system, trade reason | unknown | F18, no transaction data (T512 audit) |

Back-of-envelope on the pace/quality scratch, so nobody over-reads it: +0.17 players is roughly 1.5-2 minutes shifted per team per SD
of pace, spread across the bottom of the rotation, i.e. about 0.3-0.5 minutes for a given bench player. Movers' minutes errors are
several minutes. So pace and quality matter for *rotation size* (a p_play/ROTATION_DEPTH question over all rows, which has power),
but they are not what separates a mover from a stayer. They are an input to the mover prior, not a reason for one.

The best mathematical form I can defend for minutes on a new team is an **empirical-Bayes shrinkage on a depth-chart curve**:

    E[min_i] = w_i * mbar_i + (1 - w_i) * m(rho_i, z_pace, z_qual),   w_i = n_i / (n_i + kappa)

with m(.) the stayers' minutes-by-rank curve (rank rho_i among the new team's as-of roster by prior-season minutes, salary rank as a
tiebreak; z_pace and z_qual the team's as-of prior-10 pace and prior win%), n_i the games played with the new team, mbar_i the own recency
average, kappa a pseudo-games constant. m(.) is estimated on stayers (every team-season, high n) and applied to movers. This is the
production cold-start shape (`nba/coldstart/`, `nba/features/shrinkage.py`), not a new architecture; the new information is the rank
on the *new* roster, which production's recency features (carried from the old team) cannot see.

### B2. Honest power statement: can any retrospective test pass its gate?

**No, for the props metric.** T504 measured the binding half-widths: pts 0.0167 / 0.0170 against a 0.005 floor at 1,202 / 1,025 mover
rows; MDE at 80% power about 0.024; pooling K=10 -> 20 cuts 20-30% and the need is 3x. T512 estimated pooled history at ~5,500-6,000 rows
-> ~0.008, still failing. I audited what else is held (rule 8), label-free, counting rows only. Offseason movers (prior-season
minutes >= 500, first-game team differs from last prior-season team), regular season, rows in the new team's first 10 games, from
`data/history/nba_history.duckdb` (box scores 2013-2021) and `nba.duckdb` (2022-24):

| Season | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2023 | 2024 |
|---|---|---|---|---|---|---|---|---|---|---|
| movers | 93 | 98 | 106 | 95 | 87 | 121 | 92 | 106 | 93 | 75 |
| rows, first 10 games | 673 | 749 | 779 | 715 | 551 | 990 | 741 | 862 | 703 | 551 |
| rows with minutes > 0 | 666 | 745 | 778 | 711 | 548 | 891 | 629 | 744 | 592 | 480 |

(2022 is missing: its previous season sits in the history DB and its games in `nba.duckdb`, so it needs a union query I did not run.
Seasons 2013-2018 (2013 is the base year, not counted) hold played rows only: no DNP rows until 2019, so
"appeared" differs by era. My definition is narrower than T500/T504's 136 / 115 movers, because 2023 gives 93 here; scale my counts
by about 1.5 to compare.) Pooled 10 seasons: 7,314 rows by my definition, ~10,000-12,000 by theirs. At T504's scaling
(half-width ~ 1/sqrt(n)): 0.0167 * sqrt(1,202 / 11,000) = 0.0055 at best, i.e. at the floor, **with no stored production forecast
(A0) before 2022 and three eras of minutes distribution**. A0 would have to be rebuilt for nine seasons, which the exposure of 2023-24
(T500/T504 saw those movers) makes not blind anyway. The retrospective props test stays closed (T500, T504, T512 verdicts stand).

**Minutes level.** A minutes-only mover test is far better powered (T515: minutes half-width 0.021 min at ~7,500 rows), but a minutes
gain is not a props gain (T167/T170: oracle minutes -0.57 pts vs a rule's -0.0018), and 2023-24 mover minutes were already seen in T500.
I did not run a minutes noise arm (no harness; it would need synthetic outcomes drawn from A0's own predictive distribution).

**Recommendation (Part B):** do not freeze a retrospective mover rule. Draft the live-only forward rule `docs/prereg/MOVER_DEPTH_LIVE.md`
(arm computed pre-tip from as-of inputs, scored at a declared n, INCONCLUSIVE named as the expected outcome). Value ranking, my opinion:
the mover population is ~4% of rows; even a 0.024 pts CRPS gain on movers is ~0.001 overall. The practical use is abstention: if
the live record shows movers' first-10-game forecasts are no worse than stayers' (see section 6), the best "mover model" is none.

### B3. Playbook check, Part B

| Question | Answer |
|---|---|
| Class | Count/GBM production layer with a minutes input swapped (trees on CPU); the new piece is an empirical-Bayes minutes prior (Linear/shrinkage class) |
| 1 Information or capacity | Information: rank on the NEW roster, salary rank, draft slot (young players). Pace/quality: second-order. Rates and usage: no change (usage closed) |
| 2 Data enough for the class | Shrinkage curve: yes (stayers). Mover evaluation: no (T504); that is why it is live-only |
| 3 Test valid | As-of rosters (official roster cache at T-60), tip-gated, integer support, planted future on the roster, the old-team vs new-team label from roster history; scorecard definition differs (vs last PRESEASON game, stale-row contamination) and must not be reused |
| 4 Lineage | T500 (A1 minutes-only), T504, T509, T512, T446-T449; guards carried: player-level clustering, minutes-only isolation, leave-top-5-players-out, shuffle |
| 5 Scored against | A0 integer CRPS (not the line); the line is out of reach at this n |

## 6. The live contrast (scorecard 2026-10-22)

Devin quoted movers' CRPS 1.657 vs same-team 1.532. Source `reports/forward_scorecard/2026-10-22.md`: new team 1.657 [1.430, 1.920]
n=300 (15 games); same team 1.532 [1.425, 1.644] n=1,012 (16 games); bias +0.120 [-0.536, +0.896] vs -0.191 [-0.456, +0.075];
MAE 2.212 vs 2.111. The intervals overlap heavily (difference 0.125 against a standard error that is close to it); this is not a detected
mover effect at 15 games. The slice is "team vs last pre-season game", which includes stale-roster artefacts (14 stale movers, 10-20 log)
and a broader population than T500's offseason movers. Not evidence for or against Part B; it is the first rows of the live log.

## 7. Verdicts

| Idea | Verdict | Reason |
|---|---|---|
| Market-implied rating on a Kalman/Massey state (model 6), the BASE layer for no-line games | **PURSUE** (one frozen test, draft written) | new pre-tip information (past market views), gate passes label-free, answers the gap-decomposition question either way |
| Massey/decay, Bradley-Terry, Glicko/Kalman on results, attack/defence, neural | SKIP | same information stream; T178, T182, T318-T319, T083 |
| Market-anchored pool / Kalman with freshness (scope update), game winners, margins, totals | **PURSUE live-only** (draft `TEAM_STRENGTH_MARKET_ANCHOR_LIVE`), INCONCLUSIVE expected | retrospective power nil (table above); fresh-stratum history empty; T502 separation built in |
| Player-aggregated (RAPM-style) team rating | PARK | RAPM_RETEST fails power; revisit when T-30 lineups accumulate (live), not before |
| Mover decomposition, retrospective props test | SKIP | power (T500, T504, T512, T509) |
| Mover depth prior, live-only | PURSUE as a shadow with a declared n (draft), expected INCONCLUSIVE | information is real (T500 A1), power is the wall |
| ROTATION_DEPTH_PPLAY (pace/quality -> who plays, all rows) | not drafted here (Devin decides, FAN_KNOWLEDGE 2026-10-23); noted as the better-powered cousin | untested, has power; the 0.2-0.4 player effect is small relative to the live p_play miss |

## What I could not verify
- Whether a market-fed rating closes the Elo-to-market gap: that is the test. My ~0.004-0.006 estimate has an unmeasured split.
- Literature on past-market-margin ratings (not searched). The NBA margin sd (~12) and the Elo-to-point scale (~28 Elo per point) are
  from memory; the frozen constants use the data-derived 0.1325 logit per point instead.
- The 2022 mover count and the wide-definition mover counts (T500/T504 numbers are quoted from the ledger, not re-derived).
- Live 2026 mover counts: the season-2026 mover definition needs 2025-26 rosters (holdout season), so I did not compute it; the
  maintainer or the live scorecard supplies it.
- Whether the Odds API historical game lines for 2022-23 are still priced as in the 2026-10-09 scout (not re-checked); no purchase is proposed.

## Reproduction (scratch, read-only, no outcome columns)
Gate queries ran as scratch scripts (not committed): (a) OOF `rung0_injury_elo` p vs Pinnacle T-60 `h2h` home/away `implied_prob_raw`
de-vigged by normalisation, grouped by season, `season <= 2024`; (b) `odds_history` spreads Pinnacle/any book, `snapshot_kind='t5'`,
`side='home'`, counts of games and distinct books; (c) regression of logit(Pinnacle T-5 moneyline) on `-point` (home side); (d) mover
counts from `player_game_stats` joined to `games`, game ids starting `002`, `season <= 2024`. The draft rules reproduce them.
