# Report: autonomous run of 2026-11-16 evening (SUPER PROMPT v3)

Everything is on `agent/integration-2026-11-17` (worktree `~/Downloads/nba-wt-int-1117`). Nothing was merged to `main`,
nothing was pushed, the main checkout was not edited. Times are Central, from the clock or commit times.

## 1. What was done, by lane

| Lane | Branch | What landed |
|---|---|---|
| Phase 0 | (integration) | App brought up on fixtures and on the real export, read-only. The single-file artifact build was broken on `main` (the headshot host tripped its own no-external-address check); fixed with a stand-in so the artifact shows initials. |
| A, API hardening | `agent/app-1117-api` | Typed refusals `{ok, code, text, field, request_id, detail}` with `X-Request-Id`; one validation layer; the private desk now also requires a loopback connection (it trusted the `Host` header alone); lean `prop_lines` load; bounded live streams; a damaged export gives a typed 503, not a plain 500; `main.py` 777 → 91 lines with routes by area; hostile-input and contract tests. |
| B, web hardening | `agent/app-1117-web` | Explicit not-found route with validated params; one failure screen from the shared code registry; error boundary with a reference id; one time module (naive stored times read as UTC); live-feed backoff and a "feed stopped" state; seeded route walker with failure injection; `Leg.tsx` 999 → 237 lines, `Record.tsx` 665 → 448. |
| D, DX | `agent/app-1117-dx` | `app/scripts/dev.sh`, `check.sh`, `artifact.sh` (and `walk.sh`, added by the main session); `app/README.md` rewritten; `app/ARCHITECTURE.md`; false statements fixed in `docs/APP_ARCHITECTURE.md` and `.claude/agents/app.md`. |
| C1, design system | `agent/app-1117-design` | One token set (light and dark, model / own-picks / baseline roles, edge labels as colour + icon + word); `src/ui/` components; one number formatter; "Data as of" line and newer-data notice; non-blocking intro; Key terms page (26 terms); side rail on wide screens; palette and shortcuts sheet. |
| C2a, feature routes | `agent/app-1117-menu-api` | Read-only routes: prediction record, distribution, scorecard, forward tests, parlay diagnostics, error help, health-strip fields; seven contract docs; `docs/contracts/PROPOSAL_export_fields_2026-11-17.md`. |
| C2b, pages | `agent/app-1117-pages` | The pages for those routes, the "needs export field" placeholders, on-screen notes on the four browser-computed numbers, artifact bake of the new routes. |
| F, docs | `agent/scribe-1117-docs` | Root `README.md`; `docs/PROJECT_STATUS.md` as of 2026-11-16; drift fixes in `DAILY_PIPELINE.md` and `PARLAY_ENGINE.md`; stale root `NEXT_SESSION.md` removed on the branch. |
| E2b, markets | `agent/market-1117-live-markets` | One pure fee module (`nba/markets/fees.py`) with the three distinct formulas, old names kept as wrappers; fee-config guard test; comment drift; two verbatim section moves (`poll15_parse.py`, `book_summary.py`). |
| fee order | `agent/market-1117-polymarket-fee-fastarb` | JARVIS's Polymarket fee uses fast-arb's evaluation order (decision row 16). Separate branch so it can be left out. |
| E2a, daily/props | `agent/steward-1117-live-daily` | One T-60 lead constant (`nba/leadtime.py`) imported by six modules; pplay shadow config path resolved from the module file; DDL and config parity guard tests; stub docstrings. |
| E1, CI health | `agent/steward-1117-ci-health` | E1_PENDING |

Each lane's own report, with file:line findings, is beside this file (`lane_*.md`).

## 2. Numbers

| What | Before | After |
|---|---|---|
| API tests (`app/api`) | 434 passed, 1 failed | 560 passed |
| Web tests (`app/web`) | 319 | 408 |
| Hostile API requests generated per run | none | 9,246; 0 crashes; every refusal typed |
| Hostile route hashes (5,000 seeded) | 317 invalid, 9 threw | 0 |
| Browser walk, 150 steps, plain | 35 violations (8 uncaught exceptions, 1 blank page) | 0 |
| Browser walk with injected failures | 20 violations | 0 |
| Web bundle | 321 KB js, 68 KB css | 390 KB js, 76 KB css |
| Single-file artifact (real export) | build failed | 6.45 MB, checks passed (lane C2b's measurement) |
| Real-export load | 4.7–5.8 s, about 1,135 MB after load | 3.6–3.7 s, about 767 MB (lane A's measurement) |
| Largest route, warm, `/api/games/{id}` | 255 KB, 6 ms (Phase 0, on the 18:35 export) | 224 KB, 9.5 ms (lane A, on a frozen copy of the 19:05 export; different data, so not a before/after of the code) |
| Fee differential test | none | 552,150 cases, 0 mismatches at the bit level |
| Replay oracle, baseline (694ae69b) | | IDENTICAL `89889f6d`, 46,658 predictions, 44,562 scores, 20/20 dates |
| Replay oracle, merged tip (59625c3c, with the live-path lanes) | | IDENTICAL `89889f6d`, 46,658 predictions, 44,562 scores, 20/20 dates, ran 23:13–23:25 CST |

Last full app gate on the merged branch: `app/scripts/check.sh --bake --browser`, ALL CHECKS PASSED at 21:57 CST (before
the repo lanes merged; they do not touch `app/`).

## 3. How to run the app

```
app/scripts/dev.sh                                # fixtures; prints the URL; Ctrl-C stops both servers
app/scripts/dev.sh --export                       # tonight's export, read-only
API_PORT=8040 WEB_PORT=5196 app/scripts/dev.sh    # when 8000/5173 are busy
app/scripts/check.sh --browser                    # every app check plus the browser walk
```

## 4. Merge plan (order into `main`; each is Devin's go)

1. `agent/scribe-1117-docs` (markdown only).
2. `agent/app-1117-dx`, `agent/app-1117-api`, `agent/app-1117-web`, `agent/app-1117-menu-api`, `agent/app-1117-design`,
   `agent/app-1117-pages`, in that order (each was cut from the integration branch after the previous one merged, so the
   simplest path is to merge the integration branch's app commits as they stand). `app/` only; no oracle needed.
3. `agent/steward-1117-ci-health` (test and lint config, `.gitignore`, new files under `ops/launchd` that are not installed).
4. `agent/market-1117-live-markets`, then `agent/steward-1117-live-daily`: live path. Attach the oracle result above.
5. `agent/market-1117-polymarket-fee-fastarb` only if decision row 16 stands.

Merging `agent/integration-2026-11-17` itself gives all of it in one step; it also carries the handoff docs and the two
holdout-log rows.

## 5. Decision rows

`docs/handoff_2026-11-17/DECISIONS_ROWS.md`, rows 1–17. The ones that change what a visitor sees: 4 (picks panel hidden in
Learn), 5 (not-found screen), 8 (intro replaces the welcome modal), 15 (forward scores on the scorecard), 16 (fee order).

## 6. Proposals only (not done)

- Fail closed when a configured export directory loses its manifest (today the API serves the 2024 replay, labelled).
- Service worker: a refused export (5xx) is answered with the saved copy and the page says "offline" (`app/web/public/sw.js:74`).
- Export fields the app needs: `docs/contracts/PROPOSAL_export_fields_2026-11-17.md` (eligible scores or a T-60 flag, market
  probability per scored row, integer-support CRPS, odds capture status per book, independent and copula joints,
  "what moved it" and "why this label").
- Move the four browser-computed numbers server-side (`desk.ts:274`, `desk.ts:409`, `CardDetail.tsx:62`, `leg.ts:103`).
- One Polymarket client (the two differ in retries, timeouts, 404 handling); retire the legacy odds client after moving
  `NameResolver` and `american_to_prob` out of it; move `market_slow` and `combo_eval` to `research/`.
- JARVIS reads the string "false" as true for the Polymarket `verified` flag (`nba/jarvis/config.py:195`) and silently falls
  back to round-up when `rounding` is missing (`nba/jarvis/config.py:228`).
- `forward_withdrawals` is defined only in `nba/daily/store.py`, not `nba/db/schema.sql`.
- Remaining T-60 literals in other packages: `nba/ingest/reporter_tick.py:430`, `nba/odds/combo_pull.py:563`,
  `nba/data/report_freshness.py:39` (exact replacements in `lane_live_daily.md`).
- `pipeline.py` split plan and the long-file split plans (`lane_live_daily.md`, `lane_live_markets.md`).
- From the prompt's own list, untouched: unify the two "who is OUT" rules; stale-row pricing; the T-30 arm's future; moving
  shadows out of the pretip run; timezone-aware timestamps; Kalshi alias refresh; watchdog coverage gaps and alert noise;
  hand-edited money state in `jarvis.yaml`.

## 7. Devin-only checklist

- Mac power: it was on battery all evening (69% at 22:57 CST).
- Confirm the new odds key works (not checked by this run).
- AVG November Colab refit: the app's health strip shows 15,276 AVG rows marked stale and 6,604 shown rows on the fallback.
- Roster-source flip on 2026-11-30; launchd installs; `settings.local.json` `bypassPermissions`; data-source licensing.
- Remove this run's scratch copies when done: `~/Downloads/nba-scratch-1117/` (two oracle runs, about 8 GB) and, if the
  branches are merged, the `~/Downloads/nba-wt-1117-*` and `~/Downloads/nba-wt-int-1117` worktrees.
- An app copy started from the main checkout at 18:45 still holds ports 8000 and 5173 (old code).

## 8. Skipped, and why

- Moving finished research out of `nba/`: every candidate is pinned by sha or named by path in a frozen prereg, gate record
  or ledger row.
- Splitting `pipeline.py`, `runner.py`, `the_odds_api.py`, `live_close.py`: tests patch functions at the cut points.
- Typed route table driving router, nav and palette: budget; the plan is in `lane_dx.md`.
- A separate app polish pass and five suggested registry codes: superseded by the follow-up work (Jarvis look).
- Phase 6 (math diagnostics): PHASE6_PENDING

## 9. Not verified

- Real phones, Safari, Firefox, a screen reader; phone access over the LAN.
- The service worker's offline paths (the dev server has no worker).
- The newer-data notice firing in a browser (unit tests only).
- GitHub CI: nothing was pushed, so no workflow change has run there.
- Whether the export job is meant to stop after first tips (no export after 19:35 CST on 11-16).
- The markets-side changes are outside what the replay oracle hashes; their evidence is tests only.
- One walker rule misfires when a desk file is served (see `WORKLOG.md`); the default walk is clean.
