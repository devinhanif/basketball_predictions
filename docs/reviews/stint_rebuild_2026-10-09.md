# Stint / lineup rebuild with tracker v2 (2026-10-09)

Fix of `docs/reviews/stint_reconciliation_2026-10-09.md` (design: ADR 0002). All 5,269 games rebuilt
from cached PBP; `stints` and `possessions.off_players/def_players` rewritten in `nba.duckdb`.
`poss_idx` and every other possession column are untouched (the rebuild keys on the stored possessions).

## Reconciliation: share of player-games with |stint minutes - box minutes| <= 1.0 min (box minutes > 0)

| season | n player-games | before (stored v1) | after (v2) |
|---|---|---|---|
| 2022 | 27,737 | 49.1% | 98.0% |
| 2023 | 28,197 | 47.6% | 97.6% |
| 2024 | 28,118 | 46.9% | 97.3% |
| 2025 | 28,593 | 44.8% | 96.6% |

Target was >= 95% (accept >= 91%): met in every season. This is above the diagnosis prototype's 91.8%
(1,318 games) because of the extra name rule below (suffix-preserving match: "Jackson" vs "Jackson Jr.").
The "before" column reproduces the diagnosis (48.1% on 82,407) within season mix.

## Possession lineups, old stored five vs new five (offense, all 1,049,738 possessions with both)
all 80.2% (defense 79.5%); Q1 94.6%; Q2-Q4 75.4%. The diagnosis measured stored-vs-reference at 78.5% /
Q1 93.4% / Q2-Q4 72-75%, so the new tracker reproduces the reference's disagreement with the old one
(not an independent exact-five truth: no official lineup feed exists in the repo).
Equivalently ~20% of previously stored possessions had at least one wrong player on offense.

## What is still wrong (explained gap, not hidden)
- 981 of 5,269 games (18.6%) have at least one flagged substitution: 1,177 unresolved in-names
  (482 games) and 1,336 desynced subs (572 games), plus 94 team-periods with fewer than five evidenced
  openers (filled from the prior closing five) and 8 team Q1 look-ahead openers that differ from the
  box starters. Lists: `reports/lineups_rebuild/unresolved_subs.csv`, `desync_subs.csv`, `summary.json`.
- On a 1,757-game sample, 1,042 of 1,056 off-by-more-than-1-minute player-games sat in games with a
  flagged sub. Top unresolved in-names: Hansen 61, Poltl (diacritic spelling) 26, Edwards 19, Brown 15,
  Walker 14, Christie 14, T. Antetokounmpo 13. Cause: same-surname or never-named teammates where no
  initial evidence exists. Games with any flag can be excluded by joining those CSVs.
- Box minutes are the only ground truth; a 100% ceiling is not expected.

## Production dependency check (grep)
Production models (`rung0_injury_elo`, `props_context_residual`) read box scores and injury reports.
`nba/props/forward.py` copies `possessions.off_players` into its scratch DB and calls
`build_player_shot_rates`, whose on-court CTE reads `off_players` only when `use_oncourt_usage=True`;
no production path sets it (default False, rejected in the A/B). `context_features_v2.load_inputs_v2`
(on-court possession counts) is research-only (ctxres_v2/v3, NOT KEPT). `stints` is read only by
research code. Research that does read lineups (rapm, possession_step_features, embeddings, sim heads,
F11/F13/F15/F17) was computed on v1 lineups and should be rerun on v2.

## Review of the first rebuild: 957 negative stints, and what they were

Verification of the rebuild above found 957 stints whose end clock was later than their start clock
(2,084 negative minutes in total). The tracker sorted events by `action_number`; the feed appends
post-hoc corrections after "End of 4th Period" with the right period and clock but a late number
(example: game 0022400353, action 763, period 2, 10:15, "SUB: Poole FOR Butler", after the period's
last sub at 0:04.8). Applied at the end of Q2 this made a 10-minute negative stint and a wrong five.
4,512 such rows sit in 1,651 games; 638 are substitutions, the rest rebounds, shots and turnovers.

Two fixes were tried. Skipping a sub whose clock runs backwards lowered reconciliation to
96.5 / 96.1 / 95.5 / 94.5% (the corrections are real; applied in the wrong place they still fixed
game-total minutes by accident). Ordering events by (period, clock, substitution first at a tie) is the
fix that was kept:

| season | n player-games | v2 by action number | v2 by clock |
|---|---|---|---|
| 2022 | 27,737 | 98.0% | 99.1% |
| 2023 | 28,197 | 97.6% | 98.7% |
| 2024 | 28,118 | 97.3% | 98.6% |
| 2025 | 28,593 | 96.6% | 98.4% |

Desynced subs 1,336 -> 101 (59 in-player already on court, 42 out-player off court); Q1 look-ahead
openers disagreeing with box starters 8 -> 0; unresolved in-names unchanged at 1,177; team-periods
filled from the prior closing five 94 -> 93. About 1.0% of possession lineups change versus the first
rebuild (offense 99.03% agreement, Q2-Q4 98.84%). About 1% of player-games with box minutes have no
stint at all (never named in the PBP and not resolved); that is the main remaining gap.

## Reversibility and commands
Snapshots: v1 lineups in `data/backups/lineups_pre_fix_20261010T011007Z_{stints,possessions}.parquet`;
the first v2 rebuild (action-number order) in `..._20261010T014851Z_*`. Possession files carry
(game_id, poss_idx, off_players, def_players).
Rebuild: `uv run python -m nba.parse.rebuild_lineups --db nba.duckdb --write` (compute-only without
`--write`). `data_version`: e9b76c58a2d3 after the first rebuild, ff1db81f173a after the clock-ordered one.
