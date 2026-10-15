# top_parlays v2 (contract between nba/parlay, nba/export and app/)

Supersedes `top_parlays_v1.md` (written 2026-10-15; v2 the same day after the post-merge review
`docs/reviews/top_parlays_panel_postmerge_2026-10-15.md`). v2 is v1 plus the `gate` object, a documented required
`illustration` bool, timezone-aware times, and the invariants below. The app accepts `schema: "top_parlays/2"` only; v1 is
refused (nothing real ever used it). One JSON object per slate date, `reports/parlay/top_parlays_<date>.json`, written after
the pre-tip run. Read-only; paper only.

```json
{
  "schema": "top_parlays/2",
  "illustration": false,
  "slate_date": "2026-10-20",
  "made_at": "2026-10-20T22:10:00Z",
  "calibration": {"rule": "LINE_CALIBRATION", "sha8": "a5441242", "fit_through": "2025-06-30 (season 2024; season 2025 rows only after a logged training-use row, Devin)", "status": "Stage 1 pass (T493); live confirmation pending"},
  "gate": {"settled": 0, "settled_min": 30, "slate_dates": 0, "dates_min": 14, "skill_vs_market": null, "open": false},
  "ranking": "calibrated joint probability, highest first; ties broken by higher EV",
  "verdict": "keep_your_money",
  "empty_reason": null,
  "sizes": {"2": [ticket, ticket, ticket], "3": [ticket, ticket, ticket]},
  "caveats": ["Two-leg parlays of alternate-line overs the raw model put at 70% or more (BetRivers) hit 9.8 points less often than the raw model said (95% CI 6.8 to 12.9, n 3,337 parlays, date-clustered, 2023-24 and 2024-25; T490). Three-leg: 8.7 points (5.5 to 11.9, n 3,360). These were raw chances; the chances shown here are from calibrated legs."]
}
```

ticket and leg are exactly as in v1 (rank, same_game, legs[], p_joint_raw, p_joint_cal, p_joint_cal_80, book_implied,
ev_per_dollar, ev_low80, why; leg: game_id, tipoff, player_id, player_name, team, opp, stat, side, line, book,
price_american, price_at, p_raw, p_cal, model_source, reason).

## Fields added or tightened

- `illustration` (bool, required, no default): true means every player, line, price and probability is synthetic. The app
  labels the panel and every ticket ILLUSTRATION and hides breakdown links. An illustration file uses dates and game ids that
  no real store uses.
- `gate`: the thresholds are fixed values of the frozen gate, not parameters: `settled_min` must be 30 and `dates_min` must be 14, and the app refuses any other value (the file never sets its own bar). `settled` (n settled paper rows), `settled_min` (30), `slate_dates` (n dates), `dates_min` (14),
  `skill_vs_market` (number or null; model minus market skill, positive is better), `open` (bool). `open` must equal
  `settled >= settled_min and slate_dates >= dates_min and skill_vs_market > 0`; the app refuses a file where it does not.
- `verdict`: `keep_your_money` or `positive_ev_found`. The app refuses `positive_ev_found` unless `illustration` is false,
  `gate.open` is true and some ticket has `ev_low80 > 0`.
- Times (`made_at`, `tipoff`, `price_at`) are ISO 8601 with a timezone. Refused: `price_at >= tipoff` on any leg, and
  `made_at` at or after the earliest `tipoff`.
- `sizes` must contain both keys "2" and "3" (an empty list is allowed). `price_at` must not be later than `made_at`.
- Shape: ticket leg count equals its size key; at most 3 tickets per size; ranks are 1..k in order; `p_joint_cal_80` has
  lo <= p_joint_cal <= hi; `ev_low80 <= ev_per_dollar`; for `same_game: false`, `p_joint_cal` is within 0.05 of the product
  of the legs' `p_cal`; `price_american` is not in (-100, 100).
- Free text (`why`, `reason`, `caveats`, `calibration.status`, `ranking`, `empty_reason`) is screened by the producer's numguard and, as a second line,
  by the app for the banned phrases (CS 3): lock(s)/locked, guarantee(d), free money, can't/cannot lose (straight or curly apostrophe), hammer, best bet(s), easy money, sure thing, fade the public; case-insensitive.

## Optional additions (producer, still `top_parlays/2`)

- `legs[].tail_flag` (bool, optional): true when the leg's `p_cal` is 0.65 or more. Display only; no ranking or number depends on it.
- `tickets[].tail_note` (string, optional): present when any leg is flagged: "This ticket uses legs the model rates 65%+; in 2023-24 such
  legs ran overconfident (T495), most of them threes", with "(threes-heavy)" appended when at least half the legs are fg3m. Screened
  by numguard and the banned-phrase list like every free-text field.
- The T495 caveat is the second entry of `caveats` (default applied by the main session; Devin may change it).

## Meaning of the numbers

`p_joint_cal` is the product (or copula joint) of legs that were each calibrated (T493, single legs at the line). The joint
itself, and its 80% range, are not yet tested; the app labels them "legs calibrated; joint not yet tested". The T490 caveat above
is the producer's wording; source `reports/alt_parlay_x/results.json` cells `ge70_size2/T1`, `ge70_size3/T1`.
`book_implied` is a product of the legs' prices, not a tradable price.

Residual: the gate counters (`settled`, `slate_dates`, `skill_vs_market`) are producer-asserted; the app cannot check them against `paper_trades`.
