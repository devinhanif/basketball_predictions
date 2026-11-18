# Reading log: roster moves and role changes (information program item 5), 2026-11-17

DRAFT intake note (modeler). **Owned elsewhere in part**: MINUTES_DECISION (ROLE_SHIFT part 1, `agent/modeler-role-shift`, draft
ca87c7b2) was approved on 2026-11-17 19:33 to run H1-H3 after a pre-freeze read. This note places it and audits the rest of item 5.
Advice or momentum: *Devin's override* (all eight).

## Claim (restated)
Trades, waivers, signings and two-way call-ups change who plays and how much; knowing the move (and the new role) before tip improves
minutes and props for the mover and his new teammates.

## Information and known-at
A transaction is public when announced (league transactions page, team releases, reporters); it reaches our data when the roster
snapshot changes (daily roster pulls) or when the player first appears in a box score (too late for his first game). Two-way call-ups
and G League assignments appear on the official report as "G League - Two-Way" / "G League - On Assignment" (51 and 11 rows in the
2026-11-17 export). Forecast time: morning (roster snapshot) and T-60. Mechanism: **minutes** (the new role).

## Captured today
* Daily roster snapshots `data/rosters/<date>/<team>.parquet` from 2026-10-10 (29 dated folders through 2026-11-17): transactions are
  derivable as diffs from 2026-10-10 only.
* Movers in history from box-score team changes 2013-2024 (no as-of transaction stamp: the first box row is post-game).
* No transactions table in `nba.duckdb` (manifest 6edbcef0c89d). `data/raw/bref_salaries_2026-10-21` exists (pilot; Sports Reference
  terms unsettled; DECISIONS lists its deletion as Devin's).

## Prior evidence here
* COLDSTART_SHAREBALL_T15 (T512): STOPPED AT THE GATE. Movers' first 10 games 1,202 / 1,025 rows (2023 / 2024); pts half-width 0.013-
  0.026 vs floor 0.005; the audit (2019-21 history + a transaction pull + one live season, about 5,500-6,000 mover rows) still gives
  about 0.008: fails.
* ROLE_PRIOR_SALARY (T504): stopped at the gate on power in all four stats; closed 2026-11-17.
* PRESEASON_ROLE_PRIOR (draft, T500 lineage), ROSTER_CONTINUITY (gate failed both parts), F18 (trade reason, queued, no data).
* MINUTES_DECISION gate (draft): H1-H3 pass on all rows (minutes power 0.017 min vs floors 0.10 / 0.05); familiarity-WITH for movers
  UNTESTABLE (3/133 and 2/114 movers had an earlier game under the new coach).

## Place in the program
* **MINUTES_DECISION**: its own families (REP / SL / D / D4R); not in F-INFO. It runs on history (select 2023, report 2024): no
  dependency on this program; it can run in parallel now.
* **Mover-specific question (the cold start): NOT FREEZABLE this season or with every repair we can name** (T512 arithmetic above, the
  audit of non-negotiable 8 was done there). What would change that: about 4x more mover rows (several live seasons pooled under a new
  id), or an effect at least 2.6x the floor (none measured).
* **Cheap, descriptive, no claim (recommended):** log roster diffs as dated transactions from the daily snapshots (archivist 0.5 day; no
  new source), so a future season has as-of move stamps. Two-way call-ups are already on the report.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| As-of transactions | 2026 from 10-10 (roster diffs) | 29 daily snapshots | none before 2026-10-10 | partly: nba.com / ESPN transaction pages (terms flagged), Basketball-Reference (terms unsettled) | archivist 1 day + Devin's terms read; not proposed as default |
| Movers (box-derived) | 2013-2024 | 1,202 / 1,025 first-10-game rows (2023 / 2024) | the move date is unknown before the first game | no | n/a |
| Coaches | 2022-2025 (main) | team_coaches 238-283 a season | 2013-2021 none | yes, about 270 Basketball-Reference pages (terms unsettled) | Devin's fetch after the terms question |

## Verdict
**PARK the mover question** (gate fails; recorded with the arithmetic); **PURSUE MINUTES_DECISION** in its own lane; **PURSUE** the
transaction log as plumbing.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: MINUTES_DECISION already is one (select 2023 / report 2024; owned). For the mover cold start a backtest would
flatter: box-derived moves are known only after the mover's first game, and as-of transaction stamps exist only from 2026-10-10.
Not proposed.
