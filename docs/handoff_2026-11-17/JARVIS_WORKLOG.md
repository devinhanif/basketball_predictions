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
- With the switch ON: `API_PORT=8013 WEB_PORT=5186 app/scripts/betting-on.sh --export` (after the screens merge).
- The first run's result is still at http://127.0.0.1:5184 (integration branch).

## Merged into the follow-up branch
| Lane | Branch | Merge | Verified by the main session |
|---|---|---|---|
| J3 betting pure logic | `agent/app-jarvis-betting-logic` | 8b062459 | web check 485 tests; bundle bytes unchanged |
| J1 the switch | `agent/app-jarvis-flag` | 50d6e9b6 | `check.sh` OFF and `--betting` ON both ALL CHECKS PASSED; layering 15 passed (636d7105, c92daef2) |
| J2a visual language | `agent/app-jarvis-look` | 2d89cf8e | `check.sh --fast --browser` OFF: ALL CHECKS PASSED, web 508 tests, walk 0 violations; looked at Discover on real data, phone and 1440 |

Merge notes: both J1 and J3 created `betting/types.ts`; the seam's types now live in `betting/seam.ts`. `Shell.tsx` mounts
both `BetShellPanel` (J1's slot) and `SidePanelHost` (J2a's shared panel): the slip panel should move onto `SidePanel`
(`setSidePanel` / `clearSidePanel`) once the screens lane lands.

## Running (launched about 00:40–01:00 CST)
| Lane | Branch | Worktree | Scope |
|---|---|---|---|
| J4a | `agent/app-jarvis-betting-api` | `~/Downloads/nba-wt-1117-jv-betapi` | `/api/betting/providers`, `/board/{game}/{player}/{stat}`, `/slate/{date}`: stored quote snapshots, switch ON only, no model number, no ranking |
| J4b | `agent/app-jarvis-betting-screens` | `~/Downloads/nba-wt-1117-jv-screens` | rolling slip, add-to-slip, Compare, Hedge Finder, `#/bet` hub, journey script |
| J2b-A | `agent/app-jarvis-pages-a` | `~/Downloads/nba-wt-1117-jv-pagesA` | restyle Discover, Game, Leg family, Swipe, My picks; Analyst detail |
| J2b-B | `agent/app-jarvis-pages-b` | `~/Downloads/nba-wt-1117-jv-pagesB` | restyle Track record, Scorecard, Forward tests, Live, Parlay, Signals, History, Alerts, Preferences, Key terms, Error help, Admin |

## Next
1. As each reports: merge into the follow-up branch (expect small conflicts in `Leg.tsx` / `Game.tsx` between J4b's slots and
   J2b-A, and in `main.tsx` / `main.artifact.tsx` imports between J2b-A and J2b-B), then `app/scripts/check.sh --browser` OFF and
   `--betting` ON, `app/scripts/walk.sh --widths`, layering test, and look at screenshots.
2. After J4a + J4b: verify the screens against the real board routes; move the slip panel onto `SidePanel`; then J4c (Ideas,
   minimal Analysis view on real export fields, import/export UI) and the kit tests that still apply.
3. J5: screenshot-based checks at 375/768/1280 in both themes and modes; `docs/jarvis-donor-map.md` and the filled GAP matrix;
   README section; `REPORT_JARVIS.md`. Then merge the integration branch's late docs commits into this branch, build the
   real-data artifact with `app/scripts/artifact.sh --export`, and only then write the completion line in `REPORT.md`.

## Decisions so far (rows go to DECISIONS_ROWS.md at the end)
- "fast arb" (Devin, about 22:55 CST): JARVIS Polymarket fee uses fast-arb's order. On the integration branch, row 16.
- Betting routes live under `#/bet/*`; the existing `#/hedge` page is untouched.
- A planned price is stale after 15 minutes; cross-venue coverage needs a verified settlement-rule id (none exists yet).
- The first-run Simple/Analyst chooser is step one of the in-page intro; it never blocks; skipping keeps Simple.
- The Jarvis look is one stylesheet (`jarvis.css`); removing its import reverts it.
