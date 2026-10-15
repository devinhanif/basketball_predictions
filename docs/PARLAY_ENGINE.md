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

## Context and cross-leg dependence (sizing, 2026-10-14; `reports/parlay/context_copula_sizing.{md,json}`)
Devin's idea (2026-10-14): a feature that is real but does not improve a player's marginal forecast is redundant for the marginal, but may change the correlation BETWEEN legs. Gate sizing and inventory only: nothing frozen, no rule, live path untouched.
- **Is the production joint conditioned on context?** No. `joint.py` pools one correlation per relation (same player / teammate / opponent) x stat pair, plus `own_margin` and `game` terms; `combo_copula.py` fits one 6x6 matrix. COMBO_STOCKS_SHADOW already tried one conditioning (by position, by starter) on 2023 select data: by-starter gain 0.0008 < 0.005, so pooled was kept (docs/prereg/COMBO_STOCKS_SHADOW.md, "Correlation scheme").
- **Sizing data** (`context_data.py`): production-equivalent walk-forward marginals (same config, integer support) for seasons 2020, 2021, 2022 (79,286 played player-games, 3,814 games), trained only on earlier games from 2019-20; randomised PIT -> normal score, plus the hit indicator at the model's median line. 2023, 2024 and 2025 were never loaded (SQL `season <= 2022` and an in-memory refusal; tested). Build is the maintainer's run (about 4.5 minutes): `uv run python -W ignore -m nba.parlay.context_data --root <checkout with nba.duckdb and data/> --configs configs --out <dir>`; analysis: `uv run python -W ignore -m nba.parlay.context_report --data <dir> --out reports/parlay`.
- **Result.** 114 (pair class x context) cells; the game-clustered CI of the correlation difference excludes 0 in 36 (normal-score scale) and 25 (hit scale) against about 5 and about 10 under a label-permutation placebo, so there is real structure and it is small. Largest |phi difference| 0.051; the largest swing in a typical parlay's joint probability between a context and its complement is +6.1% (3 legs; +9.0% with every pair at the far end of its CI), against a required lift of +5.3% for the Kalshi taker fee alone, +14.8% for the measured 2-leg parlay loss (T490), and +13.9% for its least costly cell. Not a parlay-moving effect. The pooled copula already carries the first-order dependence (same-player points-rebounds-assists three-leg: +55% over independence); context is a second-order modulation of +-2 to +4% against the pooled engine.
- **Where the shifts are** (phi, context minus complement): team leader OUT raises same-player points-assists +0.045, points-rebounds +0.023, assists-rebounds +0.026; two top-3 teammates: points(A)-assists(B) +0.024 (+0.032 with leader OUT), assists-assists -0.025; underdog vs favourite: a player's rebounds track his team's result +0.072 vs +0.021; blowout, opponents, same-stat teammate points/rebounds/threes: nothing. The Trae-Young cell (assist leader OUT, remaining top-2 assister): points-assists phi +0.010 [-0.023, +0.042].
- **Do books price it?** Not checkable: no API carries same-game parlay prices (docs/research/lines_everywhere_2026-10-14.md) and no combo contract is ingested here (combos are priced at the product of leg asks and marked not tradable). The check needs forward SGP or combo quotes next to settled outcomes; the paper log from 2026-10-20 is where it would come from.
- **Verdict:** no CONTEXT_COPULA rule is drafted: the largest plausible shift is below every measured cost, and the expected log-loss saved per parlay by conditioning (3e-5 to 2e-4 nats inside the context segment) is below what the 2023-24 joint evaluation could resolve.
- **Inventory** of closed nulls with a real descriptive association (one line each; which cross-leg pairs it could touch):

| closed null / lead | real association (descriptive) | marginal result | could change cross-leg correlation? |
|---|---|---|---|
| WOWY_REDISTRIBUTION gate (branch agent/modeler-wowy) | with/without delta carries kappa pts 0.037 (se 0.008), reb 0.038, ast 0.048 (pooled 2020-22); USAGE version ast 0.126 (0.013), leader-out ast 0.202 (0.017) | expected gain -0.0016 vs floor -0.005; coefficient shrinks inside size-chosen slices | teammates, same stat and cross stat (ast-pts) among absorbers; MEASURED here: leader OUT teammates points-points +0.004, assists-assists +0.001 phi (nothing) |
| T476 returning teammate / tanking (RETURNING_TEAMMATE draft failed its power gate, DECISIONS 2026-10-12) | rotation teammates of a returning 30+ min star -0.81 pts [-1.06, -0.55] (n 2,039); +0.47 [+0.30, +0.65] while he is out (n 6,597); early bottom-5 teams -0.92 [-1.53, -0.26] | not run (gate); FULL does not close it | a team-wide shock: teammates, same stat, positive co-movement in return/regime games; not sized (no standings or return flags built for 2020-22) |
| T042 lineup archetype mix (R2 0.016, perm p .004) with F8/F11 (T244-T254, null) | non-additive lineup effect; oracle lineup gains are large (F11 A3 pts -0.0598, F8 A4 reb -0.016) | as-of A1 -0.0001 to +0.0016, CI spans 0 | teammates (who plays together is a shared unknown at T-60); the pooled teammate/same-player correlation is its footprint |
| TEAM_BUDGET T446-T448 (240-minute budget) | minutes are zero-sum; oracle shares pts -0.180 | raking HARMS (+0.10 pts) | teammates same stat, starters vs bench (negative); measured pooled teammate phi -0.011 (points), vacated context +0.002 |
| GAME_MARKET_CONTEXT T389-T391 (spread / total as features), atlas 2026-10-11 | player x total phi +0.070 [+0.062, +0.078]; underdog player over x own team covers +0.144 vs favourite +0.055 (Pinnacle, 2023-24) | +0.0033 [+0.0002, +0.0064] / +0.0005 | player x own margin, player x total; already pooled in the joint; MEASURED here (2020-22, Elo margin): rebounds +0.072 underdog vs +0.021 favourite, points +0.049 vs +0.069 |
| PACE_TOTALS T472, pace_periods A | pace is in the line (corr 0.78); residual partial r +0.004 to +0.007 | blend weight 0 | player x total (in the joint); no pace association with the player residual to size |
| vacancy (production `vac_*` columns) | injury_vacated +0.0245 pts CRPS when removed | in the model (not a null) | VAC24 alone: same-player points-rebounds +0.020, points-assists +0.017, rest within +-0.01 |
| referees T238-T243 | none (CIs span 0, G0 repaired) | null | not eligible: no association to size |
| rest / back-to-back, opponent adjustments (T034-T041, ridge) | rest_b2b reb +0.0012 [+0.0005, +0.0020]; opponent groups small | pts +0.0005 n.s.; opponent harm/zero | opponents same stat: measured phi +0.008 to +0.012, no context shift (every CI spans 0) |

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

## Context-conditioned copula as a probability question (PARLAY_CONTEXT_PROB, draft, NOT frozen, 2026-10-14)
Devin reopened the context copula as "is the shown joint probability more accurate" (DECISIONS 2026-10-14 20:31). The draft rule is
`docs/prereg/PARLAY_CONTEXT_PROB.md`; the minimum-data gate (`nba/parlay/context_prob_gate.py`, `reports/parlay/context_prob_gate.md`) FAILED:
all 114 cells have enough pairs (>= 1,045 pairs, >= 342 games in the smallest), but at the 2020-22 effect the best cell moves the displayed
joint probability by about 0.6 points (largest median over cells 0.7) and gains about 1e-4 nats per treated pair; upper-bound power for one
report season is 0.24 (0.12 at half effect), 0 of 114 cells reach 0.80. Not frozen; no contrast computed; season 2025 never read.

