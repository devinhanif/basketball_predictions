# ADR 0002 - Lineup tracker: look-ahead period openers, no eviction

**Status:** accepted (2026-10-09). Supersedes the "Period-boundary heuristic" of the v1 tracker.

## Context
The v1 tracker (`nba/parse/lineups.py`) carried the previous period's closing five into the next
period and corrected it by evicting "the first unconfirmed player" whenever an unseen player acted.
`docs/reviews/stint_reconciliation_2026-10-09.md` showed stint minutes within 1.0 min of box minutes
on only 48.1% of 82,407 player-games and a wrong opening five in ~80% of Q2/Q3/Q4 team-periods.
"Exactly five on the floor" held by construction and never validated identity.

## Decision
1. Q1 is seeded from box starters. Every later period opens with the first five players whose first
   appearance in that period is an action or the out side of a substitution (look-ahead); technical
   and delay events are not evidence. A short list is filled from the previous closing five and flagged.
2. No eviction: mid-period changes are substitutions only. Contradictions are counted, not repaired.
3. Substitution names resolve by (a) initial+surname learned from the exact-id out side of other subs,
   (b) unique surname, (c) the candidate not on court, (d) box-roster elimination for players who never
   appear by name in the PBP. Anything left is skipped and written to a report.
4. The gate is identity-sensitive: share of player-games (box minutes > 0) with |stint - box| <= 1.0 min,
   reported per season. Target >= 95%, floor 91% (`MINUTES_SHARE_TARGET`/`MINUTES_SHARE_FLOOR`);
   `python -m nba.parse.rebuild_lineups` exits 2 below the floor. `parse_games` accumulates the same
   metric plus unresolved/desync/open-filled/Q1-mismatch counts in `ParseSummary`.

## Consequences
- Tests: Q2 opener differing from Q1 closing five, bench technical not an opener, same-surname subs,
  never-acting player, report flags, bulk writer round-trip (`tests/parse/`).
- Bulk rebuild is in-memory and set-based with chunked short write connections (no per-game UPDATEs).
- Residual error lives in games with unresolved or desynced subs (listed in
  `reports/lineups_rebuild/*.csv`); a first-name source would close most of it.
