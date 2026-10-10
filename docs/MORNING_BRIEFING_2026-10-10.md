# Morning briefing, 2026-10-10

## Needs Devin (a few minutes)

1. **F8 pre-registration draft is ready** (docs/prereg/F8_LINEUP_REBOUNDING.md, not frozen). Its gate check
   found one real problem: `possessions.oreb` over-counts offensive rebounds by about 1.35× versus box
   scores (37k vs 27.5k per season; it fires on made shots and turnovers too). So lineup OREB%/DREB% from
   possessions cannot be frozen as-is. Decisions:
   - a) Freeze F8 **without** the possession-OREB% group (draft default), or fix the rebound flag first
     (a tracker change plus a new rule; about half a day). Claude's view: fix the flag first. It is the
     mechanism Devin described, and a rebounding hypothesis without rebounds is half a test.
   - b) Big threshold: 6'10" (height ≥ 82 in) or Center. Confirm or change.
   - c) Order: F8 against production first, F11 after (draft default), or F11 first with F8 on top.
   - d) The motivating evidence (HOU 2024-25 shift; +0.16 live bias) comes from the report season and the
     holdout; the doc says so. Acceptable, or pick a different motivating example?
   - e) Primary: pooled reb (floor −0.005 may be unreachable, bigs are 24% of rows) or bigs-only primary
     with pooled as secondary. Claude's view: bigs-only primary.
2. **Google Drive:** delete `gdrive:nba_colab/ridge_v2_sweep` (4.4 GB; ridge v2 NOT KEPT, T098–T101)? Yes/no.
3. **rclone client id:** the shared one retires in 2026; five minutes to make your own when you want.

## Done overnight (2026-10-09 night)

- Lineup tracker fixed twice (look-ahead openers; then clock ordering after 957 negative stints were
  found); reconciliation 98.4–99.1% per season. Every FAN_KNOWLEDGE lineup number recomputed; all hold.
- F9b columns in players_static, 100% coverage on active players.
- Restructure Day 0: oracle deterministic (two HEAD replays identical), tag `pre-restructure-2026-10-10`,
  layering ratchet test (18 edges → shrinking). Edge 1 (injury-report triggers) and edge 2 (metrics →
  nba/truth) cut; each replay byte-identical (edge 2 pending at time of writing).
- F19 descriptive: the market's four safest legs sweep 40.5% of nights vs 37.4% implied; priced right.
- App project instructions written (docs/APP_PROJECT_INSTRUCTIONS.md).

## Also decided tonight, for your review (DECISIONS.md)

- Public face in three tiers (learning site → forecast record → nothing sold as picks until the log earns it).
- Socials: Claude drafts a daily queue after opening night; you approve and post. Recommendation: disclose
  the collaboration. Your call on the name and on disclosure.
- Overnight: all eight import edges cut, each replay byte-identical to the Day-0 golden hash; the layering
  ratchet is at zero. Full 210-date replay runs next, then F9b under its frozen rule.

## Needs attention before opening night: two pretip slots did not fire

The watchdog alerted at 22:23: `daily-pretip` last started 20:50, nothing at 21:20 or 21:50 although both
are in the plist. Ruled out: sleep (Kalshi snapshots landed at 21:02, 21:17, 21:32, 21:48 and the lineups
job ran every 5 min), a held lock (no lock dir, no skip heartbeat, no "skipped" log line), a crash (stderr
empty, last exit 0). launchd's run count (19 = 18 slots since the 11:52 install + RunAtLoad) says the job
was simply not launched twice. Preseason: no prediction was missed. Unexplained launchd calendar skips
are a known class of macOS behaviour; the unified log shows nothing for the job in that window.

Recommendation (Claude): switch the pretip job from `StartCalendarInterval` to `StartInterval` 1800 s with
the :20/:50 alignment and the 09–21 CT window enforced inside `ops/nba_daily.sh` (a missed interval is
retried at the next tick; the heartbeat stays the truth), and have the watchdog run `launchctl kickstart`
on a stale pretip rather than only alerting. Both are reversible and belong before the 10-17 freeze.
First, watch whether 21:20/21:50 fire on 2026-10-10 (the watchdog will say). Devin: OK to make the
scheduler change tomorrow?

## Odds data: you bought theoddsapi.com (not the-odds-api.com); decide

The key works (Business, 6,667/day). Its archive starts 2026-05-13, so it has no 2023-25 props; it does give
a live Pinnacle props benchmark from opening night. Options: (1) keep for live benchmark, history from the live
log by December (recommended); (2) also buy the-odds-api.com 5M ($119 once) for the two-season answer now;
(3) cancel. Details: docs/research/historical_odds_2026-10-09.md, "2026-10-10 update".

- 2026-10-10: ODDS_BENCHMARK rule drafted (docs/prereg/ODDS_BENCHMARK.md): confirm or edit before the first scored pair; captures may start before, scoring may not.

- Odds capture is wired into the pre-tip job (2 requests per run). Regions us+eu; Pinnacle appears in the eu feed but posts no preseason NBA lines (0 rows on the 10-10 slate, 9 other books do). Verify Pinnacle rows on opening night before trusting the benchmark; props feed stays empty until the season.

- ODDS_HISTORY rule drafted (docs/prereg/ODDS_HISTORY.md): props model OOF vs Pinnacle/DK/FD/consensus at T-60 and T-5 on 2023-24 (fit) and 2024-25 (report), 2025-26 one logged touch. Confirm before any model row is joined to a price; the raw pull runs regardless.

- Odds history: six vendor nicknames reviewed and added to configs/kalshi_aliases.yaml (Claxton, Carrington, Herb Jones, Moe Wagner, B.J. Boston Jr, Ron Holland). After the pull chain finishes, run `uv run python -m nba.odds history-pull --season 2024 --phase props --reparse` (0 credits) so the 2024 rows pick them up; 2023/2025 pulls read them at start.

- F9b CLOSED (T186–T201): all checks passed, no stat reached the floor; the under-forecast of young top-10 starters is real (+0.4 pts) but explained by role, not draft slot. No F9c. Full table in reports/prereg_f9b.md.

- Wave A archive landed: 98 research modules (40,780 lines) now under research/, tested in CI, layering test says nba/ never imports research/. Three small follow-up cuts listed in NEXT_SESSION.

- Agents consolidated 18 → 6 (.claude/agents/; map in docs/reviews/agents_2026-10-10.md). Housekeeping for you: six stale git worktrees under .team/worktrees/ (branches agent/*) can be removed with `git worktree remove` + `git branch -D`; nothing references them.
