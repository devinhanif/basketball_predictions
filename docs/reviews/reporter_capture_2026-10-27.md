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
