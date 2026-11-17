# Lane K2: the friend pass (switch OFF, real data, phone first)

Branch `agent/app-jarvis-friend` (from `5b94a4ea`). Started 05:02 CST, gate finished 05:37 CST (both from `date`).
Edits only under `app/web/src` plus this file. Nothing pushed or merged. Real data: the frozen export; the committed
fixtures through the gate.

Screenshots: `S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/k2`.
Before: `$S/b0 b1 b2` (phone, dark), `$S/d1` (1280), `$S/e1` (Explore, light), `$S/f1` (first run), `$S/dn` (service down),
`$S/mk` (Market). After: `$S/a1 a2 a3` (phone), `$S/f2` (first run), `$S/mk2` (Parlay), `$S/dn3` (down), `$S/t1440b` (1440),
`$S/t768`, `$S/l1` (light), `$S/s1`, `$S/r2`. Driver: `$S/look.mjs`.

## What a friend would trip on, ranked
| # | Trip | Status | Commit |
|---|---|---|---|
| 1 | Phone first screen: LENS + DETAIL + two-line hint ate ~190 px | Fixed (about 35 px back: 2 px seg padding, 4 px gap, hints one line; the game card starts at y 620 instead of 655 on Discover). Hints: "How the game and forecast work. Market side hidden." / "Move the line or minutes; nothing is saved." / "Posted prices, edges and fees, as stored." Applies through `PageControls` to every page with the switches. At 1100 px and up the two switches sit side by side (heading block about half as tall) | 89c5c7d9, 5ff8ccdf |
| 2 | First-run chooser filled a whole phone screen before any content (sparkle, thumbnails) | Fixed: no sparkle or thumbnails under 600 px; both choices show on the first screen. "Start in Simple" is still just below the fold | 89c5c7d9 |
| 3 | Discover (Market) asked `/api/games/<id>/result` for unfinished games: two 404s in the console | Fixed: Discover reads the stored night list first (`finished: 0`) and does not ask; without that field it falls back to the old clock rule. 0 result requests on the frozen night | 21412177 |
| 4 | Parlay (Learn/Explore): two stacked "is a Market screen" panels | Fixed: one ("The parlay card and ticket list") | 21412177 |
| 5 | Parlay (Market, Simple): file names and "export field 4 of the export-field proposal, docs/contracts/..." on screen | Fixed: the joint-chance records are Analyst detail; Simple has one line saying where they went | 900ffcae |
| 6 | Market-screen stand-ins: heading glued to text, "the optional what-if ... is the one exception" | Fixed (spacing, balanced heading, sentence dropped; the heading still ends "is a Market screen", which the visual check pins) | 89c5c7d9 |
| 7 | Game page: "Forecasts come from our standard props model (...); for some players, from our standard props model (...)" said twice | Fixed: sources that read the same in words are said once | 21412177 |
| 8 | Two h1 with the data service down (Track record, Guess, Swipe, Scorecard, Forward tests, source page) | Fixed: `View` takes `errorLevel`; failures inside a page are h2. Checked with fetch forced to fail on 20 routes: one h1 each | 7825f986 |
| 9 | Live empty: "Nothing was captured for 2026-11-17 or 2026-11-16" | Fixed: "No live feed was stored for Tue, Nov 17, 2026 or Mon, Nov 16, 2026." Attribution line no longer touches the empty card | 21412177, 89c5c7d9 |
| 10 | Alerts empty: "THE DESK ... The desk has not published its numbers yet"; no purpose line on Alerts/My picks empty states | Fixed: kicker "Analysis notes", "Nothing has been published for this screen yet", one purpose line on both | 21412177, 5ff8ccdf |
| 11 | Search: one player listed once per game (four Shai rows), dates as 2026-11-15 | Fixed: one row per player (newest game), readable dates | 21412177 |
| 12 | Discover headline wrapped with a dot opening a line at 768 | Fixed: no-break space before each dot (display only) | 5ff8ccdf |
| 13 | Leg page: switch hint touching the headline, headline touching the matchup line | Fixed | 89c5c7d9, 3a9dbb3b |
| 14 | Explore lens, Discover: five price cards ("Closest to break-even this morning (none is a bet)" with prices and EV) sit above the games, about 1,500 px of phone | Left: the cards and their heading are `TodaysPicks.tsx` (K1's file). Also the heading beside "Morning run, prices as of Nov 16, 8:35 PM ET" is still in that file. See Devin's decisions | - |
| 15 | Scorecard "In plain words" in Learn is CRPS and "production minus baseline" prose, straight from stored sentences | Left: the sentences are stored fields; rewording them is a content decision | - |
| 16 | Night chips (Record, Swipe) are cut at the right edge with no hint that they scroll | Left: a CSS-only scroll fade needs a known parent background; a script is more than a polish | - |
| 17 | Explore leg: the line slider's tick row and "model's line" link leave a gap under the track | Left | - |

Checked and fine: Discover, game, leg, Live, Track record (replay, calibration, guess), a night, Swipe (closed night),
My picks, Scorecard, Forward tests, Key terms, Error codes, Alerts, Preferences, first run, unknown link, service down,
palette (phone search button), 360 and 390 with Giannis Antetokounmpo, Shai Gilgeous-Alexander and Portland Trail
Blazers at Minnesota Timberwolves, 768, 1280, 1440, dark and light, Learn/Explore/Market, Simple and Analyst.
A grep of visible Learn and Market Simple text for schema, export, stamp, T-60, made_at, props_, fallback, json found nothing.

## Checks (from `date`: 05:20 to 05:37)
| What | Result |
|---|---|
| `npm run check` OFF (app/web) | pass; vitest 559 tests, 55 files (was 558: one more test file content-neutral; none removed) |
| `VITE_PH_BETTING=1 npm run check` | pass; 559 |
| `node scripts/flag-parity.mjs` | passed, 16 markers; OFF js 418,681 B, css 125,305 B; ON js 585,030 B, css 125,305 B (artifact half skipped: no baked data) |
| `WALK_API_PORT=8170 WALK_WEB_PORT=5320 app/scripts/check.sh --browser` | ALL CHECKS PASSED; api pytest 604 passed, 1 skipped; ruff, format, mypy pass; walk seed 1 and 2: 0 violations |
| `app/scripts/walk.sh --widths` | 0 violations at 390 plain, 390 injected, 360, 768, 1180 |
| `VISUAL_API_PORT=8171 VISUAL_WEB_PORT=5321 app/scripts/visual.sh --quick` | 120 captures, 0 gating findings |
| `learn-dom.mjs` (fixtures, own servers, Chrome on 9661) | "Learn lens: no price words on any route." exit 0 |

Not run: `npm run build:artifact` / `check.sh --bake` (no baked data in this worktree; the build was not touched, but I
did not rebuild the file), `bet-journey.mjs` (K1's), `check.sh --betting` (only the web half, via `VITE_PH_BETTING=1 npm run check`).
Run locally: `cd app/web && npm run check && VITE_PH_BETTING=1 npm run check && node scripts/flag-parity.mjs`; from the root
`WALK_API_PORT=8170 WALK_WEB_PORT=5320 app/scripts/check.sh --browser`, `... app/scripts/walk.sh --widths`,
`app/scripts/visual.sh --quick`.

## Decisions (what / why / what would reverse it)
1. Shorter lens hints, same meaning, used everywhere. Why: one line on a phone. Reverse: the three strings in `Lens.tsx`.
2. Phone switch spacing instead of moving Detail elsewhere. Why: both stay labelled, 44 px targets stay. A tighter
   one-row design would need an icon-only Detail, which a newcomer cannot read. Reverse: the last block in the 759 px
   media query of `jarvis.css`.
3. Discover trusts the stored night list's `finished` count before requesting a result. Why: stored fields already say
   the game is not final. Reverse: drop `storedFinished` in `Today.tsx`.
4. Parlay joint-chance records moved to Analyst. Why: they carry file names and export-field talk. Reverse: render
   `ParlayDiagnostics` for every market view.
5. The "one exception" sentence left the Market stand-in. Why: it confused and hid nothing. Reverse: restore it in `Lens.tsx`.
6. Search lists one row per player. Why: four identical rows. Reverse: restore the old key in `shellData.ts`.
7. "Desk" is gone from the empty states a friend can reach; "Desk as of" stays in Analyst views.

## Needs Devin
- Whether Explore should show Today's picks (prices, EV) above the games at all, or after them, or only in Market
  (`Today.tsx` line `lens === "learn" ? null : <TodaysPicks ...>`; its wording is in K1's file).
- The Learn Scorecard prose (item 15) and "Public / this machine" in the sidebar: wording for a stranger.
- The Leg page's two line controls stay as Devin's call, untouched.

## Not verified
No real phone, Safari, Firefox or screen reader. Artifact (file) build not rebuilt. Hover and focus rings looked at only
in CSS, not in photographs. The headline's no-break space was checked in headless Chrome at 768 only after the change by
the walks (no new violations), not photographed. Servers on 8170, 8171, 5320, 5321: see below.
