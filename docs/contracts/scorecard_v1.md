# scorecard v1: means of stored scores, with n and a date-clustered interval (app/api, 2026-11-17)

`GET /api/scorecard?target=props|win&stat=pts|reb|ast|fg3m` (both optional; `stat` with `target=win` is a 422
`bad_request` naming `stat`). Read-only. Response model `Scorecard`; code in `honesty_data.load_scores`,
`scorecard.py`, `routes/scores.py`.

## What the scores are (read before showing any number)

* `settled_scores` in the export is `forward_scores` from `nba.duckdb` (nba/export/reader.py:335-352), which
  `nba.daily.settle.settle_with_refusals` fills from the **latest row per (game, model, target, player) regardless of
  lead** (nba/daily/settle.py:88-120, insert at 154). It is not the pre-registered T-60 table `forward_scores_elig`
  (nba/daily/settle.py:264, 340), which the export does not carry. The payload says so: `score_rule.rule =
  "latest_row"`.
* T-60 filter, where possible: each row's stored `made_at` is compared with the game's stored tip-off (max over the
  export's `games`, `prop_grids` and `win_probs` tip-offs). Made after tip-off minus 60 min: dropped from every mean and
  counted (`n_late_excluded`). No tip-off in the export (the export holds tip-offs only for its 7-day lookback):
  kept, counted (`n_t60_unknown`) and labelled.
* `crps` is what settle stored: the continuous 19-quantile CRPS, or the closed-form normal when a row has no grid
  (nba/daily/settle.py:57-83). Not the integer-support CRPS the forward tests use. `log_loss` and `brier` for game
  winners. `status = 'dnp'` rows (about a third of primary prop rows) have no score and are counted as `n_dnp`.
* Population: `season IN manifest.seasons` minus 2025, and `game_id LIKE '002%'` (regular season), in SQL. Season
  2025 (the holdout) is never read; a test plants a 2025 row and a preseason row and shows neither is read.

## What may be shown (decided in `scorecard.py`)

| rows | served |
|---|---|
| `props_context_residual` (production) and `props_recency_v1` (declared baseline), slates before 2026-10-30 | mean, lo, hi, n, n_dates; and the paired difference production minus baseline |
| any slate dated on or after `MODEL_LOCK_FROM` (2026-10-30; LINE_CAL_LIVE, same constant as `trackrecord.py` / `scenarios.py`) | counts only: `n_scored_locked`, `n_dates_locked`, `n_dnp_locked`, `n_pairs_locked` |
| `_int`, `_lt`, `_t30` (FORWARD_PREREG_2026_27 section 0), `_t15` (FORWARD_T15_2026_27), `_late` (no frozen rule found; withheld as if frozen), and both game-winner models (the WIN tripwire, FORWARD_PREREG section 7) | counts only, `shown: false`, `withheld_reason` names the rule |
| any model not declared | counts only |

Interval: percentile bootstrap over whole game dates, ratio of sums (docs/FORWARD_PREREG_2026_27.md:281-285's
estimator), 2,000 resamples, 95%, `random.Random(2026)` re-seeded per series; no interval with fewer than 2 dates.
`call` (comparisons only): `better` (interval below 0; lower is better), `worse` (above 0), `same` (includes 0),
`too_few_dates` when 10 or fewer dates (FORWARD_PREREG_2026_27.md:333-334: "ten or fewer dates are never decisive"),
`no_interval`. Each target has one plain `sentence` saying where the baseline wins or that no call is possible, and that
whether the market wins cannot be said (no market probability is stored for scored rows).

`market`: `{available: false, reason}`. `not_available[]`: integer-support CRPS, T-60 eligible scores, pinball19, PIT,
q10-q90 coverage, market probability per scored row, CLV, each with the export field it would need.

## Shape (trimmed; frozen export of 2026-11-16)

```json
{"available": true, "seasons": [2026], "score_rule": {"rule": "latest_row", "n_rows_read": 99534,
  "n_late_excluded": 8340, "n_t60_unknown": 48292, "limitation": "..."},
 "lock_from": "2026-10-30", "market": {"available": false, "reason": "..."},
 "targets": [{"target": "pts",
   "models": [{"model_name": "props_context_residual", "role": "production", "shown": true, "n_scored": 1572,
               "n_dates": 10, "n_dnp": 933, "n_scored_locked": 2782, "n_dates_locked": 16,
               "metrics": {"crps": {"value": "...", "lo": "...", "hi": "...", "n": 1572, "n_dates": 10}}},
              {"model_name": "props_context_residual_lt", "role": "frozen_arm", "shown": false,
               "withheld_reason": "subject of a frozen forward test: ...", "metrics": {}, "n_scored": 1416}],
   "comparisons": [{"arm": "props_context_residual", "comparator": "props_recency_v1", "shown": true,
                    "n_pairs": 1572, "metrics": {"crps": {"call": "too_few_dates", "...": "..."}}}],
   "sentence": "pts: no call: ten or fewer dates are never decisive. ..."}],
 "not_available": [{"metric": "pinball19", "needs": "forward_scores_elig.pinball19", "why": "not exported"}]}
```

Measured on that export: all targets 17,226 B, first call 59 ms (bootstrap), then cached per store and query (about
2 ms); `target=props&stat=pts` 6,239 B; `target=win` 4,409 B.
