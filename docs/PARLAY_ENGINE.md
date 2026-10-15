# Same-game parlay engine (read-only, paper-trade only)

No order code, no credentials. Paper trades and a shadow log go to a separate writable file
(`data/parlay/paper_trades.duckdb`); `nba.duckdb` and `kalshi.duckdb` are attached READ_ONLY.

## Model
- **Game latents** (`game_model.py`, fitted on seasons 2022-24 only): margin ~ Normal(b + s*logit(p_injury_elo), sd);
  total ~ Normal(a + c*f, sd) with f = as-of decayed average of both teams' game totals (pace x efficiency), shrunk to the as-of league mean.
  Fit: s = 6.97 pts/logit, margin sd = 13.6 (CLAUDE.md's "~12" is a sim target; the injury-Elo residual sd is 13.6), total sd = 18.8, n = 3951 games.
  Win and spread legs read the same margin draw, so they are coherent (P(win) >= P(win by over 7.5), opposite-team legs are exclusive).
- **Player marginals**: 19-point conditional-on-playing quantile grid from `props_context_residual` (`qdist.py`), P(Y>=t) = 1 - F(t - 0.5).
- **Copula** (`joint.py`): one latent per (margin, total, player-stat). Correlations pooled by relation (same_player / teammate / opponent x stat pair),
  player vs own-team margin, player vs total, margin vs total; shrunk (k = 200) and Higham-repaired. Gaussian or t (df 6). Estimated on 2023-24 OOF
  (context-residual OOF + injury-Elo OOF + box scores), 35 pooled pairs, n >= 1300 games each.
- **DNP** (`--dnp-policy`, default `last_fair_price` for the Kalshi commands; availability is assumed independent of the other latents):
  - `last_fair_price` (Kalshi): the market settles at the last fair market price before game start. Source: field `rules_secondary` of the
    stored market objects, e.g. `data/kalshi/raw/kalshi-markets/hist:KXNBAPTS:first:100.json`: "If a player is active but never takes the
    court, the market settles to the last fair market price before game start. Once a player enters the game the market settles based on
    the player's points recorded." EV = P(all play) * (P(hit | play) - cost) + (1 - P(all play)) * (L - cost), L = the prediction-time mid
    (a proxy: the true last fair price is only known at tip). Paper/shadow settlement of such a DNP stores NO outcome and NO pnl (the
    settlement price is not recorded; nothing is guessed). The sentence covers an ACTIVE player only: an INACTIVE player (not covered by
    the text) is unconfirmed; check one live prop series on 2026-10-20.
  - `void`: stake and fee refunded (EV = P(all play) * (P(hit | play) - cost)). Optimistic. Rule per book: unknown for every book in
    `nba/odds` (The Odds API books incl. Pinnacle); no DNP rule is stored in the repo or in the odds payloads, so none is assumed: use
    `void` or `loss` there only as labelled sensitivity bounds, never as the book's rule.
  - `loss`: a DNP leg loses whatever the side (pessimistic bound).
  Blowout (|margin|) minute cuts are NOT captured by the linear copula.

## Roster source and the market-skill gate (ledger audit pass 2 N4, N6)
- **Slate roster** (`slate.load_slate(..., roster_source)`; CLI `--roster-source recent|official` on `evaluate` and `analyze`, default `recent`
  like `nba.daily`): `official` puts each player on his CURRENT team from the cached pre-tip official roster (`data/rosters/<date>/<team>.parquet`,
  cache only, never a request), so movers and rookies are placed (about 26-27% of opening-week rotation players were not under `recent`).
  Missing cache -> falls back to `recent` and says so. A player who still cannot be placed is never silently dropped: `SlateInfo.dropped` lists
  (game, player, stat, reason) and `evaluate` prints it. Use `--roster-source official` whenever the daily run does.
- **Gate**: model weight above 0 requires (a) >= `min_settled_for_claims` settled rows, (b) >= `min_settled_dates_for_claims` slate dates, and (c) the
  LOWER bound of a two-sided game-clustered (CR1, t on G-1 df) `gate_ci_level` CI of the paired per-row Brier difference (market - model) above 0.
  A point estimate, or fewer than 3 games, never opens it; the default verdict is `no_positive_ev_found`.
- **Calibration sd** (`interval.calibration_logit_sd` 0.6, `_tail` 1.6 beyond `tail_cut` 0.70): from the measured over-confidence in
  docs/FAN_KNOWLEDGE.md (80-95% claims right 54-63%, 90-95% right 54%; logit gap about 0.6 to 2.3), replacing the unmeasured 0.15. With it a
  p = 0.80 leg's 80% interval no longer clears a 0.40 ask: only very extreme forecasts can flag, which is the intended conservatism.

## Joint calibration, 2023-24 OOF (`reports/parlay/joint_calibration.md`)
15,798 synthetic same-game parlays (2-4 legs) over 2,633 games; pre-registered rule in `joint_eval.py` (copula kept iff joint log loss CI excludes 0, game-clustered).
Cross-fit (other season's parameters/correlations); only the 2024 fold is strictly walk-forward.

| comparison (A vs B) | slice | n parlays / games | log loss diff (A-B) | 95% CI |
|---|---|---|---|---|
| Gaussian vs independence | all | 15,798 / 2,633 | -0.00053 | [-0.00106, -0.00003] |
| t vs independence | all | 15,798 / 2,633 | -0.00058 | [-0.00114, -0.00004] |
| t vs Gaussian | all | 15,798 / 2,633 | -0.00005 | [-0.00021, +0.00011] |
| Gaussian vs independence | 2024 walk-forward | 7,890 / 1,315 | -0.00099 | [-0.00177, -0.00030] |
| Gaussian vs independence | 2023 cross-fit | 7,908 / 1,318 | -0.00006 | [-0.00082, +0.00066] |

Level log loss: independence 0.37225, Gaussian 0.37173, t 0.37168. Verdict by the rule: **Gaussian copula kept over independence** (CI upper bound
-0.00003, a hair from zero); **t copula not kept over Gaussian**. The gain is ~0.14% of log loss, concentrated in parlays with a player leg;
game-only parlays (n = 615) show nothing. Do not read this as an edge over the market, only as a better joint than the product of marginals.
Leg marginals are fine (mean pred vs observed: player 0.468/0.479, win 0.492/0.499, total 0.496/0.489).

## Commands
```
uv run python -m nba.parlay fit                      # writes data/parlay/joint_model.json (2022-24 game model + 2023-24 correlations)
uv run python -m nba.parlay eval-joint               # ~75 s, writes reports/parlay/joint_calibration.{md,json}
uv run python -m nba.parlay evaluate --date YYYY-MM-DD [--dnp-policy void|loss] [--engine gaussian_copula|t_copula|independence]
      [--combo TICKER1,TICKER2] [--no-log] [--settle]
```
`evaluate` maps open Kalshi game-winner / spread / total / pts-reb-ast-3PM markets to legs, prices both the YES (at the ask) and NO (at 1 - bid) side with
the verified fee schedule (taker, per-order round-up, per-series multiplier), and emits `{legs, model_prob, prob_interval, price, fees, ev, ev_low, verdict, raw_*}`.
No combo contracts are ingested, so `--combo` prices a hypothetical same-game combo at the PRODUCT OF LEG ASKS (marked not tradable, never logged).
PRA/PR/PA/RA and steals/blocks markets are skipped (no joint marginal for sums yet).

`analyze --date D [--budget B [--target T]] [--narrate]` is the daily props analysis report; see docs/PROPS_ANALYSIS.md.

## Market-as-prior: what happens today
The model weight is `n/(n+k)` only if `n >= min_settled` (30) and shrinkage skill vs the market is positive. With zero settled history the weight is 0:
the engine **defers to the market and cannot emit a positive-EV verdict** (verified in `tests/parlay/test_joint.py`). Because paper trades are logged only
for flagged positives, a deadlock would follow, so every evaluated contract is also written to `shadow_predictions` (raw model prob vs market mid, no stake);
`--settle` resolves them against box scores and `track_record` feeds the skill statistic. `raw_ev`/`raw_ev_low` show the unshrunk model EV as a hypothesis only.

## Limits
- Eval is conditional on the player playing (OOF has no p_play) and on parlays whose lines sit near the middle of each distribution.
- Correlations are pooled, not per player; blowout nonlinearity and DNP-teammate usage shifts are not modelled.
- `forward_predictions` in `nba.duckdb` is empty right now, so `evaluate` has not been run against live Kalshi prices; it is exercised end to end on synthetic DBs in the tests.

## Cross-game independence check (`nba/parlay/independence_check.py`)
The engine prices legs from DIFFERENT games as independent (copulas are same-game only). This module tests that assumption.
- (a) props / (b) game results: correlation of different games' residuals on the same date (props: game-mean randomized-PIT normal score per stat; wins: standardized `(y-p)/sqrt(p(1-p))`, plus probit-PIT). Pair-weighted estimator, 95% CI by bootstrapping whole DATES.
- (c) synthetic cross-game parlays (2-4 legs, distinct games, one date, moneyline side random + prop N+ ladder lines): independence product vs observed hit rate, log loss / Brier, reliability, date-clustered CIs. Random ML sides cancel a home-win shock by construction, so (b) is the sensitive test for results; (c) is the end-to-end sanity check.
- `uv run python -m nba.parlay eval-joint` appends this as the "Cross-game legs" section and writes `reports/parlay/independence_check.{md,json}`.
- Forward monitor: `nba.daily.report` runs (a)/(b) on `forward_scores` once `independence_check.min_forward_dates` (30) scored dates exist, and flags when a CI excludes 0 AND |r| > `flag_abs_r` (0.05) (configs/parlay.yaml). Props check uses model `props_context_residual`, wins `rung0_injury_elo`.
- Caveats: 4 prop stats + 2 win measures are tested without multiplicity correction; the |r| floor guards against flagging statistically detectable but economically trivial correlation.

## Local chat assistant (`nba/parlay/assistant/`, read-only)
`uv run python -m nba.parlay assistant` (or `make parlay-chat`) is a chat front end to this engine. A local LLM
(Ollama, default `qwen2.5:3b`, 3B-class for an 8 GB machine) may only call engine tools; it computes nothing.

**Maintainer setup (nothing is installed by the repo):**
```
brew install ollama
ollama serve                 # leave running in another terminal
ollama pull qwen2.5:3b
make parlay-chat             # REPL;  one-shot: uv run python -m nba.parlay assistant --ask "..." --date YYYY-MM-DD
```
Flags: `--date`, `--model` (env `PARLAY_ASSISTANT_MODEL`), `--transcript out.jsonl` (audit log of every tool call and output),
`--dnp-policy`, `--allow-paper-log` (default off). Host from `OLLAMA_HOST` (default `http://localhost:11434`); settings under
`assistant:` in `configs/parlay.yaml`. Temperature 0 and a fixed seed. If Ollama is down the command exits 2 with install steps.

**Tools** (thin wrappers over `slate.py`, `joint.py`, `ev.py`, `shadow.py`): `list_slate`, `evaluate_slate` (what `evaluate` does, no logging),
`price_parlay` (copula within a game, independence across games, fees from the verified config, `prob_interval`, `ev`, `ev_low`, verdict),
`explain_leg` (stored quantiles, P(>=N), p_play, `routed_to`, `fallback_reason`, Kalshi price), `what_if` (add/remove a leg: delta in joint prob,
EV and the correlation effect vs independence), `track_record` (shadow log; "insufficient sample" below `min_settled_for_claims`).
Arguments are validated strictly; an unknown player is an error (reviewed aliases + nba_api static list, never a fuzzy guess).

**Guarantees (enforced in code, tested in `tests/parlay/test_assistant.py`):**
- Numbers: the answer is (a) a block rendered by Python from the tool results, plus (b) the LLM's prose. `numguard.py` strips any
  number-looking token in the prose that no tool result (or the user's own question) reproduces at that precision, replacing it with
  `[number removed: not from engine]`; sentences recommending a real-money bet are also removed. Dates, tickers, 0-10 counts and years are exempt.
- `no_positive_ev_found`, `prob_interval`, `ev_low`, fees, model weight and sample sizes are printed from the result, never paraphrased.
- Read-only: `nba.duckdb` and `kalshi.duckdb` are attached `READ_ONLY`; the paper DB is opened read-only except inside `log_paper_trade`,
  which does not exist unless `--allow-paper-log` is passed and needs `confirm=true`. No order endpoints, no credentials.
- Combos with no listed contract are priced at the product of leg asks and labelled `NOT_A_TRADABLE_PRICE`; legs with no market and no
  user-supplied `ask` return `not_evaluable_no_price` instead of an invented price.
- While the settled track record is below `min_settled`, the engine's model weight is 0: `model_prob` and its interval collapse onto the market
  mid and the verdict is `no_positive_ev_found`; the unshrunk `raw_*` numbers are shown as a hypothesis only.

**Example session (scripted fake LLM; the real one only changes the prose):**
```
you> Price LAL to win
=== Engine results (rendered by Python from tool outputs) ===
[price_parlay]
  legs (1, 1 game(s)): LAL win
  price (ask): $0.450   fees: $0.020   bid/ask spread: $0.050
  EV: -$0.045   EV at low bound (ev_low): -$0.045
  verdict: no_positive_ev_found
  raw unshrunk model (hypothesis only): prob 77.5%, raw_ev +$0.305, raw_ev_low +$0.266
=== Explanation (language-model prose; numbers checked against engine output) ===
Uncertain: roughly a [number removed: not from engine] chance, verdict no_positive_ev_found.
-- Analysis only, not financial advice. ...
```
Caveat: a 3B model will often pick wrong tools or arguments; the guardrails make that visible (errors are rendered) rather than silently wrong.
No live Ollama session has been run in this repo's tests (all tests use a scripted backend and an HTTP mock).

## Top-3 tickets (`python -m nba.parlay top --date YYYY-MM-DD`, 2026-10-15)

Contract: `docs/contracts/top_parlays_v2.md`. Writes `reports/parlay/top_parlays_<date>.json`: the most probable two-leg and
three-leg tickets of one book, ranked by CALIBRATED joint probability. Read-only and paper only; not wired into `ops/` or
`nba/daily`. One file per slate: wiring will need one run per tip group, each rewriting the file with `made_at` = the run clock,
legs only from games not yet tipped and quotes stamped at or before `made_at` (review B1).

- **Legs:** every pre-tip main-line over and under for pts / reb / ast / fg3m in `market_asof.duckdb` (`live_book_odds`). Only each
  book's newest snapshot per market and player is selectable (a withdrawn line never is; the export shows every line, we choose).
  Quotes are stamped before tip and no later than the forecast they pair with. Refused and counted in `diagnostics`: priced at or
  after tip, priced after `made_at`, game already tipped at the run clock, book without a CAL map, whole-number line, P(play)
  under 0.90, no stored starter rate, no production row.
- **Forecast:** `p_raw` and `p_cal` are always from the PRODUCTION row (the CAL map was fitted on production P(over));
  `model_source` is "production". A fresh AVG row's P(over) is `p_avg_raw`, display only. `top_source.choose_primary` repeats the
  export's primary rule (the layering test forbids importing `nba.export`); a parity test pins it.
- **Calibration:** the frozen LINE_CALIBRATION CAL map (`line_cal.py`, `line_cal_map_v1.json`: per book x stat, fit through
  2025-06-30, seasons 2023-2024 only, sha in the file, rebuilt by `python -m nba.parlay.line_cal_build`).
- **Search:** one leg per (book, player, stat); each book pruned to the top K = 40 legs by `p_cal`; every 2- and 3-leg ticket of
  the pruned set. Same-game legs share one seeded Gaussian-copula draw per game with the pair correlations of
  `data/parlay/joint_model.json` (its sha256, fit seasons and n are printed in `diagnostics.joint_model`); different games multiply.
- **Shown tickets (M5, default applied by the main session pending Devin's call):** within a size, each leg appears in at most
  one shown ticket, greedy by rank. The shown tickets are therefore not strictly "the three most probable"; rank 1 is.
- **`same_game`** is true when ANY two legs share a game. Such a ticket's EV is "not a book price" (books price same-game
  parlays with their own adjustment) and never opens the verdict.
- **Price and fee (B2):** book implied = product of the legs' implied probabilities. A sportsbook parlay carries NO separate fee
  (the margin is inside the legs' prices): EV per dollar = `p / implied - 1`. A ticket at a venue in `top_parlays.fee_venues`
  (configs/parlay.yaml, `kalshi`) pays the verified fee object once, on its own price. The 80% range is configured logit-scale
  noise on a pseudo n of 1000, not a tested residual of the joint.
- **Verdict / gate (M4):** `keep_your_money` unless the gate (30 settled, 14 dates, skill > 0; bars fixed in code per the
  contract) is open AND a book-product ticket has `ev_low80 > 0`. The gate is shut (`Gate()`) until the top-parlays panel has
  its own settled paper log; the Kalshi single-leg shadow log measures a different product.
- **Text:** deterministic templates from the leg's own fields; numguard and the banned-phrase screen run on every free-text field
  (`why`, `reason`, `caveats`, `ranking`, `status`, `empty_reason`); a banned phrase fails at write time.
- **Fixture:** `python -m nba.parlay.top_history` rebuilds `app/fixtures/top_parlays_example.json` (2025-01-27, season 2024,
  DraftKings T-60, month-block CAL fit). It is real hindsight data with `illustration: true`, which the contract defines as
  synthetic; it lives outside the app's served directory. `app/fixtures/top_parlays_producer_sample.json` is a committed
  synthetic producer run. Both are validated through the app's `TopParlays` model in `app/api/tests/test_top_parlays_producer.py`.
  Extra fields the app ignores: `p_avg_raw`, `drivers`, `fee`, `fee_model`, `ev_basis`, `hindsight`, `diagnostics`, `fixture`.
