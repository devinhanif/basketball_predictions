# Lane J4b: the first betting screens (2026-11-17, 00:38 to about 01:30 CST)

Branch `agent/app-jarvis-betting-screens`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-screens`. Everything sits behind
the one switch (`VITE_PH_BETTING`). With the switch off, nothing new is visible, reachable or shipped: the OFF CSS is
byte-identical, and every betting marker is absent from the OFF builds. The UI is built on the logic lane's
`slipModel`/`hedgeMath`/`catalog`. No arithmetic was re-implemented. The only additions are unit conversion and
display.

## Built (commits)

| # | Item | Commit |
|---|---|---|
| 1 | Workspace plumbing: `store.ts` (one store per viewer scope over `LocalWorkspacePort` + `createSlipSession`, serial edits, failed save kept with retry/discard, invalid import changes nothing), `useBet.tsx` (mounted once, survives routes and reloads, `storage` event sync across tabs), unpriced **picks** added to the workspace, `copy.tsx` (the one constants file), `betting.css` | `f401396e` |
| 2 | Rolling slip: the persistent panel (desktop right panel that collapses to a launcher tab; phone bottom sheet that is modal, traps focus, closes on Escape and returns focus to the launcher) and the `#/bet/slip` page. It holds: picks waiting for a price; recorded positions, locked; planned legs (include toggle, amount, typed price); per-leg provenance and "price when added"; totals; the coverage block, or the model's `text` when it is withheld; warnings; shared exposure in words; the outcome table; title/thesis/tags; "Mark slip as placed" with a confirmation dialog; "Open Hedge Finder with these positions"; export and import; the labelled demo | `4b249528` |
| 3 | Add to slip on the Leg page and beside every prop on the Game page (Market lens only, no price) | `25599a37` |
| 4 | Compare `#/bet/compare` (also contains the fix that hides the slip panel outside the Market lens) | `c9579131` |
| 5+6 | Slip Hedge Finder `#/bet/hedge` and the `#/bet` hub, which replaces the placeholder. One commit, because both edit `index.ts` (decision 9) | `0bc61ae9` |
| 7 | Tests (`screens.test.ts`, plus `store.test.ts` from commit 1) and `scripts/bet-journey.mjs` | `53438482` |

Skipped: nothing from the brief. Ideas and Analysis belong to the next lane.

## API for the Ideas lane

- Read the workspace in a component with `const bet = useBet()` (from `./useBet`). The fields are
  `bet.ready`, `bet.scope`, `bet.store`, `bet.view` (`phase`, `workspace`, `dirty` and `error`) and `bet.actions`.
  The current draft is `bet.view.workspace.draft`. Its notes are `.title`, `.thesis` and `.tags`; the slip page edits
  them through `bet.actions.setNotes({ title?, thesis?, tags? })`.
- Saved ideas live in `ws.ideas`. Save one with
  `bet.store.edit((ws) => { const r = saveIdea(ws, { now: new Date().toISOString(), status, title }); return r.ok ? { ok: true, workspace: r.workspace, text: "Saved." } : r; })`.
  Load, remove and record outcomes the same way, with `loadIdea`, `removeIdea` and `recordOutcomes`. Every edit runs
  in order and is saved; a failure comes back as `{ ok: false, code, text }`, and `say(done)` turns it into a toast.
- Open the slip with `setPanelOpen(true)` (or `revealSlip()`, which opens it only on a wide screen), or link to
  `#/bet/slip`. Link to a comparison with `compareHashFor(market, side)`, and to a hedge with `#/bet/hedge?leg=<legId>`.
- Pieces to reuse: `MoneyNote`, `MarketGate`, `OutcomeTable`, `CoverageBlock`, `LegCard`, `Provenance`, `money` and
  `signedMoney` (in `parts.tsx`); `SlipTools` (in `Slip.tsx`). Register a screen in `ROUTES`/`NAV` in `index.ts` and
  add a marker to `markers.json`.

## Tests, sizes, journey

- Web vitest: **517 passed, flag OFF and flag ON** (was 506 before the screens tests; 485 when the lane started, after
  the logic lane). The gate passed before every commit, in both states: `npm run check && npm run build:artifact`,
  then `VITE_PH_BETTING=1` the same, then `node scripts/flag-parity.mjs` (12 markers).
- Bundles, in bytes:

  | build | js | css | artifact |
  |---|---|---|---|
  | OFF, baseline at lane start | 390,596 | 76,308 | 4,520,449 |
  | OFF, now | 390,796 | 76,308 (identical file) | 4,520,649 |
  | ON, now | 502,636 | 76,308 | 4,632,480 |

  The OFF +200 bytes are the two `<AddToSlipSlot/>` call sites; they render null. OFF js was also re-hashed by a
  module-order change (decision 3).
- Journey: `node scripts/bet-journey.mjs <base> <outdir>`, run against servers with the switch on (the API lane's
  branch on the frozen export). Result: **JOURNEY PASSED at 375 and 1280, dark and light**, plus a Learn/Explore
  check (no betting UI; every `#/bet...` route shows the "Market screen" note). Prop used: Ayo Dosunmu points,
  2026-11-16, which has 14 stored venues. Steps: add to slip on the player page; the pick waits in the slip; Compare;
  type Kalshi 48c + 1c and DraftKings -110 / 0; add both; Mark slip as placed, then Cancel (focus returns to the
  button); mark again and confirm (focus lands on the Hedge Finder link); the Hedge Finder chip shows the player;
  reload, and the slip still holds 2 recorded legs. Every step also checks for uncaught exceptions, page-level
  horizontal scroll and the money note.
- `app/scripts/check.sh --browser` OFF / ON: see "Browser checks" at the end.

## Screenshots I read

All in `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`:
`journey1/` and `journey2/` (every journey step at 375 and 1280, dark and light), and `look1/`, `look2/`, `look3/`
(Compare at 1280 dark and 375 light, the hub, the empty Hedge Finder).

What I found and fixed:
- the launcher showed in the Learn lens;
- a price typed seconds earlier read as "age unknown", which would also have hidden coverage;
- the Compare cards were too long (prices are now on a fee-only line, unavailable venues fold to a "Type a price"
  button, the fee is behind a disclosure, the line picker is a select);
- the hub repeated two sentences.

## Requests for the visual lane

1. A `dollars()` / signed-dollars function in `format.ts`. The betting module has its own `money()` in `parts.tsx`,
   built on `MINUS`, `MISSING` and `count`.
2. The shared `SidePanel`. `SlipPanel` keeps simple props (`onBetRoute`) and its open state lives in
   `setPanelOpen`/`usePanelOpen`, so the swap only replaces the container `div` and the sheet's focus trap.
3. Provenance badge tones. These currently borrow `Badge` tones `base`/`mine`/`none`/`model`.
4. Toasts cover the top of a phone screen during a burst of actions. Consider a max of one, or bottom placement.

## Decisions (what / why / what would reverse it)

1. **Unpriced picks are a new optional `picks` list in the workspace.** Version 1 is unchanged; a document without
   `picks` reads as empty. Why: "add to slip with no price" has no home in `SlipLeg`, which needs a quote, and a
   separate key would fall outside export/import. Reverse: the logic lane prefers a version bump with a migration.
2. **The stylesheet ships as `betting.css?inline`, injected by `ensureBetStyles()`.** `main.tsx` and
   `main.artifact.tsx` are untouched. Why: a plain CSS import inside the module leaked into the OFF CSS (measured);
   the inline string is tree-shaken with the module. Reverse: a toolchain that drops the CSS of unused modules.
3. **`/* @__PURE__ */` on the logic lane's module-level `Object.freeze`, and lazy module state.** Why: they survived
   tree-shaking (the OFF js grew 707 bytes and carried "PrizePicks"). The OFF js hash still changed (module order);
   the size and strings match the baseline, and only identifier letters differ. Reverse: none expected.
4. **Exchange quotes stored in American odds become cents.** The export's `implied_prob_raw` is used when present,
   otherwise the cost formula's own conversion. Why: the export stores Kalshi in American odds, and the Hedge Finder
   needs whole contracts. Reverse: the export carries native cents.
5. **Planned prices go stale after 15 minutes, so every stored (delayed) quote shows "out of date" and coverage is
   withheld.** This is the logic lane's default, kept. Reverse: Devin sets `maxQuoteAgeMs`.
6. **Venue cards: priced venues first, then unavailable ones, each group in name sequence.** Never by price. The
   "lowest cost" mark is shown only if `comparability` passes for every pair, which never happens today because
   settlement is unverified. Reverse: Devin wants one flat alphabetical list.
7. **The page is titled "Slip Hedge Finder"** because the existing `#/hedge` is already called "Hedge Finder".
   Reverse: rename either one.
8. **After an add, the slip panel opens only on a wide screen.** On a phone the toast carries the count, because the
   sheet is modal. Reverse: Devin wants the sheet to open.
9. **Hedge Finder and the hub share one commit** (both edit `index.ts`). The plumbing commit was checked alone
   (lint, tsc and vitest in both states, with uncommitted work stashed); the other commits were gated on the tree as
   it stood.
10. **Edited `src/bettingSafety.test.ts`** (outside the listed files). It pinned `#/bet/slip` etc. to not-found ("no
    screen is registered yet") and pinned the placeholder sentence as marker 0. It now follows the registry:
    registered paths parse only when ON, everything else stays not-found in both states, and marker 0 is the hub's
    sentence. Nothing was weakened. Reverse: the flag lane prefers a different shape.
11. **Placeholder.tsx was removed** (replaced by the hub). The API's `/api/betting/status` is no longer called by the
    web app.
12. **A recorded position's price cannot be edited.** Only planned legs take a typed price. Why: the entry price is a
    fact of the record. Reverse: allow corrections with a "corrected" mark.

## Not verified

- Against the merged API: I ran the API lane's branch read-only on port 8081, so the betting routes are exercised
  only through that branch and its contract file. They were not run through this branch's API (which has
  `/status` only). With this branch's API, Compare shows the designed "Stored snapshots are not available" state.
  I did not screenshot that state.
- The artifact (single-file) betting pages were built (ON artifact passes its forbidden-text scan) but not opened in
  a browser.
- 768 px was not photographed; only 375 and 1280.
- No Safari, no physical phone, no real `storage` quota error (the failed-save path is unit-tested with a throwing
  storage).
- Cross-tab sync (the `storage` event) was not exercised in a browser.
- Nothing pushed, nothing merged. Servers on 8081 and 5230 were mine and are stopped.

## Browser checks (from the worktree root, `WALK_API_PORT=8095 WALK_WEB_PORT=5295`)

- `app/scripts/check.sh --browser --betting`: **ALL CHECKS PASSED**. That covers 517 vitest, artifact, flag parity
  and route walk seeds 1 and 2 at 390 px (0 violations each).
- `app/scripts/check.sh --browser` (OFF): every step passed except one walk step. Seed 1, step v5 s24
  `market reload [none] unknown-deep #/zzz/yyy?x=1`, reported "expected a not-found screen". That address never
  reaches betting code (the router only hands `bet` to the module), and the same seed and step passed in the ON run.
  A standalone OFF rerun (`app/scripts/walk.sh`) passed both seeds with 0 violations. I read it as a timing flake,
  and record it here rather than call it green.
