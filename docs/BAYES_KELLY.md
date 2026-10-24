# Bayesian-Kelly stake sizer

Advice only. Devin places every order by hand; there is no order, credential or execution code
here (CLAUDE.md non-negotiable 5). No output of this tool is a recommendation to bet. Code:
`nba/parlay/bayes_kelly.py`; CLI: `python -m nba.parlay size`; settings: `bayes_kelly:` in
`configs/parlay.yaml`; tests: `tests/parlay/test_bayes_kelly.py`.

## What it answers

Given tonight's candidate bets, each with a win probability that is itself uncertain, how many
whole contracts of each (possibly zero) maximise expected log wealth, after Kalshi's fee, under
the nightly loss cap, with a warning if the soft floor could be crossed?

Honest headline: with the numbers we have, the answer is usually zero. A 2-3 cent edge minus a
1-2 cent fee, shrunk toward the market, rarely survives. `no_positive_ev_found` is a result.

## The math, in plain language

**1. One bet, certain probability (classic Kelly, with fee).** Let `c` be the all-in cost per
contract (price plus the fee share) and `q` the win probability. A contract pays $1. With
bankroll `B`, buying `n` contracts maximises `q log(B + n(1-c)) + (1-q) log(B - n c)`. Setting
the derivative to zero gives

    n* = B (q - c) / (c (1 - c))

No edge (`q <= c`) means `n* = 0`. The numeric optimiser reproduces this when the fee is zero
(a test checks it).

**2. Whole contracts and the fee.** Kalshi charges `ceil_to_cent(rate x n x P x (1-P))` once per
order, not per contract, so 1 contract at 82c pays a 2c fee but 10 contracts pay 11c total. The
rate (taker 0.07, maker 0.0175) and the series multiplier are read from the verified fee object
in `configs/parlay.yaml` through `nba.parlay.ev.order_fee`; nothing is hard-coded. Cost of `n`
contracts is `round(n x price + fee, 2)`. Maker orders pay a quarter of the taker rate but may
not fill; the sizer prices the resting price you give it and does not model fill risk.

**3. The probability is uncertain: what that does and does not do.** Each bet has a centre
(Pinnacle de-vig if given, else the mean of sources) and a spread (`p_sd`, or the RMS gap of the
other sources from the centre, floored at 1c). Both live on the logit scale as a normal.

A single bet resolves once, so by the law of total probability its chance of winning is the
posterior MEAN of `p`. The width alone does not shrink a lone stake. Uncertainty bites in two
ways, both modelled:

* **Shrinkage toward the market (the main effect).** Prior: the true probability is the Kalshi
  price plus a noise term with sd `prior_edge_sd` (default 3c: "the market is roughly right").
  Our estimate is a noisy measurement of it. The normal-normal posterior on the logit scale is
  the precision-weighted average, so the weight on our estimate is
  `w = (1/s_est^2) / (1/s_est^2 + 1/s_prior^2)`. A wide estimate gets little weight; a certain
  one (`p_sd = 0`) gets all of it. Optional `n_settled` adds the repo's usual `n / (n + k)`
  shrink first (`n_prior_k` in config).
* **Shared error.** `shared_error_rho` (default 0.5) of each bet's logit uncertainty is common to
  all of tonight's bets (a model or de-vig bias). Outcomes become correlated through it, so
  the whole slate is sized more cautiously than independent bets would be. Integrated with
  Gauss-Hermite nodes.

**A +3c edge with +-5c uncertainty versus a sure +3c** (price 50c, true centre 53c, bankroll $100,
maker fee; run it with `tests/parlay/test_bayes_kelly.py::test_plus3_with_5c...`):

| p_sd | weight on our estimate | posterior win prob | contracts |
|---|---|---|---|
| 0 (sure) | 1.00 | 0.530 | 11 |
| 0.02 | 0.69 | 0.521 | 6 |
| 0.05 | 0.26 | 0.508 | 2 |
| 0.10 | 0.08 | 0.503 | 0 |

The +-5c case gets a fifth of the sure stake because the market-efficiency prior reads most of
the 3c as noise. The 3c prior sd is an assumption chosen before any scoring, not a measurement;
setting `prior_edge_sd` large removes this shrink and leaves only the shared-error effect.

**4. Several bets at once.** The expected log wealth is taken over the joint outcome law:
different games are independent; legs of one game (`game` field) follow a Gaussian copula on
the win events (the construction in `nba/parlay/copula.py`, nearest-correlation repair) with the
pairwise `rho` you pass in `corr` or, if absent, `same_game_rho` (default 0.25, an assumption;
the fitted pair values in `data/parlay/joint_model.json` are better and can be passed in).
Correlated legs concentrate risk, so the combined stake falls (tested).

**5. Constraints and the optimiser.** Steepest ascent on integer contracts (moves of 1, 2 or 5
of one bet), starting from zero. Hard limits: total cost <= nightly cap (and <= bankroll);
steady and swing cost each <= `steady_share` of the cap (Money Strategy v2: 50/50). Tier
defaults to steady at price >= 0.75, else swing; override with `tier`. The soft floor
(`$25 x 1.01^months`) never blocks: if the worst case (every bet loses) ends below it, the
output prints a one-line `WARNING` with the chance of ending below it.

## Input: bets.json

    {"bets": [{"ticker": "...", "side": "no", "price": 0.82, "p_center": 0.846,
               "p_sd": 0.03, "game": "GSW", "tier": null, "order_type": "maker",
               "n_settled": null, "market_p": null,
               "sources": {"pinnacle": 0.85, "model": 0.87, "kalshi_mid": 0.82}}],
     "corr": [{"a": "TICKER1", "b": "TICKER2", "rho": 0.3}]}

`price` is dollars per contract of the side you buy; `p_center` is the chance that side wins.
Give `p_center` (+ `p_sd`) or `sources`. The market prior is `market_p`, else `sources.kalshi_mid`,
else `price`. Command:

    uv run python -m nba.parlay size --bankroll 33.91 --floor 25.25 --cap 20 --bets bets.json

(`--json` prints the full result.)

## Tonight's example (2026-10-23)

Fixture: `tests/parlay/fixtures/bayes_kelly_tonight.json` (tickers are illustrative labels, not real Kalshi tickers). Bankroll $33.91, floor $25.25, cap
$20, taker orders (the config default):

    verdict: no_positive_ev_found
    ticker                    tier   ord    price   p_in    +-  p_post   n n_sure    cost   fee  E[prof]  P(loss)
    KXNBATEAMPTS-GSW-O16.5    steady taker   0.82  0.846 0.030   0.831   0      4    0.00  0.00    +0.00    16.9%
    KXNBATEAMPTS-POR-O22.5    steady taker   0.83  0.850 0.030   0.838   0      3    0.00  0.00    +0.00    16.2%
    KXNBA3PT-LUKA-5           swing  taker   0.68  0.710 0.040   0.690   0      3    0.00  0.00    +0.00    31.0%
    KXNBAREB-CASTLE-6         swing  taker   0.60  0.629 0.040   0.610   0      1    0.00  0.00    +0.00    39.0%
    KXNBA3PT-CASTLE-2         swing  taker   0.54  0.563 0.040   0.548   0      0    0.00  0.00    +0.00    45.2%

`n_sure` is what the same bet would get if its probability were known exactly. Even then the
sizes are 4, 3, 3, 1, 0 contracts ($0.60-$3.30 each): edges of 2-3c against 1-2c of rounded-up fee.
With the uncertainty the posterior edges (1c, 0.8c, 1c, 1c, 0.8c before fee) do not beat the fee.

The same five as maker orders (resting at the same prices; fill is not guaranteed, and the
`order_type` change is the only difference):

    verdict: sized_by_posterior_log_wealth
    KXNBATEAMPTS-GSW-O16.5    steady maker   0.82  0.846 0.030   0.831   2      6    1.65  0.01    +0.01    16.9%
    (the other four: 0 contracts; n_sure 4, 5, 4, 2)
    total cost $1.65  expected profit +0.01  P(net loss) 16.9%  P(below floor) 0.0%
    floor safe: even the worst case ends $32.26, above the $25.25 floor

Read it as: the best use of tonight, at these inputs, is about one cent of expected profit on
$1.65 at risk, i.e. nothing. Keep your money is a good answer. These inputs are also unproven:
the 0.846 / 0.85 / 0.71 / 0.629 / 0.563 centres come from a model that the 2025-26 replay
and the LINE_CALIBRATION work found over-confident, and the Brier gap to Kalshi's price is
unfavourable. The sizer cannot repair a centre that is wrong; it only refuses to stake into a
fee-sized edge.

## Limits (stated once)

* `prior_edge_sd`, `shared_error_rho` and `same_game_rho` are assumptions, not estimates. A
  live log with enough settled bets could estimate them; until then they are defaults.
* The optimiser is a local steepest-ascent on integers, not a proof of the global optimum; for
  concave log wealth with small fee jumps it matches the closed form to within a contract.
* Book depth, partial fills and price movement are not modelled. At most 12 bets and 5 legs
  per game.
* Expected log growth over one night is tiny and noisy; no claim about long-run growth follows.
