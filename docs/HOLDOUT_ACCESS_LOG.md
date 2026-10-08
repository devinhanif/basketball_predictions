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
| 2026-10-08 | `scratchpad/run_points_retest.py` (points sim vs season-avg @ n_sims=2000) | points sim router candidate | pts | repeated-use (same rows as the n_sims=500 tie; full data) | PRE-REGISTERED exploratory diagnostic. Pooled against Family-A (m=3); ~3x underpowered; NOT confirmatory. Result appended on completion. |

_Append new confirmatory touches above this line. Points n_sims=2000 retest and
rung-4 `season=2025` confirmatory eval must each pre-register a row here before
running._
