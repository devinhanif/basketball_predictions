# Pre-merge review: hourly Pinnacle pulls T-480..T-240 (`agent/steward-odds-hourly`, 8936003), 2026-11-08

Change: `configs/odds_live.yaml` adds kinds t240/t300/t360/t420/t480 (window +/-2.5 min, extended) and
raises `daily_max_credits` 72,000 -> 102,000; `tests/odds/test_live_close.py` and
`tests/odds/test_us_ex_region.py` updated to match. No code change. Live path (configs/), so it also needs
Devin's go and an unchanged replay oracle before merge (CLAUDE.md "Branches, not main"). I did not run the
oracle.

**Classification: SURVIVES.** No reader of `live_book_odds` changes meaning when the new kinds appear, the
budget holds with wide margin at the real per-call cost, and the newest-by-`requested_at` read in JARVIS
means these pulls do fix the 238-min reference. There are four findings, none blocking: one config comment
that is false (F1), a send/pull timing collision (F2), a label change that comes with fresher refs (F3), and
a close-capture miss that predates this branch and that the false comment hides (F4).

Verdict: APPROVED

Model class: none (capture schedule, not a model). Playbook check: information or capacity: information
(a fresher reference price); enough data: n/a; test validity: config and unit tests only, no scoring claim;
lineage: inherits the existing guards (post-tip refusal, done_games idempotency, daily cap, server floor);
scored against: nothing. This is not a claim and needs no prereg.

## 1. Readers of `live_book_odds` / `snapshot_kind`

| Reader | How it picks rows | Effect of t240..t480 |
|---|---|---|
| `nba/jarvis/sources.py:297-302` `_latest_rows` | `max(requested_at)` per game for the book, `requested_at <= now`, no kind filter | **Intended fix**: the newest Pinnacle pull wins whatever its kind |
| `nba/jarvis/slate.py:107,184` | `ref_age = now - ref.ts`; > `max_ref_age_min` (120, `configs/jarvis.yaml:53`) caps at thin | Fresher refs mean the cap fires less (see F3) |
| `nba/forward/scorecard.py:333,417` | `close` kind if present, else latest pre-tip | Unchanged. The fallback only matters with no close, and then the latest pre-tip row is t5..t60, never a new kind |
| `nba/forward/scorecard.py:427` `open_line` | `t180` by name, else earliest pre-tip | Unchanged (t180 is still there; `morning` was already earlier) |
| `nba/export/reader.py:647-664` | newest per (book, market, point, side) with `received_at <= made_at` | Fresher lines for forecasts made between 10:00 and T-180 (intended). The existing ghost-point behaviour (a point a book has dropped stays at its old price, visible by `snapshot_ts`) gets a few more chances. Minor and already there |
| `nba/parlay/top_source.py:470-487` | latest snapshot per (book, market, game) `<= made_at` | Same as above but correct (game-level latest snapshot, so no ghosts) |
| `nba/ops/coverage.py:476` | lists kinds by first `received_at`, "latest kind" | Informational only; the list gets longer. No alert keys on kind |
| `nba/ops/watchdog.py` | no odds-kind expectation | none |
| t60 or t5 only: `nba/shadow_llm/payload.py:222`, `nba/explain/data.py:350,377`, `nba/markets/decision_scoring.py:199`, `nba/markets/market_slow.py:44-45`, `nba/parlay/line_cal_build.py:61` | kind by name | none |
| `nba/eval/q1_starters_live.py:226,541` | t15 by name | none |
| `nba/odds/the_odds_api.py:59` `SNAPSHOT_KINDS`, `history_pull.py` | odds_history, a separate table | none |

Grep: `grep -rn -E "snapshot_kind|live_book_odds" nba ops`, plus `grep -rn -E '"t(120|180|90|45|30|15)"' nba` for
hard-coded kind lists. Nothing enumerates live kinds in a way that would miss or mislabel the new ones.
`_KIND_RE` (`nba/odds/live_close.py:71`) accepts `t[0-9]{1,4}`.

## 2. Budget (real ledgers, `data/odds/budget/the_odds_api_live_2026-10-20..11-08.jsonl`)

- Real per-call cost: mean 236.3, max 237, n = 1,498 `event_odds` calls; none above the 267 bound.
- Per game: 17 x 237 = 4,029 real (4,539 bound). A 15-game night costs 60,435 real (68,085 bound); 16 games
  cost 64,464 (72,624). Against 102,000 that leaves 29-41k of headroom, about 110-150 extra calls.
- Retries: `max_attempts_per_game` 2 applies only to non-terminal statuses (`_DONE_STATUSES`,
  `live_close.py:116`). `unmatched` makes no odds call (the events call is free), so retries cost about 0.
  Busiest real day: 2026-11-02, 15 games, 41,133 credits on 12 points, which scales to about 58k on 17.
- Close refused? The guard checks `spent + 267 > cap` (`the_odds_api.py:446-455`). Close runs first within a
  tick (`due_order`, `live_close.py:268-270`), but across a day every earlier kind has already spent. To
  refuse the last close you would need about 380 calls before it, which is 1.5x a full 16-game schedule.
  `done_games` idempotency makes that reachable only through a bug, not a busy night. No reserve exists for
  the close, so the margin is the only protection (recommendation R3).
- Monthly: the bound is 215 x 17 x 267 = 975,885 against the 2.5M rule (0.5 x 5M); real is about 866k.
  `active_schedule` keeps all 17. Cycle to date: server_used is 162,564 on 11-08 (cycle reset about 10-31).
  The rule counts only live capture, but the key is shared: in October other pulls (history, periods,
  movement) took server_used to 3.96M. The only coupling is `min_server_remaining` = 100k. If a history pull
  that size happens late in a month, all 17 kinds are refused together, close included, once the floor is
  reached. This predates the branch, but the branch adds about 42% to the live share (R4).

## 3. Windows

- **F1 (minor, wrong comment) `configs/odds_live.yaml:80`** says "a 5-minute-wide window is hit by exactly
  one tick". The real launchd cadence is 305-315 s, not 300. Poll snapshot ids on 2026-11-02 read
  00:19:38, 00:24:53, 00:30:06Z, and log lines 19:22:02, 19:27:13, 19:32:23. A [lo, lo+5] window is
  therefore missed outright roughly 10/310, about 3% of the time. It is never hit twice, because
  `done_games` dedupes. For the hourly kinds a miss is harmless: the previous hour's pull or t180 covers it,
  and the worst case is a ref about 2 h old. Fix: reword to "hit by at most one tick; about 3% are missed
  because the tick period is about 5 min 10 s".
- Coverage since 2026-11-01 (51 regular-season games, read-only copy `mao.duckdb`): t20..t180 and morning
  51/51, t15/t5 50/51, t10/t45 49/51, close 45/51 (see F4).
- Overlap with `morning`: for tips at or before 18:00 ET, t480 (and for earlier tips t420/t360/t300) lands in
  morning's [10:00, 11:00) grace. Both pull in the same or the next tick: a duplicate 237-credit call with
  near-identical data. It is harmless and budgeted (the 17 x 267 bound already counts it). R2 would save it.
- Early tips: `ops/nba_lineups.sh:56-57` exits before capture from 03:00 to 09:00 ET. For a 12:00 ET tip,
  t480..t240 (04:00-08:00) never run: no credits, no capture-log row. Morning (10:00) and t180 (09:00) still
  run. Harmless. For a 13:00 tip only t240 (09:00) runs. Doubleheaders are fine: each game is selected
  independently by `select_window` (`live_close.py:431-446`).

## 4. Does it fix the staleness?

Yes. `_latest_rows` reads the newest Pinnacle `requested_at <= now` per game with no kind filter and no
cache; `load_pinnacle` is called fresh per slate build (`slate.py:107`). For the 2026-11-08 case
(`desk.json` plays[130]: LAL@DET, tip 23:00Z, send 18:59:38Z = T-240.4, ref 238 min old = 10:01 ET morning),
t300 at 13:00 ET would have given a ref about 60 min old.

- **F2 (minor) send/pull collision.** The slate preview fires at T-90 to T-92 of the first tip
  (`configs/jarvis.yaml:90`). Tips sit on the :00/:30 grid, so sends land at :28-:30 or :58-:00, about 1 min
  before the next scheduled pull (t240 for this game would have been at 14:00, 21 s after the send). The
  same pattern shows on 11-02 (send 22:29Z, BOS@HOU T-181, ref 444 min) and 11-07 (20:28Z). With this
  branch, sends will typically see refs about 59 min old, not about 0. Fix: either have JARVIS take the
  first watch tick after T-90 plus 3 min, or shift the hourly windows by 5 min (e.g. t245 [242.5, 247.5]).
  Neither is needed to merge.
- **F3 (major for Devin's awareness, not a defect) label cap stops firing.** Refs older than 120 min capped
  labels at thin (`slate.py:184`). With hourly pulls nearly every pre-T-180 ref is under 120 min, so those
  plays can now carry stronger labels and stakes. Descriptive only, not a claim (selected sent plays; my
  scratch bootstrap is unpaired and game-clustered, seed 20261108, not `truth/bootstrap.py`): edge in sent
  plays with a ref older than 120 min averaged +2.60c (n = 38 plays/38 games), against +0.62c with a ref of
  60 min or less (n = 45/45). Difference +1.98c, 95% CI [1.15, 2.90]. Stale refs flattered measured edges,
  so this change should shrink the edges JARVIS reports. That direction is the honest one, but Devin should
  expect fewer "+5c" plays.
- Other causes of the age: none found. Pinnacle props were present at the 10:00 pull (the 238-min refs were
  prop refs), so props were not absent at early kinds. One residual risk: `open_ro` retries 4 x 1.5 s
  (`sources.py:91-103`) and returns no refs if the capture writer holds `market_asof.duckdb`. More kinds
  mean more writer windows in 10:00-19:00 ET, each about 3-10 s. I did not measure the lock-failure rate.

## 5. Tests

`tests/odds/test_live_close.py` and `tests/odds/test_us_ex_region.py` plus `tests/jarvis`, branch code via
`PYTHONPATH=<worktree>`: **288 passed, exit 0** (`--no-cov`; with coverage on, the subset run gives
45 passed, exit 1 from the global 70% fail-under, not a test failure).
- Cap guard intact: `allowed == 102000 // 267 == 382`, `16 * len(schedule) <= allowed`,
  `2 * estimate(15) > cap` (136,170 > 102,000).
- "Alternates drop first" intent is kept. The tight plan moved from 900k to 1.3M because at 900k the
  17-point schedule now drops alternates and extended points both (614,040 > 450,000). The new value checks
  the right ordering: 975,885 > 650k drops alternates; 614,040 <= 650k keeps all 17. The `small` (20k) case
  still checks that extended drops next. Brittle but correct: `mid` (2M) passes by 24k (975,885 <= 1M). A
  new kind would flip it. A real-world consequence the test does not record: on a plan of 1M or less,
  every extended point including t5..t180 would turn off.
- Missing negative test (minor): nothing asserts a close is still allowed after a full day of earlier kinds
  at the cap, i.e. that the cap leaves room for the close.

## F4 (pre-existing, outside this diff, major): closes are missed

Close captured for 45/51 games since 11-01. Five 19:30 ET tips on 11-02 had t5 at 00:24:5xZ and the next
tick at 00:30:06Z, after tip. The close window [0, 5] (`configs/odds_live.yaml:88`) is effectively narrower
than 5 min, because the capture runs after the poll inside the tick, and the period is about 5:10. Scorecard
lines then fall back to `latest_pre_tip` (t5). This branch neither causes nor fixes it, but the comment F1
corrects is what hid it. Handle it as its own task, for example close [0, 7.5].

## Not verified

Replay oracle; mypy/ruff on the branch (config and tests only); the market_asof read-lock failure rate under
the larger schedule; the vendor's charge for errored calls (assumed 0, as with unmatched).

## Recommendations (not applied)

- R1: reword `configs/odds_live.yaml:80` (F1).
- R2: optional. Skip a relative kind when the same game already has a pull within 30 min (morning overlap);
  this needs code, so it is a new task.
- R3: optional. Add a test (or a reserve in the guard) that a close fits after a full 16-game day of earlier
  kinds.
- R4: add a DECISIONS line saying history pulls must leave about 1M per month for live capture on the
  shared key.
- R5: open a separate task for F4.

Drafted DECISIONS row (not appended): `2026-11-08 | Hourly Pinnacle pulls T-480..T-240, cap 102k |
JARVIS slate sends saw 238-min refs; 17 points x 237 real = 4,029/game, 15 games 60k | reverse if the monthly
live spend exceeds 1.2M or the ref-age cap no longer fires and labels inflate`.
