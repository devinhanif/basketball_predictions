# Pre-merge adversary review: agent/market-out-gap (70f2aba) and agent/steward-dst-followups (a5f6bf3), 2026-11-08

Reviewer: adversary. Both branches were read in their own detached worktrees (`/Users/devin/Downloads/nba-wt-adv-outgap`,
`/Users/devin/Downloads/nba-wt-adv-dst`; tracked `data/` moved to `data.tracked_aside`, main's `data/` symlinked, checked
with `ls -la`). Neither branch was modified. The main checkout was not touched, apart from this file. No season-2025 row was
read. The replay oracle was not run (the main session runs it). Every DuckDB open was `read_only=True`.

| Branch | Verdict | Line |
|---|---|---|
| agent/market-out-gap 70f2aba | **WOUNDED**: the gate works live, but its known-at check compares ET with UTC | Verdict: BLOCKED |
| agent/steward-dst-followups a5f6bf3 | **SURVIVES** | Verdict: APPROVED |

Both branches edit `nba/parlay/analysis.py`, but they do not conflict: `git merge-tree --write-tree 70f2aba a5f6bf3` gives a clean
tree 9cf6fcc8 with both the retry attach and `drop_out_on_report` in it. **Merge order:** DST first. It is small and passes its
checks, and the 22:20/22:50 slots matter on every late-tip night. Merge out-gap after M1 is fixed. To merge DST safely, see D-M1.

---

## 1. agent/market-out-gap: WOUNDED

### Re-derived: the gap is real
On the season-2026 live store (`nba.duckdb`, read only; `as_of` converted ET to UTC, then compared with `forward_predictions.tipoff`
and `made_at`), I counted the (game, player) pairs whose newest pre-tip report row is OUT and is newer than the latest
`props_context_residual` row. **266 pairs over 103 games** since 2026-10-20. The branch claims 269 over 104. The small gap is
probably the tip cut. Script: `scratchpad/rederive_gap.py`. The gate addresses a real hole.

### Findings (severity ranked)

**M1 (major): the known-at comparison mixes clocks.** `nba/parlay/out_gate.py:66-68`
(`WHERE game_id IN (...) AND as_of <= ?` bound to `as_of`). The docstring at `:53-54` says the argument is "naive UTC", and every
caller passes naive UTC: `slate.py:187`, `top_source.py:433` (`now`), `jarvis/sources.py:183` (`utcnow()`). But
`player_availability.as_of` is the report's **naive ET wall clock**. `nba/ingest/availability.py:478` stores `as_of = report_dt`;
`nba/daily/injury.py:172` says "``player_availability.as_of`` is the report's ET wall clock; compare in ET"; and
`nba/explain/data.py:8` says the same. Live data agrees: the newest row on 2026-11-08 has `as_of` 11:45:00 while the clock read
12:17 EST (17:17 UTC).
- Planted-future attack (`scratchpad/attack_outgap.py`, exit 0). One OUT row was published 14:15 ET on 2026-11-02 and stored the
  way production stores it. Serve times of 09:30, 13:00 and 14:00 ET, all *before* the report existed, gave `refused=True` every time.
- On real data (`scratchpad/rederive_gap.py`), a back-dated run at serve = tip - 60 admits **1,999 report rows published after
  serve, over 116 games**. It refuses **63 pairs that were not OUT on any report known at serve**. It wrongly prices none (0).
- **Live effect is nil today.** With the real clock, every stored row has ET `as_of` <= now ET < now UTC, so the filter only ever
  sees the past. The harm falls on the stated constraint and on `--now` runs: `nba/daily/replay_season.py:411` and `:547`
  (`parlay_pass`) call `nba.parlay evaluate --now <rt>`, so any replayed parlay shadow log would carry reports from up to 5 h in
  the future. The branch's own tests store `as_of` on the UTC clock: `tests/parlay/test_out_gap.py:24` writes
  `OUT_AT = datetime(2026, 10, 20, 18, 15)  # 14:15 ET`. That bakes the wrong convention in, so
  `test_known_at_a_report_after_the_serve_time_is_not_used` passes while the property it names is false.
- **Smallest fix:** in `load_out_on_report`, convert before binding:
  `serve_et = as_of.replace(tzinfo=UTC).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)` and bind `serve_et`.
  Correct the docstring at `:53`. In `tests/parlay/test_out_gap.py`, store the report rows on ET (`OUT_AT = datetime(2026, 10, 20, 14, 15)`,
  `back = ...14, 45`). Add one negative test: a 14:15 ET row must be ignored at serve 18:00 UTC (13:00 EST on 2026-11-02) and used
  at 19:30 UTC. In `tests/jarvis/test_out_gap.py:23-27` (`_avail`), write `et_naive(at)`.
- Not introduced here, same defect: `nba/parlay/top_source.py:375` (`_out_names`, `as_of <= made_at`, with `made_at` in UTC) has
  existed on main. It is worth fixing in the same commit with the same helper.

**m1 (minor): the toolbox mislabels the clock.** `nba/parlay/assistant/toolbox.py:394` (branch) prints
`({note.as_of:%Y-%m-%d %H:%M} UTC)`, but the value is ET. Devin would read "14:15 UTC" for a 14:15 ET report. Fix: print `ET`.

**m2 (minor, latent crash path in JARVIS).** `out_gate.py:70-73` does `int(p)` on `player_id`, a nullable column
(`injury_intraday.py:539`). If a NULL-player row is the newest OUT row in its partition, the result is `TypeError`
(attack output: `null pid -> EXC TypeError`). `jarvis/sources.py:182-185` catches only `duckdb.Error`, so that would end
`gather`, which is the 10-28 failure class. Production has 0 such rows today (144,691 rows checked), so the risk is latent.
Fix: add `AND player_id IS NOT NULL` to the inner `WHERE`.

**m3 (minor): a held lock turns the JARVIS gate off for a whole tick.** `jarvis/sources.py:179` retries 4 x 1.5 s and then
notes "OUT-player filter is OFF". The note is loud, and the 2026-11-08 11:17 CST read hit a live writer lock. Mitigating
factors: `load_games` has already waited up to 30 s on the same file, and `ModelView.p_yes` refuses on its own copy. The
residual risk is an OUT prop priced off Pinnacle alone for one 2-minute tick. Fix (optional): reuse the last good set, with
its age in the note, or use `retries=10, wait_s=3.0` as `load_games` does.

**m4 (minor): the assistant caches the slate per session.** `toolbox.py:146-147` (`self._slates[d]`) freezes `out_on_report`
at the first call. A long chat session misses a later OUT. JARVIS builds a new `ModelView` every tick (`runner.py:125`), so
JARVIS is not affected. Fix: say so in the refusal docs, or drop the cache entry when it is older than 15 min.

**m5 (disclosure, not a defect): logged populations change.** The parlay `shadow_predictions` log (its `TrackRecord`
`min_settled` gate), JARVIS's paper book (CLV for the frozen `configs/jarvis.yaml` weight-update rule) and
`reports/parlay/top_parlays_*.json` will no longer contain OUT-player contracts. Checked against the rules
(`grep` over `docs/prereg/` and `FORWARD_PREREG_2026_27.md`):
- No frozen prereg scores those outputs.
- LINE_CAL_LIVE (frozen) scores `forward_scores`, which is untouched.
- STAR_FLOOR_PAPER (frozen) reads raw live-capture files.
- FAV_DIP reads Kalshi game tickers.
- PARLAY_JOINT_CAL scores `top_parlays` but is a **DRAFT**. Before it freezes, it must name this gate in its population.
- The JARVIS weight rule says it "is not a frozen test". Kalshi closes still come from `kalshi_prices` (`book.py:549`), so CLV
  for positions already held is not lost.

**So no frozen rule's scored population changes. This is not a major finding.** It needs a DECISIONS line (draft below).

### What holds
These checks pass:
- OUT only. `doubtful` stays priced (tests on both consumers).
- Refuse, don't reprice.
- Newest row decides, so OUT then upgraded is priced again. The case where a player drops off the report while OUT is real but
  rare: 1 pair since 10-20.
- The join is safe. All 5,226 `game_id IS NULL` rows since 10-20 belong to next-day games; 0 belong to a same-day game, so
  nothing on tonight's slate is silently dropped. No pair has duplicate or conflicting rows at the same `as_of`.
- Mixed-case `OUT` is handled (`lower(status)`).
- The diff does not touch the forward store, `forward_scores` or FORWARD_PREREG.
- The canonical dump hashes only `forward_predictions` and `forward_scores` (`nba/daily/canonical_dump.py:3-6`). The oracle
  will come back identical, and **that proves nothing about this change**. Say so when the result is reported.
- An unreadable store is reported loudly (JARVIS note, parlay note when the table is missing).
- End to end, the stale-row case is tested through `load_slate`, `drop_out_on_report`, `top_source.load_legs`, `gather` and
  `ModelView`, though on the wrong clock (M1).

### Checks
| Check | Command | n | Result | Pass |
|---|---|---|---|---|
| Planted future (production ET convention) | `PYTHONPATH=<wt> python scratchpad/attack_outgap.py` (exit 0) | 4 serve times | 3 of 3 pre-report serves refused | **FAIL** (M1) |
| Lag/future on real data, serve = tip-60 | `python -I scratchpad/rederive_gap.py` (exit 0) | 1,999 future rows, 116 games; 63 wrong refusals, 0 wrong prices | wrong clock admits future rows | **FAIL** (M1, back-dated runs only) |
| Gap re-derivation | same | 266 pairs / 103 games | matches claim (269 / 104) | pass |
| Missingness: NULL game_id | DuckDB read-only query (in review log) | 5,226 NULL of 26,412 rows; 0 same-day | NULL only for next-day games | pass |
| Missingness: NULL player_id | same | 0 of 144,691 | latent crash only | pass (m2) |
| Upgrade/drop-off | same | 1 pair (OUT, then absent from the next report) | negligible | pass |
| Knockout / shuffled labels / integer support | n/a: a consumer gate, no model, no score | n/a | n/a | n/a |
| ruff check / format | `.venv-1/bin/ruff check nba tests`; `ruff format --check nba tests` | | exit 0 / exit 0 | pass |
| mypy (config files: nba, research) | `.venv-1/bin/mypy` | | exit 1: 38 errors, all in 3 `research/scratch/` files that predate the branch; 0 in nba | pass (pre-existing) |
| pytest | `python -m pytest -q --no-cov tests/parlay tests/jarvis` | 448 passed, 2 skipped, exit 0 with `--no-cov` (exit 1 with coverage only from the 70% fail-under on a subset) | | pass |
| pytest | `python -m pytest -q --no-cov tests/test_layering.py tests/ops tests/export tests/explain` | 379 passed, exit 0 | | pass |

No CI applies here: these are code and data-integrity counts, not effect estimates.

---

## 2. agent/steward-dst-followups: SURVIVES

### Watchdog slot math under `Etc/GMT+4`
`scratchpad/attack_watchdog.py` (exit 0) runs `evaluate` with `tz=Etc/GMT+4` and hours 9..22:
- With all slots firing, the 22:21, 22:51 and 23:30 FIX checks, the next morning at 08:10 FIX, and 08:50 after the morning job
  ran all return **no stale**. `last_due` at 23:30 FIX = 02:50Z, which is right.
- Live heartbeat cross-check: pretip started 11:20:04 CST = 13:20 FIX, and the 11-07 log's first start is 07:20 CST = 09:20 FIX.
  Launchd really is on UTC-4. The new slots 22:20 and 22:50 FIX are 21:20 and 21:50 EST, which gives T-70 for a 22:30 ET tip.
  Today the last start is 20:50 EST.

### Findings
**D-M1 (major, operational; ordering, not code).** The watchdog runs from main's tree (`local.nba.watchdog` runs `python -m
nba.ops.watchdog`). If the branch merges before `sh ops/install_launchd.sh` runs, the attack shows `daily-pretip:stale` at
23:20 FIX and again at 03:00 FIX. That repeats every night until the reinstall: false alarms from 22:45 FIX (20:45 CST) to
the 09:20 slot. Separately, the installer calls `launchctl bootout` on pretip, morning, lineups and watchdog (`install_launchd.sh:18`,
`:41`), which kills a run in progress, including a T-30 `run-t30`.
- **Fix:** run the installer in the same sitting as the merge, outside any game window (before the first T-60 slot or after the last
  tip). Running it from the branch worktree before the merge also works: the plists point at main's `ROOT`, and the old watchdog
  ignores extra runs.
- Check after install: `plutil -p ~/Library/LaunchAgents/local.nba.daily-pretip.plist | grep -c Hour` should give 28 (today 26).
  Also check that the next start lands on the FIX clock (:20 FIX).
- Unknown: whether `bootstrap` re-reads Central. The DST review thought not, because launchd itself is not restarted. If it does,
  `data/ops/schedule_tz` must be removed. The first heartbeat after the install answers this.

**D-m1 (minor): installer and watchdog are tied by a comment only.** No test ties `install_launchd.sh` (`-le 22`) to
`JOBS[daily-pretip].hours`. `tests/ops/test_vm_scripts.py:99` pins only the cron string. Fix: add a test that parses `h -le N`
from `ops/install_launchd.sh` and asserts `range(9, N+1) == spec.hours`, and the same for `install_cron.sh`.

**D-m2 (minor): a docstring rewrites history.** `tests/ops/test_watchdog.py:242-243` now says the 2026-10-11 incident fired
"Hour 9..22". It was 9..21. Fix: "launchd fires the pretip hours on Eastern time".

**D-m3 (minor): the live frame is untested.** No test runs in `Etc/GMT+4`, the frame launchd actually uses. The DST review's
draft `tests/ops/test_watchdog_dst.py` (section 4) was not added. It is optional. As that review noted, it pins the frame,
not a bug.

### Do the five updated tests still test something real?
Yes.
- `test_last_due_slots` and `test_pretip_slots_are_20_and_50` still check "yesterday's last slot", now 22:50.
- `test_last_due_is_dst_correct_across_fall_back` still asserts the UTC-5 offset on the 10-31 slot and UTC-6 on 03-07.
- `test_launchd_on_eastern_slots_...` still asserts both old false alarms in Central (last due 22:50 CDT, heartbeat 21:50, now
  23:20: stale) and none in ET (last due 22:50 ET = 21:50 CDT: fresh). The heartbeat and clock moved together. The lineups
  heartbeat moved to 23:15 so the 5-min interval stays fresh. That was needed, not loosened.
- `test_cron_block_matches_launchd_schedule` checks the new cron string.
- New `test_attaches_use_lock_retry` spies both aliases (`["nba", "kal"]`).

### Collisions for the extra runs (22:20 / 22:50 FIX = 21:20 / 21:50 EST)
- **Lineups T-30/T-15:** `run-t30` waits, bounded, for `nba.duckdb`, and pretip predict holds it for about 30 s. A run takes about
  75 s (heartbeat 11:20:04 to 11:21:19). This is the same overlap every earlier :20/:50 slot already has with a 19:00-21:30 ET tip.
  Nothing new.
- **Live capture** writes raw parquet (`nba/ops/live_capture.py:3`), not DuckDB. No contention.
- **Kalshi snapshot** (`StartInterval` 900, writer on `kalshi.duckdb`): fix C is exactly the guard here. `parlay_shadow` already
  retried.
- The shared `data/ops/lock` is the only nba_daily lock, and morning (08:00 FIX) is 9 h away.
- No collision found.

### The 3 mypy errors in tests/parlay/test_analysis.py
They **exist on main**. The out-gap worktree's `test_analysis.py` is byte-identical to main e65739f (`git diff --stat` empty)
and gives the same 3 errors at :123, :275 and :276: one unused ignore and two `**dict[str, object]` arg-type errors. DST's file
reports them at :124, :290 and :291, shifted by its insertions. `pyproject.toml:74` sets `files = ["nba", "research"]`, so the
project gate never sees them.

| Check | Command | Result | Pass |
|---|---|---|---|
| ruff check / format | `.venv-1/bin/ruff check nba tests`; `ruff format --check nba tests` | exit 0 / exit 0 | pass |
| mypy (config) | `.venv-1/bin/mypy` | exit 1, 38 errors in 3 `research/scratch` files (pre-existing, identical on out-gap/main) | pass |
| mypy test file | `mypy tests/parlay/test_analysis.py` on dst and on main-equivalent | 3 errors on both | pre-existing |
| pytest | `python -m pytest -q --no-cov tests/ops tests/parlay tests/test_layering.py` | 426 passed, 2 skipped, exit 0 | pass |
| watchdog under Etc/GMT+4 | `PYTHONPATH=<wt> python scratchpad/attack_watchdog.py` | no false stale when installed; stale if merged without install | pass (D-M1) |
| merge with out-gap | `git merge-tree --write-tree 70f2aba a5f6bf3` | clean, tree 9cf6fcc8 | pass |

---

## Playbook check
Model class: **none**. Both branches are a consumer-side data gate (out-gap) and scheduler/ops wiring (DST). Neither fits or
scores a model.
1. **Information or capacity.** Out-gap adds information (a later report) and nothing else, which fits "information beats
   architecture". DST adds no information.
2. **Enough data for this class.** n/a. Nothing is fit.
3. **Test validity.** Out-gap's tests are not valid for known-at. They use the wrong clock, and an invalid test is not a pass
   (M1). DST's tests are valid but do not cover the live frame (D-m3).
4. **Lineage and inherited guards.** Out-gap inherits the 2026-11-02 Brown/George incident and the 10-28 no-crash rule. It
   honours the latter except for m2. DST inherits the 2026-10-11 and 2026-11-01 launchd-clock incidents.
5. **Scored against.** Nothing is scored. The forward store and FORWARD_PREREG are untouched. Logged populations shift (m5).

## Biggest risk
For out-gap: the gate is believed to be "known-at" and gets reused in a replay or research harness (for example
`replay_season parlay_pass`). It would then quietly feed up to 5 h of future injury reports into a shadow or paper log, and
the oracle cannot see it.

## Not verified
- I did not run the replay oracle (main session).
- I did not run the full test suite on either branch, only the subsets listed.
- Whether `launchctl bootstrap` re-reads Central (D-M1).
- The Kalshi DNP fair-price settlement mentioned in STAR_FLOOR_PAPER. The branch refuses rather than reprices, which is
  conservative.
- JARVIS end to end on a live tick.

## Recommended changes (main session acts; nothing applied here)
1. Out-gap: M1 fix and tests, m1, m2. Then re-review the diff, a short read.
2. DST: merge, then run `sh ops/install_launchd.sh` in the same sitting, outside a game window. Verify 28 Hour entries and the
   next heartbeat. Optionally add D-m1 and fix D-m2.
3. Order: DST, then out-gap.

## Drafted rows (not appended)
`docs/DECISIONS.md`:
| 2026-11-08 | JARVIS and nba.parlay refuse props of players whose newest injury-report row known at serve time (as_of compared in ET) is OUT; doubtful still priced; refused, not repriced (agent/market-out-gap). Why: 266 stale (game, player) pairs / 103 games since 10-20 priced from a pre-report p_play. Logged populations change (parlay shadow log + TrackRecord gate, JARVIS paper book/CLV, top_parlays JSON); no frozen rule scores them; PARLAY_JOINT_CAL (draft) must name the gate before freezing. Reverse if: Kalshi DNP fair-price settlement is read and repricing at p_play=0 is shown safe, or the forward store starts writing a row for newly-OUT players. Follows advice (adversary review outgap_dst_premerge_2026-11-08). |

---

## Re-review 49479d3 (diff 70f2aba..49479d3 only)

Worktree `/Users/devin/Downloads/nba-wt-adv-outgap` detached at 49479d3. 7 files, +50/-20. The oracle was not run.

**Verdict: APPROVED.** Out-gap moves from WOUNDED to **SURVIVES**. There are no new blockers or majors.

| Item | Fix | Check | Pass |
|---|---|---|---|
| M1 | `out_gate.py:40-44` `utc_to_et_naive` (ZoneInfo `America/New_York`, not a fixed -5) bound at `:78` | Re-ran `scratchpad/attack_outgap.py` (exit 0). Report 14:15 ET on 2026-11-02 (EST): serves at 09:30, 13:00 and 14:00 ET are now **not refused**; the serve at 14:30 ET is refused. DST edges: 10-31 18:15Z -> 14:15 (EDT), 11-02 19:15Z -> 14:15 (EST), 2027-03-14 06:59Z -> 01:59 and 07:01Z -> 03:01 (spring-forward gap skipped correctly). The only ambiguity is the 01:00-02:00 ET fall-back hour, when no report is published. | pass |
| M1 tests | Fixtures store ET (`tests/parlay/test_out_gap.py:25`, `tests/jarvis/test_out_gap.py:25`, `tests/parlay/test_top_source.py:63`). New negative test `test_report_stored_in_et_is_ignored_at_18_utc_and_used_at_1930_utc` (2026-10-20, EDT: 14:15 ET = 18:15Z) | Passes. The test now fails on the old code's convention. | pass |
| M1 pre-existing (`top_source._out_names:375`) | `as_of <= utc_to_et_naive(made_at[gid])`; a game with no `made_at` still admits every row, as with the old `datetime.max` | See "What `_out_names` feeds" below. | pass |
| m1 | `toolbox.py:394` "ET" | | pass |
| m2 | `AND player_id IS NOT NULL` (`out_gate.py:77`). JARVIS `load_out_players` catches `Exception` on open and on query (`sources.py:180,185`) and returns the loud "filter is OFF" note | Attack: the NULL-pid row is now ignored, with no `TypeError`. New `test_null_player_id_rows_do_not_break_the_gate` passes. | pass |
| m3 | `open_ro(retries=10, wait_s=3.0)` | See "Tick stall" below. | pass |
| m4, m5 | Not addressed. m4 is minor; m5 is the DECISIONS row the main session appends | | still open (minor / bookkeeping) |

**What `_out_names` feeds.**
- Its only consumer is `top_source.py:503-507`, the `teammates` list. That list goes into `drivers["teammates_out_names"]` (`:549`), which is display text only: `top_parlays.py:388` writes "X is out" in the leg's explanation.
- It changes no probability, filter or score.
- Behaviour change: teammates ruled OUT on reports published after the forecast row are no longer listed as drivers. Before, a report up to 4-5 h later could be listed. Now the list is "OUT as of the forecast", which is what the docstring says and the honest driver of a forecast that did not see the later report.
- `grep teammates_out` over `docs/prereg/`, `docs/contracts/`, `FORWARD_PREREG_2026_27.md`, `research/eval/parlay_joint_cal_*` and `nba/export` finds no reader. PARLAY_JOINT_CAL (DRAFT) scores `p_joint_*`, not drivers.
- So no frozen rule and no scored population reads it. The logged `top_parlays_*.json` text changes, and that is display only.

**Tick stall (m3).**
- Worst case is 10 tries with a 3 s sleep: about 30 s, plus the query time.
- `gather` runs it after `load_games`, which already waited (same 10 x 3 s) and raises on failure. So the out-gate wait only starts once the DB was free moments earlier, and it is long only if a writer (pretip predict, about 30 s, or run-t30) grabs the file in between.
- Total worst case per tick is about 60 s of DB waiting against the 120 s `StartInterval`. Launchd does not start an overlapping instance.
- The side effect is that quotes already gathered can be up to about 30 s staler. That is minor and acceptable.
- No collision with the Kalshi snapshot: this opens `nba.duckdb` only (the snapshot writes `kalshi.duckdb`). The retry holds no handle while it sleeps, and each successful read lasts milliseconds, so it cannot meaningfully block a writer (writers wait up to 300 s).

**New contradictions found:** none. `OutNotice.as_of` is now documented as ET (`out_gate.py:62-65`) and is shown as ET.

| Check | Command | Result |
|---|---|---|
| ruff check / format | `.venv-1/bin/ruff check nba tests`; `ruff format --check nba tests` | exit 0 / exit 0 |
| mypy | `.venv-1/bin/mypy nba/parlay nba/jarvis` | exit 0, no issues in 66 files |
| pytest | `python -m pytest -q --no-cov tests/parlay tests/jarvis tests/export tests/test_layering.py` | 543 passed, 2 skipped, exit 0 |
| planted future | `PYTHONPATH=<wt> python scratchpad/attack_outgap.py` | exit 0; 0 of 3 pre-report serves refused |

**Merge order is unchanged:** DST first (installer in the same sitting, D-M1), then out-gap. They merge cleanly: the 49479d3 changes do not touch `analysis.py`.
