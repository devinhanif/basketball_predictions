# Worklog: autonomous run 2026-11-16 evening → 11-17

Integration branch `agent/integration-2026-11-17`, worktree `/Users/devin/Downloads/nba-wt-int-1117`. Nothing is merged
to `main`, nothing is pushed. Times are Central.

## Quickstart (verified 2026-11-16 18:50–19:00 CST)

```
# API on fixtures (two 2024-25 nights)
cd app/api && uv sync && uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8010
# API on the real export, read-only (second terminal, optional)
cd app/api && APP_EXPORT_DIR=/Users/devin/Downloads/nba-prediction/data/export/latest \
  uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8011
# web dev server, pointed at whichever API you want
cd app/web && npm ci && API_ORIGIN=http://127.0.0.1:8010 npx vite --host 127.0.0.1 --port 5183
# single-file artifact (bake reads the export read-only, then build)
cd app/api && APP_EXPORT_DIR=<export dir> PYTHONPATH=src uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json
cd app/web && npm run build:artifact      # -> dist-artifact/parlayhitters.html (+ preview.html)
# checks
cd app/web && npm run check               # eslint, tsc, vitest, build
cd app/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Open http://127.0.0.1:5183 (fixtures) or http://127.0.0.1:5184 (real export). Ports 8000 and 5173 were already taken by an
app started from the main checkout at 18:45; it was left alone, so this run uses 8010/8011 and 5183/5184.

## Phase 0 report

- `/api/health`: 200 `{"status":"ok","mode":"fixture_replay"}` on fixtures; 200 `{"mode":"export"}` on the real export
  (manifest `data_version 764432af8422`, made 2026-11-17T00:35Z, slates 11-09 … 11-16). Manifest and sha validated.
- Every API route answered on both: 200 everywhere except `/api/desk` 404 (no desk file, not private), `/api/top-parlays/{date}`
  404 (no file), `/api/live/{game}/snapshot|plays` 404 (no capture). Largest body: `/api/games/{id}` 255 KB on the export, 6 ms.
- Browser (headless Chrome over CDP, 390x844): today, game, leg, swipe, picks, parlay, record (3 tabs), live, signals, hedge,
  history, alerts, admin, prefs all render on fixtures; today, game, leg, swipe, parlay, record, live, signals, alerts on the
  real export. No blank page, no uncaught exception.
- Console errors seen: 404 for `/api/desk` on every page and for `/api/top-parlays/{date}` on Discover (the page asks for
  optional files and the browser logs the 404); `cdn.nba.com` headshots fail in headless Chrome (ERR_HTTP2_PROTOCOL_ERROR;
  the avatar falls back to initials). Not checked in a real, headed browser.
- Does not behave well: an unknown hash (`#/zzz`) silently shows Discover instead of a not-found screen; the artifact's
  Track record page scrolls sideways at 390 px.
- **The single-file artifact build was broken on `main`**: its own check refuses any `https://` text and the headshot host
  (commit 0cc5183c) tripped it. Fixed on this branch with a stand-in module for the artifact build (initials there). Built:
  5.51 MB, loads from `preview.html` with 0 console errors on discover, game, leg, record, live, swipe, picks.
- Baseline: web 319/319 vitest, eslint and tsc clean, build 321 KB js + 68 KB css. API ruff, format and mypy clean,
  pytest 434 passed, 1 failed (`test_the_committed_export_is_what_the_real_exporter_builds`: the real exporter now writes a
  `teammate_sits` table the committed fixture manifest lacks).

## Done
- Phase 0 (above).

## In progress
- Phases 1–3 on `app/`, by lane (see below).

## Next three steps
1. Launch lanes a (app/api), b (app/web hardening), d (DX scripts and docs) in their own worktrees.
2. Merge each lane into the integration branch only with its tests green.
3. Lane c (design system and UX) after b lands, then lane e (repo-wide fixes).

## Failures and things not run
- The repo-wide suites and the replay oracle have not been run yet (game window until about 22:00 CST).
