# PARLAY_JOINT_CAL: once each leg is calibrated by the frozen LINE_CALIBRATION map, are 2- and 3-leg parlay joint probabilities calibrated? (pre-registration)

Status: **DRAFT, written by the modeler Thu Oct 15 00:45:21 CDT 2026 (clock read by `date`). Not frozen. No fit, no score, no outcome read.**
- **How it becomes the rule:** pre-freeze review, then the main session commits `prereg(PARLAY_JOINT_CAL): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PARLAY_JOINT_CAL.md | shasum -a 256`. The scoring harness does not exist; it is written after the freeze and must refuse to run without that commit. The ticket CONSTRUCTION already exists as code (`research/eval/parlay_joint_cal_gate.py`: `cross_tickets`, `same_tickets`, `top_candidates`, `keep_top`, `ticket_fingerprint`) and is part of the rule; its identity is pinned below and the harness imports it unchanged.
- **Advice or momentum:** *Devin's override* (DECISIONS 2026-10-15 00:28, "Let's do 1, 2 3 4 and 5 now"; Claude had recommended follow-up #1, CAL at alternate rungs, first). It also serves Devin's top-3 parlay panel (DECISIONS 2026-10-15 00:24, item 2; `docs/contracts/top_parlays_v1.md`).
- **Model class:** none is fitted here except an optional two-parameter joint recalibration (arm J). The objects scored are products and a Gaussian copula of already-calibrated legs: playbook classes "Set and joint models (copulas for parlays)" and "Quantile and distribution layers (recalibration at the line)".
- **Probability only, no money.** This rule says nothing about ROI, EV, prices or what to bet. Prices are not used. The honest answer to "what should I bet?" is unchanged ("keep your money") until the live log says otherwise. A price-bearing question is a new id.
- **Selects nothing.** Pools, sizes, draws, arms and words are fixed below. **Select season 2023** (CAL-served legs only, the burn-in for J and the consistency check on every word); **report season 2024** (every decision word). Season 2025 is never loaded and never earns a touch under this rule (`max(season) <= 2024` is asserted before any read). **The confirmatory test is the 2026-27 live panel log** (below).

## Question
Devin's panel shows the top 3 two-leg and three-leg parlays per slate, ranked by `p_joint_cal`: legs calibrated by the frozen CAL map, cross-game legs multiplied as if independent, same-game legs joined by a copula (contract `top_parlays_v1`). T490 found that model "70%+" parlays hit about 10 points less, and T492 traced that to leg miscalibration compounding (a ticket's gap is roughly the sum of its legs' gaps), with cross-game dependence pushing the other way. CAL fixes the legs (T493: Pinnacle log loss -0.0117 vs raw, Stage 1 pass). Questions:
1. **Joint calibration.** Is mean(hit) equal to mean(`p_joint`) (within 0.02, interval inside +-0.03) for 2- and 3-leg tickets, separately for cross-game (independent product) and same-game (copula), once legs are CAL?
2. **Did fixing the legs fix the parlay?** Joint log loss of the CAL arm against the raw-leg arm on the same tickets.
3. **Ranking-relevant.** Is the top-3-per-date by `p_joint_cal`, which is what the panel prints, calibrated, or does selecting the highest estimates reinstate over-confidence (winner's curse)?
4. **Descriptive only (no word):** the copula against independence for same-game tickets, and an optional joint-level recalibration J.

## Legs (inherited; nothing re-chosen)
- **Source.** The MODEL_CLV paired frames `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` (T-60 snapshots, real tip, DNP legs voided upstream, main line, every line x.5), joined to `reports/odds_history/model_rows_{2023,2024}.parquet` for `team_id` and, in the harness only, the production grid. Read from the main checkout's `data/` and `reports/` by absolute path, never a worktree copy.
- **Books.** **DraftKings is primary** (the contract's example book and the book of the panel's legs); **Pinnacle** is the consistency book (CAL's own primary), direction only: it never issues a word.
- **A leg** is one T-60 row, unique per (game, player, stat, book), with both prices present, `tipoff_utc - snapshot_ts >= 60 min` asserted for 100% of rows (min 62.55 min in 2023, 64.33 in 2024).
- **Side.** The hit event is the side with the higher arm probability; for the CAL arms that is `p_cal >= 0.5` over, else under. The tickets are defined once, by CAL (below), and every arm then prices the same tickets, so arms differ in probability only.
- **CAL legs are the frozen LINE_CALIBRATION map** (`prereg(LINE_CALIBRATION)` sha256 a5441242adb37506194accd74ad9b637048fc0589223ad8249edd927c8bffba9, harness sha256 pins in `reports/prereg_line_calibration/select.json`), re-run walk-forward by the same code (`research.eval.line_calibration_eval.walk_forward`, arm `cal`): per (book, stat, calendar-month block), a weighted logistic fit on every earlier T-60 leg of the book, half-life 365 days, terms `[1, x, z, bench, bench * x]`. The harness MUST reproduce the Stage 1 aggregates in `select.json` (DraftKings and Pinnacle 2024 CAL log loss to 1e-9) before scoring anything. CAL here is fit through season 2024 only, as the contract states, until Devin logs a 2025 training-use row.
- **Eligibility (new here).** A block with fewer than 2,000 earlier fit rows is served raw by LINE_CALIBRATION (identity). **A leg in an identity block is not eligible for any ticket**, so every ticket is built from legs the CAL map actually served. In 2024 no leg is excluded (every block has at least 2,000 earlier rows, because 2023 is the fit history). In 2023 the October and November blocks and a few sparse ones are identity: 11,612 of 53,247 DraftKings legs (21.8%) and 10,479 of 35,146 Pinnacle legs (29.8%) are removed, leaving 171 slate dates.
- **Played-only universe.** DNP legs are voided upstream in the paired frame (`_dnp` is 0 for every row), so a ticket here is conditioned on all its players playing, exactly like CAL's own fit. A real panel pick can still DNP before tip; the sportsbook then voids that leg and shrinks the ticket. **That is not measured here** and is stated as a limit (open question 2).

## Tickets (fixed now, seeded, no hand selection)
Four cells: {cross-game, same-game} x {2 legs, 3 legs}. Legs are first sorted by (game_date, game_id, player_id, stat).
1. **Random tickets U-ALL (the calibration universe).** Per (season, type, size, date), up to **20** draws.
   - *Cross-game:* a draw picks m distinct games uniformly without replacement among that date's games with eligible legs, then one leg uniformly within each picked game. Seeds `default_rng([2026, season, m, 1])`.
   - *Same-game:* a draw picks ONE game uniformly among that date's games with at least m eligible legs, then m distinct (player, stat) legs uniformly without replacement. Seeds `default_rng([2026, season, m, 2])`. Same-player pairs (different stats) are allowed and labelled.
   - A leg set already drawn that date is dropped (no refill). Games first, then a leg, weights games equally, not legs (as in ALT_PARLAY_X).
2. **The panel slice TOP (ranking-relevant).** Per (date, size), the `TOP_N = 3` tickets with the highest `p_joint_cal` among all candidates, ties broken by lower leg-index tuple.
   - *Cross-game candidates:* each game keeps its 3 highest-q legs; all m-subsets of games times leg choices. This is exact for the top 3 of a product (a top-3 ticket never uses a leg ranked above 3 in its game).
   - *Same-game candidates:* each game's 8 highest-q legs; all m-subsets. An approximation, stated: a lower-q leg could enter the top 3 only through copula uplift, which is small.
   - **The union panel** ranks cross-game and same-game candidates together by `p_joint_cal` (the contract prints one list per size with a `same_game` flag); TOP-cross and TOP-same are also reported separately. Same-game candidates are ranked by the copula arm, cross-game by the product, so the union ranking is the panel's own.
3. **Settlement.** A ticket hits iff every leg's side hits (`over` from the paired frame). No pushes (half-point lines).

## Arms (probabilities for the same tickets)
- **RAW-IND:** product of the legs' raw production side probabilities (what T490 called the model's 70%).
- **CAL-IND:** product of the CAL side probabilities. **The served arm for cross-game tickets and the claim for cross-game cells.**
- **RAW-COP, CAL-COP:** Gaussian copula with the arm's marginals. Latent correlation by (relation in {same player, teammates, opponents}, stat pair), the estimator of `nba.parlay.copula.estimate_pair_corr` with `corr_shrink_k = 200` (`configs/parlay.yaml`), on normal scores of the randomized PIT of production's integer grid for every earlier game (strictly before the month block's first day, both seasons; `default_rng([2026, 7])` for the PIT uniforms in sorted row order), PSD-repaired by `nearest_correlation`. P(all hit) is the exact multivariate-normal orthant probability (`scipy.stats.multivariate_normal.cdf`, fixed `rng=default_rng(2026)`, `abseps = releps = 1e-8`), not the panel's 20,000-draw Monte Carlo (whose noise is about +-0.003 at p = 0.3). **CAL-COP is the served arm for same-game cells.** Cross-game tickets have no copula term, as in the contract.
- **J (optional, descriptive): joint-level recalibration.** Per (type, size): `logit P = a + b logit p_joint_cal`, IRLS with ridge 1.0 on `b`, weights `0.5 ** (age_days / 365)`, walk-forward by calendar month on the earlier U-ALL tickets (strictly before the block; a ticket's label is known only after its last game, asserted), scored on 2024 only. Fewer than 2,000 earlier tickets means identity. 2023 is J's burn-in. J is never shown on the panel unless a later rule says so.

## Estimands, CIs, words
**CI everywhere:** percentile bootstrap, B = 5,000, `default_rng(2026)`, resampling `game_date` within season (tickets of one date share legs and the slate, and the 20 draws share legs; game clustering is not usable because a ticket spans games; the date cluster subsumes the game cluster for same-game tickets). 95% interval. Every number is reported with its ticket count and date count.

**T1, joint calibration in the large (word-bearing; Family A, m = 4 cells).** Per cell, the 2024 DraftKings U-ALL tickets, served arm (cross: CAL-IND; same: CAL-COP): gap G = mean(hit - p_joint).
- **CALIBRATED:** |G| <= 0.02 AND the interval lies inside [-0.03, +0.03] AND the 2023 point |G| <= 0.03.
- **OVERCONFIDENT:** G < -0.02, interval upper < 0, 2023 point G < 0, BH q < 0.05. **UNDERCONFIDENT:** the mirror.
- else **INCONCLUSIVE**.
- p is the two-sided date-clustered bootstrap p of G against 0 (+1 correction). The +-0.02 limit and +-0.03 margin are carried from ALT_LINE_70 / ALT_PARLAY_X (Devin 2026-10-14 13:12). The gate below sets which cells can issue CALIBRATED (all four).

**T2, did fixing the legs fix the parlay (word-bearing; Family B, m = 4).** D1 = mean ticket log loss of the served arm minus its raw counterpart (cross: CAL-IND - RAW-IND; same: CAL-COP - RAW-COP), on the 2024 U-ALL tickets, probabilities clipped to [1e-6, 1 - 1e-6]. **CAL HELPS THE JOINT:** D1 <= **-0.005** (practical floor, set at about 40% of the algebraic estimate of -0.012 below, and well under two legs' worth of the Stage 1 per-leg gain of 0.0117) AND the interval upper < 0 AND the 2023 point D1 < 0 AND BH q < 0.05 (one-sided, Family B). **CAL DOES NOT HELP** if the interval lower > -0.005 (no practical gain); else INCONCLUSIVE. Power is approximate (an algebraic estimate, not a gate): if raw tickets are about 0.07 over-confident, D1 is about -0.012 against an independent-tickets half-width near 0.005.

**T3, the panel slice (word-bearing, directional only; Family C, m = 2 sizes).** Per size, the TOP union (3 per date), DraftKings, **pooled over the two seasons** (CAL-served 2023 dates plus 2024; resampling dates within season), gap G_top = mean(hit - p_joint_cal) with the served arm per ticket type. **TOP OVERCONFIDENT:** G_top <= -0.05, interval upper < 0, the same sign in each season, BH q < 0.05. **TOP UNDERCONFIDENT:** the mirror. Else INCONCLUSIVE. **No CALIBRATED word is issued for TOP in any cell**: the gate shows a model-only half-width of 0.049 to 0.057 per season (0.035 to 0.037 pooled), above the 0.02 bound. The -0.05 floor sits at about 1.4 pooled half-widths: a true gap of -0.05 is flagged about half the time, -0.07 about 85%.

**Descriptive, no word (labels only, never a claim):**
- **D2 copula against independence**, same-game cells: mean log loss CAL-COP - CAL-IND with both the game-date interval and the conservative bound. The gate says it cannot be decided: at an assumed equicorrelation 0.10 the expected gain per ticket is 0.0006 (2-leg) and 0.0023 (3-leg) against a conservative half-width of 0.0046 and 0.0093 (independent-tickets 0.0010 and 0.0021). Prior evidence: about 0.0011 log loss in total (JOINT work, 2026-10-11).
- **J against CAL** (joint log loss and gap), same four cells. The gate cannot size it (a 3-point miscalibration is worth about 0.002 per ticket against an independent-tickets half-width of about 0.002).
- Pinnacle for every statistic above (direction only); Brier; the calibration slope of `logit(hit)` on `logit(p)`; reliability in ten equal-count bands of `p_joint` (the top band is the ranking-relevant one); the TOP-minus-U-ALL gap contrast (winner's curse: claimed uplift against realised uplift in hit rate); RAW-ranked top-3 (what the panel would have shown without CAL) scored under both RAW-IND and CAL-IND; slices by stat mix, side mix (all-under, all-over, mixed), relation (same-player pair or not, teammates, opponents), month, and tickets whose raw joint is >= 0.50 (the T490 "70%" analogue; the count is reported even if small).

**Multiplicity.** Family A (T1) m = 4; Family B (T2) m = 4; Family C (T3) m = 2. Benjamini-Hochberg within each family at q < 0.05. Pinnacle and every descriptive row are outside every family.

## Minimum-data gate (non-negotiable 8), MEASURED on the real 2023-24 data
Code `research/eval/parlay_joint_cal_gate.py` (sha256 747c4cbb039c5fe40d622089107163c0be90f2f0b5cc71e54e59847a85d3e8fa); output `reports/prereg_parlay_joint_cal/gate.json` (sha256 4b57377d6b21ebdad11714dead1dce64d809046b372a1dd848260a5132e51aeb). Read-only, counts and model-only quantities only (no `over` or `y` column read, no CAL fit; the model P that sizes the widths is the proxy q = 0.5 + 0.5 (max(p, 1 - p) - 0.5), a stand-in for the CAL leg, so widths are indicative to a few percent), season <= 2024 asserted first, about 2.3 minutes, 300 replicates.

**Clock, read by `date` in the same command as the final run:** `Thu Oct 15 00:41:15 CDT 2026` at the start; `Thu Oct 15 00:43:31 CDT 2026` at the end. Command: `cd <worktree> && <main>/.venv/bin/python -W ignore research/eval/parlay_joint_cal_gate.py --data-root /Users/devin/Downloads/nba-prediction --out reports/prereg_parlay_joint_cal/gate.json`.

Inputs (sha256, identical to the LINE_CALIBRATION pins): `pairs_2023` 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569, `pairs_2024` 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c, `model_rows_2023` 002e53ef1a5fae333e8ff6edc92654de9f449d29c9be1775017e65fc378eea85, `model_rows_2024` ec47dcdf566eac29742f8269b8c3cf4a10845440c9b298b41a40282577b62d70. Data version: the stored frames above (the paired frames are write-once derived files, not a live table).

**Legs.** DraftKings T-60: 53,247 legs on 207 dates (2023), 53,752 on 212 (2024); CAL-served after the identity rule: 41,635 on 171 dates (2023, 78.2%), 53,752 on 212 dates (2024, 100%). Pinnacle: 35,146 / 37,528 legs; CAL-served 24,667 (70.2%) / 37,528. Raw leg q (the more probable side) median 0.583 (2023) and 0.578 (2024); the proxy median 0.54.

**U-ALL tickets, DraftKings** (model-only 95% half-width of the gap G when every leg is exactly calibrated and independent; the conservative bound assumes every ticket of a date is perfectly correlated):

| Cell | Tickets 2023 / 2024 | Dates 2023 / 2024 | Model-only HW 2023 / 2024 | Conservative HW 2023 / 2024 | Pooled tickets, HW | Gate |
|---|---|---|---|---|---|---|
| cross 2-leg | 3,018 / 3,658 | 151 / 183 | 0.0180 / 0.0154 | 0.073 / 0.066 | 6,676, 0.0117 | PASS |
| cross 3-leg | 2,560 / 3,260 | 128 / 163 | 0.0152 / 0.0123 | 0.064 / 0.056 | 5,820, 0.0101 | PASS |
| same-game 2-leg | 3,407 / 4,227 | 171 / 212 | 0.0160 / 0.0145 | 0.069 / 0.061 | 7,634, 0.0110 | PASS |
| same-game 3-leg | 3,416 / 4,238 | 171 / 212 | 0.0137 / 0.0121 | 0.056 / 0.050 | 7,654, 0.0090 | PASS |

Pinnacle (consistency): 2024 tickets 3,678 / 3,239 / 4,201 / 4,235 with model-only HW 0.0158 / 0.0130 / 0.0145 / 0.0126; 2023 tickets 2,969 / 2,559 / 3,380 / 3,396 with 0.0179 / 0.0162 / 0.0177 / 0.0148. Same-game pair mix in 2024 DraftKings 2-leg: 6.2% same player, 44.8% teammates, 49.0% opponents.

**Thresholds (set after the counts were seen, before any outcome; disclosed):** G1, the report season: a cell gets a T1 word only if its 2024 U-ALL has >= 3,000 tickets, >= 150 dates and a model-only half-width <= 0.02 (CALIBRATED needs 0.02 so that a point of 0.01 still fits inside +-0.03). G2, the select season: >= 2,500 tickets, >= 120 dates, half-width <= 0.02. G3, J: >= 2,000 earlier CAL-served tickets per cell for the first 2024 block. All four cells pass G1, G2 and G3. The thinnest are 2023 cross 3-leg (2,560 tickets, 128 dates; margins of 60 tickets and 8 dates, and that cell's 2023 role is only a sign and size check). **The conservative date-cluster bound (0.050 to 0.073) is above +-0.03 in every cell**; it assumes a whole slate moves as one ticket, which T490 did not see (in the two cells I compared, 55 3-leg and 65 2-leg, its realised intervals were within about 1.0 to 1.1 times the model-only width), so the rule uses the model-only width to decide what a word can say, and the realised interval decides the word. If the realised half-width exceeds 0.03 in a cell the interval cannot sit inside +-0.03 and the word is INCONCLUSIVE; that is a result, not a gate failure.

**TOP panel slice (DraftKings, union of cross-game and same-game, independence proxy ranking):** 2-leg 513 tickets on 171 dates (2023) and 636 on 212 (2024), model-only half-width 0.0568 / 0.0494; 3-leg 513 / 636 tickets, 0.0519 / 0.0485. About 41% of the 2024 2-leg union and 43% of the 3-leg union are same-game tickets (copula re-ranking in the harness may move this). Pooled half-width about 0.037 (2-leg) and 0.035 (3-leg) (the two seasons combined in quadrature). Candidate counts: 42,225 cross-game and 36,337 same-game 2-leg candidates in 2024. **Gate result:** the TOP slice passes for the directional word T3 and cannot issue CALIBRATED in any cell.

**Not decidable and therefore word-free (gate result):** D2 (copula against independence), J, and any per-band statistic. The 2024 DraftKings same-game expected gain of the copula over independence, by assumed equicorrelation 0.05 / 0.10 / 0.20, is 0.00015 / 0.00058 / 0.00233 per 2-leg ticket and 0.00059 / 0.00229 / 0.00883 per 3-leg ticket, against independent-tickets half-widths 0.0005 / 0.0010 / 0.0021 (2-leg) and 0.0010 / 0.0021 / 0.0041 (3-leg), and conservative half-widths 0.0023 / 0.0046 / 0.0093 and 0.0047 / 0.0093 / 0.0185.

**Result: PASS for T1 (4 of 4 cells), T2 (4 of 4, power approximate), T3 (2 of 2, directional word only); D2 and J are descriptive by the gate.**

## Data-capture audit (BEST_PRACTICES 2026-10-12; non-negotiable 8)
- **Seasons held.** `odds_history` holds seasons 2023 (6,493,250 rows, 1,318 games) and 2024 (6,841,012 rows, 1,320 games) only; there are 0 rows before 2023-24. No props-price table is held for 2019-2022 (`odds_history` has 0 rows before 2023, as LINE_CALIBRATION states; `data/history/` 2019-2021 holds games and availability), so no earlier CAL fit history exists to repair 2023's identity blocks.
- **Used:** DraftKings and Pinnacle T-60 main lines (the two books CAL is fit on), seasons 2023 and 2024, all four stats; production grid `model_rows` for the copula's residual correlations.
- **Exists, not used, and why:** FanDuel T-60 (the largest book, 2.72M t60 rows including alternates; CAL is not fit on it, so no CAL leg exists; a FanDuel CAL fit is a new LINE_CALIBRATION-type id, cost: one harness run); `consensus` and 40 other books (same reason, thinner); T-5 snapshots (after the model's information cutoff; the panel is made before tip from T-60-class information); BetRivers alternate rungs (T490's pool; CAL is defined at the main line only, follow-up #1 "CAL at alternate rungs" is a separate id); season 2025 (frozen holdout; lines pulled but not read; CAL for 2026-27 is fit through 2024 by contract); Kalshi and Polymarket ladders (forward-only, follow-up #3); the 2026-27 live panel log (does not exist yet).
- **Repairable gaps and their cost.** (a) 2023 identity blocks (21.8% of DraftKings legs): not repairable, there is no earlier season; the legs are excluded and counted. (b) DNP-voided panel picks: the paired frame drops DNP rows, so the void rate and its effect cannot be measured here. Repair: rebuild the paired frame keeping DNP rows (an upstream change in `nba/markets`, not this agent's) and settle with sportsbook void rules; cost moderate, and CAL itself is fit on played-only legs, so the shown probability is conditional on playing either way. Left for the live log (the live harness records the void rate). (c) The panel's actual candidate rules (price floors, one leg per player, availability filters) are not in the contract; the rule uses the widest candidate set (open question 1).
- **Verdict of the audit:** the data are comprehensively captured for the question as posed (two books, two seasons, all four stats, every eligible T-60 leg). Nothing the project holds could add a third season of CAL-served props legs.

## Hypothesis and priors (stated before any result; binary events and expected values with 80% intervals; drafted for `docs/PREDICTIONS_LEDGER.md`)
Reasoning: CAL fixes calibration in the large of single legs (T493: P4 passes), so the joint of calibrated independent legs should be near calibrated; the selection of the more probable side and of the highest-q tickets may reinstate over-confidence; cross-game dependence is about zero (T492: E +0.002 to +0.016 after re-weighting, all intervals cover 0). Disclosure: I have seen T490 to T493, the POVER_BIAS scratch and the LINE_CALIBRATION Stage 1 aggregates (slope 0.46), so these priors are informed, not blind; I have seen no ticket-level outcome of this rule.
- Family A (2024 DraftKings, served arm): P(cross 2-leg CALIBRATED) **0.55**; P(cross 3-leg CALIBRATED) **0.50**; P(same-game 2-leg CALIBRATED) **0.45**; P(same-game 3-leg CALIBRATED) **0.40**; P(all four CALIBRATED) **0.15**; P(any cell OVERCONFIDENT) **0.12**; P(any cell UNDERCONFIDENT) **0.08**. Expected cross 2-leg gap **-0.004** (80% -0.020 to +0.012); cross 3-leg **-0.004** (80% -0.022 to +0.014). The realised half-width is expected near 0.016 to 0.019, so CALIBRATED needs |G| below about 0.012.
- Raw legs (context, descriptive): expected RAW-IND cross 2-leg gap **-0.07** (80% -0.10 to -0.04); P(RAW-IND OVERCONFIDENT by more than 0.02 in all four cells) **0.90**.
- Family B: P(CAL HELPS THE JOINT in each cell) **0.85**, P(all four) **0.65**; expected D1 cross 2-leg **-0.015** (80% -0.030 to -0.006).
- Family C: P(TOP OVERCONFIDENT, size 2) **0.15**; P(size 3) **0.15**; P(either) **0.25**. Expected pooled TOP gap **-0.02** (80% -0.07 to +0.03) for both sizes: the top-3 by estimated probability is a winner's curse, partly absorbed by the logistic fit.
- Descriptive: P(CAL-COP point log loss < CAL-IND in both same-game cells) **0.55**; P(its interval excludes 0 in either) **0.10**; P(J point log loss < CAL in at least 3 of 4 cells) **0.40**; P(DraftKings and Pinnacle agree in the sign of G in all four cells) **0.80**.
- Live (2026-27 final look): P(TOP OVERCONFIDENT by the -0.05 rule, either size) **0.20**; the live legs may come from AVG, which CAL was not fit on (hazard below).
- P(gate or integrity stop at the harness preflight, for example the Stage 1 aggregates not reproduced to 1e-9) **0.03**.
- **Honest default:** CAL repairs the legs, the product of repaired legs is close to honest for cross-game tickets, the panel's top tickets carry the largest selection risk, and none of this is a reason to bet.

## Live confirmation (the confirmatory test; frozen with this rule, harness written after the freeze)
- **What is scored.** The panel's own output, `reports/parlay/top_parlays_<slate_date>.json` (`schema top_parlays/1`), joined to settled box scores by the truth layer (read-only). Each printed ticket carries `p_joint_cal` and `p_joint_raw`; the harness scores `p_joint_cal` against the settled outcome of every leg (a leg whose player did not play voids; **a ticket with a void leg is excluded and counted**, the exclusion rate being reported because it measures the DNP effect that the 2023-24 test cannot).
- **Looks.** One final look at the first of (the regular-season end, 500 settled tickets per size); interim looks are descriptive and never words. The look date is fixed by the rule and not moved after seeing a number.
- **Words (per size and pooled over cross-game and same-game):** LIVE OVERCONFIDENT (gap <= -0.05, interval upper < 0, date-clustered, BH over the 2 sizes), LIVE UNDERCONFIDENT (mirror), else INCONCLUSIVE; no CALIBRATED word (a 500-ticket top slice has a half-width near 0.045). Slices: `model_source` (AVG against production), `same_game`, verdict, month. The live result is read next to the 2024 TOP gap by sign, not pooled with it.
- **Hazard stated now:** CAL was fit on production's P(over); the panel may serve AVG-based legs (`model_source = AVG`). AVG has a different slope (T480: AVG decile slopes 0.42 to 0.72), so applying a production CAL to AVG's P is out of distribution. The `model_source` slice exists for that reason and the finding is reported whichever way it goes.

## Lineage (playbook question 4)
Ancestors: LINE_CALIBRATION (the legs; the frozen CAL map, the identity rule, the Pinnacle-primary choice), ALT_PARLAY_X / T490 / T492 (the construction pattern, the date-clustered bootstrap, the CALIBRATED / OVERCONFIDENT words and the +-0.02 / +-0.03 margins, the "weighting artifact" caveat), the 2026-10-11 parlay work (`docs/PARLAY_ENGINE.md`, the copula and JOINT set transformer: copula over independence about 0.0011 log loss, cross-game residual correlation near 0, same-player legs co-move), `nba/parlay/copula.py` (the served dependence model). Guards carried: real-tip gating (>= 60 min asserted), played-only legs, date clustering, 2025 never loaded, BH over declared families, integer-grid support (the legs are x.5 lines on the integer grid), the paired design (arms price identical tickets), seeded construction with ticket fingerprints. Dropped, nothing is trained here except optional J: shuffled-label and permutation-null guards (CAL's own are inherited from LINE_CALIBRATION; here the arms are fixed maps of earlier data); no price or EV guard (no price is used).

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: set and joint models (copulas for parlays) over a recalibrated quantile layer; J is a two-parameter calibration layer.
1. **Information or capacity?** Neither. No new pre-tip information and no new model; it tests whether a transformation of calibrated forecasts is itself calibrated.
2. **Enough data for this class?** For gap words, yes (gate: 3,260 to 4,238 tickets and 163 to 212 dates per cell in the report season; widths 0.012 to 0.015). Not for the copula contrast or J (class check: the copula's whole gain over independence is about 0.001 log loss, below any one season's detectable effect; reported descriptively and, by the playbook, to be pooled or monitored rather than tested once). Not for the TOP slice's CALIBRATED word (636 tickets per size).
3. **Test validity?** Real tip, >= 60 min lead asserted, played-only legs (stated limit), integer-grid x.5 legs, paired arms on identical tickets, CAL legs by the frozen walk-forward code reproduced to 1e-9, identity-block legs excluded, seeded construction, date clustering. Risks: tickets share legs across the 20 draws (hence the date cluster); 2024 is also LINE_CALIBRATION's select season (the CAL map was chosen with 2024 labels in view, so a 2024 pass is weak evidence of out-of-sample skill; the 2026-27 live log is the clean test); the model-only widths use a proxy, not the CAL legs; the panel's real candidate rules are not in the contract; DNP voids unmeasured; selection by `p_joint_cal` ties the TOP slice to the CAL map's own errors.
4. **Lineage and inherited guards:** above.
5. **What it scores against:** joint calibration in the large and joint log loss on a binary event, not CRPS and not a price.
Class checks. Power: a gap of 0.03 is detectable in every cell, the top slice needs about 0.05, the copula gain and J are below detection. Correlation structure: cross-game legs treated as independent (tested by T1 against the realised joint); same-game dependence estimated from earlier games only.

## What each choice protects against
| Choice | Protects against |
|---|---|
| Tickets defined by CAL, every arm prices the same tickets | comparing arms on different ticket sets; the raw model's side choice biasing the contrast |
| Excluding legs in identity blocks | calling raw-served legs "CAL" in 2023 |
| Select 2023 / report 2024, 2023 only as a sign and size check | tuning on the report season; 2023's thin early CAL fits driving the word |
| 2025 never loaded; confirmation is the live log | spending the holdout on an exploratory probability question (the CAL map already has a logged-row path for 2025 that is Devin's) |
| Date-clustered bootstrap | an anti-conservative interval when tickets share legs and a slate |
| Gap (calibration in the large) carries the words; slopes, bands and J are labels | words on statistics the gate cannot size |
| Floor 0.02 / margin 0.03 carried; D1 floor 0.005; TOP floor 0.05 | a p-value standing for a practical effect; a word nobody can reach |
| Separate cross-game and same-game cells, copula only for same-game | a copula fit hiding a cross-game miss, or independence hiding a same-game one |
| TOP slice reported next to U-ALL | a calibrated average hiding a selection effect on exactly the tickets the panel prints |
| Exact orthant probability for the copula | Monte Carlo noise (about 0.003) becoming a gap |
| DraftKings primary, Pinnacle direction only | choosing the book after seeing which one passes |
| Construction pinned by code hash and ticket fingerprints | the construction drifting after the freeze without the markdown hash noticing |
| No price, no EV | a probability finding being read as a betting recommendation |

## Unstated details fixed before any scored number
- `over` is the paired frame's over indicator; a ticket hit is the AND of its legs' side events.
- Probabilities are clipped to [1e-6, 1 - 1e-6] for log loss; CAL legs keep LINE_CALIBRATION's own clip (0.5/199).
- The 2023 U-ALL tickets use only CAL-served legs (eligibility is applied before the draw, so a date with too few served games yields fewer tickets); the draws use the same seeds as the gate.
- Ten equal-count bands of `p_joint` are cut within (cell, season) from the probabilities only.
- The union panel breaks ties by the lower leg-index tuple; the contract's EV tiebreak is not used (no price).
- The model-only half-width uses 300 replicates, seed `[2026, 99, season, m, i]` (pooled `[2026, 99, 0, m]`); the conservative bound uses the model's own P for variance.
- Copula residual pairs, shrinkage (`corr_shrink_k = 200`), PIT seed and PSD repair are those stated above; a month block with no earlier same-game pairs uses zero correlation.
- J's weights, ridge and 2,000-ticket identity rule are fixed above and never tuned.

## Construction identity (so the markdown hash covers the code)
- `research/eval/parlay_joint_cal_gate.py` sha256 747c4cbb039c5fe40d622089107163c0be90f2f0b5cc71e54e59847a85d3e8fa; numpy and polars versions of the gate run are recorded by the harness. Any change after the freeze is a new rule id.
- The harness MUST, before scoring: (1) assert this file hash and `max(season) <= 2024`; (2) reconstruct every (book, cell, season) ticket table and assert the ticket counts and `fingerprint_sha256` equal `reports/prereg_parlay_joint_cal/gate.json`; (3) reproduce the LINE_CALIBRATION Stage 1 CAL and A0 log losses for both books in 2024 to 1e-9 against `reports/prereg_line_calibration/select.json` (sha256 a5441242...); (4) refuse to run without the `prereg(PARLAY_JOINT_CAL)` commit.

## Kill criteria and what a result opens
- Any cell INCONCLUSIVE or CALIBRATED is recorded; nothing about pools, sizes, draws, thresholds or arms changes after a result. One pre-registered follow-up at most.
- **OVERCONFIDENT in a panel-relevant cell** (cross or same-game, either size) or **TOP OVERCONFIDENT**: a proposal to Devin that the panel print the J-adjusted or shrunk number (a new rule, J is descriptive here) or carry an explicit caveat; the displayed `p_joint_cal` is not changed by this rule.
- **CALIBRATED in all four cells and TOP not overconfident:** the panel's "calibrated joint probability" wording stands for 2023-24; the live log is still the test. No promotion.
- Season 2025 is never touched under this rule; a 2025 look is a different id with its own logged row.

## Commands (maintainer; read-only)
- Gate (dry-run count = the table above; scope: the paired frames and model rows of 2023 and 2024, no labels): `cd /Users/devin/Downloads/nba-wt-parlay-joint-cal && /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore research/eval/parlay_joint_cal_gate.py --data-root /Users/devin/Downloads/nba-prediction --out reports/prereg_parlay_joint_cal/gate.json` (about 2.3 minutes; light; takes no heavy lock).
- Scoring: **no command exists** until the freeze. When written it will wrap heavy steps in `mkdir data/ops/heavy.lock` (retry 120 s, `rmdir` in `finally`), checkpoint per stage and print progress per chunk.

## Open questions for Devin
1. **The panel's candidate rules.** `top_parlays_v1` does not say which legs are eligible (one leg per player? an availability or price filter? both sides or only the higher-probability side? alt rungs?). This rule uses every DraftKings main-line leg, the higher-probability side, and one leg per (player, stat), the widest set. If the market agent's producer differs, the TOP slice describes a different list. Safe default: score the widest set now and add a live-only slice for the real producer.
2. **DNP voids.** A panel leg whose player does not play voids in a real book. 2023-24 cannot measure it (the paired frame drops DNP rows). Safe default: condition on playing, as CAL does, and let the live log report the void rate.
3. **AVG-based legs.** CAL was fit on production P; the app now shows AVG-based props. Should the panel use production legs until a CAL refit on AVG exists? Safe default: slice by `model_source` and report.
4. **Book.** DraftKings is primary because the contract shows it; Pinnacle is CAL's primary book. Safe default as drafted.

## What Devin must sign
1. The four cells, the random-ticket and panel constructions, and that the served arm for same-game tickets is the copula.
2. The words: T1 (gap, +-0.02 and +-0.03, CALIBRATED issuable in all four cells), T2 (D1 floor -0.005), T3 (TOP directional only, floor -0.05, no CALIBRATED), D2 and J descriptive, three BH families (4, 4, 2).
3. Select 2023 (CAL-served legs, a sign and size check) / report 2024, and that 2024 is partly exposed through LINE_CALIBRATION.
4. The live confirmation (final look, words, exclusion of voided tickets, `model_source` slice) and that a result is never a recommendation to bet and 2025 is not touched.

=== RESULTS BELOW ===
(none. No fit, no score and no outcome exist. The gate above is counts and model-only quantities.)
