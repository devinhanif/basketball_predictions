# Adversary review: ESPN freshness poller (agent/market-live-espn-fresh, 2c90a70)

_2026-11-10. Reviewer: adversary. Scope: `nba/live/espn_fresh.py`, the edits to `nba/live/capture.py` and
`nba/live/__main__.py`, `tests/live/test_espn_fresh.py` and its fixture, the DECISIONS row. Read from the worktree
`.claude/worktrees/market-live-espn-fresh`. No live ESPN request was sent. The running capture and launchd were not
touched. Season 2025 was not read. The offline checks read stored ESPN bodies in `data/raw/live_2026-10-21 .. 11-08`
(18 completed nights; 11-09 was still being written and was skipped)._

**Verdict: WOUNDED.** With the flag at 0 the branch changes nothing: no thread, no import, the same heartbeat bytes. The
measurement logic is sound, and the wallclock reference does not look like ingest time. Turned on, though, the poller has
four defects:
- It keeps polling at full rate through a 403, which is the exact signal the DECISIONS row says should stop it.
- Its heartbeat looks healthy while every request fails, the same blind spot as C2.
- The core-API parser was never tested on a real body, and if the shape is wrong it fails silently, reporting "never
  updates".
- It re-stores identical bodies whenever a live game has no plays yet.

Verdict: BLOCKED

(BLOCKED applies only to turning the poller on (`--fresh-interval-s > 0`), and F1 to F4 are a few lines each. Merging
with the flag at 0 is inert, as shown in check 1, but merging dormant code with known defects is Devin's call.)

**Top finding (F1, major).** `get()` backs off only on 429/5xx (`espn_fresh.py:201`). Any other non-200 status, such as
403, 404 or 400, goes to `log_error` with no backoff (`espn_fresh.py:210-212`). Worse, `ep.fails = 0; ep.next_ok = 0.0`
runs *before* that check (`:204-205`), so a 403 also clears any backoff a 429 had set. Measured with a mock transport,
2 live games, every game endpoint returning 403, 20 cycles: **80 game requests, 0 backoffs, 80 errors**. If ESPN starts
refusing us, which is the event the DECISIONS reversal clause is about, the poller keeps going at 3 requests per game
every 18 s.

## Model class and playbook check

Model class: none. This is an instrumentation and data-capture job on the live path, not a model. The cross-cutting
questions, answered for a capture job:
1. *Information or capacity?* Information: does polling faster see ESPN plays sooner? It is measurement only and no
   decision reads it.
2. *Enough data?* n/a (nothing is fitted). The offline baseline holds 16,743 live summary bodies from 18 nights.
3. *Test validity.* The fixture tests pass, but they never run the thread, never touch the `Capture` integration, never
   send a 403, and never use a real core-API body. Those gaps are where F1 to F4 sit.
4. *Lineage and inherited guards.* It inherits `USER_AGENT` and `RawStore` (write-once, manifest after rename). It does
   not inherit the main `Fetcher`'s rate limit or per-tick budget (by design: own client). It does not inherit the C2
   lesson (`last_success_ts`).
5. *What it is scored against.* The 60 s capture's first-seen lag (median 50 s, p90 96 s, DECISIONS row 2026-11-09
   22:10). See F7: as written, the reversal criterion for that comparison cannot be evaluated.

## Per-check table

| # | Check | Command / evidence | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Inert at flag 0 | Code read: `__main__.py:68-72` builds and imports `FreshPoller` only when `> 0`. `capture.py:199` adds `**{}` when `fresh is None`. `capture.py:564,580` are both guarded by `is not None`. `ops/nba_live_capture.sh` does not pass the flag. Heartbeat consumers (`nba/ops/live_capture.py` `plan`/`problem` read `date`, `status` and the file mtime; `nba/jarvis/live.py:45-52` reads `last_tick_ts`) read named keys only, so extra `fresh_*` keys cannot break them even when the flag is on. Raw readers (`jarvis/ingame.py:208`, `export/desk.py:116`, `markets/halftime_entry.py:102`) read `espn_summary/<game>` and the top-level `manifest.jsonl`. The poller writes `<out>/fresh/` with its own manifest, so nothing collides. Suite: `.venv-1/bin/python -m pytest -q -p no:cacheprovider tests/live` | 29 tests | 29 passed, exit 0 | PASS |
| 2a | Thread cannot crash the tick | `run()` catches `Exception` per cycle (`:359-362`). The thread is a daemon (`:367`). It shares no object with the main tick: own client, own `RawStore(out/"fresh")`, own files. The main thread only reads ints in `heartbeat_fields` (safe under the GIL). | code | holds | PASS |
| 2b | Shutdown | `scratchpad/attack_poller.py` A4: a request that hangs 20 s, then `close()` | 1 | `close()` returned after **15.0 s** with the thread **still alive** (daemon) | WEAK (F5) |
| 2c | Double start / restart | `start()` runs once per `Capture.run`. A launchd restart is a new process, and the shell lock blocks a second writer. On restart `prev` is empty, so the first poll of each game is stored again and marked `first_poll` (excluded from lag). | code | holds | PASS |
| 2d | "run never raises" under disk-full | Code read: `store.save` raises `OSError`. The handler calls `log_error`, which opens `fresh_errors.log` for append and raises again, outside any try (`:362`→`:180`). The thread dies. `fresh_alive=False` is in the heartbeat, but nothing reads it. Main tick unaffected. | code | claim false at the edge | WEAK (F6) |
| 3 | Known-at | `fetched_at = self.now()` after the response returns (`:200`), wall `datetime.now(UTC)`. Monotonic is used only for scheduling and backoff (`:187,221,227,358,363`). Known-at is conservative: it includes request latency. Server `Date`/`Age`/`Cache-Control` headers are captured in `Fetched.headers` and then dropped, so Mac clock skew and CDN cache age cannot be separated from source lag. | code | correct; incomplete | PASS (F8 minor) |
| 4a | Backoff on 403 | A1: 2 games, all game endpoints 403, 20 cycles | 20 cycles | 80 requests, 0 backoffs | **FAIL (F1)** |
| 4b | Heartbeat while failing | A1b: scoreboard 403 from the start, 20 cycles | 20 cycles | `fresh_cycles=20`, `fresh_live_games=0`, `fresh_last_cycle_ts` advancing, `fresh_backoffs=0`. This is indistinguishable from "no game live". A1: `fresh_cycles` and `fresh_last_cycle_ts` advance while 100% of requests fail. No `last_success_ts`. | **FAIL (F2)** |
| 4c | Request budget | Offline: 16,743 live summary bodies / 18 nights ≈ 930 live game-minutes per night. At 18 s that is ×3.33 game-cycles × 3 requests (summary + core head + core page) ≈ 9.3k, plus about 800 scoreboard calls over the ~10 h window, ≈ **10k per average night**. A 15-game night (~2,160 game-minutes) is about 22k. The "~12k" figure is a typical night, not the peak. | 18 nights | consistent | PASS (note) |
| 5 | Storage growth | `scratchpad/attack_store.py`: consecutive live summary bodies at 60 s | 16,743 bodies | Same sha **883 (5.3%)**; same newest play 4,435 (26.5%, matching the claimed 26.7%); median 29.9 KB gz per body. "Store only changed" therefore stores about 95% of bodies at 60 s. At 18 s the upper bound is ≈ 3,100 × 30 KB ≈ **93 MB/night**, on top of 100–380 MB/night today. The disk has **14 GiB free (97% used)**. Bug: an in-progress game with an empty `plays` list is stored on every fetch (A2: 10 identical bodies, saved=10) because `prev` is set only `if sha and lp` (`:268`). | 18 nights | growth acceptable; one bug | WEAK (F4) |
| 6 | Core-API parser on an unseen shape | A3: items as `$ref`-only (no inline `id`/`wallclock`) | 10 cycles | **0 errors**. Report: `unchanged_rate=1.0`, lag n=0. This reads as "core API never updates", not as "parser wrong". No real core body exists anywhere in the repo or `data/raw`. | **FAIL (F3)**: silent |
| 7 | Wallclock bias | `attack_store.py` on the final body of each game, consecutive plays in the same period | 70,124 pairs | Wallclock advances >1 s slower than the game clock (the signature of batch ingest stamping) in **158 pairs (0.23%)**, median shortfall 4 s. Ties: 28,372 (40%), at dead balls (same game clock). Non-monotonic: 1,278 (1.8%). Negative lag (wallclock after our fetch): **12 / 16,735** (min −640 s). Wallclock behaves like event time, not ingest time. | 70,124 / 16,735 | not biased in the ingest direction | PASS (F7 on interpretation) |
| 8 | Tests meaningful | Coverage of `espn_fresh.py` 88%. Uncovered: `start`/`close` (`:367-373`), all core error branches (`:312-324`), summary parse error (`:281-283`), scoreboard 304 (`:232`). No `Capture` + `fresh` test anywhere (`grep fresh tests/live/test_capture.py`: none). No 403 test. No flag-0 heartbeat-equality test. Core fixture is hand-made. | 6 tests | happy path only | WEAK (F9) |

Verification (unpiped, from the worktree, `.venv-1`): `ruff check nba/live tests/live` exit 0. `ruff format --check` exit
0. `mypy` on the four touched files exit 0. `pytest tests/live`: 29 passed, exit 0.

## Findings (severity, line, fix)

- **F1 (major): no backoff on 403/404/400, and a 403 resets an armed backoff.** `espn_fresh.py:204-212`.
  Fix: move `ep.fails = 0; ep.next_ok = 0.0` below the 200/304 branches. Route every non-200/304 status through
  `_fail()`. Add a stop latch: after N (say 5) consecutive 401/403 on any endpoint, set `self.stop`, write
  `fresh_stopped_reason="http 403"` and an `ALERTS.md`-worthy error line. A 403 is the reversal trigger in the DECISIONS
  row, and the poller should honour it by itself. Test: A1 must show requests ≤ 2 per endpoint and then zero.
- **F2 (major): heartbeat healthy while failing (the C2 blind spot again).** `espn_fresh.py:352-353,375-388`.
  Fix: add `fresh_last_success_ts` (set on any 200/304), `fresh_last_scoreboard_ok_ts`, and `fresh_consecutive_fail`.
  Keep `fresh_last_cycle_ts` but do not treat it as health. Nothing reads `fresh_*` today, so add a one-line problem in
  `nba/ops/live_capture.problem` (only when `fresh_alive` is present): "fresh poller: no success for 5 min inside the
  window" or `fresh_alive false`. That edit belongs to whoever owns `nba/ops/`, not this branch. As shipped, a poller
  that never gets a byte looks the same as a quiet night.
- **F3 (major for the measurement, unverified): the core-API parser was never tested on a real body, and if the shape
  is wrong it is silent.** `espn_fresh.py:83-95,311,321`.
  Fix: (a) treat a core item without `id` or `wallclock`, or a body without `count`, as a parse error (`log_error`, plus
  a `fresh_core_bad` counter). (b) Record one real body as a fixture. That takes 2 requests on a finished game
  (`.../events/<id>/competitions/<id>/plays?limit=1` and `&page=<count>`), and Devin runs them, per the
  downloads-via-Devin rule. (c) Assert in a test that `$ref`-only items raise the counter. My recollection is that the
  plays endpoint returns inline items, but I could not verify it offline.
- **F4 (minor): identical bodies re-stored when the live summary has no plays.** `espn_fresh.py:268-269`.
  Fix: set `self.prev[(endpoint, game)] = (sha, lp.play_id if lp else "")` whenever `sha` is set. Only `changed` should
  depend on `lp`.
- **F5 (minor): `close()` can return while a request is still in flight.** `espn_fresh.py:370-373` with `TIMEOUT=8.0`
  per phase and up to 3 sequential requests per game. The stop check sits between games only (`:348`).
  Fix: call `self.client.close()` after the join, check `stop` before each `get`, and cap the read with
  `httpx.Timeout(8.0, read=5.0)`. Harmless today (daemon thread, writes are append-only lines), but it means
  `fresh.jsonl` can get rows after the `done` beat.
- **F6 (minor): "never raises" is false on disk-full.** `espn_fresh.py:180,362`. Wrap the file write in `log_error` in
  `contextlib.suppress(OSError)` and stop on a second consecutive `OSError`. Separately, for Devin (not caused by this
  branch): **the data volume has 14 GiB free (97% used)**, and the live capture alone writes 100–380 MB a night.
- **F7 (minor, interpretation): the 50 s offline lag is not source delay, and the reversal criterion is ambiguous.**
  With polls `d` = 60 s apart, first-seen lag = source delay + U(0, d). The median 50 s therefore implies a source delay
  of roughly 20 s, and an 18 s poller should show about 20 + 9 ≈ 29 s if ESPN publishes continuously. The DECISIONS
  clause "first-seen lag median within 10 s of the 60 s cadence" does not say whether it means the 60 s *lag* (50 s) or
  the cadence (60 s).
  Fix: before the first night is read, write the comparison as a rule: "the poller shows a gain if its first-seen-lag
  median is ≥ 15 s below the 60 s capture's median *on the same games and nights* (the capture runs alongside), with a
  game-clustered bootstrap CI via `truth/bootstrap.py`". Also note that newest-play freshness is not box-score freshness:
  in 131 same-newest-play/changed-body pairs on 10-28 (4 games), `plays` changed in 46 and `boxscore` in 46. Corrected
  or inserted plays and stat updates are invisible to this metric.
- **F8 (minor): source headers are dropped.** `espn_fresh.py:209,217`. Write `date`, `age`, `cache-control`, `etag`
  and `last-modified` into the `fresh.jsonl` row. `Date` bounds Mac clock skew (the 12 negative lags, min −640 s,
  are unexplained). `Age` separates CDN cache staleness from origin staleness, which is the real question behind "is the
  142 s gap server-side or our cadence".
- **F9 (minor): tests miss the risky paths.** Add (a) a `Capture` test with `fresh=None` asserting the heartbeat key set
  equals today's. (b) A `Capture` test with a stub fresh object asserting `start` once and `close` once, and nothing
  when `once=True`. (c) The A1, A1b, A2 and A3 cases above as regression tests. (d) A thread test: `start()`, `close()`
  returns within the join with a fast transport.
- **F10 (note, scope): per-cycle duration is not recorded.** With 15 live games and three serial requests each, a slow
  night pushes the cycle past 18 s, so `freshness_report`'s "[lag − d, lag]" bound uses the nominal `interval_s`, not
  the real one. Log `cycle_s` and use the observed per-game fetch gap as `d`. Also, `freshness_report` crashes on a
  torn last line (`:408`). Skip unparseable lines and count them.

## Corrected family and power note

No inferential claim is made on this branch. It is descriptive and measurement only, so there is no family to correct.
The single quantitative claim (offline 26.7% unchanged, lag median 50 s / p90 96 s) reproduces: same newest play
4,435 / ~16,603 consecutive live pairs ≈ 26.7%. The DECISIONS row's numbers carry n but no CI. They are descriptive and
should be labelled "descriptive, no CI" until F7's rule exists. Power for the future comparison: per-game lag medians
across about 140 game-nights give a game-clustered CI of a few seconds, so a 15 s difference is detectable. This is not
computed here because there are no poller rows yet.

## The one biggest risk

If the claim is wrong, the most likely reason is a silent measurement failure, not a crash. The core-API parser reads
the wrong shape (F3), or ESPN refuses us (F1/F2), and the night's `fresh.jsonl` then shows "unchanged" or "no live
games". That would be read as "ESPN is slow" or "faster polling does not help", and the poller would keep sending
requests the source has refused.

## Not verified

- The real core-API plays body shape, the page semantics of `page=count`, and caching. No real body exists locally,
  and no live request was sent.
- Whether ESPN sends `ETag`/`Last-Modified` at all. If not, the conditional GET is dead code. That costs nothing, but
  `fresh_304` will read 0.
- Behaviour on a real night: thread contention with the main tick (expected negligible: ~30 KB gz bodies, a few
  json.loads per 18 s) and the actual cycle duration.
- Whether the Mac clock is NTP-synced during capture (F8 would measure it).

## Recommended changes (not applied)

F1, F2 (fields on this branch, the `problem()` line by the owner of `nba/ops/`), F3(a)(c), F4, then F9(c). After that,
ask Devin for the 2-request core fixture (F3b) before the first night with the flag on. F5 to F8 and F10 can follow.
Write the F7 rule and commit it before the first poller night is read.

## Drafted rows (not appended)

DECISIONS (draft, for the main session):
`| 2026-11-10 | ESPN freshness poller review: WOUNDED/BLOCKED for flag > 0 until 403 backoff+stop latch (F1), last_success heartbeat (F2), core-parser loud failure (F3), empty-plays re-store (F4) are fixed; flag 0 is inert (29 tests, code read). | Adversary review docs/reviews/espn_fresh_poller_2026-11-10.md | A real core-API fixture shows a shape the parser handles and F1–F4 land with tests. |`

## Re-review: c2dbc39 (diff 2c90a70..c2dbc39), 2026-11-10

Scope: the fixes in `nba/live/espn_fresh.py`, the new tests, the DECISIONS row and the DRAFT rule
`docs/prereg/ESPN_FRESH_POLLER_NIGHT1.md`. I re-ran every plant with
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/attack_poller_v2.py`
(mock transport, no live request), from the worktree at c2dbc39.

**Re-review verdict: SURVIVES for merging with the flag at 0. Turning the poller on stays WOUNDED (R1 below).**

Verdict: APPROVED

(The approval covers the merge with `--fresh-interval-s` at its default of 0. It does not cover turning the poller on,
which still waits for R1 and for a real core-API fixture (F3b).)

### Plants re-run

| Plant | Before (2c90a70) | After (c2dbc39) | Pass |
|---|---|---|---|
| 403 storm, 2 live games, all game endpoints 403 | 80 game requests in 20 cycles, 0 backoffs | 5 game requests, then it stops itself: `stopped_reason='http 403 x5'` | PASS |
| 403 storm, **1** live game, scoreboard OK | n/a | Backoff works (10 game requests in 40 cycles, about 12 min). It **never stops**, and `fresh_last_success_ts` stays current with `fresh_consecutive_fail=0` | **FAIL (R1)** |
| Scoreboard 403 from the start | heartbeat looked like a quiet night | Stops after 5: `fresh_stopped_reason='http 403 x5'`, `fresh_last_success_ts=''`, `fresh_consecutive_fail=5` | PASS |
| `$ref`-only core body | 0 errors, `unchanged_rate=1.0` | `fresh_parse_bad=10`, core rows not written, endpoint absent from the report | PASS |
| Empty-plays body ×10 | saved=10 | saved=1 | PASS |
| `close()` with a 20 s in-flight request | returned at 15.0 s, thread alive | Still 15.0 s, thread alive. It exits once the request returns (alive=False after the request finished). `read=5.0` caps real hangs, but the mock does not honour timeouts. | PASS (minor, R3) |
| Disk full (`store.save` raises ENOSPC; error log path unwritable) | thread died | Thread alive, cycles continue | PASS (see R2) |
| Flag 0 inert | identical | `capture.py` and `__main__.py` are untouched in this diff (0 lines). The new test `test_capture_wiring_and_flag_off_heartbeat_unchanged` asserts no `fresh*` keys when off. Consumers read named keys only (unchanged). | PASS |

Verification at c2dbc39 (unpiped, `.venv-1`): `ruff check nba/live tests/live` exit 0. `ruff format --check` exit 0.
`mypy` on the 4 files exit 0. `pytest tests/live --no-cov`: **38 passed, exit 0**.

### New findings in the revision

- **R1 (major for enabling, not for merge): the scoreboard's successes hide the game endpoints' 403s.** At
  `espn_fresh.py` `get()`, every 200/304 resets `consecutive_auth` and `consecutive_fail` and sets `last_success_ts`,
  including the scoreboard, which succeeds every 45 s. With one live game, the game endpoints add at most 4 auth
  failures per 45 s window, so the 5-in-a-row stop never fires. The heartbeat then reports success while every game
  request is refused. The DRAFT rule's invalid-night trigger (`fresh_stopped_reason`) would not fire either. The backoff
  does cut the request rate, so this is a health and measurement problem, not a politeness one.
  - **Fix:** keep the counters per endpoint class (scoreboard, summary, core). Add `fresh_last_game_success_ts`, set only
    by summary or core 200/304. Stop on 5 consecutive 401/403 within any one class.
  - **Test:** the 1-game plant above must stop.
- **R2 (minor): a failed save is never retried.** `record()` sets `prev[(endpoint, game)] = (sha, ...)` before
  `store.save`. When the save raises (disk full), the next identical body counts as unchanged and is never stored. The
  jsonl row exists but no raw file backs it. Fix: set `prev` only after a successful save, or on `OSError` clear
  `prev` for that key.
- **R3 (minor): `close()` still returns at 15 s and then closes the client under an in-flight request** (`close()`:
  join, then `self.client.close()`). The thread's resulting exception is caught by `run()`, so this is harmless. Note it
  rather than fix it.
- **R4 (minor, tests):** the wiring test runs `run(once=True)` against an empty scoreboard, which returns `no_games`
  before the start/close code is reached. So `stub.started == 0` is trivially true, and nothing asserts that a normal
  window calls `start` once and `close` once. The flag-off check asserts that no `fresh*` keys appear, not equality with
  the pre-branch key list. That is sufficient here only because `capture.py` is unchanged in this diff.

### DRAFT rule `ESPN_FRESH_POLLER_NIGHT1.md`: quick read (not a freeze review)

The structure is sound: paired, same games and nights, game-clustered bootstrap, m = 1, a minimum-n gate, no variants.
The lag reading and the budget are now correct. Points to settle before freezing:
- **D1:** state the hypotheses the 15 s floor separates. If ESPN publishes continuously, the expected mechanical
  `delta` is (60 − 18)/2 ≈ 21 s. If ESPN batches server-side at ≥ 60 s, it is ≈ 0. A 15 s floor separates these, but
  the rule should say so. Otherwise a pass reads as a discovery when it is the expected result of a shorter poll interval.
- **D2:** the invalid-night trigger "`fresh_parse_bad` > 0 on the core endpoint" cannot be read from the heartbeat
  (`parse_bad` is one counter for summary and core). Name the source (`fresh_errors.log` lines `parse_bad core`) or
  split the counter. Excluding a whole night because a descriptive endpoint failed throws away valid summary data.
  Consider excluding only on summary `parse_bad`.
- **D3:** add R1's case to the invalid-night conditions: any night where game-endpoint auth failures occurred
  (counted from `fresh_errors.log`), whether or not the poller stopped.
- **D4:** name the exact function: `nba/truth/metrics.py::bootstrap_ci` on the per-game deltas, one-sided 95%,
  2,000 draws, seed 0. Also say how a poller restart mid-game is handled. Today rows after a restart are `first_poll`
  and are excluded, which is fine, but say it.
- **D5 (non-negotiable 8):** the gate is 30 game-nights. At about 8 live games a night that is about 4 nights with the
  flag on, so it is feasible. It cannot start until R1 and F3b are done and Devin decides about the disk (14 GiB free).

### Biggest remaining risk

R1: a single-game 403 stretch, or one late game left alone on the slate, would produce a night that looks healthy and
valid but contains no game data. The draft rule would admit it.
