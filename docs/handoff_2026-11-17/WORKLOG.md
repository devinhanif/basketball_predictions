# Worklog: autonomous run 2026-11-16 evening → 11-17

Integration branch `agent/integration-2026-11-17`, worktree `/Users/devin/Downloads/nba-wt-int-1117`. Nothing is merged
to `main`, nothing is pushed. Times are Central.

## Quickstart: one command (verified 19:13 CST from the integration worktree)

```
app/scripts/dev.sh                         # API on fixtures + web; prints the URL; Ctrl-C stops both
app/scripts/dev.sh --export                # the nightly export, read-only (data/export/latest of this checkout, or --export <dir>)
API_PORT=8040 WEB_PORT=5196 app/scripts/dev.sh   # when 8000/5173 are busy (they were tonight)
app/scripts/check.sh                       # every local app check with a pass/fail table (--fast, --bake, --browser)
app/scripts/artifact.sh [--export [DIR]]   # bake + build the single-file artifact
```

## The same by hand (verified 2026-11-16 18:48–18:56 CST)

```
# API on fixtures (two 2024-25 nights)
cd app/api && uv sync && uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8010
# API on the real export, read-only (second terminal, optional)
cd app/api && APP_EXPORT_DIR=/Users/devin/Downloads/nba-prediction/data/export/latest \
  uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8011
# web dev server, pointed at whichever API you want
cd app/web && npm ci && API_ORIGIN=http://127.0.0.1:8010 npx vite --host 127.0.0.1 --port 5183
# single-file artifact (bake reads the export read-only, then build)
cd app/api && APP_EXPORT_DIR=<export dir> PYTHONPATH=src uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json
cd app/web && npm run build:artifact      # -> dist-artifact/parlayhitters.html (+ preview.html)
# checks
cd app/web && npm run check               # eslint, tsc, vitest, build
cd app/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Open http://127.0.0.1:5183 (fixtures) or http://127.0.0.1:5184 (real export). Ports 8000 and 5173 were already taken by an
app started from the main checkout at 18:45; it was left alone, so this run uses 8010/8011 and 5183/5184.

## Phase 0 report

- `/api/health`: 200 `{"status":"ok","mode":"fixture_replay"}` on fixtures; 200 `{"mode":"export"}` on the real export
  (manifest `data_version 764432af8422`, made 2026-11-17T00:35Z, slates 11-09 … 11-16). Manifest and sha validated.
- Every API route answered on both: 200 everywhere except `/api/desk` 404 (no desk file, not private), `/api/top-parlays/{date}`
  404 (no file), `/api/live/{game}/snapshot|plays` 404 (no capture). Largest body: `/api/games/{id}` 255 KB on the export, 6 ms.
- Browser (headless Chrome over CDP, 390x844): today, game, leg, swipe, picks, parlay, record (3 tabs), live, signals, hedge,
  history, alerts, admin, prefs all render on fixtures; today, game, leg, swipe, parlay, record, live, signals, alerts on the
  real export. No blank page, no uncaught exception.
- Console errors seen: 404 for `/api/desk` on every page and for `/api/top-parlays/{date}` on Discover (the page asks for
  optional files and the browser logs the 404); `cdn.nba.com` headshots fail in headless Chrome (ERR_HTTP2_PROTOCOL_ERROR;
  the avatar falls back to initials). Not checked in a real, headed browser.
- Does not behave well: an unknown hash (`#/zzz`) silently shows Discover instead of a not-found screen; the artifact's
  Track record page scrolls sideways at 390 px.
- **The single-file artifact build was broken on `main`**: its own check refuses any `https://` text and the headshot host
  (commit 0cc5183c) tripped it. Fixed on this branch with a stand-in module for the artifact build (initials there). Built:
  5.51 MB, loads from `preview.html` with 0 console errors on discover, game, leg, record, live, swipe, picks.
- Baseline: web 319/319 vitest, eslint and tsc clean, build 321 KB js + 68 KB css. API ruff, format and mypy clean,
  pytest 434 passed, 1 failed (`test_the_committed_export_is_what_the_real_exporter_builds`: the real exporter now writes a
  `teammate_sits` table the committed fixture manifest lacks).

## Done
- **First run finished** (00:38 CST): `make test-fast` on the merged integration branch: 5,905 passed, 13 skipped, 284 deselected, 1 xfailed, exit 0, 21:45 min. `REPORT.md` is complete except for the completion line (held back on purpose, see the follow-up section).
- Follow-up: J1 (switch) merged into `agent/app-jarvis-followup` as 50d6e9b6 (conflict: seam types moved to `betting/seam.ts`). On that branch: `app/scripts/check.sh` OFF and `--betting` ON both ALL CHECKS PASSED (API 582/581+1 skipped, web 495, flag parity pass); layering 15 passed after allowing test files to import the test runner. J4a `agent/app-jarvis-betting-api` (`~/Downloads/nba-wt-1117-jv-betapi`) and J4b `agent/app-jarvis-betting-screens` (`~/Downloads/nba-wt-1117-jv-screens`) launched about 00:40 CST. J2a (visual) still running.
- Lane E1 (CI health) merged as 8a6394af at 00:10 CST on 11-17. On the merged branch I ran `ruff check`, `ruff format --check` and `mypy` on nba/ research/ tests/: all exit 0. `make test-fast` started on the merged branch at 00:11 CST as a background job (log: scratchpad `int_fast.log`); result goes into `REPORT.md` section 2.
- Phase 6 note merged (docs only): TDA regime flag PARK, spectral parlay graph DROP (its draft rule fails its own gate: 0 multi-leg parlay records).
- Follow-up: J3 (betting pure logic) merged into `agent/app-jarvis-followup` as 8b062459; web check there 485 tests, bundle bytes unchanged.
- Lane E2a (daily/props) merged as d375aeb9 at 23:10 CST: lead constant (`f20c535a`), pplay shadow config path (`fe5c7efb`), DDL and config parity guard tests, stub docstrings. Its own runs: tests/daily 365 passed, layering 10 passed. `forward_withdrawals` exists only in `nba/daily/store.py`, not `schema.sql` (xfail test documents it). Pre-existing failure it met: `tests/shadow_llm/test_runner.py::test_mixed_forward_table_still_settles` (lane E1 owns it).
- Fee decision branch `agent/market-1117-polymarket-fee-fastarb` (e05667b1) merged as 59625c3c: fee tests + tests/jarvis + layering 504 passed, 1 skipped, exit 0.
- Open wording item for the app: the health strip's "Stale-model marks: props_avg_live2 15,276 (writer_flag); shown rows on a stale fallback 6,604" reads as if the app were stale. It means the AVG shadow is on October weights and the export fell back to the production row. Reword in `app/web/src/HealthStrip.tsx`.
- Lane E2b (markets cleanups) merged at 22:10 CST. Verified by me on its branch: fee differential test, fee-config guard and layering test pass (17 passed), ruff clean on the five packages, `git diff configs/` is comment lines only and the parsed YAML of `jarvis.yaml` and `cost.yaml` is equal before and after. The lane's own runs: tests/jarvis 496, kalshi+layering 154, markets 289, parlay 245, odds 170, all exit 0. Not covered by the replay oracle (it hashes the daily path only). Proposals (not done): which of two Polymarket fee evaluation orders to keep (they agree on 159,240 of 159,240 tested inputs), one Polymarket client, retiring the legacy odds client after moving `NameResolver`/`american_to_prob` out of it, moving `market_slow` and `combo_eval` to `research/`.
- Lane C2b (pages) merged as 14e8966f. `app/scripts/check.sh --bake --browser` on the merged branch: ALL CHECKS PASSED at 21:57 CST (API 560 tests, web 408 tests, artifact bake + build, walk 0 violations plain and injected). I looked at `#/scorecard`, `#/tests`, `#/source/...`, `#/help/errors` and a leg page on tonight's real export (phone size, Market lens): all render. Note: `--bake` re-baked `app/web/artifact-data/baked.json` from the fixtures; run `app/scripts/artifact.sh --export` for a real-data artifact.
- Nit seen in the screenshots: in the Market lens the health strip is four lines above every page on a phone. Not fixed.
- Lane C2a (feature routes) merged as 11045bba; `check.sh` on the merged branch passed (API 559 tests). Lane C1 (design system) merged as 8bdf54c3; `check.sh --fast --browser` on the merged branch passed at 21:08 CST (web 392 tests, walk 0 violations plain and injected). I looked at Discover (phone, Learn) and Discover (1440 px, Market) on tonight's real export: data-as-of line, side rail, Key terms in the nav all render.
- Known walker misfire, not a lock breach: with the shared desk fixture served (`APP_DESK_SHARED_FILE`), `route-walk.mjs` reports "LINE_CAL lock" on Market-lens Discover. It already did so before the design merge (6 hits on the pre-design code). The text rule sees the desk's 2026-11-08 "as of" and kill-switch dates next to the 2024 replay slate's "Final" and win chances. No locked night's model chance is beside its outcome. The default walk (no desk file) is clean. Open item: scope `lockedNightOnScreen` to the slate's own text.
- Lane A (API hardening) merged as 9ae1d973. Verified by me on the merged branch: ruff, format, mypy clean; pytest 533 passed (with `NBA_PYTHON` set so the exporter comparison runs). Under a real uvicorn server: typed refusals `{ok, code, text, field, request_id, detail}` with `X-Request-Id`, 405 on POST, no CORS header for a foreign origin, new health keys (`data_made_at`, `data_age_s`, `data_version`, `export`). Lane numbers are in `lane_api.md`.
- **App gate on the merged integration branch, 20:19 CST: `app/scripts/check.sh --browser` ALL CHECKS PASSED** (11 steps, 369 s: API 533 tests, web 370 tests, both builds, browser walk 150 steps plain + 150 injected, 0 violations). `app/scripts/walk.sh` is new (the walk with its own servers); `check.sh --browser` calls it.
- Not done on purpose: the five registry codes the web lane suggested (`bad_answer`, `too_many_requests`, `refused_showing_saved`, `storage_blocked`, `unknown_live_event`) are not added, because nothing uses them yet; they go to lane C2 with the code that would raise them.
- Lane B (web hardening) merged as 85a6ad54. Verified by me on the merged branch: `npm run check` 370/370, artifact "checks passed" 5.54 MB, `npm run walk` seed 1: 150 steps, 0 violations, and with `--inject` 150 steps, 0 violations. Lane numbers (before -> after) are in `lane_web.md`. Open findings it did not fix: the service worker answers a refused export (5xx) with the saved copy and the page says "offline" (`public/sw.js:74`, Devin's call); numbers computed in the browser (`desk.ts:274`, `desk.ts:409`, `CardDetail.tsx:62`, `leg.ts:103`, hard-coded `History.tsx:39-41`); no Tab trap in dialogs; registry lacks `bad_answer`, `too_many_requests`, `refused_showing_saved`, `storage_blocked`, `unknown_live_event`.
- Lane D (DX) merged 19:11 CST as 545ab163. I ran `dev.sh` on 8040/5196 from the integration worktree: health 200 through the web port, the busy-port message works, a terminate signal stops both servers with nothing left running. `check.sh` was run by the lane (all passed, 164 s), not yet by me on the merged branch. Report `lane_dx.md`.
- Lane F (docs) merged 19:06 CST as 60713af7: README, PROJECT_STATUS as of 11-16, drift fixes, root NEXT_SESSION.md removed. Markdown only; report `lane_docs.md`. Its drift list for code-adjacent files goes to lane E2.
- Phase 0 (above), commit 72924903. Shared error-code registry seeded (`app/error_codes.json`), commit d5d9b058.

## In progress (A, B, D launched 18:57–18:59 CST, E1 and F by 19:01, each in its own worktree, branched from d5d9b058)
| Lane | Branch | Worktree | Scope | Report |
|---|---|---|---|---|
| A | `agent/app-1117-api` | `~/Downloads/nba-wt-1117-api` | `app/api`: audit, contract/hostile/failure tests, validation layer, typed errors, request ids, file splits | `docs/handoff_2026-11-17/lane_api.md` |
| B | `agent/app-1117-web` | `~/Downloads/nba-wt-1117-web` | `app/web`: audit, route walk, not-found, error screen and boundary, time helper, SSE lifecycle | `docs/handoff_2026-11-17/lane_web.md` |
| D | `agent/app-1117-dx` | `~/Downloads/nba-wt-1117-dx` | `app/scripts` (dev, check, artifact), `app/README.md`, architecture page, docs drift | `docs/handoff_2026-11-17/lane_dx.md` |

| C1 | `agent/app-1117-design` | `~/Downloads/nba-wt-1117-design` | launched 20:02 CST: tokens, `src/ui/` components, one formatter, edge-label language, states, data-as-of line, Key terms page, intro, palette and shortcuts | `docs/handoff_2026-11-17/lane_design.md` |
| C2a | `agent/app-1117-menu-api` | `~/Downloads/nba-wt-1117-menu-api` | launched 20:21 CST: read-only routes for prediction record, distribution, scorecard, forward-test tracker, parlay diagnostics, error-code help, health strip fields; export-field proposals | `docs/handoff_2026-11-17/lane_menu_api.md` |
| C2b | `agent/app-1117-pages` | `~/Downloads/nba-wt-1117-pages` | launched about 21:09 CST: the pages (prediction record, health strip, distribution view, scorecard, forward-test tracker, error-code help, parlay diagnostics), honesty labels on browser-computed numbers, artifact bake of the new routes | `docs/handoff_2026-11-17/lane_pages.md` |
| E2a | `agent/steward-1117-live-daily` | `~/Downloads/nba-wt-1117-live-daily` | launched about 21:11 CST: one lead constant, context-residual config guard, forward DDL in one place, stubs, finished research out of `nba/`, `pipeline.py` split; one commit per task for oracle bisecting | `docs/handoff_2026-11-17/lane_live_daily.md` |
| E2b | `agent/market-1117-live-markets` | `~/Downloads/nba-wt-1117-live-markets` | launched about 21:11 CST: fee map and one fee module proven bit-identical, Polymarket client duplicate, legacy odds vendor banner, comment drift, long-file splits | `docs/handoff_2026-11-17/lane_live_markets.md` |
| E1 | `agent/steward-1117-ci-health` | `~/Downloads/nba-wt-1117-ci` | lint truth, `slow` marker, flaky tests, generated outputs out of git, launchd plist files (not installed), cleanup script (not run) | `docs/handoff_2026-11-17/lane_ci.md` |
| F | `agent/scribe-1117-docs` | `~/Downloads/nba-wt-1117-docs` | `README.md`, `docs/PROJECT_STATUS.md`, drift fixes in DAILY_PIPELINE and PARLAY_ENGINE, root `NEXT_SESSION.md` removed on the branch | `docs/handoff_2026-11-17/lane_docs.md` |

Not started: lane E2 (live-path refactors that need the replay oracle: one lead constant, one fee module, duplicate
Polymarket client, legacy odds vendor names, context-residual config, forward DDL, research out of `nba/`, long files),
lane C2 (candidate menu: prediction record, scorecard, health strip, distribution view, forward-test tracker, error-code help, parlay diagnostics; full-stack, waits for A and C1), lane E (repo-wide fixes; waits for a free slot and,
for the replay oracle, for the end of the game window about 22:00 CST).

## Next three steps
1. As lanes report (C2b pages, E1 CI health, E2a daily, E2b markets): verify each on its branch, merge into integration,
   run the matching gate (`app/scripts/check.sh --browser` for app lanes; layering test and touched test directories for
   repo lanes).
2. Read the baseline oracle hash (after about 22:50 CST). Then the E2 oracle on the merged tip (holdout row first).
3. Phase 6 (math diagnostics, analysis and proposals only) if budget remains, then `REPORT.md` ending in `RUN COMPLETE`.

## Replay oracle (state at 21:12 CST)
- Holdout row logged first: commit 694ae69b (`docs/HOLDOUT_ACCESS_LOG.md`, logged 21:12 CST).
- Worktree `~/Downloads/nba-wt-1117-oracle` (detached at 694ae69b), `data` is a symlink to the main checkout's `data/`
  (the tracked `data/` was moved to `~/Downloads/nba-scratch-1117/oracle_wt_data_aside`). Read use only.
- **BASELINE result: IDENTICAL `89889f6d`** (46,658 predictions, 44,562 scores, 20 of 20 dates, failures 0), ran 22:15 to 22:27 CST (12.5 min). Result row appended to the holdout log.
- How the baseline was run (a background job of the session): waits until 22:15 CST, then
  `nba.daily.replay_season --season 2025 --source <main>/nba.duckdb --db ~/Downloads/nba-scratch-1117/oracle_base.duckdb
  --out-dir ~/Downloads/nba-scratch-1117/oracle_base --max-dates 20 --no-lock`, then `nba.daily.canonical_dump`.
  Output: `~/Downloads/nba-scratch-1117/oracle_base.log`, `.hash`, `.start`, `.end`. Expected `89889f6d`.
- If the session died before 22:15 the job died with it: check for `oracle_base.start`; if absent, run the command by hand
  from the oracle worktree (the row is already logged).
- **E2 result: IDENTICAL `89889f6d`** at 59625c3c (46,658 predictions, 44,562 scores, 20/20 dates, failures 0), 23:13 to 23:25 CST. Result row in the holdout log.
- **E2 run started 23:13 CST** on the integration tip 59625c3c (holdout row 7470f7fa logged first, at d375aeb9, before the fee-order merge): output `~/Downloads/nba-scratch-1117/oracle_e2.{log,hash,start,end,commit}`. Expected `89889f6d`.
- The plan that led to it: after E2a and E2b merge, log a second row, `git checkout --detach <integration tip>` in the oracle worktree, run again
  into `oracle_e2.duckdb`. A different hash means bisecting E2a's commits (one per task) and reverting the offender.

## Follow-up run (Devin, 23:17 and 23:22 CST on 11-16): Jarvis look + betting tools behind one switch
Brief: `/Users/devin/Downloads/jarvis-followup/FOLLOWUP_PROMPT.md` (kit files flat in that folder). Devin: run until
09:00 CST on 11-17, usage is not a constraint. Do NOT write `RUN COMPLETE` into `REPORT.md` until this follow-up is also
finished: the terminal resume loop stops on that line.
- Follow-up integration branch: `agent/app-jarvis-followup`, worktree `~/Downloads/nba-wt-1117-jarvis`, cut from
  integration at 7bc6a0ae-or-later. Lane branches merge into it; never into `main`. Final report `REPORT_JARVIS.md`.
- Lanes: J1 `agent/app-jarvis-flag` (`~/Downloads/nba-wt-1117-jv-flag`): the one switch `VITE_PH_BETTING` / `PH_BETTING`,
  seam under `#/bet`, OFF-parity tests. J3 `agent/app-jarvis-betting-logic` (`~/Downloads/nba-wt-1117-jv-logic`): pure
  logic port into `app/web/src/betting/`. J2a `agent/app-jarvis-look` (`~/Downloads/nba-wt-1117-jv-look`): tokens,
  components, shell, Simple/Analyst chooser, state components.
- Then: J2b lens restyle (after J2a), J4 betting screens (after J1, J3, J2a), J5 visual regression, donor map, report.
- Worktree names keep "nba-wt-1117" so the resume loop's activity check sees them; a keep-awake with that marker runs
  as a background task of the session.

## Auto-resume (Devin started it in a terminal about 19:04 CST on 11-16; if it was the one-shot with `sleep 14700`, it fires about 23:09 CST)
A terminal command resumes this session headless (`claude --resume ... -p "Continue the autonomous run..."`), either once
after a fixed wait or hourly until `REPORT.md` contains the line `RUN COMPLETE`. Rules for a resumed copy:
1. First check whether the original session is still working: `pgrep -fl "nba-wt-1117"` and
   `git log --all --since="20 minutes ago" --oneline | head`. If there is activity, do nothing and end the turn.
2. Otherwise continue from "Next three steps" below.
3. The last line of `REPORT.md` is `RUN COMPLETE` only when the whole list is finished or deliberately closed.

## Notes for whoever resumes
- Servers left running by this run: API 8010 (fixtures), API 8011 (real export, read-only), Vite 5183 and 5184, all from the
  integration worktree. They are plain foreground processes of the session; start them again with the quickstart.
- Repo lint baseline: `ruff check .` finds 82 errors, 81 of them in three `research/scratch/gate_*.py` files and one in
  `app/fixtures/build_export_best_real.py`.
- The Mac was on battery at about 18:58 CST (90%). Power on game nights is Devin's item.

## Failures and things not run
- The repo-wide suites and the replay oracle have not been run yet (game window until about 22:00 CST).
- Correction: times in earlier versions of this file were written from memory and ran up to 20 minutes fast. They now match commit times (`git log --date=format:%H:%M`).
