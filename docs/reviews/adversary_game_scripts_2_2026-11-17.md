# Adversary review: game scripts merge-ready (`agent/market-game-scripts-2` 00038828), 2026-11-17

Scope: the branch's diff against `main` (19 files, +4706/-6). Reviewed in the worktree
`~/Downloads/nba-wt-adv-game-scripts` (branch `agent/adversary-game-scripts-2`, cut from 00038828). Requested by the main
session before it merges into `main` on the machine that runs the live job. Inputs: the builder's report
(`game_scripts_merge_ready_2026-11-17.md`), `combo_night_builder_2026-11-17.md`, the 11-10 review, the three contracts, and
GAME_SCRIPTS_FORWARD rev 2 (`agent/modeler-game-scripts-forward` a7760b7d, rule text 563a1c67, not frozen).

**Verdict: WOUNDED.** The plumbing is safe. I found no leak, and nothing here can fail the export or a JARVIS tick. The
scored quantity is untouched. Three things are wrong or unguarded, and each fix is small:
1. **The app's checkpoint view does NOT turn on at merge.** The writer emits `"version": 2` and the app rejects every version except 1.
2. **The step publishes season-2026 realised-script counts every night.** That contradicts the forward rule's exposure answer.
3. **Nothing tests or guards the season of the nightly `mu_t` history read.** It is the one new holdout-adjacent read.

Verdict: BLOCKED

BLOCKED until M1 to M3 land. They are small edits. Once they land, the verdict becomes APPROVED without another round, unless a fix
touches something else.

Top finding: the merge's stated outward effect is false. On a game night, the JARVIS tick writes
`ingame_checkpoints_latest.json` with `version: 2`. `app/api/src/parlayhitters_api/live.py` line 728 accepts only
`version == 1`, so every game shows `available: false`, reason "no checkpoint snapshot". I reproduced this with the real writer
and the real reader. The app's own test pins it: `app/api/tests/test_live.py` line 260 asserts that version 2 is
unavailable. The draft DECISIONS row says "Merging turns on the app's checkpoint view", and that would record a falsehood.

## Findings (severity-ranked; none applied by me)

### Blockers
None.

### Majors

**M1 (major, record and contract): merging does not turn on the app's checkpoint view.**
- Where:
  - `nba/markets/checkpoint_snapshot.py` line 28 (`VERSION = 2`).
  - `app/api/src/parlayhitters_api/live.py` lines 726-731 (`parsed.get("version") == 1`).
  - `docs/contracts/ingame_checkpoint_snapshot_v2.md` lines 5-7 ("A v1 reader that ignores unknown keys keeps working").
  - The merge-ready report, section 2 row `nba/jarvis/ingame_checkpoints.py`, and the drafted DECISIONS row in its section 10.
- Failure scenario: Devin's go was given on "merge equals go-live of the checkpoint view". After the merge, the view stays
  dark on every game night. The DECISIONS log then records a go-live that never happened. A later app change that accepts
  v2 would be the real go-live, with no decision row of its own.
- Smallest fix: no code change in this branch.
  - Correct the report and the drafted row to say: "merging writes the v2 snapshot on every live tick; the app keeps showing
    nothing until `live.py` accepts version 2 (an app change, Devin's decision)".
  - Correct the v2 contract sentence to: "the app reader in `live.py` checks `version == 1` and must be changed to accept 2".
  - If Devin wants the view on, make that a separate app change that accepts `{1, 2}`. It should also add a snapshot age check
    (m10) and say what label the rows carry (section "App side").

**M2 (major, forward-rule integrity): the step publishes a season-2026 realised-script aggregate every night.**
- Where:
  - `nba/parlay/combo_hist.py` lines 171-179 (`attach_hist` returns `past_games` with per-script counts of finished
    season-2026 games).
  - It is carried into `combo_<date>.json` `build_log.hist.past_games`, and into the manifest at
    `nba/export/combo_stage.py` line 626 (`"hist": {k: ... for k in ("n_entries", "past_games")}`). It is printed by the
    `combo_night` CLI.
- Why it matters:
  - The 11-10 review's check E ("No 2026 realised-script aggregate") passed because no code did this.
  - GAME_SCRIPTS_FORWARD section 10's "Exposure answer" states that no session computed 2026 script shares.
  - Both builder reports (combo_night_builder section "Output", merge_ready section 3) now print these counts for 205 games.
    I do not repeat them here.
  - The rule scores only post-freeze games, so the scored set is disjoint. The rule is still a draft, though, and its floors,
    definitions and priors can still be revised after someone has seen pre-freeze shares. That is the exposure the answer
    guards against.
- Failure scenario: the rule is frozen with an exposure answer that is false. The freeze adversary cannot tell whether any
  revision after 11-17 was informed by realised shares. The manifest keeps publishing fresh shares every night until the look.
- Smallest fix:
  - In `combo_hist.attach_hist`, return only `{"n_games": len(scripts)}` under `past_games`. Keep the per-player `[n, k]`
    counts, which are the display.
  - In `combo_stage.py` line 626, carry only `n_entries` and `past_games.n_games`.
  - The main session adds a line to the rule's exposure answer before freezing. The line records that season-2026 realised
    script counts through 2026-11-16 (205 games) were printed in two reviews on 2026-11-17, with no comparison to P(s).
  - The freeze adversary judges that line.

**M3 (major, holdout guard): the nightly history read's season is neither guarded nor tested.**
- Where:
  - `nba/export/build.py` line 441, `combo_stage.history_rows(src, day, guard.LIVE_SEASON)`.
  - `nba/export/combo_stage.py` lines 166-176. `history_rows` takes any season and has no `_assert_allowed` belt, unlike every
    reader function.
- Evidence: I mutated the call to `history_rows(src, day, 2025)`. All 157 tests in `tests/export` and the 76 tests in the four
  new test files still pass (mutation m9 below). The pinned SQL test checks the text, not the argument.
- Failure scenario: a one-token edit, a refactor, or a later "configurable season" change makes the nightly export read
  season-2025 finals from `nba.duckdb` into `mu_t` and `hist` every night. That is a production read of the frozen holdout,
  and no test fires.
- Smallest fix:
  - In `history_rows`, `if season != guard.LIVE_SEASON: raise guard.ExportRefused(...)`. The offline caller passes 2026, so
    nothing else changes.
  - Add a test that seeds a season-2025 final into the fixture `games` table, calls `build._history_stage`, and asserts that
    no returned row has a 2025 date or id. A second test asserts that `history_rows(con, day, 2025)` raises.

### Minors

**m1 (known-at belt): the nightly stage does not bound rows by the export's own `made_at`.**
- Where: `nba/export/build.py` line 497, the `cs.build(...)` call, which passes no `asof`. With `asof=None`, `_pretip` (`combo_stage.py` lines 135-149) checks only tip.
- Mechanism:
  - `build_export` takes `made_at` at line 600 and only then opens the source (`reader.open_read_only`, up to 30 s of lock retry).
  - If a pretip run that started after `made_at` wins the lock first, its rows are read. Those rows can be stamped up to about
    30 s after the export's `made_at`, so a block's `made_at` can exceed the file's `generated_at`.
- Effect: harmless for leakage, because every row is still before tip. It does break "nothing newer than the export".
- The builder's 01:20Z rows in a 01:05Z export: I could not reproduce them.
  - On the current export (meta `made_at` 2026-11-18T01:35:04Z), 0 rows in `win_probs`, `p_play`, `prop_grids` and
    `model_stamps` are stamped after it.
  - Run stamps match their `stamp_stamped_at` within about 10 s, so `made_at` is wall-clock run start, not a nominal slot.
  - The 30 s retry window cannot produce a 15-minute gap.
  - Most likely the builder read the meta of the 01:05Z export and the tables of the 01:35Z export (a scratch-copy mix-up).
    That is unverified, because the builder's copy no longer exists in a form I could identify.
- Fix: pass `asof=made_at` from `_scripts_stage` into `cs.build`.

**m2 (robustness): one invalid game blanks the whole night.**
- Where: `combo_stage.py` lines 613-617. Any validator error returns `absent` for every game, which removes the JARVIS
  snapshot's pre-game script priors for the night.
- Example: a single schedule tip that moved earlier than a row's stamped `tipoff` would do it. `_pretip` prefers the row's own
  `tipoff` at line 142, and the validator then fails `made_at < tip_utc`.
- Answer to the builder's question: the picks file should not survive alone, because its blocks are built from the combo
  blocks. Drop the offending game instead.
- Fix: re-run the validator per game, drop the games with errors, list them in `build_log` and the manifest, and write the
  files if at least one game passes.

**m3 (validator bypass): price-key tokens are incomplete.**
- Where: `nba/parlay/combo_night.py` lines 43-61. The list does not contain `ask`, `bid`, `spread`, `edge`, `cost` or `cents`.
  A key `yes_ask` or `edge` would pass, and camelCase keys are not split. Values are not inspected.
- Evidence: removing `fee`/`fees` from the list still passed every test (mutation m10).
- Current state: tonight's payload holds none of these keys (I listed every key of the rebuilt file).
- Fix: add the six tokens, split camelCase too, and add one test per token.

**m4 (residual of 11-10 M2): a temp directory still leaks if the copy cannot be opened.**
- Where: `nba/markets/asof.py` lines 87-90. If `shutil.copy2` or the `duckdb.connect` on the copy raises (a garbage store,
  disk full), the `mkdtemp` directory leaks. `read_markets` never learns the path because no connection was returned.
- Fix (markets-owned): wrap lines 88-90 in `try/except` and `rmtree` the temp directory before re-raising.

**m5 (market block on games in play):** `combo_stage.ask_index` lines 342-346 accepts a quote with `ts < tip` and an age of at
most 60 min. The stage keeps already-tipped games (choice 6), so a build up to 60 min after a tip attaches a pre-tip ask to a
game in play. Contract M1 makes the app hide it, but no app reads the file yet. Fix: `elif ts_ >= tip or tip <= now:`, so a
tipped game gets no market block.

**m6 (`ev` beside the verdict): the shrunk point `ev` is still exported.**
- Where: `combo_stage.make_market_fn` line 408 exports `ev` (the shrunk point) as well as `ev_low`. The merge report's m6 row
  says "picks export only `ev_low`", which is not what the code does.
- Today: `model_weight` = 0, so `ev` is negative by construction.
- Failure scenario: once the track record shows skill, `ev` can be positive beside `no_positive_ev_found`. That is the
  pattern 11-10 m6 wanted gone.
- Fix: drop `ev` from the block, or correct the report and make the contract say that `ev` is never shown without `ev_low`
  beside it.

**m7 (static claim):** `combo_stage.MARKET_NOTE` lines 71-76 hard-codes "shows no skill yet ... the verdict is
no_positive_ev_found". Fix: build the sentence from `track.skill` and the counted verdicts, or word it conditionally, as
11-10 m7 proposed ("Unless the paper track record shows positive skill ...").

**m8 (tests depend on the environment):**
- Where: `tests/export/test_scripts_stage.py`, `test_the_manifest_files_block_and_hashes_are_unchanged_by_the_step`
  (lines 384-390) and `test_a_missing_game_model_fails_soft_and_a_good_run_writes_atomically` (lines 363-381).
- Problem:
  - In a worktree without `data/` (and in CI), the scripts-on build ends `absent error:FileNotFoundError` because
    `data/parlay/joint_model.json` is missing (probe below). So the "unchanged" test compares an off build with a failed step,
    never with a successful one.
  - The second test accepts `ok` or `absent`.
  - The first test compares keys and row counts, not hashes or contents, despite its name.
- Fix: pass `game_model=gm_path` in `_build` for the on build, assert `status == "ok"` and that both files exist, and compare
  every table's contents with `meta.made_at` excluded.

**m9 (Kalshi lock exposure):**
- The export now opens `data/kalshi/kalshi.duckdb` read-only on every build, about 22 a day at :05/:35. The read took 0.083
  to 0.115 s for 2,818 markets on a copy.
- The snapshot writer (`nba/kalshi/__main__.py` line 130, plain `connect`) does not retry. `snapshot.err` already holds 13
  "Could not set lock" failures from other readers.
- Expected new collisions are rare: about 0.1 s against a 900 s cycle.
- Fix (markets-owned): `connect_with_retry` in the snapshot writer.

**m10 (app, when v2 is accepted):** `live.read_checkpoint` has no snapshot age check. If JARVIS dies mid-game, the last rows
show for as long as the game is "in". Fix in that app change: hide the rows when `now - generated_at > 180 s`.

**m11 (contract text):** the example `label` in `ingame_checkpoint_snapshot_v2.md` line 28 lacks the overtime sentence that
`SCRIPT_LABEL` carries. Fix: copy the code's text.

## The six attack areas

### 1. Can it break or slow the export or JARVIS?

- **Guard.**
  - `_history_stage` and `_scripts_stage` each catch `Exception` around everything: imports, Kalshi, build, validation and
    writes.
  - Mutating the outer `except Exception` to `except ValueError` fails 5 tests (m4 below). The crash, import, missing-model
    and second-file tests all fire.
  - Disk full on either temp write is an `OSError` inside the guard; temps are unlinked and the manifest says `absent`.
  - Not contained: `KeyboardInterrupt`, `SystemExit` and a native crash. These are the same exposure as every other stage.
- **Order of writes.** The stage runs after `export.duckdb` and `parlay_of_day.json` are written and before `manifest.json`.
  It runs inside a fresh staging directory (`.latest.stage.<pid>`) that `nba.ops.app_export` swaps in only on rc 0. Two
  consequences:
  - A stale `combo_<date>.json` can never survive into `latest`, because each build starts from an empty directory. Files
    from earlier nights do not accumulate either.
  - A failed or validation-blanked stage leaves `latest` with no combo or picks file for that build, and the manifest says why.
- **Time and memory.**
  - No time budget is enforced in code. The 11-10 review demanded a size budget (m3), which is enforced (`enforce_budget`,
    tested); it did not demand a time budget.
  - Bounds: the Kalshi open is at most 6 x 2 s of retry plus a copy of about 126 MB; `paper_trades.duckdb` makes one
    read-only open with no retry (a lock error is caught, and the market block goes empty).
  - The offline rebuild on tonight's export (5 games, 149 players, 596 entries, `hist` over 205 games) took 2.42 s wall at a
    271 MB peak RSS for the whole process.
  - The 11-10 worst case of about 20 s on 15 games still holds. The app_export deadline is 600 s per run.
- **Existing export unchanged.** I proved this by reading the code, because I may not open `nba.duckdb` for a real
  `build_export` diff:
  - No table, column, `files` entry or row count changes. The stage reads `fwd`, `box`, `players` and `teams` without
    mutating them; `_production_rows` copies its rows.
  - The manifest gains one key, `game_scripts`. The app's `export_verify.py` checks only `DB_NAME`/`PARLAY_NAME` in `files`
    and tolerates extra keys.
  - `app_export` reads only `lines.status` from the manifest.
  - The CLI's summary line gains ` scripts=<status>`; nothing parses it.
  - `git diff main HEAD -- nba/daily ops configs app nba/ops` is empty. Main moved one commit after the merge base (docs only);
    `git merge-tree` is clean.
- **JARVIS tick.**
  - `publish_snapshot` runs after `store.close()`, never in a dry run, and only when the flag is on and a game is in play.
    `score_checkpoints` returns before it otherwise, so on an off night nothing is written.
  - It is double-guarded (`CS.publish` and `publish_snapshot` both catch `Exception`). Its output goes into the tick report
    only; it is not part of the card or alert path.
  - The write is atomic (temp file in the same directory, then `os.replace`) and refuses more than 5 MB rather than
    truncating. One file is overwritten each tick, so it cannot grow without bound.
  - The pregame cache holds one entry. The extra `latest_states` loop cost 0.02 to 0.03 s in the 11-10 measurement.
  - It cannot stop a tick or an alert. The 10-28 failure class (an unguarded step) is not present.

### 2. Known-at and leakage
- **Every input pre-tip.**
  - `_pretip` drops `made_at >= tip` for props, P(plays) and win rows. The validator re-checks block `made_at < tip_utc`.
  - Quotes must satisfy `ts < tip` and an age of at most 60 min (mutating the tip check fails a test).
  - Sits come from the T-60 report. `hist_rows` (SQL `game_date < day`, `season = ?`) and box logs (`gd < day`, season 2026,
    id digits) are strictly before the slate.
  - Mutating `gd >= day` to `gd > day` fails a test.
- **Choice 1 (`made_at` = newest input).** It cannot pass tip, because every stamp in `max(stamps)` already passed `_pretip`.
  It makes the main-line check weaker: a line stamped after the prop row's own run but before a newer win or P(plays) row is
  now kept. That pairing is still known at the block's `made_at`, and the calibrated value is the export's `line_cal`
  (validated within 1e-4).
  - Nightly this mostly does not bind, because `line_cal` pairs through the row's own `lines_through`.
  - No app or JARVIS consumer reads the block `made_at`, so nothing on screen changes.
  - The past-export case is m1.
- **Season 2025.**
  - `player_logs` drops `season != 2026` and ids whose season digits are not 26. Removing that filter fails 2 tests (m1
    below). `hist` cannot take a 2025 value.
  - `mu_t` history is season 2026 by SQL, but the argument is unguarded (M3).
  - Each past game's `mu_t` comes from `asof_total_features`, which computes a date's feature before that date's update.
    Only earlier dates contribute.
- **The 01:20Z rows:** see m1. I could not reproduce them; on the current export, 0 rows are stamped after its own
  `made_at`.

### 3. Is the scored quantity untouched?
- `history_rows` is byte-identical to `agent/market-game-scripts` 529a39ef (`awk` extract, `diff` exit 0). Its SQL text equals
  the harness's `as_of_mu_t` text.
- `nba/parlay/{game_model,combo,combo_build,line_cal}.py` and `nba/export/{line_cal_fields,reader,rung_cal_fields}.py` are
  identical between this branch, `main` and the modeler branch (`git diff --quiet`, all 0).
- The offline box-sum fallback exists only in `nba/parlay/combo_night.py` (`HISTORY_BOX`, reached through
  `build_night`/the CLI). `build.py` never imports `combo_night` for history; nightly history is `_history_stage`, which
  reads the `games` table or falls back to the league mean, never to box sums.
- The rule does not score the app's file at all. The harness recomputes `mu_t` with its own copy of the SQL, and `combo_stage`
  is not in the P family's execution path. So no fallback can be what the rule scores.
- **The pin.** Rule section 4.1 and harness `CODE_PATHS = ("nba/parlay", "nba/export", harness)` refuse the look on any
  difference in those two packages from the freeze to about 2027-03-07. With the line-pairing change and more combo work
  planned, that blocks four months of work in two live packages, or forces the look to run from a checkout of the freeze tree.
  Most of the pinned surface does not affect the scored number:
  - The P family executes only `nba.parlay.combo` and `nba.parlay.game_model`. Their static closure is 8 `nba.parlay` modules
    and no `nba.export` module.
  - The S family executes `reader`, `line_cal_fields`, `rung_cal_fields` and `build._read_lines`, plus `nba.parlay.line_cal`
    and `top_source`.
- **Proposed narrowest pin** (the rule's text is not edited here; this is for the next revision before freezing):
  - Refuse on any change to the sha256 of `nba/parlay/game_model.py`, `nba/parlay/combo.py` (family P), and
    `nba/export/reader.py`, `nba/export/line_cal_fields.py`, `nba/export/rung_cal_fields.py`, `nba/parlay/line_cal.py`,
    `nba/parlay/top_source.py` (family S). List the hashes in the freeze commit message.
  - Also refuse on any change to a sha256 of the source text of `nba.export.build._read_lines` (`inspect.getsource`), because
    `build.py` will keep changing for other reasons.
  - Keep the harness and the three data pins.
  - Print, without refusing, whether `combo_stage.history_rows`, `combo_hist.game_mu_t` or `combo_build.build_game_payload`
    changed since the freeze. These decide whether the app showed the scored number, not whether the scored number is right.
  - What stays free: `combo_stage` (except the printed check), `combo_night`, `combo_hist`, `game_scripts`, `teammate_sits`,
    and the rest of `build.py`.
- **The one real conflict.** The planned line-pairing change edits `nba/export/line_cal_fields.py`, which defines the S
  family's legs. Under any pin that protects S, it must land before the freeze, or wait until after the look.

### 4. Rules of meaning
- **No price key in the combo file.** The validator runs inside the stage before anything is written, and mutating the
  `absent` return fails a test. The token list is incomplete (m3). Tonight's rebuilt file has no price-like key (every key
  listed).
- **Scripts are labelled.** Scripts carry `status` from `GS.STATUS` and the shared `script_status`.
- **EV in the picks file.** It is the engine's stored output (`nba.parlay.ev.evaluate`); raw EV is not exported (m6 covers
  the shrunk `ev`).
- **The JARVIS snapshot.**
  - It has no price, ask, bid or edge key (tested).
  - The script block is absent for a state older than 180 s (mutation m7 fails a test), in overtime, or without pregame
    numbers.
  - Its rows carry `p_model` beside `cur`, the current stat in play, never the final. The app hides everything for
    `game_state == "post"`.
- **The nine 11-10 findings, re-read in the code:**

| 11-10 finding | status | where |
|---|---|---|
| M1 (contract: market block only while `now < tip` and quote at most 60 min old) | fixed in the contract; the belt in the data is m5 | `game_scripts_v1.md` |
| M2 (Kalshi copy leak) | fixed for the locked store; a residual remains (m4) | `combo_stage.py` lines 276-292 |
| m1 (all or nothing) | fixed | `build.py`: both temps written, then both swapped, unlinked on failure (mutation m2 fails a test) |
| m2 (history status and `mu_t_source` in the manifest) | fixed | `build.py` |
| m3 (size budget) | fixed | `enforce_budget` |
| m4 (state age, overtime label) | fixed | `checkpoint_snapshot.py` lines 30-38 and 90-95 |
| m5 (proxy tip) | stated in the contract | `game_scripts_v1.md` |
| m6 (what sits beside a price) | contract fixed; the code still exports `ev` | m6 |
| m7 (MARKET_NOTE wording) | changed, but hard-codes the current state | m7 |

### 5. The app side
- **At merge, on a game night:**
  - The export writes `combo_<date>.json` and `game_scripts_<date>.json` and the manifest key. No app route or web
    component reads them (grep of `app/api/src`, `app/web/src`: 0 hits), and the artifact bake goes through API routes, not
    files.
  - JARVIS writes the v2 snapshot. `/api/live/{id}/checkpoint` returns `available: false`, "no checkpoint snapshot" (M1), and
    `CheckpointPanel` renders nothing.
- **At merge, on an off night:** nothing is written, and nothing changes.
- **Unlabelled numbers:** no number reaches the screen at merge, so none is unlabelled.
- **If v2 is accepted later:**
  - The reader whitelists only the rows, with `CHECKPOINT_LABEL` = "UNPROVEN: no live record; most nights the market already
    knows this." That says untested in substance, but it is not Devin's chosen wording ("experimental, not yet tested").
  - The script block's live chances are not passed through at all. Adding them needs their own label (`SCRIPT_LABEL` says
    "EXPLORATORY and UNPROVEN").

### 6. Tests and the replay closure
- **Mutations** (scratch copy `adv2/mut`, four test files, 76 tests): 10 mutations, 8 killed, 2 survived.

| id | mutation | result |
|---|---|---|
| m1_season | `player_logs` season and id filter removed | killed (2 failed) |
| m2_unlink | rollback unlink after a partial swap | killed (1) |
| m3_pretip | `made >= tip` allowed | killed (1) |
| m4_guard | outer `except Exception` narrowed to `except ValueError` | killed (5) |
| m5_quote_tip | post-tip quote allowed | killed (1) |
| m6_validate | validation errors ignored | killed (1) |
| m7_state_age | stale-state refusal removed | killed (1) |
| m8_hist_day | slate-day games counted in `hist` | killed (1) |
| **m9_hist_season** | nightly `history_rows(..., 2025)` | **survived** 76 tests and all 157 of `tests/export` (M3) |
| **m10_price_tokens** | `fee`/`fees` tokens removed | **survived** (m3) |

  The guard, the all-or-nothing write, the `hist` season filter and the known-at refusals are pinned. The nightly history
  season and parts of the validator are not.
- **Replay closure.** I walked the static imports, including function-level imports, of `nba.daily.replay_season`,
  `nba.daily.canonical_dump` and `nba.daily.__main__`: 188 modules. None of the 11 touched modules is in the closure, and no
  `nba.export` module is. It does contain 35 `nba.parlay` modules, none of them changed here. PASS.
  - Expected oracle: IDENTICAL. I did not run it; the main session runs it.

## What I ran (worktree root unless stated; Python `/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python` with `PYTHONPATH` = the worktree; `import nba` -> this worktree, confirmed)

| check | command | result |
|---|---|---|
| lint | `ruff check` (9 touched source files + 4 test files) | All checks passed, exit 0 |
| format | `ruff format --check` (9 source files) | 9 files already formatted, exit 0 |
| types | `mypy` (9 source files) | no issues, exit 0 |
| new tests | `pytest --no-cov -q -p no:cacheprovider tests/export/test_scripts_stage.py tests/parlay/test_combo_night.py tests/parlay/test_game_scripts.py tests/markets/test_checkpoint_snapshot.py` | 76 passed, exit 0 |
| wider tests | `pytest ... tests/export tests/parlay/test_combo_night.py tests/parlay/test_game_scripts.py tests/parlay/test_combo.py tests/markets/test_checkpoint_snapshot.py tests/markets/test_checkpoint_scorer.py tests/jarvis/test_ingame.py tests/test_layering.py` | 340 passed, exit 0 |
| app tests | `pytest app/api/tests/test_live.py -k checkpoint` | not run: `fastapi` missing in this venv (exit 4); the behaviour was reproduced directly instead (next row) |
| M1 repro | `scratchpad/adv2/app_v2.py`: `CS.build_snapshot` + `CS.write_snapshot`, then `live.read_checkpoint(..., "in", path)` | written version 2, `available: False`, reason "no checkpoint snapshot", exit 0 |
| offline rebuild | `python -m nba.parlay.combo_night --export <copy> --date 2026-11-17 --out <dir> --joint-model <copy, sha a2b4c4a9>` | exit 0; 0 errors; 69 main lines checked, max abs diff 4.96e-05; 133,436 bytes raw / 28,485 gz; 2.42 s, 271 MB peak RSS |
| per-game stamps | `keys.py` on that file | 5 of 5 blocks `made_at < tip_utc` and `<= generated_at`; all `mu_t_source` "live-season games only" (offline box-sum input, not the nightly source) |
| rows after the export's `made_at` | `future.py` on the 01:35:04Z export copy | `win_probs` 0, `p_play` 0, `prop_grids` 0, `model_stamps` 0 |
| 10,000-sample rows | `n10k.py` | 48 production rows for 12 players, `is_primary`, `primary_source` `fallback_missing` ("no AVG row before tip"), plus 8 non-primary; already the app's primary rows, so showing them in the combo file is consistent; the 1/199 raw tail pass-through stands as the builder said |
| Kalshi read cost | `time_markets.py` on a copy of `kalshi.duckdb` (126 MB) | 2,818 markets; 0.115 / 0.093 / 0.083 s |
| fixture probe | throwaway test calling `_build(src, ..., scripts=True)` (deleted afterwards) | `absent error:FileNotFoundError` in a worktree without `data/` (m8) |
| replay closure | `closure.py` (AST walk, function-level imports included) | 188 modules; touched modules in closure: none |
| pin closure | `closure.py` on the harness's P imports (`combo`, `game_model`) | 13 modules, 0 `nba.export` |
| live dirs | `git diff --stat main HEAD -- nba/daily ops configs app nba/ops` | empty; `git merge-tree --write-tree main HEAD` exit 0 |

Every database opened was a copy in the session scratchpad, `read_only=True`. I opened no DuckDB under the main checkout's
`data/` and read no season-2025 row. No network was used, and I did not run the oracle.

## Not verified
- A real `build_export` diff between `main` and the branch. It needs `nba.duckdb`, which I may not open. Instead I proved it by
  reading the code, plus the (weak, m8) unit test.
- The games-table `mu_t` on tonight's slate and its equality with the harness's `as_of_mu_t` on real data. That needs a copy of
  `nba.duckdb`, and the maintainer's command is in the merge-ready report, section 7.
- The app test suite (no `fastapi` in this venv); the reader behaviour was reproduced directly.
- The origin of the builder's 01:20Z-in-01:05Z anomaly (m1). Its copy is gone.
- Whether the planned line-pairing change touches LINE_CAL_LIVE's own pin (its section 13). Check that before scheduling it.
- Behaviour on a 15-game night and on a proxy-tipped game. These were measured or stated on 11-10 and not re-run.
- Kalshi snapshot writer collision frequency (m9). I measured the cost, not the overlap.

## Recommended order
1. **This merge**, after M1 (text only), M2 (two lines in `combo_hist`/`combo_stage` plus a rule-exposure line) and M3 (a
   belt and two tests). m1 and m5 are one-liners worth taking in the same commit. The main session runs the oracle and
   expects IDENTICAL, because the step is outside the closure.
2. **The line-pairing change in `nba/export/line_cal_fields.py`** next, as its own branch, review and oracle, and **before**
   the freeze. It redefines the S family's legs, so it belongs in the frozen tree. Otherwise it waits until about 2027-03-07.
   Check LINE_CAL_LIVE's pin first.
3. **GAME_SCRIPTS_FORWARD freeze** last:
   - Rebuild the modeler branch on that `main`.
   - Revise the rule text before freezing: the narrow pin above instead of whole packages; rule line 2's citation of
     529a39ef updated to the merged commit; the exposure answer updated per M2.
   - Then an adversary pre-freeze re-review and `status --assume-freeze <date> --s-legs`.

## Playbook check
- **Model class:** set and joint model (the combo-mode Gaussian copula with script shifts). It sits on a ratings-derived game
  model (injury-Elo margin, as-of season-2026 total feature) and an exploratory in-game independent-increments update. This is
  a wiring and delivery review, not a model test.
- **Information or capacity:** neither is added. Inputs are stored pre-tip forecasts, posted lines, P(plays), and finished
  season-2026 games for the as-of total and the display counts. Nothing is fitted.
- **Enough data for this class:** not applicable; nothing is fitted. The script parameters are the 2023-24 combo_eval fits, and
  no script is calibrated. Per-player `hist` cells hold n of 0 to 5 this season, so "k of n" is the only honest display.
- **Test validity:**
  - No claim is made, so no test is run. The forward test is GAME_SCRIPTS_FORWARD. Its validity depends on M2 (the exposure
    statement) and on the pin covering exactly what it executes (section 3).
  - The harness recomputes the scored number itself, so this branch cannot change what is scored, only whether the app shows
    the same number.
- **Lineage and inherited guards:**
  - combo_export_v1: banded calibration, `cal_k`, and the main-line parity check against `line_cal`.
  - LINE_CAL_LIVE lock: no prop prediction beside its outcome. The combo file holds no outcome of tonight's props, and `hist`
    counts are past games against tonight's line.
  - teammate_sits display rule (`sits_block`: pts numbers, reb direction only).
  - The cards' single EV path.
  - The checkpoint scorer's overtime and state-age refusals.
  - The 11-10 guard tests. All are carried forward.
  - Dropped: the "never rebuilt after tip" contract line (choice 6). It was replaced by pre-tip rows only plus an identity test.
- **Scored against:** nothing in this branch. The picks' market block is a price comparison of the unconditional probability
  shrunk by the paper record (weight 0 tonight).

## Corrected family and power
No inferential claim, so no family, no CI and no power statement. Every number above is a count, a size, a time or a code
diff, not an estimate. The builder's comparisons A to C are code-path comparisons on the same input, and they correctly carry
no CI.

## Single biggest risk
That the merge is recorded as "the checkpoint view is live" while it is dark (M1), and that the later one-line app change
which turns it on ships without its own decision, label review and staleness check. Close behind: the forward rule freezing
with an exposure answer the step has already falsified (M2).

## Drafted rows (not appended)
DECISIONS (main session, at merge, replacing the builder's draft wording on the app view):
`| 2026-11-17 HH:MM | MERGED agent/market-game-scripts-2 after adversary WOUNDED (adversary_game_scripts_2_2026-11-17): M1 the
app's checkpoint view stays OFF (live.py accepts snapshot version 1 only; the writer emits 2), M2 season-2026 realised-script
counts removed from build_log/manifest and the forward rule's exposure answer amended, M3 history_rows refuses any season but
2026 (+2 tests). Export step guarded and outside the replay closure (oracle <result>); JARVIS writes the v2 snapshot each live
tick, read by nothing yet. | Devin's go 2026-11-17 (finish game scripts). | A failed export traced to the step; any season-2025
row in mu_t or hist; the app showing checkpoint rows without a separate decision. |`

No ledger row (no test of a model was run).
