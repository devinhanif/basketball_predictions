# Adversary review: in-game checkpoints (branch `agent/market-ingame-checkpoints`, commit a56acc4), 2026-11-10

Scope: **merge with the feature OFF** (`configs/jarvis.yaml` `ingame_checkpoints.enabled: false`), plus a pre-check of the
flag-ON path for Devin's later go, the amendment-A rule draft, and where the off-support glm_e is shown. Live path (JARVIS,
launchd every 120 s). No replay oracle run (main session's job), no season 2025, no live request, no write under `nba/` or `data/`.
Every attack ran on scratch copies of the code (`git archive a56f1af` = merge base, `git archive a56acc4` = branch) against
the main checkout's recorded capture, read-only (`nba.duckdb` opened `read_only=True` by `RecencyProvider`). Recency window
forced to `--min-game-date 2026-10-01` everywhere (the live config's `null` would read season-2025 box scores as features; I am
not allowed to load 2025). The comparison is old vs new on identical inputs, so the window does not affect the equivalence result.

**Verdict for merge with the flag off: SURVIVES.**
**Verdict for the later flag-on go: WOUNDED.** The scorer is sound: live at the real tick times matches replay row for row, with no
look-ahead and no backfill. Two user-facing wording problems on the card (F1, F2) need fixing before the flag is turned on.

Verdict: APPROVED

Top finding: when the flag is on, every over line on the card prints `(baseline glm_e NN%)` (`nba/jarvis/cards/text.py:204`). That
model is a logistic fitted only at the half (e = 24). At 30 and 35 game-minutes it flags 2 to 4 times as many player-stats as M on
the 11-08 replay (0.259 vs 0.129; 0.220 vs 0.052; n = 317 and 309 units). The card calls it a "baseline", and the draft itself
calls it "a mis-specified baseline, not a finding" (section 12.3).

Model class: gradient-boosted trees (T508 arm M, in-game line model) served off its training grid at checkpoints 5 and 45, plus
a logistic baseline (glm_e) used off its fitted support at every checkpoint. No result is claimed, so the six checks below test
the plumbing and the known-at handling. None of them tests a model gain.

## Per-check table

| # | Check | Command (scratch; `S` = session scratchpad, `R` = main checkout) | n | CI | Result |
|---|---|---|---|---|---|
| 1 | Halftime rows unchanged, old vs new code | `cd $S/{base,new} && $R/.venv/bin/python -m nba.markets.halftime_scorer replay --night $R/data/raw/live_2026-11-0{7,8} --artifact $R/data/models/halftime_ingame/2026-10-27/artifact.json --nba-db $R/nba.duckdb --db $S/out/ht_{base,new}_<night>.duckdb --parlay-config $R/configs/parlay.yaml --min-game-date 2026-10-01 --top 3`, then `select * exclude (written_at) from halftime_scored order by all` compared | 566 rows (11-07), 956 rows (11-08); 42 cols; skipped 37 / 74 identical; refused 0 / 0 | exact equality, no CI | **PASS**: row for row identical. Only `written_at` and `halftime_meta.created_at` differ (wall clock). The CLI JSON output also matches byte for byte (`cmp` exit 0 on both nights). |
| 2 | Flag-off inertness of the tick hook | `$S/probe_flag_off.py` (branch config as committed; `latest_scoreboard` patched to raise) | 1 call | n/a | **PASS**: returns `{'skipped': 'disabled'}` and `ck_scored = {}`. Scoreboard not read. `checkpoint_scorer` and `checkpoint_store` not imported. Store file not created. The only new module is `nba.jarvis.ingame_checkpoints` (4.5 ms). `ck_views({}) == {}`, so `CardData.checkpoint is None`, `checkpoint_field` returns None, `has_checkpoint=False`, and `_quiet` and `need_edit` behave exactly as on main. |
| 3 | Branch tests | `cd <worktree> && .venv/bin/python -m pytest -q -p no:cacheprovider tests/jarvis tests/markets/test_checkpoint_*.py tests/markets/test_halftime_{scorer,entry,store}.py` | 578 passed, 1 skipped | n/a | **PASS** (the global 70% coverage gate reports FAIL because this is a subset run, which is expected; see F5 for the uncovered glue) |
| 4 | Planted future / known-at (flag on) | `$S/sim_live.py` on 11-08: `score_night_checkpoints` called live at each of the **301 real JARVIS tick times** from `data/ops/launchd_jarvis.out`, then a replay of the same night, compared on 45 columns | 7,803 rows live, 7,803 replay | exact | **PASS**: live and replay are identical on all 45 content columns. `made_at < known_at`: 0 rows. `state_received_at > known_at` or `quote_received_at > known_at`: 0 rows. Existing test `test_live_never_reads_a_summary_or_page_received_after_now` covers the manifest `as_of` filter (`halftime_entry.load_manifest`, `ts > as_of` dropped). |
| 5 | Stale state against the quote (flag on) | `$S/stale_state.py live_2026-11-07 live_2026-11-08` (label-free) | 476 valid fires, 15,939 candidates | n/a | **PASS**: the quote page arrives 1.6 s (p50), 2.2 s (p90) and 2.5 s (max) after the state. In 0 of 476 fires did the scorer already hold a newer summary at `known_at`. The 90 s window allowed by `QUOTE_MAX_AFTER_S` is never used in practice. |
| 6 | Idempotence (flag on) | `sim_live.py`: re-run the last 5 ticks on the file store | 5 ticks | n/a | **PASS**: 0 rows added. Live-mode transient `tick_not_closed` (62 on 11-08) was retried and resolved: live fires 314 vs replay 252, with the same rows. |
| 7 | OT exclusion and period boundaries (flag on) | 11-08 live store: `max(period)`, e_used by checkpoint | 7 games | n/a | **PASS**: max period 4. e_used ranges from 5.15 (c=5) to 45.43 (c=45). Lateness at most 64 s (limit 150). `model_support` = extrapolated only at c = 5 and 45. Tests cover OT (`test_overtime_is_excluded`, `test_no_second_game_minute_checkpoint_after_regulation`), period-end states and halftime-as-non-state. |
| 8 | Live lag vs the 180 s window (flag on) | live stores: `made_at - known_at` per (game, series, checkpoint); 11-09 rerun with the same script | 252 decisions (11-08), 280 (11-09) | n/a | **PASS on 11-08**: lag min 35 s, p50 88 s, p90 147 s, **max 158.6 s**, so 100% fall within 180 s. **11-09: 7,923 live rows identical to replay, but 20 of 280 decisions (7.1%) were lost live** because of real tick gaps up to 745 s: 12 late-write, 4 misnamed stale, and 4 silent (F11). |
| 9 | Tick cost (flag on) | `sim_live.py` (warm process) and a cold-start probe | 301 ticks (11-08) / 197 ticks (11-09), 7 games each | n/a | p50 0.35 s / 0.25 s, p90 1.64 s / 1.51 s, max 4.54 s / 2.94 s per tick (warm). JARVIS runs `--once` in a fresh process every tick, so each in-play tick also pays a **cold** cost: artifact load 1.21 s, manifest load 0.53 s (31.6k lines at end of night), recency init 0.10 s. Estimated per in-play tick on a 15-game night: about 2.5 to 6 s, against a 120 s interval. Acceptable. |
| 10 | Store growth (flag on) | 11-08 live file size | 7,803 rows, 7 games | n/a | 3.4 MB per 7-game night. Scaled to about 7 to 8 MB on a 15-game night, that is on the order of 1 GB per season, before any DuckDB compaction. Acceptable; noted for the disk monitor. |
| 11 | No new alert condition | `git diff main...a56acc4 -- nba/ \| grep '^+' \| grep -E 'send\(\|post_main\|note_alert\|kind_sent\|alert\|notify\|mention\|ping'` (exit 0) | 10 matching lines | n/a | **PASS**: every match is a comment or docstring saying "no alert". There is no call to send, post_main, note_alert or kind_sent. The tests grep the new modules for delivery code too (`test_the_new_modules_have_no_delivery_alert_or_network_code`). |
| 12 | Gate arithmetic, recomputed | by hand from the draft's inputs (section 12.9) | 194 entries over 5 nights / 39 games | n/a | **PASS**: `2.5 x (2.2414 + 0.8416)^2 x 1.70^2 / 0.10^2 = 6,868`; 6,868 / 38.8 = **177.0** nights; 0.8 x 133 = **106.4**. The variants also check out: +0.13 gives 4,064 (104.7 nights); 95% gives 5,671; design effect 1.5 gives 4,121 (106.2 nights, see F7); +21% for H1 at 97.5% (9.505 / 7.851 = 1.211). |
| 13 | f on 11-08, re-derived | `$S/out/ck_replay_2026-11-08.duckdb`: units with n_prior >= 5, best edge >= 0.05 | 317 / 309 / 310 units at 30 / 35 / 40 | n/a | **PASS**: f(M) = 0.129 / 0.052 / 0.010 and f(glm_e) = 0.259 / 0.220 / 0.100, the same as the draft's 12.3 and 12.9. H2 first-trigger entries on 11-08 = 52 (321 units). |

Shuffled labels, missingness, knockout and integer support: **n/a**. No model gain is claimed, and the store has no outcome
column (`test_there_is_no_outcome_column_and_no_update_or_delete`). Replication: the halftime-identity check was run on two
nights (11-07 and 11-08), and the live-equals-replay check on two nights (11-08 and 11-09; on 11-09 the live run kept a subset of
replay's rows, F11).

## Findings (severity for the flag-ON go unless marked; none blocks the merge with the flag off)

**F1 (major, flag-on, user-facing).** The card overstates the prior. `nba/jarvis/cards/text.py:216` prints "late-game mark (30+
game-min: where an edge is expected)", and the module docstring `nba/jarvis/ingame_checkpoints.py:11` says "any edge is expected at
30 game-minutes or later". The draft's own prior is P(H2 passes) = 0.02 with expected R$ = -0.04 (12.11). The T508 attack found a
residual below the floor even at e >= 30. A reader in a team channel will take "where an edge is expected" as advice.
*Fix:* `"late-game mark (30+ game-min: the minutes a rule would test; no edge shown so far)"`, and in the docstring, "if an edge exists, the T508
attack points at 30+ game-minutes; none is shown".

**F2 (major, flag-on, user-facing; answers question 4).** The off-support glm_e is shown as a baseline.
`nba/jarvis/cards/text.py:204` prints `(baseline glm_e NN%)` on every over line at every checkpoint. The caveat at `text.py:230`
("Baseline glm_e is fitted at the half only.") appears only on the tail line, and only when at least one over is shown. It is the
first text cut if the field grows. On 11-08, glm_e lights up on 25.9% / 22.0% of units at 30 / 35 against M's 12.9% / 5.2%. A user
who sees M at 40% and "baseline" at 55% against a 38c ask learns nothing true from the second number. Elsewhere the exposure is
internal only: the paper table column `p_glm_e` (schema comment says off-support), `replay_main`'s
`late_family_first_trigger_glm_e`, and `reports/ingame_checkpoints/f_measurement_2026-11-09.json` (`f_glm_e`, **not labelled**
off-support in the JSON). The app and export do not read it (`grep -rn glm_e app/ nba/export` returns nothing).
*Fix:* drop glm_e from `_ck_line` until R1 (the per-checkpoint refit) exists, and keep it in the paper table only. Add a
`"glm_e_support": "fitted at e=24 only"` key to the f-measurement JSON writer.

**F3 (minor, flag-on).** The checkpoint block survives onto halftime and final cards. At `nba/jarvis/cards/run.py:195` the block is
dropped only for `pretip` and other days. This is deliberate (`test_the_halftime_and_final_cards_keep_the_last_checkpoint_block`),
but on the halftime card a Q2 checkpoint quoting "vs ask 38c" sits next to the halftime block's current prices. On the final card
the asks are from before the last 3 minutes and the recap already shows the settlement. *Fix:* at `halftime` and `final`, render
only the header line ("last in-game check: 45 game-min") without asks. Otherwise state in the header that the prices are stale.

**F4 (minor, flag-on).** "Card edits never ping" holds only while an edit succeeds. `nba/jarvis/cards/run.py:256` reposts on any
4xx other than 429 (`gone` = 404, `failed` = the rest). A repost is a new message and can notify. This fallback already exists on
main for halftime and final edits. With the flag on, live edits go from 0 to at most 9 per card per night (at most about 270 on a
15-game night), which multiplies exposure to it. *Fix:* pass `phase` into the fallback and, for a `live` checkpoint-only edit, return
`res.status` instead of reposting. The next halftime or final edit (which may repost) recovers the card.

**F5 (minor, tests).** The flag-on glue is not executed by any test. Lines 107-153 of `nba/jarvis/ingame_checkpoints.py` (artifact
digest pin, store path resolution, recency cache, `build_views` over the real store) have 40% file coverage, and
`test_score_checkpoints_is_a_no_op_when_off` covers only the early return. I exercised it once (`$S/probe_glue.py`, 11-08, scratch
store): `ok: True`, no exception, and refusals as expected on a cold start (`checkpoint_missed_late`, `refused_late_write`,
`tick_not_closed`). *Fix:* add one positive test (small artifact fixture, tmp store, `stored > 0` and `latest` populated) and one
negative test (digest mismatch returns `artifact digest mismatch` and nothing is stored).

**F6 (minor, merge mechanics; answers question 3b).** `docs/prereg/HALFTIME_OVERS_VS_PRICE.md` is add/add against
`agent/modeler-halftime-overs` (5a8d140, 2026-10-27, not on main). `diff` shows that this branch's file is the modeler's text plus
three lines in the status header, one edited line in section 10 ("reversed by amendment A, section 12") and the appended section
12. Nothing of the modeler's is deleted. **This branch's version is authoritative.** Sections 1 and 8 cite
`nba/eval/halftime_overs_gate.py` and `reports/halftime_overs/gate.json`, which exist only on the modeler's branch. Merging this
branch alone leaves those references dangling. *Fix:* merge `agent/modeler-halftime-overs` first, or together. On the add/add conflict take
this branch's file (`git checkout --theirs docs/prereg/HALFTIME_OVERS_VS_PRICE.md` when merging a56acc4 after the modeler's), then
`diff` it against the modeler's to confirm the only differences are the ones listed above.

**F7 (minor, rule text).** A rounding edge in 12.9: "at design effect 1.5 and floor +0.10 it just misses (107)". Unrounded it does
not miss: 4,121 / 38.8 = 106.2 <= 106.4 = 0.8 x 133. It misses only with ceil(nights) against floor(allowed). The drafted design
effect is 2.5, so the gate verdict (FAIL, 177 vs 106) is unchanged. *Fix:* write the comparison convention into the gate rule
("projected nights, unrounded, <= 0.8 x nights remaining, unrounded") and say "on the line (106.2 vs 106.4)" for the DE-1.5 case.

**F8 (minor, robustness, flag-on).** In `nba/markets/checkpoint_scorer.py`, the per-candidate exclusion tallies are noted
(`:402`, `store.note_skip(...)`) **before** the rows are appended (`:421`). `done()` is `has() or noted()`, and `noted()` is true for
any skip row of that (game, series, checkpoint). So if `store.append` raises, the decision is closed with no rows and is never
retried. The paper log would lose rows without a trace beyond one WARNING. *Fix:* append first and note the tallies after, or
make `noted()` consider only the decision-level reasons (`_REFUSALS` plus `no_scoreable_candidates`).

**F9 (info, flag-on).** The 180 s live window is tight against the real cadence. On 11-08 (tick gap p50 123 s, max 146 s) the
maximum lag was 158.6 s and every row fell in the window. On 11-09 the tick gaps had p90 140 s, 4.4% over 175 s and a maximum of 745 s
(from `data/ops/launchd_jarvis.out`), and the flag adds about 2 to 6 s per in-play tick. This is a refusal, not a leak: a late
decision is noted `refused_late_write` and never backfilled. Rows written over 180 s late are flagged `within_prereg_window`
false and excluded from H2. The cause is the Mac's health, which is not outcome-correlated. Count it in the 12.9 capture audit.

**F11 (minor, flag-on: a silent loss and a misattributed reason after a tick gap; 11-09 addendum).** Re-running `sim_live.py` on
11-09 at its 197 real tick times: all **7,923 live rows are identical to replay** (45 cols; 0 live-only rows, 0 decisions with a
different state or known_at), but live lost **20 of 280 decisions (7.1%; 668 of 8,591 rows, 7.8%)**. The JARVIS log shows tick
gaps of 306 s, **745 s** (03:45:16 -> 03:57:41 UTC) and 462 s. Of the 20: 12 `refused_late_write` (correct). 4 `state_stale`
(game 401909959, c = 15): the replay stored them at known_at 03:46:54, and live at 03:57:41 read a later window and refused them
under the wrong name. The honest reason is a missed tick. 4 have **no row and no skip note at all** (game 401909957, c = 45, quote
03:45:58): the game went final inside the 745 s gap, and both `CheckpointReader.fires` (`if self.as_of is not None and not
last.live: continue`, `checkpoint_scorer.py:243`) and the runner's game list (`runner.py` `_checkpoints`, FINAL filtered out) skip
it silently. That contradicts the draft's "refusals are named, never silent" (12.9) and undercounts the capture audit.
*Fix:* in live mode, for a game that is final at `now`, emit the not-yet-done checkpoints whose quote tick has closed as a final
`missed_tick_game_over` skip (one note, no scoring), and pass final games of the slate to the scorer for that purpose. Check the
live-lag refusal **before** the state-quality reasons in `_quote`, so a late decision is always named `refused_late_write`.
*Invariant worth a test:* live rows can never differ from replay rows only because `LIVE_WINDOW_S (300) > LIVE_MAX_LAG_S (180) +
QUOTE_MAX_AFTER_S (90) = 270`. A state the windowed reader picks wrongly is always older than now - 300 s, so its quote is refused
as late. Add `assert LIVE_WINDOW_S > LIVE_MAX_LAG_S + QUOTE_MAX_AFTER_S` (module level) and a test, so a later tuning cannot
break it silently.

**F10 (info, merge with the flag off).** The flag-off tick changes in two visible ways only. Each tick imports
`nba.jarvis.ingame_checkpoints` (4.5 ms), and the tick report gains the key `"ingame_checkpoints": {"skipped": "disabled"}`.
`tick_line` prints nothing for it. If the replay oracle digests JARVIS tick reports, that key changes bytes. The main session's
oracle run decides. I believe JARVIS is outside the replay closure but did not verify it.

## Rule draft (question 3)

* **Nothing is presented as frozen.** The header (line 3) says "NOT FROZEN; no hash taken". The amendment header (line 263) says
  "DRAFT, not frozen, no hash". 12.9 says "NOT frozen and cannot be frozen as written". The config comment says "Ships OFF". The
  only wording that could mislead is 12.10, "Fixed now and never re-tried after a result". In context that is the forking-paths
  list, which would bind at a freeze. Suggest "fixed in this draft; any change before a freeze is logged as a draft revision".
* **Gate-fail arithmetic confirmed** (table row 12). Robustness: 11-08 alone gives 52 entries in 7 games (7.4 per game, against
  the pool's 5.0). Even at that rate, 7.4 x 7.7 games per night is about 57 entries per night, or 120 nights, still over 106. The
  draft measured with a 2026-27-only recency window while live uses `null`. By 11-04 most rotation players have 5 or more 2026
  games, so that cannot plausibly lift entries by the 67% needed (38.8 to 64.8 per night). **The FAIL is robust.**
* The "133 nights, 1,026 games" remaining from 2026-11-13 to 2027-04-11 was **not verified**: `nba.duckdb.games` holds only
  played games.

## Playbook check

1. **Information or capacity?** Information in principle (the live box at a checkpoint), but it is the same information the
   market prices within seconds. The quote arrives 1.6 s after the state (check 5), so the market sees at least as much.
   Capacity alone (serving M at more minutes) adds no information the halftime model lacked.
2. **Enough data for this class?** Training: yes (T508, 2019-2024 play-by-play). Forward evaluation: **no**. The H2 gate fails at
   38.8 entries per night (177 nights needed vs 106 allowed). "Underpowered, cannot conclude" is the honest status of any read
   this season.
3. **Was the test valid?** No test runs yet. The paper log's known-at is valid: live equals replay, with no file after `now` and
   no backfill (checks 4 to 8). The baseline is invalid as built: glm_e is off support away from e = 24. The draft says so and makes
   R1 a precondition, but the card does not (F2).
4. **Lineage and inherited guards.** T508 (frozen c956fe4; attack WOUNDED: M minus glm_e residual below the floor, concentrated
   at e >= 30). The halftime scorer's guards (fee pin, digest pin, 180 s write window, refusal on stale feed, mode lock, refusal
   of foreign stores) are carried over. The extraction of `consider_market` and `lead_of` is verbatim (check 1 proves it on 1,522
   rows).
5. **What is it scored against?** The Kalshi ask plus the verified fee (R$ per $1, first-trigger), and later M against the
   market mid in log loss (C6). Not CRPS.

Class checks (trees): served at `e_used` with a `model_support` label (trained 6 to 42, extrapolated at 5 and 45; check 7). Trees
extrapolate flat, and the card says so when extrapolated. Class checks (logistic baseline): fitted at a single e, so it is an era
flag in disguise on the e axis. It must not be shown as a comparator (F2).

## Corrected family and power note

There is no claim, so no BH family to correct. For the draft: H1 and H2 are Bonferroni at 97.5%, and the S-family is BH over nine
checkpoint slices (declared). The power note stands as written: MDE at 80% power, 97.5% two-sided and DE 2.5 with about 106 nights
x 38.8 entries (about 4,100 entries) is about +0.13 R$. Anything smaller is "cannot conclude".

## The one biggest risk

If the flag is turned on as is, the team cards print a "baseline" probability and the phrase "where an edge is expected" beside
live asks. Readers, Devin included, who enters at halftime on his own reads, will treat two numbers with no track record as a
signal. The plumbing is right. The words are the risk.

## Not verified

* The replay oracle (main session) and whether JARVIS tick reports sit inside its closure.
* The live config's `recency_min_game_date: null` path (it reads season-2025 rows; I may not load 2025).
* Discord's notification behaviour on edits (taken from Discord's documented behaviour, not tested; no network).
* The 133-nights and 1,026-games schedule figure.
* Store size after a full season (extrapolated from one night).
* The exit code of the pytest subset run itself (the coverage gate makes a subset run report FAIL; the test counts were read from
  the output file).

## Recommended changes (not applied by me)

1. Before the go: fix F1 and F2 (wording only, `text.py:204/216/230`, `ingame_checkpoints.py:11`) and add the F5 tests.
2. Before the go, recommended: F11 (name every lost decision; window invariant as an assert and a test). Optional: F3 (stale
   prices at halftime and final), F4 (no repost on a failed live edit), F8 (note after append).
3. Merge order: `agent/modeler-halftime-overs` first, then this branch, taking this branch's prereg file (F6).
4. Draft text: F7 rounding convention; the 12.10 "Fixed now" wording.

Drafted DECISIONS row (not appended): `2026-11-10 | in-game checkpoints merged with the flag OFF (adversary SURVIVES; flag-on WOUNDED
on card wording F1/F2) | halftime rows identical old vs new on 1,522 rows over 2 nights; live rows identical to replay on 7,803 (11-08) and 7,923 (11-09) rows | reverse if the
replay oracle differs or a flag-off tick shows any checkpoint I/O.`
