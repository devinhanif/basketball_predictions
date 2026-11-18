# forward_tests v1: the frozen forward tests, when they read, and n so far (app/api, 2026-11-17)

`GET /api/forward-tests`. Read-only. Response model `ForwardTests`; code in `forward_tests.py`; the list is the
committed file `app/api/src/parlayhitters_api/published/forward_tests_v1.json` (version 1, `as_of` 2026-11-17).

**Never an outcome.** No score, difference, verdict or better/worse word appears on this route (a test asserts it).

## Entries (each `source` cites file:line in the main checkout, read only)

| id | scheduled read / n needed from | arms counted |
|---|---|---|
| FORWARD_PREREG_T30 | docs/FORWARD_PREREG_2026_27.md:22, 177-185, 190-200 | `props_context_residual_t30` |
| FORWARD_PREREG_INT | docs/FORWARD_PREREG_2026_27.md:23, 140-158, 198 | `props_context_residual_int` |
| FORWARD_PREREG_LT | docs/FORWARD_PREREG_2026_27.md:24, 160-173, 199 | `props_context_residual_lt` |
| FORWARD_PREREG_WIN | docs/FORWARD_PREREG_2026_27.md:25, 249-261, 200 | `rung0_injury_elo`, `rung0_mov_elo` |
| FORWARD_T15 | docs/prereg/FORWARD_T15_2026_27.md:3, 83-91 | `props_context_residual_t15` |
| LINE_CAL_LIVE | docs/prereg/LINE_CAL_LIVE.md:3, 63-70; nba_project_reference.md:135 | none in settled_scores |
| BEST_PRICE_FORWARD | docs/prereg/BEST_PRICE_FORWARD.md:3, 62-67, 153-154, 169-170; reference :136 | none |
| LINE_MOVE_TOWARD_MODEL | docs/prereg/LINE_MOVE_TOWARD_MODEL.md:3, 53-54; reference :137 | none |
| FAV_DIP_PAPER | docs/prereg/FAV_DIP_PAPER.md:3, 50-51; reference :138 | none |
| STAR_FLOOR_PAPER | docs/prereg/STAR_FLOOR_PAPER.md:3, 49-50; reference :139 | none |
| INGAME_WINPROB_V2 | reference :140; docs/TEST_LEDGER.md:762 (no read date found) | none |
| AVG_SHADOW_LIVE (/_2) | docs/prereg/AVG_SHADOW_LIVE.md:3, 12; AVG_SHADOW_LIVE_2.md:3, 57; reference :141 | `props_avg_live2` (no settled rows in the export) |
| FULL_SHADOW_LIVE | docs/prereg/FULL_SHADOW_LIVE_2026_27.md:3, 11; reference :142 | none |
| COMBO_STOCKS_SHADOW | docs/prereg/COMBO_STOCKS_SHADOW.md:3, 21; reference :143 | none |
| PPLAY_INTO_PROPS_LIVE | docs/prereg/PPLAY_INTO_PROPS_LIVE.md:3, 11 | none |
| GAME_SCRIPTS_FORWARD | reference :144; docs/DECISIONS.md 2026-11-10 16:29 (not frozen) | none |

"reference" = `/Users/devin/Downloads/forecast-studio-ui-models/nba_project_reference.md`, section 3.3 (lines 131-144).

## n_so_far

For an entry with arms: `{arm: {scored, dnp, dates}}` counted from `settled_scores` (all of the export's forward
seasons, regular season, locked dates included: counts are allowed). `scored` counts every scored row (T-60 late rows
included), `dates` the dates with a scored row. This is **not** the test's own paired, eligible count, which only its
checkpoint states; `count_rule` in the payload says so. No arms, or no table: `n_so_far: null` with
`n_so_far_reason`.

Shape: `{version, source, as_of, note, count_rule, tests: [{id, title, source, as_of, scheduled_read, n_needed, arms,
n_so_far, n_so_far_reason}]}`. Measured on the frozen export of 2026-11-16: 9,456 B, about 2 ms.
