# ALT_LINE_70: is "over at P >= 0.70 on every line" calibrated, and can it be profitable on $20 a week? (pre-registration)

Status: **DRAFT, written by the modeler Wed Oct 14 12:32 CDT 2026 (clock read by `date`), branch `agent/modeler-alt-line-70`. Not frozen. No hit rate, return, EV-realised or any other outcome number has been computed for this rule.** The only numbers below are the minimum-data gate (counts of rows, prices and model probabilities; no `y` column is requested by the gate code, asserted in `research/eval/alt_line_70_gate.py` and its test).
- **How it becomes the rule:** the main session reads it with Devin, commits `prereg(ALT_LINE_70): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ALT_LINE_70.md | shasum -a 256`. No harness exists yet; it is written only after the freeze and must refuse to run without that commit.
- **Advice or momentum:** Devin's own question (2026-10-14: "at 70% over for all alt lines, can you be profitable, given $20 a week?"). It is not a Claude recommendation; Claude's standing advice (CLAUDE.md) is "keep your money until the live log proves skill", so this rule is built to make a clean "no" cheap to reach and a "yes" hard to fake.
- **Selects nothing.** The strategy is fixed by Devin: over side, P(over) >= 0.70, every line. There is no select season. Seasons 2023 (2023-24) and 2024 (2024-25) are both REPORT seasons, read once each; 2025 is never loaded and never earns a touch under this rule (`season <= 2024` asserted before any read).
- **Model class:** none is fitted. The object is a stored forecast layer (the production context-residual quantile forecast, a gradient-boosted residual model with a 199-point integer-support distribution) tested for calibration at a decision threshold, plus a betting-policy evaluation at posted prices. Playbook classes: "Quantile and distribution layers" and "Gradient-boosted trees"; see the Playbook check.

## What the question is, and a correction to the brief
Devin's brief said the stored odds hold main player lines only, with no alternate ladders. **That is partly wrong, and the correction changes Part 2.** From `data/odds/odds_history.duckdb` (table `odds_history`, snapshot `t60`, two-sided half-point lines, seasons 2023 and 2024; `reports/prereg_alt_line_70/gate.json`):
- Two Kambi-feed books carry real multi-line ladders with both sides priced. BetRivers points: 98.8% of player-games have >= 3 distinct half-point lines in 2023 (mean 6.94 lines; example, one player-game: 25.5 to 34.5 in unit steps) and 64.1% in 2024 (mean 3.64). Shallower ladders exist for the other stats: the share of BetRivers player-stat-games with >= 2 lines is rebounds 85% / assists 62% / threes 59% in 2023 and 26% / 24% / 18% in 2024 (the share with >= 3 lines is only 26%, 8%, 2% in 2023 and 8%, 4%, 1% in 2024). Unibet US is a copy of BetRivers in 2023 (94.9% identical over prices on 3,060 shared eligible rungs) and is absent in 2024. The exchanges Novig and ProphetX (2024 only) post a second line in 4-18% of player-stat-games and fail the ladder definition (>= 10% with >= 3 lines).
- DraftKings, FanDuel and Pinnacle post exactly one line per player-stat-game; the other books are at or near one. The ladders span roughly +-5 points around the main line, not the deep "N+" milestones.
- The store has two snapshots per game (t60, stamped 62.6 to 79.4 minutes before tip in 2023 and about 64 in 2024, and t5), not prices "over the day".
- **Plain statement for Devin:** true deep alternate-line ladders (the "20+ points" style milestone markets) are NOT in our data. What we hold is main lines at 12 or more books, plus partial ladders at one Kambi book (deep for points, two to three lines for the other stats). Part 2 tests the posted lines we hold, with the ladder rungs as its core, and cannot speak for lines no stored book posted.

## Part 1: calibration at P >= 0.70 (no prices)
**Question.** Among all half-point lines whose stored model P(over) is at least 0.70, does the hit rate equal the mean predicted P?

**Object and units.**
1. Forecast: `reports/odds_history/model_rows_{2023,2024}.parquet`, the production walk-forward context-residual forecast as stored (month blocks, trained strictly before each block, real-tip report gating, integer support; its A0 check equals the LOWER_TAIL production arm to 1e-6). `p_ge[k-1] = P(Y >= k)`, k = 1..K (K is the row length; pts 80-84, reb 35, ast 30, fg3m 18-19). Stats: pts, reb, ast, fg3m. No refit, no recalibration, no map.
2. Row universe: player-games with minutes > 0 (all stored rows; 0 rows have null or non-positive minutes). So P is conditional on playing, exactly as a sportsbook voids a DNP. Stated, not hidden.
3. Unit: a **line-row** = (player-game, stat, k) with line L = k - 0.5, P = P(Y >= k) on the 1/199 grid, hit = 1 if Y >= k. Eligible iff P >= 0.70 (tolerance 1e-12). Every k in the support is a line; no market is needed. Many lines have P = 1.0 (line 0.5 for a starter); they are counted and reported (pts 26,842 / 27,557 in 2023 / 2024), they sit in the 0.95+ bin.
4. Reliability bins: [0.70,0.75), [0.75,0.80), [0.80,0.85), [0.85,0.90), [0.90,0.95), [0.95,1.0], and the sub-row P = 1.0 inside the last.
5. **Headline band [0.70, 0.90).** Why: above 0.90 a posted price is -900 or shorter, where one point of probability is worth under 1.2% of ROI and the book's overround (8.5% to 9.2% at the eligible BetRivers rungs, gate) dominates; the 0.95+ bin is mostly lines no book posts. The six-bin table is still reported in full.
6. Distance from the main line (model ruler, defined from the forecast only so Part 1 stays price-free): the **main line** of a player-game-stat is the half-point line whose P is closest to 0.5 (ties to the lower line); distance d = (main line - L) / u with u = 2 points for pts and 1 for reb, ast, fg3m. Bins: (-inf,0], (0,1], (1,2], (2,4], (4,inf). Reported inside the headline band. A bin with < 1,000 line-rows or < 300 games is INSUFFICIENT (no word). The market ruler (distance from a book's main rung) is defined in Part 2 and applies only there.

**Statistic.** gap = (hits / n) - mean(P) over the line-rows of a cell, a ratio estimate weighting every line-row equally. Reported with n line-rows, n player-games, n games, hit rate, mean P, gap, and for every bin the break-even price at the claimed P (decimal 1/P, American equivalent) and at the realised hit rate (decimal 1/h): the price at which that rung type is fair under each belief.

**CI.** Game-clustered percentile bootstrap, B = 5,000, `numpy.random.default_rng(2026)`, resampling `game_id` with replacement, one resample matrix per season shared by every cell (lines within a player-game are nested, players within a game share the game, so the game is the right cluster). A date-clustered bootstrap (resample `game_date`, same B, `default_rng(2027)`) is run beside it. **The decision interval is the union: lower = min of the two lowers, upper = max of the two uppers.** Two-sided p = 2 min(P(b <= 0), P(b >= 0)) with a +1 correction, from the game bootstrap.

**Decision words (per stat x season headline cell; verbatim).**
- **CALIBRATED:** |gap| <= 0.02 AND the decision interval lies inside [-0.03, +0.03].
- **OVERCONFIDENT:** gap < -0.02 AND interval upper < 0 AND BH q < 0.05.
- **UNDERCONFIDENT:** gap > +0.02 AND interval lower > 0 AND BH q < 0.05.
- **INCONCLUSIVE:** anything else (including a wide interval).
BH is over the 8 headline cells (4 stats x 2 seasons). CALIBRATED is an equivalence statement and has no p-value. This differs from the sketch Devin gave ("within +-0.02 with the CI covering the mean"): a CI that must contain exactly 0 turns a practically irrelevant but detectable 0.8-point gap into "not calibrated" at n of 76,000 lines, and a wide CI that happens to contain 0 would pass; equivalence inside +-0.03 avoids both. 0.02 of probability is about 2.7% of ROI at the BetRivers median decimal price 1.36, under a third of the overround.
- **Stat word:** the same word in 2023 and 2024, else MIXED.
- **Model word: "calibrated at 70%+"** iff all four stat words are CALIBRATED. If any stat word is OVERCONFIDENT the model word is "OVERCONFIDENT at 70%+ for <stats>". Otherwise "NOT SHOWN calibrated" with the list.
- Bin-level and distance-level words use the same definitions, are descriptive, and carry a BH q over their own family (6 bins x 4 stats x 2 seasons = 48) only if anyone quotes one directional bin.

**Descriptive decomposition (no word).** The same gap for line-rows that BetRivers posted at T-60 (the Part 2 set) against the remainder: separates "the model is miscalibrated at 70%+" from "the lines a book posts near the main line are where the model is overconfident" (CALIBRATED_GRID: the grid is calibrated overall and overconfident exactly at the book's line).

**What Part 1 cannot say.** It is not a profit claim and not advice. It covers lines no book posts. A CALIBRATED word at 70%+ says nothing about whether a bettor beats a price.

## Part 2: profit at posted prices on $20 a week
**Scope.** Posted lines in the stored store, BetRivers as the fixed book. Because ladders are partial (see the correction above), the BetRivers set has two kinds of rung: ladder rungs (deep for points; two to three lines for the other stats) and single main lines (mostly reb, ast, fg3m). Its eligible rungs are 76% points in 2023 and 65% in 2024 (gate). The result is therefore mostly a points-ladder result plus main-line disagreements, and is reported by stat.

**Bet set A (primary).** In order; each exclusion is counted and reported:
1. Source: `odds_history`, `snapshot_kind = 't60'`, `book = 'betrivers'`, markets player_points / rebounds / assists / threes, `player_id` not null, seasons 2023 and 2024.
2. Two-sided: both over and under priced at the same half-point line in the same snapshot; whole-number lines and one-sided quotes are dropped (counted; no pushes can occur).
3. As-of: `snapshot_ts <= real tipoff_utc - 60 minutes` for 100% of rows (asserted; tip from `data/ops/model_clv_pairs` which carries the real tip). Any violation stops the run.
4. Join: to the stored model row on (game_id, player_id, stat) for the same season; no model row (DNP or unmatched) is counted as `void_or_unmatched` and excluded (a book voids a DNP; the model rows are played-only). P = P(Y >= point + 0.5).
5. Eligible iff P >= 0.70. One row per (game, player, stat, point); duplicates asserted absent.
6. **Side and amount:** the over price only; flat stake; decimal odds d from the American price; profit = d - 1 on a win, -1 on a loss (stake units). No pushes. No fee beyond the priced overround (as DECISION_SCORING: a sportsbook has no explicit fee, `fee = 0.0` and the source string says so). No promotions, boosts, taxes or state fees are modeled.

**Why BetRivers, fixed, and not "best price".** (a) It is the only book with ladders in both seasons; Unibet US is the same Kambi feed (94.9% identical prices) and counting it would double one source. (b) A fixed book is what a $20 bettor has; best-price shopping across 12 books picks outlier quotes, is a winner's-curse selection, and needs many accounts. (c) The brief asked for "one bet per player-game-stat-line"; with one book that is automatic. **Arm B (secondary, descriptive, never decides):** the same eligibility on the best over price across all books at that exact line (highest decimal, ties to the alphabetically first book). It exists so a reader can see what line-shopping would change.

**Staking and the $20 budget (the plan).**
- Stake **$2 flat per bet, at most 10 bets per week = $20 a week**, never more. The week is the Monday-start week of `game_date` (as stored, Eastern date). Unspent budget does not carry over. Winnings are not restaked.
- Why this size: at the median decimal price 1.36 a $2 win pays $0.72 and a loss costs $2.00; ten bets keep one week's worst case at -$20 and avoid concentrating the budget on one correlated slate. Larger stakes with fewer bets only change variance.
- Selection when more than 10 units are eligible in a week: a **unit** is (game, player, stat). Draw up to 10 units uniformly without replacement, then one rung uniformly within each unit, so nested rungs of one player are never stacked. Seeded `default_rng(2026)`, R = 2,000 independent plan draws per season. If a week has fewer than 10 units, bet all of them and spend less.

**Estimands and CIs.**
- **E1 (decision): rung ROI** = sum(profit) / n over ALL eligible rungs of arm A, each at one unit, pooled over stats and over the two seasons (resampling stratified by season, each season's clusters drawn within season). By stat and by season reported. E1 does not depend on the weekly cap; it is the property of the strategy "every eligible line".
- CI: game-clustered and date-clustered percentile bootstrap, B = 5,000 each (`default_rng(2026)` and `default_rng(2027)`), ratio estimator on cluster totals. **Decision interval = union**, as in Part 1. Why both: lines within a game share the outcome, and a whole night shares injuries and a scoring environment, which game clustering ignores.
- **E2 (plan dollars, descriptive):** the dollar path of the $2 x 10 plan. Per season over the R plan draws: total staked, final profit (median, 5th and 95th percentile), P(final > 0), median and 95th-percentile max drawdown of the weekly cumulative path, share of weeks with a loss, longest run of losing weeks. These distributions reflect only which bets the plan picks; outcome uncertainty is carried by the E1 interval, and the plan's dollars are reported as ROI interval x total staked. Context fixed now, from model-claimed inputs only: with a per-bet sd about 0.59 (d sqrt(P(1-P)) at P 0.75, d 1.36), 300 bets a season (30 weeks x 10) give a season-profit sd of about $2 x 0.59 x sqrt(300) = $20, against an expected +$24 even if the model's claimed +4% ROI were true. A single season of $20 weeks cannot, by itself, tell skill from luck; that is why E1 pools every eligible rung.

**Decision words (pooled E1, both seasons; verbatim).** Let lo and hi be the decision interval of pooled ROI.
- **PROFITABLE:** pooled ROI >= +0.02 AND lo > 0 AND the point ROI is > 0 in each season separately.
- **NOT PROFITABLE:** hi < +0.02 (the data rule out the practical floor).
- **INCONCLUSIVE:** otherwise.
A PROFITABLE word makes arm A a candidate for the forward log only; money, staking and promotion are Devin's (CLAUDE.md non-negotiable 6). It is not advice. By-stat words are descriptive; a stat-level claim needs BH q < 0.05 over the 4 stat cells and the same rule. Arm B, the comparators and E2 never decide. Floor +0.02 (not 0): at $600 staked a season, 2% is $12, below the sd of one season's P&L, so it is the smallest ROI worth the word; the CI lower bound > 0 is Devin's example criterion and is kept.

**Break-even price at each P (reported, from Part 2's set).** Per P bin: n rungs, mean claimed P, realised hit rate with its interval, mean decimal price paid, break-even decimal at the claim (1/P) and at the realised rate (1/h) with American equivalents, ROI at the claim (mean of P d - 1, what the model says) beside realised ROI. This is the table that answers "at what price would 70% over be worth it": it shows the price needed against both the claimed and the realised hit rate.

**Comparators (same n, same CI method; descriptive, not gates).** C1 market lean: BetRivers over rungs whose own de-vigged over probability is >= 0.70, model ignored (does the model add anything over "bet the favourite rungs"); C2 random rung of the same count (recovers about minus the overround, the vig baseline). Market ruler: for each BetRivers player-stat-game, the main rung is the one whose de-vigged over probability is closest to 0.5 (ties to the lower line); ROI is also sliced by distance from it in the units of Part 1.

**Attacks, run only if the word is PROFITABLE and before anyone believes it:** (i) planted-future (shift each model P to a different player-game of the same stat; ROI must fall to the C2 level); (ii) shuffle P within (game, stat) among BetRivers rungs, 50 seeds, ROI must not clear the interval; (iii) leave out the best 5% of bets and the best week; (iv) arm B and the Unibet replicate must have the same sign (the replicate is not independent evidence); (v) a missingness audit on `last_update` and quote staleness; (vi) sensitivity to the 8.5-9.2% overround: ROI minus the C2 baseline.

**What Part 2 cannot say.** One snapshot (T-60) per game; whether a real bettor could place that price at that minute, the limits, and BetRivers' availability in Devin's state are unknown; deep alternates and the "N+" ladders are not in the data; the 2025-26 season is untouched. It tests the stored posted lines, not "all alt lines".

## Kill criteria and what a result opens
- Part 1: any word is a finding and ends Part 1; there is no recalibration follow-up under this id (LINE_CALIBRATION owns recalibration). No re-cut of the threshold 0.70, the band, the bins, the ruler or the margins after the result; "P >= 0.75", the under side or a different book are new ids.
- Part 2: NOT PROFITABLE closes the stored-line question; INCONCLUSIVE is reported as inconclusive (it is not rescued by adding arm B, a season or a book). The one allowed follow-up is a forward rule on true alternate ladders (follow-up 1), new id, Devin's go. PROFITABLE triggers the attack list, then a forward shadow candidate, never a bet.
- Season 2025 is never touched under this rule; a 2025 touch would be one logged row for a new id and is Devin's decision.

## Follow-ups (each a new id and each needs Devin; neither is part of this rule)
1. **Forward capture of alternate markets from opening night.** `configs/odds_live.yaml` already lists a `player_alternate` group (`player_points_alternate`, `player_rebounds_alternate`, `player_assists_alternate`, `player_threes_alternate` and seven more). It is marked `alternate: true`, which `docs/CLOSING_LINE_CAPTURE.md` says is dropped first if the schedule exceeds the 50% credit rule, and the plan after this month is Devin's decision (the 5M plan is cancelled after the month; the smallest plan that fits the full schedule is START 100K at $59/month, per that doc). Needed: confirm the group is enabled and kept, then freeze the forward rule on the 2026-27 log. This is the only clean test of true ladders.
2. **Historical purchase of alternate markets** (The Odds API historical, `player_*_alternate`, `us` region, t60 for 2023 and 2024). `docs/research/historical_odds_2026-10-09.md` costs "alternates, 4 seasons, 3 snapshots" at about +480k credits; scaled to 2 seasons at one snapshot that is roughly 80k credits (my arithmetic, not a vendor quote; budget state and plan status not verified here). It would give the deep rungs and many books. Vendor, plan and spend are Devin's.

## Minimum-data gate (non-negotiable 8), measured Wed Oct 14 2026 12:37 CDT (final run; `gate.json` sha256 9b37c931d34206dbdbbb0dc8563d0ce23061ba216a0344bc6ffb1a316cdaa00a, identical to the run before the formatting pass)
Code: `research/eval/alt_line_70_gate.py` (counts only, no `y`), output `reports/prereg_alt_line_70/gate.json`, 6 s, read-only (DuckDB `read_only=True`, season <= 2024). data_version = these input files by sha256 (the repo's snapshot manifests were not re-cut for this draft):
`reports/odds_history/model_rows_2023.parquet` 002e53ef1a5f..., `model_rows_2024.parquet` ec47dcdf566e..., `data/ops/model_clv_pairs/pairs_2023.parquet` 050dba3e250d..., `pairs_2024.parquet` 560a3808d7d9..., `data/odds/odds_history.duckdb` 2bf7bb813de8... (full 64-hex in the json). Equivalence: four scratch scripts written first (Part 1 counts, Part 1 cell widths, Part 2 per-book counts, Part 2 pooled arms) produce the same counts as the committed script.

**Gate thresholds (no outcome has been read; the thresholds were set after the counts were seen, disclosed in the unstated details):** Part 1 headline cell needs >= 300 games, >= 5,000 line-rows and a conservative interval half-width <= 0.03; Part 2 pooled arm A needs >= 1,500 rungs, >= 300 games and a conservative ROI half-width <= 0.05. The **conservative half-width** assumes every row of a game is perfectly correlated with the others of that game: 1.96 sqrt(p(1-p)) sqrt(sum_g n_g^2) / N for hit rates, and 1.96 sqrt(sum_g (sum_i d_i sqrt(P_i(1-P_i)))^2) / N for ROI (bet sd d sqrt(P(1-P)) from the model's own P). The true half-width lies between it and the independent-bets value. 0.05 for ROI is the largest half-width at which "NOT PROFITABLE" (hi < +0.02) is still reachable for a point ROI of -0.03, a vig-sized loss.

**Part 1: PASS 8 of 8 headline cells** (all rows played; model rows 27,619 in 2023 and 27,583 in 2024 per stat; 1,318 and 1,315 games). Line-rows with P >= 0.70 (headline band [0.70,0.90) in brackets), player-games with any, and the conservative half-width of the hit rate in the band:

| Season | Stat | Line-rows P>=.70 | Band rows | Band games | Band half-width | P=1.0 rows |
|---|---|---|---|---|---|---|
| 2023 | pts | 200,430 | 76,170 | 1,318 | 0.0215 | 26,842 |
| 2023 | reb | 73,150 | 32,385 | 1,318 | 0.0216 | 9,247 |
| 2023 | ast | 38,886 | 16,848 | 1,318 | 0.0221 | 4,307 |
| 2023 | fg3m | 13,672 | 8,987 | 1,318 | 0.0225 | 118 |
| 2024 | pts | 199,996 | 76,389 | 1,315 | 0.0215 | 27,557 |
| 2024 | reb | 74,011 | 31,871 | 1,315 | 0.0216 | 7,809 |
| 2024 | ast | 40,176 | 18,304 | 1,315 | 0.0217 | 4,529 |
| 2024 | fg3m | 15,566 | 10,371 | 1,315 | 0.0221 | 150 |

The six-bin cells: >= 1,060 games each and conservative half-width 0.006 to 0.0296 (the widest is fg3m 0.70-0.75 in 2023); all within 0.03, so every bin cell is readable. Rows per 0.05 bin run from 2,062 (fg3m 0.80-0.85, 2023) to 95,137 (pts 0.95+). Distance bins inside the band that are INSUFFICIENT (< 1,000 rows or < 300 games): pts (4,inf) (876 and 494 rows in 2023 and 2024), reb (4,inf) (51, 62), ast (2,4] (499, 513), ast (4,inf) (empty), ast (-inf,0] (5 rows in 2024), fg3m (1,2] (132, 110), fg3m (2,4] and (4,inf) (empty), fg3m (-inf,0] (55, 66). The distance table is therefore informative mainly for pts (0,1] to (2,4], reb (0,1] to (2,4], ast (0,1] and (1,2], and fg3m (0,1].

**Part 2: arm A pooled PASSES; arm A in 2024 alone FAILS.** BetRivers, t60, eligible (P >= 0.70) two-sided half-point rungs (0 of the 673,771 / 494,029 two-sided half-point rungs had a snapshot after tip - 60 or no real tip; the json also holds 674,051 / 494,031 two-sided rungs before the half-point filter and 664,389 / 484,607 joined to a model row):

| Season | Rungs | Units | Games | Dates | Weeks (>= 10 units) | Median units/week | By stat (pts/reb/ast/fg3m) | Median decimal (min-max) | Overround | Cons. ROI half-width | Indep. half-width |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 3,070 | 2,392 | 947 | 197 | 35 (30) | 75 | 2343/228/234/265 | 1.36 (1.27-2.75) | 9.17% | 0.0489 | 0.0216 |
| 2024 | 1,207 | 1,026 | 460 | 135 | 32 (15) | 8.5 | 790/120/143/154 | 1.42 (1.30-2.35) | 8.46% | 0.0751 | 0.0360 |
| pooled | 4,277 | 3,418 | 1,407 | 332 | | | 3133/348/377/419 | | | 0.0410 | 0.0185 |

Pooled half-widths combine the two seasons' cluster sums (cons. 0.0410, indep. 0.0185). Gate: pooled 4,277 rungs >= 1,500, 1,407 games >= 300, 0.041 <= 0.05: **PASS**. 2023 alone 0.0489 <= 0.05 passes narrowly; **2024 alone 0.0751 > 0.05 fails**, so no 2024-only statement is made and the rule is decided on the pooled estimate with a per-season sign check (a stated consequence of the gate, written into the decision words above). Mean model P of eligible rungs 0.7487 (2023) and 0.7508 (2024); the model-CLAIMED mean EV at the BetRivers price is +5.3% (2023) and +10.3% (2024) (not a realised return; it is what the model says, and the known overconfidence at posted lines, FAN_KNOWLEDGE, makes it the number to distrust). In 2024 only 15 of 32 weeks have 10 or more eligible units, so the $20 plan is under-spent in half the weeks.

**What the pooled interval can and cannot decide.** Under the conservative half-width 0.041: PROFITABLE needs the pooled point ROI to be above about +0.041, and reaches 80% power only at a true ROI near +0.06; under the independent value 0.0185 the same bars are +0.019 and +0.027. NOT PROFITABLE is reached when the point ROI is below about -0.021 (conservative) or +0.0015 (independent): a hit rate 5 points under the claim (about 0.70 at the claim 0.75, ROI about -0.04 to -0.06) lands there. The INCONCLUSIVE zone is therefore a point ROI between about -0.02 and +0.04.

**Arm B (descriptive):** pooled 7,015 rungs, 2,016 games; 2023 4,105 rungs (cons. half-width 0.0477) and 2024 2,910 (0.0552); eligible rungs are the BetRivers set plus other books' main lines, so claimed EV is +9.6% and +15.4%.

**Data-capture audit (BEST_PRACTICES, 2026-10-12).** Used: every stored price season we hold that is not the holdout: `odds_history.duckdb` seasons 2023 and 2024 (the store also holds 2025 rows, not read by this rule); every stored model row with a full-support `p_ge`, seasons 2023 and 2024. Exists but not used, and why: (a) 2025 prices and forecasts, the frozen holdout, no touch; (b) the 2022 walk-forward OOF (PROJECT_STATUS: OOF 2022-24) has no `p_ge` store (it needs the heavy `model-rows` stage) and has no prices in the odds store (odds start 2023-05); extending Part 1 to 2022 would add rows nobody needs, since the gate passes 8 of 8; (c) the history seasons 2019-21 have no prices at all; (d) true alternate ladders: not held, purchasable (follow-up 2, estimate above) or capturable forward (follow-up 1). Repairable gaps: the 2024 BetRivers ladder thinning (64.1% of points player-games against 98.8% in 2023, and Unibet gone) is a vendor/book coverage change, not a repairable loss; no re-pull of the existing t60/t5 snapshots would add lines. Cost of the only real repair: follow-up 2.

## Hypothesis and prior (stated before any result; also drafted into the predictions ledger)
- Part 1: the stored grid is overconfident somewhere at 70%+ because prior work shows claims of 75% hitting 61% at the books' lines and 0.74 claimed becoming 0.56 after every map (CALIBRATED_GRID, FAN_KNOWLEDGE 2026-10-11), while the grid is calibrated overall. Unconditional rungs far from the main line should be better behaved than lines at the main line. P(model word is "calibrated at 70%+", all 8 cells CALIBRATED) = **0.20**. P(at least one stat OVERCONFIDENT in both seasons) = **0.55**. Expected headline-band gap for pts about -0.015 (80% -0.05 to +0.015).
- Part 2: P(PROFITABLE) = **0.03**, P(NOT PROFITABLE) = **0.60**, P(INCONCLUSIVE) = **0.35**, P(gate or integrity stop) = **0.02**. Expected pooled E1 ROI about **-0.06** (80% -0.15 to +0.03). Why: the overround at the eligible rungs is 8.5-9.2% and the model's claimed edge (+5 to +10%) rests on the confidence that has not survived at posted lines; even a perfectly calibrated model earns only its claimed 5-10% before any shrinkage.
- Honest answer to Devin's question, as a prior: no, not on this evidence and not at $20 a week; what the data will add is the size of the miss.

## Lineage (playbook question 4)
Ancestors: ODDS_HISTORY (props skill vs the market; model rows come from its `model-rows` stage), LINE_CALIBRATION and CALIBRATED_GRID (calibration at the line; maps could not fix confident bins), DECISION_SCORING (ROI at the actual price, EV at threshold 0, game-clustered CI, market-lean and random comparators), MODEL_CLV (one-snapshot pairing, 2023/2024 only). Guards carried forward: game clustering plus date clustering, integer-support scoring, DNP excluded identically (void), real-tip as-of gating (asserted), 2025 never loaded, BH over declared families, no rounding or recalibration step. Guards dropped, because nothing is fitted: shuffled-label and permutation-null guards (replaced by the post-hoc attack list, which only runs on a PROFITABLE word); era-embedding probes (no neural arm).

## Playbook check (docs/MODEL_PLAYBOOK.md)
Model class: stored quantile/distribution layer from a gradient-boosted residual model, tested for calibration and priced; nothing is trained.
1. **Information or capacity?** Neither. No new pre-tip information and no new model; the rule tests an existing forecast at a new decision point (an over bet at P >= 0.70). The record says information at the line is already in the price, so no edge from capacity is expected.
2. **Enough data for this class?** Part 1: yes (1,318 and 1,315 games, tens of thousands of rungs per stat). Part 2: only pooled (4,277 rungs); 2024 alone is too thin (gate). No learning curve applies (no fit).
3. **Test validity?** Real tip gating (`tipoff_utc` from the real tip table; the forecasts were built with report cutoff >= 60 minutes before real tip), integer support, walk-forward blocks, played-only rows with void-equivalence stated. Risks listed, not hidden: a single T-60 snapshot, stale or non-actionable quotes, limits, book availability, one fixed book; the forecast's own validity rests on the ODDS_HISTORY model-rows stage (A0 equal to LOWER_TAIL). A guard that could fire by chance: the CALIBRATED equivalence margin is sized against the conservative half-width (0.022), not from a permutation null, because no null model is fitted here; a permutation null belongs to the attack list. No inert shuffle is used as a gate.
4. **Lineage and inherited guards:** above.
5. **What it scores against:** calibration of a probability at a threshold (a reliability question) and ROI at the posted price, not CRPS. Our CRPS gains have not reached the line (T477; line log loss worse than a coin flip before recalibration), so a CRPS-good forecast is not presumed calibrated at 70%+.
Class checks (quantile layers): the reliability table is at the point that matters (P >= 0.70, bins and distance slices fixed here), per-slice data volume is gated, and the slices are fixed in advance.

## What each choice protects against
| Choice | Protects against |
|---|---|
| Equivalence margin +-0.03 with a point limit +-0.02 (no "CI must contain 0") | calling a detectable but trivial gap "not calibrated" at n = 76,000, or a wide CI passing by accident |
| Headline band [0.70,0.90) | the 0.95+ bin (mostly unpostable lines at -2,000) drowning the pooled number in trivial hits |
| Game plus date clustering, union interval | anti-conservative row-level CIs (BEST_PRACTICES 3); same-night correlation game clustering misses |
| Fixed book BetRivers, t60 | winner's-curse best-price selection; double-counting the Kambi twin; a snapshot that could post-date what the model knew |
| Rung ROI (E1) decides, plan dollars (E2) describe | a $20 plan's season P&L (sd about $20) being read as evidence |
| Floor +0.02 with lower bound > 0 and a per-season sign | a significant but practically empty ROI; one lucky season |
| Unit-then-rung weekly draw | stacking nested rungs of one player into one "diversified" bet |
| Void/unmatched counted, played-only universe | bias from conditioning on playing: a book returns the stake, so ROI per settled stake is unbiased |
| Arm B and comparators never decide | line-shopping flattering the result; a model effect confused with "bet the favourite" |
| Attacks only on PROFITABLE, planted-future first | a too-good result being believed (opp_adjusted_ridge precedent) |
| 2025 never read | a holdout spent on an exploratory question |

## Unstated details fixed before any scored number
- Hit definition: Y >= k for the line k - 0.5; Y is the stored realised stat in the model-rows `y` column, read only by the scoring harness after the freeze.
- P is on the 1/199 grid; P = 1.0 means the stored distribution puts no mass on a miss. No clipping is applied for any hit-rate or ROI computation; a log-loss column, if ever shown, would use the ODDS_HISTORY clip 0.5/199 and is not a decision input.
- Week = Monday-start week of `game_date` (polars `truncate("1w")`), not the snapshot date.
- Ties in best price go to the alphabetically first book; ties in the main line (closest to 0.5) go to the lower line.
- The gate thresholds (0.03 and 0.05 half-widths, 300 games, 1,500 rungs) were fixed after the counts were seen (the counts are not outcomes); the thresholds are justified above and would not have changed with the counts.
- "Cons. half-width" uses the model's own P for the bet variance; it is not an outcome estimate.
- The equivalence margin and the 0.02 limit are Claude's proposal and need Devin's confirmation; Devin's sketch ("within +-0.02, CI covering it") is recorded above as the alternative.
- Season label: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (holdout, untouched).
- Plan stake $2 and cap 10 bets/week are Claude's choice given $20 a week; Devin may set another split (the strategy and E1 do not depend on it).
- `date` read Wed Oct 14 12:32 CDT 2026 when the draft was started; the gate was re-run after the last script edit (adds claimed EV and median units per week) and again after formatting, with identical output.

## Commands (maintainer; read-only)
- Gate (this draft's evidence, 6 s, writes one JSON): from a checkout that holds `data/` and `reports/`: `.venv/bin/python -W ignore -m research.eval.alt_line_70_gate --data-root . --out reports/prereg_alt_line_70/gate.json`. From the modeler's worktree (data not linked there) use `--data-root /Users/devin/Downloads/nba-prediction`. Scope read: the five input files above, nothing else; dry-run count = the tables above.
- Part 1 and Part 2 scoring: **no command exists**; the harness is written after the freeze (an entrypoint that refuses without the `prereg(ALT_LINE_70)` commit, light: numpy bootstraps over about 2 x 10^5 line-rows, no heavy.lock needed, expected well under a minute per stage).

## What Devin must sign
1. The correction (partial ladders exist at BetRivers; deep alternates do not) and therefore Part 2's design: BetRivers fixed, t60, pooled over both seasons, arm B descriptive.
2. The decision words and floors: Part 1 (+-0.02 point, +-0.03 interval, band [0.70,0.90)) and Part 2 (ROI floor +0.02, lower bound > 0, both seasons positive; NOT PROFITABLE when the upper bound is below +0.02).
3. The plan: $2 x at most 10 bets a week, no carry, random unit-then-rung selection.
4. That the result is never a recommendation to bet and that 2025 is not touched.
5. Separately, not part of the freeze: follow-ups 1 and 2 (forward alt capture; historical alt purchase), and whether Devin can actually place bets at BetRivers.

=== RESULTS BELOW ===
(empty until the frozen harness runs)
