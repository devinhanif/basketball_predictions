# Model playbook: what drives each kind of model, and how to judge it fairly

Purpose (Devin, 2026-10-12): every agent judges a model by what actually moves that kind of model, not by one past number.
Read before drafting, reviewing or rejecting any model. Each section ends with the checks a draft or review must answer.
Evidence citations are ledger rows; "unknown" means nobody has measured it here yet. Living document: the validity audit
(docs/reviews/validity_audit_2026-10-12.md) and every new result update it.

## Cross-cutting questions (answer for every model, every time)
1. **Information or capacity?** Does the arm add information known before tip (reports, lineups, recent minutes), or only more model?
   Our record: information wins, capacity alone mostly ties or loses (CLAUDE.md).
2. **Was the training data enough for this class?** A short window is a flaw for data-hungry classes (neural), not for Elo.
   If in doubt, run a learning curve on the select seasons before concluding anything.
3. **Was the test valid?** Real tip gating, played-games handling, leak-free inputs, integer support for counts, power for the
   effect size, guards sized from a permutation null. Also: guards that cannot pass or fire too often by chance; an inert shuffle
   (only false passes); a placebo that leaks the future (only false fails); a comparator missing information production had; the data
   version of lineup and possession inputs at run time. Noisy inputs pull effects toward zero and CAN create a null.
   An invalid test is not a null (BEST_PRACTICES; validity audit docs/reviews/validity_audit_2026-10-12.md).
4. **Lineage.** Which earlier constructions is this essentially, which guards disqualified them, carried forward or dropped and why.
5. **What does it score against?** CRPS of the whole distribution and log loss at a market line are different questions;
   our CRPS gains have not reached the line (T477; line log loss worse than a coin flip before recalibration).

## Ratings (MOV-Elo, injury-Elo)
- Driven by: the result stream and its update speed (k), home edge, season carryover; injury information layered on top.
- Data appetite: low. More seasons do nothing past one season of warm-up (ELO_HISTORY_WINDOW null).
- Typical failure: late information it cannot see (the market's edge sits in close games and back-to-backs, 2026-10-12 scratch).
- Fair test: log loss and Brier vs the market and vs plain Elo, game-clustered; accuracy is too noisy to judge.
- Checks: does the arm add pre-tip information? Is the effect plausibly larger than about 0.002 log loss (one season detects
  about 0.004-0.005)?

## Linear and logistic models (ridge, logistic, injury coefficients)
- Driven by: feature quality and stability; little capacity, so they rarely overfit and rarely find interactions.
- Data appetite: low to moderate. Fine with a few seasons.
- Typical failure: features that are proxies for time or era (league-state inputs, era flags) learn the calendar.
- Checks: features as-of; any feature constant across rows of a date is an era flag in disguise.

## Gradient-boosted trees (LightGBM context residual, P(play), minutes)
- Driven by: features and their as-of correctness; residual targets over a strong base (recency average); calibration of the
  scale head and conformal quantiles.
- Data appetite: moderate. 2-4 seasons usually enough; more helps rare situations (role changes, returners) a little.
- Note: a 2023 select season trains on about one season, so a 2023-only fail is weak evidence (no tree verdict flipped: every 2023 fail also failed 2024).
- Typical failure: leakage through missingness patterns (the NULL-pattern leak), duplicated signals (CUSUM vs recent-role),
  and outcome-dependent populations (P(play) R coverage of questionable players).
- Fair test: integer-support CRPS for counts, game-clustered, with a past-only placebo and shuffle guards sized from a null.
- Checks: missingness audit beyond the as-of cause; planted future; does the arm only re-express the base?

## Quantile and distribution layers (conformal, lower-tail mixture, recalibration at the line)
- Driven by: calibration data volume per slice and the stability of the error distribution over time.
- Data appetite: moderate; per-slice calibration needs enough rows per slice (bench, starters, stats).
- Typical failure: shape errors at a specific point (our P(over) 4 pp low at the line) while the overall CRPS looks good.
- Checks: reliability table at the point that matters (the line), not only overall CRPS; slices fixed in advance.

## Neural sequence models (SEQ / ENSEMBLE E2, E3; W_ALL)
- Driven by: data volume first (W_ALL vs 2022-start: -0.0120 / -0.0072 pts, adversary SURVIVES), then input timing (real-tip
  report tokens), then seeds and early stopping.
- Data appetite: high. The history gain is largest early in a season and fades as the current-season pool grows.
- Validity audit: every SEQ export before SEQ_FULL_MIX_RT used the 19:00 proxy tip; SEQ-alone and ENSEMBLE verdicts (T065-T068, T441-T445) are INVALID on the training window (W_ALL raw gain -0.047 / -0.025 exceeds their recorded harm). Real-tip timing alone is unmeasured (confounded with rerun noise).
- Typical failure: era identification (the embedding probe disqualified E3, T443; the probe is weak, AUC 0.96-0.998 even between
  adjacent modern seasons), near-DNP rows (T442 guards), proxy timing channels (proxy19).
- Fair test: walk-forward warm refits by month, 3 seeds, permutation null on the blend, shuffled-input placebo, past-only placebo;
  learning curve before concluding a loss.
- Checks: training window long enough? era channels zero on test rows? lineage guards carried forward?

## Count models (NB / Poisson heads for zero-heavy stats: blk, stl, fg3m)
- Driven by: the mean model and the dispersion. A continuous scale family that is rounded to integers (the production recipe: recency mean +
  residual + conformal standardised quantiles) misplaces P(Y = 0); a count family has the support built in.
- Data appetite: moderate; the dispersion needs a few thousand rows per mean bin (4 bins on about 4,000 calibration rows were enough).
- Evidence here (COMBO_STOCKS_SHADOW, select season 2023, n = 25,815): production recipe on blocks predicted P(0) = 3% where 19.6% happened
  (1,011 rows) and lost to the recent empirical pmf (+0.0043); the NB head (LightGBM Poisson mean + binned NB dispersion) was calibrated at P(0)
  and won (-0.0069 vs recent, -0.0081 vs the calibrated-Normal baseline). The 2024 report season repeated it (blk -0.0094, stl -0.0151).
- Typical failure: P(0) reliability, tails at low means, randomised-PIT noise near a coverage bound (the PIT draw depends on the row order for a
  fixed seed: fix the order in the rule), counts of near-zero-minute rows, an empirical comparator that is already a count distribution.
- Fair test: integer CRPS on the same 199-point grid for every arm; the recency-weighted empirical pmf as the comparator (it beat the calibrated
  Normal on blocks); a within-date label shuffle on the mean head (gain must vanish); slices by role and realised minutes.
- Checks: P(Y = 0) reliability by decile; randomised PIT coverage at 0.10/0.20/0.80/0.90 with a fixed row order; dispersion by mean bin; does the
  comparator have the same support?

## Set and joint models (JOINT set transformer, copulas for parlays)
- Driven by: correlation structure across legs; needs many same-game combinations.
- Data appetite: high for the joint structure; the copula's whole gain over independence is small (about 0.0011 log loss).
- Typical failure: power. One season detects about 0.002, above the plausible effect.
- Checks: is the effect large enough to ever be detected? If not, monitor or pool seasons rather than test once.

## Play-by-play generative models (PBP_GPT)
- Driven by: event volume and vocabulary consistency across seasons; long-context training; compute.
- Data appetite: very high. Earlier runs used 2022 plus part of 2023 only (T082-T084); history has the same schema for 2019-21.
- Typical failure: small evaluation n for pregame questions (n = 150), live questions dominated by score and clock; PBP_GPT was also trained on play-by-play in the wrong event order (validity audit) - rebuild tokens in game order before any retest.
- Checks: learning curve first; is the target one the market does not already price (live win probability is heavily priced)?

## RAPM and lineup-based ratings
- Driven by: correct lineups and stints, many possessions; data-hungry (needs multiple seasons or a prior).
- Validity audit: RAPM (T176) and F12 (T185) ran on pre-2026-10-09 stints that matched box minutes only 47-49% of the time: INVALID.
- Checks: stint data version at run time; a prior from earlier seasons.

## MLPs on tabular features (WINPROB mlp_offset, CTXRES_V2 mlp_quantile)
- Driven by: the same features as trees, with more sensitivity to scaling and less to interactions; usually lose to trees on tabular data.
- Data appetite: moderate to high.
- Typical failure: inheriting a leaky feature set (mlp_quantile used opp_adjusted_ridge, so its loss is INVALID).
- Checks: leak-free inputs; compare against the tree on the same inputs; learning curve if the window was short.

## How agents use this
- Every draft (modeler), review (adversary) and data change (archivist) names the model class, answers the cross-cutting
  questions and the class checks in a short "Playbook check" section.
- No more than 2 agents at a time (cost mode). The main session reads each agent's playbook check before acting on its verdict
  and sends a second agent to challenge any verdict whose playbook check is weak.
