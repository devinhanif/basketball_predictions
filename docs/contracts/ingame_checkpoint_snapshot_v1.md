# In-game checkpoint snapshot, v1 (draft; JARVIS side writes it, the app only reads it)

Why: the app must never open `data/jarvis/ingame_checkpoints.duckdb`. A read-only DuckDB connection takes a shared file lock,
and the live writer (`nba/markets/checkpoint_store.py`) connects without retry, so a public reader could make a live
checkpoint write fail (adversary review app_live_pbp_2026-11-10, M1). The app reads this JSON file instead.

Who writes: the JARVIS in-game checkpoint job, after each checkpoint write, only when the flag is on. Write to a temp file in
the same directory and rename into place (atomic). Until it exists, the app answers `available: false` (today's state).
No change under `nba/` is made by the app work; this is the contract for the owner of that side.

Path: `data/jarvis/ingame_checkpoints_latest.json` (the app reads `APP_CHECKPOINT_SNAPSHOT`, default that path). Size limit 5 MB.

```json
{
  "version": 1,
  "generated_at": "2026-11-10T01:40:05+00:00",
  "games": {
    "<ESPN event id>": {
      "checkpoint": 25,
      "e_used": 25.1,
      "support": "trained",
      "period": 3,
      "clock": "3:39",
      "known_at": "2026-11-10T01:40:00+00:00",
      "rows": [
        {"player": "Luka Doncic", "stat": "pts", "k": 30, "p_model": 0.5, "cur": 22, "minutes": 25, "fouls": 1}
      ]
    }
  }
}
```

- One entry per game in play, holding only the newest checkpoint's `mode='live'` rows (up to 200 rows are read). Over side only,
  as in the store.
- Fields are exactly those shown. The app copies only these (a price, ask, bid or edge in the file is ignored), so the live view
  stays price-free (Learn lens).
- The app caches the file for 15 s per process and never writes it. A game that is final is hidden by the app.
- Label shown with every number: "UNPROVEN: no live record; most nights the market already knows this."
