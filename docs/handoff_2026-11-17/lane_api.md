# Lane A: app/api audit, tests and hardening (overnight 2026-11-16/17)

Branch `agent/app-1117-api`, worktree `/Users/devin/Downloads/nba-wt-1117-api`, from `d5d9b058`.
Started 2026-11-16 18:59 CST, report written 20:00 CST (both from `date`).
Only `app/api/` and this file changed. `app/error_codes.json` was read, not changed: every code the
API now raises already existed (codes added: none). No fixture changed. Nothing under `nba/`.
Not pushed, not merged.

## How to run it locally

```
cd app/api
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
# the exporter comparison test runs only when the repo interpreter is given:
NBA_PYTHON=<repo>/.venv/bin/python uv run pytest
```

## Commits (oldest first)

| hash | theme |
|---|---|
| 1e5d81f2 | the failing exporter test, fixed honestly (absent table = 0 rows only if the fresh build wrote 0) |
| 8ae39298 | typed error contract from the registry, `X-Request-Id` on every response, one log line per request, plain 500 |
| 6440d0eb | one validation layer for string path params (`params.py`); seeded hostile-input tests |
| ca6c4f9c | path containment (live capture paths, top-parlays file) |
| 52641409 | private desk also needs a loopback peer; privacy crawl of every public route |
| 5fbd3b0d | bounded SSE streams: `stale` event, `end` + close after 30 min idle or 4 h |
| 36253504 | selective `prop_lines` read (memory and load time) |
| 31906274 | `/api/health` adds data age and manifest freshness facts (existing keys unchanged) |
| 3e7c5c4c | unreadable database or parlay file is a typed 503, not a 500; failure-injection tests |
| 6c937e57 | route contract tests |
| 412732f3 | split `main.py` (91 lines now) into `deps.py`, `views.py`, `routes/{system,slates,games,props,track,market}.py` |
| d6221d22 | split `breakdown.py` (657) into `breakdown_models.py` (shapes) and `breakdown_pure.py` (math, price words) |
| c838d9d3 | split `export_store.py` (~790) into `export_verify.py` (gate) and `export_rows.py` (pure row helpers) |

Every commit had ruff, ruff format, mypy and the full pytest green before it. Caveat, against the
house rule: during the night I read pytest's summary through `| tail -1`, so for those runs I
relied on the printed "N passed" line rather than pytest's exit status. The final runs on HEAD were
done without a pipe (output redirected to a file, exit status checked); see the last section.

## Numbers

| | before (d5d9b058) | after (c838d9d3) |
|---|---|---|
| pytest with NBA_PYTHON | 434 passed, 1 failed (exporter row_counts) | 533 passed, 0 failed |
| pytest in the worktree without NBA_PYTHON | 434 passed, 1 skipped | 532 passed, 1 skipped |
| hostile cases generated (seeded) | 7113 | 7113 |
| 5xx on hostile cases | 0 | 0 |
| refusals in the contract shape | 0 of 6819 | 6912 of 6912 |
| refusal names the bad field (or is the router's own 404) | n/a | 6907 of 6912; the other 5 are "." / ".." path segments the HTTP client resolves before sending, so a different route answers and names its own field |
| 2xx on hostile values | 294 | 201 (unknown extra query keys, harmless numbers such as `seed=+1`, client-normalised paths) |
| status mix on hostile cases | 404: 4377, 422: 2442 | 404: 4471, 422: 2441 |
| unopenable database sealed into the manifest | 500 plain text (ran on the baseline code) | 503 `export_refused` |

Generator: `app/api/tests/hostile_gen.py` (seed 20261117; ~150 fixed bad values plus 60 random ones
for every path and query parameter of every route, plus an unknown and a repeated query key per
route). `tests/test_hostile.py` runs all of them on every `pytest` (about 5 s) and also checks that
the bytes of every file under `app/fixtures` are unchanged afterwards.

### Route sizes and times

Real export, read-only through `APP_EXPORT_DIR`, warm, one request each, measured 2026-11-16
~19:05 CST on the export made 2026-11-17T00:35:06Z (59 games). Bytes are the uncompressed JSON body
(GZip applies at >= 1 KB). Live routes ran on the committed fixture capture; desk on the shared
fixture.

| route | bytes | ms |
|---|---|---|
| /api/games/{gid} | 224,103 | 9.5 |
| /api/slates/{date}/results | 116,675 | 16.2 |
| /api/breakdown/{gid}/{pid}/pts | 21,584 | 2.7 |
| /api/desk | 19,728 | 3.8 |
| /api/slates/{date} | 18,129 | 1.7 |
| /api/live/{id}/snapshot | 16,681 | 1.2 |
| /api/contracts/top | 8,072 | 10.9 |
| the other 20 routes | each < 4 KB | each < 3 ms |

### prop_lines and the cold load

`prop_lines` is not served directly; it is folded into per-prop quotes at load. Serving was never
slow (every route < 20 ms warm), so no index or pagination was added. The load was the problem:
the whole table (905,963 rows at 00:35Z, 948,120 at 01:05Z) was read into Python dicts to keep
283,345 quotes. Now DuckDB selects only the rows whose (quote run, game, player, stat) belong to a
shown prop, 13 columns, in table order (`export_rows._prop_lines_for`).

| frozen copy of the 01:05Z export, 2 runs each | baseline code | after 36253504 |
|---|---|---|
| ExportStore load | 4.70 s, 5.78 s | 3.64 s, 3.74 s |
| RSS after load | 1139, 1133 MB | 767, 768 MB |
| peak RSS | 1432, 1301 MB | 971, 973 MB |

Same quotes in the same order: `tests/test_prop_lines_load.py` on both committed exports, and a
hash of `store.quotes` from the baseline and the new code on the frozen real export (identical).

### Behaviour unchanged across the refactors

An oracle hashes every body (refusals as status + detail) of 2,261 URLs on a frozen copy of the
real export (every slate, game, result, swipe deck and timeline, plus 250 seeded props x 8 routes),
2,079 on the fixtures and 2,062 on `export_best_real`. Baseline code (git archive of d5d9b058) vs
the code after 6440d0eb, 36253504, 412732f3 and c838d9d3: 0 differing URLs every time. The
real export was replaced by a live job at 19:06 CST mid-run, so the oracle uses a frozen read-only
copy in the scratchpad, never the live directory.

## Audit findings, ranked (file:line at the baseline d5d9b058)

1. **HIGH, fixed 52641409.** The private desk gate trusted the `Host` header
   (`main.py:120-124`). Behind the README's `--host 127.0.0.1` every peer is loopback, so it held;
   but a server started on `0.0.0.0` would have served the private cut (stakes, P&L) to any LAN
   client sending `Host: localhost`. Now the peer must also be loopback (`deps._is_local_request`).
2. **HIGH, fixed 36253504.** Export cold load held ~1.1 GB resident and ~1.3-1.4 GB at peak on an
   8 GB machine that runs live jobs (`export_store.py:306`, whole `prop_lines` into dicts). During a
   reload the old and new store coexist, so the peak doubles briefly. See numbers above.
3. **MEDIUM, fixed 3e7c5c4c.** A database or parlay file that matches its checksum but cannot be
   read gave a plain-text 500 (`export_store.py:281`, `:798`). Now 503 `export_refused`; the
   detail names the error kind only (DuckDB messages carry absolute paths), the log has the rest.
4. **MEDIUM, fixed 5fbd3b0d.** SSE streams were unbounded (`live_routes.py:98-106`): no cap on
   duration and no signal when the capture stops growing; a stopped capture kept the stream, the
   watcher's 0.2 s polling and the slot alive until the viewer left. Heartbeats (`: hb` every 15 s)
   already existed. Disconnect cleanup was already correct (generator `finally`).
5. **MEDIUM, fixed 8ae39298.** No error contract or request ids; FastAPI's 422 detail echoed the
   hostile input back; only `ExportRefused` had a handler (`main.py:188-190`).
6. **MEDIUM, not changed (proposal 1).** A configured `APP_EXPORT_DIR` whose manifest disappears
   silently falls back to the 2024 replay fixtures (`store.py:120-128`). The mode and its note say
   "replay", so nothing is mislabelled, but a night's real data can vanish behind old data without a
   refusal. Pinned by `test_export_removed_mid_session_falls_back_to_the_labelled_replay` so any
   change is deliberate.
7. **LOW, fixed 6440d0eb.** Validation was per route and uneven: the live date was regex-only
   (`live_routes.py:41`, `2024-02-30` gave 200), `/api/edges` took any game id string and answered
   200 (`main.py:723-728`), the top-parlays 404 echoed the raw date (`main.py:764`).
8. **LOW, defence added ca6c4f9c.** Path traversal: none found. Live paths were built only after a
   digits regex (`live_routes.py:29-31`, `:41`); top-parlays already checked containment. Both now
   check at the function that builds the path too.
9. **LOW, not changed (proposal 2).** `_et_stamp` (`main.py:127-134`, now `views.py`) converts a
   naive timestamp as if it were the server's local time (CST/CDT here) before showing ET. Every
   current `made_at`/`built_at` carries `Z` or `+00:00`, so no wrong label today; `verify` accepts a
   naive `made_at`.
10. **LOW, by design.** CORS is GET-only, localhost-origin regex, no credentials
    (`main.py:169-175`). A refused preflight is Starlette's plain-text 400, outside the JSON
    contract (browser-only path; pinned in `test_route_contracts.py`).
11. **INFO.** Time handling: `made_at` is naive UTC in the DB and served with `Z`
    (`export_store._utc_iso`); `injury_report.report_as_of_et` is naive ET, converted with
    `zoneinfo` America/New_York (DST-correct). The only server-built zone label is "ET"; no CT, CST or
    CDT label is built server-side. Breakdown availability uses reports published >= 60 min before
    the real tip (`AVAIL_LEAD`).
12. **INFO, data honesty.** The API computes no EV; `/api/edges` and `/api/contracts/top` copy the
    engine's stored rows. It does compute, from stored fields: P(over/under/push) at any line
    (`forecast.py`: exact from the stored full-support table when complete, else linear interpolation
    of the 19 stored quantiles with a continuity correction, clamped at 5%/95% and flagged
    `extrapolated`; labelled by `p_source` / `model_p_source` and `method`); the calibrated number is
    shown instead where the export has one (`line_cal` at its main line, `rung_cal` at listed lines),
    and a model-only number is hidden where a calibrated one exists elsewhere (`no_cal_here`); market
    P(over) by multiplicative de-vig of a stored two-sided quote; P(Y >= k) for the card; the
    breakdown's ladder chances at each half line (same rules) and Wilson 95% intervals on hit rates.
    n travels with tallies, hit-10, the exact distribution (denominator) and snapshots (lead
    minutes). DNP: a player with no minutes has `actual` null, is skipped in results and in the guess
    deck. LINE_CAL_LIVE: results carry no model chance (pinned in `test_route_contracts.py`); the
    guess reveal and scenarios lock from 2026-10-30 (`trackrecord.MODEL_LOCK_FROM`). The lock is per
    route: `/api/games/{gid}` (model chances) and `/api/games/{gid}/result` (actuals) are separate
    routes a client could join; that is the web lane's side.
13. **INFO.** `ClaudeProvider` (`dissect/providers.py:69-93`) is a disabled stub: enabled only with
    `ANTHROPIC_API_KEY` and a model variable set, and even then `write` raises `NotWired`, which
    `dissect/service.py:17` catches and answers from the template. No network client is imported.
    Left in place: removing it is Devin's call.
14. **INFO.** Tracked binary fixtures: `fixtures/export_small/export.duckdb` 4.99 MB,
    `fixtures/export_best_real/export.duckdb` 4.73 MB, six live capture `.json.gz` (2-18 KB), plus web
    fonts and icons. The live capture fixtures are dated 2026-11-09 (season 2026, not the holdout,
    but not pre-2025 either).
15. **INFO, report only.** `nba/parlay/top_source.choose_primary` mirrors
    `nba/export/best_model.mark_primary` branch for branch (postseason prefixes 004/005, non-regular,
    missing AVG, fresh and not behind, inputs changed, run behind, bundle stale), with identical
    `_latest` / `_behind` / `_inputs_changed` bodies. Differences: the A0 keys are spelled
    `a0_source` / `a0_q19` in top_source and `_a0_source` / `_a0_q19` in best_model (if a loader fed
    the other spelling, A0 would read as missing and AVG would fall back as "inputs changed"; I did
    not check which keys each loader writes), and only mark_primary moves an AVG row's
    `lines_run_id` to its production counterpart.
16. **INFO.** The real export declares `box_history_seasons` [2022..2026], so box scores of season
    2025 are loaded as display history (allowed by the export guard). My measurements loaded that
    store; I computed nothing on any 2025 row and read no holdout table directly.

## Decisions (what / why / what would reverse it)

1. Exporter test: a table absent from the committed manifest counts as 0 rows only when the fresh
   build wrote 0 rows. / The exporter learned `teammate_sits` after the fixture was built; the
   synthetic source cannot fill it. / A fresh build that writes rows there (the test then fails, as
   it should) or rebuilding the committed fixture.
2. Malformed string params keep the status and words the route used before (404 + old detail),
   now with a code and field; two inputs changed outcome: a non-calendar live date (was 200 with an
   empty list, now 404 `not_a_date`) and a malformed game id on `/api/edges` (was 200 empty, now
   404 `unknown_game`); a malformed top-parlays date no longer echoes the input. / One rule for
   every route; no real id or date is affected (game ids accept 1-32 of `[0-9A-Za-z_-]`). / Drop the
   `Depends` validators in `params.py` for those routes.
3. Validation refusals (422) carry a plain sentence in `detail` instead of FastAPI's error list.
   / The list echoed the hostile input; the web client only reads `detail` when it is a string, so
   it showed nothing before. / If a client needs the structured list, add it under a new key.
4. Request ids are always generated by the server; an incoming `X-Request-Id` is ignored. / A
   client-chosen id could forge or split log lines. / A trusted proxy that sets ids.
5. Private desk requires a loopback peer as well; the shared `export_client` test fixture now
   connects from 127.0.0.1 (true of every request the API sees when bound to 127.0.0.1). / Host is
   the client's word. / None expected; only a deliberate LAN deployment would need a different rule,
   and that is Devin's decision.
6. SSE bounds: `stale` after 180 s without a new capture body, `end` + close after 1800 s without
   one, `end` + close after 4 h whatever happens (`live.STALE_AFTER_S`, `IDLE_CLOSE_S`,
   `STREAM_MAX_S`). / Server-side lifecycle without changing existing events; the current web client
   listens only for snapshot/delta/final (`app/web/src/useLive.ts:60-73`), so it ignores the new
   events and EventSource reconnects after a close to a fresh snapshot. / Set the constants to
   infinity; tune once the web lane shows `stale`.
7. `/api/health` keeps `status` and `mode` and adds `data_made_at`, `data_age_s` (null for the
   replay fixtures, and for a naive `made_at`), `data_version` and an `export` block copied from the
   manifest. Two existing tests asserted the whole body and now assert the two original keys. / The
   brief allowed added keys only. / A web need for a separate route.
8. After the `main.py` split the store cache lives in `deps.py`; three tests that patched
   `parlayhitters_api.main.load_store` now patch `parlayhitters_api.deps.load_store`, and a guard
   test proves that patch point reaches `get_store`. `main` still re-exports `get_store`,
   `reset_store`, `STATS`, `_swipe_empty_note`. / Routes cannot import from `main` without a cycle.
   / Moving the cache back into `main`.
9. Unreadable database or parlay file: refused as `export_refused` (503), detail names the error
   kind, never the message. / Same honesty rule as a checksum mismatch; DuckDB messages contain
   absolute paths. / None.
10. No new dependency; no fixture or registry change; `ClaudeProvider` kept.

## Proposals not implemented

1. Fail closed (503 with a new code such as `export_missing`, added to the registry first) when
   `APP_EXPORT_DIR` is configured but its manifest is gone, instead of serving the 2024 replay.
2. Read a naive timestamp as UTC in `views._et_stamp`, or refuse a naive `made_at` in `verify`.
3. Export-contract additions the health strip needs that the export lacks today (each would be a
   field written by `nba.export`, not an app change): odds capture last success and status per book
   (incl. Pinnacle freshness); per-prop `stale_model` (exists in the AVG shadow store, not exported);
   the nightly run's own status and last error; the live capture's newest body time per night; the
   JARVIS checkpoint snapshot's age; the exporter's version. Today the manifest has
   `primary_sources` counts (e.g. fallback_stale 6604) but no per-source freshness timestamps.
4. `/api/games/{gid}` is 224 KB uncompressed for one game (19 quantiles, survival tables and quotes
   for every player); worth measuring on a phone before trimming anything.
5. While an export is refused, each request re-hashes the 62 MB database under the load lock
   (~40 ms, serialised). Fine at today's traffic; cache the refusal per manifest key if it grows.
6. `export_store.py` is still ~800 lines, almost all the `ExportStore` class; a further split would
   be class-internal (builders per table) and was not worth the risk tonight.
7. Return the CORS preflight refusal in the JSON contract (needs a small custom CORS layer).

## Not verified

- The web app against the new error bodies, the `stale` / `end` events and the new health keys
  (web lane; I only read `api.ts`, `errors.ts` and `useLive.ts`).
- The API under real uvicorn for the error/request-id paths (TestClient only; the SSE tests do run a
  real uvicorn server). The log format under uvicorn's own logging config.
- Behaviour through a real cloudflared tunnel (only header simulations).
- Cold-request timings per route (only warm), memory under the live jobs' pressure, any mobile or
  light/dark check (no web changes in this lane).
- Which A0 key spelling each `nba` loader writes (finding 15).
- `git push` was not run (forbidden tonight); the branch is local only.

## Final runs on HEAD (c838d9d3 code)

- `ruff check`, `ruff format --check`, `mypy`: clean (79 files).
- `pytest` without NBA_PYTHON: 532 passed, 1 skipped (exit 0).
- `NBA_PYTHON=... pytest`, no pipe, output to a file: 533 passed, exit 0, finished 20:06:17 CST.
