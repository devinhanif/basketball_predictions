# Rivalries and player histories for F10 — candidate list for Devin to verify (2026-10-09)

Status: **DRAFT.** Nothing here is a result or a frozen rule. Devin marks each row Y/N in the
`Devin` column; only the rows he marks Y go into any test, and the list is frozen (git hash) before
anyone looks at P(play) or residuals by flag.

Item: fan hypothesis F10 (docs/research/README.md; docs/FAN_KNOWLEDGE.md "Questionable" and
"Houston, specifically"): (a) a questionable player is *less* likely to play against a rival
("no need to risk it"); (b) players with FIBA/Olympic/college history against an opponent can
"go off". Rivalries = division opponents + Finals/playoff history.

## Verdict

- **F10a, P(play | questionable) × rivalry flag: PURSUE (as exploration first, then a prereg).**
  Cheap, fully local, uses data we already hold (T-60 injury-report status, schedule, box-score
  played/DNP), and adds pre-tip information the P(play) layer does not have (opponent identity).
- **F10b, pts residual × player-history flag: PARK.** The sample is a few hundred player-games at
  best, the as-of event list must be hand-built, and Devin's own example did not verify as a FIBA
  meeting (see "Corrections"). Keep the list; revisit as a descriptive table once 2026-27 forward
  data exist.

**Expected value vs cost.** Cost is low: a static table (this file, once verified), one join, a
logistic refit of P(play) on questionable rows; local CPU only, zero Colab units (75 left per
configs/cost.yaml). Upside is narrow: questionable rows are a small share of player-games, the
P(play) Platt layer has no confirmatory test yet (PROJECT_STATUS §5), and the prior on
"schedule-fact" features is weak (game-context features did not beat Elo for wins; only
`is_national_tv` survived the game screen, at +0.001 log loss, CONTEXT_SCREEN_GAMES_2026-10-08).
But P(play) errors flow straight into props tails and parlay legs, and the 36% of rotation DNPs
that never appear on a report (PROJECT_STATUS §5) mean any lift on the listed-questionable subset
is worth checking. One confound points the *other* way and must be controlled: since 2023-24 the
Player Participation Policy requires "star" players to be available for national-TV and NBA Cup
games absent injury, and rivalry games are likely over-represented on national TV. Expect a small
or null effect; the chance of a durable gain is modest; the cost is about a day.

## What this repo has already tested

- No rivalry, division, or opponent-history flag has been tested for P(play) or props (searched
  TEST_LEDGER, PROJECT_STATUS, NEXT_OPTIONS, CONTEXT_SCREEN_GAMES, CTXRES_V2).
- Related nulls: game-context features (travel, national TV, playoff race, tanking) for wins;
  location/tip/market-size screen; `is_national_tv` the only tiny survivor (win prob). Opponent
  conference exists in `nba/features/game_context.py` (`TEAM_CONFERENCE`); divisions do not exist
  anywhere in the repo yet.
- P(play): T089/T090 superseded by T096/T097 (T-30 lineups); T160/T161 (injury-Elo T-30).
  questionable→OUT flips between the 11:00 and 17:00 snapshots are documented in
  reviews/redteam_production_2026-10-09.md. Report snapshots must be gated at real tip − 60.

## How to read the table

- Team ids follow the `games` table convention (NBA franchise ids, `configs/team_markets.yaml`).
- `season` in our DB: 2025 = 2025-26. Playoff evidence from 2023-2026 lies *inside* our data window,
  so any "recent playoff series" flag must be **as-of**: a series counts only for games after it ended
  (e.g. HOU–LAL 2026 flags 2026-27 games only).
- Strength (proposed rubric; Devin overrides): **3** = three or more playoff meetings including one
  since 2020, or two or more Finals with overlapping current cores; **2** = a Finals meeting, or two or
  more series since 2015; **1** = a single series, history from before 2010 only, or division alone.
- "Why it matters" states the mechanism and the expected sign under F10a. Each row also carries the
  counter-mechanism where one exists.

---

## Tier 1: division pairs (60 pairs; the baseline flag)

The 2026-27 season keeps 30 teams in six five-team divisions. Expansion (Seattle, Las Vegas) is not in
effect for 2026-27, and sources disagree on whether it starts in 2027-28 or 2028-29. Under the
standard schedule format, division opponents meet four times a season. I did not re-verify that
format for 2026-27 (**unverified**).

Shared note for every division row: four meetings a season makes any single one lower-stakes. F10a
predicts sitting ("no need to risk it"). The counter-mechanism is that regional or national TV, the
crowd and the standings race can pull a player in.

### Atlantic: BOS 1610612738, BKN 1610612751, NYK 1610612752, PHI 1610612755, TOR 1610612761

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| BOS–NYK | division + playoff history | 14 playoff series all-time, 2nd-most of any pairing (2025 R2 NYK 4-2) | 3 | Recent series and both teams contend; the sign is ambiguous | |
| BOS–PHI | division + playoff history | 23 playoff series, the most of any pairing; 2026 R1 PHI 4-3 (PHI's first series win over BOS since 1982) | 3 | LeBron is now in PHI (signed Jul 2026), so national-TV exposure is likely and the participation policy pushes against sitting | |
| NYK–PHI | division + recent playoffs | 2024 R1 NYK 4-2; 2026 R2 NYK 4-0 | 2 | | |
| BKN–NYK | division (same metro) | Same MSA (team_markets.yaml); no playoff evidence checked | 1 | Crosstown game | |
| BOS–BKN | division | — | 1 | | |
| BOS–TOR | division | — | 1 | | |
| BKN–PHI | division + playoffs | 2023 R1 PHI 4-0 | 1 | | |
| BKN–TOR | division | — | 1 | | |
| NYK–TOR | division | — | 1 | | |
| PHI–TOR | division | — | 1 | | |

### Central: CHI 1610612741, CLE 1610612739, DET 1610612765, IND 1610612754, MIL 1610612749

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| CLE–DET | division + recent playoffs | 2026 R2 CLE 4-3 (beat the No. 1 seed) | 2 | Fresh seven-game series | |
| CLE–IND | division + recent playoffs | 2025 R2 IND 4-1 | 2 | | |
| IND–MIL | division + recent playoffs | 2024 R1 IND 4-2; 2025 R1 IND 4-1 | 2 | Giannis left MIL in Jun 2026, so the player edge of this rivalry is gone | |
| CHI–DET | division | Historic (not re-verified here) | 1 | | |
| CHI–CLE | division | — | 1 | | |
| CHI–IND | division | — | 1 | | |
| CHI–MIL | division | — | 1 | | |
| CLE–MIL | division | — | 1 | | |
| DET–IND | division | — | 1 | | |
| DET–MIL | division | — | 1 | | |

### Southeast: ATL 1610612737, CHA 1610612766, MIA 1610612748, ORL 1610612753, WAS 1610612764

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| ATL–CHA | division | — | 1 | | |
| ATL–MIA | division | — | 1 | | |
| ATL–ORL | division | — | 1 | | |
| ATL–WAS | division | — | 1 | | |
| CHA–MIA | division | 2026 play-in CHA 127-126 OT | 1 | A play-in is not a series; Devin decides whether it counts | |
| CHA–ORL | division | 2026 play-in ORL 121-90 | 1 | Same caveat | |
| CHA–WAS | division | — | 1 | | |
| MIA–ORL | division (in-state) | — | 1 | Florida pair | |
| MIA–WAS | division | — | 1 | | |
| ORL–WAS | division | — | 1 | | |

### Northwest: DEN 1610612743, MIN 1610612750, OKC 1610612760, POR 1610612757, UTA 1610612762

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| DEN–MIN | division + playoff history | 2023 R1 DEN 4-1; 2024 R2 MIN 4-3; 2026 R1 MIN 4-2 | 3 | Three series in four seasons; the strongest division rivalry in our data window | |
| DEN–OKC | division + recent playoffs | 2025 R2 OKC 4-3 | 2 | | |
| MIN–OKC | division + recent playoffs | 2025 WCF OKC 4-1 | 2 | | |
| OKC–POR | division | — | 1 | | |
| DEN–POR | division | — | 1 | | |
| DEN–UTA | division | — | 1 | | |
| MIN–POR | division | — | 1 | | |
| MIN–UTA | division | — | 1 | | |
| OKC–UTA | division | — | 1 | | |
| POR–UTA | division | — | 1 | | |

### Pacific: GSW 1610612744, LAC 1610612746, LAL 1610612747, PHX 1610612756, SAC 1610612758

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| LAC–LAL | division (shared arena city) | Same MSA; playoff history not checked | 1 | Devin to rate; "Battle of LA" is a fan label I have not sourced | |
| GSW–LAL | division + recent playoffs | 2023 R2 LAL 4-2 | 2 | Curry vs LeBron's old team; LeBron has left LAL | |
| GSW–SAC | division + recent playoffs | 2023 R1 GSW 4-3 | 1 | | |
| LAC–PHX | division + recent playoffs | 2023 R1 PHX 4-1 | 1 | | |
| GSW–LAC | division | — | 1 | | |
| GSW–PHX | division | — | 1 | | |
| LAL–PHX | division | — | 1 | | |
| LAL–SAC | division | — | 1 | | |
| LAC–SAC | division | — | 1 | | |
| PHX–SAC | division | — | 1 | | |

### Southwest: DAL 1610612742, HOU 1610612745, MEM 1610612763, NOP 1610612740, SAS 1610612759

| Pair | Type | Evidence | Str | Note | Devin |
|---|---|---|---|---|---|
| HOU–SAS | division (Texas) | — (playoff history not checked) | 1 | Devin (Rockets fan) to rate; the regional rivalry he describes | |
| DAL–HOU | division (Texas) | 2015 R1 HOU 4-1 | 1 | Devin to rate | |
| DAL–SAS | division (Texas) | Playoff history not checked | 1 | Devin to rate | |
| HOU–MEM | division | — | 1 | | |
| HOU–NOP | division | — | 1 | | |
| DAL–MEM | division | — | 1 | | |
| DAL–NOP | division | — | 1 | | |
| MEM–NOP | division | — | 1 | | |
| MEM–SAS | division | — | 1 | | |
| NOP–SAS | division | — | 1 | | |

---

## Tier 2: Finals and playoff history pairs (cross-division; 20 candidates)

| # | Pair (ids) | Type | Evidence | Str | Why it would matter for a questionable player | Devin |
|---|---|---|---|---|---|---|
| H1 | LAL 1610612747 – BOS 1610612738 | Finals history | 12 Finals meetings (BOS 9-3; last 2008, 2010) | 3 | The marquee national-TV game: the participation policy pushes stars to play (against F10a). This is the cleanest test of "rivalry → sits" vs "rivalry → TV → plays" | |
| H2 | CLE 1610612739 – GSW 1610612744 | Finals history | Finals 2015-2018, four in a row (GSW 3-1) | 2 | The GSW core (Curry, Green) remains; the CLE side has turned over. Mostly a fan/media rivalry now | |
| H3 | SAS 1610612759 – MIA 1610612748 | Finals history | 2013 MIA 4-3; 2014 SAS 4-1 | 2 | Neither roster overlaps; historic only. Giannis is now in MIA | |
| H4 | NYK 1610612752 – SAS 1610612759 | Finals history (recent) | 1999 SAS 4-1; **2026 NYK 4-1** (Brunson Finals MVP) | 3 | Finals rematch with intact cores (Brunson, Wembanyama). National TV is near-certain, so the policy pushes toward playing | |
| H5 | OKC 1610612760 – IND 1610612754 | Finals history (recent) | 2025 OKC 4-3 | 2 | | |
| H6 | GSW 1610612744 – BOS 1610612738 | Finals history (recent) | 2022 GSW 4-2 | 2 | Both cores largely intact | |
| H7 | DEN 1610612743 – MIA 1610612748 | Finals history (recent) | 2023 DEN 4-1 | 1 | | |
| H8 | BOS 1610612738 – DAL 1610612742 | Finals history (recent) | 2024 BOS 4-1 | 1 | Doncic has left DAL | |
| H9 | LAL 1610612747 – MIA 1610612748 | Finals history | 2020 LAL 4-2 | 1 | | |
| H10 | LAL 1610612747 – DET 1610612765 | Finals history | 1988 LAL 4-3; 1989 DET 4-0; 2004 DET 4-1 | 1 | Historic only | |
| H11 | MIA 1610612748 – DAL 1610612742 | Finals history | 2006 MIA 4-2; 2011 DAL 4-2 | 1 | Historic only | |
| H12 | HOU 1610612745 – GSW 1610612744 | Playoff history | 5 series, all GSW: 2015 WCF 4-1, 2016 R1 4-1, 2018 WCF 4-3, 2019 R2 4-2, 2025 R1 4-3 | 3 | **Rockets.** Fresh seven-game loss (2025) on top of a decade of history; Durant is a former Warrior (that is the "revenge" hypothesis, kept separate) | |
| H13 | HOU 1610612745 – LAL 1610612747 | Playoff history | 10 series (LAL 7-3); 2020 R2 LAL 4-1; **2026 R1 LAL 4-2** | 2 | **Rockets.** Their most recent playoff exit | |
| H14 | NYK 1610612752 – IND 1610612754 | Playoff history | 9 series (IND 6-3); 2024 R2 IND 4-3; 2025 ECF IND 4-2 | 3 | Back-to-back series | |
| H15 | BOS 1610612738 – MIA 1610612748 | Playoff history | 7 series 2010-2024 (ECF 2012, 2020, 2022, 2023); 2024 R1 BOS 4-1 | 3 | | |
| H16 | NYK 1610612752 – MIA 1610612748 | Playoff history | 6 series (1997-2000, 2012, 2023 R2 MIA 4-2) | 2 | | |
| H17 | DEN 1610612743 – LAL 1610612747 | Playoff history | 9 series; 2020 WCF LAL 4-1, 2023 WCF DEN 4-0, 2024 R1 DEN 4-1 | 3 | | |
| H18 | OKC 1610612760 – SAS 1610612759 | Playoff history | 2012 WCF OKC 4-2; 2014 WCF SAS 4-2; 2016 R2 OKC 4-2; **2026 WCF SAS 4-3** | 3 | The top two West teams of 2025-26; likely playoff stakes again | |
| H19 | NYK 1610612752 – CLE 1610612739 | Recent playoffs | 2023 R1 NYK 4-1; 2026 ECF NYK 4-0 | 2 | | |
| H20 | DAL 1610612742 – MIN 1610612750 | Recent playoffs | 2024 WCF DAL 4-1 | 1 | The Edwards–Doncic series, but Doncic is no longer in DAL, so this is a player pair (P1), not a team pair | |

Other single recent series (2023-2026, cross-division) that would get strength 1 if Devin wants them:
LAL–MEM 2023 R1; BOS–ATL 2023 R1; MIA–MIL 2023 R1; PHX–DEN 2023 R2; BOS–CLE 2024 R2; DAL–OKC
2024 R2; DAL–LAC 2024 R1; MIN–PHX 2024 R1; OKC–NOP 2024 R1; CLE–ORL 2024 R1; BOS–ORL 2025 R1;
CLE–MIA 2025 R1; NYK–DET 2025 R1; OKC–MEM 2025 R1; DEN–LAC 2025 R1; MIN–LAL 2025 R1; MIN–GSW
2025 R2; DET–ORL 2026 R1; NYK–ATL 2026 R1; CLE–TOR 2026 R1; OKC–PHX 2026 R1; SAS–POR 2026 R1;
LAL–OKC 2026 R2; MIN–SAS 2026 R2. Several of these are already division pairs (DEN–LAC is not).

Historic-only Finals pairs (strength 1, probably exclude): HOU–BOS (1981, 1986), HOU–NYK (1994),
HOU–ORL (1995), CHI–UTA (1997, 1998), LAL–PHI (1980, 1982, 1983, 2001), SAS–DET (2005), SAS–CLE
(2007), MIL–PHX (2021), TOR–GSW (2019), MIA–OKC (2012), LAL–IND (2000), LAL–ORL (2009).

---

## Tier 3: player-level histories (currently active; for F10b, PARKED)

Team assignments are as of the cited summer-2026 reports or earlier and must be checked against
opening-night rosters. "Both played" means a source confirms both players were on the floor in that
game.

| # | Players (team, verify) | History | Both played? | Str | Note | Devin |
|---|---|---|---|---|---|---|
| P1 | Anthony Edwards (MIN) – Luka Doncic (LAL) | **No FIBA meeting found.** The USA–Slovenia exhibition (Malaga, 12 Aug 2023, USA 92-62) was played *without* Doncic (precaution after a knee knock). The documented history is the **2024 WCF, DAL 4-1** (a friendly handshake after Game 5; the trash talk on record was Doncic–Gobert) | Playoffs: yes; FIBA: **no** | 1-2 | Devin's example. It holds as playoff history, not as FIBA history | |
| P2 | Nikola Jokic (DEN) – 2024 Team USA: Curry (GSW), Durant (HOU), LeBron (PHI), Embiid (PHI), Edwards (MIN), Booker (PHX), Tatum (BOS), Holiday, White (BOS), Haliburton (IND), Adebayo (MIA), Davis (WAS) | Olympic SF 8 Aug 2024: USA 95-91 Serbia (Curry 36; Jokic 17/11/5; USA trailed by 17) | yes (Curry, Jokic named) | 2 | The USA roster is the confirmed 12 with White replacing Kawhi Leonard. Holiday's current team was not checked | |
| P3 | Victor Wembanyama (SAS), Rudy Gobert (MIN) – 2024 Team USA (as P2) | Olympic final 10 Aug 2024: USA 98-87 France (Wembanyama 26; Curry 24) | Wembanyama and Curry: yes | 2 | Gobert's participation in the final was not checked | |
| P4 | Shai Gilgeous-Alexander (OKC), Dillon Brooks (PHX) – 2023 USA World Cup roster: Edwards, Brunson, Haliburton, Bridges, Hart, Reaves, Banchero, Jaren Jackson Jr., Cam Johnson, Kessler, Portis, Ingram | WC bronze game 2023: Canada 127-118 OT (Brooks 39, SGA 31) | Brooks, SGA: yes; USA side per roster | 2 | Brooks's PHX status is confirmed (extension, summer 2026). SGA's team is assumed OKC | |
| P5 | Franz Wagner (ORL), Daniel Theis – 2023 USA WC roster (as P4) | WC SF 2023: Germany 113-111 USA (Obst 24, F. Wagner 22, Theis 21); Germany's first-ever World Cup/Olympic win over the USA | yes (Wagner, Theis) | 2 | Theis's NBA status was not checked | |
| P6 | Alperen Sengun (HOU) – Franz Wagner (ORL), Dennis Schröder (CHA), Tristan da Silva (ORL) | EuroBasket 2025 final: Germany 88-83 Turkey (Sengun 28; F. Wagner 18; Schröder 16/12) | yes | 2 | **Rockets.** Schröder moved to CHA in Aug 2026 | |
| P7 | Alperen Sengun (HOU) – Giannis Antetokounmpo (MIA) | EuroBasket 2025 SF: Turkey 94-68 Greece | yes (Giannis's minutes not checked) | 2 | **Rockets.** Giannis was traded MIL→MIA in Jun 2026 | |
| P8 | Luka Doncic (LAL) – Franz Wagner (ORL), Dennis Schröder (CHA) | EuroBasket 2025 QF: Germany 99-91 Slovenia (Doncic 39; F. Wagner 23; Schröder 20/7) | yes | 2 | | |
| P9 | LeBron James (PHI) – SAS (team) | Finals 2007 (CLE, swept by SAS), 2013 (MIA won 4-3), 2014 (SAS won 4-1) | yes | 2 | Devin's "LeBron vs the Spurs". None of those Spurs remain, so this is a player-vs-franchise flag | |
| P10 | LeBron James (PHI) – Stephen Curry, Draymond Green (GSW) | Finals 2015-2018 (4 meetings), 2023 R2 (LAL 4-2) | yes | 3 | LeBron vs LAL (his former team) is the *revenge* hypothesis, kept separate | |
| P11 | Cameron Boozer (MEM, rookie) – Caleb Wilson (CHI, rookie) | Duke–UNC 2025-26: 7 Feb 2026 UNC 71-68 (Wilson 23; Boozer 24/11); 7 Mar 2026 Duke 76-61 (Wilson **did not play**, thumb) | Feb game: yes | 2 | College rivalry; picks 3 and 4 of the 2026 draft. Rookie rows are cold-start anyway | |
| P12 | Cameron Boozer (MEM) – Darryn Peterson (UTA) | Champions Classic, 18 Nov 2025, Duke 78-66 Kansas: **Peterson did not play** (hamstring) | **no** | 0 | Listed only to show it fails; drop unless another meeting is found | |
| P13 | Bogdan Bogdanovic (HOU per Sept 2026 preview, verify) – 2024 Team USA | Serbia at the 2024 Olympics (SF vs USA, as P2) | not checked | 1 | **Rockets.** Both his roster spot and his minutes in that game need checking | |

Not compiled, and probably worth Devin's input: 2021 Olympic final (France–USA) and the Slovenia–France
semifinal; EuroBasket 2022; NCAA tournament meetings of current players (e.g. the 2018 national final,
Villanova v Michigan with Moritz Wagner, not verified here). Teammates (Villanova, Team USA) are
*not* "past opponents" and are excluded.

### Corrections to the premise

- **Edwards vs Doncic is not a FIBA history.** I could find no game where both played for their
  countries; the one scheduled meeting (Aug 2023) was played without Doncic. Their rivalry is the
  2024 WCF. If F10b is ever tested, its flag needs a "both played" requirement, not "both rosters".
- Several "famous" meetings did not happen on court (P1, P12, and Wilson's absence in the second
  Duke–UNC game). Any automated build from rosters alone would over-flag.

---

## Proposal: how F10 could be tested with our data

Join a frozen, Devin-verified version of this table to every player-game in seasons 2022-2024
(`season` 2025 is burned and is reported only as a label). Rows are T-60 injury-report rows with
status **questionable** (real-tip gate, as in production). Flags: `div_rival` (Tier 1), `hist_rival`
(Tier 2, strength ≥ 2, **as-of**, so a series counts only after it ended), and `any_rival`. Outcome:
played (minutes > 0) vs DNP. First a descriptive table: P(play | questionable) by flag, with
game-clustered bootstrap CIs. Then the real test: does adding the flag to the existing P(play) model
(refit walk-forward, same recipe) lower log loss on questionable rows, controlling for
`is_national_tv`, back-to-back, month, star status (participation-policy definition) and games
played vs the 65-game pace (F2's other half, so the two are not confounded)? F10b would compare
the production props_context_residual OOF pts residual for player-games with a verified
"both played" history flag against all others, matched on minutes bucket. **Sample-size caveat:**
roughly a fifth of each team's schedule is division games (16 of 82, if the four-meeting format
holds), and questionable rows are a small subset of player-games that I have not counted. With a
rivalry share near 25%, a ±5-point CI on a difference in play rates needs about 3,000 questionable
rows in total once player clustering is allowed for (design effect about 1.5). That is plausible
only when 2022-24 are pooled, and then there is no separate report season. F10b has perhaps 10-20
flagged players, each seeing a flagged opponent 2-4 times a season, which gives a few hundred rows
and a pts residual SD near 6-7. Its CI would span about ±1 point, so a "goes off" effect smaller
than that cannot be seen. The 2026-27 forward log is the only clean confirmation.

## Draft pre-registration (DRAFT — only the main session logs it)

- **Hypothesis F10a.** Among players listed questionable at T-60, P(play) is lower when the
  opponent is a verified rival, conditional on national TV, back-to-back, month, star status and
  games-played pace.
- **Primary metric.** Paired per-row log-loss delta of P(play) on questionable rows: (model + flag)
  minus (model + controls without flag). Both arms are refit month-block walk-forward with an
  identical recipe.
- **Practical floor.** Delta ≤ −0.005 on questionable rows (P(play) log loss on this subset is large,
  so the floor sits above the win-prob floor). Also a descriptive play-rate difference of at least
  4 points.
- **CI.** Game-clustered bootstrap, 2,000 resamples, seed 0. A sensitivity run clusters by player.
- **Multiplicity family.** "F10 rivalry flags": {div_rival, hist_rival (strength ≥ 2), any_rival}, BH
  within the family. F2's 65-game-pace feature is a separate family. Run both in the same session
  with the shared controls fixed.
- **Seasons.** Exploration (counts, base rates) on 2022. Select on 2023 (pick at most one of the three
  flags). Report on 2024. If 2024 has fewer than ~1,000 questionable rows, the result is labelled
  descriptive and the decision moves to the 2026-27 forward log.
- **Slices (fixed now).** Star (policy definition) vs non-star; national TV vs not; home vs away;
  first 15 games vs rest; post-All-Star vs before; strength 3 vs 2; Rockets games (descriptive only,
  for Devin).
- **Kill criteria.** (1) Missingness: if report coverage differs between rival and non-rival games
  by more than 2 points, STOP. (2) If the 2023 select-season sign is positive (rivals *more* likely to
  play) with CI excluding 0, record it as the counter-hypothesis (TV/policy) and do not flip the sign
  after the fact. (3) Any slice with n ≥ 100 worse by > +0.01 means NOT KEPT. (4) If the table is
  edited after anyone has seen flag-level outcomes, the run is void.
- **Leakage guards.** The rivalry table is frozen with a git hash before any outcome query. History
  flags are as-of by series end date. The status snapshot is gated at real tip − 60. The outcome
  (played) is never used to build the table.
- **2025 holdout touch.** No; season 2025 is already burned. A pass on 2024 earns a shadow
  column in the daily P(play) log for 2026-27, never a production change from backtest alone.
- **F10b (parked).** It would be a descriptive mean-residual table only, with no pass rule, until
  the 2026-27 forward log has at least 300 flagged player-games.

## What I could not verify

- The four-meetings-per-division schedule format for 2026-27, and the NBA tiebreaker rules (not used
  above).
- Lakers–Clippers, Spurs–Rockets and Spurs–Mavericks playoff histories (left at strength 1 for Devin).
- Opening-night rosters. Player teams come from summer-2026 reports (LeBron PHI, Giannis MIA,
  Schröder CHA, Brooks PHX, Boozer MEM, Wilson CHI, Peterson UTA, Bogdanovic HOU), and Durant's
  contract status is reported inconsistently.
- Minutes played in the FIBA games, except where a source names the player's stats.
- The count of questionable rows in our DB. I did not query it (no data access by design); it is the
  first exploration step.
- Low-quality aggregator pages claimed a Lakers–Celtics 2026 Finals; Wikipedia, ESPN and SNY agree on
  Knicks over Spurs 4-1, which is used here.

## Sources

- [2026 NBA playoffs (Wikipedia)](https://en.wikipedia.org/wiki/2026_NBA_playoffs); [2026 NBA Finals (Wikipedia)](https://en.wikipedia.org/wiki/2026_NBA_Finals); [SNY, Knicks Game 5 clincher](https://sny.tv/articles/knicks-spurs-game-5-nba-finals-takeaways); [Las Vegas Sun, Spurs beat Thunder Game 7](https://lasvegassun.com/news/2026/may/30/wembanyama-spurs-topple-thunder-in-game-7-to-head/)
- [2025 NBA playoffs](https://en.wikipedia.org/wiki/2025_NBA_playoffs); [2024 NBA playoffs](https://en.wikipedia.org/wiki/2024_NBA_playoffs); [2023 NBA playoffs](https://en.wikipedia.org/wiki/2023_NBA_playoffs); [List of NBA champions](https://en.wikipedia.org/wiki/List_of_NBA_champions)
- [Land of Basketball: Rockets vs Warriors](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/rockets_vs_warriors.htm); [Heat vs Knicks](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/heat_vs_knicks.htm); [Celtics vs Heat](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/celtics_vs_heat.htm); [Lakers vs Nuggets](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/lakers_vs_nuggets.htm); [Lakers vs Rockets](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/lakers_vs_rockets.htm); [Knicks vs Pacers](https://www.landofbasketball.com/teams_comparison_all_time/playoffs/knicks_vs_pacers.htm)
- [CBS, 2019 Warriors–Rockets](https://www.cbssports.com/nba/news/2019-nba-playoffs-warriors-vs-rockets-series-schedule-scores-results-tv-channels-live-stream-odds-prediction); [2025 Game 7 Warriors–Rockets](https://www.newschannel10.com/2025/05/06/houston-sent-early-playoff-exit-with-103-89-loss-warriors-game-7/)
- [Inquirer, Sixers–Celtics playoff history (2026)](https://www.inquirer.com/sixers/sixers-celtics-playoffs-history-stats-record-last-win-20260417.html); [abc7ny, Knicks–Pacers round 9](https://abc7ny.com/post/haliburton-pacers-visit-new-york-start-eastern-conference-finals/16477881/); [Daily Thunder, Thunder–Spurs WCF guide](https://www.dailythunder.com/thunder-spurs-western-conference-finals-guide/)
- [NBA.com 2026-27 season preview](https://www.nba.com/2026-27-season-preview); [Yardbarker, realignment after expansion](https://www.yardbarker.com/nba/articles/examining_how_the_nba_should_realign_after_expansion/s1_13132_40911386)
- [Fox LA, LeBron signs with 76ers (24 Jul 2026)](https://www.foxla.com/sports/lebron-james-signs-philadelphia-76ers-free-agency); [Las Vegas Sun, Giannis to Heat](https://lasvegassun.com/news/2026/jun/22/giannis-antetokounmpo-getting-traded-to-heat-in-bl/); [Rotowire, Dillon Brooks](https://www.rotowire.com/basketball/headlines/dillon-brooks-news-new-role-on-tap-for-2026-27-532249); [NBA.com, Schröder to Charlotte](https://nba.com/news/charlotte-cleveland-trade-schroder-mann); [Fantasy Nerds, Rockets 2026-27 preview](https://www.fantasynerds.com/news/story/2026/09/10/rockets-roster-preview-veteran-stars-meet-rising-talent-for-2026-27-season-1619329)
- [NBA.com, USA–Slovenia exhibition 2023](https://www.nba.com/news/team-usas-balance-buries-slovenia-in-fiba-world-cup-exhibition); [KAIT/AP, Doncic-less Slovenia](https://www.kait8.com/2023/08/13/balanced-effort-leads-us-past-doncic-less-slovenia-92-62-world-cup-warm-up-game/); [ABC, 2024 postseason trash talk](https://abc7.com/post/best-trash-talk-2024-nba-postseason/14913695/); [SI, Doncic–Gobert](https://www.si.com/nba/luka-doncic-hilariously-dismisses-question-rudy-gobert-trash-talk)
- [NBC LA, USA–Serbia Olympic SF](https://www.nbclosangeles.com/paris-2024-summer-olympics/team-usa-mens-basketball-serbia-semifinals-score/3483142/); [FMT/AFP, USA–France final](https://www.freemalaysiatoday.com/category/sports/2024/08/11/usa-beat-france-98-87-for-mens-basketball-olympic-gold); [Click2Houston, 2024 USA roster](https://www.click2houston.com/sports/2024/04/17/lebron-curry-durant-embiid-headline-star-studded-2024-us-olympic-mens-basketball-roster/); [ESPN, Leonard withdraws / White replaces](https://africa.espn.com/olympics/story/_/id/40533195/kawhi-leonard-withdraws-team-usa-paris-olympics)
- [CBS, 2023 World Cup results](https://www.cbssports.com/nba/news/2023-fiba-world-cup-scores-results-germany-beats-serbia-for-gold-team-usa-falls-short-of-medal-vs-canada/); [NBC Sports Boston, USA loses to Germany](https://www.nbcsportsboston.com/nba/team-usa-eliminated-from-fiba-world-cup-with-semifinal-loss-to-germany/551827); [SI, 2023 USA World Cup roster](https://www.si.com/nba/2023/07/06/usa-basketball-complete-roster-2023-fiba-world-cup)
- [NBA.com, EuroBasket 2025 final recap](https://www.nba.com/news/eurobasket-2025-final-recap-germany-turkey); [theScore, Turkey routs Greece](https://www.thescore.com/nba/news/3343607/turkey-routs-greece-to-set-up-eurobasket-final-vs-germany); [AP via Click2Houston, Germany beats Slovenia](https://www.click2houston.com/sports/2025/09/10/germany-rallies-to-beat-doncics-slovenia-99-91-and-set-up-eurobasket-semifinal-with-finland/)
- [AP via Castanet, 2026 draft top 3](https://www.castanetkamloops.net/news/Basketball/621296/The-Latest-AJ-Dybantsa-Darryn-Peterson-Cameron-Boozer-go-1-2-3-in-NBA-draft); [AP, UNC 71-68 Duke](https://www.click2houston.com/sports/2026/02/08/seth-trimble-hits-late-3-to-lift-no-14-unc-past-no-4-duke-71-68-in-stunning-rivalry-finish/); [AP, Duke 76-61 UNC](https://www.clickondetroit.com/sports/2026/03/08/boozer-no-1-duke-take-over-after-halftime-to-beat-17th-ranked-rival-north-carolina-76-61/); [AP, Duke 78-66 Kansas](https://www.smdailyjournal.com/sports/cameron-boozer-scores-18-points-as-no-5-duke-outlasts-no-24-kansas-78-66/article_f42aed47-32e8-5d33-a7e1-f2fd263eb19d.html)
- [NBA PR, Player Participation Policy](https://pr.nba.com/nba-board-of-governors-approves-player-participation-policy)
- Repo: docs/FAN_KNOWLEDGE.md, docs/research/README.md (F2, F10), docs/PROJECT_STATUS.md §5,
  docs/CONTEXT_SCREEN_GAMES_2026-10-08.md, docs/reviews/redteam_production_2026-10-09.md,
  docs/INJURY_ELO_T30.md, configs/team_markets.yaml, configs/cost.yaml.
