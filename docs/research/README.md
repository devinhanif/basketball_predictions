# Reading log

Where papers, repos and outside ideas enter the project. One note per item, written by the
research-scout agent or by hand. Ideas are welcome and plentiful; claims are rare and earned.

## How an idea travels

1. **Note** — `docs/research/<slug>_<date>.md`: the claim as stated; credibility (split method,
   leakage risk, same-game inputs, sample size, venue); what this repo has already tested
   (`docs/TEST_LEDGER.md`); the *new information* it would add (architecture-only ideas have a
   poor record here); data and compute needs; verdict PURSUE / PARK / SKIP.
2. **Explore** — scratch scripts, fast and loose, in `data/scratch/` or a notebook. Nothing from
   here is a result. This is where the fun is.
3. **Freeze** — if exploration looks promising, a pre-registration doc (rule, floor, CI, seasons,
   slices) is committed *before* the real run. Claude drafts it; Devin confirms it.
4. **Run, record, attack** — one run, a ledger row (win or null), a red team if it won.

The wall between 2 and 3 is the whole method. Anything that crosses it without a frozen rule is
exploration, however good it looks.

## Log

| Date | Item | Verdict | Note |
|---|---|---|---|
| 2026-10-09 | Historical odds providers (The Odds API, Kalshi, others) | PURSUE | [historical_odds_2026-10-09.md](historical_odds_2026-10-09.md) |
| 2026-10-08 | Stacked-ensemble, GCN+RF, NCAA LSTM/Transformer papers (pasted) | tested → not kept | see T-rows for seq_props, joint_game_set, pbp_gpt, winprob family |

## Questions we would like papers for

- Market microstructure of player props: where and when prop lines are stale (late scratches,
  thin books, back-to-backs).
- Minutes: how coaches allocate minutes under blowout, foul trouble and rest; any public models.
- Usage redistribution when a starter is out (causal, not correlational).
- Conformal prediction with per-player (per-group) coverage guarantees.
- Betting-policy evaluation: stake sizing, correlated losses, risk of ruin at small stakes.
- Honest accuracy ranges for pre-game NBA win models (to calibrate what "good" means).

## Fan hypotheses queued for pre-registration (from docs/FAN_KNOWLEDGE.md)

| # | Hypothesis (Devin) | Data we already have | Target |
|---|---|---|---|
| F1 | Foul-trouble rule (2/Q1, 3/Q2, 4/Q3, 5 before last 5 min) → predictable short-minutes games | PBP fouls + clock, coach per team-season | short-minutes P, pts lower tail |
| F2 | Questionable + rivalry → sits; near the 65-game threshold → plays | injury report, games played to date, schedule | P(play) |
| F3 | Star absence: effect depends on how long he has been out and team continuity | availability history, lineup continuity | vacated-stats feature |
| F4 | FT% is a tell for shot diet (3PA share) beyond recent 3P% | box scores | fg3m, pts |
| F5 | Young/rising teams are over-rated on the road; role-growth slope for ≤3-season players | games, minutes, usage trends | win prob (HOU away 0.60 pred / 0.52 actual), props |
| F6 | Coach rest propensity on back-to-backs; roster depth by youth/injury profile | coaches, rest days, minutes | minutes, P(play) |
| F7 | Playoff rows differ (fouls, pace, series state) and should not be pooled naively | possessions, game ids | all |
| F8 | Team rebounding is a lineup property (double-big lineups), not a sum of individuals | stints, box scores (OREB/DREB), players_static heights | reb |
| F9 | Young top-10 picks in a starting role: prior pulled toward role, wider spread | draft slot, minutes/usage share, seasons played | pts, all |
| F10 | Rivalry/FIBA-history matchups: questionable → sits; past-opponent → "goes off" | divisions, Finals history list (Devin verifies), international rosters | P(play), pts |
| F11 | Lineup-conditional props: teammates' gravity/rebounding on the floor (as-of, composed from player traits) | stints, possessions, players_static | all; the "unseen lineup" problem |
| F12 | Youth volatility = closing-lineup risk: young players with deficiencies sit in close 4th quarters | stints by period, Elo margin, seasons played | minutes spread, pts lower tail |
| F13 | Momentum control: go-to scorer on floor bounds the score gap (blowout risk), beyond his points | possessions.score_diff, stints | win prob, minutes (blowout → short minutes) |
| F14 | "Coach's guy": players who follow a coach/GM across teams get trusted minutes and closing time | team_coaches, rosters across seasons, stints | minutes stability, closer share |
| F15 | Weakest-defender liability: lineup's hunted player (opp rim-share lift when he is on), decaying with development | possessions (def_players, shot_zone), seasons played | team defence, props of the hunted player's teammates |
| F16 | Season stakes: seed-lock / play-in / tank zones from as-of standings change effort and rotations | games (standings as-of), schedule | win prob, minutes, P(play) late season |
| F17 | Rest-lineup quality: who runs the offence when the star sits (guard 6th man "keeps it up") | stints, possessions | bench props, team ppp by segment |
| F9b | Young-pick prior, re-registered: undrafted as its own category; season index from draft year, not data start | players_static, box scores | pts, all (replaces the F9 rule, which failed its own data check: T184). CLOSED 2026-10-10: all checks passed, no stat reached the floor (T186-T201); no F9c |
| F18 | Traded-player cold start by trade REASON (salary dump / new direction / push / fit / age-need), inferred from the acquiring team's pre-tip situation | transactions (to collect), standings as-of, age, roster | minutes and usage in the first month after a trade |
| F19 | "Never a night where 4 givens happened": do the night's safest legs hit as often as their price implies? (favourite–longshot bias) | 2025-26 replay, Kalshi prices as-of | DESCRIPTIVE DONE 2026-10-09: top-4 sweep 40.5% vs 37.4% implied, CI includes 0; priced right (reports/f19_safest_legs.md) |
