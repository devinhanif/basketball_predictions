# PAPER_TRADER (DRAFT, rule version `paper-trader-v2`)

Status: DRAFT v2, rewritten 2026-10-21 after the adversary's pre-freeze review
(`docs/reviews/PAPER_TRADER_prefreeze_2026-10-21.md`, NOT_READY on v1). Nothing here is frozen; the main session freezes it
after a second review and Devin's sign-off, by writing `configs/papertrade_freeze.json` (`python -m nba.papertrade digest
--write`) in the freeze commit. No scored trade exists before that commit. Paper only: public GET endpoints, no credential,
no order, nothing that can reach an order (non-negotiable 5).

2026-10-22 runs as an UNSCORED SHAKEDOWN under `--rule-version paper-trader-v2-shakedown`. Its rows are in the same store but
`summarize` filters on `rule_version = paper-trader-v2` and `slate_date >= freeze_date`, so no shakedown row can reach a claim.

## The question

Does a rule-based trader that buys single Kalshi prop contracts where the stored production forecast, shrunk toward the market,
disagrees with the real ask after fees, make money per dollar staked over a season? It protects against a backtest that never
pays the ask, a one-night story told from the hits, and (new in v2) a price that no one could trade.

## What is traded (all in `configs/papertrade.yaml`)

- **Primary scope: singles at real prices.** One prop contract, entered at the top of the real order book (`GET /markets/{ticker}/orderbook`
  at entry; the listing quote is used only when the book is unavailable and the row says `entry_source = listing`), exit and
  settlement on the same market. Parlays (`parlay2`, `parlay3`) are logged as descriptive rows flagged `synthetic = TRUE`: their
  entry is the product of leg asks and their exit the product of leg bids, which no instrument trades at. They never enter the primary,
  any look, or the live-money rule; they are reported on their own with n and a point value only.
- Legs: player props (pts, reb, ast, fg3m "N+") from the stored `props_context_residual` row (`p_ge_full` / n, conditional on
  playing). Either side of a market (YES at the ask, NO at 1 minus the best YES bid). Game winners are screened and logged,
  not traded (`trade_win_legs: false`): the market beat the model there (2025-26 replay, Brier 0.0064 worse).
- Threshold: the contract threshold is `k = floor(floor_strike) + 1` and is used only if `strike_type == "greater"` AND the title's N equals k;
  otherwise the market is skipped as `threshold_mismatch`.
- Forecast: version must be in `allowed_forecast_versions` (`ctxres-v1`), made before tip, at most `max_forecast_age_h` old at entry,
  stored P(play) >= 0.90. The forecast `made_at`, version and the entry source are stored on every leg. The alias table and model version are frozen per rule
  version (the alias file is in the freeze digest); a promotion of aliases or a model during the window means a new rule version, or is
  reported as a descriptive breakpoint and never restarts the count.
- Probability for the decision: `shrunk = 0.25 * model + 0.75 * Kalshi mid` (`shrink`, fixed). Evidence for 0.25: ODDS_HISTORY H2 fitted
  weights 0.10 to 0.35, T480 calibration slopes 0.15 to 0.19. (v1 used 0.5, which the review showed selects model errors.)
- Screen: spread <= 5c, both sides quoted, ask in [0.10, 0.90], ask size >= contracts, tip >= 10 minutes away, EV per $ of cost >= 3% on
  the shrunk probability after the verified fee object (`configs/parlay.yaml`, series passed through), book ask no worse than the listing ask by more than 1c.
- Slots per slate: `single`, `single`, `single` (primary) then `parlay2`, `parlay3` (synthetic). Singles are distinct (game, player, stat) families;
  a slot with no qualifying plan stays empty and the scan records `no_positive_ev_found`.
- Stake: $8 per position, integer contracts, cost incl. the entry fee <= $8. Entries at or after tip, or inside 10 minutes, are refused and logged.
- Scans: 75 minutes before the first tip and 45 minutes before each later tip window; `monitor` marks every minute.

## Arms and metrics (primary scope: settled, non-void singles)

- (a) strategy WITH the exit rule: P&L after entry and exit fees; if no exit fired, held to settlement. **PRIMARY**, because the exit rule is Devin's question
  (the adversary's safe default was (b); this is Devin's call and is marked as such).
- (b) the same positions held to the end. Paired comparison: (a) minus (b) per $ of cost.
- Placebo: for each strategy position, a seeded random pick (seed = hash of the scan id) from the same screened universe, same slot structure, same
  asks, drawn uniformly over screen-passing (market, side) legs with the model-EV floor removed, held to the end. It never reads a probability, so its
  expectation is minus costs and it is independent of the model. (v1's "market-favourite" control was the strategy's mirror image and is dropped.)
  Reported: (a) minus placebo, stake-matched, with both n.
- Closing-line value (CLV) per contract: the held side's last pre-tip mid (the last `marks` row before tip) minus the entry ask. Date-clustered
  CI. It has far lower variance than P&L and is the only metric here with power for a small edge.

**The exit rule.** After any leg's game has tipped, exit when the estimated proceeds from selling every open leg at its bid, AFTER the exit fee,
are >= hold value x (1 + 5%) and every bid size covers the position. Hold value uses the shrunk model for legs not yet started and the mid for
started legs; **after tip the rule uses market mids only (no in-game model), so it cannot express "the edge is gone"**. Because a bid cannot
exceed the mid, for a single whose game has tipped the rule essentially never fires: (a) and (b) will be nearly identical for singles. It can fire only
for a multi-leg position with an unstarted leg valued low by the model. This is stated so nobody reads (a) as a tested timing skill.

## Looks, alpha and the scope filter

- Exactly two named looks; CIs and any claim exist only at them. **L1**: the first slate date on which cumulative settled non-void primary positions
  >= 150 AND slate dates >= 30. **L2**: the 100th slate date or the first date on/after the season end (2027-04-11), whichever is first. Alpha is split
  0.005 at L1 and 0.045 at L2 (two-sided), bootstrap quantiles set to match. The look's data are the rows with slate_date <= the look date.
- Futility close at L1 if the upper bound of the (a) CI is below zero.
- Between looks `summary` prints counts and point values with `status: not_a_look`; it prints no CI and no verdict. A test withholds the CI off-look.
- Secondary family: (a) minus (b) and CLV, each at half the look's alpha (Holm, conservative). (a) minus placebo is a gating condition, not a separate claim.
- Scope: `rule_version = paper-trader-v2`, `slate_date >= freeze_date`, role strategy, `synthetic = FALSE`, status settled. Voids and positions not yet settled do not count toward
  the 150. A shakedown rule version is never scored. Tests plant a pre-freeze row, a `v0` row and a synthetic row and check they are excluded.
- Rule integrity: the freeze record lists sha256 of this text, `configs/papertrade.yaml`, the `fee_model` block of `configs/parlay.yaml`, `nba/papertrade/*.py`,
  `nba/live/kalshi_pull.py`, `nba/live/http.py` and `configs/kalshi_aliases.yaml`. A scored `scan` recomputes the digest and refuses to open a position on any mismatch.
  Each scan row stores the alias-table hash and the combined digest.

## Minimum-data gate against the real data (non-negotiable 8; replay on the 2026-10-21 capture)

Command: `python -m nba.papertrade --nba-db <nba.duckdb> --rule-version paper-trader-v2-shakedown replay --date 2026-10-21 --raw data/raw/live_2026-10-21 [--max-age-h H] [--extra-aliases configs/kalshi_aliases_candidates_2026-10-21.yaml]`
(captured listing quotes at the six scan times; stored forecasts as of each time; one slate, so n = 1 date).
- Forecast age cap 6 h: **0 positions** (all 1,622 prop legs `forecast_stale`): production writes props once a morning (about 15:20 UTC) and the first scan is 6.4 h later.
  A 6 h cap is unreachable and is therefore NOT the rule.
- Cap 12 h (the rule as drafted), current alias table (126 of 2,078 markets unmatched): **3 singles, 3 placebos, 0 parlays** (6 passing legs at the first scan, 3 at the next).
- Cap 12 h with the 115 candidate aliases promoted: 3 singles, 1 parlay2, 1 parlay3, 5 placebos.
- Projection: at 3 singles per slate the 150-position floor needs 50 slate dates (about the second week of December if every date has a slate; fewer fills push L1 toward the 100-date L2).
  One slate is a weak basis for a projection; the shakedown adds dates 2 to 7 of evidence before the freeze. If the shakedown fill rate is under about 1.5 singles per slate, the gate
  is not reachable by L2 and the rule must change (more alias coverage, or a wider screen) BEFORE the freeze.

## What would justify Devin considering live money, and what closes it

Considering (Devin's decision, not a recommendation to stake): at a look, ALL of: the (a) CI lower bound > 0 at that look's alpha; the (a) minus placebo CI lower bound > 0;
concentration <= 25%, where concentration = max over dates of |P&L of the date| / sum over dates of |P&L of the date|. Nothing else counts, including a hot week.
Closing: futility at L1; or at L2 the (a) CI contains zero or lies below it. One pre-registered follow-up at most, no variant-chasing. Until then the answer to "what should I bet?" is "keep your money".

## What this test can and cannot say

- Power is low. Simulated (adversary, 150 positions over 50 dates): 2.9% power at a true +5% per $ edge, 27% at +30%. It can close "no loud edge"; it cannot confirm a small one. CLV is the
  secondary built to see a few percent.
- Exits add no EV under fair prices (a martingale minus costs); see the exit-rule paragraph for why (a) and (b) are expected to coincide for singles.
- Selecting the largest model-versus-price gaps selects model errors (winner's curse); shrink 0.25 is a fixed evidence-based guess, and the placebo and CLV exist to catch the rest.
- The forecast is the morning distribution while the market at T-75 holds later news: forecast age is stored per leg, and a descriptive cut by age is reported.
- Fills assume the quoted top-of-book size is available; no market impact. Settlement uses Kalshi's `result`. A single-leg player market whose player is inactive settles to a fair price: if
  `settlement_value_dollars` is stated it is used, otherwise the position is a VOID, counted and excluded from every arm. Predictions are scored at prediction-time prices, never the close.
- The raw `p_ge_full` is used, not the T493 line-calibrated probability; the `candidates` table stores model probability, bid, ask and reason for every screened leg, so a reliability table at the traded thresholds can be built later.

## Pending priors (main session)

(To be filled by the main session before the freeze commit and copied to `docs/PREDICTIONS_LEDGER.md`.)

## Market agent priors, 2026-10-21 (before any scored trade)

- P(the (a) CI lower bound > 0 at L1 or L2) = 0.05.
- (a) per $ at L2: expectation -0.06 (range -0.20 to +0.06); placebo -0.05 (spread plus fees only).
- P((a) minus placebo CI lower bound > 0 at a look) = 0.04. P(CLV mean > 0 with CI excluding zero at a look) = 0.25 (a small information edge on stale-price legs is the one thing I would not be surprised by).
- P((a) and (b) differ by more than 1% of cost in total P&L at L1) = 0.10 (the exit rule almost never fires on singles).
- P(L1 is reached before L2) = 0.55 (depends on the fill rate and the alias coverage).
- P(at least one scan reports `no_positive_ev_found` before the freeze) = 0.6.
- P(the capture and the trader both run on every slate date from 2026-10-22 to 2026-10-31 without a missed date) = 0.75.
