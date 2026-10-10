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
| 7. OpponentAdjustmentConfig into props/config | a9d7a69 | oracle_g | yes (e5714332) |
| 6. dead pts_tail flag removed | 32e89f2 | oracle_h | yes (e5714332) |
| 5. possession-sim branch out of props/forward; SB buckets to features | b402035 | oracle_i | yes (e5714332) |
| 8. shrink_rate to features/shrinkage; ratchet at zero | 2f88853 | oracle_j | yes (e5714332) |

## Wave B gate: full 210-date replay on the restructured code (2026-10-10, 00:53)

| Check | Result |
|---|---|
| Dates / failures | 210 / 0 |
| Rows | 419,729 predictions, 391,911 scores |
| First 20 dates vs the Day-0 golden run (oracle_a), compared as sets | identical, 46,370 predictions and 44,252 scores |
| Season metrics vs the 2026-10-09 replay on pre-restructure code | identical to 4 dp: injury-Elo LL 0.5920 / Brier 0.2036 (n 1,311); MOV-Elo 0.6011 / 0.2069; context-residual pts CRPS 3.3771; recency 3.5082 |
| Full-season hash (total order, below) | 267ca43361c5b40ecd4ece72ce7da9586edd9951c95cece531ae5e12e33e49d1 |

Tool fix found by this gate: the canonical dump ordered rows by the key only, and a player-game has one
row per pre-tip snapshot, so 2,118 tied groups in a 20-date replay were ordered by insertion, not
content. The order is now total (every kept column). Under the total order the Day-0 pair and edge 8 are
still identical to each other (sha 732fba310e4f0c63...); the earlier e5714332 values were correct
comparisons under equal insertion order. The full-season dump is the new golden:
`data/rehearsal/golden_full_wave_b.csv`. Tag: `live-candidate-2026-10-10`.
