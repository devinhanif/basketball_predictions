# Lane C2b: the honesty pages (2026-11-16 evening, Central)

Branch `agent/app-1117-pages`, worktree `nba-wt-1117-pages`, base `18ad2e50`. Started 21:09 CST, report written 21:52 CST (times from
`date`). Edits under `app/web/**`, `app/api/src/parlayhitters_api/bake_artifact.py`, its test
`app/api/tests/test_edges_and_bake.py`, and this file. `app/error_codes.json` untouched (no new code was needed). Nothing
pushed, nothing merged.

## How to check it yourself

```
cd app/web && npm ci && cd ../api && uv sync
# artifact data: bake from an export (or copy a baked.json) into app/web/artifact-data/baked.json
cd app/web && npm run check && npm run build:artifact                       # ends "checks passed"
cd app/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
app/scripts/check.sh --browser && app/scripts/walk.sh --widths               # from the worktree root
# browser: API on an export, vite in front of it
cd app/api && APP_EXPORT_DIR=<export dir> uv run uvicorn parlayhitters_api.main:app --app-dir src --port 8060
cd app/web && API_ORIGIN=http://127.0.0.1:8060 npx vite --host 127.0.0.1 --port 5200
# then #/source/<game>/<player>/pts, #/scorecard, #/tests, #/help/errors, a Leg page, #/parlay in the Market lens
```

## Items

| # | Item | State | Commit |
|---|---|---|---|
| 1 | Prediction record: "Where this number comes from" button beside the model chance on the Leg page (headline tile and model panel) and under the win bars on the Game page; opens a Dialog; `#/source/<game>[/<player>/<stat>]` page to share | built | `dfe3b12b` |
| 2 | System health strip under the data line | built | `0a221c28` |
| 3 | Distribution view on the Leg page | built | `69ff2318` |
| 4 | Scorecard `#/scorecard` (in the nav after Track record) | built | `9056fb29` |
| 5 | Forward-test tracker `#/tests` (in the nav) | built, no bar (decision 4) | `5019e114` |
| 6 | Error-code help `#/help/errors[/<code>]`, searchable; Problem links its code; Key terms links to it | built | `bca6fb5b` |
| 7 | Parlay diagnostics panel on `#/parlay`, Market lens, paged (50) | built | `c47bb6c2` |
| 8 | Leg placeholders "What moved it", "Why this label" | built | `78382e5f` |
| 9 | Labels on browser-computed numbers and the fixed review text | built | `a096ec4b` |
| 10 | Bake: scorecard, forward tests, errors, health, record + distribution for every prop whose edges/breakdown is baked | built | `44ea2a87` |
| 11 | Proxy 500/502/504 with no JSON body maps to `network_unreachable`; `no_parlay_check` is a plain state; new routes in walker and learn-dom | built (partial: see "not done") | `8fa235ec` |

## Numbers (before -> after)

| What | Before (`18ad2e50`) | After |
|---|---|---|
| vitest | 392 / 392 (39 files) | 408 / 408 (40 files; new `honesty.test.ts`) |
| API pytest | 558 passed, 1 skipped | 559 passed, 1 skipped (new `test_bake_carries_the_honesty_answers`) |
| Regular build js | 354.81 kB (gz 112.03) | 390.18 kB (gz 122.59) |
| Regular build css | 73.86 kB (gz 15.21) | 76.30 kB (gz 15.69) |
| Artifact html | 5.56 MB (baked.json 5,063,603 B from the integration worktree) | 6.45 MB = 6,454,060 B (baked.json 5,921,328 B from the frozen export); same bake without record/distribution 5,121,778 B, so those add 799,550 B (under the 1.5 MB cap, so all are baked: 704 records) |
| Walker | see below | see below |
| learn-dom (fixtures, Chrome 9661) | pass | pass, with 4 new routes (`source-prop`, `scorecard`, `tests`, `help-errors`) |
| Learn check on the frozen real export (same regexes, my script) | n/a | 0 hits on `#/scorecard`, `#/tests`, `#/source` (prop and game), Leg (open and locked), Game, `#/parlay`, `#/help/errors` |

Walker, all with the new routes in its list (9 fixture routes, 3 artifact names; the Learn check now covers `source`,
`scorecard`, `tests`, `help`), fixtures API, no desk file served:

| Run | Violations |
|---|---|
| `app/scripts/check.sh --browser` (all checks, then the walk on 8090/5290) | 0; table "ALL CHECKS PASSED" (api ruff, format, mypy, pytest; fixtures ruff; web eslint, tsc, vitest, build; artifact build; browser route walk) |
| `app/scripts/walk.sh --widths`: plain 390 (seed 1), injected 390 (seed 2), plain 360 / 768 / 1180 (seed 3), 150 steps each | 0 / 0 / 0 / 0 / 0 |
| Artifact walker (`--set artifact`, seed 7, 90 steps, 6.45 MB artifact) | 0 |
| Quick walk on my fixtures server, 375 px, 240 steps | 0 |

Keyboard path (CDP, frozen export, Leg page): Tab reaches "Where this number comes from" (30 tabs from the top); Enter opens
the dialog with focus on Close; 30 more Tabs stay inside it; Escape closes it and focus returns to the button.

Screenshots looked at (`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/pages/out*/`):
record page 375 light and 1280 dark, record dialog 375 dark; strip Learn 375 light, Market 375 dark (expanded) and 1280
light, fixtures Market 375; distribution 375 light/dark, 1280 dark Market, locked finished game (375), fixtures (375);
scorecard 1280 dark (before and after splitting the tables), 375 table (fixed: names broke letter by letter; now the table
scrolls in its frame), fixtures empty state 375 dark; tests 375 dark, 1280 light Market; help 375 light (one code), 1280
dark (search), 375 dark (empty search), failure screen 375 light with the code link; parlay diagnostics 375 dark, 1280
light (page 2 loading), fixtures (375), another night (375); placeholders 375 light/dark; card detail browser note 375;
artifact: scorecard, tests, Leg distribution, record page, record not in the snapshot, parlay diagnostics not in the
snapshot; API down: scorecard 375 light, tests 1280 dark.

## Every number on the new pages and where it comes from

| Page / panel | Number | API field |
|---|---|---|
| Record | model, version | `model_name`, `version` |
| Record | weights fingerprint (short, full on expand) | `weights_sha256_short`, `weights_sha256` |
| Record | rows trained on, games through | `n_train`, `fit_through` |
| Record | made, tip-off, lead in minutes | `made_at`, `tipoff`, `lead_minutes` (shown rounded to whole minutes) |
| Record | T-60 yes/no | `meets_t60` (the API's one derived flag) |
| Record | data version, code version, clean/dirty | `data_version`, `git_sha` (first 12 shown, full on expand), `git_dirty` |
| Record | stamped | `stamped_at` |
| Record | snapshots: time, lead, T-60, model, version, role | `snapshots[].made_at, lead_minutes, meets_t60, model_name, version, is_primary, is_latest, comparison_only`; row count is `Table`'s count of the list |
| Health strip | export age, schema | `data_made_at` (age via `time.ts agoText`), `export.schema_version` |
| Health strip (Market) | newest Pinnacle quote and per-book newest quote | `newest_quote.pinnacle`, `newest_quote.by_book` (age via `agoText`) |
| Health strip (Market) | stale-model marks per model and reason; shown rows on a stale fallback | `stale_marks.by_model`, `stale_marks.primary_fallback_stale` |
| Distribution | fan bands, middle, ends; table of 19 quantiles | `q_grid`, `taus` |
| Distribution | takeaway (25th, 50th, 75th points) | `q_grid[4]`, `[9]`, `[14]` with `taus[4]`, `[9]`, `[14]` |
| Distribution | mean, standard deviation | `mean`, `std` |
| Distribution | "c of n stored samples reach k or more" | `p_ge_full.counts[k-1]`, `p_ge_full.n` (k = floor(line)+1, an index, not a probability) |
| Distribution | chance to play shown, its source; projected minutes | `p_play.value`, `p_play.source`, `p_play.detail`; `proj_minutes` |
| Distribution | chance to play the row used; means if he plays / counting the chance he sits | `conditional.p_play_used`, `conditional.mean_given_plays`, `conditional.mean_unconditional` |
| Distribution | chances at marks, if he plays / counting the chance he sits | `conditional.p_ge_given_plays[t]`, `conditional.p_ge_unconditional[t]` |
| Scorecard | mean, interval, n rows, n dates (shown rows) | `targets[].models[].metrics.crps.{value,lo,hi,n,n_dates}`, `comparisons[].metrics.crps.*` |
| Scorecard | better/worse/same or "no call" | `comparisons[].metrics.crps.call` |
| Scorecard | counts-only rows: n rows, n dates, why hidden | `n_scored`/`n_pairs`, `n_dates`, `withheld_reason` |
| Scorecard | locked dates: rows, dates | `n_scored_locked`/`n_pairs_locked`, `n_dates_locked` |
| Scorecard | totals line over all measures | `score_rule.n_rows_read`, `n_late_excluded`, `n_t60_unknown`; `seasons`; `as_of` |
| Scorecard | top sentences, rule, limitation, lock text, method, population, not-available list | `targets[].sentence`, `score_rule.rule`, `.t60_filter`, `.limitation`, `lock_text`, `method`, `population`, `not_available[]` (all verbatim) |
| Forward tests | reads, n needed | `tests[].scheduled_read`, `tests[].n_needed` (verbatim text) |
| Forward tests | scored rows, dates, did-not-play per arm | `tests[].n_so_far[arm].scored, dates, dnp`; null shown as "Count not exported" with `n_so_far_reason` |
| Error help | code count, matches | the bundled registry's length and the search result length (counts of the list on screen) |
| Error help | answer status | `codes[].status` |
| Parlay diagnostics | records, engines and counts, records with independent / copula joints | `n_records`, `engines`, `n_with_independent`, `n_with_copula` |
| Parlay diagnostics | per record: side, legs, stored joint, after shrinking, model weight | `legs[].side`, `n_legs`, `joint_stored`, `shrunk_prob`, `model_weight`; `joint_independent`/`joint_copula` shown only when not null, else "not stored in tonight's export" |
| Parlay diagnostics | page position | `offset`, `n_returned` |

## Browser-computed numbers: labelled now, proposed to move server-side

| Where | What the browser computes | Label added | Proposal |
|---|---|---|---|
| `desk.ts:274` (`winRate`), shown on Discover's desk tile | n = won + lost (the Wilson interval it computes is not rendered anywhere) | "n is won plus lost, added up in your browser from the stored counts, not by the engine." | export `n_decided` in the desk record; drop the unused Wilson interval |
| `desk.ts:409` (`clusteredRate`), shown on Model History's record tile | win rate and a date-clustered bootstrap interval | "The rate and its interval are computed in your browser from the stored plays (each result and date, n = N), not by the engine." | the desk export writes rate, interval, n, n_dates, reps, seed |
| `CardDetail.tsx:62` | Wilson 95% range of the last-10 hit rate | "The range is computed in your browser from the stored hits and n = N, not by the engine." | the card route serves the interval with its method |
| `leg.ts:103` (`bookChance` -> `devigOver`), Leg headline book tile, Market lens | no-vig chance from two posted prices when the shown line is not the stored line | shown only in that case: "Computed in your browser from the two stored prices at this line (one pair, n = 1), not by the engine." | the breakdown ladder carries the de-vigged chance per line |
| `History.tsx:39-41` | fixed review figures (0.69c, -0.19c [-0.74, +0.15], n = 83) | now opens "Fixed text from a dated review (docs/reviews/desk_export_premerge_2026-11-08.md, 2026-11-08), not live data" | export the review's split, or drop the text when the snapshot changes |

## Decisions (what / why / what would reverse it)

1. **The record page is `#/source/...`, not `#/record/...`.** Why: `#/record` is the Track record route, and its parser
   and the hostile-router test treat extra parts as not found; a new name changes no existing assertion. Reverse: a
   route-table rewrite that wants `#/record/<game>`.
2. **Survival "at the line" is shown as counts ("97 of 199 stored samples reach 25 or more"), never divided.** Why: the
   stored meaning is counts[k-1] / n; dividing would be the app making a probability. The under side is not shown
   (it would need n minus count). Reverse: the API serves `p_ge_full` as probabilities.
3. **Text from the API that holds price words is shown only in the Market lens** (one regex list, the learn-dom one plus
   "Pinnacle", in `honesty.ts`). Applies to scorecard sentences and not-available items, forward-test entries, the
   distribution's source note; the Kalshi DNP note and the whole quote/stale part of the strip are Market-only by
   design. The Learn view says "further items are listed in the Market lens" without a count (a count would be a number
   the app made). Reverse: the API tags each sentence with a lens.
4. **No progress bar on the forward tests.** Why: `n_needed` is the rule's prose ("60 dates and 5,000 paired
   player-games"), and `n_so_far` counts latest-row settled rows, not the test's eligible pairs (`count_rule` says so); a
   bar would be the app parsing text and dividing two numbers that do not measure the same thing. Reverse: export item 3
   of the proposal (eligible paired n and a numeric `n_needed`), then draw the bar from those fields.
5. **No computed totals on the scorecard.** The "totals line over all measures" is the API's own `n_rows_read`,
   `n_late_excluded`, `n_t60_unknown`. Reverse: the API serves per-target totals.
6. **"Hidden until the look" has no date the app made up.** Counts-only rows show the API's `withheld_reason` and link to
   `#/tests` (where each test's scheduled read is stated); locked dates use the API's `lock_text` ("about Dec 1").
   Reverse: the scorecard payload carries each row's read date.
7. **Health strip thresholds** in one block (`HealthStrip.tsx OLD_AFTER_HOURS`): export 30 h (the data line's own
   `STALE_AFTER_HOURS`), quote 12 h (capture runs several times a day before tips). An old value shows the age plus
   "(this data is old)" from the `stale_data` registry code, highlighted. Age is `time.ts agoText`, the same as the data
   line. Reverse: the export carries capture status per book (proposal item 1), then the strip says failed/ok.
8. **The strip also shows in the Learn lens**, but only the export age and schema.
9. **Error help reads the bundled registry, not `/api/errors`.** Why: the help page must work with the data server down;
   it is the same file (`app/error_codes.json`), and the API test already checks the file equals the served list.
   Reverse: none needed.
10. **`no_parlay_check` (another night) is an absent state, not a failure panel**, via a per-call "quiet codes" map in
    `getMaybe`. Same for a 404 `not_found` (a server that predates the route) and the artifact's "not part of this
    snapshot". Reverse: a product call that another night should read as an error.
11. **Proxy 500/502/504 with a null body maps to `network_unreachable`** (registry code that already existed); a 500
    with a JSON body keeps its own code (existing test unchanged). Reverse: the API lane adds a dedicated code.
12. **Bake all record/distribution answers** for props whose edges are baked (704 of each): +799,550 B, under the 1.5 MB
    cap; the artifact is 6.45 MB. Records are model stats only (pts, reb, ast, fg3m), so a combo stat's record shows
    "not in this snapshot". Parlay diagnostics are not baked (the panel says "not in this snapshot").
13. **Wide tables scroll inside their own frame on a phone** (`.sc-table { min-width: 760px }`) rather than breaking model
    names letter by letter.
14. **Nav gains Scorecard and Forward tests** after Track record (also in the artifact, where both are baked). Error help
    is not in the nav (linked from every failure screen and Key terms).

## Not done

- Item 11, partly: the registry codes `bad_answer`, `too_many_requests`, `refused_showing_saved`, `storage_blocked`,
  `unknown_live_event` were not added (budget; each needs a registry entry, a raise site and the API agreement test).
- Number labels for the CLV tile and Discover's CLV interval: those are stored fields (`clv_summary`), not computed, so
  no label was added.

## Not verified

- History and Discover desk tiles with the new browser notes were not screenshotted (my API had no desk file); typecheck,
  lint and the walker covered them only with no desk served.
- The strip's "dead feed" look was checked by unit test (a quote 3 days old is marked old) and by the fixture strip, not
  on an export with a really old Pinnacle quote (the frozen export's quotes are 2 h old).
- Real phones, Safari, Firefox, screen readers: not run. The artifact screenshots ran at a 981 px layout viewport in
  headless Chrome (the artifact page sets no mobile viewport there), so artifact phone width was not seen.
- The export-field proposal link is plain text (the artifact forbids URLs), not a clickable link.
