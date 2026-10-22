# nba.ride: blowout-ride detector (read-only)

Reads the live capture (`data/raw/live_<date>/`), scores each live game with a small logistic win-prob
model (home margin, seconds left; fit on 2022-24 regular-season play-by-play, season 2025 never loaded),
cross-checks ESPN's win probability, and pushes a RIDE alert when the leader is >= 98% by both, the capture
heartbeat is fresh (< 2 min) and the game file is fresh. One alert per game; then plan lines only on a real
change (settle, 25% bid move over ~10 min, loose stop, thesis broken). No orders, no credentials: Devin
places every trade by hand.

Run from a checkout whose `data/` points at the real data dir:

    export NBA_IMESSAGE_TO='<your number or Apple ID>'      # never commit it
    python -m nba.ride notify-test                          # one harmless test push
    python -m nba.ride watch --date 2026-10-22              # log: reports/ride/2026-10-22.log
    python -m nba.ride replay --date 2026-10-21             # dry run on a finished capture
    python -m nba.ride backtest                             # calibration + comeback rate -> reports/ride/backtest.json

Per-minute features for the PAPER swing arms: `data/derived/ride_features/<date>/games_*.parquet` and
`candles_*.parquet` (write-once). Position state: `data/derived/ride/state_<date>.json` (restart-safe).
Not modelled: possession (not in the ESPN feed in a usable form), team strength (score-only model).
