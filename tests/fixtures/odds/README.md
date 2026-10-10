Recorded responses for tests/odds (no network, no key inside).

* `*.json.gz` are the real cached probe responses (2026-10-10) copied from
  `data/odds/raw/theoddsapi_probe/`: `/me/`, `/sports/{key}`, the empty out-of-season `/props/`,
  and `/historical/odds` pages (flat rows; 2023 window is empty).
* `odds_live_synthetic.json`, `props_live_synthetic.json` are HAND-WRITTEN in the documented live
  shape (`books: [{book, market, updated_at, outcomes: [...]}]`), because the preseason feed was
  empty when this adapter was built. They are not recordings. Replace them with a recorded in-season
  response once one exists and re-check the parser against it.
