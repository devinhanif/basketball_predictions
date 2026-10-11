Status: FROZEN 2026-10-11 17:18 CDT under Devin's approval of the day ("they run as soon as you're done, i approve", DECISIONS 16:23 CDT). Pre-freeze amendments after the gate measure, labelled in the doc: AM1 accepted (2023 R1 judged on mature-block rows), AM2 rejected (R4 stays a point estimate), A1 replacement and opp_r20 drop confirmed, R2 both seasons confirmed. Gate 20/20 measured on the AM1 draft (sha cb5fda29). Committed by the maintainer's hand BEFORE any arm beyond A0 is fitted.
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/GAME_MARKET_CONTEXT.md | shasum -a 256`. Seasons: 2023 = 2023-24, 2024 = 2024-25,
2025 = 2025-26 (frozen holdout, never loaded; the odds table has 2025 rows and every SQL here says `season <= 2024`).

# GAME_MARKET_CONTEXT: the market's own game lines (T-60) as the props model's game context, instead of our proxies (pre-registration)

## Why
Production `props_context_residual` v2 reads game context from our own numbers: the MOV-Elo expected margin (`exp_margin`, `abs_margin`) and the
teams' trailing 20-game scoring (`r20_pts_for/against`, `opp_r20_*`). Devin (2026-10-11): the model should "move away from proxies" and read the
market's game lines known at T-60 (Pinnacle de-vigged spread and the game total), which are sharper than our Elo (ledger: the market beats us on
winners by Brier 0.0064). Blowout risk (minutes of starters in a lopsided game) and pace (possessions, so counts) are the mechanisms.

## Hypothesis
H1: replacing the Elo margin and the r20 scoring columns with the T-60 market spread, |spread|, total and pace (A1), or adding them (A2), lowers
integer-support CRPS for pts, reb, ast, fg3m on 2024-25 after selecting on 2023-24, and does not worsen the over/under log loss at the DraftKings
T-60 lines. Null: no change. Prior (mine, not Devin's 20%): about 8% that any one of the 8 cells passes the whole rule, about 50% that some point
estimate is below zero. Reasons: the market margin correlates 0.90 with `exp_margin` on the scored rows (measured below), so the margin column adds
little; the total is less redundant (correlation 0.74 / 0.57 with the two r20 scoring averages) but pace moves counting stats by a few percent and
the floor of -0.005 is a hard bar; OWN_TAG (information the model had in hand but did not read) tied.

## Data exposure (honest)
Seen: the production OOF aggregates, the gate numbers below (coverage, correlations, training sufficiency), the over/under pairs' columns (not
their scores for any arm but the stored production `p_model`). Not seen: any CRPS or log loss of A1/A2/A3/P1 (nothing but A0 was fitted).
Season 2025 never loaded. `nba.duckdb` and `odds_history.duckdb` read-only.

## Fixed design (constants final)
Walk-forward, month blocks, train strictly before the block, 2022 as warm-up, seed 0, CPU, rows = played with `n_prior >= 5`, integer support for
EVERY arm: exactly the production harness (`research/eval/own_tag_eval.py`). Market source: `data/odds/odds_history.duckdb`, `snapshot_kind='t60'`
only. Book: Pinnacle where it has a complete spread AND total for the game, else the median of the complete quotes of draftkings / fanduel /
betmgm (at least 2). Known-at: a snapshot is used only if `snapshot_ts <= real tipoff_utc - 55 min`; the latest such snapshot per (game, book); no
real tip means no market (never a proxy). Columns (all game-level numbers attached to each player row):
`mkt_margin` (= - home spread for a home player, + for an away player; positive = player's team favoured), `mkt_abs_margin`, `mkt_total`,
`mkt_pace` (= total minus the mean ACTUAL total of the last 300 completed games with a strictly earlier date). Code: `research/features/game_market_context.py`.

Arms (identical rows, folds, seeds; names derived from `stat_feature_names`):
- A0 production (reference; must reproduce the stored OOF).
- A1 market context: `exp_margin`->`mkt_margin`, `abs_margin`->`mkt_abs_margin`, `r20_pts_for`->`mkt_total`, `r20_pts_against`->`mkt_pace` in
  place; `opp_r20_pts_for/against` dropped (no market analogue). Everything else identical.
- A2 both: production names plus the four market columns appended.
- A3 descriptive ("predict points without viewing points"): A1 names minus every player-own box-stat history column (`m10_pts/reb/ast/fg3m`, and
  `m5/m40/m100/sd10/role_gap/rate` of the stat), target offset `m = 0`, `s = 1` (the model predicts the stat directly); keeps minutes windows,
  role, rest, market context, teammates out. Reported only; no pass rule.
- Placebo P1a (A1 names) and P1b (A2 names): the game-level (home margin, total) tuples permuted across games within a game date as a derangement
  (no game keeps its own numbers; single-game dates pooled by season-month); same marginal distribution, no game information; pace and sign re-derived.

Training window (stated, handled): the market frames do not exist before 2023-24. Rows of 2022 and any game without a usable snapshot carry NULL
market columns in A1/A2/P1; LightGBM treats NaN as missing. For A1 this also removes the Elo and r20 context from the 2022 training rows. Measured:
market-bearing training games at the start of each 2023 test block: Oct 0, Nov 54, Dec 273, Jan 480, Feb 711, Mar 885, Apr 1,115, May 1,272, Jun
1,313; every 2024 block starts with 1,318 or more. Consequence for 2023 (the selecting season): the first four blocks (54% of its rows) are trained
with at most 480 market games, so A1 is handicapped there by construction. Reported as the slice `mature_blocks`, which is the R1 basis for 2023 (AM1) (test blocks with
>= 600 market-bearing training games: 2023 Feb-Jun = 46.2% of rows; 2024 100%).

## Metrics, support, inference
Primary: integer-support CRPS per stat, paired dCRPS (arm - A0), game-clustered bootstrap (2000, seed 0), per season; bias, 80% PIT coverage,
threshold log loss (as F11). Comparison on all scored rows (the production universe; rows of the ~0.4% of 2024 games with no market are scored in
every arm with NULL market columns) AND restricted to rows whose market columns exist (reported with CI; descriptive).
Placebo contrasts: A1 - P1a and A2 - P1b (the same names with game-information-free market columns; also controls the column-count and
column-subsample difference between A1 and A0).
Secondary (classification view): over/under log loss at the DraftKings T-60 main lines, pairs from `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet`
(book draftkings, snapshot t60: their keys, lines and prices only). `p_over` is recomputed in-run for every arm from its own integer quantile grid
(the same `pge_matrix`/`model_p_over` as the pairs builder), so A0 is a same-run reference (the stored `p_model` of 2024 predates the 118-row data
clean-up: only 49% of 2024 pairs reproduce to 1e-6, 2023 100%, so the stored column is not used). Rows with push, odd line or certain-push in any arm
are dropped for all arms. Log loss clipped at 0.5/199. dLL = arm - A0, game-clustered CI; the market's own log loss (`p_over_mult`) is printed.
Family: {A1, A2} x {pts, reb, ast, fg3m} = 8 p-values, BH within each season. MDE printed.
Slices (descriptive unless stated): starter / bench / first15 (guards); |spread| terciles, total terciles, Pinnacle vs consensus games, mature
blocks, market rows.

## Minimum data checks (fail = stop)
1. A0 reproduces `reports/lower_tail/candidates.json` 'off' (the re-baselined reference; 2024 n 27,701, pts 3.187553) to 1e-6, n identical, all 4
   stats x 2 seasons.
2. Market coverage of regular-season games with a T-60 Pinnacle-or-consensus spread AND total >= 90% in each of 2023 and 2024.
3. Known-at: zero used snapshots later than tip - 55 min; every used game has a real tip; no used quote updated after its snapshot stamp.
4. Missingness audit of the NULL-market rate by realised-minutes bucket (DNP, <5, 5-20, >20), max gap <= 2 pp (the market is a property of the
   game, so it cannot know minutes; verified).
5. Planted tests on the feature build: a tip-30 and a tip-54 snapshot are refused, a tip-55 snapshot is taken (not inert); odds edits and score
   edits on/after 2024-01-15 leave earlier games identical and change later ones; altered same-day final scores leave that day's pace baseline
   identical; the placebo keeps the marginal, leaves no fixed points (<= 1% of rows), and carries no game information (within home/away rows
   |corr| with the actual signed margin < 0.05, real > 0.3); sign of `mkt_margin` by home/away.
6. Identical row keys and `y` across the six arm frames (asserted again per stat at scoring).

### Measured 2026-10-11 ~17:06 CT by `gate --allow-draft` (research/eval/game_market_context_eval.py; reports/prereg_game_market_context/gate.json)
Real data, read-only, season <= 2024; only A0 fitted (cached in data/ops/game_market_context_cache/). ALL SIX CHECKS PASS.
1. PASS. A0 reproduces the stored reference EXACTLY (delta 0.0 for all 8 cells; 2023 n 27,619, 2024 n 27,701; pts 3.152830 / 3.187553, reb 1.292817 /
   1.314247, ast 0.905903 / 0.900215, fg3m 0.569049 / 0.592213); A0 names equal production `stat_feature_names`.
2. PASS. Regular-season games with a usable T-60 market: 2023 100.0% (1,230 of 1,230; Pinnacle 1,181, consensus 49), 2024 99.59% (1,225 of 1,230;
   Pinnacle 1,189, consensus 36); scored-row coverage 100.0% / 99.60% (27,590 of 27,701). 2022: 0 (no market exists; training NULLs).
3. PASS. 2,633 game-market rows from 62,334 odds rows: 0 late snapshots, 0 without a real tip, 0 refused (the t60 pull is always 62.5 or more
   minutes before tip; median 64.4), 0 quotes updated after their snapshot stamp. The cutoff therefore never binds on real data; the planted tests
   are what exercise it.
4. PASS. NULL-market rate by realised-minutes bucket over all 2023-24 roster rows (n 69,902): DNP 0.17%, <5 0.42%, 5-20 0.21%, >20 0.17%; max gap
   0.25 pp (0.25 pp played-only).
5. PASS (11 of 11 plants and controls). Placebo fixed points 0.14% of rows (77 of 56,315); within-home/away |corr| of the placebo margin with the
   actual signed margin 0.018 (real 0.508); an earlier version of this test pooled home and away rows and read 0.052 because the shared home-court
   level is a function of `is_home_f` alone (the placebo is meant to keep it); the test was corrected to the within-flag form before any arm result.
6. PASS. Identical keys and `y` in all six frames.
Descriptive (informs the prior, not a rule): correlation of `mkt_margin` with `exp_margin` 0.898 (2023) / 0.895 (2024); `mkt_abs_margin` with
`abs_margin` 0.787 / 0.770; `mkt_total` with `r20_pts_for + opp_r20_pts_for` 0.736 / 0.570 (n 27,619 / 27,590).
Run-time basis: the gate fitted A0 (4 stats x 18 month blocks) in about 90 s; OWN_TAG's four-stat refit of one arm took about 100 s (stored 313 s
for three arms).

## Pass rule (per arm in {A1, A2} and stat; 2023 selects, 2024 confirms)
A cell PASSES only if ALL of:
(R1) dCRPS (arm - A0) <= -0.005 with game-clustered 95% CI upper < 0 and BH p < 0.05 (family of 8 per season) in BOTH 2023 and 2024, where
     2023 is judged on its mature-block rows (test blocks with >= 600 market-bearing training games: Feb-Jun 2024, 12,757 rows; p-values and BH
     computed on those rows) and 2024 on all scored rows (pre-freeze amendment AM1, 2026-10-11 17:17 CDT);
(R2) the arm beats its placebo (A1 - P1a, A2 - P1b) with CI upper < 0 in BOTH seasons;
(R3) 2024 guards: |bias| <= 0.5; 80% coverage in [0.75, 0.85]; no guard slice (starter, bench, first15) with n >= 300 worse than +0.01;
(R4) over/under: the DraftKings T-60 log-loss difference (arm - A0) has point estimate <= +0.002 in BOTH seasons (CI reported; this is a
     non-inferiority bar on the classification view, not a requirement to win it);
and the gate passed. A pass makes the cell a shadow candidate (logged live beside production); promotion is Devin's. The descriptive A3 never
passes or fails anything.

## Kill criteria
No cell passes R1 on 2023 mature rows: closed (the all-row 2023 result is reported beside it but does not decide; pre-freeze amendment AM1, 2026-10-11 17:17 CDT). No re-encoding of the market columns (win probability, spread moves, T-5), no other book, no retune after a result. One follow-up at
most, and only the live season: the market context captured live by the new closing capture (`NBA_CLOSE_CAPTURE`) under its own id and rule.

## What each choice protects against
- Pinnacle else consensus, T-60 only, tip - 55: a closing line or a late quote would carry lineup news the T-60 props model cannot have; the
  market is the highest-information pre-game number we can get, so any gain must come from information, which is why the cutoff is asserted per
  row and planted-tested both ways.
- Placebo within date: the market columns also change column count and subsampling; a gain that survives only against A0 and not against shuffled
  market numbers is an architecture/noise effect. Derangement + within-flag correlation test: a placebo that keeps its own game's numbers is the
  real arm in disguise.
- Replace (A1) AND add (A2): A1 tests "move away from proxies" literally; A2 asks whether the market adds to the proxies. Both are in the BH family.
- Same-run A0 for over/under: stored `p_model` predates the data clean-up; a fair baseline uses the same rows and the same refit.
- R4 as non-inferiority: Devin asked for the classification view; log loss at the book's own line is the decision metric downstream, so a CRPS gain
  that hurts it is not a gain; the +0.002 allowance (chosen before any arm result, not tuned) keeps the bar a non-inferiority guard rather than a second win condition.
- Game-clustered CI: the market columns are shared by every row of a game; row-level CIs were anti-conservative.
- Reporting all-rows AND market-rows: the ~0.4% missing market games must not silently flatter or punish A1.

## Outputs (exact)
`reports/prereg_game_market_context/results.json`, `reports/prereg_game_market_context/ledger_rows.md` (T### placeholders),
`reports/prereg_game_market_context.md`; code under `research/` only (`research/eval/game_market_context_eval.py`,
`research/features/game_market_context.py`). Cache: `data/ops/game_market_context_cache/` (A0 cached from the gate).

## Pre-freeze amendments (labelled; made after the gate measure, before any arm result)
- AM1 (2026-10-11 17:17 CDT): R1 for 2023 is evaluated on the mature-block rows (>= 600 market-bearing training games in the training window, Feb-Jun 2024, 12,757
  rows), with p-values and the BH family of 8 computed on those rows; 2024 is unchanged (all blocks mature). The kill reads "no cell passes R1 on
  2023 mature rows". The all-row 2023 numbers stay in the report. Why: Oct-Jan 2023 blocks (54% of rows) are trained on at most 480 market
  games, so an all-row 2023 close could not tell "no signal" from "too little market training data". The 600 threshold was chosen after the gate
  count of training games and before any CRPS. Code: `r1_passes`, `add_bh` (mature family), unit-tested.
- AM2 (2026-10-11 17:17 CDT): proposed (R4 as CI upper bound) and REJECTED by the main session; R4 stays a point-estimate non-inferiority (<= +0.002), CI reported.
- Confirmed unchanged: A1 replaces exp_margin/abs_margin/r20_pts_* and drops opp_r20_*; R2 required in both seasons.

=== RESULTS BELOW ===
Run 2026-10-11 17:19-17:27 CDT under frozen sha256 fd37a3f3, git 0a4f230 (results written 17:28 CDT), harness research/eval/game_market_context_eval.py,
rc 0. Gate 20/20 re-run inside `run`. Full tables: reports/prereg_game_market_context.md, reports/prereg_game_market_context/results.json.
Verdict: CLOSED, no cell passes R1 on 2023 mature rows (nor on all rows, nor on 2024). A1 (market replaces Elo/r20) vs A0, dCRPS: pts
+0.0033 [+0.0002, +0.0064] / 2024 +0.0005; reb +0.0025 [+0.0010, +0.0041] / −0.0007; ast −0.0002 / −0.0002; fg3m −0.0007 / +0.0002. A2
(market added) pts +0.0052 / −0.0023 [−0.0046, −0.0000]; reb +0.0021 / −0.0001; ast +0.0005 / +0.0000; fg3m −0.0000 / +0.0000. Every real
arm is within ±0.001 of its within-date-shuffled placebo (R2 fails everywhere), so the market columns carry no game information the trees
use beyond what the proxies already give (market margin correlates 0.90 with exp_margin). R3 guards hold; R4 (DK over/under log loss not
worse than +0.002) holds in every cell, with fg3m slightly better (−0.0011 to −0.0012 in 2023). A3 (no own-stat history; "predict points
without viewing points"): pts +0.42 CRPS (3.58 vs 3.15), reb +0.17, ast +0.19, fg3m +0.10, both seasons: the player's own history is worth
about 13% of CRPS and nothing in the market or team context replaces it. Closed; one live-season follow-up allowed under a new id with the
real closing capture.

