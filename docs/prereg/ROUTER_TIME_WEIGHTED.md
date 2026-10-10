# ROUTER_TIME_WEIGHTED: which model does each kind of stat best, weighted by who has been winning lately (pre-registration)

Status: FROZEN 2026-10-10 16:58 CT on Devin's confirmation (message: "do everything", 2026-10-10 ~19:30 CT; docs/DECISIONS.md). Drafted 2026-10-10 ~18:30 CT, NOT FROZEN. Committed by the maintainer's hand BEFORE any fit.
Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ROUTER_TIME_WEIGHTED.md | shasum -a 256` (first 8 hex chars into the
results section and every ledger row). Season labels are the repo's `games.season`: 2023 = 2023-24, 2024 = 2024-25, 2025 =
2025-26 (frozen holdout, never loaded).

## What Devin said
> "I thought it was gonna be which model does each kind of stat best; should we not also factor which model got better with
> time?" (2026-10-10). The router infrastructure (docs/ROUTING.md, docs/ROUTER_V2.md; research/stack/) was built and run on
> 2023-24 evidence only; no route was ever kept and no time-weighted gate was ever run.

## Hypothesis
H1: for some (stat, player-type) cells a candidate other than production is better, and a router that picks or weights
candidates per cell from their TRAILING performance (so a model that has been winning lately gets more weight) beats
production alone on integer-support CRPS. Null: production alone is as good or better everywhere.
Protects against: the per-cell "best model" tables being noise (the cell rule below), hindsight selection (weights are
computed only from blocks strictly before the scored block), and routing to a model whose edge was a proxy artefact
(every candidate's OOF rows are rebuilt on real tips by the same harness).

## Data exposure (honest)
Seen: production, recency-weighted and rolling-10/5 point MAE by stat, season and ppg tier (2026-10-10; production better in
every cell by 0.14-0.26 pts MAE); the exp-2 per-cell tables (docs/ROUTING.md, 2023 only); descriptive volatility buckets
(FAN_KNOWLEDGE, 2025-26 replay: cold starts 1.10 MAE/std vs ~0.80 elsewhere). Not seen: any time-weighted router result.
Season 2025 never loaded; `season <= 2024` asserted; `nba.duckdb` read_only. Selection 2023, report 2024.

## Fixed design (constants final)
Candidates (k = 5), each with per-row OOF integer-support forecasts on the SAME rows (played, `n_prior >= 5`), rebuilt by the
LOWER_TAIL/F11 harness (month blocks, train strictly before the block, 2022 warm-up, seed 0, CPU, real tips):
* C0 production `props_context_residual` v2.
* C1 recency-weighted distribution (`recency_weighted_dist`, the production fallback).
* C2 rolling-10 mean with the stat's production family (NegBin for counts with rolling dispersion; Normal for pts) fit on
  the prior 10 games; C3 the same with 5 games (Devin's "rolling 10, rolling 5").
* C4 T-30 lineups-known arm (`props_context_residual_t30`) where its rows exist; rows without a T-30 snapshot fall back to C0
  (declared; the fallback share is reported).
Cells: stat x ppg tier {<10, 10-17, 17-24, 24+} (prior-10 pts mean) x cold start {`n_prior < 20`, else} = 4 x 4 x 2 = 32.
Router R1 (pick): in each cell, use the candidate with the lowest exponentially weighted trailing CRPS (half-life 60 days of
blocks strictly before the scored block; a candidate needs >= 300 trailing rows in the cell, else C0). R2 (weights): softmax
over negative trailing CRPS with temperature tau = 0.05 (quantile mixture of the candidates' integer grids).
Both are walk-forward with no fitting beyond the trailing averages. The half-life and tau are fixed here, not tuned.

## Arms
A0 = C0 production. A1 = R1 pick. A2 = R2 weights. A3 = oracle (descriptive; best candidate per cell chosen on the SAME
block, leak by construction) = the ceiling. A4 = "best single candidate chosen on 2023" (the no-router comparison).

## Metrics, support, inference
Integer support for every arm. Primary: paired dCRPS (arm - A0) per stat per season, all rows, game-clustered bootstrap
(2000, seed 0). BH over the 4 stats per season for A1 and A2 (two families). Per-stat MDE beside each result. Per-cell tables
are descriptive unless the cell rule holds: >= 500 rows AND >= 150 distinct games, BH over all eligible cells. Secondary:
bias, A1.1 80% PIT coverage, lower-tail PIT, threshold log loss. Mechanism: how often R1 leaves C0 (switch rate) per cell;
A1 vs A4 (is the gain from routing or from one better candidate?); A1 vs A3 (how much of the ceiling is captured).

## Minimum data checks (fail = stop, no model result)
1. Every candidate has OOF rows for >= 95% of the production rows in both seasons (C4's own coverage reported separately).
2. All 32 cells have >= 300 rows in 2023; cells below are merged into their neighbour tier (declared merge order: up a tier).
3. Planted-future: shifting any block's rows one day later changes no weight for that block.
4. A0 reproduces production to 1e-6.

## Pass rule (per stat; 2023 selects, 2024 confirms; all must hold)
1. dCRPS point <= -0.005, CI upper < 0, BH p < 0.05.
2. Guards: |bias| <= 0.5; 80% PIT coverage in [0.75, 0.85]; no slice (cells with n >= 300) worse by more than +0.01 CRPS.
3. The passing router also beats A4 by point <= -0.003 (else the finding is "one candidate is better", recorded as that).
Secondary metrics cannot rescue a failure. No promotion; a confirmed stat gets a shadow-log recommendation and a drafted
holdout row. Season 2025 untouched.

## Kill criteria
No stat passes on 2023: close. Do not tune half-life, tau, cell definitions or add candidates afterwards; at most ONE
pre-registered follow-up (e.g. adding a passed F8/FOUR_FACTORS arm as a candidate), then closed.

## Outputs (exact)
`reports/prereg_router/results.json`, `reports/prereg_router.md`: `[stat, season, arm, n_rows, n_games, crps_int, dcrps,
ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`; `[stat, season, cell, n, n_games, best, dcrps_best_vs_c0,
ci_lo, ci_hi, p_bh, claim]`; `[stat, season, cell, switch_rate]`; `[test, ok]` for checks 1-4; seeds, SHA, sha256.
Code under `research/` only (`research/eval/router_time_weighted.py`, reusing `research/stack/`).

=== RESULTS BELOW ===

(not run)
