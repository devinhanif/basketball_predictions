# desk_export v1 — what the app's Jarvis screens read

_2026-11-08. Producer: `nba/export/desk.py` (market agent). Consumer: `app/` (app agent), through the API only.
Additive changes keep v1; a breaking change bumps `schema`._

## Files
- `data/export_private/desk.json`: the **private** cut (money fields filled).
- `data/export_private/desk_shared.json`: the **shared** cut. Every money field is `null` and `privacy` is `"shared"`.
  This is the file the shared claude.ai page bakes.
- Both are written atomically (temp name then `os.replace`). A failed build leaves the previous files untouched.

## Rules
- **Inputs are opened read-only**, with retries:
  - `data/jarvis/paper_book.duckdb`;
  - `data/jarvis/halftime_overs.duckdb`;
  - the newest `reports/forward_scorecard/*.json`;
  - `configs/jarvis.yaml` (balance only).

  Never open `nba.duckdb` for writing. Never take `data/ops/heavy.lock`. Never import or call the JARVIS runner.
- **Every number is copied or computed from a stored row.** Every rate carries `n`, an interval, and what the interval is
  clustered on. If there is too little data, the field is `null` and a `reason` string says why.
- **No text that was never stored.** The `sends` table keeps only a hash of each message's text. If no stored text exists,
  `alerts[].text` is `null` and the alert is described from its stored fields (kind, play ids, flag keys).
- **Times are ISO-8601 UTC with a `Z`.** ET wall-clock strings appear only in `*_et_label` fields.

## Shape
```jsonc
{
  "schema": 1,
  "privacy": "private" | "shared",
  "built_at": "…Z",
  "data_as_of": "…Z",                       // newest ts read across sources
  "sources": { "<name>": "<path, row count, newest ts>" },
  "status": {
    "kill_switch": { "on": bool, "since": "…Z"|null, "n": int|null, "mean_clv_cents": num|null, "note": str|null },
    "model_review": bool,                   // true while the kill switch is latched or the gate is closed
    "w_model": num|null                     // newest model weight from model_weights.jsonl
  },
  "balance": { "start_usd": num, "current_usd": num, "floor_usd": num } | null,   // null in shared
  "plays": [{
    "play_id": str, "bundle_id": str|null, "slate_date": "YYYY-MM-DD", "ts": "…Z", "game_id": str|null,
    "matchup": str|null, "tipoff": "…Z"|null, "source": str|null, "venue": str|null, "kind": str, "side": str,
    "player_id": int|null, "player_name": str|null, "team": str|null, "stat": str|null, "threshold": num|null,
    "leg_desc": str|null, "price_cents": num|null, "fee_cents": num|null, "fair_prob": num|null,
    "model_prob": num|null, "edge_cents": num|null, "edge_label": str|null, "reason": str|null,
    "explanation": str|null, "delivered": str|null,
    "contracts": int|null, "stake_usd": num|null,                       // null in shared
    "result": { "status": str, "won": bool|null, "close_cents": num|null, "clv_cents": num|null,
                "pnl_usd": num|null } | null                            // pnl_usd null in shared
  }],
  "clv_summary": { "n": int, "mean_cents": num|null, "ci95": [num, num]|null, "clustered_on": "slate_date",
                   "reason": str|null },
  "record": { "n_settled": int, "n_won": int, "n_lost": int, "n_void": int },
  "daily": [{ "slate_date": "YYYY-MM-DD", "n": int, "n_won": int, "clv_mean_cents": num|null, "pnl_usd": num|null }],
  "observations": [{                        // what JARVIS noticed in games (ingame_flags, halftime runs, exit warnings)
    "id": str, "slate_date": "YYYY-MM-DD", "ts": "…Z", "game_id": str|null, "matchup": str|null,
    "when_text": str|null, "kind": "foul"|"rotation"|"halftime"|"exit"|"correction"|"risk"|"other",
    "title": str, "text": str|null, "certainty": "reported"|"inference"|"correction", "corrects": str|null
  }],
  "alerts": [{ "id": str, "ts": "…Z", "slate_date": "YYYY-MM-DD", "kind": str, "mode": str|null,
               "title": str, "text": str|null, "play_ids": [str], "observation_ids": [str] }],
  "halftime": [{ "slate_date": "YYYY-MM-DD", "game_id": str, "matchup": str|null, "player_id": int|null,
                 "player_name": str|null, "stat": str, "threshold": num, "price_cents": num|null,
                 "model_prob": num|null, "edge_cents": num|null, "result": str|null }],
  "track_record": {                         // from the newest forward scorecard json; null fields with a reason when absent
    "scorecard_date": "YYYY-MM-DD"|null,
    "model_vs_market": { "n": int, "metric": "brier", "diff": num|null, "ci95": [num, num]|null,
                         "clustered_on": str, "reason": str|null } | null,
    "calibration": [{ "bin_lo": num, "bin_hi": num, "n": int, "mean_pred": num, "hit_rate": num }]
  }
}
```

## The app's side
- The API serves this file at `/api/desk` only when the API runs with `PH_PRIVATE=1`.
- The artifact bake uses `desk_shared.json`, unless it is built with `--money` (Devin's decision, logged in DECISIONS).
- The app validates `schema === 1` and renders a plain "not available" state for any `null` field.
