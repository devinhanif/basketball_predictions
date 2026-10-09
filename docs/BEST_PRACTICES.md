# Best practices — NBA prediction system

Distilled from what worked and what broke in this repo (2026-10-05 → 2026-10-08). Each rule cites the
incident or result that earned it. Agents and maintainers follow this; CLAUDE.md remains the spec.

---

## 1. Data integrity (fix the data before the model)

| Practice | Why (incident) |
|---|---|
| **Treat DNP rows explicitly.** `player_game_stats` DNP rows have `minutes` NULL and stats 0. Exclude them from labels and averages; model P(play) separately. | 18.6% of rows were zero-stat DNPs; they biased baselines ~1 pt low and turned the "intermittent" volatility bucket into a DNP detector. Retracted T001. |
| **Season-scope anything called "season".** Partition by `(team_id, season)`; shrink early-season values to a regressed prior. | `games_into_season` was cumulative across seasons (mean 176); the tanking flag fired from game 1. |
| **Never cache empty API responses as "done".** Treat empty frames as failures and retry. | 1,202 empty box scores were silently cached for 2025-26. |
| **Reconcile parsed data against ground truth.** Box-score totals, final scores, possession counts. | The PBP tokenizer drops games that don't reconcile (124 of 3,951) instead of training on corrupt sequences. |
| **Name matching needs context.** Full name → suffix/diacritics → team-as-of; log unmatched; reviewed alias tables. | Last-name-only matching crashed the injury backfill (17 "Smiths"). |

## 2. Leakage (assume it until proven otherwise)

| Practice | Why |
|---|---|
| **Every feature is as-of the game:** strictly prior games; pre-tip information only, with a ≥60-minute report cutoff. | Core CLAUDE.md rule; every module ships a planted-future-row test. |
| **Gate pre-tip information on the REAL tip time in backtests too, not a proxy.** | A 19:00 ET proxy tip admitted the 17:00 injury snapshot for ~306/3,953 games (7.7%) that tipped at or before 17:00, i.e. a report published after tip (red team 2026-10-09; inflated props pts CRPS gain by ~0.003). Use `nba.features.game_tipoff` and `tip_source="real"`. |
| **Audit missingness, not just values.** A feature whose NULL pattern depends on the outcome is a leak even if its values are clean. | `opp_adjusted_ridge` was NULL iff tonight's minutes < 5. It passed the name allow-list, top-10 importance and label-correlation checks, and contaminated experiments 1–2. |
| **Suspicious gains get audited before they're celebrated.** A single feature group carrying the whole gain is a red flag. | The ablation showing the ridge "carried everything" was the leak's fingerprint. |
| **Use rosters you'd actually know.** At prediction time use projected minutes and the injury report, never box-score membership. | The sim's backtest "wins" vanished forward because the backtest saw real rosters. |
| **Honest accuracy ranges.** Pre-game NBA win models land in the mid-to-high 60s%. Treat >75% as a bug. | Published papers at 71–83% used same-game stats or random splits. |

## 3. Evaluation design

| Practice | Why |
|---|---|
| **Walk-forward by date; never random splits.** | Random splits leak future team/player strength. |
| **Pre-register the decision rule before running** (metric, floor, CI, multiplicity, slices, calibration), in the module docstring plus a doc. | Prevents goalpost-moving; caught the urge to promote per stat after seeing 2024. |
| **Select on one season, report on the next; keep a frozen holdout.** One clean touch per never-evaluated model, logged in `HOLDOUT_ACCESS_LOG.md` **before** running. | Injury-Elo and context-residual earned real confirmations this way; experiment 3's touch was voided unrun when the leak surfaced. |
| **Cluster bootstrap CIs by game** (or date for cross-game questions). | Row-level bootstraps were anti-conservative. |
| **Correct for multiplicity** (BH within declared families); keep an append-only `TEST_LEDGER.md`. | Dozens of A/Bs; only a few survived correction. |
| **Practical-significance floors** (e.g. CRPS ≤ −0.005), not just p-values. | Several "significant" effects were economically negligible. |
| **Use proper scoring rules:** log loss and Brier for probabilities, CRPS for distributions, threshold log loss for P(≥ line). Report calibration (ECE, reliability). | Parlays multiply probabilities, so calibration errors compound. |
| **Score count stats on integer support (CRPS = RPS on integers) for EVERY arm.** Comparing an integer-grid forecast (NB/Poisson) with a continuous-quantile one on integer outcomes rewards the integer grid for free. | Leak-free experiment 3's reb/ast/fg3m "wins" were 92-126% this artifact; rounding production's quantiles (`ceil(q-0.5)`) erased them (T114-T118). Likely also behind exp-2's "NB wins counts". |
| **Validate the metric itself on known-truth simulations.** | Amendment A1's coverage estimator was biased; A1.1 was chosen by simulation, not by model results. |
| **Test like a sceptic:** fair baselines (same DNP handling, same calibration), ablations, slices (cold start, starters/bench, season phase, teammate OUT). | The sim lost the fair rematch; context-residual still won against a calibrated baseline. |
| **Report negatives.** | Sim props, rung-4, RAPM, the joint set transformer and usage redistribution were all rejected honestly. |

## 4. Modelling (what actually moved the needle)

| Practice | Evidence |
|---|---|
| **New information beats new architectures.** | The two production wins both came from the pre-tip injury report. Transformers, GNN-style sets, RAPM, MLPs and stacks tied or lost. |
| **Start from strong simple baselines:** MOV-Elo, recency-weighted averages. | Elo-family models beat logistic and GBM on ~4k games; the recency average beat the sim on every prop. |
| **Residual modelling over a good baseline.** | Context-residual (GBM on actual − recency) is production props. |
| **Match the distribution to the stat.** Counts (reb/ast/3PM) → Poisson/NegBin; points (mixture of 2s/3s/FTs) → quantile/Gaussian-like. | Experiment 2: NegBin won reb/ast/3PM; Poisson failed points (bias +0.17, coverage 0.72). |
| **Don't pool stale seasons equally.** Use recency weighting or the current season. | Equal pooling degraded each added season; current-season rates gave a big CRPS gain. |
| **Tuning rarely matters; features do.** | 60-trial searches added nothing on the full data; search-set gains didn't transfer. |
| **Calibrate only where miscalibration is real and persistent** (raw ECE ≫ noise floor). | Points: calibrate. Rebounds: at the noise floor, so calibration is futile. |
| **Correlation structure:** model same-game dependence (copula); verify cross-game independence. | Same-player legs co-move (28.6% vs 20.1% hit rate); cross-game residual r ≈ 0. |

## 5. Engineering and operations

| Practice | Why |
|---|---|
| **Verify before committing:** `ruff check`, `ruff format --check`, `mypy`, `pytest`, **never piped through `tail`** (it masks exit codes). | Twice a masked failure got committed. |
| **Crash-safe long jobs:** small artifacts first, per-stage checkpoints, streamed outputs, resume on rerun. | A 95-minute run died writing one giant frame (OOM); the rework resumed cleanly. |
| **Separate data stores by writer** (DuckDB is single-writer): Kalshi DB, OOF store, paper trades. | Avoided lock contention with long read-only evals. |
| **Right hardware for the job:** trees on the local CPU (the Mac beat a T4); GPUs for large neural models. | Saves Colab units; production retrains on CPU in seconds. |
| **Registry discipline:** candidates by default; promotion is explicit, evidence-gated and human-approved; data files never go in the registry. | Promotions of injury-Elo and context-residual followed confirmation. |
| **Forward logging before tip-off** with comparison models alongside; settle afterwards. | The 2026-27 season is the only truly clean test left. |
| **Read-only markets, verified fees, defer to market without a track record.** | The parlay engine reports `no_positive_ev_found` until shadow history proves skill. |
| **Version the data, not just the code.** `make data-snapshot` before registering runs and after any load; `make data-check` flags row drops and played-vs-unplayed NULL asymmetry. | The `opp_adjusted_ridge` leak is exactly a NULL-vs-minutes asymmetry that this check surfaces mechanically (see docs/DATA_VERSIONING.md). |

## 6. Checklist for any new model

1. Write the hypothesis and pre-registered rule (and any holdout row) **first**.
2. Build features as-of; add planted-future, missingness and DNP tests.
3. Compare against the current production model **and** a strong simple baseline, with fair handling on both sides.
4. Select on season S, report on S+1; clustered CIs; BH; floor; slices; calibration.
5. If it wins, audit for leakage before believing it.
6. Register as a candidate; one logged holdout touch; promote only with approval.
7. Log it forward; let 2026-27 decide.
