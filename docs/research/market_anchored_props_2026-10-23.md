# Market-anchored player props: use the market's rates inside the forecast instead of trying to beat them (reading log, 2026-10-23)

Written Fri 2026-10-23 (shell clock `date` at the gate runs: 20:26-20:40 CDT), modeler, branch `agent/modeler-market-anchored-props`.
Origin and label: **Devin's override** (2026-10-23 ~20:05 CDT: "do as much advanced math related to incorporating the market rates into the model
instead of trying to beat it; you can use literally anything to predict this that would have a relationship"). Claude had recommended leaving
MARKET_PRIOR_RESIDUAL (T502) closed; this note reopens the question in a different form and says what is and is not new. Another agent owns team
strength, margins, totals and movers (`docs/research/team_strength_and_movers_2026-10-23.md`, `docs/prereg/TEAM_STRENGTH_*`, `MOVER_*`); nothing here edits them.
Scope: player props (pts, reb, ast, fg3m; Kalshi "N+" thresholds). Read scope: the pairs frames and `odds_history.duckdb` (both `season <= 2024`,
label columns never selected), ledger and docs. Season 2025 never loaded. No fit read a real outcome: every outcome in the gate is a synthetic draw on the real legs.
Rule: `docs/prereg/MARKET_ANCHORED_PROPS.md` (DRAFT). Gate: `reports/prereg_market_anchored_props/gate.json`, harness
`research/eval/market_anchored_props_{core,gate}.py`.

## 0. Bottom line

- **Verdict: PURSUE, narrowly, with a stated expectation that the result is below the betting floor.** One rule: a nested decomposition of the
  log-loss gap between "the market alone" and "market plus model" into (i) re-scaling the price, (ii) market-side information, (iii) model
  information given the recalibrated market, with a placebo on (iii). It exists to answer the one question T502 could not: *does the model know
  something, or is the market mis-calibrated?* The answer is read off two different components, so the two explanations cannot be confused.
- **What is already closed (do not re-run):** the model-minus-market contrast at the Pinnacle/DraftKings main line in every one-weight form
  (ODDS_HISTORY H2, MARKET_SHRINK, MARKET_LINE_CLASSIFIER), game context into the model (GAME_MARKET_CONTEXT, PACE_TOTALS), the raw model's
  calibration (LINE_CALIBRATION, ALT_RUNG_CAL), soft-vs-sharp book gaps as a bet rule (T516's prior audit). Section 2.
- **Honest expectation:** pooled 2024 `C_mod` (model given recalibrated market) about -0.0002 (80% interval -0.0010 to +0.0003): detectable at
  best, below the -0.001 "useful in a pool" floor, far below the -0.003 betting floor. The market-only components are likelier to show something
  (`C_mkt` about -0.0005) and are worth about as much as the over bias the ledger already measured (T477: de-vigged P(over) +1.5 pp). The product
  consequence is a better *displayed probability*, not an edge. "Keep your money" survives every verdict except BET_FLOOR_PASS (prior 0.01).
- **A structural finding that is not a model result:** the parlay engine's deferral gate asks whether the *calibrated model alone* beats the
  market (lower bound of the clustered Brier difference > 0, `configs/parlay.yaml` lines 35-38) before any model weight is allowed. A pooled
  forecast can beat the market while the model alone cannot (H2: raw model loses by +0.007 to +0.024 log loss, a one-weight blend gains -0.002 on
  reb). The gate tests the wrong object, so `model_weight` 0 is not informative about whether a pool would help. Section 6.
- **Gate (non-negotiable 8):** see section 8 and the rule's section 8. Legs reproduce MARKET_PRIOR_RESIDUAL exactly (35,146 / 37,528). History
  **cannot** supply the freshness covariate Devin asked for (the stored report stamps are 11:00 and 17:00 only; the odds `last_update` field is the
  API refresh clock, after the snapshot in 19% (2023) and 34% (2024) of Pinnacle rows). Freshness is a forward-only covariate and is deferred to a live follow-up.

## 1. The claim, restated (reading-log intake)

- **Target:** P(stat >= k) for a player-game at the thresholds Kalshi lists, and the log loss / integer CRPS of that forecast.
- **Data:** Pinnacle (and 12 other books) t60 main lines 2023-24 and 2024-25, DraftKings / FanDuel t60 alternate ladders, Kalshi forward ladders
  from 2026-10-21 (no Kalshi history before 2025-12-25, and 2025-26 is the holdout).
- **Model:** the market's de-vigged price as a prior; our grid as an add-on; weights learned per stat and per covariate.
- **Metric:** paired log loss at the line (CRPS against a market is undefined with one posted point, MARKET_PRIOR_RESIDUAL section 3), integer
  CRPS on ladders only where a market-implied distribution is fitted.
- **Number:** none yet. The prior is Devin's 2026-10-23 live exploration: 17 games, model overconfident (logit slope 0.46), model-under leans hit
  59.4% [53.2, 66.1] n=355 against Pinnacle's 53.1%, overs 42.3% [38.1, 47.2] n=194, bench minutes over-projected 4.7 [-5.4, -4.0], starters
  exact, p_play 0.72 -> played 0.41, assists Brier vs Pinnacle -0.023 [-0.043, 0.000], pts and reb worse. All exploratory, one week, **not used to
  set any constant**, but it is why the bench and teammate-out interactions are in the model (disclosed, rule section 14).

## 2. Prior evidence in the ledger (cited; every row Devin named plus the neighbours)

| id | what | number | what it leaves open |
|---|---|---|---|
| T477 | descriptive market split 2023-24 | model P(over) at the line 4.3 pp too low [-4.7, -3.8]; de-vigged Pinnacle P(over) +1.5 pp too high; the under>over asymmetry is mostly the market's (+3.7 pp vs ours +1.0) | a market-only re-scaling has something to find, of order 0.0005 log loss (+1.5 pp: delta^2 / (2 p (1-p)) = 0.00045) |
| T480 | T478 at lines | AVG-FULL -0.0018 [-0.0027, -0.0010] n 14,620; every arm worse than the de-vigged market (+0.0147); raw calibration slope 0.15-0.19 | line recalibration is a new rule (LINE_CALIBRATION) |
| T493 LINE_CALIBRATION | frozen a5441242, CALIBRATED | CAL-A0 -0.0117 [-0.0139, -0.0096]; CAL-C0 -0.0037 [-0.0047, -0.0027] (pts -0.0007, reb -0.0043, ast -0.0040, fg3m -0.0067); CAL 0.6882 vs coin 0.6931 vs market 0.6865; attack SURVIVES; a recency-only map gets half | **CAL still loses to the market by 0.0017**: the calibrated model alone cannot beat the price. Whether a *pool* does is open |
| SHRINKAGE_ONLY | LINE_CALIBRATION's named verdict (map beats raw but not generic shrinkage; serve C0) | not the T493 outcome (T493 beat C0 and the 50 shuffled maps) | the lesson it encodes: a gain over a raw model is cheap, a gain over shrinkage is not; our placebo arm PL and the B0 arm play that role |
| T494 ALT_RUNG_CAL | DK/FD alternates, 134,462 rungs | CAL-rung - C0 -0.0066; 92% of it is the book's main line; model-only terms add -0.0002; shuffling the main line kills it | anchoring on the main line is the information; the model adds ~0 on rungs. A principled version of "shift toward the book line" is Jeffrey anchoring (section 3.3) |
| T489/T491/T492 | DK ladders | hit 0.656 vs de-vigged 0.650 (+0.007 [-0.011, +0.024]); pooled ROI -0.057 [-0.062, -0.053] n 76,109 | the venue price is calibrated; vig is the whole loss |
| ODDS_HISTORY H1/H2 (T203-T214) | production vs de-vigged line; one-weight blend | H1 market better in 32/32 cells (+0.007 to +0.024); H2 w fit on 2023, reb vs Pinnacle -0.0022 [-0.0035, -0.0009], WOUNDED, others indistinguishable | the weights 0.10-0.35 are the largest information an oracle could have: gain -0.0011 per leg at w = 0.25 (T502 gate G9) |
| MARKET_SHRINK (T415-T419) | DK walk-forward blend + temperature | reb -0.0014 [-0.0024, -0.0005], fg3m -0.0012 [-0.0022, -0.0001], both fail the decision guard (ROI +2.0% [-4.6, +8.6] n 857 / +2.8% n 643); no cell passes | the same two seasons as H2; no independence |
| MARKET_LINE_CLASSIFIER (T320-T324) | book prob + line + spread + total + our forecast, GBM | worse than the book in 2023 (pts +0.0019 [+0.0001, +0.0039]), within noise 2024; the one-weight blend beats it | capacity added nothing: the playbook's "architecture ties or loses" |
| T389-T414, T501 GAME_MARKET_CONTEXT | market spread and total into the props model | no cell passes; 56/56 re-derived; **no positive control**, pts gains below 0.004 not excluded | the spread enters here only as a market-side covariate (`blowout`), a different question |
| T472 PACE_TOTALS | pace x efficiency total blended with the line | w = 0 every block: the line already has the pace | closed |
| T502 MARKET_PRIOR_RESIDUAL | GBM with a market offset | closed at the draft: reachable verdicts could not separate "features know something" from "market mis-calibrated"; PASS at -0.003 unreachable (R reaches it only at w = 0.5) | **this rule's design target**: nested arms so the two explanations land in different components, and a sized floor ladder |
| T495/T496 | parlay legs | CAL legs at q >= 0.65 overconfident (DK -0.047 [-0.082, -0.014] n 853); tail map unstable across seasons | tail behaviour is not fixed by a global map |
| T510 LINE_MOVE_TOWARD_MODEL, T511, T514 | frozen forward Kalshi tests | gate 324 qualifying markets on 10-21; no result | the forward confirmation lives there; this rule does not duplicate it |
| Parlay engine gate | `nba/parlay/ev.py:shrink_to_market`, `configs/parlay.yaml` | weight n/(n+k), k = 100, only if the lower bound of the game-clustered Brier difference (market - model) > 0; >= 30 settled rows over >= 14 dates; 1,122 of 1,122 Kalshi lines no_positive_ev_found on 10-23 | tests the model alone, not a pool (section 6) |
| T516 KALSHI_VS_PINNACLE_PAPER (draft) | Kalshi props within 2-3 cents of Pinnacle fair; prior audit DK vs Pinnacle gap >= 3 pts | +0.8% ROI [-1.1%, +2.6%], n 10,015; gap >= 5 +1.2% [-3.7%, +6.0%] n 1,343; forward 228 matched lines, 5 qualify at m = 0.03 | cross-venue gaps are not an edge; as a *covariate* (`u1`) the soft-book gap is in the market-side block |
| MARKET_UPDATE (draft, 10-11) | post-T-60 news as an update of the T-60 price | descriptive: lines that moved t60->t5 (10.2%) agreed with the outcome 57.8% [57.0, 58.6] | the information that matters arrives after T-60; this is why freshness is the lever (section 3.5) |

## 3. The mathematics, by design area (with a playbook check each)

Notation. For a leg (game, player, stat, line L = k - 1/2, event Y >= k): Pinnacle prices r_o, r_u (implied probabilities, overround
R = r_o + r_u - 1 about 0.070); market logit m; model grid S_A(k) = P_A(Y >= k) with logit a; disagreement delta = clip(a - m, -3, 3).

### 3.1 Market-implied distribution per player-stat

**De-vig.** Proportional (multiplicative) p = r_o / (r_o + r_u); power p = r_o^c with c solving r_o^c + r_u^c = 1; Shin
p_i = (sqrt(z^2 + 4(1-z) r_i^2 / S) - z) / (2(1-z)) with z solving p_o + p_u = 1 (z = share of insider money). Measured on the 72,674 Pinnacle t60
legs (label-free): mean overround 0.0705 / 0.0700; mean |mult - power| 0.0050 / 0.0049 (max 0.029), mean |mult - Shin| 0.0032 / 0.0032 (max 0.018),
Shin z about 0.070. Using multiplicative when Shin is the truth costs mean delta^2 / (2 p (1-p)) = 3.7e-5 / 3.5e-5 log loss. **The de-vig method is
2 orders below the floor; multiplicative is kept (it is what ODDS_HISTORY, LINE_CALIBRATION and the stored frames use); power and Shin are
sensitivity only.** (Literature: Strumbelj 2014, as cited by later work, finds Shin beats basic normalisation on three-way soccer RPS; later soccer
evidence is mixed; a 2026 preprint, arXiv 2604.17194, reports odds-only conversions beat Multiplicative/Shin/Power on soccer. I read abstracts and
secondary summaries only. A two-way prop at a 7% overround is a different regime, hence the direct measurement above.)

**Multi-book consensus weighted by sharpness.** With books b = 1..B at the same line, logarithmic pool logit p = sum_b w_b logit m_b, w_b >= 0,
fitted by log loss on earlier months (a stacking of books). Two-sided books per Pinnacle-line leg (2024): median 7, >= 3 books on 84%; 2023 median 9,
90%. Pinnacle is the sharp anchor and the other books enter as one covariate: `u1` = mean over soft books (same line, two-sided, >= 3 books) of logit m_b
minus m, and `disp` = their standard deviation. A positive coefficient on u1 means the soft books carry information Pinnacle lacks (or lag it the other
way if negative). This is "weights by sharpness" reduced to the two numbers the data can identify; a 12-weight pool is not identified on 6k legs per stat.

**Count distribution matching the main line and the ladder.** (a) Negative binomial NB(mu, phi), Var = mu + mu^2 / phi: choose (mu, phi) to minimise
sum_k w_k [logit S_book(k) - logit S_NB(k; mu, phi)]^2 over the posted rungs; with one posted point the fit is under-identified (one equation, two
unknowns), so phi must come from the stat (a pooled dispersion by mean bin, the COMBO_STOCKS_SHADOW recipe) and the line fixes mu. Right for reb, ast,
fg3m; wrong for pts (playbook: Poisson on points biased +0.17). (b) **Jeffrey (minimum-KL) anchoring of our grid, no family needed.** Given our pmf p_A on
the integers and one market constraint P(Y >= k0) = m0, the distribution closest to p_A in KL(q || p_A) is the two-sided rescaling

    q(y) = p_A(y) * m0 / S_A(k0)               for y >= k0
    q(y) = p_A(y) * (1 - m0) / (1 - S_A(k0))   for y <  k0,

so on a ladder rung k > k0: S_J(k) = m0 * S_A(k) / S_A(k0) (the model's *conditional tail* beyond the line, scaled to the market), and for k < k0:
S_J(k) = 1 - (1 - m0)(1 - S_A(k)) / (1 - S_A(k0)). It is exact on the integer support, keeps P(Y = 0) and the model's shape on each side of the line, and
equals the market at the line by construction. With several posted rungs the same projection is iterative proportional fitting (raking) and needs the
posted survival points monotone (pool-adjacent-violators, `nba/markets/implied.py`). This is the principled form of T494's finding that 92% of the
rung gain is "shift toward the book's main line", and it is the object a trader needs: sharp main line from Pinnacle, shape from us, price for a rung
Pinnacle does not post. It is **Stage 2 of the rule (descriptive, no pass role)** because its information is exactly T494's and a rung price from
DraftKings is over-only (2024: under side 195-331 rows of ~100k per stat) so only FanDuel rungs are two-sided (2024 share of FD rung points with both sides: pts 59%, reb 44%, ast 45%, fg3m 52%; 2023 pts 52%, reb 39%, ast 37%, fg3m 46%).

*Playbook check (quantile and distribution layers; count models).* Information or capacity: capacity (a re-expression of the grid), the new input is
the market line. Data enough: yes for NB dispersion by mean bin (several thousand rows per bin, playbook), Jeffrey needs no fit. Test valid: integer
support on every arm; reliability at the line and at off-line rungs separately (the calibration failure is at a specific point). Lineage: T494
(rung anchoring), COMBO_STOCKS_SHADOW (NB head), CALIBRATED_GRID (a shrinkage control beats raw by construction). Scored against: rung log loss vs the
venue's own de-vigged price, not vs raw A0.

### 3.2 Pooling market and model

1. **Linear pool** p = w p_A + (1-w) m. Ranjan and Gneiting (2010, JRSS-B 72:71-91) show any non-trivial linear pool of distinct calibrated forecasts
   is **uncalibrated** and lacks sharpness; they propose the beta-transformed linear pool. The engine's `shrink_to_market` is a linear pool with
   w = n/(n+k) and a skill gate: wrong functional form for two forecasts that are one and the same information plus a small difference, and n here counts
   *shadow rows* (hundreds per slate, strongly correlated), not information.
2. **Log-linear (logarithmic opinion) pool with free coefficients** (the form adopted):

       logit p = alpha_s + beta_s m + gamma_s (a - m)  =  alpha_s + (beta_s - gamma_s) m + gamma_s a

   Nested: gamma = 0 is the (re-scaled) market, beta = 1, alpha = 0, gamma = 0 the market itself, gamma = 1 the model. Because the model is
   overconfident (slope 0.15-0.19 raw; 0.46 live), a free gamma absorbs the scale; no separate temperature is needed (MARKET_SHRINK needed one only
   because it fixed w in [0, 1] on the raw logit). If the sources were conditionally independent noisy views, the optimum would extremise (Satopaa et
   al. 2014, IJF 30:344; the logit-model result I could confirm only from the abstract); here the model's information is nested in the market's (the
   model sees a subset of public pre-tip facts, the market sees later ones), so the expected sum of weights is below one, not above.
3. **Bates-Granger / precision weight.** For forecast errors e_M, e_A with a common component, the optimal combination weight on A is
   w* = (s_M^2 - s_MA) / (s_M^2 + s_A^2 - 2 s_MA) = Cov(e_M, d) / Var(d) with d = mu_A - mu_M: the slope of the market's residual on the *disagreement*.
   It is the gamma above in mean space. Normal-normal Bayes (market = prior with precision tau_M, model = likelihood with precision tau_A) gives the same
   weight tau_A / (tau_A + tau_M); the weight is a **ratio of error precisions on the disagreement**, not a function of how many rows we have settled.
4. **Information budget (label-free).** An oracle with coefficient gamma on delta earns Delta LL = 1/2 gamma^2 E[p (1-p) delta^2]. Measured: sd(delta)
   = 0.374 logit units pooled (share clipped at 3: 1e-4); the planted gammas that give an oracle gain of 0.0011 (H2's largest weight) and 0.0030 are
   0.222 and 0.369. **Even a model with H2's largest weight earns about a tenth of the 7% overround.** This is why the rule has a three-tier floor
   ladder instead of one number.
5. **Stacking on CRPS.** For a linear pool of CDFs the integer CRPS is convex in the weights (so stacking is a convex program); it needs a *market
   distribution* on the integer grid, which exists only where a ladder is fitted (3.1). Not used for the primary because one posted point cannot
   define a market CRPS (MARKET_PRIOR_RESIDUAL section 3); reserved for a ladder follow-up.
6. **Partial pooling of weights across stats and covariates.** Per-stat gammas (4) with shared interactions (3) and a ridge toward zero (prior sd
   about 0.14 on the delta terms) is the empirical-Bayes shrinkage; the measured overfit cost of this size is +0.0001 per leg (gate G7a), where
   the unpooled 36-parameter version cost +0.0005 and read as "worse than the market".

*Playbook check (linear and logistic models).* Information: capacity only in the main-line version; the new inputs are market-side (soft-book gap,
dispersion, spread) and a covariate that says when the model's difference should count (bench, heavy depletion, book disagreement). Data: low,
a 22-parameter ridge logistic on 28,108 scored 2023 legs and 37,528 2024 legs. Test: walk-forward by month, real tip, time weights, planted future,
placebo from K derangements, three clusterings; positive control in both directions (gate G7). Lineage: H2 (one weight), MARKET_SHRINK (+ temperature), T502
(offset GBM, closed because it could not separate), LINE_CALIBRATION (market-free map). Carried: floor scale, pins, bootstrap, derangement placebo.
Dropped: trees (capacity), decision guard (this is an information test, BEST_PRACTICES 2026-10-11 applies if a pass becomes a bet rule). Scored
against: log loss at the Pinnacle line, the same object H2 scored, plus the component split.

### 3.3 Separating "the model knows something" from "the market is mis-calibrated" (the T502 requirement)

Four nested arms on identical legs, all logistic, all walk-forward, none reading a label that was not in their training window:

| arm | logit p | params | role |
|---|---|---|---|
| M | m | 0 | market alone |
| B0 | stat intercept + stat slope on m | 8 | pure re-scaling of the price (T477 over bias, favourite-longshot) |
| MR | B0 + {hi-line, soft-book gap u1, its missing flag, blowout} + main effects of bench, heavy depletion, book dispersion | 15 | everything the market side alone can explain, including every covariate the model terms interact with |
| MD | m + model terms (offset m, no re-scaling) | 7 | model credited first |
| MM | MR + model terms (stat-specific delta, delta x bench, x heavy depletion, x book dispersion) | 22 | the pool |
| PL | MM refit with delta taken from another leg of the same (date, stat), K = 50 | 22 | placebo |

Components (paired per-leg log loss): **C_cal = B0 - M** (mis-calibration of the price proper), **C_mktx = MR - B0** (market-side information),
**C_mod = MM - MR** (the claim: the model given the best market-only forecast), C_mod_first = MD - M (the order-reversed credit), **C_mkt_after = MM - MD**
(the market side after the model has had first pick), C_pl = MM - PL, C_total = MM - M. Mis-calibration along the covariates MR contains (stat, line level, soft-book gap, spread, bench, heavy depletion, book dispersion) cannot be mistaken for model knowledge because MR receives them all and is scored on the same rows; **a market error outside that span can still reach C_mod** (the pre-freeze review showed it for bench, dispersion and depletion before they were added to MR), so the separation is only as good as the gate's off-span worlds (G7g); model knowledge cannot be mistaken for
mis-calibration because it is the *increment* over MR and must beat a placebo that keeps every other column. **The two explanations share variance**
(delta = a - m is correlated with m): a first gate draft read the market word from MR - M and a planted model-only world leaked into it in 7 of 10
replicates. The adopted market word is MR - M **kept only if MM - MD < 0** (otherwise `MODEL_EXPLAINS`); the model word is MM - MR. Each side is credited
only with what survives the other side going first, and the shared part is reported, not assigned.
Gate evidence that it separates (synthetic outcomes on the real legs, section 8): a market-mis-calibrated, model-inert world lands in C_cal/C_mktx
and not in C_mod; a model-informed world lands in C_mod; the null world fires nothing.

*Playbook check (3.3).* Information or capacity: neither added; the decomposition re-uses the pool of 3.2. Data enough: 28,108 / 37,528 scored legs for 22 ridge parameters (null cost +0.0002).
Test valid: placebo from K derangements, both-direction positive controls, planted future exact. Lineage: T502 (this is its repair), H2. Scored against: log loss at the line.

### 3.4 Minutes: the dominant error, and what the market already holds

Most prop error is minutes (CLAUDE.md), and most of that is unknowable 60 minutes before tip. The live week: bench minutes over-projected by 4.7
[-5.4, -4.0], starters exact, per-minute rates right, p_play 0.72 -> played 0.41. A market-anchored pool should therefore lean on the market for exactly
the legs where minutes are uncertain and on the model where they are not: that is the `delta x bench` and `delta x heavy depletion` terms (heavy
depletion = n_out_rot >= 3, 31% of legs; >= 1 is 83% and discriminates nothing). **Settlement note (a check for the main session, not an edit):** the
Kalshi rules text in `data/raw/live_2026-10-22/kalshi_rules/KXNBAPTS` says "If a player is inactive, or active but never takes the court, the market
settles to a fair price." So a YES price is a price for P(Y >= k | takes the court), as for a sportsbook that voids a DNP. T510's text defines
p_M = P(stat >= k) x p_play and says p_M "treats a DNP as No"; that matches neither the rule text nor the voided-DNP legs used in history. If
p_play multiplies P(Y >= k) anywhere that is compared with a Kalshi ask, the comparison is biased toward No by a factor of p_play. I did not verify the
engine's `dnp_policy` code path end to end; flagged for the adversary.

*Playbook check (3.4).* Information: yes in principle (bench / p_play structure), but the model's own minutes forecast is unchanged; here it only sets where the pool trusts the model.
Class: logistic interaction terms. Risk: `n_out_rot` is as-of the same stamp the market has already seen (one to four hours old), so heavy depletion is a weak proxy for "the market knows who is out".

### 3.5 Information freshness as the covariate on the model's weight

The right state variable for the model's weight is the time between the model's information and the market's: a model built on a report stamped
T-180 competes with a line that has seen the T-60 report and the T-30 lineups. Candidate covariates: report age at the quote; a new OUT on the
player's team stamped after the line; confirmed starters at T-30; the Kalshi quote's own staleness (time since last trade / ladder change).
**History cannot supply them** (gate, measured, section 8 and the rule's section 9):

- Stored report stamps for 2023-24 are exactly two clock times, 11:00 and 17:00 ET (backfill artifact, `DEFAULT_ANCHORS`); the used snapshot leads tip by
  120 / 180 / 300 minutes (p10 / p50 / p90) on the filename clock, and REV 3 of the timing note says every lead is 30 minutes too long (hour-only PDFs are headed
  HH:30). "Report age at the line" in history is therefore a function of the tip hour: a clock proxy, not freshness.
- `odds_history.last_update` is the API's refresh time: it is later than `snapshot_ts` in 19% (2023) and 34% (2024) of Pinnacle t60 rows and 0-5 minutes
  before it otherwise. It is not the time the line last moved. No history of line movement before t60 is stored (one t60 and one t5 snapshot per game).
- A rotation Q player's status changes between T-60 and T-5 in 0.2% of cases (T503 staleness): the report itself rarely changes late. What the market
  knows that we do not is mostly *earlier-than-us news and lineups*, which the stamps do not date.

So freshness enters the rule only as (a) a descriptive slice by tip-hour bucket in history and (b) a **named forward follow-up** on the Kalshi capture, where the
quote times (1-minute ticks) and the `player_availability` stamps are real. The weight function there is gamma(f) = gamma_0 + gamma_1 f with f = minutes
between the newest report/lineup the model used and the quote, tested date-clustered. Playbook: this is the information-not-capacity lever, which is why it is
the pre-declared follow-up, but it cannot be tested on the data we hold.

### 3.6 Everything else with a plausible relationship (as-of? in history? disposition)

| idea | as-of at T-60? | in history? | disposition |
|---|---|---|---|
| soft-book gap and dispersion | yes (same snapshot) | yes (13 books 2023, 12 in 2024) | **in the market block (u1) and the weight block (delta x disp)** |
| line movement t60 -> t5 | no (future); as a target yes | yes (t5) | MODEL_CLV / MARKET_UPDATE / T510 territory; not a feature |
| line movement before t60 | would be | no (one snapshot) | forward-only |
| Kalshi - sportsbook gap | yes live | exchange books novig / prophetx exist for 2024-25 from 2024-12-19 (904 / 917 games); no Kalshi history outside the holdout | descriptive slice only; forward follow-up |
| public-favourite bias on star overs | yes | yes | `hi` line indicator in the market block (T477: market +1.5 pp over); stat cuts 24.5 / 9.5 / 6.5 / 2.5 |
| pace, team quality -> rotation depth (Devin's notes: stacked and injury-prone rosters play deeper; blowouts; Nets run 12 deep) | yes | yes | spread enters as `blowout` = |spread| / 10 in the market block; team-level pace/depth features are GAME_MARKET_CONTEXT / T509 / ROTATION-type questions already at or below the model's resolution (T501: pts effects under 0.004 not excluded) and are not added here |
| book-specific vig / overround as a signal | yes | yes | descriptive only; the multiplicative de-vig already removes it to 4e-5 |
| exchange order-book depth | live only | no | forward follow-up |
| p_play from the market (alternate 1+ / 5+ rungs) | partly | partial | not used: Kalshi settles inactive players at a fair price, so P(plays) is not in the YES price |

*Playbook check (3.6).* Every covariate listed is a market-side input or a deferral; none is a new architecture. Information-or-capacity answer per row is in the disposition column.

## 4. Outside ideas (reading log; credibility; verdicts)

| source | claim | credibility | prior evidence here | fit | verdict |
|---|---|---|---|---|---|
| Walsh and Joshi 2024, Machine Learning with Applications 16, "Machine learning for sports betting: should model selection be based on accuracy or calibration?" | NBA game winners: choosing models by calibration gave average ROI +34.69% vs -35.17% by accuracy | peer reviewed; NBA seasons trained, **one season** bet; game winners not props; abstract only read; ROI at published odds without a clustered CI | CLAUDE.md: market beats us on winners; T495 shows a calibration fix does not create an edge | supports scoring by proper rules and calibrating, which the project does | PARK (as a reminder); no new rule |
| Ranjan and Gneiting 2010, JRSS-B 72:71-91 | linear pools of calibrated forecasts are uncalibrated; beta-transformed pool fixes it | theory plus a weather case study; abstract and summary read | the engine uses a linear pool | the log-linear pool with free coefficients is the nesting alternative | PURSUE (the functional form) |
| Gneiting and Ranjan 2013, EJS 7:1747 | generalised, spread-adjusted, beta pools for predictive distributions | same | none | relevant to ladder follow-up | PARK |
| Satopaa et al. 2014, IJF 30:344 | logit-scale aggregation with an extremising parameter beats averages | forecasting tournament (1,300+ experts, 69 events); abstract only | none | the sign of beta - 1 is a diagnostic we report | PURSUE (as a diagnostic) |
| Strumbelj 2014 and successors | Shin beats normalisation in soccer RPS; later mixed | secondary summaries only | measured here: 4e-5 | irrelevant at this scale | SKIP |
| Goto, Takeishi, Yairi 2026 arXiv 2604.17194 | odds-only conversion plus one favourite-longshot parameter beats Multiplicative/Shin/Power and richer GLMs | preprint; soccer (90,014 matches, 5 books); split and metric not stated in the abstract; basketball used only in a competition test | consistent with "features beyond odds rarely help"; our B0 arm is exactly a one-slope FLB re-scaling | supports having B0 as the first nested step | PARK (could not verify split, metric or seasons) |

Free sources only; nothing paid is proposed. Not verified: full texts of all of the above (abstracts and secondary summaries only), the Kalshi history
before 2025-12-25 (the 2026-10-09 scout says it starts then; not re-checked), and any vendor claim.

## 5. The recommended rule (one primary)

`docs/prereg/MARKET_ANCHORED_PROPS.md`: nested decomposition on the Pinnacle t60 main-line legs, select 2023 (28,108 scored legs after the 3,000-leg
burn-in), report 2024 (37,528), game / player-season / ISO-week envelope CI, K = 50 derangement placebo, BH over 10 secondaries, three-tier floor ladder
(detect: CI below 0 beating the placebo; INFO: -0.001; BET: -0.003), verdict as two words (model part, market part) plus WORSE_THAN_MARKET.

**What it protects against.** (i) A model gain that is really the market's mis-calibration (C_mod is an increment over MR, and B0/MR get every
market-side input). (ii) A mis-calibration gain read as model skill (the market word is read from C_mkt, and the model word needs the placebo).
(iii) A gain that any refit with the same columns would show (PL keeps every column and moves only the leg-specific model content). (iv) A pass that
cannot pay a 7% overround being sold as an edge (the BET tier). (v) The T502 trap of a floor no result can reach (the gate shows which tiers a planted
effect reaches). (vi) A clock proxy sold as freshness (the covariate is not in the model; slices are labelled tip-hour).

**Honest expected gain.** Below the floor (section 0). If the realised model word is NONE, the engine's `model_weight` 0 stays and the answer to "what
should I bet?" stays "keep your money". If MISCALIBRATION_* fires, the actionable item is a market-only re-scaling of the displayed Kalshi/Pinnacle
price, worth about 0.0005 log loss: a correctness item, not money.

## 6. Parlay-engine deferral gate (finding, for the main session)

`configs/parlay.yaml` lines 35-38: the model weight opens only if the lower bound of the game-clustered CI of the paired Brier difference (market -
model, per row) is > 0, then weight n/(n+100) on a *linear* pool. Three issues, each checkable without a fit: (a) it tests whether the calibrated model
alone beats the market, which T493 already shows it does not (CAL 0.6882 vs market 0.6865) and which a pool does not need to do; the object to test is
pooled-minus-market with a walk-forward weight; (b) the weight is a function of n (rows), where Bates-Granger says it should be a function of the error
precision on the disagreement; (c) the linear form is the one Ranjan-Gneiting prove miscalibrated. Proposal (Devin's decision, no live-path edit by this
role): when this rule returns a model word other than NONE, the forward confirmation scores the *pooled* forecast, and only then is `shrink_to_market`
replaced by the fitted logit pool with the walk-forward coefficients.

## 7. Playbook check (summary; per-area checks are in section 3)

1. **Information or capacity?** The primary adds no model information; it adds market-side information (soft books, spread) and decides how much of the
   existing model difference to trust. Capacity is held to 22 ridge-regularised parameters. The information lever (freshness) is deferred because history
   cannot measure it.
2. **Enough data for the class?** Yes for a low-capacity logistic pool (gate G1, G2, G8); the unpooled form was too heavy (+0.0005 overfit) and was cut
   before any real outcome.
3. **Valid test?** Real-tip hard filter (min lead 62.55 minutes), played legs only (Kalshi and books both settle inactive players at no loss), x.5
   lines only, planted future exact (0.0), derangement placebo, positive control in both directions, three clusterings, power measured not assumed. Known weakness:
   2023-24 and 2024-25 are the seasons H2 and MARKET_SHRINK used, so a pass is exploratory and not independent confirmation; the stored `p_model` for 2024 differs
   from today's production frame on about half the legs (118-row backfill, T493-T496 note), a disclosed vintage difference.
4. **Lineage.** H2 and MARKET_SHRINK (one-weight blends: this rule nests them), T502 (offset GBM: replaced by nested logistic arms), LINE_CALIBRATION (market-free
   map: the model's own recalibration is not repeated), GAME_MARKET_CONTEXT (spread is a market-side covariate here, not a model input).
5. **Scored against:** log loss at the Pinnacle line (the T477/T480 question), paired against the market alone and against the recalibrated market.

## 8. Gate summary (details in the rule, section 8; `reports/prereg_market_anchored_props/gate.json`)

23 of 23 checks pass (G7g, off-span market errors, added after the adversary's pre-freeze review) after the design changes disclosed in rule section 13 (the first draft of the gate failed six checks, which is why the model went from 36
to 19, then 22 parameters). Legs 35,146 / 37,528 (equal to MARKET_PRIOR_RESIDUAL's gate), 1,249 / 1,260 games, 377 / 414 player-seasons, 35 / 36 ISO weeks, minimum
lead 62.55 minutes, planted future exactly 0.0. Synthetic outcomes on the real legs, 10 replicates per world: the null world fires nothing (0 of 10) and
costs +0.00016 per leg (2024) / +0.00041 (2023); the market-mis-calibrated, model-inert world (oracle 0.0010) fires the market word 10 of 10 and the model
word 0 of 10; the model-informed worlds (oracle 0.0011 and 0.0030) fire the model word 10 of 10 (INFO in 1 and 10) and the market word 0 of 20 falsely;
both planted (oracle 0.0032): the model word 10 of 10 (DETECTED 8, INFO 2) and the market word MISCALIBRATION_INFO 10 of 10. Binding half-width of the report-season C_mod under the null 0.00009, so the instrument can resolve the
-0.001 tier (and sees an oracle of 0.0011 as C_mod about -0.0006: gammas below H2's largest weight are detected, not INFO). The one thing the gate says
history cannot do is the freshness covariate (3.5).

## 9. Priors (also appended to PREDICTIONS_LEDGER)

Written before any real-outcome contrast (the only outcome-like numbers I have seen are the synthetic gate worlds and Devin's live exploration above):
see `docs/PREDICTIONS_LEDGER.md` rows dated 2026-10-23 (MARKET_ANCHORED_PROPS).

## 10. Unknowns and what I could not verify

- Whether the engine's DNP policy multiplies P(Y >= k) by p_play where it is compared with a Kalshi ask (3.4).
- Kalshi pre-2025-12-25 history, vendor coverage claims, and every paper's full text (abstract-level reading only).
- Whether novig / prophetx (exchange books present from 2024-12-19) price like Kalshi; used only as a descriptive slice.
- The live week's numbers are Devin's exploratory reading; I did not recompute them.
