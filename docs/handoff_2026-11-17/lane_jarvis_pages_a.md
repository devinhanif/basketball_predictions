# Lane J2b-A: Jarvis restyle of Discover (below its heading), Leg, Game, `#/source`, My picks, Swipe

Branch `agent/app-jarvis-pages-a`, worktree `nba-wt-1117-jv-pagesA`, base `2d89cf8e`. Started 00:53 CST 2026-11-17 (from
`date`). Presentation and order only: no new data, no new arithmetic, no route, query or API change, no dependency, no
test changed or removed. Nothing pushed, nothing merged.

## How to check it yourself
```
cd app/web && npm ci && mkdir -p artifact-data && cp <a baked.json> artifact-data/
npm run check && npm run build:artifact && node scripts/flag-parity.mjs
cd ../.. && WALK_API_PORT=8096 WALK_WEB_PORT=5296 app/scripts/check.sh --browser
WALK_API_PORT=8096 WALK_WEB_PORT=5296 app/scripts/walk.sh --widths
# learn-dom: fixture API + vite + a headless Chrome on a devtools port, then
node app/web/scripts/learn-dom.mjs http://127.0.0.1:<vite port> <chrome port>
```

## Pages (oldest commit first; each commit passed check + build:artifact + flag-parity on its own tree)
| Page | What was weak | What changed | Commit |
|---|---|---|---|
| Discover (below heading) | Today's Picks filled the first phone screen: five full-width rows each carrying a long paragraph; the night tabs sat below it and the selected night (newest, at the far right) was scrolled out of view at every width; the verdict came after the picks; the games list had no heading; at 768 px (Market, Simple) player names squeezed letter by letter | Toolbar (nights + lens) straight under the heading; selected night scrolled into view (the row only, never the page); order in Market: live strip, verdict, closest look + court, Today's Picks, desk tiles, radar; picks are cards (2-up from 900 px) with a numbers row; the "why" paragraph is folded ("Why this number") in Simple and shown in Analyst; "The games" section head; one-line key players wrap under the name at any width; closest look takes the header-card finish with its three numbers as tiles | `2bd528ca` |
| Leg | "No posted line for this stat" was a bare caption in the slider ends; the alt-lines table clipped its Price column at 375/390 (Market); headline tiles squeezed the model tile's link word by word on phones; the whole-forecast block ended in stacked paragraphs (survival sentence, DNP note, model line); the two "needs export fields" stubs were bare full-width rows | Designed no-line state (icon, title, why; "starts at the middle of our own forecast" only when `line_source` is `model_middle`); phone tiles: last 10 + last 5 side by side, model (and book) on their own rows; alt-lines table fits at 375 (tighter cells, calibration tag under the %); whole forecast = chart, then three tiles (at the line with n, chance to play with source, projected minutes), then the stored pieces (folded in Simple, laid out in Analyst), then ONE facts card: DNP rule note (Market only), not stored, model (+ version in Analyst) and made-at; the stubs are quiet dashed cards side by side | `b508916e` |
| Game | No visible title (the h1 was screen-reader only); in Analyst every player's four stat blocks stacked in one narrow column, making the page very long at 1280 | `PageHead`: kicker date + tip-off, the matchup as the h1 (same text as before), Detail switch on the right; stat blocks are cards two-up from 1180 px (plain rows on phones so the distribution bar keeps 12 px labels); slot-insertion points untouched (only `PropBlock`'s tail and the `STATS.map` wrapper changed) | `1dab67d8` |
| `#/source` (and the record dialog) | At 375 the six-column snapshot table crushed (dates on 4 lines, model and version clipped); the record was one long list; T-60 was a sentence only | `PageHead` with a way back (to the leg or the game); summary first: model id + T-60 badge (icon + word) + tiles for lead before tip-off (with made-at) and rows trained on (with fit-through); ruled definition rows; snapshots as short cards below 600 px (table from 600 px; same rows, same order, only one displayed); snapshots folded in Simple, laid out in Analyst | `2ebf9a81` |
| My picks | Bare h1; empty state a custom centered box | `PageHead` (kicker, title, description, one primary action "Make more picks"); empty state from the shared `StateBlock` family; ledger untouched | `0e9381b5` |
| Swipe | Bare h1; the night tabs sat under the deck | `PageHead` with a one-line description; "How does this work?" kept; night tabs above the deck | `3f157f88` |

Screenshots (all under `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`):
before `pa-before-mkt/` (Discover, Leg, Game, source; 375 + 1280, dark, both modes), `pa-p0/` (Picks, Swipe);
after: Discover `pa-d1/`, `pa-d2m/`, `pa-d2l/` (Learn); Leg `pa-l1/`, `pa-l2/`, `pa-l2l/` (Learn); Game `pa-g1/`;
source `pa-s1/`; Picks + Swipe on fixtures `pa-p1/`. Widths 375/768/1280, dark and light, Simple and Analyst (the
scratch copy of `shots.mjs` that takes widths from env is `pa-tools/shots.mjs`). Each was looked at.

## Simple and Analyst, now
| Page | Simple | Analyst |
|---|---|---|
| Discover | picks with "Why this number" folded; key players one line each | picks with the why shown; each key player: sentence, plus a stored row q25 / q50 / q75 and the model id (`model_source`) |
| Leg | stored pieces of the whole forecast folded ("More detail: mean, spread and the chances if he plays"); depth folded (unchanged) | model id and forecast stamp under the tiles; stored pieces laid out; model version in the facts card; depth inline (unchanged) |
| Game | player rows closed (unchanged) | rows open (unchanged), stat cards two-up, the ranges named (q50, q25 to q75, q5 to q95), model id per stat |
| `#/source` and the dialog | summary, record rows, snapshots folded | everything laid out |
| My picks, Swipe | no difference (no model detail on these pages) | no difference |
Both modes obey the same locks (LINE_CAL_LIVE, teammate-sits, Learn: no price words; the DNP note stays Market-only).

## Numbers
| What | Before (`2d89cf8e`) | After (`3f157f88`) |
|---|---|---|
| vitest | 508 / 508 (48 files) | 508 / 508 (48 files), none changed |
| flag-parity OFF build | not measured at base | js 407,710 B, css 106,878 B, artifact 4,567,670 B (after the first page: js 402,050, css 102,779, artifact 4,558,372) |
| Raw hex colours in my files | 0 | 0 |
| `px` literals in my files | 224 | 239: +10 hairline borders, +8 media-query breakpoints, +4 one-off sizes (a 20 px icon box, two 2 px gaps), with some removed from picks.css |
| Contrast/size audit (scratch `shots.mjs`, real export) | Leg 375 Market Simple: "Price" clipped 30 px; source 375: 6 broken + 5 clipped, 1280: 1 broken | 0 findings on every page, every width shot (375/768/1280), both themes, both modes, both lenses where they apply |
| learn-dom (fixtures) | pass | pass ("Learn lens: no price words on any route") |
| check.sh --browser (01:12-01:28) | - | every non-browser step passed (api ruff, format, mypy, pytest 581 passed / 1 skipped; fixtures ruff; web eslint, tsc, vitest 508, build, artifact "checks passed", flag parity); the browser walk was killed by me after it hung (see below), so check.sh printed FAIL for that step only |
| walk.sh --widths (rerun, 01:27-01:38) | 0 at J2a's run | NOT VERIFIED. Seed 1 (plain, 390) finished: 11 violations, 8 "expected a not-found screen" (6 on `#/source/<game>/abc/pts`, which the router sends straight to not-found; no code of mine is on that path), 2 "blank page", 1 Learn price word "edge" from the Detail-level help text in `pages/Settings.tsx` (not my file) seen during a rapid navigation; the same kinds J2a logged under load. Seed 2 then hung at 0% CPU; both runs hung while other agents' walks held the same fixed devtools port (9362, hard-coded in `route-walk.mjs`), so seeds 2, 3 (360/768/1180) never ran. My one rerun was used; I stopped my own processes only. |

## Requests for shared components (not edited; worked around in `pages-a.css`)
1. `Card variant="feature"`: its gradient's middle stop (#8666c7) gives 3.4:1 for muted small text and about 4.1:1 even for
   full ink, so no card holding small text can use it. Ask: a darker centre stop, or use it only for large headline text.
2. `Table`: an optional stacked layout below a width (cells labelled by their column head) would remove the need for the
   duplicate card list on `#/source`.
3. `jarvis.css` `.today-head ~ nav.tabs` no longer matches (the nights row now sits in `.disc-toolbar`); the same look is
   in `pages-a.css`. The rule can go.

## Decisions (what / why / what would reverse it)
1. Closest look uses `card-header`, not `card-feature`. Why: AA (above). Reverse: change one class in `DiscoverParts.tsx`.
2. Discover Market order: verdict and closest look before Today's Picks; nights + lens toolbar first. Why: the answer and
   the navigation belong above the detail. Reverse: move two JSX lines in `Today.tsx`.
3. Selected night scrolled into view with `scrollLeft` on the tab row. Why: the newest (default) night was hidden at every
   width. Reverse: delete the effect in `Today.tsx`.
4. Snapshot cards on phones are a second render of the same rows, hidden by CSS above 600 px (the table below). Why:
   `Table` has no stacked mode and is shared. Reverse: delete `SnapCards` and two CSS rules.
5. Model stamp shown verbatim (mono), not through `stamp()`: the stored value is a description, not a time.
6. Game stat cards go plain on phones (no card padding). Why: card padding pushed the distribution bar's SVG labels under
   12 px. Reverse: drop the phone media rule.
7. "Make more picks" is the one primary action on My picks (it links to the existing `#/swipe`).

## Not done
- Discover above its heading, every other page (other lane). Knows, TeammateSits, RoleCards, AltLine, CardDetail, Explore,
  Viz, TopParlaysPanel and VerdictStrip were looked at in place but not restyled.
- Discover Analyst in the Learn lens shows "book 51%" beside posted-line key players (`KeyPropsFull`, pre-existing, not a
  learn-dom price word); worth a look by whoever owns the lens rules.
- A clean base measurement of bundle sizes.

## Not verified
- The browser route walk (above): run `WALK_API_PORT=8096 WALK_WEB_PORT=5296 app/scripts/walk.sh --widths` when no other
  agent is walking. Request: `route-walk.mjs` should pick a free devtools port (it collides across agents today).
- Headless Chrome only; no real phone, Safari or Firefox; no screen reader. The artifact build was built and checked, not
  screenshotted. Picks and Swipe were seen only on fixtures (the real export has no open deck and no seeded picks).
  Reduced motion: nothing new animates.
