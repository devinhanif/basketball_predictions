# Lane J4c: betting screens made pleasant, Ideas, Analysis, the whole flow (2026-11-17, 02:03 to about 02:55 CST)

Branch `agent/app-jarvis-ideas`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-ideas`. All of it sits behind the one
switch (`VITE_PH_BETTING`). With the switch off the JS is 2 bytes smaller than at the start, the CSS file and the
artifact are byte-identical in size, and flag parity passes with 16 markers. Times are from `date`. Nothing was pushed
or merged.

## Items

| Item | Built? | Commit |
|---|---|---|
| A1 Slip on the shared `SidePanel` | Built. The slip is a rail from 1280 px (the page narrows beside it) and the shell's bottom sheet below that. On a wide screen "Hide" (or Escape) collapses the rail, and the floating "Slip N" button brings it back. That choice is remembered (`ph.bet.panel`), and focus goes to the button that brings it back. On phones the shell's own "Slip · N" button opens the sheet, which handles the focus trap, Escape and focus return. The module's own panel, scrim and focus-trap code is gone. The slip is not repeated beside `#/bet/slip`. | `13be07ce` |
| A2 Compare as a compact board | Built. One row per venue (name and kind, native price, provenance badge and age, fee, cost per $1, one quiet Add plus "Type"), grouped Priced then Unavailable, each by name. The caveats are said once above the board, with the snapshot time span. There is one "Set a fee for <venue>" control. The typed-price and existing-bet forms open in `Dialog`. On phones each row is a short card, sized by a container query so it also adapts when the rail narrows the page. "Refresh stored prices" was added. | `292210a8` |
| A3 Toasts | Built inside the module (`notice.ts`). One notice per action; a newer one of the same kind replaces the older; at most 1 shows on phones and 2 elsewhere; 5 s, or 8 s for a failure; each is placed in the top or bottom band, whichever does not cover the focused control. `src/toast.ts` and its callers are untouched. | `13be07ce` |
| A4 Hedge Finder on phones | Built on the same board. Held-position chips scroll sideways and are pinned under the top bar below 1024 px. Each row shows the hedge size, gross payout and cost; "Show before and after" is a per-row disclosure, built only when opened. At 375 px the page went from more than 6,000 px of cards to about 4,300 px. | `292210a8` |
| A5 Hub plus `dollars()` | Built. A three-step strip, four door links (icon, text, next step), five workspace `Tile`s. `dollars()` was added to `format.ts` with 3 tests; `money`/`signedMoney` in `parts.tsx` now call it. | `13be07ce` (formatter), `72c11dec` (hub) |
| A6 Empty and first-use states | Built. Compare with no prop lists the night's props that have stored quotes (`/api/betting/slate/{date}`), searchable, 24 at first, then "Show all"; a link without a line takes the game's posted line. A slate failure other than 404 is shown as an error. The slip's empty state is tidied; the Hedge Finder's empty state already linked to the slip. | `72c11dec` |
| B Ideas | Built: `#/bet/ideas` and `#/bet/ideas/<id>`, a nav and palette entry (literal href, Market lens only), and "Save as an idea" on the slip page. Per market you record Over hit, Under hit, Void or Pending; settling needs every market. Each leg shows Won, Lost, Void or Pending. The net is the matching outcome row at the saved prices, labelled with `PAPER_NET_NOTE`. Loading a copy asks for confirmation when the slip is not empty. Delete asks for confirmation. One idea or all can be exported. An ideas file can be merge-imported: an invalid file changes nothing and says why. | `48f5d7a0` |
| C Analysis | Built. Four real panels (see "Analysis panels" below). Opened from Compare ("Model and record for this prop") and from slip legs on the full slip page. | `dd3fd643` |
| D Flow and tests | Built. The journey was extended; tests were added for coalescing, the OFF side panel, Compare grouping, refresh immutability, saved-price net, voids and the ideas file. | `c8b96c69` (journey); tests are in each item's commit |

### Analysis panels
1. The model's forecast: the player page's own `Headline`, `ModelPanel` and `DistributionPanel`, embedded as they are and fed by the same `/api/breakdown` answer.
2. Links to the stored record (`#/source/<game>/<player>/<stat>`), the player page and Compare.
3. The stored quote board, as counts only: provenance, newest snapshot, lines stored, venues on the line by side, unusable quotes, venues with nothing stored, and the export's `unknown` list.
4. The prop in the viewer's slip and saved ideas.

The donor's other panels (scenario lab, history ledger, calibration, signals) have no stored field here and were left out. Nothing on the page is a placeholder.

## Numbers

- Web vitest: **552 passed with the switch off and 552 with it on** (53 files). 22 cases are new: `format.test.ts` 3, `notice.test.ts` 6, `checks/sidePanel.test.ts` 2, `ideaModel.test.ts` 11. That puts the starting count at 530, which I inferred rather than ran. No existing test was changed or removed.
- Bundles, bytes (from `scripts/flag-parity.mjs`):

  | | js | css | artifact |
  |---|---|---|---|
  | OFF at start | 412,331 | 115,705 | 4,581,111 |
  | OFF now | 412,329 | 115,705 | 4,581,111 |
  | ON at start | 524,165 | 115,705 | 4,692,938 |
  | ON now | 576,514 | 115,705 | 4,745,279 |

- Gate before every commit, chained, exit code read, run on the working tree:
  `npm run check && npm run build:artifact && VITE_PH_BETTING=1 npm run check && VITE_PH_BETTING=1 npm run build:artifact && node scripts/flag-parity.mjs`.
- Journey: `node scripts/bet-journey.mjs http://127.0.0.1:5260/ <out>` against the frozen export (Ayo Dosunmu points 17.5, 2026-11-16, 14 venues). Result: **JOURNEY PASSED at 375 and 1280, dark and light, plus the Learn/Explore check**. The Learn/Explore check now also covers `#/bet/ideas` and `#/bet/analysis`.
- Browser checks (`WALK_API_PORT=8098 WALK_WEB_PORT=5298 app/scripts/check.sh --browser`, then the same with `--betting`): see the end of this file.

## Screenshots I read
All under `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`:
- Before: `jv_journey/20_compare-typed_1280_dark.png`, `07_hedge_375_dark.png`, `06_slip-placed_375_dark.png`, `22_slip-placed_1280_dark.png`, and the donor shots in `jv-donor-shots/`.
- After:
  - `jvi/a1/`: rail and sheet.
  - `jvi/a2/`, `jvi/a2b/`, `jvi/a2c/`: Compare and Hedge Finder at 375 and 1280, dark and light, with a before/after disclosure open.
  - `jvi/a56/`: hub, empty Compare with search, empty Hedge Finder, at 375, 768 and 1280, dark and light.
  - `jvi/b/`: Ideas list and idea page.
  - `jvi/c/`: Analysis.
  - `jvi/journey1/`: every journey step.

Fixed after looking:
- global `th` styles made venue names uppercase;
- phone rows were four lines (now flex rows, and the redundant "needs a fee" cell is hidden in the narrow layout);
- the board crammed beside the rail (now a container query);
- the props list had a stray inset;
- focus was lost after typing a price, because the row moved groups (it now lands on that row's action).

## Decisions (what / why / what would reverse it)
1. **Toasts are coalesced inside the betting module (`notice.ts`), not in `src/toast.ts`.** Why: placement away from the control and the phone limit need styling, and the only consumer of `toast.ts` is `Shell.tsx`, which is off limits. Existing callers keep their behaviour. Reverse: the shell lane adds kinds and placement to `toast.ts`; then the module calls it.
2. **On phones the slip opens from the shell's "Slip · N" sheet button.** The module's own floating "Slip N" button shows only on wide screens when the rail is hidden. Why: the shared host owns the sheet's open state. Reverse: `SidePanel` gains an `open()` API.
3. **The rail is hidden on `#/bet/slip`.** Why: it would repeat the page beside itself.
4. **Optional `voids` on a saved idea.** It is validated, documents without it still read, and an idea with a void has **no net** ("refund rules are not modelled"). Why: the brief asks for void; a refund amount would be invented. Reverse: verified per-venue void rules.
5. **`recordOutcomes` refuses to settle an incomplete idea** (for example an unknown fee) with a plain message. Why: the validator already refuses such a document; refusing earlier gives a clearer reason. Ideas built only from stored quotes (fees unknown) can be watched and noted, but not settled, until a fee is entered before saving.
6. **Ideas files are their own format (`parlayhitters-saved-ideas` v1) and import merges.** Existing ids are skipped, never overwritten, and one bad idea refuses the whole file. Whole-workspace import stays on the hub and slip pages.
7. **`#/bet/ideas/<id>` comes from an optional `param` on a route definition.** Only registered `param` routes take a second segment matching `[A-Za-z0-9_.:-]{1,160}`; `#/bet/a/b` and the existing hostile set stay not-found (test unchanged).
8. **The Compare fee you type stays on that screen only** (as before), now set from one control. Reverse: persist fees per venue in the workspace.
9. **A Compare link without `line` uses the game's posted line.** Why: the slate route carries no line.
10. **The logic file is `ideaModel.ts`, not `ideas.ts`.** Why: macOS's case-insensitive filesystem clashed with `Ideas.tsx`.
11. **The DOM-importing test lives in `src/betting/checks/`.** Why: `hygiene.test.ts` (not recursive) holds the module's flat `.ts` files to pure imports.
12. **Commits were gated on the working tree, not on each commit in isolation.** Items A1/A3/formatter and A2/A4 are two commits because their files are intertwined.

## Not verified
- The first commit (`13be07ce`) was not built on its own; only the tree at each commit's time was gated.
- Not opened in a browser: the artifact (single-file) betting pages, Safari, a real phone, a screen reader. The `storage` event across tabs was not exercised.
- The idea journey's "fresh viewer scope" is a cleared `localStorage` (a new device), not a second signed-in account; accounts do not exist.
- The Analysis panels were checked on one prop only (Ayo Dosunmu points). A prop with no history (`prior_games == 0`) omits `Headline`, by design, and was not photographed.
- 768 px was photographed for the hub, Compare-empty and the Hedge Finder empty state only.

## Browser checks
Run from the worktree root on `c8b96c69`, finished 02:54 CST:
- `WALK_API_PORT=8098 WALK_WEB_PORT=5298 app/scripts/check.sh --browser` (OFF): **ALL CHECKS PASSED**. API pytest 604 passed and 1 skipped; web vitest 552; artifact; flag parity; route walk seeds 1 and 2 with 0 violations each.
- The same with `--betting` (ON): **ALL CHECKS PASSED**. API pytest 604 passed and 1 skipped; web vitest 552; route walk with 0 and 0 violations.

My servers (8120, 5260) are stopped; the check scripts stop their own (8098, 5298).
