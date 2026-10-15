# Code review: export best-evidenced model (agent/steward-export-best-model, f706f55)

Scope: `git diff main...origin/agent/steward-export-best-model` (nba/export/best_model.py new, reader.py,
build.py, tests/export/test_best_model.py). Purpose: DECISIONS 2026-10-14 21:30 (9b45634). Code review,
not a model attack: no scores, no CIs; no season 2025 read (fixture DB only).

Verdict: BLOCKED

Top finding: the export marks `is_primary` correctly, but the only consumer
(`app/api/src/parlayhitters_api/export_store.py`) ignores it. Run against an export this branch builds,
the app still shows production v2 over a fresh AVG row and production P(play) over R. Where production is
missing it shows a **stale** AVG row. That is the reversal condition the decision names: "a fallback bug
showing a number from the wrong model".

## What was run (all exit 0)

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Branch tests | `cd nba-wt-export-best && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:cacheprovider tests/export/` | 45 passed; best_model.py 96% line coverage |
| 2 | Which row the app shows | scratch `app_pick.py`: builds the fixture DB from the branch tests, runs `build_export`, then feeds `prop_grids` to `ExportStore._latest_props` | see table below; **FAIL** |
| 3 | Live path imports | `grep -rn "nba.export" nba/daily ops` | only `nba/ops/app_export.py` (the separate launchd app-export job). nba/daily never imports it. **PASS** |
| 4 | Read-only | reader.py `open_read_only` (`read_only=True`); best_model.py only issues SELECTs on the passed connection | **PASS** |
| 5 | Season guard | `guard.season_sql` in both new SQLs; `_assert_allowed` on AVG games; test with a 2025 AVG row and a 2025 R row inside the window | **PASS** (pplay has no Python-side belt, minor 6) |

Probe output (check 2; game 0022600050, pts):

```
fresh AVG + prod     pid=1000 app_shows=props_context_residual  p_play_shown=0.9  | is_primary=props_avg_live2 p_play_primary=0.97
stale AVG, no prod   pid=1001 app_shows=props_avg_live2 (stale) p_play_shown=None | is_primary=None
```

## Hunt items

1. **Wrong model or post-tip/stale number as primary.** Inside the export: post-tip AVG rows (`made_at >= tipoff_utc`)
   and post-tip R rows are dropped. Postseason (004/005) always goes to v2. Stale-bundle logic matches the writer
   (`bundle.select_bundle`) and the scorer (`nba/eval/avg_shadow_live.py:309-319`) test for test. Timezone is
   consistent: the writer keys on the ET run date, and the export keys on `games.game_date` (ET) or on
   `et_date(tipoff_utc)` when the games row is absent. A game on Oct 31 at 22:30 ET (Nov 1 UTC) therefore needs the
   October block, as the writer serves it. Remaining gaps: the app (must-fix 1), and an AVG row older than the
   latest production row (must-fix 2).
2. **pmf to quantile / p_ge.** `avg_pmf199` is 199 equal-mass integer values (store.py DDL; `grids.build_arms`),
   not a pmf over 0..198. The export treats it as samples, which is correct. `p_ge` is `mean(s > t - 0.5)`, the same
   expression as production `_ctx_row` (forward.py:697). On integers it equals P(X >= t). Thresholds:
   `THRESHOLDS` = `ForwardConfig().thresholds`. `p_ge_full` uses the same `full_support.from_samples`. q10/q50/q90 =
   q19[1]/[9]/[17], taus 0.10/0.50/0.90 on both sides. Two differences, both minor: AVG `q_grid` is integer while v2
   is continuous (rounded 4 dp). AVG `mean` is the grid mean, while v2 `mean` is the model's point mean, not
   `mean(q199)`. Both are conditional on playing.
3. **Season guard.** Intact (check 5).
4. **Read-only, not in the live path.** Intact (checks 3-4).
5. **Vacuous tests.** None are vacuous at the unit level; the stale and post-tip cases are real negatives. Two gaps:
   no test reaches the consumer (this is how finding 1 got through), and
   `test_pplay_r_primary_else_production` asserts the questionable out_on_report behaviour (should-fix 3).
   The month boundary with the `et_date` fallback is also untested (minor 4).

## Must-fix

1. **The app ignores `is_primary` and `p_play_primary`.** `export_store.py:439-454` (`_latest_props`) ranks by
   `model_name == PREFERRED_PROP_MODEL` ("props_context_residual", line 53). `export_store.py:494` shows
   `r["p_play"]`. Fix, in two parts:
   - (a) In this branch, `best_model.avg_rows`: set `"comparison_only": True` on AVG rows, and in `mark_primary`
     set `chosen["comparison_only"] = False` only when an AVG row is chosen. The current app then never shows a
     non-primary (stale or postseason) AVG row.
   - (b) Before the decision counts as live (the same merge or an earlier one): `_latest_props` keeps only rows
     where `is_primary` is true when meta `best_model_schema >= 1`, and the forecast shows `p_play_primary`, plus
     an app test on a built export.

   Until (b) lands, the app shows v2 everywhere, which is safe but is not the decision.
2. **An AVG row older than production can be primary.** Pretip runs several times a day (launchd :20 / :50). Both
   writers are write-on-change. If the AVG step fails or skips in a later run while v2 updates (a new injury
   report), `mark_primary` (best_model.py, `elif avg["freshness"] == "fresh"`) still picks the older AVG row. That row
   was built on a superseded A0 grid and on earlier report information. Fix: choose `avg` only when
   `forward_avg_heartbeat` has `status = 'ok'` for the chosen production row's `run_id`, or with
   `started_at >= prod["made_at"]`. Otherwise fall back as `fallback_stale` with reason `avg_run_behind_production`.
   Add a test for it.

## Should-fix (Devin's call: it reads the decision text, not a code bug)

3. **R out_on_report shows production P(play).** When R's newest row has `r_reason = 'out_on_report'`,
   `pplay_rows` shows `p_play_prod`, the production hurdle value (0.4 in the test). The player is OUT on the official
   report R read. Showing p_play_primary = 0 with source `out_on_report` would be honest. As written, it follows
   the decision literally.

## Minor

4. Add a test where the games row is absent and the tip is on 2026-11-01 03:30 UTC (Oct 31 ET), with a
   `2026-10` bundle. Expect fresh.
5. Post-tip gates disagree: production rows are kept when `made_at == tipoff` (reader.py `made_at > tipoff`), while
   AVG and R drop them (`>=`). The writers refuse both, so the path is dead. Make reader.py `>=` in a later change.
6. `pplay_rows` relies on the SQL season filter only. Add `_assert_allowed` over the resolved season, as `avg_rows`
   does.
7. AVG `prediction_json` lacks `mean_uncond`, `p_ge_uncond` and `dist_family`, which v2 rows carry. No current
   consumer reads them from the export (grep). Note it in the docstring.

## Playbook check

Model class: none trained here. This is display routing between a quantile/distribution layer (AVG mixture of
integer grids) and the LightGBM context residual, plus LightGBM P(play) R.
- Information or capacity: no new information. The risk is routing staleness (must-fix 2).
- Enough data: n/a, no fit.
- Test validity: fixture tests are valid for the export. They do not test the consumer, which is where it fails.
- Lineage and inherited guards: the writer's post-tip refusal, stale flag and season guard are re-checked on read.
  The guards are intact.
- Scored against: unchanged. FORWARD_PREREG_2026_27 and AVG_SHADOW_LIVE_2 score as frozen, and the export does not
  touch scoring.

## Not verified

The live DB contents: no `forward_avg_shadow` rows exist until the bundle is installed. The real pretip
cadence and how often AVG skips while v2 updates. The front-end (React) layer beyond `export_store.py`.

Drafted rows: none (code review; no ledger claim).
