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
