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
- *F9 generalises (descriptive, 2025-26 replay, pts):* young top-10 picks (≤2 seasons) under-predicted
  by −0.561 (n=1,300) vs −0.06 for other young players (n=5,358) and +0.094 for veterans (n=19,790);
  80% coverage 0.776 vs 0.813 / 0.795. The "Sheppard effect" is league-wide: the prior for a
  high-pick in a real role is too conservative in centre and spread. Pre-register on 2023–24.

## "Everything is a lineup property" (2026-10-09, fourth round)

- Good shooters create gravity → better spacing → teammates score more (Steph and Klay let KD and
  Draymond score). Deployment changes against certain teams. Teams with many rebounds play
  differently (half-court vs full-court).
- *Data agrees:* Draymond make rate 0.540 with Curry on vs 0.501 off (984 vs 407 shooting
  possessions, 2022-24); GSW offense 1.183 vs 1.117 ppp with Curry on/off. League-wide, offense is
  +0.039 ppp with the team's top 3PM shooter on the floor (600k vs 450k possessions). Gravity is
  measurable from stints + possessions we already have.
- *Why it matters for the whole project:* every individual-feature idea went null (tracking, hustle,
  RAPM, ridge); every win was about who is on the floor (injury report, lineups). The missing
  abstraction is the LINEUP as the unit: a player's prop distribution conditional on the five around
  him, not his own history alone. This is also the "structure of the game" the project wants to
  show. Hypothesis F11: lineup-conditional features (on-court teammates' gravity/rebounding, as-of)
  for props; the unseen-lineup problem is handled by composing player traits, which is what the
  original spec's DeepSets idea was for — but as a feature of context_residual, not a new
  architecture.
- **Pedigree lasts 3–4 years**; after that teams move on to the next new guy. So the F9 young-pick
  prior should decay over seasons 1–4.
- **Youth volatility is about role, not shooting:** a young player shows potential AND clear
  deficiencies (Sheppard can shoot but is undersized and gets targeted), so he may not play the
  4th quarter of a tight game. → The spread comes from *closing-lineup risk* (minutes), which is
  pre-tip predictable from game closeness (Elo margin) and the player's "closer share".
- **Rivalries are less serious between players** than between organisations (Spurs, Thunder, Rockets
  do have them). Rebounding identity is visible: two guys on the court with generally high OREB%
  and DREB%. Devin is unsure rivalries matter much — F10 is low priority.
- *F12 descriptive (stints, 2022-24):* Q4 floor time relative to Q1-3 — veterans .335 in close games
  (final margin ≤5) vs .312 in blowouts (+7%); young players (≤2 seasons) .333 vs .341 (−2.5%).
  Coaches trust vets to close. The pattern Devin described is in the data.

## Momentum, pace identity, trusted vets (2026-10-09, fifth round)

- **Offensive gravity is momentum control.** "Basketball is a game of momentum." Players who make
  tough shots and score on three levels keep the gap manageable — within 5–10 at any time — so
  the game never gets away. Gravity-inducing ≠ high scorer; it is the player who stops runs.
  *Hypothesis F13:* with the team's go-to scorer on the floor, |score gap| stays bounded (lower
  share of possessions with gap > 10), beyond his points. Measurable from `possessions.score_diff`
  with stints. If true, it is a *team win-prob* feature (blowout risk) and a *props* feature
  (blowout → short minutes) at once.
- **Pace identity:** the Rockets are a half-court team; teams without playmakers in a half-court
  offence are full-court teams. *Data agrees:* 2024-25 share of fast possessions (≤7 s) — HOU 4th
  slowest of 30 (0.457), with DEN, MEM, CLE; fastest: WAS, PHI, CHA, ORL, BKN. Possession length
  from the clock is a usable pace-identity measure; opponent pace identity is a pre-tip feature.
- **Trusted low-usage vets** can be identified because they *follow coaches or GMs around the
  league*: same player, same coach/GM, different team. *Measurable:* player–coach co-tenure across
  team-seasons (we have team_coaches and rosters) → a "coach's guy" flag → closer share, minutes
  stability. F14.
- *F13 descriptive (2022-24, own-offence possessions):* |gap| > 10 on 28.6% of possessions with the
  team's top scorer on vs 38.4% off; Q4: 36.9% vs 61.1%. Direction as Devin predicted, BUT the
  snapshot is confounded by reverse causation: coaches sit stars in blowouts, so "star off" is partly
  a consequence of a large gap (garbage time), not a cause. The frozen F13 rule must use gap
  *trajectories* (gap growth in the k possessions after a substitution, conditioned on the gap at the
  substitution) or an instrument such as forced absences (fouls, injury) — not on/off snapshots.

## Gravity from the defence's side (2026-10-09, sixth round)

- "It's more from a defensive perspective: *I want someone else to beat me; we can't stop him too
  much.*" Gravity is the defence's decision to redistribute the opponent's shots away from the star.
  So the measurable signature is not the star's points; it is his TEAMMATES' shot diet: more and
  easier looks, and whether they can convert.
- *Data agrees (2022-24, 820k shots):* with the team's top scorer on the floor, teammates make 53.8%
  vs 52.2%, take more rim shots (35.3% vs 34.1%) and more threes (41.8% vs 40.9%), and score 1.30
  vs 1.26 points per shot. Gravity = easier teammate shots.
- *Model implication (F11 refinement):* a player's prop distribution should carry the gravity of the
  stars he shares the floor with, as the *change in his own shot quality* (rim share, open-three
  share) — and the defence's choice of "whom to let beat us" is itself predictable from the
  opponent's defensive scheme. Momentum control (F13) and defensive redistribution are the same
  idea seen from the two benches.

## Defensive choices, blowouts, timeouts, bench, seeding (2026-10-09, seventh round)

- **Whom the defence lets beat them:** the 2nd-highest scorer on the court, OR the opposite of the
  team's philosophy (a three-heavy team → make them take layups), OR a volume shooter who isn't
  Steph. In the modern NBA look at SIZING on the court: you can only switch everything with mobile
  bigs and tall guards — but tall guards are usually deficient in half-court offence (exceptions:
  Cade Cunningham, LaMelo Ball). *Model note:* lineup height/mobility is a pre-tip proxy for scheme.
- **Fouling:** a bench player simply doesn't have to be as careful defensively.
- **Last shot:** the star always takes it or makes the last pass.
- **Hunting:** the star doesn't hunt, the TEAM does — they hunt the weakest defender in each lineup
  (Reed Sheppard). *Data agrees:* opponents' rim-shot share vs HOU 34.3% → 38.8% with Sheppard on
  (rookie year, 2024-25), 34.2% → 35.8% in 2025-26 (less huntable as he developed); opp ppp +0.02.
  Hypothesis F15: a lineup's "weakest-defender" liability (opp rim share lift when he is on) as a
  team-defence and props feature; it decays with the player's development.
- **Timeouts** signify something: sometimes stopping momentum, sometimes a team break.
- **Blowout rule (Devin):** "up by 25 before the end of the game, they put in the bench for good."
  *Data:* after the gap first reaches 25 in Q4, all five starters are off on 42.9% of remaining
  possessions (mean 1.17 starters on); at 20: 30.7% (1.68); at 15: 20.5% (2.19). The rule is real
  and gradual. → F12 input: pre-tip P(Q4 gap ≥ 25) from Elo margin.
- **The rest lineup / 6th man:** depends on whether the 6th man is a guard — compare Tre Jones vs
  Bones Hyland; a guard 6th man "keeps it up".
- **Season regime:** teams finalise the goal or coast: "if I won't win I'll be the worst; if I will,
  where am I fighting for playoff advantage?" Seeds 1–4 get home court, 5–6 are locked in, 7–10
  play for the 7th/8th spot. It gets less serious once the seed is locked. *Model note:* a pre-tip
  "stakes" feature from standings: seed-lock status, play-in zone, tank zone. Hypothesis F16.
