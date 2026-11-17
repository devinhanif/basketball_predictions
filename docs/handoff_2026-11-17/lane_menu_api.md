# Lane C2a (menu API): honesty routes, report

Branch `agent/app-1117-menu-api`, worktree `/Users/devin/Downloads/nba-wt-1117-menu-api`, base `d3d8529c`. Started
Mon 2026-11-16 20:21 CST, report 20:58 CST (from `date`). Paths touched: `app/api/`, `app/error_codes.json`,
`docs/contracts/` (new files only), this report. No `nba/`, `configs/`, `ops/`, `app/web/` change; no push, no merge.

## Routes added (response models in `app/api/src/parlayhitters_api/honesty_models.py`)

| route | contract | size / time on the frozen export of 2026-11-16 (TestClient, warm) |
|---|---|---|
| `GET /api/record/{game_id}/{player_id}/{stat}` | `docs/contracts/record_v1.md` | 3,580 B, about 1 ms (first call 10 ms) |
| `GET /api/record/{game_id}` | same | 7,640 B, about 1 ms |
| `GET /api/distribution/{game_id}/{player_id}/{stat}` | `distribution_v1.md` | 2,209 B (locked finished prop 886 B), about 1 ms |
| `GET /api/scorecard?target=&stat=` | `scorecard_v1.md` | all 17,226 B, first 46 ms (bootstrap) then cached about 1 ms; `props&pts` 6,239 B |
| `GET /api/forward-tests` | `forward_tests_v1.md` | 9,456 B, about 1-2 ms |
| `GET /api/parlay-diagnostics/{date}?offset&limit` | `parlay_diagnostics_v1.md` | page of 100 55,816 B, about 6 ms; `limit=500` 275,571 B, 8 ms |
| `GET /api/errors` | `errors_v1.md` | 7,933 B, about 1 ms |
| `/api/health` + `newest_quote`, `stale_marks`, `strip_missing` | `health_strip_v1.md` | 2,486 B, about 1 ms |

Cold store load on the frozen export: 4.2-6.5 s across runs (whole store). The added capture costs about 0.07 s
(score sums in DuckDB) + 0.01 s (strip facts) + 0.8 s (record capture) and holds about 27 MB (7,172 primary rows with
their grids and survival tables, 56k snapshot tuples).

Payload shapes, trimmed:

```text
record:       {kind, model_name, version, weights_sha256(_short), weights_scope, fit_through, n_train, made_at, tipoff,
               lead_minutes, meets_t60, data_version, git_sha, git_dirty, stamped_at, primary_source, primary_detail,
               bundle, snapshots: [{made_at, lead_minutes, meets_t60, model_name, version, is_primary, ...}], missing}
distribution: {locked, taus[19], q_grid, mean, std, support: {q_grid, survival}, p_ge_full: {n, counts}, p_play: {value,
               source, detail}, proj_minutes, conditional: {p_play_used, p_ge_given_plays, p_ge_unconditional, ...} | null,
               kalshi_dnp_note, missing}
scorecard:    {score_rule: {rule: "latest_row", n_late_excluded, n_t60_unknown, limitation}, lock_from, market:
               {available: false}, targets: [{target, models: [{role, shown, n_scored, n_dates, n_dnp, *_locked, metrics:
               {crps: {value, lo, hi, n, n_dates}}}], comparisons: [{arm, comparator, shown, n_pairs, metrics: {crps: {...,
               call}}}], sentence}], not_available: [{metric, needs, why}]}
forward-tests:{version, as_of, count_rule, tests: [{id, source (file:line), scheduled_read, n_needed, arms, n_so_far,
               n_so_far_reason}]}
parlay-diag:  {n_records, engines, n_with_independent, n_with_copula, offset, limit, n_returned, records: [{engine, legs,
               joint_stored, joint_independent, joint_copula, shrunk_prob, model_weight, verdict, missing}]}
```

## Commits

1. `9f675986` record and distribution; export capture (`honesty_data.py`, `ExportStore` wiring); `no_game_forecast`.
2. `48f3ac36` scorecard and forward-test tracker (`scorecard.py`, `forward_tests.py`, `published/forward_tests_v1.json`).
3. `765a1a44` parlay diagnostics; `no_parlay_check`.
4. `3ccd54c2` `/api/errors`, health strip keys, sweeps (refused export is a typed 503 on every new store route; no new
   payload has a money key).
5. this report and the export-field proposal (docs only).

## Tests (run, unpiped, `cd app/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`)

Before: 533 collected (532 passed, 1 skipped, as briefed). After each commit's gate: 540, 551, 553, 558 passed, 1
skipped each time, exit 0. Final: 558 passed, 1 skipped in 162 s. New test files: `test_record.py`, `test_scorecard.py`,
`test_parlay_diag.py`, `test_system_extra.py` (33 tests), helper `honesty_helpers.py`, fixture `planted` in conftest.
Tests run on committed fixtures only; rows they need are planted in a private copy of `fixtures/export_small` (no
tracked binary changed).

Hostile input: every new path and query parameter is in `tests/hostile_gen.py`; total cases 9,246, of which 2,133 hit
the new routes (record prop 532, record game 178, distribution 532, scorecard 356, parlay-diagnostics 533, two
parameterless routes 1 each). Crashes: 0 (the suite asserts no 5xx except documented 503s, every refusal typed and
naming its field, nothing on disk changed).

## What `settled_scores` really is

`nba/export/reader.py:335-352` exports `forward_scores` (all of the season, `game_id NOT LIKE '001%'/'003%'`), which
`nba/daily/settle.py:88-154` fills from the **latest row per (game, model, target, player) regardless of lead**
(`row_number() ... ORDER BY made_at DESC`, line 96). The T-60 table is `forward_scores_elig` (settle.py:264, 340), not
exported. `status`: `scored` or `dnp` (the player has no box row with minutes > 0; `crps` NULL; settle.py:144-147). Prop `crps` is the
continuous 19-quantile CRPS or closed-form normal (settle.py:57-83), not integer-support. On the frozen export: 99,534
rows, 2026-10-20..11-15, 26 dates; e.g. production pts 4,354 scored and 2,215 dnp (a third DNP, as briefed).
Other facts asked for: production models `props_context_residual` (props) and `rung0_injury_elo` (winner); baselines
`props_recency_v1`, `rung0_mov_elo`; arms in the export `_int`, `_lt`, `_t15`, `_late` (no `_t30` rows), AVG
`props_avg_live2` (prop_grids only, all `freshness = stale`, reason `writer_flag`). `p_ge_full_counts`/`_n` = integer
survival P(Y >= k) = c_k / n, k = 1..K (nba/props/full_support.py:1-20, from_samples :42-49). Parlay records carry ONE
joint (`raw_model_prob`) from the named `engine` plus `model_prob` after market shrink; no independent value beside a
copula one; tonight all 1,355 are one-leg `marginal` (nba/export/parlay.py:1-14 passes the file through).
`p_ge` (given plays), `p_play` and `p_ge_uncond` (their product, written by the model) are all stored on production rows
(nba/props/forward.py:700-717), so the distribution route serves them; AVG rows lack them (null and named).

## Locks: how each is enforced and tested

* LINE_CAL_LIVE (`trackrecord.MODEL_LOCK_FROM` = 2026-10-30, the one constant): distribution of a finished game dated
  on or after it serves no number (`test_distribution_locks_a_finished_game_in_the_window`, by moving the date); the
  scorecard gives counts only for those dates (`test_locked_dates_give_counts_only`); record and snapshot lists never
  carry a forecast number (`test_prop_record_copies_the_primary_row`); constant identity tested in both modules.
* Frozen tests: `_int`, `_lt`, `_t30`, `_t15`, `_late` and both winner models: counts only, `withheld_reason` names
  the rule (`test_frozen_arms_give_counts_only`, `test_win_scores_are_the_frozen_tripwire`); forward-test tracker has
  no score/delta/verdict key (`test_forward_tests_cite_their_sources_and_carry_no_outcome`).
* Holdout: SQL `season IN manifest seasons AND season <> 2025 AND game_id LIKE '002%'`; a planted 2025 row and a
  preseason row (crps 999) are not read (`test_scorecard_never_reads_the_holdout_or_preseason`).
* Teammate-sits rules: untouched. Desk/money: no new route serves price, fees, EV, kelly, stake or balance
  (`test_new_routes_carry_no_money`, `test_parlay_diagnostics_serve_only_what_is_stored`).

## Decisions (what / why / what would reverse it)

1. Scores are only served for production vs `props_recency_v1`, pre-lock dates. Why: everything else is a frozen arm,
   the WIN tripwire, or inside LINE_CAL_LIVE. Reverse: a look happens (counts become scores for that arm only) or
   Devin rules production-vs-recency is itself under a frozen test.
2. Win models: counts only, both of them (even each model's own mean). Why: their difference is the frozen tripwire;
   two means would reveal it. Reverse: the tripwire's next look (L30), or Devin's call.
3. `_late` treated as frozen. Why: no frozen rule found, and the brief lists late arms as frozen subjects. Reverse: a
   rule that says it is not under test.
4. Distribution of a FINISHED game in the lock window withholds every number (upcoming games are served). Why: the page
   would sit beside the outcome. Reverse: the LINE_CAL_LIVE look, or Devin saying a distribution without its outcome
   is fine.
5. Rows whose tip-off the export lacks are kept in the means and counted (`n_t60_unknown`), late rows dropped. Why:
   excluding unknowns empties the card forever (tip-offs exist only for 7 days, all inside the lock); the payload
   labels the rule. Reverse: export #5/#6 of the proposal, then drop unknowns.
6. Ten or fewer dates: `call = too_few_dates` whatever the interval (FORWARD_PREREG_2026_27.md:333-334). Tonight's
   pts card has exactly 10 pre-lock dates, so no call. Reverse: none planned.
7. Bootstrap B = 2,000 at 95%, seed 2026 (the prereg's estimator, smaller B). Why: descriptive and cached; not a
   checkpoint. Reverse: a page that needs checkpoint-grade numbers reads the checkpoint, not this.
8. Regular season only (`002`). Why: the prereg population. Reverse: a page for cup/playoffs with its own rule.
9. Parlay diagnostics paged (default 100, max 500). Why: 746 KB unpaged. Reverse: the page wants a summary only.
10. `/api/errors` not store-backed, exempt from the refused-export sweep. Why: help must work when data is refused.
11. Two new codes (`no_game_forecast`, `no_parlay_check`), both 404, registry shape kept.

## not_available (named in payloads)

Integer-support CRPS, T-60 eligible scores, pinball19, PIT, q10-q90 coverage, market probability per scored row, CLV
(scorecard); market column (no stored market probability); independent and copula joints (no record stores them
tonight); odds capture status per book and a production-row stale flag (health strip). See
`docs/contracts/PROPOSAL_export_fields_2026-11-17.md` for each with its `nba/` source.

## Not verified

* `app/web` tests were not run (another lane owns it); the two new registry entries follow the shape its registry test
  checks (title/what/why/todo, no URL or markup) but that was read, not run.
* No live server (uvicorn) run; all measurements are in-process TestClient on the frozen export copy.
* Whether every book's identical newest `snapshot_ts` (2026-11-17T00:45:47Z) is the capture run's time: not checked
  in `nba/`; the label claims only "newest quote in this export".
* The INGAME_WINPROB_V2 read date and n were not found in the sources read; the entry says so.
* The scorecard numbers themselves were seen (pts production minus recency on 10 pre-lock dates) but are not claims:
  latest-row rule, continuous CRPS, too few dates.
