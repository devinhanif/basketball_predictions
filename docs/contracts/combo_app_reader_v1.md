# combo mode: what the app reads beyond combo_export_v1

Reader notes for `docs/contracts/combo_export_v1.md` (the payload contract is unchanged). Written by the app lane, 2026-11-17.

## Route

`GET /api/combo` answers `{"nights": ["YYYY-MM-DD", ...]}` (oldest first): the nights that have a `combo_<date>.json` beside the
export manifest (`APP_EXPORT_DIR`) or, in fixture mode, in `app/fixtures/combo`. `GET /api/combo/{date}` answers
`{"mode": "export" | "fixture_replay", "clock_utc": "<ISO>", "combo": <the file exactly as stored>}`. `clock_utc` is the server's real
clock in export mode and the replay slate's frozen clock in fixture mode; the browser closes any game whose `tip_utc` is at or before it
(combo mode is for games that have not tipped). An absent file is a 404 with code `no_combo` (never a sample); a file that fails
validation is a 422 `combo_refused`; an unreadable directory is a 503 `combo_unavailable`. The file is NOT one of the manifest's
checksummed files; the API validates it instead (schema name, night, shapes, `made_at` before `tip_utc`, `surv` nonincreasing in [0, 1],
and no key anywhere whose words include price, odds, fee, stake, ev, edge, kelly, vig, ask, bid, payout, wager, juice, cost or bankroll).
Unknown keys pass through unchanged (additive evolution).

## Optional keys the app reads (all tolerated absent)

- stat entry `hist`: `{"n_all", "k_all", "<script>": [n, k], ...}`, this season only: `n` = games in which the script happened and the
  player played, `k` = games among those with the stat over tonight's main line; `n_all`, `k_all` the same over all games. Shown as
  "In blowouts this season: over 24.5 in 2 of 3 games. All games this season: 7 of 12." Under = n - k. A pair with k > n, a missing
  script or any extra key is ignored. Never used in a probability or an ordering.
- stat entry `main_line_open`: where the posted main line opened (a number). Shown as "line opened 23.5, now 24.5". No price, no reading.
- top level `fixture`: `{"label"}`, shown as a banner (the fixture night only).
- `shared.scripts.history`: `{script: {"n_games", "n_occurred"}}` this season; shown beside the prior when present.

## Suggested contract changes

1. Name these keys in combo_export_v1 so the engine and the app agree (the engine lane's `hist` shape is what is read here).
2. The app needs only `cal_k` and `surv` for the calibrated-only rule; `raw_at_main` is read by the tested-range rule in the engine port and
   never displayed. If the payload ever drops it, the port treats the leg as inside the tested range on the calibrated chance alone.
3. Per-script realised shares (2023 and 2024) are constants in `web/src/combo/script.ts` quoted from section 7; if the payload carried
   them with n per season the constants could go.
