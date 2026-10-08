# Perf fix: archetype per-date assignment (matchup 3A blocker)

**Scope:** `nba/coldstart/archetypes.py::build_player_feature_frame` and
`nba/props/opponent.py::_assign_archetypes_over_dates` (consumed by
`build_archetype_opponent_features`, the matchup 3A archetype-level
opponent factor). No numerical/behavioral change; speed-only fix.

## Hotspot (confirmed by profiling, not guessed)

`_assign_archetypes_over_dates` loops over every distinct `game_date` in
the history + target frame and, for each one, called
`build_player_feature_frame(con, date)`, which internally called
`build_player_shot_rates(con)` and `build_player_reb_ast_rates(con)` --
**two full-history SQL window-function queries over the WHOLE DB, re-run
from scratch on every single date**, even though neither function takes
`as_of_date` as a parameter (they return the full as-of history; the
per-date filter happens afterward in Python). Only `players_static`
needed an age recompute per date; the two rate builders were
date-independent and should have been computed once.

Profiled with cProfile + wall-clock timing on a synthetic DB sized to
roughly match real-DB date cardinality (10 teams, 100 players, 400 games,
80 distinct dates -- built in the scratchpad, not committed):

| Step | Before | After |
|---|---|---|
| `build_player_shot_rates` x80 (one per date) | 4.47s | -- (called once: 0.056s) |
| `build_player_reb_ast_rates` x80 (one per date) | 3.33s | -- (called once: ~0.04s) |
| `_assign_archetypes_over_dates` (labeling, 80 dates x 100 players) | 8.10s | 0.22s |
| `build_archetype_opponent_features` end-to-end | **11.29s** | **0.41-0.46s** |

**~25-28x wall-clock speedup** on this synthetic DB. The old cost scales
*linearly in the number of distinct dates* (each date = 2 more full-table
re-scans); the real multi-season DB has on the order of hundreds of
distinct game dates (this module's own pre-existing cost-note comment
already flagged "a few hundred SQL round-trips"), which is exactly why
real runs were ~7x+ slower than a baseline props run and thrashed the
8GB box (duckdb re-materializing the same large result set hundreds of
times, each one a fresh Python list -> polars DataFrame build).

## The fix

Hoisted the two date-independent full-history queries out of the
per-date loop:

- `nba/coldstart/archetypes.py`: split `_players_static_asof` into
  `_players_static_raw` (one DuckDB query, position fractions
  precomputed, no age) + `_apply_asof_age` (pure-polars, cheap, still run
  per date since age genuinely depends on `as_of_date`). Split
  `_latest_prior_tendencies` into `_latest_tendencies_as_of` (takes
  already-fetched `shot`/`reb_ast` frames) so the SQL queries aren't
  re-issued. Added `PlayerFeatureSource` (dataclass caching
  `static_raw` + `shot_rates` + `reb_ast_rates`), `build_player_feature_source(con)`
  (runs the 3 queries once), and `build_player_feature_frame_cached(source, as_of_date)`
  (the fast per-date scorer, zero DuckDB round-trips). The original
  single-date `build_player_feature_frame(con, as_of_date)` is now a thin
  wrapper (`build_player_feature_source` + `build_player_feature_frame_cached`)
  so every existing single-shot caller (e.g. the one-time ref-date fit in
  `build_player_archetypes`) is untouched.
- `nba/props/opponent.py::_assign_archetypes_over_dates`: builds one
  `PlayerFeatureSource` before the date loop, calls
  `build_player_feature_frame_cached` inside it instead of
  `build_player_feature_frame` -- no other logic changed.

No multiprocessing introduced (per the memory-bound constraint) --  this
is a pure redundant-work elimination, which also *shrinks* peak memory
(the two full-history DataFrames are built once and reused by reference
instead of rebuilt-and-discarded hundreds of times).

## Equivalence proof

1. `build_player_feature_frame_cached(source, d)` vs.
   `build_player_feature_frame(con, d)` (the untouched original code
   path) for every date in a synthetic multi-team/multi-date DB: **0
   mismatches**, verified with `DataFrame.equals` (exact, not
   approximate).
2. End-to-end: `_assign_archetypes_over_dates` (new, cached) vs. a
   reference re-implementation of the OLD per-date loop (calling
   `build_player_feature_frame` fresh every iteration, no caching) over
   the full set of distinct dates in the synthetic DB: **`.equals()` ==
   `True`** on the sorted `(player_id, game_date, archetype)` frames
   (8000/8000 rows identical).
3. Added as permanent regression tests in
   `tests/props/test_opponent.py`:
   `test_cached_feature_source_matches_per_date_query` and
   `test_assign_archetypes_over_dates_matches_naive_per_date_path`.
4. Full existing `tests/props/test_opponent.py` suite (20 tests,
   including the no-leakage, directional-correctness, per-cell-guard,
   and determinism tests already covering
   `build_archetype_opponent_features`) still passes unmodified.

## Test / lint status

- `uv run pytest tests/props -q --no-cov`: **142 passed**.
- `uv run pytest tests/ml/test_archetype_asof.py tests/ml/test_coldstart_archetypes.py -q --no-cov`: **passed** (part of the 34-test run reported above).
- `uv run ruff check` + `uv run ruff format --check` on the touched files: clean (ran `ruff format` once to apply 2 pre-existing-style wraps; no logic change).
- `uv run mypy nba/coldstart/archetypes.py nba/props/opponent.py`: `Success: no issues found in 2 source files`.
- Unrelated: `tests/ml` as a whole hit a pre-existing LightGBM native segfault in `test_rung2_gbm.py` on this machine -- unrelated to this change (no archetype/opponent code in that path); not introduced by this fix.

## Is matchup 3A unblocked?

Yes, from a performance standpoint: the per-date re-scan that was making
the archetype config ~7x+ slower than baseline (and swapping) is
eliminated. The archetype path should now cost roughly the same order as
the already-measured team-level config (which already completed) --
budget still a few minutes more than baseline for the two one-time
full-history queries plus the kmeans fit/predict calls, NOT the old
per-date blowup. **Maintainer should still time the real-DB `enabled=False` /
`use_archetype_factor=True` run before the full three-config A/B** (do
not assume the synthetic-DB ratio transfers exactly -- the real DB is
larger and has more distinct dates, but the *fixed* cost no longer scales
with date count the way the *broken* cost did). Suggested quick check
(maintainer-run, not this agent, per the "no multi-minute jobs" rule):

```
time uv run python -c "
from nba.db.connect import connect
from nba.props.opponent import build_archetype_opponent_features
con = connect('nba.duckdb', read_only=True)
target = con.execute('SELECT game_id, player_id, team_id, game_date FROM player_game_stats pgs JOIN games g USING(game_id)').pl()
out = build_archetype_opponent_features(con, target, k=6)
print(out.height)
"
```

If that completes in on the order of a minute or two (not 20+), the full
three-config A/B (baseline / team-level / archetype-level) from
`NEXT_SESSION.md` item #2 is safe to run.
