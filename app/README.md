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

The wall: nothing here imports `nba/`, and `nba/` never imports `app/`. The app meets `nba.export` only through the
files it writes (`export.duckdb`, `parlay_of_day.json`, `manifest.json`); see "Reading the nightly export" below.

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
`/api/card/{game_id}/{player_id}/{stat}` (swipe-card stats and the full distribution grid, fetched only when the user opens it), `/api/parlay-of-the-day`, `/api/evidence`. Interactive docs at `/api/docs`. P(over) at a line is read from the stored
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

Fonts are self-hosted woff2 under `app/web/src/fonts/` (Barlow Condensed for headings and big numbers, IBM Plex Sans for text;
both SIL OFL 1.1, licences beside the files). No font service is contacted. Numbers use tabular figures everywhere.

The swipe card's "Stats and line slider" reads `/api/card/...`: P(Y >= k) from the stored full-support table (P(over k.5) =
P(Y >= k+1)), `rung_cal` lines when the export carries them (docs/contracts/rung_cal_export_v1.md; only lines with raw P(over) >= 0.5
are listed), else the raw model labelled "model only". Opening it records the pick as `saw_model: true`. The fixture prop with a
synthetic `rung_cal` is planted by `uv run --script app/fixtures/add_rung_cal_example.py`.
