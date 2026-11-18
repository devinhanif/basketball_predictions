# Adversary review: 47 Kalshi player-name aliases (agent/market-kalshi-aliases @ 4fc49c3e), 2026-11-17

Verdict: BLOCKED

**Call: WOUNDED.** The 47 alias rows **survive**: 47 confirmed, 0 doubtful, 0 wrong. For the 29 block-A names, our
box scores match Kalshi's settled outcomes on **411 of 411** decided markets. Swapping in a teammate on the same
markets matches 63.6%. I found no title that resolves to the wrong id after this change.

The branch is blocked by one thing that has nothing to do with the names. The scorer edit
(`research/line_move/__main__.py`) changes the code hash of the frozen T510/T510b scorer, so the committed and
reviewed dryrun record no longer matches. After a merge, `score` and `preflight` refuse with `DryrunMissing`. The
smallest fix drops that edit (B1). Once B1 is fixed, the verdict becomes SURVIVES and the alias rows can merge
unchanged.

Model class: none (a reviewed lookup table on the live path, not a model). Playbook check at the end.

## Top finding

**B1 (blocker, merge only; it fails closed).** The edit is at `research/line_move/__main__.py:62-66` and `:393`
/ `:406` (commits b97dd4f7 and 4fc49c3e). `spec.code_sha256()` hashes every `*.py` in `research/line_move/`
(`spec.py:103-113`), so `__main__.py` is part of it. `dryrun.require_pass` (`dryrun.py:335`) refuses whenever the
record's hash differs from the current code.

- main: code sha `c34f7e5c33f4`, equal to `reports/line_move/dryrun_record.json` (`c34f7e5c…`).
- branch: `7f9ce47d8d44`.
- On the branch: `require_pass(spec.DRYRUN_RECORD, 'T510')` gives `DryrunMissing: dryrun record does not match
  the current selection/analysis code`, and `failed(T510)` gives `False`.

So after merge, T510 can be neither scored nor marked unscoreable until the dryrun is re-run, re-committed and
re-reviewed. T510 has no result yet (TEST_LEDGER line 759), and its 14-date horizon has opened. The test suite
does not catch this: `test_cli_score_does_not_form_statistics_before_the_horizon` writes its own temporary record
(`tests/research/line_move/test_dryrun_loaders.py:110`).

**Fix (smallest):**
1. Revert the `__main__.py` hunks (b97dd4f7 and 4fc49c3e) and keep `configs/kalshi_aliases_t510_pin_57164b8.yaml`.
2. Change `test_dryrun_loaders.py:110` to pass `"--aliases", str(REPO / "configs" /
   "kalshi_aliases_t510_pin_57164b8.yaml")`.
3. Draft a DECISIONS row: "T510/T510b score, preflight and mark-unscoreable are run with `--aliases
   configs/kalshi_aliases_t510_pin_57164b8.yaml` (bytes 4d176c53, the 2026-10-24 21:38 pin)".

The code hash then returns to `c34f7e5c` and the record is valid again. The alternative is to keep the edit,
re-run `python -m research.line_move dryrun`, commit the new record, and get an adversary diff, as with R5. That is
more work for the same result.

## Check 1: every alias, rebuilt independently

Commands. Scripts are in the scratchpad, not in `nba/`. DB: a fresh copy of `data/export/latest/export.duckdb`,
opened `read_only=True`, `season = 2026` rows only.

```
S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad
cp /Users/devin/Downloads/nba-prediction/data/export/latest/export.duckdb $S/db/export.duckdb          # exit 0
cd /Users/devin/Downloads/nba-wt-adv-aliases
/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python $S/adv/check47.py $S      # exit 0: ids, names, box teams, ticker codes, events, collisions
/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python $S/adv/settle.py  $S      # exit 0: Kalshi end-of-capture price vs our 11-16 box score
/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python -I $S/adv/roster.py $S    # exit 0: official roster cache 2026-11-17 (parquet copy)
nba_api static list + configs/odds_player_aliases.yaml cross-check (inline, exit 0)
```

Independent sources used:

1. **Our data:** `players`, plus `box_scores` and `games` for season 2026.
2. **Kalshi's own tickers:** the team code, the player code and `kalshi_player_uuid`.
3. **Settlement:** Kalshi's last capture quote, read as YES if bid ≥ 0.95 and NO if ask ≤ 0.05. These thresholds
   were chosen before looking. The result is compared with our 11-16 box stat ≥ threshold.
4. **nba_api's bundled static player list:** NBA's ids, all of league history.
5. **Today's official roster cache:** `data/rosters/2026-11-17`, copied to scratch.
6. **The generated sportsbook alias table:** `configs/odds_player_aliases.yaml`.

Global results:

- **Diff shape:** 47 added, 0 changed, 0 removed. No existing key changes its id (no "REMAPPED" line).
- **Kalshi side:** no `kalshi_player_uuid` carries two names, and no ticker player code carries two names, across
  both nights. Each of the 47 names has exactly one uuid and one ticker code.
- **Name collisions:** none in the `players` table (any two ids with the same normalised name: 0). In nba_api's
  static list (all history), no added key belongs to a different id: 44 are unique and equal, and 3 are 2026
  rookies who are not in the list.
- **Sportsbook table:** all 47 keys are already in `odds_player_aliases.yaml` with the same id (0 different, 0 in
  `collisions`).
- **Box-score teams:** every id has box rows on exactly one team in 2026. That team equals the ticker team code
  and is one side of the market's event. Every id has at least 9 rows.
- **Official roster (2026-11-17):** each id is on the ticker team, and the ticker's initial + surname letters
  match exactly one player on that roster.
- **Settlement (block A, 11-16):** 411 of 411 decided markets agree, 0 disagree, 43 undecided. 95% Clopper-Pearson
  lower bound on the agreement rate: 0.991. Control, with every teammate swapped in on the same markets: 3,155 of
  4,963 agree (0.636). Per player, the chance a wrong teammate matches all n markets is about 0.636^n. The weakest
  is Trey Lyles (6/6, about 0.07), and he is also pinned by static, roster and ticker.
- **Pelle Larsson:** all 23 of his markets stop quoting at 23:02Z, 88 minutes before the 00:30Z tip, and our box
  row is a DNP (minutes NULL). That is consistent with a pulled market.
- **Same surname and initial:** the only cluster is the Greens (Javonte DET, Josh UTA, and Jalen, not added). Kalshi
  writes full first names, and the three normalised keys are distinct. No father/son pair, no Jr./Sr. pair and no
  league-history namesake for any of the 47.
- **Builder's counts reproduced:** 11-16: 454 newly resolved markets (411 + 43). 11-17: 320 block-B markets
  (386 minus 66 that belong to block-A players on 26NOV17PORMIN). 0 titles remain unresolved on either night.
- **Games-played column:** the builder's figures count `minutes > 0` (see the fixture's query). Mine count all
  box rows, so DNPs are included. Same team in every case.

| Kalshi name | id | 2026 box team (rows) | ticker player code | event(s) | settlement vs our box | nba_api static | result |
|---|---|---|---|---|---|---|---|
| AJ Dybantsa | 1643407 | WAS (15 rows) | WASADYBANTSA4 | 26NOV16MEMWAS | 23/23 agree | not in static (2026 rookie) | confirmed |
| Anthony Black | 1641710 | ORL (12 rows) | ORLABLACK0 | 26NOV16ORLBOS | 22/22 agree (3 undecided) | unique, same id | confirmed |
| Bones Hyland | 1630538 | MIN (14 rows) | MINBHYLANDPG | 26NOV16PORMIN, 26NOV17PORMIN | 20/20 agree (2 undecided) | unique, same id | confirmed |
| Brice Sensabaugh | 1641729 | UTA (14 rows) | UTABSENSABAUGH28 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Cam Spencer | 1642285 | MEM (14 rows) | MEMCSPENCER24 | 26NOV16MEMWAS | 22/22 agree (1 undecided) | unique, same id | confirmed |
| Coby White | 1629632 | CHA (10 rows) | CHACWHITE3 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |
| Danny Wolf | 1642874 | BKN (13 rows) | BKNDWOLF2 | 26NOV16PHIBKN | 19/19 agree (1 undecided) | unique, same id | confirmed |
| Dean Wade | 1629731 | PHI (15 rows) | PHIDWADE45 | 26NOV16PHIBKN | 18/18 agree (1 undecided) | unique, same id | confirmed |
| Derrick Jones Jr. | 1627884 | LAC (11 rows) | LACDJONES5 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Georges Niang | 1627777 | GSW (14 rows) | GSWGNIANGPF | 26NOV16GSWNOP | 11/11 agree | unique, same id | confirmed |
| Gui Santos | 1630611 | GSW (12 rows) | GSWGSANTOS15 | 26NOV16GSWNOP | 10/10 agree (1 undecided) | unique, same id | confirmed |
| Isaac Okoro | 1630171 | CHI (13 rows) | CHIIOKORO35 | 26NOV16CHICLE | 17/17 agree (2 undecided) | unique, same id | confirmed |
| Isaiah Jackson | 1630543 | LAC (11 rows) | LACIJACKSON23 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Ja'Kobe Walter | 1642266 | TOR (13 rows) | TORJWALTER14 | 26NOV17NYKTOR | n/a (11-17 not played yet) | unique, same id | confirmed |
| Jake LaRavia | 1631222 | LAL (14 rows) | LALJLARAVIA12 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |
| Javonte Green | 1629750 | DET (12 rows) | DETJGREENSF | 26NOV17DETSAS | n/a (11-17 not played yet) | unique, same id | confirmed |
| Jordan Goodwin | 1630692 | PHX (9 rows) | PHXJGOODWIN23 | 26NOV16PHXDEN | 17/17 agree (2 undecided) | unique, same id | confirmed |
| Jordan Poole | 1629673 | NOP (12 rows) | NOPJPOOLE3 | 26NOV16GSWNOP | 16/16 agree | unique, same id | confirmed |
| Jordan Walsh | 1641775 | BOS (14 rows) | BOSJWALSH27 | 26NOV16ORLBOS | 11/11 agree | unique, same id | confirmed |
| Josh Green | 1630182 | UTA (14 rows) | UTAJGREEN5 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Josh Okogie | 1629006 | UTA (14 rows) | UTAJOKOGIE21 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Kevin Huerter | 1628989 | DET (12 rows) | DETKHUERTERSF | 26NOV17DETSAS | n/a (11-17 not played yet) | unique, same id | confirmed |
| Kris Dunn | 1627739 | LAC (12 rows) | LACKDUNN8 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Luka Garza | 1630568 | BOS (14 rows) | BOSLGARZA52 | 26NOV16ORLBOS | 18/18 agree (1 undecided) | unique, same id | confirmed |
| Luke Kennard | 1628379 | PHX (13 rows) | PHXLKENNARD8 | 26NOV16PHXDEN | 10/10 agree | unique, same id | confirmed |
| Matisse Thybulle | 1629680 | LAL (14 rows) | LALMTHYBULLE26 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |
| Max Strus | 1629622 | LAC (9 rows) | LACMSTRUSSF | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Meleek Thomas | 1643509 | CLE (14 rows) | CLEMTHOMAS15 | 26NOV16CHICLE | 8/8 agree | not in static (2026 rookie) | confirmed |
| Mo Bamba | 1628964 | UTA (14 rows) | UTAMBAMBA44 | 26NOV17UTALAC | n/a (11-17 not played yet) | unique, same id | confirmed |
| Noah Clowney | 1641730 | BKN (13 rows) | BKNNCLOWNEY21 | 26NOV16PHIBKN | 7/7 agree | unique, same id | confirmed |
| Oso Ighodaro | 1642345 | PHX (13 rows) | PHXOIGHODARO11 | 26NOV16PHXDEN | 15/15 agree (1 undecided) | unique, same id | confirmed |
| Patrick Williams | 1630172 | CHI (12 rows) | CHIPWILLIAMS44 | 26NOV16CHICLE | 7/7 agree | unique, same id | confirmed |
| Paul Reed | 1630194 | DET (12 rows) | DETPREED7 | 26NOV17DETSAS | n/a (11-17 not played yet) | unique, same id | confirmed |
| Pelle Larsson | 1641796 | MIA (12 rows) | MIAPLARSSON9 | 26NOV16HOUMIA | 0 decided (quotes stop 23:02Z; box DNP: consistent) | unique, same id | confirmed |
| Robert Williams III | 1629057 | POR (11 rows) | PORRWILLIAMS35 | 26NOV16PORMIN | 8/8 agree | unique, same id | confirmed |
| Ryan Kalkbrenner | 1641750 | CHA (13 rows) | CHARKALKBRENNER11 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |
| Sidy Cissoko | 1631321 | POR (13 rows) | PORSCISSOKO91 | 26NOV16PORMIN, 26NOV17PORMIN | 20/20 agree | unique, same id | confirmed |
| Sion James | 1642883 | CHA (13 rows) | CHASJAMES4 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |
| Taylor Hendricks | 1641707 | MEM (14 rows) | MEMTHENDRICKS22 | 26NOV16MEMWAS | 13/13 agree | unique, same id | confirmed |
| Terrence Shannon Jr. | 1630545 | MIN (11 rows) | MINTSHANNON4 | 26NOV16PORMIN, 26NOV17PORMIN | 23/23 agree (1 undecided) | unique, same id | confirmed |
| Tre Johnson | 1642848 | WAS (14 rows) | WASTJOHNSON12 | 26NOV16MEMWAS | 9/9 agree | unique, same id | confirmed |
| Trevon Brazile | 1642350 | DEN (12 rows) | DENTBRAZILE7 | 26NOV16PHXDEN | 8/8 agree | not in static (2026 rookie) | confirmed |
| Trey Lyles | 1626168 | MIN (14 rows) | MINTLYLES41 | 26NOV16PORMIN, 26NOV17PORMIN | 6/6 agree (1 undecided) | unique, same id | confirmed |
| Tristan da Silva | 1641783 | ORL (12 rows) | ORLTDASILVA23 | 26NOV16ORLBOS | 19/19 agree (1 undecided) | unique, same id | confirmed |
| Tyrese Proctor | 1642878 | CLE (15 rows) | CLETPROCTOR24 | 26NOV16CHICLE | 19/19 agree (1 undecided) | unique, same id | confirmed |
| Will Richard | 1642954 | GSW (13 rows) | GSWWRICHARD3 | 26NOV16GSWNOP | 15/15 agree (1 undecided) | unique, same id | confirmed |
| Ziaire Williams | 1630533 | LAL (11 rows) | LALZWILLIAMS11 | 26NOV17CHALAL | n/a (11-17 not played yet) | unique, same id | confirmed |

Every row is also on the 2026-11-17 official roster of the ticker team, and every key is in
`odds_player_aliases.yaml` with the same id.

**Riskiest names, examined:**

- **Kris Dunn, Ryan Kalkbrenner, Brice Sensabaugh** (the builder's weak web evidence). Each is pinned by NBA's own
  id in the static list (unique across history) and by today's official roster on the ticker team (LAC, CHA, UTA).
  Each has 12-14 box rows on that team only, and the same id appears in the sportsbook table. Weak web evidence
  does not weaken the id mapping: the table maps a name to an id, not to a team.
- **AJ Dybantsa, Meleek Thomas, Trevon Brazile** (no machine candidate; not in the static list). These are
  confirmed by settlement: 23/23, 8/8 and 8/8. Each is on today's roster as `exp = R` on the ticker team, and the
  sportsbook table has the same id. This is the strongest evidence in the batch.
- **Josh Green and Javonte Green** (the shared-initial cluster). Both ticker codes are `JGREEN`, but on different
  teams (UTA5 and DETSF), and the full names differ. Kalshi titles carry full first names.

**Web checks: not done.** No web-search tool was available in this run. I replaced them with the settlement check
(block A) and the static list plus official roster (all 47). Those are id-level evidence, stronger than a team
page.

## Check 2: the normaliser

`normalize_name` (`nba/parse/availability.py:67-78`) strips accents, lowercases, turns every non-alphanumeric
character into a space, and removes one trailing `jr|sr|ii|iii|iv|v`. Lookup is exact on the key
(`nba/kalshi/aliases.py:57-78`; the fuzzy accept was removed 2026-10-24).

Titles I tried, and what each gives:

| Title | Key | Result |
|---|---|---|
| "Robert Williams" | `robert williams` | 1629057; there is no other Robert Williams in nba_api history |
| "Derrick Jones" | `derrick jones` | 1627884; no namesake |
| "Terrence Shannon" | `terrence shannon` | 1630545; no namesake |
| "Tre Johnson III" | `tre johnson` | 1642848 |
| "Paul Reed Jr." | `paul reed` | 1630194 |
| "JaKobe Walter" | `jakobe walter` | unresolved: fails closed; correct, but a missed match |
| "Tristan Da Silva" | `tristan da silva` | 1641783 |
| "J. Green" / "Jalen Green" | | not matched by any new key |
| "Chris Dunn" | | not matched |
| "Pat Spencer" | | not matched |
| "Will Richardson" | | not matched |

Each suffix-stripped or variant key reaches only the same person, because no player in nba_api history and no
id in our `players` table shares a key with any of the 47. **I could not construct a title that resolves to a
wrong id after this change.** No blocker from the normaliser.

## Check 3: what changes on merge

| Reader | How it reads the table | New behaviour | Cached? |
|---|---|---|---|
| Kalshi snapshot (`nba.kalshi` via launchd, every 900 s) | `load_reviewed_aliases()` per run (`nba/kalshi/__main__.py:131`), `parse.py:197` | open markets of the 47 get `player_id` (about 454 on an 11-16-sized slate) | no; next run |
| JARVIS (`nba.jarvis watch --once`, every 120 s) | stored `kalshi_markets.player_id` (`nba/jarvis/sources.py:244`); display names via `parlay.assistant.names.NameResolver` | **new alerts and edges become possible** on these 47 players' props. Forecasts require `q.player_id in ctx.team_of` and skip OUT-on-report (`forecasts.py:95-122`), so a market with no forecast stays silent | no; per run |
| Parlay shadow / analyze, pretip job | stored `player_id`; names via `NameResolver.default()` (`nba/parlay/analysis.py:333`) | **new paper legs and shadow EV rows** on these players; verdict counts move because the denominator grows | no |
| Market capture, export `prop_lines`, app | stored ids; sportsbook names via `theoddsapi.NameResolver` | **new displayed Kalshi lines** for these players. Sportsbook rows do not change, because `HistoryResolver` already resolved all 47 to the same ids through the generated table | no |
| Plain `theoddsapi.NameResolver.default()` (`odds/capture.py:203`, fastarb `runner.py:507` / `pinnacle.py`) | reviewed table only | these players' book and Pinnacle rows start matching (same ids as the generated table) | **yes**: fastarb holds `self.resolver` for its nightly run (calendar start), so the change applies on the next start. `halftime_scorer._resolver` and a long-lived JARVIS agent likewise |
| `pulls.orchestrator`, `live_close`, `combo_*`, `history_pull` | `default_resolver()` = reviewed, then generated | no change: identical ids from either table | per process |
| Parlay assistant / JARVIS chat | static list plus aliases; ambiguity is an error | rookies not in static now get display names `name.title()` ("Aj Dybantsa", not "AJ Dybantsa"; see m3) | per process |
| Facts build (`nba/facts/build.py:491-493`) | entity names; the canonical name is the first Kalshi alias for that id | new entities for 47 ids. For ids with older aliases ("D. Wade", "Mohamed Bamba", "Nah'Shon Hyland", "D. Jones Jr.", "I. Jackson", "Z. Williams"), the older one stays canonical | no |
| Reporter parser (`reporter_parse.resolve_player`) | exact roster name first, then alias restricted to the roster | no resolution change: all 47 are exact roster names already | no |
| Reporter `parser_fingerprint()` (`reporter_pretip.py:371-392`) | hashes the file | **the fingerprint moves.** It gates nothing on main today: stored facts carry `PARSER_VERSION = "pretip-v1"`, not the fingerprint, and REPORTER_FEED_LIVE is an unfrozen draft (agent/modeler-reporter-feed, Stage A not recorded). After Stage A, any alias edit is an integrity break (draft clause 90(b)); see m4 | n/a |
| T510/T510b scorer | sha pin on the bytes it is given | B1 | n/a |

Nothing else pins the file: `git grep` for `kalshi_aliases`, `4d176c53`, `6544535a` and `78892076` finds only
`research/line_move` and docs. No code globs `configs/*.yaml`.

## Check 4: the frozen rule

- **The pin copy is exact.** `shasum -a 256 configs/kalshi_aliases_t510_pin_57164b8.yaml` gives
  `4d176c5381249effa54887cf9eecab88fa733417ae9f32dbbd57e0dbda4cd5d9`, and it is byte-identical to
  `git show main:configs/kalshi_aliases.yaml` and to `57164b8:` (`cmp` exit 0).
- **The refusal still works.** `_check_pinned_aliases` accepts the pin copy, refuses the live file
  (`78892076 != 4d176c53`), and refuses a one-byte-mutated copy of the pin (`edaeca45`). Exit 0.
- **Rule text unchanged.** `python -m research.line_move verify` (exit 0) gives T510 881aca57 ok and T510b
  68f0ef84 ok. `git diff main...HEAD -- docs/prereg research/line_move/spec.py` is empty.
- **Tests.** `pytest tests/research/line_move tests/kalshi`: 247 passed, 1 skipped, exit 0.
- **Is a default-path change a rule change?**
  - *For "it is a change":* the rule text (`LINE_MOVE_TOWARD_MODEL.md:20`) names the path
    `configs/kalshi_aliases.yaml`, and the scorer's identity is pinned by a code hash that this edit moves.
  - *Against:* the adopted pre-look ruling (DECISIONS 2026-10-24 21:38) pins the bytes 4d176c53 and says "later
    alias changes apply to neither rule". The bytes read are identical, so the rule's inputs are unchanged.
  - *My verdict:* **not a change to the rule, but a change to the frozen scorer's identity.** The scorer's own
    guard (the dryrun-record hash) treats it as one, which is B1. The fix that leaves both untouched is to pass
    `--aliases <pin copy>` explicitly and not edit the code.

## Check 5: the replay oracle

I traced static imports (with an AST walker that includes lazy in-function imports) from all 53 `nba.daily.*`
modules to any module that reads the alias file. Two paths exist:

- `nba.daily.__main__ -> nba.ops.watchdog -> nba.odds.live_close -> nba.odds.theoddsapi -> nba.kalshi.aliases`.
  This path resolves names only when live-close capture runs, not in the replay's prediction or scoring.
- `nba.daily.replay_season -> nba.kalshi` (the package `__init__` imports `aliases`). The replay uses only
  `is_real_kalshi_db` and the parlay shadow, which reads stored `player_id`s from a Kalshi copy. Display names
  come from `NameResolver`, and the 47 are 2026 players who do not appear in a season-2025 replay.

The model input path resolves names with `nba.parse.availability.NameResolver`, not this table. **Expected:
oracle unchanged. Not run here** (watchdog; the maintainer's run), and it is still required before merge because
the branch touches `configs/`.

## Check 6: the tests

`tests/kalshi/test_aliases_2026_11_17.py` checks that each name resolves to the fixture id, that the fixture's
`games_played_2026 > 0`, that the fixture name normalises to the same key, and that no key in the YAML maps two
ways. The ids come from the builder's own fixture, so the check is close to circular. **The test does not pin
what matters:** it never asserts the team, and it never checks the id against an independent source. See m1 for
the fix. It does catch a typo or a duplicate key, which is worth something.

## Findings

**Blocker**

- **B1:** the frozen T510/T510b scorer's code hash moves, and the committed dryrun record is invalidated; detail
  under "Top finding". Fix: revert the `research/line_move/__main__.py` hunks, pass `--aliases
  configs/kalshi_aliases_t510_pin_57164b8.yaml` at `tests/research/line_move/test_dryrun_loaders.py:110` and in
  the documented score command, and draft the DECISIONS row. Verify with `python -c "from research.line_move
  import spec, dryrun; dryrun.require_pass(spec.DRYRUN_RECORD, 'T510')"` (no exception) and with a code sha of
  `c34f7e5c`.

**Major**

- **M1:** no test guards the dryrun record against the code. A test that asserts
  `json.loads(spec.DRYRUN_RECORD.read_text())["code_sha256"] == spec.code_sha256()` would have failed on this
  branch. Fix: add it to `tests/research/line_move/` in the same commit as B1.
- **M2:** live-path go. This branch changes `configs/`, so under CLAUDE.md "Branches, not main" it needs the
  replay oracle unchanged and Devin's go. It also adds about 450 linkable markets a night (about 320 of 1,434 on
  11-17's block B alone) to JARVIS, the parlay shadow and the app. Fix: run the oracle (the maintainer's command)
  and get Devin's go recorded. This is process, not a defect.

**Minor**

- **m1:** `tests/kalshi/test_aliases_2026_11_17.py:26-31` is close to circular. Fix: add three assertions.
  - `assert rec["team"] in str(rec["kalshi_event"])[7:]` (cheap, no data).
  - For each name, `nba_api.stats.static.players` exact-key ids must be a subset of `{pid}`.
  - `nba.odds.player_aliases.load_generated()[0].get(key)` must be `None` or equal to `pid`.
- **m2:** `research/line_move/__main__.py:89-91` (`cmd_verify`) still hashes the live `configs/kalshi_aliases.yaml`
  and will print "DIFFERS from the pin" forever after merge. Fix: also print the pin copy's status, or print the
  live file as "live table (not used by T510)". If you take the B1 revert, leave this for a later scorer change
  with its own dryrun.
- **m3:** for 2026 rookies missing from nba_api static, `nba/parlay/assistant/names.py:41` (`name.title()`) shows
  "Aj Dybantsa". Fix: prefer the YAML's raw display string over the normalised key. Cosmetic.
- **m4:** `reporter_pretip.FINGERPRINT_FILES` includes `configs/kalshi_aliases.yaml`. Once REPORTER_FEED_LIVE
  Stage A is recorded, every alias addition breaks its integrity clause, the same pattern as T510. Fix (before
  Stage A, in that draft): pin a dated copy of the alias table for the reporter rule, as done here for T510.
- **m5:** block B (18 names) has no settlement check yet, because 11-17 is not played. Fix: after tonight's box
  scores land, re-run `$S/adv/settle.py` (11-17 branch) and append the counts to the builder's review. Expected
  0 disagreements.
- **m6 (scope):** a frozen-rule scorer edit rode in on a config branch. The builder flagged it as "not in my area"
  and then made it anyway. Fix: B1 removes it.

## Most likely failure mode if "the aliases are right" were wrong

Kalshi reuses a display name for a different person whose team matches ours, such as a two-way call-up with the
same name. That would make settlement, roster and ticker agree by coincidence. With 411/411 settled agreements
against a teammate-swap control of 0.636, and no namesake in league history for any of the 47, this is not
credible for block A. For block B it rests on roster, static list and ticker until m5 runs.

## Family and power

There is no statistical claim here. The settlement check is a descriptive identity test: n = 411 decided
markets clustered in 28 players (Larsson had none decided). Lower 95% bound 0.991 pooled. The per-player
false-match chance under the teammate null ranges from 0.07 (n = 6) to about 1e-5 (n = 23). The only family is
the 47 names. No BH is needed because nothing is selected from the family on a p-value.

## Playbook check

- **Class:** not a model. A reviewed lookup table on the live path; the class checks do not apply.
- **Information or capacity:** information. It links existing forecasts to existing prices and adds no model
  capacity.
- **Enough data:** n/a. Identity evidence per name is listed above.
- **Test validity:** the builder's evidence 2 was weak, as it says itself. My test is settlement-based, compared
  with a teammate-swap control so that it could fail. Block B is untested on settlement (m5).
- **Lineage and inherited guards:** the same reviewed-table discipline as 57164b8. The fuzzy-accept removal
  (2026-10-24) holds: exact-key only. The T510 alias pin guard holds, but the scorer's code-hash guard is
  tripped (B1).
- **Scored against:** n/a. No score is claimed.

## What I could not check

- Web searches: no tool available in this run.
- Block-B settlement: the games have not been played.
- The replay oracle: not run (watchdog; the maintainer runs it).
- Whether the snapshot job's upsert rewrites `player_id` on already-stored open markets: I took this from the
  builder and did not read the upsert SQL.
- Whether JARVIS or the parlay engine is gated against bench players with weak p_play once linked: this is a
  behaviour question, not an alias defect.
- The scratchpad directory is shared with other sessions. My scripts are `check47.py`, `settle.py`, `roster.py`
  and `imports.py` under `$S/adv/`.

## Verification (unpiped, this worktree)

- `ruff check research/line_move tests/kalshi/test_aliases_2026_11_17.py`: exit 0.
- `ruff format --check`: exit 0.
- `mypy` on the two changed `.py` files: exit 0.
- `pytest --no-cov -q -p no:cacheprovider tests/research/line_move tests/kalshi`: 247 passed, 1 skipped, exit 0.
- `pytest … tests/parlay tests/odds tests/facts tests/ingest/test_reporter_pretip.py tests/jarvis`: 961 passed,
  3 skipped, exit 0.

## Drafted rows (not appended)

- **DECISIONS (draft):** `| 2026-11-17 | T510/T510b are scored with --aliases configs/kalshi_aliases_t510_pin_57164b8.yaml (byte copy of the 4d176c53 pin) and no scorer code change; the live alias table grows freely (47 rows 2026-11-17, adversary_kalshi_aliases_2026-11-17.md). | Keeps the committed dryrun record (code sha c34f7e5c) valid; the rule reads identical bytes. | A scorer change is needed for another reason; then re-run the dryrun under adversary diff. |`
- Ledger: none (not a test).
