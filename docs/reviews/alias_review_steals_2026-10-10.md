# Alias review: player_steals unresolved vendor names (2023-24 and 2024-25 pulls)

Status: review draft, 2026-10-10. Nothing here is applied. No config was edited; no commit. Produced read-only from `data/odds/odds_history.duckdb` and `nba.duckdb` (`player_game_stats`, `games`, `season <= 2024`), plus `data/players_static/commonallplayers_2025-26.parquet` for canonical names.

## Scope and counts

Unresolved = `market = 'player_steals'` rows with `player_id IS NULL` in `odds_history` (season 2023: 2,652 rows, season 2024: 30 rows; 2,682 total). 72 distinct vendor strings (71 in 2023, 2 in 2024, one string, Kenyon Martin Jr., in both). All 808 (season, game, name) cells were checked against that game's box-score roster (both teams, because the odds rows carry no team).

| class | vendor names | rows | share of rows |
|---|---:|---:|---:|
| AUTO | 61 | 2,334 | 87.0% |
| REVIEW | 11 | 348 | 13.0% |
| NONE | 0 | 0 | 0.0% |
| total | 72 | 2,682 | 100% |

AUTO includes 20 names (66 rows) with evidence from fewer than 3 games (marked `thin` below); they are unique matches but rest on 1-2 games. NONE is empty: every unresolved string had at least one roster candidate in its games (so the NONE class is 0 names, 0 rows). Unique AUTO keys after the project's name normalisation: 59 (GG Jackson has three spellings that collapse to one key).

## Method

- Tokenise the vendor string with the project normaliser (accents folded, punctuation dropped, parenthetical team tags and surname-first order tolerated by token-set matching).
- Per (season, game_id) take every player in that game's `player_game_stats`; a roster player matches when all tokens of the commonallplayers surname appear in the vendor string and some other token starts with the player's first initial (tier 1). Within tier 1, if any candidate's full first name also appears in the vendor string, only those are kept (this is what separates Brook from Robin Lopez).
- Fallbacks, never AUTO: tier 2 spelling variant (surname similarity >= 0.85 and initial matches), tier 3 surname only (first initial differs, e.g. legal first names), tier 4 partial hyphenated surname.
- AUTO = tier 1, exactly one candidate in every evidenced game, the same player_id in all of them (and in both seasons where a name appears in both). Anything else with a candidate = REVIEW. No candidate = NONE.
- Check run in memory only: with the 59 proposed keys added to the reviewed table, `HistoryResolver` resolves all 61 AUTO strings to the proposed ids; none of the 72 strings resolves today; no proposed key already exists in `configs/kalshi_aliases.yaml`.
- Caveat: roster evidence shows who was in that game's box score, not that the book meant that player; for a 1-game name the check is weak. Team tags in vendor strings like `(IND)` are post-trade tags and can disagree with the team in that game (Daniel Theis played for LAC in the evidence games); they are not used.

## Table

Evidence: n = games with a roster candidate / games the name appears in; teams = team(s) of the proposed player in those games; games = first three game_ids.

| class | vendor name | rows | seasons | proposed player_id | canonical name | evidence | why / note |
|---|---|---:|---|---:|---|---|---|
| AUTO | Brook Robert Lopez | 212 | 2023 | 201572 | Brook Lopez | n=53/53; MIL; games 0022300002, 0022300027, 0022300049 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Caldwell Pope Kentavious | 192 | 2023 | 203484 | Kentavious Caldwell-Pope | n=48/48; DEN; games 0022300006, 0022300024, 0022300034 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Christian James McCollum | 160 | 2023 | 203468 | CJ McCollum | n=40/40; NOP; games 0022300071, 0022300090, 0022300108 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Obadiah Toppin | 160 | 2023 | 1630167 | Obi Toppin | n=53/53; IND; games 0022300001, 0022300019, 0022300039 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Alfred Joel Horford Reynoso | 152 | 2023 | 201143 | Al Horford | n=44/44; BOS; games 0022300031, 0022300043, 0022300053 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Eric Ambrose Gordon Jr. | 148 | 2023 | 201569 | Eric Gordon | n=43/43; PHX; games 0022300015, 0022300035, 0022300041 | unique roster match by last name + first initial in every evidenced game |
| AUTO | J. Isaac | 120 | 2023 | 1628371 | Jonathan Isaac | n=35/35; ORL; games 0022300020, 0022300033, 0022300038 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Cameron Reddish | 98 | 2023 | 1629629 | Cam Reddish | n=31/31; LAL; games 0022300015, 0022300026, 0022300036 | unique roster match by last name + first initial in every evidenced game |
| AUTO | J. Tate | 98 | 2023 | 1630256 | Jae'Sean Tate | n=30/30; HOU; games 0022300011, 0022300037, 0022300048 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Cam Johnson | 90 | 2023 | 1629661 | Cameron Johnson | n=24/24; BKN; games 0022300020, 0022300054, 0022300178 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Nicholas Batum | 84 | 2023 | 201587 | Nicolas Batum | n=27/27; PHI; games 0022300040, 0022300180, 0022300208 | unique roster match by last name + first initial in every evidenced game |
| AUTO | M. Flynn | 64 | 2023 | 1630201 | Malachi Flynn | n=19/19; TOR; games 0022300031, 0022300038, 0022300046 | unique roster match by last name + first initial in every evidenced game |
| AUTO | D. Sharpe | 46 | 2023 | 1630549 | Day'Ron Sharpe | n=15/15; BKN; games 0022300010, 0022300020, 0022300054 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Gregory Jackson II | 46 | 2023 | 1641713 | GG Jackson | n=16/16; MEM; games 0022300597, 0022300621, 0022300638 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Gregory Jackson | 44 | 2023 | 1641713 | GG Jackson | n=17/17; MEM; games 0022300597, 0022300621, 0022300638 | unique roster match by last name + first initial in every evidenced game |
| AUTO | M. McBride | 44 | 2023 | 1630540 | Miles McBride | n=13/13; NYK; games 0022300514, 0022300544, 0022300557 | unique roster match by last name + first initial in every evidenced game |
| AUTO | D. Jones Jr. | 40 | 2023 | 1627884 | Derrick Jones Jr. | n=12/12; DAL; games 0022300006, 0022300014, 0022300106 | unique roster match by last name + first initial in every evidenced game |
| AUTO | D. Wade | 36 | 2023 | 1629731 | Dean Wade | n=13/13; CLE; games 0022300030, 0022300040, 0022300112 | unique roster match by last name + first initial in every evidenced game |
| AUTO | I. Jackson | 36 | 2023 | 1630543 | Isaiah Jackson | n=14/14; IND; games 0022300293, 0022300315, 0022300328 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Kenyon Martin Jr. | 34 | 2023+2024 | 1630231 | KJ Martin | n=9/9; PHI/UTA; games 0022300673, 0022300686, 0022300762 | unique roster match by last name + first initial in every evidenced game |
| AUTO | P. Pritchard | 34 | 2023 | 1630202 | Payton Pritchard | n=12/12; BOS; games 0022300043, 0022300053, 0022300065 | unique roster match by last name + first initial in every evidenced game |
| AUTO | A. Burks | 28 | 2023 | 202692 | Alec Burks | n=9/9; NYK; games 0022300755, 0022300778, 0022300794 | unique roster match by last name + first initial in every evidenced game |
| AUTO | D. Robinson | 28 | 2023 | 1629130 | Duncan Robinson | n=8/8; MIA; games 0022300003, 0022300017, 0022300068 | unique roster match by last name + first initial in every evidenced game |
| AUTO | G. Bitadze | 24 | 2023 | 1629048 | Goga Bitadze | n=7/7; ORL; games 0022300020, 0022300033, 0022300134 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Kevin Devon Knox II | 24 | 2023 | 1628995 | Kevin Knox II | n=10/10; DET; games 0022300453, 0022300474, 0022300502 | unique roster match by last name + first initial in every evidenced game |
| AUTO | R. Covington | 24 | 2023 | 203496 | Robert Covington | n=10/10; LAC/PHI; games 0022300029, 0022300074, 0022300194 | unique roster match by last name + first initial in every evidenced game |
| AUTO | C. Okeke | 22 | 2023 | 1629643 | Chuma Okeke | n=7/7; ORL; games 0022300488, 0022300499, 0022300513 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Omer Faruk Yurtseven | 20 | 2023 | 1630209 | Omer Yurtseven | n=3/3; UTA; games 0022300243, 0022300256, 0022300289 | unique roster match by last name + first initial in every evidenced game |
| AUTO | S. Dinwiddie | 20 | 2023 | 203915 | Spencer Dinwiddie | n=9/9; LAL; games 0022300802, 0022300813, 0022300818 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Daniel Theis (IND) | 18 | 2023 | 1628464 | Daniel Theis | n=5/5; LAC; games 0022300244, 0022300257, 0022300264 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Ishmael Larry Smith | 14 | 2023 | 202397 | Ish Smith | n=6/6; CHA; games 0022300390, 0022300455, 0022300506 | unique roster match by last name + first initial in every evidenced game |
| AUTO | James Huff | 14 | 2024 | 1630643 | Jay Huff | n=4/4; MEM; games 0022400225, 0022400293, 0022400306 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Z. Nnaji | 14 | 2023 | 1630192 | Zeke Nnaji | n=4/4; DEN; games 0022300006, 0022300110, 0022300123 | unique roster match by last name + first initial in every evidenced game |
| AUTO | A. Coffey | 12 | 2023 | 1629599 | Amir Coffey | n=4/4; LAC; games 0022300325, 0022300372, 0022300379 | unique roster match by last name + first initial in every evidenced game |
| AUTO | B. Biyombo | 12 | 2023 | 202687 | Bismack Biyombo | n=3/3; MEM; games 0022300012, 0022300164, 0022300179 | unique roster match by last name + first initial in every evidenced game |
| AUTO | B. Fernando | 12 | 2023 | 1628981 | Bruno Fernando | n=4/4; ATL; games 0022300777, 0022300804, 0022300835 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Immanuel Quickley (TOR) | 12 | 2023 | 1630193 | Immanuel Quickley | n=3/3; TOR; games 0022300470, 0022300492, 0022300504 | unique roster match by last name + first initial in every evidenced game |
| AUTO | K. Caldwell-Pope | 10 | 2023 | 203484 | Kentavious Caldwell-Pope | n=3/3; DEN; games 0022300893, 0022300906, 0022300922 | unique roster match by last name + first initial in every evidenced game |
| AUTO | G. Antetokounmpo | 8 | 2023 | 203507 | Giannis Antetokounmpo | n=2/2; MIL; games 0022300899, 0022300915 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | M. Moody | 8 | 2023 | 1630541 | Moses Moody | n=3/3; GSW; games 0022300062, 0022300108, 0022300126 | unique roster match by last name + first initial in every evidenced game |
| AUTO | S. Gilgeous-Alexander | 8 | 2023 | 1628983 | Shai Gilgeous-Alexander | n=2/2; OKC; games 0022300900, 0022300914 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | T. Jackson-Davis | 8 | 2023 | 1631218 | Trayce Jackson-Davis | n=3/3; GSW; games 0022300907, 0022300921, 0022300936 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Martin Kenyon Jr. | 6 | 2023 | 1630231 | KJ Martin | n=2/2; PHI; games 0022300762, 0022300779 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | N. Alexander-Walker | 6 | 2023 | 1629638 | Nickeil Alexander-Walker | n=3/3; MIN; games 0022300903, 0022300911, 0022300932 | unique roster match by last name + first initial in every evidenced game |
| AUTO | Jevon Carter (Chi) | 4 | 2023 | 1628975 | Jevon Carter | n=1/1; CHI; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | P. J. Tucker | 4 | 2023 | 200782 | P.J. Tucker | n=1/1; PHI; games 0022300092 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Simone Fontecchio (Det) | 4 | 2023 | 1631323 | Simone Fontecchio | n=1/1; DET; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | T. J. Warren | 4 | 2023 | 203933 | T.J. Warren | n=2/2; MIN; games 0022300987, 0022300995 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Z. Williams | 4 | 2023 | 1630533 | Ziaire Williams | n=1/1; MEM; games 0022300071 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Ausar Thompson (Det) | 2 | 2023 | 1641709 | Ausar Thompson | n=1/1; DET; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Drew Eubanks (Phx) | 2 | 2023 | 1629234 | Drew Eubanks | n=1/1; PHX; games 0022300247 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Evan Fournier (Det) | 2 | 2023 | 203095 | Evan Fournier | n=1/1; DET; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | GREGORY JACKSON II | 2 | 2023 | 1641713 | GG Jackson | n=1/1; MEM; games 0022300597 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Green A J | 2 | 2023 | 1631260 | AJ Green | n=1/1; MIL; games 0022300790 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | I. Smith | 2 | 2023 | 202397 | Ish Smith | n=1/1; CHA; games 0022300713 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Ibou Dianko Badji | 2 | 2023 | 1630641 | Ibou Badji | n=1/1; POR; games 0022300741 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | K. Lewis Jr. | 2 | 2023 | 1630184 | Kira Lewis Jr. | n=1/1; NOP; games 0022300129 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Kyle Lowry (Phi) | 2 | 2023 | 200768 | Kyle Lowry | n=1/1; PHI; games 0022300871 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Malachi Flynn (Det) | 2 | 2023 | 1630201 | Malachi Flynn | n=1/1; DET; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Quentin Grimes (Det) | 2 | 2023 | 1629656 | Quentin Grimes | n=1/1; DET; games 0022300839 | unique roster match by last name + first initial in every evidenced game (thin) |
| AUTO | Trendon Watford (Por) | 2 | 2023 | 1630570 | Trendon Watford | n=1/1; BKN; games 0022300339 | unique roster match by last name + first initial in every evidenced game (thin) |
| REVIEW | J. Green | 90 | 2023 | 1630224 | Jalen Green | n=29/29; HOU; games 0022300037, 0022300048, 0022300122 | ambiguous: 2+ candidates in some game; candidates: Jalen Green 1630224 in 29 games; Jeff Green 201145 in 29 games |
| REVIEW | Bojan Bogdanovich | 88 | 2023 | 202711 | Bojan Bogdanovic | n=23/23; DET; games 0022300283, 0022300352, 0022300374 | spelling variant (fuzzy last name, initial matches) |
| REVIEW | Danilo Galinari | 58 | 2023 | 201568 | Danilo Gallinari | n=17/17; WAS; games 0022300003, 0022300009, 0022300028 | spelling variant (fuzzy last name, initial matches) |
| REVIEW | Trayce Jackson | 34 | 2023 | 1631218 | Trayce Jackson-Davis | n=11/11; GSW; games 0022300402, 0022300426, 0022300444 | last name only (first initial differs); candidates: Trayce Jackson-Davis 1631218 in 7 games; Reggie Jackson 202704 in 2 games; Jaren Jackson Jr. 1628991 in 1 games; GG Jackson 1641713 in 1 games; Andre Jackson Jr. 1641748 in 1 games |
| REVIEW | C. Martin | 30 | 2023 | 1628998 | Cody Martin | n=13/13; CHA; games 0022300428, 0022300436, 0022300455 | ambiguous: 2+ candidates in some game; candidates: Cody Martin 1628998 in 13 games; Caleb Martin 1628997 in 1 games |
| REVIEW | T. Young | 14 | 2023 | 201152 | Thaddeus Young | n=7/7; TOR; games 0022300516, 0022300526, 0022300540 | ambiguous: 2+ candidates in some game; candidates: Thaddeus Young 201152 in 7 games; Trae Young 1629027 in 1 games |
| REVIEW | Edrice Femi Adebayo | 12 | 2023 | 1628389 | Bam Adebayo | n=3/3; MIA; games 0022300842, 0022300855, 0022300867 | last name only (first initial differs) |
| REVIEW | Aleksandar Vezenkov | 8 | 2023 | 1628426 | Sasha Vezenkov | n=1/1; SAC; games 0022300137 | last name only (first initial differs) |
| REVIEW | Mil. Bridges | 8 | 2023 | 1628970 | Miles Bridges | n=2/2; CHA; games 0022300202, 0022300217 | truncated first-name token "Mil." (Miles vs Mikal); only Miles Bridges is on a roster in the 2 games |
| REVIEW | Nah'Shon Hyland | 4 | 2023 | 1630538 | Bones Hyland | n=1/1; LAC; games 0022300160 | last name only (first initial differs) |
| REVIEW | Thompson | 2 | 2023 | 202684 | Tristan Thompson | n=1/1; CLE; games 0022300396 | last name only (first initial differs) |

## REVIEW decisions needed (what I would check)

- J. Green (90 rows): Jalen Green and Jeff Green were both on the Houston roster in all 29 games; the string cannot be settled from rosters. Needs a book or price-level check (for example, which one has a steals line near his rate) or leave unresolved.
- C. Martin (30 rows) and T. Young (14 rows): 12/13 and 6/7 games have only Cody Martin / Thaddeus Young as a candidate, but one game each has a second candidate (Caleb Martin, Trae Young). Likely Cody and Thaddeus; that single game is unresolved.
- Bojan Bogdanovich, Danilo Galinari: spelling variants of Bojan Bogdanovic (202711) and Danilo Gallinari (201568); unique roster candidate in every game. Would be AUTO but for the fuzzy surname. Low risk, human eyes only because the spelling rule is fuzzy.
- Trayce Jackson (34 rows): Trayce Jackson-Davis (1631218) is the intended player (his first name matches; 'Davis' dropped), but the surname-only tier also lists other Jacksons; the proposed id is the most frequent, not the first-name match. Almost certainly 1631218.
- Mil. Bridges (8 rows): Miles Bridges (1628970) is the only Bridges on those two rosters, but a truncated first token is not a name; Mikal Bridges (1628969) is the alternative.
- Edrice Femi Adebayo (Bam Adebayo, 1628389), Aleksandar Vezenkov (Sasha Vezenkov, 1628426), Nah'Shon Hyland (Bones Hyland, 1630538): legal first names; unique surname on the roster in 1-3 games.
- Thompson (2 rows): surname only, one game; Tristan Thompson is the roster candidate but the string carries no first name. Leave unresolved.

## YAML to paste into configs/kalshi_aliases.yaml (AUTO class only)

Paste under `aliases:`. Keys are normalised on load, so duplicate spellings collapse; one line per normalised key is given (the highest-row spelling). Lines marked thin rest on 1-2 games. Review before pasting; the maintainer decides.

```yaml
  # Reviewed 2026-10-10 (maintainer to confirm): vendor spellings in the player_steals 2023-24 / 2024-25 pulls,
  # unique roster match by surname + first initial in every appearance game (docs/reviews/alias_review_steals_2026-10-10.md)
  "Brook Robert Lopez": 201572  # Brook Lopez, n=53 games
  "Caldwell Pope Kentavious": 203484  # Kentavious Caldwell-Pope, n=48 games
  "Christian James McCollum": 203468  # CJ McCollum, n=40 games
  "Obadiah Toppin": 1630167  # Obi Toppin, n=53 games
  "Alfred Joel Horford Reynoso": 201143  # Al Horford, n=44 games
  "Eric Ambrose Gordon Jr.": 201569  # Eric Gordon, n=43 games
  "J. Isaac": 1628371  # Jonathan Isaac, n=35 games
  "Cameron Reddish": 1629629  # Cam Reddish, n=31 games
  "J. Tate": 1630256  # Jae'Sean Tate, n=30 games
  "Cam Johnson": 1629661  # Cameron Johnson, n=24 games
  "Nicholas Batum": 201587  # Nicolas Batum, n=27 games
  "M. Flynn": 1630201  # Malachi Flynn, n=19 games
  "D. Sharpe": 1630549  # Day'Ron Sharpe, n=15 games
  "Gregory Jackson II": 1641713  # GG Jackson, n=16 games
  "M. McBride": 1630540  # Miles McBride, n=13 games
  "D. Jones Jr.": 1627884  # Derrick Jones Jr., n=12 games
  "D. Wade": 1629731  # Dean Wade, n=13 games
  "I. Jackson": 1630543  # Isaiah Jackson, n=14 games
  "Kenyon Martin Jr.": 1630231  # KJ Martin, n=9 games
  "P. Pritchard": 1630202  # Payton Pritchard, n=12 games
  "A. Burks": 202692  # Alec Burks, n=9 games
  "D. Robinson": 1629130  # Duncan Robinson, n=8 games
  "G. Bitadze": 1629048  # Goga Bitadze, n=7 games
  "Kevin Devon Knox II": 1628995  # Kevin Knox II, n=10 games
  "R. Covington": 203496  # Robert Covington, n=10 games
  "C. Okeke": 1629643  # Chuma Okeke, n=7 games
  "Omer Faruk Yurtseven": 1630209  # Omer Yurtseven, n=3 games
  "S. Dinwiddie": 203915  # Spencer Dinwiddie, n=9 games
  "Daniel Theis (IND)": 1628464  # Daniel Theis, n=5 games
  "Ishmael Larry Smith": 202397  # Ish Smith, n=6 games
  "James Huff": 1630643  # Jay Huff, n=4 games
  "Z. Nnaji": 1630192  # Zeke Nnaji, n=4 games
  "A. Coffey": 1629599  # Amir Coffey, n=4 games
  "B. Biyombo": 202687  # Bismack Biyombo, n=3 games
  "B. Fernando": 1628981  # Bruno Fernando, n=4 games
  "Immanuel Quickley (TOR)": 1630193  # Immanuel Quickley, n=3 games
  "K. Caldwell-Pope": 203484  # Kentavious Caldwell-Pope, n=3 games
  "G. Antetokounmpo": 203507  # Giannis Antetokounmpo, n=2 games thin
  "M. Moody": 1630541  # Moses Moody, n=3 games
  "S. Gilgeous-Alexander": 1628983  # Shai Gilgeous-Alexander, n=2 games thin
  "T. Jackson-Davis": 1631218  # Trayce Jackson-Davis, n=3 games
  "Martin Kenyon Jr.": 1630231  # KJ Martin, n=2 games thin
  "N. Alexander-Walker": 1629638  # Nickeil Alexander-Walker, n=3 games
  "Jevon Carter (Chi)": 1628975  # Jevon Carter, n=1 games thin
  "P. J. Tucker": 200782  # P.J. Tucker, n=1 games thin
  "Simone Fontecchio (Det)": 1631323  # Simone Fontecchio, n=1 games thin
  "T. J. Warren": 203933  # T.J. Warren, n=2 games thin
  "Z. Williams": 1630533  # Ziaire Williams, n=1 games thin
  "Ausar Thompson (Det)": 1641709  # Ausar Thompson, n=1 games thin
  "Drew Eubanks (Phx)": 1629234  # Drew Eubanks, n=1 games thin
  "Evan Fournier (Det)": 203095  # Evan Fournier, n=1 games thin
  "Green A J": 1631260  # AJ Green, n=1 games thin
  "I. Smith": 202397  # Ish Smith, n=1 games thin
  "Ibou Dianko Badji": 1630641  # Ibou Badji, n=1 games thin
  "K. Lewis Jr.": 1630184  # Kira Lewis Jr., n=1 games thin
  "Kyle Lowry (Phi)": 200768  # Kyle Lowry, n=1 games thin
  "Malachi Flynn (Det)": 1630201  # Malachi Flynn, n=1 games thin
  "Quentin Grimes (Det)": 1629656  # Quentin Grimes, n=1 games thin
  "Trendon Watford (Por)": 1630570  # Trendon Watford, n=1 games thin
```

## Reparse (after the maintainer applies the YAML)

Cache-only (0 credits, keyless):

```
python -m nba.odds history-pull --season 2023 --phase props --markets player_steals --reparse
python -m nba.odds history-pull --season 2024 --phase props --markets player_steals --reparse
```

Expected effect if only the AUTO lines are applied: 2,334 of the 2,682 unresolved rows gain a player_id; 348 stay NULL (REVIEW class). Not run here.

## Honest note

This is a name-resolution cleanup of already-pulled prices; it does not say anything about edge. No price, forecast or EV was read.
