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
- *6th man as a guard (data, 2022-24):* Tre Jones on → team 1.131 ppp vs 1.090 off, TOV 13.9% vs 14.8%;
  Bones Hyland on → 1.123 vs 1.169 off, TOV 15.3% vs 14.4%. Opposite signs for the same role: the
  "rest lineup" has its own measurable quality (who runs the offence when the star sits), and bench
  players' props live there. Hypothesis F17: rest-lineup ppp as a pre-tip feature for bench props.
- *#2 scorer next to the star (data, 2022-24):* shot share 23.4% with the star on vs 27.7% off, but
  make rate 54.2% vs 53.0% and 1.286 vs 1.259 pts/shot. "Let someone else beat us" from the #2's
  side: fewer attempts, easier attempts → his points can stay flat while efficiency rises — a
  distribution-shape effect, not a mean effect.

## Corrections from the frozen tests (2026-10-09, night)

- **F12 closed (T185):** under the frozen rule (season index from draft year, OT excluded), the
  premise did NOT replicate. Young players' Q4 share is .319 in close games vs .465 in blowouts;
  veterans .336 vs .253. Claude's earlier quick check (.333 vs .341) was wrong — garbage time is
  where young players play, which is a different claim from "young players sit in close games."
  Devin's point about closing lineups may still be true for *rotation* young players in *close*
  games, but that needs a garbage-time definition and better stint data. Honest null for now.
- **Stint quality problem found:** stint minutes reconcile with box-score minutes within 1 minute
  on only 48% of player-games. Every lineup-based hypothesis (F11, F13, F15, F17) depends on stints,
  so fixing stint parsing is now the prerequisite. F11 is held until it is fixed.
- **F9 closed (T184) → F9b frozen:** undrafted players (112 of 561 regulars) are a category, not
  missing data; seasons must be indexed from the first NBA season, not from our 2022 data start.

## Caveat on every lineup number above (2026-10-09, late) — resolved below

The stint diagnosis (docs/reviews/stint_reconciliation_2026-10-09.md, d6a38ef) found that
`possessions.off_players/def_players` carry the previous period's five into Q2–Q4 until someone acts:
only ~78% of possessions have the exact correct five on both sides (Q1 93%, Q2–Q4 ~72–75%; mean
overlap 4.72/5). Durations are exact; attribution is not. Every on/off number in this file (Curry
gravity, Sheppard hunted, Tre Jones vs Hyland, #2-scorer lift, momentum snapshots) was computed on
these lineups. The tracker was fixed the same night (ADR 0002; 43dba25 and 05527ea) and every
number was recomputed; see the next section. F11, F13, F15, F17 can proceed on the new lineups.

## Recomputed on the fixed lineups (2026-10-09, night)

Tracker v2 (look-ahead openers, no eviction, events in clock order): stint minutes within 1 min of
box minutes on 98.4–99.1% of player-games per season, up from 45–49%. The table runs the *same*
definitions on the old lineups (v1 backup) and the new ones, so the change is the tracker alone.
Definitions: per team-season, top scorer = most total points, #2 = second, top 3PM shooter = most
threes made; "on" = in `off_players` (or `def_players` for the defence side); shooting possessions =
FGM2/FGM3/FGA_miss. Descriptive, not frozen tests; the doc's earlier numbers used slightly different
cuts, so compare within this table.

| Claim | Measure (2022–24 unless stated) | old lineups | fixed lineups |
|---|---|---|---|
| Curry gravity | Draymond make rate, Curry on / off | .555 / .511 (n 957 / 399) | **.560 / .495** (n 986 / 370) |
| | GSW ppp, Curry on / off | 1.183 / 1.117 | 1.185 / 1.113 |
| | League ppp, team's top 3PM shooter on / off | 1.160 / 1.120 (457k / 330k) | 1.160 / 1.120 (462k / 325k) |
| F13 momentum snapshot | own-offence possessions with gap > 10, top scorer on / off | 28.3% / 37.7%; Q4 36.4% / 60.1% | 28.3% / 37.9%; Q4 35.8% / 60.2% |
| Gravity, defence side | teammates' make / rim share / three share / pts per shot, star on | 55.5% / 35.4% / 41.5% / 1.31 | 55.4% / 35.5% / 41.6% / 1.31 |
| | same, star off | 53.9% / 34.1% / 40.3% / 1.27 | 53.9% / 34.0% / 40.3% / 1.27 |
| #2 scorer | shot share / make / pts per shot, star on | 23.4% / 55.8% / 1.300 | 23.3% / 55.8% / 1.302 |
| | same, star off | 27.7% / 54.7% / 1.276 | 28.0% / 54.7% / 1.275 |
| Sheppard hunted, 2024-25 | opp rim share vs HOU, Sheppard on / off | 38.7% / 34.4% | **39.8% / 34.1%** (n 1,420 / 7,274) |
| | opp ppp, on / off | 1.124 / 1.102 | 1.138 / 1.099 |
| Sheppard, 2025-26 | opp rim share, on / off | 35.8% / 34.4% | 35.9% / 34.2% (n 4,690 / 3,866) |
| Blowout rule (2022–25) | Q4 after gap first ≥ 25: all five starters off / mean starters on | 42.0% / 1.20 | **45.4% / 1.17** (n 40,308) |
| | gap ≥ 20 | 30.2% / 1.69 | 33.6% / 1.67 |
| | gap ≥ 15 | 20.7% / 2.17 | 23.3% / 2.16 |
| 6th man as a guard | Tre Jones on / off: team ppp, TOV% | 1.130 / 1.113; 13.9% / 14.7% | 1.126 / 1.115; 13.9% / 14.7% |
| | Bones Hyland on / off: team ppp, TOV% | 1.122 / 1.164; 15.3% / 14.3% | 1.116 / 1.165; 15.3% / 14.3% |

What changed: every direction holds. The effects that live in individual players sharpened
(Draymond's split widened from 4.4 to 6.5 points; Sheppard's rookie-year rim-share lift from 4.3 to
5.7 points; the up-25 bench rule from 42% to 45%), because the old tracker credited the wrong
player for the first stretch of each period and that noise diluted on/off contrasts. Team-level
aggregates (ppp with the top shooter on, the defence-side gravity shares) barely moved; with 300k+
possessions the attribution noise averaged out. The Tre Jones offensive lift is smaller here than
first reported (1.1 vs 4.1 points of ppp) because this cut pools all his 2022–24 teams; the
turnover split is unchanged. F12 (T185) is a frozen result and is not recomputed; a question about
rotation-level young players in close games would be a new rule.

## Trades, blowouts, schemes (2026-10-09, eighth round)

- **Everybody gives up after a point**: the losing team empties its bench too. So blowout bench time
  is symmetric; both benches' props live in garbage time.
- **Schemes are fluid**, flowing between drop / switch / blitz within a game. No stable team labels;
  roster SIZING (mobile bigs, tall guards) is the right pre-tip proxy for what a team *can* run.
- **Why a player gets traded (Devin's taxonomy):** (1) same output at lower financial value;
  (2) the team doesn't believe in its direction and is going a new route; (3) a drastic push to the
  next level; (4) he doesn't fit the style the team wants; also: age, or filling a need. Each story
  predicts a different first month for the player (salary dump → role holds; rebuild → old player
  plays less, young plays more; buyer's push → usage down, efficiency up; fit → the clearest
  improvement). The acquiring team's situation (standings, age, cap) is public pre-tip, so the story
  is partly inferable. *Hypothesis F18:* trade-reason classification as the cold-start prior for
  traded players, instead of "reset to league average".
- **Trusted vet, named:** Al Horford (twice). First name on the F14 list.
- *F18 feasibility:* ~70–90 in-season team changes per season are detectable from box scores alone (2022: 71, 2023: 85, 2024: 88, 2025: 79). Enough to test; waits its turn.

## Parlays (2026-10-09, ninth round)

- **Devin wants to hit crazy parlays too**, knowing they are a lottery ticket: "I'm willing to lose
  everything." Claude's position: a long parlay is where the house margin compounds (each leg priced
  as if independent, each carrying its fee), so it is not an edge play, but same-game legs are *not*
  independent and the book prices them as if they were. Blowouts (starters out at 45% after a 25-point
  gap), pace, and star-minutes correlations are where a long ticket can be priced wrong in the bettor's
  favour. The copula engine already models leg correlation. *Build:* "lottery mode" in the budget tool
  (dollars + target payout → the combination with the highest true P(hit), the honest number printed
  on the ticket), then correlation-aware same-game legs from the lineup structure.
- **"There's never a night where 4 givens happened."** Devin's observation from experience: even the
  sure things don't all land. Math agrees: four 85% legs hit together 52% of nights; four 90% legs
  66%. The feeling of a given is a single-leg feeling; the parlay multiplies the doubt four times.
  *Hypothesis F19:* the safest legs of a night (highest-priced, say ≥ 80% implied) hit more or less
  often than their price implies? The favourite–longshot bias predicts more often (favourites
  under-bet). Measurable on the 2025-26 replay with Kalshi prices: all-four hit rate of the four
  highest-priced legs per night vs the product of their implied probabilities, clustered by date.
  Descriptive first; if there is anything, a frozen rule before it touches the budget tool.
- *F19 descriptive (2025-26 replay, Kalshi game-winner prices, last two-sided candle before real tip,
  de-vigged mid; 207 nights, 1,312 games; reports/f19_safest_legs.md):* the night's four highest-priced
  sides all won on **40.5% of nights (62 of 153)**; the prices multiplied to 37.4% (gap +3.1 points,
  night-clustered CI [−4.1, +10.5]). Two legs 64.4%, three 54.1%, five 28.6%. A rolled four-leg ticket
  after Kalshi taker fees returned about −5% (CI −25% to +15%). Single legs priced ≥ 0.80 hit 85.7% vs
  86.5% implied. Devin's "never 4 givens" is exactly the arithmetic: 91 of 153 nights broke, 63 of them
  by one leg (e.g. 2026-03-21, PHX at 0.83 lost to MIL 108–105; 2025-11-21, BOS at 0.89 lost at home to
  BKN). Sweeps: 2026-04-12 (TOR, POR, PHI, DET) and 2026-04-03 (BOS, HOU, ATL, CHA). The market's
  "givens" are priced right; no favourite–longshot edge at ticket level. Our own model's top four hit
  33.3% vs 35.4% predicted. Legs ≥ 0.90 hit 65 of 66, above price, but n is tiny and the threshold was
  not declared; a curiosity, not a finding. Verdict: safe-leg parlays are a fair lottery ticket minus
  fees; the structure to exploit, if any, is same-game correlation, not favourites.

## Where the points model is good and bad (2026-10-10, descriptive, 2025-26 replay, n = 26,460)

- *By volatility bucket, error relative to the player's own predicted spread (MAE / std):* smooth .80,
  intermittent .81, lumpy .83, erratic .77, **insufficient history 1.10** (n = 563, bias −0.46). The one
  model fits every player type equally except cold starts. This is what is left of the routing idea
  (Devin, 2026-10-10: "it feels like that idea is gone"): it lives in the 2% of player-games with little
  NBA history, where a different prior is still worth testing (usage redistribution, F9b, cold-start priors).
  The possession sim was the wrong instrument for it (forward replays, SIM_STATS = () since 2026-10-08).
- *By history depth:* MAE 3.9 (< 15 prior games) → 4.7 (120+), but that tracks scoring level, not skill.
- *Most predictable relative to scoring (CRPS / avg pts, ≥ 40 games):* Gilgeous-Alexander .14 (30.5 ppg,
  MAE 5.9), Leonard .15, Embiid .15, Durant .16, Jaylen Brown .16, Booker .17, LeBron .17, Dončić .17.
  *Least:* 5-ppg bench players at .56–.63 (MAE 4.2–4.9 on a 5-point average). A star's role and minutes
  are the most knowable thing in the league; the bench's minutes are decided after tip.
- *Individual biases to ask Devin about:* under on Dončić (−2.3), Leonard (−1.9), Brown (−1.3); over on
  LeBron (+1.4). Stars under, an ageing star over: an age-curve × usage-share hypothesis.
- **F9b closed (T186–T201, 2026-10-10):** the re-registered young-pick prior passed every data check this
  time (630/630 draft status, 100% first season, slice S 948 and 759 rows) and ran all four arms. No stat
  reached the frozen floor: pts slice dCRPS −0.003 (2023) and −0.006 (2024) against a −0.02 bar, CIs
  spanning zero. The prior did move the slice bias toward zero (+0.48 → +0.34; +0.42 → +0.30) and nudged
  coverage up, but the placebo arm with permuted draft picks moved the bias nearly as much, so what moved
  it was role (minutes share), not pedigree. Devin's observation stands as description: production
  under-forecasts young top-10 picks in a real role by about 0.4 points in 2023–24 (and 0.56 in the
  2025-26 replay; same sign, different convention). What did not hold is that draft slot adds information
  beyond the role itself. Honest null; no F9c. The role piece is already what the lineup and minutes work
  is chasing.

## What the market comparison taught us (2026-10-10, ODDS_HISTORY, T203–T236)

- Two seasons, two and a half million prop prices, every book: **the line is a better forecast than our model**, for
  every stat, at the hour we would predict and at close. Our probabilities relative to the line are over-confident:
  when we say a player is 26% to go over the line, he does 45% of the time; when we say 64%, 53%. What the model adds
  beyond the line is mostly noise. Game winners: the same.
- The market-as-prior shape is confirmed: a blend that is 70–90% market and 10–30% us is indistinguishable from the
  market almost everywhere, and on rebounds against Pinnacle it is 0.002 log loss better (both timings, n ≈ 7k pairs,
  CIs clear of zero). That is the one place our information might add a sliver, and it is the stat Devin named first as a
  lineup property. It is with the adversary; nothing is claimed.
- For the budget tool: "keep your money" stays the answer; the market's own number is the one to show.
- *Red team on the rebounds cell (T237):* WOUNDED. Half the 0.002 is Pinnacle pricing rebounds overs too high (shading to the
  under needs no model); the model's own share is 0.001, below the floor. Honest summary: the market is better everywhere, and
  the one sliver that survives is a price bias, not basketball knowledge. Nothing promoted.
- **F11 closed (T244+, 2026-10-10):** projected teammates' traits, weighted by recent co-play, add nothing at T-60 on fixed lineups (powered null; placebo ties the real arm). But the oracle that uses tonight's ACTUAL five gains 0.058 CRPS on points, the biggest effect we have ever measured on props. "Everything is a lineup property" survives in its sharpest form: the lineup matters enormously, and the only version of it that is knowable is the confirmed five at T-30. That is where the T-30 arm and the live log now carry the idea.


## Where the model misses, with the data (2026-10-10, descriptive on 2023-24 and 2024-25 OOF rows; reports/model_miss_questions_2026-10-10.md)

- **Stars (prior-10 mean >= 20 ppg, n 7,022):** the only condition that moves the residual by >= 0.5 is a rotation teammate
  RETURNING after >= 3 missed games: star scores 1.17 below forecast (n 685; diff vs rest −1.37 [−2.00, −0.70]; both seasons).
  Teammates OUT (0/1/2+) does nothing; opponent defence, rest, first 15 games nothing; away +0.25 vs home −0.11 (diff 0.36 [0.02, 0.70]).
  Per-star offsets are noise (season-to-season correlation 0.24).
- **Short nights (24+ min players, actual < half the prior-10 mean): 2.15% of nights (n 27,072).** Foul-outs 1.2% of them; early-exit
  (injury/ejection proxy) 40%; no visible cause 36%; blowouts no lift. Pre-tip flags: returning from 3+ games out 4.7% vs 2.1%
  (2.2x), first 15 games 1.45x, Elo underdog 1.32x; questionable tag and foul propensity inside noise; starters 1.2% vs bench 6.4%.
- **Deep bench (prior minutes < 12 or < 5 ppg, n 13,559):** 15+ minutes on 37.2% of nights with a rotation teammate OUT at T-60 vs
  22.1% without (diff 15 pts [13, 17]); blowouts no help; the pts forecast runs 0.32 high regardless of OUT status.
- **Who absorbs a missing starter (1,651 team-games with exactly one 24+ min player OUT):** largest gainer takes a median 31% of the
  vacated minutes (9 min). Nothing identifies him pre-tip: same position 43% (chance 38%), hot hand 11% (10%), top bench by minutes
  9% (19%); a third are starters. Once known, he scores +4.8 vs forecast. The lever is the T-30 lineup, not a roster rule.
- **Lines (Pinnacle T-60, 72,674 pairs):** the model is never closer than Pinnacle in any stat x line-position x role cell. As the
  line moves from −0.5 to +0.5 sd of our mean, realised over-rate 0.54 → 0.42, ours 0.57 → 0.31, Pinnacle 0.54 → 0.45: we are
  over-confident about where the line sits. Worst: bench ceiling lines (pts, z 0.5–1: we say 23%, happens 49%, n 331).

### Devin's answers (2026-10-10, ~16:30)
1. Returning teammate costs the star about a point: **yes.** → candidate rule (new pre-registration; pre-tip definition of
   "returning" = absent the last 3+ team games and not on tonight's OUT list).
2. Number of teammates OUT does nothing for the star because the coach spreads the usage: **yes.** Recorded as a confirmed
   null; no rule.
3. Short nights: asked for named examples (reports/model_miss_examples_2026-10-10.md).
4. Deep-bench minutes: **no**, it is driven by things we cannot read (the coach's intent). The injury flag stays the only
   pre-tip signal; no "coach said he'd play" source to build.
5. Absorption: asked for named examples (same report).
6. Lines: Devin's view is that the book's line is usually wrong by **under-predicting something we are not looking at**.
   Open hypothesis; needs the "something" named before it can be a rule.

### Devin, 2026-10-10 ~17:00, after the named examples
- **Minutes restrictions are announced**, but on social media (beat reporters, Twitter), not in the official injury report.
  That is the pre-tip source for the "returning player plays a short night" case. We do not ingest it (would need a feed; Devin's call).
- **Games-played streaks**: some players (Mikal Bridges) suit up for token minutes to keep a consecutive-games streak alive. Explains
  the 0.1-minute nights; tiny n, a note not a rule.
- **Mid-game injuries**: when a man goes down, a smart coach reshapes the whole plan around what is left. Unforecastable pre-tip; it
  is why absorption is not predictable from the roster.
- **Position is the wrong unit**; cluster players into roles (usage, shot profile, rebounding, playmaking) and look at how roles affect
  each other when one is missing. → descriptive clustering analysis queued (modeler, 2026-10-10).
- **Books under-price threes and steals**, because players are priced by reputation (ppg, usage). Checked on Pinnacle T-60, 2023-24 and
  2024-25, pushes dropped, game-clustered CIs (data/scratch; reports/model_miss_examples_2026-10-10.md companion):
  realised over rate MINUS de-vigged over price is negative for every stat: fg3m −0.017 [−0.023, −0.010] (n 20,995), pts −0.013,
  reb −0.023, ast −0.007 (CI spans 0). The over is OVER-priced, not under. By scorer tier it runs with reputation, as Devin says, but
  the other way round: fg3m <10 ppg −0.005 (CI spans 0), 10–17 −0.016, 17–24 −0.027, 24+ −0.019; pts 24+ −0.036 [−0.054, −0.017] vs
  <10 ppg −0.002. The respected names' overs are the mispriced side; the no-names are priced about right. 1–3 points of probability
  is inside Pinnacle's overround (~4–5%), so this is a lean, not a profit. Threes at 2.5/3.5 lines: −0.031/−0.032. Steals: we hold
  no steals prices and have no steals model; a pull would cost roughly 0.25M credits (Devin's call).
- **Pace and the four factors**: the game-winner model carries a pace proxy (points total) and team turnovers; the props model carries
  rolling-20 team points for/against, opponent allowed per stat, rest, b2b, Elo margin. No possession-based pace and no four factors
  (eFG%, TOV%, ORB%, FT rate) in either, as-of or otherwise. A proxy opponent-defence/pace multiplier was tried early and never
  bootstrapped (ledger group F, rejected as a tested result); the opponent ridge (ctxres_v2) was the leak. We now have parsed
  possessions (n_oreb/n_dreb), so true pace and four factors are computable as-of. Not yet tested as a declared family → candidate
  pre-registration FOUR_FACTORS (Devin to confirm).
- **Role clustering (Devin's suggestion), measured 2026-10-10 (reports/role_clusters_2026-10-10.md; descriptive):** k-means roles from
  as-of per-36 profiles (fit on 2023-24, applied to 2024-25). Largest minutes-gainer is in the OUT player's ROLE 17.3% vs 16.3%
  chance at k=6 (+1.0 pts [−0.7, +2.9]); k=8 +0.2 [−1.3, +1.8]; sign flips between seasons. Listed POSITION on the same rows:
  42.8% vs 38.1% (+4.7 [+2.7, +6.9]). One pocket: starting rim big OUT → another rim big is the gainer 17.5% vs 8.3% (n 212), still
  far below useful. Same-role teammates beat the pts model by +0.3 to +0.5 more than other roles. Verdict: not a usable pre-tip rule;
  the lever stays the T-30 lineup.
- **"Route the models through a/b/c/d on the odds results" (Devin, 2026-10-10 ~17:40), piloted the same hour (scratch, Pinnacle T-60,
  pushes dropped):** per-(stat, ppg-tier) additive corrections to Pinnacle's over probability, fit on 2023-24 and applied to 2024-25,
  change log loss by pts +0.0006 (worse), reb −0.0009, ast +0.0001, fg3m −0.0001; the floor is −0.002. The tier pattern is real in
  sign (overs over-priced, most for 24+ ppg scorers: −0.046 in 2023, −0.023 in 2024) but too small and too unstable to route on.
  The one direction with information is where the line sits relative to OUR model's mean: line above our mean → overs hit 3–5 points
  less than priced (reb −0.049, fg3m −0.032, ast −0.030, pts −0.03); line below → +0.5 to +2.0. That is exactly what the frozen
  ODDS_HISTORY blend (H2) measured: passes for reb/Pinnacle only, WOUNDED (half is price bias). "No way they hit the over that much"
  is a judgement the market makes better than we do (Q10: we are over-confident about the line's position in every cell). Null;
  no new rule.
- **Devin, 2026-10-10 ~18:40: "3 is different from 2; the type of make should be categorical; a lot of the numbers should be
  categorical."** Status: production forecasts pts as one number (recency mean plus a residual model; features include the
  player's recent pts/reb/ast/fg3m means but no 2-point, free-throw or attempt split). The possession-level categorical heads
  (rung 4) and the possession sim were tried and not kept for props. A compositional pts model (2s, 3s, FTs as separate
  count heads with a shared minutes/usage draw) has never been pre-registered → candidate PTS_COMPONENTS.
- **F8 lineup rebounding (Devin's "rebounding is a lineup property"): CLOSED as a null, 2026-10-10 (T252–T263).** The expected
  size and rebounding make-up of the projected five does not lower reb or pts CRPS for bigs or overall (reb bigs −0.0009
  [−0.0047, +0.0025]). Devin's HOU observation still shows up as a bias shift (HOU 2024 reb bias −0.105 → +0.016) without a CRPS gain.
  The actual five (oracle) is worth −0.013 reb / −0.029 pts, and the T-30 columns alone −0.018 / −0.043: the lineup matters, the
  projection does not; the lever is the confirmed lineup at T-30, exactly as F11 said.
- **Coach allocation, measured (2026-10-10, descriptive; reports/coach_allocation_2026-10-10.md; 90 coach-seasons, 1,567
  regular-season one-OUT cases, 2022-24).** Rotation habits are real, moderately stable traits (year-to-year r 0.4-0.7: first sub
  279-417 s; starters' share 0.565 Jenkins to 0.738 Thibodeau; Udoka 0.667/0.674 vs league 0.637, first sub ~365 s), but a stable
  coach is also a stable roster. HOW a coach fills an absence is NOT a trait (r −0.2 to +0.3, CIs through 0; 10-15 different top
  gainers per coach-season). Five clusters of what happened: ordinary long-term absence (35%), the redraw with a promoted bench man
  taking half the minutes (22%; Billups over-represented), first night without him (17%; Mazzulla 59% of his cases), the star is
  out and minutes spread (16%; Lue, Rivers, Vogel), replace in kind (11%). Knowing the coach does not predict the cluster next
  season (−0.013 nats per case vs baseline; in-sample association is who got hurt, not the coach). Devin to supply the connecting
  knowledge the data cannot see.
- **Pace and the four factors (Devin's question): tested under a frozen rule the same day, NULL (T264–T279).** As-of pace and
  eFG%/TOV%/ORB%/FT-rate for the team and the opponent add nothing to any of the four props stats (best point −0.0004 vs floor
  −0.005); rebounds get slightly worse. Production's rolling points for/against already carry the pace-and-efficiency information.
- **Points as categorical makes (Devin's "3 is different from 2"): tested under a frozen rule, FAIL (T280–T283).** Separate 2s, 3s
  and FT heads with a shared activity draw are worse than production by 0.03 CRPS in both seasons, worst for 24+ ppg scorers
  (+0.16) and worse in the upper tail at 20/25/30. The independent sum does as well as the shared draw; adding the attempt and
  make-rate features to production ties it. The threes information is already in the model via the player's recent threes; the
  decomposition adds noise faster than structure. Closed.
- **Model routing with "who's been winning lately" weights (Devin): tested under a frozen rule, NO PASS (T284–T299).** The router
  only wins by sending 83–97% of rows to the T-30 lineups arm, and that single arm is as good as the router. Rolling-10 and
  rolling-5 are far worse than production (pts +0.17 / +0.32 CRPS). Nothing to route between; the lever stays the confirmed
  lineup. The live season, with real T-30 snapshots, is where that arm gets its honest test.
- **Returning teammate costs the star ~1.2 pts (Devin: yes), pre-tip gate measured 2026-10-10 ~21:20: NOT KNOWABLE AT T-60.** The
  post-hoc flag (teammate absent 3+ games AND played tonight) is where the −1.17 lived. The T-60 version (absent 3+ team games,
  rotation player, not on tonight's OUT list) fires 4,345 times across 2023-24 and 2024-25 but the flagged player actually plays
  only 16.5% of the time (not-with-team, G-League, unlisted absences are invisible to the report); the star residual under the
  T-60 flag is −0.16 [−0.49, +0.15], n 3,022: null. The information arrives with the active list at T-30. No T-60 rule is frozen
  (non-negotiable 8: a rule that cannot pass its gate is not frozen); the effect is a candidate column for the T-30 lineups arm in
  the live season.
- **Steals (Devin: books under-price them), measured 2026-10-10 ~22:15 on the new player_steals prices (T-60, main line, pushes
  dropped, game-clustered CIs):** the opposite, as with every other stat. Consensus over price 0.505 vs realised 0.474 in 2023-24
  (diff −0.031 [−0.038, −0.024], n 16,069) and 0.506 vs 0.494 in 2024-25 (−0.012 [−0.020, −0.004], n 13,283); DraftKings, FanDuel,
  BetMGM all the same sign. Worst at the 1.5 line in 2023 (realised 0.346 vs priced 0.406). Pinnacle quotes no steals, so no sharp
  benchmark exists. For scale: consensus log loss 0.670/0.672 vs a naive prior-20 forecaster 0.693/0.698 (a coin is 0.693); the
  books know something about steals and the naive forecaster knows nothing. The under is the mispriced side everywhere we have
  looked: pts, reb, ast, fg3m, stl.
- **Coach clusters, Devin's answer (2026-10-11): coaches redraw the rotation or spread the minutes according to the philosophy
  they hold.** The data's side: the redraw-vs-spread choice did not carry from 2023-24 to 2024-25 at the coach level (−0.013 nats
  vs a no-coach baseline), while rotation habits (first sub, starters' share, close-game usage) did (r 0.5–0.7). Reconciliation: the
  philosophy shows in HOW MUCH a coach trusts his starters, which we can measure; which man he picks when one is missing depends
  on the roster he has that night. Recorded; no rule.
- **Minutes as an on-court hazard (Claude's pick from the "team sport as math" discussion): FAIL (T301–T314).** Rotation habit × foul
  trouble × score margin, simulated over game time, is 0.55 min WORSE than the earlier feature-based minutes model M2 and far too
  narrow (80% interval covers 50%); even tonight's actual margin would not close the gap. Mechanisms we can write down do not beat
  features that learn between-game variance. Closed, with the whole "better pre-game minutes" direction: the minutes that matter
  arrive at T-30 (lineups) or from reporters (restrictions).
- **Cross-book arbitrage in the stored prop prices (Devin's question, 2026-10-11; descriptive on 2023-24 to 2025-26 T-60/T-5
  snapshots, 1.12M player-lines with both sides quoted somewhere):** on paper 4.5% of lines show the best over at one book plus the
  best under at another summing under 100%; median margin 1.0%, 12,430 lines at ≥ 2%, 2,901 at ≥ 5% (the largest "margins" are
  stale or erroneous quotes). Most involve the exchanges Novig and ProphetX, whose commission is not in the price; sportsbooks only:
  2.4% of lines, median 1.1%. Rebounds and assists arb most (6%), points 3.4%, steals 2%. Frequency rose from 1.4% (2023-24) to
  5-7% (2025-26) as more books were captured. Staleness check in the fan doc's companion query. Not a line of business here
  (read-only, no orders, non-negotiable 5); recorded as a fact about how dispersed book prices are, which is also why the
  consensus de-vig is a better benchmark than any single book.
  - **Per night, sportsbooks only (exchanges excluded), 2023-24 and 2024-25, best over at one book + best under at another on the
    same line (2026-10-11):** T-60 snapshot 11-13 arbs per night (1.6-2.2 per night at ≥ 2% margin); T-5 snapshot 17-23 per night
    (3.5-5.6 at ≥ 2%). Median margin 0.9%; 90th percentile 2.5%; 99th 6%. Quotes are fresh at the snapshot (median 0.5 min since the
    book's last update, same as non-arb lines), so staleness in the API sense does not explain them; BetRivers is on one side of
    most pairs (slow to follow moves), FanDuel/BetMGM/DraftKings on the other. Two snapshots per game undercount what a
    continuous scanner would see and overstate what survives the seconds between placing two legs.
- **"Craziest parlay the model predicted correctly" (Devin, 2026-10-11; descriptive, OOF model rows × DraftKings T-60 main lines,
  2023-24 and 2024-25):** among 72,321 legs where the model leaned ≥ 55% to a side, 55.9% hit (book implied 53.3%, model's own
  62.7%: over-confident as Q10 found). Best night of model-leaned legs that all hit: 2024-10-23, five legs paying 62x (Grayson
  Allen over 1.5 ast +180, Tari Eason over 0.5 ast +145, Aldama under 5.5 reb, Jackson-Davis under 5.5 reb, Duren under 13.5 pts);
  the model gave the combo 10%, the book 1.2%. The pre-tip STRATEGY version, the model's five most confident legs every night,
  hits all five on 5.5% of 419 nights (book 3.5%), typical payout 18.7x, return per $1 1.15 with night-bootstrap CI [0.71, 1.65];
  k=1..3 return 0.96–1.00; a control picking the book's own favourites returns 0.82 [0.52, 1.17]. Indistinguishable from
  break-even; not a claim; the live parlay shadow log is the only clean test (frozen forward rules).

### Devin's answers to the six questions (2026-10-11 ~10:15)
1. **Load management** is a general practice that varies by team because teams get in trouble (the participation policy); late
   scratches are common; "questionable" is the tag to watch; every team has its own reporters.
2. **Rookie minutes early** are "testing the waters," and the depth of the test depends on whether the team is a lottery team or
   not (lottery teams play rookies; contenders cap them).
3. **A cold start's effect** should be read from other players in his situation (same situation, not the league average).
4. **Whistles** get friendlier for the home team when the away team is good.
5. **Givens:** Jokic 6 rebounds, LeBron 15 points, Curry 2 threes, Anthony Edwards 2 threes.
Measured follow-ups below, each descriptive first.
- **Devin's four givens, measured (2023-24 + 2024-25, games played):** Jokic ≥ 6 reb 96.6% (n 175), LeBron ≥ 15 pts 95.4% (152),
  Curry ≥ 2 threes 92.9% (154), Edwards ≥ 2 threes 76.7% (189). On the 31 nights all four played, all four hit 17 times (54.8%),
  BELOW the 65.6% independence would give; Edwards alone broke it 6 times, Curry 4. So "four givens never happen" is close to
  literally true once a 77% leg is called a given, and even three 93-97% legs together only land ~85% of nights. DraftKings never
  quoted these floor lines at T-60 in the stored data (they live only as deep alternates), so there is no price to compare.
- **Measured against Devin's answers (2026-10-11; reports/fan_answers_2026-10-11.md; 2023-24 + 2024-25; game-clustered CIs):**
  *Load management:* stars sit the second night of a back-to-back 12.4% vs 6.8% other nights (diff +5.6 [+4.1, +7.0]); it is a
  team trait (two-season correlation 0.73; BOS 29%, NOP 28%, UTA 25%, TOR 22%, PHI 20%); 93% of those sit-outs are on the T-60
  report (the model already sees them) but only 2% are on the 11:00 morning report on b2b days vs 78% on other nights, so the
  morning briefing cannot know them. *Questionable:* plays 47.9% of the time (n 3,863); on weak teams 40.8% vs contenders 50.2%
  (Devin's "testing the waters": right direction, −9.4 [−13.3, −5.1]); a questionable player who plays gets 1.6 fewer minutes
  than usual; 44% of rotation DNPs were not out/doubtful at T-60. *Rookies:* weak-team rookies play 19.5 min in the first 15 games
  vs 9.9 on contenders (draft slot matters more: picks 1-5 25.4 min, undrafted 7.7), but the split explains none of the cold-start
  error (eta² 0.001). *Situation prior:* a peer group by team strength × draft band × position is 2.4 min WORSE than the recency
  baseline and no better than pooling all rookies by role. *Whistles:* opposite to the hunch: a stronger away team SHRINKS the
  home free-throw-rate edge (−0.027 [−0.037, −0.015]) and the home team fouls more; it reads as team quality, not venue; home
  players' pts residual does not move with away strength. Candidate rule: team b2b-sit trait + questionable × team strength into
  the P(play) model (Devin to say).
- **Confound check on the b2b sit trait (Devin, 2026-10-11: "Zion and Embiid have injury trouble; size and injury, not really"):**
  on a narrower star definition (prior-10 pts ≥ 20, played the previous game, DNP = zero minutes) stars sit b2b second nights 3.5%
  vs 1.9%; EXCLUDING Zion, Embiid, Barrett and Brown the excess survives (3.2% vs 1.7%, +1.6 points, n 961 b2b star-games), so it is
  not only those four. Per-team excess with the four removed is TOR +15, DET +11, PHI +11, NOP +9, MEM +8 (7-38 b2b star-games per
  team: noisy). Size: sit rates by height and weight tercile are flat (2.0-2.6% all nights; 2.8-4.7% b2b), so "not really" is
  right: no size-injury signal in sit-outs. Rule drafted: docs/prereg/PPLAY_CONTEXT.md.
- **Team back-to-back habit into P(play) (PPLAY_CONTEXT): NO PASS (T316–T317).** The model with the new terms beats the honest
  comparator by 0.010–0.016 log loss, but a placebo that scrambles the team habit across teams does exactly as well: the team term
  is noise, and the whole gain is the T-60 report tag (questionable/doubtful/probable), which that comparator never had. So "teams
  have load-management habits" is true as a description and useless as a forecast once the report is in hand. Side finding: the
  deployed Platt map on P(play) is miscalibrated (worse than the raw probability) and is flagged for Devin.
- **"Retrain on all the old data to see how MOV-Elo works with more data" (Devin): NULL (T318+).** Three more seasons of scores
  change MOV-Elo's 2024-25 forecasts by exactly zero (the ratings forget the extra years by then) and re-tuning its parameters on
  four seasons is within noise. The props extension could not even be tested cleanly because the old injury reports carry era
  identity (T315). More history is not a lever here; injury-Elo stays production.
- **"Best parlay our strongest model would have predicted correctly" (Devin, 2026-10-11; production props model OOF rows × every
  DraftKings two-sided line incl. alternates, 2023-24 + 2024-25):** of 213,998 legs the model's probability beat the book's on
  50.0%; those hit 52.3% vs the book's 50.2% implied (+2.1 points; the model's own average claim 58.2% is the over-confidence we
  know). Best single night of model-favoured legs that all hit: 2024-03-29, ten legs paying 38,366x (Paul Reed, Bam Adebayo,
  Bagley, Sochan under, Tobias Harris, Lowry, Grant Williams, Claxton, Hardaway under, Cam Thomas; mostly threes and assists on
  role players), the model gave the combo 1 in 10,000 and the book 1 in 100,000; the 8-leg cut pays 6,392x, the 5-leg 394x.
  Honest strategy version: the model's 8 largest-edge legs every night hit all 8 on 0.7% of 419 nights (3 nights), typical payout
  160x, return 1.10 per $1 carried entirely by three nights. Hindsight tickets are stories; the only forecast claim in this is the
  +2.1-point hit-rate edge on model-favoured legs, and that is the same small, unproven props edge the ledger already carries.
- **Props as a strict over/under classifier (Devin's reframe, 2026-10-11; DK main lines at T-60, 106,999 legs, 2023-24 + 2024-25):**
  the model picks the right side 54.5% of the time; the book's own favourite side 54.7%; a plain recency average 53.3%; break-even at
  −110 is 52.4%. By stat: threes 57.3% (book 57.8%), rebounds 54.9% (book 54.4%, the one stat where the model edges the book),
  assists 54.1% (55.0%), points 51.7% (51.9%, both near coin flips). Accuracy rises with the model's confidence (51% at coin-flip
  legs → 61% at 70%+) but the model's CLAIMED confidence runs 10-13 points above its accuracy in every bin (claims 75% where it is
  right 61%). When model and book disagree on the side (35% of legs) it is a coin flip (49.7% vs 50.3%); on legs where the book is
  near 50/50 the model is right 51.2%. Reading: as a classifier the model is a slightly worse version of the book's own line with an
  over-confidence problem, except on rebounds; the value framing and the classification framing agree.
- **"Can the model confidently say no to a line?" (Devin, 2026-10-11): NO, not yet.** On DraftKings lines incl. alternates where the
  model claims 80-95% for a side, it is right 54-63% (n 96-506 per bin), about what the book prices those legs at (55-58%); at
  90-95% claimed it is right 54% (n 48). The tails of the integer grid are the most over-confident part of the model, so a model
  "no" carries roughly the book's own probability, minus vig. **Sharp-vs-soft (Pinnacle's main line vs DraftKings' at the same
  minute):** the lines differ on only 12% of player-stats; betting DK toward Pinnacle hits 50.8% (DK implied 50.4%), return 0.94;
  nothing there either (threes n 81 is noise). Both recorded as nulls; the one untried context with real information is the
  market itself as an INPUT (the line, the book's probability, the game total and spread), which the model has never seen → draft
  rule MARKET_LINE_CLASSIFIER.
- **The market as an input (MARKET_LINE_CLASSIFIER): NULL (T320–T324).** Feeding the book's probability, the line, the game total
  and spread and our forecast into one learner is worse than the book on every stat in 2023-24 and within noise in 2024-25; the
  crude one-weight blend beats the clever classifier in every cell. Lesson for the product: show the book's price as the truth
  and our forecast as a small, declared lean, never the other way round.
- **OWN_TAG (the player's own questionable/probable tag into the props model): NULL (T326–T338).** Production already infers the
  −1.6-minute questionable effect from the flag history and the team's absences; the explicit tag is indistinguishable from a
  shuffled one. "Fix the injury features" ends here for the props model: the report's information is fully used at T-60.
- **CALIBRATED_GRID ("make 75% mean 75%"): CLOSED (T339–T339).** Calibration maps on the grid cannot fix the over-confidence at
  the book's lines: a shuffled-label control does as well or better, so the only effect is shrinking toward a coin flip, and the
  confident bins stay at claimed 0.74 → realised 0.56 after every map. The grid is calibrated overall; it is over-confident
  precisely where the book put its line, because the line carries information we do not have. "Confidently say no" at T-60 is
  therefore not available from our side; the book's price is the calibrated number, and our forecast is the lean.
- **"How do EV services show such high EV?" (Devin, 2026-10-11), replicated on our prices (719,628 soft-book legs at DK/FanDuel/
  BetMGM where Pinnacle quotes the same line and side, 2023-24 + 2024-25):** their "EV" is a price comparison, Pinnacle's de-vigged
  probability × the soft book's payout − 1, not a forecast. Flagging legs at ≥ 3% claimed EV (2.0% of all legs; claimed average
  +4.7%) returned +1.5% [−1.2%, +4.1%] at T-60 and +1.6% at T-5; at ≥ 5% (0.6% of legs; claimed +6.7%) +2.4% [−2.4%, +7.0%]; at
  ≥ 10% (109 legs) +9% with a CI from −12% to +32%. About a third of the claimed EV is real on average and the rest is (1) the de-vig
  method (a 2-point change in how Pinnacle's vig is removed moves "EV" by ~4 points), (2) showing the extreme tail of thousands of
  lines a day (selection), (3) the flagged legs hitting at 50.3% while Pinnacle's own de-vigged probability for them is 51.8%, i.e.
  Pinnacle is a touch over-confident on the very lines where soft books disagree with it, (4) stale soft lines that get limited or
  pulled. FanDuel's flagged legs returned 0.99; DraftKings 1.03; BetMGM 1.04. Product note: an honest scanner would show the
  realised third, with the interval, not the claimed number.
- **"Would that make the numbers useful for a parlay?" (Devin, 2026-10-11), same prices, DK + FanDuel vs Pinnacle at T-60, 2023-24 +
  2024-25 (226,510 legs, 418 dates; data/scratch/ev_parlay_2026-10-11.py):** no. Parlay math itself is exact: random legs with claimed
  EV < 0 return −6.0% [−7.7, −4.2] single, −12% / −18% / −26% at 2 / 3 / 4 legs, matching (1+EV)^k within a point at every k. Legs
  flagged at ≥ 3% claimed EV (2.3% of legs, claimed +4.7%) return +0.7% [−3.6, +5.0] single; same-book parlays of flagged legs in
  distinct games: 2-leg +3% [−5, +12] (claimed +9.5%), 3-leg −1.5% [−14, +12] (claimed +15%), 4-leg −5.6% [−25, +16] (claimed +20%).
  At ≥ 5%: +3.4% single, −3.7% / +9% / −34% at 2 / 3 / 4 legs, intervals wider still. Supply is thin: median 6 flagged legs per book
  per night across 3 games, so a 4-leg same-book parlay exists on about half of nights. What the flag does do is keep the worst-priced
  legs out (−26% vs −6% on 4 legs), which is a per-leg price fact, not a parlay edge. Date-block bootstrap, 4,000 reps.
- **Where correlation between legs actually lives (Devin, 2026-10-11, "conditional odds"; descriptive atlas against Pinnacle T-60
  marginals, 72,674 legs, 2,509 games, 2023-24 + 2024-25, game-clustered CIs, reports/joint_atlas_2026-10-11.md):** same player
  across stats is the only large positive dependence (joint over ratio 1.21 [1.19, 1.24]; fg3m × pts 1.53, pts × reb 1.18, i.e. +53
  and +18 cents per $1 on a fair 2-leg parlay). Underdog-team player over × favourite covers is the large NEGATIVE one (ratio 0.82
  [0.78, 0.86] for pts, same for reb and ast: −17 to −18 cents); favourite-team players × cover is near zero. Player over × game
  total over +7 cents pooled (pts, fg3m, ast positive; reb negative). Teammates same stat 0.975 [0.955, 0.994] (small negative,
  starters and bench alike); opponents same or cross stat: independent. Cross-game control: independent to ±0.006. Caveats: legs
  are conditional on the player playing (DNP legs dropped upstream); marginals shifted by each stat's in-sample over-rate bias
  (Pinnacle overs hit 1-2.4 points under their de-vigged price in every stat); 67 cells, ~10 would exclude zero by chance; no price
  comparison, because no API carries same-game parlay prices. What a retail SGP engine charges for these is the open question;
  user slips carry both the per-leg and the combined price, so the app's slip flow would give us the book's correlation factor.
- **"Act as a user: $10 a week on the model" (Devin, 2026-10-11; paper replay, DraftKings main lines at T-60, production props
  OOF rows, habits fixed before results, reports/paper_user_2026-10-11.md):** one model-edge single a night (edge ≥ 5%, ~$1.67 a
  bet) turned $350 of deposits into +$24 [−33, +83] in 2023-24 (207 bets, 52.7% hit) and +$61 [+17, +104] in 2024-25 (212 bets,
  58.0% hit), max drawdown $11 and $9. The bets are 87% unders at ~1.97 decimal: the model is buying the under lean the market
  under-prices, at a retail price (against Pinnacle it has no edge). A $10 weekly 3-leg model parlay hit 4/28 and 3/28 weeks, ended
  +$9 and −$31 with $100-150 drawdowns and a CI of ±$250. Blend singles −$5 / +$24. Controls: book's shortest price −$24 / +$4;
  random leg −$21 / −$18 (the vig); keep your money $0. Eight cells looked at, one CI excludes zero; descriptive, not a claim; the
  live log is the test.
- **"What causes overs to hit; black swan theory" (Devin, 2026-10-11; descriptive, DK T-60 main lines, 106,997 played rows,
  2,616 games, 2023-24 + 2024-25, game-clustered CIs, reports/over_anatomy_2026-10-11.md):** decomposing y − line into a minutes
  part and a per-minute-rate part, minutes carry 20% of the variance for pts [0.194, 0.207], 17% reb, 10% ast, 7% fg3m; the rate
  carries 80-88%. Minutes act as a switch on the over anyway: over rate 0.18 [0.17, 0.19] when the player plays 8+ minutes under his
  trailing-10 mean (n 8,533) and 0.69 [0.68, 0.70] at 8+ over (n 8,531); pts 0.12 vs 0.76. None of that is knowable at T-60 (AUC of
  the over: production p_model 0.556; minutes surplus 0.653; rate surprise 0.932; home/away and starter move the over rate by under
  1 pp). Overtime 0.57 vs 0.47; margin > 20 0.42 vs ≤ 5 0.51; team won 0.52 vs lost 0.44, all post-hoc. Black-swan check: the pts
  upper tail is NOT too thin (realised/predicted P(y > line+k): 0.94 at k=3,5; 0.96 at 8; 1.03 at 12; y ≥ 2×line 0.93 overall, 1.51
  for the top line tercile on ~68 events); reb/ast/fg3m tails are too FAT in the model (0.55-0.90 at the far end). Excess kurtosis
  of standardised pts errors 0.43 [0.31, 0.58] vs −0.18 for the model's own draws: a mild heavy-tail signal that does not show in
  exceedance rates. Caveats: model distribution reconstructed from mean/std (r 0.98-0.99 with p_model); played rows only;
  overtime inferred from team minutes > 252. Reading: the over is decided by things that happen after tip; the pre-tip model is at
  its ceiling on direction, and there is no under-priced fat tail to buy at the main line.
- **Closing-line value of the model's picks (MODEL_CLV, T340-T381, frozen dd16a802, 2026-10-11; Pinnacle and DraftKings, t5 as the
  closing proxy, 2023-24 + 2024-25):** the market moves toward the model's side by +0.001 to +0.004 probability points in the last 55
  minutes, CIs above zero in every cell, but a no-model side rule (pick the side the market's own price level favours) earns +0.002 of
  that and the model beats it by at most +0.001. Floor was +0.005. Closed. The direction of Pinnacle's pre-tip move is predictable (log
  loss 0.673 vs 0.693 constant) from the Pinnacle-vs-DraftKings gap alone; the model adds nothing. Reading: the model is "right" by a
  fifth of a point and 55 minutes early; under half a cent per dollar, not an edge.
- **"Am I better than just taking the over or the under?" (Devin, 2026-10-11; paper user v2, same setup as the $10-a-week replay,
  reports/paper_user_2026-10-11/compliance_results.json):** on the SAME legs the model selects, forcing the under gives +$24 and +$65
  against the model's +$24 and +$61; the paired difference is −$1 [−21, +22] and −$4 [−28, +20]. The model's side is the under 87-91% of
  the time, so its whole value is WHICH legs it flags, not which side: blind under on a random leg each night loses $7 and $6; blind over
  on the model's legs loses $63 and $96; blind over on random legs loses $33 and $38. How to model it: paired baselines on identical legs
  (side value) plus random-leg baselines (selection value), week-resampled CIs; the MODEL_CLV shuffle null is the same idea at the price
  level. Follower who takes the model's leg at rate x (less often when the pick is unlikely: 1.0 / 0.6 / 0.3 of x above 60% / 55% /
  below) and otherwise bets a random over: x = 0.3 loses $18 and $9; x = 0.5 −$5 and +$11; x = 0.7 +$6 and +$33; x = 1 +$24 and +$61.
  Parlay sanity: per-leg hit 0.53-0.58 → a 3-leg parlay hits 15-20% of weeks → 4-5.5 expected hits in 28 weeks; the observed 4 and 3 are
  ordinary, not luck.
- **"Was XGBoost thrown away wrongly?" (Devin, 2026-10-11; XGB_PTS_VS_LT, T382-T388, frozen c74f1d1a):** no. Leak-free XGBoost quantile
  pts vs production + lower-tail mixture, integer support: tie overall (2023 −0.0036 [−0.0094, +0.0019]; 2024 −0.0000 [−0.0063, +0.0060]).
  Complementary halves: XGBoost −0.015 to −0.021 better on players who play ≥ 5 minutes; the mixture +0.16 to +0.18 better on near-DNP rows;
  XGBoost worse in the 2024 playoffs (+0.054) and at 20-30-point thresholds. The count-stat "gains" were integer-support scoring artefacts
  (T114-T117). Closed; a combined arm would be a new question.
- **"Move off proxies: use the market's game odds as the props context; predict points without viewing points" (Devin, 2026-10-11;
  GAME_MARKET_CONTEXT, T389-T414, frozen fd37a3f3):** null. Pinnacle spread and total replacing or joining our Elo margin and 20-game
  scoring rates: every cell within ±0.005 CRPS and within ±0.001 of a within-date placebo; DK over/under log loss unchanged. The market
  margin correlates 0.90 with our Elo margin, so the trees already had it. The arm with no own-stat history is worse by +0.42 CRPS on pts
  (3.58 vs 3.15), +0.17 reb, +0.19 ast, +0.10 fg3m: a player's own history is about 13% of the model and nothing else supplies it.
  *Correction 2026-10-21 (T501, docs/reviews/GMC_null_rederivation_2026-10-21.md):* the null stands at the floor, but "the trees already extract what the market knows from our proxies" is withdrawn: removing all game context also ties production (pts 2024 -0.0008 [-0.0036, +0.0018]), so game context of any kind is below what this model can resolve; a pts gain under about 0.004 is not excluded.

## Play type, momentum, media (2026-10-11, Devin)

- **Type of play:** transition, putbacks, assisted vs self-created makes. *Testable now* from our own play-by-play, as-of per player and
  against the opponent's transition rate allowed; folded into the ADVANCED_RATES draft as the PLAY_TYPE family (not Synergy: its play-type
  numbers are season totals, so not as-of).
- **Momentum and swings:** in-game momentum is not knowable before tip, so it cannot feed a pre-game prop. Pre-game form (streaks, trend,
  volatility) is covered by the evolution representation in ADVANCED_RATES. Prior notes on the hot hand are above (2026-10-09).
- **Media influence:** the measurable pieces are national TV (the one survivor of 14 game-context tests, T180, a candidate only, needs a
  fresh-season rule; `national_tv` is NULL for 77.5% of games, audit F14), revenge games against a former team (needs dated transactions),
  milestones and award races (the 65-game rule above). Press volume or social-media attention has no as-of source we hold. *Queued*, not
  tested: a props version of national TV and revenge games, after dated transactions exist.

## Logical errors the model cannot see, and Devin's fixes (2026-10-21, ~10:30 CDT, after the first slate briefing)
Context: Claude listed eight structural misses (new team / old role; minutes caps on returning players; Questionable treated as nothing; blowouts; rookies on priors; second-night rests; surprise starters; conditional-vs-unconditional display). Devin's responses, each with the ledger's prior evidence:
- **Contract value as the cold-start role prior for movers** ("the contract value kinda tells you what would happen from a cold start"). *New information, known before tip.* Not in the repo (no salary table; nba_api has none; free sources: Basketball-Reference contracts, HoopsHype salaries, ToS to check). Prior evidence: PRESEASON_ROLE_PRIOR T500 FAIL_ONE_SEASON (the role information was real but lumpy: 80-115 movers a season, 5 players carried 98.6% of the gain); FAN_KNOWLEDGE lines 34, 76, 328 on contract incentives and salary-dump context, never tested. Draft rule: ROLE_PRIOR_SALARY: movers' and rookies' first 10 games, salary share of team payroll (or rank within team) as the blend variable for projected minutes and usage; select 2023, report 2024; integer CRPS; game AND player-season clustering; top-5-share guard (BEST_PRACTICES 2026-10-20). Claude's prior P(pass on any stat) 0.15.
- **Minutes caps** ("fix limits on minutes"). *Information from pressers and reporters, not the official report.* No historical cap table exists, so no retrospective test; a live-log rule via the reporter feed's structured `claimed_*` facts (X parser; injection defences not frozen).
- **The coin flip** (Questionable status). PPLAY_INTO_PROPS_LIVE is frozen as a live test; Q plays 0.51, D 0.02, P 0.93 (docs/research/questionable_status_scope_2026-10-15.md); the teammate cascade (a Questionable star's teammates) is not yet a feature: candidate follow-up once the live rule reports.
- **T-30 in the shown forecast.** The T-30 arm exists and is scored (FORWARD_T15/T30); showing it on the page/app is a display decision for Devin; the live comparison T-30 vs T-60 is what the forward prereg measures.
- **Minutes conditional on game script** (blowouts). MINUTES_HAZARD T300-T302: the hazard minutes model lost to the simple one by +0.547 CRPS minutes in 2023; exp_margin is already a props feature. Cheapest next step: a descriptive table of realised minutes vs realised margin by tier on 2023-24 (scratch, no rule) to see whether a margin-conditional minutes mixture has anything to buy.
- **3-in-4, 4-in-5, distance.** WORKLOAD T451-T454 (2026-10-12): "fatigue, schedule density and travel add nothing; what helps is minutes in the last game or few" (the P(play) cell passed, pts did not); rest_days and travel_miles_asof are already features of the team frame. No new rule unless framed as information (e.g. the announced rest decision).
- **Make everything conditional.** The prop forecast is already conditional on playing (mean | plays) with P(play) separate; scoring sets DNP rows aside. As a product: show P(play) x conditional and the void rule beside the conditional number (presentation). As a model: a minutes-first factorisation (minutes distribution x rate) is what the hazard rule tried and lost; the scorecard already decomposes misses into minutes vs rate.
- **"Would it be sustainable to forecast every player?"** We already do: 391 players on 2026-10-21 (every rostered player across 11 games, four stats, every half hour); a warm run takes about 30 s, a cold one 6-7 min. The limits are not compute: the tail rests on priors (rookies, fringe), books price only 10-15 players a game, and the page shows three per team.
Order Claude recommends: (1) ROLE_PRIOR_SALARY draft after the salary source and its licence are checked; (2) the blowout descriptive (scratch); (3) the teammate cascade after PPLAY_INTO_PROPS_LIVE reports; the rest are display or live-log items.
- **Addendum, 2026-10-21 ~12:00 CDT (Devin):** "make sure it's a time-aware salary cap, also accounting for the rules of luxury tax; adding a player into a team at x of the salary cap given different rules would probably affect who gets what role." Folded into the ROLE_PRIOR_SALARY draft: salary as a share of that season's cap (and of the team's payroll, plus rank in team), the team's position against the tax line and the two aprons at signing, and a contract-class category (rookie scale, minimum, exception range, max range) inferred from cap-share bands and years of service; the cap, tax and apron figures per season come from published CBA numbers, cited, with 2026-27 marked projected where it is. The salary data (Basketball-Reference team-season pages, 2022-23..2026-27) was pulled 11:48 CDT on Devin's go.
- **Addendum 2, 2026-10-21 ~12:05 CDT (Devin):** "or if a salary is dead weight." Two cases for the draft: dead money (waived or stretched players still on the payroll: count toward the tax state, exclude from the role prior and the within-team ranks) and dead-weight contracts (a high salary that no longer implies a role: fit the salary-to-role relation with age/years-of-service and prior-season minutes interactions so the data decide, and report the movers whose salary rank and minutes rank disagree).

## Shot-clock usage (2026-10-21 ~13:00 CDT, Devin)
"Are we tracking shot clock usage? If a team uses min/max of the shot clock that changes who would do what, when, where and why." Not a feature today. Derivable from our parsed play-by-play (possession start and shot clocks): team-level early- vs late-clock shot shares as-of, and each player's share of early-clock shots, as proxies for usage timing and pace. Prior evidence: PACE_TOTALS T472 weight 0; tracking-rate features T119-T123 no pass; so the prior for props is low, but the information is pre-tip and ours. First step: a descriptive (scratch, seasons <= 2024) of early-clock share by team and its stability month to month, before any rule. Also logged the same hour: mid-game (live) modelling as a separate product line, with raw live capture starting tonight (hardening item 9) and a Q1-information descriptive in the reading log; and the kitchen-sink diagnostic arm (all tested features, per-slice contrasts) for after the window.

## Playoff games in the history, and game 1 vs game 82 (2026-10-21 ~13:35 CDT, Devin)
"Why are you using playoff games to predict regular-season games, without a flag for recency? A player at game 1 vs
game 82 has different everything. Have we factored that in?" Checked, not guessed:
- Playoff (004) and play-in (005) games are kept by ingest and sit in training and in every recency window with no
  flag. Seasons 2022-2024: 7,004 playoff + 480 play-in of ~104k player-game rows (about 7 percent).
- The production props recency series (nba/props/forward.py `_stat_series`, half-life 10 games) and the
  context-residual decayed means (half-lives 5/10/40/100 games) cross the season boundary and include playoff rows.
  Tonight's forecasts drew on up to 150 prior games (median n_games 150): for a deep-run player at game 1 of a season,
  20 playoff games carry about 75 percent of the half-life-10 recency weight; 10 games into the new season they still
  carry about 37 percent.
- Season position IS represented: player games this season (n_season, resets at the boundary), days since the previous
  game (gap_days: the offseason is visible), team game number, rest/b2b, as-of standings (in_playoff_pos, in_playin),
  tanking incentive, team change. The correction model can learn an early-season adjustment from these; nothing marks
  a row or a history game as a playoff game.
- Measured regime shift, played rows, players with >= 5 playoff games (n 140-153 per season): playoff minutes minus
  their last-10 regular-season minutes averaged -0.4 to -1.6 (p10 about -10, p90 about +6); points -1.4 to -2.1
  (p10 about -6.5, p90 about +3). Stars up, everyone else down: rotations shorten. Heterogeneous, so a flag matters
  more than a global shift.
- Exposure: in the first 7 days of 2023 and 2024, 14-16 percent of played rows (166-173 of ~1,150) were players
  whose latest prior game was a playoff game; about 210 rows per season were players with 10+ playoff games.
- Ledger: no test of a playoff flag or a season-reset recency exists. Related: tree arms were worse in the 2024
  postseason (ENSEMBLE notes near T442-T445), early-season over-forecast on bottom-5 teams (T476 descriptive), the
  SEQ blend scores regular-season targets only but its history includes playoff games (T470).
Verdict: season position yes, playoff regime no; an untested assumption. Recommended: a pre-registered test with
slices (first 10 team games; deep-run players) rather than a season-wide floor, because the effect lives in a few
weeks of the year. Devin decides whether to draft it (DECISIONS when he does).

## Defender-level information (2026-10-21 ~18:15 CDT, Devin)
"If someone is missing, their matchup gets increased production; why is defensive rating by player id not something the model is trained on? Is the profile of the player ever stored anywhere?" Checked: `player_game_matchups` (who guarded whom, matchup minutes, partial possessions, points, FGA/FGM, threes, assists, switches) holds about 245k rows per season for 2022-2024 and NO model or harness reads it (only the ingest and the schema). Defense has been tested at TEAM level only: DEFENSE_CONTEXT (opponent allowed shot profile and rim FG%, tracking contests) NULL for pts and fg3m (T434-T435, ADVANCED_RATES T439); opponent-adjusted ridge was the 10-08 leak (NULL pattern); the early matchup/opponent adjustment and the archetype matchup (M3A, T056-T059) were never properly tested (no CI; REJECTED as evidence / PROVISIONAL); RAPM in the win model PROVISIONAL (T176). Player profiles stored: players_static (draft, first season), recency rates, archetype clusters (cold start). No per-defender rating and no "defender OUT" feature exists. Idea (information, not architecture): for each scorer, the as-of share of his possessions guarded by each opposing defender times that defender's points allowed per matchup possession vs league; when the injury report lists that defender OUT, the feature moves. Testable as a pre-registered rule on 2023/2024 with slices (primary defender out). Devin decides whether to draft it.

## Player "builds" for cold start (2026-10-21 ~18:40 CDT, Devin)
"Use the archetype for the cold start; like a video game (BG3): a level 1-5 player with high potential, player builds based on true data." Two uses. (a) Display: an RPG-style player card in the app (class = archetype, level = role tier from as-of minutes share, potential = draft/age/trajectory, stat bars with sample sizes), sent to the app agent as a design item; descriptive, no claim needed. (b) Forecasting: an archetype-neighbour prior for players with little own history (rookies, new-team players, first 10 games). Prior evidence is against a quick win: archetype-cluster routing was rejected as evidence (selective reporting, FDR audit row D); F9_YOUNG_PICK_PRIOR stopped at its gate (T184); PRESEASON_ROLE_PRIOR closed FAIL_ONE_SEASON (T500); ROLE_PRIOR_SALARY stopped at the gate on power (T504). The common wall is power: cold-start rows are few (tonight 43 forecasts with no history, 49 with fewer than 5 games). A testable version pools every thin-history row (rookies + movers + first 10 games) and combines the archetype build with the salary prior as one "build prior" rule. Queued behind MATCHUP_AVAILABILITY (one rule in flight).

## Parlay entry edge plus "exit velocity" (2026-10-21 ~20:15 CDT, Devin)
"Make parlays at high EV, then create exit velocity and do it that way." Assessment: with fair prices no exit timing adds expected value (a fair price is the expected value; optional stopping cannot beat it). The workable version: enter only where model value > price (all profit is the entry gap); after each leg, recompute model value with live information and exit when the cash-out offer >= model value (edge gone), hold while offer < model value. Early exit locks the edge sooner with less variance; spreads are paid twice; it only works if the model's leg edges are real (unproven: market beats us on winners; props have no live track record). Testable on paper from the live capture (Kalshi in-game prices every minute + ESPN live box) as a pre-registered paper strategy, queued behind ARCHETYPE_MATCHUP_ALL. Tracker gets an "edge gone / edge remains" signal (display only, no orders).

## Devin's live parlay playbook, first night (2026-10-21, ~18:00-23:20 CDT, real money, own decisions)
Positions (Kalshi combos): 6-leg $3.37 -> cashed $7.19 (Miller missed 20, holding would have paid $0); 8-leg $6.19 -> cashed $3.91 (one leg left, Booker benched in a 41-point blowout); 4-leg $3.84 -> won $6.04 (held through a "nearly dead" call; Booker 23, Brooks 10 in garbage time); Lakers 3-leg $6.00 -> won $12.75; 7-leg $2.16 open at write time. About +$10.5 on ~$21 staked. Pelicans moneyline (model's top game edge) lost to the Pacers.
Rules as observed: (1) several combos across games and tip windows; (2) pair one high-probability combo with cheap long shots; (3) cash out when the offer exceeds an informed estimate of the remaining chance (not the market mid); (4) hold positions already marked near zero (selling recovers little, keeps no upside); (5) let the winner run; few, calm calls (memory: live-betting-calm-calls).
Evidence status: one night, n = 5 positions, no CI; luck component named (Phoenix starters played through a blowout). Candidate rules for the paper trader (docs/prereg/PAPER_TRADER.md) as descriptive arms after the freeze; not a claim.
Correction (Devin, 23:40 CDT): Booker (23) and Brooks (10) scored in the 3rd quarter with the starters still playing; it was a blowout but not garbage time. Claude's "nearly dead" call assumed a big early deficit benches starters; the observed pattern is normal starter minutes through the 3rd and benching in the 4th (Booker 29 minutes at the end of the 3rd, none in the 4th). The in-game model must let blowout minutes depend on period, not on margin alone.

## Rotation depth, winning/losing, close-game rebounds (2026-10-23 ~19:50 CDT, Devin)
Prompted by the live residuals (17 finished games, 2026-10-20..23; game-clustered): bench players who play get 4.7 fewer minutes than projected [-5.4, -4.0] (close games too, -4.8), starters -0.05; forecast players expected to play 29.8/game vs 22.5 actual (part is ~4 stale mover rows/game); p_play 0.72 -> played 0.41, 0.85 -> 0.64, 0.98 -> 0.96; per-minute rates right (pts +0.03/min); losing-team pts -1.14 [-1.86, -0.36] vs winners +0.33; starters' reb in close games +0.59 [+0.25, +0.96]. Exploratory, ~40 slices, no multiplicity correction.
Devin: (1) "they run a rotation based on who has energy; if the pace is taxing they play more players; if the team sucks they also play more players." (2) "if you're winning, it's a philosophy to let others eat; if you're losing people kinda give up." (3) "you have to get rebounds to secure possessions, so if the game is closer you do whatever you have to do."
Scratch check, 2022-24 regular season (2025 untouched), close games (|final margin| <= 8), as-of features (prior-10 pace from box possessions, prior win%), depth = players >= 8 min, game-clustered bootstrap B=500: mean depth 8.95; +1 SD prior pace -> +0.167 [+0.141, +0.201] players; +10 pts prior win% -> -0.072 [-0.092, -0.052]; bad teams (<.35) 9.29 vs 8.88 (n 481 / 483). Both mechanisms of (1) hold in direction, but they move depth by ~0.2-0.4 players, far less than the live p_play overstatement (~3 players/game after stale rows): the live miss is mostly something else (likely the 18-man official-roster floor giving fringe/two-way players p_play 0.7-0.85; to check). (2) top-5 players' FGA share falls equally in blowout wins (0.134) and blowout losses (0.133) vs ~0.150 close: bench time on both sides, no visible "give up" asymmetry in shot share; team FG% by margin is outcome-dependent (no causal read). (3) not yet checked.
Prior ledger: pace as a stat feature NULL (FOUR_FACTORS T264-T279, ADVANCED_RATES USAGE_PACE T424-T427); TEAM_BUDGET raking HARMED (T446-T449; roster minutes overcount 36 min/team-game); early-season over-forecast on bottom-5 teams (T476, descriptive). Untested: pace/quality as inputs to WHO plays (p_play / rotation size), not to per-minute rates. Candidate rule (Devin decides whether to draft): ROTATION_DEPTH_PPLAY.
