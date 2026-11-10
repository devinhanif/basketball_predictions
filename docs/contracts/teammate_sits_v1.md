# teammate_sits export v1 (DRAFT; research/teammate_sits -> nba/export -> app Explore lens)

Status: **DRAFT, not wired.** Nothing reads or writes this until (1) TEAMMATE_SITS is frozen and run, (2) a separate branch wires it into
the nightly path with the replay oracle unchanged, and (3) Devin says go. Which stats may carry numbers is decided by the rule's words
(docs/prereg/TEAMMATE_SITS.md section 2); until then the app shows nothing from this file.

## What it is
For each regular starter P on tonight's slate who is not OUT on the latest official report, and each teammate T with a production props
row: the production forecast of T recomputed with P added to the report's OUT set (the same code that handles a real OUT player:
`vacated_features` on the OUT set, the same fitted heads, nothing refitted). It answers "if P sits, how does the model's forecast for T
move?". The baseline is production's own row; the export carries both so the app never mixes runs.

What moves on T's row: `n_out_rot` (+1), `vac_min` (+P's as-of 10-game minutes), `vac_<stat>` (+P's as-of 10-game mean). Nothing else.

## File
`data/export/<date>/teammate_sits.parquet` (or a `teammate_sits` table in the export bundle), one file per slate run, written by the same
run (same `made_at`, same model cache) as the production props rows it pairs with. A later run replaces it whole.

| column | type | meaning |
|---|---|---|
| `schema_version` | INT | 1 |
| `made_at` | TIMESTAMP (UTC) | the production run's made_at (must equal the paired props rows') |
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
