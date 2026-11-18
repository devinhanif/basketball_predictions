# Kalshi alias review, 2026-11-17

Devin, 2026-11-17: "use websearch and match stats" for the Kalshi prop names with no player link. Market agent,
branch `agent/market-kalshi-aliases`. This follows his instruction (Devin's ask); it is not momentum.

## Result

| Group | Names | 11-16 prop markets covered |
|---|---:|---:|
| Added, evidence 2 from the 11-16 markets (block A) | 29 | 454 |
| Added, evidence 2 from the on-disk 11-17 capture (block B; their teams had no 11-16 game) | 18 | 0 |
| Unresolved | 0 | 0 |
| **Total** | **47** | **454 of 454 unresolved (2,340 prop markets on the slate)** |

The 18 block-B names carry 320 of the 1,434 prop markets in the 11-17 capture (counted from the on-disk pages).

## The rule applied (every name, no exceptions)

A name is added only when both hold:

1. **Identity (web search, query = player name + team only):** a league or major-outlet page shows that player
   on the team of the Kalshi market for 2026-27 (signing, trade, option, extension, or a 2026-27 game note).
   For 13 names the NBA.com player page that came back carries the same numeric id as our table (marked
   `nba.com/player/<id>`), which is an id-level match, not only a name match.
2. **Stats (our data):** in a scratch copy of `data/export/latest/export.duckdb` (export 2026-11-17, data_version
   6edbcef0c89d, opened `read_only`, `season = 2026` rows only), the candidate id has box scores on exactly one
   team this season; that team equals the three-letter team code inside the Kalshi ticker
   (e.g. `KXNBA3PT-26NOV16CHICLE-CHIIOKORO35-1`); his team plays in the market's event that night (our `games`
   table); and each stat ladder is plausible: his season average lies between (lowest rung minus one rung step)
   and the highest rung (steps: pts/PRA 5, reb/ast 2, 3PM/stl/blk 1). All 47 pass every sub-check; the
   normalized key of each new name is unique in the table (no key maps two ways), and each id has one name in
   our player table.

**Honest weight of evidence 2.** Kalshi's ladders are coarse (5-point pts rungs from 5 or 10 up), so "plausible
rungs" rules out little on its own; the load-bearing parts are the ticker's team code agreeing with the team our
box scores put him on, and the event being his team's game.

## Deviations from the brief (stated, chosen before writing the table)

- **18 names had no 11-16 market** (UTA, LAC, CHA, LAL, DET, TOR did not play 11-16). Instead of listing them
  unresolved for want of a market, I applied the identical check to the 11-17 capture already on disk
  (`nba-scratch-prop-candles/live_2026-11-17/kalshi/*`, no request made) and our 11-17 `games` rows. They sit in a
  separate block B in the YAML, so the main session can drop them as one block if it holds to "11-16 only".
- **The three "no candidate" names are in our player table**, not missing: AJ Dybantsa 1643407 (WAS, No. 1 pick
  2026), Meleek Thomas 1643509 (CLE, No. 34), Trevon Brazile 1642350 (DEN, No. 35). nba_api's bundled static list
  lags the 2026 draft class, which is why the candidate generator found nothing; our table has them from the
  official lineup/box feeds and NBA.com's player pages show the same ids. Both checks pass, so they are added;
  no id was invented.

## Evidence table

### A. Evidence 2 from the 11-16 markets

| Kalshi name | Our id / name | Team | Evidence 1 (web) | Evidence 2 (our season-2026 rows; Kalshi rungs) | Markets 11-16 | Decision |
|---|---|---|---|---|---|---|
| AJ Dybantsa | 1643407 AJ Dybantsa | WAS | NBA.com player page nba.com/player/1643407 (Wizards); AP/WTOP: No. 1 pick 2026, debuted as a Wizards starter | 11 gp, 33.4 min, 15.6 pts / 6.1 reb / 2.3 ast / 0.9 3PM. Ticker team WAS, event 26NOV16MEMWAS; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 20/25/30; pts 10/15/20/25/30; reb 2/4/6/8/10; stl 1/2 | 23 | added |
| Anthony Black | 1641710 Anthony Black | ORL | NBA.com bio nba.com/player/1641710 (Magic logo); RotoWire Nov 2026 Magic game notes | 10 gp, 32.2 min, 16.7 pts / 5.2 reb / 5.6 ast / 2.1 3PM. Ticker team ORL, event 26NOV16ORLBOS; rungs ast 2/4/6/8; blk 1/2; fg3m 1/2/3/4/5; pra 20/25/30; pts 10/15/20/25; reb 2/4/6/8; stl 1/2/3 | 25 | added |
| Bones Hyland | 1630538 Bones Hyland | MIN | RotoWire: re-signed with Minnesota 2026-06-30 (Kalshi/RotoWire spelling of Nah'Shon Hyland; an older alias row already maps Nah'Shon Hyland to this id) | 14 gp, 13.7 min, 9.6 pts / 1.4 reb / 3.0 ast / 1.7 3PM. Ticker team MIN, event 26NOV16PORMIN; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6; stl 1/2/3 | 22 | added |
| Cam Spencer | 1642285 Cam Spencer | MEM | CBS Sports / RotoWire Oct 2026: in the mix for the Grizzlies' 2026-27 starting five | 14 gp, 21.7 min, 10.4 pts / 3.2 reb / 5.4 ast / 1.9 3PM. Ticker team MEM, event 26NOV16MEMWAS; rungs ast 4/6/8/10/12; fg3m 1/2/3/4/5; pra 20/25/30; pts 10/15/20; reb 2/4/6/8; stl 1/2/3 | 23 | added |
| Danny Wolf | 1642874 Danny Wolf | BKN | RotoWire 2026-10-26: Nets recalled him, first minutes of 2026-27 | 8 gp, 22.6 min, 9.5 pts / 5.6 reb / 1.9 ast / 2.0 3PM. Ticker team BKN, event 26NOV16PHIBKN; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20/25; pts 5/10/15/20; reb 2/4/6/8 | 20 | added |
| Dean Wade | 1629731 Dean Wade | PHI | NBA.com 'dean-wade-76ers-deal' + AP (WTOP/Fox Sports): 4 yr $39M with the 76ers, June 2026 | 13 gp, 26.6 min, 4.8 pts / 4.6 reb / 1.8 ast / 1.0 3PM. Ticker team PHI, event 26NOV16PHIBKN; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15; reb 2/4/6/8/10 | 19 | added |
| Georges Niang | 1627777 Georges Niang | GSW | ESPN (Charania): Warriors sign Niang, 1 yr, Aug 2026 | 14 gp, 19.6 min, 7.2 pts / 3.1 reb / 1.6 ast / 1.2 3PM. Ticker team GSW, event 26NOV16GSWNOP; rungs fg3m 1/2/3/4/5; pts 5/10/15; reb 2/4/6 | 11 | added |
| Gui Santos | 1630611 Gui Santos | GSW | ESPN / ABC7: Warriors 3 yr $15M extension (Feb 2026); NBC Bay Area 2026-27 piece | 11 gp, 18.7 min, 6.2 pts / 4.2 reb / 1.4 ast / 0.6 3PM. Ticker team GSW, event 26NOV16GSWNOP; rungs fg3m 1/2/3/4; pts 5/10/15; reb 2/4/6/8 | 11 | added |
| Isaac Okoro | 1630171 Isaac Okoro | CHI | ESPN bio id 4432822 (Bulls, #35); Bleacher Nation: Bulls, 2nd season in Chicago | 13 gp, 22.7 min, 6.8 pts / 2.3 reb / 1.7 ast / 0.6 3PM. Ticker team CHI, event 26NOV16CHICLE; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6/8 | 19 | added |
| Jordan Goodwin | 1630692 Jordan Goodwin | PHX | RotoWire / SI (Suns): re-signed with Phoenix, 3 yr $19M, June 2026 | 9 gp, 20.3 min, 9.8 pts / 4.2 reb / 2.7 ast / 2.1 3PM. Ticker team PHX, event 26NOV16PHXDEN; rungs fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6/8; stl 1/2/3 | 19 | added |
| Jordan Poole | 1629673 Jordan Poole | NOP | RotoWire / ESPN: traded to New Orleans June 2025; 2026-27 outlook as a Pelican | 9 gp, 21.6 min, 9.2 pts / 1.9 reb / 3.3 ast / 1.0 3PM. Ticker team NOP, event 26NOV16GSWNOP; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20/25; pts 10/15/20 | 16 | added |
| Jordan Walsh | 1641775 Jordan Walsh | BOS | Boston.com / NBC Boston: Celtics extension July 2026; started the 2026-27 opener | 14 gp, 16.5 min, 5.1 pts / 3.1 reb / 0.6 ast / 0.9 3PM. Ticker team BOS, event 26NOV16ORLBOS; rungs fg3m 1/2/3/4; pts 5/10/15; reb 2/4/6/8 | 11 | added |
| Luka Garza | 1630568 Luka Garza | BOS | RotoWire 2026 outlook: third-string center for the Celtics in 2026-27 (2-yr deal July 2025) | 14 gp, 12.8 min, 5.9 pts / 3.0 reb / 1.7 ast / 1.1 3PM. Ticker team BOS, event 26NOV16ORLBOS; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6/8 | 19 | added |
| Luke Kennard | 1628379 Luke Kennard | PHX | NBA.com 'luke-kennard-suns-deal' + AP: Suns 2 yr $13M, July 2026 | 12 gp, 17.5 min, 9.4 pts / 1.8 reb / 1.8 ast / 2.2 3PM. Ticker team PHX, event 26NOV16PHXDEN; rungs fg3m 1/2/3/4/5/6; pts 5/10/15/20 | 10 | added |
| Meleek Thomas | 1643509 Meleek Thomas | CLE | NBA.com player page nba.com/player/1643509 (Cavaliers); RotoWire: No. 34 pick 2026, signed by Cleveland | 12 gp, 24.0 min, 13.3 pts / 2.9 reb / 1.6 ast / 2.5 3PM. Ticker team CLE, event 26NOV16CHICLE; rungs fg3m 1/2/3/4/5; pts 10/15/20 | 8 | added |
| Noah Clowney | 1641730 Noah Clowney | BKN | SI (Nets): Brooklyn picked up his 2026-27 team option; NBA.com Nets signing release | 13 gp, 21.1 min, 6.9 pts / 4.5 reb / 1.0 ast / 0.9 3PM. Ticker team BKN, event 26NOV16PHIBKN; rungs pts 5/10/15; reb 2/4/6/8 | 7 | added |
| Oso Ighodaro | 1642345 Oso Ighodaro | PHX | NBA.com 'phoenix-suns-extend-oso-ighodaro' + AZFamily: extension through 2031, Oct 2026 | 11 gp, 22.7 min, 6.0 pts / 5.4 reb / 3.5 ast / 0.0 3PM. Ticker team PHX, event 26NOV16PHXDEN; rungs ast 2/4/6; pra 10/15/20; pts 5/10/15; reb 2/4/6/8/10/12/14 | 16 | added |
| Patrick Williams | 1630172 Patrick Williams | CHI | NBA.com bio nba.com/player/1630172 (Bulls logo); CBS Sports: Bulls 5-yr deal (2024). No other NBA player of that name | 6 gp, 22.6 min, 7.8 pts / 3.2 reb / 2.0 ast / 1.2 3PM. Ticker team CHI, event 26NOV16CHICLE; rungs pts 5/10/15; reb 2/4/6/8 | 7 | added |
| Pelle Larsson | 1641796 Pelle Larsson | MIA | NBA.com 'pelle-larsson-extension-heat' + Eurohoops: 4-yr extension with Miami, Sept 2026 | 11 gp, 29.6 min, 11.4 pts / 3.5 reb / 3.5 ast / 1.4 3PM. Ticker team MIA, event 26NOV16HOUMIA; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20/25; pts 10/15/20; reb 2/4/6/8; stl 1/2/3 | 23 | added |
| Robert Williams III | 1629057 Robert Williams III | POR | NBA.com bio nba.com/player/1629057 (Trail Blazers logo); KPTV: Blazers extension June 2026 | 11 gp, 16.4 min, 6.5 pts / 6.5 reb / 1.0 ast / 0.0 3PM. Ticker team POR, event 26NOV16PORMIN; rungs pts 5/10/15; reb 2/4/6/8/10 | 8 | added |
| Sidy Cissoko | 1631321 Sidy Cissoko | POR | Rose Garden Report: Blazers converted him to a standard contract; Wikipedia: Trail Blazers | 13 gp, 23.6 min, 7.6 pts / 3.6 reb / 2.0 ast / 1.3 3PM. Ticker team POR, event 26NOV16PORMIN; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15; reb 2/4/6/8; stl 1/2 | 20 | added |
| Taylor Hendricks | 1641707 Taylor Hendricks | MEM | ESPN (Charania): traded UTA->MEM in the Jaren Jackson Jr. deal, Feb 2026; RealGM RFA 2027 | 9 gp, 12.2 min, 7.4 pts / 3.3 reb / 0.8 ast / 0.9 3PM. Ticker team MEM, event 26NOV16MEMWAS; rungs fg3m 1/2/3/4/5; pts 5/10/15/20; reb 2/4/6/8 | 13 | added |
| Terrence Shannon Jr. | 1630545 Terrence Shannon Jr. | MIN | Spotrac: 4-yr Timberwolves rookie deal; FantasyPros / ESPN game log: Timberwolves | 11 gp, 22.8 min, 13.0 pts / 3.5 reb / 2.8 ast / 1.4 3PM. Ticker team MIN, event 26NOV16PORMIN; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20; pts 10/15/20/25/30/35; reb 2/4/6/8; stl 1/2 | 24 | added |
| Tre Johnson | 1642848 Tre Johnson | WAS | NBA.com Wizards release (No. 6 pick 2025); CBS Sports 2026-27 note (bench role behind Dybantsa); Wizards exercised his option | 13 gp, 23.3 min, 9.6 pts / 2.0 reb / 1.1 ast / 1.4 3PM. Ticker team WAS, event 26NOV16MEMWAS; rungs fg3m 1/2/3/4/5; pts 5/10/15/20 | 9 | added |
| Trevon Brazile | 1642350 Trevon Brazile | DEN | NBA.com player page nba.com/player/1642350 (Nuggets) + Nuggets release 'nuggets-sign-trevon-brazile' (Aug 2026, No. 35 pick) | 12 gp, 19.5 min, 7.1 pts / 5.2 reb / 0.4 ast / 0.8 3PM. Ticker team DEN, event 26NOV16PHXDEN; rungs pts 5/10/15; reb 2/4/6/8/10 | 8 | added |
| Trey Lyles | 1626168 Trey Lyles | MIN | NBA.com bio nba.com/player/1626168 (Timberwolves logo); The Athletic via RotoWire: 1-yr deal with Minnesota, July 2026 | 14 gp, 18.3 min, 6.1 pts / 3.6 reb / 0.9 ast / 0.9 3PM. Ticker team MIN, event 26NOV16PORMIN; rungs pts 5/10/15; reb 2/4/6/8 | 7 | added |
| Tristan da Silva | 1641783 Tristan da Silva | ORL | NBA.com bio nba.com/player/1641783 (Magic logo); Magic exercised his options | 11 gp, 30.6 min, 14.6 pts / 4.1 reb / 1.8 ast / 3.4 3PM. Ticker team ORL, event 26NOV16ORLBOS; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 15/20/25; pts 10/15/20; reb 2/4/6/8; stl 1/2 | 20 | added |
| Tyrese Proctor | 1642878 Tyrese Proctor | CLE | basketball.com.au / News5 Cleveland: Cavaliers, 2nd season, off the bench in 2026-27 | 14 gp, 19.9 min, 9.9 pts / 3.5 reb / 2.8 ast / 1.8 3PM. Ticker team CLE, event 26NOV16CHICLE; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 15/20/25; pts 10/15/20/25; reb 2/4/6/8 | 20 | added |
| Will Richard | 1642954 Will Richard | GSW | RotoWire / KTVU: Warriors (No. 56 pick 2025, multi-year deal) | 13 gp, 18.8 min, 6.0 pts / 2.2 reb / 0.9 ast / 1.1 3PM. Ticker team GSW, event 26NOV16GSWNOP; rungs fg3m 1/2/3/4/5; pts 5/10/15/20; reb 2/4/6/8; stl 1/2/3 | 16 | added |

### B. Evidence 2 from the 11-17 markets

| Kalshi name | Our id / name | Team | Evidence 1 (web) | Evidence 2 (our season-2026 rows; Kalshi rungs) | Markets 11-16 | Decision |
|---|---|---|---|---|---|---|
| Brice Sensabaugh | 1641729 Brice Sensabaugh | UTA | Spotrac / CBS Sports: Jazz exercised his 4th-year option, contract through 2026-27; ESPN (Aug 2026 Josh Green trade) lists him in Utah's 2026-27 wing rotation | 14 gp, 16.8 min, 8.4 pts / 3.2 reb / 1.3 ast / 1.4 3PM. Ticker team UTA, event 26NOV17UTALAC; rungs ast 2/4/6; fg3m 1/2/3/4/5; pts 10/15/20; reb 2/4/6/8 | 0 | added |
| Coby White | 1629632 Coby White | CHA | NBA.com news 'hornets-coby-white-free-agency-2026' + TSN/theScore: re-signed with Charlotte, 3 yr $74M (traded CHI->CHA Feb 2026) | 6 gp, 18.4 min, 11.7 pts / 1.8 reb / 3.2 ast / 1.0 3PM. Ticker team CHA, event 26NOV17CHALAL; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 15/20/25; pts 10/15/20/25; reb 2/4/6 | 0 | added |
| Derrick Jones Jr. | 1627884 Derrick Jones Jr. | LAC | NBA.com bio nba.com/player/1627884 (Clippers logo); Clippers release 'la-clippers-sign-derrick-jones-jr', deal through 2026-27 | 11 gp, 28.8 min, 11.6 pts / 4.8 reb / 1.3 ast / 2.2 3PM. Ticker team LAC, event 26NOV17UTALAC; rungs ast 2/4/6; blk 1/2; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Isaiah Jackson | 1630543 Isaiah Jackson | LAC | CBS Sports 'Clippers' Isaiah Jackson: Traded to LAC' (Feb 2026); RotoWire Nov 7 2026 Clippers injury note | 11 gp, 19.6 min, 8.3 pts / 5.3 reb / 2.1 ast / 0.2 3PM. Ticker team LAC, event 26NOV17UTALAC; rungs blk 1/2/3; pts 5/10/15/20; reb 4/6/8/10 | 0 | added |
| Ja'Kobe Walter | 1642266 Ja'Kobe Walter | TOR | RotoWire 2026-10-20: Raptors exercised his team option (Sportsnet); TSN draft story (Raptors No. 19, 2024) | 13 gp, 30.7 min, 10.8 pts / 4.0 reb / 1.9 ast / 2.1 3PM. Ticker team TOR, event 26NOV17NYKTOR; rungs fg3m 1/2/3/4/5; pts 10/15/20; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Jake LaRavia | 1631222 Jake LaRavia | LAL | SI (Lakers) two-year deal July 2025; RotoWire Oct 2026 Lakers injury/start notes | 13 gp, 25.1 min, 6.1 pts / 3.9 reb / 2.2 ast / 1.1 3PM. Ticker team LAL, event 26NOV17CHALAL; rungs ast 2/4/6; fg3m 1/2/3/4; pra 10/15/20; pts 5/10/15; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Javonte Green | 1629750 Javonte Green | DET | ClickOnDetroit / NBC Sports: returns to Detroit on 1-yr deal, July 2026 | 12 gp, 16.9 min, 6.0 pts / 2.2 reb / 0.5 ast / 1.0 3PM. Ticker team DET, event 26NOV17DETSAS; rungs fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15; reb 2/4/6/8 | 0 | added |
| Josh Green | 1630182 Josh Green | UTA | ESPN / NBC Sports / Salt Lake Tribune: Timberwolves traded Green to the Jazz, Aug 2026 | 12 gp, 19.1 min, 3.7 pts / 1.6 reb / 1.0 ast / 0.7 3PM. Ticker team UTA, event 26NOV17UTALAC; rungs ast 2/4/6; fg3m 1/2/3/4; pts 5/10/15; reb 2/4/6; stl 1/2/3 | 0 | added |
| Josh Okogie | 1629006 Josh Okogie | UTA | NBA.com 'josh-okogie-jazz-free-agency-2026' and NBA.com bio nba.com/player/1629006 (Jazz logo): 2 yr deal July 2026 | 13 gp, 19.3 min, 4.5 pts / 3.4 reb / 1.0 ast / 0.8 3PM. Ticker team UTA, event 26NOV17UTALAC; rungs pts 5/10/15; reb 2/4/6/8; stl 1/2 | 0 | added |
| Kevin Huerter | 1628989 Kevin Huerter | DET | NBA.com bio nba.com/player/1628989 (Pistons logo); ClickOnDetroit / TSN: re-signed 3 yr $27M, June 2026 | 12 gp, 24.4 min, 9.9 pts / 2.5 reb / 2.5 ast / 1.6 3PM. Ticker team DET, event 26NOV17DETSAS; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 15/20/25; pts 10/15/20; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Kris Dunn | 1627739 Kris Dunn | LAC | Sleeper player page (Clippers); RealGM: under contract with the Clippers, UFA 2027. No 2026-27 news item found; contract status is the evidence | 12 gp, 26.9 min, 7.8 pts / 3.8 reb / 4.2 ast / 0.8 3PM. Ticker team LAC, event 26NOV17UTALAC; rungs ast 2/4/6/8; fg3m 1/2/3/4; pra 10/15/20; pts 5/10/15; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Matisse Thybulle | 1629680 Matisse Thybulle | LAL | ESPN (abc7/abc30): 1-yr deal with the Lakers, July 2026; SportsGrid: Lakers media day Sept 2026 | 14 gp, 24.5 min, 7.4 pts / 2.3 reb / 1.0 ast / 1.7 3PM. Ticker team LAL, event 26NOV17CHALAL; rungs ast 2/4/6; blk 1/2; fg3m 1/2/3/4/5; pra 10/15; pts 5/10/15; reb 2/4/6; stl 1/2/3 | 0 | added |
| Max Strus | 1629622 Max Strus | LAC | NBA.com bio nba.com/player/1629622 (Clippers logo); NBC Sports / SportsGrid: traded CLE->LAC Aug 2026; Field Level Media: Clippers debut Nov 2026 | 6 gp, 22.6 min, 9.3 pts / 3.5 reb / 3.8 ast / 2.2 3PM. Ticker team LAC, event 26NOV17UTALAC; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6/8; stl 1/2 | 0 | added |
| Mo Bamba | 1628964 Mo Bamba | UTA | RotoWire / SI (Jazz): 2-yr deal with Utah July 2026 (the older alias row 'Mohamed Bamba' is this id) | 10 gp, 19.5 min, 9.3 pts / 5.8 reb / 1.0 ast / 0.6 3PM. Ticker team UTA, event 26NOV17UTALAC; rungs ast 2/4/6; blk 1/2/3; fg3m 1/2/3/4; pra 20/25/30; pts 10/15/20; reb 6/8/10/12/14; stl 1/2 | 0 | added |
| Paul Reed | 1630194 Paul Reed | DET | Spotrac / RotoWire: Pistons guaranteed his 2026-27 salary; Oct 2026 Pistons preseason notes | 12 gp, 26.6 min, 11.3 pts / 7.8 reb / 2.1 ast / 0.4 3PM. Ticker team DET, event 26NOV17DETSAS; rungs ast 2/4/6; blk 1/2/3; pra 15/20/25; pts 10/15/20; reb 4/6/8/10/12; stl 1/2/3 | 0 | added |
| Ryan Kalkbrenner | 1641750 Ryan Kalkbrenner | CHA | RotoWire: drafted No. 34 by Charlotte 2025; Newsweek (after the 2026 LaMelo Ball trade): Hornets lean on him at center. No 2026-27 game item found | 13 gp, 18.6 min, 4.6 pts / 5.4 reb / 1.5 ast / 0.0 3PM. Ticker team CHA, event 26NOV17CHALAL; rungs pts 5/10/15; reb 2/4/6/8 | 0 | added |
| Sion James | 1642883 Sion James | CHA | CBS Sports fantasy note: bench role for the Hornets in 2026-27 | 13 gp, 26.8 min, 5.8 pts / 3.9 reb / 3.0 ast / 1.1 3PM. Ticker team CHA, event 26NOV17CHALAL; rungs ast 2/4/6/8; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15; reb 2/4/6/8; stl 1/2/3 | 0 | added |
| Ziaire Williams | 1630533 Ziaire Williams | LAL | ESPN (abc7/abc30) + NBC Sports: 1-yr deal with the Lakers, July 2026 | 11 gp, 23.7 min, 10.6 pts / 2.5 reb / 2.1 ast / 1.8 3PM. Ticker team LAL, event 26NOV17CHALAL; rungs ast 2/4/6; fg3m 1/2/3/4/5; pra 10/15/20; pts 5/10/15/20; reb 2/4/6 | 0 | added |

Unresolved: none. Names considered and not needing a row: none.

## What changes when this merges (config on the live path)

The table is read through `nba.kalshi.aliases.load_reviewed_aliases()`; nothing else about the code changes.

- **Kalshi snapshot job** (launchd, every 15 min, `kalshi.duckdb`): at its next run after the merge it upserts the
  open markets of these 47 players with a `player_id` instead of NULL, and `data/kalshi/unmatched_names.json`
  stops listing them. Settled markets already stored with NULL stay NULL (no re-parse is part of this change).
- **Everything that reads `kalshi_markets.player_id`** then sees about 450 more prop markets a night meeting a
  model forecast: the parlay shadow (`nba.parlay evaluate`, `nba.parlay analyze` in the pretip job), market
  capture (`nba.markets capture`) and so the export's `prop_lines` and the app, JARVIS (`nba/jarvis` sources/book,
  in-game), and the parlay assistant's name lookup (`nba/parlay/assistant/names.py`). These are mostly bench and
  role players, so expect more low-line props on the board and in the shadow EV log; the verdict counts
  (including `no_positive_ev_found`) will move because the denominator grows, not because anything got better.
- **Sportsbook name resolution** (`nba.odds.theoddsapi.NameResolver`, used by `nba.pulls.orchestrator` and the
  fastarb Pinnacle reader) uses the same table, so odds rows for these players also start matching.
- **Facts build** (`nba.facts build` in the morning job) lists these display names as entities, and the reporter
  pre-tip parser resolves them; `nba.ingest.reporter_pretip.parser_fingerprint` hashes this file, so the parser
  fingerprint moves (by design; any record pinned to the old fingerprint is a different parser version).
- **Model inputs are not touched.** The forecasting path resolves injury-report names with
  `nba.parse.availability.NameResolver` (nba_api list + rosters), not this table, and no model reads Kalshi or
  odds names. The replay oracle (season-2025 predictions and scores, sha256 89889f6d) is therefore not expected
  to change; it was not run here (watchdog; the maintainer's run). Note the replay passes a Kalshi copy to the
  parlay shadow only when one is given, and the shadow reads stored `player_id`s, so a copy made before the merge
  is unaffected either way.

## A merge blocker in a frozen rule's scorer (not in my area)

T510 / T510b (LINE_MOVE_TOWARD_MODEL, frozen, forward, not yet scored) pin this file at sha256 `4d176c53`
(DECISIONS 2026-10-24 21:38: "later alias changes apply to neither rule"). The scorer
(`research/line_move/__main__.py`) defaults `--aliases` to `configs/kalshi_aliases.yaml` and refuses any other
hash, so after this change:

- `tests/research/line_move/test_dryrun_loaders.py::test_cli_score_does_not_form_statistics_before_the_horizon`
  fails (`REFUSED: alias file ... sha256 78892076 != pinned 4d176c53`; exit 2, expected 3);
- the real T510 score command would refuse unless given the pinned file.

The refusal is the safe failure the rule was built for. To keep the rule scoreable on its pinned file I added an
exact copy, `configs/kalshi_aliases_t510_pin_57164b8.yaml` (bytes from `git show 57164b8:configs/kalshi_aliases.yaml`,
sha256 `4d176c53...` verified). The fix in `research/` belongs to the main session: point the two `--aliases`
defaults (score/preflight and mark-unscoreable) at that copy. It changes no rule text and no number.

## Checks

`ruff check`, `ruff format --check`, `mypy` on the new test: exit 0. pytest (`--no-cov -q -p no:cacheprovider`)
over tests/kalshi, tests/parlay, tests/odds, tests/facts, tests/ingest/test_reporter_pretip.py,
tests/research/line_move: 721 passed, 3 skipped, 1 failed (the T510 pin test above). The new
`tests/kalshi/test_aliases_2026_11_17.py` checks that each added name resolves to the recorded id, that the id
had season-2026 box scores in the export copy (fixture `tests/kalshi/fixtures/alias_season2026_players_2026-11-17.json`,
recorded, no database opened by the test), and that no normalized key in the table maps two ways.
