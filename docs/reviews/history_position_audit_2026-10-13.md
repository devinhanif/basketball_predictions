# History position audit, phase 1 (2026-10-13)

Adversary, read-only. Question (NEXT_SESSION "Next, in order" 1; DECISIONS 2026-10-12 23:06): before the history
`players_static` fill (20:34 and 21:33 CDT, 2026-10-12), did any completed result give history-only players a degenerate
static value (pos_code, birth date, height, draft) so that "has a valid value" meant "survived into 2022+"?

**Verdict: SURVIVES for every completed result named in the handoff.** The exposure list in DECISIONS 23:06 is mostly
wrong: RECENT_ROLE_FULL (T467/T473), ROUTER_G2, PI_RECENT_ROLE, ROAD_STARS, NEAR_MISS_BUNDLE, INJURY_REASON and the
minutes_v2 / lower_tail exports never read a history row. They load `nba.duckdb` only (seasons 2022-24), and production
`players_static` covered **100% of played 2022-24 rows** in the 2026-10-11 and 2026-10-12 07:00 backups. The only completed
results that carried the survival pattern are the W_ALL sequence exports (T478, T483, T485). There it hits **322 of 77,195
history training targets (0.42%)**, all 2021-22, and **no eval row**, so the model has no test row on which the pattern
could pay off. Nothing needs a re-run before a decision. One live risk is new: the 2015-18 rows carry the same pattern
today (25% with no static row in any source), and that matters for SEQ_LEARNING_CURVE_2 and any CPU era-probe follow-up.

**Biggest risk if this audit is wrong:** some run built history features with a static source I did not trace (a scratch
script, or a Colab-side join of `players_static`). I traced every `research/` and `nba/` module that opens the history DB.
I did not read the Colab notebooks' own code.

## Results table

Static sources: P = production `nba.duckdb.players_static`, H = history DB `players_static` (empty before 20:34), C = the
per-player CommonPlayerInfo cache `data/players_static/<id>.parquet`. "Degenerate" = no position (or birth date) from any
source the run used.

| Result | Column(s) | Static source | Ran before fill (20:34)? | Degenerate share train / eval | Survival pattern? | Importance | Risk | Recommendation |
|---|---|---|---|---|---|---|---|---|
| T478 SEQ_FULL_MIX_RT, W_ALL member (holdout PASS, promotion candidate) | pos_G/F/C one-hot -> all 0 | H(empty)+P+C | yes (hist export 14:37) | 322 / 77,195 hist targets = 0.42% (0.20% of all training targets); eval 0 | **yes** (48 players, all 2021-22, none in 2022+, mean 10.0 vs 22.8 min) | unknown (neural, no stored importances) | low: training-only; the pattern cannot fire on a test row | no action |
| T478 FULL member / T467-T469 / T473 RECENT_ROLE_FULL | pos_code | P only, 2022-24 | yes | 0 played rows (5 DNP rows NULL, 2023/24) / 0 | no (no history rows) | pts knockout +0.0005 [-0.0010, +0.0021] (feature_significance 2026-10-12) | none | no action |
| T485/T486 SEQ_LEARNING_CURVE | S1 (2015-18) position zeroed for all rows by design; S2 as T478 | H+P+C | export 21:28, between the two fills | S1 100% (uniform, not survival); S2 322 / 77,195; eval 0 | S2 only, as T478; the same S2 export sits in both arms of the A contrast | unknown | low | no action |
| T483 era probe | as T478 (same W_ALL export) | H+P+C | yes | as T478 | as T478 | n/a (descriptive) | low: the flag is on 2019z (0 degenerate rows), so this cannot cause it | no action |
| T440-T445 ENSEMBLE E3 (history SEQ) | pos one-hot | H(empty)+P+C | yes (export 2026-10-11 21:53) | 0 / 49,150 (2019-20, 2020-21 only; gate.json history_no_position 0) | no | n/a | none; already CLOSED / INELIGIBLE | no action |
| T420-T423 HISTORY_WINDOW_3 | pos_code, draft_year (slices) | P+H+C (`static_with_cache`) | yes (2026-10-11 21:35) | 0 played rows without static (gate G1b); G4 flagged cells: none | no | n/a | none; CLOSED FAIL | no action |
| T315 HISTORY_WINDOW / T325 HISTORY_WINDOW_2 | pos_code, birth_date, draft_year | P+H(empty), **no cache** | yes (2026-10-11 09:41 / 15:04) | prod-only coverage of 2019-21 rows: 30% / 23% / 13.5% NULL (21.7% pooled) | **yes** (play rate 0.66 vs 0.86) | n/a (both stopped at gate; the stop was on report columns) | none for decisions (closed direction, no model claim) | no action; record the exposure |
| T481 ERROR_FEEDBACK Stage 1 | pos_code, birth_date, draft_year | P+H | **no** (select 23:38; gate and null rebuilt 22:55; pre-fill artifacts under `superseded_2026-10-12_pre_pos_fix/`) | about 0.01% / 0 | no | n/a | none; CLOSED | no action |
| T482/T484 PPLAY_MINUTES_VALID | birth_date (age); no pos_code | P+H | **no** (gate 23:05, scores 23:07) | gate check 13 passes (2 rows of one 2019 player open) | no | n/a | none | no action |
| ROUTER_G2 (T464-T466), NEAR_MISS_BUNDLE, PI_RECENT_ROLE (T460-T461), ROAD_STARS (T475), INJURY_REASON, minutes_v2 and lower_tail npz | pos_code (birth_date in minutes_v2) | P only, 2022-24; ROUTER/NEAR_MISS SEQ inputs are `seq_ensemble_cur` (2022-24 only) | yes | 0 played rows / 0 | no | as FULL | none | no action |
| pplay_context_eval, `nba/daily/pplay_shadow` | birth_date | P only (history DB read for 2021-22 game results only, as a win% prior) | n/a | 0 | no | n/a | none | no action |

**Ranking by risk (exposure x importance x decision weight):** (1) T478 W_ALL member: the only exposed result a
decision rests on (the AVG promotion), but the exposure is 0.20% of training targets and 0 eval rows. (2) T485, the same
export in both arms of the contrast. (3) T483, descriptive. Everything else is unexposed, or closed with no claim.

**Re-run cost, if Devin wants certainty anyway:** re-export the W_ALL history segment against today's H (322 rows gain a
position; CPU, about 5 min) and refit W_ALL on Colab T4 (about 20-25 min per seed, as for T478's run 20261012). The
re-run would be scored on 2023/24 OOF only and must not touch 2025. I do not recommend it: the expected movement is far
below the T478 seed noise.

## Checks run (seasons <= 2024 and history only; all read-only)

| Check | Command (scratchpad script `cov.py`, exit 0) | n | Result | Pass |
|---|---|---|---|---|
| History static empty before fill | `count(*) FROM players_static` on the backups `..._pre_2015_19_ingest_20261012T232608Z` (18:26 CDT), `..._pre_static_fix_20261012T203450` and `..._T213333` | 0 / 0 / 736 rows; now 791 | confirmed: empty until 20:34 | n/a |
| Degenerate value in the tree path | `context_residual.py` 548-565: column present, table empty -> left join gives NULL; `_matrix` casts to Float64 -> NaN; no `fill_null` in error_feedback_eval | code | **NULL/NaN (LightGBM's missing branch), not 0.** The "0" in NEXT_SESSION and DECISIONS 23:06 is correct only for the sequence exporter (one-hot all zero) | finding |
| Prod static coverage at run time (2022-24) | join pgs x players_static on `nba_20261011` and `nba_20261012` backups, `season <= 2024` | 104,170 played rows | 0 played rows without position; 5 DNP rows NULL | pass |
| History coverage by source and time | cache mtime < 20:34 (1,250 files) or < 14:37; P; H | 2019-21: 94,101 rows | P only: 21.7% NULL, play 0.66 vs 0.86 (survival); P+C before fill: 685 rows NULL (play 0.47), **322 played, exactly the exporter's recorded count**, 48 players, all 2021, none in 2022+ | reproduces |
| Eval rows with degenerate position | as above, for the 2023/24 eval seasons | 56,433 played | 0 | pass |
| 2015-18 today (forward risk) | P+H+C now | 111,568 rows, 827 ids | 28,370 rows (25.4%, 384 ids) have no static row in any source; those players average 16.5 vs 24.9 min | **open risk** |

Not run: shuffled labels, planted future, knockout and replication. No completed result was found exposed enough to
attack, and refits are out of scope (no refit, no Colab). Integer support: n/a.

## Playbook check

Model classes: gradient-boosted trees (LightGBM context residual heads: FULL, PI_RECENT_ROLE, ROAD_STARS, ERROR_FEEDBACK,
HISTORY_WINDOW*) and neural sequence models (W_ALL / SEQ, T478, T483, T485).
1. **Information or capacity?** Neither: this is a test-validity audit of one input (position or age), and that input
   carries little information (pts knockout +0.0005 [-0.0010, +0.0021], n 55,320, 2,638 games).
2. **Enough data for the class?** Not at issue. The degenerate rows are 0.2-0.4% of the neural class's training set.
3. **Test valid?** Yes for every result a decision rests on. The tree checks ask for a missingness audit beyond the as-of
   cause. Done here: the NULL pattern is future-derived (static row exists iff the player appears later), but it is
   confined to training rows, so no eval row can exploit it. This differs from the opp_adjusted_ridge precedent, where
   the NULL appeared on eval rows and tracked tonight's minutes.
4. **Lineage:** HISTORY_WINDOW_3 already found and fixed this pattern on 2026-10-11 (`static_with_cache`, gate G1b, a
   pos_code-excluded G4 diagnostic). The sequence exporter inherited the cache merge. The guard did not carry into
   `history_window_eval` (T315/T325, closed) or into ERROR_FEEDBACK's first gate (caught and rebuilt at 21:40).
5. **Scored against:** integer CRPS vs A0 / FULL on 2023-24 (and 2025 for T478). None of the exposed rows is an eval row.

## Surprises and notes

- The handoff list was built from "what reads `build_features`", not from "what reads history rows". Almost none of
  the listed results trains on history.
- `history_window_eval.py` (T315/T325) still unions only P+H with no cache. If that harness is ever revived, port
  `static_with_cache` and the G1b gate first.
- Static columns are current listings, not as-of (position and weight as of the pull date). This is a known class of
  as-of error, uniform across players and not the survival pattern. Not audited further.
- **Hygiene disclosure:** one early coverage query on `nba.duckdb` grouped `games` and `player_game_stats` row counts by
  season without a `season <= 2024` filter, so it printed 2025 row counts (1,321 games; 34,756 player rows, all with a
  static position). No outcome, label, score or model output for 2025 was read. The main session should decide whether
  this needs a holdout-log note. I drafted none (I may not write logs).

## Recommended changes (not applied)

1. Correct NEXT_SESSION item 1 and the DECISIONS 23:06 audit note: tree-path degenerate value is NULL, not 0; exposure
   is confined to the sequence history segment (322 training rows), and the audit closes with no re-run.
2. Before SEQ_LEARNING_CURVE_2 (windows 9-10) or any history-trained CPU model is frozen, add a gate item: share of
   pre-2019 rows with no static row in P+H+C, split by later survival. Either zero position for all pre-2019 rows
   (S1's rule) or fetch the 384 missing ids first (about 4 min at 0.6 s each, Devin's shell; birth dates included).
3. Port `static_with_cache` into `research/eval/history_window_eval.py`, or mark that harness archived.

Drafted DECISIONS row (not appended):
`2026-10-13 — History position audit closed (docs/reviews/history_position_audit_2026-10-13.md): no completed result a
decision rests on is exposed; W_ALL history segment had 322/77,195 training targets with zero position (2021-22
non-survivors), 0 eval rows; no re-run. Open: 2015-18 rows 25% without static in any source (gate item for any pre-2019
consumer). What would reverse it: a Colab-side static join found in a notebook.`
