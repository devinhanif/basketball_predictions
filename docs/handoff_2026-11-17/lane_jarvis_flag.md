# Lane J1: the betting switch (plumbing only)

Branch `agent/app-jarvis-flag`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-flag`. No betting screens, no betting
logic: one switch, one seam, one placeholder route, and the tests that prove the switch OFF changes nothing.

## How the flag is read and folded

- Web: `app/web/src/features.ts` is the only file that reads `import.meta.env.VITE_PH_BETTING`;
  `export const BETTING: boolean = betting === "1"`. Same pattern as `IS_ARTIFACT` in `env.ts`. Vite inlines the env value
  and the minifier folds the cross-module constant, so `BETTING ? ... : ...` and `if (!BETTING) return null` drop out, and
  `src/betting/` is tree-shaken whole. No `define` was needed: proved on real builds (below), in both the regular build
  and the single-file artifact (vite reads `VITE_*` from the process environment in both configs).
- One trap found by the build, not by reasoning: a module-level call (`href: betHref()`) in `betting/index.ts` survived
  tree-shaking in the OFF bundle (the minifier cannot prove the call pure), leaking `#/bet`. Fixed with a literal; the
  parity check would catch a repeat.
- API: `app/api/src/parlayhitters_api/features.py` is the only file that reads `PH_BETTING` (`betting_enabled()`).
  `create_app(*, betting: bool | None = None)` reads it once at creation; tests pass `True`/`False`.
  The betting package registers its route with `register(app)` like every other area, not `include_router`: FastAPI
  0.143 wraps an included router in `_IncludedRouter`, which would hide the route from the existing route-walking tests
  (GET-only, every route fuzzed). Found by a failing test.

Evidence (`app/web/scripts/flag-parity.mjs`, four real builds into a temp dir, never `dist/` or `dist-artifact/`):

```
flag parity: sizes in bytes
  off  js 390596  css 76308  artifact 4520449
  on   js 392943  css 76308  artifact 4522792
  markers checked: 4
flag parity passed: the OFF builds carry no betting marker; the ON builds carry every one
```

Markers (`app/web/src/betting/markers.json`): the placeholder sentence, `/api/betting`, `#/bet`, `ph-bet`. Before the
script existed the same was checked by hand with grep on scratch builds: OFF 0 hits for each, ON 1 hit for each; the
OFF css file is byte-identical to the baseline css. The ON artifact also passes `build-artifact.mjs`'s own forbidden-text
scan (no `fetch(`, no URL). The parity script is its own step in `app/scripts/check.sh` ("flag parity").

## Files

New: `app/web/src/features.ts`, `app/web/src/betting/{index.ts,types.ts,Placeholder.tsx,markers.json}`,
`app/web/src/BetSlots.tsx`, `app/web/src/bettingSafety.test.ts`, `app/web/scripts/flag-parity.mjs`,
`app/api/src/parlayhitters_api/features.py`, `app/api/src/parlayhitters_api/betting/{__init__.py,routes.py}`,
`app/api/tests/test_betting_flag.py`, `app/scripts/betting-on.sh`, `app/scripts/betting-off.sh`, this report.

Changed: `app/web/src/{router.ts,App.tsx,Shell.tsx,shellData.ts}` (guarded wiring only),
`app/web/scripts/build-artifact.mjs` (`--out <dir>`; default unchanged), `app/api/src/parlayhitters_api/main.py`
(`create_app(betting=...)`), `app/api/tests/hostile_gen.py` (`BETTING_ROUTES`, joined to `ROUTES` only in an ON run),
`app/scripts/{check.sh,artifact.sh,dev.sh}` (`--betting`), `app/README.md` (one section + two env rows),
`tests/test_layering.py` (five tests added, none changed).

## The seam (for the next lanes)

Contract: `app/web/src/betting/types.ts`. The app reaches the module only through `BETTING`-guarded code.

```ts
interface BetRoute { name: "bet"; path: readonly string[]; query: string }       // a Route variant in router.ts
interface BettingRouteDef { path: string; title: string; render: (r: BetRoute) => ComponentChildren }
interface BettingNavEntry { href: string; label: string; icon: IconName; sub: string; words?: string; marketOnly: boolean }
interface AddToSlipProps { gameId: string; playerId: number | null; stat: string | null; side: Side | null; line: number | null; from: string }
interface ShellPanelProps { onBetRoute: boolean }
interface BettingModule { routes; nav; AddToSlip: FunctionComponent<AddToSlipProps>; ShellPanel: FunctionComponent<ShellPanelProps> }
```

- Register a screen: add `{ path: "compare", title: "Compare", render: (r) => h(Compare, { route: r }) }` to `ROUTES` in
  `betting/index.ts`. `parseBet` accepts one lowercase segment and only registered paths, so `#/bet/compare` stops being
  not-found exactly when it is registered. The breadcrumb comes from `title`.
- Nav entry: add a `BettingNavEntry` to `NAV` with a literal `href` (`"#/bet/compare"`, not a call; see the trap above).
  It shows in the sidebar, the mobile menu sheet and the search palette; `marketOnly: true` lists it in the Market lens only.
- Add to slip: a page renders `<AddToSlipSlot gameId=... playerId=... stat=... side=... line=... from="leg" />` from
  `src/BetSlots.tsx`; implement `BETTING_MODULE.AddToSlip` (currently `() => null`). OFF it renders nothing and pulls
  nothing in.
- Shell panel: `Shell.tsx` already renders `<BetShellPanel onBetRoute=.../>` after `<main>`; implement
  `BETTING_MODULE.ShellPanel` (currently `() => null`).
- Every new screen: add one distinctive string of it to `betting/markers.json` so the parity check covers it. Strings
  in `markers.json` must not appear in source outside `src/betting/` (a test checks it), including comments.
- API: add a route inside `register(app)` in `parlayhitters_api/betting/routes.py` (GET only, path under
  `/api/betting/`) and its template to `BETTING_ROUTES` in `tests/hostile_gen.py`.

## Money-safety grep (`app/web/src/bettingSafety.test.ts`, both module directories)

Fails on: `authorization`, `api_key`/`api-key`/`apikey`, `secret`, `place_order`, `execute` (all case-insensitive), the
words `token(s)` and `order(s)`, any `fetch(` whose argument is not a string literal starting with a single `/`, any
`http(s)://` or `ws(s)://`, `XMLHttpRequest`/`WebSocket`/`EventSource`/`sendBeacon`, a Python network-client import, and
a non-GET FastAPI route (`@x.post/put/patch/delete/api_route/websocket(`, `add_api_route`, `methods=`).
Allow-list (exact, case-sensitive, removed before scanning): `"no orders are placed"`. Nothing else. A self-test plants
each forbidden form and checks the scanner catches it. Env-name test: within `app/web/src` only `features.ts` and within
`app/api/src` only `features.py` contain either variable name (scripts, configs, tests and README may set them; they are
outside those trees).

## Sizes, switch OFF (bytes)

| | before (b6e16d67) | after | delta |
|---|---|---|---|
| js (`dist/assets/index-*.js`) | 390523 | 390596 | +73 |
| css | 76308 | 76308 | 0 (identical file) |
| artifact `parlayhitters.html` | 4520376 | 4520449 | +73 |

The 73 bytes are the seam's residue after folding, read from the bundle: `case"bet":return t` (router),
`case"bet":a=null;break` (App), `bet:"today"` (Shell's route table). ON adds about 2.3 KB.

## Test counts

| suite | OFF | ON |
|---|---|---|
| `app/scripts/check.sh --browser` (`--betting` for ON) | all steps pass (see note) | ALL CHECKS PASSED |
| api pytest | 582 passed | 582 passed |
| web vitest | 418 passed (41 files) | 418 passed |
| flag parity | pass | pass |
| browser route walk (2 x 150 steps, 390 px) | 0 violations | 0 violations |
| `tests/test_layering.py` (repo root) | 15 passed | n/a (no switch) |

New tests: api 22 (`test_betting_flag.py`), web 10 (`bettingSafety.test.ts`), layering 5; so the existing suites
were 560 api, 408 web, 10 layering, and every one of them passes unchanged in both states. In an ON run the existing
route-walking API tests (GET-only, hostile generator, sha-mismatch 503) also cover `/api/betting/status` through
`hostile_gen.BETTING_ROUTES`. The first ON run found one real failure (the sha-mismatch contract: the status route
answered 200 with a refused export); fixed by decision 3, not by touching the test.

Note on the OFF run: in the final OFF `check.sh --browser` every step passed except the walk, whose seed-1 run hung
idle (0% CPU for 12 minutes, the API, vite and Chrome all alive, no output). I killed that walk's own Chrome and node
process; seed 2 then passed, and a standalone OFF `app/scripts/walk.sh` rerun passed both seeds with 0 violations. The
earlier OFF `check.sh --browser` run (before decision 3) also passed the walk. I could not find the cause; other agents
were running Chrome walks at the same time.

After the last full runs I changed one line in `Shell.tsx` (no nav item marked current on a betting screen). After it:
OFF `npm run check` (418) and `build:artifact` pass and the OFF bundle is byte-identical (same content hash
`index-Cae6p686.js`); ON vitest 418, flag parity and the ON artifact build pass. The walk was not rerun after that line.

End to end, switch ON (`API_PORT=8094 WEB_PORT=5294 app/scripts/betting-on.sh`): `/api/betting/status` through the web
proxy gave `200 {"enabled":true,"note":"analysis only; no orders are placed; this service is read-only"}` and `#/bet`
rendered "The API's switch: on (...)". Switch OFF with `PH_BETTING=1 VITE_PH_BETTING=1` inherited
(`app/scripts/betting-off.sh`): `/api/betting/status` was `404 not_found` and `#/bet` showed no `.ph-bet` (not-found).
Screenshots of `#/bet` (ON artifact and ON dev server) at 375 and 1280 px, light and dark: legible in both themes
(text rgb(41,38,59) on rgb(245,245,250) light; rgb(244,245,250) on rgb(16,19,29) dark), no horizontal overflow, the
footer's 21+ / 1-800-GAMBLER line present.

## Decisions (what / why / what would reverse it)

1. Fold by Vite's own env inlining, no `define`. Why: `IS_ARTIFACT` already folds across modules in both builds and the
   parity check proves it for `BETTING`. Reverse: if a toolchain upgrade stops folding, the parity check fails; then add
   `define: {"import.meta.env.VITE_PH_BETTING": ...}` to both Vite configs.
2. API route registered with `register(app)`, not `include_router`. Why: FastAPI 0.143's `_IncludedRouter` hides routes
   from `app.routes`, blinding the GET-only and fuzz-coverage tests. Reverse: a FastAPI that lists included routes as
   `APIRoute`.
3. `/api/betting/status` depends on the export store (503 `export_refused` when the export is refused). Why: the
   existing contract test requires every non-parameter store route to refuse on a sha mismatch; failing closed beat
   adding an exemption to an existing test. Reverse: Devin wants the status route to answer regardless (then exempt it
   in `test_a_sha_mismatch_is_503_on_every_store_route`).
4. `/api/meta` and `/api/health` gain nothing in either state (a test pins that). Why: keys-only additions were optional;
   the placeholder asks `/api/betting/status` instead. Reverse: a later lane needs the web to discover the API switch
   without a 404.
5. The betting nav entry is `marketOnly: true` (sidebar and menu list it in the Market lens only, like Hedge Finder);
   the route and the search palette work in every lens. Reverse: Devin wants it visible in Learn.
6. Betting routes in the artifact stay in memory (no plain `#name` hash), like detail pages. Reverse: a decision to give
   the betting screens artifact names in `router.ts`.
7. `hostile_gen.ROUTES` gains `BETTING_ROUTES` only in an ON run, so OFF runs are byte-for-byte the old generator and
   ON runs fuzz the new route through every existing route-walking test. `test_betting_flag.py` fuzzes the betting
   paths in both states explicitly.
8. `dev.sh`, `check.sh`, `artifact.sh` remove inherited `VITE_PH_BETTING`/`PH_BETTING` unless `--betting` (as dev.sh
   does for `PH_PRIVATE`), so a stray shell export never turns a default run ON. `walk.sh` is unchanged (inherits from
   check.sh).
9. The env-name test covers `app/web/src` and `app/api/src` only; scripts, Vite/Node tooling, tests and the README set
   or document the variables and are outside it. The marker test also forbids marker strings in comments outside
   `src/betting/` (cheap to satisfy, and keeps the OFF grep meaningful).
10. On a betting screen Shell's `here` is null, so no top-level item is marked current (the screenshot showed Discover
   marked current before). Reverse: a betting nav design that wants a parent highlighted.
11. The grep test is stricter than asked: `orders`/`tokens` plurals too, plus URLs, other network channels and non-GET
   FastAPI routes; the only allowed phrase is `no orders are placed`.

## Commits (branch `agent/app-jarvis-flag`, not pushed)

`ddda43ac` api switch and package, `0570c6eb` web switch, seam, parity, grep, `9f509323` scripts and README,
`93d0fcc1` layering, `eafb496a` betting route fails closed on a refused export, `f94a1d15` Shell current-nav, then
this report.

## Not verified

- The browser walk does not visit `#/bet` (its route list is fixed); the ON page was checked by the screenshots above,
  not by the walk. The walk after the final `Shell.tsx` line was not rerun.
- The cause of the one hung OFF walk (seed 1) is unknown.
- `npm run build` (with `precache.mjs`) is what `check.sh` runs; `flag-parity.mjs` uses plain `vite build`, so the
  service worker's `precache.json` is not scanned for markers (it lists file names only).
- Nothing pushed, nothing merged. The replay oracle was not run (no change outside `app/` and `tests/test_layering.py`).
- Commands to reproduce, from the worktree root: `app/scripts/check.sh --browser` and
  `app/scripts/check.sh --browser --betting` (set `WALK_API_PORT`/`WALK_WEB_PORT` if 8090/5290 are busy);
  `cd app/web && node scripts/flag-parity.mjs`; `uv run --no-sync pytest tests/test_layering.py --no-cov -q`.
