# Jarvis donor map: every kit file and where it went in parlayhitters

The Jarvis Integration Kit (React 19 + TypeScript + Vite reference UI, its tests, integration starter and docs) is
donor code; parlayhitters (`app/`) is the destination and the source of truth. The kit arrived as a flat folder,
`/Users/devin/Downloads/jarvis-followup/`, with path separators encoded as `__` (so `kit__ui__src__Compare.tsx` is the
kit's `ui/src/Compare.tsx`). All 54 `kit__*` files are listed below, grouped by area. Each destination path was checked
to exist on branch `agent/app-jarvis-verify` (worktree head `850a3cb7` plus this lane's commits) on 2026-11-17.

Sources for the mapping: the lane reports in `docs/handoff_2026-11-17/` (`lane_jarvis_flag.md`, `lane_jarvis_logic.md`,
`lane_jarvis_look.md`, `lane_jarvis_betapi.md`, `lane_jarvis_screens.md`, `lane_jarvis_pages_a.md`,
`lane_jarvis_pages_b.md`) and the code itself. Rows marked **pending the Ideas lane** depend on work (Ideas, the minimal
Analysis view) that had not merged into this worktree when the map was written.

Not-ported reasons used below: **React-only shell** (a standalone React root, build or config the app already has in
Preact), **fixture data** (sample players, prices, P&L or metrics that must never look live), **photos** (the kit's
player PNGs are not copied: brief section 2), **demo auth** (the browser-side `Jarvis` password and demo accounts: no
accounts exist), **superseded** (an existing app feature already does it), **deferred** (later work or Devin's call).

## Docs and packaging

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__README.md` | `README.md` | Not ported (the kit's own front page). Its run instructions are for the React reference; the app's equivalent is the section "Betting tools (behind one switch)" in `app/README.md`. |
| `kit__HANDOFF_PROMPT.md` | `HANDOFF_PROMPT.md` | Not ported: superseded by the follow-up brief (`FOLLOWUP_PROMPT.md`, outside the repo) and the lane reports in `docs/handoff_2026-11-17/`. |
| `kit__VERIFICATION.md` | `VERIFICATION.md` | Not ported (the kit's record of its own 71 checks). The app's evidence is `docs/jarvis-acceptance-checklist.md` and the lane reports. |
| `kit__docs__ACCEPTANCE_CHECKLIST.md` | `docs/ACCEPTANCE_CHECKLIST.md` | `docs/jarvis-acceptance-checklist.md` (filled against the real app). |
| `kit__docs__GAP_MATRIX.md` | `docs/GAP_MATRIX.md` | `docs/jarvis-gap-matrix.md` (filled against the real app). |
| `kit__docs__INTEGRATION_GUIDE.md` | `docs/INTEGRATION_GUIDE.md` | Not ported as a file. Its unit and ownership rules are in "How to port more of the kit" below; the quote board's contract is `docs/contracts/betting_board_v1.md`. |
| `kit__docs__UI_INVENTORY.md` | `docs/UI_INVENTORY.md` | Not ported as a file: this map replaces it for the app. |
| `kit__integration__feed.example.json` | `integration/feed.example.json` | Not ported: fixture data. Its validation cases (unknown fee, unknown rules, arbitrary ids) were adapted as tests in `app/web/src/betting/catalog.test.ts` ("donor feed cases, adapted"). |

## Build, config and entry (React reference only)

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__README.md` | `ui/README.md` | Not ported: React-only shell (reference build instructions). |
| `kit__ui__index.html` | `ui/index.html` | Not ported: React-only shell. The app's page is `app/web/index.html`. |
| `kit__ui__package.json` | `ui/package.json` | Not ported: React 19 and `lucide-react` would be new dependencies (brief section 2). The app keeps `app/web/package.json`. |
| `kit__ui__tsconfig.json` | `ui/tsconfig.json` | Not ported: React-only shell. The app keeps `app/web/tsconfig.json`. |
| `kit__ui__vite.config.ts` | `ui/vite.config.ts` | Not ported: React-only shell. The app keeps `app/web/vite.config.ts`; the switch is read only in `app/web/src/features.ts`. |
| `kit__ui__scripts__build-standalone.mjs` | `ui/scripts/build-standalone.mjs` | Not ported: superseded by the app's single-file build, `app/web/scripts/build-artifact.mjs`. |
| `kit__ui__src__main.tsx` | `ui/src/main.tsx` | Not ported: React-only shell (a standalone root). The app's entries are `app/web/src/main.tsx` and `app/web/src/main.artifact.tsx`. |

## Look and shell (not behind the switch)

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__src__styles.css` | `ui/src/styles.css` | Tokens reconciled into `app/web/src/theme.css` (radius, elevation, gradients, status colours, focus ring, shell geometry; nothing removed or renamed) and the restyle of existing classes in `app/web/src/jarvis.css`. No donor CSS pasted; global selectors not copied. (lane J2a) |
| `kit__ui__src__ui.tsx` | `ui/src/ui.tsx` | Primitives re-made in Preact in `app/web/src/ui/` (`Card.tsx`, `Badge.tsx`, `PageHead.tsx`, `Chips.tsx`, `State.tsx`, `SidePanel.tsx`, `Tabs.tsx`, `Table.tsx`; exported from `app/web/src/ui/index.ts`). Portrait/asset lookup not ported (photos): avatars stay `app/web/src/Avatar.tsx` (NBA headshot by player id, else initials). |
| `kit__ui__src__App.tsx` | `ui/src/App.tsx` | Partly. Shell patterns: `app/web/src/Shell.tsx` (sidebar, crumbs, theme, search), `app/web/src/MenuDrawer.tsx` (mobile drawer with focus trap and Escape), `app/web/src/ui/SidePanel.tsx` (desktop rail, phone sheet). Preferences: superseded by `app/web/src/pages/Settings.tsx`. Add-to-slip: `app/web/src/betting/AddToSlip.tsx` through `app/web/src/BetSlots.tsx` (on Leg and Game, Market lens). Not ported: the overview's fixture players and watchlist (fixture data), the `Jarvis` gate (demo auth). |
| `kit__ui__src__Entry.tsx` | `ui/src/Entry.tsx` | Partly. `ModeChooser` became step one of the in-page intro in `app/web/src/Welcome.tsx` (never blocks; choice in `ph.mode`). `LoginScreen` not ported (demo auth). |
| `kit__ui__src__entry.css` | `ui/src/entry.css` | Restyled through tokens in `app/web/src/jarvis.css` (chooser cards and glow). |

## Betting tools (behind the switch, `app/web/src/betting/`)

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__src__slip-model.ts` | `ui/src/slip-model.ts` | `app/web/src/betting/slipModel.ts`; its catalog part to `app/web/src/betting/catalog.ts`, sample fixtures to `app/web/src/betting/sample.ts` (labelled, only on explicit action), enumeration to `app/web/src/betting/hedgeMath.ts`. The four-market `MarketId` union and the 24-quote rule are gone (injected catalog). (lane J3) |
| `kit__ui__src__types.ts` | `ui/src/types.ts` | `app/web/src/betting/types.ts` (slip types only). Player, account and Admin types not ported (demo auth, photos). |
| `kit__ui__src__hedge.mjs` | `ui/src/hedge.mjs` | `app/web/src/betting/hedgeMath.ts` (`calculateFixedScenarioHedge`, the donor's legacy six-state calculator, kept pure with its tests). The app's own `#/hedge` page (`app/web/src/pages/Hedge.tsx`, `app/web/src/hedge.ts`) is untouched. |
| `kit__ui__src__hedge.d.mts` | `ui/src/hedge.d.mts` | Folded into `app/web/src/betting/hedgeMath.ts` (typed TypeScript, no separate declaration). |
| `kit__ui__src__local-id.ts` | `ui/src/local-id.ts` | `app/web/src/betting/ids.ts`. |
| `kit__ui__src__storage.ts` | `ui/src/storage.ts` | `app/web/src/betting/repository.ts` (`LocalWorkspacePort`, versioned and viewer-scoped key `ph.betting.workspace:device`) and `app/web/src/betting/store.ts` (one store per scope, failed save kept with retry/discard, invalid import changes nothing). Not ported: the unscoped key, the sample fallback, photo and account validation. |
| `kit__ui__src__Compare.tsx` | `ui/src/Compare.tsx` | `app/web/src/betting/Compare.tsx` (`#/bet/compare`) with `app/web/src/betting/board.tsx` (stored-snapshot cards, typed prices, the existing-bet form). `intent="hedge"` became `app/web/src/betting/Hedge.tsx` (`#/bet/hedge`, titled "Slip Hedge Finder"). (lane J4b) |
| `kit__ui__src__compare.css` | `ui/src/compare.css` | `app/web/src/betting/betting.css`, scoped under `.ph-bet`, injected only by the module (`?inline`), so the OFF CSS is unchanged. |
| `kit__ui__src__SlipCart.tsx` | `ui/src/SlipCart.tsx` | `app/web/src/betting/Slip.tsx` (`SlipPage` at `#/bet/slip`, `SlipPanel` in the shell: desktop panel, phone sheet), with `app/web/src/betting/parts.tsx` (money note, outcome table, coverage block) and `app/web/src/betting/useBet.tsx`. |
| `kit__ui__src__slip-cart.css` | `ui/src/slip-cart.css` | `app/web/src/betting/betting.css` (scoped). |
| `kit__ui__src__workspace-slip.css` | `ui/src/workspace-slip.css` | `app/web/src/betting/betting.css` (slip panel and sheet, scoped). The generic rail and sheet are `app/web/src/ui/SidePanel.tsx`; moving the slip onto it is an open request (lane J4b request 2). |
| `kit__ui__src__Ideas.tsx` | `ui/src/Ideas.tsx` | **Pending the Ideas lane.** The pure logic exists (`saveIdea`, `loadIdea`, `removeIdea`, `recordOutcomes`, `describeOverlaps` in `app/web/src/betting/slipModel.ts`) and the slip edits title, thesis and tags; no Ideas screen is registered in `app/web/src/betting/index.ts` in this worktree. |
| `kit__ui__src__ideas.css` | `ui/src/ideas.css` | **Pending the Ideas lane.** |

## Desk and analysis screens

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__src__Analysis.tsx` | `ui/src/Analysis.tsx` | Superseded: Signals by `app/web/src/pages/Signals.tsx` (correction links from `app/web/src/desk.ts`), History by `app/web/src/pages/History.tsx` (the desk's own ledger, calibration and kill-switch state, no fixture metrics), Alerts by `app/web/src/pages/Alerts.tsx` with `app/web/src/alerts.ts`. Its legacy `Hedge` is the pure `calculateFixedScenarioHedge` above. The brief's "minimal Analysis view on real export fields" is **pending the Ideas lane**. Fixture metrics not ported. |
| `kit__ui__src__analysis.css` | `ui/src/analysis.css` | Not ported: superseded by `app/web/src/desk.css` (already in the donor's style per lane J2b-B) and `app/web/src/pages-b.css`. |
| `kit__ui__src__Admin.tsx` | `ui/src/Admin.tsx` | Superseded by `app/web/src/pages/Admin.tsx` (player photos, activity, people, data tabs; photos via `app/web/src/photos.ts`). Not ported: account roles and the `Jarvis` password (demo auth), photo upload into kit PNG storage (photos). |
| `kit__ui__src__admin.css` | `ui/src/admin.css` | Not ported: superseded by `app/web/src/desk.css`. |
| `kit__ui__src__data.ts` | `ui/src/data.ts` | Not ported: fixture data (sample probabilities, edge labels, risk states, P&L, counts). |

## Integration starter (`ui/src/integration/`)

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__src__integration__contracts.ts` | `ui/src/integration/contracts.ts` | `app/web/src/betting/types.ts` (fee as known or unknown, provenance live/delayed/manual/sample, entry picks, freshness). `ModelSignal`, `FeedResult`, `PositionView` not ported: the model is the nba/ export's, and the betting routes serve no model number (`model_ref` only). |
| `kit__ui__src__integration__validate-feed.ts` | `ui/src/integration/validate-feed.ts` | `app/web/src/betting/catalog.ts` (`buildCatalog`, per-record validation; holdout season refused). API side: `app/api/src/parlayhitters_api/betting/catalog.py`. |
| `kit__ui__src__integration__model-feed.ts` | `ui/src/integration/model-feed.ts` | `app/web/src/betting/catalog.ts` (`freshness`, `comparability`). `createModelFeedPort` not ported: no model reader in the module (the nba/ model stays the only authority). |
| `kit__ui__src__integration__workspace-controller.ts` | `ui/src/integration/workspace-controller.ts` | `app/web/src/betting/repository.ts` (`createWorkspaceSession`: revision tag, idempotency `mutationKey`, failed edits kept, conflicts typed). |
| `kit__ui__src__integration__ConnectedWorkspace.tsx` | `ui/src/integration/ConnectedWorkspace.tsx` | Not ported as-is (the brief says not to ship it). Its job is done by `app/web/src/betting/useBet.tsx` and `app/web/src/betting/store.ts`. |
| `kit__ui__src__integration__host-bindings.example.ts` | `ui/src/integration/host-bindings.example.ts` | Not ported (an example). The seam for a future server is the unimplemented `WorkspacePort` in `app/web/src/betting/repository.ts` (a marked comment; no server write route exists). |
| `kit__ui__src__integration__index.ts` | `ui/src/integration/index.ts` | Not ported (a barrel). The module's single entry is `app/web/src/betting/index.ts`. |
| `kit__ui__src__integration__integration.css` | `ui/src/integration/integration.css` | Not ported: styles for `ConnectedWorkspace` only. |

## Tests

| Kit file | Kit path | Destination in parlayhitters |
|---|---|---|
| `kit__ui__tests__hedge.test.mjs` | `ui/tests/hedge.test.mjs` | `app/web/src/betting/hedgeMath.test.ts`, block "donor fixed-scenario hedge" (10 cases, ported). |
| `kit__ui__tests__slip-model.test.mjs` | `ui/tests/slip-model.test.mjs` | `app/web/src/betting/slipModel.test.ts`, block "donor slip-model cases" (ported and adapted: entry picks priced null, coverage suppressed on unverified settlement, no 24-quote rule). |
| `kit__ui__tests__integration__controller.test.ts` | `ui/tests/integration/controller.test.ts` | `app/web/src/betting/repository.test.ts`, block "donor controller cases on the session" (a conflict is a typed result, not a thrown error). |
| `kit__ui__tests__integration__feed.test.ts` | `ui/tests/integration/feed.test.ts` | `app/web/src/betting/catalog.test.ts`, block "donor feed cases, adapted" (the model-reader case dropped: no model reader). |
| `kit__ui__tests__slip-ui.test.tsx` | `ui/tests/slip-ui.test.tsx` | Partly. The flow (Jalen sample, add Bobby, mark placed, Hedge Finder) is `app/web/src/betting/screens.test.ts` ("slip to Hedge Finder: the donor's NBA flow") over the store, not a DOM render (no DOM test library is a dependency); the browser version is `app/web/scripts/bet-journey.mjs`. Ideas cases **pending the Ideas lane**. |
| `kit__ui__tests__integration__connected-workspace.test.tsx` | `ui/tests/integration/connected-workspace.test.tsx` | Not ported as a component test. Its behaviours (failed save kept and retried, invalid import changes nothing, no sample on error) are in `app/web/src/betting/store.test.ts` and `app/web/src/betting/repository.test.ts`. |
| `kit__ui__tests__ui.test.tsx` | `ui/tests/ui.test.tsx` | Not ported: it tests the reference's onboarding, Admin, photos and alerts. The app's own suites cover its versions (for example `app/web/src/modes.test.ts`, `app/web/src/desk.test.ts`). |
| `kit__ui__tests__standalone.integration.mjs` | `ui/tests/standalone.integration.mjs` | Not ported: superseded by the app's single-file build checks (`app/web/scripts/build-artifact.mjs` forbidden-text scan, `app/web/src/artifact.test.ts`) and the storage-blocked fallback test in `app/web/src/modes.test.ts`. A scripts-disabled preview check does not exist (see the acceptance checklist). |

Not in this folder and not copied: the kit's player photos (`ui/public/players/*.png`), its `asset-sources.json`,
`preview/Jarvis-Interactive.html`, `MANIFEST.json`, `CHECKSUMS.sha256` and lockfile (photos and React-only shell).

## How to port more of the kit

Rules (from the brief, sections 1 to 3, and tonight's lane decisions):

1. **Preact, no new dependency.** Re-write JSX for Preact (`preact`, `preact/hooks`); no React, no `lucide-react` (use
   `app/web/src/icons.tsx`), no kit `package.json`, Vite config or lockfile.
2. **Behind the switch.** Betting code lives only in `app/web/src/betting/` (web) and
   `app/api/src/parlayhitters_api/betting/` (API). Only `app/web/src/features.ts` and
   `app/api/src/parlayhitters_api/features.py` read the switch. A module-level call in `betting/index.ts` can survive
   tree-shaking (lane J1 found `href: betHref()` leaking `#/bet`): use literals there, and mark module-level
   `Object.freeze` calls `/* @__PURE__ */` (lane J4b decision 3).
3. **Scope the CSS under `.ph-bet`** and ship it through `betting.css?inline` (injected by `ensureBetStyles()`), never a
   plain CSS import: a plain import leaked into the OFF CSS (lane J4b decision 2). Kit globals (`body`, `button`,
   `input`, `.card`, `.modal`, `.badge`) map to the app's tokens in `app/web/src/theme.css`.
4. **Pure logic first, with the donor's tests.** Port the arithmetic into a framework-free `.ts` file next to its
   tests (the pattern of `slipModel.ts`, `hedgeMath.ts`, `catalog.ts`; `app/web/src/betting/hygiene.test.ts` refuses
   framework imports in those files), keep the donor's exact fixtures (53c vs 54.954955c, $24.50 to 25 contracts), then
   build the screen on top. The model stays the only authority on probabilities: no re-derived or "synergy" numbers.
5. **Money-safety grep.** `app/web/src/bettingSafety.test.ts` scans both betting directories for credentials words,
   `order(s)`, `token(s)`, `execute`, non-same-origin `fetch`, any URL, other network channels and non-GET routes; the
   only allowed phrase is "no orders are placed". Every priced screen shows `MoneyNote` from
   `app/web/src/betting/parts.tsx`.
6. **Markers for flag parity.** Add one distinctive string of each new screen to `app/web/src/betting/markers.json`;
   `app/web/scripts/flag-parity.mjs` then proves the OFF builds carry none and the ON builds carry all. A marker must
   not appear in source outside `src/betting/`, comments included.
7. **No sample as live.** Demo content loads only from the labelled button; a failed request is an error state.

The seam (lane J1, `app/web/src/betting/seam.ts` for the types):

- **A screen:** add a `BettingRouteDef` (`{ path, title, render }`) to `ROUTES` in `app/web/src/betting/index.ts`; the
  route `#/bet/<path>` parses only once registered, and only with the switch on.
- **A nav entry:** add a `BettingNavEntry` to `NAV` in the same file, with a literal `href`; `marketOnly: true` keeps
  it out of the Learn and Explore sidebars.
- **A slot on an existing page:** render `<AddToSlipSlot .../>` from `app/web/src/BetSlots.tsx` (renders nothing with
  the switch off); the shell's panel is `BETTING_MODULE.ShellPanel`.
- **An API route:** inside `register(app)` in `app/api/src/parlayhitters_api/betting/routes.py` (GET only, under
  `/api/betting/`), plus its template in `BETTING_ROUTES` in `app/api/tests/hostile_gen.py`.

Then run, from the repo root: `app/scripts/check.sh` and `app/scripts/check.sh --betting` (lint, types, tests, builds,
flag parity), `node app/web/scripts/bet-journey.mjs <base> <outdir>` against servers with the switch on, and
`app/scripts/visual.sh` / `app/scripts/visual.sh --betting` (add the new screen to `BET_SCREENS` in
`app/web/scripts/visual-check.mjs`).
