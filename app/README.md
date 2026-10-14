# parlayhitters app (local, fixtures only)

First milestone of the parlayhitters.ai PWA. Nothing is deployed, nothing is public, no accounts, no credentials,
no orders. It shows two historical 2024-25 slates (2024-11-12 and 2024-11-20) from stored, out-of-fold data. Design:
`docs/APP_ARCHITECTURE.md`, `docs/APP_CONTENT_SPEC.md`, `docs/APP_SECURITY_BASELINE.md`.

```
app/
  fixtures/   build_fixtures.py + the committed fixture JSON (season <= 2024 only)
  api/        FastAPI, read-only, own uv project (pyproject.toml, uv.lock, exact pins)
  web/        Vite + TypeScript + Preact PWA, own npm project (package-lock.json, exact pins)
```

The wall: nothing here imports `nba/`, and `nba/` never imports `app/`. When `nba.export` exists, only
`app/api/src/parlayhitters_api/store.py` changes.

## Run it (two terminals, from the repo root)

API (binds 127.0.0.1 only):

```
cd app/api
uv sync
uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Web, development server with hot reload (port 5173):

```
cd app/web
npm ci
npm run dev
```

Open http://localhost:5173. The browser only talks to its own origin; Vite proxies `/api` to the API on 127.0.0.1:8000.

Installable build with the service worker (the service worker is production-only), port 4173:

```
cd app/web
npm run build
npm run preview
```

Open http://localhost:4173, then use the browser's Install / Add to Home Screen. Offline, it serves the shell and the last
data fetched, with an "offline" banner.

## On a phone on the same Wi-Fi (no public exposure)

Start the API as above (it stays on 127.0.0.1; the phone never talks to it directly), then serve the web app on the LAN:

```
cd app/web
npm run dev:lan        # or: npm run build && npm run preview:lan
```

Find the Mac's address (`ipconfig getifaddr en0`; it was 10.50.2.136 when this was written) and open
`http://<that address>:5173` (or `:4173` for the preview) on the phone. Notes: both devices must be on the same network
and the router must not isolate clients; macOS may ask to allow incoming connections for node. A service worker and
"Add to Home Screen" need HTTPS or localhost, so on a phone over plain HTTP you get the app but not the install or offline
support. Stop the server when you are done; the API itself is never exposed.

## Checks (run unpiped)

```
cd app/api
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run ruff check ../fixtures/build_fixtures.py --config ../fixtures/ruff.toml

cd ../web
npm run check          # eslint, tsc, vitest, build
```

## Rebuild the fixtures

```
uv run --script app/fixtures/build_fixtures.py                          # the two default slates
uv run --script app/fixtures/build_fixtures.py --dates 2024-11-12 2024-12-05
```

Read-only. Opens `nba.duckdb`, `data/stack/oof.duckdb` and `data/odds/odds_history.duckdb` with `read_only=True` (retries,
then copies to a temp file if another job holds a write lock), every query carries `season <= 2024`, and the script refuses
any game from a later season. `fixtures/manifest.json` records the verbatim queries, the source `data_version`
(docs/data_manifests/latest.json), the git sha, the source hashes and the known stubs.

| Fixture field | Source |
|---|---|
| games, scores, player box-score outcomes | `nba.duckdb` games, player_game_stats |
| win probability (P home wins) | `data/stack/oof.duckdb` rung0_injury_elo, walk-forward out-of-fold |
| prop forecasts (19 quantiles, 0.05 to 0.95) | `data/stack/oof.duckdb` props_context_residual v1, out-of-fold |
| lines and prices, market win probability | `data/odds/odds_history.duckdb`, DraftKings T-60 snapshot |
| scheduled tip, team names | `data/schedule/raw_2024-25.parquet` |
| model-vs-market evidence | `reports/odds_history.md`, season 2024 section, copied |
| parlay-of-the-day state | `reports/props_analysis/2026-10-11.json` at git HEAD, copied (the engine's real no-parlay state) |

## API (read-only; GET only; CORS allows localhost origins only)

`/api/meta`, `/api/slates`, `/api/slates/{date}`, `/api/games/{id}`, `/api/games/{id}/result`,
`/api/props/{game_id}/{player_id}/{stat}?line=`, `/api/breakdown/{game_id}/{player_id}/{stat}?line=&side=`, `/api/slates/{date}/swipe?seed=&n=`, `/api/slates/{date}/results`,
`/api/parlay-of-the-day`, `/api/evidence`. Interactive docs at `/api/docs`. P(over) at a line is read from the stored
quantiles by interpolation with a continuity correction for integer counts: an approximation, labelled as one.

## Leg breakdown (`#/leg/<game>/<player>/<stat>`, one tap from every prop on the game page)

Last 10 and last 5 against the line (hit/miss chips, number, minutes), home/away, rest and back-to-back, this opponent,
rotation teammates out on the pre-tip report with how his numbers moved when they sat, what the opponent allows to his
position, alt-line ladder, and the model's chance. Stats: pts, reb, ast, fg3m, blk, stl, pra, pr, pa, ra, stocks. All of
it is counted in `api/src/parlayhitters_api/breakdown.py` from `fixtures/breakdown.json` (raw box-score rows, 2023-24 to
2024-11-23), strictly before the slate date. The model chance exists only for pts/reb/ast/fg3m; blocks, steals and combos
say "No model forecast for this stat yet" (a combo is never built from single-stat forecasts). Prices only where one is
stored (the DraftKings main line; steals has a stored main line, no stat has stored alt lines). Rebuild only this fixture:
`uv run --script app/fixtures/build_fixtures.py --only breakdown --data-root <checkout with nba.duckdb and data/>`.

## What is stubbed or limited

- The breakdown shows no minutes forecast (none is stored): it shows recent minutes and starting role, labelled as context.
  Its "model reason" text is built from the stored forecast and those recent facts, not from model internals.
- "Teammate out" effects use a rule (rotation player, on the team within 20 team games) and are small samples; n is printed.

- Replay mode: the clock is frozen 30 minutes before the first tip of the slate so the swipe tip rule can be exercised on
  history. The rule is implemented and unit tested; in live mode the clock is real time.
- Minutes are not forecast in the fixture; actual minutes and starters appear only as a result.
- Prices are DraftKings American odds, not Kalshi; the app never says a price is tradable.
- The parlay card is the engine's real current state (no parlay, gate closed), not a recompute.
- No accounts, server-side ledger, slip upload, leagues, chat or posting. The ledger lives in this browser's localStorage.
