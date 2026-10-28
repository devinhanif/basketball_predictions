# Adversary review: reporter capture (agent/steward-reporter-capture @ cb95f55), 2026-10-27 20:14 CDT

Written by the adversary agent; saved by the main session because the harness refused the agent's file write.

Verdict: BLOCKED (NOT_READY). On the claim "capture is leak-free and write-once": WOUNDED.

- Held up: the store sha across real rebuilds; the prediction-path import closure; the counts guards apart from --max-usd.
- Broken: a reporter fact reaches a forecaster input (B1); three ways a fact gets an early known_at (M1, M2). None of it bites until the tick is installed and the store has rows.

## Top finding (B1, blocker)
Reporter facts, with raw post text in `value_text`, reach the LLM shadow payload. LLM_SHADOW_2026_27 section 1 forbids exactly this. Three things together: the nightly `nba.facts build` merges the store by default; `as_of` returns `claimed_*` and `reporter_*` rows; `nba/shadow_llm/payload.py::_facts_section` passes every as-of fact on. Shown on a facts DB built by the real CLI: 6 of 12 facts in the section were reporter-derived (e.g. `claimed_minutes_restriction`, value_text "Alperen Sengun will be on a 24-minute restriction tonight.", source x:RocketsBeat).

## Playbook check
Class: an information source feeding a fixed adjustment of production; nothing fitted; nearest lineage INJURY_TIMING. Information, so the known-at stamp is the whole game. Power rests on lambda and f (unmeasured); M4 and M7 cut the scarce rows. Validity threats: known-at (M1, M2), coverage (M3), parser (M5). Inherited guards: first_seen from our own fetch, write-once raw, allow-list first, no LLM, the opp_ridge missingness lesson. Coverage gaps are knowable pre-tip but not random (Mac sleep; posts_per_day 400 cutting the handles polled last). Scored against production's T-60 row, untouched by this branch.

## Checks
1. Known-at. 1a FAIL (M1): a 429 wait inside `fetch_page`; post stamped 22:29:00 (T-60 22:30:00) but the response arrived 22:30:50. 1b FAIL (M2): a raw_backfill file replayed through `parse-only --store` was stored capture_mode=live with known_at <= T-60. 1d FAIL (M2): a file outside every raw root with a planted fetched_at of T-15 became a live fact. 1c pass (index wins when the post is also in raw/). 1e: the primary key leaves out known_at, so an early stamp can never be corrected. 1f pass: created_at used only for game attachment and F3/F4; as_of gates on known_at.
2. Write-once store. 2a pass: real `nba.facts build` twice plus once under a lock; store sha f873f9ce85176960 unchanged; 6/6 reporter rows identical incl. fact_id; locked build exited 0 "skipped (not dropped)". 2b pass: no UPDATE/DELETE/DROP. 2c FAIL (M4): two caps in one post; the numbered cap (N=20) deduped away, stored value NULL.
3. Cursor and lookback. pass: short pages never advance the cursor; a 12:30 ET tip still reads a 09:00 ET post. 3d FAIL (M7, silent): a Tuesday 13:00 ET post about Wednesday from a team idle Tuesday is never requested (18 h cap) and the poll still ends ok. 3b minor (m2): a page-2 error leaves page-1 posts billed but unwritten.
4. Coverage. 4a FAIL (M3): stale/retimed tip gives false "covered" (tick loads the schedule cache with no max_age). 4b FAIL (M3): a postponed game made up later keeps its stale coverage row. pass: DST end 2026-11-01; 22:30 ET tip on the right day; one handle cannot cover two teams.
5. Parser, 24 adversarial sentences labelled before running: precision 0.67 (n 9; Wilson [0.35, 0.88]); recall 0.46 (n 13; [0.23, 0.71]); recap false positives 0.375 (n 8; [0.14, 0.69]; bound 0.02). FAIL (M5). The 121-row dev set (TP 40, FP 0, FN 1, TN 80) was tuned against and written by one builder; docs call it DEV honestly.
6. Prediction path. pass: full closure of `nba.daily.__main__` (131 modules, lazy imports included) has no reporter or nba.facts module. FAIL (B1) above. Watchdog silent until installed; one manual `tick` starts stale alerts (m5).
7. counts. pass: --dry-run sends 0 requests, writes 0 files; no --confirm exits 2; no token exits 2; 2025-26 refused; every start_time before 2025-08-01. FAIL (m1): --max-usd 0.24 still spent $0.32 (32 requests).

Tests: reporter + facts + ops + layering 414 passed, exit 0. Wider (+ shadow_llm, explain): 578 passed, 1 failed (`tests/shadow_llm/test_runner.py::test_mixed_forward_table_still_settles`, also fails on main). ruff 0, format 0, mypy 0.

## Defects
- B1 blocker: reporter facts reach the shadow payload. `nba/facts/__main__.py:34-38` (merge default on), `nba/facts/build.py:660-663`, `nba/facts/query.py:56`, `nba/shadow_llm/payload.py:368-399` (no predicate filter). Fix: default `--reporter-store ''`; better, skip `claimed_`/`reporter_` predicates in `_facts_section` with a planted-fact test.
- M1: `fetched_at` stamped before the request (`reporters.py` fetch_page). Fix: stamp after `_get` returns.
- M2: parse-only marks backfill and outside files live. Fix: index both roots; write nothing (or capture_mode="external", ineligible) for posts not in the index; default BACKFILL; tests.
- M3: coverage final against the wrong tip (tick loads schedule cache without max_age; `reporter_store.py:84` keys on (game_id, team_id)). Fix: max_age=MAX_SCHEDULE_CACHE_AGE; add tip to the coverage key; the prereg's scorer recomputes coverage from poll_log.
- M4: one post's second claim deduped away (`reporter_store.py:59` PK). Fix: merge claims per (post, player, predicate) preferring the numbered cap, or add evidence_sha256 to the key; same in the draft's section 6.
- M5: recap headlines pass F1/F2 (`reporter_pretip.py:67-70` treats vs|against as prospective; F2 needs a stat word). Fix: a cap needs a real future/tonight marker; add `\bscores?\s+\d+\b`, `\b\d+\s+and\s+\d+\b`, `\bin\s+\d+\s+min(?:ute)?s\b` to F2; `finishes|finished|ended up` to F1. Acceptance still from the 5 live nights.
- M6: the frozen parser sha covers too little (hashes `reporter_parse.py` only). Fix: at Stage A record parser_fingerprint() plus the sha of `reporter_pretip.py`, `reporter_facts.py`, `nba/parse/availability.py`, `configs/reporter_nicknames.yaml`, `configs/kalshi_aliases.yaml`.
- M7: day-before posts lost silently (18 h cap; team handles polled on game days only). Fix: lower bound = max(cursor - overlap, previous team tip + 3 h, now - 48 h).
- Minors: m1 counts worst case is 32 requests (`max_requests = sum(pages_for(s)+2 ...) * len(queries)`); m2 write raw per page; m3 a tick skipped on the lock returns rc 0 (return rc 2 after 10 min held); m4 docs out of step (store path, cadence T-90/T-70/T-40, docstring at reporter_facts.py:433-436, plist text, REPORTER_FEED.md:133); m5 document the stale alert after a manual tick; m6 `claimed_store_facts` keeps post text in an insert-only store, so the 24 h deletion term cannot be honoured (store post_id + evidence_sha256 only; Devin's C5); m7 an opponent player gets the reporter's team_id in `resolve`; m8 recall losses on real pre-tip statuses.

## Biggest risk
An early stamp (M1/M2) entering the insert-only store. Kill criterion (b) checks only `first_seen > tip-60`, never a too-early stamp, and the store cannot correct one. A read at T-58 would be scored as a T-60 front-run.

## Not verified
exclude=replies on live X; real tip drift vs the cached schedule; frequency of recap headlines like A01/A04/A05; whether NBA_LLM_SHADOW is on (.env not read; DECISIONS says off); whether posts_per_day 400 binds.

## Recommended order
1. B1 before merge. 2. M1-M4 before the tick is installed. 3. M5-M7 before Stage A. 4. Minors when convenient; m6 with Devin's C5.

## Round 2 (agent/steward-reporter-capture @ a304d0e), 2026-10-27 20:45 CDT

Verdict: APPROVED

Scope of the verdict: **merge only.** B1, the one merge blocker, is closed and verified. **Installing the tick is NOT_READY** until N1 is fixed (it is the same class as M1/M2: a wrong live fact entering the insert-only store, which cannot correct it). **Stage A is NOT_READY** until N4 is fixed and M5 passes acceptance on live nights. The branch touches `nba/ops/watchdog.py` and `ops/launchd/`, so merging still needs Devin's go and an unchanged replay oracle (CLAUDE.md, branches); I did not run the oracle.

Top finding (N1, new, major, pre-existing code): after a late tip that is followed by a back-to-back, a postgame "tonight" post written after midnight ET and later than tip + 3 h becomes a live `reporter_minutes_cap` fact on the NEXT game, stamped about 18 h before its T-60. All 5 recaps planted at 01:45 ET were written; 0/5 were written at 01:15 ET (inside F4) or on the same evening (F3). Mechanism: F3 resolves "tonight" against the post's ET date (`nba/ingest/reporter_facts.py:525`), but a West-coast writer's "tonight" at 22:45 PT is the previous game; F4 (`IN_PROGRESS = 3 h`, `reporter_facts.py:63`, `:513`) has ended by then. Kill criterion (b) cannot see it, because the stamp is honest.

### Fix-by-fix (my round-1 scripts, adjusted only for changed interfaces; scratchpad, fixtures and tmp dirs; no network, no token, no 2025, no write to data/)

| id | check | command | result | pass |
|---|---|---|---|---|
| B1 | facts DB from the real CLI with the store merged EXPLICITLY (`--reporter-store <store>`), then `_facts_section` at T-60 | `atk2_store.py r2` then `atk6b_shadow.py r2/atk2/f1.duckdb` | `as_of` returns 12 rows, 6 reporter-derived; the section has 6 facts, 0 reporter-derived (round 1: 6 of 12). The default is now `''` (`nba/facts/__main__.py:36`); no nightly caller passes the flag (grep ops/ scripts/ nba/ configs/) | PASS |
| M1 | a 429 wait inside `fetch_page` | `atk1_knownat.py` A1a | stamp 22:30:50 = response 22:30:50, gap 0 s (round 1: 50 s early). Coverage now needs the poll to END by T-60 (`reporter_store.py:286`) | PASS |
| M2 | a raw_backfill file through `parse-only --store`; an outside file with a planted T-15 stamp; the same with an honest raw copy | A1b, A1d, A1c | backfill: 0 facts. Outside, no raw: 0 facts, with the stderr notice. Outside plus honest raw: the stamp is the honest T-40, not T-15 | PASS |
| M2 (residual) | primary key leaves out known_at (A1e) | A1e | the first write still wins, by design; both routes to an early stamp found in round 1 are now closed | note |
| M3 | stale cache; made-up game | `atk3_cursor_cov.py` D1/D2, `atk3b_r2.py` M3 | a 40 h old cache through the real `tick` CLI: rc 5, 0 requests. The made-up game gets its own row (2 rows, keyed on tip). `compute_coverage` still trusts whatever tip it is given (the D1 cached-vs-true tip pair still disagrees); the draft's scorer uses the real tip and `poll_log`, so this is covered there | PASS |
| M4 | numbered cap behind an unnumbered one, both sentence orders | `atk2c_r2.py` | `reporter_minutes_cap` stored as 20.0 in both orders; out beats game_time_decision. **But `claimed_minutes_restriction` stored NULL** in unnumbered-then-numbered: `claimed_store_facts` (`reporter_facts.py:375-411`) is not merged and the PK dedups blind. Briefing/explainer only | PASS (scored path); N2 minor |
| M5 | my 24 held-out sentences | `atk4_parser.py` | before: precision 0.67 (n 9; Wilson [0.35, 0.88]), recall 0.46 (n 13; [0.23, 0.71]), recap FP 0.375 (n 8; [0.14, 0.69]). After: precision 1.00 (n 6; [0.61, 1.00]), recall 0.46 (n 13; [0.23, 0.71]), recap FP 0.00 (n 8; [0.00, 0.32]) | see below |
| M5 | **was the parser tuned to them? Yes.** The F1/F2 changes (`reporter_pretip.py:59-86`) are exactly the regexes I proposed in round 1 from A01/A03/A04/A05. The 6 new dev recaps L055-L060 are paraphrases of my constructions ("in N minutes", "finishes with", "ended up", "N and M", "scores N", each with vs/against). No sentence was copied verbatim, but the templates were. The 24 are now dev data, and the "after" numbers are dev numbers | `git diff cb95f55 a304d0e -- tests/fixtures/reporters/pretip_labels_v1.jsonl` | | |
| M5 | **fresh set**: 20 new sentences, labels fixed in the file before running (`r2/atk4b_labels_frozen.py`, sha256 4fe9389d); prior written before running: ~5/12 recaps pass | `atk4b_r2.py` | recap FP **0.42 (5/12; [0.19, 0.68])** against a 0.02 bound; precision 0.55 (6/11; [0.28, 0.79]); recall 0.75 (6/8; [0.41, 0.93]). The 5 were R02/R05/R08/R10/R11, exactly the prior | FAIL as text; see next row |
| M5 | the same 5 end to end at real post times (F3/F4 in play) | `atk4c_r2.py` | same night, tip + 3h10m: 0/5 written (f3_day). Late tip, back-to-back, 01:15 ET: 0/5 (f4_prev_game). **Late tip, back-to-back, 01:45 ET: 5/5 written as live facts on the next game ([0.57, 1.00])**, which is N1 | FAIL (N1) |
| M6 | mutate one byte of each file in a scratch copy | inline (`parser_fingerprint(root)`) | all 6 files change the hash; a missing file raises. **The rule draft still pins "parser sha256: <nba/ingest/reporter_parse.py>"** (`docs/prereg/REPORTER_FEED_LIVE.md:133` on agent/modeler-reporter-feed @ eae9efa) | PASS (code); N4 (rule) |
| M7 | Tue 13:00 ET post about Wed, team idle Tue | `atk3b_r2.py` C1 via `poll(prev_tips=...)` and via the real `run_tick` | the poll starts at Mon tip + 3 h (22:30 ET); 1 post read, status ok, both paths. The live schedule cache holds the full season (1200 games, 47 already played), so `prev_tip` is found in production | PASS |
| M7 (residual) | league-wide handle across a league-idle day (Thanksgiving) | `atk3b_r2.py` R1 | the poll starts Thu 19:30 ET, so a Thu 14:00 post is never requested, and the status is still ok. 3 of 30 teams rely on the one league-wide handle | N3 minor |
| m1 | a server that always returns another page; `--max-usd` below the bound | `atk7_counts.py`; inline | 28 requests = the hard bound, $0.28 = `--max-usd` (round 1: $0.32 against $0.24). `--max-usd 0.24`: BudgetExceeded, 0 requests. Dry run 0 requests; no `--confirm` exits 2; no token exits 2; 2025-26 exits 2 | PASS |
| m2 | page 2 fails after page 1 | `atk3_cursor_cov.py` C3 | 1 raw file written, status error:Unreachable, cursor not advanced | PASS |
| m3 | stuck lock | read `reporter_tick.py:280-291`; test `test_a_stuck_tick_lock_is_an_alert_not_a_quiet_skip_m3` | rc 2 after 600 s | PASS |
| m6 | post text in any insert-only store | read the store from atk2 | `reporter_facts.value_text` holds post_id plus evidence_sha256 (and metadata), with no text; `claim_rejects.detail` holds clock_fault timestamps only. Raw parquet still holds the text, by design: Devin's C5 deletion term applies there and is not verified | PASS |
| write-once | store sha across 2 real builds plus 1 locked build | `atk2_store.py` | 025a94969e3d4ba4, unchanged x3; 6/6 rows identical including fact_id; the locked build exits 0 "skipped (not dropped)" | PASS |
| closure | prediction-path imports | not re-run: `nba/daily` is not in the diff | n/a | n/a |

Tests, unpiped: `pytest tests/ingest/test_reporter_{capture,facts,lineups,parse,pretip}.py tests/ingest/test_reporters.py tests/facts tests/ops tests/test_layering.py tests/shadow_llm`: **542 passed, 1 failed, exit 1**. The failure is `tests/shadow_llm/test_runner.py::test_mixed_forward_table_still_settles`. It fails identically on main at 75a6b6e with main's own code (`git archive 75a6b6e` into the scratchpad, `PYTHONPATH` pinned; `nba.__file__` checked): pre-existing, not this branch. `ruff check nba tests` 0; `ruff format --check nba tests` 0; `mypy nba` 0 (351 files). A bare `ruff check .`/`mypy` exits 1 only on research/scratch and app/fixtures files, which this branch does not touch (committed 10-22).

### Remaining defects (file:line, severity, smallest fix)
- **N1 major (before the tick is installed)**: `nba/ingest/reporter_facts.py:525`. Resolve F3's tonight/today/this evening against the ET date of `created_at - 6 h` (a post before 06:00 ET belongs to the previous evening). Add a test: a 22:30 ET tip, a back-to-back, a "tonight" post at 01:45 ET writes nothing. Also add to kill criterion (b) of the draft: any qualifying fact whose created_at lies between the team's previous tip and 06:00 ET the next day.
- **N4 major (before Stage A)**: `docs/prereg/REPORTER_FEED_LIVE.md:133` (agent/modeler-reporter-feed). Replace "parser sha256: <nba/ingest/reporter_parse.py ...>" with "parser_fingerprint(): <nba.ingest.reporter_pretip.parser_fingerprint() over FINGERPRINT_FILES>". The same draft also says the store is at `data/reporters/reporter_facts.duckdb` and posts_per_day is 600 (line 116); the code says `data/facts/` and the config says 400. Align them before the freeze.
- **M5 open (before Stage A)**: no text-only fix reaches a 0.02 recap FP bound (fresh set 0.42, [0.19, 0.68]). Acceptance must come from the 5 live nights, with labels written by someone other than the builder. The 121+6 dev rows and my 24 are both dev now; the 20 in `r2/atk4b_labels_frozen.py` are spent too (I have run them).
- N2 minor: `reporter_facts.py:411`. Return `_merge_per_post_player(out, Counter())` from `claimed_store_facts`.
- N3 minor: `reporters.py:949`. For a league-wide handle, cap the lookback at `max(max_lookback_hours, hours since the last game day's last tip + 3 h)`, capped at 48 h. Or log `short:lookback_cap` when the cursor is older than the cap, so the gap is not silent.
- N5 minor: `nba/facts/build.py:524` copies every store predicate, and the payload filter (`payload.py:387`) matches by prefix only. Restrict the copy to `predicate LIKE 'claimed_%' OR predicate LIKE 'reporter_%'`, or also drop `source LIKE 'x:%'` in `_facts_section`.
- N6 minor: `FINGERPRINT_FILES` (`reporter_pretip.py:315`) leaves out `nba/kalshi/aliases.py` and `nba/props/rosters.py` (both decide player resolution). Add them.
- N7 minor (cost knob, Devin's): M7 widens the first game-day poll of a team handle from 18 h to up to 48 h. With posts_per_day 400 over about 20 handles, a busy day can run into budget refusals. They are visible (refused:not_reached, T-55 alert), not silent; watch the first two weeks.
- N8 minor: no CLI-level test for M2 (`parse-only --store` on a raw_backfill file and on an outside file). `atk1_knownat.py` A1b/A1d are the shape of the test.

### Playbook check
Class: an information source (a pre-tip text feed) feeding a fixed adjustment; nothing fitted; lineage INJURY_TIMING. (1) Information or capacity: information, so the known-at stamp and the game it is attached to are the whole test. N1 attaches the wrong game with an honest stamp, which is the failure the stamp checks cannot see. (2) Data: unchanged since round 1; M7 adds rows, N7 may remove some. (3) Test validity: the M5 text set is no longer held out, so an invalid test is not evidence. The live-night acceptance is the valid test. (4) Lineage and inherited guards: first_seen from our own fetch (now after the response), the raw index decides live vs backfill, write-once, allow-list, and the payload's predicate guard. (5) Scored against production's T-60 row; this branch does not touch it.

### Single most likely failure mode if "capture is leak-free" is wrong
N1: West-coast late games on back-to-backs put postgame "tonight" caps onto the next game as pre-tip facts. Caps often persist across a back-to-back, so these rows will look correct often enough to pass a spot check. That is the precedent shape: right-looking values arriving through a timing pattern.

### Not verified
The replay oracle (not run). exclude=replies on live X. Real posts per handle per day (N7). Raw-store deletion handling for X terms (C5). Whether launchd timing slips push the T-70 poll past T-60.

### Drafted row (PREDICTIONS_LEDGER, not appended)
| 2026-10-27 20:30 | adversary prior, fresh reporter recap set (r2/atk4b_labels_frozen.py sha 4fe9389d): about 5/12 recaps qualify (R02, R05, R08, R10, R11) | result 5/12, the same five | docs/reviews/reporter_capture_2026-10-27.md (Round 2) | scored: hit |
