# Adversary review: "the live loop refuses, not reports" (plan item A), 2026-10-21

Branch `agent/steward-refuse-not-report`, head `e335489`, one commit on main `18bc03f`. Live path
(nba/daily, ops/, export, parlay). Reviewer: adversary. Oracle not run here (it runs separately).

Verdict: BLOCKED

One blocker, and it has a one-line fix. Fix B1 and M1 (a test assertion) and this becomes APPROVED WITH EDITS: the
remaining majors and minors can follow as their own items. The four fixes do what they say for the incident
paths that happened on 2026-10-20. The blocker is that fix (3) also changes what the pre-registered scoring table
`forward_scores_elig` holds. Nobody amended the rule for that, and the change buys nothing, because a misplaced
player is not in the box and the frozen rule already counts him as `dnp`.

## Playbook check

Model class: none. This is a live-path ops and scoring-plumbing change, not a model. The five questions, as
they apply here:
- **Information or capacity:** neither. No forecast changes; only which rows get graded or shown.
- **Enough data for this class:** n/a. The data question is whether the 10-row floor can exclude a real
  final. It cannot in 15,474 games (see S1).
- **Test validity:** the claimed test for the elig winner guard does not test it (M1; a mutant survives).
- **Lineage and inherited guards:** it inherits the 4 h `SETTLE_AFTER_TIP_HOURS` guard (DECISIONS 2026-10-20)
  and the append-once score rule. Both are preserved.
- **What it is scored against:** `forward_scores_elig` is the frozen FORWARD_PREREG_2026_27 table (rules
  1-4). Any change to its contents is a change to that rule (B1).

## Findings (severity-ranked)

### Blocker

**B1. Withdrawals silently remove rows from the pre-registered scoring table.**
`nba/daily/settle.py:231` (`  AND NOT {withdrawn}` in `_SELECT_ELIGIBLE`).
FORWARD_PREREG_2026_27 section 1 rule 1 says DNP and not-in-box rows "are excluded identically from both
sides and counted". Rule 2 says one-sided rows "are never dropped silently". With this line, a withdrawn key never
gets a `forward_scores_elig` row, so the checkpoint's `dnp` counter undercounts and nothing reports the gap.
- If the withdrawn player is really off the game's roster, he is not in the box. The frozen rule would write
  `status='dnp'`, which is excluded from pairs. So the line changes counts and buys no accuracy. The steward's
  own test says so: `tests/daily/test_refuse_not_report.py` notes "before the fix: one 'dnp' row per stat".
- If the official roster is wrong (a player listed on two rosters around a trade, with his real team's
  fetch missing), he plays. The line then deletes a scorable pair for every model whose key existed at
  withdrawal time. The decision uses pre-tip information only, so this is not a leak, but it changes n.
- It can also be asymmetric. Withdrawals are written only by `run_daily` pretip runs (`pipeline.py`
  `_withdraw_misplaced`). A T-30 or T-15 key logged after the last pretip run before tip is never withdrawn. The
  checkpoint then sees that key as `only_arm` instead of `dnp`.
- **Fix (safe default):** delete line 231 of `nba/daily/settle.py`. Keep line 87, because `forward_scores` is
  not the frozen table. In `tests/daily/test_refuse_not_report.py:178-181`, replace the
  `SELECT count(*) ... assert elig == (0,)` block with
  `SELECT DISTINCT status FROM forward_scores_elig WHERE player_id = ? AND game_id = ?` and
  `assert elig == [("dnp",)]`. That asserts the frozen rule still counts him as not-in-box.
  Docs: `docs/DAILY_PIPELINE.md` item 3, "Both settle paths skip withdrawn keys", becomes "`settle` skips
  withdrawn keys; `forward_scores_elig` is unchanged (the frozen rule counts them as dnp)".
- **Alternative (Devin's call, not the default):** if withdrawals should apply to the frozen table, add a dated
  entry to the Amendments section of FORWARD_PREREG_2026_27 and a DECISIONS row. `nba/daily/checkpoint.py`
  `pair_rows` would also need to count withdrawn keys per arm, so they are counted and not silently dropped.

### Major

**M1. The `forward_scores_elig` winner box guard has no failing test.**
`tests/daily/test_refuse_not_report.py:60` asserts `n_elig[0] >= 1` only after the 10th played row lands. If the
guard were missing, the elig row would be written by the first `settle_pending` call (0 box rows) and that assertion
would still pass. Mutation check: I removed `AND (l.target <> ? OR {has_box})` and its `WIN_TARGET` parameter from
`_SELECT_ELIGIBLE` in a scratch worktree at e335489 and ran `test_refuse_not_report.py`, `test_review_fixes.py`,
`test_t15_arm.py` and `test_checkpoint.py`: **45 passed**, so the mutant survives. The four existing tests that
now call `add_final_box` add a box and never assert a refusal.
**Fix:** after line 52 (the 9-row call), add
`assert con.execute("SELECT count(*) FROM forward_scores_elig WHERE game_id=? AND target='win_prob_home'", [G1]).fetchone() == (0,)`,
and replace line 60 with `== n_models` (the number of win models eligible at 120 min) or at least `>= 1`, now
following the zero assertion above it.

**M2. Props are still graded on any box at all, and box-versus-score consistency is not checked (open paths 3 and 4).**
`nba/daily/settle.py:118` (`if box is None or box[0] == 0: continue`) and `_SELECT_ELIGIBLE`'s `has_box`
(`SELECT DISTINCT game_id FROM player_game_stats`) grade props as soon as one box row exists. Winner rows now need
10 played rows, but props do not. That leaves two paths open:
- **Path 3 (box pulled mid-game).** `ingest_step.missing_boxscore_game_ids` (`ingest_step.py:26`) selects only
  games with no rows, so a partial box is never re-pulled. Props would then be graded on partial stats, and
  winners on whatever `games` says.
- **Path 4 (new; the third incident path).** The finder can return a partial score after the box is final (OKC@SAS
  44-65, 16 min after the scoreboard said Final). The winner guard passes on a full box while `games` still holds
  the partial score. Within the scheduled jobs this needs a settle more than 4 h after tip while the finder lags.
  The 07:00 morning ingest refreshes `games` first, so the exposure is evening pretip settles of early tips
  (holiday slates) and manual runs.
- **Fix (closes both, for every target):** replace `_HAS_BOX_SQL` with a box-consistency fragment. The game has
  >= `MIN_PLAYED_ROWS` played rows AND per-team `sum(pts)` of `player_game_stats` equals `games.home_pts` /
  `games.away_pts` (join on `home_team` / `away_team`). Apply it to all targets: drop the
  `l.target <> ? OR` and the `WIN_TARGET` parameter in both selects. In settle_pending, gate the prop branch on
  the same check instead of `box[0] == 0`.
- **Data:** box sums equal the stored final score in **3,958 / 3,958** games of 2022-2024 (nba.duckdb),
  **11,512 / 11,516** of 2013-2021 (data/history; 4 historical source mismatches), and **3 / 3** of 2026.
  A partial box or a partial score then always fails the check: refused, never graded.
- **Why major, not blocker:** observed endpoint behaviour so far. Six in-progress or just-final V3 pulls
  returned empty (BOS@DET 15:20, 15:50, 16:20 and 16:50 CDT with tip at 14:00 CDT; PHI@NYK 20:50 CDT; OKC@SAS
  23:50 CDT). `fetch_cached(allow_empty=False)` marked each one failed and retried later. No partial box has been
  observed, though n = 6 is small.

**M3. A refusal is silent.** `settle_pending` (`nba/daily/settle.py:133`) returns only scored counts. A winner key
can wait forever and no counter reports it. That happens when the box never comes, when its minutes are all NULL
(one such game in 15,474: `0021400010`, 25 rows, 0 played), or, after M2, when the box disagrees with the score.
"Refuse, not report" still has to say what it refused.
**Fix:** add `win_refused_no_box`: the count of latest win keys with `home_pts > 0`, past the 4 h guard,
failing the box guard. Return it from `settle_pending`. In `ops/nba_daily.sh` morning, the settle step should
exit 4 (informational) when the count is > 0 for keys tipped less than 24 h ago, and 5 (alert) when any key
tipped more than 24 h ago.

### Minor

- **m1. Withdrawals are permanent per key; nothing reinstates a player.** `nba/daily/store.py:91` (`NOT EXISTS`
  on any earlier withdrawal) and `:102` (`official = min(teams)`, arbitrary when he is on several rosters).
  Official rosters are cached per date (`load_official_rosters`), so within one game's date the decision
  rarely changes. With B1 fixed, permanence affects display and `forward_scores` only. Acceptable. Document the
  `min(teams)` tie rule in the docstring.
- **m2. The schedule-cache directory is defined twice.** `nba/daily/__main__.py:31` (`Path("data/schedule_cache")`,
  relative to cwd, where the pretip writes) and `nba/daily/schedule.py:160` (absolute from `__file__`, where the
  readers look). They agree only while launchd runs from the repo root. **Fix:**
  `from nba.daily.schedule import DEFAULT_SCHEDULE_CACHE_DIR as SCHEDULE_CACHE_DIR` in `__main__.py`.
- **m3. The export drops withdrawn rows without a counter.** `nba/export/reader.py:174` `continue`s before the
  `post_tip` counter. Add `excluded_withdrawn` to `ForwardBundle`, next to `excluded_post_tip`.
- **m4. Branches with no test:** real DNP rows have NULL minutes (19,741 NULL against 15 zero in 2022-24), and the
  test inserts only a 0-minute DNP. Also untested: a player on two fetched rosters, one of them in the game (must
  not be withdrawn); `run_ingest` with a box failure (rc 0, `boxscores_failed` counted); `ingest` lock-busy
  (`RC_DB_BUSY`, shared `main` path); and a withdrawal for a player with keys in two games the same night.
- **m5. Path 8: the shadow settlers and `nba.parlay` evaluate/analyze do not read `forward_withdrawals`.**
  The shadows write `not_in_box` rows for a misplaced player, which are excluded from their pairs. Under
  `--roster-source official`, `nba/parlay/slate.py` places players by the official map, so a misplaced player
  is not placed in the wrong game. Low severity; close it when the shadows next change.

## S1. Can the 10-row floor exclude a real final? (frozen-rule inputs)

The minimum count of played rows (`minutes > 0`) per completed game, read-only:

| Source | Seasons | Games | Min played | Games < 10 played |
|---|---|---|---|---|
| nba.duckdb | 2022-2024 | 3,958 | 13 (`0022201226`, 2023-04-09) | 0 |
| data/history/nba_history.duckdb | 2013-2021 | 11,516 | 0 | 1 (`0021400010`: 25 rows, all minutes NULL/0, a source defect) |

No real final in 12 seasons has fewer than 13 played rows. NBA forfeits do not occur in this span. The
floor changes **when** a winner row is written (once the box lands), never **what** it scores, for any final
with a usable box. The one case it changes is a box with no minutes: the row then stays pending, never
wrongly scored. M3 makes that visible.

## Oracle reasoning (expected IDENTICAL 89889f6d)

The steward's argument holds:
- `rehearsal.restore_results` (`rehearsal.py:67-100`) restores `games` scores and every game-keyed table,
  `player_game_stats` included, in one call before that date's settle.
- `canonical_dump` reads only `forward_predictions` and `forward_scores` with fixed columns, `scored_at`
  excluded.
- The replay runs `roster_source="recent"` (`replay_season.py:297,355`), so `official is None` and
  `_withdraw_misplaced` returns early.

The hash could still move legitimately in two ways:
1. A replay game whose restored box has fewer than 10 played rows, or NULL minutes. Its winner rows would
   vanish from `forward_scores`. I could not check this because the replay season is not readable by me.
2. If B1's fix lands, nothing moves, because `forward_scores_elig` is not in the dump.

M2's fix would also change prop settling for a box inconsistent with the score. That cannot happen with
restored full boxes, so the expectation is IDENTICAL, but re-run the oracle after M2.

## Check table

| # | Check | Command | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff --stat main...agent/steward-refuse-not-report` | 18 files, A(1)-(4), their tests, DAILY_PIPELINE; no `data/` path; `tests/test_layering.py` unchanged (KNOWN_EDGES still empty) | pass |
| 2 | Frozen rule (elig) | read `settle.py:226-262` and FORWARD_PREREG section 1 | box floor: no legit final excluded (S1); withdrawal skip changes counts silently (B1) | **fail (B1)** |
| 3 | Floor bound | `q2.py`/`q3.py` (scratch) read-only on nba.duckdb and nba_history.duckdb, seasons <= 2024 | min played 13 over 15,474 games; 1 source-defect game with 0 | pass |
| 4 | Box/score consistency | `q4.py` (scratch) per-team `sum(pts)` vs `games` | 3,958/3,958 (2022-24); 11,512/11,516 (2013-21); 3/3 (2026) | supports M2 |
| 5 | Tests | `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/daily tests/export tests/parlay tests/ops tests/test_layering.py --no-cov -q -p no:cacheprovider -x` in wt_refuse at e335489 | `706 passed, 2 skipped, 64814 warnings in 349.65s (0:05:49)`, exit 0 | pass |
| 6 | Mutation: elig winner guard removed | scratch worktree at e335489, 4 test files | `45 passed` (mutant survives) | **fail (M1)** |
| 7 | ruff | `uv run --no-sync ruff check --no-cache <16 changed files>` | `All checks passed!`, exit 0 | pass |
| 8 | format | `uv run --no-sync ruff format --check --no-cache <16 files>` | `16 files already formatted`, exit 0 | pass |
| 9 | mypy | `uv run --no-sync mypy --no-incremental <7 changed nba files>` | `Success: no issues found in 7 source files`, exit 0 | pass |
| 10 | Layering | in run 5 (`tests/test_layering.py`) | passed; new imports `nba.export/parlay -> nba.daily.schedule/store` are live to live, no research import | pass |
| 11 | Ingest subcommand | read `__main__.py` `run_ingest`, `ingest_step.py`, `ops/nba_daily.sh` (`set -u` only, no `set -e`) | fetch failure -> rc 5, alert, settle still runs; box failure per game counted, never fatal; `--date` default today ET -> `season_int_for_date` (Aug-Dec year, else year-1) is right at 07:00 CDT; lock via `connect_with_retry` -> `RC_DB_BUSY` | pass |
| 12 | Endpoint evidence (path 3) | `grep ingest: data/ops/pretip_20261020.log`, `manual_refresh_20261020_2350.log` | 6/6 in-progress or just-final box pulls empty -> failed -> retried | open, likelihood low |

## Path table (bad state: a result graded on a non-final outcome, or a misplaced forecast scored or shown)

| # | Path | Status after this branch |
|---|---|---|
| 1 | Feed gives an in-progress score; settle runs within 4 h of tip | covered (4 h guard, prior merge) + winner box floor |
| 2 | Last pretip leaves a partial score; 07:00 settle runs before ingest (PHI@NYK 63-62) | covered (`step ingest` before settle; winner box floor) |
| 3 | Box pulled mid-game with >= 10 played rows, cached, never refreshed | **open**: props graded on the partial box; winner graded on `games`. Likelihood low (6/6 empty). M2 closes it |
| 4 | Finder returns a partial score after the box is final (OKC@SAS 44-65) and settle runs past 4 h | **open** for winners: the box floor passes. Bounded by the 07:00 ingest-first order. M2 closes it |
| 5 | Morning ingest fails (DNS down, as at 12:23 CDT 10-20) | covered for games without a box (refused); combined with 3 or 4, open as those are |
| 6 | Box never arrives, or all minutes NULL | refused, but silently: M3 |
| 7 | Misplaced mover forecast scored or shown | covered: `forward_scores`, export, top_source. B1: the elig skip should be removed, not kept |
| 8 | Misplaced mover in the shadows and in parlay evaluate/analyze | open, low (m5) |
| 9 | Game missing from `games`, so export/top_source drop it (OKC@SAS) | covered: schedule cache, then the win row's teams, then `game_teams_unknown` counted |
| 10 | Wrong withdrawal (stale or ambiguous official roster) made permanent | open, low with B1 fixed (m1) |

**Severity for the 07:00 settle on 2026-10-22:** path 3 is the only one that could grade props wrongly, and only
if a pretip pull tonight gets a partial box. The endpoint has not done that yet. Path 4 cannot reach the 07:00
settle, because ingest now refreshes `games` first. Merging B1+M1 tonight protects tomorrow's morning. Without
the merge, the old order (settle before ingest) is back in force for tonight's slate.

## Not verified

- The replay season's minimum played rows (holdout; the oracle run answers it).
- Whether stats.nba.com V3 ever serves a partial box mid-game (n = 6 observations, all empty).
- The full test suite. I ran only the five directories above.
- Behaviour under real launchd cwd for m2 (read, not run).
- The DECISIONS row for this change. Item A's "Devin's go" is the pre-authorization. The main session should
  add a row recording the frozen-table decision from B1.

## Drafted DECISIONS row (not appended)

| 2026-10-21 | Plan item A merged after the adversary's B1 fix: `forward_scores_elig` keeps the frozen rule (withdrawn movers stay `dnp`, counted); `forward_scores`, export and top_source skip withdrawn keys; winner rows need >= 10 played box rows (no real final in 15,474 games 2013-2024 has < 13; one source-defect box with 0). | Withdrawals use pre-tip information only, and a misplaced player is not in the box, so the frozen table gains nothing by dropping him and rules 1-2 forbid silent drops. | Reverse by a dated prereg amendment if withdrawn keys should leave the frozen table, with the checkpoint counting them per arm. |

## Re-check 79b98b8 (2026-10-21)

Verdict: APPROVED

I checked `git diff e335489..79b98b8`: 6 files, +22/-11. Every requested fix is in, and the diff changes nothing beyond them.
- **B1 fixed.** `AND NOT {withdrawn}` and its `.format` argument are gone from `_SELECT_ELIGIBLE`, so withdrawals no longer touch `forward_scores_elig`. `settle_pending` still skips withdrawn keys (`settle.py:93`). The mover test now asserts `elig == [("dnp",)]`. DAILY_PIPELINE item 3 matches the code.
- **M1 fixed.** The test asserts 0 elig win rows at 9 played rows, and `>= 1` at 10. I re-ran the mutation from the first review (elig `has_box` guard and its `WIN_TARGET` parameter removed, scratch worktree at 79b98b8): `tests/daily/test_refuse_not_report.py` now gives **1 failed, 7 passed**, with `assert (1,) == (0,)` on the new line. The test catches the missing guard.
- **m2 fixed.** `__main__.SCHEDULE_CACHE_DIR = DEFAULT_SCHEDULE_CACHE_DIR`, one definition.
- **m3 fixed.** `ForwardBundle.excluded_withdrawn` counts both forward rows and AVG rows, and the export reports it under `excluded.withdrawn`.

Checks, run read-only in wt_refuse at 79b98b8:
- **pytest:** `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/daily tests/export tests/parlay tests/ops tests/test_layering.py --no-cov -q -p no:cacheprovider` gave `706 passed, 2 skipped, 64814 warnings in 294.65s (0:04:54)`, exit 0.
- **Lint and types on the 5 changed files:** ruff exit 0, format exit 0, mypy exit 0 (4 source files).

**The flake** (`test_combo_shadow_reader.py::test_the_full_reader_honours_a_counting_start_when_its_meta_table_exists`, `assert 72 < 72`) does not depend on the seed. `nba/eval/full_shadow_live._LIVE_SQL` has no `ORDER BY`, so DuckDB's parallel join can return rows in any order. When `base["day"][len // 2]` happens to be the earliest date, the counting start filters nothing and `later` keeps all 72 rows. Fix: pick the median date with `sorted(set(base["day"]))[n // 2]`, or add `ORDER BY` to `_LIVE_SQL`. This is a test-side defect and does not affect this merge.

Recorded as follow-ups for Devin, not blocking: M2 (box-versus-score consistency for every target; closes paths 3 and 4), M3 (refusal counter and alert), m1, m4, m5.
