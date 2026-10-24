# Kalshi unmatched player names, 2026-10-23

Cause: `configs/kalshi_aliases.yaml` held 97 rows (7 built in), written for the 2025-26 vendor
spellings. The 2026-27 prop series list ordinary full names (stars, suffixes, diacritics, 8 rookies)
that were never reviewed into the table, so every snapshot reported ~162 `UNMATCHED` names and
those markets were stored with `player_id` NULL. No fuzzy or format bug: the names were simply
absent. (`unmatched_names.json` accumulates across snapshots: 183 keys; 164 distinct names were
still unmatched against the current table, 163 unmatched + 1 ambiguous.)

Fix: 164 exact rows plus 7 spellings that only resolved through the fuzzy fallback, now exact.
Rule (no fuzzy): the normalized name has exactly one `personId` in the official NBA lineup feed
(`data/lineups/raw`, 332 files) and, where nba_api's static list has the name, that id is among its
hits. 156 agree with the static list, 8 rookies are in the lineup feed only: Caleb Wilson,
Cameron Boozer, Darius Acuff Jr., Darryn Peterson, Keaton Wagler, Kingston Flemings, Mikel Brown Jr.,
Yaxel Lendeborg. Namesakes resolved by exact full name (Jabari Smith Jr. vs Jabari Smith, Jaren
Jackson Jr. vs Jaren Jackson, Tim Hardaway Jr. vs Tim Hardaway): the lineup feed has one id each.

Before: 196 distinct prop names in kalshi.duckdb, 32 matched, 163 unmatched, 1 ambiguous.
After: 196 matched (189 exact, 7 fuzzy-fallback rows now exact too in the table), 0 unmatched.
Candidates needing a human: none ambiguous. The 8 rookies are the only rows whose id comes from a
single source; spot-check them. The 2026-10-21 candidates file named in the task does not exist in
any branch; the 2026-10-09 file's 20 names are all in the table.

Not changed: markets already stored with NULL `player_id` (snapshot upserts by ticker, so open
markets are filled on the next run; settled history is not backfilled). A name still absent next
season repeats the failure mode; run `python -m nba.kalshi.alias_review` after each new slate.
The Kalshi matcher still accepts a single fuzzy neighbour at 0.84 for unknown names; with a larger
table that surface grows. Proposal for Devin: remove the fuzzy accept (fail loudly instead).
