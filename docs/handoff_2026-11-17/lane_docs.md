# Lane report: docs (branch `agent/scribe-1117-docs`, 2026-11-16 evening CST)

Files edited: `README.md`, `docs/PROJECT_STATUS.md`, `docs/DAILY_PIPELINE.md`, `docs/PARLAY_ENGINE.md`, root `NEXT_SESSION.md` (removed), this report. No code, config, ledger or database touched; nothing run except read-only `grep`, `sed`, `ls`, `git log`.

## Commits (one per deliverable)

| Deliverable | Commit |
|---|---|
| DAILY_PIPELINE fixes | `882df4fb` |
| PARLAY_ENGINE DNP rule | `85630a05` |
| Root NEXT_SESSION.md removed | `babc82e5` |
| README | `b7f530d8` |
| PROJECT_STATUS rewrite | `72204ca5` |
| This report | committed after it |

## DAILY_PIPELINE.md corrections (file:line, before -> after)

- `docs/DAILY_PIPELINE.md:165`. Before: "...template (NOT installed): two runs a day (11:00, 16:30 local, assumed US-Eastern)." After: same, with "(NOT installed; superseded 2026-10-09 by the jobs `ops/install_launchd.sh` writes, see "Scheduled runs" below; kept for history)". Check: the template file exists at `nba/daily/com.nba-prediction.daily.plist.template`, is marked TEMPLATE ONLY, and no `com.nba-prediction.*` plist is in `~/Library/LaunchAgents`.
- `docs/DAILY_PIPELINE.md:233`. Before: "`ops/install_launchd.sh` installs two user launchd jobs (Mac local time, US/Central)." After: "installs five user launchd jobs (`daily-pretip`, `daily-morning`, `lineups`, `pulls`, `watchdog`; the calendar clock is the fixed UTC-4 clock described under "launchd calendar timezone" below, not US/Central)." Check: `ops/install_launchd.sh` lines 22, 57-89; the doc's own timezone section.
- `docs/DAILY_PIPELINE.md:239` (pretip row). Before: "...then `nba.odds capture` (read-only sharp-line benchmark from theoddsapi.com into `odds_snapshots`; key from `.env` via `uv run --env-file`; exit 4 = feed unreachable or budget refused, informational; exit 5 alerts; added 2026-10-10, docs/prereg/ODDS_BENCHMARK.md), then `nba.markets capture`..." After: "then `nba.markets capture` (the pretip `nba.odds capture` step ... was added 2026-10-10 and removed the same day when that subscription was cancelled; sportsbook odds are now captured by the 5-minute lineups tick when `NBA_CLOSE_CAPTURE=1`, from the-odds-api.com into `live_book_odds`, see `ops/nba_lineups.sh` and docs/CLOSING_LINE_CAPTURE.md) and the read-only `nba.parlay evaluate` shadow log". Check: `ops/nba_daily.sh` lines 235-237 (the step was removed 2026-10-10), `ops/nba_lineups.sh` lines 98-118. "Same day" is my reading of two sources that both say 2026-10-10.

Left alone (history, not false): the 2026-10-09 "hourly :30" notes and the "about 26 runs a day" line near 382 (the plist has 28 slots; it describes the old cadence in a replay-storage note); the app-export "Not installed by anything in the repo" (true of the repo; the plist is in `~/Library/LaunchAgents`). Not verified: whether each installed job is currently loaded (`launchctl` not run).

## PARLAY_ENGINE.md corrections

- `docs/PARLAY_ENGINE.md:22`. Before: "...an INACTIVE player (not covered by the text) is unconfirmed; check one live prop series on 2026-10-20." After: "...was unconfirmed until 2026-11-10. VERIFIED 2026-11-10 (DECISIONS 2026-11-10 13:20 and 16:36; merge `e921f7ed`): the full contract terms (NBAPTS/NBAAST, PDF dated 2026-10-05) say a player who withdraws before tip resolves to the last fair price ... not as No and not as a refund ... `configs/jarvis.yaml` has `kalshi_prop_dnp_rule_verified: true`." Also states what is still not known: the settlement price itself is not stored, so paper settlement of a DNP still records no outcome and no pnl (unchanged from the line above it).
- `docs/PARLAY_ENGINE.md:86` (was 82). Before: "`forward_predictions` in `nba.duckdb` is empty right now, so `evaluate` has not been run against live Kalshi prices..." After: a dated correction: per the 2026-11-16 survey the table holds about 139k rows and `evaluate` runs in every pretip job as a shadow log; model weight is 0. Not about DNP; fixed because it was plainly false.
- The `last_fair_price` default and the "assumes DNP = No" case: the doc already defaulted to `last_fair_price` for Kalshi (lines 15-21) and called `void` and `loss` sensitivity bounds. Nothing in it assumed DNP = No, so no other edit. The statement "Rule per book: unknown for every book in `nba/odds`" (sportsbooks) still stands; the verification is for Kalshi only.

## Root NEXT_SESSION.md

Removed with `git rm`. References checked (`grep -rn NEXT_SESSION` outside `data/` and `.git/`): no code, Makefile, workflow, config, `.claude/` or `CLAUDE.md` references the root file; `CLAUDE.md` lines 113 and 129 point at `docs/NEXT_SESSION.md`. Bare mentions of "NEXT_SESSION.md" remain in dated 2026-10-08 docs (`docs/FDR_AUDIT_2026-10-08.md`, `docs/OVERVIEW_PLAIN_2026-10-08.md`, others) and in docstrings of `research/features/time_decay.py`, `research/features/possession_step_features.py`, `research/models/colab/export_training_data.py`, `research/models/rung4_stepheads.py` and a notebook; they cite the 2026-10-08 content by name, not by path, and are historical. Recoverable with `git show 2cc4e5c9:NEXT_SESSION.md`. If Devin wants them kept pointing somewhere, restoring a two-line pointer file is the reversal.

## PROJECT_STATUS.md: what was dropped

Kept (re-checked or carried with provenance): the production models table and its out-of-fold figures (agree between the old file and the survey), the red-team verdicts, the "market beats us" finding, the 10-10 odds-vendor cancellation.

Dropped (all remain in git history and, where marked, the ledger):
- "Shipped since 2026-10-10 midday" block (T-30 reader fix, data clean-up, facts store, reporter feed build): history; in `docs/DATA_CHANGELOG.md` and DECISIONS.
- Old section 2 job table (pretip "official rosters through 2026-11-03", "21:50" last slot, ingest queue "47 pending" snapshot, kalshi-snapshot note): stale; replaced by the verified table. The ingest-queue status line is dropped as unverifiable tonight.
- "Components built but not scheduled" (facts store, reporter feed): the facts store now runs nightly via `NBA_FACTS_NIGHTLY` in the installed morning plist; the reporter feed is covered as "not installed".
- Section 4 experiments ledger (about 40 bullets of closed experiments and their numbers, T082-T314): in `docs/TEST_LEDGER.md`; replaced by a 3-line lesson summary. Not re-verified against the ledger tonight.
- Section 5 risks that are resolved or superseded (opening-week rosters, schedule lookahead, "not rehearsed end to end", "NBA Cup knockout games dropped", data loose ends at data_version 1bee33d02ff2, odds vendor and reporter-feed untested notes).
- Section 6 decisions of 2026-10-11 (X sign-up, delete candidates, Oracle VM, rclone id, J. Green alias): most overtaken; the X spend item is carried into section 8.
- Section 7 opening-night checklist and section 8 roadmap (opening night was 2026-10-20).
- "Background (2026-10-08 narrative)": the possession-sim-as-north-star pivot, the "Elo explained" primer, the 10-08 status-by-front table (markets "not started", "CI green") and "What you KNOW / DON'T know". The pivot was reversed by later results; the Elo explanation was good but belongs in a plain-language note, not a status file. In history at `HEAD~` of the rewrite commit.

New content is labelled "per the survey" wherever I could not check it; the sections for live problems and decisions waiting on Devin carry an "as of" date.

## Drift list for files I may not edit (exact text for another lane)

1. `nba/jarvis/__init__.py:1-6` (docstring). Says "texts it to the maintainer through macOS Messages". Discord is the default: `configs/jarvis.yaml:93` is `sinks: [discord]  # discord (default) and/or imessage (optional)`; `nba/jarvis/imessage.py` exists as the optional sink. Corrected text: "...and posts it to the maintainer's Discord (the default sink; macOS Messages is an optional second sink), and records every play..." Same drift: `nba/jarvis/cli.py:3-4` ("The one secret JARVIS reads is the iMessage recipient...": the Discord webhook, `JARVIS_DISCORD_WEBHOOK`, and `JARVIS_DISCORD_TEAM_WEBHOOKS` are secrets read from the environment too) and `configs/jarvis.yaml:2` (same sentence: should name `JARVIS_DISCORD_WEBHOOK` and `JARVIS_DISCORD_TEAM_WEBHOOKS`, `JARVIS_IMESSAGE_TO` being optional).
2. `configs/jarvis.yaml:196-197`: comment "Ships OFF: flipping it on is the merge-time decision (Devin's go + replay oracle), not part of the branch." beside `enabled: true` (in the `ingame_checkpoints:` block, comment at 190-196). Corrected: "Shipped OFF at merge; `enabled: true` since <date, per DECISIONS>." I did not find the flip date in the lines I read; the other lane should take it from DECISIONS (the 2026-11-09 21:31 row introduced the block; the 11-10 handoff says the checkpoints flag was Devin's call and the JARVIS snapshot JSON must exist first). Not verified whether the in-game checkpoints job is actually producing rows.
3. `ops/vm/bootstrap.sh:23`: "(pretip :30 09:30-21:30, morning 08:00)". Corrected: "(pretip :20 and :50 of hours 09-22, morning 08:00)", the schedule `ops/install_launchd.sh:46-57` and `ops/vm/install_cron.sh:5,38` use. Also note that the Mac's launchd clock is now a fixed UTC-4 clock, so "same local times as the Mac" is no longer exactly true (`docs/DAILY_PIPELINE.md`, "launchd calendar timezone").
4. `ops/vm/install_cron.sh`: the pretip minutes are already right (`:5`, `:38`: `20,50 9-22`); the header (lines 1-9) and the cron block (lines 38-42) list only pretip, morning, lineups and the Kalshi snapshot. Missing relative to the Mac as of 2026-11-16: `pulls` (120 s), `watchdog` (600 s), `jarvis` (120 s; needs its webhook env), `app-export` (:05/:35, hours 12-23), `live-capture-start` (600 s), `kalshi-candles` (600 s), and the morning job's `NBA_FACTS_NIGHTLY=1` environment. The VM plan is not active (per the survey); the minimal fix is a header line: "Not a full mirror of the Mac: pulls, watchdog, jarvis, app-export, live-capture and candles are not included; do not use for failover without adding them."
5. `configs/cost.yaml:1`: "Inputs for the cost-monitor agent (.claude/agents/cost-monitor.md)". `.claude/agents/` holds adversary, app, archivist, market, modeler, scribe, steward; no `cost-monitor.md`, and no other file in the repo mentions "cost-monitor" (grep, outside `data/`). Corrected: "Inputs for cost tracking (no `cost-monitor` agent exists; `.claude/agents/cost-monitor.md` was never created)", or delete the reference.

Not in the brief, noticed: `docs/APP_ARCHITECTURE.md` (stale; per the survey, off-limits to this lane), `.claude/agents/app.md` (stale per the survey, not checked).

## Decisions (what / why / what would reverse it)

1. Delete root `NEXT_SESSION.md` rather than keep a pointer. Why: no path references; it said "CI green" and named agents that no longer exist (survey; the file is dated 2026-10-08). Reverse: restore from `2cc4e5c9` or add a two-line pointer.
2. Carry survey facts into PROJECT_STATUS only with a "per the survey" label when not re-checked. Why: the brief's rule. Reverse: a later session re-checks them and drops the label.
3. State "model weight 0" with the CI that `docs/NEXT_SESSION.md` gives (upper bound truncated there) rather than invent the bound.
4. README states the headline numbers from `CLAUDE.md` and the ledger and points to PROJECT_STATUS for the rest; it does not restate app commands beyond one line. README is 90 lines, under the 120 aim, because nothing else earned its place.
5. Treated the DAILY_PIPELINE template line as "stale, superseded" and kept it, because the file exists; deleting the template is code-adjacent and not mine.

## Could not verify

- Whether the new odds key works: only that ALERTS.md has no `rc=4` odds line after 18:19 as of 19:03 CST.
- Whether each launchd job is loaded now; I read plists and `~/Library/LaunchAgents` filenames only.
- All "per the survey" items in PROJECT_STATUS: CI history, AVG stale rows, T-30/T-15 counts, disk, DNP share, BEST_PRICE window date, INGAME_WINPROB_V2 stage, AVG and FULL read dates.
- Whether the "Ships OFF" flip date for in-game checkpoints appears in DECISIONS (not searched).
- Nothing was linted or tested: markdown only, no code changed, no tests run.
