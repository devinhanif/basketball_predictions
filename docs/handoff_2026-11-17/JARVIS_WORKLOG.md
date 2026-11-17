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

## Running (wave 3, launched about 02:03 CST)
| Lane | Branch | Worktree | Scope |
|---|---|---|---|
| J4c | `agent/app-jarvis-ideas` | `~/Downloads/nba-wt-1117-jv-ideas` | betting polish (slip onto shared SidePanel, compact Compare and Hedge Finder, toasts, hub, dollars formatter), Ideas, minimal real Analysis, extended journey |
| J6 | `agent/app-jarvis-polish` | `~/Downloads/nba-wt-1117-jv-polish` | shared-component inbox (card-feature contrast, Table stacked layout, MarketOnly headings, palette lens filter), unrestyled components, whole-product design review (first run, consistency, navigation grouping) |
| J5 | `agent/app-jarvis-verify` | `~/Downloads/nba-wt-1117-jv-verify` | `visual-check.mjs` (structural assertions + local pixel baseline), donor map, filled gap matrix and acceptance checklist, README section |

## Next
1. As each reports: merge into the follow-up branch (J4c and J6 may both touch shell-adjacent files: expect small conflicts),
   then `npm run check` OFF and ON, artifact build, `flag-parity.mjs`, `app/scripts/walk.sh --widths`, the journey against 5186,
   layering test, and look at screenshots.
2. Ask J5 (SendMessage) to refresh the gap matrix and checklist rows that were pending J4c / J6; run its visual check on
   the final tree.
3. Final: merge `agent/integration-2026-11-17` into this branch (late docs commits), build the real-data artifact OFF and ON
   with `app/scripts/artifact.sh --export [--betting]`, restart the previews on 5185 (OFF) and 5186 (ON), write
   `REPORT_JARVIS.md` and the DECISIONS rows, and only then the completion line in `REPORT.md` on both branches.

## Decisions so far (rows go to DECISIONS_ROWS.md at the end)
- "fast arb" (Devin, about 22:55 CST): JARVIS Polymarket fee uses fast-arb's order. On the integration branch, row 16.
- Betting routes live under `#/bet/*`; the existing `#/hedge` page is untouched.
- A planned price is stale after 15 minutes; cross-venue coverage needs a verified settlement-rule id (none exists yet).
- The first-run Simple/Analyst chooser is step one of the in-page intro; it never blocks; skipping keeps Simple.
- The Jarvis look is one stylesheet (`jarvis.css`); removing its import reverts it.
