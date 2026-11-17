# Jarvis acceptance checklist, marked against parlayhitters

The kit's `docs/ACCEPTANCE_CHECKLIST.md` (flat copy `kit__docs__ACCEPTANCE_CHECKLIST.md`), every item marked against
the real app on branch `agent/app-jarvis-verify` (worktree head `850a3cb7`, lanes J1, J2a, J2b-A, J2b-B, J3, J4a and J4b
merged), 2026-11-17. States: **met** (evidence looked at tonight: a test that ran green tonight, a journey step, or a
screenshot), **partly**, **not met**, **not applicable** (the app has no such thing, by decision). Refreshed after the Ideas lane (J4c) and the polish lane (J6) merged (`f9874a78`): web vitest 558 passed in 54 files
with the switch off and on, and `bet-journey.mjs` passed on fixtures (screenshots in
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/jv-journey2/`). Strict reading:
"met" needs evidence seen tonight; a lane report's claim alone is "partly" at most. Rows that depend on unmerged work
say **pending the last polish lane**.

Evidence seen tonight by this lane: `app/scripts/check.sh --visual` OFF and `--betting --visual` ON (lint, types, every
web and API test, builds, flag parity, the visual structural layer; results in
`docs/handoff_2026-11-17/lane_jarvis_verify.md`), `app/scripts/visual.sh` OFF and `--betting` (360 and 444 captures,
screenshots under `app/web/.visual/`), and `app/web/scripts/bet-journey.mjs` against fixtures with the switch on.
Test names are quoted as `file` "describe" > "it".

## Preserve the working application

| Item | State | Evidence or gap |
|---|---|---|
| Record the target revision and run its existing build, model/service, and UI checks before modifying it. | partly | The lanes recorded their bases and ran `check.sh` before and after (lane reports J1 to J4b, e.g. J1: api 560 / web 408 before). This lane ran the checks after, not before (it modified no application code). |
| Existing inference inputs/outputs and model call timing remain compatible; no alternate model or duplicate connection is introduced. | met | The betting routes serve no model number (`model_ref` only); API tests ran green tonight in both switch states (`check.sh` api pytest), including `app/api/tests/test_betting_board.py` (existing payloads identical OFF and ON). No model code under `app/web/src/betting/` (`app/web/src/betting/hygiene.test.ts` green). |
| Existing useful target-only screens, controls, metrics, and model features remain accessible. | met | Every existing route captured tonight at 375/768/1280, both themes, both modes (`app/scripts/visual.sh`); `app/web/src/bettingSafety.test.ts` "existing routes are untouched, including the Hedge Finder" green. |
| The host router, authentication/session flow, permissions, query/cache library, and deployment configuration are reused. | met | Betting screens register in the existing hash router through `app/web/src/betting/index.ts` (`ROUTES`) and fetch through the app's `api.ts`; no second router, query library or config. There is no authentication to reuse (no accounts). |
| Do not copy the donor's private hosting identity, demo account directory, `Jarvis` client-side gate, or local sample seed into real authentication/holdings. | met | `bettingSafety.test.ts` money-safety grep green tonight; the sample loads only from the labelled button (`app/web/src/betting/repository.test.ts` "sample only on explicit action" green); no `Jarvis` password anywhere in `app/` (`app/web/src/desk.test.ts` "admin gating (never a password)" green). |

## Data mapping and live-state behavior

| Item | State | Evidence or gap |
|---|---|---|
| Run at least one complete scenario using a player, market, and event ID outside `bobby/oso/jalen/collin`. | met | `app/web/src/betting/screens.test.ts` "an arbitrary non-sample player works end to end (export market id, typed prices, placed, hedged)" green; `bet-journey.mjs` tonight on the fixture player (see the verify report). |
| Do not require 24 quote slots. A missing provider/side displays unavailable and leaves unrelated data usable. | met | `app/web/src/betting/catalog.test.ts` "partial availability" (both cases) green. |
| Distinguish an empty valid feed from loading, unauthenticated, permission denied, transport failure, malformed payload, stale data, and disconnected subscriptions. | partly | Empty board, loading, transport failure, malformed payload and stale are distinct (`screens.test.ts` "an empty board is a designed state, and other stored lines are offered"; visual capture `api-down`; the route walk's garbage-200 mode). Unauthenticated and permission denied do not exist (no accounts); the betting module has no subscription. |
| Failed or invalid live responses never invoke the prototype's initial/sample fallback as if it were current account data. | met | `repository.test.ts` "an invalid load never substitutes a seeded demo or overwrites an acknowledged document" and `app/web/src/betting/slipModel.test.ts` "missing or invalid data is refused, not replaced by an empty or sample workspace" green. |
| Preserve explicit units: model probabilities, American odds, cents per contract, currency stake, gross payout versus net profit, fee unit, timestamps, and timezones. | partly | Odds, cents, stake, gross vs net and fee basis are typed (`QuotePrice`, `Fee` in `app/web/src/betting/types.ts`) and tested (`slipModel.test.ts` "a sportsbook stake pays principal plus winnings for either odds sign", "a sportsbook fee is charged per $1 of gross payout"). Zone-less instants are refused (lane J3 decision 8). No probabilities in the module. Exchange cents are converted from stored American odds (lane J4b decision 4), not native. |
| Match markets by stable identities and semantics, not display names. | met | `catalog.test.ts` "a different event, date, period, line or settlement rule, or an unverified rule, blocks comparison" green. |
| Quote refresh updates the board only. Existing position entry prices and saved idea snapshots do not move with it. | met | `slipModel.test.ts` "a quote refresh moves the board only: planned legs, recorded positions and saved ideas keep their prices" green. |
| Out-of-order refresh/subscription responses cannot overwrite newer revisions. Unmount and account changes cancel/ignore in-flight work. | met | Storage side: `repository.test.ts` "a superseded load cannot overwrite a newer snapshot" and "cancel ignores a late save answer, and stage copies what it is given" green. Board fetches in Compare: no out-of-order test seen. Update 05:53 CST (lane K1): a board fetched for another prop or line is read as loading, never shown against the new market (`closeLane.test.ts`, the `boardMatches` guard in `useStored` and `useRawBoard`); `useLoad` drops a superseded answer (asserted from source, not run in a browser). |
| Manual, illustrative, provider quote, backend-confirmed fill, and model prediction provenance remain distinguishable. | partly | live/delayed/manual/sample provenance badges (`app/web/src/betting/parts.tsx`, `jarvisKit.test.ts` "provenance" green). Backend-confirmed fills do not exist (no execution); recorded positions are manual records. |

## Rolling slip and placement-to-hedge flow

| Item | State | Evidence or gap |
|---|---|---|
| In explicit demo mode, load the labeled Jalen Green sample: planned DK YES, $10 at +145. No real account is seeded. | met | `slipModel.test.ts` "the NBA sample is one planned Jalen Green DraftKings YES, $10 at +145, $24.50 gross" green; visual capture `bet-slip-demo` (loaded through the "Load the labelled demo" button, which loads `sampleWorkspace()`: the same Jalen draft, `buildNBASampleDraft()` in `app/web/src/betting/sample.ts`). |
| Add Bobby from the overview to the same active slip; adding again does not silently duplicate the same planned quote. | partly | `slipModel.test.ts` "re-adding the same planned quote re-enables it instead of duplicating" and `app/web/src/betting/store.test.ts` "a pick added survives a reload, and adding it again does not duplicate it" green. The add action is on Leg and Game, not the overview (Discover). |
| Planned legs can be selected, deselected, edited, and removed; each change recalculates cost/exposure. | met | `slipModel.test.ts` "switched-off planned legs are excluded, recorded exposure is never hidden" green; the slip page's toggles and amounts in `app/web/src/betting/Slip.tsx`. |
| Existing positions remain included. Removing one from a notebook is labeled as removing the record, not canceling the external wager. | met | Included: same test as above. The remove label's wording was not checked tonight. Update 05:53 CST (lane K1): the journey removes a recorded position and asserts the `REMOVE_RECORD_NOTE` wording is on screen before anything is removed (screenshot `14_remove-record_375_dark.png`). |
| Route changes preserve the active slip. A successful save survives reload; failed saves preserve the last committed record and show a recoverable error. | met | `bet-journey.mjs` step 6 (reload keeps 2 recorded legs), run tonight; `store.test.ts` "a failed save keeps the edit on screen (unsaved), keeps stored data, and retry saves it" green. |
| "Mark slip as placed" retains each selected leg's ID, original quote snapshot, side, and native amount. Excluded planned legs are not marked placed. | met | `slipModel.test.ts` "markPlaced records only switched-on planned legs and keeps ids, quotes, sides and amounts" green. |
| That action opens Hedge Finder with the exact selected holdings and the opposite side of the selected market; it never resets to the hardcoded Collin example. | met | `screens.test.ts` "Jalen DK YES $10 at +145 pays $24.50; add Bobby; mark placed keeps ids, quotes and amounts; Kalshi NO hedge is 25 contracts" green; `bet-journey.mjs` step 5 (the Hedge Finder chip shows the journey's own player). |
| Selecting a different held position updates the hedge market and side. A Jalen DK $10 +145 holding exposes $24.50 gross payout; an opposite Kalshi hedge rounds to 25 whole contracts. | met | `app/web/src/betting/hedgeMath.test.ts` "a recorded Jalen DraftKings $10 at +145 exposes $24.50; the Kalshi NO hedge rounds up to 25 whole contracts" green. |
| Pending, submitted, rejected, canceled, and partially filled host orders retain their real lifecycle states. Only confirmed fills become verified exposure; manual "already placed" records stay marked manual. | not applicable | No orders exist or can exist (brief section 2: no execution code). Recorded positions are always manual records. |
| No new real order submission is introduced by this UI integration. | met | `bettingSafety.test.ts` "no file contains a forbidden word, credential, network channel or write route" green; `store.test.ts` "mark placed is a local record: no network call of any kind" green. |

## Price, payout, and eligibility correctness

| Item | State | Evidence or gap |
|---|---|---|
| Exact donor regression: DK YES $40 at +150 pays $100 gross; Kalshi NO 100 at 52c + 1c costs $53; combined $93; both outcomes net $7. Illustrative. | met | `slipModel.test.ts` "the demo gives exact payouts ($93 cost, $100 either way, net +$7) but no coverage claim: settlement is unverified" green. |
| Equal-$1-payout comparison: DK NO -122 costs 54.954955c; Kalshi NO 52c + 1c costs 53c; difference 1.954955c. | met | `screens.test.ts` "DK NO -122 costs 54.954955c per $1; Kalshi NO 52c + 1c costs 53c" and `slipModel.test.ts` "same payout comparison includes exchange fees and converts American odds (53c vs 54.954955c)" green. |
| Unequal quantities reveal residual downside: 50 NO contracts against $100 YES payout: $66.50 cost, -$16.50 worst, +$33.50 best. | met | `slipModel.test.ts` "unequal amounts expose residual payout and keep downside (-$16.50 / +$33.50 on $66.50)" green. |
| Fees use the actual provider fee model where available; not silently constant. | met | Fees are `unknown` unless the viewer types one (`catalog.test.ts` "a neutral example with an unknown fee and unknown rules is accepted as-is, with nothing filled in"; `slipModel.test.ts` "an unknown fee stays unknown: no normalised cost, no leg cost, no table, no coverage" green). |
| Opposing same-platform positions do not sum mutually exclusive payouts into an impossible "maximum payout." | met | `slipModel.test.ts` "a provider's maximum payout counts mutually exclusive opposing legs once" green. |
| Unknown settlement equivalence, stale quotes, unavailable liquidity, unsupported outcomes, or incomplete entry terms prevent guaranteed-arbitrage language. | met | `screens.test.ts` "a stale planned price suppresses the coverage block and says why" and `slipModel.test.ts` "a stale planned price suppresses coverage; a fresh one shows it; a recorded entry price never goes stale" green; liquidity is listed `unknown` by the API (`docs/contracts/betting_board_v1.md`). |
| Incomplete PrizePicks entries suppress combined best/worst/coverage claims. Single picks have no standalone contract-price ranking. | met | `slipModel.test.ts` "an incomplete entry blocks the table and the coverage summary", "an entry pick never yields a per-pick price, cost or payout" green. |
| PrizePicks group stake charged once; 2-pick/$10/3x pays $30 gross only when all win (net +$20), else -$10. | met | `slipModel.test.ts` "an entry is priced once and pays only when every grouped pick wins (2 picks, $10, 3x)" green. Flex tiers, voids, promotions: not modelled (no verified terms). |
| Contradictory/duplicate selections, invalid odds/quantities, nonfinite numbers, and unsupported markets produce explicit validation. | met | `slipModel.test.ts` "duplicate or opposing picks in one market are not a valid entry", "non-finite or invalid American odds never produce a cost", "invalid stored prices, amounts, ids and tags fail validation with the path that failed" green. |
| Larger catalogs do not trigger unbounded `2^N` work. | met | `hedgeMath.test.ts` "enumeration cap" (both cases) green; cap 10 markets. |

## Ideas, synergy, and historical data

| Item | State | Evidence or gap |
|---|---|---|
| Title, thesis, tags, linked selections, and snapshot timestamps save and reload per user. | partly | Saved and reloaded per device scope (no accounts exist, so "per user" cannot be shown): `ideaModel.test.ts` "survives edits to the slip and reloads, and loading gives a copy" green on 2026-11-17 (vitest 558, switch off and on); journey step "saved as an idea with a thesis and two tags"; scopes isolated by `repository.test.ts` "one viewer never sees another viewer's ... data". |
| Shared-player and shared-game exposure is visible alongside the thesis; no invented synergy score. | met | Idea page "Shared exposure" under the thesis, seen in journey screenshot `25_idea-settled_375_light.png`; `slipModel.test.ts` "overlaps are words only: no score, weight or probability field" green. |
| Any existing real model correlation/relationship output retains its own provenance, method/version, timestamp, and uncertainty. | not applicable | The export carries no correlation output, and the module computes none. |
| Saving an idea makes an independent snapshot. | met | `slipModel.test.ts` "legs, ideas and validated copies do not share references with the board or the draft" green. |
| Loading an idea copies it into the draft and confirms replacement when appropriate; the saved original remains intact. | met | Journey step "a copy loaded into the slip (cancel returned focus)" (confirmation shown, cancelled once, then loaded); `ideaModel.test.ts` "through the store: save, clear the slip, then loading the idea brings its legs back and keeps the idea" green. |
| Recorded results require an outcome for every active market. Results use saved prices and the full grouped-entry payout. | met | `slipModel.test.ts` "a settled idea needs an outcome for every active market"; `ideaModel.test.ts` "settling needs every active market; reopening to paper clears the outcomes", "the paper net uses the saved prices only, whatever the slip or typed prices say now" green; journey step "outcomes recorded; paper net shown with its label". |
| Manual paper outcomes remain distinguishable from provider-verified settlement, including corrections/reopening and audit history. | met | Labelled as typed by hand, unverified and "not a venue's settlement" (journey screenshot `25_idea-settled_375_light.png`); reopening: `ideaModel.test.ts` "settling needs every active market; reopening to paper clears the outcomes". No audit history of corrections exists. Provider-verified settlement does not exist. Update 05:53 CST (lane K1): every recorded, corrected or reopened outcome appends to the idea's history (`SavedIdea.history`, workspace schema v2 with a 1-to-2 migration; tests in `closeLane.test.ts`), shown on the idea page as "History of this record", labelled typed by hand. Provider-verified settlement does not exist, by design. |
| History, P&L, balance, CLV, calibration, and protection status come from the target's actual services or an honest unavailable state. | met | `app/web/src/pages/History.tsx` reads the desk export; `desk.test.ts` "rates carry n and an interval, or say they are not enough to say" and "calibration renders nothing (the honest building state) when there are no bins" green; captures `history`, `record-cal`, `scorecard` tonight. |

## Identity, Admin, media, and persistence

| Item | State | Evidence or gap |
|---|---|---|
| Mode choice offered after the user's first sign-in and remembered per account. | not applicable | No accounts or sign-in. The chooser is offered on first visit and remembered per device (`app/web/src/Welcome.tsx`, `ph.mode`); a test of the remembered choice: none yet. |
| Admin and photo mutation APIs enforce the host's permissions on the server. | not applicable | No server write routes exist (GET-only API; `bettingSafety.test.ts` forbids non-GET routes in the betting package). Photos are stored in the browser. |
| Player library remains inside Admin; photos and identity agree across surfaces. | partly | Library inside Admin: met (`app/web/src/pages/Admin.tsx`, capture `admin`). Agreement across surfaces: one `Avatar` component by NBA player id; no cross-surface test. |
| Approved remote/CDN/signed photo URLs are supported by an explicit media contract; failures degrade gracefully. | partly | The NBA headshot by id, with initials on failure (`app/web/src/Avatar.tsx`); every visual capture tonight ran with the headshot host blocked and showed initials. No signed-URL contract (none needed now). |
| Workspace/idea writes are user-scoped, versioned, and concurrency-aware. | met | `repository.test.ts` "compare-and-set: a save based on an old revision is a conflict and changes nothing", "the same idempotency key replays the earlier answer; reusing it for a different change is refused", "one viewer never sees another viewer's ... data" green. |
| Test import migration using a legacy donor export without `slip`, preserving photos/preferences. | partly | `store.test.ts` "a workspace written before picks existed still loads (picks read as empty)" and `repository.test.ts` "migration runs version steps in sequence ..." green. A donor-format export is not accepted (different schema, by design); photos and preferences are not in the betting workspace. |
| Invalid/oversized import and quota/network failures preserve current data. Exports omit credentials/session tokens. | met | `store.test.ts` "an invalid import changes nothing and says why; a valid one replaces the workspace"; `repository.test.ts` "a failed save (storage full) returns save_failed and keeps the stored data exactly as it was", "export carries no viewer id; import validates and, when invalid, preserves current data" green. |

## Browser, mobile, and accessibility

| Item | State | Evidence or gap |
|---|---|---|
| Verify client boundaries/SSR. | not applicable | Client-only SPA, no server render. |
| Exercise 375 / 768 / 1280+ layouts with a populated slip and long player names/theses. | met | 375/768/1280, both themes, both modes: captures `bet-slip-demo` and `bet-hedge-demo` (populated with the demo) tonight, plus `bet-journey.mjs` at 375 and 1280. Long names and long theses were not exercised. Update 05:53 CST (lane K1): the journey uses a 40-character name, a 600-character thesis and two 60-character unbroken tags on the slip, the ideas list and the idea page at 375 and 1280, dark and light: no page scroll, no control outside the viewport (`15_long-slip`, `16_long-ideas`, `17_long-idea`). The outcome table stacks below 640 px (ce624bd3). |
| Desktop slip stays alongside content; phone/tablet overlay scrollable, including close and placement confirmation. | met | Desktop: the slip is a rail beside the page from 1280 px (journey screenshot `35_hedge_1280_dark.png`; `notice.test.ts` "rail when wide and open, the Slip button when wide and hidden, a sheet below 1280 px" green). Phone: the shared sheet (focus trap, Escape) and the placement dialog pass the journey at 375; scrolling a long slip in the sheet was not checked. Update 06:44 CST (lane K4): the journey seeds 8 planned legs, opens the phone sheet at 375, and checks it fits the screen, scrolls inside, and that "Mark slip as placed" can be scrolled into view. |
| Phone keyboard does not cover required actions; inputs can be cleared and retyped. | not met | Not checked: headless Chrome has no on-screen keyboard. |
| Keyboard navigation, visible focus, dialog focus trapping/restoration, Escape, labels, screen-reader state changes. | partly | Visible focus on the first Tab stop: every visual capture on the first pass. Dialog focus in and back: journey steps "focus returned after Cancel", "typed prices in dialogs; focus back on each row". No screen reader used. Touch targets under 44 px at 375 (first-pass visual check): **pending the last polish lane**. Update 04:56 CST: the touch-target findings are fixed (visual check quick set, 0 gating findings off and on). Still no screen reader. |
| Preserve `vh` fallback before `dvh`; avoid unguarded `structuredClone`, `crypto.randomUUID`, `Array.at`. | partly | `app/web/src/betting/ids.ts` replaces the donor's id helper (lane J3). Not audited tonight for `dvh` order or `Array.at` across the app. Update 05:53 CST (lane K1): enforced for `app/web/src/betting/` by a new `hygiene.test.ts` test (no `dvh` without `vh` first, no unguarded `structuredClone`, `crypto.randomUUID`, `Array.at`). The rest of the app is not audited. |
| If distributing standalone HTML, test the actual bundle with blocked localStorage and missing newer APIs; a scripts-disabled preview is honest. | partly | The single-file build passes its forbidden-text scan in both states (`check.sh` artifact step tonight). Blocked storage: prefs fallback test. The artifact's betting pages were never opened in a browser (lane J4b), and no scripts-disabled check exists. Update 05:53 CST (lane K1): the switch-ON single-file page was opened as a file with `localStorage` and `sessionStorage` throwing: all six betting screens show "This browser cannot save your slip." and keep working in memory (3 unit tests). Lane J7 opened both single-file pages as files at 375 and 1280. A scripts-disabled preview was not checked. |

## Completion report

| Item | State | Evidence or gap |
|---|---|---|
| Updated gap matrix contains target evidence, implemented/deferred decisions, and preserved target-only features. | met | `docs/jarvis-gap-matrix.md`. |
| List changed files, adapters and units, services reused, schema/migration decisions, tests run, remaining provider/data limitations. | partly | Per lane in `docs/handoff_2026-11-17/lane_jarvis_*.md`; the overall `REPORT_JARVIS.md` is the main session's. |
| Demonstrate add-to-slip → select/deselect → record placed / real fill → current-position hedge comparison → saved idea → recorded result. | met | `bet-journey.mjs` passed on 2026-11-17 (fixtures, 375 and 1280, dark and light): add to slip, compare, typed prices, mark placed, Hedge Finder, reload, save as an idea, outcomes recorded with the paper net, load a copy, export/import. Select/deselect is unit-tested only (`slipModel.test.ts` "switched-off planned legs are excluded, recorded exposure is never hidden"), not in the journey; a real fill is not applicable (no execution). Update 05:57 CST: the journey now unchecks a planned leg (total $10.98 to $10.00), re-checks it, shows a recorded position has no include box, and removes a record; passed on the real export, 138 steps. |
| Report browser/device checks separately from DOM/unit checks. | met | The verify report separates headless-Chrome checks (visual, journey) from vitest; no physical device was used. |

## Counts by state

| State | Items |
|---|---|
| met | 41 |
| partly | 13 |
| not met | 1 |
| not applicable | 5 |
| Total | 60 |

Nothing is pending a lane. Refreshed 05:59 CST after the last polish pass (`0e1d18a3`) and the checklist lane
(`1f05972b`, `ce624bd3`): six rows moved from partly to met (out-of-order boards, remove-record wording, paper-outcome
history, long content, the full journey, and at 06:44 the scrolling phone sheet). The one "not met" is the phone on-screen keyboard (headless Chrome has none).
