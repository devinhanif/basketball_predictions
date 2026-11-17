# parlayhitters app

## Quickstart

```
app/scripts/dev.sh                     # from the repo root: API on fixtures + web dev server, then open the URL it prints
```

It installs what is missing (`uv sync`, `npm ci`), starts the read-only API on 127.0.0.1:8000 and the web server on
5173, waits for `/api/health`, and stops both on Ctrl-C. Variations: `--export` (the real nightly export, read-only),
`--lan` (a phone on the same Wi-Fi), `--private` (the private desk), `API_PORT=8030 WEB_PORT=5195 app/scripts/dev.sh`
when a port is taken. Everything else is below. The three scripts: `scripts/dev.sh` (run), `scripts/check.sh` (every
check, with a table), `scripts/artifact.sh` (the single-file page).

Design: `docs/APP_ARCHITECTURE.md` (the plan; not all of it is built), `docs/APP_CONTENT_SPEC.md`,
`docs/APP_SECURITY_BASELINE.md`. One screen of how the code fits together: `app/ARCHITECTURE.md`.

## What the app is, and is not

The parlayhitters.ai PWA, run locally. Nothing is deployed or public; no accounts, credentials or orders.

- It is **read-only**: the API answers GET only; the export is opened `read_only=True`. It never writes to the repo's data.
- It **never computes expected value**. Chances, ranges, prices and edges are stored fields copied from the engine's
  export; the app shows them with their n or interval. A missing number is shown as missing.
- "No positive EV found" / "keep your money" is a normal screen, not an error.
- The **private desk** (`/api/desk`, the Jarvis Desk) answers the private file only when the API runs with
  `PH_PRIVATE=1` and the request reaches the machine directly (not through a tunnel or proxy). Otherwise it serves the
  shared cut, which the API refuses if it carries any money field. `scripts/dev.sh` never sets `PH_PRIVATE` unless `--private` is passed.
- **Display locks** (what the screens may show): only the calibrated chance is displayed where the export has one, else
  the raw model number labelled "model only" (`forecast.shown_probs`, `tests/test_calibrated_only.py`); the swipe card face
  is blind (no model number before the pick); written text passes the number guard; a stored contract is never called a
  lock, a guarantee or a best bet; no season prop calibration is served.
- Responsible-gambling resources are in every page footer.

## Fixture mode and real export

| | Fixture mode (default) | Export mode |
|---|---|---|
| Switch | `APP_EXPORT_DIR` unset (or a directory with no `manifest.json`: a warning is logged) | `APP_EXPORT_DIR=<dir with manifest.json>`, or `scripts/dev.sh --export [DIR]` |
| Data | two real 2024-25 nights (2024-11-12, 2024-11-20) from stored out-of-fold data, season <= 2024 | the nightly `nba.export` files: `export.duckdb`, `parlay_of_day.json`, `manifest.json` |
| `/api/health` | `{"status":"ok","mode":"fixture_replay"}` | `{"status":"ok","mode":"export"}` |
| A bad export | n/a | the manifest and both sha256 are checked first; a failure answers 503 on every route (never a silent fall back to fixtures) |

The real export on this Mac is `data/export/latest` under the main checkout; `--export` with no directory uses
`$APP_EXPORT_DIR`, else `<repo>/data/export/latest`, and refuses with a plain message if there is no `manifest.json`.
See "Reading the nightly export" below for the exact rules.

## Ports and environment variables

| Variable | Read by | Default | Meaning |
|---|---|---|---|
| `API_PORT` | `scripts/dev.sh` | 8000 | API port (127.0.0.1 only) |
| `WEB_PORT` | `scripts/dev.sh` | 5173 | web dev server port (Vite `--port`, strict) |
| `API_ORIGIN` | `web/vite.config.ts` (dev and preview proxy) | `http://127.0.0.1:8000` | where `/api` is proxied; set by `dev.sh` from `API_PORT` |
| `APP_EXPORT_DIR` | API (`store.py`), `bake_artifact`, scripts | unset = fixtures | directory holding the export's `manifest.json` |
| `APP_FIXTURES_DIR` | API (`store.py`) | `app/fixtures` | fixture directory |
| `PH_PRIVATE` | API (`main.py`) | unset | `1` lets `/api/desk` answer the private file to a direct local request |
| `APP_DESK_FILE` | API (`desk.py`) | `<repo>/data/export_private/desk.json` | private desk file |
| `APP_DESK_SHARED_FILE` | API (`desk.py`) | `<repo>/data/export_private/desk_shared.json` | shared desk cut |
| `APP_DESK_DIR` | `bake_artifact` | `<repo>/data/export_private` | where the bake looks for `desk_shared.json` |
| `APP_TOP_PARLAYS_DIR` | API (`top_parlays.py`) | `app/fixtures/top_parlays` | top-parlays files; set but not a directory = 503 |
| `APP_LIVE_DIR` | API (`live.py`) | `<repo>/data/raw` | live capture bodies read from disk |
| `APP_LIVE_POLL_S` | API (`live.py`) | 0.2 | live poll interval, seconds |
| `APP_CHECKPOINT_SNAPSHOT` | API (`live.py`) | `<repo>/data/jarvis/ingame_checkpoints_latest.json` | in-game checkpoint snapshot |
| `ANTHROPIC_API_KEY` | `dissect/providers.py` | unset | only enables a stub provider; no call is ever made (`NotWired`) |
| `PARLAYHITTERS_MODEL_CHEAP`, `_STRONG` | `dissect/providers.py` | unset | model names for the stub provider's tiers |
| `NBA_PYTHON` | one API test | unset | interpreter that rebuilds `fixtures/export_small`; the test skips without it |
| `VITE_ARTIFACT` | `web/vite.config.artifact.ts` (`env.ts`) | unset | `1` only in the artifact build: swaps `transport.ts` for `transport.static.ts` |
| `VITE_PH_BETTING` | web build (`web/src/features.ts` only) | unset = off | `1` builds the betting tools module in (see "The betting switch") |
| `PH_BETTING` | API (`features.py` only), read at `create_app` | unset = off | `1` adds the betting module's GET routes under `/api/betting/` |
| `ONLY`, `W` | `web/scripts/shots.mjs` | unset | screenshot filters |

Ports in use: 8000 API, 5173 dev server, 4173 `npm run preview`. All are bound to 127.0.0.1 except the `:lan` scripts.

## Checks (run unpiped)

```
app/scripts/check.sh              # everything, in order, with a pass/fail table and seconds per step
app/scripts/check.sh --fast       # pytest without test_live, test_links, test_last10_links (the slow files)
app/scripts/check.sh --bake       # bake the artifact data from the fixtures first
app/scripts/check.sh --browser    # also the browser route walk: scripts/walk.sh starts its own servers, needs Chrome (about 2.5 min)
app/scripts/walk.sh --widths      # the walk alone, plus 360, 768 and 1180 px
app/scripts/check.sh --betting    # every step with the betting switch ON (see "The betting switch")
```

Or by hand:

```
cd app/api
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run ruff check ../fixtures/build_fixtures.py --config ../fixtures/ruff.toml

cd ../web
npm run check          # eslint, tsc, vitest, build
```

The layering rule (nothing in `nba/` imports `app/`; `app/` meets `nba` only through `nba.export`) is checked by the repo's
`tests/test_layering.py`, at the root, not under `app/`.

## The artifact (one self-contained page)

```
app/scripts/artifact.sh                  # bake from the fixtures, build, print the path and size
app/scripts/artifact.sh --export [DIR]   # bake from the nightly export (read-only)
```

Output: `app/web/dist-artifact/parlayhitters.html` (plus `preview.html` for local viewing). By hand: `cd app/api &&
APP_EXPORT_DIR=<export dir> PYTHONPATH=src uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json`,
then `cd ../web && npm run build:artifact`; the build needs `artifact-data/baked.json` and says so if it is missing. With
`APP_EXPORT_DIR` unset the bake reads the fixtures (the two 2024-25 nights). Details are in "The artifact" below.

## The betting switch (default off)

One switch turns the betting tools module on or off: `VITE_PH_BETTING=1` at web build time (read only by
`web/src/features.ts`, folded to a constant so a build without it drops the module's code entirely) and `PH_BETTING=1` for
the API (read only by `api/src/parlayhitters_api/features.py` when the app is created). Unset, or any value but `1`, is
off, everywhere: dev, checks, the regular build and the single-file artifact. With it off the app is the app without the
module: `#/bet...` is the not-found screen, `/api/betting/*` is the ordinary typed 404, and
`web/scripts/flag-parity.mjs` (a step of `check.sh`) proves on real builds that no string in
`web/src/betting/markers.json` reaches either bundle. Turn it on for local work with `app/scripts/betting-on.sh` (same
options as `dev.sh`; it runs `dev.sh --betting`) and back off with `app/scripts/betting-off.sh` or plain `dev.sh`;
`app/scripts/check.sh --betting` and `app/scripts/artifact.sh --betting` run the checks or build the page with it on. The
module lives in `web/src/betting/` (its contract is `betting/types.ts`; it reaches the app only through
`features.ts`-guarded code in `router.ts`, `App.tsx`, `Shell.tsx`, `shellData.ts` and `BetSlots.tsx`) and
`api/src/parlayhitters_api/betting/`. Money-safety rules, on or off: analysis only, not advice; no orders are placed,
routed or cancelled anywhere; no credentials, keys or account access of any kind; the API stays GET-only; all betting
state lives in this browser. `web/src/bettingSafety.test.ts` greps both module directories for credentials, order words,
off-origin requests and write routes, and the repo's `tests/test_layering.py` keeps the module inside `app/`.

## Betting tools (behind one switch)

**The switch.** One switch, default **off** everywhere (dev, checks, the regular build, the single-file artifact, CI):
`VITE_PH_BETTING=1` at web build time (read only by `web/src/features.ts`) and `PH_BETTING=1` for the API (read only by
`api/src/parlayhitters_api/features.py`). Off, the betting screens, the nav entries, the "Add to slip" buttons and the
`/api/betting/*` routes do not exist, and the module is dropped from the bundle (see "The betting switch" above).

**Run it.** From the repo root:

```
app/scripts/betting-on.sh                    # API + web dev server with the switch ON, on the fixtures (dev.sh --betting)
app/scripts/betting-on.sh --export [DIR]     # ON, reading the nightly export read-only (refuses a DIR without manifest.json)
app/scripts/betting-off.sh                   # the same servers with the switch OFF (an inherited value is removed)
API_PORT=8140 WEB_PORT=5280 app/scripts/betting-on.sh    # other ports
```

The screens are in the **Market lens** (`#/bet` hub, `#/bet/slip`, `#/bet/compare`, `#/bet/hedge`); in Learn and Explore
every `#/bet...` address shows the "is a Market screen" note.

**Where it lives.** Web: `web/src/betting/` (entry `index.ts` with `ROUTES` and `NAV`; seam types `seam.ts`; pure logic
`slipModel.ts`, `hedgeMath.ts`, `catalog.ts`, `repository.ts`; screens `Home.tsx`, `Slip.tsx`, `Compare.tsx`,
`Hedge.tsx`; the app reaches it only through `BETTING`-guarded code and `web/src/BetSlots.tsx`). API:
`api/src/parlayhitters_api/betting/` (GET routes registered by `register(app)` in `routes.py`).

**Money-safety rules** (flag on or off):

- Analysis only, not advice: every screen with a price, cost or hedge shows "Analysis only, not advice: no orders are
  placed." and, where it applies, that the numbers are arithmetic on the prices you entered.
- No orders: nothing places, routes, cancels or executes a bet or touches a sportsbook or exchange account; "Mark slip
  as placed" only writes a local record of a bet you already placed elsewhere.
- No credentials: no keys, tokens, passwords or account access for any provider, anywhere.
- No scraping: quotes come only from what you type or from the nightly export's stored snapshots, through this app's
  own read-only API.
- Browser-only state: the slip, typed prices and recorded positions stay in this browser (`ph.betting.workspace:device`,
  versioned), with JSON export and import; an invalid import changes nothing. The API has no write route.

**The quote board** (`/api/betting/providers`, `/api/betting/board/{game}/{player}/{stat}`,
`/api/betting/slate/{date}`; contract `docs/contracts/betting_board_v1.md`) serves, per line, provider and side, only the
newest stored snapshot provably from before tip-off, with its snapshot time; fees, settlement rules, liquidity and live
status are reported as unknown. It never serves a model number (only links to the existing props and record routes,
whose locks keep applying), never a price for a flagged quote, never season 2025 (the holdout), never PrizePicks, and
never anything it fetched itself. The fixture replay stores no quote snapshots, so on fixtures every board is empty and
Compare offers typed prices.

**Check it.** From the repo root (Chrome needed for the last three):

```
app/scripts/check.sh --betting               # lint, types, every test, builds and flag parity with the switch ON
cd app/web && node scripts/flag-parity.mjs   # alone: OFF builds carry no betting marker, ON builds carry all
API_PORT=8140 WEB_PORT=5280 app/scripts/betting-on.sh       # then, in a second terminal:
cd app/web && node scripts/bet-journey.mjs http://127.0.0.1:5280/ /tmp/ph-journey   # add -> slip -> compare -> placed -> hedge -> reload
app/scripts/visual.sh --betting              # visual checks with the switch ON (also: app/scripts/visual.sh for OFF)
```

The visual checks (`web/scripts/visual-check.mjs`, run by `app/scripts/visual.sh`, which starts its own fixture servers on
8142/5282, override with `VISUAL_API_PORT`/`VISUAL_WEB_PORT`) photograph every screen at 375, 768 and 1280 px, dark and
light, Simple and Analyst, in each page's lens, with a pinned clock, no motion and initials for avatars. On every capture
they assert, and fail on: no sideways page scroll, one `h1`, 44 px touch targets at 375 px, no text under 12 px, AA
contrast, no clipped text, no overlapping controls, a visible focus ring on the first Tab stop, the money-safety note on
priced betting screens, and no price words in the Learn lens. Pixels are compared with a local baseline only when one
exists, and gate only with `--strict`:

```
app/scripts/visual.sh --update               # take the screenshots and make them the baseline (app/web/.visual/off/baseline/)
app/scripts/visual.sh                        # later: structural checks + pixel delta per capture against that baseline
app/scripts/visual.sh --strict               # the same, failing a capture above 0.5% changed pixels (--threshold)
app/scripts/visual.sh --quick                # a representative third of the matrix
app/scripts/check.sh --visual                # the structural layer as a check.sh step (with --betting: switch ON)
```

Baselines and screenshots live in `app/web/.visual/` (git-ignored; PNGs are never committed), so each machine makes its
own baseline: fonts and rendering differ between machines.

**Port more of the kit.** `docs/jarvis-donor-map.md` maps every Jarvis kit file to its place here (or why it was not
ported) and lists the rules: Preact with no new dependency, behind the switch, CSS scoped under `.ph-bet`, pure logic
first with the donor's tests, the money-safety grep, and a marker per screen for flag parity. The filled gap matrix and
acceptance checklist are `docs/jarvis-gap-matrix.md` and `docs/jarvis-acceptance-checklist.md`.

## Installable build and a phone on the same Wi-Fi

Installable build with the service worker (production only), port 4173:

```
cd app/web && npm run build && npm run preview
```

Open http://localhost:4173, then Install / Add to Home Screen. Offline it serves the shell and the last data fetched, with an "offline" banner.

For a phone: `app/scripts/dev.sh --lan` (the API stays on 127.0.0.1; the phone reaches it only through the web server's
proxy). It prints the Mac's address; by hand it is `npm run dev:lan` (or `npm run build && npm run preview:lan`) and
`ipconfig getifaddr en0`. Both devices must be on the same network and the router must not isolate clients; macOS may ask
to allow incoming connections for node. A service worker and "Add to Home Screen" need HTTPS or localhost, so over plain
HTTP the phone gets the app but not install or offline support. Stop the server when done.

## Checklists

The 2026-11-17 hardening work (a shared error-code registry, one error screen, an explicit not-found route, a time helper)
is on other branches. Steps marked "after the 2026-11-17 hardening merge" depend on it; do not follow them before then.

### Add a page

1. `web/src/router.ts`: add a `{ name: "x" }` variant to `Route`, a case in `parse()` (and `plainName()` if it is a
   top-level view), and a `TopName` plus an `ARTIFACT_NAME` entry if it is in the nav.
2. `web/src/pages/X.tsx`: the page. Load data through `useLoad`/`api` and show an honest empty state.
3. `web/src/App.tsx`: a `case "x"` in the route switch.
4. `web/src/Shell.tsx`: `ROUTE_TOP`, `TITLES`, and `ALL_WORKSPACE` (nav) or `TABS` (bottom bar); add the name to
   `MARKET_ONLY` if its subject is prices and edges, and decide whether the artifact keeps it (the `WORKSPACE` filter).
5. A test in `web/src/routes.test.ts` for `parse` and the href; then `app/scripts/check.sh`.
6. After the 2026-11-17 hardening merge: unknown paths go to the explicit not-found route, so a new path needs no extra
   handling there; add its title to whatever table the merge leaves for titles.

### Add an API route

1. `api/src/parlayhitters_api/models.py`: a pydantic response model (a `response_model` keeps the shape checked).
2. `api/src/parlayhitters_api/main.py`: a GET handler inside `register(app)` taking `store: StoreDep`; read from the
   store, never compute a price or EV. (`live_routes.py` holds the live routes the same way.)
3. A failure is a refusal with a registered code (see "Add an error code"). Until the hardening merge a refusal is
   `HTTPException(status, detail)`; after it, every refusal is `{ok:false, code, text, field, request_id, detail}`.
4. `web/src/api.ts`: a function in the `api` object (`getJson<T>` with `encodeURIComponent` on path parts) and its type
   in `types.ts`. If the page must work in the artifact, add the path to `bake_artifact.py` and `bakedStore.ts` (`shardOf`).
5. A test in `api/tests/` (the `client` fixture serves the fixtures, `export_client` the small export), then check.sh.

### Add an error code

1. `app/error_codes.json` first: `code`, `status` (null for web-only), `side` (`api` or `web`), `title`, `what`, `why`,
   `todo`, in plain words. The registry is the single source for both sides.
2. API side, after the 2026-11-17 hardening merge: raise or return the new code from the handler; the merge's tests check
   that every code used is in the registry. Before the merge there is no code field on a refusal.
3. Web side, after the merge: the one error screen looks the code up in the registry; nothing else to add.
4. Run `app/scripts/check.sh`.

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

`/api/health`, `/api/meta`, `/api/slates`, `/api/slates/{date}`, `/api/games/{id}`, `/api/games/{id}/result`, `/api/games/{id}/scenarios`,
`/api/props/{game_id}/{player_id}/{stat}?line=`, `/api/breakdown/{game_id}/{player_id}/{stat}?line=&side=`, `/api/slates/{date}/swipe?seed=&n=`, `/api/slates/{date}/results`,
`/api/card/{game_id}/{player_id}/{stat}` (swipe-card stats and the full distribution grid, fetched only when the user opens it), `/api/parlay-of-the-day`, `/api/evidence`,
`/api/knows/{game_id}[/{player_id}/{stat}]`, `/api/track-record/{calibration,guess}` (and `guess/{game_id}/{player_id}/{stat}`), `/api/edges/{game_id}/{player_id}/{stat}`, `/api/contracts/top`, `/api/top-parlays/{date}`, `/api/desk`, and the live routes below. Interactive docs at `/api/docs`. P(over) at a line is read from the stored
quantiles by interpolation with a continuity correction for integer counts: an approximation, labelled as one.

## Reading the nightly export (`APP_EXPORT_DIR`)

```
APP_EXPORT_DIR=/path/to/export uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

With the variable unset, or pointing at a directory with no `manifest.json`, the API serves the fixtures (a warning is
logged for the second case). With an export present, `export_store.py` first checks the manifest: valid JSON, schema 1,
a non-empty `data_version`, a parseable `made_at`, no season 2025, and the sha256 of both data files, all before
`export.duckdb` is opened (`read_only=True`, read once, closed). Anything wrong raises `ExportRefused` and every route
answers 503 with the reason; nothing is silently replaced by fixtures. A new manifest on disk is picked up without a
restart. "Data as of" in the banner is the manifest's `made_at`.

Schema 1 and 2 both load (anything else is refused). **Schema 2** adds `prop_lines`, `prop_grids.team_id` and
`box_scores.stl/blk/tov`:

- A prop's line is the posted main line: among usable, non-alternate quotes of the forecast's own run, the line the most
  books carry (ties: nearest the median, then lower). The price beside it is one book's over and under (sportsbooks in a
  fixed order, then exchanges alphabetically); its market probability is that pair de-vigged. A prop with no usable main
  line shows a "reference line" (middle of our forecast) and no price. Lines exist only for pts/reb/ast/fg3m.
- The leg screen lists every book's quote at the line on screen (Kalshi, Polymarket, Novig included), and the ladder
  carries alternate-line prices. A quote the exporter flagged `unusable` (or whose number is not an American price) has
  no price in any response: the screen says "not usable".
- The Swipe deck is built from the posted main lines (cards before tip only); settled rows exist for played games.
- A forecast's team comes from `prop_grids.team_id`; blocks, steals and stocks come from the box (a game whose box has
  them NULL is left out of those stats, not counted as zero).

**Schema 1** has none of that: a prop's line is the middle of the model's own forecast ("reference line", never a market
line); no swipe deck, no settled-pick rows, no stocks/blk/stl legs; players are placed from their earlier box scores or
the injury report, and those who cannot be placed are counted in the `/api/meta` stubs. In both schemas "opponent allows
to his position" is absent (no positions are exported) and home/away splits count only games the export schedules. Alt
lines read the exact stored full-support table (`p_ge_full`) when it is complete, else interpolate the quantile grid.

Test export: `fixtures/export_small/` (schema 2) was built by the real `python -m nba.export build --odds-db` from
synthetic rows dated Oct-Nov 2024 (invented player ids and prices, real team ids), including a planted Polymarket Under
placeholder. The tests derive a schema 1 export from it by dropping the schema 2 tables and columns. Rebuild from the repo root with the project interpreter:
`.venv/bin/python app/fixtures/build_export_fixture.py`. `NBA_PYTHON=.venv/bin/python uv run pytest` also runs the test
that rebuilds it and compares.

## Written dissection of a leg (scaffold; no model is called)

`api/src/parlayhitters_api/dissect/`: `packet.py` builds the fact packet for a leg (last 10 and 5 against the line,
minutes, splits, teammates out, opponent allowed, model chance and reason, small-n and disagreement flags);
`template.py` writes two to four plain sentences from it; `numguard.py` rejects any text with a number the packet does not
hold (no exemption for small numbers; recommendation and hype phrases too); `providers.py` has `TemplateProvider`
(default) and a stub `ClaudeProvider`, disabled unless `ANTHROPIC_API_KEY` is in the environment (never read from a file)
and, even then, it makes no call: `write` raises `NotWired` until Devin turns it on. Routing by difficulty: one leg goes
to the cheap tier, a whole ticket to the strong tier; the model names come from `PARLAYHITTERS_MODEL_CHEAP` and
`PARLAYHITTERS_MODEL_STRONG`. A provider that cannot run or fails the guard is replaced by the template, with the reason
in `dissection.rejected`; if even the template failed, the text would be empty, not wrong. The text is attached to
`/api/breakdown/...` as `dissection` and shown on the leg screen as "In plain words".

## Leg breakdown (`#/leg/<game>/<player>/<stat>`, one tap from every prop on the game page)

Last 10 and last 5 against the line (hit/miss chips, number, minutes), home/away, rest and back-to-back, this opponent,
rotation teammates out on the pre-tip report with how his numbers moved when they sat, what the opponent allows to his
position, alt-line ladder, and the model's chance. Stats: pts, reb, ast, fg3m, blk, stl, pra, pr, pa, ra, stocks. All of
it is counted in `api/src/parlayhitters_api/breakdown.py` from `fixtures/breakdown.json` (raw box-score rows, 2023-24 to
2024-11-23), strictly before the slate date. The model chance exists only for pts/reb/ast/fg3m; blocks, steals and combos
say "No model forecast for this stat yet" (a combo is never built from single-stat forecasts). Prices only where one is
stored (the DraftKings main line; steals has a stored main line, no stat has stored alt lines). Rebuild only this fixture:
`uv run --script app/fixtures/build_fixtures.py --only breakdown --data-root <checkout with nba.duckdb and data/>`.

## Role steadiness, on/off and without-teammate numbers (descriptive)

`api/src/parlayhitters_api/role_stats.py` (math), `role.py` (game page), `breakdown.py` (leg screen); web:
`RoleCards.tsx`, `RoleSection.tsx`. As-of like everything else: games strictly before the slate date.

- **Role steadiness** badge: Steady / Mixed / Limited data, always with n. From the sample standard deviation of his
  minutes over his last 15 games played, as a share of their average (Steady below 20%, Mixed otherwise; fewer than
  10 games is Limited data). The 20% cut is a display choice, not a tested rule. It is worded as a fact ("his minutes have
  varied by about X either side of an average of Y over his last N games"), never as model accuracy or "easier to bet"
  (docs/research/player_reliability_2026-10-14.md: accuracy tiers do not persist, minutes steadiness does; DECISIONS
  2026-10-14 16:06).
- **On/off**: team points scored minus allowed per 100 possessions with him on and off the floor, in games he played, this
  season and since last season, with possession counts (both ends), games, an approximate game-clustered 95% range on the
  gap, and a small-sample flag (under 10 games or under 1,000 possessions on one side). From `fixtures/onoff.json`
  (possession totals per game and per slate player from the `possessions` table; assists never come from it).
  The exporter has no possessions yet, so an export-fed API says so ("no play-by-play on/off") and keeps the badge.
- **Without a teammate who is out tonight** (injury report at least an hour before tip): his minutes and per-36 points,
  rebounds and assists (assists from the box score) in games with and without that teammate (same game sets as the
  existing with/without tally), with n.

Rebuild only the on/off fixture: `uv run --script app/fixtures/build_fixtures.py --only onoff --data-root <checkout with nba.duckdb>`
(asserts possession points equal each final score; season <= 2024).

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

## Which model a number came from (best_model_schema 1)

When the export's meta carries `best_model_schema`, each prop shows the row the exporter marked `is_primary`
(AVG when a fresh pre-tip AVG forecast exists; production v2 when AVG is missing or stale, and in the postseason)
and says so in words (`api/src/parlayhitters_api/model_source.py`). A prop with no primary row is not shown. Chance to
play is `p_play_primary` with its source (availability model R, production, or out on the official injury report).
An older export keeps the production-only behaviour and says so. Fixtures: `fixtures/export_small` (best-model rows
added by `fixtures/add_best_model_rows.py`) and `fixtures/export_best_real` (written by the real exporter via
`fixtures/build_export_best_real.py --repo <checkout with nba/export/best_model.py>`).

## Fonts and the line slider

Fonts are self-hosted woff2 under `app/web/src/fonts/` (Manrope for display, DM Sans for text;
both SIL OFL 1.1, licences beside the files). No font service is contacted. Numbers use tabular figures everywhere.

The swipe card's "Stats and line slider" reads `/api/card/...`: P(Y >= k) from the stored full-support table (P(over k.5) =
P(Y >= k+1)), `rung_cal` lines when the export carries them (docs/contracts/rung_cal_export_v1.md; only lines with raw P(over) >= 0.5
are listed), else the raw model labelled "model only". Opening it records the pick as `saw_model: true`. The fixture prop with a
synthetic `rung_cal` is planted by `uv run --script app/fixtures/add_rung_cal_example.py`.

Two numbers sit under the slider's big figure, always labelled. "Model, raw" is the forecast table's own chance of over at the
slider's line. "Model, calibrated" is that chance passed through the frozen calibration map (rung_cal_map_v1, DraftKings only),
which corrects for how past chances like this turned out; it exists only at a line the export's `rung_cal.lines` lists (those
are lines where the raw chance is 50% or more), and the app never interpolates it. Where it is missing, the raw number stands
alone with a one-line reason (no two-sided DraftKings line before tip; line outside the tested range; or not exported). The
export carries reason counts per slate in the manifest, not per prop, so the reason shown is inferred only from fields the row
holds. The swipe card face stays blind: both numbers appear only after "Stats and line slider" is opened.

Line-calibrated number (`line_cal`, docs/contracts/line_cal_export_v1.md). When the export row carries `prop_grids.line_cal`, then at the
DraftKings main line (the only line it is valid at) "Model, calibrated at the book's line" is its `p_over_cal` (LINE_CALIBRATION, T493),
with `p_over_raw` as the raw number and the under side as 1 - `p_over_cal`; away from that line the `rung_cal` rules above are unchanged.
When it is NULL, the raw number stands alone with a plain sentence for `line_cal_null_reason` (`no_book_line` says no DraftKings line was
posted before tip; the internal codes say the number could not be reproduced for this row), and at that main line the rung number is not
substituted. A missing column reads as NULL, so older exports render as before. The API passes both columns through (`clean_line_cal`
validates, a violation drops the whole value). The details footer names both maps. The synthetic fixture rows (Jaylen Brown, game
0022400001: pts with line_cal, ast `no_book_line`, reb `grid_mismatch`) are planted by `uv run --script app/fixtures/add_line_cal_example.py`
after the rung one; the calibrated value there is synthetic.

## Super app shell (P1, docs/DESIGN_SUPER_APP.md)

Tokens, fonts, base and the shell are in `web/src/theme.css` (dark first, full light theme, purple accent, orange only for
"tonight"); new surfaces in `web/src/ui.css`. Sidebar from 1024 px, top bar and bottom tabs below. Today opens with the
night's answer from stored counts (`headline.ts`). Simple (plain cards) and Analyst (line ladder, splits, calibration, and
"Edge after fees": model % [range] vs ask + fee = edge, copied from the engine's stored rows via `/api/edges/...`) and
Settings (theme, mode, opens-on) are per-device preferences in localStorage (`prefs.ts`).
`node scripts/shots.mjs` (see its header) screenshots every screen at 390 and 1440, dark and light, and audits text size
(>= 12 px), contrast (>= 4.5:1; 3:1 for large text) and sideways overflow.

## The artifact: how the page is built

Commands are in "The artifact" near the top. The bake is the API's own code over the export, read-only, newest 3 nights.

`web/vite.config.artifact.ts` swaps `transport.ts` (fetch) for `transport.static.ts`, so the page asks nothing of any
server: answers come from the baked data, gzip shards opened on first use. Page content only (no doctype/html/head/body),
fonts as data: URIs, hash routes `#today #swipe #picks #prefs`, detail pages are in-memory state. The build fails if the
page holds fetch, a service worker, an http(s) URL, alert/confirm/prompt, print or a download link. Swipe uses the
played nights only (replay clock 30 minutes before the first tip) so picks can be graded. Shared paper picks
(`sharedPicks.ts`): `picks/<user id>` documents through the platform db, ids only, names resolved at render, other
viewers' data validated; without db it is the same local ledger. Publish with capabilities
`{"db":{"rules":[{"path":"picks","read":"view","write":"owner"},{"path":"picks/{self}","write":"interact"},{"path":"watch","read":"view","write":"owner"},{"path":"watch/{self}","write":"interact"},{"path":"photos","read":"view","write":"admin"}]},"user":{"scopes":["profile"]},"assets":{},"downloads":true}`.
(`data/users/<id>/state`, the private read-state document, needs no rule: the default keeps each viewer's own subtree private.)
The line slider is off in the snapshot (only the posted line is baked).

## Jarvis Desk screens (docs/contracts/desk_export_v1.md)

Discover, Signals, Hedge Finder (the position lab), Model History, Alerts, Admin studio, Preferences, a first-run welcome,
search (Ctrl/Cmd+K), the bell, the profile menu and the About modal all read the desk export through `/api/desk`
(`api/src/parlayhitters_api/desk.py`), which answers only when the API runs with `PH_PRIVATE=1` (bind 127.0.0.1 or the LAN):

```
PH_PRIVATE=1 APP_DESK_FILE=data/export_private/desk.json uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Without a desk file every desk screen says so plainly. The artifact bake takes `desk_shared.json` (directory from
`APP_DESK_DIR`, default `<repo>/data/export_private`), else the synthetic fixture in `fixtures/desk_fixture_shared.json`
(built by `fixtures/build_desk_fixture.py`, labelled as a fixture on every screen); `--money` bakes the private `desk.json`
and is not for a shared page. The API refuses a shared cut that carries any money field.
`/api/contracts/top` (best stored contracts by edge after fees) feeds Discover's closest look and the position lab.
Identity: the platform user in the shared page, a locally typed name in the regular build; never a password. Admin studio:
`user.isOwner()` or `canEdit()` in the shared page (photos through the `assets` capability into `photos/<player id>`),
the private build locally.

## Live play-by-play (Devin's override, DECISIONS 2026-11-10 03:10)

Reads the live capture's ESPN summary bodies from disk (no network, no export store). `APP_LIVE_DIR` (default
`<repo>/data/raw`), `APP_LIVE_POLL_S` (default 0.2), `APP_CHECKPOINT_SNAPSHOT` (default `<repo>/data/jarvis/ingame_checkpoints_latest.json`; contract in `docs/contracts/ingame_checkpoint_snapshot_v1.md`; the app never opens the JARVIS DuckDB store).
Routes: `GET /api/live/{date}`, `/api/live/{event_id}/stream` (SSE), `/snapshot`, `/plays?before=`, `/checkpoint`.
Latency replay (own port, fixtures): `cd app/api && PYTHONPATH=src uv run python tests/live_harness.py`.
Fixtures: `app/fixtures/live/` (trimmed 2026-11-09 capture bodies; `build_live_fixture.py` rebuilds them).

Rate limiting (not in code): the live routes other than the stream share the API's thread pool with the store routes, so put a
Cloudflare rate-limiting rule in front, for example: path starts with `/api/live/` and is not `/stream`, 60 requests per minute
per IP, then block for 60 s; and for `/api/live/*/stream`, 10 new connections per minute per IP. The API itself caps the stream at
150 viewers per game and 300 in all (503 beyond that) and refuses a decompressed capture body over 20 MB.
