# distribution v1: the stored distribution of a shown prop (app/api, 2026-11-17)

`GET /api/distribution/{game_id}/{player_id}/{stat}`. Read-only. Response model `Distribution`
(`app/api/src/parlayhitters_api/honesty_models.py`); code in `record.py`.

Same row as `record_v1` (the shown primary prop). Every number is a stored field. A piece that is not stored is null
and named in `missing`; nothing is derived to fill it.

| field | source |
|---|---|
| `taus` | the 19 levels 0.05..0.95 (`forecast.TAUS`) |
| `q_grid`, `mean`, `std` | `prop_grids.q_grid`, `mean`, `std` |
| `support.q_grid` | `integer` when every stored quantile is a whole number, else `continuous` (read off the values) |
| `support.survival` | `integer` when `p_ge_full` is stored, else null |
| `p_ge_full` | `{n, counts, k_from: 1}`: P(stat >= k) = counts[k-1] / n, as stored (`p_ge_full_counts`, `p_ge_full_n`; nba/props/full_support.py:1-20) |
| `p_play` | `{value, source, detail}`: the chance to play the app shows (`p_play_primary`, `p_play_source`, p_play table detail) |
| `proj_minutes` | stored |
| `conditional` | only when ALL three are stored: `p_play_used` (the row's own `p_play`), `p_ge_given_plays` (`p_ge`), `p_ge_unconditional` (`p_ge_uncond` from `prediction_json`, which the model wrote as the product; nba/props/forward.py:700-717), plus `mean_given_plays` and `mean_unconditional`. Else null, with `conditional.<piece>` in `missing`. The app multiplies nothing |
| `kalshi_dnp_note` | text: a pre-tip withdrawal on Kalshi resolves at the last fair price, not as No (Kalshi BASKETBALLENTITYSTAT terms, docs/DECISIONS.md 2026-11-10 13:20) |
| `locked`, `lock_text` | LINE_CAL_LIVE display lock: a FINISHED game dated on or after `MODEL_LOCK_FROM` (2026-10-30, the constant in `trackrecord.py`, also used by `scenarios.py`) is served with every number null and `missing: ["withheld: finished game inside the locked test window"]`; model name, version and made_at stay |
| `available` | always true when the prop is shown; on the fixture replay the shown forecast's stored grid and mean are served and the rest is named missing |

Note: `conditional.p_play_used` is the chance to play the row was built with; it can differ from `p_play.value` (the
availability model R when stored). Both are stored; the page should label which is which.

Refusals: as `record_v1` (prop form).

Measured on the frozen export of 2026-11-16: 2,209 B for an upcoming prop, 886 B for a locked finished prop, about 1-2
ms warm.
