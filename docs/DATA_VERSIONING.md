# Data versioning (lightweight, no DVC)

`python -m nba.datamanifest` records how the data's structure and content change over time and
stamps a `data_version` on every registered model run. No new dependencies; config in
`configs/datamanifest.yaml`.

## What a manifest holds
Per DuckDB table (and optional parquet file): schema hash, row count, primary/natural-key
duplicate count, per-season rows and game coverage (distinct games / `games` table), date
ranges, per-column NULL rate overall and per season. For player-game tables it also holds the NULL
rate split by played (`minutes >= 5`) vs not and their absolute difference (`asymmetry`).
Also: file count and total bytes for each `data/<dir>` cache (sizes only, no hashes).

Content fingerprint per table: `bit_xor` and sum of DuckDB's row `hash()`, order independent;
about 1 second on the full DB. `data_version` = 12-hex hash over schema + fingerprint of every
DB table, except output/log tables listed in `exclude_from_version` (experiments, ingest_log,
prop_predictions, ...), which change on every run and would make the stamp meaningless.

## Commands
| Command | Effect |
|---|---|
| `make data-snapshot` (`SNAPSHOT_ARGS="--parquet f.parquet"`) | writes `data/manifests/<UTC ts>_<version>.json` (ignored) and the committed `docs/data_manifests/latest.json` (counts, schema, null rates; no data values) |
| `make data-diff` / `diff --from X --to Y` | markdown diff, default latest vs previous |
| `make data-check` | exit 1 on any flag (latest vs previous plus a leak scan of the latest) |

The DB is opened `read_only=True`; if a writer holds the lock the tool retries with backoff for
at most `lock_retry.max_wait_s` (30 s), then exits 2 with `LOCKED`. It never writes to the DB.

## Flags
`ROW_DROP`, `TABLE_REMOVED`, `COLUMN_REMOVED`, `TYPE_CHANGE`, `COVERAGE_DROP`, `PK_DUPLICATES`,
`NULL_SHIFT` (overall or per season), and `POSSIBLE_LEAK`: a column whose NULL rate differs between
played and unplayed rows by more than `thresholds.asymmetry` (both groups >= `min_group_rows`),
reported by `check` for any column and marked NEW in `diff` when it was not asymmetric before.
Reviewed structural cases go in `known_asymmetry` (`table.column`).

## Registry
`build_run_metadata` adds `data_version`, read from the newest manifest file (no hashing per run;
`None` if no snapshot exists). Take a snapshot before registering runs, and after any ingest load.

## Limits
Fingerprints prove "same or different", not "what differs"; use `diff` and the per-season rows for
that. The played split relies on `player_game_stats.minutes`; feature parquet files are joined on
`(game_id, player_id)`. Rows with no box score are excluded from the split.
