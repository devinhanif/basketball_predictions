# Lane J2b-B: Jarvis restyle of Track record, Scorecard, Forward tests, Live, Parlay and the desk/help pages

Branch `agent/app-jarvis-pages-b`, worktree `nba-wt-1117-jv-pagesB`, base `2d89cf8e`. Started 00:53 CST 2026-11-17, final checks finished 01:49 CST (both from `date`).
Edits only in the files the lane owns plus this report. Nothing pushed, no merge, no new dependency, no font or image.
Presentation and order only: no new field, no new arithmetic, no route, URL, query or API change.

## How to check it yourself
```
cd app/web && npm ci && mkdir -p artifact-data && cp <a baked.json> artifact-data/
npm run check && npm run build:artifact && node scripts/flag-parity.mjs        # "checks passed", "flag parity passed"
cd ../.. && WALK_API_PORT=8097 WALK_WEB_PORT=5297 app/scripts/check.sh --browser
WALK_API_PORT=8097 WALK_WEB_PORT=5297 app/scripts/walk.sh --widths
```
Screens: real data `APP_EXPORT_DIR=<frozen export> uv run uvicorn parlayhitters_api.main:app --app-dir src --port 8110` +
`API_ORIGIN=http://127.0.0.1:8110 npx vite --port 5250`; fixtures (live games, synthetic desk) on 8111/5251 with
`APP_LIVE_DIR=app/fixtures/live APP_DESK_SHARED_FILE=app/fixtures/desk_fixture_shared.json`. Live list: `#/live?date=2026-11-09`.

## Per page
All screenshot directories are under `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`
(before: `pb-before/`, 375 and 1280 dark Simple). After: 375/768/1280 x dark/light x Simple/Analyst unless noted.

| Page | What was weak | What changed | Commit | Shots |
|---|---|---|---|---|
| Scorecard | four stacked text panels; method before the numbers; 7-column tables clipped and scrolled sideways at 375 with the numbers off screen; lens switch a full-width bar | `PageHead` (kicker, title, so-what, lens switch on the right); order: plain-sentence card, shown table, counts-only table (sticky header in its own frame), "what we cannot show yet" as name / needs rows, method last (Simple: one "How this is measured" disclosure; Analyst: a Method card with four tiles from `score_rule` and `seasons`/`as_of`, method and population text open); phones: each table row becomes a labelled stacked row, header row kept as sort buttons; interval under its mean | `042bcbc9` | `pb-sc1/`, `pb-sc2/` |
| Forward tests | each test a tall run of dt/dd; rule path in the main flow; note panel a text wall | one card per test, the same four rows (Reads, n needed, n so far, Outcome) in the same place, "Outcome hidden until the look" badge in the card head, rule path behind a disclosure (Simple) or at the card foot (Analyst); cards two across from 1100 px; the list note as a card with "How this is measured" | `f2fda598` | `pb-sc1/`, `pb-sc2/` |
| Track record (Replay, Calibration, Guess) | h1 + two lines; game cards full width with lots of air; calibration "tabs" were buttons in a group; method lines everywhere | `PageHead` with the keep-your-money sentence bold and links to Scorecard / Forward tests; night picker card with a "Locked test night" badge beside `LOCK_NOTE`; replay games two across from 1100 px, an opened game spans both columns with its players two across; Calibration uses `Tabs`/`TabPanel`; ledger id / rule / log-loss lines / series notes behind "How this is measured" in Simple, in place in Analyst; win-model name only in Analyst; empty and loading states via `StateBlock` | `d09b12a3` | `pb-rec1/` |
| Live (list, final game, box, trackers, unknown game) | h1 inside a panel; cards two narrow columns; feed error a yellow strip; live views were `role=tab` without a panel; box headers 11.5 px | `PageHead`; sections with `SectionHead`; cards as tiles in an auto-fill grid; empty / feed error / reconnecting as `StateBlock`; live views through `Tabs` + `TabPanel`; score head as the header card; box headers at 12 px | `d200db12` | `pb-live0/` (before), `pb-live1/` |
| Parlay | three stacked h1-sized blocks in Learn; no page head; details only behind a disclosure | `PageHead`; Learn: the two Market-screen states side by side from 1000 px; "three ways a day can go" as three badged tiles (Trusted / Unproven / Keep your money, icon + word); Market: verdict as the header card (the purple feature card failed AA in light, so not used), exact details laid out in Analyst; diagnostics with `SectionHead`, Analyst count tiles, sticky table, labelled stacked rows on phones | `53016c1c` | `pb-par1/`, `pb-par1m/` (Market), `pb-par2/` |
| Key terms | lens switch full-width; one long column | `PageHead` with the lens switch; search and count on one toolbar row; terms two across as tiles from 1000 px | `67744378` (also carries the Preferences CSS) | `pb-tp1/` |
| Preferences | first card had no title; account avatar stacked | `PageHead` ("Make it yours"); a kicker and title on every card; account as a tile strip | `40bdef9f` | `pb-tp1/` |
| Model History | review figures read like live data | `PageHead`; the 2026-11-08 review text sits in a dashed amber callout with a "Fixed review text, not live data" badge (book icon + words); tiles one across under 520 px | `a863659b` | `pb-desk1/`, `pb-desk3/` (review branch, scratch copy of the desk fixture) |
| Signals | headline + tabs inside one header | `PageHead`, night tabs below it, empty desk via `StateBlock` | `38b41aa4` | `pb-desk1/` |
| Alerts | header built from rows | `PageHead` with "Test an alert" as its action | `95f7147b` | `pb-desk1/` |
| Hedge Finder | header built by hand (picker and lab) | `PageHead` in both | `f6004cdc` | `pb-desk1/` |
| Admin | tabs inside the header; owner-only state a bare panel | `PageHead`, tabs below, owner-only state via `StateBlock` | `f3a5b11d` | `pb-desk1/` |
| Desk panels (`DeskUI`) | no-desk / held-back state bare | `PageHead` + `StateBlock` (empty; held-back as partial) | `da0981d1` | `pb-desk2/` |
| Error help | three bold-label paragraphs per code, one column | `PageHead`, search toolbar, each code a card with aligned What happened / Why / What to do rows, two across from 1100 px | `a8b30d7d` | `pb-err1/` |
| Not-found / failure screen (`Problem`) | title and text undifferentiated | warning mark beside the title, plain sentence larger, code line set apart, the way on is the primary button; codes and text unchanged | `6d362edb` | `pb-err1/` |

## Simple and Analyst now
| Page | Simple | Analyst |
|---|---|---|
| Scorecard | sentences, both tables, cannot-show list; rule, counts, method and population behind one disclosure | the same plus a Method card: rule, T-60 filter, limitation, lock text, tiles (rows read, left out late, kept with tip-off unknown, seasons with as-of), method and population open |
| Forward tests | four rows per test; rule path and the count rule behind disclosures | rule path at each card's foot; count rule and list source open |
| Track record | headline per card; ledger id, rule hash, log-loss line, series notes and excluded counts behind "How this is measured"; no model name on replay cards | all of those in place; game-winner model name on each replay card |
| Parlay | engine card with "Show the exact details" closed; diagnostics sentence | exact details laid out; diagnostics count tiles |
| Live, Terms, Preferences, desk pages, help | no difference (as before) | no difference |
Nothing in Analyst needs a field the page did not already receive.

| Failure screen fix | the new grid let a long unbreakable address widen the page by 158 px at 390 (route walk, hostile Hedge link) | grid column `minmax(0, 1fr)`, children wrap anywhere | `69a302ad` | `pb-hh/` (360, 390) |

## Numbers (before = base `2d89cf8e`, after = `69a302ad`)
| What | Before | After |
|---|---|---|
| vitest | 508 / 508 (48 files) | 508 / 508 (48 files); no test changed, weakened or removed |
| Regular build js / css | not rebuilt at base; lane J2a reported 400.48 kB / 100.89 kB at `5d2b4ee8` | 407.79 kB (gz 127.78) / 109.63 kB (gz 22.18) |
| Artifact html | J2a reported 4,554,917 B | 4,570,973 B, "checks passed"; flag parity passed |
| Raw hex colours in my files | 0 | 0 |
| `px` values in my files | 278 (desk.css 204, live.css 42, honesty.css 32) | 297: live.css 33 (9 one-off sizes moved to tokens, box header 0.72rem -> --fs-xs), desk.css and honesty.css unchanged, new pages-b.css 28 = 13 one-pixel hairlines, 3 44 px touch targets, 12 media-query breakpoints; no one-off spacing or font size |
| check.sh --browser (walker plain 390 seed 1 / injected 390 seed 2) | 0 / 0 (J2a) | first run 2 / 3 (two real: hostile Hedge link widened the page 158 px through my failure-screen grid, fixed in `69a302ad`; one blank page + one not-found under load); after the fix 0 / 0, ALL CHECKS PASSED (01:32-01:37) |
| walk.sh --widths 360 / 768 / 1180 (seed 3) | 0 / 0 / 0 (J2a) | 0 / 0 / 0. Its leading seed-1 390 run logged 20 under load (another agent's walk on 8094 ran at the same time, load average 5.3): 11 "expected a not-found screen", 8 "blank page", 1 Learn price word on `#/source` (not my page); the same seed gave 0 in check.sh minutes earlier. Rerun once alone (01:43-01:49): 0 / 0 / 0 / 0 / 0 (390 plain, 390 injected, 360, 768, 1180), exit 0. |
| learn-dom | pass (J2a) | pass: "Learn lens: no price words on any route" (01:31) |
| Contrast / size audit (`audit-dom.js` inside my screenshot runs) | Live box headers 11.5 px (11 findings per box-score screen) | 327 screens, 0 findings, both themes, both modes, 375/768/1280 (Live/desk on fixtures, the rest on the frozen real export, Parlay in Learn and Market). One interim finding fixed before commit: the purple feature card behind Parlay's verdict failed AA in light (3.4:1), so it uses the header card |

## Requests for shared components (not changed by me)
1. `shellData.ts` (`useSearchIndex`): the search palette lists Market-only screens (Signals, Hedge Finder, Model History, Alerts and the parlay screens) in the Learn lens. Fix: filter `SCREEN_ITEMS` by lens the way `termsFor(market)` already filters terms (and by the same rule `screenAllowed` uses for "Opens on").
2. `Lens.tsx` `MarketOnly`: it always renders an `h1` with the fixed id `mo-h`; on Parlay (Learn) two of them sit under the page's own h1 (duplicate id, three h1s, pre-existing). Ask: a `level` prop and an id from the name.
3. `jarvis.css` `.panel.card-feature`: white text on the light-theme gradient measures 3.4-4.1:1 where the gradient is lightest; fine for a short hero, not for paragraphs. Darken the light-theme stops or keep it to one short line.
4. `ui/Table`: a `stack` option (phone rows as labelled blocks) would replace my per-table `::before` labels in pages-b.css (`.sc-shown`, `.sc-hidden`, `.pd-table`), which must be kept in step with the column lists by hand.

## Decisions (what / why / what would reverse it)
1. One stylesheet `pages-b.css`, imported right after `jarvis.css` in both entries; all restyle rules for my pages live there except live.css fixes. Why: one import to drop reverts the look. Reverse: remove the two import lines.
2. Simple hides method lines behind one "How this is measured" disclosure; Analyst shows them in place. Every number stays reachable in Simple. Reverse: render the disclosure contents inline in both modes.
3. Replay cards show the game-winner model name only in Analyst. Why: jargon in Simple. Reverse: drop the `analyst ?` guard in `ReplayGame`.
4. Scorecard order follows the brief: sentence, shown table, counts-only table, cannot-show list, method last. Reverse: move the `Method` block up.
5. Phone tables (Scorecard, parlay diagnostics) become labelled stacked rows under 640 px, sort buttons kept. Why: the scroll frame hid every number at 375. Reverse: delete the `@media (max-width: 640px)` block for `.sc-table`.
6. Parlay verdict uses the header card, not the purple feature card (contrast). Reverse: `card-header` -> `card-feature` in `Parlay.tsx` once the light gradient passes AA.
7. Added links to Scorecard and Forward tests in the Track record heading, and kickers on cards (presentation only; existing routes).
8. The Preferences CSS landed in the Key terms commit (`67744378`) because it was appended before that commit; the Preferences commit carries only `Settings.tsx`.

## Not done
- No visual-regression tests (the screenshot tooling stays manual).
- Live "feed stopped" state not photographed (needs a live game whose capture stalls; the fixtures have one final and one pre-game game); its markup (`ProblemPanel live_feed_stopped`) is unchanged apart from the shared failure-screen layout. The in-play LIVE card and trackers with lines were not seen either (no lines for the fixture game).
- Hedge Finder, Alerts, Signals, Admin got the page head and states only; their bodies (desk.css) were already in the donor style and were left as they were.
- Model History's fixed-text callout was seen only with a scratch copy of the desk fixture whose `clv_summary` was set to n = 83, mean -0.8795 (`scratchpad/pb-desk-review83.json`, never in the repo); the real desk is not in the frozen export.
- Analyst shows no model names on Live, Terms or the desk pages: those pages receive no such field beyond what they already print.

## Not verified
- Headless Chrome only; no real phone, Safari, Firefox or screen reader. Reduced motion covered by the existing global rule plus one rule in live.css; not toggled in a browser.
- Artifact build checked by its script, not photographed.
- Desk money fields in a private session: the privacy guard code was not touched; not exercised (fixtures are the shared cut).
