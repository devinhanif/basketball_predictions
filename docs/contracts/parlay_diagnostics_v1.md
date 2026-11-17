# parlay_diagnostics v1: the engine's stored joint chances, as stored (app/api, 2026-11-17)

`GET /api/parlay-diagnostics/{date}?offset=0&limit=100` (`offset` 0..100000, `limit` 1..500). Read-only. Response
model `ParlayDiagnostics`; code in `parlay_diag.py`, `routes/parlay_diag.py`.

## What the file holds

`parlay_of_day.json` is the engine's `reports/parlay/evaluate_<date>.json`, copied verbatim (nba/export/parlay.py:1-14,
28-49). Each record names one `engine` and carries ONE joint chance, `raw_model_prob`, from that engine, plus
`model_prob` (after shrinking toward the market by `model_weight`). **No record stores an independent-legs chance
beside a copula chance.** On the frozen export of 2026-11-16 all 1,355 records are one-leg `engine: "marginal"`
contracts, so neither joint is stored for any of them.

## Fields

| field | source |
|---|---|
| `joint_stored`, `joint_stored_field` | `raw_model_prob`, as stored |
| `joint_independent` | `raw_model_prob` only when the record's engine is `independence`; else null and named in `missing` |
| `joint_copula` | `raw_model_prob` only when the engine is `gaussian_copula` or `t_copula`; else null and named |
| `shrunk_prob`, `model_weight`, `verdict`, `ticker`, `title`, `engine`, `legs[]` (`game_id`, `player_id`, `stat`, `side`, `threshold`), `n_legs` | as stored |
| `engines`, `n_with_independent`, `n_with_copula`, `n_records` | counts over all records; `records` is the page `[offset, offset + limit)`, `n_returned` its length |

Nothing is multiplied. **Not served:** `price`, `fees`, `ev`, `ev_low`, `raw_ev`, `raw_ev_low`, `kelly`, `spread`,
`order_type` (no money, stake or expected value on this route; a test asserts none of those keys appears). A record
whose fields have the wrong type serves nulls, never a 500.

Refusals: `not_a_date` (404, `date`), `no_parlay_check` (404, `date`; new code) when the date is not the export's
parlay date (the export holds one night), `bad_request` (422) for offset/limit out of range, `export_refused` (503).
On the fixture replay a stored slate date answers `available: false` with a reason.

Measured on the frozen export of 2026-11-16: default page (100) 55,816 B, about 8 ms; `limit=500` 275,571 B, about 12
ms. Unpaged (an earlier draft) was 746,013 B, which is why the route pages.
