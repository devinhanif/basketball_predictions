# Adversary review: agent/steward-morning-fixes (f6ba98d, 6e40df6, a1d150a), 2026-10-24

Pre-merge code review of a live-path branch (the 07:00 CDT morning job), diffed against merge-base 3904d59.
This is plumbing, not a model claim, so the six statistical checks are mostly n/a. The attacks below target the
seven questions in the brief. The replay oracle is run by the main session and is not covered here.

Verdict: BLOCKED

Read BLOCKED as CHANGES_REQUIRED on 6e40df6 only. Two small fixes are required (R1, R2 below).
f6ba98d is APPROVED and safe to merge alone. a1d150a is safe to merge alone because nothing on the live path imports it;
it carries one governance note (M3).

## Per-commit verdict

| commit | what | verdict | merge alone? |
|---|---|---|---|
| f6ba98d | coverage: no ALERT echo feedback, streamed logs | **SURVIVES**, APPROVED | yes |
| 6e40df6 | postgame pending / scope / personId 0 / load isolation / datamanifest wildcard | **WOUNDED**, needs R1 and R2 | after R1 and R2 (see "If R1/R2 cannot land before 07:00") |
| a1d150a | dated exclusions (read-only), docs | **SURVIVES** for code; M3 for governance | yes (its DAILY_PIPELINE section also describes 6e40df6; merge it after 6e40df6, or accept a doc that is briefly ahead of the code) |

## Checks (exact commands; every one run unpiped, with its exit code)

Review tree: `git worktree add <scratch>/wt_mf agent/steward-morning-fixes` (HEAD a1d150a). The original worktree
`.claude/worktrees/agent-abc7f58eec16406cd` had been switched to `agent/steward-live-capture-launchd` during the review,
so nothing was read from it. Toolchain: `/Users/devin/Downloads/nba-prediction/.venv-1/bin` (the main `.venv` has no pytest/ruff/mypy).

| # | question | command / attack | n | result | pass |
|---|---|---|---|---|---|
| A1 | Does coverage's alert check reach a fixed point? | `scratch/attack/fixedpoint.py`: append the real `cv.render()` output plus `start/end coverage` stamped lines to the pretip log 8 times; a planted stamped-and-indented echo is included | 8 runs | alert count `[1,1,1,1,1,1,1,1]`; log grows by a constant 1108 B per run (linear, bounded by the 50-line cap); the stamped echo `[stamp]     echo: [stamp] ALERT:` is not counted | PASS |
| A2 | Does the `_KEEP` filter drop lines that readers need? | read `parse_pretip_runs` (`^run \w+ date=`, `models:`) and `_stamped` (`^\[stamp\] `) against `_KEEP` | 3 readers | every pattern a reader matches is kept. Every line coverage prints starts with a status word or 4 spaces (`grep -n '\\n' nba/ops/coverage.py`: lines 649, 651, 680 indent) | PASS |
| B1 | Can "pending" hide a real failure forever? | `scratch/attack/pending.py`: an endpoint whose constructor raises `AttributeError("'DataFrame' object has no attribute 'append'")` (a library/pandas incompatibility), and one that raises `IndexError` (renamed result set) | 2 | both become `NotPublishedError`, which counts as **PENDING**. `__main__.py:254` `return 1 if summary.failed else 0` then exits 0. No staleness alarm exists: datamanifest flags only COVERAGE_DROP/ROW_DROP, not a table that stops growing | **FAIL** (R1) |
| B2 | Are there real keys that already fail permanently? | read-only `data/{officials,matchups}/ingest_log.duckdb` (`status='failed'`) | 5 historical keys | officials 0022500259-261; matchups 0022300383, 0022301230 have failed every morning. If their cause is a null payload, the branch silences them for good. The cause was not verified (I made no network calls) | risk |
| B3 | Is throttling misread as pending? | read `_with_retry` and `fetch_cached` | n/a | timeouts and HTTP/JSON errors still count as FAILED and feed the breaker. Edge case: attempt 0 raises, attempt 1 returns empty, so `err=None`, then `EmptyFetchError`, then PENDING. A flaky throttle can end as pending. Minor; R1 covers it | minor |
| C1 | Does the scored-only scope drop late-scored games? | read `scope_game_ids`; the scope is recomputed every run | n/a | a game enters scope the run after its score lands. Nothing is dropped permanently | PASS |
| C2 | Does "scored" mean "final"? | read-only `nba.duckdb`: `SELECT game_id, home_pts, away_pts FROM games WHERE season=2026 AND game_date>='2026-10-23'` at 04:32Z | 12 rows | 0022600098 holds **53-65** about 4.5 h after its 00:00Z tip, a mid-game score. The docstring's "scored = has post-game data" is false. A fetch run during a game (backfill/queue) could cache a partial box as `done` forever. This predates the branch (the old scope was all games), so it is not a regression | minor (R3) |
| D1 | Could dropping personId 0 drop real rows? | read-only `SELECT count(*) FILTER (WHERE player_id=0), count(*)` | hustle 106,428; tracking 139,235 | 0 rows with player_id 0 in either table. NBA person ids are never 0. `_normalize_player_frame` serves only tracking and hustle; officials and matchups do not use it. The mixed-frame test keeps the 4 real rows | PASS |
| E1 | Does per-source isolation still exit non-zero? | `scratch/attack/isolation.py`: `load_source` raises for hustle; run `main(["--db-path",tmp,"--data-dir",tmp,"postgame-load", 4 sources])` | 4 sources | the other 3 sources load, hustle prints FAILED, **rc 1**. Each frame is its own BEGIN/COMMIT/ROLLBACK, so a failure leaves no partial frame. No committed test covers the `__main__` exit code (M2) | PASS |
| F1 | Does the wildcard mask anything beyond outcome columns? | `scratch/attack/wildcard.py` against `docs/data_manifests/latest.json` | 6 tables, 96 columns matched | flags with no allow-list: 22; with the branch's `forward_*scores*.*`: 0. **Planted** `forward_scores.pred` NULL iff unplayed (asymmetry 0.9) is **masked** by the branch pattern. The narrowed list in R2 still flags it, and still clears all 22 real flags | **FAIL** (R2) |
| F2 | Does the wildcard touch NULL_SHIFT? | read `diff.py:16-35, 136-143` | n/a | `known_asymmetry` feeds only `leak_candidates` (POSSIBLE_LEAK). NULL_SHIFT, COVERAGE_DROP and LABEL_NULL_GAP are untouched. No feature table matches the pattern | PASS |
| G1 | Does exclusions touch scoring? | `grep -rn "nba.ops.exclusions\|forward_exclusions" nba tests ops docs/*.md` | n/a | imported only by its own test. It opens DuckDB with `read_only=True`, writes nothing, and nothing in settle/forward_scores reads it | PASS |
| V | verification | `pytest -q -p no:cacheprovider --no-cov tests/ingest/test_postgame.py tests/ops/test_coverage.py tests/ops/test_exclusions.py tests/datamanifest/test_datamanifest.py` | 53 tests | 53 passed, exit 0 (with coverage on, exit 1 only from the 70% repo-wide fail-under on a subset run). `ruff check` (touched files) exit 0; `ruff format --check nba tests` exit 0; `mypy` (5 touched modules) exit 0 | PASS |

The six statistical checks (shuffled labels, planted future, missingness, knockout, replication, integer support) are n/a: no
metric claim is made. Planted-future and missingness logic was applied where it fits (F1 is a planted NULL pattern).

## Findings, severity-ranked, each with the exact fix

### Required before merge of 6e40df6

**R1 (major, blocks 6e40df6): "pending" has no staleness bound.** `nba/ingest/postgame.py:187-198` maps every
`AttributeError`/`IndexError` raised inside an nba_api endpoint to `NotPublishedError`. `fetch_source` (`:665-667`) then puts the
key in `pending`, and `nba/ingest/__main__.py:254` exits 0. If nba_api, pandas or the endpoint schema breaks, every key becomes
"not published yet" every morning, rc stays 0, the step never alerts, and tracking/hustle/officials/matchups silently stop
growing. Nothing downstream alarms on a table that stops growing. Fix (about 15 lines plus one test):

```python
# nba/ingest/postgame.py
PENDING_GRACE_DAYS = 2  # endpoints publish about a day after the game

def stale_pending(nba_db: str | Path, keys: list[str], today: date | None = None) -> list[str]:
    """Pending keys whose game is older than the grace period: not 'not yet', but broken."""
    if not keys:
        return []
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    con = duckdb.connect(str(nba_db), read_only=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT game_id FROM games WHERE game_id IN (SELECT unnest(?)) AND game_date < ?",
            [keys, today - timedelta(days=PENDING_GRACE_DAYS)]).fetchall()]
    finally:
        con.close()
```
```python
# nba/ingest/__main__.py, replacing line 254
        stale = stale_pending(db_path or DEFAULT_DB_PATH, summary.pending)
        if stale:
            print(f"postgame-fetch[{summary.source}] STALE PENDING (> {PENDING_GRACE_DAYS} d): {stale[:20]}")
        return 1 if summary.failed or stale else 0
```
Test: a scope game dated 2023 whose fetch raises `NotPublishedError` gives exit 1, and a game dated today gives exit 0.
The five historical keys in B2 will then alert as before. That is the status quo and the honest outcome; if they are
confirmed permanent, list them in a reviewed config, not in code.

**R2 (major, blocks 6e40df6): the wildcard covers whole tables.** `configs/datamanifest.yaml:46`, `- "forward_*scores*.*"`,
also exempts the forecast-time columns of six tables (`pred`, `p_play_prod`, `p_play_r`, `starter_rate`, `lead_min`,
`snapshot_fetched_at`, `made_at`, ...). A forecast column that is NULL iff the player did not play is the
opp_adjusted_ridge fingerprint, and here it would mean a forecast row was written or changed after the outcome. F1 shows
the planted case is masked. Replace line 46 with exactly the columns that settlement fills:
```yaml
known_asymmetry:
  - "forward_*scores*.y"
  - "forward_*scores*.crps*"
  - "forward_scores_elig.pit_*"
  - "forward_scores_elig.pinball19"
  - "forward_scores_elig.rps"
  - "forward_scores_elig.q10"   # copied from pred at settlement (nba/daily/settle.py:405)
  - "forward_scores_elig.q90"
```
Against `latest.json` this clears all 22 current flags and still catches the planted `pred` (F1). Update the test at
`tests/datamanifest/test_datamanifest.py:308` to use one of these patterns, and add a case where `forward_scores.pred`
must still flag.

### Recommended (not blocking)

- **R3 (minor, pre-existing):** `postgame.py` `scope_game_ids`: "scored" is not "final" (C2). Add
  `AND game_date < ?` with today in ET, so a same-day, possibly live game is never fetched and cached as `done`. Also correct
  the docstring, which currently claims a NULL-score row is the only kind without post-game data.
- **M1 (minor):** `_endpoint_frames` should be narrowed later. Build the endpoint with `get_request=False`, call
  `get_request()`, and map to `NotPublishedError` only when the raw body is `null` or the result set is empty. With R1
  in place this is hygiene, not a blocker.
- **M2 (minor, missing negative test):** no committed test asserts that `postgame-load` returns 1 when one source raises.
  `test_load_ignores_team_level_rows_and_sources_are_isolated` tests only `load_frames`, and the "isolated" in its name is not tested.
  Port `scratch/attack/isolation.py` as a test.
- **M3 (governance, a1d150a):** the `stale_price_2026-10-23` entry was listed on 10-23 while or after that slate played.
  Its selector (quote age at `made_at`) was knowable before tip and is outcome-blind, and Devin authorised it, so it is not
  a leak. It is still a comparison rule that did not exist before results. Two requests: (a) make stale-quote exclusion
  a standing, prospective rule in the forward prereg family (a new rule id, not an edit), and (b) wherever an exclusion is
  applied, report the model-vs-market number both with and without it. Separately, the yaml (`ops/forward_exclusions.yaml:4-6`)
  and DAILY_PIPELINE say readers "drop the selected rows", but no reader applies them yet. The shadow_predictions rule
  (`created_at` vs newest full snapshot) is described in a comment and not implemented. Reword to "selected; not yet applied
  by any reader", or wire it in, before anyone cites an excluded comparison.
- **M4 (minor):** `_is_placeholder` keys on file mtime (14 d). A backfill that rewrites old files makes them "recent". That
  only costs parquet reads, but a placeholder older than 14 days is never self-healed. Acceptable; note it.

## If R1/R2 cannot land before 07:00 (headless: question, options, safe default)

Question: should 6e40df6 merge as-is for tonight's run?
Options: (a) merge with R1+R2 (about 20 lines, preferred); (b) merge as-is tonight, with R1+R2 due within 48 h and a
DECISIONS row; (c) hold 6e40df6, so the morning job again times out (rc 142, lock held about 2 h), postgame_load fails, and data_check exits 1.
Safe default: **(b)**. A key cannot go stale inside the 2-day grace that R1 introduces, so one night of the unbounded
pending costs nothing that R1 would have caught. R2's masking risk over one night is low, because forward-score tables
are outputs and no model reads them. Option (c) has known costs every morning. This is the main session's and Devin's call; the
verdict on the commit stays BLOCKED until R1 and R2 are in.

## Playbook check

Model class: none. This is ingest, ops and datamanifest plumbing, so the class checks do not apply. Cross-cutting questions:
(1) information or capacity: neither; the change only decides whether data gets fetched, loaded and alarmed.
(2) enough data: n/a. (3) test validity: the fixed-point and isolation claims were re-derived by attack (A1, E1), not
taken from the commit's tests. The commit's fixed-point test feeds `a.text` with a rewritten prefix, not `render()`; A1
closes that gap. (4) lineage and inherited guards: R2 weakens an inherited leak guard (the
opp_adjusted_ridge NULL-pattern check), and R1 weakens the "fetch failure alerts" guard. (5) scored against: n/a; no
metric is affected. `exclusions.py` touches no scoring (G1).

## Corrected family and power note

No statistical claims, so no BH family and no MDE. The one count that matters: datamanifest flags 22 to 0 with the branch pattern,
22 to 0 with the R2 list, and planted `pred` asymmetry 0.9 masked versus caught.

## Biggest risk

A broken nba_api or endpoint becomes "not published yet" forever. rc stays 0, no alert fires, and the post-game feature
tables silently stop growing (R1).

## Not verified

- The real nba_api behaviour for the 10-21..23 null payloads, and what currently causes the five historical failed keys (no network calls made).
- The replay oracle (the main session runs it).
- The full test suite (53 targeted tests only), and launchd behaviour on the 9.9 GB logs (streamed scan time was not measured).
- No season-2025 rows were read. The B2 query read ingest_log bookkeeping keys only, no outcomes.

## Drafted rows (not appended)

DECISIONS (draft): `2026-10-24 | postgame "pending" (unpublished payload) is bounded: pending older than 2 days exits 1 |
why: a library or schema break otherwise looks like "not yet" forever with rc 0 | reverses if: endpoints are shown to
publish more than 2 days after the game.`
