# Reading log: later lineup and availability news (information program item 1), 2026-11-17

DRAFT intake note (modeler). Part of `docs/prereg/INFO_PROGRAM_2026_27.md` (DRAFT umbrella). Item 1 is **owned elsewhere**: this note
summarises its state and lists what is missing; it drafts no new rule. Advice or momentum: *follows advice* (CLAUDE.md "later
information is the lever"; the main session ranked it first).

## Claim (restated)
Information released between T-60 and tip (confirmed starting fives, late injury-report changes, reporter posts) reduces props error,
mostly through minutes and P(play). Target: props CRPS and P(play) at a forecast time later than production's T-60.

## The information and when it is knowable
| Piece | Knowable | Forecast time it can enter |
|---|---|---|
| Confirmed starting fives (NBA daily_lineups feed) | median 16.8 min before tip (5.5-24.9; 0/14 games both fives by T-30, 13/14 by T-10; DECISIONS 2026-10-22 16:0x) | T-15 (T-30 dropped at its own gate) |
| Official injury report updates | every 15 min (live probes); about 0.8 rotation-player changes a game after the T-60 list (2023-24 lower bound) | T-15 (LATE_REPORT) |
| Beat-reporter posts (status, minutes caps) | shootaround (morning) and pre-game media 75-100 min before tip; first_seen on our clock | T-60 (REPORTER_FEED_LIVE) and T-15 |

## What we capture today
* Lineups: `data/lineups/lineups.duckdb` and 40 raw day folders since 2026-10-09; `forward_t15_decisions` 205 rows (172 of 200 logged
  by 11-16, PROJECT_STATUS), `forward_t30_decisions` 215 (1 of 212 logged; arm dropped 2026-11-17 19:33).
* Report: `player_availability` 2026 32,905 rows (manifest 6edbcef0c89d); LATE_REPORT `forward_late_decisions` 59 rows since 2026-11-09.
* Reporters: 45 post reads in preseason only (10-15/16); capture merged 2026-10-27 but not installed; no in-season post.

## State of the rules (from DECISIONS, FORWARD_PREREG_2026_27 and the ledger)
* **FORWARD_T15_2026_27**: FROZEN (sha256 5c0dec6d, commit f2389f5). Family m = 4 (pts, reb, ast, fg3m), Pocock alpha 0.0182, L30
  kill-only look about 2026-11-20, first efficacy look L60 about 2026-12-22 (pts, reb), L120 about 2027-02-24 (ast, fg3m). Logged share
  so far 0.86, above its 0.70 Wilson gate need.
* **T-30 arm**: dropped at its own logged-share gate (DECISIONS 2026-11-17 19:33). No T-10 arm for now.
* **LATE_REPORT** (`props_context_residual_late`): shadow merged and logging since 2026-11-08/09. Its scoring rule (LATE_REPORT_FORWARD)
  was sketched only in the build agent's hand-back (no file in the repo: `git log -S LATE_REPORT_FORWARD` finds only the ledger commit
  a0851794); the prior is in PREDICTIONS_LEDGER (2026-11-02 21:01: P(pass) 0.30 at >= 60 changed games
  over >= 20 dates). **Not frozen.**
* **REPORTER_FEED_LIVE**: DRAFT (eae9efab, `agent/modeler-reporter-feed`), Stage A not frozen; one season can carry S1 (status vs report)
  and M1 (cap shortfall), not C1/C2 (CRPS on cap rows needs about 6-10 seasons).
* **REPORTER_LEAD_TRIAL**: approved 2026-11-17 19:33 (capped $50; does a reporter post precede the official feed's confirmation?).
  **Rule not written.** Install and the key are Devin's steps.
* RETURNING_TEAMMATE_LIVE_T30: a draft built on the T-30 arm; with T-30 dropped it is dead as written (a T-15 version would be a new id).

## What is missing (and its cost)
1. **Freeze LATE_REPORT_FORWARD before any outcome of its rows is joined** (modeler 0.5 day to write it into `docs/prereg/` from the
   ledger prior, add the gate counts from `forward_late_decisions` label-free; main session commits). Without it the shadow accrues rows with no rule (the decisions review
   of 2026-11-17 made the same point for the in-game logs).
2. **Write REPORTER_LEAD_TRIAL** (modeler 0.5 day): label-free statistic = lead of first_seen of a reporter status post over the first
   official-report stamp carrying the same status; Wilson / bootstrap CI on the median; budget stop at $50; no outcome needed. Gate
   arithmetic: $50 buys about 10,000 posts at $0.005 (less user reads), about 40 nights at about 230 posts a night; at about 0.3
   matched status events a game x 7.5 games, about 90 events: enough for a median lead CI of a few minutes. Rates are guesses until
   Stage 0 of REPORTER_FEED_LIVE or the trial's first week.
3. A decision on REPORTER_FEED_LIVE Stage A after the trial (Devin).

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Announced starting fives | 2026 live only (since 2026-10-09) | 205 T-15 decisions | no announced-five history 2013-2025 (box starter flag only, differs from as-of usual starters on 17% of mover rows, T512); collector holes (778 min preseason) | history: no (the feed is not archived by the source as far as we know); live: keep the Mac awake (BEST_PRACTICES 2026-11-17) | none |
| Official report intraday | 2019-20 about 4.7 stamps a game; 2021 hourly; 2022-24 two stamps a day (backfill artifact); 2026 every 15 min | 2026: 32,905 rows | 2022-24 intraday sequence (INJURY_TIMING 2024 exposure 197 vs 300 games) | yes, CDN pull (250-request pilot drafted) | archivist about 1 day + Devin's download; nba.com terms read flagged |
| Reporter posts | none in season | 0 | no free archive | yes, X full-archive search (paid, $0.005 a post) | the $50 trial covers the forward lead only |

## Playbook check
Class: information source feeding production's own trees (the T-15 arm and LATE_REPORT re-run production with later inputs; nothing new
is fitted). (1) Information, not capacity. (2) Data appetite: trees already trained; the arms are cutoffs, not fits. (3) Validity: real
tips; strict `fetched_at < cutoff`; `run_after_tipoff` counted as skipped; DNPs counted. (4) Lineage: LINEUPS_KNOWN (T085-T090, WOUNDED:
range -0.020 to -0.042 pts), LINEUPS_KNOWN_REBASE (T522), INJURY_TIMING (gate fail). (5) Scores against production T-60 CRPS; the t15
price comparison is descriptive (the book may know more).

## Verdict
**PURSUE (owned elsewhere).** Highest expected value of the eight: the only item with a measured large gain (T085, T522). Actions for
this program: items 1 and 2 above, first in the order of work.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: **not proposed as evidence.** The confirmed-lineup feed's timing has no history (the box starter flag resolves
game-time decisions the T-15 feed may not, T085 WOUNDED: a flattering backtest); 2022-24 intraday reports are two stamps a day on a clock
30 min early (INJURY_TIMING rev 3), so a T-15 report backtest would mis-time the news; reporter posts have no first-seen history (a paid
archive gives `created_at` and misses deleted posts). The only honest history piece is 2021-22's hourly report (one season, and 2019-21
reports are structurally different, T315): too thin to select on. Stage 2: forward only (already the design of T-15, LATE_REPORT and the
reporter rules). Time to first answer: T-15 L60 about 2026-12-22 (pts, reb); LATE_REPORT and the lead trial weeks after their rules freeze.
