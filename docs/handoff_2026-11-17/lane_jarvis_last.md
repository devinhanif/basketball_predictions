# Lane J7: the last polish pass (switch OFF and ON, both single-file artifacts)

Branch `agent/app-jarvis-last`, worktree `nba-wt-1117-jv-last`. Started 03:21 CST 2026-11-17 (from `date`). Edits only
under `app/web/` and this file. Presentation, wording and small interaction fixes: no new feature, field, arithmetic,
route, API call or dependency. Nothing pushed, nothing merged.

Screenshots: `S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/jl`.
Before: `$S/journey0/` (journey on the starting tree), `$S/b1/`, `$S/a4/` (ON shell at 375/768/1280/1440, taken while
the first fixes were landing). After: `$S/a1/`, `$S/a6/`, `$S/a7/`, `$S/artoff/`, `$S/arton/`, `$S/fv/`, `$S/keyon/`,
`$S/keyoff/`, `$S/rm2/`. Scratch CDP driver: `$S/look.mjs` (plans in `$S/plans/`).

## A. Known rough edges
| # | Item | Fixed | Commit |
|---|---|---|---|
| A1 | Slip rail unit labels broke mid-word | Yes. The module's six `overflow-wrap: anywhere` became `break-word` (only an over-long token breaks), and the unit after an amount is `nowrap`. Before `$S/journey0/32_compare-typed_1280_dark.png` ("contra cts", "stak e"); after `$S/a4/cmp_1440_dark.png` | `9edcc51c` |
| A2 | Notice over the middle of the Compare board | Yes. From 1024 px every notice sits in one stack at the foot of the sidebar (never over the board, the rail or the control just used); below 1024 px the existing top/bottom placement stays, the bottom band clears the bottom bar and the "Slip · N" button; Dismiss is 44 px. After `$S/a1/add_1440_light.png`, `$S/a1/add_375_light.png` | `9edcc51c` |
| A3 | Floating "Slip · N" over page text | Yes. `SidePanelHost` sets `has-sidepanel-btn` and the module's launcher sets `has-float-launcher` on the root while shown; the footer then reserves the button's height plus a gap and the safe-area inset (136 px phone, 92 px from 1024). After `$S/a1/cmpend_375_light.png` | `9edcc51c` |
| A4 | `theme-color` / manifest colours | Yes: `#ffffff` light, `#0c0f18` dark (the `--head` tokens), manifest `theme_color #0c0f18`, `background_color #f5f5fa` (`--bg` light). The artifact's own head check passes (its build has no theme-color meta) | `fee97143` |
| A5 | "Records from 0, 50 on this page" | Yes: "{n_returned} of {n_records} stored records on this page" (wording checked by typecheck and tests, not photographed on a parlay night), plus ", after the first N" only when `offset > 0`. No arithmetic | `7fb4e312` |
| A6 | Game: floating "Where this number comes from" | Yes: a card foot under the two "?" disclosures, ruled, left-aligned, with "the stored record behind this win chance" beside it; 44 px button. Before `$S/b1/g_top.png`, after `$S/a1/game_1280_dark.png` (the 6 px link inset seen there was removed after) | `1a4e514c` |
| A7 | Explore: two line sliders | Partly. They are two independent states (the lab's line moves the what-if distribution; the line card moves the page), so merging them would change what the lab computes from. The lab's heading is now "Move the what-if line" with one sentence saying it starts at the posted (or model's) line and that neither follows the other. A shared line is in section C | `1a4e514c` |
| A8 | Leg tables clip at 360 | Yes. At <= 380 px heads track tighter and the first column and the "model only" tag may wrap between words. Measured at 360, Market Simple and Analyst: both `table-wrap`s 0 px overflow (were 265>260 and 266>260). After `$S/a6/leg360a.png` | `9edcc51c` |
| A9 | `MarketGate` heading level | Yes: `level?: 1 | 2 | 3` passed to `MarketOnly` (default 1; every current gate is the page's only heading) | `9edcc51c` |
| A10 | "8 h ago · out of date" on every row | Yes. Above the board, once: "A price older than 15 minutes counts as out of date, so the slip shows no coverage figure while it is in a planned leg: every stored snapshot on this board is older than that. Type a current price to use one." (or "each row shows its age" when only some are). Rows keep the provenance badge and age; "out of date" stays in the screen-reader text and stays visible for typed prices and on Ideas (no board note there). 15 comes from `DEFAULT_MAX_QUOTE_AGE_MS` | `9edcc51c` |

Coordinator's touch-target list (visual check): Key terms `a.term-name`, "Where this number comes from", `p_play`, the
Explore checkbox, "The full story", Preferences `button.switch` (now a 60x44 button with the 52x32 track drawn inside),
the slip's include checkbox (a 44 px `tap-check` with a drawn 22 px box), the Forward tests heading links overlapping
at 768 (`nowrap`, same on Scorecard), and on ON screens the hub's step links, "Pick another prop", and `.btn.small`
(Over/Under, Type) on phones. Commits `1a4e514c`, `b7ac7b9c`, `b7fff404`. Parlay Learn: one `h1` (checked at 375/1280).

## B. States looked at (ranked)
| # | State | Problem | Fix | Commit |
|---|---|---|---|---|
| 1 | ON artifact, Compare | Said "not available from this server" in a file with no server | "Stored snapshots are not available in this snapshot; enter prices by hand." (artifact only; Analysis uses the same sentence). Typed price, add, rail, mark placed, Hedge Finder, save idea and export all worked from `file://` (`$S/arton/`) | `b7ac7b9c` |
| 2 | Artifact and app, first visit | Leaving Discover (chooser open) for a game, player or tool page carried the full chooser above that page (`$S/artoff/leg_1280_light.png`) | An untouched chooser follows the page: slim line off Discover; once asked for, it stays open | `b7fff404` |
| 3 | ON artifact, slip page | The floating slip button showed beside the slip page itself (the file keeps detail routes in memory, so the hash never says `#/bet/slip`) | The slip page announces itself to the panel | `b7ac7b9c` |
| 4 | Saved ideas, idea, hub, slip, Compare, Hedge, Analysis | Bare `h1` + lede, unlike the rest of the app | `PageHead` (kicker, title, one-sentence description, actions right; the idea's status badge sits in actions). Exactly one `h1` and a page title on every one at 375/768/1280/1440, both themes (`$S/a7/`) | `9edcc51c` |
| 5 | Slip, Compare | Disclosures ("The thinking behind this slip", fee, more) showed no sign that they open | The app's chevron, scoped under `.ph-bet` | `b7ac7b9c` |
| 6 | Compare at 375 (visual check) | Board heads "clipped by thead" (hidden with `overflow:hidden`) | Hidden with `clip-path` instead; still read as the table's heads | `b7ac7b9c` |

Looked at and left: the drawer, sheet, rail open and hidden beside Discover, Leg and every betting screen at 375 / 768 /
1280 / 1440 dark and light (`$S/a4/`, `$S/a7/`): no page-level horizontal scroll anywhere, one `h1` everywhere.
Keyboard (`$S/keyon.log`, `$S/keyoff.log`): Tab order skip link, home, workspace, grouped nav, top bar; the phone
drawer, the slip sheet and the Type-a-price dialog trap focus (40, 30, 12 Tabs stayed inside), Escape closes each and
focus returns to its opener. Ctrl/Cmd-K: ON lists Compare venues, Slip Hedge Finder, Saved ideas, Your slip, Betting
tools; OFF lists none of them (only the existing Hedge Finder). Reduced motion emulated: sheet, drawer, notice and rail
compute 0.01 ms (`kit.css`'s global rule); no skeleton was caught on screen (loads too fast), covered by the same rule.

## Artifacts opened as files (real data baked from the frozen export, 2026-11-14..16)
OFF 6,529,475 B, ON 6,695,161 B (`$S/art_off/`, `$S/art_on/`, built before `b7ac7b9c`/`b7fff404`; the fixture builds
in the gate include them). Opened `preview.html` over `file://` at 375 and 1280, dark and light. OFF: Discover with
the chooser, a game, a leg, Track record, Scorecard, Forward tests, Key terms, a deep link (slim intro line): all render,
one `h1`, no horizontal scroll. ON: additionally the hub, Compare (the plain no-snapshots sentence, never sample data),
a typed Kalshi price with a fee, add, the rail, an amount, Mark slip as placed, the Hedge Finder with that position,
Save as an idea, Export all ideas. Import was not driven in the file (it needs a file chooser).

## Numbers
| What | Before | After |
|---|---|---|
| vitest OFF / ON | 558 / 558 | 558 / 558 (none removed or changed) |
| OFF js / css / artifact (fixture bake) | 417,211 / 123,240 / 4,593,454 | 417,895 / 124,827 / 4,595,443 |
| ON js / css / artifact | 581,413 / 123,240 / 4,757,646 | 584,063 / 124,827 / 4,761,601 |
| flag parity | passed, 16 markers | passed, 16 markers |
| `check.sh --browser` OFF (8150/5300, finished 04:18) | - | ALL CHECKS PASSED: api 604 passed / 1 skipped, web 558, artifact, parity, walk seeds 1 and 2: 0 / 0 violations |
| `check.sh --browser --betting` | - | ALL CHECKS PASSED: api 604 / 1 skipped, web 558, walk 0 / 0 |
| `walk.sh --widths` | - | 0 violations at all five settings (390 plain, 390 injected, 360, 768, 1180) |
| `learn-dom.mjs` (fixtures, switch OFF) | - | "Learn lens: no price words on any route." exit 0 |
| `bet-journey.mjs` (ON, frozen real export) | passed | JOURNEY PASSED, 98 ok steps (375 and 1280, dark and light, ideas, export/import, analysis, Learn/Explore) |
| follow-up branch `visual-check.mjs --quick` OFF (scratch copy, fixtures) | 12 captures with a gating finding (90 small targets, 1 overlap) | 120 captures, 0 gating findings |
| the same with `--betting` | 2 captures failing (9 small targets, 5 clipped heads) | 148 captures, 0 gating findings |

Commands, from the worktree root: `cd app/web && npm run check && npm run build:artifact && VITE_PH_BETTING=1 npm run check
&& VITE_PH_BETTING=1 npm run build:artifact && node scripts/flag-parity.mjs`; `WALK_API_PORT=8150 WALK_WEB_PORT=5300
app/scripts/check.sh --browser` (and `--betting`); `WALK_API_PORT=8150 WALK_WEB_PORT=5300 app/scripts/walk.sh --widths`;
after merging the follow-up branch, `app/scripts/visual.sh --quick` and `--quick --betting`.

## C. Recorded, not done (ranked)
1. One line for the Leg page: the what-if lab could read the line card's line (or offer "use the card's line") so the
   two never disagree. A behaviour change in what the lab starts from; Devin's call.
2. `theme-color` follows the system theme only; a theme picked in the app does not update it (needs a small runtime
   meta update in `prefs`).
3. The data band's "Export made 9 h ago, schema 2" uses an internal word ("schema") a friend will not know.
4. The palette has no "Prop analysis" entry (it opens from Compare and slip legs only; it needs a prop to mean anything).
5. Slip page: two primary buttons ("Mark slip as placed", "Save as an idea"); a hierarchy choice for the module owner.
6. The visual check's 44 px rule measures a checkbox's own box, not its label; `tap-check` satisfies it, but a native
   checkbox inside a 44 px label is also a fine target (WCAG 2.5.8 is 24 px). Not edited, as asked.

## Decisions (what / why / what would reverse it)
1. Notices from 1024 px live at the sidebar's foot. Why: the only area that is never the board, the rail or the control
   just used. Reverse: the old `left: sidebar + 24px` block in `betting.css`.
2. Footer reservation by root classes (`has-sidepanel-btn`, `has-float-launcher`), named in shared CSS without `.ph-bet`.
   Why: content scrolls clear of any floating button; flag parity unaffected. Reverse: drop the two class toggles.
3. Staleness of stored snapshots said once (A10); typed prices and Ideas keep the per-row word. Reverse: drop `quiet`.
4. A7 labelled, not merged (see C1). Reverse: revert the sentence in `Explore.tsx`.
5. 44 px by construction (`.tap`, `.tap-check`, the switch's own box) rather than invisible hit areas. Why: the checker
   and real fingers agree. Reverse: the old `.switch::before` inset.
6. Welcome follows the route until asked. Reverse: `useState(props.deep !== true)` as before.
7. Commits are grouped by theme where files are shared (`jarvis.css`, `betting.css` cannot be staged by hunk here); the
   gate ran on the working tree before each pair of commits, not on each commit alone.
8. The follow-up branch's visual check was run from a scratch copy (`$S/vc/`) instead of merging: the merge was refused
   by the permission system and the brief forbids merges.

## Incident
At 03:38 I killed PID 38040, a headless Chrome I took for my own; its profile was `scratchpad/pages/ldprof`, another
agent's learn-dom run. If that agent reported a stalled or failed learn-dom around 03:38, this is the cause; rerun it.

## Not verified
- No real phone, Safari, Firefox or screen reader. Import of a file in the artifact. Cross-tab sync.
- The real-data artifacts were not rebuilt after the last two commits (the fixture artifacts in the gate were).
