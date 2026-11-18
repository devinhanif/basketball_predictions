# Lane J5: verification tooling and documentation (visual checks, donor map, gap matrix, checklist, README)

Branch `agent/app-jarvis-verify`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-verify`, base `850a3cb7` (the follow-up
branch with J1, J2a, J2b-A, J2b-B, J3, J4a, J4b merged). Started 02:03 CST 2026-11-17, report written 03:25 CST (both from
`date`). No application behaviour changed: nothing under `app/web/src/` or `app/api/` was edited. Nothing pushed or
merged. Servers I started (8140-8143, 5280-5283) are stopped.

## What landed

| File | What |
|---|---|
| `app/web/scripts/visual-check.mjs` (new) | The visual check: fixtures, CDP, structural layer (gates) and pixel layer (opt-in). |
| `app/web/scripts/png.mjs` (new) | PNG decoder (8-bit, colour types 0/2/3/4/6, all five filters, CRC checked) built on `node:zlib`, a tiny encoder for its self-test, and the pixel comparison with changed regions as boxes. `node scripts/png.mjs --self-test`. |
| `app/scripts/visual.sh` (new) | Self-contained like `walk.sh`: fixture API (`APP_LIVE_DIR=app/fixtures/live`) + vite on 8142/5282 (`VISUAL_API_PORT`/`VISUAL_WEB_PORT`), runs the check, stops both. `--betting` sets the switch for those two servers only; an inherited value is removed otherwise. |
| `app/scripts/check.sh` | One opt-in step: `--visual` runs `visual.sh --no-pixels` (with `--betting` when given). |
| `app/web/.gitignore` | `.visual` (baselines, screenshots, report.json). |
| `app/README.md` | One section, "Betting tools (behind one switch)". |
| `docs/jarvis-donor-map.md`, `docs/jarvis-gap-matrix.md`, `docs/jarvis-acceptance-checklist.md` (new) | Donor map, filled gap matrix, marked checklist. |

## How to run it and make a baseline

From the repo root (needs Chrome, `npm ci` in `app/web`, `uv sync` in `app/api`):

```
app/scripts/visual.sh --update        # first time on a machine: screenshots + baseline in app/web/.visual/off/baseline/
app/scripts/visual.sh                 # structural checks (gate, exit 1) + pixel delta per capture vs the baseline (informational)
app/scripts/visual.sh --strict        # pixel delta above --threshold (default 0.005 = 0.5%) also fails
app/scripts/visual.sh --betting [--update]   # the same with the switch ON; its own baseline in app/web/.visual/on/
app/scripts/visual.sh --quick         # a representative third (each screen keeps 4 of its 12 width/theme/mode combinations)
app/scripts/visual.sh --only leg,game # chosen screens
app/scripts/check.sh --visual         # structural layer as a check.sh step; add --betting for ON
```

Output: one table line per capture (route, width, theme, mode, lens, structural result, pixel delta) and
`app/web/.visual/<off|on>/report.json` (every finding, the allow-list, the run time). Baselines are per machine (fonts
and rendering differ); PNGs are never committed.

Deterministic input: fixtures only; fixed viewports (375x812, 768x1024, 1280x800, DPR 1); `prefers-reduced-motion:
reduce`; animations, transitions and the caret disabled by an injected stylesheet; `document.fonts.ready` and two frames
before capture; a pinned clock (`Date` starts at 2026-11-10T15:00Z and runs at real speed); time zone America/Chicago,
locale en-US; seeded `Math.random`, `crypto.getRandomValues` and `crypto.randomUUID`; storage cleared per capture and
the intro dismissed with the app's own keys (`ph.welcomed`, `ph.tour`; theme, mode and lens via `ph.theme`, `ph.mode`,
`ph.lens`); `cdn.nba.com` blocked so avatars are initials; Chrome on a free devtools port from the system.

Matrix: 23 screens OFF (Discover, Game, Leg in Learn/Explore/Market, `#/source`, Swipe, My picks, Parlay, Preferences,
Signals, Hedge Finder, Model History, Alerts, Admin, Track record replay/calibration/guess, Live, Key terms, Scorecard,
Forward tests, Error help, not-found, API-down via request interception), in their lenses (30 screen-lens pairs) x 3
widths x 2 themes x 2 modes = 360 captures. ON adds 7 (`#/bet` hub, slip empty, slip with the demo loaded through the
"Load the labelled demo" button, Compare on the fixture prop, Slip Hedge Finder empty and with the demo, `#/bet` in the
Learn lens) = 444.

## Results (all run tonight, headless Chrome on this Mac)

| Run | Captures | Time | Gating result | Pixels |
|---|---|---|---|---|
| OFF `--update` (baseline) | 360 | 412 s | 48 captures with findings | baseline written |
| OFF again (determinism, before seeding randomness) | 360 | 454 s | same 48 | 16 above 0.5%: Swipe (deck order) and the guess game (random round), up to 1.38% and three height changes; fixed by seeding `Math.random`/`crypto`; those 24 re-baselined and rerun: 24/24 at 0.000% |
| ON `--betting --update` (baseline) | 444 | 520 s | 72 with findings | baseline written |
| ON again (determinism) | 444 | 562 s | same 72 | 444 compared: 431 identical, 13 differ by at most 0.006%, 0 above 0.5% |
| OFF final (determinism, final script) | 360 | 462 s | same 48 | 360 compared: 350 identical, 10 differ by at most 0.0056%, 0 above 0.5% |
| OFF `--quick` | 120 | 145 s | 15 with findings | 0 above 0.5% |

The residual noise (at most 15 pixels in a capture) is the dash phase of dotted underlines on Leg and a 64x32 tile at
y 960 at 768 px; I looked at the Leg crop: content identical. The full matrix takes 7 to 9.5 minutes, so `--quick`
exists: run the full matrix before a merge or for a baseline, `--quick` while iterating; `check.sh --visual` runs the
full structural matrix.

**Detection demonstration.** A scratch copy of `app/web` outside the repo with one CSS value changed (`--radius-lg:
16px` to `10px` in `theme.css`), served on 5283 over the fixture API on 8143, compared against the real OFF baseline for
Discover, Game and not-found: 60 of 60 captures changed, 0.029% to 0.166% of pixels, the boxes at the card corners
(for example `320,992 55x96` on Discover at 375). All are below the 0.5% default, so `--strict` at the default would
not fail this change; with `--strict --threshold 0.0002` all 12 not-found captures failed (exit 1). On this machine the
noise floor is 0.006%, so 0.02% has more than 3x headroom. Unchanged pages in the copy were not compared.

**Check runs:** `app/scripts/check.sh --visual` (OFF, 02:59 to 03:12): api ruff, format, mypy pass;
api pytest 604 passed, 1 skipped; fixtures ruff pass; web eslint, tsc pass; vitest 530 passed (50 files); build,
artifact build ("checks passed"), flag parity ("the OFF builds carry no betting marker; the ON builds carry every one")
pass; visual (structural) FAIL: 360 captures in 401 s, 48 with findings (the defects below), so the summary says SOME
CHECKS FAILED. `app/scripts/check.sh --betting --visual` (ON, 03:12 to 03:23): the same steps, the same counts (604/1,
530), every step pass except visual (structural) FAIL: 444 captures in 491 s, 72 with findings. The visual step fails
because of real findings, not because of the tooling; it is opt-in, so plain `check.sh` is unaffected.
`app/scripts/visual.sh --strict --only notfound,api-down` on the unchanged tree: 24 captures, 0 above 0.5%, exit 0.

**Journey** (`node scripts/bet-journey.mjs http://127.0.0.1:5280/ <scratch>` against `API_PORT=8140 WEB_PORT=5280
app/scripts/betting-on.sh` on fixtures): JOURNEY PASSED at 375 and 1280, dark and light, plus Learn and Explore (no
betting UI). Prop: Donovan Mitchell points, game 0022400252 (no stored quotes; prices typed). `betting-off.sh` with
`PH_BETTING=1 VITE_PH_BETTING=1` inherited: `/api/betting/providers` answered `404 not_found`.

## Structural findings (defects for the other lanes; nothing fixed here)

Every capture passed: no page-level horizontal scroll, no text under 12 px, no AA contrast failure, no clipped text, a
visible focus ring on the first Tab stop, the money-safety note on every priced betting screen, no Learn-lens price
words, the not-found and API-down screens (failure panel with a way on), the Learn-lens `#/bet` note. The findings are
all from the 44 px target rule, the one-h1 rule and the overlap rule. Screenshot paths are relative to the worktree;
the same file names exist under `app/web/.visual/off/current/` for the non-betting screens.

| Route | Lens | Width | Finding | Captures | Screenshot |
|---|---|---|---|---|---|
| Key terms `#/terms` | learn, market | 375 | every term title link `a.term-name` is 20 px tall (e.g. 127x20, 64x20) | 172 findings in 8 captures | `app/web/.visual/on/current/terms_learn_375_dark_simple.png` |
| Leg `#/leg/...` | learn, explore, market | 375 | "Where this number comes from" (`button.linklike.prov-open`) 200x32 and 212x32 | 12 | `app/web/.visual/on/current/leg_learn_375_dark_simple.png` |
| Leg | learn, explore, market | 375 | standalone `p_play` link in the chance-to-play tile (`Distribution.tsx`) 39x16 | 12 | `app/web/.visual/on/current/leg_market_375_dark_simple.png` |
| Leg | explore, market | 375 | `input[type=checkbox]` 22x22 | 8 | `app/web/.visual/on/current/leg_explore_375_dark_simple.png` |
| Game `#/game/...` | learn, market | 375 | "Where this number comes from" 212x32 | 8 | `app/web/.visual/on/current/game_learn_375_dark_simple.png` |
| Discover | market | 375 | "The full story" link (`VerdictStrip.tsx`, own paragraph) 102x20 | 4 | `app/web/.visual/on/current/discover_market_375_dark_simple.png` |
| Preferences `#/prefs` | learn | 375 | switch buttons `button.switch` 52x32 | 4 captures, 12 findings | `app/web/.visual/on/current/prefs_learn_375_dark_simple.png` |
| Parlay `#/parlay` | learn | all | three `h1` ("Parlay of the day" plus two `MarketOnly` headings; lane J2b-B request 2, pre-existing) | 12 | `app/web/.visual/on/current/parlay_learn_1280_dark_simple.png` |
| `#/bet/slip` with the demo | market | 375, 768, 1280 | the toast's "Dismiss" button overlaps the slip launcher "Open your slip, 1 item" (44x12 at 375/768, 44x20 at 1280); the toast also covers page content and the footer (lane J4b request 4) | 12 | `app/web/.visual/on/current/bet-slip-demo_market_375_dark_simple.png` |
| `#/bet/hedge` after the demo | market | 375, 768, 1280 | same toast / launcher overlap | 12 | `app/web/.visual/on/current/bet-hedge-demo_market_1280_light_analyst.png` |
| `#/bet/slip` with the demo | market | 375 | planned-leg include checkbox 20x20 | 4 | `app/web/.visual/on/current/bet-slip-demo_market_375_dark_simple.png` |
| `#/bet/slip` with the demo | market | 375, 768, 1280 | informational (not gating): the stake input's label breaks mid-word ("stak / e") | 12 | `app/web/.visual/on/current/bet-slip-demo_market_375_dark_simple.png` |

Pages-lane owners: Terms, Preferences, Parlay, Discover's verdict strip (J2b-A/B); Leg, Game (J2b-A); betting slip and
toast (J4b, or the polish lane for the shared toast).

## Docs

- `docs/jarvis-donor-map.md`: all 54 `kit__*` files, each to a destination path verified to exist in this worktree or
  "not ported" with its reason; "How to port more of the kit" (rules and seam).
- `docs/jarvis-gap-matrix.md`: 28 rows. MATCHES 12, PARTIAL 10, TARGET-ONLY 5, DEFERRED 1, MISSING 0, UNKNOWN 0. Every
  acceptance-evidence test name was checked to exist in the test files by a script. "Decisions to record" filled from
  the lane reports.
- `docs/jarvis-acceptance-checklist.md`: 60 items. met 33, partly 20, not met 2, not applicable 5. "met" only where a test ran green in tonight's `check.sh`, the journey ran, or a capture was looked at.
- Pending other lanes: gap matrix 4 rows pending the Ideas lane (idea snapshots, shared exposure, idea history, the
  Analysis view inside Model history) and 2 pending the polish lane (slip on `SidePanel`: persistent slip, responsive
  sheet); checklist 5 items pending the Ideas lane and 1 pending the polish lane.

## Decisions (what / why / what would reverse it)

1. **The clock is pinned to a start instant and then runs at real speed**, not frozen. Why: a frozen `Date.now` stalls
   code that waits for elapsed time; labels are minute-grained so milliseconds of drift never show. Reverse: a page that
   prints seconds; then freeze it and accept the risk.
2. **Seeded `Math.random` and `crypto`** in every page. Why: Swipe and the guess game differed by up to 1.4% between
   identical runs. Reverse: none expected.
3. **44 px rule exemptions:** inline links inside a sentence (WCAG 2.5.8 inline exception, by rule) and two selectors
   with reasons (`.skip-link`, the hidden file input `input[type=file].sr`). Overlaps are not counted between a fixed
   layer and page content (a floating layer passes over content by design); two fixed elements are counted (that is how
   the toast/launcher overlap was found). Reverse: tighten by deleting the rule in `visual-check.mjs`.
4. **Mid-word breaks are informational, not gating** (the brief did not list them). Reverse: move `broken` into the
   gating list.
5. **Full-page captures, capped at 8000 px**; with `captureBeyondViewport` a fixed bottom bar is painted where the first
   viewport ends (mid-page on tall phone captures). Deterministic, so harmless for the pixel layer. Reverse: viewport-only
   captures plus scrolling.
6. **Pixel defaults kept as briefed:** tolerance 4 per channel, threshold 0.5%. The demonstration shows 0.5% misses a
   card-radius change; 0.02% catches it with headroom. Reverse: make `--threshold 0.0002` the default once baselines
   are trusted on Devin's machine.
7. **`check.sh --visual` runs the full structural matrix** (about 8 to 9.5 minutes) with `--no-pixels`, not `--quick`.
   Reverse: add `--quick` in `check.sh` if the step is too slow.
8. **Separate baselines for OFF and ON** (`.visual/off`, `.visual/on`), and the script refuses to run if the page's
   switch disagrees with `--betting`. Why: the nav differs, so mixing would be all noise.
9. **Ports 8142/5282 by default** (inside this lane's range); a busy port stops the script with a message, never a
   silent reuse.
10. **API-down is simulated by failing every `/api/*` request** (CDP `Fetch.failRequest`), on the Game page in Learn.

## Not verified

- Real export: the checks run on fixtures only (the fixture replay stores no quote snapshots, so Compare shows typed
  prices and empty boards). `betting-on.sh --export` refused here (no export in this worktree; I did not read the main
  checkout's).
- The single-file artifact's screens (only built by `check.sh`), Safari, Firefox, a physical phone, a screen reader,
  the on-screen keyboard.
- The browser route walk (`check.sh --browser`) was not run by this lane.
- Only the first Tab stop's focus ring is checked, not every control.
- The pixel layer was compared on this Mac only; another machine needs its own baseline.
- Ideas, the Analysis view and the polish lane's shared components were not in this worktree.

## Commits

`5f1ab593` visual-check tooling, `check.sh --visual`, ignore; `96ed3e49` donor map and gap matrix; `c89d26b8` acceptance checklist and README section; then this report.
