# Lane B: web client audit, harness and hardening (2026-11-16 evening, Central)

Branch `agent/app-1117-web`, worktree `nba-wt-1117-web`. Edits only under `app/web/` plus this file. Started 18:59 CST,
report written 19:56 CST (times from `date`). Nothing pushed, nothing merged.

## How to run it yourself

```
cd app/web && npm ci
npm run check              # eslint, tsc, vitest, build
mkdir -p artifact-data && cp <a baked.json> artifact-data/ && npm run build:artifact   # must end "checks passed"
# browser walker (needs Chrome and two servers; not part of check):
cd app/api && uv sync && APP_LIVE_DIR=$PWD/../fixtures/live uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8020
cd app/web && API_ORIGIN=http://127.0.0.1:8020 npx vite --host 127.0.0.1 --port 5190
npm run walk -- http://127.0.0.1:5190/ --seed 1 --visitors 6 --steps 25            # plain
npm run walk -- http://127.0.0.1:5190/ --seed 2 --visitors 6 --steps 25 --inject   # API down, 503 (both shapes), mid-503, slow, killed stream
npm run walk -- http://127.0.0.1:5190/ --seed 3 --visitors 3 --steps 20 --garbage  # 200 answers of the wrong shape
npm run walk -- file://$PWD/dist-artifact/preview.html --set artifact --seed 7 --visitors 3 --steps 30
npm run walk -- http://127.0.0.1:5190/ --snapshot before.json                     # DOM text of every route, for refactors
```

`APP_LIVE_DIR` matters: without it the live routes have no fixture games.

## Numbers (before -> after)

| What | Before | After |
|---|---|---|
| vitest | 319 / 319 | 370 / 370 (29 -> 39 files) |
| Hostile hashes (5,000 seeded, `router.hostile.test.ts`) | 317 invalid routes, 9 of them thrown (URIError) | 0 |
| Walker, plain, seed 1, 150 steps @390 | 35 violations: 20 bad links rendered Discover, 8 uncaught exceptions (URIError, RangeError), 5 Learn-lens price words, 1 blank page, 1 sideways scroll | 0 |
| Walker, injected, seed 2, 150 steps (55 under a fault) | 20 violations: 10 not-found, 5 exceptions, 3 blank pages, 1 sideways scroll, 1 refusal without reason* | 0 |
| Walker, wrong-shape answers, seed 3, 60 steps | 15: 13 uncaught exceptions, 1 blank page, 1 Learn word | 0 (needed a second fix, ed9df299) |
| Walker @360 / @768 / @1180, seed 5, 75 steps each | 15 each (Learn lens on `#/parlay`) | 0 each |
| Walker on the artifact (real export, all nights locked), 90 steps | not run before; probe showed Track record 46 px too wide @390 | 0; replay lock wording checked on 90 steps |
| `404 /api/desk` / `404 /api/top-parlays` in the seed-1 walk | 28 / 47 | 6 / 13 (one per fresh visitor) |
| Regular build js / css | 321.01 kB (gz 100.23) / 67.97 kB | 334.87 kB (gz 104.93) / 67.98 kB |
| Artifact html | 5,510,880 B | 5,535,461 B (+ modes.css, live.css, registry) |
| Longest files | Leg.tsx 999, Record.tsx 665 | Leg.tsx 237, LegLine 249, LegHistory 270, LegContext 267; Record.tsx 448, RecordGuess 229 |

\* The walker's rules were refined during the night (failure panels judged by their registry `data-code`; inline
`<script>` text ignored; the lock heuristic narrowed after two false positives). Before-numbers were taken with the
earlier rules; I re-read each before-violation and all but the "*" one are real defects under the final rules.
Split proof: the walker's DOM-text snapshot of all routes in Market and Learn lenses (80 pages) was taken twice before
the split to find nondeterministic pages (swipe decks, guess deck, live pre-game age: 10); the other 70 pages, 12 of them
leg and record pages, were byte-identical after the split. A script also confirmed the moved code is line-for-line the
original (only `export` and imports added).

## Commits by theme

- Harness: `090f7498` walker with fault injection, `ec672cb7`, `de28ee70` walker refinements; hostile-hash test in `14f08413`.
- (i) routes: `14f08413` validated params, explicit `notfound` route, designed not-found screen (`Problem.tsx`), registry import (`errorCodes.ts`).
- (ii) failures: `b454f651` one typed `ApiFailure` (new `{ok,code,text,field,request_id,detail}` body and the old `{detail}`, plus `X-Request-Id`), one `ProblemPanel` used by every `View`/`ErrorPanel`, banner says why when the export is refused, empty-night state.
- (vi) boundary: `5faf7288` outer and per-page error boundary with a local reference id (`w-...`), Try again, Back to Discover; `ed9df299` leg wrong-shape answer.
- (iii) time: `896de95c` `src/time.ts` owns every conversion; `time.test.ts` (tip, midnight, both DST changes, naive UTC) plus a test that no other file builds a zoned formatter or calls `Date.parse`.
- Learn lens: `90bb658c` Discover's morning picks, `e5ecf712` parlay engine card.
- (iv) live: `cb4a3252`.
- Rule 4: `2f3e9882` + `0094a7d2` (the first commit of the pair was red: a test regex missed the `&&` form; my command chain committed after the artifact build passed although `check` had failed. Fixed in the next commit; from then on every commit went through a gate that stops on either failure).
- (vii) optional 404s: `610f9052`. (viii) overflow: `4b20477f`. (ix) splits: `4204672e`, `a2d8ca0d`. Router stat list: `80c164fc`.
- (v) raw HTML: nothing to fix; no `dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML`, `eval` or data-built `javascript:` URL exists. Every data-built href starts with a literal `#/`; avatar `src` comes from the platform identity or a cdn.nba.com URL built from a numeric id.

## Ranked audit findings (file:line on the branch head; "fixed" names the commit)

1. **Learn lens showed price words** on Discover (`TodaysPicks`, "break-even", Kalshi, fees) and on `#/parlay` (engine card). learn-dom only checks `#/parlay?date=...`, so it missed the second. Fixed `90bb658c`, `e5ecf712`; learn-dom passes on the head (run 19:5x).
2. **Blank page / uncaught exceptions from hand-edited links**: `#/?date=garbage` threw RangeError in `longDate` and blanked `<main>`; `#/game/%E0%A4%A` threw URIError in `parse`. Fixed (i) + (vi).
3. **Service worker hides a refused export behind the saved copy** (`public/sw.js:74`: any 5xx, 503 included, answers from cache; `Shell.tsx:492` then says "You are offline, or the data server is not answering"). Honest copy would say the data failed its checks, with the reason and the copy's age. NOT fixed: whether a refused export may show yesterday's data at all is a product call (Devin). Suggested: keep the saved copy, add `X-Upstream-Status: 503` and the reason in the worker, and let the banner say so.
4. **Naive stored times were read as local time** (JavaScript parses `2026-10-11T23:20:10.735352` as the viewer's zone). Affected `stamp(p.as_of)` on the parlay page (fixture `as_of` is naive), top-parlays `made_at` stale/after-tip checks, ledger and Knows ordering. Fixed `896de95c`.
5. **Artifact lacked `modes.css` and `live.css`** (`main.artifact.tsx`), so Track record and live styles were missing; caused the 390 px sideways scroll. Fixed `4b20477f` with a parity test.
6. **Desk money guarded only by the API** (`History.tsx:59`, `:186` rendered balance and daily P&L whenever present). Now also checks `privacy === "private"`; test `privacy.test.ts`.
7. **Client-side numbers that are not stored fields** (data honesty; NOT changed, listed for review):
   - `desk.ts:274` Wilson interval on the desk record (not date-clustered, unlike CLAUDE.md's claim standard).
   - `desk.ts:409` seeded date-clustered bootstrap of the desk win rate, shown at `History.tsx:54`.
   - `CardDetail.tsx:62` Wilson interval on a card's hit rate.
   - `leg.ts:103` / `stats.ts:20` no-vig chance from two stored prices (comment says the API does the same; it is still computed here).
   - `Explore.tsx:256-282` / `whatif.ts:32` minutes what-if reshapes the stored distribution (an explicit what-if, labelled as such).
   - `ledger.ts:178`, `record.ts:72` Wilson on the visitor's own picks and guesses (the visitor's data; fine).
   - `hedge.ts`, `Explore.tsx:130` arithmetic on numbers the visitor types (fine).
   - `History.tsx:39-41` hard-coded review numbers (-0.19c, 95% -0.74c to +0.15c, n = 83) shown when the desk's CLV snapshot is n = 83 and -0.8795c: a number on screen that is not a stored field.
   - `Today.tsx:201` "played" if tip was over 3.5 h ago; `live.ts:358` treats a game as over 4 h after tip: two different constants for one idea.
   I did not audit every number for its n and support line; spot checks on History, Discover tiles, leg and role cards all carried n.
8. **Live list polling has no backoff** (`useLiveData.ts:18-45`): every 5 s on Live, 20 s on Discover (`Today.tsx:203`), errors included, for any night (also 2024 fixture nights). Not changed.
9. **Dialogs have Escape and focus in/out but no Tab trap** (`Shell.tsx:56-70` Modal, `Tour.tsx:52-63`; Welcome has neither Escape nor trap). Not changed (design lane).
10. **Two tabs share localStorage with no `storage` listener**: prefs and the paper ledger are last-writer-wins across tabs (`prefs.ts:28-41`, `ledger.ts:35-70`). Not changed.
11. **Text under 12 px**: `.cal-tag` "model only" at 11.2 px (`styles.css:1407`, also `live.css:49`). Contrast: 0 failures under 4.5:1 on not-found, bad game, Discover, game, leg and record, light and dark, @390 (`scripts/audit-dom.js`). Not changed (design lane).
12. **Hard-coded values**: `record.ts:10` lock date (mirrors `api/trackrecord.py`, by design); fixture copy "2024-25" (`App.tsx:52`, `Today.tsx` note); `sits.ts:93` evidence seasons; `types.ts:5` four stats; `format.ts` book names. `STALE_AFTER_HOURS = 30` is now the one stale constant (`time.ts`).
13. **Optional files requested every load** (desk, ticket list): now remembered absent for 10 minutes per tab. Live's own 404s (`/api/live/zzz/stream`) remain.
14. **Live stream**: backoff existed (2-30 s + 0.5 s jitter) but never gave up, did not guard `JSON.parse`, could spin on repeated gaps, and accepted messages from a replaced stream. Fixed `cb4a3252` (`MAX_FAILS = 6`, then "The live feed stopped" panel with Retry while retrying at the cap; jitter up to +25%; at most 3 quick gap repairs).
15. **Routes**: unknown hashes rendered Discover; bad ids reached the 404 copy only via the API; `hedge` took any query. Fixed (i). Unknown `tab` on Track record still opens Replay (an existing test asks for that).
16. **Performance**: one 335 kB bundle, no code splitting; long lists use "Show more" paging (Alerts, History, Signals). Not changed.

## Decisions (what / why / what would reverse it)

- Bad ids and unknown paths are a not-found screen, not Discover. Why: the task, and a silent redirect hides a broken link. Three existing assertions changed from `today` to `notfound` (`format.test.ts:42`, `routes.test.ts:19-21`); the "never blank" intent is kept and the hostile test is stricter. Reverse: Devin prefers the old redirect.
- Unknown Track record tab still opens Replay. Why: `modes.test.ts:54` asks for it; not a missing page. Reverse: none needed.
- Clock labels stay "ET" everywhere. Why: every page already says ET and NBA nights are ET dates; "ET"/"CT" are correct in both standard and daylight time. `tipTime`/`stamp` take an optional zone (`CT_ZONE`) and `zoneAbbr` gives EST/EDT/CST/CDT, so switching to Central is a one-argument change. Reverse: Devin wants Central on screen.
- The page boundary resets on route change without remounting the page (`resetKey`), so a leg's slider state survives a side toggle as before. Reverse: none.
- The reference id on a crash is made on the device (`w-<time>-<rand>`) and logged to the console; there is no server log to match it to. Reverse: a client error endpoint (Devin's call; outward-facing).
- 503 reason shown only for refusals (503 and the `*_refused` codes); a 404's `detail` is never shown as a "reason". Reverse: API lane wants details shown.
- Absent desk / ticket list remembered 10 minutes per tab (sessionStorage). Why: behaviour-preserving within a session; a file written later appears within 10 minutes or in a new tab. Reverse: files that must appear instantly.
- Discover's morning picks and the parlay engine card are hidden outside the Market lens (the page's ticket list already was). Why: hard rule, Learn shows no price words. Explore now shows the MarketOnly note for the card too, matching the note's own words ("hidden in the Learn and Explore lenses"). Reverse: Devin wants the honest "keep your money" card in Learn, reworded without price words.
- Record.tsx keeps replay and calibration; only the guess game moved. Why: `calibratedOnly.test.ts` reads Record.tsx by name for the lock and published-record checks; moving them would take code out of those tests' sight. `split.test.ts` repeats the by-name checks on the new pieces. New files live in `src/pages/` (not a subfolder) because `routes.test.ts` scans that folder non-recursively.
- Not done: the service worker refusal behaviour (finding 3), Tab traps, cross-tab sync, polling backoff, code splitting.

## Codes the web app needs that the registry lacks (`app/error_codes.json`, API lane owns it)

- `bad_answer` (web): a 200 answer of the wrong shape; today it shows `unexpected_error`.
- `too_many_requests` (429): mapped to `unexpected_error` now.
- `refused_showing_saved` (web): a refused export while the worker shows a saved copy (finding 3).
- `storage_blocked` (web): localStorage refused, picks kept for this visit only (currently silent).
- `unknown_live_event` (404): an unknown live event id currently maps to `no_live_capture`, whose words say "No live feed for that game", which is close but not the same.

## What I did not verify

- No real phone, no Safari/Firefox; headless Chrome only, at 360, 390, 768 and 1180. Keyboard-only use, screen reader output and reduced motion were not exercised (only grepped: reduced-motion rules exist in `styles.css` and `ux.css`).
- The service worker paths (offline with a saved copy) were not run: the dev server has no worker, and I did not run `vite preview` with the worker.
- The new API error body was only exercised by injection in the walker and by unit tests; the API lane's real implementation was not running here.
- The live "feed stopped" panel was verified by unit tests and the walker's killed-stream steps (no exception, no blank page), not by watching the 6-failure threshold in a browser.
- Light and dark contrast was checked on 6 routes only. The walker's LINE_CAL check is a text heuristic; on the artifact it checked the replay's lock wording (90 steps), but the artifact's Discover links only to tonight's unplayed games, so a finished locked game page was not visited.
- `npm audit` reports advisories from `npm ci`; not looked at (no dependency changes allowed).
