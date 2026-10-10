# Next session

_Updated 2026-10-09 (HEAD b39aed0). Status overview: PROJECT_STATUS.md._

## Before opening night (by 2026-10-19)

1. Confirm jobs are loaded: `launchctl list | grep local.nba` should show daily-pretip, daily-morning, lineups, kalshi-snapshot.
2. One supervised live run of the pretip path (live schedule and injury-PDF fetch were not rehearsed): `uv run python -m nba.daily run --date <date> --roster-source official --log-int-variant`, then check `data/ops/ALERTS.md`.
3. Check the ingest queue (`data/ops/ingest_queue_status.md`). The queue holds stats.nba.com quota; confirm the daily and lineup jobs still get through (they yield to it, b39aed0). Decide whether to pause the queue on game days.
4. Disable Mac sleep on game days (a missed job runs once on wake only).
5. Resolve maintainer decisions (PROJECT_STATUS section 6): VM, rclone client_id, checkpoint cleanup, integer-quantile route.
6. Confirm the registry shows `rung0_injury_elo` v2 and `props_context_residual` v2 as production (`uv run python -m nba.registry list`).

- Pretip scheduler: launchd evaluates the calendar an hour ahead (timezone cached at load; morning job fired 07:00 for an
  08:00 plist; the 21:20/21:50 slots fire at 20:20/20:50 CDT). Devin chose to leave it (2026-10-10): benign for opening
  night (last tip 21:30 ET = 20:30 CT is covered). Reboot or the StartInterval schedule if it ever matters.

- Wave A follow-ups: DONE 2026-10-10 in the restructure (gate.py, pts_tail, stack/oof archived; living docs carry no
  archived `nba.*` path, checked 15:55; tests/daily/test_review_fixes.py writes only to tmp_path). Still open, on purpose:
  the pickle shim `nba/sim/usage_redistribution.py` stays until every cache under data/models/ and data/rehearsal/
  post-dates 2026-10-10 (the 2026-10-09 caches still unpickle through it). Historical docs (ledger, reviews, frozen
  pre-registrations) keep the paths they were written with; research/INDEX.md is the map.

## Opening night, 2026-10-20

- Morning (08:00 job): settle/report ran with no alerts; backup copy exists.
- Day: pretip job runs hourly from 09:30; confirm a forward prediction row exists per slate game before tip, with official-roster source and no rows written after tip.
- Confirm the injury report was fetched (feed failures are recorded, never block Elo).
- Confirm the lineups collector wrote snapshots for the slate and the T-30 shadow rows exist.
- Confirm the integer-variant rows are logged next to production rows; parlay shadow log has rows; Kalshi snapshot job is writing.
- If Kalshi lists player props, run the alias review before any matching is trusted.
- Next morning: settled predictions, forward report, DNP handling (absent players settle as DNP).

## First two weeks

- Official roster source stays on through 2026-11-03; then the script switches to `recent` automatically. Compare coverage and rookie minutes error.
- Run the steward's weekly forward health check: calibration, bias per stat, production versus comparison models.
- Do not read win/loss on small n; report CIs with every comparison.

## Roadmap after opening night

1. Score the shadow arms forward (integer quantiles, T-30 lineups) with paired clustered bootstrap once enough games exist; decide promotion on live data.
2. DONE 2026-10-09: hustle feature screen ran under c88788a; no family passes (T174-T175); screen closed.
3. History window experiment using 2013-2021 once the queue finishes; handle the missing injury feed before 2018-12-20 with an era flag.
4. CLOSED 2026-10-10 by inspection: the production path has no forward-looking schedule feature (rest is the gap to the
   previous game only; no `shift(-1)`, `days_to_next` or `b2b_first` anywhere under nba/). The lookahead lived only in the
   research v2 features, fixed in c9b00d1.
5. Platt P(play) forward check before trusting unconditional parlay probabilities.
6. Parlay: stay shadow until the deferral gate (30 settled rows, 14 distinct dates) and a positive-EV-at-conservative-bound result; report "no positive EV found" when true.
7. Optional, only if live results justify: pts-only regular-season hybrid variant with upper-tail fix; no 2025 touch.
8. DONE 2026-10-10 (docs/reviews/possession_order_2026-10-10.md; data_version 85d8a41277ce; backup data/backups/possessions_pre_order_fix_*). Was: Possession parser time order (before F11, F13, F15, F17; not needed by F8). `nba/parse/possessions.py` sorts by
   `action_number`, which is not game order (ADR 0002 item 5). Measured 2026-10-09 on the stored table:
   5,789 of 1,049,740 possessions end after they start and 3,939 start after the previous one ended, in 4,258
   games, so clock glitches beyond the appended corrections are involved. A re-parse renumbers `poss_idx` in
   those games; shots, lineups and anything keyed on it must be rebuilt in the same step, with a pre-fix backup.

- Loose end (2026-10-10, found by the coach-allocation analysis): `team_coaches` names the wrong head coach in 14 of 87
  team-seasons 2022-24 (rows look shifted one season: Udoka/HOU and Griffin/MIL under 2022) and carries no mid-season changes.
  No production reader. Archivist: check the postgame "coaches" fetch's season parameter, add a game-by-game head-coach table
  (the analysis embedded a hand-built one in data/scratch/coach_allocation_2026-10-10.py), re-ingest. Not urgent.
