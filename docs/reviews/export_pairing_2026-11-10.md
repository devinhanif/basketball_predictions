# Adversary review: export line pairing through the newest covering run (branch agent/steward-export-pairing, 3db5cf7)

Reviewed 2026-11-09 (local), file dated per request. Scope: `nba/export/reader.py`, `build.py`, `best_model.py`,
`tests/export/test_line_pairing.py`, `docs/DESIGN_EXPORT_LINE_PAIRING.md`. Known-at change on the live path (the
app export). Data: scratch copies of `nba.duckdb` and `data/markets/market_asof.duckdb` (copied 2026-11-09 20:59
local), every connection `read_only=True`, season 2026 rows only (`Window(..., seasons=(2026,))`), no box-score
values read, no replay run, no season 2025 touched. Attack scripts live in the scratchpad
(`.../scratchpad/attack/a1.py` to `a5.py`, `test_planted.py`), never under `nba/`.

**Classification: BROKEN** (the claimed effect is an artifact: the shipped build does not do it, and the version
that would do it breaks two other export fields). The F2 half (close the market store before pairing) is sound.

Verdict: BLOCKED

## Top finding

The claimed 11-09 replay (28 -> 364 and 165 -> 401 paired keys) is not what the shipped build produces. In
`pair_quotes` every row of one `run_id` shares one cutoff and "on a clash keep the earlier one"
(`reader.py`, `if k in preds: through = min(through, preds[k][2])`). The AVG shadow rows (`props_avg_live2`) are
in the same props list, carry the **same run_id as the production run** (107 of 114 season-2026 production runs),
and are given `lines_through = made_at` (`best_model.py` `avg_rows`). So the AVG row pulls the production key back
to `made_at` and the extension vanishes. On the scratch copy, latest production keys paired, old rule vs shipped:
11-04 201 -> 209, 11-05 25 -> 25, 11-06 123 -> 127, 11-07 154 -> 154, 11-08 59 -> 59, 11-09 219 -> 219; quote
rows received after `made_at` on 11-09: 0. Dropping the AVG rows from the pairing input reproduces the claimed
size (11-09: 219 -> 436 keys, 53,978 rows after `made_at`), so the replay was most likely measured without AVG
rows (the branch tests also have no AVG row). And once the collapse is fixed, finding B2 below wipes 69-92% of
the export's `line_cal` values.

## Per-question results

| # | Check | Command / evidence | n | Result |
|---|---|---|---|---|
| 1 | Store guarantee (dedup) | read `nba/daily/store.py` 192-262, `pipeline.py` 239-260, 785-830, `late_report.py` 425-450, `stamps.py`; stamps per model query | 497 stamped runs each model since 10-20 | **FAIL as stated**: group dedup is sound for keys *in the batch*; "stamped run covering the game" does not imply "key re-evaluated" (four paths, M1/M2) |
| 2 | Residual: OUT after the row | `a4.py 2026-11-09 5` (injury report `out_players` at `lines_through` vs at `made_at`, label-free) | 11-04..11-09: 76/1872, 20/256, 44/1156, 112/944, 32/924, 84/1016 latest prod rows; 19, 5, 11, 28, 8, 21 players | **FAIL** (latent; masked by B1): with extension working, 11-09 pairs 3,414 fresh quotes (2,674 usable overs) to rows of 21 players listed OUT before the newest run |
| 3 | Planted future / boundaries | `test_planted.py` (6 tests, run as plain script, exit 0) | 6 | **PASS**: quote at exactly `lines_through` in, +1 us out; quote at tip out even with `lines_through` past tip; run at tip, after tip, other ET date excluded; DST 2026-11-01 boundary correct. Multiple models in one run: see B1 |
| 4 | Frozen consumers of `prop_lines` | `git grep -ln "prop_lines\|nba\.export\.reader\|from nba.export" 3db5cf7 -- research nba scripts ops`; imports of `research/eval/best_price_forward_{eval,gate}.py`, `line_cal_live_{eval,gate}.py`; `nba/jarvis` | 0 frozen readers | **PASS**: BEST_PRICE_FORWARD (5ac0fa2f), LINE_CAL_LIVE, JARVIS read the DBs with their own cutoffs; `nba/parlay/top_source.py` has its own SQL cut at `prod.made_at`. The export's own `line_cal`/`rung_cal` fields are inputs that change (B2) |
| 5a | Old-rule equivalence | `a1.py 2026-11-09 0`: new `pair_quotes` with `lines_through = made_at` vs old `prop_lines` | 145,444 rows | **PASS**: identical row sets |
| 5b | F2 hold | timing on lookback 7 (production default), 2 passes | 53,785 props, 4,512,130 quotes | **PASS**: hold = fetch only, 7.9 s / 17.6 s; pairing 7.4 s / 14.2 s now outside the hold. Memory unchanged (old code also materialized the same rows) |
| 6 | App tolerance / schema | `export_store.py` `_table` = `SELECT *` into dicts; `build.py` `SCHEMA_VERSION = 2  # additive changes keep 2` | n/a | **PASS** (by reading; app tests not run, see Not verified) |

## Findings (severity, line, fix)

**B1 (blocker): AVG rows of the same run collapse the production extension; the claimed result is not produced.**
`nba/export/reader.py`, `pair_quotes`: `if k in preds: through = min(through, preds[k][2])`, with AVG rows from
`best_model.avg_rows` (`"lines_through": made_at`) sharing the production run_id. Evidence above (`a3.py`).
Fix: compute the per-key cutoff from production-table rows only (skip rows whose `source_table` is
`forward_avg_shadow` when building `preds`; an AVG primary already takes its quotes through `lines_run_id` =
production run). Add a test with an AVG row in the same run (scratch `test_avg_row_in_same_run_collapses_production_extension`).
Re-run the replay on the shipped build with AVG rows present and report those numbers.

**B2 (blocker, becomes live the moment B1 is fixed): `rung_cal` and `line_cal` lose their pre-`made_at` quote.**
`pair_quotes` keeps only the newest quote per (book, market, line, side) up to `lines_through`; `build.py` then
passes those rows to `rung_cal_fields.attach` and `line_cal_fields.attach`, which cut at `made_at`
(`rung_cal_fields.main_line`: `ts < made_at`). The quote before `made_at` was overwritten by a later one, so the
main line is gone. With the collapse removed (`a4.py`): `line_cal` values lost or changed 11-04 1379/1678,
11-05 230/250, 11-06 765/990, 11-07 1151/1580, 11-08 422/496, 11-09 1842/2213 (1518 to `no_book_line`, 324 to a
different value); `rung_cal` changed on 651, 95, 392, 451, 198, 757 rows. The design note's sentence
"`rung_cal_fields` and `line_cal_fields` still cut quotes at the row's own `made_at`" is true of the filter and
false of the result. Fix: in `build.py` after `quotes = reader.fetch_quotes(...)`, pair twice:
`cal_lines = reader.pair_quotes([dict(p, lines_through=p["made_at"]) for p in props], quotes)` and pass
`cal_lines.rows` to both `attach` calls; export `lines.rows`. Test: a build-level test that `line_cal` and
`rung_cal` are identical between the old and new rule (scratch `test_newest_quote_dedup_hides_pre_made_at_quote_from_line_cal`).

**M1 (major): a key the newest run did not emit pairs fresh prices to a stale row (disclosed residual, measured).**
The store cannot tell "unchanged" from "not emitted" (dedup leaves no row in both cases; `forward_prop_grid199` is
also write-on-change, so there is no per-run emitted set to count k vs k+1). Label-free proxy: player OUT on the
latest official report at `lines_through` but not at `made_at` (the pipeline drops these players via
`out_players`). 2-12% of latest production rows per night; on 11-09, 84 rows / 21 players, and with the extension
working 2,674 usable over quotes newer than the row. The app already shows the stale row; the change adds a fresh
price beside it, which reads as a current edge on a player who is out. Other non-emission paths with no proxy:
the `min_proj_minutes` / `min_p_play` / roster-size thresholds, roster changes. Fix (cheap, read-only, pre-tip):
in `forward_predictions`, keep `lines_through = made_at` when the player is in `out_players(con, cov)` (same
function and as-of rule as the pipeline). Proper fix (live path, replay oracle): a per-run `(run_id, game_id,
model, target, player_id)` coverage record in `nba.daily`, and extend only keys the covering run emitted.

**M2 (major, latent today): `newest_cover` is model-blind.** `reader.pretip_run_times` takes `DISTINCT made_at`
over all of `forward_model_stamps`. (a) If `props_context_residual` fails and the run falls back to recency
(`pipeline.py` ~808-822), the run still stamps the other models and the stale context rows are extended through
it; today 0 cases (each model has 497 stamps since 10-20). (b) `late_*` shadow runs stamp `props_context_residual_late`
for the games within tip - 15 only, yet count as covering every game of the date; today they never win (each
late stamp precedes the :20/:50 main run by 1-4 minutes), but a held or failed main run makes them the cover.
(c) Rows of models no main run re-evaluates are extended: on 11-09, 804 `props_context_residual_t15` (no stamps,
no dedup, written once) and 804 `_late` rows (idempotent per game) got `lines_through` from a main run. Fix:
cover per `(model_name, version)` from `forward_model_stamps`, excluding `run_id LIKE 'late_%'` for non-late
models; a model with no stamps (t15, shadow_llm) keeps `made_at`. Add a test for each of (a)-(c).

**m1 (minor):** design note says "AVG-shadow rows unchanged", but an AVG primary shows the production run's
quotes via `lines_run_id`, so after B1 is fixed the AVG card shows extended prices. State it in the note.

**m2 (minor):** `top_source.py` (parlay legs) keeps the old cut (`q[7] <= prod["made_at"]`); after B1 the card
price and the parlay leg price come from different times. Document, or align deliberately.

**m3 (minor, app tests on main):** `app/api/tests/test_best_model.py:245` asserts "the AVG run has no prop_lines
of its own"; in the data 107/114 production run_ids are shared with AVG rows. The fixture misses the case that
broke this branch.

**m4 (minor):** the replay claim carried no build configuration; the numbers must come from the shipped
`build_export` path (AVG rows included), not from a pairing call on production rows.

## The single most likely failure mode if the claim is wrong

It is wrong as shipped: the extension is a near no-op because AVG rows share the production run_id (B1). The most
likely failure after a naive fix is B2 (silent loss of `line_cal` on most rows), then M1 (fresh prices next to
forecasts for players ruled out).

## Corrected family and power note

Not an inference claim; no CI applies. Counts above are full enumerations of season-2026 forward rows on
2026-11-04..11-09 (a scratch copy), not samples. The 28 -> 364 / 165 -> 401 figures are unreproduced and should
not enter the ledger.

## Playbook check

Model class: none (export known-at data plumbing; not a model). Cross-cutting: (1) information or capacity:
neither; it changes which already-stored quotes sit beside a forecast, so the only question is known-at
correctness; (2) data enough: n/a; (3) test valid: no, the branch tests omit AVG rows and the cal consumers, so
they could not see B1 or B2; planted-future boundaries are correct; (4) lineage: the write-on-change dedup
(`append_predictions_ex`) and the prior export reviews (`app_reexport_2026-11-09.md` F2/F3,
`export_line_cal_review_2026-10-21.md`); the inherited guard "cal fields cut at the producer's `made_at`" is
kept in the filter but defeated by the newest-quote reduction; (5) scored against: nothing frozen (check 4); the
export's display fields `line_cal`/`rung_cal` are the affected outputs. Class checks: n/a.

## Not verified

- App and export test suites (no pytest in `.venv`; installing was refused). The six scratch tests ran as a plain
  script, exit 0. ruff/format/mypy not run (no code of mine under the tree).
- The claimed replay itself (main session owns the replay oracle); the mid-day build states (first build) were
  not re-simulated; my numbers are end-of-day.
- Whether the app hides OUT players from the injury table before showing prices (M1 severity for display).
- `nba/shadow_llm/runner.py` rows (append without dedup) are exported or not.

## Recommended changes (not applied)

1. B1 fix + AVG-in-same-run test; 2. B2 fix (pair twice) + build-level cal-equality test; 3. M2 per-model cover
+ three tests; 4. M1 OUT guard now, coverage record later (live path, Devin's go); 5. re-run the replay on the
shipped build and replace the claimed numbers; 6. design note edits (m1, B2 sentence, residual counts).

## Drafted rows (not appended)

DECISIONS (draft): "2026-11-10 export line pairing (3db5cf7) blocked: AVG rows in the same run cancel the
extension; the working version would drop line_cal on most rows; fix B1+B2+M2 and an OUT guard before merge.
Reverse if a replay of the shipped build shows line_cal/rung_cal unchanged and the extension present."

## Re-review (diff-only, 3db5cf7..5fc1a36, 2026-11-10)

Scope: `git diff 3db5cf7 5fc1a36` (reader.py, build.py, design note, tests). Same read-only scratch copies,
season 2026 only, reader-level runs one day at a time (machine load ~7), no full build, no replay. Code
snapshot: `git archive 5fc1a36 nba tests` into the scratchpad (`new2/`). Scripts: `attack/a6.py`, `a7.py`,
`test_planted2.py`.

| Finding | Replant | n | Result |
|---|---|---|---|
| B1 AVG collapse | `test_planted2.py`: AVG row same run, AVG first or last in the input -> production row still pairs the later quote; a superseded production arm in the same run still lowers the cutoff | 3 tests | **FIXED** |
| B1 on data | `a6.py 2026-11-04`: latest production keys paired, old rule 201 -> fixed 743; 91,156 quote rows newer than `made_at` | 1,872 latest prod rows | **FIXED** (the extension now happens) |
| B2 cal fields | `build._read_lines` returns `(lines, cal_lines)`; `cal_lines` = `pair_quotes` with `lines_through = made_at`, the input proven identical to the old rule (145,444/145,444 rows, first review); both `attach` calls take `cal_lines.rows` only. Agent's 11-04 export: line_cal/rung_cal identical to main 52,899/52,899 | 52,899 rows | **FIXED** (by construction plus the agent's build comparison) |
| M1 OUT | `a6.py`: extended latest production rows whose player is OUT at `lines_through` | 0 of 1,796 | **FIXED** for OUT |
| M1 other drops | `a7.py`: extended latest prod player-games whose official-report status changed between `made_at` and the cutoff (a dropped player who was not OUT would show here) | 11-04: 21/449; 11-09: 11/233 | **Residual, small and benign in kind**: every transition is toward playing (questionable/probable -> available 29, not listed -> available/questionable 2, available -> not listed 1); none to doubtful or OUT. Threshold and roster drops stay invisible (disclosed in the design note) |
| M2 other-model stamp | `a6.py`: extended rows by model = context 1,796, recency 1,796, int 1,194, lt 398; `_t15` 0, `_late` 0 | 5,184 | **FIXED** |
| M2 late_ stamp | `test_late_like_escape_in_duckdb` (`late_x` excluded, `lateXx` kept: the `\_` escape works); `a6.py` rows whose cutoff equals a late stamp time: 0 | 1 test, 5,184 rows | **FIXED** |
| Planted future | quote at exactly the cutoff in, +1 us out; at tip out; run at/after tip, other ET date, DST 11-01 boundary | 9 scratch tests, plain script, exit 0 | **PASS** |
| No future quote | `a6.py`: quotes with `snapshot_ts > paired_through` or at/after tip | 0 of 190,424 | **PASS** |
| Frozen consumers | `git grep -ln "prop_lines\|nba\.export\.reader\|from nba.export\|paired_through" 5fc1a36 -- research nba scripts ops`: only `nba/export/*` and the `top_source.py` docstring | 0 frozen readers | **PASS** (unchanged) |

### Is the gain worth the known-at surface?

The agent framed the gain as "keys with prop_lines 761 -> 769", which undersells it: that counts keys with a
quote on *any* row (the t15 and late rows, made minutes before tip, already pair almost every key). The number
that matters is the card the app shows. In the agent's 11-04 export (`pair/out_new_04/export.duckdb`, joined on
`lines_run_id`), 743 of 1,872 primary rows have a price, against 160 under the old cut, and 742 of those 743
show a quote newer than the forecast. On the card, that is a 4.6x gain in priced primary cards, not a 1% gain.

What it risks is display only: no frozen rule, scorer or JARVIS path reads export `prop_lines` (check 4), the
calibration fields are byte-identical, and every quote stays before both the covering run and the tip. The
remaining surface is a key a covering run dropped for a reason other than OUT. The status-change detector finds
none in the dangerous direction on two nights. My opinion: **merge**. The gain is real and visible, the
surface is bounded and disclosed, and the per-run coverage record (live path, needs Devin) can wait for a
night that shows a problem.

### New minor findings (none blocking)

- **r1:** `build._read_lines` now pairs twice. At lookback 7 one pairing took 7.4-14.2 s, so this adds about
  that much again, outside the store hold, under the 600 s export deadline. Watch it on a loaded machine.
- **r2:** the agent's summary metric (761 -> 769) should be replaced in the ledger and DECISIONS by the
  primary-card metric (160 -> 743 on 11-04, n = 1,872 primaries) so the record states the gain correctly.
- **r3:** the "what the game knew" timeline (`app/api/.../knows.py`) places a forecast at `made_at`. A shown
  price can now be newer than that forecast. Nothing there claims the price was seen by the forecast, but the
  app copy should not imply it.

### Not verified (re-review)

- The branch's own tests (`tests/export/test_line_pairing.py`, 8 tests) and the app suites: pytest is not in
  `.venv`. The agent's 52,899/52,899 cal identity was taken from its build, not re-run by me (a full build was
  out of budget). My identity argument is by construction.
- The replay oracle (main session) and 11-05..11-09 primary-card counts.

Re-review verdict: B1, B2, M1 (OUT) and M2 are fixed. The residual is disclosed and small. Recommend merge
after the replay oracle is IDENTICAL and Devin gives the live-path go.

Verdict: APPROVED
