# Morning briefing, 2026-10-11

_Written by Claude for Devin's few minutes. Overnight ran unattended under DECISIONS 2026-10-11 ~03:05. Yesterday's running
log is docs/MORNING_BRIEFING_2026-10-10.md (it carries the whole 10th and the night into the 11th)._

## Bottom line
Production unchanged and healthy; the 07:00 morning job and the 09:20 pretip both exited 0 (the watchdog's "stale" lines are
the known hour-early launchd drift you chose to leave). Nine ideas closed under rules since the 10th, all nulls; the data layer
is clean at data_version 418510d401d3; the replay golden is 030804df and unchanged by the overnight fixes. HISTORY_WINDOW is
running now: the ingest queue is PAUSED for it (resumable; done steps skip) and will be resumed after the run.

## Overnight, done and committed
- Same-surname substitution rule: missing stint rows 91 → 1; every season reconciles to box minutes within a minute for ≥ 99.95%.
- 21 orphaned injury-report rows linked by PDF evidence; 167 stay NULL on purpose (postponed > 7 days, NBA Cup final, one
  ambiguous GSW@MIN date).
- Facts store: explicit `--seasons` list (holdout provably absent), 2026-27 preseason lineups and tips loaded; nightly rebuild
  step written behind `NBA_FACTS_NIGHTLY=1` (OFF until you set it).
- HISTORY_WINDOW harness built and tested (frozen rule 9a1090be from 2026-10-09); era flags materialised; real tips for 2019-21
  matched 3,637/3,637.
- Fan doc: cross-book arbitrage (4.5% of lines on paper, 1% median margin, mostly exchanges) and the "craziest parlay the model
  got right" (62x on 2024-10-23; the top-5-per-night strategy returns 1.15 with CI 0.71-1.65: break-even, no claim).

## Needs you (none urgent)
1. `NBA_FACTS_NIGHTLY=1` in the daily-morning launchd environment when you want the facts store rebuilt nightly.
2. Delete leftovers when at the keyboard (permission layer blocks me):
   `rm -rf data/rehearsal/oracle_t30base* data/rehearsal/oracle_t30fix* data/rehearsal/oracle_[b-j]* data/rehearsal/oracle_facts.duckdb data/rehearsal/oracle_facts data/rehearsal/oracle_facts.log`
   (keeps oracle_a, oracle_golden2, oracle_golden3, oracle_facts_r2, full_wave_b, replay2025).
3. The six basketball questions from last night (load management policy; late scratches after T-30; rookie minutes early; role
   settling after a trade; road stars +0.36; what a "given" leg is). Any answers become rules.
4. X sign-up stays skipped per your word; the feed is built and waits.

## In flight
- HISTORY_WINDOW: queue paused → history-DB prep (era flags, report load) → G0 → harness gate → smoke → Claude's 20-date PDF spot
  check → run → report → ledger rows → queue resumed. Result lands here when done.

## 09:50 update
HISTORY_WINDOW stopped at its own gate (T315): the 2019-21 injury reports carry era identity (no PDFs before 2019-11-10; three
snapshots a day), so the props extension cannot be evaluated cleanly. No model was fit. Queue resumed. Your MOV-Elo question
moves to a report-free follow-up, ELO_HISTORY_WINDOW, drafted for your "confirmed".

## 10:40 update
Supervised pretip run: clean, rc 0, but the slate is empty until 2026-10-20, so it proves the wiring, not the live fetch; it repeats
on opening day. PPLAY_CONTEXT and ELO_HISTORY_WINDOW confirmed by you; both gates being measured, then freeze and run.

## 12:40 update
PPLAY_CONTEXT: no pass (T316–T317): the report tag is the information, the team habit is noise against a placebo. NEEDS YOU (production):
the P(play) Platt map in the forward path is miscalibrated (ECE ~0.14; raw p is better by ~0.07 log loss); say "fix pplay platt" and the
steward drafts the change with the replay oracle. Queue reordered: tracking/hustle first (your call). ELO_HISTORY_WINDOW freezing now.
