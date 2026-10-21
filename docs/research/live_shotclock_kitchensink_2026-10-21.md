# Reading-log intake: shot-clock usage, mid-game modelling, kitchen-sink arm (2026-10-21)

Three ideas Devin raised on 2026-10-21 between 12:30 and 13:00 CDT, logged here so each has a claim, the evidence we
already hold, a fit, and a first step. None is a rule yet. Scratch first; a result becomes a claim only through a frozen
test. Companion note: docs/FAN_KNOWLEDGE.md (same day).

## 1. Shot-clock usage as information ("if a team uses min/max of the shot clock that changes who does what")

- **Claim.** Teams differ in how early or late in the shot clock they shoot; that timing, and each player's share of
  early-clock shots, shifts who takes shots and how many possessions a game has. As a pre-tip feature it could move
  props (pts, fg3m, ast) and totals.
- **What we hold.** `possessions` (seasons through 2024): `clock_start`, `clock_end`, `off_team`, `shooter_id`,
  `assister_id`, `outcome`, `shot_zone`, lineups on both sides. Possession length = clock_start minus clock_end is the
  shot-clock proxy (no shot-clock field, so an offensive rebound resets are approximated by `oreb`). Enough for a team
  and player early-clock share as-of any date. Not tracked today as a feature.
- **Prior evidence here.** Pace x efficiency totals: the walk-forward blend weight chose w = 0 in every block against
  the market line (T472). Tracking and hustle rate families add nothing beyond recency box-score averages for pts, reb,
  ast, fg3m (T119-T123, T174-T175; n 27,583 player-games, 2024, clustered CIs crossing zero). Schedule density and
  travel add nothing to minutes or P(play) (T451-T454). So the prior that a possession-timing feature beats recency
  averages for props is low; the information is pre-tip and ours, which is the one kind of idea that has won here.
- **Fit.** Residual feature on A0 (context_residual) for pts and fg3m; a totals feature only if a game-total model is
  ever revived (none is in production; markets beat us on game outcomes).
- **First step (scratch, seasons <= 2024, no rule).** Team early-clock share (shots with possession length <= 7 s of
  possession) and late-clock share (>= 18 s) by month; month-to-month stability (Spearman); player early-clock shot
  share vs usage. If the team shares are not stable month to month, stop: an unstable feature cannot be known at
  tip. If stable, a pre-registration draft with the standard gate (label-free power on 2023/2024, priors in the
  predictions ledger, three clusterings).

## 2. Mid-game modelling (live odds, "better by the end of Q1, Q2, Q3", half-time and first-to-10 markets)

- **Claim.** A forecast that updates on the live box score and play-by-play beats the pre-tip forecast for the rest
  of the game, and in-game markets (half-time winner, first to 10, quarter totals) are where a model with the raw
  state could find edge.
- **Credibility.** Plausible by construction (conditioning on realised minutes and foul trouble removes the largest
  error source: "most prop error is minutes"). The open question is not whether information helps but whether we can
  get it, process it and price it inside the latency and fees of the live market. Prices in-game move fast; our
  Kalshi snapshot is every 15 min and pre-tip.
- **Prior evidence here.** None in-game. Pre-tip: the market beats us on winners (2025-26 replay Brier +0.0064 worse
  than Kalshi); props are the plausible edge and it is small (T478, T493).
- **Fit.** A separate product line, not a change to the daily pipeline. It needs its own raw data first.
- **First step (tonight, hardening item 9).** Capture only: live scoreboard, box score and play-by-play each minute and
  Kalshi in-game market pages each minute, write-once under `data/raw/live_<date>/`, no model. Then a descriptive on
  historical play-by-play (seasons <= 2024): how much of a player's final pts is explained by his Q1 minutes and
  points, by quarter; how often the first-to-10 side is the pre-tip favourite. Only after that, a rule.

## 3. Kitchen-sink diagnostic arm ("a model that incorporates every factor regardless of pass or fail")

- **Claim.** A single model with every tested feature family would reveal segment structure (the piecewise picture)
  even where single families failed their own gates.
- **Credibility.** As a production model, no: every architecture-only idea lost or tied here and a feature carrying a
  gain alone has been the leak signature twice (BEST_PRACTICES). As a diagnostic it is useful: per-slice contrasts
  of the kitchen-sink arm against A0 show where information exists that the single-family tests were too small to see.
- **Fit.** Research arm only, never promoted on its own; its output is a list of slices worth a pre-registered test.
- **First step (after the hardening window).** One harness over seasons 2023-2024 with every closed feature family
  (tracking, hustle, workload, advanced rates, market context, injury reason, calendar) on A0; report per-slice CRPS
  deltas with clustered CIs and the knockout of each family; nothing on 2025.

Decision rows and ledger rows are written only when one of these becomes a rule.
