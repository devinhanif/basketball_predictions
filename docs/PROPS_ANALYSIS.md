# Daily props analysis (read-only, analysis only, not financial advice)

`python -m nba.parlay analyze --date D` prices every mapped, pre-tip Kalshi game and prop contract on a slate with the
existing engine (`slate.load_slate`, `price_singles`/`price_contract`, `ev`, copula `JointModel`, `shadow.track_record`).
Nothing is re-implemented: per-contract EV and verdicts equal `evaluate` (tested). It writes only
`reports/props_analysis/<D>.md` and `<D>.json`; `nba.duckdb`, the Kalshi DB and the paper DB are opened READ_ONLY (a missing
paper DB means a zero track record and is NOT created). No orders, no credentials.

```
uv run python -m nba.parlay analyze --date 2026-10-21 [--now 2026-10-21T20:00] [--out reports/props_analysis]
    [--db nba.duckdb --kalshi-db data/kalshi/kalshi.duckdb --paper-db data/parlay/paper_trades.duckdb]
    [--no-shadow] [--narrate] [--budget 20 [--target 100]] [--engine gaussian_copula|t_copula|independence]
```

## What the report contains
- Honest header: sample sizes (games, markets read/mapped, contracts priced), the shadow track record vs the market
  (n settled rows, n slate dates, skill), the gate (>= 30 settled rows over >= 14 slate dates with positive skill), the
  current model weight (0 until the gate opens), the verified fee schedule, "analysis only, not financial advice".
- Game and prop tables: model P(yes) with its 80% interval (raw, unshrunk), market mid and ask, per-contract fee, EV and
  EV at the low bound for the YES side, EV_low for the NO side, model weight, verdict (`no_positive_ev_found` first class),
  p_play / DNP risk, and the raw (hypothesis) EV_low.
- Key drivers (player props): DNP risk, projected minutes, context shift vs recency (pts), teammates OUT from
  `player_availability` (as_of <= --now) with their last-10 per-game production ("vacated"), and the opponent's last-20
  team total allowed vs league mean. The stored forward prediction does not carry the model's own vacated/opponent
  features, so these are descriptive context recomputed from `nba.duckdb`, not the model's inputs.
- Shadow arms (`props_context_residual_int / _lt / _t30`): P(>=N) side by side, labelled "shadow, unconfirmed", with a
  disagreement flag at >= 0.05 absolute. OMITTED automatically for slates before 2026-09-01 (replay seasons; the
  shadow-arm deltas are SEALED per docs/HOLDOUT_ACCESS_LOG.md 2026-10-09) or with `--no-shadow`.
- Same-game parlays (top by EV at the low bound): copula/engine joint with interval vs the independence product and the
  correlation effect; cross-game 2-leg combos assumed independent (docs/PARLAY_ENGINE.md independence check). Parlays are
  priced at the PRODUCT of leg asks, which is not a tradable price (no combo contracts are ingested).

## Budget and target questions ("I have $20, what is best?")
`best_for_budget(budget_usd, date, now)` and `best_for_target(budget_usd, target_profit_usd, date, now)` in
`nba/parlay/budget.py`, also exposed as assistant tools and as `analyze --budget 20 [--target 100]`.
- Whole contracts only. Fees use the configured verified schedule with the per-ORDER round-up, so EV is computed per
  order (a 1-contract order at $0.45 pays a $0.02 fee; 10 contracts pay $0.18, not $0.20).
- Never exceeds the budget (cost = contracts x ask + order fee, maximal affordable size checked).
- Budget mode ranks by EV at the conservative low bound PER DOLLAR staked (dollar EV shown, not ranked on, so tiny stakes
  do not win by losing less). Informational
  fractional-Kelly contracts use the configured fraction and cap.
- Singles and engine-priced parlays (<= 4 legs, bounded enumeration: top-12 legs by raw edge, <= 400 combos) are both
  considered; parlays are flagged "no (hypothetical)" and can never be the headline recommendation.
- Target mode sizes each option to the smallest order whose profit-if-hit after fees reaches the target within the
  budget. The primary answer is the MOST LIKELY way to make it (highest P(hit)); the best EV-low-per-$ option is a
  secondary column. The frontier (+$5/+10/+25/+50/+100 plus the asked target) is non-increasing in P(hit) by
  construction (feasible sets are nested) and tested.
- Names come from Kalshi prop titles, then reviewed aliases and the nba_api static list; teams are abbreviations.
- Until the gate opens the model weight is 0, the shrunk probability equals the market, and the answer is
  "keep your money: no positive-EV bet at the conservative bound". The unshrunk model view is shown only as
  "hypothesis, not a recommendation".

## AI layer
`--narrate` sends ONLY a compact JSON of already-computed numbers to the local Ollama backend (`assistant:` in
configs/parlay.yaml, no paid API). The prose goes through `numguard` against those numbers (foreign numbers become
`[number removed: not from engine]`, betting-advice sentences are removed). If Ollama is down the step is skipped with a
note. The assistant (`python -m nba.parlay assistant`) has the same budget/target as tools `best_for_budget` and
`best_for_target`.

## Ops wiring (the steward applies this; nothing under ops/ was edited)
In `ops/nba_daily.sh`, pretip case, directly after the `parlay_shadow` step (which follows `market_capture`):
```
    step props_analysis "$UV" run --no-sync python -m nba.parlay analyze --date "$TODAY_ET"
```
Optional narration once Ollama is installed: append `--narrate`. Overwrites `reports/props_analysis/<date>.{md,json}`
each pretip run; read-only on all databases.

## Replay demo
`reports/props_analysis/demo_synthetic/` holds three example reports built from a SYNTHETIC fixture (the 2025-26 replay
DBs were still initialising and write-locked when this was built; no forward predictions existed yet). For the replay:
```
uv run python -m nba.parlay analyze --date 2025-11-12 --now 2025-11-12T20:00 \
  --db data/rehearsal/replay2025.duckdb --kalshi-db data/rehearsal/kalshi_2025.duckdb \
  --paper-db data/rehearsal/paper_copy.duckdb --out reports/props_analysis/replay2025
```
Shadow columns are dropped automatically for these dates. Replay results are descriptive only.
