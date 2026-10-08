# NEXT SESSION — handoff (paste this at the start of your next Claude Code session)

_Repo: `/Users/devin/Downloads/nba-prediction` · GitHub: github.com/devinhanif/basketball_predictions (main, CI green) · as of 2026-10-08._

## Read these first (standing rules — do not violate)
- **No AI attribution anywhere.** Never put "Claude"/"Anthropic"/assistant/AI co-author trailers or mentions in any commit, file, or PR. Repo-wide.
- **This machine is memory-bound** (Apple A18 Pro, 6 cores, **8 GB RAM**, swaps hard under load). For speed: turn **Low Power Mode OFF**, **plug in**, close other apps. In code: **do NOT fan out processes** (fan-out backfires under swap) — shrink footprint first (chunk/stream, fewer `n_sims`, select columns). GPU/neural work belongs on **Colab**.
- **DuckDB `nba.duckdb` is single-writer**: open `read_only=True` for reads; only one writer at a time. It's gitignored/ephemeral — rebuildable.
- **Agents build + unit-test on the fixture; the maintainer (you/me in the main loop) runs the long real-data jobs** as tracked background commands (a ~10-min watchdog kills silent agents). Agents should NOT commit — leave changes in the tree for review.
- Env: **`uv run <cmd>`**, Python 3.12. Verify `ruff`/`mypy`/`pytest` clean BEFORE committing (don't bundle check+commit).
- If a result looks too good, assume leakage and audit `as_of` first. Trust data+arithmetic over memory on the 2025-26 season (knowledge-cutoff gap).

## Where the project stands (the honest scoreboard)
- **Win probability:** MOV-Elo tuned by differential evolution is the champion (holdout log loss 0.601). Nothing beats it; the sim doesn't either. It'll be blended at the final ensemble rung, and its expected-margin can feed the minutes garbage-time branch (1B).
- **Player props (possession sim vs. season-average, real 4-season data):**
  - **Rebounds:** sim **beats** season-avg (CRPS −0.014, CI excludes 0) — earned by real lineups + `players_static` positions.
  - **Points / Assists:** tie the season-average marginally.
  - **Routing (sim vs season-avg by as-of volatility/archetype bucket), OUT-OF-SAMPLE:** **Assists = clean win on both splits**; **Rebounds = win on walk-forward, tie on holdout**; **Points = no win** (sim too weak there; `n_sims=500` noise penalized it — a higher-sim rerun is the obvious retest). The router sends cold-start/intermittent/erratic players → sim, smooth regulars → season-avg.
- **Clustering:** k=6 archetypes (silhouette 0.347), interpretable as position×role-tier (starter vs backup bigs, lead vs rotation guards, 3&D vs scoring forwards). Syntetos-Boylan volatility classes computed as-of.
- **Minutes 1A (learned game-context fit + depth-chart/hazard feature), cold-start-gated:** **clean win** — overall MAE −0.096, cold-start MAE −1.47 (CIs exclude 0), zero regression on established players. Shipped flag-gated; `learned_context_max_n_played=20`.
- **Set aside honestly (kept flag-off, documented):** on-court usage denominator (regressed); lineup archetype-MIX (real but negligible, R²≈0.016); usage redistribution 2A/2B (**inert — blocked on an injury/availability data feed**, the mechanism never fires from history alone).

## ⚠️ New agents need a RELOAD to be invocable
These `.claude/agents/*.md` were added this session but the agent registry loads at startup, so they are **not invocable until you restart / reload**: **`data-innovator`** (preprocessing-to-best-practice + representation R&D, owns `nba/preprocess/`), **`analyst`** (plain-English analysis, every finding paired with a named hit+miss), **`statistician`** (inference validity / FDR / power), **`perf-engineer`** (runtime + 8GB memory). Existing agents: data-engineer, ml-engineer, props-modeler, markets-engineer, devops-ci, qa-reviewer, lead. All run on **sonnet**.

## Picking up where we left off — recommended order
The agreed roadmap order was **1 → 2 → 5 → 4 → 3** (mostly done through the fixable blind spots):
1. **FIRST, run a `statistician` pass** (now invocable): apply Benjamini-Hochberg FDR across ALL the A/Bs this session (routing buckets × stats × splits, minutes, matchup) and report which "wins" survive correction + check the frozen holdout wasn't reused. Trust only survivors. **This is the highest-value next action** — we've run dozens of tests and haven't corrected for multiplicity.
2. **Matchup 3A verdict** — a full-props A/B (archetype-vs-archetype opponent factor, flag-off) was RUNNING at handoff but didn't finish. Re-run it: `uv run python /private/tmp/.../scratchpad/matchup_ab.py` (or rebuild: three `PropsConfig` variants — baseline, `opponent_adjustment.enabled=True`, `opponent_adjustment.use_archetype_factor=True`), compare CRPS per stat, **hard gate: rebounds must not regress**. Keep only if it beats baseline with CI excluding 0; else flag-off + document (it's retrying a documented failure at finer granularity).
3. **Retest points routing at `n_sims=2000`** — the out-of-sample points "no-win" was partly Monte-Carlo noise from `n_sims=500`; a sharper sim could flip it.
4. **#4 Rung-4 neural step-heads (needs your Colab credentials)** — learned possession-outcome heads (NOT the DeepSets lineup encoder; lineup-mix showed negligible signal). This becomes a THIRD candidate in the router (esp. for points, the stat the sim doesn't win). Colab because this box is memory-bound and GPU-less.
5. **#3 Phase 3 — markets & EV** — Kalshi read-only ingestor → copula joint probabilities → honest-EV engine with `no_positive_ev_found`. Answers "is there edge." Expect thin history; report sample sizes.

## Known data gaps worth closing (both surfaced this session)
- **No time-decay/recency weighting across seasons.** As-of features pool all prior seasons with EQUAL weight → slow to reflect season-over-season change; muddies volatility buckets. Fix = CLAUDE.md cold-start **method 4** (time-decay carryover + age curve), not yet in the possession feature builder (minutes uses a 150-game rolling window as a partial exception).
- **No forward-looking availability/injury feed.** This is what blocks usage redistribution (#2) and would most help points (minutes is the #1 prop-error driver). A real injury/announced-rest source is the highest-leverage new data.

## Key entrypoints
- Props evals: `nba/eval/player_points_sim_eval.py::run_sim_vs_baseline_eval` (has `return_raw`, `use_oncourt_usage`), `nba/eval/player_reb_ast_sim_eval.py::run_reb_ast_sim_vs_baseline_eval`.
- Routing: `nba/eval/model_routing.py::route_by_bucket`, `nba/eval/routed_eval.py::evaluate_routing`.
- Clustering: `nba/coldstart/archetypes.py` (fit-once/apply-per-date), `nba/coldstart/sb_classification.py`.
- Preprocessing: `nba/preprocess/depth_chart.py`, `nba/preprocess/embeddings.py`.
- Minutes: `nba/props/minutes.py` (+ `MinutesModelConfig` flags in `nba/props/config.py`).
- Full results: **`docs/RESULTS_2026-10-08.md`**; options brief: `docs/NEXT_OPTIONS.md`; living status: `docs/PROJECT_STATUS.md`.

## Notifications (so you can stay in manager mode)
Run **`/remote-control`** → scan QR in the Claude phone app (same account, Code tab) → **`/config`** → enable *"Push when Claude decides"* + *"Push when actions required."* Then I ping your phone only at decision points. Permission allow-list is already set in `.claude/settings.json` to cut "Allow" prompts.
