> Historical: the original project spec (2026-10-05). Superseded by CLAUDE.md on 2026-10-09; kept for the record. Where it conflicts with CLAUDE.md, CLAUDE.md wins.

# CLAUDE.md — NBA Possession-Level Prediction System

## Mission
Predict NBA game outcomes (win prob, spread, total, player lines) by simulating games possession-by-possession, and backtest every layer against actual box scores, play-by-play, and closing lines. Optimize for **calibrated probabilities** (Brier, log loss), not raw accuracy.

## Success criteria (agreed definition of "done")
Per-game point predictions cannot be within 0.5 (single-game player stats swing by several points). Success is therefore defined as:
1. **Mean bias within +/-0.5** for points, rebounds, assists (and PRA / pairwise combos) averaged over >=500 player-games per stat. Tested with a bootstrap CI that must include 0.
2. **Calibrated distributions:** for threshold events like P(points >= N), predicted probability vs. observed frequency within a tolerance set in config (reliability diagram plus expected calibration error).
3. **Beats naive baselines** (season average, last-10 average) on CRPS and log loss, using paired per-game bootstrap comparisons, not eyeballed deltas.
4. **Ships as a real pipeline:** CI/CD, tests, reproducible runs, versioned models (see "CI/CD").
5. **Parlay tool is honest:** it reports expected value after fees with an uncertainty band, and says "no positive-EV parlay found" when that's the truth.
Stretch (not required): approach the market's closing-line calibration on props where Kalshi/sportsbook prices exist.

## Hard constraints
- **Fully local, cheapest possible.** DuckDB + Python + PyTorch + `nba_api`. No cloud services, no paid APIs, no always-on infra.
- **No leakage.** Every feature is computed `as_of` the game date. All validation is walk-forward (train on past, test on future). Never random splits.
- **Every model change is backtested** and compared to the previous rung of the ladder before it's kept.
- Keep dependencies minimal. Prefer stdlib/DuckDB SQL over new packages.
- Deterministic: seed everything, log the seed.
- **Read-only with respect to markets.** The system never places orders, never stores trading credentials, and has no execution code. It outputs analysis and paper-trade logs only. Not financial advice; the README must say so.
- **Do not copy employer code, data, table names, or configs.** Registry patterns are re-implemented generically (see Registry retrofit).

## Stack
Python 3.11+, DuckDB, polars, numpy, scipy, scikit-learn, statsmodels, LightGBM, PyTorch (CPU is fine), `nba_api`, `httpx` (Kalshi, read-only). Dev/CI: pytest, hypothesis, ruff, mypy, pre-commit, gitleaks, uv (lockfile), Docker, GitHub Actions.

## Repo layout
```
nba/
  data/            # raw pulls, cached as parquet; never refetch what's cached
  db/              # nba.duckdb + schema.sql
  ingest/          # nba_api pullers (rate-limited, resumable)
  parse/           # play-by-play -> possessions, stints
  features/        # as-of feature builders (SQL-first)
  coldstart/       # priors, embeddings, shrinkage (see below)
  models/          # one module per architecture rung
  sim/             # possession Monte Carlo engine
  eval/            # walk-forward backtest, calibration, reports
  registry/        # adapter layer (see "Registry retrofit")
  props/           # per-player stat distributions (Phase 2)
  kalshi/          # read-only market data ingestor (Phase 3)
  parlay/          # joint-probability + EV engine, paper-trade log (Phase 3)
.github/workflows/ # CI/CD (see "CI/CD")
  configs/         # yaml per experiment
tests/
```

## Data schema (DuckDB)
```sql
CREATE TABLE games (
  game_id VARCHAR PRIMARY KEY, game_date DATE, season INT,
  home_team INT, away_team INT, home_pts INT, away_pts INT
);
CREATE TABLE possessions (
  game_id VARCHAR, poss_idx INT, period INT,
  clock_start FLOAT, clock_end FLOAT,
  off_team INT, def_team INT,
  off_players INT[5], def_players INT[5],
  score_diff INT,
  outcome VARCHAR,            -- FGM2, FGM3, FGA_miss, TOV, FT_trip, other
  shooter_id INT, shot_zone VARCHAR,   -- rim, mid, corner3, above3
  assister_id INT, oreb BOOLEAN, fta INT, pts INT,
  PRIMARY KEY (game_id, poss_idx)
);
CREATE TABLE stints (
  game_id VARCHAR, team_id INT, period INT,
  start_clock FLOAT, end_clock FLOAT, players INT[5]
);
CREATE TABLE player_rates (          -- as-of snapshots, one row per player per date
  player_id INT, as_of DATE,
  usage FLOAT, tov_rate FLOAT, ft_pct FLOAT, foul_draw FLOAT,
  zone_mix FLOAT[4], zone_fg FLOAT[4],
  orb_rate FLOAT, drb_rate FLOAT, min_per_g FLOAT,
  n_poss INT,                        -- sample size, drives shrinkage weight
  source VARCHAR                     -- 'observed' | 'blended' | 'prior'
);
CREATE TABLE team_context (
  game_id VARCHAR, team_id INT, rest_days INT, b2b BOOLEAN,
  travel_miles FLOAT, injured_out INT[]
);
CREATE TABLE players_static (        -- cold-start inputs
  player_id INT PRIMARY KEY, position VARCHAR, height_in FLOAT, weight_lb FLOAT,
  birth_date DATE, draft_year INT, draft_pick INT, college VARCHAR,
  college_stats JSON                 -- nullable; per-100 poss where available
);
CREATE TABLE player_game_stats (     -- actual box score lines, label source for props
  game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT,
  pts INT, reb INT, ast INT, fg3m INT, stl INT, blk INT, tov INT, starter BOOLEAN
);
CREATE TABLE prop_predictions (      -- model output, one row per player-game-stat
  run_id VARCHAR, game_id VARCHAR, player_id INT, stat VARCHAR,   -- pts|reb|ast|fg3m|pra...
  mean FLOAT, dist_family VARCHAR, dist_params JSON,
  p_ge JSON,                         -- {"10":0.93,"15":0.71,...} P(stat >= N)
  q10 FLOAT, q50 FLOAT, q90 FLOAT, made_at TIMESTAMP
);
CREATE TABLE kalshi_markets (        -- read-only ingest
  ticker VARCHAR PRIMARY KEY, series_ticker VARCHAR, event_ticker VARCHAR,
  title VARCHAR, player_id INT, stat VARCHAR, threshold FLOAT,   -- "N+" contracts
  open_time TIMESTAMP, close_time TIMESTAMP, settled_ts TIMESTAMP, result VARCHAR
);
CREATE TABLE kalshi_prices (         -- candlesticks / snapshots
  ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT, last FLOAT,
  volume INT, open_interest INT, source VARCHAR    -- 'live' | 'historical'
);
CREATE TABLE paper_trades (          -- parlay tool log; settles against actuals
  trade_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, legs JSON,
  model_prob FLOAT, model_prob_lo FLOAT, model_prob_hi FLOAT,
  price FLOAT, fee_model VARCHAR, expected_value FLOAT,
  settled BOOLEAN, outcome BOOLEAN, realized_pnl FLOAT
);
CREATE TABLE experiments (           -- local registry fallback, see below
  run_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, rung INT, model_name VARCHAR,
  config JSON, metrics JSON, artifact_path VARCHAR, stage VARCHAR
);
```
Validate possession counts against box-score estimate `FGA + 0.44*FTA + TOV - OREB` per team-game; fail the ingest test if the mean error exceeds 1 possession.

## Cold start modeling
Cold start = anything where observed sample size is too small to trust. Handle it explicitly, as a first-class module, not an afterthought.

### Cases
1. **Rookies / no NBA history**
2. **Low-minute players** (noisy rates)
3. **Traded players / new team** (role and usage shift)
4. **New season** (aging, offseason change, roster turnover)
5. **Unseen lineups** (5-man combos with little or no shared minutes)
6. **Early-season team strength** (first ~10-15 games)

### Methods (implement in this order, each behind a flag, evaluate each)
1. **Empirical-Bayes shrinkage (baseline, must ship first).** For each rate `r`, posterior = `(n*r_obs + k*r_prior) / (n + k)`. `n` = possessions observed, `k` = pseudo-count tuned per stat by walk-forward CV. Priors come from position/archetype group means.
2. **Archetype priors.** Cluster players (k-means or GMM) on height, weight, position, and prior-season tendencies. Prior for a new player = their cluster's mean. Pick cluster count by backtest, not by eye.
3. **Rookie priors from draft and college.** Ridge or small GBM mapping `draft_pick, age, height, position, college_stats` to first-season rates. Fall back to archetype prior when college data is missing. Draft slot is the strongest single input; do not skip it.
4. **Time-decay carryover for returning players.** Blend last season's rates with a regression-to-the-mean term and an age curve. Tune decay and carryover weights by backtest.
5. **kNN similar-player prior.** Nearest neighbors in the learned player-embedding space (rung 5 models), averaged and shrunk toward the archetype prior.
6. **Set-based lineup encoder for unseen lineups.** Lineup vector = permutation-invariant function (DeepSets or small attention) over the 5 player embeddings, so any combination is representable without having been seen. New players get an embedding from methods 2-5 until they have enough possessions.
7. **Team strength warm start.** Initialize each season's team rating from the sum of expected player contributions weighted by projected minutes, blended into Elo/ratings with a weight that decays as games accumulate.

### Required behavior
- Every `player_rates` row carries `n_poss` and `source`. Models receive `log(1+n_poss)` as a feature so they can learn how much to trust a rate.
- Report **backtest metrics split by cold-start bucket** (e.g., games where >=1 starter has <500 career possessions vs. not). A model that wins overall but loses on cold-start games is not done.
- Unit-test that shrinkage converges to the observed rate as `n` grows and to the prior as `n -> 0`.

## Architecture ladder
Climb one rung at a time. A rung is kept only if it beats the previous on **walk-forward log loss** (and does not worsen calibration). Log every rung to the registry, including failures.

| Rung | Model | Purpose |
|---|---|---|
| 0 | Baselines: home-court-only, Elo, closing-line implied prob | Floor and ceiling to beat/approach |
| 1 | Logistic / ridge on team-level as-of features (ratings, rest, b2b, travel, injuries) | Cheap, strong, interpretable |
| 2 | LightGBM on the same + rolling efficiency (ORtg, DRtg, pace) | Nonlinear interactions |
| 3 | Possession Monte Carlo sim, one logistic/GBM head per step (duration, outcome, shooter, zone, make, rebound, FT) | Full-game distributions, player lines |
| 4 | PyTorch multi-head net with shared set-based lineup encoder (DeepSets) feeding the sim heads | Cold-start-friendly lineup modeling |
| 5 | GATv2-TCN: graph attention over on-court players plus temporal conv over game sequence | Non-additive interactions, existing design work |
| 6 | Stack/blend of the best rungs, then isotonic or Platt calibration on a held-out recent window | Final probabilities |

Notes:
- Rungs 3-5 feed the same sim engine; only the probability heads change.
- Sim outputs: run `N` games per matchup (start N=2,000, increase only if win-prob SE is too high). Report win prob, margin distribution, total, and player lines.
- Cheap-first: do not start rung 5 until rungs 1-4 are backtested and logged. Expect most of the gain from rungs 1-3 plus good cold-start handling.

## Evaluation
- **Walk-forward by date**: train through day D, predict day D+1 (or week blocks), roll forward. Minimum 3 full seasons of test.
- Primary: **log loss, Brier score**, calibration curve (reliability diagram), and log loss vs. closing-line implied probability.
- Secondary: accuracy, spread MAE, total MAE, margin SD (target ~12), pace within ~1 poss/team, 3PA rate / TOV% / OREB% within ~0.5 pt of actual.
- Always output metrics sliced by: cold-start bucket, home/away, rest, season phase (first 15 games vs. rest), and favorite/underdog.
- Compare vs. closing line explicitly. Beating it consistently is very hard; treat "close to it with better calibration in cold-start slices" as a win.
- Emit a single `report.md` per experiment with the tables above.

## Registry retrofit (port the work registry's patterns, not its infrastructure)
The work registry (`spp-tonnage-forecast-pipeline`) is a **Snowflake Model Registry** (`snowflake-ml-python`, key-pair auth, warehouse compute). Connecting this project to it would cost warehouse credits, require employer credentials, and put personal work on employer infrastructure. **Do not connect to it. Do not copy its code or any SPP table/column names, configs, or data.** Re-implement the *design patterns* below in a local adapter. That reuses what already works at the lowest cost: zero infra, zero credits.

### Patterns to port (generic ideas, re-implemented from scratch)
| Work registry pattern | Local equivalent |
|---|---|
| `ForecastModel` ABC: `predict()`, `get_metrics()`, `get_config()` | Same 3-method contract for every rung in `nba/models/` |
| Auto-increment versions `v1, v2, ...` per model name | `next_version(model_name)` reading the `experiments` table |
| Aliases: `candidate` -> `production`; old production -> `deprecated` | `stage` column with the same three values; `promote()` auto-deprecates the previous production version |
| Promotion is an explicit flag, not automatic | `--register` logs a candidate; `--promote` is separate and manual |
| Backtest metrics stored on the registered version, queryable with SQL | Metrics JSON in `experiments`, queried with DuckDB |
| Audit metadata blob: data date range, row counts, lineage, runtime, timestamp | `build_run_metadata()`: game_date min/max, possession count, git SHA, seed, python/torch versions, GPU flag |
| Model-level tags for governance and discovery | `tags` JSON: `rung`, `model_method`, `cold_start_flags`, `source_config` |
| Standard CLI contract across models (`--register`, `--promote`, `--backtest`, `--debug`, `--scenario`) | Same flags on every `run_<rung>.py`; `--scenario` = what-if config tracked separately from production runs |
| `--scenario` and `--promote` cannot be combined | Keep this guard |
| Per-model isolated entry points | One `run_<rung>.py` per architecture; shared code lives in `nba/shared/` |

### Adapter
```python
class RegistryAdapter(Protocol):
    def next_version(self, model_name: str) -> str: ...
    def log_model(self, model_name: str, version: str, model_path: str,
                  metrics: dict, metadata: dict, tags: dict) -> None: ...
    def set_alias(self, model_name: str, version: str, alias: str) -> None: ...
    def promote(self, model_name: str, version: str) -> None: ...   # old production -> deprecated
    def load_model(self, model_name: str, alias: str = "production") -> str: ...  # local path
```
Backends via `REGISTRY_BACKEND`:
1. `local` (**default, the only one to build now**): DuckDB `experiments` table (add `model_name`, `version`, `alias`, `tags`, `metadata` columns) plus `./registry_store/<model_name>/<version>/` holding weights, config yaml, and `report.md`.
2. `mlflow` (optional later): only if wanted for a UI. Same protocol.
Do not build a Snowflake backend.

### Cheapness rules
- Log params, metrics, metadata, and small artifacts only (weights under ~100 MB). Never store raw data, parquet, or the DuckDB file in the registry store.
- One registered version per experiment config. Per-fold metrics go in one JSON blob.
- No serving, no scheduled jobs, no containers. Models load from disk into local scripts.
- Promotion requires the full multi-season backtest to pass; log failed rungs too.
- Never hardcode credentials; read env vars.

## Phase 2 — Player stat distributions (props)
Targets: points, rebounds, assists, 3PM, and sums (PRA, P+R, P+A, R+A). Output a full distribution per player-game, not just a mean.

- **Frequency-severity decomposition (borrowed from insurance):** model minutes x per-minute rate x efficiency instead of predicting the stat directly. Points = attempts (frequency) x make rate and value (severity).
- **Distribution families:** points ~ Normal or Gamma-shaped; rebounds/assists/3PM ~ Negative Binomial; fit dispersion per player archetype with shrinkage. Check zero-inflation for 3PM/blocks/steals.
- **Minutes model first.** Most prop error comes from minutes uncertainty (injuries, blowouts, rest). Model P(minutes) with a separate head, including a DNP/low-minute branch.
- **Role change detection (borrowed from manufacturing quality control):** Bayesian online changepoint detection or CUSUM on usage/minutes to flag trades, injury returns, and starter changes. On a flagged change, cold-start logic temporarily raises prior weight.
- **Quantile heads + conformal intervals:** train quantile regression (pinball loss) and wrap with split-conformal prediction so intervals have honest coverage without distributional assumptions.
- **Hierarchical coherence (borrowed from retail demand forecasting):** player-level predictions must sum to a coherent team total; reconcile with a top-down/bottom-up (MinT-style) step so player and team forecasts do not contradict each other.
- **Metrics:** CRPS, log loss on threshold events, pinball loss, calibration (ECE + reliability), mean bias (+/-0.5 target), and coverage of the 80% interval (should be ~80%).

## Phase 3 — Kalshi ingestion and parlay engine (read-only)
### Kalshi ingestor (`nba/kalshi/`)
- Public market data needs no authentication. Use the live endpoints for the recent window and the `/historical/*` endpoints for older settled markets and candlesticks; call `GET /historical/cutoff` at startup to know which tier to query. Merge into `kalshi_prices` with `source` marked.
- Pull NBA player-prop series (points, rebounds, assists, threes) and game markets; parse "N+" thresholds into `(player_id, stat, threshold)`. Name matching to `player_id` is fuzzy: build a reviewed alias table and fail loudly on unmatched names.
- Respect rate limits, cache everything, make pulls resumable and idempotent. Store raw JSON snapshots for replay.
- Do not request authenticated trading scopes. No order endpoints are ever called.
- Expect thin history (props are new and cover limited players). Report sample sizes with every comparison and refuse to claim superiority from fewer than the configured minimum number of settled markets.
- Odds from a second source (sportsbook props via a paid or free-tier odds provider) is an optional adapter behind a flag, for deeper history. No provider is hardcoded.

### Joint probability engine (`nba/parlay/`)
Build in this order, each behind a flag, compared on calibration of joint outcomes:
1. **Independence baseline:** product of marginals. Known to be wrong for same-game legs; kept as the floor.
2. **Gaussian copula over marginals (borrowed from finance/credit risk):** keep each leg's fitted marginal (NegBin/Normal), estimate a correlation matrix from historical same-game residuals (player-player and stat-stat within a game), and sample joint outcomes. Cheap, fast, and often captures most of the correlation. Use a t-copula check for tail dependence.
3. **Possession-sim joint (rung 3/4 sim):** read joint outcomes directly from simulated games. Use only if it beats the copula on joint calibration by more than the paired-bootstrap noise.

### EV and sizing
- **EV per parlay** = model joint probability x payout - cost, net of fees. The **fee model is a config object** (verify against Kalshi's current published fee schedule; do not hardcode a rate) and the bid/ask spread is included, not the midpoint.
- Report a **probability interval** (bootstrap or posterior samples), and compute EV at the low end as well. A parlay is only flagged if EV is positive at a conservative bound.
- **Market-as-prior (borrowed from election forecasting):** treat the market price as a strong prior and the model as an update. Report the model's deviation from the price and shrink toward the price in proportion to the model's out-of-sample track record on that stat. Disagreement with the market is a hypothesis to test, not a signal to trust.
- **Sizing guidance:** display a fractional-Kelly figure capped at a small fraction (config) with a plain-language caveat that estimated edges are noisy. This is informational only.
- **Output contract:** `{legs, model_prob, prob_interval, price, fees, ev, ev_low, verdict}` where `verdict` includes `"no_positive_ev_found"` as a first-class result.
- **Paper trading:** every recommendation is logged to `paper_trades` and auto-settled against actuals. Publish a rolling report: realized vs. expected return, calibration of joint probabilities, number of settled trades. Real-money use is out of scope for this repo.

## CI/CD (required deliverable)
**Pipeline (GitHub Actions; free-tier friendly):**
1. `lint-type`: `ruff` (lint + format check) and `mypy` on `nba/` (strict on `registry/`, `parlay/`, `kalshi/`).
2. `unit`: `pytest` with coverage gate (start at 70%, ratchet upward). Property-based tests (`hypothesis`) for shrinkage, distribution fitting (CDF monotone, probabilities in [0,1], P(>=N) non-increasing in N), copula sampling (valid correlation matrix), and EV math (fee and payout edge cases).
3. `data-contract`: schema tests on DuckDB tables with a tiny committed fixture dataset (a few games); possession-count reconciliation test; no-leakage test asserting every feature row has `as_of` <= game date.
4. `smoke-backtest`: run the full train/backtest/register path on the fixture in <3 minutes; fail if the report is not produced or metrics are NaN.
5. `model-gate` (on PRs touching `models/`, `props/`, `parlay/`): compare candidate metrics vs. the current `production` version in the registry; fail on regression beyond tolerance in log loss, CRPS, or calibration ECE.
6. `build`: CPU Docker image (pinned base, lockfile via `uv` or `pip-tools`), tagged with git SHA; run the smoke test inside the container.
7. `release` (on tag): publish image, attach `report.md` and registry metadata as release artifacts.
8. `nightly` (scheduled): incremental ingest (games + Kalshi snapshots), settle paper trades, regenerate the calibration report. Opens an issue if ingest row counts or calibration drift beyond thresholds.

**Repo hygiene:**
- `pre-commit` hooks (ruff, mypy, secret scan such as `gitleaks`, large-file block so DuckDB/parquet never get committed).
- `Makefile` targets: `make setup test lint backtest smoke report`.
- Secrets only via environment or GitHub Actions secrets. `.env.example` committed, `.env` ignored.
- Config-driven experiments (`configs/*.yaml`), every run logged to the registry with git SHA and seed.
- `README.md`: architecture diagram, quickstart (`make setup && make smoke`), data sources, disclaimer, results table (metrics with CIs, not point estimates).
- `docs/` with an ADR (architecture decision record) per major choice (why the copula before the sim, why local registry, etc.).

## Risks and guardrails (from engineering review)
1. **Sim is not automatically better.** Rung 3+ must beat rung 2 on win-prob calibration or be justified purely by player/joint outputs.
2. **Statistical power.** ~3 test seasons cannot separate close rungs. Use paired per-game bootstrap comparisons, report CIs, and keep one **frozen final holdout season** never used for tuning.
3. **No lineup cheating.** At prediction time use projected minutes and injury-adjusted availability, never the actual box score minutes/lineups. A test must prove it.
4. **Odds data availability.** Closing lines are not in `nba_api`. Kalshi history is shallow. State sample sizes in every market comparison.
5. **Parser risk.** Time-box the play-by-play parser; do not build on top of it until reconciliation tests pass.
6. **Scope control.** Cold start: ship shrinkage + draft/archetype priors first; add others only if cold-start slices show a measurable gain. GATv2-TCN is a late experiment, not on the critical path.
7. **Edge is probably small or negative.** The tool must be comfortable reporting that.

## Optional experiments ("borrowed techniques", only after Phase 2 is stable)
| Idea | Origin industry | Use here |
|---|---|---|
| Gaussian/t-copula joint outcomes | Credit risk, structured finance | Cheap correlated parlay probabilities |
| Frequency-severity decomposition | Insurance/actuarial | Points = attempts x efficiency; minutes x rate |
| Hierarchical reconciliation (MinT) | Retail/supply-chain demand | Player forecasts sum to team totals |
| Bayesian online changepoint / CUSUM | Manufacturing QC, predictive maintenance | Detect role changes, injury-return effects |
| Conformal prediction, CRPS, reliability diagrams | Weather/ensemble forecasting | Honest intervals and calibration |
| Probabilistic load forecasting (quantile loss) | Energy grid forecasting | Quantile heads for stat lines |
| Market-as-prior Bayesian updating | Election forecasting | Model deviation from market price as a tested hypothesis |
| Item/SKU cold start via attribute similarity | E-commerce, retail | Rookie priors from draft slot, position, college |
| Survival/hazard models | Churn, reliability engineering | Injury risk, minutes load, "time until rest day" |
| Sequence embeddings (word2vec-style) | NLP, genomics | Player/lineup embeddings from co-occurrence |

## Build order (milestones)
**Phase 1 — Foundation (the shippable core)**
1. Repo scaffold, Makefile, pre-commit, CI skeleton (lint/type/unit run on an empty package), Dockerfile, fixture dataset.
2. Ingest + schema: `nba_api` pullers, resumable and cached, loaded to DuckDB. Test row counts and coverage.
3. Registry adapter (local backend, patterns above) + eval harness, so everything later is logged.
4. Rungs 0-2 with as-of features; walk-forward harness; frozen holdout season set aside.
5. Cold start methods 1-4 (shrinkage, archetypes, rookie priors, carryover); report cold-start slices.
6. Model-gate CI step wired to the registry.
**Phase 2 — Props**
7. Minutes model, then stat distributions (points, rebounds, assists, 3PM) with calibration reporting.
8. Combos (PRA etc.), hierarchical coherence, role changepoint detection, conformal intervals.
**Phase 3 — Markets and parlays**
9. Kalshi read-only ingestor (live + historical tiers), name matching, tests with recorded fixtures.
10. Joint probability engine: independence -> copula -> (optional) possession sim.
11. EV engine with configurable fees, intervals, market-as-prior, `no_positive_ev_found` verdict.
12. Paper-trade log, auto-settlement, nightly calibration report.
**Phase 4 — Optional depth**
13. Possession sim (rungs 3-4) and DeepSets lineup encoder if Phase 2/3 results justify them.
14. GATv2-TCN and other borrowed techniques, each as a gated experiment.
15. Optional MLflow backend only if a UI is wanted.

## Working agreements for Claude Code
- Plan briefly, then implement one milestone at a time; run tests before moving on.
- Prefer SQL in DuckDB for feature building; keep Python for modeling.
- After each milestone, print a short summary: what changed, test status, key metric deltas with CIs.
- If a result looks too good (e.g., accuracy >75%, or near-zero calibration error), assume leakage and audit the `as_of` logic before reporting it.
- Do not add dependencies or abstractions beyond what the current milestone needs.
- Never add code that places orders or handles trading credentials.
