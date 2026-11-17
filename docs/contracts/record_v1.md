# record v1: where a shown forecast came from (app/api, 2026-11-17)

`GET /api/record/{game_id}/{player_id}/{stat}` (a player prop) and `GET /api/record/{game_id}` (the game winner).
Read-only. Response model `Record` (`app/api/src/parlayhitters_api/honesty_models.py`); code in `record.py` and
`routes/record.py`.

## Which row

The row the app already shows as primary; no second selection:

* prop: `ExportStore._latest_props` (the exporter's `is_primary` row, newest `made_at`), captured at store load by
  `honesty_data.capture_records`. The player must be listed in the game (same refusal as the card route otherwise).
* game winner: `ExportStore._win_primary` (latest, not comparison-only, primary first), the same rows `_latest_win`
  turns into the game's win probability.

## Fields (all copied from the stored row unless said)

| field | source |
|---|---|
| `kind` | `prop` or `game_winner` |
| `model_name`, `version` | `prop_grids` / `win_probs` |
| `weights_sha256`, `weights_sha256_short` (first 12), `weights_scope`, `fit_through`, `n_train`, `data_version`, `git_sha`, `git_dirty`, `stamped_at` | the row's `stamp_*` columns, which the exporter copies from `forward_model_stamps` (nba/export/reader.py:157-160, 224, 251) |
| `made_at`, `tipoff` | stored, ISO UTC with `Z` |
| `lead_minutes` | stored `lead_minutes` (tip-off minus made_at; nba/export/reader.py:247) |
| `meets_t60` | the only derived value: `lead_minutes >= 60` (the forward rule's cutoff, docs/FORWARD_PREREG_2026_27.md:37-39); null when no lead is stored |
| `primary_source`, `primary_detail` | prop only (nba/export/best_model.py) |
| `fallback_reason` | game winner only |
| `source_table`, `bundle` (`bundle_id`, `bundle_month`, `bundle_made_at`, `freshness`, `freshness_reason`) | prop only; `bundle` null when the row has no bundle id or month |
| `snapshots[]` | every stored row for the same prop (all models) or game: `made_at`, `lead_minutes`, `meets_t60`, `model_name`, `version`, `is_primary`, `comparison_only`, `is_latest`. **Never a forecast number** (no mean, grid, chance or p_home), so no lock can be broken through it |
| `missing[]` | names of stamp fields that are null (`stamp.<name>`), and `primary_source` when absent |
| `available`, `reason` | false with a reason on the fixture replay (it has no stamps or snapshots) |

## Refusals

`unknown_game` (404, field `game_id`), `unknown_stat` (404, `stat`), `no_forecast` (404, `player_id` or `stat`),
`no_game_forecast` (404, `game_id`; new code), `bad_request` (422) for a non-integer player id, `export_refused` (503).

## Example (frozen export of 2026-11-16, trimmed)

```json
{"kind": "prop", "game_id": "0022600245", "player_id": 1628369, "stat": "pts", "available": true,
 "model_name": "props_context_residual", "version": "ctxres-v1", "weights_sha256_short": "7c313751fe67",
 "fit_through": "2026-11-15", "n_train": 117297, "made_at": "2026-11-16T22:20:02Z", "tipoff": "2026-11-17T00:00:00Z",
 "lead_minutes": 99.95, "meets_t60": true, "primary_source": "fallback_stale",
 "primary_detail": "bundle stale: writer_flag", "snapshots": [{"made_at": "2026-11-16T14:00:16Z",
 "lead_minutes": 599.7, "meets_t60": true, "model_name": "props_avg_live2", "is_primary": false, "...": "..."}],
 "missing": []}
```

Measured on that export (TestClient, in-process): prop record 3,580 B, game record 7,640 B, about 1-3 ms warm.
