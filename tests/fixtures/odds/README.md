Recorded responses for tests/odds (no network, no key inside).

* `*.json.gz` are the real cached probe responses (2026-10-10) copied from
  `data/odds/raw/theoddsapi_probe/`: `/me/`, `/sports/{key}`, the empty out-of-season `/props/`,
  and `/historical/odds` pages (flat rows; 2023 window is empty).
* `odds_live_synthetic.json`, `props_live_synthetic.json` are HAND-WRITTEN in the documented live
  shape (`books: [{book, market, updated_at, outcomes: [...]}]`), because the preseason feed was
  empty when this adapter was built. They are not recordings. Replace them with a recorded in-season
  response once one exists and re-check the parser against it.

* `the_odds_api/hist_*.json.gz` are the real cached probe responses (2026-10-10) from
  the-odds-api.com `/v4/historical/...` (events lists, per-event props and game lines for
  2023-05-10, 2024-01-15, 2025-01-15). Each file is `{status, headers, body, params, ...}`; `params`
  never contains the API key. `hist_props_2024-01-15` is a real empty response (event already tipped).
