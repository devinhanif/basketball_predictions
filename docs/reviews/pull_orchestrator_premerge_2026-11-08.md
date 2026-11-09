# Pre-merge adversary review: pull orchestrator P1 (`agent/market-pull-orchestrator` 219636f)

_2026-11-08. Diff vs `origin/agent/steward-odds-hourly` 8936003 (approved in
docs/reviews/odds_hourly_premerge_2026-11-08.md): 25 files, +2221/-5. Spec: docs/DESIGN_PULL_ORCHESTRATOR.md and the
DECISIONS row of 2026-11-08 21:06. No vendor call, no launchd, nothing written under the real `data/`. Attacks ran on a
`git archive` copy and a copy-on-write clone of the market store, both in the scratchpad._

**Verdict: WOUNDED.** The safety properties hold. A hold only skips a send; no label, edge, stake, kill switch, dedupe
or paper-book row changes. With the flag off, behaviour is identical. Known-at holds, the budget reserve holds and the
dry run files nothing. But the feature does not cover the incident that motivated it, and a held alert usually cannot be
released on the next tick. Three small live-path defects need fixing before merge.

Verdict: BLOCKED

**Top finding.** The 11-08 incident was a **slate preview**, not an alert. JARVIS's paper book has **0 `alert` sends
ever** (13 `slate` sends since 10-25) and **0 strong plays**. The 11-08 13:59 ET post (`sends.kind='slate'`) carried 5
`thin` plays priced against a 238-min-old Pinnacle line. The hold only gates strong or arbitrage *alerts*
(`nba/jarvis/runner.py:343`), never the slate path (`runner.py:241-258`, `325-329`). On this branch the 11-08 post would
go out byte-identically. A strong alert's reference is already capped at 120 min (`max_ref_age_min`), so the hold only
acts on strong alerts with a reference 30-120 min old, a population that has been empty all season.

## Playbook check
Model class: none. This is data-freshness infrastructure in front of a rules engine (JARVIS edge/label), so the class
checks do not apply. The cross-cutting questions:
1. **Information or capacity?** Information: a fresher sharp reference before tip. That is the kind of change that has
   worked here before. Its value depends on reaching the posts that go out, and it currently does not (top finding).
2. **Enough data?** n/a for fitting. For the "reverse if" in the design: 0 strong alerts in 13 slates means a 3-night
   held-to-sent latency measurement will likely have n = 0. That is underpowered and cannot conclude either way.
3. **Test validity.** The builder's tests are valid for what they test, but `test_the_alert_goes_out_on_a_later_tick`
   plants the fresh row 2 min *before* the quote's timestamp, so it never meets `max_ref_lead_min` (F2).
4. **Lineage.** This is the scheduled capture (`nba.odds.live_close`) with a new caller. The inherited guards are
   carried forward: write-once (game, kind), `write_snapshot`'s received < tip and commence check, the ledger cap and
   server floor. One inherited weakness is also carried forward: the store's write lock is held across network calls (F3).
5. **Scored against?** Nothing frozen. `forward_scores` and `nba/truth` never read `live_book_odds`. The diagnostic
   scorecard's latest-pre-tip fallback can move (F7).

## Per-check table

| # | Check | Command (scratchpad `po/`) | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | Flag off identical to the base code (texts, tick reports, every paper-book table, 3 ticks) | `pytest tests/jarvis/test_adv_attack.py::test_flag_off_identical_to_no_hold_code` on the src copy | 3 ticks | identical | PASS |
| 1b | Held play leaves no trace (no plays, sends or alert_state rows) | `test_held_alert_state_empty` | 1 | paper book not even created | PASS |
| 1c | Labels, edges and stakes unchanged by the hold | builder's `test_the_hold_changes_no_label_edge_or_stake`, plus code read of 343-346 (after every dedupe gate, before `explain_one`) | - | unchanged | PASS |
| 1d | Dry run files nothing | mutant M8 (drop the dry-run return) killed | - | - | PASS |
| 1e | Held alert released when the reference refreshes | `test_realistic_pull_timing[0..4]`, `test_why_not_sent` | 5 timings | released only when the pull lands at most 2 min after the Kalshi quote's timestamp; otherwise `thin`: "Pinnacle is 3 min newer than this price" | **FAIL (efficacy, F2)** |
| 1f | A pulls.yaml error must not take JARVIS down | `test_bad_pulls_config_crashes_tick` | 1 | `ValueError` escapes `run_tick`, and `cmd_watch` (`cli.py:210`) has no handler | **FAIL (F1)** |
| 2a | An ondemand row is never close, t60 or t15 | grep of every `live_book_odds` / `snapshot_kind` reader (list below); mutants M3, M3b, M16, M17, M18 killed | 19 readers | exact-kind filters everywhere; never `close`/`t60` | PASS |
| 2b | Never written after tip | `orchestrator.py:201` plus `_pull_all` request-time check plus `write_snapshot` receive-time check; mutant M9 killed (the inner check remains anyway) | - | - | PASS |
| 2c | Scorecard numbers unchanged when ondemand rows exist | `scorecard_attack.py`: plant every t120 snapshot as `ondemand1` 25 min later (all books), on a CoW clone | 7,435 consensus, 4,979 Pinnacle keys | consensus 0 changed; Pinnacle 1 changed (`open_line` None to 6.5, 0022600149 / 1630529 pts) | minor (F7) |
| 3a | Reserve math | `ondemand_allowed`; mutants M4, M5 killed | - | `spent + 267 + outstanding x 267 <= 102,000` | PASS |
| 3b | Can ondemand starve the close or the schedule? | arithmetic, real costs from `data/odds/budget/*.jsonl` (read-only) | 766 paid calls, 10-31..11-08 | real cost **237**/call (upper bound used: 267). 15 games x 17 kinds x 267 = 68,085, plus ondemand cap 20,000 = 88,085 <= 102,000 | PASS |
| 3c | Can the schedule starve ondemand? | same | - | no on budget. Store lock: requests stay open, expire at need_by (15 min), are re-filed | PASS |
| 4 | Market-store write-lock hold per served request | `lockhold.py <latency>` on a CoW clone of market_asof (3.76 GB), with a real recorded per-event body and injected latency | 3 runs | 0.8 s per call: **2.1 s**; 2.5 s: **5.3 s**; 15 s: **30.3 s**. Observed vendor latency (`mao.duckdb`, 1,498 pulls): p50 0.81 s, p99 2.53 s, max 14.85 s | **FAIL at the tail (F3)** |
| 5 | Ops script: lock, heartbeat, gate, rc to ALERTS; never `heavy.lock`, `data/ops/lock` or a write to `nba.duckdb` | read `ops/nba_pulls.sh`, plist, install line; `tests/ops/test_pulls_script.py` | 7 tests | correct. Docs wrongly say "never opens nba.duckdb" (F8) | PASS |
| 6 | Watchdog false alarms | `wdreplay.py`: the 60+5 min rule every 10 min over [T-180, T], 94 regular-season games 10-27..11-08 | 94 games, 1,692 checks | with the hourly schedule (t240 present) **1/94 games** (1.1%) alarm, 2 checks; without it, 54/94 (57%) | PASS (F9) |
| 7 | Do the tests catch the regressions? | `mut.py` (18 mutants on the src copy) | 18 | 16 killed. **Survivors: M2** (held play written to alert_state), **M10** (server floor not wired into the tick) | partial (F10) |
| - | Targeted suites in the worktree (`.venv-1`, `--no-cov`, by path) | `pytest tests/jarvis tests/odds tests/ops tests/pulls` | 711 | 711 passed, exit 0 | PASS |
| - | ruff / format / mypy on changed files | unpiped | - | 0 / 0 / 0 | PASS |

Readers re-verified for check 2a: `forward/scorecard.py:321,404` (close, else latest pre-tip; F7),
`export/reader.py:647` and `parlay/top_source.py:438` (newest pre-tip snapshot per book: ondemand rows become the newest
line, which is intended and changes the parlay shadow and export population, but no frozen rule scores them),
`ops/coverage.py:476` (lists kinds, diagnostic only), `jarvis/sources.py:188,297` (tips fallback; newest Pinnacle of any
kind, intended), `explain/data.py:350,377`, `shadow_llm/payload.py:222`, `markets/decision_scoring.py:199`,
`markets/market_slow.py:44-45`, `parlay/line_cal_build.py:61`, `eval/q1_starters_live.py:226,443,541` (exact `t60`, `t5`
or `t15`), `live_close.done_games` (exact kind; ondemand never `close`), `export/build.py:203` (schema column). No reader
validates kinds against an enum (app/ included).

## Findings (severity-ranked, each with line and fix)

**F1 (blocker, cheap).** `nba/jarvis/runner.py:377`: `pc = rt.pulls_cfg or load_pulls_config()` runs outside the
try/except. A malformed `configs/pulls.yaml` (YAML error, or a `<= 0` threshold, which `config.py:81-84` rejects with
`ValueError`) raises out of `_tick`. `run_tick` catches only `duckdb.IOException`, so the live watch loop dies, and
halftime and in-game go with it, on the first tick that has a strong play. Shown by `test_bad_pulls_config_crashes_tick`.
*Fix:* load once in `Runtime` construction (`cli._runtime`), or wrap line 377:
`try: pc = rt.pulls_cfg or load_pulls_config()` / `except Exception: log.warning(...); return None`. The safe
default is to send as before. Add a test.

**F2 (major, efficacy; Devin's call on scope).** Released holds are capped by `max_ref_lead_min: 2`
(`configs/jarvis.yaml:54`, `slate.py:187`). Kalshi quotes come from the 15-min snapshot, and a strong label needs a
quote of 5 min or less. The on-demand pull lands 0-2 min after the request (2-min tick), so Pinnacle is usually more
than 2 min *newer* than the quote, and the play prices `thin` with no alert. It can only go out after the next Kalshi
snapshot, about 15 min later. Shown by `test_why_not_sent`: pull at +1 min gives "Pinnacle is 3 min newer than this
price; capped at thin". The design's "the next JARVIS tick (2 min) sees the fresh row" does not hold in the typical
case. *Fix options:* (a) measure skew against the quote's *read* time rather than the snapshot stamp, or allow the
lead when the reference came from an on-demand pull for this play; (b) file the request at the Kalshi snapshot cadence;
(c) accept it and say so in DECISIONS. Together with the top finding (slate preview not covered), this needs Devin's
decision on what P1 is for. Option (d): extend the request to the slate path. For example, at
`slate_lead_min + 10` before the first tip, file one request for every game whose reference is over 30 min old, so the
T-90 preview prices fresh lines.

**F3 (major, cheap).** `nba/pulls/orchestrator.py:214-222` holds the `market_asof.duckdb` write lock across every
network call of every served request (`_pull_all` at `:299`, serially for every request in the tick). Measured hold:
about 2 x vendor latency + 0.3 s per request, so 2.1 s typical and 30.3 s at the worst latency observed. Under
transport retries the bound is 3 x 30 s timeout + backoff per call. The lineups capture's `open_store` gives up after
6 x 5 s = 30 s (`live_close.py:478`), and its close window is hit by one tick only. So an ondemand served while any
game sits in its [0, 5] close window can cost that close. That inverts the declared priority close > ondemand: the
reserve protects the close's credits, not its lock. *Fix (either; both are best):* in `_serve`, skip ondemand pulls
with the request left open (not failed) when any slate game has `0 <= tip - t <= 10 min`; and serve at most one request
per tick, or close and reopen the store around each pull. Test: a game in its close window leaves the request open
with no odds call.

**F4 (major).** The hold fails closed with no liveness check (`runner.py:375-379`). It checks only
`pulls.yaml enabled`, not whether `local.nba.pulls` is installed, gated on, or beating. The shipped config turns the
hold on (`configs/jarvis.yaml` `hold_stale_alerts: true`). If JARVIS restarts on this code before the pulls job is
installed, or the job is dead, every strong alert with a reference over 30 min is held until a scheduled pull refreshes
it (up to about 30 min per hourly gap). The requests just expire, and the watchdog heartbeat check stays silent for a
job that never beat (`freshness.py:170`). Terminal refusals (per-game cap of 4, `refused_budget`) also hold without end.
*Fix:* hold only when `data/ops/heartbeat/pulls.json` is under `wd_heartbeat_max_age_min` old and its `detail` is not
`disabled`. When the game's last request is terminal (`refused_*`, `failed`, `expired`) within `cooldown_min`, send as
before with the stale-reference note. Add tests for both.

**F5 (minor).** `orchestrator.py:177-183` and `:205-206`: an unreadable slate (`read_slate` opens lineups.duckdb
read-only with no retry while the lineups job writes it) becomes `failed: game has no real tip`. The tick exits rc 4,
which writes an ALERTS line, and the 10-min cooldown blocks JARVIS's re-request. *Fix:* when the slate read raised,
leave that slate's requests open, as the "market store busy" path already does.

**F6 (minor, spec).** Design step 3.3 says the tick checks the "monthly plan rule". The code checks the daily cap, the
server floor and its own caps only. Arithmetic says it is safe today: schedule 4,539 x 215 = 975,885 plus ondemand
20,000 x 31 = 620,000, total 1.6 M, under 2.5 M. *Fix:* add ondemand's monthly worst case to
`estimate_credits`/`active_schedule`, or amend the design text.

**F7 (minor).** `forward/scorecard.py:333-336,415-428`: with no `close`, the fallback pools every pre-tip snapshot, so
ondemand rows enter the consensus line, the Pinnacle book line and `open_line`. Planted test: 1/4,979 Pinnacle keys
changed, 0/7,435 consensus keys. Today 13/140 games lack a close, and their last row is t5..t30, after any ondemand. So
the exposure is small but non-zero, and it ties a diagnostic to JARVIS's activity. *Fix:* add
`AND snapshot_kind NOT LIKE 'ondemand%'` to both loaders, or record the choice in the scorecard `fields`.

**F8 (minor, docs).** `ops/nba_pulls.sh:10` and the plist comment say the job "never opens nba.duckdb". The tick does
open it read-only through `read_slate` and then `schedule_teams` (`live_close.py:385-387`) for pre-lineup games, and it
opens lineups.duckdb read-only. Both opens are brief and match the lineups capture. *Fix:* say "never writes nba.duckdb".

**F9 (minor).** The watchdog is fine with the hourly kinds present: 1/94 games. It depends on `t240..t480`, which are
`extended: true`. If `active_schedule` ever drops extended kinds, the same rule alarms on about 57% of games. The
threshold has 2.3 min of headroom: the largest observed t180 to t120 gap is 62.7 min against a 65-min limit. Playoff
and play-in ids (`004`, `005`) are outside `REGULAR_SEASON_PREFIX`, so the check goes silent then. *Fix:* skip the
check when `lcfg.dropped_groups()` or the schedule lacks t240; note the playoff silence.

**F10 (minor, tests).** Two mutants survive. M2: a held play written to `alert_state`. Its effect is latent today
because `realert_due` is consulted only after a sent alert, so it is not a live bug. M10: `server_remaining=None` in
the tick. *Fix:* assert `alert_state` is empty after a hold in `test_stale_reference_holds_the_alert...`, and add a
tick-level test where the ledger's `server_remaining` sits just above the floor plus reserve.

Also noted, not findings: requests dedupe correctly (M13 killed). A double send after release is impossible
(`sent_recently` within 30 min). `request_id` is booked on both ledger lines. No key appears in anything written
(builder test). The ledger is `flock`-safe across the two writers. A JARVIS read-only open during an orchestrator write
reads as "no reference" for that tick, a pattern the lineups capture already causes.

## Corrected family and power
No statistical claim is made, so there is no BH family. Power for the design's "reverse if" (held-to-sent latency over
3 nights): the base rate of strong alerts is 0 in 13 slates, so the expected n is about 0. Underpowered, cannot
conclude. Either measure on the slate path (F2, option d) or run 3 nights in shadow (requests filed, alerts still
sent) to get an n.

## Single biggest risk
That the merge ships a mechanism that never fires on the posts Devin reads. The slate preview stays stale (top
finding), and the rare strong alert it holds is delayed about 15 min (F2) or held without end when the job is absent
(F4). Meanwhile it adds a second writer that can, at the latency tail, cost a close (F3).

## Not verified
A real vendor call or real lock contention with the live lineups job (forbidden; simulated on a clone with a fake
client). The replay oracle. JARVIS launchd behaviour on restart (`local.nba.jarvis` is not managed by
`install_launchd.sh`, so the runner change takes effect only when JARVIS restarts). The shell script under real
launchd/TCC. The 3.7 GB store's checkpoint cost under a concurrent reader.

## Recommended changes (not applied)
F1, F3, F4 before merge; F2 and the slate-path scope to Devin; F5-F10 with the next pass. Re-review the revision for
new contradictions, especially the F3 close-window skip interacting with need_by expiry and F4's fail-open interacting
with the dedupe gates.

## Drafted DECISIONS row (not appended)
| 2026-11-08 | Adversary review of agent/market-pull-orchestrator 219636f: WOUNDED / BLOCKED (docs/reviews/pull_orchestrator_premerge_2026-11-08.md). The hold covers strong alerts only; the 11-08 stale post was a slate preview (0 alert sends all season), so P1 as built would not have changed it. Blocking: config-load crash in the JARVIS tick (F1), market-store write lock held across vendor calls during close windows (F3), hold fails closed without an orchestrator liveness check (F4). Devin to decide whether P1 extends to the slate preview and how a held alert clears max_ref_lead_min (F2). | Follows the adversary review. | The three fixes land with tests and the scope question is answered. |

---

## Re-review 539fa84

_2026-11-08. Diff 219636f..539fa84 (20 files, +1032/-138); decisions on main 7e73962 (21:42 row). Same constraints:
`git archive` copies of 8936003 (base) and 539fa84 in the scratchpad, CoW clone of the market store, fake vendor,
`.venv-1`, targeted paths only._

**Verdict: WOUNDED.** F1, F3, F4 (mostly), F5-F10 are fixed and the fixes hold under attack. One new fail-closed path
remains (R1): it reopens the silence that F4 was meant to close. The fix is cheap.

Verdict: BLOCKED

### Checks

| # | Check | How | Result | Pass |
|---|---|---|---|---|
| 1 | Flag off identical to base 8936003, alert and slate paths | the same driver test in both trees, 8 ticks 20:00..23:40 (one alert, one slate preview); diff of texts, tick reports and every paper-book table (`o_base.json` vs `o_rev_off.json`) | texts, reports and book all identical | PASS |
| 1b | Flags on, pulls job dead (no heartbeat) | same driver, `o_rev_dead.json` | identical to base | PASS |
| 2a | Preview: held plays not recorded as announced | `test_adv_slate.py` (alive beat, RULE 20, 12 one-minute ticks, slate due at 20:00) | `plays` holds only the G2 play; `alert_state` empty; `sends.slate` keys exclude G1 | PASS |
| 2b | Count line | same | "1 plays held: sharp price not refreshed (oldest 30 min)" | PASS |
| 2c | No double post later | same, 12 ticks, served and unserved | one slate send; no later alert for the held key (with both flags on) | PASS |
| 2d | Preview on time when the job is dead or slow | dead: 1b; slow (alive, never served, expires at need_by): `test_adv_alive.py` | the preview goes out in its first due tick (22:58) every run; prefetch filed 2 then deduped | PASS |
| 3a | Lock never held across a vendor call | `lockhold2.py`: the fake vendor opens the store for write from a subprocess *during* each call | events rc 0, odds rc 0 (writer got the lock); tick 2.18 s | PASS |
| 3b | Close guard | same script, a second game 7 min from tip | "a game is within 10 min of tip; on-demand waits", 0 vendor calls | PASS |
| 3c | Store busy after the paid call | `lockhold3.py`: another writer takes the store during the odds call and holds it 12 s | **237 credits in the ledger; no `live_capture_log` row, no odds rows, request still `open`**; raw file written | **FAIL (R2)** |
| 4 | Mutants | `mut_rev.py`: 32 (the 18 from round 1 re-targeted, plus 14 new) | 27 killed. Both round-1 survivors (M2, M10) now killed. Survivors: M8b, N6, N6b, N8, N10 | partial (R5) |
| 5 | New crash or silence | code read of every new call site plus `test_adv_crashloop.py` | no new exception escapes `run_tick`, but there is a new silence path | **FAIL (R1)** |
| - | Targeted suites in the worktree | `pytest tests/jarvis tests/odds tests/ops tests/pulls tests/forward` | 759 passed, exit 0 | PASS |
| - | ruff / format / mypy | changed packages | 0 / 0 / 0 | PASS |

### Findings

**R1 (blocker, cheap).** The orchestrator can crash every tick while the heartbeat stays fresh, and then JARVIS holds
plays without end. `pulls_alive` (`nba/pulls/freshness.py:124-143`) checks age and "disabled" only, not `rc`.
`ops/nba_pulls.sh` writes a fresh heartbeat with `rc != 0` after every failing tick. Requests become `expired` only
when an orchestrator tick reaches step 1. So a tick that dies before step 1 (an import error after a bad merge,
`open_requests` raising) leaves requests `open` forever. `Gate.released` (`nba/jarvis/pullhold.py:44-54`) then never
releases them.

Shown by `test_adv_crashloop.py`: a 3-hour-old request, past its need_by, with a fresh beat and `rc: 1`. The alert is
held ("already open") and the preview leaves the play out ("1 plays held").

*Fix, both parts, about 6 lines:*
- In `Gate.released`, treat an `open` request with `self.now >= last.need_by_utc` as released. JARVIS then enforces
  the deadline itself.
- In `pulls_alive`, return `(False, "pulls tick failing")` when the beat's `rc` is not in `(0, 4, None)`.

Add a test with this scenario.

**R2 (major, bounded).** A paid on-demand call can become invisible to the caps (`orchestrator.py` `_Store.get`
reopening after `live_event_odds`). With `store_retries` 3 x `store_wait_s` 2 s, a lineups tick holding the store
for more than 6 s after the paid call raises `IOException`. `_serve` then logs "market store busy; requests stay
open": the credits sit in the ledger but there is no `live_capture_log` row and no odds rows. The next tick pays again
under the same `ondemand1`. The per-game cap (4) and the daily on-demand cap (20,000) are counted from
`live_capture_log`, so they never see these spends; only the 102k ledger cap does. Repeats are bounded by need_by (15
min, at most about 7 ticks).

*Fix:* once the paid response is in hand no network is held, so retry the reopen with the capture's budget
(`open_store` default, 6 x 5 s). Count caps from the ledger lines that carry `request_id`, not only from
`live_capture_log`. On final failure, ingest the raw file on the next tick instead of pulling again.

**R3 (minor).** Mismatched flags post the play on the same stale price anyway. With `hold_stale_slate: true` and
`hold_stale_alerts: false`, a strong play held out of the preview is alerted in the same tick on the same stale price
(`test_adv_slate.py` case 2: `sends` has `slate` then `alert` at 20:00). The shipped config sets both to true, so
this is latent. *Fix:* load_config refuses `hold_stale_slate` without `hold_stale_alerts`, or document that they go
together.

**R4 (minor).** The Daily Dollar is not gated. `dailydollar._leg_fair` (`nba/jarvis/dailydollar.py:30-32`) uses
`st.refs` with no age check, so the ticket in the same preview can be priced on the stale Pinnacle line that held the
other plays. *Fix:* when the slate gate is open, treat a leg whose ref is older than `gate.limit` as having no
Pinnacle price (fall back to the venue mid, as the module already does).

**R5 (minor, tests).** Survivors:
- **M8b:** a dry run would file real prefetch requests, which cost real credits once served. Add a dry-run prefetch
  test.
- **N10:** the watchdog's `t240` skip is untested. Add one test with `active_schedule()` lacking t240.
- **N8:** reverting `sent_keys` to all plays goes uncaught. Assert that a held strong play reaches the alert hold
  in the preview tick.
- **N6, N6b:** equivalent mutants (two fail-open layers each catch the other). No action.

**F2 (open, Devin's call, as logged).** Measured with the new counter: in the served case, the play is held at
20:00-20:01, the pull lands, then `lead_capped_after_hold=1` every tick 20:02-20:11 and no alert is sent. The 3-night
measurement in the DECISIONS row is the right next step. It will report thin caps, not latency.

Checked and fine: the config is loaded once per Runtime and a bad file fails open (killed mutant). Every new JARVIS
call site is wrapped. Request-store reads retry up to 3 s and then fail open on the first error (bounded). The
scorecard excludes on-demand rows in both loaders (killed N5, N5b). The slate read retry, the monthly plan rule and the
server floor are all tested (killed N11, N9, M10).

### Biggest risk
R1: the change promised to fail open, but an orchestrator that dies every tick and keeps beating makes JARVIS silent
on stale plays and trims the preview, with nothing but ALERTS.md lines to show for it.

### Not verified
The replay oracle, real launchd/TCC behaviour, real lock contention with the live lineups job, and a real vendor call.

### Drafted DECISIONS row (not appended)
| 2026-11-08 | Adversary re-review of agent/market-pull-orchestrator 539fa84: WOUNDED / BLOCKED on one item (docs/reviews/pull_orchestrator_premerge_2026-11-08.md, "Re-review 539fa84"). Flag-off and dead-job runs are identical to 8936003 over 8 ticks; the lock is released during vendor calls; the close guard and the slate hold behave. Blocking: R1 (a crash-looping orchestrator with a fresh heartbeat holds plays forever; fix = JARVIS treats open past need_by as released, and pulls_alive requires rc 0/4). Major, non-blocking: R2 (paid call invisible to the caps when the store reopen fails). | Follows the adversary review. | R1 fixed with a test; R2 scheduled. |
