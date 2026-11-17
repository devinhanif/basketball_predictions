# Lane J6: the finishing pass (shared components, unrestyled components, whole-product review)

Branch `agent/app-jarvis-polish`, worktree `nba-wt-1117-jv-polish`, base `850a3cb7`. Started 02:03 CST 2026-11-17 (from
`date`). Edits only under `app/web/src/`, `app/web/scripts/learn-dom.mjs` and `route-walk.mjs` (one regex line each), and
this file. Presentation and wording only: no new field, no new arithmetic, no route, query or API change, no dependency.
Nothing pushed, nothing merged. Not touched: `src/betting/**`, `BetSlots.tsx`, `format.ts`, `toast.ts`, `router.ts`,
`api.ts`, `transport*.ts`.

## How to check it yourself
```
cd app/web && npm ci && mkdir -p artifact-data && cp <a baked.json> artifact-data/
npm run check && npm run build:artifact && VITE_PH_BETTING=1 npm run check && node scripts/flag-parity.mjs
cd ../.. && WALK_API_PORT=8099 WALK_WEB_PORT=5299 app/scripts/check.sh --browser
WALK_API_PORT=8099 WALK_WEB_PORT=5299 app/scripts/walk.sh --widths
node app/web/scripts/learn-dom.mjs http://127.0.0.1:<vite on fixtures> <chrome devtools port>
LENS=market node app/web/scripts/shots.mjs regular http://127.0.0.1:<vite> <outdir>   # contrast/size audit
```

## Inbox (shared-component requests from the other lanes)
| Item | What changed | Commit |
|---|---|---|
| `card-feature` below AA in light | Centre stop `#8666c7` -> `#6a52a8` (ink 5.8:1, muted 4.8:1); inside the card the tokens are re-pointed (ink, muted, accent-ink, lines, dark-glass surfaces and tiles, dark-theme status colours, initials disc), so any component on it passes in both themes. Now used for Parlay's verdict and Discover's closest look. Audit: 0 findings | `df01feb3` |
| `Table` stacked layout | `stack` prop: below 640 px each row is a labelled block; `lead` / `wide` / `label` per column; explicit `role=table/rowgroup/row/columnheader/cell` so headers stay exposed when CSS changes display; unsortable heads visually hidden (not removed); sort buttons kept as 44 px pills. Replaced the ~120 lines of hand-kept `::before` labels in `pages-b.css` (Scorecard x2, parlay diagnostics) and the second card render (`SnapCards`) on `#/source` | `e06fda4d` |
| `MarketOnly` duplicate id / h1s | `level` prop (default 1) and an id from the name (`marketOnlyId`); Parlay passes `level={2}`: one h1, no duplicate id | `06dc00f5` |
| Search lists Market-only screens in Learn | `marketOnly` flag on search items, `screensFor(market)`; `MARKET_ONLY` moved to `shellData` (re-exported from `Shell`) | `06dc00f5` |
| Dead `.today-head ~ nav.tabs` | Removed (the nights row is inside `.disc-toolbar`, never a sibling of the heading) | `132ddb15` |
| "book NN%" in Learn (Discover Analyst) | Hidden in Learn, kept in Explore and Market; `/\bbook \d{1,3}(\.\d)?%/i` added to the Learn price-word lists in `kit.test.ts`, `learn-dom.mjs`, `route-walk.mjs` (no existing copy matches) | `9c7722d8` |
| Dialogs without trap/Escape/return | The tour now renders inside `ui/Dialog` (trap, Escape, backdrop, focus return). The profile popover was `role="menu"` with non-menu children and no Escape: now a labelled group that closes on Escape (focus back to its button) or an outside click. Checked by driving keys over CDP: 14 Tabs stayed inside the tour; Escape closed both | `f52f9704` |

## Components restyled
| Component | Change | Commit |
|---|---|---|
| `Explore.tsx` | Each what-if a tile under the panel head; the player's name not repeated on his own page; phone insets that keep the chart's 12 px labels (flat at <= 380 px) | `d840dca9`, `79df0e47` |
| `TeammateSits.tsx` | Teammates are bordered toggles with a pressed state (they read as plain text before); read-out rows ruled, tabular | `d840dca9` |
| `AltLine.tsx` | "Try your own line" is a tile whose summary reads as a control, with a result box | `d840dca9` |
| `RoleSection.tsx` / `RoleCards.tsx` | Summary with hover and chevron; blocks as tiles; tabular numbers | `d840dca9` |
| `Knows.tsx` | Events as tiles on the rail; "what changed" as a tinted strip | `4129cd35` |
| `CardDetail.tsx` | Same radius, elevation and hover as the other disclosures | `4129cd35` |
| `Viz.tsx` | Chips bordered, tabular | `4129cd35` |
| `TopParlaysPanel.tsx` / `VerdictStrip.tsx` | Verdict box, tickets as tiles, strip as the header-card finish | `4129cd35` |
| `Tour.tsx` | Moved onto `Dialog` (above) | `f52f9704` |
| all plain `<details>` | A summary set to `display: flex` had lost the browser's marker, so "Try your own line", the role section, injury lists, "What this is and is not", the needs-field stubs etc. gave no sign that they open. Opt-in chevron by class | `d840dca9` + `00eee40e` |

## Review findings (ranked by value; screen, problem, fixed or not, commit)
| # | Screen | Problem | Fixed | Commit |
|---|---|---|---|---|
| 1 | Shell | Sidebar a flat list of 13 to 17 entries under "Workspace"; Market lens put six items in the phone bar | Grouped (below); five bottom tabs in every lens | `12048f7e` |
| 2 | Leg / Discover / Game / Scorecard / Tests / Terms | Lens and Simple/Analyst switches in four different places (full-width bar under the heading, a "View: lens (change)" fold, a lone row on Leg, the heading) | `PageControls`: both switches, labelled, at the right of the heading (stacked under it on a phone), lens meaning underneath | `12048f7e` |
| 3 | Deep link, first visit | A friend opening a shared player or game link saw the full chooser first; the page started below the fold on a phone | On any route but Discover the intro waits as one slim line ("New here? ... Start the short intro"); Discover keeps the chooser | `494e438d` |
| 4 | Every page | `document.title` was always "parlayhitters" | "Scorecard · parlayhitters"; game and player pages add their name once loaded | `09a0ba20` |
| 5 | Leg, Game, Scorecard, Tests | Long pages with no way to jump | "On this page" built from the page's own h2s (never lists a section that is not rendered), open on wide screens, folded on phones, moves focus | `bc197a1a` |
| 6 | Many | Disclosures with no visible affordance | Chevrons (above) | `d840dca9` |
| 7 | Tables | Sortable heads mixed case, plain heads upper case in the same row | Sort buttons inherit case, tracking and size; numbers tabular | `4129cd35` |
| 8 | Preferences (Learn) | "edge arithmetic" in the Detail-level help: a price word in Learn (the walk logged it once under load) | Lens-aware wording | `d0292b8a` |
| 9 | Search | Scorecard, Forward tests and Error codes not searchable | Listed | `d0292b8a` |
| 10 | Track record, phone | Seven night pills stacked one per row (~350 px of picker) | One scrolling row | `d0292b8a` |
| 11 | Leg, phone | Prices-by-book "Min to tip" clipped 40 px at 375; alt-lines Price at 360 | Heads wrap, tighter cells; 0 at 375. At 360 Market Simple a 1-2 px clip remains | `79df0e47` |
| 12 | Leg | h1 text read "ADAyo Dosunmu" (initials inside the heading) | Photo wrapper `aria-hidden` (accessible name and title are clean; `textContent` still holds the initials) | `12048f7e` |
| 13 | Headings with controls | Title pushed down by the taller control column | Top-aligned | `494e438d` |
| 14 | Print | No print styles | Content and footer on white, no chrome | `b9e36767` |
| 15 | `index.html` | `theme-color` (`#f1f1ee` / `#0e0f12`) does not match the Jarvis head colours (`#ffffff` / `#0c0f18`) | Not fixed: outside `src/` | - |
| 16 | Parlay verdict | "priced 1355 contracts" without a thousands separator | Not fixed: it is the engine's stored note text, shown verbatim | - |
| 17 | Parlay diagnostics | Caption "Records from 0, 50 on this page" reads oddly | Not fixed (wording would need offset+1 arithmetic) | - |
| 18 | Game | "Where this number comes from" link floats centred between two "?" disclosures | Not fixed | - |
| 19 | Explore lens, Leg | Two line sliders on one page (what-if lab and the line card) | Not fixed (structure, not finish) | - |
| 20 | Game (headless) | Below-the-fold avatars render as empty discs until their lazy image fails over to initials | Not fixed; only seen headless without network | - |

## Navigation grouping (and why)
Tonight: Discover, Live, Swipe, My picks. Record and tests: Track record, Scorecard, Forward tests. Market tools (Market
lens only; the betting module's entries join here when the switch is on): Signals, Hedge Finder, Model History.
Reference: Key terms, Error codes. You: Alerts, Preferences, Admin studio (when allowed). Why: it follows how a visitor
uses the app (tonight's slate, then whether to trust it, then prices, then help); "Market tools" disappears as a group in
Learn and Explore instead of leaving gaps. Every route is kept; `#/help/...` is now marked current by its own entry;
`#/source/...` marks Discover (as before); `#/bet/...` marks the module's entry. Phone bar: Discover, Live, Track record,
Swipe, My picks in every lens (Key terms fills in for the artifact, which has no Live or Track record); the rest is in the
drawer, grouped the same way.

## Numbers
| What | Before | After |
|---|---|---|
| vitest | 530 (base) | 536 (new `polish.test.ts`: search lens filter, Market-note ids, stack labels, jump-list labels); none removed or weakened |
| OFF js / css / artifact | 412.6 kB / 115.7 kB (first gate, after commit 1) | 417,207 / 123,240 / 4,593,454 B |
| ON js / css / artifact | - | 529,065 / 123,240 / 4,705,305 B; flag parity passed (12 markers) |
| Contrast/size audit (`shots.mjs`, real export, 390 + 1440, dark + light, Simple + Analyst) | - | Learn 112 screens, 0 findings; Market 112 screens, 0 findings |
| h1 per page | Parlay (Learn) 3 h1 + duplicate `mo-h` | exactly 1 on all 20 routes shot, both lenses, 375 and 1280; no duplicate ids |
| `check.sh --browser` (8099/5299, 02:58-03:03) | - | ALL CHECKS PASSED: api 604 passed / 1 skipped, web 536, artifact and flag parity, walk seeds 1 and 2 at 390: 0 / 0 violations |
| `walk.sh --widths` (03:03-03:08) | - | 0 violations at 390 plain, 390 injected, 360, 768, 1180 |
| learn-dom (fixtures, 03:09) | pass | pass: "Learn lens: no price words on any route" |

## Betting module (not mine; seen in code only)
- Its `MarketGate` renders `MarketOnly` at the default h1, which is right on its standalone screens; if it is ever placed
  under a page's own h1 it should pass `level={2}` (the prop now exists).
- Any shared CSS that names a betting class trips flag parity (I hit this once, `d840dca9`, fixed in `00eee40e`): worth a
  line in the module's README.
- Not exercised with `VITE_PH_BETTING=1` servers in a browser tonight (only the ON check and parity).

## Decisions (what / why / what would reverse it)
1. Grouped nav as above. Why: findability, fewer entries per glance. Reverse: restore `ALL_WORKSPACE` order and the two
   `side-label`s in `Shell.tsx`.
2. One place for view controls (`PageControls`, heading right). Why: Discover and Game already used the heading; the
   brief asked for one placement. The Discover "View: lens (change)" fold is gone. Reverse: per-page `<LensSwitch />`.
3. Deep links get the slim intro; Discover keeps the full chooser. Why: the page someone shared should be what a friend
   sees first. Reverse: drop `deep` in `App.tsx`.
4. Feature card tokens re-pointed inside the card rather than banning small text on it. Why: AA for any component.
   Reverse: the old stop in `theme.css` and drop the token block.
5. "On this page" reads headings from the DOM instead of hand-kept ids. Why: it can never list a section that is not
   rendered (lens and mode change sections). Reverse: remove the four `<OnThisPage />` lines.
6. Bottom bar the same five in every lens (Signals left the phone bar for the drawer). Why: muscle memory; the desk is
   often empty. Reverse: add "signals" back to `TABS`.
7. Error codes added to the nav under Reference. Why: `#/help` had no current marking and no way in except failures.
   Reverse: remove the entry.
8. Profile popover stops claiming `role="menu"`. Why: its children were not menu items, so assistive tech announced a
   broken menu. Reverse: a full ARIA menu with roving focus.
9. Commits are grouped by theme rather than one per component where the CSS lives in one file (`jarvis.css` cannot be
   staged by hunk non-interactively). `d840dca9` alone fails flag parity; `00eee40e` right after fixes it (no history
   rewrite: no force operations).

## Not verified
- Headless Chrome only; no real phone, Safari, Firefox or screen reader. Keyboard checked for the tour and popover only.
- The switch-ON shell (betting entries in the "Market tools" group) was not seen in a browser.
- Reduced motion not toggled (the jump list uses `auto` scrolling when it is set).
- Artifact built and checked by its script, not photographed.

## Screenshot directories (all under `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/jp/`)
`b0/` (before, every screen), `f1/` (feature card), `t1/` (stacked tables), `d1/` (tour), `n1/` (nav, drawer),
`pc1/` (page controls), `cx/`, `cx2/`, `c4/` (components), `sw/` (Swipe fixtures), `fr/`, `fr2/` (first run), `otp/`
(jump list), `lt/` (phone tables), `pr/` (print), `audit-learn/`, `audit-market/` (full audits). Donor: `../jv-donor-shots/`.
