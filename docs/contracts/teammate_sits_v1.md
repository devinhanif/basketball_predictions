# teammate_sits export v1 (WIRED on branch agent/market-teammate-sits-wiring; not merged, no launchd change)

Status 2026-11-10: TEAMMATE_SITS is frozen and run (T526) and attacked (T526-a: pts SURVIVES, reb WOUNDED, ast INCONCLUSIVE, fg3m
UNRESOLVED). Implemented in `nba/export/teammate_sits.py` (+ `sits_core.py`, the pure functions of `research/teammate_sits/scenario.py`
moved because `nba/` may not import `research`; `tests/export/test_sits_core.py` proves equality with the reference). Differences from
the draft below, all chosen from the attack:
- Only **pts and reb** rows are exported (ast and fg3m have no word to display, so they are not computed or stored). `rule_word` carries
  the frozen word (HELPS for both).
- The **baseline and the scenario come from the same recompute** (the morning model cache `data/models/context_residual/<date>`, the report
  usable at export time, production's roster), never from a mix of runs. The manifest's `teammate_sits.base_vs_production` counts how many
  rows differ from the newest stored `props_context_residual` row (a newer report between runs explains a difference).
- The table is `teammate_sits` in `export.duckdb` (always present, empty when absent); `manifest.teammate_sits` = `{status: ok|absent, reason,
  n_rows, n_scenarios, n_games, null_reasons, rule_sha8, stats, rule_words, base_vs_production, seconds}`. null_reasons also counts receiver_out (a teammate OUT on the report used is never a receiver). The cache fingerprint is compared in full (rows, newest date, config) like production's. Reasons: disabled, no_unfinished_games,
  no_model_cache, model_cache_stale (never refits), no_roster_rows, no_candidate_sitters, no_forecast_rows, over_row_budget (12,000 rows),
  over_time_budget (150 s, checked between stages), error:<ExceptionType>. The export never fails because of this step.
- Measured on a scratch copy of nba.duckdb, slate 2026-11-09 (8 games, nothing refitted): scenario step 2.78 s (full build 71.3 s without vs 76.2 s with; the difference is inside run noise), 2,024 rows, 79 scenarios, about 253 rows a game. base_vs_production against the stored 12:00 UTC run's props_context_residual rows: 190 compared, max abs diff 0.0049 (rounding), none over 0.05; the 15:20 run's 24 rows likewise. Against later runs the means differ by up to 3.8 because later reports changed (expected; the manifest counts it nightly).
- App: `/api/games/{id}/scenarios` sends only what the display rule allows (pts direction per teammate + one team-level number; reb direction
  only, "uncertain size"; nothing else) and sends nothing for a finished game on a LINE_CAL_LIVE-locked night.

## What it is
For each regular starter P on tonight's slate who is not OUT on the latest official report, and each teammate T with a production props
row: the production forecast of T recomputed with P added to the report's OUT set (the same code that handles a real OUT player:
`vacated_features` on the OUT set, the same fitted heads, nothing refitted). It answers "if P sits, how does the model's forecast for T
move?". The baseline is the SAME recompute with nobody added (cached morning heads, the report usable at export time), not the stored row; the export carries both so the app never mixes runs.

What moves on T's row: `n_out_rot` (+1), `vac_min` (+P's as-of 10-game minutes), `vac_<stat>` (+P's as-of 10-game mean). Nothing else.

## File
`data/export/<date>/teammate_sits.parquet` (or a `teammate_sits` table in the export bundle), one file per slate run, written by the same
run (same `made_at`, same model cache) as the production props rows it pairs with. A later run replaces it whole.

| column | type | meaning |
|---|---|---|
| `schema_version` | INT | 1 |
| `made_at` | TIMESTAMP (UTC) | the export's made_at (the build's clock; rows are a same-run recompute) |
| `game_id` | VARCHAR | |
| `team_id` | BIGINT | P's and T's team |
| `sitter_id` | BIGINT | P |
| `sitter_status` | VARCHAR | P's status on the report used (`available`, `probable`, `questionable`, `doubtful`, `unlisted`) |
| `player_id` | BIGINT | T (never P; never an opponent) |
| `stat` | VARCHAR | pts, reb, ast, fg3m |
| `base_mean`, `scn_mean`, `delta_mean` | DOUBLE | conditional-on-playing means, rounded to 0.01 |
| `base_q10`, `base_q50`, `base_q90`, `scn_q10`, `scn_q50`, `scn_q90` | DOUBLE | from the 19-point grid, rounded to 0.1 |
| `p_play` | DOUBLE | T's production P(play), UNCHANGED by the scenario (shown so the app can form `p_play * scn_mean`) |
| `rule_word` | VARCHAR | the stat's TEAMMATE_SITS word (`HELPS`, `CLOSED_TIE`, ...), or `UNTESTED` before the run |

Manifest (`manifest.teammate_sits`): `n_rows`, `n_scenarios` (distinct (game, sitter)), `n_games`, `null_reasons` counts, `rule_sha8`.

## Rows that are not produced (counted in `null_reasons`)
`no_report` (the game has no usable report: production has no vacancy there, so a scenario would invent one), `sitter_already_out`,
`not_counted` (P fails production's vacancy filters: fewer than 5 played games or min40 < 12), `not_regular_starter` (starter10 < 0.6),
`no_context_row` (T has fewer than 5 played games, so production routes T to the recency fallback, which has no vacancy input).

## Size per night (measured on 2024-25 rows, `reports/prereg_teammate_sits/counts.json`)
4.29 candidate sitters per team-game (max 7), 9.8 rostered teammates each; about 336 rows per game for four stats. Median slate (6 games)
about 2,000 rows; the largest (15 games) about 5,000. At about 60 bytes a row in parquet (dictionary-encoded ids, rounded doubles) that is
roughly 120 KB median and 300 KB at most; as JSON for the app about 3x that. Compute: one batched vacancy call plus four head predictions
on about 1,000 to 2,500 extra rows: seconds, no refit.

## What the app must say (and not say)
- Label: "If <P> sits: the model's forecast for <T>", never "will score". Show `delta_mean` only for stats whose `rule_word` allows it.
- Not modelled, to show on tap: who specifically absorbs P's role (the model spreads it by each teammate's own role, learned league-wide);
  T moving into the starting five; T's own chance to play; the opponent (unchanged by construction); the game result and pace; two or
  more sitters at once; team totals are not forced to add up.
- Questionable starters: the baseline already treats them as playing; the scenario is the "he sits" branch.
