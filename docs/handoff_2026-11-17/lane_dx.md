# Lane D (developer experience), branch agent/app-1117-dx, 2026-11-16 evening

Files: `app/scripts/{dev,check,artifact}.sh`, `app/README.md` (rewritten top, reference kept), `app/ARCHITECTURE.md` (new),
`docs/APP_ARCHITECTURE.md`, `.claude/agents/app.md`. Nothing under `app/api`, `app/web`, `nba/`, `ops/`, `configs/` was edited.
Ignored build outputs (`app/api/.venv`, `app/web/node_modules`, `artifact-data/`, `dist-artifact/`) now exist in the worktree.

## Scripts and the evidence they work

**`app/scripts/dev.sh`** (`--export [DIR]`, `--lan`, `--private`; env `API_PORT`, `WEB_PORT`). Runs `uv sync` / `npm ci` only when
`.venv` / `node_modules` is missing, starts the API (cwd `app/api`, 127.0.0.1) and `npm run dev -- --port $WEB_PORT` with
`API_ORIGIN` set, polls `/api/health` through the web port, stops both process trees on Ctrl-C/TERM, removes its temp logs.
Seen, with `API_PORT=8030 WEB_PORT=5195`:
- first run installed deps, printed `parlayhitters is up   (200 {"status":"ok","mode":"fixture_replay"})`, `open: http://localhost:5195`;
  fetched `/api/health` through 5195 (200, fixture_replay), the API directly on 8030 (200), and `/` on 5195 (200).
- A real Ctrl-C (pty, `\x03`) printed `dev.sh: stopping`; `pgrep -f "port 8030|port 5195"` printed nothing afterwards, temp log dir gone.
  SIGTERM likewise. (`kill -INT` on a background job does nothing: bash ignores SIGINT for background jobs; that is a test artifact, not the script.)
- `--export /Users/devin/Downloads/nba-prediction/data/export/latest --private --lan`: `mode":"export"`, phone URL printed, the "PH_PRIVATE=1 is SET (--private)" line printed, clean stop.
- Busy port: `dev.sh: port 8030 is already in use (another copy of the app?). Choose another, for example: API_PORT=8060 app/scripts/dev.sh`, rc=1 (and ports 8000/5173 were busy tonight, so the default also hit this message).
- `--export /nonexistent` -> `no manifest.json in '/nonexistent'...` rc=1; equal ports and unknown option refused, rc=1.
- PH_PRIVATE: the child environment is built with `env -u PH_PRIVATE` (and `-u APP_EXPORT_DIR` in fixture mode); `PH_PRIVATE=1` only appended for `--private`.
  Verified the mechanism with the same array in a one-liner: parent `PH_PRIVATE=1 APP_EXPORT_DIR=/x` -> child `None`/`None`; with the append -> `1`.
  (I could not read the live API process env: `ps -E` shows nothing in this sandbox.)

**`app/scripts/check.sh`** (`--fast`, `--bake`, `--browser`). Runs every step even after a failure, prints the table, exits 1 on any FAIL.
Full run (unpiped, `nice -n 10`), first run, includes pytest 434 passed 1 skipped (the skip is the `NBA_PYTHON` rebuild test) and vitest 319 passed:

```
step                       result seconds
api ruff                   pass   1
api ruff format            pass   0
api mypy                   pass   7
api pytest                 pass   143
fixtures ruff              pass   0
web eslint                 pass   6
web tsc                    pass   2
web vitest                 pass   1
web build                  pass   3
artifact build             pass   1
total                             164
ALL CHECKS PASSED
```

`--fast --browser` (second run): pytest ignores `tests/test_live.py`, `test_links.py`, `test_last10_links.py` (the slowest per `--durations`:
test_live 24 s + 15 s, test_links 15 + 11 + 5 s, test_last10_links 10 + 9 + 5 s); pytest 14 s, total 28 s, `browser route walk skip` with the
note that `route-walk.mjs` does not exist yet, exit 0. The artifact step ran because `artifact-data/baked.json` existed from my artifact run; it is skipped with a note when absent. Failure path: a copy of the script with the first step replaced by `false` printed `api ruff FAIL`, `SOME CHECKS FAILED`, exit=1.

**`app/scripts/artifact.sh`** (`--export [DIR]`). Bake then `npm run build:artifact`; prints path and size.
- Fixtures (`APP_EXPORT_DIR` explicitly unset): `wrote ../web/artifact-data/baked.json (3.74 MB): ... dates ['2024-11-12', '2024-11-20'], {'games': 16, 'breakdowns': 2552, 'cards': 492}`; `size: 4183794 bytes`, 27 s. So the bake does work on fixtures.
- `--export /Users/devin/Downloads/nba-prediction/data/export/latest`: dates 2026-11-14, 11-15, 11-16, 5510888 bytes, build checks passed; nothing written under the main checkout (read-only; I did not run `git status` there beyond `data/export`, which was empty).
Output: `/Users/devin/Downloads/nba-wt-1117-dx/app/web/dist-artifact/parlayhitters.html`. Never passes `--money`.

## False statements fixed (before text, file:line as of the base commit)

`.claude/agents/app.md`
- 3: "Fixture data until nba/export exists." (it exists) -> fixtures or the export files.
- 10-11: "app imports nba only via nba.export (which does not exist yet: use fixtures ...)" -> exists; the app imports nothing from nba (`KNOWN_APP_EDGES` is empty) and reads its output files.
- 12: "app/ is its own uv project (app/pyproject.toml, app/uv.lock)" (they are `app/api/pyproject.toml`, `app/api/uv.lock`).
- 21: "traces to a fixture field" incomplete in export mode -> "fixture or export field". (Permissions and tools untouched.)

`docs/APP_ARCHITECTURE.md`
- 1: "design only ... nothing is built" -> status block: what is built, what is planned.
- 4: "Status: DRAFT for Devin" -> reworded (checklist stays Devin's).
- 18: "tests (`tests/app/`)", "pyproject extras, lockfile section" -> two projects `app/api`, `app/web`, tests in `app/api/tests/` and beside web sources.
- 19-22: `nba/export/` "functions returning plain dicts" "does not exist" implied -> it exists and writes `export.duckdb`, `parlay_of_day.json`, `manifest.json`.
- 23-24: "test_layering.py gains two rules" (future) -> the rules exist (`tests/test_layering.py` lines 198-220).
- 25-26: export job "a new entrypoint added to the closure" (it is explicitly not in the closure), hands-off window 10-18..20 presented as live -> past.
- 30-33, 34-36: PWA "Cloudflare Pages", "Preact is the ceiling", page list; API "Oracle VM", "app DB (SQLite)", "Rate-limited", "CORS pinned to https://parlayhitters.ai" -> built vs planned; CORS is localhost-only, no rate limit, no app DB.
- 37-72: components 3, 4, 5, 7, 8 (slip ingestion, magic-link ledger, settlement, social adapters with `app/social/`, support) -> marked PLANNED (the ledger is browser-local today); 6 marked built.
- 71, 80, 98, 105: sections 4, 5, 6, 7 (what runs where, data flow, failure behaviour, costs) -> headed as planned deployment shape.
- 123-128: build order "Now to 10-20" -> past; status added (steps 1-2 built, 3 only local).
- 130-157: gameplan weeks 3 to 6+ -> status paragraph: swipe and local ledger built; leagues, post-mortems, chat, accounts, VM cutover not built (verified no "league"/"post-mortem" feature in `app/`; `docs/PROJECT_STATUS.md` says launchd on the Mac "until a VM is chosen").
Left as written on purpose (decisions, not descriptions): sections 1, 3.3 text details, cost rows, week table dates.

`app/README.md`: 1 and 3 ("local, fixtures only", "First milestone", "fixtures only"; the app also serves the export), the route list (missing 12+ routes; now listed), the artifact section duplicated (merged), "Rate limiting (not in code)" kept as advice.

## Decisions (what / why / what would reverse it)
- `--fast` = ignore three slow test files. Why: the only slow part is those files (about 100 of 118 s). Reverse: mark them with a pytest marker (needs an API-lane edit).
- check.sh's artifact step runs when `baked.json` exists, whatever it was baked from. Why: simplest; the table says "artifact build". Reverse: print the bake source in the step name.
- dev.sh kills process trees by `pgrep -P` recursion from the PIDs it started, not process groups. Why: works without job control; touches nothing else. Reverse: use `set -m` and group kill if a grandchild ever survives.
- Web port is passed as `npm run dev -- --port` rather than editing `vite.config.ts` (web lane owns it); strictPort stays on.
- Health is read through the web port (proves the proxy), via node `fetch` (no curl).
- README documents steps depending on the hardening work as "after the 2026-11-17 hardening merge". Reverse: delete those qualifiers once merged, and rewrite step 3 of "add an API route" and steps 2-3 of "add an error code".

## Not verified
- `--browser` with a real `route-walk.mjs` (absent; its arguments or need for a running server are unknown).
- Phone access over the LAN (only that the URL prints and `dev:lan` starts); the 503 path of `--export` with a corrupt manifest; macOS firewall prompt.
- The README checklists were written from the current code, before the other lanes merge; they will drift on the error-code steps.
- `npm ci` printed "9 moderate severity vulnerabilities" (npm audit); not investigated, not changed.
- `.claude/agents/app.md` edits are descriptive only; I did not re-run any agent.

## Proposal: one typed route table, one typed API client (for the design lane, after the web hardening merge)

Today a page's identity is spread over six places: `Route` union, `parse()`, `plainName()`, `ARTIFACT_NAME`/`topHref()` and
`SCREEN_HASH` in `router.ts`; the `switch` in `App.tsx`; and `ROUTE_TOP`, `TITLES`, `CRUMB`, `ALL_WORKSPACE`, `TABS`, `MARKET_ONLY` in
`Shell.tsx`; plus `SCREEN_ITEMS` hard-coded in `shellData.ts` (the palette) with its own labels and `topHref`. An unknown path silently
becomes Today (`parse` default and the `game`/`leg` fallbacks), which is why there is no not-found.

Smallest refactor: a new `web/src/routes.ts` exporting `const ROUTES = [...] as const satisfies readonly RouteDef[]`, where each def
is `{ name, path (hash segment, with the artifact alias), title, nav?: {label, icon, group: "main"|"manage", tab?: boolean},
marketOnly?: boolean, artifact: boolean, parse(parts, query) -> params | null, href(params) -> string, page: lazy component }`.
Derive from it: `Route` (a mapped union type), `parse()` (find by first segment; `null` from a def's parser or no match returns
`{name:"notFound", hash}` instead of Today), `topHref`, `plainName`, the nav lists and tabs in `Shell.tsx`, `ROUTE_TOP`/`TITLES`/crumbs,
`MARKET_ONLY`, the `SCREEN_ITEMS` of the command palette, and the `App.tsx` switch (`ROUTES.find(r => r.name === route.name).page`).
`routes.test.ts` then gains one property test: every def parses its own `href` round trip, every nav item has a title and an icon,
and no two defs share a path.
Files touched: `router.ts` (shrinks to hooks + scroll code), new `routes.ts`, `Shell.tsx`, `App.tsx`, `shellData.ts`, `prefs.ts`
(`SCREEN_HASH`, `MARKET_ONLY_SCREENS` read the table), `routes.test.ts`. Pages and `Route` consumers do not change if `Route` keeps its shape.
Risks: (1) the web lane is already adding an explicit not-found route and a time helper: land after it and fold its route in as a def, or the two will conflict in `router.ts`/`App.tsx`; (2) artifact hash aliases (`prefs` for settings, `discover` for today) and the artifact-only exclusions (`record`, `live`) must stay data in the table, covered by `artifact.test.ts`; (3) lazy pages change bundle splitting and the single-file build forbids extra chunks, so keep eager imports; (4) `Route` is a discriminated union used in about a dozen files, so keep names and fields identical.

One typed API client: today `api.ts` holds the `api` object plus ad hoc functions (`getTopParlays`, `getScenarios`, `getTopContracts`,
`getDesk`) that each re-implement status handling (404 as a normal state, 422 as refused) and a string-built path; `getJson` throws an
`Error` whose message is parsed back later (`errors.ts`, `httpErrorMessage`). `transport.ts` and `transport.static.ts` are already the
right seam (`getRaw` -> `Raw`).
Smallest refactor: in `api.ts` define `type Endpoint<P, T> = { path(p: P): string; parse?(body: unknown): T; tolerate?: Record<number, (raw) => T> }`
and one `call(endpoint, params): Promise<Loaded<T>>` that does the status mapping once, returns a typed `ApiError {status, code, text, field, request_id}`
(from the new refusal body; falls back to status text for an old API) instead of a string-parsed `Error`, and applies each endpoint's `tolerate` table (404 -> missing, 422 -> refused).
Keep `api.meta()`, `api.slate(date)` etc. as the public names, now built as `endpoint(...)` entries, so pages do not change. Put every path
in one place so `bake_artifact.py`'s keys and `bakedStore.ts` `shardOf` can be checked against it in a test (a test that every endpoint path
shape is baked or explicitly marked "not in the artifact"). Validate `parse` only where a type is risky (`getDesk` already does).
Files touched: `api.ts`, `errors.ts` (error type), `types.ts` (ApiError), the hooks that catch errors (`useLoad.ts`, `useSettled.ts`, `useLive*.ts`), `components.tsx` (error screen) and their tests. Risks: (1) this is exactly the area the web hardening lane is changing (one error screen, error boundary, registry), so do it strictly after that merge; (2) the static transport returns `Raw` with baked statuses, so `tolerate` behaviour must be identical there (the artifact tests cover it); (3) pages that `catch (e)` and read `e.message` need the typed error to keep a `.message`.
