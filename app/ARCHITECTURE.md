# App architecture, one screen

Read-only. The app shows what the engine stored; it never computes EV. Run it with `app/scripts/dev.sh`; see `README.md`.

```
nba.export files            app/api (FastAPI, GET only)                    app/web (Vite + Preact)
 export.duckdb         -->  export_store.ExportStore  --\
 manifest.json (sha)        store.Store (fixtures)       +--> main.py routes --> transport.ts (fetch) --> api.ts --> pages/*
 parlay_of_day.json         live.py (capture bodies)  --/                        /api proxied by Vite
                                                         \
                            bake_artifact.py (TestClient over the same routes) --> baked.json
                                      --> transport.static.ts + bakedStore.ts (artifact build only) --> the same api.ts, pages
```

- **The wall.** Nothing in `app/` imports `nba/`, and `nba/` never imports `app/`; the app meets `nba.export` only through
  the files it writes. Enforced both ways by the repo-root `tests/test_layering.py` (not under `app/`).
- **Stores** (`api/src/parlayhitters_api/`). `store_key()`/`load_store()` in `store.py` pick one: `ExportStore`
  (`export_store.py`) when `APP_EXPORT_DIR` holds a `manifest.json`, after the manifest and both sha256 are verified and the
  database is opened `read_only=True` (a failure is `ExportRefused`, answered 503, never a fallback); otherwise `Store`, the
  committed fixtures (`app/fixtures/`, season <= 2024). A new manifest is picked up without a restart. The live routes read
  capture bodies from disk through `LiveHub` (`live.py`), not the store.
- **Route groups** (`main.py` `register()`, `live_routes.py`). Health/meta; slates and games (`/api/slates`, `/api/games`,
  scenarios, result); props (`/api/props`, `/api/card`, `/api/breakdown`, `/api/knows`, `/api/edges`); swipe and results;
  track record (`/api/track-record/*`, `/api/evidence`); market (`/api/parlay-of-the-day`, `/api/contracts/top`,
  `/api/top-parlays/{date}`); desk (`/api/desk`); live (`/api/live/*`, including the SSE stream).
- **Web.** `router.ts` parses the hash into a `Route`; `App.tsx` picks the page; `Shell.tsx` is the nav, search, banner
  and footer (responsible-gambling resources); `api.ts` is the one client; `transport.ts` is `fetch`, swapped by a Vite alias
  for `transport.static.ts` in the single-file artifact build, so pages and `api.ts` are identical in both.
- **Lenses.** Learn (default), Explore, Market, a per-device preference (`prefs.ts`, `Lens.tsx`). Signals, Hedge Finder and
  Model History are Market-only (`MARKET_ONLY` in `Shell.tsx`; `MarketOnly` placeholder in `App.tsx`).
- **Privacy.** `/api/desk` returns the private desk only when `PH_PRIVATE=1` and the request is direct and local
  (`_is_local_request`, no proxy headers); else the shared cut, which is refused if it carries money fields (`desk.py`). The
  artifact bake never takes the private file unless `--money` is passed.
- **Display locks.** Calibrated chance only, else the raw number labelled "model only" (`forecast.shown_probs`); the swipe
  card face is blind; text is number-guarded (`dissect/numguard.py`) and phrase-filtered (`top_parlays.py` banned words);
  CORS allows localhost origins only; GET only.
- **Tests.** API: `app/api/tests/` (pytest; `client` = fixtures, `export_client` = `fixtures/export_small`); web:
  `app/web/src/*.test.ts` (vitest); layering: `tests/test_layering.py` at the repo root. All of it: `app/scripts/check.sh`.
