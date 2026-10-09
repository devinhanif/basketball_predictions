# MARKET_FEATURE - pre-registration: Kalshi market state as a model feature

Status: PRE-REGISTERED 2026-10-09, before any forward market-capture data exists (the capture
table is empty at the time of writing). Read-only: public Kalshi market data only; no orders, no
credentials, no execution code. Not financial advice.

## 0. Framing (read this first)

- The goal is a model that is **better than the market**, not one that reproduces it. A
  market-as-feature model that mostly copies the price will look good against the injury-Elo model
  and be worthless. Every comparison therefore reports **model vs market log loss explicitly**
  (and model vs rung0_injury_elo), never only model vs our own baseline.
- "No improvement over the market" is an expected, acceptable result. The honest outputs are:
  `beats_market`, `matches_market` (CI contains 0), `worse_than_market`. Each is a success of the
  measurement. Only `beats_market` under section 5 conditions is a candidate for anything.
- Nothing here changes any frozen shadow arm of docs/FORWARD_PREREG_2026_27.md (rule 9). Market-
  feature models are NEW `model_name`s with their own counters.

## 1. What is captured (plumbing, built before the season)

Code: `nba/markets/` (asof.py, implied.py, capture.py). Table `market_at_prediction` in
`data/markets/market_asof.duckdb` (separate file; `nba.duckdb` is never written).

- **Strict as-of.** State = latest `kalshi_prices` row per ticker with `ts < made_at` (strict;
  a snapshot stamped exactly at `made_at` is not used). Tested with planted future books.
- **One row per forward prediction key** `(game_id, model_name, version, target, player_id,
  made_at)`, written by `python -m nba.markets capture --date D`, idempotent, safe to re-run late
  (the state is a pure function of the price history and `made_at`, so a late capture equals an
  on-time one as long as `data/kalshi/` history is retained).
- **Game fields:** de-vigged moneyline home win probability (`mid_h / (mid_h + mid_a)`, with the
  cross-book bounds `lo/hi`), implied home margin (median of the spread ladder, with the
  moneyline as the point P(M>0)), implied total (median of the total ladder), implied team totals
  `(total +/- margin)/2`, rung counts, snapshot age, raw quotes (bid/ask/last/volume/open interest).
- **Prop fields:** per listed N the raw mid and a monotone-projected implied P(stat >= N)
  (pool-adjacent-violators), the ladder median, raw quotes.
- **Usability rule (fixed now):** a quote feeds a derived value only if both sides exist, it is not
  crossed, spread <= 0.30 and age <= 2 h before `made_at`. Raw quotes of all rungs are always kept,
  so the rule can be audited but is NOT tuned on outcomes. Preseason books were seen at 0.15/0.79;
  those are correctly unusable.
- **Missingness is data.** Rows with no usable market are kept with a `reason`
  (`no_kalshi_markets`, `no_quote_before_as_of`, `unresolved_game`). Coverage by slice is reported
  at every look; the market-feature models are only scored on rows where the market exists, and
  the comparison against rung0_injury_elo is made on the SAME rows.
- A `backfill_proxy` capture (past games, 19:00 ET tip proxy) exists for plumbing checks only and
  never enters a pre-registered comparison.

## 2. Planned models (nothing else may be added without an amendment)

Both are ridge-regularised, fitted only on rows with `made_at` before the scored date
(walk-forward by date, expanding window), penalty chosen by inner walk-forward CV on the training
window only.

**W1 - win probability.** Market as prior, model as the update:
`logit P(home) = logit(p_mkt) + w1 * (logit(p_injury_elo) - logit(p_mkt)) + w0`, with ridge on
`(w0, w1)` shrinking toward 0 (i.e. toward copying the market). Offset-ridge form, no other
features in v1. Comparators: market `p_home_win` (de-vigged mid) and `rung0_injury_elo` p_home,
same rows.

**P1 - props** (pts, reb, ast, fg3m). For each listed threshold N with a usable rung:
`logit P(Y>=N) = logit(p_model_ge_N) + a + b * (logit(p_impl_ge_N) - logit(p_model_ge_N))
+ c * (implied_team_total - model_team_context_total)`, ridge on `(a, b, c)`, one fit per stat.
`p_model_ge_N` is `props_context_residual` as stored at `made_at`. Comparators: the raw implied
P(>=N) and `props_context_residual`, same (player, game, N) rows. DNP rows follow
FORWARD_PREREG rule 1 (excluded identically, counted).

Not planned (would need an amendment): GBMs, per-player market effects, closing-line features,
anything using prices after `made_at`.

## 3. Earliest look and sample sizes

- **Win (W1):** first look when at least **200 games** have a usable moneyline state at `made_at`
  AND those games span at least **30 distinct ET game dates**. Walk-forward scoring starts after
  the first 100 usable games, so the first look's scored n is about 100 games (stated with every
  number; expect wide intervals - a log-loss delta of 0.01 needs thousands of games, see
  FORWARD_PREREG section 6). The first look is DESCRIPTIVE for W1.
- **Props (P1):** first look when at least **2,000 paired (player, game, stat) rows with at least
  one usable rung** exist over at least **30 distinct dates**, per stat; stats below the floor
  are reported as "not eligible". Pairs are clustered by game date for inference.
- Nobody looks earlier than this for any decision. The daily report may print counts of usable
  rows (coverage) but not model-vs-market losses before the look.

## 4. Scoring conventions (inherit docs/FORWARD_PREREG_2026_27.md)

- Win: log loss primary, Brier secondary; paired per-game deltas, date-clustered percentile
  bootstrap, B = 10,000, `default_rng(2026)`, seed logged (rule 5). Props: integer-support CRPS is
  primary for the distribution; for the threshold-event model P1 the primary metric is log loss on
  the listed-threshold events, with integer CRPS reported as secondary where a full distribution
  can be reconstructed (rule 4 convention, Delta = candidate minus comparator, negative = better).
- Three deltas are reported at every look, each with n (games or pairs, dates) and CI:
  (a) model - market, (b) model - rung0_injury_elo / props_context_residual, (c) market -
  baseline, i.e. how good the market itself is on these rows. A model that wins (b) but not (a)
  has learned to copy the market and is reported as such.
- Multiplicity: W1 plus P1 x {pts, reb, ast, fg3m} = 5 new tests, BH-adjusted together at each
  look; they are separate from the m = 9 family of FORWARD_PREREG and do not change it.
- Decision CI level follows FORWARD_PREREG section 5 (98.18% interval at efficacy looks,
  Pocock-style) for looks at 200/400/800 usable games (W1) and the analogous 2,000/4,000/8,000
  usable pairs (P1). Looks before the first are not decision looks.

## 5. What counts as a result

`beats_market` for a target requires ALL of: CI of (a) excludes 0 on the improving side at the
look's level; BH-adjusted p below alpha_look; model-vs-market bias guard (|bias| <= 0.5 for props);
calibration slope within [0.8, 1.2]; the improvement present in at least 3 of 4 pre-declared slices
(home/away favourite, first 15 team games vs rest, starter vs bench for props) with n stated;
and coverage not collapsing (usable share >= 50% of eligible rows, else "insufficient market
coverage"). Anything else is `matches_market` or `worse_than_market`. Even `beats_market` triggers
only continued shadowing and a paper-trade `evaluate` hypothesis; the parlay engine's own 14-date
track-record gate and the market-as-prior shrinkage (CLAUDE.md) still apply. EV after fees and
spread is a separate question and is not answered by this document.

## 6. Declared threats to validity

1. Selection: markets exist only for popular games/players; results do not generalise to unlisted
   props. Coverage is reported.
2. Timing: `made_at` is the pretip run time (hourly), the market keeps moving after it; prices are
   top-of-book snapshots every ~15 minutes, so the state can be up to ~15 minutes older than
   `made_at` (age recorded). Thin books have wide spreads; mids are noisy, hence the usability rule.
3. Opening weeks: books are thinner and Elo is least informed; early looks are dominated by this
   regime. Season-phase slices are reported.
4. The listing ladder is not a probability distribution: only the median and per-rung P(>=N) are
   used, no tail mass is inferred.
5. Mac sleep gaps lose snapshots (observed ~2% of runs failed, more when asleep); age is recorded.
6. Low power: ~100 scored games cannot separate a 0.01 log-loss effect from noise. A null at the
   first look is the expected outcome and does not close the question.

## 7. Operations

Add after the `predict` step of the `pretip` mode in `ops/nba_daily.sh` (lead applies; this
document does not edit ops scripts):

```
    step market_capture "$UV" run python -m nba.markets capture --date "$TODAY_ET"
```

It reads `nba.duckdb` and `kalshi.duckdb` read-only (retrying on a held lock; `kalshi.duckdb` falls
back to a private copy), writes only `data/markets/market_asof.duckdb`, exits 0 when there are no
forward rows, and is idempotent per prediction key, so hourly re-runs are safe. Backfill check:
`uv run python -m nba.markets backfill --since 2026-10-08`.

## Amendments (append only, dated; none yet)
