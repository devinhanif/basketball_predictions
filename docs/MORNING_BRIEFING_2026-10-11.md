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

## 13:10 update
ELO_HISTORY_WINDOW: null (long replay changes 2024-25 by exactly zero; retune within noise). History-window direction closed for the season.

## 14:20 update ("fix everything")
Done: CI green again (fixture typing); P(play) Platt map OFF by default (oracle: only p_play and the unconditional fields moved;
golden re-baselined 89889f6d); nightly facts rebuild switched ON in the morning job; early-2019 injury layouts parsed and loaded
(+7,554 rows); layout-C reason text cleaned in 9,120 history rows. Still only yours: the delete command (permission layer).
- 14:35 (needs Devin): one last history-DB insert of 262 injury-report rows (2019-11-20..12-17, the four early-2019 aliased
  players) is dry-run clean but its apply was denied by the permission classifier in the agent's session. Say "apply 262" and I run
  it, or run it yourself outside xx:15-25 / xx:45-55:
  `mkdir data/ops/heavy.lock && uv run python -m nba.ingest.history_reason_fix --insert-missing --apply; rmdir data/ops/heavy.lock`
  (backs up first; commits only if rows == 362,528 + 262 and every PDF's stored rows equal its parsed rows).

## 19:20 update
MARKET_LINE_CLASSIFIER: null (T320–T324); the one-weight blend beats the learner everywhere; closed. OWN_TAG waits on a regenerated
production reference (the stored one predates the restored games); CALIBRATED_GRID and HISTORY_WINDOW_2 gates still being measured.

## 20:10 update
HISTORY_WINDOW_2: the repaired audit still stops, at a threshold below its own noise; 2019-20 and 2020-21 now look like the current
seasons, 2021-22 (hourly report) is the real odd one. Not frozen; direction closed (T325). Your option if you want it: a new rule on
2019-20 + 2020-21 only, or a CI-aware threshold. OWN_TAG and CALIBRATED_GRID still running.
- 20:30: OWN_TAG null (T326–T338); closed. CALIBRATED_GRID still running (the last of the four).

## 21:00 update
CALIBRATED_GRID: closed, no pass (T339–T339): the over-confidence at lines is the line's information, not a fixable map. All four
"try everything" rules done: four honest nulls. The T-60 props model is at its information ceiling; live season next.

## 17:15 CDT update (times earlier in this file from 14:20 on may be mislabelled by up to ~6.5 h; see DECISIONS 17:10)
Since the EV-tool answer (15:35): parlays of EV-flagged legs are not positive; realised leg-correlation atlas (same-player stacks +18 to
+53c, underdog-star × favourite-covers −18c, everything else ~independent); sportsbook arbs 11-23 per night at ~1% median; $10-a-week paper
user (+$24 / +$61 on model singles, all of it the under lean on the model's legs; blind under on the same legs matches; parlay persona
break-even); anatomy of an over (minutes is the switch, not knowable pre-tip; pts upper tail not too thin). Decisions: guidance free /
honesty paid; CLOSING LINE is the live benchmark; the-odds-api kept; closing capture merged and ON (NBA_CLOSE_CAPTURE=1; first-night check in
docs/CLOSING_LINE_CAPTURE.md). Rules run: MODEL_CLV closed (model beats the close by ~0.2 pp, mostly a no-model side rule; T340-T381);
XGB_PTS_VS_LT closed (tie overall; XGBoost better on the body, the lower-tail mixture better on near-DNP rows; T382-T388). Drafting:
GAME_MARKET_CONTEXT (market spread/total as the props model's game context instead of Elo proxies). Nothing needs you tonight.

## 17:28 CDT update
GAME_MARKET_CONTEXT closed, null (T389-T414): market spread/total as props context = placebo; no own-stat history = 13% worse. LLM shadow
forecaster approved (DECISIONS 17:14): rule drafted d15249ea (committed as DRAFT; needs your MONTHLY_CAP_USD), job building behind
NBA_LLM_SHADOW in a worktree. Capture ON. Needs you: the monthly dollar cap for the LLM shadow; nothing else.


## 17:56 CDT update
LLM shadow job merged (560232f), flag off; rule draft at 0724ce2 waits on your MONTHLY_CAP_USD. Enable later with three lines in .env
(ANTHROPIC_API_KEY, NBA_LLM_SHADOW=1) plus the cap in configs/llm_shadow.yaml and `uv sync --extra llm`; docs/LLM_SHADOW.md has the
first-night check. Oracle golden unchanged.
