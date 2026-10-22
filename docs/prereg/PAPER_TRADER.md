# PAPER_TRADER (DRAFT, rule version `paper-trader-v1`)

Status: DRAFT written 2026-10-21 before the first paper trade exists. The scoring rule below is committed with
the code and `configs/papertrade.yaml`; nothing in either changes after a result is seen (a new question gets a new
rule version). Devin confirms it matches what he cares about; until then no number from this trader is a claim.
Origin: DECISIONS 2026-10-21 20:50 ("paper robo-trader and full live capture approved"). Paper only: public
GET endpoints, no credential, no order, nothing that can reach an order. Non-negotiable 5.

## The question

Does a rule-based trader that buys where the stored production forecast disagrees with the Kalshi ask, after fees
and spread, and may sell early, make money per dollar staked over a season of slates? It protects against two things
we have already been burned by: a backtest that never pays the ask (the replay has no spread), and a one-night story
told from the hits (a parlay that cashes is still a -X% bet; a miss is not proof of no edge).

## What is traded (all fixed in `configs/papertrade.yaml`)

- Legs: player props (pts, reb, ast, fg3m "N+" markets) priced from the stored `props_context_residual` row made
  before tip (`p_ge_full` count / n, conditional on playing; P(play) >= 0.90 or the leg is skipped). Either side of
  a market (YES at the ask, NO at 1 - the YES bid). Game winners are screened and logged but NOT traded
  (`trade_win_legs: false`): the 2025-26 replay has the market ahead of us there (Brier 0.0064 worse).
- Player-name matching uses only the reviewed alias table (`configs/kalshi_aliases.yaml`). Unmatched names are listed in
  every scan and never guessed. On the 2026-10-21 slate 1,916 of 2,078 prop and winner markets had
  no alias (126 names), so props are traded on the reviewed subset only until the maintainer promotes candidates
  (`configs/kalshi_aliases_candidates_2026-10-21.yaml`).
- Probability used for the decision: shrunk = 0.5 * model + 0.5 * Kalshi mid (`shrink`, fixed, never tuned).
- Candidate screen: spread <= 5c with both sides quoted, ask in [0.10, 0.90], ask size >= contracts needed, tip at
  least 10 minutes away, and EV per $ of cost >= 3% on the shrunk probability after the verified fee object
  (`configs/parlay.yaml`, ceil-to-cent per order) at the ask.
- Positions per slate: N = 3, slots `single`, `parlay2`, `parlay3`, filled in that order by best shrunk EV per $.
  Parlay legs come from different games and different tip windows and are treated as independent (cross-game
  residual r is about 0, BEST_PRACTICES s5). Legs are never reused within a slate. A slot with no qualifying plan stays
  empty and the scan says `no_positive_ev_found` when nothing qualifies.
- Stake: $8 per position, integer contracts, cost = contracts x price + entry fee <= $8. Entry at the real ask seen
  at `made_at`; a parlay enters at the product of its leg asks (synthetic, labelled; a Kalshi combo quote may
  differ) with one fee on the product price. Entries at or after any leg's tip (or inside 10 minutes of it) are refused
  and logged in `refusals`.
- Exit rule (strategy a only): live once any leg's game has tipped. Fair = product of leg mids (settled legs 1 or 0).
  Hold = the same with not-yet-started legs at the shrunk model. Exit when the value of selling every open leg at its bid
  >= max(fair, hold) - 1c and every open leg's bid size covers the position. The paper exit is logged at those real bids,
  with a taker fee on the sale.
- Scans run 75 minutes before the first tip and 45 minutes before each later tip window; `monitor` marks every minute.

## Arms and metrics

- (a) strategy with the exit rule: P&L after entry and exit fees; if no exit fired, held to settlement.
- (b) the same positions held to the end (no exit).
- (c) market-favourite control, stake matched: for each strategy position, the same markets on the side the market
  favours (YES if the YES mid >= 0.5 else NO), entered at that side's ask at the same moment, held to the end. It takes
  no view, so its expected result is minus the spread and fees.

Primary metric: cumulative after-fee P&L of (a) per $ staked (sum of P&L / sum of cost), with a 95% CI from a
bootstrap that resamples whole slate dates (4,000 reps, seed 20261022). One primary test, no multiplicity.

Secondary (descriptive, each with n and a date-clustered CI; Holm across the two if either is called a finding):
(b) minus (a) per $ of (a)'s cost (does the exit rule add value), and (a) minus (c) stake matched (does the
forecast beat the market's own favourite side).

Minimum sample before any claim: 30 slate dates AND 150 positions (`min_slate_dates`, `min_positions`). With N = 3 per
slate the 150-position floor is the binding one and needs about 50 slate dates even if every slot fills. Below it
the report prints `insufficient_sample` and claims nothing. Voided positions (a leg that settled to a fair price with no stated
value, inactive player) are counted and excluded from every arm, never guessed.

## What would justify Devin considering live money, and what closes it

Considering (a decision for Devin, not a recommendation to stake): at the minimum sample, (a)'s per-$ CI excludes zero
on the positive side after fees, AND (a) minus (c) excludes zero on the positive side, AND no single slate date
contributes more than 25% of the total P&L. Nothing else counts, including a hot week.

Closing: at the minimum sample the (a) CI upper bound is below zero, or at 100 slate dates the CI still contains zero
(inconclusive and closed; no variant-chasing, one pre-registered follow-up at most). The default answer to "what should
I bet?" stays "keep your money" until the first condition is met.

## What this test can and cannot say (honest limits)

- Exits add no EV under fair prices (a martingale minus the cost of crossing the spread). Arm (a) therefore measures
  entry edge and exit timing together; (b) separates them, and (b) minus (a) is expected to be negative or zero.
- Power is low. Per-$ returns of longshot parlays have a standard deviation near 1.5 to 2, so at 150 positions the
  95% half-width is about 0.25 to 0.3 per $. The test can close "no large edge"; it cannot confirm a real 3 to 5 percent
  edge. That is accepted: the question is whether a loud edge exists, not whether a quiet one does.
- Selecting the largest model-versus-price gaps selects model errors (winner's curse). The shrink of 0.5 is a fixed
  guess, not a fitted weight; the date-clustered CI is what answers whether it was enough.
- Fills are assumed at the quoted ask or bid in full (size checked, no impact); a combo is priced as the product of leg
  asks. Both flatter a real trader slightly.
- Settlement uses Kalshi's own result field. A DNP player leg settles to a fair price on Kalshi; the forecast used is
  conditional on playing, which matches that. Legs are scored at prediction-time prices, never the close.
- Every screened leg (passing or not, including disabled winner legs) is stored per scan with its model probability, bid,
  ask and reason, so the capture and the settled markets support a later model-versus-price scoring that does not depend on the
  eight or so positions we happened to take.

## Priors (stated before any result)

Pending priors (main session).

Market agent, 2026-10-21, before the first trade:
- P(the primary CI excludes zero on the positive side at the minimum sample) = 0.07.
- Expected (a) per $ at the minimum sample = -0.10 (plausible range -0.30 to +0.08): spread plus fee costs roughly 5 to
  8 percent per leg crossed, more on parlays, against an edge I expect to be near zero.
- Expected (c) per $ = -0.06 (spread and fees only).
- P((a) minus (c) CI excludes zero on the positive side) = 0.05.
- P((b) minus (a) CI excludes zero in favour of (b)) = 0.20 (exiting pays the spread and a second fee); in favour of (a) = 0.05.
- P(the trader and capture ran for every slate date through 2026-10-31 without a missed day) = 0.75.
- P(at least one scan reports `no_positive_ev_found`) = 0.85.
