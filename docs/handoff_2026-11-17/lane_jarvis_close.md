# Lane K1: closing the betting module's "partly" rows (switch OFF and ON)

Branch `agent/app-jarvis-close` (cut from `5b94a4ea`), worktree `nba-wt-1117-jv-close`. Started 05:02 CST 2026-11-17 (from
`date`), gates finished 05:52. Edits only under `app/web/src/betting/**`, `app/web/scripts/bet-journey.mjs` and this file.
Nothing pushed, nothing merged. Scratch: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/k1/`
(journey screenshots `journey4/`, artifact with blocked storage `artblock/`).

## Items
| # | Item | Result | Proof |
|---|---|---|---|
| 1 | Cross-tab sync | Done. `store.externalChange(key)` (new): only this scope's key (or null) counts; it runs behind any edit or save in flight; an unsaved edit on screen is never overwritten. `useBet` calls it from the `storage` event | `closeLane.test.ts` "cross-tab sync" (4 tests) |
| 2 | Journey: select / deselect / remove | Done | `bet-journey.mjs` steps "deselect moved the total $10.98 -> $10.00; reselect restored", "recorded positions cannot be switched off", "removing a recorded position shows its wording first and removes the note only after confirming" |
| 3 | Paper-outcome history | Done. `SavedIdea.history` (time, action recorded/corrected/reopened, status, outcomes, voids), appended by `recordOutcomes` (an identical save adds none; capped at 200). Workspace schema v2, migration 1 to 2 adds an empty history. Idea page "History of this record", labelled typed by hand. Export/import carry it | `closeLane.test.ts` "history of an idea's paper outcomes" (4 tests: actions, version-1 workspace loads, older file imports, invalid import changes nothing); journey step "history of this record lists the entry" |
| 4 | Add to slip from Discover | **Skipped, by the brief's own rule.** The pick card has `game_id`, `player_id`, `stat`, `side` but only `threshold`, an integer "N or more" count (real data: 2, 1, 6, 15). The slot needs a sportsbook-style `line` (N - 0.5). That is a derived number, not a field on the card. No edit to `TodaysPicks.tsx`. Reversal: if the API ever serves the line on the contract, mount `AddToSlipSlot` there with `from="picks"` | none |
| 5 | Compare board ordering | Guard added. `useLoad` already drops answers of a superseded request (`live` flag), but for one render after a prop/line change the old board was still read against the new market. `boardMatches` (game, player, stat) now turns a mismatched board into "loading" in `useStored` and `useRawBoard` | `closeLane.test.ts` "Compare board ordering" (pure match test; source check that `useLoad`'s guard and both uses remain). No DOM test is possible (vitest runs in node here) |
| 6 | Long content | Done (CSS wrap rules for tags, thesis, leg text). 40-character name, 600-character thesis, two 60-character unbroken tags | journey steps long-slip / long-ideas / long-idea at 375 and 1280, dark and light: no page scroll and no control outside the viewport |
| 7 | Compatibility audit | Done. Nothing needed fixing (no `dvh`, no `Array.at`, `randomUUID` only inside a `typeof` guard in `ids.ts`; one comment mentioning `structuredClone` reworded). Test enforces it for `src/betting/**` ts, tsx and css | `hygiene.test.ts` "older-browser rules" |
| 8 | Blocked storage | Done. If the very first read throws, the store runs in this tab's memory (`BetView.memoryOnly`); `MoneyNote` (on every betting screen) then shows "This browser cannot save your slip." with what it means and that Export keeps a copy. A store that has read fine once is never swapped for memory. Opened the switch-ON single-file page as a file with `localStorage` and `sessionStorage` throwing: all six screens show the notice, no page scroll, no exception | `closeLane.test.ts` "blocked storage" (3 tests); `artblock/_bet_slip_375.png` |
| 9 | First-time-user pass | Partly (long-content screenshots only). One thing seen and left: on the idea page and slip at 375 the "Every listed outcome" table scrolls sideways inside its own box, so the "Net with planned legs" column is cut at the edge (existing behaviour; `OutcomeTable` in `parts.tsx` is mine if wanted: stack the cells under 400 px) | `journey4/17_long-idea_375_dark.png` |

New markers in `markers.json`: "History of this record", "This browser cannot save your slip." (18 markers now).

## Checklist rows that can move to "met"
- Out-of-order responses (row 39): storage side was already tested; board side now has the `boardMatches` guard and its test (the `useLoad` ordering is asserted from source, not run: "met" with that caveat).
- Remove label wording (row 49): journey step on the remove-record dialog quotes `REMOVE_RECORD_NOTE` and checks nothing is removed before confirming.
- Manual paper outcomes with audit history (row 82): the history list and its tests (provider-verified settlement still does not exist, by design; say "met for manual").
- Long names / theses (row 102): journey long-content steps at 375 and 1280. Scrolling a long slip inside the phone sheet (row 103) was not checked.
- `vh` before `dvh`, no unguarded APIs (row 106): `hygiene.test.ts`, scoped to `src/betting/**` (the rest of the app is not audited).
- Standalone HTML with blocked storage (row 107): done for the switch-ON file; the scripts-disabled preview was not done.
- Select/deselect in the journey (row 115): done. Row 47 (add from the overview) stays "partly": item 4 skipped.

## Checks (all green, run unpiped, output in the scratch folder)
- `npm run check` in `app/web`, switch OFF and ON: eslint, tsc, vitest 56 files / 573 tests each, build.
- `node scripts/flag-parity.mjs`: OFF js 418,151 B, css 124,827 B; ON js 588,208 B, css 124,827 B; 18 markers; passed (artifact half skipped there because no baked data was present; the artifact scan ran inside `check.sh`).
- `app/scripts/check.sh --browser`: all pass (api pytest 184, route walk 158 s, violations 0). `--betting`: all pass (api pytest 170). `--browser --betting`: all pass (route walk violations 0).
- `app/scripts/visual.sh --quick --betting`: 148 captures, 0 gating findings.
- `bet-journey.mjs` on the ON server (frozen export, fresh output dir): JOURNEY PASSED at 375 and 1280, dark and light; 36 steps per run (new: deselect/reselect, recorded has no box, history, remove-record, long content x3), Learn/Explore show no betting UI.
- `tests/test_layering.py`: 15 passed.
- Switch-ON artifact built from the frozen export (6,699,781 B) and opened as a file with blocked storage.

## Decisions
- Cross-tab sync moved into the store as `externalChange`. Why: testable without a DOM and shares the store's serial queue (the superseded-load guard). Reverse: if a dropped event is ever seen, call `load` on window focus too.
- Schema version 2 for the history. Why: the brief asked for a migration step; the field is optional so version-1 files and ideas files still load. A version-2 export is refused by an older build (unsupported version, nothing changed). Reverse: keep version 1 and rely on the optional field.
- Memory fallback only when the very first read throws. Why: a never-readable store has nothing to lose; swapping after a good read would hide real saved data. A quota failure on write keeps the existing "Not saved, retry or discard" state. Reverse: show the old "could not be used" error and no in-memory slip.
- An identical outcome save adds no history line. Why: a log of non-changes hides the corrections.
- Discover add-to-slip skipped (item 4) rather than turning a Kalshi "N or more" threshold into a line.

## Not verified
- The ordering guard has no running-browser test of a slow earlier answer (no DOM test runner; a delayed-response journey step was not built).
- Cross-tab sync was tested through the store, not with two real tabs.
- Scripts-disabled preview of the artifact; scrolling a long slip inside the phone sheet; any screen reader.
- Item 9 beyond the long-content pages.

Servers: the API (8160) and web (8161 / 5310 / 5311 / 8161) processes I started are stopped; no listener remained on 8160, 8161, 5310 or 5311 at 05:52.
