# SHARP_WALLETS_PERSIST: do Polymarket wallets that beat the NBA closing price in one window keep beating it in the next? (pre-registration, descriptive, season 2024 only)

Status: **DRAFT 2026-10-27, modeler, branch `agent/modeler-sharp-wallets` off `main` aadcad8. NOT FROZEN. No wallet, trade,
price or market data has been read by anyone for this rule.** The only data read while drafting was the NBA schedule
`data/schedule/raw_2024-25.parquet` (sha256 215bf656…2243) to count games per window. Every gate below is therefore
**specified, not yet measured**; section 8 is a two-step procedure that measures it on selection-window data only, before the
report window is pulled.

**Advice or momentum: Devin's request** (DECISIONS 2026-10-27 18:17, "check it"). Not a model input. Claude's prior in that row
is P(persistence beats the close out of sample) about 0.15; the modeler's prior (section 12) is a little higher, for the reason given there.

**Non-negotiable 8 vs this rule's ordering (stated, not hidden).** NN8 says a gate is checked on real data before a freeze.
DECISIONS 18:17 says the rule is committed before any wallet data is read. This draft follows DECISIONS. The two-step procedure
keeps the protection NN8 exists for: the rule's gate is measured on selection-window data before anything that bears on
persistence (the report window) exists locally, and a gate failure ends the rule (INSUFFICIENT_DATA, after the NN8 capture audit in
8.4), with no re-specification. Open choice C1 offers the alternative.

Model class: **empirical-Bayes rating** (normal-normal shrinkage of a per-wallet closing-line-value score) used for a
**descriptive persistence test**. It is not a forecasting model and feeds nothing.

## Playbook check
1. **Information or capacity.** Information, of a new kind here: whether some market participants have information
   (or speed) that the closing price has not absorbed yet when they trade. No model capacity is added. This only matters later if it
   leads Kalshi prices (section 11).
2. **Enough data for this class.** A rating's data appetite is per rated unit: each wallet needs enough independent games for
   its CLV standard error to sit below the between-wallet spread (tau). Eligibility (>= 10 games, >= 20 orders) and gate A5
   (projected power at the floor) check this on the selection data before the report window is read. Unknown until step 1.
3. **Test validity.** Out of sample in time (select <= 2025-01-31, report >= 2025-02-01, by game ET date). The close is built
   from other wallets' taker fills only, so a wallet is never scored against its own trades. Real scheduled tips (schedule
   `gameDateTimeUTC`, 0 of 1,321 window games missing). Comparator is activity-matched wallets plus a permutation null of the whole
   select-then-score pipeline, so selection artifacts (regression to the mean, activity traits) are in the null. Two-way clustered
   CI (matched set x game). Power is gate A5. Known weak point: price impact (a large wallet moves the later price it is scored
   against), reported in section 7 and the slices.
4. **Lineage.** The CLV arithmetic is MODEL_CLV's (T340-T381: price CLV of a bet vs the closing price, game-clustered); new here are
   the per-wallet ratio estimator and the shrinkage. Inherited guards: real tips; clustered CI; BH families; no 2025 touch;
   permutation null sized from the actual pipeline; no variant chasing. Not inherited: integer support (no count outcome) and
   the DNP rules (no player rows).
5. **What it is scored against.** The Polymarket closing price (trade-tape mid proxy, section 3), not outcomes. Realized P&L
   against settlement is secondary, since it is much noisier per wallet. Neither is a bet result for Devin.
Class checks (rating / shrinkage): the between-wallet variance tau^2 is estimated before ranking (tau^2 = 0 stops the rule:
NO_DISPERSION); the shrinkage weights use clustered (per-game) standard errors, not per-fill ones; no tuning; the ranking cut is fixed
(top decile) before the data exists.

## Hypothesis
H1: among wallets eligible in the selection window, the top decile by shrunk selection-window CLV (S) beats the closing price in the
report window by at least **1.0 cent per $ risked** more than activity-matched wallets (M), with the two-way clustered 95% CI lower bound
above 0. H0: report-window CLV of S is no better than M (selection-window CLV was luck, or the edge did not last).

## 1. Data, windows, known-at
* **Source.** Polymarket international public Data API `GET https://data-api.polymarket.com/trades` (no key) for fills, and
  the Gamma API `GET https://gamma-api.polymarket.com/markets` (and `/events`) for market metadata. Pulled **per market**
  (`market=<conditionId>`), never per user, so step 1 can hold selection-window markets only. Devin runs the download (agents cannot
  curl), write-once to `data/raw/polymarket_wallets_<pull date>/step1_selection/` and later `.../step2_report/`.
* **Season.** 2024-25 only (`seasonYear` 2024-25 in the schedule; our `season = 2024`). Game types: regular season (`002`), the
  Cup final (`006`), play-in (`005`), playoffs (`004`). Preseason (`001`) and All-Star (`003`) are excluded.
* **Selection window**: games with ET date 2024-10-22..2025-01-31: **712 games** (711 regular season + the 2024-12-17 Cup final).
* **Report window**: games with ET date 2025-02-01..2025-06-22, **regular season + play-in + playoffs (through Finals game 7)**:
  **609 games** (519 + 6 + 84). Playoffs are included for n; "regular season + play-in only" is a slice (section 7). Open choice C2.
* **Holdout.** Season 2025 (2025-26) is not touched. The pull script refuses any market whose matched game is not in the 2024-25
  schedule above and any fill with `timestamp >= 2025-07-01T00:00:00Z`. S and M (section 4) are never queried on any later market under
  this rule.
* **Tip.** `tip = gameDateTimeUTC` from `data/schedule/raw_2024-25.parquet` (real scheduled tip). Gamma `gameStartTime` is a
  cross-check only: |gameStartTime - tip| > 15 min is counted and the schedule wins.
* **Known-at.** Each fill's time is its `timestamp` (epoch seconds; on-chain block time). **Pre-tip** fill: `timestamp < tip`.
  **Buffer**: fills in `[tip, tip + 15 min)` are excluded from everything (scheduled tip is not the actual tip; the buffer
  stops early in-play fills from counting as pre-tip or late pre-tip fills from counting as in-play). **In-play**: `timestamp >= tip + 15 min`
  and before the market's `closedTime`.

## 2. Market scope, fills, orders
* **NBA game markets.** Gamma markets tagged NBA whose event matches one window game by (ET date of `gameStartTime` or the event
  slug date, home and away team). Futures, season-long, awards, series-winner and "first to" markets are excluded (no single tip).
  A market that matches no game, or more than one, is excluded and counted (integrity I1).
* **Per type**, from Gamma `sportsMarketType` (fallback: question-text regex, and the fallback count is reported):
  game winner (`moneyline`), spread (`spreads`), total (`totals`), player props (any player stat type), other (excluded and
  counted). **Whether spreads, totals and props existed on Polymarket for 2024-25 NBA games is unverified.** Each type enters the
  pooled primary if it has any matched markets; it is a slice only if it passes the per-type gate (A6). Types that do not exist are
  reported as "absent", not as nulls.
* **Fill.** One Data API record with `takerOnly=true` (the default): the wallet `proxyWallet` was the taker. The schema has no
  maker/taker field; makers are not studied (makers' fill times are chosen by their counterparty, so maker "CLV" measures adverse
  selection, a different question). Open choice C3.
* **Dedup.** Records identical in every returned field are pagination duplicates: kept once, counted.
* **Order.** Fills with the same (`transactionHash`, `proxyWallet`, `asset`, `side`) form one order: size = sum of sizes, price =
  size-weighted mean. Counts and eligibility use orders, so one order split across several makers is not counted several times.
* **Direction (no netting needed).** A BUY of token x at price p is a long x at q = p. A SELL of token x at p is treated as a long of
  the complement token x' at q = 1 - p (economically the same exposure in a binary market). Both scores below are linear in orders,
  so a round trip scores its realized spread automatically; positions are not netted. Buys and sells are both included;
  the SELL share is reported.
* **Dollar stake** of an order = size x q (dollars at risk on the long leg).

## 3. Closing price and CLV
* **Close for wallet w on market m, token x**: the size-weighted mean price, mapped to x (a fill on the other token at p counts
  as 1 - p), of the **last 10 taker orders on m by wallets other than w** with `timestamp < tip`, provided the 10th-last of them is no more than
  60 minutes before tip. Otherwise m has no close for w: w's orders on m are excluded and counted (integrity I2). Taker prints
  alternate between ask and bid (a buy of x' at 1 - bid_x maps to bid_x), so this is a trade-tape proxy for the mid.
  A pre-tip book mid is not used because the CLOB `prices-history` for resolved markets is reported to be available only at about
  12-hour granularity (py-clob-client issue 216; unverified, open choice C4).
* **CLV of an order** i (long x at q, size s): `s * (close_x - q)` dollars; stake `s * q`.
* **Wallet CLV per $** over a set of orders: `c_w = sum_i s_i (close_i - q_i) / sum_i s_i q_i`. Reported in cents per $ (x 100).
  Pre-fee and pre-gas. Polymarket's 2024-25 fees on these markets are unverified (Gamma `feesEnabled`, `takerBaseFee` are pulled
  and reported); CLV ignores them by construction.
* **Its standard error** (game-clustered ratio estimator): with per-game sums A_g = sum s(close - q) and B_g = sum s q over the wallet's
  G games, `se_w^2 = G/(G-1) * sum_g (A_g - c_w B_g)^2 / (sum_g B_g)^2`, floored at se_w = 0.005.

## 4. Selection (selection window, pre-tip orders only)
* **Eligible wallet**: >= 20 pre-tip orders, on >= 10 distinct games, with total stake >= $500, all in the selection window.
  Protects against ranking on a handful of lucky games.
* **Primary selection score: shrunk CLV.** CLV, not realized P&L: P&L per $ on a binary contract near 0.5 has SD about 1 per $ per
  game; CLV is expected to be several times less noisy (not yet measured here), so ranking on P&L mostly ranks luck. DerSimonian-Laird estimates of mu and tau^2 across
  eligible wallets (inputs c_w, se_w^2); `shrunk_w = mu + tau^2 / (tau^2 + se_w^2) * (c_w - mu)`. If tau^2 = 0 the ranking is all
  noise: verdict **NO_DISPERSION**, stop, no report pull.
* **Sharp set S**: the top ceil(N_elig / 10) wallets by shrunk_w (ties by lower-case address, ascending). Top decile rather than a
  fixed count, so the cut does not depend on how many wallets turn out to exist.
* **Matched set M**: for each s in S, in order of descending shrunk_w, the 3 nearest eligible wallets outside S not yet taken, by
  Euclidean distance on z-scored (log10 total stake, log10 orders, share of stake in game-winner markets, median log10 minutes from
  order to tip), all from the selection window. Ties by address. This matches on activity only, never on score. A matched set
  is s plus its 3. Balance: standardized mean difference S vs M on each matching variable, reported; guard G3.
* **Frozen list.** S, M, every eligible wallet's c_w / se_w / shrunk_w, mu, tau^2 and the gate-A JSON are written to
  `reports/sharp_wallets/selection.json` and **committed (sha256 in DECISIONS) before step 2 is downloaded**.

## 5. Primary metric, floor, CI, decision (report window, pre-tip orders only)
* **Active** in the report window: >= 10 pre-tip orders on >= 5 games (report window). Inactive members of S or M drop from the
  means; attrition per group is reported (guard against reading survivorship as skill: section 9).
* `r_w` = wallet CLV per $ (section 3) over w's report-window pre-tip orders.
* **D = mean_{w in S_active} r_w - mean_{w in M_active} r_w** (each wallet weighted equally, so one whale cannot carry it), cents per $.
* **CI**: two-way (pigeonhole) cluster bootstrap, 10,000 resamples, `numpy.random.default_rng(20261027)`: draw matched sets (sorted
  by sharp-wallet address) and report-window games (sorted by `gameId`) independently with replacement; each order is weighted by
  (its set's multiplicity x its game's multiplicity); r_w is recomputed with those weights (a wallet with zero weight drops for that
  resample) and the group means weight wallets by set multiplicity. 95% percentile CI. Wallets linked by the sybil check (section 10)
  are merged into one cluster first.
* **Floor: D >= 1.0 cent per $**, pre-fee, net of nothing (CLV is pre-fee by definition). Context only: a Kalshi copier would pay the
  taker fee, about 0.07 x (1 - p) per $ (3.5 c per $ at p = 0.5), so 1 c per $ is a "skill exists" floor, not a "followable" one.
* **Verdicts** (one, in this order):
  - INTEGRITY_FAIL: any of I1-I3 fails.
  - PERSISTS: D >= 0.010, CI lower > 0, and guards G1-G4 all hold.
  - PERSISTS_BELOW_FLOOR: CI lower > 0 and G1-G4 hold, but D < 0.010.
  - REVERSAL: CI upper < 0 (S worse than matched in the report window).
  - NULL: anything else (including a CI excluding 0 with a guard failing; the failing guard is named).

## 6. Guards (all must hold for PERSISTS / PERSISTS_BELOW_FLOOR)
* **G1 absolute**: mean r_w over S_active > 0 (point estimate). "Beats the close" is meant literally, not only "loses less than M".
* **G2 permutation null**: 1,000 permutations (`default_rng(20261028)`): shuffle shrunk_w across eligible wallets, re-select S
  (top decile) and M (matching), recompute D on the report window. p = (1 + #{D_perm >= D_obs}) / 1001 < 0.05. This sizes the
  whole pipeline (activity traits, matching residue, attrition) under no persistence.
* **G3 balance**: |SMD| < 0.25 on every matching variable.
* **G4 one-wallet**: dropping the S wallet with the largest report-window stake (and its matched set) leaves D > 0.
* **Integrity**: I1 >= 95% of Gamma NBA game markets in the windows matched to exactly one game; I2 >= 80% of pre-tip stake has a
  close; I3 no fill with `timestamp >= 2025-07-01T00:00:00Z` and no market matched to a game outside the 2024-25 schedule is present in either pull (holdout fence).

## 7. Secondary and slices (descriptive; never a verdict)
* **Secondary family (BH q = 0.10, 3 tests)**: (a) realized P&L per $ (settlement from Gamma `outcomePrices` at resolution; void
  markets dropped and counted), D_pnl = S minus M, same CI; (b) Spearman rank correlation between shrunk_w and r_w over all wallets
  eligible in selection and active in the report window, wallet bootstrap CI; (c) mean r_w of S alone, same CI.
* **Slice family (BH q = 0.10)**, D within each, same CI, only where the slice holds >= 30 active S wallets:
  market type (game winner; spread; total; player props: each only if it passed A6), phase (regular + play-in vs playoffs),
  time-to-tip (orders >= 60 min before tip vs < 60 min; the < 60 min slice is where price impact and latency trading live), and
  **in-play**: orders >= tip + 15 min, scored by a **10-minute markout** (size-weighted mapped price of other wallets' taker orders in
  [t + 5 min, t + 15 min]; >= 3 orders or the order is dropped and counted) instead of the close. At most 7 tests; the family size is
  the number actually run, reported.
* **Also reported**: N_elig, |S|, |M|, active counts and attrition per group; SELL share; stake distribution; mu and tau^2 in
  both windows; balance table; fraction of S whose selection CLV comes > 50% from one game; S defined as a fixed top 50 (sensitivity);
  Gamma fee fields; the gameStartTime mismatch count.

## 8. Minimum-data gate (non-negotiable 8): two steps, selection first
**8.1 Step 1 (after the freeze; selection-window markets only on disk).** Devin pulls Gamma metadata for the whole season (metadata
carries no wallet information) and Data API fills for **selection-window markets only**. A gate script reads only step-1 files and writes
`reports/sharp_wallets/gate_a.json`:
* A1 = I1 on selection markets; A2 = I2 on selection orders.
* A3: N_elig >= 300 (so |S| >= 30 and |M| >= 90).
* A4: tau^2 > 0, else NO_DISPERSION.
* A5 projected power: assume each wallet's report activity is its selection activity x 609/712 and 50% attrition in both groups;
  per-wallet variance v_w = tau^2 + se_w^2 x 712/609; `SE_proj = sqrt(mean_S v_w / (0.5 |S|) + mean_M v_w / (0.5 |M|))`. Requires
  `2.8 x SE_proj <= 0.010` (80% power, two-sided 0.05, at the floor).
* A6 per type: a type is a slice only with >= 100 matched selection markets and >= 30 S wallets with >= 5 orders in it.
Any of A1-A5 failing: **INSUFFICIENT_DATA** (or NO_DISPERSION), after 8.4; no report pull; no re-specification.
**8.2 Freeze of the lists.** S, M and gate_a.json are committed (section 4) before step 2 is downloaded.
**8.3 Step 2 (report-window markets).** A counts-only script reads only `proxyWallet`, `conditionId`, `timestamp`, `transactionHash`
(no price, size, side or outcome) and checks **B1: >= 30 active S wallets and >= 60 active M wallets**. B1 failing:
INSUFFICIENT_DATA. Only then is the scorer run, once.
**8.4 Capture audit before any data-based failure (NN8, second sentence).** Before declaring A or B failed, show: every NBA game market
Gamma lists for 2024-25 was requested; pagination reached `has_more = false` or the 10,000-offset cap was split by `start`/`end`
windows; and these repair sources, with cost: the Goldsky-hosted Polymarket orderbook subgraph (`orderFilledEvent`: maker, taker,
asset ids, amounts, timestamp, tx hash; free, public), and Polygon CTF Exchange `OrderFilled` logs via a public RPC (free, slow, hours).
**Deadline**: market-scoped Data API requests return about the last three years only, so the 2024-10 fills may disappear around
**2027-10**. Pull well before that.

## 9. What each choice protects against
| Choice | Protects against |
|---|---|
| Select <= 2025-01-31, report >= 2025-02-01, by game date | in-sample persistence (the selection itself would "persist") |
| Per-market pull, step 1 before step 2 | peeking at report-window behaviour while choosing S |
| Lists committed before step 2 | changing S after seeing who stayed good |
| CLV, not P&L, as the score | ranking luck; P&L per $ is expected to be several times noisier |
| Close excludes w's own orders | a wallet scoring against itself (pushes its CLV toward 0) |
| Shrinkage with clustered SE, eligibility floors | small-sample winners at the top of the ranking |
| Activity-matched M | "S beats everyone" just because heavy or early traders do |
| Permutation of the whole pipeline (G2) | regression-to-the-mean and selection artifacts the matching misses |
| Equal wallet weights, G4 | one whale carrying D |
| G1 absolute | calling "loses less than average" sharp |
| Attrition reported per group | survivorship (losers quit, survivors look sharp); both groups face the same activity filter |
| Two-way clustering | shared game news moving many wallets at once (game) and repeated bets by one wallet |
| 15-min tip buffer | early in-play fills scored as pre-tip |
| No 2025-26 data, date fence I3 | holdout contamination |

## 10. Sybil, bots, impact (caveats and the one mitigation)
* **One entity, many wallets** (sybil) makes S look larger and the CI too narrow. Mitigation: within S union M, two wallets are
  **linked** if >= 50% of either's report-window orders have an order by the other on the same market and mapped token within 60 s;
  connected components become one bootstrap cluster. The number linked is reported. Funding-address links are not available from
  this API; residual sybil dependence is a stated limit.
* **One wallet, many people** (vaults, copy-trading proxies) makes a "wallet" not a person; not identifiable here, stated.
* **Bots / latency traders** (Polymarket vs sportsbook arbitrage) can persist genuinely and are not removable; the < 60 min slice
  and the forward lead-lag test (section 11) are what separate "fast" from "informed".
* **Price impact**: a large S taker moves later prints by others, raising its own close. Excluding w's orders does not remove it.
  This would bias toward PERSISTS; the time-to-tip slice and the stake distribution are reported so a reader can see it.

## 11. What a pass earns; what a null means; kill and follow-up
* **PERSISTS earns exactly one thing**: a new pre-registered forward **lead-lag test** of whether S's live order flow on Polymarket
  precedes Kalshi `KXNBAGAME` mid moves (paper only, new rule id, fresh S selected on 2026-27 data inside that rule). **No betting,
  sizing or model change** follows from any verdict here (non-negotiables 5 and 6). S is never a model input.
* PERSISTS_BELOW_FLOOR, NULL, REVERSAL, INSUFFICIENT_DATA, NO_DISPERSION: **closed**. One pre-registered follow-up at most (named
  in advance: the same rule on 2023-24, select <= 2024-01-31, if and only if the verdict is INSUFFICIENT_DATA because 2024-25
  is thin and 2023-24 holds more; otherwise none).
* A NULL means: no persistence at or above the floor that this sample can detect (MDE from A5, reported); it does not mean no
  sharp traders exist.
* Kill: any integrity failure; any discovery that a 2025-26 fill reached the scorer (void the run, log it).
* Nothing in this rule changes after step 1 is read; a new question gets a new id.

## 12. Priors (drafted for PREDICTIONS_LEDGER before any data)
P(PERSISTS) 0.20; P(PERSISTS_BELOW_FLOOR) 0.12; P(NULL) 0.30; P(REVERSAL) 0.03; P(INSUFFICIENT_DATA or NO_DISPERSION) 0.30;
P(INTEGRITY_FAIL) 0.05. Expected D +0.6 c per $ (80% interval -0.3 to +2.0), conditional on passing the gates. P(gate A passes)
0.72 (so about 0.28 of the 0.30 is step 1, 0.02 is B1). Reason above Claude's 0.15: sportsbooks are widely reported to identify sharp bettors by persistent CLV (industry practice, not verified here),
and latency traders against sportsbook moves would persist mechanically; reason it stays low: 2024-25 NBA volume on Polymarket
international may be thin per wallet, and the floor plus G1/G2 are strict.

## 13. Exact fields the pull needs (nothing else)
* Gamma `/markets` (tag NBA, `closed=true`, `end_date_min` 2024-10-01, `end_date_max` 2025-06-30): `conditionId`, `clobTokenIds`,
  `outcomes`, `outcomePrices`, `question`, `slug`, `sportsMarketType`, `line`, `gameStartTime`, `gameId`, `startDate`, `endDate`,
  `closedTime`, `umaResolutionStatus`, `feesEnabled`, `takerBaseFee`, `makerBaseFee`, and from `events[]`: `id`, `slug`, `title`,
  `startDate`. Tags only to filter.
* Data API `/trades` (`market=<conditionId>`, `takerOnly=true`, `start`/`end` windows so no window exceeds the 10,000 offset cap):
  `proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`, `timestamp`, `transactionHash`, `outcome`, `outcomeIndex`.
  **Not stored**: `name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`, `icon`, `title`, `slug`, `eventSlug` (profile
  data is personal and unneeded; titles come from Gamma).
* Step 1 requests only markets whose matched game ET date <= 2025-01-31; step 2 only 2025-02-01..2025-06-22. No `user=` requests.

## Open choices for Devin (defaults taken in the text)
* C1 ordering: default freeze first, gate on step 1 (DECISIONS 18:17). Alt: a counts-only step 1 (no prices) before freeze, honouring NN8 literally.
* C2 report window: default regular season + play-in + playoffs to 2025-06-22. Alt: regular season only (2025-04-13), 519 games.
* C3 makers: default taker fills only. Alt: also `takerOnly=false`, maker records as a separate secondary (role inference unverified).
* C4 close: default last-10-orders trade proxy. Alt: CLOB `prices-history` mid if it proves to have <= 5-minute resolution for resolved markets.
* C5 floor: default 1.0 c per $. Alt: 3.5 c per $ ("followable on Kalshi after the fee").
* C6 sharp set: default top decile. Alt: fixed top 50.
