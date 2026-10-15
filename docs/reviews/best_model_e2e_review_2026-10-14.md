# End-to-end re-review: the app shows the best-evidenced model (export a0cd303 + app 382b511)

Scope: origin/agent/steward-export-best-model at a0cd303 (nba/export/) and origin/agent/app-primary-model at
382b511 (app/ only), against DECISIONS 2026-10-14 21:30 (9b45634). This re-reviews
`export_best_model_review_2026-10-14.md` (BLOCKED f706f55). It is a code review, so it has no scores and no CIs.
No season-2025 data was read: every probe ran on synthetic scratch source DBs (season 2024). The planted
`00225…` ids are guard tests.

Verdict: BLOCKED

Top finding: **P(play) can be stale.** `forward_pplay_shadow` is write-on-change and runs in its own
try/except after the primary rows. Take a run where production updates a player's P(play) (for example a later
report rules him out) while the R step fails. The export then takes the older shadow row, and the app shows it as
the authority. Probe `pplay_stale`: the production row at T-10 says p_play 0.0, yet the app still shows R 0.93-0.96
and "production" 0.97 from the T-31 shadow row, for 44 props in the game. Before this branch, the app showed the
row's own (fresh) p_play, so this is a regression.

## Checks run (all on scratch worktrees; main checkout untouched)

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Export tests | `cd wt_export && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/export/` | 49 passed, exit 0. With coverage on, the repo-wide 70% floor fails a subset run (exit 1), which is expected |
| 2 | App tests on a clean checkout of 382b511 | `cd wt_app/app/api && NBA_PYTHON=…/.venv/bin/python uv run pytest -q -p no:cacheprovider` | **184 passed, 7 errors, exit 1**. Every `test_best_model_real.py` test errors with `export.duckdb is missing` (must-fix 2) |
| 3 | Same, after rebuilding the fixture | `python app/fixtures/build_export_best_real.py --repo wt_export`, then the same pytest | 191 passed, exit 0. The rebuilt manifest differs only in made_at and the sha |
| 4 | E2E probe, real exporter, then real `ExportStore` | `cd wt_app/app/api && uv run python scratchpad/probe/e2e.py <scratch>` (variants below) | see the table |
| 5 | Guard regex on ids | `scratchpad/regex_probe.py` (DuckDB `SEASON_SQL` vs `season_of_game_id`) | SQL and Python agree on all 18 ids. See check (3) |

E2E probe variants (the fixture scenario plus one planted change each):

| Variant | What the app shows | Pass |
|---|---|---|
| base | AVG 43 / fallback_missing 1 in the fresh game; stale 24 / missing 24 in the mixed game. One primary per triple; quotes all from `lines_run_id` | PASS |
| later production run, no heartbeat table | fresh game: `fallback_stale` / `avg_run_behind_production` (43) | PASS |
| later production run, heartbeat `failed` (AVG run `ok`) | same fallback | PASS |
| later production run, heartbeat `skipped` | same fallback | PASS |
| heartbeat `ok` only for other runs, or for `'r_c '` (trailing space) | same fallback | PASS |
| heartbeat `ok` for the production run | AVG; quotes from the production run (lines_run_id) | PASS (residual risk, should-fix 4) |
| later production run with p_play 0.0, shadow not rewritten | props fall back correctly, but **P(play) shows the old R / production value** | **FAIL** (must-fix 1) |
| AVG at tip and 1 min after tip, for the gap triple | dropped (`avg_post_tip` 2); triple stays `fallback_missing` | PASS |
| 2025 id with no games row; 2025 id with games.season 2025; 001; 003; `X022400023`; 9-digit id | none in any export table (`unresolved_or_disallowed_season` 91) | PASS |
| play-in 005 / playoff 004 with fresh AVG | production, source `postseason` | PASS |
| Cup final 006 with fresh AVG | AVG | minor 6 (the writer never serves 006) |
| AVG-only game with a 2024 id and no games row (reachable only since the regex change) | exporter writes a games row with NULL teams; **`ExportStore` raises TypeError and the whole app fails to load** | **FAIL** (must-fix 3) |

## (1) Earlier must-fixes

- MF1(a) comparison_only on non-primary AVG: **closed** (best_model.py:204, :333). The old app on a new export
  therefore never shows a stale or postseason AVG row.
- MF1(b) app keeps only `is_primary` rows and shows `p_play_primary`: **closed** (export_store.py `_latest_props`
  and `_pplay`), with consumer tests. The real-fixture half only runs if its DB is present (must-fix 2).
- MF2 AVG older than production: **closed as specified**, using the heartbeat for the production run_id. Verified
  that the AVG and production steps share `made_at` and `summary.run_id` (pipeline.py), so `>=` and the run_id
  match are sound.
- SF3 out_on_report shown as 0.0: closed. Minor 4 (month-boundary test) and minor 6 (pplay season belt): closed.
  Minor 5 (reader.py `made_at > tipoff` vs `>=`): **not done**, and the path is still dead. Minor 7 (docstring):
  not checked.

## (2) Can the app show a wrong number?
Non-primary: no. Postseason AVG: no. Post-tip: no. 2025: no. Prices from the wrong run: no (every posted line
shown is in `prop_lines` for `lines_run_id`; the AVG primary's `lines_run_id` equals the latest production
run). **Stale: yes, for P(play)** (must-fix 1).

## (3) Guard regex `00[0-9]{7}` -> `00[0-9]{8}`
The old pattern could never match a 10-character id, so every row without a games row was dropped. The new one
decodes it. DuckDB `SIMILAR TO` anchors the whole string: `00226000500` and `0022600050\n` give NULL. A 2025 id
decodes to 2025 and fails `IN (seasons)`, which `resolve_seasons` and `_assert_seasons`/`_assert_allowed` back
up. 001/003 are cut by `NOT LIKE` before decoding. Malformed ids give NULL and are excluded. **No 2025 path is
opened.** The new exposure is availability, not leakage: rows with no games row now reach the app with NULL teams
(must-fix 3). The same applies to production prop rows with no games row and no win-prob row (reader.py:203 fills
teams only from a win-prob `prediction_json`).

## (4) Heartbeat
A failed, skipped or missing AVG run is never treated as ok. One residual hole: `status = 'ok'` is run-level
(avg_shadow/run.py:319). A player dropped from an `ok` run (`no_full`, `no_a0`, `dropped_no_team`) keeps his
older AVG row as primary while production moved on (should-fix 4).

## (5) Old-export compatibility
An export without `best_model_schema` falls back to the old rule (strip test passes). The old app on a new export
shows production first, and an AVG primary only where no production row exists. Minor 5: the app tests `== "1"`
where the spec says `>= 1`, so a future schema "2" silently reverts to production-first (safe, but wrong).

## (6) Do tests reach the consumer?
Yes in design: `test_best_model_real.py` runs the real `ExportStore` on real exporter output. But the DB is
gitignored (root `.gitignore:4 *.duckdb`; `app/.gitignore` only re-includes `export_small`), so on a clean
checkout those 7 tests error. No consumer test covers P(play) staleness, NULL-team games or the heartbeat.

## Must-fix

1. **Stale P(play) (export).** best_model.py:339-346 (`mark_primary`) and `pplay_rows` (best_model.py:252-280,
   whose output is the `p_play` table the app treats as authority). Use a shadow row only when its
   `made_at >= ` the latest pre-tip production prop row's `made_at` for that (game, player), or when its
   `p_play_prod` equals that production row's `p_play` (write-on-change makes equal content mean "unchanged").
   Otherwise set `p_play_primary = ` the production row's `p_play`, `p_play_source = "production"`,
   `p_play_detail = "r_run_behind_production"`. Write the same reconciled value to the `p_play` table rows.
   Add an export test and an app real-fixture test: later production row with p_play 0.0, shadow unchanged,
   expect 0.0.
2. **Commit the real-exporter fixture (app).** Add `!fixtures/export_best_real/export.duckdb` to `app/.gitignore`
   (next to line 12) and commit the DB that matches `export_best_real/manifest.json`. Otherwise CI errors on 7
   tests.
3. **NULL-team games crash the app.** best_model.py:215-218 emits a game dict with `home_team`/`away_team` NULL
   when the games row is absent. Drop such AVG game dicts, and their rows, unless a production game dict with
   teams exists (reader.py:227 already prefers production). Count them in `excluded`. In the app,
   export_store.py:643-644: skip (and stub-note) games whose teams are NULL instead of `int(None)`. Add a test
   with an AVG-only 2024/2026 id and no games row.

## Should-fix / minor

4. Per-row freshness instead of run-level: the AVG row already stores `a0_q19`. Accept it when `a0_q19` equals
   the production row's `q_grid` (it was built on the current production grid). Otherwise fall back as
   `avg_run_behind_production`. This closes the hole in (4) without the heartbeat.
5. export_store.py:304: `str(meta.get("best_model_schema", "0")).isdigit() and int(...) >= 1`.
6. Mirror the writer: AVG may be primary only for `002` ids (avg_shadow/run.py:158 serves `REGULAR` only), so
   006 and any other prefix go to production.
7. reader.py production post-tip gate: `made_at >= tipoff` (carried from the earlier review).

## Playbook check
Model class: none trained. This is display routing between the AVG integer-grid mixture, the LightGBM context
residual, and LightGBM P(play) R.
- Information or capacity: no new information. The risk is routing staleness (must-fix 1, should-fix 4).
- Enough data: n/a, no fit.
- Test validity: the consumer tests are valid but do not run on a clean checkout (must-fix 2). The probes cover
  what the tests do not.
- Lineage and inherited guards: the writer's post-tip refusal, stale flag and season guard are re-checked on read
  and hold. The writer's 002-only serving is not mirrored (minor 6).
- Scored against: unchanged. FORWARD_PREREG_2026_27 and AVG_SHADOW_LIVE_2 score from their own tables, and the
  export does not touch scoring.

## Not verified
Live DB contents and real cadence (no AVG bundle installed yet). How often R fails while production updates. The
React layer beyond the API payload. Minor 7 of the earlier review.

Drafted rows: none (code review; no ledger claim).
