# Holdout access log — append-only

Binding ledger for every **confirmatory** touch of the project-wide frozen
holdout. Enforces the "at most once per model" rule from
[`ACCEPTANCE_CRITERIA_2026-10-08.md`](ACCEPTANCE_CRITERIA_2026-10-08.md) §2
instead of trusting it. Never rewrite history here; only append.

## Definition (binding)
- **Frozen holdout season:** `season = 2025` (the 2025-26 season) — one
  project-wide holdout, not one per domain. Mirrors the Elo discipline:
  `nba.eval.walkforward.split_frozen_holdout`, matching
  `nba/eval/ga_tune.py --holdout-season` (default `2025`).
- `season < 2025` is the tuning/CV pool. The props/routing paths now thread
  `holdout_season` / `holdout_mode` through `split_frozen_holdout`
  (`nba/props/config.py`, `nba/props/__main__.py --holdout-season/--holdout-mode`,
  `nba/eval/model_routing.py`, `nba/eval/routed_eval.py`) so a holdout split is
  reproducible from committed code, not reassembled by hand.

## Rules
1. A result may be called a **clean frozen-holdout confirmation** only if the
   model has **never before** been evaluated on `season=2025` rows. Everything
   else that re-touches `season=2025` is labeled **"repeated-use holdout,
   informal cross-check"** — not confirmatory.
2. **Pre-register** each touch: write the planned row below *before* running;
   fill the result in after.
3. **Rollover (standing TODO, re-check every session once 2026-27 exists):**
   when the 2026-27 season has ≥20 games ingested, re-designate the canonical
   props holdout as `season=2026`, fold `season=2025` back into the tuning pool,
   and never tune on 2026 before its first confirmatory use.

## Status as of 2026-10-08: NO VIRGIN HOLDOUT
Per [`FDR_AUDIT_2026-10-08.md`](FDR_AUDIT_2026-10-08.md) §3, `season=2025` rows
have already been looked at (directly or via walk-forward blocks) by essentially
every props / routing / matchup / minutes result produced to date. There is
currently **no genuinely virgin confirmatory holdout.** Treat every
`season=2025`-touching result below as repeated-use until rollover to 2026.

## Ledger

| date | run_id / command | model family | stat(s) | mode | result summary |
|---|---|---|---|---|---|
| 2026-10-08 | _(pre-session, unlogged)_ | props / routing / matchup / minutes (all prior work) | all | repeated-use | **Burned.** Retroactively marks `season=2025` as non-virgin; see FDR audit §3. |
| 2026-10-08 | `scratchpad/run_3a.py` (matchup 3A A/B) | props opponent-adjust (baseline/team/archetype) | pts,reb,ast,fg3m | repeated-use (full 2022-10-18..2026-06-13, no `split_frozen_holdout`) | Exploratory. Archetype factor regressed all stats; rebounds gate FAIL → no signal, flag off. Not a clean-holdout confirmation. |
| 2026-10-08 | `scratchpad/run_points_retest.py` (points sim vs season-avg @ n_sims=2000) | points sim router candidate | pts | repeated-use (same rows as the n_sims=500 tie; full data) | PRE-REGISTERED exploratory diagnostic. Pooled against Family-A (m=3); ~3x underpowered; NOT confirmatory. **Result:** sim 3.5671 vs season-avg 3.3772 CRPS, delta +0.190 clustered CI [+0.175,+0.206] on 138,409 player-games — sim significantly WORSE; points stays routed to season-avg. |

| 2026-10-08 | `nba.eval.injury_elo_eval --confirmatory-holdout 2025` (commit 84848f9 procedure, frozen) | win prob: rung0_injury_elo vs rung0_mov_elo | win | **CLEAN first touch** (injury_elo never evaluated on season=2025) | PRE-REGISTERED before running. Procedure frozen exactly as committed: MOV-Elo params from configs/mov_elo_tuned.yaml, injury coefficients fit by the same monthly walk-forward ridge refit (training = all games strictly before each month, incl. 2022-24), tip proxy 19:00 ET, 60-min report cutoff. Metric: paired per-game delta log loss vs MOV-Elo on all 2025 games, 2000-resample game bootstrap. CONFIRM if delta < 0 with 95% CI upper < 0; otherwise report as not confirmed. Run once; no re-runs or config changes after viewing. **Result: CONFIRMED.** 1316 games: log loss 0.6010 -> 0.5908, delta -0.0102 CI [-0.0180,-0.0026]; Brier -0.0038 [-0.0072,-0.0005]; ECE -0.0048 (CI spans 0). Gain concentrated in 2+ rotation-out games (-0.0123). Holdout now spent for injury_elo. |

| 2026-10-08 | `nba.eval.context_residual_eval --confirmatory-holdout 2025` (procedure frozen at the commit that adds this row) | props: context_residual vs recency average (both the raw recency baseline and recency_conformal) | pts, reb, ast, fg3m | **CLEAN first touch** (context_residual never evaluated on season=2025) | PRE-REGISTERED before running. Frozen procedure: same config/features/hyperparameters as configs/context_residual.yaml; month-block walk-forward continues into 2025 (each month trained on all played rows strictly before it, incl. 2022-24). Metric: paired per-row CRPS delta vs recency_conformal, played rows with >=5 prior played games, CI clustered by game (2000 resamples). Family m=4, BH q=0.05. CONFIRM a stat if its delta < 0 with CI upper < 0 and it survives BH; also report bias and 80% coverage. Run once; no re-runs or config changes after viewing. **Result: CONFIRMED all 4 stats** (28,071 played rows/stat, 1,316 games). Delta vs recency_conformal: pts -0.1206 [-0.1340,-0.1079], reb -0.0435 [-0.0482,-0.0391], ast -0.0258 [-0.0290,-0.0228], fg3m -0.0109 [-0.0125,-0.0092]; BH p 0.0005 each. Bias pts +0.104 [+0.038,+0.171] (within +/-0.5), others ~0; 80% coverage 0.796-0.804. Holdout now spent for context_residual. |

| 2026-10-08 | `nba.eval.ctxres_v3_eval --run <run_id> --prereg-id EXP3-2025-897ebde` (job `ctxres_v3_hybrid`; procedure frozen at commit 897ebde, docs/CTXRES_V3.md) | props: per-stat hybrid (pts `xgb_v12_quantile`; reb/ast/fg3m `xgb_v12_poisson_nb`; exp-2 run 20261008_171446 configs, Platt on pts thresholds only) vs production `v1_prod` refit identically, plus recency baseline | pts, reb, ast, fg3m | **CLEAN first touch** (hybrid and its components never evaluated on season=2025; `v1_prod` comparator is a refit of an already-touched model, comparator only) | PRE-REGISTERED before running (prereg id EXP3-2025-897ebde). Month-block walk-forward over 2025, each block fit on all played rows strictly before it (incl. 2022-24 and earlier 2025 months); frozen configs, no search. Metric: exp-2 keep rule per stat (CRPS delta <= -0.005, game-clustered 95% CI upper < 0, BH q <= 0.05 over m=4, no slice regression > 0.01, |bias| <= 0.5, A1.1 PIT coverage 0.75-0.85, leak audit passed); hybrid kept overall only if all 4 pass. Composition chosen after viewing 2024 results (stated in docs/CTXRES_V3.md). Run once; no re-runs or config changes after viewing. **Result: VOID — NOT RUN (2026-10-08 19:35).** Before any 2025 row was scored, the ridge-v2 audit found a leak in the v2 feature `opp_adjusted_ridge` (NULL exactly when tonight's minutes < 5). The hybrid's feature set includes it, so this touch was cancelled unrun; season 2025 remains untouched for the hybrid. A leak-fixed experiment needs a NEW pre-registration row. |

_Append new confirmatory touches above this line. Points n_sims=2000 retest and
rung-4 `season=2025` confirmatory eval must each pre-register a row here before
running._

**NOTICE 2026-10-09 (red team, docs/reviews/redteam_production_2026-10-09.md):** both 2025 confirmatory touches above (injury_elo and context_residual) were gated with the 19:00 ET tip PROXY, which admits the 17:00 ET report snapshot for games that tipped at or before 17:00 (about 7.7% of games). The Elo effect is nil (-0.00786 proxy vs -0.00812 real tip on 2022-24), but the props pts holdout figure is likely overstated by about 0.003-0.004 CRPS. No re-touch of 2025 has been performed or is planned. The label "CLEAN first touch" means clean for those models only, not for the research programme (some design inputs were chosen with 2025 visible elsewhere). Real-tip gating is now available behind `tip_source="real"` (production default unchanged); corrected 2022-24 OOF numbers are in docs/TEST_LEDGER.md.
