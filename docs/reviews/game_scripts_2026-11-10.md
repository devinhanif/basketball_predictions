# Adversary review: game scripts pieces 1-3 (`agent/market-game-scripts` 529a39ef), 2026-11-10

Scope: the diff against main (12 files, +2362/-3): the guarded export step (`nba/export/combo_stage.py`, hooks in
`nba/export/build.py` and `nba/export/__main__.py`), `nba/parlay/game_scripts.py`, the JARVIS snapshot writer
(`nba/markets/checkpoint_snapshot.py`, hook in `nba/jarvis/ingame_checkpoints.py`, additive
`CheckpointReport.latest_states` in `nba/markets/checkpoint_scorer.py`), contracts `game_scripts_v1.md` and
`ingame_checkpoint_snapshot_v2.md`. Requested by the main session; DECISIONS 2026-11-10 12:52 (pieces 2 and 3 are Devin's
override of Claude's order).

**Verdict: WOUNDED.** Nothing leaks and nothing can fail the export or the JARVIS tick. Two small must-fixes: the contract
lets an app show a quote hours old or from before tip during the game, and the locked-store path leaves a 100 MB copy behind.
Mapped to the checklist's two values: Verdict: BLOCKED until M1 and M2 land. Both are small edits; after them, APPROVED
without another round unless the fix touches something else.

Top finding: the export never fails because of this step. I ran seven injected failures on DB copies and all seven
completed. The live P(script) equals the prior exactly at tip (max abs diff 0.0). The forward rule's number is what the
export computes. What remains is display hygiene (M1) and a disk leak in the read-only fallback (M2).

## Merge check

`git merge --no-commit --no-ff main` into the branch: clean (`Auto-merging nba/export/build.py`, no conflict with the bulk
insert 1f3e24ae). Everything below ran on that merged tree, and the test merge was aborted afterwards. Main was not touched.

## Verification (merged tree, one pytest process at a time, unpiped exit codes)

| command | result |
|---|---|
| `.venv-1/bin/python -m pytest tests/export --no-cov -q` | 143 passed, exit 0 |
| `.venv-1/bin/python -m pytest tests/parlay --no-cov -q` | 258 passed, 2 skipped, exit 0 |
| `.venv-1/bin/python -m pytest tests/markets --no-cov -q` | 294 passed, exit 0 |
| `.venv-1/bin/python -m pytest tests/jarvis --no-cov -q` | 477 passed, 1 skipped, exit 0 |
| `ruff check` (10 touched files) | exit 0 |
| `ruff format --check` (10 touched files) | exit 0 |
| `mypy` (7 touched source files) | exit 0 |

## Attacks (scripts in the session scratchpad; DB copies only; `read_only` everywhere; no season 2025)

Copies: `nba.duckdb` (1.74 GB), `market_asof.duckdb` (4.1 GB) and `kalshi.duckdb` (100 MB), copied at 16:35 CST into the
scratchpad. A full export ran from the merged tree into a scratch out dir (`run_export.sh`) with rc 0 in 46 s wall. The
scripts step took 2.01 s for 6 games and 172 players, with 955 asks and 135 priced picks, all `no_positive_ev_found`.

### A. Export failure containment (`contain.py <scenario>`: `build_export` on the copy, sits off, no odds db)

| scenario | export completed | manifest `game_scripts` | files | extra time |
|---|---|---|---|---|
| import error (`sys.modules['nba.export.combo_stage']=None`) | yes | absent, error:ModuleNotFoundError | no combo/picks | 0 s |
| game model missing | yes | absent, error:FileNotFoundError | no combo/picks | 0.4 s |
| crash inside `best_picks` | yes | absent, error:RuntimeError | no combo/picks | 0.6 s |
| Kalshi store write-locked (connect raises IOException) | yes | ok, markets ok (copy fallback), 144 priced | both | **+13.5 s and a 99,889,152-byte copy left in `$TMPDIR/market_ro_*`** |
| Kalshi store is garbage | yes | ok, markets error:IOException, 0 priced | both | +12.6 s, 1.7 KB copy left |
| disk error on the second file | yes | absent, error:OSError | **combo present, picks absent** | 1.6 s |
| history query fails | yes | ok; `mu_t_source` "league fallback" (mu_t 227.45 on every game) | both | 0 s |

PASS on "the export never fails". The step runs after `src.close()`, so it never holds `nba.duckdb`. Its worst case
(+13.5 s) sits far inside app_export's 600 s run deadline and 900 s attempt timeout. Findings M2, m1 and m2 come from this
table.

### B. Timing on a 15-game night (`time15.py`)

`best_picks` per game, with every player given a main line on every stat and asks at k = 1..29: 0.25 to 0.35 s. For 15
games that is about 4.2 to 5.3 s, plus about 1.5 s of grid calibration. Even with the lock retry, the step stays under
about 20 s. PASS. Size: a 15-game combo file with all lines is 365,309 bytes raw, over the contract's 300 KB budget (m3).

### C. Known-at of quotes and forecasts (`inspect2.py` on the scratch output)

- Every game's `made_at` < `tip_utc` (asserted, 6 of 6). The reader exports only `made_at < tipoff`. Prop rows were stamped
  12:00Z to 20:20Z against tips of 00:30Z to 04:00Z.
- 135 priced legs, 58 unique tickers: 0 quotes at or after tip, maximum quote age 15.6 min at build. Manifest `left_out`:
  `quote_stale` 16. The filter is `ts < tip` and `now - ts <= 60 min`, both in naive UTC. Kalshi `ts` is naive UTC, as is
  the stored tipoff and `made_at`. PASS at build time.
- **Display-time staleness is not covered (M1).** app_export rebuilds only after a pretip run ends and never after the
  slate's last tip (`nba/ops/app_export.py` docstring, "Re-export"). So `game_scripts_<night>.json` keeps its pre-tip asks in
  `market` while games are in play, until the next day's first export. `game_scripts_v1.md` section 3 says only "at most 60
  minutes before the build".
- The tip used by `ask_index` is the stamped `tipoff`. On a game stamped with the 19:00 ET proxy, an early tip (weekend
  afternoon) would admit post-tip quotes. Tonight all 6 tips are real. This is unverified for proxy-tipped games (m5).

### D. Forward-rule compatibility (GAME_SCRIPTS_FORWARD rev 2, a7760b7d, rule line 2)

- `mu_t`: the export calls `combo_stage.history_rows(src, day, 2026)`, and the harness `as_of_mu_t` runs the identical SQL
  text. `slate_total_feature` filters `game_date < slate` itself, so the export's pre-filtered history and the harness's
  wider one give the same feature. `guard.LIVE_SEASON` = 2026, and the SQL carries `season = ?`, so 2025 is never read.
  PASS.
- Rounding: `combo_build.build_game_payload` rounds `mu_m`, `sd_m`, `mu_t` and `sd_t` to 2 dp. `GS.script_priors(payload)`
  calls `combo.script_prior` unchanged on the rounded block (`combo.py` is not in the diff). The exported `scripts.p` is that
  number rounded to 4 dp. Tonight, MIL@NYK: prior blowout 0.500264, export 0.5003. PASS.
- Win row: the app prefers `rung0_injury_elo`, else MOV-Elo. The rule scores injury-Elo only, as the newest row with
  `made_at < tipoff`, the same selection the app makes. A MOV fallback game is `unforecast` in the rule, not a mismatch.
  PASS.
- Residual divergence (m2): if the history query fails transiently, or yesterday's scores are not yet ingested when the last
  pre-tip export runs, the app shows a different `mu_t` than the rule later recomputes. The rule scores its own recompute
  ("dropped in revision 1: the comparison with a nightly stamped combo block"), so the test stays valid. Only the app's
  shown number can drift. The manifest's `game_scripts.status` stays "ok" in that case.

### E. No 2026 realised-script aggregate

`git diff main...agent/market-game-scripts | grep -i "realis|realiz|outcome|home_pts|final_margin|hit_rate|brier|calib"`:
the only reads of 2026 final scores are `history_rows` (the as-of total feature, which the rule permits). No code compares a
realised script with P(script). The live block is overwritten every tick, never logged, and absent once a game is not in
play. My own scripts printed no 2026 outcome. PASS.

### F. JARVIS snapshot writer: atomicity and tick safety

- Write: temp file `.<name>.tmp.<pid>` in the same directory, then `os.replace`, then unlink in `finally`. A document over
  5 MB is refused rather than truncated. PASS.
- Exceptions: `CS.publish` catches every Exception. `publish_snapshot` wraps the import and path resolution in a second
  guard. Both are called after `store.close()`, so the DuckDB store lock is not held during the write. PASS.
- Cost (`tick_cost.py`, real capture `data/raw/live_2026-11-09`): the extra `latest_states` loop takes 0.02 to 0.03 s with a
  cold cache. With 8 events and 4 live, the reader opens in 0.07 to 0.09 s, which already happens on the tick.
  `load_pregame` re-parses the combo file only when its mtime changes. PASS.
- Matching: the combo file uses nba_api tri-codes, and the checkpoint key uses ESPN codes mapped by `ESPN_TO_KALSHI`
  (GS→GSW, NY→NYK, SA→SAS, NO→NOP, UTAH→UTA, WSH→WAS). These agree for all 30 teams by inspection. On tonight's file the 6
  keys are MIL@NYK, BOS@PHI, LAC@PHX, MIA@UTA, ORL@SAC and OKC@POR.
- No state-age limit on the script block (m4). The checkpoint rows refuse a state more than 150 s old, but the script block
  uses `reader.latest(eid)` at any age. Observed ages were 23 to 39 s. The block does carry `state_at`.
- **Outward consequence of merging (needs_devin):** main has no writer of `data/jarvis/ingame_checkpoints_latest.json`
  today, and the app's checkpoint view is `available: false` until that file appears (DECISIONS 2026-11-10 10:45). With
  `ingame_checkpoints.enabled: true` in configs/jarvis.yaml, the first in-play tick after merge turns the app's v1 checkpoint
  view on. That falls within Devin's piece 3, but he should know merge equals go-live of that view.

### G. Live P(script) maths (`live_math.py`, tonight's MIL@NYK block, synthetic states)

- At tip (e = 0, 0-0) it equals `combo.script_prior` for all four scripts, max abs diff 0.0.
- Blowout and close use the same integer-corrected thresholds as the prior. Shootout and grind use the game's own fixed
  `min_total` and `max_total` from the pre-game `mu_t`.
- Collapse: at 47.5 min, margin 20, total 234, blowout = 1.000 and shootout = 0.928. At 47.99 min, margin 0, close = 1.000.
  The 0.005 floor on `rem` (about 14 s) keeps some uncertainty in the last seconds. That is acceptable.
- Overtime: `period > 4` or elapsed ≥ 48:00 gives no block. That is honest, but the rule scores the final score including
  OT. At a tied late regulation the block says close = 1.000 although OT can end outside ±5, and shootout is understated
  when OT is likely. This is disclosed as "overtime out of scope", so it is a label item only (m4).
- The block holds no ask, bid, price, edge, EV or stake key. PASS.

### H. Could picks read as edges?

- EV goes through `nba.parlay.ev.evaluate` with the configured fee object on `p_play * p_over` (unconditional). I
  re-derived `ev_raw = p_model_incl_dnp - ask - fee` for all 135 priced legs: 0 mismatches beyond 2e-4. There is no second
  EV formula. `p_low` is back-solved from `raw_ev_low`, which is algebra on evaluate's output, not a new formula.
- No stake, size, kelly, contract-count or edge key in either file (grep on the outputs: 0). The combo file has no `ask`.
  `raw_at_main` appears 41 times; the contract allows it for Analyst mode.
- Risk (m6): the `market` block sits inside the same leg object as `p_leg_script`. On 3 of 135 priced legs,
  `p_leg_script - ask - fee > 0`, and 6 have `ev_raw > 0`, while every verdict is `no_positive_ev_found` (`model_weight` 0).
  The contract forbids putting a script number against a price, but it does not say which fields an app row may show beside
  `ask`.
- `MARKET_NOTE` (combo_stage.py line 56-60) says "Without a settled track record the engine shrinks to the market". Tonight
  `n_settled_track` = 31,481 and the weight is 0 because the skill is not positive. The contract states this correctly; the
  file's own note does not (m7).

### I. Replay closure claim (`closure.py`)

The closure is every import, including function-level imports, from `nba/daily/*`, `nba/truth/*` and every `python -m`
entry point in `ops/*.sh` (19 entry points, 204 modules). Of the 7 touched modules, none is in the closure, and no
`nba.export`, `nba.jarvis`, `nba.markets.checkpoint*` or `nba.parlay.game_scripts` module appears. PASS: the claim holds.
The JARVIS tick is a live job outside the oracle; section F covers it.

## Findings (severity-ranked; exact line, exact fix; none applied by me)

**M1 (major, contract).** `docs/contracts/game_scripts_v1.md` section 3, the sentence "`<leg>.market` is `null` unless ...
at most 60 minutes before the build". The app agent builds from this file alone, but the export is not rebuilt after the
last tip or between pretip runs. An app following the contract would show a pre-tip ask beside a live game, or a quote hours
old. Fix: add one sentence: "The app shows a `market` block only while `now < tip_utc` and `now - price_ts <= 60 min`;
otherwise it shows 'no current quote'." Optionally set `market_valid_until = min(tip, price_ts + 60 min)` per block in
`make_market_fn`.

**M2 (major, ops).** `nba/export/combo_stage.py` line 241 `open_read_only(path, copy_fallback=True)`: when the Kalshi store
is write-locked, `nba/markets/asof.py` lines 88-90 copy it to `mkdtemp(prefix="market_ro_")`, and nobody deletes the copy.
Measured: 99.9 MB left per occurrence, plus 13.5 s. The existing callers already leak (9 `market_ro_*` dirs since 10-29, one
still holding 93 MB from 11-08). This branch adds 24 more opens a day, against a snapshot job whose 15-min start time
drifts across :05/:35. Smallest fix, inside this branch: in `read_markets`, before `con.close()`, read
`con.execute("PRAGMA database_list").fetchall()`. If the file is not `path`, `shutil.rmtree` its parent after close. Better,
in a separate markets-owned change: make `open_read_only` return a context manager that removes its copy.

**m1 (minor).** `nba/export/build.py` `_scripts_stage`, the loop writing two files: an error on the second file leaves a
new `combo_<night>.json` without `game_scripts_<night>.json` while the manifest says `absent`. Fix: in the `except`, unlink
the files this call wrote (`files` keys), or write both temps and then `os.replace` both.

**m2 (minor).** `nba/export/build.py` `_history_stage` returns None silently into a status "ok" block. Fix: put
`history: ok|error:<type>` and `mu_t_source` counts into the manifest's `game_scripts` block, so a league-fallback night is
visible without opening the combo file.

**m3 (minor).** Size: a 15-game night is about 365 KB raw against the 300 KB budget. Nothing enforces it. Fix before the app
consumes it: apply the contract's own trim (drop `raw_at_main`, trim `surv` at 0.001) when the dump exceeds 300 KB, and
record that in the manifest.

**m4 (minor).** `nba/markets/checkpoint_snapshot.py` `script_block`: no state-age limit, and the late-regulation tie shows
close = 1.000. Fix: return None when `now - state.received_at > 180 s` (the checkpoints' STATE_MAX_AGE_S spirit). Add to
`SCRIPT_LABEL`: "overtime not modelled; a tied game late in regulation can still end outside ±5".

**m5 (minor, unverified).** `combo_stage.ask_index` trusts the stamped `tipoff`. Fix: if the reader exposes the tip source,
refuse quotes for proxy-tipped games. Otherwise note it in the contract.

**m6 (minor, contract).** Section 3: add "in a pick row, `ask` and `ev` are shown only beside `p_leg` / `p_model_incl_dnp`,
never in the `p_leg_script` column; `ev_raw` and `ev_raw_low` are Analyst-mode only".

**m7 (minor).** `combo_stage.py` line 56-60 `MARKET_NOTE`: replace "Without a settled track record" with "Unless the paper
track record shows positive skill".

## Playbook check

- **Model class:** set / joint model (the Gaussian copula combo of `docs/MODEL_PLAYBOOK.md`, "Playbook check: combo mode"),
  plus a ratings-derived game model (MOV/injury-Elo margin, as-of total) and an exploratory in-game Brownian update. This is
  a delivery and wiring review, not a model test.
- **Information or capacity?** No new pre-tip information. The live block adds in-game information (score and clock) but
  no new model capacity. Nothing is fitted.
- **Enough data for this class?** Not applicable: nothing is fitted. The scripts carry combo_eval's verdict (none
  calibrated; grind fragile), and the live update is unvalidated. Both are labelled `exploratory` in every block.
- **Test validity:** no claim is made here. The forward test is GAME_SCRIPTS_FORWARD (not yet frozen), and section D shows
  it will score exactly the export's number (same SQL, same rounding, same `script_prior`).
- **Lineage and inherited guards:** combo_export_v1 (banded calibration, the 0.75 tested-range drop, `cal_k`), teammate_sits
  display rule (pts numbers and reb direction only; checked in `sits_block`), LINE_CAL_LIVE (no outcome in either file),
  the cards' fee path (single `evaluate`), the checkpoint scorer's overtime exclusion. All carried forward.
- **Scored against:** nothing yet. The market block is a price comparison of the unconditional probability, shrunk by the
  paper track record (weight 0 tonight).

## Corrected family and power

No inferential claim in this branch, so no family and no CI. The builder's "140/140 no_positive_ev_found" reproduces as
135/135 with lines and 144/144 without (the leg set depends on main lines). That is a count, not a claim. The live P(script)
has no validation n, so "unvalidated" is the correct label and the review makes no power statement.

## Single biggest risk

That a future app panel, built from `game_scripts_v1.md` alone, shows a stale or pre-tip ask next to a script-ranked leg
during the game. Each piece is honest, but the combination reads as "the model sees value if this becomes a blowout".
M1 and m6 close it in the contract before the app piece is built.

## Not verified

- The app side (no consumer of either file in this diff). The finished-game LINE_CAL_LIVE gate for these files is a future
  app change.
- The Kalshi snapshot job's lock-hold duration, and so the real collision rate for M2. I measured the cost per collision,
  not the frequency.
- Whether the export's brief shared lock on `kalshi.duckdb` (0.11 s query) can make the snapshot writer's non-retrying open
  fail. snapshot.err already shows 10 historical "Could not set lock" failures from other readers. The added exposure is
  about 0.2 s per export run.
- ESPN abbreviations for all 30 teams against live data. I checked the mapping table, plus 12 teams on tonight's slate.
- Behaviour on a proxy-tipped game (m5).

## Drafted rows (not appended)

DECISIONS (for the main session, at merge): `| 2026-11-10 HH:MM | MERGED agent/market-game-scripts pieces 1-3 after
adversary WOUNDED -> M1 (contract: market block only while now < tip and quote <= 60 min old) and M2 (remove the Kalshi
read-only copy) fixed. Export step guarded (7 injected failures, export completed in all); +2 s tonight, <= ~20 s worst case on 15
games; outside the replay closure (static import closure of nba/daily, nba/truth, ops entry points: 0 of 7 touched modules).
Merging turns on the JARVIS snapshot writer and with it the app's v1 checkpoint view. | Devin's go (12:52, pieces 2-3 his
override). | A failed export traced to the step; a stale quote shown in play; a 2026 realised-script aggregate anywhere. |`

No ledger row (no test was run).
