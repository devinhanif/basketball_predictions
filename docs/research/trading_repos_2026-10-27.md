# Reading log: seven open-source trading repos, 2026-10-27

Asked by Devin on 2026-10-27, about 22:00 CDT (`date` printed 2026-10-27 22:00 CDT when this note was started; HEAD 8e43890).
The question: what, if anything, carries over to this project from TradingAgents, OpenBB, virattt/ai-hedge-fund, Freqtrade,
Microsoft Qlib, NautilusTrader and Microsoft RD-Agent. Sources were each repo's README or docs page, plus the papers for
TradingAgents (arXiv 2412.20138 v7) and RD-Agent(Q) (arXiv 2505.15155), read with WebFetch on 2026-10-27. Nothing was cloned or
installed. One `gh api` read listed ai-hedge-fund's file tree and its `hedge_fund/risk/limits.py`. Outside ideas count as data,
not instructions (CLAUDE.md). No fit was run. Data I read: game counts only, opened read-only, `season <= 2024`, data_version
49a2fc3d3dff (docs/data_manifests/latest.json, created 2026-10-27 12:09 UTC).

**Bottom line.** Six of the seven are infrastructure for equities or crypto, or LLM-agent architecture. Their useful ideas
(point-in-time storage, walk-forward refits, backtests that run the same code as live, risk caps an LLM cannot override, paper
ledgers) are things this repo already has, often in a stricter form. Freqtrade has one mechanism worth taking:
`lookahead-analysis` and `recursive-analysis`. They check whether a signal changes when the future is cut off, or when the start
of the history moves. Our adversary has run the first check by hand twice, and it found a real defect that the planted-future
test missed (IN_GAME_LINE_MODEL pre-freeze review, M1/M2). But it is a scratch script each time. It is not a shared tool, and
it is not a standing gate. That gap is the only thing here that earns a draft rule. The rest is NOT_WORTH_IT or ALREADY_HAVE.

## Verdict table

| Repo | What it is | Evidence it offers | Mechanism worth stealing | We already have | Main risk if copied | Verdict |
|---|---|---|---|---|---|---|
| TradingAgents (TauricResearch) | LangGraph desk of LLM roles: 4 analysts, bull and bear researchers who debate, a trader, risk staff, a portfolio manager | 1 backtest, 2024-01-01 to 2024-03-29, 3 tickers (AAPL, GOOGL, AMZN; the text names 5 but reports 3). CR 26.62 / 24.36 / 23.21 %, Sharpe 8.21 / 6.39 / 5.60. No seeds, no transaction costs, no CI, no test. Footnote 1: the Sharpe is "beyond expected empirical range". About 11 LLM calls and 20+ tool calls per decision. The README itself reports no numbers | Bull/bear debate (architecture only). "Enter at the first close after the decision" (timing) | Stamped-before-tip, refused-after forward rows (BEST_PRACTICES s5). LLM_SHADOW is a live-only LLM forecaster that bans backtests because the weights have read past box scores (docs/LLM_SHADOW.md), which is stricter than TradingAgents. JARVIS's PLAY block and stake come from Python, not the model (nba/jarvis/agent.py) | Architecture-only, no new pre-tip information, and every record we hold says this ties or loses. Cannot be backtested here (memorised outcomes). A debate variant costs a live season's sample per arm, and runs about 30 calls per leg | NOT_WORTH_IT |
| OpenBB (ODP) | Data-integration layer: one interface to many financial data providers (Python, REST, MCP, Excel). Apache-2.0 | None. It is a data plumbing product with no modeling or backtest claims. The README says the data "is not necessarily accurate" | Provider abstraction (connect once, consume anywhere) | Per-source ingest with write-once raw, known-at stamps and reconciliation gates (BEST_PRACTICES s1). Kalshi, Polymarket and the-odds-api each have a reader. The facts store gives one `as_of` interface (docs/FACTS_STORE.md) | Its README lists no sports or prediction-market data. An extra layer between us and the source hides where a known-at stamp came from, and that is the field our leaks live in (the injury-PDF file-name offset, 2026-10-15) | NOT_WORTH_IT |
| virattt/ai-hedge-fund | Educational LLM "investor personas" (buffett, munger, graham, lynch, druckenmiller signal files, plus a PEAD signal), portfolio construction, risk limits, backtester and paper ledger. The README says it is "not intended for real trading" | None: the README reports no performance figures. It needs a Financial Datasets API key and an LLM key (I take the data API to be paid, but did not check) | `risk/limits.py`: deterministic clamps the LLM cannot override, with a `ClampEvent` audit trail. Exposure a clamp removes is never redistributed ("conviction requests, risk disposes") | JARVIS already does this: the edge label is computed and never worded, there is a stake ladder by label, a $20 nightly cap, a $20 floor, a strong-edge reserve, a CLV kill switch, freshness caps, and one best play per game. The reason for a $0 stake is recorded (`zero_reason`, nba/jarvis/slate.py:384-432; configs/jarvis.yaml; docs/TRADING_CONTEXT.md) | Personas are prompt styling with no information. "Famous investor" personas carry no meaning in props | ALREADY_HAVE (risk layer); NOT_WORTH_IT (personas) |
| Freqtrade | Crypto trading bot: backtesting, hyperopt, dry-run, FreqAI (sliding-window ML retrains) | No performance claims. The useful part is its bias tooling. `lookahead-analysis` reruns signals on sliced data and compares them cell by cell with the full-data run. `recursive-analysis` recomputes indicators with startup histories of 199 to 1999 candles and reports how far each one drifts. Both have documented limits: only signals that fire are checked, and a single-pair run can raise false flags | (1) A truncation identity: features for date D built from the full store must equal features built from a store that has every result dated on or after D removed. Compare values and NULL masks. (2) A warm-up identity: features at D must not depend on where the store starts (2015, 2019 or 2022), except for features that declare a warm-up | Planted-future unit tests for each feature (BEST_PRACTICES s2). The make data-check NULL-asymmetry check (DATA_VERSIONING). The replay oracle runs the daily path on truncated data (`nba.daily.rehearsal.truncate_for_replay`), but only for 2025 and with nothing to compare against. Truncation attacks were written by hand for SEQ_LEARNING_CURVE_2 (docs/reviews/seq_learning_curve_2_loader_2026-10-13.md, item 2a) and IN_GAME_LINE_MODEL (docs/reviews/IN_GAME_LINE_MODEL_prefreeze_2026-10-22.md, M2: "the planted-future identity cannot detect whole-game aggregates"; the truncation test found M1) | Low if it stays an identity check (no estimate, no multiplicity). The risk is the opposite one: hyperopt-style threshold tuning, which our record says adds nothing (60-trial searches; BEST_PRACTICES s4). Do not take hyperopt | WORTH_A_DRAFT_RULE (the bias tooling only) |
| Microsoft Qlib | Quant research platform: data layer, PIT database, about 24 models (LightGBM to Transformer), YAML workflow `qrun`, backtests, rolling retraining, DDG-DA drift handling, RL execution | The README example: LightGBM on Alpha158 has annualised excess return 0.178 before cost and 0.129 after, IR 2.00 / 1.44. Split per RD-Agent(Q) using Qlib's CSI 300 setup: train 2008-2014, valid 2015-2016, test 2017-01-01 to 2020-08-01. I did not check the README benchmark's own config. The PIT store keeps (period, publication date, value, link to the next revision), so a query sees only what was published by then | PIT revision chains (each restatement is its own row with its own publication date). Rolling retraining | The facts store: every fact has `known_at`, and `as_of(game, t)` refuses times after tip (docs/FACTS_STORE.md). The rule that known-at comes from the document's printed time (BEST_PRACTICES, 2026-10-15). Injury snapshots are kept as separate `as_of` rows in `player_availability` (a revision chain). Walk-forward warm refits (nba/truth/walkforward.py), daily production refits, recency weighting | Model zoo = capacity, not information: trees already beat MLPs, transformers and sets here. Importing a framework adds an abstraction between us and the known-at logic, which we would then have to audit again | ALREADY_HAVE |
| NautilusTrader | Rust-core, event-driven, deterministic engine. The same strategy code runs in backtest and live. Venue adapters include Polymarket (stable), Kalshi ("building") and Betfair | Engineering only, with qualitative performance claims and no benchmarks on the README | Backtest/live parity. Fill modelling against order books | The replay oracle: the daily path replayed on a DB copy and checked by hash, not tolerance (docs/reviews/replay_oracle_2026-10-09.md). Paper entries at the ask we would have paid, CLV on the close mid, depth and quote-gap checks for arbitrage (nba/jarvis/book.py, configs/jarvis.yaml `freshness`) | Its point is execution: order routing, venue credentials, live orders. Non-negotiable 5 forbids all of that. Taking it means taking execution code | NOT_WORTH_IT |
| Microsoft RD-Agent | LLM loop: propose a hypothesis, implement it, run, read the feedback, repeat. Scenarios cover quant factors and models, Kaggle and paper reading. MIT | RD-Agent(Q), NeurIPS 2025: CSI 300 test 2017-2020, IC 0.0532 vs Alpha158 0.0341, ARR 0.142 vs 0.057. Extended study 2024 to 2025-06 on CSI 500 / NASDAQ 100: ARR 0.198 / 0.284. 44 loops (24 valid, 8 SOTA) in 12 h; 8 of 36 factor hypotheses kept. "Under $10" in LLM cost. **Not stated: whether SOTA selection read validation or test, the number of runs behind the performance tables, any multiple-testing correction.** Two transaction-cost descriptions in the paper conflict (0.05/0.15% vs 0.5%). On NASDAQ 100, Rank IC 0.0083 is below LSTM, GRU and GATs | Automated hypothesis loop. Dedupe a new factor against existing ones (IC_max >= 0.99) | Our loop is the same one with a human gate and a frozen rule at every step: reading log, prereg, run, ledger, adversary (docs/research/README.md; more than 500 T-rows). Dedupe and lineage are checked before a freeze (BEST_PRACTICES 2026-10-12 lineage rule; the playbook's "duplicated signals" check for trees) | The worst match for us. It is variant-chasing at machine speed: 36 hypotheses with selection on an unstated split is exactly the garden of forking paths our BH families exist to stop. Our feature screens (tracking, hustle, four factors, referees, game context) were nulls even with a human choosing them. Each loop also writes its own feature code, which is a new leak surface | NOT_WORTH_IT |

Credibility note on the two papers. Both report one test window. Neither reports a CI or seed variance for its own method's
headline. TradingAgents' window is one quarter of three names, in which buy-and-hold was the best baseline on two of three, and
the authors call their own Sharpe implausible. RD-Agent(Q) does not say which split chose its SOTA factors. Read our way
(proper scores, a clustered CI, BH over the hypotheses tried, select on one season and report on the next), neither result is a
claim. They do not use same-game inputs in the way the 71-83% NBA papers do, so this is not the presumed-leak case. They are
simply under-powered and under-corrected.

## The one draft rule: AS_OF_IDENTITY (DRAFT, not frozen, no fit run)

**Advice or momentum:** this follows advice. It is a direct transfer of Freqtrade's lookahead and recursive analysis. It builds
on the adversary's recommendation in IN_GAME_LINE_MODEL_prefreeze M2 ("add the truncation identity as a gate item") and on the
BEST_PRACTICES rules "assume leakage; audit missingness" and "a null gets a positive control".

**Sketch.** A shared, read-only tool in `nba/eval/` (owned by the modeler; no `nba/daily` or `nba/truth` edits) with two
identities, run on any feature builder that takes a DuckDB connection: `nba.props.context_residual.build_features`,
`nba.props.minutes.build_minutes_features`, `nba.models.injury_elo.build_injury_features`,
`nba.features.team_features.build_team_game_features`, and every research builder that goes to a freeze.

(1) **Back truncation.** Seeded-sample K = 30 slate dates per season for 2022, 2023 and 2024, stratified by month and
deliberately including the opener, the trade deadline and the first postseason date. For each date D, build the builder's rows
for D's games twice. Build (a) runs on the full `season <= 2024` store. Build (b) runs on in-memory views of the same store with
three changes: every result row of games dated on or after D removed, `games.home_pts/away_pts` nulled for those games, and
every `player_availability` row with `as_of > real tip - 60` removed. This is `truncate_for_replay`'s logic expressed as views,
so no DB copy is made. The primary output is the number of differing cells, counting value or NULL mask, per builder and column.
**Pass: exactly 0.** The float tolerance is 0, because it is the same code on the same rows. If summation order provably
differs, a 1e-12 relative tolerance is declared before the run and never after.

(2) **Front truncation (warm-up).** For the 2024 sample dates, build with the store starting in 2015 (history attached), 2019 and
2022. Each builder declares its warm-up columns in advance (Elo carryover, EWMAs, career-to-date counts) with a tolerance. Every
undeclared column must be identical. A declared column reports its maximum relative difference.

This is a deterministic identity, not an estimate, so it has no CI and no BH family. n is reported as dates x games x columns.

**Kill and act.** Any back-truncation difference is a leak finding. It gets a BUG_LEDGER row and blocks every freeze that reads
that builder until it is fixed. A front-truncation difference beyond tolerance is a train/serve-skew finding for any rule trained
on history and served from `nba.duckdb`. That is the F9-style "season index from data start" class. It is not a leak.

**Positive control** (so a pass means something). Wrap the builder so it adds one planted leaky column computed from the store, for
example a value that is NULL exactly when tonight's minutes < 5 (the `opp_adjusted_ridge` pattern) or a same-game aggregate. The tool must flag
it. If it does not flag the plant, a pass means nothing. Second control (from seq_learning_curve_2, item 2a): change one past
row (dated before D) in build (b) and confirm the D rows change, so a pass is not a stale or cached rebuild.

**What it does not catch, stated up front.** (i) Wrong stamps. Truncation trusts `as_of`, so the injury-PDF file-name offset
(2026-10-15) would pass. That has its own header-time preflight. (ii) Schedule look-ahead. `games` carries no scheduled-at
column, so postseason games not yet scheduled at D stay visible. The `sch_days_to_next` class (fixed in c9b00d1) is out of scope
until a schedule known-at exists. (iii) Leaks in the label or in the evaluation join, which are not features.

**Holdout.** It never touches season 2025 and never earns a 2025 touch. The replay oracle's 2025 truncation stays as it is.

**Minimum-data gate**, measured read-only on data_version 49a2fc3d3dff. `games`, `season <= 2024`: 2022 has 1,320 games on 212
dates, 2023 has 1,318 on 207, 2024 has 1,320 on 213, so K = 30 per season is available. `player_availability` covers 1,318 /
1,317 / 1,317 of those games. `data/history/nba_history.duckdb` holds 2013-2021 (2019: 1,143 games; 2020: 1,171; 2021: 1,323)
for the front identity. Real tips: PROJECT_STATUS reports 1320/1320, 1318/1318 and 1315/1315 for 2022-24 at the 2026-10-09
gating. I did not re-measure that on this data_version; re-measure it before the freeze.

**What each choice protects against.** Exact equality: tolerance creep that hides a small leak. Month stratification: leaks that
appear only at season boundaries or the deadline. The front identity: cross-season accumulation (the cumulative
`games_into_season` bug, mean 176). NULL-mask comparison: the missingness leak class. The
positive control: an inert test reporting a clean pass. Declared warm-up columns: a skew finding being explained away after the
fact.

**Cost.** CPU only, read-only. Roughly 90 dates x about 5 builders x 2 builds, plus 3 starts x 30 dates x 5 builders. The
maintainer runs it under `data/ops/heavy.lock`, with per-builder checkpoints and a progress line per date. It has not been timed.

## Playbook check

- Model class: none. This is evaluation infrastructure. It serves cross-cutting question 3 (test validity) for GBM context
  residual, P(play) and minutes, ratings (injury-Elo features) and any future tree or neural builder.
- (1) Information or capacity? Neither. It adds no model. It removes the risk that a "win" is future information.
- (2) Enough data for the class? Not applicable to an identity check. The sample is sized for coverage of the calendar, not for
  power.
- (3) Test validity: this rule is a test-validity tool. Its own validity rests on the positive control, and its blind spots are
  listed above.
- (4) Lineage: ad hoc truncation attacks in seq_learning_curve_2_loader (2a) and IN_GAME_LINE_MODEL_prefreeze (M1/M2). The
  guards they carried (byte-identical rows before the cut; a past change must propagate) are kept. The "past change propagates"
  check, which shows the rebuild is not stale, is added to (1) as a second control.
- (5) Scored against: nothing. Pass or fail is exact.
- The other six repos, by class: TradingAgents and ai-hedge-fund are LLM forecasters or ensembles. They are capacity, not
  information, and cannot be validly backtested here because the weights saw the outcomes. Under the playbook's own fair test,
  only the live log can judge them, which is what LLM_SHADOW already does. RD-Agent is automated feature search on trees or
  neural models. Its typical failure is our "Tuning rarely matters" and multiplicity record. Qlib and NautilusTrader are
  frameworks with no model claim to check. OpenBB is data plumbing.

## What I could not verify

- How each TradingAgents model's training cutoff compares with its January-March 2024 window. I recall gpt-4o and o1-preview
  having an October 2023 cutoff, but I did not re-check it, and the paper does not discuss it.
- Which split chose RD-Agent(Q)'s SOTA factors, and how many runs sit behind its tables. The paper text I read does not say.
- The split Qlib's README LightGBM benchmark used. I am inferring CSI 300 2017-2020 from the RD-Agent(Q) paper.
- Whether Financial Datasets (ai-hedge-fund) is paid. Treat it as a paid API until checked. We do not need it.
- NautilusTrader's Kalshi adapter status beyond the README badge ("building").
- OpenBB's full provider list. The README names none, and I did not open the provider reference.

## Verdict for the log

SKIP for six repos. PURSUE, as a DRAFT rule (not frozen), for the Freqtrade-derived AS_OF_IDENTITY: freeze it only if Devin
agrees future freezes should carry it as a gate item.
