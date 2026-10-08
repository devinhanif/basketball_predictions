# Player availability / injury feed -- design note (2026-10-08)

## Why this exists
NEXT_SESSION.md flags "no forward-looking availability/injury feed" as the
#1 data gap: it blocks usage-redistribution (sim mechanism 2A/2B never
fires from history alone) and is the single highest-leverage new input for
points, since minutes uncertainty is the #1 prop-error driver (CLAUDE.md,
"Minutes model first"). This note documents what nba_api actually exposes,
the honest gap, and the schema/puller/parser groundwork landed this
session so a future pricing/scraping task can plug in without a schema
change.

## What nba_api actually has (verified against source/docs; network not
exercised live this session -- `nba_api` is not installed in this sandbox,
see "What I could not verify live" below)
- **`BoxScoreSummaryV2`** -> `get_data_frames()[3]` (`InactivePlayers`
  result set): `GAME_ID`, `PLAYER_ID`, `FIRST_NAME`, `LAST_NAME`,
  `JERSEY_NUM`, `TEAM_ID`, `TEAM_CITY`, `TEAM_NAME`, `TEAM_ABBREVIATION`.
  This is the **only** injury/availability-adjacent data nba_api exposes.
  It has three hard limitations:
  1. **Not forward-looking.** The endpoint only returns data once a game
     exists in the NBA Stats API, i.e. at/after that game's own tip-off.
     You cannot query it the day before to find out who's questionable
     for tomorrow's game -- by the time it has an answer, the game has
     already started (or finished).
  2. **No reason/severity field.** `InactivePlayers` says *who* didn't
     play, not *why* (no "left ankle sprain", no "DNP-rest" vs
     "DNP-injury" distinction) and no questionable/probable gradations --
     only a binary inactive/not-inactive outcome.
  3. **No pregame-minutes-risk signal** (no "questionable" tier at all;
     real NBA injury reports have out/doubtful/questionable/probable,
     this endpoint only ever says "inactive," discovered after the fact).
- No other nba_api endpoint (`CommonPlayerInfo`, `PlayerGameLogs`,
  `CommonTeamRoster`, etc.) carries injury status at all.

**Bottom line (the honest gap):** nba_api has zero genuine forward-looking
injury signal. The real pregame injury report the NBA publishes (out /
doubtful / questionable / probable, with reasons, ~5 times per game day
starting the afternoon before) is a PDF published outside nba_api
entirely. The table/parser below was designed so that source could be
added later with zero schema change -- **it has now been added** (see
"Official injury-report PDF source (added)" below); this section is kept
as-is for the historical record of what nba_api alone provides.

## What landed this session

### Schema: `player_availability` (`nba/db/schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS player_availability (
    player_id INT,
    as_of TIMESTAMP,       -- when this status became KNOWN (not game date)
    game_id VARCHAR,        -- nullable
    status VARCHAR,         -- 'out'|'questionable'|'probable'|'available'|'inactive'
    reason VARCHAR,          -- nullable
    source VARCHAR,          -- 'nba_inactive_list' | 'manual_announced'
    pulled_at TIMESTAMP
);
```
**Leakage contract** (spelled out as a comment directly above the table in
schema.sql, mirroring `team_context`'s discipline): `as_of` is the moment
the status became known, never the game date. A row is valid evidence for
a prediction at time T only if `as_of <= T`.
- `source='nba_inactive_list'` rows can **never** be used to predict the
  game they're attached to (the row only exists after that game's own
  tip-off) -- they're only safe as historical context for *later* games
  (e.g. feeding cold-start/role-change detection: "this teammate has been
  out the last 3 games, watch for a usage bump").
- `source='manual_announced'` is the slot for a real forward-looking feed
  once one exists; it carries whatever `as_of` a human curator (or future
  scraper) supplies, and callers still must enforce `as_of <= T`
  themselves -- the table does not enforce this at write time, only at
  query time, same as every other as-of table in this schema.

### Puller: `nba/ingest/availability.py`
- `pull_game_availability(con, game_id, ...)`: per-game, same
  cached/resumable/idempotent shape as `nba/ingest/boxscores.py` --
  `fetch_cached` with `allow_empty=True` (zero inactive players is a
  legitimate outcome, unlike an empty box score). Stores a raw JSON
  snapshot (`GameSummary` + `InactivePlayers` frames) under
  `data/availability_raw/<game_id>.json` before normalizing, for replay.
  Wired into the CLI: `python -m nba.ingest availability --game-id ... /
  --season ...`.
- `load_manual_availability_file(con, json_path, name_index, ...)`: loads
  a human-curated JSON snapshot (list of `{player_name, status, reason,
  as_of, game_id?}` records). Idempotent by file path (tracked in
  `ingest_log` under source `availability-manual`) -- reloading the same
  file is a no-op, matching "never refetch cached data." Copies the raw
  file to `data/availability_manual/` for replay.
- `build_name_index_from_cached_pbp(data_dir)`: builds the name resolver
  (see below) from whatever play-by-play has already been pulled -- there
  is no dedicated player-name table in this schema yet, so pbp's
  `player_name`/`player_id` columns are the best available source.

### Parser: `nba/parse/availability.py`
- `normalize_inactive_frame(...)`: `InactivePlayers` already carries
  `PLAYER_ID` directly -- **no name matching needed** for this source.
- `normalize_name` / `build_name_index` / `resolve_player_id`: fuzzy
  name -> player_id resolution for the `manual_announced` path (lowercases,
  strips accents/punctuation/Jr./Sr./II-IV suffixes; exact match first,
  then a high-confidence (`difflib` ratio >= 0.84) fuzzy fallback).
  **Fails loudly** (`UnmatchedPlayerNameError`) on anything below that
  confidence or any multi-candidate ambiguity, same discipline CLAUDE.md
  requires for Kalshi name matching -- a wrong silent match would attach
  an injury status to the wrong player, which is worse than no data.
- `parse_manual_announcements(...)`: validates `status` against the
  table's enum and requires `as_of`/`player_name` per record, raising
  `ValueError` on malformed input rather than silently dropping rows.

### Fixtures + tests (`tests/fixtures/availability_raw/`,
`tests/fixtures/availability_manual/`, `tests/ingest/test_availability.py`)
15 tests, all passing, no network: schema contract (`information_schema`
columns match `AVAILABILITY_SCHEMA`), row counts across 3 fixture games (2
inactive rows total, one game with zero -- proven to load correctly as
legitimate empty), idempotent re-load of a manual file (no duplicate
rows), fuzzy-match success/failure cases, and an **as-of leakage test**:
every row with a resolved `game_id` must have `as_of.date() <= game_date`
(a row can never claim a status *after* the game it's attached to).

## What I could not verify live
`nba_api` is not installed in this sandbox (`ModuleNotFoundError`), so the
exact `BoxScoreSummaryV2` frame shape above is from the package's
documented/known result-set layout (same lazy-import, no-network-at-import
pattern as every other puller in `nba/ingest/`), not a live call this
session. Per the task constraints (no multi-minute live network jobs), the
maintainer should run one real pull to confirm the frame shape before
relying on it at scale:
```
uv run python -m nba.ingest availability --game-id <a_recent_real_game_id>
```
If `InactivePlayers`' column names differ from what's assumed here (verify
via `BoxScoreSummaryV2(game_id=...).get_data_frames()[3].columns`), update
`_fetch_inactive_for_game`'s indexing/column names in
`nba/ingest/availability.py` accordingly -- the parser/schema/tests do not
need to change, only the raw-frame extraction.

## Official injury-report PDF source (added)
The real forward-looking source is now wired in, as its own
`source='nba_official_report'` path (not `manual_announced` -- kept
distinct so the two can be told apart in `player_availability`):

- **URL pattern** (verified against a real published report):
  `https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM.pdf`,
  12-hour zero-padded time (`09_45AM`, `03_00PM`), built by
  `nba.ingest.availability.official_report_url(report_dt)`
  (`strftime("%Y-%m-%d_%I_%M%p")`). Reports publish on a cadence (roughly
  :30/:45/:00 past the hour leading to tip) -- the caller supplies the
  exact published timestamp, this function does not snap to the cadence.
- **Parser**: `nba.parse.availability.parse_official_injury_report(path,
  name_index, report_dt=...)`. Table columns identified by stable `x0` left
  boundaries (gamedate/gametime/matchup/team/player/status/reason);
  `gamedate`/`gametime`/`matchup`/`team` are forward-filled across rows
  (and across pages -- they only print on the first player row of each
  game/team block). The one real parsing hazard: wrapped `reason` text can
  span multiple visual rows and sit above *or* below the player's own row;
  each stray reason fragment is assigned to the nearest player row by `y`,
  **scoped to the same page** (pooling this across pages was the one bug
  found during validation -- `y` resets per page, so a page-2 fragment can
  look "nearest" to a page-1 player compared globally). "Last,First" is
  reordered to "First Last" before `resolve_player_id`. ALL unmatched names
  in a report are accumulated and raised together in one
  `UnmatchedPlayerNameError` (not fail-on-first), so a single run tells the
  caller everything that needs fixing.
- **Puller**: `nba.ingest.availability.pull_official_injury_report(con,
  report_dt, name_index=..., data_dir=..., rate_limiter=...)`. Plain
  unauthenticated `httpx.get`, retried like every other puller's
  `_fetch_*_with_retry`. Resumable/idempotent by filename (never
  re-downloads an already-fetched report, tracked in `ingest_log` under
  `availability-official-report`); loading into DuckDB is an idempotent
  delete-then-insert keyed on `(source='nba_official_report',
  as_of=report_dt)`. Raw PDF bytes snapshotted under
  `data/availability_official/` for replay. Wired into the CLI:
  `python -m nba.ingest official-injury-report --date YYYY-MM-DD --time
  HH:MMAM/PM`.
- **Fixture + tests**: `tests/fixtures/injury_report/Injury-Report_2026-10-07_09_45AM.pdf`
  (2 pages, 19 players, BOS@DET + GSW@OKC) and
  `tests/ingest/test_official_injury_report.py` -- no network, parses the
  fixture directly. Proves: all 19 players parsed; the wrapped-reason case
  (Garza's reason spans a fragment above and below his row); the
  per-page-scoping fix (Ducas, page 2, gets his own reason and not
  Carter's, page 1, same visual-row `y`); name resolution via a hand-built
  index; `UnmatchedPlayerNameError` lists the missing name; status enum;
  `as_of == report_dt`; `game_id is None`; and the URL-format helper for
  both AM and PM times.

### Follow-ups (2026-10-08, later same day): both DONE
- **`game_id` resolution -- DONE.** `nba.ingest.teams.TEAM_ABBREV_TO_ID` is a
  committed, verified nba_api-static team-abbrev -> `team_id` literal (no
  network, no nba_api import -- cross-checked against this project's own
  `games` table, e.g. `ATL == 1610612737`). `nba.parse.availability.
  resolve_game_id(con, game_date, away_abbrev, home_abbrev)` looks up
  `games` for that exact `(game_date, home_team, away_team)`, with a
  swapped-orientation fallback query, and returns `None` (never raises) on
  an unknown abbreviation or no match. `parse_official_injury_report` now
  takes an optional `con` kwarg -- when supplied, each row's own
  (forward-filled) `GameDate`/`Matchup` columns are resolved into a
  `game_id`; `pull_official_injury_report` always passes its own `con`
  through. **`game_id` is best-effort**: it stays `None` whenever the game
  isn't in `games` yet (e.g. a report published for a game not yet
  ingested) or the matchup/date can't be parsed -- this is expected, not a
  bug, and is never an error. Omitting `con` (the old call signature)
  still works and still always returns `None`, so existing callers are
  unaffected.
- **Name-index coverage -- DONE.** `nba.parse.availability.
  build_name_index_from_static_players(active_only=True)` builds the name
  index from `nba_api.stats.static.players` -- a **bundled, offline**
  player list shipped inside the `nba_api` package itself (not a network
  call), scoped to active players (~530) so it stays small and
  near-collision-free. Any normalized-name collision is resolved
  deterministically (first-seen `player_id` kept, a `UserWarning` lists
  every collided key) rather than crashing the whole index build.
  `nba.ingest.availability.build_merged_name_index(data_dir=...)` unions
  this with `build_name_index_from_cached_pbp` (static takes precedence on
  overlap); the CLI's `official-injury-report` command now builds its name
  index this way, so a currently-active player who simply hasn't shown up
  in any cached play-by-play file yet still resolves, while unknown/bogus
  names still fail loudly via `UnmatchedPlayerNameError`.
- New tests: `tests/ingest/test_game_id_resolution.py` (resolve_game_id
  happy path, unknown date, unknown abbrev, swapped-orientation fallback,
  static-player index non-triviality, static-only player resolving via the
  merged index, graceful degrade to pbp-only if the static import fails)
  plus two additions to `tests/ingest/test_official_injury_report.py`
  (`con` supplied + games loaded -> both fixture games resolve; `con`
  supplied + empty `games` -> stays `None`, never raises).
