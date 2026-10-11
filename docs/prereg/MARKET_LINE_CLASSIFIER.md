# MARKET_LINE_CLASSIFIER: predict the side of the book's line with the market as an input (pre-registration)

Status: DRAFT 2026-10-11 ~16:40 CT, NOT FROZEN; awaiting Devin's "confirmed"; gate to be measured on real data before any freeze.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_LINE_CLASSIFIER.md | shasum -a 256`.
Season labels: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (frozen holdout, never loaded).

## What Devin asked
> "What can we do to get more context into the prediction that we haven't tried? The goal is to confidently predict over and under."
Everything tried so far re-shuffles our own inputs. The one context the production props model has NEVER seen is the market: the
line itself, the book's de-vigged probability, the game's total and spread, and where the line sits relative to our own forecast.
The frozen ODDS_HISTORY blend (one logit weight per stat) was the crude version: it passed only reb vs Pinnacle and was WOUNDED
(half of it was price-bias correction). This is the full version, with the market as a prior and our forecast as the residual
information, exactly the stance CLAUDE.md allows ("markets are a prior, not a target"; a shrinkage prior with a track record).

## Hypothesis
H1: a walk-forward classifier of the side (over/under at the book's main line) that takes as inputs the book's de-vigged
probability, the line's position relative to our integer-grid forecast (z and P(over L) from the grid), the game total and spread
(de-vigged, consensus), the stat, the player's tier and the production context flags (b2b, teammate-OUT count, report tag at
T-60) beats the book's own probability on log loss at T-60, per stat, on 2024-25 after selecting on 2023-24. Null: no gain.
Counter-hypothesis up front: ODDS_HISTORY showed the market better everywhere and the blend's only pass was wounded; the
classifier may only re-learn the market's price-bias (overs over-priced) and the attack arm below exists to show that.
Prior pass probability ~25% for reb, ~10% for the others.

## Data exposure (honest)
Seen: ODDS_HISTORY results (T203-T237); the 2026-10-11 descriptive cuts: side accuracy by stat (model 54.5% vs book 54.7%),
over-pricing by tier, tails over-confident, sharp-vs-soft null; the odds history holds h2h/spreads/totals and props at T-60 and
T-5 for 2023-25. Not seen: any classifier number. 2025 never loaded; `season <= 2024` asserted. Select 2023, report 2024.

## Fixed design (constants final)
Rows: DK main-line pairs with a production OOF row (the ODDS_HISTORY pairing; pushes dropped), ~27k per season per stat... exact
n from the gate. Label: over. Features (all at T-60, all stored, nothing from tonight): `p_book_over` (multiplicative de-vig,
consensus and DK both; DK is the one scored), `line`, `z = (line - mean_int)/std_int`, `p_grid_over` (our P(Y > L)), `stat`,
`tier` (prior-10 pts: <10, 10-17, 17-24, 24+), `starter10`, `is_b2b`, `n_out_rot`, `tag` (T-60 report status code), `game_total`
and `spread` (consensus de-vigged, from the h2h/spreads/totals markets at T-60; missing → median with a flag), `n_books`.
Model: LightGBM binary, production hyper-parameters (200 trees, lr 0.04, 15 leaves, min_child 100, seed 0), month-block
walk-forward, train strictly before the block, 2022 warm-up where pairs exist (else the first 2023 block is unscored and reported).
Arms (identical rows):
* M0 book: `p_book_over` as is (the bar).
* M1 blend: the frozen ODDS_HISTORY logit blend weight per stat (the crude version, for continuity).
* M2 candidate: the classifier with all features.
* M3 market-only control: the classifier on `p_book_over`, `line`, `stat`, `tier`, `game_total`, `spread`, `n_books` only (no
  forecast inputs): if M3 ≈ M2 the gain is price-bias correction, not our information.
* M4 placebo: M2 with `p_grid_over` and `z` permuted within (stat, season).

## Metrics, support, inference
Per stat, per season: log loss and Brier vs M0 (paired, game-clustered bootstrap 2000, seed 0); side accuracy at the book's
price with the break-even line (52.4% at −110) shown; reliability in ten bins; realised return per $1 at DK's price for legs
with classifier edge ≥ 0.03 (descriptive, reported only if H1 passes for that stat, as in ODDS_HISTORY). Family: 4 stats × {M2}
at T-60, Holm; M3 and M4 are controls. MDE from ODDS_HISTORY's SEs (~0.001 log loss per stat-season).

## Minimum data checks (fail = stop; measured before the freeze)
1. Pairs per stat per season ≥ 5,000 with DK two-sided main lines and a consensus game total/spread for ≥ 95% of them.
2. Leak guard: every feature's snapshot_ts ≤ tip − 60 min (ODDS_HISTORY choice 7).
3. M0 reproduces the ODDS_HISTORY market log loss per stat/season to 1e-6 on the same pairs.
4. Missingness audit of game_total/spread and tag by stat and tier; max gap ≤ 2 pp; planted-future test on the walk-forward.

## Pass rule (per stat; 2023 selects, 2024 confirms; all must hold)
1. M2 − M0 log loss ≤ −0.002 with CI upper < 0 and Holm p < 0.05.
2. M2 beats M3 by ≤ −0.001 with CI upper < 0 (our forecast adds information beyond price-bias correction).
3. M2 beats M4 by ≤ −0.001 (the forecast inputs are not just extra columns).
4. Reliability not worse than M0 (ECE within +0.005); side accuracy ≥ M0's.
A pass makes M2 a shadow candidate for the market-as-prior weight in the budget tool (logged live from the next game day, no
promotion); money claims need the live log.

## Kill criteria
No stat passes on 2023: close; no feature additions, no second learner, no T-5 variant afterwards; one pre-registered follow-up
at most (a T-5 version if T-60 passes), then closed.

## Outputs (exact)
`reports/prereg_market_line/results.json`, `reports/prereg_market_line.md`: `[stat, season, arm, n, logloss, brier, dlogloss,
ci_lo, ci_hi, p, p_holm, ece, acc, acc_book]`; `[stat, season, bin, n, mean_pred, mean_obs]`; `[test, ok]` for checks 1-4; seeds,
SHA, sha256. Code under `research/` only (`research/eval/market_line_classifier_eval.py`).

=== RESULTS BELOW ===

(not run)
