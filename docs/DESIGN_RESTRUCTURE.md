# Design: restructure into four layers, a research archive, and an explainer

_Draft 2026-10-09 for Devin and Claude to mark up together. Nothing in this document has been done.
No file has moved, no agent has changed, no code has been edited. Items marked ❓ are open questions
for Devin; the ones marked ❓❓ are the five that change the plan most._

Goal, in Devin's words: elegant, scalable, shows the structure of the game; if it cannot beat the
odds, people use it to learn. Hard constraint: opening night 2026-10-20 is not disturbed, and the
2026-27 forward log (the only clean test left) must come out identical to what the current code would
have produced.

---

## 0. What this document is, and what the numbers rest on

Facts used below, with where they come from (so you can check me):

| Fact | Source |
|---|---|
| 270 modules / 80,842 lines in `nba/` (24 are empty-ish `__init__.py`) | `find nba -name '*.py'`; line counts per file in Appendix A |
| 169 modules / 45,951 lines reachable from runtime entrypoints; ~35k lines not | measured upstream of this doc; I did not re-derive it exactly (my own static import pass gave 159-175 modules depending on which CLIs count as entrypoints) |
| The "unreachable" tree is mostly `nba/eval` (31 modules), `nba/colab` (21), and dead features/models | same |
| Production = three models: `rung0_mov_elo` (baseline), `rung0_injury_elo` v2 (residual on MOV-Elo logit), `props_context_residual` v2 (residual on recency average) | `docs/PROJECT_STATUS.md` s1 |
| `SIM_STATS = ()` in `nba/props/forward.py:115`: the sim/routed prop path is switched off in production | code |
| A full 2025-26 systems replay (210 dates, 0 failures) takes about 105 min wall, 1.3-3.3 GB per 20-date chunk | `docs/REPLAY_2025_26.md` |
| 172 test files under `tests/` | `find tests -name 'test_*.py'` |

### Three findings that shape the plan (none of them is "delete the dead code")

1. **The dead tree is not dead; live code reaches into it.** Several "archive" candidates
   are imported by production modules. Deleting them naively breaks the nightly job. The
   entanglements (full list in s3.3) are small but real:
   - `nba.sim.usage_redistribution` (743 lines, verdict: inert null, T047) is imported by
     `models/injury_elo.py`, `props/context_residual.py`, `daily/predict.py` and
     `props/context_features_v2.py` for six report-trigger functions (`ReportTriggerConfig`,
     `load_report_rows`, `usable_report_rows`, `latest_pretip_flagged`, `serve_pretip_flagged`,
     `rotation_flagged_by_team`). The information that made both production models win lives inside
     a file named for an experiment that lost.
   - `nba.eval.metrics` / `nba.eval.walkforward` / `nba.eval.injury_elo_eval.feature_config_from` are imported
     by `daily/settle.py`, `props/*`, `registry/bootstrap.py` and `daily/predict.py`.
   - `daily/predict.py` lazily imports `nba.stack.frozen` / `nba.stack.populate` (line ~463) and
     `registry/routing.py` for a routed-props path that is not the default and has no active route.
   - `props/forward.py` (1,014 lines) imports the sim (`player_attribution`) and four sim-only feature
     builders to serve a branch that `SIM_STATS = ()` disables.
2. **Seven archive groups (about a fifth of the archived lines) had no ledger row; now back-filled.** CLAUDE.md says research is "archived under the ledger".
   RAPM, rung-4 heads, the win-prob family, time-decay, the game-context and player-context screens, the GA tune
   and the experiment-1/2 prop runners now have PROVISIONAL back-fill rows T176-T183 in `docs/TEST_LEDGER.md`
   (2026-10-09 NOTICE; verdicts copied from the docs and registry, no new numbers, no CI where none was recorded).
   Appendix A's `NO ledger row` marks for these groups are superseded by those rows. ❓❓ (Q3)
3. **We already own the safety oracle.** `nba/daily/replay_season.py` (933 lines, currently classified
   "unreachable") is the single most valuable tool for this restructure: it replays 210 dates of
   2025-26 through the real daily process. A restructure that leaves its output unchanged cannot have
   changed a forecast. It must be promoted, not archived. Its limit: it does not cover referees
   collect, market capture, post-game ingest, the lineups collector, the Kalshi snapshot, launchd, or
   the live schedule and injury-PDF fetch (replay doc, "Not replayed"). Code in those areas gets a
   different, weaker safety net, so we leave it alone before 10-20.

---

## 1. Target layout

Four layers with one rule each, plus an archive and an explainer:

```
nba/
  data/        LAYER 1  what we know, and when we knew it
    bronze/      write-once raw pulls (ingest/*, lineups collector, official rosters pull)
    silver/      clean tables carrying event_time AND known_at (parse/*)
    known_at/    the as-of API: Known(T) handle, tip times, injury-report triggers
    gold/        derived, rebuildable model-ready frames (only the builders production calls)
    manifest/    structure + missingness diff (the leak detector)
    db.py  schema.sql
  models/      LAYER 2  the three production models, as residuals on baselines
    baselines/   mov_elo.py   recency.py            (what the residuals correct)
    win/         injury_elo.py                      (logit residual on MOV-Elo)
    props/       context_residual.py minutes.py distributions.py conformal.py integer_support.py ...
    lineup/      F11, the lineup as the unit (new; s6)
    priors.py    empirical-Bayes shrinkage (the one cold-start primitive production uses)
    base.py      Model contract: predict(known, game) -> list[Forecast]
  truth/       LAYER 3  what happened, and whether we were right
    store.py     forward_predictions append; refuses writes at/after tip (LeakageError)
    settle.py  checkpoint.py  report.py  metrics.py  bootstrap.py
    holdout.py   frozen-season guard: a touch needs a logged row first (s5.2)
    prereg.py    "refuse to fit without a committed pre-registration" (s5.3)
    replay.py    the byte-identical replay harness (was daily/replay_season + rehearsal)
    backtest/    the walk-forward drivers for the production models only
    registry/    local adapter, promotion stays explicit and Devin's
  markets/     LAYER 4  what the market thought, and what it is worth after fees
    kalshi/      read-only ingest, aliases (alias_review waits for player markets)
    odds/        as-of market state, capture at prediction time, implied probabilities
    ev/          joint probability (independence/copula), EV after fees, budget, paper trades,
                 "no_positive_ev_found" as a first-class verdict
  explain/     the game explainer (s7) + tool-grounded Q&A (was parlay/assistant)
  daily/       ONE readable flow (s4): flow.py, arms/t30.py, __main__.py
  ops/         watchdog, exit codes, version
research/      archived code, indexed by the ledger; never imported by nba/ (enforced by a test)
  INDEX.md     module -> ledger rows -> verdict -> last commit where it ran
  colab/ eval/ features/ models/ sim/ stack/ props/ registry/ markets/ coldstart/ preprocess/
```

### 1.1 The rule of each layer (what makes the structure teachable)

| Layer | Question it answers | Writes | Reads | Hard rule |
|---|---|---|---|---|
| data | What was knowable at time T? | bronze (immutable), silver, gold | the network (bronze only) | every silver row has `event_time` and `known_at`; gold is rebuildable from silver |
| models | Given what was knowable, what do we forecast? | nothing durable (returns `Forecast` objects) | `Known(T)` only | a model never receives a DB connection, so it cannot peek |
| truth | Were we right, and can we trust the claim? | stored forecasts, scores, checkpoints | silver (results), models' stored output | a forecast is stamped before tip and refused after; scoring uses stored output, never a refit |
| markets | What did the market think, and is the gap worth anything? | Kalshi DB, paper trades | `Forecast` + market state | read-only: no orders, no credentials, ever (rule 5) |

Dependency direction is one-way: `markets -> models -> data`, `truth -> data`, `explain -> all (read-only)`,
`daily -> all`. A test (`tests/test_layering.py`) fails the build if `data` imports `models`, if anything
in `nba/` imports `research/`, or if `models` opens a DuckDB connection.

### 1.2 The `Known(T)` handle (the one new idea in layer 1)

Today as-of discipline is a convention: each builder takes `as_of` and every module ships a
planted-future-row test. The replay found the failure mode (a 19:00 ET proxy admitted post-tip reports for
306/3,953 games; a NULL pattern leaked minutes). The structural fix is to make the unsafe thing
impossible to write:

```python
known = data.known_at(db, T)                  # read-only view; every table filtered known_at <= T
known.results()                               # games completed before T (known_at = end of ET day + 0)
known.injury_report(game)                     # latest snapshot with stamp <= tip - 60 min and <= 36 h old
known.lineup_snapshot(game)                   # T-30 only if it exists at T, else None
known.market(game)                            # latest Kalshi state with ts <= T
```

Models receive `Known`, never a connection. New code (F11, explain/) is written against it from day one.
Legacy production models (`props/forward.py` builds frames from a connection directly) get a
`Known`-wrapped connection in a later wave; they are not rewritten for this. ❓ (Q6: new-code-only first, or
retrofit the two production models? I recommend new-code-only until after the 30-date checkpoint.)

Open design point: several silver tables have only `game_date`, not a `known_at` timestamp. Proposal:
results get `known_at = 00:00 ET the day after game_date` (conservative, identical to today's
`game_date < slate date` rule), report snapshots keep their publication stamp, tips keep the schedule fetch
time, lineups keep their poll time. No schema migration before 10-20 (s8); `known_at` is computed in a view first.

### 1.3 Bronze / silver / gold, mapped to what exists

| Tier | Today | Rule |
|---|---|---|
| bronze | `data/boxscore`, `data/games`, `data/availability_official`, `data/availability_raw`, `data/schedule/raw_*.parquet`, `data/lineups` snapshots, `data/kalshi` raw JSON, `data/hustle`, `data/history` | write-once; never edited; replayable. **No data directory moves in this plan** (code moves only) |
| silver | tables in `nba.duckdb`: `games`, `player_game_stats`, `player_availability`, `possessions`, `stints`, `players_static`, `forward_*` | clean + typed + `known_at`; DNP rows explicit (BEST_PRACTICES s1) |
| gold | `data/models/context_residual/<date>/` caches, feature frames built in `props/*`, `features/*` | derived only; deleting a gold artifact must be harmless |

History (2019-2021) stays in `nba_history.duckdb`, never in production (CLAUDE.md, DECISIONS 2026-10-09).

---

## 2. What each production model looks like in the new tree

Principle from CLAUDE.md ("simple and residual"): a strong baseline, a small model on top, a distribution
shaped to the stat.

| Production model | Baseline it corrects | The residual (what it adds) | Info that earns it | Lives at |
|---|---|---|---|---|
| `rung0_mov_elo` | none (it is the baseline) | - | results only | `models/baselines/mov_elo.py` |
| `rung0_injury_elo` v2 | MOV-Elo logit | `b*(V_out_away - V_out_home)/10 + g*(V_doubt_away - V_doubt_home)/10`, ridge refit each run | official injury report at tip-60 | `models/win/injury_elo.py` |
| `props_context_residual` v2 | recency-weighted played-games average (half-life 10) | LightGBM mean-residual + \|residual\| scale heads, split-conformal quantiles, integer support for counts | injury report (vacated stats), Elo margin, rest, minutes/role gaps | `models/props/context_residual.py` |

Shadow arms (`int`, `lt`, `t30`) stay exactly where they are logically (`models/props/integer_support.py`,
`lower_tail.py`, `daily/arms/t30.py`) until the frozen 30/60/120-date checkpoints decide them
(`docs/FORWARD_PREREG_2026_27.md`). We do not fold or delete an arm before its checkpoint; that would be
changing a rule after the data started arriving (non-negotiable 4). ❓ (Q4)

Every model implements one contract (replaces the `ForecastModel` ABC residue in `models/base.py`):

```python
class Model(Protocol):
    name: str; version: str
    def predict(self, known: Known, game: Game) -> list[Forecast]: ...   # no I/O, no refit
    def fit(self, known: Known) -> None: ...                              # daily refit uses only known(T)
```
`Forecast` is the stored unit: `(game_id, model, version, subject, stat, made_at, quantile_grid | p_win,
p_ge_full, inputs_fingerprint)`. `truth/store.py` is the only writer.

---

## 3. The move table

Legend: **KEEP** = stays in `nba/` (possibly renamed to its new home); **EXTRACT** = the live part moves
to the new tree and the rest goes to `research/`; **RESEARCH** = archived to `research/` with its tests;
**DELETE** = no archive value (recoverable from the git tag). Per-module rows are in Appendix A.

### 3.1 Summary by current directory (lines, not counting `__init__.py`)

| Dir | Files | Lines | KEEP | EXTRACT | RESEARCH | DELETE |
|---|---|---|---|---|---|---|
| `colab/` | 20 | 9,331 | 0 | 0 | 7,488 | 1,843 |
| `coldstart/` | 6 | 1,285 | 117 | 0 | 1,168 | 0 |
| `daily/` | 16 | 5,312 | 4,023 | 1,289 | 0 | 0 |
| `datamanifest/` | 3 | 562 | 562 | 0 | 0 | 0 |
| `db/` | 1 | 117 | 117 | 0 | 0 | 0 |
| `eval/` | 37 | 15,062 | 1,771 | 386 | 12,905 | 0 |
| `features/` | 18 | 8,324 | 1,069 | 0 | 7,255 | 0 |
| `ingest/` | 17 | 5,081 | 5,081 | 0 | 0 | 0 |
| `kalshi/` | 10 | 1,893 | 1,893 | 0 | 0 | 0 |
| `lineups/` | 5 | 876 | 876 | 0 | 0 | 0 |
| `markets/` | 4 | 868 | 868 | 0 | 0 | 0 |
| `models/` | 11 | 3,754 | 762 | 329 | 2,663 | 0 |
| `ops/` | 3 | 373 | 373 | 0 | 0 | 0 |
| `parlay/` | 30 | 6,919 | 5,615 | 0 | 1,304 | 0 |
| `parse/` | 6 | 2,035 | 2,035 | 0 | 0 | 0 |
| `preprocess/` | 2 | 483 | 0 | 0 | 483 | 0 |
| `props/` | 28 | 11,086 | 4,469 | 1,014 | 5,603 | 0 |
| `registry/` | 11 | 2,287 | 1,103 | 341 | 843 | 0 |
| `shared/` | 1 | 7 | 7 | 0 | 0 | 0 |
| `sim/` | 5 | 2,410 | 0 | 743 | 1,667 | 0 |
| `stack/` | 13 | 2,561 | 0 | 0 | 2,561 | 0 |
| **Total** | **247** | **80,626** | **30,741** | **4,102** | **43,940** | **1,843** |

Plus 24 `__init__.py` files (216 lines) that follow their packages. In the `daily/` row, KEEP includes
`replay_season`/`rehearsal`, promoted to `truth/replay.py`. The EXTRACT column is whole-file; roughly 1,200 of
those 4,102 lines are the research remainder (usage redistribution ~510, the sim branch of `forward.py` ~300,
the routed branch of `predict.py` ~120, the rest of `rung0_baselines`/`injury_elo_eval`/`registry.__main__`).

**Estimated outcome:** about 47k of 80.8k lines (58%) leave `nba/`; `nba/` ends near 34k lines and roughly
135-145 modules (merges of 20-60-line files included). That is a little more than the "about half" we agreed on,
because I classified the sim-feeding feature builders and the routing stack as research once I saw that
`SIM_STATS = ()` switches them off. Treat the number as a +/-3k estimate until the Day-1 import audit (s8). The
dependency list (`torch`) shrinks only after 10-20.

### 3.2 Ledger justification by archive group

Every archive group cites ledger rows; where there is none, the group is flagged and needs a back-fill row
before moving (Q3).

| Archive group (research/…) | Lines | Ledger rows | Verdict | Note |
|---|---|---|---|---|
| Possession sim for props: `sim/engine, possession_model, player_attribution`, `models/rung3_sim`, `eval/player_*_sim_eval`, `eval/fair_rematch`, sim-only features | ~7.5k | T001-T004, T061-T064 | sim ties or loses to a calibrated recency baseline; no win-prob value | fair rematch (T062-T064) is the decisive row |
| Routing and stack: `stack/`, `registry/routing`, `eval/model_routing`, `eval/routed_eval`, `props/volatility`, `coldstart/sb_classification` | ~5.8k | T005-T033 (families C, D, E; D rejected as selective, C/E provisional) | not kept; `routes_draft` only, no active route | verify with `nba.registry list` on Day 1 |
| Rung-4 step heads, set-based lineups: `models/rung4_stepheads`, `sim/learned_heads`, `features/possession_step_features`, `colab/jobs/rung4_stepheads` | ~1.7k | **none** (RESULTS_2026-10-08 only); related T042 (lineup archetype-mix R2 0.016) | not kept | back-fill row |
| Usage redistribution (remainder of `sim/usage_redistribution`, `eval/usage_redistribution_eval`) | ~1.3k | T047 | inert null | the report-trigger functions are EXTRACTED, not archived |
| Matchup / opponent adjustment: `props/opponent`, `coldstart/archetypes`, `coldstart/config` | ~1.5k | T034-T041 (family F, no bootstrap), T048-T060 (M3A) | not kept | |
| Time decay and carryover: `features/time_decay`, `coldstart/carryover`, `eval/time_decay_tune` | ~0.8k | **none** (TIME_DECAY_2026-10-08) | not kept | back-fill row |
| RAPM: `features/rapm`, `eval/rapm_injury_elo_eval` | ~0.7k | **none** (RAPM.md) | not kept | back-fill row |
| Win-prob family and rungs 1-2: `models/winprob_family`, `rung1_logistic`, `rung2_gbm`, `eval/winprob_family_eval` | ~2.6k | **none** (WINPROB_FAMILY.md, RESULTS) | Elo family wins | back-fill row |
| Game-context screens: `eval/context_screen_*`, `features/game_location_context` | ~1.2k | **none** (CONTEXT_SCREEN_*.md) | national TV tiny survivor, otherwise null | back-fill row |
| Generic ladder runner: `eval/run, report, __main__`, `registry/model_gate` | ~1.3k | n/a (infrastructure for a closed ladder) | | `model_gate` is rebuilt as a "prereg + ledger + holdout rows exist" check |
| GA tune: `eval/ga_tune` | 0.3k | **none** | produced `configs/mov_elo_tuned.yaml`, which stays | |
| ctxres v2/v3 sweeps and ridge: `props/context_features_v2`, `features/opponent_ridge_v2`, `eval/ctxres_*`, `eval/ridge_v2_eval`, colab jobs | ~12k | T073-T080 (voided, leak f1fcb91), T098-T101, T102-T118 | BROKEN / WOUNDED / not kept | the missingness-leak fingerprint lives here: keep it findable |
| Set transformer, GPT, sequence props: `features/game_sets, pbp_tokens, player_sequences`, `eval/*`, colab jobs | ~9k | T065-T072, T081, T082-T084 | not kept | |
| Tracking / hustle: `features/tracking_features`, `eval/tracking_screen` | ~0.8k | T119-T123, T174-T175 | no family passes | |
| Lineups-known offline, T-30 injury-Elo, minutes v2, pts tail: `eval/lineups_known_eval`, `models/injury_elo_t30`, `props/minutes_v2`, `props/pts_tail` + evals | ~3.7k | T085-T090, T096-T097; T160-T161; T162-T173; T129-T135 | WOUNDED / not kept | T-30 lineups live on as the shadow arm |
| Experiment-1/2 prop runners: `props/run, __main__, stat_models, dispersion, report, coherence, combos, roster_replay` | ~3.1k | RESULTS_2026-10-08 (NegBin/Normal experiments) | superseded by context_residual | no per-test row; same back-fill question |
| Colab tooling: `colab/*.py` | ~0.6k | n/a | no GPU job planned | revive from tag if a large neural model is ever justified (Q5) |
| Duplicate: `colab/jobs/ctxres_v3_hybrid/ctxres_v2_engine.py` | 1.8k | n/a | `cmp` says byte-identical to `ctxres_v2_sweep.py` | the only DELETE |

### 3.3 Edges to cut before anything can move (the entanglement list)

| Live module | Imports from a research module | Fix | Live-path effect |
|---|---|---|---|
| `models/injury_elo.py`, `props/context_residual.py`, `daily/predict.py`, `props/context_features_v2.py` | `sim.usage_redistribution` (6 report-trigger functions) | new `data/known_at/injury_report.py`; old module re-exports for one step, then shim removed | import-only |
| `daily/settle.py`, `props/metrics.py`, `props/minutes.py`, `props/volatility.py`, `registry/bootstrap.py` | `eval.metrics`, `eval.walkforward` | move to `truth/metrics.py`, `truth/holdout.py` (same functions, same signatures) | import-only |
| `daily/predict.py` | `eval.injury_elo_eval.feature_config_from` | move that function next to `InjuryFeatureConfig` | import-only |
| `daily/predict.py` (lazy, ~L463) and `registry/__main__.py` (routes subcommand) | `stack.frozen`, `stack.populate`, `stack.routebuild`, `registry.routing` | delete the routed branch (`apply_active_route`, `routed_prop_predictions`, `--props-model routed`) after confirming no `route` alias is active | removes a non-default code path |
| `props/forward.py` | `sim.player_attribution`, `coldstart.sb_classification`, four sim-only feature builders | split into `models/baselines/recency.py` + `models/props/slate.py`; sim/profile branch (~300 lines behind `SIM_STATS = ()`) to research | removes a dead branch; recency and context rows must stay byte-identical |
| `props/context_residual.py` | `props/pts_tail` flag branch (default off, T129-T135 no pass) | remove the flag | removes a dead branch |
| `props/config.py` | `props/opponent` config type | cut the opponent fields | import-only |
| `props/minutes.py` (+`team_features`, `game_context`) | `features.game_context` | keep, but audit what is actually called | none |
| `features/{player_possession_features, player_rebound_assist_features, possession_features, time_decay}`, `props/minutes.py` (found by the Day-0 import snapshot, 2026-10-09) | `coldstart.shrinkage.shrink_rate`, `coldstart.carryover.carryover_blend` | these are live utilities, not research: move both functions to `features/shrinkage.py`; `coldstart` re-exports for one step | import-only |

Each is a one-commit change followed by the replay oracle (s8). The full measured list (18 edges on HEAD
a39a75e) is in `docs/reviews/import_graph_2026-10-09.md` and enforced by `tests/test_layering.py`, a
two-way ratchet: a new edge fails, and so does a stale entry once an edge is cut.

---

## 4. `nba/daily` as one readable flow

Today `pipeline.py` (723), `predict.py` (566), `store.py`, `settle.py`, `injury.py`, `schedule.py` and
`ingest_step.py` interleave scheduling, I/O, three families of arms and fallback logic; the flow cannot be read
top to bottom. Target: `daily/flow.py` reads as the six verbs of the project (schedule -> knowable-at-T ->
predict -> store -> settle -> score), and everything clever is a function it calls.

Sketch (pseudocode-level; names are illustrative, not an implementation):

```python
"""nba/daily/flow.py - one day of the forecast loop. Read top to bottom; nothing clever here."""
from nba import data, models, truth, markets
from nba.daily.arms import ARMS                      # shadow arms, frozen under FORWARD_PREREG_2026_27


def pretip(day: date, *, now: datetime | None = None) -> RunSummary:
    """Run every ~30 min on game days. Idempotent: re-running writes only what changed."""
    now = now or utcnow()
    summary = RunSummary(day, now)

    # 1. SCHEDULE - which games exist today and when exactly do they tip (real tips, never a proxy)
    slate = data.known_at.schedule(day)                              # list[Game(id, home, away, tip_utc)]
    open_games = [g for g in slate if now < g.tip]                   # a tipped game is never predicted
    summary.skipped_tipped = len(slate) - len(open_games)            # -> exit code 4 (informational)

    # 2. KNOWABLE AT T - one read-only handle; nothing downstream can query past it
    known = data.known_at(db_path(), T=now)
    summary.feeds = known.feed_status()                              # injury PDF, rosters, lineups: ok/missing/stale

    # 3. PREDICT - production models first, then comparison baselines, then shadow arms
    production = models.resolve_production(registry())               # mov_elo, injury_elo, context_residual
    baselines = models.baselines()                                    # mov_elo, recency (logged next to production)
    forecasts: list[Forecast] = []
    for g in open_games:
        gk = known.for_game(g, report_cutoff=g.tip - minutes(60))    # the rule, in one place
        forecasts += predict_game(production, baselines, gk, g, summary)
    for arm in ARMS:                                                  # int, lt, t30: comparison rows only
        forecasts += run_arm(arm, known, open_games, production, summary)   # failure isolated, never touches primary

    # 4. STORE - the only writer; refuses (LeakageError) if made_at >= tip; write-on-change by content hash
    summary.stored = truth.store.append(forecasts, made_at=now)

    # 5. MARKET CONTEXT - capture what the market said at the same instant (read-only)
    markets.odds.capture(open_games, at=now)
    return summary


def predict_game(production, baselines, gk, g, summary) -> list[Forecast]:
    out = []
    for model in (*production, *baselines):
        try:
            out += model.predict(gk, g)                               # no I/O, no refit inside predict
        except FeedMissing as e:                                      # e.g. no usable injury report
            out += fallback(model, gk, g, reason=str(e))             # MOV-Elo with fallback_reason stored
            summary.fallbacks.append((g.id, model.name, str(e)))
    return out


def morning(day: date) -> RunSummary:
    """08:00 job. Settle yesterday, score, report. Never predicts."""
    summary = RunSummary(day, utcnow())
    data.bronze.refresh_results(day - 1)                              # games + box scores for finished games
    summary.settled = truth.settle.settle_pending(scored_at=utcnow()) # uses STORED forecasts, never refits
    summary.eligible = truth.settle.settle_eligible_pending()         # integer-support, eligible rows
    truth.checkpoint.run_if_due()                                     # frozen 30/60/120-date looks, BH over 9
    truth.report.write_forward_report()
    markets.ev.settle_paper_trades()
    data.manifest.snapshot_diff_check()                               # missingness / row-drop leak detector
    return summary


def main(argv: list[str]) -> int:
    args = parse(argv)                                                # run | settle | report | checkpoint
    with job_lock(), heartbeat(args.cmd):                             # one job at a time; independent watchdog
        s = {"run": pretip, "settle": morning}[args.cmd](args.date)
    s.write_json(); alert_on(s)                                       # failures -> ALERTS.md + notification
    return s.exit_code()                                              # 0 ok, 4 some tipped, 5 degraded
```

Design notes:

- `flow.py` is about 150 lines because all branching lives behind four names: `known_at`, `resolve_production`,
  `ARMS`, `truth.store.append`. The current 720-line `pipeline.py` shrinks mostly by moving the three prop
  families (context/recency/routed) into `models/props/slate.py` and deleting the routed one.
- Idempotence and write-on-change stay (commit e8669d3); `content_hash` stays the byte-identity key.
- Every fallback (no report, tipped game, feed failure) is a value in `RunSummary`, not an exception path, so
  the report and the watchdog read the same object.
- The rewrite is never put live blind. It runs in dual mode (s8, Wave E): old and new flows write to two
  databases for N game days and their `content_hash` sets are diffed; cut-over only when the diff is empty.

---

## 5. The six agents

Eighteen agent files today; several overlap (red-team / statistician / qa-reviewer; ml-engineer /
props-modeler / data-innovator; game-day-operator / forward-monitor / devops-ci / cost-monitor;
data-engineer / data-steward). Rule from CLAUDE.md that stays: **one owner per directory; sequence is
build -> adversary -> review -> decide; parallel only across non-overlapping areas; cross-cutting changes go
through the main session.** The main session (Devin + Claude) replaces `lead`; it is the only committer.

| Agent | Owns (write access) | Takes in | Puts out | Replaces |
|---|---|---|---|---|
| **archivist** | `nba/data/`, `research/`, `docs/TEST_LEDGER.md`, `docs/HOLDOUT_ACCESS_LOG.md`, `docs/DECISIONS.md`, `docs/data_manifests/`, `docs/DATA_CHANGELOG.md` | data-source changes, finished experiments, archive requests | silver/gold builders, manifest diffs, ledger and holdout-log rows (drafted; committed by the main session), `research/INDEX.md` | data-engineer, data-steward, the preprocessing half of data-innovator |
| **modeler** | `nba/models/`, `configs/` for models, `docs/prereg/` (drafts) | a hypothesis (from Devin, FAN_KNOWLEDGE, or the scout-style reading log) | pre-registration draft first, then a fit behind the committed rule, then a result note | ml-engineer, props-modeler, research-scout (drafting rules), the representation half of data-innovator, colab-runner, perf-engineer |
| **adversary** | nothing in `nba/`; writes only `docs/reviews/` | any claimed win or any change to `truth/` | verdict SURVIVES / WOUNDED / BROKEN with shuffled-label, planted-future, missingness, knockout, replication, integer-support checks | red-team, statistician, qa-reviewer |
| **steward** | `nba/truth/`, `nba/daily/`, `nba/ops/`, `ops/`, `tests/` infrastructure, CI, Makefile | the live loop, the replay, alerts | pre-tip runs, settle, checkpoints, weekly forward health, cost report, replay oracle results | game-day-operator, forward-monitor, devops-ci, cost-monitor, `lead` (operations half) |
| **market** | `nba/markets/` | model forecasts, Kalshi state | fee-verified EV after spread, intervals, `no_positive_ev_found`, alias review, paper-trade log | markets-engineer |
| **scribe** | `nba/explain/`, `README.md`, `docs/` narrative (briefings, overview, journal intake) | stored forecasts, ledger, FAN_KNOWLEDGE | the game explainer, morning briefing, plain-language results, fan-hypothesis intake | analyst |

Adversary independence: the adversary has no write access to `nba/`, and any diff to `truth/` (the scoring
code, where a bug silently flatters everything) goes to the adversary before it is merged. The steward owns
`truth/` but does not judge itself.

### 5.1 The non-negotiables each agent enforces

| # | Non-negotiable | Enforced by | How (mechanism, not promise) |
|---|---|---|---|
| 1 | Decision rule committed before any result exists | modeler drafts, main session commits, steward guards | `truth/prereg.py: require_prereg(id)` refuses to start a fit unless the rule file is committed on `main`, the working copy equals the committed blob, and the committed hash matches the one the run is given; adversary verifies commit time < first artifact time |
| 2 | Holdout never touched without a row logged first | archivist, steward | `truth/holdout.py` returns holdout rows only to a caller holding a `touch_id` whose row exists in `HOLDOUT_ACCESS_LOG.md` at a commit earlier than the call |
| 3 | A claim carries n and a clustered CI | adversary | result notes lacking n + clustered CI are returned unread; `truth/bootstrap.py` is the only CI code |
| 4 | Nothing about a rule changes after a result is seen | adversary | diff of the rule file between prereg commit and result commit must be empty; a changed rule is a new id |
| 5 | No orders, credentials, execution code | market | a test greps `nba/markets` for order/auth endpoints; Kalshi client is GET-only on public routes |
| 6 | Money, production, promotions, outward-facing are Devin's | all; main session | `registry promote` and publishing require a Devin-authored commit or explicit flag; agents may only draft |
| 7 | Direction-setting choices get a DECISIONS line | archivist | a step that moves a module or changes an agent includes the DECISIONS row in the same commit; the steward's checklist fails without it |

Skipping one is itself reported: the scribe's morning briefing lists "rules enforced today" and any bypass.

### 5.2 How pre-registrations get committed

1. The modeler writes `docs/prereg/<id>.md` (metric, floor, CI type, clustering, multiplicity family, select
   season, report season, slices, what each choice protects against). Nothing about results exists yet.
2. The main session (the only agent that can commit) reads it with Devin, confirms it matches what he cares
   about, and commits it **to `main` before any fit**: `prereg(<id>): <sha256>`. The hash goes into the
   ledger header for the family (current practice, e.g. `sha256 ac87bd71`).
3. The fit entrypoint calls `require_prereg(id, sha)`; no commit, no run. Subagents cannot commit, so
   they cannot start from an uncommitted rule even by accident.
4. Results land in a separate commit; the adversary runs; the review note is committed; Devin decides.

A guard that can block Devin's own exploratory runs is a process change in its own right. The exploratory
wall stays: scratch work is free, a number becomes a claim only through a frozen test. ❓❓ (Q4)

---

## 6. The lineup as the unit (F11): the first new modelling abstraction

Why this and not another architecture: every individual-feature idea went null (tracking, hustle, RAPM,
ridge, sequences); every win was about *who is on the floor* (injury report, lineups). Devin's fan knowledge
says the same thing four ways (gravity, rebounding as a lineup property, closing lineups, pedigree decay),
and the stints we already own measure it: Draymond's make rate is 0.540 with Curry on vs 0.501 off
(984 vs 407 shooting possessions); league offense is +0.039 ppp with the team's top 3PM shooter on the floor
(600k vs 450k possessions); veterans get +7% Q4 floor time in close games, young players -2.5% (F12). This is
information, not architecture, which is the pattern that has worked.

The abstraction: **a player's forecast is conditional on the five around him, not only on his own history.**

```
Lineup(team, players: frozenset[5], known_at)           # unit of analysis, hashable, order-free
LineupTraits(lineup) = pool(trait(p) for p in players)  # permutation-invariant: sum / mean / max
   traits per player, as-of, shrunk by n_poss:  gravity (own 3P volume x accuracy),
   oreb_rate, dreb_rate, usage, ball_handling, closer_share, pedigree (decays over seasons 1-4, F9)
effect(player | lineup) = f(own recency average, LineupTraits(lineup minus player))
```

Pooling by sum/mean/max is the hand-built version of the original spec's DeepSets idea. It handles unseen
lineups by construction (compose the player traits), needs no neural net, and is shrunk by the same
empirical-Bayes primitive (`models/priors.py`) production already uses. It enters the system as **a feature
block of `context_residual`, not a new model**, so it inherits the baseline, the conformal intervals and the
integer-support scoring.

| Layer | What F11 adds |
|---|---|
| data | `silver/stints` + `silver/possessions` already exist; `gold/lineup_rates`: as-of on/off possessions, FG/3P/OREB with and without each teammate, n_poss, `known_at` |
| models | `models/lineup/`: `Lineup`, `LineupTraits`, `lineup_features(known, game, player)`; consumed by `context_residual`; absorbs the existing `props/lineup_features.py` (confirmed-starter features) |
| truth | one pre-registration per hypothesis (F8 OREB by on-court bigs; F9/F11 gravity and pedigree; F12 closer share); select 2023, report 2024; 2025 burned; the live log is the clean test |
| markets | no change; any gain flows through the prop distributions |
| explain | the "where did points come from" panel is the lineup view (s7), so the teaching and the modelling are the same object |

Known-at honesty: tonight's five are not known at tip-60. Pre-tip we use the **expected lineup distribution**:
the rotation weighted by projected minutes from the injury report and P(play), upgraded to the confirmed
starters when a T-30 snapshot exists (the t30 shadow arm already measures that gap). F12 (closer share
x game closeness) is pre-tip predictable from Elo margin; the lineup effect is not.

Honest priors on whether it works: the descriptive facts are strong, but "descriptive" is where the sim,
RAPM and ridge started too. The first pre-registration should be F8 (rebounding by on-court bigs), because it
has the cleanest mechanism and a measured live miss (HOU reb +0.16). Stop rule: if F8 and F11 each fail their
floors on the 2023-select / 2024-report split, the abstraction is archived as a descriptive tool for the
explainer and not pursued as a forecasting feature. A null is a result we keep proudly.

---

## 7. The game explainer (`nba/explain/`)

One page for one game (`python -m nba.explain game --id <game_id>` writes a self-contained HTML file; no new
dependencies, charts are inline SVG built from polars, no server). It reads stored forecasts and silver/gold
tables through a read-only handle and **never refits**: what you see is what we said before tip.

| Panel | Shows | Built from |
|---|---|---|
| 1. Who played, and why those minutes | per team: rotation bars (minutes) with a marker for predicted minutes and P(play); starters vs closers; stint timeline by quarter; flags: injury-report status at tip-60, foul trouble (foul clock from play-by-play), blowout garbage time, back-to-back | `silver/stints`, `player_game_stats`, `player_availability`, stored `minutes` forecast, PBP fouls |
| 2. Where the points came from | team points split by shot zone and by assisted/unassisted; per player pts = minutes x usage x efficiency (frequency-severity); lineup panel: the five-man units with ppp, their gravity/rebounding traits (F11) | `silver/possessions`, `gold/lineup_rates` |
| 3. What changed with a starter out | with/without table for the missing starter (his teammates' minutes, usage, rebounds vs their as-of baseline); what the vacated-stats feature predicted vs what happened | `player_availability`, injury-Elo `V_out`, context_residual features, stints |
| 4. Prediction vs outcome | win probability and outcome; per player a quantile fan (the stored 19-quantile grid, integer support for counts) with the actual drawn on it and its PIT value; log loss / CRPS contribution of this game; a running calibration mini-plot | stored `Forecast` rows, `forward_scores` |
| 5. What the market thought | the Kalshi/odds state at prediction time (not the close), implied probability, model minus market, EV after fee and spread, the verdict (`no_positive_ev_found` shown as plainly as a positive one) | `markets/odds` `market_at_prediction`, `markets/ev` |

Display rules inherited from DECISIONS (2026-10-09): true odds are shown identically after wins and losses
(same font, same size); a hit is annotated "this one hit; it was still a -X% bet". No stakes, no sizing prompts
on the page. Games without stored forecasts (before the forward log, or tipped before the job ran) say so
instead of recomputing.

Test bed: the 2025-26 replay database covers up to 1,316 games, which is a ready-made fixture
for building and checking the explainer before opening night, without touching the live path. Golden-file test
on the committed fixture game. Where it publishes (a local `site/` folder, a repo page, a shared link) is
outward-facing and therefore Devin's decision (rule 6). ❓❓ (Q7)

`parlay/assistant` (tool-grounded Q&A with a `numguard` that rejects numbers not returned by a tool) moves
to `explain/assistant/` as the conversational face of the same page. ❓ (Q10: do you use it?)

---

## 8. Sequencing

Calendar: today is Fri 2026-10-09. Opening night is Tue 2026-10-20. **Live-path freeze: end of 2026-10-17;
nothing changes 10-18 to 10-20** (the last supervised live run is 10-17).

### 8.1 The safety test, defined

"Byte-identical" means: replay 2025-26 through the real daily process on a DB copy and compare the canonical
dump. Canonical dump = every row of `forward_predictions` and `forward_scores`, sorted by
`(game_id, model, player_id, stat)`, with the stored `content_hash` and score columns, excluding
`run_id`, `made_at`, `scored_at`; sha256 of the CSV. Three cautions:

- **Day 0 must prove the oracle is deterministic**: replay the unmodified HEAD twice and require the two
  dumps to match each other. LightGBM threading or an unseeded step could make "identical" impossible, in
  which case the oracle is a tolerance, not a hash, and I want to know that before moving anything.
- The replay predates commit e8669d3 (write-on-change); the golden dump is regenerated on HEAD, not read
  from the 2026-10-09 run.
- A full replay costs about 105 minutes and 1.3-3.3 GB per chunk. Proposal: **after each step**, a chunk replay
  of three 20-date windows (opening week, a back-to-back-heavy window, a late-season window; about 30
  minutes) plus the full test suite; **after each wave**, the full 210-date replay. ❓ (Q11)

Also after every step, unpiped: `ruff check`, `ruff format --check`, `mypy`, `pytest`, a `python -m <entrypoint> --help`
for each launchd entrypoint (catches import errors), and the new layering test. Rollback = `git revert` to the tag.

### 8.2 Plan

| Day(s) | Step | Touches live path? | Gate |
|---|---|---|---|
| **10-09/10-10** (Day 0) | This document marked up. Tag `pre-restructure-2026-10-10`. Baseline: replay HEAD twice, store the golden dump; baseline `ruff/mypy/pytest`; snapshot the import graph; confirm no `route` alias is active (`nba.registry list`). | no | oracle deterministic or we change the oracle |
| **10-10** (Day 1) | Docs and agents only: six agent files (port the incident each one encodes: red-team's missingness precedent, game-day's "a missed pre-tip prediction can never be backfilled"); `docs/DECISIONS.md` rows; ledger back-fill rows for the `NO ledger row` groups; `research/INDEX.md` skeleton; the Day-1 import audit settles every "audit" mark in Appendix A. | no | Devin signs off the move table |
| **10-11/10-12** (Wave A) | Pure archive of modules with **zero live importers** (colab jobs and tooling, research evals, `models/rung1-4`, `winprob_family`, `injury_elo_t30`, `features/{pbp_tokens, game_sets, player_sequences, opponent_ridge_v2, rapm, tracking_features, ...}`, `preprocess/*`, `props/{run, stat_models, ...}`). `git mv` with their tests; `research/INDEX.md` rows; layering test added. The one DELETE (duplicate engine). | no (nothing live imports them) | tests, lint, types, import-help, chunk replay |
| **10-13/10-15** (Wave B) | Cut the entanglement edges in s3.3, one commit each, each followed by a chunk replay: report-trigger extraction, metrics/walkforward move, `feature_config_from`, routed branch, sim branch of `forward.py`, `pts_tail` flag. Then archive what those edges were holding (`sim/*`, `stack/*`, `registry/routing`, sim feature builders). | **yes (import-only or dead-branch removal)** | chunk replay byte-identical per step; **full replay at end of wave B (10-15)** |
| **10-13 onward, in parallel** (Wave C) | `explain/` skeleton built read-only against the 2025-26 replay DB and the fixture: panels 4 and 5 first (stored forecast vs outcome, market at prediction). Draft F8/F11 pre-registrations (documents only). | no (new code, not imported by daily) | golden-file test |
| **10-16** | Stop line for Wave B. If the full replay is not byte-identical, revert the last step; no new work after this. Tag `live-candidate-2026-10-16`. | - | full replay + whole suite |
| **10-17** | One supervised live run of the final tree (the same item already on `NEXT_SESSION.md`): schedule fetch, injury PDF, rosters, lineups, watchdog heartbeats, Kalshi snapshot. | verification | no alerts; forward rows present |
| **10-18 to 10-20** | **Hands off.** No commits that touch `nba/`, `ops/`, `configs/`, `uv.lock`. Docs and explainer work only if it cannot affect the running tree. | - | - |

### 8.3 Explicitly NOT done before 10-20

- Renaming or moving any live module into the four-layer tree (`data/`, `truth/`, `markets/` as paths), beyond
  the cuts in Wave B. This is a mechanical change with a long tail of import errors on paths the replay does not
  cover. After the season has some weeks of boring.
- The `daily/flow.py` rewrite (s4), including any change to exit codes, summary JSON or alert text that the
  watchdog and briefing parse.
- Anything in `ingest/`, `lineups/`, `kalshi/`, `markets/`, `ops/`, `parse/`: not covered by the replay, and the
  ingest queue and lineups collector hold the stats.nba.com quota. Left exactly as is.
- Data directory moves; schema changes (no `known_at` columns added to real tables); parquet or DuckDB layout.
- Dependency changes: dropping `torch`, editing `pyproject.toml` or `uv.lock`; launchd plists.
- Folding, removing or editing the shadow arms (`int`, `lt`, `t30`), `checkpoint.py`, `lt_live.py`, or any
  frozen section of `FORWARD_PREREG_2026_27.md`.
- Changing the official-roster / `recent` roster switch (due 2026-11-03).
- Fitting F8/F11 or any new model; promoting anything; any Kalshi alias work.
- Anything that changes a stored forecast byte (the oracle's whole point).

### 8.4 After opening night (proposed, loose)

| When | Wave | Gate |
|---|---|---|
| 10-21 to 10-27 | Observe. Fix only breakages. | first 5 game days clean |
| ~10-28 to 11-02 | Wave D: rename live modules into `data/ truth/ markets/` (mechanical), deploy after the last tip of a day, rollback tag ready | full replay byte-identical; next morning's run clean |
| after 11-03 | Wave E: `daily/flow.py` in dual mode (old and new write to two DBs, `content_hash` sets diffed daily); cut over after 10 consecutive empty diffs | empty diffs |
| rolling | Wave F: `Known(T)` retrofit of the two production models; drop `torch`; F8 pre-registration committed, then run; explainer goes from local to wherever Devin decides | per-item prereg and review |
| at 30/60/120 dates | arms decided under the frozen rules; `int`/`lt`/`t30` folded or deleted *then* | frozen checkpoints |

---

## 9. Risks, and what would make us stop

| Risk | Likelihood / impact | Mitigation | Stop condition |
|---|---|---|---|
| A refactor breaks the pre-tip job and a day's forecasts are lost forever | low / high (permanent hole in the only clean test) | oracle replay, import-only changes pre-10-20, freeze 10-18, deploy after last tip, rollback tag | any incident traced to the restructure: freeze all further structure work until the end of the season, keep the archive step |
| The oracle is not actually deterministic, so byte-identical cannot be checked | medium / high | Day 0 double replay | if two HEAD replays differ and cannot be made equal in a day: use a tolerance oracle with a stated tolerance and shrink Wave B to Wave A only |
| Replay does not cover the paths we touch (ingest, collectors, launchd, live fetch) | certain / medium | do not touch those paths; supervised live run 10-17 | any proposal to touch them before 10-20 is refused |
| Archive loses something we later need (F11 needs stints, pedigree, shrinkage) | medium / low | git tag; `research/INDEX.md` names the last commit where each module ran; F11 inputs are KEEP | n/a |
| "Delete half" is judged by lines, not by value | medium / medium | each archive group cites a ledger row; the back-fill rows are explicit | if a group's verdict is `PROVISIONAL` and Devin disagrees, it stays |
| The prereg/holdout guards block legitimate exploration | medium / low | scratch wall stays; guards apply to claims, not to scratch | if guards cost more than one false block a week, loosen to warnings and keep the adversary check |
| Six agents lose institutional memory in the 18 prompts (each encodes an incident) | medium / medium | port the incidents verbatim into the new files on Day 1 | n/a |
| The lineup abstraction repeats the sim's story: strong descriptives, null forecast | medium / medium | pre-register F8 first; stop rule in s6 | two nulls (F8, F11) on the select/report split: archive as descriptive explainer content |
| The explainer becomes a second product that eats the season (Devin has minutes a day) | medium / medium | read-only, no new deps, built on the replay DB; one page, five panels | if panels 1-3 need recomputation instead of stored data, cut them rather than refit |
| A restructure is attention spent not watching the live log | medium / medium | keep each wave to its dates; the weekly forward-monitor note is not skipped | restructure pauses for any week in which the forward report has an unexplained alert |
| Numbers in this document are estimates (line totals after extraction, module count after merges) | certain / low | Day-1 import audit replaces them | n/a |

What would make us abandon the whole plan, not just a step: (a) Day-0 shows the replay cannot serve as an
oracle and no substitute can be built in two days; (b) Devin decides the risk to the forward log outweighs
the benefit before opening night. Both leave an acceptable fallback: do only docs, agents and ledger
back-fill before 10-20, and resume the code moves in Wave D.

---

## 10. Open questions for Devin

The five that change the plan most are marked ❓❓.

- ❓❓ **Q1. Timing.** My recommendation: archive and cut edges before 10-18 (Waves A and B), but do every rename
  of live modules and the `daily/flow.py` rewrite after the first weeks of the season, in dual mode. The
  alternative is to do all of it before 10-18 (tidier opening night, more risk to a log we cannot backfill) or
  none of it before November (zero risk, a messier repo on the opening night). Which?
- ❓❓ **Q2. What does "archive" mean?** (a) move to `research/` in this repo, read-only, "reproducible at the
  tag" (my default); (b) same, but kept importable and tested in CI (costly, defeats the point); (c) a
  separate repo or branch with only a stub index left here; (d) delete, since git history keeps everything. And is
  it acceptable that archived code is not guaranteed to run?
- ❓❓ **Q3. Ledger back-fill.** Seven archive groups (RAPM, rung-4, win-prob family, time-decay, context
  screens, GA tune, experiment-1/2 prop runners) have a verdict in a doc but no `T###` row. Do we back-fill
  PROVISIONAL-ARCHIVAL rows (no new numbers, verdict copied, labelled) before moving them, or accept the doc
  as the record for pre-ledger work?
- ❓❓ **Q4. Hard guards.** Should `require_prereg()` and the holdout `touch_id` check be enforced in code
  (fits and holdout reads fail without a committed rule/row), or stay as reviewed process? Code makes the
  non-negotiables un-skippable, including by me; it can also block your own exploratory runs. Related:
  keep the three shadow arms in the live path until their 30/60/120-date checkpoints (my default), or fold `int`
  and `lt` earlier?
- ❓❓ **Q7. The explainer: who is it for, and where does it live?** Your own screen, a local `site/` folder,
  or something public? Publishing is outward-facing (rule 6) and yours to decide. The answer shapes what the
  page can show (a public page cannot show a stake-like number, even a disclaimed one) and whether `explain/` is
  built read-only against stored forecasts only (my plan).

Lower-order:

- ❓ **Q5.** Archive the Colab tooling (`push/pull/drive/status`, 0.6k lines) and drop the `colab-runner` agent?
  I would, because no GPU job is planned and information has beaten architecture every time, but you have 75
  units and may want a neural rung later.
- ❓ **Q6. `Known(T)` scope.** New code only (F11, explain) now, retrofit the two production models later
  (my default), or make it the target for all models in Wave F?
- ❓ **Q8. Names.** `truth` vs `scoring`, `research` vs `archive`, `gold` vs `features`. I picked the ones that read
  best to a stranger; they are cheap to change now, expensive later.
- ❓ **Q9. Registry.** `registry/model_gate` is research (it depends on the closed rung-ladder runner). I propose
  replacing it with a promotion check ("prereg commit, ledger row, holdout row, adversary note all exist"). Agree
  that promotion stays a manual, Devin-only step with that check as a precondition?
- ❓ **Q10. `parlay/assistant`** (tool-grounded Q&A, 1.5k lines): in or out of the explainer's scope? Do you use it?
- ❓ **Q11. Oracle cost.** Full replay is 105 minutes. Chunk replay per step plus full replay per wave
  (proposed), or full replay every time? Also: may the replay run while the ingest queue holds quota (it uses
  copies, no network)?
- ❓ **Q12. First lineup hypothesis.** F8 (OREB by on-court bigs, with a measured live miss on HOU) as the first
  pre-registration? Or F9/F11 gravity? Draft the documents this week; run after 10-20.
- ❓ **Q13. The team harness.** `team.py` (897 lines) and `team/tasks.json` queue agents by the old names. Updating
  it is a code change to a file nothing live depends on; do you want it in Wave A, or left for later?
- ❓ **Q14. Cadence for you.** With the restructure running next to the season, do you want the daily briefing to
  carry a "restructure status" line (step done / oracle result / next), or a separate weekly note?

---

## Appendix A. Every module: disposition and reason

Format: `module (relative to nba/) | lines | disposition | target | reason / ledger rows`. "Target" is the new
path in the `nba/` tree or `research/`. Rows marked "audit" are decided on Day 1 by the import audit. The 24
`__init__.py` files follow their packages (empty `data/__init__.py` becomes the new data layer's init).

| module | lines | disposition | target | reason / ledger rows |
|---|---|---|---|---|
| `colab/__main__` | 69 | RESEARCH | `research/colab_tools/` | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is |
| `colab/drive` | 81 | RESEARCH | `research/colab_tools/` | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is |
| `colab/jobs` | 91 | RESEARCH | `research/colab/` | job tooling |
| `colab/jobs/ctxres_v2_sweep/build_notebook` | 79 | RESEARCH | `research/colab/ctxres_v2_sweep` | T073-T080 (voided by the missingness leak, f1fcb91) |
| `colab/jobs/ctxres_v2_sweep/ctxres_v2_sweep` | 1,843 | RESEARCH | `research/colab/ctxres_v2_sweep` | T073-T080 (voided by the missingness leak, f1fcb91) |
| `colab/jobs/ctxres_v3_hybrid/build_notebook` | 99 | RESEARCH | `research/colab/ctxres_v3_hybrid` | T073-T080 family; superseded by leak-free exp 3 (T102-T118) |
| `colab/jobs/ctxres_v3_hybrid/ctxres_v2_engine` | 1,843 | DELETE | - | byte-identical duplicate of ctxres_v2_sweep.py (cmp); recoverable from git |
| `colab/jobs/ctxres_v3_hybrid/ctxres_v3_hybrid` | 413 | RESEARCH | `research/colab/ctxres_v3_hybrid` | T073-T080 family; superseded by leak-free exp 3 (T102-T118) |
| `colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree` | 372 | RESEARCH | `research/colab/ctxres_v3_leakfree` | T102-T118 (BROKEN reb/ast/fg3m, WOUNDED pts) |
| `colab/jobs/joint_game_set/build_notebook` | 75 | RESEARCH | `research/colab/joint_game_set` | T081 (ties Gaussian copula) |
| `colab/jobs/joint_game_set/joint_game_set` | 1,323 | RESEARCH | `research/colab/joint_game_set` | T081 (ties Gaussian copula) |
| `colab/jobs/pbp_gpt/build_notebook` | 74 | RESEARCH | `research/colab/pbp_gpt` | T082-T084 (not kept) |
| `colab/jobs/pbp_gpt/pbp_gpt` | 1,270 | RESEARCH | `research/colab/pbp_gpt` | T082-T084 (not kept) |
| `colab/jobs/ridge_v2_sweep/build_notebook` | 77 | RESEARCH | `research/colab/ridge_v2_sweep` | T098-T101 (gain was the leak) |
| `colab/jobs/ridge_v2_sweep/ridge_v2_sweep` | 773 | RESEARCH | `research/colab/ridge_v2_sweep` | T098-T101 (gain was the leak) |
| `colab/jobs/seq_props/build_notebook` | 68 | RESEARCH | `research/colab/seq_props` | T065-T072 (not kept) |
| `colab/jobs/seq_props/seq_props_train` | 433 | RESEARCH | `research/colab/seq_props` | T065-T072 (not kept) |
| `colab/pull` | 99 | RESEARCH | `research/colab_tools/` | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is |
| `colab/push` | 190 | RESEARCH | `research/colab_tools/` | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is |
| `colab/status` | 59 | RESEARCH | `research/colab_tools/` | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is |
| `coldstart/archetypes` | 692 | RESEARCH | `research/coldstart/` | M3A T048-T060 (not kept); only props.opponent imports it, itself a research candidate |
| `coldstart/carryover` | 62 | RESEARCH | `research/coldstart/` | TIME_DECAY_2026-10-08.md (not kept); NO ledger row |
| `coldstart/config` | 85 | RESEARCH | `research/coldstart/` | goes with archetypes |
| `coldstart/rookie_priors` | 99 | RESEARCH | `research/coldstart/` | unreferenced; F9 (draft-pick prior) will be rebuilt as a lineup/role feature, not from this |
| `coldstart/sb_classification` | 230 | RESEARCH | `research/coldstart/` | volatility buckets for sim routing (T005-T019, family C); only used when SIM_STATS is non-empty (it is empty) |
| `coldstart/shrinkage` | 117 | KEEP | `models/priors.py` | empirical-Bayes shrinkage; imported by props.minutes and stat models |
| `daily/__main__` | 284 | KEEP | `daily/__main__.py` | CLI unchanged: nba.daily run|settle|report|checkpoint |
| `daily/checkpoint` | 953 | KEEP | `truth/checkpoint.py` | frozen 30/60/120-date checkpoints, FORWARD_PREREG_2026_27 |
| `daily/com.nba-prediction.daily.plist.template` | 0 | KEEP | `ops/` | launchd template |
| `daily/ingest_step` | 72 | KEEP | `data/bronze/refresh.py` | incremental ingest |
| `daily/injury` | 192 | KEEP | `data/known_at/injury_report.py` | probe + pull of the injury report |
| `daily/lt_live` | 115 | KEEP | `truth/checkpoint_lt.py` | frozen live rule for the lt shadow arm; archive after its checkpoint |
| `daily/pipeline` | 723 | EXTRACT | `daily/flow.py + daily/arms/` | rewritten, section 3 |
| `daily/predict` | 566 | EXTRACT | `models/ (win+props wiring) + daily/flow.py` | split: resolve_production/injury_report_rows stay; routed/stack branch (apply_active_route, ~120 lines) goes to research |
| `daily/rehearsal` | 124 | KEEP | `truth/replay.py (merged)` | truncate_for_replay lives with the replay |
| `daily/replay_season` | 933 | KEEP | `truth/replay.py` | THE safety harness (210-date replay); promoted to first-class |
| `daily/report` | 292 | KEEP | `truth/report.py` | forward_report.md |
| `daily/schedule` | 134 | KEEP | `data/known_at/schedule.py` | real tip times |
| `daily/season` | 35 | KEEP | `data/known_at/schedule.py (merged)` | 35 lines |
| `daily/settle` | 272 | KEEP | `truth/settle.py` | scoring of stored forecasts |
| `daily/store` | 201 | KEEP | `truth/store.py` | forward_predictions append + LeakageError (the "refused after tip" rule) |
| `daily/t30` | 416 | KEEP | `daily/arms/t30.py` | shadow arm; frozen; decided at the checkpoints |
| `datamanifest/__main__` | 117 | KEEP | `data/manifest/` | structure diffs + missingness check (the leak detector) |
| `datamanifest/diff` | 125 | KEEP | `data/manifest/` | structure diffs + missingness check (the leak detector) |
| `datamanifest/manifest` | 320 | KEEP | `data/manifest/` | structure diffs + missingness check (the leak detector) |
| `db/connect` | 117 | KEEP | `data/db.py` | - |
| `eval/__main__` | 134 | RESEARCH | `research/eval/` | ladder CLI; registry.model_gate depends on it |
| `eval/backlog_small_eval` | 410 | RESEARCH | `research/eval/` | BACKLOG_SMALL_2026-10-08.md (T034-T041 family F) |
| `eval/context_residual_eval` | 521 | KEEP | `truth/backtest/context_residual.py` | production backtest (T092-T095) |
| `eval/context_screen_games` | 302 | RESEARCH | `research/eval/` | CONTEXT_SCREEN_*.md; NO ledger row |
| `eval/context_screen_players` | 674 | RESEARCH | `research/eval/` | CONTEXT_SCREEN_*.md; NO ledger row |
| `eval/ctxres_v2_coverage_sim` | 218 | RESEARCH | `research/eval/` | T073-T080 (voided by leak) |
| `eval/ctxres_v2_descriptive` | 700 | RESEARCH | `research/eval/` | T073-T080 (voided by leak) |
| `eval/ctxres_v2_eval` | 420 | RESEARCH | `research/eval/` | T073-T080 (voided by leak) |
| `eval/ctxres_v3_eval` | 368 | RESEARCH | `research/eval/` | T073-T080 family (voided) |
| `eval/ctxres_v3_leakfree` | 505 | RESEARCH | `research/eval/` | T102-T118 |
| `eval/fair_rematch` | 403 | RESEARCH | `research/eval/` | T062-T064 (sim lost every prop) |
| `eval/ga_tune` | 297 | RESEARCH | `research/eval/` | produced configs/mov_elo_tuned.yaml (config stays, GA code archived); NO ledger row |
| `eval/injury_elo_eval` | 386 | EXTRACT | `models/win/injury_elo.py (feature_config_from) + truth/backtest/` | the production backtest (T091, T128) stays runnable; daily.predict currently imports feature_config_from from it |
| `eval/injury_elo_t30_eval` | 232 | RESEARCH | `research/eval/` | T160-T161 (not kept) |
| `eval/integer_quantiles_eval` | 132 | KEEP | `truth/backtest/` | shadow arm int, T124-T127; archive at its checkpoint |
| `eval/joint_game_set_eval` | 867 | RESEARCH | `research/eval/` | T081 |
| `eval/lineups_known_eval` | 527 | RESEARCH | `research/eval/` | T085-T090, T096-T097 (WOUNDED; T-30 lives on as shadow arm) |
| `eval/lower_tail_eval` | 577 | KEEP | `truth/backtest/` | shadow arm lt, T136-T159; archive at its checkpoint |
| `eval/metrics` | 335 | KEEP | `truth/metrics.py` | log loss, Brier, CRPS, clustered bootstrap CI; merged with props.metrics |
| `eval/minutes_v2_eval` | 904 | RESEARCH | `research/eval/` | T162-T173 (props gain fails the floor in 2024) |
| `eval/model_routing` | 203 | RESEARCH | `research/eval/` | T005-T033 (routing families C-E) |
| `eval/pbp_gpt_eval` | 737 | RESEARCH | `research/eval/` | T082-T084 |
| `eval/player_points_sim_eval` | 304 | RESEARCH | `research/eval/` | T003, T061 (sim vs season avg) |
| `eval/player_reb_ast_sim_eval` | 349 | RESEARCH | `research/eval/` | T001-T002 |
| `eval/pts_tail_eval` | 308 | RESEARCH | `research/eval/` | T129-T135 (no candidate passes) |
| `eval/rapm_injury_elo_eval` | 377 | RESEARCH | `research/eval/` | RAPM.md; NO ledger row |
| `eval/report` | 165 | RESEARCH | `research/eval/` | ladder report |
| `eval/ridge_v2_eval` | 307 | RESEARCH | `research/eval/` | T098-T101 |
| `eval/routed_eval` | 148 | RESEARCH | `research/eval/` | T028-T033 (routing, provisional) |
| `eval/run` | 397 | RESEARCH | `research/eval/` | generic rung-ladder runner (rungs 0-2); rung ladder is closed |
| `eval/seq_props_eval` | 305 | RESEARCH | `research/eval/` | T065-T072 |
| `eval/slices` | 65 | KEEP | `truth/metrics.py (merged)` | 65 lines |
| `eval/time_decay_tune` | 411 | RESEARCH | `research/eval/` | TIME_DECAY_2026-10-08.md; NO ledger row |
| `eval/tracking_screen` | 455 | RESEARCH | `research/eval/` | T119-T123, T174-T175 (no family passes) |
| `eval/usage_redistribution_eval` | 803 | RESEARCH | `research/eval/` | T047 (inert null) |
| `eval/walkforward` | 141 | KEEP | `truth/holdout.py` | split_frozen_holdout, holdout-mode guard |
| `eval/winprob_family_eval` | 675 | RESEARCH | `research/eval/` | WINPROB_FAMILY.md; NO ledger row |
| `features/game_context` | 413 | KEEP | `data/gold/context.py` | used by props.minutes via team_features (audit: trim); screened as a win-prob feature in T-family F, not kept there |
| `features/game_location_context` | 227 | RESEARCH | `research/features/` | unreferenced; CONTEXT_SCREEN_GAMES |
| `features/game_sets` | 802 | RESEARCH | `research/features/` | T081 |
| `features/game_tipoff` | 118 | KEEP | `data/known_at/tipoff.py` | real tip-off gating; the most-cited fix in the project |
| `features/opponent_ridge_v2` | 1,116 | RESEARCH | `research/features/` | T098-T101 (the missingness leak lived here) |
| `features/pbp_tokens` | 1,335 | RESEARCH | `research/features/` | T082-T084 |
| `features/played_baseline` | 105 | RESEARCH | `research/features/` | fair-rematch helper (T062-T064); the live recency average is props.baselines |
| `features/player_features` | 130 | RESEARCH | `research/features/` | unreferenced |
| `features/player_pedigree` | 164 | KEEP | `data/gold/pedigree.py` | used by roster_cold (rookie minutes); home of F9/F11 pedigree features |
| `features/player_possession_features` | 633 | RESEARCH | `research/features/` | built only for the sim branch of props.forward (SIM_STATS = ()); T001-T033, T061-T064 |
| `features/player_rebound_assist_features` | 363 | RESEARCH | `research/features/` | same: sim branch only |
| `features/player_sequences` | 793 | RESEARCH | `research/features/` | T065-T072 |
| `features/possession_features` | 310 | RESEARCH | `research/features/` | same: sim branch only |
| `features/possession_step_features` | 455 | RESEARCH | `research/features/` | rung-4 heads; NO ledger row |
| `features/rapm` | 287 | RESEARCH | `research/features/` | RAPM.md; NO ledger row |
| `features/team_features` | 374 | KEEP | `data/gold/team.py` | used by props.minutes (audit: trim to what it calls) |
| `features/time_decay` | 335 | RESEARCH | `research/features/` | TIME_DECAY_2026-10-08.md; NO ledger row |
| `features/tracking_features` | 364 | RESEARCH | `research/features/` | T119-T123, T174-T175 |
| `ingest/__main__` | 352 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/arenas` | 71 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/availability` | 875 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/boxscores` | 208 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/cache` | 387 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/era_flags` | 135 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/game_window` | 148 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/games` | 140 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/history_injury` | 335 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/national_tv` | 155 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/pbp` | 149 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/players_static` | 280 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/postgame` | 737 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/queue` | 470 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/referees` | 428 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/team_advanced` | 156 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `ingest/teams` | 55 | KEEP | `data/bronze/` | write-once pullers; rate limits and queue unchanged |
| `kalshi/__main__` | 229 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/alias_review` | 337 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/aliases` | 111 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/client` | 223 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/cutoff` | 71 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/ingest` | 134 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/parse` | 326 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/sampling` | 65 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/snapshot` | 289 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `kalshi/thresholds` | 108 | KEEP | `markets/kalshi/` | read-only ingest; alias_review waits for player markets |
| `lineups/__main__` | 120 | KEEP | `data/bronze/lineups/` | T-30 collector (static JSON, one GET per tick) |
| `lineups/analysis` | 271 | KEEP | `truth/checkpoint_t30.py` | pairs T-30 snapshots with outcomes for the t30 checkpoint; archive after it |
| `lineups/collector` | 102 | KEEP | `data/bronze/lineups/` | T-30 collector (static JSON, one GET per tick) |
| `lineups/source` | 170 | KEEP | `data/bronze/lineups/` | T-30 collector (static JSON, one GET per tick) |
| `lineups/store` | 213 | KEEP | `data/bronze/lineups/` | T-30 collector (static JSON, one GET per tick) |
| `markets/__main__` | 58 | KEEP | `markets/odds/` | as-of market state and capture; "implied" = price to probability |
| `markets/asof` | 471 | KEEP | `markets/odds/` | as-of market state and capture; "implied" = price to probability |
| `markets/capture` | 276 | KEEP | `markets/odds/` | as-of market state and capture; "implied" = price to probability |
| `markets/implied` | 63 | KEEP | `markets/odds/` | as-of market state and capture; "implied" = price to probability |
| `models/base` | 54 | KEEP | `models/base.py` | - |
| `models/colab/export_training_data` | 110 | RESEARCH | `research/models/` | GPU export for rung 4 |
| `models/common` | 35 | KEEP | `models/base.py (merged)` | - |
| `models/injury_elo` | 673 | KEEP | `models/win/injury_elo.py` | PRODUCTION: residual on MOV-Elo logit |
| `models/injury_elo_t30` | 257 | RESEARCH | `research/models/` | T160-T161 (not kept) |
| `models/rung0_baselines` | 329 | EXTRACT | `models/baselines/mov_elo.py` | MovEloBaseline is production fallback; home-court-only and plain Elo go to research |
| `models/rung1_logistic` | 63 | RESEARCH | `research/models/` | rung ladder, closed; Elo beat it (RESULTS_2026-10-08) |
| `models/rung2_gbm` | 103 | RESEARCH | `research/models/` | same |
| `models/rung3_sim` | 140 | RESEARCH | `research/models/` | sim lost the fair rematch T062-T064 |
| `models/rung4_stepheads` | 590 | RESEARCH | `research/models/` | rung-4 heads lost; NO ledger row |
| `models/winprob_family` | 1,400 | RESEARCH | `research/models/` | WINPROB_FAMILY.md; NO ledger row |
| `ops/__main__` | 7 | KEEP | `ops/` | watchdog + exit codes |
| `ops/exitcodes` | 13 | KEEP | `ops/` | watchdog + exit codes |
| `ops/watchdog` | 353 | KEEP | `ops/` | watchdog + exit codes |
| `parlay/__main__` | 524 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/analysis` | 589 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/assistant/agent` | 161 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/backend` | 113 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/cli` | 89 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/config` | 45 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/names` | 71 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/numguard` | 93 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/render` | 220 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/toolbox` | 739 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/assistant/tools` | 190 | KEEP | `explain/assistant/` | tool-grounded Q&A with numguard (numbers must come from tools); fits the explainer |
| `parlay/budget` | 381 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/calibration` | 141 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/config` | 119 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/copula` | 152 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/engines` | 98 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/ev` | 207 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/game_model` | 182 | KEEP | `markets/ev/game_model.py` | audit: confirm shadow path calls it |
| `parlay/independence_check` | 598 | RESEARCH | `research/markets/` | verification study of cross-game independence (BEST_PRACTICES s4) |
| `parlay/joint` | 322 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/joint_eval` | 537 | RESEARCH | `research/markets/` | copula-vs-independence joint-calibration study |
| `parlay/kalshi_map` | 110 | KEEP | `markets/ev/legs.py (merged)` | - |
| `parlay/legs` | 71 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/narrate` | 79 | KEEP | `explain/narrate.py` | - |
| `parlay/papertrade` | 220 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/qdist` | 53 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/report` | 377 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/shadow` | 148 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/slate` | 121 | KEEP | `markets/ev/` | EV after fees, intervals, budget, shadow log, paper trades; "no_positive_ev_found" stays first-class |
| `parlay/totals_poisson` | 169 | RESEARCH | `research/markets/` | unreferenced |
| `parse/availability` | 839 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `parse/history` | 228 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `parse/lineups` | 475 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `parse/loader` | 74 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `parse/possessions` | 349 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `parse/reconcile` | 70 | KEEP | `data/silver/` | possessions, stints, lineups, availability; reconcile gate |
| `preprocess/depth_chart` | 243 | RESEARCH | `research/preprocess/` | unreferenced; revive if a rookie-role prereg is written |
| `preprocess/embeddings` | 240 | RESEARCH | `research/preprocess/` | sequence embeddings, unreferenced; rung 5 idea, closed |
| `props/__main__` | 145 | RESEARCH | `research/props/` | same |
| `props/baselines` | 88 | KEEP | `models/baselines/recency.py` | recency-weighted played-games average (half-life 10) |
| `props/coherence` | 322 | RESEARCH | `research/props/` | hierarchical reconciliation (spec phase 2); no ledger row |
| `props/combos` | 165 | RESEARCH | `research/props/` | PRA combos; no player-market for them |
| `props/config` | 335 | KEEP | `models/props/config.py` | trim opponent fields |
| `props/conformal` | 163 | KEEP | `models/props/conformal.py` | - |
| `props/context_features_v2` | 1,080 | RESEARCH | `research/props/` | T073-T080 (voided) |
| `props/context_residual` | 901 | KEEP | `models/props/context_residual.py` | PRODUCTION: GBM residual on recency average; drop pts_tail flag branch |
| `props/dispersion` | 192 | RESEARCH | `research/props/` | same |
| `props/distributions` | 477 | KEEP | `models/props/distributions.py` | NegBin / quantile shapes |
| `props/forward` | 1,014 | EXTRACT | `models/props/slate.py + models/baselines/recency.py` | 1,014 lines: recency baseline + context wrapper stay; sim/profile branch (~300 lines, dead behind SIM_STATS=()) goes to research |
| `props/full_support` | 115 | KEEP | `models/props/integer_support.py` | p_ge_full on integer support |
| `props/lineup_features` | 221 | KEEP | `models/lineup/features.py` | seed of F11: confirmed-starter / on-floor features |
| `props/lower_tail` | 178 | KEEP | `models/props/lower_tail.py` | shadow arm lt; fold in or delete at its checkpoint |
| `props/metrics` | 322 | KEEP | `truth/metrics.py (merged)` | CRPS and threshold log loss |
| `props/minutes` | 724 | KEEP | `models/props/minutes.py` | P(play) + hurdle minutes |
| `props/minutes_v2` | 575 | RESEARCH | `research/props/` | T162-T173 |
| `props/opponent` | 744 | RESEARCH | `research/props/` | M3A/T-family F matchup adjustment (not kept); props.config imports its config type - cut |
| `props/pts_tail` | 121 | RESEARCH | `research/props/` | T129-T135 (no candidate passes) |
| `props/report` | 297 | RESEARCH | `research/props/` | experiment report |
| `props/role_change` | 181 | KEEP | `models/props/role_change.py` | CUSUM |
| `props/rookie_minutes` | 325 | KEEP | `models/props/rookie_minutes.py` | opening-week rookies |
| `props/roster_cold` | 208 | KEEP | `models/props/roster_cold.py` | - |
| `props/roster_replay` | 458 | RESEARCH | `research/props/` | OPENING_WEEK_ROSTERS study |
| `props/rosters` | 231 | KEEP | `data/silver/rosters.py` | official rosters (data, not model) |
| `props/run` | 875 | RESEARCH | `research/props/` | experiment-1/2 runner (NegBin vs Normal) |
| `props/stat_models` | 359 | RESEARCH | `research/props/` | experiment-2 stat models |
| `props/volatility` | 270 | RESEARCH | `research/props/` | volatility buckets for routing, T005-T019 |
| `registry/__main__` | 341 | EXTRACT | `truth/registry/__main__.py` | list/promote; routes subcommand (lazy stack import) removed |
| `registry/bootstrap` | 240 | KEEP | `truth/bootstrap.py` | clustered bootstrap CIs |
| `registry/cli_guard` | 20 | KEEP | `truth/registry/` | adapter + local backend; promotion stays explicit |
| `registry/factory` | 41 | KEEP | `truth/registry/` | adapter + local backend; promotion stays explicit |
| `registry/local` | 198 | KEEP | `truth/registry/` | adapter + local backend; promotion stays explicit |
| `registry/metadata` | 88 | KEEP | `truth/registry/` | adapter + local backend; promotion stays explicit |
| `registry/model_gate` | 201 | RESEARCH | `research/registry/` | imports eval.run; rebuild as "prereg + ledger row + holdout row exist" check (see agents/steward) |
| `registry/ops` | 396 | KEEP | `truth/registry/` | - |
| `registry/protocol` | 50 | KEEP | `truth/registry/` | adapter + local backend; promotion stays explicit |
| `registry/routing` | 642 | RESEARCH | `research/registry/` | ROUTING.md/ROUTER_V2.md, T028-T033; daily.predict imports it today |
| `registry/smoke_guard` | 70 | KEEP | `truth/registry/` | - |
| `shared/version` | 7 | KEEP | `ops/version.py` | - |
| `sim/engine` | 190 | RESEARCH | `research/sim/` | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 |
| `sim/learned_heads` | 484 | RESEARCH | `research/sim/` | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 |
| `sim/player_attribution` | 873 | RESEARCH | `research/sim/` | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 |
| `sim/possession_model` | 120 | RESEARCH | `research/sim/` | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 |
| `sim/usage_redistribution` | 743 | EXTRACT | `data/known_at/injury_report.py (ReportTriggerConfig, load_report_rows, usable_report_rows, latest_pretip_flagged, serve_pretip_flagged, rotation_flagged_by_team, prior_minutes_state)` | THE entanglement: 6 live modules import report-trigger code from a research file; remainder (~500 lines, boost/redistribution) to research (T047) |
| `stack/adapters` | 142 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/artifacts` | 127 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/cells` | 107 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/data` | 102 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/evaluate` | 229 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/frozen` | 119 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/holdout` | 307 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/oof` | 183 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/populate` | 383 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/routebuild` | 364 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/router` | 329 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/scoring` | 55 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |
| `stack/threshold` | 114 | RESEARCH | `research/stack/` | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today |

