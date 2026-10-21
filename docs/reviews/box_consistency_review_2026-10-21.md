# Review: box agrees with score (M2) and visible refusals (M3), branch agent/steward-box-consistency

Date: 2026-10-21. Reviewer role: adversary. Branch head 5521209 (one commit on main 60d1e12).
Source review: `docs/reviews/refuse_not_report_review_2026-10-21.md` (M2, M3).
Kind of work: follows advice (it implements M2 and M3 from that review).

Verdict: APPROVED WITH EDITS

Top finding: the gate is correct, and it never grades a partial box. But it has no repair path. The
pretip `nba.daily run` ingests box scores at :20/:50 until 21:50, while early games are still in progress
(`missing_boxscore_game_ids`, nba/daily/ingest_step.py:19, selects any game with `home_pts > 0` and no box
rows, and `fetch_cached` never re-pulls). So an in-progress box can be cached for good. Before this branch
that box was graded wrong once and the wrong grade stood. After it, the game stays pending forever and
`settle` exits 5 every morning, with no command an operator can run to clear it. That trade is the right
one (refuse, not report). It is safe to merge only because the keys are kept and get graded correctly once
a re-pull exists. That re-pull is the next change the ingest steward has to make.

## Findings, severity-ranked

### Major

**M1. A refused game has no repair path, and its rc 5 recurs daily (operability; also bears on the frozen
population, see "Frozen-rule judgement").**
- Where: `docs/DAILY_PIPELINE.md:798-799` ("a box that stays wrong is only surfaced (rc 5), not repaired"),
  `nba/daily/__main__.py:397`, `nba/daily/ingest_step.py:19-30` (not changed here, correctly out of scope).
- Problem: the pretip job (`ops/nba_daily.sh:153`, `nba.daily run` with no `--skip-ingest`; launchd :20/:50,
  09:20-21:50) can pull a 7 pm game's box while the game is live. The cached raw is never re-fetched, and
  the game is never selected again because it has rows. The new gate then refuses the game (box_mismatch)
  every morning: rc 4 on day 1, rc 5 from day 2 on, forever. Opening night did not show it (all 3 2026 boxes
  agree today), but nothing in the code prevents it. When a source defect is real (4 of 15,474 games
  2013-24), the alert never clears, and there is no sanctioned way to acknowledge it.
  `forward_withdrawals` would silence it, but that table is for misplaced players, and using it here would
  be a misuse.
- Fix (doc, this branch): replace the last paragraph of `docs/DAILY_PIPELINE.md` (lines 798-799) with a
  runbook:
  "On `refused <gid> box_mismatch` at rc 5: (1) compare `games` home_pts/away_pts with the per-team box sums
  for <gid>. (2) If the box is short, delete the cached raw boxscore for <gid> and its `ingest_log` 'done'
  row, then run `python -m nba.daily ingest` (the delete-then-insert in `nba/ingest/boxscores.py:207`
  replaces the rows). The next settle grades the keys with the same eligible predictions. (3) If the
  official box itself disagrees with the official score after a fresh pull, record a DECISIONS row naming
  the game. The keys stay unscored, and the checkpoint lists the game (follow-up F1)."
- Follow-up (ingest steward, its own branch, not this one): F1 is in "Recommended changes" below.

**M2. The shadow settlers grade on boxes the primary refuses. Pre-existing, but the branch's text implies
the in-progress-box path is closed.**
- Where: `nba/daily/pplay_shadow/settle.py:20-34`, `full_shadow/settle.py:~40-60`,
  `avg_shadow/settle.py:~20-36`, `combo_shadow/settle.py:~30-45`. Each requires only `n_box > 0` and
  `home_pts > 0`. They have no 4 h guard (no `tipoff + ... INTERVAL` clause in any of the four) and no
  agrees gate. `nba/daily/pipeline.py:391-582` runs them inside the pretip `run`, so at 21:50 they can score
  a 19:00 game on a live box. One example: pplay labels a player y = 0 if he has not entered yet. The rows
  are written once and stand.
- Problem for this branch: `docs/DAILY_PIPELINE.md:778-787` says the in-progress box "is closed" for "any
  target", but the "Not closed" line (798) names only `forward_withdrawals` for the shadows.
- Fix (doc, this branch): at `docs/DAILY_PIPELINE.md:798`, add: "The shadow settlers (pplay, full, avg,
  combo) have neither the 4 h guard nor the box-agrees gate. They still grade a game with any box rows,
  including a box pulled in progress during the pretip `run`." Code fix (follow-up F2): import
  `SETTLE_AFTER_TIP_HOURS` and `_BOX_AGREES_SQL` into each shadow `_SQL` (the shadow modules are their
  owners' to change). That is a stricter WHEN on pre-registered shadow logs, the same judgement as below.

### Minor

**m1. `refused_games` drops withdrawn keys from the elig half too, which disagrees with B1.**
`nba/daily/settle.py:167` applies `AND NOT {withdrawn}` to the whole key. Since 79b98b8 (B1), withdrawn keys
ARE still written to `forward_scores_elig`. A game whose only unscored keys are withdrawn elig keys is
therefore refused silently. Fix: move the withdrawn test inside the first branch of the OR:
`AND ((NOT EXISTS (... forward_scores ...) AND NOT {withdrawn}) OR (p.model_name IN ({models}) AND NOT EXISTS (... forward_scores_elig ...)))`.

**m2. `refused_games` runs three times per `nba.daily settle`, and its `bx` CTE scans the whole box table each
time.** `settle.py:402` (in `settle_eligible_pending`, whose return value `settle_pending` discards),
`settle.py:150`, and `__main__.py:380`. `settle_pending` also runs on every pretip `run`, which makes about
75 full scans a day. The cost is small in DuckDB, but there are three copies of one answer. Fix: delete the
call at `settle.py:402`. Have `settle_pending` return the list (or let the CLI reuse it) instead of calling
`refused_games` again at `__main__.py:380`. Restrict `bx` with
`WHERE b.game_id IN (SELECT game_id FROM keys)` (settle.py:173).

**m3. Test assertions in test_daily got slightly looser.** `tests/daily/test_daily.py:295-337`: `_counts(...)`
projects the dict to the three scored counts, so the exact-dict asserts no longer pin the new keys. In the
shown states nothing changes (the refusal keys are 0 at TIPOFF+1h and on the second idempotent call). Fix:
after each projected assert, add
`assert {k: v for k, v in out.items() if k.startswith("refused_")} == dict.fromkeys(("refused_no_box", "refused_thin_box", "refused_box_mismatch", "refused_stale"), 0)`.

**m4. Branches with no test.** `tests/daily/test_box_consistency.py` does not cover:
(a) a box with rows for one team only (the other team's sum coalesces to 0 and must be refused);
(b) a played row whose `team_id` is neither home nor away (excluded from both sums, so refused). Only
    `test_store_dedup` exercises this, implicitly, through its `team_id = 1` rows plus the align filler;
(c) one game that is both thin and mismatched (precedence within a game; the existing test uses two
    different games);
(d) a refused game with prop keys only (`has_win` false, mismatched, so the reason must be box_mismatch,
    never thin_box);
(e) the 23 h 59 m side of the stale boundary.
The exactly-24 h side IS tested: `test_settle_cli_exit_codes_0_4_5` calls `--now TIPOFF+24h`, G1's stored
tipoff is TIPOFF, and the result is RC_DEGRADED. That matches `refusal_counts` `tip <= cut` (settle.py:217,
inclusive). Fix: add five short cases (a)-(e) to `test_box_consistency.py`.

**m5. The morning row of the schedule table does not mention settle's exit codes.** `docs/DAILY_PIPELINE.md:216`
lists `settle` without its 4/5. The pretip row (215) spells out its codes. Fix: after `settle`, add "(exit 4 =
a game past the 4 h guard refused, tipped < 24 h ago, informational; exit 5 = a refusal 24 h or older,
alerts)".

**m6. The fixture filler row is a minutes = 0 row carrying points.** `tests/daily/conftest.py` `align_box_to_score`
inserts player 990000/990001 with `minutes = 0, pts = remainder`. The primary and elig paths read stats only
`WHEN minutes > 0`, so this is harmless today. A future test that counts DNP rows, or a shadow test that reads
`pts` without the minutes filter, would see a DNP who scored. No change required. Note it in the docstring
("filler: not a real player; its pts exist only to make the team sum agree").

## Check table

All runs are read-only. Tests ran in the steward's worktree (wt_settle2, head 5521209) with
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`. DuckDB was opened with `read_only=True` only, and no query
read season 2025.

| # | Check | Command | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff main...agent/steward-box-consistency --stat`; `git diff --name-only main...HEAD \| grep -E "^data/\|ingest_step\|pipeline.py"` | 14 files, all named in the brief (settle.py, __main__.py settle branch, nba_daily.sh step(), DAILY_PIPELINE, 9 test files). grep exit 1 (no data/, ingest_step, pipeline). `git merge-tree --write-tree agent/steward-scheduled-games agent/steward-box-consistency` exit 0: no conflict. The scheduled-games branch touches `__main__.py` hunks at lines 15/235/248/372, this branch at 25/375-397, and the two do not overlap. | PASS |
| 2a | Column types | `DESCRIBE games / player_game_stats` (scratch box_schema.py) | `games.home_team/away_team INTEGER`, `player_game_stats.team_id INTEGER`, `pts INTEGER`, `game_id VARCHAR` on both sides | PASS |
| 2b | NULL / foreign team_id, NULL pts on played rows, duplicate (game, player) | same script, seasons 2022, 2023, 2024, 2026 | rows 34,024 / 34,815 / 35,087 / 103. NULL team_id 0, foreign team_id 0, played-with-NULL-pts 0, games with != 2 teams 0, duplicate (game, player) 0 | PASS |
| 2c | Agreement census (re-derived) | scratch census.py, seasons 2022-24 + 2026 | 1,320/1,320, 1,318/1,318, 1,320/1,320, 3/3 agree (3,961 of 3,961). 2013-21 reused from my earlier census: 11,512 of 11,516. Overall disagreement 4/15,474 = 0.026%, Wilson 95% CI [0.010%, 0.066%], i.e. 0.12-0.82 games per 1,230-game season | PASS |
| 2d | SQL semantics (read) | settle.py:41-47, 160-181, 198-217 | NULL pts gives 0 (inner coalesce). Empty box: count = 0, so false (outer coalesce makes sum = 0, never NULL = NULL). One-team box: other side 0 != score, refused. Foreign or NULL team_id: excluded from both sums, refused (safe direction). Overtime: in the sums. Forfeit: no box, so no_box forever and rc 5 (visible). Traded player: the box carries the team he played for in that game. The same `_BOX_AGREES_SQL` string is used at lines 100 and 316. Precedence no_box > thin_box (win keys only) > box_mismatch. Stale is `tip <= now - 24h`, inclusive | PASS |
| 3 | Frozen rule | `git diff main...HEAD --stat -- docs/FORWARD_PREREG_2026_27.md` (empty); read rules 1-4 | WHEN-only for every game whose box ever agrees. For a permanent defect the game leaves the population on both sides and nothing counts it. See judgement | PASS with M1 |
| 4 | M3 wiring | read `__main__.py:376-397`, `nba_daily.sh:96`; `tests/ops/test_daily_script_flags.py::test_settle_rc4_is_informational_and_rc5_alerts` (runs the real morning script with stub uv) | rc 0 when no refusals (a day with no games has no keys past the guard, so rc 0). rc 4 when all refusals are < 24 h. rc 5 otherwise. Shadows run before the return (the loop at 383-392 precedes it). One `refused <gid> <reason> (tip ... UTC; stays pending)` line per game. step() treats settle rc 4 as informational and rc 5 alerts (the script test asserts ALERTS.md is absent for 4 and present for 5) | PASS |
| 4b | Live dry read (no write) | scratch live_refused.py: `_REFUSED_SQL` on nba.duckdb read-only, restricted to `g.season = 2026`, withdrawn fragment = FALSE (the table does not exist yet on the live DB), now = 2026-10-21 07:34 UTC | one game past the guard with unscored keys (0022600003, 34 rows, 27 played, agrees = True), so 0 refusals and tomorrow's settle exits 0 | PASS |
| 5 | Fixture changes | `git diff main...agent/steward-box-consistency -- tests/` | `add_final_box` still writes n = 10 rows at minutes = 20 (>= 10 played) with team sums equal to the score (per-team split plus remainder on the first row). `align_box_to_score` adds only minutes = 0 rows, so the played count never rises. In test_refuse_not_report, the 9-played case is still refused for thinness (box agrees after align), so A's assertion keeps its meaning. The score bump preserves the margin, so the win y is unchanged. One slight loosening (m3) | PASS with m3 |
| 6 | Tests | `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/daily tests/ops tests/test_layering.py --no-cov -q -p no:cacheprovider` | `464 passed, 13 warnings in 307.53s (0:05:07)`, exit 0. The known combo-reader flake did not fire | PASS |
| 6b | Lint, types | `ruff check --no-cache <12 changed py files>`; `ruff format --check --no-cache <same>`; `mypy --no-incremental nba/daily/settle.py nba/daily/__main__.py`; `sh -n ops/nba_daily.sh` | "All checks passed!" exit 0; "12 files already formatted" exit 0; "Success: no issues found in 2 source files" exit 0; exit 0 | PASS |
| 7 | Oracle reasoning | read `nba/daily/rehearsal.py:67-100`, `nba/daily/canonical_dump.py` | see "Oracle" | not run (separate) |

The six adversary checks for model claims (shuffled labels, planted future, missingness, knockout,
replication, integer support) do not apply: this change makes no performance claim. The planted-future
analogue is the in-progress box. `test_partial_box_scores_no_target_and_an_agreeing_box_scores_all` covers
it: a 70-of-120 home box writes nothing to either table, and the corrected box then scores all keys.

## Frozen-rule judgement (FORWARD_PREREG_2026_27.md rules 1-4)

The steward's paragraph (`DAILY_PIPELINE.md:787`, "changes WHEN a row is scored, never WHAT") is **true
for every game whose box eventually agrees, and incomplete for the rest**.

- Agreeing games: nothing about the elig row depends on `scored_at` except the `scored_at` column itself.
  The eligible prediction is chosen by `made_at` against `tipoff` (rule 3), `y` comes from the same box
  rows, the win `y` from the same score, and RPS from the stored `p_ge_full` (rule 4). A delayed row is
  byte-identical apart from `scored_at`. This is the allowed "stricter condition on WHEN".
- It also fixes WHAT in the direction the rule wants. Without the gate, a partial box (or a partial games
  score after a final box, OKC@SAS 44-65) would have been written once with a wrong `y`, and rule 4 scores
  the true outcome.
- Permanent defects: the game's keys are never written for ANY arm, so the game drops out of the pairs on
  both sides. Rule 2's "never dropped silently" counts one-sided rows (`only_arm`, `only_comparator`,
  `dnp`). A two-sided drop is a population change against rule 1 ("regular-season games of season =
  2026"), and no counter sees it. Expected size: 0.12-0.82 games per season from source defects (CI
  above). That is negligible to any CRPS delta at the declared floor of -0.005, but it should be
  counted, not invisible. In-progress cached boxes are a different, possibly frequent mechanism (M1). They
  delay a game and do not lose it, but only once a re-pull exists.
- Acceptable? Yes, with two conditions. (1) The checkpoint/report lists refused-and-never-scored games
  (F1b). (2) The main session appends a dated engineering note (no rule changed) to the Amendments of
  `FORWARD_PREREG_2026_27.md`, as the 2026-10-09 notes did: "settle grades a game only when its box sums
  to its score; a game whose official box never agrees is not scored for any arm and is listed by the
  checkpoint". This is append-only and inside the frozen-text sha's allowance.
- Is rc 5 visible enough? It is visible: a daily alert naming the game. Without M1's runbook it is not
  *actionable*, and a permanent rc 5 trains the operator to ignore settle alerts. With the runbook, the
  operator re-pulls, and if the official source is wrong, logs a DECISIONS row. Until F1c exists that
  still leaves a daily alert. F1c: an acknowledged-exceptions list that keeps the game unscored but stops
  counting it as stale, a Devin decision.

## Oracle reasoning (check 7)

The replay (`replay_season`) restores each date's `games` scores and all game-keyed tables, including
`player_game_stats`, from the untouched full copy (`rehearsal.py:75-97`), and then runs `settle_pending`.
`canonical_dump` hashes `forward_predictions` and `forward_scores` only. The hash moves exactly when a
replayed 2025 game that settled under main is now refused, meaning its restored box does not sum to its
restored score. That happens through a source defect like the four in 2013-21, through a played row with
a NULL or foreign `team_id`, or through a one-team box. Those keys would be missing from `forward_scores`.
If the oracle comes back DIFFERENT, that is the only explanation that needs no other diff. It can be
diagnosed without reading outcomes by counting refused games in the branch copy (`refused_games`
against the branch replay DB, reporting game counts only, logged first). Note: `forward_scores_elig` is
not in the hash, so the oracle cannot see changes on the elig path. The elig path uses the same fragment.

## Playbook check (docs/MODEL_PLAYBOOK.md)

- Model class: none. This is scoring infrastructure (the settle and truth boundary feeding the
  pre-registered scorer), not a model.
- Information or capacity: neither. It changes no forecast. It decides when an outcome counts as known.
- Enough data for this class: the census (15,474 games, 0.026% disagreement) is what the gate's
  false-refusal rate rests on. It holds.
- Test validity: the fixtures now build boxes that sum to the score. The thin-box test keeps its meaning
  because the filler rows have minutes = 0. Gaps are listed in m4.
- Lineage and inherited guards: it inherits A's 4 h guard, A's >= 10 played rows for winners, and B1's
  "withdrawn keys still elig-scored" (m1 is the one inconsistency). The shadow settlers did not inherit
  A's guards or this one (M2).
- What it is scored against: the official box and the official score, which must agree. Disagreement means
  refuse, never pick one.

## Corrected family and power

There is no statistical claim, so there is no family and no BH. The only number is the census
disagreement rate, 4/15,474 (Wilson 95% [0.010%, 0.066%]), unclustered because games are the unit.

## What I could not verify

- The 2025 replay oracle (running separately; I did not read season 2025).
- Whether the live boxscore endpoint returns a partial box during a game or an empty frame (an empty frame
  would be marked failed by `allow_empty=False` and retried, which would make M1 rare). No opening-night box
  disagrees now, and I have no record of what the 19:50/20:20 pretip pulls returned.
- Behaviour against a live DB after `forward_withdrawals` exists (the dry read substituted FALSE).
- The shadow settlers' exact line numbers for M2 are approximate (~). The absence of a tip guard was
  confirmed by grep (no `tipoff +` clause in any of the four).

## Recommended changes (not applied by me)

On this branch, before merge (doc and tests only; no scoring code changes except m1):
1. M1 runbook text at `docs/DAILY_PIPELINE.md:798-799`.
2. M2 sentence at `docs/DAILY_PIPELINE.md:798`.
3. m1 withdrawn placement (`settle.py:167`). It affects only the refusal listing, never what is written.
4. m3 and m4 tests. m5 table text. m2 and m6 optional.

Follow-ups (own branches, own reviews):
- F1a (ingest steward): `missing_boxscore_game_ids` also selects games of the season whose box disagrees
  with the score, and bypasses `fetch_cached` for them. Bound it, e.g. at most 3 re-pulls per game, then
  leave it to the operator.
- F1b (checkpoint owner): list season-2026 games past 24 h that are refused, by reason, in the checkpoint
  snapshot and report.
- F1c (Devin): whether to allow an acknowledged-exception list for official-source defects.
- F2 (shadow owners): the 4 h guard and `_BOX_AGREES_SQL` in the four shadow settlers.

## Drafted rows (not appended)

DECISIONS (draft): "2026-10-21: settle grades a game (any target, primary and pre-registered tables) only
when per-team box points equal the game score. Why: an in-progress box is cached for good, and the games
feed can lag a final box (OKC@SAS 44-65). Reverse if: refusals of games that are later confirmed correct
exceed 1 per month, or the official source shows systematic box/score disagreement."

FORWARD_PREREG_2026_27.md Amendments (draft engineering note, no rule changed): the text quoted under
"Frozen-rule judgement", acceptable condition (2).

## Re-check f5f0f63 (2026-10-21)

Read: `git diff 5521209..f5f0f63` (6 files, +77/-14; no data/, ingest_step, pipeline or FORWARD_PREREG
change: the grep exited 1).

Verdict: APPROVED

| Item | Status |
|---|---|
| M1 runbook (DAILY_PIPELINE.md:802-818) | Fixed. The steward's correction is right: `nba.daily ingest` cannot re-select a game that has rows. I checked each step against the code. `cache_path_for('boxscore', gid)` = `data/boxscore/<gid>.parquet` (cache.py:125-128). `is_cached` needs a 'done' `ingest_log` row AND an existing file (cache.py:131-139), so either step 2 action alone forces a refetch; doing both is belt and braces. `ingest_log` has PK (source, key) (schema.sql:273-280), so the DELETE is exact. `pull_game_boxscore` -> `fetch_cached(allow_empty=False)` -> `upsert_player_game_stats`, which runs `DELETE ... WHERE game_id IN (df game ids)` and then inserts (boxscores.py:197-208). The rows are replaced. If the refetch comes back empty, it raises before any delete, so the old rows survive (safe). |
| M2 shadow sentence (DAILY_PIPELINE.md:798-800) | Fixed. |
| m1 withdrawn placement (settle.py:169-176) | Fixed: the withdrawn test now sits inside the forward_scores branch only. No dedicated test (nit n2). |
| m2 one scan (`settle_with_refusals`, settle.py:88-159; `__main__.py:378`; elig call removed; `bx` restricted to the keys' games) | Fixed. `settle_pending` keeps its signature (returns `[0]`). |
| m3 `_counts` now asserts all four refusal counts are 0 (test_daily.py:295-301) | Fixed. |
| m4 tests: one-team box, foreign team_id, thin+mismatched precedence, props-only, 23 h 59 m (test_box_consistency.py:129-157) | Fixed (see nit n1). |
| m5 morning table row (DAILY_PIPELINE.md:216) | Fixed. |
| m6 filler docstring (conftest.py:78-80) | Fixed. |

Verification (steward worktree at f5f0f63, read-only, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`):
- `uv run --no-sync pytest tests/daily tests/ops tests/test_layering.py --no-cov -q`: `466 passed, 13 warnings in 275.14s (0:04:35)`, exit 0. That is 464 plus the 2 new tests. The combo flake did not fire.
- `ruff check --no-cache` on the 5 changed .py files: "All checks passed!" exit 0. `ruff format --check`: "5 files already formatted" exit 0. `mypy --no-incremental nba/daily/settle.py nba/daily/__main__.py`: "Success: no issues found in 2 source files" exit 0.

Nits (none blocks):
- n1. test_box_consistency.py:156-157: the 23 h 59 m assert (`refused_stale == 0`) would also pass if nothing were refused. Fix: `out = settle_pending(con, near); assert out["refused_box_mismatch"] == 1 and out["refused_stale"] == 0`.
- n2. No test for m1. Fix: withdraw one prereg prop key of a mismatched game whose other keys are all scored in forward_scores, then assert the game is still listed by `refused_games` (via its elig key).
- n3. Runbook step 3 (DAILY_PIPELINE.md:813): use `uv run --no-sync` (repo convention), and say to run it from the main checkout so `connect()` opens the production `nba.duckdb`.

Prereg Amendments note held for Devin: this does not change the verdict on the branch. The branch does not
edit the frozen text. Every game whose box ever agrees is scored identically, only later. The rest (at most
about 0.8 games per season, Wilson upper bound) is documented in runbook step 4 and stays a population note
for Devin to place.
