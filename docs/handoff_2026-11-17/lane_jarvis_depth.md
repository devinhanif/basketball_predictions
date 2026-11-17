# Lane K3: the depth pass (game page, leg page, record pages; switch OFF, real data)

Branch `agent/app-jarvis-depth` (cut from `29aef787`). Started 05:40 CST, final gate finished 06:32 CST
(all times from `date`). Edits only under `app/web/src` plus this file. Nothing pushed or merged. Real data: the frozen
2026-11-16 export (read-only); the committed 2024-25 fixtures for posted lines, results and the walkers.

Screenshots (full page, sliced; `S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/k3`):
- Before: `$S/b1` (leg and game, Learn, Simple, phone), `$S/b3` (leg, Market, Analyst), `$S/r1` (Track record, Scorecard, Forward tests, phone).
- After: `$S/b5` (leg Analyst), `$S/b2` (leg Explore), `$S/b4`, `$S/f1` (fixtures, Market, 375 and 360), `$S/f2` (finished fixture game),
  `$S/d1`, `$S/d3` (1280, dark and light), `$S/t7` (768 light), `$S/m1` (game, Market), `$S/r2`, `$S/r3`, `$S/r4`, `$S/r5` (records, phone and 1280),
  `$S/s1` (Swipe), `$S/x1` (sections forced to fail), `$S/n1` (stat with no forecast), `$S/g1` (game, Explore, Analyst).
  Some "before" folders were reused for the after shot of the same page; the ones above are the first captures.
- Driver: `$S/look.mjs` (a copy of K2's with real full-page capture and slicing), `$S/run.sh`, `$S/otp.js` (jump list check).

## Findings, ranked by how much they hurt
| # | Page | Finding | Status | Commit |
|---|---|---|---|---|
| 1 | Game, Analyst | Every player opens at once: 16 players x 4 stats = 38,000 px on a phone | Fixed: Analyst opens the top three per team, with "Open all N players" / "Close all"; 10,700 px | b1283c9a |
| 2 | Game, Analyst/Explore | "Full breakdown: last 10, splits, teammates out" ran outside its own button at 375 | Fixed (text wraps; the button rule lost to `.btn`) | b1283c9a |
| 3 | Scorecard (Learn/Simple) | 13,500 px; 23 "counts only" rows each with file paths (`docs/prereg/...md:83-91`, `nba/daily/settle.py:264-418`); a list of what cannot be shown with the same paths | Fixed: both fold in Simple ("Show the 23 rows kept back", "Show the 6 things we cannot show yet"); bracketed source paths stripped in Simple, kept in Analyst; 6,300 px | 8a533153 |
| 4 | Scorecard (phone) | Stacked rows repeated an empty "Better, worse or same: -" | Fixed (an empty cell is left out of the stacked block) | 8a533153 |
| 5 | Leg, Simple | "[needs export fields]" placeholders ("What moved it", "Why this label") with a docs path on a friend's screen | Fixed: Analyst only | 07720c87 |
| 6 | Leg, Simple | Internal words: `props_context_residual` model id, "Source r. p_play", "whole-number support", "from the availability model R" | Fixed: plain wording in Simple ("chance he plays", "Our props model", "our availability model"); Analyst keeps the exact terms | 07720c87 |
| 7 | Leg | The model's own reason was a third telling of minutes and chance to play (plain-words paragraph, Games note, Model panel) | Fixed: the Model panel reason is Analyst only | 07720c87 |
| 8 | Track record, Replay, locked nights | 12 cards of "Name / No usable sportsbook line. / Tap to reveal what happened" | Fixed: one row per player (name, "Reveal"), one note above the list. Nothing was unhidden: the lock still hides typical night and chance | fa96498e |
| 9 | Leg, Explore | Gap under the what-if slider before the tick and "model's line" link (left open by K2) | Fixed (the 44 px slider plus padding put the ticks 40 px low) | 07720c87 |
| 10 | Leg, Explore (and the line card) | The what-if said "the line card above" while the line card is below it | Fixed ("the line card on this page") | 07720c87 |
| 11 | Track record, Swipe, Discover, Alerts, leg stat chips | Chip rows cut at the right edge with no cue (left open by K2) | Fixed: a fade at whichever edge has more behind it, both themes, gone at the end, absent when the row fits (CSS scroll-driven animation; where unsupported the cut chip stays the cue) | fa96498e, cd3c9ca3 |
| 12 | Swipe | Night chips started at the oldest night; the chosen (newest) one was off screen to the right | Fixed: the row opens on the chosen night (Swipe and Replay, `useCurrentTab`) | b07cb074 |
| 13 | Leg, Explore at 375 | "Price is for the over / under" and the three fee choices wrapped as ragged rows (and "Exchange" split mid-word at first try) | Fixed: equal columns, tested at 375 and 360 | eaf57583 |
| 14 | Leg, Explore at 1280 | What-if chart stretched to 930 px (its 12 px labels became 40 px); everything in one 930 px column | Fixed: chart capped at 640 px; line and minutes sit side by side from 1100 px | be1462cb |
| 15 | Every page with a section that can fail | Four h1 when sections failed (the leg page with its sections failing) | Fixed: section failures are h2 (leg page with three sections failed: one h1) | cd3c9ca3 |
| 16 | Leg, Simple | Jump list names sections inside the closed "More depth" fold; a jump landed on a hidden heading | Fixed: a jump opens any closed fold around its target. Checked: 10 of 10 jumps land with the heading at 80 px under a 61 px header | 07720c87 |
| 17 | Game | Player lines (the point of the page) sat 1,700 px down, under the injury timeline | Fixed: the timeline now follows the player lines | 204cc57b |
| 18 | Game | "Typical pts 6" next to "22.8" | Fixed: one decimal everywhere in the list | b07cb074 |
| 19 | Game, desktop | "Forecasts that moved" one narrow row per player across 1000 px | Fixed: columns on wide screens | b07cb074 |
| 20 | Forward tests | "Outcome: Hidden until the look. The result is published..." under a badge that says it, on 15 cards | Fixed: said once (badge and the page's intro) | 8a533153 |
| 21 | Leg | "Over -110 at 28.5 (over -110, under -120)" said the same price twice | Fixed: names the other side only | eaf57583 |
| 22 | Leg | "Splits bettors check" in the Learn lens | Fixed: "How he does in each situation" | 7c1c8507 |
| 23 | Leg, Games panel | "from the availability model R" in Simple | Fixed (via 6) | 07720c87 |

Checked and fine: game and leg in all three lenses, Simple and Analyst, 375/360/768/1280 (1440 not re-shot after the
changes), dark and light, on the frozen export and the fixtures; finished fixture game (result beside forecast on a
pre-lock night); a stat with no forecast (dotted chip, "No model forecast for this stat yet", recent games still shown);
three sections forced to fail (the rest of the page stays; one h1); player without a headshot (initials; the sandbox has
no network, so no real photo loaded anywhere); Track record, a night, Scorecard, Forward tests at 375 and 1280.

## Left (with the reason)
| Finding | Why left |
|---|---|
| Explore leg: the what-if lab sits above the line card and the recent games, so the record of his last 10 starts about 1,600 px down on a phone | Product decision: it is Explore's point. Moving the lab below the headline (as Market does) is one line in `Leg.tsx` |
| Leg: the plain-words paragraph, the yellow "Heads up" and the Headline say the same 8-of-10-against-48% thing | They are stored sentences and a stored flag; trimming one is a content decision |
| Scorecard "In plain words" is CRPS prose in Learn; model ids (`props_context_residual_t15`) in its Row column | Stored sentences and ids; K2 left it too |
| Section failure panel says "This page hit a problem ... Back to Discover" for a failed section | The copy is `app/error_codes.json` (API lane). A compact section variant would be better; needs a registry entry or a prop on `ProblemPanel` |
| `TodaysPicks.tsx` (K1's file) renders its `View` without `errorLevel={2}`; a failure there adds a second h1 on Discover | Not my file. One prop |
| Leg: "99.6% chance he plays" (Model panel) beside "100%" and "over 99%" in the stored paragraphs | Different stored roundings; one style would need the engine's sentence changed |
| Leg: the Over price field and the Under field (label two lines) sit a few px apart on the baseline | Cosmetic, Market/Explore price block only |

## Checks (all from `date`, switch OFF unless named)
| What | Result |
|---|---|
| `npm run check` (app/web) | exit 0: eslint, tsc, vitest 559 tests in 55 files (none added, removed or changed), build |
| `VITE_PH_BETTING=1 npm run check` | exit 0, 559 tests |
| `node scripts/flag-parity.mjs` | passed, 16 markers; OFF js 420,551 B, css 126,884 B; ON js 586,896 B (artifact half skipped: no baked data) |
| `WALK_API_PORT=8180 WALK_WEB_PORT=5330 app/scripts/check.sh --browser` | ALL CHECKS PASSED: api ruff, format, mypy, pytest (164 s step), web steps, flag parity, route walk seeds 1 and 2: 0 violations |
| `app/scripts/walk.sh --widths` | 0 violations at 390 plain, 390 injected, 360, 768, 1180 |
| `VISUAL_API_PORT=8181 VISUAL_WEB_PORT=5331 app/scripts/visual.sh --quick` | 120 captures, 0 gating findings, no baseline to compare |
| `node scripts/learn-dom.mjs http://127.0.0.1:5331 9661` (fixtures) | "Learn lens: no price words on any route." exit 0 |
| Jump list, 4 pages at 375 | every jump lands at 80 px under a 61 px sticky header |

Run locally: `cd app/web && npm run check && VITE_PH_BETTING=1 npm run check && node scripts/flag-parity.mjs`; from the
worktree root `WALK_API_PORT=8180 WALK_WEB_PORT=5330 app/scripts/check.sh --browser`, `... app/scripts/walk.sh --widths`,
`VISUAL_API_PORT=8181 VISUAL_WEB_PORT=5331 app/scripts/visual.sh --quick`.

## Decisions (what / why / what would reverse it)
1. Analyst opens the top three players per team on the game page. Why: 38,000 px of open cards is not usable; "Open all" keeps the old view one tap away. Reverse: `open={openAll ?? (mode === "analyst" && i < 3)}` in `Game.tsx`.
2. Placeholders for fields the export lacks (`NeedsFields`) are Analyst only. Why: a friend read "[needs export fields]" as a broken page. Reverse: drop the `mode === "analyst"` guard in `Leg.tsx`.
3. Simple wording on the leg page comes from the same stored fields with the internal words removed; Analyst shows the stored text. Reverse: per-line `analyst ?` switches in `Distribution.tsx` and `LegHistory.tsx`.
4. Scorecard in Simple folds the long lists and strips bracketed source paths; Analyst shows everything. Reverse: the `analyst ? ... : <details>` branches in `Scorecard.tsx`.
5. Locked replay rows are one line each. Why: the lock leaves nothing to say per player; the note says so once. Reverse: `bare` in `Record.tsx`.
6. The fade on scrolling chip rows is CSS only (no script). Reverse: the `@supports (animation-timeline ...)` block in `jarvis.css`.
7. The game page shows player lines before the injury timeline. Reverse: move `<GameKnowsPanel>` back above the Player lines section in `Game.tsx`.
8. Section failures are h2 everywhere a `View` sits inside a page (one h1 per page). Reverse: drop `errorLevel={2}`.

## Needs Devin
- Explore leg order (lab first or the recent games first).
- The repeated sentence on the leg page (plain words, Heads up, headline) and the Scorecard prose.
- A compact failure panel for a section ("This part could not load. Try again.") instead of the page-level words (registry copy).

## Not verified
No real phone, Safari, Firefox or screen reader. Headshots: the sandbox cannot reach the NBA image host, so only initials were seen.
The scroll-driven fade needs Chrome 115+ / Safari 26; elsewhere the row just scrolls. Artifact (file) build not rebuilt (no baked data).
Hover and focus rings were not photographed. 1440 and the Market lens at 768 were not re-shot after the later commits.
`bet-journey.mjs` and `check.sh --betting` not run (K1's). Servers on 8180, 8181, 5330, 5331 and Chrome on 9661: stopped.
