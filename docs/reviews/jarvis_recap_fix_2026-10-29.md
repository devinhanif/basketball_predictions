# Adversary review: agent/market-jarvis-recap-fix at e8c49e3 (2026-10-29)

Verdict: APPROVED

Scope: a fix to the live path (nba/jarvis, configs/jarvis.yaml), not a model claim. The six model checks (shuffled
labels, planted future, missingness, knockout, replication, integer support) do not apply: nothing here is scored and no
number is claimed. What this review checks is whether the code fails safe.

Incident: on 2026-10-28 every JARVIS tick after the recap hour crashed at `posts.py:235`
(`f"{kill.mean_clv:+.2f}"` with `KillState(False, n, None)` for 0 < n < min_bets). The err log has 463 tracebacks, and
all 463 are this line (`grep -c 'posts.py", line 235' data/ops/launchd_jarvis.err` = 463). Nothing posted that day and the
halftime scorer never ran.

## Checks

| # | Check | Command / evidence | Result | Pass |
|---|---|---|---|---|
| 5 | Reproduce on main | `cd <main> && .venv/bin/python scratchpad/adv_recap/adv_tick.py 2026-10-29T00:10:00 <scratch book copy>` (dry run, fixed clock, scratch copy of the paper book, recording sender that asserts `dry_run=True`) | exit 3, `TypeError: unsupported format string passed to NoneType.__format__` at posts.py:235 | crash reproduced |
| 5 | Same tick on branch | same command, cwd = worktree | exit 0; `tick 2026-10-29T00:10:00: plays=0 alerts=0 slate_send=yes \| ht_scored=137rows/2games`; 3 dry sends: recap for 10-27, slate preview, `halftime overs MIA@ATL` | PASS |
| 1 | Recap guard hides nothing | Injected a sender that raises inside the recap (first version of the script): stderr got `recap failed; the tick goes on (RuntimeError)` plus the full traceback (via logging's lastResort handler; cli.py sets up no handler); `rep["recap"] = {"kind":"recap","ok":False,"error":...}`; `tick_line` adds `recap_error=<type>` whenever `recap.ok is False` (runner.py:366-367). Test `test_a_recap_bug_cannot_take_the_halftime_check_in_down` pins the line. | logged on every failing tick, and in the tick line | PASS |
| 1b | Side effects of a recap that keeps failing | `_maybe_recap` runs `score_book` and `update_weights` before formatting. `update_weights` has a `min_days_between_updates` guard (weights.py:86-89), and model_weights.jsonl has 6 rows, all dated 10-26, so the 463 crashes did not step any weight. `score_book` only inserts unscored plays. | no repeated weight step | PASS |
| 2 | Other None format specs | `grep -nE '\{[^}]*:[+ ]?[0-9]*\.[0-9]+' posts.py render.py halftime_overs.py`: 25 sites traced. All are guarded or non-null: posts.py:121 (`feed_age is not None`), :147 (truthy), :212/:229 (`pnl_usd` = `round(sum(...))`, never None), :217-218 (config floats), :226-227 (guarded), :235 (now guarded); render.py:21/63/72/81/122-124/218/220/228 (guarded or dataclass floats); `overs_lines` uses `round(100*p_glm_e)`, and `p_glm_e` is `DOUBLE NOT NULL` (halftime_store.py:48), `bid_c` is non-null after `display_ok`. Also weights.py:142 (`mean_clv_cents` is None only when action == hold, which is filtered out) and book.py:710 `float(sum(pnl_usd))` (no NULL pnl is ever written, book.py:611-618, 640-651). | no remaining None formatting | PASS |
| 3 | IOException path | runner.py:304-305 re-raises `duckdb.IOException` to `run_tick` (runner.py:285), which returns `idle` and no alerts. `describe_store_busy` (sources.py:66-88) only parses the message and runs `ps -p <digits> -o command=` (argv list, no shell, `timeout=2`, `check=False`, OSError/SubprocessError caught). A gone PID gives empty stdout, rendered as "exited since". PID owned by another user: ps still reads it, read-only. No unlink, open or lock call. Test with a real holder subprocess: `test_a_real_duckdb_lock_is_named_in_the_idle_report`. | idles; never touches the lock | PASS |
| 4 | post_without_plays: true gates | runner.py `_halftime_overs_one`: `top_overs(rows, game, min_edge=0.05, top_n=3, display_min_bid_c=1, display_max_spread_c=10)`, unchanged. Live capture at 00:10Z: MIA@ATL 137 rows, 11 with edge >= .05, 9 pass display, 3 picked, 5 lines (header with "UNPROVEN, paper", 3 "(Yes)" lines, "no stake suggested"). WAS@CLE at the half had 0 rows, so 0 picks, `overs_lines` returns [] and the code returns None: no message (seen in the dry run: only one overs send). Rows are Yes asks by construction (no side column; the label is hard-coded "(Yes)"). Dedupe per game is unchanged (`kind_sent("halftime_overs", slate, game_id)`). | gates hold; no empty message | PASS |
| 6 | Tests | `.venv/bin/python -m pytest tests/jarvis tests/markets tests/test_layering.py --no-cov -q -p no:cacheprovider` | 465 passed, exit 0 | PASS |
| - | Lint/type | `ruff check nba/jarvis tests/jarvis` exit 0; `ruff format --check` exit 0 (44 files); `mypy runner.py sources.py posts.py render.py cli.py` exit 0 | | PASS |

## Findings (none is a blocker or a major)

1. **minor**, runner.py:304-305. The guard re-raises every `duckdb.IOException`, not only lock conflicts. A non-lock
   IOException in the recap (corrupt or unreadable file) would idle the whole tick on every pass, which is the 10-28
   outage again. This predates the branch for the rest of `_tick`, but the recap is the new guard. Smallest fix:
   `except duckdb.IOException as exc: if "lock" not in str(exc).lower(): <treat as recap failure>; else: raise`.
2. **minor**, runner.py:306-308. A recap that keeps failing shows up only in launchd logs (the tick line and a traceback
   every tick, about 288 a day). Devin sees only that the recap never arrived. Smallest fix: once per ET date, post one
   line "morning recap failed (<type>); see data/ops/launchd_jarvis.err", or have the watchdog alert on `recap_error=`.
3. **minor**, sources.py:87. The store-busy message prints the holder's full command line, untruncated, into the launchd
   log. Keys are env-only by policy, so the risk is low, but any secret passed on argv would land in the log. Fix: keep
   `cmd[:160]`. A reused PID would also name the wrong process; that is cosmetic.

## Playbook check

The class is live-path ops code that displays an UNPROVEN paper model (T508 halftime overs artifact, digest-pinned). This
is not a model review.
(a) Information or capacity: n/a, no model change.
(b) Data: n/a.
(c) Test validity: the dry tick replays the stored 10-28 capture at a fixed clock with no writes and no sends; it is a
    valid regression test for the crash.
(d) Lineage: the digest pin, min_edge, top_n and display gates are inherited unchanged.
(e) Scored against: nothing. The line stays paper, with no stake.

## Not verified

- A real (non-dry) send path: no Discord was used, so dedupe after a real post (`kind_sent`) was read in the code, not run.
- The replay oracle for this branch (holdout log row 03b86fe belongs to the main session).
- launchd behaviour (PATH for `ps` under launchd: /bin is on the default launchd PATH; FileNotFoundError is caught anyway).
- Whether Devin's go for `post_without_plays: true` exists outside the commit message.

## Scope

The diff matches the task: recap None-safety, the recap guard, the busy message, tick-line flags, scored_line None-safety,
the config flip and the doc line. No scope creep found.
