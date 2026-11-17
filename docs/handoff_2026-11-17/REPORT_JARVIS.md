# Report: follow-up run of 2026-11-17 (Jarvis look + betting tools behind one switch)

Brief: `~/Downloads/jarvis-followup/FOLLOWUP_PROMPT.md`. Everything is on `agent/app-jarvis-followup` (worktree
`~/Downloads/nba-wt-1117-jarvis`), which is a superset of the first run's `agent/integration-2026-11-17`. Nothing was
merged to `main`, nothing was pushed, the main checkout was not edited. Times are Central, from the clock.

## 1. See it

```
http://127.0.0.1:5185        the new look, switch OFF, tonight's export (what a friend sees)
http://127.0.0.1:5186        the same with the betting tools ON
app/scripts/betting-off.sh --export          # start it yourself, switch OFF
app/scripts/betting-on.sh --export           # switch ON
API_PORT=8013 WEB_PORT=5186 app/scripts/betting-on.sh --export     # when the default ports are busy
```

The two URLs are background jobs of the overnight session; if they are gone, use the scripts. The betting tools are in the
Market lens: switch the lens at the top right of Discover, then use "Market tools" in the sidebar, or the "Add to slip"
buttons on a game or player page.

## 2. What landed

**The switch (section 1 of the brief).** `VITE_PH_BETTING` (web build) and `PH_BETTING=1` (API), default off everywhere.
Only `app/web/src/features.ts` and `app/api/src/parlayhitters_api/features.py` read them (a test enforces it). Off: no
nav entry, no route (`#/bet...` is the not-found screen), no add-to-slip button, no `/api/betting/*` route (typed 404),
and the module is absent from both bundles. `app/web/scripts/flag-parity.mjs` builds both ways and proves it with 16
marker strings; it is a step of `app/scripts/check.sh`. Scripts: `app/scripts/betting-on.sh`, `betting-off.sh`, and
`--betting` on `dev.sh`, `check.sh`, `artifact.sh`, `visual.sh`.

**The look (section 3), not behind the switch.** The app already had the kit's palette and fonts (it descends from an
older Jarvis prototype), so the work was finish and structure:
- Tokens for radius, shadow, gradient, status colours and focus; one stylesheet `jarvis.css` plus `pages-a.css` and
  `pages-b.css`. Removing those three imports reverts the look.
- Shared components: page heading, section head, card variants, tiles, filter chips, provenance and freshness badges,
  sticky and stacked tables, one family of loading / empty / stale / partial / error / reconnecting states, a side panel
  (right-hand column from 1280 px, bottom sheet below).
- Shell: grouped sidebar (Tonight, Record and tests, Market tools, Reference, You), slide-in phone menu, the same five
  bottom tabs in every lens, search palette, theme toggle, one-line health strip in plain words.
- Simple / Analyst: a first-run chooser as step one of the in-page intro (never blocks; skipping keeps Simple); the
  switch sits beside the lens switch at the right of every page heading that has them. Analyst lays out model name and
  version, stored quantiles, n and intervals, lead time and the T-60 mark; Simple folds them.
- Every page restyled, one main heading per page, tab titles that name the page, "On this page" jump lists on the long
  pages, a slim intro on shared deep links.
- Kept from the app rather than the kit: 12 px minimum text (the kit uses 6 to 9 px labels), self-hosted fonts only.

**Betting tools (section 4), behind the switch, Market lens only.**
- Pure logic in `app/web/src/betting/` (catalog, slip model, hedge maths, local repository, ids), generalised from the
  kit's four demo markets to an injected catalog; the kit's numeric fixtures are kept exactly.
- Screens: hub `#/bet`, rolling slip (side rail on desktop, bottom sheet on phones, also `#/bet/slip`), add-to-slip on
  game and player pages, Compare venues, Slip Hedge Finder, Saved ideas (`#/bet/ideas`), Prop analysis
  (`#/bet/analysis`, four panels, all on real fields).
- API (GET only, switch on only): `/api/betting/providers`, `/api/betting/board/{game}/{player}/{stat}`,
  `/api/betting/slate/{date}`. Stored quote snapshots from the nightly export: one per line, provider and side, the
  newest provably before tip-off. No model number, no ranking, fees and settlement rules reported as unknown.
- State is in the browser only, scoped per viewer, with JSON export and import; an invalid import changes nothing.
  A `WorkspacePort` seam is marked and not implemented. There is no server write route.

**Verification tooling (sections 3.6 and 5).** `app/scripts/visual.sh` (`visual-check.mjs`): 360 captures with the switch
off, 444 with it on (routes x 375 / 768 / 1280 x dark / light x Simple / Analyst), structural assertions that gate, and
an opt-in pixel comparison against a local baseline. `app/web/scripts/bet-journey.mjs`: the whole flow in headless
Chrome. The route walker now takes a free port from the system (a seed-derived port had made concurrent walkers drive
each other's browser).

**Docs (section 6).** `app/README.md` section "Betting tools (behind one switch)"; `docs/jarvis-donor-map.md` (all 54 kit
files); `docs/jarvis-gap-matrix.md`; `docs/jarvis-acceptance-checklist.md`; `docs/contracts/betting_board_v1.md`.

## 3. Numbers

| What | Before the follow-up | Now, switch OFF | Now, switch ON |
|---|---|---|---|
| Web tests (vitest) | 408 | 559 | 559 |
| API tests | 560 | 604 passed, 1 skipped | 604 passed, 1 skipped |
| Layering test | 10 | 15 | |
| Flag parity | | passed, 16 markers | |
| js bundle | 390,523 B | 418,151 B | 584,495 B |
| css | 76,308 B | 124,827 B | same file |
| Single-file page, fixtures bake (at 0e1d18a3) | 4,520,376 B | 4,595,443 B | 4,761,601 B |
| Single-file page, tonight's export | | 6,497,488 B | 6,663,830 B |
| Route walk, 5 settings x 150 steps | 0 violations | 0 violations at all five settings (390, 390 with injected faults, 360, 768, 1180) | |
| Betting journey (real export, 375 and 1280, dark and light) | | | passed, 98 steps |
| Visual check, structural layer | | 120 captures (quick set), 0 with a gating finding | 148 captures (quick set), 0 with a gating finding |

With the switch off the js grew by the new look and the shared components, not by betting code: flag parity finds no
betting marker in either OFF bundle.

## 4. Skipped or partial, and why

- Coverage across venues is always "not shown" on stored prices: they are delayed snapshots older than the 15-minute
  staleness rule, and no settlement-rule identity is verified for any venue. Typed prices on one venue can show it.
- PrizePicks has no stored quotes (not in the export); it exists only as a typed entry with one stake and one multiplier.
- The kit's Admin, photo library, alerts and signals screens were not ported: the app already has its own, and the kit's
  metrics there are fixtures. Details per file in `docs/jarvis-donor-map.md`.
- Pixel comparison is opt-in and local: baselines are not committed (the repo blocks binary files).
- The prop page keeps two separate lines (the line card's and the what-if lab's). They are now labelled as separate;
  making them one changes what the lab starts from, so it is left for you.
- The search palette has no entry for Prop analysis: the screen needs a prop to mean anything, so it opens from Compare
  and from a slip leg.

## 5. Decisions proposed

Rows 18 onward in `docs/handoff_2026-11-17/DECISIONS_ROWS.md`. The ones that change what you see or use: the grouped
sidebar and fixed bottom tabs; the first-run chooser inside the intro; betting pages under `#/bet/...` with the existing
`#/hedge` page left alone; the 15-minute staleness rule; an idea with a voided market shows no net; ideas import merges.

## 6. For Devin only

- **Before using the betting tools with real money:** a legal and compliance read for your state and each venue's terms
  (recordkeeping and price comparison only; nothing here places a bet); whether venue names and stored quotes may be shown
  to a friend; the responsible-gambling line in the footer.
- **Whether to ever add a server write path** for the workspace (the seam is marked, not built). Today a slip lives in
  one browser and moves by export and import.
- **Fees and settlement rules per venue** are unknown in the export. Until they are supplied and verified, normalised
  cost needs a fee you type, and cross-venue coverage stays withheld.
- **Fonts and photos:** fonts are the two self-hosted OFL families the app already had; no kit PNG was copied; headshots
  stay the existing NBA-hosted images on served pages and initials in the artifact (your earlier decision).
- **Publishing:** the artifacts are built locally (section 7) and not published anywhere.
- **Merging:** this branch contains both runs. The first run's merge plan (`REPORT.md` section 4) still applies; the
  follow-up adds `app/` changes, `tests/test_layering.py`, and docs.

## 7. Artifacts

Built at 05:18 CST from the export made 2026-11-16 19:35 CST (nights 11-14, 11-15, 11-16), shared desk cut only (the bake
never passes the money switch). Not published.

```
~/Downloads/parlayhitters-artifacts-2026-11-17/parlayhitters.html               6,497,488 B   switch OFF (what a friend gets)
~/Downloads/parlayhitters-artifacts-2026-11-17/parlayhitters-betting-on.html    6,663,830 B   switch ON
```

Each is one file that opens from disk with no server. In the switch-ON file the stored quote snapshots are not included
(they come from the API), and Compare says so and takes typed prices. Rebuild: `app/scripts/artifact.sh --export` and
`app/scripts/artifact.sh --export --betting`.

## 8. Not verified

- A real phone, Safari, Firefox, a screen reader, the on-screen keyboard.
- The service worker's offline paths.
- Cross-tab sync of the slip.
- GitHub CI (nothing was pushed).
- The full visual set (360 captures off, 444 on) on the final tree: the quick set ran (120 and 148 captures).
- Importing a file inside the single-file page (it needs a file chooser).
