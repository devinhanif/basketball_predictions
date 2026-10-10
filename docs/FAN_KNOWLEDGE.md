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
