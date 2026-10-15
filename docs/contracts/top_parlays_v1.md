# top_parlays v1 (contract between nba/parlay, nba/export and app/)

> Superseded by `top_parlays_v2.md` (2026-10-15). The app accepts `top_parlays/2` only.

Written 2026-10-15 for Devin's request: top 3 most confident two-leg and three-leg parlays, with a plain-language reason.
One JSON object per slate date, `reports/parlay/top_parlays_<date>.json`, written after the pre-tip run. Read-only; paper only.

```json
{
  "schema": "top_parlays/1",
  "slate_date": "2026-10-20",
  "made_at": "2026-10-20T22:10:00Z",
  "calibration": {"rule": "LINE_CALIBRATION", "sha8": "a5441242", "fit_through": "2025-06-30 (season 2024; season 2025 rows only after a logged training-use row, Devin)", "status": "Stage 1 pass (T493); live confirmation pending"},
  "ranking": "calibrated joint probability, highest first; ties broken by higher EV",
  "verdict": "keep_your_money",
  "empty_reason": null,
  "sizes": {
    "2": [ticket, ticket, ticket],
    "3": [ticket, ticket, ticket]
  },
  "caveats": ["Parlays the model called 70%+ hit about 10 points less in 2023-24 (T490).", "..."]
}
```

ticket:
```json
{
  "rank": 1,
  "same_game": false,
  "legs": [
    {"game_id": "...", "tipoff": "...", "player_id": 1, "player_name": "...", "team": "BOS", "opp": "NYK",
     "stat": "reb", "side": "under", "line": 8.5, "book": "draftkings", "price_american": -125, "price_at": "...",
     "p_raw": 0.71, "p_cal": 0.66, "model_source": "AVG|production", "reason": "one sentence, numbers only from this leg's fields"}
  ],
  "p_joint_raw": 0.49, "p_joint_cal": 0.43, "p_joint_cal_80": [0.38, 0.48],
  "book_implied": 0.41, "ev_per_dollar": -0.04, "ev_low80": -0.12,
  "why": "two or three plain sentences; every number must pass nba/parlay/assistant/numguard.py"
}
```
Rules: only legs with a pre-tip price stamped before tip; same-game legs priced with the copula, cross-game independent;
fees from the verified fee config; `verdict` is `keep_your_money` unless the frozen gate (30 settled, 14 dates, skill > 0) is
open AND ev_low80 > 0. Empty sizes are allowed (`empty_reason`: "no slate", "no priced legs", ...).
