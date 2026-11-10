# Adversary review: local player-name fallback for the export (2026-11-10)

Branch `agent/archivist-player-names`, commit `79d3332`. Files: `nba/explain/local_names.py` (new),
`nba/export/build.py` (`_names()` only), `tests/explain/test_local_names.py` (new).
Claim: export `players.full_name` placeholders drop 96 -> 0 using three local sources (rosters ->
lineups.duckdb -> `commonallplayers_2025-26.parquet`, a name list, not game data).

**Result: WOUNDED.** The names are correct: no wrong-name case found. Failure isolation is incomplete,
though. A non-integer or NaN `player_id` in any source raises `ValueError` through `_names()` and
fails the whole export. The brief asked for "never raises", and the code does not deliver that.

Verdict: BLOCKED

Lifted by fix F1 below (two lines). Nothing else blocks.

## Model class / playbook check
Not a model. This is a display-only lookup with no effect on predictions, scoring or the truth path.
- Information or capacity: n/a. Test validity: fixture tests plus a cross-check on real local data.
- Lineage and inherited guards: it inherits `names.player_name()`, so the static list still wins first.
- Scored against: the books' spelling by `player_id` (below). No holdout or game data is read.

## Checks

All scripts are in the scratchpad (`names/attack.py`, `names/odds_x.py`, `names/odds_y.py`,
`names/fail.py`). They were run with `.venv/bin/python -I` and the worktree on `sys.path`, against
the main checkout's `data/`. Every run exited 0 (shown unpiped).

| # | Check | n | Result | Pass |
|---|---|---|---|---|
| 1a | Same id, different name across the 3 sources (all ids, not a sample) | 5 conflicts / 169 local-only ids | 4 differ only in diacritics (`Biberović`/`Biberovic`, `Cissé`, `López`, `Vukčević`). 1 differs by suffix (`Labaron Philon Jr.` vs `Labaron Philon`). None names a different person. | PASS |
| 1b | One roster name held by more than one id (id reuse / two-way) | 630 roster files, 624 ids | 0 | PASS |
| 1c | Seeded sample of 20 local-only ids (seed 20261110) across sources | 20 | 18 agree on all 3 sources. 2 are lineup-only (`196700052 Landrius Horton`, `1643597 Robert McCray V`) with no second source to cross-check. | PASS (2 unverifiable) |
| 1d | Book spelling by `player_id` (`market_asof.duckdb` `live_book_odds`, read-only) | 21 local-only ids with book rows | 21/21 match after folding (accents, punctuation, Jr/II/III/V) | PASS |
| 1e | Merged map: the same name on more than one id | 40 | All are historic namesakes in `commonallplayers` (Patrick Ewing Sr/Jr, Mike James, ...). Also `196700042 Maxwell Lewis` and `196700051 Tarik Phillip`: the lineups feed carries a 1967xxxxx id beside the real NBA id. The name is still the right person. | PASS (note) |
| 2 | Lock: writer holds `lineups.duckdb`, reader opens it | scratch copy | Reader returns `{}` in 0.005 s with no wait. The reverse case is covered: `connect_lineups` retries 10 x 3 s, and the reader holds its shared lock for about 0.2 s (one `group by`). | PASS |
| 3 | Fault injection (scratch fixtures) | 9 cases | Garbage parquet, garbage or folder `lineups.duckdb`, a db without the view, garbage official list, an unreadable file or dir: all return `{}`. **A string `player_id` raises `ValueError`, and so does a NaN `player_id`.** | **FAIL** |
| 4 | Performance, full `data/rosters` (21 dirs, 630 parquet) | 2 runs | `load_local_names` takes 2.50 s cold and 0.58 s warm (roster alone: 3.55 s cold). It runs once per export. | PASS (minor) |
| 5 | Tests meaningful | 5 tests | They cover priority, the conflict log, newest snapshot wins, the placeholder, and missing sources. No corrupt-source test, no lineups-source test, and no locked-db test. Export tests call `_names()` without `fallback`, so they read the real repo `data/` (non-hermetic). | PARTIAL |

Verification on the branch worktree (unpiped):
- `uv run --offline pytest -q tests/explain tests/export -p no:cacheprovider`: 166 passed, exit 1. The exit 1 is only the repo-wide coverage gate on a subset run (3.96% < 70%).
- `ruff check`: exit 0. `ruff format --check`: exit 0. `mypy` on the 2 source files: exit 0.

## Findings (severity-ranked)

- **F1 (blocker): the export can fail because of names.**
  - Where: `local_names.py` `int(pid)` in `_roster` (`out[int(pid)] = ...`), `_lineups` and `_commonall`. `build.py:271` calls `load_local_names()` with no guard.
  - Fix: in `_names()`, wrap the call: `try: fallback = local_names.load_local_names().names` / `except Exception: log.warning(...); fallback = {}`.
  - Also filter in SQL so one bad row cannot sink a file: `where try_cast(player_id as bigint) is not null`.
  - Add a fixture test with a string id and a NaN id.
- **F2 (minor): export tests are non-hermetic.**
  - `build.py:270-271` uses `DEFAULT_DATA_DIR` (repo `data/`), not the export's `roster_dir`.
  - Effect: `tests/export/*` now read live rosters and open the live `lineups.duckdb` read-only, so outputs depend on the checkout.
  - Fix: thread a `data_dir`/`fallback` argument from `build_export`, or monkeypatch `load_local_names` to `{}` in a `tests/export` conftest fixture.
- **F3 (minor): the lineup source ids are not all NBA ids.**
  - `lineup_snapshots_dedup` carries 1967xxxxx ids. Names are correct, but only 2 of the 169 ids depend on the lineups source alone, with no second check.
  - Fix (optional): log `source == "lineups"` ids in the export manifest so a stranger can audit them.
- **F4 (minor): the roster scan cost grows with snapshots.** It runs 630 separate queries (2.5 s cold).
  - Fix (optional): use one `read_parquet('rosters/*/*.parquet', filename=true, union_by_name=true)` with `arg_max(player_name, filename)`.

Scope: the diff does what the task says and nothing more. No live-path writes. The loader is
read-only throughout.

## Most likely failure mode if the claim is wrong
A roster or lineup feed changes its schema (a string id or a NaN). The export then raises from
`_names()` on game night and ships no bundle, all for a cosmetic field. Wrong names are unlikely:
every id with two or more sources agrees.

## Not verified
- The exact 96 ids from "today's inputs". I checked the superset instead: all 169 local-only ids.
- Kalshi spellings. The alias files hold only lower-case keys, and the Kalshi tables have no `player_id`.
- Behaviour under the live lineups poller at the moment of export (simulated only on a scratch copy).
- No replay run, per the brief. No season-2025 game data was read. `commonallplayers_2025-26` is a name list.

## Drafted rows
None.
