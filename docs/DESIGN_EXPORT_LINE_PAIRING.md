# Design note: pairing sportsbook lines with write-on-change forecast rows (2026-11-09)

Follow-up to the C1 adversary review (`docs/reviews/app_reexport_2026-11-09.md`, F2 and F3). Code:
`nba/export/reader.py` (`newest_cover`, `forward_predictions`, `fetch_quotes`, `pair_quotes`).

## Problem

The export paired a quote with a forecast row only when `received_at <= row.made_at`. The store writes
a prediction group only when it changed (`nba/daily/store.py` `append_predictions_ex(dedup=True)`), so
a row keeps the `made_at` of the run that last changed it, often the morning run. Quotes that arrived
after that never paired: on 2026-11-09 at most 165 of 409 keys with a pre-tip line.

## Invariant

For a latest row R of key K (model, version, target, game, player) and a pretip run C that covered R's
game with `C.made_at < tip`:

1. R is exactly the content a full (non-deduped) log would hold for K at C. The store skips a group
   only when every member has the same `content_hash(tipoff, prediction)` as the latest stored row
   of its key AND that row's `made_at <= this made_at` (store.py, the `unchanged` test). So if C had
   produced different content for K, C would have written a newer row and R would not be latest.
2. Therefore every quote with `received_at <= C.made_at` and `received_at < tip` was knowable when a
   forecast with R's exact content existed. Pairing it with R reads nothing from the future.

Rule: `lines_through(R) = max(R.made_at, newest covering run made_at)` if R is the latest row of its key,
else `R.made_at` (a superseded row is never re-confirmed by a later run: it was replaced). The quote must
also satisfy `received_at <= lines_through` and `received_at < row.tipoff` and `< commence_time` (unchanged
query) and `lines_through` is capped at the row's tip. Every exported quote carries `paired_through`
(new additive column of `prop_lines`; schema version stays 2). AVG-shadow rows keep
`lines_through = made_at` (a different table and dedup; not extended).

## "Newest pretip run that covered the game"

A fully deduped run leaves no row, so coverage cannot come from `forward_predictions`. It comes from
`forward_model_stamps` (one row per model per run, written for every run that built predictions,
including a run whose rows were all skipped). A run covers a game when: its `made_at < tipoff` of the
game and the ET date of its `made_at` equals the game's date (a run predicts every upcoming game of
its own slate date). A run for another date is not counted, which can only lower the cutoff. No stamps
table, or no qualifying run: `lines_through = made_at` (the old behaviour).

## Checked in data (2026-11-09 scratch copies, read-only)

* Group-level write-on-change: stored consecutive rows with identical content exist only for arms
  rewritten with their changed group (recency arms and MOV-Elo rewritten when the context or
  injury-Elo member changed): e.g. 2026-11-09 all-games, props_recency_v1: 596 identical vs 4 changed
  consecutive pairs; props_context_residual: 598 changed vs 2 identical. The primary prop rows are
  rewritten only when they change.
* Game 0022600203 (tip 11-10 00:00 UTC): 24 stamped runs covered it; rows for the context model
  were written at 12:00, 18:20, 22:50, 23:49 only (378, 158, 146, 92 rows). Between those, runs wrote
  2 rows (the injury-Elo win rows, which change each run) and no prop rows: the skip.
* Example key (props_context_residual, ast, 1626172): written 12:00 and 18:20 with a different mean
  (0.8016 -> 0.8145), nothing at the 14 stamped runs between or the 8 after: unchanged by construction.

## Residual (not eliminated)

A key a later run did not emit at all (e.g. a player dropped after being ruled out) also leaves its old
row as latest, and its row cannot be told from a skipped one without a per-key run record. The pairing
then attaches pre-tip quotes to a forecast the newest run no longer made. The app already shows that
row (it is the latest); no post-tip or post-run quote is added. Recommended follow-up if it matters:
write a per-run `(run_id, game_id, key)` coverage record in `nba.daily` (live path, replay oracle).

`rung_cal_fields` and `line_cal_fields` still cut quotes at the row's own `made_at` (they are the
producer's calibration cutoff and score-aligned); only `prop_lines` rows are extended.

## F2: store hold

`fetch_quotes` is the only code that touches `market_asof.duckdb`: one filtered read into memory, then
`build._read_lines` closes the connection and `pair_quotes` runs on the in-memory rows. The hold is the
query time only.

## Tests (`tests/export/test_line_pairing.py`)

Planted: a quote after the newest run (refused); a quote after tip (refused, counted as post-tip); a
row that DID change at a later run (the superseded row pairs only up to its own `made_at`, the new row
up to its own `made_at` = the newest run); a game with no later run (unchanged); `newest_cover` ignores
a run after tip, a run for another date, an empty list and `made_at == tip`.
