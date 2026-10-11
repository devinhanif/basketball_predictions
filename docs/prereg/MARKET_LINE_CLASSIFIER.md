# MARKET_LINE_CLASSIFIER: predict the side of the book's line with the market as an input (pre-registration)

Status: FROZEN 2026-10-11 15:06 CT on Devin's confirmation ("i confirm everything, try everything", 2026-10-11 16:50; DECISIONS). Gate measured (all four pass; section below); Oct-Nov 2023 are the unscored walk-forward warm-up (no 2022 price pairs exist) and the interpretive choices in the gate section are frozen with the rule.
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

### Gate measured 2026-10-11 (harness `research/eval/market_line_classifier_eval.py gate`; no arm fitted; `reports/prereg_market_line/gate.json`)
1. PASS. DK t60 two-sided main-line pairs per stat/season (pts, reb, ast, fg3m): 2023 = 14,354 / 15,052 / 9,650 / 14,191; 2024 = 15,882 /
   14,748 / 10,574 / 12,548 (all >= 5,000; 53,247 and 53,752 in total). Consensus game total, home spread and h2h P(home) cover 100% of
   pairs (1,318 and 1,320 games with lines; game lines are the median over books of each book's main line; no row is filled). Consensus
   player p exists for 100% of pairs; its line differs from DK's on 7.3%. Scored after warm-up: odds exist from 2023 only, so the pooled
   walk-forward cannot score its first blocks: October 2023 (0 training rows) and November 2023 (2,401 rows < 5,000) are UNSCORED, which
   leaves 2023 with 11,180 / 11,742 / 7,592 / 11,121 scored pairs (78%) and 2024 with all of its pairs. Constant: a block is scored only
   with >= 5,000 earlier training rows.
2. PASS. DK pairs with snapshot_ts > tip - 60 min: 0 of 116,250 (2023) and 122,296 (2024) t60 pairs (ODDS_HISTORY's guard, called not
   copied); consensus-p rows after the as-of: 0; game-line quote rows after the as-of: 0 excluded of every row (so nothing was dropped); report
   snapshots after tip - 60 min: 0 of 38,794 (all games have a real tip, 0 on the proxy). b2b, tier, starter10 and n_out_rot are built from
   strictly earlier games or stored production rows.
3. PASS. M0 (DK de-vigged P(over)) reproduces ODDS_HISTORY's stored market log loss on the same pairs, same n, max |diff| 1.1e-16 (limit
   1e-6): pts/reb/ast/fg3m 2023 = 0.69140 / 0.68724 / 0.68504 / 0.67246 (n 14,354 / 15,052 / 9,650 / 14,191), 2024 = 0.69205 / 0.68645 /
   0.68586 / 0.67727 (n 15,882 / 14,748 / 10,574 / 12,548). The de-vig recomputed from the raw DK prices matches the stored p exactly. The
   2026-10-11 clean-up did not change these pairs (they come from the stored parquets; played/tip tables re-read from nba.duckdb gave the
   same n), so no registry-keys variant is needed.
4. PASS. Null rate by stat and tier (cells under 300 rows listed, not judged; the only such cells are the empty tier -1): game_total 0.0%
   and spread 0.0% everywhere; T-60 report tag (null = the game has no usable snapshot) 0.107% overall, max gap across tiers within a stat
   0.066 pp, across stats within a tier 0.044 pp (limit 2 pp). Planted-future on the feature builders and the walk-forward: future box scores,
   game lines and report rows, same-game box scores, post-tip report rows and flipped labels leave the as-of features byte-identical; the
   walk-forward trains strictly before each block, a rewrite of every row on/after a cut leaves earlier blocks' training sets identical;
   all four positive controls fire (a past edit moves later tiers, a report edit moves tags, a game-line edit moves features, a later block
   moves).
Built-in choices the text above fixes (unstated until now): tier = prior-10 points by an as-of join strictly before the game date (so a
DNP row is not special); spread is signed to the player's team; p_cons_over is at the consensus line; M4 permutes p_grid_over and z
jointly within (stat, season); one pooled model over the four stats per block with the stat as a numeric code; the pass rule is read
as 2023 selects (conditions 1-4 on the point and CI, no Holm) and 2024 confirms (all, with Holm over the 4 stats).

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

RUN 2026-10-11 under sha256 b85ae0d7237f77759393a2816c18e4885e91ba49462a0970da64c3b6b80f790b (harness git c9e9a6f). Runtime: smoke 6 s, run 40 s (18 blocks, 2 unscored), report 7 s.
Verdict: NO STAT PASSES. Full table, pairwise deltas, reliability: reports/prereg_market_line.md and reports/prereg_market_line/results.json.
M2 - M0 log loss (negative = better), 2023 (select) / 2024 (report; Holm over 4 stats):
- pts: +0.00189 [+0.00006, +0.00387] / +0.00027 [-0.00073, +0.00134] Holm 1.000 (n 11,180 / 15,882)
- reb: +0.00106 [-0.00085, +0.00297] / -0.00067 [-0.00184, +0.00044] Holm 0.908 (n 11,742 / 14,748)
- ast: +0.00157 [-0.00065, +0.00388] / +0.00045 [-0.00083, +0.00170] Holm 1.000 (n 7,592 / 10,574)
- fg3m: +0.00099 [-0.00116, +0.00295] / +0.00036 [-0.00105, +0.00174] Holm 1.000 (n 11,121 / 12,548)
M2 - M3 (market-only) and M2 - M4 (placebo), 2024: pts -0.00043 / -0.00046; reb -0.00018 / +0.00007; ast -0.00047 / +0.00010; fg3m -0.00041 / -0.00071; no CI excludes 0 and none reaches the -0.001 floor.
M1 (frozen blend, continuity) vs M0: 2024 -0.00032, -0.00141 (CI excludes 0), -0.00041, -0.00118 (CI excludes 0); 2023 is in-sample.
M2 is worse than the book on 2023 for all four stats, so the kill criterion "no stat passes on 2023" fires: closed, no follow-up. The one-weight blend M1 beats the classifier M2 in every cell: the classifier overfits relative to a single shrinkage weight.
Side accuracy 2024, M2 vs book (break-even at -110 is 52.4%): pts 51.5 vs 52.4, reb 54.8 vs 54.7, ast 54.7 vs 54.3, fg3m 57.6 vs 57.3. No EV section (no stat passed).


(not run)

Freeze: 2026-10-11 15:06 CT, commit 0a728ee, sha256 of everything above the line: b85ae0d7237f77759393a2816c18e4885e91ba49462a0970da64c3b6b80f790b (first 8: b85ae0d7). Verify with the awk command in the header.
