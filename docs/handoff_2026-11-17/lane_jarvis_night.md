# Lane K4: the betting tools lived through on a phone (switch ON)

Branch `agent/app-jarvis-night` (from 6b37acdd), worktree `nba-wt-1117-jv-night`. Started 06:00 CST 2026-11-17 (from `date`),
last check 06:34. Edits only under `app/web/src/betting/`, `app/web/scripts/bet-journey.mjs` and this file.

Screenshots: `S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/k4`.
Before: `$S/before/` (journey, 375/1280 dark/light), `$S/hub375/`. After: `$S/after375/`, `$S/after/`, `$S/final/`.

## What got in the way, ranked

1. Compare at 375: five bullets, a 140 px fee form and three stacked buttons sat above the venue board. FIXED (7605ee86,
   c0181152): two lines stay visible (where the prices come from, "delayed, not live" with its age; the settlement caveat),
   the rest (stale rule, blank cost, listing order) is behind "About these prices"; the fee form is a "Venue fees (n venues
   without one)" disclosure; the three actions are small buttons in one row; shorter page description.
2. Hub `#/bet`: a lede paragraph, three steps and four doors (the same content twice) before anything about the slip. FIXED:
   description carries the one-line "never estimates a chance" sentence, a one-line "Your slip: N planned, N recorded, N waiting
   for a price . cost" glance with a link, then four numbered cards (Pick a prop and compare, Build your slip, Size a hedge,
   Save it as an idea), then the tiles and export/import. The "keeping your money is a fine answer" line moved to the foot.
3. The same thing under different names: "provider" in slip warnings, "Cross-provider", "2 recorded" vs "recorded
   positions". FIXED: glossary below; five strings in `slipModel.ts` reworded ("venue"), counts say "recorded positions".
4. Slip actions had no primary at the "picks only" stage and Hedge Finder was a plain button once only positions exist.
   FIXED: "Compare venues to price this pick" is primary with picks and no planned legs; Hedge Finder becomes primary when
   nothing planned is included; "Mark slip as placed" stays primary when planned legs are included.
5. The 8-leg phone sheet (checklist row 103) had no test. FIXED: the journey now seeds 8 planned legs, opens the sheet at
   375, checks it fits the screen, scrolls inside itself and that "Mark slip as placed" can be scrolled into view
   (steps `sheet-8-legs`, dark and light). Checklist row 103 can move from "partly" to "met" on that evidence (I did not edit
   the checklist file: outside my files).
6. 1280 rail density and amount alignment: LOOKED, LEFT. The rail is readable (amounts right-aligned, one primary per stage);
   no change needed beyond item 4.

## Glossary (top of `copy.tsx`)
slip (the one working list; "Your rolling slip" only as the page title) / pick (no venue price yet) / leg (a priced line:
planned leg or recorded position) / position (recorded only) / venue (never provider or book) / idea / item (counts only,
"Slip . 3") / stored (delayed), typed (manual), demo (sample).

## Checks (all green, ON and OFF)
- `npm run check` and `VITE_PH_BETTING=1 npm run check` in `app/web`: 573 tests each (56 files), lint, tsc, build.
- `node scripts/flag-parity.mjs`: passed, 18 markers; bytes OFF js 418668 / css 125305, ON js 589496 / css 125305.
- `WALK_API_PORT=8190 WALK_WEB_PORT=5340 app/scripts/check.sh --browser --betting`: ALL PASSED (api pytest 184, route walk 155 steps, violations 0).
- `VISUAL_API_PORT=8191 VISUAL_WEB_PORT=5341 app/scripts/visual.sh --quick --betting`: 148 captures, 0 gating findings
  (the first run found one: the new "About these prices" summary was 40 px tall; fixed to 44 px).
- `node scripts/bet-journey.mjs http://127.0.0.1:5340/ $S/final`: PASSED, 142 "ok" lines, now including the 8-leg sheet.
- `tests/test_layering.py`: 15 passed. No safety test or marker was changed.

## Decisions
- Fold, do not delete: every caveat is still in the DOM; the money-safety note, provenance badge and age, "delayed, not live"
  and the settlement caveat stay visible without a tap. Would reverse: a reviewer wanting the stale-price rule visible too.
- "Item" kept as a count-only word: the launcher counts picks plus legs and no single word covers both.
- Hub cards replace the separate steps list (duplicate content). Would reverse: if a first-time user needs the longer prose.

## For Devin
- Nothing needs a decision. Outside my files, seen but not touched: the Compare head shows an initials avatar when the
  headshot request fails (network in the test browser), which pushes the title down at 375.

## Not verified
Blocked storage, the API stopped mid-way, an empty slip and a prop with no stored quotes were not re-walked this pass (earlier
lanes' tests cover them; the corrupt-store error state did appear by accident while seeding legs and read correctly: a plain
message, nothing replaced by sample). No real phone; Chrome emulation only. 1280 hub and slip-placed screens only looked at
before the hub change (journey covers them without errors).

Servers on 8190, 8191, 5340 and 5341 stopped at hand-back.
