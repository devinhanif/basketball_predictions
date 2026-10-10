# Replay oracle, Day 0 (2026-10-09)

Design: docs/DESIGN_RESTRUCTURE.md s8.1. Tool: `python -m nba.daily.canonical_dump`.

Window: the first 20 game dates of 2025-26 (2025-10-21 onward), replayed through the daily
process on a copy of `nba.duckdb` (`--max-dates 20`), about 30 minutes each.

| Replay | HEAD | predictions | scores | sha256 |
|---|---|---|---|---|
| oracle_a | a39a75e | 46,370 | 44,252 | e5714332a04c9552958fc91ce2b3ae3175729457b126fec8a3ab3ec42fb4d356 |
| oracle_b | a39a75e | 46,370 | 44,252 | e5714332a04c9552958fc91ce2b3ae3175729457b126fec8a3ab3ec42fb4d356 |

**Verdict: deterministic.** The oracle is a hash, not a tolerance. Golden dump kept at
`data/rehearsal/golden_head_a39a75e.csv` (not committed; regenerable from the tag
`pre-restructure-2026-10-10`).

Procedure for every edge cut: commit, then
`python -m nba.daily.replay_season --season 2025 --source nba.duckdb --db data/rehearsal/oracle_<n>.duckdb --out-dir data/rehearsal/oracle_<n> --max-dates 20`
followed by `python -m nba.daily.canonical_dump --db data/rehearsal/oracle_a.duckdb --db data/rehearsal/oracle_<n>.duckdb`.
A different hash reverts the cut.

| Step | Commit | Replay | Hash matches golden |
|---|---|---|---|
| 1. injury-report triggers out of nba.sim | 9de4302 | oracle_c | yes (e5714332) |
| 2. metrics + walk-forward to nba/truth | e9ff326 | oracle_d | yes (e5714332) |
| 3. feature_config_from next to its config | be4b11d | oracle_e | yes (e5714332) |
| 4. dead routed-props branches deleted | ed2b168 | oracle_f | yes (e5714332) |
