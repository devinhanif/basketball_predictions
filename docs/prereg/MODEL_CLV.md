# MODEL_CLV: does the production props model get a better price than the close? (pre-registration)

Status: FROZEN 2026-10-11 ~23:10 CT under Devin's approval of the day ("im keeping the api ... they run as soon as you're done, i approve"; docs/DECISIONS.md 2026-10-11 ~22:55). Pre-freeze amendments decided by the main session after the gate measure and recorded below: A1 KEPT (shuffle null is pass condition 3; check 4 amended to match); A2 NOT APPLIED (M3 kept as drafted at 0.03: a threshold change needs the matched fade comparator rebuilt, and M1/M2 carry the question; M3 is informative on DraftKings only); A3 done (parquets placed at data/ops/model_clv_pairs/, sha256 re-checked by `run`); FanDuel stays descriptive. Committed by the maintainer's hand BEFORE any arm result.
Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MODEL_CLV.md | shasum -a 256`. Seasons: 2023 = 2023-24 (select), 2024 = 2024-25
(report), 2025 = 2025-26 (frozen holdout, never loaded, never earns a touch under this rule).

## Why
Devin, 2026-10-11: the benchmark for the live season becomes the CLOSING line, and the model may be trained to beat lines. Closing-line value (CLV)
is the currency sharp bettors are measured in: a bet that gets a better price than the close was right about where the market was going,
without waiting for 300 outcomes. Until now the project scored the model on outcomes (log loss, Brier, return); this rule asks the earlier
question, with a power advantage: CLV is a continuous per-leg quantity, outcomes are 0/1.

## Hypothesis
H1: for the legs the production props model would place at T-60 (edge >= 0.03 or 0.05 against the book's own implied probability), the book's
de-vigged closing probability of the chosen side is higher than its de-vigged T-60 probability of that side, on average, by at least half a
point of probability, in Pinnacle and DraftKings, for pts / reb / ast / fg3m, and not merely because (i) the side is chosen by the price's own
level, or (ii) DraftKings is chasing Pinnacle. Null: no CLV, or CLV a price scanner or the market's own drift would also earn.
Prior pass probability (mine): any Pinnacle cell passing the full rule on both seasons about 15%; any DraftKings cell (which also needs to beat
fade-retail-toward-Pinnacle) about 7%. Devin's prior: ~35% Pinnacle, ~15% DraftKings against the fade comparator. Why mine is lower: the
T-60 -> close window (about 55 minutes) is where the market digests the injury report, which the model already reads (`has_report`, `n_out_rot`);
the model's known under lean is a market lean; and the measured shuffle null below already eats a third to a half of the floor.

## Closing proxy (stated, not hidden)
"Close" here = the `t5` capture. Its `snapshot_ts` is about tip - 9 minutes (requested for tip - 5), and the `t60` capture is stamped about
tip - 64 minutes. So this rule measures movement over roughly 55 minutes, not to the real close. The live companion rule will use the real
last-before-tip capture; passing here does not claim CLV to the real close.

## Data exposure (honest)
Seen before drafting: the shape of the two frames, row counts, join coverage, the unchanged-line share, the sign and size of the over-side
drift (about -0.0003 to -0.0010, see checks), the planted-shuffle and random-side placebo numbers (they drove the shuffle-null item [PROPOSED A1]).
NOT seen: any cell's model CLV, any CLV difference vs a comparator for the real model, any return, any L1 score. Season 2025 never loaded;
`season <= 2024` asserted on both frames; no DuckDB is opened by the harness. Inputs: `pairs_2023.parquet`, `pairs_2024.parquet` (production
OOF `p_model` at each book's line, de-vigged prices, outcome, tip time), built by `research/eval/model_clv_pairs.py` from the odds-history
model/market rows; their sha256 are frozen below and re-checked by `run`.

## Fixed design
Legs: t60 rows of pinnacle / draftkings / fanduel (consensus excluded). Join t60 to t5 on (game_id, player_id, stat, book).
- Side: edge_over = p_model - p_over_raw(T-60); edge_under = (1 - p_model) - p_under_raw(T-60); side = argmax (tie -> over); raw = the book's own vigged implied probability.
- Arms: M1 = p_model, bet when edge >= 0.03. M2 = p_model, edge >= 0.05. M3 = `blend_p(p_over_mult(T-60), p_model, 0.25)` (research/eval/odds_history_eval), edge (blend side probability minus raw) >= 0.03.
- (a) price CLV (primary), unchanged-line legs only (line at t5 == line at t60): chosen over: mult(t5) - mult(t60); chosen under: -(mult(t5) - mult(t60)). mult = multiplicative de-vig. Positive = we beat the close. Probability points.
- (b) line-move agreement (secondary), changed-line legs only: +1 if the chosen side's number was later moved past (over chosen and the line rose, or under chosen and the line fell), -1 if the line moved against; scored as the mean; the unchanged-line share is reported beside it.
- (c) controls, on the same legs as the arm's primary legs: shorter-price side (over iff p_over_mult >= 0.5); a seeded random side; always-over and always-under (the drift); mix-adjusted CLV (CLV minus (2s-1) x over move, s the cell's over share). Unconditioned controls over all unchanged legs of each book x stat: random, shorter price, always-over, and the fade-retail rule at 0.03 and 0.05.
- Fade retail toward Pinnacle (DK, FD): same-line Pinnacle T-60 quote required; gap_over = pin p_over_mult - retail p_over_raw, gap_under = (1 - pin p_over_mult) - retail p_under_raw; side = argmax gap; bet when gap >= the arm's threshold (0.03 for M1/M3, 0.05 for M2).
- [PROPOSED A1] Shuffle null: for every arm and cell, 20 arms with `p_model` permuted within (game_id, stat, book) (seeds 1..20), same edge rule and legs rules; null = the mean of their cell CLVs. It prices what selecting the side by the market's own price level earns with no model information. Measured at the gate (below): +0.0018 (2023) and +0.0026 (2024) on average over the 20 cells, 36 to 52 percent of the floor, while the random side earns 0.0000/+0.0001. The user's "shuffled arm collapses to the random-side value" does not hold for this reason.
- Secondary (reported, never a pass condition): realised return per $1 at the T-60 price and at the t5 price of the chosen legs (unchanged lines); regression of CLV on edge and on the T-60 Pinnacle-minus-DK de-vigged gap of the chosen side (DK M1 legs with a same-line Pinnacle quote; clustered OLS; reports the edge coefficient with and without the gap, and the share of mean CLV left after removing the gap term). FanDuel cells are descriptive.
- L1 (descriptive): LightGBM, target = sign of the Pinnacle de-vigged over-probability move t60 -> t5 (unchanged lines, non-zero moves), features p_model, Pinnacle raw and de-vigged over probability, DK raw and de-vigged (same line, else NaN), Pinnacle-minus-DK gap, stat code, line, starter10, has_report, n_out_rot; fixed params (300 trees, lr 0.03, 15 leaves, min_child_samples 100, subsample 0.8, colsample 0.8, seed 0); fit 2023, scored 2024; log loss vs the 2023 constant base rate and vs the gap-only logistic on the same rows, game-clustered CI of the differences. Descriptive only: no pass rule (a pass would need a floor and CI, and a model that predicts the move is a different claim from a bet that earns it).

## Metrics, inference
Game-clustered bootstrap (game_id), 2000 resamples, seed 0, one multinomial resample matrix per season shared by all statistics; percentile 95%
CI; one-sided p = (1 + #{boot <= 0}) / 2001 for H0 "CLV <= 0". Family per season: the surviving {M1, M2, M3} x {pinnacle, draftkings} x
{pts, reb, ast, fg3m} cells (24 minus the cells dropped below), BH over the family on 2023; on 2024, BH over the cells that passed 2023.
Why not smaller: the 24 cells are the declared question and BH, not a hand-picked subset, handles them; the cells overlap heavily (same legs),
so BH is conservative, which is the right direction. FanDuel is outside the family: it is a retail book that follows the same Pinnacle signal
and would only add a third near-duplicate of the DraftKings cells.
Slices (reported, not pass conditions): by stat, book, arm, over/under share, starter (starter10 >= 0.5), has_report.

## Pass rule (verbatim)
A cell is a PASS iff all of the following hold on 2023 (select) AND again on 2024 (report):
1. mean price CLV (a) >= +0.005 (half a point of probability) AND the 95% game-clustered CI lower bound > 0 AND BH q < 0.05 (2023: over the surviving family; 2024: over the cells that passed 2023);
2. for DraftKings cells only: the difference of means, (cell CLV) minus (fade-retail-toward-Pinnacle CLV at the matched threshold, same stat and season), has CI lower bound > 0;
3. [PROPOSED A1] (cell CLV) minus (its 20-shuffle null) has CI lower bound > 0.
Else not a pass. A pass is a shadow candidate for a live CLV log only; promotion and money are Devin's.

## Kill criteria
No cell passes on 2023 (conditions 1-3 as above): closed. The one allowed follow-up is the live companion rule (real last-before-tip capture,
2026-27 season). No re-cut of thresholds, de-vig method, books or window after the result. Season 2025: no touch under this rule.

## Minimum data checks (fail = stop)
1. t60 <-> t5 join coverage >= 80% per book per season (join = a t5 row with a non-null de-vigged probability); the unchanged-line share is reported and, if under 50% anywhere, the primary metric is flagged under-powered (non-fatal flag) with this fallback: score (b) line-move agreement as the primary and drop (a) for that book-season.
2. Known-at: t5 `snapshot_ts` strictly before `tipoff_utc` for 100% of joined rows (asserted), and t60 `snapshot_ts` at least 60 minutes before tip for 100% of legs.
3. Placebo: random-side CLV has CI containing 0 for every cell. Bonferroni-corrected over the tested cells (the 20 family cells plus the 12 book x stat unconditioned) because "all 32 plain 95% CIs contain 0" fails about 3 times in 4 by chance; plain-95% misses are reported. This test cannot detect a de-vig bias (a random side is symmetric by construction); the drift diagnostic (always-over) does that job and is reported, not gated.
4. Planted: shuffle `p_model` within (game, stat, book) (seed 0): no shuffled cell clears floor + CI + BH, and every shuffled cell minus the 20-shuffle null has a Bonferroni CI containing 0 [uses A1]. The user's wording ("collapses to the random-side value") is reported as a non-fatal diagnostic: it fails (18/20 cells in 2023, 16/20 in 2024) because the shuffled arm still chooses the side by price level.
5. Cell size: >= 300 primary legs (edge >= the arm's own threshold, joined, unchanged line) per cell in 2023, else the cell is dropped from the family before the freeze and listed. (The literal "edge >= 0.03 legs" is reported beside it; they differ for M2 and M3.)

### Measured 2026-10-11 16:25 local by `gate --allow-draft` (research/eval/model_clv_eval.py; reports/prereg_model_clv/gate.json; 9 s)
Real data, read-only parquet, season <= 2024 (max loaded 2024), no model result computed for any decision. 2023: 133,075 legs, 1,318 games; 2024: 137,658 legs, 1,315 games.
1. PASS. Coverage (t60 legs, joined, share; unchanged lines, share of t60 legs / of joined): 2023 pinnacle 35,146 / 33,570 / 95.5%; unchanged 30,454, 86.6% / 90.7%. 2023 draftkings 53,247 / 52,815 / 99.2%; 48,467, 91.0% / 91.8%. 2023 fanduel 44,682 / 43,612 / 97.6%; 38,446, 86.0% / 88.2%. 2024 pinnacle 37,528 / 34,654 / 92.3%; 30,674, 81.7% / 88.5%. 2024 draftkings 53,752 / 53,075 / 98.7%; 46,464, 86.4% / 87.5%. 2024 fanduel 46,378 / 44,903 / 96.8%; 41,366, 89.2% / 92.1%. Lowest coverage 92.3%; lowest unchanged share 81.7%: not under-powered anywhere; the fallback is not triggered.
2. PASS. t5 stamp is 9.27 to 9.38 minutes before tip (2023) and 9.32 to 9.38 (2024) on every one of 129,997 and 132,632 joined rows (100% strictly before tip); t60 stamp 62.6 to 79.4 minutes before tip (2023), 64.3 to 64.4 (2024), 100% >= 60.
3. PASS. Random side: 0 of 32 tests outside the Bonferroni CI in either season (max |z| 2.37 in 2023, 2.50 in 2024); plain-95% misses 1 (2023) and 2 (2024) of 32, as expected. Drift (always-over mean move, unchanged legs): 2023 pinnacle -0.0008, draftkings -0.0003, fanduel -0.0004; 2024 pinnacle -0.0006, draftkings -0.0006, fanduel -0.0010 (the over price falls slightly, so an under lean earns a small positive CLV from drift alone).
4. PASS as amended, with the user's wording FAILING as a diagnostic. No shuffled cell clears floor + CI + BH (0/20 in both seasons); shuffled minus the 20-shuffle null: 0/20 outside the Bonferroni CI in both seasons. Shuffled-arm mean CLV over the 20 cells +0.0019 (2023), +0.0025 (2024); null +0.0018, +0.0026; random side +0.0000, +0.0001. Shuffled vs random side outside the CI: 18/20 (2023), 16/20 (2024).
5. PASS with drops. Primary legs per cell in 2023 (literal "model edge >= 0.03" in parentheses): M1 pinnacle pts 5,475 (6,888), reb 3,319 (3,860), ast 2,932 (3,336), fg3m 5,233 (5,652); M1 draftkings pts 6,904, reb 7,538, ast 4,588, fg3m 7,791; M2 pinnacle pts 4,297, reb 2,674, ast 2,217, fg3m 4,067; M2 draftkings pts 5,501, reb 5,798, ast 3,501, fg3m 6,081; M3 draftkings pts 491, reb 371, ast 337, fg3m 326 (an order of magnitude thinner: the 0.25 weight rarely lifts the blend 0.03 above the raw price). DROPPED (n < 300): M3 pinnacle pts 281, reb 142, ast 196, fg3m 138. Family = 20 cells. M3 unchanged legs available at lower thresholds in 2023: edge >= 0.02: draftkings 3,121, pinnacle 1,534; edge >= 0.01: draftkings 6,218, pinnacle 3,119 (information for amendment A2, not applied).

FAMILY_CELLS: M1|draftkings|ast, M1|draftkings|fg3m, M1|draftkings|pts, M1|draftkings|reb, M1|pinnacle|ast, M1|pinnacle|fg3m, M1|pinnacle|pts, M1|pinnacle|reb, M2|draftkings|ast, M2|draftkings|fg3m, M2|draftkings|pts, M2|draftkings|reb, M2|pinnacle|ast, M2|pinnacle|fg3m, M2|pinnacle|pts, M2|pinnacle|reb, M3|draftkings|ast, M3|draftkings|fg3m, M3|draftkings|pts, M3|draftkings|reb
PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c

## What each choice protects against
- Closing proxy at tip - 9 minutes, stated: protects against claiming CLV to a close we did not measure.
- Unchanged lines only for (a): a moved line makes de-vigged probabilities incomparable (different bet); (b) scores those legs on a direction so they are not discarded.
- Floor +0.005 with CI lower > 0 and BH: a significant 0.001 is not a betting edge; the floor is practical, the CI is the guard against noise, BH against 20 looks.
- Fade-retail-toward-Pinnacle comparator (DK): protects against calling a price scanner a model. Compared by cluster-resampling games jointly (two different leg sets), not on shared legs.
- Shuffle null [A1]: protects against CLV earned by choosing the side from the price's own level (a mean-reverting or drifting price pays whoever picks the long side), which a random side cannot expose.
- Random-side placebo with Bonferroni: protects against a miscentred metric in code, without a placebo that fails by chance.
- Planted shuffle: protects against a leak in the join or the edge (the model information is destroyed; CLV must collapse to the null).
- Select 2023 / confirm 2024 with a confirmation family of 2023 passers: protects against a one-season artifact.
- Game clustering: legs within a game share a market move.
- Dropping cells under 300 legs before the freeze: protects against a cell that can only pass by luck.

## Proposed pre-freeze amendments (Devin: keep or strike; none applied silently)
- A1 shuffle null as pass condition 3 and the amended check 4 (above). Without A1 the floor is cleared by the null alone in some cells. DECIDED 2026-10-11 ~23:10 (pre-freeze): KEPT.
- A2 M3 threshold: M3 at 0.03 has about 330-490 legs in DraftKings and is dropped in all Pinnacle cells; alternative edge >= 0.01 (6,218 DK / 3,119 Pinnacle unchanged legs in 2023). Not applied; changing it is a new arm, so strike M3 or keep it as drafted. DECIDED 2026-10-11 ~23:10 (pre-freeze): kept as drafted.
- A3 the pairs builder is not yet committed history: `research/eval/model_clv_pairs.py` (copied from the scratch builder, untested here) and the two parquets must be placed at `data/ops/model_clv_pairs/` by the maintainer; the frozen sha256 line makes the inputs auditable.

## Outputs (exact)
`reports/prereg_model_clv/results.json`, `reports/prereg_model_clv.md`, `reports/prereg_model_clv/ledger_rows.md` (T### placeholders); code under
`research/` only (`research/eval/model_clv_eval.py`, `research/eval/model_clv_pairs.py`, tests `tests/research/eval/test_model_clv_eval.py`).
Maintainer command after the freeze (about a minute; light, takes no lock, refuses if `data/ops/heavy.lock` exists):
`.venv/bin/python -W ignore -m research.eval.model_clv_eval run --doc docs/prereg/MODEL_CLV.md --sha <64 hex> --pairs-dir data/ops/model_clv_pairs`

=== RESULTS BELOW ===

Run 2026-10-11 23:35 CT under frozen sha256 dd16a802, git 6e75614, harness research/eval/model_clv_eval.py (one harness fix before any
result was seen: the doc parser split on the first occurrence of the marker text, which also appears inside the quoted freeze command;
it now matches the marker as a whole line; the frozen block is unchanged, hash re-verified). Gate 5/5 pass; family 20 cells.
Verdict: NO CELL PASSES on 2023 (select); closed. Price CLV of the model's T-60 pick is positive in every cell but an order of
magnitude under the floor: M1/M2 Pinnacle +0.0013 to +0.0033 probability points (all CIs above 0), DraftKings +0.0009 to +0.0029;
the 20-shuffle null (pick a side from the market's own price level, no model) earns +0.0018 to +0.0026 of that, and the model beats
its null by only +0.0000 to +0.0012. The fade-retail comparator rarely has the same-line Pinnacle quote on unchanged lines (n 1-56),
so the DK-vs-fade condition is weak evidence either way. 2024 confirms the same picture (M1/M2 +0.0013 to +0.0040). Reading: the
market moves toward the model's side by about a fifth of a point of probability in the last 55 minutes, i.e. the model holds a
little information the T-60 line lacks, worth under half a cent per dollar at even money; not a betting edge. Full tables:
reports/prereg_model_clv.md, reports/prereg_model_clv/results.json. One follow-up allowed: the live companion rule with the real
closing capture.
