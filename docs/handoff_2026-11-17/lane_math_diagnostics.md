# Lane: Phase 6 math diagnostics (TDA regime flag, spectral parlay graph), report

Branch `agent/modeler-1117-math-diagnostics`, worktree `/Users/devin/Downloads/nba-wt-1117-phase6`, base 49ec9963. Started
Mon 2026-11-16 23:31 CST (`date`). Docs only: no code, no config, no fit, no score, no outcome read, no network, no
`nba.duckdb` opened. Advice or momentum: Devin's request (Phase 6 of the overnight brief); Claude recommends building neither.

## Files
- `docs/research/tda_regime_diagnostic_2026-11-17.md` (verdict PARK)
- `docs/research/parlay_spectral_diagnostic_2026-11-17.md` (verdict DROP)
- `docs/prereg/TDA_REGIME_DIAGNOSTIC_DRAFT.md` (DRAFT, not frozen; rule-text sha256 d2d40715 at this commit)
- `docs/prereg/PARLAY_SPECTRAL_DIAGNOSTIC_DRAFT.md` (DRAFT, not frozen, fails its own gates; sha256 f92ac293)

## TDA regime flag: PARK
Established: persistence diagrams are stable under perturbation; H0 persistence of a Rips filtration is the minimum spanning
tree, i.e. single-linkage clustering. Speculative: that a topological score on 10-40 games says anything about forecast
reliability beyond minutes sd, games since a starter change, or a k-NN distance from the training rows. It adds no pre-tip
information (same box scores), the information version already won as RECENT_ROLE (T458, T473), and regime routing failed or
never earned numbers (families C, D). Draft rule: top-10% flag per (season, stat); coverage gap of the stored [q10, q90] by
randomised integer PIT; TDA must beat ALL three baselines by >= 0.03, game-clustered CI > 0, BH over 12, 2023 and 2024;
label-free collinearity stop at Spearman >= 0.9. Gate: documented 27,619 / 27,701 rows per stat (2023 / 2024); the eligible
share (>= 40 prior played games) is UNKNOWN because `nba.duckdb` was out of bounds; needed >= 18%. Gate status: not
checkable tonight, expected to pass. Live 2026-27 rows are not units.

## Spectral parlay graph: DROP
Established: Fiedler > 0 iff connected; Cheeger bound; spectral clustering recovers separated blocks. Derived for this engine:
correlations are pooled by (relation, stat pair) with fixed shrinkage, so the Laplacian and every eigenvalue are a deterministic
function of the legs' relation pattern; for 2 legs the Fiedler value is |r|. It is a lossy summary of the matrix the copula
already uses (whose whole gain over independence is 0.14% of log loss, n 15,798 / 2,633 games). The measured parlay risk is leg
miscalibration compounding (T490/T492), which a correlation graph cannot see. The draft rule fails its information precheck
by construction (R^2 = 1) and has 0 forward units. Show the copula lift instead, once export field #4 lands.

## Counts measured (frozen export 2026-11-16, data_version 764432af8422, read-only, counts and shapes only)
Run via `cd app/api && uv run python -c ...` with `duckdb.connect(<export>, read_only=True)`. Every box/score query had
`season <> 2025`; the games table and parlay file hold season 2026 only.
- `parlay_of_day.json`: 1,355 records; `Counter(len(r['legs']))` = {1: 1355}; `engine` = {marginal: 1355}; legs per game
  {0022600250: 475, 0022600251: 403, 0022600252: 477}; leg stats fg3m 259, reb 256, pts 236, ast 221, spread 197, total 174, win 12.
  Multi-leg records: 0.
- Box history: `select season, count(*), count(distinct game_id), count(distinct player_id) from box_scores where season<>2025
  group by 1` -> 2022: 46 rows / 45 games; 2023: 500 / 383; 2024: 4,715 / 1,162; 2026: 5,628 / 197 games / 530 players.
- Prior stored games per player with a primary latest prop row: 506 players, quantiles 6 / 12 / 14 / 27 / 44 (10..90%), 335 < 20,
  75 < 10, 5 with none (`left join` of distinct `prop_grids where is_primary and is_latest` to non-2025 `box_scores` counts).
  This is the export's truncated window; not the gate.
- Games per night (`games`, season 2026, 2026-11-09..11-16): 4, 5, 6, 8, 8, 9, 9, 10 (8 nights, 59 games).
- `settled_scores` (counts only, `season<>2025 and game_id like '002%'`): 26 dates, 197 games; production and recency each
  4,354 scored + 2,215 dnp rows per stat. No `y`, score or metric column was selected.

## Gate verdicts
- TDA_REGIME_DIAGNOSTIC: cannot be checked tonight (needs a counts-only eligibility read of `nba.duckdb`); expected pass.
- PARLAY_SPECTRAL_DIAGNOSTIC: FAILS (information precheck by design; 0 forward units). Not freezable.

## Draft PREDICTIONS_LEDGER rows (not appended)
| Date | Rule / question | Prior stated before the result | Where written | Outcome | Hit? |
|---|---|---|---|---|---|
| 2026-11-16 23:31 | TDA_REGIME_DIAGNOSTIC G1 gate passes on 2023-24 (>= 5,000 eligible rows per season-stat) | P = 0.85 | docs/prereg/TDA_REGIME_DIAGNOSTIC_DRAFT.md (draft d2d40715) | pending | |
| 2026-11-16 23:31 | TDA_REGIME_DIAGNOSTIC collinearity precheck stops it (Spearman >= 0.9 with a baseline) | P = 0.35 | same | pending | |
| 2026-11-16 23:31 | TDA_REGIME_DIAGNOSTIC passes (any stat, both seasons, all three baselines) | P = 0.04; max D vs best baseline expected 0.00, 80% interval [-0.03, +0.01] | same | pending | |
| 2026-11-16 23:31 | PARLAY_SPECTRAL_DIAGNOSTIC information precheck I0 fires (R^2 >= 0.99) if ever run | P = 0.97 | docs/prereg/PARLAY_SPECTRAL_DIAGNOSTIC_DRAFT.md (draft f92ac293) | pending | |
| 2026-11-16 23:31 | PARLAY_SPECTRAL_DIAGNOSTIC passes | P = 0.01; D vs lift expected 0.000, 80% interval [-0.005, +0.005] | same | pending | |

## Draft DECISIONS row (not appended)
2026-11-17, Phase 6 math diagnostics. What: TDA regime flag PARKED, spectral parlay diagnostic DROPPED; neither built; no
`nba/` code. Why: no new pre-tip information; the spectral score is a function of the copula's pooled matrix; 0 multi-leg
forward records. Reverse if: a simple reliability flag passes its own rule and TDA is not collinear with it (TDA); per-player
correlations and a settled multi-leg forward log exist (spectral).

## Could not determine
- Eligible share for the TDA rule in `nba.duckdb` 2023-24 (not opened, by instruction).
- Whether `joint_eval`'s synthetic parlays include game legs in every parlay (affects graph connectivity, not the R^2 = 1 result).
- Citations (Cohen-Steiner et al. 2007, Fiedler 1973, Gidea and Katz 2018) are from memory; no network request was made.
- Power figures assume a game-cluster design effect of 1.3; not measured.
