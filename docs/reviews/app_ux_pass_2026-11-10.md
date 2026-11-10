# Adversary review: app UX pass (agent/app-ux-pass at 90d3ec5a, from main 5fa84e0b; app/web only)

Scope: honesty regressions only, as asked. I ran my own servers and stopped them all:
- API :8765 from the branch worktree, on `data/export/latest`, with `APP_LIVE_DIR` set to a scratch copy of the live
  fixture (game 401909952 final, 401909955 not started);
- vite :5293;
- Chrome :9672.

I did not touch 5173, 8000 or cloudflared. The diff touches no API code.

**Overall: SURVIVES.**

Verdict: APPROVED

| # | Check | Command / method | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Learn/Explore price-free | `crawl3.mjs` (same price regex as earlier reviews; every `<details>` opened; aria/title/alt text) in Learn and Explore on 21 routes: Today (default, 11-08, 11-09), game pages, leg pages (a calibrated prop), swipe, picks, parlay (with the fixture tickets file), prefs, the three market-only notes, record (3 tabs), live list, live game, alerts | 42 pages | 0 American prices, 0 `$`, 0 cents, 0 "odds"/EV in either lens | PASS |
| 2 | LINE_CAL_LIVE lock: Live strip and state chip | code read of `Today.tsx` `StateChip`/`LiveStrip`; text and screenshots of `#/?date=2026-11-09` (LAL at ATL final on the capture) and `#/?date=2026-11-08` | 2 nights | The chip reads "FINAL" or "Live <clock>" and never shows a score (the code comment says so, and the render agrees). The strip shows counts only ("1 final, 1 not started. Plays and player stats."). No per-prop outcome appears on Today. The cards keep their chances (for example "Kevin Durant over 20.5 pts 52% calibrated") with no result beside them. The Court card, which could show a final score, is now Market-only. The game-page lock from 38ea8571 is untouched by this diff | PASS |
| 3 | Calibrated-only tags | text counts | 7 pages | The calibrated-prop leg page shows 8 "calibrated" tags; Today 11-08 shows 3; the reference-line game shows "model only" labels. No raw or unlabelled chance. The diff removes no `CalTag`, `RESULT_HIDDEN` or `LOCK_NOTE` | PASS |
| 4 | Attribution and UNPROVEN | text and code | live list, live game | "Play-by-play: ESPN. Not affiliated." on both Live pages. `CHECKPOINT_LABEL` (UNPROVEN) is still rendered (`LiveParts.tsx:360`). The diff removes neither. A final game's as-of line now reads "Game over. This is the last copy we captured", which is accurate | PASS |
| 5 | 375 px overflow | `ux375.mjs` (`scrollWidth` plus any element past the viewport outside a scroll container), dark and light | 4 pages x 2 | Today, Welcome, Live list and Live game: `scrollWidth` 375, no element past the viewport. The night tabs scroll inside their own row | PASS |
| 6 | Gates (web; unpiped) | `npm run lint`; `npm run typecheck`; `npm test` | 290 tests | all exit 0 | PASS |

Notes (not blocking):
- The Today headline still reads "8 games tonight" on a night where the strip says one is final. That is wording, not
  a number problem.
- The Learn "Parlays live in the Market lens. Keep your money." panel moved below the games. It is still present.

**Not verified:** a chip with an in-progress game rendered on Today (the fixture holds only final and not-started
games; by code the chip shows the clock, never a score); the artifact build; the Market lens (outside this scope).
