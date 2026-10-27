# Adversary review: morning-job advisory monitors (branch agent/steward-morning-advisory, 91d295a)

Reviewed 2026-10-27 against main 6b72db8. Scope: ops/nba_daily.sh, nba/ops/app_export.py, nba/ops/coverage.py,
nba/datamanifest/{__main__,diff,manifest}.py and their tests. Context: docs/reviews/morning_failures_2026-10-27.md;
DECISIONS 2026-10-27 15:59 (Devin's go on items 1-3).

Verdict: APPROVED

Adversary grade: SURVIVES, with six minors and no blocker or major. No real hard-stop class (leak, schema,
row loss, PK duplicates, unknown kind) can pass the export gate now. The pairing check is weaker than its
wording says (see m3, m4), which is acceptable for a monitor as long as nobody cites it for a market claim.

Playbook check: no model is involved. This is an ops monitor and gate change, so the model-class checks
do not apply. What it is scored against: the morning step records and the replayed data below.

## What was attacked, with commands and results

All runs used the branch worktree, quiet copies, and read-only connections: the 07:09 backup
`data/backups/nba_20261027.duckdb` and a scratch copy of `market_asof.duckdb`. Nothing under data/ or the
main checkout was written, and season 2025 was not loaded.

| # | Attack | Command (scratch scripts) | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Hard flag mixed with the real advisory flags on the real 10-26 -> 10-27 manifest pair | `uv run python scratch/plant.py` (plants into copies, runs `python -m nba.datamanifest --config <copy> check`) | 7 plants, plus 9 real COVERAGE_DROP each | none: rc 3; POSSIBLE_LEAK, ROW_DROP, PK_DUPLICATES, TYPE_CHANGE, TABLE_REMOVED, LABEL_NULL_GAP: rc 1 each | pass |
| 2 | Unknown flag kind / mixed kinds | `tests/datamanifest::test_check_exit_code_splits_hard_from_advisory` (`all(kind in ADVISORY_KINDS)`, diff.py:18-22) | 8 kinds | unknown kind -> 1, mixed -> 1 | pass |
| 3 | Can a crash exit 3? | `uv run python -c` raise / SIGQUIT / SIGTERM; `-m nba.datamanifest --config /nonexistent check`; bogus subcommand | 5 | 1, 131, 143, 1, 2. No `exit(3)`/`SystemExit(3)` in nba/datamanifest (grep). uv 0.12.23 maps signal to 128+n | pass |
| 4 | rc captured from the right command; set -e / pipefail | read ops/nba_daily.sh:141-158 | n/a | `/bin/sh` with `set -u` only (no -e, no pipefail). `rc=$?` is taken right after the bare command and before `log`. 127 (cannot exec) is hard | pass |
| 5 | Shell syntax | `bash -n ops/nba_daily.sh`; `sh -n ops/nba_daily.sh` | n/a | exit 0, exit 0 | pass |
| 6 | Gate fails closed when a record is missing | app_export.py:53-64, 84-109; tests/ops/test_app_export | n/a | data_check stays in REQUIRED_STEPS. The rc-3 path writes `data_check 0 ok` plus `data_check_advisory 3 failed`; a missing or foreign steps file, an unreadable line, or a missing required step still skips. Only app_export reads the steps file (grep) | pass |
| 7 | Historic replay of `check` (branch code, today's config) on every consecutive manifest pair from 10-19 to 10-27 | `sh scratch/replay_check.sh` | 7 pairs | all rc 3. Only COVERAGE_DROP and NULL_SHIFT appear: the 10-21..23 POSSIBLE_LEAK flags were cleared by the earlier `known_asymmetry` wildcard, not by this branch | pass |
| 8 | Manifest numerator on real data | `uv run python scratch/transition.py` (build_manifest on the 10-27 backup, diffed against the 10-27 manifest) | 33 game tables | forward_* and player_availability go 1.077 -> 1.0 (56 games, 52 completed). Completed-game tables are unchanged (player_game_tracking stays 0.827 = 43/52). preseason_games -> 0.0 for every season (m1) | pass, with m1 and m2 |
| 9 | Pairing replay on the 10-26 slate | `uv run python scratch/pairing.py <copy> 2026-10-26` | 9 games, 474 quoted | branch: 467/474 paired, all OK. Stored made_at alone: 4/474. Last confirming run is 10 min before tip on every game | pass, with m4 |
| 10 | Stamps after tip must not count | same script, every tip moved 3 h earlier | 9 games | 8 FAIL, 1 WARN (4/53) | pass |
| 11 | Pairing at the comparator lead (confirmation capped at tip-60) | `uv run python scratch/t60.py <copy>` | 474 quoted | 336/474 (71%). Both 23:00Z games are 0/53 and 0/52 (latest run at or before T-60 is 21:50Z; first quote 21:57Z) | informational (m4) |
| 12 | Prediction path untouched | `git diff --name-only main...91d295a`; import closure of nba.daily.pipeline / store / nba.truth.metrics | n/a | nba/daily and nba/truth are not in the diff. nba.daily imports nba.datamanifest.manifest only for `latest_data_version` (unchanged); `compute_data_version` hashes schema and fingerprint only, so stamps' data_version does not change | pass |
| 13 | Scoped verification | `ruff check nba tests`; `ruff format --check nba tests`; `mypy nba/ops/coverage.py nba/ops/app_export.py nba/datamanifest`; `pytest -q --no-cov tests/ops tests/datamanifest tests/test_layering.py` | 246 tests | 0; 0; 0; 246 passed, exit 0. With the repo's coverage plugin on, the same run exits 1 only on the global `fail-under 70%` floor (2.56% on a scoped run); there were no test failures | pass |

There are no statistical claims here, so no CI or multiplicity applies. The counts above are complete
enumerations, not samples.

## Defects (none block the merge)

**m1 (minor): preseason_games coverage is now 0.0 for every season.** nba/datamanifest/manifest.py:218-237.
Preseason game_ids are never in `games`, so `games_completed = 0` (on 10-27: 66 games, 0 completed; it was
0.049 for 2022-2025 and 1.27 for 2026). The ratio was meaningless before. Now it is a constant 0, so a real
preseason gap can never show up as a COVERAGE_DROP (ROW_DROP still catches deletions). Smallest fix: line 237,
`ent["coverage"] = _r(done / of) if of and done else None` (no completed-game overlap means not measurable,
not 0). Alternative: a `coverage_exclude: [preseason_games]` list in configs/datamanifest.yaml.

**m2 (minor, expected): one advisory alert on the first morning after the merge.** The first new-formula
snapshot is diffed against today's old-formula manifest. My simulation on the 10-27 data gives 12 COVERAGE_DROP:
7 tables at 1.077 -> 1.0 and preseason_games in 5 seasons. That is rc 3: an alert, the export still runs, and
the heartbeat stays green. No code fix is needed. Tell Devin to expect it, or apply m1 first, which removes
the 5 preseason flags.

**m3 (minor): the "confirmation" is per run, not per game or per player.** nba/ops/coverage.py:559-580 takes
`max(made_at)` over every props_context_residual stamp before tip, with no link to the game or its slate date.
These cases now count as paired: (a) a hand-run `nba.daily run --date <another day>` before tip; (b) a later
run that no longer produced this player (ruled OUT or dropped from the roster), whose stale row is credited
as confirmed; (c) `real_tip=False` games, where a run after the real tip but before the proxy tip counts.
None of this is recorded per game, because the store and grid199 both write on change only, so (b) cannot be
fixed cheaply. Smallest fix for (a): line 575, add `AND made_at >= ?` bound to the UTC start of the game's ET
date (`g.tip_et` date at 00:00 ET). For (b) and (c), change the wording at line 544 to "latest props run
before tip" instead of "last-confirmed". The fallback (no table, or a duckdb.Error -> `{}` -> stored made_at)
fails in the conservative direction (more FAILs), which is right.

**m4 (minor): an OK pairing is not the T-60 comparator's pairing.** On 10-26 the branch reports 467/474
paired, but at the FORWARD_PREREG lead (confirmation at or before tip-60) only 336/474 pair, and the two
23:00Z games pair 0%. That is acceptable for a monitor, whose question is whether lines were captured while
production runs were still going. The FAIL still fires when pretip runs stop before the first quote
(attack 10), and still fires when capture is down (no quotes -> the separate "no main-line quote" result).
But the check must not be cited as evidence that the scored forecast had a market price. That is the
diagnosis's item 4, which still needs a written rule. Smallest fix: one sentence in the line-544 text or
the module docstring: "monitor only; the scored row is the latest made_at <= tip-60 (FORWARD_PREREG rule 3)".

**m5 (minor): docs not updated.** docs/DAILY_PIPELINE.md:752-755 (the gate paragraph) does not mention
ADVISORY_STEPS, `data_check_advisory`, or rc 3. Line 951 still says "The morning job now has one advisory
step". Smallest fix: list the three advisory steps in the gate paragraph, add "data_check rc 3 = advisory
diff flags only (COVERAGE_DROP, NULL_SHIFT, STALE_SCHEDULED), export not held back; any other rc is a hard
stop", and change "one advisory step" to "advisory steps (stints_nightly, coverage_yesterday)".

**m6 (minor, residual risk inside Devin's approval): NULL_SHIFT and STALE_SCHEDULED are real data signals
that now only alert.** Two cases from the record: 10-24 `forward_t15_decisions.snapshot_id` was 21% NULL,
an operational gap in the T-15 decisions; and STALE_SCHEDULED's own text says "features count it as played".
Both would now alert without holding the export back. The export gate never protected the forecasts (pretip
does not read data_check), so this changes what the app shows, not what is predicted. Optional tightening if
Devin wants it: keep NULL_SHIFT hard for a short list of core input tables (games, player_game_stats,
player_availability) via a config key read in diff.py:13-22. Not required by the approved scope.

Separately, the heartbeat no longer goes red on a coverage_yesterday FAIL or on advisory flags, so the
watchdog will not page on them. ALERTS.md and the macOS notification remain the only signal. This is as
designed, and worth one line in the handoff.

## The single most likely way this is wrong

A real data loss arrives only as a COVERAGE_DROP or NULL_SHIFT: games never ingested into a completed-game
table, or a feed that starts writing NULLs. Neither ROW_DROP nor a leak flag fires, so the app exports on top
of it with one alert line. The tracking lag (player_game_tracking 43/52 every morning) means a COVERAGE_DROP
alert on that table will become routine, and routine alerts get ignored.

## Not verified

- The full bash morning flow on the real launchd host. Only the stubbed harness (tests/ops/test_daily_script_flags)
  and `bash -n` / `sh -n` were run. The replay oracle was not run, per instructions.
- How uv behaves on exit codes after an upgrade. The 128+n mapping was measured on uv 0.12.23 only.
- How often case m3(b) happens (an OUT or dropped player with a quote whose stale row is now counted as paired).
  The data does not record which players a deduped run covered.
- The earlier POSSIBLE_LEAK suppression (the known_asymmetry wildcards); it predates this branch.

## Recommended changes (not applied by me)

1. m1: one-line guard at manifest.py:237 (or a config exclude), before the first post-merge morning.
2. m3(a): slate-date lower bound in coverage.py:575; m3/m4: reword coverage.py:544.
3. m5: DAILY_PIPELINE.md:752-755 and :951.
4. Merge after tonight's last pretip run (DECISIONS 15:59: bash reads a running script incrementally).

## Proposed rows (drafted, not appended)

DECISIONS (optional, only if Devin takes m6's tightening): `| 2026-10-27 | NULL_SHIFT on core input tables
(games, player_game_stats, player_availability) stays a hard data_check flag; other NULL_SHIFT advisory |
adversary m6: a feed writing NULLs reaches the app with one alert line | two weeks with no false hard stop
from these tables -> keep; any -> revisit |`
