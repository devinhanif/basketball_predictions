# Fan knowledge

Things Devin knows about the game from watching it that are not in any dataset here. Recorded in
his words (lightly condensed), with Claude's notes on what each would change and whether it is
testable with data we already have. These are hypotheses, not facts, until a frozen test says so;
they are also the best source of *new* hypotheses the project has. Add to it any time.

Devin is a Rockets fan.

## Minutes and rest (2026-10-09)

- Certain coaches believe in resting starters on back-to-backs, and will also rest a player with a
  lingering injury when the season projection looks fine (Anthony Edwards sat vs HOU last season:
  lingering injury + second game of a back-to-back).
- Blowouts: when the bench comes in depends on whether the team has youth to develop ("project
  players") and on the coach. The Nets run 12 deep, European style. Some coaches finish with a
  different five than they start.
- *Testable now:* coach-level rest propensity on B2Bs (we have coaches per team-season, rest days,
  and minutes); blowout bench-timing by team age profile; starter-vs-closer differences from
  stints. Candidate pre-tip features: coach rest-rate, team youth share, "closer" minutes share.

## Foul trouble (2026-10-09)

- The rule of thumb: 2 fouls in Q1, 3 in Q2, 4 in Q3, or 5 before the last 5 minutes → benched
  until the end of that quarter. Some bench players are there to foul.
- *Testable now:* play-by-play has fouls with clock; we can measure how strictly each coach follows
  the rule and how much minutes a star loses to it. A pre-tip "foul-trouble risk" = player foul
  rate × opponent FTA rate × coach strictness.

## "Questionable" (2026-10-09)

- Questionable players are *less* likely to play in a rivalry game (no need to risk it) — so
  opponent matters.
- Contract incentives and award eligibility (the 65-game rule for major awards/some bonuses) push
  players to play when near the threshold — so games-played-to-date and the calendar matter.
- *Testable now:* P(play | questionable) by opponent-rivalry flag, by games-played vs the 65 pace,
  by month. Our P(play) model has none of these.

## When a star is out (2026-10-09)

- Depends on team structure: stable teams go "more team basketball"; otherwise it depends on how
  long the star is out and who is around him. It is about *opportunity*, not the backup at the same
  position.
- *Testable now:* usage redistribution conditioned on absence length (first game vs 10th game out)
  and on team "stability" (lineup continuity). The vacated-stats feature currently ignores how
  long the player has been out.

## Shooting and the hot hand (2026-10-09)

- Less about the hot hand, more about what the coach allows: 8 threes last night does not mean 15
  attempts tonight. Shot diet is set by the coach.
- Free-throw percentage is a tell for who is a good shooter and therefore who gets the better shot
  diet (FT% over ~85%).
- *Testable now:* does FT% predict 3PA share and 3P% beyond recent 3P%? Does last-night's makes
  predict tonight's attempts (coach allowance) or only makes (hot hand)?

## Playoffs (2026-10-09)

- Game-planning is specific to the opponent; what counts as a foul gets more physical (unclear if
  fewer are called). Series context matters: games 1–2 home, 3–4 away, then 5–7; the goal is to
  avoid going down 3–1.
- *Testable now:* fouls per possession and pace in playoffs vs regular season; model error by
  series game number and series score. Our models treat playoff rows like regular-season rows,
  which is probably wrong.

## Rookies (2026-10-09)

- Role depends on team context. Some arrive ready (Donovan Mitchell, LeBron); others grow into the
  role. High picks get the right to minutes if they are at least average and the best in the depth
  chart.
- *Testable now:* rookie minutes by draft slot × depth-chart position (we have rosters and
  minutes); time-to-stable-role distribution.

## Things no dataset has (2026-10-09)

- Revenge games matter. A coach on the hot seat matters. The contract-year effect is probably
  disproved. Marketable players play better on national TV (we have the national-TV flag; our
  earlier screen found it the only tiny survivor for win prob — now test it for *player* props).
  Coaches have favorites. Some players "set the pace".
- *Testable partly:* revenge games (player's former team) from transaction history; national TV ×
  player marketability for props; coach favorites from minutes-vs-production residuals.

## Gambling, from the inside (2026-10-09)

- Happiness vs knowledge is a U-shaped curve: knowing nothing is fun, knowing a lot is fun, the
  middle is painful. Winning moves you along the knowledge axis.
- It is addictive because growth in your own skill feels like justification, but that same growth
  clouds judgment.
- *Design implication:* the tool should keep showing the true odds even when the user is winning;
  the moments after wins are when the display matters most. Record wins and losses in the same
  font.

## Pace, roles, rosters, playoffs, deadline (2026-10-09, second round)

- **Pace-setters:** the modern game cares about shot-clock utilisation and points per minute.
  Fred VanVleet (last season he played) controlled the clock, spaced the floor, took hard shots,
  never turned it over, and dictated the flow. *Measurable:* seconds-into-the-clock at shot, team
  TOV rate and pace with him on vs off (we have possession clocks and stints).
- **Favorites:** coaches' favorites and *management's* favorites differ. Players like Derrick White
  or Al Horford keep minutes despite age because of presence and playing their role. *Measurable:*
  minutes that do not fall when production falls (minutes-vs-production residual by player).
- **Rotation depth** follows the roster: a stacked roster plays deeper; an injury-prone roster also
  plays deeper, to prevent injuries, because a dense rotation causes them. Some teams practise harder
  (the Heat). So depth is a roster trait more than a coach trait.
- **Home games are the ones you must not lose**: teams do more of whatever they are best at, at
  home. Beating someone away means something. *Model note:* HOU 2025-26 replay — home predicted
  0.700 vs actual 0.705; away predicted 0.601 vs actual 0.523. The model over-rated a young team on
  the road. Test league-wide: road over-rating by team age.
- **Roster constraints and the deadline:** players get sat or cut by roster constraints; teams get
  "financial" as soon as they realise they are out of the race (the Celtics, the Suns last season).
  Load management depends on whether the player is an asset; teams get in trouble for it, so it is
  under-reported. *Model note:* late-season tanking/financial behaviour is a regime change our
  features do not see.
- **Rockets blind spot (replay):** a 13.5-ppg young scorer under-predicted by 1.6 pts/game all
  season; two more starters by ~0.8. Rebounds over-predicted by +0.16 (league -0.003). The model is
  slow on a rising young team: recency averages look backward. Hypothesis for a prereg: role-growth
  slope as a feature (trend in minutes/usage over the last N games), especially for players ≤ 3
  seasons.

## Gambling, from the inside (second round)

- "When I win I just bet more in the way that I won. Once I bet $3, won $100, and bet $3 twenty
  more times and lost. The one was what I was chasing. It's the satisfaction in being right and
  having pride to back yourself on capitalism."
- *Design implication:* after a win the tool shows the same odds in the same font and says "this one
  hit; it was still a −X% bet." The moment after a win is the moment honesty matters most.

## Houston, specifically (2026-10-09, third round)

- **Rebounding is a lineup property.** With Steven Adams and Clint Capela in double/triple-big
  lineups, Houston led the league in offensive rebounding and second-chance points; opponents
  game-planned for it. When Adams sat for surgery the rate dropped. *Data agrees:* HOU OREB%
  25.1% (2023-24) → 31.6% (2024-25, Adams 64 gp) → 34.5% (2025-26, Adams 22.8 min). Our props model
  attributes boards to individuals, so it over-predicted Rockets rebounds (+0.16) when the lineup
  changed. *Hypothesis F8:* team OREB%/DREB% as a function of the on-court bigs (lineup-level
  rebounding, from stints), as a feature for individual reb; Devin: think in usage, RB%, BLK%.
- **Reed Sheppard** (id 1642263): top-3 pick who sat behind Fred VanVleet (undersized like him, a
  leader, mentor/starter). When VanVleet tore his ACL, Sheppard got the role and was expected to do
  his best; "he's young so he's volatile." *Data:* VanVleet has 0 games in 2025-26; Sheppard was
  ~26 min / 15 ppg from November. The model under-predicted him 1.6 ppg all season at steady
  minutes → the error is a too-conservative young-player prior (center AND spread), not a lag.
  *Hypothesis F9:* for players ≤ 2 seasons with a top-10 draft slot, the recency prior should be
  pulled toward the role (minutes, usage share) rather than the league average for size.
- **Rivalries** are regional (within divisions) plus Finals history (Lakers–Celtics, Cavs–Warriors,
  LeBron vs the Spurs, …) — Claude should compile a list and Devin verifies. Also: players who met
  in FIBA/Olympics/college can "go off for an unforeseen reason" — see Anthony Edwards vs Luka
  Doncic.
