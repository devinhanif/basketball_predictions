# Worklog: follow-up run (Jarvis look + betting tools behind one switch)

Brief: `/Users/devin/Downloads/jarvis-followup/FOLLOWUP_PROMPT.md` (kit files flat in that folder). Devin, 23:22 CST on
2026-11-16: run until 09:00 CST on 11-17; usage is not a constraint; "the most beautiful, usable, and comprehensive
experience for me and my friend". Follow-up integration branch `agent/app-jarvis-followup`, worktree
`~/Downloads/nba-wt-1117-jarvis`. Never merged to `main`, never pushed. Times are Central, from the clock.

## See it
- Follow-up branch, tonight's real export, switch OFF: http://127.0.0.1:5185 (API 8012), started 00:52 CST as background
  jobs of the session. Restart: `cd app/api && APP_EXPORT_DIR=/Users/devin/Downloads/nba-prediction/data/export/latest uv run
  uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8012` and `cd app/web && API_ORIGIN=http://127.0.0.1:8012
  npx vite --host 127.0.0.1 --port 5185`.
- Follow-up branch with the switch ON, tonight's real export: http://127.0.0.1:5186 (API 8013), started 01:02 CST as
  background jobs (`PH_BETTING=1 APP_EXPORT_DIR=... uvicorn ... --port 8013`; `VITE_PH_BETTING=1 API_ORIGIN=http://127.0.0.1:8013
  npx vite --host 127.0.0.1 --port 5186`). Or one command: `API_PORT=8013 WEB_PORT=5186 app/scripts/betting-on.sh --export`.
  Only 3 of the 8 games on 11-16 have stored quotes in the 19:35 export (those not yet tipped at export time).
- The first run's result is still at http://127.0.0.1:5184 (integration branch).

## Merged into the follow-up branch
| Lane | Branch | Merge | Verified by the main session |
|---|---|---|---|
| J3 betting pure logic | `agent/app-jarvis-betting-logic` | 8b062459 | web check 485 tests; bundle bytes unchanged |
| J1 the switch | `agent/app-jarvis-flag` | 50d6e9b6 | `check.sh` OFF and `--betting` ON both ALL CHECKS PASSED; layering 15 passed (636d7105, c92daef2) |
| J2a visual language | `agent/app-jarvis-look` | 2d89cf8e | `check.sh --fast --browser` OFF: ALL CHECKS PASSED, web 508 tests, walk 0 violations; looked at Discover on real data, phone and 1440 |
| J4a quote-board API | `agent/app-jarvis-betting-api` | d84cd759 | API suite OFF and ON: 604 passed, 1 skipped each; ruff and mypy clean; web money-safety test passes; under a real server (01:03 CST): `/api/betting/providers` 2,956 B, `/slate/2026-11-16` 161 props over 3 games, a 29-line board for a real player, and a typed 404 on the switch-off server |

Merge notes: both J1 and J3 created `betting/types.ts`; the seam's types now live in `betting/seam.ts`. `Shell.tsx` mounts
both `BetShellPanel` (J1's slot) and `SidePanelHost` (J2a's shared panel): the slip panel should move onto `SidePanel`
(`setSidePanel` / `clearSidePanel`) once the screens lane lands.

## Also merged (02:03 CST; tip 850a3cb7 + this worklog)
| Lane | Merge | Verified by the main session on the merged tree |
|---|---|---|
| J4b betting screens | 811634d5 | journey `scripts/bet-journey.mjs` against the switch-on preview on tonight's real export: PASSED at 375 and 1280, dark and light; I looked at Compare and the Slip Hedge Finder |
| J2b-A pages (Discover, Leg, Game, source, picks, swipe) | b90f8200 | web check OFF and ON 530 tests; walk 0 violations at 390, 390 injected, 360, 768, 1180 |
| J2b-B pages (record, scorecard, tests, live, parlay, signals, history, alerts, prefs, terms, admin) | 850a3cb7 | web check OFF and ON 530 tests; artifact checks passed; flag parity passed (OFF js 412,331 / css 115,705 / artifact 4,581,111; ON js 524,165 / artifact 4,692,938); walk 0 violations at all five settings |

Walker fix (4f92c2f7): `route-walk.mjs` derived its devtools port from the seed, so two walkers with the same seed drove each
other's browser. That was the cause of tonight's hangs and of the "flaky" not-found and blank-page violations lanes saw
under load. It now takes a free port from the system, stops with exit 3 if the browser stalls for 90 s, and `walk.sh`
retries that once.

| J4c betting polish, Ideas, Analysis | 21ac525e | merged 02:58 CST: web check OFF and ON 552 tests; flag parity passed (OFF js 412,329, within 2 bytes of before; ON js 576,514; ON artifact 4,745,279); extended journey against 5186 on real data PASSED (98 ok steps, 375 and 1280, dark and light); I looked at the compact Compare |

| J6 finishing pass | 895a7d79 | merged 03:10 CST: web check OFF and ON 558 tests; flag parity passed (OFF js 417,211 / css 123,240 / artifact 4,593,454; ON js 581,413 / artifact 4,757,646); walk 0 violations at all five settings; journey PASSED on real data; I looked at Discover (1440, Market, switch ON) and Compare (phone) |
| integration branch | 1a6567ab | merged 03:20 CST so this branch is a superset of the first run (CI health, math-diagnostics note, oracle rows, report); repo lint clean; layering 15 passed after 89c4e5fa (the betting import check had started matching ordinary strings in the new screens; it now reads real import statements only) |

Polish items seen by the main session in the merged betting screens (for the last pass): the slip rail's unit labels wrap
mid-word ("contra cts", "stak e"); a notice can sit over the middle of the Compare board.

## Merged after 03:30 CST
| Lane | Tip | State |
|---|---|---|
| J5 verification docs | 925451e4 | merged 03:32 CST (e99a562c): gap matrix (28 rows: 15 match, 7 partial, 5 target-only, 1 deferred, 0 missing), acceptance checklist (60: 35 met, 19 partly, 1 not met, 5 n/a), donor map, README section |
| J7 last polish | ea01450f | merged 04:44 CST (0e1d18a3): web check OFF and ON 558 tests; api 604 passed, 1 skipped OFF and ON; flag parity passed (OFF js 417,895 / css 124,827 / artifact 4,595,443; ON js 584,063 / artifact 4,761,601); layering 15 passed. Incident it reported: at 03:38 it killed a headless Chrome (PID 38040, profile `scratchpad/pages/ldprof`) that was not its own; no lane was running a learn-dom check at that time, so nothing needed a rerun |
| main session | e5b789b5, 5b94a4ea | 04:45 and 05:00 CST: the browser bar colour follows a theme picked in the app (test `headColor.test.ts`); the data line says "Forecast data made" and, outside the Market lens, shows only when the data is old (the line above it already gives the age, so a phone's first screen gains a row); one primary button on the slip page; Compare's slip summary counts picks waiting for a price. Web check OFF and ON 559 tests |

Verification of the tree at e5b789b5 (04:45 to 04:57 CST): walk 0 violations at all five settings; visual check quick OFF 120
captures and ON 148 captures, 0 with a gating finding; journey PASSED on the real export (98 ok steps). The same set was
rerun on 5b94a4ea (see REPORT_JARVIS.md section 3 for the last numbers).

## Running (wave 4, launched 05:01 CST, hard stop 07:10 CST)
| Lane | Branch | Worktree | Scope |
|---|---|---|---|
| K1 | `agent/app-jarvis-close` | `~/Downloads/nba-wt-1117-jv-close` | betting module only: cross-tab sync, select / deselect / remove in the journey, paper-outcome history, add to slip from Discover's cards, Compare ordering guard, long content, compatibility audit, blocked storage. Ports 8160-8161 / 5310-5311 |
| K2 | `agent/app-jarvis-friend` | `~/Downloads/nba-wt-1117-jv-friend` | everything except the betting module: a friend's first visit on a phone with the switch off on real data, then desktop; first-screen density, wording, empty states, rhythm. Ports 8170-8171 / 5320-5321 |

## Next
1. When K1 and K2 report (by 07:10 CST): merge each into the follow-up branch (`git merge --no-ff --no-commit`), then the
   gate: `npm run check` OFF and ON, `flag-parity.mjs`, API tests OFF and ON, layering, `check.sh --browser`,
   `walk.sh --widths`, `visual.sh --quick` and `--quick --betting`, the journey against 5186. Heavy work ends before
   08:20 CST (live jobs).
2. Rebuild the real-data single-file pages into `~/Downloads/parlayhitters-artifacts-2026-11-17/` (`artifact.sh --export`
   and `--export --betting`), refresh the numbers in `REPORT_JARVIS.md`, add decision rows, refresh the checklist rows K1
   closed.
3. Only then the completion line in `REPORT.md` on both branches.
4. If a lane does not report in time: leave its branch unmerged, say so in the report; the follow-up branch at 5b94a4ea
   is already verified and complete on its own.

## Decisions so far (rows go to DECISIONS_ROWS.md at the end)
- "fast arb" (Devin, about 22:55 CST): JARVIS Polymarket fee uses fast-arb's order. On the integration branch, row 16.
- Betting routes live under `#/bet/*`; the existing `#/hedge` page is untouched.
- A planned price is stale after 15 minutes; cross-venue coverage needs a verified settlement-rule id (none exists yet).
- The first-run Simple/Analyst chooser is step one of the in-page intro; it never blocks; skipping keeps Simple.
- The Jarvis look is one stylesheet (`jarvis.css`); removing its import reverts it.
