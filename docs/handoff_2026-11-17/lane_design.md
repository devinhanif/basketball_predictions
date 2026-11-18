# Lane C1: design system, first load and UX shell (2026-11-16 evening, Central)

Branch `agent/app-1117-design`, worktree `nba-wt-1117-design`. Edits only under `app/web/` plus this file. Started 20:02 CST,
report written 21:01 CST (times from `date`). Nothing pushed, nothing merged. `app/api/` and `app/error_codes.json` untouched.

## How to check it yourself

```
cd app/web && npm ci
mkdir -p artifact-data && cp <a baked.json> artifact-data/
npm run check && npm run build:artifact          # must end "checks passed"
# browser (two servers): API with fixtures (+ the shared desk fixture, to see edge badges)
cd app/api && APP_DESK_SHARED_FILE=$PWD/../fixtures/desk_fixture_shared.json APP_LIVE_DIR=$PWD/../fixtures/live \
  uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8050
cd app/web && API_ORIGIN=http://127.0.0.1:8050 npx vite --host 127.0.0.1 --port 5197
npm run walk -- http://127.0.0.1:5197/ --width 360        # also 768, 1180, --inject, --garbage
node scripts/shots.mjs regular http://127.0.0.1:5197 <outdir>   # LENS=market, ONLY=a,b and W=390 are optional env
```

## What was built (commits, oldest first)

| # | Item | Commit |
|---|---|---|
| 1 | Tokens: spacing scale, type scale (12 px floor), radius/pill, scrims, density knob (`--pad`, `--row-gap`, `data-density`), semantic roles: `--model` (purple), `--mine` (gold), `--base` (grey), better/worse/same, four edge-label pairs, light and dark. Font sizes, gaps, radii and both stray hex colours in the other sheets moved onto tokens. | `a6524578` |
| 3 | One formatter in `format.ts` (extended, not forked): `percent`, `decimal`, `delta`, `centsText`, `deltaCents`, `count`, `nText`, `odds` (American/decimal), `priceText` (follows the pref); missing values show `—`, never 0; no negative zero; real minus U+2212. `numbers.test.ts` covers null/undefined/NaN/Infinity/-0/negatives. Prefs gain `odds` (default american) and `density` (default comfortable). | `a6524578`, `e4163ecc` |
| 2 | `src/ui/`: Button, Card, Tile (label, value, unit, n, note), Badge/EdgeBadge/CompareBadge/SideBadge, Dialog (focus trap, Esc, focus returns, `aria-modal`, labelled), Table (caption, count, `aria-sort` headers), Tabs/TabPanel (roving tabindex, arrows, Home/End), Banner, Skeleton, ChartFrame (title, takeaway, chart, "Show the numbers" table), SoWhat. Pure logic in `ui/logic.ts`, tested. Styles in `src/kit.css` (imported by both entries; parity test passes). Migrated: Shell's Modal (all modals: search, about, shortcuts) now the Dialog; Game team tabs now Tabs; the shared Loading state uses Skeleton; intro uses Button. | `45205b98`, `298531a3` |
| 4 | Edge labels: one mapping (`ui/semantic.ts`) for arbitrage/strong/thin/none: colour + icon + word, meaning in `title`, link to its Key terms anchor. Meanings taken from `nba/jarvis/edge.py classify` and `configs/jarvis.yaml strong_cents: 3.0`. Used on Discover's radar and Model History's ledger. Better/worse/same and over/under badges exist (icon + word) but have no call site yet. | `b8c2fe8e` |
| 5 | States: Skeleton in the shared Loading; the existing Problem screen for failures (checked with the API down); Key terms has a designed empty search state. | `298531a3` |
| 6 | Shell: persistent data line from `/api/meta` (`metaWatch.ts`): "Data as of Nov 16, 7:35 PM CT, 2 h ago" or "A replay of real 2024-25 games, not tonight's slate (fixtures built ...)"; the artifact says it is a saved snapshot. Newer-data notice with a Reload button when `data_version` changes (poll every 5 min while visible and on tab focus, ignored when the answer is the service worker's saved copy; never in the artifact). So-what line on Game, Leg, Record, Live, Parlay (Discover's existing headline kept). Non-blocking five-point intro replaces the blocking Welcome and the auto-opening tour. Side rail already shows from 1024 px; bottom bar on phones unchanged. Fixed: the static "Loading parlayhitters..." placeholder stayed in the DOM under every page. | `45205b98`, `298531a3` |
| 7 | Key terms `#/terms` (validated `t` anchor and `q` search in the hash, artifact `#terms`), in nav, palette and walker. 26 terms (17 in Learn/Explore), searchable, each with an anchor. | `45205b98` |
| 8 | Remembered: lens (existing), odds format, density. In the hash: Key terms anchor and search (search via `replaceState`, no scroll jump); Track record tab and night were already there. | `a6524578`, `e4163ecc`, `45205b98` |
| 9 | Cmd/Ctrl-K palette now also lists Live, Track record, Parlay of the day, Key terms and every term (lens-filtered); `?` opens the shortcut sheet (not while typing or inside a dialog; also in the profile menu); focus trap in all dialogs; `prefers-reduced-motion` global rule; 44 px on phones for the new controls. | `45205b98` |
| - | Screenshot fixes: "model only" tag clipped in narrow leg tables; long names broken letter by letter in Discover's one-line props in the Market lens at 390 px. | `111fbad1` |

## Skipped, and why
- Item 10 (typed route table): budget; behaviour-preserving refactor of six places is better done fresh.
- Hash state for Game's team tab and Discover's radar filter: Game's `Route` shape is asserted with `toEqual` in `routes.test.ts`/`format.test.ts`; adding a field means changing those assertions. Left.
- Card, Tile, Table, ChartFrame, CompareBadge, SideBadge have no call sites yet: no like-for-like swap was cheap and safe tonight (Discover/Record tiles and tables carry page-specific classes the audit and walker read). They are tested only through their pure logic.
- Existing helpers `signed`, `signedCents`, `american`, `usd`, `pct` keep their ASCII hyphen so no existing assertion changes; the visible change is posted prices now render via `priceText` with U+2212.
- Scattered `toFixed` in History, Knows, Explore, Record, picksLogic were not swept (they feed tested strings).

## Numbers (before -> after)

| What | Before | After |
|---|---|---|
| vitest | 370 / 370 (37 files) | 392 / 392 (39 files) |
| Regular build js / css | 334.83 kB (gz 104.92) / 67.98 kB (gz 13.96) | 354.18 kB (gz 111.87) / 73.86 kB (gz 15.21) |
| Artifact html | 5,535,428 B | 5,557,845 B |
| Raw hex colours in CSS outside `theme.css` | 2 | 0 (tsx: 31, all team colours in `teams.ts`, unchanged) |
| Font sizes below 12 px in CSS | 2 (`0.7rem`, the `.cal-tag`) | 0 |
| Walker 360 / 768 / 1180, plain, seed 1, 150 steps (fixtures API, no desk file, as lane B ran it) | 0 / 0 / 0 (lane B) | 0 / 0 / 0 (run 20:46-21:00 on `111fbad1`) |
| Walker injected, seed 2, at 360 / 768 / 1180 (3 visitors x 25 steps each) | 0 (lane B, 390) | 0 / 0 / 0 |
| Walker wrong-shape (seed 3) / artifact (seed 7, 90 steps) | 0 / 0 (lane B) | 0 / 0 |
| Walker with the shared desk fixture served (`APP_DESK_SHARED_FILE`), 360/768/1180 | not run | 5 plain / 3 injected each, all "LINE_CAL lock" heuristic: see open finding 1 |
| Contrast/size audit (`shots.mjs`, 112 screens, 390 and 1440, light and dark, Learn lens) | not run by me before | 0 findings after `111fbad1` (2 clipped-tag findings fixed); Market lens Discover + History 16 screens: 4 findings (letter-broken names) fixed, then 0 |
| learn-dom | pass (lane B) | pass ("Learn lens: no price words on any route"); the walker now also checks `#/terms` under the Learn rule |

Screenshots: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`
(`shots-final/` all screens; `shots-market*/` Market lens with the shared desk fixture; `shots-down/` API stopped; `shots-leg/`). Looked at:
Discover (welcome, Learn and Market), Game, Leg, Record, Key terms, shortcut sheet, not-found, failure, Model History (Market).

## Tests whose assertions changed
- `routes.test.ts`: "terms" added to the known-route set (the test would otherwise fail on the new nav link); new assertions for `#/terms` parsing and two not-found cases. No assertion removed or loosened.
- New: `numbers.test.ts` (formatters, prefs defaults), `kit.test.ts` (focus trap, roving tabs, table sort, edge mapping, Key terms Learn rule with the learn-dom regexes, intro has five points and no price words and no `aria-modal`, data line text, version change, age words).

## Decisions (what / why / what would reverse it)
- First visit is a dismissible intro card at the top of the page, in both builds; the tour opens only on request (intro button, profile menu, About). The artifact's two-step "Who is looking?" / Simple-or-Analyst modal is gone: identity is one line in the card, the mode toggle stays in the profile menu. Why: the brief asks for an intro that does not block and one onboarding, not three. Reverse: restore `Welcome.tsx` from `a6524578` and the auto-open effect in `App.tsx`.
- The data line gives the as-of time in Central ("CT"); tip times stay in ET as the pages say. Why: the brief (Central for the viewer where the page does not say ET). Reverse: drop `CT_ZONE` in `metaWatch.ts`.
- Key terms sits inside the Learn promise: Learn and Explore list 17 terms with no price words; the 9 market terms (CLV, de-vig, edge after fees, DNP rule, the four labels, Market lens) show only in the Market lens, and a followed link to one says so. Reverse: none needed; or move the page out of `LEARN_CHECKED` if Devin wants price terms visible in Learn.
- Edge-label wording hard-codes "3 cents" from `configs/jarvis.yaml`. Why: the app may not import nba; the export carries no threshold. Reverse: export `strong_cents` and read it.
- A null stored `edge_label` shows as "None" (what `labelText` showed); any other unknown label (e.g. `arb_candidate`) shows verbatim in grey with an info icon, never mapped. Reverse: a product call on `arb_candidate` wording.
- Odds-format choice appears in Preferences only in the Market lens (it is about prices); default American, which is what the app showed. Missing posted prices now print `—` instead of `+0`/`0` in Explore.
- Commits are grouped by theme but the second (`45205b98`) carries several shell items together, because they share files (Shell, App, main); each commit passed the full gate.

## Open findings for the next lane
1. **Walker "LINE_CAL lock" hits only when a desk file is served.** With `APP_DESK_SHARED_FILE=app/fixtures/desk_fixture_shared.json`, Discover in the Market lens shows the desk's dates (Nov 3-8, 2026, inside the lock window) on the same page as a finished 2024-11-20 replay game with its win chance, and the walker's text rule (`route-walk.mjs:291`: any locked date + "Final" + a percentage, no lock words) fires. The chance shown is for the 2024 game, so I read it as a heuristic hit, not a lock breach, but I did not prove that on the base commit: an attempt to serve the base from a `git archive` copy rendered blank pages and was abandoned. My only change on that page is the radar status cell (diff shown in the commit `b8c2fe8e`). Lane B's and my 0-violation numbers are without a desk file. Needs a look by whoever owns the lock rule.
2. One plain walk at 360 with the desk served also logged one "blank page" on `#/live/401909955?date=2026-11-09` (pre-game live view); it did not recur in the six walks without the desk. Not investigated.
3. With the API process down, the dev proxy answers 500 and the screen says "Something went wrong on our side" (`internal_error`) while the data line says "not answering". A proxy 502/500 with no body should map to `server_unreachable` (registry/API lane).
4. `ui/` Card, Tile, Table, ChartFrame have no call sites: migrate Discover tiles, the Calibration table and History's ledger table next (they would gain `aria-sort`, counts and a text alternative for the charts).
5. Route table (lane D proposal) still open; the palette's screen list in `shellData.ts` is now one more place it would remove.
6. Leg's so-what is wording only (no number): the page sits under the LINE_CAL lock rules and I did not want a new place that could print a model chance on a locked night.
7. `Explain`/`<details>` summaries and the Live list's polling were not changed (lane B finding 8 stands).
8. Many `toFixed` calls remain in History, Knows, Explore, Record and picksLogic.

## Not verified
- No real phone, Safari or Firefox; headless Chrome only. Screen-reader output not listened to. Keyboard: the focus trap, roving tabs and `?` were tested as pure logic and seen in screenshots, not driven key by key in a browser.
- The newer-data notice was not seen firing in a browser (would need the export's `data_version` to change under a running page); covered by `versionChanged` and `metaText` tests only.
- Density "compact" and decimal odds were not screenshotted.
- Artifact screenshots were not taken (artifact walker only).
