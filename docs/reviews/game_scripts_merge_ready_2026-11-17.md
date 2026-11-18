# Game scripts: merge-ready on today's main, one combo producer (2026-11-17)

Branch `agent/market-game-scripts-2`, cut from `agent/market-game-scripts` f04d1116, worktree `~/Downloads/nba-wt-game-scripts-2`.
Not pushed, not merged. **Follows advice / Devin's go:** Devin decided on 2026-11-17 to finish the game-scripts work; the brief's
reconciliation (stage = producer; combo_night = offline tool) follows the combo-night builder's own proposal
(`docs/reviews/combo_night_builder_2026-11-17.md`, "they should be one step, not two").

Commits on top of f04d1116: `b5ceb51e` merge main 55ccf5ca; `1b5ba917` merge `agent/market-combo-night` 7a98a267;
`a83a46ab` one producer; this report.

## 1. Conflict log

| merge | textual conflicts | semantic checks | result |
|---|---|---|---|
| `main` (55ccf5ca, 301 commits ahead) into the branch | none (`Automatic merge went well`) | the branch never imports the moved fee code directly: EV goes through `nba.parlay.ev.evaluate`, which on main calls `nba.markets.fees.ceil_cent_fee`; it does not touch `nba/kalshi/poll15.py` or `nba/jarvis/book.py`; `MAX_ASK_AGE_MIN = 60` is a quote age, not the T-60 lead (`tests/test_leadtime.py` 18 passed) | taken as merged; ruff, mypy (404 files) and the 308 affected tests green before any edit |
| `agent/market-combo-night` (7a98a267) | none (adds 5 files, edits `combo_export_v1.md`) | two producers of `combo_<date>.json` now coexist: resolved in section 3 | reconciled in `a83a46ab` |

No side was "taken" on any hunk because none conflicted. Live behaviour of everything already on main is unchanged: no file under
`nba/daily`, `ops/` or `configs/` differs from main, and the existing export tables and files are unchanged
(`test_the_manifest_files_block_and_hashes_are_unchanged_by_the_step`: same `files` keys and `row_counts` with the step on or off).

## 2. Live-path files that differ from main

| file | can it change what the nightly job or JARVIS does? |
|---|---|
| `nba/export/__main__.py` | **yes, additive:** `--no-scripts`, `--kalshi-db`, `--paper-db`, `--game-model`; the step is ON by default, so `nba.ops.app_export` (which runs `python -m nba.export build`) starts writing `combo_<date>.json` and `game_scripts_<date>.json` beside `export.duckdb` and prints `scripts=<status>` |
| `nba/export/build.py` | **yes, additive:** `_history_stage` reads `games` (season 2026) from the already-open read-only source; `_scripts_stage` (one guard; never fails the export) reads `kalshi.duckdb` and `paper_trades.duckdb` read-only and writes the two files all-or-nothing; adds manifest key `game_scripts`. Existing tables, files and row counts unchanged |
| `nba/export/combo_stage.py` | new; runs only inside that guard |
| `nba/jarvis/ingame_checkpoints.py` | **yes:** after the store closes, every live tick writes `data/jarvis/ingame_checkpoints_latest.json` (`publish_snapshot`; never raises; skipped in a dry run). `ingame_checkpoints.enabled: true` on main, and `app/api/.../live.py` reads that file, so **merging turns on the app's v1 checkpoint view** (11-10 review section F). No card, alert or ping changes |
| `ops/`, `configs/`, `nba/daily/` | nothing differs |

On the JARVIS tick but outside the listed directories: `nba/markets/checkpoint_scorer.py` (additive `latest_states` field) and
`nba/markets/checkpoint_snapshot.py` (new). Both exactly as the 11-10 review approved; this run did not touch them.

## 3. Reconciliation: one producer

**Producer:** `nba.export.combo_stage.build`. **Offline rebuild:** `python -m nba.parlay.combo_night --export <copy> --date D --out DIR
--joint-model <copy> [--asof ISO] [--history-db <copy of nba.duckdb>]`, which turns an export copy into the stage's inputs and calls
the same `build`. Removed duplicates: combo_night's own builder (row selection, calibration loop, `mu_m`/`mu_t`, player assembly,
provenance, `load_expected`), combo_stage's `_hist_frame` and `_main_line`, combo_hist's SQL readers and `tonight_mu_t`.
What each module now owns:

- `combo_stage`: inputs, known-at filtering (`_pretip`), `calibrated`, `main_line`, `history_rows` (byte-identical SQL, now pinned by a
  test), the payload, `build_log`, budget, and a call to the validator before anything is returned.
- `combo_night`: `validate_payload` (two new switches: `check_size=False` for the stage, whose budget step trims and flags instead;
  `raw_at_main_dropped=True` after the budget's first cut), the export-copy adapter, the CLI.
- `combo_hist`: `hist_frame`, `game_mu_t` (tonight's `mu_t`), `past_scripts`, `player_logs`, `hist_entry`, `attach_hist`; no database.

**`mu_t`** comes from `history_rows` (the `games` table, season 2026) through `combo_hist.game_mu_t` = `slate_total_feature` + `total_mu`,
rounded to 2 dp by `build_game_payload`. **`hist`** uses the same frame for past games' scores and pre-game `mu_t`
(`asof_total_features`, the same arithmetic per date), and season-2026 box rows for player logs (nightly: the export's in-memory `box`,
which already holds every final season-2026 game; season-2025 display rows are dropped in `player_logs`). Offline without
`--history-db`, the frame is built from the export copy's box-score team sums and `build_log.history_input` says so.

**Behaviour choices (stated, chosen before comparing):**
1. Block `made_at` = newest stamp of every input used (prop rows, win row, P(plays) rows). Before: newest prop row only, which
   understated the block whenever a later run's win row fed `mu_m`. The win row stays the newest pre-tip injury-Elo row (the forward
   rule's row), not "at or before the prop rows' stamp" (that would have moved `mu_m` away from the rule).
2. A main line is kept only if `line_cal.price_at <= made_at` (combo_night's check). Nightly it never fires (`line_cal` is built from
   the production row's own run); offline with an earlier `--asof` it drops lines the export copied from a newer row.
3. Calibration grouped by grid length (combo_night), not zero-padded to the stat's longest grid (stage).
4. Grids at another resolution (10,000 samples) are used with their own `n` (stage), counted as `grid_n_10000_used`.
5. P(plays): newest pre-tip `p_play` table row, else the row's own (stage).
6. Games that tipped before the build are kept (stage): their block is built from pre-tip rows only and is identical when rebuilt
   after tip (tested); the JARVIS snapshot needs their pre-game block. combo_night left them out.
7. Unnamed player: kept as `Player <id>` and counted `no_player_name` (stage). combo_night skipped them.
8. Provenance `combo_params_sha8` = sha of the params FILE (`1a85bb31`, the rule's pin; was the inner `sha256` field `8b5a124e`);
   `rule_sha8` added beside `rule_commit`.
9. A payload that fails validation: no combo file and no picks file; manifest `game_scripts.status = absent`,
   `reason = validation_failed`, first 10 errors listed.

### Numbers on tonight's export copy (scratch copy of `data/export/latest/export.duckdb`, meta `made_at` 2026-11-18T01:05:03Z, slate 2026-11-17, 5 games)

Descriptive comparisons of two code paths on the same input; no outcome, no claim, so no CI.

| comparison | n compared | equal | differ |
|---|---|---|---|
| A. old combo_night vs unified, `--asof 00:29Z` (before the first tip) | 5 games, 137 common players, 548 common stat entries | survival grids (max abs diff 0), `cal_k`, `k0`, `main_line` (65), `raw_at_main`, `hist` (65 entries), `mu_m`, `mu_t`, `sd_m`, `sd_t`, `starter`, names | `p_play` 41 of 137 (largest 0.3151: 0022600257 / 1629004, 0.9642 old vs 0.6491 new); `made_at` 5 of 5; 12 players only in unified (10,000-sample grids: 3, 4, 5 in games 253, 255, 256) |
| B. old stage vs unified stage, same inputs, `asof 00:29Z` | 5 games, 149 players, 596 entries | grids (max 0), `cal_k`, `mu_m`, `mu_t`, `p_play`, `starter`, 20 script priors | `made_at` 5 of 5; `main_line` 4 of 596 (game 257: lines copied from a 00:50Z row onto 14:00Z rows, dropped by choice 2); `hist` added on 65; params sha; picks file differs in game 257 only |
| C. old stage vs unified stage at the export's own cut (the nightly view) | 5 games, 149 players, 596 entries | every number: grids, `cal_k`, `mu_m`, `mu_t`, `p_play`, `main_line` (69), 20 script priors | `made_at` 2 of 5 (e.g. 0022600253: 14:00:22Z to 23:20:03Z, its 23:20Z win row and P(plays) rows); `hist` added on 69 entries; params sha |

Which is right where they differ: `p_play` the stage's (the newest pre-tip availability, and with choice 1 the block's `made_at`
now covers it; combo_night used the prop row's own stamp, up to 9 hours older); the 12 extra players the stage's (the map applies to a
probability, and `line_cal` itself computes `p_over` with the row's own `n`); `made_at` the new rule (the stamp must cover every input).

`mu_t` source: the export copy's `games` table holds only 51 completed games (the last week), so the app's games-table `mu_t` cannot
be recomputed from it; A to C all used box-score team sums for history, which tests the code path, not the source. On the 51 games
present in both, box-score team sums equal the `games` table scores on both teams (51 of 51). The games-table number on tonight's
slate needs `--history-db` on a copy of `nba.duckdb` (maintainer command below); this run was not allowed to open one.

Offline rebuild at the export's cut: 5 games, 149 players, 596 stat entries, 69 main lines checked against `line_cal.p_over_cal`
(largest abs diff 4.96e-05, tolerance 1e-4), 0 validation errors; 133,030 bytes raw, 28,449 gzipped (budget 300 KB / 60 KB);
`hist` on 69 entries; season-2026 past games 205 (blowout 72, close 37, shootout 62, grind 48; descriptive counts of finished games,
not compared with any forecast).

### Known gaps

**(a) Main-line coverage (69 of 596 entries at the export's cut; 65 of 548 / 436 in the combo-night run).** The stage already builds
from the newest pre-tip production row per prop (`rcf._production_rows`), so "build from the latest pre-tip forecast row" is what it
does today and does not raise coverage. The gap: in games 253, 256 and 257 the newest production row is the 14:00Z run (write-on-change:
later runs reconfirmed the forecast without writing a new row), and DraftKings' first snapshot is 15:04Z; `line_cal` pairs a line only
when stamped at or before the production row's own run. Pairing a 14:00Z forecast with a later line would loosen that rule; not done.
The fix within known-at is the one GAME_SCRIPTS_FORWARD's capture audit names: pair `line_cal` through the newest run that
**covered** the prop (stamped it) before tip. That is a change to `nba/export/line_cal_fields.py` on the live path; it changes the
`line_cal` export and the forward rule's family S as it would be frozen; Devin's call, not made here. Its size cannot be counted from the
export (prop lines are attached only to runs that wrote a row; 1 of 527 null-line props shows a pair at a later stamped run there);
the rule's audit estimates it would roughly double S legs.

**(b) 10,000-sample grids.** A resolution assumption, not a limitation: combo_night hard-coded 199; the stage divides by the row's
own `n`, as `line_cal` does. Fixed by using the stage path; `combo.py` and `combo_build.py` untouched, so the golden vectors are
unchanged (`tests/parlay/test_combo.py` passes). Residual assumption left in place: `calibrate_surv_banded` passes raw values below
1/199 through uncalibrated, so on a 10,000-sample row the tail between 0.0001 and 0.005 stays raw; changing it would touch
`combo_build` and its golden vectors. Tonight none of those 56 rows has a main line (`no_book_line`), so no export value checked them;
a unit test does. Adversary: confirm rows with `primary_source = 'fallback_missing'` are meant to be shown.

**(c) `hist` keys.** Exactly as `combo_export_v1.md` section 3 (combo-night text): optional, per stat entry, only where `main_line` is
set, `{"n_all", "k_all", "blowout": [n, k], "close": [n, k], "shootout": [n, k], "grind": [n, k]}`, integers; `build_log.hist` with
`past_games`. Validated by `_hist_errors` inside the stage. Without box rows the key is left out (not zero). Contract text revised only
where the source changed: past scores from the `games` table (offline fallback: player-point sums), `build_log` keys, `made_at`.

## 4. The 11-10 adversary findings after the merge

| finding | status on this branch | evidence |
|---|---|---|
| M1 market block only while `now < tip_utc` and quote <= 60 min | fixed | `game_scripts_v1.md` line 181 |
| M2 locked-store copy removed | fixed | `combo_stage.read_markets` `shutil.rmtree` of `market_ro_*`; 2 tests pass |
| m1 all-or-nothing files | fixed | `build.py` two temps then two `os.replace`, unlink on failure; test passes |
| m2 history status and `mu_t_source` in the manifest | fixed | `game_scripts.history`, `.mu_t_source` |
| m3 budget enforced and reported | fixed | `enforce_budget`; test passes (now also with the validator in the loop) |
| m4 state-age limit and overtime label | fixed | `checkpoint_snapshot.STATE_MAX_AGE_S = 180`, label text |
| m5 proxy tip | stated in contract | `game_scripts_v1.md` "Tip source (m5)" |
| m6 which columns sit beside `ask` | fixed | contract line 184; picks export only `ev_low` |
| m7 MARKET_NOTE wording | fixed | test passes |

## 5. GAME_SCRIPTS_FORWARD (rev 2, `agent/modeler-game-scripts-forward` a7760b7d, draft text 563a1c67): what must be true to freeze

- **The scored `mu_t`:** rule line 2, `total_a + total_c * f`, `f = game_model.slate_total_feature` over `combo_stage.history_rows(src, day, 2026)`
  on `nba.duckdb`'s **`games`** table, rounded to 2 dp as `combo_build.build_game_payload`. The harness recomputes it itself
  (`as_of_mu_t`, the same SQL text); the app's number is produced by `nba/export/combo_stage.py` `build` ->
  `nba/parlay/combo_hist.py` `game_mu_t(hist_frame(history_rows(...)))`.
- **Did this run change the scored quantity? No.** `history_rows` is byte-identical (now pinned by
  `test_history_rows_sql_is_the_text_the_forward_rule_runs`), `hist_frame` is the old `_hist_frame` moved, `slate_total_feature`,
  `total_mu`, the rounding and `combo.script_prior` are untouched; comparisons B and C show 0 difference in `mu_t`, `mu_m` and all 20
  script priors. The win row (rule line 1) is still the newest pre-tip injury-Elo row.
- **Preconditions on main (rule section 10):** this branch merged to main; the modeler branch rebuilt on that main so
  `nba/export/combo_stage.py` is in the frozen tree, its sha256 in the freeze commit message; an adversary re-review returning
  READY_TO_FREEZE; `WIN_VERSION` checked against production (`v2`); `status --assume-freeze <date> --s-legs` re-run label-free; pins
  `joint_model.json` a2b4c4a9, `combo_params_v1.json` 1a85bb31, `line_cal_map_v1.json` c46aef41 (all three match on this tree and the
  main checkout's `data/parlay/joint_model.json`).
- **Two things the main session should look at before freezing (not edited here):** (1) rule line 2 cites `agent/market-game-scripts`
  529a39ef for `history_rows`; the function is unchanged but now lives at a different commit. (2) The run refuses on any difference in
  `nba/parlay` or `nba/export` between the freeze commit and the run tree, so from the freeze until the look (projected 2027-03-07) any
  later commit to those packages blocks `run` unless it is reverted or the run is done from the freeze tree. That is the rule's design;
  Devin should know it pins two live packages for four months.

## 6. Checks (worktree root, `/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python`, `import nba` -> this worktree; unpiped)

- `ruff check nba tests`: All checks passed, exit 0.
- `ruff format --check nba tests`: 1018 files already formatted, exit 0.
- `mypy nba/`: no issues in 404 source files, exit 0.
- `pytest --no-cov -q -p no:cacheprovider` on tests/export/{test_best_model, test_box_history, test_export, test_line_cal_fields,
  test_line_pairing, test_rung_cal_fields, test_scripts_stage, test_teammate_sits}, tests/jarvis/cards/{test_ingame_checkpoint_card,
  test_run}, tests/jarvis/{test_runner, test_ingame}, tests/markets/{test_checkpoint_fixes, test_checkpoint_scorer,
  test_checkpoint_snapshot, test_checkpoint_store}, tests/parlay/{test_combo_night, test_game_scripts, test_combo}, tests/test_layering.py,
  tests/test_leadtime.py: **418 passed, exit 0** (160 s). Not run: the full suite (game night, 8 GB), the rest of tests/jarvis.

## 7. Replay oracle

The stage is **outside** the replay closure: the static import closure (function-level imports included, `tests/test_layering.py`'s
walker) of `nba.daily.replay_season` + `nba.daily.canonical_dump` (149 modules) and of `nba.daily.__main__` (124) contains none of the
touched modules (`nba.export.*`, `nba.parlay.combo_night`, `combo_hist`, `game_scripts`, `nba.markets.checkpoint_*`,
`nba.jarvis.ingame_checkpoints`). The export runs as a subprocess of `nba.ops.app_export`, which the replay does not run. Expected
result: IDENTICAL `89889f6d`. It proves the daily path is untouched; it does not exercise this stage.

Command (main session, after logging its HOLDOUT_ACCESS_LOG row and after 22:15 CST), from a worktree of the merged tip with `data/`
symlinked to the main checkout's `data/` (verify with `ls -la` first):

```
python -m nba.daily.replay_season --season 2025 --max-dates 20 --no-lock \
  --source /Users/devin/Downloads/nba-prediction/data/nba.duckdb \
  --db ~/Downloads/nba-scratch-1117/oracle_gs.duckdb --out-dir ~/Downloads/nba-scratch-1117/oracle_gs_out
python -m nba.daily.canonical_dump --db ~/Downloads/nba-scratch-1117/oracle_gs.duckdb   # expect 89889f6d
```

The games-table `mu_t` for tonight (not a holdout read; season 2026 only), on copies:

```
python -m nba.parlay.combo_night --export <copy of data/export/latest/export.duckdb> --date 2026-11-17 --out <dir> \
  --joint-model <copy of data/parlay/joint_model.json> --history-db <copy of data/nba.duckdb>
```

## 8. What the adversary should attack first

1. Choice 1 (`made_at` = newest of all inputs): any consumer that reads `made_at` as "the prop run"; the 01:20Z rows below.
2. The validator inside the stage: a validation failure removes both files and with them the JARVIS snapshot's pre-game priors
   for that night (fail-soft, manifest says why). Is that the right failure mode, or should the picks file survive?
3. `hist` from the in-memory `box`: no season-2025 value may reach a count (Python filter on `season` and the id digits, plus SQL in
   the offline adapter); duplicates; DNPs; games in `box` but not in `history_rows` count in `n_all` without a script.
4. 10,000-sample `fallback_missing` rows in the payload; the 1/199 pass-through on them.
5. Tipped games kept in the payload (contract text changed from "never rebuilt after tip").
6. `history_rows` drift: the SQL pin test against the harness's text.

## 9. Not verified

- The games-table `mu_t` on a real night (needs a copy of `nba.duckdb`; not opened here); equality with the harness's `as_of_mu_t` on
  real data.
- A full `build_export` run on a real database copy (only the synthetic fixture and the offline rebuild on an export copy ran).
- The full test suite and the rest of tests/jarvis.
- **Anomaly, outside this lane:** the export copy (file last written about 01:06Z, meta `made_at` 01:05:03Z) holds `win_probs` and
  `p_play` rows of run `4f90046bd2d7` stamped `made_at` 01:20:09Z (`stamp_stamped_at` 01:20:19Z), after the file was written.
  The stage's known-at check is against tip, so it accepts them; the clock or stamping behind it was not investigated.
- How many null main lines a covering-run pairing would recover (section 3a).

## 10. Drafted rows (not appended)

DECISIONS (main session, at merge):
`| 2026-11-17 HH:MM | MERGED agent/market-game-scripts-2 (game scripts 1-3 + combo-night, one producer): combo_stage.build is the only
writer of combo_<date>.json; it runs combo_night.validate_payload and writes nothing on an error; hist (season-2026 k of n) from the
same games-table history rows as mu_t; block made_at = newest stamp of every input used; 10,000-sample grids used with their own n;
combo_night is the offline rebuild. history_rows SQL unchanged (pinned by a test); mu_t, mu_m and script priors equal the pre-merge
stage on tonight's export (596 entries). Outside the replay closure; oracle <result>. Merging turns on the app's checkpoint view. |
Devin's go 2026-11-17 (finish game scripts). | A combo file whose mu_t differs from GAME_SCRIPTS_FORWARD's recompute; a validation
failure on a normal night; any season-2025 value in hist. |`

Optional, Devin's call (not drafted as decided): pair `line_cal` through the newest covering run (section 3a).

No ledger row (no test of a model was run).
