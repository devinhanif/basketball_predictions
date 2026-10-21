# Adversary review: slate briefing (agent/scribe-slate-briefing 3b718f3)

Date 2026-10-21. Reviewed: `git diff main...agent/scribe-slate-briefing` (nba/explain/slate.py new, 803 lines;
nba/explain/__main__.py +19/-1; tests/explain/test_slate.py new, 492 lines). Branch base e67ce3f. All reads of
nba.duckdb were `read_only=True`; no 2025 outcome was read; the scribe's worktree was not modified.

Verdict: BLOCKED

Short reason: every number on the 2026-10-20 page is right, and the code reads no 2025 data. The defects are
in the words and the placement, and the page exists to get those right. (1) When the roster cache is missing,
players are placed by their 2024-25 teams, and the page calls that the "current-season box score". On the
real 10-20 data this lists Jaylen Brown as Boston's top scorer. (2) The early edition says it was written
"before the first official injury report", while its own game block names the report it used. (3) A player
ruled OUT after the morning run still shows his morning forecast ("plays 9x%") right under the OUT line, and
the report edition's "what changed" section does not mention him. Each fix is small (about 30 lines plus
tests); a re-review of the revision diff only.

This is text for Devin, not a model result. The six statistical checks (shuffled labels, planted future,
missingness, knockout, replication, integer support) do not apply: nothing is fitted, scored or claimed. The
page quotes one accuracy figure, which is already in the ledger (check 3).

## Playbook check

Model class: none. This is a presentation layer over stored rows of two classes: ratings (rung0_injury_elo /
rung0_mov_elo) and gradient-boosted trees (props_context_residual). The five questions:
- Information or capacity: n/a. It adds no information, so the question is whether it reports the stored
  information faithfully (checks 1 and 4).
- Enough data for this class: n/a (no fit).
- Test validity: the tests use a synthetic fixture. Two of them assert behaviour this review calls wrong
  (findings B2 and B3), so passing tests do not mean the page is right.
- Lineage and inherited guards: T-60 cutoff (`made_at <= tipoff - 60 min`, the same rule as the settle step
  and the game explainer), the official roster cache from nba/daily/pipeline._official_roster, and the
  holdout exclusion. The cutoff and holdout guards are inherited correctly. The roster guard is inherited
  only when the cache exists (B1).
- What it is scored against: nothing. The page states the market comparison (check 3).

## Findings (severity-ranked)

### Blockers

**B1. The fallback team lookup reads 2022-24 box scores and calls them "current-season"**
(nba/explain/slate.py:285-298, 551-553).
- What is wrong: `latest_teams` filters `g.season <> ?` with 2025. That keeps 2022, 2023, 2024 and 2026.
  nba.duckdb holds box scores for 2022 (1,320 games), 2023 (1,318), 2024 (1,320), 2025 (1,321) and 2026 (3).
  Before the slate's own games, the latest non-holdout team of almost every player is therefore his 2024-25
  team. The docstring (line 286), the module docstring (line 14) and the page note (lines 552-553) all say
  "current season".
- Demonstration: I rendered 2026-10-20 against an empty roster root, which is the path the page takes when
  the official roster fetch fails. Opening night had a quiet roster failure (DECISIONS 2026-10-20). The
  output is `adv_brief/2026-10-20_early_nocache.md` in my scratchpad:
  - BOS block: "Jaylen Brown: 24.4 points ... plays 92%" ranked first. Brown played 34.9 minutes for PHI
    that night (box 0022600002), and the 24.4 comes from a misplaced morning row.
  - PHI @ NYK: "OUT ... Ariel Hukporti (NYK, ...)". The cache and the report put him on PHI.
  - BOS @ DET: "Report 13:45 ET: nobody from either roster listed OUT. 5 more OUT entries ..." With the cache,
    the same report names five players.
  The failure path is exactly where the guard matters, and there it puts players on the wrong team while
  the note says otherwise.
- Fix:
  - Line 295: `AND g.season <> ?` becomes `AND g.season = ?`, with the slate's season
    (`slate.year if slate.month >= 7 else slate.year - 1`, which gives 2026 for 2026-10-20).
  - Keep the 2025 exclusion as an assert.
  - With no cache and no current-season box, leave the player unplaced. The note at line 553 already says
    players with no game yet are left out.
  - Test: add a 2024-season box row for 9000030 on DET in `make_db` and assert he stays unplaced.

**B2. The early-edition header contradicts its own body**
(nba/explain/slate.py:592-596; locked in by tests/explain/test_slate.py:264-273 together with :276-284).
- What is wrong: the early header says, as a fixed string, "written HH:MM CDT before the first official
  injury report (next report about 13:00 ET)". On real data the 10:44 CDT run of 2026-10-20 (made_at
  15:44:01 UTC) had already used the 06:45 ET report: report_as_of_et `2026-10-20 06:45:00.804400` in all
  three games, with 2 OUT entries each in BOS@DET and PHI@NYK. The game block then prints "OUT on the
  06:45 ET report: ...". The fixture pages produce the same contradiction, and the tests assert both
  halves. The DECISIONS 08:4x row prescribed this label, so the label itself is wrong on the data (see
  needs_devin).
- Fix: build the early header from the reports actually used, as the report edition does at lines 598-608:
  - If any game has a `report_at`: "**Early edition**, written HH:MM CDT. It uses the HH:MM ET injury
    report; later reports (the main one about 13:00 ET) will move these numbers."
  - Otherwise: "... before any official injury report for these games; numbers move after about 13:00 ET."
  - Change the test at :268-270 to assert the report-aware wording.

**B3. A player dropped from later runs shows his old forecast under his own OUT line, and the changes
section hides the move** (nba/explain/slate.py:230-259, 407-429, 475-505).
- What is wrong:
  - `latest_rows` takes the latest row per (game, model, target, player). The live path writes a row only
    when it changes (nba/daily/store.py:192-230). It also drops players who are OUT on the report from each
    new batch (`_context_props` calls `slate_report_outs`, and the recency path passes `out_set`; see
    pipeline.py:790).
  - So when a player is forecast at 10:44 and listed OUT at 13:00, his 10:44 row stays his "latest usable
    row". The page then shows him with points and "plays 9x%" directly under "OUT on the 13:00 ET report:
    <him>".
  - In `_changes` the current row and the baseline row are the same stale row, so the report edition says
    nothing about the one change it exists to show.
- Demonstration (scratch copy of the test fixture; RUN2's row for 9000011, newly OUT at 13:00, deleted to
  match what the live path writes):
  - The game line reads "OUT on the 13:00 ET report: Player 9000010 ...; Player 9000011 ...".
  - The "Players:" list has only 9000001. The 9000011 line ("P(play) from 62% to 31%") is gone.
  - The fixture's own 9000010 (OUT at 06:45) is shown in DET's top three as "15.0 points ... plays 5%".
    test_slate.py:303-306 asserts that line as "a miss shown as plainly as a hit".
- On the real 10-20 slate: 0 of 13 OUT entries (on the reports the final T-60 runs used) had a surviving
  prop row, so this did not fire yet. Every mid-day scratch of a forecast player will trigger it.
- Fix:
  - In `game_view` (after line 411): skip players whose `win.report.get(pid)` has status `out`, and count
    them in a new `ruled_out` field rendered as "N player forecast(s) not shown: OUT on the report."
  - In `_changes` (after line 481): if the player is OUT on `cw.report` and was not OUT on `bw.report`,
    append "<name> (<team>): now OUT on the report (<reason>); his last stored forecast was <mean> points,
    plays <p>%" whether or not the row moved.
  - Tests: one with the RUN2 row deleted, as above; and change :303-306 so an OUT player is not listed.

### Major

**M1. "Player forecasts (model means, not book lines)" is betting vocabulary, and the test's word list
cannot catch it** (slate.py:678; test_slate.py:35-37).
- What is wrong: DECISIONS 2026-10-21 08:4x says "No betting language anywhere in the briefing". "Book
  lines" appears 3 times per page. The test regex has no `book`, `line(s)`, `over`, `under`, `wager`,
  `lock`, `value` or `units`.
- Fix:
  - Line 678 becomes `"Player forecasts (model averages if he plays):"`; this also fixes M2.
  - Add `book|books|sportsbook|line|lines|over|under|wager|wagers|lock|value|units|fade` to `BET_WORDS`.
    After the line-678 change I expect no hits on these pages, but run it.

**M2. The points figure is the mean if he plays, but the page does not say so** (slate.py:421, 430, 663).
- What is wrong: the stored row carries `mean` and `mean_uncond`. The page prints `mean` next to "plays NN%".
  A stranger reads "22.7 points" as expected points. Top-3 ranking by the conditional mean also lets a
  low-P(play) player outrank a sure starter.
- Fix: the label change in M1, and rank `team_top` by `mean_uncond` when it is present (line 430), so the
  ordering matches "who is likely to score most".

### Minor

- **m1** (slate.py:485): `crossed` only catches a P(play) drop through 0.5. The page says "P(play)
  crossing 0.5". Fix:
  `crossed = bp is not None and cp is not None and (bp >= PLAY_CUT) != (cp >= PLAY_CUT)`.
- **m2** (slate.py:475-482): the changes loop does not skip withdrawn keys or players on neither roster.
  On 10-20, "player projections compared: 77" includes the 20 morning rows for players on neither team's
  official roster (Jaylen Brown in BOS@DET, Paul George in PHI@NYK, ...). Fix: skip when `key in withdrawn`
  or `placer.side(pid, sides) is None`, the same guards `game_view` uses.
- **m3** (slate.py:540-544): "official rosters cached ... (the files the forecast reads)" is not true for
  rows made before the cache existed. The 2026-10-20 cache files are stamped 12:50 CDT; the 10:44 CDT run
  had no cache and forecast 20 players onto teams whose official roster does not list them. Fix: "Team
  membership: official rosters cached for <date> (fetched by the first run that had them)", or compare the
  cache mtime with the earliest row used.
- **m4** (slate.py:262-272, 574-576): the "what changed" baseline is one slate-wide `min(made_at)`, not the
  earliest run before each game's cutoff as the scribe describes it. A game first forecast later than
  another game has no baseline rows at that stamp and is dropped silently; only `n_games` shows it. Fix:
  take the minimum per game (`GROUP BY game_id`) and call `latest_rows` per game with its own cap.
- **m5** (slate.py:778-780): a missing database returns 4 ("no rows"). The task brief and the steward
  caller treat 4 as informational, so a missing nba.duckdb on game day would not alert. Fix: return
  `EXIT_UNREADABLE`.
- **m6** (slate.py:789-799): only `duckdb.Error` is caught. A corrupt roster parquet (polars error) or a row
  without `mean` (KeyError) raises with no page, which contradicts line 18 ("A missing optional source never
  fails the run"). Fix: wrap `read_rosters` per file in try/except, treat an unreadable file as not cached,
  and say so in the note.
- **m7** (slate.py:325-334): the report lookup is an exact match `as_of = report_as_of_et`. It matched 27 of
  27 stored rows. If the two formats ever diverge, the page silently says "nobody from either roster listed
  OUT". Fix: when `report_as_of_et` is set and no line for any game exists at that `as_of`, print "report
  lines not found" instead.
- **m8** (slate.py:590): "written HH:MM CDT" has no date. A re-render of a past slate (as here) reads as same
  day. Fix: `%Y-%m-%d %H:%M`.
- **m9** (brief): the hand-off says 20 tests. tests/explain/test_slate.py runs 24 (`24 passed in 1.14s`).

### Pre-existing (not in this branch; confirmed)

**P1. The per-game explainer reads 2025-season box rows to place players.**
- Where: nba/explain/data.py `load_report` (lines 245-252: `player_game_stats JOIN games ... WHERE
  p.player_id IN (...) AND g.game_date <= ?`, no season filter) and nba/explain/page.py:308-317
  `_guess_team` (the same query, no season filter).
- What is read: for a 2026 game, `arg_max(team_id, game_date)` ranges over 2025-26 rows. What is exposed is
  team membership and appearance in the holdout box scores, not stat values.
- Also: `load_game` (data.py:95-115) returns `home_pts`/`away_pts` for any game_id, including a 2025 game if
  someone asks for one.
- Fix (separate branch):
  - Add `AND g.season = ?` (bound to `game.season`) to both queries, or at least `AND g.season <> 2025`.
  - Better: place players from the official roster cache, as the slate does.
  - Have `load_game` refuse `season == 2025`.
  - Add a source scan to tests/eval/test_holdout_source_filter.py style that covers nba/explain/.

## Re-derived game block (BOS @ DET, 0022600001, SQL independent of slate.py)

The latest `rung0_injury_elo` row with `made_at <= tipoff - 60 min` is made_at 2026-10-20 17:50:10 UTC:
`p_home 0.5571`, `p_mov_elo 0.5631`, delta -0.60 points, report_as_of_et `2026-10-20 13:45:00.773225`, no
fallback. The report time (13:45 ET) is before the run (13:50:10 ET). Across all 27 stored injury-Elo rows,
the report was never after the run (0 of 27), and every report_as_of_et matched `player_availability.as_of`
exactly (27 of 27).

OUT at that `as_of` (5 lines, all OUT), placed by the 2026-10-20 cache: Taurean Prince DET PersonalReasons;
Elijah Harkless DET G League two-way; Devin Carter BOS illness; Ugonna Onyenso DET two-way; Isaac Jones DET
two-way. The page matches, in the same order.

Props (props_context_residual, latest usable row per key, top 3 points per team, largest reb and ast):

| | page | SQL |
|---|---|---|
| Tatum | 22.9 pts, rec 23.6, -0.8, 36 min, 94% | 22.9, 23.6, -0.8, 36, 94% |
| George | 15.2, 16.8, -1.6, 32, 91% | 15.2, 16.8, -1.6, 32, 91% |
| Pritchard | 13.8, 16.4, -2.6, 30, 98% | same |
| Cunningham | 23.0, 24.4, -1.4, 35, 95% | same |
| Duren | 13.9, 14.4, -0.5, 28, 97% | same |
| Collins | 10.9, 13.4, -2.5, 28, 97% | same |
| Most reb | Duren 9.0 (rec 9.1, +0.0) | Duren 9.0 (rec 9.1, -0.0, displayed +0.0) |
| Most ast | Cunningham 8.1 (rec 8.5, -0.4) | same |

The scribe's quote (55.7%, 56.3%, -0.6, five OUT names) is verified. PHI @ NYK (73.45 / 73.53, -0.1) and
OKC @ SAS (56.48 / 55.79, +0.7) also match their latest usable rows.

## Check table

| # | Check | Command (scratchpad = S) | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Numbers vs rows | `python -m nba.explain slate --date 2026-10-20 --db nba.duckdb --out S/adv_brief/2026-10-20_{early,report}.md --edition {early,report}`; `python -I S/adv_scripts/{rederive,avail,block}.py` | 3 games, 27 injury-Elo rows, 10 props lines | exit 0 / 0. Every value matches. Report after run: 0 of 27. | PASS |
| 1b | Report vs run time | `S/adv_scripts/checks.py` | 27 | 0 rows with report_as_of_et after made_at (ET); 0 exact-match misses | PASS |
| 2 | Holdout in this branch | read every SQL in slate.py (lines 220, 238-253, 266-270, 279-281, 292-296, 329-332) | 6 queries | Only `latest_teams` reads box scores, and it excludes 2025. forward_predictions holds 2026 only (1,642 rows, tips 2026-10-10 to 10-21). The roster cache path is the slate date. | PASS (holdout); FAIL (season label, B1) |
| 2b | Pre-existing exposure | read data.py:224-256, page.py:308-317 | 2 queries | No season filter: confirmed | P1 (not this branch) |
| 3 | Replay Brier sentence | `sed -n 10p docs/prereg/WINNER_RECAL.md` | 1,311 games / 207 dates | Number, CI and n match the source; already in the ledger; nothing is read from 2025 | KEEP (wording edit below) |
| 4 | Roster placements | `S/adv_scripts/roster.py`, `hidden.py`; `ls -la data/rosters/2026-10-20` | 519 cache rows / 30 teams; 103 box rows | 0 of 103 box rows contradict the cache. George BOS 30.6 min, Brown PHI 34.9, James PHI 33.8, Harris SAS (DNP row), Hukporti PHI on cache (no box, OUT). Same source as pipeline `_official_roster` (`roster_cache_path(DEFAULT_ROSTER_DIR, run_date, team)`). Cache written 12:50 CDT, after the 10:44 run. | PASS (with cache); FAIL without cache (B1) |
| 5 | Bet words | `grep -o -i -E '\b(bet\|...\|book\|line\|lines\|over\|under\|play\|plays)\b' S/adv_brief/2026-10-20_{early,report}.md \| sort \| uniq -c` | 2 pages | `6 book`, `6 lines` ("not book lines"), `1 play`, `48 plays` (P(play)). No over/under/odds/edge/pick. | FAIL (M1) |
| 6 | Tests read | test_slate.py, all functions | 24 run | Branch list below. Two tests assert wrong behaviour (B2, B3). | partial |
| 7 | Gates | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/explain tests/test_layering.py --no-cov -q -p no:cacheprovider` | | `65 passed in 4.43s`, exit 0 (test_slate.py alone: `24 passed in 1.14s`, exit 0) | PASS |
| 7b | Lint and types | `ruff check --no-cache` on the 3 files; `ruff format --check --no-cache`; `mypy --cache-dir=/dev/null nba/explain/slate.py nba/explain/__main__.py` | | `All checks passed!` exit 0; `3 files already formatted` exit 0; `Success: no issues found in 2 source files` exit 0 | PASS |
| x | B1 demo | `python S/adv_scripts/nocache.py > S/adv_brief/2026-10-20_early_nocache.md` | 3 games | Brown in the BOS top 3; Hukporti on NYK | FAIL |
| x | B3 demo | `python S/adv_scripts/stale_demo.py S/adv_brief/stale_demo.duckdb` (scratch fixture) | 1 game | Newly OUT 9000011 missing from "Players:"; OUT 9000010 shown "plays 5%" | FAIL |

## Check 3: the replay sentence

Quoting it leaks nothing. The figure is +0.0064 [+0.0023, +0.0104], n 2,622 market rows = 1,311 games in
207 date clusters, and it sits in docs/prereg/WINNER_RECAL.md:10, docs/REPLAY_2025_26.md and CLAUDE.md.
Rendering it reads no data. The status words ("exploratory, and that season is the holdout already spent")
are honest. **Keep**, with two edits:
- "Player props are the only plausible place to beat the market" invites the reading the page is meant to
  avoid. Use "Player props are where the system might know something the market does not; that is unproven
  on the live log."
- Add "(Brier: probability error, lower is better)" once, so a stranger can read the number.

## Check 6: tests and untested branches

Covered:
- fallback_reason set (stale_elo_params)
- the no-roster-cache day (the default `NO_ROSTER`)
- P(play) < 0.5 (5%, 31%)
- report edition with real movement (G1 -3.7 points; 9000001 -3.0; 9000011 crossing)
- single run
- late-only game
- exit codes 0/4, missing database, database byte-identical after a run
- the 2025 exclusion

Untested:
- a player dropped from the later run (B3)
- a pre-2025 box row used by the fallback (B1)
- an early edition whose rows used a report (it is tested, but the assertion encodes the contradiction, B2)
- an upward P(play) crossing (m1)
- a ghost or withdrawn row in the changes section (m2)
- a game whose first run is later than the slate's first run (m4)
- a corrupt roster parquet (m6)
- a player on both cached rosters
- a partially cached slate where one game mixes cache and fallback
- the `inj is None` path (MOV row only)
- a win row without `home_team`
- `p_mov_elo` absent from the injury row

## Holdout and access statement

- No 2025 outcome was read.
- One metadata query counted box rows and games per season (`S/adv_scripts/seasons.py`: counts only, all
  seasons including 2025, no stat values) to size B1.
- The no-cache render ran `latest_teams`, which excludes 2025.
- Season 2026 box rows for 0022600001/2/3 were read for team_id and minutes (check 4). That is the live
  forward season, not the frozen holdout.

## The single biggest risk

The first mid-afternoon scratch of a star. The report edition will list him OUT in the game line, show him
in the top three with his morning "plays 95%", and say "Nothing moved past those thresholds", on the page
whose purpose is to say why numbers moved (B3).

## What was not verified

- Whether a real fallback_reason row says what the page says ("team strength only"). There are 0 fallback
  rows in production (0 of 27).
- That `mean_recency` is conditional on playing, like `mean`.
- The steward branch that calls this subcommand (reviewed separately), including how it treats exit 4.
- Behaviour on a slate whose late games tip after 00:00 ET.
- `nba.explain.names` was not read beyond its function list (no SQL in it).

## Drafted rows (not appended)

DECISIONS (draft):

| 2026-10-21 | Slate briefing (agent/scribe-slate-briefing 3b718f3) BLOCKED by adversary review (docs/reviews/slate_briefing_review_2026-10-21.md): numbers and holdout exclusion verified on 2026-10-20 (BOS@DET 55.7 / 56.3 / -0.6, five OUT names, ten props lines re-derived by SQL), but the no-cache fallback places players by 2024-25 teams under a "current-season" label (B1), the early header says "before the first official injury report" while naming the report used (B2), and a player dropped from later runs shows his stale forecast under his own OUT line (B3). Fixes are about 30 lines plus tests; re-review the revision diff. | The page is read daily by Devin and must not contradict itself. | Reverse if the revision passes the B1-B3 tests and a re-render of 2026-10-20 with and without the cache. |

## Re-check fe6e9eb (2026-10-21)

Reviewed `git diff 3b718f3..fe6e9eb` (2296453 E3 + fe6e9eb fixes; slate.py +445/-220, test_slate.py +311/-87).

Verdict: APPROVED

| Item | Evidence | Pass |
|---|---|---|
| B1 | `latest_teams` now filters `g.season = season_int_for_date(slate)` (2026) and asserts the season is not 2025. Re-rendered 10-20 with no cache (`S/adv_re/nocache.md`): no player placed from an earlier season, so Brown is no longer on BOS and Hukporti no longer on NYK. Test `test_fallback_never_places_from_an_earlier_season`. | PASS |
| B2 | The early header names the report used ("Injury report used: the 20:15 ET report; later reports can move these numbers") or says none was used. Tests `test_early_edition_header_footer_and_sentence` and `test_early_header_without_any_report_says_so`. | PASS |
| B3 | `game_view` drops players OUT on the report the game's win row used; `_changes` flags "now OUT". My earlier fixture demo, re-run, now prints "OUT on the report, no forecast shown: ... 9000011" and "Player 9000011 (DET): now OUT on the 13:00 ET report ...; his last stored forecast was 9.0 points, plays 62%". Tests `test_ruled_out_player_has_no_projection_shown` and `test_player_ruled_out_with_no_row_in_the_latest_run`. | PASS |
| M1 | "model averages if he plays". `BET_WORDS` now includes book, line(s), over, under, wager, lock, value, units and fade. My scan of both re-rendered pages: 0 hits (grep exit 1). | PASS |
| M2 | Labelled "if he plays", with P(play) on each line. Ranking stays by the conditional mean: deviation 2 accepted, because the label now says what is ranked. | PASS |
| m1-m9 | Crossing either way (line 569). Ghost and withdrawn rows excluded: 77 compared becomes 57 on 10-20, which matches the 20 misplaced rows. Per-game baseline (`first_usable_by_game`). Report matched within 1 s, with a "missing" flag. Dates on stamps. Cache mtime in the roster note. | PASS |
| Exits | Missing DB: exit 1, no file written. Corrupt DB: exit 1, previous page sha `c243ab2c` unchanged before and after. Atomic `os.replace`. | PASS |
| Numbers | The 10-20 report page has the same values as the first review (BOS@DET 55.7 / 56.3 / -0.6, five OUT, ten props lines identical). | PASS |
| Gates | `pytest tests/explain tests/test_layering.py --no-cov -q -p no:cacheprovider`: `72 passed in 3.99s`, exit 0. ruff check: `All checks passed!`, exit 0. ruff format: `2 files already formatted`, exit 0. mypy slate.py and __main__.py: `Success: no issues found in 2 source files`, exit 0. | PASS |

Deviation 1 (B3 handled through OUT-on-report or withdrawal, not the latest run's row set): accepted. Write-on-change
(store.py:192) makes absence from the latest run ambiguous. The only live-path prop exclusions I found are
report OUT (`slate_report_outs` in `_context_props`; `out_set` at pipeline.py:790) and roster withdrawal. The
morning-run rows for players on neither team's official roster are handled by placement. My search was not
exhaustive: a future exclusion rule in the props path would reopen B3.

Non-blocking leftovers (minor):
- On the no-cache path (slate.py, `_game_md` / `_win_lines`) the words claim more than is known.
  - "No points forecasts stored for this team" (they are stored, just unplaced).
  - "N player forecast(s) not shown: on neither team's roster" (no roster is on file).
  - "Report 13:45 ET: nobody from either roster listed OUT. 5 more OUT entries ... not placed" (the 5 are
    unplaced, not absent).
  - Fix: when a side's team is not in `placer.cached`, use "not placed: no official roster on file for this
    team" in all three places.
- The holdout guard in `latest_teams` is an `assert`, which `python -O` removes. Fix: raise `ValueError`.
