# Pre-merge review: agent/steward-stints-nightly (069f20f) — 2026-10-24

Reviewer: adversary. Diff: `git diff d73708d 069f20f` (merge-base with main = d73708d). 8 files, +762/-3:
`nba/ops/stints_nightly.py` (new), `ops/nba_daily.sh` (`advisory_step` + one call), `nba/ops/app_export.py`
(`ADVISORY_STEPS`), tests, `docs/DAILY_PIPELINE.md`, `tests/test_layering.py`.
Scratch worktree, detached at 069f20f; `data/` moved aside and symlinked to the main checkout's `data/`
(`ls -la` showed `data -> /Users/devin/Downloads/nba-prediction/data`). Nothing was computed on outcomes.

Verdict: BLOCKED

(BLOCKED here means CHANGES_REQUIRED: one blocker that needs a decision from Devin, not a code defect, plus two
major fixes that each take one line.)

## Top finding (blocker)

**Writing 2026 `possessions` changes the live AVG forecasts, and AVG is what the app export uses for props.**
The branch writes `possessions` rows for season 2026, using `parse_possessions` + `load_possessions`, for every game
whose PBP parses, even when the tracker later skips the game. Today there are **0** possessions rows and **0** stints rows for 2026 (read-only
count, below). `nba/daily/avg_shadow/sequences.py:191-202` reads `possessions` (shooter attempts, FT trips, shot zones,
team possession counts). Those feed the AVG_SHADOW_LIVE_2 token features `usg_poss` (line 496), `zone_rim/mid/ab3`
(lines 497-503), `team_pace`, `opp_pace` and `opp_drtg` (lines 511-513, 549-550; `drtg_g = pts_against / opp_poss`, line 251).
Since 2026-10-20 every played 2026 token has had `usg_poss = 0` and zone shares of 0
(`sequences.py:308-317`, NaN filled to 0). Team pace and drtg have also been sliding toward the fixed priors (99 and 113) as
the rolling window fills with 2026 games that have no possessions. The bundle was trained (T478) and parity-proved (P2, on 2024) with
possessions present, so live AVG has been running with a **train/serve skew since opening night**. This branch would
fix that skew, and once merged the next inference rewrites the history tokens for every 2026 game.
`forward_avg_shadow` has 5,916 rows, the bundle is installed (heartbeat `avg_shadow.json`, model_id b8d2b27b), and
DECISIONS line 1198 makes the app export and API prefer AVG for props pts/reb/ast/fg3m. So this is a
**behavioural change to an outward-facing forecast and to a frozen forward arm in the middle of its count**. The production
primary (`nba.daily` props, `season_avg`) is **not** affected: `nba/props/forward.py:187-190` copies `possessions`
into its scratch DB, but no builder it calls reads that table (grep of `nba/` for `FROM|JOIN possessions`, `off_players`,
`stints`). The replay oracle cannot catch this, because the AVG tables are outside the canonical dump (AVG_SHADOW_LIVE_2 item 5).

Exact fix (this is a decision, not code): before merge, Devin decides and DECISIONS records one of the following.
(a) Merge as is: the input repair is a data fix, and the AVG readout splits rows at the merge date and reports both halves
(scored rows before the merge ran with zeroed possession features). (b) Merge, but gate the `possessions`
writes for 2026 behind a switch until the AVG steward signs off. The steward also logs the skew itself as an
incident (`docs/AGENT_INCIDENTS.md` / BEST_PRACTICES row: "a live model's input table had no rows for the live season;
P2 parity ran on 2024 only"). My recommendation is (a). The skew is a bug in a model the app already prefers, and the
repair moves the live object toward the one that was tested. Under either option the boundary must be written down
before the next AVG look.

## Findings, severity-ranked

| # | Sev | Where | Finding | Exact fix |
|---|---|---|---|---|
| 1 | blocker | `nba/ops/stints_nightly.py:251-262` + `nba/daily/avg_shadow/sequences.py:191-202` | 2026 possessions change the AVG live inputs (above) | Devin's decision + DECISIONS row + AVG readout boundary, see above |
| 2 | major | `ops/nba_daily.sh:266-270` | `stints_nightly` runs even when the post-game fetches are skipped because `run_postgame_backfill.sh` or `nba.ingest.queue` is running (lines 255-258). That breaks the "one stats.nba.com puller at a time" rule, and the backfill may also be writing `nba.duckdb` (rc 75 after the 300 s wait, a needless alert) | Move the `advisory_step stints_nightly ...` line inside the `else` branch, after `postgame_load`, or wrap it in the same `pgrep` guard and `log` a skip. Add a test: with a fake `pgrep` that matches, no `nba.ops.stints_nightly` call |
| 3 | major | `ops/nba_daily.sh:151-152` vs the stale-lock rule at `:158` | The alarm budgets now add up past the 3 h stale-lock age: early_forecast 1200 + 4 fetches x 1800 + postgame_load 1200 + stints 1800 = 11,400 s (3 h 10 min), before the unalarmed ingest/settle/report/facts (facts is ON in the plist). On a 10-21-style hang day, a pretip run clears the "stale" morning lock while stints still holds the `nba.duckdb` write connection. Before this branch the sum was 9,600 s | Skip the advisory step when the morning is already late, e.g. record `START_EPOCH=$(date +%s)` at start and run stints only if `$(( $(date +%s) - START_EPOCH )) -lt ${NBA_STINTS_LATEST_S:-5400}`, else `log "stints_nightly skipped: morning past 90 min"` (it heals within 7 days). Add a test |
| 4 | minor | `stints_nightly.py:117-132` | "Final" is only `game_date < today` + a box row exists. If the morning `ingest` failed (rc 5), a game can still carry the half-time box a pretip run stored (the PHI@NYK 63-62 case). The tracker's roster is built from that partial box (`rebuild_lineups.py:160-163`), and because a game with stints is never rebuilt (idempotence), a wrong build is permanent | Also require each team's box minutes to sum to >= 240 (`HAVING sum(minutes) >= 239.5` per team), or require the game to be graded by settle. Add a test with a partial box |
| 5 | minor | `stints_nightly.py:206-283` | The write connection is opened at the start and held through the network fetch phase (up to 1,500 s) and through `compute_all`. `compute_all` loads all of `player_game_stats` and `possessions` and scans all 5,279 PBP parquets twice for the name and alias books (`rebuild_lineups.py:133-143`). Wall time was not measured. The T-30 lineups tick waits only 600 s for the DB and does not take the daily lock | Print the phase wall times in the summary JSON (fetch, compute, write), and ask the maintainer for the wall time of the 28-game copy run. If compute is > 120 s, restrict the books to the last 2 seasons in a follow-up (that is a tracker change, so it needs its own review) |
| 6 | minor | `stints_nightly.py:270-272` | `except ValueError` labels any ValueError raised inside `compute_all` (a real bug) as "tracker skipped" | Check `if not any(...)` before the call, or re-raise unless the message is polars' empty-concat message |
| 7 | minor | `stints_nightly.py:292-296` | rc 6 masks rc 5: a night under 95% and a failed game alert only as "under 95%" | Put the failed game ids in the rc-6 `ALERT` line (`main`, line 357) |
| 8 | minor | `stints_nightly.py:292` + reconciliation | The 95% per-night alert fires on noise for small nights. 10-20: 64/68 = 94.1%, Wilson 95% CI [0.858, 0.977] (not game-clustered; the night is 2 games, so the true uncertainty is wider). Pooled over the 4 nights: 638/642 = 99.4%. A 45-player-game night alerts on 3 misses | Do not change the 95% target (Devin's number). Add `n >= 150 player-games` to the per-night alert, and below that alert on the rolling 7-day pool. Keep the per-night row in the JSONL regardless |
| 9 | minor | — | A game the tracker cannot build (no starters) stays eligible for 7 nights and alerts rc 5 each night | Acceptable. Optionally log the repeat count in the summary |

## What was checked and passed

| Question | Evidence | Result |
|---|---|---|
| `advisory_step` never changes the heartbeat rc | `ops/nba_daily.sh:117-132`: no `HB_RC=` assignment, `return 0`; `hb_end` uses `HB_RC` only. The globals `name/rc/failed` are reset by the next `step`. Tests `test_stints_failure_only_alerts[1,5,6,75,142]` assert `"rc": 0` in the heartbeat and that `data_snapshot/diff/check` ran | pass |
| It still alerts | `alert` on every rc except 0 and 4; separate texts for 142 and 6; tests assert that ALERTS.md names the step | pass |
| `ADVISORY_STEPS` lets only `stints_nightly` through | `app_export.py:52,99-103`: exact tuple membership. `test_a_failed_advisory_stints_step_does_not_block_the_export` asserts that `report` failed + stints failed returns `"morning data step failed: report"`. The steps file has no other reader (grep of `nba/`) | pass |
| Alarm bound | `alarm_run "$STINTS_TIMEOUT_S"` (1800 s); `test_a_hung_stints_step_times_out_and_the_morning_continues` (rc 142, heartbeat 0, check ran) | pass (see finding 3 for the total budget) |
| Idempotence | `stints_nightly.py:231-233` skips games that have stints; `write_chunk` is delete+insert in one transaction, scoped to chunk games (`rebuild_lineups.py:198-224`); `test_second_run_is_idempotent` | pass |
| Scope filter | `season = 2026` hard-coded (`:68,124-131`), `game_date <= today-1` ET (`:121`, `today_et` uses America/New_York), both scores NOT NULL, box EXISTS, `since = max(2026-10-20, today-7)` | pass (finding 4 on what "final" means) |
| 2025 rows / replay path | Writes are restricted to the `only` set built from 2026 ids (`compute_all(only=...)`, `write_chunk` scoped by `_chunk_games`). `compute_all` **reads** every season's box, possessions and PBP names to build the name books (read-only, the same as the 5,269-game rebuild; not an evaluation). `stints` and `possessions` are not in `nba/daily/canonical_dump.py` | pass, no 2025 write. Oracle result is the main session's |
| Production primary forecasts | `nba/props/forward.py` copies `possessions` into scratch, but no reader of it exists in its builders; nothing in `nba/` reads `stints` except `nba/eval/f12_closing_risk.py` (research) | pass |
| AVG shadow / app export | See finding 1 | **fail** (decision needed) |
| Negative tests present | rc 1/5/6/75/142 alert, rc 4 silent, hung step, report still blocks export, idempotence, dry-run writes nothing, budget stop, off day | pass. Missing: tests for findings 2, 3 and 4 |

## Commands (scratch worktree, all unpiped; exit codes shown)

```
git merge-base main 069f20f                       -> d73708d (rc 0)
git worktree add --detach <scratch>/wt 069f20f    -> rc 0; data/ -> symlink, ls -la verified
uv sync --frozen --all-extras                     -> rc 0 (scratch .venv)
.venv/bin/ruff check <5 changed py files>         -> All checks passed, rc 0
.venv/bin/ruff format --check <same>              -> 5 files already formatted, rc 0
.venv/bin/mypy nba/ops/stints_nightly.py nba/ops/app_export.py -> Success, rc 0
.venv/bin/python -m pytest -q --no-cov tests/ops/test_stints_nightly.py tests/ops/test_daily_script_flags.py \
    tests/ops/test_app_export.py tests/test_layering.py -> 77 passed, rc 0
  (with coverage on: 77 passed, rc 1 = the global 70% fail-under on a 4-file subset, not a test failure)
read-only DuckDB count: possessions/stints rows by season for season >= 2025 -> 2026: 0 and 0; forward_avg_shadow: 5,916
```

Disclosure: the read-only count above used `season >= 2025` instead of `= 2026`, so it returned 2025 **row counts**
(possessions 263,271; stints 80,586). It read no outcomes, scored no model and built no features. It was still a
read touching a 2025 table that I did not log first (I cannot write the log). Draft row for the main session:
`2026-10-24 | adversary (stints_nightly review) | nba.duckdb read_only | COUNT(*) of possessions and stints grouped by season, filter season >= 2025 (should have been = 2026) | row counts only; no outcome, no model, no feature | not an evaluation touch; no model's first-touch status changes`.

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none new. This is an ops driver over the existing lineup tracker (deterministic parser). The model it touches
is AVG (sequence net, AVG_SHADOW_LIVE_2).
1. Information or capacity: information. It adds data the AVG was trained with and has been missing live (finding 1).
2. Enough data for this class: n/a (no fit).
3. Test validity: the reconciliation is a data-quality gate, not a model test. Per-night n is small (finding 8).
   AVG's P2 parity proof ran on 2024, which had possessions, so it could not see the 2026 gap. That is an invalid test of live
   parity, not a pass.
4. Lineage and inherited guards: tracker v2 (ADR 0002), with the same name and alias books as the bulk rebuild, so the
   rebuild's reconciliation review (`docs/reviews/stint_reconciliation_2026-10-09.md`) carries over. The AVG frozen rule's "no change after a live
   result" clause is what makes finding 1 a decision rather than a fix.
5. Scored against: nothing is scored by this branch. AVG is scored against production A0 and FULL; the boundary matters.

## Family, power

No inferential claim is made, so there is no family and no BH. The reconciliation alert is an operating threshold; its power note
is finding 8 (a 2-game night cannot tell 94% from 98%).

## Single most likely failure mode if APPROVED as is

The AVG live forecasts (the app's props source) shift on the first morning after merge, and nobody can tell the shift
apart from a model effect in the AVG readout, because the scored rows before and after the merge ran on different
inputs and no boundary was recorded.

## Not verified

- Wall time of `compute_all` on the full DB (I did not run it: it holds a write connection and reads every season's PBP).
- The replay oracle (the main session is running it).
- Whether `datamanifest diff` flags the one-off jump in `stints`/`possessions` rows after the backfill (likely a single
  informational diff line; not checked).
- The direction and size of the AVG forecast change. That is the AVG steward's job, on a copy, after Devin's decision.

## Drafted rows (not appended)

DECISIONS (draft): "2026-10-24 — Nightly stints/possessions for 2026 merged [or held]. What: `stints_nightly` writes
2026 `possessions`, which AVG_SHADOW_LIVE_2 reads (usage, zones, pace, drtg); since 2026-10-20 those inputs were zero or prior
for 2026 games. Why: it repairs a train/serve skew. Effect: AVG live forecasts change from <merge date>; the AVG readout
reports scored rows before and after the boundary separately. Reverse if: the AVG steward shows the repair breaks a
frozen-rule proof."

Holdout log (draft): the disclosure row above.

## Re-review (diff only, 069f20f..de63fbe): APPROVED

Verdict: APPROVED. Major 1-2 and Minor 1-5 fixed as specified. Major 1: the step runs inside the same pgrep else-branch as the post-game fetches (test). Major 2: START_EPOCH plus NBA_STINTS_LATEST_S (5400 s) bounds the step to finish by 7,200 s or skip (test). Minor 1: the 239.5 team-minutes floor checked on real 2026 data, 56/56 team-games pass (239.98 to 240.02). Minor 2: timings_s in the summary. Minor 3: only the empty-concat ValueError is swallowed (test). Minor 4: rc-6 alert lists failed ids. Minor 5: per-night alert only at >= 150 player-games, else pooled (test). Hold switch default off matches option (a), with tests. Checks at de63fbe: ruff 0, format 0, mypy 0, pytest 87 passed.

New minor (no block): the hold switch is one-way. Games built while the hold is on get stints but no possessions and are then skipped forever (idempotence keys on stints). Fix adopted: a DAILY_PIPELINE note; repair by deleting their stints rows or running the parser by hand.

Not verified: compute_all wall time on the full nba.duckdb (read it off the first nightly log); the oracle on de63fbe (main session).
